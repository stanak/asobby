// 古いクライアント検出時（または未起動時）に Web 全ページで更新を案内する
(function () {
  "use strict";

  const LOCAL_API_BASE = "http://127.0.0.1:49152";
  const LOCAL_HEALTH_TIMEOUT_MS = 1200;
  const RECHECK_MS = 5 * 60 * 1000;
  const DISMISS_KEY = "asobby-client-banner-dismissed";

  let bannerEl = null;
  let recheckTimer = null;

  function injectStyles() {
    if (document.getElementById("client-update-styles")) return;
    const style = document.createElement("style");
    style.id = "client-update-styles";
    style.textContent = `
      .client-update-banner {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 10px 14px;
        margin: 0 20px 12px;
        padding: 12px 16px;
        border: 1px solid rgba(106, 176, 243, 0.45);
        border-radius: 10px;
        background: linear-gradient(
          90deg,
          rgba(106, 176, 243, 0.18),
          rgba(87, 192, 125, 0.12)
        );
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
      }
      .client-update-banner .client-update-text {
        flex: 1 1 220px;
        font-size: 14px;
        line-height: 1.45;
      }
      .client-update-banner .client-update-actions {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
      }
      .client-update-banner .client-update-download {
        display: inline-block;
        padding: 8px 14px;
        border-radius: 8px;
        background: var(--accent, #6ab0f3);
        color: #0f1419;
        font-weight: 600;
        text-decoration: none;
      }
      .client-update-banner .client-update-download:hover {
        filter: brightness(1.08);
      }
      .client-update-banner .client-update-dismiss {
        padding: 8px 12px;
        border: 1px solid var(--border, #2c3440);
        border-radius: 8px;
        background: transparent;
        color: var(--muted, #7b8794);
        cursor: pointer;
        font-size: 13px;
      }
      #nav-client-dl.nav-client-dl--highlight {
        padding: 4px 10px;
        border-radius: 999px;
        background: rgba(106, 176, 243, 0.2);
        border: 1px solid rgba(106, 176, 243, 0.55);
        font-weight: 600;
        animation: client-dl-pulse 2.4s ease-in-out infinite;
      }
      @keyframes client-dl-pulse {
        0%, 100% { box-shadow: 0 0 0 0 rgba(106, 176, 243, 0.0); }
        50% { box-shadow: 0 0 0 4px rgba(106, 176, 243, 0.18); }
      }
    `;
    document.head.appendChild(style);
  }

  async function fetchLocalClientVersion() {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), LOCAL_HEALTH_TIMEOUT_MS);
      const res = await fetch(`${LOCAL_API_BASE}/health`, { signal: ctrl.signal });
      clearTimeout(timer);
      if (!res.ok) return null;
      const data = await res.json();
      return data && data.version ? String(data.version) : null;
    } catch {
      return null;
    }
  }

  async function fetchLatest(current) {
    const params = current ? `?current=${encodeURIComponent(current)}` : "";
    const res = await fetch(`/client/latest${params}`);
    if (!res.ok) return null;
    const data = await res.json();
    return data && data.ok ? data : null;
  }

  function dismissKey(data, localVersion) {
    return `${localVersion || "none"}:${data.tag || data.version}`;
  }

  function clearBanner() {
    if (bannerEl) {
      bannerEl.remove();
      bannerEl = null;
    }
    document.getElementById("nav-client-dl")?.classList.remove("nav-client-dl--highlight");
  }

  function renderBanner(data, localVersion) {
    injectStyles();
    const tFn = window.t || ((key) => key);
    const key = dismissKey(data, localVersion);
    if (localStorage.getItem(DISMISS_KEY) === key) {
      clearBanner();
      return;
    }

    const version =
      data.latest || String(data.version || data.tag || "").replace(/^v/i, "");
    const releaseUrl = data.html_url;
    if (!releaseUrl) return;

    clearBanner();

    bannerEl = document.createElement("div");
    bannerEl.id = "client-update-banner";
    bannerEl.className = "client-update-banner";

    const text = document.createElement("div");
    text.className = "client-update-text";
    text.textContent = tFn("clientUpdate.banner", { version: version });

    const actions = document.createElement("div");
    actions.className = "client-update-actions";

    const link = document.createElement("a");
    link.className = "client-update-download";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.href = releaseUrl;
    link.textContent = tFn("clientUpdate.download");
    actions.appendChild(link);

    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "client-update-dismiss";
    dismiss.textContent = tFn("clientUpdate.dismiss");
    dismiss.addEventListener("click", () => {
      localStorage.setItem(DISMISS_KEY, key);
      clearBanner();
    });
    actions.appendChild(dismiss);

    bannerEl.appendChild(text);
    bannerEl.appendChild(actions);

    const header = document.querySelector("header");
    if (!header) return;
    header.insertAdjacentElement("afterend", bannerEl);

    const navLink = document.getElementById("nav-client-dl");
    if (navLink) {
      navLink.classList.add("nav-client-dl--highlight");
      navLink.href = releaseUrl;
    }
  }

  async function refreshClientUpdateBanner() {
    try {
      const localVersion = await fetchLocalClientVersion();
      const data = await fetchLatest(localVersion || "");
      if (!data || !data.tag) {
        clearBanner();
        return;
      }

      const shouldShow = data.outdated || !localVersion;
      if (!shouldShow) {
        clearBanner();
        return;
      }

      renderBanner(data, localVersion);
    } catch {
      /* ignore */
    }
  }

  function scheduleRecheck() {
    if (recheckTimer) clearInterval(recheckTimer);
    recheckTimer = setInterval(refreshClientUpdateBanner, RECHECK_MS);
  }

  function initClientUpdateBanner() {
    void refreshClientUpdateBanner();
    scheduleRecheck();
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") void refreshClientUpdateBanner();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initClientUpdateBanner);
  } else {
    initClientUpdateBanner();
  }
})();
