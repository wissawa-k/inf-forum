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
