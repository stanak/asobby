// Run with: node tests/admin_integrations_ui.cjs [path-to-playwright-module]
// Every HTTP request is fulfilled locally. No real account or webhook is used.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.argv[2] || "playwright");

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 414, height: 896 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("dialog", (dialog) => dialog.accept());
    const staticDir = path.join(__dirname, "../static");
    let isAdmin = true;
    let records = [];
    let creates = 0;
    let tests = 0;
    let edits = [];
    const credentials = (record) => ({
      integration: record, api_key: "local-api-key", signing_secret: "local-signature-key",
    });
    await page.route("**/*", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      assert.equal(url.hostname, "asobby.test", "Unexpected external request");
      const respond = (body, status = 200) => route.fulfill({
        status, contentType: "application/json", body: JSON.stringify(body),
      });
      if (url.pathname === "/admin") return route.fulfill({
        contentType: "text/html", body: fs.readFileSync(path.join(staticDir, "admin.html"), "utf8"),
      });
      if (url.pathname === "/static/admin-integrations.js") return route.fulfill({
        contentType: "application/javascript", body: fs.readFileSync(path.join(staticDir, "admin-integrations.js"), "utf8"),
      });
      if (url.pathname === "/auth/me") return respond({ id: "local-user", is_admin: isAdmin });
      if (url.pathname === "/announcement") return respond({ announcement: null });
      if (url.pathname === "/admin/feedback") return respond({ entries: [{
        category: "other", user_name: "<img src=x onerror=alert(1)>", user_id: "123",
        client_version: "<img src=x onerror=alert(1)>", text: "feedback", ts: 1,
      }] });
      if (url.pathname === "/admin/integrations") {
        if (req.method() === "GET") return respond({
          integrations: records, api_url: "https://asobby.test/api/v1/lobby", storage_error: false,
        });
        creates++;
        const body = req.postDataJSON();
        const item = {
          id: String(creates), name: body.name, enabled: body.enabled,
          include_address: body.include_address, last_success_at: null, last_error: null,
          allow_posting: body.allow_posting,
          webhook_host: body.webhook_url ? new URL(body.webhook_url).hostname : "",
        };
        records.push(item);
        return respond(credentials(item), 201);
      }
      const match = url.pathname.match(/^\/admin\/integrations\/([^/]+)(\/rotate|\/test)?$/);
      if (match) {
        const record = records.find((item) => item.id === match[1]);
        if (match[2] === "/rotate") return respond(credentials(record));
        if (match[2] === "/test") {
          tests++;
          return respond({ queued: true }, 202);
        }
        if (req.method() === "DELETE") {
          records = records.filter((item) => item.id !== match[1]);
          return route.fulfill({ status: 204, body: "" });
        }
        const patch = req.postDataJSON();
        edits.push(patch);
        Object.assign(record, patch);
        if ("webhook_url" in patch) record.webhook_host = patch.webhook_url ? new URL(patch.webhook_url).hostname : "";
        return respond({ integration: record });
      }
      return route.fulfill({ status: 404, body: "" });
    });

    await page.goto("https://asobby.test/admin");
    await page.locator("#integrations-panel").waitFor({ state: "visible" });
    await page.locator(".feedback-item").waitFor();
    assert.equal(await page.locator(".feedback-item img").count(), 0);
    await page.locator("#integration-name").fill("<img src=x onerror=alert(1)>");
    await page.locator("#integration-url").fill("https://receiver.example/private-token");
    await page.locator("#integration-save").click();
    await page.locator("#integration-credentials").waitFor({ state: "visible" });
    assert.equal(records[0].allow_posting, false);
    assert.equal(await page.locator("#integration-api-key").inputValue(), "local-api-key");
    await page.locator(".integration-card").waitFor();
    assert.equal(await page.locator(".integration-card img").count(), 0);
    assert.equal(await page.locator("#integration-list").innerText().then((text) => text.includes("private-token")), false);
    const card = page.locator(".integration-card");
    await card.getByRole("button", { name: "編集", exact: true }).click();
    assert.equal(await page.locator("#integration-url").inputValue(), "");
    await page.locator("#integration-name").fill("Renamed");
    await page.locator("#integration-posting").check();
    await page.locator("#integration-save").click();
    await page.waitForFunction(() => document.querySelector(".integration-card strong").textContent === "Renamed");
    assert.equal("webhook_url" in edits.at(-1), false);
    assert.equal(records[0].allow_posting, true);
    await card.getByRole("button", { name: "編集", exact: true }).click();
    assert.equal(await page.locator("#integration-posting").isChecked(), true);
    await page.locator("#integration-posting").uncheck();
    await page.locator("#integration-save").click();
    await page.waitForFunction(() => document.querySelector("#integration-list").textContent.includes("一覧取得のみ"));
    assert.equal(records[0].allow_posting, false);
    await card.getByRole("button", { name: "停止", exact: true }).click();
    await card.getByRole("button", { name: "再開", exact: true }).waitFor();
    assert.equal(records[0].enabled, false);
    assert.equal(await card.getByRole("button", { name: "テスト通知", exact: true }).count(), 0);
    await card.getByRole("button", { name: "再開", exact: true }).click();
    await card.getByRole("button", { name: "テスト通知", exact: true }).click();
    await page.waitForFunction(() => document.querySelector("#integration-status").textContent.includes("予約"));
    assert.equal(tests, 1);
    await card.getByRole("button", { name: "キー再発行", exact: true }).click();
    await page.locator("#integration-credentials").waitFor({ state: "visible" });
    await page.locator("#integration-hide-credentials").click();
    assert.equal(await page.locator("#integration-api-key").inputValue(), "");
    await card.getByRole("button", { name: "削除", exact: true }).click();
    await card.waitFor({ state: "detached" });

    await page.locator("#integration-name").fill("API only");
    await page.locator("#integration-api-only").check();
    await page.locator("#integration-save").click();
    await page.locator(".integration-card").waitFor();
    assert.equal(records[0].webhook_host, "");
    await page.locator(".integration-card").getByRole("button", { name: "編集", exact: true }).click();
    await page.locator("#integration-api-only").uncheck();
    assert.equal(await page.locator("#integration-url").evaluate((element) => element.required), true);
    await page.locator("#integration-url").fill("https://receiver.example/new-hook");
    await page.locator("#integration-address").check();
    await page.locator("#integration-save").click();
    await page.waitForFunction(() => document.querySelector("#integration-list").textContent.includes("receiver.example"));
    assert.equal(records[0].include_address, true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);

    isAdmin = false;
    await page.reload();
    await page.locator("#gate").waitFor({ state: "visible" });
    assert.equal(await page.locator("#integrations-panel").isVisible(), false);
    console.log("Admin UI passed: create, edit, URL preservation, pause/resume, test, rotate, delete, API-only, IP scope, XSS text rendering, mobile layout, non-admin gate.");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
