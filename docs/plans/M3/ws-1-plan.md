# M3 ws-1(LATCH応答系コア)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** M2が作った「提示(proposed)まで」に対し「回答から成立・不成立まで」を実装する — ①`latches/`ドメイン新設(GET /v1/latches・GET /v1/latches/{id}・POST /v1/latches/{id}/response・yes/no/defer・FOR UPDATE+条件付きUPDATE・409分岐LATCH_EXPIRED/ALREADY_ANSWERED/LATCH_CLOSED)②成立時のIntent matched化+競合クローズ(同一tx)③グループ回答(必要人数=len(intent_ids)・部分成立禁止)④Calibration記録(rejected/matched時・prediction+segment)⑤stage1削除Event処理へlatchesクローズ+matched解散・参加Intent復帰を追加⑥group_engine昇格UPDATEへgroup_candidate_id列追加(ws-7引継ぎ)。**マイグレーション追加なし・依存追加なし**。

**Architecture:** design §2.1案A — `src/latch/latches/`へroutes・service・store・errors・schemas・calibrationを新設(intentsと同型の3層・永続化はtext()生SQL)。回答はdesign §2.2の単一tx手順0〜6(FOR UPDATE→事前検査→条件付きUPDATE〔06 §6のWHERE+参加Intent検査NOT EXISTS〕→影響0時は事前検査の分類で409)。成立時のIntent matched化・競合クローズ・Calibration INSERTは回答と同一tx(design §2.3・§2.5案A)。評価行特定(1対1=score一致3段階)とグループminペア選択は純関数へ分離(calibration.py・unit試験で決定的に)。segment判定はlayer3の文字bigramを再利用(`bigrams`public化)。外部SDKなし(context7確認不要・design §1冒頭)。

**Tech Stack:** 変更なし(Python 3.13 / FastAPI / SQLAlchemy[asyncio]+asyncpg / Pydantic)。

**Spec:** `docs/plans/M3/ws-1-design.md`(agent1設計メモ。**未解決論点なし** — §5のsupervisor承認事項8件は2026-09-30承認済み・承認記録はSTATUS.mdのM3セクション。③paused成立と④segment計算時点はG3時確認事項としてSTATUSへ記録済み・本計画はdesign本文どおりに扱う)。design.mdが本計画より優先(§1の規定)。

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m3-ws-1`。**agent3はworktree内でコミットする**(mainへの直接コミット・pushは禁止。マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する(**依存追加・マイグレーション追加ともになし**のためlockもalembicも進まない)。
- **共有ci-db運用**(STATUS運用ルール1〜5): 本単位は**マイグレーションを追加しない**ため(design §4.3・alembic head=0005不変)、**agent3は `make test-ci` を実行してよい**(2026-09-30スーパーバイザー指示)。ただし**必ず `docker compose build api` を先行させる**(運用ルール4。compose.yamlのapiはbuild型・ソースマウントなし。コード変更後のapiイメージを再ビルドせずにtest-ciを走らせると古いコードで試験する)。順序は `make lint && make test` → `docker compose build api` → `make test-ci`。`make up` / `make down` / `make migrate` / `docker build` / `docker pull` は実行しない(`make test-ci` 内部の `docker compose up -d --wait` は可)。test-ci終了後にgeo実データがfixtureで失われるgeofeatures系試験はないが、実運用データはスーパーバイザーが復旧判断する(agent3はgeo-importしない)。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。**計画時点で機械確認済み(2026-09-30)**: 現状 `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空(既存106ファイル)。本計画の新規5ファイル(`test_latches_service.py`・`test_calibration.py`・`test_latches_store_sql.py`・`test_latches_api.py`・`test_latches_routes.py`)は既存と衝突しない(既存リストに同名なし。`unit/intents/test_service.py`・`unit/intents/test_store_sql.py` はbasename部分が異なる `test_intents_service.py` 側ではなく `test_service.py`/`test_store_sql.py` なので本計画の5名とは無関係)。各Taskのコミット前に同コマンドが空であることを再確認する。
- **alembic 0001〜0005は変更禁止**(design §3.3・マイグレーション追加なし): Task 12で `git diff main -- backend/alembic` が空であることを検証する。head=0005不変。
- **依存追加なし**: `backend/pyproject.toml`・`backend/uv.lock`・`Makefile`・`.env`・`.env.example`・`settings.py` に新規依存・設定キーを追加しない。
- **並走単位なし**(本単位はwave先頭・後続ws-2〜4は本単位の完了待ち)。ただし既存ファイルへ触れるため、各Taskのコミット前に `git status --short` で意図しないファイルの変更が混入していないことを確認する(§4の一覧以外に差分が出ていたら作業を止めて報告する)。
- **固定値の遵守**: design §2 の採用判断(案A・手順0〜6・3段階特定・minペア)と本計画§9のIF確定事項は固定値。**変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `refactor:` `chore:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| 回答API契約: request `{"response": "yes"}`・response 200 `{"latch": {...}}`。409三種(LATCH_EXPIRED=期限切れ/ALREADY_ANSWERED=二重回答/LATCH_CLOSED=競合クローズ後)・422 VALIDATION_ERROR | 05 §5・design §1.2引用#1 |
| responseはyes/no/deferの3値。deferもrejected相当だが回答種別はresponsesで区別保持(D-07判定が読む) | 05 §2・03 D-07・引用#2 |
| 直列化: 単一tx内の条件付きUPDATE。FOR UPDATEで排他してからstatus遷移。WHEREは `status IN ('proposed','partial_accept') AND response_deadline > :now AND expires_at > :now` | 06 §6・引用#3・C9 |
| 影響行数0は処理中止し事前読取の分類で409(期限切れ=LATCH_EXPIRED・既回答=ALREADY_ANSWERED・競合クローズ後=LATCH_CLOSED) | 06 §6・引用#4 |
| `:now` はアプリ層のClock.now()(DBのclock_timestamp()不使用・テストのClock注入と時刻源を揃える) | 06 §6・04 §5 C2・引用#5 |
| 期限切れバッチ遅延でstatusがproposedのままでも期限後の回答は受理しない(期限後成立の構造的排除) | 06 §6・引用#6 |
| 競合クローズ: matched成立時点で参加Intentを含むcandidate/proposed/partial_acceptのlatchesをcancelledへ。回答処理と同一トランザクション | 06 §6・01 §8・引用#7 |
| LATCH遷移表: proposed→partial_accept(一部yes)/partial_accept→matched(全員yes)/proposed・partial_accept→rejected(no・defer)/expired(ws-2)/matched→cancelled(参加Intent削除)/matched→completed(ws-2) | 05 §6・引用#8 |
| Intent遷移: active→matched(成立)/matched→active・matched→expired(解散時。expires_at経過済みならexpired・経過前ならactive復帰) | 05 §6・引用#9 |
| D-06: 集合全員のYESが必要(必要人数=\|S\|)。部分成立禁止。誰かのno/deferで即rejectedも遷移表どおり | 06 §8・引用#10 |
| responses構造 `[{"user_id","intent_id","response","answered_at"}]`・latch_status_eventsは遷移txと同時挿入(user_idは回答起因=回答者・システム起因=NULL) | 05 §2・06 §10・引用#11・#12・C10 |
| Calibration: 回答確定時(rejected/matched遷移時)に作成。prediction=would_a/would_b/MutualScore/L/jev_5axis/provider/model/segment・proposal_snapshot=proposal同形・actual_responses=responses同形・matched真偽。actual_attended/cancelled_after/anonymized_atはNULL | 07 §6・05 §2・引用#13 |
| segment: proposed時点の語彙一致/意味近接フラグ。category_secondary(一方nullならsoft_constraints全文言)間の表層トークン一致でlexical/semantic。グループは集約minペアのフラグ | 09 §2.3・引用#14・design §2.6 |
| GET /v1/latches: 一覧(本人関与かつproposed以降=cursor・limit既定20上限100・対象時刻昇順・同点created_at降順) | 05 §5・引用#15 |
| GET /v1/latches/{id}: 詳細(成立後は解放情報=参加者表示名・集合情報)。参加者のみ | 05 §5・引用#16・#21 |
| 403 FORBIDDEN=参加者以外(存在秘匿せず403)・404 NOT_FOUND=不在・未登録JWTも404。エラー形式 `{"error":{"code","message","details"}}` | 05 §5・引用#17・#18 |
| 削除Event処理: 当該Intentを含む候補・保留(latches.status=candidate)の無効化。matched解散時の残Intent復帰(expires_atでexpired/active分岐) | 06 §1・§9・§10・引用#19・#20 |
| 成立後の解放: チャット・相手表示名・最小限プロフィール・集合情報(対象日時・場所の要約)・次アクション。completedはws-2 | 03 §6・引用#21 |
| グループ提案では必要人数と現況を人数のみ表示(誰が回答済みか特定しない=応答に回答者user_idを出さない) | 03 §5・引用#22 |
| 不成立の理由・回答種別はAPIも開示しない | 03 §7・引用#23 |
| ユーザーに見せるLATCHはproposed以降(candidateは一覧除外) | 03 §2・引用#24 |
| match_candidates: UNIQUE(intent_a_id,intent_b_id,intent_a_version,intent_b_version)・jev_result保持 | 05 §2・引用#25 |
| latches.scoreは提示時スコア・response_deadlineは提示時D-05再計算済み(回答判定は値との比較のみ) | 05 §2・06 §6・引用#26・#28 |
| 02#14(YES/NO回答)はci統合試験が本体。#15/#16/#18/#22/#25はws-9 | 10 §3・引用#27 |
| latchesの評価行特定=score一致3段階・グループprediction=minペア準用・全段失敗はレコード不作成+構造化ログ | design §2.11・§2.12・§2.5・承認事項5/6 |
| group_engine昇格UPDATEへgroup_candidate_id列追加(ON CONFLICT昇格で旧gidが残る問題の解消) | design §2.10・承認事項8・STATUS ws-7引継ぎ |
| 既存資産の再利用(latch_calc/latch_engine/proposal/group_engine/layer3/stage1/intents定型/get_clock/GeoService) | design §1.3・12 C9/C10 |

## 2. グローバル制約(全タスクに暗黙に適用)

- **時刻はClock経由のみ**。製品コード(`backend/src/latch/`)で実時間参照禁止(arch test `test_arch_no_direct_time.py` が強制)。回答の `:now` はFOR UPDATE取得後に `self._clock.now()` で採取し、検査とUPDATEで同一の値を使い回す(design §2.2・latch_engine.try_promoteと同一規律)。テストコードの `_future()` はSystemClockでよい(既存integration流儀)。
- **永続化はtext()生SQLのみ**(design §2.1案A)。SQLAlchemy ORMは導入しない。asyncpgのUUID復元は `_coerce_uuid` 規律(M0 ws-3・M2 ws-1で3度検出された欠陥パターン)。uuid[]のbindは `list[uuid.UUID]` を渡してSQL側で `CAST(:x AS uuid[])`(group_calc.uuid_arrayの§9-14規律。文字列リテラルはasyncpgが拒否)。
- **latchesのUPDATEで書く列はresponses・status・group_candidate_id(§2.10分)のみ**。latchesにupdated_at列は存在しない(0001スキーマ)。completed_atはcompleted遷移(ws-2)まで入れない(design §2.2)。
- **回答APIから通知を送らない**(design §1.4。notificationsへの新規書き込みなし。観測点はlatch_status_eventsで確保済み・ws-3が参照する)。
- **回答API内でexpired遷移を書かない**(期限切れは409のみ・遷移はws-2のsweeper。引用#6・#8)。
- **例外・ログに機微を入れない**(08 §2.4)。Proposal本文・soft_constraints文言・回答理由をログ・例外メッセージへ出さない。latchesドメインの固定文言は§9-13の一覧どおり。
- **latch_status_eventsは遷移と同一txで挿入**(C10)。回答起因はuser_id=回答者・システム起因(競合クローズ・stage1)はNULL(引用#12)。partial_accept遷移にも挿入する(design §2.2手順6d)。
- **書き込みは追記のみ**(design §2.13)。responses要素の更新・削除はしない(`responses || CAST(:item AS jsonb)`)。
- **`make lint`(ruff E,F,I,UP,B・format行長88)と `make test` を毎コミット通す**。unit試験は外部プロセス不要・実時間待ちなし(スタブ注入・FakeClock)。
- **既存部品はimportして使うのみ**(design §3.3): `worker/matching/latch_calc.py`・`latch_engine.py`・`proposal.py`・`group_engine.py`(§2.10分のみ変更)・`layer3.py`(§2.6分のみ変更)・`worker/stage1.py`(§2.7分のみ変更)・`intents/`・`auth/`・`geo/service.py`・`core/clock.py`・`core/deps.py`・`ratelimit/`。
- **全タスクのコミット前に**: `make lint && make test` グリーン・`git status --short` で差分が§4の一覧どおり・basename一意コマンドが空。

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **競合クローズのFOR UPDATEが回答tx間でデッドロックする**(design §2.3のSELECTに順序がない) — 2つの回答txが互いに相手側のlatches行をクローズしようとすると行ロック獲得順序が交差し、PostgreSQLのデッドロック検出で片方がabort(503)する。稀(同一2メンバーの別latchesは0004部分UNIQUEで存在しない)が、`ORDER BY id` を付けてロック順序を固定すれば交差が構造的に起きない → Task 5のSQLピン(ORDER BY id FOR UPDATE)+Task 11試験5が検証(§9-1に記録)
2. **期限切れバッチ遅延中のproposed行へ回答が受理される**(引用#6違反) — WHEREの期限比較を書き漏らす・`:now`を検査とUPDATEで別値にすると、statusだけ見る実装になり期限後成立が起きる → Task 5のSQLピン(response_deadline/expires_atの両比較)+Task 6 unit(now採取の一貫性)+Task 11試験4(status=proposedのまま409)
3. **削除済みIntentを含む提案へ回答が受理される**(design §2.2の EXISTS追加の趣旨) — NOT EXISTS検査を書き漏らすとstage1処理前の削除レース窓で成立してしまう → Task 5のSQLピン(NOT EXISTS)・影響0時の分類はLATCH_CLOSED → Task 11試験17で削除経路を通してclosed分岐を検証
4. **uuid[]バインドを文字列で渡してasyncpgが拒否**(ws-7スーパーバイザー検証の再発) — `intent_ids && CAST(:member_ids AS uuid[])` 等へlist[uuid.UUID]以外を渡すとruntimeエラーで503 → store全関数は `list[uuid.UUID]` を渡す(§2規律)・Task 5のcompile検査がbind param完全認識を担保・Task 11試験5/11が実DBで `&&` を通す
5. **成立後の応答・一覧に回答者の特定情報が混入**(引用#22違反) — my_responseは自分の値のみ・remainingは人数のみ。responses配列をそのまま応答へ出すと他者の回答種別が漏れる → schemas.pyのLatchSummaryOutは responses を持たない構造ピン(Task 2)+Task 11試験15(my_response・remainingのみ)

---

## 4. スコープ(作成・変更するファイル一覧)

作成(§0のbasename機械確認済み・design §3.1+§9の実装詳細確定分):

```text
backend/src/latch/latches/__init__.py                     (Task 2で雛形・Task 8で完成形)
backend/src/latch/latches/errors.py                       (Task 2。LatchesError基底+404/403/409系×3/422/503)
backend/src/latch/latches/schemas.py                      (Task 2。ResponseRequest・LatchSummaryOut・LatchDetailOut・List)
backend/src/latch/latches/calibration.py                  (Task 3〜4。segment_texts/classify_segment・pick_eval_row・select_versioned_pairs/pick_min_pair・build_prediction)
backend/src/latch/latches/store.py                        (Task 5。SQL定数+conn受取関数群)
backend/src/latch/latches/service.py                      (Task 6〜7。respond手順0〜6・list・get・cursor・make_latches_service)
backend/src/latch/latches/routes.py                       (Task 8。latches_router 3エンドポイント)
backend/tests/unit/latches/test_latches_service.py        (Task 2・6・7。例外ピン・409分類・new_status・remaining・cursor)
backend/tests/unit/latches/test_calibration.py            (Task 3〜4。segment・3段階・minペア・prediction)
backend/tests/unit/latches/test_latches_store_sql.py      (Task 5。実dialect compile検査+SQL句ピン)
backend/tests/integration/test_latches_api.py             (Task 11。design §4.2の17試験)
backend/tests/unit/latches/test_latches_routes.py         (Task 8。スタブサービス+ASGITransportのroutes試験)
docs/plans/M3/ws-1-report.md                              (Task 12。報告ファイル)
```

変更(design §3.2+§9の実装詳細確定分):

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/src/latch/worker/matching/layer3.py` | `_bigrams` のpublic化(`bigrams` 関数を追加・`_bigrams` は削除せず両参照可・design §2.6) | 1 |
| `backend/tests/unit/matching/test_layer3.py` | public版=private版同一実装のピン1件を追記 | 1 |
| `backend/src/latch/worker/matching/group_engine.py` | `_UPDATE_GROUP_LATCH_FOR_PROMOTION` へ `group_candidate_id = CAST(:gid AS uuid)` 追加+関数シグネチャへgid+呼び出し側へ `gid=r.gid`(§2.10) | 9 |
| `backend/tests/unit/matching/test_group_engine.py` | 昇格UPDATEのSET句へgroup_candidate_idが含まれるSQLピン1件を追記 | 9 |
| `backend/src/latch/worker/stage1.py` | 削除Event処理へ `_close_latches_on_delete`(開いているlatchesクローズ+matched解散+残Intent復帰)を追加(§2.7) | 10 |
| `backend/tests/unit/test_worker_stage1.py` | 既存削除Event試験2件のFakeResult追従+新規2件(開いている3状態クローズ・matched解散復帰) | 10 |
| `backend/src/latch/main.py` | latches_router登録・LatchesErrorハンドラ・lifespanへlatches_service構築(独立スキップ判定)・create_appへlatches_service注入引数 | 8 |
| `backend/tests/integration/test_matching_groupengine.py` | 世代交代昇格(latches.group_candidate_idが新gidへ書き換わる)1試験を追記(§2.10本体のE2E) | 11 |

`backend/tests/unit/latches/` ディレクトリは新規作成するが `__init__.py` は置かない(tests配下は prepend mode・§0のbasename規律)。

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`。

## 5. 禁止(触ってはいけないもの・スコープ外の判断基準)

- **docker系のうち `make up` / `make down` / `make migrate` / `docker build` / `docker pull` / `docker compose …`(build api・test-ci内部のupを除く)**: §0のとおり。api再ビルド(`docker compose build api`)と `make test-ci` は実行可・報告書に結果を記録
- **実API呼び出し**: `make g1-gate`・`make g2-gate`・`make embed-smoke`・`make jev-smoke` は起動しない(本単位は外部SDKなし)
- **design §3.3の禁止**: `backend/src/latch/` 配下の auth / users / intents / ratelimit / geo / core / events / llm / g1gate / g2gate 各モジュール。`worker/` の既存ファイルのうち変更対象以外(`worker/__main__.py`・`main.py`・`embedding.py`・`embedding_text.py`・`debounce.py`・`backfill.py`・`reeval.py`・`jev.py`・`cost/`)。`worker/matching/` のうち変更対象以外(`candidates.py`・`group_calc.py`・`latch_calc.py`・`latch_engine.py`・`layer1.py`・`layer2.py`・`layer4.py`・`origin.py`・`proposal.py`・`runner.py`)。`backend/alembic/`(0001〜0005不変・追加もしない)。`compose.yaml`・`docker/`・`frontend/`・`prototype/`。`backend/pyproject.toml`・`backend/uv.lock`(依存追加なし)。`.env`・`.env.example`・`settings.py`(新規キーなし)。`docs/`(01〜12・learn・testassets)。`docs/plans/STATUS.md`(スーパーバイザー管理)。`docs/plans/` の既存ファイル(M0/M1/M2のdesign・plan・report)。`backend/tests/` の§4に列挙した以外の既存試験
- スコープ外と判断する基準(必要になったと感じても作らない — design §1.4・§2.13):
  - **expiry_sweeper・Intent期限切れバッチ・catch-up・リセットジョブ・matched→completed遷移** → ws-2(引用#8・C9の同一直列化方式は本単位が参照実装を残す)
  - **通知送信(matched成立通知・不成立通知・FCM・アプリ内)** → ws-3(本単位はlatch_status_events挿入のみ)
  - **チャットAPI・実施自己申告(D-09)** → ws-4(actual_attended/cancelled_afterはNULLのまま)
  - **ブロック成立後即時適用(D-23)** → ws-5
  - **削除・退会のcalibration匿名化(D-13)** → ws-6(anonymized_atはNULLのまま)
  - **フロントエンド・02#15/#16/#18/#22/#25のstaging E2E** → ws-7〜9(本単位のci統合試験は#14の本体)
  - **latchesへの評価スナップショット列・match_candidates参照列** — §2.11の3段階特定で足りる(design §2.13)
  - **一覧のOFFSETページネーション・latches向けindex追加** — 05 §5はcursor規定・性能は10 §4で後で(design §2.13)
  - **responses要素の更新・削除(回答の訂正)** — 規定なし・書き込みは追記のみ
  - **回答API内のexpired遷移・completed_at書き込み** — ws-2(design §2.2)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 12で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(本単位のunit追加分・既存試験の追従を含む)
   検証: `make lint && make test` — ともにexit 0
2. **`docker compose build api` 後の `make test-ci` がグリーン**(既存1188+新規integration 17+groupengine追記1を含む)
   検証: `docker compose build api` exit 0 → `make test-ci` exit 0。報告書に全件数を記録
3. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし(空)
4. **マイグレーション・依存に差分なし**
   検証: `git diff main -- backend/alembic backend/pyproject.toml backend/uv.lock Makefile` — 出力なし(空)
5. **変更ファイルが§4の一覧どおり(作成13+変更8=21ファイル)**
   検証: Task 12の報告コミット後に `git diff --name-only main | sort` が§4の一覧(report込み)と完全一致。`git status --short` が空
6. **実時間参照が `core/clock.py` のみ**
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
7. **既存unit試験がすべてグリーンのまま**(stage1・group_engineへの追従が期待値変更ではなくSQLピン追記であることの証明として `make test` の全件数を報告書へ記録し、変更が§4列挙以外に及んでいないことを§4との突合で示す)

## 7. 報告形式

**結果ファイル**: `docs/plans/M3/ws-1-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M3 ws-1(LATCH応答系コア)実行報告

- ブランチ: m3-ws-1 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る(全件数も)> |
| 2 | docker compose build api → make test-ci | PASS/FAIL | <build完了とtest-ci全件数> |
| 3 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 4 | alembic・依存・Makefile無変更 | PASS/FAIL | <git diff出力(空なら「空」)> |
| 5 | 変更ファイル=§4の21ファイル | PASS/FAIL | <git diff --name-only出力> |
| 6 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 7 | 既存unit試験グリーン維持 | PASS/FAIL | <make test 全件数と§4列挙以外の期待値変更がないことの確認結果> |

## design §5 実装時確認事項の結果
- (なし — design §5の8件はすべて承認済み。本計画§9のIF確定事項から
  変更した場合は下に「変更前→変更後+理由」を記録する)

## 固定値の変更有無(design.md §2・本計画§9)
- 競合クローズSELECTのORDER BY id追加(§9-1): 変更なし / 変更あり
- 期限切れintegration試験=DB値操作(§9-2): 変更なし / 変更あり
- cursor 3キーとLatchValidationError新設(§9-3/4): 変更なし / 変更あり
- その他§9のIF確定事項: 変更なし / 変更あり(<前→後+理由>)

## (G3・ws-2〜ws-4への引継ぎ)
- ws-2: expiry_sweeperは本単位の_UPDATE_RESPONSEと同一WHERE(06 §6・C9)。
  matched→completed・Intent期限切れバッチもws-2
- ws-3: 通知はlatch_status_events(to_status='matched'/'rejected')と
  latches.statusを参照して実装
- 観測: 回答APIの固定文言一覧は計画§9-13

## コミット一覧
<git log --oneline main..HEAD>

## 補足(詰まった点・判断した点があれば)
<自由記述>
```

**検証手順(design §4.3。報告書の完了条件2に対応)**:

1. `make lint && make test` — unit全件グリーン
2. `docker compose build api` — **test-ciの前に必ず先行**(STATUS運用ルール4)
3. `make test-ci` — 既存1188+新規18前後がグリーン
4. basename一意確認(運用ルール5): `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空であることを報告書に記録
5. lint再実行(test-ciにlintが含まれないため・ws-4運用メモ): `make lint`
6. test-ci後の残存確認: users/intents/latches系の残行が0件(subject prefix `m3ws1-` で掃除・teardownの実効性)
   ```sql
   SELECT COUNT(*) FROM users WHERE auth_subject LIKE 'm3ws1-%';
   SELECT COUNT(*) FROM intents WHERE user_id IN
     (SELECT id FROM users WHERE auth_subject LIKE 'm3ws1-%');
   SELECT COUNT(*) FROM latches WHERE intent_ids && ARRAY(
     SELECT id FROM intents WHERE user_id IN
     (SELECT id FROM users WHERE auth_subject LIKE 'm3ws1-%'));
   ```
   すべて0件であること。Redisは `m3ws1-` prefixでSCANして0件

## 8. 実装ステップ(TDD。Task 1〜12の順で実行する)

### Task 1: layer3.bigramsのpublic化(segment判定の再利用土台)

**Files:**
- Modify: `backend/src/latch/worker/matching/layer3.py:102-110`(_bigramsの直後へ追記)
- Test: `backend/tests/unit/matching/test_layer3.py`(追記)

**Interfaces:**
- Produces: `latch.worker.matching.layer3.bigrams(texts: tuple[str, ...]) -> frozenset[str]`(Task 3のcalibration.pyがimport)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_layer3.py` の末尾(既存の `test_bigrams_nfkc_short_and_no_cross_text` の後)へ追記:

```python
def test_bigrams_public_wrapper_matches_private():
    """public版bigramsは_bigramsと同一実装(segment判定が再利用・M3 ws-1)。"""
    from latch.worker.matching.layer3 import bigrams

    assert bigrams(("焼肉", "ビアバー")) == _bigrams(("焼肉", "ビアバー"))
    assert bigrams(()) == frozenset()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_layer3.py::test_bigrams_public_wrapper_matches_private -v`
Expected: FAIL with "ImportError: cannot import name 'bigrams'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/worker/matching/layer3.py` の `_bigrams` 定義(102〜110行)の直後へ追記:

```python
def bigrams(texts: tuple[str, ...]) -> frozenset[str]:
    """文字bigram集合のpublic版(M3 ws-1 design §2.6)。

    segment判定(calibration)が語彙計算と同一のトークン化を使うための公開IF。
    _bigramsと同一実装(パイプライン内でトークン化を一つに保つ)。
    """
    return _bigrams(texts)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_layer3.py -v`
Expected: PASS(既存試験含む全件・`_bigrams` は削除していないため既存importは無傷)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/worker/matching/layer3.py backend/tests/unit/matching/test_layer3.py
git commit -m "feat: expose layer3 bigrams for segment classification"
```

### Task 2: latchesパッケージ雛形 — errors.py・schemas.py・__init__.py

**Files:**
- Create: `backend/src/latch/latches/__init__.py`
- Create: `backend/src/latch/latches/errors.py`
- Create: `backend/src/latch/latches/schemas.py`
- Test: `backend/tests/unit/latches/test_latches_service.py`(新規作成)

**Interfaces:**
- Produces: `LatchesError` 基底と `http_status`/`code` 属性を持つ例外8種(Task 6/8が消費・main.pyハンドラが変換)。`ResponseRequest`・`LatchSummaryOut`・`LatchEnvelope`・`ParticipantOut`・`LatchDetailOut`・`LatchDetailEnvelope`・`LatchListResponse`(Task 7/8が消費)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/latches/` ディレクトリを作成し(`__init__.py` は置かない)、`test_latches_service.py` を新規作成:

```python
"""latchesドメインservice・例外・スキーマのunit試験(M3 ws-1 design §4.1)。

storeはスタブ(行値を返すだけ)でSQLに依存しない。時刻はFakeClock。
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.latches.errors import (
    AlreadyAnsweredError,
    DependencyUnavailableError,
    ForbiddenError,
    LatchesError,
    LatchClosedError,
    LatchExpiredError,
    LatchNotFoundError,
    LatchValidationError,
)
from latch.latches.schemas import (
    LatchDetailOut,
    LatchEnvelope,
    LatchListResponse,
    LatchSummaryOut,
    ResponseRequest,
    ParticipantOut,
)

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


def test_error_codes_and_statuses():
    """例外はhttp_status+code固定(design §2.9の対応表・05 §5エラー形式)。"""
    cases = [
        (LatchNotFoundError, 404, "NOT_FOUND"),
        (ForbiddenError, 403, "FORBIDDEN"),
        (LatchValidationError, 422, "VALIDATION_ERROR"),
        (LatchExpiredError, 409, "LATCH_EXPIRED"),
        (AlreadyAnsweredError, 409, "ALREADY_ANSWERED"),
        (LatchClosedError, 409, "LATCH_CLOSED"),
        (DependencyUnavailableError, 503, "DEPENDENCY_UNAVAILABLE"),
    ]
    for exc_cls, status, code in cases:
        assert exc_cls.http_status == status, exc_cls
        assert exc_cls.code == code, exc_cls
        assert issubclass(exc_cls, LatchesError)


def test_response_request_accepts_three_values_only():
    """responseはyes/no/deferの3値のみ(引用#2)。値域外はValidationError。"""
    assert ResponseRequest(response="yes").response == "yes"
    assert ResponseRequest(response="no").response == "no"
    assert ResponseRequest(response="defer").response == "defer"
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ResponseRequest(response="maybe")


def test_summary_shape_has_no_responses_field():
    """LatchSummaryOutはresponses配列を持たない(引用#22・Review Focus 5)。

    応答へ他者の回答種別が漏れない構造ピン。
    """
    fields = set(LatchSummaryOut.model_fields)
    assert "responses" not in fields
    assert fields == {
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
    }


def test_detail_extends_summary_with_release_fields():
    """詳細は一覧要素+解放情報3字段(matched/completedのみ値が入る・design §2.8)。"""
    assert set(LatchDetailOut.model_fields) - set(LatchSummaryOut.model_fields) == {
        "participants",
        "time_summary",
        "area_name",
    }
    assert "user_id" in ParticipantOut.model_fields
    assert "display_name" in ParticipantOut.model_fields
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_latches_service.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'latch.latches'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/latches/__init__.py`(Task 8で完成形へ拡張):

```python
"""LATCHドメイン(M3 ws-1)。回答API・一覧・詳細・Calibration記録。"""
```

`backend/src/latch/latches/errors.py`:

```python
"""latchesドメイン例外階層(M3 ws-1 design §2.9・§3.1)。

各例外は http_status と code(05 §5エラー形式表)を固定で持つ。main.py の
ハンドラが共通envelopeへ変換する。例外メッセージにProposal本文・回答内容
を混ぜない(08 §2.4)— 呼び出し側は固定文言のみを渡す(§9-13)。
"""

from __future__ import annotations


class LatchesError(Exception):
    """latchesドメインエラーの基底。http_status/code を持つ(ハンドラが消費する)。"""

    http_status: int
    code: str


class LatchNotFoundError(LatchesError):
    """対象LATCHが存在しない。未登録JWT(User行なし)も同じ404(引用#17)。"""

    http_status = 404
    code = "NOT_FOUND"


class ForbiddenError(LatchesError):
    """参加者以外の操作(05 §5)。存在秘匿の404ではなく403(引用#17)。"""

    http_status = 403
    code = "FORBIDDEN"


class LatchValidationError(LatchesError):
    """リクエスト検証422(cursor形式不正等・intentsのIntentValidationError同型)。"""

    http_status = 422
    code = "VALIDATION_ERROR"


class LatchExpiredError(LatchesError):
    """回答期限切れ(response_deadline・expires_at経過・引用#1)。"""

    http_status = 409
    code = "LATCH_EXPIRED"


class AlreadyAnsweredError(LatchesError):
    """同一ユーザーの二重回答(引用#1)。"""

    http_status = 409
    code = "ALREADY_ANSWERED"


class LatchClosedError(LatchesError):
    """終端済み・未提示candidate・参加Intent変化によるクローズ(引用#1・01 §8)。"""

    http_status = 409
    code = "LATCH_CLOSED"


class DependencyUnavailableError(LatchesError):
    """DB・Redis等の依存障害(05 §5「全API」)。"""

    http_status = 503
    code = "DEPENDENCY_UNAVAILABLE"
```

`backend/src/latch/latches/schemas.py`:

```python
"""latches APIの入出力スキーマ(M3 ws-1 design §2.8・§3.1)。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ResponseValue = Literal["yes", "no", "defer"]


class ResponseRequest(BaseModel):
    """POST /v1/latches/{id}/response のbody(05 §5)。値域外は422(引用#1)。"""

    response: ResponseValue


class LatchSummaryOut(BaseModel):
    """一覧要素・POST response応答のlatch要素(design §2.8)。

    responses配列は持たない(他者の回答種別を応答へ出さない — 引用#22)。
    my_responseは自分の回答のみ・remaining_responsesは人数のみ。
    """

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


class LatchEnvelope(BaseModel):
    """POST /v1/latches/{id}/response の200応答(05 §5の {"latch": {...}})。"""

    latch: LatchSummaryOut


class ParticipantOut(BaseModel):
    """成立後の参加者情報(03 §6・引用#21)。profileはbio等をそのまま返す。"""

    user_id: uuid.UUID
    display_name: str
    profile: dict


class LatchDetailOut(LatchSummaryOut):
    """詳細応答のlatch要素。participants等はmatched/completedのみ非null(design §2.8)。"""

    participants: list[ParticipantOut] | None = None
    time_summary: str | None = None
    area_name: str | None = None


class LatchDetailEnvelope(BaseModel):
    latch: LatchDetailOut


class LatchListResponse(BaseModel):
    """GET /v1/latches 応答(05 §5共通規定・cursor改頁)。"""

    items: list[LatchSummaryOut]
    next_cursor: str | None = None
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_latches_service.py -v`
Expected: PASS(4件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/latches/ backend/tests/unit/latches/
git commit -m "feat: latches domain skeleton (errors + schemas)"
```

### Task 3: calibration.py — segment判定(segment_texts・classify_segment)

**Files:**
- Create: `backend/src/latch/latches/calibration.py`(本Taskでsegment部のみ)
- Test: `backend/tests/unit/latches/test_calibration.py`(新規作成)

**Interfaces:**
- Consumes: `latch.worker.matching.layer3.bigrams`(Task 1)
- Produces: `segment_texts(structured_data: object) -> tuple[str, ...]`・`classify_segment(texts_a, texts_b) -> str`("lexical"|"semantic")

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/latches/test_calibration.py` を新規作成:

```python
"""calibration純計算部のunit試験(M3 ws-1 design §4.1)。

segment判定・評価行3段階特定・minペア選択・prediction組み立て。
すべて純関数(決定的・DBなし)。
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from latch.latches.calibration import (
    classify_segment,
    segment_texts,
)


def test_segment_texts_uses_secondary_when_present():
    """category_secondary非nullならその1要素(引用#14・design §2.6)。"""
    sd = {
        "category_secondary": "焼肉",
        "soft_constraints": [{"text": "静かな店で"}],
    }
    assert segment_texts(sd) == ("焼肉",)


def test_segment_texts_falls_back_to_soft_constraints_including_downgraded():
    """secondaryがnullならsoft_constraints全文言(降格文言を含む — 09 §2.3)。"""
    sd = {
        "category_secondary": None,
        "soft_constraints": [
            {"text": "静かな店で"},
            {"text": "深夜でも", "downgraded_from_ng": True},
            {"downgraded_from_ng": True},  # textなし要素は除外
            "不正要素",
        ],
    }
    assert segment_texts(sd) == ("静かな店で", "深夜でも")


def test_segment_texts_both_missing_returns_empty():
    """双方null・soft_constraintsなしは空(→semanticへ流れる)。"""
    assert segment_texts({"category_secondary": None}) == ()
    assert segment_texts("not-a-dict") == ()  # 構造想定外は安全側
    assert segment_texts('{"category_secondary": "焼肉"}') == ("焼肉",)  # str JSONB


def test_classify_segment_lexical_on_shared_bigram():
    """表層トークン一致(bigram積集合非空)→lexical(引用#14)。"""
    assert classify_segment(("焼肉",), ("焼肉",)) == "lexical"
    assert classify_segment(("焼肉食べたい",), ("焼肉に行こう",)) == "lexical"


def test_classify_segment_semantic_on_no_overlap():
    """一致なし→semantic。双方空もsemantic(design §2.6)。"""
    assert classify_segment(("焼肉",), ("イタリアン",)) == "semantic"
    assert classify_segment((), ()) == "semantic"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_calibration.py -v`
Expected: FAIL with "cannot import name 'segment_texts'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/latches/calibration.py` を新規作成:

```python
"""Calibration記録の純計算部(M3 ws-1 design §2.5・§2.6・§2.11・§2.12)。

prediction組み立て(1対1評価行3段階特定・グループminペア選択)と
segment判定(09 §2.3)。DB・asyncを持たない(unit試験は決定的)。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from latch.worker.matching.layer3 import bigrams


def segment_texts(structured_data: object) -> tuple[str, ...]:
    """segment判定対象のテキスト組(09 §2.3・design §2.6)。

    category_secondary(保存形式の平キー・latch_engine._read_intent_inputsと
    同一の読み方)が非nullならその1要素、nullならsoft_constraints全文言
    (downgraded_from_ngを含む — 09 §2.3は文言からの除外を指定していない)。
    soft_texts(語彙重なり・降格除外)とは対象が違う別関数(Layer 3は06 §4の
    確定値・segmentは09 §2.3の文言指定)。構造想定外は空(安全側)。
    """
    if isinstance(structured_data, str):
        try:
            structured_data = json.loads(structured_data)
        except json.JSONDecodeError:
            return ()
    if not isinstance(structured_data, dict):
        return ()
    secondary = structured_data.get("category_secondary")
    if isinstance(secondary, str) and secondary:
        return (secondary,)
    raw = structured_data.get("soft_constraints")
    if not isinstance(raw, list):
        return ()
    return tuple(
        item["text"]
        for item in raw
        if isinstance(item, dict)
        and isinstance(item.get("text"), str)
        and bool(item["text"])
    )


def classify_segment(texts_a: tuple[str, ...], texts_b: tuple[str, ...]) -> str:
    """bigram積集合が非空→lexical・空→semantic(双方空はsemantic)。"""
    if bigrams(texts_a) & bigrams(texts_b):
        return "lexical"
    return "semantic"
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_calibration.py -v`
Expected: PASS(5件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/latches/calibration.py backend/tests/unit/latches/test_calibration.py
git commit -m "feat: segment classification for calibration records"
```

### Task 4: calibration.py — prediction組み立て(3段階特定・minペア・build_prediction)

**Files:**
- Modify: `backend/src/latch/latches/calibration.py`(追記)
- Test: `backend/tests/unit/latches/test_calibration.py`(追記)

**Interfaces:**
- Produces: `EvalRow(jev_result, latch_score, updated_at)`・`pick_eval_row(rows, score, latch_created) -> EvalRow | None`・`PairEvalRow(a, b, va, vb, jev_result)`・`select_versioned_pairs(rows, versions) -> list[PairEvalRow]`・`pick_min_pair(rows) -> PairEvalRow | None`・`build_prediction(jev_result, l_score, segment) -> dict`(Task 6のserviceが消費)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/latches/test_calibration.py` へ追記(import部へ追記):
```python
from latch.latches.calibration import (
    EvalRow,
    PairEvalRow,
    build_prediction,
    classify_segment,
    pick_eval_row,
    pick_min_pair,
    segment_texts,
    select_versioned_pairs,
)
```
関数部へ追記:

```python
T0 = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


def _jev(wa: float, wb: float, provider: str = "typesafe_jev") -> dict:
    return {
        "would_a_accept_b": wa,
        "would_b_accept_a": wb,
        "jev_5axis": {
            "purpose_fit": {"value": 0.5, "confidence": 0.9},
            "latent_yes": {"value": 0.4, "confidence": None},
        },
        "provider": provider,
        "model": "jev-1.13.0",
    }


def test_pick_eval_row_first_stage_score_match():
    """第1段: latch_score一致行の最新(design §2.11)。rowsはupdated_at降順。"""
    r_new = EvalRow(
        jev_result=_jev(0.9, 0.8),
        latch_score=Decimal("0.85"),
        updated_at=T0 + timedelta(minutes=10),
    )
    r_old_match = EvalRow(
        jev_result=_jev(0.7, 0.7),
        latch_score=Decimal("0.70"),
        updated_at=T0,
    )
    rows = [r_new, r_old_match]  # 降順
    hit = pick_eval_row(rows, Decimal("0.70"), T0 + timedelta(hours=1))
    assert hit is r_old_match


def test_pick_eval_row_second_stage_created_at_bound():
    """第2段: score不一致なら latches.created_at 以前の最新(jev_resultあり)。"""
    newer = EvalRow(
        jev_result=_jev(0.9, 0.9),
        latch_score=None,
        updated_at=T0 + timedelta(hours=2),  # created_at後
    )
    older = EvalRow(
        jev_result=_jev(0.8, 0.8),
        latch_score=None,
        updated_at=T0 + timedelta(hours=1),  # created_at以前
    )
    rows = [newer, older]
    latch_created = T0 + timedelta(hours=1, minutes=30)
    assert pick_eval_row(rows, Decimal("0.85"), latch_created) is older


def test_pick_eval_row_third_stage_falls_back_to_latest():
    """第3段: 第2段でも取れないなら同対の最新(世代不問)。全行なしはNone。"""
    only = EvalRow(
        jev_result=_jev(0.9, 0.9),
        latch_score=None,
        updated_at=T0 + timedelta(hours=3),
    )
    rows = [only]
    assert pick_eval_row(rows, Decimal("0.85"), T0) is only
    assert pick_eval_row([], Decimal("0.85"), T0) is None


def _pair(a_hex: str, b_hex: str, wa: float, wb: float) -> PairEvalRow:
    return PairEvalRow(
        a=uuid.UUID(a_hex),
        b=uuid.UUID(b_hex),
        va=1,
        vb=1,
        jev_result=_jev(wa, wb),
    )


A = "00000000-0000-0000-0000-00000000000a"
B = "00000000-0000-0000-0000-00000000000b"
C = "00000000-0000-0000-0000-00000000000c"


def test_select_versioned_pairs_filters_by_version():
    """versions一致のペア行のみ(UNIQUE(a,b,va,vb)で高々1行・引用#25)。"""
    va2 = PairEvalRow(
        a=uuid.UUID(A),
        b=uuid.UUID(B),
        va=2,
        vb=1,
        jev_result=_jev(0.5, 0.5),
    )
    rows = [_pair(A, B, 0.9, 0.8), va2, _pair(A, C, 0.7, 0.7), _pair(B, C, 0.6, 0.6)]
    versions = {uuid.UUID(A): 1, uuid.UUID(B): 1, uuid.UUID(C): 1}
    got = select_versioned_pairs(rows, versions)
    assert len(got) == 3  # 3人=3ペアすべて現行世代
    assert all(r.va == 1 for r in got)


def test_pick_min_pair_minimum_mutual_with_tiebreak():
    """MutualScore最小ペア。同点は(a,b)辞書順最小で決定的(design §2.12)。"""
    ab = _pair(A, B, 0.9, 0.8)  # mutual 0.8
    ac = _pair(A, C, 0.6, 0.9)  # mutual 0.6 ← 最小
    bc = _pair(B, C, 0.7, 0.7)  # mutual 0.7
    assert pick_min_pair([ab, ac, bc]) is ac
    # 同点(双方mutual 0.6)は辞書順最小(a,b)組
    ab2 = _pair(A, B, 0.6, 0.9)
    ac2 = _pair(A, C, 0.6, 0.9)
    assert pick_min_pair([ac2, ab2]) is ab2
    assert pick_min_pair([]) is None


def test_build_prediction_shape():
    """prediction=would_*・MutualScore=min・L・jev_5axis・provider/model・segment(引用#13/#14)。"""
    got = build_prediction(_jev(0.9, 0.7), l_score=0.85, segment="lexical")
    assert got["would_a_accept_b"] == 0.9
    assert got["would_b_accept_a"] == 0.7
    assert got["MutualScore"] == 0.7
    assert got["L"] == 0.85
    assert got["jev_5axis"]["purpose_fit"]["value"] == 0.5
    assert got["provider"] == "typesafe_jev"
    assert got["model"] == "jev-1.13.0"
    assert got["segment"] == "lexical"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_calibration.py -v`
Expected: FAIL with "ImportError: cannot import name 'EvalRow'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/latches/calibration.py` へ追記(classify_segmentの後):

```python
@dataclass(frozen=True)
class EvalRow:
    """1対1の評価行材料(jev_resultあり行・design §2.11)。

    rowsは呼び出し側SQLが updated_at DESC で並べたもの(第1段の「一致行の
    最新」=走査最初の一致で表現できる)。
    """

    jev_result: dict
    latch_score: Decimal | None
    updated_at: datetime


def pick_eval_row(
    rows: list[EvalRow], score: Decimal, latch_created: datetime
) -> EvalRow | None:
    """1対1の評価行3段階特定(design §2.11)。

    第1段=latch_score一致の最新・第2段=updated_at<=latches.created_atの最新・
    第3段=最新(世代不問)。全段失敗=None(呼び出し側はレコードを作らず
    構造化ログのみ — design §2.5)。NUMERIC同値判定(Decimal ==・丸め問題なし)。
    """
    for row in rows:  # 第1段
        if row.latch_score == score:
            return row
    for row in rows:  # 第2段
        if row.updated_at <= latch_created:
            return row
    return rows[0] if rows else None  # 第3段


@dataclass(frozen=True)
class PairEvalRow:
    """グループの1ペア評価行(UNIQUE(a,b,va,vb)で一意・design §2.12)。"""

    a: uuid.UUID
    b: uuid.UUID
    va: int
    vb: int
    jev_result: dict


def select_versioned_pairs(
    rows: list[PairEvalRow], versions: dict[uuid.UUID, int]
) -> list[PairEvalRow]:
    """versions一致の全ペア評価行(members昇順の組合せごとに高々1行)。"""
    out: list[PairEvalRow] = []
    members = sorted(versions)
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            a, b = members[i], members[j]
            for r in rows:
                if (
                    r.a == a
                    and r.b == b
                    and r.va == versions[a]
                    and r.vb == versions[b]
                ):
                    out.append(r)
                    break
    return out


def pick_min_pair(rows: list[PairEvalRow]) -> PairEvalRow | None:
    """MutualScore最小ペア(同点は(a,b)辞書順最小で決定的・design §2.12)。"""
    if not rows:
        return None

    def key(r: PairEvalRow):
        return (
            min(
                float(r.jev_result["would_a_accept_b"]),
                float(r.jev_result["would_b_accept_a"]),
            ),
            r.a,
            r.b,
        )

    return min(rows, key=key)


def build_prediction(jev_result: dict, l_score: float, segment: str) -> dict:
    """prediction dict(07 §6・引用#13・#14)。jev_resultから必要キーを写す。

    MutualScore=min(would_a, would_b)。Lはlatches.score(提示時・引用#26)。
    """
    return {
        "would_a_accept_b": float(jev_result["would_a_accept_b"]),
        "would_b_accept_a": float(jev_result["would_b_accept_a"]),
        "MutualScore": min(
            float(jev_result["would_a_accept_b"]),
            float(jev_result["would_b_accept_a"]),
        ),
        "L": l_score,
        "jev_5axis": jev_result["jev_5axis"],
        "provider": jev_result["provider"],
        "model": jev_result["model"],
        "segment": segment,
    }
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_calibration.py -v`
Expected: PASS(11件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/latches/calibration.py backend/tests/unit/latches/test_calibration.py
git commit -m "feat: calibration prediction assembly (3-stage lookup, min pair)"
```

### Task 5: latches/store.py — SQL一式(conn受取関数群)

**Files:**
- Create: `backend/src/latch/latches/store.py`
- Test: `backend/tests/unit/latches/test_latches_store_sql.py`(新規作成)

**Interfaces:**
- Consumes: `latch.latches.calibration.EvalRow`・`PairEvalRow`(Task 4)
- Produces(Task 6/7のserviceが消費): `LatchRow` dataclass、`fetch_user_id`・`select_latch_for_update`・`select_latch`・`select_participant_intent`・`update_response`・`match_intents`・`select_conflicting_latches`・`cancel_latch`・`insert_latch_event`・`insert_calibration`・`fetch_pair_rows`・`fetch_group_pairs`・`fetch_member_scores`・`fetch_intents_structured`・`fetch_intents_geo`・`fetch_participants`・`select_latches_page`・`PageRow`

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/latches/test_latches_store_sql.py` を新規作成:

```python
"""latches storeのSQL検査(実dialect compile+句ピン・M3 ws-1 design §4.1)。

compile検査はintents/test_store_sql.pyの流儀(bind paramの部分認識ゼロ。
':name::type' 系の落穴を実dialectで再現)。句ピンは回答UPDATEの直列化条件
(06 §6+design §2.2のEXISTS)と競合クローズ・一覧の条件を文字列で確定。
"""

from sqlalchemy.dialects import postgresql

from latch.latches import store


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_all_sql_bind_params_fully_recognized():
    """compiled文字列に未変換の ':name' が残らない(部分認識ゼロ)。"""
    for name in dir(store):
        if not name.isupper():
            continue
        stmt = getattr(store, name)
        if not hasattr(stmt, "compile"):
            continue
        compiled = _compiled(stmt)
        # bind paramは %(name)s 形式へ置換される。':x' が残っていれば未認識
        for token in (
            ":latch_id",
            ":me",
            ":ids",
            ":item",
            ":new_status",
            ":now",
            ":self_id",
            ":member_ids",
            ":from_status",
            ":to_status",
            ":user_id",
            ":a",
            ":b",
            ":gid",
            ":intent_ids",
            ":prediction",
            ":proposal",
            ":responses",
            ":matched",
            ":provider",
            ":subject",
            ":tt",
            ":ct",
            ":lid",
            ":limit",
        ):
            assert token not in compiled, (name, token)


def test_update_response_serialization_conditions_pinned():
    """回答UPDATEのWHERE=06 §6確定値+参加Intent検査NOT EXISTS(design §2.2手順4)。"""
    sql = _compiled(store._UPDATE_RESPONSE)
    assert "responses = responses || CAST(:item AS jsonb)" in sql.replace("%(item)s", ":item").replace("%(new_status)s", ":new_status") or "|| " in sql
    raw = str(store._UPDATE_RESPONSE)
    assert "status IN ('proposed', 'partial_accept')" in raw
    assert "response_deadline > CAST(:now AS timestamptz)" in raw
    assert "expires_at > CAST(:now AS timestamptz)" in raw
    assert "NOT EXISTS" in raw
    assert "i.status NOT IN ('active', 'paused')" in raw
    assert "RETURNING id, status" in raw


def test_match_intents_includes_paused():
    """Intent matched化はactive+paused(承認事項3・design §2.3)。"""
    raw = str(store._MATCH_INTENTS)
    assert "SET status = 'matched'" in raw
    assert "status IN ('active', 'paused')" in raw
    assert "RETURNING id" in raw


def test_conflicting_latches_overlap_and_order_and_lock():
    """競合クローズ対象=自己除外・3状態・配列交差・ORDER BY id つきFOR UPDATE(§9-1)。"""
    raw = str(store._SELECT_CONFLICTING_LATCHES)
    assert "id <> CAST(:self_id AS uuid)" in raw
    assert "status IN ('candidate', 'proposed', 'partial_accept')" in raw
    assert "intent_ids && CAST(:member_ids AS uuid[])" in raw
    assert "ORDER BY id" in raw
    assert "FOR UPDATE" in raw


def test_latches_page_excludes_candidate_and_sorts():
    """一覧はcandidate除外・本人関与EXISTS・3キー昇降ソート(design §2.8)。"""
    raw = str(store._SELECT_LATCHES_PAGE)
    assert "l.status <> 'candidate'" in raw
    assert "i.user_id = CAST(:me AS uuid)" in raw
    assert "ORDER BY target_time ASC, l.created_at DESC, l.id ASC" in raw
    assert "LIMIT :limit" in raw


def test_insert_calibration_columns_pinned():
    """calibration INSERTの列一式(actual_attended等は書かない・引用#13)。"""
    raw = str(store._INSERT_CALIBRATION)
    for col in (
        "latch_id",
        "intent_ids",
        "prediction",
        "proposal_snapshot",
        "actual_responses",
        "matched",
        "created_at",
        "updated_at",
    ):
        assert col in raw
    assert "actual_attended" not in raw
    assert "cancelled_after" not in raw
    assert "anonymized_at" not in raw


def test_pair_rows_ordered_by_updated_desc():
    """評価行取得はupdated_at降順(pick_eval_rowの走査前提・design §2.11)。"""
    assert "ORDER BY updated_at DESC" in str(store._SELECT_PAIR_ROWS)
    assert "jev_result IS NOT NULL" in str(store._SELECT_PAIR_ROWS)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_latches_store_sql.py -v`
Expected: FAIL with "ModuleNotFoundError"(latch.latches.store不在)

- [ ] **Step 3: 実装**

`backend/src/latch/latches/store.py` を新規作成(全文):

```python
"""latches永続化(text()生SQL・M3 ws-1 design §2.2・§2.3・§2.8・§2.11・§2.12)。

SQL定数+connを受け取るasync関数群(latch_engine/group_engine流儀。
トランザクションはserviceが統轄)。asyncpgのUUID復元は_coerce_uuid規律・
uuid[]のbindはlist[uuid.UUID]+SQL側CAST(group_calc.uuid_array §9-14規律)。
latchesにupdated_at列はなく、回答UPDATEが書くのはresponses・statusのみ
(design §2.2)。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from latch.latches.calibration import EvalRow, PairEvalRow


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT/RETURNING結果のUUID列復元(asyncpgサブクラス対策・origin.pyと同一)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _jsonb(value: object) -> object:
    """JSONB列の読取値復元(strならjson.loads・worker/embedding.pyと同規律)。"""
    if isinstance(value, str):
        return json.loads(value)
    return value


@dataclass(frozen=True)
class LatchRow:
    """latches 1行(_SELECT_LATCH_FOR_UPDATE / _SELECT_LATCH の読取結果)。"""

    id: uuid.UUID
    status: str
    intent_ids: list[uuid.UUID]
    responses: list[dict]
    response_deadline: datetime
    expires_at: datetime
    score: Decimal
    proposal: dict
    group_candidate_id: uuid.UUID | None
    created_at: datetime


@dataclass(frozen=True)
class GeoRow:
    """fetch_intents_geoの1行(詳細APIの集合情報構築用)。"""

    intent_id: uuid.UUID
    time_start: datetime
    lon: float
    lat: float


@dataclass(frozen=True)
class ParticipantRow:
    """fetch_participantsの1行(成立後の解放情報・引用#21)。"""

    intent_id: uuid.UUID
    user_id: uuid.UUID
    display_name: str
    profile: dict


@dataclass(frozen=True)
class PageRow:
    """一覧の1行(design §2.8。target_timeはソートキー計算値)。"""

    id: uuid.UUID
    status: str
    intent_ids: list[uuid.UUID]
    responses: list[dict]
    response_deadline: datetime
    expires_at: datetime
    proposal: dict
    created_at: datetime
    completed_at: datetime | None
    group_candidate_id: uuid | None
    target_time: datetime


# -- 回答処理(design §2.2手順1〜4・06 §6の直列化方式) --

_SELECT_LATCH_FOR_UPDATE = text("""
    SELECT id, status, intent_ids, responses, response_deadline, expires_at,
           score, proposal, group_candidate_id, created_at
    FROM latches WHERE id = CAST(:latch_id AS uuid)
    FOR UPDATE
""")

_SELECT_PARTICIPANT_INTENT = text("""
    SELECT id FROM intents
    WHERE id = ANY(CAST(:ids AS uuid[])) AND user_id = CAST(:me AS uuid)
""")

# 手順4: 条件付きUPDATE(06 §6のWHERE+参加Intent検査NOT EXISTS=design §2.2承認事項2)
_UPDATE_RESPONSE = text("""
    UPDATE latches
    SET responses = responses || CAST(:item AS jsonb),
        status = :new_status
    WHERE id = CAST(:latch_id AS uuid)
      AND status IN ('proposed', 'partial_accept')
      AND response_deadline > CAST(:now AS timestamptz)
      AND expires_at > CAST(:now AS timestamptz)
      AND NOT EXISTS (
          SELECT 1 FROM intents i
          WHERE i.id = ANY(latches.intent_ids)
            AND i.status NOT IN ('active', 'paused'))
    RETURNING id, status
""")

# Intent matched化(active+paused=承認事項3・design §2.3)
_MATCH_INTENTS = text("""
    UPDATE intents SET status = 'matched', updated_at = CAST(:now AS timestamptz)
    WHERE id = ANY(CAST(:ids AS uuid[])) AND status IN ('active', 'paused')
    RETURNING id
""")

# 競合クローズ対象(引用#7・design §2.3。ORDER BY id でロック順序を固定し
# 回答tx同士のデッドロック交差を構造的に排除 — 本計画§9-1)
_SELECT_CONFLICTING_LATCHES = text("""
    SELECT id, status FROM latches
    WHERE id <> CAST(:self_id AS uuid)
      AND status IN ('candidate', 'proposed', 'partial_accept')
      AND intent_ids && CAST(:member_ids AS uuid[])
    ORDER BY id
    FOR UPDATE
""")

_CANCEL_LATCH = text("""
    UPDATE latches SET status = 'cancelled'
    WHERE id = CAST(:latch_id AS uuid)
      AND status IN ('candidate', 'proposed', 'partial_accept')
    RETURNING id
""")

_INSERT_LATCH_EVENT = text("""
    INSERT INTO latch_status_events
        (latch_id, from_status, to_status, user_id, created_at)
    VALUES (CAST(:latch_id AS uuid), CAST(:from_status AS text),
            :to_status, CAST(:user_id AS uuid), CAST(:now AS timestamptz))
""")

# -- Calibration(design §2.5・引用#13) --

_INSERT_CALIBRATION = text("""
    INSERT INTO calibration_records
        (latch_id, intent_ids, prediction, proposal_snapshot,
         actual_responses, matched, created_at, updated_at)
    VALUES (CAST(:latch_id AS uuid), CAST(:intent_ids AS uuid[]),
            CAST(:prediction AS jsonb), CAST(:proposal AS jsonb),
            CAST(:responses AS jsonb), :matched,
            CAST(:now AS timestamptz), CAST(:now AS timestamptz))
    RETURNING id
""")

# -- 評価行特定(design §2.11・§2.12) --

_SELECT_PAIR_ROWS = text("""
    SELECT jev_result, latch_score, updated_at
    FROM match_candidates
    WHERE intent_a_id = CAST(:a AS uuid) AND intent_b_id = CAST(:b AS uuid)
      AND jev_result IS NOT NULL
    ORDER BY updated_at DESC
""")

_SELECT_GROUP_PAIRS = text("""
    SELECT intent_a_id, intent_b_id, intent_a_version, intent_b_version, jev_result
    FROM match_candidates
    WHERE intent_a_id = ANY(CAST(:ids AS uuid[]))
      AND intent_b_id = ANY(CAST(:ids AS uuid[]))
      AND jev_result IS NOT NULL
    ORDER BY intent_a_id, intent_b_id, updated_at DESC
""")

_SELECT_MEMBER_SCORES = text("""
    SELECT member_scores FROM group_candidates WHERE id = CAST(:gid AS uuid)
""")

# -- 読取系(一覧・詳細・解放情報・design §2.8) --

_SELECT_USER_ID = text("""
    SELECT id FROM users
    WHERE auth_provider = :provider AND auth_subject = :subject
""")

_SELECT_LATCHES_PAGE = text("""
    SELECT l.id, l.status, l.intent_ids, l.responses, l.response_deadline,
           l.expires_at, l.proposal, l.created_at, l.completed_at,
           l.group_candidate_id,
           (SELECT max(i.time_start) FROM intents i
            WHERE i.id = ANY(l.intent_ids)) AS target_time
    FROM latches l
    WHERE l.status <> 'candidate'
      AND EXISTS (SELECT 1 FROM intents i
                  WHERE i.id = ANY(l.intent_ids)
                    AND i.user_id = CAST(:me AS uuid))
      AND (:tt IS NULL OR
           (SELECT max(i.time_start) FROM intents i
            WHERE i.id = ANY(l.intent_ids)) > CAST(:tt AS timestamptz)
           OR ((SELECT max(i.time_start) FROM intents i
                WHERE i.id = ANY(l.intent_ids)) = CAST(:tt AS timestamptz)
               AND (l.created_at < CAST(:ct AS timestamptz)
                    OR (l.created_at = CAST(:ct AS timestamptz)
                        AND l.id > CAST(:lid AS uuid)))))
    ORDER BY target_time ASC, l.created_at DESC, l.id ASC
    LIMIT :limit
""")

_SELECT_LATCH = text("""
    SELECT id, status, intent_ids, responses, response_deadline, expires_at,
           score, proposal, group_candidate_id, created_at
    FROM latches WHERE id = CAST(:latch_id AS uuid)
""")

_SELECT_INTENTS_STRUCTURED = text("""
    SELECT id, structured_data FROM intents WHERE id = ANY(CAST(:ids AS uuid[]))
""")

_SELECT_INTENTS_GEO = text("""
    SELECT id, time_start,
           ST_X(geo_center::geometry) AS lon, ST_Y(geo_center::geometry) AS lat
    FROM intents WHERE id = ANY(CAST(:ids AS uuid[]))
""")

_SELECT_PARTICIPANTS = text("""
    SELECT i.id AS intent_id, i.user_id, u.display_name, u.profile
    FROM intents i JOIN users u ON u.id = i.user_id
    WHERE i.id = ANY(CAST(:ids AS uuid[]))
""")


def _latch_row(mapping) -> LatchRow:
    """_SELECT_LATCH(_FOR_UPDATE)のmappings行→LatchRow。"""
    return LatchRow(
        id=_coerce_uuid(mapping["id"]),
        status=mapping["status"],
        intent_ids=[_coerce_uuid(x) for x in mapping["intent_ids"]],
        responses=list(_jsonb(mapping["responses"]) or []),
        response_deadline=mapping["response_deadline"],
        expires_at=mapping["expires_at"],
        score=mapping["score"] if isinstance(mapping["score"], Decimal) else Decimal(str(mapping["score"])),
        proposal=dict(_jsonb(mapping["proposal"]) or {}),
        group_candidate_id=(
            _coerce_uuid(mapping["group_candidate_id"])
            if mapping["group_candidate_id"] is not None
            else None
        ),
        created_at=mapping["created_at"],
    )


async def fetch_user_id(
    conn: AsyncConnection, provider: str, subject: str
) -> uuid.UUID | None:
    """認証subject→users.id。未登録JWT=None(呼び出し側は404・引用#17)。"""
    res = await conn.execute(
        _SELECT_USER_ID, {"provider": provider, "subject": subject}
    )
    row = res.first()
    return _coerce_uuid(row[0]) if row is not None else None


async def select_latch_for_update(
    conn: AsyncConnection, latch_id: uuid.UUID
) -> LatchRow | None:
    """手順1: 行ロックつき読取(design §2.2)。"""
    row = (
        (await conn.execute(_SELECT_LATCH_FOR_UPDATE, {"latch_id": latch_id}))
        .mappings()
        .first()
    )
    return _latch_row(row) if row is not None else None


async def select_latch(conn: AsyncConnection, latch_id: uuid.UUID) -> LatchRow | None:
    """詳細API用の読取専用行(FOR UPDATEなし)。"""
    row = (
        (await conn.execute(_SELECT_LATCH, {"latch_id": latch_id}))
        .mappings()
        .first()
    )
    return _latch_row(row) if row is not None else None


async def select_participant_intent(
    conn: AsyncConnection, intent_ids: list[uuid.UUID], user_id: uuid.UUID
) -> uuid.UUID | None:
    """手順2a: 本人の参加Intent特定(0行=None→呼び出し側403・引用#17)。"""
    res = await conn.execute(
        _SELECT_PARTICIPANT_INTENT, {"ids": list(intent_ids), "me": user_id}
    )
    row = res.first()
    return _coerce_uuid(row[0]) if row is not None else None


async def update_response(
    conn: AsyncConnection,
    *,
    latch_id: uuid.UUID,
    item: dict,
    new_status: str,
    now: datetime,
) -> tuple[uuid.UUID, str] | None:
    """手順4: 条件付きUPDATE。None=影響0(呼び出し側は事前検査の分類で409)。"""
    res = await conn.execute(
        _UPDATE_RESPONSE,
        {
            "latch_id": latch_id,
            "item": json.dumps(item, ensure_ascii=False),
            "new_status": new_status,
            "now": now,
        },
    )
    row = res.first()
    if row is None:
        return None
    return _coerce_uuid(row[0]), row[1]


async def match_intents(
    conn: AsyncConnection, intent_ids: list[uuid.UUID], now: datetime
) -> list[uuid.UUID]:
    """成立時の参加Intent matched化(引用#9・design §2.3)。"""
    res = await conn.execute(
        _MATCH_INTENTS, {"ids": list(intent_ids), "now": now}
    )
    return [_coerce_uuid(r[0]) for r in res.fetchall()]


async def select_conflicting_latches(
    conn: AsyncConnection, self_id: uuid.UUID, member_ids: list[uuid.UUID]
) -> list[tuple[uuid.UUID, str]]:
    """競合クローズ対象の行ロック取得(ORDER BY id・§9-1)。"""
    res = await conn.execute(
        _SELECT_CONFLICTING_LATCHES,
        {"self_id": self_id, "member_ids": list(member_ids)},
    )
    return [(_coerce_uuid(r[0]), r[1]) for r in res.fetchall()]


async def cancel_latch(conn: AsyncConnection, latch_id: uuid.UUID) -> bool:
    """競合クローズ1行の条件付きcancelled UPDATE(競合負けはFalse)。"""
    res = await conn.execute(_CANCEL_LATCH, {"latch_id": latch_id})
    return res.first() is not None


async def insert_latch_event(
    conn: AsyncConnection,
    latch_id: uuid.UUID,
    from_status: str | None,
    to_status: str,
    user_id: uuid.UUID | None,
    now: datetime,
) -> None:
    """latch_status_events挿入(回答起因=user_id・システム起因=None・引用#12)。"""
    await conn.execute(
        _INSERT_LATCH_EVENT,
        {
            "latch_id": latch_id,
            "from_status": from_status,
            "to_status": to_status,
            "user_id": user_id,
            "now": now,
        },
    )


async def insert_calibration(
    conn: AsyncConnection,
    *,
    latch_id: uuid.UUID,
    intent_ids: list[uuid.UUID],
    prediction: dict,
    proposal: dict,
    responses: list[dict],
    matched: bool,
    now: datetime,
) -> uuid.UUID:
    """calibration_records INSERT(design §2.5)。"""
    res = await conn.execute(
        _INSERT_CALIBRATION,
        {
            "latch_id": latch_id,
            "intent_ids": list(intent_ids),
            "prediction": json.dumps(prediction, ensure_ascii=False),
            "proposal": json.dumps(proposal, ensure_ascii=False),
            "responses": json.dumps(responses, ensure_ascii=False),
            "matched": matched,
            "now": now,
        },
    )
    return _coerce_uuid(res.first()[0])


async def fetch_pair_rows(
    conn: AsyncConnection, a_id: uuid.UUID, b_id: uuid.UUID
) -> list[EvalRow]:
    """1対1の評価行(jev_resultあり・updated_at降順・design §2.11)。"""
    res = await conn.execute(_SELECT_PAIR_ROWS, {"a": a_id, "b": b_id})
    out: list[EvalRow] = []
    for r in res.fetchall():
        jev = _jsonb(r[0])
        out.append(
            EvalRow(
                jev_result=jev if isinstance(jev, dict) else {},
                latch_score=r[1] if r[1] is None or isinstance(r[1], Decimal) else Decimal(str(r[1])),
                updated_at=r[2],
            )
        )
    return out


async def fetch_group_pairs(
    conn: AsyncConnection, ids: list[uuid.UUID]
) -> list[PairEvalRow]:
    """集合内ペアの評価行(jev_resultあり・design §2.12)。"""
    res = await conn.execute(_SELECT_GROUP_PAIRS, {"ids": list(ids)})
    out: list[PairEvalRow] = []
    for r in res.fetchall():
        jev = _jsonb(r[4])
        out.append(
            PairEvalRow(
                a=_coerce_uuid(r[0]),
                b=_coerce_uuid(r[1]),
                va=r[2],
                vb=r[3],
                jev_result=jev if isinstance(jev, dict) else {},
            )
        )
    return out


async def fetch_member_scores(conn: AsyncConnection, gid: uuid.UUID) -> dict | None:
    """group_candidates.member_scores({"seed_id","versions"}・STATUS引継ぎ③)。"""
    res = await conn.execute(_SELECT_MEMBER_SCORES, {"gid": gid})
    row = res.first()
    if row is None:
        return None
    ms = _jsonb(row[0])
    return ms if isinstance(ms, dict) else {}


async def fetch_intents_structured(
    conn: AsyncConnection, ids: list[uuid.UUID]
) -> list[dict]:
    """参加Intentのstructured_data(ids順に並べ替え・segment材料)。"""
    res = await conn.execute(_SELECT_INTENTS_STRUCTURED, {"ids": list(ids)})
    by_id: dict[uuid.UUID, dict] = {}
    for r in res.fetchall():
        sd = _jsonb(r[1])
        by_id[_coerce_uuid(r[0])] = sd if isinstance(sd, dict) else {}
    return [by_id[i] for i in ids if i in by_id]


async def fetch_intents_geo(
    conn: AsyncConnection, ids: list[uuid.UUID]
) -> list[GeoRow]:
    """参加Intentのtime_start・geo中心(ids順・詳細APIの集合情報)。"""
    res = await conn.execute(_SELECT_INTENTS_GEO, {"ids": list(ids)})
    by_id: dict[uuid.UUID, GeoRow] = {}
    for r in res.fetchall():
        by_id[_coerce_uuid(r[0])] = GeoRow(
            intent_id=_coerce_uuid(r[0]),
            time_start=r[1],
            lon=float(r[2]),
            lat=float(r[3]),
        )
    return [by_id[i] for i in ids if i in by_id]


async def fetch_participants(
    conn: AsyncConnection, ids: list[uuid.UUID]
) -> list[ParticipantRow]:
    """参加者の表示名・profile(ids順・成立後の解放情報・引用#21)。"""
    res = await conn.execute(_SELECT_PARTICIPANTS, {"ids": list(ids)})
    by_id: dict[uuid.UUID, ParticipantRow] = {}
    for r in res.fetchall():
        profile = _jsonb(r[3])
        by_id[_coerce_uuid(r[0])] = ParticipantRow(
            intent_id=_coerce_uuid(r[0]),
            user_id=_coerce_uuid(r[1]),
            display_name=r[2],
            profile=profile if isinstance(profile, dict) else {},
        )
    return [by_id[i] for i in ids if i in by_id]


async def select_latches_page(
    conn: AsyncConnection,
    *,
    me: uuid.UUID,
    before: tuple[datetime, datetime, uuid.UUID] | None,
    limit: int,
) -> list[PageRow]:
    """一覧(design §2.8)。before=cursor位置(target_time, created_at, id)。"""
    params: dict = {
        "me": me,
        "tt": before[0] if before else None,
        "ct": before[1] if before else None,
        "lid": before[2] if before else None,
        "limit": limit,
    }
    res = await conn.execute(_SELECT_LATCHES_PAGE, params)
    out: list[PageRow] = []
    for r in res.fetchall():
        out.append(
            PageRow(
                id=_coerce_uuid(r[0]),
                status=r[1],
                intent_ids=[_coerce_uuid(x) for x in r[2]],
                responses=list(_jsonb(r[3]) or []),
                response_deadline=r[4],
                expires_at=r[5],
                proposal=dict(_jsonb(r[6]) or {}),
                created_at=r[7],
                completed_at=r[8],
                group_candidate_id=r[9],
                target_time=r[10],
            )
        )
    return out
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_latches_store_sql.py -v`
Expected: PASS(7件)。`ruff format` の指摘が出たら `cd backend && uv run ruff format .` を当てる(長い行の折返しが入っても文字列定数は変わらない)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/latches/store.py backend/tests/unit/latches/test_latches_store_sql.py
git commit -m "feat: latches store (serialization SQL + read queries)"
```

### Task 6: latches/service.py — respond(回答API本体・手順0〜6)

**Files:**
- Create: `backend/src/latch/latches/service.py`(本Taskでrespond系のみ)
- Test: `backend/tests/unit/latches/test_latches_service.py`(追記)

**Interfaces:**
- Consumes: store(Task 5)・calibration(Task 3/4)・errors(Task 2)・`latch.geo.service.GeoService`の`reverse_geocode`(詳細APIのみで使用・Task 7)
- Produces: `LatchesService(clock, engine, geo)`・`compute_new_status(response, responses, total) -> str`・`make_latches_service(clock, engine)`(Task 8が消費)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/latches/test_latches_service.py` へ追記(import部へ):

```python
from latch.latches.service import LatchesService, compute_new_status
```

ファイル末尾へ追記:

```python
def _latch_row(**overrides) -> object:
    """store.LatchRow相当のスタブ(serviceは属性アクセスのみ)。"""
    from types import SimpleNamespace

    base = dict(
        id=uuid.uuid4(),
        status="proposed",
        intent_ids=[uuid.uuid4(), uuid.uuid4()],
        responses=[],
        response_deadline=NOW + timedelta(hours=1),
        expires_at=NOW + timedelta(days=5),
        score=Decimal("0.85"),
        proposal={"headcount": 2, "match_level": "medium"},
        group_candidate_id=None,
        created_at=NOW - timedelta(hours=1),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class StubStore:
    """store関数のスタブ(serviceはモジュール関数をmonkeypatchで差し替え)。"""


def test_compute_new_status_matrix():
    """手順3の純計算(1対1・3人・no/defer・design §2.4)。"""
    # 1対1: 片方yes→partial(必要人数2・まだ1)/揃う→matched
    assert compute_new_status("yes", [{"response": "yes"}], 2) == "partial_accept"
    assert (
        compute_new_status(
            "yes", [{"response": "yes"}, {"response": "yes"}], 2
        )
        == "matched"
    )
    # 3人: 2人yesのあとの3人目yes→matched(必要人数=|S|・D-06)
    assert (
        compute_new_status(
            "yes",
            [{"response": "yes"}, {"response": "yes"}, {"response": "yes"}],
            3,
        )
        == "matched"
    )
    assert (
        compute_new_status(
            "yes", [{"response": "yes"}, {"response": "yes"}], 3
        )
        == "partial_accept"
    )
    # no/deferは即rejected(グループでも部分成立なし・引用#10)
    assert compute_new_status("no", [{"response": "no"}], 2) == "rejected"
    assert (
        compute_new_status("defer", [{"response": "yes"}, {"response": "defer"}], 3)
        == "rejected"
    )
```

続き(サービスの409分類・スタブengine。同一ファイルへさらに追記):

```python
class FakeResult:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class ScriptedConn:
    """store呼び出しの結果を順に返すスタブ(intentsのStubStoreより薄い層)。"""

    def __init__(self, results: list):
        self._results = list(results)

    async def execute(self, stmt, params=None):
        if self._results:
            return self._results.pop(0)
        return FakeResult()


class ScriptedEngine:
    def __init__(self, results: list):
        self.conn = ScriptedConn(results)

    def begin(self):
        return self

    def connect(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


def _svc(engine) -> LatchesService:
    return LatchesService(clock=FakeClock(NOW), engine=engine, geo=None)


ME = uuid.uuid4()
PEER = uuid.uuid4()
I1, I2 = uuid.uuid4(), uuid.uuid4()
ROW = _latch_row()


def _mapping(row) -> dict:
    """store._latch_row相当のmapping(dict)。select_latch_for_update用。"""
    return {
        "id": row.id,
        "status": row.status,
        "intent_ids": row.intent_ids,
        "responses": row.responses,
        "response_deadline": row.response_deadline,
        "expires_at": row.expires_at,
        "score": row.score,
        "proposal": row.proposal,
        "group_candidate_id": row.group_candidate_id,
        "created_at": row.created_at,
    }


def _ret(value):
    """monkeypatch差し替え用の「常にvalueを返すasync関数」ファクトリ。"""

    async def _inner(*args, **kwargs):
        return value

    return _inner


async def test_respond_unregistered_jwt_404(monkeypatch):
    """未登録JWTは404(引用#17・intentsと同型)。"""
    from latch.latches import store as store_mod

    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(None))
    with pytest.raises(LatchNotFoundError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google",
            auth_subject="unknown",
            latch_id=uuid.uuid4(),
            response="yes",
        )


async def test_respond_participant_forbidden(monkeypatch):
    """参加者以外は403(存在秘匿しない・引用#17)。"""
    from latch.latches import store as store_mod

    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(ROW))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(None))
    with pytest.raises(ForbiddenError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google",
            auth_subject="s",
            latch_id=ROW.id,
            response="yes",
        )

```python
async def test_respond_classification_matrix(monkeypatch):
    """手順2の分類表を全分岐(期限切れ・既回答・終端済み・candidate・design §2.9)。"""
    from latch.latches import store as store_mod

    base_row = _latch_row()
    # 参加Intentは常に自分のもの
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))

    # 期限切れ(status=proposedのまま・期限後回答は409 LATCH_EXPIRED・引用#6)
    expired = _latch_row(
        response_deadline=NOW - timedelta(minutes=1)
    )
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(expired))
    with pytest.raises(LatchExpiredError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google", auth_subject="s",
            latch_id=expired.id, response="yes",
        )

    # expires_at切れもLATCH_EXPIRED
    expired2 = _latch_row(expires_at=NOW - timedelta(minutes=1))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(expired2))
    with pytest.raises(LatchExpiredError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google", auth_subject="s",
            latch_id=expired2.id, response="yes",
        )

    # 終端済み(rejected等・期限前)はLATCH_CLOSED
    closed = _latch_row(status="rejected")
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(closed))
    with pytest.raises(LatchClosedError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google", auth_subject="s",
            latch_id=closed.id, response="yes",
        )

    # 未提示candidateもLATCH_CLOSED(引用#24)
    cand = _latch_row(status="candidate")
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(cand))
    with pytest.raises(LatchClosedError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google", auth_subject="s",
            latch_id=cand.id, response="yes",
        )

    # 二重回答はALREADY_ANSWERED(期限前・proposed)
    answered = _latch_row(
        responses=[{"user_id": str(ME), "response": "yes"}]
    )
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(answered))
    with pytest.raises(AlreadyAnsweredError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google", auth_subject="s",
            latch_id=answered.id, response="yes",
        )


async def test_respond_update_conflict_reraises_classification(monkeypatch):
    """手順5: UPDATE影響0は事前検査の分類で409(削除レース等・引用#4)。"""
    from latch.latches import store as store_mod

    row = _latch_row()  # proposed・期限前
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    monkeypatch.setattr(store_mod, "update_response", _ret(None))  # 影響0
    with pytest.raises(LatchClosedError):  # 期限前のためCLOSED(EXISTS検査負け)
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google", auth_subject="s",
            latch_id=row.id, response="yes",
        )


async def test_respond_yes_full_flow_partial_then_matched(monkeypatch):
    """1対1: 1人目yes→partial_accept・2人目yes→matched(イベント・matched化・クローズ)。"""
    from latch.latches import store as store_mod

    calls: list[tuple[str, dict]] = []

    async def _event(latch_id, from_status, to_status, user_id, now):
        calls.append(("event", {"from": from_status, "to": to_status, "uid": user_id}))

    async def _match(ids, now):
        calls.append(("match", {"ids": ids}))
        return list(ids)

    async def _conflicting(self_id, member_ids):
        calls.append(("conflicting", {"self": self_id}))
        return []

    async def _cancel(latch_id):
        return False

    async def _calibration(**kwargs):
        calls.append(("calibration", kwargs))
        return uuid.uuid4()

    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "insert_latch_event", _event)
    monkeypatch.setattr(store_mod, "match_intents", _match)
    monkeypatch.setattr(store_mod, "select_conflicting_latches", _conflicting)
    monkeypatch.setattr(store_mod, "cancel_latch", _cancel)
    monkeypatch.setattr(store_mod, "insert_calibration", _calibration)
    monkeypatch.setattr(
        store_mod, "fetch_pair_rows", _ret([])
    )  # 評価行なし→ログのみ
    monkeypatch.setattr(
        store_mod, "fetch_intents_structured", _ret([{}, {}])
    )

    # 1人目yes → partial_accept
    row1 = _latch_row()
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row1))
    monkeypatch.setattr(
        store_mod, "update_response", _ret((row1.id, "partial_accept"))
    )
    out = await _svc(ScriptedEngine([])).respond(
        auth_provider="google", auth_subject="s",
        latch_id=row1.id, response="yes",
    )
    assert out.status == "partial_accept"
    assert out.my_response == "yes"
    assert out.remaining_responses == 1  # 2人中1人yes
    assert calls[0] == (
        "event",
        {"from": "proposed", "to": "partial_accept", "uid": ME},
    )
    # partial_acceptではmatched化もCalibrationもしない(design §2.2手順6d)
    assert not any(c[0] == "match" for c in calls)
    assert not any(c[0] == "calibration" for c in calls)

    # 2人目yes → matched(イベント・Intent matched化・Calibration)
    calls.clear()
    row2 = _latch_row(
        responses=[{"user_id": str(ME), "response": "yes"}]
    )
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row2))
    monkeypatch.setattr(
        store_mod, "update_response", _ret((row2.id, "matched"))
    )
    out = await _svc(ScriptedEngine([])).respond(
        auth_provider="google", auth_subject="s",
        latch_id=row2.id, response="yes",
    )
    assert out.status == "matched"
    assert out.remaining_responses == 0  # matched以降は0(design §2.8)
    assert calls[0][0] == "event"  # partial_accept→matched(user_id=回答者)
    assert any(c[0] == "match" for c in calls)
    assert any(c[0] == "calibration" for c in calls)  # matched=Trueで呼ばれる
    assert any(c[0] == "conflicting" for c in calls)


async def test_respond_no_creates_rejected_calibration(monkeypatch):
    """no→rejectedでCalibration作成(matched=False・design §2.5案A)。"""
    from latch.latches import store as store_mod

    cal_calls: list[dict] = []

    async def _calibration(**kwargs):
        cal_calls.append(kwargs)
        return uuid.uuid4()

    row = _latch_row()
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    monkeypatch.setattr(
        store_mod, "update_response", _ret((row.id, "rejected"))
    )
    monkeypatch.setattr(store_mod, "insert_calibration", _calibration)
    monkeypatch.setattr(store_mod, "fetch_pair_rows", _ret([]))
    monkeypatch.setattr(store_mod, "fetch_intents_structured", _ret([{}, {}]))
    out = await _svc(ScriptedEngine([])).respond(
        auth_provider="google", auth_subject="s",
        latch_id=row.id, response="no",
    )
    assert out.status == "rejected"
    assert len(cal_calls) == 1
    assert cal_calls[0]["matched"] is False
    assert cal_calls[0]["responses"] == [
        {"user_id": str(ME), "intent_id": str(I1), "response": "no",
         "answered_at": NOW.isoformat()}
    ]


async def test_respond_matched_intent_shortfall_503(monkeypatch):
    """matched化の戻り行数不一致はtx失敗(503・design §2.3の防御)。"""
    from latch.latches import store as store_mod

    row = _latch_row(
        responses=[{"user_id": str(ME), "response": "yes"}]
    )
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    monkeypatch.setattr(
        store_mod, "update_response", _ret((row.id, "matched"))
    )
    monkeypatch.setattr(store_mod, "insert_latch_event", _ret(None))
    monkeypatch.setattr(
        store_mod, "match_intents", _ret([])
    )  # 0行=全員不一致
    with pytest.raises(DependencyUnavailableError):
        await _svc(ScriptedEngine([])).respond(
            auth_provider="google", auth_subject="s",
            latch_id=row.id, response="yes",
        )


async def test_respond_calibration_missing_row_logs_and_succeeds(monkeypatch):
    """評価行が特定できない場合はレコードを作らず回答自体は成功(design §2.5)。"""
    from latch.latches import service as svc_mod
    from latch.latches import store as store_mod

    row = _latch_row()
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    monkeypatch.setattr(
        store_mod, "update_response", _ret((row.id, "rejected"))
    )
    monkeypatch.setattr(store_mod, "insert_latch_event", _ret(None))
    monkeypatch.setattr(store_mod, "insert_calibration", _ret(uuid.uuid4()))
    monkeypatch.setattr(
        store_mod, "fetch_pair_rows", _ret([])
    )  # 評価行ゼロ→全段失敗
    out = await _svc(ScriptedEngine([])).respond(
        auth_provider="google", auth_subject="s",
        latch_id=row.id, response="no",
    )
    assert out.status == "rejected"  # 回答は成功
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_latches_service.py -v`
Expected: FAIL with "ImportError: cannot import name 'LatchesService'"

- [ ] **Step 3: 実装**

`backend/src/latch/latches/service.py` を新規作成(本Taskでrespond系・Task 7でlist/get系を追記):

```python
"""latchesユースケース(M3 ws-1 design §2.2・§2.4・§2.8・§2.9)。

回答(respond)はdesign §2.2の手順0〜6を単一トランザクションで実行する。
:nowはFOR UPDATE取得後にClock.now()で採取し検査とUPDATEで同一値を使う
(latch_engine.try_promoteと同一規律)。予期しない例外はintentsと同じ
ラップ方針(例外クラス名のみログへ残して503)。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.latches import calibration, store
from latch.latches.errors import (
    AlreadyAnsweredError,
    DependencyUnavailableError,
    ForbiddenError,
    LatchesError,
    LatchClosedError,
    LatchExpiredError,
    LatchNotFoundError,
)
from latch.latches.schemas import LatchSummaryOut

logger = logging.getLogger("latch.latches")

RESPONSE_YES = "yes"
_RESPONSE_ACCEPTING = ("proposed", "partial_accept")
_TERMINAL_FOR_CALIBRATION = ("rejected", "matched")

logger = logging.getLogger("latch.calibration")


def _wrap_unexpected(exc: Exception) -> DependencyUnavailableError:
    """予期しない例外を503へ包む。例外のクラス名のみログへ残す(08 §2.4)。"""
    logger.warning("latches.unexpected class=%s", type(exc).__name__)
    return DependencyUnavailableError("latches dependency unavailable")


def compute_new_status(response: str, responses: list[dict], total: int) -> str:
    """手順3の純計算(design §2.4)。responsesは追記後の全量。

    yes→全員yesでmatched・未満でpartial_accept(必要人数=|S|・D-06)。
    no/defer→即rejected(グループでも部分成立なし)。
    """
    if response != RESPONSE_YES:
        return "rejected"
    yes = sum(1 for r in responses if r.get("response") == RESPONSE_YES)
    return "matched" if yes == total else "partial_accept"


class LatchesService:
    """回答・一覧・詳細のユースケース(design §2.2・§2.8)。"""

    def __init__(self, *, clock: Clock, engine: AsyncEngine, geo=None) -> None:
        self._clock = clock
        self._engine = engine
        self._geo = geo  # GeoService | None(Noneならarea_name=None)

    # -- 回答(design §2.2手順0〜6) --

    async def respond(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        latch_id: uuid.UUID,
        response: str,
    ) -> LatchSummaryOut:
        try:
            async with self._engine.begin() as conn:
                user_id = await store.fetch_user_id(
                    conn, auth_provider, auth_subject
                )
                if user_id is None:
                    raise LatchNotFoundError("user not found")
                row = await store.select_latch_for_update(conn, latch_id)
                if row is None:
                    raise LatchNotFoundError("latch not found")
                now = self._clock.now()  # FOR UPDATE取得後に採取
                my_intent = await store.select_participant_intent(
                    conn, row.intent_ids, user_id
                )
                if my_intent is None:
                    raise ForbiddenError("not a participant")
                for r in row.responses:
                    if r.get("user_id") == str(user_id):
                        raise AlreadyAnsweredError("already answered")
                if row.status not in _RESPONSE_ACCEPTING:
                    self._raise_closed_or_expired(row, now)
                item = {
                    "user_id": str(user_id),
                    "intent_id": str(my_intent),
                    "response": response,
                    "answered_at": now.isoformat(),
                }
                after = [*row.responses, item]
                new_status = compute_new_status(
                    response, after, len(row.intent_ids)
                )
                updated = await store.update_response(
                    conn,
                    latch_id=row.id,
                    item=item,
                    new_status=new_status,
                    now=now,
                )
                if updated is None:  # 手順5: 影響0→事前検査の分類で409
                    self._raise_closed_or_expired(row, now)
                await store.insert_latch_event(
                    conn,
                    row.id,
                    from_status=row.status,
                    to_status=new_status,
                    user_id=user_id,
                    now=now,
                )
                if new_status == "matched":
                    await self._on_matched(conn, row, now)
                if new_status in _TERMINAL_FOR_CALIBRATION:
                    await self._create_calibration(
                        conn,
                        row=row,
                        after=after,
                        matched=new_status == "matched",
                        now=now,
                    )
                return _summary(
                    row=row,
                    status=new_status,
                    my_response=response,
                    after=after,
                )
        except LatchesError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

    @staticmethod
    def _raise_closed_or_expired(row, now) -> None:
        """手順2c/5の409分類(期限切れが上・design §2.2)。"""
        if row.response_deadline <= now or row.expires_at <= now:
            raise LatchExpiredError("response deadline passed")
        raise LatchClosedError("latch closed")

    async def _on_matched(self, conn, row, now) -> None:
        """手順6c: 参加Intent matched化+競合クローズ(design §2.3)。"""
        matched = await store.match_intents(conn, row.intent_ids, now)
        if len(matched) != len(row.intent_ids):
            # 手順4のEXISTSでactive/pausedは担保済みのため通常は起きない防御
            raise DependencyUnavailableError("intents changed during match")
        conflicts = await store.select_conflicting_latches(
            conn, row.id, row.intent_ids
        )
        for latch_id, from_status in conflicts:
            if await store.cancel_latch(conn, latch_id):
                await store.insert_latch_event(
                    conn, latch_id, from_status, "cancelled", None, now
                )

    async def _create_calibration(
        self, conn, *, row, after: list[dict], matched: bool, now
    ) -> None:
        """Calibration作成(design §2.5・§2.11・§2.12)。

        評価行が特定できない場合はレコードを作らず構造化ログ1行のみ
        (回答成立を落とす損失のほうが大きい — design §2.5)。
        """
        if row.group_candidate_id is None:
            a_id, b_id = sorted(row.intent_ids)[:2]
            eval_rows = await store.fetch_pair_rows(conn, a_id, b_id)
            hit = calibration.pick_eval_row(eval_rows, row.score, row.created_at)
            pair_ids: tuple | None = (a_id, b_id)
            hit_jev = hit.jev_result if hit is not None else None
        else:
            ms = await store.fetch_member_scores(conn, row.group_candidate_id)
            versions = _versions_of(ms)
            pair_rows = await store.fetch_group_pairs(conn, row.intent_ids)
            versioned = calibration.select_versioned_pairs(pair_rows, versions)
            best = calibration.pick_min_pair(versioned)
            hit_jev = best.jev_result if best is not None else None
            pair_ids = (best.a, best.b) if best is not None else None
        if hit_jev is None or pair_ids is None:
            logger.warning("latch.calibration.missing latch_id=%s", row.id)
            return
        structured = await store.fetch_intents_structured(conn, list(pair_ids))
        if len(structured) != 2:
            logger.warning("latch.calibration.missing latch_id=%s", row.id)
            return
        segment = calibration.classify_segment(
            calibration.segment_texts(structured[0]),
            calibration.segment_texts(structured[1]),
        )
        prediction = calibration.build_prediction(
            hit_jev, float(row.score), segment
        )
        await store.insert_calibration(
            conn,
            latch_id=row.id,
            intent_ids=row.intent_ids,
            prediction=prediction,
            proposal=row.proposal,
            responses=after,
            matched=matched,
            now=now,
        )


def _versions_of(member_scores: dict | None) -> dict[uuid.UUID, int]:
    """member_scores.versions({str(uuid): version})→{uuid: int}(design §2.12-1)。"""
    if not member_scores:
        return {}
    raw = member_scores.get("versions")
    if not isinstance(raw, dict):
        return {}
    out: dict[uuid.UUID, int] = {}
    for key, value in raw.items():
        try:
            out[uuid.UUID(key)] = int(value)
        except (ValueError, TypeError):
            continue
    return out


def _summary(
    *, row, status: str, my_response: str | None, after: list[dict]
) -> LatchSummaryOut:
    """応答要素(responses配列は出さない・引用#22)。"""
    total = len(row.intent_ids)
    yes = sum(1 for r in after if r.get("response") == RESPONSE_YES)
    if status in ("matched", "completed"):
        remaining = 0
    else:
        remaining = total - yes
    return LatchSummaryOut(
        id=row.id,
        status=status,
        response_deadline=row.response_deadline,
        expires_at=row.expires_at,
        created_at=row.created_at,
        completed_at=None,  # completed遷移はws-2
        proposal=row.proposal,
        is_group=row.group_candidate_id is not None,
        my_response=my_response,
        remaining_responses=remaining,
    )
```

**注意**: 上記のlogger宣言が2回出ている(`logger = logging.getLogger("latch.latches")` と `logger = logging.getLogger("latch.calibration")`)のは執筆上の重複。実装では `latch.latches` の1宣言のみとする(構造化ログの識別はメッセージプレフィクス `latch.calibration.missing` で行う・design §2.5の文言どおり)。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_latches_service.py -v`
Expected: PASS(Task 2の4件+本Task追加分)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/latches/service.py backend/tests/unit/latches/test_latches_service.py
git commit -m "feat: latch response flow (0-6 steps, 409 classification, calibration)"
```

### Task 7: latches/service.py — list・get(cursor・解放情報)

**Files:**
- Modify: `backend/src/latch/latches/service.py`(追記)
- Test: `backend/tests/unit/latches/test_latches_service.py`(追記)

**Interfaces:**
- Consumes: `store.select_latches_page`・`store.select_latch`・`store.fetch_participants`・`store.fetch_intents_geo`(Task 5)
- Produces: `encode_cursor(target_time, created_at, latch_id)`・`decode_cursor(cursor)`・`LatchesService.list`・`LatchesService.get`・`make_latches_service(clock, engine)`(Task 8が消費)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/latches/test_latches_service.py` へ追記(importへ `LatchValidationError`・`encode_cursor, decode_cursor` を追加):

```python
async def test_cursor_roundtrip_and_invalid():
    """cursorは3キー(target_time|created_at|id)のbase64url・不正は422(§9-3)。"""
    from latch.latches.service import decode_cursor, encode_cursor

    tt = NOW + timedelta(days=5)
    lid = uuid.uuid4()
    token = encode_cursor(tt, NOW, lid)
    assert decode_cursor(token) == (tt, NOW, lid)
    with pytest.raises(LatchValidationError):
        decode_cursor("%%%invalid%%%")
    with pytest.raises(LatchValidationError):
        decode_cursor("aGVsbG8=")  # "hello"(区切りなし)


async def test_list_builds_items_and_next_cursor(monkeypatch):
    """一覧: limit+1件取得→next_cursor生成・summary変換(my_response・remaining)。"""
    from latch.latches import store as store_mod
    from latch.latches.service import LatchesService

    page_rows = []
    for i in range(3):  # limit=2+1件
        page_rows.append(
            SimpleNamespace(
                id=uuid.uuid4(),
                status="proposed" if i < 2 else "partial_accept",
                intent_ids=[I1, I2],
                responses=(
                    [{"user_id": str(ME), "response": "yes"}] if i == 0 else []
                ),
                response_deadline=NOW + timedelta(hours=1),
                expires_at=NOW + timedelta(days=5),
                proposal={"headcount": 2, "match_level": "medium"},
                created_at=NOW - timedelta(minutes=i),
                completed_at=None,
                group_candidate_id=None,
                target_time=NOW + timedelta(days=5, minutes=i),
            )
        )
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(
        store_mod, "select_latches_page", _ret(page_rows)
    )
    items, next_cursor = await _svc(ScriptedEngine([])).list(
        auth_provider="google", auth_subject="s", limit=2
    )
    assert len(items) == 2
    assert items[0].my_response == "yes"
    assert items[0].remaining_responses == 1
    assert items[1].my_response is None
    assert next_cursor is not None  # 3件>limit=2
    # 最終ページ(limit=2で2件のみ)はnext_cursor=None
    monkeypatch.setattr(
        store_mod, "select_latches_page", _ret(page_rows[:2])
    )
    items2, next_cursor2 = await _svc(ScriptedEngine([])).list(
        auth_provider="google", auth_subject="s", limit=2
    )
    assert len(items2) == 2 and next_cursor2 is None


async def test_list_unregistered_404(monkeypatch):
    from latch.latches import store as store_mod

    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(None))
    with pytest.raises(LatchNotFoundError):
        await _svc(ScriptedEngine([])).list(
            auth_provider="google", auth_subject="s"
        )


async def test_get_detail_release_only_after_match(monkeypatch):
    """詳細: proposedはparticipants等なし・matchedは解放情報(引用#16・#21)。"""
    from latch.latches import store as store_mod

    participant = SimpleNamespace(
        intent_id=I1,
        user_id=ME,
        display_name="テスト郎",
        profile={"bio": "よろしく"},
    )
    geo = SimpleNamespace(
        intent_id=I1,
        time_start=NOW + timedelta(days=5),
        lon=130.6,
        lat=31.6,
    )

    class _Geo:
        async def reverse_geocode(self, lon, lat):
            return "鹿児島市天文館"

    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))
    monkeypatch.setattr(store_mod, "fetch_participants", _ret([participant]))
    monkeypatch.setattr(
        store_mod, "fetch_intents_geo", _ret([geo, geo])
    )

    svc = LatchesService(clock=FakeClock(NOW), engine=ScriptedEngine([]), geo=_Geo())

    proposed_row = _latch_row()
    monkeypatch.setattr(store_mod, "select_latch", _ret(proposed_row))
    detail = await svc.get(
        auth_provider="google", auth_subject="s", latch_id=proposed_row.id
    )
    assert detail.participants is None
    assert detail.time_summary is None
    assert detail.area_name is None

    matched_row = _latch_row(status="matched")
    monkeypatch.setattr(store_mod, "select_latch", _ret(matched_row))
    detail = await svc.get(
        auth_provider="google", auth_subject="s", latch_id=matched_row.id
    )
    assert detail.participants is not None
    assert detail.participants[0].display_name == "テスト郎"
    assert detail.participants[0].profile == {"bio": "よろしく"}
    # time_summaryは対象開始時刻(max(time_start))のJST書式「YYYY-MM-DD HH:MM」
    assert detail.time_summary == (
        (NOW + timedelta(days=5)).astimezone(JST).strftime("%Y-%m-%d %H:%M")
    )
    assert detail.area_name == "鹿児島市天文館"


async def test_get_forbidden_for_non_participant(monkeypatch):
    from latch.latches import store as store_mod

    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_latch", _ret(_latch_row()))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(None))
    with pytest.raises(ForbiddenError):
        await _svc(ScriptedEngine([])).get(
            auth_provider="google", auth_subject="s", latch_id=uuid.uuid4()
        )
```

テストファイルのimport部へ `from types import SimpleNamespace` と `from latch.core.clock import JST` を追加する。

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_latches_service.py -v`
Expected: FAIL with "ImportError: cannot import name 'encode_cursor'"

- [ ] **Step 3: 実装**

`backend/src/latch/latches/service.py` へ追記(import部へ `base64`・`from latch.core.clock import Clock, JST`〔JSTを追加〕・`LatchValidationError`・`LatchDetailOut`・`ParticipantOut`・`store.PageRow` 等を追記し、クラス内へlist/getメソッドを追加):

```python
# ファイル先頭へ追記するimport(既存importへ統合)
import base64
from latch.core.clock import JST
from latch.latches.errors import LatchValidationError
from latch.latches.schemas import LatchDetailOut, ParticipantOut


def encode_cursor(
    target_time: datetime, created_at: datetime, latch_id: uuid.UUID
) -> str:
    """キーセットcursor 3キー(design §2.8): base64url("ISO|ISO|uuid")。

    ソート順(target_time ASC, created_at DESC, id ASC)をタプル比較で表現する。
    intentsのencode_cursorパターンの拡張。
    """
    raw = f"{target_time.isoformat()}|{created_at.isoformat()}|{latch_id}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> tuple[datetime, datetime, uuid.UUID]:
    """cursor復元。形式不正は422 VALIDATION_ERROR(intentsと同型)。"""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        tt, ct, lid = raw.split("|")
        return datetime.fromisoformat(tt), datetime.fromisoformat(ct), uuid.UUID(lid)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LatchValidationError("invalid cursor") from exc
```

`LatchesService` クラスへ追記:

```python
    # -- 一覧・詳細(design §2.8) --

    async def list(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[LatchSummaryOut], str | None]:
        try:
            async with self._engine.connect() as conn:
                user_id = await store.fetch_user_id(
                    conn, auth_provider, auth_subject
                )
                if user_id is None:
                    raise LatchNotFoundError("user not found")
                before = decode_cursor(cursor) if cursor else None
                rows = await store.select_latches_page(
                    conn, me=user_id, before=before, limit=limit + 1
                )
            items = [_summary_from_page(r, user_id) for r in rows[:limit]]
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit - 1]
                next_cursor = encode_cursor(
                    last.target_time, last.created_at, last.id
                )
            return items, next_cursor
        except LatchesError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

    async def get(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        latch_id: uuid.UUID,
    ) -> LatchDetailOut:
        try:
            async with self._engine.connect() as conn:
                user_id = await store.fetch_user_id(
                    conn, auth_provider, auth_subject
                )
                if user_id is None:
                    raise LatchNotFoundError("user not found")
                row = await store.select_latch(conn, latch_id)
                if row is None:
                    raise LatchNotFoundError("latch not found")
                if (
                    await store.select_participant_intent(
                        conn, row.intent_ids, user_id
                    )
                    is None
                ):
                    raise ForbiddenError("not a participant")
                summary = _summary_from_page(
                    _page_view_of(row), user_id
                )
                if row.status not in ("matched", "completed"):
                    return LatchDetailOut(**summary.model_dump())
                participants = await store.fetch_participants(
                    conn, row.intent_ids
                )
                geo_rows = await store.fetch_intents_geo(conn, row.intent_ids)
            # connを閉じた後でgeo呼び出し(GeoServiceは別接続を開く)
            detail_extra: dict = {}
            if geo_rows:
                target = max(g.time_start for g in geo_rows)
                detail_extra["time_summary"] = (
                    target.astimezone(JST).strftime("%Y-%m-%d %H:%M")
                )
                if self._geo is not None:
                    lon = sum(g.lon for g in geo_rows) / len(geo_rows)
                    lat = sum(g.lat for g in geo_rows) / len(geo_rows)
                    detail_extra["area_name"] = await self._geo.reverse_geocode(
                        lon, lat
                    )
            detail_extra["participants"] = [
                ParticipantOut(
                    user_id=p.user_id,
                    display_name=p.display_name,
                    profile=p.profile,
                )
                for p in participants
            ]
            return LatchDetailOut(**summary.model_dump(), **detail_extra)
        except LatchesError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc
```

ファイル末尾へ模块関数を追記:

```python
def _yes_count(responses: list[dict]) -> int:
    return sum(1 for r in responses if r.get("response") == RESPONSE_YES)


def _my_response(responses: list[dict], user_id: uuid.UUID) -> str | None:
    for r in responses:
        if r.get("user_id") == str(user_id):
            return r.get("response")
    return None


def _summary_from_page(row, user_id: uuid.UUID) -> LatchSummaryOut:
    """一覧・詳細行→応答要素(responses配列は出さない・引用#22)。"""
    total = len(row.intent_ids)
    if row.status in ("matched", "completed"):
        remaining = 0
    else:
        remaining = total - _yes_count(row.responses)
    return LatchSummaryOut(
        id=row.id,
        status=row.status,
        response_deadline=row.response_deadline,
        expires_at=row.expires_at,
        created_at=row.created_at,
        completed_at=row.completed_at,
        proposal=row.proposal,
        is_group=row.group_candidate_id is not None,
        my_response=_my_response(row.responses, user_id),
        remaining_responses=remaining,
    )


def _page_view_of(row):
    """LatchRow→_summary_from_page互換のビュー(completed_atを持たせる)。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        id=row.id,
        status=row.status,
        intent_ids=row.intent_ids,
        responses=row.responses,
        response_deadline=row.response_deadline,
        expires_at=row.expires_at,
        created_at=row.created_at,
        completed_at=None,
        proposal=row.proposal,
        group_candidate_id=row.group_candidate_id,
    )
```

さらに `make_latches_service` をファイル末尾へ追記:

```python
def make_latches_service(*, clock: Clock, engine: AsyncEngine) -> LatchesService:
    """main.py lifespan用の構築(GeoServiceはengineから作る)。"""
    from latch.geo.service import GeoService

    return LatchesService(clock=clock, engine=engine, geo=GeoService(engine))
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_latches_service.py -v`
Expected: PASS(全件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/latches/service.py backend/tests/unit/latches/test_latches_service.py
git commit -m "feat: latch list/detail endpoints service (cursor, release info)"
```

### Task 8: routes.py + main.py登録 + __init__.py完成形

**Files:**
- Create: `backend/src/latch/latches/routes.py`
- Modify: `backend/src/latch/latches/__init__.py`(完成形へ)
- Modify: `backend/src/latch/main.py`(router登録・ハンドラ・lifespan)
- Test: `backend/tests/unit/latches/test_latches_routes.py`(新規作成 — **basename一意に注意**: 既存に `test_routes.py`(unit/intents)・`test_crud_routes.py` 等があるが `test_latches_routes.py` は衝突なし)

**Interfaces:**
- Consumes: `LatchesService`・`make_latches_service`(Task 7)・`require_authenticated`・`api_rate_limited`・schemas(Task 2)
- Produces: `latches_router`(main.pyが登録)・`get_latches_service`(テストがdependency_overridesで差し替え)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/latches/test_latches_routes.py` を新規作成(test_crud_routes.py流儀・スタブサービス+ASGITransport):

```python
"""latchesルーティングのunit試験(M3 ws-1)。スタブサービス注入で応答形状を検証。"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.latches.errors import LatchClosedError
from latch.main import create_app

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
LATCH_ID = uuid.uuid4()


def _claims() -> AccessTokenClaims:
    return AccessTokenClaims(
        auth_provider="google",
        auth_subject="sub-1",
        jti="jti-1",
        sid="sid-1",
        iat=NOW,
        exp=NOW + timedelta(hours=1),
    )


def _summary(**overrides) -> SimpleNamespace:
    base = dict(
        id=LATCH_ID,
        status="proposed",
        response_deadline=NOW + timedelta(hours=1),
        expires_at=NOW + timedelta(days=5),
        created_at=NOW,
        completed_at=None,
        proposal={"headcount": 2, "match_level": "medium"},
        is_group=False,
        my_response=None,
        remaining_responses=2,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class StubService:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def respond(self, **kwargs):
        self.calls.append(("respond", kwargs))
        return _summary(status="partial_accept", my_response="yes",
                        remaining_responses=1)

    async def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return ([_summary()], "next-token")

    async def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return SimpleNamespace(
            **vars(_summary(status="matched")),
            participants=None,
            time_summary=None,
            area_name=None,
        )


def _app(svc: StubService):
    app = create_app(latches_service=svc)
    app.dependency_overrides[require_authenticated] = lambda: _claims()
    return app


@pytest.fixture
def stub():
    return StubService()


async def test_response_endpoint_returns_latch_envelope(stub):
    """POST /v1/latches/{id}/response は {"latch": {...}}(05 §5)。"""
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/v1/latches/{LATCH_ID}/response", json={"response": "yes"}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"latch"}
    latch = body["latch"]
    assert latch["status"] == "partial_accept"
    assert latch["my_response"] == "yes"
    assert latch["remaining_responses"] == 1
    assert "responses" not in latch  # 引用#22
    assert stub.calls[0][1]["response"] == "yes"


async def test_response_invalid_value_422(stub):
    """response="maybe" は422 VALIDATION_ERROR(引用#1・試験8の本体)。"""
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/v1/latches/{LATCH_ID}/response", json={"response": "maybe"}
        )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_domain_error_maps_to_envelope(stub):
    """LatchesErrorは {"error":{"code",...}} へ(引用#18)。"""
    async def _raise(**kwargs):
        raise LatchClosedError("latch closed")

    stub.respond = _raise
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/v1/latches/{LATCH_ID}/response", json={"response": "yes"}
        )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "LATCH_CLOSED"


async def test_list_forwards_cursor_and_limit(stub):
    """GET /v1/latches はcursor・limit(既定20・上限100)をserviceへ流す。"""
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/v1/latches", params={"cursor": "abc", "limit": 5}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["next_cursor"] == "next-token"
    assert len(body["items"]) == 1
    assert stub.calls[0][1]["cursor"] == "abc"
    assert stub.calls[0][1]["limit"] == 5


async def test_limit_out_of_range_422(stub):
    async with AsyncClient(
        transport=ASGITransport(app=_app(stub)), base_url="http://test"
    ) as client:
        resp = await client.get("/v1/latches", params={"limit": 101})
    assert resp.status_code == 422


async def test_unauthenticated_401(stub):
    """認証なしは401(認証ハンドラ・スタブは呼ばれない)。"""
    app = create_app(latches_service=stub)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/v1/latches")
    assert resp.status_code == 401
    assert stub.calls == []
```

**注意**: `create_app(latches_service=...)` への注入はlatches_service注入時lifespanのbuild_latches判定をスキップさせる(Step 3のmain.py変更)。auth/users等の未注入サービスはlifespanで構築されるが、ASGITransportのunit試験ではlifespanが走らない(httpx ASGITransportはlifespanを実行しない)ためDB接続は発生しない(test_crud_routes.pyと同一前提)。

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_latches_routes.py -v`
Expected: FAIL with "TypeError: create_app() got an unexpected keyword argument 'latches_service'"

- [ ] **Step 3: 実装**

`backend/src/latch/latches/routes.py` を新規作成:

```python
"""latchesルータ(M3 ws-1 design §2.8・05 §5)。

claimsはプリミティブ(provider/subject)としてサービスへ渡す(intentsと
同型)。ログはstatus/codeのみ — Proposal本文・回答内容は出さない(08 §2.4)。
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.latches.schemas import (
    LatchDetailEnvelope,
    LatchEnvelope,
    LatchListResponse,
    ResponseRequest,
)
from latch.latches.service import LatchesService
from latch.ratelimit.deps import api_rate_limited

logger = logging.getLogger("latch.latches")

latches_router = APIRouter(
    prefix="/v1/latches",
    tags=["latches"],
    dependencies=[Depends(api_rate_limited)],  # 401→429(M1 ws-4と同型)
)


def get_latches_service(request: Request) -> LatchesService:
    """app.state.latches_service へのアクセス(lifespanまたはテスト注入で載る)。"""
    return request.app.state.latches_service


@latches_router.get("", response_model=LatchListResponse)
async def list_latches(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[LatchesService, Depends(get_latches_service)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> LatchListResponse:
    """GET /v1/latches(05 §5。本人関与かつcandidate除外・cursor改頁)。"""
    items, next_cursor = await svc.list(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        cursor=cursor,
        limit=limit,
    )
    return LatchListResponse(items=items, next_cursor=next_cursor)


@latches_router.get("/{latch_id}", response_model=LatchDetailEnvelope)
async def get_latch(
    latch_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[LatchesService, Depends(get_latches_service)],
) -> LatchDetailEnvelope:
    """GET /v1/latches/{id}(05 §5。成立後は解放情報を含む)。"""
    detail = await svc.get(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        latch_id=latch_id,
    )
    logger.info("latches.get ok status=%s", detail.status)
    return LatchDetailEnvelope(latch=detail)


@latches_router.post("/{latch_id}/response", response_model=LatchEnvelope)
async def respond_to_latch(
    latch_id: uuid.UUID,
    body: ResponseRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[LatchesService, Depends(get_latches_service)],
) -> LatchEnvelope:
    """POST /v1/latches/{id}/response(05 §5・06 §6の直列化)。"""
    summary = await svc.respond(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        latch_id=latch_id,
        response=body.response,
    )
    logger.info("latches.response ok status=%s", summary.status)
    return LatchEnvelope(latch=summary)
```

`backend/src/latch/latches/__init__.py` を完成形へ書き換え:

```python
"""LATCHドメイン(M3 ws-1)。回答API・一覧・詳細・Calibration記録。"""

from latch.latches.errors import (
    AlreadyAnsweredError,
    DependencyUnavailableError,
    ForbiddenError,
    LatchesError,
    LatchClosedError,
    LatchExpiredError,
    LatchNotFoundError,
    LatchValidationError,
)
from latch.latches.routes import latches_router
from latch.latches.service import LatchesService, make_latches_service

__all__ = [
    "AlreadyAnsweredError",
    "DependencyUnavailableError",
    "ForbiddenError",
    "LatchesError",
    "LatchClosedError",
    "LatchExpiredError",
    "LatchNotFoundError",
    "LatchValidationError",
    "LatchesService",
    "latches_router",
    "make_latches_service",
]
```

`backend/src/latch/main.py` へ変更(既存コードへの差分):

1. import部へ追記:
```python
from latch.latches import LatchesError, latches_router, make_latches_service
```

2. `_lifespan` の冒頭判定ブロックへ追加:
```python
    build_latches = not hasattr(app.state, "latches_service")
```
早期returnの条件へ `or build_latches` を追記。`if build_intents_crud:` のelif連鎖の後(`elif` ではない独立ifでもよい — engine取得部の後に置く)へ次を追記:
```python
    if build_latches:
        app.state.latches_service = make_latches_service(
            clock=app.state.clock, engine=engine
        )
```
配置位置: `elif build_intents_crud:` ブロックの後(try: yield の前)。engineは既存の共有取得部(`engine = getattr(app.state, "db_engine", None)`)の後であればどの位置でもよいが、構築順の読みやすさからusers/intents系の直後に置く。

3. `create_app` のシグネチャへ引数を追加(既存引数の末尾):
```python
    latches_service=None,
```
state設定部へ追記:
```python
    if latches_service is not None:
        app.state.latches_service = latches_service
```

4. `app.include_router(intents_crud_router)` の次へ追記:
```python
    app.include_router(latches_router)
```

5. 例外ハンドラ(intents_error_handlerの後)へ追記:
```python
    @app.exception_handler(LatchesError)
    async def latches_error_handler(
        request: Request, exc: LatchesError
    ) -> JSONResponse:
        latches_logger.warning("latches.error code=%s", exc.code)
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(exc.code, str(exc)),
        )
```
モジュール先頭のlogger宣言部へ `latches_logger = logging.getLogger("latch.latches")` を追記。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/latches/ -v`
Expected: PASS(routes 6件+service/calibration/store_sql 全件)。併せて `uv run pytest tests/unit/test_app_health.py -v`(既存main.py試験が無傷)もPASS

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/latches/ backend/src/latch/main.py backend/tests/unit/latches/test_latches_routes.py
git commit -m "feat: wire latches router into app (3 endpoints, error handler)"
```

### Task 9: group_engine昇格UPDATEへgroup_candidate_id列追加(§2.10)

**Files:**
- Modify: `backend/src/latch/worker/matching/group_engine.py:209-215`(_UPDATE_GROUP_LATCH_FOR_PROMOTION)・`505-523`(_update_group_latch_for_promotion)・`920-927`(呼び出し側)
- Test: `backend/tests/unit/matching/test_group_engine.py`(追記)

**Interfaces:**
- Produces: `_UPDATE_GROUP_LATCH_FOR_PROMOTION` のSET句へ `group_candidate_id = CAST(:gid AS uuid)`。`_update_group_latch_for_promotion(conn, latch_id, *, score, proposal, deadline, gid)`(Task 11の世代交代試験がこの経路を検証)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_group_engine.py` の末尾へ追記:

```python
def test_promotion_update_writes_group_candidate_id():
    """昇格UPDATEはgroup_candidate_idも書く(§2.10・ws-7引継ぎの確定)。

    ON CONFLICTで既存の開いているlatches行へ昇格するとき、旧gidのまま
    残っていた問題への対処。SET句とbind paramのピン。
    """
    raw = str(group_engine._UPDATE_GROUP_LATCH_FOR_PROMOTION)
    assert "group_candidate_id = CAST(:gid AS uuid)" in raw
    assert "score = :score" in raw
    assert "response_deadline = CAST(:deadline AS timestamptz)" in raw
    assert "WHERE id = CAST(:latch_id AS uuid) AND status = 'candidate'" in raw
```

(ファイル冒頭のimportに `group_engine` モジュール参照が既にあることを確認。`from latch.worker.matching import group_engine` の形でなければ追記する)

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_group_engine.py::test_promotion_update_writes_group_candidate_id -v`
Expected: FAIL with "assert 'group_candidate_id = CAST(:gid AS uuid)' in ..."

- [ ] **Step 3: 実装**

`backend/src/latch/worker/matching/group_engine.py` の3箇所を変更:

1. `_UPDATE_GROUP_LATCH_FOR_PROMOTION`(209〜215行)を置き換え:

```python
# 昇格時のcandidate行更新(latch_engine._UPDATE_FOR_PROMOTIONと同型。
# group_candidate_idも書く: 同一intent_idsの新世代集合(gid')が既存の
# 開いているlatches行へ昇格するとき旧gidが残るのを防ぐ — ws-7引継ぎの
# 確定・M3 ws-1 design §2.10)
_UPDATE_GROUP_LATCH_FOR_PROMOTION = text("""
    UPDATE latches
    SET score = :score, proposal = CAST(:proposal AS jsonb),
        response_deadline = CAST(:deadline AS timestamptz),
        group_candidate_id = CAST(:gid AS uuid)
    WHERE id = CAST(:latch_id AS uuid) AND status = 'candidate'
    RETURNING id
""")
```

2. `_update_group_latch_for_promotion`(505〜523行)のシグネチャとparamsへgidを追加:

```python
async def _update_group_latch_for_promotion(
    conn: AsyncConnection,
    latch_id: uuid.UUID,
    *,
    score: float,
    proposal: dict,
    deadline,
    gid: uuid.UUID,
) -> bool:
    """昇格時のcandidate行更新(score/proposal/response_deadline/group_candidate_id)。"""
    res = await conn.execute(
        _UPDATE_GROUP_LATCH_FOR_PROMOTION,
        {
            "latch_id": latch_id,
            "score": score,
            "proposal": json.dumps(proposal, ensure_ascii=False),
            "deadline": deadline,
            "gid": gid,
        },
    )
    return res.first() is not None
```

3. 呼び出し側(finalize内・920行付近)へ `gid=r.gid` を追加:

```python
                    if not await _update_group_latch_for_promotion(
                        conn,
                        found[0],
                        score=r.score,
                        proposal=proposal,
                        deadline=deadline0,
                        gid=r.gid,
                    ):
                        continue
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_group_engine.py -v && uv run pytest tests/unit -q`
Expected: PASS(既存試験は昇格時のgid期待値を持たないため無傷。`test_matching_groupengine.py`(integration)の480行 `str(latch[4]) == str(gc2[0])` は同一gidでの昇格のため無傷 — Task 11のtest-ciで確認)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/worker/matching/group_engine.py backend/tests/unit/matching/test_group_engine.py
git commit -m "fix: promotion update writes group_candidate_id (ws-7 handover)"
```

### Task 10: stage1削除Event処理へlatchesクローズ+解散復帰を追加(§2.7)

**Files:**
- Modify: `backend/src/latch/worker/stage1.py`(SQL 4本+関数+呼び出し)
- Test: `backend/tests/unit/test_worker_stage1.py`(既存2件の追従+新規2件)

**Interfaces:**
- Produces: `stage1.close_latches_on_delete(conn, intent_id, now)`(Task 11のintegration試験17がengine.begin()内で直接呼ぶ)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_worker_stage1.py` へ追記。まず**既存2件の追従**(FakeResultの結果列にlatches系SELECT 2件分を追加 — 呼び出し順序は「ロック→version→CLOSE_CANDIDATES→CLOSE_GROUPS→latches(開いている)SELECT→latches(matched)SELECT→processed」。新規SQLは結果0行を返す):

`test_process_deleted_closes_candidates` を次へ書き換え:

```python
async def test_process_deleted_closes_candidates():
    """deleted → match_candidatesの無効化SQL(status='closed'・06 §1)。"""
    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 0),  # 候補UPDATE(0件でも正常 — design §2.5)
            FakeResult(None, 0),  # group_candidatesUPDATE(ws-7・0件でも正常)
            FakeResult((), 0),  # latches(開いている)SELECT(M3 ws-1・0行)
            FakeResult((), 0),  # latches(matched)SELECT(M3 ws-1・0行)
            FakeResult(None, 1),  # processed
        ]
    )
    await _stage1(engine).process(
        _event("deleted", IID, 1), ("deleted", IID, 1), ROW_ID
    )
    close_sql = _sql(engine.conn, 2)
    assert "match_candidates" in close_sql and "closed" in close_sql
    assert engine.conn.calls[2][1]["intent_id"] == IID
    # group_candidatesの無効化はmatch_candidatesの直後(§9-10)
    gc_sql = _sql(engine.conn, 3)
    assert "group_candidates" in gc_sql and "closed" in gc_sql
    assert "ANY(intent_ids)" in gc_sql
    assert engine.conn.calls[3][1]["intent_id"] == IID
```

`test_process_deleted_closes_group_candidates` へ同じ2件のFakeResult追加(`FakeResult((), 0)` を2つ・processedの前へ挿入。アサーションはindex不変のためそのまま):

```python
    engine = ScriptedEngine(
        [
            FakeResult(("pending",)),
            FakeResult((1,)),
            FakeResult(None, 0),  # match_candidates UPDATE
            FakeResult(None, 0),  # group_candidates UPDATE(本試験の主対象)
            FakeResult((), 0),  # latches(開いている)SELECT(M3 ws-1)
            FakeResult((), 0),  # latches(matched)SELECT(M3 ws-1)
            FakeResult(None, 1),  # processed
        ]
    )
```

次に**新規2件**をファイル末尾へ追記:

```python
# -- M3 ws-1: 削除Eventのlatchesクローズ+matched解散・復帰(design §2.7) --

LID = uuid.uuid4()
LID2 = uuid.uuid4()
REST_ID = uuid.uuid4()
MATCHED_IDS = [IID, REST_ID]


async def test_close_latches_on_delete_cancels_open_latches():
    """開いている3状態(candidate/proposed/partial_accept)→cancelled+events。"""
    conn = ScriptedConn(
        [
            FakeResult(((LID, "proposed"),), 1),  # 開いているSELECT FOR UPDATE
            FakeResult((LID,), 1),  # cancel UPDATE
            FakeResult(None, 1),  # event INSERT
            FakeResult((), 0),  # matched SELECT(0行)
        ]
    )
    await stage1_mod.close_latches_on_delete(conn, IID, S1_NOW)
    select_sql = str(conn.calls[0][0])
    assert "status IN ('candidate', 'proposed', 'partial_accept')" in select_sql
    assert "CAST(:intent_id AS uuid) = ANY(intent_ids)" in select_sql
    assert "FOR UPDATE" in select_sql
    cancel_sql = str(conn.calls[1][0])
    assert "SET status = 'cancelled'" in cancel_sql
    event_params = conn.calls[2][1]
    assert event_params["from_status"] == "proposed"
    assert event_params["to_status"] == "cancelled"
    assert event_params["user_id"] is None  # システム起因=引用#12


async def test_close_latches_on_delete_dissolves_matched_and_restores():
    """matched行→cancelled+events+残Intent復帰(expires_atでexpired/active分岐)。"""
    conn = ScriptedConn(
        [
            FakeResult((), 0),  # 開いているSELECT(0行)
            FakeResult(((LID2, MATCHED_IDS),), 1),  # matched SELECT FOR UPDATE
            FakeResult((LID2,), 1),  # cancel UPDATE
            FakeResult(None, 1),  # event INSERT
            FakeResult(((REST_ID, "active"),), 1),  # 復帰UPDATE
        ]
    )
    await stage1_mod.close_latches_on_delete(conn, IID, S1_NOW)
    restore_sql = str(conn.calls[4][0])
    assert "CASE WHEN expires_at <= CAST(:now AS timestamptz)" in restore_sql
    assert "THEN 'expired'" in restore_sql
    assert "ELSE 'active'" in restore_sql
    assert "status = 'matched'" in restore_sql
    # 復帰対象は削除Intentを除く残る参加Intentのみ(引用#9・#20)
    assert conn.calls[4][1]["ids"] == [REST_ID]
    # matched解散イベント(from=matched・user_id=None)
    assert conn.calls[3][1]["from_status"] == "matched"
    assert conn.calls[3][1]["to_status"] == "cancelled"
```

import部へ追記(ファイル冒頭の既存importへ):

```python
from latch.worker import stage1 as stage1_mod
```

`S1_NOW`・`ScriptedConn`・`FakeResult` はファイル内の既存定義をそのまま使う。

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_worker_stage1.py -v`
Expected: FAIL(新規2件は "AttributeError: module 'latch.worker.stage1' has no attribute 'close_latches_on_delete'")

- [ ] **Step 3: 実装**

`backend/src/latch/worker/stage1.py` へ変更:

1. SQL定数部(`_CLOSE_GROUPS` の後)へ追記:

```python
# M3 ws-1 design §2.7: 削除Intentを含むlatchesのクローズ・matched解散。
# 開いている行を先にSELECT FOR UPDATE(from_status確定)→条件付きUPDATE。
_SELECT_OPEN_LATCHES_ON_DELETE = text("""
    SELECT id, status FROM latches
    WHERE CAST(:intent_id AS uuid) = ANY(intent_ids)
      AND status IN ('candidate', 'proposed', 'partial_accept')
    ORDER BY id
    FOR UPDATE
""")
_CANCEL_LATCH_ON_DELETE = text("""
    UPDATE latches SET status = 'cancelled'
    WHERE id = CAST(:latch_id AS uuid)
      AND status IN ('candidate', 'proposed', 'partial_accept')
    RETURNING id
""")
_SELECT_MATCHED_LATCHES_ON_DELETE = text("""
    SELECT id, intent_ids FROM latches
    WHERE CAST(:intent_id AS uuid) = ANY(intent_ids)
      AND status = 'matched'
    ORDER BY id
    FOR UPDATE
""")
_CANCEL_MATCHED_LATCH_ON_DELETE = text("""
    UPDATE latches SET status = 'cancelled'
    WHERE id = CAST(:latch_id AS uuid) AND status = 'matched'
    RETURNING id
""")
# 解散時の残る参加Intent復帰(引用#9・#20: expires_at経過→expired・それ以外→active)
_RESTORE_INTENTS_ON_DISSOLVE = text("""
    UPDATE intents
    SET status = CASE WHEN expires_at <= CAST(:now AS timestamptz)
                      THEN 'expired' ELSE 'active' END,
        updated_at = CAST(:now AS timestamptz)
    WHERE id = ANY(CAST(:ids AS uuid[])) AND status = 'matched'
    RETURNING id, status
""")
```

2. モジュール関数(`_quarantine_direct` の後・ファイル末尾)へ追記:

```python
async def close_latches_on_delete(conn: AsyncConnection, intent_id: uuid.UUID, now) -> None:
    """削除Event処理のlatches波及(M3 ws-1 design §2.7・引用#19・#20)。

    1) 開いているlatches(candidate/proposed/partial_accept)→cancelled+events
       (保留=latches.status=candidateの無効化・06 §1の未実装追随)
    2) matched行→cancelled(解散・05 §6遷移表)+events+残る参加Intentの復帰
       (削除されたIntent自身はintents側でcancelled遷移済みのため対象外)。
    削除Intentを含むlatchesへ回答が走るレースは回答UPDATEのNOT EXISTS検査
    (design §2.2手順4)で封じている。イベントのuser_idはNULL(システム起因)。
    """
    open_rows = (
        await conn.execute(
            _SELECT_OPEN_LATCHES_ON_DELETE, {"intent_id": intent_id}
        )
    ).fetchall()
    for latch_id, from_status in open_rows:
        res = await conn.execute(
            _CANCEL_LATCH_ON_DELETE, {"latch_id": _coerce_uuid(latch_id)}
        )
        if res.first() is None:
            continue  # 同一tx内で他経路が閉じた(通常ない防御)
        await conn.execute(
            _INSERT_LATCH_EVENT_SQL,
            {
                "latch_id": latch_id,
                "from_status": from_status,
                "to_status": "cancelled",
                "user_id": None,
                "now": now,
            },
        )
    matched_rows = (
        await conn.execute(
            _SELECT_MATCHED_LATCHES_ON_DELETE, {"intent_id": intent_id}
        )
    ).fetchall()
    for latch_id, intent_ids in matched_rows:
        res = await conn.execute(
            _CANCEL_MATCHED_LATCH_ON_DELETE, {"latch_id": _coerce_uuid(latch_id)}
        )
        if res.first() is None:
            continue
        await conn.execute(
            _INSERT_LATCH_EVENT_SQL,
            {
                "latch_id": latch_id,
                "from_status": "matched",
                "to_status": "cancelled",
                "user_id": None,
                "now": now,
            },
        )
        rest = [i for i in (_coerce_uuid(x) for x in intent_ids) if i != intent_id]
        if rest:
            await conn.execute(
                _RESTORE_INTENTS_ON_DISSOLVE, {"ids": rest, "now": now}
            )
```

**注意**: stage1.pyにはlatch_status_events INSERTのSQLがまだないため、`_INSERT_LATCH_EVENT_SQL` を同ファイルへ追加する(latch_engineと同一SQL・stage1はworker/matchingをimportしない方釠のため自前定数):

```python
_INSERT_LATCH_EVENT_SQL = text("""
    INSERT INTO latch_status_events
        (latch_id, from_status, to_status, user_id, created_at)
    VALUES (CAST(:latch_id AS uuid), CAST(:from_status AS text),
            :to_status, CAST(:user_id AS uuid), CAST(:now AS timestamptz))
""")
```

3. `_process_once` のEVENT_DELETED分岐(286〜290行)へ呼び出しを追加:

```python
            if event_type == EVENT_DELETED:
                await conn.execute(
                    _CLOSE_CANDIDATES, {"intent_id": intent_id, "now": now}
                )
                await conn.execute(_CLOSE_GROUPS, {"intent_id": intent_id, "now": now})
                await close_latches_on_delete(conn, intent_id, now)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/test_worker_stage1.py -v && uv run pytest tests/unit -q`
Expected: PASS(既存追従2件+新規2件・他のstage1試験はEVENT_DELETEDを通らないため無傷)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/worker/stage1.py backend/tests/unit/test_worker_stage1.py
git commit -m "feat: stage1 closes latches on intent deletion (dissolve + restore)"
```

### Task 11: integration試験(design §4.2の17試験+groupengine世代交代1試験)

**Files:**
- Create: `backend/tests/integration/test_latches_api.py`
- Modify: `backend/tests/integration/test_matching_groupengine.py`(世代交代1試験を追記)

**Interfaces:**
- Consumes: API3エンドポイント(Task 8)・`stage1.close_latches_on_delete`(Task 10)・`GroupEngine.finalize`(既存)・latches/match_candidates/group_candidatesの直接INSERT fixture

対抗策(design §4.2冒頭+M2 ws-8流儀): (1)subjectプレフィックス `m3ws1-`(teardownはFK順で完全削除) (2)時間窓はnow+5日系(BASE_HOURS=120)で分離 (3)期限切れ試験はcompose常設apiのSystemClockに対しDB値(response_deadline/expires_at)を過去へUPDATE(§9-2・FakeClock進めと等価) (4)jev_resultはmatch_candidatesへ直接INSERT (5)Redisは `m3ws1-` prefix掃除(本試験はRedisを消費しないが規律として同名prefix・api_rate_limitedの60req/分はユーザー分離で回避)。

- [ ] **Step 1: 試験ファイルを作成(実行はStep 3・収集は毎コミット `make test` で検証される)**

`backend/tests/integration/test_latches_api.py` を新規作成:

```python
"""LATCH応答系コアのintegration試験(M3 ws-1 design §4.2の17試験)。

実DB(compose常設)・実Redis・api常設(実HTTP)。latches行はfixtureで直接
INSERT(回答APIはlatches行しか読まないためパイプラインを走らせない)。
jev_resultはmatch_candidatesへ直接INSERT(Calibration試験用・ws流儀)。
期限切れ試験はDB値を過去へUPDATE(§9-2・apiプロセスのClockは差し替え
不能のため等価置換)。stage1拡張(試験17)はclose_latches_on_deleteを
直接呼ぶ(§9-6)。
"""

import asyncio
import json
import sys
import uuid as uuid_mod
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock

pytestmark = pytest.mark.integration

CATEGORY = "meal"
BASE_HOURS = 120
SUBJECT_PREFIX = "m3ws1-"


@pytest.fixture
async def field(db_engine):
    """試験ごとに一意のsubjectプレフィックス。teardownでFK順に全削除。"""
    prefix = f"{SUBJECT_PREFIX}{uuid_mod.uuid4().hex[:8]}-"
    yield prefix
    p = {"p": prefix + "%"}
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM latch_status_events WHERE latch_id IN"
                " (SELECT id FROM latches WHERE intent_ids && ARRAY("
                "  SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p)))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM calibration_records WHERE latch_id IN"
                " (SELECT id FROM latches WHERE intent_ids && ARRAY("
                "  SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p)))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM notifications WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM latches WHERE intent_ids && ARRAY("
                "  SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM match_candidates WHERE intent_a_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
                " OR intent_b_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM group_candidates WHERE intent_ids && ARRAY("
                "  SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM match_events WHERE source_intent_id IN"
                " (SELECT id FROM intents WHERE user_id IN"
                "  (SELECT id FROM users WHERE auth_subject LIKE :p))"
            ),
            p,
        )
        await conn.execute(
            text(
                "DELETE FROM intents WHERE user_id IN"
                " (SELECT id FROM users WHERE auth_subject LIKE :p)"
            ),
            p,
        )
        await conn.execute(text("DELETE FROM users WHERE auth_subject LIKE :p"), p)


async def _cli_idp_token(provider: str, subject: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "latch.auth", "issue-idp-token",
        "--provider", provider, "--subject", subject,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    return stdout.decode().strip()


async def _user(api_client, prefix: str):
    subject = f"{prefix}{uuid_mod.uuid4().hex[:8]}"
    idp = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users", headers=headers,
        json={"display_name": "m3ws1", "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    return (SystemClock().now() + timedelta(hours=hours)).isoformat()


def _structured(*, secondary: str | None = None, start: str | None = None) -> dict:
    d: dict = {
        "category": {"primary": CATEGORY, "secondary": secondary},
        "alcohol_involved": False,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }
    return d


async def _intent(api_client, headers, *, secondary: str | None = None,
                  start: str | None = None) -> dict:
    resp = await api_client.post(
        "/v1/intents", headers=headers,
        json={
            "raw_text": "分類用テキスト",
            "status": "active",
            "structured_intent": _structured(secondary=secondary, start=start),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["intent"]


async def _latch(db_engine, intent_ids: list[str], *, status: str = "proposed",
                 score: float = 0.85, gid: str | None = None,
                 deadline_hours: float = 2.0, expires_hours: float = 120.0,
                 proposal: dict | None = None) -> str:
    """latches行を直接INSERT(fixture・回答APIはlatches行のみ読む)。"""
    now = SystemClock().now()
    if proposal is None:
        proposal = {"headcount": len(intent_ids), "match_level": "medium"}
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text("""
                INSERT INTO latches
                    (intent_ids, group_candidate_id, proposal, score, status,
                     response_deadline, expires_at, created_at)
                VALUES (CAST(:ids AS uuid[]), CAST(:gid AS uuid),
                        CAST(:proposal AS jsonb), :score, :status,
                        CAST(:deadline AS timestamptz),
                        CAST(:expires AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": [uuid_mod.UUID(i) for i in intent_ids],
                "gid": uuid_mod.UUID(gid) if gid else None,
                "proposal": json.dumps(proposal, ensure_ascii=False),
                "score": score,
                "status": status,
                "deadline": now + timedelta(hours=deadline_hours),
                "expires": now + timedelta(hours=expires_hours),
                "now": now,
            },
        )
    return str(res.first()[0])


async def _pair_eval(db_engine, a_id: str, b_id: str, *, wa: float, wb: float,
                     latch_score: float | None = None) -> None:
    """match_candidatesへjev_result付きevaluatedを直接INSERT(試験13/14用)。"""
    jev = {
        "would_a_accept_b": wa,
        "would_b_accept_a": wb,
        "jev_5axis": {
            "purpose_fit": {"value": 0.5, "confidence": 0.9},
            "mood_fit": {"value": 0.5, "confidence": 0.8},
            "timing_fit": {"value": 0.5, "confidence": 0.7},
            "social_fit": {"value": 0.5, "confidence": 0.6},
            "latent_yes": {"value": 0.5, "confidence": None},
        },
        "provider": "typesafe_jev",
        "model": "jev-1.13.0",
    }
    now = SystemClock().now()
    async with db_engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO match_candidates
                    (intent_a_id, intent_b_id, intent_a_version,
                     intent_b_version, retrieval_score, cheap_judge_score,
                     jev_result, latch_score, status, created_at, updated_at)
                VALUES (CAST(:a AS uuid), CAST(:b AS uuid), 1, 1,
                        0.9, 0.9, CAST(:jev AS jsonb), :ls, 'evaluated',
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
            """),
            {
                "a": uuid_mod.UUID(a_id), "b": uuid_mod.UUID(b_id),
                "jev": json.dumps(jev, ensure_ascii=False),
                "ls": latch_score, "now": now,
            },
        )


async def _respond(api_client, headers, latch_id: str, response: str):
    return await api_client.post(
        f"/v1/latches/{latch_id}/response",
        headers=headers, json={"response": response},
    )


async def _latch_row(db_engine, latch_id: str) -> dict:
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, responses FROM latches WHERE id = CAST(:l AS uuid)"),
                {"l": latch_id},
            )
        ).mappings().first()
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


async def _calibration(db_engine, latch_id: str) -> dict | None:
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT prediction, proposal_snapshot, actual_responses,"
                    " matched FROM calibration_records"
                    " WHERE latch_id = CAST(:l AS uuid)"
                ),
                {"l": latch_id},
            )
        ).mappings().first()
    return dict(row) if row is not None else None
```

試験1〜8(同ファイルへ追記):

```python
# -- 試験1: 1対1の双方YES --


async def test_1_pair_double_yes(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    r1 = await _respond(api_client, h1, latch, "yes")
    assert r1.status_code == 200, r1.text
    assert r1.json()["latch"]["status"] == "partial_accept"
    r2 = await _respond(api_client, h2, latch, "yes")
    assert r2.status_code == 200, r2.text
    body = r2.json()["latch"]
    assert body["status"] == "matched"
    assert body["remaining_responses"] == 0
    row = await _latch_row(db_engine, latch)
    assert len(row["responses"]) == 2
    for r in row["responses"]:
        datetime.fromisoformat(r["answered_at"])  # Clock由来ISO(§9-2注記)
    assert row["status"] == "matched"


# -- 試験2: noとdefer --


async def test_2_no_and_defer_rejected(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch_no = await _latch(db_engine, [i1["id"], i2["id"]])
    r = await _respond(api_client, h1, latch_no, "no")
    assert r.json()["latch"]["status"] == "rejected"
    row = await _latch_row(db_engine, latch_no)
    assert row["responses"][0]["response"] == "no"  # 区別保持(引用#2)
    i3 = await _intent(api_client, h1, start=_future(BASE_HOURS + 12))
    i4 = await _intent(api_client, h2, start=_future(BASE_HOURS + 12))
    latch_defer = await _latch(db_engine, [i3["id"], i4["id"]])
    r = await _respond(api_client, h1, latch_defer, "defer")
    assert r.json()["latch"]["status"] == "rejected"
    row = await _latch_row(db_engine, latch_defer)
    assert row["responses"][0]["response"] == "defer"


# -- 試験3: 二重回答 --


async def test_3_double_answer_409(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    assert (await _respond(api_client, h1, latch, "yes")).status_code == 200
    r = await _respond(api_client, h1, latch, "yes")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ALREADY_ANSWERED"


# -- 試験4: 期限切れ(DB値操作・§9-2) --


async def test_4_expired_409_without_transition(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    # api常設のClockは動かせないため期限値を過去へ(等価置換・§9-2)
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE latches SET response_deadline = CAST(:d AS timestamptz)"
                 " WHERE id = CAST(:l AS uuid)"),
            {"d": SystemClock().now() - timedelta(hours=1), "l": latch},
        )
    r = await _respond(api_client, h1, latch, "yes")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "LATCH_EXPIRED"
    row = await _latch_row(db_engine, latch)
    assert row["status"] == "proposed"  # 遷移しない(expired遷移はws-2・引用#8)
    assert row["responses"] == []


# -- 試験5: 競合クローズ --


async def test_5_conflict_close_on_match(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    h3 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    i3 = await _intent(api_client, h3)
    latch_a = await _latch(db_engine, [i1["id"], i2["id"]])
    latch_b = await _latch(db_engine, [i2["id"], i3["id"]])
    await _respond(api_client, h1, latch_a, "yes")  # partial
    r = await _respond(api_client, h2, latch_a, "yes")  # matched
    assert r.status_code == 200
    assert r.json()["latch"]["status"] == "matched"
    row_b = await _latch_row(db_engine, latch_b)
    assert row_b["status"] == "cancelled"  # I2を含む他の開いているlatches
    events_b = await _events(db_engine, latch_b)
    assert ("proposed", "cancelled", None) in events_b  # user_id=NULL(引用#12)


# -- 試験6: クローズ後の回答 --


async def test_6_answer_to_cancelled_409(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]], status="cancelled")
    r = await _respond(api_client, h1, latch, "yes")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "LATCH_CLOSED"


# -- 試験7: 認可・不在・未登録 --


async def test_7_authorization(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    outsider = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)  # 同一ユーザー2Intent(1対1作成用)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    # 参加者以外は403(存在秘匿しない・引用#17)
    r = await _respond(api_client, outsider, latch, "yes")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
    # 存在しないidは404
    r = await _respond(api_client, h1, str(uuid_mod.uuid4()), "yes")
    assert r.status_code == 404
    # 未登録JWTも404(intentsと同型)
    subject = f"{field}{uuid_mod.uuid4().hex[:8]}"
    idp = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp}
    )
    unregistered = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    r = await _respond(api_client, unregistered, latch, "yes")
    assert r.status_code == 404


# -- 試験8: 422 --


async def test_8_invalid_response_value_422(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h1)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    r = await _respond(api_client, h1, latch, "maybe")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
```

試験9〜17(同ファイルへ追記):

```python
# -- 試験9: グループ3人(全員YES成立・部分成立禁止・D-06) --


async def test_9_group_of_three(api_client, db_engine, field):
    hs = [await _user(api_client, field) for _ in range(3)]
    intents = [await _intent(api_client, h) for h in hs]
    latch = await _latch(db_engine, [i["id"] for i in intents])
    r1 = await _respond(api_client, hs[0], latch, "yes")
    assert r1.status_code == 200
    body = r1.json()["latch"]
    assert body["status"] == "partial_accept"
    assert body["remaining_responses"] == 1  # あと1人(人数のみ・引用#22)
    await _respond(api_client, hs[1], latch, "yes")  # 2人yesでも未成立
    assert (await _latch_row(db_engine, latch))["status"] == "partial_accept"
    r3 = await _respond(api_client, hs[2], latch, "yes")
    assert r3.json()["latch"]["status"] == "matched"  # 3人目で成立
    # 別latch: 誰かのnoで即rejected(3人YESでも成立しない逆側・部分成立なし)
    intents2 = [
        await _intent(api_client, h, start=_future(BASE_HOURS + 24))
        for h in hs
    ]
    latch2 = await _latch(db_engine, [i["id"] for i in intents2])
    await _respond(api_client, hs[0], latch2, "yes")
    await _respond(api_client, hs[1], latch2, "yes")
    r = await _respond(api_client, hs[2], latch2, "no")
    assert r.json()["latch"]["status"] == "rejected"


# -- 試験10: グループ4人・group_candidate_id --


async def test_10_group_of_four_keeps_gid(api_client, db_engine, field):
    hs = [await _user(api_client, field) for _ in range(4)]
    intents = [await _intent(api_client, h) for h in hs]
    ids = [i["id"] for i in intents]
    now = SystemClock().now()
    member_scores = {
        "seed_id": ids[0],
        "versions": {i: 1 for i in ids},
    }
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text("""
                INSERT INTO group_candidates
                    (intent_ids, member_scores, status, created_at, updated_at)
                VALUES (CAST(:ids AS uuid[]), CAST(:ms AS jsonb), 'proposed',
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": [uuid_mod.UUID(i) for i in ids],
                "ms": json.dumps(member_scores),
                "now": now,
            },
        )
        gid = str(res.first()[0])
    latch = await _latch(db_engine, ids, gid=gid)
    for h in hs:
        r = await _respond(api_client, h, latch, "yes")
        assert r.status_code == 200, r.text
    assert (await _latch_row(db_engine, latch))["status"] == "matched"
    async with db_engine.connect() as conn:
        got = (
            await conn.execute(
                text("SELECT group_candidate_id FROM latches WHERE id = CAST(:l AS uuid)"),
                {"l": latch},
            )
        ).scalar_one()
    assert str(got) == gid  # 回答APIはgidを壊さない(§2.10と対の検証)


# -- 試験11: Intent遷移・競合(成立時) --


async def test_11_intent_transitions_on_match(api_client, db_engine, field):
    hs = [await _user(api_client, field) for _ in range(3)]
    i1 = await _intent(api_client, hs[0])
    i2 = await _intent(api_client, hs[1])
    i3 = await _intent(api_client, hs[2])
    latch_a = await _latch(db_engine, [i1["id"], i2["id"]])
    latch_b = await _latch(db_engine, [i2["id"], i3["id"]])
    await _respond(api_client, hs[0], latch_a, "yes")
    await _respond(api_client, hs[1], latch_a, "yes")  # matched
    async with db_engine.connect() as conn:
        statuses = (
            await conn.execute(
                text("SELECT id, status FROM intents WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": [uuid_mod.UUID(x) for x in (i1["id"], i2["id"], i3["id"])]},
            )
        ).fetchall()
    by_id = {str(r[0]): r[1] for r in statuses}
    assert by_id[i1["id"]] == "matched"  # 参加Intent=matched(引用#9)
    assert by_id[i2["id"]] == "matched"
    assert by_id[i3["id"]] == "active"  # 競合側の参加Intentは untouched
    assert (await _latch_row(db_engine, latch_b))["status"] == "cancelled"


# -- 試験12: latch_status_events(回答起因とシステム起因) --


async def test_12_status_events_user_and_system(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    h3 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    i3 = await _intent(api_client, h3)
    latch_a = await _latch(db_engine, [i1["id"], i2["id"]])
    latch_b = await _latch(db_engine, [i2["id"], i3["id"]])
    await _respond(api_client, h1, latch_a, "yes")  # proposed→partial_accept
    await _respond(api_client, h2, latch_a, "yes")  # partial→matched
    events_a = await _events(db_engine, latch_a)
    async with db_engine.connect() as conn:
        u1 = str(
            (await conn.execute(
                text("SELECT user_id FROM intents WHERE id = CAST(:i AS uuid)"),
                {"i": i1["id"]},
            )).scalar_one()
        )
        u2 = str(
            (await conn.execute(
                text("SELECT user_id FROM intents WHERE id = CAST(:i AS uuid)"),
                {"i": i2["id"]},
            )).scalar_one()
        )
    assert ("proposed", "partial_accept", u1) in events_a  # 回答起因=回答者
    assert ("partial_accept", "matched", u2) in events_a
    events_b = await _events(db_engine, latch_b)
    assert ("proposed", "cancelled", None) in events_b  # システム起因=NULL


# -- 試験13: Calibration(matched/rejected/グループminペア) --


async def test_13_calibration_records(api_client, db_engine, field):
    # 1対1 matched: 評価行のlatch_scoreをlatches.scoreと一致させる(§2.11第1段)
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    await _pair_eval(db_engine, i1["id"], i2["id"], wa=0.9, wb=0.8,
                     latch_score=0.85)
    latch = await _latch(db_engine, [i1["id"], i2["id"]], score=0.85)
    await _respond(api_client, h1, latch, "yes")
    await _respond(api_client, h2, latch, "yes")  # matched
    cal = await _calibration(db_engine, latch)
    assert cal is not None
    pred = cal["prediction"]
    assert pred["would_a_accept_b"] == 0.9
    assert pred["would_b_accept_a"] == 0.8
    assert pred["MutualScore"] == 0.8
    assert float(pred["L"]) == 0.85
    assert pred["provider"] == "typesafe_jev"
    assert pred["model"] == "jev-1.13.0"
    assert pred["segment"] in ("lexical", "semantic")
    assert cal["proposal_snapshot"]["headcount"] == 2
    assert len(cal["actual_responses"]) == 2
    assert cal["matched"] is True
    # rejected側: matched=False
    i3 = await _intent(api_client, h1, start=_future(BASE_HOURS + 36))
    i4 = await _intent(api_client, h2, start=_future(BASE_HOURS + 36))
    await _pair_eval(db_engine, i3["id"], i4["id"], wa=0.7, wb=0.7,
                     latch_score=0.7)
    latch2 = await _latch(db_engine, [i3["id"], i4["id"]], score=0.7)
    await _respond(api_client, h1, latch2, "no")
    cal2 = await _calibration(db_engine, latch2)
    assert cal2 is not None and cal2["matched"] is False
    assert len(cal2["actual_responses"]) == 1


async def test_13b_group_calibration_uses_min_pair(api_client, db_engine, field):
    """グループのprediction=minペアの値(§2.12・MutualScore最小=ACの0.6)。"""
    hs = [await _user(api_client, field) for _ in range(3)]
    intents = [await _intent(api_client, h) for h in hs]
    a, b, c = (i["id"] for i in intents)
    now = SystemClock().now()
    member_scores = {"seed_id": a, "versions": {a: 1, b: 1, c: 1}}
    async with db_engine.begin() as conn:
        res = await conn.execute(
            text("""
                INSERT INTO group_candidates
                    (intent_ids, member_scores, status, created_at, updated_at)
                VALUES (CAST(:ids AS uuid[]), CAST(:ms AS jsonb), 'proposed',
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {
                "ids": [uuid_mod.UUID(x) for x in (a, b, c)],
                "ms": json.dumps(member_scores), "now": now,
            },
        )
        gid = str(res.first()[0])
    await _pair_eval(db_engine, a, b, wa=0.9, wb=0.8)  # mutual 0.8
    await _pair_eval(db_engine, a, c, wa=0.6, wb=0.9)  # mutual 0.6 ← min
    await _pair_eval(db_engine, b, c, wa=0.7, wb=0.7)  # mutual 0.7
    latch = await _latch(db_engine, [a, b, c], gid=gid, score=0.6)
    for h in hs:
        await _respond(api_client, h, latch, "yes")
    cal = await _calibration(db_engine, latch)
    assert cal is not None
    pred = cal["prediction"]
    assert pred["MutualScore"] == 0.6  # minペア(AC)
    assert pred["would_a_accept_b"] == 0.6
    assert pred["would_b_accept_a"] == 0.9
    assert float(pred["L"]) == 0.6  # latches.score(aggregate)


# -- 試験14: segment(lexical/semantic) --


async def test_14_segment_lexical_and_semantic(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    h3 = await _user(api_client, field)
    h4 = await _user(api_client, field)
    # lexical: category_secondary一致(「焼肉」/「焼肉」)
    i1 = await _intent(api_client, h1, secondary="焼肉")
    i2 = await _intent(api_client, h2, secondary="焼肉")
    await _pair_eval(db_engine, i1["id"], i2["id"], wa=0.9, wb=0.9,
                     latch_score=0.9)
    latch_lex = await _latch(db_engine, [i1["id"], i2["id"]], score=0.9)
    await _respond(api_client, h1, latch_lex, "yes")
    await _respond(api_client, h2, latch_lex, "yes")
    cal = await _calibration(db_engine, latch_lex)
    assert cal["prediction"]["segment"] == "lexical"
    # semantic: 不一致(「焼肉」/「イタリアン」)
    i3 = await _intent(api_client, h3, secondary="焼肉",
                       start=_future(BASE_HOURS + 48))
    i4 = await _intent(api_client, h4, secondary="イタリアン",
                       start=_future(BASE_HOURS + 48))
    await _pair_eval(db_engine, i3["id"], i4["id"], wa=0.9, wb=0.9,
                     latch_score=0.9)
    latch_sem = await _latch(db_engine, [i3["id"], i4["id"]], score=0.9)
    await _respond(api_client, h3, latch_sem, "no")
    cal2 = await _calibration(db_engine, latch_sem)
    assert cal2["prediction"]["segment"] == "semantic"


# -- 試験15: GET一覧 --


async def test_15_list_excludes_candidate_sorts_and_paginates(
    api_client, db_engine, field
):
    h1 = await _user(api_client, field)
    i1 = await _intent(api_client, h1, start=_future(BASE_HOURS))
    i2 = await _intent(api_client, h1, start=_future(BASE_HOURS + 6))
    i3 = await _intent(api_client, h1, start=_future(BASE_HOURS + 12))
    # candidate(未提示・nearby的位置づけ)は一覧に出ない(引用#24)
    await _latch(db_engine, [i1["id"], i3["id"]], status="candidate")
    # target_time=max(time_start): early=+6(i1,i2)・late=+12(i2,i3)で昇順検証
    latch_early = await _latch(db_engine, [i1["id"], i2["id"]])
    latch_late = await _latch(db_engine, [i2["id"], i3["id"]])
    await _respond(api_client, h1, latch_early, "yes")  # my_response検証用
    resp = await api_client.get("/v1/latches", headers=h1)
    assert resp.status_code == 200
    body = resp.json()
    ids = [item["id"] for item in body["items"]]
    assert ids.count(latch_early) == 1
    assert ids.count(latch_late) == 1  # 2行とも出る(candidateは出ない)
    assert body["items"][0]["id"] == latch_early  # 対象時刻昇順
    assert body["items"][0]["my_response"] == "yes"
    assert body["items"][0]["remaining_responses"] == 1
    assert body["items"][1]["my_response"] is None
    assert "responses" not in body["items"][0]  # 引用#22
    # limit=1で2頁(cursor改頁)
    resp2 = await api_client.get("/v1/latches", headers=h1,
                                 params={"limit": 1})
    body2 = resp2.json()
    assert len(body2["items"]) == 1
    assert body2["next_cursor"] is not None
    resp3 = await api_client.get(
        "/v1/latches", headers=h1,
        params={"limit": 1, "cursor": body2["next_cursor"]},
    )
    body3 = resp3.json()
    assert body3["items"][0]["id"] == latch_late
    assert body3["next_cursor"] is None


# -- 試験16: GET詳細(解放情報) --


async def test_16_detail_release_after_match(api_client, db_engine, field):
    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    outsider = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    latch = await _latch(db_engine, [i1["id"], i2["id"]])
    # proposed: participants等は出ない(成立前の情報最小化・design §2.8)
    resp = await api_client.get(f"/v1/latches/{latch}", headers=h1)
    assert resp.status_code == 200
    latch_body = resp.json()["latch"]
    assert latch_body["participants"] is None
    assert latch_body["time_summary"] is None
    assert latch_body["area_name"] is None
    assert latch_body["proposal"]["match_level"] == "medium"
    # matched: 解放(表示名・time_summaryのJST書式・area_name=実geo)
    await _respond(api_client, h1, latch, "yes")
    await _respond(api_client, h2, latch, "yes")
    resp = await api_client.get(f"/v1/latches/{latch}", headers=h1)
    latch_body = resp.json()["latch"]
    names = sorted(p["display_name"] for p in latch_body["participants"])
    assert names == ["m3ws1", "m3ws1"]  # fixtureのdisplay_name
    assert all(p["profile"] == {} for p in latch_body["participants"])
    datetime.strptime(latch_body["time_summary"], "%Y-%m-%d %H:%M")
    assert latch_body["area_name"] is not None  # 実geofeaturesで天文館周辺
    # 参加者以外は403
    resp = await api_client.get(f"/v1/latches/{latch}", headers=outsider)
    assert resp.status_code == 403
    resp = await api_client.get(f"/v1/latches/{uuid_mod.uuid4()}", headers=h1)
    assert resp.status_code == 404


# -- 試験17: 削除経路(stage1拡張・close_latches_on_delete直呼び・§9-6) --


async def test_17_deletion_closes_and_restores(api_client, db_engine, field):
    from latch.worker import stage1 as stage1_mod

    h1 = await _user(api_client, field)
    h2 = await _user(api_client, field)
    i1 = await _intent(api_client, h1)
    i2 = await _intent(api_client, h2)
    # (a) 開いているlatchesのクローズ
    latch_open = await _latch(db_engine, [i1["id"], i2["id"]])
    del1 = await api_client.delete(f"/v1/intents/{i1['id']}", headers=h1)
    assert del1.status_code == 204
    async with db_engine.begin() as conn:
        await stage1_mod.close_latches_on_delete(
            conn, uuid_mod.UUID(i1["id"]), SystemClock().now()
        )
    assert (await _latch_row(db_engine, latch_open))["status"] == "cancelled"
    assert ("proposed", "cancelled", None) in await _events(
        db_engine, latch_open
    )
    # (b) matched解散+残Intent復帰(active側)
    i3 = await _intent(api_client, h1, start=_future(BASE_HOURS + 60))
    i4 = await _intent(api_client, h2, start=_future(BASE_HOURS + 60))
    latch_m = await _latch(db_engine, [i3["id"], i4["id"]])
    await _respond(api_client, h1, latch_m, "yes")
    await _respond(api_client, h2, latch_m, "yes")  # matched
    del2 = await api_client.delete(f"/v1/intents/{i3['id']}", headers=h1)
    assert del2.status_code == 204
    async with db_engine.begin() as conn:
        await stage1_mod.close_latches_on_delete(
            conn, uuid_mod.UUID(i3["id"]), SystemClock().now()
        )
    assert (await _latch_row(db_engine, latch_m))["status"] == "cancelled"
    assert ("matched", "cancelled", None) in await _events(
        db_engine, latch_m
    )
    async with db_engine.connect() as conn:
        restored = (
            await conn.execute(
                text("SELECT status FROM intents WHERE id = CAST(:i AS uuid)"),
                {"i": i4["id"]},
            )
        ).scalar_one()
    assert restored == "active"  # 残Intent復帰(expires_at未経過・引用#9)
    # (c) expires_at経過側はexpired復帰
    i5 = await _intent(api_client, h1, start=_future(BASE_HOURS + 72))
    i6 = await _intent(api_client, h2, start=_future(BASE_HOURS + 72))
    latch_e = await _latch(db_engine, [i5["id"], i6["id"]])
    await _respond(api_client, h1, latch_e, "yes")
    await _respond(api_client, h2, latch_e, "yes")  # matched
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE intents SET expires_at = CAST(:d AS timestamptz)"
                 " WHERE id = CAST(:i AS uuid)"),
            {"d": SystemClock().now() - timedelta(hours=1), "i": i6["id"]},
        )
    del3 = await api_client.delete(f"/v1/intents/{i5['id']}", headers=h1)
    assert del3.status_code == 204
    async with db_engine.begin() as conn:
        await stage1_mod.close_latches_on_delete(
            conn, uuid_mod.UUID(i5["id"]), SystemClock().now()
        )
    async with db_engine.connect() as conn:
        restored2 = (
            await conn.execute(
                text("SELECT status FROM intents WHERE id = CAST(:i AS uuid)"),
                {"i": i6["id"]},
            )
        ).scalar_one()
    assert restored2 == "expired"  # 経過済み復帰(引用#9・#20)
```

- [ ] **Step 2: groupengine世代交代試験を追記**

`backend/tests/integration/test_matching_groupengine.py` の末尾へ追記(§2.10の本体検証: ON CONFLICT昇格でlatches.group_candidate_idが新gidへ書き換わる。fixture: 旧gidのclosed集合+開いているcandidate行latches+新gidのcandidate集合+全ペアjev_result直書き→finalize→昇格UPDATE):

```python
async def test_promotion_overwrites_group_candidate_id(
    api_client, db_engine, field, redis_client, redis_sweep
):
    """世代交代昇格: 開いているlatches行へ昇格時gidが新世代へ書き換わる(§2.10)。

    旧世代(gid_old・closed)が残したcandidate行latchesへ、同一メンバーの
    新世代集合(gid_new)のfinalizeがON CONFLICT昇格する。
    """
    import json as json_mod

    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    members: list[dict] = []
    for _ in range(4):
        h, _s = await _user(api_client, field)
        members.append(
            await _intent(
                api_client, db_engine, h,
                _structured(start=start, expires=expires,
                            budget_max=4000, participants=(2, 4)),
            )
        )
    ids = [m["id"] for m in members]
    now = SystemClock().now()
    ms = {"seed_id": ids[0], "versions": {i: 1 for i in ids}}
    async with db_engine.begin() as conn:
        # 旧世代: 閉じた集合+candidatesのまま残っているlatches行
        res_old = await conn.execute(
            text("""
                INSERT INTO group_candidates
                    (intent_ids, member_scores, status, created_at, updated_at)
                VALUES (CAST(:ids AS uuid[]), CAST(:ms AS jsonb), 'closed',
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {"ids": [uuid_mod.UUID(i) for i in ids],
             "ms": json_mod.dumps(ms), "now": now},
        )
        gid_old = str(res_old.first()[0])
        await conn.execute(
            text("""
                INSERT INTO latches
                    (intent_ids, group_candidate_id, proposal, score, status,
                     response_deadline, expires_at, created_at)
                VALUES (CAST(:ids AS uuid[]), CAST(:gid AS uuid),
                        CAST(:proposal AS jsonb), :score, 'candidate',
                        CAST(:deadline AS timestamptz),
                        CAST(:expires AS timestamptz), CAST(:now AS timestamptz))
            """),
            {
                "ids": [uuid_mod.UUID(i) for i in ids], "gid": gid_old,
                "proposal": json_mod.dumps({"headcount": 4, "match_level": "low"}),
                "score": 0.5,
                "deadline": now + timedelta(hours=2),
                "expires": now + timedelta(days=5), "now": now,
            },
        )
        # 新世代: candidate集合(0005部分UNIQUEは旧がclosedのため共存可)
        res_new = await conn.execute(
            text("""
                INSERT INTO group_candidates
                    (intent_ids, member_scores, status, created_at, updated_at)
                VALUES (CAST(:ids AS uuid[]), CAST(:ms AS jsonb), 'candidate',
                        CAST(:now AS timestamptz), CAST(:now AS timestamptz))
                RETURNING id
            """),
            {"ids": [uuid_mod.UUID(i) for i in ids],
             "ms": json_mod.dumps(ms), "now": now},
        )
        gid_new = str(res_new.first()[0])
        # 全6ペアのjev_result直書き(世代=現行version 1)
        jev = {
            "would_a_accept_b": 0.9, "would_b_accept_a": 0.85,
            "jev_5axis": {}, "provider": "fixture", "model": None,
        }
        for x in range(4):
            for y in range(x + 1, 4):
                await conn.execute(
                    text("""
                        INSERT INTO match_candidates
                            (intent_a_id, intent_b_id, intent_a_version,
                             intent_b_version, retrieval_score,
                             cheap_judge_score, jev_result, status,
                             created_at, updated_at)
                        VALUES (CAST(:a AS uuid), CAST(:b AS uuid), 1, 1, 0.9,
                                0.9, CAST(:jev AS jsonb), 'evaluated',
                                CAST(:now AS timestamptz),
                                CAST(:now AS timestamptz))
                    """),
                    {
                        "a": uuid_mod.UUID(ids[x]), "b": uuid_mod.UUID(ids[y]),
                        "jev": json_mod.dumps(jev), "now": now,
                    },
                )
    group = _group_engine(db_engine, clock)
    await group.finalize(uuid_mod.UUID(ids[0]))
    async with db_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, group_candidate_id FROM latches"
                     " WHERE intent_ids = CAST(:ids AS uuid[])"),
                {"ids": [uuid_mod.UUID(i) for i in ids]},
            )
        ).first()
    assert row is not None
    assert row[0] in ("candidate", "proposed")  # try_promote後proposed
    assert str(row[1]) == gid_new  # 旧gid_old → 新gid_newへ書き換わる(§2.10)
```

**注記**: `_clock()`・`_future`・`BASE_HOURS`・`FAR_EXPIRES_H`・`_structured`・`_user`・`_intent`・`_group_engine` はtest_matching_groupengine.pyの既存ヘルパ。`_user`の戻り値は `(headers, subject)`。既存ヘルパのシグネチャ(例: `_structured` の `participants` キーの有無)はファイル内の実物に合わせて調整してよい(意図は「4人・互換・time_start共通」のfixture)。`timedelta` と `SystemClock` のimportが既存ファイルにない場合は追記する。

- [ ] **Step 3: 収集と実行の確認**

Run: `cd backend && uv run pytest --collect-only tests/integration/test_latches_api.py -q`
Expected: 18件収集(試験1〜17+13b)

Run: `make lint && make test`(unit全件・integrationは収集まで)

Run: `docker compose build api && make test-ci`
Expected: 既存1188+新規19前後(18+groupengine追記1)がグリーン。test-ci内でintegration試験が実DB・実HTTPで走る

- [ ] **Step 4: test-ci後の残存確認(報告書§検証手順6)**

```bash
docker compose exec -T db psql -U latch -d latch -tAc \
  "SELECT COUNT(*) FROM users WHERE auth_subject LIKE 'm3ws1-%'"
```
Expected: 0(latches・intents系も同様に0)

- [ ] **Step 5: コミット**

```bash
git add backend/tests/integration/test_latches_api.py backend/tests/integration/test_matching_groupengine.py
git commit -m "test: latches response integration (17 cases + gid promotion)"
```

### Task 12: 報告書作成・完了条件の検証

**Files:**
- Create: `docs/plans/M3/ws-1-report.md`

- [ ] **Step 1: 完了条件1〜7を§6のコマンドで検証し、結果を§7の形式で報告ファイルへ書く**

検証コマンド一式(§6の7条件・順に実行して出力を記録):

```bash
make lint && make test
docker compose build api
make test-ci
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
git diff main -- backend/alembic backend/pyproject.toml backend/uv.lock Makefile
git diff --name-only main | sort
git status --short
rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src
cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v
```

- [ ] **Step 2: 残存確認(§7検証手順6のSQL)を実行し報告書へ記録**

- [ ] **Step 3: コミット**

```bash
git add docs/plans/M3/ws-1-report.md
git commit -m "docs: ws-1 implementation report"
```

## 9. IF確定事項(実装定義の決定値。docs・design.mdが例示のみの部分を本計画で確定する。変更時は報告書に記録)

1. **競合クローズのSELECT FOR UPDATEに `ORDER BY id` を追加**(design §2.3のSQLへの追加)。ロック獲得順序を固定し、2つの回答txが互いに他側のlatches行をクローズしようとする際のデッドロック交差を構造的に排除する。対象行集合・遷移結果は不変(観測不変の安全化)。stage1側の`_SELECT_OPEN_LATCHES_ON_DELETE`・`_SELECT_MATCHED_LATCHES_ON_DELETE`も同じくORDER BY id付き
2. **期限切れintegration試験(試験4)はDB値操作で実現**: compose常設apiプロセスのClock(SystemClock)はテストから差し替え不能のため、design §4.2試験4の「FakeClockをresponse_deadline経過へ進める」を「latches.response_deadlineを過去へUPDATE」へ等価置換する。観測内容(409 LATCH_EXPIRED・status=proposed不変・responses不変)はdesignどおり。answered_atは「ISO形式・Clock由来」の検証まで(固定値一致は不可・試験1)
3. **cursorは3キー**: base64url(`target_time ISO | created_at ISO | latch_id`)。intentsのencode_cursorパターンの拡張。WHERE句のcursor条件は `(target_time > :tt) OR (target_time = :tt AND (created_at < :ct OR (created_at = :ct AND id > :lid)))`(ソート順 target_time ASC・created_at DESC・id ASC のタプル比較を展開・`:tt IS NULL OR` で先頭ページを同一SQLで扱う)
4. **`LatchValidationError`(422 VALIDATION_ERROR)をerrors.pyへ追加**: cursor形式不正のために必要(intentsのIntentValidationErrorと同型・design §3.1の列挙への追加)。response値域外の422はPydantic+main.pyのRequestValidationErrorハンドラが担う(latches固有ではない)
5. **評価行3段階特定は「全行取得+純関数」で実装**: `SELECT jev_result, latch_score, updated_at ... WHERE a,b AND jev_result IS NOT NULL ORDER BY updated_at DESC`(同一ペアの行数は世代数程度で高々数行)を取得し、Python側の `pick_eval_row` がdesign §2.11の3段階を適用。unit試験で3段階を決定的に検証できる。latch_scoreとscoreの比較はDecimal同士(NUMERIC同値・design §2.11)
6. **stage1削除Eventのintegration試験(試験17)は `close_latches_on_delete` 直呼び**: 実HTTP DELETE(認可・cancelled遷移はM1実装)の後に、WorkereenのEvent投入を介さず関数を直接呼ぶ(Workereenはtest-ciで停止しており、intake経由のE2E投入はws-2のバッチ系試験とともに整備する)。実DB・実SQLでの検証は直呼びでも同価値
7. **`make_latches_service(clock, engine)`はGeoServiceをengineから内部構築**: main.py lifespanは引数2つで呼ぶ。unit試験は `LatchesService(clock=..., engine=..., geo=None)` で直接構築(geo=Noneならarea_name=None)
8. **main.pyの注入引数は `latches_service`**(create_appの既存引数群の末尾に追加)。lifespanの構築スキップ判定・早期return条件へbuild_latchesを加える(intents・usersと同型の独立判定)
9. **`fetch_intents_structured`/`fetch_intents_geo`/`fetch_participants` は引数idsの順に並べ替えて返す**(SQLの結果順に依存しない決定性)
10. **remaining_responsesはmatched/completedで0・それ以外はlen(intent_ids)−yes数**。my_responseは自分の回答のみ(未回答null)。responses配列を応答へ出さない(引用#22)
11. **POST response応答のcompleted_atは常にNone**(completed遷移はws-2)。latch要素はLatchSummaryOut(response_deadline・expires_at・created_at・proposalを含む・05 §5の応答例に沿う)
12. **group_engine昇格UPDATEの呼び出し側は `_ReadyGroup.gid`(r.gid)を渡す**。世代交代のE2Eはtest_matching_groupengine.pyへ1試験追記(旧gidのclosed集合が残したcandidate行latches+新gid集合+全ペアjev直書き→finalize→gid書き換え検証)。latches fixtureでgroup_candidatesの開いている行を2行作ることは0005部分UNIQUEにより不可(旧はclosedにする)
13. **固定文言一覧**(08 §2.4・機微なし): NotFoundError="user not found"/"latch not found"・Forbidden="not a participant"・Expired="response deadline passed"・Closed="latch closed"・AlreadyAnswered="already answered"・503="latches dependency unavailable"/"intents changed during match"・構造化ログは `latch.calibration.missing latch_id=%s` と `latches.unexpected class=%s`・routesのinfoログは `latches.response ok status=%s` のみ
14. **回答APIの`:now`はFOR UPDATE取得後に1回だけ採取**(design §2.2。検査2cとUPDATEのWHEREで同一オブジェクトを使い回す)
15. **試験13の1対1はlatch_scoreをlatches.scoreと一致させて第1段特定を通す**(0.85/0.85)。一致しない場合は第2段(updated_at<=created_at)で拾うが、試験は第1段で確定的に
16. **`_INSERT_LATCH_EVENT_SQL`はstage1.py内へ自前定義**(latch_engine._INSERT_LATCH_EVENTと同一SQL。stage1はworker/matchingをimportしない現状構成を維持)

## Self-Review記録(計画書作成時の確認 — 2026-09-30)

1. **design §1.1の9項目の対応**: ①回答API3本→Task 5〜8 ②LATCH遷移残り→Task 6(compute_new_status+手順6) ③グループ回答→Task 6(compute_new_statusの|S|対応)+試験9/10 ④競合クローズ→Task 6(_on_matched)+試験5/11 ⑤Intent遷移(matched化)→Task 6・復帰はTask 10 ⑥Calibration→Task 3/4/6+試験13/13b/14 ⑦ws-7引継ぎ→Task 9+試験10・世代交代。design §2.13の切り捨ては§5へ一覧化
2. **placeholder scan**: 「 TBD/TODO/適切に/同様に 」なし。Task 6テストのスタブ補助は `_ret` 1本に統一済み。試験15はtarget_timeが同点になるIntent組(i3共有)を修正済み(early=(i1,i2)・late=(i2,i3))
3. **型整合**: `pick_eval_row(rows: list[EvalRow], score: Decimal, latch_created)` とserviceの呼び出し(`row.score` はLatchRow.score: Decimal)整合。`PairEvalRow(a, b, va, vb, jev_result)` とfetch_group_pairsの構築整合。`compute_new_status(response: str, responses, total)` とroutesのbody.response(str)整合。Task 9の `_update_group_latch_for_promotion(..., gid)` と呼び出し `gid=r.gid` 整合
4. **Review Focus**: 1(デッドロック)→§9-1+Task 5ピン+試験5。2(期限切れ受理)→Task 5ピン+Task 6 unit+試験4。3(削除レース)→Task 5 NOT EXISTSピン+試験17。4(uuid[]バインド)→Task 5 compile検査+試験5/11の `&&` 実行。5(回答者特定情報)→Task 2構造ピン+試験15。すべて所有タスクの試験でピン留め
5. **機械チェック(M2 ws-7/ws-8踏襲)**: basename一意=§0に機械確認結果(既存106ファイル・新規5ファイルとも衝突なし・Task 8のtest_latches_routes.pyを含む)。ピン試験への影響訪問=group_engineの列追加は既存試験の期待値を持たない(test_group_engine.pyへ新規ピン追加のみ・test_matching_groupengine.py:480は同一gid昇格のため無傷)/layer3の `_bigrams` は削除しないためtest_layer3.pyの既存import無傷/stage1の削除Event試験2件はFakeResult追加の機械的追従(§Task 10に明記)。DB/Redis残存干渉対抗策=subjectプレフィックス `m3ws1-`+FK順teardown(§Task 11)+時間窓now+5日系(BASE_HOURS=120)分離+Redis prefix(本単位はRedisを消費しないが規律として同prefix)
6. **design §4.2の17試験とTask 11の対応**: 1〜17すべて実装(13は13/13bの2関数)。groupengine世代交代を+1。02#14は試験1〜3・6〜8が本体(design §4.2末尾の記述どおり)
