// Requests are served from local source; never contact accounts or production APIs.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.argv[2] || "playwright");
const staticDir = path.join(__dirname, "../static");

(async () => {
  const browser = await chromium.launch({headless:true, ...(process.platform === "win32" ? {channel:"msedge"} : {})});
  try {
    const context = await browser.newContext({javaScriptEnabled:false});
    const requests = [];
    await context.route("**/*", async route => {
      const url = new URL(route.request().url());
      requests.push(url);
      assert.equal(url.hostname, "asobby.test");
      let file;
      if (url.pathname === "/privacy") file = url.searchParams.get("lang") === "en" ? "privacy-en.html" : "privacy.html";
      else {
        assert.ok(url.pathname.startsWith("/static/"));
        file = path.basename(url.pathname);
      }
      const contentType = file.endsWith(".html") ? "text/html" : file.endsWith(".css") ? "text/css" : file.endsWith(".svg") ? "image/svg+xml" : "image/png";
      await route.fulfill({contentType,body:fs.readFileSync(path.join(staticDir,file))});
    });
    const page = await context.newPage();
    for (const lang of ["ja", "en"]) {
      for (const width of [320,390,1280]) {
        await page.setViewportSize({width,height:844});
        await page.goto(`https://asobby.test/privacy?lang=${lang}`);
        assert.equal(await page.locator("html").getAttribute("lang"),lang);
        assert.equal(await page.locator("h1").innerText(),lang === "ja" ? "プライバシーポリシー" : "Privacy policy");
        assert.equal(await page.locator("script").count(),0);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),true,`${lang} at ${width}px`);
        await page.locator('nav a[href="#contact"]').first().click();
        assert.equal(await page.locator('#contact a[href="mailto:midoristar@empengineer.cool"]').isVisible(),true);
        assert.ok(page.url().endsWith("#contact"));
      }
    }
    await page.locator('a[hreflang="ja"]').click();
    assert.equal(await page.locator("html").getAttribute("lang"),"ja");
    await page.locator('a[hreflang="en"]').click();
    assert.equal(await page.locator("html").getAttribute("lang"),"en");
    assert.equal((await context.cookies()).length,0);
    assert.ok(requests.every(url => !/auth|presence|analytics|client\/latest/.test(url.pathname)));
    if (process.argv[3]) await page.screenshot({path:process.argv[3],fullPage:true});
    await context.close();

    const jsPage = await browser.newPage();
    await jsPage.route("**/*", route => route.fulfill({contentType:"text/html",body:'<html><body><a href="/privacy" data-i18n="nav.privacy"></a><a href="/privacy#contact" data-privacy-contact>Contact</a></body></html>'}));
    for (const lang of ["ja","en"]) {
      await jsPage.goto(`https://asobby.test/?lang=${lang}`);
      await jsPage.addScriptTag({content:fs.readFileSync(path.join(staticDir,"i18n.js"),"utf8")});
      await jsPage.evaluate(() => window.applyDocumentI18n());
      assert.equal(await jsPage.locator('[data-i18n="nav.privacy"]').getAttribute("href"),`/privacy?lang=${lang}`);
      assert.equal(await jsPage.locator('[data-i18n="nav.privacy"]').innerText(),lang === "ja" ? "プライバシーポリシー" : "Privacy policy");
      assert.equal(await jsPage.locator('[data-privacy-contact]').getAttribute("href"),`/privacy?lang=${lang}#contact`);
    }
    console.log("privacy UI: JS-free JA/EN, mobile/desktop, contacts and localized navigation passed");
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error);process.exitCode=1;});
