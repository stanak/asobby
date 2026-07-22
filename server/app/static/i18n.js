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
    "nav.guide": "使い方",
    "nav.settings": "設定",
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
    "common.onlineCount": "オンライン {n}人",
    "common.onlineCountTitle": "現在 asobby.com を閲覧中の人数",
    "common.discordLoginRequired": "Discord ログインが必要です",
    "common.loginGateClientHint":
      "asobby クライアント（トレイメニュー「Discord でログイン」）からログインしてください。",
    "common.resultWin": "○",
    "common.resultLoss": "×",
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
    "lobby.colCap": "Require",
    "lobby.colRequire": "Require",
    "lobby.colStream": "Stream",
    "lobby.colComment": "Comment",
    "lobby.colPing": "Ping",
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
    "lobby.chatTabJa": "JP",
    "lobby.chatTabEn": "EN",
    "lobby.chatPlaceholder": "メッセージを入力 (@でメンション)",
    "lobby.chatSend": "送信",
    "lobby.chatHide": "ロビーチャットを隠す",
    "lobby.chatShow": "ロビーチャットを表示",
    "lobby.chatEmpty": "まだメッセージはありません",
    "lobby.chatCooldown": "しばらく待ってから送ってください",
    "lobby.pingUnavailable": "—",
    "lobby.pingClientRequired":
      "閲覧者 PC で asobby クライアントを起動し、ブラウザで localhost への接続を許可すると Ping を表示できます",
    "lobby.pingBannerTitle": "Ping 列を表示するには追加の設定が必要です",
    "lobby.pingBannerBody":
      "ロビーは閲覧者 PC の asobby クライアント (127.0.0.1) と通信して Ping を測定します。初回はブラウザが「ローカル ネットワークへのアクセス」の許可を求めます。",
    "lobby.pingBannerDenied":
      "ブラウザでローカル接続がブロックされています。アドレスバー左のサイト設定 → 「ローカル ネットワークへのアクセス」（または「ループバック ネットワークへのアクセス」）を「許可」にしてから再試行してください。",
    "lobby.pingBannerChecklist":
      "確認: (1) asobby クライアントが起動している (2) ブラウザで localhost への接続を許可している",
    "lobby.pingBannerRetry": "接続を再試行",
    "lobby.pingBannerRetrying": "確認中…",
    "lobby.pingBannerGuide": "詳しい手順（使い方）",
    "lobby.pingProbeFailed":
      "あなたの PC からホストへ UDP で到達できません (Autopunch / ポート開放 / 天則ホスト待機を確認)",
    "lobby.pingThresholdHint": "警告しきい値: {threshold}ms",

    // stats
    "stats.pageTitle": "asobby - 戦績",
    "settings.pageTitle": "asobby - 設定",
    "settings.title": "asobby 設定",
    "settings.subtitle": "通知と表示",
    "settings.gateLine1": "設定を変更するには Discord ログインが必要です",
    "settings.faviconSection": "タブ favicon 通知",
    "settings.faviconHint":
      "ロビーに条件を満たす他人の募集があるとき、ブラウザタブのアイコンに色付きの点が表示されます。Ping 条件はデスクトップクライアント起動時のみ有効です。",
    "settings.legendRanked": "青 — ランクマ",
    "settings.legendCasual": "緑 — カジュアル",
    "settings.rankedEnabled": "ランクマ募集を通知する",
    "settings.rankedSameBand": "自分と同じランク帯のランクマのみ",
    "settings.casualEnabled": "カジュアル募集を通知する",
    "settings.excludeInBattle": "対戦中の募集は除外する",
    "settings.maxPingMs": "Ping 上限 (ms)",
    "settings.requirePing": "Ping が測れない募集は通知しない",
    "settings.save": "保存",
    "settings.saving": "保存中…",
    "settings.saved": "保存しました",
    "settings.saveFailed": "保存に失敗しました",
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
    "stats.colRating": "レート",
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
      "{games} 戦 {wins} 勝 {losses} 敗 勝率 {rate}",
    "stats.summaryRecent": "直近{n}: ",
    "stats.summaryRecentPartial": " ({games}戦)",
    "stats.currentRank": "現在ランク: {rank}",
    "stats.rankedTotalLabel": "ランクマ総合成績:",
    "stats.rankedTotalRecord": "{wins}-{losses}",
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

    // guide
    "guide.pageTitle": "asobby - 使い方",
    "guide.title": "使い方",
    "guide.subtitle": "Windows クライアント",
    "guide.sectionBasic": "基本の使い方",
    "guide.basicLead": "asobby は、非想天則のホスト募集を自動で Web ロビーに掲載するツールです。",
    "guide.basicStep1": "Windows クライアントを起動し、タスクトレイに常駐させます。",
    "guide.basicStep2": "天則を起動してホストを立てます。",
    "guide.basicStep3": "クライアントがホスト状態を検知すると、接続可能な場合は自動でロビーに募集が掲載されます。",
    "guide.basicFail":
      "接続できない場合（ポート未開放・Autopunch 未設定など）は募集されず、トースト通知で「募集に失敗しました: ポート開放または autopunch を確認してください」と知らせます。",
    "guide.basicLogin":
      "asobby の利用（募集の自動投稿・Web ロビー・戦績・チャットなど）には、トレイメニューから Discord ログインが必要です。",
    "guide.sectionRanked": "ランクマの仕様",
    "guide.rankedIntro":
      "ランクマ募集で、同じランク帯の相手（または「格上に挑戦」ON 時は 1 段上位）と Discord ログイン済みの相手が対戦すると、条件を満たす対戦はランクマとして記録されます。",
    "guide.rankedSessionLimit":
      "同じ相手との連続対戦では、ランクマとして数えられるのは最初の 3 戦までです。4 戦目以降はカジュアル扱いになります（募集タイプがランクマのままでも）。",
    "guide.rankedGuestReset":
      "相手が切断したり、別の人が入ったりするとセッションが切り替わり、対戦カウントがリセットされます。一度 3 戦終えた相手でも、間に別の人が入ったあとなら、再びランクマ 3 戦が可能です。",
    "guide.rankedEval":
      "昇格・降格は、現在のランク帯における直近 30 戦の勝率で判定します（30 戦揃ってから判定されます）。",
    "guide.rank.colRank": "ランク",
    "guide.rank.colPromote": "昇格条件",
    "guide.rank.colDemote": "降格条件",
    "guide.rank.none": "—",
    "guide.rank.e.promote": "勝率 50% 以上 → N",
    "guide.rank.n.promote": "勝率 50% 以上 → Ex",
    "guide.rank.ex.promote": "勝率 60% 以上 → H",
    "guide.rank.ex.demote": "勝率 20% 未満 → N",
    "guide.rank.h.promote": "勝率 60% 以上 → L",
    "guide.rank.h.demote": "勝率 20% 未満 → Ex",
    "guide.rank.l.promote": "勝率 70% 以上 → Ph",
    "guide.rank.l.demote": "勝率 20% 未満 → H",
    "guide.rank.ph.promote": "昇格なし",
    "guide.rank.ph.demote": "降格なし",
    "guide.rankedPh":
      "Ph 帯ではランクの昇降格はありません。Ph 同士のランクマ対戦では、キャラごとのレート（TrueSkill）が更新されます。",
    "guide.sectionStats": "戦績の管理",
    "guide.statsIntro":
      "対戦結果はクライアント側にも保存され、Discord ログイン後にサーバーとも同期されます（トレイの「戦績をサーバーと同期」、または Web 戦績ページ）。",
    "guide.statsClient": "クライアントの「戦績を見る...」— PC 内の記録をウィンドウで閲覧",
    "guide.statsWeb":
      "Web の戦績ページ (/stats) — サーバー上の記録を閲覧（要 Discord ログイン）",
    "guide.statsFilterLead":
      "どちらも、次の表の行をクリックすると対戦履歴が絞り込まれます。組み合わせて使うと、知りたい情報にすぐたどり着けます。",
    "guide.statsFilterMyChar": "自キャラ別 — 自分が使ったキャラでフィルタ",
    "guide.statsFilterOppChar": "相手キャラ別 — 相手キャラでフィルタ",
    "guide.statsFilterOppProfile": "相手プロファイル別 — 相手のプロファイル名でフィルタ",
    "guide.statsFilterNote":
      "フィルタは対戦履歴・集計の両方に反映されます。チップの × または「クリア」で解除できます。",
    "guide.sectionHighPing": "高 Ping 警告",
    "guide.highPingIntro":
      "ロビー閲覧者がホストへ接続する際の Ping を監視し、しきい値を超えたときにホスト PC へトースト通知します。対戦前に回線状況を把握するための機能です。",
    "guide.highPingWhen":
      "ゲストが接続したあとだけ判定します。募集中（ゲスト未接続）の間は警告しません。",
    "guide.highPingGuest":
      "警告を送れるのは、接続中のゲスト本人（Discord ログイン済み）だけです。他の閲覧者の Ping では通知されません。",
    "guide.highPingThreshold":
      "デフォルトのしきい値は通常 60ms、Giuroll 使用ホストは 100ms です。Giuroll ホストかどうかで自動的に使い分けます。",
    "guide.highPingSettings":
      "ON/OFF としきい値 (ms) は「投稿設定...」で変更できます。トレイメニューの「高 Ping 警告」チェックでも ON/OFF を切り替えられます（しきい値は投稿設定側）。",
    "guide.highPingLobby":
      "Web ロビーの Ping 列は、閲覧者 PC で asobby クライアントが起動しているときだけ表示されます。緑 (良好) / 黄 (しきい値の 75% 以上) / 赤 (しきい値以上) で色分けされます。",
    "guide.highPingBrowserTitle": "ブラウザのローカル接続許可（Ping 列）",
    "guide.highPingBrowserIntro":
      "Ping 列は Web ロビー (asobby.com) から、閲覧者 PC 上の asobby クライアント API (127.0.0.1:49152) へ接続して測定します。",
    "guide.highPingBrowserPrompt":
      "Chrome / Edge 142 以降では、初回アクセス時にブラウザが「ローカル ネットワークへのアクセス」（127.0.0.1 の場合は「ループバック ネットワークへのアクセス」）の許可を求めます。「許可」を選ぶと Ping 列が使えます。",
    "guide.highPingBrowserStepsTitle": "誤ってブロックした場合の再許可",
    "guide.highPingBrowserStep1":
      "asobby.com のロビーを開いた状態で、アドレスバー左の鍵（または調整）アイコン → 「サイトの設定」",
    "guide.highPingBrowserStep2":
      "「ローカル ネットワークへのアクセス」（または「ループバック ネットワークへのアクセス」）を「許可」に変更",
    "guide.highPingBrowserStep3":
      "ページを再読み込みするか、ロビー上部の「接続を再試行」を押す",
    "guide.highPingBrowserNote":
      "クライアント未起動の場合も Ping は「—」のままです。asobby トレイアイコンが表示されているかも確認してください。",
    "guide.sectionIcon": "トレイアイコンの色",
    "guide.iconIdle": "灰色 — 待機中。天則でホストを立てると自動投稿を開始します。",
    "guide.iconRecruit": "緑 — 募集中。ロビーに掲載中です。",
    "guide.iconBattle": "橙 — 対戦中。ゲストが接続した状態です。",
    "guide.iconStatus": "メニュー最上部のステータス行にも、同じ状態がテキストで表示されます。",
    "guide.sectionMenu": "トレイメニュー各項目",
    "guide.menu.openLobby.name": "ロビーページを開く",
    "guide.menu.openLobby.desc":
      "Web ロビーをブラウザで開きます。募集一覧の閲覧・ロビーチャット・相手への定型メッセージ送信ができます（要 Discord ログイン）。",
    "guide.menu.settings.name": "投稿設定...",
    "guide.menu.settings.desc":
      "募集モード（カジュアル / ランクマ）、コメント候補、配信 URL 候補、ランクマ時の「格上に挑戦」、高 Ping 警告 (ON/OFF・通常 60ms / Giuroll 100ms) を設定します。OK でサーバーへ反映されます。",
    "guide.menu.pingWarn.name": "高 Ping 警告",
    "guide.menu.pingWarn.desc":
      "高 Ping 警告の ON/OFF を切り替えます。しきい値 (ms) の変更は「投稿設定...」から行います。",
    "guide.menu.stats.name": "戦績を見る...",
    "guide.menu.stats.desc":
      "PC 内に保存した対戦記録をウィンドウで表示します。フィルタやソートで自分の戦績を確認できます。",
    "guide.menu.syncStats.name": "戦績をサーバーと同期",
    "guide.menu.syncStats.desc":
      "ローカル戦績とサーバー上の記録を双方向に同期します（要 Discord ログイン）。",
    "guide.menu.postType.name": "募集タイプ切替",
    "guide.menu.postType.desc":
      "カジュアルとランクマを切り替えます。いずれの募集も Discord ログインが必要です。",
    "guide.menu.comment.name": "コメント切替",
    "guide.menu.comment.desc":
      "ロビーに表示するコメント文を、投稿設定で登録した候補から選びます。",
    "guide.menu.stream.name": "配信URL切替",
    "guide.menu.stream.desc":
      "YouTube / Twitch / ニコニコの配信 URL を候補から選び、ロビーに表示します。",
    "guide.menu.pause.name": "ロビー自動投稿を一時停止",
    "guide.menu.pause.desc":
      "asobby.com への自動投稿だけを止めます。天則の検知・到達性チェックは継続します（30 分 / 1 時間 / 3 時間 / 許可するまで、または今すぐ再開）。",
    "guide.menu.copyAddr.name": "ホスト時に IP:Port をコピー",
    "guide.menu.copyAddr.desc":
      "ON にすると、募集開始時に接続アドレスをクリップボードへコピーします。",
    "guide.menu.challengeUpper.name": "格上に挑戦",
    "guide.menu.challengeUpper.desc":
      "ランクマ募集時のみ表示。ON の間、1 段上位ランク帯の相手との対戦もランクマ扱いになります（ランクマモード時のみ）。",
    "guide.menu.reply.name": "リクエストに返信",
    "guide.menu.reply.desc":
      "ロビー閲覧者から Giuroll 使用依頼やカジュアルお誘いが届いたとき、承諾 / 拒否で返信します。未返信があるときだけ表示されます。",
    "guide.menu.discord.name": "Discord でログイン / ログアウト",
    "guide.menu.discord.desc":
      "asobby 全般に必要な Discord アカウント連携です。ログアウト後の再ログインではアカウント選択画面が開きます。",
    "guide.menu.tools.name": "Autopunch / Giuroll / 天則 (soku)",
    "guide.menu.tools.desc":
      "各ツールのパス設定・起動・停止です。初回は exe の場所を指定します。Autopunch はポート開放なしで接続可能にするために使います。",
    "guide.menu.update.name": "更新をダウンロード",
    "guide.menu.update.desc":
      "新しいクライアント版が公開されているときだけ表示され、GitHub の Release ページを開きます。",
    "guide.menu.log.name": "ログを開く",
    "guide.menu.log.desc":
      "asobby.log をエディタで開き、検知状態やエラーの詳細を確認できます。",
    "guide.menu.resetPaths.name": "ツールのパスをリセット",
    "guide.menu.resetPaths.desc":
      "Autopunch / Giuroll / 天則の exe パス設定をクリアします。",
    "guide.menu.lang.name": "言語 / Language",
    "guide.menu.lang.desc":
      "クライアント UI の表示言語（日本語 / English）を切り替えます。",
    "guide.menu.quit.name": "終了",
    "guide.menu.quit.desc":
      "クライアントを終了します。募集中の投稿は閉じられます。",
    "guide.sectionWinNotify": "Windows の通知設定",
    "guide.winNotifyIntro":
      "asobby クライアントは、募集失敗・高 Ping 警告・勝敗数・リクエストなどを Windows のトースト通知で知らせます。ゲーム中（フルスクリーン）でも見逃しにくくするため、Windows 側の設定を整えることをおすすめします。",
    "guide.winNotifyDuration":
      "【約 7 秒で自動的に消えます】通知バナーは画面右下に短時間表示されたあと引っ込みます。内容は Win + N の通知センターから後から確認できます。",
    "guide.winNotifyPriorityLead":
      "とくに非想天則をフルスクリーンで遊ぶときは、「優先通知」に asobby を登録しておくことをおすすめします。おやすみモード中でも重要な通知が届きやすくなります。",
    "guide.winNotifyWin11Title": "Windows 11 — 優先通知を設定する",
    "guide.winNotifyWin11Step1":
      "設定 → システム → 通知 → 「優先通知を設定する」を開きます。",
    "guide.winNotifyWin11Step2":
      "「アプリ」で アプリの追加 を押し、一覧から asobby を選びます（初回通知後に一覧へ出る場合があります）。",
    "guide.winNotifyWin11Step3":
      "同じ画面で asobby の通知がオンになっていることを確認します。",
    "guide.winNotifyScreenshotAlt":
      "Windows 11 の優先通知を設定する画面。アプリの追加ボタンから asobby を登録する。",
    "guide.winNotifyScreenshotCaption":
      "Windows 11 — システム → 通知 → 優先通知を設定する",
    "guide.winNotifyImportant":
      "ホスト接続の失敗（ポート未開放など）だけ、asobby 側で「重要な通知」扱いと短い通知音が使われます。それ以外の通知は通常表示です。",
    "guide.winNotifyWin10":
      "Windows 10 の場合は、設定 → システム → 通知 → 集中モード で、ゲーム中・全画面アプリ中に自動でオンになるルールをオフにし、asobby の通知を許可してください。",
    "guide.winNotifyTray":
      "トレイメニューの 通知音 で、ホスト接続失敗時のチャイムの ON/OFF を切り替えられます（デフォルト ON）。",
    "guide.sectionAv": "ウイルス対策ソフトの警告について",
    "guide.avIntro":
      "asobby クライアント (PyInstaller でビルドした exe) は、コード署名がないため、Windows Defender などが誤検知（偽陽性）することがあります。新しい exe を配布するたびに警告が出る場合もありますが、asobby 自体にマルウェアは含まれていません。",
    "guide.avOpenSource":
      "ソースコードは https://github.com/stanak/asobby で公開されています。不安な場合は Actions のビルドログと照合するか、ご自身でビルドしてください。",
    "guide.avAllowTitle": "Windows Defender で許可する",
    "guide.avAllowStep1":
      "「ウイルスと脅威の防止」→「保護の履歴」を開き、asobby の検出項目を選びます。",
    "guide.avAllowStep2":
      "「操作」→「デバイス上で許可する」を選びます。",
    "guide.avAllowStep3":
      "再度 GitHub Releases から exe をダウンロードして実行します。",
    "guide.avReport":
      "Microsoft への誤検知報告: https://www.microsoft.com/en-us/wdsi/filesubmission から exe をアップロードし、「誤検知」と申告すると、今後の定義更新で改善されることがあります。",
    "guide.sectionNotify": "主な通知",
    "guide.notifyPostFailed":
      "募集失敗 — サーバーから接続確認が取れません。ポート転送・Autopunch・ファイアウォールを確認してください。",
    "guide.notifyLogin":
      "ログインが必要 — 募集の自動投稿などを行う前に Discord ログインしてください。",
    "guide.notifyCasual":
      "カジュアル扱い — ランクマ募集でも、相手が未ログインまたはランク帯不一致（格上挑戦 OFF 時）だと戦績はカジュアルになります。",
    "guide.notifyUpdate":
      "更新あり — 新しい exe が公開されたとき。トレイからダウンロードできます。",
    "guide.notifyHighPing":
      "高 Ping 警告 — 接続中ゲストの Ping がしきい値以上のとき、ホスト PC にトースト通知します（例: 「○○ さんからの Ping が 80ms です (警告: 60ms 以上)」）。",
  };

  const en = {
    // nav
    "nav.lobby": "Lobby",
    "nav.stats": "Stats",
    "nav.replays": "Replay search",
    "nav.clientDownload": "Download client",
    "nav.guide": "Guide",
    "nav.settings": "Settings",
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
    "common.onlineCount": "{n} online",
    "common.onlineCountTitle": "People currently viewing asobby.com",
    "common.discordLoginRequired": "Discord login required",
    "common.loginGateClientHint":
      'Log in from the asobby client (tray menu "Log in with Discord").',
    "common.resultWin": "W",
    "common.resultLoss": "L",
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
    "lobby.colCap": "Require",
    "lobby.colRequire": "Require",
    "lobby.colStream": "Stream",
    "lobby.colComment": "Comment",
    "lobby.colPing": "Ping",
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
    "lobby.chatTabJa": "JP",
    "lobby.chatTabEn": "EN",
    "lobby.chatPlaceholder": "Type a message (@ to mention)",
    "lobby.chatSend": "Send",
    "lobby.chatHide": "Hide lobby chat",
    "lobby.chatShow": "Show lobby chat",
    "lobby.chatEmpty": "No messages yet",
    "lobby.chatCooldown": "Please wait before sending another message",
    "lobby.pingUnavailable": "—",
    "lobby.pingClientRequired":
      "Run the asobby client on your PC and allow localhost access in your browser to show ping",
    "lobby.pingBannerTitle": "Extra setup is required to show the Ping column",
    "lobby.pingBannerBody":
      "The lobby talks to the asobby client on your PC (127.0.0.1) to measure ping. On first use, your browser asks for Local network access.",
    "lobby.pingBannerDenied":
      "Your browser is blocking local connections. Open site settings from the lock icon → set Local network access (or Loopback network access) to Allow, then retry.",
    "lobby.pingBannerChecklist":
      "Check: (1) the asobby client is running (2) your browser allows connections to localhost",
    "lobby.pingBannerRetry": "Retry connection",
    "lobby.pingBannerRetrying": "Checking…",
    "lobby.pingBannerGuide": "Full instructions (guide)",
    "lobby.pingProbeFailed":
      "UDP probe from your PC failed (check Autopunch, port forwarding, or host waiting screen)",
    "lobby.pingThresholdHint": "Warning threshold: {threshold}ms",

    // stats
    "stats.pageTitle": "asobby - Stats",
    "settings.pageTitle": "asobby - Settings",
    "settings.title": "asobby Settings",
    "settings.subtitle": "Notifications & display",
    "settings.gateLine1": "Discord login is required to change settings",
    "settings.faviconSection": "Tab favicon notifications",
    "settings.faviconHint":
      "When the lobby has matching recruitment from others, colored dots appear on the tab icon. Ping filters work only while the desktop client is running.",
    "settings.legendRanked": "Blue — ranked",
    "settings.legendCasual": "Green — casual",
    "settings.rankedEnabled": "Notify for ranked posts",
    "settings.rankedSameBand": "Ranked posts in my band only",
    "settings.casualEnabled": "Notify for casual posts",
    "settings.excludeInBattle": "Exclude posts already in battle",
    "settings.maxPingMs": "Max Ping (ms)",
    "settings.requirePing": "Skip posts when Ping is unavailable",
    "settings.save": "Save",
    "settings.saving": "Saving…",
    "settings.saved": "Saved",
    "settings.saveFailed": "Failed to save",
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
    "stats.colRating": "Rating",
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
      "{games} played · {wins}W {losses}L · win rate {rate}",
    "stats.summaryRecent": "Last {n}: ",
    "stats.summaryRecentPartial": " ({games} games)",
    "stats.currentRank": "Current rank: {rank}",
    "stats.rankedTotalLabel": "Ranked overall:",
    "stats.rankedTotalRecord": "{wins}-{losses}",
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

    // guide
    "guide.pageTitle": "asobby - Guide",
    "guide.title": "Guide",
    "guide.subtitle": "Windows client",
    "guide.sectionBasic": "Basics",
    "guide.basicLead":
      "asobby automatically lists your Hisoutensoku host on the web lobby.",
    "guide.basicStep1": "Start the Windows client and leave it running in the system tray.",
    "guide.basicStep2": "Launch Hisoutensoku and open a host.",
    "guide.basicStep3":
      "When the client detects hosting, it posts to the lobby automatically if the server can reach your host.",
    "guide.basicFail":
      "If you are not reachable (no port forwarding, Autopunch not set up, etc.), nothing is posted and a toast says: “Failed to post: check port forwarding or autopunch”.",
    "guide.basicLogin":
      "Discord login from the tray menu is required to use asobby (auto-posting, web lobby, stats, chat, and more).",
    "guide.sectionRanked": "Ranked rules",
    "guide.rankedIntro":
      "On ranked posts, qualifying matches against a logged-in opponent in the same band (or one band above with Challenge upper rank) are recorded as ranked.",
    "guide.rankedSessionLimit":
      "With the same opponent in one session, only the first 3 matches count as ranked. From the 4th onward they are casual, even if your post stays ranked.",
    "guide.rankedGuestReset":
      "When your opponent disconnects or someone else connects, the session resets and the match counter starts over. After another guest sits in between, you can play up to 3 ranked matches again with the same person.",
    "guide.rankedEval":
      "Promotion and demotion use your win rate over the latest 30 ranked matches in your current band (evaluated once all 30 are recorded).",
    "guide.rank.colRank": "Rank",
    "guide.rank.colPromote": "Promotion",
    "guide.rank.colDemote": "Demotion",
    "guide.rank.none": "—",
    "guide.rank.e.promote": "Win rate ≥ 50% → N",
    "guide.rank.n.promote": "Win rate ≥ 50% → Ex",
    "guide.rank.ex.promote": "Win rate ≥ 60% → H",
    "guide.rank.ex.demote": "Win rate < 20% → N",
    "guide.rank.h.promote": "Win rate ≥ 60% → L",
    "guide.rank.h.demote": "Win rate < 20% → Ex",
    "guide.rank.l.promote": "Win rate ≥ 70% → Ph",
    "guide.rank.l.demote": "Win rate < 20% → H",
    "guide.rank.ph.promote": "No promotion",
    "guide.rank.ph.demote": "No demotion",
    "guide.rankedPh":
      "There is no rank promotion or demotion in Ph. Ranked Ph vs Ph matches update per-character TrueSkill ratings.",
    "guide.sectionStats": "Stats",
    "guide.statsIntro":
      "Match results are stored on the client and synced to the server after Discord login (tray “Sync stats with server” or the web stats page).",
    "guide.statsClient": "Tray “View stats...” — browse records stored on this PC",
    "guide.statsWeb":
      "Web stats page (/stats) — browse server-side records (Discord login required)",
    "guide.statsFilterLead":
      "In both UIs, click a row in the breakdown tables below to filter match history. Combine filters to find what you need quickly.",
    "guide.statsFilterMyChar": "My character — filter by characters you played",
    "guide.statsFilterOppChar": "Opponent character — filter by opponent character",
    "guide.statsFilterOppProfile": "Opponent profile — filter by opponent profile name",
    "guide.statsFilterNote":
      "Filters apply to both history and summaries. Clear them with the chip × or Clear button.",
    "guide.sectionHighPing": "High ping warnings",
    "guide.highPingIntro":
      "Monitors lobby viewers’ ping to your host and shows a toast on your PC when it exceeds your threshold—useful before starting a match.",
    "guide.highPingWhen":
      "Warnings run only after a guest connects. No alerts while you are still recruiting with no guest.",
    "guide.highPingGuest":
      "Only the connected guest (Discord login required) can trigger a warning. Other viewers’ ping does not notify you.",
    "guide.highPingThreshold":
      "Default thresholds are 60ms normally and 100ms for Giuroll hosts. The client picks the Giuroll threshold automatically when Giuroll is in use.",
    "guide.highPingSettings":
      "Turn warnings on/off and set thresholds (ms) in Post settings…. The tray item “High ping warnings” toggles on/off only (thresholds stay in Post settings).",
    "guide.highPingLobby":
      "The lobby Ping column appears only while the asobby client is running on the viewer’s PC. Colors: green (good), yellow (≥75% of threshold), red (≥ threshold).",
    "guide.highPingBrowserTitle": "Browser local network permission (Ping column)",
    "guide.highPingBrowserIntro":
      "The Ping column works by connecting from the web lobby (asobby.com) to the asobby client API on your PC (127.0.0.1:49152).",
    "guide.highPingBrowserPrompt":
      "In Chrome / Edge 142+, the first connection triggers a Local network access prompt (Loopback network access for 127.0.0.1). Choose Allow to enable the Ping column.",
    "guide.highPingBrowserStepsTitle": "If you blocked it by mistake",
    "guide.highPingBrowserStep1":
      "On the asobby.com lobby, click the lock (or tune) icon left of the address bar → Site settings",
    "guide.highPingBrowserStep2":
      "Set Local network access (or Loopback network access) to Allow",
    "guide.highPingBrowserStep3":
      "Reload the page or click Retry connection at the top of the lobby",
    "guide.highPingBrowserNote":
      "Ping stays “—” if the client is not running too. Check that the asobby tray icon is visible.",
    "guide.sectionIcon": "Tray icon colors",
    "guide.iconIdle": "Gray — idle. Start a host in Hisoutensoku to begin auto-posting.",
    "guide.iconRecruit": "Green — recruiting. Your post is live on the lobby.",
    "guide.iconBattle": "Orange — in battle. A guest is connected.",
    "guide.iconStatus": "The status line at the top of the menu shows the same state in text.",
    "guide.sectionMenu": "Tray menu items",
    "guide.menu.openLobby.name": "Open lobby page",
    "guide.menu.openLobby.desc":
      "Opens the web lobby in your browser: post list, lobby chat, and preset messages to hosts (Discord login required).",
    "guide.menu.settings.name": "Post settings...",
    "guide.menu.settings.desc":
      "Post mode (casual / ranked), comment presets, stream URL presets, “Challenge upper rank” for ranked mode, and Ping warning thresholds (default 60ms / Giuroll 100ms). OK applies to the server.",
    "guide.menu.pingWarn.name": "High ping warnings",
    "guide.menu.pingWarn.desc":
      "Toggle high ping warnings on or off. Change thresholds (ms) in Post settings….",
    "guide.menu.stats.name": "View stats...",
    "guide.menu.stats.desc":
      "Opens a window with match history stored on this PC. Filter and sort your records.",
    "guide.menu.syncStats.name": "Sync stats with server",
    "guide.menu.syncStats.desc":
      "Two-way sync between local stats and the server (Discord login required).",
    "guide.menu.postType.name": "Post type",
    "guide.menu.postType.desc":
      "Switch between casual and ranked. All posts require Discord login.",
    "guide.menu.comment.name": "Comment preset",
    "guide.menu.comment.desc":
      "Pick the comment shown on the lobby from presets defined in Post settings.",
    "guide.menu.stream.name": "Stream URL preset",
    "guide.menu.stream.desc":
      "Pick a YouTube / Twitch / Niconico URL from presets to show on the lobby.",
    "guide.menu.pause.name": "Pause auto posting",
    "guide.menu.pause.desc":
      "Stops posting to asobby.com only. Detection and reachability checks keep running (30 min / 1 h / 3 h / until you resume, or resume now).",
    "guide.menu.copyAddr.name": "Copy IP:Port when hosting",
    "guide.menu.copyAddr.desc":
      "When enabled, copies your host address to the clipboard when recruiting starts.",
    "guide.menu.challengeUpper.name": "Challenge upper rank",
    "guide.menu.challengeUpper.desc":
      "Ranked posts only. While ON, matches against guests exactly one rank band above also count as ranked.",
    "guide.menu.reply.name": "Reply to requests",
    "guide.menu.reply.desc":
      "Accept or decline Giuroll requests and casual invites from lobby viewers. Shown only while requests are pending.",
    "guide.menu.discord.name": "Log in / Log out (Discord)",
    "guide.menu.discord.desc":
      "Links your Discord account for all asobby features. Re-login opens the account picker.",
    "guide.menu.tools.name": "Autopunch / Giuroll / Hisoutensoku (soku)",
    "guide.menu.tools.desc":
      "Set paths, launch, or stop each tool. Autopunch helps guests connect without manual port forwarding.",
    "guide.menu.update.name": "Download update",
    "guide.menu.update.desc":
      "Shown when a newer client release is available; opens the GitHub Releases page.",
    "guide.menu.log.name": "Open log",
    "guide.menu.log.desc":
      "Opens asobby.log for detection details and errors.",
    "guide.menu.resetPaths.name": "Reset tool paths",
    "guide.menu.resetPaths.desc":
      "Clears saved exe paths for Autopunch, Giuroll, and Hisoutensoku.",
    "guide.menu.lang.name": "Language / 言語",
    "guide.menu.lang.desc":
      "Switch the client UI between Japanese and English.",
    "guide.menu.quit.name": "Quit",
    "guide.menu.quit.desc":
      "Exits the client and closes any active post.",
    "guide.sectionWinNotify": "Windows notification settings",
    "guide.winNotifyIntro":
      "The asobby client uses Windows toast notifications for post failures, high ping warnings, win-loss counts, requests, and more. We recommend adjusting Windows settings so alerts are easier to notice while gaming (including fullscreen).",
    "guide.winNotifyDuration":
      "[Dismisses in about 7 seconds] Toast banners appear briefly at the bottom-right, then slide away. You can review them later in the notification center (Win + N).",
    "guide.winNotifyPriorityLead":
      "If you play Hisoutensoku in fullscreen, we strongly recommend adding asobby to priority notifications so important alerts are more likely during Focus assist / Do not disturb.",
    "guide.winNotifyWin11Title": "Windows 11 — Set priority notifications",
    "guide.winNotifyWin11Step1":
      "Open Settings → System → Notifications → Set priority notifications.",
    "guide.winNotifyWin11Step2":
      "Under Apps, click Add apps and choose asobby from the list (it may appear after the first notification).",
    "guide.winNotifyWin11Step3":
      "Confirm notifications for asobby are enabled on the same page.",
    "guide.winNotifyScreenshotAlt":
      "Windows 11 priority notifications settings. Use Add apps to register asobby.",
    "guide.winNotifyScreenshotCaption":
      "Windows 11 — System → Notifications → Set priority notifications",
    "guide.winNotifyImportant":
      "Only host connection failures (e.g. port not open) use Important priority and a short chime on the asobby side. Other notifications use the default style.",
    "guide.winNotifyWin10":
      "On Windows 10, open Settings → System → Notifications → Focus assist and turn off rules that auto-enable during games or fullscreen apps, and allow notifications from asobby.",
    "guide.winNotifyTray":
      "Use the tray menu item Notification sound to toggle the chime on host connection failures (on by default).",
    "guide.sectionAv": "Antivirus warnings",
    "guide.avIntro":
      "The asobby client (a PyInstaller-built exe) is unsigned, so Windows Defender and others may flag it as a false positive. A warning can appear again whenever a new exe is published, but asobby does not contain malware.",
    "guide.avOpenSource":
      "Source code is public at https://github.com/stanak/asobby . Compare with Actions build logs or build it yourself if unsure.",
    "guide.avAllowTitle": "Allow in Windows Defender",
    "guide.avAllowStep1":
      "Open Virus & threat protection → Protection history and select the asobby detection.",
    "guide.avAllowStep2":
      "Choose Actions → Allow on device.",
    "guide.avAllowStep3":
      "Download the exe again from GitHub Releases and run it.",
    "guide.avReport":
      "Report a false positive to Microsoft at https://www.microsoft.com/en-us/wdsi/filesubmission ; whitelisting may improve after definition updates.",
    "guide.sectionNotify": "Common notifications",
    "guide.notifyPostFailed":
      "Post failed — the server could not verify your host. Check port forwarding, Autopunch, and firewall.",
    "guide.notifyLogin":
      "Login required — sign in with Discord before auto-posting and other features.",
    "guide.notifyCasual":
      "Casual match — even on ranked posts, stats stay casual if the opponent is not logged in or ranks do not match (unless Challenge upper rank is on).",
    "guide.notifyUpdate":
      "Update available — a new exe is published; download it from the tray menu.",
    "guide.notifyHighPing":
      "High ping warning — toast on your PC when the connected guest’s ping is at or above your threshold (e.g. “High ping from Alice: 80ms (warn threshold: 60ms+)”).",
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
    for (const el of document.querySelectorAll("[data-i18n-alt]")) {
      const key = el.getAttribute("data-i18n-alt");
      if (key) el.alt = t(key);
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

  const PRESENCE_HEARTBEAT_MS = 45000;
  const PRESENCE_COUNT_MS = 30000;

  /** @param {number} n */
  function renderOnlineCount(n) {
    const el = document.getElementById("online-count");
    if (!el) return;
    el.textContent = t("common.onlineCount", { n });
    el.title = t("common.onlineCountTitle");
    el.hidden = false;
  }

  async function sendPresenceHeartbeat() {
    try {
      const res = await fetch("/presence/heartbeat", {
        method: "POST",
        credentials: "same-origin",
      });
      if (!res.ok) return;
      const data = await res.json();
      if (data.ok && typeof data.count === "number") {
        renderOnlineCount(data.count);
      }
    } catch (_) {}
  }

  async function refreshOnlineCount() {
    try {
      const res = await fetch("/presence/count");
      if (!res.ok) return;
      const data = await res.json();
      if (data.ok && typeof data.count === "number") {
        renderOnlineCount(data.count);
      }
    } catch (_) {}
  }

  function initOnlinePresence() {
    if (!document.getElementById("online-count")) return;
    sendPresenceHeartbeat();
    refreshOnlineCount();
    window.setInterval(sendPresenceHeartbeat, PRESENCE_HEARTBEAT_MS);
    window.setInterval(refreshOnlineCount, PRESENCE_COUNT_MS);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        sendPresenceHeartbeat();
        refreshOnlineCount();
      }
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
  window.initOnlinePresence = initOnlinePresence;
  window.tError = tError;
})();
