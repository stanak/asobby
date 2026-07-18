// プレイヤー名 / @メンション補完 (replays 検索・ロビーチャット共用)
(function () {
  "use strict";

  const SUGGEST_DEBOUNCE_MS = 250;

  function discordAvatarUrl(userId, avatarHash) {
    if (avatarHash) {
      return (
        "https://cdn.discordapp.com/avatars/" +
        userId +
        "/" +
        avatarHash +
        ".png?size=64"
      );
    }
    return "https://cdn.discordapp.com/embed/avatars/0.png";
  }

  function mentionContext(input) {
    const value = input.value;
    const pos = input.selectionStart ?? value.length;
    const before = value.slice(0, pos);
    const at = before.lastIndexOf("@");
    if (at < 0) return null;
    const query = before.slice(at + 1);
    if (query.includes("\n") || query.includes(" ")) return null;
    return { query, start: at, end: pos };
  }

  /**
   * @param {object} opts
   * @param {HTMLInputElement|HTMLTextAreaElement} opts.input
   * @param {HTMLElement} opts.suggestEl
   * @param {string} opts.fetchUrl
   * @param {"plain"|"mention"} [opts.mode]
   * @param {() => string} [opts.getQuery] plain モード用
   */
  function attachPlayerSuggest(opts) {
    const input = opts.input;
    const suggestEl = opts.suggestEl;
    const fetchUrl = opts.fetchUrl;
    const mode = opts.mode || "plain";
    const t = window.t || ((key) => key);

    let suggestTimer = null;
    let suggestSeq = 0;
    let suggestItems = [];
    let suggestActive = -1;
    let suggestMouseDown = false;
    let mentionRange = null;

    function closeSuggest() {
      suggestEl.hidden = true;
      suggestEl.replaceChildren();
      suggestItems = [];
      suggestActive = -1;
      mentionRange = null;
    }

    function selectSuggest(idx) {
      if (idx < 0 || idx >= suggestItems.length) return;
      const item = suggestItems[idx];
      if (mode === "mention" && mentionRange) {
        const value = input.value;
        const insert = "@" + item.name + " ";
        input.value =
          value.slice(0, mentionRange.start) +
          insert +
          value.slice(mentionRange.end);
        const caret = mentionRange.start + insert.length;
        input.setSelectionRange(caret, caret);
      } else {
        input.value = item.name;
      }
      closeSuggest();
      input.focus();
    }

    function renderSuggest() {
      suggestEl.replaceChildren();
      if (!suggestItems.length) {
        suggestEl.hidden = true;
        return;
      }
      const frag = document.createDocumentFragment();
      suggestItems.forEach((item, idx) => {
        const row = document.createElement("div");
        row.className =
          "player-suggest-item" + (idx === suggestActive ? " active" : "");
        row.setAttribute("role", "option");
        row.dataset.idx = String(idx);

        if (item.kind === "user" && item.user_id) {
          const img = document.createElement("img");
          img.className = "player-suggest-avatar";
          img.src = discordAvatarUrl(item.user_id, item.avatar);
          img.loading = "lazy";
          img.referrerPolicy = "no-referrer";
          img.alt = "";
          row.appendChild(img);
        }

        const nameSpan = document.createElement("span");
        nameSpan.className = "player-suggest-name";
        nameSpan.textContent = item.name;
        row.appendChild(nameSpan);

        if (mode === "plain") {
          const badge = document.createElement("span");
          badge.className = "player-suggest-badge";
          badge.textContent =
            item.kind === "user"
              ? t("replays.badgeDiscord")
              : t("replays.badgeProfile");
          row.appendChild(badge);
        }

        frag.appendChild(row);
      });
      suggestEl.appendChild(frag);
      suggestEl.hidden = false;
    }

    function highlightSuggest(idx) {
      if (!suggestItems.length) return;
      if (idx < 0) idx = suggestItems.length - 1;
      if (idx >= suggestItems.length) idx = 0;
      suggestActive = idx;
      const rows = suggestEl.querySelectorAll(".player-suggest-item");
      rows.forEach((row, i) => {
        row.classList.toggle("active", i === suggestActive);
      });
      const activeRow = rows[suggestActive];
      if (activeRow) activeRow.scrollIntoView({ block: "nearest" });
    }

    async function fetchSuggest(query) {
      const seq = ++suggestSeq;
      try {
        const params = new URLSearchParams({ q: query });
        const res = await fetch(fetchUrl + "?" + params.toString());
        if (!res.ok) return;
        const data = await res.json();
        if (seq !== suggestSeq) return;
        if (mode === "mention") {
          const ctx = mentionContext(input);
          if (!ctx || ctx.query !== query) return;
          mentionRange = { start: ctx.start, end: ctx.end };
        } else if (opts.getQuery) {
          if (query !== opts.getQuery()) return;
        }
        if (!data.ok || !Array.isArray(data.suggestions)) {
          closeSuggest();
          return;
        }
        suggestItems = data.suggestions;
        suggestActive = -1;
        renderSuggest();
      } catch (_err) {
        if (seq === suggestSeq) closeSuggest();
      }
    }

    function scheduleSuggest() {
      if (suggestTimer) clearTimeout(suggestTimer);
      let query = "";
      if (mode === "mention") {
        const ctx = mentionContext(input);
        if (!ctx || !ctx.query) {
          closeSuggest();
          return;
        }
        mentionRange = { start: ctx.start, end: ctx.end };
        query = ctx.query;
      } else {
        query = opts.getQuery ? opts.getQuery() : input.value.trim();
        if (!query) {
          closeSuggest();
          return;
        }
      }
      suggestTimer = setTimeout(() => fetchSuggest(query), SUGGEST_DEBOUNCE_MS);
    }

    input.addEventListener("input", scheduleSuggest);

    input.addEventListener("keydown", (e) => {
      if (suggestEl.hidden || !suggestItems.length) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        highlightSuggest(suggestActive + 1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        highlightSuggest(suggestActive - 1);
      } else if (e.key === "Enter" && suggestActive >= 0) {
        e.preventDefault();
        selectSuggest(suggestActive);
      } else if (e.key === "Escape") {
        closeSuggest();
      }
    });

    input.addEventListener("blur", () => {
      if (suggestMouseDown) return;
      closeSuggest();
    });

    suggestEl.addEventListener("mousedown", (e) => {
      suggestMouseDown = true;
      e.preventDefault();
    });

    document.addEventListener("mouseup", () => {
      suggestMouseDown = false;
    });

    suggestEl.addEventListener("click", (e) => {
      const row = e.target.closest(".player-suggest-item");
      if (!row) return;
      const idx = Number(row.dataset.idx);
      if (!Number.isNaN(idx)) selectSuggest(idx);
    });

    document.addEventListener("click", (e) => {
      if (!input.contains(e.target) && !suggestEl.contains(e.target)) {
        closeSuggest();
      }
    });

    return { close: closeSuggest };
  }

  window.attachPlayerSuggest = attachPlayerSuggest;
})();
