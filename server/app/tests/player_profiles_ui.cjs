// Fully mocked requests: no real accounts, services, statistics, or profile writes.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.argv[2] || "playwright");

(async () => {
  const browser = await chromium.launch({ headless:true, ...(process.platform === "win32" ? {channel:"msedge"} : {}) });
  try {
    const page = await browser.newPage({ viewport:{width:390,height:844} });
    const errors = [], searches = [], writes = [];
    page.on("pageerror", e => errors.push(e.message));
    const staticDir = path.join(__dirname, "../static");
    let loggedIn = true, saveError = false, allowRates = false;
    const player = {
      id:"alice", display_name:"あそび人", player_name:"あそび人", discord_name:"Alice", discord_username:"alice123",
      main_character:20,
      bio:'対戦よろしくお願いします！\n<img src=x onerror="window.injected=1">',
      profile_links:[{label:"YouTube",url:"https://www.youtube.com/@example"},{label:"<b>My site</b>",url:"https://example.com/"}],
      lobby_name:"あそび人", use_player_name:true, age:25, country_code:"JP", device_type:"gamepad", device_model:"RAP-4",
      rank:"ph", rank_symbol:"Ph", rating:39.3, avatar:"", favorite_players:["Top Player"],
      other_games:["STREET FIGHTER 6", '<img src=x onerror="window.injected=1">'], strong_characters:[0,5], weak_characters:[1,19],
      character_winrates_public:false, total_matches:5, unique_opponents:2, unidentified_opponent_matches:1,
      character_winrates:null, is_owner:false,
    };
    let savedProfile = null;
    await page.route("**/*", async route => {
      const url = new URL(route.request().url()), req = route.request();
      if (url.hostname !== "asobby.test") return route.fulfill({status:503,body:""});
      const json = (data, status=200) => route.fulfill({status,contentType:"application/json",body:JSON.stringify(data)});
      if (["/players", "/profile", "/players/alice", "/players/missing"].includes(url.pathname)) return route.fulfill({contentType:"text/html",body:fs.readFileSync(path.join(staticDir,"players.html"))});
      if (url.pathname.startsWith("/static/")) {
        const file = path.join(staticDir,path.basename(url.pathname));
        if (!fs.existsSync(file)) return route.fulfill({status:404,body:""});
        return route.fulfill({contentType:file.endsWith(".js") ? "application/javascript" : file.endsWith(".css") ? "text/css" : "image/png", body:fs.readFileSync(file)});
      }
      if (url.pathname === "/auth/me") return json(loggedIn ? {id:"alice",name:"Alice"} : {detail:"login required"}, loggedIn ? 200 : 401);
      if (url.pathname === "/api/players/options") return json({countries:["JP","US"],ranks:{easy:"E",normal:"N",ex:"Ex",hard:"H",luna:"L",ph:"Ph"},devices:["keyboard","gamepad","arcade","other"]});
      if (url.pathname === "/user/profile") {
        if (req.method() === "GET") return json({...player,birth_date:"2000-09-20",birth_visibility:"public",...savedProfile});
        assert.equal(req.headers()["content-type"], "application/json"); writes.push(req.postDataJSON());
        if (!saveError) savedProfile = req.postDataJSON();
        return json(saveError ? {detail:[{loc:["body","player_name"],msg:"24 CP932 bytes"}]} : {ok:true,id:"alice"}, saveError ? 422 : 200);
      }
      if (url.pathname === "/api/players") { searches.push(url.searchParams); return json({players:url.searchParams.get("name") === "nobody" ? [] : [player],total:url.searchParams.get("name") === "nobody" ? 0 : 25,page:Number(url.searchParams.get("page")||1),limit:24}); }
      if (url.pathname === "/api/players/statistics") return json({countries:[{country_code:"JP",count:1}],age_bands:[{min:20,max:29,count:1}]});
      if (url.pathname === "/api/players/alice") return json({...player, character_winrates_public:allowRates, character_winrates:allowRates ? [{char:0,games:5,wins:2,losses:2,draws:1,win_rate:.4}] : null});
      return json({detail:"player not found"},404);
    });
    async function ready(url) { await page.goto(`https://asobby.test${url}`); await page.waitForFunction(() => document.getElementById("app").getAttribute("aria-busy") === "false"); }
    async function noOverflow() { assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),true); }
    for (const lang of ["ja","en"]) {
      savedProfile = null;
      await ready(`/profile?lang=${lang}`);
      assert.equal(await page.locator('select[name="birth_visibility"]').inputValue(),"public");
      assert.deepEqual(await page.locator('select[name="birth_visibility"] option').evaluateAll(options => options.map(o => o.value)),["public","statistics","secret"]);
      assert.equal(await page.locator('#profile-form input[name="character_winrates_public"]').isChecked(),false);
      for (const [kind, expected] of [["strong",["0","5"]],["weak",["1","19"]]]) {
        assert.equal(await page.locator(`input[name="${kind}_characters"]`).count(),20);
        assert.deepEqual(await page.locator(`input[name="${kind}_characters"]:checked`).evaluateAll(inputs => inputs.map(i => i.value)), expected);
        assert.equal(await page.locator(`input[name="${kind}_characters"][value="20"]`).count(),0);
      }
      assert.equal(await page.locator('select[name="main_character"] option').count(),22); // 21 + unset
      assert.equal(await page.locator('select[name="main_character"]').inputValue(),"20");
      assert.equal(await page.locator('[name="bio"]').inputValue(),player.bio);
      assert.equal(await page.locator('[data-link-label]').count(),2);
      assert.equal(await page.locator('[data-link-label]').first().inputValue(),"YouTube");
      await page.locator('[name="bio"]').fill("😀".repeat(401));
      assert.equal(await page.locator('[name="bio"]').evaluate(e => e.validity.valid),false);
      assert.match(await page.locator('#bio-count').innerText(),/401 \/ 400/);
      await page.locator('[name="bio"]').fill("😀".repeat(400));
      assert.equal(await page.locator('[name="bio"]').evaluate(e => e.validity.valid),true);
      assert.match(await page.locator('#bio-count').innerText(),/400 \/ 400/);
      const newBio = "こんにちは😀\n対戦歓迎です！";
      await page.locator('[name="bio"]').fill(newBio);
      await page.locator('#add-profile-link').click();
      await page.locator('[data-link-label]').last().fill("Twitch");
      await page.locator('[data-link-url]').last().fill("javascript:alert(1)");
      assert.equal(await page.locator('[data-link-url]').last().evaluate(e => e.validity.valid),false);
      await page.locator('[data-link-url]').last().fill("https://user:pass@example.com/");
      assert.equal(await page.locator('[data-link-url]').last().evaluate(e => e.validity.valid),false);
      await page.locator('[data-link-url]').last().fill("https://www.twitch.tv/example");
      assert.equal(await page.locator('[data-link-url]').last().evaluate(e => e.validity.valid),true);
      await page.locator('select[name="birth_visibility"]').selectOption("statistics");
      assert.equal(await page.locator('[name="birth_date"]').isEnabled(),true);
      await page.locator('#profile-form button[type="submit"]').click();
      await page.locator('#save-status.success').waitFor();
      assert.equal(writes.at(-1).birth_visibility,"statistics");
      assert.equal(writes.at(-1).birth_date,"2000-09-20");
      assert.equal(writes.at(-1).bio,newBio);
      assert.deepEqual(writes.at(-1).profile_links,[...player.profile_links,{label:"Twitch",url:"https://www.twitch.tv/example"}]);
      await page.locator('select[name="birth_visibility"]').selectOption("secret");
      assert.equal(await page.locator('[name="birth_date"]').isDisabled(),true);
      assert.equal(await page.locator('[name="birth_date"]').inputValue(),"");
      await page.locator('#tag-favorites').fill("New Hero"); await page.locator('#tag-favorites').press("Enter");
      await page.locator('[name="character_winrates_public"]').check();
      await page.locator('input[name="strong_characters"][value="6"]').check();
      await page.locator('input[name="weak_characters"][value="1"]').uncheck();
      await page.locator('input[name="weak_characters"][value="5"]').check();
      await page.locator('#profile-form button[type="submit"]').click();
      await page.locator('#save-status.success').waitFor();
      assert.equal(writes.at(-1).birth_date,null);
      assert.equal(writes.at(-1).birth_visibility,"secret");
      assert.deepEqual(writes.at(-1).favorite_players,["Top Player","New Hero"]);
      assert.equal(writes.at(-1).character_winrates_public,true);
      assert.equal(writes.at(-1).main_character,20);
      assert.deepEqual(writes.at(-1).strong_characters,[0,5,6]);
      assert.deepEqual(writes.at(-1).weak_characters,[5,19]);
      assert.equal("strong_character" in writes.at(-1),false);
      assert.equal("weak_character" in writes.at(-1),false);
      assert.equal("rank" in writes.at(-1),false);
      await ready(`/profile?lang=${lang}`);
      assert.equal(await page.locator('[name="bio"]').inputValue(),newBio);
      assert.equal(await page.locator('[data-link-label]').count(),3);
      assert.equal(await page.locator('[data-link-url]').last().inputValue(),"https://www.twitch.tv/example");
      assert.deepEqual(await page.locator('input[name="strong_characters"]:checked').evaluateAll(inputs => inputs.map(i => Number(i.value))),[0,5,6]);
      assert.deepEqual(await page.locator('input[name="weak_characters"]:checked').evaluateAll(inputs => inputs.map(i => Number(i.value))),[5,19]);
      for (const kind of ["strong","weak"]) {
        for (const checkbox of await page.locator(`input[name="${kind}_characters"]`).all()) await checkbox.uncheck();
      }
      await page.locator('#profile-form button[type="submit"]').click();
      await page.locator('#save-status.success').waitFor();
      assert.deepEqual(writes.at(-1).strong_characters,[]);
      assert.deepEqual(writes.at(-1).weak_characters,[]);
      await ready(`/profile?lang=${lang}`);
      assert.equal(await page.locator('.character-choices input:checked').count(),0);
      for (let n = 3; n < 10; n++) await page.locator('#add-profile-link').click();
      assert.equal(await page.locator('[data-link-label]').count(),10);
      assert.equal(await page.locator('#add-profile-link').isDisabled(),true);
      await page.locator('[data-remove-link]').first().click();
      assert.equal(await page.locator('#add-profile-link').isEnabled(),true);
      while (await page.locator('[data-remove-link]').count()) await page.locator('[data-remove-link]').first().click();
      await page.locator('[name="bio"]').fill("");
      await page.locator('#profile-form button[type="submit"]').click();
      await page.locator('#save-status.success').waitFor();
      assert.equal(writes.at(-1).bio,""); assert.deepEqual(writes.at(-1).profile_links,[]);
      await ready(`/profile?lang=${lang}`);
      assert.equal(await page.locator('[data-link-label]').count(),0);
      assert.equal(await page.locator('[name="bio"]').inputValue(),"");
      saveError = true;
      await page.locator('[name="player_name"]').fill("あ".repeat(13));
      await page.locator('#profile-form button[type="submit"]').click();
      await page.locator('#save-status.error').waitFor();
      assert.match(await page.locator('#save-status').innerText(),/CP932/);
      assert.equal(await page.locator('#profile-form button[type="submit"]').isEnabled(),true);
      saveError = false; await noOverflow();

      await ready(`/players?lang=${lang}&name=Alice&age_min=20&age_max=30&strong_char=0&rank=ph`);
      await page.locator('.player-card').waitFor();
      assert.equal(await page.locator('aside>details').first().getAttribute('open'),null);
      await page.locator('aside>details>summary').first().click();
      assert.equal(searches.at(-1).get("age_min"),"20");
      assert.deepEqual(searches.at(-1).getAll("strong_char"),["0"]);
      for (const kind of ["strong","weak"]) {
        assert.equal(await page.locator(`select[name="${kind}_char"] option`).count(),21);
        assert.equal(await page.locator(`select[name="${kind}_char"]`).getAttribute("multiple"),null);
      }
      await page.locator('select[name="strong_char"]').selectOption("5");
      await page.locator('select[name="weak_char"]').selectOption("19");
      assert.equal(await page.locator('#search-form [name="total_matches"]').count(),0);
      assert.equal(await page.locator('#search-form [name="rating_min"], #search-form [name="rating_max"]').count(),0);
      await page.locator('[name="device_model"]').fill("RAP-4");
      await page.locator('#search-form button[type="submit"]').click();
      await page.waitForFunction(() => new URLSearchParams(location.search).get("device_model") === "RAP-4");
      await page.locator('.player-card').waitFor();
      assert.equal(searches.at(-1).get("device_model"),"RAP-4");
      assert.deepEqual(searches.at(-1).getAll("strong_char"),["5"]);
      assert.deepEqual(searches.at(-1).getAll("weak_char"),["19"]);
      assert.equal(await page.locator('.player-card a[href="/players?strong_char=5"]').count(),1);
      assert.equal(await page.locator('.player-card a[href="/players?weak_char=19"]').count(),1);
      await page.locator('.pagination button').last().click();
      await page.waitForFunction(() => new URLSearchParams(location.search).get("page") === "2");
      await page.goBack(); await page.locator('.player-card').waitFor();
      assert.equal(new URL(page.url()).searchParams.get("page"),"1");
      assert.equal(await page.locator('#content img:not(.avatar)').count(),0);
      assert.equal(await page.evaluate(() => window.injected),undefined); await noOverflow();

      await ready(`/players/alice?lang=${lang}`);
      assert.match(await page.locator('#content').innerText(),/Ph · 39.3/);
      assert.equal(await page.locator('#content table').count(),0);
      assert.doesNotMatch(await page.locator('#content').innerText(),/2000-09-20/);
      assert.equal(await page.locator('#profile-bio .biography').textContent(),player.bio);
      assert.equal(await page.locator('#profile-bio .biography').evaluate(e => getComputedStyle(e).whiteSpace),"pre-wrap");
      assert.equal(await page.locator('#profile-bio img, #profile-links b').count(),0);
      assert.equal(await page.locator('#content > :last-child').getAttribute("class"),"profile-footer");
      assert.equal(await page.locator('#profile-links a').count(),2);
      const external = page.locator('#profile-links a').first();
      assert.equal(await external.getAttribute("href"),"https://www.youtube.com/@example");
      assert.equal(await external.getAttribute("target"),"_blank");
      assert.equal(await external.getAttribute("rel"),"noopener noreferrer nofollow ugc");
      assert.equal(await external.getAttribute("referrerpolicy"),"no-referrer");
      assert.match(await external.innerText(),/https:\/\/www.youtube.com\/@example/);
      assert.equal(await page.evaluate(() => window.injected),undefined);
      for (const [kind, ids] of [["strong",[0,5]],["weak",[1,19]]]) {
        for (const id of ids) assert.equal(await page.locator(`#content a[href="/players?${kind}_char=${id}"]`).count(),1);
      }
      const favorite = page.locator('#content a').filter({hasText:"Top Player"});
      assert.equal(await favorite.getAttribute("href"),"/players?name=Top+Player");
      await favorite.click(); await page.locator('.player-card').waitFor();
      assert.equal(searches.at(-1).get("name"),"Top Player");
      allowRates = true; await ready(`/players/alice?lang=${lang}`);
      assert.match(await page.locator('table').innerText(),/40.0%/); await noOverflow(); allowRates = false;
    }
    const originalBio = player.bio, originalLinks = player.profile_links;
    player.profile_links = [...originalLinks,{label:"Unsafe",url:"javascript:alert(1)"},{label:"Credentials",url:"https://user:pass@example.com/"}];
    await ready('/players/alice?lang=ja');
    assert.equal(await page.locator('#profile-links a').count(),2);
    assert.equal(await page.locator('a[href^="javascript:"]').count(),0);
    player.bio = ""; player.profile_links = [];
    await ready('/players/alice?lang=ja');
    assert.equal(await page.locator('#profile-bio, #profile-links').count(),0);
    player.bio = originalBio; player.profile_links = originalLinks;
    await ready('/players?lang=ja&name=nobody');
    assert.equal(await page.locator('.player-card').count(),0);
    await ready('/players/missing?lang=ja'); assert.match(await page.locator('#status').innerText(),/見つかりません/);
    loggedIn = false; await ready('/profile?lang=ja'); assert.equal(await page.locator('#gate').isVisible(),true);
    assert.equal(await page.locator('#profile-form').count(),0);
    loggedIn = true; await ready('/players/alice?lang=ja');
    if (process.argv[3]) await page.screenshot({path:path.join(process.argv[3],"asobby-profile-mobile.png"),fullPage:true});
    await page.setViewportSize({width:1280,height:900}); await ready('/players?lang=ja'); await page.locator('.player-card').waitFor(); await noOverflow();
    if (process.argv[3]) await page.screenshot({path:path.join(process.argv[3],"asobby-player-search.png"),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log("Player profile UI passed: JA/EN, mobile/desktop, bio Unicode limits, labelled link editing/reload/clear/limits, safe external links, multiple characters, search, privacy, XSS and login gate.");
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode=1; });
