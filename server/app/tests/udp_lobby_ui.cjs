// All HTTP and EventSource data is mocked; never registers a real listing.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.argv[2] || "playwright");

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.addInitScript(() => {
      window.EventSource = class {
        constructor() { this.handlers = {}; window.mockSse = this; }
        addEventListener(name, callback) { this.handlers[name] = callback; }
        close() {}
      };
    });
    const staticDir = path.join(__dirname, "../static");
    await page.route("**/*", route => {
      const url = new URL(route.request().url());
      if (url.hostname !== "asobby.test") return route.fulfill({ status: 503, body: "" });
      const json = data => route.fulfill({ contentType: "application/json", body: JSON.stringify(data) });
      if (url.pathname === "/") return route.fulfill({ contentType: "text/html", body: fs.readFileSync(path.join(staticDir, "index.html"), "utf8") });
      if (url.pathname.startsWith("/static/")) {
        const filename = path.join(staticDir, path.basename(url.pathname));
        if (fs.existsSync(filename) && fs.statSync(filename).isFile()) return route.fulfill({
          contentType: filename.endsWith(".js") ? "application/javascript" : "image/png", body: fs.readFileSync(filename),
        });
        return route.fulfill({ status: 404, body: "" });
      }
      if (url.pathname === "/auth/me") return json({ id: "viewer", name: "viewer", rank: "normal", can_choose_rank: false });
      if (url.pathname === "/announcement") return json({ announcement: null });
      if (url.pathname === "/user/settings") return json({ favicon_notify: {} });
      if (url.pathname === "/posts") return json([]);
      return json({ count: 1 });
    });
    await page.goto("https://asobby.test/?lang=ja");
    await page.waitForFunction(() => window.mockSse?.handlers.snapshot);
    const post = {
      id: "udp", owner_name: "<img src=x onerror=alert(1)>", rank: "", rating: null,
      post_type: "casual", addr: "93.184.216.34:10800", comment: "hello", net_status: 3,
      giuroll: true, autopunch: true, direct_reachable: false, supports_messages: false,
      ping_warn_enabled: false, created_at: 1,
    };
    await page.evaluate(p => window.mockSse.handlers.snapshot({ data: JSON.stringify([p]) }), post);
    const row = page.locator("#casual-rows tr");
    await row.waitFor();
    assert.equal(await row.locator(".user-cell img").count(), 0);
    assert.equal(await row.locator(".msg-btn").count(), 0);
    assert.equal(await row.locator("td").nth(2).innerText(), "");
    assert.match(await row.innerText(), /募集中/);
    assert.doesNotMatch(await row.innerText(), /外部募集/);
    assert.match(await row.innerText(), /GIU/i);
    assert.match(await row.innerText(), /AP/);
    for (const [net_status, label] of [[2, "接続中"], [0, "状態不明"], [3, "募集中"]]) {
      await page.evaluate(p => window.mockSse.handlers.upsert({ data: JSON.stringify(p) }), { ...post, net_status });
      assert.match(await row.innerText(), new RegExp(label));
    }
    // A normal client-backed post still gets its message action.
    await page.evaluate(p => window.mockSse.handlers.upsert({ data: JSON.stringify(p) }), { ...post, supports_messages: true, giuroll: false });
    assert.equal(await row.locator(".msg-btn").count(), 1);
    await page.evaluate(p => window.mockSse.handlers.upsert({ data: JSON.stringify(p) }), {
      ...post, owner_profile_url: "/players/123", guest_user_id: "456", guest_name: "Guest",
    });
    assert.equal(await row.locator('a[href="/players/123"]').innerText(), post.owner_name);
    assert.equal(await row.locator('a[href="/players/456"]').innerText(), "Guest");
    assert.equal(await row.locator(".user-cell img").count(), 0);
    await page.evaluate(p => window.mockSse.handlers.upsert({ data: JSON.stringify(p) }), {
      ...post, owner_profile_url: "javascript:alert(1)",
    });
    assert.equal(await row.locator(".user-cell a").count(), 0);
    await page.evaluate(() => window.mockSse.handlers.close({ data: JSON.stringify({ id: "udp" }) }));
    assert.equal(await row.count(), 0);
    // Same normal rank, different evidence: both lobby tables and languages.
    for (const lang of ["ja", "en"]) {
      await page.goto(`https://asobby.test/?lang=${lang}`);
      await page.waitForFunction(() => window.mockSse?.handlers.snapshot);
      const nativeChat = { id: "native-chat", user_id: "123", name: "Native", text: "hello", ts: 1, mentions: [] };
      const externalChat = {
        id: "external-chat", user_id: "", source: "integration", ts: 2, mentions: [],
        name: "<img src=x onerror=alert(1)>", text: "<script>alert(1)</script> @everyone",
      };
      await page.evaluate(messages => window.mockSse.handlers.chat_snapshot({ data: JSON.stringify(messages) }), [nativeChat, externalChat]);
      const chatRows = page.locator("#lobby-chat-messages .chat-row");
      assert.equal(await chatRows.count(), 2);
      assert.equal(await chatRows.nth(0).locator(".chat-origin").count(), 0);
      assert.equal(await chatRows.nth(1).locator(".chat-origin").innerText(), lang === "ja" ? "外部連携" : "External");
      assert.equal(await chatRows.nth(1).locator(".chat-name img, .chat-body script").count(), 0);
      assert.equal(await chatRows.nth(1).locator(".chat-name").innerText(), externalChat.name);
      assert.equal(await chatRows.nth(1).locator(".chat-body").innerText(), externalChat.text);
      await page.evaluate(message => window.mockSse.handlers.chat_message({ data: JSON.stringify(message) }), externalChat);
      assert.equal(await chatRows.count(), 2, "SSE retries must upsert, not duplicate chat");
      const cases = lang === "ja" ? [
        ["unset", 0, "未設定"], ["initial", 0, "初期設定"],
        ["provisional", 49, "暫定・49戦"], ["ranked", 50, "ランク戦50戦"],
        ["unknown", null, "実績不明"],
      ] : [
        ["unset", 0, "Not set"], ["initial", 0, "Initial rank"],
        ["provisional", 49, "Provisional · 49 games"], ["ranked", 50, "50 ranked games"],
        ["unknown", null, "History unknown"],
      ];
      for (const post_type of ["casual", "ranked"]) {
        for (const [rank_status, ranked_games, label] of cases) {
          const p = { ...post, rank: "normal", post_type, rank_status, ranked_games };
          await page.evaluate(p => window.mockSse.handlers.snapshot({ data: JSON.stringify([p]) }), p);
          const cell = page.locator(`#${post_type}-rows .rank-cell`);
          assert.match(await cell.innerText(), /^N/);
          assert.equal(await cell.locator(".rank-status").innerText(), label);
          assert.equal(await cell.locator(".rank-status").getAttribute("data-rank-status"), rank_status);
          assert.ok(await cell.locator(".rank-status").getAttribute("aria-label"));
        }
      }
      // Ph still displays its numeric rating, and missing rank stays blank.
      await page.evaluate(p => window.mockSse.handlers.snapshot({ data: JSON.stringify([p]) }), {
        ...post, rank: "ph", rating: 123.5, rank_status: "ranked", ranked_games: 100,
      });
      assert.match(await page.locator("#casual-rows .rank-cell").innerText(), /Ph \(123\.5\)/i);
      await page.evaluate(p => window.mockSse.handlers.upsert({ data: JSON.stringify(p) }), post);
      assert.equal(await page.locator("#casual-rows .rank-cell").innerText(), "");
    }
    assert.deepEqual(errors, []);
    console.log("Lobby UI passed: UDP listings, JA/EN rank evidence in both tables, Ph rating, unknown history, safe text, messages and SSE.");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
