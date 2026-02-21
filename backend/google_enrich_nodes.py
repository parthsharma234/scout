#!/usr/bin/env python3
"""
Google enrichment for compiled final entities.

Uses:
  - Google Custom Search JSON API
  - Google Knowledge Graph Search API

Reads:
  - data/final_entity/final_entities.json
  - data/final_entity/final_source_nodes.json

Writes:
  - data/final_entity/google_source_nodes.json
  - data/final_entity/final_source_nodes_enriched.json
  - data/final_entity/final_entities_enriched.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
KG_ENDPOINT = "https://kgsearch.googleapis.com/v1/entities:search"

SOURCE_GOOGLE_SEARCH = "google_search"
SOURCE_GOOGLE_KG = "google_kg"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def http_json(url: str, timeout_seconds: int) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "scout-google-enricher/0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def cse_search(api_key: str, cse_id: str, query: str, num: int, timeout_seconds: int) -> list[dict[str, Any]]:
    params = {
        "key": api_key,
        "cx": cse_id,
        "q": query,
        "num": max(1, min(num, 10)),
        "safe": "off",
    }
    url = f"{CSE_ENDPOINT}?{urllib.parse.urlencode(params)}"
    payload = http_json(url, timeout_seconds)
    return [row for row in (payload.get("items") or []) if isinstance(row, dict)]


def kg_search(api_key: str, query: str, limit: int, timeout_seconds: int) -> list[dict[str, Any]]:
    params = {
        "key": api_key,
        "query": query,
        "limit": max(1, min(limit, 10)),
        "indent": "True",
    }
    url = f"{KG_ENDPOINT}?{urllib.parse.urlencode(params)}"
    payload = http_json(url, timeout_seconds)
    return [row for row in (payload.get("itemListElement") or []) if isinstance(row, dict)]


def text_contains_entity(text: str, entity: str) -> bool:
    a = normalize_key(text)
    b = normalize_key(entity)
    if not a or not b:
        return False
    return b in a or a in b


def extract_cse_published_at(item: dict[str, Any]) -> str | None:
    pagemap = item.get("pagemap") or {}
    if not isinstance(pagemap, dict):
        return None
    metatags = pagemap.get("metatags") or []
    if not isinstance(metatags, list):
        return None
    keys = (
        "article:published_time",
        "og:published_time",
        "publishdate",
        "datepublished",
        "pubdate",
    )
    for tag in metatags:
        if not isinstance(tag, dict):
            continue
        for key in keys:
            value = tag.get(key)
            if value and parse_iso(str(value)):
                return parse_iso(str(value)).isoformat()
    return None


def make_cse_node(entity: str, item: dict[str, Any], rank: int) -> dict[str, Any] | None:
    url = str(item.get("link") or "").strip()
    if not url:
        return None
    title = str(item.get("title") or "").strip() or f"{entity} - Web result"
    snippet = str(item.get("snippet") or "").strip() or "Google search result."

    base_conf = 0.6
    if text_contains_entity(title, entity):
        base_conf += 0.17
    if text_contains_entity(snippet, entity):
        base_conf += 0.1
    confidence = round(min(0.95, base_conf), 3)

    interactions = max(6, int(42 - rank * 7))
    views = max(20, int(interactions * 12))
    impressions = max(views, int(views * confidence * 1.2))

    return {
        "id": f"google-search-{normalize_key(entity)}-{normalize_key(url)}",
        "entity": entity,
        "source_id": SOURCE_GOOGLE_SEARCH,
        "source_name": "Google Search",
        "headline": title,
        "url": url,
        "summary": snippet[:400],
        "interactions": interactions,
        "views": views,
        "impressions": impressions,
        "published_at": extract_cse_published_at(item),
        "confidence": confidence,
        "google_rank": rank,
    }


def make_kg_node(entity: str, row: dict[str, Any], rank: int) -> dict[str, Any] | None:
    result = row.get("result") or {}
    if not isinstance(result, dict):
        return None

    name = str(result.get("name") or "").strip()
    description = str(result.get("description") or "").strip()
    dd = result.get("detailedDescription") or {}
    if not isinstance(dd, dict):
        dd = {}
    url = str(dd.get("url") or "").strip() or str(result.get("url") or "").strip()
    if not url:
        return None

    body = str(dd.get("articleBody") or "").strip()
    summary = " ".join([part for part in [description, body] if part]).strip() or "Google Knowledge Graph result."
    headline = f"Knowledge Graph: {name or entity}"
    result_score = float(row.get("resultScore") or 0.0)

    confidence = 0.63
    if text_contains_entity(name, entity):
        confidence += 0.2
    if text_contains_entity(summary, entity):
        confidence += 0.08
    confidence = round(min(0.96, confidence), 3)

    interactions = max(8, int(min(80, 10 + result_score * 0.09) - rank * 2))
    views = max(24, int(interactions * 9))
    impressions = max(views, int(views * (1.0 + confidence)))

    return {
        "id": f"google-kg-{normalize_key(entity)}-{normalize_key(url)}",
        "entity": entity,
        "source_id": SOURCE_GOOGLE_KG,
        "source_name": "Google Knowledge Graph",
        "headline": headline,
        "url": url,
        "summary": summary[:400],
        "interactions": interactions,
        "views": views,
        "impressions": impressions,
        "published_at": None,
        "confidence": confidence,
        "google_rank": rank,
        "result_score": result_score,
    }


def merge_nodes(existing_nodes: list[dict[str, Any]], new_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed: dict[str, dict[str, Any]] = {}
    for node in [*existing_nodes, *new_nodes]:
        key = str(node.get("id") or "")
        if not key:
            key = f"{node.get('source_id')}-{normalize_key(str(node.get('entity') or ''))}-{normalize_key(str(node.get('url') or ''))}"
        keyed[key] = node
    rows = list(keyed.values())
    rows.sort(
        key=lambda r: (
            int(r.get("interactions") or 0) + int(float(r.get("views") or 0) * 0.18),
            int(r.get("impressions") or 0),
        ),
        reverse=True,
    )
    return rows


def enrich_entities(
    entities: list[dict[str, Any]],
    google_nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_entity_key: dict[str, list[dict[str, Any]]] = {}
    for node in google_nodes:
        key = normalize_key(str(node.get("entity") or ""))
        if not key:
            continue
        by_entity_key.setdefault(key, []).append(node)

    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for row in entities:
        if not isinstance(row, dict):
            continue
        entity = str(row.get("entity") or "")
        key = normalize_key(entity)
        nodes = by_entity_key.get(key, [])
        if not nodes:
            out.append(dict(row))
            continue

        updated = dict(row)
        source_counts = dict(updated.get("source_counts") or {})
        sources = set(updated.get("sources") or [])
        quality_signals = set(updated.get("quality_signals") or [])
        source_rows = list(updated.get("source_entity_rows") or [])

        grouped: dict[str, list[dict[str, Any]]] = {}
        for node in nodes:
            sid = str(node.get("source_id") or "")
            grouped.setdefault(sid, []).append(node)

        mention_1h = int(updated.get("mention_count_1h") or 0)
        mention_24h = int(updated.get("mention_count_24h") or 0)
        evidence_count = int(updated.get("evidence_count") or 0)
        impressions = int(updated.get("impressions") or 0)
        conf = float(updated.get("confidence") or 0.0)

        for sid, items in grouped.items():
            sources.add(sid)
            source_counts[sid] = int(source_counts.get(sid, 0)) + len(items)
            evidence_count += len(items)
            mention_24h += len(items)
            quality_signals.add("google_enrichment")
            quality_signals.add(sid)

            avg_conf = sum(float(n.get("confidence") or 0.0) for n in items) / max(1, len(items))
            imp_sum = sum(int(n.get("impressions") or 0) for n in items)
            impressions += imp_sum
            conf = (conf + avg_conf) / 2.0
            source_rows.append(
                {
                    "source_id": sid,
                    "entity": entity,
                    "confidence": round(avg_conf, 3),
                    "impressions": int(imp_sum),
                }
            )

            for item in items:
                published = parse_iso(item.get("published_at"))
                if published and (now - published).total_seconds() <= 3600:
                    mention_1h += 1

        updated["sources"] = sorted(sources)
        updated["source_counts"] = source_counts
        updated["quality_signals"] = sorted(quality_signals)
        updated["source_entity_rows"] = source_rows
        updated["mention_count_1h"] = mention_1h
        updated["mention_count_24h"] = mention_24h
        updated["evidence_count"] = evidence_count
        updated["impressions"] = impressions
        updated["confidence"] = round(min(1.0, conf), 3)
        updated["last_seen_at"] = now_iso()
        out.append(updated)
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enrich final entities with Google Search + KG nodes.")
    parser.add_argument("--entities-in", type=Path, default=Path("data/final_entity/final_entities.json"))
    parser.add_argument("--nodes-in", type=Path, default=Path("data/final_entity/final_source_nodes.json"))
    parser.add_argument("--out-google-nodes", type=Path, default=Path("data/final_entity/google_source_nodes.json"))
    parser.add_argument("--out-merged-nodes", type=Path, default=Path("data/final_entity/final_source_nodes_enriched.json"))
    parser.add_argument("--out-entities", type=Path, default=Path("data/final_entity/final_entities_enriched.json"))
    parser.add_argument("--max-entities", type=int, default=100, help="0 means all entities")
    parser.add_argument("--cse-results", type=int, default=3, help="Custom Search results per entity (1-10)")
    parser.add_argument("--kg-results", type=int, default=2, help="Knowledge Graph results per entity (1-10)")
    parser.add_argument("--sleep-seconds", type=float, default=0.12)
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--api-key", default=os.getenv("GOOGLE_API_KEY", "").strip())
    parser.add_argument("--cse-id", default=os.getenv("GOOGLE_CSE_ID", "").strip())
    parser.add_argument("--query-suffix", default="startup company")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.api_key:
        raise ValueError("Missing Google API key. Set GOOGLE_API_KEY or pass --api-key.")
    if not args.cse_id:
        raise ValueError("Missing Custom Search Engine ID. Set GOOGLE_CSE_ID or pass --cse-id.")

    entities_payload = read_json(args.entities_in)
    nodes_payload = read_json(args.nodes_in)

    entities = [r for r in (entities_payload.get("entities") or []) if isinstance(r, dict)]
    existing_nodes = [r for r in (nodes_payload.get("source_nodes") or []) if isinstance(r, dict)]
    if not entities:
        raise RuntimeError(f"No entities found in {args.entities_in}")

    rows = entities
    if args.max_entities and args.max_entities > 0:
        rows = rows[: args.max_entities]

    google_nodes: list[dict[str, Any]] = []
    failed = 0
    for idx, row in enumerate(rows, start=1):
        entity = str(row.get("entity") or "").strip()
        if not entity:
            continue
        aliases = [str(a).strip() for a in (row.get("aliases") or []) if str(a).strip()]
        query_base = entity
        if aliases:
            query_base = f"{entity} OR {aliases[0]}"
        cse_query = f"{query_base} {args.query_suffix}".strip()

        try:
            cse_items = cse_search(args.api_key, args.cse_id, cse_query, args.cse_results, args.timeout_seconds)
            for rank, item in enumerate(cse_items, start=1):
                node = make_cse_node(entity, item, rank)
                if node:
                    google_nodes.append(node)
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
            failed += 1
            print(f"[warn] cse failed for '{entity}': {exc}")

        try:
            kg_items = kg_search(args.api_key, entity, args.kg_results, args.timeout_seconds)
            for rank, item in enumerate(kg_items, start=1):
                node = make_kg_node(entity, item, rank)
                if node:
                    google_nodes.append(node)
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
            failed += 1
            print(f"[warn] kg failed for '{entity}': {exc}")

        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)
        if idx % 20 == 0:
            print(f"processed {idx}/{len(rows)} entities, google_nodes={len(google_nodes)}")

    # De-dupe by entity+url for google nodes themselves.
    dedup_google: dict[str, dict[str, Any]] = {}
    for node in google_nodes:
        key = f"{normalize_key(str(node.get('entity') or ''))}|{normalize_key(str(node.get('url') or ''))}|{node.get('source_id')}"
        dedup_google[key] = node
    google_nodes = list(dedup_google.values())

    merged_nodes = merge_nodes(existing_nodes, google_nodes)
    enriched_entities = enrich_entities(entities, google_nodes)

    google_payload = {
        "_meta": {
            "description": "Google-enriched source nodes from Custom Search and Knowledge Graph APIs.",
            "source": "scout_google_enrichment",
            "schema_version": "1.0",
            "generated_at": now_iso(),
        },
        "input_entity_count": len(rows),
        "google_node_count": len(google_nodes),
        "failed_requests": failed,
        "source_nodes": google_nodes,
    }
    merged_payload = {
        "_meta": {
            "description": "Merged source nodes including Google-enriched nodes.",
            "source": "scout_google_enrichment",
            "schema_version": "1.0",
            "generated_at": now_iso(),
        },
        "input_node_count": len(existing_nodes),
        "google_node_count": len(google_nodes),
        "source_nodes": merged_nodes,
    }
    entities_out_payload = dict(entities_payload)
    entities_out_payload["generated_at"] = now_iso()
    entities_out_payload["entity_count"] = len(enriched_entities)
    entities_out_payload["entities"] = enriched_entities
    entities_out_payload["_meta"] = {
        **(entities_payload.get("_meta") or {}),
        "description": "Final entities enriched with Google Search + Knowledge Graph evidence.",
        "source": "scout_google_enrichment",
        "schema_version": "1.0",
    }

    write_json(args.out_google_nodes, google_payload)
    write_json(args.out_merged_nodes, merged_payload)
    write_json(args.out_entities, entities_out_payload)

    print(
        f"done: entities={len(rows)} google_nodes={len(google_nodes)} merged_nodes={len(merged_nodes)} "
        f"failed_requests={failed}"
    )
    print(f"google_nodes -> {args.out_google_nodes}")
    print(f"merged_nodes -> {args.out_merged_nodes}")
    print(f"enriched_entities -> {args.out_entities}")


if __name__ == "__main__":
    main()
