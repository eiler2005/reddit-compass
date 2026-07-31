(() => {
    "use strict";

    const section = document.querySelector("[data-reading-feed-url]");
    if (!(section instanceof HTMLElement)) return;

    const endpoint = section.dataset.readingFeedUrl;
    const feed = section.querySelector("[data-reading-feed]");
    if (!endpoint || !(feed instanceof HTMLElement)) return;

    const safeHttpUrl = (value) => {
        if (typeof value !== "string") return "";
        try {
            const parsed = new URL(value);
            return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : "";
        } catch (_) {
            return "";
        }
    };

    const appendText = (parent, tag, text, className = "") => {
        const element = document.createElement(tag);
        if (className) element.className = className;
        element.textContent = String(text || "");
        parent.append(element);
        return element;
    };

    const externalLink = (url, label) => {
        const safeUrl = safeHttpUrl(url);
        if (!safeUrl) return null;
        const link = document.createElement("a");
        link.className = "card-action-link";
        link.href = safeUrl;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = label;
        return link;
    };

    const renderItem = (item, index) => {
        const article = document.createElement("article");
        article.className = "reading-item";
        appendText(article, "div", String(index + 1).padStart(2, "0"), "reading-rank");

        const main = document.createElement("div");
        main.className = "reading-main";
        const meta = document.createElement("div");
        meta.className = "story-meta";
        [item.provider_label, item.source_section, item.source_cluster, item.published_at]
            .filter(Boolean)
            .forEach((value) => appendText(meta, "span", value));
        main.append(meta);

        const title = document.createElement("h3");
        const titleLink = externalLink(item.primary_url, item.title || "Открыть материал");
        if (titleLink) title.append(titleLink);
        else appendText(title, "span", item.title || "Материал без ссылки");
        main.append(title);

        const chips = document.createElement("div");
        chips.className = "chip-cloud";
        (Array.isArray(item.domain_labels) ? item.domain_labels : [])
            .slice(0, 3)
            .forEach((label) => appendText(chips, "span", label, "chip"));
        if (item.reason) appendText(chips, "span", item.reason, "chip chip-accent");
        main.append(chips);
        article.append(main);

        const actions = document.createElement("div");
        actions.className = "reading-actions";
        const open = externalLink(item.primary_url, "Открыть");
        if (open) actions.append(open);
        const secondary = externalLink(item.secondary_url, item.secondary_label || "Обсуждение");
        if (secondary) actions.append(secondary);
        if (item.story_id) {
            const story = document.createElement("a");
            story.className = "card-action-link";
            story.href = `/stories/${encodeURIComponent(String(item.story_id))}?channel=broad`;
            story.textContent = "Story";
            actions.append(story);
        }
        article.append(actions);
        return article;
    };

    const fetchPage = (offset) => {
        const url = new URL(endpoint, window.location.origin);
        url.searchParams.set("offset", String(offset));
        url.searchParams.set("limit", "10");
        return fetch(url, { headers: { Accept: "application/json" } })
            .then((response) => (response.ok ? response.json() : Promise.reject(response)))
            .then((payload) => (Array.isArray(payload.items) ? payload.items : []));
    };

    Promise.all([fetchPage(0), fetchPage(10)])
        .then((pages) => {
            const items = pages.flat();
            feed.replaceChildren();
            if (!items.length) {
                appendText(
                    feed,
                    "p",
                    "Лента чтения пока пуста для этого опубликованного release.",
                    "section-note"
                );
                return;
            }
            items.forEach((item, index) => feed.append(renderItem(item, index)));
        })
        .catch(() => {
            feed.replaceChildren();
            appendText(
                feed,
                "p",
                "Не удалось загрузить ленту. Откройте News, чтобы посмотреть материалы выпуска.",
                "section-note"
            );
        });
})();
