// asobby web i18n (ja / en). 文言はすべてここに集約する。
// 言語は localStorage に永続化し、切替時はリロードで反映する。

(function () {
  "use strict";

  const STORAGE_KEY = "asobby-lang";
  const LANGS = ["ja", "en"];

  const LANG_SWITCH_LABEL = {
    ja: "English",
    en: "Japanese",
  };

  function isLang(v) {
    return v === "ja" || v === "en";
  }

  function detectLang() {
    const q = new URLSearchParams(location.search).get("lang");
    if (isLang(q)) return q;
    const saved = localStorage.getItem(STORAGE_KEY);
    if (isLang(saved)) return saved;
    const nav = navigator.language.toLowerCase();
    if (nav.startsWith("ja")) return "ja";
    return "en";
  }

  let lang = detectLang();

  const ja = {
    // nav
    "nav.lobby": "ロビー",
    "nav.stats": "戦績",
    "nav.replays": "リプレイ検索",
    "nav.clientDownload": "クライアント DL",
    "nav.support": "ご支援",

    "clientUpdate.banner": "Windows クライアント v{version} が公開されています",
    "clientUpdate.download": "GitHub で見る",
    "clientUpdate.dismiss": "閉じる",

    // common
    "common.appName": "asobby",
    "common.loading": "読み込み中...",
    "common.loadFailed": "読み込みに失敗しました",
    "common.networkError": "通信に失敗しました",
    "common.errorWithStatus": "エラー ({status})",
    "common.errorWithStatusDetail": "エラー ({status}): {detail}",
    "common.copied": "コピーしました",
    "common.clickToCopy": "クリックでコピー",
    "common.noData": "データなし",
    "common.more": "もっと見る",
    "common.upload": "アップロード",
    "common.clearAll": "すべてクリア",
    "common.removeFilter": "解除",
    "common.vs": " vs ",
    "common.vsLower": "vs ",
    "common.unknown": "(不明)",
    "common.none": "(なし)",
    "common.unspecified": "(指定なし)",
    "common.countItems": "{n} 件",
    "common.totalGames": "全 {n} 戦",
    "common.failed": "失敗",
    "common.dl": "DL",
    "common.stream": "▶ 配信",
    "common.connecting": "接続中...",
    "common.reconnecting": "再接続中...",
    "common.realtimeUpdating": "リアルタイム更新中",
    "common.discordLoginRequired": "Discord ログインが必要です",
    "common.loginGateClientHint":
      "asobby クライアント（トレイメニュー「Discord でログイン」）からログインしてください。",
    "common.resultWin": "○",
    "common.resultLoss": "×",
    "common.resultDraw": "△",
    "common.badgeGiu": "Giu",
    "common.badgeAp": "AP",
    "common.sortAsc": " ▲",
    "common.sortDesc": " ▼",

    "auth.loginOk": "ログイン完了",
    "auth.loginFail": "ログイン失敗",
    "auth.closeTab": "このタブは閉じて構いません。",
    "auth.cancelled": "ログインがキャンセルされました。",
    "auth.discordFailed": "Discord との連携に失敗しました。",
    "auth.discordUserFailed": "Discord ユーザー情報の取得に失敗しました。",
    "auth.discordUserInvalid": "Discord ユーザー情報が不正です。",
    "auth.loginSuccess": "{name} としてログインしました。アプリに戻ってください。",

    // lobby
    "lobby.pageTitle": "asobby - 非想天則ロビー",
    "lobby.subtitle": "非想天則 対戦募集ロビー",
    "lobby.rankChoiceTitle":
      "ランクマの開始ランクを選択できます（初回のみ・未選択なら N）",
    "lobby.loginGateLine1": "募集一覧を見るには Discord ログインが必要です。",
    "lobby.sectionRanked": "ランクマ募集",
    "lobby.sectionCasual": "カジュアル募集",
    "lobby.colMatch": "Match",
    "lobby.colUser": "User",
    "lobby.colRank": "Rank",
    "lobby.colCap": "Cap",
    "lobby.colStream": "Stream",
    "lobby.colComment": "Comment",
    "lobby.colAddr": "Addr",
    "lobby.emptyRanked": "現在ランクマ募集はありません",
    "lobby.emptyCasual": "現在カジュアル募集はありません",
    "lobby.loggedInAs": "{name} でログイン中 ({rank})",
    "lobby.rankConfirm":
      "開始ランクを {label} にします。この選択は一度だけです。よろしいですか？",
    "lobby.rankDescEasy": "初心者にお勧め",
    "lobby.rankDescNormal": "通常のスタート地点・初級者以上",
    "lobby.rankDescEx": "中級者層",
    "lobby.rankDescHard": "中級者以上",
    "lobby.rankDescLuna": "上級者以上",

    "lobby.chatTitle": "ロビーチャット(感想戦などにもどうぞ)",
    "lobby.chatPlaceholder": "メッセージを入力 (@でメンション)",
    "lobby.chatSend": "送信",
    "lobby.chatHide": "ロビーチャットを隠す",
    "lobby.chatShow": "ロビーチャットを表示",
    "lobby.chatEmpty": "まだメッセージはありません",
    "lobby.chatCooldown": "しばらく待ってから送ってください",

    // stats
    "stats.pageTitle": "asobby - 戦績",
    "stats.title": "asobby 戦績",
    "stats.subtitle": "非想天則 対戦記録",
    "stats.loadingMatches": "対戦データを読み込み中...",
    "stats.gateLine1": "戦績を見るには Discord ログインが必要です",
    "stats.resync": "サーバーと再同期",
    "stats.resyncing": "再同期中...",
    "stats.resyncDone": "再同期しました（全 {n} 件, {time}）",
    "stats.resyncFailed": "再同期に失敗しました",
    "stats.ranked": "ランクマ",
    "stats.filter": "絞り込み",
    "stats.facetMyChar": "自キャラ別",
    "stats.facetOppChar": "相手キャラ別",
    "stats.facetOppProfile": "相手プロファイル別",
    "stats.colChar": "キャラ",
    "stats.colProfile": "プロファイル",
    "stats.colGames": "対戦",
    "stats.colWins": "勝",
    "stats.colLosses": "負",
    "stats.colWinRate": "勝率",
    "stats.history": "対戦履歴",
    "stats.colDateTime": "日時",
    "stats.colMyChar": "自キャラ",
    "stats.colOppChar": "相手キャラ",
    "stats.colOppProfile": "相手プロファイル",
    "stats.colResult": "勝敗",
    "stats.colRanked": "ランクマ",
    "stats.colReplay": "リプレイ",
    "stats.importTitle": "天則観インポート",
    "stats.importDesc":
      "天則観 (tsk) の戦績DB (.db) をアップロードすると過去の対戦が取り込まれます。同じファイルを再アップロードしても重複しません。",
    "stats.selectFile": "ファイルを選択してください",
    "stats.importResult":
      "取り込み: {imported} 件 / 重複スキップ: {skippedDup} 件 / 不正スキップ: {skippedInvalid} 件（全 {total} 行）",
    "stats.uploadFailed": "アップロードに失敗しました",
    "stats.filterMyChar": "自キャラ: {name}",
    "stats.filterOppChar": "相手: {name}",
    "stats.filterOppProfile": "相手プロファイル: {profile}",
    "stats.filterHint": "ファセットをクリックして絞り込み",
    "stats.rankedOnly": "ランクマのみ",
    "stats.summaryMain":
      "{games} 戦 {wins} 勝 {losses} 敗 勝率 {rate} (引分 {draws})",
    "stats.summaryRecent": "直近{n}: ",
    "stats.summaryRecentPartial": " ({games}戦)",
    "stats.currentRank": "現在ランク: {rank}",
    "stats.rankedTotalLabel": "ランクマ総合成績:",
    "stats.rankedTotalRecord": "{wins}-{losses}-{draws}",
    "stats.winRateLabel": "勝率:",
    "stats.rankedTotalGames": "（{games} 戦）",
    "stats.rankedRecent30": "直近 30 戦: {rate}（{wins}勝 / {games}戦）",

    // replays
    "replays.pageTitle": "asobby - リプレイ検索",
    "replays.title": "asobby リプレイ検索",
    "replays.subtitle": "非想天則 リプレイ一覧",
    "replays.searchCriteria": "検索条件",
    "replays.playerName": "プレイヤー名",
    "replays.playerPlaceholder": "プロファイル名 / Discord 名",
    "replays.char1": "キャラ 1",
    "replays.char2": "キャラ 2 (対面)",
    "replays.dateFrom": "日付 (from)",
    "replays.dateTo": "日付 (to)",
    "replays.sortOrder": "並び順",
    "replays.sortDateDesc": "新しい順",
    "replays.sortDateAsc": "古い順",
    "replays.sortRankDesc": "ランク高い順",
    "replays.search": "検索",
    "replays.results": "検索結果",
    "replays.promptSearch": "条件を入力して検索してください",
    "replays.searching": "検索中...",
    "replays.searchFailed": "検索に失敗しました",
    "replays.noResults": "該当するリプレイがありません",
    "replays.resultMeta": "{shown} / {total} 件表示",
    "replays.colDateTime": "日時",
    "replays.colHost": "ホスト",
    "replays.colClient": "クライアント",
    "replays.colResult": "勝敗",
    "replays.colRanked": "ランクマ",
    "replays.colDL": "DL",
    "replays.badgeDiscord": "Discord",
    "replays.badgeProfile": "プロファイル",

    // msg (lobby messages / toasts)
    "msg.send": "✉ 送る",
    "msg.sent": "✉ 送信済",
    "msg.waiting": "✉ 待機中",
    "msg.sentOk": "送信しました",
    "msg.cooldown": "しばらく待ってから送ってください",
    "msg.askGiuroll": "Giuroll をお願いする",
    "msg.inviteCasual": "カジュアル対戦に誘う",
    "msg.typeGiurollRequest": "Giuroll リクエスト",
    "msg.typeCasualInvite": "カジュアル対戦のお誘い",
    "msg.hostDefault": "ホスト",
    "msg.toastAcceptGiuroll":
      "ホスト {name} さんが Giuroll リクエストを承諾しました",
    "msg.toastAcceptCasual":
      "ホスト {name} さんがカジュアル対戦のお誘いを承諾しました",
    "msg.toastDecline":
      "ホスト {name} さん: ごめんなさい（{reqLabel}は見送り）",

    // err (API / server error strings)
    "err.unknown": "エラーが発生しました",
    "err.invalidSession": "セッションが無効または期限切れです",
    "err.loginRequired": "ログインが必要です",
    "err.discordLoginRequired": "Discord ログインが必要です",
    "err.discordNotConfigured": "Discord ログインが設定されていません",
    "err.databaseNotConfigured": "データベースが設定されていません",
    "err.postNotFound": "募集が見つかりません",
    "err.invalidOwnerToken": "オーナートークンが無効です",
    "err.notFound": "見つかりません",
    "err.loginRequestExpired": "ログイン要求の有効期限が切れました",
    "err.invalidPort": "ポート番号が無効です",
    "err.codeExpired": "コードの有効期限が切れました",
    "err.userNotFound": "ユーザーが見つかりません",
    "err.rankAlreadyLocked": "開始ランクは既に確定しています",
    "err.invalidStreamUrl":
      "配信 URL は YouTube・Twitch・ニコニコのいずれかである必要があります",
    "err.tooManyCreateRequests": "作成リクエストが多すぎます",
    "err.tooManyActivePosts": "アクティブな募集が多すぎます",
    "err.cannotMessageOwnPost": "自分の募集にはメッセージを送れません",
    "err.giurollAlreadyEnabled": "Giuroll は既に有効です",
    "err.notRankedPost": "ランクマ募集ではありません",
    "err.messageCooldown": "しばらく待ってからメッセージを送ってください",
    "err.messageNotFound": "メッセージが見つかりません",
    "err.alreadyReplied": "既に返信済みです",
    "err.emptyBody": "本文が空です",
    "err.replayTooLarge": "リプレイが大きすぎます",
    "err.clientOutdated":
      "このクライアントバージョンはサポートされていません。asobby を更新してください",
    "err.invalidTskDatabase": "天則観 DB が不正です: {reason}",
    "err.tooManyTskRows": "天則観 DB の行数が多すぎます",
    "err.tskDatabaseTooLarge": "天則観 DB が大きすぎます",
    "err.notSqliteDatabase": "SQLite データベースではありません",
    "err.addrMustBeIpv4":
      "アドレスは IPv4:port 形式である必要があります（ゲームは IPv6 非対応）",
    "err.hostNotReachable": "ホストに接続できません",
    "err.autopunchHostNotReachable":
      "Autopunch 経由でホストに接続できません（Autopunch は起動していますか？）",
  };

  const en = {
    // nav
    "nav.lobby": "Lobby",
    "nav.stats": "Stats",
    "nav.replays": "Replay search",
    "nav.clientDownload": "Download client",
    "nav.support": "Support us",

    "clientUpdate.banner": "Windows client v{version} is available",
    "clientUpdate.download": "View on GitHub",
    "clientUpdate.dismiss": "Dismiss",
    "common.appName": "asobby",
    "common.loading": "Loading…",
    "common.loadFailed": "Failed to load",
    "common.networkError": "Network error",
    "common.errorWithStatus": "Error ({status})",
    "common.errorWithStatusDetail": "Error ({status}): {detail}",
    "common.copied": "Copied",
    "common.clickToCopy": "Click to copy",
    "common.noData": "No data",
    "common.more": "Load more",
    "common.upload": "Upload",
    "common.clearAll": "Clear all",
    "common.removeFilter": "Remove",
    "common.vs": " vs ",
    "common.vsLower": "vs ",
    "common.unknown": "(unknown)",
    "common.none": "(none)",
    "common.unspecified": "(any)",
    "common.countItems": "{n} posts",
    "common.totalGames": "{n} matches total",
    "common.failed": "Failed",
    "common.dl": "DL",
    "common.stream": "▶ Stream",
    "common.connecting": "Connecting…",
    "common.reconnecting": "Reconnecting…",
    "common.realtimeUpdating": "Live updates",
    "common.discordLoginRequired": "Discord login required",
    "common.loginGateClientHint":
      'Log in from the asobby client (tray menu "Log in with Discord").',
    "common.resultWin": "W",
    "common.resultLoss": "L",
    "common.resultDraw": "D",
    "common.badgeGiu": "Giu",
    "common.badgeAp": "AP",
    "common.sortAsc": " ▲",
    "common.sortDesc": " ▼",

    "auth.loginOk": "Login complete",
    "auth.loginFail": "Login failed",
    "auth.closeTab": "You may close this tab.",
    "auth.cancelled": "Login was cancelled.",
    "auth.discordFailed": "Failed to connect to Discord.",
    "auth.discordUserFailed": "Failed to fetch Discord user info.",
    "auth.discordUserInvalid": "Invalid Discord user info.",
    "auth.loginSuccess": "Logged in as {name}. Return to the app.",

    // lobby
    "lobby.pageTitle": "asobby - Touhou Hisoutensoku lobby",
    "lobby.subtitle": "Touhou Hisoutensoku matchmaking lobby",
    "lobby.rankChoiceTitle":
      "Choose your starting ranked tier (first time only; defaults to N if skipped)",
    "lobby.loginGateLine1": "Discord login is required to view listings.",
    "lobby.sectionRanked": "Ranked listings",
    "lobby.sectionCasual": "Casual listings",
    "lobby.colMatch": "Match",
    "lobby.colUser": "User",
    "lobby.colRank": "Rank",
    "lobby.colCap": "Cap",
    "lobby.colStream": "Stream",
    "lobby.colComment": "Comment",
    "lobby.colAddr": "Addr",
    "lobby.emptyRanked": "No ranked listings right now",
    "lobby.emptyCasual": "No casual listings right now",
    "lobby.loggedInAs": "Logged in as {name} ({rank})",
    "lobby.rankConfirm":
      "Set starting rank to {label}. This choice is one-time only. Continue?",
    "lobby.rankDescEasy": "Recommended for beginners",
    "lobby.rankDescNormal": "Default start · beginners and up",
    "lobby.rankDescEx": "Intermediate tier",
    "lobby.rankDescHard": "Intermediate and up",
    "lobby.rankDescLuna": "Advanced and up",

    "lobby.chatTitle": "Lobby chat (post-match chat welcome)",
    "lobby.chatPlaceholder": "Type a message (@ to mention)",
    "lobby.chatSend": "Send",
    "lobby.chatHide": "Hide lobby chat",
    "lobby.chatShow": "Show lobby chat",
    "lobby.chatEmpty": "No messages yet",
    "lobby.chatCooldown": "Please wait before sending another message",

    // stats
    "stats.pageTitle": "asobby - Stats",
    "stats.title": "asobby Stats",
    "stats.subtitle": "Touhou Hisoutensoku match history",
    "stats.loadingMatches": "Loading match data…",
    "stats.gateLine1": "Discord login is required to view stats",
    "stats.resync": "Resync with server",
    "stats.resyncing": "Resyncing…",
    "stats.resyncDone": "Resynced ({n} matches, {time})",
    "stats.resyncFailed": "Resync failed",
    "stats.ranked": "Ranked",
    "stats.filter": "Filters",
    "stats.facetMyChar": "By your character",
    "stats.facetOppChar": "By opponent character",
    "stats.facetOppProfile": "By opponent profile",
    "stats.colChar": "Char",
    "stats.colProfile": "Profile",
    "stats.colGames": "Games",
    "stats.colWins": "W",
    "stats.colLosses": "L",
    "stats.colWinRate": "Win %",
    "stats.history": "Match history",
    "stats.colDateTime": "Date/time",
    "stats.colMyChar": "Your char",
    "stats.colOppChar": "Opp. char",
    "stats.colOppProfile": "Opp. profile",
    "stats.colResult": "Result",
    "stats.colRanked": "Ranked",
    "stats.colReplay": "Replay",
    "stats.importTitle": "Import Tensokukan",
    "stats.importDesc":
      "Upload a Tensokukan (tsk) stats DB (.db) to import past matches. Re-uploading the same file will not create duplicates.",
    "stats.selectFile": "Please select a file",
    "stats.importResult":
      "Imported: {imported} / Duplicates skipped: {skippedDup} / Invalid skipped: {skippedInvalid} ({total} rows total)",
    "stats.uploadFailed": "Upload failed",
    "stats.filterMyChar": "Your char: {name}",
    "stats.filterOppChar": "Opponent: {name}",
    "stats.filterOppProfile": "Opp. profile: {profile}",
    "stats.filterHint": "Click a facet row to filter",
    "stats.rankedOnly": "Ranked only",
    "stats.summaryMain":
      "{games} played · {wins}W {losses}L · win rate {rate} ({draws} draws)",
    "stats.summaryRecent": "Last {n}: ",
    "stats.summaryRecentPartial": " ({games} games)",
    "stats.currentRank": "Current rank: {rank}",
    "stats.rankedTotalLabel": "Ranked overall:",
    "stats.rankedTotalRecord": "{wins}-{losses}-{draws}",
    "stats.winRateLabel": "Win rate:",
    "stats.rankedTotalGames": "({games} games)",
    "stats.rankedRecent30": "Last 30: {rate} ({wins}W / {games} games)",

    // replays
    "replays.pageTitle": "asobby - Replay search",
    "replays.title": "asobby Replay search",
    "replays.subtitle": "Touhou Hisoutensoku replay list",
    "replays.searchCriteria": "Search filters",
    "replays.playerName": "Player name",
    "replays.playerPlaceholder": "Profile name / Discord name",
    "replays.char1": "Character 1",
    "replays.char2": "Character 2 (matchup)",
    "replays.dateFrom": "Date (from)",
    "replays.dateTo": "Date (to)",
    "replays.sortOrder": "Sort by",
    "replays.sortDateDesc": "Newest first",
    "replays.sortDateAsc": "Oldest first",
    "replays.sortRankDesc": "Highest rank first",
    "replays.search": "Search",
    "replays.results": "Results",
    "replays.promptSearch": "Enter filters and search",
    "replays.searching": "Searching…",
    "replays.searchFailed": "Search failed",
    "replays.noResults": "No matching replays",
    "replays.resultMeta": "Showing {shown} / {total}",
    "replays.colDateTime": "Date/time",
    "replays.colHost": "Host",
    "replays.colClient": "Client",
    "replays.colResult": "Result",
    "replays.colRanked": "Ranked",
    "replays.colDL": "DL",
    "replays.badgeDiscord": "Discord",
    "replays.badgeProfile": "Profile",

    // msg
    "msg.send": "✉ Send",
    "msg.sent": "✉ Sent",
    "msg.waiting": "✉ Wait",
    "msg.sentOk": "Sent",
    "msg.cooldown": "Please wait before sending again",
    "msg.askGiuroll": "Request Giuroll",
    "msg.inviteCasual": "Invite to casual match",
    "msg.typeGiurollRequest": "Giuroll request",
    "msg.typeCasualInvite": "Casual match invite",
    "msg.hostDefault": "Host",
    "msg.toastAcceptGiuroll": "Host {name} accepted your Giuroll request",
    "msg.toastAcceptCasual": "Host {name} accepted your casual match invite",
    "msg.toastDecline":
      "Host {name}: Sorry ({reqLabel} declined)",

    // err
    "err.unknown": "An error occurred",
    "err.invalidSession": "Invalid or expired session",
    "err.loginRequired": "Login required",
    "err.discordLoginRequired": "Discord login required",
    "err.discordNotConfigured": "Discord login is not configured",
    "err.databaseNotConfigured": "Database is not configured",
    "err.postNotFound": "Listing not found",
    "err.invalidOwnerToken": "Invalid owner token",
    "err.notFound": "Not found",
    "err.loginRequestExpired": "Login request expired",
    "err.invalidPort": "Invalid port",
    "err.codeExpired": "Code expired",
    "err.userNotFound": "User not found",
    "err.rankAlreadyLocked": "Starting rank is already locked",
    "err.invalidStreamUrl":
      "Stream URL must be YouTube, Twitch, or Niconico",
    "err.tooManyCreateRequests": "Too many create requests",
    "err.tooManyActivePosts": "Too many active listings",
    "err.cannotMessageOwnPost": "Cannot message your own listing",
    "err.giurollAlreadyEnabled": "Giuroll is already enabled",
    "err.notRankedPost": "Not a ranked listing",
    "err.messageCooldown": "Please wait before sending another message",
    "err.messageNotFound": "Message not found",
    "err.alreadyReplied": "Already replied",
    "err.emptyBody": "Empty body",
    "err.replayTooLarge": "Replay too large",
    "err.clientOutdated":
      "This client version is no longer supported; please update asobby",
    "err.invalidTskDatabase": "Invalid Tensokukan DB: {reason}",
    "err.tooManyTskRows": "Too many rows in Tensokukan DB",
    "err.tskDatabaseTooLarge": "Tensokukan DB too large",
    "err.notSqliteDatabase": "Not a SQLite database",
    "err.addrMustBeIpv4":
      "Address must be IPv4:port (the game does not support IPv6)",
    "err.hostNotReachable": "Host not reachable",
    "err.autopunchHostNotReachable":
      "Host not reachable via Autopunch (is Autopunch running?)",
  };

  const dicts = { ja, en };

  /** @param {string} key @param {Record<string, string|number>|undefined} params */
  function t(key, params) {
    let s = dicts[lang][key];
    if (s == null) s = dicts.ja[key] || key;
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        s = s.replaceAll(`{${k}}`, String(v));
      }
    }
    return s;
  }

  function getLang() {
    return lang;
  }

  function setLang(next) {
    if (!isLang(next)) return;
    localStorage.setItem(STORAGE_KEY, next);
    const params = new URLSearchParams(location.search);
    params.delete("lang");
    const qs = params.toString();
    location.href = location.pathname + (qs ? `?${qs}` : "");
  }

  function applyDocumentI18n() {
    document.documentElement.lang = lang;
    for (const el of document.querySelectorAll("[data-i18n]")) {
      const key = el.getAttribute("data-i18n");
      if (key) el.textContent = t(key);
    }
    for (const el of document.querySelectorAll("[data-i18n-title]")) {
      const key = el.getAttribute("data-i18n-title");
      if (key) el.title = t(key);
    }
    const titleEl = document.querySelector("title[data-i18n]");
    if (titleEl) {
      const key = titleEl.getAttribute("data-i18n");
      if (key) document.title = t(key);
    }
  }

  function initLangToggle() {
    const btn = document.getElementById("lang-toggle");
    if (!btn) return;
    function updateLabel() {
      btn.textContent = LANG_SWITCH_LABEL[lang];
    }
    updateLabel();
    btn.addEventListener("click", () => {
      setLang(lang === "ja" ? "en" : "ja");
    });
  }

  const ERROR_MAP = {
    "invalid or expired session": "err.invalidSession",
    "login required": "err.loginRequired",
    "discord login required": "err.discordLoginRequired",
    "discord login is not configured": "err.discordNotConfigured",
    "database is not configured": "err.databaseNotConfigured",
    "post not found": "err.postNotFound",
    "invalid owner_token": "err.invalidOwnerToken",
    "not found": "err.notFound",
    "login request expired": "err.loginRequestExpired",
    "invalid port": "err.invalidPort",
    "code expired": "err.codeExpired",
    "user not found": "err.userNotFound",
    "rank already locked": "err.rankAlreadyLocked",
    "stream_url must be youtube, twitch, or niconico": "err.invalidStreamUrl",
    "too many create requests": "err.tooManyCreateRequests",
    "too many active posts": "err.tooManyActivePosts",
    "cannot message your own post": "err.cannotMessageOwnPost",
    "giuroll is already enabled": "err.giurollAlreadyEnabled",
    "not a ranked post": "err.notRankedPost",
    "please wait before sending another message": "err.messageCooldown",
    "message not found": "err.messageNotFound",
    "already replied": "err.alreadyReplied",
    "empty body": "err.emptyBody",
    "replay too large": "err.replayTooLarge",
    "this client version is no longer supported; please update asobby":
      "err.clientOutdated",
    "too many rows in tsk database": "err.tooManyTskRows",
    "tsk database too large": "err.tskDatabaseTooLarge",
    "not a sqlite database": "err.notSqliteDatabase",
    "addr must be IPv4:port (IPv6 is not supported by the game)":
      "err.addrMustBeIpv4",
    "host not reachable": "err.hostNotReachable",
    "autopunch host not reachable (is autopunch running?)":
      "err.autopunchHostNotReachable",
  };

  /** @param {unknown} detail */
  function tError(detail) {
    if (detail == null || detail === "") return t("err.unknown");
    if (typeof detail === "object") {
      const obj = /** @type {{ message?: string; detail?: string }} */ (detail);
      if (obj.message) return tError(obj.message);
      if (obj.detail) return tError(obj.detail);
      return t("err.unknown");
    }
    const s = String(detail);
    const mapped = ERROR_MAP[s];
    if (mapped) return t(mapped);
    const tskPrefix = "invalid tsk database:";
    if (s.startsWith(tskPrefix)) {
      return t("err.invalidTskDatabase", {
        reason: s.slice(tskPrefix.length).trim(),
      });
    }
    return s;
  }

  window.t = t;
  window.getLang = getLang;
  window.setLang = setLang;
  window.applyDocumentI18n = applyDocumentI18n;
  window.initLangToggle = initLangToggle;
  window.tError = tError;
})();
