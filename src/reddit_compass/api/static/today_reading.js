(() => {
    "use strict";

    // Разметка карточек живёт только в Jinja (components/today_*.html).
    // Эндпоинты /ui/today-changes и /ui/today-reading отдают готовые HTML-фрагменты,
    // и здесь остаётся лишь вставить их. Раньше те же карточки строились вторым
    // экземпляром через createElement, и правка требовала синхронных изменений
    // в шаблоне и в этом файле.

    const fetchFragment = (endpoint) => {
        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), 30000);
        return fetch(endpoint, {
            headers: { Accept: "text/html" },
            signal: controller.signal,
        })
            .then((response) => (response.ok ? response.text() : Promise.reject(response)))
            .finally(() => window.clearTimeout(timer));
    };

    const showNote = (container, text) => {
        const note = document.createElement("p");
        note.className = "section-note";
        note.textContent = text;
        container.replaceChildren(note);
    };

    const changesSection = document.querySelector("[data-today-changes-url]");
    if (changesSection instanceof HTMLElement) {
        const endpoint = changesSection.dataset.todayChangesUrl;
        const container = changesSection.querySelector("[data-today-changes]");
        // Первые карточки приходят вместе со страницей. Если они уже есть,
        // перерисовывать нечего: устаревший или прерванный запрос не должен
        // заменять собой готовый брифинг.
        const serverRendered =
            container instanceof HTMLElement && container.dataset.serverRendered === "true";
        if (endpoint && container instanceof HTMLElement && !serverRendered) {
            fetchFragment(endpoint)
                .then((html) => {
                    if (html.trim()) {
                        container.innerHTML = html;
                    } else {
                        showNote(container, "В опубликованной версии пока нет trend-кандидатов.");
                    }
                })
                .catch(() => {
                    showNote(
                        container,
                        "Не удалось загрузить изменения. Откройте Trends, чтобы продолжить исследование."
                    );
                });
        }
    }

    const readingSection = document.querySelector("[data-reading-feed-url]");
    if (!(readingSection instanceof HTMLElement)) return;

    const readingEndpoint = readingSection.dataset.readingFeedUrl;
    const feed = readingSection.querySelector("[data-reading-feed]");
    if (!readingEndpoint || !(feed instanceof HTMLElement)) return;

    const pageUrl = (offset, limit) => {
        const separator = readingEndpoint.includes("?") ? "&" : "?";
        return `${readingEndpoint}${separator}offset=${offset}&limit=${limit}`;
    };

    const appendPage = (html) => {
        if (!html.trim()) return false;
        feed.insertAdjacentHTML("beforeend", html);
        return true;
    };

    const initiallyRendered = feed.querySelectorAll("[data-reading-item]").length;

    // Первая страница рендерится на сервере в актуальных релизах. Старый HTML
    // без неё по-прежнему грузит обе страницы, поэтому деплой обратно совместим.
    const initial = initiallyRendered
        ? Promise.resolve(true)
        : fetchFragment(pageUrl(0, 10)).then((html) => {
              feed.replaceChildren();
              if (!appendPage(html)) {
                  showNote(feed, "Лента чтения пока пуста для этого опубликованного release.");
                  return false;
              }
              return true;
          });

    initial
        .then((hasFirstPage) => {
            if (!hasFirstPage) return;
            const offset = feed.querySelectorAll("[data-reading-item]").length;
            return fetchFragment(pageUrl(offset, 10)).then((html) => {
                const loading = feed.querySelector(".section-note");
                if (loading) loading.remove();
                appendPage(html);
            });
        })
        .catch(() => {
            const loading = feed.querySelector(".section-note");
            if (loading) loading.remove();
            if (feed.querySelector("[data-reading-item]")) {
                const note = document.createElement("p");
                note.className = "section-note";
                note.textContent =
                    "Остальные материалы пока недоступны. Уже загруженные можно открыть; полный выпуск — в News.";
                feed.append(note);
            } else {
                showNote(
                    feed,
                    "Не удалось загрузить ленту. Откройте News, чтобы посмотреть материалы выпуска."
                );
            }
        });
})();
