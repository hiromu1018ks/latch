# LATCH 実装進行状態

このファイルが実装の現在位置。スーパーバイザー(メインセッション)は各作業単位の完了・
ゲート承認をここに記録する。新セッションは .claude/prompts/supervisor.md を貼って
開始し、本ファイルから状況を復元する。

## 現在

- フェーズ: M0(開発基盤)
- 実装言語: Python (FastAPI) — 2026-09-27決定
- 並列構成: worktree完全分離(herdr worktree)。ゲート毎に人間承認
- 次の着手: G0承認待ち(ユーザー判断)。承認後はM1(Intentドメイン)着手

## M0 作業単位

| 単位 | 内容 | 出典(12) | 依存 | 状態 |
|---|---|---|---|---|
| 雛形 | リポジトリ構成・FastAPI最小構成・テスト基盤・ci環境(API 1/Worker 1/DB共用)・Clock+モック | M0-1, M0-3 / C2 | — | 完了 |
| ws-1 | PostgreSQLスキーマ+Index+マイグレーション(friendshipsはEntity定義のみ) | M0-2 / C1 | 雛形 | 完了 |
| ws-2 | LLM Gateway: 3系統集約・送信記録・プロバイダ抽象・スタブ(応答記録+レイテンシ注入) | M0-5 / C4 | 雛形 | 完了 |
| ws-3 | 認証: token/refresh/logout・Redis失効リスト・JWKS検証・テスト用認証構成(staging鍵ペア+JWT発行ツール) | M0-4, M0-7 / C3 | ws-1(users) | 完了 |
| ws-4 | 地物データ取り込み(位置参照情報+OSM→PostGIS)・正転/逆転ジオコーディング | M0-6 / C5 | ws-1(PostGIS) | 完了 |

実行wave: 雛形 → (ws-1 ∥ ws-2) → (ws-3 ∥ ws-4)

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

## 運用ルール(並列worktree × ci環境DB共有。ws-1レビューの引継ぎ事項より裁定)

共有ci-db(compose常設・名前付きボリューム)の `alembic_version` はworktree間で取り合う状態になる。
マイグレーションを追加する単位のworktreeが `test-ci`/`migrate` を実行するとDBがそのworktreeのheadに進み、
他worktree(より古いマイグレーション一式)のconftest `upgrade head` は "Can't locate revision" 系で失敗する。

1. マイグレーションを追加する単位(ws-4等)とDBを消費する単位(ws-3等)のtest-ci実行は**同時にしない**。
   実装エージェントには計画書§0と起動定型で「共有ci-dbへの test-ci/migrate はunit試験で開発を進め、
   報告ファイルに『test-ci=スーパーバイザー検証待ち』と記録してよい」ことを条件付ける
2. スーパーバイザーは検証・マージ時にtest-ciを**直列**で実行する(古い単位→マイグレーション追加単位の順)
3. マージ済みmainでのtest-ci実行がスキーマ状態の唯一の真実。worktreeでのDB状態は検証途中の経過とみなす

## ゲート承認

- G0: **証拠揃え完了・ユーザー承認待ち**(2026-09-27。上記G0欄の4条件すべて独立検証済み)

## 並行トラック(開発外・人間領域)

- T1 LLMプロバイダ契約(D-14の6基準+契約5条件): 未着手
- T2 初期エリアの最終指定(11 第2節の4基準でスコアリング): 未着手
- T3 ゴールドセット整備(シード200〜300件・期待値表・飲酒判定セット): M1開始時に着手
