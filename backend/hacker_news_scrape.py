"""
Hacker News Scraper → Supabase
Scrapes stories, jobs, polls AND full comment threads.

Tables:
  hn_stories  — top-level items (story, job, poll)
  hn_comments — all comments, linked to their root story and immediate parent

HN Firebase API endpoints used:
  /topstories, /newstories, /beststories
  /askstories, /showstories, /jobstories
  /updates   — changed items (efficient polling)
  /maxitem   — historical backfill
  /item/<id> — individual item fetch
"""

import os
import time
import logging
import requests
from collections import deque
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

HN_BASE = "https://hacker-news.firebaseio.com/v0"

# ── Keyword taxonomy ───────────────────────────────────────────────────────────
# Applied to stories AND comments — comments often contain the richest signal.
KEYWORD_TAXONOMY: dict[str, list[str]] = {
    # Story format
    "show_hn":      ["show hn"],
    "ask_hn":       ["ask hn"],
    "tell_hn":      ["tell hn"],
    # Builder signals
    "launch":       ["just launched", "we launched", "i launched", "launch"],
    "built_by_dev": ["i built", "we built", "i made", "i created", "i wrote"],
    "side_project": ["side project", "side hustle", "weekend project", "nights and weekends"],
    "indie":        ["indie", "indiehacker", "indie hacker", "bootstrapped", "solopreneur", "solo founder"],
    "mvp":          ["mvp", "prototype", "v0.", "alpha", "beta", "early access"],
    "open_source":  ["open source", "open-source", "oss", "github", "self-hosted", "selfhosted"],
    # Business model
    "saas":         ["saas", "subscription", "b2b", "b2c", "recurring revenue", "mrr", "arr"],
    "marketplace":  ["marketplace", "two-sided", "network effect"],
    "api_product":  ["api", "sdk", "developer tool", "devtool", "library", "package"],
    "no_code":      ["no-code", "nocode", "low-code", "lowcode", "without code"],
    # Tech domains
    "ai_ml":        ["ai", "llm", "gpt", "machine learning", "ml", "neural", "embedding", "vector", "rag", "fine-tun"],
    "agents":       ["agent", "agentic", "autonomous", "multi-agent", "ai agent"],
    "automation":   ["automation", "automate", "workflow", "zapier", "n8n", "make.com", "integrat"],
    "devtools":     ["devtools", "developer experience", "dx", "ide", "terminal", "cli", "shell", "editor"],
    "infra":        ["infrastructure", "cloud", "kubernetes", "docker", "serverless", "edge", "cdn", "database"],
    "security":     ["security", "cybersecurity", "privacy", "encryption", "auth", "zero trust", "soc2"],
    "data":         ["data pipeline", "etl", "analytics", "warehouse", "lakehouse", "dbt", "spark"],
    # Verticals
    "fintech":      ["fintech", "payments", "banking", "crypto", "defi", "web3", "blockchain", "neobank"],
    "healthtech":   ["health", "medtech", "medical", "healthcare", "mental health", "wellness", "biotech"],
    "edtech":       ["edtech", "education", "learning", "course", "tutoring", "skill"],
    "climate":      ["climate", "sustainability", "green", "carbon", "clean energy", "solar", "ev"],
    "proptech":     ["proptech", "real estate", "housing", "rent", "mortgage"],
    "legaltech":    ["legaltech", "legal", "law", "compliance", "contract"],
    # Community signals
    "alternative":  ["alternative to", "open alternative", "replace", "instead of"],
    "hiring":       ["hiring", "we're hiring", "join our team", "looking for co"],
    "funding":      ["raised", "seed round", "series a", "series b", "vc backed", "yc", "y combinator"],
    "acquisition":  ["acquired", "acquisition", "exit", "sold to"],
}


# ── Supabase ───────────────────────────────────────────────────────────────────

def get_supabase() -> Client:
    return create_client(
        os.environ["DB_URL"],
        os.environ["SECRET_KEY"],
    )


# ── HN API helpers ─────────────────────────────────────────────────────────────

def fetch(url: str, retries: int = 3) -> Optional[dict | list | int]:
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            logger.warning(f"Fetch failed [{attempt+1}/{retries}] {url} — {e}")
            time.sleep(2 ** attempt)
    return None


def get_max_item_id() -> int:
    result = fetch(f"{HN_BASE}/maxitem.json")
    return int(result) if result else 0


def get_feed(feed: str, limit: int = 500) -> list[int]:
    data = fetch(f"{HN_BASE}/{feed}.json")
    return (data or [])[:limit]


def get_updates() -> tuple[list[int], list[str]]:
    data = fetch(f"{HN_BASE}/updates.json")
    if not data:
        return [], []
    return data.get("items", []), data.get("profiles", [])


def get_item(item_id: int) -> Optional[dict]:
    return fetch(f"{HN_BASE}/item/{item_id}.json")


def is_valid_story(item: Optional[dict]) -> bool:
    if not item or item.get("deleted") or item.get("dead"):
        return False
    return item.get("type") in {"story", "job", "poll"}


def is_valid_comment(item: Optional[dict]) -> bool:
    if not item or item.get("deleted") or item.get("dead"):
        return False
    return item.get("type") == "comment"


# ── Categorisation ─────────────────────────────────────────────────────────────

def _haystack(item: dict) -> str:
    return " ".join([
        item.get("title") or "",
        item.get("text")  or "",
        item.get("url")   or "",
    ]).lower()


def extract_tags(item: dict) -> list[str]:
    h = _haystack(item)
    return [cat for cat, kws in KEYWORD_TAXONOMY.items() if any(kw in h for kw in kws)]


def extract_matched_keywords(item: dict) -> dict[str, list[str]]:
    h = _haystack(item)
    return {
        cat: [kw for kw in kws if kw in h]
        for cat, kws in KEYWORD_TAXONOMY.items()
        if any(kw in h for kw in kws)
    }


# ── Comment tree fetching ──────────────────────────────────────────────────────

def fetch_comment_tree(
    story_id: int,
    root_kids: list[int],
    max_depth: int = 10,
    max_comments: int = 2000,
) -> list[dict]:
    """
    BFS traversal of the comment tree for a single story.

    Args:
        story_id:     The root story's HN ID (used to link every comment back).
        root_kids:    Top-level comment IDs from the story item.
        max_depth:    How many levels deep to traverse (default 10 — covers most threads).
        max_comments: Safety cap per story to avoid runaway fetches on massive threads.

    Returns:
        List of raw HN comment dicts, each enriched with `story_id` and `depth`.
    """
    if not root_kids:
        return []

    comments: list[dict] = []
    # Queue entries: (comment_id, parent_id, depth)
    queue: deque[tuple[int, int, int]] = deque(
        (kid, story_id, 1) for kid in root_kids
    )

    while queue and len(comments) < max_comments:
        comment_id, parent_id, depth = queue.popleft()

        if depth > max_depth:
            continue

        item = get_item(comment_id)
        if not is_valid_comment(item):
            continue

        # Enrich with relationship metadata before storing
        item["_story_id"]  = story_id
        item["_parent_id"] = parent_id
        item["_depth"]     = depth
        comments.append(item)

        # Queue this comment's children (one level deeper)
        for child_id in (item.get("kids") or []):
            if len(comments) < max_comments:
                queue.append((child_id, comment_id, depth + 1))

        time.sleep(0.02)  # gentle rate limiting per comment fetch

    return comments


# ── Supabase upserts ───────────────────────────────────────────────────────────

def upsert_stories(supabase: Client, items: list[dict]) -> int:
    if not items:
        return 0

    rows = []
    for item in items:
        tags   = extract_tags(item)
        kw_map = extract_matched_keywords(item)
        rows.append({
            "hn_id":            item["id"],
            "item_type":        item.get("type"),
            "title":            item.get("title"),
            "url":              item.get("url"),
            "text":             item.get("text"),
            "score":            item.get("score", 0),
            "author":           item.get("by"),
            "descendants":      item.get("descendants", 0),
            "kids":             item.get("kids") or [],
            "tags":             tags,
            "matched_keywords": kw_map,
            "is_categorised":   len(tags) > 0,
            "hn_created_at":    datetime.fromtimestamp(
                                    item["time"], tz=timezone.utc
                                ).isoformat() if item.get("time") else None,
            "fetched_at":       datetime.now(timezone.utc).isoformat(),
        })

    resp  = supabase.table("hn_stories").upsert(rows, on_conflict="hn_id").execute()
    count = len(resp.data) if resp.data else 0
    logger.info(f"  → Stories upserted: {count} ({sum(1 for r in rows if r['is_categorised'])} categorised)")
    return count


def upsert_comments(supabase: Client, comments: list[dict]) -> int:
    """
    Upsert comments into hn_comments.
    Comments also get keyword-tagged so you can find signal in thread discussions.
    """
    if not comments:
        return 0

    BATCH = 500  # Supabase upsert batch limit
    total = 0

    for i in range(0, len(comments), BATCH):
        chunk = comments[i: i + BATCH]
        rows = []
        for c in chunk:
            tags   = extract_tags(c)
            kw_map = extract_matched_keywords(c)
            rows.append({
                "hn_id":            c["id"],
                "story_id":         c["_story_id"],    # root story
                "parent_id":        c["_parent_id"],   # immediate parent (story or comment)
                "depth":            c["_depth"],        # 1 = top-level, 2 = reply, etc.
                "text":             c.get("text"),
                "author":           c.get("by"),
                "kids":             c.get("kids") or [],
                "tags":             tags,
                "matched_keywords": kw_map,
                "is_categorised":   len(tags) > 0,
                "hn_created_at":    datetime.fromtimestamp(
                                        c["time"], tz=timezone.utc
                                    ).isoformat() if c.get("time") else None,
                "fetched_at":       datetime.now(timezone.utc).isoformat(),
            })

        resp   = supabase.table("hn_comments").upsert(rows, on_conflict="hn_id").execute()
        total += len(resp.data) if resp.data else 0

    logger.info(f"  → Comments upserted: {total}")
    return total


# ── Core fetch-and-upsert pipeline ────────────────────────────────────────────

def _fetch_and_upsert(
    supabase: Client,
    ids: list[int],
    batch_size: int,
    scrape_comments: bool = True,
    max_depth: int = 10,
    max_comments_per_story: int = 2000,
) -> None:
    """
    Fetch items by ID, upsert stories, then optionally traverse and upsert
    their full comment trees.
    """
    valid_stories: list[dict] = []
    total_batches = (len(ids) + batch_size - 1) // batch_size

    # ── 1. Fetch and upsert stories ───────────────────────────────────────────
    for i in range(0, len(ids), batch_size):
        batch = ids[i: i + batch_size]
        for sid in batch:
            item = get_item(sid)
            if is_valid_story(item):
                valid_stories.append(item)

        logger.info(
            f"Batch {i // batch_size + 1}/{total_batches} — "
            f"{len(valid_stories)} stories collected."
        )
        time.sleep(0.05)

    logger.info(f"Total valid stories: {len(valid_stories)}")
    upsert_stories(supabase, valid_stories)

    # ── 2. Fetch and upsert comments ──────────────────────────────────────────
    if not scrape_comments:
        return

    logger.info("Starting comment tree traversal...")
    all_comments: list[dict] = []

    for idx, story in enumerate(valid_stories):
        story_id  = story["id"]
        root_kids = story.get("kids") or []

        if not root_kids:
            continue

        logger.info(
            f"[{idx+1}/{len(valid_stories)}] Fetching comments for story "
            f"{story_id} ({len(root_kids)} top-level kids)..."
        )
        comments = fetch_comment_tree(
            story_id=story_id,
            root_kids=root_kids,
            max_depth=max_depth,
            max_comments=max_comments_per_story,
        )
        all_comments.extend(comments)

        # Upsert in rolling batches to avoid holding too much in memory
        if len(all_comments) >= 1000:
            upsert_comments(supabase, all_comments)
            all_comments = []

    # Flush remainder
    if all_comments:
        upsert_comments(supabase, all_comments)

    logger.info("Comment traversal complete.")


# ── Public scrape functions ────────────────────────────────────────────────────

def scrape_feeds(
    feeds: list[str] | None = None,
    limit: int = 500,
    batch_size: int = 50,
    scrape_comments: bool = True,
    max_depth: int = 10,
    max_comments_per_story: int = 2000,
) -> None:
    if feeds is None:
        feeds = ["topstories", "newstories", "beststories",
                 "askstories", "showstories", "jobstories"]

    supabase = get_supabase()
    logger.info(f"Scraping feeds: {feeds} | comments: {scrape_comments}")

    story_ids: set[int] = set()
    for feed in feeds:
        ids = get_feed(feed, limit)
        story_ids.update(ids)
        logger.info(f"  {feed}: {len(ids)} IDs (total: {len(story_ids)})")

    _fetch_and_upsert(supabase, list(story_ids), batch_size,
                      scrape_comments, max_depth, max_comments_per_story)


def scrape_updates(
    batch_size: int = 50,
    scrape_comments: bool = True,
    max_depth: int = 5,           # shallower for update cycles — speed over depth
    max_comments_per_story: int = 500,
) -> None:
    """
    Fetch only items changed since the last /v0/updates call.
    Comments are fetched at shallower depth by default for speed.
    """
    supabase = get_supabase()
    item_ids, profiles = get_updates()
    logger.info(f"/v0/updates: {len(item_ids)} changed items, {len(profiles)} profiles.")
    _fetch_and_upsert(supabase, item_ids, batch_size,
                      scrape_comments, max_depth, max_comments_per_story)


def scrape_historical(
    n_items: int = 1000,
    batch_size: int = 50,
    start_from: Optional[int] = None,
    scrape_comments: bool = False,   # off by default — historical runs are already large
    max_depth: int = 10,
    max_comments_per_story: int = 2000,
) -> None:
    supabase = get_supabase()
    max_id = start_from or get_max_item_id()
    ids = list(range(max_id, max(0, max_id - n_items), -1))
    logger.info(f"Historical backfill: {max_id} → {ids[-1]} ({len(ids)} items).")
    _fetch_and_upsert(supabase, ids, batch_size,
                      scrape_comments, max_depth, max_comments_per_story)


def scrape_comments_for_stored_stories(
    limit: int = 100,
    max_depth: int = 10,
    max_comments_per_story: int = 2000,
    only_uncrawled: bool = True,
) -> None:
    """
    Fetch comments for stories already in the DB.
    Useful for backfilling comments on stories scraped before comment support was added.

    Args:
        limit:           How many stories to process in this run.
        only_uncrawled:  If True, skip stories that already have comments in hn_comments.
    """
    supabase = get_supabase()

    if only_uncrawled:
        # Stories whose hn_id doesn't appear in hn_comments.story_id yet
        logger.info("Fetching stories with no comments yet...")
        resp = supabase.rpc("stories_without_comments", {"lim": limit}).execute()
        stories = resp.data or []
    else:
        resp = (
            supabase.table("hn_stories")
            .select("hn_id, kids")
            .order("score", desc=True)
            .limit(limit)
            .execute()
        )
        stories = resp.data or []

    logger.info(f"Fetching comments for {len(stories)} stories...")
    all_comments: list[dict] = []

    for idx, row in enumerate(stories):
        story_id  = row["hn_id"]
        root_kids = row.get("kids") or []

        if not root_kids:
            continue

        logger.info(
            f"[{idx+1}/{len(stories)}] Story {story_id} "
            f"({len(root_kids)} top-level kids)..."
        )
        comments = fetch_comment_tree(story_id, root_kids, max_depth, max_comments_per_story)
        all_comments.extend(comments)

        if len(all_comments) >= 1000:
            upsert_comments(supabase, all_comments)
            all_comments = []

    if all_comments:
        upsert_comments(supabase, all_comments)

    logger.info("Comment backfill complete.")


# ── Polling loop ───────────────────────────────────────────────────────────────

def run_polling(
    interval_seconds: int = 60,
    use_updates_endpoint: bool = True,
    scrape_comments: bool = True,
) -> None:
    logger.info(
        f"Polling — interval: {interval_seconds}s | "
        f"strategy: {'updates' if use_updates_endpoint else 'feeds'} | "
        f"comments: {scrape_comments}"
    )
    while True:
        try:
            if use_updates_endpoint:
                scrape_updates(scrape_comments=scrape_comments)
            else:
                scrape_feeds(
                    feeds=["topstories", "newstories", "askstories", "showstories"],
                    limit=200,
                    scrape_comments=scrape_comments,
                )
        except Exception as e:
            logger.error(f"Polling cycle error: {e}")
        logger.info(f"Sleeping {interval_seconds}s...")
        time.sleep(interval_seconds)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="HN → Supabase Scraper (stories + comments)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_comment_args(p):
        p.add_argument("--no-comments", action="store_true",
            help="Skip comment scraping (stories only).")
        p.add_argument("--max-depth", type=int, default=10,
            help="Max comment thread depth to traverse (default: 10).")
        p.add_argument("--max-comments", type=int, default=2000,
            help="Max comments to fetch per story (default: 2000).")

    # feeds
    p_feeds = subparsers.add_parser("feeds", help="Scrape named HN feeds + comments.")
    p_feeds.add_argument("--feeds", nargs="+",
        default=["topstories", "newstories", "beststories",
                 "askstories", "showstories", "jobstories"])
    p_feeds.add_argument("--limit", type=int, default=500)
    add_comment_args(p_feeds)

    # updates
    p_updates = subparsers.add_parser("updates", help="Scrape /v0/updates + comments.")
    add_comment_args(p_updates)

    # historical
    p_hist = subparsers.add_parser("historical", help="Backfill from maxitem backward.")
    p_hist.add_argument("--n", type=int, default=1000)
    p_hist.add_argument("--start-from", type=int, default=None)
    p_hist.add_argument("--with-comments", action="store_true",
        help="Also fetch comments (off by default for historical runs).")
    p_hist.add_argument("--max-depth", type=int, default=10)
    p_hist.add_argument("--max-comments", type=int, default=2000)

    # comments — backfill comments for already-stored stories
    p_comments = subparsers.add_parser("comments",
        help="Fetch comments for stories already stored in Supabase.")
    p_comments.add_argument("--limit", type=int, default=100,
        help="How many stories to process.")
    p_comments.add_argument("--all", action="store_true",
        help="Process all stories, even those with existing comments.")
    p_comments.add_argument("--max-depth", type=int, default=10)
    p_comments.add_argument("--max-comments", type=int, default=2000)

    # poll
    p_poll = subparsers.add_parser("poll", help="Continuous polling loop.")
    p_poll.add_argument("--interval", type=int, default=60)
    p_poll.add_argument("--strategy", choices=["updates", "feeds"], default="updates")
    p_poll.add_argument("--no-comments", action="store_true")

    args = parser.parse_args()

    if args.command == "feeds":
        scrape_feeds(
            feeds=args.feeds,
            limit=args.limit,
            scrape_comments=not args.no_comments,
            max_depth=args.max_depth,
            max_comments_per_story=args.max_comments,
        )
    elif args.command == "updates":
        scrape_updates(
            scrape_comments=not args.no_comments,
            max_depth=args.max_depth,
            max_comments_per_story=args.max_comments,
        )
    elif args.command == "historical":
        scrape_historical(
            n_items=args.n,
            start_from=args.start_from,
            scrape_comments=args.with_comments,
            max_depth=args.max_depth,
            max_comments_per_story=args.max_comments,
        )
    elif args.command == "comments":
        scrape_comments_for_stored_stories(
            limit=args.limit,
            only_uncrawled=not args.all,
            max_depth=args.max_depth,
            max_comments_per_story=args.max_comments,
        )
    elif args.command == "poll":
        run_polling(
            interval_seconds=args.interval,
            use_updates_endpoint=(args.strategy == "updates"),
            scrape_comments=not args.no_comments,
        )
