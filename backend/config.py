#!/usr/bin/env python3
"""
Shared configuration/env loading for Scout backend.
"""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_ENV_PATH = Path(".env")


def load_env_file(path: Path = DEFAULT_ENV_PATH) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_env_any(*keys: str, default: str = "") -> str:
    for key in keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return default


def get_producthunt_token() -> str:
    return get_env_any("PHUNT", "PRODUCTHUNT_DEVELOPER_TOKEN", "PRODUCT_HUNT")


def get_google_cse_api_key() -> str:
    return get_env_any("GOOGLE_CSE_API_KEY")


def get_google_cse_cx() -> str:
    return get_env_any("GOOGLE_CSE_CX")


def get_pipeline_run_utc() -> str:
    return get_env_any("PIPELINE_RUN_UTC", default="02:30")


def get_pipeline_enrich_top_n() -> int:
    value = get_env_any("PIPELINE_ENRICH_TOP_N", default="50")
    try:
        return max(1, int(value))
    except ValueError:
        return 50


def get_pipeline_backfill_months() -> int:
    value = get_env_any("PIPELINE_BACKFILL_MONTHS", default="3")
    try:
        return max(1, int(value))
    except ValueError:
        return 3
