#!/usr/bin/env python3
"""
GitHub JSONL -> Scout JSON pipeline.

Transforms repo-level GitHub scrape output into source-specific data artifacts:
  - github_raw.jsonl
  - github_entities.json
  - github_source_nodes.json
  - github_state.json
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SOURCE_ID = "github"
SOURCE_NAME = "GitHub"

ENTITY_STOP = {
    "Github",
    "Git",
    "Api",
    "Sdk",
    "Cli",
    "Tool",
    "Tools",
    "Demo",
    "Project",
    "Template",
    "Starter",
    "Example",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_hours(value: str | None) -> float | None:
    parsed = parse_iso(value)
    if not parsed:
        return None
    delta = datetime.now(timezone.utc) - parsed
    return max(0.0, delta.total_seconds() / 3600.0)


def clean_entity(value: str) -> str:
    entity = value.strip()
    entity = re.sub(r"[._]+", " ", entity)
    entity = re.sub(r"-+", " ", entity)
    entity = re.sub(r"\s+", " ", entity).strip()
    if not entity:
        return ""
    if entity.islower():
        entity = " ".join(part.capitalize() for part in entity.split())
    return entity


def entity_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def is_valid_entity(value: str) -> bool:
    if not value:
        return False
    if len(value) < 2 or len(value) > 48:
        return False
    if value in ENTITY_STOP:
        return False
    if len(value.split()) > 4:
        return False
    if value.isdigit():
        return False
    return True


def pick_entity(repo: dict[str, Any]) -> tuple[str, list[str]]:
    owner = clean_entity(str(repo.get("owner_login") or ""))
    name = clean_entity(str(repo.get("name") or ""))
    full_name = str(repo.get("full_name") or "")
    owner_type = str(repo.get("owner_type") or "")

    reasons: list[str] = []
    candidate = ""

    if owner and owner_type == "Organization":
        candidate = owner
        reasons.append("owner_org")
    elif owner and bool(repo.get("likely_company_repo")):
        candidate = owner
        reasons.append("owner_likely_company")
    elif name:
        candidate = name
        reasons.append("repo_name")

    if "/" in full_name and name:
        reasons.append("full_name_present")

    return candidate, reasons


def score_impressions(repo: dict[str, Any]) -> int:
    stars = int(repo.get("stargazers_count") or 0)
    forks = int(repo.get("forks_count") or 0)
    watchers = int(repo.get("watchers_count") or 0)
    issues = int(repo.get("open_issues_count") or 0)
    velocity = float(repo.get("star_velocity_per_day") or 0.0)
    recent_release = bool(repo.get("has_recent_release"))
    pushed_age = age_hours(repo.get("pushed_at"))

    base = stars * 8 + forks * 6 + watchers * 3 + issues * 1
    velocity_boost = int(min(300, velocity * 45))
    release_boost = 120 if recent_release else 0
    recency_boost = 0
    if pushed_age is not None:
        recency_boost = int(max(0.0, 72.0 - pushed_age) * 2.0)
    return max(0, base + velocity_boost + release_boost + recency_boost)


def build_keywords(repo: dict[str, Any]) -> list[str]:
    out: list[str] = []
    topics = repo.get("topics") or []
    if isinstance(topics, list):
        out.extend([str(t).lower() for t in topics if isinstance(t, str)])

    language = repo.get("language")
    if isinstance(language, str) and language.strip():
        out.append(language.lower())

    desc = str(repo.get("description") or "")
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9\-\+]{2,}", desc.lower())
    for token in tokens:
        if token in {"with", "from", "into", "this", "that", "have", "your", "about"}:
            continue
        out.append(token)

    counts = Counter(out)
    return [token for token, _ in counts.most_common(8)]


def confidence_for_repo(repo: dict[str, Any], reasons: list[str]) -> float:
    confidence = 0.35
    if "owner_org" in reasons:
        confidence += 0.25
    if "owner_likely_company" in reasons:
        confidence += 0.15
    if bool(repo.get("has_recent_release")):
        confidence += 0.1
    stars = int(repo.get("stargazers_count") or 0)
    if stars >= 100:
        confidence += 0.1
    elif stars >= 25:
        confidence += 0.05
    velocity = float(repo.get("star_velocity_per_day") or 0.0)
    if velocity >= 1.0:
        confidence += 0.1
    return round(min(1.0, confidence), 3)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for index, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if index == 0 and isinstance(obj, dict) and "_meta" in obj:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]], description: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "_meta": {
            "description": description,
            "source": SOURCE_ID,
            "schema_version": "1.0",
            "generated_at": now_iso(),
            "record_type": "meta",
        }
    }
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transform GitHub scrape rows into Scout data files")
    parser.add_argument("--in", dest="input_path", type=Path, default=Path("data/github_repos.jsonl"))
    parser.add_argument("--raw-out", type=Path, default=Path("data/github_data/github_raw.jsonl"))
    parser.add_argument("--entities-out", type=Path, default=Path("data/github_data/github_entities.json"))
    parser.add_argument("--nodes-out", type=Path, default=Path("data/github_data/github_source_nodes.json"))
    parser.add_argument("--state-out", type=Path, default=Path("data/github_data/github_state.json"))
    parser.add_argument("--min-stars", type=int, default=5)
    parser.add_argument("--min-confidence", type=float, default=0.45)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started_at = now_iso()

    repo_rows = read_jsonl(args.input_path)
    transformed: list[dict[str, Any]] = []
    mentions: list[dict[str, Any]] = []

    for repo in repo_rows:
        if bool(repo.get("is_private")) or bool(repo.get("is_archived")) or bool(repo.get("is_fork")):
            continue

        stars = int(repo.get("stargazers_count") or 0)
        forks = int(repo.get("forks_count") or 0)
        watchers = int(repo.get("watchers_count") or 0)
        recent_release = bool(repo.get("has_recent_release"))
        description = str(repo.get("description") or "").strip()

        quality_gate = (
            stars >= args.min_stars
            or forks >= 2
            or watchers >= 10
            or (recent_release and stars >= 2)
        )
        if not quality_gate:
            continue
        if len(description) < 16:
            continue

        entity, reasons = pick_entity(repo)
        entity = clean_entity(entity)
        if not is_valid_entity(entity):
            continue

        confidence = confidence_for_repo(repo, reasons)
        if confidence < args.min_confidence:
            continue

        impressions = score_impressions(repo)
        keywords = build_keywords(repo)
        summary = description or "No repository description provided."

        transformed.append(
            {
                "repo_id": repo.get("id"),
                "full_name": repo.get("full_name"),
                "name": repo.get("name"),
                "entity": entity,
                "url": repo.get("html_url"),
                "owner_login": repo.get("owner_login"),
                "owner_type": repo.get("owner_type"),
                "language": repo.get("language"),
                "topics": repo.get("topics") or [],
                "stars": stars,
                "forks": forks,
                "watchers": watchers,
                "open_issues": int(repo.get("open_issues_count") or 0),
                "updated_at": repo.get("updated_at"),
                "pushed_at": repo.get("pushed_at"),
                "latest_release_tag": repo.get("latest_release_tag"),
                "latest_release_published_at": repo.get("latest_release_published_at"),
                "has_recent_release": bool(repo.get("has_recent_release")),
                "query": repo.get("query"),
                "impressions": impressions,
                "keywords": keywords,
                "summary": summary[:320],
                "entity_confidence": confidence,
                "entity_reasons": reasons,
                "fetched_at": now_iso(),
            }
        )

        mentions.append(
            {
                "entity": entity,
                "confidence": confidence,
                "reasons": reasons,
                "repo_id": repo.get("id"),
                "full_name": repo.get("full_name"),
                "headline": repo.get("full_name") or repo.get("name") or entity,
                "url": repo.get("html_url"),
                "summary": summary[:320],
                "keywords": keywords,
                "stars": stars,
                "forks": forks,
                "watchers": watchers,
                "open_issues": int(repo.get("open_issues_count") or 0),
                "impressions": impressions,
                "updated_at": repo.get("updated_at"),
                "pushed_at": repo.get("pushed_at"),
            }
        )

    by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    display_name: dict[str, str] = {}
    for mention in mentions:
        key = entity_key(mention["entity"])
        if not key:
            continue
        by_entity[key].append(mention)
        current = display_name.get(key)
        if not current or len(mention["entity"]) < len(current):
            display_name[key] = mention["entity"]

    now_ts = int(time.time())
    entity_rows: list[dict[str, Any]] = []
    node_rows: list[dict[str, Any]] = []

    for key, entity_mentions in by_entity.items():
        entity = display_name.get(key, entity_mentions[0]["entity"])
        mentions_sorted = sorted(entity_mentions, key=lambda m: m["impressions"], reverse=True)
        impressions_total = sum(m["impressions"] for m in entity_mentions)
        count = len(entity_mentions)
        avg_conf = round(sum(m["confidence"] for m in entity_mentions) / count, 3)
        boosted_conf = min(1.0, avg_conf + min(0.2, 0.04 * max(0, count - 1)))

        mention_1h = 0
        mention_24h = 0
        for mention in entity_mentions:
            for stamp in (mention.get("pushed_at"), mention.get("updated_at")):
                parsed = parse_iso(stamp)
                if not parsed:
                    continue
                age = now_ts - int(parsed.timestamp())
                if age <= 3600:
                    mention_1h += 1
                if age <= 86400:
                    mention_24h += 1
                break

        keyword_counts = Counter()
        for mention in entity_mentions:
            keyword_counts.update(mention["keywords"])

        entity_rows.append(
            {
                "entity": entity,
                "confidence": round(boosted_conf, 3),
                "impressions": impressions_total,
                "repos": count,
                "mention_count_1h": mention_1h,
                "mention_count_24h": mention_24h,
                "sources": [SOURCE_ID],
                "source_counts": {SOURCE_ID: count},
                "top_keywords": [token for token, _ in keyword_counts.most_common(6)],
                "evidence_count": count,
                "quality_signals": sorted({reason for m in entity_mentions for reason in m["reasons"]}),
                "first_seen_at": min((m.get("updated_at") or m.get("pushed_at") or now_iso()) for m in entity_mentions),
                "last_seen_at": max((m.get("updated_at") or m.get("pushed_at") or now_iso()) for m in entity_mentions),
            }
        )

        for mention in mentions_sorted:
            interactions = int(mention["stars"] + mention["forks"] * 4 + mention["open_issues"] + mention["watchers"] * 0.5)
            node_rows.append(
                {
                    "id": f"github-{mention['repo_id']}-{re.sub(r'[^a-z0-9]+', '-', entity.lower()).strip('-')}",
                    "entity": entity,
                    "source_id": SOURCE_ID,
                    "source_name": SOURCE_NAME,
                    "headline": mention["headline"],
                    "url": mention["url"],
                    "summary": mention["summary"],
                    "interactions": interactions,
                    "views": int(max(mention["impressions"], mention["stars"] * 10)),
                    "impressions": mention["impressions"],
                    "repo_id": mention["repo_id"],
                    "published_at": mention.get("pushed_at") or mention.get("updated_at"),
                    "confidence": mention["confidence"],
                }
            )

    max_impressions = max((row["impressions"] for row in entity_rows), default=1)
    for row in entity_rows:
        impression_component = (row["impressions"] / max_impressions) * 70.0
        confidence_component = row["confidence"] * 30.0
        row["trend_score"] = round(impression_component + confidence_component, 2)
        row["velocity_delta_pct"] = 0.0
        row["spike_detected"] = row["mention_count_24h"] >= 2

    entity_rows.sort(key=lambda r: r["trend_score"], reverse=True)
    node_rows.sort(key=lambda r: (r["interactions"] + r["views"] * 0.18), reverse=True)

    write_jsonl(
        args.raw_out,
        transformed,
        description=(
            "Raw GitHub repository records filtered for startup discovery and enriched with "
            "entity mapping, engagement impressions, and keyword extraction."
        ),
    )
    write_json(
        args.entities_out,
        {
            "_meta": {
                "description": (
                    "Entity-level aggregation from GitHub repository activity. "
                    "Use for source-specific rankings and cross-source merge."
                ),
                "source": SOURCE_ID,
                "schema_version": "1.0",
            },
            "generated_at": now_iso(),
            "input_repo_count": len(repo_rows),
            "repo_count": len(transformed),
            "entity_count": len(entity_rows),
            "entities": entity_rows,
        },
    )
    write_json(
        args.nodes_out,
        {
            "_meta": {
                "description": (
                    "Source node records for GitHub. Each node represents one repository mapped "
                    "to a detected entity."
                ),
                "source": SOURCE_ID,
                "schema_version": "1.0",
            },
            "generated_at": now_iso(),
            "source_nodes": node_rows,
        },
    )
    write_json(
        args.state_out,
        {
            "_meta": {
                "description": "Pipeline run metadata for GitHub transform job.",
                "source": SOURCE_ID,
                "schema_version": "1.0",
            },
            "last_run_started_at": started_at,
            "last_run_finished_at": now_iso(),
            "input_repo_count": len(repo_rows),
            "repos_written": len(transformed),
            "entities_written": len(entity_rows),
            "nodes_written": len(node_rows),
        },
    )

    print(
        f"done: repos={len(transformed)} entities={len(entity_rows)} nodes={len(node_rows)} "
        f"-> {args.entities_out} {args.nodes_out}"
    )


if __name__ == "__main__":
    main()
