// 最新クライアント公開時に Web 全ページで DL を強調するバナー
(function () {
  "use strict";

  const DISMISS_KEY = "asobby-client-banner-dismissed";

  function injectStyles() {
    if (document.getElementById("client-update-styles")) return;
    const style = document.createElement("style");
    style.id = "client-update-styles";
    style.textContent = `
      #client-update-banner {
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
      #client-update-banner .client-update-text {
        flex: 1 1 220px;
        font-size: 14px;
        line-height: 1.45;
      }
      #client-update-banner .client-update-actions {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
      }
      #client-update-banner .client-update-download {
        display: inline-block;
        padding: 8px 14px;
        border-radius: 8px;
        background: var(--accent, #6ab0f3);
        color: #0f1419;
        font-weight: 600;
        text-decoration: none;
      }
      #client-update-banner .client-update-download:hover {
        filter: brightness(1.08);
      }
      #client-update-banner .client-update-dismiss {
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

  async function initClientUpdateBanner() {
    injectStyles();
    try {
      const res = await fetch("/client/latest");
      if (!res.ok) return;
      const data = await res.json();
      if (!data.ok || !data.tag) return;

      const dismissed = localStorage.getItem(DISMISS_KEY);
      if (dismissed === data.tag) return;

      const header = document.querySelector("header");
      if (!header) return;

      const t = window.t || ((key, vars) => key);
      const version = data.version || data.tag.replace(/^v/i, "");
      const releaseUrl = data.html_url;
      if (!releaseUrl) return;

      const banner = document.createElement("div");
      banner.id = "client-update-banner";
      banner.innerHTML =
        '<div class="client-update-text"></div>' +
        '<div class="client-update-actions">' +
        '<a class="client-update-download" target="_blank" rel="noopener noreferrer"></a>' +
        '<button type="button" class="client-update-dismiss"></button>' +
        "</div>";

      banner.querySelector(".client-update-text").textContent = t(
        "clientUpdate.banner",
        { version: version }
      );
      const dl = banner.querySelector(".client-update-download");
      dl.href = releaseUrl;
      dl.textContent = t("clientUpdate.download");
      banner.querySelector(".client-update-dismiss").textContent = t(
        "clientUpdate.dismiss"
      );

      header.insertAdjacentElement("afterend", banner);

      const navLink = document.getElementById("nav-client-dl");
      if (navLink) {
        navLink.classList.add("nav-client-dl--highlight");
        if (data.html_url) navLink.href = data.html_url;
      }

      banner.querySelector(".client-update-dismiss").addEventListener("click", () => {
        localStorage.setItem(DISMISS_KEY, data.tag);
        banner.remove();
        navLink?.classList.remove("nav-client-dl--highlight");
      });
    } catch (_e) {
      /* ignore */
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initClientUpdateBanner);
  } else {
    initClientUpdateBanner();
  }
})();
