const MIN_SELECTED_CATEGORIES = 3;
const FEED_BATCH_SIZE = 20;

let loadedDataset = null;
let feedCursor = 0;
let feedItems = [];
let feedObserver = null;

function formatDate(timestamp) {
    if (!timestamp) {
        return "Unknown date";
    }
    const parsed = new Date(timestamp);
    if (Number.isNaN(parsed.getTime())) {
        return timestamp;
    }
    return parsed.toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric"
    });
}

function createPostElement(item) {
    const post = item.post;
    const article = document.createElement("article");
    article.className = "post-card";

    const title = document.createElement("h3");
    title.className = "post-header";
    title.textContent = `#${post.id} - ${post.title}`;

    const metadata = document.createElement("p");
    metadata.className = "post-meta";
    metadata.textContent = formatDate(post.updated_at);

    const summary = document.createElement("p");
    summary.className = "post-summary";
    summary.textContent = post.summary;

    const tags = document.createElement("div");
    tags.className = "post-tags";
    item.matchedCategories.forEach(category => {
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = category;
        tags.appendChild(tag);
    });

    const source = document.createElement("a");
    source.className = "post-link";
    source.href = post.source;
    source.target = "_blank";
    source.rel = "noopener noreferrer";
    source.textContent = "Source";

    article.append(title, metadata, summary);
    if (item.matchedCategories.length > 0) {
        article.appendChild(tags);
    }
    article.appendChild(source);
    return article;
}

function renderSinglePost(dataset, postId) {
    const container = document.getElementById("posts");
    const postsById = dataset.articles_by_id || dataset.posts_by_id || {};
    container.innerHTML = "";
    const post = postsById[postId];
    if (!post) {
        const missing = document.createElement("p");
        missing.className = "feed-status";
        missing.textContent = `Article id ${postId} was not found.`;
        container.appendChild(missing);
        return;
    }

    container.appendChild(createPostElement({ post, matchedCategories: post.categories || [] }));
}

function buildFeedItems(dataset, selectedCategories) {
    const postsById = dataset.articles_by_id || dataset.posts_by_id || {};
    const categoryMap = dataset.categories || {};
    const selected = new Set(selectedCategories);
    const seenIds = new Set();
    const items = [];

    selectedCategories.forEach(category => {
        const ids = categoryMap[category] || [];
        ids.forEach(id => {
            const idKey = String(id);
            if (seenIds.has(idKey)) {
                return;
            }
            const post = postsById[idKey];
            if (!post) {
                return;
            }
            seenIds.add(idKey);
            items.push({
                post,
                matchedCategories: (post.categories || []).filter(postCategory => selected.has(postCategory))
            });
        });
    });

    items.sort((a, b) => {
        const left = new Date(a.post.updated_at || 0).getTime();
        const right = new Date(b.post.updated_at || 0).getTime();
        return right - left;
    });

    return items;
}

function updateFeedStatus() {
    const status = document.getElementById("feed-status");
    if (!status) {
        return;
    }
    if (feedItems.length === 0) {
        status.textContent = "No posts found for the selected categories.";
        return;
    }
    if (feedCursor >= feedItems.length) {
        status.textContent = `Showing all ${feedItems.length} posts.`;
        return;
    }
    status.textContent = `Showing ${feedCursor} of ${feedItems.length} posts. Scroll for more.`;
}

function renderNextBatch() {
    const container = document.getElementById("posts");
    const sentinel = document.getElementById("scroll-sentinel");
    if (!container || feedCursor >= feedItems.length) {
        updateFeedStatus();
        if (feedObserver) {
            feedObserver.disconnect();
            feedObserver = null;
        }
        if (sentinel) {
            sentinel.textContent = "";
        }
        return;
    }

    const nextCursor = Math.min(feedCursor + FEED_BATCH_SIZE, feedItems.length);
    for (let index = feedCursor; index < nextCursor; index += 1) {
        container.appendChild(createPostElement(feedItems[index]));
    }
    feedCursor = nextCursor;
    updateFeedStatus();
}

function initializeInfiniteScroll() {
    const sentinel = document.getElementById("scroll-sentinel");
    if (!sentinel) {
        return;
    }
    sentinel.textContent = "Loading more...";

    if (feedObserver) {
        feedObserver.disconnect();
    }

    feedObserver = new IntersectionObserver(entries => {
        const [entry] = entries;
        if (!entry || !entry.isIntersecting) {
            return;
        }
        renderNextBatch();
    }, {
        root: null,
        rootMargin: "600px 0px",
        threshold: 0.01
    });

    feedObserver.observe(sentinel);
}

function updateSelectionState(statusElement, submitButton, selectedCount) {
    statusElement.textContent = `${selectedCount} selected (minimum ${MIN_SELECTED_CATEGORIES})`;
    submitButton.disabled = selectedCount < MIN_SELECTED_CATEGORIES;
}

function renderCategoryOptions(categories, statusElement, submitButton) {
    const optionsContainer = document.getElementById("category-options");
    const selected = new Set();
    optionsContainer.innerHTML = "";

    categories.forEach(category => {
        const wrapper = document.createElement("div");
        wrapper.className = "category-option";

        const label = document.createElement("label");

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.name = "categories";
        checkbox.value = category;

        checkbox.addEventListener("change", () => {
            if (checkbox.checked) {
                selected.add(category);
            } else {
                selected.delete(category);
            }
            updateSelectionState(statusElement, submitButton, selected.size);
        });

        const text = document.createElement("span");
        text.textContent = category;

        label.append(checkbox, text);
        wrapper.appendChild(label);
        optionsContainer.appendChild(wrapper);
    });

    updateSelectionState(statusElement, submitButton, selected.size);
    return selected;
}

async function initializePage() {
    const gate = document.getElementById("category-gate");
    const content = document.getElementById("content");
    const form = document.getElementById("category-form");
    const statusElement = document.getElementById("category-status");
    const submitButton = document.getElementById("enter-button");
    const container = document.getElementById("posts");
    const params = new URLSearchParams(window.location.search);
    const queryId = params.get("id");

    const categoriesResponse = await fetch("./data/categories.json");

    const categoriesData = await categoriesResponse.json();
    const categories = categoriesData.categories || [];
    const selected = renderCategoryOptions(categories, statusElement, submitButton);

    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (selected.size < MIN_SELECTED_CATEGORIES) {
            updateSelectionState(statusElement, submitButton, selected.size);
            return;
        }

        submitButton.disabled = true;
        statusElement.textContent = "Loading posts...";

        if (!loadedDataset) {
            const postsResponse = await fetch("./data/posts.json");
            loadedDataset = await postsResponse.json();
        }

        gate.classList.add("hidden");
        content.classList.remove("hidden");

        if (!container) {
            return;
        }
        container.innerHTML = "";

        if (queryId) {
            renderSinglePost(loadedDataset, queryId);
            document.getElementById("feed-status").textContent = "";
            document.getElementById("scroll-sentinel").textContent = "";
            return;
        }

        feedItems = buildFeedItems(loadedDataset, Array.from(selected));
        feedCursor = 0;
        renderNextBatch();
        initializeInfiniteScroll();
    });
}

initializePage();
