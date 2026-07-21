#!/usr/bin/env python3

import argparse
import json
import sys
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


WIKIPEDIA_SUMMARY_ENDPOINT = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
WIKIPEDIA_TOP_PAGES_ENDPOINT = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/"
    "{year}/{month:02d}/{day:02d}"
)
WIKIPEDIA_QUERY_API_ENDPOINT = "https://en.wikipedia.org/w/api.php?{}"
USER_AGENT = "wikifetch/1.0 (https://github.com/)"


def normalize_category(raw_category: str) -> str:
    category = raw_category.strip().lower()
    return category if category else "uncategorized"


def read_categorized_article_requests(input_path: Path) -> list[tuple[str, str]]:
    requests: list[tuple[str, str]] = []
    active_category = "uncategorized"

    with input_path.open("r", encoding="utf-8") as input_file:
        for raw_line in input_file:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("[") and line.endswith("]"):
                active_category = normalize_category(line[1:-1])
                continue

            if "|" in line:
                category, title = line.split("|", maxsplit=1)
                requests.append((normalize_category(category), title.strip()))
                continue

            requests.append((active_category, line))

    return requests


def fetch_json(url: str, suppress_errors: bool = False) -> dict[str, Any] | None:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if not suppress_errors:
            print(f"Request failed for '{url}': HTTP {error.code}", file=sys.stderr)
        return None
    except URLError as error:
        if not suppress_errors:
            print(f"Request failed for '{url}': {error.reason}", file=sys.stderr)
        return None


def fetch_article_summary(title: str) -> dict[str, Any] | None:
    url = WIKIPEDIA_SUMMARY_ENDPOINT.format(quote(title, safe=""))
    payload = fetch_json(url)
    if payload is None:
        print(f"Failed to fetch summary for '{title}'", file=sys.stderr)
        return None

    if payload.get("type") == "https://mediawiki.org/wiki/HyperSwitch/errors/not_found":
        print(f"Article not found: '{title}'", file=sys.stderr)
        return None

    timestamp = payload.get("timestamp")
    if not timestamp:
        timestamp = datetime.now(timezone.utc).isoformat()

    return {
        "title": payload.get("title", title),
        "summary": payload.get("extract", ""),
        "updated_at": timestamp,
        "source": payload.get("content_urls", {}).get("desktop", {}).get("page", ""),
    }


def fetch_top_visited_titles(limit: int) -> list[str]:
    payload: dict[str, Any] | None = None
    for day_offset in range(1, 8):
        target_date = datetime.now(timezone.utc).date() - timedelta(days=day_offset)
        url = WIKIPEDIA_TOP_PAGES_ENDPOINT.format(
            year=target_date.year, month=target_date.month, day=target_date.day
        )
        payload = fetch_json(url, suppress_errors=True)
        if payload and payload.get("items"):
            break
    if payload is None or not payload.get("items"):
        fallback_url = WIKIPEDIA_TOP_PAGES_ENDPOINT.format(year=2024, month=1, day=1)
        payload = fetch_json(fallback_url, suppress_errors=True)
    if payload is None or not payload.get("items"):
        print("Could not fetch top visited pages for the last 7 days.", file=sys.stderr)
        return []

    items = payload.get("items", [])
    titles = []
    for article in items[0].get("articles", []):
        name = article.get("article", "")
        if not name or name == "Main_Page" or ":" in name:
            continue
        titles.append(name.replace("_", " "))
        if len(titles) >= limit:
            break
    return titles


def fetch_page_links(title: str, max_links: int | None = None) -> list[str]:
    links: list[str] = []
    continue_token: str | None = None

    while True:
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

        url = WIKIPEDIA_QUERY_API_ENDPOINT.format(urlencode(params))
        payload = fetch_json(url)
        if payload is None:
            return links

        pages = payload.get("query", {}).get("pages", {})
        for page in pages.values():
            for link in page.get("links", []):
                link_title = link.get("title")
                if not link_title:
                    continue
                links.append(link_title)
                if max_links is not None and len(links) >= max_links:
                    return links

        continue_token = payload.get("continue", {}).get("plcontinue")
        if not continue_token:
            return links


def fetch_recursive_titles(
    start_title: str, max_depth: int, branch_limit: int
) -> list[tuple[str, str]]:
    queue: deque[tuple[str, int]] = deque([(start_title, 0)])
    visited: set[str] = set()
    requests: list[tuple[str, str]] = []

    while queue:
        title, depth = queue.popleft()
        if title in visited:
            continue
        visited.add(title)
        requests.append((f"depth-{depth}", title))

        if depth >= max_depth:
            continue

        for linked_title in fetch_page_links(title, max_links=branch_limit):
            if linked_title not in visited:
                queue.append((linked_title, depth + 1))

    return requests


def build_indexed_dataset(
    article_requests: list[tuple[str, str]],
) -> dict[str, Any]:
    articles_by_id: dict[str, dict[str, Any]] = {}
    article_id_by_title: dict[str, str] = {}
    categories: dict[str, list[int]] = {}
    next_id = 1

    for category, title in article_requests:
        if title not in article_id_by_title:
            summary = fetch_article_summary(title)
            if summary is None:
                continue

            article_id = str(next_id)
            article_id_by_title[title] = article_id
            articles_by_id[article_id] = {
                "id": next_id,
                "title": summary["title"],
                "summary": summary["summary"],
                "updated_at": summary["updated_at"],
                "source": summary["source"],
                "categories": [],
            }
            next_id += 1

        article_id = article_id_by_title[title]
        numeric_id = int(article_id)

        if category not in categories:
            categories[category] = []
        if numeric_id not in categories[category]:
            categories[category].append(numeric_id)

        article_categories = articles_by_id[article_id]["categories"]
        if category not in article_categories:
            article_categories.append(category)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "article_count": len(articles_by_id),
        "articles_by_id": articles_by_id,
        "categories": categories,
    }


def write_posts(output_path: Path, dataset: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(dataset, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser(
        description="Fetch Wikipedia article summaries into data/posts.json"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=repo_root / "input" / "articles.txt",
        help="Path to categorized text file (supports [category] headers)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / "docs" / "data" / "posts.json",
        help="Path to output JSON file",
    )
    parser.add_argument(
        "--mode",
        choices=["input", "top", "recursive"],
        default="input",
        help="Fetch mode: input file, top visited pages, or recursive crawl",
    )
    parser.add_argument(
        "--top-count",
        type=int,
        default=10,
        help="Number of top visited pages to fetch when mode=top",
    )
    parser.add_argument(
        "--recursive-title",
        type=str,
        help="Starting page title when mode=recursive",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=1,
        help="Recursive depth when mode=recursive",
    )
    parser.add_argument(
        "--branch-limit",
        type=int,
        default=20,
        help="Max number of links to follow per page when mode=recursive",
    )
    args = parser.parse_args()

    article_requests: list[tuple[str, str]] = []
    if args.mode == "input":
        article_requests = read_categorized_article_requests(args.input)
    elif args.mode == "top":
        titles = fetch_top_visited_titles(args.top_count)
        article_requests = [("top-visited", title) for title in titles]
    else:
        if not args.recursive_title:
            print(
                "--recursive-title is required when --mode recursive",
                file=sys.stderr,
            )
            return 1
        if args.depth < 0:
            print("--depth must be 0 or greater", file=sys.stderr)
            return 1
        if args.branch_limit < 1:
            print("--branch-limit must be 1 or greater", file=sys.stderr)
            return 1
        article_requests = fetch_recursive_titles(
            args.recursive_title, args.depth, args.branch_limit
        )

    dataset = build_indexed_dataset(article_requests)
    write_posts(args.output, dataset)
    print(
        f"Wrote {dataset['article_count']} unique articles to {args.output} "
        f"(mode={args.mode})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
