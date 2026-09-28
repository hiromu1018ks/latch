# LATCH 実装進行状態

このファイルが実装の現在位置。スーパーバイザー(メインセッション)は各作業単位の完了・
ゲート承認をここに記録する。新セッションは .claude/prompts/supervisor.md を貼って
開始し、本ファイルから状況を復元する。

## 現在

- フェーズ: M1(Intentドメイン)
- 実装言語: Python (FastAPI) — 2026-09-27決定
- 並列構成: worktree完全分離(herdr worktree)。ゲート毎に人間承認
- 学習資産: docs/learn/(Diátaxis・初心者向け)を運用開始。**各マージ後にagent4で同期**(規約は .claude/prompts/agent4-learn.md に一元化)
- 次の着手: **M1実装単位はすべて完了(ws-1〜ws-5マージ済み・マージ後main test-ci 561 passed)**。G1判定は人間領域の前提待ち: (1) T1 LLMプロバイダ契約(精度ゲートは実プロバイダでの実測が前提・08 §3)、(2) T3草案のユーザー確認(docs/testassets/)、(3) 02#4期限経過確認の扱い(ws-3設計§6-2)。揃い次第ゲート実行

## M0 作業単位

| 単位 | 内容 | 出典(12) | 依存 | 状態 |
|---|---|---|---|---|
| 雛形 | リポジトリ構成・FastAPI最小構成・テスト基盤・ci環境(API 1/Worker 1/DB共用)・Clock+モック | M0-1, M0-3 / C2 | — | 完了 |
| ws-1 | PostgreSQLスキーマ+Index+マイグレーション(friendshipsはEntity定義のみ) | M0-2 / C1 | 雛形 | 完了 |
| ws-2 | LLM Gateway: 3系統集約・送信記録・プロバイダ抽象・スタブ(応答記録+レイテンシ注入) | M0-5 / C4 | 雛形 | 完了 |
| ws-3 | 認証: token/refresh/logout・Redis失効リスト・JWKS検証・テスト用認証構成(staging鍵ペア+JWT発行ツール) | M0-4, M0-7 / C3 | ws-1(users) | 完了 |
| ws-4 | 地物データ取り込み(位置参照情報+OSM→PostGIS)・正転/逆転ジオコーディング | M0-6 / C5 | ws-1(PostGIS) | 完了 |

実行wave: 雛形 → (ws-1 ∥ ws-2) → (ws-3 ∥ ws-4)

## M1 作業単位

| 単位 | 内容 | 出典(12) | 依存 | 状態 |
|---|---|---|---|---|
| ws-1 | users API: POST /v1/users(初回登録・birth_date必須・18歳未満422 UNDER_AGE)・GET /v1/users/me | M1-1 / 05 §5 | M0(usersスキーマ・認証) | 完了 |
| ws-2 | Intent Parser(07 §2: 確定済みシステムプロンプト実装・アプリ層補完の単一規則・D-04連携ng_unverifiable→warnings)+ POST /v1/intents/parse(同期LLM・timeout 10秒・再試行なし・503 LLM_UNAVAILABLE/422 VALIDATION_ERROR切替 D-17) | M1-2, M1-3 / 07 §2・05 §5 | M0(LLM Gateway) | 完了 |
| ws-3 | intents CRUD: POST(active/draft)・GET・PATCH・DELETE・pause/resume・draft→active遷移(全検証通過後に受理・初回MatchEvent発行)。ジオコーディング正転の保存組み込み・alcohol_involvedのサーバ側確定・時刻検証(過去不可・+7日上限・active時のみ) | M1-4, M1-5 / 05 §5〜§6 | ws-1・ws-2 | 完了 |
| ws-4 | レート制限: Active 5件・作成20件/日・更新6回/時・API 60req/分(Redis・JST日付キー) | M1-6 / 08 §5.4・04 §5 | ws-3・M0(Redis) | 完了 |
| ws-5 | フロントエンド(prototype準拠): parse連携・条件リストの動的連結・有効期限の既定選択計算+disabled化・必須3フィールド催促・判定不能NG条件のNG行・注意表示・保存API接続(active/draft。03 第10節の既知差分解消) | M1-7 / 03 §3・§10 | ws-2〜ws-4 | 完了 |

実行wave: (ws-1 ∥ ws-2) → ws-3 → ws-4 → ws-5

## G1(完了条件 — 12 M1より)

- [x] 02#1(下書き経路含む)〜#3がci環境でグリーン(2026-09-28。マージ後main test-ci 561 passed。下書き経路=CRUD試験のdraft系。10 第3節の振り分けどおりbackend integration試験が本体。**02#4は保存時検証(過去不可・+7日)まで実施済み・「期限経過後expired遷移」の確認はexpiry_sweeper不在(M3-3)のため未実施〔ws-3設計§6-2の裁定待ち〕**)
- [ ] Parser構造化精度ゲート: 入力セット30件以上で category 85% / time.start 90% / location 90% / participants 80% / budget 90%(07 D-17)。**実施にはT1(実プロバイダ契約)とT3草案確定が前提**
- [ ] alcohol_involved精度ゲート: recall 100%・precision下限90%(09 第4.3節)。同上
- [x] プロンプト変更のたびに両ゲートを再実行できる状態(2026-09-28。PARSER_SYSTEM_PROMPT定数+全文ピン試験+docs/testassets/入力セット〔草案〕。ゲートharnessの実行部はT1確定後に実装)

## G1判定の待ち事項(人間領域)

1. **T1 LLMプロバイダ契約**(08 §3のD-14基準6件+契約5条件) — 精度ゲートは実プロバイダでの実測が前提。契約未確定プロバイダは技術基準を満たしても不採用
2. **T3草案の確認**(docs/testassets/・5a28a0e) — 要確認7件の裁定を含む確定作業
3. **02#4の期限経過確認の扱い**(ws-3設計§6-2) — (a)保存時検証+Clock操作による期限切れ値の保存で代替、(b)期限切れバッチをM1へ前倒し、のいずれかをユーザーが裁定

## G0(完了条件 — 12 M0より)

- [x] 認証3エンドポイントとJWKS検証経路がci環境でグリーン(2026-09-27。test-ci 270 passedにtest_auth_api.py#1〜7=実HTTP・実Redis・実DB含む。スーパーバイザー独立検証でasyncpg UUID変換とalembic fileConfigの両欠陥を発見・修正済み 59cd61b)
- [x] 時刻参照がすべてClock経由であることをコード検査で確認(arch test test_arch_no_direct_time.py が毎コミットで強制。rg直接確認でも core/clock.py:33 のみ)
- [x] LLM Gatewayがスタブで3系統の呼び出しを記録できる(test_gateway_{parse,embed,jev}.py=ok/timeout/error全記録・機微非混入・Clock由来時刻)
- [x] 対象エリアの地物データでジオコーディングがPostGIS上で完結する(外部送信経路ゼロ。**暫定エリア=鹿児島市天文館周辺約3km四方・T2確定後に差し替え**: 実データISJ令和7年46201分46行+OSM九州extract・bbox内563行を取り込み、正転「天文館」→天文館(osm_poi)・逆転2点→「鹿児島市泉町」/「鹿児島市祇園之洲町」(別1kmセルで別地名)を確認。geo配下network import禁止arch testあり)

## 完了記録

(形式: 単位 / コミット / 証拠パス / 日付 — マージごとに追記)

- 雛形 / マージ d2b53d7(14コミット・設計 406aa53・計画 8323498・条件文言修正 5c11be1)/ docs/plans/M0/scaffold-report.md(+scaffold-design.md・scaffold-plan.md)/ 2026-09-27
  - 完了条件6項目をスーパーバイザーが独立検証(unit 30・test-ci 33・arch testでC2強制・compose常設環境3healthy+worker running・PostGIS 3.6.4/pgvector 0.8.6同梱確認)
  - 実行環境メモ: dockerグループ参加(usermod+再起動)を実施済み。以降のセッションはdocker APIを直接利用可

- ws-1 / マージ 28fde85(設計 117236e・計画 fb4a189・実装は0552ec9まで)/ docs/plans/M0/ws-1-report.md / 2026-09-27
  - 完了条件5項目を独立検証(migrate=0001(head)・test-ci 62 passed・スキーマ25試験・§1.2対応表トレース済み・禁止領域差分なし)
- ws-2 / マージ 9eba5be(設計 bea2e02・計画 e21138c)/ docs/plans/M0/ws-2-report.md / 2026-09-27
  - 完了条件6項目を独立検証(unit 77・llm 47・3系統送信記録ok/timeout/error・依存追加なし・時刻参照clock.pyのみ)
  - supervisor承認: 送信記録の保存先は構造化ログ(`latch.llm.send`)で開始(docsは媒体未確定・08 §2.4許可リスト準拠。DB化はSendRecord差し替えで再計画可能)
- マージ後main: test-ci 109 passed(unit 79 + integration 30)。settings.py競合は両追記保持で解消

- ws-3 / マージ 215105c(設計 317b4a3・計画 c44aa67・検証時fix 59cd61b)/ docs/plans/M0/ws-3-report.md / 2026-09-27
  - スーパーバイザー独立検証(test-ci)で2欠陥を発見しagent3に修正させた: (1) asyncpgが返すUUIDインスタンスへのuuid.UUID()再構築で503(unit試験はスタブlookupのため未検出だった経路)(2) alembic env.pyのfileConfig既定によりlatch.*ロガー無効化で後続unit試験のcaplogが空
  - 修正後test-ci 202 passed(G0-1証拠)。依存: pyjwt[crypto]・redis(dev: fakeredis)
- ws-4 / マージ b5fcc5c(設計 efca595・計画 2c031d4)/ docs/plans/M0/ws-4-report.md / 2026-09-27
  - test-ci(fixture)177 passed。実データ確認=スーパーバイザー実施: ISJ令和7年(2025)鹿児島県分(46000-19.0b.zip)・OSM九州extract 315MBを取得し geo-import(46+563行)・geo-verify(正転・逆転2点)でG0-4証拠採取
  - supervisor承認: ci暫定エリア=鹿児島市天文館周辺約3km四方でT2確定前にG0判定(差し替えは設定変更+再取り込みのみ)
- マージ後main最終状態: lint クリーン・unit 209 passed・alembic 0002(head)・test-ci 270 passed。settings/README/pyprojectは両側追記保持で解消、uv.lockはuv lock再生成
- 運用メモ: geo integration試験はgeofeaturesをfixtureでフルリロードするため、test-ci実行後に実データが失われる。実運用データは make geo-import 再実行で復旧(証拠採取はtest-ciの後に行うこと)

### M1(2026-09-27〜)

- ws-1 / マージ e5f830d(設計 2fe23fb・計画 de30cc8・実装は0895411まで・9コミット)/ docs/plans/M1/ws-1-report.md / 2026-09-27
  - スーパーバイザー独立検証(実HTTP test-ci)で1欠陥を発見しagent3に修正させた: IntegrityError制約名分類がSQLAlchemy asyncpg dialectのドライバ例外ラップを認識できず(.orig直でなくdriver_exception先に生asyncpg例外)、重複登録が409 USER_EXISTSでなく503になる(test_4で発見・unitの合成エラーでは未検出。M0 ws-3と同系統)。修正0895411
  - 修正後: worktree基準308 passed・test_users_api 6件全グリーン(初回登録フロー・UNDER_AGE・VALIDATION_ERROR・409・404・401)
- タイムボム修正 / マージ 0ae0ec6(実装 30afa9a)/ 2026-09-27
  - M0由来の既存欠陥: tests/unit/auth/test_tokens.py::test_token_payload_structure がFakeClock固定時刻(12:00 UTC)発行のJWT(exp=13:00 UTC)をシステム実刻で期限検証する構造で、2026-09-27 13:00 UTC以降必ず赤化。ws-1実装中に発見。decode時にverify_exp無効化で解消(verify_iat・verify_audと同型の最小修正・テスト意図はclaim構成検査のまま)
- ws-2 / マージ 326ae51(設計 f48f577・計画 12bb7cc・9コミット)/ docs/plans/M1/ws-2-report.md / 2026-09-27
  - worktree基準334 passed(当時main未反映のタイムボム1件のみdeselect)。実装は10分で完了(計画書がコードレベルまで詳細化されていたため)
  - スーパーバイザー検証: test_intents_parse_api 5件グリーン(ハッピーパス・300字境界・形式不正・401・未登録subjectでの200)
  - マージ時main.py競合は両側追記保持で解消(auth・users・intentsの3サービス独立スキップ判定へ統合)。**マージ後のみ顕在化したpytest basename衝突(users/intents両方のtest_service.py・__init__.pyなしのprepend mode)をusers側test_users_service.pyへ改名してマージコミットへ繰り込み**(個別worktreeでは検出不能な類)
- マージ後main最終状態(2026-09-27): lintクリーン・unit 302 passed・**test-ci 374 passed(除外なし・users 6件+parse 5件integration込み)**・alembic 0002(マイグレーション追加なし)・geo実データ復旧済み(46+563行)
- 学習資産追従: 12200b5(第4章「1本の書き込みの経路」・第5章「LLMという外部の協力者」新設・既存6ファイル更新)
- ws-3 / 設計 cdf313a・計画(コード全文記載・事前検証416 passed)/ 2026-09-28実装着手
- supervisor承認・ユーザー決定(2026-09-28):
  - **T3ゴールドセットは「エージェント草案→ユーザー確認で確定」方式**(G1精度ゲート入力=Parser入力セット30件+・飲酒判定セット30件+)
  - **location.name格納=structured_data(JSONB)へlocation_nameキー追加**を承認。05 §2「structured_data保持キー」への同キー追記は次回のdocs改版に含める(ユーザー承認済み・ws-3設計§6-1)
  - §6-2(G1 02#4の期限経過確認はexpiry_sweeperがM3-3のため、G1では保存時検証+Clock操作による期限切れ値保存での代替とするか)は**G1判定時にあらためてユーザー承認**とする

- ws-3 / マージ f222416(設計 cdf313a・計画 0b44582・実装は934849fまで・13+3コミット)/ docs/plans/M1/ws-3-report.md / 2026-09-28
  - スーパーバイザー独立検験(実HTTP test-ci)で2欠陥を発見しagent3に修正させた:
    (1) store.pyの `:geo_lon::float8` をSQLAlchemy text()のbind param正規化が認識せずリテラル落ち→INSERT構文エラーで全保存系が503(unit 416緑でも検出不能。CAST(:x AS float8)へ修正+実dialect compileによる回帰試験 test_store_sql.py・eeb5263。あわせて503ラップ時のログに例外クラス名のみ残す運用改善)
    (2) test_7_deleteがmatch_events検証をイベント種で絞らず.one()(active intentはcreated+deletedの2行が正当)→テスト側を修正(6835d9b)
  - 修正後: worktree基準502 passed(test_intents_crud_api 10件込み)。マージ後main基準でも502 passed・geo実データ復旧済み
- T3部分資産(草案)/ 5a28a0e / docs/testassets/(README・g1-parser-struct.yaml 35件・g1-alcohol.yaml 36件=true24/false12)/ 2026-09-28
  - エージェント草案→ユーザー確認方式(同日承認)。要確認フラグ7件(セット1=5件・セット2=2件)。**確定までG1入力として未使用**

- ws-4 / マージ b4c4740(設計 1199d86・計画 6d7c8a7・実装はb152fe8まで)/ docs/plans/M1/ws-4-report.md / 2026-09-28
  - スーパーバイザー独立検証: **一発グリーン**(worktree・マージ後mainとも554 passed・api 60req/分が既存試験に干渉せず)
  - 経過メモ: 実装中にエージェント側API接続断(EAI_AGAIN)でターン中断 → スーパーバイザーが再開指示して完了( Task 4の途中から継続・成果物に影響なし)
  - supervisor承認(design §5の5件): resumeも更新6回/時に計上・期限切れactiveはActive数計上から除外・auth系429はprovider+subject単位・INCR先行(429・422失敗も消費)・固定バケット窓の境界2倍は許容
- ws-5 / 設計 79718c0(supervisor承認: E2E不導入・expiry-options API追加〔05 §5追記は後日ユーザー確認〕・トークン保存場所固定・debounce 1秒・frontend/新設+prototype温存)/ 2026-09-28

- ws-5 / マージ b9769ff(計画 c38ff80・実装はad684cdまで・13コミット)/ docs/plans/M1/ws-5-report.md / 2026-09-28
  - 実装中に1回BLOCKED: expiry-options追加がws-4の契約カウンタ試験(test_rate_limit_wiring.py)の期待値と衝突 → スーパーバイザーが期待値1行追加を許可(承認済み設計変更の機械的追随)して完了
  - スーパーバイザー独立検証: backend 561 passed(api再ビルド後・マージ後mainでも同値)・frontend vitest 81 passed・npm build成功・preview実機結合確認(フロント配信200・API無認証401)
- 学習資産追従: ws-4分 89922d6(第7章「レート制限」+Lab 3新設)。ws-5分 d301868(第8章「フロントエンドとbackendの合流」+Lab 4新設)。**M1の5単位分すべて同期完了**

## M1 実装完了時点のサマリ(2026-09-28 13:05)

- マージ済み単位: ws-1〜ws-5(マージコミット e5f830d・326ae51・f222416・b4c4740・b9769ff)
- 検証最終値: マージ後main test-ci **561 passed(除外なし)**・frontend vitest 81 passed・lintクリーン・alembic 0002(マイグレーション追加なし)・geo実データ復旧済み
- 検証で発見・修正した欠陥: 計5件(ws-1 IntegrityError分類503化・タイムボム・ws-3 bind paramリテラル落ち503化・test_7_delete期待値・ws-5契約カウンタ期待値〔裁定〕)
- G1判定は「G1判定の待ち事項」の3項目(T1・T3・02#4扱い)が揃い次第実行

## 運用ルール(並列worktree × ci環境DB共有。ws-1レビューの引継ぎ事項より裁定)

共有ci-db(compose常設・名前付きボリューム)の `alembic_version` はworktree間で取り合う状態になる。
マイグレーションを追加する単位のworktreeが `test-ci`/`migrate` を実行するとDBがそのworktreeのheadに進み、
他worktree(より古いマイグレーション一式)のconftest `upgrade head` は "Can't locate revision" 系で失敗する。

1. マイグレーションを追加する単位(ws-4等)とDBを消費する単位(ws-3等)のtest-ci実行は**同時にしない**。
   実装エージェントには計画書§0と起動定型で「共有ci-dbへの test-ci/migrate はunit試験で開発を進め、
   報告ファイルに『test-ci=スーパーバイザー検証待ち』と記録してよい」ことを条件付ける
2. スーパーバイザーは検証・マージ時にtest-ciを**直列**で実行する(古い単位→マイグレーション追加単位の順)
3. マージ済みmainでのtest-ci実行がスキーマ状態の唯一の真実。worktreeでのDB状態は検証途中の経過とみなす
4. make test-ci(compose up)はapiイメージを再ビルドしない(compose.yamlのapiはbuild型・ソースマウントなし)。コード変更後の検証では docker compose build api が先行必須(M1 ws-1で確定。各計画書の報告形式に検証手順として明記)
5. backend/testsは__init__.pyなし(pytest prepend import mode)のため、テストファイルのbasenameはtests配下全体で一意にすること。並走単位のマージ時は `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空であることを確認(個別worktreeでは検出できない・M1 ws-2マージで実際に発生)

## ゲート承認

- G0: **承認済み**(2026-09-27 ユーザー承認。暫定エリア・送信記録=構造化ログ・test-ci後のgeo再取り込みの各supervisor判断を含む承認)。**M1以降の実装は同時にユーザー指示で一時停止**

## 並行トラック(開発外・人間領域)

- T1 LLMプロバイダ契約(D-14の6基準+契約5条件): 未着手
- T2 初期エリアの最終指定(11 第2節の4基準でスコアリング): 未着手
- T3 ゴールドセット整備(シード200〜300件・期待値表・飲酒判定セット): M1開始に伴い着手対象。G1判定までにParser入力セット30件+・飲酒判定セット30件+が最低必要(進め方はユーザーと協議)
