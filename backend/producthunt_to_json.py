#!/usr/bin/env python3
"""
Product Hunt -> Scout JSON pipeline.

Outputs (default in ./data/producthunt_data):
  - producthunt_raw.jsonl
  - producthunt_entities.json
  - producthunt_source_nodes.json
  - producthunt_state.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from config import get_producthunt_token, load_env_file
    from db import migrate
    from pipeline_store import ingest_source_artifacts
except ModuleNotFoundError:
    from backend.config import get_producthunt_token, load_env_file  # type: ignore
    from backend.db import migrate  # type: ignore
    from backend.pipeline_store import ingest_source_artifacts  # type: ignore

API_ENDPOINT = "https://api.producthunt.com/v2/api/graphql"
SOURCE_ID = "producthunt"
SOURCE_NAME = "Product Hunt"

EXCLUDED_INCUMBENTS = {
    "OpenAI",
    "Anthropic",
    "Google",
    "Microsoft",
    "Meta",
    "Amazon",
    "Apple",
    "GitHub",
    "Gitlab",
    "Stripe",
    "Databricks",
    "Cloudflare",
    "Notion",
    "Figma",
    "Netlify",
    "Vercel",
    "Visual Studio",
    "Visual Studio Code",
    "VS Code",
    "Vscode",
}

QUERY_POSTS = """
query Posts($first: Int!, $after: String, $order: PostsOrder) {
  posts(first: $first, after: $after, order: $order) {
    edges {
      node {
        id
        name
        tagline
        description
        url
        createdAt
        votesCount
        commentsCount
        reviewsRating
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_token() -> str:
    return get_producthunt_token() or os.getenv("PHUNT", "").strip() or os.getenv("PRODUCTHUNT_DEVELOPER_TOKEN", "").strip()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_age_hours(value: str | None) -> float | None:
    parsed = parse_iso(value)
    if not parsed:
        return None
    delta = datetime.now(timezone.utc) - parsed
    return max(0.0, delta.total_seconds() / 3600.0)


def clean_entity(value: str) -> str:
    entity = value.strip()
    entity = re.sub(r"\s+", " ", entity)
    entity = re.sub(r"[^\w\s\-\+\.]", "", entity).strip(" -_.")
    return entity


def is_valid_entity(value: str) -> bool:
    if not value:
        return False
    if len(value) < 2 or len(value) > 60:
        return False
    if value.isdigit():
        return False
    if value.title() in EXCLUDED_INCUMBENTS:
        return False
    if value.lower().strip() in {"netlify", "vercel", "visual studio", "visual studio code", "vscode", "vs code"}:
        return False
    return True


def extract_keywords(name: str, tagline: str, description: str, top_n: int = 8) -> list[str]:
    text = f"{name} {tagline} {description}".lower()
    tokens = re.findall(r"[a-z][a-z0-9\-\+]{2,}", text)
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "into", "your", "you", "have", "has",
        "will", "are", "was", "were", "can", "just", "more", "all", "build", "product", "products",
    }
    filtered = [token for token in tokens if token not in stop]
    counts = Counter(filtered)
    return [token for token, _ in counts.most_common(top_n)]


def score_impressions(votes: int, comments: int, rating: float, created_at: str | None) -> int:
    age_hours = get_age_hours(created_at)
    base = votes * 11 + comments * 9 + int(rating * 28)
    recency = 0
    if age_hours is not None:
        recency = int(max(0.0, 120.0 - age_hours) * 2.2)
    return max(0, base + recency)


def graphql_request(token: str, query: str, variables: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        API_ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "scout-producthunt-json/0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_posts(
    token: str,
    limit_posts: int,
    order: str,
    sleep_seconds: float,
    timeout_seconds: int,
    min_created_days: int = 0,
) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    after: str | None = None
    cutoff = None
    if int(min_created_days) > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(min_created_days))

    while len(posts) < limit_posts:
        reached_cutoff = False
        first = min(20, limit_posts - len(posts))
        payload = graphql_request(token, QUERY_POSTS, {"first": first, "after": after, "order": order}, timeout_seconds)
        if payload.get("errors"):
            raise RuntimeError(f"Product Hunt API returned errors: {payload['errors']}")

        bucket = (payload.get("data", {}) or {}).get("posts", {})
        edges = bucket.get("edges", [])
        for edge in edges:
            node = edge.get("node") or {}
            if not isinstance(node, dict):
                continue
            if cutoff:
                created = parse_iso(str(node.get("createdAt") or ""))
                if created and created < cutoff:
                    if order == "NEWEST":
                        reached_cutoff = True
                        break
                    continue
            posts.append(node)
            if len(posts) >= limit_posts:
                break

        if reached_cutoff:
            break

        page_info = bucket.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
        if not after:
            break
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return posts


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
    parser = argparse.ArgumentParser(description="Product Hunt to JSON pipeline for Scout")
    parser.add_argument("--limit-posts", type=int, default=240)
    parser.add_argument("--order", choices=["RANKING", "VOTES", "NEWEST"], default="VOTES")
    parser.add_argument(
        "--min-created-days",
        type=int,
        default=0,
        help="When >0, keep only posts with createdAt within this many days (best with --order NEWEST).",
    )
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--min-votes", type=int, default=2)
    parser.add_argument("--raw-out", type=Path, default=Path("data/producthunt_data/producthunt_raw.jsonl"))
    parser.add_argument("--entities-out", type=Path, default=Path("data/producthunt_data/producthunt_entities.json"))
    parser.add_argument("--nodes-out", type=Path, default=Path("data/producthunt_data/producthunt_source_nodes.json"))
    parser.add_argument("--state-out", type=Path, default=Path("data/producthunt_data/producthunt_state.json"))
    parser.add_argument("--no-db-sync", action="store_true", help="Skip SQLite sync step")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file()
    started_at = now_iso()
    token = get_token()
    if not token:
        raise ValueError("Missing Product Hunt token. Set PRODUCT_HUNT, PHUNT, or PRODUCTHUNT_DEVELOPER_TOKEN.")

    posts = fetch_posts(
        token=token,
        limit_posts=args.limit_posts,
        order=args.order,
        sleep_seconds=args.sleep_seconds,
        timeout_seconds=args.timeout_seconds,
        min_created_days=max(0, int(args.min_created_days)),
    )

    raw_rows: list[dict[str, Any]] = []
    mentions: list[dict[str, Any]] = []

    for post in posts:
        name = clean_entity(str(post.get("name") or ""))
        if not is_valid_entity(name):
            continue

        votes = int(post.get("votesCount") or 0)
        comments = int(post.get("commentsCount") or 0)
        rating = float(post.get("reviewsRating") or 0.0)
        if votes < args.min_votes:
            continue

        tagline = str(post.get("tagline") or "")
        description = str(post.get("description") or "")
        url = str(post.get("url") or "")
        created_at = post.get("createdAt")
        impressions = score_impressions(votes, comments, rating, created_at)
        keywords = extract_keywords(name, tagline, description)
        summary = (description or tagline or "No summary available.")[:320]

        raw_rows.append(
            {
                "ph_id": post.get("id"),
                "name": name,
                "tagline": tagline,
                "description": description,
                "url": url,
                "created_at": created_at,
                "votes_count": votes,
                "comments_count": comments,
                "reviews_rating": rating,
                "impressions": impressions,
                "keywords": keywords,
                "summary": summary,
                "entity_candidates": [
                    {
                        "entity": name,
                        "confidence": 0.8 if votes >= 25 else 0.7,
                        "reasons": ["post_name", "votes_threshold"],
                    }
                ],
                "fetched_at": now_iso(),
            }
        )

        mentions.append(
            {
                "entity": name,
                "confidence": 0.8 if votes >= 25 else 0.7,
                "reasons": ["post_name", "votes_threshold"],
                "ph_id": post.get("id"),
                "headline": name,
                "tagline": tagline,
                "url": url,
                "summary": summary,
                "keywords": keywords,
                "votes": votes,
                "comments": comments,
                "rating": rating,
                "impressions": impressions,
                "created_at": created_at,
            }
        )

    by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mention in mentions:
        key = re.sub(r"[^a-z0-9]+", "", mention["entity"].lower())
        if key:
            by_entity[key].append(mention)

    now_ts = int(time.time())
    entity_rows: list[dict[str, Any]] = []
    node_rows: list[dict[str, Any]] = []

    for _, entity_mentions in by_entity.items():
        entity = entity_mentions[0]["entity"]
        mentions_sorted = sorted(entity_mentions, key=lambda m: m["impressions"], reverse=True)
        count = len(entity_mentions)
        impressions_total = sum(m["impressions"] for m in entity_mentions)
        confidence = round(sum(m["confidence"] for m in entity_mentions) / count, 3)

        mention_1h = 0
        mention_24h = 0
        for mention in entity_mentions:
            parsed = parse_iso(mention["created_at"])
            if not parsed:
                continue
            age = now_ts - int(parsed.timestamp())
            if age <= 3600:
                mention_1h += 1
            if age <= 86400:
                mention_24h += 1

        keyword_counts = Counter()
        for mention in entity_mentions:
            keyword_counts.update(mention["keywords"])

        entity_rows.append(
            {
                "entity": entity,
                "confidence": confidence,
                "impressions": impressions_total,
                "posts": count,
                "mention_count_1h": mention_1h,
                "mention_count_24h": mention_24h,
                "sources": [SOURCE_ID],
                "source_counts": {SOURCE_ID: count},
                "top_keywords": [token for token, _ in keyword_counts.most_common(6)],
                "evidence_count": count,
                "quality_signals": sorted({reason for m in entity_mentions for reason in m["reasons"]}),
                "first_seen_at": min((m["created_at"] or now_iso()) for m in entity_mentions),
                "last_seen_at": max((m["created_at"] or now_iso()) for m in entity_mentions),
            }
        )

        for mention in mentions_sorted:
            node_rows.append(
                {
                    "id": f"ph-{mention['ph_id']}-{re.sub(r'[^a-z0-9]+', '-', entity.lower()).strip('-')}",
                    "entity": entity,
                    "source_id": SOURCE_ID,
                    "source_name": SOURCE_NAME,
                    "headline": f"{mention['entity']} — {mention['tagline']}".strip(" —"),
                    "url": mention["url"],
                    "summary": mention["summary"],
                    "interactions": int(mention["votes"] + mention["comments"] * 2 + mention["rating"] * 5),
                    "views": int(max(mention["impressions"], mention["votes"] * 14)),
                    "impressions": mention["impressions"],
                    "ph_id": mention["ph_id"],
                    "published_at": mention["created_at"],
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
        raw_rows,
        description=(
            "Raw Product Hunt post records with engagement metrics, entity candidates, "
            "keywords, and summaries."
        ),
    )
    write_json(
        args.entities_out,
        {
            "_meta": {
                "description": (
                    "Entity-level aggregation from Product Hunt posts. "
                    "Use for source-specific rankings and cross-source merge."
                ),
                "source": SOURCE_ID,
                "schema_version": "1.0",
            },
            "generated_at": now_iso(),
            "post_count": len(raw_rows),
            "entity_count": len(entity_rows),
            "entities": entity_rows,
        },
    )
    write_json(
        args.nodes_out,
        {
            "_meta": {
                "description": (
                    "Source node records for Product Hunt. Each node represents one product post "
                    "mapped to an entity."
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
                "description": "Pipeline run metadata for Product Hunt transform job.",
                "source": SOURCE_ID,
                "schema_version": "1.0",
            },
            "last_run_started_at": started_at,
            "last_run_finished_at": now_iso(),
            "min_created_days": max(0, int(args.min_created_days)),
            "input_post_count": len(posts),
            "posts_written": len(raw_rows),
            "entities_written": len(entity_rows),
            "nodes_written": len(node_rows),
        },
    )

    if not args.no_db_sync:
        migrate()
        ingest_source_artifacts(
            source=SOURCE_ID,
            raw_path=args.raw_out,
            entities_path=args.entities_out,
            nodes_path=args.nodes_out,
            mode="manual",
        )

    print(
        f"done: posts={len(raw_rows)} entities={len(entity_rows)} nodes={len(node_rows)} "
        f"-> {args.entities_out} {args.nodes_out}"
    )


if __name__ == "__main__":
    main()
