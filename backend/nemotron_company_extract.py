#!/usr/bin/env python3
"""
Extract company names from Reddit posts using NVIDIA Nemotron via OpenAI-compatible API.

Usage:
  python nemotron_company_extract.py --in data/reddit_startups_velocity.jsonl --out data/reddit_company_entities.jsonl

Environment variables required:
- NIM_API_KEY
- NIM_API_BASE_URL  (e.g. https://integrate.api.nvidia.com/v1)
- NIM_MODEL         (e.g. "nvidia/nemotron-3-nano-30b-a3b")
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

SYSTEM_PROMPT = (
    "You are a strict entity extractor. "
    "Return only valid JSON and nothing else."
)

USER_PROMPT_TEMPLATE = """
Extract company and product names from the following text and URL.
Rules:
- Only include real company/product names explicitly mentioned.
- If none, return empty arrays.
- Output format must be valid JSON with keys: companies, products, domains.

Text:
{content}

URL:
{url}
"""


def get_env(name: str, required: bool = True) -> str:
    value = os.getenv(name, "").strip()
    if required and not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


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


def call_nemotron(
    api_key: str,
    api_base: str,
    model: str,
    content: str,
    url: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 256,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_PROMPT_TEMPLATE.format(content=content, url=url),
            },
        ],
    }

    req = urllib.request.Request(
        f"{api_base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
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
        raise RuntimeError(f"Nemotron API error {e.code}: {body}") from e


def parse_json_from_response(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices", [])
    if not choices:
        return {"companies": [], "products": [], "domains": []}
    content = (choices[0].get("message") or {}).get("content") or ""
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"companies": [], "products": [], "domains": [], "raw": content}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract company names using Nemotron."
    )
    parser.add_argument(
        "--in",
        dest="input_path",
        type=Path,
        default=Path("data/reddit_startups_velocity.jsonl"),
        help="Input JSONL file (posts).",
    )
    parser.add_argument(
        "--out",
        dest="output_path",
        type=Path,
        default=Path("data/reddit_company_entities.jsonl"),
        help="Output JSONL file (entities).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of posts processed (0 = all).",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.2,
        help="Delay between API calls.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=30,
        help="HTTP timeout in seconds.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_key = get_env("NIM_API_KEY", required=True)
    api_base = get_env("NIM_API_BASE_URL", required=True)
    model = get_env("NIM_MODEL", required=True)

    records: list[dict[str, Any]] = []
    processed = 0

    for post in load_jsonl(args.input_path):
        if args.limit and processed >= args.limit:
            break
        processed += 1

        text = "\n".join([post.get("title") or "", post.get("selftext") or ""])
        url = post.get("url") or ""

        response = call_nemotron(
            api_key=api_key,
            api_base=api_base,
            model=model,
            content=text,
            url=url,
            timeout_seconds=args.timeout_seconds,
        )
        entities = parse_json_from_response(response)

        records.append(
            {
                "post_id": post.get("id"),
                "permalink": post.get("permalink"),
                "title": post.get("title"),
                "url": url,
                "entities": entities,
                "scraped_at_utc": int(time.time()),
            }
        )

        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    save_jsonl(records, args.output_path)
    print(f"Saved {len(records)} entity rows to {args.output_path}")


if __name__ == "__main__":
    main()
