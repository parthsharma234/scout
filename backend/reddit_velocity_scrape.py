#!/usr/bin/env python3
"""
Reddit JSON listing scraper with velocity scoring.

Scrapes subreddit listings via the public .json endpoint (no auth), paginates using `after`,
and scores posts by engagement velocity to surface fast-rising company mentions.

Usage:
  python reddit_velocity_scrape.py --subreddit startups --limit 500 --out data/reddit_startups_velocity.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_URL = "https://www.reddit.com/r/{subreddit}.json"
USER_AGENT = "startup-signal-scraper/0.1"


def read_json(url: str, timeout_seconds: int) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
        return json.loads(body)


def normalize_domain(domain: str) -> str:
    domain = domain.lower().strip(".")
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def domain_to_name(domain: str) -> str:
    core = normalize_domain(domain)
    if not core:
        return ""
    core = core.split(":")[0].split("@")[-1]
    parts = core.split(".")
    if len(parts) >= 2:
        core = parts[-2]
    return core.replace("-", " ").title()


def extract_domains(text: str) -> set[str]:
    if not text:
        return set()
    domains = set()
    for match in re.findall(r"https?://([^/\s]+)", text):
        domains.add(normalize_domain(match))
    return domains


def extract_pattern_names(text: str) -> set[str]:
    if not text:
        return set()
    patterns = [
        r"\bwe (?:built|launched|shipping|shipped|released|made)\s+([A-Z][A-Za-z0-9][A-Za-z0-9\- ]{0,40})",
        r"\bintroducing\s+([A-Z][A-Za-z0-9][A-Za-z0-9\- ]{0,40})",
        r"\blaunching\s+([A-Z][A-Za-z0-9][A-Za-z0-9\- ]{0,40})",
        r"\bannounce(?:d|ment)?\s+([A-Z][A-Za-z0-9][A-Za-z0-9\- ]{0,40})",
        r"\bmeet\s+([A-Z][A-Za-z0-9][A-Za-z0-9\- ]{0,40})",
        r"\b([A-Z][A-Za-z0-9][A-Za-z0-9\- ]{0,40})\s+is live\b",
        r"\b([A-Z][A-Za-z0-9][A-Za-z0-9\- ]{0,40})\s+just launched\b",
    ]
    results = set()
    for pattern in patterns:
        for match in re.findall(pattern, text):
            if isinstance(match, tuple):
                match = match[0]
            candidate = match.strip()
            if 2 <= len(candidate) <= 50:
                results.add(candidate)
    return results


def extract_company_candidates(text: str, url: str | None) -> list[str]:
    candidates = set()
    combined_text = text or ""
    if url:
        combined_text = combined_text + "\n" + url

    # Pattern-based names
    candidates.update(extract_pattern_names(combined_text))

    # Capitalized phrases (short)
    candidates.update(
        re.findall(r"\b[A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+){0,2}\b", combined_text)
    )

    # Domains + derived names
    domains = extract_domains(combined_text)
    for domain in domains:
        candidates.add(domain)
        derived = domain_to_name(domain)
        if derived:
            candidates.add(derived)

    # Filter obvious noise tokens
    noise = {"I", "We", "A", "The", "My", "Our", "Startup", "Startups"}
    cleaned = [c for c in candidates if c and c not in noise]
    return sorted(set(cleaned))


def velocity_score(score: int, num_comments: int, created_utc: float) -> float:
    hours_since = max((time.time() - created_utc) / 3600.0, 1.0)
    return (score + 2 * num_comments) / hours_since


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape subreddit listing JSON and rank posts by engagement velocity."
    )
    parser.add_argument(
        "--subreddit",
        default="startups",
        help="Subreddit name without r/ prefix.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum number of posts to collect.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/reddit_velocity.jsonl"),
        help="Output JSONL file.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.5,
        help="Delay between paginated requests.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=20,
        help="HTTP timeout in seconds.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    collected: list[dict[str, Any]] = []
    after: str | None = None

    while len(collected) < args.limit:
        params = {"limit": min(100, args.limit - len(collected))}
        if after:
            params["after"] = after
        url = f"{BASE_URL.format(subreddit=args.subreddit)}?{urllib.parse.urlencode(params)}"

        try:
            payload = read_json(url, args.timeout_seconds)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Reddit error {e.code}: {body}") from e

        children = payload.get("data", {}).get("children", [])
        if not children:
            break

        for child in children:
            if child.get("kind") != "t3":
                continue
            data = child.get("data", {})
            created_utc = float(data.get("created_utc") or 0)
            post_score = int(data.get("score") or 0)
            num_comments = int(data.get("num_comments") or 0)
            velocity = velocity_score(post_score, num_comments, created_utc)

            text = "\n".join([data.get("title") or "", data.get("selftext") or ""])
            candidates = extract_company_candidates(text, data.get("url"))

            collected.append(
                {
                    "id": data.get("id"),
                    "name": data.get("name"),
                    "title": data.get("title"),
                    "selftext": data.get("selftext"),
                    "url": data.get("url"),
                    "permalink": data.get("permalink"),
                    "author": data.get("author"),
                    "created_utc": created_utc,
                    "created_at": datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat(),
                    "score": post_score,
                    "num_comments": num_comments,
                    "upvote_ratio": data.get("upvote_ratio"),
                    "velocity": round(velocity, 4),
                    "company_candidates": candidates,
                    "scraped_at_utc": int(time.time()),
                }
            )

            if len(collected) >= args.limit:
                break

        after = payload.get("data", {}).get("after")
        if not after:
            break

        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for row in collected:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Saved {len(collected)} posts to {args.out}")


if __name__ == "__main__":
    main()
