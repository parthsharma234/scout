#!/usr/bin/env python3
"""
Read/write index payloads from SQLite canonical store.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from db import get_conn, json_loads, now_iso
except ModuleNotFoundError:
    from backend.db import get_conn, json_loads, now_iso  # type: ignore


EXTERNAL_GITHUB_INDEX = Path("data/index_data/github_index.json")
INDEX_EXCLUDED_KEYS = {
    "vercel",
    "netlify",
    "visualstudio",
    "visualstudiocode",
    "vscode",
    "crates",
    "crate",
    "npmjs",
    "github",
    "gitlab",
}
INDEX_BLOCKED_DOMAINS = {
    "vercel.app",
    "vercel.com",
    "netlify.app",
    "netlify.com",
    "visualstudio.com",
    "visualstudio.microsoft.com",
}
DEFAULT_NEMOTRON_MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"


def _normalize_key(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _domain_root(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower().strip()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def _is_filtered_entity(entity_name: str, entity_key: str, nodes: list[dict[str, Any]] | None = None) -> bool:
    key = _normalize_key(entity_key or entity_name)
    if not key:
        return True
    if key in INDEX_EXCLUDED_KEYS:
        return True
    if nodes:
        url_hits = 0
        blocked_hits = 0
        for node in nodes[:8]:
            if not isinstance(node, dict):
                continue
            domain = _domain_root(str(node.get("url") or ""))
            if not domain:
                continue
            url_hits += 1
            if domain in INDEX_BLOCKED_DOMAINS:
                blocked_hits += 1
        if url_hits > 0 and blocked_hits / max(1, url_hits) >= 0.6:
            return True
    return False


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = (len(ordered) - 1) * max(0.0, min(1.0, q))
    lo = int(pos)
    hi = min(len(ordered) - 1, lo + 1)
    frac = pos - lo
    return float(ordered[lo]) * (1.0 - frac) + float(ordered[hi]) * frac


def _normalize_scores(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    raw_scores = [_safe_float(row.get("trend_score"), 0.0) for row in rows]
    p10 = _percentile(raw_scores, 0.10)
    p95 = _percentile(raw_scores, 0.95)
    span = max(1e-6, p95 - p10)

    interaction_raw: list[float] = []
    for row in rows:
        source_interactions = row.get("source_interactions") or {}
        if not isinstance(source_interactions, dict):
            source_interactions = {}
        score = 0.0
        for src, value in source_interactions.items():
            weight = 1.0
            if str(src) == "producthunt":
                weight = 1.08
            elif str(src) == "github":
                weight = 0.92
            score += weight * _safe_float(value, 0.0)
        interaction_raw.append(score)
    i10 = _percentile(interaction_raw, 0.10)
    i95 = _percentile(interaction_raw, 0.95)
    ispan = max(1e-6, i95 - i10)

    for idx, row in enumerate(rows):
        base_raw = _safe_float(row.get("trend_score"), 0.0)
        base_unit = max(0.0, min(1.0, (base_raw - p10) / span))
        base = (base_unit**0.62) * 100.0

        i_raw = interaction_raw[idx]
        i_unit = max(0.0, min(1.0, (i_raw - i10) / ispan))
        interaction = (i_unit**0.6) * 100.0

        recency = min(
            100.0,
            _safe_int(row.get("mention_count_24h"), 0) * 6.0
            + _safe_int(row.get("mention_count_1h"), 0) * 10.0
            + _safe_int(row.get("activity_last_30d"), 0) * 0.9,
        )
        final_score = base * 0.45 + interaction * 0.35 + recency * 0.20
        row["raw_trend_score"] = base_raw
        row["interaction_score"] = round(interaction, 2)
        row["momentum_score"] = round(base, 2)
        row["trend_score"] = round(min(98.0, max(0.1, final_score)), 2)


def _load_external_github_rows(path: Path = EXTERNAL_GITHUB_INDEX) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    entities = payload.get("entities") if isinstance(payload, dict) else []
    if not isinstance(entities, list):
        return []

    rows: list[dict[str, Any]] = []
    for row in entities:
        if not isinstance(row, dict):
            continue
        entity = str(row.get("entity") or "").strip()
        key = _normalize_key(str(row.get("entity_key") or entity))
        if not entity or _is_filtered_entity(entity, key):
            continue
        source_counts = row.get("source_counts") if isinstance(row.get("source_counts"), dict) else {}
        github_count = _safe_int(source_counts.get("github"), _safe_int(row.get("repos"), 1))
        trend = min(40.0, max(0.0, _safe_float(row.get("trend_score"), 0.0) * 0.42))
        rows.append(
            {
                "entity_key": key,
                "entity": entity,
                "confidence": min(1.0, max(0.0, _safe_float(row.get("confidence"), 0.0))),
                "trend_score": trend,
                "momentum_score": trend * 0.85,
                "is_known_incumbent": False,
                "impressions": _safe_int(row.get("impressions"), 0),
                "mention_count_1h": _safe_int(row.get("mention_count_1h"), 0),
                "mention_count_24h": _safe_int(row.get("mention_count_24h"), 0),
                "spike_detected": bool(row.get("spike_detected")),
                "sources": ["github"],
                "source_counts": {"github": max(1, github_count)},
                "source_interactions": {"github": max(1, _safe_int(row.get("impressions"), 0))},
                "top_keywords": row.get("top_keywords") if isinstance(row.get("top_keywords"), list) else [],
                "quality_signals": row.get("quality_signals") if isinstance(row.get("quality_signals"), list) else [],
                "node_count": max(1, _safe_int(row.get("repos"), _safe_int(row.get("evidence_count"), 1))),
                "top_nodes": [],
                "first_seen_at": row.get("first_seen_at"),
                "last_seen_at": row.get("last_seen_at"),
                "activity_last_30d": max(
                    _safe_int(row.get("mention_count_24h"), 0),
                    _safe_int(row.get("repos"), 1),
                ),
            }
        )
    return rows


def _merge_external_rows(base_rows: list[dict[str, Any]], external_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not external_rows:
        return base_rows
    by_key = {str(row.get("entity_key") or ""): row for row in base_rows}
    for ext in external_rows:
        key = str(ext.get("entity_key") or "")
        if not key:
            continue
        current = by_key.get(key)
        if not current:
            by_key[key] = ext
            continue
        current_sources = set(current.get("sources") or [])
        current_sources.update(ext.get("sources") or [])
        current["sources"] = sorted(current_sources)

        source_counts = current.get("source_counts") or {}
        if not isinstance(source_counts, dict):
            source_counts = {}
        for src, count in (ext.get("source_counts") or {}).items():
            source_counts[str(src)] = max(_safe_int(source_counts.get(src), 0), _safe_int(count, 0))
        current["source_counts"] = source_counts

        source_interactions = current.get("source_interactions") or {}
        if not isinstance(source_interactions, dict):
            source_interactions = {}
        for src, count in (ext.get("source_interactions") or {}).items():
            source_interactions[str(src)] = max(_safe_int(source_interactions.get(src), 0), _safe_int(count, 0))
        current["source_interactions"] = source_interactions

        current["confidence"] = max(_safe_float(current.get("confidence"), 0.0), _safe_float(ext.get("confidence"), 0.0))
        current["trend_score"] = max(_safe_float(current.get("trend_score"), 0.0), _safe_float(ext.get("trend_score"), 0.0))
        current["momentum_score"] = max(
            _safe_float(current.get("momentum_score"), 0.0),
            _safe_float(ext.get("momentum_score"), 0.0),
        )
        current["mention_count_1h"] = max(_safe_int(current.get("mention_count_1h"), 0), _safe_int(ext.get("mention_count_1h"), 0))
        current["mention_count_24h"] = max(_safe_int(current.get("mention_count_24h"), 0), _safe_int(ext.get("mention_count_24h"), 0))
        current["activity_last_30d"] = max(_safe_int(current.get("activity_last_30d"), 0), _safe_int(ext.get("activity_last_30d"), 0))
        current["impressions"] = max(_safe_int(current.get("impressions"), 0), _safe_int(ext.get("impressions"), 0))
        current["node_count"] = max(_safe_int(current.get("node_count"), 0), _safe_int(ext.get("node_count"), 0))
        current["spike_detected"] = bool(current.get("spike_detected")) or bool(ext.get("spike_detected"))
        current["top_keywords"] = sorted(
            {
                *(current.get("top_keywords") or []),
                *(ext.get("top_keywords") or []),
            }
        )[:12]
    return list(by_key.values())


def _parse_nemotron_json(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\})", text, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                return {}
    return {}


def _nemotron_filter_top_rows(rows: list[dict[str, Any]], top_n: int = 120) -> list[dict[str, Any]]:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key or not rows:
        return rows

    candidates = sorted(rows, key=lambda row: _safe_float(row.get("trend_score"), 0.0), reverse=True)[: max(50, top_n)]
    compact = []
    for row in candidates:
        nodes = row.get("top_nodes") or []
        urls = [str(node.get("url") or "") for node in nodes[:4] if str(node.get("url") or "").strip()]
        domains = sorted({_domain_root(url) for url in urls if _domain_root(url)})
        compact.append(
            {
                "entity_key": row.get("entity_key"),
                "entity": row.get("entity"),
                "sources": row.get("sources") or [],
                "source_counts": row.get("source_counts") or {},
                "keywords": (row.get("top_keywords") or [])[:8],
                "domains": domains,
                "urls": urls,
                "score": row.get("trend_score"),
            }
        )

    prompt = {
        "task": "Filter startup leaderboard candidates. Exclude incumbents/platform vendors/generic junk labels.",
        "rules": [
            "include=true only for startup/tool entities useful for discovery.",
            "exclude items that are known incumbents/platform hosts/generic categories.",
            "Use domains and urls as grounding evidence.",
            "Return strict JSON only.",
        ],
        "candidates": compact,
        "schema": {"results": [{"entity_key": "string", "include": "boolean", "reason": "string"}]},
    }
    payload = {
        "model": os.getenv("OPENROUTER_MODEL", "").strip() or os.getenv("NEMOTRON_MODEL", "").strip() or DEFAULT_NEMOTRON_MODEL,
        "temperature": 0,
        "max_tokens": 2600,
        "messages": [
            {"role": "system", "content": "You are a startup ranking quality filter. Return strict JSON only."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
    }
    req = urllib.request.Request(
        f"{(os.getenv('OPENROUTER_BASE_URL', '').strip() or 'https://openrouter.ai/api/v1').rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "HTTP-Referer": "https://scout.local",
            "X-Title": "Scout Top50 Filter",
            "User-Agent": "scout-index/0.1",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            raw = json.loads(response.read().decode("utf-8"))
        content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        parsed = _parse_nemotron_json(content)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return rows

    results = parsed.get("results") if isinstance(parsed, dict) else []
    if not isinstance(results, list):
        return rows
    include_map: dict[str, bool] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        key = str(item.get("entity_key") or "").strip()
        if key:
            include_map[key] = bool(item.get("include"))

    if not include_map:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("entity_key") or "")
        # Only enforce Nemotron decision on top-candidate bucket.
        if key in include_map and not include_map[key]:
            continue
        out.append(row)
    return out


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

    rows = [
        row
        for row in rows
        if not _is_filtered_entity(
            str(row.get("entity") or ""),
            str(row.get("entity_key") or ""),
            row.get("top_nodes") if isinstance(row.get("top_nodes"), list) else None,
        )
    ]
    rows = _merge_external_rows(rows, _load_external_github_rows())
    _normalize_scores(rows)
    rows = _nemotron_filter_top_rows(rows, top_n=150)
    rows.sort(key=lambda row: _safe_float(row.get("trend_score"), 0.0), reverse=True)

    payload = {
        "_meta": {
            "description": (
                "Unified cross-source entity index for niche search. "
                "Aggregates canonicalized entities from SQLite pipeline store."
            ),
            "schema_version": "2.0",
            "generated_at": now_iso(),
            "sources_expected": ["github", "hackernews", "producthunt"],
            "external_indexes": ["github_index.json"] if EXTERNAL_GITHUB_INDEX.exists() else [],
            "nemotron_filter": bool(os.getenv("OPENROUTER_API_KEY", "").strip()),
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
