#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

USER_AGENT = "wikifetch/1.0 (https://github.com/)"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
REQUEST_DELAY_SECONDS = 0.08
MAX_RETRIES = 5
MIN_SCORE_TO_APPLY = 2.5

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "technology": [
        "technology",
        "engineering",
        "software",
        "computer",
        "computing",
        "internet",
        "ai",
        "artificial intelligence",
        "digital",
        "cyber",
        "electronics",
    ],
    "science": [
        "science",
        "physics",
        "chemistry",
        "biology",
        "astronomy",
        "scientific",
        "research",
        "mathematics",
        "ecology",
    ],
    "gaming": [
        "video game",
        "games",
        "game",
        "esports",
        "nintendo",
        "playstation",
        "xbox",
    ],
    "movies": [
        "film",
        "movie",
        "cinema",
        "animation",
        "director",
        "screenwriter",
        "actor",
        "actress",
    ],
    "music": [
        "music",
        "song",
        "album",
        "band",
        "singer",
        "composer",
        "musician",
        "record label",
    ],
    "sports": [
        "sport",
        "football",
        "basketball",
        "baseball",
        "olympic",
        "athlete",
        "championship",
        "fifa",
    ],
    "history": [
        "history",
        "historical",
        "ancient",
        "medieval",
        "empire",
        "war",
        "revolution",
        "century",
    ],
    "business": [
        "company",
        "corporation",
        "business",
        "enterprise",
        "industry",
        "startup",
        "ceo",
        "management",
    ],
    "health": [
        "health",
        "medical",
        "medicine",
        "disease",
        "treatment",
        "hospital",
        "clinical",
        "nutrition",
    ],
    "travel": [
        "tourism",
        "travel",
        "city",
        "country",
        "region",
        "destination",
        "airport",
        "geography",
    ],
    "fashion": [
        "fashion",
        "clothing",
        "apparel",
        "designer",
        "runway",
        "cosmetics",
        "beauty",
    ],
    "food": [
        "food",
        "cuisine",
        "dish",
        "cooking",
        "restaurant",
        "chef",
        "ingredient",
    ],
    "education": [
        "education",
        "school",
        "university",
        "college",
        "academic",
        "learning",
        "curriculum",
    ],
    "politics": [
        "politics",
        "political",
        "government",
        "election",
        "parliament",
        "president",
        "policy",
        "diplomacy",
    ],
    "finance": [
        "finance",
        "financial",
        "bank",
        "banking",
        "investment",
        "market",
        "stock",
        "economic",
    ],
    "books": [
        "book",
        "novel",
        "literature",
        "author",
        "poetry",
        "publisher",
        "writer",
    ],
    "art": [
        "art",
        "artist",
        "painting",
        "sculpture",
        "museum",
        "gallery",
        "artwork",
    ],
    "nature": [
        "nature",
        "environment",
        "wildlife",
        "ecosystem",
        "species",
        "forest",
        "climate",
        "conservation",
    ],
}


def fetch_json(url: str) -> dict[str, Any] | None:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            sleep(min(2**attempt, 20))
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
                continue
            print(f"Request failed for '{url}': HTTP {error.code}", file=sys.stderr)
            return None
        except URLError as error:
            if attempt < MAX_RETRIES:
                continue
            print(f"Request failed for '{url}': {error.reason}", file=sys.stderr)
            return None
    return None


def extract_title_from_source(post: dict[str, Any]) -> str:
    source = post.get("source", "")
    if isinstance(source, str) and source:
        parsed = urlparse(source)
        slug = parsed.path.rsplit("/", maxsplit=1)[-1]
        if slug:
            return unquote(slug.replace("_", " "))
    title = post.get("title", "")
    return str(title).strip()


def fetch_page_categories(title: str) -> list[str]:
    categories: list[str] = []
    continue_token: str | None = None

    while True:
        params = [
            "action=query",
            "format=json",
            "redirects=1",
            "prop=categories",
            "cllimit=max",
            f"titles={quote(title)}",
        ]
        if continue_token:
            params.append(f"clcontinue={quote(continue_token)}")
        url = f"{WIKIPEDIA_API}?{'&'.join(params)}"
        payload = fetch_json(url)
        if payload is None:
            return categories

        pages = payload.get("query", {}).get("pages", {})
        for page in pages.values():
            for category in page.get("categories", []):
                raw_title = category.get("title", "")
                if not isinstance(raw_title, str):
                    continue
                cleaned = raw_title.removeprefix("Category:").strip()
                if cleaned:
                    categories.append(cleaned.lower())

        continue_token = payload.get("continue", {}).get("clcontinue")
        if not continue_token:
            return categories


def score_categories(
    post: dict[str, Any], wiki_categories: list[str], allowed_categories: list[str]
) -> dict[str, float]:
    corpus_parts = [
        str(post.get("title", "")),
        str(post.get("description", "")),
        str(post.get("summary", "")),
        " ".join(wiki_categories),
    ]
    corpus = " ".join(corpus_parts).lower()
    scores: dict[str, float] = {category: 0.0 for category in allowed_categories}

    for category in allowed_categories:
        for keyword in CATEGORY_KEYWORDS.get(category, []):
            if keyword in corpus:
                scores[category] += 1.0

    country_signals = (
        "countries in" in corpus
        or "sovereign states" in corpus
        or "geography of" in corpus
        or "capitals in" in corpus
        or " former countries" in corpus
        or "country" in str(post.get("description", "")).lower()
    )
    if country_signals:
        scores["travel"] += 3.0
        scores["politics"] += 1.8
        scores["history"] += 1.2
        scores["technology"] -= 2.5

    if "technology" in corpus and "country" not in corpus:
        scores["technology"] += 1.5

    if "economic" in corpus and "history" in corpus:
        scores["history"] += 0.7

    return scores


def pick_categories(scores: dict[str, float], fallback: list[str]) -> list[str]:
    if not scores:
        return sorted(set(fallback))

    top_score = max(scores.values())
    if top_score < MIN_SCORE_TO_APPLY:
        return sorted(set(fallback))

    selected = [
        category
        for category, score in scores.items()
        if score >= MIN_SCORE_TO_APPLY and score >= top_score - 1.3
    ]
    if not selected:
        return sorted(set(fallback))
    return sorted(set(selected))


def rebuild_categories_index(posts_by_id: dict[str, dict[str, Any]]) -> dict[str, list[int]]:
    mapping: dict[str, list[int]] = {}
    for post_id, post in posts_by_id.items():
        try:
            numeric_id = int(post_id)
        except ValueError:
            continue
        for category in post.get("categories", []):
            key = str(category)
            mapping.setdefault(key, []).append(numeric_id)

    for ids in mapping.values():
        ids.sort()
    return mapping


def load_cache(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as cache_file:
        payload = json.load(cache_file)
    if not isinstance(payload, dict):
        return {}
    cache: dict[str, list[str]] = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, list):
            cache[key] = [str(item).lower() for item in value]
    return cache


def save_cache(path: Path, cache: dict[str, list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        json.dump(cache, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


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


def render_progress(current: int, total: int, changed_count: int, done: bool = False) -> None:
    if total < 1:
        return

    ratio = min(max(current / total, 0.0), 1.0)
    width = 30
    filled = int(width * ratio)
    bar = "#" * filled + "-" * (width - filled)
    message = (
        f"\r[{bar}] {current}/{total} "
        f"({ratio * 100:5.1f}%) corrected={changed_count}"
    )

    if done:
        print("", file=sys.stderr)
        return
    print(message, end="", file=sys.stderr, flush=True)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    default_posts = repo_root / "docs" / "data" / "posts.json"
    default_categories = repo_root / "docs" / "data" / "categories.json"
    default_cache = repo_root / "docs" / "data" / "wiki_category_cache.json"

    parser = argparse.ArgumentParser(
        description="Recategorize posts.json using Wikipedia page category metadata."
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
        "--categories-file",
        type=Path,
        default=default_categories,
        help="Allowed top-level categories JSON file.",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=default_cache,
        help="Cache file for Wikipedia category lookups.",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        help="Optional limit for number of posts to process.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode only; exits with status 1 if recategorizations are needed.",
    )
    args = parser.parse_args()

    if args.max_posts is not None and args.max_posts < 1:
        print("--max-posts must be 1 or greater.", file=sys.stderr)
        return 1

    with args.input.open("r", encoding="utf-8") as input_file:
        payload = json.load(input_file)
    if not isinstance(payload, dict):
        print("posts.json must be a JSON object.", file=sys.stderr)
        return 1

    with args.categories_file.open("r", encoding="utf-8") as categories_file:
        categories_payload = json.load(categories_file)
    allowed_categories = categories_payload.get("categories", [])
    if not isinstance(allowed_categories, list) or not all(
        isinstance(category, str) for category in allowed_categories
    ):
        print("categories.json must contain a string array at key 'categories'.", file=sys.stderr)
        return 1

    posts_by_id = payload.get("posts_by_id")
    if not isinstance(posts_by_id, dict):
        print("posts.json missing 'posts_by_id' object.", file=sys.stderr)
        return 1

    cache = load_cache(args.cache)
    changed_count = 0
    processed = 0

    post_ids = sort_post_ids(list(posts_by_id.keys()))
    candidate_post_ids = (
        post_ids[: args.max_posts] if args.max_posts is not None else post_ids
    )
    total_candidates = len(candidate_post_ids)

    for index, post_id in enumerate(candidate_post_ids, start=1):
        post = posts_by_id.get(post_id)
        if not isinstance(post, dict):
            render_progress(index, total_candidates, changed_count)
            continue

        title = extract_title_from_source(post)
        if not title:
            render_progress(index, total_candidates, changed_count)
            continue
        cache_key = title.casefold()
        wiki_categories = cache.get(cache_key)
        if wiki_categories is None:
            wiki_categories = fetch_page_categories(title)
            cache[cache_key] = wiki_categories

        existing_categories = post.get("categories", [])
        if not isinstance(existing_categories, list):
            existing_categories = []
        existing = [str(category) for category in existing_categories if isinstance(category, str)]

        scores = score_categories(post, wiki_categories, allowed_categories)
        predicted = pick_categories(scores, existing)
        processed += 1

        if sorted(existing) != sorted(predicted):
            post["categories"] = predicted
            changed_count += 1

        render_progress(index, total_candidates, changed_count)

    render_progress(total_candidates, total_candidates, changed_count, done=True)

    if args.check:
        print(
            f"Processed {processed} posts; {changed_count} posts need category corrections."
        )
        save_cache(args.cache, cache)
        return 1 if changed_count > 0 else 0

    payload["categories"] = rebuild_categories_index(posts_by_id)
    payload["total_posts"] = len(posts_by_id)
    write_json(args.output, payload)
    save_cache(args.cache, cache)
    print(
        f"Processed {processed} posts and corrected {changed_count} posts in {args.output}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
