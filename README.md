# asobby

asobby は東方非想天則のネット対戦ロビーシステムです。

- **閲覧**: サーバーが配信する Web ページ（`http://<server>/`）で誰でも募集一覧を見られます。インストール不要・リアルタイム更新。
- **募集**: Windows 用クライアント（自動投稿エージェント）を起動しておくと、非想天則でホストを立てるだけで自動的に募集が投稿・更新・削除されます。クライアントはタスクトレイに常駐し、soku / giuroll / autopunch のランチャーも兼ねます。トレイアイコンの右クリックメニューから投稿設定・ツール起動・ロビーページを開く操作ができます（アイコン色: 灰=待機 / 緑=募集中 / 橙=対戦中）。

## ディレクトリ構成

- `client/` クライアント（自動投稿エージェント & ツールランチャー）
- `server/` サーバー（FastAPI。API・SSE・閲覧ページ）

## server 起動

詳細は `server/README.md` を参照

## Credits

- [autopunch](https://github.com/delthas/autopunch) by [delthas](https://github.com/delthas) — asobby uses autopunch for NAT hole punching, so players can host without port forwarding. Thank you for this great tool! (MIT License)
- [giuroll](https://github.com/Giufinn/giuroll) — rollback netcode for Touhou Hisoutensoku.
