#!/usr/bin/env python3
"""
SQLite helpers for Scout pipeline.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DB_PATH = Path("data/scout.db")
MIGRATIONS_PATH = Path("backend/migrations")
_DB_LOCK = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def get_conn(db_path: Path = DB_PATH) -> Iterable[sqlite3.Connection]:
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def migrate(db_path: Path = DB_PATH, migrations_path: Path = MIGRATIONS_PATH) -> None:
    migrations_path.mkdir(parents=True, exist_ok=True)
    with get_conn(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version TEXT PRIMARY KEY,
              applied_at TEXT NOT NULL
            )
            """
        )
        applied = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        sql_files = sorted(migrations_path.glob("*.sql"))
        for sql_file in sql_files:
            if sql_file.name in applied:
                continue
            sql = sql_file.read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (sql_file.name, now_iso()),
            )


def dict_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def set_state(key: str, value: str, db_path: Path = DB_PATH) -> None:
    stamp = now_iso()
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pipeline_state(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              value=excluded.value,
              updated_at=excluded.updated_at
            """,
            (key, value, stamp),
        )


def get_state(key: str, db_path: Path = DB_PATH) -> str | None:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM pipeline_state WHERE key=?",
            (key,),
        ).fetchone()
    return str(row["value"]) if row else None


def try_acquire_lock(lock_key: str, owner: str, ttl_seconds: int = 300, db_path: Path = DB_PATH) -> bool:
    now = datetime.now(timezone.utc)
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT value, updated_at FROM pipeline_state WHERE key=?",
            (lock_key,),
        ).fetchone()
        if row:
            updated = datetime.fromisoformat(str(row["updated_at"]))
            age = (now - updated).total_seconds()
            if age < ttl_seconds and row["value"] != "":
                return False
        conn.execute(
            """
            INSERT INTO pipeline_state(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              value=excluded.value,
              updated_at=excluded.updated_at
            """,
            (lock_key, owner, now.isoformat()),
        )
    return True


def release_lock(lock_key: str, owner: str, db_path: Path = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM pipeline_state WHERE key=?",
            (lock_key,),
        ).fetchone()
        if row and str(row["value"]) == owner:
            conn.execute(
                "UPDATE pipeline_state SET value=?, updated_at=? WHERE key=?",
                ("", now_iso(), lock_key),
            )
