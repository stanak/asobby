// Mock every request: no production statistics, profile data or writes.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.argv[2] || "playwright");

(async () => {
  const browser = await chromium.launch({headless:true, ...(process.platform === "win32" ? {channel:"msedge"} : {})});
  try {
    const page = await browser.newPage({viewport:{width:1280,height:900}}), errors = [], queries = [];
    page.on("pageerror", e => errors.push(e.message));
    const staticDir = path.join(__dirname, "../static"), ranks = {easy:"E",normal:"N",ex:"Ex",hard:"H",luna:"L",ph:"Ph"};
    let loggedIn = true, serverError = false;
    function data(q) {
      const twenties = q.get("age_band") === "20", hidden = q.get("rank") === "ph";
      const groups = map => Object.entries(map).map(([key,count]) => ({key,count,suppressed:count === null}));
      return {
        filters:Object.fromEntries(q), as_of:"2026-09-19", suppressed:hidden, min_players:5, age_scope:"consented", match_scope:"all_confirmed",
        total_players:hidden ? null : twenties ? 10 : 20, match_players:hidden ? null : 10, unique_matches:hidden ? null : 5,
        participations:hidden ? null : 10, unknown_character_games:hidden ? null : 0,
        distributions:hidden ? {age_band:[],rank:[],country:[],device:[]} : {
          age_band:groups({0:0,10:0,20:twenties?10:10,30:twenties?0:5,40:0,50:0,60:0,70:0,80:0,90:0,100:0,unknown:twenties?0:5}),
          rank:groups({easy:twenties?0:5,normal:5,ex:twenties?0:5,hard:5,luna:0,ph:0,unknown:0}),
          country:groups({JP:twenties?5:10,US:5,unknown:twenties?0:5}),
          device:groups({keyboard:5,gamepad:twenties?5:10,arcade:0,other:0,unknown:twenties?0:5}),
        },
        characters:hidden ? [] : Array.from({length:21}, (_, char) => ({char, games:char===0?5:char===7?null:0,
          wins:char===0?4:char===7?null:0, losses:char===7?null:0, draws:char===0?1:char===7?null:0,
          win_rate:char===0?.8:null, suppressed:char===7})),
      };
    }
    await page.route("**/*", async route => {
      const url = new URL(route.request().url());
      if (url.hostname !== "asobby.test") return route.fulfill({status:503,body:""});
      const json = (value,status=200) => route.fulfill({status,contentType:"application/json",body:JSON.stringify(value)});
      if (url.pathname === "/players/statistics") return route.fulfill({contentType:"text/html",body:fs.readFileSync(path.join(staticDir,"players.html"))});
      if (url.pathname.startsWith("/static/")) {
        const file = path.join(staticDir,path.basename(url.pathname));
        if (!fs.existsSync(file)) return route.fulfill({status:404,body:""});
        return route.fulfill({contentType:file.endsWith(".js")?"application/javascript":file.endsWith(".css")?"text/css":"image/png",body:fs.readFileSync(file)});
      }
      if (url.pathname === "/auth/me") return json(loggedIn?{id:"viewer",name:"Viewer"}:{detail:"login required"},loggedIn?200:401);
      if (url.pathname === "/api/players/options") return json({countries:["JP","US"],ranks,devices:["keyboard","gamepad","arcade","other"]});
      if (url.pathname === "/api/players/analytics") {
        queries.push(url.searchParams);
        return json(serverError?{detail:"Temporary statistics error"}:data(url.searchParams),serverError?503:200);
      }
      return json({detail:"unexpected API"},404);
    });
    async function idle() { await page.waitForFunction(() => document.querySelector('#statistics-results')?.getAttribute('aria-busy') === 'false'); }
    async function ready(query) {
      await page.goto(`https://asobby.test/players/statistics?${query}`);
      await page.waitForFunction(() => document.getElementById("app").getAttribute("aria-busy") === "false");
    }
    async function noOverflow() { assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),true); }
    for (const lang of ["ja","en"]) {
      await ready(`lang=${lang}`); await idle();
      assert.equal(await page.locator('[data-distribution]').count(),4);
      assert.equal(await page.locator('#statistics-results tbody tr').count(),21);
      assert.match(await page.locator('[data-character="0"]').innerText(),/80.0%/);
      assert.match(await page.locator('[data-character="7"]').innerText(),lang === "ja"?/非表示/:/Hidden/);
      assert.doesNotMatch(await page.locator('#statistics-results').innerText(),/NaN|Infinity|undefined|2000-09-20/);
      await noOverflow();
      await page.locator('[data-distribution="age_band"] button[data-value="20"]').click(); await idle();
      assert.equal(queries.at(-1).get('age_band'),'20');
      assert.equal(new URL(page.url()).searchParams.get('age_band'),'20');
      assert.equal(await page.locator('#statistics-form [name="age_band"]').inputValue(),'20');
      assert.match(await page.locator('[data-distribution="rank"] li').filter({has:page.locator('button[data-value="normal"]')}).innerText(),/5 \(50.0%\)/);
      for (const [key,value] of [['rank','normal'],['country_code','JP'],['device_type','keyboard'],['character','0'],['match_type','ranked']]) await page.locator(`#statistics-form [name="${key}"]`).selectOption(value);
      await page.locator('#statistics-form button[type="submit"]').click(); await idle();
      for (const [key,value] of [['age_band','20'],['rank','normal'],['country_code','JP'],['device_type','keyboard'],['character','0'],['match_type','ranked']]) assert.equal(queries.at(-1).get(key),value);
      await page.goBack(); await idle();
      assert.equal(await page.locator('#statistics-form [name="age_band"]').inputValue(),'20');
      assert.equal(await page.locator('#statistics-form [name="rank"]').inputValue(),'');
      await page.locator('[data-character="20"] button').click(); await idle();
      assert.equal(queries.at(-1).get('character'),'20');
      await page.locator('#statistics-form [name="rank"]').selectOption('ph');
      await page.locator('#statistics-form button[type="submit"]').click(); await idle();
      assert.equal(await page.locator('[data-distribution], #statistics-results table, #statistics-results .number').count(),0);
      assert.match(await page.locator('#statistics-results').innerText(),lang === 'ja'?/5人未満/:/fewer than 5/);
      serverError = true;
      await page.locator('#statistics-form button[type="submit"]').click(); await idle();
      assert.match(await page.locator('#status.error').innerText(),/Temporary statistics error/);
      assert.equal(await page.locator('#statistics-form button[type="submit"]').isEnabled(),true);
      assert.equal(await page.locator('#statistics-results').innerText(),'');
      serverError = false;
      await page.locator('#statistics-form a').click(); await idle();
      assert.equal(queries.at(-1).has('age_band'),false);
      assert.equal(await page.locator('[data-distribution]').count(),4);
      await page.setViewportSize({width:390,height:844}); await noOverflow();
      if (process.argv[3] && lang==='ja') await page.screenshot({path:path.join(process.argv[3],'player-statistics-mobile.png'),fullPage:true});
      await page.setViewportSize({width:1280,height:900});
    }
    await ready('lang=ja&age_band=20'); await idle();
    if (process.argv[3]) await page.screenshot({path:path.join(process.argv[3],'player-statistics.png'),fullPage:true});
    loggedIn = false; const before = queries.length; await ready('lang=ja');
    assert.equal(await page.locator('#gate').isVisible(),true);
    assert.equal(await page.locator('#statistics-form').count(),0);
    assert.equal(queries.length,before);
    assert.deepEqual(errors,[]);
    console.log('Player statistics UI passed: JA/EN, desktop/mobile, demographic and character filters, URL/back/reset, suppression, errors and login gate.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode=1; });
