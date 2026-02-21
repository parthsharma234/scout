#!/usr/bin/env python3
"""
GitHub repo scraper for startup-signal discovery.

Usage:
  python github_scrape.py --limit-per-query 50 --out data/github_repos.jsonl
  python github_scrape.py --incremental
  python github_scrape.py --skip-release-lookup

Environment variables required:
- GHUB (preferred) or GITHUB_TOKEN (fine-grained or classic PAT with public repo read)
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

API_BASE = "https://api.github.com"
SEARCH_REPOS_PATH = "/search/repositories"
RELEASE_LATEST_PATH = "/repos/{owner}/{repo}/releases/latest"

DEFAULT_QUERIES = [
    "topic:startup",
    "topic:saas",
    "topic:indie-hacker",
    "topic:side-project",
    "topic:productivity",
    "topic:ai",
    "topic:llm",
    "topic:agent",
    "topic:devtools",
    "topic:fintech",
    "topic:healthtech",
    "topic:edtech",
    "topic:climate-tech",
    "topic:infra",
    "topic:observability",
    "topic:security",
    "topic:automation",
    "topic:opensource",
    "topic:sdk",
    "topic:api",
    "topic:cli",
    "topic:no-code",
    "topic:low-code",
    "topic:marketplace",
    "topic:payments",
    "in:name,description \"launch\"",
    "in:name,description \"beta\"",
    "in:name,description \"waitlist\"",
    "in:name,description \"early access\"",
    "in:name,description \"mvp\"",
    "in:name,description \"v1\"",
]


def get_env(name: str, required: bool = True) -> str:
    value = os.getenv(name, "").strip()
    if required and not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def read_json_response(
    req: urllib.request.Request,
    timeout_seconds: int,
    retries: int = 3,
    backoff_seconds: float = 1.0,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8")
                headers = {k.lower(): v for k, v in response.headers.items()}
                return response.status, json.loads(body), headers
        except urllib.error.URLError as e:
            # Let HTTP errors bubble up so callers can handle 404/422, etc.
            if isinstance(e, urllib.error.HTTPError):
                raise
            if attempt >= retries:
                raise RuntimeError(
                    "Network/DNS error while calling GitHub. "
                    "Check internet connection, VPN/proxy, and DNS settings."
                ) from e
            time.sleep(backoff_seconds * (2 ** attempt))


def parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def days_since(value: str | None) -> float | None:
    parsed = parse_iso8601(value)
    if not parsed:
        return None
    delta = datetime.now(timezone.utc) - parsed
    return max(delta.total_seconds() / 86400.0, 0.0)


def likely_company(owner_type: str | None, repo: dict[str, Any]) -> bool:
    if owner_type == "Organization":
        return True
    name = (repo.get("name") or "").lower()
    description = (repo.get("description") or "").lower()
    topics = [t.lower() for t in (repo.get("topics") or [])]
    keywords = ["official", "company", "inc", "llc", "corp", "gmbh", "ltd", "ventures"]
    for keyword in keywords:
        if keyword in name or keyword in description:
            return True
        if keyword in topics:
            return True
    return False


def extract_repo(repo: dict[str, Any], query: str, release_info: dict[str, Any] | None, release_within_days: int) -> dict[str, Any]:
    owner = repo.get("owner", {})
    created_days = days_since(repo.get("created_at"))
    stargazers = repo.get("stargazers_count") or 0
    star_velocity = None
    if created_days is not None and created_days > 0:
        star_velocity = round(stargazers / created_days, 4)

    latest_release_tag = None
    latest_release_published_at = None
    has_recent_release = False
    if release_info:
        latest_release_tag = release_info.get("tag_name")
        latest_release_published_at = release_info.get("published_at")
        published_days = days_since(latest_release_published_at)
        if published_days is not None and published_days <= release_within_days:
            has_recent_release = True

    return {
        "id": repo.get("id"),
        "name": repo.get("name"),
        "full_name": repo.get("full_name"),
        "html_url": repo.get("html_url"),
        "description": repo.get("description"),
        "created_at": repo.get("created_at"),
        "updated_at": repo.get("updated_at"),
        "pushed_at": repo.get("pushed_at"),
        "language": repo.get("language"),
        "topics": repo.get("topics"),
        "stargazers_count": repo.get("stargazers_count"),
        "star_velocity_per_day": star_velocity,
        "forks_count": repo.get("forks_count"),
        "open_issues_count": repo.get("open_issues_count"),
        "watchers_count": repo.get("watchers_count"),
        "license": (repo.get("license") or {}).get("spdx_id"),
        "is_fork": repo.get("fork"),
        "is_archived": repo.get("archived"),
        "is_private": repo.get("private"),
        "owner_login": owner.get("login"),
        "owner_type": owner.get("type"),
        "likely_company_repo": likely_company(owner.get("type"), repo),
        "latest_release_tag": latest_release_tag,
        "latest_release_published_at": latest_release_published_at,
        "has_recent_release": has_recent_release,
        "query": query,
        "scraped_at_utc": int(time.time()),
    }


def iso_date_days_ago(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%d")


def load_incremental_state(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload.get("last_run_date")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def save_incremental_state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"last_run_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def fetch_query_repos(
    token: str,
    query: str,
    limit: int,
    sleep_seconds: float,
    timeout_seconds: int,
    sort: str,
    order: str,
) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    page = 1

    while len(collected) < limit:
        per_page = min(100, limit - len(collected))
        params: dict[str, Any] = {
            "q": query,
            "sort": sort,
            "order": order,
            "per_page": per_page,
            "page": page,
        }

        url = f"{API_BASE}{SEARCH_REPOS_PATH}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "startup-signal-scraper/0.1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

        try:
            _, payload, headers = read_json_response(req, timeout_seconds)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 403 and "rate limit" in body.lower():
                reset = (e.headers or {}).get("X-RateLimit-Reset")
                if reset:
                    wait = max(5, int(reset) - int(time.time()) + 1)
                    time.sleep(wait)
                    continue
            raise RuntimeError(f"GitHub API error {e.code}: {body}") from e

        items = payload.get("items", [])
        if not items:
            break

        for repo in items:
            collected.append(repo)
            if len(collected) >= limit:
                break

        # GitHub Search API caps at 1000 results; stop when exhausted.
        if len(items) < per_page:
            break

        page += 1
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return collected


def fetch_latest_release(
    token: str,
    owner: str,
    repo: str,
    timeout_seconds: int,
) -> dict[str, Any] | None:
    url = f"{API_BASE}{RELEASE_LATEST_PATH.format(owner=owner, repo=repo)}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "startup-signal-scraper/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        _, payload, _ = read_json_response(req, timeout_seconds)
        return payload
    except urllib.error.HTTPError as e:
        if e.code in (404, 422):
            return None
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API error {e.code}: {body}") from e


def save_output(records: list[dict[str, Any]], output_path: Path, as_jsonl: bool) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if as_jsonl:
        with output_path.open("w", encoding="utf-8") as f:
            for row in records:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    else:
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape startup-related GitHub repositories via search API."
    )
    parser.add_argument(
        "--queries",
        nargs="+",
        default=DEFAULT_QUERIES,
        help="Search queries to run. Wrap each query in quotes.",
    )
    parser.add_argument(
        "--limit-per-query",
        type=int,
        default=50,
        help="Maximum repos to collect per query.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/github_repos.jsonl"),
        help="Output file path.",
    )
    parser.add_argument(
        "--json-array",
        action="store_true",
        help="Write output as a JSON array instead of JSONL.",
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
    parser.add_argument(
        "--sort",
        choices=["stars", "updated", "forks", "help-wanted-issues"],
        default="updated",
        help="Sort order for results.",
    )
    parser.add_argument(
        "--order",
        choices=["desc", "asc"],
        default="desc",
        help="Sort direction.",
    )
    parser.add_argument(
        "--created-within-days",
        type=int,
        default=14,
        help="Add created:>YYYY-MM-DD filter based on days back.",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Only query repos created after the last run date.",
    )
    parser.add_argument(
        "--incremental-state",
        type=Path,
        default=Path("data/github_incremental.json"),
        help="Path to store last run date for incremental mode.",
    )
    parser.add_argument(
        "--release-within-days",
        type=int,
        default=30,
        help="Flag repos with releases published within this many days.",
    )
    parser.add_argument(
        "--skip-release-lookup",
        action="store_true",
        help="Skip per-repo release lookup to reduce API calls.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = get_env("GHUB", required=False) or get_env("GITHUB_TOKEN", required=True)

    created_filter = ""
    if args.incremental:
        last_run = load_incremental_state(args.incremental_state)
        if last_run:
            created_filter = f" created:>{last_run}"
        elif args.created_within_days and args.created_within_days > 0:
            created_filter = f" created:>{iso_date_days_ago(args.created_within_days)}"
    elif args.created_within_days and args.created_within_days > 0:
        created_filter = f" created:>{iso_date_days_ago(args.created_within_days)}"

    all_records: list[dict[str, Any]] = []

    for query in args.queries:
        full_query = f"{query}{created_filter}"
        repos = fetch_query_repos(
            token=token,
            query=full_query,
            limit=args.limit_per_query,
            sleep_seconds=args.sleep_seconds,
            timeout_seconds=args.timeout_seconds,
            sort=args.sort,
            order=args.order,
        )
        print(f"query={full_query!r}: fetched {len(repos)} repos")

        for repo in repos:
            owner_login = (repo.get("owner") or {}).get("login")
            repo_name = repo.get("name")
            release_info = None
            if not args.skip_release_lookup and owner_login and repo_name:
                release_info = fetch_latest_release(
                    token=token,
                    owner=owner_login,
                    repo=repo_name,
                    timeout_seconds=args.timeout_seconds,
                )
                if args.sleep_seconds > 0:
                    time.sleep(args.sleep_seconds)

            all_records.append(
                extract_repo(repo, full_query, release_info, args.release_within_days)
            )

    save_output(all_records, args.out, as_jsonl=not args.json_array)
    print(f"Saved {len(all_records)} repos to {args.out}")
    if args.incremental:
        save_incremental_state(args.incremental_state)


if __name__ == "__main__":
    main()
