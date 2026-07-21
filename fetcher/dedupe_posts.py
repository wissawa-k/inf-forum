#!/usr/bin/env python3

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def normalize_space(value: str) -> str:
    return " ".join(value.strip().split())


def normalize_source(source: str) -> str:
    normalized = normalize_space(source)
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized


def fingerprint_post(post: dict[str, Any]) -> tuple[str, str]:
    source = post.get("source")
    if isinstance(source, str):
        normalized_source = normalize_source(source)
        if normalized_source:
            return ("source", normalized_source.casefold())

    title = post.get("title")
    if isinstance(title, str):
        normalized_title = normalize_space(title)
        if normalized_title:
            return ("title", normalized_title.casefold())

    summary = post.get("summary")
    if isinstance(summary, str):
        normalized_summary = normalize_space(summary)
        if normalized_summary:
            return ("summary", normalized_summary.casefold())

    post_id = str(post.get("id", ""))
    return ("id", post_id)


def parse_numeric_id(raw_id: str) -> int:
    try:
        return int(raw_id)
    except ValueError:
        return 10**12


def parse_numeric_id_strict(raw_id: str) -> int | None:
    try:
        return int(raw_id)
    except ValueError:
        return None


def dedupe_dataset(payload: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
    posts_key = "posts_by_id" if "posts_by_id" in payload else "articles_by_id"
    raw_posts = payload.get(posts_key)
    if not isinstance(raw_posts, dict):
        raise ValueError(f"Expected '{posts_key}' to be a JSON object.")

    categories = payload.get("categories", {})
    if not isinstance(categories, dict):
        categories = {}

    old_to_canonical: dict[str, str] = {}
    canonical_posts: dict[str, dict[str, Any]] = {}
    seen_fingerprints: dict[tuple[str, str], str] = {}

    sorted_post_ids = sorted(raw_posts.keys(), key=parse_numeric_id)
    for raw_post_id in sorted_post_ids:
        post = raw_posts.get(raw_post_id)
        if not isinstance(post, dict):
            continue

        marker = fingerprint_post(post)
        existing_canonical_id = seen_fingerprints.get(marker)
        if existing_canonical_id is None:
            canonical_copy = dict(post)
            post_categories = canonical_copy.get("categories")
            if isinstance(post_categories, list):
                canonical_copy["categories"] = [str(category) for category in post_categories]
            else:
                canonical_copy["categories"] = []

            canonical_posts[raw_post_id] = canonical_copy
            seen_fingerprints[marker] = raw_post_id
            old_to_canonical[raw_post_id] = raw_post_id
            continue

        old_to_canonical[raw_post_id] = existing_canonical_id
        duplicate_categories = post.get("categories", [])
        if isinstance(duplicate_categories, list):
            canonical_category_list = canonical_posts[existing_canonical_id].setdefault(
                "categories", []
            )
            for category in duplicate_categories:
                category_text = str(category)
                if category_text not in canonical_category_list:
                    canonical_category_list.append(category_text)

    rebuilt_categories: dict[str, list[int]] = {}
    for category, post_ids in categories.items():
        if not isinstance(post_ids, list):
            continue

        unique_ids: list[int] = []
        seen_ids: set[int] = set()
        for raw_id in post_ids:
            canonical_id = old_to_canonical.get(str(raw_id))
            if canonical_id is None:
                continue
            numeric_id = parse_numeric_id_strict(canonical_id)
            if numeric_id is None:
                continue
            if numeric_id in seen_ids:
                continue
            seen_ids.add(numeric_id)
            unique_ids.append(numeric_id)

            post = canonical_posts.get(canonical_id)
            if post is None:
                continue
            post_category_list = post.setdefault("categories", [])
            category_name = str(category)
            if category_name not in post_category_list:
                post_category_list.append(category_name)

        rebuilt_categories[str(category)] = unique_ids

    for post_id, post in canonical_posts.items():
        parsed_id = parse_numeric_id_strict(post_id)
        if parsed_id is not None:
            post["id"] = parsed_id
        post_categories = post.get("categories", [])
        if isinstance(post_categories, list):
            deduped_categories: list[str] = []
            seen_categories: set[str] = set()
            for category in post_categories:
                category_text = str(category)
                if category_text in seen_categories:
                    continue
                seen_categories.add(category_text)
                deduped_categories.append(category_text)
            post["categories"] = sorted(deduped_categories)

    deduped_payload = dict(payload)
    deduped_payload[posts_key] = canonical_posts
    deduped_payload["categories"] = rebuilt_categories
    deduped_payload["total_posts"] = len(canonical_posts)
    if "article_count" in deduped_payload:
        deduped_payload["article_count"] = len(canonical_posts)

    stats = deduped_payload.get("stats")
    if isinstance(stats, dict):
        special_posts = sum(
            1
            for post in canonical_posts.values()
            if isinstance(post, dict) and post.get("post_type") == "special"
        )
        stats["special_posts"] = special_posts
        stats["normal_posts"] = len(canonical_posts) - special_posts

    duplicate_count = len(raw_posts) - len(canonical_posts)
    return deduped_payload, len(raw_posts), duplicate_count


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    default_posts = repo_root / "docs" / "data" / "posts.json"

    parser = argparse.ArgumentParser(
        description="Ensure docs/data/posts.json has no duplicate posts."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=default_posts,
        help="Path to input posts JSON file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_posts,
        help="Path to output posts JSON file.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode only: exits with status 1 when duplicates are found.",
    )
    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8") as input_file:
        payload = json.load(input_file)

    if not isinstance(payload, dict):
        print("Input JSON must be an object.", file=sys.stderr)
        return 1

    deduped_payload, original_count, duplicate_count = dedupe_dataset(payload)

    if args.check:
        if duplicate_count > 0:
            print(
                f"Found {duplicate_count} duplicate posts in {args.input} "
                f"(original={original_count}, unique={original_count - duplicate_count}).",
                file=sys.stderr,
            )
            return 1
        print(f"No duplicate posts found in {args.input}.")
        return 0

    write_json(args.output, deduped_payload)
    print(
        f"Wrote deduplicated dataset to {args.output} "
        f"(removed={duplicate_count}, original={original_count}, unique={original_count - duplicate_count})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
