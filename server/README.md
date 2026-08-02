# asobby server

FastAPI ベースのロビーサーバー。募集の API に加えて、閲覧用 Web ページ（`GET /`）を配信する。

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
| `GET /posts` | 募集一覧（公開フィールドのみ） |
| `POST /posts` | 募集の新規作成（Discord ログイン必須）。レスポンスで `owner_token` を発行 |
| `POST /posts/update` | 募集の更新。`id` + `owner_token` が必須。応答に `messages`（閲覧者からの未読定型メッセージ）を含み、返却後キューはクリア |
| `POST /posts/{id}/message` | Web ロビー閲覧者がホストへ定型メッセージを送る（Discord ログイン必須） |
| `POST /posts/reply` | ホストがリクエストメッセージへ承諾/拒否を返す。`id` + `owner_token` + `message_id` + `reply` (`accept`/`decline`) |
| `POST /posts/close` | 募集の削除。`id` + `owner_token` が必須 |
| `POST /posts/result` | 対戦勝敗の報告。`id` + `owner_token` + `winner` (`host`/`guest`/`draw`) |
| `POST /matches/report` | ゲスト側クライアントからの対戦結果補完報告。`Authorization: Bearer` 必須 |
| `POST /replays/upload` | リプレイ (.rep) のアップロード。`Authorization: Bearer` 必須。body は生バイト |
| `POST /import/tensokukan` | 天則観 (tsk) 戦績 DB (.db) のインポート。`Authorization: Bearer` またはクッキー必須。body は生バイト |
| `GET /sse/posts` | SSE。接続直後に `snapshot`、以後 `upsert` / `close` / `message_reply` |
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

**ロビー閲覧（`GET /posts`, `GET /sse/posts`）はログイン不要。**
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
直近 60 秒以内の重複報告は排除する（ゲスト報告はスキップ、ホスト報告はゲスト報告行を
昇格して上書きする）。
`ASOBBY_HOSTCHECK` は既定 **off**（UDP 到達性プローブ無効。REQUIRE の AP 表示は
クライアント申告の `autopunch` のみ）。`ASOBBY_HOSTCHECK=on` で従来の直接/AP 判定を有効化できる。

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
- 昇降格は**現ランクで行ったランクマ対戦の直近 30 戦の勝率**で判定（最低 10 戦揃ってから）。ランク変更時に窓はリセット (`rank_changed_at` 以降のみ集計)
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
