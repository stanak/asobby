/* Profile values only enter the DOM through textContent, never HTML. */
(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  const tr = (key, params) => t(`players.${key}`, params);
  const jaChars = ["霊夢","魔理沙","咲夜","アリス","パチュリー","妖夢","レミリア","幽々子","紫","萃香","鈴仙","文","小町","衣玖","天子","早苗","チルノ","美鈴","空","諏訪子","ランダム"];
  const enChars = ["Reimu","Marisa","Sakuya","Alice","Patchouli","Youmu","Remilia","Yuyuko","Yukari","Suika","Reisen","Aya","Komachi","Iku","Tenshi","Sanae","Cirno","Meiling","Utsuho","Suwako","Random"];
  const charName = id => (getLang() === "ja" ? jaChars : enChars)[id] || "?";
  const countryNames = new Intl.DisplayNames([getLang()], { type:"region" });
  const countryName = code => code ? `${[...code].map(c => String.fromCodePoint(127397 + c.charCodeAt(0))).join("")} ${countryNames.of(code)}` : tr("unset");
  let options, me;

  function link(text, href, cls) { const a = el("a", text, cls); a.href = href; return a; }
  function safeProfileUrl(value) {
    if (typeof value !== "string" || !/^https?:\/\/[^/?#]/i.test(value) || /[\s\\\p{C}]/u.test(value)) return null;
    try {
      const url = new URL(value);
      return ["https:", "http:"].includes(url.protocol) && url.hostname && !url.username && !url.password ? url : null;
    } catch { return null; }
  }
  function searchLink(text, field, value) { const q = new URLSearchParams({ [field]: value }); return link(text, `/players?${q}`, "chip"); }
  function status(text = "", error = false) { $("status").textContent = text; $("status").className = error ? "error" : ""; }
  async function api(path, init) {
    const r = await fetch(path, { credentials:"same-origin", cache:"no-store", ...init });
    if (r.status === 401) { $("gate").hidden = false; $("content").replaceChildren(); throw new Error(tr("loginHint")); }
    const data = await r.json();
    if (!r.ok) {
      const detail = data.detail;
      throw new Error(r.status === 404 ? tr("notFound") : Array.isArray(detail)
        ? detail.map(d => `${d.loc?.slice(1).join(".") || ""}: ${d.msg}`).join("\n") : String(detail || t("common.loadFailed")));
    }
    return data;
  }
  function heading(title, subtitle) {
    document.title = `${title} - asobby`;
    const wrap = el("div", null, "page-heading"), copy = el("div");
    copy.append(el("h1", title)); if (subtitle) copy.append(el("p", subtitle, "muted")); wrap.append(copy); return wrap;
  }
  function avatar(player) {
    if (player.avatar && /^https:\/\/cdn\.discordapp\.com\/avatars\//.test(player.avatar)) {
      const img = el("img", null, "avatar"); img.src = player.avatar; img.alt = ""; img.loading = "lazy"; img.referrerPolicy = "no-referrer"; return img;
    }
    return el("span", [...(player.display_name || "?")][0], "avatar placeholder");
  }
  function rankBadge(player) { return el("span", `${player.rank_symbol}${player.rating == null ? "" : ` · ${player.rating}`}`, "badge"); }
  function chips(values, make) {
    const wrap = el("div", null, "chips");
    if (!values.length) wrap.append(el("span", tr("unset"), "muted"));
    values.forEach(value => wrap.append(make(value))); return wrap;
  }
  function field(parent, label, name, type="text", value="", hint="") {
    const wrap = el("div", null, "field"), id = `field-${name}`, caption = el("label", label), input = el("input");
    caption.htmlFor = id; input.id = id; input.name = name; input.type = type; input.value = value ?? "";
    wrap.append(caption, input);
    if (hint) { const note = el("span", hint, "hint"); note.id = `${id}-hint`; input.setAttribute("aria-describedby", note.id); wrap.append(note); }
    parent.append(wrap); return input;
  }
  function select(parent, label, name, choices, value="", empty=tr("unset")) {
    const wrap = el("div", null, "field"), caption = el("label", label), input = el("select");
    input.id = `field-${name}`; input.name = name; caption.htmlFor = input.id;
    for (const [key, text] of [...(empty == null ? [] : [["", empty]]), ...choices]) { const op = el("option", text); op.value = key; input.append(op); }
    input.value = value; wrap.append(caption, input); parent.append(wrap); return input;
  }
  function check(parent, label, name, value="1", checked=false) {
    const wrap = el("label", null, "check"), input = el("input"); input.type = "checkbox"; input.name = name; input.value = value; input.checked = checked;
    wrap.append(input, el("span", label)); parent.append(wrap); return input;
  }
  function countries() { return options.countries.map(code => [code, countryName(code)]).sort((a,b) => a[1].localeCompare(b[1], getLang())); }
  function panel(parent, title) { const p = el("section", null, "panel"); p.append(el("h2", title)); parent.append(p); return p; }
  const characterChoices = count => Array.from({length:count}, (_, id) => [String(id), charName(id)]);

  function characterEditor(parent, key, name, initial) {
    const group = el("fieldset"), choices = el("div", null, "character-choices");
    group.append(el("legend", tr(key)), choices); parent.append(group);
    const inputs = characterChoices(20).map(([id, label]) => check(choices, label, name, id, initial.includes(Number(id))));
    return () => inputs.filter(input => input.checked).map(input => Number(input.value));
  }

  function biographyEditor(parent, initial) {
    const wrap = el("div", null, "field"), caption = el("label", tr("bio")), input = el("textarea");
    input.id = "field-bio"; input.name = "bio"; input.rows = 7; input.value = initial || ""; caption.htmlFor = input.id;
    const hint = el("span", tr("bioHint"), "hint"), count = el("span", null, "hint");
    hint.id = "bio-hint"; count.id = "bio-count"; input.setAttribute("aria-describedby", "bio-hint bio-count");
    const validate = () => {
      const n = [...input.value].length; count.textContent = tr("bioCount", {n}); count.className = n > 400 ? "error" : "hint";
      input.setCustomValidity(n > 400 ? tr("bioTooLong") : ""); return n <= 400;
    };
    input.oninput = validate; validate(); wrap.append(caption, input, hint, count); parent.append(wrap);
    return () => { if (!validate()) throw new Error(tr("bioTooLong")); return input.value; };
  }

  function profileLinksEditor(parent, initial) {
    parent.append(el("p", tr("linksHint"), "hint"));
    const list = el("div", null, "link-editor"), add = el("button", tr("addLink")); add.id = "add-profile-link"; add.type = "button";
    parent.append(list, add);
    let rows = [], serial = 0;
    function addRow(value = {label:"", url:""}, focus = false) {
      if (rows.length >= 10) return;
      const wrap = el("div", null, "link-editor-row"), fields = el("div", null, "two-col"), id = serial++;
      wrap.append(fields);
      const label = field(fields, tr("linkLabel"), `profile-link-${id}-label`, "text", value.label);
      const url = field(fields, "URL", `profile-link-${id}-url`, "url", value.url);
      label.dataset.linkLabel = ""; url.dataset.linkUrl = ""; url.maxLength = 2048;
      label.placeholder = tr("linkLabelExample"); url.placeholder = "https://…";
      const remove = el("button", tr("removeLink")); remove.type = "button"; remove.dataset.removeLink = ""; wrap.append(remove);
      const validate = () => {
        const name = label.value.trim(), address = url.value.trim(), empty = !name && !address;
        label.setCustomValidity(empty || (name && [...name].length <= 40 && !/\p{C}/u.test(name)) ? "" : tr("linkLabelError"));
        url.setCustomValidity(empty || safeProfileUrl(address) ? "" : tr("linkUrlError"));
        remove.setAttribute("aria-label", tr("remove", {name:name || tr("linkLabel")}));
        return label.validity.valid && url.validity.valid;
      };
      const row = {wrap, label, url, validate}; rows.push(row);
      label.oninput = url.oninput = validate;
      remove.onclick = () => { rows = rows.filter(item => item !== row); wrap.remove(); add.disabled = rows.length >= 10; add.focus(); };
      validate(); list.append(wrap); add.disabled = rows.length >= 10; if (focus) label.focus();
    }
    initial.forEach(value => addRow(value)); add.onclick = () => addRow(undefined, true);
    return () => rows.flatMap(row => {
      if (!row.validate()) throw new Error(tr("linkInvalid"));
      const label = row.label.value.trim(), url = row.url.value.trim();
      return label || url ? [{label, url}] : [];
    });
  }

  function tagEditor(parent, key, initial, max, maxLength, hint) {
    const wrap = el("div", null, "field"), list = el("div", null, "chips"), entry = el("div", null, "tag-entry"), input = el("input"), add = el("button", tr("add"));
    const label = el("label", tr(key)); label.htmlFor = `tag-${key}`; input.id = label.htmlFor; input.maxLength = maxLength;
    const note = el("span", hint, "hint"); note.id = `tag-${key}-hint`; input.setAttribute("aria-describedby", note.id);
    add.type = "button"; entry.append(input, add); wrap.append(label, list, entry, note); parent.append(wrap);
    let values = [...initial];
    function render() {
      list.replaceChildren();
      values.forEach((value, i) => {
        const chip = el("span", value, "chip"), remove = el("button", "×"); remove.type = "button"; remove.setAttribute("aria-label", tr("remove", {name:value}));
        remove.onclick = () => { values.splice(i,1); input.setCustomValidity(""); render(); }; chip.append(remove); list.append(chip);
      });
    }
    function commit() {
      const value = input.value.trim(); if (!value) return true;
      if (!values.some(v => v.normalize("NFKC").toLowerCase() === value.normalize("NFKC").toLowerCase())) {
        if (values.length >= max) { input.setCustomValidity(tr("tagLimit")); input.reportValidity(); return false; }
        values.push(value);
      }
      input.value = ""; input.setCustomValidity(""); render(); return true;
    }
    input.oninput = () => input.setCustomValidity("");
    input.onkeydown = e => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); commit(); } };
    add.onclick = commit; render();
    return () => { if (!commit()) throw new Error(tr("tagLimit")); return values; };
  }

  async function editProfile() {
    const data = await api("/user/profile"), form = el("form", null, "editor"); form.id = "profile-form";
    const head = heading(tr("edit"), tr("editHint")); head.append(link(tr("view"), `/players/${encodeURIComponent(me.id)}`, "button")); form.append(head);
    const basics = panel(form, tr("basics"));
    const name = field(basics, tr("name"), "player_name", "text", data.player_name, tr("nameHint")); name.maxLength = 24;
    const mainCharacter = select(basics, tr("mainCharacter"), "main_character", characterChoices(21), data.main_character == null ? "" : String(data.main_character));
    const useName = check(basics, tr("lobbyName"), "use_player_name", "1", data.use_player_name);
    const birthVisibility = select(basics, tr("birthVisibility"), "birth_visibility", [
      ["public", tr("birthPublic")], ["statistics", tr("birthStatistics")], ["secret", tr("secret")],
    ], data.birth_visibility || "secret", null);
    const birth = field(basics, tr("birth"), "birth_date", "date", data.birth_date, tr("birthHint")); birth.min = "1900-01-01";
    birth.max = new Intl.DateTimeFormat("en-CA", {timeZone:"Asia/Tokyo", year:"numeric", month:"2-digit", day:"2-digit"}).format(new Date());
    birth.disabled = birthVisibility.value === "secret"; birth.required = !birth.disabled;
    birthVisibility.onchange = () => { birth.disabled = birthVisibility.value === "secret"; birth.required = !birth.disabled; if (birth.disabled) birth.value = ""; else birth.focus(); };
    const country = select(basics, tr("country"), "country_code", countries(), data.country_code); basics.append(el("p", tr("countryHint"), "hint"));
    const interests = panel(form, tr("interests"));
    const device = select(interests, tr("device"), "device_type", options.devices.map(d => [d, tr(d)]), data.device_type);
    const model = field(interests, tr("deviceModel"), "device_model", "text", data.device_model); model.maxLength = 120;
    const favorites = tagEditor(interests, "favorites", data.favorite_players, 20, 100, tr("favoriteHint"));
    const games = tagEditor(interests, "games", data.other_games, 30, 120, tr("gamesHint"));
    const characters = panel(form, tr("characters")); characters.append(el("p", tr("charHint"), "hint"));
    const strongCharacters = characterEditor(characters, "strong", "strong_characters", data.strong_characters);
    const weakCharacters = characterEditor(characters, "weak", "weak_characters", data.weak_characters);
    const privacy = panel(form, tr("privacy"));
    const allow = check(privacy, tr("allowWinrates"), "character_winrates_public", "1", data.character_winrates_public);
    privacy.append(el("p", tr("privacyHint"), "hint"));
    const introduction = panel(form, tr("bio")), bio = biographyEditor(introduction, data.bio);
    const links = profileLinksEditor(panel(form, tr("links")), data.profile_links || []);
    const actions = el("div", null, "actions"), save = el("button", tr("save"), "primary"), message = el("span");
    save.type = "submit"; message.id = "save-status"; message.setAttribute("role", "status"); actions.append(save, message); form.append(actions);
    form.onsubmit = async event => {
      event.preventDefault(); if (save.disabled) return;
      message.textContent = ""; message.className = "";
      try {
        const payload = {
          player_name:name.value, use_player_name:useName.checked, birth_date:birthVisibility.value === "secret" ? null : birth.value || null,
          birth_visibility:birthVisibility.value,
          main_character:mainCharacter.value === "" ? null : Number(mainCharacter.value),
          country_code:country.value, device_type:device.value, device_model:model.value,
          favorite_players:favorites(), other_games:games(), character_winrates_public:allow.checked,
          strong_characters:strongCharacters(), weak_characters:weakCharacters(),
          bio:bio(), profile_links:links(),
        };
        save.disabled = true; save.textContent = tr("saving");
        await api("/user/profile", {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
        message.textContent = tr("saved"); message.className = "success";
      } catch (e) { message.textContent = e.message; message.className = "error"; }
      finally { save.disabled = false; save.textContent = tr("save"); }
    };
    $("content").replaceChildren(form);
  }

  function playerCard(player) {
    const card = el("article", null, "player-card"), identity = el("div", null, "identity"), copy = el("div"), title = el("h2");
    title.append(link(player.display_name, `/players/${encodeURIComponent(player.id)}`)); copy.append(title, el("div", player.discord_name, "muted")); identity.append(avatar(player), copy);
    card.append(identity, chips([player], rankBadge));
    if (player.main_character != null) card.append(searchLink(`${tr("mainCharacter")}: ${charName(player.main_character)}`, "main_character", player.main_character));
    card.append(el("div", [player.age == null ? null : tr("ageValue", {n:player.age}), player.country_code ? countryName(player.country_code) : null].filter(Boolean).join(" · "), "muted"));
    if (player.device_type) card.append(el("div", `${tr(player.device_type)}${player.device_model ? ` · ${player.device_model}` : ""}`));
    for (const [key, field] of [["strong", "strong_char"], ["weak", "weak_char"]]) {
      const values = player[`${key}_characters`];
      if (values.length) {
        const group = el("div"); group.append(el("div", tr(key), "muted"), chips(values, id => searchLink(charName(id), field, id))); card.append(group);
      }
    }
    if (player.other_games.length) card.append(chips(player.other_games.slice(0,4), game => searchLink(game, "game", game)));
    return card;
  }

  async function searchPage() {
    const head = heading(tr("title"), tr("intro")); head.append(link(tr("edit"), "/profile", "button"));
    const layout = el("div", null, "search-layout"), details = el("details", null, "panel"), form = el("form"), result = el("section");
    details.open = !window.matchMedia("(max-width:620px)").matches;
    details.append(el("summary", tr("filters")), form); form.id = "search-form"; result.id = "search-results";
    const side = el("aside"); side.append(details); layout.append(side, result); $("content").replaceChildren(head, layout);
    const stats = el("details", null, "panel"); stats.append(el("summary", tr("stats"))); side.append(stats);
    let statsLoaded = false;
    stats.ontoggle = async () => {
      if (!stats.open || statsLoaded) return; statsLoaded = true;
      try {
        const population = await api("/api/players/statistics"); stats.append(el("p", tr("statsHint"), "hint"));
        const ages = el("dl"), countries = el("dl");
        population.age_bands.forEach(band => { ages.append(el("dt", `${band.min}${band.max == null ? "+" : `–${band.max}`}`), el("dd", band.count)); });
        population.countries.forEach(c => { countries.append(el("dt", countryName(c.country_code)), el("dd", c.count)); });
        stats.append(el("h2", tr("age")), ages, el("h2", tr("country")), countries);
      } catch (e) { statsLoaded = false; status(e.message, true); }
    };
    const values = new URLSearchParams(location.search);
    field(form, tr("searchName"), "name", "text", values.get("name")).maxLength = 100;
    select(form, tr("mainCharacter"), "main_character", characterChoices(21), values.get("main_character") || "", t("common.unspecified"));
    const ages = el("div", null, "two-col"); form.append(ages);
    for (const key of ["age_min", "age_max"]) { const input = field(ages, tr(key === "age_min" ? "ageMin" : "ageMax"), key, "number", values.get(key)); input.min = "0"; input.max = "150"; input.step = "1"; }
    select(form, tr("country"), "country_code", countries(), values.get("country_code") || "", t("common.unspecified"));
    select(form, tr("device"), "device_type", options.devices.map(d => [d, tr(d)]), values.get("device_type") || "", t("common.unspecified"));
    field(form, tr("deviceSearch"), "device_model", "text", values.get("device_model")).maxLength = 120;
    field(form, tr("favoriteSearch"), "favorite_player", "text", values.get("favorite_player")).maxLength = 100;
    field(form, tr("games"), "game", "text", values.get("game")).maxLength = 120;
    const ranks = el("fieldset"), rankChoices = el("div", null, "chips"); ranks.append(el("legend", tr("rank")), rankChoices);
    Object.entries(options.ranks).forEach(([rank, symbol]) => check(rankChoices, symbol, "rank", rank, values.getAll("rank").includes(rank))); form.append(ranks);
    select(form, tr("strong"), "strong_char", characterChoices(20), values.get("strong_char") || "", t("common.unspecified"));
    select(form, tr("weak"), "weak_char", characterChoices(20), values.get("weak_char") || "", t("common.unspecified"));
    form.append(el("p", tr("searchHint"), "hint"));
    const submit = el("button", tr("search"), "primary"), actions = el("div", null, "actions"); submit.type = "submit"; actions.append(submit, link(tr("reset"), "/players")); form.append(actions);
    let requestNumber = 0;
    async function run(page, replace=false) {
      const seq = ++requestNumber;
      const query = new URLSearchParams(); for (const [key, value] of new FormData(form)) if (value.trim()) query.append(key, value.trim());
      query.set("page", String(page)); if (values.get("lang")) query.set("lang", values.get("lang"));
      history[replace ? "replaceState" : "pushState"](null, "", `/players?${query}`); query.delete("lang");
      result.setAttribute("aria-busy", "true"); submit.disabled = true; status(t("common.loading"));
      try {
        if (query.has("age_min") && query.has("age_max") && Number(query.get("age_min")) > Number(query.get("age_max"))) throw new Error(tr("rangeError"));
        const data = await api(`/api/players?${query}`); if (seq !== requestNumber) return;
        result.replaceChildren(el("h2", tr("results", {n:data.total})));
        const cards = el("div", null, "cards"); data.players.forEach(p => cards.append(playerCard(p))); result.append(cards);
        if (!data.players.length) result.append(el("p", tr("noResults"), "muted"));
        const pagination = el("nav", null, "pagination"), prev = el("button", tr("previous")), next = el("button", tr("next"));
        pagination.setAttribute("aria-label", tr("page", {n:page})); prev.disabled = page <= 1; next.disabled = page * data.limit >= data.total;
        prev.onclick = () => run(page-1); next.onclick = () => run(page+1); pagination.append(prev, el("span", tr("page", {n:page})), next); result.append(pagination); status();
      } catch (e) { if (seq === requestNumber) { result.replaceChildren(); status(e.message, true); } }
      finally { if (seq === requestNumber) { submit.disabled = false; result.setAttribute("aria-busy", "false"); } }
    }
    form.onsubmit = e => { e.preventDefault(); run(1); };
    window.onpopstate = () => searchPage().catch(e => status(e.message, true));
    const page = Number(values.get("page") || 1); await run(Number.isInteger(page) && page > 0 ? page : 1, true);
  }

  async function profilePage(id) {
    const player = await api(`/api/players/${encodeURIComponent(id)}`), top = el("section", null, "panel profile-top"), title = el("div");
    title.append(el("h1", player.display_name), el("p", [player.discord_name, player.discord_username ? `@${player.discord_username}` : ""].filter(Boolean).join(" / "), "muted"), rankBadge(player));
    top.append(avatar(player), title); if (player.is_owner) top.append(link(tr("edit"), "/profile", "button"));
    const columns = el("div", null, "profile-columns"), left = el("div"), right = el("div"); columns.append(left,right);
    const about = panel(left, tr("basics")), facts = el("dl"); about.append(facts);
    const fact = (name, value) => { facts.append(el("dt", tr(name))); const dd = el("dd"); dd.append(typeof value === "string" ? document.createTextNode(value) : value); facts.append(dd); };
    fact("name", player.player_name || tr("unset"));
    fact("mainCharacter", player.main_character == null ? tr("unset") : searchLink(charName(player.main_character), "main_character", player.main_character));
    fact("age", player.age == null ? tr("privateAge") : tr("ageValue", {n:player.age}));
    fact("country", player.country_code ? searchLink(countryName(player.country_code), "country_code", player.country_code) : tr("unset"));
    fact("device", player.device_type ? `${tr(player.device_type)}${player.device_model ? ` · ${player.device_model}` : ""}` : tr("unset"));
    fact("favorites", chips(player.favorite_players, name => searchLink(name, "name", name)));
    fact("games", chips(player.other_games, game => searchLink(game, "game", game)));
    fact("strong", chips(player.strong_characters, id => searchLink(charName(id), "strong_char", id)));
    fact("weak", chips(player.weak_characters, id => searchLink(charName(id), "weak_char", id)));
    const summary = panel(right, t("nav.stats")), numbers = el("div", null, "numbers");
    for (const [key,label] of [["total_matches","total"],["unique_opponents","opponents"]]) { const item = el("div"); item.append(el("div", player[key].toLocaleString(), "number"), el("div", tr(label), "muted")); numbers.append(item); }
    summary.append(numbers, el("p", tr("countHint", {n:player.unidentified_opponent_matches}), "hint"));
    const rates = panel(right, tr("winrates"));
    if (player.character_winrates === null) rates.append(el("p", tr("privateRates"), "muted"));
    else {
      if (!player.character_winrates_public) rates.append(el("p", tr("ownRates"), "hint"));
      if (!player.character_winrates.length) rates.append(el("p", t("common.noData"), "muted"));
      else {
        const table = el("table"), thead = el("thead"), row = el("tr"), tbody = el("tbody"), wrap = el("div", null, "table-wrap");
        ["char","matches","wins","losses","draws","rate"].forEach(key => { const th = el("th", tr(key)); th.scope = "col"; row.append(th); }); thead.append(row);
        player.character_winrates.forEach(stat => { const row = el("tr"); [charName(stat.char),stat.games,stat.wins,stat.losses,stat.draws,`${(stat.win_rate*100).toFixed(1)}%`].forEach(value => row.append(el("td", value))); tbody.append(row); });
        table.append(thead,tbody); wrap.append(table); rates.append(wrap);
      }
      rates.append(el("p", tr("rateHint"), "hint"));
    }
    const footer = el("div", null, "profile-footer");
    if (player.bio?.trim()) {
      const introduction = panel(footer, tr("bio")); introduction.id = "profile-bio";
      introduction.append(el("p", player.bio, "biography"));
    }
    const externalLinks = (player.profile_links || []).map(item => ({...item, parsed:safeProfileUrl(item.url)})).filter(item => item.parsed);
    if (externalLinks.length) {
      const links = panel(footer, tr("links")); links.id = "profile-links";
      links.append(el("p", tr("externalLinksHint"), "hint"));
      const list = el("ul", null, "profile-links");
      externalLinks.forEach(item => {
        const entry = el("li"), anchor = link("", item.parsed.href, "profile-external-link");
        anchor.target = "_blank"; anchor.rel = "noopener noreferrer nofollow ugc"; anchor.referrerPolicy = "no-referrer";
        anchor.append(el("span", item.label, "external-label"), el("span", item.parsed.href, "muted")); entry.append(anchor); list.append(entry);
      });
      links.append(list);
    }
    $("content").replaceChildren(top, columns, footer); document.title = `${player.display_name} - asobby`;
  }

  async function init() {
    applyDocumentI18n(); initLangToggle(); document.title = `${tr("profile")} - asobby`;
    $("login").href = `/auth/discord/web?next=${encodeURIComponent(location.pathname + location.search)}`;
    try {
      me = await api("/auth/me"); options = await api("/api/players/options");
      if (location.pathname === "/profile") await editProfile();
      else if (location.pathname.startsWith("/players/")) await profilePage(decodeURIComponent(location.pathname.slice(9)));
      else await searchPage();
      if (!$("status").classList.contains("error")) status();
    } catch (e) { status(e.message || t("common.networkError"), true); }
    finally { $("app").setAttribute("aria-busy", "false"); }
  }
  init();
})();
