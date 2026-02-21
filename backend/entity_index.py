#!/usr/bin/env python3
"""
Build unified entity index for Scout from SQLite canonical store.
"""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from config import load_env_file
    from db import migrate
    from entity_resolver import rebuild_canonical_entities
    from index_store import export_index_json
except ModuleNotFoundError:
    from backend.config import load_env_file  # type: ignore
    from backend.db import migrate  # type: ignore
    from backend.entity_resolver import rebuild_canonical_entities  # type: ignore
    from backend.index_store import export_index_json  # type: ignore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build unified Scout entity index")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/index_data/entity_index.json"),
        help="Output file path",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file()
    migrate()
    resolver_stats = rebuild_canonical_entities()
    payload = export_index_json(args.out)
    print(
        "done: "
        f"entities={int(payload.get('entity_count') or 0)} "
        f"resolver_entities={resolver_stats.get('entity_count', 0)} "
        f"merges={resolver_stats.get('merge_edges', 0)} "
        f"-> {args.out}"
    )


if __name__ == "__main__":
    main()

