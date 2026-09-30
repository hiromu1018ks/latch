# M2 ws-8(縮退運転+G2ハーネス)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** M2最終単位としてG2完了条件の機械側判定材料を揃える — ①circuit breaker(`llm/breaker.py`・Gateway judge_pair第一候補側への組み込み・窓60秒/エラー率50%超/p95≥timeoutで開放・60秒後半開・1リクエスト試験)②StubLLMへの障害注入拡張(`fail_jev_exc`)③縮退E2E 8試験(切替・双障害skipped・開放→半開往復・p95分離・復旧後再評価・Worker一気通貫)④K上限裏付けE2E+冪等性+#7更新E2E+#8 Bucket再評価の4試験⑤`purge-match-sub` CLI+test-ci挿入(常設subscription掃除)⑥G2日本語評価ハーネス(`latch.g2gate`パッケージ+`make g2-gate`・Gateway公開直呼びIF `call_jev_first`/`call_jev_fallback`)⑦`_SELECT_GROUP_PAIRS`へORDER BY追加(決定性明文化)。**マイグレーション追加なし・依存追加なし**。

**Architecture:** breakerはdesign §2.1案A — LLMGatewayがCircuitBreakerを持ち(プロセス内メモリ・Clock注入・envに出さない)、judge_pairの第一候補側でallow/record。開放中のスキップは内部例外`_FirstCandidateSkippedOpen`で既存切替try/exceptへ合流(フォールバック呼び出し・送信記録の既存動作をそのまま利用)。g2gateはg1gate書式の踏襲(cases/runner/compare/report/__main__・pytestに実APIが構造的に存在しない)。縮退E2Eはdesign §2.4案A(JevWorker直接構築+FakeClock+注入Gateway・最後にworker_env一気通貫1本)。K上限E2Eはdense配置(118 Intent)でL1〜5全層+GroupEngineを直列実行し記録で上限を検証。外部SDKのAPI事実への新規依存なし(既存TypeSafeJevProvider/AnthropicJevFallbackProvider再利用・context7確認不要 — design §1冒頭)。

**Tech Stack:** 変更なし(Python 3.13 / SQLAlchemy[asyncio]+asyncpg / redis-py / google-cloud-pubsub / pydantic・pyyamlは既存導入済み)。

**Spec:** `docs/plans/M2/ws-8-design.md`(agent1設計メモ。**未解決論点なし** — §5のsupervisor承認事項8件は2026-09-30承認済み・§6に承認記録。解釈3件はSTATUS「G2時確認事項」④へ記録済み)。design.mdが本計画より優先(§1の規定)。

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m2-ws-8`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う。**本計画書のmainへのコミットもスーパーバイザーがレビュー後に行う — agent3はworktree内でのみコミット**)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する(**依存追加・マイグレーション追加ともになし**のためlockもalembicも進まない)。
- **共有ci-db運用**(STATUS運用ルール1〜3): 本単位は**マイグレーションを追加しない単位**であるが、**agent3は `make test-ci` / `make migrate` / `make up` / `make down` / `docker compose …` / `docker build` / `docker pull` を一切実行しない**(STATUS運用ルール1の定型)。開発はunit試験(`make lint`・`make test`)で完結させ、報告書に「**test-ci=スーパーバイザー検証待ち**」と記録する。integration試験ファイルは作成するが**実行せず**、収集(`--collect-only`)のみ確認する。`make test` はintegrationファイルの収集(import)まで行うため、構文・importの正当性はunit実行で検証される。本単位はwave単独(後続なし・並走なし)のためDB取り合いは計画上発生しない。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。**計画時点で機械確認済み(2026-09-30)**: 現状 `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空。本計画の新規5ファイル(`test_breaker.py`・`test_cases_compare.py`・`test_events_cli.py`・`test_degraded_e2e.py`・`test_k_limits_e2e.py`)は既存と衝突しない(既存リストに同名なし。特にg1gateの `test_cases.py`/`test_compare.py` とg2gate新規 `test_cases_compare.py` は別名)。各Taskのコミット前に同コマンドが空であることを再確認する。
- **alembic 0001〜0005は変更禁止**(design §3.3・マイグレーション追加なし): Task 13で `git diff main -- backend/alembic` が空であることを検証する。head=0005不変。
- **依存追加なし**: `backend/pyproject.toml`・`backend/uv.lock`・`Makefile`のsetup系・`.env`・`.env.example`・`settings.py` に新規依存・設定キーを追加しない(breakerパラメータはenvに出さない — design §2.2)。
- **並走単位なし**(design §1前提: ws-8が最終単位)。ただし既存ファイルへ触れるため、各Taskのコミット前に `git status --short` で意図しないファイルの変更が混入していないことを確認する(§4の一覧以外に差分が出ていたら作業を止めて報告する)。
- **固定値の遵守**: design §2 の採用判断(案A・BreakerParams・g1gate流儀・purge案a)と本計画§9のIF確定事項は固定値。**変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `refactor:` `chore:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| circuit breaker初期値(FR-10): 測定窓1分の間に(a)第一候補Jev呼び出しのエラー率が50%を超える、または(b)同呼び出しのp95レイテンシがtimeout(6秒)を超える、のいずれかで開放。開放中は第一候補の呼び出しを停止しフォールバックLLMで判定を継続。フォールバックLLM自体も継続して失敗する場合のみ候補はskippedで保留。開放から60秒後に半開へ移行し、半開では第一候補へ1リクエストのみを試験し、成功なら閉じ、失敗なら開放へ戻す。開放⇔半開の往復は障害の継続する限り何度でも許す。単発のtimeoutやレート制限のスパイクでは発動せず継続障害のみを拾う | 06 §8 D-15 FR-10・design §1.2引用#1 |
| timeoutは第一候補・フォールバックとも6秒。双方で再試行を設けない | 07 §1・06 §1 |
| 切替条件: 429・529・timeout 6秒・接続障害→即座にフォールバックLLMへ切替(backoff再試行なし)。LLMProviderError(400系)・JevOutputInvalidError(出力検証失敗)は切替せず伝播 | 07 §4切替表・ws-5実装 |
| フォールバックLLMの失敗(timeout・429・5xx・出力検証失敗)→縮退へ(候補はskippedで保留) | 07 §4・06 D-15 |
| 縮退試験の確認事項6点(切替・双障害skipped・提案ゼロ・breaker開放→半開・Parser失敗区別〔実施済み〕・Embedding復帰〔実施済み〕)+復旧後の再評価(FR-18) | 10 §4.5 |
| K上限試験: 一次候補100件超の密集配置でVector≦50・Cheap≦20・Jev≦8回(1対1最低4回保証)・Pool≦15が記録で守られる。切り詰めの決定性は同一入力での2回実行が同一結果(スコア上位・同点はintent_id昇順) | 10 §4.6・06 §8 D-24 |
| Queue障害・冪等性: UNIQUE制約(idempotency key=(event_type, source_intent_id, version))による同一Event重複処理排除 — 2回投入してもmatch_candidatesが二重生成しない | 10 §4.7・06 §9 |
| 02#5〜#12の検証方法と合格証拠(#5=処理件数・レイテンシがIntent数で急増しない/本格はload・#7=更新後に候補の生成または消失・#8=時刻到来による候補生成・#11=Jev実行回数上限・他は既存資産) | 02 §4・10 §3・design §2.5対応表 |
| 日本語評価指標: would_* noul確率・5軸正規化値・confidenceへPrecision/Recall(閾値0.70/0.80/0.90)・ECE(10分割ビン)・Brier・Mutual Acceptance Precision(gold true/falseのMutualScore分布の分離度)。フォールバックLLMも同一ゴールドセットで評価しproviderキーで分離。C=1・L=H×MutualScore×C。ハーネスがintentペアを正規化テキスト(07 §4形式・visibility/notification_levelを含めない)へ組み立て1ペア1リクエスト | 09 §4〜§4.2・goldset-plan §11・design §1.2引用#11 |
| ゴールドセット: 520ペア・304 Intent・status confirmed。intentsはstructured(アプリ層補完後)。pairsはkind(1to1/group)・segment・layer1_pass・expected{gold_mutual・would_* label+band・fit 0〜4・latent_yes band}。ng_unverifiableは正規化テキストで「(システムで判定不能)」付きsoft行。bandはlow<0.35/0.35≤mid<0.65/high≥0.65 | g2-jev-goldset.yaml meta・goldset-plan §5・§7・07 §4 |
| コスト試算: 両経路520ペア≈$4.75・再実行2回込み上限$15。--limit 2部分実行≈$0.02。評価実行(520全件)はG2時・本単位では--limit部分実行での動作確認まで | goldset-plan §8・design §1.4-1 |
| ci環境=最小構成(API 1・Worker 1・DB共用)。LLM呼び出しはスタブで決定的に。時刻参照はすべてClock経由(FakeClock注入で実時間待ちを排除) | 10 §1 |
| #5本格負荷・D-16上限到達・resume/Bucket遅延回収フル試験・性能5目標・レイテンシ注入・staging構築・Worker並列化・usageのDB記録はスコープ外(M4/後続) | design §1.4・12 M4 |
| 実装方式の全判断(案A・BreakerParams・状態機・stub fail_jev_exc・縮退E2E構成案A・02対応表・K上限配置・purge案a・g2gate構成・Minor(a)のみ拾う・YAGNI切り捨て)とsupervisor承認8件 | design §2〜§6 |
| G2完了条件5項(①02#5〜#12グリーン②K上限③冪等性④縮退⑤日本語評価)と本単位の充足マップ | 12 §3 M2・design §2.10 |

## 2. グローバル制約(全タスクに暗黙に適用)

- **時刻はClock経由のみ**。製品コード(`backend/src/latch/`)で実時間参照禁止(arch test `test_arch_no_direct_time.py` が強制)。CircuitBreakerの時刻参照はClock注入・g2gateの実施日時(report meta)はSystemClock(12 C2・design §2.8)
- **breakerパラメータはenvに出さない**: `BreakerParams` は `Timeouts` と同様のコンストラクタ上書き式。settings.pyへ新規キーを追加しない(design §2.2)
- **例外は握り潰さない**: breakerの状態遷移は構造化ログ(logger名 `latch.breaker`)のみで記録(永化しない・design §2.11-3)。g2gate runnerは1ペア失敗を記録して継続(再試行しない — 07 §4「失敗は再試行せず」)
- **例外メッセージ・ログにIntent本文を入れない**(08 §2.4)。breakerログ・g2gateレポートのper_pairはID・数値・ラベルのみ(goldsetはテスト資産のため機微制限対象外 — 09 §4.1・g1gateと同一判断)
- **永続化は既存形式を踏襲**: g2gateの入力読込はpydantic(_IgnoreExtra流儀)・レポートは `yaml.safe_dump(sort_keys=False, allow_unicode=True)`(g1gate report.pyと同一)
- **変更しない既存部品をimportして使うのみ**(design §3.3): `worker/jev.py`・`worker/main.py`・`layer1〜4`・`latch_engine`・`origin.py`・`candidates.py`・`reeval.py`・`runner.py`(RESELECT_ALWAYS・`_run_post_retrieval`・run_candidate_retrieval・ReevalRunner.run_onceは再利用)。`core/clock.py`・`settings.py`・`events/pubsub_bus.py`・`events/relay.py`・`events/bus.py`・compose.yaml・`docker/` も無変更(pubsub_busの `delete_subscription`・`ensure` は呼ぶのみ)
- **unit試験は外部プロセス不要・実時間待ちなしを原則**(スタブ注入・FakeClock)。例外は既存 `test_stub_delay_injects_real_latency` と同型の遅延注入1件(Task 3のtimeout置換検証・200ms)
- **実API呼び出しはpytestに構造的に存在させない**: g2gate部品のunit試験は合成入力のみ(markerによらない分離・g1gateと同一規律・design §2.8)
- **改行・行長**: ruff(E,F,I,UP,B)と `ruff format`(行長88)を毎コミット通す
- **全タスクのコミット前に**: `make lint && make test` グリーン・`git status --short` で差分が§4の一覧どおり・basename一意コマンドが空

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **窓内1回の失敗だけでbreakerが開放する**(引用#1「単発のtimeoutやレート制限のスパイクでは発動せず」違反) — N=1はエラー率100%になるためmin_samples=2の判定を入れ漏れると単発429で開放し、以後60秒間第一候補不呼出になる → Task 1の試験2(N=1失敗→closed維持)がピン。p95判定にはmin_samplesを適用しない(§9-3)ため、その境界もTask 1試験7で明示
2. **開放中に第一候補が呼ばれる**(allow判定の漏れ・例外合流忘れ) — judge_pairの切替try構造を組み替える際、breaker.allowを確認せず `_jev_call` を呼ぶ旧経路が残ると開放の実効性が消える → Task 4 unit(開放済breakerで第一候補judge呼び出し0回)+Task 11試験3(judge_calls不変)がピン
3. **timeout呼び出しのレイテンシが実測0秒で記録される**(FakeClock下でレイテンシ計測が常に0) — timeout打ち切りを例外種別で `Timeouts.jev_s` へ置換しないと、p95条件がunit/integrationとも永远に成立しない → Task 1試験7(latency=6.0の母集団)+Task 4 unit(`samples()` でtimeout記録がtimeout_sであること)がピン
4. **JevOutputInvalidErrorがbreakerのエラーとして計上される**(design §2.2「呼び出し成功扱い」違反) — `except LLMError` で一括record(error=True)にすると、出力検証失敗の連発で誤開放する(JevOutputInvalidErrorはLLMErrorのサブクラス) → Task 4 unit(JevOutputInvalidErrorで `samples()` がerror=Falseを記録)がピン
5. **フォールバック側の失敗でbreaker状態が変わる**(design §2.2「breakerは第一候補の品質のみを見る」違反) — record呼び出しをフォールバック側にも置くと双障害時に開放が早まり、実装解釈(p95・min_samples)の実証が崩れる → Task 4 unit(第一候補成功+フォールバック失敗のjudge_pairでbreaker窓が空のまま)がピン

---

## 4. スコープ(作成・変更するファイル一覧)

作成(§0のbasename機械確認済み・design §3.1+§9の実装詳細確定分):

```text
backend/src/latch/llm/breaker.py                       (Task 1。CircuitBreaker・BreakerParams)
backend/src/latch/g2gate/__init__.py                   (Task 9。パッケージ公開IF)
backend/src/latch/g2gate/cases.py                      (Task 7。goldset読込・JevTextInput変換・SHA)
backend/src/latch/g2gate/compare.py                    (Task 8。P/R/ECE/Brier/AUC・band診断)
backend/src/latch/g2gate/runner.py                     (Task 9。両経路の評価実行)
backend/src/latch/g2gate/report.py                     (Task 9。証拠YAML生成)
backend/src/latch/g2gate/__main__.py                   (Task 9。CLI --assets/--out/--limit/--route)
backend/src/latch/events/__main__.py                   (Task 6。purge-match-sub サブコマンド)
backend/tests/unit/llm/test_breaker.py                 (Task 1。状態機12件)
backend/tests/unit/g2gate/test_cases_compare.py        (Task 7〜9。cases/compare/report/__main__既知値)
backend/tests/unit/test_events_cli.py                  (Task 6。purge CLIのスタブ試験)
backend/tests/integration/test_degraded_e2e.py         (Task 11。縮退E2E 8試験・作成のみ)
backend/tests/integration/test_k_limits_e2e.py         (Task 12。K上限等4試験・作成のみ)
docs/plans/M2/ws-8-report.md                           (Task 13。報告ファイル)
```

変更(design §3.2+§9の実装詳細確定分):

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/src/latch/llm/gateway.py` | (1)コンストラクタへ `breaker: CircuitBreaker \| None = None`(既定None=従動作) (2)judge_pair第一候補側へbreaker組み込み+内部例外 `_FirstCandidateSkippedOpen`(§9-6) (3)`call_jev_first`/`call_jev_fallback` 公開IF(§9-5) (4)build_worker_gatewayのstub/real両構成でCircuitBreaker生成・渡し | 3・4 |
| `backend/src/latch/llm/jev.py` | `JevJudgment` へ `usage: dict \| None = None` フィールド追加(§9-5。g2gateのusage集計用・Layer 4のjev_result書込は従来どおりusageを含めない) | 3 |
| `backend/src/latch/llm/stub.py` | コンストラクタへ `fail_jev_exc: str \| None = None` 追加(§2.3。"ratelimit"/"overloaded"/"timeout"/"connection"→各LLM例外。既存fail_jev=TrueはLLMProviderErrorのまま下位互換) | 2 |
| `backend/src/latch/llm/jev_smoke.py` | `gateway._jev_fallback.judge` プライベート属性アクセスを `gateway.call_jev_fallback` へ置換(振る舞い不変。§2.8・§9注記: 置換後はGateway経由のため送信記録1件が出る=08 §3の開示要件に合致する向上) | 3 |
| `backend/src/latch/worker/matching/group_engine.py` | `_SELECT_GROUP_PAIRS` へ `ORDER BY intent_a_id, intent_b_id, updated_at DESC` 追加(§2.9(a)・挙動不変の決定性明文化) | 5 |
| `backend/tests/integration/conftest.py` | `make_worker_env`(jev注入付きasync contextmanager)・`worker_env` fixture・`worker_env_factory` fixtureを新設(design §3.2「worker_envへJevWorker注入オプション」の実体。§9-11) | 10 |
| `backend/tests/integration/test_events_pipeline.py` | 既存 `worker_env` fixture定義(162〜192行付近)と `_WorkerEnv`・`_instant` をconftestへ移設(削除のみ。**既存8試験は引数名だけなので無変更**) | 10 |
| `backend/tests/unit/llm/test_gateway_jev_switch.py` | breaker連携・公開IF・timeout置換・JevOutputInvalid成功扱い・fb失敗不計上のunit試験を追記 | 3・4 |
| `backend/tests/unit/llm/test_stub.py` | `fail_jev_exc` の4種例外マップ試験を追記(既存試験は値不変で無傷) | 2 |
| `backend/tests/unit/matching/test_group_engine.py` | `_SELECT_GROUP_PAIRS` のORDER BYピン1件を追記 | 5 |
| `Makefile` | (1)test-ciのpytest終了後・worker復帰前に `purge-match-sub` 挿入(§2.7・rc保持) (2)`g2-gate` ターゲット追加+.PHONY追記 | 6・9 |

`worker/matching/__init__.py`・`g2gate/__init__.py` 以外の `__init__.py` は変更しない(直接import規律)。

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`・g2gate部分実行のレポート(make g2-gate --limit 2の出力。スーパーバイザー検証時に生成・docs/testassets/results/へ保管)。

## 5. 禁止(触ってはいけないもの・スコープ外の判断基準)

- **docker系コマンド一切**(§0): `make test-ci` / `make up` / `make down` / `make migrate` / `docker compose …` / `docker build` / `docker pull`。apiイメージ再ビルド・integration実行・孤立行掃除SQL実行はすべてスーパーバイザーの検証手順(§7)に含まれる
- **実API呼び出しをagent3が実行しない**: `make g2-gate`・`make jev-smoke`・`make embed-smoke`・`make g1-gate` は起動しない(--limit 2部分実行もスーパーバイザー検証時・§7手順5)。g2gateのunit試験は合成入力のみ
- **design §3.3の禁止**: `backend/src/latch/` 配下の intents / auth / users / ratelimit / geo / core / g1gate 各モジュール。`llm/` の既存ファイルのうち変更対象以外(`errors.py`・`providers.py`・`records.py`・`jev.py`〔JevJudgment.usageの1行追加のみ〕・`typesafe.py`・`anthropic.py`・`anthropic_jev.py`・`gemini.py`・`embed_smoke.py`)。`worker/` の既存ファイルすべて(jev.py・main.py・stage1.py・embedding.py・debounce.py・backfill.py・reeval.py・cost/・matching/のlayer1〜layer4・origin・candidates・latch_calc・latch_engine・proposal・runner・group_calc)。`events/` の既存3ファイル(bus.py・pubsub_bus.py・relay.py・__init__.py)。`backend/alembic/`(0001〜0005不変・追加もしない)。`compose.yaml`・`docker/`・`frontend/`・`prototype/`。`backend/pyproject.toml`・`backend/uv.lock`(依存追加なし)。`.env`・`.env.example`・`settings.py`(新規キーなし)。`docs/`(01〜12・learn・testassets〔goldset yamlは読み取り専源〕の改版不要)。`docs/plans/STATUS.md`(スーパーバイザー管理)。`docs/plans/M0/`・`M1/`・`M2/` の既存ファイル(design・過去plan・過去report)。`backend/tests/` の§4に列挙した以外の既存試験
- スコープ外と判断する基準(必要になったと感じても作らない — design §1.4・§2.11):
  - **日本語評価の実行(520全件)と合格判定** — G2時(オーナー確定・実行前金額報告はスーパーバイザー)。本単位は--limit部分実行による動作確認まで(§7手順5)
  - **D-16上限到達・月次リセット・resume/Bucket遅延回収のフル試験** — M4非機能(12 M4スコープ4・承認事項5)。Guard denyのskipはws-4試験済み
  - **#5本格負荷試験(1万→5万→10万)・性能5目標・レイテンシ分布注入(p50/p95)** — load環境・M4(引用#14)
  - **staging環境の構築・27項目フル実行** — M4-1/M4-3(承認事項4。02#5〜#12はci環境で実施してG2証拠とする)
  - **Worker並列化・Jev評価の並行化・breaker状態のRedis共有・開放状態のDB永化** — M4(design §2.11-1/3/4。IF不変で移行可能)
  - **フォールバック側のbreaker・品質測定** — D-15はフォールバック失敗をskipped保留で受け止める設計(design §2.11-2)
  - **g2gateでの再試行・失敗ペアの再実行・pytest自動化・合格基準との比較判定** — design §2.11-7/8・スコープ外1(レポートは指標値と参考値表示まで)
  - **K上限E2Eでの実API** — スタブで十分(10 §1)。実API品質は⑤日本語評価が担う(design §2.11-10)
  - **ws-7 Minor引継ぎ(b)(c)(d)**(世代リセット後のペア再生成経由・aggregate早期continue・latches.group_candidate_id旧gid)— M3/M4(design §2.9)
  - **usageトークンのDB記録・コスト集計** — M4(g2gateはレポートmetaへの合計表示のみ)
  - **毒ペイロード・削除済み参照Eventの試験** — ws-1 test_5/6で実施済み(引用#8)。対応付けのみ
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 13で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(本単位のunit追加分・既存試験の機械的追随を含む)
   検証: `make lint && make test` — ともにexit 0
2. **integration 2ファイル(12試験)が収集できる** — **ただし実行はしない**(§0。実DB・実Redis・api再ビルドが必要なためスーパーバイザー検証時に実施)
   検証(実装側): `cd backend && uv run pytest --collect-only tests/integration/test_degraded_e2e.py tests/integration/test_k_limits_e2e.py -q` がexit 0(8+4=12件収集)。報告書に「**test-ci=スーパーバイザー検証待ち**」と記録
3. **実時間参照が `core/clock.py` のみ**(breaker.py・g2gate配下はヒットしない)
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ(`llm/stub.py` の `asyncio.sleep` は待機であり時刻参照ではない・既存arch testと同一判定)。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
4. **マイグレーション・依存に差分なし**
   検証: `git diff main -- backend/alembic backend/pyproject.toml backend/uv.lock` — 出力なし(空)。alembic head=0005不変(スーパーバイザー検証手順7でtest-ci後に確認)
5. **変更ファイルが§4の一覧どおり(作成14+変更11=25ファイル)**
   検証: Task 13の報告コミット後に `git diff --name-only main | sort` が§4の一覧(report込み)と完全一致。`git status --short` が空(未コミット変更なし)
6. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし
7. **既存unit試験がすべてグリーンのまま**(既存試験の期待値変更はgateway/stub/groupengineへの追記のみであることの証明として `make test` の全件数を報告書へ記録し、変更が§4列挙以外に及んでいないことを§4との突合で示す)

## 7. 報告形式

**結果ファイル**: `docs/plans/M2/ws-8-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M2 ws-8(縮退運転+G2ハーネス)実行報告

- ブランチ: m2-ws-8 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る(全件数も)> |
| 2 | integration 12試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | <collect-only出力> |
| 3 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 4 | alembic・依存無変更 | PASS/FAIL | <git diff出力(空なら「空」)> |
| 5 | 変更ファイル=§4の25ファイル | PASS/FAIL | <git diff --name-only出力> |
| 6 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 7 | 既存unit試験グリーン維持 | PASS/FAIL | <make test 全件数と§4列挙以外の期待値変更がないことの確認結果> |

**test-ci=スーパーバイザー検証待ち**(STATUS運用ルール1〜3。docker系・実APIは
実装側で未実行。wave単独のため並走とのDB取り合いは計画上なし)

## スーパーバイザー検証手順(design §4.3をそのまま実施)

1. `make lint && make test` — unit全件グリーン
2. `docker compose build api worker` — STATUS運用ルール4(composeのapi/workerは
   build型・ソースマウントなし。コード変更後の検証では再ビルドが先行必須)
3. `make test-ci` — 既存1125+新規12前後がグリーン・**worker復帰前にpurge-match-sub
   が走ること**(Makefile出力で「[events] purged subscription:」を確認)
4. test-ci後の残存・孤立行確認: users/intents/match_candidates系は従来手順
   (subject prefix等)+group_candidates/latchesの孤立行(intent_ids全要素が
   intentsに存在しない行)が**0件** — 対抗策の実効性実証(design §2.7)。
   1度目は既存残存の掃除SQLを先行実行:
   ```sql
   DELETE FROM group_candidates
   WHERE NOT (intent_ids <@ (SELECT array_agg(id) FROM intents));
   DELETE FROM latches
   WHERE NOT (intent_ids <@ (SELECT array_agg(id) FROM intents));
   ```
   (<@ = 包含演算子。「intent_idsの全要素がintentsに存在する」行を残す)
5. `make g2-gate -- --limit 2 --route both`(部分実行・実課金≈$0.02)でexit 0・
   docs/testassets/results/へレポート生成・partial=true明記。
   **520全件実行はG2時(本単位では実施しない)**
6. lint再実行(test-ciにlintが含まれないため・ws-4運用メモ): `make lint`
7. basename一意確認(運用ルール5)・alembic head=0005不変・時刻参照がclock.pyのみ
   (arch test)

## design §5 実装時確認事項の結果
- (なし — design §5の8件はすべて承認済みで実装時確認事項の留保はない。
  本計画§9のIF確定事項から変更した場合は下に記録する)

## 固定値の変更有無(design.md §2・本計画§9)
- breaker=Gateway内・プロセス内メモリ(案A・design §2.1): 変更なし / 変更あり(<前→後+理由>)
- BreakerParams既定値60.0/0.5/60.0/2+latency_threshold_s(§9-1): 変更なし / 変更あり
- JevJudgment.usage追加(§9-5): 変更なし / 変更あり
- worker_envのconftest移設+factory化(§9-11): 変更なし / 変更あり
- K上限E2E配置=計118 Intent・layer1_pass_count=101(§9-15): 変更なし / 変更あり
- 本計画§9のその他のIF確定事項: 変更なし / 変更あり(<前→後+理由>)

## (G2・M3/M4への引継ぎ)
- G2: ハーネス(make g2-gate)と--limit動作確認まで実施済み。評価実行(520全件・
  実課金上限$15)・合格基準確定・オーナー判定がG2時の待ち事項(STATUS)
- M4: breakerのRedis共有(Worker複数構成確定時・IF不変)・D-16上限到達試験・
  #5本格負荷・性能5目標・レイテンシ分布注入
- M3-1: ws-7 Minor引継ぎ(b)(c)(d)

## コミット一覧
<git log --oneline main..HEAD>

## 補足(詰まった点・判断した点があれば)
<自由記述>
```

## 8. 実装ステップ(TDD。Task 1〜13の順で実行する)

### Task 1: CircuitBreaker・BreakerParams(llm/breaker.py)

**Files:**
- Create: `backend/src/latch/llm/breaker.py`
- Test: `backend/tests/unit/llm/test_breaker.py`

**Interfaces:**
- Consumes: `latch.core.clock.Clock`(Clock注入のみ・実時間参照なし)
- Produces: `BreakerParams(window_s=60.0, error_rate_threshold=0.5, half_open_after_s=60.0, min_samples=2)`・`CircuitBreaker(clock, params=None, latency_threshold_s=6.0)`・`allow(now: datetime) -> bool`・`record(*, error: bool, latency_s: float) -> None`・`state` プロパティ("closed"/"open"/"half_open")・`samples() -> tuple[tuple[bool, float], ...]`(観測用・§9-8)。Task 3/4のGatewayがimportする

- [ ] **Step 1: 失敗テストを書く**(`backend/tests/unit/llm/test_breaker.py` 新規)

```python
"""CircuitBreaker状態機のunit試験(ws-8 design §2.2・§4.1)。

純部品(FakeClock注入)でdocs確定値の機械検査を行う。実DB・実Redis不要。
"""

from datetime import UTC, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.llm.breaker import (
    CLOSED,
    HALF_OPEN,
    OPEN,
    BreakerParams,
    CircuitBreaker,
)

NOW = datetime(2026, 10, 1, 2, 30, 0, tzinfo=UTC)  # 11:30 JST(goldset基準)


def _clock() -> FakeClock:
    return FakeClock(NOW)


def test_1_params_defaults_pin():
    """BreakerParams既定値=docs確定値(窓60秒・50%超・半開60秒・最小2)。"""
    p = BreakerParams()
    assert p.window_s == 60.0
    assert p.error_rate_threshold == 0.5
    assert p.half_open_after_s == 60.0
    assert p.min_samples == 2


def test_2_single_error_does_not_open():
    """窓内1失敗だけでは開放しない(引用#1「単発では発動せず」・min_samples=2)。"""
    b = CircuitBreaker(clock=_clock())
    b.record(error=True, latency_s=0.1)
    assert b.state == CLOSED


def test_3_error_rate_opens_at_two_failures():
    """2失敗/2呼(100%>50%)で開放(design §2.4試験3の性質)。"""
    b = CircuitBreaker(clock=_clock())
    b.record(error=True, latency_s=0.1)
    b.record(error=True, latency_s=0.1)
    assert b.state == OPEN


def test_4_error_rate_3_of_5_opens():
    b = CircuitBreaker(clock=_clock())
    for _ in range(3):
        b.record(error=True, latency_s=0.1)
    for _ in range(2):
        b.record(error=False, latency_s=0.1)
    assert b.state == OPEN  # 3/5=60%>50%


def test_5_error_rate_2_of_5_stays_closed():
    b = CircuitBreaker(clock=_clock())
    for _ in range(2):
        b.record(error=True, latency_s=0.1)
    for _ in range(3):
        b.record(error=False, latency_s=0.1)
    assert b.state == CLOSED  # 2/5=40%≤50%


def test_6_window_eviction_excludes_old_failures():
    """窓追い出し: 60秒前の失敗は判定から除外される。"""
    clock = _clock()
    b = CircuitBreaker(clock=clock)
    b.record(error=True, latency_s=0.1)  # t0の失敗
    clock.advance(timedelta(seconds=61))  # 窓外へ
    b.record(error=True, latency_s=0.1)  # t61の失敗
    # 窓内は新1件のみ(N=1<min_samples)→開放しない
    assert b.state == CLOSED
    assert b.samples() == ((True, 0.1),)  # 旧サンプルは除去済み


def test_7_p95_boundary_n20():
    """p95位置境界: N=20でtimeout(=threshold値)1件=5%は不開放・2件=10%は開放。

    timeout呼び出しのレイテンシは打ち切り時点のtimeout_sとして記録される
    (design §2.2・承認事項1)。p95位置=ceil(0.95×N)−1(0-indexed)。
    """
    b = CircuitBreaker(clock=_clock(), latency_threshold_s=6.0)
    for _ in range(19):
        b.record(error=False, latency_s=0.1)
    b.record(error=True, latency_s=6.0)  # timeout打ち切り(1件=5%)
    assert b.state == CLOSED  # p95位置=18番目は成功(0.1)

    b2 = CircuitBreaker(clock=_clock(), latency_threshold_s=6.0)
    for _ in range(18):
        b2.record(error=False, latency_s=0.1)
    b2.record(error=True, latency_s=6.0)
    b2.record(error=True, latency_s=6.0)  # 2件=10%・エラー率も10%≤50%
    assert b2.state == OPEN  # p95位置=18番目が6.0≥6.0


def test_8_half_open_transition_after_60s():
    b = CircuitBreaker(clock=_clock())
    b.record(error=True, latency_s=0.1)
    b.record(error=True, latency_s=0.1)
    assert b.state == OPEN
    assert b.allow(b._clock.now()) is False  # 60秒未満は拒否
    b._clock.advance(timedelta(seconds=60))
    assert b.allow(b._clock.now()) is True  # 半開の試験リクエスト
    assert b.state == HALF_OPEN


def test_9_half_open_success_closes_and_resets_window():
    clock = _clock()
    b = CircuitBreaker(clock=clock)
    b.record(error=True, latency_s=0.1)
    b.record(error=True, latency_s=0.1)
    clock.advance(timedelta(seconds=60))
    assert b.allow(clock.now()) is True
    b.record(error=False, latency_s=0.1)  # 試験成功→closed+窓リセット
    assert b.state == CLOSED
    assert b.samples() == ()  # 窓リセット
    b.record(error=True, latency_s=0.1)  # リセット後は1失敗で開放しない
    assert b.state == CLOSED


def test_10_half_open_failure_reopens_and_extends():
    clock = _clock()
    b = CircuitBreaker(clock=clock)
    b.record(error=True, latency_s=0.1)
    b.record(error=True, latency_s=0.1)
    clock.advance(timedelta(seconds=60))
    assert b.allow(clock.now()) is True
    b.record(error=True, latency_s=0.1)  # 試験失敗→open戻し(opened_at更新)
    assert b.state == OPEN
    assert b.allow(clock.now()) is False
    clock.advance(timedelta(seconds=60))  # 再び60秒→再度半開(往復)
    assert b.allow(clock.now()) is True
    assert b.state == HALF_OPEN


def test_11_half_open_probe_pending_defense():
    """半開の試験リクエストが未recordのうちの再allowはFalse(防御)。"""
    clock = _clock()
    b = CircuitBreaker(clock=clock)
    b.record(error=True, latency_s=0.1)
    b.record(error=True, latency_s=0.1)
    clock.advance(timedelta(seconds=60))
    assert b.allow(clock.now()) is True
    assert b.allow(clock.now()) is False  # 未recordの2回目は拒否


def test_12_state_transitions_logged(caplog):
    """状態遷移は構造化ログ(latch.breaker)へ(design §2.11-3)。"""
    import logging

    clock = _clock()
    b = CircuitBreaker(clock=clock)
    with caplog.at_level(logging.INFO, logger="latch.breaker"):
        b.record(error=True, latency_s=0.1)
        b.record(error=True, latency_s=0.1)  # closed -> open
        clock.advance(timedelta(seconds=60))
        b.allow(clock.now())  # open -> half_open
        b.record(error=False, latency_s=0.1)  # half_open -> closed
    messages = [r.message for r in caplog.records]
    assert any("closed -> open" in m for m in messages)
    assert any("open -> half_open" in m for m in messages)
    assert any("half_open -> closed" in m for m in messages)
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_breaker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'latch.llm.breaker'`

- [ ] **Step 3: 最小実装**(`backend/src/latch/llm/breaker.py` 新規・全文)

```python
"""Circuit Breaker(06 §8 D-15 FR-10・ws-8 design §2.2・§2.1案A)。

第一候補TypeSafe Jev呼び出しの継続障害検知。測定窓1分で(a)エラー率50%超
または(b)p95レイテンシがtimeout相当以上の呼び出しで開放、開放から60秒後に
半開で1リクエストのみ試験。状態はWorkerプロセス内メモリ(再起動でclosedから
出直す — design §2.1)。複数Worker共有化はM4構成確定後にRedisへ移行
(IF不変・design §2.11-1)。

確定値(design §2.2・supervisor承認事項1・2):
- エラー率判定は窓内呼び出し数 N >= min_samples(=2) かつ error数/N > 0.5。
  N=1の失敗では開放しない(「単発のtimeoutやレート制限のスパイクでは発動
  せず」の実装解釈)
- p95判定は窓内呼び出しのレイテンシ昇順ソートで p95位置=ceil(0.95×N)−1
  (0-indexed)の値が latency_threshold_s 以上なら開放。timeout
  (asyncio.timeout打ち切り)呼び出しは打ち切り時点のtimeout_sをレイテンシ
  として記録するため母集団に入る(成功呼び出しは必ずtimeout_s未満)。
  min_samplesはエラー率判定のみに適用しp95判定には適用しない
- 時刻参照はClock注入のみ(arch test規律・C2)。パラメータはenvに出さない
  (Timeoutsと同様のコンストラクタ上書き式)
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from latch.core.clock import Clock

logger = logging.getLogger("latch.breaker")

WINDOW_S = 60.0
ERROR_RATE_THRESHOLD = 0.5
HALF_OPEN_AFTER_S = 60.0
MIN_SAMPLES = 2
LATENCY_THRESHOLD_S = 6.0  # 既定はTimeouts.jev_sと同値(07 §1・引用#2)

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


@dataclass(frozen=True)
class BreakerParams:
    """測定窓・しきい値(design §2.2)。unit試験で短縮注入するための上書き経路。"""

    window_s: float = WINDOW_S
    error_rate_threshold: float = ERROR_RATE_THRESHOLD
    half_open_after_s: float = HALF_OPEN_AFTER_S
    min_samples: int = MIN_SAMPLES


class CircuitBreaker:
    """closed/open/half_openの3状態。allow→呼び出し→recordの契約。"""

    def __init__(
        self,
        *,
        clock: Clock,
        params: BreakerParams | None = None,
        latency_threshold_s: float = LATENCY_THRESHOLD_S,
    ) -> None:
        self._clock = clock
        self._params = params if params is not None else BreakerParams()
        # p95しきい値はGatewayがTimeouts.jev_sと同じ値を渡す(design §2.2)
        self._latency_threshold_s = latency_threshold_s
        self._state = CLOSED
        self._opened_at: datetime | None = None
        self._samples: deque[tuple[datetime, bool, float]] = deque()
        self._half_open_probe_pending = False

    @property
    def state(self) -> str:
        return self._state

    def samples(self) -> tuple[tuple[bool, float], ...]:
        """窓内サンプルの読み取り専用ビュー(unit試験・診断用。§9-8)。"""
        return tuple((error, latency) for _, error, latency in self._samples)

    def allow(self, now: datetime) -> bool:
        """第一候補呼び出しの可否。openは期限到達でhalf_openへ遷移する。"""
        if self._state == CLOSED:
            return True
        if self._state == OPEN:
            assert self._opened_at is not None
            elapsed = now - self._opened_at
            if elapsed >= timedelta(seconds=self._params.half_open_after_s):
                self._transition(HALF_OPEN, reason="half_open_after")
                self._half_open_probe_pending = True
                return True  # この呼び出しが半開の試験リクエスト
            return False
        # half_open: 前回の試験リクエストが未recordなら防御的に拒否
        # (WorkerはEvent直列処理のため通常発生しない — design §2.2)
        return not self._half_open_probe_pending

    def record(self, *, error: bool, latency_s: float) -> None:
        """呼び出し1回の記録。窓外サンプルを除去して追記し、遷移を判定。"""
        now = self._clock.now()
        if self._state == HALF_OPEN:
            # 試験リクエストの結果: 成功ならclosed+窓リセット・失敗ならopen戻し
            self._half_open_probe_pending = False
            if error:
                self._transition(OPEN, reason="half_open_probe_failed")
            else:
                self._transition(CLOSED, reason="half_open_probe_succeeded")
            self._opened_at = now if error else None
            self._samples.clear()
            return
        cutoff = now - timedelta(seconds=self._params.window_s)
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()
        self._samples.append((now, error, latency_s))
        if self._state == CLOSED:
            reason = self._open_reason()
            if reason is not None:
                self._transition(OPEN, reason=reason)
                self._opened_at = now

    def _open_reason(self) -> str | None:
        """closed中のrecord直後の開放判定。どちらか先で開放(§9-3)。"""
        n = len(self._samples)
        if n >= self._params.min_samples:
            errors = sum(1 for _, error, _ in self._samples if error)
            if errors / n > self._params.error_rate_threshold:
                return "error_rate"
        latencies = sorted(latency for _, _, latency in self._samples)
        p95_index = math.ceil(0.95 * n) - 1
        if latencies[p95_index] >= self._latency_threshold_s:
            return "p95_latency"
        return None

    def _transition(self, new_state: str, *, reason: str) -> None:
        old = self._state
        self._state = new_state
        logger.info("breaker state %s -> %s reason=%s", old, new_state, reason)
```

- [ ] **Step 4: 実行してパスを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_breaker.py -v`
Expected: PASS 12件

- [ ] **Step 5: lint・basename・コミット**

```bash
make lint && make test
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d  # 空
git add backend/src/latch/llm/breaker.py backend/tests/unit/llm/test_breaker.py
git commit -m "feat: circuit breakerの状態機(llm/breaker.py)とunit試験12件"
```

### Task 2: StubLLMへfail_jev_exc追加(障害注入の種別指定)

**Files:**
- Modify: `backend/src/latch/llm/stub.py`
- Test: `backend/tests/unit/llm/test_stub.py`(追記)

**Interfaces:**
- Produces: `StubLLM(fail_jev_exc=None | "ratelimit" | "overloaded" | "timeout" | "connection")` — judgeがそれぞれ `LLMRateLimitError`/`LLMOverloadedError`/`LLMTimeoutError`/`LLMConnectionError` を送出。既存 `fail_jev=True` は `LLMProviderError` のまま(下位互換)。優先順位は `fail_jev_exc` が先

- [ ] **Step 1: 失敗テストを書く**(`test_stub.py` へ追記。import節へ `LLMRateLimitError, LLMOverloadedError, LLMTimeoutError, LLMConnectionError` を追加)

```python
async def test_stub_fail_jev_exc_exception_map():
    """fail_jev_excの4種が07 §4切替条件の例外種別へ対応(ws-8 design §2.3)。"""
    cases = [
        ("ratelimit", LLMRateLimitError),
        ("overloaded", LLMOverloadedError),
        ("timeout", LLMTimeoutError),
        ("connection", LLMConnectionError),
    ]
    for value, exc_type in cases:
        stub = StubLLM(fail_jev_exc=value)
        with pytest.raises(exc_type):
            await stub.judge("A", "B")


async def test_stub_fail_jev_exc_takes_precedence_over_fail_jev():
    """fail_jev_exc指定時は種別例外が優先(fail_jev=Trueは下位互換のまま)。"""
    stub = StubLLM(fail_jev=True, fail_jev_exc="ratelimit")
    with pytest.raises(LLMRateLimitError):
        await stub.judge("A", "B")


async def test_stub_fail_jev_exc_none_by_default():
    """既定None(既存構成への影響なし)。"""
    stub = StubLLM()
    env = await stub.judge("A", "B")
    assert env["model"] == "jev-1.13.0"
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_stub.py -v -k fail_jev_exc`
Expected: FAIL — `TypeError: StubLLM.__init__() got an unexpected keyword argument 'fail_jev_exc'`

- [ ] **Step 3: 最小実装**(`stub.py`。コンストラクタ引数追加+judge分岐。diff)

```python
# import節へ追加
from latch.llm.errors import (
    LLMConnectionError,
    LLMOverloadedError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)

# コンストラクタシグネチャへ追加(fail_jevの直後):
#         fail_jev: bool = False,
#         fail_jev_exc: str | None = None,

# __init__本体へ追加(self._fail_jev = fail_jev の直後):
#         # 07 §4切替条件の例外種別指定(ws-8 design §2.3)。
#         # None | "ratelimit" | "overloaded" | "timeout" | "connection"
#         self._fail_jev_exc = fail_jev_exc

# judgeを差し替え:
    async def judge(self, intent_a: str, intent_b: str) -> dict:
        await self._apply_delay("jev")
        if self._fail_jev_exc is not None:
            exc = {
                "ratelimit": lambda: LLMRateLimitError("stub: fail_jev_exc=ratelimit"),
                "overloaded": lambda: LLMOverloadedError("stub: fail_jev_exc=overloaded"),
                "timeout": lambda: LLMTimeoutError("stub: fail_jev_exc=timeout"),
                "connection": lambda: LLMConnectionError("stub: fail_jev_exc=connection"),
            }[self._fail_jev_exc]
            raise exc()
        if self._fail_jev:
            raise LLMProviderError("stub: fail_jev=True")
        return copy.deepcopy(self._jev_response)
```

(実装時はラムダdictでなくif連鎖でもよい — ruffが警告しない形で。値の対応と優先順位・既存fail_jev挙動不変が本質)

- [ ] **Step 4: 実行してパスを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_stub.py -v`
Expected: PASS(既存19件+新規3件)

- [ ] **Step 5: lint・コミット**

```bash
make lint && make test
git add backend/src/latch/llm/stub.py backend/tests/unit/llm/test_stub.py
git commit -m "feat: StubLLMへfail_jev_exc追加(429/529/timeout/接続障害の例外種別注入)"
```

### Task 3: JevJudgment.usage + Gateway公開直呼びIF(call_jev_first/call_jev_fallback)+ jev_smoke置換

**Files:**
- Modify: `backend/src/latch/llm/jev.py`(JevJudgmentへusageフィールド1行)
- Modify: `backend/src/latch/llm/gateway.py`(call_jev_first/call_jev_fallback新設。judge_pairの第一候補・フォールバック両側から再利用)
- Modify: `backend/src/latch/llm/jev_smoke.py`(`gateway._jev_fallback.judge` を `gateway.call_jev_fallback` へ置換)
- Test: `backend/tests/unit/llm/test_gateway_jev_switch.py`(追記)

**Interfaces:**
- Consumes: Task 2のstub拡張(試験で使用)・既存 `_jev_call`・`validate_and_normalize`・`_envelope_model`
- Produces: `async def call_jev_first(self, *, intent_a: str, intent_b: str, intent_ids: list[str]) -> JevJudgment`(provider="typesafe_jev"・model=応答バージョンID・usage=envelopeのusage)・`async def call_jev_fallback(...) -> JevJudgment`(provider="fallback_llm"・model=None・usage同様)。**両メソッドはbreakerを参照しない**(§2.8・承認事項6)。`JevJudgment.usage: dict | None = None`(§9-5)。Task 9のg2gate runnerが使用

- [ ] **Step 1: 失敗テストを書く**(`test_gateway_jev_switch.py` へ追記)

```python
async def test_call_jev_first_returns_judgment_with_usage(caplog):
    """公開直呼びIF(第一候補): provider/model/usageをJevJudgmentへ載せる。"""
    gw = _gateway(StubLLM(), _RecordingStub())
    judgment = await gw.call_jev_first(
        intent_a=IA, intent_b=IB, intent_ids=["i-a", "i-b"]
    )
    assert judgment.provider == "typesafe_jev"
    assert judgment.model == "jev-1.13.0"
    assert judgment.usage == {"input_tokens": 0, "output_tokens": 0}
    assert judgment.result["would_a_accept_b"] == 0.5


async def test_call_jev_fallback_returns_judgment():
    """公開直呼びIF(フォールバック): provider=fallback_llm・model=None。"""
    gw = _gateway(StubLLM(), _RecordingStub())
    judgment = await gw.call_jev_fallback(
        intent_a=IA, intent_b=IB, intent_ids=["i-a", "i-b"]
    )
    assert judgment.provider == "fallback_llm"
    assert judgment.model is None
    assert judgment.result["would_b_accept_a"] == 0.5


async def test_call_jev_first_does_not_switch_on_rate_limit():
    """直呼びIFは切替しない(第一候補の例外はそのまま伝播・測定の分離)。"""
    first = _ThrowingJev(LLMRateLimitError("429"))
    gw = _gateway(first, _RecordingStub())
    with pytest.raises(LLMRateLimitError):
        await gw.call_jev_first(intent_a=IA, intent_b=IB, intent_ids=["i-a", "i-b"])


async def test_call_jev_first_invalid_output_propagates_with_provider():
    """出力検証失敗はprovider="typesafe_jev"付きで伝播(judge_pairと同一経路)。"""

    class _Invalid(JevProvider):
        name = "invalid"

        async def judge(self, intent_a: str, intent_b: str) -> dict:
            return {"model": "jev-1.13.0", "answers": {}, "usage": {}}

    gw = _gateway(_Invalid(), _RecordingStub())
    with pytest.raises(JevOutputInvalidError) as ei:
        await gw.call_jev_first(intent_a=IA, intent_b=IB, intent_ids=["i-a", "i-b"])
    assert ei.value.provider == "typesafe_jev"
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_gateway_jev_switch.py -v -k call_jev`
Expected: FAIL — `AttributeError: 'LLMGateway' object has no attribute 'call_jev_first'`

- [ ] **Step 3: 最小実装**

`jev.py` — JevJudgmentへ1行追加(modelフィールドの直後):

```python
    usage: dict | None = None  # envelopeのusage(g2gateの集計用・Layer 4は使わない)
```

`gateway.py` — `_jev_call` の直後に2メソッド新設(judge_pairは本Taskでは未変更。Task 4で両側から再利用する):

```python
    async def call_jev_first(
        self, *, intent_a: str, intent_b: str, intent_ids: list[str]
    ) -> JevJudgment:
        """第一候補(TypeSafe Jev)の直接呼び出し(公開IF・ws-8 design §2.8)。

        g2gate評価とjev_smokeが使う。breakerを参照しない(評価は経路品質の
        実測が目的 — 承認事項6)。judge_pairの第一候補側からも再利用する
        (二重実装なし)。送信記録・timeoutは_call経由でjudge_pairと同一。
        """
        envelope = await self._jev_call(intent_a, intent_b, intent_ids, fallback=False)
        try:
            result = validate_and_normalize(envelope)
        except JevOutputInvalidError as exc:
            raise JevOutputInvalidError(
                str(exc), provider="typesafe_jev"
            ) from exc
        return JevJudgment(
            provider="typesafe_jev",
            model=_envelope_model(envelope),
            result=result,
            usage=envelope.get("usage") if isinstance(envelope, dict) else None,
        )

    async def call_jev_fallback(
        self, *, intent_a: str, intent_b: str, intent_ids: list[str]
    ) -> JevJudgment:
        """フォールバックLLMの直接呼び出し(公開IF・design §2.8)。"""
        envelope = await self._jev_call(intent_a, intent_b, intent_ids, fallback=True)
        try:
            result = validate_and_normalize(envelope)
        except JevOutputInvalidError as exc:
            raise JevOutputInvalidError(
                str(exc), provider="fallback_llm"
            ) from exc
        return JevJudgment(
            provider="fallback_llm",
            model=None,
            result=result,
            usage=envelope.get("usage") if isinstance(envelope, dict) else None,
        )
```

`jev_smoke.py` — FALLBACK=1 分岐を置換:

```python
    if os.environ.get("FALLBACK") == "1":
        judgment = await gateway.call_jev_fallback(
            intent_a=text_a, intent_b=text_b, intent_ids=["jev-smoke-a", "jev-smoke-b"]
        )
        provider = "fallback_llm(直接)"
        model, result = judgment.model, judgment.result
```

(importの `validate_and_normalize` が未使用になるため除去。表示は従来どおりprovider/model/result/expected_model/OK)

- [ ] **Step 4: 実行してパスを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_gateway_jev_switch.py tests/unit/llm/test_jev_format.py tests/unit/test_worker_jev.py -v`
Expected: PASS(既存の切替マトリクス・JevJudgment利用試験はusage既定Noneで無傷)

- [ ] **Step 5: lint・コミット**

```bash
make lint && make test
git add backend/src/latch/llm/jev.py backend/src/latch/llm/gateway.py \
  backend/src/latch/llm/jev_smoke.py backend/tests/unit/llm/test_gateway_jev_switch.py
git commit -m "feat: Gatewayへcall_jev_first/call_jev_fallback公開IF+JevJudgment.usage(jev_smokeのプライベートアクセス解消)"
```

### Task 4: judge_pairへbreaker組み込み + build_worker_gatewayでbreaker生成

**Files:**
- Modify: `backend/src/latch/llm/gateway.py`(judge_pair差し替え・コンストラクタbreaker引数・build_worker_gateway)
- Test: `backend/tests/unit/llm/test_gateway_jev_switch.py`(追記)

**Interfaces:**
- Consumes: Task 1の `CircuitBreaker`・Task 3の `call_jev_first`/`call_jev_fallback`
- Produces: `LLMGateway(..., breaker: CircuitBreaker | None = None)`(既定None=APIプロセス・既存試験は従動作)。内部例外 `_FirstCandidateSkippedOpen(LLMError)`。build_worker_gatewayのstub/real両構成がbreaker付きGatewayを返す(ci常設worker・jev-smokeが自動的にbreaker有効)

- [ ] **Step 1: 失敗テストを書く**(`test_gateway_jev_switch.py` へ追記。importへ `CircuitBreaker`・`Timedelta相当(テストではFakeClockを直接advance)` を追加)

```python
def _breaker_gateway(first, fallback, *, breaker):
    clock = FakeClock(NOW)
    return LLMGateway(
        clock=clock,
        parser=StubLLM(),
        embedding=StubLLM(),
        jev=first,
        jev_fallback=fallback,
        breaker=breaker,
    )


async def test_breaker_open_skips_first_candidate():
    """開放中は第一候補を呼ばずフォールバックへ(Review Focus 2)。"""
    first = _RecordingStub()  # name="fb"流儀の計数スタブ(正常応答)
    first.name = "first"
    breaker = CircuitBreaker(clock=FakeClock(NOW))
    breaker.record(error=True, latency_s=0.1)
    breaker.record(error=True, latency_s=0.1)  # 2失敗/2呼=100%→open
    assert breaker.state == "open"
    gw = _breaker_gateway(first, _RecordingStub(), breaker=breaker)
    judgment = await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert judgment.provider == "fallback_llm"
    assert first.calls == 0  # 第一候補は1回も呼ばれていない


async def test_breaker_none_keeps_existing_behavior():
    """breaker=None(既定)は従動作(第一候補成功でtypesafe_jev)。"""
    gw = _gateway(StubLLM(), _RecordingStub())  # breaker未渡し
    judgment = await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert judgment.provider == "typesafe_jev"


async def test_judge_pair_records_errors_and_opens():
    """judge_pairの第一候補失敗がbreakerへ計上され2回で開放する。"""
    first = _ThrowingJev(LLMRateLimitError("429"))
    clock = FakeClock(NOW)
    breaker = CircuitBreaker(clock=clock)
    gw = _breaker_gateway(first, _RecordingStub(), breaker=breaker)
    for _ in range(2):
        judgment = await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
        assert judgment.provider == "fallback_llm"
    assert breaker.state == "open"
    assert breaker.samples() == ((True, 0.0), (True, 0.0))  # FakeClockで実測0秒


async def test_timeout_latency_replaced_with_timeout_s():
    """timeout呼び出しはレイテンシ=Timeouts.jev_sで記録(Review Focus 3)。"""
    from latch.llm.gateway import Timeouts

    clock = FakeClock(NOW)
    breaker = CircuitBreaker(clock=clock)
    first = StubLLM(delay_jev_ms=200)  # 実遅延(asyncio.timeoutで打ち切り)
    gw = LLMGateway(
        clock=clock,
        parser=StubLLM(),
        embedding=StubLLM(),
        jev=first,
        jev_fallback=_RecordingStub(),
        timeouts=Timeouts(jev_s=0.05),
        breaker=breaker,
    )
    judgment = await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert judgment.provider == "fallback_llm"
    assert breaker.samples() == ((True, 0.05),)  # 実測でなくtimeout_s


async def test_invalid_output_counts_as_success_in_breaker():
    """JevOutputInvalidErrorは呼び出し成功扱い(Review Focus 4)。"""
    from latch.llm.jev import JEV_RESULT_KEYS

    class _InvalidOut(JevProvider):
        name = "invalid"

        async def judge(self, intent_a: str, intent_b: str) -> dict:
            return {"model": "jev-1.13.0", "answers": {}, "usage": {}}

    clock = FakeClock(NOW)
    breaker = CircuitBreaker(clock=clock)
    gw = _breaker_gateway(_InvalidOut(), _RecordingStub(), breaker=breaker)
    with pytest.raises(JevOutputInvalidError):
        await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert breaker.samples() == ((False, 0.0),)  # error=False(成功扱い)
```

(注記: `_InvalidOut` 内のJEV_RESULT_KEYS importは不要になるため実装時に除去。answers={}はvalidate_and_normalizeがJevOutputInvalidErrorを出す最小形)

さらにフォールバック失敗の不計上(Review Focus 5)と半開の実測(unitで先証明):

```python
async def test_fallback_failure_does_not_touch_breaker():
    """フォールバック失敗でbreaker状態は変わらない(design §2.2)。"""
    clock = FakeClock(NOW)
    breaker = CircuitBreaker(clock=clock)
    fb = _ThrowingJev(LLMTimeoutError("fb timeout"))
    gw = _breaker_gateway(StubLLM(), fb, breaker=breaker)
    with pytest.raises(LLMTimeoutError):
        await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    # 第一候補は成功(1件記録)・フォールバックの失敗は計上されない
    assert breaker.samples() == ((False, 0.0),)


async def test_half_open_round_trip_through_judge_pair():
    """開放→60秒→半開で第一候補を1回だけ試験し成功なら閉じる(design §2.4試験4)。"""
    clock = FakeClock(NOW)
    breaker = CircuitBreaker(clock=clock)
    failing = StubLLM(fail_jev_exc="ratelimit")
    healthy = StubLLM()
    gw = _breaker_gateway(failing, _RecordingStub(), breaker=breaker)
    await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert breaker.state == "open"
    clock.advance(timedelta(seconds=60))
    # 半開の試験リクエストは第一候補へ(gateway差し替え: healthyへ)
    gw2 = LLMGateway(
        clock=clock, parser=StubLLM(), embedding=StubLLM(),
        jev=healthy, jev_fallback=_RecordingStub(), breaker=breaker,
    )
    judgment = await gw2.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert judgment.provider == "typesafe_jev"
    assert breaker.state == "closed"
    judgment2 = await gw2.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert judgment2.provider == "typesafe_jev"  # closed後は第一候補へ復帰
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_gateway_jev_switch.py -v -k breaker`
Expected: FAIL — `TypeError: LLMGateway.__init__() got an unexpected keyword argument 'breaker'`

- [ ] **Step 3: 最小実装**(`gateway.py`)

import節へ追加:

```python
from latch.llm.breaker import CircuitBreaker
```

`_envelope_model` の前に内部例外を定義:

```python
class _FirstCandidateSkippedOpen(LLMError):
    """breaker開放中の第一候補スキップ(design §2.2)。

    既存の切替except節へ合流させるためLLMErrorを継承する内部例外。
    呼んでいないため送信記録もbreaker計上も発生しない(正しい)。
    """
```

コンストラクタへ引数追加(`timeouts` の直後):

```python
        timeouts: Timeouts | None = None,
        breaker: CircuitBreaker | None = None,
```

`self._timeouts = ...` の直後:

```python
        # breaker=None(既定)は従動作(APIプロセス・既存試験互換・design §2.2)。
        # build_worker_gatewayの両構成がCircuitBreakerを渡す
        self._breaker = breaker
```

`judge_pair` を差し替え(docstringへbreaker文言を追記のうえ本体):

```python
    async def judge_pair(
        self, *, intent_a: str, intent_b: str, intent_ids: list[str]
    ) -> JevJudgment:
        """07 第4節。2 Intent分の正規化テキスト→7設問JSON。timeout 6秒。

        第一候補=TypeSafe Jev。429/529/timeout/接続障害の4種でフォールバックLLM
        へ切替(07 §4切替表・design §2.2)。LLMProviderError(400系)と
        JevOutputInvalidError(出力検証失敗=実装不整合)は切替せず伝播する。
        切替時は各呼び出しが既存_callを通るため送信記録2件。フォールバック失敗
        (双障害)はそのまま伝播(D-15の縮退はLayer 4が記録に変換する)。
        circuit breaker(ws-8・06 D-15 FR-10): breaker注入時、第一候補側で
        allow/recordする。開放中は第一候補を呼ばず(_FirstCandidateSkippedOpen
        で切替へ合流)フォールバックで継続。JevOutputInvalidErrorは呼び出し
        成功扱いで計上(design §2.2)。フォールバック側はbreakerに計上しない。
        """
        try:
            if self._breaker is None:
                return await self.call_jev_first(
                    intent_a=intent_a, intent_b=intent_b, intent_ids=intent_ids
                )
            if not self._breaker.allow(self._clock.now()):
                raise _FirstCandidateSkippedOpen()
            t0 = self._clock.now()
            try:
                judgment = await self.call_jev_first(
                    intent_a=intent_a, intent_b=intent_b, intent_ids=intent_ids
                )
            except LLMError as exc:
                # timeout(asyncio.timeout打ち切り)は実レイテンシ計測不能のため
                # 打ち切り時点のtimeout_sを記録(design §2.2・承認事項1)。
                # JevOutputInvalidErrorは応答が得られている呼び出し成功扱い。
                invalid_output = isinstance(exc, JevOutputInvalidError)
                if isinstance(exc, LLMTimeoutError):
                    latency_s = self._timeouts.jev_s
                else:
                    latency_s = (self._clock.now() - t0).total_seconds()
                self._breaker.record(error=not invalid_output, latency_s=latency_s)
                raise
            self._breaker.record(
                error=False,
                latency_s=(self._clock.now() - t0).total_seconds(),
            )
            return judgment
        except (
            LLMTimeoutError,
            LLMRateLimitError,
            LLMOverloadedError,
            LLMConnectionError,
            _FirstCandidateSkippedOpen,
        ):
            pass  # 07 §4の切替条件4種+開放中スキップ。LLMProviderError・
            # JevOutputInvalidErrorは伝播
        return await self.call_jev_fallback(
            intent_a=intent_a, intent_b=intent_b, intent_ids=intent_ids
        )
```

`build_worker_gateway` の戻り2箇所へbreakerを渡す:

```python
    if settings.llm_mode == "stub":
        return LLMGateway(
            clock=clock,
            parser=stub,
            embedding=stub,
            jev=stub,
            jev_fallback=stub,
            breaker=CircuitBreaker(clock=clock),
        )
    # real構成のreturnも同様に breaker=CircuitBreaker(clock=clock) を追加
```

- [ ] **Step 4: 実行してパスを確認**

Run: `cd backend && uv run pytest tests/unit/llm/ -v`
Expected: PASS(gateway系すべて。test_llm_factory.pyのbuild_worker_gateway試験は構成検査のためbreaker有無で失敗しない — もし落ちた場合は構成assertへの機械的追従を報告書に記録のうえ修正)

- [ ] **Step 5: lint・コミット**

```bash
make lint && make test
git add backend/src/latch/llm/gateway.py backend/tests/unit/llm/test_gateway_jev_switch.py
git commit -m "feat: judge_pair第一候補側へcircuit breaker組み込み(開放中スキップ・p95計上・build_worker_gatewayで生成)"
```

### Task 5: _SELECT_GROUP_PAIRSへORDER BY追加(ws-7 Minor(a)・決定性明文化)

**Files:**
- Modify: `backend/src/latch/worker/matching/group_engine.py`(`_SELECT_GROUP_PAIRS` の1行追加)
- Test: `backend/tests/unit/matching/test_group_engine.py`(ORDER BYピン1件追記)

**Interfaces:**
- 変更はSQL文字列のみ。Python側の `_select_group_pairs`(行タプル化)は無変更。同ペア複数バージョン行が存在しうる場面での選択順が `ORDER BY intent_a_id, intent_b_id, updated_at DESC`(新行優先)で確定(現実の挙動と同一・design §2.9(a))

- [ ] **Step 1: 失敗テストを書く**(`test_group_engine.py` へ追記。同ファイルのtext()定数ピン流儀)

```python
def test_select_group_pairs_orders_for_determinism():
    """同ペア複数行の選択順をORDER BYで確定(ws-7 Minor(a)・ws-8 design §2.9)。"""
    from latch.worker.matching.group_engine import _SELECT_GROUP_PAIRS

    sql = str(_SELECT_GROUP_PAIRS)
    assert "ORDER BY intent_a_id, intent_b_id, updated_at DESC" in sql
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_group_engine.py -v -k group_pairs_orders`
Expected: FAIL — assert不在

- [ ] **Step 3: 最小実装**(`group_engine.py` の `_SELECT_GROUP_PAIRS`。WHERE句の後に1行)

```python
_SELECT_GROUP_PAIRS = text("""
    SELECT id, intent_a_id, intent_b_id, intent_a_version, intent_b_version,
           jev_result
    FROM match_candidates
    WHERE intent_a_id = ANY(CAST(:ids AS uuid[]))
      AND intent_b_id = ANY(CAST(:ids AS uuid[]))
    ORDER BY intent_a_id, intent_b_id, updated_at DESC
""")
```

(コメントへ「同ペア複数バージョン行の新行優先を確定(ws-8 design §2.9(a)・挙動不変の決定性明文化)」を追記)

- [ ] **Step 4: 実行してパスを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_group_engine.py tests/unit/test_worker_jev.py -v`
Expected: PASS(挙動不変のため既存試験はすべて無傷)

- [ ] **Step 5: lint・コミット**

```bash
make lint && make test
git add backend/src/latch/worker/matching/group_engine.py \
  backend/tests/unit/matching/test_group_engine.py
git commit -m "refactor: _SELECT_GROUP_PAIRSへORDER BY追加(同ペア複数行の新行優先を明文化)"
```

### Task 6: purge-match-sub CLI + Makefile test-ciへ挿入

**Files:**
- Create: `backend/src/latch/events/__main__.py`
- Modify: `Makefile`(test-ciレシピ)
- Test: `backend/tests/unit/test_events_cli.py`(新規)

**Interfaces:**
- Consumes: `PubsubEventBus(settings)`・`bus.delete_subscription()`(NotFound握り済み・同期メソッド)・`bus.close()`
- Produces: `python -m latch.events purge-match-sub` — 設定の常設subscription(settings.pubsub_subscription_match_events・既定match-events-sub)を削除しexit 0。Makefile test-ciのpytest終了後・worker復帰前に実行され、滞留メッセージを構造的に全廃する(worker復帰時の `await bus.ensure()` が再作成・design §2.7案a)

- [ ] **Step 1: 失敗テストを書く**(`backend/tests/unit/test_events_cli.py` 新規)

```python
"""latch.events CLI(purge-match-sub)のunit試験(ws-8 design §2.7)。

PubsubEventBusをスタブへ差し替え、構築→delete_subscription→close→exit 0を
検証する(実エミュレータ不要・外部プロセスなし)。
"""

import pytest

import latch.events.__main__ as events_cli


class _StubBus:
    instances: list["_StubBus"] = []

    def __init__(self, settings) -> None:
        self.settings = settings
        self.deleted = 0
        self.closed = False
        _StubBus.instances.append(self)

    def delete_subscription(self) -> None:
        self.deleted += 1

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def stub_bus(monkeypatch):
    _StubBus.instances = []
    monkeypatch.setattr(events_cli, "PubsubEventBus", _StubBus)
    return _StubBus


async def test_purge_deletes_subscription_and_closes(stub_bus, capsys):
    rc = await events_cli._run_purge(events_cli.Settings())
    assert rc == 0
    (bus,) = stub_bus.instances
    assert bus.deleted == 1
    assert bus.closed is True
    out = capsys.readouterr().out
    assert "purged subscription" in out
    assert "match-events-sub" in out  # 設定の常設subscription名


def test_main_dispatches_purge_match_sub(stub_bus, monkeypatch):
    import asyncio

    rc = events_cli.main(["purge-match-sub"])
    assert rc == 0
    assert stub_bus.instances[0].deleted == 1
    assert asyncio is not None  # mainはasyncio.runで_run_purgeを呼ぶ


def test_main_requires_command():
    with pytest.raises(SystemExit) as ei:
        events_cli.main([])
    assert ei.value.code == 2  # argparse required
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/test_events_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'latch.events.__main__'`

- [ ] **Step 3: 最小実装**(`backend/src/latch/events/__main__.py` 新規・全文)

```python
"""イベント基盤の運用CLI(ws-8 design §2.7)。

python -m latch.events purge-match-sub — 常設match-events subscriptionを削除
する。make test-ci のpytest終了後・worker復帰前に実行し、テスト中にAPIが
publishしたEventの常設subscriptionへの滞留(未配信メッセージ)を構造的に
全廃する(Pub/Subはsubscription削除で未配信メッセージごと消える)。
worker復帰(run)時の await bus.ensure() がcreate_subscription冪等で再作成
するため、workerは次の新規メッセージから処理を始める。存在しない
(NotFound)は「既に滞留なし」と同じ結果のため成功扱い(exit 0)。
"""

from __future__ import annotations

import argparse
import asyncio

from latch.events.pubsub_bus import PubsubEventBus
from latch.settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latch.events",
        description="イベント基盤の運用CLI(ws-8)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "purge-match-sub",
        help="常設match-events subscriptionを削除(滞留メッセージ全廃・"
        "worker復帰時にensure()が再作成)",
    )
    return parser


async def _run_purge(settings: Settings) -> int:
    bus = PubsubEventBus(settings)
    try:
        bus.delete_subscription()  # NotFoundは握る(pubsub_busと同一契約)
    finally:
        await bus.close()
    print(
        "[events] purged subscription:"
        f" {settings.pubsub_project_id}/"
        f"{settings.pubsub_subscription_match_events}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "purge-match-sub":
        return asyncio.run(_run_purge(Settings()))
    raise AssertionError(f"unreachable: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
```

`Makefile` — test-ciレシピを差し替え(rc保持・design §2.7):

```makefile
test-ci: ## ci環境試験(worker停止→unit+integration→subscription掃除→worker復帰。--group geoでosmium込み)
	docker compose up -d --wait
	docker compose stop worker
	cd backend && uv run --group geo pytest; rc=$$?; \
	uv run python -m latch.events purge-match-sub; docker compose start worker; exit $$rc
```

- [ ] **Step 4: 実行してパスを確認**

Run: `cd backend && uv run pytest tests/unit/test_events_cli.py tests/unit/test_pubsub_bus_sdk_calls.py -v`
Expected: PASS(新規3件+pubsub回帰)

- [ ] **Step 5: lint・コミット**

```bash
make lint && make test
git add backend/src/latch/events/__main__.py backend/tests/unit/test_events_cli.py Makefile
git commit -m "feat: purge-match-sub CLIとtest-ciへの挿入(常設subscription掃除で孤立行を構造的に防止)"
```

### Task 7: g2gate/cases.py(goldset読込・JevTextInput変換・SHA)

**Files:**
- Create: `backend/src/latch/g2gate/cases.py`
- Test: `backend/tests/unit/g2gate/test_cases_compare.py`(新規・cases分)

**Interfaces:**
- Consumes: `yaml.safe_load`・pydantic(_IgnoreExtra流儀)・`latch.llm.jev.JevTextInput`
- Produces: `G2_BASE_CURRENT_DATETIME = datetime(2026,10,1,11,30,tzinfo=JST)`・`Goldset(pairs: list[GoldsetPair], inputs: dict[str, JevTextInput], expected: dict[str, PairExpected], yaml_sha256: str, meta: GoldsetMeta)`・`load_goldset(path: Path) -> Goldset`(fail-fast検査つき)。Task 8/9が使用

- [ ] **Step 1: 失敗テストを書く**(`backend/tests/unit/g2gate/test_cases_compare.py` 新規・cases分)

```python
"""g2gate部品(cases・compare・report・__main__)のunit試験(ws-8 design §2.8・§4.1)。

合成入力のみ(実APIなし・g1gateと同一規律)。指標は手計算既知値で検証する。
"""

import hashlib
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from latch.core.clock import JST
from latch.g2gate.cases import (
    G2_BASE_CURRENT_DATETIME,
    load_goldset,
)

BASE = {
    "meta": {
        "status": "confirmed",
        "created": "2026-09-29",
        "current_datetime": "2026-10-01T11:30:00+09:00",
        "pair_count": 1,
        "intent_count": 2,
        "gold_true": 1,
        "gold_false": 0,
    },
    "intents": [
        {
            "id": "SI-001",
            "user": "U-001",
            "author_age": 34,
            "structured": {
                "category": {"primary": "drinking", "secondary": "居酒屋"},
                "alcohol_involved": True,
                "time": {
                    "start": "2026-10-01T20:00:00+09:00",
                    "end": "2026-10-01T23:00:00+09:00",
                },
                "location": {"name": "天文館", "radius_m": 1000},
                "budget": {"max": 3500},
                "participants": {"min": 2, "max": 2},
                "soft_constraints": ["軽く飲みたい"],
                "ng_unverifiable": ["会社関係の人は避けたい"],
            },
            "source": "drafted",
        },
        {
            "id": "SI-002",
            "user": "U-002",
            "author_age": 30,
            "structured": {
                "category": {"primary": "drinking"},
                "alcohol_involved": True,
                "time": {
                    "start": "2026-10-01T20:00:00+09:00",
                    "end": "2026-10-01T23:00:00+09:00",
                },
                "location": {"name": "天文館", "radius_m": 1000},
                "budget": {"max": 3000},
                "participants": {"min": 2, "max": 2},
                "soft_constraints": [],
                "ng_unverifiable": [],
            },
            "source": "machine",
        },
    ],
    "pairs": [
        {
            "id": "GP-001",
            "intent_a": "SI-001",
            "intent_b": "SI-002",
            "kind": "1to1",
            "segment": "lexical",
            "layer1_pass": True,
            "expected": {
                "gold_mutual": True,
                "would_a_accept_b": {"label": "accept", "band": "high"},
                "would_b_accept_a": {"label": "accept", "band": "high"},
                "purpose_fit": 4,
                "mood_fit": 3,
                "timing_fit": 4,
                "social_fit": 4,
                "latent_yes": {"band": "mid"},
            },
            "rationale": "test",
            "flag": False,
            "source": "drafted",
        }
    ],
}


def _write_goldset(tmp_path: Path, data: dict | None = None) -> Path:
    path = tmp_path / "g2-jev-goldset.yaml"
    path.write_text(
        yaml.safe_dump(data if data is not None else BASE, allow_unicode=True),
        encoding="utf-8",
    )
    return path


def test_load_goldset_returns_inputs_and_sha(tmp_path):
    g = load_goldset(_write_goldset(tmp_path))
    assert g.meta.status == "confirmed"
    assert g.yaml_sha256 == hashlib.sha256(
        (tmp_path / "g2-jev-goldset.yaml").read_bytes()
    ).hexdigest()
    a = g.inputs["SI-001"]
    assert a.category_primary == "drinking"
    assert a.time_start == datetime(2026, 10, 1, 20, 0, tzinfo=JST)
    assert a.budget_max == 3500
    assert a.geo_radius_m == 1000
    assert g.expected["GP-001"].gold_mutual is True


def test_load_goldset_ng_unverifiable_becomes_downgraded_soft(tmp_path):
    g = load_goldset(_write_goldset(tmp_path))
    soft = g.inputs["SI-001"].structured_data["soft_constraints"]
    # 順序: {Falseの通常行} + {ng_unverifiable由来行}(引用#12・07 §4)
    assert soft == [
        {"text": "軽く飲みたい", "downgraded_from_ng": False},
        {"text": "会社関係の人は避けたい", "downgraded_from_ng": True},
    ]


def test_load_goldset_rejects_unconfirmed(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE, allow_unicode=True))
    data["meta"]["status"] = "draft"
    with pytest.raises(ValueError, match="confirmed"):
        load_goldset(_write_goldset(tmp_path, data))


def test_load_goldset_rejects_wrong_current_datetime(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE, allow_unicode=True))
    data["meta"]["current_datetime"] = "2026-10-02T11:30:00+09:00"
    with pytest.raises(ValueError, match="current_datetime"):
        load_goldset(_write_goldset(tmp_path, data))


def test_load_goldset_rejects_count_mismatch(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE, allow_unicode=True))
    data["meta"]["pair_count"] = 2
    with pytest.raises(ValueError, match="pair_count"):
        load_goldset(_write_goldset(tmp_path, data))


def test_load_goldset_rejects_gold_true_mismatch(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE, allow_unicode=True))
    data["meta"]["gold_true"] = 0
    with pytest.raises(ValueError, match="gold"):
        load_goldset(_write_goldset(tmp_path, data))


def test_load_goldset_rejects_unknown_pair_reference(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(BASE, allow_unicode=True))
    data["pairs"][0]["intent_b"] = "SI-999"
    with pytest.raises(ValueError, match="SI-999"):
        load_goldset(_write_goldset(tmp_path, data))


def test_base_current_datetime_pin():
    assert G2_BASE_CURRENT_DATETIME == datetime(2026, 10, 1, 11, 30, tzinfo=JST)
    assert G2_BASE_CURRENT_DATETIME == datetime.fromisoformat(
        "2026-10-01T11:30:00+09:00"
    )
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/g2gate/test_cases_compare.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'latch.g2gate'`

- [ ] **Step 3: 最小実装**(`backend/src/latch/g2gate/cases.py` 新規・全文。空の `__init__.py` も作成 — Task 9で公開IFを書く)

```python
"""G2評価ゴールドセット(docs/testassets/g2-jev-goldset.yaml)の読み込みと検証。

YAMLはconfirmed・読み取り専用(g1gate casesと同一規律)。構造検証(pydantic)
+meta整合(件数・gold構成・基準日時)+id一意・ペア参照存在をfail-fastで行う。
SHA256をGoldsetへ載せて返す(レポートmetaの証跡)。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from latch.core.clock import JST
from latch.llm.jev import JevTextInput

# goldset meta.current_datetimeと一致検査する基準日時(README「共通の前提」
# と同一・g1と同時刻)。正規化テキストの組み立てはClock非依為だが、Gatewayの
# 送信記録occurred_atがこの基準になる
G2_BASE_CURRENT_DATETIME = datetime(2026, 10, 1, 11, 30, tzinfo=JST)


class _IgnoreExtra(BaseModel):
    model_config = ConfigDict(extra="ignore")


class StructuredCategory(_IgnoreExtra):
    primary: str
    secondary: str | None = None


class StructuredTime(_IgnoreExtra):
    start: str  # ISO8601(+09:00)。アプリ層補完後のためendは必須(機械検査済み)
    end: str


class StructuredLocation(_IgnoreExtra):
    name: str
    radius_m: int  # 補完後(Null→1000)のため必須


class StructuredBudget(_IgnoreExtra):
    max: int | None = None


class StructuredParticipants(_IgnoreExtra):
    min: int
    max: int


class StructuredIntent(_IgnoreExtra):
    category: StructuredCategory
    alcohol_involved: bool
    time: StructuredTime
    location: StructuredLocation
    budget: StructuredBudget
    participants: StructuredParticipants
    soft_constraints: list[str] = []
    ng_unverifiable: list[str] = []


class GoldsetIntent(_IgnoreExtra):
    id: str
    user: str
    author_age: int | None = None
    structured: StructuredIntent
    source: str | None = None


class BandExpectation(_IgnoreExtra):
    label: str | None = None  # latent_yesはbandのみ
    band: str


class PairExpected(_IgnoreExtra):
    gold_mutual: bool
    would_a_accept_b: BandExpectation
    would_b_accept_a: BandExpectation
    purpose_fit: int
    mood_fit: int
    timing_fit: int
    social_fit: int
    latent_yes: BandExpectation


class GoldsetPair(_IgnoreExtra):
    id: str
    intent_a: str
    intent_b: str
    kind: str
    segment: str
    layer1_pass: bool
    expected: PairExpected
    flag: bool = False
    fail_reason: str | None = None
    source: str | None = None


class GoldsetMeta(_IgnoreExtra):
    status: str
    created: str
    current_datetime: datetime
    pair_count: int
    intent_count: int
    gold_true: int
    gold_false: int


class _GoldsetRoot(_IgnoreExtra):
    meta: GoldsetMeta
    intents: list[GoldsetIntent]
    pairs: list[GoldsetPair]


@dataclass(frozen=True)
class Goldset:
    pairs: list[GoldsetPair]
    inputs: dict[str, JevTextInput]
    expected: dict[str, PairExpected]
    yaml_sha256: str
    meta: GoldsetMeta


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: トップレベルはマッピングである必要があります")
    return data


def _verify_unique_ids(ids: list[str], *, kind: str) -> None:
    if len(set(ids)) != len(ids):
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"{kind}: idが重複しています: {duplicated}")


def _to_jev_input(item: GoldsetIntent) -> JevTextInput:
    """intents→JevTextInput(goldset-plan §11手順3・07 §4正規化テキストの材料)。

    ng_unverifiableはdowngraded_from_ng=Trueのsoft行へ変換(引用#12)。
    順序は{Falseの通常行}+{ng_unverifiable由来行}。visibility・
    notification_levelはJevTextInput自体が持たないため構造的に含まれない。
    """
    s = item.structured
    soft: list[dict] = [
        {"text": t, "downgraded_from_ng": False} for t in s.soft_constraints
    ]
    soft += [
        {"text": t, "downgraded_from_ng": True} for t in s.ng_unverifiable
    ]
    return JevTextInput(
        category_primary=s.category.primary,
        structured_data={
            "location_name": s.location.name,
            "soft_constraints": soft,
        },
        participants_min=s.participants.min,
        participants_max=s.participants.max,
        time_start=datetime.fromisoformat(s.time.start),
        time_end=datetime.fromisoformat(s.time.end),
        budget_max=s.budget.max,
        geo_radius_m=s.location.radius_m,
    )


def load_goldset(path: Path) -> Goldset:
    """g2-jev-goldset.yamlを読み、構造・meta整合を検証してGoldsetを返す。"""
    parsed = _GoldsetRoot.model_validate(_load_yaml(path))
    if parsed.meta.status != "confirmed":
        raise ValueError(
            f"{path.name}: statusがconfirmedではありません: {parsed.meta.status!r}"
        )
    if parsed.meta.current_datetime != G2_BASE_CURRENT_DATETIME:
        raise ValueError(
            f"{path.name}: meta.current_datetime"
            f"({parsed.meta.current_datetime.isoformat()})が基準日時"
            f"({G2_BASE_CURRENT_DATETIME.isoformat()})と一致しません"
        )
    _verify_unique_ids([i.id for i in parsed.intents], kind=path.name)
    _verify_unique_ids([p.id for p in parsed.pairs], kind=path.name)
    if len(parsed.pairs) != parsed.meta.pair_count:
        raise ValueError(
            f"{path.name}: pairs {len(parsed.pairs)}件がmeta.pair_count"
            f" {parsed.meta.pair_count}と一致しません"
        )
    if len(parsed.intents) != parsed.meta.intent_count:
        raise ValueError(
            f"{path.name}: intents {len(parsed.intents)}件がmeta.intent_count"
            f" {parsed.meta.intent_count}と一致しません"
        )
    gold_true = sum(1 for p in parsed.pairs if p.expected.gold_mutual)
    gold_false = len(parsed.pairs) - gold_true
    if gold_true != parsed.meta.gold_true or gold_false != parsed.meta.gold_false:
        raise ValueError(
            f"{path.name}: gold構成(true {gold_true}/false {gold_false})が"
            f"meta(gold_true {parsed.meta.gold_true}/"
            f"gold_false {parsed.meta.gold_false})と一致しません"
        )
    intent_ids = {i.id for i in parsed.intents}
    for p in parsed.pairs:
        for ref in (p.intent_a, p.intent_b):
            if ref not in intent_ids:
                raise ValueError(f"{path.name}: ペア{p.id}が未知のintentを参照: {ref}")
        if p.intent_a == p.intent_b:
            raise ValueError(f"{path.name}: ペア{p.id}が自己ペア: {p.intent_a}")
    return Goldset(
        pairs=parsed.pairs,
        inputs={i.id: _to_jev_input(i) for i in parsed.intents},
        expected={p.id: p.expected for p in parsed.pairs},
        yaml_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        meta=parsed.meta,
    )
```

(注記: `soft_constraints: list[str] = []` のmutable既定はpydanticが安全にコピーするためこの形でよい(既存g1gateと同一・ruffのB006はpydanticモデルで非対象。警告が出たら `Field(default_factory=list)` へ)

- [ ] **Step 4: 実行してパスを確認**

Run: `cd backend && uv run pytest tests/unit/g2gate/test_cases_compare.py -v`
Expected: PASS(cases分9件)

- [ ] **Step 5: lint・コミット**

```bash
make lint && make test
git add backend/src/latch/g2gate/ backend/tests/unit/g2gate/
git commit -m "feat: g2gate cases(goldset読込・JevTextInput変換・SHA・fail-fast検査)"
```

### Task 8: g2gate/compare.py(指標算出・純関数)

**Files:**
- Create: `backend/src/latch/g2gate/compare.py`
- Test: `backend/tests/unit/g2gate/test_cases_compare.py`(追記)

**Interfaces:**
- Consumes: Task 7の `PairExpected`・Task 9で定義する `PairOutcome`(本Taskでは仮依存 — runner先行定義に注意。**PairOutcomeはrunner.py(Task 9)に置くため、compareはduck-typingで `outcome.mutual_score`・`outcome.pair_id`・`outcome.route`・`outcome.judgment`・`outcome.error` を読む**。テストではcompare試験用の最小dataclassをテスト側で定義する)
- Produces: `THRESHOLDS=(0.70,0.80,0.90)`・`band_of(value)`・`expected_calibration_error(scores, golds)`・`brier_score(scores, golds)`・`auc_separation(pos, neg)`・`compute_metrics(outcomes, goldset) -> dict`

- [ ] **Step 1: 失敗テストを書く**(`test_cases_compare.py` へ追記)

```python
# -- compare(手計算既知値・design §4.1) --

from dataclasses import dataclass

from latch.llm.jev import JevJudgment
from latch.g2gate.compare import (
    THRESHOLDS,
    auc_separation,
    band_of,
    brier_score,
    compute_metrics,
    expected_calibration_error,
)


@dataclass(frozen=True)
class _Outcome:  # runner.PairOutcomeと同じ形(duck-typing)
    pair_id: str
    route: str
    judgment: JevJudgment | None
    error: str | None

    @property
    def mutual_score(self) -> float | None:
        if self.judgment is None:
            return None
        r = self.judgment.result
        return min(r["would_a_accept_b"], r["would_b_accept_a"])


def _judgment(would_a: float, would_b: float) -> JevJudgment:
    result = {
        "would_a_accept_b": would_a,
        "would_b_accept_a": would_b,
        "jev_5axis": {
            k: {"value": 0.5, "confidence": None}
            for k in ("purpose_fit", "mood_fit", "timing_fit", "social_fit")
        } | {"latent_yes": {"value": 0.5, "confidence": None}},
    }
    return JevJudgment(provider="typesafe_jev", model="jev-1.13.0", result=result)


def test_thresholds_pin():
    assert THRESHOLDS == (0.70, 0.80, 0.90)  # 09 §4.2・D-01


def test_band_of_boundaries():
    """band境界: low<0.35 / 0.35≤mid<0.65 / high≥0.65(goldset-plan §5)。"""
    assert band_of(0.349) == "low"
    assert band_of(0.35) == "mid"
    assert band_of(0.649) == "mid"
    assert band_of(0.65) == "high"
    assert band_of(1.0) == "high"


def test_precision_recall_known_values():
    """P/R手計算: L=[0.9(T),0.85(T),0.4(F)]・閾値0.80→提案2件・TP2・FP0。"""
    m = compute_metrics(
        [
            _Outcome("p1", "first", _judgment(0.95, 0.9), None),
            _Outcome("p2", "first", _judgment(0.9, 0.85), None),
            _Outcome("p3", "first", _judgment(0.5, 0.4), None),
        ],
        _goldset_with_gold({"p1": True, "p2": True, "p3": False}),
    )
    t80 = m["thresholds"]["0.8"]
    assert t80["precision"] == 1.0
    assert t80["recall"] == 1.0
    t70 = m["thresholds"]["0.7"]
    assert t70["precision"] == 1.0
    t90 = m["thresholds"]["0.9"]
    # 閾値0.90: 提案={0.95}(0.9は0.90>=0.90で提案)→ p1,p2 が提案
    assert t90["precision"] == 1.0


def test_ece_known_value():
    """ECE手計算: pred 0.9(gold T)と0.1(gold F)→ 各bin |0.9-1.0|=0.1・|0.1-0.0|=0.1
    → 加重平均 0.5*(0.1+0.1)=0.1。"""
    ece = expected_calibration_error([0.9, 0.1], [True, False])
    assert ece == pytest.approx(0.1)


def test_brier_known_value():
    """Brier手計算: (0.9-1)^2+(0.1-0)^2=0.02 → 平均0.01。"""
    assert brier_score([0.9, 0.1], [True, False]) == pytest.approx(0.01)


def test_auc_perfect_reversal_and_tie():
    """分離度: 完全分離=1.0・完全逆転=0.0・完全タイ=0.5(Mann-Whitney U)。"""
    assert auc_separation([0.9, 0.8], [0.3, 0.2]) == 1.0
    assert auc_separation([0.2, 0.3], [0.8, 0.9]) == 0.0
    assert auc_separation([0.5, 0.5], [0.5, 0.5]) == 0.5
    # 部分タイ: pos=[0.9,0.5] neg=[0.5,0.1] → U=(勝1+タイ0.5)/2=0.75
    assert auc_separation([0.9, 0.5], [0.5, 0.1]) == pytest.approx(0.75)


def test_compute_metrics_counts_failures_and_diagnostics():
    """失敗ペアは指標分母から除外せず失敗数で報告(design §2.8)。"""
    outcomes = [
        _Outcome("p1", "first", _judgment(0.95, 0.9), None),
        _Outcome("p2", "first", None, "LLMRateLimitError"),
    ]
    m = compute_metrics(
        outcomes, _goldset_with_gold({"p1": True, "p2": True})
    )
    assert m["pair_count"] == 2
    assert m["success_count"] == 1
    assert m["failure_count"] == 1
    assert m["failures"] == {"LLMRateLimitError": 1}
    # diagnostics: fit軸の±1一致率(期待4・実測0.5*4=2.0 → |2-4|=2>1 → 不一致)
    assert m["diagnostics"]["fit_within_1_rate"]["purpose_fit"] == 0.0


def _goldset_with_gold(gold: dict[str, bool]):
    """compute_metrics試験用の最小goldset(expectedのみ使用)。"""
    from latch.g2gate.cases import Goldset, PairExpected

    class _Pairs(list):
        pass

    pairs = []
    expected = {}
    for pair_id, g in gold.items():
        exp = PairExpected(
            gold_mutual=g,
            would_a_accept_b={"label": "accept", "band": "high"},
            would_b_accept_a={"label": "accept", "band": "high"},
            purpose_fit=4,
            mood_fit=4,
            timing_fit=4,
            social_fit=4,
            latent_yes={"label": None, "band": "mid"},
        )
        expected[pair_id] = exp
    return Goldset(
        pairs=pairs, inputs={}, expected=expected, yaml_sha256="x", meta=None
    )
```

(pairs/metaはcompute_metricsが使わないため空/Noneでよい — 実装 signatureを `compute_metrics(outcomes, goldset)` としgoldsetからは `goldset.expected` のみ読む)

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/g2gate/test_cases_compare.py -v -k "band or auc or brier or ece or threshold or compute"`
Expected: FAIL — `ModuleNotFoundError: No module named 'latch.g2gate.compare'`

- [ ] **Step 3: 最小実装**(`backend/src/latch/g2gate/compare.py` 新規・全文)

```python
"""G2評価の指標算出(09 §4〜§4.2・goldset-plan §11手順4〜5)。すべて純関数。

- MutualScore = min(would_a_accept_b, would_b_accept_a)・L = H×MutualScore×C
  (H=1・C=1 — layer1_pass=falseの30件はJev単体検証のためH=1で通す・§2.8)
- Precision/Recall: 提案=L≥閾値(0.70/0.80/0.90)・実YES=gold_mutual
- ECE: MutualScoreを10分割ビン([0,0.1)…[0.9,1])・各ビン|平均予測−実YES率|の
  加重平均(空ビンは重み0で自然に消える)
- Brier: (L − gold)^2 の平均
- Mutual Acceptance Precision: gold true群とfalse群のMutualScore分布の分離度
  — Mann-Whitney Uに基づくAUC(09 §4.2「ランク指標」の実装解釈・supervisor採用)
- 診断(参考値・正式な集計方法はG2実施時に確定 — goldset-plan §5):
  would_*のband(low/mid/high)一致率・latent_yes band一致率・
  fit軸0〜4の±1以内一致率(実測は正規化値のため×4で復元)
失敗ペアは指標の分母から除外せず失敗数で報告する(design §2.8)。
"""

from __future__ import annotations

THRESHOLDS = (0.70, 0.80, 0.90)  # 09 §4.2・D-01の3点
ECE_BINS = 10
BAND_LOW, BAND_HIGH = 0.35, 0.65  # goldset-plan §5(low<0.35/mid/high≥0.65)
SCORE_AXIS_KEYS = ("purpose_fit", "mood_fit", "timing_fit", "social_fit")


def band_of(value: float) -> str:
    if value < BAND_LOW:
        return "low"
    if value < BAND_HIGH:
        return "mid"
    return "high"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def precision_recall(
    scores: list[float], golds: list[bool], threshold: float
) -> tuple[float | None, float | None]:
    """提案=L≥閾値・実YES=gold。提案0件/gold true 0件はNone(レポートはnull)。"""
    tp = sum(1 for s, g in zip(scores, golds) if s >= threshold and g)
    proposed = sum(1 for s in scores if s >= threshold)
    precision = tp / proposed if proposed else None
    total_true = sum(1 for g in golds if g)
    recall = tp / total_true if total_true else None
    return precision, recall


def expected_calibration_error(
    scores: list[float], golds: list[bool], *, bins: int = ECE_BINS
) -> float | None:
    """10分割ビンECE。最終ビンは1.0を含む閉区間(§9-13)。"""
    n = len(scores)
    if n == 0:
        return None
    total = 0.0
    for i in range(bins):
        lo = i / bins
        hi = (i + 1) / bins
        idx = [
            k
            for k, s in enumerate(scores)
            if (lo <= s < hi) or (i == bins - 1 and s == 1.0)
        ]
        if not idx:
            continue
        avg = _mean([scores[k] for k in idx])
        obs = _mean([1.0 if golds[k] else 0.0 for k in idx])
        total += len(idx) / n * abs(avg - obs)
    return total


def brier_score(scores: list[float], golds: list[bool]) -> float | None:
    if not scores:
        return None
    return _mean([(s - (1.0 if g else 0.0)) ** 2 for s, g in zip(scores, golds)])


def _rankdata(values: list[float]) -> list[float]:
    """1-indexed順位。タイは平均順位(Mann-Whitney Uの標準形)。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def auc_separation(pos: list[float], neg: list[float]) -> float | None:
    """gold true群(pos)がfalse群(neg)より上位に置けた率(U統計量ベースAUC)。"""
    if not pos or not neg:
        return None
    ranks = _rankdata(pos + neg)
    r_pos = sum(ranks[: len(pos)])
    u = r_pos - len(pos) * (len(pos) + 1) / 2
    return u / (len(pos) * len(neg))


def compute_metrics(outcomes, goldset) -> dict:
    """PairOutcome群+goldset期待値→指標dict。outcomesは単一routeのリスト。"""
    from latch.g2gate.cases import PairExpected  # 循環import回避の局部import

    expected: dict[str, PairExpected] = goldset.expected
    success = [o for o in outcomes if o.error is None and o.judgment is not None]
    failed = [o for o in outcomes if o.error is not None]
    scores = [o.mutual_score for o in success]  # type: ignore[misc]
    golds = [expected[o.pair_id].gold_mutual for o in success]
    pos = [s for s, g in zip(scores, golds) if g]
    neg = [s for s, g in zip(scores, golds) if not g]
    thresholds: dict[str, dict] = {}
    for t in THRESHOLDS:
        precision, recall = precision_recall(scores, golds, t)
        thresholds[f"{t:.1f}"] = {"precision": precision, "recall": recall}
    # 診断(参考値)
    band_match = {"would_a": [], "would_b": [], "latent_yes": []}
    fit_within_1: dict[str, list[bool]] = {k: [] for k in SCORE_AXIS_KEYS}
    for o in success:
        exp = expected[o.pair_id]
        result = o.judgment.result
        band_match["would_a"].append(
            band_of(result["would_a_accept_b"]) == exp.would_a_accept_b.band
        )
        band_match["would_b"].append(
            band_of(result["would_b_accept_a"]) == exp.would_b_accept_a.band
        )
        band_match["latent_yes"].append(
            band_of(result["jev_5axis"]["latent_yes"]["value"])
            == exp.latent_yes.band
        )
        for key in SCORE_AXIS_KEYS:
            actual = result["jev_5axis"][key]["value"] * 4  # 正規化を0〜4へ復元
            fit_within_1[key].append(abs(actual - getattr(exp, key)) <= 1.0)
    diagnostics = {
        "would_a_band_match_rate": _mean([1.0 if x else 0.0 for x in band_match["would_a"]]) if success else None,
        "would_b_band_match_rate": _mean([1.0 if x else 0.0 for x in band_match["would_b"]]) if success else None,
        "latent_band_match_rate": _mean([1.0 if x else 0.0 for x in band_match["latent_yes"]]) if success else None,
        "fit_within_1_rate": {
            k: (_mean([1.0 if x else 0.0 for x in v]) if v else None)
            for k, v in fit_within_1.items()
        },
    }
    failures: dict[str, int] = {}
    for o in failed:
        failures[o.error] = failures.get(o.error, 0) + 1
    return {
        "pair_count": len(outcomes),
        "success_count": len(success),
        "failure_count": len(failed),
        "failures": failures,
        "thresholds": thresholds,
        "ece": expected_calibration_error(scores, golds),
        "brier": brier_score(scores, golds),
        "auc": auc_separation(pos, neg),
        "diagnostics": diagnostics,
    }
```

- [ ] **Step 4: 実行してパスを確認**

Run: `cd backend && uv run pytest tests/unit/g2gate/test_cases_compare.py -v`
Expected: PASS(cases+compare分)

- [ ] **Step 5: lint・コミット**

```bash
make lint && make test
git add backend/src/latch/g2gate/compare.py backend/tests/unit/g2gate/test_cases_compare.py
git commit -m "feat: g2gate compare(P/R・ECE・Brier・Mann-Whitney AUC・band診断の純関数)"
```

### Task 9: g2gate runner・report・__main__・__init__ + make g2-gate

**Files:**
- Create: `backend/src/latch/g2gate/runner.py`・`report.py`・`__main__.py`
- Modify: `backend/src/latch/g2gate/__init__.py`(公開IF)・`Makefile`(g2-gateターゲット)
- Test: `backend/tests/unit/g2gate/test_cases_compare.py`(追記)

**Interfaces:**
- Consumes: Task 3の `call_jev_first`/`call_jev_fallback`・Task 7 `load_goldset`・Task 8 `compute_metrics`・`build_worker_gateway`・`build_jev_text`
- Produces: `PairOutcome`(runner.py)・`run_routes(gateway, goldset, *, route="both", limit=None, echo=None) -> list[PairOutcome]`・`usage_totals(outcomes) -> dict[route, dict]`・`build_report(...)`・`write_report(report, *, out_dir, executed_at) -> Path`・`main(argv) -> int`(起動検証fail-fast)。CLI: `--assets`(既定 `../docs/testassets`)・`--out`(既定 `<assets>/results`)・`--limit`・`--route first|fallback|both`(既定both)

- [ ] **Step 1: 失敗テストを書く**(`test_cases_compare.py` へ追記)

```python
# -- report/__main__(design §4.1) --

from latch.g2gate.report import build_report, write_report


def test_build_report_partial_flag_and_note():
    """--limit時はpartial=true・レポート先頭に「部分実行=証拠外」(design §2.8)。"""
    report = build_report(
        executed_at=datetime(2026, 10, 1, 11, 30, tzinfo=JST),
        route="both",
        goldset_file="g2-jev-goldset.yaml",
        goldset_sha256="sha",
        questions_sha256="qsha",
        metrics_by_route={"first": {}, "fallback": {}},
        outcomes=[],
        usage_totals={"first": {"input_tokens": 10}, "fallback": {}},
        limit=2,
    )
    first_key = next(iter(report))
    assert first_key == "note"
    assert "部分実行" in report["note"]
    assert report["meta"]["partial"] is True
    assert report["meta"]["pair_count"] == 0


def test_build_report_full_run_has_no_note():
    report = build_report(
        executed_at=datetime(2026, 10, 1, 11, 30, tzinfo=JST),
        route="first",
        goldset_file="g2-jev-goldset.yaml",
        goldset_sha256="sha",
        questions_sha256="qsha",
        metrics_by_route={"first": {}},
        outcomes=[],
        usage_totals={"first": {}},
        limit=None,
    )
    assert "note" not in report
    assert report["meta"]["partial"] is False


def test_write_report_basename(tmp_path):
    executed = datetime(2026, 10, 1, 20, 30, tzinfo=JST)
    path = write_report({"meta": {"partial": True}}, out_dir=tmp_path, executed_at=executed)
    assert path.name == "g2-jev-result-20261001-203000.yaml"
    assert path.exists()


def test_main_rejects_stub_mode(monkeypatch, capsys):
    """起動検証: stubのまま実測したと錯覚させない(g1gateと同一規律)。"""
    monkeypatch.setenv("LATCH_LLM_MODE", "stub")
    from latch.g2gate.__main__ import main

    assert main([]) == 2
    assert "real" in capsys.readouterr().err


def test_main_rejects_missing_typesafe_key(monkeypatch, capsys):
    monkeypatch.setenv("LATCH_LLM_MODE", "real")
    monkeypatch.setenv("LATCH_ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("LATCH_GEMINI_API_KEY", "x")
    monkeypatch.delenv("LATCH_TYPESAFE_API_KEY", raising=False)
    from latch.g2gate.__main__ import main

    assert main([]) == 2
    assert "TYPESAFE" in capsys.readouterr().err


def test_run_routes_uses_public_if_and_counts_failures(monkeypatch):
    """run_routesはcall_jev_first/fallback直呼び・失敗は記録して継続。"""
    import asyncio

    from latch.g2gate.runner import run_routes

    class _Gateway:
        def __init__(self):
            self.first_calls = 0
            self.fallback_calls = 0

        async def call_jev_first(self, *, intent_a, intent_b, intent_ids):
            self.first_calls += 1
            if self.first_calls == 1:
                raise LLMRateLimitError("429")
            return _judgment(0.9, 0.9)

        async def call_jev_fallback(self, *, intent_a, intent_b, intent_ids):
            self.fallback_calls += 1
            return _judgment(0.8, 0.8)

    goldset = load_goldset(_write_goldset(Path("/tmp/_unused")))
    outcomes = asyncio.run(run_routes(_Gateway(), goldset, route="both"))
    assert len(outcomes) == 2  # 520ペアだが合成goldsetは1ペア×両経路
    assert [o.route for o in outcomes] == ["first", "fallback"]
    assert outcomes[0].error == "LLMRateLimitError"
    assert outcomes[0].mutual_score is None
    assert outcomes[1].mutual_score == 0.8
```

(注記: `test_run_routes_uses_public_if_and_counts_failures` のgoldsetはfixtureの `_write_goldset(tmp_path)` を使う — 関数シグネチャを `def test_run_routes_uses_public_if_and_counts_failures(monkeypatch, tmp_path)` に修正して `_write_goldset(tmp_path)` から読むこと)

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/g2gate/test_cases_compare.py -v -k "report or main or run_routes"`
Expected: FAIL — `ModuleNotFoundError: No module named 'latch.g2gate.report'`

- [ ] **Step 3: 最小実装**

`runner.py`(新規・全文):

```python
"""G2評価の実行経路(goldset-plan §11手順3〜4・design §2.8)。

各ペアを正規化テキスト(07 §4形式)へ組み立て、route別にGatewayの公開直呼び
IF(call_jev_first/call_jev_fallback)で1ペア1リクエスト。直列実行
(g1流儀・レート制限余裕 — 引用#13で520×2は制約にならない規模)。
JevOutputInvalidError・LLMErrorはそのペアの失敗として記録し継続する
(再試行しない — 07 §4・design §2.11-7)。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from latch.llm.errors import JevOutputInvalidError, LLMError
from latch.llm.jev import JevJudgment, build_jev_text

ROUTES = ("first", "fallback")


@dataclass(frozen=True)
class PairOutcome:
    pair_id: str
    route: str
    judgment: JevJudgment | None
    error: str | None

    @property
    def mutual_score(self) -> float | None:
        if self.judgment is None:
            return None
        result = self.judgment.result
        return min(result["would_a_accept_b"], result["would_b_accept_a"])


def _classify(exc: Exception) -> str:
    if isinstance(exc, JevOutputInvalidError):
        return "JevOutputInvalidError"
    return type(exc).__name__


async def run_routes(
    gateway,
    goldset,
    *,
    route: str = "both",
    limit: int | None = None,
    echo: Callable[[str], None] | None = None,
) -> list[PairOutcome]:
    """route="both"はfirst→fallbackの順に全ペアを実行(--routeで限定可)。"""
    if route == "both":
        routes = ROUTES
    elif route in ROUTES:
        routes = (route,)
    else:
        raise ValueError(f"unknown route: {route!r} ({'/'.join(ROUTES)}/both)")
    pairs = goldset.pairs[:limit] if limit is not None else goldset.pairs
    outcomes: list[PairOutcome] = []
    for rt in routes:
        call = (
            gateway.call_jev_first if rt == "first" else gateway.call_jev_fallback
        )
        for pair in pairs:
            text_a = build_jev_text(goldset.inputs[pair.intent_a], label="Intent A")
            text_b = build_jev_text(goldset.inputs[pair.intent_b], label="Intent B")
            judgment: JevJudgment | None = None
            error: str | None = None
            try:
                judgment = await call(
                    intent_a=text_a,
                    intent_b=text_b,
                    intent_ids=[pair.intent_a, pair.intent_b],
                )
            except (JevOutputInvalidError, LLMError) as exc:
                error = _classify(exc)
            outcomes.append(
                PairOutcome(pair_id=pair.id, route=rt, judgment=judgment, error=error)
            )
            if echo is not None:
                echo(f"{rt} {pair.id}: {error or 'ok'}")
    return outcomes


def usage_totals(outcomes: list[PairOutcome]) -> dict[str, dict[str, int]]:
    """route別のusage合計(goldset-plan §8「実測時はusage(input_tokens)を記録」)。"""
    totals: dict[str, dict[str, int]] = {}
    for outcome in outcomes:
        if outcome.judgment is None or not outcome.judgment.usage:
            continue
        bucket = totals.setdefault(
            outcome.route, {"input_tokens": 0, "output_tokens": 0}
        )
        for key in ("input_tokens", "output_tokens"):
            bucket[key] += int(outcome.judgment.usage.get(key, 0))
    return totals
```

`report.py`(新規・全文):

```python
"""G2評価の証拠レポート書き出し(design §2.8・10 §5の証拠形式はg1と同一)。

docs/testassets/results/g2-jev-result-YYYYMMDD-HHMMSS.yaml(JST実行時刻)。
--limit部分実行時はレポート先頭に「部分実行=証拠外」を明記する。
合否判定は組み込まない(基準はG2実施前にオーナー確定 — design §1.4-1)。
参考値としてgoldset-plan §11手順2の「Precision 0.60以上@閾値0.80」を表示する。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import yaml

from latch.core.clock import JST
from latch.g2gate.runner import PairOutcome

REFERENCE_THRESHOLD = 0.80  # goldset-plan §11手順2の参考値表示(判定はしない)
REFERENCE_PRECISION = 0.60

_PARTIAL_NOTE = (
    "部分実行(--limit)=証拠外。G2判定には520全件実行のレポートを使うこと"
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def questions_sha256() -> str:
    """JEV_QUESTIONS定数のSHA(design §2.8のmeta証跡・dict順安定化つき)。"""
    import json

    from latch.llm.jev import JEV_QUESTIONS

    return sha256_text(
        json.dumps(JEV_QUESTIONS, ensure_ascii=False, sort_keys=True)
    )


def build_report(
    *,
    executed_at: datetime,
    route: str,
    goldset_file: str,
    goldset_sha256: str,
    questions_sha: str,
    metrics_by_route: dict[str, dict],
    outcomes: list[PairOutcome],
    usage_totals: dict[str, dict],
    limit: int | None,
) -> dict:
    partial = limit is not None
    report: dict = {}
    if partial:
        report["note"] = _PARTIAL_NOTE
    report["meta"] = {
        "trial_id": f"g2-{executed_at.astimezone(JST):%Y%m%d-%H%M%S}",
        "executed_at": executed_at.isoformat(),
        "route": route,
        "partial": partial,
        "limit": limit,
        "pair_count": len(outcomes),
        "goldset": {"file": goldset_file, "sha256": goldset_sha256},
        "jev_questions_sha256": questions_sha,
        "usage_totals": usage_totals,
        "reference": {
            "note": "参考値(合否判定はG2実施前にオーナーが確定 — 09 §4)",
            "precision_at": REFERENCE_THRESHOLD,
            "precision_min": REFERENCE_PRECISION,
        },
    }
    report["routes"] = metrics_by_route
    report["per_pair"] = [
        {
            "id": o.pair_id,
            "route": o.route,
            "error": o.error,
            "would_a_accept_b": (
                None if o.judgment is None else o.judgment.result["would_a_accept_b"]
            ),
            "would_b_accept_a": (
                None if o.judgment is None else o.judgment.result["would_b_accept_a"]
            ),
            "mutual_score": o.mutual_score,
        }
        for o in outcomes
    ]
    return report


def write_report(
    report: dict, *, out_dir: Path, executed_at: datetime
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = executed_at.astimezone(JST).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"g2-jev-result-{stamp}.yaml"
    path.write_text(
        yaml.safe_dump(report, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path
```

`__main__.py`(新規・全文):

```python
"""python -m latch.g2gate(design §2.8)。G2日本語評価harnessのCLI。

実API経路はこのCLIのみ(make g2-gate)。pytest(make test/test-ci)には実API
呼び出しが構造的に存在しない(g1gateと同一規律)。起動時にllm_mode=realと
3鍵を検証し、不足なら即座に拒否する(誤実行防止)。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from latch.core.clock import FakeClock, SystemClock
from latch.g2gate.cases import G2_BASE_CURRENT_DATETIME, load_goldset
from latch.g2gate.compare import compute_metrics
from latch.g2gate.report import build_report, questions_sha256, write_report
from latch.g2gate.runner import run_routes, usage_totals
from latch.llm.gateway import build_worker_gateway
from latch.settings import Settings

DEFAULT_ASSETS = Path("../docs/testassets")  # make g2-gateはbackend/をcwdとして起動


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latch.g2gate",
        description="G2日本語評価harness(TypeSafe Jev+フォールバック両経路・09 §4)",
    )
    parser.add_argument(
        "--assets", type=Path, default=DEFAULT_ASSETS,
        help="入力セットディレクトリ(既定 %(default)s)",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="レポート出力ディレクトリ(既定 <assets>/results)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="各route先頭Nペアのみ実行(部分実行・動作確認用。証拠外)",
    )
    parser.add_argument(
        "--route", choices=("first", "fallback", "both"), default="both",
        help="評価経路(既定 %(default)s)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    settings = Settings()
    if settings.llm_mode != "real":
        print(
            "ERROR: LATCH_LLM_MODE=real が必要です"
            "(make g2-gate は .env を --env-file で読みます)",
            file=sys.stderr,
        )
        return 2
    if not settings.llm_typesafe_api_key:
        print(
            "ERROR: LATCH_TYPESAFE_API_KEY が未設定です(.env へ設定してください)",
            file=sys.stderr,
        )
        return 2
    if not settings.llm_anthropic_api_key:
        print(
            "ERROR: LATCH_ANTHROPIC_API_KEY が未設定です"
            "(.env・フォールバック経路用)",
            file=sys.stderr,
        )
        return 2
    if not settings.llm_gemini_api_key:
        print(
            "ERROR: LATCH_GEMINI_API_KEY が未設定です"
            "(build_worker_gatewayのreal構成は3鍵必須)",
            file=sys.stderr,
        )
        return 2
    goldset = load_goldset(args.assets / "g2-jev-goldset.yaml")
    # 送信記録occurred_atの基準=goldset基準日時(FakeClock)。実施日時(証拠)は
    # SystemClock(arch test規律 — g1gate runnerと同一分担)
    gateway = build_worker_gateway(FakeClock(G2_BASE_CURRENT_DATETIME), settings)
    executed_at = SystemClock().now()
    outcomes = asyncio.run(
        run_routes(
            gateway, goldset, route=args.route, limit=args.limit,
            echo=lambda message: print(message, file=sys.stderr),
        )
    )
    routes = ("first", "fallback") if args.route == "both" else (args.route,)
    metrics_by_route = {
        rt: compute_metrics([o for o in outcomes if o.route == rt], goldset)
        for rt in routes
    }
    report = build_report(
        executed_at=executed_at,
        route=args.route,
        goldset_file="g2-jev-goldset.yaml",
        goldset_sha256=goldset.yaml_sha256,
        questions_sha=questions_sha256(),
        metrics_by_route=metrics_by_route,
        outcomes=outcomes,
        usage_totals=usage_totals(outcomes),
        limit=args.limit,
    )
    out_dir = args.out if args.out is not None else args.assets / "results"
    path = write_report(report, out_dir=out_dir, executed_at=executed_at)
    print(f"report: {path}")
    print(f"pairs: {len(outcomes)} partial={report['meta']['partial']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`__init__.py`(公開IF・g1gate流儀):

```python
"""G2日本語評価harness(M2 ws-8)。CLI(make g2-gate)専用パッケージ。

pytest実行経路に実APIは存在しない(design §2.8)。公開IFの再exportのみ。
"""

from latch.g2gate.cases import (
    G2_BASE_CURRENT_DATETIME,
    Goldset,
    load_goldset,
)
from latch.g2gate.compare import THRESHOLDS, compute_metrics
from latch.g2gate.report import build_report, write_report
from latch.g2gate.runner import PairOutcome, run_routes, usage_totals

__all__ = [
    "G2_BASE_CURRENT_DATETIME",
    "Goldset",
    "PairOutcome",
    "THRESHOLDS",
    "build_report",
    "compute_metrics",
    "load_goldset",
    "run_routes",
    "usage_totals",
    "write_report",
]
```

`Makefile` — g2-gateターゲット追加(.PHONYへも追記):

```makefile
g2-gate: ## G2日本語評価harness(実API・課金。.envにLATCH_LLM_MODE=real+3鍵必須。--limit/--routeは -- で渡す)
	cd backend && uv run --env-file ../.env python -m latch.g2gate
```

- [ ] **Step 4: 実行してパスを確認**

Run: `cd backend && uv run pytest tests/unit/g2gate/test_cases_compare.py -v`
Expected: PASS(全g2gate分。実API呼び出しゼロを確認 — 実行時間が数秒以内であること)

- [ ] **Step 5: lint・コミット**

```bash
make lint && make test
git add backend/src/latch/g2gate/ backend/tests/unit/g2gate/test_cases_compare.py Makefile
git commit -m "feat: g2gate runner/report/CLI+make g2-gate(両経路評価の1コマンド実行・部分実行partial明記)"
```

### Task 10: worker_envのconftest移設+factory化(JevWorker注入オプション)

**Files:**
- Modify: `backend/tests/integration/conftest.py`(make_worker_env・worker_env・worker_env_factory)
- Modify: `backend/tests/integration/test_events_pipeline.py`(fixture定義を削除のみ)

**Interfaces:**
- Produces: `make_worker_env(db_engine, *, jev: JevWorker | None = None)`(async contextmanager・`_WorkerEnv(bus, clock, worker, task)` をyield)・fixture `worker_env`(jev=None・既存どおり)・fixture `worker_env_factory`(make_worker_envをyield)。**既存8試験は引数名だけなので無変更**(design §3.2「worker_envへJevWorker注入オプション(§2.4試験8用の最小拡張・既存8試験は無変更)」の実体 — §9-11)

- [ ] **Step 1: conftest.py へ移設・追加**(`test_events_pipeline.py` 162〜192行のworker_env定義と `_WorkerEnv`・`_instant` をconftestへ。**実装先行** — このタスクはリファクタリングであり新規試験はTask 11/12が消費する)

conftest.py 末尾へ追加(import節へ `contextlib`・`uuid`・`NamedTuple`・`FakeClock`・`SystemClock`・`PubsubEventBus`・`Worker`・`Settings`・必要に応じて既存importへ追記):

```python
# -- worker_env(ws-8: test_events_pipelineから移設+JevWorker注入オプション) --

async def _instant(_seconds: float) -> None:
    return None  # 実時間sleepさせない(移設元test_events_pipeline._instantと同一)


class _WorkerEnv(NamedTuple):
    bus: PubsubEventBus
    clock: FakeClock
    worker: Worker
    task: asyncio.Task


@contextlib.asynccontextmanager
async def make_worker_env(db_engine, *, jev=None):
    """テストプロセス内Worker環境。jevへJevWorkerを渡すとWorker DIで注入
    (縮退E2E試験8 — design §2.4)。未注入はrun()内で再構築(既存挙動)。
    """
    sub_name = f"match-events-test-{uuid.uuid4().hex[:8]}"
    settings = Settings()
    bus = PubsubEventBus(settings, subscription=sub_name)
    for _ in range(40):  # エミュレータ起動待ち(最大20秒)
        try:
            await bus.ensure()
            break
        except Exception:
            await asyncio.sleep(0.5)
    else:
        pytest.fail("pubsub emulator not reachable at 127.0.0.1:8085")
    clock = FakeClock(SystemClock().now())
    worker = Worker(
        clock=clock, settings=settings, bus=bus, engine=db_engine,
        sleep=_instant, jev=jev,
    )
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.1)  # subscribe開始を待つ
    try:
        yield _WorkerEnv(bus=bus, clock=clock, worker=worker, task=task)
    finally:
        worker.request_shutdown()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except TimeoutError:
            task.cancel()
        # 残余メッセージを次試験へ残さない。closeの前に削除する
        bus.delete_subscription()
        await bus.close()


@pytest.fixture
async def worker_env(db_engine):
    async with make_worker_env(db_engine) as env:
        yield env


@pytest.fixture
def worker_env_factory():
    """jev注入付きWorker環境のfactory(縮退E2E試験8)。teardownはctx抜けで走る。"""
    return make_worker_env
```

(注記: `Worker.__init__` のDI引数名は `jev`(worker/main.py既存・ws-5資産)。移設によりtest_events_pipeline.pyのimportから未使用になる名前はlintで掃除する)

`test_events_pipeline.py` — 該当fixture定義・`_WorkerEnv`・`_instant` を削除(lintの未使用import掃除を含む。**試験本体の8関数は触らない**)。

- [ ] **Step 2: 既存試験が無傷であることをunit収集で確認**

Run: `cd backend && uv run pytest --collect-only tests/integration/test_events_pipeline.py -q`
Expected: 8件収集(既存どおり)。`make test` もグリーン(import妥当性)

- [ ] **Step 3: lint・コミット**

```bash
make lint && make test
git add backend/tests/integration/conftest.py backend/tests/integration/test_events_pipeline.py
git commit -m "refactor: worker_envをconftestへ移設+JevWorker注入factory(縮退E2E試験8の土台)"
```

### Task 11: 縮退E2E 8試験(test_degraded_e2e.py・作成のみ・実行はスーパーバイザー)

**Files:**
- Create: `backend/tests/integration/test_degraded_e2e.py`

**Interfaces:**
- Consumes: Task 1〜4のbreaker・Task 2のfail_jev_exc・Task 10のworker_env_factory・`JevWorker`・`LatchEngine`・`run_candidate_retrieval`・`JevCostGuard/JevCostStore`
- design §2.4試験1〜8。対抗策4項目(ws-5流儀): 時間窓now+5日統一・subject prefix teardown(FK順・`m2ws8-`)・Redis prefix掃除・FakeClockでbreaker 60秒・debounce 10秒を進行

- [ ] **Step 1: 試験ファイルを作成する**(全文。**実行しない — 収集確認のみ**)

```python
"""縮退運転のE2E試験(M2 ws-8 design §2.4・10 §4.5)。

実DB(compose常設)・実Redis。Workerプロセスは立てない(試験1〜7はJevWorker
直接構築+注入Gateway・FakeClock。試験8のみテストプロセス内Worker+API実HTTP)。
対抗策(ws-5流儀): (1)時間窓をnow+5日(BASE_HOURS=120)へ統一(2)subject prefix
単位のFK順teardown(match_candidates→group_candidates/latches→match_events→
intents→users)(3)Redisは試験ごとのkey_prefixでSCAN+DELETE(4)FakeClockで
breaker 60秒・debounce窓を進行(実時間待ちなし)。
ci環境=スタブLLMで決定的(10 §1)。
"""

import asyncio
import copy
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.llm.breaker import CircuitBreaker
from latch.llm.errors import (
    LLMConnectionError,
    LLMOverloadedError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from latch.llm.gateway import LLMGateway
from latch.llm.stub import DEFAULT_JEV_RESPONSE, StubLLM
from latch.worker.cost import JevCostGuard, JevCostStore
from latch.worker.jev import JevWorker
from latch.worker.matching import run_candidate_retrieval
from latch.worker.matching.latch_engine import LatchEngine
from latch.geo.service import GeoService

pytestmark = pytest.mark.integration

CATEGORY = "meal"
BASE_HOURS = 120
SUBJECT_PREFIX = "m2ws8-"


def _vec(*components: float) -> str:
    vals = [0.0] * 768
    for i, c in enumerate(components):
        vals[i] = c
    return "[" + ",".join(repr(v) for v in vals) + "]"


E1 = _vec(1.0)


def _high_prob_envelope() -> dict:
    """MutualScore=0.95≥0.80でlatches提案が成立するstub応答(試験7・8用)。"""
    env = copy.deepcopy(DEFAULT_JEV_RESPONSE)
    for key in ("would_a_accept_b", "would_b_accept_a"):
        env["answers"][key] = {"type": "noul", "noul": 0.95}
    return env


async def _cli_idp_token(provider: str, subject: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "latch.auth", "issue-idp-token",
        "--provider", provider, "--subject", subject,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    return stdout.decode().strip()


@pytest.fixture
async def field(db_engine):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除。"""
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM match_candidates WHERE intent_a_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
                " OR intent_b_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ), p,
        )
        await conn.execute(
            text(
                "DELETE FROM latches WHERE intent_ids && (SELECT array_agg(id)"
                " FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))::uuid[]"
            ), p,
        )
        await conn.execute(
            text(
                "DELETE FROM group_candidates WHERE intent_ids && (SELECT"
                " array_agg(id) FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))::uuid[]"
            ), p,
        )
        await conn.execute(
            text(
                "DELETE FROM match_events WHERE source_intent_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ), p,
        )
        await conn.execute(
            text(
                "DELETE FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ), p,
        )
        await conn.execute(text("DELETE FROM users WHERE auth_subject LIKE :p"), p)


@pytest.fixture
async def redis_sweep(redis_client):
    prefix = f"ws8-{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    cursor = 0
    keys: list[str] = []
    while True:
        cursor, batch = await redis_client.scan(
            cursor=cursor, match=f"{prefix}*", count=100
        )
        keys.extend(batch)
        if cursor == 0:
            break
    if keys:
        await redis_client.delete(*keys)


async def _user(api_client, prefix: str, birth_date: str = "1990-04-01"):
    subject = f"{prefix}{uuid_mod.uuid4().hex[:8]}"
    idp = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users", headers=headers,
        json={"display_name": "m2ws8", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    now = SystemClock().now()
    return (now + timedelta(hours=hours)).isoformat()


def _structured(*, start: str | None = None) -> dict:
    return {
        "category": {"primary": CATEGORY, "secondary": None},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }


def _payload(structured: dict) -> dict:
    return {
        "raw_text": "分類用テキスト", "status": "active",
        "structured_intent": structured,
    }


async def _intent(api_client, db_engine, headers, structured) -> dict:
    resp = await api_client.post(
        "/v1/intents", headers=headers, json=_payload(structured)
    )
    assert resp.status_code == 201, resp.text
    intent = resp.json()["intent"]
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intents SET embedding = CAST(:vec AS vector),"
                " embedding_model = 'fixture' WHERE id = CAST(:iid AS uuid)"
            ),
            {"vec": E1, "iid": intent["id"]},
        )
    return intent


class _CountingStub(StubLLM):
    """judge呼び出しを数えるスタブ(第一候補不呼出の検査用)。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.judge_calls = 0

    async def judge(self, intent_a, intent_b):
        self.judge_calls += 1
        return await super().judge(intent_a, intent_b)


class _FlakyFirst(_CountingStub):
    """最初のN回のjudgeだけ例外を投げ、以後正常へ回復する第一候補スタブ。"""

    def __init__(self, *, fail_calls: int, exc: Exception, **kw):
        super().__init__(**kw)
        self._fail_calls = fail_calls
        self._exc = exc

    async def judge(self, intent_a, intent_b):
        self.judge_calls += 1
        if self.judge_calls <= self._fail_calls:
            raise self._exc
        return await StubLLM.judge(self, intent_a, intent_b)


class _TimeoutPatternStub(_CountingStub):
    """指定呼び出し番号(1-indexed)だけLLMTimeoutError(p95再現・試験6)。"""

    def __init__(self, *, timeout_at: set[int], **kw):
        super().__init__(**kw)
        self._timeout_at = timeout_at

    async def judge(self, intent_a, intent_b):
        self.judge_calls += 1
        if self.judge_calls in self._timeout_at:
            raise LLMTimeoutError("stub: p95 pattern timeout")
        return await StubLLM.judge(self, intent_a, intent_b)


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


def _breaker_gateway(clock, jev, fb, *, breaker=None) -> LLMGateway:
    return LLMGateway(
        clock=clock, parser=StubLLM(), embedding=StubLLM(),
        jev=jev, jev_fallback=fb,
        breaker=breaker if breaker is not None else CircuitBreaker(clock=clock),
    )


def _stores(redis_client, redis_sweep: str, clock):
    store = JevCostStore(redis_client, key_prefix=redis_sweep)
    return JevCostGuard(store=store, clock=clock), store


async def _pair_of(db_engine, a: str, b: str):
    lo, hi = sorted([a, b], key=str)
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT status, skip_reason, jev_result FROM match_candidates"
                    " WHERE intent_a_id = CAST(:a AS uuid)"
                    " AND intent_b_id = CAST(:b AS uuid)"
                    " ORDER BY created_at DESC LIMIT 1"
                ),
                {"a": lo, "b": hi},
            )
        ).first()


async def _latches_of(db_engine, intent_id: str) -> list:
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT id, status FROM latches"
                    " WHERE CAST(:i AS uuid) = ANY(intent_ids)"
                ),
                {"i": intent_id},
            )
        ).all()


# -- design §2.4 試験1〜8 --


async def test_1_rate_limit_switches_to_fallback(
    api_client, db_engine, field, redis_client, redis_sweep, caplog
):
    """切替(引用#6-1): 第一候補429→フォールバック評価継続・provider記録・送信2件。"""
    import json
    import logging

    from latch.llm.records import LOGGER_NAME

    clock = _clock()
    start = _future(BASE_HOURS)
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=start))
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, _structured(start=start))
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    first = StubLLM(fail_jev_exc="ratelimit")
    fb = _CountingStub()
    gateway = _breaker_gateway(clock, first, fb)
    guard, store = _stores(redis_client, redis_sweep, clock)
    worker = JevWorker(
        engine=db_engine, clock=clock, gateway=gateway,
        guard=guard, cost_store=store,
    )
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await worker.handle(uuid_mod.UUID(a["id"]))
    row = await _pair_of(db_engine, a["id"], b["id"])
    assert row is not None and row[0] == "evaluated"
    assert row[2]["provider"] == "fallback_llm"  # jev_resultのproviderキー
    assert fb.judge_calls == 1
    payloads = [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == LOGGER_NAME
    ]
    statuses = [p["status"] for p in payloads if p.get("system") == "jev"]
    assert "error" in statuses and "ok" in statuses  # 第一候補失敗+fb成功の2件


async def test_2_dual_failure_skips_and_zero_proposals(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """双障害(引用#6-2・3): skipped(llm_failure)・jev_result NULL・提案ゼロ。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=start))
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, _structured(start=start))
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    gateway = _breaker_gateway(
        clock,
        StubLLM(fail_jev_exc="ratelimit"),
        StubLLM(fail_jev_exc="timeout"),
    )
    guard, store = _stores(redis_client, redis_sweep, clock)
    worker = JevWorker(
        engine=db_engine, clock=clock, gateway=gateway,
        guard=guard, cost_store=store,
    )
    await worker.handle(uuid_mod.UUID(a["id"]))
    row = await _pair_of(db_engine, a["id"], b["id"])
    assert row[0] == "skipped"
    assert row[1] == "llm_failure"
    assert row[2] is None  # jev_resultはNULLのまま
    assert await _latches_of(db_engine, a["id"]) == []  # 提案ゼロ


async def test_3_breaker_opens_on_error_rate(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """breaker開放・エラー率(引用#6-4): 429を窓内2回→開放・以後第一候補不呼出。

    BreakerParamsは既定値のまま(60秒窓・min_samples=2)。FakeClockで時刻を
    進めない=同一窓内に収める(design §2.4「実値60秒窓のままで呼び出し2回の
    失敗だけで開放する性質を利用」)。実DB要素は開放中でも評価が継続する
    こと(jev_result.provider=fallback_llm)。
    """
    clock = _clock()
    start = _future(BASE_HOURS)
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=start))
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    first = StubLLM(fail_jev_exc="ratelimit")
    fb = _CountingStub()
    breaker = CircuitBreaker(clock=clock)
    gateway = _breaker_gateway(clock, first, fb, breaker=breaker)
    # (a) judge_pair直接2回で開放(breaker状態の確認)
    for _ in range(2):
        judgment = await gateway.judge_pair(
            intent_a="A", intent_b="B", intent_ids=["x", "y"]
        )
        assert judgment.provider == "fallback_llm"
    assert breaker.state == "open"
    calls_before = first.judge_calls
    # (b) 開放後のjudge_pairは第一候補を呼ばない
    judgment = await gateway.judge_pair(
        intent_a="A", intent_b="B", intent_ids=["x", "y"]
    )
    assert judgment.provider == "fallback_llm"
    assert first.judge_calls == calls_before  # 増えていない
    # (c) 開放中でもDB評価はフォールバックで継続
    guard, store = _stores(redis_client, redis_sweep, clock)
    worker = JevWorker(
        engine=db_engine, clock=clock, gateway=gateway,
        guard=guard, cost_store=store,
    )
    await worker.handle(uuid_mod.UUID(a["id"]))
    assert first.judge_calls == calls_before  # handle経由でも第一候補不呼出
    rows = await _candidates(db_engine, a["id"])
    assert rows and rows[0][2] and rows[0][2]["provider"] == "fallback_llm"


async def _candidates(db_engine, origin_id: str) -> list:
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT status, skip_reason, jev_result FROM match_candidates"
                    " WHERE intent_a_id = CAST(:i AS uuid)"
                    " OR intent_b_id = CAST(:i AS uuid)"
                ),
                {"i": origin_id},
            )
        ).all()


async def test_4_half_open_success_closes(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """半開・成功で閉じ(引用#6-4): +60秒→第一候補を1回だけ試験→closed→復帰。"""
    clock = _clock()
    first = _FlakyFirst(fail_calls=2, exc=LLMRateLimitError("429"))
    fb = _CountingStub()
    breaker = CircuitBreaker(clock=clock)
    gateway = _breaker_gateway(clock, first, fb, breaker=breaker)
    for _ in range(2):
        await gateway.judge_pair(intent_a="A", intent_b="B", intent_ids=["x", "y"])
    assert breaker.state == "open"
    clock.advance(timedelta(seconds=60))  # 開放→半開
    judgment = await gateway.judge_pair(
        intent_a="A", intent_b="B", intent_ids=["x", "y"]
    )
    assert judgment.provider == "typesafe_jev"  # 試験リクエストは第一候補
    assert first.judge_calls == 3
    assert breaker.state == "closed"
    judgment2 = await gateway.judge_pair(
        intent_a="A", intent_b="B", intent_ids=["x", "y"]
    )
    assert judgment2.provider == "typesafe_jev"  # closed後は第一候補へ復帰
    assert first.judge_calls == 4


async def test_5_half_open_failure_reopens_and_round_trips(
    api_client, db_engine, field
):
    """半開・失敗で開放戻し(引用#1「往復は何度でも」): 2往復を実証。"""
    clock = _clock()
    first = StubLLM(fail_jev_exc="overloaded")  # 常時529失敗
    fb = _CountingStub()
    breaker = CircuitBreaker(clock=clock)
    gateway = _breaker_gateway(clock, first, fb, breaker=breaker)
    for _ in range(2):
        await gateway.judge_pair(intent_a="A", intent_b="B", intent_ids=["x", "y"])
    assert breaker.state == "open"
    for _round in range(2):  # 往復2回
        clock.advance(timedelta(seconds=60))  # 半開へ
        calls_before = first.judge_calls
        judgment = await gateway.judge_pair(
            intent_a="A", intent_b="B", intent_ids=["x", "y"]
        )
        assert judgment.provider == "fallback_llm"  # 試験は失敗→fb
        assert first.judge_calls == calls_before + 1  # 半開は1回だけ呼ぶ
        assert breaker.state == "open"  # 失敗→open戻し
        j2 = await gateway.judge_pair(
            intent_a="A", intent_b="B", intent_ids=["x", "y"]
        )
        assert j2.provider == "fallback_llm"
        assert first.judge_calls == calls_before + 1  # open中は不呼出


async def test_6_p95_condition_opens_alone(api_client, db_engine, field):
    """p95条件単独開放(承認事項1の裏付け): 20呼び出し中2件timeout+18成功
    →エラー率10%≤50%・p95位置=timeout→開放。timeout呼び出しのレイテンシは
    打ち切り時点のtimeout_s(6秒)として母集団に入る(design §2.4試験6)。"""
    clock = _clock()
    first = _TimeoutPatternStub(timeout_at={18, 19})
    fb = _CountingStub()
    breaker = CircuitBreaker(clock=clock)
    gateway = _breaker_gateway(clock, first, fb, breaker=breaker)
    for _ in range(18):
        judgment = await gateway.judge_pair(
            intent_a="A", intent_b="B", intent_ids=["x", "y"]
        )
        assert judgment.provider == "typesafe_jev"
    assert breaker.state == "closed"  # 18成功(エラー率0%)
    for _ in range(2):  # 19・20呼び出し目がtimeout
        await gateway.judge_pair(intent_a="A", intent_b="B", intent_ids=["x", "y"])
    # エラー率2/20=10%≤50% だがp95位置(昇順18番目)=6.0≥6.0で開放
    assert breaker.state == "open"


async def test_7_recovery_reevaluates_skipped(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """復旧後再評価(FR-18・引用#6): 双障害skipped→フォールバック回復→
    同一起点の再handle(「障害回復後のMatch Event」相当)でRESELECT_ALWAYSが
    skipped行を再選択→evaluated・提案発生(高確率応答でL≥0.80)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=start))
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, _structured(start=start))
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    # 第一候補=常時429・フォールバック=最初の1回だけtimeout→以後回復(高確率応答)
    fb = _FlakyFirst(
        fail_calls=1, exc=LLMTimeoutError("fb timeout"),
        jev_response=_high_prob_envelope(),
    )
    gateway = _breaker_gateway(clock, StubLLM(fail_jev_exc="ratelimit"), fb)
    guard, store = _stores(redis_client, redis_sweep, clock)
    worker = JevWorker(
        engine=db_engine, clock=clock, gateway=gateway,
        guard=guard, cost_store=store,
    )
    latch = LatchEngine(
        engine=db_engine, clock=clock, geo=GeoService(db_engine)
    )
    await worker.handle(uuid_mod.UUID(a["id"]))  # 双障害→skipped
    row = await _pair_of(db_engine, a["id"], b["id"])
    assert row[0] == "skipped" and row[1] == "llm_failure"
    await latch.handle(uuid_mod.UUID(a["id"]))
    assert await _latches_of(db_engine, a["id"]) == []
    # 回復後の再評価(同一起点のhandle再実行)
    await worker.handle(uuid_mod.UUID(a["id"]))
    row2 = await _pair_of(db_engine, a["id"], b["id"])
    assert row2[0] == "evaluated"
    assert row2[2]["provider"] == "fallback_llm"
    await latch.handle(uuid_mod.UUID(a["id"]))
    latches = await _latches_of(db_engine, a["id"])
    assert len(latches) == 1  # 提案発生(L=0.95≥0.80)


async def test_8_worker_e2e_with_faulty_first(
    api_client, db_engine, worker_env_factory, field, redis_client, redis_sweep
):
    """Worker一気通貫(design §2.4試験8): API→Event→Worker(stage1→embedding→
    L1〜3→Group→Jev→Latch)で第一候補429を注入してもフォールバック経由で
    latches提案まで到達する(Worker DI jev=注入・既存IF)。teardownはfield。"""
    clock = FakeClock(SystemClock().now())
    gateway = _breaker_gateway(
        clock,
        StubLLM(fail_jev_exc="ratelimit"),
        StubLLM(jev_response=_high_prob_envelope()),
    )
    store = JevCostStore(redis_client, key_prefix=redis_sweep)
    jev_worker = JevWorker(
        engine=db_engine, clock=clock, gateway=gateway,
        guard=JevCostGuard(store=store, clock=clock), cost_store=store,
    )
    async with worker_env_factory(db_engine, jev=jev_worker) as env:
        ha = await _user(api_client, field)
        a = await _intent(api_client, db_engine, ha, _structured())
        hb = await _user(api_client, field)
        await _intent(api_client, db_engine, hb, _structured())
        await _wait_processed(db_engine, a["id"])
        await _wait_latch(db_engine, a["id"])
        async with db_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT jev_result->>'provider' FROM match_candidates"
                        " WHERE intent_a_id = CAST(:i AS uuid)"
                        " OR intent_b_id = CAST(:i AS uuid)"
                    ),
                    {"i": a["id"]},
                )
            ).first()
        assert row is not None and row[0] == "fallback_llm"
        assert len(await _latches_of(db_engine, a["id"])) == 1  # 提案まで到達


async def _wait_processed(db_engine, intent_id: str, timeout=10.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with db_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT status FROM match_events"
                        " WHERE source_intent_id = CAST(:i AS uuid)"
                        " ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"i": intent_id},
                )
            ).first()
        if row is not None and row[0] == "processed":
            return
        await asyncio.sleep(0.2)
    pytest.fail(f"match_events not processed: {intent_id}")


async def _wait_latch(db_engine, intent_id: str, timeout=15.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if await _latches_of(db_engine, intent_id):
            return
        await asyncio.sleep(0.2)
    pytest.fail(f"latch not created for {intent_id}")
```

(実装注記: 試験8の `_latches_of` はファイル末尾ヘルパー。`env` 変数が未使用になるため `_` へ受けるか `async with worker_env_factory(...) as _:` とする(lint対応))

- [ ] **Step 2: 収集確認(実行しない)**

Run: `cd backend && uv run pytest --collect-only tests/integration/test_degraded_e2e.py -q`
Expected: 8件収集。`make test` でimportが検証されグリーン

- [ ] **Step 3: lint・コミット**

```bash
make lint && make test
git add backend/tests/integration/test_degraded_e2e.py
git commit -m "test: 縮退E2E 8試験(切替・双障害skipped・breaker開放/半開往復・p95分離・復旧再評価・Worker一気通貫)"
```

### Task 12: K上限裏付け・冪等性・#7更新・#8 Bucketの4試験(test_k_limits_e2e.py)

**Files:**
- Create: `backend/tests/integration/test_k_limits_e2e.py`

**Interfaces:**
- Consumes: Task 10の `worker_env`・`run_candidate_retrieval`・`GroupEngine`・`JevWorker`・`LatchEngine`・`ReevalRunner`(test_10流儀の直接構成)・`bus.publish_raw`
- 配置の確定値(§9-15・§9-16): 試験1は計118 Intent(起点1+Layer1純通過組101+min=3混入10+除外要因6)→ layer1_pass_count=101>100。試験2は独立簡易配置(起点+15)。除外要因6名の内訳=時間交差なし3名(startを起点と重ならない値へ)+ペア予算min<500の3名(budget_max=300)

- [ ] **Step 1: 試験ファイルを作成する**(全文。**実行しない — 収集確認のみ**。ヘルパー `_vec/_user/_intent/field/redis_sweep/_clock` 等はtest_degraded_e2e.pyと同一内容をこのファイル内にコピーする(basename一意・import共有はしない流儀))

```python
"""K上限裏付け・冪等性・02#7更新・02#8 Bucket再評価のE2E(M2 ws-8 design §2.6・10 §4.6〜§4.7)。

実DB・実Redis。Workerプロセスは立てない(試験1・3・4は部品直接構成・
試験2のみworker_env)。対抗策はtest_degraded_e2eと同一(時間窓now+5日統一・
subject prefix teardown・Redis prefix掃除・FakeClock)。
"""

import asyncio
import copy
import json
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.geo.service import GeoService
from latch.llm.gateway import LLMGateway
from latch.llm.stub import DEFAULT_JEV_RESPONSE, StubLLM
from latch.worker.cost import JevCostGuard, JevCostStore
from latch.worker.jev import JevWorker
from latch.worker.matching import run_candidate_retrieval
from latch.worker.matching.group_engine import GroupEngine
from latch.worker.matching.latch_engine import LatchEngine
from latch.worker.reeval import ReevalRunner
from latch.worker.cost import ReevalGuard

pytestmark = pytest.mark.integration

CATEGORY = "meal"
BASE_HOURS = 120
SUBJECT_PREFIX = "m2ws8k-"


def _vec(*components: float) -> str:
    vals = [0.0] * 768
    for i, c in enumerate(components):
        vals[i] = c
    return "[" + ",".join(repr(v) for v in vals) + "]"


E1 = _vec(1.0)  # 全員同一ベクトル(同点→intent_id昇順の決定性)


def _high_prob_envelope() -> dict:
    env = copy.deepcopy(DEFAULT_JEV_RESPONSE)
    for key in ("would_a_accept_b", "would_b_accept_a"):
        env["answers"][key] = {"type": "noul", "noul": 0.95}
    return env


# (ヘルパー _cli_idp_token・field・redis_sweep・_user・_future・_structured・
#  _payload・_intent・_clock はtest_degraded_e2e.pyと同一実装をコピー)
# fieldのteardown SQLはtest_degraded_e2eと同一(latches・group_candidatesの
# &&掃除込み)。redis_sweepのprefixは "ws8k-" を使用


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


def _breaker_free_gateway(clock) -> LLMGateway:
    """K上限試験はbreaker無しのstub gateway(breakerは別資産が検証済み)。"""
    return LLMGateway(
        clock=clock, parser=StubLLM(), embedding=StubLLM(),
        jev=StubLLM(jev_response=_high_prob_envelope()), jev_fallback=StubLLM(),
    )


class _RecordingGateway(LLMGateway):
    """judge_pairの呼び出し順を記録(決定性検証・§2.6試験1の⑧)。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.judge_order: list[tuple[str, str]] = []

    async def judge_pair(self, *, intent_a, intent_b, intent_ids):
        self.judge_order.append(tuple(sorted(intent_ids)))
        return await super().judge_pair(
            intent_a=intent_a, intent_b=intent_b, intent_ids=intent_ids
        )


def _stores(redis_client, redis_sweep: str, clock):
    store = JevCostStore(redis_client, key_prefix=redis_sweep)
    return JevCostGuard(store=store, clock=clock), store


def _peer_ids(outcome) -> list[str]:
    """Outcomeのpairsから起点以外の側のidを行順に取り出す
    (test_matching_retrieval._peer_idsと同一流儀)。"""
    out = []
    for p in outcome.pairs:
        a, b = str(p.intent_a_id), str(p.intent_b_id)
        out.append(b if str(outcome.intent_id) == a else a)
    return out


async def _full_chain(db_engine, clock, gateway, guard, store, origin_id):
    """L1〜3→Group→Jev→Latch→Group.finalizeの共通チェーン
    (worker_envの_run_post_retrieval相当を試験内で直列呼び出し)。"""
    from latch.worker.matching import run_candidate_retrieval as _run

    async with db_engine.begin() as conn:
        outcome = await _run(conn, clock, uuid_mod.UUID(origin_id))
    latch = LatchEngine(
        engine=db_engine, clock=clock, geo=GeoService(db_engine)
    )
    group = GroupEngine(
        engine=db_engine, clock=clock, geo=GeoService(db_engine), latch=latch
    )
    group_ctx = await group.handle(uuid_mod.UUID(origin_id))
    jev = JevWorker(
        engine=db_engine, clock=clock, gateway=gateway,
        guard=guard, cost_store=store,
    )
    await jev.handle(uuid_mod.UUID(origin_id), group_ctx)
    await latch.handle(uuid_mod.UUID(origin_id))
    await group.finalize(uuid_mod.UUID(origin_id))
    return outcome


async def test_1_k_limits_all_layers(
    api_client, db_engine, field, redis_client, redis_sweep, caplog
):
    """K上限一気通貫(10 §4.6・引用#7・#11・#5観察・G2条件②)。

    配置(§9-15): 起点1(min=2/max=4 — Layer1通過とGroupEngine種トリガー
    〔起点max>=3・ws-7 load_group_origin〕の両立)+Layer1純通過組101
    (min=2/max=2)+min=3混入10(max=4・Pool用・Layer1の1対1では不通過)
    +除外要因6(時間交差なし3+ペア予算300円3)=計118 Intent。全員同一時間帯・
    同一ベクトル(同点→intent_id昇順)。検証: ①layer1_pass_count>100
    ②pairs≤50=同点intent_id昇順上位50 ③topkc≤20 ④Jev≤8・1対1最低4回
    ⑤`group pool built pool_size=`≤15 ⑥latchesがD-08上限内(起点≤3)
    ⑦除外ペア非生成 ⑧決定性(同一入力2回実行で同一結果)。
    """
    import logging
    import time

    import latch.worker.matching.group_engine as ge_mod

    clock = _clock()
    start = _future(BASE_HOURS)
    h0 = await _user(api_client, field)
    origin_struct = _structured(start=start)
    origin_struct["participants"] = {"min": 2, "max": 4}
    origin_struct["budget"] = {"max": 5000}
    origin = await _intent(api_client, db_engine, h0, origin_struct)
    pure_ids: list[str] = []  # Layer1純通過組101(min=2/max=2・budget5000)
    for _ in range(101):
        h = await _user(api_client, field)
        t = await _intent(api_client, db_engine, h, _structured(start=start))
        pure_ids.append(t["id"])
    group_ids: list[str] = []  # min=3/max=4混入10(Pool用・Layer1では不通過)
    for _ in range(10):
        h = await _user(api_client, field)
        s = _structured(start=start)
        s["participants"] = {"min": 3, "max": 4}
        s["budget"] = {"max": 5000}
        t = await _intent(api_client, db_engine, h, s)
        group_ids.append(t["id"])
    excluded: dict[str, str] = {}  # 除外要因6(3=時間交差なし・3=予算300)
    for _ in range(3):
        h = await _user(api_client, field)
        t = await _intent(
            api_client, db_engine, h, _structured(start=_future(BASE_HOURS + 72))
        )
        excluded[t["id"]] = "time"
    for _ in range(3):
        h = await _user(api_client, field)
        s = _structured(start=start)
        s["budget"] = {"max": 300}
        t = await _intent(api_client, db_engine, h, s)
        excluded[t["id"]] = "budget"

    guard, store = _stores(redis_client, redis_sweep, clock)
    gateway = _RecordingGateway(
        clock=clock, parser=StubLLM(), embedding=StubLLM(),
        jev=StubLLM(jev_response=_high_prob_envelope()),
        jev_fallback=StubLLM(),
    )
    with caplog.at_level(logging.INFO, logger=ge_mod.__name__):
        t0 = time.monotonic()
        outcome = await _full_chain(
            db_engine, clock, gateway, guard, store, origin["id"]
        )
        elapsed = time.monotonic() - t0
    print(f"\n[ws8k test1] chain elapsed={elapsed:.3f}s")
    # ①一次候補100件超(10 §4.6)。layer1_pass_countは起点を除く通過数
    # (test_2_kv流儀) = 純通過組101(min3組10・除外6はLayer1不通過)
    assert outcome.layer1_pass_count > 100
    assert outcome.layer1_pass_count == 101
    # ②Vector出力≤50・同点はintent_id昇順上位50(D-24・_peer_ids流儀)
    assert len(outcome.pairs) == 50
    assert _peer_ids(outcome) == sorted(pure_ids)[:50]
    # ③Cheap Judge出力≤20(match_candidates生成)
    assert len(outcome.topkc) <= 20
    # ④Jev実行回数≤8・1対1最低4回保証(06 §5配分)
    async with db_engine.connect() as conn:
        evaluated = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM match_candidates"
                    " WHERE (intent_a_id = CAST(:o AS uuid)"
                    " OR intent_b_id = CAST(:o AS uuid))"
                    " AND status = 'evaluated'"
                ),
                {"o": origin["id"]},
            )
        ).scalar_one()
    assert evaluated <= 8
    assert evaluated >= 4  # 1対1最低4回
    # ⑤Pool≤15(ws-7のログ・D-24)。Pool候補は人数緩和検索(min<=4 AND max>=3)
    # でmin3混入10名+起点は種(純通過組はmax=2でPool外)→pool_size=10
    pool_logs = [
        r.message for r in caplog.records if "group pool built" in r.message
    ]
    assert pool_logs  # 起点max=4>=3でPool構築が走る
    for m in pool_logs:
        size = int(m.split("pool_size=")[1].split()[0])
        assert size <= 15
    # ⑥latchesがD-08上限内(同時3件/Intent — noul0.95で全評価が閾値超)
    origin_latches = await _latches_of(db_engine, origin["id"])
    assert len(origin_latches) <= 3
    # ⑦除外ペア非生成(#9のE2E側)
    for ex_id in excluded:
        assert await _pair_of(db_engine, origin["id"], ex_id) is None
    # ⑧決定性: 掃除→同一入力(同一uuid)で再実行→切り詰め選択・Jev呼び出し順
    # ・行集合が同一(引用#7「同一入力での2回実行が同一結果」)
    snap1 = await _snapshot(db_engine, origin["id"])
    order1 = list(gateway.judge_order)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM notifications WHERE latch_id IN"
                " (SELECT id FROM latches WHERE CAST(:o AS uuid) = ANY(intent_ids))"
            ),
            {"o": origin["id"]},
        )
        await conn.execute(
            text(
                "DELETE FROM latches WHERE CAST(:o AS uuid) = ANY(intent_ids)"
            ),
            {"o": origin["id"]},
        )
        await conn.execute(
            text(
                "DELETE FROM group_candidates WHERE CAST(:o AS uuid) = ANY(intent_ids)"
            ),
            {"o": origin["id"]},
        )
        await conn.execute(
            text(
                "DELETE FROM match_candidates WHERE intent_a_id = CAST(:o AS uuid)"
                " OR intent_b_id = CAST(:o AS uuid)"
            ),
            {"o": origin["id"]},
        )
    # 再チェーン(_full_chainと同一構成・RecordingGatewayで呼び出し順を採取)
    rec_guard, rec_store = _stores(redis_client, redis_sweep, clock)
    rec_gateway = _RecordingGateway(
        clock=clock, parser=StubLLM(), embedding=StubLLM(),
        jev=StubLLM(jev_response=_high_prob_envelope()),
        jev_fallback=StubLLM(),
    )
    outcome2 = await _full_chain(
        db_engine, clock, rec_gateway, rec_guard, rec_store, origin["id"]
    )
    assert _peer_ids(outcome2) == _peer_ids(outcome)  # 切り詰め選択の同一性
    assert rec_gateway.judge_order == order1  # Jev呼び出し順の同一性
    snap2 = await _snapshot(db_engine, origin["id"])
    assert snap1 == snap2  # ペア・status・cheap_judge_score・latches・group


async def _pair_of(db_engine, a: str, b: str):
    lo, hi = sorted([a, b], key=str)
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT id FROM match_candidates"
                    " WHERE intent_a_id = CAST(:a AS uuid)"
                    " AND intent_b_id = CAST(:b AS uuid) LIMIT 1"
                ),
                {"a": lo, "b": hi},
            )
        ).first()


async def _latches_of(db_engine, intent_id: str) -> list:
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT id, status FROM latches"
                    " WHERE CAST(:i AS uuid) = ANY(intent_ids)"
                ),
                {"i": intent_id},
            )
        ).all()


async def _snapshot(db_engine, origin_id: str):
    """決定性検証の行集合(ペア・status・cheap_judge_score+latches+group)。"""
    async with db_engine.connect() as conn:
        mc = (
            await conn.execute(
                text(
                    "SELECT intent_a_id, intent_b_id, status,"
                    " cheap_judge_score::float8 FROM match_candidates"
                    " WHERE intent_a_id = CAST(:o AS uuid)"
                    " OR intent_b_id = CAST(:o AS uuid)"
                    " ORDER BY intent_a_id, intent_b_id"
                ),
                {"o": origin_id},
            )
        ).all()
        latches = (
            await conn.execute(
                text(
                    "SELECT intent_ids, status, score::float8 FROM latches"
                    " WHERE CAST(:o AS uuid) = ANY(intent_ids)"
                    " ORDER BY intent_ids::text"
                ),
                {"o": origin_id},
            )
        ).all()
        gc = (
            await conn.execute(
                text(
                    "SELECT intent_ids::text, status, aggregate_score::float8"
                    " FROM group_candidates WHERE CAST(:o AS uuid) = ANY(intent_ids)"
                    " ORDER BY intent_ids::text"
                ),
                {"o": origin_id},
            )
        ).all()
    return {
        "mc": sorted((str(a), str(b), s, c) for a, b, s, c in mc),
        "latches": sorted((ids, s, sc) for ids, s, sc in latches),
        "group": sorted(gc),
    }


async def test_2_duplicate_event_no_double_candidates(
    api_client, db_engine, worker_env
):
    """冪等性(10 §4.7・引用#8・G2条件③): 同一Event2回投入でmatch_candidates
    が二重生成しない(独立簡易配置: 起点U0+相手15・同一時間帯)。"""
    prefix_subject = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    ha = await _user(api_client, prefix_subject)
    a = await _intent(api_client, db_engine, ha, _structured())
    for _ in range(15):  # 相手15(dense相当・全員同一時間帯)
        h = await _user(api_client, prefix_subject)
        await _intent(api_client, db_engine, h, _structured())
    # Workerにcreated Eventを処理させる(embedding→L1〜3まで走る)
    await _wait_candidate_count(db_engine, a["id"], expected_ge=1)
    before = await _candidate_rows(db_engine, a["id"])
    assert len(before) >= 1
    # 同一3点組ペイロードを2回投入(Stage1のUNIQUEで2回目以降はduplicate)
    payload = json.dumps(
        {"event_type": "created", "source_intent_id": a["id"], "version": 1}
    ).encode()
    await worker_env.bus.publish_raw(payload)
    await worker_env.bus.publish_raw(payload)
    await asyncio.sleep(2.0)  # Workerの処理settle
    after = await _candidate_rows(db_engine, a["id"])
    assert after == before  # 行数・内容とも不変(二重生成なし)
    await _teardown_prefix(db_engine, prefix_subject)  # 独自prefixのteardown


async def _wait_candidate_count(db_engine, origin_id, *, expected_ge, timeout=20.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        rows = await _candidate_rows(db_engine, origin_id)
        if len(rows) >= expected_ge:
            return rows
        await asyncio.sleep(0.3)
    pytest.fail("candidates not generated in time")


async def _candidate_rows(db_engine, origin_id):
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT intent_a_id, intent_b_id, status,"
                    " cheap_judge_score::float8 FROM match_candidates"
                    " WHERE intent_a_id = CAST(:o AS uuid)"
                    " OR intent_b_id = CAST(:o AS uuid)"
                    " ORDER BY intent_a_id, intent_b_id"
                ),
                {"o": origin_id},
            )
        ).all()


async def _teardown_prefix(db_engine, prefix: str) -> None:
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM match_candidates WHERE intent_a_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
                " OR intent_b_id IN (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ), p,
        )
        await conn.execute(
            text(
                "DELETE FROM latches WHERE intent_ids && (SELECT array_agg(id)"
                " FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))::uuid[]"
            ), p,
        )
        await conn.execute(
            text(
                "DELETE FROM group_candidates WHERE intent_ids && (SELECT"
                " array_agg(id) FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))::uuid[]"
            ), p,
        )
        await conn.execute(
            text(
                "DELETE FROM match_events WHERE source_intent_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ), p,
        )
        await conn.execute(
            text(
                "DELETE FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ), p,
        )
        await conn.execute(text("DELETE FROM users WHERE auth_subject LIKE :p"), p)


async def test_3_intent_update_drops_stale_pair(
    api_client, db_engine, worker_env
):
    """02#7更新E2E(design §2.5-A): 2 Intent→候補生成→PATCH(時間帯を交差
    しない値へ)→debounce窓解放(FakeClock)→再評価で旧ペアが新評価世代で
    再生成されない=消失。"""
    prefix_subject = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    ha = await _user(api_client, prefix_subject)
    a = await _intent(api_client, db_engine, ha, _structured())
    hb = await _user(api_client, prefix_subject)
    b = await _intent(api_client, db_engine, hb, _structured())
    await _wait_candidate_count(db_engine, a["id"], expected_ge=1)
    row = await _pair_of(db_engine, a["id"], b["id"])
    assert row is not None  # PATCH前: 候補生成の記録(02#6も兼ねる)
    # PATCH: Bの時間帯を交差しない値へ(+72時間)
    new_payload = _payload(_structured(start=_future(BASE_HOURS + 72)))
    new_payload["raw_text"] = "別の日の別の時間帯で"
    resp = await api_client.patch(
        f"/v1/intents/{b['id']}",
        headers=hb, json=new_payload,
    )
    assert resp.status_code == 200, resp.text
    new_version = resp.json()["intent"]["version"]
    # debounce窓解放(ws-1流儀: 受信settle後にClockを進める)
    await asyncio.sleep(1.0)
    worker_env.clock.advance(timedelta(seconds=10))
    await _wait_processed_version(db_engine, b["id"], new_version)
    await asyncio.sleep(1.0)  # 再評価チェーン(_run_post_retrieval)のsettle
    # 旧ペアは新評価世代で再生成されない(候補の消失)。主検証は
    # 「A現行version×B新versionの行が存在しない」=新評価世代での非再生成
    async with db_engine.connect() as conn:
        latest = (
            await conn.execute(
                text(
                    "SELECT intent_a_version, intent_b_version FROM"
                    " match_candidates WHERE intent_a_id = CAST(:a AS uuid)"
                    " AND intent_b_id = CAST(:b AS uuid)"
                    " ORDER BY created_at DESC LIMIT 1"
                ),
                {"a": min(a["id"], b["id"]), "b": max(a["id"], b["id"])},
            )
        ).first()
    assert latest is not None  # 旧評価世代の行は残る(履歴)
    async with db_engine.connect() as conn:
        new_gen = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM match_candidates"
                    " WHERE intent_a_id = CAST(:a AS uuid)"
                    " AND intent_b_id = CAST(:b AS uuid)"
                    " AND intent_a_version = :av AND intent_b_version = :bv"
                ),
                {
                    "a": min(a["id"], b["id"]), "b": max(a["id"], b["id"]),
                    "av": a["version"], "bv": new_version,
                },
            )
        ).scalar_one()
    assert new_gen == 0  # 新評価世代のペア行は存在しない=消失
    # 起点Aの現行評価対象(次のJev選択)に旧ペアが入らない
    async with db_engine.connect() as conn:
        pending = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM match_candidates"
                    " WHERE (intent_a_id = CAST(:a AS uuid)"
                    " OR intent_b_id = CAST(:a AS uuid))"
                    " AND jev_result IS NULL AND status = 'pending'"
                    " AND intent_a_version = :av"
                ),
                {"a": a["id"], "av": a["version"]},
            )
        ).scalar_one()
    assert pending == 0
    await _teardown_prefix(db_engine, prefix_subject)


async def _wait_processed_version(db_engine, intent_id, version, timeout=15.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with db_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT status FROM match_events"
                        " WHERE source_intent_id = CAST(:i AS uuid)"
                        " AND version = :v"
                    ),
                    {"i": intent_id, "v": version},
                )
            ).first()
        if row is not None and row[0] == "processed":
            return
        await asyncio.sleep(0.2)
    pytest.fail(f"updated event not processed: {intent_id} v{version}")


async def test_4_bucket_reeval_generates_candidates(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """02#8 Bucket再評価(design §2.5-B・06 §9 FR-09): time_startを未来Bucketに
    置いた2 Intent→FakeClockでBucket境界を経過→run_onceで抽出→候補生成。
    _SELECT_BUCKET_TARGETS側の明示試験(test_10はcatch-up側)。"""
    clock = _clock()
    # 未来Bucket: 現在の30分Bucketの2つ先(境界経過の余地を持たせる)。
    # time_startが未来ならexpires_at(作成時の+7日上限内)は十分遠方で
    # catch-up対象外になる
    bucket_ahead = clock.now() + timedelta(minutes=45)
    start = bucket_ahead.isoformat()
    ha = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=start))
    hb = await _user(api_client, field)
    b = await _intent(api_client, db_engine, hb, _structured(start=start))
    calls: list[str] = []

    async def pipeline(intent_id):
        calls.append(str(intent_id))
        async with db_engine.begin() as conn:
            await run_candidate_retrieval(conn, clock, intent_id)

    runner = ReevalRunner(
        engine=db_engine, clock=clock,
        guard=ReevalGuard(redis_client, key_prefix=redis_sweep),
        pipeline=pipeline, interval_sec=0.01, batch_limit=50,
    )
    assert await runner.run_once() == 0  # 未来Bucket・期限遠方→対象なし
    assert calls == []
    clock.advance(timedelta(minutes=45))  # Bucket境界を経過
    done = await runner.run_once()
    assert done >= 1
    assert set(calls) >= {a["id"], b["id"]}
    row = await _pair_of(db_engine, a["id"], b["id"])
    assert row is not None  # 時刻到来による候補生成(02#8)
```

(注記1: ヘルパー `_cli_idp_token・field・redis_sweep・_user・_future・_structured・_payload・_intent・_clock` はtest_degraded_e2e.pyと同一実装をこのファイル内へコピーする(redis_sweepのprefixは `ws8k-`・fieldのteardown SQLはlatches・group_candidatesの `&&` 掃除込みの同一内容)。import共有はしない流儀)
(注記2: `_full_chain`・`_peer_ids`・`_stores` はTask 12冒頭に定義済み。`_peer_ids` はtest_matching_retrievalと同一流儀)
(注記3: 試験2・3は独自prefix+`_teardown_prefix`で後始末する(field fixtureは試験1・4のみ使用))

- [ ] **Step 2: 収集確認(実行しない)**

Run: `cd backend && uv run pytest --collect-only tests/integration/test_k_limits_e2e.py -q`
Expected: 4件収集。`make test` でimportが検証されグリーン

- [ ] **Step 3: lint・コミット**

```bash
make lint && make test
git add backend/tests/integration/test_k_limits_e2e.py
git commit -m "test: K上限裏付け+冪等性+02#7更新E2E+02#8 Bucket再評価の4試験(dense配置118 Intent・決定性2回実行)"
```

### Task 13: 報告書作成・完了条件の検証

**Files:**
- Create: `docs/plans/M2/ws-8-report.md`

- [ ] **Step 1: 完了条件1〜7を全て実行して証拠を採る**(`docs/plans/M2/ws-8-report.md` を§7の形式で作成。test-ci・docker系・実APIは実行しない=「スーパーバイザー検証待ち」と記録)

```bash
make lint && make test                       # 条件1
cd backend && uv run pytest --collect-only tests/integration/test_degraded_e2e.py tests/integration/test_k_limits_e2e.py -q  # 条件2
rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src   # 条件3(clock.py・stub.pyのasyncio.sleepのみ)
cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v          # 条件3
git diff main -- backend/alembic backend/pyproject.toml backend/uv.lock        # 条件4(空)
git diff --name-only main | sort                                               # 条件5
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d  # 条件6(空)
```

- [ ] **Step 2: 報告書をコミット**

```bash
git add docs/plans/M2/ws-8-report.md
git commit -m "docs: ws-8実行報告(unit全件グリーン・integration収集済み・test-ci=スーパーバイザー検証待ち)"
```

- [ ] **Step 3: 最終確認**

```bash
make lint && make test && git status --short  # 空であること
git log --oneline main..HEAD                   # コミット一覧を報告書へ記録
```

## 9. IF確定事項(実装定義の決定値。docs・design.mdが例示のみの部分を本計画で確定する。変更時は報告書に記録)

1. **BreakerParams既定値**: `window_s=60.0・error_rate_threshold=0.5・half_open_after_s=60.0・min_samples=2`(引用#1・承認事項2)。判定は `>`(50%「超」)。p95しきい値 `latency_threshold_s=6.0` はBreakerParams外のコンストラクタ引数(既定は `Timeouts.jev_s` と同値 — design §2.2「GatewayがTimeouts.jev_sと同じ値を渡す」)
2. **状態定数**: `"closed" / "open" / "half_open"`。状態遷移ログはlogger名 `latch.breaker`(info・`breaker state <old> -> <new> reason=<reason>`)
3. **min_samplesはエラー率判定のみに適用**しp95判定には適用しない(design §2.2の文言どおり。N=1のtimeoutはp95で開放する — timeout呼び出しはレイテンシ母集団に入る設計の帰結)
4. **p95位置**: 昇順ソートで `ceil(0.95×N)−1`(0-indexed)。判定は「その位置の値 ≥ latency_threshold_s」。timeout呼び出し(asyncio.timeout打ち切り)のレイテンシは打ち切り時点の `Timeouts.jev_s` を記録(実レイテンシ6秒以上は計測不能のため・承認事項1)
5. **JevJudgment.usage**: `usage: dict | None = None` を追加。`call_jev_first`/`call_jev_fallback` がenvelopeのusageを載せる。Layer 4(worker/jev.py)のjev_result書込は `{**result, provider, model}` のまま**usageを含めない**(既存DB契約不変)。g2gateのusage集計用
6. **`_FirstCandidateSkippedOpen(LLMError)`**: gateway内部例外。judge_pairの切替except節(4種)へ追加して合流。raise前にbreaker記上・送信記録は発生しない
7. **call_jev_first/call_jev_fallback はbreakerを参照しない**(design §2.8・承認事項6)。送信記録・timeout・validate_and_normalize・JevJudgment組立はjudge_pairと同一経路(`_jev_call`→`_call`)
8. **`CircuitBreaker.samples()`**: 窓内サンプル `(error, latency)` の読み取り専用ビュー(unit試験・診断用。timestampは非公開のまま)
9. **_SELECT_GROUP_PAIRSのORDER BY**: `ORDER BY intent_a_id, intent_b_id, updated_at DESC`(design §2.9(a)の趣旨=同ペア複数バージョン行の新行優先。`GREATEST(updated_at)` は行単一値のため等価の単純形)
10. **purge-match-sub CLI**: `python -m latch.events purge-match-sub`。`PubsubEventBus(Settings())` を構築し `delete_subscription()`(NotFound握り)→ `await bus.close()` → exit 0。subscription名は設定(`pubsub_subscription_match_events`)。Makefile test-ciはpytestのrcを保持(`rc=$$?; …; exit $$rc`)
11. **worker_envのconftest移設**: `make_worker_env(db_engine, *, jev=None)`(async contextmanager)+`worker_env` fixture(jev=None)+`worker_env_factory` fixture(design §3.2「worker_envへJevWorker注入オプション」の実体。既存8試験は引数名のみで無変更)
12. **g2gateのClock分担**: 送信記録occurred_atの基準=`FakeClock(G2_BASE_CURRENT_DATETIME)`(goldset meta.current_datetime=2026-10-01T11:30 JSTと一致検査)。実施日時(証拠meta)=SystemClock(g1gateと同一分担・arch test規律)
13. **compareの確定値**: THRESHOLDS=(0.70,0.80,0.90)・band境界(0.35,0.65)・ECE 10分割(最終binは1.0を含む)・AUC=Mann-Whitney U(タイ平均順位)・fit一致=|実測value×4−期待int|≤1.0(参考値)。提案0件・gold true 0件のprecision/recallはNone(レポートはnull)
14. **report**: `g2-jev-result-YYYYMMDD-HHMMSS.yaml`(JST)。--limit時はreport dictの先頭キー `note` に「部分実行(--limit)=証拠外…」+meta.partial=true。metaへgoldset SHA・JEV_QUESTIONS SHA(json.dumps sort_keys安定化)・usage合計・参考値(Precision@0.80≥0.60表示のみ)を載せる
15. **K上限E2E試験1の配置**: 計118 Intent=起点1(**min=2/max=4** — Layer1通過(2∈[2,4])とGroupEngine種トリガー(起点max≥3・ws-7 `load_group_origin`)の両立)+Layer1純通過組101(min=2/max=2)+min=3/max=4混入10(Pool用・1対1Layer1では不通過)+除外要因6(時間交差なし3+ペア予算300円3)。→ layer1_pass_count=101(純通過組のみ・起点は自己除外でカウント外)>100(引用#7「一次候補100件超」の成立)。design §2.6の「相手101人(計102)」は配置例であり、除外要因とmin=3混入でそのままでは100件超が成立せず起点条件込みで調整した(検証条件①が本体)。Poolは人数緩和検索(min≤4 AND max≥3)によりmin=3混入10名+起点が候補(純通過組はmax=2でPool外)→pool_size=10≤15
16. **冪等性試験2は独立簡易配置**(起点+15・同一時間帯)。design §2.6「dense配置(§2.6試験1と共用可)」の「可」は選択肢であり、試験1(直接チェーン呼び出し)と試験2(worker_env経由)でteardown・Event状態が干渉するため分離する
17. **make g2-gate**: `cd backend && uv run --env-file ../.env python -m latch.g2gate`(g1-gateと同一書式。`--limit/--route` は `make g2-gate -- --limit 2 --route both` で渡す)
18. **jev_smoke.py置換の注記**: `_jev_fallback.judge`(provider直接・送信記録なし)→`call_jev_fallback`(Gateway経由・送信記録1件)。振る舞い(表示・成功判定)は不変、送信記録が増えるのは08 §3の開示要件に合致する向上として報告書に記録
19. **g2gate/cases.pyの検証範囲**: status=confirmed・current_datetime一致・id一意・ペア参照存在・自己ペア排除・件数・gold構成の一致。goldset-plan §5のlabel×band整合(accept×low禁止等)はgoldset確定時の機械検査(付録A)で担保済みのため再検査しない(YAGNI)
20. **FAIL時のBLOCKED判断**: Task 4でtest_llm_factory.pyのbuild_worker_gateway構成検査がbreaker有無に敏感な場合、構成assertへの機械的追従を報告書に記録のうえ修正してよい(ws-2・ws-5の前例と同一扱い)。それ以外の既存試験の期待値変更を要するときは作業を止めてBLOCKEDとして報告

## Self-Review記録(計画書作成時の確認 — 2026-09-30)

1. **Spec coverage**: design §2.1〜§2.10の各判断→Task対応表: §2.2(BreakerParams・状態機)=Task 1・§2.3(fail_jev_exc)=Task 2・§2.8前半(公開IF)=Task 3・§2.2後半(judge_pair組み込み・build_worker_gateway)=Task 4・§2.9(a)(ORDER BY)=Task 5・§2.7(purge)=Task 6・§2.8後半(g2gate cases/runner/compare/report/CLI)=Task 7〜9・§2.4(縮退E2E 8試験)=Task 10+11・§2.5〜§2.6(K上限・冪等・#7・#8)=Task 12・§4.3(検証手順)=§7へそのまま反映。design §3.1の新規10ファイル+§3.2の変更7ファイルは§4へ反映(実装詳細確定によりjev.py・conftest.py・test_events_cli.pyを追加)。**design §4.1「test_events_cli.py」に相当するunit試験の列挙はdesignにないが§4.1のunit方針(外部プロセス不要・スタブ)に合致する追加として§4へ明示**
2. **Placeholder scan**: 「TBD」「TODO」「適切に」「〜に合わせて調整」なし。Task 11・12の(注記)はヘルパーのファイル内コピー・lint対応など実装時の明確な指示。すべてのcode stepに全文または正確なdiffを記載
3. **Type consistency**: `CircuitBreaker(clock=…, params=None, latency_threshold_s=6.0)` と `allow(now)`/`record(*, error, latency_s)` のシグネチャはTask 1(定義)とTask 4(使用)・Task 11(試験3〜6の `gateway._breaker.state` 参照)で一致。`call_jev_first/call_jev_fallback` のキーワード引数名はTask 3(定義)・Task 9(runner)・jev_smoke置換で一致。`PairOutcome.mutual_score` はTask 8(テスト側dataclass)とTask 9(runner本物)で同一形(duck-typing明記)。`make_worker_env(db_engine, *, jev=None)` はTask 10とTask 11試験8で一致
4. **Review Focus**: 5項目それぞれ→Task 1試験2/7(min_samples・p95)・Task 4 unit(開放中不呼出・timeout置換・JevOutputInvalid成功扱い・fb失敗不計上)+Task 11試験3(実DBで不呼出)にピン留め済み
5. **追加の機械チェック(2026-09-30実施)**: basename一意(現状96+新規5で衝突なし)・既存の定数ピン試験の追随访問(test_stub.pyのfail_jev系・test_gateway_jev*.pyは動作試験でbreaker=None既定により無傷・test_group_engine.pyのSQLピンへORDER BYピンを追加・`JevJudgment` の構成ピンは存在せずusage既定Noneで無傷・test_llm_factory.pyは構成検査のため§9-20で対応を規定)
6. **実装資産の再突合(2026-09-30・計画書修正時に実施)**: GroupEngineの種トリガーは「起点max≥3」(`load_group_origin`・group_engine.py:3,42,250を確認)→Task 12試験1の起点をmin=2/max=4に確定(§9-15)。`_peer_ids` の流儀(test_matching_retrieval:153)・`layer1_pass_count` が起点を除く通過数であること(test_2_kvで55相手=55)・match_candidates新規行のstatus='pending'(candidates.py:29)・`_instant`(test_events_pipeline:33)・`JevWorker._evaluate(org, origin_inp, row)` のシグネチャ(judge呼び出し順の記録はJevWorkerでなくGatewayのwrapで行う方針に修正)を確認のうえ反映




