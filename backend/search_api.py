#!/usr/bin/env python3
"""
Lightweight API server for Scout niche search prompts.

Endpoints:
  GET  /api/health
  POST /api/niche-search
  GET  /api/niche-search?query=...

Run:
  python backend/search_api.py --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse
import json
import os
import traceback
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from niche_search import (
        call_openrouter_nemotron,
        combine_scores,
        load_env_file,
        load_index,
        parse_refresh_targets,
        save_latest_results,
        tool_build_index,
        tool_refresh_source,
        tool_search_index,
    )
except ModuleNotFoundError:
    from backend.niche_search import (  # type: ignore
        call_openrouter_nemotron,
        combine_scores,
        load_env_file,
        load_index,
        parse_refresh_targets,
        save_latest_results,
        tool_build_index,
        tool_refresh_source,
        tool_search_index,
    )


DEFAULT_INDEX = Path("data/index_data/entity_index.json")
DEFAULT_SAVE = Path("data/index_data/latest_query_results.json")
VALID_LEADERBOARDS = {"global_prominence", "niche_opportunity"}


def _safe_float(value: object, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _normalize_scores(values: list[float]) -> list[float]:
    if not values:
        return []
    low = min(values)
    high = max(values)
    if high <= low:
        return [100.0 for _ in values]
    return [((v - low) / (high - low)) * 100.0 for v in values]


def build_trends_payload(index_payload: dict, leaderboard_mode: str = "global_prominence") -> dict:
    mode = leaderboard_mode if leaderboard_mode in VALID_LEADERBOARDS else "global_prominence"
    entities = index_payload.get("entities") or []
    if not isinstance(entities, list):
        entities = []

    global_raw: list[float] = []
    niche_raw: list[float] = []
    for row in entities:
        mention_1h = int(row.get("mention_count_1h") or 0)
        mention_24h = int(row.get("mention_count_24h") or 0)
        sources = row.get("sources") or []
        source_count = len(sources) if isinstance(sources, list) else 0
        trend_score = _safe_float(row.get("global_prominence_score"), _safe_float(row.get("trend_score")))
        confidence = _safe_float(row.get("confidence"))
        velocity = _safe_float(row.get("velocity_delta_pct"))
        spike_bonus = 14.0 if bool(row.get("spike_detected")) else 0.0
        single_source_bonus = 20.0 if source_count <= 1 else (8.0 if source_count == 2 else 0.0)

        global_raw.append(trend_score)
        niche_raw.append(
            (trend_score * 0.32)
            + (min(mention_1h, 20) * 2.4)
            + (min(mention_24h, 30) * 1.2)
            + (min(max(velocity, 0.0), 200.0) * 0.12)
            + (min(confidence, 1.0) * 25.0)
            + single_source_bonus
            + spike_bonus
        )

    normalized_global = _normalize_scores(global_raw)
    normalized_niche = _normalize_scores(niche_raw)
    trends = []
    aggregate_sources: Counter[str] = Counter()
    for idx, row in enumerate(entities):
        source_counts = row.get("source_counts") or {}
        if isinstance(source_counts, dict):
            for src, count in source_counts.items():
                try:
                    aggregate_sources[str(src)] += int(count)
                except (TypeError, ValueError):
                    continue

        global_score = round(normalized_global[idx], 2) if idx < len(normalized_global) else 0.0
        niche_score = round(normalized_niche[idx], 2) if idx < len(normalized_niche) else 0.0
        selected_score = global_score if mode == "global_prominence" else niche_score
        trends.append(
            {
                "entity": row.get("entity"),
                "trend_score": selected_score,
                "global_prominence_score": global_score,
                "niche_opportunity_score": niche_score,
                "leaderboard_mode": mode,
                "velocity_delta_pct": _safe_float(row.get("velocity_delta_pct"), 0.0),
                "sentiment": {"positive": 0.55, "neutral": 0.3, "negative": 0.15},
                "mention_count_1h": int(row.get("mention_count_1h") or 0),
                "mention_count_24h": int(row.get("mention_count_24h") or 0),
                "spike_detected": bool(row.get("spike_detected")),
                "sources": row.get("sources") or [],
                "top_keywords": row.get("top_keywords") or [],
                "source_counts": source_counts if isinstance(source_counts, dict) else {},
            }
        )

    trends.sort(
        key=lambda t: (
            t["trend_score"],
            t["global_prominence_score"],
            t.get("mention_count_24h") or 0,
        ),
        reverse=True,
    )

    return {
        "leaderboard_mode": mode,
        "available_leaderboards": sorted(VALID_LEADERBOARDS),
        "entities": trends,
        "count": len(trends),
        "source_totals": dict(aggregate_sources),
    }


def build_sources_payload(index_payload: dict) -> dict:
    source_totals = Counter()
    entities = index_payload.get("entities") or []
    if not isinstance(entities, list):
        entities = []

    for row in entities:
        counts = row.get("source_counts") or {}
        if not isinstance(counts, dict):
            continue
        for src, value in counts.items():
            try:
                source_totals[str(src)] += int(value)
            except (TypeError, ValueError):
                continue

    now = index_payload.get("_meta", {}).get("generated_at", "")
    known = [
        ("hackernews", "HN"),
        ("github", "GitHub"),
        ("producthunt", "PH"),
        ("techcrunch", "RSS"),
        ("twitter", "Twitter"),
    ]
    rows = []
    for source_id, label in known:
        items = int(source_totals.get(source_id, 0))
        rows.append(
            {
                "id": source_id,
                "label": label,
                "status": "live" if items > 0 else "cached",
                "items_ingested": items,
                "last_scraped": now,
            }
        )
    return {"sources": rows, "count": len(rows)}


def run_niche_pipeline(
    query: str,
    limit: int = 12,
    min_score: float = 10.0,
    refresh: str = "",
    rebuild_index: bool = False,
    use_nemotron: bool = True,
    index_path: Path = DEFAULT_INDEX,
    save_out: Path = DEFAULT_SAVE,
) -> dict:
    if not query.strip():
        raise ValueError("query is required")

    load_env_file()

    for target in parse_refresh_targets(refresh):
        tool_refresh_source(target)

    if rebuild_index or not index_path.exists():
        tool_build_index(index_path)

    index_payload = load_index(index_path)
    entities = index_payload.get("entities") or []
    if not isinstance(entities, list) or not entities:
        raise RuntimeError("Entity index is empty. Build/refresh data first.")

    candidates = tool_search_index(query, entities, limit=max(limit * 5, 40))
    llm_payload = None

    should_use_nemotron = use_nemotron and bool(os.getenv("OPENROUTER_API_KEY", "").strip())
    if should_use_nemotron and candidates:
        llm_payload = call_openrouter_nemotron(query, candidates)

    rows = combine_scores(candidates, llm_payload)
    filtered = [row for row in rows if row.get("include", True)] if llm_payload else rows
    final_rows = filtered if filtered else rows
    final_rows = [row for row in final_rows if float(row.get("final_score") or 0.0) >= min_score]

    save_latest_results(save_out, query, final_rows[: max(limit, 25)])

    return {
        "query": query,
        "used_nemotron": bool(llm_payload),
        "result_count": len(final_rows[:limit]),
        "results": final_rows[:limit],
        "index_stats": index_payload.get("stats") or {},
        "index_entity_count": index_payload.get("entity_count") or len(entities),
    }


class SearchHandler(BaseHTTPRequestHandler):
    server_version = "ScoutSearchAPI/0.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        """
        Avoid hard-failing requests when launched detached on Windows where stderr
        may be unavailable.
        """
        try:
            super().log_message(format, *args)
        except Exception:
            return

    def _set_json(self, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
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

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._set_json(200)
            self.wfile.write(json.dumps({"ok": True, "service": "scout-search-api"}).encode("utf-8"))
            return

        if parsed.path in {"/api/trends", "/api/trends/"}:
            try:
                load_env_file()
                if not DEFAULT_INDEX.exists():
                    tool_build_index(DEFAULT_INDEX)
                params = parse_qs(parsed.query)
                leaderboard_mode = str((params.get("leaderboard") or params.get("mode") or ["global_prominence"])[0])
                payload = build_trends_payload(load_index(DEFAULT_INDEX), leaderboard_mode=leaderboard_mode)
                self._set_json(200)
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                self._set_json(400)
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        if parsed.path in {"/api/sources", "/api/sources/"}:
            try:
                load_env_file()
                if not DEFAULT_INDEX.exists():
                    tool_build_index(DEFAULT_INDEX)
                payload = build_sources_payload(load_index(DEFAULT_INDEX))
                self._set_json(200)
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                self._set_json(400)
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        if parsed.path == "/api/niche-search":
            params = parse_qs(parsed.query)
            query = (params.get("query") or params.get("q") or [""])[0]
            limit = int((params.get("limit") or ["12"])[0])
            min_score = float((params.get("min_score") or ["10"])[0])
            use_nemotron = ((params.get("use_nemotron") or ["true"])[0]).lower() != "false"

            try:
                payload = run_niche_pipeline(
                    query=query,
                    limit=max(1, min(limit, 50)),
                    min_score=min_score,
                    use_nemotron=use_nemotron,
                )
                self._set_json(200)
                self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                self._set_json(400)
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        self._set_json(404)
        self.wfile.write(json.dumps({"error": "Not found"}).encode("utf-8"))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
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
    parser = argparse.ArgumentParser(description="Run Scout niche search API server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file()
    server = ThreadingHTTPServer((args.host, args.port), SearchHandler)
    print(f"Scout search API listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
