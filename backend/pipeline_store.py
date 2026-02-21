#!/usr/bin/env python3
"""
Persistence helpers for ingestion outputs -> SQLite.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

try:
    from db import get_conn, json_dumps, now_iso
except ModuleNotFoundError:
    from backend.db import get_conn, json_dumps, now_iso  # type: ignore


SOURCE_EXTERNAL_ID_KEYS: dict[str, tuple[str, ...]] = {
    "hackernews": ("hn_id", "id"),
    "github": ("repo_id", "id", "full_name"),
    "producthunt": ("ph_id", "id"),
}

SOURCE_PUBLISHED_KEYS: dict[str, tuple[str, ...]] = {
    "hackernews": ("hn_created_at", "published_at"),
    "github": ("pushed_at", "updated_at", "published_at"),
    "producthunt": ("created_at", "published_at"),
}


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _pick_key(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def start_ingestion_run(source: str, mode: str = "manual", meta: dict[str, Any] | None = None) -> int:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE ingestion_runs
            SET status='failed',
                finished_at=?,
                error='interrupted_or_stale'
            WHERE source=? AND mode=? AND status='running'
            """,
            (now_iso(), source, mode),
        )
        cur = conn.execute(
            """
            INSERT INTO ingestion_runs(source, mode, status, started_at, meta_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (source, mode, "running", now_iso(), json_dumps(meta or {})),
        )
        run_id = int(cur.lastrowid or 0)
    return run_id


def finish_ingestion_run(
    run_id: int,
    status: str,
    fetched_count: int = 0,
    written_count: int = 0,
    error: str = "",
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE ingestion_runs
            SET status=?, finished_at=?, fetched_count=?, written_count=?, error=?
            WHERE id=?
            """,
            (status, now_iso(), int(fetched_count), int(written_count), error, run_id),
        )


def upsert_source_item(
    source: str,
    row: dict[str, Any],
    run_id: int | None = None,
) -> bool:
    ext_keys = SOURCE_EXTERNAL_ID_KEYS.get(source, ("id",))
    pub_keys = SOURCE_PUBLISHED_KEYS.get(source, ("published_at",))
    external_id = _pick_key(row, ext_keys)
    if not external_id:
        return False
    published_at = _pick_key(row, pub_keys)
    fetched_at = str(row.get("fetched_at") or now_iso())
    title = str(row.get("title") or row.get("headline") or row.get("name") or "")
    url = str(row.get("url") or "")
    stamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO source_items(
              source, external_id, published_at, fetched_at, title, url, raw_json, run_id, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, external_id) DO UPDATE SET
              published_at=excluded.published_at,
              fetched_at=excluded.fetched_at,
              title=excluded.title,
              url=excluded.url,
              raw_json=excluded.raw_json,
              run_id=excluded.run_id,
              updated_at=excluded.updated_at
            """,
            (
                source,
                external_id,
                published_at or None,
                fetched_at,
                title,
                url,
                json_dumps(row),
                run_id,
                stamp,
                stamp,
            ),
        )
    return True


def insert_entity_mention(
    source: str,
    entity_name: str,
    confidence: float,
    row: dict[str, Any],
    run_id: int | None = None,
) -> None:
    mention_key = normalize_key(entity_name)
    if not mention_key:
        return
    published_at = str(row.get("published_at") or row.get("hn_created_at") or row.get("created_at") or row.get("updated_at") or "")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO entity_mentions(
              source, mention_key, entity_name, confidence, published_at, item_external_id,
              url, title, summary, keywords_json, raw_json, run_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                mention_key,
                entity_name,
                float(confidence or 0.0),
                published_at or None,
                str(row.get("item_external_id") or row.get("id") or row.get("repo_id") or row.get("ph_id") or row.get("hn_id") or ""),
                str(row.get("url") or ""),
                str(row.get("title") or row.get("headline") or ""),
                str(row.get("summary") or ""),
                json_dumps(row.get("keywords") or row.get("top_keywords") or []),
                json_dumps(row),
                run_id,
                now_iso(),
            ),
        )


def upsert_entity_node(
    source: str,
    row: dict[str, Any],
    run_id: int | None = None,
    node_type: str = "source_raw",
) -> bool:
    node_id = str(row.get("id") or "").strip()
    entity_name = str(row.get("entity") or "").strip()
    if not node_id or not entity_name:
        return False
    alias_key = normalize_key(entity_name)
    stamp = now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO entity_nodes(
              node_id, entity_id, alias_key, entity_name, source_id, source_name,
              headline, url, summary, interactions, views, impressions,
              published_at, confidence, node_type, raw_json, created_at, updated_at
            )
            VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
              alias_key=excluded.alias_key,
              entity_name=excluded.entity_name,
              source_id=excluded.source_id,
              source_name=excluded.source_name,
              headline=excluded.headline,
              url=excluded.url,
              summary=excluded.summary,
              interactions=excluded.interactions,
              views=excluded.views,
              impressions=excluded.impressions,
              published_at=excluded.published_at,
              confidence=excluded.confidence,
              node_type=excluded.node_type,
              raw_json=excluded.raw_json,
              updated_at=excluded.updated_at
            """,
            (
                node_id,
                alias_key,
                entity_name,
                str(row.get("source_id") or source),
                str(row.get("source_name") or source.title()),
                str(row.get("headline") or ""),
                str(row.get("url") or ""),
                str(row.get("summary") or ""),
                int(row.get("interactions") or 0),
                int(row.get("views") or 0),
                int(row.get("impressions") or 0),
                str(row.get("published_at") or ""),
                float(row.get("confidence") or 0.0),
                node_type,
                json_dumps(row),
                stamp,
                stamp,
            ),
        )
    if run_id:
        insert_entity_mention(
            source=source,
            entity_name=entity_name,
            confidence=float(row.get("confidence") or 0.0),
            row={
                "item_external_id": row.get("id"),
                "url": row.get("url"),
                "title": row.get("headline"),
                "summary": row.get("summary"),
                "published_at": row.get("published_at"),
                "keywords": [],
            },
            run_id=run_id,
        )
    return True


def ingest_source_artifacts(
    source: str,
    raw_path: Path,
    entities_path: Path,
    nodes_path: Path,
    mode: str = "manual",
) -> dict[str, int]:
    run_id = start_ingestion_run(source=source, mode=mode, meta={"raw_path": str(raw_path)})
    fetched = 0
    written = 0
    try:
        raw_rows = read_jsonl(raw_path)
        entities_payload = read_json(entities_path)
        node_payload = read_json(nodes_path)
        node_rows = node_payload.get("source_nodes") if isinstance(node_payload, dict) else []
        entity_rows = entities_payload.get("entities") if isinstance(entities_payload, dict) else []
        if not isinstance(node_rows, list):
            node_rows = []
        if not isinstance(entity_rows, list):
            entity_rows = []
        ext_keys = SOURCE_EXTERNAL_ID_KEYS.get(source, ("id",))
        pub_keys = SOURCE_PUBLISHED_KEYS.get(source, ("published_at",))
        with get_conn() as conn:
            for row in raw_rows:
                fetched += 1
                external_id = _pick_key(row, ext_keys)
                if external_id:
                    published_at = _pick_key(row, pub_keys)
                    fetched_at = str(row.get("fetched_at") or now_iso())
                    title = str(row.get("title") or row.get("headline") or row.get("name") or "")
                    url = str(row.get("url") or "")
                    stamp = now_iso()
                    conn.execute(
                        """
                        INSERT INTO source_items(
                          source, external_id, published_at, fetched_at, title, url, raw_json, run_id, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(source, external_id) DO UPDATE SET
                          published_at=excluded.published_at,
                          fetched_at=excluded.fetched_at,
                          title=excluded.title,
                          url=excluded.url,
                          raw_json=excluded.raw_json,
                          run_id=excluded.run_id,
                          updated_at=excluded.updated_at
                        """,
                        (
                            source,
                            external_id,
                            published_at or None,
                            fetched_at,
                            title,
                            url,
                            json_dumps(row),
                            run_id,
                            stamp,
                            stamp,
                        ),
                    )
                    written += 1

                for cand in row.get("entity_candidates") or []:
                    if not isinstance(cand, dict):
                        continue
                    entity_name = str(cand.get("entity") or "").strip()
                    mention_key = normalize_key(entity_name)
                    if not mention_key:
                        continue
                    published_at = str(
                        row.get("published_at")
                        or row.get("hn_created_at")
                        or row.get("created_at")
                        or row.get("updated_at")
                        or ""
                    )
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO entity_mentions(
                          source, mention_key, entity_name, confidence, published_at, item_external_id,
                          url, title, summary, keywords_json, raw_json, run_id, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            source,
                            mention_key,
                            entity_name,
                            float(cand.get("confidence") or 0.0),
                            published_at or None,
                            str(
                                row.get("item_external_id")
                                or row.get("id")
                                or row.get("repo_id")
                                or row.get("ph_id")
                                or row.get("hn_id")
                                or ""
                            ),
                            str(row.get("url") or ""),
                            str(row.get("title") or row.get("headline") or ""),
                            str(row.get("summary") or ""),
                            json_dumps(row.get("keywords") or row.get("top_keywords") or []),
                            json_dumps(row),
                            run_id,
                            now_iso(),
                        ),
                    )

            for entity_row in entity_rows:
                if not isinstance(entity_row, dict):
                    continue
                entity_name = str(entity_row.get("entity") or "").strip()
                mention_key = normalize_key(entity_name)
                if not mention_key:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO entity_mentions(
                      source, mention_key, entity_name, confidence, published_at, item_external_id,
                      url, title, summary, keywords_json, raw_json, run_id, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source,
                        mention_key,
                        entity_name,
                        float(entity_row.get("confidence") or 0.0),
                        str(entity_row.get("last_seen_at") or ""),
                        entity_name,
                        "",
                        entity_name,
                        "",
                        json_dumps(entity_row.get("top_keywords") or []),
                        json_dumps(entity_row),
                        run_id,
                        now_iso(),
                    ),
                )

            for row in node_rows:
                if not isinstance(row, dict):
                    continue
                fetched += 1
                node_id = str(row.get("id") or "").strip()
                entity_name = str(row.get("entity") or "").strip()
                if not node_id or not entity_name:
                    continue
                alias_key = normalize_key(entity_name)
                stamp = now_iso()
                conn.execute(
                    """
                    INSERT INTO entity_nodes(
                      node_id, entity_id, alias_key, entity_name, source_id, source_name,
                      headline, url, summary, interactions, views, impressions,
                      published_at, confidence, node_type, raw_json, created_at, updated_at
                    )
                    VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(node_id) DO UPDATE SET
                      alias_key=excluded.alias_key,
                      entity_name=excluded.entity_name,
                      source_id=excluded.source_id,
                      source_name=excluded.source_name,
                      headline=excluded.headline,
                      url=excluded.url,
                      summary=excluded.summary,
                      interactions=excluded.interactions,
                      views=excluded.views,
                      impressions=excluded.impressions,
                      published_at=excluded.published_at,
                      confidence=excluded.confidence,
                      node_type=excluded.node_type,
                      raw_json=excluded.raw_json,
                      updated_at=excluded.updated_at
                    """,
                    (
                        node_id,
                        alias_key,
                        entity_name,
                        str(row.get("source_id") or source),
                        str(row.get("source_name") or source.title()),
                        str(row.get("headline") or ""),
                        str(row.get("url") or ""),
                        str(row.get("summary") or ""),
                        int(row.get("interactions") or 0),
                        int(row.get("views") or 0),
                        int(row.get("impressions") or 0),
                        str(row.get("published_at") or ""),
                        float(row.get("confidence") or 0.0),
                        "source_raw",
                        json_dumps(row),
                        stamp,
                        stamp,
                    ),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO entity_mentions(
                      source, mention_key, entity_name, confidence, published_at, item_external_id,
                      url, title, summary, keywords_json, raw_json, run_id, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source,
                        alias_key,
                        entity_name,
                        float(row.get("confidence") or 0.0),
                        str(row.get("published_at") or ""),
                        node_id,
                        str(row.get("url") or ""),
                        str(row.get("headline") or ""),
                        str(row.get("summary") or ""),
                        "[]",
                        json_dumps(row),
                        run_id,
                        now_iso(),
                    ),
                )
                written += 1

        finish_ingestion_run(
            run_id=run_id,
            status="success",
            fetched_count=fetched,
            written_count=written,
        )
        return {"run_id": run_id, "fetched": fetched, "written": written}
    except Exception as exc:  # noqa: BLE001
        finish_ingestion_run(
            run_id=run_id,
            status="failed",
            fetched_count=fetched,
            written_count=written,
            error=str(exc),
        )
        raise
