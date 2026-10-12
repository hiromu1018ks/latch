# 発見事項(findings)— Phase 2時点 2026-10-12 / Phase 3追記あり / 環境基盤+P0パス追記あり

アプリバグの記録(known-failure)・false-green・重複クラスタ・観察。**バグを発見したら findings#N として追記し、該当テストはtest.fixme+参照をつける**。

## アプリケーションバグ(known-failure)

### findings#1 — 並行refresh競合によるセッションfamily失効(2026-10-12発見・**同日修正済み**)

- **再現手順**: accessなし+refresh保持のセッションでアプリを開く(新規タブ再訪・access期限後という正規の継続経路)。起動時に複数のAPI呼び出しが並行して401となり、それぞれ `refreshOnce`(frontend/src/api/client.js)を**単一フライト保護なし**に同時実行する。同一refreshトークンでの2回目の `POST /v1/auth/refresh` が回転ストアの再利用検知に掛かる
- **実API検証(2026-10-12)**: 同一refreshでの並行更新 → 片方200・他方401。**勝者側が受け取った正規の新トークンでの再試行も401**(=family全体が失効している)
- **影響**: 該当経由のユーザーは完全ログアウトし、保存済みrefreshも無効(refresh=localStorage 30日の継続設計と矛盾)。P1(コア継続性・認証)
- **修正(2026-10-12)**: `createClient` 内で進行中のrefresh promiseを共有する単一フライト化(`refreshShared`)。並行401は1回のrefreshに束ねられ、全呼び出しが回転後のaccessで再送される
- **検証**: 単体試験(client.test.js「並行401はrefresh 1回に束ねられる…」=368件緑)とE2E A5(accessなし再訪でstandby描画=36件緑・2回連続)で実API再現→修正→解消を確認

### findings#2 — お知らせpopoverの初回オープンが起動時スナップショットを使う(2026-10-12発見・**同日修正済み**)

- **再現手順**: アプリ起動(Headerのpreloadが `/v1/notifications` を1回取得・`preloaded=true`)→ **起動後に**提案・成立通知が届く → お知らせpopoverを**初めて**開く。`NoticePopover.open`(frontend/src/latch/NoticePopover.jsx:57-68)は `preloaded.current` がtrueなら取得をスキップし起動時のitems(=空)で描画する
- **実API検証(2026-10-12・staging E2E)**: UI預け入れ→実パイプラインで提案+成立通知がDBに存在(`GET /v1/notifications` で2件確認)しても、起動済みページのpopover初回オープンは「まだ新しい候補はありません」。2回目のオープン(開き直し)は再取得して正しく2行表示
- **影響**: 起動中に提案が届いたユーザーが最初のお知らせ確認で「来ていない」と認識する(未読ドットも起動時値のまま)。Header.jsx:63のコメント「popoverを開くたびに取得」と実装が矛盾。P1(提案到達の視認性=サービス核心)
- **修正(2026-10-12・V1-P1)**: `open()`からpreloaded分岐を削除し**常にGET**する( preloadはヘッダー未読ドット判定のみに使用・行描画には使わない)。Headerコメントと実装の整合を回復
- **検証**: 単体(latch-notice.test.jsx「preload後の初回openも必ずGETする…」=起動時0件→起動後に2件届く状態で初回オープンが2行描画・GET2回)とstaging実機(pipeline-ui.spec.js:84の「開き直し3回クリック」workaroundを撤去し初回オープン1回でproposal+成立の2行をassert)で修正を証明。vitest 368・staging E2E 5緑
- **備考**: hashのみ遷移ではページが再読み込みされない(同一文書遷移)ため、SPAを開き続ける通常使い方で起きていた

### findings#3 — ground-truth specのEXPIRESがJST 0:00〜4:59の実行で+7日超過422(2026-10-13発見・**同日修正済み**)

- **再現手順**: `tests/e2e-staging/ground-truth.spec.js` をJST 0:00〜4:59に実行する。fixtureの共通期限 `EXPIRES = D(7, "05:00")` は「JST実行日+7日の05:00」だが、実行時刻が05:00を回ると now+7日 の上限を最大4時間超える(保存検証の「現在+7日超は422」に抵触)。JST 05:00〜23:59の実行では diff≤7日 で通る
- **実API検証(2026-10-13 00:08 JST)**: staging E2E実行で `422 VALIDATION_ERROR "expires_at exceeds 7 days"`(ground-truth 1件のみ赤・他4件緑)。V1-P4時(深夜前の実行)は通過していたため潜在していたタイムボム
- **影響**: テスト資産側の欠陥(製品コードは正しく拒否している)。specが1日のうち5時間赤になる。P2(継続実行の信頼性)
- **修正(2026-10-13・V1-P5)**: EXPIRESを `D(6, "23:59")` へ変更(常に diff<7日 で時刻非依存。fixtureの開始時刻はD(2)〜D(5)のため期限として十分)
- **検証**: 修正後にstaging E2E 5件緑(00:2x JST実行=旧仕様の赤窓内での実証)

## False greens(疑似緑 — partial格下げの根拠)

| ID | テスト | 問題 | 影響cell |
|---|---|---|---|
| FG-01 | latch-shell.test.jsx:224-244 | バッジ表示の検証がテスト内Badgeコンポーネント(実CountPill=Header.jsx:49-58のロジック複製)頼み。実CountPillが非0件表示を壊しても緑のまま | J1 |
| FG-02 | latch-standby.test.jsx:172-191 | 取り下げ後のcounts検査が`chrome.current.counts`(コンテキスト内部値)で、Header/Tabbar実DOMバッジの検証ではない | J1・E3 state補強 |
| FG-03 | latch-notice.test.jsx:258-274 | 既読化後の再判定がitem.read_atローカルmutateのみでサーバ再取得なし。既読persistの実証ではない | J4 persist |
| FG-04 | latch-consent.test.jsx:102-114 | 「通常画面へ戻る」の証明がonAgreedスパイ呼び出しまで。SettingsScreen.jsx:44-46のhash遷移が壊れても緑 | B3 |
| FG-05 | smoke.test.js:199-213 | tabbar/a11yの検証がstyles.css文字列含有一致。実ビューポート描画を証明しない | J3 |
| FG-06 | confirm.test.jsx:256-260 | 編集モードのclientモックがフォールスルーでも同一`{intent}`を返す。GETパスの誤り(誤endpointへのGET)を検出不能 | D8(GETパス面) |
| FG-07 | latch-detail.test.jsx:190-193 | undo後refreshのモックが常にjoined latchを返す。**undo→open復帰表示が壊れても緑**(かつ復帰をassertするテストが全ファイルに存在しない) | G5 |

## 重複クラスタ(統合検討・削除・統合はユーザーの判断)

1. hash→section切替の3重: smoke.test.js:74-98 / latch-shell.test.jsx:15-41 / latch-shell.test.jsx:107-120 — 後者が非active unmountまで見る最強。前2つを集約検討
2. トークンパネル表示の2重: smoke.test.js:159-165 / latch-shell.test.jsx:122-126 — shell版が文言まで見る上位互換
3. D-04(+1換算)の3重: format.test.js:191-197 / format.test.js:271-283 / confirm.test.jsx:138-158
4. 保存リクエストbodyの2重: save.test.js:84-98(flow層) / confirm.test.jsx:105-136(UI層・網羅的)
5. チャット送信の2層: latch-chat.test.jsx:126-149 / latch-detail.test.jsx:615-639
6. undo POSTの3箇所: latch-detail.test.jsx:199-202, 279-282 / latch-respond.test.js:50-56
7. defer+reasons bodyの2箇所: latch-detail.test.jsx:247-249, 376-378 / latch-respond.test.js:36-42
8. 取り下げflowの3重: latch-standby.test.jsx:134-170(メイン)/172-191(counts)/348-379(他行)— 担当分離は意図的だがdialog文言検証は1箇所化可能
9. blocks空状態文言の3回: latch-blocks.test.jsx:53-58, 96-98, 136-138
10. skip link/a11y CSS検査の2重: smoke.test.js:199-213 / latch-shell.test.jsx:293-301

## 観察・仕様確認事項

- **OBS-01(A7)**: 未登録トークン保持者(交換済み・users行なし)の期待挙動が未定義。登録UIが存在せずPOST /v1/usersはAPI専用のため通常到達しないが、仕様として決める価値あり。W-01でwaive
- **OBS-02**: TokenPanel.jsx:19がfetchを注入不可で直参照しており、unitでパネルUI結合(接続・エラー文言)が書けない構造。G-03(E2E)が天然の証明場所
- **OBS-03**: HomeScreenのcomposer配線(型入力→parse→chips→CTA有効化)がtests全体で未証明(型入力fireEventがゼロ)。純関数層への分解テストのみ。G-02が最初の証明
- **OBS-04**: D-05で辞退(no)送信導線は廃止・回答2択は「YES/見送り(defer)」。matrix行G3はPhase 2で「my_response=noの受信表示」へ修正済み
- **OBS-05**: withdraw成功後に/v1/intentsを再取得しない(StandbyScreen.jsx:302はlatchesのみ)。取り下げ永続はE2Eでの再取得(G-06)で証明する
- **OBS-06**: E6トーストの「remountで出ない」がモジュール共有Set(toastShown・StandbyScreen.jsx:49)依存で、ファイル内テスト順序変更で偽陰性化しうる(現時点ではfalse-greenではない)
