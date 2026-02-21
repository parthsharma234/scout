#!/usr/bin/env python3
"""
Cross-source entity merge for Scout.

Builds a unified "final entity" view by matching entities from:
  - GitHub
  - Hacker News
  - Product Hunt

Outputs (default in ./data/final_entity):
  - final_entities.json
  - final_source_nodes.json
  - match_report.json
  - final_state.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


TOKEN_STOPWORDS = {
    "ai",
    "app",
    "labs",
    "tech",
    "tools",
    "tool",
    "project",
    "startup",
    "system",
    "company",
    "inc",
    "llc",
    "ltd",
    "corp",
    "hq",
    "official",
    "platform",
}

LEGAL_SUFFIX_RE = re.compile(r"\b(inc|llc|ltd|corp|corporation|company|co)\b\.?$", flags=re.IGNORECASE)


@dataclass(frozen=True)
class Mention:
    source_id: str
    entity: str
    row: dict[str, Any]


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
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object JSON in {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def normalize_key(value: str) -> str:
    cleaned = LEGAL_SUFFIX_RE.sub("", value.strip())
    return re.sub(r"[^a-z0-9]+", "", cleaned.lower())


def token_set(value: str) -> set[str]:
    cleaned = LEGAL_SUFFIX_RE.sub("", value.strip())
    tokens = re.findall(r"[a-z0-9]+", cleaned.lower())
    return {t for t in tokens if t and t not in TOKEN_STOPWORDS}


def similarity(a: str, b: str) -> tuple[float, str]:
    a_key = normalize_key(a)
    b_key = normalize_key(b)
    if not a_key or not b_key:
        return 0.0, "empty_key"
    if a_key == b_key:
        return 1.0, "exact_key"

    if len(a_key) >= 6 and len(b_key) >= 6:
        if a_key in b_key or b_key in a_key:
            return 0.9, "substring_key"

    a_tokens = token_set(a)
    b_tokens = token_set(b)
    if a_tokens and b_tokens:
        intersect = len(a_tokens & b_tokens)
        union = len(a_tokens | b_tokens)
        if union > 0:
            jaccard = intersect / union
            if jaccard >= 0.8 and intersect >= 1:
                return 0.88, "token_overlap_strong"
            if jaccard >= 0.67 and intersect >= 2:
                return 0.82, "token_overlap"

    ratio = SequenceMatcher(None, a_key, b_key).ratio()
    if ratio >= 0.93 and min(len(a_key), len(b_key)) >= 6:
        return 0.8, "fuzzy_ratio"
    return ratio * 0.55, "weak"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge entities across GitHub, HN, and Product Hunt.")
    parser.add_argument("--github-entities", type=Path, default=Path("data/github_data/github_entities.json"))
    parser.add_argument("--hn-entities", type=Path, default=Path("data/hn_data/hn_entities.json"))
    parser.add_argument("--producthunt-entities", type=Path, default=Path("data/producthunt_data/producthunt_entities.json"))
    parser.add_argument("--github-nodes", type=Path, default=Path("data/github_data/github_source_nodes.json"))
    parser.add_argument("--hn-nodes", type=Path, default=Path("data/hn_data/hn_source_nodes.json"))
    parser.add_argument("--producthunt-nodes", type=Path, default=Path("data/producthunt_data/producthunt_source_nodes.json"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/final_entity"))
    parser.add_argument("--min-match-score", type=float, default=0.8)
    return parser.parse_args()


def load_mentions(path: Path, source_id: str) -> list[Mention]:
    payload = read_json(path)
    out: list[Mention] = []
    for row in payload.get("entities", []):
        if not isinstance(row, dict):
            continue
        entity = str(row.get("entity") or "").strip()
        if not entity:
            continue
        out.append(Mention(source_id=source_id, entity=entity, row=row))
    return out


def load_nodes(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    rows = payload.get("source_nodes", [])
    return [row for row in rows if isinstance(row, dict)]


def pick_canonical_name(members: list[Mention]) -> str:
    ranked = sorted(
        members,
        key=lambda m: (
            float(m.row.get("confidence") or 0.0),
            float(m.row.get("impressions") or 0.0),
            -len(m.entity),
        ),
        reverse=True,
    )
    return ranked[0].entity


def merge_cluster(members: list[Mention]) -> dict[str, Any]:
    canonical = pick_canonical_name(members)

    impressions = int(sum(int(m.row.get("impressions") or 0) for m in members))
    confidence_vals = [float(m.row.get("confidence") or 0.0) for m in members]
    confidence = round(sum(confidence_vals) / max(1, len(confidence_vals)), 3)

    mention_1h = int(sum(int(m.row.get("mention_count_1h") or 0) for m in members))
    mention_24h = int(sum(int(m.row.get("mention_count_24h") or 0) for m in members))
    evidence_count = int(sum(int(m.row.get("evidence_count") or 1) for m in members))

    source_counts: Counter[str] = Counter()
    sources: set[str] = set()
    keyword_counts: Counter[str] = Counter()
    quality_signals: set[str] = set()
    velocity_vals: list[float] = []
    growth_vals: list[float] = []
    spike_detected = False

    first_seen: datetime | None = None
    last_seen: datetime | None = None

    for mention in members:
        row = mention.row
        source_id = mention.source_id
        sources.add(source_id)

        row_source_counts = row.get("source_counts")
        if isinstance(row_source_counts, dict):
            for key, value in row_source_counts.items():
                source_counts[str(key)] += int(value or 0)
        else:
            source_counts[source_id] += 1

        for keyword in row.get("top_keywords", []):
            if isinstance(keyword, str) and keyword.strip():
                keyword_counts[keyword.strip().lower()] += 1

        for signal in row.get("quality_signals", []):
            if isinstance(signal, str) and signal.strip():
                quality_signals.add(signal.strip())

        velocity_vals.append(float(row.get("velocity_delta_pct") or 0.0))
        growth_vals.append(float(row.get("growth_signal_score") or 0.0))
        spike_detected = spike_detected or bool(row.get("spike_detected"))

        a = parse_iso(row.get("first_seen_at"))
        b = parse_iso(row.get("last_seen_at"))
        if a and (first_seen is None or a < first_seen):
            first_seen = a
        if b and (last_seen is None or b > last_seen):
            last_seen = b

    return {
        "entity": canonical,
        "aliases": sorted({m.entity for m in members if normalize_key(m.entity) != normalize_key(canonical)}),
        "confidence": confidence,
        "impressions": impressions,
        "mention_count_1h": mention_1h,
        "mention_count_24h": mention_24h,
        "sources": sorted(sources),
        "source_counts": dict(source_counts),
        "top_keywords": [k for k, _ in keyword_counts.most_common(8)],
        "evidence_count": evidence_count,
        "quality_signals": sorted(quality_signals),
        "first_seen_at": first_seen.isoformat() if first_seen else now_iso(),
        "last_seen_at": last_seen.isoformat() if last_seen else now_iso(),
        "velocity_delta_pct": round(sum(velocity_vals) / max(1, len(velocity_vals)), 2),
        "growth_signal_score": round(sum(growth_vals) / max(1, len(growth_vals)), 2),
        "spike_detected": bool(spike_detected or mention_24h >= 4),
        "source_entity_rows": [
            {
                "source_id": m.source_id,
                "entity": m.entity,
                "confidence": float(m.row.get("confidence") or 0.0),
                "impressions": int(m.row.get("impressions") or 0),
            }
            for m in sorted(
                members,
                key=lambda x: float(x.row.get("impressions") or 0.0),
                reverse=True,
            )
        ],
    }


def main() -> None:
    args = parse_args()
    started_at = now_iso()

    mentions = [
        *load_mentions(args.github_entities, "github"),
        *load_mentions(args.hn_entities, "hackernews"),
        *load_mentions(args.producthunt_entities, "producthunt"),
    ]
    if not mentions:
        raise RuntimeError("No entities found in input files.")

    n = len(mentions)
    parent = list(range(n))
    edge_reasons: list[dict[str, Any]] = []

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        pa = find(a)
        pb = find(b)
        if pa != pb:
            parent[pb] = pa

    for i in range(n):
        for j in range(i + 1, n):
            score, reason = similarity(mentions[i].entity, mentions[j].entity)
            if score < args.min_match_score:
                continue
            union(i, j)
            edge_reasons.append(
                {
                    "a_source": mentions[i].source_id,
                    "a_entity": mentions[i].entity,
                    "b_source": mentions[j].source_id,
                    "b_entity": mentions[j].entity,
                    "score": round(score, 3),
                    "reason": reason,
                }
            )

    components: dict[int, list[Mention]] = {}
    for idx, mention in enumerate(mentions):
        root = find(idx)
        components.setdefault(root, []).append(mention)

    merged_entities = [merge_cluster(members) for members in components.values()]
    max_impressions = max((int(row.get("impressions") or 0) for row in merged_entities), default=1)
    for row in merged_entities:
        impression_component = (int(row["impressions"]) / max_impressions) * 70.0
        source_bonus = min(15.0, (len(row["sources"]) - 1) * 7.5)
        confidence_component = float(row["confidence"]) * 15.0
        momentum_component = min(18.0, max(0.0, float(row.get("velocity_delta_pct") or 0.0)) * 0.25)
        growth_component = min(12.0, max(0.0, float(row.get("growth_signal_score") or 0.0)) * 0.12)
        spike_bonus = 6.0 if bool(row.get("spike_detected")) else 0.0
        row["trend_score"] = round(
            impression_component + source_bonus + confidence_component + momentum_component + growth_component + spike_bonus,
            2,
        )

    merged_entities.sort(key=lambda r: r["trend_score"], reverse=True)

    mapping: dict[tuple[str, str], str] = {}
    for row in merged_entities:
        canonical = str(row["entity"])
        for member in row["source_entity_rows"]:
            source_id = str(member["source_id"])
            member_entity = str(member["entity"])
            mapping[(source_id, normalize_key(member_entity))] = canonical

    nodes = [
        *load_nodes(args.github_nodes),
        *load_nodes(args.hn_nodes),
        *load_nodes(args.producthunt_nodes),
    ]
    final_nodes: list[dict[str, Any]] = []
    for node in nodes:
        source_id = str(node.get("source_id") or "")
        entity = str(node.get("entity") or "")
        canonical = mapping.get((source_id, normalize_key(entity)))
        if not canonical:
            best_score = 0.0
            best_entity = ""
            for row in merged_entities:
                score, _ = similarity(entity, str(row["entity"]))
                if score > best_score:
                    best_score = score
                    best_entity = str(row["entity"])
            if best_score >= args.min_match_score:
                canonical = best_entity
        if not canonical:
            continue
        merged = dict(node)
        merged["entity"] = canonical
        final_nodes.append(merged)

    final_nodes.sort(
        key=lambda r: (
            int(r.get("interactions") or 0) + int(float(r.get("views") or 0) * 0.18),
            int(r.get("impressions") or 0),
        ),
        reverse=True,
    )

    out_entities = args.out_dir / "final_entities.json"
    out_nodes = args.out_dir / "final_source_nodes.json"
    out_report = args.out_dir / "match_report.json"
    out_state = args.out_dir / "final_state.json"

    write_json(
        out_entities,
        {
            "_meta": {
                "description": (
                    "Final unified entities merged across GitHub, Hacker News, and Product Hunt. "
                    "This is the main cross-source ranking output for Scout."
                ),
                "source": "scout_final_entity",
                "schema_version": "1.0",
            },
            "generated_at": now_iso(),
            "entity_count": len(merged_entities),
            "entities": merged_entities,
        },
    )
    write_json(
        out_nodes,
        {
            "_meta": {
                "description": "Merged source nodes mapped onto canonical final entities.",
                "source": "scout_final_entity",
                "schema_version": "1.0",
            },
            "generated_at": now_iso(),
            "source_nodes": final_nodes,
        },
    )
    write_json(
        out_report,
        {
            "_meta": {
                "description": "Entity match decisions and pairwise links used for cross-source merge.",
                "source": "scout_final_entity",
                "schema_version": "1.0",
            },
            "generated_at": now_iso(),
            "min_match_score": args.min_match_score,
            "input_entity_count": len(mentions),
            "merged_entity_count": len(merged_entities),
            "match_edges": edge_reasons,
        },
    )
    write_json(
        out_state,
        {
            "_meta": {
                "description": "Run metadata for final cross-source entity merge job.",
                "source": "scout_final_entity",
                "schema_version": "1.0",
            },
            "last_run_started_at": started_at,
            "last_run_finished_at": now_iso(),
            "input_entity_count": len(mentions),
            "output_entity_count": len(merged_entities),
            "output_node_count": len(final_nodes),
            "match_edge_count": len(edge_reasons),
        },
    )

    print(
        f"done: input_entities={len(mentions)} merged_entities={len(merged_entities)} "
        f"nodes={len(final_nodes)} -> {out_entities}"
    )


if __name__ == "__main__":
    main()
