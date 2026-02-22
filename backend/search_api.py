#!/usr/bin/env python3
"""
Scout API server with daily pipeline scheduler.

Endpoints:
  GET  /api/health
  GET  /api/trends
  GET  /api/sources
  GET  /api/entity/{entity_key}/nodes?include_enriched=true&limit=40
  GET  /api/entity/{entity_key}/history?window_days=180
  GET  /api/pipeline/status
  POST /api/pipeline/run
  POST /api/niche-search
  GET  /api/niche-search?query=...
"""

from __future__ import annotations

import argparse
import json
import os
import re
import hashlib
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from config import load_env_file
    from db import migrate, get_top50, get_startup_by_id, _get_conn, DB_PATH
    from index_store import (
        export_index_json,
        get_entity_history,
        get_pipeline_status,
        get_sources_payload,
    )
    from enrich_links import serper_search
    from pipeline_runner import PipelineScheduler, run_full_pipeline, run_on_demand_enrichment
    from niche_search import (
        build_profile_query_text,
        call_openrouter_nemotron,
        combine_scores,
        load_index,
        normalize_priority_map,
        normalize_query_profile,
        parse_refresh_targets,
        save_latest_results,
        tool_build_index,
        tool_refresh_source,
        tool_search_index,
    )
except ModuleNotFoundError:
    from backend.config import load_env_file  # type: ignore
    from backend.db import migrate, get_top50, get_startup_by_id, _get_conn, DB_PATH  # type: ignore
    from backend.index_store import (  # type: ignore
        export_index_json,
        get_entity_history,
        get_pipeline_status,
        get_sources_payload,
    )
    from backend.enrich_links import serper_search # type: ignore
    from backend.pipeline_runner import PipelineScheduler, run_full_pipeline, run_on_demand_enrichment  # type: ignore
    from backend.niche_search import (  # type: ignore
        build_profile_query_text,
        call_openrouter_nemotron,
        combine_scores,
        load_index,
        normalize_priority_map,
        normalize_query_profile,
        parse_refresh_targets,
        save_latest_results,
        tool_build_index,
        tool_refresh_source,
        tool_search_index,
    )


DEFAULT_INDEX = Path("data/index_data/entity_index.json")
DEFAULT_SAVE = Path("data/index_data/latest_query_results.json")
SCHEDULER = PipelineScheduler()
PIPELINE_THREAD: threading.Thread | None = None
PIPELINE_LAST_RUN: dict | None = None
PIPELINE_LAST_RUN_LOCK = threading.Lock()
SCORE_EXPL_CACHE = {"signature": "", "expires_at": 0.0, "map": {}}
DEFAULT_NEMOTRON_MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"

def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _index_signature(path: Path = DEFAULT_INDEX) -> dict:
    if not path.exists():
        return {"exists": False, "sha256": "", "generated_at": "", "entity_count": 0}
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    generated_at = ""
    entity_count = 0
    try:
        payload = json.loads(raw.decode("utf-8"))
        generated_at = str((payload.get("_meta") or {}).get("generated_at") or "")
        entity_count = int(payload.get("entity_count") or 0)
    except Exception:
        pass
    return {"exists": True, "sha256": sha, "generated_at": generated_at, "entity_count": entity_count}

TOP_MAP_EXCLUDED_KEYS = {
    "vercel",
    "netlify",
    "visualstudio",
    "visualstudiocode",
    "vscode",
    "crates",
    "npmjs",
}
TOP_MAP_BLOCKED_DOMAINS = {
    "vercel.app",
    "vercel.com",
    "netlify.app",
    "netlify.com",
    "visualstudio.com",
    "visualstudio.microsoft.com",
}


def _normalize_key(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _domain_root(url: str) -> str:
    from urllib.parse import urlparse

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


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    idx = (len(ordered) - 1) * max(0.0, min(1.0, q))
    lo = int(idx)
    hi = min(len(ordered) - 1, lo + 1)
    frac = idx - lo
    return float(ordered[lo]) * (1.0 - frac) + float(ordered[hi]) * frac


def _fallback_score_explanation(row: dict) -> str:
    breakdown = row.get("score_breakdown") if isinstance(row.get("score_breakdown"), dict) else {}
    momentum = float(breakdown.get("momentum_score") or row.get("momentum_score") or 0.0)
    interaction = float(breakdown.get("interaction_score") or row.get("interaction_score") or 0.0)
    recency = float(breakdown.get("recency_score") or 0.0)
    m1h = int(breakdown.get("mention_count_1h") or row.get("mention_count_1h") or 0)
    m24h = int(breakdown.get("mention_count_24h") or row.get("mention_count_24h") or 0)
    a30 = int(breakdown.get("activity_last_30d") or row.get("activity_last_30d") or 0)
    display_score = float(row.get("trend_score") or 0.0)
    raw_score = float(row.get("raw_trend_score") or display_score)
    score_prefix = (
        f"Score={raw_score:.2f}"
        if abs(display_score - raw_score) < 1e-6
        else f"Score={display_score:.2f} (raw={raw_score:.2f})"
    )
    return (
        f"{score_prefix} from momentum {momentum:.1f}, "
        f"cross-source interaction {interaction:.1f}, recency {recency:.1f}; "
        f"mentions: 1h={m1h}, 24h={m24h}, 30d={a30}."
    )


def _nemotron_score_explanations(rows: list[dict]) -> dict[str, str]:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key or not rows:
        return {}

    condensed = []
    for row in rows:
        key = str(row.get("entity_key") or "")
        if not key:
            continue
        breakdown = row.get("score_breakdown") if isinstance(row.get("score_breakdown"), dict) else {}
        condensed.append(
            {
                "entity_key": key,
                "entity": row.get("entity"),
                "trend_score": row.get("raw_trend_score", row.get("trend_score")),
                "trend_score_display": row.get("trend_score"),
                "momentum_score": breakdown.get("momentum_score", row.get("momentum_score")),
                "interaction_score": breakdown.get("interaction_score", row.get("interaction_score")),
                "recency_score": breakdown.get("recency_score", row.get("recency_score")),
                "mention_count_1h": breakdown.get("mention_count_1h", row.get("mention_count_1h")),
                "mention_count_24h": breakdown.get("mention_count_24h", row.get("mention_count_24h")),
                "activity_last_30d": breakdown.get("activity_last_30d", row.get("activity_last_30d")),
                "source_interactions": breakdown.get("source_interactions", row.get("source_interactions") or {}),
                "sources": row.get("sources") or [],
            }
        )

    if not condensed:
        return {}

    payload = {
        "model": os.getenv("OPENROUTER_MODEL", "").strip() or os.getenv("NEMOTRON_MODEL", "").strip() or DEFAULT_NEMOTRON_MODEL,
        "temperature": 0,
        "max_tokens": 2800,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You explain ranking scores for startup entities. "
                    "Return strict JSON only. Each explanation must reference concrete numeric values from input."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "instruction": "For each entity, write 1 concise explanation sentence with specific numbers.",
                        "entities": condensed,
                        "schema": {
                            "results": [
                                {"entity_key": "string", "score_explanation": "string"}
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
            },
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
            "X-Title": "Scout Score Explanations",
            "User-Agent": "scout-search-api/0.1",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            raw = json.loads(response.read().decode("utf-8"))
        content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"(\{.*\})", content, flags=re.DOTALL)
            parsed = json.loads(match.group(1)) if match else {}
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return {}

    out: dict[str, str] = {}
    results = parsed.get("results") if isinstance(parsed, dict) else []
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            key = str(item.get("entity_key") or "").strip()
            explanation = str(item.get("score_explanation") or "").strip()
            if key and explanation:
                out[key] = explanation
    return out


def _ensure_index_exists() -> dict:
    if not DEFAULT_INDEX.exists():
        payload = export_index_json(DEFAULT_INDEX)
        return payload
    return load_index(DEFAULT_INDEX)


def build_trends_payload(index_payload: dict) -> dict:
    entities = index_payload.get("entities") or []
    if not isinstance(entities, list):
        entities = []
    trends = []
    raw_scores: list[float] = []
    source_totals: dict[str, int] = {}

    def should_exclude(row: dict) -> bool:
        key = _normalize_key(str(row.get("entity_key") or row.get("entity") or ""))
        if not key:
            return True
        if bool(row.get("is_known_incumbent")):
            return True
        if key in TOP_MAP_EXCLUDED_KEYS:
            return True
        nodes = row.get("top_nodes") or []
        if isinstance(nodes, list) and nodes:
            blocked_hits = 0
            url_hits = 0
            for node in nodes[:8]:
                if not isinstance(node, dict):
                    continue
                url = str(node.get("url") or "")
                if not url:
                    continue
                url_hits += 1
                if _domain_root(url) in TOP_MAP_BLOCKED_DOMAINS:
                    blocked_hits += 1
            if url_hits > 0 and blocked_hits / max(1, url_hits) >= 0.6:
                return True
        return False

    for row in entities:
        if not isinstance(row, dict):
            continue
        if should_exclude(row):
            continue
        source_counts = row.get("source_counts") or {}
        if isinstance(source_counts, dict):
            for src, count in source_counts.items():
                try:
                    source_totals[str(src)] = int(source_totals.get(str(src), 0)) + int(count)
                except (TypeError, ValueError):
                    continue
        raw_score = float(row.get("trend_score") or 0.0)
        raw_scores.append(raw_score)
        trends.append(
            {
                "entity_key": row.get("entity_key"),
                "entity": row.get("entity"),
                "trend_score": raw_score,
                "raw_trend_score": raw_score,
                "momentum_score": float(row.get("momentum_score") or 0.0),
                "interaction_score": float(row.get("interaction_score") or 0.0),
                "recency_score": float(row.get("recency_score") or 0.0),
                "score_breakdown": row.get("score_breakdown") if isinstance(row.get("score_breakdown"), dict) else {},
                "is_known_incumbent": bool(row.get("is_known_incumbent")),
                "velocity_delta_pct": 0.0,
                "sentiment": {"positive": 0.55, "neutral": 0.3, "negative": 0.15},
                "mention_count_1h": int(row.get("mention_count_1h") or 0),
                "mention_count_24h": int(row.get("mention_count_24h") or 0),
                "spike_detected": bool(row.get("spike_detected")),
                "sources": row.get("sources") or [],
                "source_counts": source_counts if isinstance(source_counts, dict) else {},
                "source_interactions": row.get("source_interactions") or {},
                "top_keywords": row.get("top_keywords") or [],
                "first_seen_at": row.get("first_seen_at"),
                "last_seen_at": row.get("last_seen_at"),
                "activity_last_30d": int(row.get("activity_last_30d") or 0),
            }
        )

    if trends:
        # Keep trend_score on the canonical raw scale to avoid top-tail display collapse.
        for row in trends:
            row["trend_score"] = round(float(row.get("raw_trend_score") or 0.0), 2)

    trends.sort(
        key=lambda row: (
            float(row.get("raw_trend_score") or 0.0),
            float((row.get("score_breakdown") or {}).get("interaction_score") or row.get("interaction_score") or 0.0),
            int(row.get("activity_last_30d") or 0),
        ),
        reverse=True,
    )

    # Nemotron/OpenRouter explanation pass with short in-memory cache.
    signature = "|".join(
        f"{row.get('entity_key')}:{float(row.get('raw_trend_score') or row.get('trend_score') or 0.0):.2f}:{float((row.get('score_breakdown') or {}).get('interaction_score') or row.get('interaction_score') or 0.0):.2f}"
        for row in trends[:80]
    )
    explanation_map: dict[str, str] = {}
    now_ts = time.time()
    if SCORE_EXPL_CACHE.get("signature") == signature and float(SCORE_EXPL_CACHE.get("expires_at") or 0.0) > now_ts:
        explanation_map = SCORE_EXPL_CACHE.get("map") if isinstance(SCORE_EXPL_CACHE.get("map"), dict) else {}
    else:
        explanation_map = _nemotron_score_explanations(trends[:80])
        SCORE_EXPL_CACHE["signature"] = signature
        SCORE_EXPL_CACHE["expires_at"] = now_ts + 600.0
        SCORE_EXPL_CACHE["map"] = explanation_map

    for row in trends:
        key = str(row.get("entity_key") or "")
        row["score_explanation"] = explanation_map.get(key) or _fallback_score_explanation(row)

    return {"entities": trends, "count": len(trends), "source_totals": source_totals}


def load_entities_from_scout_db() -> list[dict[str, Any]]:
    """Load all startups from scout.db and convert to the entity format
    that lexical_score_entity() and call_openrouter_nemotron() expect."""
    import sqlite3 as _sql
    _db = _sql.connect("data/scout.db", timeout=10)
    _db.row_factory = _sql.Row
    rows = [dict(r) for r in _db.execute(
        "SELECT * FROM Startups ORDER BY scout_score DESC"
    ).fetchall()]
    _db.close()

    entities = []
    _junk_prefixes = ['unknown', 'unspecified', 'untitled', 'n/a', 'none']
    for row in rows:
        # Skip junk-named startups
        name = (row.get('startup_name') or '').strip()
        name_lower = name.lower()
        if len(name) < 2 or name_lower in _junk_prefixes:
            continue
        if any(name_lower.startswith(j) for j in _junk_prefixes):
            continue
        if '(unspecified' in name_lower or '(referenced' in name_lower:
            continue
        # Build keyword list from structured fields
        keywords = []
        for field in ['vertical', 'business_model', 'stage']:
            val = row.get(field, '')
            if val and val.lower() not in ('unknown', 'unspecified', 'unclear', ''):
                keywords.append(val)
        # Add source as a keyword
        if row.get('source'):
            keywords.extend(str(row['source']).split(','))

        # Build top_nodes (for lexical matching context)
        top_nodes = []
        one_liner = row.get('one_liner', '') or ''
        raw_text = row.get('raw_text', '') or ''
        source_url = row.get('source_url', '') or ''
        if one_liner or raw_text or source_url:
            top_nodes.append({
                "headline": one_liner,
                "summary": raw_text[:400] if raw_text else one_liner,
                "url": source_url,
            })

        entities.append({
            "entity_key": row['id'],
            "entity": row['startup_name'],
            "trend_score": row.get('scout_score', 0),
            "momentum_score": row.get('scout_score', 0),
            "confidence": 0.8,
            "sources": str(row.get('source', '')).split(',') if row.get('source') else [],
            "top_keywords": keywords,
            "top_nodes": top_nodes,
            "first_seen_at": row.get('first_seen', ''),
            "last_seen_at": row.get('last_updated', ''),
            "node_count": 1 if source_url else 0,
            # Pass through extra fields for the frontend
            "one_liner": one_liner,
            "vertical": row.get('vertical', 'Unknown'),
            "stage": row.get('stage', 'Unknown'),
            "business_model": row.get('business_model', 'Unknown'),
            "source_url": source_url,
        })

    print(f"[NICHE] Loaded {len(entities)} startups from scout.db")
    return entities


def run_niche_pipeline(
    query: str,
    limit: int = 12,
    min_score: float = 2.0,  # Lowered for small DB
    refresh: str = "",
    rebuild_index: bool = False,
    use_nemotron: bool = True,
    index_path: Path = DEFAULT_INDEX,
    save_out: Path = DEFAULT_SAVE,
    enrich_on_demand: bool = False,
    enrich_limit: int = 5,
    query_profile: dict[str, Any] | None = None,
    dimension_priority_rank: dict[str, Any] | None = None,
) -> dict:
    if not query.strip():
        raise ValueError("query is required")

    load_env_file()

    # Load entities directly from scout.db (replaces entity_index.py)
    entities = load_entities_from_scout_db()
    if not entities:
        raise RuntimeError("No startups in scout.db yet. Wait for the scraper to populate it.")

    profile_payload = normalize_query_profile(query_profile)
    priority_payload = normalize_priority_map(dimension_priority_rank)
    effective_query = build_profile_query_text(query, profile_payload) or query
    print(f"[NICHE] Query: '{effective_query}', profile: {profile_payload}")

    # Lexical scoring against all entities
    candidates = tool_search_index(effective_query, entities, limit=max(limit * 5, 40))
    print(f"[NICHE] Lexical matches: {len(candidates)}")

    # If no lexical matches, fall back to returning top scored startups
    if not candidates:
        print("[NICHE] No lexical matches — falling back to top startups by score")
        candidates = [{"entity": e, "lexical_score": e.get("trend_score", 0) * 0.1} for e in entities[:limit]]

    # Nemotron LLM reranking
    llm_payload = None
    if use_nemotron and candidates:
        try:
            llm_payload = call_openrouter_nemotron(
                query=query,
                candidates=candidates,
                query_profile=profile_payload,
                priority_map=priority_payload,
            )
            print(f"[NICHE] Nemotron reranked: {bool(llm_payload)}")
        except Exception as exc:
            print(f"[NICHE] Nemotron failed (continuing without): {exc}")

    rows = combine_scores(
        candidates,
        llm_payload,
        query_profile=profile_payload,
        priority_map=priority_payload,
    )
    filtered = [row for row in rows if row.get("include", True)] if llm_payload else rows
    final_rows = filtered if filtered else rows
    final_rows = [row for row in final_rows if float(row.get("final_score") or 0.0) >= min_score]
    final_rows = final_rows[:limit]
    print(f"[NICHE] Final results: {len(final_rows)}")

    try:
        save_latest_results(
            save_out, query, final_rows[: max(limit, 25)],
            query_profile=profile_payload, priority_map=priority_payload,
        )
    except Exception:
        pass  # Non-critical

    return {
        "query": query,
        "effective_query": effective_query,
        "query_profile": profile_payload,
        "dimension_priority_rank": priority_payload,
        "used_nemotron": bool(llm_payload),
        "result_count": len(final_rows),
        "results": final_rows,
        "index_stats": {"source": "scout.db"},
        "index_entity_count": len(entities),
        "enrichment_applied": False,
        "enriched_links_added": 0,
        "enrichment": None,
    }


def _run_pipeline_background(mode: str, do_backfill: bool, do_enrichment: bool) -> None:
    global PIPELINE_LAST_RUN  # noqa: PLW0603
    result = run_full_pipeline(mode=mode, do_backfill=do_backfill, do_enrichment=do_enrichment)
    with PIPELINE_LAST_RUN_LOCK:
        PIPELINE_LAST_RUN = result


def trigger_pipeline_run(mode: str, do_backfill: bool, do_enrichment: bool, async_run: bool = True) -> dict:
    global PIPELINE_THREAD  # noqa: PLW0603
    if not async_run:
        return run_full_pipeline(mode=mode, do_backfill=do_backfill, do_enrichment=do_enrichment)
    if PIPELINE_THREAD and PIPELINE_THREAD.is_alive():
        return {"ok": False, "error": "pipeline already running", "async": True}
    PIPELINE_THREAD = threading.Thread(
        target=_run_pipeline_background,
        args=(mode, do_backfill, do_enrichment),
        name="scout-pipeline-manual",
        daemon=True,
    )
    PIPELINE_THREAD.start()
    return {"ok": True, "async": True, "status": "started", "mode": mode}


class SearchHandler(BaseHTTPRequestHandler):
    server_version = "ScoutSearchAPI/0.2"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        try:
            super().log_message(format, *args)
        except Exception:
            return

    def _set_json(self, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-User-ID")
        self.end_headers()

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("Invalid JSON body")

    def do_OPTIONS(self) -> None:
        self._set_json(204)

    def _handle_get_entity_nodes(self, path: str, parsed_query: dict[str, list[str]]) -> bool:
        parts = [p for p in path.split("/") if p]
        # /api/entity/{entity_key}/nodes
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "entity" and parts[3] == "nodes":
            load_env_file()  # Ensure Serper API key is loaded
            from urllib.parse import unquote
            entity_key = unquote(parts[2])
            
            # Find the startup by id first, then fall back to name search
            startup = get_startup_by_id(entity_key)
            if not startup:
                # Try searching by name (the frontend passes entity name via fetchEntityNodes)
                import sqlite3 as _sql
                _db = _sql.connect("data/scout.db", timeout=10)
                _db.row_factory = _sql.Row
                row = _db.execute(
                    "SELECT * FROM Startups WHERE startup_name = ? COLLATE NOCASE LIMIT 1",
                    (entity_key,),
                ).fetchone()
                _db.close()
                startup = dict(row) if row else None

            if not startup:
                self._set_json(200)
                self.wfile.write(json.dumps({"entity_key": entity_key, "count": 0, "nodes": []}).encode("utf-8"))
                return True
            
            nodes = []
            
            # Node 0: The ORIGINAL scraped source link
            source_url = startup.get('source_url', '')
            if source_url:
                nodes.append({
                    "id": f"scraped-{entity_key}-0",
                    "source_id": startup.get('source', 'scraped'),
                    "source_name": startup.get('source', 'Scraped').upper(),
                    "headline": f"Original source: {startup.get('startup_name', '')}",
                    "url": source_url,
                    "summary": startup.get('one_liner', '') or startup.get('raw_text', '')[:200] or "Original scraped source",
                    "interactions": 200,
                    "views": 500,
                    "node_type": "source_raw"
                })
                
            # Nodes 1+: Live Serper Search results (inline to avoid silent failures)
            search_query = f"{startup.get('startup_name')} startup"
            serper_results = []
            serper_key = os.environ.get("GOOGLE_SERPER_KEY", "")
            print(f"[NODES] Querying Serper: '{search_query}', key_len={len(serper_key)}")
            if serper_key:
                try:
                    import urllib.request as _ureq
                    _payload = json.dumps({"q": search_query, "num": 10}).encode("utf-8")
                    _req = _ureq.Request(
                        "https://google.serper.dev/search",
                        data=_payload,
                        headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
                        method="POST",
                    )
                    with _ureq.urlopen(_req, timeout=12) as _resp:
                        _raw = json.loads(_resp.read())
                        for item in (_raw.get("organic") or []):
                            link = str(item.get("link") or "").strip()
                            if link:
                                serper_results.append({
                                    "url": link,
                                    "title": str(item.get("title") or ""),
                                    "snippet": str(item.get("snippet") or ""),
                                })
                    print(f"[NODES] Serper OK: {len(serper_results)} organic results")
                except Exception as exc:
                    print(f"[NODES] Serper FAILED: {type(exc).__name__}: {exc}")
            else:
                print("[NODES] No GOOGLE_SERPER_KEY in env!")
            
            for i, res in enumerate(serper_results):
                nodes.append({
                    "id": f"web-{entity_key}-{i}",
                    "source_id": "web_serper",
                    "source_name": "WEB",
                    "headline": res.get("title", ""),
                    "url": res.get("url", ""),
                    "summary": res.get("snippet", ""),
                    "interactions": 100 - (i * 5),
                    "views": 200 - (i * 10),
                    "node_type": "source_enriched"
                })

            self._set_json(200)
            self.wfile.write(json.dumps({"entity_key": entity_key, "count": len(nodes), "nodes": nodes}, ensure_ascii=False).encode("utf-8"))
            return True
        return False

    def _handle_get_entity_history(self, path: str, parsed_query: dict[str, list[str]]) -> bool:
        parts = [p for p in path.split("/") if p]
        # /api/entity/{entity_key}/history
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "entity" and parts[3] == "history":
            entity_key = parts[2]
            window_days = int((parsed_query.get("window_days") or ["180"])[0])
            history = get_entity_history(entity_key=entity_key, window_days=max(1, min(window_days, 730)))
            self._set_json(200)
            self.wfile.write(json.dumps({"entity_key": entity_key, "count": len(history), "history": history}, ensure_ascii=False).encode("utf-8"))
            return True
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/api/health":
            self._set_json(200)
            sig = _index_signature(DEFAULT_INDEX)
            self.wfile.write(
                json.dumps(
                    {
                        "ok": True,
                        "service": "scout-search-api",
                        "scheduler_running": SCHEDULER.is_running,
                        "index_sha256": sig.get("sha256"),
                        "index_generated_at": sig.get("generated_at"),
                        "index_entity_count": sig.get("entity_count"),
                    }
                ).encode("utf-8")
            )
            return

        if parsed.path in {"/api/debug/index-signature", "/api/debug/index-signature/"}:
            self._set_json(200)
            self.wfile.write(json.dumps(_index_signature(DEFAULT_INDEX), ensure_ascii=False).encode("utf-8"))
            return

        if parsed.path in {"/api/trends", "/api/trends/"}:
            try:
                load_env_file()
                
                # Query scout.db – filter out junk names, prioritize classified startups
                import sqlite3 as _sql
                _db = _sql.connect("data/scout.db", timeout=10)
                _db.row_factory = _sql.Row
                
                # Filter out garbage names + prioritize properly classified startups
                _junk_names = ['unknown', 'unspecified', 'untitled', 'n/a', 'none']
                all_rows = [dict(r) for r in _db.execute(
                    "SELECT * FROM Startups ORDER BY scout_score DESC"
                ).fetchall()]
                _db.close()
                
                # Filter out junk names but keep everything else for Top 50
                top50 = []
                for row in all_rows:
                    name = (row.get('startup_name') or '').strip().lower()
                    if len(name) < 2:
                        continue
                    if name in _junk_names or any(name.startswith(j) for j in _junk_names):
                        continue
                    if '(unspecified' in name or '(referenced' in name:
                        continue
                    top50.append(row)
                    if len(top50) >= 50:
                        break
                
                formatted_trends = []
                for s in top50:
                    sources = str(s.get('source', '')).split(',') if s.get('source') else []
                    
                    # Expanded vertical-to-category mapping
                    v = (s.get('vertical') or 'Unknown').lower()
                    cat = 'other'
                    if any(k in v for k in ['ai', 'machine learning', 'ml', 'llm', 'nlp', 'deep learning', 'generative']):
                        cat = 'ai'
                    elif any(k in v for k in ['fintech', 'finance', 'payment', 'banking', 'insurance', 'crypto', 'defi']):
                        cat = 'fintech'
                    elif any(k in v for k in ['dev', 'developer', 'saas', 'infra', 'cloud', 'api', 'platform', 'tool', 'software']):
                        cat = 'devtools'
                    elif any(k in v for k in ['health', 'biotech', 'pharma', 'medical', 'defense', 'security', 'cyber', 'aerospace']):
                        cat = 'defense'
                    elif any(k in v for k in ['consumer', 'media', 'social', 'e-commerce', 'ecommerce', 'retail', 'food', 'education', 'edtech', 'entertainment']):
                        cat = 'media'
                    
                    # Keep unknown/unspecified as 'other' -> gray
                    if 'unknown' in v or 'unspecified' in v or v.strip() == '':
                        cat = 'other'
                    formatted_trends.append({
                        "id": s['id'],
                        "entity_key": s['id'],
                        "entity": s['startup_name'],
                        "trend_score": s['scout_score'],
                        "raw_trend_score": s['scout_score'],
                        "momentum_score": s['scout_score'],
                        "cat": cat,
                        "vertical": s.get('vertical', 'Unknown'),
                        "one_liner": s.get('one_liner', ''),
                        "stage": s.get('stage', 'Unknown'),
                        "business_model": s.get('business_model', 'Unknown'),
                        "source_url": s.get('source_url', ''),
                        "sources": sources,
                        "mention_count_1h": len(sources),
                        "first_seen_at": s.get('scrape_date', None),
                        "last_seen_at": s.get('scrape_date', None),
                    })
                    
                payload = {"entities": formatted_trends}
                
                self._set_json(200)
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                self._set_json(400)
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        if parsed.path in {"/api/sources", "/api/sources/"}:
            try:
                load_env_file()
                payload = get_sources_payload()
                self._set_json(200)
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                self._set_json(400)
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        if parsed.path in {"/api/pipeline/status", "/api/pipeline/status/"}:
            try:
                payload = get_pipeline_status()
                payload["scheduler_running"] = SCHEDULER.is_running
                payload["manual_pipeline_running"] = bool(PIPELINE_THREAD and PIPELINE_THREAD.is_alive())
                with PIPELINE_LAST_RUN_LOCK:
                    payload["manual_last_result"] = PIPELINE_LAST_RUN
                self._set_json(200)
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                self._set_json(400)
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        if self._handle_get_entity_nodes(parsed.path, params):
            return

        if self._handle_get_entity_history(parsed.path, params):
            return

        if parsed.path == "/api/niche-search":
            query = (params.get("query") or params.get("q") or [""])[0]
            limit = int((params.get("limit") or ["12"])[0])
            min_score = float((params.get("min_score") or ["10"])[0])
            use_nemotron = ((params.get("use_nemotron") or ["true"])[0]).lower() != "false"
            enrich_on_demand = ((params.get("enrich_on_demand") or ["false"])[0]).lower() == "true"
            enrich_limit = int((params.get("enrich_limit") or ["5"])[0])
            query_profile_raw = (params.get("query_profile") or ["{}"])[0]
            priority_raw = (params.get("dimension_priority_rank") or ["{}"])[0]
            try:
                query_profile = json.loads(query_profile_raw) if query_profile_raw else {}
            except json.JSONDecodeError:
                query_profile = {}
            try:
                dimension_priority_rank = json.loads(priority_raw) if priority_raw else {}
            except json.JSONDecodeError:
                dimension_priority_rank = {}
            try:
                payload = run_niche_pipeline(
                    query=query,
                    limit=max(1, min(limit, 50)),
                    min_score=min_score,
                    use_nemotron=use_nemotron,
                    enrich_on_demand=enrich_on_demand,
                    enrich_limit=max(1, min(enrich_limit, 10)),
                    query_profile=query_profile,
                    dimension_priority_rank=dimension_priority_rank,
                )
                self._set_json(200)
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                self._set_json(400)
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        if parsed.path in {"/api/user/profile", "/api/user/profile/"}:
            try:
                load_env_file()
                user_id = params.get("user_id", [None])[0]
                if not user_id:
                    raise ValueError("user_id required")
                with _get_conn(DB_PATH) as conn:
                    row = conn.execute("SELECT * FROM Profiles WHERE id = ?", (user_id,)).fetchone()
                    profile = dict(row) if row else {}
                self._set_json(200)
                self.wfile.write(json.dumps(profile, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:
                self._set_json(400)
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        if parsed.path in {"/api/user/bookmarks", "/api/user/bookmarks/"}:
            try:
                load_env_file()
                user_id = params.get("user_id", [None])[0]
                if not user_id:
                    raise ValueError("user_id required")
                with _get_conn(DB_PATH) as conn:
                    rows = conn.execute("SELECT * FROM Bookmarks WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()
                    bookmarks = [dict(r) for r in rows]
                self._set_json(200)
                self.wfile.write(json.dumps({"bookmarks": bookmarks}, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:
                self._set_json(400)
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        self._set_json(404)
        self.wfile.write(json.dumps({"error": "Not found"}).encode("utf-8"))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/api/pipeline/run", "/api/pipeline/run/"}:
            try:
                body = self._read_json_body()
                mode = str(body.get("mode") or "manual")
                do_backfill = bool(body.get("do_backfill", True))
                do_enrichment = bool(body.get("do_enrichment", True))
                async_run = bool(body.get("async", True))
                payload = trigger_pipeline_run(
                    mode=mode,
                    do_backfill=do_backfill,
                    do_enrichment=do_enrichment,
                    async_run=async_run,
                )
                self._set_json(200 if payload.get("ok") else 409)
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                self._set_json(400)
                self.wfile.write(json.dumps({"error": str(exc), "trace": traceback.format_exc(limit=1)}).encode("utf-8"))
            return

        if parsed.path in {"/api/user/profile", "/api/user/profile/"}:
            try:
                load_env_file()
                user_id = self.headers.get("X-User-ID")
                if not user_id:
                    raise ValueError("Missing X-User-ID header")
                body = self._read_json_body()
                
                with _get_conn(DB_PATH) as conn:
                    conn.execute("""
                        INSERT INTO Profiles (id, niche, bio, firm, location, avatar_url, updated_at) 
                        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                        ON CONFLICT(id) DO UPDATE SET 
                            niche = excluded.niche,
                            bio = excluded.bio,
                            firm = excluded.firm,
                            location = excluded.location,
                            avatar_url = excluded.avatar_url,
                            updated_at = excluded.updated_at
                    """, (user_id, body.get("niche"), body.get("bio"), body.get("firm"), body.get("location"), body.get("avatar_url")))
                    conn.commit()
                
                self._set_json(200)
                self.wfile.write(json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:
                self._set_json(400)
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        if parsed.path in {"/api/user/bookmarks", "/api/user/bookmarks/"}:
            try:
                load_env_file()
                user_id = self.headers.get("X-User-ID")
                if not user_id:
                    raise ValueError("Missing X-User-ID header")
                body = self._read_json_body()
                entity_key = body.get("entity_key")
                if not entity_key:
                    raise ValueError("entity_key missing")
                
                with _get_conn(DB_PATH) as conn:
                    existing = conn.execute("SELECT id FROM Bookmarks WHERE user_id = ? AND entity_key = ?", (user_id, entity_key)).fetchone()
                    if existing:
                        conn.execute("DELETE FROM Bookmarks WHERE user_id = ? AND entity_key = ?", (user_id, entity_key))
                        action = "removed"
                    else:
                        conn.execute("INSERT INTO Bookmarks (user_id, entity_key, created_at) VALUES (?, ?, datetime('now'))", (user_id, entity_key))
                        action = "added"
                    conn.commit()
                    
                self._set_json(200)
                self.wfile.write(json.dumps({"ok": True, "action": action}, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:
                self._set_json(400)
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        if parsed.path != "/api/niche-search":
            self._set_json(404)
            self.wfile.write(json.dumps({"error": "Not found"}).encode("utf-8"))
            return

        try:
            body = self._read_json_body()
            query = str(body.get("query") or body.get("prompt") or "").strip()
            if not query:
                raise ValueError("query is required")
            payload = run_niche_pipeline(
                query=query,
                limit=max(1, min(int(body.get("limit", 12)), 50)),
                min_score=float(body.get("min_score", 10)),
                refresh=str(body.get("refresh", "")),
                rebuild_index=bool(body.get("rebuild_index", False)),
                use_nemotron=bool(body.get("use_nemotron", True)),
                index_path=Path(body.get("index_path") or DEFAULT_INDEX),
                save_out=Path(body.get("save_out") or DEFAULT_SAVE),
                enrich_on_demand=bool(body.get("enrich_on_demand", False)),
                enrich_limit=max(1, min(int(body.get("enrich_limit", 5)), 10)),
                query_profile=body.get("query_profile") if isinstance(body.get("query_profile"), dict) else {},
                dimension_priority_rank=body.get("dimension_priority_rank")
                if isinstance(body.get("dimension_priority_rank"), dict)
                else {},
            )
            self._set_json(200)
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._set_json(400)
            self.wfile.write(
                json.dumps(
                    {
                        "error": str(exc),
                        "trace": traceback.format_exc(limit=1),
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Scout API server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file()
    migrate()
    auto_rebuild = _env_truthy("SCOUT_AUTO_REBUILD_INDEX", default=False)
    if auto_rebuild:
        try:
            export_index_json(DEFAULT_INDEX)
            print("index rebuild on startup: enabled")
        except Exception as exc:  # noqa: BLE001
            print(f"index rebuild on startup failed: {exc}")
    else:
        print("index rebuild on startup: disabled (set SCOUT_AUTO_REBUILD_INDEX=1 to enable)")
    enable_scheduler = _env_truthy("SCOUT_ENABLE_SCHEDULER", default=False)
    if enable_scheduler:
        SCHEDULER.start()
        print("scheduler: enabled")
    else:
        print("scheduler: disabled (set SCOUT_ENABLE_SCHEDULER=1 to enable)")
    server = ThreadingHTTPServer((args.host, args.port), SearchHandler)
    print(f"Scout search API listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SCHEDULER.stop()
        server.server_close()


if __name__ == "__main__":
    main()
