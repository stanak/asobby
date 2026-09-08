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
  表示名・投稿者の Discord ID・コメント・対戦状態・配信 URL などは取得可能なため、掲載先にも注意する。
- 通知先 URL は資格情報を含むことがあるため、管理一覧でもホスト名だけを表示。
  編集時に URL 欄を空にすると既存 URL を維持。「API のみ」を選択すると送信先を解除する。

### 募集一覧 API

`GET /api/v1/lobby` に `Authorization: Bearer <APIキー>` を付ける。
Discord のセッション・URL クエリに入れたキーは利用できない。
既定では募集の作成/変更/終了や管理操作はできない。管理者が作成を許可した場合に限り、
下記の登録 API が使える。既存 `/posts` のログイン要件は維持する。

以下は `GET /api/v1/lobby` の **HTTP 200 レスポンス本文**の仕様。
Webhook の受信本文や `GET /api/v1/posts/{id}` の登録状態とは別の形式である。
`/openapi.json` には現在、これらのレスポンスの全フィールドが定義されていないため、
連携先の出力スキーマには下記の JSON Schema を使う。

```http
GET /api/v1/lobby HTTP/1.1
Host: asobby.com
Authorization: Bearer <APIキー>
```

#### 完全なレスポンス例（IP 提供なし）

この例はフィールドを省略していない。ID・日時・表示内容は説明用で、`revision` はこの `posts` から計算した値。

<!-- api-doc:lobby-example -->
```json
{
  "schema_version": 1,
  "revision": "b413115ae31a76c4c245919ca9e7c51432f6e6eda2f7aa957eac4b9c752a21a4",
  "count": 1,
  "posts": [{
    "id": "0123456789abcdef0123456789abcdef",
    "owner_name": "プレイヤー",
    "discord_user_id": "123456789012345678",
    "discord_user_id_source": "oauth",
    "rank": "N",
    "post_type": "casual",
    "rating": null,
    "comment": "対戦募集",
    "created_at": 1788825600.0,
    "rank_status": "unset",
    "ranked_games": 0,
    "stream_url": "",
    "giuroll": false,
    "autopunch": false,
    "direct_reachable": true,
    "reachability_uncertain": false,
    "reachability_lost": false,
    "match_status": "",
    "guest_name": "",
    "ranked_active": false,
    "country_code": "",
    "country_name": "",
    "status": "waiting"
  }],
  "lobby_url": "https://asobby.com/"
}
```

`include_address: true` の連携では、各 `posts[]` に `"addr": "203.0.113.10:10800"` のような
文字列フィールドが1つ追加される（ここでの IP は説明用アドレス）。権限がなければ **キー自体を省略**し、
`null` や空文字でマスクする方式ではない。他の23フィールドは常に存在する。

#### JSON Schema（一覧 API の200本文専用）

Draft 2020-12。IP 提供あり・なしの両方を表現するため、`addr` だけは必須にしていない。
これは JSON Schema オブジェクトであり、上のレスポンス例そのものとは異なる。

<!-- api-doc:lobby-schema -->
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AsobbyLobbySnapshot",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "revision", "count", "posts", "lobby_url"],
  "properties": {
    "schema_version": {"type": "integer", "const": 1},
    "revision": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "count": {"type": "integer", "minimum": 0},
    "lobby_url": {"type": "string"},
    "posts": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "id", "owner_name", "rank", "post_type", "rating", "comment", "created_at",
          "rank_status", "ranked_games", "stream_url", "giuroll", "autopunch",
          "direct_reachable", "reachability_uncertain", "reachability_lost",
          "match_status", "guest_name", "ranked_active", "country_code", "country_name", "status",
          "discord_user_id", "discord_user_id_source"
        ],
        "properties": {
          "id": {"type": "string"},
          "owner_name": {"type": "string"},
          "discord_user_id": {"type": ["string", "null"]},
          "discord_user_id_source": {"type": ["string", "null"], "enum": ["oauth", "integration", null]},
          "rank": {"type": "string", "enum": ["", "E", "N", "Ex", "H", "L", "Ph"]},
          "post_type": {"type": "string", "enum": ["casual", "ranked"]},
          "rating": {"type": ["number", "null"]},
          "comment": {"type": "string"},
          "created_at": {"type": "number"},
          "rank_status": {"type": "string", "enum": ["unset", "initial", "provisional", "ranked", "unknown"]},
          "ranked_games": {"type": ["integer", "null"], "minimum": 0},
          "stream_url": {"type": "string"},
          "giuroll": {"type": "boolean"},
          "autopunch": {"type": "boolean"},
          "direct_reachable": {"type": "boolean"},
          "reachability_uncertain": {"type": "boolean"},
          "reachability_lost": {"type": "boolean"},
          "match_status": {"type": "string"},
          "guest_name": {"type": "string"},
          "ranked_active": {"type": "boolean"},
          "country_code": {"type": "string"},
          "country_name": {"type": "string"},
          "status": {"type": "string", "enum": ["waiting", "connecting", "playing", "unknown"]},
          "addr": {"type": "string"}
        }
      }
    }
  }
}
```

#### フィールドの意味・空値・省略条件

| フィールド | 型 | 内容 |
| --- | --- | --- |
| `schema_version` | integer | 現在は `1`。サーバー全体の API バージョン `v0.2` とは別 |
| `revision` | string | 返却する `posts` の内容から計算した64桁の SHA-256（小文字16進） |
| `count` | integer | `posts.length` と同じ。0件でも本文は返り、`posts: []` になる |
| `posts` | array of object | 公開中で期限切れでない募集。`created_at` 降順、同時刻は `id` 昇順 |
| `lobby_url` | string | ロビーの URL。以下の URL 例は既定の `ASOBBY_BASE_URL=https://asobby.com` の場合 |
| `posts[].id` | string | 募集 ID。操作権限を与えるトークンではない |
| `posts[].owner_name` | string | ホスト表示名。値がなければ `""` |
| `posts[].discord_user_id` | string または null | 投稿者の Discord ユーザー ID。数値に変換せず文字列として扱う。不明は `null`。IP 提供権限には依存しない |
| `posts[].discord_user_id_source` | string または null | `oauth`: asobby の Discord ログイン由来、`integration`: 登録元の連携が申告、ID 不明は `null` |
| `posts[].rank` | string | 表示用シンボル `E` / `N` / `Ex` / `H` / `L` / `Ph`。未紐付けの API 登録募集など、ランク不明の場合は `""` |
| `posts[].rank_status` | string | `unset` / `initial` / `provisional` / `ranked` / `unknown`。下記「ランクの設定・実績表示」を参照 |
| `posts[].ranked_games` | integer または null | 累計の確定ランク戦数。不明は `null`、確認済み0戦は `0` |
| `posts[].post_type` | string | `casual` または `ranked`。現在の対戦がランク戦として成立したかとは別 |
| `posts[].rating` | number または null | Ph の表示レート。それ以外・未紐付けは `null` |
| `posts[].comment` | string | 募集コメント。未設定は `""` |
| `posts[].created_at` | number | Unix 時刻（秒、小数可）。日時文字列・ミリ秒ではない |
| `posts[].stream_url` | string | 配信 URL。未設定は `""` |
| `posts[].giuroll` | boolean | Giuroll フラグ。UDP 監視募集の `false` は未検出であって不使用の保証ではない |
| `posts[].autopunch` | boolean | AutoPunch フラグ。ロビーの AP 表示条件はこれが `true` かつ `direct_reachable` が `false` |
| `posts[].direct_reachable` | boolean | サーバーから直接 UDP 到達を確認したか |
| `posts[].reachability_uncertain` | boolean | 到達性が不確実であることを示すフラグ |
| `posts[].reachability_lost` | boolean | 到達性の喪失を検知したフラグ。`status` とは独立 |
| `posts[].match_status` | string | クライアントの表示用文字列。空文字もある。状態の enum ではない |
| `posts[].guest_name` | string | 同定できた対戦相手の表示名。未同定は `""`。接続していないとは限らない |
| `posts[].ranked_active` | boolean | 現在の相手とのランク戦成立フラグ。個々の結果がランク戦として記録される保証ではない（連戦上限等は別判定） |
| `posts[].country_code` | string | 国コード（例 `JP`）。未取得は `""` |
| `posts[].country_name` | string | 国名。未取得は `""` |
| `posts[].status` | string | 下記の優先順で算出した状態 |
| `posts[].addr` | string、省略あり | IP:port。`include_address=true` の場合のみ存在。`null` にはならない |

`GET /api/v1/lobby` の `rank` はシンボルをそのまま表示できる。
対応は `easy → E`、`normal → N`、`ex → Ex`、`hard → H`、`luna → L`、`ph → Ph`。
内部の保存値・ランク判定・既存クライアント向け `/posts` / SSE のランクコードは変更しない。
`rank_status`・`ranked_games`・`rating` も変更しない。
この API は以前の `"normal"` 等の代わりに `"N"` 等を返すため、連携先でランクを条件分岐・変換していた場合は更新する。
`schema_version` は `1` のまま。`revision` / `ETag` は変換後の JSON から計算し、変更後の値に切り替わる。

`status` の算出順は、元の募集の `net_status=4` → `playing`、それ以外で
`guest_connected=true` または `net_status=2` → `connecting`、それ以外で `net_status=3` → `waiting`、
それ以外 → `unknown`。`guest_name` や `match_status` の空・非空から状態を推測しない。

一覧 API はこのスキーマにあるフィールドだけを返す。特に `net_status`、`updated_at`、
`owner_avatar`、`guest_avatar`、`guest_user_id`、`guest_connected`、`supports_messages`、
`ping_warn_enabled`、`ping_warn_ms`、`ping_warn_giuroll_ms` は返さない。
`owner_token`、内部監視情報、受信メッセージ、API キーも含めない。

`discord_user_id` は表示・紐付け用で、認証やランク判定には使わない。
API 登録で `discord_user_id` を省略した募集は `null`。`external_user_id` が数字だけでも、
Discord ID であると推測して転記しない。`integration` は asobby 自身が OAuth で本人確認した意味ではない。
`external_user_id` は連携固有の ID のため一覧 API には出さず、登録元だけが状態取得 API で確認できる。
今回の追加後も `schema_version` は `1`。厳密なスキーマ検証をする連携先は、この README の最新版へ更新する。

#### HTTP ステータス・キャッシュ・認証エラー

レスポンスの `ETag` を次回の `If-None-Match` に指定できる（変化なしは 304）。
200 の `ETag` は `revision` を二重引用符で囲んだ値。**304 の本文は空**なので JSON としてパースしない。
200 / 304 とも `Cache-Control: private, no-store` と `Vary: Authorization` を付ける。
`revision` は内容比較用の値であり、時系列順の番号ではない。
IP 提供の有無で返却する `posts` が変われば値も変わるが、0件の場合はどちらも `[]` なので同じ値になる。
認証後に条件付き取得を判定し、失効済みキーには 304 を返さない。
1 キーあたり 60 回/分まで。超過は 429 と `Retry-After` を返す。
この枠は `GET /api/v1/lobby` と登録 API の POST / GET で共有し、304 も消費する。

| HTTP | 条件・本文 |
| --- | --- |
| 200 | 上記の一覧 JSON |
| 304 | 変更なし。本文なし |
| 401 | キー未指定・不正・失効・連携停止。`{"detail":"invalid integration key"}`、`WWW-Authenticate: Bearer` |
| 429 | API 呼出上限。`{"detail":"too many requests"}`、`Retry-After: 60` |
| 503 | 連携ストレージ利用不可。`{"detail":"integration storage unavailable"}` |

HTTP エラーの本文は一覧スキーマではない。権限や入力によるエラーの優先順に依存する実装は避ける。

### Webhook の形式と配送

`lobby.changed` は連携の登録・再開・再起動時、および一覧に表示する内容が変化したときに通知する。
5 秒ごとのハートビートだけでは通知しない。短時間の変化はまとめ、1 送信先につき同時 1 件、
通常は 5 秒以上の間隔で送る。既存の募集処理から外部 HTTP 応答を待たない。

<!-- api-doc:webhook-example -->
```json
{
  "schema_version": 1,
  "id": "11111111111111111111111111111111",
  "type": "lobby.changed",
  "occurred_at": "2026-09-07T12:00:00+00:00",
  "source": "https://asobby.com",
  "data": {
    "count": 0,
    "snapshot_url": "https://asobby.com/api/v1/lobby"
  }
}
```

新しい募集が初めて公開されたときは、上記とは別に **`post.created`** を1募集につき1通知生成する。
通常のクライアント募集と API 登録募集の両方が対象。API 登録は POST 受付時ではなく、UDP 確認後の初回掲載時。
既存募集の更新・接続状態の変化・再起動による復元・連携の登録/再開では生成しない。
API の同一登録の再送でも二重通知せず、終了後に改めて登録された別 ID の募集は新規として扱う。

<!-- api-doc:webhook-created-example -->
```json
{
  "schema_version": 1,
  "id": "22222222222222222222222222222222",
  "type": "post.created",
  "occurred_at": "2026-09-07T12:00:00+00:00",
  "source": "https://asobby.com",
  "data": {
    "count": 1,
    "snapshot_url": "https://asobby.com/api/v1/lobby",
    "post_id": "0123456789abcdef0123456789abcdef"
  }
}
```

`data.post_id` は新規募集の ID。トップレベルの `id` は通知 ID なので取り違えない。
受信側は一覧 API の `posts[]` から `id == data.post_id` を探せば、投稿者の Discord ID 等を取得できる。
配送待ちの間に募集が終了している場合は、一覧に存在しないこともある。
通知には募集本文・Discord ID・IP・API キーを載せない。受信側は設定済みの一覧 API から最新状態を取得する。
管理画面のテスト通知は `lobby.changed` の形式で、トップレベルに `test: true` が付く。
通常の JSON Webhook であり、Discord Incoming Webhook 専用のメッセージ形式ではない。
`data.count` は通知を作った時点の件数なので、受信後に取得する一覧 API の件数とは異なる場合がある。
`occurred_at` はタイムゾーン付き ISO 8601 文字列（UTC、小数秒が付く場合もある）で、
一覧の `created_at` の数値形式とは異なる。

Webhook 受信用の JSON Schema は以下。一覧 API のスキーマをここに流用しない。

<!-- api-doc:webhook-schema -->
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "AsobbyLobbyEvent",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "id", "type", "occurred_at", "source", "data"],
  "properties": {
    "schema_version": {"type": "integer", "const": 1},
    "id": {"type": "string"},
    "type": {"type": "string", "enum": ["lobby.changed", "post.created"]},
    "occurred_at": {"type": "string", "format": "date-time"},
    "source": {"type": "string"},
    "test": {"type": "boolean", "const": true},
    "data": {
      "type": "object",
      "additionalProperties": false,
      "required": ["count", "snapshot_url"],
      "properties": {
        "count": {"type": "integer", "minimum": 0},
        "snapshot_url": {"type": "string"},
        "post_id": {"type": "string"}
      }
    }
  },
  "allOf": [{
    "if": {"properties": {"type": {"const": "post.created"}}},
    "then": {
      "properties": {"data": {"required": ["post_id"]}},
      "not": {"required": ["test"]}
    },
    "else": {"properties": {"data": {"properties": {"post_id": false}}}}
  }]
}
```

通常通知は `test` を省略し、テスト通知だけ `test: true` を返す。`test: false` は送らない。

- 2xx を配送成功とする。これは受信側が処理を受け付けたことを表し、その先の Discord 表示成功までは保証しない。
- エラーは 5 秒から最大 300 秒まで間隔を延ばして再試行。`Retry-After` は最大 1 時間まで尊重する。
- 再試行では同じ通知 ID を使用する。`lobby.changed` は待機中に一覧が変われば最新状態の通知に置き換える。
- `post.created` は別の待ち行列に保持し、一覧更新やテスト通知で上書きしない。
  両種の通知がある場合は交互に配送し、送信間隔・再試行待ち・1送信先1件の同時送信制限は共有する。
  1送信先あたり最大200件。上限では先頭の送信/再試行対象を残し、古い待機通知から間引く。
  この待ち行列はメモリ上のみで、再起動・連携設定の変更/キー再発行/停止/削除では消える。
  設定の変更/再起動後は `lobby.changed` で再同期し、過去の募集を新規通知として再送しない。
- 送信は 15 秒で打ち切る。リダイレクトは追わず、プロキシ環境変数は利用しない。
  登録時と毎送信時に DNS を確認し、公開 IP に固定して TLS 検証付きで接続する。
  localhost、プライベート/リンクローカル IP などへの送信は拒否する。

この機能は **最新の募集一覧を同期するための通知**に、新規募集の識別を加えたもの。
全変更の履歴・新規通知の永続的な配送保証・厳密な一度だけの配送は保証しない。
受信側は初回と数分ごとにも一覧を取得し、通知の重複・一時停止から復旧できるようにする。
同じ Discord メッセージの更新は定期実行分も含めて直列化し、内容が変わった場合だけ編集する。
API 取得失敗を「0 件」と扱わず、古い情報であることを表示するか、再試行する。

### ランクの設定・実績表示

内部ランクが同じ `normal`（一覧 API の `rank` は `N`）でも、開始ランク未選択とランク戦の経験者を区別できるように、
ロビー（カジュアル・ランクマ両方）・`GET /posts`・SSE・`GET /api/v1/lobby` に
`rank_status` と `ranked_games` を追加している。ランク欄の補助表示は日本語・英語に対応。

| `rank_status` | ロビー表示 | 判定 |
| --- | --- | --- |
| `unset` | 未設定 | 開始ランク未選択（`rank_locked=false`）、確定ランク戦0戦 |
| `initial` | 初期設定 | 開始ランク設定済み（`rank_locked=true`）、確定ランク戦0戦 |
| `provisional` | 暫定・N戦 | 累計の確定ランク戦が1〜49戦 |
| `ranked` | ランク戦N戦 | 累計の確定ランク戦が50戦以上 |
| `unknown` | 実績不明 | アカウントの実績を確認できない。`ranked_games` は `null` |

- `ranked_games` はホスト・ゲスト両方として参加した確定ランク戦の累計。勝敗・引き分けを含み、
  カジュアル戦やランク戦として認定されていないインポート戦績、未確定の試合は含めない。
- 50戦は現在の昇降格判定に必要な試合数に合わせた**実績の目安**。現在ランク期間の試合数ではなく累計なので、
  昇降格直後に「初期設定」「暫定」へ戻らない。同じランクに留まっていても50戦以上なら実績ありと分かる。
- 初期ランクを選んだ動機や実力は判定できない。「適当に選んだ」「ランクが正確」と断定するものではない。
  古いデータも保存済みの選択フラグ・戦績から算出し、累計戦数だけで過去の自動昇降格の有無を断定しない。
- これらはサーバー計算の表示用情報。ランクマの参加条件・勝率計算・昇降格ルール自体は変更しない。
  初期選択・ランク戦結果の確定・募集の定期更新・再起動時の復元で更新する。
  ランク文字列が変わらなくても実績が変われば一覧の `revision`・Webhook 通知対象になる。
- アカウントに紐づかない API 登録の募集は `rank: ""`、`rank_status: "unknown"`、`ranked_games: null`。
  ロビーのランク欄は従来どおり空欄で、未設定ユーザーや0戦のユーザーには分類しない。
- dpalette では `rank_status` で表示を分け、`ranked_games` を併記できる。
  新規登録・更新リクエストからランク実績を自己申告することはできない。

### 署名検証

以下のヘッダーを付与する。

| ヘッダー | 値 |
| --- | --- |
| `X-Asobby-Event` | JSON の `type` と同じ（`lobby.changed` / `post.created`） |
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
   HTTP ノードはメソッド `GET`、URL `https://asobby.com/api/v1/lobby`、クエリ `{}`、JSON本文 `null`。
   「ヘッダー」欄は JSON で `{"Authorization":"Bearer {{ secret.asobby_api_key }}"}` と入力する。
   Secret 名は `asobby_api_key`、保存する値は `Bearer ` を付けない API キー本体。
   バックスラッシュで `_` をエスケープしない。ドライランでは実際の HTTP 通信はしない。
   一覧の取得先には自分で設定した asobby の URL を使い、通知本文から無条件で転記しない。
4. 一覧 0 件のときも「現在募集中のホストはいません」と同期する。同じ `message_key` を使い続ける。
5. 初回実行と定期的な再同期を設定し、asobby 管理画面からテスト通知を送る。

新規募集専用の処理は `trigger.webhook` の `body.type == "post.created"` で分岐し、
`body.data.post_id` を利用する。既存一覧の同期は `lobby.changed` だけで引き続き利用できる。

dpalette の汎用 Webhook は受信用 URL のトークンで保護される。
上記 HMAC の自動検証を備えていることは前提にしていない。署名検証を必須にする場合は受信側に追加する。
この機能の導入だけでは、dpalette のワークフロー作成や Discord への投稿は行われない。

### クライアント不要のカジュアル募集登録（UDP で維持）

管理画面の連携設定で **「カジュアル募集の登録を許可する」** を有効にする。
`POST /api/v1/posts` に同じ Bearer API キーを付け、次の JSON を送信する。
`addr` のプレースホルダーは、募集する本人の公開 IPv4 に置き換える。

<!-- api-doc:registration-request -->
```json
{
  "request_id": "recruitment-message-123",
  "external_user_id": "player-456",
  "discord_user_id": "123456789012345678",
  "owner_name": "プレイヤー",
  "addr": "<本人の公開IPv4>:10800",
  "comment": "対戦募集",
  "stream_url": ""
}
```

- 必須: `request_id`・`external_user_id`（各128文字以内）、`owner_name`（80文字以内）、`addr`。
- 任意: `comment`（200文字以内）、`stream_url`（300文字以内。YouTube/Twitch/ニコニコのみ）。
- 上記6フィールドの型は string。`null` は不可。`comment`・`stream_url` の省略時は `""`。
  `request_id`・`external_user_id`・`owner_name` は入力時1文字以上で、前後空白を除去した結果も空でないこと、
  U+0000〜U+001F の制御文字を含まないことを検証する。長さ上限は前後空白を除去する前の入力に適用される。
  `addr` は64文字以内。本文に列挙していない余分なキーがあれば422。
  例の `<本人の公開IPv4>` は置換必須で、文字どおり送信すると422になる。
- 任意: `discord_user_id`（string または null、省略時 `null`）。1〜20桁の半角数字で、
  先頭0なし、1〜18446744073709551615 の範囲。JSON の数値ではなく文字列で送信する。
  Discord 連携では認証済みのコマンド投稿者の ID を設定する。asobby 側では連携元の申告として扱い、
  Discord アカウントへのログイン権限・既存戦績との紐付け・ランク設定には使用しない。
  従来の `external_user_id` だけの POST はそのまま使え、既存の再送判定も維持する。
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

新規受付は UDP 検証を待たず、永続化後に **202 Accepted** を返す。
登録 POST は同じリクエストの再送でも202。その登録が既に公開済みなら、下記の `active` 形式を返す。

<!-- api-doc:registration-checking -->
```json
{
  "id": "fedcba9876543210fedcba9876543210",
  "state": "checking",
  "status_url": "https://asobby.com/api/v1/posts/fedcba9876543210fedcba9876543210",
  "external_user_id": "player-456",
  "post": null
}
```

`GET /api/v1/posts/{id}` に同じ連携のキーを付けると状態を取得できる。
この GET にも `allow_posting=true` が必要。成功時は200で、POST の202と本文の形は同じ。
どちらも `Cache-Control: no-store` を付ける。**`state` は `checking` / `active` の2種類だけ**で、
`closed` / `expired` などの終了状態は返さず、終了後は404になる。
`state=checking` なら `post=null`、`state=active` なら `post` は33フィールドの募集オブジェクト。
`active` は「公開済み」を意味し、その瞬間の接続可否・募集中・対戦中を保証しない。
未応答の猶予期間でも `active` のまま `reachability_lost=true` / `net_status=0` になることがある。

公開済みの完全な本文例（ID・IP・日時は説明用）:

<!-- api-doc:registration-active -->
```json
{
  "id": "fedcba9876543210fedcba9876543210",
  "state": "active",
  "status_url": "https://asobby.com/api/v1/posts/fedcba9876543210fedcba9876543210",
  "external_user_id": "player-456",
  "post": {
    "id": "fedcba9876543210fedcba9876543210",
    "rank": "",
    "rank_status": "unknown",
    "ranked_games": null,
    "post_type": "casual",
    "rating": null,
    "addr": "203.0.113.10:10800",
    "comment": "対戦募集",
    "updated_at": 1788825600.0,
    "created_at": 1788825600.0,
    "stream_url": "",
    "giuroll": false,
    "autopunch": false,
    "direct_reachable": true,
    "reachability_uncertain": false,
    "reachability_lost": false,
    "match_status": "",
    "net_status": 3,
    "owner_name": "プレイヤー",
    "discord_user_id": "123456789012345678",
    "discord_user_id_source": "integration",
    "owner_avatar": "",
    "guest_name": "",
    "guest_avatar": "",
    "guest_user_id": "",
    "guest_connected": false,
    "ranked_active": false,
    "ping_warn_enabled": false,
    "ping_warn_ms": 60,
    "ping_warn_giuroll_ms": 100,
    "country_code": "",
    "country_name": "",
    "supports_messages": false
  }
}
```

`post` は公開用 `Post` の31フィールドに Discord ID 関連の2フィールドを加えたもので、
**一覧 API の `posts[]` と同じスキーマではない**。
共通22フィールドの型は一覧 API と同じだが、合成フィールド `status` は存在しない。
代わりに、一覧 API では常に除外される以下の10フィールドと、常に存在する `addr` がある。

| フィールド | 型 | 登録結果での内容 |
| --- | --- | --- |
| `addr` | string | 登録した IP:port。`include_address` に関係なく返る |
| `updated_at` | number | Unix 時刻（秒、小数可）。登録時または募集内容が変化した時刻で、毎回の UDP 確認時刻ではない |
| `net_status` | integer | UDP 監視では `0` 不明 / `2` 接続中 / `3` 待機相当。試合中を確定できないので `4` は設定しない |
| `owner_avatar` | string | API 登録では `""` |
| `guest_avatar` | string | API 登録では `""` |
| `guest_user_id` | string | API 登録では `""`。既存アカウントに推測で紐付けない |
| `guest_connected` | boolean | UDP の応答から接続中と判定したか。対戦相手の同定済みとは限らない |
| `ping_warn_enabled` | boolean | API 登録では `false` |
| `ping_warn_ms` | integer | 通常ホスト向けの警告しきい値（ms）。API 登録では警告無効なので未使用 |
| `ping_warn_giuroll_ms` | integer | Giuroll 向けの警告しきい値（ms）。API 登録では未使用 |
| `supports_messages` | boolean | API 登録では `false` |

登録結果は `{id: string, state: string, status_url: string, external_user_id: string, post: object | null}` の5キーを常に返す。
`external_user_id` は POST で指定した連携内の投稿者 ID。公開前の `checking` でも返し、別連携からは取得できない。
`post` がある場合は上記33キーを省略せず返す。`id` は `post.id` と一致する。
`status_url` は状態取得の URL であり、それ自体に認証情報は含まない。
`post` の IP は登録した連携には返すが、一覧 API の `include_address` 権限とは独立。
未確認の募集はロビー・一覧 API・募集通知には出さない。別の連携の登録や終了済み登録は404。
定期確認する場合は15秒以上の間隔にし、429の `Retry-After` に従う。

表示は通常のカジュアル募集と同一で、外部募集の区分・バッジは付けない。
asobby のランクが確認できないのでランク欄は空欄。
ホスト側クライアントがないため、定型メッセージと高 Ping 警告は無効。
`supports_messages: false` は受信機能の有無を表す。

#### 登録 API のエラー

一覧 API と同じ401・429・連携ストレージの503に加えて、以下を返す。
HTTPException の本文は `{"detail":"説明文字列"}`。
Pydantic/FastAPI の入力検証エラーでは `detail` が文字列ではなく配列になるため、別扱いにする。

| HTTP | 条件 |
| --- | --- |
| 403 | 作成権限がない（状態取得 GET にも必要） |
| 404 | 状態取得で、存在しない・終了した・別連携の登録 ID を指定 |
| 409 | 同じ `request_id` で内容が異なる、または同じ IP:port の募集が既に存在 |
| 422 | 必須キー不足・型/長さ/アドレス検証・余分なキー・不許可の配信 URL 等 |
| 429 | 同時募集数上限（`Retry-After: 30`）、新規登録6件/分または API 全体60回/分（`Retry-After: 60`） |
| 503 | UDP 監視無効、または募集/連携ストレージ利用不可 |

422 の検証エラー要素には `type`・`loc`・`msg` があり、`input` や `ctx` が含まれる場合もある。
登録の変更・手動終了用 PATCH / DELETE API はない。

#### 生存確認と自動検出

- クライアントの定期更新は不要。**手動終了 API・固定の掲載期限は設けない。**
  応答が続く限り掲載を維持し、確認済みの未応答が3回連続、かつ最初の失敗から2秒以上で掲載を終了する。
  通常の確認間隔の目標は15秒、確認済みの未応答後は1秒間隔で再確認する。
  全体を最低1秒間隔で分散し、既存プローブと共有する固定 UDP ポートで直列実行するため、
  件数・確認待ち・応答時間によって実際の間隔や終了検知は長くなる。ホスト停止から2秒以内の削除を保証するものではない。
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
| `GET /api/v1/lobby` | 連携キーで取得する一覧オブジェクト。IP は権限次第。上記 JSON Schema を参照 |
| `POST /api/v1/posts` | 作成権限付き連携キーで UDP 監視募集を登録。202、状態取得と同じ本文 |
| `GET /api/v1/posts/{id}` | 同じ連携・作成権限付きキーで登録状態を取得。200、`checking` / `active` |
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
- 通常クライアントの投稿は通常 TTL 20 秒、ゲスト IP を取得済み、または `guest_connected=true` の場合は600秒。
  `net_status` だけでは延長されない。クライアントは5秒間隔のハートビートで維持する。
  API 登録の UDP 監視募集には、この TTL やクライアントのハートビート要件は適用しない
- 新規作成時とアドレス変更時にホスト到達性を検証する。通常ホストは UDP soku echo で直接プローブする
- 通常クライアントの autopunch ホストは AutoPunch リレー経由で検証する（リレー lookup → hole punch → soku echo）。リレー先は環境変数 `ASOBBY_AUTOPUNCH_RELAY` で変更可能（既定 `delthas.fr:14763`）。通常クライアントの新規登録はリレー到達不能時に検証をスキップする（fail-open）。API 登録はゲーム応答の確認まで掲載しない
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

設定は `app/fly.toml`。東京リージョン（nrt）・**shared-cpu-2x（共有 CPU 2 vCPU）/ 2GB**・マシン1台構成。

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
