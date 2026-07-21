#!/usr/bin/env python3

import argparse
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

USER_AGENT = "wikifetch/1.0 (https://github.com/)"
WIKIPEDIA_SUMMARY_ENDPOINT = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
OLLAMA_GENERATE_ENDPOINT = "http://127.0.0.1:11434/api/generate"
REQUEST_DELAY_SECONDS = 0.1
MAX_RETRIES = 5

PERSON_KEYWORDS = {
    "actor",
    "actress",
    "artist",
    "athlete",
    "author",
    "biologist",
    "chemist",
    "composer",
    "director",
    "economist",
    "engineer",
    "entrepreneur",
    "footballer",
    "historian",
    "journalist",
    "mathematician",
    "musician",
    "physicist",
    "philosopher",
    "politician",
    "scientist",
    "singer",
    "writer",
}

ACHIEVEMENT_KEYWORDS = {
    "won",
    "winner",
    "awarded",
    "award",
    "received",
    "recipient",
    "prize",
    "medal",
    "champion",
    "record",
    "nobel",
    "oscar",
    "grammy",
    "pulitzer",
    "appointed",
    "elected",
    "founded",
    "discovered",
    "invented",
}


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def sanitize_text(value: str, max_len: int | None = None) -> str:
    cleaned = re.sub(r"\[[0-9]+\]", "", value)
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if max_len is not None and len(cleaned) > max_len:
        return f"{cleaned[: max_len - 1].rstrip()}…"
    return cleaned


def fetch_wikipedia_summary(title: str) -> dict[str, str] | None:
    url = WIKIPEDIA_SUMMARY_ENDPOINT.format(quote(title, safe=""))
    request = Request(url, headers={"User-Agent": USER_AGENT})

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            sleep(min(2**attempt, 20))
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
                sleep(REQUEST_DELAY_SECONDS)
        except HTTPError as error:
            if error.code == 429 and attempt < MAX_RETRIES:
                retry_after = error.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    sleep(int(retry_after))
                continue
            print(f"Summary request failed for '{title}': HTTP {error.code}", file=sys.stderr)
            return None
        except URLError as error:
            if attempt < MAX_RETRIES:
                continue
            print(f"Summary request failed for '{title}': {error.reason}", file=sys.stderr)
            return None

        if payload.get("type") == "https://mediawiki.org/wiki/HyperSwitch/errors/not_found":
            return None

        return {
            "title": sanitize_text(payload.get("title", title)),
            "description": sanitize_text(payload.get("description", "")),
            "summary": sanitize_text(payload.get("extract", ""), 1200),
            "source": payload.get("content_urls", {}).get("desktop", {}).get("page", ""),
            "updated_at": payload.get("timestamp", ""),
            "thumbnail": payload.get("thumbnail", {}).get("source", ""),
        }

    return None


def is_person_article(article: dict[str, Any]) -> bool:
    description = article.get("description", "").lower()
    summary = article.get("summary", "").lower()
    if any(keyword in description for keyword in PERSON_KEYWORDS):
        return True
    title = article.get("title", "")
    proper_name_like = (
        isinstance(title, str)
        and len(title.split()) >= 2
        and re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ'.\- ]+", title) is not None
    )
    biography_signal = any(
        phrase in summary
        for phrase in (
            " was born ",
            " born ",
            " is an ",
            " is a ",
            " was an ",
            " was a ",
        )
    ) and (" he " in summary or " she " in summary or " they " in summary)
    if proper_name_like and biography_signal:
        return True
    return False


def classify_post_type(article: dict[str, Any]) -> str:
    thumbnail = article.get("thumbnail", "")
    if not isinstance(thumbnail, str) or not thumbnail.strip():
        return "normal"
    if not is_person_article(article):
        return "normal"

    summary = article.get("summary", "").lower()
    if any(keyword in summary for keyword in ACHIEVEMENT_KEYWORDS):
        return "special"
    return "normal"


def extract_achievement_event(article: dict[str, Any]) -> str:
    summary = article.get("summary", "")
    sentences = split_sentences(summary)
    for sentence in sentences:
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in ACHIEVEMENT_KEYWORDS):
            return sanitize_text(sentence, 280)
    return sanitize_text(summary, 280)


def load_summary_cache(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as cache_file:
        raw = json.load(cache_file)
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, dict)}


def save_summary_cache(path: Path, cache: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as cache_file:
        json.dump(cache, cache_file, ensure_ascii=False, indent=2)
        cache_file.write("\n")


def extract_titles_by_category(data: dict[str, Any]) -> dict[str, list[str]]:
    categories = data.get("categories")
    if not isinstance(categories, dict):
        raise ValueError("Input data does not contain a valid 'categories' object.")

    extracted: dict[str, list[str]] = {}

    # Format A: social_media_categories.json -> category: [title, title]
    social_format = all(
        isinstance(value, list) and (not value or isinstance(value[0], str))
        for value in categories.values()
    )
    if social_format:
        for category, titles in categories.items():
            extracted[str(category)] = [sanitize_text(title) for title in titles if title]
        return extracted

    # Format B: posts.json -> categories: {category: [ids]}, articles_by_id: {...}
    articles_by_id = data.get("articles_by_id")
    if not isinstance(articles_by_id, dict):
        raise ValueError("Input categories look ID-based but 'articles_by_id' is missing.")

    for category, ids in categories.items():
        if not isinstance(ids, list):
            continue
        titles: list[str] = []
        for item_id in ids:
            article = articles_by_id.get(str(item_id))
            if isinstance(article, dict) and isinstance(article.get("title"), str):
                titles.append(sanitize_text(article["title"]))
        extracted[str(category)] = titles
    return extracted


def build_articles(
    titles_by_category: dict[str, list[str]],
    cache: dict[str, dict[str, str]],
    limit: int | None,
    random_seed: int | None,
) -> tuple[list[dict[str, Any]], dict[str, list[int]], dict[str, dict[str, str]]]:
    title_categories: dict[str, set[str]] = {}
    category_queues: dict[str, list[str]] = {}
    rng = random.Random(random_seed)

    for category, titles in titles_by_category.items():
        shuffled_titles = [title for title in titles if title]
        rng.shuffle(shuffled_titles)
        category_queues[category] = shuffled_titles
        for title in titles:
            if not title:
                continue
            cleaned_title = sanitize_text(title)
            if cleaned_title not in title_categories:
                title_categories[cleaned_title] = set()
            title_categories[cleaned_title].add(category)

    category_positions = {category: 0 for category in category_queues}
    unique_titles: list[str] = []
    selected: set[str] = set()
    max_unique = limit if limit is not None else len(title_categories)

    while len(unique_titles) < max_unique:
        active_categories = [
            category
            for category, queue in category_queues.items()
            if category_positions[category] < len(queue)
        ]
        if not active_categories:
            break
        rng.shuffle(active_categories)

        progress = False
        for category in active_categories:
            queue = category_queues[category]
            while category_positions[category] < len(queue):
                candidate = sanitize_text(queue[category_positions[category]])
                category_positions[category] += 1
                if not candidate or candidate in selected:
                    continue
                selected.add(candidate)
                unique_titles.append(candidate)
                progress = True
                break
            if len(unique_titles) >= max_unique:
                break
        if not progress:
            break

    articles: list[dict[str, Any]] = []
    categories_to_ids: dict[str, list[int]] = {category: [] for category in titles_by_category}
    next_id = 1

    for title in unique_titles:
        summary_data = cache.get(title)
        if summary_data is None:
            summary_data = fetch_wikipedia_summary(title)
            if summary_data:
                cache[title] = summary_data
        if summary_data is None:
            continue

        categories = sorted(title_categories[title])
        post_type = classify_post_type(summary_data)
        article = {
            "id": next_id,
            "title": summary_data["title"],
            "description": summary_data["description"],
            "summary": summary_data["summary"],
            "source": summary_data["source"],
            "updated_at": summary_data["updated_at"],
            "thumbnail": summary_data.get("thumbnail", ""),
            "categories": categories,
            "post_type": post_type,
            "event_context": "",
            "post_text": "",
        }
        if post_type == "special":
            article["event_context"] = extract_achievement_event(summary_data)
        articles.append(article)
        for category in categories:
            categories_to_ids.setdefault(category, []).append(next_id)
        next_id += 1

    return articles, categories_to_ids, cache


def ollama_generate_structured(
    model: str,
    prompt: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": schema,
    }
    data = json.dumps(payload).encode("utf-8")
    request = Request(
        OLLAMA_GENERATE_ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            sleep(min(2**attempt, 20))
        try:
            with urlopen(request, timeout=240) as response:
                outer_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if attempt < MAX_RETRIES:
                continue
            raise RuntimeError(f"Ollama API error: HTTP {error.code}") from error
        except URLError as error:
            if attempt < MAX_RETRIES:
                continue
            raise RuntimeError(f"Ollama API connection error: {error.reason}") from error

        model_response = outer_payload.get("response")
        if not isinstance(model_response, str):
            raise RuntimeError("Ollama API returned invalid response payload.")
        parsed = json.loads(model_response)
        if not isinstance(parsed, dict):
            raise RuntimeError("Ollama structured output is not a JSON object.")
        return parsed

    raise RuntimeError("Ollama API retries exhausted.")


def generate_special_posts_with_ollama(
    articles: list[dict[str, Any]],
    model: str,
    batch_size: int,
) -> None:
    special_articles = [article for article in articles if article["post_type"] == "special"]
    if not special_articles:
        return

    for offset in range(0, len(special_articles), batch_size):
        batch = special_articles[offset : offset + batch_size]
        prompt_items = []
        for article in batch:
            prompt_items.append(
                {
                    "id": article["id"],
                    "title": article["title"],
                    "description": article["description"],
                    "summary": sanitize_text(article["summary"], 600),
                    "event_context": article["event_context"],
                    "post_type": article["post_type"],
                }
            )

        prompt = (
            "You are generating social media posts from Wikipedia data.\n"
            "Return JSON that matches the provided schema. No markdown.\n"
            "Input is a list of article objects.\n"
            "Rules:\n"
            "1) Keep each output item id unchanged.\n"
            "2) Keep post_type unchanged.\n"
            "3) Write one post per item as first-person text from that person.\n"
            "4) Every item here is special: make it about the concrete event/accomplishment.\n"
            "5) Use event_context as the core fact to announce.\n"
            "6) No hashtags, no emojis, no fabricated facts.\n"
            "7) Max 220 characters per post.\n"
            "Output format object: {\"items\": [{\"id\": number, \"post_type\": \"normal|special\", \"post_text\": \"...\"}]}\n\n"
            f"Input:\n{json.dumps(prompt_items, ensure_ascii=False)}"
        )
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "post_type": {
                                "type": "string",
                                "enum": ["normal", "special"],
                            },
                            "post_text": {"type": "string"},
                        },
                        "required": ["id", "post_type", "post_text"],
                    },
                }
            },
            "required": ["items"],
        }
        structured = ollama_generate_structured(model, prompt, schema)
        results = structured.get("items")
        if not isinstance(results, list):
            raise ValueError("Ollama output does not contain an 'items' list.")

        by_id: dict[int, dict[str, Any]] = {}
        for item in results:
            if isinstance(item, dict) and isinstance(item.get("id"), int):
                by_id[item["id"]] = item

        missing_ids = [article["id"] for article in batch if article["id"] not in by_id]
        if missing_ids:
            raise ValueError(f"LLM output missing ids: {missing_ids[:10]}")

        for article in batch:
            llm_item = by_id[article["id"]]
            text = llm_item.get("post_text")
            if not isinstance(text, str) or not text.strip():
                raise ValueError(f"LLM returned empty post_text for id={article['id']}")
            article["post_text"] = sanitize_text(text, 220)


def set_normal_post_text(articles: list[dict[str, Any]]) -> None:
    for article in articles:
        if article["post_type"] == "special":
            continue
        article["post_text"] = sanitize_text(article["summary"], 220)


def write_output(
    output_path: Path,
    model: str,
    articles: list[dict[str, Any]],
    categories_to_ids: dict[str, list[int]],
) -> None:
    posts_by_id: dict[str, dict[str, Any]] = {}
    special_count = 0

    for article in articles:
        if article["post_type"] == "special":
            special_count += 1
        article.pop("event_context", None)
        posts_by_id[str(article["id"])] = article

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "total_posts": len(articles),
        "stats": {
            "normal_posts": len(articles) - special_count,
            "special_posts": special_count,
        },
        "posts_by_id": posts_by_id,
        "categories": categories_to_ids,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Generate classified social posts from Wikipedia data with local Ollama."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=repo_root / "docs" / "data" / "social_media_categories.json",
        help="Input dataset path.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / "docs" / "data" / "social_posts.json",
        help="Output posts dataset path.",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=repo_root / "docs" / "data" / "wikipedia_summary_cache.json",
        help="Summary cache path.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="qwen3:8b",
        help="Ollama model name.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=12,
        help="Number of articles per LLM batch.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional cap on unique titles for quicker runs.",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        help="Optional random seed for reproducible random distribution across categories.",
    )
    args = parser.parse_args()

    if args.batch_size < 1:
        print("--batch-size must be 1 or greater", file=sys.stderr)
        return 1
    if args.limit is not None and args.limit < 1:
        print("--limit must be 1 or greater", file=sys.stderr)
        return 1

    with args.input.open("r", encoding="utf-8") as input_file:
        input_data = json.load(input_file)

    titles_by_category = extract_titles_by_category(input_data)
    cache = load_summary_cache(args.cache)
    articles, categories_to_ids, cache = build_articles(
        titles_by_category,
        cache,
        args.limit,
        args.random_seed,
    )
    if not articles:
        print("No articles available after preprocessing.", file=sys.stderr)
        return 1

    save_summary_cache(args.cache, cache)
    set_normal_post_text(articles)
    generate_special_posts_with_ollama(articles, args.model, args.batch_size)
    write_output(args.output, args.model, articles, categories_to_ids)

    special_count = sum(1 for article in articles if article["post_type"] == "special")
    print(
        f"Wrote {len(articles)} posts to {args.output} "
        f"(special={special_count}, normal={len(articles) - special_count})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
