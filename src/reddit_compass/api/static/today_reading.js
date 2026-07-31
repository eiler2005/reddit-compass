(() => {
    "use strict";

    const safeHttpUrl = (value) => {
        if (typeof value !== "string") return "";
        try {
            const parsed = new URL(value);
            return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : "";
        } catch (_) {
            return "";
        }
    };

    const safeInternalTrendUrl = (value) => {
        if (typeof value !== "string") return "";
        try {
            const parsed = new URL(value, window.location.origin);
            if (parsed.origin !== window.location.origin || !parsed.pathname.startsWith("/trends/")) {
                return "";
            }
            return `${parsed.pathname}${parsed.search}`;
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

    const fetchItems = (endpoint) => {
        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), 30000);
        return fetch(endpoint, {
            headers: { Accept: "application/json" },
            signal: controller.signal,
        })
            .then((response) => (response.ok ? response.json() : Promise.reject(response)))
            .then((payload) => (Array.isArray(payload.items) ? payload.items : []))
            .finally(() => window.clearTimeout(timer));
    };

    const renderChange = (trend) => {
        const card = document.createElement("a");
        card.className = "story-card today-trend-card";
        card.href = safeInternalTrendUrl(trend.url) || "/trends?channel=broad";

        const meta = document.createElement("div");
        meta.className = "story-meta";
        appendText(meta, "span", trend.lifecycle_label, "direction-badge");
        if (trend.source_scope_label) {
            appendText(meta, "span", trend.source_scope_label, `scope-badge scope-${trend.source_scope}`);
        }
        appendText(meta, "span", trend.review_label);
        appendText(meta, "span", `${trend.confidence_pct || 0}%`);
        card.append(meta);

        appendText(card, "h3", trend.title || "Trend-кандидат");
        if (trend.pattern) appendText(card, "p", trend.pattern);

        const footer = document.createElement("div");
        footer.className = "today-card-footer";
        appendText(footer, "span", `${trend.source_count || 0} источников`);
        appendText(footer, "span", `${trend.story_count || 0} событий`);
        appendText(footer, "span", "Открыть →");
        card.append(footer);
        return card;
    };

    const changesSection = document.querySelector("[data-today-changes-url]");
    if (changesSection instanceof HTMLElement) {
        const endpoint = changesSection.dataset.todayChangesUrl;
        const container = changesSection.querySelector("[data-today-changes]");
        // The first cards are rendered with the HTML response.  This keeps
        // Today readable if a browser has a stale JS asset or a request is
        // interrupted; there is no reason to replace an already good brief.
        const serverRendered = container instanceof HTMLElement && container.dataset.serverRendered === "true";
        if (endpoint && container instanceof HTMLElement && !serverRendered) {
            fetchItems(endpoint)
                .then((items) => {
                    container.replaceChildren();
                    if (!items.length) {
                        appendText(
                            container,
                            "p",
                            "В опубликованной версии пока нет trend-кандидатов.",
                            "section-note"
                        );
                        return;
                    }
                    items.forEach((trend) => container.append(renderChange(trend)));
                })
                .catch(() => {
                    container.replaceChildren();
                    appendText(
                        container,
                        "p",
                        "Не удалось загрузить изменения. Откройте Trends, чтобы продолжить исследование.",
                        "section-note"
                    );
                });
        }
    }

    const readingSection = document.querySelector("[data-reading-feed-url]");
    if (!(readingSection instanceof HTMLElement)) return;

    const readingEndpoint = readingSection.dataset.readingFeedUrl;
    const feed = readingSection.querySelector("[data-reading-feed]");
    if (!readingEndpoint || !(feed instanceof HTMLElement)) return;

    const renderReadingItem = (item, index) => {
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

    const fetchReadingPage = (offset) => {
        const url = new URL(readingEndpoint, window.location.origin);
        url.searchParams.set("offset", String(offset));
        url.searchParams.set("limit", "10");
        return fetchItems(url);
    };

    const initiallyRendered = feed.querySelectorAll("[data-reading-item]").length;
    const appendSecondPage = (firstPage, offset) => {
        appendText(feed, "p", "Подгружаю ещё материалы…", "section-note");
        return fetchReadingPage(offset).then((secondPage) => ({ firstPage, secondPage }));
    };

    // The first ten entries are server-rendered in current releases.  Older
    // HTML still follows the original p0 -> p1 path, so deploys are backwards
    // compatible while browsers refresh their static assets.
    const readingPromise = initiallyRendered
        ? appendSecondPage(Array.from({ length: initiallyRendered }), initiallyRendered)
        : fetchReadingPage(0).then((firstPage) => {
            feed.replaceChildren();
            if (!firstPage.length) {
                appendText(
                    feed,
                    "p",
                    "Лента чтения пока пуста для этого опубликованного release.",
                    "section-note"
                );
                return null;
            }
            firstPage.forEach((item, index) => feed.append(renderReadingItem(item, index)));
            return appendSecondPage(firstPage, firstPage.length);
        });

    readingPromise
        .then((pages) => {
            if (!pages) return;
            const loading = feed.querySelector(".section-note");
            if (loading) loading.remove();
            pages.secondPage.forEach((item, index) =>
                feed.append(renderReadingItem(item, pages.firstPage.length + index))
            );
        })
        .catch(() => {
            const loading = feed.querySelector(".section-note");
            if (loading) loading.remove();
            if (feed.querySelector(".reading-item")) {
                appendText(
                    feed,
                    "p",
                    "Остальные материалы пока недоступны. Уже загруженные можно открыть; полный выпуск — в News.",
                    "section-note"
                );
            } else {
                feed.replaceChildren();
                appendText(
                    feed,
                    "p",
                    "Не удалось загрузить ленту. Откройте News, чтобы посмотреть материалы выпуска.",
                    "section-note"
                );
            }
        });
})();
