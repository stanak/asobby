# ランダム選択の戦績

## 記録・集計・レート

- `matches.host_char` / `guest_char` は記録上の選択キャラ（0〜19、Random=20）。
  ランダム選択はWeb/ローカル戦績、個人のキャラ別勝率、匿名集団統計で20に分類する。
  実キャラ側には二重計上しない。集団統計の少人数非表示ルールは維持する。
- `host_actual_char` / `guest_actual_char` は実際に出たキャラ。
  `host_random` / `guest_random` は選択観測の結果（true / false / null）。
  nullは未観測または旧版の記録であり、プロフィールのメインキャラから推測しない。
- Ph同士のランク戦では、各参加者の選択キャラ枠だけを更新する。
  例えばRandom→霊夢ならRandom枠を更新し、霊夢枠は更新しない。
  全21枠のキャラ別レートと、従来の全体レートの算出方法は維持する。
- リプレイの通常キャラ検索（0〜19）は実キャラ、20指定はランダム選択で検索する。
  旧行は従来のキャラ列を使う。検索一覧は `Random (Reimu)` のように表示し、
  リプレイファイル名には実キャラを使う。

## クライアントの検出

ネット対戦のキャラ選択シーン（8/9）で、Select構造体の左右の選択段階が
ともに3（選択完了）となった時点のグローバルキャラIDを保存する。
その後の実キャラへの置換で上書きしない。選択をキャンセルしたら破棄する。
カーソルを合わせただけの状態や、RandomDeck（ランダムデッキ）のフラグは使わない。
通常キャラの直接選択は確定前後のIDが一致した場合にだけfalseとする。
選択完了後に起動し、既に実キャラへ置換されていた場合はnull（未観測）とする。

読み取り時にシーン・ポインター・選択段階を再確認し、遷移中の不整合な値は捨てる。
プロセス/相手/立場の変更、ネット対戦からの離脱、観戦でも破棄する。
次の対戦開始時に、既存の試合IDへ選択情報を一度だけ結び付ける。
KOや一瞬のキャラ選択への戻りで、そのIDの情報を変更しない。

参照した公開定義・選択確定処理:
[SokuLib Scenes.hpp](https://github.com/SokuDev/SokuLib/blob/master/src/Scenes.hpp)、
[SokuLib SokuAddresses.hpp](https://github.com/SokuDev/SokuLib/blob/master/src/SokuAddresses.hpp)、
[InfiniteDecks selectProcessCommon](https://github.com/SokuDev/SokuMods/blob/master/modules/InfiniteDecks/main.cpp)。
メモリ読取・遷移はモックで検証するが、実ゲーム/Giuroll/Wine上の確認は別途必要。
選択確定の観測を取り逃した試合にはランダム情報を補完しない。

## API・新旧互換性

`GET /matches/protocol` は `{"report_version":2,"random_selection":true}` を返す。
選択情報を持つ新クライアントはこの対応フラグを確認する。未対応サーバーには
その報告を送らずローカルに保持する（情報を落として送信しない）。

`POST /posts/result` / `POST /matches/report` の入力 `host_char` / `guest_char` は
**従来どおり実キャラID**。`report_version:2` の場合に以下の任意項目を追加できる。
ランダムを選んだという理由で入力キャラIDを20に置き換えない。

```json
{
  "host_char": 0,
  "guest_char": 5,
  "host_random": true,
  "guest_random": false
}
```

上記は既存の試合ID・勝敗・時間・スコア等に追加する部分フィールド例。
フラグはJSONのbooleanまたはnull（省略時null）。文字列や数値は422。
選択情報をv1報告に付けた場合も422。
`POST /matches/sync` は従来どおり `my_char` / `opp_char` に実キャラを送り、
追加フラグは視点変換せず `host_random` / `guest_random` のまま送る。

結果の照合は実キャラID・勝敗・スコア・参加者等で行い、任意フラグは一致条件に含めない。
参加者本人の観測を優先し、本人が未観測/旧版なら相手の観測を補完に使う。
両者不明なら従来どおり実キャラとして記録する。実キャラが不明ならランダムを確定しない。
確定時に選択キャラを20に変換し、同一トランザクションでPhレートを更新する。
最初に永続化した各報告のフラグを使い、再送のフラグ変更で戦績やレートを変更しない。

`GET /stats/me/matches`、`GET /matches/reports` の確定 `match`、
`GET /replays/search` の各行では `host_char` / `guest_char` は**記録上の選択キャラ**。
加えて `host_actual_char` / `guest_actual_char`（integer|null）と
`host_random` / `guest_random`（boolean|null）を返す。
入力と出力のキャラIDの意味は区別する。クライアントの再送には実キャラ列を使う。

ここでいう旧版互換は、試合ID方式（v2）対応・選択フラグ未対応のクライアントとの互換。
試合IDのない旧報告の保留ルールは[従来のまま](match-identity.md)。

## 更新・過去データ

DB移行0020は実キャラ・観測フラグのnullable列を追加するだけ。
既存データからランダム選択を推定したり、戦績・レートを遡及変更したりしない。
サーバーを更新・移行してから対応クライアントを配布する。
ローカルSQLiteも起動時に不足列を追加し、過去行は変更しない。
スキーマを0019へ戻す場合は、記録のキャラIDを保持した実キャラへ戻すが、
レートの再計算は行わない。運用上のロールバックには別途レートの整合性確認が必要。
