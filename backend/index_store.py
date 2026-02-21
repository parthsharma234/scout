#!/usr/bin/env python3
"""
Read/write index payloads from SQLite canonical store.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from db import get_conn, json_loads, now_iso
except ModuleNotFoundError:
    from backend.db import get_conn, json_loads, now_iso  # type: ignore


def _row_to_node(row: Any) -> dict[str, Any]:
    return {
        "id": row["node_id"],
        "source_id": row["source_id"],
        "source_name": row["source_name"],
        "headline": row["headline"] or "",
        "url": row["url"] or "",
        "summary": row["summary"] or "",
        "interactions": int(row["interactions"] or 0),
        "views": int(row["views"] or 0),
        "impressions": int(row["impressions"] or 0),
        "published_at": row["published_at"],
        "confidence": float(row["confidence"] or 0.0),
        "node_type": row["node_type"] or "source_raw",
    }


def build_index_payload(limit_nodes: int = 30) -> dict[str, Any]:
    with get_conn() as conn:
        profiles = conn.execute(
            """
            SELECT id, entity_key, display_name, confidence, trend_score,
                   momentum_score, is_known_incumbent,
                   mention_count_1h, mention_count_24h, activity_last_30d,
                   first_seen_at, last_seen_at, sources_json, source_counts_json,
                   top_keywords_json, quality_signals_json, node_count
            FROM entity_profiles
            ORDER BY trend_score DESC, display_name ASC
            """
        ).fetchall()
        stats = conn.execute(
            """
            SELECT source, MAX(finished_at) AS last_run, SUM(written_count) AS total_written
            FROM ingestion_runs
            WHERE status='success'
            GROUP BY source
            """
        ).fetchall()
        source_seen = {str(row["source"]) for row in stats if row["source"]}
        rows: list[dict[str, Any]] = []
        for profile in profiles:
            nodes = conn.execute(
                """
                SELECT node_id, source_id, source_name, headline, url, summary,
                       interactions, views, impressions, published_at, confidence, node_type
                FROM entity_nodes
                WHERE entity_id=?
                ORDER BY (interactions + views * 0.18 + impressions * 0.05) DESC
                LIMIT ?
                """,
                (int(profile["id"]), int(limit_nodes)),
            ).fetchall()
            source_interactions: dict[str, int] = {}
            for node in nodes:
                src = str(node["source_id"] or "")
                if not src:
                    continue
                score = int(
                    max(
                        0,
                        int(node["interactions"] or 0)
                        + int(round(float(node["views"] or 0) * 0.22))
                        + int(round(float(node["impressions"] or 0) * 0.04)),
                    )
                )
                source_interactions[src] = int(source_interactions.get(src, 0) + score)
            rows.append(
                {
                    "entity_key": profile["entity_key"],
                    "entity": profile["display_name"],
                    "confidence": float(profile["confidence"] or 0.0),
                    "trend_score": float(profile["trend_score"] or 0.0),
                    "momentum_score": float(profile["momentum_score"] or 0.0),
                    "is_known_incumbent": bool(int(profile["is_known_incumbent"] or 0)),
                    "impressions": int(sum(int(n["impressions"] or 0) for n in nodes)),
                    "mention_count_1h": int(profile["mention_count_1h"] or 0),
                    "mention_count_24h": int(profile["mention_count_24h"] or 0),
                    "spike_detected": int(profile["mention_count_1h"] or 0) >= 3,
                    "sources": json_loads(profile["sources_json"], []),
                    "source_counts": json_loads(profile["source_counts_json"], {}),
                    "source_interactions": source_interactions,
                    "top_keywords": json_loads(profile["top_keywords_json"], []),
                    "quality_signals": json_loads(profile["quality_signals_json"], []),
                    "node_count": int(profile["node_count"] or 0),
                    "top_nodes": [_row_to_node(node) for node in nodes],
                    "first_seen_at": profile["first_seen_at"],
                    "last_seen_at": profile["last_seen_at"],
                    "activity_last_30d": int(profile["activity_last_30d"] or 0),
                }
            )

    payload = {
        "_meta": {
            "description": (
                "Unified cross-source entity index for niche search. "
                "Aggregates canonicalized entities from SQLite pipeline store."
            ),
            "schema_version": "2.0",
            "generated_at": now_iso(),
            "sources_expected": ["github", "hackernews", "producthunt"],
        },
        "entity_count": len(rows),
        "stats": {
            "sources_seen": len(source_seen),
            "entity_rows_seen": len(rows),
            "node_rows_seen": sum(int(row.get("node_count") or 0) for row in rows),
        },
        "entities": rows,
    }
    return payload


def export_index_json(out_path: Path) -> dict[str, Any]:
    payload = build_index_payload()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def get_entity_nodes(entity_key: str, include_enriched: bool = True, limit: int = 40) -> list[dict[str, Any]]:
    with get_conn() as conn:
        profile = conn.execute(
            "SELECT id FROM entity_profiles WHERE entity_key=?",
            (entity_key,),
        ).fetchone()
        if not profile:
            return []
        if include_enriched:
            rows = conn.execute(
                """
                SELECT node_id, source_id, source_name, headline, url, summary,
                       interactions, views, impressions, published_at, confidence, node_type
                FROM entity_nodes
                WHERE entity_id=?
                ORDER BY (interactions + views * 0.18 + impressions * 0.05) DESC
                LIMIT ?
                """,
                (int(profile["id"]), int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT node_id, source_id, source_name, headline, url, summary,
                       interactions, views, impressions, published_at, confidence, node_type
                FROM entity_nodes
                WHERE entity_id=? AND node_type='source_raw'
                ORDER BY (interactions + views * 0.18 + impressions * 0.05) DESC
                LIMIT ?
                """,
                (int(profile["id"]), int(limit)),
            ).fetchall()
    return [_row_to_node(row) for row in rows]


def get_entity_history(entity_key: str, window_days: int = 180) -> list[dict[str, Any]]:
    with get_conn() as conn:
        profile = conn.execute(
            "SELECT id FROM entity_profiles WHERE entity_key=?",
            (entity_key,),
        ).fetchone()
        if not profile:
            return []
        rows = conn.execute(
            """
            SELECT date, mention_count, impressions, trend_score, source_counts_json, activity_score
            FROM entity_daily_metrics
            WHERE entity_id=?
            ORDER BY date DESC
            LIMIT ?
            """,
            (int(profile["id"]), int(max(1, window_days))),
        ).fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "date": row["date"],
                "mention_count": int(row["mention_count"] or 0),
                "impressions": int(row["impressions"] or 0),
                "trend_score": float(row["trend_score"] or 0.0),
                "source_counts": json_loads(row["source_counts_json"], {}),
                "activity_score": float(row["activity_score"] or 0.0),
            }
        )
    out.reverse()
    return out


def get_sources_payload() -> dict[str, Any]:
    known = [
        ("hackernews", "HN"),
        ("github", "GitHub"),
        ("producthunt", "PH"),
        ("reddit", "Reddit"),
        ("techcrunch", "RSS"),
        ("twitter", "Twitter"),
    ]
    with get_conn() as conn:
        source_totals = Counter()
        profile_rows = conn.execute("SELECT source_counts_json FROM entity_profiles").fetchall()
        for row in profile_rows:
            counts = json_loads(row["source_counts_json"], {})
            if isinstance(counts, dict):
                for src, value in counts.items():
                    try:
                        source_totals[str(src)] += int(value)
                    except (TypeError, ValueError):
                        continue
        runs = conn.execute(
            """
            SELECT source, status, finished_at, error, written_count
            FROM ingestion_runs
            WHERE id IN (
              SELECT MAX(id) FROM ingestion_runs GROUP BY source
            )
            """
        ).fetchall()
    run_map = {str(row["source"]): row for row in runs if row["source"]}
    rows = []
    for source_id, label in known:
        run = run_map.get(source_id)
        items = int(source_totals.get(source_id, 0))
        status = "cached"
        error_message = None
        last_scraped = ""
        if run:
            last_scraped = str(run["finished_at"] or "")
            if str(run["status"]) == "success":
                status = "live" if items > 0 else "cached"
            else:
                status = "error"
                error_message = str(run["error"] or "pipeline failed")
        else:
            status = "live" if items > 0 else "cached"
        rows.append(
            {
                "id": source_id,
                "label": label,
                "status": status,
                "items_ingested": items,
                "last_scraped": last_scraped,
                "error_message": error_message,
            }
        )
    return {"sources": rows, "count": len(rows)}


def get_pipeline_status() -> dict[str, Any]:
    with get_conn() as conn:
        recent_runs = conn.execute(
            """
            SELECT id, source, mode, status, started_at, finished_at, fetched_count, written_count, error
            FROM ingestion_runs
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()
        lock_row = conn.execute(
            "SELECT value, updated_at FROM pipeline_state WHERE key='pipeline_lock'"
        ).fetchone()
        last_success_row = conn.execute(
            """
            SELECT MAX(finished_at) AS last_success_at
            FROM ingestion_runs
            WHERE status='success'
            """
        ).fetchone()
        backfill_rows = conn.execute(
            """
            SELECT key, value, updated_at
            FROM pipeline_state
            WHERE key LIKE 'backfill\\_%' ESCAPE '\\'
            """
        ).fetchall()

    backfill_state: dict[str, Any] = {}
    for row in backfill_rows:
        key = str(row["key"] or "")
        if not key:
            continue
        # keys: backfill_hackernews_completed, backfill_hackernews_completed_at, backfill_hackernews_months
        parts = key.split("_", 2)
        if len(parts) < 3:
            continue
        source = parts[1]
        metric = parts[2]
        bucket = backfill_state.setdefault(source, {})
        bucket[metric] = row["value"]
        bucket[f"{metric}_updated_at"] = row["updated_at"]

    return {
        "scheduler": {
            "lock_owner": str(lock_row["value"]) if lock_row else "",
            "lock_updated_at": str(lock_row["updated_at"]) if lock_row else "",
            "last_success_at": str(last_success_row["last_success_at"] or ""),
        },
        "backfill": backfill_state,
        "runs": [
            {
                "id": int(row["id"]),
                "source": row["source"],
                "mode": row["mode"],
                "status": row["status"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "fetched_count": int(row["fetched_count"] or 0),
                "written_count": int(row["written_count"] or 0),
                "error": row["error"],
            }
            for row in recent_runs
        ],
    }
