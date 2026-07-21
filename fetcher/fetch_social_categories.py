#!/usr/bin/env python3

import argparse
import json
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USER_AGENT = "wikifetch/1.0 (https://github.com/)"
WIKIPEDIA_QUERY_API_ENDPOINT = "https://en.wikipedia.org/w/api.php?{}"
REQUEST_DELAY_SECONDS = 0.15
MAX_RETRIES = 6

# User-friendly labels mapped to source Wikipedia categories.
SOCIAL_CATEGORY_SEEDS: dict[str, list[str]] = {
    "technology": [
        "Technology",
        "Software",
        "Artificial intelligence",
    ],
    "science": [
        "Physics",
        "Biology",
        "Astronomy",
    ],
    "gaming": [
        "Video games",
        "Esports",
        "Game engines",
    ],
    "movies": [
        "Films",
        "Film directors",
        "Film genres",
    ],
    "music": [
        "Music",
        "Singers",
        "Music genres",
    ],
    "sports": [
        "Sports",
        "Olympic Games",
        "Association football",
    ],
    "history": [
        "History",
        "Ancient history",
        "World War II",
    ],
    "business": [
        "Companies",
        "Entrepreneurship",
        "Economics",
    ],
    "health": [
        "Medicine",
        "Nutrition",
        "Exercise",
    ],
    "travel": [
        "Tourism",
        "Countries",
        "World Heritage Sites",
    ],
    "fashion": [
        "Fashion",
        "Clothing",
        "Designers",
    ],
    "food": [
        "Food",
        "Cuisine",
        "Cooking",
    ],
    "education": [
        "Education",
        "Universities and colleges",
        "Academic disciplines",
    ],
    "politics": [
        "Politics",
        "Political ideologies",
        "Governments",
    ],
    "finance": [
        "Finance",
        "Investment",
        "Banking",
    ],
    "books": [
        "Books",
        "Writers",
        "Literature",
    ],
    "art": [
        "Art",
        "Painting",
        "Sculpture",
    ],
    "nature": [
        "Nature",
        "Environment",
        "Ecology",
    ],
}


def fetch_json(params: dict[str, str]) -> dict[str, Any] | None:
    url = WIKIPEDIA_QUERY_API_ENDPOINT.format(urlencode(params))
    request = Request(url, headers={"User-Agent": USER_AGENT})
    attempt = 0

    while attempt <= MAX_RETRIES:
        if attempt > 0:
            backoff = min(2**attempt, 30)
            sleep(backoff)

        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
                sleep(REQUEST_DELAY_SECONDS)
                return payload
        except HTTPError as error:
            if error.code == 429 and attempt < MAX_RETRIES:
                retry_after = error.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    sleep(int(retry_after))
                attempt += 1
                continue
            print(f"Request failed for '{url}': HTTP {error.code}", file=sys.stderr)
            return None
        except URLError as error:
            if attempt < MAX_RETRIES:
                attempt += 1
                continue
            print(f"Request failed for '{url}': {error.reason}", file=sys.stderr)
            return None

    print(f"Request failed for '{url}': retries exhausted", file=sys.stderr)
    return None


def fetch_category_members(category: str, max_items: int) -> list[str]:
    category_title = category if category.startswith("Category:") else f"Category:{category}"
    titles: list[str] = []
    continue_token: str | None = None

    while len(titles) < max_items:
        params: dict[str, str] = {
            "action": "query",
            "format": "json",
            "list": "categorymembers",
            "cmtitle": category_title,
            "cmnamespace": "0",
            "cmtype": "page",
            "cmlimit": "500",
        }
        if continue_token is not None:
            params["cmcontinue"] = continue_token

        payload = fetch_json(params)
        if payload is None:
            return titles

        members = payload.get("query", {}).get("categorymembers", [])
        for member in members:
            title = member.get("title")
            if not title:
                continue
            titles.append(title)
            if len(titles) >= max_items:
                break

        continue_token = payload.get("continue", {}).get("cmcontinue")
        if not continue_token:
            break

    return titles


def fetch_page_links(title: str, max_links: int) -> list[str]:
    links: list[str] = []
    continue_token: str | None = None

    while len(links) < max_links:
        params: dict[str, str] = {
            "action": "query",
            "format": "json",
            "prop": "links",
            "titles": title,
            "plnamespace": "0",
            "pllimit": "max",
        }
        if continue_token is not None:
            params["plcontinue"] = continue_token

        payload = fetch_json(params)
        if payload is None:
            return links

        pages = payload.get("query", {}).get("pages", {})
        for page in pages.values():
            for link in page.get("links", []):
                link_title = link.get("title")
                if not link_title or ":" in link_title:
                    continue
                links.append(link_title)
                if len(links) >= max_links:
                    return links

        continue_token = payload.get("continue", {}).get("plcontinue")
        if not continue_token:
            break

    return links


def fetch_top_seed_titles(
    source_categories: list[str],
    top_seed_count: int,
) -> list[str]:
    seeds: list[str] = []
    seen: set[str] = set()

    for source_category in source_categories:
        members = fetch_category_members(source_category, top_seed_count * 5)
        for member in members:
            if member in seen or ":" in member:
                continue
            seen.add(member)
            seeds.append(member)
            if len(seeds) >= top_seed_count:
                return seeds

    return seeds


def expand_titles_recursively(
    seed_titles: list[str],
    recursive_depth: int,
    links_per_page: int,
    per_category_limit: int,
) -> list[str]:
    visited: set[str] = set()
    collected: list[str] = []
    queue: deque[tuple[str, int]] = deque()

    for title in seed_titles:
        if title in visited:
            continue
        visited.add(title)
        collected.append(title)
        queue.append((title, 0))
        if len(collected) >= per_category_limit:
            return collected

    while queue and len(collected) < per_category_limit:
        title, depth = queue.popleft()
        if depth >= recursive_depth:
            continue

        for linked_title in fetch_page_links(title, links_per_page):
            if linked_title in visited:
                continue
            visited.add(linked_title)
            collected.append(linked_title)
            queue.append((linked_title, depth + 1))
            if len(collected) >= per_category_limit:
                break

    return collected


def build_social_categories(
    top_seed_count: int,
    recursive_depth: int,
    links_per_page: int,
    per_category_limit: int,
) -> tuple[dict[str, list[str]], dict[str, Any]]:
    social_categories: dict[str, list[str]] = {}
    stats: dict[str, Any] = {"categories": {}}

    for label, wikipedia_categories in SOCIAL_CATEGORY_SEEDS.items():
        seed_titles = fetch_top_seed_titles(wikipedia_categories, top_seed_count)
        collected = expand_titles_recursively(
            seed_titles, recursive_depth, links_per_page, per_category_limit
        )

        social_categories[label] = collected
        stats["categories"][label] = {
            "seed_count": len(seed_titles),
            "article_count": len(collected),
        }

    return social_categories, stats


def write_text_categories(path: Path, social_categories: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        for category, titles in social_categories.items():
            output_file.write(f"[{category}]\n")
            for title in titles:
                output_file.write(f"{title}\n")
            output_file.write("\n")


def write_json_categories(
    path: Path, social_categories: dict[str, list[str]], stats: dict[str, Any]
) -> None:
    total_titles = sum(len(titles) for titles in social_categories.values())
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_titles": total_titles,
        "category_count": len(social_categories),
        "crawl_stats": stats,
        "categories": social_categories,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Fetch social-media-friendly topic categories from Wikipedia"
    )
    parser.add_argument(
        "--top-seed-count",
        type=int,
        default=10,
        help="Top N category pages used as recursive starting points per category",
    )
    parser.add_argument(
        "--recursive-depth",
        type=int,
        default=1,
        help="Recursive depth for link expansion from each seed page",
    )
    parser.add_argument(
        "--links-per-page",
        type=int,
        default=60,
        help="Max linked pages to collect from each expanded page",
    )
    parser.add_argument(
        "--per-category-limit",
        type=int,
        default=450,
        help="Max number of titles to output per category",
    )
    parser.add_argument(
        "--text-output",
        type=Path,
        default=repo_root / "input" / "social_media_articles.txt",
        help="Path for simplified text output ([category] + article titles)",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=repo_root / "docs" / "data" / "social_media_categories.json",
        help="Path for JSON output",
    )
    args = parser.parse_args()

    if args.top_seed_count < 1:
        print("--top-seed-count must be 1 or greater", file=sys.stderr)
        return 1
    if args.recursive_depth < 0:
        print("--recursive-depth must be 0 or greater", file=sys.stderr)
        return 1
    if args.links_per_page < 1:
        print("--links-per-page must be 1 or greater", file=sys.stderr)
        return 1
    if args.per_category_limit < 1:
        print("--per-category-limit must be 1 or greater", file=sys.stderr)
        return 1

    social_categories, stats = build_social_categories(
        top_seed_count=args.top_seed_count,
        recursive_depth=args.recursive_depth,
        links_per_page=args.links_per_page,
        per_category_limit=args.per_category_limit,
    )
    write_text_categories(args.text_output, social_categories)
    write_json_categories(args.json_output, social_categories, stats)

    total = sum(len(titles) for titles in social_categories.values())
    print(
        f"Wrote {total} topics across {len(social_categories)} categories "
        f"to {args.text_output} and {args.json_output}"
    )
    if total < 5000:
        print(
            "Warning: fetched fewer than 5000 topics. Increase --per-category-limit, "
            "--recursive-depth, or --links-per-page.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
