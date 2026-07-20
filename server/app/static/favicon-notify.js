(function () {
  "use strict";

  const NET_BATTLE = 4;
  const LOCAL_API_BASE = "http://127.0.0.1:49152";
  const DEFAULT_FAVICON_NOTIFY = {
    ranked_enabled: true,
    casual_enabled: true,
    ranked_same_band_only: true,
    max_ping_ms: 60,
    require_ping: false,
    exclude_in_battle: true,
  };

  function normalizeFaviconNotify(raw) {
    const src = raw && typeof raw === "object" ? raw : {};
    const out = { ...DEFAULT_FAVICON_NOTIFY };
    for (const key of Object.keys(DEFAULT_FAVICON_NOTIFY)) {
      if (!(key in src)) continue;
      const val = src[key];
      if (key === "max_ping_ms") {
        const n = Number.parseInt(val, 10);
        if (Number.isFinite(n)) out[key] = Math.max(1, Math.min(999, n));
      } else {
        out[key] = !!val;
      }
    }
    return out;
  }

  function normalizeUserSettings(raw) {
    const src = raw && typeof raw === "object" ? raw : {};
    return { favicon_notify: normalizeFaviconNotify(src.favicon_notify) };
  }

  function isOwnPost(post, ctx) {
    if (!ctx || !ctx.loggedIn) return false;
    if (ctx.myUserId && post.owner_user_id && post.owner_user_id === ctx.myUserId) {
      return true;
    }
    return !!(ctx.myName && post.owner_name === ctx.myName);
  }

  function pingPasses(prefs, pingMs) {
    const maxPing = Number(prefs.max_ping_ms) || 60;
    if (prefs.require_ping) {
      return pingMs != null && pingMs <= maxPing;
    }
    if (pingMs != null && pingMs > maxPing) return false;
    return true;
  }

  function classifyPost(post, prefs, ctx, pingMs) {
    if (prefs.exclude_in_battle && (post.guest_connected || post.net_status === NET_BATTLE)) {
      return { ranked: false, casual: false };
    }
    if (!pingPasses(prefs, pingMs)) {
      return { ranked: false, casual: false };
    }

    const postType = post.post_type || "casual";
    let ranked = false;
    let casual = false;
    if (postType === "ranked") {
      if (prefs.ranked_enabled) {
        if (prefs.ranked_same_band_only) {
          const band = (ctx.myRank || "").toLowerCase();
          if (band && (post.rank || "easy").toLowerCase() === band) ranked = true;
        } else {
          ranked = true;
        }
      }
    } else if (prefs.casual_enabled) {
      casual = true;
    }
    return { ranked, casual };
  }

  function evaluateBadges(posts, prefs, ctx, pingByPostId) {
    const normalized = normalizeFaviconNotify(prefs);
    const pings = pingByPostId || new Map();
    let ranked = false;
    let casual = false;
    for (const post of posts) {
      if (isOwnPost(post, ctx)) continue;
      const pingMs = pings instanceof Map ? pings.get(post.id) : pings[post.id];
      const hit = classifyPost(post, normalized, ctx, pingMs);
      ranked = ranked || hit.ranked;
      casual = casual || hit.casual;
    }
    return { ranked, casual };
  }

  function parsePostAddr(addr) {
    if (!addr || typeof addr !== "string") return null;
    const idx = addr.lastIndexOf(":");
    if (idx <= 0) return null;
    const host = addr.slice(0, idx);
    const port = Number.parseInt(addr.slice(idx + 1), 10);
    if (!host || !Number.isFinite(port)) return null;
    return { host, port };
  }

  async function checkClientPingApi() {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 1200);
    try {
      const res = await fetch(`${LOCAL_API_BASE}/health`, { signal: ctrl.signal });
      return res.ok;
    } catch {
      return false;
    } finally {
      clearTimeout(timer);
    }
  }

  async function refreshPostPings(posts, pingByPostId) {
    const available = await checkClientPingApi();
    if (!available) return false;
    const targets = [];
    for (const post of posts) {
      const parsed = parsePostAddr(post.addr);
      if (!parsed) continue;
      targets.push({
        id: post.id,
        host: parsed.host,
        port: parsed.port,
        autopunch: !!post.autopunch,
      });
    }
    if (!targets.length) return true;
    try {
      const res = await fetch(`${LOCAL_API_BASE}/lobby/ping`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ targets }),
      });
      if (!res.ok) return false;
      const data = await res.json();
      if (!data.ok || !Array.isArray(data.results)) return false;
      for (const item of data.results) {
        pingByPostId.set(item.id, item.ok ? item.rtt_ms : null);
      }
      return true;
    } catch {
      return false;
    }
  }

  async function fetchUserSettings() {
    const res = await fetch("/user/settings", { credentials: "same-origin" });
    if (!res.ok) throw new Error("settings fetch failed");
    const data = await res.json();
    return normalizeUserSettings(data);
  }

  function applyBadges(badges) {
    if (!window.AsobbyFavicon || !window.AsobbyFavicon.setBadges) return;
    AsobbyFavicon.setBadges(badges);
  }

  async function initBackgroundNotify(options) {
    const intervalMs = (options && options.intervalMs) || 15000;
    let prefs = normalizeFaviconNotify(DEFAULT_FAVICON_NOTIFY);
    let ctx = {
      loggedIn: false,
      myUserId: null,
      myName: "",
      myRank: "",
    };
    const pingByPostId = new Map();
    let posts = [];

    async function refreshAuth() {
      try {
        const res = await fetch("/auth/me", { credentials: "same-origin" });
        if (!res.ok) {
          ctx = { loggedIn: false, myUserId: null, myName: "", myRank: "" };
          applyBadges({ ranked: false, casual: false });
          return;
        }
        const user = await res.json();
        ctx = {
          loggedIn: true,
          myUserId: user.id || null,
          myName: user.name || "",
          myRank: (user.rank || "").toLowerCase(),
        };
        if (user.settings) {
          prefs = normalizeFaviconNotify(user.settings.favicon_notify);
        }
      } catch {
        ctx = { loggedIn: false, myUserId: null, myName: "", myRank: "" };
        applyBadges({ ranked: false, casual: false });
      }
    }

    async function refreshPosts() {
      if (!ctx.loggedIn) {
        posts = [];
        applyBadges({ ranked: false, casual: false });
        return;
      }
      try {
        const res = await fetch("/posts", { credentials: "same-origin" });
        if (!res.ok) {
          posts = [];
          applyBadges({ ranked: false, casual: false });
          return;
        }
        posts = await res.json();
      } catch {
        posts = [];
      }
    }

    async function refreshAll() {
      await refreshAuth();
      await refreshPosts();
      if (!ctx.loggedIn) return;
      await refreshPostPings(posts, pingByPostId);
      applyBadges(evaluateBadges(posts, prefs, ctx, pingByPostId));
    }

    await refreshAll();
    setInterval(refreshAll, intervalMs);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") void refreshAll();
    });

    return {
      reloadSettings: async () => {
        try {
          const settings = await fetchUserSettings();
          prefs = settings.favicon_notify;
        } catch {
          /* keep current */
        }
        applyBadges(evaluateBadges(posts, prefs, ctx, pingByPostId));
      },
    };
  }

  window.AsobbyNotify = {
    DEFAULT_FAVICON_NOTIFY,
    normalizeFaviconNotify,
    normalizeUserSettings,
    isOwnPost,
    pingPasses,
    classifyPost,
    evaluateBadges,
    parsePostAddr,
    checkClientPingApi,
    refreshPostPings,
    fetchUserSettings,
    initBackgroundNotify,
  };
})();
