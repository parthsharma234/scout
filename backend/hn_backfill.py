#!/usr/bin/env python3
"""
HN historical backfill via Algolia Search API.

Writes:
  data/hn_data/hn_raw.jsonl
  data/hn_data/hn_entities.json
  data/hn_data/hn_source_nodes.json
  data/hn_data/hn_state.json
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from hn_to_json import (
        SOURCE_ID,
        SOURCE_NAME,
        extract_story_candidates,
        extract_story_keywords,
        now_iso,
        write_json,
        write_jsonl,
    )
except ModuleNotFoundError:
    from backend.hn_to_json import (  # type: ignore
        SOURCE_ID,
        SOURCE_NAME,
        extract_story_candidates,
        extract_story_keywords,
        now_iso,
        write_json,
        write_jsonl,
    )


ALGOLIA_ENDPOINT = "https://hn.algolia.com/api/v1/search_by_date"


def fetch_algolia_window(start_ts: int, end_ts: int, max_pages: int = 20) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in range(max_pages):
        params = urllib.parse.urlencode(
            {
                "tags": "story",
                "numericFilters": f"created_at_i>={start_ts},created_at_i<={end_ts}",
                "hitsPerPage": 100,
                "page": page,
            }
        )
        url = f"{ALGOLIA_ENDPOINT}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "scout-hn-backfill/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            break
        hits = payload.get("hits") or []
        if not isinstance(hits, list) or not hits:
            break
        for hit in hits:
            if isinstance(hit, dict):
                out.append(hit)
        nb_pages = int(payload.get("nbPages") or 0)
        if page + 1 >= nb_pages:
            break
    return out


def read_existing_raw(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for idx, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if idx == 0 and isinstance(obj, dict) and "_meta" in obj:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill HN stories from Algolia API")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--max-pages", type=int, default=12)
    parser.add_argument("--raw-out", type=Path, default=Path("data/hn_data/hn_raw.jsonl"))
    parser.add_argument("--entities-out", type=Path, default=Path("data/hn_data/hn_entities.json"))
    parser.add_argument("--nodes-out", type=Path, default=Path("data/hn_data/hn_source_nodes.json"))
    parser.add_argument("--state-out", type=Path, default=Path("data/hn_data/hn_state.json"))
    parser.add_argument("--entity-threshold", type=float, default=0.55)
    return parser.parse_args()


def _score_impressions(points: int, comments: int, created_unix: int) -> int:
    age_hours = max(1.0, (time.time() - created_unix) / 3600.0)
    base = points * 6 + comments * 10
    recency_boost = max(0.0, 72.0 - age_hours) * 1.5
    return int(max(0, base + recency_boost))


def main() -> None:
    args = parse_args()
    started_at = now_iso()
    now = datetime.now(timezone.utc)
    months = max(1, int(args.months))
    window_days = max(1, int(args.window_days))
    start = now - timedelta(days=months * 30)
    cursor = start

    fetched_hits: list[dict[str, Any]] = []
    while cursor < now:
        window_end = min(now, cursor + timedelta(days=window_days))
        hits = fetch_algolia_window(
            start_ts=int(cursor.timestamp()),
            end_ts=int(window_end.timestamp()),
            max_pages=max(1, int(args.max_pages)),
        )
        fetched_hits.extend(hits)
        cursor = window_end

    existing_raw = read_existing_raw(args.raw_out)
    by_hn_id: dict[int, dict[str, Any]] = {}
    for row in existing_raw:
        hn_id = int(row.get("hn_id") or 0)
        if hn_id > 0:
            by_hn_id[hn_id] = row

    for hit in fetched_hits:
        hn_id = int(hit.get("objectID") or 0)
        if hn_id <= 0:
            continue
        title = str(hit.get("title") or hit.get("story_title") or "").strip()
        text = str(hit.get("story_text") or hit.get("comment_text") or "").strip()
        url = str(hit.get("url") or hit.get("story_url") or "").strip()
        points = int(hit.get("points") or 0)
        comments = int(hit.get("num_comments") or 0)
        created_unix = int(hit.get("created_at_i") or int(time.time()))
        created_iso = datetime.fromtimestamp(created_unix, tz=timezone.utc).isoformat()
        story = {
            "id": hn_id,
            "type": "story",
            "title": title,
            "url": url,
            "score": points,
            "descendants": comments,
            "time": created_unix,
            "text": text,
        }
        comments_sample: list[str] = []
        candidates = extract_story_candidates(story, comments_sample)
        if not candidates:
            continue
        impressions = _score_impressions(points, comments, created_unix)
        summary = text[:280] if text else (title[:280] if title else "No summary text available.")
        keywords = extract_story_keywords(f"{title} {text}")
        by_hn_id[hn_id] = {
            "hn_id": hn_id,
            "type": "story",
            "title": title,
            "url": url,
            "score": points,
            "descendants": comments,
            "hn_created_at": created_iso,
            "impressions": impressions,
            "keywords": keywords,
            "summary": summary,
            "comments_sample_count": 0,
            "entity_candidates": candidates,
            "fetched_at": now_iso(),
        }

    raw_rows = sorted(by_hn_id.values(), key=lambda row: str(row.get("hn_created_at") or ""), reverse=True)

    entity_mentions: list[dict[str, Any]] = []
    for row in raw_rows:
        hn_id = int(row.get("hn_id") or 0)
        created_iso = str(row.get("hn_created_at") or "")
        created_unix = int(datetime.fromisoformat(created_iso).timestamp()) if created_iso else int(time.time())
        for cand in row.get("entity_candidates") or []:
            if not isinstance(cand, dict):
                continue
            entity_mentions.append(
                {
                    "entity": str(cand.get("entity") or ""),
                    "confidence": float(cand.get("confidence") or 0.0),
                    "reasons": cand.get("reasons") if isinstance(cand.get("reasons"), list) else [],
                    "hn_id": hn_id,
                    "title": str(row.get("title") or ""),
                    "url": str(row.get("url") or ""),
                    "summary": str(row.get("summary") or ""),
                    "keywords": row.get("keywords") if isinstance(row.get("keywords"), list) else [],
                    "score": int(row.get("score") or 0),
                    "descendants": int(row.get("descendants") or 0),
                    "impressions": int(row.get("impressions") or 0),
                    "hn_created_unix": created_unix,
                    "hn_created_at": created_iso,
                }
            )

    by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    entity_display: dict[str, str] = {}
    for mention in entity_mentions:
        key = "".join(ch for ch in mention["entity"].lower() if ch.isalnum())
        if not key:
            continue
        by_entity[key].append(mention)
        current = entity_display.get(key)
        if not current or len(mention["entity"]) < len(current):
            entity_display[key] = mention["entity"]

    entity_rows: list[dict[str, Any]] = []
    node_rows: list[dict[str, Any]] = []
    now_ts = int(time.time())
    for key, mentions in by_entity.items():
        entity = entity_display.get(key, mentions[0]["entity"])
        mentions_sorted = sorted(mentions, key=lambda m: m["impressions"], reverse=True)
        count = len(mentions)
        avg_conf = sum(m["confidence"] for m in mentions) / max(1, count)
        boosted_conf = min(1.0, avg_conf + min(0.25, 0.05 * max(0, count - 1)))
        if boosted_conf < float(args.entity_threshold):
            continue
        impressions_total = sum(int(m["impressions"]) for m in mentions)
        mention_1h = sum(1 for m in mentions if (now_ts - int(m["hn_created_unix"])) <= 3600)
        mention_24h = sum(1 for m in mentions if (now_ts - int(m["hn_created_unix"])) <= 86400)
        keyword_counts = Counter()
        reasons = set()
        for m in mentions:
            keyword_counts.update(m["keywords"])
            reasons.update(m["reasons"])

        entity_rows.append(
            {
                "entity": entity,
                "confidence": round(boosted_conf, 3),
                "impressions": impressions_total,
                "stories": count,
                "mention_count_1h": mention_1h,
                "mention_count_24h": mention_24h,
                "sources": [SOURCE_ID],
                "source_counts": {SOURCE_ID: count},
                "top_keywords": [k for k, _ in keyword_counts.most_common(6)],
                "evidence_count": count,
                "quality_signals": sorted(reasons),
                "first_seen_at": min(m["hn_created_at"] for m in mentions if m["hn_created_at"]),
                "last_seen_at": max(m["hn_created_at"] for m in mentions if m["hn_created_at"]),
            }
        )
        for mention in mentions_sorted[:40]:
            node_rows.append(
                {
                    "id": f"hn-{mention['hn_id']}-{''.join(ch for ch in entity.lower() if ch.isalnum() or ch == ' ').replace(' ', '-')}",
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
                }
            )

    max_impressions = max((row["impressions"] for row in entity_rows), default=1)
    for row in entity_rows:
        row["trend_score"] = round((row["impressions"] / max_impressions) * 70.0 + row["confidence"] * 30.0, 2)
        row["velocity_delta_pct"] = 0.0
        row["spike_detected"] = row["mention_count_1h"] >= 3

    entity_rows.sort(key=lambda row: row["trend_score"], reverse=True)
    node_rows.sort(key=lambda row: (row["interactions"] + row["views"] * 0.18), reverse=True)

    write_jsonl(
        args.raw_out,
        raw_rows,
        description="HN raw rows with combined feed and Algolia backfill history.",
    )
    write_json(
        args.entities_out,
        {
            "_meta": {
                "description": "Entity-level aggregation derived from HN stories (including Algolia backfill).",
                "source": "hackernews",
                "schema_version": "1.2",
            },
            "generated_at": now_iso(),
            "mode": "backfill",
            "story_count": len(raw_rows),
            "entity_count": len(entity_rows),
            "entities": entity_rows,
        },
    )
    write_json(
        args.nodes_out,
        {
            "_meta": {
                "description": "Source-level interaction nodes for HN entities (including backfill).",
                "source": "hackernews",
                "schema_version": "1.2",
            },
            "generated_at": now_iso(),
            "source_nodes": node_rows,
        },
    )
    write_json(
        args.state_out,
        {
            "_meta": {
                "description": "Backfill run metadata for HN Algolia import.",
                "source": "hackernews",
                "schema_version": "1.2",
            },
            "last_run_started_at": started_at,
            "last_run_finished_at": now_iso(),
            "months": months,
            "hits_fetched": len(fetched_hits),
            "stories_written": len(raw_rows),
            "entities_written": len(entity_rows),
            "nodes_written": len(node_rows),
        },
    )
    print(
        f"done: hits={len(fetched_hits)} stories={len(raw_rows)} entities={len(entity_rows)} nodes={len(node_rows)}"
    )


if __name__ == "__main__":
    main()

