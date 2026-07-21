#!/usr/bin/env python3

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USER_AGENT = "wikifetch/1.0 (https://github.com/)"
WIKIPEDIA_QUERY_API_ENDPOINT = "https://en.wikipedia.org/w/api.php?{}"
OLLAMA_GENERATE_ENDPOINT = "http://127.0.0.1:11434/api/generate"
REQUEST_DELAY_SECONDS = 0.1
MAX_RETRIES = 5

STOPWORD_PATTERNS = (
    r"\barticles\b",
    r"\bstubs\b",
    r"\bwikipedia\b",
    r"\bcommons\b",
    r"\btemplates\b",
    r"\bredirects\b",
    r"\bmaintenance\b",
    r"\b(1[0-9]{3}|20[0-9]{2}|2100)\b",
)

MUSEUM_EXCLUDE_PATTERNS = (
    r"\bmuseums in\b",
    r"\bmuseums by country\b",
    r"\bmuseums by city\b",
    r"\bmuseums by region\b",
    r"\bmuseums by continent\b",
    r"\bmuseums established\b",
    r"\bformer museums\b",
    r"\bdefunct museums\b",
    r"\blists of museums\b",
)

MUSEUM_NON_TYPE_PATTERNS = (
    r"\bpeople\b",
    r"\blogos\b",
    r"\bevents\b",
    r"\bcollections\b",
    r"\bdesigners\b",
    r"\bgroup\b",
    r"\bcrime\b",
    r"\bbooks\b",
    r"\bgallery\b",
    r"\bbuildings\b",
    r"\blists of\b",
)

MUSEUM_GENERIC_EXACT = {
    "planetariums",
}

BOOK_NON_TRADITIONAL_PATTERNS = (
    r"\bbooks about\b",
    r"\bbooks by\b",
    r"\bbooks in\b",
    r"\bnovels set in\b",
    r"\bfictional .+ by\b",
    r"\b\d{3,4}s\b",
    r"\bhistory of books\b",
    r"\bfictional books\b",
    r"\bpress books\b",
)

BOOK_TYPE_KEYWORDS = (
    "fiction",
    "non-fiction",
    "nonfiction",
    "mystery",
    "thriller",
    "fantasy",
    "science fiction",
    "romance",
    "historical",
    "biography",
    "autobiography",
    "history",
    "poetry",
    "children",
    "young adult",
    "self-help",
    "horror",
    "comics",
    "graphic novels",
    "reference",
)


def render_progress(label: str, current: int, total: int, width: int = 30) -> None:
    if total < 1:
        return
    bounded_current = min(max(current, 0), total)
    ratio = bounded_current / total
    filled = int(width * ratio)
    bar = "#" * filled + "-" * (width - filled)
    sys.stderr.write(f"\r{label} [{bar}] {bounded_current}/{total}")
    if bounded_current >= total:
        sys.stderr.write("\n")
    sys.stderr.flush()


def fetch_json(params: dict[str, str]) -> dict[str, Any] | None:
    url = WIKIPEDIA_QUERY_API_ENDPOINT.format(urlencode(params))
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


def normalize_topic_name(topic: str) -> str:
    normalized = re.sub(r"\s+", " ", topic.strip())
    return normalized


def topic_to_category_candidates(topic: str) -> list[str]:
    base = normalize_topic_name(topic)
    lower = base.lower()
    candidates = [f"Category:{base}"]

    if not lower.endswith("s"):
        candidates.append(f"Category:{base}s")
    if lower.endswith("y") and len(base) > 1:
        candidates.append(f"Category:{base[:-1]}ies")

    if lower == "museum":
        candidates.insert(0, "Category:Museums")
    if lower == "dinosaur":
        candidates.insert(0, "Category:Dinosauria")
        candidates.insert(1, "Category:Dinosaurs")

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def category_exists(category_title: str) -> bool:
    payload = fetch_json(
        {
            "action": "query",
            "format": "json",
            "titles": category_title,
        }
    )
    if payload is None:
        return False
    pages = payload.get("query", {}).get("pages", {})
    return any("missing" not in page for page in pages.values())


def fetch_category_search_matches(topic: str, limit: int = 20) -> list[str]:
    payload = fetch_json(
        {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": topic,
            "srnamespace": "14",
            "srlimit": str(limit),
        }
    )
    if payload is None:
        return []
    results = payload.get("query", {}).get("search", [])
    matches: list[str] = []
    for result in results:
        title = result.get("title")
        if isinstance(title, str) and title.startswith("Category:"):
            matches.append(title)
    return matches


def fetch_allcategories_prefix(topic: str, limit: int = 30) -> list[str]:
    titles: list[str] = []
    continue_token: str | None = None

    while len(titles) < limit:
        params: dict[str, str] = {
            "action": "query",
            "format": "json",
            "list": "allcategories",
            "acprefix": topic,
            "aclimit": "500",
        }
        if continue_token is not None:
            params["accontinue"] = continue_token

        payload = fetch_json(params)
        if payload is None:
            return titles

        categories = payload.get("query", {}).get("allcategories", [])
        for category in categories:
            name = category.get("*")
            if not isinstance(name, str):
                continue
            titles.append(f"Category:{name}")
            if len(titles) >= limit:
                break

        continue_token = payload.get("continue", {}).get("accontinue")
        if not continue_token:
            break

    return titles


def fetch_page_categories_for_title(title: str, limit: int = 60) -> list[str]:
    categories: list[str] = []
    continue_token: str | None = None

    while len(categories) < limit:
        params: dict[str, str] = {
            "action": "query",
            "format": "json",
            "prop": "categories",
            "titles": title,
            "cllimit": "max",
            "clshow": "!hidden",
        }
        if continue_token is not None:
            params["clcontinue"] = continue_token

        payload = fetch_json(params)
        if payload is None:
            return categories

        pages = payload.get("query", {}).get("pages", {})
        for page in pages.values():
            for category in page.get("categories", []):
                category_title = category.get("title")
                if isinstance(category_title, str) and category_title.startswith("Category:"):
                    categories.append(category_title)
                    if len(categories) >= limit:
                        return categories

        continue_token = payload.get("continue", {}).get("clcontinue")
        if not continue_token:
            break

    return categories


def get_root_categories(topic: str, max_roots: int = 10) -> list[str]:
    roots: list[str] = []
    seen: set[str] = set()

    def add_root(value: str) -> None:
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        roots.append(value)

    for candidate in topic_to_category_candidates(topic):
        if category_exists(candidate):
            add_root(candidate)
        if len(roots) >= max_roots:
            return roots

    for title in fetch_category_search_matches(topic, limit=25):
        add_root(title)
        if len(roots) >= max_roots:
            return roots

    for title in fetch_allcategories_prefix(topic, limit=35):
        add_root(title)
        if len(roots) >= max_roots:
            return roots

    for title in fetch_page_categories_for_title(topic, limit=60):
        add_root(title)
        if len(roots) >= max_roots:
            return roots

    return roots


def fetch_subcategories(category_title: str, limit: int = 500) -> list[str]:
    titles: list[str] = []
    continue_token: str | None = None

    while len(titles) < limit:
        params: dict[str, str] = {
            "action": "query",
            "format": "json",
            "list": "categorymembers",
            "cmtitle": category_title,
            "cmnamespace": "14",
            "cmtype": "subcat",
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
            if not isinstance(title, str) or not title.startswith("Category:"):
                continue
            titles.append(title)
            if len(titles) >= limit:
                break

        continue_token = payload.get("continue", {}).get("cmcontinue")
        if not continue_token:
            break

    return titles


def strip_category_prefix(category_title: str) -> str:
    if category_title.startswith("Category:"):
        return category_title[len("Category:") :]
    return category_title


def is_usable_subcategory(topic: str, value: str) -> bool:
    name = value.strip()
    if not name or len(name) < 3 or len(name) > 80:
        return False

    lowered = name.lower()
    if any(re.search(pattern, lowered) for pattern in STOPWORD_PATTERNS):
        return False

    if topic.lower() == "museum":
        if any(re.search(pattern, lowered) for pattern in MUSEUM_EXCLUDE_PATTERNS):
            return False
        if "," in name:
            return False
        if any(re.search(pattern, lowered) for pattern in MUSEUM_NON_TYPE_PATTERNS):
            return False
        is_generic_type = (
            lowered.endswith("museums")
            or " museums " in lowered
            or lowered in MUSEUM_GENERIC_EXACT
        )
        if not is_generic_type:
            return False

    if topic.lower() == "books":
        if any(re.search(pattern, lowered) for pattern in BOOK_NON_TRADITIONAL_PATTERNS):
            return False
        if any(char.isdigit() for char in lowered):
            return False
        if lowered.startswith("books ") and "fiction" not in lowered and "non-fiction" not in lowered:
            return False
        if not any(keyword in lowered for keyword in BOOK_TYPE_KEYWORDS):
            return False

    return True


def crawl_topic_subcategories(topic: str, depth: int, max_nodes: int) -> list[str]:
    root_categories = get_root_categories(topic)
    if not root_categories:
        return []

    queue: list[tuple[str, int]] = []
    queued: set[str] = set()
    for root_category in root_categories:
        queue.append((root_category, 0))
        queued.add(root_category.lower())

    visited: set[str] = set()
    usable_names: list[str] = []
    usable_seen: set[str] = set()

    while queue and len(visited) < max_nodes:
        current, current_depth = queue.pop(0)
        current_key = current.lower()
        if current_key in visited:
            continue
        visited.add(current_key)

        subcats = fetch_subcategories(current, limit=500)
        for subcat in subcats:
            key = subcat.lower()
            if current_depth < depth and key not in queued:
                queue.append((subcat, current_depth + 1))
                queued.add(key)

            plain_name = strip_category_prefix(subcat)
            if not is_usable_subcategory(topic, plain_name):
                continue
            plain_key = plain_name.lower()
            if plain_key in usable_seen:
                continue
            usable_seen.add(plain_key)
            usable_names.append(plain_name)

    return usable_names


def rank_subcategories(topic: str, categories: list[str]) -> list[str]:
    topic_words = set(re.findall(r"[a-z0-9]+", topic.lower()))

    def score(name: str) -> tuple[int, int, str]:
        lowered = name.lower()
        words = set(re.findall(r"[a-z0-9]+", lowered))
        overlap = len(topic_words.intersection(words))
        topical_boost = 1 if overlap > 0 else 0

        concise_bonus = 0
        if 10 <= len(name) <= 35:
            concise_bonus = 1

        museum_bonus = 0
        if topic.lower() == "museum":
            if "museum" in lowered or "museums" in lowered:
                museum_bonus += 1
            if any(
                token in lowered
                for token in (
                    "art",
                    "history",
                    "science",
                    "natural",
                    "children",
                    "military",
                    "maritime",
                    "aviation",
                    "railway",
                    "medical",
                    "design",
                    "open-air",
                    "university",
                    "technology",
                )
            ):
                museum_bonus += 1

        dinosaur_bonus = 0
        if topic.lower() == "dinosaur":
            if any(
                token in lowered
                for token in (
                    "dinosaur",
                    "dinosauria",
                    "theropod",
                    "sauropod",
                    "ornithischian",
                    "ceratopsian",
                    "ankylosaur",
                    "stegosaur",
                    "fossil",
                    "mesozoic",
                )
            ):
                dinosaur_bonus += 2

        total = overlap + topical_boost + concise_bonus + museum_bonus + dinosaur_bonus
        return (total, -len(name), name)

    return sorted(categories, key=score, reverse=True)


def ollama_suggest_subcategories(
    topic: str,
    current_candidates: list[str],
    min_categories: int,
    model: str,
    max_suggestions: int,
) -> list[str]:
    traditional_examples = ""
    if topic.lower() == "books":
        traditional_examples = (
            "For Books, prioritize conventional bookstore/library filters such as: "
            "Fiction, Non-fiction, Mystery, Fantasy, Science fiction, Romance, "
            "Biography, History, Self-help, Children's books, Young adult.\n"
        )

    prompt = (
        "Suggest practical, filter-friendly subcategory labels for one topic.\n"
        "Prioritize conventional, mainstream taxonomy users expect in apps.\n"
        "Return strict JSON only in this shape: {\"subcategories\": [\"...\"]}.\n"
        "Rules:\n"
        "1) No explanations.\n"
        "2) Keep each label between 3 and 60 characters.\n"
        "3) Prefer broad category types, not specific entities/places/people.\n"
        "4) Avoid 'about X', 'in country', 'by author', and list/maintenance labels.\n"
        "5) Prefer labels that can map to real Wikipedia category concepts.\n"
        f"Topic: {topic}\n"
        f"{traditional_examples}"
        f"Already found: {json.dumps(current_candidates[:80], ensure_ascii=False)}\n"
        f"Need at least {min_categories} labels total.\n"
        f"Return up to {max_suggestions} new labels."
    )
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": {
            "type": "object",
            "properties": {
                "subcategories": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            },
            "required": ["subcategories"],
        },
    }
    request = Request(
        OLLAMA_GENERATE_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            sleep(min(2**attempt, 20))
        try:
            with urlopen(request, timeout=120) as response:
                outer = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if attempt < MAX_RETRIES:
                continue
            print(f"Ollama request failed: HTTP {error.code}", file=sys.stderr)
            return []
        except URLError as error:
            if attempt < MAX_RETRIES:
                continue
            print(f"Ollama request failed: {error.reason}", file=sys.stderr)
            return []
        except TimeoutError:
            if attempt < MAX_RETRIES:
                continue
            print("Ollama request failed: timeout", file=sys.stderr)
            return []

        raw_response = outer.get("response")
        if not isinstance(raw_response, str):
            return []
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError:
            return []

        suggested = parsed.get("subcategories") if isinstance(parsed, dict) else None
        if not isinstance(suggested, list):
            return []

        cleaned: list[str] = []
        for item in suggested:
            if not isinstance(item, str):
                continue
            value = normalize_topic_name(item)
            if not value:
                continue
            cleaned.append(value)
        return cleaned

    return []


def ollama_refine_subcategories(
    topic: str,
    candidates: list[str],
    min_categories: int,
    model: str,
) -> list[str]:
    topic_note = ""
    if topic.lower() == "books":
        topic_note = (
            "For Books, prefer mainstream shelf taxonomy like Fiction, Non-fiction, "
            "Mystery, Fantasy, Romance, Science fiction, Biography, History, "
            "Children's books, Young adult.\n"
        )

    prompt = (
        "You are curating user-facing filter categories.\n"
        "Return strict JSON only: {\"subcategories\": [\"...\"]}.\n"
        "Select broad, meaningful, non-entity categories from candidates.\n"
        "Rules:\n"
        "1) Prefer traditional taxonomy users expect.\n"
        "2) Reject places, organizations, people, events, years, and 'about X' labels.\n"
        "3) Keep labels concise and reusable.\n"
        "4) Output at least the requested count if enough valid candidates exist.\n"
        f"Topic: {topic}\n"
        f"{topic_note}"
        f"Requested count: {min_categories}\n"
        f"Candidates: {json.dumps(candidates[:200], ensure_ascii=False)}"
    )

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": {
            "type": "object",
            "properties": {
                "subcategories": {
                    "type": "array",
                    "items": {"type": "string"},
                }
            },
            "required": ["subcategories"],
        },
    }
    request = Request(
        OLLAMA_GENERATE_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            sleep(min(2**attempt, 20))
        try:
            with urlopen(request, timeout=120) as response:
                outer = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError):
            if attempt < MAX_RETRIES:
                continue
            return []

        raw_response = outer.get("response")
        if not isinstance(raw_response, str):
            return []
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError:
            return []

        values = parsed.get("subcategories") if isinstance(parsed, dict) else None
        if not isinstance(values, list):
            return []

        cleaned: list[str] = []
        for item in values:
            if not isinstance(item, str):
                continue
            value = normalize_topic_name(item)
            if not value:
                continue
            cleaned.append(value)
        return cleaned

    return []


def parse_topics(payload: Any) -> list[str]:
    if isinstance(payload, list):
        return [normalize_topic_name(item) for item in payload if isinstance(item, str)]

    if not isinstance(payload, dict):
        raise ValueError("Input must be a JSON object with a 'topics' field or a list of topic names.")

    raw_topics = payload.get("topics")
    if not isinstance(raw_topics, list):
        raise ValueError("Input JSON must include a 'topics' array.")

    parsed: list[str] = []
    for item in raw_topics:
        if isinstance(item, str):
            parsed.append(normalize_topic_name(item))
            continue
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            parsed.append(normalize_topic_name(item["name"]))

    return [topic for topic in parsed if topic]


def build_output(
    topics: list[str],
    min_categories: int,
    crawl_depth: int,
    max_nodes: int,
    llm_model: str | None,
    llm_suggestions: int,
) -> dict[str, Any]:
    output_topics: list[dict[str, Any]] = []
    total_topics = len(topics)

    for index, topic in enumerate(topics, start=1):
        render_progress("Generating topics", index - 1, total_topics)
        crawled = crawl_topic_subcategories(topic, crawl_depth, max_nodes)

        llm_generated: list[str] = []
        llm_refined: list[str] = []
        if llm_model:
            llm_generated = ollama_suggest_subcategories(
                topic=topic,
                current_candidates=crawled,
                min_categories=min_categories,
                model=llm_model,
                max_suggestions=llm_suggestions,
            )
            refinement_pool: list[str] = []
            refinement_seen: set[str] = set()
            for candidate in llm_generated + crawled:
                key = candidate.lower()
                if key in refinement_seen:
                    continue
                refinement_seen.add(key)
                refinement_pool.append(candidate)
            llm_refined = ollama_refine_subcategories(
                topic=topic,
                candidates=refinement_pool,
                min_categories=min_categories,
                model=llm_model,
            )

        selected: list[str] = []
        seen: set[str] = set()

        ranked_llm = rank_subcategories(topic, llm_generated)
        ranked_refined = rank_subcategories(topic, llm_refined)
        ranked_crawled = rank_subcategories(topic, crawled)

        primary_candidates = ranked_crawled
        secondary_candidates = ranked_llm
        tertiary_candidates: list[str] = []
        if llm_model:
            primary_candidates = ranked_refined
            secondary_candidates = ranked_llm
            tertiary_candidates = ranked_crawled

        for item in primary_candidates:
            if not is_usable_subcategory(topic, item):
                continue
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            selected.append(item)
            if len(selected) >= min_categories:
                break

        for item in secondary_candidates:
            if not is_usable_subcategory(topic, item):
                continue
            if len(selected) >= min_categories:
                break
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            selected.append(item)

        for item in tertiary_candidates:
            if not is_usable_subcategory(topic, item):
                continue
            if len(selected) >= min_categories:
                break
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            selected.append(item)

        if len(selected) < min_categories:
            print(
                f"Warning: topic '{topic}' only produced {len(selected)} subcategories.",
                file=sys.stderr,
            )

        output_topics.append(
            {
                "name": topic,
                "subcategories": selected,
                "subcategory_count": len(selected),
                "source_stats": {
                    "crawled_candidates": len(crawled),
                    "llm_candidates": len(llm_generated),
                    "llm_refined_candidates": len(llm_refined),
                    "llm_selected": len([value for value in selected if value in llm_generated]),
                    "llm_refined_selected": len([value for value in selected if value in llm_refined]),
                    "crawl_selected": len([value for value in selected if value in crawled]),
                },
            }
        )
        render_progress("Generating topics", index, total_topics)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "min_categories": min_categories,
        "topic_count": len(output_topics),
        "topics": output_topics,
    }


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Generate usable topic subcategories from broad topics in JSON input."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=repo_root / "input" / "topics.json",
        help="Input JSON path containing topics.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / "docs" / "data" / "topic_subcategories.json",
        help="Output JSON path.",
    )
    parser.add_argument(
        "--min-categories",
        type=int,
        default=15,
        help="Minimum number of subcategories to output per topic.",
    )
    parser.add_argument(
        "--crawl-depth",
        type=int,
        default=1,
        help="How many levels of category tree to traverse from each root topic.",
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=120,
        help="Max number of category nodes to traverse per topic.",
    )
    parser.add_argument(
        "--llm-model",
        type=str,
        help="Optional local Ollama model for extra subcategory suggestions.",
    )
    parser.add_argument(
        "--llm-suggestions",
        type=int,
        default=30,
        help="Max number of LLM-generated candidate subcategories per topic.",
    )
    args = parser.parse_args()

    if args.min_categories < 1:
        print("--min-categories must be 1 or greater", file=sys.stderr)
        return 1
    if args.crawl_depth < 0:
        print("--crawl-depth must be 0 or greater", file=sys.stderr)
        return 1
    if args.max_nodes < 1:
        print("--max-nodes must be 1 or greater", file=sys.stderr)
        return 1
    if args.llm_suggestions < 1:
        print("--llm-suggestions must be 1 or greater", file=sys.stderr)
        return 1

    if not args.input.exists():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 1

    with args.input.open("r", encoding="utf-8") as input_file:
        input_payload = json.load(input_file)

    topics = parse_topics(input_payload)
    if not topics:
        print("No valid topic names found in input.", file=sys.stderr)
        return 1

    output_payload = build_output(
        topics=topics,
        min_categories=args.min_categories,
        crawl_depth=args.crawl_depth,
        max_nodes=args.max_nodes,
        llm_model=args.llm_model,
        llm_suggestions=args.llm_suggestions,
    )

    output_path = args.output
    if output_path.resolve() == args.input.resolve():
        print("--output must be different from --input", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        json.dump(output_payload, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")

    print(f"Wrote subcategories for {len(topics)} topic(s) to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
