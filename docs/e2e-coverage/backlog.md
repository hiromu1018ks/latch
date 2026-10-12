# E2E・検証バックログ(総洗い出し・Phase 4パス2 — 2026-10-12)

- 生成経緯: ユーザー指示「残り得るじゃなくてすべて洗い出せ」。スキルPhase 4(再分析)パス2として、受け入れ条件#1〜#27全行(docs/02)・FR抽出(docs/01・reviews)・パイプライン仕様(docs/06)・コスト保護(docs/04)・プライバシー(docs/08)・評価計画(docs/09)・NFR/リリース(docs/10・11)を総当たり走査し、既存資産(vitest 368・Playwright 36・backend unit/integration/contracts・staging S0〜S5+obs+nfr・load・G1/G2ゲート・cloudflare probe)との差分を列挙した
- 本書の位置づけ: `gaps.md` はUI振る舞い行列由来のギャップ(全17・実装済み)。**本書はそれより広い検証空間の完全インベントリ**である。優先度は P0(安全・セキュリティ・コンプラ直接)> P1(受け入れ条件に直結)> P2(品質・網羅の厚み)> P3(運用・将来)
- 凡例: 「性質」= ギャップの種別。**UI実機**(実ブラウザE2Eが必要)/ **実機**(実端末・実LLM・実障害)/ **自動化**(ハーネスを 作る必要)/ **実装欠落**(対象機能が存在せず検証不能)/ **要再実施**(資産はあるが条件付き)
- **[2026-10-12 実装パス追記]** 推奨着手順①+②(環境基盤E-01/E-03とP0群P-01/A-01/U-01/U-06)を実装済み。Status列に `done(spec)` を付記。稼働実績: ci 40 / staging 3 / cloudflare 2 = **45テスト緑**(vitest 368無影響)。成果物は `frontend/tests/e2e-staging/`・`frontend/tests/e2e-cloudflare/`・`frontend/scripts/cf-e2e-server.mjs`・ci側safety/proposal-minimal spec。実機発見: **findings#2(お知らせpopover初回オープンが起動時スナップショット)**

## 0. 大前提の構造的ギャップ(すべての項目に効く3つ+1)

| # | ギャップ | 影響 | 状態 |
|---|---|---|---|
| S-1 | **実パイプライン×UI結合が両軸に存在しない** — Playwright 36件のlatchは全てSQL直挿入(helpers.js insertPairLatch)でworker/Layer1〜5/Jevを経由しない。staging S0〜S5は実パイプラインだがUIなし | 「UIから預けたIntentが実workerで候補→提案→通知→04画面に届く」連鎖が一度も実機検証されていない | **解消(2026-10-12)**: staging=UI預け入れ→実worker提案→YES×2→成立チャット(e2e-staging/pipeline-ui.spec.js)・Cloudflare実装でも同チェーン(e2e-cloudflare/cf-smoke.spec.js) |
| S-2 | **UI E2EはPostgreSQL参照実装(compose+vite)のみ** — Cloudflare実装(本番デプロイ経路)・staging(nginx 18000)・loadのいずれにもUI E2E未接続 | デプロイ経路のUI結合・staging資産(seed/Clock)のUI活用が空白 | **解消(2026-10-12)**: cf-dev(miniflare 8787・build済みassets配信・専用persist `.cf-e2e-d1`)+staging(18000・vite proxy env化)をPlaywright第2/3projectへ(`LATCH_E2E_PROJECTS=ci,staging,cloudflare`) |
| S-3 | **LLMが常にstub** — 実LLMはG1/G2ゲート(手動・課金)のみ。composeはstub Jev既定0.5<閾値0.60のため実パイプラインでも提案が形成されない | 精度・実APIレイテンシ下の挙動がUIで未検証 | **一部解消**: staging(stub JEV noul0.90=スコア0.90)とcf(同0.9コード固定)は提案形成まで実機証明済み。実LLM(A-10/A-11)は残る |
| S-4 | **CIが存在しない**(GitHub Actionsなし) — 全試験がローカル手動実行 | 「もれなく」を継続的に保証する仕組みがない | **解消(2026-10-12・V1-P2)**: .github/workflows/ci.yml新設(ci E2E+vitest+backend全套+lint・pushで毎回実行。**実pushと初回実行確認はユーザー承認待ち**) |

## 1. 実行ターゲット・環境軸

| ID | 項目 | 性質 | 現状 | 検証方針案 | 優先 | Status |
|---|---|---|---|---|---|---|
| E-01 | Cloudflare実装(cf-dev 8787)UI E2E | UI実機 | 契約130ケースがAPI面のみ。UIゼロ(matrix §1.1) | Playwright第2project(webServer=`make cf-dev`+`--var LATCH_LLM_STUB_JEV_NOUL=0.85`)。D1ローカルmigration適用のsetupスクリプト化が必要 | **P1**(=旧提案A) | **done(e2e-cloudflare/cf-smoke.spec.js:13,42)** — webServer=`frontend/scripts/cf-e2e-server.mjs`(build+cf-sync+D1 migration+seed+pywrangler dev・persistは`.cf-e2e-d1`へ監視対象外)。stub JEVは0.9コード固定のため`--var`不要。同意導線+主幹作成+P-01チェーン(提案~5s・2回連続緑) |
| E-02 | Cloudflare実配備(workers.dev)UI E2E | 実機 | API側 `test_pipeline_remote_e2e.py`(LATCH_CF_REMOTE=1)のみ | 実配備URLへ同スペックのスモークproject(別承認) | P2 | — |
| E-03 | staging(18000)へUI E2E接続 | UI実機 | stagingはAPIハーネス専用 | vite proxy向き先をenv化し18000へ。seed/Clock/実workerをUI試験に使う(F-01の土台) | **P1** | **done(e2e-staging/pipeline-ui.spec.js)** — `LATCH_VITE_PROXY_TARGET`でvite proxy(5174)→18000。鍵=.staging/keys・DB=latch-staging-db-1をhelpersが対象切替。Clock=実時間保証(ensureStagingClockReal) |
| E-04 | load環境でのUI到達性 | UI実機 | loadgenはAPI直叩き | 省略可(API面で十分)だが記録として明記 | P3 | — |

## 2. 実パイプライン×UI結合(S-1の展開)

| ID | 項目 | 性質 | 現状 | 検証方針案 | 優先 | Status |
|---|---|---|---|---|---|---|
| P-01 | UI作成Intent→実形成→04画面→回答のフルチェーン | UI実機 | staging S0〜S5がAPI面で実施。UIなし | E-01/E-03いずれかの環境で「2ユーザー意図→debounce待ち→04が届く→YES→成立」数本(debounce 10s+pipeline込みで1本30〜60秒) | **P0**(=旧提案A/F1基礎) | **done(e2e-staging/pipeline-ui.spec.js:22,84 + e2e-cloudflare/cf-smoke.spec.js:42)** — staging=UI預け入れ(01入力→02行編集→預ける)×2→実worker提案(debounce+tick込み実測~15s)→04最小形→YES×2→成立チャット双方向+お知らせ2種。cf実装でも同一チェーン(Queues即時経路~5s)。D-05回答期限式(target-60min)により短時間スロットは+15分期限になる実挙動も観測 |
| P-02 | グループLATCH(3〜4人)のUI | UI実機 | staging/G3がAPI面で完備(#18)。UI完全空白 — 04の必要人数/残回答表示(partial_accept)・全員YES成立・通報対象ラジオ(N人)が未実機 | group fixture(intent 3つ+latch)で04/05 E2E。SQL直挿入でも即可能(実形成はP-01後) | **P1**(受け入れ#18) | **done(e2e/group.spec.js:12)** — insertGroupLatch(proposed)→Aの04(必要3人=まだ見えない2人)→YES→回答待ち2人(partial_accept)→B YES(1人が回答済み)→A再訪で現況→C YESでallIn「3人全員が参加しました。」→チャットを開く→05ヒーロー3人+メンバー3行(B/C/あなた)→latch=matched・Intent 3件matched化・成立通知3行(D-20同時送信)をDB側証明 (参考: 通報対象ラジオ2人選択はU-01でgroup fixtureにより実機証明済み=safety.spec.js:50) |
| P-03 | muted Intentの提案受信(通知なし・ホームの候補から回答可能) | UI実機 | staging S2#13・G3がAPI面。UI未実機 | notification_level=mutedなintentでlatch fixture→通知ドット増えない+候補一覧に出る | P2 |
| P-04 | nearby_also存在通知の受信体験 | **実装欠落+UI実機** | backend/仕様(03 v0.4)にあるがfrontendにUI設定経路なし(HomeScreenはDEFAULT固定・nearby文字不在) | 実装(設定導線)が先。E2Eはnotification fixtureのnearby行(既読G-12の拡張) | P3 |
| P-05 | 保留キュー→提示(D-08上限時の翈Bucket回し・期限再計算) | UI実機 | unit/integration/staging S1#8あり。UI体験なし | staging+Clock(E-03)で上限到達→時間経過→トースト/お知らせが出る | P2(FR-47) |
| P-06 | debounce 10s体感(更新連打→1回だけ候補トースト) | UI実機 | unit/integrationあり。UI×実workerなし | P-01環境で更新を2回→10s後に1回だけ通知 | P2 |
| P-07 | 更新(PATCH)→reeval再評価の成果がUIへ | UI実機 | staging S1#7・integrationあり | P-01環境内で条件変更→候補変化をUIで観測 | P2 |
| P-08 | expiry sweeper(60秒)の成果: 提案expiredで閉じる姿 | UI実機 | staging S3#16・integration。UIなし | staging+Clockで期限経過後04を開く→「この提案は成立しませんでした」 | **P1**(受け入れ#16 UI面) | **done(e2e-staging/expiry-ui.spec.js:56)** — U-19/T-01と同一シナリオ(response_deadline過去化→実sweeper 60秒tickのexpired化をwaitStatusで実測→04 closed) |
| P-09 | ブロック済み相手から提案が来ない(実パイプライン) | UI実機 | staging S5#20・hardfilter:368がAPI面(負の証明) | E-03でブロック→意図→候補に出ない。UI観測は「出ないこと」の待ち時間設計が必要 | P2 |
| P-10 | LLM失敗→フォールバック(Anthropic→TypeSafe)実API | 実機 | unit/integrationはエラー注入。実API切替は未実施 | 実LLMキーで障害注入は困難→フォールバック直接(jev-smoke FALLBACK=1)のUI接続はP3 | P3 |
| P-11 | サーキットブレーカ開放(実API)・D-16上限での縮退体感 | 実機 | unit/integration/staging n3完備 | 実APIでは再現困難。staging n3をUI接続(E-03)すれば「その夜提案が来ない」体験のみ可能 | P3 |
| P-12 | Redis障害503 DEPENDENCY_UNAVAILABLEのUI | UI実機 | unit(test_limiter:93等)のみ。stagingでRedis実停止試験なし | staging で redis stop→UIに503系文言。backend実施もなし | P2 |

## 3. マッチング精度・評価軸(docs/09。旧提案Fの展開)

| ID | 項目 | 性質 | 現状 | 検証方針案 | 優先 | Status |
|---|---|---|---|---|---|---|
| A-01 | グラウンドトゥルース精度検証(適合/不適合/境界のintent群→実パイプライン→正しい提案だけ届く両方向assert) | 自動化+UI実機 | staging S1が個別項目単位。**精度/再現の統括的assertは不存在** | 8〜12ユーザー×適合対/不適合対/境界(時間隣接・距離境界・人数・予算・visibility混在)のfixture定義→E-01/E-03で実行→UI側+#/proposalsで行列検証 | **P0** | **done(e2e-staging/ground-truth.spec.js:30)** — 14ユーザー×7対(適合2: summary_only×2=全形提案・visibility混在=最小形提案/不適合5: 時間+5h・距離2.4km>半径和2km・人数min3・予算ペア300<500・カテゴリ不一致)。正例2対は提案到達+proposal形状Assert・負例5対×両者はlatch 0件Assert(正例観測+grace後)・UI側は#/proposals行列(全形=照合表あり/混在=なし/不適合=空状態)。スロットは相互3h以上分離で交差マッチ排除 |
| A-02 | G2オフライン精度(precision@0.60=0.738・ECE 0.0988・AUC 0.751)の**合格基準確定** | 運用 | g2-jev-acceptance-criteria.md がオーナー承認待ち | 基準確定→make g2-gate を定期再実行(規律化) | **P1** |
| A-03 | MLR前期比−5pt判定のベースライン記録・自動化 | 自動化 | MLR算出はkpi.py/staging完備。前期比判定なし | kpi_dailyの前期差分と閾値判定を追加 | P2 |
| A-04 | segment別YES率の集計(仮説2: semantic≥10%かつlexicalの50%) | 自動化 | classify_segment(calibration.py:18)で記録済み・**集計/ダッシュボードなし** | calibration_recordsからsegment×回答の集計クエリ+KPI化 | P2 |
| A-05 | A/B基盤(D-01閾値0.60/0.50・D-08通知上限。割付・ペア適用・停止ルール) | **実装欠落** | frontend/backend全体に不在(ab_test|variant等ゼロ) | 基盤実装が先(09 §5)。E2Eは割付の再現性試験 | P2(実装後) |
| A-06 | 体感アンケート(仮説1/5: オンボ直後+4週後・5段階) | 実装+UI実機 | 実装済み(05 v0.7・2026-10-12 V1-P3) | 実装→E2Eで完結 | P3 | **done(仕様=05 v0.7・backend=latch/users/survey.py+users/routes.py+alembic 0010+obs/survey.py集計CLI・Cloudflare=migrations/0006_survey.sql+svc_users.py・UI=SurveyCard.jsx〔03待機中の受動カード〕+texts.js)** — 契約試験survey 10ケース(参照+D1両側緑)・integration test_survey_api.py 12件・unit 21件・E2E e2e/survey.spec.js:19,52,66(onboarding往復=active化後1問→送信→済み→再訪で出ないをDB証明/未配信=Intentなし+draftのみユーザーに非表示/week4=初回回答29日前SQL過去化→3問→answers+free_commentをDB証明)。集計は`cd backend && uv run python -m latch.obs.survey`(ラウンド別n・convenience平均・継続意向分布・仮説1/5判定フラグ) |
| A-07 | 1成立あたりAIコスト(通貨)KPI | 自動化 | METRIC_KEYSに不在(jev_usage_tokens_totalはある) | 換算レート挟んだ集計追加 | P2 |
| A-08 | C係数再計算(09 §4.4・四半期) | 運用 | C=1固定(test_latch_calc pin)・手順のみ | 運用規律化 | P3 |
| A-09 | G1(Parser構造化+飲酒)・プロンプト変更時の再実行規律 | 運用 | 手動ゲート・CI対象外 | 変更トリガの再実行手順を文書化+実行 | P2 |
| A-10 | 実Embedding(Gemini)検索品質・実TypeSafe JevでのK_j配分 | 実機 | embed-smoke/jev-smoke/G2の手動のみ | LATCH_LLM_MODE=realでの限定スモーク(課金・別承認) | P3 |
| A-11 | stub vs 実LLMの挙動差測定(遅延・分布) | 実機 | loadはstub遅延注入のみ | 実APIレイテンシ記録を取って分布更新 | P3 |

## 4. 時間進行・Clock軸(旧提案Bの展開。staging ControlledClock活用)

| ID | 項目 | 性質 | 現状 | 検証方針案 | 優先 | Status |
|---|---|---|---|---|---|---|
| T-01 | 回答期限経過→「締切」表示→候補静かに消える | UI実機 | response.spec.js:59 はSQLで期限を過去化したundo 409のみ | staging+Clock(E-03)。期限切れ04実描画 | **P1** | **done(e2e-staging/expiry-ui.spec.js:56)** — U-19と同一シナリオで実施(期限過去化はSQL・Clockでなく。04=タイマー0:00+回答2択disabled→実sweeper expired化→03から自分のlatch行が消失) |
| T-02 | Intent expires_at経過→expired・03からの消失 | UI実機 | staging S3#16・integration expiry_batches。UIなし | 同上 | **P1**(受け入れ#4後半) | **done(e2e-staging/expiry-ui.spec.js:116)** — expires_atをSQL過去化→実sweeper(60秒tick)の_expire_intent→03待機中から消失をreloadで観測 |
| T-03 | attendance申告窓(3日)の開閉(窓超過で非表示) | UI実機 | vitest H8(WINDOW_CLOSED)のみ | staging+Clockでcompleted→3日経過 | P2 | |
| T-04 | completed遷移(対象時刻経過)→チャット「対象時刻を過ぎたため…」読取専用 | UI実機 | vitest H4②のみ | staging+Clock | P2 | |
| T-05 | JST日跨ぎ(0時リセット・日次/時/分レート制限バケット・RetentionJob) | UI実機 | unit test_jst_boundary・n3の0時リセット | staging+Clock 0時跨ぎシナリオ | P3 | |
| T-06 | 30分Bucket再評価・catch-upのUI面(期限2時間前の再提示) | UI実機 | staging n4完備(API) | E-03でClock前進→再提示観測 | P3 | |
| T-07 | チャット30秒ポーリング・通知5秒以内到達 | UI実機 | vitest(W-03)・load notify p95=4.92s | P-01環境内で経過観測(任意) | P3 | |

## 5. UI機能の残り(受け入れ#1〜#27とFRから。旧提案C+エージェント1の追加)

| ID | 項目 | 性質 | 現状 | 検証方針案 | 優先 | Status |
|---|---|---|---|---|---|---|
| U-01 | **通報dialogの実機E2E**(対象ラジオ→理由4択→POST) | UI実機 | vitest latch-safety:96 のみ。**docs/02の検証方法が唯一UI操作を明記するSafety項目(#21)なのにE2Eゼロ** | matched fixture→「通報・ブロック」→dialog完走 | **P0** | **done(e2e/safety.spec.js:20,50,80)** — 3形態: ①1対1成立=対象固定checked→理由4択→POST(reportee=相手をDB側証明) ②グループ成立(group_candidates+latches直接INSERT・3人)=対象ラジオ2人から選択(未選択で送信不可)→選択者がreportee ③1対1提案(04)=対象ラジオなし・reportee_id省略(案X)。POSTは受付のみのためreports表の行で証明 |
| U-02 | ブロックdialog(H10)の実機(提案/成立画面起点) | UI実機 | vitestのみ。E2Eは設定解除(G-14)のみ | matched fixture→dialog→POST→ブロック一覧へ | **P1**(#20 UI面) | **done(e2e/block.spec.js:10)** — 05成立起点で「通報・ブロック」→対象固定checked→「ブロックする」→完了表示(「今後この相手とLATCHが成立することはありません」)→blocks表1行をDB側証明+設定のブロック一覧へ反映(表示名行+「さんのブロックを解除」導線)。提案(04)起点のブロック導線は存在しない(participants非開示=safety.spec.js:90で実証済み)のため成立起点が全範囲 |
| U-03 | 成立後ブロック→チャット読取専用(D-23) | UI実機 | staging S4 D-23(API)・vitest | U-02の延長 | P2 |
| U-04 | attendance happyの実機(2択→POST→済み) | UI実機 | vitest H7/H8のみ(gaps W-04関連でE2E対象外とした) | completed fixture→申告 | **P1**(原則6) | **done(e2e/attendance.spec.js:13)** — insertCompletedLatch(latch=completed+completed_at+calibration_records直投入)→05の出席申告2択→「会いました」→POST→「回答を送りました」済み表示(2択消滅)+calibration_records.actual_attended=trueをDB側証明 |
| U-05 | 両者YES→matched遷移の2ブラウザ連鎖 | UI実機 | response.specは片方YESまで。成立姿はfixture | 2コンテキストで順にYES→双方05へ | **P1**(#15) | **done(e2e/response.spec.js:19)** — pairLatchFixture+openAuthed(userA/userB)の2コンテキストで順にYES→B「全員が参加しました。」→チャットを開く→05・Aは再訪で05到達 |
| U-06 | hidden_until_match最小形04画面(照合表なし・一致度/期限/必要人数のみ) | UI実機 | E2E fixtureはsummary_only全形のみ。**実運用の既定姿が未実機** | proposalを{headcount,match_level}のみのfixture→最小形assert | **P0**(受け入れ#17・原則8) | **done(e2e/proposal-minimal.spec.js:11 + e2e-staging/pipeline-ui.spec.js:22)** — SQL fixture(p proposal={headcount,match_level})で04=照合表・4情報カードなし/非開示注記/まだ見えない1人/回答期限/YES可をAssert+実パイプライン版(UI預け入れはhidden確定送信のため実workerの提案が自動的に最小形=提案形状をtoEqualで証明) |
| U-07 | visibility 2値比較(同一候補をsummary_only/hiddenで作り表示差assert)+ raw_text/名前/NG条件/座標の**否定的assert** | UI実機 | G3/stagingがAPI面(#22/#23)。UI 2値比較ゼロ | 2 fixture並列描画で差分+非表示検査 | **P1**(#22/#23) | **done(e2e/visibility.spec.js:38)** — 同一条件の候補をsummary_only(全形)/hidden_until_match(最小形)で配置(同一Intent対へのopen latch二重挿入はuq_latches_intent_ids_openで禁止のため別ペアA+B/A+C)→04差分(全形=4情報カード+照合表+エリア/最小形=「条件が合う候補があります」+期限+必要人数)・**raw_text/表示名/NG条件/座標(geo中心実値+丸め4/6桁)が両形で画面に出ないことを否定的assert**+一覧のサマリ行差分(hrefスコープ) |
| U-08 | 18〜19歳ユーザーのUI作成拒否(422 UNDER_AGE表示) | UI実機 | staging S5 D-10・G1 alcoholがAPI/LLM面。UIゼロ | helpersのbirth_date調整で alcohol系intent保存 | **P1**(#27) | **done(e2e/errors-and-settings.spec.js:99)** — provisionUserへbirthDate拡張(2008-06-15=18歳)。ciのstub Parserはalcohol=false固定のためparse応答のみroute書き換え(429テストと同手法)でalcohol=trueに・サーバ422 UNDER_AGEと全体note表示は本物 |
| U-09 | 下書き→「直して預ける」→預けるのUI完走(draft→active化) | UI実機 | C5は行表示まで。#1の検証方法に明記の経路 | 03下書き行→02→預ける→active化 | **P1**(#1) | **done(e2e/main-flow.spec.js:78)** — 下書き保存→03下書き行→「を直して預ける」(#/confirm?intent=)→02編集(時間/場所/人数確認/期限)→預ける(PATCH status=active)→03待機中へ移り下書き欄から消失 |
| U-10 | NG条件注意表示「この条件は確実には除外できません」(D-04降格) | UI実機 | vitest(state/confirm)のみ | NG条件入り入力→02の注意文表示 | P2 |
| U-11 | Parser失敗→構造化フォームフォールバック遷移(FR-43/D-17) | UI実機 | vitest applyParseFallback純関数のみ | route注入でparse 503→フォールバックUI遷移 | P2 |
| U-12 | 通知許可トグルの実機(権限4状態) | UI実機 | vitest latch-settingsのみ | contextにpermission権限を与えてgranted/denied | P2 |
| U-13 | 実レート制限429(APIを61連打→UI専用文言) | UI実機 | E2Eはroute注入(G-09/G-10)。phase7持ち帰り「safety 429」未再確認 | 1ユーザーでAPIバースト→UI操作で429文言 | P2 |
| U-14 | Intent作成20/日・更新6/時・active上限5(422 ACTIVE_INTENT_LIMIT)のUI表示 | UI実機 | integration test_ratelimit_api完備。UI未 | バーストまたはSQLでカウンタ投入→保存時の表示 | P2 |
| U-15 | 一覧追頁(>20件)3種(latches/notifications/blocks) | UI実機 | W-02 waive。SQLで21件作れば安価 | fixture投入→「もっと見る」 | P2 |
| U-16 | 5条件行編集の実機網羅(時間・予算・目的行。現状人数/場所のみ) | UI実機 | vitest conditions完備・E2Eは2行 | 主幹フロー内で全行触る | P3 |
| U-17 | 典型表現10件以上セットでの入力→保存完走(#1の検証方法の字義) | UI実機 | E2Eは2文。G1(32+3件)が精度面で担う設計 | suite化するかG1で十分の明記判断 | P3 |
| U-18 | GEOCODING_FAILEDの実機(地名範囲外→場所行エラー) | UI実機 | vitest confirm.test(D6)。E2E未遭遇(地名制約内のため) | 「東京駅」のまま保存→422表示 | P2 |
| U-19 | 期限切れ提案04の実描画(expired/closed姿) | UI実機 | vitest 4状態のみ | SQLでstatus過去化 or T-01 | **P1**(#16) | **done(e2e-staging/expiry-ui.spec.js:56)** — T-01/P-08と同一シナリオ(実sweeperのexpired化後・04=#detailScreenのclosed card「この提案は成立しませんでした」) |
| U-20 | 解散(相手leave/退会)後の自分UI(「成立しませんませんでした」・読取専用) | UI実機 | staging S4 D-23(API)。UIなし | 相手がleave→自分の05がCLOSED/読取専用 | P2 |
| U-21 | 退会(DELETE users/me)実行後のUI(セッション消失・データ消滅) | UI実機 | UIに退会導線なし(API専用)=境界確認事項 | API退会→UI再訪でパネル。境界L-08参照 | P3 |
| U-22 | 通知3種×既読/未読のUI行列(attendance_request行) | UI実機 | vitest行列・G-12はproposal行のみ | notification fixture 3種 | P2 |
| U-23 | お知らせ2文面(summary_onlyサマリ付き/hidden文言なし)のUI | UI実機 | staging fr21(API・バイト一致)。UI未 | fixture 2種でpopover文言比較 | P2(FR-21表示面) |

## 6. プライバシー・安全(08。U-06/07と重複するものは除く)

| ID | 項目 | 性質 | 現状 | 検証方針案 | 優先 |
|---|---|---|---|---|---|
| V-01 | FCM実送信とOS通知経路(ロック画面・通知履歴)の実端末表示 | 実機+**実装欠落**(frontendにWeb Push/FCM登録コードなし) | backendはドライラン記録のみ | 実装(プッシュ登録)→実機端末試験。E2Eでは原理的に不可と明記 | P2(実装後) |
| V-02 | D-13第二段の月次匿名化バッチ | **実装欠落** | deletion.py:49にコメントのみ。仕様(08 §3.4)未実装 | 実装が先。10 §4相当試験も不可能状態 | P2(実装後) |
| V-03 | 通報理由の被通報者非開示(実機再確認) | UI実機 | API面(integration)で非開示担保 | U-01の延長で相手画面に何も出ないassert | P2 |
| V-04 | 同意文面と08 D-14要件の突合(送信先名称・学習禁止・再確認導線) | 運用 | E2E consent.specは挙動のみ。文言突合は明示手順なし | 文言レビュー記録化 | P3 |

## 7. レート制限・コスト保護・縮退(04)

| ID | 項目 | 性質 | 現状 | 検証方針案 | 優先 |
|---|---|---|---|---|---|
| C-01 | D-16 alertの宛先・エスカレーション | 運用(実装欠落) | 構造化ログのみ(04:148で運用設計に委ねる) | 宛先実装(メール等) | P2 |
| C-02 | 実TypeSafe/Anthropic障害時のbreaker実証 | 実機 | unit/integrationの注入のみ | 実APIでは再現困難→jev-smoke系で限定 | P3 |
| C-03 | 認証系レート制限(provider+subject 60/分)の実発火 | UI実機 | integration test_ratelimit_api(429)あり | U-13と同時にauth/tokenバースト | P3 |

## 8. 認証・セキュリティ軸

| ID | 項目 | 性質 | 現状 | 検証方針案 | 優先 |
|---|---|---|---|---|---|
| X-01 | 他リソースへの横断(intent/notifications/blocks/messages の他人URL直叩リ) | UI実機 | G-01はlatchのみ。API面は契約(outsider)完備 | latch以外はURL直叩きUIが存在しない=API面で十分の明記、またはAPI経由での否認 | P3 |
| X-02 | logout後access失効(JWT失効リスト)のUI | UI実機 | 契約・unit。logout UIなし(API専用) | API logout→同accessでUI操作→401パネル | P2 |
| X-03 | XSS実ブラウザ確認(raw_text・チャット・表示名のテキストノード化) | UI実機 | vitestでエスケースassert済み | fixtureにscriptタグ込み→実ブラウザで非実行 | P2 |
| X-04 | 実IdP(Google/Apple)経路 | 実機 | staging専用鍵で代置(10 §1どおり) | 後続段階(04 v0.4)と明記 | P3 |

## 9. UI品質・環境の端(旧提案D)

| ID | 項目 | 性質 | 現状 | 検証方針案 | 優先 |
|---|---|---|---|---|---|
| Q-01 | firefox/webkitクロスブラウザ | UI実機 | chromiumのみ | `npx playwright install firefox webkit`+projects追加 | P2 |
| Q-02 | キーボードのみ操作(dialogのEsc・Tab順・focus管理) | UI実機 | a11y静的検査はvitest(latch-shell) | 実キー操作E2E | P2 |
| Q-03 | axe-core自動a11y検査(全画面) | 自動化 | なし | @axe-core/playwright導入・違反ゼロpin | P2 |
| Q-04 | 視覚リグレッション(夜と翡翠デザインの漂移) | 自動化 | なし(手動390pxキャプチャのみ) | toHaveScreenshot(閾値運用) | P3 |
| Q-05 | FCP/LCP等UI体感の計測ハーネス | 自動化 | なし | lighthouse-ci or Playwright trace中心の簡易計測 | P3 |
| Q-06 | モバイル幅群(390/720境界・横画面)・タッチ操作 | UI実機 | misc.spec 390pxのみ | viewport行列化 | P3 |
| Q-07 | 遅延3G等のネットワーク制約下UI | UI実機 | なし | context CPU/ネットワークスロットリング | P3 |

## 10. オブザーバビリティ・運用(旧提案E+ws8Follow)

| ID | 項目 | 性質 | 現状 | 検証方針案 | 優先 |
|---|---|---|---|---|---|
| O-01 | CI(GitHub Actions)への載せ | 自動化 | なし(S-4) | compose services+playwright+make test のworkflow | **P1** | **done(.github/workflows/ci.yml・**実pushは承認待ち**)** — 第一段階=backend全套(make test-ci・LATCH_LLM_MODE=stub明示)+backend lint+frontend vitest+build+ci E2Eを1jobで。**順序=geo fixture投入→test-ci(クリーンDB)→geo再投入→E2E**(test_expiry_batchesの計数assertがDB残存で崩れる=matrix §9.2-5・geofeatures空だと契約/intent系が422で大量失敗=§9.2-6をドライランで発見)。geofeaturesはbackend試験fixture(isj_sample+osm_sample・天文館を含む)をlatch.geo CLIで投入(実データの大容量DL不使用)・load DB(massload前提)もworkflow内で起動+マイグレーション。staging/cf projectは対象外(実環境・鍵依存のため契機実行・手動維持)。actionlint 1.7.12静的確認済み+ローカルでコマンド列ドライラン(順序欠陥2件を発見修正のうえ確定シーケンスで緑)。**workflowの実行確認はpushを伴うためユーザー指示待ち(人間領域の別承認)** |
| O-02 | 初期LATCH判定p95 10秒の**条件付き再計測**(D-16枯渇下の記録のみ・activate経路null) | 要再実施 | ws6-load証拠 | 予算枯渇なしでloadperf再実行 | **P1** |
| O-03 | Phase 0(内部α)統合受け入れラン(11の7チェックを一括) | 要再実施 | 各証拠は揃ってるが統合ラン未実施 | staging-reset→全ハーネス→E2Eの順で回す手順化 | P2 |
| O-04 | ws8が発見したロールバック手順書の不備修正(build サービス名・nginx 502→restart・文言) | 運用 | walkthroughのfindings止まり | docs/plans/M4/ws-8-rollback.md更新+再演習 | P2 |
| O-05 | 重大事故時の即時停止判断フロー演習 | 運用 | なし | ドライラン手順化 | P3 |
| O-06 | D-02警報線(YES率<10%・通知オフ率週比2倍)の自動検知 | **実装欠落** | kpiは算出するが監視なし | 閾値監視+通知 | P2(実装後) |
| O-07 | E2E実行がsessions計測/KPIに乗ることの確認 | UI実機 | obs sessions APIは実装済み | E2E中にPOST /v1/sessions発火をnetwork観測 | P3 |
| O-08 | 録画ギャラリーの長期運用(容量・保持3件の確認) | 運用 | 導入済(9fdf48e) | 運用観察 | P3 |
| O-09 | cleanup残存データ(e2eユーザー/latch)の蓄積監視 | 運用 | ベストエフォート(catch付き) | 定期的な件数確認かプレフィックス掃除タスク | P3 |

## 11. 境界確認観点(誤ってE2Eを書いてはいけないもの — docs/02「作らない」と廃止宣言)

1. 決済・店舗予約・自動購入・在庫連携・Recommendation Feed・広告・自動交渉 — route不存在を契約が担保。E2E不要
2. friendships/friends_only/friend_of_friend — `/v1/friends`不存在・visibilityは2値のみ。**friends系E2Eを書かない**
3. 所属情報の収集・所属Hard Filter — 登録フィールド3つのみ・hardfilter系統に所属なし
4. ユーザーのミュート・スパム/不適合検知 — 実装なし(notification_level=mutedはIntent単位の別物・混同注意)
5. 自前パスワード認証 — TokenPanelは開発導線(本番IdP代替)
6. visibility/notification_levelの**UI切替導線**(D-02廃止) — UIは常にhidden_until_match/proposals_onlyで確定送信。**E2E fixtureがsummary_only形を使うのは「API側で設定された提案の表示検証」として正当**(02:122がAPI直叩きと明記)・逆にUIから切替操作を書かない
7. 「辞退する(no)」UI導線(D-05廃止) — 回答2択はyes/deferのみ。noはAPI直叩き(s2_item14実施済み)
8. 廃止UI要素(下書き確認モーダル・「Intent N件」popover)の不存在
9. 公開範囲違反での不成立経路 — Layer 1にvisibility判定なし(D-03再決定)。**visibilityがHard Filterで除外される試験を書かない**

## 12. 集計と推奨着手順

- 総項目数: **88**(S:4 / E:4 / P:12 / A:11 / T:7 / U:23 / V:4 / C:3 / X:4 / Q:7 / O:9)
- 性質別(主分類): UI実機 約40 / 自動化ハーネス要 約10 / 実機(実端末・実LLM・実障害) 約8 / **実装欠落(検証以前に機能なし)** 7(V-01/V-02/A-05/O-06/P-04/C-01/X-04の一部。**A-06は2026-10-12 V1-P3で実装+検証済み**)/ 要再実施・運用規律 約10
- 優先度別: **P0: 4**(P-01実パイプライン×UI・A-01グラウンドトゥルース精度・U-01通報dialog・U-06 hidden_until_match最小形)/ **P1: 16**(E-01・E-03・P-02・P-08・A-02・T-01・T-02・U-02・U-04・U-05・U-07・U-08・U-09・U-19・O-01・O-02)/ P2: 約35 / P3: 約33
- **推奨着手順**: ①環境基盤(E-01+E-03 → S-2/S-3解消) → ②P0群(P-01・A-01・U-01・U-06 → S-1解消・受け入れの最大穴) → ③P1のUI群(U-05/08/09/19+T-01/02) → ④O-01 CI載せ(以降の継続性) → ⑤残りP2 → ⑥実装欠落群(A-05/A-06/V-01/V-02/O-06/P-04)は製品判断を経て実装後に検証
- **[2026-10-12] ①+②実施済み**: E-01/E-03(環境基盤)+P0全4項目(P-01・A-01・U-01・U-06)がdone。S-1/S-2解消・S-3はstub条件下で解消(実LLM残る)。
- **[2026-10-12 V1-P1] ③実施済み+findings#2修正**: ③のP1 UI群5項目(U-05・U-08・U-09・U-19+T-01/P-08統合・T-02)がdone・**findings#2修正済み**(open()常時GET化+pipeline-ui初回オープン証明に転換)。稼働実績: ci 43 / staging 5 / cloudflare 2 = **50テスト緑**(vitest 368・2回連続+α)。**期限切れ系はSQL過去化+実sweeper方式**(Clock不使用: 04の期限表示はブラウザ実時間由来のためClockで描画不能=matrix §7.3)。残るP1は16中7(P-02・A-02・U-02・U-04・U-07・O-01・O-02)。次は④O-01 CI載せと残りP1 UI群(U-02ブロックdialog・U-04 attendance・U-07 visibility 2値)。実行方法: `cd frontend && npm run test:e2e:staging`(staging環境前提=make staging-up)/ `npm run test:e2e:cf`(cf-devは自動build+migrate+起動)
- **[2026-10-12 V1-P2] ④+残りP1 UI群実施済み**: ④O-01(CI workflow作成・**実pushは承認待ち**)+P1 UI群4項目(U-02・U-04・U-07・P-02)がdone。稼働実績: ci 47 / staging 5 / cloudflare 2 = **54テスト緑**(vitest 368)。**残るP1は16中2(A-02=オーナー承認待ち・O-02=load環境実施)** — P1の開発側実装は出揃い。発見=①同一Intent対へのopen状態latch二重挿入はuq_latches_intent_ids_open(M2 0004)で禁止→2値比較は同一条件の別ペアで実施 ②make lint既存drift 2件(test_llm_real*.py=V1-P1 A-2残)を回収 ③CI順序の欠陥2件(test_expiry_batches計数assertのDB残存感度→E2Eを先に走らせない・geofeatures空だと契約/intent系が422大量失敗→投入はtest-ci前後2回)をローカルドライランで発見し順序確定(以上=matrix §9.2)
- **[2026-10-12 V1-P3] A-06体感アンケート実装+検証済み(13§3表2のPhase 1開始前要件を開発側で充足)**: 仕様=05 v0.7(表survey_responses・GET/POST /v1/users/me/survey・配信契機=アクセス時判定/通知typeなし・集計CLI)→参照(alembic 0010)+Cloudflare(migrations 0006)両実装・契約survey 10ケース両側緑・E2E e2e/survey.spec.js 3件(onboarding往復・未配信・week4のSQL過去化駆動)。稼働実績: **ci 50 / staging 5 / cloudflare 2 = 57テスト緑(2回連続)・vitest 376・backend全套2229(クリーンDB)**。実装欠落群は残り7(A-05/V-01/V-02/O-06/P-04/C-01/X-04の一部)。次はV1-P4(O-06+A-03+A-05=観測・判定・A/B基盤)
