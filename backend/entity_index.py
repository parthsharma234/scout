#!/usr/bin/env python3
"""
Build unified entity index for Scout niche search.

Consumes source-specific data files and emits:
  data/index_data/entity_index.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SOURCE_CONFIG = {
    "hackernews": {
        "entities": Path("data/hn_data/hn_entities.json"),
        "nodes": Path("data/hn_data/hn_source_nodes.json"),
    },
    "github": {
        "entities": Path("data/github_data/github_entities.json"),
        "nodes": Path("data/github_data/github_source_nodes.json"),
    },
    "producthunt": {
        "entities": Path("data/producthunt_data/producthunt_entities.json"),
        "nodes": Path("data/producthunt_data/producthunt_source_nodes.json"),
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def merge_entities() -> tuple[list[dict[str, Any]], dict[str, int]]:
    per_entity: dict[str, dict[str, Any]] = {}
    counts = {
        "sources_seen": 0,
        "entity_rows_seen": 0,
        "node_rows_seen": 0,
    }

    for source_id, cfg in SOURCE_CONFIG.items():
        entity_payload = read_json(cfg["entities"])
        node_payload = read_json(cfg["nodes"])
        entities = entity_payload.get("entities") if isinstance(entity_payload, dict) else []
        nodes = node_payload.get("source_nodes") if isinstance(node_payload, dict) else []

        if not isinstance(entities, list):
            entities = []
        if not isinstance(nodes, list):
            nodes = []

        if entities or nodes:
            counts["sources_seen"] += 1

        counts["entity_rows_seen"] += len(entities)
        counts["node_rows_seen"] += len(nodes)

        nodes_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for node in nodes:
            entity_name = str(node.get("entity") or "")
            key = normalize_key(entity_name)
            if not key:
                continue
            nodes_by_key[key].append(
                {
                    "id": node.get("id"),
                    "source_id": node.get("source_id") or source_id,
                    "source_name": node.get("source_name") or source_id,
                    "headline": node.get("headline") or "",
                    "url": node.get("url") or "",
                    "summary": node.get("summary") or "",
                    "interactions": int(node.get("interactions") or 0),
                    "views": int(node.get("views") or 0),
                    "impressions": int(node.get("impressions") or 0),
                    "published_at": node.get("published_at"),
                    "confidence": float(node.get("confidence") or 0.0),
                }
            )

        for row in entities:
            entity_name = str(row.get("entity") or "")
            key = normalize_key(entity_name)
            if not key:
                continue

            state = per_entity.get(key)
            if not state:
                state = {
                    "entity_key": key,
                    "display_name": entity_name,
                    "confidence_sum": 0.0,
                    "confidence_count": 0,
                    "raw_score_sum": 0.0,
                    "impressions_sum": 0,
                    "mention_1h_sum": 0,
                    "mention_24h_sum": 0,
                    "spike_detected": False,
                    "sources": set(),
                    "source_counts": Counter(),
                    "keywords": Counter(),
                    "signals": set(),
                    "nodes": [],
                }
                per_entity[key] = state

            if len(entity_name) < len(state["display_name"]):
                state["display_name"] = entity_name

            state["confidence_sum"] += float(row.get("confidence") or 0.0)
            state["confidence_count"] += 1
            state["raw_score_sum"] += float(row.get("trend_score") or 0.0)
            state["impressions_sum"] += int(row.get("impressions") or 0)
            state["mention_1h_sum"] += int(row.get("mention_count_1h") or 0)
            state["mention_24h_sum"] += int(row.get("mention_count_24h") or 0)
            state["spike_detected"] = state["spike_detected"] or bool(row.get("spike_detected"))
            state["sources"].add(source_id)

            for src, count in (row.get("source_counts") or {}).items():
                state["source_counts"][str(src)] += int(count or 0)
            for kw in (row.get("top_keywords") or []):
                if isinstance(kw, str) and kw.strip():
                    state["keywords"][kw.strip().lower()] += 1
            for sig in (row.get("quality_signals") or []):
                if isinstance(sig, str) and sig.strip():
                    state["signals"].add(sig.strip())

            state["nodes"].extend(nodes_by_key.get(key, []))

    merged_rows: list[dict[str, Any]] = []
    max_impressions = max((v["impressions_sum"] for v in per_entity.values()), default=1)
    max_raw_score = max((v["raw_score_sum"] for v in per_entity.values()), default=1.0)

    for _, state in per_entity.items():
        nodes_sorted = sorted(
            state["nodes"],
            key=lambda n: (n["interactions"] + n["views"] * 0.18 + n["impressions"] * 0.05),
            reverse=True,
        )

        confidence = (
            state["confidence_sum"] / state["confidence_count"]
            if state["confidence_count"] > 0
            else 0.0
        )
        normalized_impressions = state["impressions_sum"] / max_impressions if max_impressions else 0.0
        normalized_raw_score = state["raw_score_sum"] / max_raw_score if max_raw_score else 0.0
        trend_score = round((normalized_impressions * 0.6 + normalized_raw_score * 0.4) * 100, 2)

        merged_rows.append(
            {
                "entity_key": state["entity_key"],
                "entity": state["display_name"],
                "confidence": round(confidence, 3),
                "trend_score": trend_score,
                "impressions": state["impressions_sum"],
                "mention_count_1h": state["mention_1h_sum"],
                "mention_count_24h": state["mention_24h_sum"],
                "spike_detected": state["spike_detected"],
                "sources": sorted(state["sources"]),
                "source_counts": dict(state["source_counts"]),
                "top_keywords": [kw for kw, _ in state["keywords"].most_common(10)],
                "quality_signals": sorted(state["signals"]),
                "node_count": len(nodes_sorted),
                "top_nodes": nodes_sorted[:30],
            }
        )

    merged_rows.sort(key=lambda row: row["trend_score"], reverse=True)
    return merged_rows, counts


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
    entities, counts = merge_entities()
    payload = {
        "_meta": {
            "description": (
                "Unified cross-source entity index for niche search. "
                "Aggregates Hacker News, GitHub, and Product Hunt entities and nodes."
            ),
            "schema_version": "1.0",
            "generated_at": now_iso(),
            "sources_expected": sorted(SOURCE_CONFIG.keys()),
        },
        "entity_count": len(entities),
        "stats": counts,
        "entities": entities,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"done: entities={len(entities)} -> {args.out}")


if __name__ == "__main__":
    main()
