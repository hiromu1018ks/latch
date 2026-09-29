# M2 ws-5(Layer 4 Jev — TypeSafe Jev第一候補+フォールバックLLM切替)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LLM Gatewayの`judge_pair`を実装化する(第一候補=TypeSafe Jev `jev-1.13.0`固定・フォールバックLLM=Anthropic Sonnet 5・429/529/timeoutで切替・SDK backoff無効化)と、Layer 4実行部JevWorker(K_j=8選択・Guard課税・`jev_result`記録・同一評価世代スキップとH再検証FR-07)、StubJevのSystem One互換answers形式化、実APIスモーク資産(`make jev-smoke`)・マイグレーション0003(`match_candidates.skip_reason`)を実装する。Layer 5(latch_score)・Bucket再評価・グループマッチ・circuit breakerは後続単位(ws-6〜ws-8)。

**Architecture:** Layer 4はStage1コミット後のWorkerキック(案B): `Worker._dispatch`がembedding_completedをprocessed/duplicateにした後コミット前ack前に`_kick_jev`を呼び、本体は`worker/jev.py`のJevWorkerがembedding.pyと同一の2フェーズ構成(短tx読取+選択+H再検証→tx外API呼び出し→短txガード付きUPDATE)で組む。外部API呼び出しはトランザクション外で、DB失敗は_dispatchの既存exceptが受けてackなし再配信が回収する(冪等ガード`jev_result IS NULL`)。切替はGateway内で完結し、GatewayはRedis非依存のまま(providerを返すのみ・計上はLayer 4が行う)。

**Tech Stack:** 既存+`typesafe-sdk`1件(PyPI名はTask 2で実装時確認。取得不能時はhttpx直実装代替 — §9-12)。Python 3.13 / SQLAlchemy[asyncio]+asyncpg / redis-py(asyncio) / fakeredis[unit]。

**Spec:** `docs/plans/M2/ws-5-design.md`(agent1設計メモ。**未解決論点なし** — design §5のsupervisor承認事項〔skip_reason列+マイグレーション0003・既存試験の機械的追随〕と解釈記録〔score正規化分母=4・Guard課税は起点側のみ〕は2026-09-29承認済みの確定事項として本計画へ反映。実装時確認事項§5-3〔PyPIパッケージ名〕・§5-4〔retry引数名〕はTask 2へ組み込み、httpx直実装代替は設計不変の予備経路として§9-12に明記)

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m2-ws-5`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築したうえで、Task 2で `uv add` を実行する(依存追加があるためlockが進む)。
- **共有ci-db運用**(STATUS運用ルール1〜3): 本単位は**マイグレーション0003を追加する単位**である。**agent3は `make test-ci` / `make migrate` / `make up` / `make down` / `docker compose …` / `docker build` / `docker pull` を一切実行しない**。開発はunit試験(`make lint`・`make test`)で完結させ、報告書に「**test-ci=スーパーバイザー検証待ち**」と記録する。integration試験ファイルは作成するが**実行せず**、収集(`--collect-only`)のみ確認する。`make test` はintegrationファイルの収集(import)まで行うため、構文・importの正当性はunit実行で検証される。なお本単位はwave単独のため並走とのDB取り合いは計画上発生しない(運用ルール1・2は報告書へ明記)。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。**計画時点で機械確認済み**: 現状 `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空。本計画の新規7ファイル(`test_jev_format.py`・`test_typesafe_jev.py`・`test_anthropic_jev_fallback.py`・`test_gateway_jev_switch.py`・`test_layer4.py`・`test_worker_jev.py`・`test_matching_jev.py`)は既存と衝突しない。各Taskのコミット前に同コマンドが空であることを再確認する。
- **ピン試験の干渉確認済み**(supervisor追記4): 設定系「ちょうどN項目」ピンは `rg "model_fields" backend/tests` で4ファイル確認し、干渉は `tests/unit/llm/test_llm_factory.py:79` の `test_llm_settings_are_exactly_seven_fields` のみ(+2項目で9項目化 — Task 6の機械的追随)。`test_geo_settings_are_exactly_three_fields`・`test_auth_settings_are_exactly_eight_fields`・`test_send_record_fields_are_exactly_the_allowlist` はprefix違いで非干渉。
- **並走単位なし**(design §1前提: 実行waveはws-5単独)。ただし既存ファイルへ触れるため、各Taskのコミット前に `git status --short` で意図しないファイルの変更が混入していないことを確認する(§4の一覧以外に差分が出ていたら作業を止めて報告する)。
- **固定値の遵守**: design §2 の採用判断と本計画§9のIF確定事項は固定値。**変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **外部SDKの一次確認**(memory: use-context7-for-external-sdks): Task 2でtypesafe-sdkを、Task 3でanthropic SDKの引数を実装前に確認する(context7・パッケージ署名)。docs確定値(エンドポイント・質問型・応答形式)とSDK署名が異なるときはSDK署名に合わせる(呼び出し側の定数・値は不変)。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `docs:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` / `uv add` 経由。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| 呼び出し条件はLayer 3通過の上位候補。1候補1回のAPI呼び出しで両方向を判定=1実行回数。フォールバック呼び出しも同一の1実行回数に計上 | 06 §5・04 §4 D-16 |
| 第一候補=TypeSafe Jev(`jev-1.13.0`固定・エイリアス不使用)。429・529・timeoutでフォールバックLLM(Anthropic Claude API・Sonnet 5)へ切替。**SDK既定のbackoff retryは無効化・再試行なし**。モデル更新時はバージョンを上げて日本語評価を再実行してから切替 | 06 §5・07 §4・07 §1 |
| 入力は両Intentの正規化テキスト([hard]/[soft]行・soft constraintsとD-04降格NG条件を含む)のみでraw_textは送らない。visibility・notification_levelは含めない。stateは`{"intent_a":…,"intent_b":…}` | 07 §4・06 §5・01 §21 |
| 7軸の写像: would_a_accept_b・would_b_accept_a・latent_yes→noul、purpose_fit・mood_fit・timing_fit・social_fit→score(5段階0〜4)。7質問は1リクエスト。質問の構造・型・キー名は確定値、instructions・criteriaの最終確定はG2日本語評価(09 §4) | 07 §4 |
| 出力検証: answersの同キー性・noul値域[0,1]・score値域[0,4]を防御的に検証。失敗は再試行せず縮退(skipped保留・実装不整合)。応答のmodelフィールドをjev_resultへ記録 | 07 §4 |
| スコア計算はLayer 5の担い(本単位は書かない)。scoreの値(0〜4)は**最大レベル4で割って**[0,1]へ正規化して保存(supervisor承認の解釈記録・design §2.4)。confidenceは5軸の分析用・would_*には保存しない・noulに付かない。フォールバックLLMはconfidenceを返さないためnull | 07 §4・05 §2 |
| **reasonは廃止**(第一候補・フォールバック双方で生成も格納もしない) | 07 §4・05 §2・08 §2.5 |
| jev_result格納形: would_a_accept_b / would_b_accept_a(noul確率0〜1)/ jev_5axis(5軸の正規化値0〜1とconfidence)/ provider("typesafe_jev" / "fallback_llm")/ model(第一候補時の応答バージョンID・フォールバック時はnull)。同一バージョン組の再評価では再実行せず保持 | 05 §2 |
| 切替表: 429・529・timeout→即座に切替(backoff再試行なし)/ フォールバック失敗(timeout・429・5xx・出力検証失敗)→縮退(skipped保留・circuit breaker開放対象はws-8)。フォールバックは同じstate・7質問にキー同一・noul→[0,1]・score→[0,4]のJSONをstructured outputで返す。Layer 5以降は判定経路に依存しない | 07 §4 |
| timeoutは第一候補・フォールバックとも6秒(層別予算Jev ≤5秒+1秒吸収)。双方で再試行なし | 07 §1・06 §8 D-15 |
| K_j=8(1イベント処理あたり上限・回数ベース): (1)1対1に最低4回保証(cheap_score降順上位4件) (2)残り最大4回をグループ構成ペアへ(グループはws-7) (3)全ペア判定が揃わない集合は提案化せず保持(未判定ペア継続優先はws-7) (4)割当順序=1対1最低枠→継続→新規。グループ由来ペアが存在しないイベントでは残り枠を1対1へ繰り上げ(1対1の実効上限はK_j=8) | 06 §5・06 §8 D-24 |
| 同一評価世代スキップ(FR-07): 同一バージョン組かつjev_result既存在なら再実行せず保持、Layer 1の再検証(Hの再計算)のみ行う。H再検証で不成立となったペアはstatus=closedへ閉じる。順位変動で新たに上位に入ったペアは新しいペアとして通常どおり実行対象 | 06 §5 |
| Jev予算3層とD-16上限はws-4実装済み。契約: `request_execution(intent_id, user_id)` をLayer 4実行直前に呼ぶ・deny時はmatch_candidates.status=skipped遷移と理由記録・`record_execution(provider)` は実際のAPI呼び出しベースで切替完了側(ws-5)が呼ぶ・INCR先行(denyも消費) | 06 §5・04 §4 D-16・04 §5・ws-4報告書§5-6・ws-4設計§2.4 |
| 縮退(D-15): 双方のAPI障害・D-16上限到達・予算/頻度由来スキップのいずれかで提案見送り・status=skipped。保留は日次リセット後・障害回復後のMatch Event・再評価経路で再評価される。単発の429・529・timeoutは第一候補内の切替で吸収 | 06 §8 D-15 |
| StubJev応答はSystem One互換answers形式(model+answers同キー+noul[0,1]・score[0,4]+confidence+usage)。第一候補と同じanswers取り出し経路を通す。スタブ値は決定的に固定 | 10 §1 |
| 層別予算: 初期LATCH判定p95 10秒のうちJev ≤5秒(切替時は第一候補6秒+フォールバック6秒の最悪12秒を許容)。レイテンシ注入はws-8が検証対象 | 06 §1・10 §4.1 |
| K上限の裏付け: 密集配置でJev ≤8回が記録で守られ、切り詰めが決定的(同一入力2回で同一結果) | 10 §4.6・02 §4 #11 |
| 送信先と記録: TypeSafe AI(api.typesafe.ai・米国)/フォールバック時はAnthropic Claude API。LLM Gatewayが送信先・データ種別・時刻を記録(既存latch.llm.send構造化ログ) | 07 §4・08 §3 |
| Layer 1〜5はembedding_completedを起点にのみ走る(作成・更新Eventの処理はEmbedding要求キックまで)。処理失敗は5回再試行→隔離(既存経路) | 06 §1・§9・12 C6 |
| TypeSafe API仕様: POST https://api.typesafe.ai/v1/systemone・Bearer認証・state+model+questions(質問名→定義マップ・応答は同キー)。質問型3種(noul/choice/score)。応答=model+answers+usage。Python SDK `typesafe_sdk`・`AsyncTypeSafeClient`・`RetryPolicy(max_retries=0)`でretry無効化。例外: TypeSafeError/TypeSafeAPIError(.status)/TypeSafeRateLimitError/TypeSafeAPIConnectionError | 07 §4・design §1.3 T1〜T7 |
| Anthropic仕様: モデルID `claude-sonnet-5`。sampling系(temperature・top_p・top_k)は廃止 — 送信すると400。structured outputsは `output_config={format:{type:"json_schema",schema}}`。`max_retries` 既定2→0へ。タイムアウトは`anthropic.APITimeoutError` | design §1.3 A1〜A4 |
| 既存実装資産との接続(gateway・providers・stub・anthropic・cost・matching・embedding・main・layer1・completion・alembic 0002) | design §1.4 |
| 時刻参照はすべてClock経由(JST日付キー・再選択のJST境界を含む) | 04 §5・12 C2 |
| supervisor承認事項(2026-09-29): skip_reason列+マイグレーション0003・既存試験の機械的追随(test_gateway_jev・test_stub・test_llm_factory+2項目・test_llm_gemini改名)・解釈記録(score/4正規化・Guard課税は起点側のみ) | design §2.4・§2.5-1・§2.7・§5 |

## 2. グローバル制約(全タスクに暗黙に適用)

- **永続化は `sqlalchemy.text()` 生SQLのみ**(worker/matching・embedding.pyと同じ形式)。ORMモデル・リポジトリ層を作らない
- **bind param は `CAST(:x AS ...)` 形式**(NULLを渡しうるパラメータは必須。test_layer_sql.py流儀のcompile検査が回帰を防ぐ)
- **時刻はClock経由のみ**。製品コード(`backend/src/latch/`)で実時間参照禁止(arch test `test_arch_no_direct_time.py` が強制)。再選択規則のJST境界・Guardの日付バケットもClockから導出する(C2)
- **例外は握り潰さない**(matching/ 配下の規律)。ただし設計上握るべきものは明示的に限る(design §2.9の表): JevWorkerはLLMError・JevOutputInvalidErrorをskipped記録に変換する(例外にしない)が、DB書き込み失敗・JevCostDependencyError(Guard Redis失敗)は伝播させる(fail-closed→ackなし再配信)。`llm/` のプロバイダ実装はSDK例外を翻訳するのみで握らない(gateway.pyの既存wrapが最終関門)
- **例外メッセージにIntent本文を入れない**(08 §2.4「例外・エラーはIDのみ」。status等の機械情報は可)
- **K_j=8・ONE_ON_ONE_MIN=4・JEV_MODEL・ANTHROPIC_JEV_FALLBACK_MODEL・タイムアウト6秒・D-16値はモジュール定数**(settings化しない — design §2.8・ws-4 §2.8-4規律)。docs確定値の調整はdocs改版を伴う
- **丸めなし**: score正規化(`/4`)・jev_result格納とも丸めない
- **改行・行長**: ruff(E,F,I,UP,B)と `ruff format`(行長88)を毎コミット通す
- **unit試験は外部プロセス不要・実時間待ちなし**(fakeredis・スタブクライアント注入・FakeClock)。SQL文字列の正当性はcompile検査(unit)+実DB試験(integration・スーパーバイザー検証時)の二段構え
- **全タスクのコミット前に**: `make lint && make test` グリーン・`git status --short` で差分が§4の一覧どおり・basename一意コマンドが空

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **第一候補の出力検証失敗でフォールバックへ切り替わってしまう** — 検証失敗は切替条件外(07 §4「失敗は再試行せず縮退へ・実装不整合」)。誤切替は無意味なフォールバック課金と縮退記録の汚染を生む → Task 5の `test_primary_invalid_output_does_not_switch`(JevOutputInvalidError伝播・フォールバック不呼出・送信記録1件)
2. **SDK/Anthropicの既定backoff retryが効いて遅延とコストを二重払いする** — T5(既定max_retries=2・backoff_initial=0.5〜5.0)/A4(Anthropic既定2)を無効化しないと429時に再試行が発生し、再試行なし・6秒・二重払い排除(引用#2・#10)に違反 → Task 2・3の設定ピン(RetryPolicy max_retries=0・max_retries=0)+Task 6の実構築ピン
3. **同一バージョン組の評価済みペアが再評価される(FR-07違反)** — 選択SQLから`jev_result IS NULL`が落ちると課金が二重になりjev_resultが上書きされる → Task 8のSQLピン(`jev_result IS NULL`)+Task 9の冪等試験(API・guard不呼出)+Task 12 integration試験3(更新時刻不変)
4. **Guard denyがINCR消費なしに発生する・deny後に残枠補充が走る** — INCR先行(denyも消費・ws-4承認)と補充禁止(§2.5-4「deny理由は同一Event内で同じ判定になるため補充は無駄なINCR」)の双方が崩れるとD-16上限の裏付けが壊れる → Task 9のdeny試験(実行前INCR+record不呼出・targetsをそのまま消化)+Task 12 integration試験5(denyもカウンタ増加)
5. **フォールバックのリクエストにsampling系パラメータ(temperature/top_p/top_k)が乗って400になる** — A2: Anthropic 5系はsamplingパラメータを廃止・送信すると400。乗らないことを引数の機械検査で保証する → Task 3の `test_create_args_have_no_sampling_params`

## 4. スコープ(作成・変更するファイル一覧)

作成(§0のbasename機械確認済み・design §3.1 + 本計画の変更点):

```text
backend/src/latch/llm/jev.py                           (Task 1。JEV_MODEL・JEV_QUESTIONS・build_jev_text・validate_and_normalize・JevJudgment)
backend/src/latch/llm/typesafe.py                      (Task 2。TypeSafeJevProvider)
backend/src/latch/llm/anthropic_jev.py                 (Task 3。AnthropicJevFallbackProvider)
backend/src/latch/worker/matching/layer4.py            (Task 8。選択SQL・H再検証・close・配分純関数)
backend/src/latch/worker/jev.py                        (Task 9。JevWorker)
backend/src/latch/llm/jev_smoke.py                     (Task 11。実APIスモーク)
backend/alembic/versions/0003_match_candidates_skip_reason.py (Task 7)
backend/tests/unit/llm/test_jev_format.py              (Task 1)
backend/tests/unit/llm/test_typesafe_jev.py            (Task 2)
backend/tests/unit/llm/test_anthropic_jev_fallback.py  (Task 3)
backend/tests/unit/llm/test_gateway_jev_switch.py      (Task 5)
backend/tests/unit/matching/test_layer4.py             (Task 8)
backend/tests/unit/test_worker_jev.py                  (Task 9)
backend/tests/integration/test_matching_jev.py         (Task 12。作成のみ・実行しない)
docs/plans/M2/ws-5-report.md                           (Task 12。報告ファイル)
```

変更(design §3.2):

| ファイル | 変更内容 | Task |
|---|---|---|
| `llm/gateway.py` | `judge_pair` の実装化(切替ロジック+JevJudgment戻り値)・`jev_fallback` プロバイダ引数(既定None=第一候補と同一 — §9-4)・`build_embedding_gateway` → `build_worker_gateway` 改名拡張(embedding+jev+フォールバック real) | 5・6 |
| `llm/errors.py` | `JevOutputInvalidError`(provider属性付き — §9-4)をTask 1で先行追加・`LLMRateLimitError`・`LLMOverloadedError`・`LLMConnectionError` をTask 4で追加(いずれもLLMError継承) | 1・4 |
| `llm/providers.py` | `JevProvider.judge` の戻り値docstringをSystem One envelope形式へ約束変更(署名不変) | 4 |
| `llm/stub.py` | `DEFAULT_JEV_RESPONSE` をSystem One envelope形式へ(値は決定的固定: noul=0.5・score=2.0・confidence=0.5) | 4 |
| `llm/__init__.py` | 公開API追記(`build_worker_gateway`・新例外4種・`TypeSafeJevProvider`・`AnthropicJevFallbackProvider`・`JevJudgment`・`JEV_MODEL`) | 4・5・6 |
| `worker/main.py` | `_kick_jev`(embedding_completed processed/duplicate後)・JevWorker DI(guard・cost_storeはreevalと同一redis接続から構築)・`build_worker_gateway` への呼び替え | 6・10 |
| `llm/embed_smoke.py` | `build_worker_gateway` へのimport先変更のみ(ロジック無変更) | 6 |
| `settings.py` | `llm_typesafe_api_key`・`llm_typesafe_base_url` 追加(AliasChoices形式) | 6 |
| `backend/pyproject.toml` | 依存へ `typesafe-sdk` 追加(Task 2でPyPI名実装時確認) | 2 |
| `backend/uv.lock` | `uv add` による自動更新(手編集しない) | 2 |
| `Makefile` | `jev-smoke` ターゲット追加(embed-smokeと同型・`uv run --env-file ../.env`) | 11 |
| `tests/unit/llm/test_gateway_jev.py` | スタブ応答形式追随(envelope)・戻り値JevJudgment化・timeout試験は2件記録化(機械的追随) | 4・5 |
| `tests/unit/llm/test_stub.py` | JEV期待値をenvelope形式へ(機械的追随) | 4 |
| `tests/unit/llm/test_llm_factory.py` | `test_llm_settings_are_exactly_seven_fields` を9項目化(+2項目・改名付き)・LLM_ENV_VARSへ2変数追加・default/env試験追記(機械的追随 — ws-2の前例と同じsupervisor承認事項) | 6 |
| `tests/unit/test_llm_gemini.py` | `build_embedding_gateway` → `build_worker_gateway` 改名追随(3試験の改名+real時jev/jev_fallback検証)(機械的追随) | 6 |
| `tests/unit/test_worker.py` | `_kick_jev` 配線のピン追加(embedding_completed processed/duplicateで呼出・created/updatedでは呼ばない・未注入時no-op) | 10 |

既存integration(test_matching_cheapjudge.py等)は `run_candidate_retrieval` の外部契約が不変のため無変更で動く見込み。影響が出た場合は機械的追随を報告書に記録する。

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`。

## 5. 禁止(触ってはいけないもの・スコープ外の判断基準)

- **docker系コマンド一切**(§0): `make test-ci` / `make up` / `make down` / `make migrate` / `docker compose …` / `docker build` / `docker pull`。マイグレーション0003の適用とapiイメージ再ビルド・integration実行・jev-smoke実行(課金)はすべてスーパーバイザーの検証手順に含まれる
- **design §3.3の禁止**: `backend/src/latch/` 配下の intents / auth / users / ratelimit / geo / g1gate / events / core 各モジュール。`worker/` の既存ファイルのうち stage1.py・main.py 以外(embedding.py・embedding_text.py・debounce.py・backfill.py・__main__.py・`matching/` 配下の既存6ファイル{origin,layer1,layer2,layer3,candidates,runner}.py — **layer1.LAYER1_WHEREはimportして再利用するのみ・変更しない**)。`backend/alembic/` の既存分(0001・0002不変。0003追加のみ)。`backend/src/latch/llm/` のうち §4 列挙以外(anthropic.py・gemini.py・records.py — `adapt_schema_for_anthropic` と `_STRIP_KEYS` はanthropic.pyから**importして流用するのみ**)。`compose.yaml`・`frontend/`・`prototype/`・`docs/`(01〜12改版不要・05 §2へのskip_reason追記は次回docs改版に含める方針 — §2.7)・`.env`・`.env.example`(実値はユーザー/スーパーバイザー領域)。`docs/plans/STATUS.md`(スーパーバイザー管理)。`docs/plans/M0/`・`M1/`・`M2/` の既存ファイル(design・過去plan・過去report)。`backend/tests/` の§4に列挙した以外の既存試験
- スコープ外と判断する基準(必要になったと感じても作らない — design §1.5・§2.10):
  - **circuit breaker・障害注入・G2ハーネス**(ws-8): 切替注入ポイントの構造確保のみ(Task 5)。開放/半開・p95測定は作らない
  - **Layer 5 LATCH Engine**(ws-6): MutualScore・閾値・latch_score書き込み。本単位はjev_result記録まで
  - **30分Bucket再評価・catch-upスキャン**(ws-6): 本単位のLayer 4はembedding_completed起点のみ。`JevWorker.handle(intent_id)` を起点非依存のIFにしてある(ws-6が呼ぶ)
  - **グループマッチ**(ws-7): 候補Pool・貪欲法・グループ由来ペアのK_j配分(規則2〜4)・未判定ペア継続優先。本単位は1対1のみ(`select_jev_targets` に定数とpair_kindタグを置くのみ)
  - **リセットジョブ本体**(M3-4): skipped候補の再評価イベント発行。本単位は再選択規則(§9-8)で選択側の再評価可能性を担保するのみ
  - **質問文言(instructions/criteria)の最終調整**(G2日本語評価): 07 §4の実装イメージ初期値を§9-2として固定し全文ピンで守る
  - **usageトークンのDB記録・コスト集計**(M4): 送信記録は既存の構造化ログ枠組み
  - **ペア評価の並行化(asyncio.gather)・行単位のFOR UPDATEクレーム**(design §2.10-4・5): 直列+at-least-once
  - **jev_resultへのskip理由格納**(design §2.10-6): skip_reason列(0003)を使う。jev_resultは「IS NULL=未評価」フィルタのためにNULLのまま保つ
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 12で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(本単位のunit追加分・既存試験の機械的追随を含む)
   検証: `make lint && make test` — ともにexit 0
2. **integration 1ファイル(8試験)が収集できる** — **ただし実行はしない**(§0。実DB・実Redis・compose常設apiが必要なためスーパーバイザー検証時に実施)
   検証(実装側): `cd backend && uv run pytest --collect-only tests/integration/test_matching_jev.py -q` がexit 0(8件収集)。報告書に「**test-ci=スーパーバイザー検証待ち**」と記録
3. **実時間参照が `core/clock.py` のみ**(llm/・worker/jev.py・worker/matching/layer4.py 配下はヒットしない)
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
4. **マイグレーションは0003追加のみ・0001/0002とdocs(01〜12)に差分なし**
   検証: `git diff --stat main -- backend/alembic/versions/0001_initial_schema.py backend/alembic/versions/0002_geofeatures.py docs` — 出力なし(0003は新規ファイルとして表示される・reportはTask 12コミット後に再確認)
5. **変更ファイルが§4の一覧どおり(作成15+変更16=31ファイル)**
   検証: Task 12 Step 3の報告コミット後に `git diff --name-only main | sort` が§4の一覧(31ファイル・report込み)と完全一致。`git status --short` が空(未コミット変更なし)
6. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし
7. **既存unit試験がすべてグリーンのまま**(既存試験の期待値変更は§4に列挙した機械的追随のみであることの証明として `make test` の全件数を報告書へ記録し、変更が列挙対象以外に及んでいないことを§4との突合で示す)

## 7. 報告形式

**結果ファイル**: `docs/plans/M2/ws-5-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M2 ws-5(Layer 4 Jev)実行報告

- ブランチ: m2-ws-5 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る(全件数も)> |
| 2 | integration 8試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | <collect-only出力> |
| 3 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 4 | alembic 0001/0002・docs無変更 | PASS/FAIL | <git diff --stat 出力(空なら「空」)> |
| 5 | 変更ファイル=§4の30ファイル | PASS/FAIL | <git diff --name-only出力> |
| 6 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 7 | 既存unit試験グリーン維持 | PASS/FAIL | <make test 全件数と§4列挙以外の期待値変更がないことの確認結果> |

**test-ci=スーパーバイザー検証待ち**(STATUS運用ルール1〜3・マイグレーション0003追加のため
migrate/test-ci/migrateは実装側で未実行。wave単独のため並走とのDB取り合いは計画上なし)

## design §5 実装時確認事項の結果
3. typesafe-sdkのPyPIパッケージ名とバージョン: <uv addした実名とバージョン。取得不能で
   httpx直実装(§9-12)へ切替した場合はその理由と実装差分を記録>
4. AsyncTypeSafeClientのretry引数名: <実装時に確認した署名(retry / retry_policy・design §5-4)
   と渡した実引数。効果は同じmax_retries=0>
7. noul応答のJSON形状: <スモーク時に確認(スーパーバイザー検証時)。SDK型(float)採用のまま>
その他(design §5-8〜12)は先送り・引継ぎ記録として無変更

## 固定値の変更有無(design.md §2・本計画§9)
- Gateway内切替+切替条件4種(design §2.2の表): 変更なし / 変更あり(<前→後+理由>)
- score正規化分母=4(design §2.4・supervisor承認の解釈記録): 変更なし / 変更あり(<前→後+理由>)
- Guard課税は起点Intent側のみ(design §2.5-1・承認済み解釈記録): 変更なし / 変更あり(<前→後+理由>)
- K_j選択は永続行から・補充なし(design §2.5): 変更なし / 変更あり(<前→後+理由>)
- H再検証はLAYER1_WHERE再利用・evaluated行のみclose(design §2.6): 変更なし / 変更あり(<前→後+理由>)
- 再選択規則4分岐(design §2.7): 変更なし / 変更あり(<前→後+理由>)
- 本計画§9のIF確定事項(質問定数・プロンプト全文・テキスト行形式・計上ルール・SQL全文): 変更なし / 変更あり(<前→後+理由>)

## (ws-6・ws-7・ws-8への引継ぎ)
- ws-6: JevWorker.handle(intent_id) は起点非依存。Bucket再評価・catch-upから同一部品を呼ぶ
  (reevalガードは入口で共用 — ws-4報告書§5-7)。latch_score計算・保留キューはws-6
- ws-7: select_jev_targets の純関数と K_J=8・ONE_ON_ONE_MIN=4・pair_kind タグが拡張点。
  グループ由来ペア(規則2〜4)・未判定ペア継続優先・group_candidates保持はws-7
- ws-8: circuit breakerはjudge_pairの第一候補呼び出し(_jev_call の第一候補側)を包む位置に
  注入する。開放中の第一候補停止・半開試験・p95レイテンシ測定はws-8

## スーパーバイザー検証手順(test-ci実行時)
1. `.env` の `LATCH_TYPESAFE_API_KEY` に実値が設定済みであることを確認(値の有無まで確認。
   キー名の存在だけでは「設定済み」ではない。jev-smoke用・test-ci自体には不要)
2. `docker compose build api` — apiイメージ再ビルド(STATUS運用ルール4・依存追加があるため必須)
3. `make migrate` — 0003適用確認(alembic_version=0003・match_candidates.skip_reason列の存在)
4. `make test-ci` — 既存全数(818)+本単位integration 9件がグリーン
5. `make jev-smoke` — 実API 1呼び出し(課金)。`make jev-smoke FALLBACK=1` でフォールバック直接
   呼び出しも任意。noul応答のJSON形状確認(design §5-7)を併せて実施
6. マージ前: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空
   (運用ルール5)
7. test-ci後、Redis残存(prefix掃除)とuser残存(subject LIKE 'm2ws5-%'=0件)を1回手動確認

## コミット一覧
<git log --oneline main..HEAD の出力>

## 補足(詰まった点・判断した点があれば)
```

完了後の最終返信は報告ファイルのパスと完了条件7項目の結果一覧(項目2の実行部分は「test-ci=スーパーバイザー検証待ち」)。

## 9. IF確定事項(実装定義の決定値。docsが例示のみの部分を本計画で確定する。変更時は報告書に記録)

### 9-1. モジュール構成と経路名の区別

- **送信記録の送信先(destination・provider.name)**: `typesafe`(第一候補)/ `anthropic`(フォールバック)/ `stub`(スタブ・ci構成は第一候補・フォールバックとも同一スタブ)
- **jev_result格納値(JevJudgment.provider)**: `typesafe_jev`(第一候補)/ `fallback_llm`(フォールバック)。Gatewayが固定文字列で決める(スタブでも第一候補経路なら `typesafe_jev`)
- `llm/jev.py` は外部SDKに依存しない純部品(定数・純関数・dataclassのみ)

### 9-2. JEV_QUESTIONS定数(llm/jev.py・07 §4実装イメージのそのまま定数化・全文ピン対象)

```python
JEV_MODEL = "jev-1.13.0"  # 07 §4・エイリアス不使用

JEV_QUESTIONS: dict = {
    "would_a_accept_b": {
        "type": "noul",
        "instructions": {
            "a": "`state.intent_a`",
            "b": "`state.intent_b`",
            "question": "`a`の作成者が`b`との成立を提示されたときyesと答える確率。"
            "`a`の[hard]条件を満たすか、`a`の[soft]条件と判定不能NG条件に"
            "触れないことを含めて判定する。`b`が`a`の[hard]条件と意味的に"
            "矛盾する場合(予算感の著しい乖離が食事内容を成立させない等)は"
            "yesから遠ざける",
        },
        "criteria": {
            "true": "成立し得る提案である",
            "false": "成立しない提案である",
        },
    },
    "would_b_accept_a": {
        "type": "noul",
        "instructions": {
            "a": "`state.intent_b`",
            "b": "`state.intent_a`",
            "question": "`a`の作成者が`b`との成立を提示されたときyesと答える確率。"
            "`a`の[hard]条件を満たすか、`a`の[soft]条件と判定不能NG条件に"
            "触れないことを含めて判定する。`b`が`a`の[hard]条件と意味的に"
            "矛盾する場合(予算感の著しい乖離が食事内容を成立させない等)は"
            "yesから遠ざける",
        },
        "criteria": {
            "true": "成立し得る提案である",
            "false": "成立しない提案である",
        },
    },
    "purpose_fit": {
        "type": "score",
        "instructions": "`a`と`b`の目的・カテゴリの適合度(飲みたい×食べたいのズレ等)",
        "criteria": [
            "目的が根本的に異なる",
            "目的が近いが核心がずれる",
            "目的が部分的に重なる",
            "ほぼ同一目的",
            "同一目的",
        ],
    },
    "mood_fit": {
        "type": "score",
        "instructions": "`a`と`b`の雰囲気・軽さの適合度(「軽く」×「がっつり」等)",
        "criteria": [
            "雰囲気が相容れない",
            "重さが大きく異なる",
            "どちらでも成立する",
            "重さが近い",
            "雰囲気が同一",
        ],
    },
    "timing_fit": {
        "type": "score",
        "instructions": "`a`と`b`の時間帯・所要時間の適合度",
        "criteria": [
            "時間帯が合わない",
            "開始は合うが所要が合わない",
            "開始・所要が部分的に重なる",
            "時間帯・所要が近い",
            "同一時間帯",
        ],
    },
    "social_fit": {
        "type": "score",
        "instructions": "`a`と`b`の人数・社会的文脈(立場・関係性)の適合度",
        "criteria": [
            "人数・文脈が成立しない",
            "人数は合うが社会的文脈がずれる",
            "人数・文脈が部分的に合う",
            "人数・文脈が近い",
            "人数・文脈が同一",
        ],
    },
    "latent_yes": {
        "type": "noul",
        "instructions": "どちらかが明示していないが、そのIntentの記述の範囲内でYESになり得る可能性(「焼肉に行きたい」×「今日は肉系ならどこでもいい」等)",
    },
}
```

注意: `would_b_accept_a` のinstructionsオブジェクトは `a`/`b` の**参照先を入れ替え**(07 §4「逆方向・同形式」)。question文の文言自体は同一。`latent_yes` はcriteriaなし(07 §4実装イメージどおり)。

### 9-3. 正規化テキスト(llm/jev.py・07 §4の実装化)

```python
@dataclass(frozen=True)
class JevTextInput:
    """Jev正規化テキスト導出の入力。raw_textを保持しない(構造的ピン)。"""

    category_primary: str
    structured_data: dict
    participants_min: int | None
    participants_max: int | None
    time_start: datetime | None
    time_end: datetime | None
    budget_max: int | None
    geo_radius_m: int | None
```

`build_jev_text(inp: JevTextInput, *, label: str) -> str` の行生成規則(上から固定順):

1. 1行目: `Intent {label}:`(labelは "Intent A" / "Intent B" を呼び出し側が渡す)
2. `[hard] category: {category_primary}`
3. `[hard] time: {YYYY-MM-DD HH:MM}–{YYYY-MM-DD HH:MM}` — JST(astimezone(JST))・`strftime("%Y-%m-%d %H:%M")`・結合子はen dash `–`。time_endは**補完後の値**(JevWorker側で`default_time_end`適用済みのOrigin.time_endを使う)。time_startがNoneなら行ごと省略
4. `[hard] location: {location_name} 半径{R}` — `structured_data["location_name"]`。Rの書式: `geo_radius_m % 1000 == 0` なら `半径{geo_radius_m//1000}km`、それ以外(端数)は `半径{geo_radius_m}m`(07 §4例「半径2km」・design §2.3表)。geo_radius_mがNoneなら既定1000mとして扱わない — そのままm表記で出す(防御・起きない前提)。location_nameが空/欠損なら行ごと省略
5. `[hard] participants: {min}–{max}人`(en dash)・min==maxは `{n}人`。片方Noneなら行ごと省略(防御)
6. `[hard] budget_max: {budget_max}円` — **budget_maxがNone(制約なし)なら行ごと省略**
7. `[soft] {text}` — structured_data["soft_constraints"] の全項目(降格込み・配列順)。各項目は `{"text": str, "downgraded_from_ng": bool}` 形式(mapping.pyどおり)。`downgraded_from_ng is True` の項目は末尾に `(システムで判定不能)` を付加
8. 含めないもの: visibility・notification_level・alcohol_involved・category_secondary・raw_text

省略が起きる行は飛ばし、連続する行は直接改行で連結する(空行を挿入しない)。

### 9-4. 出力検証・正規化(llm/jev.py)

```python
NOUL_KEYS = ("would_a_accept_b", "would_b_accept_a", "latent_yes")
SCORE_KEYS = ("purpose_fit", "mood_fit", "timing_fit", "social_fit")
JEV_RESULT_KEYS = frozenset((*NOUL_KEYS, *SCORE_KEYS))
SCORE_MAX_LEVEL = 4  # 5段階の最大レベル(design §2.4・supervisor承認の解釈記録)


@dataclass(frozen=True)
class JevJudgment:
    """judge_pairの戻り値(design §2.2)。Layer 4は jev_result = {**result,
    "provider": judgment.provider, "model": judgment.model} を書き込む。"""

    provider: str  # "typesafe_jev" | "fallback_llm"
    model: str | None  # 第一候補時の応答バージョンID・フォールバック時はNone
    result: dict  # validate_and_normalize の出力(jev_resultのresult部)
```

`validate_and_normalize(envelope: object) -> dict` の規則:

- 入力はSystem One envelopeのdict: `{"model": ..., "answers": {7キー: answer}, "usage": ...}`。`model` と `usage` は検証対象外(値のみ取り出し)
- answerの取り出しはdict/オブジェクト両対応のヘルパ `_field(answer, key)`(dictなら `.get(key)`・それ以外は `getattr(answer, key, None)`)で行う(SDKの応答型がdictかdataclassかはTask 2実装時確認。両対応にしておけばどちらでも動く)
- noul(would_a_accept_b・would_b_accept_a・latent_yes): `_field(answer, "noul")` を取り出し、bool除外の実数で `0.0 <= v <= 1.0`。`latent_yes` のconfidenceは常にNone
- score(4軸): `_field(answer, "score")` を取り出し、bool除外の実数で `0.0 <= v <= 4.0`。confidenceは `_field(answer, "confidence")`(None または 0.0〜1.0の実数)
- 出力(丸めなし):

```python
{
    "would_a_accept_b": <noul値 float>,
    "would_b_accept_a": <noul値 float>,
    "jev_5axis": {
        "purpose_fit": {"value": <score/4>, "confidence": <0..1 or None>},
        "mood_fit": {"value": <score/4>, "confidence": <0..1 or None>},
        "timing_fit": {"value": <score/4>, "confidence": <0..1 or None>},
        "social_fit": {"value": <score/4>, "confidence": <0..1 or None>},
        "latent_yes": {"value": <noul値>, "confidence": None},
    },
}
```

- 違反(answers欠損・キー集合不一致・余分キー・値域外・型不一致)は `JevOutputInvalidError("jev answers invalid")` を送出。メッセージにIntent本文・応答値を入れない
- **provider/modelはresult部に含めない**(GatewayがJevJudgmentへ載せる)
- **`JevOutputInvalidError` は `provider` 属性を持つ**(実装定義 — Layer 4の内訳計上のため):

```python
class JevOutputInvalidError(LLMError):
    """出力検証失敗(07 §4)。再試行も切替もしない(実装不整合として扱う)。"""

    def __init__(self, msg: str, *, provider: str | None = None) -> None:
        super().__init__(msg)
        self.provider = provider  # 検証対象の経路("typesafe_jev" | "fallback_llm")
```

  `validate_and_normalize` は `JevOutputInvalidError("jev answers invalid")`(provider=None)を送出し、Gatewayが捕まえて `raise JevOutputInvalidError(str(exc), provider="typesafe_jev") from exc` の形で経路を設定して再送出する(Task 5)。Layer 4は `exc.provider` をrecord_executionへ渡す(design §2.5-3「実際のAPI呼び出しベースで計上」の検証失敗版)

### 9-5. Gateway切替(llm/gateway.py)

- `LLMGateway.__init__` に `jev_fallback: JevProvider | None = None` を追加。`self._jev_fallback = jev_fallback if jev_fallback is not None else jev`(**未注入時は第一候補と同一** — 既存5箇所のLLMGateway直構築試験を無変更で通すための既定。real構成ではbuild_worker_gatewayが常に明示渡しする)
- `judge_pair` の戻り値型を `dict` → `JevJudgment` へ変更。ロジック:

```python
async def judge_pair(self, *, intent_a: str, intent_b: str, intent_ids: list[str]) -> JevJudgment:
    try:
        envelope = await self._jev_call(intent_a, intent_b, intent_ids, fallback=False)
        result = validate_and_normalize(envelope)
        return JevJudgment(provider="typesafe_jev", model=_envelope_model(envelope), result=result)
    except (LLMTimeoutError, LLMRateLimitError, LLMOverloadedError, LLMConnectionError):
        pass  # 07 §4の切替条件4種。LLMProviderError・JevOutputInvalidErrorは切替せず伝播
    envelope = await self._jev_call(intent_a, intent_b, intent_ids, fallback=True)
    result = validate_and_normalize(envelope)
    return JevJudgment(provider="fallback_llm", model=None, result=result)

async def _jev_call(self, intent_a: str, intent_b: str, intent_ids: list[str], *, fallback: bool) -> dict:
    provider = self._jev_fallback if fallback else self._jev
    return await self._call(
        system="jev", destination=provider.name,
        timeout_s=self._timeouts.jev_s, intent_ids=intent_ids, user_id=None,
        invoke=lambda: provider.judge(intent_a, intent_b),
    )

def _envelope_model(envelope: object) -> str | None:
    model = envelope.get("model") if isinstance(envelope, dict) else None
    return str(model) if model is not None else None
```

- circuit breaker(ws-8)の注入ポイントは `_jev_call` の第一候補側(`fallback=False` の呼び出し)。本単位では実装しない
- 送信記録: 第一候補・フォールバックの各呼び出しが既存 `_call` を通るため、切替時は2件(destination=typesafe→anthropicまたはstub)・timeout/error/okのstatusが各々記録される

### 9-6. TypeSafeJevProvider(llm/typesafe.py)・AnthropicJevFallbackProvider(llm/anthropic_jev.py)

```python
# typesafe.py(例外翻訳はjudge()内。メッセージに本文を入れない)
TYPESAFE_JEV_TIMEOUT_S = 6.0   # TIMEOUT_JEV_Sと同値(unit試験が同値性を強制)
TYPESAFE_JEV_RETRIES = 0       # T5: RetryPolicy(max_retries=0)相当

class TypeSafeJevProvider(JevProvider):
    """第一候補(07 §4)。name="typesafe"(08 §3送信記録)。"""

    def __init__(self, *, api_key: str, base_url: str = "https://api.typesafe.ai", client=None):
        if not api_key:
            raise ValueError("TypeSafeJevProvider requires api_key")  # fail-fast
        self.name = "typesafe"
        self._api_key = api_key
        self._base_url = base_url
        self._client = client  # Noneならjudge()内で都度構築(async context manager)

    async def judge(self, intent_a: str, intent_b: str) -> dict:
        client = self._client if self._client is not None else self._build_client()
        try:
            async with client as c:
                resp = await c.system_one(
                    state={"intent_a": intent_a, "intent_b": intent_b},
                    model=JEV_MODEL,
                    questions=JEV_QUESTIONS,
                )
        except TypeSafeRateLimitError as exc:
            raise LLMRateLimitError("typesafe rate limited") from exc
        except TypeSafeAPIConnectionError as exc:
            raise LLMConnectionError("typesafe connection failed") from exc
        except TypeSafeAPIError as exc:
            if getattr(exc, "status", None) == 529:
                raise LLMOverloadedError("typesafe overloaded") from exc
            raise LLMProviderError("typesafe provider failed") from exc
        return _envelope(resp)  # model/answers/usage をdictへ正規化(9-7)
```

- `_build_client()` はモジュール内1関数: `AsyncTypeSafeClient(api_key=self._api_key, base_url=self._base_url, timeout=TYPESAFE_JEV_TIMEOUT_S, <retry引数>=RetryPolicy(max_retries=TYPESAFE_JEV_RETRIES))`。**retry引数名とコンストラクタ署名はTask 2でSDK実装を確認して实名で渡す**(design §5-4・既定max_retries=2無効化)
- `_envelope(resp)`: SDK応答オブジェクトを `{"model": resp.model, "answers": <answersのdict化>, "usage": {"input_tokens": ..., "output_tokens": ...}}` へ。answersはdict/オブジェクト両対応ヘルパでそのまま通す(検証はvalidate_and_normalizeが担う)。SDK応答がdictを返す場合もこの関数は同一

```python
# anthropic_jev.py
ANTHROPIC_JEV_FALLBACK_MODEL = "claude-sonnet-5"
ANTHROPIC_JEV_FALLBACK_TIMEOUT_S = 6.0   # TIMEOUT_JEV_Sと同値(unit試験が同値性を強制)
ANTHROPIC_JEV_FALLBACK_MAX_TOKENS = 512  # design §5-8(実装定義初期値)
ANTHROPIC_JEV_BASE_URL = "https://api.anthropic.com"  # anthropic.pyのANTHROPIC_PARSER_BASE_URLと同値(unit試験が同値性を強制)

FALLBACK_SYSTEM_PROMPT = (
    "あなたは2つのIntentペアの相互受け入れ可能性を評価する判定器です。"
    "入力される2つのIntent(正規化テキスト)に対し、次の7つの質問に答えるJSONのみを"
    "出力します。JSON以外の文章・説明・根拠文は一切出力しません。\n"
    "\n"
    "質問と値域:\n"
    "- would_a_accept_b: Intent Aの作成者の立場でBとの成立にyesと答える確率(0〜1の数値)\n"
    "- would_b_accept_a: Intent Bの作成者の立場でAとの成立にyesと答える確率(0〜1の数値)\n"
    "- purpose_fit: 目的・カテゴリの適合度(0〜4の数値)\n"
    "- mood_fit: 雰囲気・軽さの適合度(0〜4の数値)\n"
    "- timing_fit: 時間帯・所要時間の適合度(0〜4の数値)\n"
    "- social_fit: 人数・社会的文脈の適合度(0〜4の数値)\n"
    "- latent_yes: どちらかが明示していないが、そのIntentの記述の範囲内でYESになり得る"
    "可能性(0〜1の数値)\n"
    "\n"
    "判定規則:\n"
    "- would_*は相手側の条件も考慮し、相手の[hard]条件を満たさない場合、または相手の"
    "[soft]条件・(システムで判定不能)と付いた条件に触れる場合はyesから遠ざけます\n"
    "- [hard]条件との意味的矛盾(予算感の著しい乖離が食事内容を成立させない等)は"
    "yesから遠ざけます\n"
    "- 根拠の説明は出力しません(値のみ)\n"
)
```

- スキーマはpydanticモデル `FallbackJevAnswers`(`would_a_accept_b: float` / `would_b_accept_a: float` / `purpose_fit: float` / `mood_fit: float` / `timing_fit: float` / `social_fit: float` / `latent_yes: float`・**min/max等の数値制約を付けない** — A3 `_STRIP_KEYS` の知見どおり)から `adapt_schema_for_anthropic(FallbackJevAnswers.model_json_schema())` で導出(単一の真実・機械的後加工のみ)
- `judge()`:

```python
async def judge(self, intent_a: str, intent_b: str) -> dict:
    response = await self._client.messages.create(
        model=ANTHROPIC_JEV_FALLBACK_MODEL,
        system=FALLBACK_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"{intent_a}\n\n{intent_b}"}],
        output_config={"format": {"type": "json_schema", "schema": self._schema}},
        # sampling系(temperature/top_p/top_k)は廃止のため送らない(A2: 400)
        thinking={"type": "disabled"},
        max_tokens=ANTHROPIC_JEV_FALLBACK_MAX_TOKENS,
    )
    first_text = next(block.text for block in response.content if block.type == "text")
    data = json.loads(first_text)
    return {
        "model": None,  # 引用#8: フォールバック時のmodelはnull
        "answers": {
            "would_a_accept_b": {"type": "noul", "noul": data["would_a_accept_b"]},
            "would_b_accept_a": {"type": "noul", "noul": data["would_b_accept_a"]},
            "latent_yes": {"type": "noul", "noul": data["latent_yes"]},
            "purpose_fit": {"type": "score", "score": data["purpose_fit"], "confidence": None},
            "mood_fit": {"type": "score", "score": data["mood_fit"], "confidence": None},
            "timing_fit": {"type": "score", "score": data["timing_fit"], "confidence": None},
            "social_fit": {"type": "score", "score": data["social_fit"], "confidence": None},
        },
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
    }
```

- コンストラクタ: `AnthropicJevFallbackProvider(*, api_key, base_url=ANTHROPIC_JEV_BASE_URL, client: AsyncAnthropic | None = None)`・空鍵は `ValueError("AnthropicJevFallbackProvider requires api_key")`(fail-fast)。AsyncAnthropicは `api_key`・`base_url`・`timeout=ANTHROPIC_JEV_FALLBACK_TIMEOUT_S`・`max_retries=0`(A4)
- SDK例外は翻訳せず素通り(Gatewayの既存wrapと送信記録が最終関門 — anthropic.pyと同一規律)

### 9-7. Stub envelope(llm/stub.py・DEFAULT_JEV_RESPONSE置換)

```python
# 10 §1 v0.4: System One互換のanswers形式(決定的固定値)
DEFAULT_JEV_RESPONSE: dict = {
    "model": "jev-1.13.0",
    "answers": {
        "would_a_accept_b": {"type": "noul", "noul": 0.5},
        "would_b_accept_a": {"type": "noul", "noul": 0.5},
        "latent_yes": {"type": "noul", "noul": 0.5},
        "purpose_fit": {"type": "score", "score": 2.0, "confidence": 0.5},
        "mood_fit": {"type": "score", "score": 2.0, "confidence": 0.5},
        "timing_fit": {"type": "score", "score": 2.0, "confidence": 0.5},
        "social_fit": {"type": "score", "score": 2.0, "confidence": 0.5},
    },
    "usage": {"input_tokens": 0, "output_tokens": 0},
}
```

- コンストラクタ引数 `jev_response` はdictのまま(envelope形式を渡す)。`fail_jev` はLLMProviderError送出のまま(llm_failure経路の試験に使用)
- フォールバック側のスタブはconfidenceをnullにしたenvelopeを渡す(10 §1どおり)。`build_worker_gateway` stub時は同一インスタンスを第一候補・フォールバックへ渡す(stubは切替可能な例外を出さないため問題ない — design §2.8)

### 9-8. 再選択規則と選択SQL(worker/matching/layer4.py)

```python
K_J = 8             # 06 §8 D-24(1イベント処理あたりのJev実行上限)
ONE_ON_ONE_MIN = 4  # 1対1最低保証(引用#11)
PAIR_KIND_ONE_ON_ONE = "one_on_one"  # ws-7がグループ種別を追加する拡張点

# deny理由のうち予算系=期間切れ後・障害系=即時(design §2.7)
RESELECT_ALWAYS = ("llm_failure", "invalid_output")
RESELECT_DAILY = ("intent_daily", "user_daily", "global_daily")
RESELECT_MONTHLY = ("global_monthly",)

_SELECT_JEV_ROWS = text("""
    SELECT mc.id, mc.intent_a_id, mc.intent_b_id, mc.intent_a_version,
           mc.intent_b_version, mc.cheap_judge_score, mc.status, mc.skip_reason
    FROM match_candidates mc
    WHERE (mc.intent_a_id = CAST(:origin AS uuid)
           OR mc.intent_b_id = CAST(:origin AS uuid))
      AND (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                THEN mc.intent_a_version
                ELSE mc.intent_b_version END) = :origin_version
      AND mc.jev_result IS NULL
      AND (
        mc.status = 'pending'
        OR (mc.status = 'skipped'
            AND mc.skip_reason IN ('llm_failure', 'invalid_output'))
        OR (mc.status = 'skipped'
            AND mc.skip_reason IN ('intent_daily', 'user_daily', 'global_daily')
            AND mc.updated_at < CAST(:jst_day_start AS timestamptz))
        OR (mc.status = 'skipped' AND mc.skip_reason = 'global_monthly'
            AND mc.updated_at < CAST(:jst_month_start AS timestamptz))
      )
    ORDER BY mc.cheap_judge_score DESC,
             CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                  THEN mc.intent_b_id
                  ELSE mc.intent_a_id END ASC
    LIMIT 8
""")
```

- LIMIT 8は `K_J` と同値(二重防御)。SQL文字列ではリテラル8、純関数でK_J再適用
- `JevCandidateRow`(frozen dataclass): `row_id: uuid.UUID` / `intent_a_id: uuid.UUID` / `intent_b_id: uuid.UUID` / `intent_a_version: int` / `intent_b_version: int` / `cheap_judge_score: float | None` / `status: str` / `skip_reason: str | None` / `pair_kind: str = PAIR_KIND_ONE_ON_ONE`(SELECT結果から組立・asyncpgのUUIDは`_coerce_uuid`規律で復元)
- `select_jev_rows(conn, origin_id, origin_version, day_start, month_start) -> list[JevCandidateRow]`(Python引数名は `day_start`/`month_start`〔モジュール関数jst_day_start/jst_month_startとの衝突回避〕。SQLのbind param名は `jst_day_start`/`jst_month_start` のまま)
- `jst_day_start(d: date) -> datetime`: `datetime.combine(d, time(0, 0), tzinfo=JST)` / `jst_month_start(d: date) -> datetime`: `datetime.combine(d.replace(day=1), time(0, 0), tzinfo=JST)`(JST境界はClock.jst_date()から導出 — C2)
- `select_jev_targets(rows: list[JevCandidateRow]) -> list[JevCandidateRow]`: `return [r for r in rows if r.pair_kind == PAIR_KIND_ONE_ON_ONE][:K_J]` — ws-5時点で入力は全て1対1のため恒等操作+上限再適用。ws-7が規則(1)〜(4)をこの関数の拡張として追加する
- H再検証(1対1判定):

```python
_H_RECHECK = text(f"""
    SELECT 1 FROM intents i JOIN users u ON u.id = i.user_id
    WHERE i.id = CAST(:candidate_id AS uuid)
      AND {LAYER1_WHERE}
    LIMIT 1
""")

async def hard_constraint_holds(conn, origin, candidate_id: uuid.UUID) -> bool:
    params = bind_params(origin)
    params["candidate_id"] = candidate_id
    row = (await conn.execute(_H_RECHECK, params)).first()
    return row is not None
```

- H不成立行の一括close(同一バージョン組のevaluated行のみ・pending行は閉じない):

```python
_CLOSE_BROKEN = text(f"""
    UPDATE match_candidates mc
    SET status = 'closed', updated_at = :now
    WHERE (mc.intent_a_id = CAST(:origin AS uuid)
           OR mc.intent_b_id = CAST(:origin AS uuid))
      AND (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                THEN mc.intent_a_version
                ELSE mc.intent_b_version END) = :origin_version
      AND mc.jev_result IS NOT NULL
      AND mc.status = 'evaluated'
      AND NOT EXISTS (
        SELECT 1 FROM intents i JOIN users u ON u.id = i.user_id
        WHERE i.id = (CASE WHEN mc.intent_a_id = CAST(:origin AS uuid)
                           THEN mc.intent_b_id
                           ELSE mc.intent_a_id END)
          AND {LAYER1_WHERE}
      )
    RETURNING mc.id
""")

async def close_broken_pairs(conn, origin, now) -> int:
    params = bind_params(origin)
    params["origin"] = origin.intent_id
    params["origin_version"] = origin.version
    params["now"] = now
    rows = (await conn.execute(_CLOSE_BROKEN, params)).all()
    return len(rows)
```

- `bind_params(origin)` には既に `"now"`(=origin.evaluated_at)が含まれる。`close_broken_pairs(conn, origin, now)` の `now` 引数は呼び出し側(Task 9)が `org.evaluated_at` を渡すため、`params["now"] = now` は同一値の再設定となり害はない(コード例どおりでよい)

### 9-9. JevWorker(worker/jev.py)

```python
_SKIP_REASON_LLM_FAILURE = "llm_failure"
_SKIP_REASON_INVALID_OUTPUT = "invalid_output"

# 評価結果のガード付きUPDATE(冪等: jev_result IS NULL が二重排除)
_COMPLETE = text("""
    UPDATE match_candidates
    SET jev_result = CAST(:jev AS jsonb), status = 'evaluated',
        skip_reason = NULL, updated_at = :now
    WHERE id = CAST(:row_id AS uuid) AND jev_result IS NULL
    RETURNING id
""")
# deny・LLM失敗・検証失敗の共通skip(jev_resultはNULLのまま)
_SKIP = text("""
    UPDATE match_candidates
    SET status = 'skipped', skip_reason = :reason, updated_at = :now
    WHERE id = CAST(:row_id AS uuid) AND jev_result IS NULL
    RETURNING id
""")
# 相手Intent(または起点)のjev入力列読取(version照合付き)
_SELECT_PEER = text("""
    SELECT version, category_primary, structured_data, participants_min,
           participants_max, time_start, time_end, budget_max, geo_radius_m
    FROM intents WHERE id = CAST(:intent_id AS uuid)
""")
```

- `JevWorker(*, engine, clock, gateway, guard, cost_store)` — `guard: JevCostGuard`・`cost_store: JevCostStore`
- `handle(intent_id: uuid.UUID) -> None`:
  1. **フェーズ1(短tx)**: `origin.load_origin`(既存・再利用)→ skip_reasonありなら構造化ログを出してno-op return。`close_broken_pairs(conn, org, org.evaluated_at)` → `jst_day_start/jst_month_start(clock.jst_date())` を渡して `select_jev_rows` → `select_jev_targets(rows)`。起点が読み取れない(削除・非active・embedding NULL等)場合はno-op(構造化ログ)。起点のversionガードは選択SQLの「行の起点version == 現在version」条件で実現される(handleにversion引数はなく・旧世代行は選ばれない)
  2. **フェーズ2+3(ペア毎・直列)**: 各行について `_evaluate(org, row)`:
     a. 相手intent_id=CASE論理のPython版(`row.intent_a_id == candidate_id なら a,b の対応から` — originがaかbかで正規化順を解決)
     b. `_SELECT_PEER` で相手を読み `version == row の相手version` を照合 → **不一致なら何もせず次の行へ**(新世代行が別Eventで作られる。旧世代行はjev_result保持のまま)
     c. `hard_constraint_holds(conn, org, candidate_id)` → Falseなら **評価せず次の行へ**(pendingのまま・Guardも呼ばない — 実行しないものは課税しない)
     d. `guard.request_execution(org.intent_id, org.user_id)`(起点側のみ課税・承認済み解釈記録)→ deny時: `_skip(row_id, deny_reason)` + **record_executionは呼ばない**(API呼び出しなし)→ 次の行へ。**補充しない**(§2.5-4)= 選択はSELECT時点でK_j件に確定しており、denyが出ても順位を繰り上げて枠外の行を追加しない。選択済みの残り行はそのまま消化する(後続もdenyになる場合は全行がskipped記録される — 記録の可視性を優先・INCRは選択済み件数分で有限)
     e. build_jev_text ×2(起点側と相手側。labelは正規化順に"Intent A"/"Intent B")→ `gateway.judge_pair(intent_a=…, intent_b=…, intent_ids=[str(a_id), str(b_id)])`
     f. 成功: `_complete(row_id, {**judgment.result, "provider": judgment.provider, "model": judgment.model})` → `cost_store.record_execution(judgment.provider, day)`(day=`clock.jst_date().strftime("%Y%m%d")`)
     g. `JevOutputInvalidError`: 計上(`cost_store.record_execution(exc.provider, day)`)→ `_skip(row_id, "invalid_output")` → 次の行へ(例外にしない)
     h. その他の `LLMError`(双障害): 計上(`cost_store.record_execution("fallback_llm", day)`)→ `_skip(row_id, "llm_failure")` → 次の行へ
     i. **計上はUPDATE成功後に1回・最終経路のみ**(ペア評価あたり1回。「実際のAPI呼び出しベースで切替完了側が呼ぶ」の実体・design §2.5-3。UPDATEが競合負け[RETURNING無]しても計上は行う — API呼び出しは発生しておりat-least-once受容)
  3. **例外方針(design §2.9の表)**: JevCostDependencyError・DB書き込み失敗は伝播(fail-closed→_dispatchの既存except→ackなし再配信→duplicate経由で_kick_jev再実行。冪等ガードで完了分は飛ばす)。LLM失敗・検証失敗・denyは例外にせずskipped記録
- `_complete`/`_skip` は短tx(`engine.begin()`)・`RETURNING id` がNoneなら競合負けとして何もしない
- JevTextInputの組立て: 起点側は `origin.category_primary / origin.participants_min / origin.participants_max / origin.time_start / origin.time_end(補完済み)` + **`_SELECT_PEER` と同一SQLで起点の `structured_data / budget_max / geo_radius_m` を追加読取**(origin.load_originがstructured_data全体をOriginへ載せないため・origin.pyは変更禁止 — §5)。相手側は `_SELECT_PEER` の行から。`structured_data` がstrで返る場合は `json.loads`(embedding.pyと同一規律)

### 9-10. ピン試験の「ちょうどN項目」対処

- `tests/unit/llm/test_llm_factory.py` の `test_llm_settings_are_exactly_seven_fields` は **`test_llm_settings_are_exactly_nine_fields` へ改名**し、期待値セットへ `llm_typesafe_api_key`・`llm_typesafe_base_url` を追加する(9項目)。`LLM_ENV_VARS` タプルへ `"LATCH_TYPESAFE_API_KEY"`・`"LATCH_TYPESAFE_BASE_URL"` を追加する
- 他の「ちょうどN項目」ピン(geo・auth・send_record)はprefix違いで非干渉(§0で機械確認済み)

### 9-11. マイグレーション0003

```python
"""match_candidatesへskip_reason列を追加(M2 ws-5・design §2.7)。

deny理由(design §2.4-2の列挙)とD-15の回収経路区別(予算系=期間切れ後・
障害系=即時)をupdated_atだけで実装できないための列追加。値域は
intent_daily / user_daily / global_daily / global_monthly / llm_failure /
invalid_output(小文字スネーク・小文字固定)。evaluated・closed・pending行はNULL。
05 §2への追記は次回docs改版に含める(ws-3のlocation_name前例)。

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE match_candidates ADD COLUMN skip_reason text NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE match_candidates DROP COLUMN skip_reason")
```

- 0001・0002と同じ `op.execute` 形式・`CREATE/DROP` のみ・DB時刻関数のDEFAULTなし

### 9-12. httpx直実装代替(design §5-3・設計不変の予備経路)

`uv add <sdk名>` でtypesafe-sdkが取得できない場合(PyPI名の確定失敗・import不整合等):

- **実装を `llm/typesafe.py` 内でhttpx直呼び出しに切り替える**(設計は不変 — APIはRESTで完全規定済み: design §1.3 T1〜T3)
  - `judge()`: `POST {base_url}/v1/systemone`・`Authorization: Bearer <api_key>`・`Content-Type: application/json`・body `{"state": {...}, "model": JEV_MODEL, "questions": JEV_QUESTIONS}`・httpx.AsyncClient(timeout=6.0)をasync withで
  - 例外分類はHTTPステータスから直接行う: 429→`LLMRateLimitError` / 529→`LLMOverloadedError` / 400・その他4xx/5xx→`LLMProviderError` / 接続エラー・タイムアウト(`httpx.TimeoutException`含む)→`LLMConnectionError`(Gatewayのasyncio.timeout(6秒)が先に効くため通常はLLMTimeoutError側)
  - `_build_client()` 相当を `_http_client()` に差し替えるのみで、テストの注入点(`client` 引数)・Gateway・Layer 4は無変更
- この場合は報告書の「design §5 実装時確認事項の結果」に理由と実装差分を記録する

### 9-13. build_worker_gateway(llm/gateway.py・改名拡張)

```python
def build_worker_gateway(clock: Clock, settings: Settings) -> LLMGateway:
    """Worker・スモーク用のGateway構築(M2 ws-5・design §2.8)。

    llm_mode="stub": 3系統+jev_fallbackすべてStubLLM(同一インスタンス)。
    llm_mode="real": Embedding系統=GeminiEmbeddingProvider・Jev系統=
    TypeSafeJevProvider・フォールバック=AnthropicJevFallbackProvider
    (llm_anthropic_api_keyはparserと共用)。parser系統はstub継続
    (APIプロセスのbuild_llm_gateway契約は無変更)。鍵の欠落はfail-fast
    (静かにスタブへ落ちない)。
    """
    stub = StubLLM(
        delay_parser_ms=settings.llm_stub_delay_parser_ms,
        delay_embedding_ms=settings.llm_stub_delay_embedding_ms,
        delay_jev_ms=settings.llm_stub_delay_jev_ms,
    )
    if settings.llm_mode == "stub":
        return LLMGateway(clock=clock, parser=stub, embedding=stub, jev=stub, jev_fallback=stub)
    if settings.llm_mode == "real":
        if not settings.llm_gemini_api_key:
            raise ValueError("llm_mode='real' requires llm_gemini_api_key (embedding gateway)")
        if not settings.llm_typesafe_api_key:
            raise ValueError("llm_mode='real' requires llm_typesafe_api_key (jev gateway)")
        if not settings.llm_anthropic_api_key:
            raise ValueError("llm_mode='real' requires llm_anthropic_api_key (jev fallback)")
        embedding = GeminiEmbeddingProvider(api_key=settings.llm_gemini_api_key)
        jev = TypeSafeJevProvider(
            api_key=settings.llm_typesafe_api_key,
            base_url=settings.llm_typesafe_base_url,
        )
        jev_fallback = AnthropicJevFallbackProvider(
            api_key=settings.llm_anthropic_api_key,
            base_url=settings.llm_anthropic_base_url,
        )
        return LLMGateway(clock=clock, parser=stub, embedding=embedding, jev=jev, jev_fallback=jev_fallback)
    raise ValueError(f"unknown llm_mode: {settings.llm_mode!r} ('stub' or 'real')")
```

- 旧 `build_embedding_gateway` は削除(改名)。`llm/__init__.py`・`worker/main.py`・`llm/embed_smoke.py`・`tests/unit/test_llm_gemini.py` を§4のとおり追随させる

### 9-14. Worker配線(worker/main.py)

- import変更: `from latch.llm.gateway import build_embedding_gateway` → `from latch.llm.gateway import build_worker_gateway`(Task 6で実施済みの置換)。Task 10での追加import: `from latch.worker.jev import JevWorker`・`from latch.worker.cost import JevCostGuard, JevCostStore`・`from latch.intents.events import EVENT_EMBEDDING_COMPLETED`
- `Worker.__init__` に `jev: JevWorker | None = None` を追加・`self._jev = jev`
- `run()`: gateway構築を `build_worker_gateway(self.clock, self.settings)` に1本化(embeddingとJevWorkerで同一インスタンス)。redis_clientが構築された場合(own_redis)に:

```python
if self._jev is None and redis_client is not None:
    cost_store = JevCostStore(redis_client)
    self._jev = JevWorker(
        engine=engine, clock=self.clock, gateway=gateway,
        guard=JevCostGuard(store=cost_store, clock=self.clock),
        cost_store=cost_store,
    )
```

- `_kick_jev`(embeddingと対称・コミット後ack前):

```python
async def _kick_jev(self, event_type: str, intent_id: uuid.UUID, version: int) -> None:
    """Stage1処理コミット後・ack前のLayer 4キック(design §2.1案B)。

    embedding_completedのみ(06 §1「Layer 1〜5はembedding_completed起点」)。
    JevLLM失敗はhandle内でskipped記録に変換し、DB失敗・Guard Redis失敗は
    ここから伝播して_dispatch/_on_releaseの既存exceptが受け、ackなし
    再配信が回収する(冪等ガード jev_result IS NULL)。Jev未注入(ws-1資産
    の試験)は何もしない。version引数はhandleが起点読取で再検証するため
    使わない(IFは起点非依存 — ws-6がbucket起点から呼ぶ)。
    """
    if self._jev is None or event_type != EVENT_EMBEDDING_COMPLETED:
        return
    await self._jev.handle(intent_id)
```

- `_dispatch` のprocessed/duplicate経路は `_kick_embedding(*result.triple)` に続けて `await self._kick_jev(*result.triple)` を追加(embedding_completedでは_kick_embeddingがevent_typeフィルタでno-op・created/updatedでは_kick_jevがno-opになる対称構造)。`_on_release`(debounce=created/updatedのみ)には追加しない

## 8. 実装ステップ(TDD。Task 1〜12の順で実行する)

### Task 1: llm/jev.py(System One共通の定数・純関数・JevJudgment)

**Files:**
- Create: `backend/src/latch/llm/jev.py`
- Test: `backend/tests/unit/llm/test_jev_format.py`

**Interfaces:**
- Consumes: なし(標準ライブラリのみ。外部SDKに依存しない)
- Produces(Task 2〜6・9が使用 — §9-1〜9-4のとおり):
  - `JEV_MODEL = "jev-1.13.0"`・`JEV_QUESTIONS: dict`(7質問のdict定数)
  - `NOUL_KEYS` / `SCORE_KEYS` / `JEV_RESULT_KEYS` / `SCORE_MAX_LEVEL = 4`
  - `JevTextInput`(frozen dataclass: category_primary / structured_data / participants_min / participants_max / time_start / time_end / budget_max / geo_radius_m)
  - `build_jev_text(inp: JevTextInput, *, label: str) -> str`
  - `validate_and_normalize(envelope: object) -> dict`(失敗は `JevOutputInvalidError`)
  - `JevJudgment(provider: str, model: str | None, result: dict)`(frozen dataclass)
  - `_field(answer, key)`(dict/オブジェクト両対応ヘルパ)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/llm/test_jev_format.py` を作成。試験項目(design §4.1・すべて決定的):

1. **JEV_QUESTIONS全文ピン**(dict全体を期待値と完全一致させる — PARSER_SYSTEM_PROMPTと同一規律・変更検知):
   `assert JEV_QUESTIONS == {…07 §4実装イメージ全文(§9-2のdict)…}`。加えて構造ピン: `set(JEV_QUESTIONS) == JEV_RESULT_KEYS`・`would_*` 3キーのうち2つはtype="noul"でcriteriaがマップ・4軸はtype="score"でcriteriaが長さ5の配列・`latent_yes` はinstructionsのみ・`JEV_MODEL == "jev-1.13.0"`
2. **build_jev_text: 07 §4例の再現**(固定入力→全文一致):

```python
"""llm/jev.py(正規化テキスト・出力検証・質問定数)のunit試験(design §4.1)。"""

from datetime import UTC, datetime, timedelta

import pytest

from latch.llm.errors import JevOutputInvalidError
from latch.llm.jev import (
    JEV_MODEL,
    JEV_QUESTIONS,
    JEV_RESULT_KEYS,
    SCORE_KEYS,
    JevTextInput,
    build_jev_text,
    validate_and_normalize,
)

T0 = datetime(2026, 9, 26, 11, 0, 0, tzinfo=UTC)  # JST 2026-09-26 20:00
T1 = T0 + timedelta(hours=3)


def _inp(**over) -> JevTextInput:
    base = dict(
        category_primary="drinking",
        structured_data={
            "location_name": "天文館周辺",
            "soft_constraints": [
                {"text": "軽く飲みたい", "downgraded_from_ng": False},
                {"text": "会社関係の人は避けたい", "downgraded_from_ng": True},
            ],
        },
        participants_min=2,
        participants_max=4,
        time_start=T0,
        time_end=T1,
        budget_max=5000,
        geo_radius_m=2000,
    )
    base.update(over)
    return JevTextInput(**base)


def test_build_jev_text_reproduces_07_example():
    assert build_jev_text(_inp(), label="Intent A") == (
        "Intent A:\n"
        "[hard] category: drinking\n"
        "[hard] time: 2026-09-26 20:00–23:00\n"
        "[hard] location: 天文館周辺 半径2km\n"
        "[hard] participants: 2–4人\n"
        "[hard] budget_max: 5000円\n"
        "[soft] 軽く飲みたい\n"
        "[soft] 会社関係の人は避けたい(システムで判定不能)"
    )
```

3. **build_jev_textの行規則ピン**(§9-3): budget_max=Noneでbudget行のみ消失 / location_name=""でlocation行消失 / geo_radius_m=1500で`半径1500m`・500で`半径500m` / participants 2,2で`2人` / time_start=Noneでtime行消失 / soft_constraints空なら[soft]行なし / label "Intent B"で1行目が`Intent B:`
4. **raw_textを保持しない構造ピン**: `dataclasses.fields(JevTextInput)` の名前集合に `raw_text`・`visibility`・`notification_level`・`alcohol_involved`・`category_secondary` が含まれないこと
5. **validate_and_normalize 正常系**(System One応答形式):

```python
def _envelope(**over) -> dict:
    answers = {
        "would_a_accept_b": {"type": "noul", "noul": 0.83},
        "would_b_accept_a": {"type": "noul", "noul": 0.71},
        "latent_yes": {"type": "noul", "noul": 0.40},
        "purpose_fit": {"type": "score", "score": 3.0, "confidence": 0.8},
        "mood_fit": {"type": "score", "score": 2.0, "confidence": 0.6},
        "timing_fit": {"type": "score", "score": 2.0, "confidence": 0.7},
        "social_fit": {"type": "score", "score": 3.0, "confidence": 0.6},
    }
    answers.update(over.pop("answers", {}))
    return {"model": "jev-1.13.0", "answers": answers, "usage": {}}


async def test_normalize_returns_result_part():
    result = validate_and_normalize(_envelope())
    assert result["would_a_accept_b"] == 0.83
    assert result["would_b_accept_a"] == 0.71
    axis = result["jev_5axis"]
    assert axis["purpose_fit"] == {"value": 0.75, "confidence": 0.8}  # 3.0/4
    assert axis["latent_yes"] == {"value": 0.40, "confidence": None}  # noulに付かない
    assert set(result) == {"would_a_accept_b", "would_b_accept_a", "jev_5axis"}
    assert "provider" not in result and "model" not in result  # Gatewayが載せる
```

6. **score境界**(0・4・1.035のfloat): score=0→value=0.0・score=4→value=1.0・score=1.035→value=1.035/4。score=4.1はJevOutputInvalidError
7. **異常系全種**(各々JevOutputInvalidError): answersキー欠損 / 余分キー / noul=1.1(値域外) / noul=-0.01 / score=-0.1 / 型不一致(文字列"0.5") / bool(True は実数として除外) / answersがdictでない / envelopeがdictでない / confidence=1.1(値域外) / confidence="x"
8. **フォールバック系envelope**: 全confidence=Noneでも検証通過(値が入るのみ)
9. **dictでないanswer(オブジェクト)**: `SimpleNamespace(noul=0.5, score=2.0, confidence=None)` のようなオブジェクトanswerも `_field` 経由で検証通過(型両対応)

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_jev_format.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'latch.llm.jev'`

- [ ] **Step 3: 実装する**

`backend/src/latch/llm/jev.py` を作成する。内容は§9-2(JEV_MODEL・JEV_QUESTIONS)・§9-3(JevTextInput・build_jev_text)・§9-4(定数・JevJudgment・validate_and_normalize・_field)のとおり。`JevOutputInvalidError` はTask 4で `llm/errors.py` に追加するが、import解決のため**本タスクで先行追加する**(errors.pyへ `JevOutputInvalidError(LLMError)` の1行を追加するのみ — Task 4の残り3例外はTask 4で追加)。docstringには出典(07 §4・design §2.3〜§2.4)を書く。

- [ ] **Step 4: 実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_jev_format.py -v`
Expected: PASS(全項目)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/llm/jev.py backend/src/latch/llm/errors.py backend/tests/unit/llm/test_jev_format.py
git commit -m "feat: llm/jev.py System One共通部品(質問定数・正規化テキスト・出力検証)"
```

### Task 2: typesafe-sdk依存追加と llm/typesafe.py(TypeSafeJevProvider)

**Files:**
- Modify: `backend/pyproject.toml`(+`backend/uv.lock` — `uv add` が更新)
- Create: `backend/src/latch/llm/typesafe.py`
- Test: `backend/tests/unit/llm/test_typesafe_jev.py`

**Interfaces:**
- Consumes: `JEV_MODEL`・`JEV_QUESTIONS`(Task 1)
- Produces(Task 6のbuild_worker_gatewayが使用):
  - `TypeSafeJevProvider(JevProvider)`: `name="typesafe"`・`__init__(*, api_key, base_url="https://api.typesafe.ai", client=None)`・`judge(intent_a, intent_b) -> dict`(System One envelope)
  - 定数: `TYPESAFE_JEV_TIMEOUT_S = 6.0`・`TYPESAFE_JEV_RETRIES = 0`

- [ ] **Step 1: SDK取得と実装時確認(design §5-3・§5-4)**

context7 `/websites/typesafe_ai` のsdk/python頁を再確認し、次を確定する:
1. PyPIパッケージ名(docs上のimport名は `typesafe_sdk`・pip名は別の可能性 — **実名を `uv add` で確認**)。取得不能なら **§9-12のhttpx直実装に切り替える**(本タスクのStep 3のjudge()実装を入れ替えるのみ・テストは同一で通るようにclient注入点を維持)。実名とバージョンを報告書に記録する
2. `AsyncTypeSafeClient` のコンストラクタ署名(timeout・retry または retry_policy・base_url)と `system_one()` の返却型(dict か dataclass か)。retry無効化は `RetryPolicy(max_retries=0)`(T5)

Run: `cd backend && uv add <確定したパッケージ名>`
Expected: 依存追加が完了する(pyproject.tomlのdependenciesへ1行・uv.lock更新)

- [ ] **Step 2: 失敗するテストを書く**

`backend/tests/unit/llm/test_typesafe_jev.py` を作成。試験項目(design §4.1):

```python
"""TypeSafeJevProviderの契約ピン(design §4.1・§1.3 T1〜T7)。

client注入スタブで実APIなしに検証する。実機挙動(応答形式・noulのJSON形状)は
make jev-smoke(スーパーバイザー実行)が初回検証する — design §5-7
(embed-smokeの401事故と同じ位置づけ)。
"""

from types import SimpleNamespace

import pytest

from latch.llm.errors import (
    LLMConnectionError,
    LLMOverloadedError,
    LLMProviderError,
    LLMRateLimitError,
)
from latch.llm.jev import JEV_MODEL, JEV_QUESTIONS
from latch.llm.typesafe import (
    TYPESAFE_JEV_RETRIES,
    TYPESAFE_JEV_TIMEOUT_S,
    TypeSafeJevProvider,
)
```

1. **タイムアウト・retry設定ピン**: `TYPESAFE_JEV_TIMEOUT_S == 6.0`(07 §1)・`TYPESAFE_JEV_RETRIES == 0`(T5: RetryPolicy(max_retries=0)の意味)。SDKのretryクラス自体の同値性(例: `RetryPolicy(max_retries=0) == TYPESAFE_JEV_RETRIES` 相当)は、SDK実装確認の結果に応じて `_build_client()` の構築引数ピンとして書く
2. **system_one呼び出し引数ピン**: client注入スタブ(呼び出し引数を記録)で `judge()` を呼び、`state == {"intent_a": <入力A>, "intent_b": <入力B>}`・`model == "jev-1.13.0"`・`questions is JEV_QUESTIONS`・エイリアス(jev-latest)でないこと
3. **応答→envelope組立てピン**: SDK応答(model="jev-1.13.0"・answers 7キー・usage)→ `{"model": ..., "answers": {...}, "usage": {...}}` のdictで返る
4. **例外翻訳4種**: TypeSafeRateLimitError→LLMRateLimitError / TypeSafeAPIError(status=529)→LLMOverloadedError / TypeSafeAPIError(status=400)→LLMProviderError(**切替しない側**) / TypeSafeAPIConnectionError→LLMConnectionError。翻訳された例外メッセージにIntent本文が含まれないこと(1例で検査)
5. **鍵欠落fail-fast**: `TypeSafeJevProvider(api_key="")` でValueError
6. **name**: `provider.name == "typesafe"`
7. **例外はLLMError継承のみで握らない**: 未知の例外(RuntimeError等)は素通り

- [ ] **Step 3: 実装する**

`backend/src/latch/llm/typesafe.py` を作成。内容は§9-6のtypesafe側(定数・コンストラクタ・`_build_client()`・`judge()`・`_envelope()`・例外翻訳表)のとおり。docstringに出典(07 §4・design §1.3 T1〜T7・§9-12の予備経路)を書く。

- [ ] **Step 4: 実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_typesafe_jev.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/pyproject.toml backend/uv.lock backend/src/latch/llm/typesafe.py backend/tests/unit/llm/test_typesafe_jev.py
git commit -m "feat: TypeSafeJevProvider(typesafe-sdk・retry無効化・例外翻訳)"
```

### Task 3: llm/anthropic_jev.py(フォールバックLLMプロバイダ)

**Files:**
- Create: `backend/src/latch/llm/anthropic_jev.py`
- Test: `backend/tests/unit/llm/test_anthropic_jev_fallback.py`

**Interfaces:**
- Consumes: `adapt_schema_for_anthropic`(`llm/anthropic.py`からimport流用 — 変更しない)
- Produces(Task 6のbuild_worker_gatewayが使用):
  - `AnthropicJevFallbackProvider(JevProvider)`: `name="anthropic"`・`__init__(*, api_key, base_url=ANTHROPIC_JEV_BASE_URL, client=None)`・`judge(intent_a, intent_b) -> dict`(System One envelope・model=None・confidence=None)
  - 定数: `ANTHROPIC_JEV_FALLBACK_MODEL = "claude-sonnet-5"` / `ANTHROPIC_JEV_FALLBACK_TIMEOUT_S = 6.0` / `ANTHROPIC_JEV_FALLBACK_MAX_TOKENS = 512` / `ANTHROPIC_JEV_BASE_URL = "https://api.anthropic.com"` / `FALLBACK_SYSTEM_PROMPT`(§9-6の全文)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/llm/test_anthropic_jev_fallback.py` を作成。試験項目(design §4.1・client注入スタブでAsyncAnthropicを置換):

1. **messages.create引数ピン**(呼び出し引数を記録するスタブで検査): `model == "claude-sonnet-5"` / `system == FALLBACK_SYSTEM_PROMPT` / `messages` のuser contentが2テキストを`\n\n`で連結したもの / `output_config.format.type == "json_schema"` かつ schemaが7キー / `thinking == {"type": "disabled"}` / `max_tokens == 512`
2. **sampling系不送ピン(Review Focus 5・試験名 `test_create_args_have_no_sampling_params`)**: create呼び出しの引数名集合に `temperature`・`top_p`・`top_k` が**存在しない**(A2: 送信すると400)
3. **スキーマピン**: `provider._schema` が `$ref`/`$defs`/`minimum`/`maximum`/`minLength` を含まない(adapt済み)・propertiesが7キーすべて `{"type": "number"}`(required=全プロパティ・additionalProperties=False)
4. **envelope組立てピン**: テキスト応答(JSON)→ `{"model": None, "answers": {...System One形式に組立直し(noulは {"type":"noul","noul":<値>}・scoreは {"type":"score","score":<値>,"confidence":None})}, "usage": {...}}`。answersの全値が元JSONと一致・confidenceが全てNone(引用#6)・`usage` が応答usageのinput/output_tokensと一致
5. **timeout・retryピン**: `ANTHROPIC_JEV_FALLBACK_TIMEOUT_S == 6.0` かつ `latch.llm.gateway.TIMEOUT_JEV_S` と同値・SDK構築ピン(`ANTHROPIC_JEV_FALLBACK_MAX_TOKENS == 512`・実構築時 `max_retries=0` は `client` 未注入時に `AsyncAnthropic(api_key=…, base_url=…, timeout=6.0, max_retries=0)` で構築されること — AnthropicParserProviderと同一規律。`ANTHROPIC_JEV_BASE_URL == ANTHROPIC_PARSER_BASE_URL` の同値性も強制)
6. **鍵欠落fail-fast**: `AnthropicJevFallbackProvider(api_key="")` でValueError
7. **プロンプト全文ピン**: `FALLBACK_SYSTEM_PROMPT == <§9-6の全文>`(変更検知)
8. **SDK例外は素通り**: スタブがRuntimeErrorを投げたらRuntimeErrorのまま(Gatewayのwrapが最終関門 — anthropic.pyと同一規律)

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_anthropic_jev_fallback.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'latch.llm.anthropic_jev'`

- [ ] **Step 3: 実装する**

`backend/src/latch/llm/anthropic_jev.py` を作成。内容は§9-6のanthropic側(定数・FALLBACK_SYSTEM_PROMPT全文・FallbackJevAnswers pydanticモデル・コンストラクタ・judge())のとおり。docstringに出典(07 §4切替節・design §1.3 A1〜A4・§5-8の先送り記録)を書く。

- [ ] **Step 4: 実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_anthropic_jev_fallback.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/llm/anthropic_jev.py backend/tests/unit/llm/test_anthropic_jev_fallback.py
git commit -m "feat: AnthropicJevFallbackProvider(structured output・sampling不送・envelope組立て)"
```

### Task 4: stub envelope化・新例外・providers docstring(機械的追随1)

**Files:**
- Modify: `backend/src/latch/llm/stub.py`(DEFAULT_JEV_RESPONSEを§9-7へ置換)
- Modify: `backend/src/latch/llm/errors.py`(LLMRateLimitError・LLMOverloadedError・LLMConnectionError追加 — JevOutputInvalidErrorはTask 1で追加済み)
- Modify: `backend/src/latch/llm/providers.py`(JevProvider.judgeのdocstringのみ)
- Modify: `backend/src/latch/llm/__init__.py`(新例外4種を公開APIへ)
- Test: `backend/tests/unit/llm/test_stub.py`(追随)

**Interfaces:**
- Consumes: なし
- Produces(Task 5以降): DEFAULT_JEV_RESPONSEがSystem One envelope・新例外3種(`LLMRateLimitError`/`LLMOverloadedError`/`LLMConnectionError`、いずれもLLMError継承)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/llm/test_stub.py` を修正(機械的追随 — supervisor承認):

1. `test_stub_jev_default_response_matches_07_section4` を書き換え:

```python
async def test_stub_jev_default_response_matches_10_section1():
    resp = await StubLLM().judge(
        "Intent A:\n[hard] category: drinking", "Intent B:\n[hard] category: meal"
    )
    assert set(resp) == {"model", "answers", "usage"}  # System One envelope(10 §1 v0.4)
    assert set(resp["answers"]) == JEV_KEYS
    assert resp["model"] == "jev-1.13.0"
    assert resp["answers"]["would_a_accept_b"] == {"type": "noul", "noul": 0.5}
    assert resp["answers"]["purpose_fit"] == {"type": "score", "score": 2.0, "confidence": 0.5}
    assert resp["usage"] == {"input_tokens": 0, "output_tokens": 0}
```

2. `test_default_response_constants_match_spec`: `assert set(DEFAULT_JEV_RESPONSE) == {"model", "answers", "usage"}`
3. `test_stub_response_override`: `bad_jev = {"model": "jev-1.13.0", "answers": {"would_a_accept_b": {}}, "usage": {}}`(検証失敗応答の注入例・envelope化)
4. `test_stub_responses_are_not_shared_mutable`: `jev["purpose_fit"] = 0.99` を `jev["answers"]["purpose_fit"]["score"] = 0.99` へ・その後のassertも `(await jev_stub.judge("a", "b"))["answers"]["purpose_fit"]["score"] == 2.0`(デフォルト値はscore=2.0)へ・`DEFAULT_PARSER_RESPONSE` の既定primary=="meal"の部分は無変更
5. **新例外ピン**(`test_stub.py`末尾に追加):

```python
def test_switch_exceptions_are_llm_error_subclasses():
    """切替条件例外4種はLLMError継承(07 §4切替表・design §2.2)。"""
    from latch.llm.errors import (
        JevOutputInvalidError,
        LLMConnectionError,
        LLMOverloadedError,
        LLMRateLimitError,
    )

    for exc in (LLMRateLimitError, LLMOverloadedError, LLMConnectionError, JevOutputInvalidError):
        assert issubclass(exc, LLMError)
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_stub.py -v`
Expected: FAIL(envelope期待値と旧v0.4形式の差分・新例外が未定義)

- [ ] **Step 3: 実装する**

1. `llm/stub.py`: `DEFAULT_JEV_RESPONSE` を§9-7のenvelopeへ置換(docstringに出典10 §1 v0.4を追記)。他の変更なし
2. `llm/errors.py`: `LLMRateLimitError("429(07 §4切替条件)…")` / `LLMOverloadedError("529…")` / `LLMConnectionError("接続障害…")` を追加(各々LLMError継承・1行docstring)
3. `llm/providers.py`: `JevProvider.judge` のdocstringを「戻り値はSystem One envelope(`{"model": ..., "answers": {7キー: answer}, "usage": ...}`)」へ約束変更(署名不変・呼び出し元はGatewayのみ)
4. `llm/__init__.py`: errors import行へ新例外4種を追加し `__all__` へ追記(アルファベット順を維持)

- [ ] **Step 4: 実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_stub.py tests/unit/llm/test_gateway_jev.py -v`
Expected: test_stub.pyはPASS。**test_gateway_jev.pyは既定(dict応答の比較)を変更していないためPASS**(judge_pairはまだ旧実装でdictをそのまま返す — Task 5で追随)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/llm/stub.py backend/src/latch/llm/errors.py backend/src/latch/llm/providers.py backend/src/latch/llm/__init__.py backend/tests/unit/llm/test_stub.py
git commit -m "feat: StubJevをSystem One answers形式へ・切替条件例外3種新設"
```

### Task 5: Gateway judge_pair実装化(切替ロジック・JevJudgment)

**Files:**
- Modify: `backend/src/latch/llm/gateway.py`(judge_pair実装化・`jev_fallback`引数追加・`_jev_call`・`_envelope_model`)
- Modify: `backend/src/latch/llm/__init__.py`(`JevJudgment`を公開APIへ)
- Create: `backend/tests/unit/llm/test_gateway_jev_switch.py`
- Modify: `backend/tests/unit/llm/test_gateway_jev.py`(機械的追随)

**Interfaces:**
- Consumes: `validate_and_normalize`・`JevJudgment`(Task 1)・新例外3種(Task 4)
- Produces(Task 6・9が使用):
  - `LLMGateway.__init__(..., jev_fallback: JevProvider | None = None)`(未注入時は第一候補と同一 — §9-5)
  - `judge_pair(*, intent_a, intent_b, intent_ids) -> JevJudgment`(切替条件4種でフォールバック)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/llm/test_gateway_jev_switch.py` を作成。切替マトリクス全行(design §2.2の表・§4.1):

```python
"""judge_pair切替マトリクスのunit試験(design §2.2の表・§4.1)。"""

import json
import logging
from datetime import UTC, datetime

import pytest

from latch.core.clock import FakeClock
from latch.llm.errors import (
    JevOutputInvalidError,
    LLMConnectionError,
    LLMOverloadedError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from latch.llm.gateway import LLMGateway
from latch.llm.jev import JevJudgment
from latch.llm.providers import JevProvider
from latch.llm.records import LOGGER_NAME
from latch.llm.stub import DEFAULT_JEV_RESPONSE, StubLLM

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)
IA = "Intent A:\n[hard] category: drinking"
IB = "Intent B:\n[hard] category: meal"


class _ThrowingJev(JevProvider):
    """指定例外を投げる第一候補スタブ(切替条件の注入)。"""

    name = "throwing"

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def judge(self, intent_a: str, intent_b: str) -> dict:
        raise self._exc


def _stub_envelope(model: str = "jev-1.13.0") -> dict:
    import copy

    env = copy.deepcopy(DEFAULT_JEV_RESPONSE)
    env["model"] = model
    return env


def _gateway(first: JevProvider, fallback: JevProvider) -> LLMGateway:
    return LLMGateway(clock=FakeClock(NOW), parser=StubLLM(), embedding=StubLLM(), jev=first, jev_fallback=fallback)


def _send_payloads(caplog) -> list[dict]:
    messages = [r for r in caplog.records if r.name == LOGGER_NAME]
    return [json.loads(m.getMessage()) for m in messages]


async def test_primary_ok_returns_typesafe_judgment(caplog):
    fb = _RecordingStub()
    gw = _gateway(StubLLM(jev_response=_stub_envelope("jev-1.13.0")), fb)
    j = await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert isinstance(j, JevJudgment)
    assert j.provider == "typesafe_jev"
    assert j.model == "jev-1.13.0"
    assert j.result["jev_5axis"]["purpose_fit"]["value"] == 0.5  # 2.0/4
    assert fb.calls == 0  # 切替不要


async def test_primary_timeout_switches_to_fallback(caplog):
    fb = _RecordingStub()
    gw = _gateway(_ThrowingJev(LLMTimeoutError("t")), fb)
    j = await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert j.provider == "fallback_llm" and j.model is None
    assert fb.calls == 1
    payloads = _send_payloads(caplog)
    assert [p["destination"] for p in payloads] == ["throwing", "fb"]  # 送信記録2件
    assert payloads[0]["status"] == "error"


@pytest.mark.parametrize(
    "exc",
    [LLMRateLimitError("429"), LLMOverloadedError("529"), LLMConnectionError("conn")],
)
async def test_switch_conditions_all_switch(exc, caplog):
    gw = _gateway(_ThrowingJev(exc), _RecordingStub())
    j = await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert j.provider == "fallback_llm"
    assert len(_send_payloads(caplog)) == 2


async def test_primary_provider_error_does_not_switch(caplog):
    """400系(LLMProviderError)は切替条件外で伝播(07 §4)。"""
    fb = _RecordingStub()
    gw = _gateway(_ThrowingJev(LLMProviderError("400")), fb)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with pytest.raises(LLMProviderError):
            await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert fb.calls == 0
    assert len(_send_payloads(caplog)) == 1  # 切替なし


async def test_primary_invalid_output_does_not_switch(caplog):
    """出力検証失敗は再試行も切替もしない(Review Focus 1・07 §4)。"""
    bad = _stub_envelope()
    del bad["answers"]["latent_yes"]  # キー欠損=検証失敗
    fb = _RecordingStub()
    gw = _gateway(StubLLM(jev_response=bad), fb)
    with pytest.raises(JevOutputInvalidError):
        await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert fb.calls == 0


async def test_fallback_failure_propagates(caplog):
    """フォールバック失敗(双障害)はそのまま伝播(D-15)。"""
    gw = _gateway(_ThrowingJev(LLMTimeoutError("t")), _ThrowingJev(LLMTimeoutError("fb")))
    with pytest.raises(LLMTimeoutError):
        await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])
    assert len(_send_payloads(caplog)) == 2


async def test_fallback_invalid_output_propagates(caplog):
    gw = _gateway(_ThrowingJev(LLMRateLimitError("429")), StubLLM(jev_response={"answers": {}}))
    with pytest.raises(JevOutputInvalidError):
        await gw.judge_pair(intent_a=IA, intent_b=IB, intent_ids=["a", "b"])


class _RecordingStub(StubLLM):
    """フォールバック側の正常スタブ(呼び出し回数を記録)。"""

    name = "fb"

    def __init__(self) -> None:
        super().__init__(jev_response={"model": None, "answers": {k: {"type": "noul", "noul": 0.5} if k in ("would_a_accept_b", "would_b_accept_a", "latent_yes") else {"type": "score", "score": 2.0, "confidence": None} for k in ("would_a_accept_b", "would_b_accept_a", "latent_yes", "purpose_fit", "mood_fit", "timing_fit", "social_fit")}, "usage": {}})
        self.calls = 0

    async def judge(self, intent_a: str, intent_b: str) -> dict:
        self.calls += 1
        return await super().judge(intent_a, intent_b)
```

(上記コードは構成の目安 — `_RecordingStub` をファイル先頭に移動し、各試験から使えるようにする。期待値の詳細は§9-4・§9-5どおり)

`backend/tests/unit/llm/test_gateway_jev.py` を修正(機械的追随 — supervisor承認):

1. `test_judge_returns_7_question_response` → JevJudgment化:

```python
async def test_judge_returns_judgment_from_stub_envelope(clock):
    resp = await _gateway(clock, StubLLM()).judge_pair(
        intent_a=INTENT_A, intent_b=INTENT_B, intent_ids=["i-1", "i-2"]
    )
    assert resp.provider == "typesafe_jev"  # 第一候補経路(スタブでも)
    assert resp.model == "jev-1.13.0"
    assert resp.result["would_a_accept_b"] == 0.5
    assert resp.result["jev_5axis"]["purpose_fit"] == {"value": 0.5, "confidence": 0.5}
```

2. `test_judge_timeout_records_then_raises`: 未注入fallback=同一スタブのためtimeoutが双障害になる。送信記録2件化:

```python
    (p1, p2) = _send_payloads(caplog)
    assert p1["status"] == "timeout" and p2["status"] == "timeout"
```

3. `test_judge_provider_error_records_then_raises`: 無変更で通る(LLMProviderErrorは切替せず・記録1件)
4. `test_judge_records_send_record_with_two_intent_ids`・`test_judge_input_texts_never_appear_in_send_record`: 無変更で通る

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_gateway_jev_switch.py tests/unit/llm/test_gateway_jev.py -v`
Expected: FAIL(judge_pairがdictを返し切替がない)

- [ ] **Step 3: 実装する**

`llm/gateway.py` を§9-5のとおり変更(jev_fallback引数・judge_pair実装化・_jev_call・_envelope_model)。importへ `from latch.llm.errors import …LLMRateLimitError, LLMOverloadedError, LLMConnectionError` と `from latch.llm.jev import JevJudgment, validate_and_normalize` を追加。`llm/__init__.py` へ `JevJudgment` を追記。
注意: `TIMEOUT_JEV_S = 6.0` は第一候補・フォールバックで共用(`_call` ごとに6秒) — 双方で6秒(引用#10)。

- [ ] **Step 4: 実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit/llm/ -v`
Expected: PASS(test_llm_factoryの`test_factory_passes_delay_settings_to_stub`もjudge_pair戻り値変更に伴い`await gw.judge_pair(...)`のまま呼び出しだけで問題なし)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/llm/gateway.py backend/src/latch/llm/__init__.py backend/tests/unit/llm/test_gateway_jev_switch.py backend/tests/unit/llm/test_gateway_jev.py
git commit -m "feat: judge_pair実装化(TypeSafe Jev第一候補+フォールバック切替)"
```

### Task 6: settings追加・build_worker_gateway改名拡張(機械的追随2)

**Files:**
- Modify: `backend/src/latch/settings.py`(`llm_typesafe_api_key`・`llm_typesafe_base_url`追加)
- Modify: `backend/src/latch/llm/gateway.py`(`build_embedding_gateway` → `build_worker_gateway`改名拡張・旧名削除)
- Modify: `backend/src/latch/llm/__init__.py`(export名変更)
- Modify: `backend/src/latch/worker/main.py`(import先変更のみ)
- Modify: `backend/src/latch/llm/embed_smoke.py`(import先変更のみ)
- Test: `backend/tests/unit/llm/test_llm_factory.py`(機械的追随)・`backend/tests/unit/test_llm_gemini.py`(改名追随)

**Interfaces:**
- Consumes: `TypeSafeJevProvider`(Task 2)・`AnthropicJevFallbackProvider`(Task 3)
- Produces(Task 9〜11が使用): `build_worker_gateway(clock, settings) -> LLMGateway`(stub時はjev_fallback=同一スタブ・real時は3鍵必須fail-fast — §9-13)
- Produces: `Settings.llm_typesafe_api_key`(env `LATCH_TYPESAFE_API_KEY`)・`Settings.llm_typesafe_base_url`(env `LATCH_TYPESAFE_BASE_URL`・既定 `https://api.typesafe.ai`)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/llm/test_llm_factory.py` を修正(機械的追随 — supervisor承認・§9-10):

1. `LLM_ENV_VARS` へ `"LATCH_TYPESAFE_API_KEY"`・`"LATCH_TYPESAFE_BASE_URL"` を追加
2. `test_llm_settings_defaults` へ `assert s.llm_typesafe_api_key == ""` と `assert s.llm_typesafe_base_url == "https://api.typesafe.ai"` を追加
3. `test_llm_settings_are_exactly_seven_fields` を `test_llm_settings_are_exactly_nine_fields` へ改名し、期待値セットへ `"llm_typesafe_api_key"`・`"llm_typesafe_base_url"` を追加(9項目)
4. env読み取り試験を追加(`test_llm_settings_env_reads_typesafe_key`・`test_llm_settings_env_reads_typesafe_base_url` — anthropicと同一パターン: `monkeypatch.setenv("LATCH_TYPESAFE_API_KEY", "env-key")` → `Settings().llm_typesafe_api_key == "env-key"`)
5. build_worker_gatewayの試験を追加:

```python
def test_worker_gateway_real_builds_typesafe_and_fallback(clock, monkeypatch):
    """real=embedding+jev+フォールバックの3系統real化(design §2.8)。"""
    from latch.llm.anthropic_jev import AnthropicJevFallbackProvider
    from latch.llm.typesafe import TypeSafeJevProvider

    gw = build_worker_gateway(
        clock,
        _clean_settings(
            monkeypatch,
            llm_mode="real",
            llm_gemini_api_key="gk",
            llm_typesafe_api_key="tk",
            llm_anthropic_api_key="ak",
        ),
    )
    assert isinstance(gw._embedding, GeminiEmbeddingProvider)
    assert isinstance(gw._jev, TypeSafeJevProvider)
    assert gw._jev.name == "typesafe"
    assert isinstance(gw._jev_fallback, AnthropicJevFallbackProvider)
    assert gw._jev_fallback.name == "anthropic"
    assert isinstance(gw._parser, StubLLM)  # parser系統はstub継続


@pytest.mark.parametrize("missing", ["llm_gemini_api_key", "llm_typesafe_api_key", "llm_anthropic_api_key"])
def test_worker_gateway_real_fails_fast_without_key(clock, monkeypatch, missing):
    kwargs = {"llm_mode": "real", "llm_gemini_api_key": "gk", "llm_typesafe_api_key": "tk", "llm_anthropic_api_key": "ak"}
    kwargs.pop(missing)
    with pytest.raises(ValueError, match="api_key"):
        build_worker_gateway(clock, _clean_settings(monkeypatch, **kwargs))


def test_worker_gateway_stub_shares_stub_for_fallback(clock, monkeypatch):
    gw = build_worker_gateway(clock, _clean_settings(monkeypatch))
    assert gw._jev is gw._jev_fallback  # stub時は同一インスタンス(design §2.8)
```

(test内のimportはファイル先頭にまとめる。`build_llm_gateway`の既存試験は無変更)

`backend/tests/unit/test_llm_gemini.py` を修正(機械的追随):

1. 3試験を `build_worker_gateway` 呼び出しに改名・置換: `test_build_embedding_gateway_stub_mode_all_stub` → `test_build_worker_gateway_stub_mode_all_stub`(`from latch.llm import StubLLM, build_worker_gateway`)・`test_build_embedding_gateway_real_embeds_only` → `test_build_worker_gateway_real_builds_embedding_and_jev`(assertへjev/jev_fallback検証を追加)・`test_build_embedding_gateway_real_without_key_fails_fast` → `test_build_worker_gateway_real_without_key_fails_fast`
2. import先コメント(「§2.8-B」等)は実態に合わせて「§2.8・ws-5」へ更新

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/llm/test_llm_factory.py tests/unit/test_llm_gemini.py -v`
Expected: FAIL(typesafe設定が存在しない・build_worker_gatewayが未定義)

- [ ] **Step 3: 実装する**

1. `settings.py`: Embeddingセクションの後へ§9-13の2フィールドを追加(AliasChoices形式・コメントにgemini鍵と同一規律を記す)
2. `gateway.py`: `build_embedding_gateway` を削除し、`build_worker_gateway` を§9-13のとおり新設。importへTypeSafeJevProvider・AnthropicJevFallbackProviderを追加(循環importに注意: `llm/typesafe.py`・`llm/anthropic_jev.py` はgatewayをimportしない)
3. `llm/__init__.py`: `build_embedding_gateway` → `build_worker_gateway` へ・`TypeSafeJevProvider`・`AnthropicJevFallbackProvider`・`JEV_MODEL` を追加(`__all__` も更新)
4. `worker/main.py`: `from latch.llm.gateway import build_worker_gateway` へ1行変更(gateway構築点はTask 10で置換するため、**本タスクではimportのみ変更し `build_worker_gateway(self.clock, self.settings)` 呼び出しに置換**する — 呼び名が変わるのみで挙動は同一・embedding構築にそのまま使える)
5. `llm/embed_smoke.py`: import先を `build_worker_gateway` へ変更し、呼び出し行も置換(ロジック無変更)

- [ ] **Step 4: 実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit -v`
Expected: PASS(unit全体)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/settings.py backend/src/latch/llm/gateway.py backend/src/latch/llm/__init__.py backend/src/latch/worker/main.py backend/src/latch/llm/embed_smoke.py backend/tests/unit/llm/test_llm_factory.py backend/tests/unit/test_llm_gemini.py
git commit -m "feat: build_worker_gateway改名拡張(embedding+jev+フォールバックreal化・typesafe設定追加)"
```

### Task 7: マイグレーション0003(skip_reason列)

**Files:**
- Create: `backend/alembic/versions/0003_match_candidates_skip_reason.py`
- Test: 既存 `backend/tests/integration/test_schema.py` の実DB検証はスーパーバイザー検証時。unitでは「ファイルの存在とrevisionチェーン」をpy_compile相当で確認(`uv run pytest --collect-only` のimport検査に含まれる)

**Interfaces:**
- Consumes: なし
- Produces(Task 8・9・integrationが使用): `match_candidates.skip_reason text NULL`

- [ ] **Step 1: 実装する**

`backend/alembic/versions/0003_match_candidates_skip_reason.py` を作成。内容は§9-11のとおり(revision="0003"・down_revision="0002"・`op.execute` のALTER TABLE 1文ずつ)。`skip_reason` は値域CHECKを付けない(値域はアプリ層の定数 `RESELECT_ALWAYS`/`RESELECT_DAILY`/`RESELECT_MONTHLY`・`DENY_*` が保持 — ws-4のstatus列と同じ判断)。

- [ ] **Step 2: 適用可能であることを静的に確認する**

**`make migrate` / `docker` は実行禁止(§0)**。代わりにスクリプトチェーンの読み込みで確認(実DBには触れない):

Run:
```bash
cd backend && uv run python - <<'EOF'
from alembic.config import Config
from alembic.script import ScriptDirectory
sd = ScriptDirectory.from_config(Config("alembic.ini"))
heads = sd.get_heads()
assert heads == ["0003"], heads
walk = [rev.revision for rev in sd.walk_revisions()]
assert "0003" in walk and "0002" in walk and "0001" in walk, walk
print("OK heads=", heads)
EOF
```
Expected: `OK heads= ['0003']`(チェーン接続の静的確認)
Run: `cd backend && uv run pytest --collect-only tests/integration/test_schema.py -q`
Expected: 収集成功(実DB検証はtest-ci=スーパーバイザー検証待ち)

- [ ] **Step 3: コミット**

```bash
make lint && make test
git add backend/alembic/versions/0003_match_candidates_skip_reason.py
git commit -m "feat: マイグレーション0003(match_candidates.skip_reason列追加)"
```

### Task 8: worker/matching/layer4.py(選択SQL・H再検証・close・配分純関数)

**Files:**
- Create: `backend/src/latch/worker/matching/layer4.py`
- Test: `backend/tests/unit/matching/test_layer4.py`

**Interfaces:**
- Consumes: `layer1.LAYER1_WHERE`(importして文字列再利用 — 変更しない)・`origin.Origin`・`origin.bind_params`
- Produces(Task 9が使用 — §9-8のとおり):
  - 定数: `K_J = 8` / `ONE_ON_ONE_MIN = 4` / `PAIR_KIND_ONE_ON_ONE = "one_on_one"` / `RESELECT_ALWAYS = ("llm_failure", "invalid_output")` / `RESELECT_DAILY = ("intent_daily", "user_daily", "global_daily")` / `RESELECT_MONTHLY = ("global_monthly",)`
  - `JevCandidateRow`(frozen dataclass)
  - `select_jev_rows(conn, origin_id, origin_version, day_start, month_start) -> list[JevCandidateRow]`(day_start/month_startはJST境界のtz-aware datetime — モジュール関数jst_day_start/jst_month_startとの名前衝突を避けるため引数名からjst_を外す)
  - `hard_constraint_holds(conn, origin, candidate_id) -> bool`
  - `close_broken_pairs(conn, origin, now) -> int`
  - `select_jev_targets(rows) -> list[JevCandidateRow]`
  - `jst_day_start(d: date) -> datetime` / `jst_month_start(d: date) -> datetime`

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_layer4.py` を作成。試験項目(design §4.1):

1. **select_jev_targets配分**(design §4.1・ws-5では恒等):
   - 8件入力(全てone_on_one)→8件そのまま返る(順序維持)
   - 12件入力→先頭8件(K_J上限)
   - 3件入力→3件(ONE_ON_ONE_MIN=4でも4件に補わない — 補充禁止は行レベルの割当であり、存在しない行を作らない)
   - 同点順序は入力順維持(並べ替えはSQL側の担当)
   - `K_J == 8 and ONE_ON_ONE_MIN == 4`(docs確定値ピン)
2. **SQL句ピン**(test_layer_sql.py流儀):

```python
"""layer4(選択SQL・H再検証・配分)のunit試験(design §4.1)。"""

import uuid

from sqlalchemy.dialects import postgresql

from latch.worker.matching import layer1, layer4
from latch.worker.matching.layer4 import (
    JevCandidateRow,
    K_J,
    ONE_ON_ONE_MIN,
    jst_day_start,
    jst_month_start,
    select_jev_targets,
)

def _row(n: int, kind: str = "one_on_one") -> JevCandidateRow:
    return JevCandidateRow(
        row_id=uuid.UUID(f"00000000-0000-4000-8000-{n:012d}"),
        intent_a_id=uuid.UUID(f"00000000-0000-4000-8000-{n:012d}"),
        intent_b_id=uuid.UUID(f"00000000-0000-4000-8000-{(n + 100):012d}"),
        intent_a_version=1,
        intent_b_version=1,
        cheap_judge_score=0.5,
        status="pending",
        skip_reason=None,
        pair_kind=kind,
    )


def test_constants_pin_docs_values():
    assert K_J == 8  # 06 §8 D-24
    assert ONE_ON_ONE_MIN == 4  # 06 §5(1対1最低保証)


def test_select_targets_identity_and_cap():
    rows = [_row(i) for i in range(8)]
    assert select_jev_targets(rows) == rows  # 恒等(全て1対1)
    rows12 = [_row(i) for i in range(12)]
    assert len(select_jev_targets(rows12)) == 8  # K_J上限
    rows3 = [_row(i) for i in range(3)]
    assert len(select_jev_targets(rows3)) == 3  # 補充しない


def test_select_sql_pins():
    sql = str(layer4._SELECT_JEV_ROWS)
    # 選択条件(design §2.5・§2.7)
    assert "mc.jev_result IS NULL" in sql  # FR-07: 同一評価世代スキップのフィルタ
    assert "mc.status = 'pending'" in sql
    assert "'llm_failure', 'invalid_output'" in sql  # 障害系=即時
    assert "'intent_daily', 'user_daily', 'global_daily'" in sql
    assert "mc.skip_reason = 'global_monthly'" in sql
    assert "mc.updated_at < CAST(:jst_day_start AS timestamptz)" in sql
    assert "mc.updated_at < CAST(:jst_month_start AS timestamptz)" in sql
    # 並び替えと上限
    assert "ORDER BY mc.cheap_judge_score DESC," in sql
    assert "LIMIT 8" in sql


def test_select_sql_all_bind_params_recognized():
    compiled = str(layer4._SELECT_JEV_ROWS.compile(dialect=postgresql.dialect()))
    for key in ("origin", "origin_version", "jst_day_start", "jst_month_start"):
        assert f":{key}" not in compiled, key


def test_h_recheck_reuses_layer1_where():
    """H再検証はLAYER1_WHEREを再利用(試験と本番のWHERE乖離なし — ws-3規律)。"""
    assert layer1.LAYER1_WHERE.strip() in str(layer4._H_RECHECK)
    sql = str(layer4._H_RECHECK)
    assert "LIMIT 1" in sql
    assert "i.id = CAST(:candidate_id AS uuid)" in sql


def test_close_broken_pins():
    sql = str(layer4._CLOSE_BROKEN)
    assert "jev_result IS NOT NULL" in sql
    assert "mc.status = 'evaluated'" in sql  # evaluated行のみclose(pendingは閉じない)
    assert "SET status = 'closed'" in sql
    assert "NOT EXISTS" in sql and layer1.LAYER1_WHERE.strip() in sql
    compiled = str(layer4._CLOSE_BROKEN.compile(dialect=postgresql.dialect()))
    for key in ("origin", "origin_version", "now", "origin_user_id", "origin_category", "origin_time_start", "origin_time_end", "origin_lon", "origin_lat", "origin_radius_m", "origin_budget", "origin_alcohol", "origin_user_ge_20"):
        assert f":{key}" not in compiled, key


def test_jst_boundaries():
    from datetime import date, timedelta
    from latch.core.clock import JST
    d = date(2026, 9, 29)
    assert jst_day_start(d) == datetime(2026, 9, 29, 0, 0, tzinfo=JST)
    assert jst_month_start(d) == datetime(2026, 9, 1, 0, 0, tzinfo=JST)
    # 月跨ぎ・年末
    assert jst_month_start(date(2026, 12, 31)) == datetime(2026, 12, 1, 0, 0, tzinfo=JST)
    assert jst_day_start(d) + timedelta(hours=24) == jst_day_start(date(2026, 9, 30))
```

(テスト先頭で `from datetime import datetime` も必要)

3. **SELECTのcompile検査**(上記2に含む)+ **`_SELECT_JEV_ROWS` のselect列ピン**: `mc.id, mc.intent_a_id, mc.intent_b_id, mc.intent_a_version, mc.intent_b_version, mc.cheap_judge_score, mc.status, mc.skip_reason` がすべて含まれること

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_layer4.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'latch.worker.matching.layer4'`

- [ ] **Step 3: 実装する**

`backend/src/latch/worker/matching/layer4.py` を作成。内容は§9-8のとおり。docstringに「06 §5・§8 D-24・design §2.5〜§2.7・LAYER1_WHERE再利用の規律継承」を書く。`async def` の実装は下記のとおり:

```python
async def select_jev_rows(
    conn: AsyncConnection,
    origin_id: uuid.UUID,
    origin_version: int,
    day_start: datetime,
    month_start: datetime,
) -> list[JevCandidateRow]:
    """起点の現在versionにおける未評価ペアから上位K_j(§2.5 SQL)。"""
    rows = (
        await conn.execute(
            _SELECT_JEV_ROWS,
            {
                "origin": origin_id,
                "origin_version": origin_version,
                "jst_day_start": day_start,
                "jst_month_start": month_start,
            },
        )
    ).all()
    return [
        JevCandidateRow(
            row_id=_coerce_uuid(r[0]),
            intent_a_id=_coerce_uuid(r[1]),
            intent_b_id=_coerce_uuid(r[2]),
            intent_a_version=r[3],
            intent_b_version=r[4],
            cheap_judge_score=None if r[5] is None else float(r[5]),
            status=r[6],
            skip_reason=r[7],
        )
        for r in rows
    ]
```

(`_coerce_uuid` はorigin.pyと同一のasyncpg UUIDサブクラス対策 — layer4内に小さく置く・origin.pyからimportしない〔変更禁止〕。SQLのbind param名は `jst_day_start`/`jst_month_start` のまま・Python引数名は `day_start`/`month_start`〔モジュール関数jst_day_start/jst_month_startとの衝突回避〕。呼び出し側Task 9は `select_jev_rows(conn, org.intent_id, org.version, jst_day_start(clock.jst_date()), jst_month_start(clock.jst_date()))` の形で渡す)

- [ ] **Step 4: 実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_layer4.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/matching/layer4.py backend/tests/unit/matching/test_layer4.py
git commit -m "feat: layer4(K_j選択SQL・H再検証・一括close・配分純関数)"
```

### Task 9: worker/jev.py(JevWorker本体)

**Files:**
- Create: `backend/src/latch/worker/jev.py`
- Test: `backend/tests/unit/test_worker_jev.py`

**Interfaces:**
- Consumes: `build_jev_text`・`JevTextInput`(Task 1)・`JevJudgment`(Task 5のjudge_pair戻り値)・layer4関数群(Task 8)・`JevCostGuard.request_execution`・`JevCostStore.record_execution`(ws-4)・`origin.load_origin`・`completion.default_time_end`
- Produces(Task 10・11・integrationが使用):
  - `JevWorker(*, engine, clock, gateway, guard, cost_store)`
  - `handle(intent_id: uuid.UUID) -> None`(**起点非依存のIF — ws-6がbucket起点から同一部品を呼ぶ**)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_worker_jev.py` を作成。テスト方針(design §4.1): layer4のconn系関数(`select_jev_rows`等)とDB UPDATEは、JevWorkerが `latch.worker.jev` モジュールの属性経由で呼ぶ形にしてmonkeypatch可能にする(runnerと同一規律)。guard・cost_storeはfakeredis構築の実物を注入(決定的)。テスト項目(design §2.9の表の全分岐):

```python
"""JevWorker.handleの全分岐(design §2.9の表)・冪等・versionガード・計上。"""

import json
import uuid
from datetime import UTC, datetime

import pytest

from latch.core.clock import FakeClock
from latch.llm.errors import (
    JevOutputInvalidError,
    LLMProviderError,
    LLMTimeoutError,
)
from latch.llm.gateway import LLMGateway
from latch.llm.jev import JevJudgment
from latch.llm.stub import DEFAULT_JEV_RESPONSE, StubLLM
from latch.worker import jev as jev_mod
from latch.worker.cost import JevCostGuard, JevCostStore
from latch.worker.jev import JevWorker
from latch.worker.matching.layer4 import JevCandidateRow

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)


def _uid(n: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-4000-8000-{n:012d}")


def _row(n: int = 1, status: str = "pending", skip_reason: str | None = None,
         cheap: float = 0.9) -> JevCandidateRow:
    return JevCandidateRow(
        row_id=_uid(n),
        intent_a_id=_uid(n),
        intent_b_id=_uid(n + 100),
        intent_a_version=1,
        intent_b_version=1,
        cheap_judge_score=cheap,
        status=status,
        skip_reason=skip_reason,
    )


def _judgment(provider: str = "typesafe_jev", model: str | None = "jev-1.13.0") -> JevJudgment:
    return JevJudgment(provider=provider, model=model, result={
        "would_a_accept_b": 0.83, "would_b_accept_a": 0.71,
        "jev_5axis": {
            "purpose_fit": {"value": 0.75, "confidence": 0.8},
            "mood_fit": {"value": 0.5, "confidence": 0.6},
            "timing_fit": {"value": 0.5, "confidence": 0.7},
            "social_fit": {"value": 0.75, "confidence": 0.6},
            "latent_yes": {"value": 0.4, "confidence": None},
        },
    })
```

以降の共通フィクスチャ: `FakeGuard`(実JevCostGuard+fakeredisを包み、decision/deny_reasonを差し替え可能に)・`FakeGateway`(judge_pairの戻り/例外を差し替え・呼び出し引数を記録)・`FakeEngine`(executeを記録し、phase1系のmonkeypatchとUPDATE結果を制御)。`worker/jev.py` はDB接続フェーズ(フェーズ1・相手読取・UPDATE)をモジュール関数 `_read_origin_input`・`_read_peer`・`_complete_row`・`_skip_row` に切り出し、unit試験はこれらをmonkeypatchする(embedding.py流儀とrunner流儀の併用)。

試験項目(全て design §4.1のリストどおり・各試験の期待値):

1. **成功経路**: gateway正常→jev_result書込({**result, provider, model}の完全一致)・status='evaluated'・skip_reason=NULLクリア・`record_execution("typesafe_jev", day)` 呼出1回・`request_execution(origin_id, user_id)` の引数が起点側(承認済み解釈記録)
2. **deny経路**: `JevDecision(allowed=False, deny_reason="intent_daily")`→status='skipped'・skip_reason='intent_daily'・jev_result NULLのまま・record_execution不呼出・judge_pair不呼出
3. **deny後の補充なし**: rowsに2件あっても1件目がdenyなら2件目も評価される(**補充でなく「そのまま消化」を検査** — denyでも以降の行は処理する。2件ともdenyならINCR 2回)
4. **双障害**: gatewayがLLMTimeoutError→status='skipped'・skip_reason='llm_failure'・`record_execution("fallback_llm", day)` 呼出・以降の行も処理される
5. **検証失敗**: gatewayがJevOutputInvalidError(provider="typesafe_jev"属性付き)→skip_reason='invalid_output'・`record_execution("typesafe_jev", day)`
6. **H不成立(pending)**: `hard_constraint_holds`→False→評価せず・guard不呼出・行状態不変(pending)・次の行は評価される
7. **H不成立(evaluated行)→close**: `close_broken_pairs` が呼ばれる(フェーズ1内・monkeypatchで呼出回数を記録)・モジュール関数の返り値が行数としてログに出る(呼出確認のみ)
8. **versionガード**: `_read_peer` がversion不一致を返す→judge_pair・guard不呼出・行状態不変
9. **冪等**: `_complete_row`/`_skip_row` のUPDATE WHEREに `jev_result IS NULL` があること(SQLピン)+ ジェバresult存在行への再handle→フェーズ1のselect_jev_rowsが空を返す(monkeypatch)→API・guard不呼出
10. **Guard Redis例外**: guardがJevCostDependencyError→handle外へ伝播(pytest.raises)・skipped記録に変換されない(fail-closed)
11. **DB書込失敗**: `_complete_row` が例外→伝播
12. **起点no-op**: `load_origin` がskip_reasonありを返す(monkeypatch)→close_broken_pairs・select_jev_rows不呼出・構造化ログのみ
13. **build_jev_text呼び出しピン**: FakeGatewayが受け取るintent_a/intent_bが `[hard]`/`[soft]` 行を含むテキスト・intent_idsが正規化順 `[str(a_id), str(b_id)]`
14. **経路providerの計上ピン**: 切替なし成功→typesafe_jev・成功(フォールバック経路のJevJudgment)→fallback_llm・`day` 引数が `clock.jst_date().strftime("%Y%m%d")` と一致

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/test_worker_jev.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'latch.worker.jev'`

- [ ] **Step 3: 実装する**

`backend/src/latch/worker/jev.py` を作成。構成は§9-9のとおり(SQL定数 `_COMPLETE`/`_SKIP`/`_SELECT_PEER`・JevWorker・handle・フェーズ分離)。実装の要点:

1. **モジュール属性経由の呼び出し**(monkeypatch可能にする規律): `from latch.worker.matching import origin as origin_mod`・`from latch.worker.matching import layer4` をimportし、`layer4.select_jev_rows(...)`・`layer4.close_broken_pairs(...)`・`origin_mod.load_origin(...)` の形で呼ぶ。DB UPDATEも `_complete_row(conn, row_id, jev_result, now) -> bool`・`_skip_row(conn, row_id, reason, now) -> bool` をモジュール関数に切り出す
2. **フェーズ1**(§9-9どおり): `async with self._engine.begin() as conn:` 内で load_origin→no-op判定→close_broken_pairs(org.evaluated_at)→`layer4.select_jev_rows(conn, org.intent_id, org.version, day_start, month_start)`→(tx外で)`layer4.select_jev_targets(rows)`。起点のjev入力列(structured_data/budget_max/geo_radius_m)は同一tx内で `_read_peer` と同一SQL(`_SELECT_PEER`・intent_id=起点)で読む
3. **フェーズ2+3**: 各行について§9-9の手順a〜i。相手の`_SELECT_PEER`読取・version照合・H再検証は `async with self._engine.begin() as conn:`(読取のみ)・UPDATEは別の短tx
4. **A/B割当**: 行のintent_a/intent_bをそのまま使い、originがどちらかを判定して `texts = {origin_id: origin側テキスト, peer_id: peer側テキスト}` → `intent_a = build_jev_text(inp_a, label="Intent A")`(intent_a_id側)・`intent_b = build_jev_text(inp_b, label="Intent B")`(intent_b_id側)。`judge_pair(intent_a=…, intent_b=…, intent_ids=[str(intent_a_id), str(intent_b_id)])`
5. **time_end補完**: JevTextInputにはOrigin.time_end(補完済み)を使う。peer側は `_SELECT_PEER` のtime_endがNULLのとき `default_time_end(time_start)`(intents/completion.py・load_originと同一規則)
6. **計上**: §9-9のf〜hどおり。**計上はUPDATEの後**。`day = self._clock.jst_date().strftime("%Y%m%d")`
7. docstringに「design §2.1案B・§2.9の表・06 §5(FR-07)・ws-4引継ぎ契約」を書く

- [ ] **Step 4: 実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_worker_jev.py -v`
Expected: PASS

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/jev.py backend/tests/unit/test_worker_jev.py
git commit -m "feat: JevWorker(2フェーズ実行・Guard課税・jev_result記録・世代スキップ)"
```

### Task 10: Worker配線(_kick_jev・JevWorker DI)

**Files:**
- Modify: `backend/src/latch/worker/main.py`(_kick_jev追加・JevWorker DI)
- Test: `backend/tests/unit/test_worker.py`(ピン追加)

**Interfaces:**
- Consumes: `JevWorker`(Task 9)・`build_worker_gateway`(Task 6)・`JevCostGuard`・`JevCostStore`(ws-4・worker/cost/__init__.pyで再export済み)・`EVENT_EMBEDDING_COMPLETED`(intents/events.py)
- Produces: `Worker._kick_jev(event_type, intent_id, version)`・`Worker._jev`(DI・未注入時no-op)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_worker.py` へ追加(embedding配線ピンと同一パターン — `_RecordingEmbedding` と対称の `_RecordingJev` を書く):

```python
class _RecordingJev:
    """JevWorkerスタブ(handleの呼び出しを記録)。"""

    def __init__(self):
        self.calls: list[uuid.UUID] = []

    async def handle(self, intent_id):
        self.calls.append(intent_id)


async def _started_worker_with_jev(fake_clock, stage1, jev, embedding=None):
    from latch.worker.main import Worker

    worker = Worker(clock=fake_clock, bus=_FakeBus(), stage1=stage1, embedding=embedding, jev=jev)
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.01)
    return worker, task


async def test_dispatch_processed_embedding_completed_kicks_jev(fake_clock):
    """processed × embedding_completed → handle(intent_id)をackの前に呼ぶ(design §2.1案B)。"""
    jev = _RecordingJev()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker_with_jev(fake_clock, stage1, jev)
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("embedding_completed", iid, 1))
        assert jev.calls == [iid]
    finally:
        await _stop(worker, task)


async def test_dispatch_duplicate_embedding_completed_kicks_jev(fake_clock):
    """duplicate × embedding_completed → handleを呼ぶ(再配信回収 — §2.9)。"""
    jev = _RecordingJev()
    stage1 = _RecordingStage1("duplicate")
    worker, task = await _started_worker_with_jev(fake_clock, stage1, jev)
    try:
        iid = uuid.uuid4()
        await worker._dispatch(_make_event("embedding_completed", iid, 1))
        assert jev.calls == [iid]
    finally:
        await _stop(worker, task)


async def test_dispatch_created_does_not_kick_jev(fake_clock):
    """created/updatedでは_kick_jevしない(06 §1: Layer 4はembedding_completed起点のみ)。"""
    jev = _RecordingJev()
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker_with_jev(fake_clock, stage1, jev)
    try:
        await worker._dispatch(_make_event("created", uuid.uuid4(), 1))
        assert jev.calls == []
    finally:
        await _stop(worker, task)


async def test_jev_not_injected_is_noop(fake_clock):
    """Jev未注入(ws-1資産の試験)は何もしない。"""
    stage1 = _RecordingStage1("processed")
    worker, task = await _started_worker(fake_clock, stage1)  # jev未注入
    try:
        await worker._dispatch(_make_event("embedding_completed", uuid.uuid4(), 1))
    finally:
        await _stop(worker, task)
```

(`_make_event` は既存のヘルパ。既存試験 `test_dispatch_non_create_update_types_no_kick`〔embedding側〕への影響なし)

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/test_worker.py -v`
Expected: FAIL(Workerにjev引数がない・_kick_jevが未定義)

- [ ] **Step 3: 実装する**

`worker/main.py` を§9-14のとおり変更(jev引数・DI・_kick_jev・_dispatchへの追加呼び出し)。Task 6でimport済みの `build_worker_gateway` 呼び出しをそのままembedding構築へ使う。DIは `if self._jev is None and redis_client is not None:`(§9-14)。

- [ ] **Step 4: 実行して通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_worker.py tests/unit/test_worker_embedding.py -v`
Expected: PASS(既存embedding配線試験へ影響なし)

- [ ] **Step 5: コミット**

```bash
make lint && make test
git add backend/src/latch/worker/main.py backend/tests/unit/test_worker.py
git commit -m "feat: WorkerへJevWorker配線(_kick_jev・DI)"
```

### Task 11: 実APIスモーク資産(make jev-smoke)

**Files:**
- Create: `backend/src/latch/llm/jev_smoke.py`
- Modify: `Makefile`(`jev-smoke`ターゲット追加)

**Interfaces:**
- Consumes: `build_worker_gateway`(Task 6)・`build_jev_text`・`JevTextInput`・`validate_and_normalize`(Task 1)
- Produces: `make jev-smoke`(実API 1呼び出し・**実行はスーパーバイザー検証時のみ** — 課金のため設計・実装段階では実行しない)

- [ ] **Step 1: 実装する**

`backend/src/latch/llm/jev_smoke.py` を作成(embed_smoke.pyと同型):

```python
"""Jev実APIスモーク(make jev-smoke・design §3.1)。実行はスーパーバイザー検証時のみ。

07 §4例の正規化テキスト2件を固定入力とし、第一候補(TypeSafe Jev)を1呼び出し
する。SendRecordも通常どおり出力される(Gateway経由 — 08 §3)。実行には
.env の LATCH_LLM_MODE=real・LATCH_GEMINI_API_KEY・LATCH_TYPESAFE_API_KEY・
LATCH_ANTHROPIC_API_KEY が必要(make jev-smoke が uv run --env-file ../.env 経由)。
FALLBACK=1 でフォールバックLLM(Anthropic Sonnet 5)の直接呼び出しに切り替える
(第一候補の障害を再現せず、フォールバック経路単体の応答確認用)。
noul応答のJSON形状の最終確認(design §5-7)もここで行う。
"""

from __future__ import annotations

import asyncio
import json

from latch.core.clock import SystemClock
from latch.llm.gateway import build_worker_gateway
from latch.llm.jev import JEV_MODEL, JevTextInput, build_jev_text, validate_and_normalize
from latch.settings import Settings

# 07 §4の正規化テキスト例をそのまま固定入力にする
A = JevTextInput(
    category_primary="drinking",
    structured_data={
        "location_name": "天文館周辺",
        "soft_constraints": [
            {"text": "軽く飲みたい", "downgraded_from_ng": False},
            {"text": "会社関係の人は避けたい", "downgraded_from_ng": True},
        ],
    },
    participants_min=2,
    participants_max=4,
    time_start=_from_iso("2026-09-26T20:00:00+09:00"),
    time_end=_from_iso("2026-09-26T23:00:00+09:00"),
    budget_max=5000,
    geo_radius_m=2000,
)
B = JevTextInput(
    category_primary="meal",
    structured_data={"location_name": "天文館周辺", "soft_constraints": []},
    participants_min=2,
    participants_max=2,
    time_start=_from_iso("2026-09-26T19:30:00+09:00"),
    time_end=_from_iso("2026-09-26T22:30:00+09:00"),
    budget_max=4000,
    geo_radius_m=1000,
)


def _from_iso(s: str):
    from datetime import datetime

    return datetime.fromisoformat(s)


async def main() -> int:
    import os

    settings = Settings()
    if settings.llm_mode != "real":
        print("[jev-smoke] FAIL: LATCH_LLM_MODE=real が必要(.env・make jev-smoke)")
        return 1
    if not settings.llm_typesafe_api_key:
        print("[jev-smoke] FAIL: LATCH_TYPESAFE_API_KEY が未設定(.env)")
        return 1
    if not settings.llm_anthropic_api_key:
        print("[jev-smoke] FAIL: LATCH_ANTHROPIC_API_KEY が未設定(.env・フォールバック用)")
        return 1
    if not settings.llm_gemini_api_key:
        print("[jev-smoke] FAIL: LATCH_GEMINI_API_KEY が未設定(.env・realは3鍵必須)")
        return 1
    gateway = build_worker_gateway(SystemClock(), settings)  # 鍵欠落はfail-fast
    text_a = build_jev_text(A, label="Intent A")
    text_b = build_jev_text(B, label="Intent B")
    if os.environ.get("FALLBACK") == "1":
        envelope = await gateway._jev_fallback.judge(text_a, text_b)
        provider, model = "fallback_llm(直接)", envelope.get("model")
        result = validate_and_normalize(envelope)
    else:
        judgment = await gateway.judge_pair(
            intent_a=text_a, intent_b=text_b, intent_ids=["jev-smoke-a", "jev-smoke-b"]
        )
        provider, model, result = judgment.provider, judgment.model, judgment.result
    print("[jev-smoke] provider=", provider, " model=", model)
    print("[jev-smoke] result=", json.dumps(result, ensure_ascii=False))
    print("[jev-smoke] expected_model=", JEV_MODEL)
    print("[jev-smoke] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

(`_from_iso` をモジュール先頭の関数として定義し、A/Bの定義より前に置く。`FALLBACK=1` のフォールバック直接呼び出しはスモーク専用の便宜であり、製品経路はjudge_pair)

`Makefile` へ追記(.PHONYへも追加):

```makefile
jev-smoke: ## Jev実APIスモーク(1呼び出し・課金。.envにLATCH_LLM_MODE=real+3鍵必須。FALLBACK=1でフォールバック直接)
	cd backend && uv run --env-file ../.env python -m latch.llm.jev_smoke
```

- [ ] **Step 2: 収集・import確認(実行はしない)**

Run: `cd backend && uv run python -c "import latch.llm.jev_smoke"`
Expected: エラーなし(実API呼び出しは `__main__` ガードのため走らない)
Run: `rg -n "jev-smoke" Makefile`
Expected: 1件(ターゲット+ヘルプコメント)

- [ ] **Step 3: コミット**

```bash
make lint && make test
git add backend/src/latch/llm/jev_smoke.py Makefile
git commit -m "feat: make jev-smoke(実APIスモーク・スーパーバイザー検証用)"
```

### Task 12: integration試験作成・完了条件検証・報告書

**Files:**
- Create: `backend/tests/integration/test_matching_jev.py`(作成のみ・**実行しない**)
- Create: `docs/plans/M2/ws-5-report.md`

**Interfaces:**
- Consumes: 全Taskの成果物
- Produces: 報告書(§7の形式)

- [ ] **Step 1: integration試験を書く**

`backend/tests/integration/test_matching_jev.py` を作成。**test_matching_cheapjudge.py流儀を踏襲**(対抗策はws-4で確立したもの — design §4.2): `pytestmark = pytest.mark.integration`・`BASE_HOURS = 120`(時間窓分離)・subjectプレフィックス `m2ws5-` のfixture(field)でteardown完全性(FK順: match_candidates→match_events→intents→users)・`redis_sweep` fixture(試験ごとの一意prefix・teardownでSCAN+DELETE)・スタブGateway(StubLLM)で決定的・Workerプロセス起動なし(JevWorker直接構築・FakeClock)。

ファイル冒頭のdocstringに対抗策4項目(§4.2)を明記する。試験内容(8試験+収集に関する注記):

1. **test_1_evaluate_records_jev_result**: ユーザー2人→active Intent 2件(API)→embedding直接UPDATE(ws-3流儀)→`run_candidate_retrieval`→`JevWorker.handle`→match_candidates行が `jev_result` 記録(provider="typesafe_jev"・model="jev-1.13.0"〔stub固定値〕・would_*=0.5・jev_5axisのpurpose_fit.value=0.5〔2.0/4〕・confidence=0.5)・status='evaluated'・skip_reason NULL
2. **test_2_kj8_truncation_deterministic**: 候補10件(完全同点: 同一embedding・同一start・同語彙でcheap_score同点)→8件evaluated・2件pending・評価された8件の集合が「相手intent_id昇順の先頭8件」と一致(実行順序ではなく集合で検証 — 同点tie-breakの決定性)・同一入力2回handleで同一結果(10 §4.6のJev部分)
3. **test_3_generation_skip_is_idempotent**: handle再実行→jev_result不変(updated_at不変)・API呼び出し0回(カウンタ付きStubLLMラップで検証)・guard.request_execution不呼出(FR-07)
4. **test_4_h_recheck_closes_evaluated_pair**: 評価済みペアの相手Intentを時間窓外へUPDATE→起点側handle再実行→当該行status='closed'・**jev_resultは保持**(値不変)・pending行は閉じない対照も検査(§2.6)
5. **test_5_guard_deny_records_reason**: key_prefix付きJevCostStore+実redis_sweepで起点のintent別カウンタを40へseed→handle→request_executionがINCRして41(>40)でdeny→1件目からstatus='skipped'・skip_reason='intent_daily'・API呼び出し0回・**denyもカウンタが増える**(seed40→41の確認=INCR先行)
6. **test_6_reselection_rules**: llm_failureのskipped行は即再選択(直後のhandleで評価される)/ intent_dailyのskipped行は同日は不可・FakeClockをJST翌日へ進めると再選択(updated_at < jst_day_start分岐)
7. **test_7_record_execution_breakdown**: 成功時 `jev:exec:{day}:typesafe_jev` が+1・月次キー(`jev:monthly:*`)は不在。双障害時は「第一候補がLLMTimeoutErrorを投げるスタブ + フォールバックが `fail_jev=True` のStubLLM」で組んだGateway(`LLMGateway(clock, parser=stub, embedding=stub, jev=第一候補スタブ, jev_fallback=フォールバックスタブ)`)→skipped/llm_failureで `jev:exec:{day}:fallback_llm` が+1(双障害は「最後に実際に呼んだ経路」を計上 — design §2.5-3)
8. **test_8_stage1_wiring_to_jev**: embedding_completed Eventをstage1.intakeへ投入→match_candidates生成(processed)→`Worker(clock=…, bus=_FakeBus相当, stage1=実Stage1, engine=db_engine, embedding=実EmbeddingWorker, jev=実JevWorker)`…は重いので **`_dispatch` 相当の直接呼び出しで確認**(ws-4試験5と同じ流儀: stage1.intake→`_kick_jev`相当=`JevWorker.handle` の直接呼び出し)→評価行の存在確認(workerプロセス起動不要)
9. **(注記 — 試験を置かない)**: design §4.2-9「既存全数(test-ci 818)のグリーン維持」はスーパーバイザー検証時のtest-ciで確認するため、本ファイルには試験を置かず報告書の「test-ci=スーパーバイザー検証待ち」記録で担う。**よって収集数は8試験**(完了条件2の期待値)

(実装詳細: JevWorker構築は `JevWorker(engine=db_engine, clock=fake_clock, gateway=stub_gateway, guard=JevCostGuard(store=JevCostStore(redis_client, key_prefix=redis_sweep), clock=fake_clock), cost_store=JevCostStore(redis_client, key_prefix=redis_sweep))`。stub Gatewayは `build_worker_gateway` を通さず `LLMGateway(clock, parser=stub, embedding=stub, jev=stub, jev_fallback=stub)` を直接組んでよい〔stub modeと同一構成〕)

- [ ] **Step 2: 収集確認(実行はしない)**

Run: `cd backend && uv run pytest --collect-only tests/integration/test_matching_jev.py -q`
Expected: 8件収集・exit 0
Run: `make lint && make test`
Expected: PASS(unitは外部プロセス不要のためintegrationのimport検査のみで完了)

- [ ] **Step 3: 完了条件7項目を検証する**

1. `make lint && make test` — exit 0(全件数を記録)
2. `cd backend && uv run pytest --collect-only tests/integration/test_matching_jev.py -q` — 8件・exit 0
3. `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` — ヒットが `core/clock.py` のみ + `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` — exit 0
4. `git diff --stat main -- backend/alembic/versions/0001_initial_schema.py backend/alembic/versions/0002_geofeatures.py docs` — 出力なし
5. (報告書コミット後に再確認)`git diff --name-only main | sort` — §4の31ファイルと完全一致 + `git status --short` — 空
6. `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし
7. `make test` の全件数を報告書へ記録(既存818+本単位追加。§4列挙以外の期待値変更がないことの突合)

- [ ] **Step 4: 報告書を作成する**

§7の形式で `docs/plans/M2/ws-5-report.md` を作成・記入する(design §5実装時確認事項〔PyPIパッケージ名・retry引数名〕の結果・固定値の変更有無・引継ぎ事項・スーパーバイザー検証手順を含む)。

- [ ] **Step 5: コミット**

```bash
git add backend/tests/integration/test_matching_jev.py docs/plans/M2/ws-5-report.md
git commit -m "docs: ws-5報告書とintegration試験(test-ci=スーパーバイザー検証待ち)"
git status --short  # 空であることを最終確認
```

完了後の最終返信は「報告ファイルのパス+完了条件7項目の結果一覧」。工作はここまで — スーパーバイザー検証(`docker compose build api` → `make migrate` → `make test-ci` → `make jev-smoke`)は実装側では実行しない。

---


