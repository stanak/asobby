# Linux / Wine（実験的対応）

Windows 版 `asobby.exe` を Wine 上で実行します。Linux の Python から直接起動する
ネイティブ Linux 版ではありません。この変更を含むソースからビルドした exe が必要です。
従来のリリース exe にはこの対応は含まれません。

## 起動

1. まず、Wine 上で天則単体が動くことを確認します。
2. 天則が使っている **Wine prefix と Wine の実行ファイル（runner）** を確認します。
   Lutris / Bottles 等を使っている場合も、そのゲームと同じ環境を使ってください。
3. asobby.exe を書き込み可能なフォルダーに配置し、以下で起動します。

```sh
WINEPREFIX="/home/yourname/Games/soku-prefix" \
  sh client/run-wine.sh "/home/yourname/Apps/asobby/asobby.exe"
```

独自の Wine runner を使っている場合:

```sh
WINEPREFIX="/home/yourname/Games/soku-prefix" \
WINE="/path/to/game-runner/bin/wine" \
  sh client/run-wine.sh "/home/yourname/Apps/asobby/asobby.exe"
```

スクリプトは既存 prefix の指定を必須とし、新しい prefix の作成や設定変更は行いません。
exe のあるフォルダーを作業ディレクトリにし、設定・ログ・戦績の保存先を安定させます。
`WINE` は実行ファイルのパスであり、オプションを含むコマンド文字列ではありません。

スクリプトを使わず、同じ runner から起動することもできます:

```sh
cd "/home/yourname/Apps/asobby"
WINEPREFIX="/home/yourname/Games/soku-prefix" ASOBBY_WINE=1 wine ./asobby.exe
```

通常は Wine を自動検出します。`ASOBBY_WINE=1` は検出を強制する指定です。
配布用ビルドは 64bit Windows クライアントのため、32bit の天則と64bit の asobby の
両方を実行できる Wine 環境が必要です。既存の32bit専用 prefix を無理に変更しないでください。
Proton の個別環境や各ランチャーとの組み合わせは動作保証していません。

## Wine での操作

- タスクトレイの代わりに操作ウィンドウを表示します。最小化はできますが、閉じると終了します。
- 「メニュー」からログイン・募集設定・ツール起動・戦績表示などを操作できます。
- Windows トースト通知（WinRT）は使わず、ウィンドウ内に最新50件の通知を表示します。
  リンク付き通知は選択して「選択した通知を開く」で開けます。
- 対戦・観戦リクエストは「メニュー」→「リクエストに返信」から承諾・拒否します。
- 「起動時の通知を表示」をOFFにしても、操作ウィンドウは表示します。
- グローバルホットキー・ブラウザー・クリップボード連携は Wine とデスクトップ環境の
  対応状況に依存します。ホットキーが使えなくてもメニューから同じ操作ができます。
  ブラウザーが開かない場合は Wine の URL 関連付けを確認してください。

ゲーム検出・メモリ読み取り・勝敗判定は Windows 版と同じ処理を使います。
**別 prefix の天則は検出対象にできません**。同じ prefix 内でも、必要ならメニューから
天則 exe のパスを指定してください。Giuroll / AutoPunch も同じ環境で実行します。
多重起動防止も同じ Wine prefix 内が対象です。別 prefix との間の排他は保証しません。

## 検証状況

Wine 判定、WinRT を読み込まないこと、操作ウィンドウと通知・メニュー、起動スクリプトは
自動テストの対象です。Windows 上での互換モード試験は実際の Wine での動作保証ではありません。
この変更の開発環境には Wine がなく、Linux 実機でのエンドツーエンド検証は未実施です。

実利用前に、使用する Wine / デスクトップ環境で次を確認してください:

- Discord ログイン、ロビー表示、設定保存、同じ prefix での多重起動防止。
- 通常の天則 / Giuroll / AutoPunch のプロセス検出、ホスト募集・終了、ゲスト接続。
- 対戦・観戦リクエストの通知と返信、ブラウザーからクライアントへの操作。
- 対戦終了・再戦・ロールバック時の勝敗記録（1試合が重複記録されないこと）、戦績同期、リプレイ。

問題を報告する際は Wine のバージョン、ディストリビューション、デスクトップ環境、
利用した runner、asobby のバージョンと `asobby.log` を添えてください。
