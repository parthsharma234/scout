#!/usr/bin/env python3
"""
Niche search over unified Scout index with optional Nemotron reranking.

Examples:
  python backend/niche_search.py --query "protein folding companies"
  python backend/niche_search.py --query "robotics defense startups" --refresh hn,github --rebuild-index
  python backend/niche_search.py --query "devtools agents" --no-nemotron
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from config import load_env_file as shared_load_env_file
except ModuleNotFoundError:
    from backend.config import load_env_file as shared_load_env_file  # type: ignore


ALIAS_MAP: dict[str, list[str]] = {
    "protein_folding": [
        "protein folding",
        "protein structure",
        "alphafold",
        "computational biology",
        "drug discovery",
        "biotech ai",
    ],
    "cybersecurity": ["cyber", "security", "application security", "threat", "soc", "siem"],
    "fintech": ["fintech", "payments", "banking", "ledger", "treasury", "credit"],
    "devtools": ["devtools", "developer tools", "sdk", "api", "cli", "infrastructure"],
    "robotics": ["robotics", "robot", "autonomous systems", "drone", "perception"],
}

QUERY_STOPWORDS = {
    "show",
    "me",
    "some",
    "list",
    "company",
    "companies",
    "startup",
    "startups",
    "actual",
    "real",
    "best",
    "top",
}

PROFILE_DIMENSIONS: list[str] = [
    "sector",
    "customer_pain",
    "buyer_gtm",
    "business_model",
    "stage_traction",
    "moat_defensibility",
    "timing_tailwinds",
    "risk_filter",
]

DIMENSION_LABELS: dict[str, str] = {
    "sector": "Sector",
    "customer_pain": "Customer + pain",
    "buyer_gtm": "Buyer + GTM",
    "business_model": "Business model",
    "stage_traction": "Stage / traction",
    "moat_defensibility": "Moat / defensibility",
    "timing_tailwinds": "Timing / tailwinds",
    "risk_filter": "Risk filter",
}


def load_env_file(path: Path = Path(".env")) -> None:
    shared_load_env_file(path)


def normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-zA-Z0-9\-\+]{2,}", (text or "").lower()) if t]


def parse_refresh_targets(raw: str) -> list[str]:
    if not raw.strip():
        return []
    targets = [part.strip().lower() for part in raw.split(",") if part.strip()]
    if "all" in targets:
        return ["hn", "github", "producthunt"]
    out: list[str] = []
    for target in targets:
        if target in {"hn", "github", "producthunt"} and target not in out:
            out.append(target)
    return out


def normalize_query_profile(raw_profile: dict[str, Any] | None) -> dict[str, str]:
    profile: dict[str, str] = {}
    if not isinstance(raw_profile, dict):
        return profile
    for key in PROFILE_DIMENSIONS:
        value = str(raw_profile.get(key) or "").strip()
        if value:
            profile[key] = value
    return profile


def normalize_priority_map(raw_priorities: dict[str, Any] | None) -> dict[str, int]:
    max_rank = len(PROFILE_DIMENSIONS)
    priorities: dict[str, int] = {}
    if isinstance(raw_priorities, dict):
        for key in PROFILE_DIMENSIONS:
            raw = raw_priorities.get(key)
            try:
                rank = int(raw)
            except (TypeError, ValueError):
                continue
            priorities[key] = min(max(1, rank), max_rank)

    for default_rank, key in enumerate(PROFILE_DIMENSIONS, start=1):
        priorities.setdefault(key, default_rank)
    return priorities


def profile_priority_weights(priority_map: dict[str, int] | None) -> dict[str, float]:
    priorities = normalize_priority_map(priority_map)
    max_rank = len(PROFILE_DIMENSIONS)
    raw_weights: dict[str, float] = {}
    for key in PROFILE_DIMENSIONS:
        rank = priorities.get(key, max_rank)
        raw_weights[key] = float(max_rank - rank + 1)

    total = sum(raw_weights.values()) or 1.0
    return {key: round(value / total, 4) for key, value in raw_weights.items()}


def build_profile_query_text(query: str, query_profile: dict[str, str] | None) -> str:
    profile = normalize_query_profile(query_profile)
    parts = [str(query or "").strip()]
    for key in PROFILE_DIMENSIONS:
        value = profile.get(key, "").strip()
        if value:
            parts.append(value)
    return " ".join(part for part in parts if part).strip()


def run_command(cmd: list[str]) -> None:
    print(f"[tool] run: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        raise RuntimeError(f"Command failed with code {result.returncode}: {' '.join(cmd)}")


def tool_refresh_source(source: str) -> None:
    if source == "hn":
        run_command(
            [
                sys.executable,
                "backend/hn_to_json.py",
                "--mode",
                "feeds",
                "--per-feed",
                "180",
                "--max-items",
                "360",
                "--max-comments",
                "8",
                "--max-depth",
                "2",
            ]
        )
        return

    if source == "github":
        run_command(
            [
                sys.executable,
                "backend/github_scrape.py",
                "--limit-per-query",
                "8",
                "--out",
                "data/github_data/github_repos.jsonl",
            ]
        )
        run_command(
            [
                sys.executable,
                "backend/github_to_json.py",
                "--in",
                "data/github_data/github_repos.jsonl",
            ]
        )
        return

    if source == "producthunt":
        run_command(
            [
                sys.executable,
                "backend/producthunt_to_json.py",
                "--limit-posts",
                "240",
                "--order",
                "VOTES",
            ]
        )
        return

    raise ValueError(f"Unsupported refresh target: {source}")


def tool_build_index(index_path: Path) -> None:
    run_command([sys.executable, "backend/entity_index.py", "--out", str(index_path)])


def load_index(index_path: Path) -> dict[str, Any]:
    if not index_path.exists():
        raise FileNotFoundError(f"Index not found: {index_path}")
    return json.loads(index_path.read_text(encoding="utf-8"))


def expand_query_terms(query: str) -> list[str]:
    terms = [term for term in tokenize(query) if term not in QUERY_STOPWORDS]
    lowered = query.lower()
    for aliases in ALIAS_MAP.values():
        if any(alias in lowered for alias in aliases):
            terms.extend([term for term in tokenize(" ".join(aliases)) if term not in QUERY_STOPWORDS])
    return sorted(set(terms))


def lexical_score_entity(entity: dict[str, Any], query_terms: list[str], query_text: str) -> float:
    score = 0.0
    match_hits = 0
    entity_name = (entity.get("entity") or "").lower()
    keywords = [str(k).lower() for k in entity.get("top_keywords") or []]
    combined_keywords = " ".join(keywords)

    node_text_chunks = []
    for node in entity.get("top_nodes") or []:
        node_text_chunks.append(str(node.get("headline") or ""))
        node_text_chunks.append(str(node.get("summary") or ""))
    node_text = " ".join(node_text_chunks).lower()

    query_lower = query_text.lower().strip()
    if query_lower and query_lower in entity_name:
        score += 22.0
        match_hits += 1
    if query_lower and query_lower in node_text:
        score += 10.0
        match_hits += 1

    for term in query_terms:
        if term in entity_name:
            score += 6.0
            match_hits += 1
        if term in combined_keywords:
            score += 4.0
            match_hits += 1
        if term in node_text:
            score += 2.0
            match_hits += 1

    if match_hits == 0:
        return 0.0
    if match_hits == 1:
        score *= 0.65

    score += float(entity.get("trend_score") or 0.0) * 0.08
    score += min(float(entity.get("confidence") or 0.0) * 10.0, 8.0)
    score += min(int(entity.get("node_count") or 0), 12) * 0.5
    return round(score, 3)


def tool_search_index(query: str, entities: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    terms = expand_query_terms(query)
    scored: list[dict[str, Any]] = []
    for entity in entities:
        score = lexical_score_entity(entity, terms, query)
        if score <= 0:
            continue
        scored.append({"entity": entity, "lexical_score": score})
    scored.sort(key=lambda x: x["lexical_score"], reverse=True)
    return scored[:limit]


def _build_retrieval_context(query: str, candidates: list[dict[str, Any]], max_chunks: int = 36) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for rank, item in enumerate(candidates[:16], start=1):
        entity = item.get("entity") or {}
        entity_name = str(entity.get("entity") or "")
        if not entity_name:
            continue
        top_keywords = [str(token) for token in (entity.get("top_keywords") or [])[:8] if str(token).strip()]
        nodes = entity.get("top_nodes") or []
        for idx, node in enumerate(nodes[:3], start=1):
            headline = str(node.get("headline") or "").strip()
            summary = str(node.get("summary") or "").strip()
            url = str(node.get("url") or "").strip()
            source_id = str(node.get("source_id") or "").strip()
            if not headline and not summary:
                continue
            text_parts = [part for part in [headline, summary] if part]
            text = " ".join(text_parts)[:420]
            chunks.append(
                {
                    "entity": entity_name,
                    "candidate_rank": rank,
                    "node_rank": idx,
                    "source": source_id,
                    "keywords": top_keywords,
                    "url": url,
                    "text": text,
                }
            )
            if len(chunks) >= max_chunks:
                return chunks
    return chunks


def call_openrouter_nemotron(
    query: str,
    candidates: list[dict[str, Any]],
    query_profile: dict[str, str] | None = None,
    priority_map: dict[str, int] | None = None,
    timeout_seconds: int = 40,
) -> dict[str, Any]:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Missing OPENROUTER_API_KEY for Nemotron reranking")

    model = (
        os.getenv("OPENROUTER_MODEL", "").strip()
        or os.getenv("NEMOTRON_MODEL", "").strip()
        or "nvidia/llama-3.1-nemotron-ultra-253b-v1"
    )
    api_base = os.getenv("OPENROUTER_BASE_URL", "").strip() or "https://openrouter.ai/api/v1"

    condensed = []
    for item in candidates:
        entity = item["entity"]
        nodes = entity.get("top_nodes") or []
        top_urls = [str(node.get("url") or "") for node in nodes[:3] if str(node.get("url") or "").strip()]
        top_headlines = [str(node.get("headline") or "") for node in nodes[:3] if str(node.get("headline") or "").strip()]
        condensed.append(
            {
                "entity_key": entity.get("entity_key"),
                "entity": entity.get("entity"),
                "sources": entity.get("sources"),
                "keywords": (entity.get("top_keywords") or [])[:8],
                "trend_score": entity.get("trend_score"),
                "confidence": entity.get("confidence"),
                "headlines": top_headlines,
                "urls": top_urls,
                "lexical_score": item.get("lexical_score"),
            }
        )

    retrieval_context = _build_retrieval_context(query=query, candidates=candidates)

    normalized_profile = normalize_query_profile(query_profile)
    normalized_priorities = normalize_priority_map(priority_map)
    priority_weights = profile_priority_weights(normalized_priorities)

    system_prompt = (
        "You are a VC intelligence ranking assistant. "
        "Score each candidate against a weighted venture profile, classify entity_type, and decide inclusion. "
        "Return strict JSON only."
    )
    user_prompt = {
        "query": query,
        "query_profile": {
            key: normalized_profile.get(key, "")
            for key in PROFILE_DIMENSIONS
        },
        "dimension_labels": DIMENSION_LABELS,
        "dimension_priority_rank": normalized_priorities,
        "dimension_priority_weight": priority_weights,
        "entity_type_labels": [
            "startup_company",
            "open_source_project",
            "research_project",
            "news_about_company",
            "unknown",
        ],
        "instructions": [
            "Prefer startup_company when signals indicate a company/product venture.",
            "Use open_source_project for repos/projects without clear company signal.",
            "Set include=true only if relevant to the query niche.",
            "relevance_score must be 0-100.",
            "profile_match_score must be 0-100 and reflect weighted match to query_profile.",
            "Use dimension_match_scores (0-100 each) for dimensions present in query_profile.",
            "Do not invent URLs.",
            "Use retrieval_context as grounding evidence before deciding relevance.",
        ],
        "retrieval_context": retrieval_context,
        "candidates": condensed,
        "output_schema": {
            "results": [
                {
                    "entity_key": "string",
                    "include": "boolean",
                    "entity_type": "string",
                    "relevance_score": "number",
                    "profile_match_score": "number",
                    "dimension_match_scores": {"dimension_key": "number"},
                    "reason": "string",
                }
            ]
        },
    }

    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 2500,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
        ],
    }

    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "HTTP-Referer": "https://scout.local",
            "X-Title": "Scout VC Niche Search",
            "User-Agent": "scout-niche-search/0.1",
        },
    )

    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        raw = json.loads(response.read().decode("utf-8"))

    content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, flags=re.DOTALL | re.IGNORECASE)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        obj_match = re.search(r"(\{.*\})", content, flags=re.DOTALL)
        if obj_match:
            try:
                return json.loads(obj_match.group(1))
            except json.JSONDecodeError:
                pass
        return {"results": [], "raw": content}

def _vertical_to_cat(vertical: str) -> str:
    """Map a Nemotron-classified vertical to a cluster map color category."""
    v = (vertical or '').lower()
    if any(k in v for k in ['ai', 'machine learning', 'ml', 'llm', 'nlp', 'deep learning', 'generative', 'robotics']):
        return 'ai'
    if any(k in v for k in ['fintech', 'finance', 'payment', 'banking', 'insurance', 'crypto', 'defi']):
        return 'fintech'
    if any(k in v for k in ['dev', 'developer', 'saas', 'infra', 'cloud', 'api', 'platform', 'tool', 'software', 'b2b']):
        return 'devtools'
    if any(k in v for k in ['health', 'biotech', 'pharma', 'medical', 'defense', 'security', 'cyber', 'aerospace', 'climate']):
        return 'defense'
    if any(k in v for k in ['consumer', 'media', 'social', 'e-commerce', 'ecommerce', 'retail', 'food', 'education', 'edtech', 'entertainment']):
        return 'media'
    if 'unknown' in v or 'unspecified' in v or not v.strip():
        return 'other'
    return 'other'


def combine_scores(
    candidates: list[dict[str, Any]],
    llm_payload: dict[str, Any] | None,
    query_profile: dict[str, str] | None = None,
    priority_map: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    llm_map: dict[str, dict[str, Any]] = {}
    if isinstance(llm_payload, dict):
        for row in llm_payload.get("results") or []:
            key = str(row.get("entity_key") or "")
            if key:
                llm_map[key] = row

    llm_active = len(llm_map) > 0
    lexical_max = max((float(item.get("lexical_score") or 0.0) for item in candidates), default=1.0)
    output: list[dict[str, Any]] = []
    for item in candidates:
        entity = item["entity"]
        key = str(entity.get("entity_key") or "")
        llm = llm_map.get(key, {})
        raw_relevance = llm.get("relevance_score") if llm else None
        raw_profile_match = llm.get("profile_match_score") if llm else None
        relevance = float(raw_relevance or 0.0)
        profile_match = float(raw_profile_match or 0.0)
        include = bool(llm.get("include")) if llm else True
        entity_type = str(llm.get("entity_type") or "unknown")
        reason = str(llm.get("reason") or "")
        dimension_match_scores = llm.get("dimension_match_scores")
        if not isinstance(dimension_match_scores, dict):
            dimension_match_scores = {}

        lexical = float(item.get("lexical_score") or 0.0)
        lexical_norm = 100.0 * lexical / max(1.0, lexical_max)
        momentum = float(entity.get("momentum_score") or entity.get("trend_score") or 0.0)

        has_llm_profile_match = bool(llm) and raw_profile_match is not None
        if llm_active:
            if has_llm_profile_match:
                final_score = round(lexical_norm * 0.2 + relevance * 0.4 + profile_match * 0.3 + momentum * 0.1, 3)
            else:
                # If LLM rerank is active but this entity did not receive a profile match,
                # heavily downrank and remove momentum boost.
                final_score = round(lexical_norm * 0.25, 3)
                relevance = 0.0
                profile_match = 0.0
                if not reason:
                    reason = "Downranked: no LLM profile match returned."
        else:
            final_score = round(lexical_norm * 0.7 + momentum * 0.3, 3)

        if llm and not include:
            final_score *= 0.5

        output.append(
            {
                "entity_key": key,
                "entity": entity.get("entity"),
                "final_score": final_score,
                "lexical_score": lexical,
                "lexical_score_norm": round(lexical_norm, 3),
                "relevance_score": relevance if llm else None,
                "profile_match_score": profile_match if llm else None,
                "llm_profile_matched": has_llm_profile_match,
                "dimension_match_scores": dimension_match_scores if llm else {},
                "entity_type": entity_type,
                "include": include,
                "reason": reason,
                "sources": entity.get("sources") or [],
                "top_keywords": entity.get("top_keywords") or [],
                "trend_score": entity.get("trend_score"),
                "momentum_score": momentum,
                "confidence": entity.get("confidence"),
                "is_known_incumbent": bool(entity.get("is_known_incumbent")),
                "first_seen_at": entity.get("first_seen_at"),
                "last_seen_at": entity.get("last_seen_at"),
                "activity_last_30d": entity.get("activity_last_30d"),
                "source_counts": entity.get("source_counts") or {},
                "source_interactions": entity.get("source_interactions") or {},
                "mention_count_1h": entity.get("mention_count_1h") or 0,
                "mention_count_24h": entity.get("mention_count_24h") or 0,
                "spike_detected": bool(entity.get("spike_detected")),
                "top_nodes": entity.get("top_nodes") or [],
                # Extra fields for cluster map coloring
                "vertical": entity.get("vertical", "Unknown"),
                "one_liner": entity.get("one_liner", ""),
                "source_url": entity.get("source_url", ""),
                "cat": _vertical_to_cat(entity.get("vertical", "")),
            }
        )

    output.sort(key=lambda row: row["final_score"], reverse=True)
    return output


def save_latest_results(
    path: Path,
    query: str,
    rows: list[dict[str, Any]],
    query_profile: dict[str, str] | None = None,
    priority_map: dict[str, int] | None = None,
) -> None:
    payload = {
        "_meta": {
            "description": "Latest niche query results from Scout tool pipeline",
            "query": query,
            "query_profile": normalize_query_profile(query_profile),
            "dimension_priority_rank": normalize_priority_map(priority_map),
        },
        "result_count": len(rows),
        "results": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def print_results(query: str, rows: list[dict[str, Any]], limit: int) -> None:
    print(f"\nQuery: {query}")
    print(f"Top {min(limit, len(rows))} results:")
    for index, row in enumerate(rows[:limit], start=1):
        urls = [node.get("url") for node in row.get("top_nodes", []) if node.get("url")]
        top_url = urls[0] if urls else ""
        print(
            f"{index:02d}. {row['entity']} | score={row['final_score']:.2f} | "
            f"type={row.get('entity_type') or 'n/a'} | sources={','.join(row.get('sources') or [])}"
        )
        if row.get("reason"):
            print(f"    reason: {row['reason']}")
        if top_url:
            print(f"    url: {top_url}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search Scout entities by niche query")
    parser.add_argument("--query", required=True, help="Niche query, e.g. 'protein folding companies'")
    parser.add_argument("--limit", type=int, default=12, help="Number of results to print")
    parser.add_argument("--min-score", type=float, default=10.0, help="Minimum final score to keep")
    parser.add_argument(
        "--index",
        type=Path,
        default=Path("data/index_data/entity_index.json"),
        help="Entity index path",
    )
    parser.add_argument(
        "--refresh",
        type=str,
        default="",
        help="Optional sources to refresh before search: hn,github,producthunt,all",
    )
    parser.add_argument("--rebuild-index", action="store_true", help="Rebuild index before search")
    parser.add_argument("--no-nemotron", action="store_true", help="Disable Nemotron reranking")
    parser.add_argument(
        "--save-out",
        type=Path,
        default=Path("data/index_data/latest_query_results.json"),
        help="Path for saving latest query results",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file()

    refresh_targets = parse_refresh_targets(args.refresh)
    for target in refresh_targets:
        tool_refresh_source(target)

    if args.rebuild_index or not args.index.exists():
        tool_build_index(args.index)

    index_payload = load_index(args.index)
    entities = index_payload.get("entities") or []
    if not isinstance(entities, list) or not entities:
        raise RuntimeError("Entity index is empty. Build/refresh data first.")

    candidates = tool_search_index(args.query, entities, limit=max(args.limit * 5, 40))

    llm_payload = None
    use_nemotron = not args.no_nemotron and bool(os.getenv("OPENROUTER_API_KEY", "").strip())
    if use_nemotron and candidates:
        try:
            print("[tool] run: nemotron_rerank")
            llm_payload = call_openrouter_nemotron(args.query, candidates)
        except (ValueError, urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as exc:
            print(f"[warn] Nemotron rerank failed, falling back to lexical ranking: {exc}", file=sys.stderr)
            llm_payload = None

    rows = combine_scores(candidates, llm_payload)
    filtered = [row for row in rows if row.get("include", True)] if llm_payload else rows
    final_rows = filtered if filtered else rows
    final_rows = [row for row in final_rows if float(row.get("final_score") or 0.0) >= args.min_score]

    save_latest_results(args.save_out, args.query, final_rows[: max(args.limit, 25)])
    print_results(args.query, final_rows, args.limit)
    print(f"\nSaved results -> {args.save_out}")


if __name__ == "__main__":
    main()
