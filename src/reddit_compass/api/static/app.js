// reddit-compass — kinetic UI
// Theme toggle + micro-interactions

(function () {
    "use strict";

    // ─── Theme toggle ────────────────────────────────────────────────────
    const THEME_KEY = "rc-theme";

    function getPreferredTheme() {
        const stored = localStorage.getItem(THEME_KEY);
        if (stored) return stored;
        return "dark"; // dark by default
    }

    function applyTheme(theme) {
        document.documentElement.setAttribute("data-theme", theme);
        localStorage.setItem(THEME_KEY, theme);
        updateToggleIcon(theme);
    }

    function updateToggleIcon(theme) {
        const toggle = document.querySelector(".theme-toggle");
        if (toggle) {
            toggle.textContent = theme === "dark" ? "☀️" : "🌙";
            toggle.setAttribute("aria-label", theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
        }
    }

    function initTheme() {
        applyTheme(getPreferredTheme());

        const toggle = document.querySelector(".theme-toggle");
        if (toggle) {
            toggle.addEventListener("click", function () {
                const current = document.documentElement.getAttribute("data-theme");
                applyTheme(current === "dark" ? "light" : "dark");
            });
        }
    }

    // ─── KPI count-up animation ──────────────────────────────────────────
    function animateCountUp(el) {
        const target = parseInt(el.textContent, 10);
        if (isNaN(target) || target === 0) return;

        const duration = 800;
        const start = performance.now();

        function tick(now) {
            const elapsed = now - start;
            const progress = Math.min(elapsed / duration, 1);
            // Ease out cubic
            const eased = 1 - Math.pow(1 - progress, 3);
            el.textContent = Math.round(target * eased);
            if (progress < 1) {
                requestAnimationFrame(tick);
            }
        }

        el.textContent = "0";
        requestAnimationFrame(tick);
    }

    function initCounters() {
        const counters = document.querySelectorAll(".kpi-num, .trend-score");
        const observer = new IntersectionObserver(
            function (entries) {
                entries.forEach(function (entry) {
                    if (entry.isIntersecting) {
                        animateCountUp(entry.target);
                        observer.unobserve(entry.target);
                    }
                });
            },
            { threshold: 0.5 }
        );
        counters.forEach(function (c) {
            observer.observe(c);
        });
    }

    // ─── Scroll reveal for sections ──────────────────────────────────────
    function initScrollReveal() {
        const sections = document.querySelectorAll(".section, .story-card, .kpi-card");
        const observer = new IntersectionObserver(
            function (entries) {
                entries.forEach(function (entry) {
                    if (entry.isIntersecting) {
                        entry.target.classList.add("revealed");
                        observer.unobserve(entry.target);
                    }
                });
            },
            { threshold: 0.1, rootMargin: "0px 0px -40px 0px" }
        );
        sections.forEach(function (s) {
            observer.observe(s);
        });
    }

    // ─── Active nav link ─────────────────────────────────────────────────
    function initActiveNav() {
        const path = window.location.pathname;
        const links = document.querySelectorAll(".nav-link");
        links.forEach(function (link) {
            const href = link.getAttribute("href");
            if (href === path || (path.startsWith(href) && href !== "/")) {
                link.classList.add("active");
            }
        });
    }

    // ─── Keyboard navigation for story cards ─────────────────────────────
    function initCardKeyboard() {
        const cards = document.querySelectorAll(".story-card");
        cards.forEach(function (card) {
            card.setAttribute("tabindex", "0");
            card.addEventListener("keydown", function (e) {
                if (e.key === "Enter") {
                    const link = card.querySelector(".story-title a");
                    if (link) link.click();
                }
            });
        });
    }

    // ─── Clickable cards → primary evidence ──────────────────────────────
    function initCardClick() {
        document.addEventListener("click", function (e) {
            const card = e.target.closest(".story-card[data-href]");
            if (!card) return;
            if (e.target.closest("a, button, details, summary, input, select")) return;
            const href = card.getAttribute("data-href");
            if (href) {
                window.open(href, "_blank", "noopener,noreferrer");
            }
        });
    }

    // ─── Init ───────────────────────────────────────────────────────────
    document.addEventListener("DOMContentLoaded", function () {
        initTheme();
        initCounters();
        initScrollReveal();
        initActiveNav();
        initCardKeyboard();
        initCardClick();
    });
})();
