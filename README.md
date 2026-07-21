# INF Forum

INF Forum is a lightweight, feed-based learning experience that presents educational content in the familiar format of a social media timeline. Instead of long-form reading, users discover short, engaging posts across topics like science, history, and technology—designed to encourage curiosity through quick, continuous exploration.

## Core Idea

- Turn the addictive scrolling pattern of modern platforms into a tool for learning
- Deliver bite-sized knowledge that’s easy to consume and revisit
- Personalize content through user-selected categories

## How It Works

- Users select at least 3 categories to enter the app
- A personalized feed is generated from those interests
- Posts are shown in an infinite scroll format, similar to social platforms

## Strong Points

- Familiar UX: Uses a feed-based interface users already understand
- Low friction learning: No sign-ups, no barriers—just select topics and start
- Personalized content: Category-based filtering shapes each user’s experience
- Lightweight performance: Minimal JS, no heavy frameworks

## Fully Static Architecture

INF Forum is a fully static site:

- No backend server required
- Data is served from JSON files
- User state is stored locally in the browser (localStorage)
- Easy and cheap to deploy (GitHub Pages, Netlify, etc.)

This makes the project highly portable, scalable, and resilient, while still delivering a dynamic, app-like experience.

## Goal

To make learning feel as natural and engaging as scrolling—transforming passive browsing time into moments of discovery.

## One-File Configuration

You can configure and run the full fetch pipeline from one root file:

- Config file: `fetch.config.json`
- Runner command: `python3 fetch.py`

Basic flow:

1. Edit `fetch.config.json`:
   - `project.title` for app title
   - `topics` for your broad topic list
   - `pipeline` for crawl/model/output behavior
2. Run `python3 fetch.py`

Useful commands:

- `python3 fetch.py --dry-run` to print the generated pipeline command
- `python3 fetch.py --manual` for quick usage help

The runner writes:

- Topic input JSON to `input/topics.json` (or configured path)
- Site config JSON to `docs/data/site_config.json` (or configured path)
- Final posts JSON to `docs/data/posts.json` (or configured path)
