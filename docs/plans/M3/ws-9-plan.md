# M3 ws-9(G3ハーネス)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** G3完了条件4項のうち機械側の判定材料(条件1〜3)を揃える — ①integration新規1ファイル `test_g3_acceptance_e2e.py` に6試験を新設(02#13 muted回答可能チェーン・02#22 公開設定2値A/B+02#23 座標非開示・09 §3 期限確定/競合クローズ・グループ側/部分成立defer側・FR-17 自己重複集合)②m2ws1-/m2ws2-系users teardown漏れの恒久対策として既存2ファイルのuser_env teardownへusers削除1行ずつ追加。**製品コード(src/)・マイグレーション・Makefile・compose・frontendへの変更は一切ゼロ**(design大前提)。

**Architecture:** design §2.2案A — 新設6試験は単一ファイルへ集約(G3判定資料のトレース性を最優先・対応表と試験IDが1:1)。ヘルパー(_user/_intent/_structured/_seed_jev/_handle/_latch/_clock等)はtest_notifications_api・test_latches_api・test_expiry_batches・test_matching_groupengineの確立形式を自前複製する(共通モジュール化は既存100超試験へ波及するため本単位外・design §2.6-6)。パイプライン系(試験1・2・6)はrun_candidate_retrieval+_seed_jev+LatchEngine/GroupEngine.handleのテストプロセス内直接構成(Workerプロセスは立てない)。回答・期限系(試験3〜5)はlatches行を_latch直接INSERT(ws-1流儀・回答APIはlatches行しか読まない)。

**Tech Stack:** 変更なし(Python 3.13 / pytest+caplog / SQLAlchemy text()生SQL / httpx AsyncClient / FakeClock)。依存追加なし・alembic head=0006不変。

**Spec:** `docs/plans/M3/ws-9-design.md`(agent1設計メモ。**未解決論点なし** — §5の7件は2026-10-02 supervisor承認済み・STATUS 807177dコミットに承認記録。design.mdが本計画より優先)

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m3-ws-9`。**agent3はworktree内でコミットする**(mainへの直接コミット・pushは禁止。マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する(**依存追加・マイグレーション追加ともになし**のためlockもalembicも進まない)。
- **【最重要】共有ci-dbへの `make test-ci` / `make migrate` は実行禁止**(STATUS運用ルール1)。本単位はマイグレーションを追加せずM3最終単位で並走もなしだが、**integration試験の実行自体がスーパーバイザー検証時まで禁止**であることに変わりはない(共有ci-db・STATUS運用ルール1〜3)。開発は **unit相当の検証(`make lint && make test`・`--collect-only`・`uv run python -m py_compile`)のみ**で進める。報告ファイルの該当欄には **「test-ci=スーパーバイザー検証待ち」** と記録する。`docker compose build api` 等のdocker系コマンドも不要(test-ciを実行しないため)。
- **本単位はテストのみの新設でTDDの「失敗→実装」サイクルが適用不能**(被テスト実装は既にmainにある)。各Taskの検証ステップは「①構文確認(py_compile/ruff) ②収集確認(--collect-only) ③unit全件グリーン(make test)」で代替する(design §4.1-1の収集と実行の分離)。
- **テストファイルのbasename一意**(STATUS運用ルール5): **計画時点で機械確認済み(2026-10-02)**: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空(既存114ファイル)。新規 `tests/integration/test_g3_acceptance_e2e.py` は `find backend/tests -name "test_g3_acceptance_e2e.py"` が0件(既存に同名なし)。各Taskのコミット前に同コマンドが空であることを再確認する。
- **alembic・依存・Makefile・compose変更禁止**(design §3.3): Task 5で `git diff main -- backend/src backend/alembic backend/pyproject.toml backend/uv.lock Makefile compose.yaml docker frontend` が空であることを検証する。head=0006不変。
- **固定値の遵守**: design §2(採用判断)と本計画§9のIF確定事項は固定値。**変更した場合は必ず報告ファイルに「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`test:` `docs:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| G3完了条件4項(①02#13〜#23グリーン〔#22は公開設定2値分岐・#13はmuted含むドライラン確認〕②グループLATCH 6検証グリーン〔集合生成・全員YES成立・部分成立禁止・期限確定・通知順序・競合クローズ〕③追加試験グリーン〔D-10・D-23・FR-21・FR-17〕④プロトタイプ6画面) | 12 §3 M3・design §1.2-D0 |
| ci=最小構成で機能試験(毎コミット)。LLM呼び出しはスタブで決定的に。FCMはドライラン(送信内容を記録のみ)とし、通知の到達と本文の表示規制(FR-21)は記録で検証 | 10 §1 |
| #13: 閾値超過の候補(お知らせ設定がproposals_onlyのIntentとmutedのIntentの双方)。合格証拠=通知の到達記録。mutedのIntentでは通知ドライラン記録に提案通知が出力されないこと、latches.statusがproposedへ遷移しホームのLATCH候補から回答可能なこと | 02 §4・design §1.2-A |
| #22: 同一候補をvisibility=hidden_until_matchとsummary_onlyの双方で配置し、提案画面の内容を比較。hidden_until_matchでは条件サマリも表示されず(headcount・一致度・回答期限のみ)、summary_onlyでは条件サマリのみ表示。いずれも名前・プロフィールは成立まで表示されない | 02 §4・01 第21節・design §1.2-A |
| #23: 提案画面の位置表現。座標精度の値が表示されない | 02 §4・design §1.2-A |
| グループLATCH・期限時の確定条件: 全員YESが揃わないままresponse_deadlineを経過→expiredで閉じる(3人YESでも成立しない) | 09 §3・design §1.2-B |
| グループLATCH・競合クローズ: あるIntentが属する2つの提案を用意し、片方を成立させる→もう片方はcancelledで閉じる | 09 §3・design §1.2-B |
| グループLATCH・部分成立の禁止: 一部がno**またはdefer**、残りがyes→rejectedで閉じ、部分成立しない | 09 §3・design §1.2-B |
| FR-17配置ケース②自己重複集合の除外: 同一ユーザーのIntentを2つ含むような3〜4人集合の構成要件を作り、group_candidatesに当該集合が生成されないこと(06 第7節Hard互換条件・05 第3節集合内user_id一意) | 10 §3・design §1.2-C |
| 証拠=API応答・DB記録・通知ドライラン記録。時刻参照はすべてClock経由(FakeClock注入で実時間待ちを排除)。期限系はClock操作/DB値操作で再現 | 10 §1・§5・C2 |
| stagingはM4-1構築のため本単位はci環境で実施(G2解釈④と同型・G3判定時ユーザー確認=STATUS待ち事項2) | design §1.2-D4・§5-1 |
| visibility生成分岐: 双方summary_only→全フィールド(time_summary/area_name/headcount/category_primary/category_secondary/budget/match_level)。いずれかhidden→headcount+match_levelのみ | 05 §2・06 §2・design §2.3試験2 |
| mutedのIntentは通知を送らない(遷移は通常どおりproposedまで) | 06 §2・§6・C13 |
| 自己ペア/自己集合除外はLayer 1(`i.user_id <> :origin_user_id`・`i1.user_id <> i2.user_id`)と貪欲法のuser相異・INSERT前検査の多層防御 | 06 §2・06 第7節・design §2.3試験6 |

## 2. グローバル制約(全タスクに暗黙に適用)

- **製品コード変更ゼロ**: `backend/src/latch/` 配下には1行も触れない(breakerのような実装追加はしない。不足は試験のみ・design大前提)。変更した時点で計画違反。
- **integration試験は実行しない**(§0): 収集確認(`--collect-only`)と構文確認まで。実行はスーパーバイザー検証時の `make test-ci` のみ。
- **DB干渉対抗策**(design §4.1-3): SUBJECT_PREFIX=`m3ws9-`(2026-10-02実査で衝突なし)・field fixture(FK順完全削除・§8 Task 1のSQL一式)・BASE_HOURS=120(now+5日系)・FAR_EXPIRES_H=144。期限切れ再現はDB値操作(deadline過去化)で実時間待ちを作らない。
- **時刻はClock経由**: テストプロセス内の構成要素(LatchEngine/GroupEngine/ExpirySweeper)にはFakeClockを注入。api常設プロセスのClock(SystemClock)は差し替え不能のため、api経由の操作と干渉する時刻はSystemClock基準で作る(_latchヘルパーのnow=SystemClock・test_latches_api流儀)。
- **latches行の直接INSERTは_latchヘルパーのみから**(test_latches_api §4.2流儀。回答APIはlatches行しか読まないためパイプラインを走らせない)。
- **`make lint`(ruff E,F,I,UP,B・format行長88)と `make test` を毎コミット通す**。unit 1215件が基線(新規はintegrationのみのため件数不変)。
- **全タスクのコミット前に**: `make lint && make test` グリーン・`git status --short` で差分が§4の一覧どおり・basename一意コマンドが空。

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **GET /v1/latches/{id}応答キー集合の過不足(supervisor引継ぎ事項)** — LatchDetailOutはLatchSummaryOut(10フィールド)を継承しparticipants/time_summary/area_nameを追加した**13キー**。proposedではparticipants/time_summary/area_nameは**None値だがキーとして存在する**(`response_model_exclude_none`なし・routes.py 62行の`response_model=LatchDetailEnvelope`を確認済み)。キー集合を過不足なく列挙しないと「座標的フィールドが構造上存在しない」否定的証明にならない → Task 1試験2の⑤(set(body)==EXPECTED_DETAIL_KEYS 13キー完全一致+None値3キーの明示assert)
2. **A/B比較の「同一条件」崩れ(#22)** — 比較の対象はvisibility差のみである必要がある。時間・地域・カテゴリ・予算が両組で異なると「キー集合の差分==条件サマリ5情報」の比較が不等価比較になる → Task 1試験2の配置(両組同一start/location/secondary/budget_maxを変数共有で固定)
3. **muted検証の偽陽性(#13)** — caplogは試験関数スコープで始まるが、試験内でhandleを複数回呼ぶと記録が混入する。「muted側0件」が他のhandle由来で壊れないよう、試験1のhandle呼び出しは1回のみに固定し `[r["user_id"] for r in recs] == [uid_a]` で完全列挙検証 → Task 1試験1の①
4. **sweeper戻り値の環境依存(試験3)** — `run_once()` の戻り値は遷移させた行数(latches+intents+completedの合計)で、DBに他試験の期限切れ残骸があれば>1になる。field teardownのFK順削除+時間窓分離(now+5日系で期限切れを作らない)が前提 → Task 2試験3では`== 1`をassert(既存test_expiry_batches test_3と同一前提)し、意図せず不一致なら報告書に記録
5. **自己重複集合試験の「対照」欠落(試験6)** — 「{s,s2}を含む集合が0件」は検証環境が壊れていても(そもそも集合生成が動かなくても)通ってしまう偽陰性 → {s,V,W}の3人集合が実際に生成されることを対照assert(検証環境が通過状態であることの保証)+`ctx is not None` → Task 3試験6の②

---

## 4. スコープ(作成・変更するファイル一覧)

作成(§0のbasename機械確認済み・design §3.1):

```text
backend/tests/integration/test_g3_acceptance_e2e.py   (Task 1〜3で6試験を段階構築)
docs/plans/M3/ws-9-report.md                          (Task 5。報告ファイル)
```

変更(design §3.2):

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/tests/integration/test_events_pipeline.py` | user_env fixtureのteardown末尾(intents削除の後)へusers削除1行追加(§2.4恒久対策) | 4 |
| `backend/tests/integration/test_embedding_pipeline.py` | 同上 | 4 |

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`。

## 5. 禁止(触ってはいけないもの・スコープ外と判断する基準)

- **`backend/src/latch/` 配下すべて**(design §3.3・本単位の大前提)。`backend/alembic/`(head=0006不変)・`Makefile`・`compose.yaml`・`docker/`・`frontend/`・`prototype/`・`backend/pyproject.toml`・`backend/uv.lock`・`.env`・`.env.example`。
- **`docs/` の既存ファイル**(01〜12・learn・testassets・reviews)。`docs/plans/STATUS.md`(スーパーバイザー管理)。`docs/plans/` の既存ファイル(M0/M1/M2/M3のdesign・plan・report)。G3証拠YAML等のtestassets新規資産も作らない(design §2.6-2・§3.3)。
- **`backend/tests/` の§4に列挙した以外の既存試験**(test_latches_api・test_expiry_batches・test_notifications_api・test_matching_groupengine等への追加変更なし。対応付けのみ・design §2.1)。
- **`make test-ci` / `make migrate` / docker系コマンド全般**: §0のとおり(実行はスーパーバイザー検証時)。
- **実API呼び出し**: `make g1-gate`・`make g2-gate`・`make embed-smoke`・`make jev-smoke` は起動しない。
- スコープ外と判断する基準(必要になったと感じても作らない — design §1.4・§2.6):
  - **staging環境の構築と27項目フル実行・非機能試験** → M4-1/M4-3
  - **FCM実送信・Firebase資材** → G3判定の待ち事項1(人間領域)。本単位はStubPushSender経路のみ
  - **#24〜#26・MLR日次集計・Observability** → M4-5(G3条件外)
  - **新規makeターゲット(g3-gate等)** → G3証拠はtest-ci+frontend vitestで足りる(design §2.6-1)
  - **テスト資産YAMLの新設・実機ブラウザE2E(Playwright等)** → design §2.6-2/3(10 §5の証拠定義=API応答・DB記録・通知ドライラン記録の範囲内)
  - **Worker経由の一気通貫試験の追加** → 試験1・2・6はエンジン直接構成で十分(M2 ws-8試験8・M3 ws-2試験7がイベント経路を実証済み・design §2.6-4)
  - **既存ヘルパーの共通モジュール化** → 既存試験への波及が大きく本単位外(design §2.6-6)
  - **m2ws1-/m2ws2-蓄積usersの既存分掃除** → 1回限りの掃除SQLは検証手順(スーパーバイザー検証時)に記載するのみ(design §1.4-7)
- mainブランチへのコミット・push・マージ。

## 6. 完了条件(テストで証明できる形。Task 5で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(unit 1215件不変 — 新規はintegrationのみのため)
   検証: `make lint && make test` — ともにexit 0・全件数を報告書に記録
2. **integration新規6試験が収集できる**(実行はスーパーバイザー検証時)
   検証: `cd backend && uv run pytest --collect-only tests/integration/test_g3_acceptance_e2e.py -q` — exit 0・**6件収集**。かつ全体で `uv run pytest --collect-only -q` が**1475件**(=1469+6)
3. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし(空)
4. **製品コード・マイグレーション・依存・Makefile・compose・frontendに差分なし**
   検証: `git diff main -- backend/src backend/alembic backend/pyproject.toml backend/uv.lock Makefile compose.yaml docker frontend prototype` — 出力なし(空)
5. **変更ファイルが§4の一覧どおり(作成2+変更2=4ファイル)**
   検証: Task 5の報告コミット後に `git diff --name-only main | sort` が§4の一覧(report込み)と完全一致。`git status --short` が空
6. **`make test-ci` は実行していないこと**(§0規律・報告書に「test-ci=スーパーバイザー検証待ち」と記録)
7. **teardown恒久対策がテストコードに反映されていること**(design §2.4): 両ファイルのuser_env teardown末尾に `DELETE FROM users WHERE id = CAST(:uid AS uuid)` が存在することを `rg -n "DELETE FROM users" backend/tests/integration/test_events_pipeline.py backend/tests/integration/test_embedding_pipeline.py` で確認(各1ヒット)。効果実証(m2ws1-%/m2ws2-% users==0件)はスーパーバイザー検証時

## 7. 報告形式

**結果ファイル**: `docs/plans/M3/ws-9-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M3 ws-9(G3ハーネス)実行報告

- ブランチ: m3-ws-9 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る(全件数も。期待unit 1215不変)> |
| 2 | integration収集 | PASS/FAIL | <collect-only出力(期待: 新規6件・全体1475)> |
| 3 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 4 | 製品コード等無変更 | PASS/FAIL | <git diff出力(空なら「空」)> |
| 5 | 変更ファイル=§4の4ファイル | PASS/FAIL | <git diff --name-only出力> |
| 6 | test-ci未実行 | PASS | test-ci=スーパーバイザー検証待ち(§0規律) |
| 7 | teardown恒久対策反映 | PASS/FAIL | <rg出力(両ファイルへ1ヒット)> |

## design §5 承認事項の実装対応
- §5-1(ci環境実施・supervisor前提①): 試験はci構成(実DB・api常設・StubPushSender)で書いた。staging再実行はM4-3
- §5-2(単一ファイル集約): test_g3_acceptance_e2e.pyへ6試験集約
- §5-3(#22の観測レイヤー=API応答+frontend vitest): 試験2はAPI応答のキー集合比較。実機ブラウザE2Eなし
- §5-4(競合クローズ=グループ成立×1対1): 試験4の構成どおり
- §5-5(期限確定=4人組3人YES): 試験3の構成どおり
- §5-6(teardown恒久対策を本単位へ): Task 4で2ファイルへusers削除1行追加
- §5-7(#24〜#26・MLR・非機能はG3資料に含めない): 対応表・報告書に含めていない

## 固定値の変更有無(本計画§9)
- SUBJECT_PREFIX=m3ws9-・期待キー集合(13キー/7キー/2キー): 変更なし / 変更あり
- ヘルパー形式・SQL: 変更なし / 変更あり(<前→後+理由>)
- その他§9のIF確定事項: 変更なし / 変更あり(<前→後+理由>)

## (スーパーバイザー検証時の手順・結果記入欄・design §4.2)
1. `make lint && make test` — unit 1215 passed(不変)
2. `docker compose build api worker` — 運用ルール4(形式どおり)
3. `make test-ci` — **1475 passed**(=1469+6)・purge-match-sub実行をMakefile出力で確認
4. `cd frontend && npm test` — 217 passed(G3条件4の証拠化・再実行)
5. 残存確認: m3ws9-系は従来手順+**m2ws1-%/m2ws2-% users==0件**(<SQL結果。1回目は既存蓄積分の掃除SQLを先行実行>)
   ```sql
   DELETE FROM users WHERE auth_subject LIKE 'm2ws1-%' OR auth_subject LIKE 'm2ws2-%';  -- 既存蓄積分(1回のみ)
   ```
   の後にもう一度test-ciを実行し、`SELECT COUNT(*) FROM users WHERE auth_subject LIKE 'm2ws1-%' OR auth_subject LIKE 'm2ws2-%';` が0件
6. lint再実行(test-ciにlintが含まれないため・ws-4運用メモ)
7. basename一意確認・alembic head=0006不変・時刻参照がclock.pyのみ(arch test)
8. G3判定資料への反映: design §2.1対応表+test-ci/frontendの実行ログをSTATUS「G3判定資料」節へ集約(G2判定資料と同型)

## コミット一覧
<git log --oneline main..HEAD>

## 補足(詰まった点・判断した点があれば)
<自由記述>
```

---

## 8. 実装ステップ(テスト新設のためTDDの代わりに「作成→構文→収集→unit全件」サイクル。Task 1〜5の順で実行する)

### Task 1: test_g3_acceptance_e2e.py — field fixture・ヘルパー一式・試験1・試験2

**Files:**
- Create: `backend/tests/integration/test_g3_acceptance_e2e.py`

**Interfaces:**
- Consumes: `run_candidate_retrieval`(latch.worker.matching.runner)・`LatchEngine`・`GroupEngine`・`ExpirySweeper`・`StubPushSender`・`latch.notifications.records.LOGGER_NAME`・`FakeClock/SystemClock`・`GeoService`(すべてmain実装・変更なし)
- Produces: 本Taskで確定するファイル共通規律とヘルパー(後続Taskが同じファイルへ追記する): `SUBJECT_PREFIX="m3ws9-"`・`field` fixture・`_cli_idp_token`・`_user`・`_future`・`_vec`/`E1`・`_structured`・`_intent`・`_seed_jev`・`_latch_engine`・`_handle`・`_push_records`・`_clock`・`_user_id_of`・`_notifications_rows`・`_latch_id_of`・`_latch`・`_respond`・`_latch_row`・`_events`・`_sweeper`・`_group_of`・`_pair_row`・`_all_keys`(Task 2・3の試験3〜6が使う)

- [ ] **Step 1: 試験ファイルを作成する(docstring・fixture・ヘルパー一式+試験1・2)**

`backend/tests/integration/test_g3_acceptance_e2e.py` を作成。**ヘルパーは既存4ファイルの形式の自前複製**(design §2.2 — 引数形式・unpack・SQLを§10の引用どおり正確に写すこと):

```python
"""G3受け入れE2Eのintegration試験(M3 ws-9 design §2.3・12 §3 M3のG3条件1〜3)。

実DB(compose常設)・実Redis・api常設(実HTTP)。新設6試験は既存試験が
持ち切れないG3文言の一気通貫分(02#13のmuted回答可能チェーン・02#22の
公開設定2値A/B比較+02#23座標非開示・09 §3の期限確定/競合クローズ
グループ成立側/部分成立defer側・FR-17自己重複集合)。ヘルパーは
test_notifications_api・test_latches_api・test_expiry_batches・
test_matching_groupengineの確立形式の自前複製(共通化リファクタは
既存試験への波及が大きく本単位外 — design §2.2)。
実行はスーパーバイザー検証時のmake test-ci(STATUS運用ルール1〜3)。
"""

import asyncio
import json
import logging
import sys
import uuid as uuid_mod
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.geo.service import GeoService
from latch.notifications.records import LOGGER_NAME
from latch.notifications.sender import StubPushSender
from latch.worker.matching.group_engine import GroupEngine
from latch.worker.matching.latch_engine import LatchEngine
from latch.worker.matching.runner import run_candidate_retrieval
from latch.worker.sweeper import ExpirySweeper

pytestmark = pytest.mark.integration

CATEGORY = "meal"
BASE_HOURS = 120  # now+5日系(既存単位と同一の対抗策・時間窓分離)
FAR_EXPIRES_H = 144
SUBJECT_PREFIX = "m3ws9-"


@pytest.fixture
async def field(db_engine):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除。"""
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        for sql in (
            "DELETE FROM latch_status_events WHERE latch_id IN"
            " (SELECT id FROM latches WHERE intent_ids && ARRAY("
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p)))",
            "DELETE FROM calibration_records WHERE latch_id IN"
            " (SELECT id FROM latches WHERE intent_ids && ARRAY("
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p)))",
            "DELETE FROM notifications WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)",
            "DELETE FROM latches WHERE intent_ids && ARRAY("
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM match_candidates WHERE intent_a_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            " OR intent_b_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
            "  SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM match_events WHERE source_intent_id IN"
            " (SELECT id FROM intents WHERE user_id IN"
            "  (SELECT id FROM users WHERE auth_subject LIKE :p))",
            "DELETE FROM intents WHERE user_id IN"
            " (SELECT id FROM users WHERE auth_subject LIKE :p)",
            "DELETE FROM users WHERE auth_subject LIKE :p",
        ):
            await conn.execute(text(sql), p)


# -- ヘルパー(4ファイルの確立形式の自前複製・design §2.2) --


async def _cli_idp_token(provider: str, subject: str) -> str:
    """テストIdPでJWT発行(認証サービスのci構成・各integrationファイルが
    自前定義する流儀 — tests配下に__init__.pyがないためimport不可)。"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        provider,
        "--subject",
        subject,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    return stdout.decode().strip()


async def _user(api_client, prefix: str, birth_date: str = "1990-04-01"):
    subject = f"{prefix}{uuid_mod.uuid4().hex[:8]}"
    idp = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "m3ws9", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    return (SystemClock().now() + timedelta(hours=hours)).isoformat()


def _vec(*components: float) -> str:
    """768次元ベクトル文字列(先頭要素のみ値・残り0 — groupengine流儀)。"""
    vals = [0.0] * 768
    for i, c in enumerate(components):
        vals[i] = c
    return "[" + ",".join(repr(v) for v in vals) + "]"


E1 = _vec(1.0)  # 全員同一(similarity=1)


def _structured(
    *,
    start: str | None = None,
    expires: str | None = None,
    visibility: str = "summary_only",
    notification_level: str | None = None,
    secondary: str | None = None,
    budget_max: int | None = None,
    participants: tuple[int, int] | None = None,
) -> dict:
    """visibilityは常時明示(groupengine流儀 — DB既定がhidden_until_matchの
    ため省略すると意図しない分岐になる・0001初期スキーマ実査)。"""
    d: dict = {
        "category": {"primary": CATEGORY, "secondary": secondary},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
        "visibility": visibility,
    }
    if expires is not None:
        d["expires_at"] = expires
    if notification_level is not None:
        d["notification_level"] = notification_level
    if budget_max is not None:
        d["budget"] = {"max": budget_max}
    if participants is not None:
        d["participants"] = {"min": participants[0], "max": participants[1]}
    return d


async def _intent(api_client, db_engine, headers, structured) -> dict:
    """active Intentを作りembeddingを直接挿入(ws流儀のfixture方針)。"""
    resp = await api_client.post(
        "/v1/intents",
        headers=headers,
        json={
            "raw_text": "分類用テキスト",
            "status": "active",
            "structured_intent": structured,
        },
    )
    assert resp.status_code == 201, resp.text
    intent = resp.json()["intent"]
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE intents SET embedding = CAST(:v AS vector)"
                " WHERE id = CAST(:id AS uuid)"
            ),
            {"v": E1, "id": intent["id"]},
        )
    return intent


async def _seed_jev(db_engine, a_id: str, b_id: str, wa: float, wb: float) -> None:
    """match_candidatesへjev_result付きevaluatedを直接投入。"""
    jev = json.dumps(
        {
            "would_a_accept_b": wa,
            "would_b_accept_a": wb,
            "provider": "fixture",
            "model": None,
        }
    )
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text(
                "UPDATE match_candidates SET jev_result = CAST(:jev AS jsonb),"
                " status = 'evaluated'"
                " WHERE status = 'pending'"
                " AND ((intent_a_id = CAST(:a AS uuid)"
                " AND intent_b_id = CAST(:b AS uuid))"
                " OR (intent_a_id = CAST(:b AS uuid)"
                " AND intent_b_id = CAST(:a AS uuid)))"
            ),
            {"jev": jev, "a": a_id, "b": b_id},
        )
        assert res.rowcount == 1, "jev直接投入対象行なし"


def _latch_engine(db_engine, clock, push=None) -> LatchEngine:
    return LatchEngine(
        engine=db_engine, clock=clock, geo=GeoService(db_engine), push=push
    )


async def _handle(db_engine, clock, intent_id: str, push=None) -> None:
    await _latch_engine(db_engine, clock, push).handle(uuid_mod.UUID(intent_id))


def _push_records(caplog) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == LOGGER_NAME]


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


async def _user_id_of(db_engine, intent_id: str) -> str:
    async with db_engine.connect() as conn:
        return str(
            (
                await conn.execute(
                    text("SELECT user_id FROM intents WHERE id = CAST(:id AS uuid)"),
                    {"id": intent_id},
                )
            ).scalar_one()
        )


async def _notifications_rows(db_engine, user_id: str) -> list[tuple]:
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, type, read_at, created_at FROM notifications"
                    " WHERE user_id = CAST(:u AS uuid)"
                    " ORDER BY created_at DESC, id DESC"
                ),
                {"u": user_id},
            )
        ).fetchall()
    return rows


async def _latch_id_of(db_engine, intent_ids: list[str]) -> str:
    """指定Intent群を含むlatches行のid(test_3(notifications)流儀の一般化)。"""
    arr = ", ".join(f"CAST('{i}' AS uuid)" for i in intent_ids)
    async with db_engine.connect() as conn:
        return str(
            (
                await conn.execute(
                    text(
                        f"SELECT id FROM latches"
                        f" WHERE intent_ids && ARRAY[{arr}]::uuid[]"
                    )
                )
            ).scalar_one()
        )


async def _latch(
    db_engine,
    intent_ids: list[str],
    *,
    status: str = "proposed",
    score: float = 0.85,
    deadline_hours: float = 2.0,
    expires_hours: float = 120.0,
    proposal: dict | None = None,
) -> str:
    """latches行を直接INSERT(test_latches_api流儀。回答APIはlatches行のみ読む)。

    now基準はSystemClock(api常設プロセスのClockと同一・回答APIと干渉しない)。
    """
    now = SystemClock().now()
    if proposal is None:
        proposal = {"headcount": len(intent_ids), "match_level": "medium"}
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text("""
                INSERT INTO latches
                    (intent_ids, proposal, score, status,
                     response_deadline, expires_at, created_at)
                VALUES (CAST(:ids AS uuid[]),
                        CAST(:proposal AS jsonb), :score, :status,
                        CAST(:deadline AS timestamptz),
                        CAST(:expires AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": [uuid_mod.UUID(i) for i in intent_ids],
                "proposal": json.dumps(proposal, ensure_ascii=False),
                "score": score,
                "status": status,
                "deadline": now + timedelta(hours=deadline_hours),
                "expires": now + timedelta(hours=expires_hours),
                "now": now,
            },
        )
    return str(res.first()[0])


async def _respond(api_client, headers, latch_id: str, response: str):
    return await api_client.post(
        f"/v1/latches/{latch_id}/response",
        headers=headers,
        json={"response": response},
    )


async def _latch_row(db_engine, latch_id: str) -> dict:
    async with db_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT status, responses FROM latches"
                        " WHERE id = CAST(:l AS uuid)"
                    ),
                    {"l": latch_id},
                )
            )
            .mappings()
            .first()
        )
    assert row is not None
    return dict(row)


async def _events(db_engine, latch_id: str) -> list[tuple]:
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT from_status, to_status, user_id FROM latch_status_events"
                    " WHERE latch_id = CAST(:l AS uuid) ORDER BY created_at"
                ),
                {"l": latch_id},
            )
        ).fetchall()
    return [(str(r[0]), r[1], None if r[2] is None else str(r[2])) for r in rows]


def _sweeper(db_engine, clock):
    """実LatchEngineをdrain対象に持つExpirySweeper(test_expiry_batches流儀)。"""
    latch_engine = LatchEngine(engine=db_engine, clock=clock)
    return ExpirySweeper(
        engine=db_engine, clock=clock, latch=latch_engine, batch_limit=50
    )


async def _group_of(db_engine, intent_ids: list[str]):
    """指定メンバー(sorted)と一致するgroup_candidates行(開いている行優先)。"""
    arr = ", ".join(f"CAST('{i}' AS uuid)" for i in sorted(intent_ids, key=str))
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    f"SELECT id, status, member_scores, aggregate_score"
                    f" FROM group_candidates"
                    f" WHERE intent_ids = ARRAY[{arr}]::uuid[]"
                    f" ORDER BY (status IN ('candidate','proposed')) DESC,"
                    f" updated_at DESC LIMIT 1"
                )
            )
        ).first()


async def _pair_row(db_engine, a: str, b: str):
    lo, hi = sorted([a, b], key=str)
    async with db_engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT id, status, jev_result, latch_score FROM"
                    " match_candidates WHERE intent_a_id = CAST(:a AS uuid)"
                    " AND intent_b_id = CAST(:b AS uuid)"
                    " ORDER BY created_at DESC LIMIT 1"
                ),
                {"a": lo, "b": hi},
            )
        ).first()


def _all_keys(obj) -> set:
    """dictを再帰的に歩いて全キーを集める(値リスト内のdictも。試験2用)。"""
    keys: set = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            keys |= _all_keys(item)
    return keys


# -- design §2.3 試験1〜6 --
# (試験3〜6はTask 2・3でこの位置へ追記する)


async def test_1_muted_notification_chain_answerable(
    api_client, db_engine, field, caplog
):
    """02#13 muted含むドライラン+回答可能チェーン(G3条件1・design §2.3試験1)。

    A(proposals_only)×B(muted)の閾値超過候補: ①push記録はA側1件のみ
    (mutedのIntentではドライラン記録に提案通知が出ない)②notifications行も
    B側0行③GET /v1/latchesのB視点一覧に当該latchがstatus=proposedで含まれる
    (ホームのLATCH候補)④Bの回答が200で受理されmy_response="yes"(回答可能)。
    既存test_3(notifications)との差分=③④(同試験はDBのstatus確認まで)。
    """
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha = await _user(api_client, field)
    a = await _intent(
        api_client,
        db_engine,
        ha,
        _structured(
            start=start, expires=expires, notification_level="proposals_only"
        ),
    )
    hb = await _user(api_client, field)
    b = await _intent(
        api_client,
        db_engine,
        hb,
        _structured(start=start, expires=expires, notification_level="muted"),
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.85)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _handle(db_engine, clock, a["id"], push=StubPushSender(clock=clock))
    recs = _push_records(caplog)
    uid_a = await _user_id_of(db_engine, a["id"])
    uid_b = await _user_id_of(db_engine, b["id"])
    # ①muted側(b)の提案通知はドライラン記録に出ない(完全列挙)
    assert [r["user_id"] for r in recs] == [uid_a]
    for r in recs:
        assert r["notification_type"] == "proposal"
        assert r["status"] == "ok"
    # ②notifications行もb側0行
    assert len(await _notifications_rows(db_engine, uid_b)) == 0
    # ③ホームのLATCH候補: B視点の一覧にstatus=proposedで含まれる
    latch_id = await _latch_id_of(db_engine, [a["id"], b["id"]])
    resp = await api_client.get("/v1/latches", headers=hb)
    assert resp.status_code == 200, resp.text
    items = [i for i in resp.json()["items"] if i["id"] == latch_id]
    assert len(items) == 1
    assert items[0]["status"] == "proposed"
    # ④Bの回答が200で受理(my_response="yes"・A未回答のためpartial_accept)
    r = await _respond(api_client, hb, latch_id, "yes")
    assert r.status_code == 200, r.text
    body = r.json()["latch"]
    assert body["my_response"] == "yes"
    assert body["status"] == "partial_accept"


# GET /v1/latches/{id}の応答キー規定集合(LatchDetailOut全13キー — §10-1と突合済み。
# proposedでは participants/time_summary/area_name は None 値だがキーとして存在)
EXPECTED_DETAIL_KEYS = {
    "id",
    "status",
    "response_deadline",
    "expires_at",
    "created_at",
    "completed_at",
    "proposal",
    "is_group",
    "my_response",
    "remaining_responses",
    "participants",
    "time_summary",
    "area_name",
}
# summary_only双方のproposalキー(proposal.build_proposal実装と突合済み・§10-6)
SUMMARY_PROPOSAL_KEYS = {
    "time_summary",
    "area_name",
    "headcount",
    "category_primary",
    "category_secondary",
    "budget",
    "match_level",
}
# raw_text相当・NG条件文言・表示名・プロフィールに対応するフィールド(不在の証明)
FORBIDDEN_KEYS = {
    "raw_text",
    "soft_constraints",
    "ng_conditions",
    "soft_texts",
    "display_name",
    "profile",
    "user_id",
    "bio",
    "name",
}
# 座標系フィールド(02#23: 座標精度の値が表示されないことの否定的証明)
GEO_KEYS = {
    "lon",
    "lat",
    "geo_lon",
    "geo_lat",
    "geo_center",
    "geo_radius_m",
    "latitude",
    "longitude",
    "coordinates",
    "location",
    "accuracy",
    "radius",
}


async def test_2_visibility_ab_comparison_and_no_coordinates(
    api_client, db_engine, field
):
    """02#22 公開設定2値のA/B比較+02#23 座標非開示(G3条件1・design §2.3試験2)。

    同一条件(時間・地域・カテゴリ・予算)の2組を実際のパイプライン
    (retrieval→jev投入→LatchEngine.handle)でproposed化し、GET /v1/latches/{id}
    を各組の参加者双方の視点で比較する。①組1(summary_only×2)のproposalは
    条件サマリ7キー②組2(hidden混在)は{headcount, match_level}のみ
    (「条件が合う候補があります」相当・一致度=match_level・回答期限=
    response_deadlineはproposal外のトップレベルフィールドで双方に存在)③差分==
    条件サマリ5情報のみ④raw_text相当・NG条件・表示名・プロフィールが応答全体に
    存在しない⑤応答トップレベルのキー集合が規定13キーと完全一致(座標的
    フィールドが構造上存在しないことの否定的証明。位置表現はproposal.area_name
    =逆転ジオコーディングの地名文字列のみ)。
    """
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    cases = [  # (a側visibility, b側visibility) — 以外の条件は両組完全同一
        ("summary_only", "summary_only"),
        ("hidden_until_match", "summary_only"),
    ]
    header_pairs = []
    latch_ids = []
    for va, vb in cases:
        ha = await _user(api_client, field)
        a = await _intent(
            api_client,
            db_engine,
            ha,
            _structured(
                start=start,
                expires=expires,
                visibility=va,
                secondary="焼肉",
                budget_max=5000,
            ),
        )
        hb = await _user(api_client, field)
        b = await _intent(
            api_client,
            db_engine,
            hb,
            _structured(
                start=start,
                expires=expires,
                visibility=vb,
                secondary="焼肉",
                budget_max=5000,
            ),
        )
        async with db_engine.begin() as conn:
            await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
        await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
        await _handle(db_engine, clock, a["id"])
        header_pairs.append((ha, hb))
        latch_ids.append(await _latch_id_of(db_engine, [a["id"], b["id"]]))
    # 参加者双方の視点で詳細取得(4視点)
    views: list[dict] = []
    for (h1, h2), lid in zip(header_pairs, latch_ids):
        for h in (h1, h2):
            resp = await api_client.get(f"/v1/latches/{lid}", headers=h)
            assert resp.status_code == 200, resp.text
            views.append(resp.json()["latch"])
    summary_views = views[:2]  # 組1(a1・b1視点)
    hidden_views = views[2:]  # 組2(a2・b2視点)
    # ①組1(summary_only×2): 条件サマリ7キー+値の検証
    for body in summary_views:
        assert set(body["proposal"]) == SUMMARY_PROPOSAL_KEYS
        p = body["proposal"]
        assert p["headcount"] == 2
        assert p["match_level"] == "high"  # min(0.9, 0.9)=0.90→high
        assert p["budget"] == {"max": 5000}
        assert p["category_primary"] == CATEGORY
        assert p["category_secondary"] == "焼肉"
        datetime.strptime(p["time_summary"], "%Y-%m-%d %H:%M")  # JST書式
        assert isinstance(p["area_name"], str) and p["area_name"]  # 地名(実geo)
    # ②組2(hidden混在): {headcount, match_level}のみ
    for body in hidden_views:
        assert set(body["proposal"]) == {"headcount", "match_level"}
        assert body["proposal"] == {"headcount": 2, "match_level": "high"}
    # ③キー集合の差分==条件サマリ5情報のみ
    assert SUMMARY_PROPOSAL_KEYS - {"headcount", "match_level"} == {
        "time_summary",
        "area_name",
        "category_primary",
        "category_secondary",
        "budget",
    }
    # ④禁止フィールド(raw_text相当・NG条件文言・表示名・プロフィール)不在
    for body in views:
        assert _all_keys(body) & FORBIDDEN_KEYS == set(), body
    # ⑤#23: トップレベル13キー完全一致+座標系キー不在+proposed詳細のNULL 3キー
    for body in views:
        assert set(body) == EXPECTED_DETAIL_KEYS
        assert body["status"] == "proposed"  # パイプラインが提案化した前提の保証
        assert body["participants"] is None  # 成立前の情報最小化
        assert body["time_summary"] is None  # トップレベル(成立後解放用)
        assert body["area_name"] is None  # トップレベル(成立後解放用)
        assert body["is_group"] is False
        assert body["remaining_responses"] == 2
        assert body["my_response"] is None
        assert body["response_deadline"] is not None  # 回答期限は双方に存在
        assert _all_keys(body) & GEO_KEYS == set(), body
```

- [ ] **Step 2: 構文・収集確認(実行はしない・§0規律)**

Run: `cd backend && uv run python -m py_compile tests/integration/test_g3_acceptance_e2e.py && uv run pytest --collect-only tests/integration/test_g3_acceptance_e2e.py -q`
Expected: exit 0・**2件収集**(test_1・test_2)

- [ ] **Step 3: unit全件の再確認(コミット前検査)**

Run: `make lint && make test`
Expected: グリーン(unit 1215件不変 — 本ファイルはintegrationマークのため `-m "not integration"` では収集されない)

- [ ] **Step 4: コミット**

```bash
git add backend/tests/integration/test_g3_acceptance_e2e.py
git commit -m "test: G3 acceptance e2e tests 1-2 (muted answer chain, visibility A/B)"
```

### Task 2: 試験3・試験4・試験5(期限確定・競合クローズ・defer部分成立)

**Files:**
- Modify: `backend/tests/integration/test_g3_acceptance_e2e.py`(Task 1が置いたコメント行 `# (試験3〜6はTask 2・3でこの位置へ追記する)` を削除し、ファイル末尾(試験2の直後)へ試験3〜5を追記)

**Interfaces:**
- Consumes: Task 1の `_latch`・`_respond`・`_latch_row`・`_events`・`_sweeper`・`_user`・`_intent`・`_structured`・`_clock`
- Produces: なし(試験のみ)

- [ ] **Step 1: 試験3〜5を追記する**

Task 1のコードブロック内のコメント行 `# (試験3〜6はTask 2・3でこの位置へ追記する)` を削除する。続けて、ファイル末尾(試験2の直後)へ以下の試験3〜5を追記する(定数定義 EXPECTED_DETAIL_KEYS 等の位置は変えない):

```python
async def test_3_group_deadline_expires_with_three_yes(api_client, db_engine, field):
    """09 §3 期限時の確定条件(G3条件2・design §2.3試験3): 4人組で3人YES
    (partial_accept)のままresponse_deadlineを経過→expiredで閉じる
    (3人YESでも成立しない=matchedにならない)。4人目の回答は409。

    deadline過去化はapi常設プロセスのClock(SystemClock)が差し替え不能な
    ためDB値操作(ws-1計画§9-2規律・test_expiry_batches試験3と同一手法)。
    FakeClockをset/advanceしないためrun_once内nowがsweeper構築時の
    last_tickと同一時刻になり(created_at > last_tick不成立)クローズ検知
    drainは観測しない(test_expiry_batches試験2と同型)。
    """
    hs = [await _user(api_client, field) for _ in range(4)]
    intents = [await _intent(api_client, db_engine, h, _structured()) for h in hs]
    ids = [i["id"] for i in intents]
    latch = await _latch(db_engine, ids)  # 4人組・proposed・deadline未来
    # 3人yes → partial_accept・remaining=1(3人YESでも成立しない)
    for h in hs[:3]:
        r = await _respond(api_client, h, latch, "yes")
        assert r.status_code == 200, r.text
    assert (await _latch_row(db_engine, latch))["status"] == "partial_accept"
    # deadlineを過去へ(sweeper遅延でstatus=partial_acceptのままの状態を再現)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE latches SET response_deadline ="
                " CAST(:past AS timestamptz) WHERE id = CAST(:l AS uuid)"
            ),
            {"past": SystemClock().now() - timedelta(hours=1), "l": latch},
        )
    clock = FakeClock(SystemClock().now())
    # ①run_onceが当該latchを処理(他試験残骸はteardown+時間窓分離で不在)
    assert await _sweeper(db_engine, clock).run_once() == 1
    # ②expired(matchedにならない)
    assert (await _latch_row(db_engine, latch))["status"] == "expired"
    # ③latch_status_events: partial_accept→expired・user_id=NULL(システム起因)
    assert ("partial_accept", "expired", None) in await _events(db_engine, latch)
    # ④4人目の回答は409
    r4 = await _respond(api_client, hs[3], latch, "yes")
    assert r4.status_code == 409, r4.text
    assert r4.json()["error"]["code"] == "LATCH_EXPIRED"


async def test_4_group_match_closes_one_on_one(api_client, db_engine, field):
    """09 §3 競合クローズ・グループ成立側(G3条件2・design §2.3試験4)。

    i2を共有する2つの提案(グループ{i1,i2,i3}と1対1{i2,iX})を用意し、
    グループを成立させる→1対1側はcancelledで閉じる(対象抽出は
    intent_ids && member_ids=同一Intent含有・§10-2の_SELECT_CONFLICTING_
    LATCHES)。iX(非共有)はactiveのまま。test_11(latches・1対1×1対1)の
    グループ成立側対。
    """
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    h3 = await _user(api_client, field)
    hx = await _user(api_client, field)  # iXの所有者(外部ユーザーU4相当)
    i1 = await _intent(api_client, db_engine, h1, _structured())
    i2 = await _intent(api_client, db_engine, h2, _structured())
    i3 = await _intent(api_client, db_engine, h3, _structured())
    ix = await _intent(api_client, db_engine, hx, _structured())
    group_latch = await _latch(db_engine, [i1["id"], i2["id"], i3["id"]])
    pair_latch = await _latch(db_engine, [i2["id"], ix["id"]])
    # グループ3人yes → matched
    for h in (h1, h2, h3):
        r = await _respond(api_client, h, group_latch, "yes")
        assert r.status_code == 200, r.text
    # ①グループmatched・参加Intentはmatched(05 §6 Intent遷移)
    assert (await _latch_row(db_engine, group_latch))["status"] == "matched"
    # ②1対1側はcancelled(もう片方はcancelledで閉じる)
    assert (await _latch_row(db_engine, pair_latch))["status"] == "cancelled"
    # ③latch_status_events(1対1側: proposed→cancelled・user_id=NULL=システム起因)
    assert ("proposed", "cancelled", None) in await _events(db_engine, pair_latch)
    # ④Intent遷移: グループ側3つはmatched・iXはactiveのまま
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, status FROM intents"
                    " WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {
                    "ids": [
                        uuid_mod.UUID(x)
                        for x in (i1["id"], i2["id"], i3["id"], ix["id"])
                    ]
                },
            )
        ).fetchall()
    by_id = {str(r[0]): r[1] for r in rows}
    assert by_id[i1["id"]] == "matched"
    assert by_id[i2["id"]] == "matched"
    assert by_id[i3["id"]] == "matched"
    assert by_id[ix["id"]] == "active"  # 閉じるのはi2を含む提案のみ


async def test_5_group_defer_rejects(api_client, db_engine, field):
    """09 §3 部分成立の禁止・defer側(G3条件2・design §2.3試験5)。

    3人グループで2人yes(partial_accept)→1人defer→即rejected(部分成立なし)。
    「一部がnoまたはdefer」のdefer側(no側は既存test_9(latches)担当)。
    """
    hs = [await _user(api_client, field) for _ in range(3)]
    intents = [await _intent(api_client, db_engine, h, _structured()) for h in hs]
    latch = await _latch(db_engine, [i["id"] for i in intents])
    await _respond(api_client, hs[0], latch, "yes")
    r2 = await _respond(api_client, hs[1], latch, "yes")
    assert r2.status_code == 200, r2.text
    assert r2.json()["latch"]["status"] == "partial_accept"
    r3 = await _respond(api_client, hs[2], latch, "defer")
    assert r3.status_code == 200, r3.text
    assert r3.json()["latch"]["status"] == "rejected"  # 即時・部分成立なし
    row = await _latch_row(db_engine, latch)
    assert row["status"] == "rejected"
    assert row["responses"][-1]["response"] == "defer"  # 区別保持
```

- [ ] **Step 2: 構文・収集確認**

Run: `cd backend && uv run python -m py_compile tests/integration/test_g3_acceptance_e2e.py && uv run pytest --collect-only tests/integration/test_g3_acceptance_e2e.py -q`
Expected: exit 0・**5件収集**(test_1〜test_5)

- [ ] **Step 3: unit全件の再確認**

Run: `make lint && make test`
Expected: グリーン(unit 1215件不変)

- [ ] **Step 4: コミット**

```bash
git add backend/tests/integration/test_g3_acceptance_e2e.py
git commit -m "test: G3 acceptance e2e tests 3-5 (deadline expiry, conflict close, defer)"
```

### Task 3: 試験6(FR-17 自己重複集合の除外)

**Files:**
- Modify: `backend/tests/integration/test_g3_acceptance_e2e.py`(ファイル末尾へ追記)

**Interfaces:**
- Consumes: Task 1の `_vec`/`E1`・`_structured(participants=)`・`_group_of`・`_pair_row`・`GroupEngine`(§10-7・10-8のPool構成手順)

- [ ] **Step 1: 試験6を追記する**

```python
async def test_6_self_duplicate_group_excluded(api_client, db_engine, field):
    """FR-17(10 §3)自己重複集合の除外・統合配置ケース(G3条件3・design §2.3試験6)。

    同一ユーザーUのIntent 2つ(s・s2)+別ユーザーV・WのIntent各1を同一条件
    (時間Bucket・地域・カテゴリ)で配置しGroupEngine.handle(s)を実行:
    ①{s,s2}を同時に含くgroup_candidates行==0件(同一ユーザーのIntentを2つ
    含む集合が生成されない)②対照: {s,V,W}は生成される(検証環境が通過状態
    であることの保証)③s×s2のペア行(match_candidates)なし。多層防御=
    Pool検索のLAYER1_WHERE_BASE(i.user_id <> :origin_user_id・§10-7)が
    前段でs2をPoolから除外し、_PAIR_COMPAT(i1.user_id <> i2.user_id)・
    貪欲法のuser相異(group_calc.build_groups・§10-8)・INSERT前user相異検査
    が続く。1対1側(hardfilter test_10)と対のグループ経路確認。
    """
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    hu = await _user(api_client, field)  # ユーザーU(2 Intent)
    s = await _intent(
        api_client,
        db_engine,
        hu,
        _structured(start=start, expires=expires, participants=(2, 4)),
    )
    s2 = await _intent(
        api_client,
        db_engine,
        hu,
        _structured(start=start, expires=expires, participants=(2, 4)),
    )
    hv = await _user(api_client, field)
    v = await _intent(
        api_client,
        db_engine,
        hv,
        _structured(start=start, expires=expires, participants=(2, 4)),
    )
    hw = await _user(api_client, field)
    w = await _intent(
        api_client,
        db_engine,
        hw,
        _structured(start=start, expires=expires, participants=(2, 4)),
    )
    group = GroupEngine(engine=db_engine, clock=clock)
    ctx = await group.handle(uuid_mod.UUID(s["id"]))
    assert ctx is not None  # handle自体は集合を生成する(対照の前提)
    # ①{s,s2}を同時に含む集合行==0件
    async with db_engine.connect() as conn:
        n_bad = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM group_candidates"
                    " WHERE intent_ids @>"
                    " ARRAY[CAST(:s AS uuid), CAST(:s2 AS uuid)]"
                ),
                {"s": s["id"], "s2": s2["id"]},
            )
        ).scalar_one()
    assert n_bad == 0
    # ②対照: {s,V,W}(Uの2つ目を含まない3人集合)は生成される
    # (全員の[min,max]に3が∈のため成立・偽陰性の対抗)
    gc = await _group_of(db_engine, [s["id"], v["id"], w["id"]])
    assert gc is not None and gc[1] == "candidate"
    # ③s×s2のペア行なし(Pool×起点UPSERTの対象外=自己除外の実効)
    assert await _pair_row(db_engine, s["id"], s2["id"]) is None
```

- [ ] **Step 2: 構文・収集確認**

Run: `cd backend && uv run python -m py_compile tests/integration/test_g3_acceptance_e2e.py && uv run pytest --collect-only tests/integration/test_g3_acceptance_e2e.py -q`
Expected: exit 0・**6件収集**(test_1〜test_6)。かつ `uv run pytest --collect-only -q | tail -1` が**1475件**

- [ ] **Step 3: unit全件の再確認**

Run: `make lint && make test`
Expected: グリーン(unit 1215件不変)

- [ ] **Step 4: コミット**

```bash
git add backend/tests/integration/test_g3_acceptance_e2e.py
git commit -m "test: G3 acceptance e2e test 6 (self-duplicate group exclusion)"
```

### Task 4: m2ws1-/m2ws2- teardown恒久対策(users削除1行)

**Files:**
- Modify: `backend/tests/integration/test_events_pipeline.py`(user_env fixtureのteardown末尾)
- Modify: `backend/tests/integration/test_embedding_pipeline.py`(同上)

**Interfaces:**
- Consumes: 既存 `_register` の戻り `user_id`(str型 — `created.json()["user"]["id"]`。CAST(:uid AS uuid) が必要)
- Produces: なし(fixture修正のみ。design §2.4)

- [ ] **Step 1: test_events_pipeline.py へusers削除を追加**

`user_env` fixtureのteardown、`DELETE FROM intents WHERE user_id = :uid` の実行ブロックの**直後(同一 `async with db_engine.begin() as conn:` ブロック内の末尾)**へ追記:

```python
            # usersも消す(STATUS ws-1節の観察: test-ci 1回あたり約12件のusers
            # 蓄積。intents削除の後=FK順。usersはauth_*系テーブルから参照
            # されないため追加削除は不要 — design §2.4)
            await conn.execute(
                text("DELETE FROM users WHERE id = CAST(:uid AS uuid)"),
                {"uid": user_id},
            )
```

(user_idはstr型〔`created.json()["user"]["id"]`〕のためCAST必須 — §10-9のSQL流儀と同一)

**注記**: design §2.4の実績判断では、両ファイルの試験は単一ユーザーのIntent配置(Event/Embedding経路)のためlatches・notifications等のusers参照行は残らない(STATUS ws-1節の掃除観察=usersのみ蓄積)。万一スーパーバイザー検証時にFK違反(ForeignKeyViolation)で当該ファイルの既存試験が落ちた場合はdesign想定と異なる残行があるということなので、**勝手に削除順を変えず**報告書に状況を記録して作業を停止する(削除順の追加はスーパーバイザー判断)。

- [ ] **Step 2: test_embedding_pipeline.py へ同一追加**

同ファイルの `user_env` fixtureのteardown、`DELETE FROM intents WHERE user_id = :uid` の直後へ同一コードを追記(コメントの「STATUS ws-1節の観察」は同じ)。

- [ ] **Step 3: 構文・収集確認(件数不変の確認)**

Run: `cd backend && uv run python -m py_compile tests/integration/test_events_pipeline.py tests/integration/test_embedding_pipeline.py && uv run pytest --collect-only tests/integration/test_events_pipeline.py tests/integration/test_embedding_pipeline.py -q`
Expected: exit 0・**15件収集**(main基準=変更前と同数・test_events_pipeline 8+test_embedding_pipeline 7。fixture内部のみの変更のため件数不変)

- [ ] **Step 4: unit全件の再確認**

Run: `make lint && make test`
Expected: グリーン(unit 1215件不変)

- [ ] **Step 5: コミット**

```bash
git add backend/tests/integration/test_events_pipeline.py backend/tests/integration/test_embedding_pipeline.py
git commit -m "test: delete users in user_env teardown (m2ws1/m2ws2 leak fix)"
```

### Task 5: 報告ファイル + 最終検証

**Files:**
- Create: `docs/plans/M3/ws-9-report.md`

- [ ] **Step 1: §6の完了条件1〜7を全て実行し、証拠(コマンド出力の要点)を採取する**

(コマンド一覧: ①`make lint && make test` ②`cd backend && uv run pytest --collect-only tests/integration/test_g3_acceptance_e2e.py -q` と `uv run pytest --collect-only -q | tail -1`(期待1475) ③basename一意コマンド ④`git diff main -- backend/src backend/alembic backend/pyproject.toml backend/uv.lock Makefile compose.yaml docker frontend prototype` ⑤`git diff --name-only main | sort` ⑦`rg -n "DELETE FROM users" backend/tests/integration/test_events_pipeline.py backend/tests/integration/test_embedding_pipeline.py`。test-ciは実行しない)

- [ ] **Step 2: 報告ファイルを§7の形式で作成する**

design §5承認事項(§5-1〜§5-7)の実装対応・§9 IF確定事項からの変更有無を必ず記入する。

- [ ] **Step 3: コミット**

```bash
git add docs/plans/M3/ws-9-report.md
git commit -m "docs: ws-9 report with verification evidence"
```

- [ ] **Step 4: worktreeの状態を整える**

`git status --short` が空(`.venv/`・`__pycache__/` はコミットしない)であることを確認。**mainへのマージはスーパーバイザーが行うため、push・mergeはしない**。

---

## 9. IF確定事項(design §2・§4.1の固定値。ここから変更した場合は報告書に記録)

1. **SUBJECT_PREFIX**=`"m3ws9-"`(2026-10-02実査で既存m3ws1〜m3ws3・m2ws7等と衝突なし)。BASE_HOURS=120・FAR_EXPIRES_H=144(既存単位と同一の時間窓分離)
2. **EXPECTED_DETAIL_KEYS(GET /v1/latches/{id}のlatch要素13キー)**: id/status/response_deadline/expires_at/created_at/completed_at/proposal/is_group/my_response/remaining_responses(LatchSummaryOut)+participants/time_summary/area_name(LatchDetailOut追加)。proposedでは末尾3キーは**None値だがキーとして存在**(`response_model_exclude_none`なし・routes.py 62行実査)。**supervisor引継ぎ事項の反映**
3. **SUMMARY_PROPOSAL_KEYS(7キー)**=time_summary/area_name/headcount/category_primary/category_secondary/budget/match_level・**hidden側は{headcount, match_level}の2キー**(proposal.build_proposal実装と突合・§10-6)。差分5キー=time_summary/area_name/category_primary/category_secondary/budget
4. **FORBIDDEN_KEYS**・**GEO_KEYS**: §8 Task 1のコードブロックどおり(raw_text/soft_constraints/ng_conditions/soft_texts/display_name/profile/user_id/bio/name + lon/lat/geo_lon/geo_lat/geo_center/geo_radius_m/latitude/longitude/coordinates/location/accuracy/radius)
5. **_structuredのvisibilityは常時明示・デフォルト"summary_only"**(groupengine流儀。alembic 0001のintents.visibilityは`DEFAULT 'hidden_until_match'`のため省略すると意図しない分岐)
6. **_latchヘルパー**: test_latches_api流儀(now=SystemClock・deadline_hours/expires_hours)。gid(group_candidate_id)引数なし — 競合クローズの対象抽出はintent_ids && member_idsでlatches行だけで完結しgid不要(§10-2)
7. **_sweeperヘルパー**: test_expiry_batches流儀(`LatchEngine(engine, clock)` をdrain対象・`ExpirySweeper(engine, clock, latch, batch_limit=50)`)。run_once戻り値=遷移させた行数(latches+intents+completed合計・drain除く)
8. **GroupEngine構成(試験6)**: `GroupEngine(engine=db_engine, clock=clock)`(geo/latch省略= None — handleは集合生成のみでfinalize/try_promoteしないため)。Pool構成手順は§10-7のとおり(_POOL_SEARCHのLAYER1_WHERE_BASEに`i.user_id <> :origin_user_id`)
9. **users削除SQL(teardown恒久対策)**: `DELETE FROM users WHERE id = CAST(:uid AS uuid)`(user_idはstr型のためCAST。FK順としてintents削除の後・同一txブロック内)
10. **試験数の基線**(2026-10-02実査): main現状=unit 1215+integration 254=test-ci 1469。本単位はintegration 6件追加のみ→**test-ci期待値1475**(integration 260・unit 1215不変)。frontend 217不変。**収集・実行の実測値がこの見込みと異なる場合は報告書に実測値と差異の理由を記録する**(試験の追加・統合はagent3の裁量ではなく、計画からの逸脱として記録対象)

## 10. 参照実装の引用(agent3が参照漏れしないための全文・2026-10-02 main実査)

**10-1. latches/schemas.py — LatchDetailOut(試験2の⑤の根拠)**:

```python
class LatchSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str
    response_deadline: datetime
    expires_at: datetime
    created_at: datetime
    completed_at: datetime | None = None
    proposal: dict
    is_group: bool
    my_response: str | None = None
    remaining_responses: int

class LatchDetailOut(LatchSummaryOut):
    participants: list[ParticipantOut] | None = None
    time_summary: str | None = None
    area_name: str | None = None
```

GET /v1/latches/{id} は `response_model=LatchDetailEnvelope`(routes.py 62行)・`response_model_exclude_none` なし → **None値フィールドもJSONキーとして出力**(proposedで13キー全存在)。

**10-2. latches/store.py — _SELECT_CONFLICTING_LATCHES(試験4の根拠)**:

```sql
SELECT id, status FROM latches
WHERE id <> CAST(:self_id AS uuid)
  AND status IN ('candidate', 'proposed', 'partial_accept')
  AND intent_ids && CAST(:member_ids AS uuid[])
ORDER BY id
FOR UPDATE
```

競合クローズの対象抽出は**Intent含有**(`intent_ids && member_ids`)。グループ成立側のmember_ids=当該latchのintent_ids={i1,i2,i3}→i2を含む1対1latch{i2,iX}がヒット→cancelled(test_11(latches)と同一構成原理)。

**10-3. latches_apiの_latch流儀(試験3〜5のlatches行直接INSERT)**: §8 Task 1の `_latch` ヘルパーのコードブロックどおり(test_latches_api.py 171〜209行からgid引数を抜いた形。INSERT列は intent_ids, proposal, score, status, response_deadline, expires_at, created_at の7つ)。

**10-4. expiry_batchesの_sweeper流儀(試験3)**: §8 Task 1の `_sweeper` ヘルパーのコードブロックどおり。**run_onceの戻り値は「遷移させた行数(latches+intents+completedの合計・drain呼出を含まない)」**(sweeper.py 212〜241行)。deadline過去化はapiプロセスClock(SystemClock)差し替え不能のためDB値操作(test_expiry_batches試験3・482〜489行のUPDATEと同一手法)。FakeClock不進行ならクローズ検知drainは不発(test_expiry_batches試験2のdocstringと同一注記)。

**10-5. notificationsテンプレート(試験1の通知経路)**: StubPushSenderは`build_push(notification_type)`の固定文言で`latch.notifications`ロガー(LOGGER_NAME)へJSON 1行記録。`PUSH_BODY_LATCH="LATCH候補があります。\n詳細はアプリでご確認ください。"`。muted参加者はnotifications書き込み・push送信とも対象外(遷移はproposedまで通常どおり・C13)。push記録の検証は`_push_records(caplog)`+`caplog.at_level(logging.INFO, logger=LOGGER_NAME)`。

**10-6. worker/matching/proposal.py — build_proposal(試験2の①②③の根拠)**:

```python
def build_proposal(*, origin, peer, score, area_name) -> dict:
    if origin.visibility != "summary_only" or peer.visibility != "summary_only":
        return {"headcount": 2, "match_level": match_level(score)}
    budgets = [b for b in (origin.budget_max, peer.budget_max) if b is not None]
    return {
        "time_summary": pair_target_time(origin.time_start, peer.time_start)
        .astimezone(JST).strftime("%Y-%m-%d %H:%M"),
        "area_name": area_name,
        "headcount": 2,
        "category_primary": origin.category_primary,
        "category_secondary": origin.category_secondary,
        "budget": {"max": min(budgets)} if budgets else None,
        "match_level": match_level(score),
    }
```

双方summary_only→7キー(budgets空ならbudget=Noneだが**キーは常に存在**)。いずれかhidden→2キー。match_level(0.9)="high"(MATCH_LEVEL_HIGH_MIN=0.90・latch_calc.py)。

**10-7. groupengineのPool構成手順(試験6の根拠)**: GroupEngine.handle → load_group_origin(max>=3ガード)→`_POOL_SEARCH`(WHEREに`LAYER1_WHERE_BASE`を含む):

```sql
-- layer1.py LAYER1_WHERE_HEAD(抜粋・_POOL_SEARCHが使用):
    i.status = 'active'
    AND i.embedding IS NOT NULL
    AND i.user_id <> CAST(:origin_user_id AS uuid)
    AND i.category_primary = :origin_category
    ... (時間・地理・予算・ブロック・飲酒条件)
-- さらに _POOL_SEARCH 固有の緩和:
      AND i.participants_min <= 4 AND i.participants_max >= 3
      AND i.time_start >= :bucket_start AND i.time_start < :bucket_end  (30分Bucket)
```

**起点と同一ユーザーのIntent(s2)はPoolに入らない**(前段除外)。配置はgroupengine流儀: 同一start(同一30分Bucket)・同location「天文館」・同category・participants=(2,4)・embedding=E1(_vec(1.0)で全員similarity=1)。handle後の集合照会は`_group_of`(intent_idsのsorted正規化一致・groupengine流儀)。

**10-8. group_calc.build_groups の自己除外(試験6の多層防御)**: `_PAIR_COMPAT`(group_engine.py)の `AND i1.user_id <> i2.user_id`(互換行列に同一ユーザーペア不入)・貪欲法の `if any(cand.user_id == m.user_id for m in members): continue`(作成user_id相異)・handle内のINSERT前検査 `if len({v[2] for v in versions}) != len(ids): continue`。いずれかの層で{s,s2}混入集合は生成されない。

**10-9. teardown FK順(field fixture)**: latch_status_events→calibration_records→notifications→latches→match_candidates→group_candidates→match_events→intents→users(notifications流儀・§8 Task 1のSQL一式どおり)。users削除の恒久対策(Task 4)も「intentsの後=FK順」に同じ理由で置く。

## Self-Review(計画書レビュー時の機械チェック・2026-10-02実施)

1. **新規テストファイルのbasename衝突**: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は**空**(2026-10-02実査)。新規 `test_g3_acceptance_e2e.py` は `find backend/tests -name "test_g3_acceptance_e2e.py"` が0件(実査)。design §4-1-2の機械チェックを満たす
2. **ピン試験への追随访問**(design §4-1-4): 本単位は製品コード・設定・マイグレーションを変えないため、test_settings・test_rate_limit_wiring・test_llm_factory等のピンは**無影響**(新規はintegrationマーク付きテストファイル1本と既存2ファイルのteardown修正のみ。unit収集件数1215不変で証明)。Task 4のteardown修正はtest_events_pipeline・test_embedding_pipelineの**収集件数を変えない**(fixture内部のみ)
3. **DB残存干渉の対抗策**(design §4-1-3): SUBJECT_PREFIX=m3ws9-(fixtureが試験ごとに一意hex付与)・teardownはFK順9ステップ(§8 Task 1)・時間窓はnow+5日系(BASE_HOURS=120・FAR_EXPIRES_H=144)・期限切れ再現はDB値操作(deadline過去化のみで実時間待ちなし)。試験3のrun_once==1は他試験残骸不在をteardownが担保(既存test_expiry_batches試験3と同一前提)
4. **specカバレッジ**(design §2.3・§2.4→Taskの逆引き): 試験1→Task 1・試験2→Task 1・試験3〜5→Task 2・試験6→Task 3・teardown恒久対策→Task 4・design §4.2の検証手順→§7報告形式のスーパーバイザー検証時欄(1〜8)。design §2.1表1〜3の「既存」欄は本計画では再構成しない(G3判定時にdesign §2.1対応表+実行ログでスーパーバイザーが集約・§7-8)
5. **プレースホルダスキャン**: TBD/TODO/「適切に」等のプレースホルダなし。全コードステップに全文コードあり
6. **型整合**: `_user(api_client, prefix, birth_date="1990-04-01") -> headers(dict)`・`_intent(api_client, db_engine, headers, structured) -> dict`・`_seed_jev(db_engine, a_id: str, b_id: str, wa: float, wb: float) -> None`・`_latch(db_engine, intent_ids: list[str], *, status, score, deadline_hours, expires_hours, proposal) -> str`・`_respond(api_client, headers, latch_id: str, response: str)`・`_events(db_engine, latch_id: str) -> list[tuple]`・`_sweeper(db_engine, clock) -> ExpirySweeper`・`_group_of(db_engine, intent_ids: list[str])`・`_pair_row(db_engine, a: str, b: str)`・`_all_keys(obj) -> set`。Task 1〜3で使う呼び出しはすべてシグネチャ突合済み(_latch_id_ofの引数はlist[str]・_respondのlatch_idはstr型のままURLへf-string埋め込み=既存流儀)
7. **supervisor引継ぎ事項の反映**: 試験2⑤の期待キー集合は§9-2・§10-1のとおりparticipants/time_summary/area_nameを含む13キーで完全列挙(schemas.pyのLatchDetailOutと突合済み)
