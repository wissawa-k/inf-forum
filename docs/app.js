const MIN_SELECTED_CATEGORIES = 3;

function createPostElement(post) {
    const article = document.createElement("article");

    const title = document.createElement("h4");
    title.textContent = `#${post.id} - ${post.title}`;

    const summary = document.createElement("p");
    summary.textContent = post.summary;

    const updatedAt = document.createElement("small");
    updatedAt.textContent = post.updated_at;

    const categoryLine = document.createElement("small");
    categoryLine.textContent = `Categories: ${(post.categories || []).join(", ")}`;

    const source = document.createElement("a");
    source.href = post.source;
    source.target = "_blank";
    source.rel = "noopener noreferrer";
    source.textContent = "Source";

    article.append(title, summary, updatedAt, document.createElement("br"), categoryLine, document.createElement("br"), source);
    return article;
}

function renderPosts(dataset, selectedCategories) {
    const container = document.getElementById("posts");
    const postsById = dataset.articles_by_id || dataset.posts_by_id || {};
    const categoryMap = dataset.categories || {};
    const params = new URLSearchParams(window.location.search);
    const queryId = params.get("id");
    container.innerHTML = "";

    if (queryId) {
        const post = postsById[queryId];
        if (!post) {
            const missing = document.createElement("p");
            missing.textContent = `Article id ${queryId} was not found.`;
            container.appendChild(missing);
            return;
        }

        container.appendChild(createPostElement(post));
        return;
    }

    selectedCategories.forEach(category => {
        const ids = categoryMap[category] || [];
        const section = document.createElement("section");
        section.className = "category-section";

        const heading = document.createElement("h3");
        heading.textContent = category;
        section.appendChild(heading);

        if (ids.length === 0) {
            const empty = document.createElement("p");
            empty.textContent = "No posts found in this category.";
            section.appendChild(empty);
            container.appendChild(section);
            return;
        }

        ids.forEach(id => {
            const post = postsById[String(id)];
            if (!post) {
                return;
            }
            section.appendChild(createPostElement(post));
        });

        container.appendChild(section);
    });
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

    const [categoriesResponse, postsResponse] = await Promise.all([
        fetch("./data/categories.json"),
        fetch("./data/posts.json")
    ]);

    const categoriesData = await categoriesResponse.json();
    const dataset = await postsResponse.json();
    const categories = categoriesData.categories || [];
    const selected = renderCategoryOptions(categories, statusElement, submitButton);

    form.addEventListener("submit", event => {
        event.preventDefault();
        if (selected.size < MIN_SELECTED_CATEGORIES) {
            updateSelectionState(statusElement, submitButton, selected.size);
            return;
        }

        gate.classList.add("hidden");
        content.classList.remove("hidden");
        renderPosts(dataset, Array.from(selected));
    });
}

initializePage();
