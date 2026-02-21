#!/usr/bin/env python3
"""
Canonical entity resolver and scoring for Scout.

This module rebuilds canonical entity profiles from raw mentions/nodes in SQLite.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

try:
    from db import get_conn, json_dumps, json_loads, now_iso
except ModuleNotFoundError:
    from backend.db import get_conn, json_dumps, json_loads, now_iso  # type: ignore


MERGE_THRESHOLD = 2.5

KNOWN_INCUMBENT_KEYS = {
    "openai",
    "anthropic",
    "google",
    "microsoft",
    "meta",
    "amazon",
    "aws",
    "oracle",
    "databricks",
    "stripe",
    "cloudflare",
    "spacex",
    "figma",
    "notion",
    "vercel",
    "netlify",
    "visualstudio",
    "visualstudiocode",
    "vscode",
}

KNOWN_INCUMBENT_DOMAINS = {
    "openai.com",
    "anthropic.com",
    "google.com",
    "microsoft.com",
    "meta.com",
    "amazon.com",
    "aws.amazon.com",
    "oracle.com",
    "databricks.com",
    "stripe.com",
    "cloudflare.com",
    "spacex.com",
    "figma.com",
    "notion.so",
    "vercel.com",
    "netlify.com",
    "netlify.app",
    "vercel.app",
    "visualstudio.com",
    "visualstudio.microsoft.com",
}

TOKEN_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "tool",
    "startup",
    "startups",
    "project",
    "platform",
    "official",
    "beta",
    "alpha",
    "launch",
    "show",
    "news",
}

SOURCE_MOMENTUM_WEIGHT = {
    "hackernews": 1.0,
    "github": 0.92,
    "producthunt": 1.08,
    "web_enriched": 0.35,
}

SOURCE_INTERACTION_WEIGHT = {
    "hackernews": 1.0,
    "github": 0.9,
    "producthunt": 1.1,
}


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _tokenize(value: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9\-\+]{1,}", (value or "").lower())
    return {token for token in tokens if token and token not in TOKEN_STOPWORDS}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _domain_root(url: str) -> str:
    if not url:
        return ""
    try:
        host = (urlparse(url).hostname or "").lower().strip()
    except ValueError:
        return ""
    if not host:
        return ""
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    return ".".join(parts[-2:])


def _url_norm(url: str) -> str:
    if not url:
        return ""
    return url.strip().rstrip("/").lower()


def _time_delta_days(a: datetime | None, b: datetime | None) -> float | None:
    if not a or not b:
        return None
    return abs((a - b).total_seconds()) / 86400.0


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    idx = (len(ordered) - 1) * max(0.0, min(1.0, p))
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return float(ordered[lo])
    lo_v = float(ordered[lo])
    hi_v = float(ordered[hi])
    frac = idx - lo
    return lo_v + (hi_v - lo_v) * frac


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _candidate_template(alias_key: str) -> dict[str, Any]:
    return {
        "alias_key": alias_key,
        "names": set(),
        "keys": {alias_key},
        "domains": set(),
        "urls": set(),
        "tokens": set(),
        "source_ids": set(),
        "owners": set(),
        "earliest_at": None,
        "latest_at": None,
        "evidence_count": 0,
    }


def _extract_owner(raw_payload: dict[str, Any]) -> str:
    if not isinstance(raw_payload, dict):
        return ""
    owner_login = str(raw_payload.get("owner_login") or "").strip().lower()
    if owner_login:
        return owner_login
    owner = raw_payload.get("owner")
    if isinstance(owner, dict):
        candidate = str(owner.get("login") or owner.get("name") or "").strip().lower()
        if candidate:
            return candidate
    organization = str(raw_payload.get("organization") or "").strip().lower()
    if organization:
        return organization
    return ""


def _add_timestamp(candidate: dict[str, Any], value: str | None) -> None:
    parsed = _parse_time(value)
    if not parsed:
        return
    earliest = candidate.get("earliest_at")
    latest = candidate.get("latest_at")
    candidate["earliest_at"] = parsed if earliest is None else min(earliest, parsed)
    candidate["latest_at"] = parsed if latest is None else max(latest, parsed)


def _merge_score(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, str]:
    a_domains = set(a.get("domains") or set())
    b_domains = set(b.get("domains") or set())
    a_urls = set(a.get("urls") or set())
    b_urls = set(b.get("urls") or set())
    a_tokens = set(a.get("tokens") or set())
    b_tokens = set(b.get("tokens") or set())
    a_owners = set(a.get("owners") or set())
    b_owners = set(b.get("owners") or set())

    domain_overlap = bool(a_domains & b_domains)
    url_overlap = bool(a_urls & b_urls)
    owner_overlap = bool(a_owners & b_owners)
    token_overlap_size = len(a_tokens & b_tokens)

    # Hard block obvious identity conflicts.
    if a_domains and b_domains and not domain_overlap:
        if not url_overlap and not owner_overlap and token_overlap_size <= 1:
            return 0.0, "identity_conflict"

    score = 0.0
    reasons: list[str] = []

    if domain_overlap:
        score += 2.1
        reasons.append("shared_domain_root")
    if url_overlap:
        score += 1.7
        reasons.append("url_overlap")

    a_keys = set(a.get("keys") or set())
    b_keys = set(b.get("keys") or set())
    key_containment = False
    for ka in a_keys:
        for kb in b_keys:
            if len(ka) >= 5 and len(kb) >= 5 and (ka in kb or kb in ka):
                key_containment = True
                break
        if key_containment:
            break
    if key_containment:
        score += 0.8
        reasons.append("key_containment")

    if a_tokens and b_tokens:
        small = a_tokens if len(a_tokens) <= len(b_tokens) else b_tokens
        big = b_tokens if small is a_tokens else a_tokens
        if small and small.issubset(big):
            score += 1.1
            reasons.append("token_containment")
        elif token_overlap_size >= 2:
            score += 0.7
            reasons.append("token_overlap")

    if owner_overlap:
        score += 1.1
        reasons.append("owner_overlap")

    close_days = _time_delta_days(a.get("latest_at"), b.get("latest_at"))
    if close_days is not None and close_days <= 14:
        score += 0.55
        reasons.append("close_publish_window")

    source_overlap = set(a.get("source_ids") or set()) & set(b.get("source_ids") or set())
    if source_overlap:
        score += 0.2
        reasons.append("source_overlap")

    if score <= 0:
        return 0.0, "no_match"
    return round(score, 3), ",".join(reasons)


class _UnionFind:
    def __init__(self, keys: list[str]) -> None:
        self.parent = {key: key for key in keys}
        self.rank = {key: 0 for key in keys}

    def find(self, key: str) -> str:
        parent = self.parent[key]
        if parent != key:
            self.parent[key] = self.find(parent)
        return self.parent[key]

    def union(self, a: str, b: str) -> bool:
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return False
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        return True


def _collect_candidates() -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    candidates: dict[str, Any] = {}
    alias_nodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    alias_mentions: dict[str, list[dict[str, Any]]] = defaultdict(list)

    with get_conn() as conn:
        node_rows = conn.execute(
            """
            SELECT id, node_id, alias_key, entity_name, source_id, source_name, url,
                   headline, summary, interactions, views, impressions, published_at,
                   confidence, node_type, raw_json
            FROM entity_nodes
            """
        ).fetchall()
        mention_rows = conn.execute(
            """
            SELECT id, source, mention_key, entity_name, confidence, published_at, item_external_id,
                   url, title, summary, keywords_json, raw_json
            FROM entity_mentions
            """
        ).fetchall()

    for row in node_rows:
        alias_key = normalize_key(str(row["alias_key"] or row["entity_name"] or ""))
        if not alias_key:
            continue
        candidate = candidates.setdefault(alias_key, _candidate_template(alias_key))
        name = str(row["entity_name"] or "").strip()
        if name:
            candidate["names"].add(name)
            candidate["tokens"].update(_tokenize(name))
        source_id = str(row["source_id"] or "").strip().lower()
        if source_id:
            candidate["source_ids"].add(source_id)
        url = _url_norm(str(row["url"] or ""))
        if url:
            candidate["urls"].add(url)
            domain = _domain_root(url)
            if domain:
                candidate["domains"].add(domain)
        raw_payload = json_loads(row["raw_json"], {})
        owner = _extract_owner(raw_payload if isinstance(raw_payload, dict) else {})
        if owner:
            candidate["owners"].add(owner)
        _add_timestamp(candidate, str(row["published_at"] or ""))
        candidate["evidence_count"] += 1

        alias_nodes[alias_key].append(
            {
                "node_id": str(row["node_id"] or ""),
                "entity_name": name,
                "source_id": source_id,
                "source_name": str(row["source_name"] or ""),
                "url": url,
                "headline": str(row["headline"] or ""),
                "summary": str(row["summary"] or ""),
                "interactions": int(row["interactions"] or 0),
                "views": int(row["views"] or 0),
                "impressions": int(row["impressions"] or 0),
                "published_at": str(row["published_at"] or ""),
                "confidence": float(row["confidence"] or 0.0),
                "node_type": str(row["node_type"] or "source_raw"),
            }
        )

    for row in mention_rows:
        alias_key = normalize_key(str(row["mention_key"] or row["entity_name"] or ""))
        if not alias_key:
            continue
        candidate = candidates.setdefault(alias_key, _candidate_template(alias_key))
        name = str(row["entity_name"] or "").strip()
        if name:
            candidate["names"].add(name)
            candidate["tokens"].update(_tokenize(name))
        source = str(row["source"] or "").strip().lower()
        if source:
            candidate["source_ids"].add(source)
        url = _url_norm(str(row["url"] or ""))
        if url:
            candidate["urls"].add(url)
            domain = _domain_root(url)
            if domain:
                candidate["domains"].add(domain)
        raw_payload = json_loads(row["raw_json"], {})
        owner = _extract_owner(raw_payload if isinstance(raw_payload, dict) else {})
        if owner:
            candidate["owners"].add(owner)
        _add_timestamp(candidate, str(row["published_at"] or ""))
        candidate["evidence_count"] += 1

        keywords = json_loads(row["keywords_json"], [])
        if not isinstance(keywords, list):
            keywords = []
        alias_mentions[alias_key].append(
            {
                "source": source,
                "entity_name": name,
                "item_external_id": str(row["item_external_id"] or ""),
                "url": url,
                "title": str(row["title"] or ""),
                "summary": str(row["summary"] or ""),
                "published_at": str(row["published_at"] or ""),
                "confidence": float(row["confidence"] or 0.0),
                "keywords": [str(item).lower() for item in keywords if str(item).strip()],
            }
        )

    return candidates, alias_nodes, alias_mentions


def _build_candidate_pairs(candidates: dict[str, Any]) -> list[tuple[str, str]]:
    keys = list(candidates.keys())
    if len(keys) <= 1:
        return []

    buckets: dict[str, set[str]] = defaultdict(set)
    for key, candidate in candidates.items():
        buckets[f"p:{key[:5]}"].add(key)
        for domain in candidate.get("domains") or []:
            buckets[f"d:{domain}"].add(key)
        for token in candidate.get("tokens") or []:
            if len(token) >= 4:
                buckets[f"t:{token}"].add(key)

    pairs: set[tuple[str, str]] = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        values = sorted(members)
        if len(values) > 200:
            continue
        for idx in range(len(values)):
            for jdx in range(idx + 1, len(values)):
                pairs.add((values[idx], values[jdx]))

    # Fallback for very small sets.
    if not pairs and len(keys) <= 120:
        ordered = sorted(keys)
        for idx in range(len(ordered)):
            for jdx in range(idx + 1, len(ordered)):
                pairs.add((ordered[idx], ordered[jdx]))

    return sorted(pairs)


def _choose_display_name(aliases: list[str], candidates: dict[str, Any]) -> str:
    scored: dict[str, float] = defaultdict(float)
    for alias in aliases:
        candidate = candidates.get(alias) or {}
        weight = float(candidate.get("evidence_count") or 1.0)
        for name in candidate.get("names") or []:
            clean = str(name).strip()
            if not clean:
                continue
            score = weight
            if normalize_key(clean) == alias:
                score += 0.7
            token_count = len(clean.split())
            if 1 <= token_count <= 3:
                score += 0.4
            if token_count > 4:
                score -= 0.8
            scored[clean] += score
    if not scored:
        return aliases[0]
    return sorted(scored.items(), key=lambda item: (-item[1], len(item[0]), item[0].lower()))[0][0]


def _node_momentum(node: dict[str, Any], now_dt: datetime) -> float:
    interactions = float(node.get("interactions") or 0.0)
    views = float(node.get("views") or 0.0)
    impressions = float(node.get("impressions") or 0.0)
    source_id = str(node.get("source_id") or "").lower()
    weight = SOURCE_MOMENTUM_WEIGHT.get(source_id, 0.9)

    published = _parse_time(str(node.get("published_at") or ""))
    age_days = 30.0
    if published:
        age_days = max(0.0, (now_dt - published).total_seconds() / 86400.0)
    decay = math.exp(-age_days / 24.0)
    base = math.log1p(max(0.0, interactions + views * 0.18 + impressions * 0.05))
    return base * decay * weight


def _is_known_incumbent(
    display_name: str,
    domains: set[str],
    first_seen: datetime | None,
    node_count: int,
    engagement_total: float,
) -> bool:
    entity_key = normalize_key(display_name)
    if entity_key in KNOWN_INCUMBENT_KEYS:
        return True
    if domains & KNOWN_INCUMBENT_DOMAINS:
        return True
    if first_seen:
        age_days = (datetime.now(timezone.utc) - first_seen).total_seconds() / 86400.0
        if age_days > 365 and node_count >= 20 and engagement_total > 18000:
            return True
    return False


def _to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _prune_duplicate_mentions() -> int:
    with get_conn() as conn:
        before = int(conn.execute("SELECT COUNT(*) AS c FROM entity_mentions").fetchone()["c"] or 0)
        conn.execute(
            """
            DELETE FROM entity_mentions
            WHERE id NOT IN (
              SELECT MIN(id)
              FROM entity_mentions
              GROUP BY source, mention_key, COALESCE(item_external_id, ''), COALESCE(url, ''), COALESCE(published_at, '')
            )
            """
        )
        after = int(conn.execute("SELECT COUNT(*) AS c FROM entity_mentions").fetchone()["c"] or 0)
    return max(0, before - after)


def rebuild_canonical_entities() -> dict[str, Any]:
    deduped_mentions = _prune_duplicate_mentions()
    candidates, alias_nodes, alias_mentions = _collect_candidates()
    alias_keys = sorted(candidates.keys())
    if not alias_keys:
        with get_conn() as conn:
            conn.execute("DELETE FROM entity_aliases")
            conn.execute("DELETE FROM entity_profiles")
            conn.execute("DELETE FROM entity_daily_metrics")
            conn.execute("UPDATE entity_nodes SET entity_id=NULL")
        return {"entity_count": 0, "alias_count": 0, "merge_edges": 0, "deduped_mentions": deduped_mentions}

    pairs = _build_candidate_pairs(candidates)
    uf = _UnionFind(alias_keys)
    merge_edges = 0
    merge_reasons: dict[tuple[str, str], str] = {}

    for a, b in pairs:
        score, reason = _merge_score(candidates[a], candidates[b])
        if score >= MERGE_THRESHOLD:
            if uf.union(a, b):
                merge_edges += 1
            merge_reasons[(a, b)] = reason

    groups: dict[str, list[str]] = defaultdict(list)
    for alias in alias_keys:
        groups[uf.find(alias)].append(alias)

    now_dt = datetime.now(timezone.utc)
    profiles: list[dict[str, Any]] = []
    alias_to_entity_key: dict[str, str] = {}
    entity_key_used: set[str] = set()
    daily_metrics_map: dict[str, dict[str, dict[str, Any]]] = {}

    for _, aliases in sorted(groups.items(), key=lambda item: (len(item[1]), item[0]), reverse=True):
        aliases = sorted(aliases)
        display_name = _choose_display_name(aliases, candidates)
        base_key = normalize_key(display_name) or aliases[0]
        entity_key = base_key
        if entity_key in entity_key_used:
            suffix = 2
            while f"{base_key}{suffix}" in entity_key_used:
                suffix += 1
            entity_key = f"{base_key}{suffix}"
        entity_key_used.add(entity_key)

        nodes: list[dict[str, Any]] = []
        mentions: list[dict[str, Any]] = []
        domains: set[str] = set()
        alias_names: set[str] = set()
        merged_reason_set: set[str] = set()
        for alias in aliases:
            candidate = candidates.get(alias) or {}
            domains.update(candidate.get("domains") or set())
            alias_names.update(candidate.get("names") or set())
            nodes.extend(alias_nodes.get(alias) or [])
            mentions.extend(alias_mentions.get(alias) or [])
            alias_to_entity_key[alias] = entity_key

        if len(aliases) > 1:
            alias_set = set(aliases)
            for (a, b), reason in merge_reasons.items():
                if a in alias_set and b in alias_set and reason:
                    merged_reason_set.add(reason)

        first_seen: datetime | None = None
        last_seen: datetime | None = None
        activity_last_30d = 0
        mention_count_1h = 0
        mention_count_24h = 0
        confidence_values: list[float] = []
        source_counts: Counter[str] = Counter()
        source_interactions: Counter[str] = Counter()
        keyword_counts: Counter[str] = Counter()
        quality_signals: set[str] = set()
        daily_rows: dict[str, dict[str, Any]] = {}

        def touch_daily(date_key: str) -> dict[str, Any]:
            if date_key not in daily_rows:
                daily_rows[date_key] = {
                    "mention_count": 0,
                    "impressions": 0,
                    "source_counts": Counter(),
                }
            return daily_rows[date_key]

        engagement_total = 0.0
        raw_momentum = 0.0

        for node in nodes:
            source = str(node.get("source_id") or "").lower()
            if source:
                source_counts[source] += 1
            confidence_values.append(float(node.get("confidence") or 0.0))
            raw_momentum += _node_momentum(node, now_dt)
            node_interactions = max(
                0.0,
                float(node.get("interactions") or 0.0)
                + float(node.get("views") or 0.0) * 0.22
                + float(node.get("impressions") or 0.0) * 0.04,
            )
            if source in SOURCE_INTERACTION_WEIGHT:
                source_interactions[source] += int(round(node_interactions))
            engagement_total += max(
                0.0,
                float(node.get("interactions") or 0.0)
                + float(node.get("views") or 0.0) * 0.15
                + float(node.get("impressions") or 0.0) * 0.04,
            )

            title_text = f"{node.get('headline') or ''} {node.get('summary') or ''}"
            keyword_counts.update(_tokenize(title_text))

            published = _parse_time(str(node.get("published_at") or ""))
            if published:
                first_seen = published if first_seen is None else min(first_seen, published)
                last_seen = published if last_seen is None else max(last_seen, published)
                age_seconds = (now_dt - published).total_seconds()
                if age_seconds <= 30 * 86400:
                    activity_last_30d += 1
                if age_seconds <= 24 * 3600:
                    mention_count_24h += 1
                if age_seconds <= 3600:
                    mention_count_1h += 1
                date_key = published.date().isoformat()
                bucket = touch_daily(date_key)
                bucket["mention_count"] += 1
                bucket["impressions"] += int(node.get("impressions") or 0)
                if source:
                    bucket["source_counts"][source] += 1

            if str(node.get("node_type") or "") == "source_enriched":
                quality_signals.add("web_enriched")

        seen_mentions: set[tuple[str, str, str, str, str]] = set()
        for mention in mentions:
            dedupe_key = (
                str(mention.get("source") or ""),
                str(mention.get("item_external_id") or ""),
                str(mention.get("url") or ""),
                str(mention.get("title") or ""),
                str(mention.get("published_at") or ""),
            )
            if dedupe_key in seen_mentions:
                continue
            seen_mentions.add(dedupe_key)
            source = str(mention.get("source") or "").lower()
            if source:
                source_counts[source] += 1
            confidence_values.append(float(mention.get("confidence") or 0.0))
            keyword_counts.update(
                token for token in (mention.get("keywords") or []) if token and token not in TOKEN_STOPWORDS
            )
            keyword_counts.update(_tokenize(str(mention.get("title") or "")))
            keyword_counts.update(_tokenize(str(mention.get("summary") or "")))

            published = _parse_time(str(mention.get("published_at") or ""))
            if published:
                first_seen = published if first_seen is None else min(first_seen, published)
                last_seen = published if last_seen is None else max(last_seen, published)
                age_seconds = (now_dt - published).total_seconds()
                if age_seconds <= 30 * 86400:
                    activity_last_30d += 1
                if age_seconds <= 24 * 3600:
                    mention_count_24h += 1
                if age_seconds <= 3600:
                    mention_count_1h += 1
                date_key = published.date().isoformat()
                bucket = touch_daily(date_key)
                bucket["mention_count"] += 1
                if source:
                    bucket["source_counts"][source] += 1

        if len(source_counts) >= 2:
            quality_signals.add("multi_source")
        if mention_count_24h >= 3:
            quality_signals.add("daily_spike")
        if mention_count_1h >= 2:
            quality_signals.add("hourly_spike")
        if domains:
            quality_signals.add("domain_signal")
        for reason in sorted(merged_reason_set):
            quality_signals.add(f"merge:{reason}")

        keyword_counts.update(_tokenize(display_name))
        top_keywords = [token for token, _ in keyword_counts.most_common(10)]

        raw_momentum += math.log1p(max(0, activity_last_30d)) * 1.8
        raw_momentum += float(mention_count_24h) * 0.7 + float(mention_count_1h) * 1.2
        raw_momentum *= 1.0 + min(0.35, max(0.0, len(source_counts) - 1) * 0.08)

        cross_source_interaction = 0.0
        interaction_weight_sum = 0.0
        for source_id, weight in SOURCE_INTERACTION_WEIGHT.items():
            if source_id in source_interactions:
                cross_source_interaction += math.log1p(max(0, source_interactions[source_id])) * weight
                interaction_weight_sum += weight
        if interaction_weight_sum > 0:
            cross_source_interaction = cross_source_interaction / interaction_weight_sum
        cross_source_interaction += max(0.0, len(set(source_interactions.keys())) - 1) * 0.22

        is_incumbent = _is_known_incumbent(
            display_name=display_name,
            domains=domains,
            first_seen=first_seen,
            node_count=len(nodes),
            engagement_total=engagement_total,
        )

        confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.45
        confidence = _clamp(confidence, 0.0, 1.0)

        profiles.append(
            {
                "entity_key": entity_key,
                "display_name": display_name,
                "aliases": aliases,
                "alias_names": sorted(alias_names),
                "first_seen_at": _to_iso(first_seen),
                "last_seen_at": _to_iso(last_seen),
                "confidence": round(confidence, 3),
                "mention_count_1h": int(mention_count_1h),
                "mention_count_24h": int(mention_count_24h),
                "activity_last_30d": int(activity_last_30d),
                "sources": sorted(source_counts.keys()),
                "source_counts": dict(source_counts),
                "top_keywords": top_keywords,
                "quality_signals": sorted(quality_signals),
                "node_count": int(len(nodes)),
                "raw_momentum": float(raw_momentum),
                "raw_interaction_score": float(cross_source_interaction),
                "source_interactions": dict(source_interactions),
                "is_known_incumbent": 1 if is_incumbent else 0,
            }
        )
        daily_metrics_map[entity_key] = daily_rows

    momentum_values = [float(row["raw_momentum"]) for row in profiles]
    interaction_values = [float(row.get("raw_interaction_score") or 0.0) for row in profiles]
    lo = _percentile(momentum_values, 0.05)
    hi = _percentile(momentum_values, 0.95)
    span = hi - lo if hi - lo > 1e-6 else 1.0
    ilow = _percentile(interaction_values, 0.05)
    ihigh = _percentile(interaction_values, 0.95)
    ispan = ihigh - ilow if ihigh - ilow > 1e-6 else 1.0
    for row in profiles:
        raw_momentum = float(row["raw_momentum"])
        momentum_unit = _clamp((raw_momentum - lo) / span, 0.0, 1.0)
        # sqrt spreads low values upward so we avoid 100 vs near-0 collapse.
        momentum_score = math.sqrt(momentum_unit) * 100.0

        raw_interaction = float(row.get("raw_interaction_score") or 0.0)
        interaction_unit = _clamp((raw_interaction - ilow) / ispan, 0.0, 1.0)
        interaction_score = math.sqrt(interaction_unit) * 100.0

        recency_term = min(
            100.0,
            row["mention_count_24h"] * 6.2 + row["mention_count_1h"] * 10.0 + row["activity_last_30d"] * 0.95,
        )
        trend_score = momentum_score * 0.45 + interaction_score * 0.35 + recency_term * 0.20
        if row["is_known_incumbent"]:
            trend_score *= 0.55
            momentum_score *= 0.72
            interaction_score *= 0.72
        row["interaction_score"] = round(_clamp(interaction_score, 0.0, 100.0), 2)
        row["momentum_score"] = round(_clamp(momentum_score, 0.0, 100.0), 2)
        row["trend_score"] = round(_clamp(trend_score, 0.0, 100.0), 2)

    profile_id_by_key: dict[str, int] = {}
    stamp = now_iso()
    with get_conn() as conn:
        for row in profiles:
            conn.execute(
                """
                INSERT INTO entity_profiles(
                  entity_key, display_name, first_seen_at, last_seen_at, confidence, trend_score,
                  mention_count_1h, mention_count_24h, activity_last_30d, sources_json, source_counts_json,
                  top_keywords_json, quality_signals_json, node_count, is_known_incumbent, momentum_score,
                  created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_key) DO UPDATE SET
                  display_name=excluded.display_name,
                  first_seen_at=excluded.first_seen_at,
                  last_seen_at=excluded.last_seen_at,
                  confidence=excluded.confidence,
                  trend_score=excluded.trend_score,
                  mention_count_1h=excluded.mention_count_1h,
                  mention_count_24h=excluded.mention_count_24h,
                  activity_last_30d=excluded.activity_last_30d,
                  sources_json=excluded.sources_json,
                  source_counts_json=excluded.source_counts_json,
                  top_keywords_json=excluded.top_keywords_json,
                  quality_signals_json=excluded.quality_signals_json,
                  node_count=excluded.node_count,
                  is_known_incumbent=excluded.is_known_incumbent,
                  momentum_score=excluded.momentum_score,
                  updated_at=excluded.updated_at
                """,
                (
                    row["entity_key"],
                    row["display_name"],
                    row["first_seen_at"],
                    row["last_seen_at"],
                    row["confidence"],
                    row["trend_score"],
                    row["mention_count_1h"],
                    row["mention_count_24h"],
                    row["activity_last_30d"],
                    json_dumps(row["sources"]),
                    json_dumps(row["source_counts"]),
                    json_dumps(row["top_keywords"]),
                    json_dumps(row["quality_signals"]),
                    row["node_count"],
                    row["is_known_incumbent"],
                    row["momentum_score"],
                    stamp,
                    stamp,
                ),
            )

        valid_keys = [row["entity_key"] for row in profiles]
        if valid_keys:
            placeholders = ",".join("?" for _ in valid_keys)
            conn.execute(f"DELETE FROM entity_profiles WHERE entity_key NOT IN ({placeholders})", valid_keys)
        else:
            conn.execute("DELETE FROM entity_profiles")

        id_rows = conn.execute("SELECT id, entity_key FROM entity_profiles").fetchall()
        profile_id_by_key = {str(row["entity_key"]): int(row["id"]) for row in id_rows}

        conn.execute("DELETE FROM entity_aliases")
        conn.execute("DELETE FROM entity_daily_metrics")
        conn.execute("UPDATE entity_nodes SET entity_id=NULL")

        for alias_key, entity_key in alias_to_entity_key.items():
            profile_id = profile_id_by_key.get(entity_key)
            if not profile_id:
                continue
            alias_names = sorted(candidates.get(alias_key, {}).get("names") or [])
            alias_name = alias_names[0] if alias_names else alias_key
            reason = "canonical" if normalize_key(alias_name) == entity_key else "merged"
            conn.execute(
                """
                INSERT INTO entity_aliases(alias_key, alias_name, entity_id, confidence, reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (alias_key, alias_name, profile_id, 1.0 if reason == "canonical" else 0.85, reason, stamp, stamp),
            )
            conn.execute(
                "UPDATE entity_nodes SET entity_id=?, updated_at=? WHERE alias_key=?",
                (profile_id, stamp, alias_key),
            )

        for row in profiles:
            profile_id = profile_id_by_key.get(row["entity_key"])
            if not profile_id:
                continue
            day_map = daily_metrics_map.get(row["entity_key"]) or {}
            for date_key, bucket in day_map.items():
                mention_count = int(bucket.get("mention_count") or 0)
                impressions = int(bucket.get("impressions") or 0)
                source_counts = bucket.get("source_counts") or Counter()
                if not isinstance(source_counts, Counter):
                    source_counts = Counter(source_counts)
                diversity = len(source_counts)
                activity_score = round(
                    mention_count * 1.0 + math.log1p(max(0, impressions)) * 0.25 + diversity * 0.8,
                    3,
                )
                trend_score = round(_clamp(activity_score * 4.2, 0.0, 100.0), 3)
                conn.execute(
                    """
                    INSERT INTO entity_daily_metrics(
                      entity_id, date, mention_count, impressions, trend_score, source_counts_json,
                      activity_score, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(entity_id, date) DO UPDATE SET
                      mention_count=excluded.mention_count,
                      impressions=excluded.impressions,
                      trend_score=excluded.trend_score,
                      source_counts_json=excluded.source_counts_json,
                      activity_score=excluded.activity_score,
                      updated_at=excluded.updated_at
                    """,
                    (
                        profile_id,
                        date_key,
                        mention_count,
                        impressions,
                        trend_score,
                        json_dumps(dict(source_counts)),
                        activity_score,
                        stamp,
                        stamp,
                    ),
                )

    return {
        "entity_count": len(profiles),
        "alias_count": len(alias_to_entity_key),
        "merge_edges": merge_edges,
        "incumbent_count": sum(int(row["is_known_incumbent"]) for row in profiles),
        "deduped_mentions": deduped_mentions,
    }


__all__ = ["rebuild_canonical_entities", "_merge_score"]
