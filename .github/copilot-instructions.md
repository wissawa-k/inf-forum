# Copilot Instructions for `inf-forum`

## Build, test, and lint commands

This repository has no configured build system or linter. The app is static frontend + Python data pipeline scripts.

| Task | Command |
| --- | --- |
| Run frontend locally (static files in `docs/`) | `python3 -m http.server 8000 --directory docs` |
| Quick API smoke test (single script) | `python3 fetcher/test.py` |
| Generate seed category graph from Wikipedia | `python3 fetcher/fetch_social_categories.py` |
| Generate posts with local Ollama (full run) | `python3 fetcher/generate_social_posts.py --input docs/data/social_media_categories.json --output docs/data/posts.json` |
| Generate a smaller single-run sample (fast iteration) | `python3 fetcher/generate_social_posts.py --limit 1 --batch-size 1 --input docs/data/social_media_categories.json --output docs/data/posts.json` |
| Check duplicates in dataset (check-only mode) | `python3 fetcher/dedupe_posts.py --check --input docs/data/posts.json` |
| Check category quality on a single post | `python3 fetcher/recategorize_posts.py --check --max-posts 1 --input docs/data/posts.json --categories-file docs/data/categories.json` |
| Refresh popularity for one post | `python3 fetcher/fetch_post_popularity.py --max-posts 1 --input docs/data/posts.json --output docs/data/posts.json` |

## High-level architecture

The repository has two coupled parts:

1. `fetcher/*.py`: offline data generation and enrichment pipeline that produces JSON in `docs/data/`.
2. `docs/`: static client (`index.html`, `app.js`, `styles.css`) that reads `docs/data/categories.json` and `docs/data/posts.json` at runtime.

Data flow is:

1. Category/topic discovery (`fetch_social_categories.py`) → `docs/data/social_media_categories.json`.
2. Post generation/classification (`generate_social_posts.py`) → `docs/data/posts.json` (or `social_posts.json` if default output is used).
3. Optional cleanup/enrichment passes over `posts.json`: `dedupe_posts.py`, `recategorize_posts.py`, `fetch_post_popularity.py`.
4. Frontend loads `posts.json`, builds category-filtered feed, and tracks user profile + stats in browser `localStorage` (`PROFILE_STORAGE_KEY = "inf-forum-profile-v1"` in `docs/app.js`).

## Key conventions in this codebase

1. **Dataset shape is ID-indexed, not list-based**: posts are stored as `posts_by_id` (or legacy `articles_by_id`) object maps keyed by string IDs, and category indexes are `categories: { "<category>": [<int post ids>] }`.
2. **Scripts support both schema variants**: several scripts intentionally accept either `posts_by_id` or `articles_by_id` for compatibility; preserve this behavior when editing loaders/writers.
3. **Category names are normalized lowercase labels** shared across pipeline and frontend (`technology`, `science`, etc.); avoid introducing mixed-case or alternate spellings.
4. **Network calls follow retry/backoff + explicit stderr reporting** in fetcher scripts; keep this failure-handling style when adding new external requests.
5. **CLI-first pipeline design**: each fetcher script is an executable CLI with `argparse`, sensible repo-relative defaults (`Path(__file__).resolve().parents[1]`), and explicit non-zero exits on invalid args/check failures.
