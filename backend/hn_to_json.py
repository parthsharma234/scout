#!/usr/bin/env python3
"""
Hacker News -> JSON pipeline for Scout cluster map.

Outputs (default in ./data/hn_data):
  - hn_raw.jsonl: raw story rows + extracted candidate entities
  - hn_entities.json: aggregated entity rankings
  - hn_source_nodes.json: story-level nodes for the source web
  - hn_state.json: lightweight state (last run metadata)
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HN_BASE = "https://hacker-news.firebaseio.com/v0"

FEEDS = [
    "topstories",
    "newstories",
    "beststories",
    "askstories",
    "showstories",
]

SOURCE_ID = "hackernews"
SOURCE_NAME = "Hacker News"

DOMAIN_BLOCKLIST = {
    "news.ycombinator.com",
    "github.com",
    "www.github.com",
    "gitlab.com",
    "www.gitlab.com",
    "docs.google.com",
    "medium.com",
    "www.medium.com",
    "wikipedia.org",
    "www.wikipedia.org",
    "youtube.com",
    "www.youtube.com",
    "x.com",
    "twitter.com",
    "www.twitter.com",
    "reddit.com",
    "www.reddit.com",
    "techcrunch.com",
    "www.techcrunch.com",
    "arstechnica.com",
    "www.arstechnica.com",
    "fortune.com",
    "www.fortune.com",
    "nytimes.com",
    "www.nytimes.com",
    "bloomberg.com",
    "www.bloomberg.com",
    "wsj.com",
    "www.wsj.com",
    "wired.com",
    "www.wired.com",
    "theverge.com",
    "www.theverge.com",
}

DOMAIN_CORE_BLOCKLIST = {
    "blog", "news", "docs", "developer", "developers", "support",
    "help", "status", "changelog", "updates", "wiki", "forum",
    "app", "dev", "io", "co", "net", "org", "com",
}

STOP_ENTITIES = {
    "HN", "Show HN", "Ask HN", "Tell HN", "API", "SaaS", "AI", "ML",
    "Startup", "Startups", "Open Source", "GitHub", "Reddit", "Twitter",
    "Chrome", "Firefox", "Linux", "Windows", "Mac", "App", "Tool",
    "They", "This", "That", "These", "Those", "Lawyer", "Vulnerability",
    "Turn", "Keep", "Are", "Can", "Use", "Free", "Open", "New",
}

EXCLUDED_INCUMBENTS = {
    "OpenAI", "Anthropic", "Google", "Meta", "Microsoft", "Amazon", "Apple",
    "NVIDIA", "Github", "GitHub", "GitLab", "Stripe", "Databricks",
    "Cloudflare", "Notion", "Figma", "Shopify", "Reddit", "Twitter", "X",
    "Tesla", "SpaceX", "Adobe", "Oracle", "Salesforce", "IBM", "Intel",
}

KEYWORD_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "into", "your", "you",
    "have", "has", "had", "are", "was", "were", "will", "would", "about", "just",
    "what", "when", "where", "which", "their", "there", "them", "then", "than",
    "launch", "launched", "startup", "product", "build", "built", "show", "ask",
}

BUILDER_SIGNAL_PATTERNS = [
    r"\bwe built\b",
    r"\bi built\b",
    r"\bwe made\b",
    r"\bi made\b",
    r"\bwe launched\b",
    r"\bi launched\b",
    r"\bmy startup\b",
    r"\bour startup\b",
    r"\bmy side project\b",
    r"\bour side project\b",
    r"\bshow hn\b",
]

STARTUP_TITLE_PATTERNS = [
    re.compile(r"^(show hn)\s*[:\-]\s*", re.IGNORECASE),
    re.compile(r"\b(i built|we built|i made|we made|i launched|we launched)\b", re.IGNORECASE),
    re.compile(r"\b(my startup|our startup|my side project|our side project)\b", re.IGNORECASE),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_json(url: str, timeout_seconds: int = 15, retries: int = 3) -> Any:
    last_error = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "scout-hn-json/0.1"},
            )
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(min(2 ** attempt, 3))
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def get_item(item_id: int) -> dict[str, Any] | None:
    try:
        item = fetch_json(f"{HN_BASE}/item/{item_id}.json")
    except RuntimeError:
        return None
    return item if isinstance(item, dict) else None


def get_feed_ids(feed: str, per_feed: int) -> list[int]:
    data = fetch_json(f"{HN_BASE}/{feed}.json")
    if not isinstance(data, list):
        return []
    return [int(x) for x in data[:per_feed]]


def strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_domain(url: str | None) -> str:
    if not url:
        return ""
    try:
        host = urllib.parse.urlparse(url).netloc.lower().strip(".")
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def domain_to_company(domain: str) -> str:
    if not domain:
        return ""
    parts = domain.split(".")
    if len(parts) < 2:
        return ""
    core = parts[-2]
    if not core or core in DOMAIN_CORE_BLOCKLIST:
        return ""
    return core.replace("-", " ").title()


def clean_entity(candidate: str) -> str:
    candidate = candidate.strip()
    candidate = re.sub(r"\s+", " ", candidate)
    candidate = re.sub(r"[^\w\s\-\+\.]", "", candidate)
    candidate = candidate.strip(" -_.")
    return candidate


def canonicalize_entity(candidate: str) -> str:
    normalized = clean_entity(candidate)
    normalized = re.sub(r"\.(com|io|ai|dev|app|gg|fyi|co|net|org)$", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized.islower():
        normalized = normalized.title()
    return normalized


def is_valid_entity(candidate: str) -> bool:
    if not candidate:
        return False
    if len(candidate) < 2 or len(candidate) > 48:
        return False
    if candidate in STOP_ENTITIES:
        return False
    if candidate.isdigit():
        return False
    if candidate.lower() in {"show hn", "ask hn", "tell hn"}:
        return False
    if candidate.title() in EXCLUDED_INCUMBENTS:
        return False
    if len(candidate.split()) > 4:
        return False
    return True


def extract_title_entities(title: str) -> list[str]:
    candidates: set[str] = set()
    t = title.strip()

    m = re.match(r"^(show hn|ask hn|tell hn)\s*[:\-]\s*(.+)$", t, flags=re.IGNORECASE)
    if m:
        lead = re.split(r"\s+[—–\-:|]\s+|\s+\(|\s+\[", m.group(2).strip(), maxsplit=1)[0].strip()
        candidates.add(clean_entity(lead))

    patterns = [
        r"\bintroducing\s+([A-Z][A-Za-z0-9][A-Za-z0-9\-\+ ]{1,40})",
        r"\blaunch(?:ing|ed)?\s+([A-Z][A-Za-z0-9][A-Za-z0-9\-\+ ]{1,40})",
        r"\b([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9\-\+]+){0,2})\s+(?:is live|raised|announced)\b",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, t):
            candidates.add(clean_entity(match))

    return [c for c in candidates if is_valid_entity(c)]


def is_showcase_title(title: str) -> bool:
    return bool(re.match(r"^(show hn)\s*[:\-]\s*", title.strip(), flags=re.IGNORECASE))


def has_startup_title_signal(title: str) -> bool:
    return any(pattern.search(title) for pattern in STARTUP_TITLE_PATTERNS)


def extract_story_keywords(text: str, top_n: int = 8) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-\+]{2,}", text.lower())
    filtered = [
        t for t in tokens
        if t not in KEYWORD_STOPWORDS and len(t) > 2 and not t.isdigit()
    ]
    if not filtered:
        return []
    counts = Counter(filtered)
    return [token for token, _ in counts.most_common(top_n)]


def has_builder_signal(text: str) -> bool:
    lower = text.lower()
    return any(re.search(pattern, lower) for pattern in BUILDER_SIGNAL_PATTERNS)


def score_impressions(score: int, descendants: int, created_unix: int) -> int:
    age_hours = max(1.0, (time.time() - created_unix) / 3600.0)
    base = score * 6 + descendants * 10
    recency_boost = max(0.0, 72.0 - age_hours) * 1.5
    return int(max(0, base + recency_boost))


def summarize_story(story: dict[str, Any], comments: list[str]) -> str:
    text = strip_html(story.get("text"))
    if text:
        return text[:280]
    if comments:
        return comments[0][:280]
    return "No summary text available."


def fetch_comment_texts(story: dict[str, Any], max_comments: int, max_depth: int) -> list[str]:
    kids = story.get("kids") or []
    if not kids:
        return []

    out: list[str] = []
    queue: deque[tuple[int, int]] = deque((int(k), 1) for k in kids)

    while queue and len(out) < max_comments:
        item_id, depth = queue.popleft()
        if depth > max_depth:
            continue
        item = get_item(item_id)
        if not item:
            continue
        if item.get("deleted") or item.get("dead"):
            continue
        if item.get("type") != "comment":
            continue
        text = strip_html(item.get("text"))
        if text:
            out.append(text)
        for child in item.get("kids") or []:
            if len(out) < max_comments:
                queue.append((int(child), depth + 1))

    return out


def extract_story_candidates(story: dict[str, Any], comments: list[str]) -> list[dict[str, Any]]:
    title = story.get("title") or ""
    text = strip_html(story.get("text"))
    joined_comments = " ".join(comments[:8])
    haystack = f"{title}\n{text}\n{joined_comments}".strip()
    author_haystack = f"{title}\n{text}".strip()
    showcase_story = is_showcase_title(title)
    builder_signal = has_builder_signal(author_haystack)
    startup_title_signal = has_startup_title_signal(title)
    startup_context = showcase_story or builder_signal or startup_title_signal

    candidates: dict[str, dict[str, Any]] = {}

    if not startup_context:
        return []

    domain = normalize_domain(story.get("url"))
    if domain and domain not in DOMAIN_BLOCKLIST and (showcase_story or builder_signal):
        name = domain_to_company(domain)
        if is_valid_entity(name):
            entry = candidates.setdefault(name, {"entity": name, "confidence": 0.0, "reasons": []})
            entry["confidence"] = max(entry["confidence"], 0.62 if showcase_story else 0.54)
            entry["reasons"].append("domain_entity")
            if showcase_story:
                entry["reasons"].append("showcase_story")

    for entity in extract_title_entities(title):
        entry = candidates.setdefault(entity, {"entity": entity, "confidence": 0.0, "reasons": []})
        entry["confidence"] += 0.22
        entry["reasons"].append("title_pattern")
        if showcase_story:
            entry["reasons"].append("showcase_story")

    if builder_signal:
        for entry in candidates.values():
            entry["confidence"] += 0.15
            entry["reasons"].append("builder_signal")

    lower_haystack = haystack.lower()
    for entry in candidates.values():
        normalized = canonicalize_entity(entry["entity"])
        if not is_valid_entity(normalized):
            entry["confidence"] = 0.0
            continue
        entry["entity"] = normalized
        if normalized.lower() in lower_haystack:
            entry["confidence"] += 0.1
            entry["reasons"].append("text_presence")
        entry["confidence"] = round(min(entry["confidence"], 1.0), 3)

    deduped: dict[str, dict[str, Any]] = {}
    for entry in candidates.values():
        if entry["confidence"] <= 0:
            continue
        key = re.sub(r"[^a-z0-9]+", "", entry["entity"].lower())
        current = deduped.get(key)
        if not current or entry["confidence"] > current["confidence"]:
            deduped[key] = entry

    return sorted(deduped.values(), key=lambda c: c["confidence"], reverse=True)


def write_jsonl(path: Path, rows: list[dict[str, Any]], description: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "_meta": {
            "description": description,
            "generated_at": now_iso(),
            "schema_version": "1.1",
            "record_type": "meta",
        }
    }
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hacker News to JSON pipeline for Scout")
    parser.add_argument("--mode", choices=["feeds", "updates"], default="feeds")
    parser.add_argument("--per-feed", type=int, default=120, help="Items per feed when mode=feeds")
    parser.add_argument("--max-items", type=int, default=320, help="Hard cap after dedupe")
    parser.add_argument("--max-comments", type=int, default=8, help="Max comments fetched per story")
    parser.add_argument("--max-depth", type=int, default=2, help="Comment traversal depth")
    parser.add_argument("--entity-threshold", type=float, default=0.66, help="Confidence threshold")
    parser.add_argument("--raw-out", type=Path, default=Path("data/hn_data/hn_raw.jsonl"))
    parser.add_argument("--entities-out", type=Path, default=Path("data/hn_data/hn_entities.json"))
    parser.add_argument("--nodes-out", type=Path, default=Path("data/hn_data/hn_source_nodes.json"))
    parser.add_argument("--state-out", type=Path, default=Path("data/hn_data/hn_state.json"))
    return parser.parse_args()


def collect_story_ids(mode: str, per_feed: int, max_items: int) -> list[int]:
    ids: list[int] = []
    if mode == "updates":
        updates = fetch_json(f"{HN_BASE}/updates.json")
        if isinstance(updates, dict):
            ids = [int(x) for x in (updates.get("items") or [])]
    else:
        seen: set[int] = set()
        for feed in FEEDS:
            for item_id in get_feed_ids(feed, per_feed):
                if item_id not in seen:
                    seen.add(item_id)
                    ids.append(item_id)
    return ids[:max_items]


def main() -> None:
    args = parse_args()
    started_at = now_iso()

    story_ids = collect_story_ids(args.mode, args.per_feed, args.max_items)
    raw_rows: list[dict[str, Any]] = []
    entity_mentions: list[dict[str, Any]] = []

    for idx, story_id in enumerate(story_ids, start=1):
        item = get_item(story_id)
        if not item:
            continue
        if item.get("type") not in {"story", "job", "poll"}:
            continue
        if item.get("deleted") or item.get("dead"):
            continue

        comments = fetch_comment_texts(item, args.max_comments, args.max_depth)
        candidates = extract_story_candidates(item, comments)
        impressions = score_impressions(
            int(item.get("score") or 0),
            int(item.get("descendants") or 0),
            int(item.get("time") or int(time.time())),
        )
        keywords = extract_story_keywords(f"{item.get('title') or ''} {strip_html(item.get('text'))}")
        summary = summarize_story(item, comments)

        raw_rows.append({
            "hn_id": item.get("id"),
            "type": item.get("type"),
            "title": item.get("title"),
            "url": item.get("url"),
            "score": int(item.get("score") or 0),
            "descendants": int(item.get("descendants") or 0),
            "hn_created_at": datetime.fromtimestamp(int(item.get("time") or int(time.time())), tz=timezone.utc).isoformat(),
            "impressions": impressions,
            "keywords": keywords,
            "summary": summary,
            "comments_sample_count": len(comments),
            "entity_candidates": candidates,
            "fetched_at": now_iso(),
        })

        for cand in candidates:
            entity_mentions.append({
                "entity": cand["entity"],
                "confidence": cand["confidence"],
                "reasons": cand["reasons"],
                "hn_id": item.get("id"),
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "summary": summary,
                "keywords": keywords,
                "score": int(item.get("score") or 0),
                "descendants": int(item.get("descendants") or 0),
                "impressions": impressions,
                "hn_created_unix": int(item.get("time") or int(time.time())),
                "hn_created_at": datetime.fromtimestamp(int(item.get("time") or int(time.time())), tz=timezone.utc).isoformat(),
            })

        if idx % 25 == 0:
            print(f"processed {idx}/{len(story_ids)} candidate stories")

    # Aggregate mentions by entity with repeat-evidence confidence boost
    by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    entity_display: dict[str, str] = {}
    for mention in entity_mentions:
        entity_key = re.sub(r"[^a-z0-9]+", "", mention["entity"].lower())
        if not entity_key:
            continue
        by_entity[entity_key].append(mention)
        current_name = entity_display.get(entity_key)
        if not current_name or len(mention["entity"]) < len(current_name):
            entity_display[entity_key] = mention["entity"]

    entity_rows: list[dict[str, Any]] = []
    node_rows: list[dict[str, Any]] = []
    now_ts = int(time.time())

    for entity_key, mentions in by_entity.items():
        entity = entity_display.get(entity_key, mentions[0]["entity"])
        mentions_sorted = sorted(mentions, key=lambda m: m["impressions"], reverse=True)
        count = len(mentions)
        avg_conf = sum(m["confidence"] for m in mentions) / count
        boosted_conf = min(1.0, avg_conf + min(0.25, 0.05 * (count - 1)))
        all_reasons = {reason for mention in mentions for reason in mention["reasons"]}
        has_high_signal = bool({"title_pattern", "showcase_story"} & all_reasons)

        if boosted_conf < args.entity_threshold:
            if has_high_signal and boosted_conf >= 0.45:
                pass
            elif count >= 2 and boosted_conf >= 0.58:
                pass
            else:
                continue
        if not has_high_signal and count < 3:
            continue

        impressions_total = sum(m["impressions"] for m in mentions)
        if impressions_total < 120 and count < 2 and "showcase_story" not in all_reasons:
            continue
        mention_1h = sum(1 for m in mentions if (now_ts - m["hn_created_unix"]) <= 3600)
        mention_24h = sum(1 for m in mentions if (now_ts - m["hn_created_unix"]) <= 86400)

        keyword_counts = Counter()
        for mention in mentions:
            keyword_counts.update(mention["keywords"])
        top_keywords = [k for k, _ in keyword_counts.most_common(6)]

        source_counts = {SOURCE_ID: count}

        entity_rows.append({
            "entity": entity,
            "confidence": round(boosted_conf, 3),
            "impressions": impressions_total,
            "stories": count,
            "mention_count_1h": mention_1h,
            "mention_count_24h": mention_24h,
            "sources": [SOURCE_ID],
            "source_counts": source_counts,
            "top_keywords": top_keywords,
            "evidence_count": count,
            "quality_signals": sorted(all_reasons),
            "first_seen_at": min(m["hn_created_at"] for m in mentions),
            "last_seen_at": max(m["hn_created_at"] for m in mentions),
        })

        for mention in mentions_sorted[:40]:
            node_rows.append({
                "id": f"hn-{mention['hn_id']}-{re.sub(r'[^a-z0-9]+', '-', entity.lower()).strip('-')}",
                "entity": entity,
                "source_id": SOURCE_ID,
                "source_name": SOURCE_NAME,
                "headline": mention["title"],
                "url": mention["url"] or f"https://news.ycombinator.com/item?id={mention['hn_id']}",
                "summary": mention["summary"],
                "interactions": int(mention["score"] + mention["descendants"] * 2),
                "views": int(max(mention["impressions"], mention["score"] * 10)),
                "impressions": mention["impressions"],
                "hn_id": mention["hn_id"],
                "published_at": mention["hn_created_at"],
                "confidence": mention["confidence"],
            })

    # Trend score normalization after filtering
    max_impressions = max((row["impressions"] for row in entity_rows), default=1)
    for row in entity_rows:
        impression_component = (row["impressions"] / max_impressions) * 70.0
        confidence_component = row["confidence"] * 30.0
        row["trend_score"] = round(impression_component + confidence_component, 2)
        row["velocity_delta_pct"] = 0.0
        row["spike_detected"] = row["mention_count_1h"] >= 3

    entity_rows.sort(key=lambda r: r["trend_score"], reverse=True)
    node_rows.sort(key=lambda r: (r["interactions"] + r["views"] * 0.18), reverse=True)

    write_jsonl(
        args.raw_out,
        raw_rows,
        description=(
            "Raw Hacker News story records from selected feeds, including "
            "engagement metrics, keyword extraction, summaries, and entity candidates."
        ),
    )
    write_json(
        args.entities_out,
        {
            "_meta": {
                "description": (
                    "Entity-level aggregation derived from HN stories. "
                    "Use this file for cluster-map rankings and leaderboard inputs."
                ),
                "source": "hackernews",
                "schema_version": "1.1",
            },
            "generated_at": now_iso(),
            "mode": args.mode,
            "story_count": len(raw_rows),
            "entity_count": len(entity_rows),
            "entities": entity_rows,
        },
    )
    write_json(
        args.nodes_out,
        {
            "_meta": {
                "description": (
                    "Source-level interaction nodes for each entity. "
                    "Each node represents one HN story linked to an entity."
                ),
                "source": "hackernews",
                "schema_version": "1.1",
            },
            "generated_at": now_iso(),
            "source_nodes": node_rows,
        },
    )
    write_json(
        args.state_out,
        {
            "_meta": {
                "description": (
                    "Pipeline run metadata and counts for the latest HN collection run."
                ),
                "source": "hackernews",
                "schema_version": "1.1",
            },
            "last_run_started_at": started_at,
            "last_run_finished_at": now_iso(),
            "mode": args.mode,
            "input_story_ids": len(story_ids),
            "processed_stories": len(raw_rows),
            "entities_written": len(entity_rows),
            "nodes_written": len(node_rows),
        },
    )

    print(
        f"done: stories={len(raw_rows)} entities={len(entity_rows)} nodes={len(node_rows)} "
        f"-> {args.entities_out} {args.nodes_out}"
    )


if __name__ == "__main__":
    main()
