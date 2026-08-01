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

    // ─── Фильтры тематик без перезагрузки ────────────────────────────────
    // Чипы остаются обычными ссылками, поэтому без скриптов фильтр работает
    // как прежде. Скрипт перехватывает клик и подменяет только список: раньше
    // смена тематики заново считала радар, дашборд и ленту чтения целиком.
    const filters = document.querySelector("[data-reddit-filters]");
    const redditList = document.querySelector("[data-reddit-list]");
    if (filters instanceof HTMLElement && redditList instanceof HTMLElement) {
        const endpoint = filters.dataset.redditEndpoint || "";

        const markActive = (type) => {
            filters.querySelectorAll("[data-reddit-type]").forEach((chip) => {
                chip.classList.toggle(
                    "reddit-type-chip-active",
                    (chip.dataset.redditType || "") === type
                );
            });
        };

        const load = (type) => {
            if (!endpoint) return Promise.resolve();
            const separator = endpoint.includes("?") ? "&" : "?";
            const url = type ? `${endpoint}${separator}reddit_type=${encodeURIComponent(type)}` : endpoint;
            redditList.setAttribute("aria-busy", "true");
            return fetchFragment(url)
                .then((html) => {
                    redditList.innerHTML =
                        html.trim() || '<li class="section-note">Свежих постов по этой теме в выпуске нет.</li>';
                })
                .catch(() => {
                    redditList.innerHTML =
                        '<li class="section-note">Не удалось обновить список. Откройте Reddit Pulse.</li>';
                })
                .finally(() => redditList.removeAttribute("aria-busy"));
        };

        filters.addEventListener("click", (event) => {
            const chip = event.target.closest("[data-reddit-type]");
            if (!chip || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
            event.preventDefault();
            const type = chip.dataset.redditType || "";
            markActive(type);
            // Адрес обязан меняться вместе со списком: иначе ссылку нельзя
            // отправить, а «назад» уведёт со страницы вместо снятия фильтра.
            window.history.pushState({ redditType: type }, "", chip.getAttribute("href"));
            load(type);
        });

        window.addEventListener("popstate", () => {
            const type = new URLSearchParams(window.location.search).get("reddit_type") || "";
            markActive(type);
            load(type);
        });
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
