// All HTTP/SSE data is mocked. Never connects to production or changes a real rank.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.argv[2] || "playwright");

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.platform === "win32" ? {channel:"msedge"} : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.addInitScript(() => {
      window.EventSource = class { addEventListener() {} close() {} };
    });
    const staticDir = path.join(__dirname, "../static");
    let user, requests, respond;
    await page.route("**/*", async route => {
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
      if (url.pathname === "/auth/me") return json(user);
      if (url.pathname === "/announcement") return json({ announcement: null });
      if (url.pathname === "/user/settings") return json({ favicon_notify: {} });
      if (url.pathname === "/posts") return json([]);
      if (url.pathname.startsWith("/rank/")) {
        const body = route.request().postDataJSON();
        requests.push({ endpoint: url.pathname, body });
        assert.equal(route.request().headers()["content-type"], "application/json");
        return respond(route, body, url.pathname);
      }
      return json({ count: 1 });
    });
    const manual = page.locator("#rank-change-panel");
    const initial = page.locator("#rank-choice-banner");
    async function open(lang, state) {
      user = { id: "viewer", name: "viewer", rank: "normal", can_choose_rank: false, can_change_rank: true, ...state };
      requests = [];
      respond = async (route, body, endpoint) => {
        user.rank = body.rank;
        user.can_choose_rank = false;
        user.can_change_rank = endpoint === "/rank/initial";
        await route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, rank: body.rank }) });
      };
      await page.goto(`https://asobby.test/?lang=${lang}`);
      await page.locator("#auth-info").waitFor();
    }
    for (const lang of ["ja", "en"]) {
      await open(lang, { can_choose_rank: true, can_change_rank: false });
      assert.equal(await initial.isVisible(), true);
      assert.equal(await manual.isVisible(), false);
      assert.equal(await initial.locator("button").count(), 5);
      page.once("dialog", dialog => dialog.accept());
      await Promise.all([
        page.waitForEvent("load"), initial.locator('[data-rank="normal"]').click(),
      ]);
      await manual.waitFor();
      assert.deepEqual(requests, [{ endpoint: "/rank/initial", body: { rank: "normal" } }]);
      assert.equal(await initial.isVisible(), false);
      // Initial selection preserves the extra allowance, including choosing N.
      assert.equal(await manual.getAttribute("open"), null);
      await manual.locator("summary").click();
      assert.deepEqual(await manual.locator("button").evaluateAll(nodes => nodes.map(n => n.dataset.rank)), ["easy", "normal", "ex", "hard", "luna"]);
      assert.equal(await manual.locator('[data-rank="normal"]').isDisabled(), true);
      requests = [];
      let confirmation;
      page.once("dialog", async dialog => { confirmation = dialog.message(); await dialog.dismiss(); });
      await manual.locator('[data-rank="hard"]').click();
      assert.equal(requests.length, 0);
      assert.match(confirmation, lang === "ja" ? /一度しか使えず/ : /only be used once/);
      assert.match(confirmation, /N/);
      assert.match(confirmation, /H/);

      // All choices are disabled during submission; a second invocation cannot send.
      let release;
      const hold = new Promise(resolve => { release = resolve; });
      const success = respond;
      respond = async (...args) => { await hold; return success(...args); };
      page.once("dialog", dialog => dialog.accept());
      await manual.locator('[data-rank="hard"]').click();
      await page.waitForFunction(() => [...document.querySelectorAll("#rank-change-btns button")].every(b => b.disabled));
      await page.evaluate(() => chooseRank("luna", "L", true));
      assert.deepEqual(requests, [{ endpoint: "/rank/change", body: { rank: "hard" } }]);
      const reloaded = page.waitForEvent("load");
      release();
      await reloaded;
      await page.locator("#auth-info").waitFor();
      assert.equal(await manual.isVisible(), false);
      assert.equal(await initial.isVisible(), false);

      for (const state of [
        { can_change_rank: false },
        // Even inconsistent/stale Ph capability data cannot expose either control.
        { rank: "ph", can_choose_rank: true, can_change_rank: true },
      ]) {
        await open(lang, state);
        assert.equal(await manual.isVisible(), false);
        assert.equal(await initial.isVisible(), false);
      }
      for (const [status, detail, expected] of [
        [409, "rank change already used", lang === "ja" ? /使用済み/ : /already been used/],
        [403, "ph rank cannot be changed manually", lang === "ja" ? /現在Ph/ : /Current Ph/],
        [401, "invalid or expired session", lang === "ja" ? /セッション/ : /session|log in/i],
        [0, "", lang === "ja" ? /通信|ネットワーク/ : /network/i],
      ]) {
        await open(lang, {});
        respond = route => status === 0 ? route.abort() : route.fulfill({ status, contentType: "application/json", body: JSON.stringify({ detail }) });
        await manual.locator("summary").click();
        page.once("dialog", dialog => dialog.accept());
        await manual.locator('[data-rank="easy"]').click();
        await page.locator("#rank-change-error").waitFor();
        assert.match(await page.locator("#rank-change-error").innerText(), expected);
        assert.equal(await manual.locator('[data-rank="easy"]').isDisabled(), false);
        assert.equal(await manual.locator('[data-rank="normal"]').isDisabled(), true);
      }
    }
    assert.deepEqual(errors, []);
    console.log("Rank change UI passed: JA/EN, mobile, separate initial/extra allowance, Ph exclusion, confirmation/cancel, double-submit protection, success/reload and error recovery.");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
