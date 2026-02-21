#!/usr/bin/env python3
"""
Match GitHub repos to Product Hunt posts.

Reads GitHub repo records (JSONL), fetches recent Product Hunt posts,
then matches by normalized name overlap.

Usage:
  python producthunt_match_github.py --in data/github_repos.jsonl --out data/github_ph_matches.jsonl

Environment variables required:
- PHUNT (preferred) or PRODUCTHUNT_DEVELOPER_TOKEN
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

API_ENDPOINT = "https://api.producthunt.com/v2/api/graphql"

QUERY_POSTS = """
query Posts($first: Int!, $after: String, $order: PostsOrder) {
  posts(first: $first, after: $after, order: $order) {
    edges {
      node {
        id
        name
        tagline
        description
        url
        createdAt
        votesCount
        commentsCount
        reviewsRating
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""


def get_env(name: str, required: bool = True) -> str:
    value = os.getenv(name, "").strip()
    if required and not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def load_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def save_jsonl(records: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for row in records:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def print_matched_company_titles(records: list[dict[str, Any]]) -> None:
    titles: set[str] = set()
    for record in records:
        product = record.get("product_hunt_post") or {}
        name = product.get("name")
        if isinstance(name, str) and name.strip():
            titles.add(name.strip())
    for title in sorted(titles):
        print(title)


def extract_candidates(repo: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("name", "owner_login", "full_name"):
        raw = repo.get(key)
        if raw and isinstance(raw, str) and raw not in candidates:
            candidates.append(raw)
    return candidates


def score_match(candidate: str, product: dict[str, Any]) -> float:
    cand_n = normalize_name(candidate)
    name_n = normalize_name(product.get("name") or "")
    if not cand_n or not name_n:
        return 0.0
    if cand_n == name_n:
        return 1.0
    if cand_n in name_n or name_n in cand_n:
        return 0.7
    return 0.0


def graphql_request(token: str, query: str, variables: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        API_ENDPOINT,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "startup-signal-scraper/0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Product Hunt API error {e.code}: {body}") from e


def fetch_posts(
    token: str,
    limit_posts: int,
    order: str,
    sleep_seconds: float,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []
    after: str | None = None

    while len(posts) < limit_posts:
        first = min(20, limit_posts - len(posts))
        variables = {"first": first, "after": after, "order": order}
        payload = graphql_request(token, QUERY_POSTS, variables, timeout_seconds)
        if payload.get("errors"):
            raise RuntimeError(f"Product Hunt API returned errors: {payload['errors']}")

        edges = (payload.get("data", {}) or {}).get("posts", {}).get("edges", [])
        for edge in edges:
            node = edge.get("node") or {}
            posts.append(node)

        page_info = (payload.get("data", {}) or {}).get("posts", {}).get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
        if not after:
            break

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return posts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Match GitHub repos to Product Hunt posts via GraphQL API."
    )
    parser.add_argument(
        "--in",
        dest="input_path",
        type=Path,
        default=Path("data/github_repos.jsonl"),
        help="Input GitHub JSONL file.",
    )
    parser.add_argument(
        "--out",
        dest="output_path",
        type=Path,
        default=Path("data/github_ph_matches.jsonl"),
        help="Output JSONL file for matches.",
    )
    parser.add_argument(
        "--limit-repos",
        type=int,
        default=0,
        help="Limit number of repos processed (0 = all).",
    )
    parser.add_argument(
        "--limit-posts",
        type=int,
        default=200,
        help="Number of Product Hunt posts to fetch.",
    )
    parser.add_argument(
        "--order",
        choices=["RANKING", "VOTES", "NEWEST"],
        default="VOTES",
        help="Order of Product Hunt posts.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.2,
        help="Delay between API calls.",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.7,
        help="Minimum match score to include.",
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
    token = get_env("PHUNT", required=False) or get_env("PRODUCTHUNT_DEVELOPER_TOKEN", required=True)

    repos = load_jsonl(args.input_path)
    posts = fetch_posts(
        token=token,
        limit_posts=args.limit_posts,
        order=args.order,
        sleep_seconds=args.sleep_seconds,
        timeout_seconds=args.timeout_seconds,
    )

    matches: list[dict[str, Any]] = []
    processed = 0

    for repo in repos:
        if args.limit_repos and processed >= args.limit_repos:
            break
        processed += 1

        candidates = extract_candidates(repo)
        for candidate in candidates:
            for post in posts:
                score = score_match(candidate, post)
                if score < args.min_score:
                    continue
                matches.append(
                    {
                        "github_repo": {
                            "id": repo.get("id"),
                            "name": repo.get("name"),
                            "full_name": repo.get("full_name"),
                            "html_url": repo.get("html_url"),
                            "owner_login": repo.get("owner_login"),
                            "description": repo.get("description"),
                        },
                        "product_hunt_post": {
                            "id": post.get("id"),
                            "name": post.get("name"),
                            "tagline": post.get("tagline"),
                            "description": post.get("description"),
                            "url": post.get("url"),
                            "created_at": post.get("createdAt"),
                            "votes_count": post.get("votesCount"),
                            "comments_count": post.get("commentsCount"),
                            "reviews_rating": post.get("reviewsRating"),
                        },
                        "match": {
                            "candidate": candidate,
                            "score": score,
                        },
                        "scraped_at_utc": int(time.time()),
                    }
                )

    save_jsonl(matches, args.output_path)
    print(f"Saved {len(matches)} matches to {args.output_path}")
    print_matched_company_titles(matches)


if __name__ == "__main__":
    main()
