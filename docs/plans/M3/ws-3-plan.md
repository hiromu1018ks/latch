# M3 ws-3(通知の媒体確定)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** M2 ws-6(latch_engine)とM3 ws-2(sweeper)が先行書き込みしたnotifications行の媒体を確定させる — ①`notifications/`パッケージ新設: プッシュ文面テンプレート(type→固定文言の写像のみ=FR-21の構造担保)・StubPushSender(ドライラン・`latch.push.send`構造化ログ)・PushSenderをLatchEngine/ExpirySweeperへ注入(txコミット直後に送信・例外は握る)②お知らせAPI: GET /v1/notifications(latchesをLEFT JOIN埋め込み・2キーcursor)・POST /v1/notifications/{id}/read(204冪等)③settings 2キー(push_mode/push_stub_delay_ms)。**マイグレーション追加なし(alembic head=0005不変)・依存追加なし・実FCM資材はG3後**。

**Architecture:** design §2.1案A — 送信は「notifications書き込みtxのコミット直後・Worker内で直接呼び出し」。順序を「txコミット→送信」で固定(逆だとnotifications行がないままプッシュが飛び得る)。失敗時の扱いは「例外を握って記録」(notifications行=通知の事実はコミット済み・プッシュは配信経路の一つ=引用#3/D-18)。文言は`build_push(notification_type)`がtype以外の引数を持たないことでFR-21を構造担保(design §2.4案A)。お知らせ一覧は`payload->>'latch_id'`でlatchesをLEFT JOINしlatch要素を埋め、文言組み立てはクライアントへ委ねる(design §2.5案A・05 §2引用#8)。表示規制(hidden/nearbyの最小proposal)はサーバ側のデータ構造で担保されたまま。

**Tech Stack:** 変更なし(Python 3.13 / FastAPI / SQLAlchemy text()生SQL+asyncpg / pytest+caplog)。firebase-adminのimportすら行わない(資材分離・G3待ち)。

**Spec:** `docs/plans/M3/ws-3-design.md`(agent1設計メモ。**未解決論点なし** — §5のsupervisor承認事項4件+解釈記録2件は2026-10-01承認済み・承認記録はSTATUS.md 310行目。①PUSH_BODY_NOTICE第二汎用文〔G3時確認候補〕②nearby本文も提案同一文言③送信はtxコミット直後・失敗再送なし〔outboxはG3後再検討〕④お知らせ一覧へlatch要素LEFT JOIN埋め込み・文言はクライアント組立。解釈記録=成立matched通知は作らない・attendance_requestはD-08上限不消費はG3時確認事項へ。design.mdが本計画より優先)。

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m3-ws-3`。**agent3はworktree内でコミットする**(mainへの直接コミット・pushは禁止。マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する(**依存追加・マイグレーション追加ともになし**のためlockもalembicも進まない)。
- **【最重要】共有ci-dbへの `make test-ci` / `make migrate` は実行禁止**(2026-10-01スーパーバイザー指示)。**ws-4(チャット+実施自己申告)が並行で実装されており、ws-4はマイグレーション0006を追加する**ため同時実行がDBを取り合う(運用ルール1。「Can't locate revision」系の失敗・他単位の試験破壊の恐れ)。開発は **unit試験(`make lint && make test`)のみ**で進め、integration試験は**収集確認(`--collect-only`)まで**とする。実行はスーパーバイザーが直列で行う。報告ファイルの該当欄には **「test-ci=スーパーバイザー検証待ち」** と記録する。`docker compose build api` 等のdocker系コマンドも本単位では不要(test-ciを実行しないため)。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。**計画時点で機械確認済み(2026-10-01)**: 現状 `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空(既存114ファイル)。本計画の新規4ファイル(`tests/unit/notifications/test_templates.py`・`tests/unit/notifications/test_push_sender.py`・`tests/unit/notifications/test_notifications_service.py`・`tests/integration/test_notifications_api.py`)は既存と衝突しない(既存リストに同名なし)。各Taskのコミット前に同コマンドが空であることを再確認する。
- **`tests/unit/notifications/` ディレクトリは新規作成するが `__init__.py` は置かない**(prepend mode運用・`tests/unit/worker/` と同型)。
- **alembic 0001〜0005は変更禁止**(design §3.3・マイグレーション追加なし): Task 11で `git diff main -- backend/alembic` が空であることを検証する。head=0005不変。
- **依存追加なし**: `backend/pyproject.toml`・`backend/uv.lock`・`Makefile`・`.env`・`.env.example` に新規依存・設定キーを追加しない。`settings.py` への追加は `push_mode`・`push_stub_delay_ms` の2キーのみ(design §3.2)。
- **test_rate_limit_wiring.py は両単位が触る**: ws-4が同ファイルへ別ルート3行を追記する。**追記位置はルート列挙(Counterの辞書リテラル)の該当箇所に機械的に足すのみ**。マージ時に両側保持で解消するのはスーパーバイザーの担当のため、コンフリクトを気にせず追記してよい(design §5-8)。
- **固定値の遵守**: design §2 の採用判断(案A・SQL・IF)と本計画§9のIF確定事項は固定値。**変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `refactor:` `chore:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| プッシュ本文は汎用文「LATCH候補があります。詳細はアプリでご確認ください」のみ。条件サマリ(人数・時間帯・地域名・カテゴリ・予算)を含めない。**drinkingも様式同一**(カテゴリ推察自体を防ぐ)。規制はプッシュ許可の有無と独立 | 03 §4・08 §2.6(FR-21)・design §1.2#1 |
| アプリ内通知(summary_only)=「LATCH候補があります。/ 今夜 20:00〜 / 天文館 / 3人 / 焼肉 / 予算上限5,000円 / …一致度が高い候補です。」・アプリ内通知(hidden_until_match)=「条件が合う候補があります。一致度が高い候補です。今夜中にご回答ください。」(文言はクライアントが組み立て) | 03 §4様式・design §1.2#2 |
| 通知手段はプッシュ+アプリ内通知の併用。許可を取得できないユーザーにはアプリ内通知で到達(プッシュは合図のみ・内容の確認と回答はアプリ内) | 03 §4・D-18・design §1.2#3 |
| 提案通知は双方(グループでは必要人数全員)へ同時送信 | 03 §8 D-20・01 §8・design §1.2#4 |
| nearby存在通知もD-08日次上限(日6件)に含む。同時進行上限はproposed数の上限のためcandidateのままの存在通知には適用しない。nearby存在通知に条件サマリ・一致度・相手情報を含めない | 03 §4・01 §10・06 §6・design §1.2#5 |
| mutedのIntentは通知を送らない(proposed遷移などの挙動は通常どおり) | 06 §6・05 §6・design §1.2#6 |
| GET /v1/notifications / POST /v1/notifications/{id}/read — 本人のみ。一覧はページネーション共通規定(`?cursor=&limit=1〜100`・既定20・超過422・`{"items":[], "next_cursor"}`・不透明cursor)・ソートはnotificationsがcreated_at降順 | 05 §5・§5ページネーション共通規定・design §1.2#7 |
| latches.proposalは通知文・提案画面の表示要素のデータソース。**文言はクライアント/Layer 5のテンプレートがこの構造から組み立てる**。hidden_until_matchではheadcount+match_levelのみ格納 | 05 §2・design §1.2#8 |
| 通知タップは提案詳細画面へ遷移。バナー上の直接回答ボタンは設けない | 03 §4(FR-46)・design §1.2#9 |
| 実施自己申告: matched→completed時点でプッシュ+アプリ内通知により1問の自己申告を求める。設問「実際に会いましたか?」・回答期限3日。cancelled LATCHには送らない | 05 §6・09 §2.2 D-09・design §1.2#10 |
| FCMはドライラン(送信内容を記録のみ)とし、通知の到達と本文の表示規制(FR-21)は記録で検証。#13はmutedケースを含む。追加試験はプッシュ本文が汎用文であること・条件サマリがプッシュへ出力されないこと(drinking含む) | 10 §1・§3(#13・追加試験)・design §1.2#11 |
| 通知2経路: プッシュ=FCM統一。アプリ内通知=DB保存+自前表示 | 04 §2・§3・design §1.2#12 |
| 通知は判定後5秒以内(LATCH Engine確定後、NotificationがFCM APIを同期的に呼び出す)。層別予算はLayer 5+通知送信≤2秒 | 04 性能目標表・06 §1・design §1.2#13 |
| 構造化ログは許可リスト方式(出力フィールドを型で固定)。例外・エラーはIDのみ | 08 §2.4・design §1.2#14 |
| time_summary=JST `YYYY-MM-DD HH:MM`・area_name=geo中点の逆転ジオコーディング・nearby提案は`{"headcount", "match_level": "low"}`最小構成(ws-6実装済みの解釈) | ws-6設計§2.5・§2.7・design §1.2#15 |
| 書き込み経路は2箇所のみ: `latch_engine._insert_notification`(proposal/nearby_candidate)と`sweeper._INSERT_ATTENDANCE_NOTIFICATION`(attendance_request) | design §1.3 |
| D-08カウントの真実はnotifications行(type IN ('proposal','nearby_candidate')のJST日付窓COUNT)。attendance_requestは提案通知でないため日次上限を消費しない(本単位で変更しない) | design §1.3・§5-6 |
| LLM Gatewayスタブの型(模倣対象): Provider protocol+StubLLM(遅延注入・決定的応答)・SendRecord→構造化ログ`latch.llm.send`・settings `llm_mode`(realは鍵欠落でfail-fast) | design §1.3 |
| 送信はtxコミット直後(案A)。失敗時は例外を握って記録(再送なし)。notifications書き込みtx失敗ならプッシュは送らない(構造的排除) | design §2.1・§2.7・supervisor承認③ |
| StubPushSender: `build_push(notification_type)`で(title, body)を得て遅延後PushSendRecord(status="ok")。ネットワーク呼び出し一切なし。PUSH_TIMEOUT_S=3.0(コード定数・unit試験で短縮注入) | design §2.2 |
| PushSendRecordは本文を含む(10 §1が記録での本文検証を要求)。出力先は`latch.push.send`(latch.llm.sendと同型・JSON 1行) | design §2.3・10 §1 |
| プッシュ文面はtype→固定文言の写像のみ(案A)。proposal/nearby_candidateは同一文言。attendance_requestは第二汎用文。hidden_until_matchの提案もプッシュ本文は同一 | design §2.4・supervisor承認①② |
| お知らせ一覧はlatchesをLEFT JOINで埋め込み(案A)・文言はクライアントが組み立て。cursorはbase64url 2キー(created_at, id)・同点はid降順タイブレーク | design §2.5・supervisor承認④ |
| 既読API: 対象行SELECT(WHERE id AND user_id=:me)→行がなければ404(他人・不存在を区別しない)→未読ならUPDATE→204冪等。POST /v1/sessionsと同型 | design §2.6 |
| 不明なnotification_typeはbuild_pushがValueError(fail-fast・握らない)=実装バグの即時顕在化 | design §2.7 |
| 採用しないもの: push outbox・未読カウントAPI・firebase-admin先行追加・type CHECK制約・FCM topic・一覧絞り込み | design §2.8・§1.4 |

## 2. グローバル制約(全タスクに暗黙に適用)

- **時刻はClock経由のみ**。製品コード(`backend/src/latch/`)で実時間参照禁止(arch test `test_arch_no_direct_time.py` が強制)。PushSendRecord.occurred_atはClock.now()(tz-aware UTC)。`asyncio.sleep` は待機であり時刻参照ではない(StubLLM `_apply_delay` と同一規律・design §2.2)。
- **永続化はtext()生SQLのみ**。SQLAlchemy ORMは導入しない。asyncpgのUUID復元は `_coerce_uuid` 規律(latches/intents/sweeperと同一)。
- **FR-21の構造担保**: `build_push(notification_type)`の引数はtypeのみ。proposal/latchのデータ(時間・地域・人数・カテゴリ・予算・スコア)を受け取る経路を型の上に作らない(design §2.4案A)。
- **送信メソッドは例外を呼び出し元へ出さない(握って記録)**。ただし `build_push` のValueError(未知type)は握らない(fail-fast・design §2.7)。
- **latch_engine・sweeperの既存tx構成は変更しない**。追加するのはtx外の送信呼び出しのみ(LLM呼び出しのtx外化と同じ規律・design §2.7)。
- **`_count_daily_notifications`(D-08日次カウントSQL)・`_COUNT_DAILY_NOTIFICATIONS` は無変更**(attendance_requestは上限不消費・design §1.3)。try_promote・_nearby_in_txの上限判定ロジックに触れない。
- **例外・ログに機微を入れない**(08 §2.4)。PushSendRecordは文言定数のみを通り得るため機微は入り得ない(design §2.3)。
- **notificationsテーブル(0001)は無変更**: `id/user_id/type/payload(jsonb)/read_at/created_at`・typeにCHECK制約なし(ws-6実装時確認事項9の判断を継承)。
- **`make lint`(ruff E,F,I,UP,B・format行長88)と `make test` を毎コミット通す**。unit試験は外部プロセス不要・実時間待ちなし(スタブ注入・FakeClock)。timeout試験は `asyncio.timeout` が打ち切るため実時間は待機しない。
- **全タスクのコミット前に**: `make lint && make test` グリーン・`git status --short` で差分が§4の一覧どおり・basename一意コマンドが空。

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **プッシュ本文へ条件サマリの混入(FR-21・引用#1)** — 人数・時間帯・地域名・カテゴリ・予算・一致度・visibility/drinkingの別が本文へ出るとOS経路(ロック画面等)に第三者へ漏れる。文面を組み立てる関数がproposalデータを受け取れる時点で保証はレビュー依存になる → Task 1の構造(引数はtypeのみ)+`test_build_push_output_has_no_condition_summary`(語彙不在検査)+Task 10試験2(3ケースbyte同一)
2. **txコミット前の送信(design §2.1)** — 送信後にtxが失敗/ロールバックすると「notifications行がないのにプッシュだけ飛ぶ」状態が生まれ、D-08カウントの真実(通知の事実)と配送が乖離する。早期return(上限保留・競合負け)で送信が残っていてはならない → Task 4 unit(`test_promote_daily_limit_candidate_keeps_no_push`・promote失敗=0件)+Task 5 unit(影響0=0件)+送信呼び出しの配置はtxブロック外(コード構造)
3. **muted参加者への送信(引用#6・#13)** — mutedは通知のみしない(proposed遷移・日次上限計上は通常どおり)。notifications書き込みのnotifyリスト(muted除外)と送信対象が別ロジックに分かれると片方だけ漏れる → Task 4 unit(muted除外1件)+Task 10試験3(muted下地: 記録1件・行1行・status=proposed)
4. **他人の通知の閲覧・既読(design §2.6)** — idを知っていれば他人のお知らせを読める/既読にできる。所有検査の漏れ・他人と不存在の区別(存在秘匿) → Task 7のSQLピン(WHERE user_id=:me)+Task 10試験5b(他人404・ランダムuuid 404)
5. **同一tx内同時刻行のcursor取りこぼし(design §2.5)** — D-20同時送信は同一txで全員分の行を書くためcreated_atが同時刻。created_atのみのcursor・ORDER BYは同時刻行を落とす/重複する。加えてpayload->>'latch_id'が不正値の行で一覧が500(CAST失敗)にならない → Task 7の2キーcursor+ORDER BY created_at DESC, id DESC+正規表現ガードJOIN(SQLピン)+Task 10試験4a/4b

---

## 4. スコープ(作成・変更するファイル一覧)

作成(§0のbasename機械確認済み・design §3.1+報告ファイル):

```text
backend/src/latch/notifications/__init__.py               (Task 2で雛形・Task 8/9で公開IF完成)
backend/src/latch/notifications/types.py                  (Task 1)
backend/src/latch/notifications/templates.py              (Task 1)
backend/src/latch/notifications/records.py                (Task 2)
backend/src/latch/notifications/sender.py                 (Task 2)
backend/src/latch/notifications/store.py                  (Task 7)
backend/src/latch/notifications/schemas.py                (Task 8)
backend/src/latch/notifications/service.py                (Task 8)
backend/src/latch/notifications/routes.py                 (Task 9)
backend/tests/unit/notifications/test_templates.py        (Task 1)
backend/tests/unit/notifications/test_push_sender.py      (Task 2)
backend/tests/unit/notifications/test_notifications_service.py (Task 7〜8)
backend/tests/integration/test_notifications_api.py       (Task 10)
docs/plans/M3/ws-3-report.md                              (Task 11。報告ファイル)
```

変更(design §3.2):

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/src/latch/worker/matching/latch_engine.py` | ①type定数をnotifications.typesへ移譲(import切替・31〜32行の定義削除)②ctorへ `push` 引数(None許容)③try_promote: tx内で送信対象を集めtx後に送信 ④_nearby_in_tx: 戻り値に通知済みuser_id群を追加+呼び出し元_evaluate_pairがtx後に送信 ⑤`_send_pushes` ヘルパー追加 | 4 |
| `backend/tests/unit/matching/test_latch_engine.py` | `_engine` ヘルパーへpush引数追加+末尾へFakeSender注入試験4件追記 | 4 |
| `backend/src/latch/worker/sweeper.py` | ①type定数import切替(31行)②ctorへpush引数 ③_complete_latch: tx後送信 ④`_send_attendance_pushes` ヘルパー追加 | 5 |
| `backend/tests/unit/worker/test_sweeper.py` | `_sweeper` ヘルパーへpush引数追加+attendance送信試験2件追記 | 5 |
| `backend/src/latch/worker/main.py` | `build_push_sender` で構築しLatchEngine・ExpirySweeperへ注入(design §2.1案Aの配線) | 6 |
| `backend/src/latch/settings.py` | `push_mode: str = "stub"`・`push_stub_delay_ms: int = 0` 追加(ファイル末尾・reset_retry_secの後) | 3 |
| `backend/tests/unit/test_settings.py` | `test_settings_defaults` へ2キーのassertを追記(機械的) | 3 |
| `backend/src/latch/main.py` | notifications_router登録・build_notificationsフラグ(latchesと同型)・create_appのnotifications_service引数・NotificationsErrorハンドラ | 9 |
| `backend/tests/unit/ratelimit/test_rate_limit_wiring.py` | Counterへ新ルート2行の追従(機械的・M1 ws-5前例・ws-4とマージ時両側保持) | 9 |

`backend/tests/unit/notifications/` ディレクトリは新規作成するが `__init__.py` は置かない(§0のとおり)。

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`。

## 5. 禁止(触ってはいけないもの・スコープ外と判断する基準)

- **`make test-ci` / `make migrate` / docker系コマンド全般**(`docker compose build api` 含む): §0のとおり(test-ci禁止・検証はスーパーバイザー)。unitは `make test` のみ。
- **実API呼び出し**: `make g1-gate`・`make g2-gate`・`make embed-smoke`・`make jev-smoke` は起動しない(本単位はLLM Gatewayに触らない)。
- **design §3.3の禁止**: `backend/src/latch/` 配下の auth / users / intents / ratelimit / geo / core / events / llm / g1gate / g2gate / latches 各モジュール。`worker/` の既存ファイルのうち変更対象以外(`worker/__main__.py`・`worker/embedding.py`・`worker/embedding_text.py`・`worker/debounce.py`・`worker/backfill.py`・`worker/jev.py`・`worker/stage1.py`・`worker/reset.py`・`worker/reeval.py`・`cost/`配下)。`worker/matching/` のうち変更対象以外(`candidates.py`・`group_calc.py`・`latch_calc.py`・`group_engine.py`・`layer1.py`〜`layer4.py`・`origin.py`・`proposal.py`・`runner.py`)。`backend/alembic/`(0001〜0005不変・追加もしない)。`compose.yaml`・`docker/`・`frontend/`・`prototype/`。`backend/pyproject.toml`・`backend/uv.lock`(依存追加なし)。`.env`・`.env.example`。`docs/`(01〜12・learn・testassets)。`docs/plans/STATUS.md`(スーパーバイザー管理)。`docs/plans/` の既存ファイル(M0/M1/M2/M3のdesign・plan・report)。`backend/tests/` の§4に列挙した以外の既存試験(**test_matching_latchengine.py〔integration〕・test_expiry_batches.py〔integration〕・test_llm_factory.py・test_worker_reeval.py は触らない** — 影響有無はSelf-Reviewで確認済み)
- スコープ外と判断する基準(必要になったと感じても作らない — design §1.4・§2.8):
  - **お知らせUI・通知許可の要求・設定画面・未読ドット** → ws-8。未読カウント専用APIは05に規定がないため作らない(一覧itemsのread_atで判定可能)
  - **実FCM送信の資材**(firebase-admin依存・Firebaseプロジェクト・サーバー認証情報・VAPID鍵・デバイストークン管理・WebpushConfigのclick URL) → G3後の「real」実装(STATUS G3判定の待ち事項1の人間領域)。`FirebasePushSender` は本単位では**不作成**
  - **D-08上限判定・提示順・D-05再計算の本体** → M2 ws-6実装済み・再実装しない。`_count_daily_notifications` 無変更
  - **チャット・実施自己申告の回答API(attendance)** → ws-4。attendance_request通知の**回答側**は本単位の外(媒体だけが本単位)
  - **提案詳細画面の残時間表示** → ws-7(response_deadline列は既に供給済み)
  - **成立(matched)通知・expiry通知** → 05 §6遷移表に通知規定なし(design §5-5・STATUS 61行)。作らない
  - **push outbox(push_sent_at列+ディスパッチャ)** → design §2.8-1。実FCM化(G3後)に再検討
  - **一覧の絞り込み(unread_only等)・type CHECK制約・文言列・FCM topic配信** → design §2.8
  - **02#13等のstaging E2E・#22のUI** → ws-9(本単位はci相当の下地試験まで)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 11で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(本単位のunit追加分29件・既存試験の追従を含む)
   検証: `make lint && make test` — ともにexit 0。全件数を報告書に記録。**期待値: unit 1129件**(2026-10-01時点のmain 1100件+本単位新規29件=test_templates 4+test_push_sender 9〔parametrize込み〕+test_latch_engine 4+test_sweeper 2+test_notifications_service 10。test_settings/test_rate_limit_wiringは既存試験への追記で件数不変。§9-12)
2. **integration試験が収集できる**(実行はスーパーバイザー検証時)
   検証: `uv run pytest --collect-only tests/integration/test_notifications_api.py -q` — exit 0・**12件収集**。件数を報告書に記録(201+12=213件・test-ci全体は1301+29+12=1342件になる見込み)
3. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし(空)
4. **マイグレーション・依存に差分なし**
   検証: `git diff main -- backend/alembic backend/pyproject.toml backend/uv.lock Makefile` — 出力なし(空)
5. **変更ファイルが§4の一覧どおり(作成14+変更9=23ファイル)**
   検証: Task 11の報告コミット後に `git diff --name-only main | sort` が§4の一覧(report込み)と完全一致。`git status --short` が空
6. **実時間参照が `core/clock.py` のみ**
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|from time import' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0(`asyncio.sleep` は待機であり禁止対象外・StubLLMと同一)
7. **既存ピン試験がすべてグリーンのまま**(Self-Review(2)の追随访問対象が期待どおり無傷であることを `make test` の全件数1129と§4との突合で示す)
8. **`make test-ci` は実行していないこと**(§0規律・報告書に「test-ci=スーパーバイザー検証待ち」と記録)

## 7. 報告形式

**結果ファイル**: `docs/plans/M3/ws-3-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M3 ws-3(通知の媒体確定)実行報告

- ブランチ: m3-ws-3 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る(全件数も。期待unit 1129)> |
| 2 | integration収集 | PASS/FAIL | <collect-only出力(期待12件)> |
| 3 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 4 | alembic・依存・Makefile無変更 | PASS/FAIL | <git diff出力(空なら「空」)> |
| 5 | 変更ファイル=§4の23ファイル | PASS/FAIL | <git diff --name-only出力> |
| 6 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 7 | 既存ピン試験グリーン維持 | PASS/FAIL | <make test 全件数と§4列挙以外の期待値変更がないことの確認結果> |
| 8 | test-ci未実行 | PASS | test-ci=スーパーバイザー検証待ち(ws-4並走・0006取り合い回避) |

## design §5 実装時確認事項の結果
- §5-7(type定数import切替後の既存試験の無傷): <test_latch_engine.py・test_sweeper.pyが
  文字列リテラル/再export参照で緑維持であることの確認結果>
- §5-8(test_rate_limit_wiring.py追記のws-4との衝突): <追記2行の位置と内容・機械的追従であること>

## 固定値の変更有無(design.md §2・本計画§9)
- build_pushの写像・文言3定数(§9-2): 変更なし / 変更あり
- StubPushSender・PUSH_TIMEOUT_S=3.0(§9-4): 変更なし / 変更あり
- store SQL 3本+LEFT JOIN正規表現ガード(§9-6): 変更なし / 変更あり
- cursor 2キー・スキーマ(§9-7/8): 変更なし / 変更あり
- その他§9のIF確定事項: 変更なし / 変更あり(<前→後+理由>)

## (スーパーバイザー検証時の手順・結果記入欄)
- docker compose build api → make test-ci(期待 1342 = main 1301 + 新規41)
- 残存確認(m3ws3-%): <SQL 5本の結果(すべて0件)>
- Redis掃除確認: <SCAN結果(0件)>

## コミット一覧
<git log --oneline main..HEAD>

## 補足(詰まった点・判断した点があれば)
<自由記述>
```

**検証手順(design §4.3・§0規律どおりtest-ciは実行しない)**:

1. `make lint && make test` — unit全件グリーン(期待1129件)
2. `cd backend && uv run pytest --collect-only tests/integration/test_notifications_api.py -q` — 12件収集
3. basename一意確認(運用ルール5): `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空
4. `git diff main -- backend/alembic backend/pyproject.toml backend/uv.lock Makefile` が空
5. 実時間参照検査(§6-6のrgコマンド)
6. **スーパーバイザー検証時**(報告書には記入しない・結果はスーパーバイザーがSTATUSへ): `docker compose build api` → `make test-ci`(期待1342件)→ 残存確認:
   ```sql
   SELECT COUNT(*) FROM users WHERE auth_subject LIKE 'm3ws3-%';
   SELECT COUNT(*) FROM intents WHERE user_id IN
     (SELECT id FROM users WHERE auth_subject LIKE 'm3ws3-%');
   SELECT COUNT(*) FROM latches WHERE intent_ids && ARRAY(
     SELECT id FROM intents WHERE user_id IN
     (SELECT id FROM users WHERE auth_subject LIKE 'm3ws3-%'));
   SELECT COUNT(*) FROM latch_status_events WHERE latch_id IN
     (SELECT id FROM latches WHERE intent_ids && ARRAY(
     SELECT id FROM intents WHERE user_id IN
     (SELECT id FROM users WHERE auth_subject LIKE 'm3ws3-%')));
   SELECT COUNT(*) FROM notifications WHERE user_id IN
     (SELECT id FROM users WHERE auth_subject LIKE 'm3ws3-%');
   ```
   すべて0件であること。

---

## 8. 実装ステップ(TDD。Task 1〜11の順で実行する)

### Task 1: notifications.types + templates(FR-21の構造担保)

**Files:**
- Create: `backend/src/latch/notifications/__init__.py`(空の雛形docstringのみ)
- Create: `backend/src/latch/notifications/types.py`
- Create: `backend/src/latch/notifications/templates.py`
- Test: `backend/tests/unit/notifications/test_templates.py`

**Interfaces:**
- Produces: `NOTIFICATION_PROPOSAL == "proposal"`・`NOTIFICATION_NEARBY == "nearby_candidate"`・`NOTIFICATION_ATTENDANCE_REQUEST == "attendance_request"`(latch/sender.py は文字列リテラルのみで参照)。`PUSH_TITLE`・`PUSH_BODY_LATCH`・`PUSH_BODY_NOTICE`(str定数)・`build_push(notification_type: str) -> tuple[str, str]`(未知typeはValueError。Task 2のStubPushSender・Task 10のintegration試験が使う)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/notifications/test_templates.py` を新規作成(ディレクトリに `__init__.py` は置かない):

```python
"""プッシュ文面テンプレートのunit試験(M3 ws-3 design §2.4・§4.1-1)。

FR-21(03 §4・08 §2.6)の対: 文言定数の全文ピン(恒久固定)と、
build_pushがtypeのみから固定文言へ写像すること(構造担保)を検証する。
"""

import pytest

from latch.notifications.templates import (
    PUSH_BODY_LATCH,
    PUSH_BODY_NOTICE,
    PUSH_TITLE,
    build_push,
)
from latch.notifications.types import (
    NOTIFICATION_ATTENDANCE_REQUEST,
    NOTIFICATION_NEARBY,
    NOTIFICATION_PROPOSAL,
)


def test_push_constants_full_text():
    """3定数の全文ピン(design §2.4初期値・03 §4様式)。"""
    assert PUSH_TITLE == "LATCH"
    assert PUSH_BODY_LATCH == "LATCH候補があります。\n詳細はアプリでご確認ください。"
    assert (
        PUSH_BODY_NOTICE == "LATCHからのお知らせがあります。\n詳細はアプリでご確認ください。"
    )


def test_build_push_maps_all_types():
    """全typeが(title, body)を返す。proposal/nearbyは同一文言(§2.4承認②)。"""
    latch_text = (PUSH_TITLE, PUSH_BODY_LATCH)
    assert build_push(NOTIFICATION_PROPOSAL) == latch_text
    assert build_push(NOTIFICATION_NEARBY) == latch_text
    assert build_push(NOTIFICATION_ATTENDANCE_REQUEST) == (
        PUSH_TITLE,
        PUSH_BODY_NOTICE,
    )


def test_build_push_unknown_type_raises():
    """未知typeはValueError(fail-fast・握らない — design §2.7)。"""
    with pytest.raises(ValueError, match="notification_type"):
        build_push("unknown_type")


def test_build_push_output_has_no_condition_summary():
    """戻り値に条件サマリ語彙が現れない(FR-21・引用#1)。

    build_pushの戻り値は定数のみを通り得る。proposal語彙(time_summary・
    area_name・category・headcount・budget・match_level等)が将来の変更で
    混入した場合、この試験が検知する。
    """
    forbidden = (
        "time_summary",
        "area_name",
        "category",
        "headcount",
        "budget",
        "match_level",
        "summary_only",
        "hidden_until_match",
        "drinking",
        "meal",
        "焼肉",
        "天文館",
    )
    for ntype in (
        NOTIFICATION_PROPOSAL,
        NOTIFICATION_NEARBY,
        NOTIFICATION_ATTENDANCE_REQUEST,
    ):
        title, body = build_push(ntype)
        for word in forbidden:
            assert word not in title, (ntype, word)
            assert word not in body, (ntype, word)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/notifications/test_templates.py -v`
Expected: 全4試験 FAIL with "ModuleNotFoundError: No module named 'latch.notifications'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/notifications/__init__.py` を作成(Task 9で公開IFを完成させる雛形):

```python
"""通知ドメイン(M3 ws-3)。プッシュ媒体(スタブ)とアプリ内通知(お知らせ)API。"""
```

`backend/src/latch/notifications/types.py` を作成:

```python
"""通知type定数の正本(M3 ws-3 design §3.1)。

latch_engine(NOTIFICATION_PROPOSAL/NEARBY)とsweeper
(NOTIFICATION_ATTENDANCE_REQUEST)がimport切替で参照する単一ソース。
値はws-6/ws-2実装時の文字列と同一(既存試験の文字列リテラルと無干渉)。
"""

NOTIFICATION_PROPOSAL = "proposal"
NOTIFICATION_NEARBY = "nearby_candidate"
NOTIFICATION_ATTENDANCE_REQUEST = "attendance_request"
```

`backend/src/latch/notifications/templates.py` を作成:

```python
"""プッシュ文面テンプレート(M3 ws-3 design §2.4案A)。

build_pushの引数はnotification_typeのみ(03 §4・08 §2.6 FR-21の構造担保)。
proposal/latchのデータ(時間・地域・人数・カテゴリ・予算・スコア)を
受け取る引数は存在しない — 条件サマリを本文へ混ぜる経路が型の上で
存在しない。文言の変更はこの定数のみ(A/B対象「通知文」の変更に構造が
引かれない・05 §2)。
"""

from latch.notifications.types import (
    NOTIFICATION_ATTENDANCE_REQUEST,
    NOTIFICATION_NEARBY,
    NOTIFICATION_PROPOSAL,
)

PUSH_TITLE = "LATCH"
PUSH_BODY_LATCH = "LATCH候補があります。\n詳細はアプリでご確認ください。"
PUSH_BODY_NOTICE = "LATCHからのお知らせがあります。\n詳細はアプリでご確認ください。"


def build_push(notification_type: str) -> tuple[str, str]:
    """type→(title, body)の写像(design §2.4)。

    proposal/nearby_candidateは同一文言(文面を分けると「閾値未満の候補
    である」ことがOS経路〔ロック画面等〕に漏れるため・承認②)。
    attendance_requestは第二汎用文(08 §2.6趣旨の準用・承認①)。
    未知typeはValueError(実装バグの即時顕在化・design §2.7)。
    """
    if notification_type in (NOTIFICATION_PROPOSAL, NOTIFICATION_NEARBY):
        return PUSH_TITLE, PUSH_BODY_LATCH
    if notification_type == NOTIFICATION_ATTENDANCE_REQUEST:
        return PUSH_TITLE, PUSH_BODY_NOTICE
    raise ValueError(f"unknown notification_type: {notification_type!r}")
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/notifications/test_templates.py -v`
Expected: PASS(4件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/notifications/__init__.py backend/src/latch/notifications/types.py backend/src/latch/notifications/templates.py backend/tests/unit/notifications/test_templates.py
git commit -m "feat: notification type constants and push templates (FR-21 structural guard)"
```

### Task 2: settings 2キー + test_settings.py追従(senderが依存する前提を先に用意)

**Files:**
- Modify: `backend/src/latch/settings.py`(ファイル末尾・reset_retry_secの後)
- Modify: `backend/tests/unit/test_settings.py`(test_settings_defaults へ2 assert追記)

**Interfaces:**
- Produces: `Settings.push_mode: str = "stub"`・`Settings.push_stub_delay_ms: int = 0`(Task 3のbuild_push_sender・Task 6のworker/main.pyが使う)

- [ ] **Step 1: 失敗するテストを書く(既存試験への機械的追記)**

`backend/tests/unit/test_settings.py` の `test_settings_defaults` へ、既存のassert群(reset_retry_secの行)の後に追記:

```python
    assert s.push_mode == "stub"  # M3 ws-3(design §3.2・realはG3後)
    assert s.push_stub_delay_ms == 0  # M3 ws-3(スタブ遅延注入)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/test_settings.py -v`
Expected: test_settings_defaults が FAIL with "AttributeError: 'Settings' object has no attribute 'push_mode'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/settings.py` の末尾(reset_retry_secの後)へ追記:

```python
    # --- プッシュ通知(M3 ws-3・design §3.2)---
    # "stub": ドライラン(送信内容を構造化ログlatch.push.sendへ記録のみ)。
    # "real"(実FCM・FirebasePushSender)はG3後。未実装値の指定は
    # build_push_senderがValueError(静かにスタブへ落ちない — llm_mode規律)
    push_mode: str = "stub"
    # スタブの遅延注入ms(10 第1節レイテンシ注入・llm_stub_delay_*と同型)
    push_stub_delay_ms: int = 0
```

- [ ] **Step 4: テストが通ることを確認(影響範囲の確認を含む)**

Run: `cd backend && uv run pytest tests/unit/test_settings.py tests/unit/llm/test_llm_factory.py -v`
Expected: 全PASS。**llm 9項目ピン(test_llm_settings_are_exactly_nine_fields)は `f.startswith("llm_")` での機械検査のためpush_*プレフィックスと無干渉**(design §3.2・緑維持をここで確認)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/settings.py backend/tests/unit/test_settings.py
git commit -m "feat: push_mode and push_stub_delay_ms settings"
```

### Task 3: records + sender(StubPushSender・ドライラン記録)

**Files:**
- Create: `backend/src/latch/notifications/records.py`
- Create: `backend/src/latch/notifications/sender.py`
- Test: `backend/tests/unit/notifications/test_push_sender.py`

**Interfaces:**
- Consumes: Task 1の `build_push`・Task 2の `Settings.push_mode`/`push_stub_delay_ms`
- Produces: `PushSendRecord`(pydantic・8フィールド)・`SendStatus`・`push_log(record)`(ロガー`latch.push.send`)・`PushSender`(Protocol・`name: str`・`async send(*, user_id, notification_type, latch_id) -> None`)・`StubPushSender(clock, delay_ms, timeout_s, fail)`・`build_push_sender(clock, settings)`・`PUSH_TIMEOUT_S = 3.0`(Task 4〜6・Task 10が使う)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/notifications/test_push_sender.py` を作成:

```python
"""StubPushSenderとbuild_push_senderのunit試験(M3 ws-3 design §2.2〜2.3・§4.1-2)。"""

import json
import logging
from datetime import UTC, datetime

import pytest

from latch.core.clock import FakeClock
from latch.notifications.records import LOGGER_NAME, PushSendRecord
from latch.notifications.sender import (
    PUSH_TIMEOUT_S,
    StubPushSender,
    build_push_sender,
)
from latch.notifications.types import (
    NOTIFICATION_ATTENDANCE_REQUEST,
    NOTIFICATION_PROPOSAL,
)
from latch.settings import Settings

NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
USER = "00000000-0000-4000-8000-0000000000c1"
LATCH = "00000000-0000-4000-8000-0000000000a1"


def _records(caplog) -> list[dict]:
    return [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == LOGGER_NAME
    ]


async def test_stub_send_ok_record_full_fields(caplog):
    """ok記録の全フィールド(design §2.3)。本文=テンプレート定数。"""
    sender = StubPushSender(clock=FakeClock(NOW))
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await sender.send(
            user_id=USER, notification_type=NOTIFICATION_PROPOSAL, latch_id=LATCH
        )
    (rec,) = _records(caplog)
    assert rec == {
        "occurred_at": NOW.isoformat(),
        "user_id": USER,
        "notification_type": "proposal",
        "latch_id": LATCH,
        "title": "LATCH",
        "body": "LATCH候補があります。\n詳細はアプリでご確認ください。",
        "status": "ok",
        "error_code": None,
    }


async def test_stub_send_short_delay_within_timeout_ok(caplog):
    """delay<timeout → ok(レイテンシ注入・LLMスタブと同型)。"""
    sender = StubPushSender(clock=FakeClock(NOW), delay_ms=10, timeout_s=5.0)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await sender.send(
            user_id=USER,
            notification_type=NOTIFICATION_ATTENDANCE_REQUEST,
            latch_id=LATCH,
        )
    (rec,) = _records(caplog)
    assert rec["status"] == "ok"
    assert rec["body"] == "LATCHからのお知らせがあります。\n詳細はアプリでご確認ください。"


async def test_stub_send_delay_beyond_timeout_records_timeout(caplog):
    """delay>timeout → status=timeout・error_code=PushTimeoutError(asyncio.timeoutが
    即打ち切りするため試験は速い・design §2.2)。"""
    sender = StubPushSender(clock=FakeClock(NOW), delay_ms=5000, timeout_s=0.01)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await sender.send(
            user_id=USER, notification_type=NOTIFICATION_PROPOSAL, latch_id=LATCH
        )
    (rec,) = _records(caplog)
    assert rec["status"] == "timeout"
    assert rec["error_code"] == "PushTimeoutError"


async def test_stub_send_failure_records_error_and_swallows(caplog):
    """送出例外(fail差し替え)→ error記録+握る(呼び出し元に例外が出ない・§2.7)。"""
    sender = StubPushSender(clock=FakeClock(NOW), fail=True)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        # 例外が送出されたらこの試験自体が落ちる=握りの証明
        await sender.send(
            user_id=USER, notification_type=NOTIFICATION_PROPOSAL, latch_id=LATCH
        )
    (rec,) = _records(caplog)
    assert rec["status"] == "error"
    assert rec["error_code"] == "RuntimeError"


async def test_stub_send_unknown_type_raises_value_error():
    """未知typeはValueError=fail-fast(握らない・design §2.7)。記録型は出ない。"""
    sender = StubPushSender(clock=FakeClock(NOW))
    with pytest.raises(ValueError, match="notification_type"):
        await sender.send(
            user_id=USER, notification_type="bogus", latch_id=LATCH
        )


def test_push_timeout_default_is_three_seconds():
    """PUSH_TIMEOUT_S=3.0(コード定数・層別予算Layer5+通知≤2秒はp95目標で
    timeoutは上限・design §2.2)。"""
    assert PUSH_TIMEOUT_S == 3.0


def test_build_push_sender_stub_uses_settings():
    """push_mode=stub → StubPushSender(delayはsettings反映・§9-5)。"""
    sender = build_push_sender(
        FakeClock(NOW), Settings(push_mode="stub", push_stub_delay_ms=7)
    )
    assert isinstance(sender, StubPushSender)
    assert sender.name == "stub"
    assert sender._delay_ms == 7


@pytest.mark.parametrize("mode", ["real", "production"])
def test_build_push_sender_rejects_unsupported_mode(mode):
    """未実装値はValueError(静かにスタブへ落ちない — llm_modeと同一規律)。"""
    with pytest.raises(ValueError, match="push_mode"):
        build_push_sender(FakeClock(NOW), Settings(push_mode=mode))
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/notifications/test_push_sender.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'latch.notifications.records'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/notifications/records.py` を作成:

```python
"""プッシュ送信記録(M3 ws-3 design §2.3)。

LLMのSendRecord(08 §3「内容を含まない」)と異なり、本文を含む — 10 §1が
「プッシュ本文が汎用文であること」を記録で検証することを要求するため。
本文はtemplates.pyの固定定数のみを通り得るため機微は入り得ない
(許可リスト方式・08 §2.4の型固定は踏襲)。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

LOGGER_NAME = "latch.push.send"

SendStatus = Literal["ok", "timeout", "error"]


class PushSendRecord(BaseModel):
    """ドライラン送信記録1件(design §2.3)。出力フィールドをこの型で固定。"""

    occurred_at: datetime  # Clock.now()(tz-aware UTC)
    user_id: str  # 宛先ユーザー
    notification_type: str  # proposal / nearby_candidate / attendance_request
    latch_id: str  # 参照先
    title: str  # テンプレート定数
    body: str  # テンプレート定数(汎用文)
    status: SendStatus
    error_code: str | None = None  # 例外IDのみ(08 §2.4)


def push_log(record: PushSendRecord) -> None:
    """ロガー 'latch.push.send' へJSON 1行で出力(latch.llm.sendと同型)。"""
    logging.getLogger(LOGGER_NAME).info(record.model_dump_json())
```

`backend/src/latch/notifications/sender.py` を作成:

```python
"""プッシュ送信(M3 ws-3 design §2.2)。

PushSenderはユーザ単位の抽象(トークン解決はreal実装の内部)。StubPushSender
はネットワーク呼び出しを一切行わないドライラン: build_pushの文言で
PushSendRecord(status=ok)を出す。実FCM(FirebasePushSender)はG3後の
"real"実装(Admin SDKのsend_each_async/dry_run形を模倣したIF・design §2.2)。
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from latch.core.clock import Clock
from latch.notifications.records import PushSendRecord, SendStatus, push_log
from latch.notifications.templates import build_push
from latch.settings import Settings

PUSH_TIMEOUT_S = 3.0  # design §2.2(コード定数・unit試験で短縮注入)


class PushSender(Protocol):
    """送信IF(design §2.2)。例外を出さない(記録して握る)。戻り値なし。"""

    name: str

    async def send(
        self, *, user_id, notification_type: str, latch_id
    ) -> None: ...


class StubPushSender:
    """テスト/ドライラン用スタブ(10 第1節)。name="stub"。

    delay_ms>0なら asyncio.sleep を挟む(レイテンシ注入 — LLMスタブの
    _apply_delayと同型・待機であり時刻参照ではない)。timeout打ち切りは
    asyncio.timeout(gateway._callと同型)。fail=Trueなら送出例外を発生させ
    error記録の経路試験に使う(§4.1-2)。
    """

    def __init__(
        self,
        *,
        clock: Clock,
        delay_ms: int = 0,
        timeout_s: float = PUSH_TIMEOUT_S,
        fail: bool = False,
    ) -> None:
        self.name = "stub"
        self._clock = clock
        self._delay_ms = delay_ms
        self._timeout_s = timeout_s
        self._fail = fail

    async def send(
        self, *, user_id, notification_type: str, latch_id
    ) -> None:
        """ドライラン送信。例外を出さない(§2.1)。ただし未知typeの
        ValueError(fail-fast)は握らない(§2.7)。"""
        title, body = build_push(notification_type)
        occurred_at = self._clock.now()
        status: SendStatus = "ok"
        error_code: str | None = None
        try:
            async with asyncio.timeout(self._timeout_s):
                if self._delay_ms > 0:
                    # 待機であり時刻参照ではない(arch test禁止対象外)
                    await asyncio.sleep(self._delay_ms / 1000)
                if self._fail:
                    raise RuntimeError("stub push failure (injected)")
        except TimeoutError:
            status = "timeout"
            error_code = "PushTimeoutError"
        except Exception as exc:  # 送信側例外は握る(§2.7)
            status = "error"
            error_code = type(exc).__name__
        push_log(
            PushSendRecord(
                occurred_at=occurred_at,
                user_id=str(user_id),
                notification_type=notification_type,
                latch_id=str(latch_id),
                title=title,
                body=body,
                status=status,
                error_code=error_code,
            )
        )


def build_push_sender(clock: Clock, settings: Settings) -> PushSender:
    """設定からPushSenderを構築(worker/main.py配線用・design §2.2)。

    "real"(実FCM)はG3後。未実装値はValueError — 静かにスタブへ落ちない
    規律(llm_modeのbuild_llm_gatewayと同一)。
    """
    if settings.push_mode == "stub":
        return StubPushSender(
            clock=clock, delay_ms=settings.push_stub_delay_ms
        )
    raise ValueError(
        f"unknown push_mode: {settings.push_mode!r} "
        "('stub' only — 'real' arrives after G3)"
    )
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/notifications/test_push_sender.py -v`
Expected: PASS(9件・`test_build_push_sender_rejects_unsupported_mode` はparametrize 2ケース)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/notifications/records.py backend/src/latch/notifications/sender.py backend/tests/unit/notifications/test_push_sender.py
git commit -m "feat: PushSender protocol and StubPushSender with dry-run records"
```

### Task 4: latch_engine へのPushSender注入(import切替・tx後送信)

**Files:**
- Modify: `backend/src/latch/worker/matching/latch_engine.py`(import節・31〜32行・547行ctor・691行_nearby_in_tx・746行try_promote・_evaluate_pair)
- Test: `backend/tests/unit/matching/test_latch_engine.py`(248行 `_engine` ヘルパー拡張+ファイル末尾へ4件追記)

**Interfaces:**
- Consumes: Task 1の `NOTIFICATION_NEARBY`/`NOTIFICATION_PROPOSAL`(import切替・**latch_engineモジュールから同名で再参照し続けるため既存の `from latch.worker.matching.latch_engine import NOTIFICATION_PROPOSAL` を壊さない**)・Task 3の `PushSender`
- Produces: `LatchEngine(engine=..., clock=..., geo=..., push=None)`(キーワード引数push・None許容)・`LatchEngine._nearby_in_tx(...) -> tuple[uuid.UUID | None, list[uuid.UUID]]`(戻り値変更・Task 10 integrationが直接呼ばない・単体試験のみ)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/matching/test_latch_engine.py` の248行の `_engine` を拡張:

```python
def _engine(clock=None, geo=None, push=None) -> LatchEngine:
    return LatchEngine(
        engine=_FakeEngine(), clock=clock or FakeClock(NOW), geo=geo, push=push
    )
```

ファイル末尾へ追記:

```python
# --- M3 ws-3: PushSender注入(design §2.1案A・§4.1-5) ---


class _RecordingPush:
    """送信呼び出しを記録するスタブ(design §4.1-5)。"""

    name = "fake"

    def __init__(self):
        self.calls: list[tuple] = []

    async def send(self, *, user_id, notification_type, latch_id):
        self.calls.append((user_id, notification_type, latch_id))


async def test_promote_sends_push_to_notify_participants(monkeypatch):
    """proposed遷移+通知書き込み(tx内)→tx後にnotify全員へ送信(2名=2件)。"""
    log = _patch_promote(monkeypatch, row=_latch_row())
    push = _RecordingPush()
    await _engine(push=push).try_promote(LID)
    assert log["promote"]  # proposed遷移が起きた前提
    assert push.calls == log["notifications"]  # 通知書き込みと同一対象・同一順
    assert {u for u, _, _ in push.calls} == {
        _participant(1).user_id,
        _participant(101).user_id,
    }
    assert all(t == NOTIFICATION_PROPOSAL for _, t, _ in push.calls)


async def test_promote_push_skips_muted_participant(monkeypatch):
    """片方muted → pushはnon-muted側1件のみ(引用#6・Review Focus 3)。"""
    parts = [
        _participant(1),
        _participant(101, notification_level="muted"),
    ]
    log = _patch_promote(monkeypatch, row=_latch_row(), parts=parts)
    push = _RecordingPush()
    await _engine(push=push).try_promote(LID)
    assert push.calls == [(_participant(1).user_id, NOTIFICATION_PROPOSAL, LID)]
    assert log["notifications"] == push.calls


async def test_promote_daily_limit_candidate_keeps_no_push(monkeypatch):
    """日次上限到達 → candidate保留・notifications 0件・push 0件
    (Review Focus 2: 早期returnでは1件も送らない)。"""
    daily = {
        _participant(1).user_id: 6,
        _participant(101).user_id: 6,
    }
    log = _patch_promote(monkeypatch, row=_latch_row(), daily=daily)
    push = _RecordingPush()
    await _engine(push=push).try_promote(LID)
    assert log["promote"] == []
    assert log["notifications"] == []
    assert push.calls == []


async def test_nearby_push_sent_to_nearby_also_only(monkeypatch):
    """nearby存在通知のプッシュはnearby_also側のみ(txコミット後・承認②)。"""
    row = _row(1, wa=0.5, wb=0.5)
    inputs = {
        _uid(1): _inputs(1, notification_level="nearby_also"),
        _uid(101): _inputs(101, notification_level="muted"),
    }
    log = _patch(monkeypatch, org=_origin(1), rows=[row], inputs=inputs)
    push = _RecordingPush()
    await _engine(push=push).handle(_uid(1))
    assert log["notifications"] == [
        (_inputs(1).user_id, NOTIFICATION_NEARBY, NEW_LATCH_ID)
    ]
    assert push.calls == log["notifications"]
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/matching/test_latch_engine.py -v -k push`
Expected: 4件 FAIL with "TypeError: LatchEngine.__init__() got an unexpected keyword argument 'push'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/worker/matching/latch_engine.py` を変更する。

(1) 31〜32行の定数定義を削除し、importへ切替(既存の `from latch.` 系importのまとまりへ追加。**モジュール属性として同名が残るため既存importは無傷**):

```python
# 削除する2行:
# NOTIFICATION_PROPOSAL = "proposal"
# NOTIFICATION_NEARBY = "nearby_candidate"

# import節へ追加(latch系importのまとまり):
from latch.notifications.types import NOTIFICATION_NEARBY, NOTIFICATION_PROPOSAL
```

(2) TYPE_CHECKING import(既存のimport節へ追加 — latch_engine.py は現在typing importがないため `from dataclasses import dataclass` の行の後に新規追加。`from __future__ import annotations` がファイル冒頭にあるためアノテーションは評価されない):

```python
from typing import TYPE_CHECKING

from latch.core.clock import Clock
from latch.notifications.types import NOTIFICATION_NEARBY, NOTIFICATION_PROPOSAL
from latch.worker.matching import group_calc, latch_calc, layer4
from latch.worker.matching import origin as origin_mod
from latch.worker.matching import proposal as proposal_mod
from latch.worker.matching.proposal import LatchIntentInputs

if TYPE_CHECKING:
    from latch.notifications.sender import PushSender

logger = logging.getLogger(__name__)
```

(import節は `from latch.core.clock import Clock` → `from latch.notifications.types import ...` → 既存の `from latch.worker.matching ...` の順(ruff I001のisort順・latch.notifications は latch.core と latch.worker の間))

(3) ctor(547行)へpush引数を追加:

```python
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        geo=None,
        push: "PushSender | None" = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._geo = geo  # GeoService | None(Noneならarea_name=None)
        self._push = push  # PushSender | None(未注入ならno-op・design §2.7)
```

(4) `_send_pushes` ヘルパーをクラスへ追加(`drain` メソッドの直前あたり):

```python
    async def _send_pushes(
        self, targets: list[tuple[uuid.UUID, str, uuid.UUID]]
    ) -> None:
        """txコミット後のプッシュ送信(design §2.1案A・§2.7)。

        未注入(既存試験構成)はno-op(_kick_embeddingと同型)。送信例外は
        sender側で握るためここからは伝播しない。
        """
        if self._push is None:
            return
        for user_id, ntype, lid in targets:
            await self._push.send(
                user_id=user_id, notification_type=ntype, latch_id=lid
            )
```

(5) try_promote(746行): tx内で送信対象を集め、txブロック外(関数末尾)で送信。関数全体の形:

```python
    async def try_promote(self, latch_id: uuid.UUID) -> None:
        """提示判定(1tx・design §2.6手順1〜9・Review Focus 4の手順順序)。

        (既存docstringを保持。末尾へ1行追記:)
        M3 ws-3: 通知対象はtx内で収集しtxコミット後にプッシュ送信(§2.1案A)。
        """
        pending: list[tuple[uuid.UUID, str, uuid.UUID]] = []
        async with self._engine.begin() as conn:
            # ...(行ロック〜_promote_latch・イベント挿入までは変更なし)...
            for p in notify:
                await _insert_notification(
                    conn, p.user_id, NOTIFICATION_PROPOSAL, latch_id, now
                )
                pending.append((p.user_id, NOTIFICATION_PROPOSAL, latch_id))
        # txコミット後のみ送信(§2.1: コミット前に送るとnotifications行が
        # ないままプッシュが飛び得る)。早期return(candidate保留・競合負け)
        # はpending空のままtxを抜けるため送信されない。
        await self._send_pushes(pending)
```

(async with ブロックの中身〔行ロック・75分ルール・D-06・D-05・D-08判定・条件付きUPDATE・イベント挿入〕は1行も変更しない。変更は `pending` の初期化行・forループへの `pending.append` 1行・tx後の `await self._send_pushes(pending)` のみ)

(6) _evaluate_pair: nearby分岐の戻り値受取とtx後送信。txブロック前に初期化を追加:

```python
        latch_id: uuid.UUID | None = None
        nearby_latch_id: uuid.UUID | None = None
        notified_users: list[uuid.UUID] = []
        async with self._engine.begin() as conn:
```

nearby分岐(else節・676行)の呼び出しで戻り値を受ける:

```python
            else:
                # nearbyはtry_promoteしない(candidateのまま・引用#9)
                nearby_latch_id, notified_users = await self._nearby_in_tx(
                    conn,
                    a_id=a_id,
                    b_id=b_id,
                    score=score,
                    origin_inputs=origin_inputs,
                    peer_inputs=peer_inputs,
                    deadline0=deadline0,
                    min_expires=min_expires,
                    has_no=has_no,
                    now=now,
                )
```

関数末尾(tx後・`if latch_id is not None:` の後)へ:

```python
        if latch_id is not None:
            await self.try_promote(latch_id)
        if nearby_latch_id is not None:
            # 存在通知の送信もtxコミット後(design §2.1案A・§3.2④)
            await self._send_pushes(
                [
                    (uid, NOTIFICATION_NEARBY, nearby_latch_id)
                    for uid in notified_users
                ]
            )
```

(7) _nearby_in_tx(691行): 戻り値型とreturn文を変更:

```python
    async def _nearby_in_tx(
        self,
        conn,
        *,
        a_id,
        b_id,
        score,
        origin_inputs,
        peer_inputs,
        deadline0,
        min_expires,
        has_no,
        now,
    ) -> tuple[uuid.UUID | None, list[uuid.UUID]]:
        """閾値未満: nearby_also参加者への存在通知(§2.7・引用#9・集約tx内)。

        戻り値は(latch_id, 通知済みuser_id群)。新規INSERT成功かつ日次上限内
        の場合のみ通知済みリストが非空(開いている行あり/対象なしは(None, []))。
        nearbyはtry_promoteしない(candidateのまま)。M3 ws-3: 通知済みuser_id群は
        呼び出し元がtx後にプッシュ送信へ使う(design §3.2④)。
        """
        notify_targets = [
            inp
            for inp in (origin_inputs, peer_inputs)
            if inp.notification_level == "nearby_also"
        ]
        if not notify_targets:
            return None, []
        if has_no:
            return None, []  # 存在通知も出さない(D-07の一貫適用・設計確定)
        latch_id = await _insert_latch(
            conn,
            a_id=a_id,
            b_id=b_id,
            proposal=proposal_mod.nearby_proposal(),
            score=score,
            deadline=deadline0,
            expires=min_expires,
            now=now,
        )
        if latch_id is None:
            return None, []  # 開いている行あり・閾値未満評価では既存行を更新しない
        await _insert_latch_event(conn, latch_id, None, "candidate", None, now)
        day_start = layer4.jst_day_start(self._clock.jst_date())
        day_next = day_start + timedelta(days=1)
        counts = await _count_daily_notifications(
            conn, [u.user_id for u in notify_targets], day_start, day_next
        )
        notified: list[uuid.UUID] = []
        for u in notify_targets:
            if counts.get(u.user_id, 0) < latch_calc.D08_DAILY_LIMIT:
                await _insert_notification(
                    conn, u.user_id, NOTIFICATION_NEARBY, latch_id, now
                )
                notified.append(u.user_id)
            else:
                logger.info("latch nearby daily limit uid=%s", u.user_id)
        return latch_id, notified
```

- [ ] **Step 4: テストが通ることを確認(既存試験の無傷を含む)**

Run: `cd backend && uv run pytest tests/unit/matching/test_latch_engine.py -v`
Expected: 全PASS(既存全試験+新規4件。**design §5-7の実装時確認**: 既存試験は `NOTIFICATION_PROPOSAL`/`NOTIFICATION_NEARBY` をlatch_engineからimport(再参照)・`"proposal"` 等の文字列リテラル・661〜662行の値ピン — いずれも定数値不変のため無傷。報告書に記録する)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/worker/matching/latch_engine.py backend/tests/unit/matching/test_latch_engine.py
git commit -m "feat: wire PushSender into LatchEngine (post-commit send)"
```

### Task 5: sweeper へのPushSender注入(attendance通知の送信)

**Files:**
- Modify: `backend/src/latch/worker/sweeper.py`(31行・194行ctor・293行_complete_latch)
- Test: `backend/tests/unit/worker/test_sweeper.py`(`_sweeper` ヘルパー拡張+attendance送信2件追記)

**Interfaces:**
- Consumes: Task 1の `NOTIFICATION_ATTENDANCE_REQUEST`(import切替・**sweeperモジュールから同名で再参照**)・Task 3の `PushSender`
- Produces: `ExpirySweeper(engine=..., clock=..., latch=..., batch_limit=..., push=None)`(キーワード引数push・None許容・デフォルトNoneのため既存呼び出し〔test_expiry_batches.py・test_worker_reeval.py・worker/main.py〕は無傷)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/worker/test_sweeper.py` の `_sweeper` ヘルパー(207行付近)を拡張:

```python
def _sweeper(engine=None, clock=None, latch=None, batch_limit=50, push=None):
    return ExpirySweeper(
        engine=engine or object(),  # 抽出関数はmonkeypatchで差し替え
        clock=clock or FakeClock(NOW),
        latch=latch or RecordingLatch(),
        batch_limit=batch_limit,
        push=push,
    )
```

「9. _complete_latch」節(test_complete_latch_no_row_no_side_effects の後)へ追記:

```python
class _RecordingPush:
    """送信呼び出しを記録するスタブ(M3 ws-3・design §4.1)。"""

    name = "fake"

    def __init__(self):
        self.calls: list[tuple] = []

    async def send(self, *, user_id, notification_type, latch_id):
        self.calls.append((user_id, notification_type, latch_id))


async def test_complete_latch_sends_attendance_push_after_commit():
    """tx後・参加者全員へattendanceプッシュ(第二汎用文・design §2.1案A)。"""
    conn = FakeConn(
        {
            _COMPLETE_LATCH: [([INTENT1, INTENT2],)],
            _SELECT_LATCH_PARTICIPANT_USERS: [(USER1,), (USER2,)],
        }
    )
    push = _RecordingPush()
    sw = _sweeper(engine=FakeEngine(conn), push=push)
    assert await sw._complete_latch(LATCH1, NOW) is True
    assert push.calls == [
        (USER1, sweeper_mod.NOTIFICATION_ATTENDANCE_REQUEST, LATCH1),
        (USER2, sweeper_mod.NOTIFICATION_ATTENDANCE_REQUEST, LATCH1),
    ]


async def test_complete_latch_push_not_injected_is_noop():
    """push未注入(既存試験構成)→通知書き込みは通常どおり・送信なし。"""
    conn = FakeConn(
        {
            _COMPLETE_LATCH: [([INTENT1],)],
            _SELECT_LATCH_PARTICIPANT_USERS: [(USER1,)],
        }
    )
    sw = _sweeper(engine=FakeEngine(conn))  # push未渡し=None
    assert await sw._complete_latch(LATCH1, NOW) is True
    notified = [c for c in conn.calls if c[0] is _INSERT_ATTENDANCE_NOTIFICATION]
    assert len(notified) == 1  # 通知書き込み(tx内)は影響なし
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/worker/test_sweeper.py -v -k push`
Expected: 2件 FAIL with "TypeError: ExpirySweeper.__init__() got an unexpected keyword argument 'push'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/worker/sweeper.py` を変更する。

(1) 31行の定数定義を削除しimportへ切替(import節へ追加・**モジュール属性として同名が残る**):

```python
# 削除:
# NOTIFICATION_ATTENDANCE_REQUEST = "attendance_request"

# import節へ追加:
from latch.notifications.types import NOTIFICATION_ATTENDANCE_REQUEST
```

(2) TYPE_CHECKING import(import節へ追加。`from typing import TYPE_CHECKING` は `import uuid` の後あたりへ。`from __future__ import annotations` があるためアノテーションは評価されない):

```python
from typing import TYPE_CHECKING

from latch.intents.events import EVENT_EXPIRED, insert_match_event
from latch.notifications.types import NOTIFICATION_ATTENDANCE_REQUEST

if TYPE_CHECKING:
    from latch.notifications.sender import PushSender

logger = logging.getLogger(__name__)
```

(latch.notifications.types は latch.intents.events の直後 — isort順)

(3) ctor(194行)へpush引数:

```python
    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock,  # Clock(既存のClock型・core.clock)
        latch,  # LatchEngine(drain呼び出し・design §2.7)
        batch_limit: int,
        push: "PushSender | None" = None,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._latch = latch
        self._batch_limit = batch_limit
        self._push = push  # PushSender | None(未注入ならno-op・design §2.7)
        self._last_tick: datetime = clock.now()  # 起動時刻(§2.7)
```

(4) _complete_latch(293行)を送信対象収集+tx後送信へ変更:

```python
    async def _complete_latch(self, latch_id: uuid.UUID, now: datetime) -> bool:
        """行単位tx: completed化+イベント+実施自己申告の通知先行書き込み(D-09)。

        cancelled(解散済み)はstatus='matched'でなく対象外=通知を送らない
        (引用#15・構造的担保)。参加者はintents.user_idを行順に全員へ(§9-9)。
        M3 ws-3: プッシュ送信はtxコミット後(design §2.1案A)。
        """
        targets: list[uuid.UUID] = []
        async with self._engine.begin() as conn:
            res = await conn.execute(
                _COMPLETE_LATCH, {"latch_id": latch_id, "now": now}
            )
            row = res.first()
            if row is None:
                return False
            await conn.execute(
                _INSERT_LATCH_EVENT_SQL,
                {
                    "latch_id": latch_id,
                    "from_status": "matched",
                    "to_status": "completed",
                    "user_id": None,
                    "now": now,
                },
            )
            users = await conn.execute(
                _SELECT_LATCH_PARTICIPANT_USERS,
                {"ids": [_coerce_uuid(x) for x in row[0]]},
            )
            for (user_id,) in users.fetchall():
                await conn.execute(
                    _INSERT_ATTENDANCE_NOTIFICATION,
                    {
                        "user_id": _coerce_uuid(user_id),
                        "type": NOTIFICATION_ATTENDANCE_REQUEST,
                        "payload": json.dumps({"latch_id": str(latch_id)}),
                        "now": now,
                    },
                )
                targets.append(_coerce_uuid(user_id))
            logger.info("sweeper.completed latch_id=%s", latch_id)
        await self._send_attendance_pushes(latch_id, targets)
        return True

    async def _send_attendance_pushes(
        self, latch_id: uuid.UUID, targets: list[uuid.UUID]
    ) -> None:
        """txコミット後のattendanceプッシュ送信(design §2.1案A・§2.7)。

        未注入(既存試験構成)はno-op。送信例外はsender側で握る。
        """
        if self._push is None:
            return
        for user_id in targets:
            await self._push.send(
                user_id=user_id,
                notification_type=NOTIFICATION_ATTENDANCE_REQUEST,
                latch_id=latch_id,
            )
```

- [ ] **Step 4: テストが通ることを確認(既存試験の無傷を含む)**

Run: `cd backend && uv run pytest tests/unit/worker/ tests/unit/test_worker_reeval.py -v`
Expected: 全PASS(既存+新規2件。**design §5-7確認**: test_sweeper.pyの既存試験はSQL定数import・`"attendance_request"` 文字列リテラル比較であり定数import切替で無傷。test_worker_reeval.pyのsweeper注入試験はスタブsweeperを使うためctor変更の影響なし)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/worker/sweeper.py backend/tests/unit/worker/test_sweeper.py
git commit -m "feat: wire PushSender into ExpirySweeper attendance notifications"
```

### Task 6: worker/main.py 配線(build_push_sender→LatchEngine・ExpirySweeper)

**Files:**
- Modify: `backend/src/latch/worker/main.py`(import節・172行LatchEngine構築・194行ExpirySweeper構築)

**Interfaces:**
- Consumes: Task 3の `build_push_sender`・Task 4/5の `push` キーワード引数
- Produces: なし(配線のみ・Worker ctorへの引数追加は行わない — design §3.2の変更はbuild_push_senderで構築し注入、のみ。テストは部品単位で直接構成するためWorkerへの注入IFは不要)

- [ ] **Step 1: 実装(配線のみ・新規unit試験は追加しない)**

配線の正しさはTask 4/5の部品試験(push引数の挙動)+本ステップの静的検査で担保する(ws-2のworker/main.py配線と同一方針)。`backend/src/latch/worker/main.py` を変更:

import節へ追加:

```python
from latch.notifications.sender import build_push_sender
```

run()内・LatchEngine構築(172行付近)へpush構築と注入:

```python
            # PushSender(M3 ws-3・design §2.1案A): StubPushSender(ドライラン)。
            # LatchEngineとExpirySweeperの両方へ注入(notifications書き込みtxの
            # コミット直後に送信・失敗はsender側で握る)。real(実FCM)はG3後
            push = build_push_sender(self.clock, self.settings)
            # LatchEngine DI(M2 ws-6・design §2.1案A): JevWorker直後のLayer 5。
            # redis非依存のため常に構築(注入済み資産は再構築しない)
            if self._latch is None:
                self._latch = LatchEngine(
                    engine=engine,
                    clock=self.clock,
                    geo=GeoService(engine),
                    push=push,
                )
```

ExpirySweeper構築(194行付近)へpush注入:

```python
                sweeper = ExpirySweeper(
                    engine=engine,
                    clock=self.clock,
                    latch=self._latch,
                    batch_limit=self.settings.sweeper_batch_limit,
                    push=push,
                )
```

- [ ] **Step 2: 検査**

Run: `cd backend && uv run pytest tests/unit/test_worker.py tests/unit/test_worker_reeval.py -v && rg -n "build_push_sender|push=push" src/latch/worker/main.py`
Expected: 試験全PASS(worker試験はWorkerにpushを渡さない構成=未注入互換の緑維持)。rg出力に `build_push_sender(self.clock, self.settings)` と2箇所の `push=push` が含まれる

- [ ] **Step 3: コミット**

```bash
git add backend/src/latch/worker/main.py
git commit -m "feat: build PushSender in Worker and inject into engine and sweeper"
```

### Task 7: notifications/store.py(LEFT JOIN改頁・所有検査・既読)

**Files:**
- Create: `backend/src/latch/notifications/store.py`
- Test: `backend/tests/unit/notifications/test_notifications_service.py`(本Taskで作成・store検査3件。Task 8でservice試験を追記)

**Interfaces:**
- Consumes: なし(latches/intentsのstoreと同じく自前のusers解決SQLを持つ — パッケージ分離の慣行)
- Produces: `fetch_user_id(conn, provider, subject) -> uuid.UUID | None`・`NotificationPageRow`(dataclass)・`LatchJoinedRow`(dataclass)・`select_notifications_page(conn, *, me, before, limit) -> list[NotificationPageRow]`・`select_notification_owned(conn, *, me, notification_id) -> OwnedRow | None`・`mark_read(conn, *, me, notification_id, now) -> None`(Task 8のserviceが使う)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/notifications/test_notifications_service.py` を作成(store SQL検査のみ。service試験はTask 8で追記):

```python
"""notifications store/serviceのunit試験(M3 ws-3 design §4.1-3/4)。

store検査はlatches/test_latches_store_sql.pyの流儀(実dialect compileで
bind paramの部分認識ゼロ + 句ピン)。service検証はスタブstoreで分岐のみ。
"""

from sqlalchemy.dialects import postgresql

from latch.notifications import store


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_store_sql_bind_params_fully_recognized():
    """compiled文字列に未変換の ':name' が残らない(部分認識ゼロ)。"""
    for name in ("_SELECT_USER_ID", "_SELECT_NOTIFICATIONS_PAGE",
                 "_SELECT_NOTIFICATION_OWNED", "_MARK_READ"):
        compiled = _compiled(getattr(store, name))
        for token in (":provider", ":subject", ":me", ":ct", ":lid",
                      ":limit", ":nid", ":now"):
            assert token not in compiled, (name, token)


def test_select_page_pins_join_order_and_tiebreak():
    """一覧SQLの確定値(design §2.5): 本人絞り込み・LEFT JOIN(uuid形式
    ガード=Review Focus 5のCAST失敗防止)・created_at降順+id降順タイブレーク・
    2キーキーセット条件。"""
    sql = _compiled(store._SELECT_NOTIFICATIONS_PAGE)
    assert "FROM notifications n" in sql
    assert "LEFT JOIN latches l" in sql
    # 不正なlatch_id文字列でCAST例外にならないよう形式ガード(§9-6)
    assert "n.payload->>'latch_id' ~" in sql
    assert "l.id = CAST(n.payload->>'latch_id' AS uuid)" in sql
    # 本人のみ(design §2.6・Review Focus 4)
    assert "n.user_id = CAST(:me AS uuid)" in sql
    # 2キーキーセット(created_at DESC, id DESC と同値の厳密小条件)
    assert "n.created_at < CAST(:ct AS timestamptz)" in sql
    assert "n.created_at = CAST(:ct AS timestamptz)" in sql
    assert "n.id < CAST(:lid AS uuid)" in sql
    assert "ORDER BY n.created_at DESC, n.id DESC" in sql
    assert "LIMIT :limit" in sql


def test_mark_read_pins_owner_guard_and_null_condition():
    """既読UPDATE(design §2.6): 本人条件・未読条件(既読済みなら影響0=冪等)。"""
    sql = _compiled(store._MARK_READ)
    assert "UPDATE notifications SET read_at = :now" in sql
    assert "WHERE id = CAST(:nid AS uuid)" in sql
    assert "user_id = CAST(:me AS uuid)" in sql
    assert "read_at IS NULL" in sql
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/notifications/test_notifications_service.py -v`
Expected: 3件 FAIL with "ImportError: cannot import name 'store' from 'latch.notifications'"

- [ ] **Step 3: 最小実装**

`backend/src/latch/notifications/store.py` を作成:

```python
"""notifications永続化(M3 ws-3 design §2.5・§2.6・§3.1)。

text()生SQL・_coerce_uuid規律(latches/intentsと同一)。users解決SQLは
パッケージ自前(latches/store.pyと同一慣行 — uq_users_auth_provider_subject
索引へのSELECT 1本)。一覧はpayload->>'latch_id'でlatchesをLEFT JOIN
(design §2.5案A): 参照先不明(削除等)・形式不正はlatch=null(防御)。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_SELECT_USER_ID = text("""
    SELECT id FROM users
    WHERE auth_provider = :provider AND auth_subject = :subject
""")

# お知らせ一覧(design §2.5案A)。cursorは2キー(created_at, id)のキーセット。
# 同一tx内の複数行(D-20同時送信)が同時刻になるためid降順タイブレークが必須。
# LEFT JOINのON句のuuid形式ガードは不正なpayloadでCAST例外→一覧500を防ぐ
# (Review Focus 5・先行書き込み経路は常に正しいuuidを書くため通常通らない防御)
_SELECT_NOTIFICATIONS_PAGE = text("""
    SELECT n.id, n.type, n.payload->>'latch_id', n.read_at, n.created_at,
           l.id, l.status, l.response_deadline, l.expires_at, l.completed_at,
           l.proposal
    FROM notifications n
    LEFT JOIN latches l
      ON n.payload->>'latch_id'
         ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
     AND l.id = CAST(n.payload->>'latch_id' AS uuid)
    WHERE n.user_id = CAST(:me AS uuid)
      AND (CAST(:ct AS timestamptz) IS NULL
           OR n.created_at < CAST(:ct AS timestamptz)
           OR (n.created_at = CAST(:ct AS timestamptz)
               AND n.id < CAST(:lid AS uuid)))
    ORDER BY n.created_at DESC, n.id DESC
    LIMIT :limit
""")

# 既読API手順1(design §2.6): 対象行のSELECT(本人のみ。不在=None)
_SELECT_NOTIFICATION_OWNED = text("""
    SELECT id, read_at FROM notifications
    WHERE id = CAST(:nid AS uuid) AND user_id = CAST(:me AS uuid)
""")

# 既読API手順2(design §2.6): 未読のみUPDATE(既読済みは影響0=冪等)
_MARK_READ = text("""
    UPDATE notifications SET read_at = :now
    WHERE id = CAST(:nid AS uuid) AND user_id = CAST(:me AS uuid)
      AND read_at IS NULL
""")


def _coerce_uuid(value: object) -> uuid.UUID:
    """SELECT結果のUUID列復元(asyncpgのUUIDサブクラス対策・latchesと同一)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _jsonb(value: object) -> object:
    """jsonb列の復元(latches/store.pyと同一規律)。"""
    if isinstance(value, str):
        return json.loads(value)
    return value


@dataclass(frozen=True)
class LatchJoinedRow:
    """LEFT JOINで埋めたlatch要素(design §2.5の応答latchブロック)。"""

    id: uuid.UUID
    status: str
    response_deadline: datetime
    expires_at: datetime
    completed_at: datetime | None
    proposal: dict


@dataclass(frozen=True)
class NotificationPageRow:
    """一覧行(payloadのlatch_idは未変換の生文字列で持つ・serviceで変換)。"""

    id: uuid.UUID
    type: str
    latch_id_raw: str | None
    read_at: datetime | None
    created_at: datetime
    latch: LatchJoinedRow | None


@dataclass(frozen=True)
class OwnedRow:
    """既読APIの所有検査結果。"""

    id: uuid.UUID
    read_at: datetime | None


async def fetch_user_id(
    conn: AsyncConnection, provider: str, subject: str
) -> uuid.UUID | None:
    """認証subject→users.id。未登録JWT=None(呼び出し側は404・引用#17)。"""
    res = await conn.execute(
        _SELECT_USER_ID, {"provider": provider, "subject": subject}
    )
    row = res.first()
    return _coerce_uuid(row[0]) if row is not None else None


async def select_notifications_page(
    conn: AsyncConnection,
    *,
    me: uuid.UUID,
    before: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[NotificationPageRow]:
    """一覧(design §2.5)。before=cursor位置(created_at, id)。"""
    params: dict = {
        "me": me,
        "ct": before[0] if before else None,
        "lid": before[1] if before else None,
        "limit": limit,
    }
    res = await conn.execute(_SELECT_NOTIFICATIONS_PAGE, params)
    out: list[NotificationPageRow] = []
    for r in res.fetchall():
        latch: LatchJoinedRow | None = None
        if r[5] is not None:  # LEFT JOIN成立(l.id)
            latch = LatchJoinedRow(
                id=_coerce_uuid(r[5]),
                status=r[6],
                response_deadline=r[7],
                expires_at=r[8],
                completed_at=r[9],
                proposal=dict(_jsonb(r[10]) or {}),
            )
        out.append(
            NotificationPageRow(
                id=_coerce_uuid(r[0]),
                type=r[1],
                latch_id_raw=r[2],
                read_at=r[3],
                created_at=r[4],
                latch=latch,
            )
        )
    return out


async def select_notification_owned(
    conn: AsyncConnection, *, me: uuid.UUID, notification_id: uuid.UUID
) -> OwnedRow | None:
    """所有検査(本人の行のみ。他人・不存在は同じNone=区別しない404)。"""
    res = await conn.execute(
        _SELECT_NOTIFICATION_OWNED, {"nid": notification_id, "me": me}
    )
    row = res.first()
    if row is None:
        return None
    return OwnedRow(id=_coerce_uuid(row[0]), read_at=row[1])


async def mark_read(
    conn: AsyncConnection,
    *,
    me: uuid.UUID,
    notification_id: uuid.UUID,
    now: datetime,
) -> None:
    """既読化(design §2.6)。未読のみUPDATE(影響は確認不要=冪等204)。"""
    await conn.execute(_MARK_READ, {"nid": notification_id, "me": me, "now": now})
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/notifications/test_notifications_service.py -v`
Expected: PASS(3件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/notifications/store.py backend/tests/unit/notifications/test_notifications_service.py
git commit -m "feat: notifications store (LEFT JOIN page, owned select, mark read)"
```

### Task 8: schemas + service(cursor・一覧・既読のユースケース)

**Files:**
- Create: `backend/src/latch/notifications/schemas.py`
- Create: `backend/src/latch/notifications/service.py`
- Test: `backend/tests/unit/notifications/test_notifications_service.py`(末尾へ7件追記)

**Interfaces:**
- Consumes: Task 7のstore関数
- Produces: `NotificationsService`(`async list(*, auth_provider, auth_subject, cursor=None, limit=20) -> tuple[list[NotificationOut], str | None]`・`async read(*, auth_provider, auth_subject, notification_id) -> None`)・`make_notifications_service(*, clock, engine)`・`NotificationsError`/`NotificationNotFoundError`(404)/`NotificationValidationError`(422)/`DependencyUnavailableError`(503)・`encode_cursor(created_at, notification_id) -> str`・`decode_cursor(cursor) -> tuple[datetime, uuid.UUID]`・`NotificationOut`/`NotificationLatchOut`/`NotificationListResponse`(Task 9のroutes/mainが使う)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/notifications/test_notifications_service.py` へ追記する。**importはTask 7で作成したファイル先頭のimport群へ統合すること**(ruff E402回避・`import uuid as uuid_mod`・`from datetime import UTC, datetime, timedelta`・`import pytest`・`from latch.notifications.schemas import NotificationOut`・`from latch.notifications.service import ...`・`from latch.notifications.store import LatchJoinedRow, NotificationPageRow, OwnedRow`)。関数・クラス定義はファイル末尾へ追記:

```python
# -- service: cursor・read分岐・list組み立て(スタブstore・design §4.1-3) --

SVC_NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
PROVIDER = "google"
SUBJECT = "svc-subject"


class _FakeConn:
    """store呼び出しを記録し固定行を返すスタブconn。"""

    def __init__(self, page_rows=None, owned=None, user_id=None):
        self.page_rows = page_rows or []
        self.owned = owned
        self.user_id = user_id
        self.mark_read_calls: list[dict] = []
        self.last_page_args: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeEngine:
    """serviceのengineスタブ(connectもbeginも同一connを返す)。"""

    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return self._conn

    def begin(self):
        return self._conn


class _StubStore:
    """store関数のスタブ(serviceと同一シグネチャ)。"""

    def __init__(self, conn):
        self._conn = conn

    async def fetch_user_id(self, conn, provider, subject):
        return self._conn.user_id

    async def select_notifications_page(self, conn, *, me, before, limit):
        self._conn.last_page_args = {"me": me, "before": before, "limit": limit}
        return self._conn.page_rows

    async def select_notification_owned(self, conn, *, me, notification_id):
        return self._conn.owned

    async def mark_read(self, conn, *, me, notification_id, now):
        self._conn.mark_read_calls.append(
            {"me": me, "nid": notification_id, "now": now}
        )


def _service(conn) -> NotificationsService:
    return NotificationsService(
        clock=FakeClockForService(), engine=_FakeEngine(conn)
    )


class FakeClockForService:
    def now(self):
        return SVC_NOW


def _page_row(n: int, *, latch=True) -> NotificationPageRow:
    lid = uuid_mod.UUID(f"00000000-0000-4000-8000-{n:012d}")
    latch_row = (
        LatchJoinedRow(
            id=lid,
            status="proposed",
            response_deadline=SVC_NOW + timedelta(hours=2),
            expires_at=SVC_NOW + timedelta(days=5),
            completed_at=None,
            proposal={"headcount": 2, "match_level": "medium"},
        )
        if latch
        else None
    )
    return NotificationPageRow(
        id=uuid_mod.UUID(f"00000000-0000-4000-8000-{1000 + n:012d}"),
        type="proposal",
        latch_id_raw=str(lid) if latch else "not-a-uuid",
        read_at=None,
        created_at=SVC_NOW,
        latch=latch_row,
    )


def test_encode_decode_cursor_roundtrip():
    """2キーcursor(created_at, id)の往復(design §2.5)。"""
    nid = uuid_mod.UUID("00000000-0000-4000-8000-0000000000d1")
    cursor = encode_cursor(SVC_NOW, nid)
    assert decode_cursor(cursor) == (SVC_NOW, nid)


def test_decode_cursor_invalid_raises_422():
    """形式不正はNotificationValidationError(422 VALIDATION_ERROR)。"""
    with pytest.raises(NotificationValidationError):
        decode_cursor("!!!not-base64!!!")
    with pytest.raises(NotificationValidationError):
        decode_cursor("YWJj")  # base64urlとしては有効だが区切りなし


async def test_read_marks_unread():
    """未読行 → mark_read呼出(例外なし=204相当)。"""
    nid = uuid_mod.UUID("00000000-0000-4000-8000-0000000000d1")
    uid = uuid_mod.UUID("00000000-0000-4000-8000-0000000000e1")
    conn = _FakeConn(
        owned=OwnedRow(id=nid, read_at=None),
        user_id=uid,
    )
    stub = _StubStore(conn)
    service = _service(conn)
    _monkeypatch_store(stub)
    await service.read(
        auth_provider=PROVIDER, auth_subject=SUBJECT, notification_id=nid
    )
    assert conn.mark_read_calls == [{"me": uid, "nid": nid, "now": SVC_NOW}]


async def test_read_already_read_is_idempotent():
    """既読行 → UPDATEせず完了(冪等・design §2.6)。"""
    nid = uuid_mod.UUID("00000000-0000-4000-8000-0000000000d1")
    conn = _FakeConn(
        owned=OwnedRow(id=nid, read_at=SVC_NOW),
        user_id=uuid_mod.UUID("00000000-0000-4000-8000-0000000000e1"),
    )
    service = _service(conn)
    _monkeypatch_store(_StubStore(conn))
    await service.read(
        auth_provider=PROVIDER, auth_subject=SUBJECT, notification_id=nid
    )
    assert conn.mark_read_calls == []


async def test_read_not_owned_or_missing_raises_404():
    """所有検査None(他人・不存在を区別しない)→ 404(design §2.6)。"""
    conn = _FakeConn(owned=None, user_id=uuid_mod.UUID(
        "00000000-0000-4000-8000-0000000000e1"
    ))
    service = _service(conn)
    _monkeypatch_store(_StubStore(conn))
    with pytest.raises(NotificationNotFoundError):
        await service.read(
            auth_provider=PROVIDER,
            auth_subject=SUBJECT,
            notification_id=uuid_mod.UUID(
                "00000000-0000-4000-8000-0000000000d2"
            ),
        )


def _monkeypatch_store(stub):
    """service.store属性をスタブへ差し替え(monkeypatchなしの簡易版)。"""
    import latch.notifications.service as service_module

    service_module.store = stub
    return stub


async def test_list_builds_items_with_latch():
    """list応答組み立て: latch要素埋め込み・limit+1でnext_cursor。"""
    rows = [_page_row(1), _page_row(2)]
    conn = _FakeConn(
        page_rows=rows,
        user_id=uuid_mod.UUID("00000000-0000-4000-8000-0000000000e1"),
    )
    service = _service(conn)
    _monkeypatch_store(_StubStore(conn))
    items, next_cursor = await service.list(
        auth_provider=PROVIDER, auth_subject=SUBJECT, limit=1
    )
    assert len(items) == 1
    assert isinstance(items[0], NotificationOut)
    assert items[0].latch is not None
    assert items[0].latch.proposal == {"headcount": 2, "match_level": "medium"}
    assert items[0].latch_id == rows[0].latch_id_raw
    assert next_cursor is not None  # 2行取得(>limit)のため次頁あり
    assert conn.last_page_args["limit"] == 2  # limit+1


async def test_list_latch_null_defensive():
    """参照先不明(latch=null)・不正latch_id文字列 → itemはnullで落ちない。"""
    row = _page_row(3, latch=False)
    conn = _FakeConn(
        page_rows=[row],
        user_id=uuid_mod.UUID("00000000-0000-4000-8000-0000000000e1"),
    )
    service = _service(conn)
    _monkeypatch_store(_StubStore(conn))
    items, next_cursor = await service.list(
        auth_provider=PROVIDER, auth_subject=SUBJECT, limit=20
    )
    assert items[0].latch is None
    assert items[0].latch_id is None  # uuid変換失敗はnull(防御)
    assert next_cursor is None
```

(注: `_monkeypatch_store` は `latch.notifications.service` モジュールの `store` 属性をスタブへ差し替える — service.pyが `from latch.notifications import store` でimportし `store.fetch_user_id(...)` 形式で呼ぶため(latch_engineのモジュール属性経由規律と同一・monkeypatchで差し替え可能)。直接差し替えは本ファイル内のservice試験だけで完結する(本ファイル以外にservice.storeを参照するunit試験は存在しない)ため、pytestのmonkeypatch fixtureを使わずこの簡易形でよい)

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/notifications/test_notifications_service.py -v`
Expected: 追加7件 FAIL with "ImportError: cannot import name 'service'"(store検査3件はPASSのまま)

- [ ] **Step 3: 最小実装**

`backend/src/latch/notifications/schemas.py` を作成:

```python
"""notifications APIの入出力スキーマ(M3 ws-3 design §2.5・§3.1)。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class NotificationLatchOut(BaseModel):
    """お知らせitemのlatch要素(LEFT JOIN埋め込み・design §2.5案A)。

    文言はクライアント/テンプレートがこの構造から組み立てる(05 §2・引用#8)。
    hidden_until_match行はheadcount+match_levelのみ・nearby行は最小構成で
    格納済みのため、条件サマリは構造的に出ない(表示規制のサーバ側担保)。
    """

    id: uuid.UUID
    status: str
    response_deadline: datetime
    expires_at: datetime
    completed_at: datetime | None = None
    proposal: dict


class NotificationOut(BaseModel):
    """お知らせ一覧のitem(design §2.5の応答スキーマ)。"""

    id: uuid.UUID
    type: str
    # payload参照。先行書き込みは常に {"latch_id": "<uuid>"} を書くため通常は
    # 値が入る。不正値はnull(防御・Review Focus 5)
    latch_id: uuid.UUID | None = None
    read_at: datetime | None = None
    created_at: datetime
    latch: NotificationLatchOut | None = None


class NotificationListResponse(BaseModel):
    """GET /v1/notifications応答(05 §5ページネーション共通規定)。"""

    items: list[NotificationOut]
    next_cursor: str | None = None
```

`backend/src/latch/notifications/service.py` を作成:

```python
"""notificationsユースケース(M3 ws-3 design §2.5・§2.6・§2.8)。

一覧(cursor 2キー・LEFT JOIN行の組み立て)と既読(所有検査→冪等UPDATE)。
storeはモジュール属性経由で呼ぶ(unit試験が差し替え可能 — latch_engineと
同一規律)。予期しない例外はlatchesと同じラップ方針(例外クラス名のみ
ログへ残して503)。
"""

from __future__ import annotations

import base64
import logging
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.notifications import store
from latch.notifications.schemas import NotificationLatchOut, NotificationOut

logger = logging.getLogger("latch.notifications")


class NotificationsError(Exception):
    """notificationsドメインエラーの基底。http_status/codeを持つ。"""

    http_status: int
    code: str


class NotificationNotFoundError(NotificationsError):
    """対象が存在しない/他人のもの(区別しない・design §2.6)。未登録JWTも404。"""

    http_status = 404
    code = "NOT_FOUND"


class NotificationValidationError(NotificationsError):
    """リクエスト検証422(cursor形式不正等・intents/latchesと同型)。"""

    http_status = 422
    code = "VALIDATION_ERROR"


class DependencyUnavailableError(NotificationsError):
    """DB等の依存障害(05 §5「全API」)。"""

    http_status = 503
    code = "DEPENDENCY_UNAVAILABLE"


def _wrap_unexpected(exc: Exception) -> DependencyUnavailableError:
    """予期しない例外を503へ包む。例外のクラス名のみログへ残す(08 §2.4)。"""
    logger.warning("notifications.unexpected class=%s", type(exc).__name__)
    return DependencyUnavailableError("notifications dependency unavailable")


def encode_cursor(created_at: datetime, notification_id: uuid.UUID) -> str:
    """キーセットcursor 2キー(design §2.5): base64url("ISO8601|uuid")。

    ソート順(created_at DESC, id DESC)をタプル比較で表現(intentsの
    encode_cursorパターン)。
    """
    raw = f"{created_at.isoformat()}|{notification_id}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """cursor復元。形式不正は422 VALIDATION_ERROR(intentsと同型)。"""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        ts_part, id_part = raw.split("|")
        return datetime.fromisoformat(ts_part), uuid.UUID(id_part)
    except (ValueError, UnicodeDecodeError) as exc:
        raise NotificationValidationError("invalid cursor") from exc


def _to_uuid_or_none(raw: str | None) -> uuid.UUID | None:
    """payloadのlatch_id生文字列→uuid(失敗はnull・防御)。"""
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


class NotificationsService:
    """お知らせ一覧・既読のユースケース(design §2.5・§2.6)。"""

    def __init__(self, *, clock: Clock, engine: AsyncEngine) -> None:
        self._clock = clock
        self._engine = engine

    async def list(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[NotificationOut], str | None]:
        try:
            async with self._engine.connect() as conn:
                user_id = await store.fetch_user_id(
                    conn, auth_provider, auth_subject
                )
                if user_id is None:
                    raise NotificationNotFoundError("user not found")
                before = decode_cursor(cursor) if cursor else None
                rows = await store.select_notifications_page(
                    conn, me=user_id, before=before, limit=limit + 1
                )
            items = []
            for r in rows[:limit]:
                latch_out = None
                if r.latch is not None:
                    latch_out = NotificationLatchOut(
                        id=r.latch.id,
                        status=r.latch.status,
                        response_deadline=r.latch.response_deadline,
                        expires_at=r.latch.expires_at,
                        completed_at=r.latch.completed_at,
                        proposal=r.latch.proposal,
                    )
                items.append(
                    NotificationOut(
                        id=r.id,
                        type=r.type,
                        latch_id=_to_uuid_or_none(r.latch_id_raw),
                        read_at=r.read_at,
                        created_at=r.created_at,
                        latch=latch_out,
                    )
                )
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit - 1]
                next_cursor = encode_cursor(last.created_at, last.id)
            return items, next_cursor
        except NotificationsError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

    async def read(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        notification_id: uuid.UUID,
    ) -> None:
        """既読化(design §2.6)。所有検査Noneは404。既読済みはUPDATEせず完了。"""
        try:
            async with self._engine.begin() as conn:
                user_id = await store.fetch_user_id(
                    conn, auth_provider, auth_subject
                )
                if user_id is None:
                    raise NotificationNotFoundError("user not found")
                row = await store.select_notification_owned(
                    conn, me=user_id, notification_id=notification_id
                )
                if row is None:
                    raise NotificationNotFoundError("notification not found")
                if row.read_at is None:
                    await store.mark_read(
                        conn,
                        me=user_id,
                        notification_id=notification_id,
                        now=self._clock.now(),
                    )
        except NotificationsError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc


def make_notifications_service(
    *, clock: Clock, engine: AsyncEngine
) -> NotificationsService:
    """main.py lifespan用の構築(latchesのmake_latches_serviceと同型)。"""
    return NotificationsService(clock=clock, engine=engine)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/notifications/ -v`
Expected: 全PASS(store 3件+service 7件=10件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/notifications/schemas.py backend/src/latch/notifications/service.py backend/tests/unit/notifications/test_notifications_service.py
git commit -m "feat: notifications service and schemas (cursor, list, read)"
```

### Task 9: routes + __init__公開IF + main.py登録 + test_rate_limit_wiring追従

**Files:**
- Create: `backend/src/latch/notifications/routes.py`
- Modify: `backend/src/latch/notifications/__init__.py`(公開IFの完成)
- Modify: `backend/src/latch/main.py`(import・build_notificationsフラグ・create_app引数・router登録・例外ハンドラ)
- Modify: `backend/tests/unit/ratelimit/test_rate_limit_wiring.py`(Counterへ2行)

**Interfaces:**
- Consumes: Task 8の `NotificationsService`・`NotificationListResponse`・`NotificationsError`・`make_notifications_service`
- Produces: `notifications_router`(GET /v1/notifications・POST /v1/notifications/{notification_id}/read)

- [ ] **Step 1: 失敗するテストを書く(既存ピン試験への機械的追記)**

`backend/tests/unit/ratelimit/test_rate_limit_wiring.py` の `test_all_v1_routes_are_rate_limited` のCounter期待値へ、`"/v1/latches/{latch_id}/response": 1,` の行の後(アルファベット順でlatchesとusersの間)へ追記:

```python
        "/v1/notifications": 1,  # M3 ws-3(お知らせ一覧)
        "/v1/notifications/{notification_id}/read": 1,  # M3 ws-3(既読)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/ratelimit/test_rate_limit_wiring.py -v`
Expected: FAIL(assert失敗・protectedに `/v1/notifications` 2ルートが足りない。create_appがまだrouterを登録していないため)

- [ ] **Step 3: 最小実装**

`backend/src/latch/notifications/routes.py` を作成:

```python
"""notificationsルータ(M3 ws-3 design §2.5・§2.6・05 §5)。

claimsはプリミティブ(provider/subject)としてサービスへ渡す(latchesと
同型)。ログはstatus/codeのみ — 通知文面は出さない(08 §2.4)。
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.notifications.schemas import NotificationListResponse
from latch.notifications.service import NotificationsService
from latch.ratelimit.deps import api_rate_limited

logger = logging.getLogger("latch.notifications")

notifications_router = APIRouter(
    prefix="/v1/notifications",
    tags=["notifications"],
    dependencies=[Depends(api_rate_limited)],  # 401→429(M1 ws-4と同型)
)


def get_notifications_service(request: Request) -> NotificationsService:
    """app.state.notifications_service へのアクセス(lifespan/テスト注入で載る)。"""
    return request.app.state.notifications_service


@notifications_router.get("", response_model=NotificationListResponse)
async def list_notifications(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[NotificationsService, Depends(get_notifications_service)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> NotificationListResponse:
    """GET /v1/notifications(05 §5・design §2.5。本人のお知らせ一覧)。"""
    items, next_cursor = await svc.list(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        cursor=cursor,
        limit=limit,
    )
    return NotificationListResponse(items=items, next_cursor=next_cursor)


@notifications_router.post("/{notification_id}/read", status_code=204)
async def mark_notification_read(
    notification_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[NotificationsService, Depends(get_notifications_service)],
) -> None:
    """POST /v1/notifications/{id}/read(05 §5・design §2.6。204冪等)。"""
    await svc.read(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        notification_id=notification_id,
    )
```

`backend/src/latch/notifications/__init__.py` を完成(design §3.1の公開IF):

```python
"""通知ドメイン(M3 ws-3)。プッシュ媒体(スタブ)とアプリ内通知(お知らせ)API。"""

from latch.notifications.records import PushSendRecord, SendStatus, push_log
from latch.notifications.routes import notifications_router
from latch.notifications.sender import (
    PushSender,
    StubPushSender,
    build_push_sender,
)
from latch.notifications.service import (
    DependencyUnavailableError,
    NotificationNotFoundError,
    NotificationValidationError,
    NotificationsError,
    NotificationsService,
    make_notifications_service,
)

__all__ = [
    "DependencyUnavailableError",
    "NotificationNotFoundError",
    "NotificationValidationError",
    "NotificationsError",
    "NotificationsService",
    "PushSendRecord",
    "PushSender",
    "SendStatus",
    "StubPushSender",
    "build_push_sender",
    "make_notifications_service",
    "notifications_router",
    "push_log",
]
```

`backend/src/latch/main.py` を変更する(4箇所):

(1) import節(latches importの後へ):

```python
from latch.notifications import (
    NotificationsError,
    make_notifications_service,
    notifications_router,
)
```

(2) `_lifespan` 内(51行付近のフラグ群へ + 59〜68行のor条件へ + 139行 build_latches の後へ):

```python
    # フラグ判定行へ追加:
    build_notifications = not hasattr(app.state, "notifications_service")
    # should_run_lifecycle(or条件・engine構築判定)へ build_notifications を追加
    # 構築(engineを載せた後・build_latchesの次):
    if build_notifications:
        app.state.notifications_service = make_notifications_service(
            clock=app.state.clock, engine=engine
        )
```

(3) `create_app` へ引数とstate設定(latches_serviceの次):

```python
def create_app(
    clock: Clock | None = None,
    settings: Settings | None = None,
    auth_service=None,
    users_service=None,
    intent_parse_service=None,
    intent_service=None,
    rate_limiter=None,
    latches_service=None,
    notifications_service=None,
) -> FastAPI:
    ...
    if notifications_service is not None:
        app.state.notifications_service = notifications_service
```

router登録へ追加(`app.include_router(latches_router)` の後):

```python
    app.include_router(notifications_router)
```

(4) 例外ハンドラ(latches_error_handlerの後へ・`notifications_logger = logging.getLogger("latch.notifications")` をモジュール先頭のロガー並びへ):

```python
    @app.exception_handler(NotificationsError)
    async def notifications_error_handler(
        request: Request, exc: NotificationsError
    ) -> JSONResponse:
        notifications_logger.warning("notifications.error code=%s", exc.code)
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(exc.code, str(exc)),
        )
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/ratelimit/test_rate_limit_wiring.py tests/unit/test_app_health.py -v`
Expected: 全PASS

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/notifications/routes.py backend/src/latch/notifications/__init__.py backend/src/latch/main.py backend/tests/unit/ratelimit/test_rate_limit_wiring.py
git commit -m "feat: GET/POST /v1/notifications routes and app wiring"
```

### Task 10: integration試験 test_notifications_api.py(12件・収集確認まで)

**Files:**
- Create: `backend/tests/integration/test_notifications_api.py`

**Interfaces:**
- Consumes: Task 3の `StubPushSender`・Task 4/5の `push` 引数・`/v1/notifications` API・`latch.notifications.records.LOGGER_NAME`(caplog検証)
- Produces: なし(試験のみ。**`make test-ci` は実行しない** — §0規律。`--collect-only` で12件の収集を確認する)

対抗策(design §4.2・Self-Review(3)): SUBJECT_PREFIX=`m3ws3-`・teardownはFK順・時間窓はnow+5日系(BASE_HOURS=120・FAR_EXPIRES_H=144)で他単位と分離。

- [ ] **Step 1: 試験ファイルを作成する(ヘルパー+12試験)**

`backend/tests/integration/test_notifications_api.py` を作成:

```python
"""通知の媒体確定のintegration試験(M3 ws-3 design §4.2・02#13下地)。

実DB(compose常設)・実Redis・api常設(実HTTP・一覧/既読/認証)。プッシュは
StubPushSenderを注入したLatchEngine/ExpirySweeperをテストプロセス内で直接
構成しcaplogで記録を検証する(Workerプロセスは立てない —
test_matching_latchengineと同型)。sweeper試験(試験6)はFakeClockで
run_onceを1回呼ぶ(test_expiry_batchesと同型)。
"""

import asyncio
import json
import logging
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import FakeClock, SystemClock
from latch.geo.service import GeoService
from latch.notifications.records import LOGGER_NAME
from latch.notifications.sender import StubPushSender
from latch.notifications.templates import PUSH_BODY_LATCH, PUSH_BODY_NOTICE
from latch.worker.matching.latch_engine import LatchEngine
from latch.worker.matching.runner import run_candidate_retrieval
from latch.worker.sweeper import ExpirySweeper

pytestmark = pytest.mark.integration

CATEGORY = "meal"
BASE_HOURS = 120  # now+5日系(ws-1/ws-2と同一の対抗策・時間窓分離)
FAR_EXPIRES_H = 144
SUBJECT_PREFIX = "m3ws3-"


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


# -- ヘルパー(test_matching_latchengine.pyと同一形式・自前定義) --


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
        json={"display_name": "m3ws3", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers


def _future(hours: float) -> str:
    return (SystemClock().now() + timedelta(hours=hours)).isoformat()


def _structured(
    *,
    start: str | None = None,
    expires: str | None = None,
    visibility: str | None = None,
    notification_level: str | None = None,
    secondary: str | None = None,
    alcohol: bool = False,
    category: str | None = None,
) -> dict:
    d = {
        "category": {"primary": category or CATEGORY, "secondary": secondary},
        "alcohol_involved": alcohol,
        "time": {"start": start or _future(BASE_HOURS), "end": None},
        "location": {"name": "天文館"},
    }
    if expires is not None:
        d["expires_at"] = expires
    if visibility is not None:
        d["visibility"] = visibility
    if notification_level is not None:
        d["notification_level"] = notification_level
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
            {
                "v": "[" + ",".join(["0.1"] * 768) + "]",
                "id": intent["id"],
            },
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
    return [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == LOGGER_NAME
    ]


def _clock() -> FakeClock:
    return FakeClock(SystemClock().now())


async def _user_id_of(db_engine, intent_id: str) -> str:
    async with db_engine.connect() as conn:
        return str(
            (
                await conn.execute(
                    text(
                        "SELECT user_id FROM intents"
                        " WHERE id = CAST(:id AS uuid)"
                    ),
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


# -- §4.2 試験1〜8(12件) --


async def test_1_e2e_push_sent_and_listed(
    api_client, db_engine, field, caplog
):
    """E2E送信(02#13下地): proposed遷移→tx後ok記録2件(汎用文フル文字列)→
    GET /v1/notificationsで本人にお知らせ1件(latch.status=proposed)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=start, expires=expires)
    )
    hb, _ = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.85)
    sender = StubPushSender(clock=clock)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _handle(db_engine, clock, a["id"], push=sender)
    recs = _push_records(caplog)
    uid_a, uid_b = await _user_id_of(db_engine, a["id"]), await _user_id_of(
        db_engine, b["id"]
    )
    assert len(recs) == 2
    assert {r["user_id"] for r in recs} == {uid_a, uid_b}
    for r in recs:
        assert r["status"] == "ok"
        assert r["title"] == "LATCH"
        assert r["body"] == PUSH_BODY_LATCH  # 汎用文フル文字列
        assert r["notification_type"] == "proposal"
        assert r["latch_id"]
    # お知らせ一覧(本人のみ・latch埋め込み)
    resp = await api_client.get("/v1/notifications", headers=ha)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) >= 1
    mine = [i for i in body["items"] if i["type"] == "proposal"]
    assert len(mine) == 1
    assert mine[0]["latch_id"] == uuid_mod.UUID(recs[0]["latch_id"]) or (
        mine[0]["latch_id"] == uuid_mod.UUID(recs[1]["latch_id"])
    )
    assert mine[0]["latch"]["status"] == "proposed"
    assert mine[0]["latch"]["proposal"]["headcount"] == 2


async def test_2_push_body_identical_across_visibility_and_category(
    api_client, db_engine, field, caplog
):
    """FR-21(引用#1・#11): summary_only×2・hidden混在・drinkingの3ケースで
    プッシュ本文がbyte同一(文言分岐なしの証明)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    bodies: list[str] = []
    cases = [
        # (a側structured, b側structured)
        (
            _structured(start=start, expires=expires, visibility="summary_only"),
            _structured(start=start, expires=expires, visibility="summary_only"),
        ),
        (
            _structured(
                start=start, expires=expires, visibility="hidden_until_match"
            ),
            _structured(start=start, expires=expires, visibility="summary_only"),
        ),
        # drinking(カテゴリ推察防止の様式同一・引用#1)
        (
            _structured(
                start=start, expires=expires, alcohol=True, category="drinking"
            ),
            _structured(
                start=start, expires=expires, alcohol=True, category="drinking"
            ),
        ),
    ]
    for sa, sb in cases:
        ha, _ = await _user(api_client, field)
        a = await _intent(api_client, db_engine, ha, sa)
        hb, _ = await _user(api_client, field)
        b = await _intent(api_client, db_engine, hb, sb)
        async with db_engine.begin() as conn:
            await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
        await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            await _handle(db_engine, clock, a["id"], push=StubPushSender(clock))
    recs = [r for r in _push_records(caplog) if r["status"] == "ok"]
    assert len(recs) == 6  # 3ケース×2名
    assert {r["body"] for r in recs} == {PUSH_BODY_LATCH}  # byte同一(集合1要素)
    assert {r["title"] for r in recs} == {"LATCH"}


async def test_3_muted_participant_no_push(
    api_client, db_engine, field, caplog
):
    """#13 muted下地(引用#6): 片方muted→push記録1件のみ・notifications行は
    muted側0行・latches.status=proposed(遷移は通常どおり)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=start, expires=expires))
    hb, _ = await _user(api_client, field)
    b = await _intent(
        api_client,
        db_engine,
        hb,
        _structured(
            start=start, expires=expires, notification_level="muted"
        ),
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.85)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _handle(db_engine, clock, a["id"], push=StubPushSender(clock))
    recs = _push_records(caplog)
    uid_a, uid_b = await _user_id_of(db_engine, a["id"]), await _user_id_of(
        db_engine, b["id"]
    )
    assert len(recs) == 1
    assert recs[0]["user_id"] == uid_a  # muted側(b)には出ない
    # DB: a側1行・b側0行・latchはproposed
    assert len(await _notifications_rows(db_engine, uid_a)) == 1
    assert len(await _notifications_rows(db_engine, uid_b)) == 0
    async with db_engine.connect() as conn:
        status = (
            await conn.execute(
                text(
                    "SELECT status FROM latches"
                    " WHERE intent_ids && ARRAY[CAST(:a AS uuid),"
                    " CAST(:b AS uuid)]"
                ),
                {"a": a["id"], "b": b["id"]},
            )
        ).scalar_one()
    assert status == "proposed"
```

(続く試験4〜8は次のブロック。**1ファイルとして連結して書くこと**)

試験4〜8を同じファイルへ続けて追記:

```python
async def test_4a_list_desc_order_with_id_tiebreak(api_client, db_engine, field):
    """一覧: created_at降順・同一時刻行はid降順タイブレーク(Review Focus 5)。

    FakeClock固定なので2回のhandleが同一created_atになる → タイブレーク検証。
    """
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=start, expires=expires))
    # latchを2つ作る(相手を変える・同一起点ユーザー)
    partners = []
    for _ in range(2):
        hb, _ = await _user(api_client, field)
        partners.append(
            await _intent(
                api_client, db_engine, hb,
                _structured(start=start, expires=expires),
            )
        )
    for b in partners:
        async with db_engine.begin() as conn:
            await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
        await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
        await _handle(db_engine, clock, a["id"])  # push無しでもnotificationsは書く
    resp = await api_client.get("/v1/notifications", headers=ha)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 2  # 起点ユーザーに提案通知2件
    assert items[0]["created_at"] == items[1]["created_at"]  # 同一時刻(FakeClock)
    assert items[0]["id"] > items[1]["id"]  # id降順タイブレーク
    assert [i["type"] for i in items] == ["proposal", "proposal"]


async def test_4b_pagination_and_validation_errors(
    api_client, db_engine, field
):
    """改頁(limit=1で2頁)・limit=101→422・cursor形式不正→422(05 §5共通規定)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=start, expires=expires))
    for _ in range(2):
        hb, _ = await _user(api_client, field)
        b = await _intent(
            api_client, db_engine, hb, _structured(start=start, expires=expires)
        )
        async with db_engine.begin() as conn:
            await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
        await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
        await _handle(db_engine, clock, a["id"])
    # 1頁目
    resp = await api_client.get("/v1/notifications?limit=1", headers=ha)
    assert resp.status_code == 200, resp.text
    first = resp.json()
    assert len(first["items"]) == 1
    assert first["next_cursor"]
    # 2頁目
    resp2 = await api_client.get(
        f"/v1/notifications?limit=1&cursor={first['next_cursor']}", headers=ha
    )
    assert resp2.status_code == 200, resp2.text
    second = resp2.json()
    assert len(second["items"]) == 1
    assert second["items"][0]["id"] != first["items"][0]["id"]  # 取りこぼし・重複なし
    assert second["next_cursor"] is None
    # limit超過422
    resp3 = await api_client.get("/v1/notifications?limit=101", headers=ha)
    assert resp3.status_code == 422, resp3.text
    # cursor形式不正422
    resp4 = await api_client.get(
        "/v1/notifications?cursor=%21%21invalid", headers=ha
    )
    assert resp4.status_code == 422, resp4.text


async def test_4c_hidden_row_proposal_minimal(api_client, db_engine, field):
    """hidden_until_match行: item.latch.proposalがheadcount+match_levelのみ
    (#22の下地・引用#2/#8・表示規制のサーバ側構造担保)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(
        api_client,
        db_engine,
        ha,
        _structured(start=start, expires=expires, visibility="hidden_until_match"),
    )
    hb, _ = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
    await _handle(db_engine, clock, a["id"])
    resp = await api_client.get("/v1/notifications", headers=ha)
    assert resp.status_code == 200, resp.text
    (item,) = resp.json()["items"]
    assert item["type"] == "proposal"
    assert item["latch"]["proposal"] == {
        "headcount": 2,
        "match_level": "high",
    }  # 0.9=high。条件サマリ(time_summary等)は出ない


async def test_5a_read_marks_and_idempotent(api_client, db_engine, field):
    """既読: 204・read_at反映・二回目も204(冪等・design §2.6)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=start, expires=expires))
    hb, _ = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
    await _handle(db_engine, clock, a["id"])
    resp = await api_client.get("/v1/notifications", headers=ha)
    (item,) = resp.json()["items"]
    assert item["read_at"] is None
    # 1回目: 204
    r1 = await api_client.post(f"/v1/notifications/{item['id']}/read", headers=ha)
    assert r1.status_code == 204, r1.text
    resp = await api_client.get("/v1/notifications", headers=ha)
    (after,) = resp.json()["items"]
    assert after["read_at"] is not None
    # 2回目: 204(冪等)
    r2 = await api_client.post(f"/v1/notifications/{item['id']}/read", headers=ha)
    assert r2.status_code == 204, r2.text


async def test_5b_read_not_found_for_others_and_missing(
    api_client, db_engine, field
):
    """404系(Review Focus 4): 他人のid・存在しないidを区別しない404。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=start, expires=expires))
    hb, _ = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
    await _handle(db_engine, clock, a["id"])
    resp = await api_client.get("/v1/notifications", headers=ha)
    (item,) = resp.json()["items"]
    # 他人(hb)から: 404
    r_other = await api_client.post(
        f"/v1/notifications/{item['id']}/read", headers=hb
    )
    assert r_other.status_code == 404, r_other.text
    assert r_other.json()["error"]["code"] == "NOT_FOUND"
    # 存在しないid: 404
    r_missing = await api_client.post(
        f"/v1/notifications/{uuid_mod.uuid4()}/read", headers=ha
    )
    assert r_missing.status_code == 404, r_missing.text


async def test_6_attendance_push_and_list(api_client, db_engine, field, caplog):
    """attendance送信(引用#10): matched→completed(sweeper)→push記録(第二汎用文)・
    一覧にattendance_request行・latch.status=completed。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(api_client, db_engine, ha, _structured(start=start, expires=expires))
    hb, _ = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    # matchedなlatches行を直接INSERT(回答APIを回すより早い・ws-2流儀)
    lo, hi = sorted([a["id"], b["id"]], key=str)
    async with db_engine.begin() as conn:
        latch_id = (
            await conn.execute(
                text("""
                    INSERT INTO latches
                        (intent_ids, proposal, score, status, responses,
                         response_deadline, expires_at, created_at)
                    VALUES (ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[],
                            CAST(:proposal AS jsonb), 0.9, 'matched',
                            CAST(:responses AS jsonb),
                            CAST(:deadline AS timestamptz),
                            CAST(:expires AS timestamptz),
                            CAST(:created AS timestamptz))
                    RETURNING id
                """),
                {
                    "a": lo,
                    "b": hi,
                    "proposal": json.dumps({"headcount": 2, "match_level": "high"}),
                    "responses": json.dumps([]),
                    "deadline": clock.now() + timedelta(hours=2),
                    "expires": clock.now() + timedelta(hours=FAR_EXPIRES_H),
                    "created": clock.now(),
                },
            )
        ).scalar_one()
        # 対象時刻経過を再現(intents.time_startを過去へ・apiのClockは差し替え不能
        # なためDB値操作 — ws-1計画§9-2規律)
        await conn.execute(
            text(
                "UPDATE intents SET time_start = CAST(:past AS timestamptz)"
                " WHERE id = ANY(ARRAY[CAST(:a AS uuid), CAST(:b AS uuid)]::uuid[])"
            ),
            {"past": clock.now() - timedelta(hours=1), "a": lo, "b": hi},
        )
    sender = StubPushSender(clock=clock)

    class _NoopLatch:
        async def drain(self):
            return None

    sweeper = ExpirySweeper(
        engine=db_engine,
        clock=clock,
        latch=_NoopLatch(),
        batch_limit=50,
        push=sender,
    )
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await sweeper.run_once()
    recs = [r for r in _push_records(caplog) if r["latch_id"] == str(latch_id)]
    uid_a, uid_b = await _user_id_of(db_engine, a["id"]), await _user_id_of(
        db_engine, b["id"]
    )
    assert len(recs) == 2  # 参加者全員
    assert {r["user_id"] for r in recs} == {uid_a, uid_b}
    for r in recs:
        assert r["body"] == PUSH_BODY_NOTICE  # 第二汎用文
        assert r["notification_type"] == "attendance_request"
    resp = await api_client.get("/v1/notifications", headers=ha)
    types = [i["type"] for i in resp.json()["items"]]
    assert "attendance_request" in types
    att = next(i for i in resp.json()["items"] if i["type"] == "attendance_request")
    assert att["latch"]["status"] == "completed"


async def test_7a_nearby_push_notification(api_client, db_engine, field, caplog):
    """nearby(引用#5): 閾値未満+nearby_also→存在通知のpush記録(提案と同一文言)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(
        api_client,
        db_engine,
        ha,
        _structured(start=start, expires=expires, notification_level="nearby_also"),
    )
    hb, _ = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.5, 0.5)  # 閾値未満
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _handle(db_engine, clock, a["id"], push=StubPushSender(clock))
    recs = _push_records(caplog)
    uid_a = await _user_id_of(db_engine, a["id"])
    assert len(recs) == 1
    assert recs[0]["user_id"] == uid_a  # nearby_also側のみ
    assert recs[0]["notification_type"] == "nearby_candidate"
    assert recs[0]["body"] == PUSH_BODY_LATCH  # 提案と同一文言(承認②)
    resp = await api_client.get("/v1/notifications", headers=ha)
    (item,) = resp.json()["items"]
    assert item["type"] == "nearby_candidate"
    assert item["latch"]["proposal"] == {"headcount": 2, "match_level": "low"}


async def test_7b_nearby_daily_limit_no_push(api_client, db_engine, field, caplog):
    """nearby日次上限(引用#5): 当日6件済み→存在通知なし(push記録なし)。
    candidate行自体は作る(上限はcandidateを破棄しない・ws-6)。"""
    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, _ = await _user(api_client, field)
    a = await _intent(
        api_client,
        db_engine,
        ha,
        _structured(start=start, expires=expires, notification_level="nearby_also"),
    )
    uid_a = await _user_id_of(db_engine, a["id"])
    # 当日分の通知を6件事前投入(JST日付窓=FakeClockのnow)
    async with db_engine.begin() as conn:
        for i in range(6):
            await conn.execute(
                text(
                    "INSERT INTO notifications (user_id, type, payload, created_at)"
                    " VALUES (CAST(:u AS uuid), 'proposal',"
                    " CAST(:payload AS jsonb), CAST(:now AS timestamptz))"
                ),
                {
                    "u": uid_a,
                    "payload": json.dumps({"latch_id": str(uuid_mod.uuid4())}),
                    "now": clock.now(),
                },
            )
    hb, _ = await _user(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.5, 0.5)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        await _handle(db_engine, clock, a["id"], push=StubPushSender(clock))
    recs = [r for r in _push_records(caplog) if r["user_id"] == uid_a]
    assert recs == []  # 上限到達→存在通知なし
    # 事前投入6件のみ(当日分は増えない)
    rows = await _notifications_rows(db_engine, uid_a)
    assert len([r for r in rows if r[1] == "nearby_candidate"]) == 0


async def test_8_unauthenticated_401(api_client, db_engine, field):
    """無token 401(GET・POST両経路・C3)。"""
    resp = await api_client.get("/v1/notifications")
    assert resp.status_code == 401, resp.text
    resp2 = await api_client.post(
        f"/v1/notifications/{uuid_mod.uuid4()}/read"
    )
    assert resp2.status_code == 401, resp2.text
```

- [ ] **Step 2: 収集確認(実行はしない・§0規律)**

Run: `cd backend && uv run pytest --collect-only tests/integration/test_notifications_api.py -q`
Expected: exit 0・**12件収集**(design §4.2の8群を4a/4b/4c・5a/5b・7a/7bに展開: test_1・test_2・test_3・test_4a・test_4b・test_4c・test_5a・test_5b・test_6・test_7a・test_7b・test_8=12関数・parametrizeなし)。実測件数を報告書に記録する(§9-12の見込みと照合)

- [ ] **Step 3: unit全件の再確認(コミット前検査)**

Run: `make lint && make test`
Expected: グリーン(unit 1129件)

- [ ] **Step 4: コミット**

```bash
git add backend/tests/integration/test_notifications_api.py
git commit -m "test: integration tests for notifications API and push dry-run"
```

### Task 11: 報告ファイル + 最終検証

**Files:**
- Create: `docs/plans/M3/ws-3-report.md`

- [ ] **Step 1: §6の完了条件1〜8を全て実行し、証拠(コマンド出力の要点)を採取する**

(コマンド一覧は§7の検証手順どおり。test-ciは実行しない)

- [ ] **Step 2: 報告ファイルを§7の形式で作成する**

design §5-7/§5-8(実装時確認事項)の結果・§9 IF確定事項からの変更有無を必ず記入する。

- [ ] **Step 3: コミット**

```bash
git add docs/plans/M3/ws-3-report.md
git commit -m "docs: ws-3 report with verification evidence"
```

- [ ] **Step 4: worktreeの状態を整える**

`git status --short` が空(`.venv/`・`__pycache__/` はコミットしない)であることを確認。**mainへのマージはスーパーバイザーが行うため、push・mergeはしない**。

---

## 9. IF確定事項(design §2の固定値。ここから変更した場合は報告書に記録)

1. **type定数**(types.py・design §1.3): `NOTIFICATION_PROPOSAL="proposal"`・`NOTIFICATION_NEARBY="nearby_candidate"`・`NOTIFICATION_ATTENDANCE_REQUEST="attendance_request"`。値は既存実装と同一(ws-6/ws-2)。latch_engine/sweeperはimport切替で**同名をモジュール属性として再参照**(既存の `from latch.worker.matching.latch_engine import NOTIFICATION_PROPOSAL`・`from latch.worker.sweeper import ...` を壊さない)
2. **プッシュ文言**(templates.py・design §2.4): `PUSH_TITLE="LATCH"`・`PUSH_BODY_LATCH="LATCH候補があります。\n詳細はアプリでご確認ください。"`(proposal/nearby_candidate共用)・`PUSH_BODY_NOTICE="LATCHからのお知らせがあります。\n詳細はアプリでご確認ください。"`(attendance_request)。`build_push(notification_type)`はtype以外の引数を持たない(FR-21構造担保)。未知typeはValueError
3. **PushSendRecord**(records.py・design §2.3): 8フィールド(occurred_at/user_id/notification_type/latch_id/title/body/status/error_code)。`LOGGER_NAME="latch.push.send"`・JSON 1行(model_dump_json)。本文を含むのはLLM SendRecordとの意図的差分(10 §1の記録検証要求)
4. **StubPushSender**(sender.py・design §2.2): ctor `(clock, delay_ms=0, timeout_s=PUSH_TIMEOUT_S, fail=False)`。`PUSH_TIMEOUT_S=3.0`。timeout判定は `asyncio.timeout`(gatewayと同型)。fail=Trueはerror記録経路の試験用。例外握り・ValueErrorは握らない
5. **build_push_sender(clock, settings)**: 引数順は `(clock, settings)`(design §2.2の表記に対しbuild_llm_gateway/build_worker_gatewayの慣行に合わせた・機能的同一)。push_mode="stub"のみ対応・他はValueError
6. **store SQL**(store.py・design §2.5/2.6): `_SELECT_USER_ID`(users解決・latchesと同一のSELECT 1本)・`_SELECT_NOTIFICATIONS_PAGE`(LEFT JOIN+uuid形式正規表現ガード+2キーキーセット+ORDER BY created_at DESC, id DESC)・`_SELECT_NOTIFICATION_OWNED`(WHERE id AND user_id)・`_MARK_READ`(read_at IS NULL条件・影響確認不要の冪等UPDATE)。SQL全文はTask 7 Step 3のコードブロックどおり
7. **cursor 2キー**(service.py・design §2.5): base64url("ISO8601|uuid")・形式不正は422 VALIDATION_ERROR(intents/latchesと同型)
8. **API応答スキーマ**(schemas.py・design §2.5): `{"items": [{id, type, latch_id, read_at, created_at, latch|null}], "next_cursor"}`。`latch={id, status, response_deadline, expires_at, completed_at, proposal}|null`。latch_id/latchは防御的にnull許容(不正payloadで一覧が500にならない)。responses・score・intent_ids・参加者情報はitemに出さない
9. **例外**(service.py内に定義・**errors.pyは作らない** — design §3.1のファイル一覧13件を守る): `NotificationsError`基底・`NotificationNotFoundError`(404 NOT_FOUND)・`NotificationValidationError`(422 VALIDATION_ERROR)・`DependencyUnavailableError`(503 DEPENDENCY_UNAVAILABLE)。main.pyの例外ハンドラはlatchesと同型
10. **注入IF**: `LatchEngine(..., push=None)`・`ExpirySweeper(..., push=None)`(キーワード引数・None許容=no-op)。worker/main.pyは `build_push_sender(self.clock, self.settings)` を1つ構築し両方へ注入。送信はtxブロック外(コミット後)。latch_engineの送信ヘルパー `_send_pushes(targets: list[(user_id, type, latch_id)])`・sweeperの `_send_attendance_pushes(latch_id, user_ids)`
11. **無変更の確定値**: `_count_daily_notifications`/`_COUNT_DAILY_NOTIFICATIONS`(D-08・attendance_requestは上限不消費)・`_insert_notification`・`_INSERT_ATTENDANCE_NOTIFICATION`・try_promote/_nearby_in_txの上限判定ロジック・`_kick_embedding` 規律(None許容no-op)
12. **試験数の整合**(2026-10-01時点のmain基準・unit 1100+integration 201=test-ci 1301): unit新規29件(test_templates 4+test_push_sender 9〔parametrize 2ケース込み〕+test_latch_engine 4+test_sweeper 2+test_notifications_service 10〔store 3+service 7〕)=**unit 1129件**。integration新規12件=**213件**。test-ci全体=**1342件**(1301+41)。**収集・実行の実測値がこの見込みと異なる場合は報告書に実測値と差異の理由を記録する**(試験の追加・統合はagent3の裁量ではなく、計画からの逸脱として記録対象)

## Self-Review(計画書レビュー時の機械チェック・2026-10-01実施)

1. **新規テストファイルのbasename衝突**: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は**空**(既存114ファイル時点・design §4はこの時点の確認)。新規4ファイル `test_templates.py`・`test_push_sender.py`・`test_notifications_service.py`・`test_notifications_api.py` は既存リストに同名なし(`find ... -name "<name>"` で0件を確認済み)
2. **ピン試験への追随访問**(触る全ピン試験と影響有無):
   - `tests/unit/test_settings.py` — **影響あり・機械的追従**: test_settings_defaultsへpush_2項目のassert追記(Task 2)
   - `tests/unit/llm/test_llm_factory.py` — **影響なし(触らない)**: llm 9項目ピン `test_llm_settings_are_exactly_nine_fields` は `f.startswith("llm_")` の機械検査のためpush_*プレフィックスと無干渉。Task 2 Step 4で緑維持を確認
   - `tests/unit/matching/test_latch_engine.py` — **影響あり・追記+ヘルパー拡張**: `NOTIFICATION_PROPOSAL`/`NOTIFICATION_NEARBY` はlatch_engineからの再参照で無傷(値不変・661〜662行の値ピンも不変)。`_engine` ヘルパーへpush引数(Task 4)・末尾へ4件追記
   - `tests/unit/worker/test_sweeper.py` — **影響あり・追記+ヘルパー拡張**: 定数import切替に対し既存試験はSQL定数import・`"attendance_request"` 文字列リテラル比較のため無傷(design §5-7)。`_sweeper` ヘルパーへpush引数(Task 5)・attendance送信2件追記
   - `tests/unit/ratelimit/test_rate_limit_wiring.py` — **影響あり・機械的追従**: Counterへ新ルート2行(Task 9・ws-4も同ファイルへ3行追記するがマージはスーパーバイザーが両側保持で解消・design §5-8)
   - `tests/unit/test_worker_reeval.py` — **影響なし(触らない)**: sweeper注入試験はOrderededSweeperスタブを使いExpirySweeper ctorを直接呼ばない
   - `tests/integration/test_matching_latchengine.py` — **影響なし(触らない)**: `'proposal'` 文字列リテラル比較
   - `tests/integration/test_expiry_batches.py` — **影響なし(触らない)**: ExpirySweeper ctorのpushはデフォルトNone
   - `tests/unit/test_llm_gemini.py`・`tests/unit/g1gate/*`・`tests/unit/g2gate/*` — **影響なし(触らない・Settingsにllm系以外のキーを追加しない)**。ただし pydantic-settings は未知フィールドを拒否するため、**Settingsを直接構築する全既存試験はpush_2キーの追加で壊れない**(デフォルト値つき追加のため)。make test全件で証明
3. **DB残存干渉の対抗策**: SUBJECT_PREFIX=`m3ws3-`(fixtureが試験ごとに一意hexを付与)・teardownはFK順(latch_status_events→calibration_records→notifications→latches→match_candidates→group_candidates→match_events→intents→users)・時間窓はnow+5日系(BASE_HOURS=120・FAR_EXPIRES_H=144)でws-1/ws-2と同一規律・sweeper試験(試験6)のtime_start過去化は当該latch行のみUPDATE
4. **specカバレッジ**(design §1.1の3成果物→Taskの逆引き):
   - FCMプッシュ送信(スタブ) → Task 1〜6(types/templates/records/sender/注入/配線)
   - アプリ内通知(お知らせ)API → Task 7〜9(store/service/routes/main)
   - 通知文面の供給規則 → Task 1(build_push)+Task 7〜8(LEFT JOIN埋め込み・文言はクライアント)
   - design §4.1のunit群(1〜5)→ Task 1/2/4/5/7/8・design §4.2のintegration群(1〜8)→ Task 10
   - design §4.3の検証手順 → §6・§7(test-ci禁止の適用あり・§0)
   - design §1.4のスコープ外・§2.8の切り捨て → §5にすべて明記
5. **プレースホルダスキャン**: TBD/TODO/「適切に」等のプレースホルダなし。全コードステップに全文コードあり(service.pyのlist組み立ては単一の形に統一済み・遅延importなし)
6. **型整合**: `build_push(notification_type: str) -> tuple[str, str]`(Task 1→2/10)・`StubPushSender.send(*, user_id, notification_type, latch_id) -> None`(Task 2→4/5/10)・`LatchEngine(..., push=None)`(Task 4→6/10)・`ExpirySweeper(..., push=None)`(Task 5→6/10)・`select_notifications_page(conn, *, me, before, limit)`(Task 7→8)・`NotificationsService.list/read`(Task 8→9)・`notifications_router`(Task 9→main.py)。すべて突合済み
