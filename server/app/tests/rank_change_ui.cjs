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
    let user, requests, respond, profileWrites;
    const profile = {
      player_name:"", main_character:null, use_player_name:false, birth_date:null, birth_visibility:"secret",
      country_code:"", device_type:"", device_model:"", favorite_players:[], other_games:[],
      strong_characters:[], weak_characters:[], character_winrates_public:false, bio:"", profile_links:[],
    };
    await page.route("**/*", async route => {
      const url = new URL(route.request().url());
      if (url.hostname !== "asobby.test") return route.fulfill({ status: 503, body: "" });
      const json = (data, status=200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
      if (url.pathname === "/") return route.fulfill({ contentType: "text/html", body: fs.readFileSync(path.join(staticDir, "index.html"), "utf8") });
      if (["/profile", "/players/viewer"].includes(url.pathname)) return route.fulfill({ contentType:"text/html", body:fs.readFileSync(path.join(staticDir,"players.html")) });
      if (url.pathname.startsWith("/static/")) {
        const filename = path.join(staticDir, path.basename(url.pathname));
        if (fs.existsSync(filename) && fs.statSync(filename).isFile()) return route.fulfill({
          contentType: filename.endsWith(".js") ? "application/javascript" : filename.endsWith(".css") ? "text/css" : "image/png", body: fs.readFileSync(filename),
        });
        return route.fulfill({ status: 404, body: "" });
      }
      if (url.pathname === "/auth/me") return user ? json(user) : json({detail:"invalid or expired session"},401);
      if (url.pathname === "/api/players/options") return json({ countries:["JP"], devices:["keyboard","gamepad","arcade","other"], ranks:{easy:"E",normal:"N",ex:"Ex",hard:"H",luna:"L",ph:"Ph"} });
      if (url.pathname === "/user/profile") {
        if (route.request().method() === "PUT") { profileWrites.push(route.request().postDataJSON()); return json({ok:true}); }
        return json(profile);
      }
      if (url.pathname === "/api/players/viewer") return json({ ...profile, id:"viewer", display_name:"Viewer", discord_name:"Viewer", discord_username:"viewer", rank_symbol:"N", age:null, is_owner:true, total_matches:0, unique_opponents:0, unidentified_opponent_matches:0, character_winrates:null });
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
    async function ready(where, lang) {
      await page.goto(`https://asobby.test${where}?lang=${lang}`);
      if (where === "/") await page.locator("#auth-info").waitFor();
      else await page.waitForFunction(() => document.getElementById("app").getAttribute("aria-busy") === "false");
    }
    async function open(lang, state, where="/") {
      user = { id: "viewer", name: "viewer", rank: "normal", can_choose_rank: false, can_change_rank: true, ...state };
      requests = [];
      profileWrites = [];
      respond = async (route, body, endpoint) => {
        user.rank = body.rank;
        user.can_choose_rank = false;
        user.can_change_rank = endpoint === "/rank/initial";
        await route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, rank: body.rank }) });
      };
      await ready(where, lang);
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
      await page.locator("#auth-info").waitFor();
      assert.deepEqual(requests, [{ endpoint: "/rank/initial", body: { rank: "normal" } }]);
      assert.equal(await initial.isVisible(), false);
      // No manual rank-change panel on the lobby, even with an unused allowance.
      assert.equal(await manual.count(), 0);
      // Normal navigation uses the preference saved by the language toggle.
      await page.evaluate(lang => localStorage.setItem("asobby-lang", lang), lang);
      await page.locator('nav a[href="/profile"]').click();
      await manual.waitFor();
      // Initial selection preserves the extra allowance, including choosing N.
      assert.equal(await manual.getAttribute("open"), null);
      assert.equal(await manual.evaluate(node => !node.closest("form") && !!(document.getElementById("profile-form").compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)), true);
      await manual.locator("summary").click();
      assert.deepEqual(await manual.locator("button").evaluateAll(nodes => nodes.map(n => n.dataset.rank)), ["easy", "normal", "ex", "hard", "luna"]);
      assert.equal(await manual.locator('[data-rank="normal"]').isDisabled(), true);
      // Invalid, unfinished profile edits must neither block nor be submitted by rank changes.
      await page.locator('[name="bio"]').fill("あ".repeat(401));
      assert.equal(await page.locator('[name="bio"]').evaluate(node => node.validity.valid), false);
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
      await manual.locator('[data-rank="luna"]').evaluate(node => node.onclick());
      assert.deepEqual(requests, [{ endpoint: "/rank/change", body: { rank: "hard" } }]);
      await page.evaluate(() => { window.keepProfileDraft = true; });
      release();
      await page.locator("#rank-change-result").waitFor();
      assert.equal(await page.evaluate(() => window.keepProfileDraft), true);
      assert.match(await page.locator("#rank-change-result").innerText(), /H/);
      assert.equal(await page.locator('[name="bio"]').inputValue(), "あ".repeat(401));
      assert.deepEqual(profileWrites, []);
      assert.equal(await manual.isVisible(), false);
      assert.equal(await initial.isVisible(), false);
      await page.locator('[name="bio"]').fill("Draft preserved after rank change");
      await page.locator('#profile-form button[type="submit"]').click();
      await page.locator("#save-status.success").waitFor();
      assert.equal(profileWrites.length, 1);
      assert.equal(profileWrites[0].bio, "Draft preserved after rank change");
      assert.equal("rank" in profileWrites[0], false);
      await ready("/profile", lang);
      assert.equal(await manual.count(), 0); // The consumed allowance stays hidden after reload.
      await ready("/", lang);
      assert.equal(await manual.count(), 0);
      assert.equal(await initial.isVisible(), false);

      for (const state of [
        { can_change_rank: false },
        { can_choose_rank: true, can_change_rank: false },
        // Even inconsistent/stale Ph capability data cannot expose either control.
        { rank: "ph", can_choose_rank: true, can_change_rank: true },
      ]) {
        await open(lang, state, "/profile");
        assert.equal(await manual.isVisible(), false);
        assert.equal(await initial.isVisible(), false);
      }
      await open(lang, {rank:"ph", can_choose_rank:true, can_change_rank:true});
      assert.equal(await initial.isVisible(), false);
      await open(lang, {}, "/players/viewer");
      assert.equal(await manual.count(), 0); // Not on the public profile, even one's own.
      for (const [status, detail, expected] of [
        [409, "rank change already used", lang === "ja" ? /使用済み/ : /already been used/],
        [403, "ph rank cannot be changed manually", lang === "ja" ? /現在Ph/ : /Current Ph/],
        [401, "invalid or expired session", lang === "ja" ? /セッション/ : /session|log in/i],
        [0, "", lang === "ja" ? /通信|ネットワーク/ : /network/i],
      ]) {
        await open(lang, {}, "/profile");
        await page.locator('[name="bio"]').fill("Keep this draft on error");
        respond = route => status === 0 ? route.abort() : route.fulfill({ status, contentType: "application/json", body: JSON.stringify({ detail }) });
        await manual.locator("summary").click();
        page.once("dialog", dialog => dialog.accept());
        await manual.locator('[data-rank="easy"]').click();
        await page.locator("#rank-change-error").waitFor();
        assert.match(await page.locator("#rank-change-error").innerText(), expected);
        assert.equal(await manual.locator('[data-rank="easy"]').isDisabled(), false);
        assert.equal(await manual.locator('[data-rank="normal"]').isDisabled(), true);
        assert.equal(await page.locator('[name="bio"]').inputValue(), "Keep this draft on error");
        assert.deepEqual(profileWrites, []);
      }
      for (const width of [320,1280]) {
        await page.setViewportSize({width,height:844});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      }
      await page.setViewportSize({width:390,height:844});
      user = null;
      await ready("/profile", lang);
      assert.equal(await page.locator("#gate").isVisible(), true);
      assert.equal(await manual.count(), 0);
    }
    assert.deepEqual(errors, []);
    console.log("Rank change UI passed: initial choice stays in lobby; manual choice only in collapsed profile-editor footer; JA/EN, mobile/desktop, Ph exclusion, confirm/cancel, double-submit guard, draft preservation, independent saving, persistence and error recovery.");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
