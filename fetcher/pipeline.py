#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter, sleep
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USER_AGENT = "wikifetch/1.0 (https://github.com/)"
WIKIPEDIA_QUERY_API_ENDPOINT = "https://en.wikipedia.org/w/api.php?{}"
REQUEST_DELAY_SECONDS = 0.08
MAX_RETRIES = 5

STAGE_ORDER = [
    "topic-subcategories",
    "category-title-expansion",
    "generate-posts",
    "dedupe-posts",
    "recategorize-posts",
    "popularity-enrichment",
]

STAGE_ALIAS_TO_NAME = {
    "topic": "topic-subcategories",
    "expand": "category-title-expansion",
    "generate": "generate-posts",
    "dedupe": "dedupe-posts",
    "recategorize": "recategorize-posts",
    "popularity": "popularity-enrichment",
}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def ollama_is_available(timeout_seconds: float = 2.0) -> bool:
    request = Request("http://127.0.0.1:11434/api/tags", method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds):
            return True
    except URLError:
        return False


def print_manual(parser: argparse.ArgumentParser, repo_root: Path) -> None:
    print("INF Forum Pipeline Control Center")
    print("================================")
    print("")
    print("Quick start (full run):")
    print("  python3 fetcher/pipeline.py --full --overwrite")
    print("")
    print("Run specific stage(s):")
    print("  python3 fetcher/pipeline.py --stage topic --overwrite")
    print("  python3 fetcher/pipeline.py --stage expand --overwrite")
    print("  python3 fetcher/pipeline.py --stage generate --stage dedupe --overwrite")
    print("  python3 fetcher/pipeline.py --stage popularity --overwrite")
    print("")
    print("Stage aliases:")
    print("- topic -> generate topic subcategories")
    print("- expand -> fetch article titles for each generated subcategory")
    print("- generate -> build posts from titles with Ollama")
    print("- dedupe -> remove duplicate posts")
    print("- recategorize -> optional category correction pass")
    print("- popularity -> add view_count / like_count")
    print("")
    print("Important behavior:")
    print("- Recategorization is skipped by default in --full.")
    print("- Add --with-recategorize with --full to include it.")
    print("- By default, topic subcategories use local LLM suggestions via --model.")
    print("- Add --no-llm-subcategories to disable LLM for topic stage.")
    print("- No hardcoded fallback categories are injected.")
    print("- Existing outputs are protected unless --overwrite is passed.")
    print("")
    print("Pipeline artifacts:")
    print(f"- {repo_root / 'docs' / 'data' / 'topic_subcategories.json'}")
    print(f"- {repo_root / 'docs' / 'data' / 'social_media_categories.json'}")
    print(f"- {repo_root / 'docs' / 'data' / 'posts.pipeline.json'}")
    print(f"- {repo_root / 'docs' / 'data' / 'posts.json'}")
    print(f"- {repo_root / 'docs' / 'data' / 'pipeline_trace.json'}")
    print("")
    parser.print_help()


def render_progress(label: str, current: int, total: int, detail: str = "") -> None:
    if total < 1:
        return
    ratio = min(max(current / total, 0.0), 1.0)
    width = 32
    filled = int(width * ratio)
    bar = "#" * filled + "-" * (width - filled)
    suffix = f" {detail}" if detail else ""
    print(
        f"\r{label} [{bar}] {current}/{total} ({ratio * 100:5.1f}%){suffix}",
        end="",
        file=sys.stderr,
        flush=True,
    )
    if current >= total:
        print("", file=sys.stderr)


def fetch_json(params: dict[str, str]) -> dict[str, Any] | None:
    url = WIKIPEDIA_QUERY_API_ENDPOINT.format(urlencode(params))
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
            print(f"Request failed for '{url}': HTTP {error.code}", file=sys.stderr)
            return None
        except URLError as error:
            if attempt < MAX_RETRIES:
                continue
            print(f"Request failed for '{url}': {error.reason}", file=sys.stderr)
            return None

    return None


def fetch_category_page_titles(category_name: str, limit: int) -> list[str]:
    category_title = (
        category_name if category_name.startswith("Category:") else f"Category:{category_name}"
    )
    titles: list[str] = []
    continue_token: str | None = None

    while len(titles) < limit:
        params: dict[str, str] = {
            "action": "query",
            "format": "json",
            "list": "categorymembers",
            "cmtitle": category_title,
            "cmnamespace": "0",
            "cmtype": "page",
            "cmlimit": "500",
        }
        if continue_token:
            params["cmcontinue"] = continue_token

        payload = fetch_json(params)
        if payload is None:
            return titles

        members = payload.get("query", {}).get("categorymembers", [])
        for member in members:
            title = member.get("title")
            if not isinstance(title, str) or ":" in title:
                continue
            titles.append(title)
            if len(titles) >= limit:
                break

        continue_token = payload.get("continue", {}).get("cmcontinue")
        if not continue_token:
            break

    return titles


def extract_subcategories(topic_payload: dict[str, Any]) -> list[str]:
    topics = topic_payload.get("topics")
    if not isinstance(topics, list):
        return []

    collected: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        subcategories = topic.get("subcategories")
        if not isinstance(subcategories, list):
            continue
        for subcategory in subcategories:
            if not isinstance(subcategory, str):
                continue
            value = subcategory.strip()
            if not value:
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            collected.append(value)
    return collected


def build_social_categories_from_subcategories(
    subcategories: list[str],
    per_category_limit: int,
) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {}
    total = len(subcategories)

    for index, subcategory in enumerate(subcategories, start=1):
        render_progress(
            "Expanding categories",
            index - 1,
            total,
            detail=f"{subcategory[:36]}",
        )
        titles = fetch_category_page_titles(subcategory, per_category_limit)
        if titles:
            categories[subcategory] = titles
        render_progress(
            "Expanding categories",
            index,
            total,
            detail=f"{subcategory[:36]} titles={len(titles)}",
        )

    return categories


def run_command(command: list[str], stage_name: str) -> None:
    print(f"\n[stage] {stage_name}", file=sys.stderr)
    print(f"[cmd] {' '.join(command)}", file=sys.stderr)
    subprocess.run(command, check=True)


def require_file(path: Path, help_text: str) -> None:
    if path.exists():
        return
    print(f"Missing required file: {path}", file=sys.stderr)
    print(help_text, file=sys.stderr)
    raise SystemExit(1)


def ensure_writable_outputs(paths: list[Path], overwrite: bool) -> None:
    if overwrite:
        return
    existing = [path for path in paths if path.exists()]
    if not existing:
        return
    print("Refusing to overwrite existing files without --overwrite:", file=sys.stderr)
    for path in existing:
        print(f"- {path}", file=sys.stderr)
    raise SystemExit(1)


def stage_topic_subcategories(args: argparse.Namespace, repo_root: Path, output_path: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(repo_root / "fetcher" / "generate_topic_subcategories.py"),
        "--input",
        str(args.input),
        "--output",
        str(output_path),
        "--min-categories",
        str(args.min_categories),
        "--crawl-depth",
        str(args.crawl_depth),
        "--max-nodes",
        str(args.max_nodes),
        "--llm-suggestions",
        str(args.llm_suggestions),
    ]
    if args.effective_llm_model:
        command.extend(["--llm-model", args.effective_llm_model])

    run_command(command, stage_name="Generate topic subcategories")
    return {"output": str(output_path)}


def stage_category_title_expansion(
    args: argparse.Namespace,
    topic_subcategories_path: Path,
    social_categories_path: Path,
    categories_path: Path,
) -> dict[str, Any]:
    require_file(
        topic_subcategories_path,
        "Run with --stage topic first, or run --full.",
    )

    with topic_subcategories_path.open("r", encoding="utf-8") as subcat_file:
        topic_payload = json.load(subcat_file)
    if not isinstance(topic_payload, dict):
        print("topic_subcategories.json must be a JSON object.", file=sys.stderr)
        raise SystemExit(1)

    subcategories = extract_subcategories(topic_payload)
    if not subcategories:
        print("No subcategories found after topic expansion.", file=sys.stderr)
        raise SystemExit(1)

    categories_to_titles = build_social_categories_from_subcategories(
        subcategories,
        args.titles_per_category,
    )
    if not categories_to_titles:
        print("No article titles were fetched from generated subcategories.", file=sys.stderr)
        raise SystemExit(1)

    total_titles = sum(len(titles) for titles in categories_to_titles.values())
    social_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "topic_pipeline",
        "topic_input": str(args.input),
        "topic_subcategories_file": str(topic_subcategories_path),
        "category_count": len(categories_to_titles),
        "total_titles": total_titles,
        "categories": categories_to_titles,
    }
    write_json(social_categories_path, social_payload)
    write_json(categories_path, {"categories": sorted(categories_to_titles.keys())})
    return {
        "output": str(social_categories_path),
        "category_count": len(categories_to_titles),
        "total_titles": total_titles,
    }


def stage_generate_posts(
    args: argparse.Namespace,
    repo_root: Path,
    social_categories_path: Path,
    working_posts_path: Path,
    data_dir: Path,
) -> dict[str, Any]:
    require_file(
        social_categories_path,
        "Run with --stage expand first, or run --full.",
    )

    command = [
        sys.executable,
        str(repo_root / "fetcher" / "generate_social_posts.py"),
        "--input",
        str(social_categories_path),
        "--output",
        str(working_posts_path),
        "--cache",
        str(data_dir / "wikipedia_summary_cache.json"),
        "--model",
        args.model,
        "--batch-size",
        str(args.batch_size),
    ]
    if args.limit is not None:
        command.extend(["--limit", str(args.limit)])
    if args.random_seed is not None:
        command.extend(["--random-seed", str(args.random_seed)])

    run_command(command, stage_name="Generate social posts")
    return {"output": str(working_posts_path)}


def stage_dedupe_posts(repo_root: Path, working_posts_path: Path) -> dict[str, Any]:
    require_file(
        working_posts_path,
        "Run with --stage generate first, or run --full.",
    )
    run_command(
        [
            sys.executable,
            str(repo_root / "fetcher" / "dedupe_posts.py"),
            "--input",
            str(working_posts_path),
            "--output",
            str(working_posts_path),
        ],
        stage_name="Dedupe posts",
    )
    return {"output": str(working_posts_path)}


def stage_recategorize_posts(
    repo_root: Path,
    working_posts_path: Path,
    categories_path: Path,
    data_dir: Path,
) -> dict[str, Any]:
    require_file(
        working_posts_path,
        "Run with --stage generate first, or run --full.",
    )
    require_file(
        categories_path,
        "Run with --stage expand first, or run --full.",
    )
    run_command(
        [
            sys.executable,
            str(repo_root / "fetcher" / "recategorize_posts.py"),
            "--input",
            str(working_posts_path),
            "--output",
            str(working_posts_path),
            "--categories-file",
            str(categories_path),
            "--cache",
            str(data_dir / "wiki_category_cache.json"),
        ],
        stage_name="Recategorize posts",
    )
    return {"output": str(working_posts_path)}


def stage_popularity_enrichment(
    args: argparse.Namespace,
    repo_root: Path,
    working_posts_path: Path,
    data_dir: Path,
) -> dict[str, Any]:
    require_file(
        working_posts_path,
        "Run with --stage generate first, or run --full.",
    )
    run_command(
        [
            sys.executable,
            str(repo_root / "fetcher" / "fetch_post_popularity.py"),
            "--input",
            str(working_posts_path),
            "--output",
            str(args.output),
            "--cache",
            str(data_dir / "wiki_popularity_cache.json"),
            "--days",
            str(args.days),
        ],
        stage_name="Fetch popularity",
    )
    return {"output": str(args.output)}


def selected_stages(args: argparse.Namespace) -> list[str]:
    if args.full:
        names = [
            "topic-subcategories",
            "category-title-expansion",
            "generate-posts",
            "dedupe-posts",
        ]
        if args.with_recategorize:
            names.append("recategorize-posts")
        names.append("popularity-enrichment")
        return names

    if not args.stage:
        return []

    selected: set[str] = set()
    for alias in args.stage:
        selected.add(STAGE_ALIAS_TO_NAME[alias])

    ordered = [name for name in STAGE_ORDER if name in selected]
    return ordered


def outputs_for_stages(
    stages: list[str],
    topic_subcategories_path: Path,
    social_categories_path: Path,
    categories_path: Path,
    working_posts_path: Path,
    final_output_path: Path,
) -> list[Path]:
    paths: list[Path] = []
    if "topic-subcategories" in stages:
        paths.append(topic_subcategories_path)
    if "category-title-expansion" in stages:
        paths.append(social_categories_path)
        paths.append(categories_path)
    if "generate-posts" in stages:
        paths.append(working_posts_path)
    if "dedupe-posts" in stages:
        paths.append(working_posts_path)
    if "recategorize-posts" in stages:
        paths.append(working_posts_path)
    if "popularity-enrichment" in stages:
        paths.append(final_output_path)

    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    data_dir = repo_root / "docs" / "data"

    parser = argparse.ArgumentParser(
        description="Unified pipeline control center for topic->posts generation."
    )
    parser.add_argument("--full", action="store_true", help="Run full end-to-end pipeline.")
    parser.add_argument(
        "--stage",
        action="append",
        choices=sorted(STAGE_ALIAS_TO_NAME.keys()),
        help="Run specific stage alias; pass multiple times for a sequence.",
    )
    parser.add_argument("--manual", action="store_true", help="Show usage manual.")
    parser.add_argument(
        "--input",
        type=Path,
        default=repo_root / "input" / "topics.json",
        help="Input topic JSON file.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=data_dir / "posts.json",
        help="Final posts output path.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow overwriting output artifacts.",
    )
    parser.add_argument(
        "--with-recategorize",
        action="store_true",
        help="Include recategorization during --full run.",
    )
    parser.add_argument("--min-categories", type=int, default=15)
    parser.add_argument("--crawl-depth", type=int, default=1)
    parser.add_argument("--max-nodes", type=int, default=120)
    parser.add_argument("--titles-per-category", type=int, default=300)
    parser.add_argument("--model", type=str, default="qwen3:8b")
    parser.add_argument(
        "--llm-model",
        type=str,
        help="Optional Ollama model for subcategory suggestion in topic stage.",
    )
    parser.add_argument(
        "--no-llm-subcategories",
        action="store_true",
        help="Disable LLM suggestions for topic subcategories.",
    )
    parser.add_argument("--llm-suggestions", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--random-seed", type=int)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    if args.manual or len(sys.argv) == 1:
        print_manual(parser, repo_root)
        return 0

    if args.full and args.stage:
        print("Use either --full or --stage, not both.", file=sys.stderr)
        return 1

    stages = selected_stages(args)
    if not stages:
        print("No stages selected. Use --full or --stage. Use --manual for help.", file=sys.stderr)
        return 1

    if args.min_categories < 1:
        print("--min-categories must be 1 or greater", file=sys.stderr)
        return 1
    if args.crawl_depth < 0:
        print("--crawl-depth must be 0 or greater", file=sys.stderr)
        return 1
    if args.max_nodes < 1:
        print("--max-nodes must be 1 or greater", file=sys.stderr)
        return 1
    if args.titles_per_category < 1:
        print("--titles-per-category must be 1 or greater", file=sys.stderr)
        return 1
    if args.batch_size < 1:
        print("--batch-size must be 1 or greater", file=sys.stderr)
        return 1
    if args.llm_suggestions < 1:
        print("--llm-suggestions must be 1 or greater", file=sys.stderr)
        return 1
    if args.days < 1:
        print("--days must be 1 or greater", file=sys.stderr)
        return 1

    args.effective_llm_model = None
    if not args.no_llm_subcategories:
        preferred_model = args.llm_model if args.llm_model else args.model
        if ollama_is_available():
            args.effective_llm_model = preferred_model
        else:
            print(
                "Ollama is not reachable at http://127.0.0.1:11434; "
                "topic stage will skip LLM suggestions.",
                file=sys.stderr,
            )

    topic_subcategories_path = data_dir / "topic_subcategories.json"
    social_categories_path = data_dir / "social_media_categories.json"
    categories_path = data_dir / "categories.json"
    working_posts_path = data_dir / "posts.pipeline.json"
    trace_path = data_dir / "pipeline_trace.json"

    if "topic-subcategories" in stages and not args.input.exists():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 1

    ensure_writable_outputs(
        outputs_for_stages(
            stages,
            topic_subcategories_path,
            social_categories_path,
            categories_path,
            working_posts_path,
            args.output,
        ),
        args.overwrite,
    )

    trace: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "full": args.full,
        "stages_requested": stages,
        "overwrite": args.overwrite,
        "with_recategorize": args.with_recategorize,
        "stages": [],
    }

    try:
        total = len(stages)
        for index, stage_name in enumerate(stages, start=1):
            render_progress("Pipeline", index - 1, total, detail=stage_name)
            started = perf_counter()

            if stage_name == "topic-subcategories":
                meta = stage_topic_subcategories(args, repo_root, topic_subcategories_path)
            elif stage_name == "category-title-expansion":
                meta = stage_category_title_expansion(
                    args,
                    topic_subcategories_path,
                    social_categories_path,
                    categories_path,
                )
            elif stage_name == "generate-posts":
                meta = stage_generate_posts(
                    args,
                    repo_root,
                    social_categories_path,
                    working_posts_path,
                    data_dir,
                )
            elif stage_name == "dedupe-posts":
                meta = stage_dedupe_posts(repo_root, working_posts_path)
            elif stage_name == "recategorize-posts":
                meta = stage_recategorize_posts(
                    repo_root,
                    working_posts_path,
                    categories_path,
                    data_dir,
                )
            else:
                meta = stage_popularity_enrichment(
                    args,
                    repo_root,
                    working_posts_path,
                    data_dir,
                )

            trace["stages"].append(
                {
                    "name": stage_name,
                    "duration_seconds": round(perf_counter() - started, 3),
                    **meta,
                }
            )
            render_progress("Pipeline", index, total, detail=f"{stage_name} done")

        if "popularity-enrichment" in stages and args.output.exists():
            with args.output.open("r", encoding="utf-8") as final_file:
                final_payload = json.load(final_file)
            if isinstance(final_payload, dict):
                total_posts = final_payload.get("total_posts")
                if isinstance(total_posts, int):
                    trace["final_total_posts"] = total_posts

    except subprocess.CalledProcessError as error:
        trace["failed"] = True
        trace["error"] = f"command failed with exit code {error.returncode}"
        trace["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(trace_path, trace)
        return error.returncode

    trace["failed"] = False
    trace["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(trace_path, trace)
    print(f"\nPipeline complete. Ran {len(stages)} stage(s).")
    print(f"Trace written to: {trace_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
