# プレイヤープロフィールと検索

## 画面と公開範囲

- `/profile`: 本人のプロフィール編集。プレイヤーネームの直下にメインキャラクターを置く。
- `/players/{Discord ID}`: ユーザーページ。ロビーのホスト名・同定済みゲスト名からも移動できる。
- `/players`: プレイヤー検索。検索条件はURLに保存し、ページ送りとブラウザの戻る操作に対応する。

既存ロビーと同様、プロフィールの閲覧・検索・編集はDiscordログイン必須。
全項目は任意。未登録でも既存のDiscord表示名・ランクからユーザーページを利用できる。
生年月日は本人の編集APIだけに返し、公開用プロフィール・検索結果・人口統計には返さない。
生年月日には次の3状態があり、年齢の基準日は日本時間。

| 状態 | 生年月日の保存 | 年齢の表示・検索 | 全ユーザー統計 |
| --- | --- | --- | --- |
| `public` 公開・検索可能 | あり | 年齢だけ公開、検索対象 | 利用する |
| `statistics` 非公開（統計利用を許可） | あり | 非公開、年齢検索対象外 | 利用する |
| `secret` 秘密（既定） | なし | 非公開、年齢検索対象外 | 利用しない |

どの状態でも生年月日そのものは本人以外に返さない。
「秘密」に戻すと保存済みの生年月日もNULLにする。
非公開と秘密の区別も他人に公開しない（公開用APIではどちらも `age: null`）。
年齢と居住国は検索・全ユーザー統計に使用することを入力欄に明記する。

プレイヤーネームはDiscordの名前とは別に保存し、ロビーでの使用をチェックボックスで選ぶ。
保存すると現在の募集表示も更新するが、ホストの生存期限を延長しない。
名前を変更しても対戦の認証・紐付けは既存アカウントIDで行う。
Discordの表示名とユーザー名はOAuthログインで更新し、登録したプレイヤーネームは上書きしない。
**既存ユーザーのDiscordユーザー名（`@username`）は次回ログインから取得できる。**
取得前でも既存のDiscord表示名では検索できる。

ユーザーページ下部には自己紹介と「SNS・Webサイト」を表示する（未登録の欄は非表示）。
自己紹介は最大400 Unicodeコードポイント（改行も1文字。複数コードポイントで構成される絵文字は複数文字）。
改行を保持したプレーンテキストで表示し、HTML・Markdownは解釈しない。
リンクは任意のラベル＋URLを最大10件、登録順に表示する。ラベルだけでなく実際のURLも併記し、
`noopener noreferrer nofollow ugc` 付きの新しいタブで開く。リンク先の自動取得・埋め込みは行わない。
自己紹介・リンクもログインユーザーへの公開情報。これらを対象にした検索条件は追加していない。

居住国は本人の自己申告。ロビーにある接続先IPの国とは独立しており、GeoIPから自動登録しない。
選択肢はISO 3166-1 alpha-2の249国・地域。表示には国旗文字とローカライズされた名称を使用する。
端末に国旗絵文字の対応がない場合も国名で識別できる。

## 編集API

`GET /user/profile` は本人の編集データを返す（このAPIだけに `birth_date`・`birth_visibility`・`player_name_bytes` を含む）。
`PUT /user/profile` は次の編集項目を**全体置換**する。省略した項目は既定値に戻るので、
一部だけ変更する場合もGETしたデータから編集項目一式を組み立てて送る。
Cookieセッションまたは個人セッションの `Authorization: Bearer` が必要。
連携用のAPIキーはこの個人認証を代替しない。

<!-- player-profile-edit -->
```json
{
  "player_name": "あそび人",
  "bio": "霊夢を使っています。\n対戦よろしくお願いします！",
  "profile_links": [
    {"label": "YouTube", "url": "https://www.youtube.com/@example"},
    {"label": "個人サイト", "url": "https://example.com/"}
  ],
  "main_character": 20,
  "use_player_name": true,
  "birth_date": "2000-09-20",
  "birth_visibility": "public",
  "country_code": "JP",
  "device_type": "gamepad",
  "device_model": "HORI RAP-4",
  "favorite_players": ["好きなプレイヤー", "Another Player"],
  "other_games": ["STREET FIGHTER 6", "ぷよぷよ"],
  "strong_characters": [0, 5],
  "weak_characters": [1, 19],
  "character_winrates_public": false
}
```

| 項目 | 型・制約 | 既定値 |
| --- | --- | --- |
| `player_name` | CP932で24バイト以内。日本語全角12文字・ASCII24文字相当。CP932で表せない絵文字など・制御文字は不可 | `""` |
| `bio` | 400文字以内。CRLF/CRはLFに統一。改行・タブ以外の制御文字、サロゲートは不可。空白や改行は保持 | `""` |
| `profile_links` | `{label,url}` の配列、最大10件、登録順。labelは1〜40文字（前後空白除去、制御文字不可）。URLは絶対HTTP(S) URL、正規化後も2048文字以内。認証情報・内部空白・制御文字・バックスラッシュは不可 | `[]` |
| `main_character` | 整数0〜20から1つ。20はランダム | `null` |
| `use_player_name` | ロビーでプレイヤーネームを使用する。trueの場合は名前必須 | `false` |
| `birth_date` | `YYYY-MM-DD`、1900-01-01から日本時間の今日まで。秘密の場合はnull | `null` |
| `birth_visibility` | `public` / `statistics` / `secret`。公開・統計利用時は日付必須。秘密なら送信された日付も保存しない | `"secret"` |
| `country_code` | ISO alpha-2。小文字入力は大文字に変換 | `""` |
| `device_type` | `keyboard` / `gamepad` / `arcade` / `other` / 空文字 | `""` |
| `device_model` | 120文字以内、型番入力時は種別必須 | `""` |
| `favorite_players` | 自由入力の文字列配列。各100文字、最大20件 | `[]` |
| `other_games` | 自由入力の文字列配列。各120文字、最大30件 | `[]` |
| `strong_characters` / `weak_characters` | 整数0〜19の配列、それぞれ最大20件。ランダムは含まない。重複除去・昇順で保存する | `[]` |
| `character_winrates_public` | キャラ別勝率の他ユーザーへの公開許可 | `false` |

名前・タグは前後空白を除去。タグはNFKC正規化＋casefoldで重複排除する。
得意キャラと苦手キャラは別の自己申告なので、同じキャラを両方に選ぶこともできる。
編集画面ではそれぞれ20キャラのチェックボックスから複数選択でき、全解除も可能。
旧仕様の単数形 `strong_character` / `weak_character` はAPIでは受け付けない。
既存のDB登録値は移行処理で複数選択形式に引き継ぐ。
ランク・レート・ユーザーID・戦績など、編集項目以外の送信は422になる。
応答は `{"ok":true,"id":"本人のDiscord ID"}`。

## 閲覧・検索API

`GET /api/players/{Discord ID}` は公開プロフィールに加え、次のDB集計を返す。
他人の生年月日・IP・認証情報などは返さない。本人でもこの公開用APIに生年月日は含まれない。

- `rank`（内部値）/ `rank_symbol`（E / N / Ex / H / L / Ph）、Phのみ `rating`（それ以外はnull）。
- `total_matches`: 確定戦績テーブルにある勝敗または引き分けの総数。
- `unique_opponents`: 相手アカウントIDの重複を除いた人数。相手未同定の試合は数えない。
- `unidentified_opponent_matches`: 相手アカウントIDがない試合数。
- `character_winrates`: 非公開ならnull。本人か公開許可ありなら
  `{char,games,wins,losses,draws,win_rate}` の配列。試合のあるキャラだけ。Random(20)の記録も含む。
  `win_rate` は0〜1、勝利数÷対戦数を小数第4位まで丸める。引き分けも分母に含む。
- `is_owner`: 本人ならtrue。非公開勝率を本人だけに表示する場合の説明に使う。

生年月日の代わりに `age` を返し、非公開（統計利用のみ）・秘密はnull。
`display_name` はプレイヤーネームがあればその値、なければDiscord表示名。
`lobby_name` はロビー表示選択に従う名前。
`discord_name` はDiscord表示名、`discord_username` は取得済みのユーザー名（未取得は空文字）。
`strong_characters` / `weak_characters` は登録した全キャラのID配列（昇順、未登録は空配列）で、
本人用API・ユーザーページ・検索結果に共通する。旧単数形のフィールドは返さない。
`bio` / `profile_links` も本人用API・公開プロフィール・検索結果に共通し、未登録はそれぞれ空文字・空配列。

`GET /api/players` は次のクエリで検索し、
`{players:[公開プロフィール...],total,page,limit}` を返す。検索結果に個別戦績集計は含まれない。
異なる条件はAND、複数ランクはORで検索する。メイン・得意・苦手キャラはそれぞれ1つを指定する。
得意・苦手は、複数登録されたキャラのどれかに指定IDが含まれる人が該当する。

| クエリ | 検索方法 |
| --- | --- |
| `name` | プレイヤーネーム・Discord表示名・Discordユーザー名のいずれかに部分一致（英字大小無視） |
| `main_character` | 整数0〜20の完全一致。Random対応 |
| `age_min` / `age_max` | 満年齢の範囲、両端を含む。公開の人だけ対象。0〜150 |
| `country_code` | 国・地域コードの完全一致 |
| `device_type` | デバイス種別の完全一致 |
| `device_model` | 型番のNFKC＋casefold部分一致 |
| `favorite_player` | 登録した推しプレイヤー名のNFKC＋casefold部分一致 |
| `game` | 好きなゲーム名のNFKC＋casefold部分一致 |
| `rank` | 内部ランク値（easy/normal/ex/hard/luna/ph）。繰り返し指定可 |
| `strong_char` / `weak_char` | それぞれ整数0〜19を1つ指定。登録配列にそのIDを含む人を検索 |
| `page` / `limit` | 既定1ページ・24人、最大100人/ページ。ID順で安定したページ送り |

推しプレイヤーのリンクは `name` 検索へ移動する（`favorite_player` 検索とは別）。
`%` / `_` はワイルドカードではなく文字として扱う。
**Phレート・総対戦数・対戦相手数・勝率・生年月日そのものを検索するクエリは受け付けず、422を返す。**
Phレートはプロフィールへの表示のみで、ランク（Phなど）で絞り込む。
下限>上限、不正なキャラ/国/ランクなども422。未認証は401、存在しないプレイヤーは404。
個人情報を含む応答は `Cache-Control: private, no-store`。

`GET /api/players/options` は国・ランク・デバイスの選択肢を返す（認証不要）。
`GET /api/players/statistics` は全ユーザーの国別人数・年齢帯別人数を返す（認証必須）。
年齢統計は公開・非公開（統計利用許可）の情報を使う。秘密の生年月日・空の居住国は、それぞれの集計から除外。個別の生年月日は返さない。

## DB移行と確認

0017はUserの任意プロフィール列と複数値の `player_profile_tags` を追加する。
既存の名前・ランク・戦績は変更しない。生年月日・メイン/得意/苦手キャラはNULL、他の任意情報は空、
名前切替・勝率公開はfalse、生年月日の公開範囲はsecretで開始する。追加のクライアント更新は不要。
0018は `player_profile_characters` を追加し、既存の得意・苦手キャラを移す（NULLは空配列相当）。
以降はこのテーブルを保存・検索の正とし、旧単一値列はロールバック用に残して選択IDの最小値（未登録はNULL）を反映する。
0017へのダウングレードは、複数選択が残っている場合はデータ欠落を防ぐため拒否する。
戻す場合は本人が各選択を1つ以下に減らす必要がある。
0019は `users.bio` と `users.profile_links` を追加し、既存ユーザーは空文字・空配列で開始する。
0019のダウングレードはこの2項目を削除するため、必要なデータは事前に退避すること。
本番に反映するにはサーバー更新が必要（起動時に既存のAlembic手順でマイグレーション）。

テストは `test_player_profiles.py` / `test_player_profiles_migration.py`。
`player_profiles_ui.cjs` は全通信をモックしたJA/EN・モバイル/デスクトップ画面テストで、本番を書き換えない。
