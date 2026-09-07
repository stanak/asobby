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
    await page.evaluate(() => window.mockSse.handlers.close({ data: JSON.stringify({ id: "udp" }) }));
    assert.equal(await row.count(), 0);
    assert.deepEqual(errors, []);
    console.log("UDP lobby UI passed: normal casual table, badges, states, safe text, message capability, SSE removal.");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
