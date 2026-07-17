# asobby

asobby は東方非想天則のネット対戦ロビーシステムです。

- **閲覧**: サーバーが配信する Web ページ（`http://<server>/`）で募集一覧を見られます（リアルタイム更新）。カジュアル・ランクマとも閲覧には Discord ログインが必要です。ログイン・ログアウトはクライアントのトレイメニューから行います（クライアントでログインすると、その際に使ったブラウザにもログインが引き継がれます）。
- **募集**: Windows 用クライアント（自動投稿エージェント）を起動しておくと、非想天則でホストを立てるだけで自動的に募集が投稿・更新・削除されます。クライアントはタスクトレイに常駐し、soku / giuroll / autopunch のランチャーも兼ねます。soku のパスは起動中の天則を見つけたときに自動設定されます（未設定時のみ）。逆に「set soku path」で exe を指定してある場合は、リネームした exe や th123.exe が複数ある環境でも、そのパスのプロセスを優先して検知します。トレイアイコンをクリックしてメニューから投稿設定・ツール起動・ロビーページを開く操作ができます（アイコン色: 灰=待機 / 緑=募集中 / 橙=対戦中）。身内戦などで募集を出したくないときは、トレイメニュー「ホスト自動検知を一時停止」から 30 分 / 1 時間 / 3 時間の間、自動投稿を止められます（停止中は既存の募集も閉じられ、期限が来ると自動で再開。手動再開も可能）。停止中も戦績のローカル記録・ゲスト側の戦績報告・リプレイ収集は通常どおり動作します。トレイメニュー「ホスト時に IP:Port をコピー」を ON にすると（デフォルト OFF）、ホストを立てた時点で自分の募集アドレスがクリップボードにコピーされます（ログインや自動投稿の一時停止とは無関係に動作。1 回のホストにつき 1 回）。
- **戦績**: ネット対戦の KO 確定ごとにローカル SQLite (`matches.db`) へ記録します。`asobby_config.json` と同じディレクトリに保存され、サーバーがなくてもクライアント単体で戦績の閲覧・絞り込みができます（トレイメニュー「戦績を見る...」）。Discord ログイン時は起動 10 秒後と以後 10 分ごとにサーバーと双方向同期します（Pull: サーバー戦績の取り込み / Push: 5 分以上経過した未送信ローカル戦績の送信）。
- **言語**: 日本語 / English。Web ページはヘッダーの言語ボタン（`?lang=en` または `localStorage`）、クライアントはトレイメニュー「言語 / Language」から切り替え（`asobby_config.json` の `options.locale` に保存）。

## ディレクトリ構成

- `client/` クライアント（自動投稿エージェント & ツールランチャー）
- `server/` サーバー（FastAPI。API・SSE・閲覧ページ）

## server 起動

詳細は `server/README.md` を参照

## Credits

- [autopunch](https://github.com/delthas/autopunch) by [delthas](https://github.com/delthas) — asobby uses autopunch for NAT hole punching, so players can host without port forwarding. Thank you for this great tool! (MIT License)
- [giuroll](https://github.com/Giufinn/giuroll) — rollback netcode for Touhou Hisoutensoku.
