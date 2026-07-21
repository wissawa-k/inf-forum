const MIN_SELECTED_CATEGORIES = 3;
const FEED_BATCH_SIZE = 20;
const PROFILE_STORAGE_KEY = "inf-forum-profile-v1";
const STATS_FLUSH_INTERVAL_MS = 15000;

let loadedDataset = null;
let feedCursor = 0;
let feedItems = [];
let feedObserver = null;
let profile = null;
let seenPostIds = new Set();
let sessionLastTickMs = Date.now();
let sessionCarryMs = 0;
let statsFlushTimer = null;
let hasEnteredFeed = false;
let feedCategorySelection = new Set();
let profileCategorySelection = new Set();
let currentView = "gate";
let requestedPostId = null;

function formatCount(value) {
    return new Intl.NumberFormat("en-US").format(Math.max(0, Number(value) || 0));
}

function formatDuration(totalSeconds) {
    const seconds = Math.max(0, Number(totalSeconds) || 0);
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    if (hours > 0) {
        return `${hours}h ${minutes}m ${secs}s`;
    }
    return `${minutes}m ${secs}s`;
}

function createDefaultProfile() {
    return {
        preferred_categories: [],
        stats: {
            seen_post_ids: [],
            posts_seen_count: 0,
            total_time_seconds: 0,
            sessions: 0,
            last_active_at: "",
        },
    };
}

function loadProfile() {
    const fallback = createDefaultProfile();
    try {
        const raw = window.localStorage.getItem(PROFILE_STORAGE_KEY);
        if (!raw) {
            return fallback;
        }
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object") {
            return fallback;
        }
        const preferredCategories = Array.isArray(parsed.preferred_categories)
            ? parsed.preferred_categories.filter(value => typeof value === "string")
            : [];
        const stats = parsed.stats && typeof parsed.stats === "object"
            ? parsed.stats
            : {};
        const seenIds = Array.isArray(stats.seen_post_ids)
            ? stats.seen_post_ids.map(value => String(value))
            : [];
        return {
            preferred_categories: Array.from(new Set(preferredCategories)),
            stats: {
                seen_post_ids: Array.from(new Set(seenIds)),
                posts_seen_count: Math.max(0, Number(stats.posts_seen_count) || 0),
                total_time_seconds: Math.max(0, Number(stats.total_time_seconds) || 0),
                sessions: Math.max(0, Number(stats.sessions) || 0),
                last_active_at: typeof stats.last_active_at === "string" ? stats.last_active_at : "",
            },
        };
    } catch {
        return fallback;
    }
}

function saveProfile() {
    profile.stats.seen_post_ids = Array.from(seenPostIds);
    profile.stats.posts_seen_count = profile.stats.seen_post_ids.length;
    profile.stats.last_active_at = new Date().toISOString();
    try {
        window.localStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(profile));
    } catch {
        // Ignore storage failures so rendering still works.
    }
}

function ensureStatsElementsUpdated() {
    const postsSeen = document.getElementById("profile-posts-seen");
    const timeOnSite = document.getElementById("profile-time-on-site");
    const sessions = document.getElementById("profile-sessions");
    if (postsSeen) {
        postsSeen.textContent = formatCount(profile.stats.posts_seen_count);
    }
    if (timeOnSite) {
        timeOnSite.textContent = formatDuration(profile.stats.total_time_seconds);
    }
    if (sessions) {
        sessions.textContent = formatCount(profile.stats.sessions);
    }
}

function updateSelectionState(statusElement, submitButton, selectedCount) {
    statusElement.textContent = `${selectedCount} selected (minimum ${MIN_SELECTED_CATEGORIES})`;
    submitButton.disabled = selectedCount < MIN_SELECTED_CATEGORIES;
}

function renderCategoryOptions(containerId, statusElement, submitButton, categories, preselected = []) {
    const optionsContainer = document.getElementById(containerId);
    const selected = new Set(preselected);
    optionsContainer.innerHTML = "";

    categories.forEach(category => {
        const wrapper = document.createElement("div");
        wrapper.className = "category-option";

        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.name = "categories";
        checkbox.value = category;
        checkbox.checked = selected.has(category);

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

function updateTimeStats() {
    const now = Date.now();
    const elapsed = Math.max(0, now - sessionLastTickMs);
    sessionLastTickMs = now;
    sessionCarryMs += elapsed;
    const addSeconds = Math.floor(sessionCarryMs / 1000);
    if (addSeconds > 0) {
        profile.stats.total_time_seconds += addSeconds;
        sessionCarryMs -= addSeconds * 1000;
        saveProfile();
        ensureStatsElementsUpdated();
    }
}

function trackPostSeen(post) {
    const postId = String(post.id ?? "");
    if (!postId || seenPostIds.has(postId)) {
        return;
    }
    seenPostIds.add(postId);
    profile.stats.posts_seen_count = seenPostIds.size;
    saveProfile();
    ensureStatsElementsUpdated();
}

function createPostElement(item) {
    const post = item.post;
    const article = document.createElement("article");
    article.className = "post-card";
    const isSpecialPost = post.post_type === "special";

    if (isSpecialPost) {
        const author = document.createElement("div");
        author.className = "special-author";

        const authorThumbnail = (post.thumbnail || "").trim();
        if (authorThumbnail) {
            const avatar = document.createElement("img");
            avatar.className = "special-author-avatar";
            avatar.src = authorThumbnail;
            avatar.alt = `${post.title} profile image`;
            avatar.loading = "lazy";
            author.appendChild(avatar);
        } else {
            const avatarPlaceholder = document.createElement("span");
            avatarPlaceholder.className = "special-author-avatar-placeholder";
            avatarPlaceholder.textContent = (post.title || "?").slice(0, 1).toUpperCase();
            author.appendChild(avatarPlaceholder);
        }

        const authorName = document.createElement("p");
        authorName.className = "special-author-name";
        authorName.textContent = post.title || `Post #${post.id}`;
        author.appendChild(authorName);
        article.appendChild(author);
    } else {
        const title = document.createElement("h3");
        title.className = "post-header";
        title.textContent = `#${post.id} - ${post.title}`;
        article.appendChild(title);
    }

    const summary = document.createElement("p");
    summary.className = "post-summary";
    summary.textContent = isSpecialPost
        ? (post.post_text || post.summary)
        : post.summary;

    const thumbnail = (post.thumbnail || "").trim();
    let image = null;
    if (thumbnail && !isSpecialPost) {
        image = document.createElement("img");
        image.className = "post-thumbnail";
        image.src = thumbnail;
        image.alt = post.title || "Post thumbnail";
        image.loading = "lazy";
    }

    const source = document.createElement("a");
    source.className = "post-link";
    source.href = post.source;
    source.target = "_blank";
    source.rel = "noopener noreferrer";
    source.textContent = "Source";

    const interactions = document.createElement("div");
    interactions.className = "post-interactions";

    const views = document.createElement("span");
    views.className = "post-interaction";
    views.textContent = `Views ${formatCount(post.view_count)}`;

    const likes = document.createElement("span");
    likes.className = "post-interaction";
    likes.textContent = `Likes ${formatCount(post.like_count)}`;

    interactions.append(views, likes);

    const footer = document.createElement("div");
    footer.className = "post-footer";
    footer.append(source, interactions);

    article.appendChild(summary);
    if (image) {
        article.appendChild(image);
    }
    article.appendChild(footer);
    return article;
}

async function ensureDatasetLoaded() {
    if (loadedDataset) {
        return loadedDataset;
    }
    const postsResponse = await fetch("./data/posts.json");
    loadedDataset = await postsResponse.json();
    return loadedDataset;
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
                matchedCategories: (post.categories || []).filter(postCategory => selected.has(postCategory)),
            });
        });
    });

    for (let index = items.length - 1; index > 0; index -= 1) {
        const randomIndex = Math.floor(Math.random() * (index + 1));
        [items[index], items[randomIndex]] = [items[randomIndex], items[index]];
    }

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
    status.textContent = "";
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

    trackPostSeen(post);
    container.appendChild(createPostElement({ post, matchedCategories: post.categories || [] }));
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
        const item = feedItems[index];
        trackPostSeen(item.post);
        container.appendChild(createPostElement(item));
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
        threshold: 0.01,
    });

    feedObserver.observe(sentinel);
}

function updateHeaderNav() {
    const feedButton = document.getElementById("feed-nav-button");
    const profileButton = document.getElementById("profile-nav-button");
    const infoButton = document.getElementById("info-nav-button");
    feedButton.classList.toggle("active-nav", currentView === "feed");
    profileButton.classList.toggle("active-nav", currentView === "profile");
    infoButton.classList.toggle("active-nav", currentView === "info");
}

function showView(view) {
    const gate = document.getElementById("category-gate");
    const content = document.getElementById("content");
    const profilePage = document.getElementById("profile-page");
    const infoPage = document.getElementById("info-page");
    currentView = view;
    if (view === "profile") {
        gate.classList.add("hidden");
        content.classList.add("hidden");
        profilePage.classList.remove("hidden");
        infoPage.classList.add("hidden");
    } else if (view === "info") {
        gate.classList.add("hidden");
        content.classList.add("hidden");
        profilePage.classList.add("hidden");
        infoPage.classList.remove("hidden");
    } else if (view === "feed") {
        gate.classList.add("hidden");
        content.classList.remove("hidden");
        profilePage.classList.add("hidden");
        infoPage.classList.add("hidden");
    } else {
        gate.classList.remove("hidden");
        content.classList.add("hidden");
        profilePage.classList.add("hidden");
        infoPage.classList.add("hidden");
    }
    updateHeaderNav();
}

async function openFeedWithCategories(selectedCategories) {
    const categoryList = Array.from(new Set(selectedCategories));
    if (categoryList.length < MIN_SELECTED_CATEGORIES && !requestedPostId) {
        showView("gate");
        return false;
    }

    const statusElement = document.getElementById("category-status");
    statusElement.textContent = "Loading posts...";

    const dataset = await ensureDatasetLoaded();
    const container = document.getElementById("posts");
    container.innerHTML = "";
    hasEnteredFeed = true;
    showView("feed");

    if (requestedPostId) {
        renderSinglePost(dataset, requestedPostId);
        document.getElementById("feed-status").textContent = "";
        document.getElementById("scroll-sentinel").textContent = "";
        return true;
    }

    feedItems = buildFeedItems(dataset, categoryList);
    feedCursor = 0;
    renderNextBatch();
    initializeInfiniteScroll();
    return true;
}

function setupSessionTracking() {
    profile.stats.sessions += 1;
    saveProfile();
    ensureStatsElementsUpdated();
    sessionLastTickMs = Date.now();
    statsFlushTimer = window.setInterval(updateTimeStats, STATS_FLUSH_INTERVAL_MS);
    window.addEventListener("beforeunload", updateTimeStats);
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            updateTimeStats();
        } else {
            sessionLastTickMs = Date.now();
        }
    });
}

async function initializePage() {
    profile = loadProfile();
    seenPostIds = new Set(profile.stats.seen_post_ids || []);
    requestedPostId = new URLSearchParams(window.location.search).get("id");

    const categoriesResponse = await fetch("./data/categories.json");
    const categoriesData = await categoriesResponse.json();
    const categories = Array.isArray(categoriesData.categories) ? categoriesData.categories : [];

    const gateForm = document.getElementById("category-form");
    const gateStatus = document.getElementById("category-status");
    const gateSubmitButton = document.getElementById("enter-button");

    const profileForm = document.getElementById("profile-form");
    const profileStatus = document.getElementById("profile-category-status");
    const profileSaveButton = document.getElementById("profile-save-button");
    const profileDeleteButton = document.getElementById("profile-delete-button");
    const renderProfileSelectors = preselected => {
        feedCategorySelection = renderCategoryOptions(
            "category-options",
            gateStatus,
            gateSubmitButton,
            categories,
            preselected
        );
        profileCategorySelection = renderCategoryOptions(
            "profile-category-options",
            profileStatus,
            profileSaveButton,
            categories,
            preselected
        );
    };
    renderProfileSelectors(profile.preferred_categories);
    ensureStatsElementsUpdated();

    gateForm.addEventListener("submit", async event => {
        event.preventDefault();
        if (feedCategorySelection.size < MIN_SELECTED_CATEGORIES && !requestedPostId) {
            updateSelectionState(gateStatus, gateSubmitButton, feedCategorySelection.size);
            return;
        }
        const selectedCategories = Array.from(feedCategorySelection);
        profile.preferred_categories = selectedCategories;
        saveProfile();
        await openFeedWithCategories(selectedCategories);
    });

    profileForm.addEventListener("submit", async event => {
        event.preventDefault();
        if (profileCategorySelection.size < MIN_SELECTED_CATEGORIES) {
            updateSelectionState(profileStatus, profileSaveButton, profileCategorySelection.size);
            return;
        }
        profile.preferred_categories = Array.from(profileCategorySelection);
        saveProfile();
        renderProfileSelectors(profile.preferred_categories);
        profileStatus.textContent = "Profile saved.";
        ensureStatsElementsUpdated();
    });
    profileDeleteButton.addEventListener("click", () => {
        const confirmed = window.confirm(
            "Delete all saved profile data on this browser? This will remove preferred categories and stats."
        );
        if (!confirmed) {
            return;
        }

        profile = createDefaultProfile();
        seenPostIds = new Set();
        feedItems = [];
        feedCursor = 0;
        hasEnteredFeed = false;
        sessionCarryMs = 0;
        sessionLastTickMs = Date.now();
        requestedPostId = null;

        try {
            window.localStorage.removeItem(PROFILE_STORAGE_KEY);
        } catch {
            // Ignore storage cleanup failures.
        }

        renderProfileSelectors([]);
        ensureStatsElementsUpdated();
        document.getElementById("posts").innerHTML = "";
        document.getElementById("feed-status").textContent = "";
        document.getElementById("scroll-sentinel").textContent = "";
        profileStatus.textContent = "Profile data deleted.";
        showView("gate");
    });

    document.getElementById("profile-nav-button").addEventListener("click", () => {
        showView("profile");
        ensureStatsElementsUpdated();
    });
    document.getElementById("feed-nav-button").addEventListener("click", async () => {
        if (!hasEnteredFeed && profile.preferred_categories.length < MIN_SELECTED_CATEGORIES && !requestedPostId) {
            showView("gate");
            return;
        }
        if (!hasEnteredFeed) {
            await openFeedWithCategories(profile.preferred_categories);
            return;
        }
        showView("feed");
    });
    document.getElementById("info-nav-button").addEventListener("click", () => {
        showView("info");
    });

    setupSessionTracking();

    if (requestedPostId || profile.preferred_categories.length >= MIN_SELECTED_CATEGORIES) {
        await openFeedWithCategories(profile.preferred_categories);
    } else {
        showView("gate");
    }
}

initializePage();
