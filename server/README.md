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

- 投稿の更新・削除には作成時に発行される `owner_token` が必要（他人の投稿は操作不可）
- 投稿は TTL 20 秒。クライアントは 5 秒間隔のハートビート（update）で維持する
- 新規作成時とアドレス変更時に UDP echo でホスト到達性を検証（autopunch 時は除く）
- 作成レート制限: IP あたり 2 秒間隔・同時 2 件まで
- 旧 `POST /posts/upsert` は 410 Gone を返す（旧クライアントへの更新案内）

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
