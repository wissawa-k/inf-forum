#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")


def resolve_repo_path(repo_root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return repo_root / path


def parse_topics(raw_topics: Any) -> list[str]:
    if not isinstance(raw_topics, list):
        raise ValueError("Config field 'topics' must be an array of strings.")

    parsed: list[str] = []
    for value in raw_topics:
        if not isinstance(value, str):
            continue
        cleaned = " ".join(value.strip().split())
        if cleaned:
            parsed.append(cleaned)
    if not parsed:
        raise ValueError("Config 'topics' must include at least one non-empty topic name.")
    return parsed


def as_int(value: Any, key: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Config '{key}' must be an integer.")
    if isinstance(value, int):
        return value
    raise ValueError(f"Config '{key}' must be an integer.")


def as_bool(value: Any, key: str, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValueError(f"Config '{key}' must be true/false.")


def as_str(value: Any, key: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else None
    raise ValueError(f"Config '{key}' must be a string.")


def build_pipeline_command(
    repo_root: Path,
    config: dict[str, Any],
    topics_input_path: Path,
    output_posts_path: Path,
) -> list[str]:
    pipeline_cfg = config.get("pipeline", {})
    if not isinstance(pipeline_cfg, dict):
        raise ValueError("Config field 'pipeline' must be a JSON object.")

    mode = as_str(pipeline_cfg.get("mode"), "pipeline.mode") or "full"
    if mode not in {"full", "stages"}:
        raise ValueError("Config 'pipeline.mode' must be 'full' or 'stages'.")

    command = [
        sys.executable,
        str(repo_root / "fetcher" / "pipeline.py"),
        "--input",
        str(topics_input_path),
        "--output",
        str(output_posts_path),
    ]

    if mode == "full":
        command.append("--full")
    else:
        stages = pipeline_cfg.get("stages")
        if not isinstance(stages, list) or not stages:
            raise ValueError("Config 'pipeline.stages' must be a non-empty array when mode='stages'.")
        for stage in stages:
            if not isinstance(stage, str):
                continue
            stage_name = stage.strip().lower()
            if stage_name:
                command.extend(["--stage", stage_name])

    if as_bool(pipeline_cfg.get("overwrite"), "pipeline.overwrite", default=False):
        command.append("--overwrite")
    if as_bool(pipeline_cfg.get("with_recategorize"), "pipeline.with_recategorize", default=False):
        command.append("--with-recategorize")
    if as_bool(
        pipeline_cfg.get("no_llm_subcategories"),
        "pipeline.no_llm_subcategories",
        default=False,
    ):
        command.append("--no-llm-subcategories")

    int_flags = {
        "min_categories": "--min-categories",
        "crawl_depth": "--crawl-depth",
        "max_nodes": "--max-nodes",
        "titles_per_category": "--titles-per-category",
        "llm_suggestions": "--llm-suggestions",
        "batch_size": "--batch-size",
        "limit": "--limit",
        "random_seed": "--random-seed",
        "days": "--days",
    }
    for key, flag in int_flags.items():
        value = as_int(pipeline_cfg.get(key), f"pipeline.{key}")
        if value is not None:
            command.extend([flag, str(value)])

    model = as_str(pipeline_cfg.get("model"), "pipeline.model")
    if model:
        command.extend(["--model", model])

    llm_model = as_str(pipeline_cfg.get("llm_model"), "pipeline.llm_model")
    if llm_model:
        command.extend(["--llm-model", llm_model])

    return command


def print_manual(default_config_path: Path) -> None:
    print("Fetch Runner")
    print("============")
    print("")
    print("1) Edit root config file:")
    print(f"   {default_config_path}")
    print("2) Run:")
    print("   python3 fetch.py")
    print("")
    print("Optional:")
    print("- python3 fetch.py --dry-run")
    print("- python3 fetch.py --config path/to/config.json")


def main() -> int:
    repo_root = Path(__file__).resolve().parent
    default_config_path = repo_root / "fetch.config.json"

    parser = argparse.ArgumentParser(
        description="Run the topic-to-posts pipeline from one root config file."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path,
        help="Path to fetch config JSON file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated pipeline command without running it.",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Show quick usage manual.",
    )
    args = parser.parse_args()

    if args.manual:
        print_manual(default_config_path)
        return 0

    config_path = args.config if args.config.is_absolute() else repo_root / args.config
    if not config_path.exists():
        print(f"Config file not found: {config_path}", file=sys.stderr)
        print("Run with --manual for setup instructions.", file=sys.stderr)
        return 1

    with config_path.open("r", encoding="utf-8") as config_file:
        raw_config = json.load(config_file)
    if not isinstance(raw_config, dict):
        print("Config must be a JSON object.", file=sys.stderr)
        return 1

    try:
        title = "INF Forum"
        project_cfg = raw_config.get("project", {})
        if isinstance(project_cfg, dict):
            configured_title = as_str(project_cfg.get("title"), "project.title")
            if configured_title:
                title = configured_title

        topics = parse_topics(raw_config.get("topics"))

        paths_cfg = raw_config.get("paths", {})
        if not isinstance(paths_cfg, dict):
            raise ValueError("Config field 'paths' must be a JSON object.")

        topics_input_path = resolve_repo_path(
            repo_root,
            as_str(paths_cfg.get("topics_input"), "paths.topics_input") or "input/topics.json",
        )
        output_posts_path = resolve_repo_path(
            repo_root,
            as_str(paths_cfg.get("output_posts"), "paths.output_posts")
            or "docs/data/posts.json",
        )
        site_config_path = resolve_repo_path(
            repo_root,
            as_str(paths_cfg.get("site_config"), "paths.site_config")
            or "docs/data/site_config.json",
        )

        write_json(topics_input_path, {"topics": topics})
        write_json(
            site_config_path,
            {
                "title": title,
                "topics": topics,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        command = build_pipeline_command(
            repo_root=repo_root,
            config=raw_config,
            topics_input_path=topics_input_path,
            output_posts_path=output_posts_path,
        )
    except ValueError as error:
        print(f"Config error: {error}", file=sys.stderr)
        return 1

    print(f"Prepared topics input: {topics_input_path}")
    print(f"Prepared site config: {site_config_path}")
    print(f"Running command: {' '.join(command)}")

    if args.dry_run:
        return 0

    subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
