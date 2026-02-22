#!/usr/bin/env python3
"""
Web enrichment using Google CSE + optional Nemotron reranking.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from config import get_google_cse_api_key, get_google_cse_cx, get_google_serper_key
    from db import get_conn, json_dumps, json_loads, now_iso
except ModuleNotFoundError:
    from backend.config import get_google_cse_api_key, get_google_cse_cx, get_google_serper_key  # type: ignore
    from backend.db import get_conn, json_dumps, json_loads, now_iso  # type: ignore


GOOGLE_CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
SERPER_ENDPOINT = "https://google.serper.dev/search"
DEFAULT_MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"


def _hash_url(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]  # noqa: S324


def _request_json(url: str, timeout_seconds: int = 20, method: str = "GET", data: bytes | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    final_headers = {
        "Accept": "application/json",
        "User-Agent": "scout-enrichment/0.1",
    }
    if headers:
        final_headers.update(headers)
    
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=final_headers,
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def google_cse_search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    api_key = get_google_cse_api_key()
    cx = get_google_cse_cx()
    if not api_key or not cx:
        return []
    params = urllib.parse.urlencode(
        {
            "key": api_key,
            "cx": cx,
            "q": query,
            "num": max(1, min(10, int(limit))),
            "safe": "off",
        }
    )
    url = f"{GOOGLE_CSE_ENDPOINT}?{params}"
    try:
        payload = _request_json(url)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return []
    items = payload.get("items") or []
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        if not link:
            continue
        out.append(
            {
                "url": link,
                "title": title,
                "snippet": snippet,
                "display_link": str(item.get("displayLink") or ""),
            }
        )
    return out


def serper_search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    api_key = get_google_serper_key()
    if not api_key:
        return []
    
    payload = json.dumps({
        "q": query,
        "num": max(1, min(20, int(limit))),
    }).encode("utf-8")
    
    try:
        resp = _request_json(
            SERPER_ENDPOINT,
            method="POST",
            data=payload,
            headers={
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
            }
        )
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return []
    
    organic = resp.get("organic") or []
    if not isinstance(organic, list):
        return []
    
    out: list[dict[str, Any]] = []
    for item in organic:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        if not link:
            continue
        out.append({
            "url": link,
            "title": title,
            "snippet": snippet,
            "display_link": _domain_root(link),
        })
    return out


def _domain_root(url: str) -> str:
    from urllib.parse import urlparse
    try:
        host = (urlparse(url).hostname or "").lower().strip()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _nemotron_select_links(entity_name: str, query: str, candidates: list[dict[str, Any]], max_links: int = 8) -> list[dict[str, Any]]:
# ... (rest of the file remains same, I will use replace_file_content carefully)
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return candidates[:max_links]

    model = os.getenv("OPENROUTER_MODEL", "").strip() or os.getenv("NEMOTRON_MODEL", "").strip() or DEFAULT_MODEL
    api_base = os.getenv("OPENROUTER_BASE_URL", "").strip() or "https://openrouter.ai/api/v1"
    reduced = candidates[:12]
    prompt = {
        "entity": entity_name,
        "query": query,
        "instructions": [
            "Pick links that are canonical and high-signal for startup/tool intelligence.",
            "Prefer official pages, docs, company posts, reputable launches/coverage.",
            "Add concise watchouts for each chosen link.",
            "Return strict JSON only.",
        ],
        "max_links": max_links,
        "candidates": reduced,
        "schema": {
            "results": [
                {
                    "url": "string",
                    "title": "string",
                    "score": "number 0-100",
                    "watchouts": ["string"],
                }
            ]
        },
    }
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 1400,
        "messages": [
            {
                "role": "system",
                "content": "You rank startup intelligence links. Return strict JSON only.",
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
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
            "X-Title": "Scout Link Enrichment",
            "User-Agent": "scout-enrichment/0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as response:
            raw = json.loads(response.read().decode("utf-8"))
        content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return candidates[:max_links]

    parsed: dict[str, Any] | None = None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\})", content, flags=re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
            except json.JSONDecodeError:
                parsed = None
    if not isinstance(parsed, dict):
        return candidates[:max_links]

    results = parsed.get("results") or []
    if not isinstance(results, list):
        return candidates[:max_links]

    by_url = {row["url"]: row for row in candidates if row.get("url")}
    selected: list[dict[str, Any]] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url or url not in by_url:
            continue
        source = by_url[url]
        selected.append(
            {
                "url": url,
                "title": str(row.get("title") or source.get("title") or ""),
                "snippet": str(source.get("snippet") or ""),
                "score": float(row.get("score") or 0.0),
                "watchouts": row.get("watchouts") if isinstance(row.get("watchouts"), list) else [],
            }
        )
        if len(selected) >= max_links:
            break
    return selected if selected else candidates[:max_links]


def _start_job(entity_id: int, provider: str, query: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO enrichment_jobs(entity_id, provider, query, status, started_at, meta_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entity_id, provider, query, "running", now_iso(), "{}"),
        )
        return int(cur.lastrowid or 0)


def _finish_job(job_id: int, status: str, links_found: int = 0, error: str = "", meta: dict[str, Any] | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE enrichment_jobs
            SET status=?, finished_at=?, links_found=?, error=?, meta_json=?
            WHERE id=?
            """,
            (status, now_iso(), int(links_found), error, json_dumps(meta or {}), int(job_id)),
        )


def enrich_entity(entity_key: str, force: bool = False, max_links: int = 8) -> dict[str, Any]:
    with get_conn() as conn:
        profile = conn.execute(
            """
            SELECT id, display_name, top_keywords_json, last_enriched_at
            FROM entity_profiles
            WHERE entity_key=?
            """,
            (entity_key,),
        ).fetchone()
    if not profile:
        return {"ok": False, "reason": "entity_not_found", "entity_key": entity_key, "links_added": 0}

    entity_id = int(profile["id"])
    display_name = str(profile["display_name"])
    top_keywords = json_loads(profile["top_keywords_json"], [])
    if not isinstance(top_keywords, list):
        top_keywords = []
    last_enriched_at = profile["last_enriched_at"]
    if last_enriched_at and not force:
        parsed = datetime.fromisoformat(str(last_enriched_at))
        if (datetime.now(timezone.utc) - parsed) < timedelta(days=7):
            return {"ok": True, "reason": "fresh_cache", "entity_key": entity_key, "links_added": 0}

    keyword_tail = " ".join(str(token) for token in top_keywords[:4] if str(token).strip())
    query = f"{display_name} startup tool {keyword_tail}".strip()

    serper_key = get_google_serper_key()
    provider = "serper" if serper_key else "google_cse"
    job_id = _start_job(entity_id=entity_id, provider=provider, query=query)
    try:
        if serper_key:
            candidates = serper_search(query=query, limit=max(10, max_links))
        else:
            candidates = google_cse_search(query=query, limit=max(10, max_links))

        if not candidates:
            _finish_job(job_id, "success", links_found=0, meta={"empty": True})
            return {"ok": True, "reason": "no_candidates", "entity_key": entity_key, "links_added": 0}

        selected = _nemotron_select_links(
            entity_name=display_name,
            query=query,
            candidates=candidates,
            max_links=max_links,
        )

        added = 0
        stamp = now_iso()
        with get_conn() as conn:
            for idx, row in enumerate(selected):
                url = str(row.get("url") or "").strip()
                if not url:
                    continue
                title = str(row.get("title") or "")
                snippet = str(row.get("snippet") or "")
                score = float(row.get("score") or max(0.0, 100.0 - idx * 8.0))
                watchouts = row.get("watchouts") if isinstance(row.get("watchouts"), list) else []
                conn.execute(
                    """
                    INSERT INTO enrichment_links(
                      entity_id, url, title, snippet, provider, score, watchouts_json, published_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(entity_id, url) DO UPDATE SET
                      title=excluded.title,
                      snippet=excluded.snippet,
                      score=excluded.score,
                      watchouts_json=excluded.watchouts_json,
                      published_at=excluded.published_at,
                      updated_at=excluded.updated_at
                    """,
                    (
                        entity_id,
                        url,
                        title,
                        snippet,
                        provider,
                        score,
                        json_dumps(watchouts),
                        stamp,
                        stamp,
                        stamp,
                    ),
                )
                node_id = f"web-{entity_key}-{_hash_url(url)}"
                conn.execute(
                    """
                    INSERT INTO entity_nodes(
                      node_id, entity_id, alias_key, entity_name, source_id, source_name,
                      headline, url, summary, interactions, views, impressions,
                      published_at, confidence, node_type, raw_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(node_id) DO UPDATE SET
                      entity_id=excluded.entity_id,
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
                        entity_id,
                        entity_key,
                        display_name,
                        f"web_{provider}",
                        "WEB",
                        title or display_name,
                        url,
                        snippet,
                        int(round(score)),
                        int(round(score * 20)),
                        int(round(score * 25)),
                        stamp,
                        min(1.0, max(0.0, score / 100.0)),
                        "source_enriched",
                        json_dumps(
                            {
                                "provider": provider,
                                "url": url,
                                "title": title,
                                "snippet": snippet,
                                "watchouts": watchouts,
                                "score": score,
                            }
                        ),
                        stamp,
                        stamp,
                    ),
                )
                added += 1
            conn.execute(
                "UPDATE entity_profiles SET last_enriched_at=?, updated_at=? WHERE id=?",
                (stamp, stamp, entity_id),
            )

        _finish_job(job_id, "success", links_found=added, meta={"query": query, "selected": len(selected)})
        return {"ok": True, "reason": "enriched", "entity_key": entity_key, "links_added": added}
    except Exception as exc:  # noqa: BLE001
        _finish_job(job_id, "failed", links_found=0, error=str(exc))
        return {"ok": False, "reason": str(exc), "entity_key": entity_key, "links_added": 0}


def enrich_top_entities(limit: int = 50, force: bool = False, max_links: int = 8) -> dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT entity_key
            FROM entity_profiles
            ORDER BY trend_score DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    results = [enrich_entity(str(row["entity_key"]), force=force, max_links=max_links) for row in rows]
    return {
        "total": len(results),
        "ok": sum(1 for row in results if row.get("ok")),
        "links_added": sum(int(row.get("links_added") or 0) for row in results),
        "results": results,
    }

