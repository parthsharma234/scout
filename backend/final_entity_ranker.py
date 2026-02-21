#!/usr/bin/env python3
"""
Rank final entities for frontend display.

Reads merged entities from data/final_entity/final_entities.json, computes a
relevance score, and writes:
  - data/final_entity/final_entities_ranked.json
  - data/final_entity/final_entities_top50.json
"""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GENERIC_ENTITY_TERMS = {
    "ai",
    "app",
    "tool",
    "tools",
    "data",
    "open",
    "base",
    "seed",
    "labs",
    "tech",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected object JSON at {path}")
    return data


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def normalize_scalar(values: list[float]) -> list[float]:
    if not values:
        return []
    low = min(values)
    high = max(values)
    if math.isclose(low, high):
        return [1.0 for _ in values]
    spread = high - low
    return [(v - low) / spread for v in values]


def token_entropy_ratio(source_counts: dict[str, Any]) -> float:
    counts = [float(v or 0) for v in source_counts.values() if float(v or 0) > 0]
    if not counts:
        return 0.0
    total = sum(counts)
    if total <= 0:
        return 0.0
    probs = [c / total for c in counts]
    entropy = -sum(p * math.log(p, 2) for p in probs if p > 0)
    max_entropy = math.log(len(probs), 2) if len(probs) > 1 else 1.0
    return min(1.0, entropy / max_entropy)


def hygiene_penalty(entity: str, evidence_count: int, confidence: float) -> float:
    penalty = 0.0
    key = re.sub(r"[^a-z0-9]+", "", entity.lower())
    tokens = re.findall(r"[a-z0-9]+", entity.lower())
    if len(key) <= 3:
        penalty += 0.35
    if len(tokens) == 1 and tokens and tokens[0] in GENERIC_ENTITY_TERMS:
        penalty += 0.45
    if evidence_count <= 1:
        penalty += 0.2
    if confidence < 0.55:
        penalty += 0.15
    return min(1.0, penalty)


def build_ranked_entities(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for entity in entities:
        impressions = float(entity.get("impressions") or 0.0)
        mention_1h = float(entity.get("mention_count_1h") or 0.0)
        mention_24h = float(entity.get("mention_count_24h") or 0.0)
        velocity = float(entity.get("velocity_delta_pct") or 0.0)
        spike = bool(entity.get("spike_detected"))
        confidence = float(entity.get("confidence") or 0.0)
        evidence_count = int(entity.get("evidence_count") or 0)
        source_counts = entity.get("source_counts") or {}
        if not isinstance(source_counts, dict):
            source_counts = {}
        sources = entity.get("sources") or []
        if not isinstance(sources, list):
            sources = []
        quality_signals = entity.get("quality_signals") or []
        if not isinstance(quality_signals, list):
            quality_signals = []

        last_seen = parse_iso(entity.get("last_seen_at"))
        if last_seen is None:
            age_hours = 240.0
        else:
            age_hours = max(0.0, (now - last_seen).total_seconds() / 3600.0)

        engagement_raw = (
            math.log1p(impressions)
            + 1.3 * math.log1p(mention_1h)
            + 0.9 * math.log1p(mention_24h)
        )
        momentum_raw = max(0.0, velocity) / 100.0 + (0.35 if spike else 0.0)
        coverage_raw = min(1.0, len(set(str(s) for s in sources)) / 3.0)
        diversity_raw = token_entropy_ratio(source_counts)
        cross_source_raw = (coverage_raw * 0.65) + (diversity_raw * 0.35)
        confidence_raw = min(1.0, confidence + min(0.2, len(quality_signals) * 0.02))
        recency_raw = math.exp(-age_hours / 96.0)
        penalty_raw = hygiene_penalty(str(entity.get("entity") or ""), evidence_count, confidence)

        row = dict(entity)
        row["_metrics"] = {
            "engagement_raw": engagement_raw,
            "momentum_raw": momentum_raw,
            "cross_source_raw": cross_source_raw,
            "confidence_raw": confidence_raw,
            "recency_raw": recency_raw,
            "penalty_raw": penalty_raw,
        }
        rows.append(row)

    engagement = normalize_scalar([r["_metrics"]["engagement_raw"] for r in rows])
    momentum = normalize_scalar([r["_metrics"]["momentum_raw"] for r in rows])
    cross_source = normalize_scalar([r["_metrics"]["cross_source_raw"] for r in rows])
    confidence_vals = normalize_scalar([r["_metrics"]["confidence_raw"] for r in rows])
    recency = normalize_scalar([r["_metrics"]["recency_raw"] for r in rows])
    penalty = normalize_scalar([r["_metrics"]["penalty_raw"] for r in rows])

    for idx, row in enumerate(rows):
        global_prominence = (
            0.28 * engagement[idx]
            + 0.22 * momentum[idx]
            + 0.30 * cross_source[idx]
            + 0.12 * confidence_vals[idx]
            + 0.08 * recency[idx]
            - 0.25 * penalty[idx]
        )
        source_count = len(set(str(s) for s in (row.get("sources") or [])))
        single_source_bonus = 1.0 if source_count <= 1 else (0.45 if source_count == 2 else 0.0)
        niche_opportunity = (
            0.18 * engagement[idx]
            + 0.34 * momentum[idx]
            + 0.08 * cross_source[idx]
            + 0.14 * confidence_vals[idx]
            + 0.16 * recency[idx]
            + 0.18 * single_source_bonus
            - 0.14 * penalty[idx]
        )
        global_score = round(max(0.0, global_prominence) * 100.0, 2)
        niche_score = round(max(0.0, niche_opportunity) * 100.0, 2)
        row["global_prominence_score"] = global_score
        row["niche_opportunity_score"] = niche_score
        row["leaderboard_scores"] = {
            "global_prominence": global_score,
            "niche_opportunity": niche_score,
        }
        row["relevance_score"] = global_score
        row["ranking_components"] = {
            "engagement": round(engagement[idx], 4),
            "momentum": round(momentum[idx], 4),
            "cross_source": round(cross_source[idx], 4),
            "confidence": round(confidence_vals[idx], 4),
            "recency": round(recency[idx], 4),
            "hygiene_penalty": round(penalty[idx], 4),
        }
        del row["_metrics"]

    rows.sort(
        key=lambda r: (
            float(r.get("global_prominence_score") or r.get("relevance_score") or 0.0),
            float(r.get("trend_score") or 0.0),
        ),
        reverse=True,
    )
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rank merged final entities and export top results.")
    parser.add_argument("--in", dest="input_path", type=Path, default=Path("data/final_entity/final_entities.json"))
    parser.add_argument("--out-ranked", type=Path, default=Path("data/final_entity/final_entities_ranked.json"))
    parser.add_argument("--out-top", type=Path, default=Path("data/final_entity/final_entities_top50.json"))
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--min-confidence", type=float, default=0.45)
    parser.add_argument("--min-evidence", type=int, default=1)
    parser.add_argument("--min-sources-top", type=int, default=2)
    parser.add_argument("--min-mentions24h-top", type=int, default=1)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = read_json(args.input_path)
    entities = payload.get("entities") or []
    if not isinstance(entities, list):
        raise ValueError("Input file does not contain an entities array.")

    ranked = build_ranked_entities([e for e in entities if isinstance(e, dict)])
    filtered = [
        r for r in ranked
        if float(r.get("confidence") or 0.0) >= args.min_confidence
        and int(r.get("evidence_count") or 0) >= args.min_evidence
    ]
    top_n = max(1, args.top_n)

    def source_count(row: dict[str, Any]) -> int:
        sources = row.get("sources") or []
        return len(sources) if isinstance(sources, list) else 0

    def mention_24h(row: dict[str, Any]) -> int:
        return int(row.get("mention_count_24h") or 0)

    tier1 = [
        r for r in filtered
        if source_count(r) >= args.min_sources_top and mention_24h(r) >= args.min_mentions24h_top
    ]
    tier2 = [
        r for r in filtered
        if source_count(r) >= args.min_sources_top and r not in tier1
    ]
    tier3 = [
        r for r in filtered
        if mention_24h(r) >= args.min_mentions24h_top and r not in tier1 and r not in tier2
    ]
    tier4 = [r for r in filtered if r not in tier1 and r not in tier2 and r not in tier3]

    top_entities = (tier1 + tier2 + tier3 + tier4)[:top_n]

    for row in top_entities:
        if row in tier1:
            row["selection_tier"] = "multi_source_active"
        elif row in tier2:
            row["selection_tier"] = "multi_source"
        elif row in tier3:
            row["selection_tier"] = "active_single_source"
        else:
            row["selection_tier"] = "fallback"

    ranked_payload = {
        "_meta": {
            "description": "Ranked final entities with composite relevance score.",
            "source": "scout_final_entity_ranker",
            "schema_version": "1.0",
        },
        "generated_at": now_iso(),
        "input_entity_count": len(entities),
        "ranked_entity_count": len(ranked),
        "filtered_entity_count": len(filtered),
        "entities": ranked,
    }
    niche_ranked = sorted(
        filtered,
        key=lambda r: (
            float(r.get("niche_opportunity_score") or 0.0),
            float(r.get("global_prominence_score") or r.get("relevance_score") or 0.0),
        ),
        reverse=True,
    )
    top_payload = {
        "_meta": {
            "description": "Top ranked final entities for frontend display.",
            "source": "scout_final_entity_ranker",
            "schema_version": "1.0",
        },
        "generated_at": now_iso(),
        "input_entity_count": len(entities),
        "filtered_entity_count": len(filtered),
        "top_n": args.top_n,
        "min_sources_top": args.min_sources_top,
        "min_mentions24h_top": args.min_mentions24h_top,
        "tier_counts": {
            "multi_source_active": len(tier1),
            "multi_source": len(tier2),
            "active_single_source": len(tier3),
            "fallback": len(tier4),
        },
        "leaderboards": {
            "global_prominence": top_entities,
            "niche_opportunity": niche_ranked[:top_n],
        },
        "entities": top_entities,
    }

    write_json(args.out_ranked, ranked_payload)
    write_json(args.out_top, top_payload)

    print(
        f"done: input={len(entities)} ranked={len(ranked)} filtered={len(filtered)} "
        f"top={len(top_entities)} -> {args.out_top}"
    )


if __name__ == "__main__":
    main()
