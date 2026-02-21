#!/usr/bin/env python3
"""
Orchestrates daily ingestion, dedupe, index export, and enrichment.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from config import (
        get_pipeline_backfill_months,
        get_pipeline_enrich_top_n,
        get_pipeline_run_utc,
        load_env_file,
    )
    from db import get_conn, get_state, migrate, release_lock, set_state, try_acquire_lock
    from enrich_links import enrich_entity, enrich_top_entities
    from entity_resolver import rebuild_canonical_entities
    from index_store import export_index_json, get_pipeline_status
    from pipeline_store import ingest_source_artifacts
except ModuleNotFoundError:
    from backend.config import (  # type: ignore
        get_pipeline_backfill_months,
        get_pipeline_enrich_top_n,
        get_pipeline_run_utc,
        load_env_file,
    )
    from backend.db import get_conn, get_state, migrate, release_lock, set_state, try_acquire_lock  # type: ignore
    from backend.enrich_links import enrich_entity, enrich_top_entities  # type: ignore
    from backend.entity_resolver import rebuild_canonical_entities  # type: ignore
    from backend.index_store import export_index_json, get_pipeline_status  # type: ignore
    from backend.pipeline_store import ingest_source_artifacts  # type: ignore


DEFAULT_INDEX = Path("data/index_data/entity_index.json")
PIPELINE_LOCK_KEY = "pipeline_lock"


def _run_command(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")


def run_source_pipeline(source: str, mode: str = "daily") -> dict[str, int]:
    if source == "hackernews":
        _run_command(
            [
                sys.executable,
                "backend/hn_to_json.py",
                "--mode",
                "feeds",
                "--per-feed",
                "180",
                "--max-items",
                "480",
                "--max-comments",
                "8",
                "--max-depth",
                "2",
                "--no-db-sync",
            ]
        )
        return ingest_source_artifacts(
            source="hackernews",
            raw_path=Path("data/hn_data/hn_raw.jsonl"),
            entities_path=Path("data/hn_data/hn_entities.json"),
            nodes_path=Path("data/hn_data/hn_source_nodes.json"),
            mode=mode,
        )

    if source == "github":
        args = [
            sys.executable,
            "backend/github_scrape.py",
            "--limit-per-query",
            "10",
            "--out",
            "data/github_data/github_repos.jsonl",
            "--sort",
            "updated",
            "--order",
            "desc",
            "--skip-release-lookup",
        ]
        if mode == "daily":
            args.extend(["--incremental", "--incremental-state", "data/github_data/github_incremental.json"])
        _run_command(args)
        _run_command(
            [
                sys.executable,
                "backend/github_to_json.py",
                "--in",
                "data/github_data/github_repos.jsonl",
                "--no-db-sync",
            ]
        )
        return ingest_source_artifacts(
            source="github",
            raw_path=Path("data/github_data/github_raw.jsonl"),
            entities_path=Path("data/github_data/github_entities.json"),
            nodes_path=Path("data/github_data/github_source_nodes.json"),
            mode=mode,
        )

    if source == "producthunt":
        _run_command(
            [
                sys.executable,
                "backend/producthunt_to_json.py",
                "--limit-posts",
                "260",
                "--order",
                "NEWEST" if mode == "backfill" else "VOTES",
                "--no-db-sync",
            ]
        )
        return ingest_source_artifacts(
            source="producthunt",
            raw_path=Path("data/producthunt_data/producthunt_raw.jsonl"),
            entities_path=Path("data/producthunt_data/producthunt_entities.json"),
            nodes_path=Path("data/producthunt_data/producthunt_source_nodes.json"),
            mode=mode,
        )

    raise ValueError(f"Unsupported source: {source}")


def bootstrap_from_artifacts_if_empty() -> dict[str, Any]:
    with get_conn() as conn:
        node_count = conn.execute("SELECT COUNT(*) AS c FROM entity_nodes").fetchone()["c"]
    if int(node_count or 0) > 0:
        return {"bootstrapped": False, "reason": "db_not_empty"}

    results: dict[str, Any] = {"bootstrapped": True, "sources": {}}
    source_map = {
        "hackernews": (
            Path("data/hn_data/hn_raw.jsonl"),
            Path("data/hn_data/hn_entities.json"),
            Path("data/hn_data/hn_source_nodes.json"),
        ),
        "github": (
            Path("data/github_data/github_raw.jsonl"),
            Path("data/github_data/github_entities.json"),
            Path("data/github_data/github_source_nodes.json"),
        ),
        "producthunt": (
            Path("data/producthunt_data/producthunt_raw.jsonl"),
            Path("data/producthunt_data/producthunt_entities.json"),
            Path("data/producthunt_data/producthunt_source_nodes.json"),
        ),
    }
    for source, (raw_path, entities_path, nodes_path) in source_map.items():
        if raw_path.exists() and entities_path.exists() and nodes_path.exists():
            try:
                results["sources"][source] = ingest_source_artifacts(
                    source=source,
                    raw_path=raw_path,
                    entities_path=entities_path,
                    nodes_path=nodes_path,
                    mode="bootstrap",
                )
            except Exception as exc:  # noqa: BLE001
                results["sources"][source] = {"error": str(exc)}
        else:
            results["sources"][source] = {"skipped": "missing_artifacts"}
    return results


def _backfill_completed(source: str, months: int) -> bool:
    completed = (get_state(f"backfill_{source}_completed") or "").lower() == "true"
    try:
        completed_months = int(get_state(f"backfill_{source}_months") or "0")
    except ValueError:
        completed_months = 0
    return completed and completed_months >= int(months)


def _mark_backfill(source: str, months: int) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    set_state(f"backfill_{source}_completed", "true")
    set_state(f"backfill_{source}_months", str(int(months)))
    set_state(f"backfill_{source}_completed_at", stamp)


def _mark_backfill_error(source: str, error: str) -> None:
    set_state(f"backfill_{source}_error", error[:600])
    set_state(f"backfill_{source}_error_at", datetime.now(timezone.utc).isoformat())


def _run_backfill_if_needed() -> dict[str, Any]:
    months = get_pipeline_backfill_months()
    result: dict[str, Any] = {"ran": False, "months": months, "sources": {}}
    backfill_days = max(30, int(months) * 30)

    if not _backfill_completed("hackernews", months):
        result["ran"] = True
        try:
            _run_command([sys.executable, "backend/hn_backfill.py", "--months", str(months)])
            ingest_source_artifacts(
                source="hackernews",
                raw_path=Path("data/hn_data/hn_raw.jsonl"),
                entities_path=Path("data/hn_data/hn_entities.json"),
                nodes_path=Path("data/hn_data/hn_source_nodes.json"),
                mode="backfill",
            )
            _mark_backfill("hackernews", months)
            result["sources"]["hackernews"] = {"ok": True}
        except Exception as exc:  # noqa: BLE001
            _mark_backfill_error("hackernews", str(exc))
            result["sources"]["hackernews"] = {"ok": False, "error": str(exc)}
    else:
        result["sources"]["hackernews"] = {"ok": True, "skipped": "already_completed"}

    if not _backfill_completed("github", months):
        result["ran"] = True
        try:
            _run_command(
                [
                    sys.executable,
                    "backend/github_scrape.py",
                    "--created-within-days",
                    str(backfill_days),
                    "--limit-per-query",
                    "40",
                    "--out",
                    "data/github_data/github_repos.jsonl",
                    "--sort",
                    "updated",
                    "--order",
                    "desc",
                    "--skip-release-lookup",
                ]
            )
            _run_command(
                [
                    sys.executable,
                    "backend/github_to_json.py",
                    "--in",
                    "data/github_data/github_repos.jsonl",
                    "--no-db-sync",
                ]
            )
            ingest_source_artifacts(
                source="github",
                raw_path=Path("data/github_data/github_raw.jsonl"),
                entities_path=Path("data/github_data/github_entities.json"),
                nodes_path=Path("data/github_data/github_source_nodes.json"),
                mode="backfill",
            )
            _mark_backfill("github", months)
            result["sources"]["github"] = {"ok": True}
        except Exception as exc:  # noqa: BLE001
            _mark_backfill_error("github", str(exc))
            result["sources"]["github"] = {"ok": False, "error": str(exc)}
    else:
        result["sources"]["github"] = {"ok": True, "skipped": "already_completed"}

    if not _backfill_completed("producthunt", months):
        result["ran"] = True
        try:
            _run_command(
                [
                    sys.executable,
                    "backend/producthunt_to_json.py",
                    "--limit-posts",
                    "2000",
                    "--order",
                    "NEWEST",
                    "--min-created-days",
                    str(backfill_days),
                    "--no-db-sync",
                ]
            )
            ingest_source_artifacts(
                source="producthunt",
                raw_path=Path("data/producthunt_data/producthunt_raw.jsonl"),
                entities_path=Path("data/producthunt_data/producthunt_entities.json"),
                nodes_path=Path("data/producthunt_data/producthunt_source_nodes.json"),
                mode="backfill",
            )
            _mark_backfill("producthunt", months)
            result["sources"]["producthunt"] = {"ok": True}
        except Exception as exc:  # noqa: BLE001
            _mark_backfill_error("producthunt", str(exc))
            result["sources"]["producthunt"] = {"ok": False, "error": str(exc)}
    else:
        result["sources"]["producthunt"] = {"ok": True, "skipped": "already_completed"}

    set_state(
        "backfill_completed",
        "true" if all((result["sources"].get(src) or {}).get("ok") for src in ["hackernews", "github", "producthunt"]) else "partial",
    )
    set_state("backfill_completed_at", datetime.now(timezone.utc).isoformat())
    return result


def run_full_pipeline(mode: str = "daily", do_backfill: bool = True, do_enrichment: bool = True) -> dict[str, Any]:
    load_env_file()
    migrate()
    owner = f"{mode}:{datetime.now(timezone.utc).isoformat()}"
    if not try_acquire_lock(PIPELINE_LOCK_KEY, owner=owner):
        return {"ok": False, "error": "pipeline already running", "locked": True}

    started = datetime.now(timezone.utc).isoformat()
    output: dict[str, Any] = {
        "ok": True,
        "mode": mode,
        "started_at": started,
        "sources": {},
        "resolver": {},
        "index": {},
        "enrichment": {},
        "backfill": {},
    }
    try:
        if do_backfill:
            output["backfill"] = _run_backfill_if_needed()

        output["bootstrap"] = bootstrap_from_artifacts_if_empty()

        for source in ["hackernews", "github", "producthunt"]:
            try:
                output["sources"][source] = run_source_pipeline(source, mode=mode)
            except Exception as exc:  # noqa: BLE001
                output["sources"][source] = {"error": str(exc)}
                output.setdefault("warnings", []).append(f"{source} refresh failed: {exc}")

        output["resolver"] = rebuild_canonical_entities()
        payload = export_index_json(DEFAULT_INDEX)
        output["index"] = {
            "entity_count": int(payload.get("entity_count") or 0),
            "path": str(DEFAULT_INDEX),
        }

        if do_enrichment:
            output["enrichment"] = enrich_top_entities(limit=get_pipeline_enrich_top_n(), force=False, max_links=8)

        set_state("pipeline_last_success_at", datetime.now(timezone.utc).isoformat())
        set_state("pipeline_last_success_meta", json.dumps(output, ensure_ascii=False))
        output["finished_at"] = datetime.now(timezone.utc).isoformat()
        return output
    except Exception as exc:  # noqa: BLE001
        output["ok"] = False
        output["error"] = str(exc)
        output["trace"] = traceback.format_exc(limit=1)
        set_state("pipeline_last_error_at", datetime.now(timezone.utc).isoformat())
        set_state("pipeline_last_error", str(exc))
        return output
    finally:
        release_lock(PIPELINE_LOCK_KEY, owner=owner)


def run_on_demand_enrichment(entity_keys: list[str], max_links: int = 8) -> dict[str, Any]:
    migrate()
    results = [enrich_entity(key, force=False, max_links=max_links) for key in entity_keys]
    return {
        "total": len(results),
        "ok": sum(1 for row in results if row.get("ok")),
        "links_added": sum(int(row.get("links_added") or 0) for row in results),
        "results": results,
    }


def _next_run_datetime_utc(hhmm: str) -> datetime:
    now = datetime.now(timezone.utc)
    parts = hhmm.split(":")
    hour = int(parts[0]) if len(parts) >= 1 else 2
    minute = int(parts[1]) if len(parts) >= 2 else 30
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


class PipelineScheduler:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._running = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scout-pipeline-scheduler", daemon=True)
        self._thread.start()
        self._running.set()

    def stop(self) -> None:
        self._stop.set()
        self._running.clear()

    def _loop(self) -> None:
        # Catch-up run if stale.
        try:
            last_success = get_state("pipeline_last_success_at") or ""
            if last_success:
                parsed = datetime.fromisoformat(last_success)
                if (datetime.now(timezone.utc) - parsed) > timedelta(hours=24):
                    run_full_pipeline(mode="catchup", do_backfill=True, do_enrichment=True)
            else:
                run_full_pipeline(mode="bootstrap", do_backfill=True, do_enrichment=True)
        except Exception:
            pass

        while not self._stop.is_set():
            target = _next_run_datetime_utc(get_pipeline_run_utc())
            while not self._stop.is_set():
                now = datetime.now(timezone.utc)
                if now >= target:
                    break
                sleep_for = min(30, max(1, int((target - now).total_seconds())))
                time.sleep(sleep_for)

            if self._stop.is_set():
                break
            run_full_pipeline(mode="daily", do_backfill=True, do_enrichment=True)


def get_scheduler_status() -> dict[str, Any]:
    status = get_pipeline_status()
    status["config"] = {
        "run_utc": get_pipeline_run_utc(),
        "enrich_top_n": get_pipeline_enrich_top_n(),
        "backfill_months": get_pipeline_backfill_months(),
    }
    return status
