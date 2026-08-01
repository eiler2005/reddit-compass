// Клавиатура и состояние прочитанного.
//
// Сервис открывают каждое утро и проходят списком сверху вниз — это работа,
// а не разглядывание витрины. Отсюда две вещи, которых не хватало:
//
// 1. j/k/Enter, чтобы идти по списку без мыши. Аудитория уже знает эти клавиши
//    по HN и Reddit, а у Linear на них построена вся навигация.
// 2. Отметка прочитанного: раздел называется «Что прочитать сегодня», но
//    назавтра нельзя было понять, что уже смотрел.
//
// Обе надстройки необязательны: без скриптов список остаётся полностью рабочим.
(() => {
    "use strict";

    const ROW_SELECTOR = ".item-row, .pulse-link-item, [data-reading-item]";
    const READ_KEY_PREFIX = "rc-read:";
    // Ключи живут по дате: иначе хранилище растёт бесконечно, а «прочитано»
    // накапливается за все дни сразу.
    const READ_KEY = READ_KEY_PREFIX + new Date().toISOString().slice(0, 10);

    // ─── Прочитанное ─────────────────────────────────────────────────────
    const loadRead = () => {
        try {
            const raw = window.localStorage.getItem(READ_KEY);
            return new Set(raw ? JSON.parse(raw) : []);
        } catch (e) {
            return new Set();
        }
    };

    const read = loadRead();

    const persist = () => {
        try {
            window.localStorage.setItem(READ_KEY, JSON.stringify([...read]));
            // Вчерашние отметки больше не нужны.
            for (let i = 0; i < window.localStorage.length; i += 1) {
                const key = window.localStorage.key(i);
                if (key && key.startsWith(READ_KEY_PREFIX) && key !== READ_KEY) {
                    window.localStorage.removeItem(key);
                }
            }
        } catch (e) {
            /* приватный режим или переполненное хранилище — не повод ломать страницу */
        }
    };

    // Идентификатор строки — её основная ссылка: он переживает перерисовку
    // фрагментом и не зависит от порядка элементов.
    const rowId = (row) => {
        const link = row.querySelector("a[href]");
        return link ? link.getAttribute("href") : "";
    };

    const applyRead = (row) => {
        const id = rowId(row);
        if (id && read.has(id)) row.classList.add("is-read");
    };

    const toggleRead = (row) => {
        const id = rowId(row);
        if (!id) return;
        if (read.has(id)) {
            read.delete(id);
            row.classList.remove("is-read");
        } else {
            read.add(id);
            row.classList.add("is-read");
        }
        persist();
    };

    // ─── Навигация с клавиатуры ──────────────────────────────────────────
    let cursor = -1;

    const rows = () => [...document.querySelectorAll(ROW_SELECTOR)];

    const focusRow = (index) => {
        const list = rows();
        if (!list.length) return;
        const next = Math.max(0, Math.min(index, list.length - 1));
        list.forEach((row) => row.classList.remove("is-cursor"));
        const row = list[next];
        row.classList.add("is-cursor");
        row.scrollIntoView({ block: "nearest" });
        cursor = next;
    };

    const openRow = () => {
        const row = rows()[cursor];
        if (!row) return;
        const link = row.querySelector("a[href]");
        if (link) link.click();
    };

    const typingIn = (el) =>
        el instanceof HTMLElement &&
        (el.tagName === "INPUT" ||
            el.tagName === "TEXTAREA" ||
            el.tagName === "SELECT" ||
            el.isContentEditable);

    let pendingGo = false;

    document.addEventListener("keydown", (event) => {
        if (event.metaKey || event.ctrlKey || event.altKey) return;
        if (typingIn(document.activeElement)) return;

        // g + буква — переход по разделам, как в почтовых клиентах.
        if (pendingGo) {
            pendingGo = false;
            const routes = { t: "/trends", p: "/pulse", s: "/stories", n: "/news", d: "/today" };
            if (routes[event.key]) {
                event.preventDefault();
                window.location.href = routes[event.key];
                return;
            }
        }

        switch (event.key) {
            case "j":
                event.preventDefault();
                focusRow(cursor + 1);
                break;
            case "k":
                event.preventDefault();
                focusRow(cursor - 1);
                break;
            case "o":
            case "Enter":
                if (cursor >= 0) {
                    event.preventDefault();
                    openRow();
                }
                break;
            case "m": {
                const row = rows()[cursor];
                if (row) {
                    event.preventDefault();
                    toggleRead(row);
                }
                break;
            }
            case "/": {
                const search = document.querySelector(
                    'input[type="search"], .filters input[type="text"]'
                );
                if (search instanceof HTMLElement) {
                    event.preventDefault();
                    search.focus();
                }
                break;
            }
            case "Escape":
                rows().forEach((row) => row.classList.remove("is-cursor"));
                cursor = -1;
                break;
            case "g":
                pendingGo = true;
                break;
            default:
                break;
        }
    });

    // Открытая ссылка означает прочитано — это и есть основной способ отметки,
    // клавиша «m» нужна лишь чтобы снять или поставить её вручную.
    document.addEventListener("click", (event) => {
        const link = event.target.closest("a[href]");
        if (!link) return;
        const row = link.closest(ROW_SELECTOR);
        if (row && !read.has(rowId(row))) toggleRead(row);
    });

    const markAll = () => rows().forEach(applyRead);

    document.addEventListener("DOMContentLoaded", markAll);
    markAll();

    // Списки догружаются и подменяются фрагментами, поэтому отметки нужно
    // проставлять и на новой разметке.
    new MutationObserver(markAll).observe(document.documentElement, {
        childList: true,
        subtree: true,
    });
})();
