# M3 ws-8(フロントエンド 周辺2画面)実行報告

- ブランチ: m3-ws-8 / ベース: 61c9660
- 日付: 2026-10-02
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | npm test | PASS | `Test Files 23 passed (23) / Tests 217 passed (217)`(170+47。要所の既存3ファイル latch-shell・smoke・latch-detail も個別実行で 23 passed を確認) |
| 2 | npm run build | PASS | `✓ built in 235ms`・exit 0(dist生成) |
| 3 | テストbasename衝突なし | PASS | `ls frontend/tests/*.test.js \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 出力空(23ファイル) |
| 4 | backend/prototype/docs差分ゼロ | PASS | `git diff --name-only main -- backend prototype docs` → 出力なし |
| 5 | 変更ファイル=§4の17ファイル | PASS | `git diff --name-only main \| sort` → frontend新規9+変更8+report.md=18ファイル(下記コミット一覧時点ではreport抜け17・報告コミット後に18へ一致) |

検証時の正確な出力末尾(Task 8実施分):

```text
$ cd frontend && npm test
 Test Files  23 passed (23)
      Tests  217 passed (217)
   Duration  1.21s

$ cd frontend && npm run build
dist/client/assets/index-QlrbVJYN.js  45.78 kB │ gzip: 15.36 kB
✓ built in 235ms
(exit 0)

$ ls frontend/tests/*.test.js | awk -F/ '{print $NF}' | sort | uniq -d
(出力なし・衝突ゼロ)

$ git diff --name-only main -- backend prototype docs
(出力なし)

$ git diff --name-only main | sort
frontend/index.html
frontend/src/latch/blockFlow.js
frontend/src/latch/blocks.js
frontend/src/latch/detail.js
frontend/src/latch/notice.js
frontend/src/latch/permission.js
frontend/src/latch/settings.js
frontend/src/latch/texts.js
frontend/src/latch/view.js
frontend/src/main.js
frontend/src/router.js
frontend/styles.css
frontend/tests/latch-block-flow.test.js
frontend/tests/latch-blocks.test.js
frontend/tests/latch-notice.test.js
frontend/tests/latch-settings.test.js
frontend/tests/router.test.js
docs/plans/M3/ws-8-report.md   ← 報告コミットで追加(計18ファイル)
```

完了条件6(03 §7の一文統一がCLOSED_TEXT単一参照で担保): latch-notice.test.js の終了行試験は `import { CLOSED_TEXT } from "../src/latch/texts.js"` した上で `expect(...).toEqual([CLOSED_TEXT])` により期待値を定数参照(文言の直書きでない)。design §6-3どおり。

## 固定値の変更有無(design.md §2・本計画§2)
- お知らせ行マトリクス(§2-2): 変更なし
- 既読化の規則「開いた=見た」+POST read(§2-2): 変更なし
- 通知許可4状態(§2-5): 変更なし
- ブロック解除確認モーダル・404収束(§2-6): 変更なし
- ブロック登録導線=成立済み詳細のみ(§2-7・§5-1承認): 変更なし
- その他: 変更なし(§9-6の適合措置3件=計画書既定の範囲内で実装。下記引継ぎ欄に記録)

## (スーパーバイザー・ws-9/G3への引継ぎ)
- §9-6の適合措置の実施記録: ①`#unblockModal` をindex.htmlへ追補した(design §3列挙になかったが§2.6必須の解除確認モーダルbackdrop・reportModalと同型) ②`noticeTimeText` は `jstParts` 流用の「M月D日 HH:MM」(年なし・時分zero埋め)としてview.jsへ実装(ブロック日表示にも流用) ③notification-dotへ `id="notificationDot"`+初期`hidden`を付与(CSSのdisplay規定なしのため`[hidden]`が有効)
- tokenConnect成功直後はpreloadを呼ばない(design規定外 — 次のpopoverオープンで取得)
- G3完了条件「プロトタイプ6画面」が本単位の完了で揃った旨(ホーム・提案詳細・成立済み詳細・お知らせ一覧・設定。実装基準はprototype/・実機確認は下記スモーク)
- Task 1のRED確認時の補足: 追記3件のうち`#/settings/`回帰ピン1件は現行実装でも当初からPASS(既存動作のピン留めのため)。`#/settings`解析2件がFAILした時点でRED成立と判断し実装へ進んだ(計画書Expectedの「追加3件がFAIL」に対し実測2 FAIL+1 PASS・失敗理由は期待どおりparseHashの機能欠如)

## 実機スモーク手順(スーパーバイザー検証用・agent3は実施しない — design §6-2)
1. mainへマージ後: `docker compose build api && make up`(api再ビルド必須・STATUS運用ル則4)
2. `cd frontend && npm install && npm run build && npm run preview`(dist配信・localhost:4173)
3. トークンパネルへIdPトークン(`cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject <subject>`)を貼る
4. 事前データ(backend内部CLIまたはAPI直叩きで作成 — 例: 2ユーザー×Intent2件からlatchを直接INSERTしnotifications行を生成):
   - ①通知fixture(proposed 1件+hidden 1件+nearby 1件+attendance_request 1件)で起動時ドット表示→popoverを開く→行が§2.3マトリクスどおり・nearby行はリンクなし
   - ②開いた後ドットが消灯し、再取得でread_atが反映(既読化)
   - ③終了済みlatch(cancelled等)の通知行が「この提案は成立しませんでした」で、タップで詳細の不成立姿
   - ④設定(`#/settings`)がaccountPopoverの「設定」から開く。通知許可の状態が表示され、default状態で[通知の許可を求める]が機能する(ブラウザの許可ダイアログ)
   - ⑤ブロック登録(API直叩き: `POST /v1/users/{id}/block`)→設定の一覧に表示名が並ぶ→確認モーダル→解除で行が消える
   - ⑥成立済み詳細からブロック登録→トースト「ブロックしました」→チャットが読取専用(「このチャットは利用できません」)へ収束

## コミット一覧
```text
$ git log --oneline main..HEAD
<このコミット(docs: add M3 ws-8 execution report)>← Task 9
ba5fada feat: add settings screen, notice container, and block entry wiring (M3 ws-8)
f2f12b5 feat: add block registration modal flow for matched detail (M3 ws-8)
164efcd feat: add block management section with confirm modal and 404 convergence (M3 ws-8)
d83572f feat: add notification permission control with DI (M3 ws-8)
863c52c feat: add notice popover flow with unread dot and mark-read (M3 ws-8)
1879bd2 feat: add notification row text matrix and new screen constants (M3 ws-8)
4891b58 feat: add #/settings route to hash router (M3 ws-8)
```

## 補足(詰まった点・判断した点があれば)
- TDDは全TaskでRed→Greenを確認してから実装(Task 1のRED時の内訳は上記引継ぎ欄のとおり2 FAIL+1 PASS。Task 2〜6は新規ファイルのimportエラー/関数不在で全件RED→実装でGREEN)
- Task 7のdetail.js編集時に一度 `renderClosed` の定義行を誤削除したが、直後の`node --check`とファイル確認で発見し即時復元(コミット前の修正のため影響なし。`npm test` 全緑で確認済み)
- スキップした計画書項目なし。仕様矛盾は発見せず
