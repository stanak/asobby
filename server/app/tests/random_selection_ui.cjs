// Exercise real pages with mocked data only; no production requests or writes.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.argv[2] || "playwright");

(async () => {
  const browser = await chromium.launch({headless:true, ...(process.platform === "win32" ? {channel:"msedge"} : {})});
  try {
    const page = await browser.newPage(), errors = [], searches = [];
    page.on("pageerror", error => errors.push(error.message));
    const root = path.join(__dirname, "../static");
    const match = {id:"a".repeat(32),match_id:"a".repeat(32),played_at:1790000000,my_side:"host",winner:"host",
      host_char:20,guest_char:5,host_actual_char:0,guest_actual_char:5,host_random:true,guest_random:false,
      host_profile:"hp",guest_profile:"gp",host_name:"Host",guest_name:"Guest",host_rank:"ph",guest_rank:"ph",
      host_rating:100,guest_rating:90,ranked:true,match_rank:"ph",has_replay:true,host_wins:2,guest_wins:0};
    await page.route("**/*", async route => {
      const url = new URL(route.request().url());
      const json = (value,status=200) => route.fulfill({status,contentType:"application/json",body:JSON.stringify(value)});
      if (url.hostname !== "asobby.test") return route.fulfill({status:503,body:""});
      if (["/stats","/replays"].includes(url.pathname)) return route.fulfill({contentType:"text/html",body:fs.readFileSync(path.join(root,url.pathname.slice(1)+".html"))});
      if (url.pathname.startsWith("/static/")) {
        const file = path.join(root,path.basename(url.pathname));
        if (fs.existsSync(file)) return route.fulfill({contentType:file.endsWith(".js") ? "application/javascript" : "image/svg+xml",body:fs.readFileSync(file)});
      }
      if (url.pathname === "/stats/me") return json({user:{id:"test",name:"Test",rank:"ph"},ranked:{rank:"ph",rating:100,
        char_ratings:Array.from({length:21},(_,char)=>({char,rating:char===20?120:100}))}});
      if (url.pathname === "/stats/me/matches") return json({matches:[match],total:1});
      if (url.pathname === "/replays/search") {
        searches.push(url.searchParams);
        return json({ok:true,replays:[match],total:1});
      }
      if (url.pathname === "/auth/me") return json({logged_in:false},401);
      return json({});
    });
    for (const lang of ["ja","en"]) {
      await page.goto(`https://asobby.test/stats?lang=${lang}`);
      await page.locator('#history-rows tr').first().waitFor();
      assert.equal(await page.locator('#history-rows tr td').nth(1).innerText(),"Random");
      const random = page.locator('tr[data-dim="myChar"][data-value="20"] td');
      assert.deepEqual((await random.allTextContents()).slice(0,4),["Random","120","1","1"]);
      const actual = page.locator('tr[data-dim="myChar"][data-value="0"] td');
      assert.deepEqual((await actual.allTextContents()).slice(0,4),["Reimu","100","0","0"]);
      assert.equal(await page.locator('tr[data-dim="myChar"]').count(),21);
      await page.goto(`https://asobby.test/replays?lang=${lang}`);
      await page.getByText(/Random \(Reimu\)/).waitFor();
      assert.equal(await page.locator('#char1 option').count(),22); // all + 21
      for (const character of ["0","20"]) {
        await page.locator('#char1').selectOption(character);
        const response = page.waitForResponse(r => r.url().includes('/replays/search?') && new URL(r.url()).searchParams.get('char1') === character);
        await page.locator('#search-form button[type="submit"]').click();
        await response;
        assert.equal(searches.at(-1).get('char1'),character);
        assert.match(await page.locator('body').innerText(),/Random \(Reimu\)/);
      }
    }
    assert.deepEqual(errors,[]);
    console.log("Random UI passed: JA/EN history, 21 Ph ratings, no actual-character double count, replay labels and both searches.");
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
