# asobby server

FastAPI ベースのロビーサーバー。募集の API に加えて、閲覧用 Web ページ（`GET /`）を配信する。

## 外部連携（募集一覧 API・Webhook）

管理者が `/admin` の「外部連携」から登録します。特定の Bot やサービスには依存せず、
任意の公開 HTTPS エンドポイントに JSON を送信できます。登録がなければ外部送信はありません。
管理権限は既存の `ASOBBY_ADMIN_USER_IDS`（Discord ID、カンマ区切り）を利用します。

- 登録・編集・停止/再開・削除・キー再発行・テスト通知・最終配信状況の確認に対応。
- 通知先なしの **API 専用連携** も作成可能。最大 20 連携。
- 連携ごとに API キーと署名シークレットを発行。表示は登録/再発行時のみ。API キーは既定で読み取り専用。
- 「カジュアル募集の登録を許可する」を有効にした連携だけ、募集登録 API も利用可能。既存のキーに自動で作成権限は付かない。
- 停止・削除すると API キーも利用不可。再発行すると旧 API キーは即時失効し、以後の通知は新しい署名シークレットで署名する。
- 対戦用 IP:port は既定で API に含めない。必要な連携だけ管理者が許可する。
  表示名・コメント・対戦状態・配信 URL などは取得可能なため、掲載先にも注意する。
- 通知先 URL は資格情報を含むことがあるため、管理一覧でもホスト名だけを表示。
  編集時に URL 欄を空にすると既存 URL を維持。「API のみ」を選択すると送信先を解除する。

### 募集一覧 API

`GET /api/v1/lobby` に `Authorization: Bearer <APIキー>` を付ける。
Discord のセッション・URL クエリに入れたキーは利用できない。
既定では募集の作成/変更/終了や管理操作はできない。管理者が作成を許可した場合に限り、
下記の登録 API が使える。既存 `/posts` のログイン要件は維持する。

```http
GET /api/v1/lobby HTTP/1.1
Host: asobby.com
Authorization: Bearer <APIキー>
```

```json
{
  "schema_version": 1,
  "revision": "<表示内容のSHA-256>",
  "count": 1,
  "posts": [{
    "id": "<募集ID>",
    "owner_name": "プレイヤー",
    "post_type": "casual",
    "rank": "normal",
    "comment": "対戦募集",
    "status": "waiting"
  }],
  "lobby_url": "https://asobby.com/"
}
```

`posts` の例は主要項目のみ。状態は `waiting`（ホスト待ち）・`connecting`（接続/準備中）・
`playing`（対戦中）・`unknown`。作成時刻の新しい順。同定済みの対戦相手名や接続補助ツール、
到達性、国情報なども含む。ユーザー ID、投稿操作トークン、受信メッセージ、ハートビート時刻は含めない。
IP 提供を許可した連携にだけ `addr` を追加する。API は期限切れの募集を除外し、0 件は `posts: []` で返す。

レスポンスの `ETag` を次回の `If-None-Match` に指定できる（変化なしは 304）。
`revision` は内容比較用の値であり、時系列順の番号ではない。IP の提供権限が異なる場合は値も異なる。
認証後に条件付き取得を判定し、失効済みキーには 304 を返さない。
1 キーあたり 60 回/分まで。超過は 429 と `Retry-After` を返す。

### Webhook の形式と配送

登録・再開・再起動時、および一覧に表示する内容が変化したときに通知する。
5 秒ごとのハートビートだけでは通知しない。短時間の変化はまとめ、1 送信先につき同時 1 件、
通常は 5 秒以上の間隔で送る。既存の募集処理から外部 HTTP 応答を待たない。

```json
{
  "schema_version": 1,
  "id": "<通知ID>",
  "type": "lobby.changed",
  "occurred_at": "2026-09-07T12:00:00+00:00",
  "source": "https://asobby.com",
  "data": {
    "count": 0,
    "snapshot_url": "https://asobby.com/api/v1/lobby"
  }
}
```

通知には募集内容や IP、API キーを載せない。受信側は設定済みの一覧 API から最新状態を取得する。
テスト通知も同じ形式で、トップレベルに `test: true` が付く。
通常の JSON Webhook であり、Discord Incoming Webhook 専用のメッセージ形式ではない。

- 2xx を配送成功とする。これは受信側が処理を受け付けたことを表し、その先の Discord 表示成功までは保証しない。
- エラーは 5 秒から最大 300 秒まで間隔を延ばして再試行。`Retry-After` は最大 1 時間まで尊重する。
- 再試行では同じ通知 ID を使用する。待機中に一覧が変われば、最新状態の通知に置き換える。
- 送信は 15 秒で打ち切る。リダイレクトは追わず、プロキシ環境変数は利用しない。
  登録時と毎送信時に DNS を確認し、公開 IP に固定して TLS 検証付きで接続する。
  localhost、プライベート/リンクローカル IP などへの送信は拒否する。

この機能は **最新の募集一覧を同期するための通知**。全変更の履歴・厳密な一度だけの配送は保証しない。
受信側は初回と数分ごとにも一覧を取得し、通知の重複・一時停止から復旧できるようにする。
同じ Discord メッセージの更新は定期実行分も含めて直列化し、内容が変わった場合だけ編集する。
API 取得失敗を「0 件」と扱わず、古い情報であることを表示するか、再試行する。

### 署名検証

以下のヘッダーを付与する。

| ヘッダー | 値 |
| --- | --- |
| `X-Asobby-Event` | `lobby.changed` |
| `X-Asobby-Delivery` | JSON 内の通知 ID と同じ値 |
| `X-Asobby-Timestamp` | 送信時刻（Unix 秒） |
| `X-Asobby-Signature` | `sha256=<HMACの16進表記>` |

HMAC-SHA256 の鍵は連携ごとの署名シークレット。対象バイト列は
`timestamp + "." + HTTPリクエストの生body`（UTF-8）。JSON を再シリアライズせずに検証する。
受信側では時刻差（例: 5 分以内）と定時間比較で署名を確認し、同じ通知 ID の再送を安全に扱う。
署名の時刻は再試行のたびに更新される。TLS に加えて送信元を確認するための署名であり、API キーとは別物。

### dpalette との接続例

1. dpalette で Webhook トリガーのワークフローを作り、公開後の受信用 URL を取得する。
2. asobby の `/admin` で、その URL を通知先として登録。発行した API キーを dpalette の Secret に保存する。
3. `trigger.webhook` → `http.request`（一覧取得）→ 一覧を整形 → `discord.message.sync` を接続する。
   HTTP ヘッダー例: `Authorization: Bearer {{secret.asobby_api_key}}`。
   一覧の取得先には自分で設定した asobby の URL を使う。
4. 一覧 0 件のときも「現在募集中のホストはいません」と同期する。同じ `message_key` を使い続ける。
5. 初回実行と定期的な再同期を設定し、asobby 管理画面からテスト通知を送る。

dpalette の汎用 Webhook は受信用 URL のトークンで保護される。
上記 HMAC の自動検証を備えていることは前提にしていない。署名検証を必須にする場合は受信側に追加する。
この機能の導入だけでは、dpalette のワークフロー作成や Discord への投稿は行われない。

### クライアント不要のカジュアル募集登録（UDP で維持）

管理画面の連携設定で **「カジュアル募集の登録を許可する」** を有効にする。
`POST /api/v1/posts` に同じ Bearer API キーを付け、次の JSON を送信する。
`addr` のプレースホルダーは、募集する本人の公開 IPv4 に置き換える。

```json
{
  "request_id": "recruitment-message-123",
  "external_user_id": "player-456",
  "owner_name": "プレイヤー",
  "addr": "<本人の公開IPv4>:10800",
  "comment": "対戦募集",
  "stream_url": ""
}
```

- 必須: `request_id`・`external_user_id`（各128文字以内）、`owner_name`（80文字以内）、`addr`。
- 任意: `comment`（200文字以内）、`stream_url`（300文字以内。YouTube/Twitch/ニコニコのみ）。
- `request_id` は Discord の募集操作・メッセージなどを一意に識別する ID。
  確認中・掲載中に同じ ID と内容を再送しても二重登録しない。内容が違えば409。
  終了した登録の再送は新規登録になるため、202受信後に404になった場合は自動で再登録しない。
- `external_user_id` は**連携内の投稿者 ID**。連携サービスが認証済みの投稿者から設定する。
  asobby の既存 Discord アカウント、ランク、IP 履歴には結び付けない。
  連携側で投稿者を確認し、本人のホストであることと、IP を asobby の一覧へ掲載する同意を得る。
  UDP 応答はホストの所有権の証明ではない。ユーザー入力だけで他人の ID や IP を代理登録しない。
- カジュアル固定。`post_type`・`rank`・`net_status`・`giuroll`・`autopunch` などの自己申告フィールドは受け付けない。
- 確認中を含めて、全体50件・1連携10件・連携内の1投稿者1件。同一 IP:port の重複掲載は不可。
  新規受付は1連携6件/分。既存の API 全体の60リクエスト/分制限も適用する。
- 接続先は公開 IPv4 の数値表記のみ。内部・予約・マルチキャストなどのアドレス、ホスト名は不可。
  UDP 送信時と復元時にも検証する。

受付は UDP 検証を待たず、永続化後に **202 Accepted** を返す。

```json
{
  "id": "<募集ID>",
  "state": "checking",
  "status_url": "https://asobby.com/api/v1/posts/<募集ID>",
  "post": null
}
```

`GET /api/v1/posts/{id}` に同じ連携のキーを付けると状態を取得できる。
確認できた後は `state: "active"` と通常の募集形式の `post` を返す。
`post` の IP は登録した連携には返すが、一覧 API の `include_address` 権限とは独立。
未確認の募集はロビー・一覧 API・募集通知には出さない。別の連携の登録や終了済み登録は404。
定期確認する場合は15秒以上の間隔にし、429の `Retry-After` に従う。

表示は通常のカジュアル募集と同一で、外部募集の区分・バッジは付けない。
asobby のランクが確認できないのでランク欄は空欄。
ホスト側クライアントがないため、定型メッセージと高 Ping 警告は無効。
`supports_messages: false` は受信機能の有無を表す。

#### 生存確認と自動検出

- クライアントの定期更新は不要。**手動終了 API・固定の掲載期限は設けない。**
  応答が続く限り掲載を維持し、確認済みの未応答が3回連続、かつ最初の失敗から30秒以上で掲載を終了する。
  確認間隔の目標は15秒。全体を最低1秒間隔で分散し、既存プローブと共有する固定 UDP ポートで直列実行するため、
  件数や応答時間によって実際の間隔・終了検知は長くなる。
- 検証されたゲーム応答、または Giuroll 専用応答が生存の根拠。単なる AutoPunch 登録や任意の UDP 返信では掲載しない。
  未応答中は到達性警告を表示する。ローカルソケット異常・仲介サーバーの障害は判定不能とし、終了用の失敗回数に数えない。
- `0x07` の理由1は待機相当、理由0（観戦無効）は状態不明、`0x08`（転送）／`0x06`（観戦初期化応答）は接続中として扱う。
  接続中と実際の試合中は UDP のこの情報だけでは区別しない。対戦相手の IP を追跡先に追加したり、名前を推測したりしない。
- Giuroll は `6C 00` に対する `6D 61` を検出する。未検出時は60秒ごと、検出済みなら生存確認を兼ねて毎回確認する。
  `giuroll: false` は**未検出**であり未使用の保証ではない。応答を一度検出した登録では、その後の無応答だけで GIU 表示を消さない。
  この応答の実装は [本家](https://github.com/Giufin/giuroll/blob/7eaf02ea6e38d40fd58bce95383880908101384b/src/lib.rs#L1341)
  と [Hagb版](https://github.com/Hagb/giuroll-hagb/blob/53535bdcfcbc3c7e8fef2547bf21ea7c598580e2/src/lib.rs#L2536)で確認している。
  バージョン・待機状態・AutoPunch併用時の実機互換性は別途確認が必要。
- AutoPunch は直結確認の**後**に仲介サーバーへ問い合わせ、取得した NAT ポートへ同じソケットから確認する。
  AP経由と判定済みの登録は、穴あけ後の返信を「AP不要」と誤認しないよう AP経路を維持する。
  仲介サーバーの返信元・対象 IP・ポート、ゲームの返信元 IP:port を検証し、別ホストをプローブしない。
  AP表示は検証した経路を示すもので、全対戦者からの接続可否やツールの起動状況を保証するものではない。
- 通常のクライアント募集は引き続き既存のハートビートで維持する。サーバー監視の募集は Redis にも固定 TTL を付けず保存し、
  再起動後に再確認する。起動していなかった時間を失敗回数には数えない。
- 管理者が作成権限を外すか連携を停止・削除した場合は、その連携の登録を次の監視処理で取り下げる。
  キー再発行だけなら登録は維持する。連携設定の読込失敗では一括削除しない。
- `ASOBBY_HOSTCHECK=off` では登録 API は503。監視が使えない状態を生存確認の成功とは扱わない。

dpalette では、認証済み Discord コマンド／フォーム → `http.request`（登録）を追加する。
一覧表示は既存の `lobby.changed` → 一覧 API → `discord.message.sync` のままでよい。
asobby の API キーを Discord メッセージやブラウザへ公開しない。

### 永続化と運用

既存の保存方式に合わせ、ローカルなら `ASOBBY_STORE_DIR/integrations.json`（権限 0600）、
Redis 利用時は `asobby:integrations:v1` に設定と最終配送状況を保存する。
API キーはハッシュのみ保存。送信 URL と署名シークレットは配信に必要なため保存する。
保存先・バックアップは秘密情報として管理する。

再起動時は保存済み連携を読み込み、復元された最新一覧の通知を送り直す。
設定を読めない場合は連携機能を停止し、壊れた設定を空の設定で上書きしない。
`ASOBBY_STORE=memory` では永続化しない。現在のロビーと同様、**単一アプリケーションプロセス**が前提。
複数 worker/複数インスタンスから同時に配信する構成は未対応。

## API 概要 (v0.2)

| Method/Path | 説明 |
| --- | --- |
| `GET /` | 閲覧用 Web ページ（SSE でリアルタイム更新） |
| `GET /stats` | 戦績閲覧用 Web ページ |
| `GET /replays` | リプレイ検索 Web ページ |
| `GET /replays/search` | リプレイ付き対戦の検索（公開・ログイン不要） |
| `GET /replays/players` | リプレイ検索用プレイヤー名候補（`q` / `limit` クエリ、公開・ログイン不要） |
| `GET /stats/me` | ログインユーザーの戦績集計（JSON） |
| `GET /stats/me/matches` | ログインユーザーの対戦一覧（`since` / `limit` クエリ、played_at 昇順） |
| `GET /replays/{match_id}` | リプレイ (.rep) をダウンロード（公開・ログイン不要） |
| `POST /matches/sync` | クライアントのローカル戦績を一括登録（最大 500 件） |
| `GET /myip` | クライアントのグローバル IP を返す |
| `GET /posts` | 募集一覧（Discord ログイン必須、閲覧用フィールドのみ） |
| `POST /posts` | 募集の新規作成（Discord ログイン必須）。レスポンスで `owner_token` を発行 |
| `POST /posts/update` | 募集の更新。`id` + `owner_token` が必須。応答に `messages`（閲覧者からの未読定型メッセージ）を含み、返却後キューはクリア |
| `POST /posts/{id}/message` | Web ロビー閲覧者がホストへ定型メッセージを送る（Discord ログイン必須） |
| `POST /posts/reply` | ホストがリクエストメッセージへ承諾/拒否を返す。`id` + `owner_token` + `message_id` + `reply` (`accept`/`decline`) |
| `POST /posts/close` | 募集の削除。`id` + `owner_token` が必須 |
| `POST /posts/result` | 対戦勝敗の報告。`id` + `owner_token` + `winner` (`host`/`guest`/`draw`) |
| `POST /matches/report` | ゲスト側クライアントからの対戦結果補完報告。`Authorization: Bearer` 必須 |
| `POST /replays/upload` | リプレイ (.rep) のアップロード。`Authorization: Bearer` 必須。body は生バイト |
| `POST /import/tensokukan` | 天則観 (tsk) 戦績 DB (.db) のインポート。`Authorization: Bearer` またはクッキー必須。body は生バイト |
| `GET /sse/posts` | SSE（Discord ログイン必須）。接続直後に `snapshot`、以後 `upsert` / `close` / `message_reply` |
| `POST /auth/device` | Discord ログイン開始。`device_code` と `verify_url` を返す |
| `GET /auth/discord/start` | (ブラウザ用) Discord の認可画面へリダイレクト |
| `GET /auth/discord/web` | (Web 閲覧用) Discord ログイン。完了後クッキーセッションを発行 |
| `GET /auth/discord/callback` | (ブラウザ用) OAuth コールバック。完了ページを表示 |
| `POST /auth/device/poll` | ログイン完了ポーリング。完了時に `session_token` を返す |
| `GET /auth/client/handoff?port=N` | (ブラウザ用) Web クッキーセッションをクライアントへ引き渡す。ワンタイムコード付きで `http://127.0.0.1:N/auth` へリダイレクト |
| `POST /auth/client/exchange` | ワンタイムコードを `session_token` に交換 |
| `GET /auth/me` | `Authorization: Bearer` またはクッキーのセッション検証・ユーザー情報 |
| `GET /auth/logout` | Web クッキーセッションを削除して `/` へリダイレクト |

- 投稿の更新・削除には作成時に発行される `owner_token` が必要（他人の投稿は操作不可）
- 投稿は TTL 20 秒。クライアントは 5 秒間隔のハートビート（update）で維持する
- 新規作成時とアドレス変更時にホスト到達性を検証する。通常ホストは UDP soku echo で直接プローブする
- autopunch ホストは AutoPunch リレー経由で検証する（リレー lookup → hole punch → soku echo）。リレー先は環境変数 `ASOBBY_AUTOPUNCH_RELAY` で変更可能（既定 `delthas.fr:14763`）。リレー自体に到達できない場合は検証をスキップする（fail-open）
- 作成レート制限: IP あたり 2 秒間隔・同時 2 件まで
- 旧 `POST /posts/upsert` は 410 Gone を返す（旧クライアントへの更新案内）
- 募集の `addr` IP から国コードを推定し、Web ロビーのアドレス横に国旗を表示する（マウスオーバーで国名）。MaxMind GeoLite2-Country を使用

### GeoIP（国旗表示）

起動時に `GeoLite2-Country.mmdb` を読み込む。ファイルが無い場合は
`GEOIP_MAXMIND_ACCOUNT_ID` / `GEOIP_MAXMIND_LICENSE_KEY` が設定されていれば
MaxMind から自動ダウンロードする（[無料アカウント](https://www.maxmind.com/en/geolite2/signup) が必要）。

```sh
fly secrets set \
  GEOIP_MAXMIND_ACCOUNT_ID=<Account ID> \
  GEOIP_MAXMIND_LICENSE_KEY=<License key>
```

- 任意: `GEOIP_COUNTRY_DB` で DB ファイルのパスを上書き（既定 `app/GeoLite2-Country.mmdb`）
- DB 未設定時は国旗は表示されず、他機能は通常動作
- GeoLite2 利用時は MaxMind への帰属表示が必要（[利用規約](https://dev.maxmind.com/geoip/geolite2-free-geolocation-data)）

### Web ロビーからホストへのメッセージ

Discord ログイン済みの Web ロビー閲覧者が、募集中のホストへ定型メッセージを送れる。
ホストの asobby クライアントは 5 秒間隔のハートビート (`POST /posts/update`) の応答
`messages` 配列で受け取り、トースト通知する。

メッセージ種別:

| type | 内容 | 送信条件 |
| --- | --- | --- |
| `giuroll_request` | Giuroll を使ってほしい | 対象投稿の `giuroll` が false のときのみ |
| `casual_invite` | カジュアル対戦のお誘い | 対象投稿の `post_type` が `ranked` のときのみ |

- `POST /posts/{id}/message` は Discord セッション必須（未ログイン 401）
- 自分の投稿へは 400。条件不一致は 409
- 同一送信者・同一投稿への再送は 60 秒クールダウン（429、`Retry-After` 付き）
- 未読キューは投稿あたり最大 20 件（古いものから破棄）

返信 (`giuroll_request` / `casual_invite` のみ):

- ホストクライアントは `POST /posts/reply` で `accept` または `decline` を返す
- 送信時に付与された `message_id`（ハートビート応答 `messages[].id`）が必要
- 返信は SSE `message_reply` イベントで送信者の Web ロビーページへ配信される
- 同一 `message_id` への再返信は 409

## 永続化 (PostgreSQL)

ユーザー・戦績 (matches)・リプレイ (replays) を PostgreSQL に永続化する。

- スタック: SQLAlchemy 2.0 (async) + asyncpg + Alembic
- スキーマは起動時に自動でマイグレーションされる（`alembic upgrade head` 相当）
- `DATABASE_URL` 未設定なら DB 機能（Discord ログイン含む）だけ無効になり、投稿は通常動作

### 募集・ロビーチャット (ローカルファイル)

募集投稿とロビーチャットは、デプロイ・再起動後も復元できるようローカルファイルに保存する（既定）。
外部 Redis (Upstash) は **不要**。

- 保存先: 環境変数 `ASOBBY_STORE_DIR`（未設定時は fly.io 上 `/data/asobby`、ローカルは `server/data/asobby`）
- fly.io では `[mounts]` で `/data` にボリュームをマウントする（初回: `fly volumes create asobby_store --region nrt --size 1`）
- テスト用に永続化を切る: `ASOBBY_STORE=memory`
- 任意で Upstash Redis を使う場合は `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` を設定（`pip install upstash-redis` が必要）
- 閲覧人数 (presence) は Redis 未使用時インメモリ（単一プロセス向け）

### テーブル概要

- `users`: Discord ID 主キー、表示名、`token_version`（インクリメントで発行済みセッションを失効）、`last_ip`（ログイン時・認証リクエスト時に自動更新。echo パケットで得た対戦相手 IP との照合用のため **IPv4 のみ保存**。IPv6 からのリクエストでは既存値を保持）、`client_version`（クライアントが `X-Asobby-Client-Version` ヘッダーで送った版。認証付きリクエストのたびに更新）
- `matches`: ホスト/ゲストのユーザー ID・IP・勝敗。戦績機能用に schema のみ先行準備
- `replays`: match に紐づくリプレイファイル（bytea、100KB 程度想定）

### Neon のセットアップ（推奨）

1. [Neon](https://neon.tech) でプロジェクト作成（無料枠で十分）
2. 接続文字列（`postgresql://...?sslmode=require`）をコピー
3. `fly secrets set DATABASE_URL='postgresql://...'`

`sslmode=require` / `channel_binding=require` クエリは自動で asyncpg 用に変換される。
ローカル開発では `DATABASE_URL='sqlite+aiosqlite:///./dev.db'` でも動作する。

### マイグレーションの追加（開発時）

```sh
cd app
DATABASE_URL=... ../bin/alembic revision --autogenerate -m "add xxx"
```

## Discord ログイン（募集投稿に必須）

募集の投稿には Discord ログインが必須（対戦相手の同定のため）。
ログインすると投稿に Discord の表示名（`owner_name`）が載り、
ロビーの User 列に表示される。

**ロビーの募集一覧閲覧（`GET /posts`, `GET /sse/posts`）は Discord ログイン必須。**
外部サービスからは、管理者が発行した連携キーで `GET /api/v1/lobby` を利用する。
Web ページのログインはクッキーセッション（`asobby_session`、有効期限 30 日）。

クライアントのログインはブラウザセッション引き継ぎ（ハンドオフ）方式:
クライアントが 127.0.0.1 の空きポートで待ち受け、ブラウザで
`GET /auth/client/handoff?port=N` を開く → Web 側でログイン済みなら
ワンタイムコード付きで即 localhost へリダイレクト（未ログインなら Discord OAuth を
経由してから戻る）→ クライアントが `POST /auth/client/exchange` で
`session_token` を受領。**Web ロビーでログイン済みならクライアント側の操作は不要**。
募集検知時に未ログインだった場合、クライアントはこのフローを一度だけ自動実行する。
（旧デバイスコード方式 `POST /auth/device` / `poll` も互換のため残している）
セッションは HMAC 署名付きトークン（有効期限 30 日）で、`users.token_version` と
突合して検証される（DB 側で version を上げれば個別に失効可能）。
以後 `POST /posts` に `Authorization: Bearer <session_token>` を付ける。
ログイン完了時と認証付きリクエストのたびにユーザーの `last_ip` が最新化される。
**Discord ログインには `DATABASE_URL` の設定も必要。**

### 対戦相手の同定

10 秒間隔で各募集ホストへ soku echo (UDP) をプローブする。対戦中 (0x08) の
応答に含まれるゲスト IP:port を取得し、`users.last_ip` と照合して Discord
ユーザーを同定する。同定できた場合はロビーの User 列に `vs <ゲスト名>` が
表示され、ホストがログイン済みなら `matches` テーブルに対戦記録が残る。
ホストのクライアントが KO 検出時に `POST /posts/result` で勝敗を報告し、
`matches.winner` に記録される。
ホストが asobby を使っていない場合でも、ログイン済みゲストは自分のクライアントが
`POST /matches/report` で戦績を補完できる（ランクマ扱いにはならない）。
ホスト・ゲスト双方が asobby を導入している場合、KO 検出はほぼ同時に届くため、
直近 30 秒以内の重複報告は排除する（ゲスト報告はスキップ、ホスト報告はゲスト報告行を
昇格して上書きする）。
`ASOBBY_HOSTCHECK=off` の場合はプローブ自体が無効になる。

### 戦績

KO 報告 (`POST /posts/result`) には使用キャラとプロファイル名も含まれる。
ホスト非導入時はゲスト報告 (`POST /matches/report`) でも戦績が残るが、
`ranked=False` でランク評価・TrueSkill 更新の対象外となる。
ログインユーザーは `/stats` で総合勝率、直近 30 / 50 / 100 戦の勝率、
自キャラ別・対戦相手キャラ別・対戦相手プロファイル別の勝率を閲覧できる。
天則観 (AlwaysRecordable/tsk) の SQLite 戦績 DB (.db) を `POST /import/tensokukan` で
取り込める。天則観の p1 (自分) は asobby 側のホスト欄に格納され、カジュアル扱い
(`ranked=False`) となる。同じ DB を再アップロードしても決定的 ID により重複しない。
asobby クライアントはローカル SQLite に戦績を保持し、`POST /matches/sync` で
サーバー未記録分を一括同期できる（決定的 ID により再送安全）。同期済みの対戦で
リプレイが添付されていれば `GET /replays/{match_id}` から誰でもダウンロードできる
（未ログインでも可。存在しない match / リプレイ未添付は 404）。
`GET /replays` の Web ページからプレイヤー名・キャラ・日付でリプレイを検索できる。

### ランクマッチ

- 開始ランクのデフォルトは N (`normal`)。ログイン後、**初回のみ** E / N / Ex / H / L から開始ランクを選択できる（Ph は選択不可）。選択後、またはランクマ対戦を 1 戦記録した時点でロックされ、以降は昇降格のみ
- ランク (E → N → Ex → H → L → Ph) はシステムが決定する。初回選択を行わなければ N からスタート
- 昇降格は**現ランクで行ったランクマ対戦の直近 50 戦の勝率**で判定（50 戦揃ってから）。ランク変更時に窓はリセット (`rank_changed_at` 以降のみ集計)
  - E: 勝率 >= 50% で N へ昇格（降格なし）
  - N: 降格なし。>= 50% で Ex へ
  - Ex: < 20% で N へ降格。>= 60% で H へ
  - H: < 20% で Ex へ降格。>= 60% で L へ
  - L: < 20% で H へ降格。>= 70% で Ph へ
  - Ph: 昇降格なし。Ph 同士のランクマ対戦で TrueSkill レートを更新（表示レート = mu - 3σ）
- 募集は「カジュアル」か「ランクマ」の 2 種類。ランクマ募集は**同じランクのログインユーザーにのみ** Web ロビーで表示される
- 1 回のゲスト接続セッションでは**最初の 3 戦だけ**ランクマ扱い（以降はカジュアル同様に記録のみ）

### リプレイ収集

- asobby クライアントはネット対戦終了後、非想天則が保存した `.rep` を自動アップロードする
- 1 対戦 (match) に対して 1 ファイルのみ保存される（両側クライアントがアップロードしても先着 1 件のみ）
- 同一 `.rep` 内容 (SHA-256) は 1 件のみ保存される（別 match への誤再利用を拒否）
- **保存条件**: 対戦双方が Discord ログイン済み asobby ユーザーとして同定されていること。どちらか一方でも非導入・未同定なら保存しない (`reason: opponent_not_asobby`)。リプレイ保存拒否設定が有効な参加者がいる場合も保存しない (`reason: refused`)
- **ダウンロードは公開**（`GET /replays/{match_id}` はログイン不要。リプレイ未添付・存在しない match は 404）
- **`GET /replays` でリプレイ検索ページ**を提供。プレイヤー名（プロファイル / Discord 表示名）、キャラ、日付範囲、並び順（日付・ランク）で絞り込み可能
- ファイル名形式: `{日時JST}_{host_profile}-{host_char}_vs_{guest_profile}-{guest_char}_{result}.rep`
  - `result`: ホスト勝ち `ox`、ゲスト勝ち `xo`、引き分け `xx`
  - プロファイル名はファイル名に使えない文字を `_` に置換
- アップロード上限 300KB。直近 15 分以内の未リプレイ対戦に紐付く

### 設定手順

1. [Discord Developer Portal](https://discord.com/developers/applications) でアプリを作成
2. OAuth2 → Redirects に `https://asobby.com/auth/discord/callback` を登録
3. 環境変数を設定（fly なら `fly secrets set`）:

```sh
fly secrets set \
  ASOBBY_DISCORD_CLIENT_ID=<Client ID> \
  ASOBBY_DISCORD_CLIENT_SECRET=<Client Secret> \
  ASOBBY_SESSION_SECRET=$(openssl rand -base64 32)
```

- `ASOBBY_BASE_URL` は既定で `https://asobby.com`。別ドメインで動かす場合は上書きし、Discord 側の Redirect も合わせる
- `ASOBBY_SESSION_SECRET` 未設定時は起動ごとにランダム生成され、再起動で全セッションが失効するので本番では必ず設定する
- client id / secret が未設定なら `/auth/*` は 503 を返す（ログイン機能だけ無効になり、他は通常動作）

### 意見・報告フォーム (`/feedback`)

Discord ログイン済みユーザー向け。カテゴリは不具合 / 要望 / その他。本文上限 2000 文字、同一ユーザーは 5 分に 1 回まで。

- 投稿は `/data/asobby/feedback.jsonl`（または Redis）に保存。管理者は `/admin` で一覧
- 通知: `ASOBBY_FEEDBACK_WEBHOOK_URL` に Discord Webhook URL を設定すると投稿時に embed 通知（未設定なら保存のみ）

```sh
fly secrets set ASOBBY_FEEDBACK_WEBHOOK_URL='https://discord.com/api/webhooks/...'
```

管理者 Discord ID は `ASOBBY_ADMIN_USER_IDS`（カンマ区切り）で指定する。

## カスタムドメイン (asobby.com)

```sh
fly certs add asobby.com
fly certs show asobby.com   # 表示される A/AAAA レコードを DNS に登録して検証
```

専用 IPv4 を割り当て済みなら A レコードはその IP を指す。証明書は Let's Encrypt で自動更新される。

## デプロイ (fly.io)

設定は `app/fly.toml`。東京リージョン（nrt）・**performance-1x（専用 CPU）/ 2GB**・マシン1台構成。

```sh
cd app
fly launch --copy-config --no-deploy   # 初回のみ: アプリ作成
fly ips allocate-v4                    # 初回のみ: 専用 IPv4 ($2/月、下記参照)
fly deploy --ha=false                  # デプロイ（マシン1台）
```

更新は `fly deploy` だけでよい。ログは `fly logs`。

### 重複戦績 (guest/sync) の一括整理

`tools/merge_duplicate_matches.py` が同一プロファイル・勝敗・時刻近傍の重複行を検出し、
ランクマ付き / リプレイ付き / source=host を優先して 1 行にマージする。デフォルトは dry-run。

```sh
cd app
# 本番 VM 内 (DATABASE_URL は fly secrets から注入済み)
fly ssh console -a asobby -C 'sh -c "cd /app && PYTHONPATH=/app python tools/merge_duplicate_matches.py"'
fly ssh console -a asobby -C 'sh -c "cd /app && PYTHONPATH=/app python tools/merge_duplicate_matches.py --apply"'
```

VM サイズ変更は `fly.toml` の `[[vm]] size` を編集して再デプロイする。

注意:

- 投稿はインメモリ保持のため**マシンは必ず1台**で運用する（`--ha=false` を忘れると2台作られて一覧が分裂する。`fly scale count 1` で修正可能）
- デプロイ後の URL は `https://<app名>.fly.dev`。クライアントの `asobby_config.json` の `server.api_base` をこの URL に変更すること
- SSE 常時接続を維持するため `auto_stop_machines = "off"` にしてある（無料枠の自動停止とは相性が悪い構成なので注意）

### ホスト到達性検証と UDP について

fly.io は任意ポートへの外向き UDP を遮断するため、そのままではホスト到達性検証
（soku echo プローブ）が失敗して投稿が全て 409 になる。

対策として fly.toml では以下を設定している:

- `[[services]]` で UDP ポート 10800 をサービスとして公開
- プローブの送信元を `fly-global-services:10800` に固定
  （`ASOBBY_PROBE_BIND_HOST` / `ASOBBY_PROBE_BIND_PORT`）

この経路は**専用 IPv4（$2/月）が必要**。`fly ips allocate-v4` で割り当てる。
費用をかけたくない場合は、代わりに `[env]` に `ASOBBY_HOSTCHECK = 'off'` を
設定すれば検証なしで動作する（ポート未開放の募集も掲載される）。

#### ゲスト検出プローブの間隔

fly.io では送信元 UDP ポートを 1 つ固定するため、**同時並列プローブはできない**
（ソケット競合と返信の取り違え防止）。代わりにサーバーは **ラウンドロビン** で
1 件ずつ分散プローブし、全募集を約 10 秒で 1 周する。

| 環境変数 | 既定 | 説明 |
| --- | --- | --- |
| `ASOBBY_GUEST_PROBE_ROUND_SEC` | `10` | 全募集を 1 周する目標秒数 |
| `ASOBBY_GUEST_PROBE_MIN_TICK_SEC` | `0.4` | tick 間隔の下限（秒） |
| `ASOBBY_GUEST_PROBE_TIMEOUT_SEC` | `0.35` | 定期プローブ 1 回の UDP タイムアウト |

募集が N 件のとき tick 間隔は `max(MIN_TICK, ROUND/N)` になる。
例: 20 件 → 0.5 秒ごとに 1 件、10 秒で全件 1 周。
将来、fly で複数 UDP ポートを公開できれば並列化も可能。

## 起動 (Podman)

```sh
podman build -t asobby-server ./app
podman run -d --name asobby_server -p 8000:8000 --restart unless-stopped asobby-server
```

環境変数が必要な場合は `podman run` に `--env-file .env` を追加する。

ホスト再起動後もコンテナを自動起動させる場合（rootless）:

```sh
systemctl --user enable --now podman-restart.service
loginctl enable-linger $USER
```

## ログ確認

```sh
podman logs -f asobby_server
```

## 停止

```sh
podman stop asobby_server
```

## 更新

```sh
git pull
podman build -t asobby-server ./app
podman rm -f asobby_server
podman run -d --name asobby_server -p 8000:8000 --restart unless-stopped asobby-server
```
