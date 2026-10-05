# M4 ws-8 実施報告(ロールバック手順+G4証拠整備)

## 1. 実施サマリ

- 実施日: 2026-10-05
- 環境: git worktree(`/home/misty/.herdr/worktrees/latch/m4-ws-8`)・ブランチ `m4-ws-8`・基点main head=d231d60。`make setup`(`cd backend && uv sync`)を実施のうえ開始。
- 成果物(新規3本のみ・既存ファイルの変更なし):
  1. `docs/plans/M4/ws-8-rollback.md` — ロールバック・縮退運用手順書(11 §7の4区分+データ事故の5区分・3段階遮断表・遮断conf草案・デプロイ記録規律・確定欄(空欄20個)・walkthrough合否基準)
  2. `docs/testassets/results/README.md` — G4 7項目×証拠の対応表+欠け証拠(ws-5分3点)の採取手順+walkthrough証拠の位置づけ+既存ファイル一覧
  3. `docs/plans/M4/ws-8-report.md` — 本報告書
- コミット一覧:
  | ハッシュ | 要約 |
  |---|---|
  | 302c76f | docs: M4 ws-8 ロールバック・縮退運用手順書(11 §7の4区分+データ事故・3段階遮断・確定欄) |
  | 4e97f03 | docs: G4 7項目×証拠の対応表+欠け証拠(ws-5分3点)の採取手順をresults/READMEへ新設 |
  | (本コミット) | docs: M4 ws-8 実施報告(文書整備2本+実在照合の記録・staging実施は検証待ち) |

## 2. 完了条件別の証拠(計画書§6の1〜7)

| # | 完了条件 | 実行した照合と結果 |
|---|---|---|
| 1 | 手順書が5区分構造+3段階遮断表+conf草案+デプロイ記録規律+確定欄(空欄20個)を含む | `rg -n '^## ' docs/plans/M4/ws-8-rollback.md` → 11行実在(§1判断基準〜§9記録+付録A・B)。`rg --count-matches '\(空欄\)'` → 20(§4の照合参照)。3段階表は§3.1に段1/段2/段3の3行・付録Aにconf全文がコードブロックで実在 |
| 2 | 対応表READMEが7項目の行+欠け3点とwalkthroughの採取手順を含む | `rg -n '^\| ①|^\| ②|…|^\| ⑦' docs/testassets/results/README.md` → 7行実在(14〜20行目)。§3に3-1(ws5-consent)・3-2(ws5-logallowlist)・3-3(ws5-logsample)・§4にwalkthroughの合否基準が実在 |
| 3 | 対応表が参照する証拠パスが実在する | Task 2 Step 2: 7ファイルのls照合がエラー0。glob照合(glob展開の単一lsコマンド)でbruteforce 3点・nfr 5点の点数実査ずみ(§8-3参照) |
| 4 | 実在照合(design §4.3)で参照名がすべて実在 | Task 3 Step 1の(a)〜(f)+§2.1表12行分の追加照合 → すべて期待どおり(§3の一覧参照。#6のみ照合コマンドのパス訂正のうえ一致) |
| 5 | 現行値の維持(コード変更ゼロ) | `make lint` → All checks passed。`make test` → 1472 passed(§5参照)。`git diff main --stat`(対象指定) → 空出力(§5参照) |
| 6 | 差分の範囲が§4どおり | `git status --short`: 報告書作成後コミット前は `?? docs/plans/M4/ws-8-report.md` の1行のみ(§5参照)。手順書・READMEは各コミット(302c76f・4e97f03)済み・既存ファイルの変更行なし |
| 7 | 報告書が§7の形式で存在+検証待ちの明記 | 本ファイルが存在。第7節に「test-ci: 実行不要(コード変更ゼロ)」を明記 |

## 3. 実在照合の結果一覧(design §4.3・計画書§2.1の表12行)

| # | 参照名(期待) | 照合コマンド | 実測(実出力の要約) | 判定 |
|---|---|---|---|---|
| 1 | Makefileターゲット11種 | `rg -n '^(staging-reset\|…\|lint):' Makefile`(Task 3 (a)・13種を照合) | 13行実在(lint:18・test:21・test-ci:24・staging-ps:79・staging-token:96・staging-reset:142・staging-probe:155・staging-d16:158・staging-dump:161・staging-db2-up:165・staging-restore:168・staging-db2-switch:172・staging-db2-down:175)。§2.1の11種はすべて含まれる | 一致 |
| 2 | compose.staging.yamlのサービス8種 | Task 3 (b)(5種)+追加照合 `rg -n '^  (db\|redis\|pubsub):'` | api1:103・api2:108・nginx:113・worker1:126・worker2:131+db:66・redis:84・pubsub:95 = 8種全実在 | 一致 |
| 3 | env名: alert_queue_enabled実在+停止系env不在 | `rg -n 'alert_queue_enabled' settings.py`+`rg -n 'MAINTENANCE\|STOP\|FREEZE' settings.py` | alert_queue_enabledはsettings.py:183(既定False)。停止系は出力なし(終了コード1) | 一致(手順書§3.1「機能フラグが存在しない」の根拠を確認) |
| 4 | 許可リスト | `rg -n 'ALLOWED_STRUCTURED_LOGS' logallow.py test_log_allowlist.py` | logallow.py:21で定義・test側で:12・37・38・48・49に使用 | 一致 |
| 5 | 許可リスト試験 | 同上((e)に含む) | backend/tests/unit/test_log_allowlist.py実在 | 一致 |
| 6 | マイグレーション0001〜0008の8ファイル | `ls backend/migrations/versions/` → **実在せず(終了コード2)**。実パスで再照合: `ls backend/alembic/versions/` | 0001_initial_schema〜0008_obs_sessions_kpiの8ファイル実在(+`__pycache__`はビルド生成物で件数外) | 結果は一致。**照合コマンドのパス訂正**(計画書§2.1の「backend/migrations/versions/」は実パスではなかった)。成果物の文書はこのパスを参照していないため修正不要 |
| 7 | API経路(intents・parse・users) | `rg -n '@(parse_router\|intents_crud_router)\.(get\|post)' intents/routes.py`+users側 | intents: POST /parse(81)・GET /expiry-options(122)・POST ""(259)・GET ""(277)・GET /{intent_id}(299)・pause(350)・resume(365)。users: POST ""(86)・GET /me(107)・POST /me/consent(135) | 一致。GET一覧(277)の実在を再確認=遮断confにメソッド条件が必須という付録Aの設計前提を支持 |
| 8 | 遮断confの前提(nginx) | `rg -n 'listen\|server_name\|proxy_pass\|proxy_next_upstream' docker/nginx/staging.conf`+`rg -n 'conf.d' compose.staging.yaml` | staging.conf:13 `listen 80`・server_nameの行なし(無指定)・:16 `proxy_pass http://latch_api`・:19 proxy_next_upstream。compose.staging.yaml:116 `./docker/nginx/staging.conf:/etc/nginx/conf.d/default.conf:ro` | 一致(default.confがread-onlyマウント=遮断は00-drop.conf追加+reload方式という手順書§3.2・付録Aの前提を支持) |
| 9 | staging鍵・JWT発行コマンド | `ls .staging/keys/`+Makefile:96(staging-token) | Makefile:96にstaging-token実在(§2.1と同一の発行コマンド)。`.staging/keys/idp_test_private.pem`はワークツリー内に不在(終了コード2) | 想定内(.staging/はgit管理外で・実体はスーパーバイザーのstaging実施環境=リポジトリルート側に置かれるもの。§8実施時に同環境で確認される) |
| 10 | Intent作成bodyの実例(m4-seed SD-001) | `ls docs/testassets/m4-seed.yaml`+`rg -c 'SD-001'` | ファイル実在・SD-001出現2件 | 一致 |
| 11 | walkthroughの戻し先候補 | `git log --oneline -5 -- backend/ compose.yaml compose.staging.yaml Makefile` | 先頭 cf9e612(再エンベディング対象をactive Intentへ限定)〜3ed917f | 一致(計画時と同一・driftなし) |
| 12 | 現行値(1472 passed) | `make test`(Task 3 Step 3) | 1472 passed, 282 deselected | 一致 |

## 4. 確定欄の空欄照合

- 手順書: `rg -c '\(空欄\)' docs/plans/M4/ws-8-rollback.md` → **12**(行数)。`rg --count-matches '\(空欄\)' …` → **20**。期待値20は `--count-matches`(出現総数)で一致。
  - **注記(照合コマンドの補正)**: 計画書§2.2・Task 1 Step 2・Task 3 Step 2の照合コマンドは `rg -c` だが・このオプションは「一致した行数」を数えるため・§2のデプロイ記録雛形(1行に(空欄)5個)が1行と数えられ・結果は12行になる。出現総数の照合には `rg --count-matches` を使用した。文書側は修正していない(§2.2の内訳=§2雛形5+§5運用値2+§7担当表8+§8本番欄5=20を草案どおり保持)。
- 対応表README: `rg -c '\(空欄\)' docs/testassets/results/README.md` → 出力なし(終了コード1=0件・期待どおり)。

## 5. コード変更ゼロの証明

- `git diff main --stat -- backend/ compose.yaml compose.staging.yaml compose.load.yaml compose.dashboard.yaml Makefile docker/ backend/pyproject.toml` → **空出力**(終了コード0)。コード・環境資産の変更ゼロを確認。
- `git status --short` の全行:
  - Task 3 Step 4時点(報告書作成前): 空出力(手順書・READMEは各コミット済み)
  - 報告書作成後・コミット前: `?? docs/plans/M4/ws-8-report.md` の1行
  - 最終コミット後: 空出力(ワーキングツリークリーン)
- 変更対象は新規3ファイルのみ(§4.1どおり)。既存ファイルの変更行は0。

## 6. make lint・make testの実数

| 検査 | 基準値 | 実数 | 判定 |
|---|---|---|---|
| `make lint` | エラーなし | All checks passed(356 files already formatted) | 合格 |
| `make test` | 1472 passed | **1472 passed**, 282 deselected(9.43s) | 一致(ズレなし・原因考察不要) |

各コミット前(302c76f直前・4e97f03直前・本コミット直前)に同一の検査を実行し・毎回1472 passedを確認した。

## 7. スーパーバイザー検証待ちの記録(2026-10-05 実施済みに更新)

- **(a) test-ci**: スーパーバイザーがマージ後mainで実施。1回目はload DB停止中のためmassload 4件が接続失敗(1750 passed+4 failed・コード欠陥ではない)・load DB起動後の再実行で**1754 passed・exit 0**。massload試験がload DB(127.0.0.1:25432)起動前提である点是環境前提(計画書§8手順0の記載漏れ)。
- **(b) staging実施(§8手順1〜5)**: **実施済み**(詳細は§9)。証拠YAML 4本をresults/へ作成。
- **(c) G4時確認事項13への追記**: スーパーバイザーがSTATUS.mdへ実施済み。

## 8. 詰まった箇所と判断

作業の停止に至る詰まりはなかった。ただし・照合の手順として次の4点で判断をした(すべて成果物の文書の変更を伴わない・照合コマンド側の補正または判断の記録である)。

1. **空欄照合コマンドの補正**: 計画書の `rg -c` の期待値20は・rg -cの仕様(行数カウント)とは合わず12行になる。§2の雛形が1行5個であることが原因で・文書の空欄数は確定値どおり20個。`rg --count-matches` で20を確認し・この補正を本報告書に記録した(§4)。
2. **マイグレーション照合パスの訂正**: 計画書§2.1の照合パス「backend/migrations/versions/」は実在せず・実パスは「backend/alembic/versions/」だった。8ファイル実在という照合結果自体は一致。成果物はこのパスを参照していないため修正不要(§3の#6)。
3. **点数実査コマンドのパイプ回避**: 計画書Task 2 Step 2の `ls … | rg '…'` は・パイプ規律(計画書§0・Global Constraints「検証チェーンにパイプを書かない」)に触れるため・glob展開の単一lsコマンドで代替し・bruteforce 3点・nfr 5点を目視実査した。
4. **natural-japanese lintの判断**: 手順書に対する機械検査(natural-japanese lint・genre=tech)で「文の長短のメリハリ(low_burstiness)」のwarnが1件出たが・手順書は走査性優先で説明文を伴う性格(計画書の執筆規律)に照らし「残す」と判断した。禁止語・翻訳調等の検出は0件。表記ゆれ(段/区の呼び名)・コマンドの形式一致(11本すべて `docker compose -f compose.staging.yaml` 形式)は点検済み。

## 9. スーパーバイザー実施結果(2026-10-05 22:23〜23:04 JST)

test-ci 1754 passed(§7-a)後・staging-reset→デプロイ記録(手順書§2規律の初適用)→walkthrough→ws-5証拠3点採取→環境downまで実施。**すべて合格**。

- **1世代戻し(手順書§6.2)**: 現行版200(22:33:50)→cf9e612へcheckout・再ビルド(コード同一のため全layer CACHED)・再作成→**22:35:08にnginx経由502を観測**→nginx再起動で200(22:35:35=旧版が新スキーマ0008のDBで起動)→main復帰・再ビルド・再作成・nginx再起動で200(22:35:59)。
- **停止ドライラン(手順書§3.2)**: 段1=worker停止中GET 200(22:36:45〜47)。段2=nginx -t warning付き成功→POST intents/parse 503・GET users/me・GET一覧 200・解除後POST 201(22:37:03〜29)。段3=api停止中**504**を観測(手順書の502と違いはあるがupstream不在の5xx)・復帰200(22:37:56)。
- **ws-5証拠3点**: ①同意フロー(201→422 CONSENT_REQUIRED→同意200→201・23:01) ②許可リスト機械検査5 passed+登録表(23:01:35) ③実ログのraw_text非混入(latch.llm.sendサンプル・禁止語0件・23:02)。
- **証拠YAML 4本**: ws8-rollback-walkthrough-20261005-223756・ws5-consent-20261005-230127・ws5-logallowlist-20261005-230135・ws5-logsample-20261005-230226。
- **実施時発見4件(手順書・READMEへ修正反映済み)**: (1) §6.2のbuildサービス名(api worker→api1 api2 worker1 worker2) (2) api再作成後のnginx再起動が必須(upstream IPキャッシュで502・ws-3知見の再実証) (3) 段3の停止中応答は502でなく504を観測 (4) README §3-1の認証には/v1/auth/tokenでのアクセストークン交換が必須。
- alembic downgradeは全工程で不実行(手順書§6.1規律どおり)。ci一時停止なし(walkthroughは性能測定を伴わない)。
