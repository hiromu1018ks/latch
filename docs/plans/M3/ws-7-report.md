# M3 ws-7(フロントエンド コア3画面)実行報告

- ブランチ: m3-ws-7 / ベース: c3541d3(マージベースは aabc60d=計画書コミット。main は c3541d3 まで進行)
- 日付: 2026-10-01〜02(01に実装・02に最終レビューfix。usage limitによる中断を挟む)
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | `265 files already formatted` `All checks passed!` / `1196 passed, 240 deselected in 7.26s`(期待1196=main 1188+unit 8・最終レビュー後も再確認) |
| 2 | npm test | PASS | `Test Files 19 passed (19)` `Tests 170 passed (170)`(計画期待167=既存81+新規86に対し+3: 最終レビューfixの回帰ピン3件=latch-chat+1・latch-view+1・latch-home+1。既存9ファイル全緑) |
| 3 | npm run build | PASS | `✓ built in 250ms`(dist/client/ 生成・exit 0) |
| 4 | テストbasename衝突なし | PASS | frontend `uniq -d` → 空 / backend `uniq -d` → 空(双方とも衝突なし) |
| 5 | alembic head=0006不変 | PASS | `0006 (head)` 単一 / `git diff --name-only main -- backend/alembic` → 空(出力なし) |
| 6 | 変更ファイル=§4の一覧どおり | PASS | マージベース(aabc60d)基準 `git diff --name-only` 30ファイル(frontend新規21+変更3・backend変更5・report 1)が§4と完全一致・`git status --short` 空。※計画§6の「計29ファイル(report込み)」は数え誤り(21+3+5=29はreport抜きの合計。report込みは30)・※`git diff main`(=c3541d3)基準では main側で更新された docs/plans/STATUS.md が逆差分として現れるが本単位の変更ではない(§5どおり無変更) |
| 7 | integration収集10件 | PASS | `10 tests collected in 0.01s`(test_1〜test_10・実行はスーパーバイザー) |
| 8 | test-ci | **スーパーバイザー検証待ち** | 実施せず(§0規律・共有ci-db保護)。期待 1436 passed(main 1427+unit 8+integration 1・ws-6分は別加算)・alembic head=0006・m3ws7-残存ゼロ(下記SQL) |

## 固定値の変更有無(design.md §2・本計画§2)
- 残時間書式の4区切り+「まもなく締切」(§2-2): 変更なし
- チャット30秒ポーリング・5頁上限・ローカル追記なし(§2-2): 変更なし(実装詳細の1点のみ修正: 再同期時の追記をid重複排除で行う — 計画書実装コードのconcatは終端到達後の再同期で二重連結するため。design §2.6「保持しているcursorから差分を追記」の意図どおり)
- attendance表示条件=status=completedのみ・窓判定は409へ一本化(§9-6①): 変更なし
- 案Xの検査順(§2-3): 変更なし
- その他: 変更あり(下記4点。いずれも計画書テストの意図を正として実装側を修正・テストは計画書どおり)
  1. router.jsのhash正規表現(§8 Task 5 Step 3): `[0-9a-fA-F]{8}-…`(任意バージョンUUID)→ v4形式(`4xxx`+`[89ab]`バリアント)。前: 計画書のテスト期待「11111111-1111-1111-1111-111111111111はuuid形式不正=ホーム」にマッチしない。後: v4形式のみ詳細画川面へ。理由: backendのID発行はuuid4のみ・design §2.1「uuid形式不正はホームへ」のテスト側意図を尊重
  2. chat.jsのsync(§8 Task 8 Step 3): `messages = messages.concat(data.items)` → id重複排除の追記。理由: backendのcursor仕様(cursorなし=最古頁)では終端到達後の再同期が最古頁から返り、concatでは同一メッセージが二重表示になる(テスト「送信はPOST後に再取得しローカル追記しない」が失敗)。**最終レビューでさらに精密化**(f79fc4d): ①messagesの先頭省略(500件上限)を保持側sliceから表示側(render)へ移す ②終端到達時はcursorを当該頁の取得位置へ戻してbreak — 次回の差分同期は「保持している最終頁のcursorから1頁」(design §2.6の文字通り。終端後の全頁再巡回=レート超過を解消)
  3. detail.jsの回答済み現況行(§8 Task 12 Step 3): `my_response==="yes" && groupNeedText(latch)`(is_group条件) → status=partial_accept+remaining_responses>0の条件へ(is_group不問)。理由: design §2.4「回答済み表示: yes: 『参加します』+partial_acceptなら『あとN人の回答が必要』」はstatusで規定しており、計画書テスト(1対1partial_acceptで文言期待)と矛盾していたためdesignを正とした
  4. 試験コード3箇所(§8 Task 7/8/11/10): `client.mock` → `client.call.mock`(ヘルパー戻り値は`{call: vi.fn(...)}`のため)。Task 10のselectReasonヘルパーはhappy-domがラジオ排他を実装しないため「全解除→選択」へ(テストの意図不変)

## (スーパーバイザー・G3・ws-8への引継ぎ)
- 詳細応答のcompleted_atは常にnull(ws-1実装の_page_view_of)。G3/ws-9での修正候補
- texts.jsの定数群をws-8が参照(CLOSED_TEXT等・一文統一の単一ソース)
- 成立済み詳細からのブロック導線の要否はws-8設計時に判断(design §5-2)
- お知らせpopover中身・未読ドット接続・Intent個別編集はws-8
- 1対1partial_acceptの回答済み表示で「あと1人の回答が必要」を出すことをdesign §2.4どおり実装(本単位の固定値変更3を参照)
- **最終レビュー(fresh reviewer)のdeferred minor**(ws-8/ws-9での対応候補):
  - appState.reset()の呼び出し経路がない(トークンパネルで別ユーザーへ貼り替えるとmeがstale・チャットの左右が反転)— design §2.6「再ログインで再取得」の配線欠落
  - 一覧のLatchSummary.proposalは成立後も最小形のまま(backend `_summary_from_page` がrow.proposalを素通し)=成立済みセクションのカードが「条件が合う候補があります」になる。根本はbackend一覧契約・フロントは対処不能
  - 詳細→ホーム遷移でチャットpoller・残時間タイマーが停止しない(別latchを開けば停止・上限1インスタンス)
  - detail.show()のcatch-allが一時エラー(初回messages GET失敗・401)でも「提案が見つかりません」へ置換する
  - グループpartial_accept回答済みで「あとN人の回答が必要」がbadgeとgroup-noteの2箇所に出る
  - 期限切れ直後の送信で429/422が返るとfinallyで3択が次の30秒tickまで再活性化
  - 通報モーダル: グループで対象未選択のまま送信すると一律エラー文言(「相手を選んでください」の案内なし)・Escapeで閉じない

## 実機スモーク手順(スーパーバイザー検証用・agent3は実施しない)
1. mainへマージ後: `docker compose build api && make up`(api再ビルド必須・STATUS運用ル則4)
2. `cd frontend && npm install && npm run build && npm run preview`(dist配信・localhost:4173)
3. トークンパネルへIdPトークン(`cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject <subject>`)を貼る(/v1/users登録済みのsubject)
4. ①ホームにLATCH候補・成立済みが表示(ない場合空状態) ②提案詳細でvisibility両形(hiddenは条件サマリなし)・一致度・残時間・グループ人数が表示され3択で回答 ③双方yesで成立済み詳細(チャット送受信・参加者・集合情報)へ遷移 ④completedでattendance回答 ⑤成立済み詳細から通報(reportee_id明示) ⑥提案詳細(1対1)から通報(latch_idのみ) ⑦409系(期限切れ回答・二重回答・閲覧専用チャット送信)が規定表示へ収束
5. test-ci後の残存確認(§7末尾のSQL・m3ws7-)

## コミット一覧
```
f79fc4d fix: keep chat order for 500+ messages and drop null area_name rows (M3 ws-7)
3df8ece docs: add M3 ws-7 execution report (M3 ws-7)
05bfd86 feat: wire home/detail screens with hash router and shell structure (M3 ws-7)
4a8102d feat: add latch detail with three status modes and report entry (M3 ws-7)
10fc23f feat: add home sections with status split and intent count (M3 ws-7)
a6dcd4d feat: add report flow with fixed/selected/omitted reportee forms (M3 ws-7)
a44fcb7 feat: add attendance flow switching display on 409 codes (M3 ws-7)
a578d4f feat: add chat with paged initial load and 30s polling (M3 ws-7)
2bfe57c fix: reference client.call.mock in respond flow test (M3 ws-7)
8400ce0 feat: add response flow with pending guard and 409 refetch (M3 ws-7)
f7e5832 feat: add app state holding current user id (M3 ws-7)
f722d19 fix: restrict latch route hash to uuid4 form (M3 ws-7)
6f3cd26 feat: add hash router with home/latch screen switching (M3 ws-7)
7523c88 feat: add latch display text constants and pure view functions (M3 ws-7)
9f01a9e test: add reportee resolution integration case (supervisor-run) (M3 ws-7)
4b80ed0 feat: make reports reportee_id optional in request schema (M3 ws-7)
7c7ff30 feat: allow reportee_id omission with latch participant resolution in report_user (M3 ws-7)
```

## 補足(詰まった点・判断した点)

### 計画書のテストと実装コードの矛盾4件(いずれもテストの意図を正として実装を修正・詳細は「固定値の変更有無」)
計画書にはテスト(Step 1)と実装コード(Step 3)の全文が記載されていたが、4箇所で両者が矛盾していた。いずれも design.md の規定・テストの意図を正として実装側を修正し、テストは計画書どおり作成した(上記「その他」1〜3)。

### 手順上の反省2件(記録)
- Task 5・Task 7でGREEN確認前にコミットしてしまった(1 failedを残したまま)。いずれも直後に失敗原因を分析してfixコミットで全緑化。以後のタスクはGREEN確認とコミットを段階分けして再発なし
- Task 5のfixコミットをledgerに誤ったハッシュ(7dc9b6b)で記録した(実際はf722d19)。即時訂正済み

### RED確認時の実装前パスについて
回帰ピン(現行挙動の保持を確認するテスト)は実装前からパスするのが自然で、次の5件が該当: test_report_explicit_path_unchanged(Task 1)・test_report_both_omitted_422(Task 2)・tokenPanel温存ピン(Task 13)。新規ロジックのテストはすべて期待どおりRED→GREENを確認済み。計画書各TaskのExpected記載(「追加N件すべてFAIL」)はこの点で不正確だったが、テスト側の問題ではないためテストは計画書どおりのまま。

### ruff format指摘(毎コミット検査)
Task 2(service.py)・Task 3(test_safety_api.py)で`ruff format --check`が整形差分を指摘。計画§0の指示どおり`uv run ruff format .`を適用後にlint再実行でグリーン。service.pyの整形差分はTask 2コミットへ同梱(論理変更なし・コミットメッセージに明記)。

### index.htmlの変更手法
「既存の.intent-editor/.deposit-settingsは1行も変更しない」規律を厳格に適用し、既存行のインデントは変更せず周囲に行を挿入する形にした(workspace section内でインデントが2レベル不揃いになるが、diff最小化を優先)。

### 最終レビュー(fresh reviewer・全ブランチ)
Task 15後にopusレビュアーへ全16コミットのレビューを依頼した。結果: **Critical 0・Important 2・Minor 9**(minorは引継ぎ欄へ記載済み・一部はI-1のfixで解消)。Review Focus 7項目(計画§3)はすべて実装・試験で担保されていることを第三者確認。Important 2件はTDD(RED→GREEN)でfixし回帰ピン3件を追加した(f79fc4d):
- I-1: 500件超チャットで差分同期のたびにメッセージ順序が崩れる(先頭省略のsliceと再巡回の相互作用)→ 上記「その他2」の修正
- I-2: area_name=nullの全フィールド版proposal(逆ジオコーディング不成立の到達可能状態)で条件サマリに「null」と表示される → conditionSummaryLinesのnull行除外+カード見出しのarea_name直参照化(行index依存の解消)

なお実装中にusage limitによる中断が1回あり、2026-10-02の再開指示でfix-2を継続完了した。
