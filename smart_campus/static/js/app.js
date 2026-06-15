// TM1118 Smart Campus W311 - Client-side JS

document.addEventListener("DOMContentLoaded", function () {

    // === Theme Toggle ===
    const themeToggle = document.getElementById("themeToggle");
    const html = document.documentElement;

    function getPreferredTheme() {
        const stored = localStorage.getItem("theme");
        if (stored) return stored;
        return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }

    function setTheme(theme) {
        html.setAttribute("data-theme", theme);
        localStorage.setItem("theme", theme);
    }

    setTheme(getPreferredTheme());

    if (themeToggle) {
        themeToggle.addEventListener("click", function () {
            const current = html.getAttribute("data-theme");
            setTheme(current === "dark" ? "light" : "dark");
        });
    }

    // === Mobile Hamburger ===
    const hamburger = document.getElementById("hamburgerBtn");
    const mobileNav = document.getElementById("mobileNav");
    if (hamburger && mobileNav) {
        hamburger.addEventListener("click", function () {
            mobileNav.classList.toggle("open");
        });
        document.addEventListener("click", function (e) {
            if (!hamburger.contains(e.target) && !mobileNav.contains(e.target)) {
                mobileNav.classList.remove("open");
            }
        });
    }

    // === Toast Notification System ===
    function showToast(message, type) {
        type = type || "info";
        var container = document.getElementById("toastContainer");
        if (!container) return;

        var toast = document.createElement("div");
        toast.className = "toast " + type;
        toast.textContent = message;

        var icons = {
            "success": "\u2713",
            "error": "\u2717",
            "info": "\u2139"
        };
        toast.textContent = (icons[type] || "") + " " + message;

        container.appendChild(toast);

        setTimeout(function () {
            toast.classList.add("toast-out");
            setTimeout(function () { toast.remove(); }, 300);
        }, 3500);
    }

    window.showToast = showToast;

    // === Auto-refresh for all pages ===
    var refreshInterval = null;
    var REFRESH_INTERVAL_MS = 30000; // 30 seconds

    function startAutoRefresh() {
        if (refreshInterval) return;
        var toggle = document.getElementById("autoRefreshToggle");
        if (toggle) toggle.checked = true;
        // Show indicator in nav
        var ind = document.getElementById("autoRefreshIndicator");
        if (ind) ind.style.display = "inline";
        refreshInterval = setInterval(function () {
            window.location.reload();
        }, REFRESH_INTERVAL_MS);
    }

    function stopAutoRefresh() {
        if (refreshInterval) {
            clearInterval(refreshInterval);
            refreshInterval = null;
        }
        var toggle = document.getElementById("autoRefreshToggle");
        if (toggle) toggle.checked = false;
        var ind = document.getElementById("autoRefreshIndicator");
        if (ind) ind.style.display = "none";
    }

    // Connect the toggle if it exists on this page
    var toggle = document.getElementById("autoRefreshToggle");
    if (toggle) {
        // Restore saved state
        var saved = localStorage.getItem("autoRefresh");
        if (saved === "true") {
            toggle.checked = true;
            startAutoRefresh();
        }
        toggle.addEventListener("change", function () {
            if (this.checked) {
                localStorage.setItem("autoRefresh", "true");
                startAutoRefresh();
            } else {
                localStorage.setItem("autoRefresh", "false");
                stopAutoRefresh();
            }
        });
    } else {
        // No toggle on this page — still check localStorage
        if (localStorage.getItem("autoRefresh") === "true") {
            startAutoRefresh();
        }
    }

});
