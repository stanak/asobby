# asobby server

FastAPI ベースのロビーサーバー。募集の API に加えて、閲覧用 Web ページ（`GET /`）を配信する。

## API 概要 (v0.2)

| Method/Path | 説明 |
| --- | --- |
| `GET /` | 閲覧用 Web ページ（SSE でリアルタイム更新） |
| `GET /myip` | クライアントのグローバル IP を返す |
| `GET /posts` | 募集一覧（公開フィールドのみ） |
| `POST /posts` | 募集の新規作成。レスポンスで `owner_token` を発行 |
| `POST /posts/update` | 募集の更新。`id` + `owner_token` が必須 |
| `POST /posts/close` | 募集の削除。`id` + `owner_token` が必須 |
| `GET /sse/posts` | SSE。接続直後に `snapshot`、以後 `upsert` / `close` |
| `POST /auth/device` | Discord ログイン開始。`device_code` と `verify_url` を返す |
| `GET /auth/discord/start` | (ブラウザ用) Discord の認可画面へリダイレクト |
| `GET /auth/discord/callback` | (ブラウザ用) OAuth コールバック。完了ページを表示 |
| `POST /auth/device/poll` | ログイン完了ポーリング。完了時に `session_token` を返す |
| `GET /auth/me` | `Authorization: Bearer` のセッション検証・ユーザー情報 |

- 投稿の更新・削除には作成時に発行される `owner_token` が必要（他人の投稿は操作不可）
- 投稿は TTL 20 秒。クライアントは 5 秒間隔のハートビート（update）で維持する
- 新規作成時とアドレス変更時にホスト到達性を検証する。通常ホストは UDP soku echo で直接プローブする
- autopunch ホストは AutoPunch リレー経由で検証する（リレー lookup → hole punch → soku echo）。リレー先は環境変数 `ASOBBY_AUTOPUNCH_RELAY` で変更可能（既定 `delthas.fr:14763`）。リレー自体に到達できない場合は検証をスキップする（fail-open）
- 作成レート制限: IP あたり 2 秒間隔・同時 2 件まで
- 旧 `POST /posts/upsert` は 410 Gone を返す（旧クライアントへの更新案内）

## 永続化 (PostgreSQL)

ユーザー・戦績 (matches)・リプレイ (replays) を PostgreSQL に永続化する。
募集投稿は TTL 20 秒の揮発データなので従来どおりインメモリ。

- スタック: SQLAlchemy 2.0 (async) + asyncpg + Alembic
- スキーマは起動時に自動でマイグレーションされる（`alembic upgrade head` 相当）
- `DATABASE_URL` 未設定なら DB 機能（Discord ログイン含む）だけ無効になり、投稿は通常動作

### テーブル概要

- `users`: Discord ID 主キー、表示名、`token_version`（インクリメントで発行済みセッションを失効）、`last_ip`（ログイン時・認証リクエスト時に自動更新。echo パケットで得た対戦相手 IP との照合用）
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

## Discord ログイン（任意）

クライアントはログインなしでも投稿できる。ログインすると投稿に Discord の
表示名（`owner_name`）が載り、ロビーの User 列に表示される。

フローはデバイスコード方式:
クライアントが `POST /auth/device` → ユーザーがブラウザで `verify_url` を開いて
Discord で承認 → クライアントが `POST /auth/device/poll` で `session_token` を受領。
セッションは HMAC 署名付きトークン（有効期限 30 日）で、`users.token_version` と
突合して検証される（DB 側で version を上げれば個別に失効可能）。
以後 `POST /posts` に `Authorization: Bearer <session_token>` を付ける。
ログイン完了時と認証付きリクエストのたびにユーザーの `last_ip` が最新化される。
**Discord ログインには `DATABASE_URL` の設定も必要。**

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

設定は `app/fly.toml`。東京リージョン（nrt）・マシン1台構成。

```sh
cd app
fly launch --copy-config --no-deploy   # 初回のみ: アプリ作成
fly ips allocate-v4                    # 初回のみ: 専用 IPv4 ($2/月、下記参照)
fly deploy --ha=false                  # デプロイ（マシン1台）
```

更新は `fly deploy` だけでよい。ログは `fly logs`。

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
