#!/usr/bin/env python3

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

USER_AGENT = "wikifetch/1.0 (https://github.com/)"
PAGEVIEWS_ENDPOINT = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia.org/all-access/user/{title}/daily/{start}/{end}"
)
REQUEST_DELAY_SECONDS = 0.08
MAX_RETRIES = 6


def fetch_json(url: str) -> dict[str, Any] | None:
    request = Request(url, headers={"User-Agent": USER_AGENT})

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            sleep(min(2**attempt, 20))
        try:
            with urlopen(request, timeout=25) as response:
                payload = json.loads(response.read().decode("utf-8"))
                sleep(REQUEST_DELAY_SECONDS)
                return payload
        except HTTPError as error:
            if error.code == 429 and attempt < MAX_RETRIES:
                retry_after = error.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    sleep(int(retry_after))
                continue
            if error.code == 404:
                return None
            print(f"Request failed for '{url}': HTTP {error.code}", file=sys.stderr)
            return None
        except URLError as error:
            if attempt < MAX_RETRIES:
                continue
            print(f"Request failed for '{url}': {error.reason}", file=sys.stderr)
            return None

    return None


def extract_wikipedia_title(post: dict[str, Any]) -> str:
    source = post.get("source", "")
    if not isinstance(source, str) or not source.strip():
        return ""
    parsed = urlparse(source)
    if "wikipedia.org" not in parsed.netloc:
        return ""
    slug = parsed.path.rsplit("/", maxsplit=1)[-1]
    if not slug:
        return ""
    return unquote(slug).replace(" ", "_")


def fetch_view_count(title: str, days: int) -> int:
    end_date = datetime.now(timezone.utc).date() - timedelta(days=1)
    start_date = end_date - timedelta(days=days - 1)
    start = f"{start_date.strftime('%Y%m%d')}00"
    end = f"{end_date.strftime('%Y%m%d')}00"
    encoded_title = quote(title, safe="")
    url = PAGEVIEWS_ENDPOINT.format(title=encoded_title, start=start, end=end)
    payload = fetch_json(url)
    if payload is None:
        return 0
    items = payload.get("items", [])
    if not isinstance(items, list):
        return 0
    return sum(int(item.get("views", 0)) for item in items if isinstance(item, dict))


def derive_like_count(view_count: int, key: str) -> int:
    if view_count <= 0:
        return 0
    # Keep likes deterministic while still varying naturally by post.
    seed = hashlib.sha256(key.encode("utf-8")).digest()[0]
    ratio = 0.02 + (seed / 255.0) * 0.08
    return max(1, int(round(view_count * ratio)))


def load_cache(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as cache_file:
        payload = json.load(cache_file)
    if not isinstance(payload, dict):
        return {}
    cache: dict[str, int] = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, int):
            cache[key] = value
    return cache


def save_cache(path: Path, cache: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as cache_file:
        json.dump(cache, cache_file, ensure_ascii=False, indent=2)
        cache_file.write("\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def sort_post_ids(post_ids: list[str]) -> list[str]:
    def sort_key(value: str) -> tuple[int, int | str]:
        try:
            return (0, int(value))
        except ValueError:
            return (1, value)

    return sorted(post_ids, key=sort_key)


def render_progress(current: int, total: int, updated_count: int, done: bool = False) -> None:
    if total < 1:
        return

    ratio = min(max(current / total, 0.0), 1.0)
    width = 30
    filled = int(width * ratio)
    bar = "#" * filled + "-" * (width - filled)
    message = (
        f"\r[{bar}] {current}/{total} "
        f"({ratio * 100:5.1f}%) updated={updated_count}"
    )

    if done:
        print("", file=sys.stderr)
        return
    print(message, end="", file=sys.stderr, flush=True)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    default_posts = repo_root / "docs" / "data" / "posts.json"
    default_cache = repo_root / "docs" / "data" / "wiki_popularity_cache.json"

    parser = argparse.ArgumentParser(
        description=(
            "Fetch Wikipedia pageviews for every post in posts.json and add "
            "'view_count' and 'like_count' fields."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=default_posts,
        help="Input posts.json file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_posts,
        help="Output posts.json file.",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=default_cache,
        help="Cache file for pageview lookups.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of past days to aggregate pageviews over.",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        help="Optional limit for number of posts to process.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Ignore cache and refetch all pageview counts.",
    )
    args = parser.parse_args()

    if args.days < 1:
        print("--days must be 1 or greater.", file=sys.stderr)
        return 1
    if args.max_posts is not None and args.max_posts < 1:
        print("--max-posts must be 1 or greater.", file=sys.stderr)
        return 1

    with args.input.open("r", encoding="utf-8") as input_file:
        payload = json.load(input_file)
    if not isinstance(payload, dict):
        print("posts.json must be a JSON object.", file=sys.stderr)
        return 1

    posts_by_id = payload.get("posts_by_id") or payload.get("articles_by_id")
    if not isinstance(posts_by_id, dict):
        print("posts.json missing 'posts_by_id' object.", file=sys.stderr)
        return 1

    cache = load_cache(args.cache)
    post_ids = sort_post_ids(list(posts_by_id.keys()))
    candidate_post_ids = (
        post_ids[: args.max_posts] if args.max_posts is not None else post_ids
    )
    total_candidates = len(candidate_post_ids)

    updated_count = 0
    for index, post_id in enumerate(candidate_post_ids, start=1):
        post = posts_by_id.get(post_id)
        if not isinstance(post, dict):
            render_progress(index, total_candidates, updated_count)
            continue

        title = extract_wikipedia_title(post)
        if not title:
            post["view_count"] = 0
            post["like_count"] = 0
            render_progress(index, total_candidates, updated_count)
            continue

        view_count: int
        if not args.force_refresh and title in cache:
            view_count = cache[title]
        else:
            view_count = fetch_view_count(title, args.days)
            cache[title] = view_count

        like_count = derive_like_count(view_count, f"{post_id}:{title}")
        post["view_count"] = view_count
        post["like_count"] = like_count
        updated_count += 1
        render_progress(index, total_candidates, updated_count)

    render_progress(total_candidates, total_candidates, updated_count, done=True)

    write_json(args.output, payload)
    save_cache(args.cache, cache)
    print(
        f"Updated popularity fields for {updated_count} posts in {args.output} "
        f"(days={args.days})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
