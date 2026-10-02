# M3 m3-g3fix(⑫my_response+③成立通知)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** G3レビュー指摘2件を解消する — 修正A(⑫): GET /v1/notifications応答のlatch要素へ`my_response`を追加し、お知らせ終了行を自分の操作履歴文言(no=「辞退しました」/defer=「今回は見送りました」)へ分岐させる。修正B(③): 提案成立(partial_accept→matched)時に`type=latch_matched`通知を参加者全員へ先行書き込み+txコミット後に第三汎用文プッシュを送る。**マイグレーションなし・notificationsテーブル構造不変・依頼追加なし**

**Architecture:** design §2 — my_responseは**案A**(一覧SELECTへ`l.responses`を1列追加し、Pythonで`_my_response`抽出。latches/service.py:539の同名privateヘルパーと同一ロジックの複製=private関数のimportはモジュール規律に反するため。両者の一致はunit試験の入出力対応で担保)。フロント文言は**closedText一元化**(ws-7詳細画面と同じ関数・同じ値で文言一貫が構造的に担保)。成立通知は**respond()への挿入**(matched遺移を書く経路はrespondのみ=05 §6遺移表。sweeperのcompleted通知と対称な「tx内先行書き込み・コミット後プッシュ送信」構造)。プッシュ本文は**第三の汎用文**(build_pushの引数・構造とも不変=08 §2.6の構造担保を踏襲)。

**Tech Stack:** 変更なし(Python 3.13 / FastAPI+SQLAlchemy text()生SQL / pytest / frontend: Vite+vanilla JS+vitest 3)。依頼追加なし(uv・npmともに)・alembic head=0006不変。

**Spec:** `docs/plans/M3/m3-g3fix-design.md`(agent1設計メモ。**未解決論点なし** — §6の6件は2026-10-02オーナー裁定「全部推奨で」+supervisor技術判断で全件確定・STATUS 9f9e7cfコミットに承認記録。design.mdが本計画より優先)

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m3-g3fix`。**agent3はworktree内でコミットする**(mainへの直接コミット・pushは禁止。マージはスーパーバイザーが行う)。worktreeでは最初に `make setup`(`uv sync`)と `cd frontend && npm install` の両方を実行する(backendとfrontendの両方に触るため。依頼追加なしのためlockもpackage-lock.jsonも不変)
- **【最重要】共有ci-dbへの `make test-ci` / `make migrate` は実行禁止**(STATUS運用ルール1)。**integration試験の実行自体がスーパーバイザー検証時まで禁止**(共有ci-db・STATUS運用ルール1〜3)。開発は **unit相当の検査(`make lint && make test`・`--collect-only`・`uv run python -m py_compile`・frontendは`npm test`)**のみで進める。報告ファイルの該当欄には **「test-ci=スーパーバイザー検証待ち」** と記録する。`docker compose` 系コマンドも不要(test-ciを実行しないため)
- **テストコードを書いたら自己見直しを怠らない**(M3 ws-3の教訓・supervisor指示): ヘルパーの引数形式(キーワード専用引数か)・戻り値unpack・モック応答形式・比較の型(UUIDかstrか)。特に本単位は ①responses配列の`user_id`は**str(uuid)**で格納(05 §2) ②PushSendRecordのuser_id/latch_idは**str** ③`_page_row(n, *, latch)`はキーワード専用 ④`_user_with_subject`は`(headers, subject)`のtuple返し—unpackを忘れない
- **テストファイルのbasename一意**(STATUS運用ルール5): **本単位は新規テストファイルゼロ**(design §3のテスト5ファイルはすべて既存ファイルへの追記)。念のため各Taskのコミット前に `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空であることを確認する
- **マイグレーション・依頼・Makefile・compose変更禁止**(design §3): Task 6で `git diff main -- backend/alembic backend/pyproject.toml backend/uv.lock frontend/package.json frontend/package-lock.json Makefile compose.yaml docker` が空であることを検証する。head=0006不変
- **固定値の遵守**: design §2(採用判断)・§6(裁定)と本計画§9のIF確定事項は固定値。**変更した場合は必ず報告ファイルに「変更前→変更後+理由」を記録する**。黙って変えない
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `docs:` 等)
- **毎コミットの検査**: backend変更タスク(Task 1〜3・5)は `make lint && make test` がグリーン。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行。frontendタスク(Task 4)は `cd frontend && npm test` がグリーン
- **`python` / `pip` / `npm` の個別コマンドは素直に**: python系はすべて `uv run` 経由。frontendは `npm test` / `npm install` のみ
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| 提案が閉じる表示は2種類に集約 — 「自分の操作によるもの(NO・見送り・Intentの停止削除): 自身の操作履歴として表示」「それ以外: 『この提案は成立しませんでした』の一文で統一」。相手の回答種別・ブロック事実は一切開示しない | 03 §7(design §1-1) |
| プッシュ本文は汎用文のみとし条件サマリ(人数・時間帯・地域名・カテゴリ・予算)を含めない。この規制はプッシュ許可の有無と独立 | 03 §4・08 §2.6(design §1-2) |
| 通知のタイミングは双方(グループでは必要人数全員)への同時送信 | 03 §4 D-20(design §1-3) |
| D-08上限(1ユーザー日6件)の対象は「提案通知」(nearby_alsoの存在通知を含む)=成立通知は対象外 | 03 §4 D-08・06 §10(design §1-4・§2.3) |
| mutedのIntentは提案通知を送らない(それ以外の挙動は通常どおり)=成立通知への適用なし(契約成立の連絡であり提案供給ではない) | 06 §6(design §1-5・§2.3) |
| 遷移表: partial_accept→matched のトリガーは「必要人数全員がyes」(通知規定なし=今回追加)。matched→completed は「遷移時に実施自己申告の通知を送る」 | 05 §6(design §1-6) |
| 成立後はチャット・表示名・最小限プロフィール・集合情報・次アクション(チャット開始の誘導)を解放する | 03 §6(design §1-7) |
| 必要人数のYESが揃った時点で(中略)チャット・集合情報を解放する | 08 §2.2(design §1-8) |
| 「預けておけば、成立時にだけ通知が来る状態を作る」=成立通知が導入文の約束 | 01 第46行付近(design §1-9) |
| GET /v1/notificationsはcursor方式・既定ソートcreated_at降順(limit既定20) | 05 §5(design §1-10) |
| latches.responsesは構造化配列(`{user_id, intent_id, response: yes/no/defer, answered_at}`)。回答種別は記録として区別保持 | 05 §2・§7・03 D-07(design §1-11) |
| タップの遷移先はプッシュ・アプリ内通知のいずれからでも提案詳細画面へ(バナー上の直接回答ボタンなし)=notificationHref変更不要 | 03 §4 FR-46(design §1-12) |
| attendance_request通知は参加者全員へ先行書き込み・D-08日次上限は消費しない=成立通知と同型の解釈 | 05 §6・ws-3解釈④(design §1-13) |
| 文言裁定(2026-10-02全件確定・design §6): ①終了行=詳細画面同一文言流用 ②アプリ内=見出し「LATCHが成立しました。」+導線「チャットで挨拶を交わしましょう」(NEXT_ACTION_TEXT流用)+バッジ(成立済み/完了)・条件サマリ不含 ③プッシュ=「LATCHが成立しました。\nチャットでメッセージを確認しましょう。」(推奨案) ④type=latch_matched ⑤参加者全員へ同時送信 ⑥matched経路追加時の通知追随をコードコメント+STATUS引継ぎへ記録 | design §6 |
| 通知type定数の正本・build_pushの引数はnotification_typeのみ(構造担保) | ws-3 design §3.1・§2.4 |
| プッシュ送信はtxコミット後(先行書き込み経路の慣行) | ws-3 design §2.1案A |

## 2. グローバル制約(全タスクに暗黙に適用)

- **notificationsテーブル構造不変・payloadは既存`{"latch_id"}`最小参照**(design §3)。latchesテーブルも不変(responses配列は既存列の読取のみ)
- **build_pushの引数・戻り値の型は不変**(`(notification_type: str) -> tuple[str, str]`・design §2.3)。条件サマリを本文へ混ぜる経路を作らない
- **`_COUNT_DAILY_NOTIFICATIONS`(latch_engine)は変更しない**(`type IN ('proposal', 'nearby_candidate')`のまま=latch_matchedはD-08対象外・design §2.3)
- **muted判定を成立通知へ組み込まない**(design §2.3。宛先はfetch_participant_user_idsの全員)
- **`my_response`のフィールド名・位置**: `NotificationLatchOut.my_response: str | None = None`をproposalの直後に置く(latches APIの`LatchSummaryOut.my_response`と同名・同位置・design §2.1)。値域は`"yes" / "no" / "defer" / None`
- **latch_engine・sweeper・latches/routes.py・notifications/routes.py・sender.py・records.pyは無変更**(design §3「触らないもの」)
- **frontendはview.js・texts.jsのみ**。notice.js(行のHTML組み立てはnotificationLinesのみ依存)・respond.js・detail.js・home.jsは触らない(closedTextの一元化により変更不要)
- **`make lint`(ruff E,F,I,UP,B・format行長88)と `make test` を毎コミット通す**(backendを触るタスク)。frontendタスクは`npm test`
- **全タスクのコミット前に**: 該当検証がグリーン・`git status --short` の差分が§4の一覧にのみ載っていること

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **responses配列のuser_idはstr(uuid)** — `_my_response`は`r.get("user_id") == str(user_id)`の文字列比較(latches実装と同一)。fixturesでuuid.UUIDをそのまま入れると常にNoneになり「未回答」と偽陰性になる → Task 2試験の`resp()`ヘルパーは`str(uid)`で格納(`{"user_id": str(uid), ...}`)し、未回答ケースはpeerの行のみで作る
2. **視点者ごとの抽出混同** — my_responseは一覧APIの呼び出し本人の値。他人の回答が漏れると回答種別の開示(03 §7違反)になる → Task 2試験はpeerのyes行を常に混ぜた上で本人値のみをassert(no/defer/yes/Noneの4ケース)
3. **成立通知がmatched以外で発火する偽陽性** — respondはpartial_accept・rejectedでも通る経路。差し替え先の記録が空でも試験は通るため「呼ばれないこと」の明示assertが要る → Task 3試験2(partial_acceptとrejectedの2経路で`calls == []`・`push.calls == []`の完全列挙)
4. **tx後プッシュの失敗が回答APIを落とす** — 送信はtx外だがtry内。sender契約(例外を握る)に依存するため、未注入(None)と記録スタブの2形で例外なく応答が返ることをピン → Task 3試験1(記録スタブで2件)・試験3(push=Noneでmatched完了)
5. **latch_matched行のlatch=null(削除済みlatch)・解散後status** — 参照先latchが削除・cancelledの場合にお知らせ行が落ちないこと。バックエンドは既存のLEFT JOIN防御(latchブロックごとnull)。フロントはtype分岐のlatch=null→NOTICE_FALLBACK_TEXT・解散後→CLOSED_TEXT → Task 4試験5・6(latch=null・cancelledの2ケース)

---

## 4. スコープ(作成・変更するファイル一覧)

作成:

```text
docs/plans/M3/m3-g3fix-report.md                          (Task 6。報告ファイル)
```

変更(design §3・新規テストファイルゼロ=すべて既存ファイルへの追記):

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/src/latch/notifications/types.py` | `NOTIFICATION_MATCHED = "latch_matched"`追加(正本docstring更新) | 1 |
| `backend/src/latch/notifications/templates.py` | `PUSH_BODY_MATCHED`定数・build_pushへlatch_matched分岐 | 1 |
| `backend/tests/unit/notifications/test_templates.py` | 全文ピン・写像・forbiddenループへlatch_matched追加(新規試験関数なし) | 1 |
| `backend/src/latch/notifications/store.py` | `_SELECT_NOTIFICATIONS_PAGE`へ`l.responses`追加(r[11])・`LatchJoinedRow`へ`responses`フィールド(default_factory=list) | 2 |
| `backend/src/latch/notifications/schemas.py` | `NotificationLatchOut`へ`my_response: str \| None = None`(proposalの直後) | 2 |
| `backend/src/latch/notifications/service.py` | `_my_response`ヘルパー(latches/service.py:539の複製)・list()のlatch組み立てへmy_response | 2 |
| `backend/tests/unit/notifications/test_notifications_service.py` | `_page_row`へ`latch_responses`引数・my_response抽出試験+1件 | 2 |
| `backend/src/latch/latches/store.py` | `_INSERT_MATCHED_NOTIFICATION`SQL・`insert_matched_notifications`関数(宛先取得は**既存`fetch_participant_user_ids`を再利用**・design §2.4と同一形のため新規不要) | 3 |
| `backend/src/latch/latches/service.py` | respond()のmatched分岐へ通知先行書き込み+tx後`_send_matched_pushes`・`__init__`/`make_latches_service`へpush注入 | 3 |
| `backend/src/latch/main.py` | lifespanのbuild_latchesで`build_push_sender(clock, settings)`を注入 | 3 |
| `backend/tests/unit/latches/test_latches_service.py` | `_RecordingPush`スタブ・成立通知3試験(+3件) | 3 |
| `frontend/src/latch/texts.js` | `MATCHED_NOTICE_TITLE`新設(導線は既存`NEXT_ACTION_TEXT`流用) | 4 |
| `frontend/src/latch/view.js` | notificationLines終了行を`closedText(latch)`へ・`latch_matched`分岐追加・コメント更新 | 4 |
| `frontend/tests/latch-notice.test.js` | my_response分岐・latch_matched行・hrefの6試験追加 | 4 |
| `backend/tests/integration/test_notifications_api.py` | `_user_with_subject`ヘルパー+test_9成立E2E追記(収集確認のみ・実行はスーパーバイザー検証時) | 5 |

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`・`frontend/node_modules/`。

## 5. 禁止(触ってはいけないもの・スコープ外と判断する基準)

- **`backend/alembic/`(head=0006不変)・`backend/pyproject.toml`・`backend/uv.lock`・`frontend/package.json`・`frontend/package-lock.json`・`frontend/vite.config.mjs`・`frontend/vitest.config.mjs`・`Makefile`・`compose.yaml`・`docker/`・`.env`系**
- **`backend/src/latch/worker/`配下すべて**(sweeper・latch_engine・group_engine等。提案・近傍・申告の各通知は不変・design §3)。**`backend/src/latch/notifications/routes.py`・`sender.py`・`records.py`**(応答モデル・送信IF・記録は不変)。**`backend/src/latch/latches/routes.py`**(応答モデルは無変更)
- **frontendの`src/latch/notice.js`・`respond.js`・`detail.js`・`home.js`・`chat.js`・`report.js`・`src/api/`・`src/ui/`**(closedText一元化により変更不要・design §3)
- **`docs/`の既存ファイル**(01〜12・learn・testassets・reviews)。**`docs/plans/STATUS.md`**(スーパーバイザー管理)。`docs/plans/`の既存ファイル(M0〜M3のdesign・plan・report)。design §5のdocs改版候補は**本単位では整形のみで本体は触らない**(改版はG3判定後の別判断)
- **`backend/tests/`・`frontend/tests/`の§4に列挙した以外の既存試験への変更**(design §4影響調査表で「対応なし」の試験は1行も触らない。既存試験への追記はtest_templates・test_notifications_service・test_latches_service・test_notifications_api・latch-notice.test.jsの5ファイルのみ)
- **`make test-ci` / `make migrate` / docker系コマンド全般**: §0のとおり(実行はスーパーバイザー検証時)
- **実API呼び出し**: `make g1-gate`・`make g2-gate`・`make embed-smoke`・`make jev-smoke` は起動しない
- スコープ外と判断する基準(必要になったと感じても作らない):
  - **競合クローズされた側(他LATCH成立でcancelled)への不成立通知** → docs規定なし・design §2.4で範囲外明記
  - **cancelled(Intent停止削除)の操作履歴文言** → my_responseからは判定不能・詳細画面と同一の限界(design §2.2)。CLOSED_TEXTのまま
  - **D-08上限SQL・muted判定への干渉** → design §2.3で対象外と確定済み
  - **`my_response`のlatches API側変更** → 既に実装済み(ws-1)。notifications側の追加のみ
  - **docs 01/03/05/06/08の本体追記** → design §5の改版候補表の処理はG3判定後
  - **FCM実送信・Web Push実装** → G3後(M4)。本単位はStubPushSender経路のみ
- mainブランチへのコミット・push・マージ。

## 6. 完了条件(テストで証明できる形。Task 6で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(unit 1215→**1219**見込み=+4〔test_notifications_service +1・test_latches_service +3〕。実測が異なる場合は理由を報告書へ記録)
   検証: `make lint && make test` — ともにexit 0・全件数を報告書に記録
2. **frontend `npm test` がグリーン**(217→**223**見込み=+6)
   検証: `cd frontend && npm test` — exit 0・全件数を報告書に記録
3. **integration新規1試験(test_9)が収集できる**(実行はスーパーバイザー検証時)
   検証: `cd backend && uv run pytest --collect-only tests/integration/test_notifications_api.py -q` — exit 0・**test_9含む13件**(main基準12件+1)。かつ `uv run pytest --collect-only -q | tail -1` が**1480件**(=1475+5〔unit+4・integration+1〕)
4. **テストファイルbasenameがbackend/tests配下全体で一意**(本単位は新規ファイルなしだが確認は行う)
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし(空)
5. **マイグレーション・依頼・Makefile・compose・docker・frontend設定に差分なし**
   検証: `git diff main -- backend/alembic backend/pyproject.toml backend/uv.lock frontend/package.json frontend/package-lock.json frontend/vite.config.mjs frontend/vitest.config.mjs Makefile compose.yaml docker` — 出力なし(空)
6. **変更ファイルが§4の一覧どおり(変更14ファイル+report=15エントリ)**
   検証: Task 6の報告コミット後に `git diff --name-only main | sort` が§4の一覧(report込み)と完全一致。`git status --short` が空
7. **`make test-ci` は実行していないこと**(§0規律・報告書に「test-ci=スーパーバイザー検証待ち」と記録)
8. **成立通知の構造ピンが試験で証明されていること**: ①matched時のみ通知INSERT(unit試験2)②宛先=参加者全員(unit試験1)③プッシュbody=第三汎用文byte同一(templates全文ピン+Task 5 E2E)④D-08対象外=`_COUNT_DAILY_NOTIFICATIONS`無変更(§5のgit diff検査で証明)

## 7. 報告形式

**結果ファイル**: `docs/plans/M3/m3-g3fix-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M3 m3-g3fix(⑫my_response+③成立通知)実行報告

- ブランチ: m3-g3fix / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る(全件数も。期待unit 1219=1215+4)> |
| 2 | frontend npm test | PASS/FAIL | <出力末尾(期待223 passed=217+6)> |
| 3 | integration収集 | PASS/FAIL | <collect-only出力(期待: test_notifications_api 13件・全体1480)> |
| 4 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 5 | マイグレーション等無変更 | PASS/FAIL | <git diff出力(空なら「空」)> |
| 6 | 変更ファイル=§4の一覧 | PASS/FAIL | <git diff --name-only出力> |
| 7 | test-ci未実行 | PASS | test-ci=スーパーバイザー検証待ち(§0規律) |
| 8 | 成立通知の構造ピン | PASS | ①〜④はTask 1〜3・5の試験コードと§5検査で証明(該当試験名を列挙) |

## design §6 裁定事項の実装対応
- §6-1(終了行=詳細画面同一文言): view.js終了行をclosedText(latch)へ(ANSWERED_TEXT流用・文言追加なし)
- §6-2(アプリ内=見出し+導線+バッジ): MATCHED_NOTICE_TITLE+NEXT_ACTION_TEXT+STATUS_TEXT。条件サマリ不含
- §6-3(プッシュ本文): PUSH_BODY_MATCHED=「LATCHが成立しました。\nチャットでメッセージを確認しましょう。」
- §6-4(type名): NOTIFICATION_MATCHED="latch_matched"
- §6-5(全員へ同時送信): fetch_participant_user_idsの全員(本人含む)
- §6-6(matched経路追加時の追随): respond()の該当箇所へコメント記載(<実際のコメント行を引用>)

## 固定値の変更有無(本計画§9)
- PUSH_BODY_MATCHED・MATCHED_NOTICE_TITLE・type値: 変更なし / 変更あり(<前→後+理由>)
- my_responseの位置・値域: 変更なし / 変更あり
- その他§9のIF確定事項: 変更なし / 変更あり(<前→後+理由>)

## (スーパーバイザー検証時の手順・結果記入欄)
1. `make lint && make test` — unit 1219 passed
2. `cd frontend && npm test` — 223 passed
3. `docker compose build api` — **運用ルール4(必須。api常設が新コードでないとtest_9の③GET検証が旧応答になる)**
4. `make test-ci` — **1480 passed**(=1475+5)・purge-match-sub実行をMakefile出力で確認
5. lint再実行(test-ciにlintが含まれないため・ws-4運用メモ)
6. basename一意確認・alembic head=0006不変
7. 残存確認: m3ws3-系notificationsはfield teardownで掃除済み(test_9も同一field構成)
8. STATUSのG3判定資料へ反映(⑫③の解消証拠=test_9ログ+latch-notice試験)

## コミット一覧
<git log --oneline main..HEAD>

## 補足(詰まった点・判断した点があれば)
<自由記述>
```

---

## 8. 実装ステップ(TDD: 各Task「失敗試験→red確認→実装→green確認→コミット」の順。Task 1〜6の順で実行する)

### Task 1: 通知type定数と第三汎用文(types.py・templates.py)

**Files:**
- Modify: `backend/src/latch/notifications/types.py`
- Modify: `backend/src/latch/notifications/templates.py`
- Test: `backend/tests/unit/notifications/test_templates.py`(既存3試験関数へ追記・新規関数なし)

**Interfaces:**
- Consumes: 既存 `PUSH_TITLE`・`PUSH_BODY_LATCH`・`PUSH_BODY_NOTICE`・`build_push`(templates.py)・既存3type定数(types.py)
- Produces: `NOTIFICATION_MATCHED = "latch_matched"`(types.py・Task 3/5がimport)・`PUSH_BODY_MATCHED`(templates.py・Task 5のE2Eがbyte比較で使用)・`build_push("latch_matched") == (PUSH_TITLE, PUSH_BODY_MATCHED)`

- [ ] **Step 1: 失敗試験を書く(test_templates.pyの3試験へlatch_matched分を追加)**

`backend/tests/unit/notifications/test_templates.py` を次のとおり変更する(import節2箇所+3試験関数)。

import節(templates側):

```python
from latch.notifications.templates import (
    PUSH_BODY_LATCH,
    PUSH_BODY_MATCHED,
    PUSH_BODY_NOTICE,
    PUSH_TITLE,
    build_push,
)
```

import節(types側):

```python
from latch.notifications.types import (
    NOTIFICATION_ATTENDANCE_REQUEST,
    NOTIFICATION_MATCHED,
    NOTIFICATION_NEARBY,
    NOTIFICATION_PROPOSAL,
)
```

`test_push_constants_full_text` を次へ置き換える(docstringと末尾assert追加):

```python
def test_push_constants_full_text():
    """4定数の全文ピン(design §2.4初期値・03 §4様式・m3-g3fix裁定3)。"""
    assert PUSH_TITLE == "LATCH"
    assert PUSH_BODY_LATCH == "LATCH候補があります。\n詳細はアプリでご確認ください。"
    assert (
        PUSH_BODY_NOTICE
        == "LATCHからのお知らせがあります。\n詳細はアプリでご確認ください。"
    )
    assert (
        PUSH_BODY_MATCHED
        == "LATCHが成立しました。\nチャットでメッセージを確認しましょう。"
    )
```

`test_build_push_maps_all_types` の末尾(`assert build_push(NOTIFICATION_ATTENDANCE_REQUEST) == ...`の直後)へ1行追加。追記後の試験関数全体:

```python
def test_build_push_maps_all_types():
    """全typeが(title, body)を返す。proposal/nearbyは同一文言(§2.4承認②)。"""
    latch_text = (PUSH_TITLE, PUSH_BODY_LATCH)
    assert build_push(NOTIFICATION_PROPOSAL) == latch_text
    assert build_push(NOTIFICATION_NEARBY) == latch_text
    assert build_push(NOTIFICATION_ATTENDANCE_REQUEST) == (
        PUSH_TITLE,
        PUSH_BODY_NOTICE,
    )
    assert build_push(NOTIFICATION_MATCHED) == (PUSH_TITLE, PUSH_BODY_MATCHED)
```

`test_build_push_output_has_no_condition_summary` のtypeループへ`NOTIFICATION_MATCHED`を追加:

```python
    for ntype in (
        NOTIFICATION_PROPOSAL,
        NOTIFICATION_NEARBY,
        NOTIFICATION_ATTENDANCE_REQUEST,
        NOTIFICATION_MATCHED,
    ):
```

- [ ] **Step 2: red確認**

Run: `cd backend && uv run pytest tests/unit/notifications/test_templates.py -v`
Expected: **FAIL/ERROR** — `ImportError: cannot import name 'PUSH_BODY_MATCHED'`(PUSH_BODY_MATCHED・NOTIFICATION_MATCHEDが未定義のため)

- [ ] **Step 3: 実装**

`backend/src/latch/notifications/types.py` を次の全文へ置き換える:

```python
"""通知type定数の正本(M3 ws-3 design §3.1)。

latch_engine(NOTIFICATION_PROPOSAL/NEARBY)・sweeper
(NOTIFICATION_ATTENDANCE_REQUEST)・latches serviceの成立通知
(NOTIFICATION_MATCHED・m3-g3fix)がimport切替で参照する単一ソース。
値はws-6/ws-2実装時の文字列と同一(既存試験の文字列リテラルと無干渉)。
"""

NOTIFICATION_PROPOSAL = "proposal"
NOTIFICATION_NEARBY = "nearby_candidate"
NOTIFICATION_ATTENDANCE_REQUEST = "attendance_request"
NOTIFICATION_MATCHED = "latch_matched"  # m3-g3fix裁定4(既存3値の命名規律に同じ)
```

`backend/src/latch/notifications/templates.py` のimport節・定数節・build_pushを次へ変更する(モジュールdocstringは不変):

```python
from latch.notifications.types import (
    NOTIFICATION_ATTENDANCE_REQUEST,
    NOTIFICATION_MATCHED,
    NOTIFICATION_NEARBY,
    NOTIFICATION_PROPOSAL,
)

PUSH_TITLE = "LATCH"
PUSH_BODY_LATCH = "LATCH候補があります。\n詳細はアプリでご確認ください。"
PUSH_BODY_NOTICE = "LATCHからのお知らせがあります。\n詳細はアプリでご確認ください。"
PUSH_BODY_MATCHED = "LATCHが成立しました。\nチャットでメッセージを確認しましょう。"


def build_push(notification_type: str) -> tuple[str, str]:
    """type→(title, body)の写像(design §2.4)。

    proposal/nearby_candidateは同一文言(文面を分けると「閾値未満の候補
    である」ことがOS経路〔ロック画面等〕に漏れるため・承認②)。
    attendance_requestは第二汎用文(08 §2.6趣旨の準用・承認①)。
    latch_matchedは第三汎用文(成立連絡・m3-g3fix design §2.3。条件
    サマリではなく成立後のチャット導線を伝える・裁定3)。
    未知typeはValueError(実装バグの即時顕在化・design §2.7)。
    """
    if notification_type in (NOTIFICATION_PROPOSAL, NOTIFICATION_NEARBY):
        return PUSH_TITLE, PUSH_BODY_LATCH
    if notification_type == NOTIFICATION_ATTENDANCE_REQUEST:
        return PUSH_TITLE, PUSH_BODY_NOTICE
    if notification_type == NOTIFICATION_MATCHED:
        return PUSH_TITLE, PUSH_BODY_MATCHED
    raise ValueError(f"unknown notification_type: {notification_type!r}")
```

- [ ] **Step 4: green確認**

Run: `cd backend && uv run pytest tests/unit/notifications/test_templates.py -v`
Expected: PASS(4件・件数不変=新規試験関数なし)

- [ ] **Step 5: 全unit検査してコミット**

Run: `make lint && make test`
Expected: グリーン(unit 1215件不変・本Taskは試験関数を追加していないため)

```bash
git add backend/src/latch/notifications/types.py backend/src/latch/notifications/templates.py backend/tests/unit/notifications/test_templates.py
git commit -m "feat: latch_matched notification type and third generic push body"
```

### Task 2: お知らせ一覧のmy_response(notifications store・schemas・service)

**Files:**
- Modify: `backend/src/latch/notifications/store.py`
- Modify: `backend/src/latch/notifications/schemas.py`
- Modify: `backend/src/latch/notifications/service.py`
- Test: `backend/tests/unit/notifications/test_notifications_service.py`(fixture拡張+新規試験1件)

**Interfaces:**
- Consumes: 既存 `LatchJoinedRow`・`NotificationPageRow`・`select_notifications_page`(store.py)・既存 `NotificationsService.list`(service.py)・テスト既存資産 `_FakeConn`・`_FakeEngine`・`_StubStore`・`_service`・`_page_row`・`_monkeypatch_store`
- Produces: `LatchJoinedRow.responses: list[dict]`(default_factory=list・Task 2内で消費)・`NotificationLatchOut.my_response: str | None = None`(JSON応答の`latch.my_response`・Task 4のfrontendが参照)・`_my_response(responses, user_id) -> str | None`(service.py内private)

- [ ] **Step 1: 失敗試験を書く(fixture拡張+新規試験)**

`backend/tests/unit/notifications/test_notifications_service.py` へ2箇所変更する。

①`_page_row`を`latch_responses`引数つきへ置き換える(LatchJoinedRowへ`responses=`を渡す):

```python
def _page_row(n: int, *, latch=True, latch_responses=None) -> NotificationPageRow:
    lid = uuid_mod.UUID(f"00000000-0000-4000-8000-{n:012d}")
    latch_row = (
        LatchJoinedRow(
            id=lid,
            status="proposed",
            response_deadline=SVC_NOW + timedelta(hours=2),
            expires_at=SVC_NOW + timedelta(days=5),
            completed_at=None,
            proposal={"headcount": 2, "match_level": "medium"},
            responses=latch_responses or [],
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
```

②ファイル末尾(`test_list_latch_null_defensive`の後)へ新規試験を追記:

```python
async def test_list_my_response_extracted_per_viewer():
    """my_response抽出(m3-g3fix design §2.1): responsesから本人の回答を
    視点者ごとに抽出。no/defer/yes/未回答(None)・latch=null行はlatch
    ブロックごとnull。入出力ケースはunit/latchesの応答試験(my_response)
    と対応させ、latches/service._my_responseとのロジック一致を担保
    (ヘルパーは複製・importはモジュール規律に反するため)。"""
    uid = uuid_mod.UUID("00000000-0000-4000-8000-0000000000e1")
    peer = "00000000-0000-4000-8000-0000000000e2"  # str(uuid)で格納(05 §2)

    def resp(mine: str | None) -> list[dict]:
        rows = [{"user_id": peer, "response": "yes"}]
        if mine is not None:
            rows.append({"user_id": str(uid), "response": mine})
        return rows

    cases = [
        (resp("no"), "no"),
        (resp("defer"), "defer"),
        (resp("yes"), "yes"),
        (resp(None), None),  # 未回答(本人の行がない)
    ]
    for latch_responses, expected in cases:
        conn = _FakeConn(
            page_rows=[_page_row(1, latch_responses=latch_responses)],
            user_id=uid,
        )
        service = _service(conn)
        _monkeypatch_store(_StubStore(conn))
        items, _ = await service.list(
            auth_provider=PROVIDER, auth_subject=SUBJECT, limit=20
        )
        assert items[0].latch.my_response == expected, latch_responses
    # latch=null防御行はlatchブロックごとnull(my_responseも出ない)
    conn = _FakeConn(page_rows=[_page_row(2, latch=False)], user_id=uid)
    service = _service(conn)
    _monkeypatch_store(_StubStore(conn))
    items, _ = await service.list(
        auth_provider=PROVIDER, auth_subject=SUBJECT, limit=20
    )
    assert items[0].latch is None
```

- [ ] **Step 2: red確認**

Run: `cd backend && uv run pytest tests/unit/notifications/test_notifications_service.py::test_list_my_response_extracted_per_viewer -v`
Expected: **FAIL** — `TypeError: LatchJoinedRow.__init__() got an unexpected keyword argument 'responses'`(responsesフィールド未実装のため)

- [ ] **Step 3: 実装(3ファイル)**

`backend/src/latch/notifications/store.py`:

①import節のdataclass行へ`field`を追加:

```python
from dataclasses import dataclass, field
```

②`_SELECT_NOTIFICATIONS_PAGE`のSELECT列へ`l.responses`を追加(末尾・r[11]):

```python
_SELECT_NOTIFICATIONS_PAGE = text("""
    SELECT n.id, n.type, n.payload->>'latch_id', n.read_at, n.created_at,
           l.id, l.status, l.response_deadline, l.expires_at, l.completed_at,
           l.proposal, l.responses
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
```

③`LatchJoinedRow`へ`responses`フィールドを追加(default_factory=list):

```python
@dataclass(frozen=True)
class LatchJoinedRow:
    """LEFT JOINで埋めたlatch要素(design §2.5の応答latchブロック)。"""

    id: uuid.UUID
    status: str
    response_deadline: datetime
    expires_at: datetime
    completed_at: datetime | None
    proposal: dict
    # 本人の回答抽出用(m3-g3fix design §2.1・latches一覧と同一構造)。
    # default_factory=list=既存fixture(未指定)互換
    responses: list[dict] = field(default_factory=list)
```

④`select_notifications_page`内のLatchJoinedRow生成へ`responses=`を追加:

```python
        if r[5] is not None:  # LEFT JOIN成立(l.id)
            latch = LatchJoinedRow(
                id=_coerce_uuid(r[5]),
                status=r[6],
                response_deadline=r[7],
                expires_at=r[8],
                completed_at=r[9],
                proposal=dict(_jsonb(r[10]) or {}),
                responses=list(_jsonb(r[11]) or []),
            )
```

`backend/src/latch/notifications/schemas.py` — `NotificationLatchOut`へ`my_response`を追加(proposalの直後=latches APIのLatchSummaryOutと同名・同位置):

```python
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
    # 自分の回答(yes/no/defer・未回答とlatch行不在はnull)。latches APIの
    # LatchSummaryOut.my_responseと同名・同位置(m3-g3fix design §2.1)。
    # 他者の回答種別は出さない(03 §7)
    my_response: str | None = None
```

`backend/src/latch/notifications/service.py` — 2箇所変更。

①`_to_uuid_or_none`の直後に`_my_response`ヘルパーを追加:

```python
def _my_response(responses: list[dict], user_id: uuid.UUID) -> str | None:
    """responses配列から本人の回答抽出(m3-g3fix design §2.1)。

    latches/service.py:539の同名privateヘルパーと同一ロジックの複製
    (private関数のimportはモジュール規律に反するため)。両者の一致は
    unit試験の入出力対応で担保(no/defer/None・§8 Task 2)。
    """
    for r in responses:
        if r.get("user_id") == str(user_id):
            return r.get("response")
    return None
```

②`list()`内の`NotificationLatchOut(...)`組み立てへ`my_response=`を追加:

```python
                latch_out = NotificationLatchOut(
                    id=r.latch.id,
                    status=r.latch.status,
                    response_deadline=r.latch.response_deadline,
                    expires_at=r.latch.expires_at,
                    completed_at=r.latch.completed_at,
                    proposal=r.latch.proposal,
                    my_response=_my_response(r.latch.responses, user_id),
                )
```

- [ ] **Step 4: green確認**

Run: `cd backend && uv run pytest tests/unit/notifications/test_notifications_service.py -v`
Expected: PASS(12件=既存11+新規1)

- [ ] **Step 5: 全unit検査してコミット**

Run: `make lint && make test`
Expected: グリーン(unit **1216**=1215+1)

```bash
git add backend/src/latch/notifications/store.py backend/src/latch/notifications/schemas.py backend/src/latch/notifications/service.py backend/tests/unit/notifications/test_notifications_service.py
git commit -m "feat: my_response in notifications list latch block (A/issue-12)"
```

### Task 3: 成立通知の先行書き込みとtx後プッシュ(latches store・service・main.py)

**Files:**
- Modify: `backend/src/latch/latches/store.py`
- Modify: `backend/src/latch/latches/service.py`
- Modify: `backend/src/latch/main.py`
- Test: `backend/tests/unit/latches/test_latches_service.py`(_RecordingPush+新規試験3件)

**Interfaces:**
- Consumes: Task 1の `NOTIFICATION_MATCHED`(types.py)・既存 `fetch_participant_user_ids`(latches/store.py:623・§10-6)・`PushSender` Protocol(notifications/sender.py・`send(*, user_id, notification_type, latch_id)`)・`build_push_sender`(notificationsパッケージexport済み・§10-8)・テスト既存資産 `_latch_row`・`_ret`・`ScriptedEngine`・`ME`・`PEER`・`I1`・`NOW`・`FakeClock`・`EvalRow`
- Produces: `insert_matched_notifications(conn, *, latch_id, user_ids, now) -> None`(store.py・respond tx内から呼ぶ)・`LatchesService(..., push: PushSender | None = None)`・`make_latches_service(..., push=None)`・`LatchesService._send_matched_pushes(latch_id, targets)`

- [ ] **Step 1: 失敗試験を書く(3試験+スタブ)**

`backend/tests/unit/latches/test_latches_service.py` へ変更する。

①ファイル末尾へ`_RecordingPush`スタブと3試験を追記する。

```python
# -- m3-g3fix: 成立通知の先行書き込み+tx後プッシュ(design §2.4) --


class _RecordingPush:
    """PushSenderスタブ(送信呼び出しを記録・sender.pyのIFと同形)。"""

    def __init__(self):
        self.name = "recording"
        self.calls: list[dict] = []

    async def send(self, *, user_id, notification_type, latch_id):
        self.calls.append(
            {"user_id": user_id, "type": notification_type, "latch_id": latch_id}
        )


def _patch_matched_common(monkeypatch) -> None:
    """matched経路の共通差し替え(通知宛先・Calibration材料・イベント系)。"""
    from latch.latches import store as store_mod
    from latch.latches.calibration import EvalRow

    async def _event(
        conn, latch_id, from_status=None, to_status=None, user_id=None, now=None
    ):
        return None

    async def _match(conn, ids, now):
        return list(ids)

    async def _conflicting(conn, self_id, member_ids):
        return []

    async def _cancel(conn, latch_id):
        return False

    async def _calibration(conn=None, **kwargs):
        return uuid.uuid4()

    async def _participant_users(conn, intent_ids):
        return [ME, PEER]

    eval_row = EvalRow(
        jev_result={
            "would_a_accept_b": 0.6,
            "would_b_accept_a": 0.7,
            "jev_5axis": {},
            "provider": "typesafe_jev",
            "model": "jev-1.13.0",
        },
        latch_score=Decimal("0.85"),
        updated_at=NOW,
    )
    monkeypatch.setattr(store_mod, "insert_latch_event", _event)
    monkeypatch.setattr(store_mod, "match_intents", _match)
    monkeypatch.setattr(store_mod, "select_conflicting_latches", _conflicting)
    monkeypatch.setattr(store_mod, "cancel_latch", _cancel)
    monkeypatch.setattr(store_mod, "insert_calibration", _calibration)
    monkeypatch.setattr(store_mod, "fetch_pair_rows", _ret([eval_row]))
    monkeypatch.setattr(store_mod, "fetch_intents_structured", _ret([{}, {}]))
    monkeypatch.setattr(store_mod, "fetch_participant_user_ids", _participant_users)


async def test_respond_matched_writes_notifications_and_pushes(monkeypatch):
    """matched成立時: 参加者全員への通知先行書き込み+tx後プッシュ2件
    (m3-g3fix design §2.4・03 §4 D-20同時送信・最後にYESを入れた本人を含む)。"""
    from latch.latches import store as store_mod

    notify_calls: list[dict] = []

    async def _notify_insert(conn, *, latch_id, user_ids, now):
        notify_calls.append({"latch_id": latch_id, "users": list(user_ids)})

    _patch_matched_common(monkeypatch)
    monkeypatch.setattr(store_mod, "insert_matched_notifications", _notify_insert)
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))

    push = _RecordingPush()
    row = _latch_row(responses=[{"user_id": str(PEER), "response": "yes"}])
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    monkeypatch.setattr(store_mod, "update_response", _ret((row.id, "matched")))
    svc = LatchesService(clock=FakeClock(NOW), engine=ScriptedEngine([]), push=push)
    out = await svc.respond(
        auth_provider="google", auth_subject="s", latch_id=row.id, response="yes"
    )
    assert out.status == "matched"
    # 通知先行書き込み: 1回・宛先は参加者全員(本人MEを含む)
    assert len(notify_calls) == 1
    assert notify_calls[0]["latch_id"] == row.id
    assert notify_calls[0]["users"] == [ME, PEER]
    # tx後プッシュ: 全員へlatch_matched(D-08上限は消費しない・design §2.3)
    assert len(push.calls) == 2
    assert {c["user_id"] for c in push.calls} == {ME, PEER}
    assert all(c["type"] == "latch_matched" for c in push.calls)
    assert all(c["latch_id"] == row.id for c in push.calls)


async def test_respond_non_matched_no_notification(monkeypatch):
    """partial_accept・rejectedでは成立通知を書かない・送らない
    (matched遷移のみが通知のトリガー・design §2.4)。"""
    from latch.latches import store as store_mod

    notify_calls: list[dict] = []

    async def _notify_insert(conn, *, latch_id, user_ids, now):
        notify_calls.append({"latch_id": latch_id, "users": list(user_ids)})

    _patch_matched_common(monkeypatch)
    monkeypatch.setattr(store_mod, "insert_matched_notifications", _notify_insert)
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))

    push = _RecordingPush()
    row = _latch_row()
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    svc = LatchesService(clock=FakeClock(NOW), engine=ScriptedEngine([]), push=push)

    # 1人目yes → partial_accept(まだ成立していない)
    monkeypatch.setattr(store_mod, "update_response", _ret((row.id, "partial_accept")))
    out1 = await svc.respond(
        auth_provider="google", auth_subject="s", latch_id=row.id, response="yes"
    )
    assert out1.status == "partial_accept"
    # no → rejected
    monkeypatch.setattr(store_mod, "update_response", _ret((row.id, "rejected")))
    out2 = await svc.respond(
        auth_provider="google", auth_subject="s", latch_id=row.id, response="no"
    )
    assert out2.status == "rejected"
    assert notify_calls == []  # 成立通知はmatched時のみ(完全列挙)
    assert push.calls == []


async def test_respond_matched_push_none_is_noop(monkeypatch):
    """push未注入(既定None)でもmatched成立は例外なく完了(sweeperと同型)。"""
    from latch.latches import store as store_mod

    async def _notify_insert(conn, *, latch_id, user_ids, now):
        return None

    _patch_matched_common(monkeypatch)
    monkeypatch.setattr(store_mod, "insert_matched_notifications", _notify_insert)
    monkeypatch.setattr(store_mod, "fetch_user_id", _ret(ME))
    monkeypatch.setattr(store_mod, "select_participant_intent", _ret(I1))

    row = _latch_row(responses=[{"user_id": str(PEER), "response": "yes"}])
    monkeypatch.setattr(store_mod, "select_latch_for_update", _ret(row))
    monkeypatch.setattr(store_mod, "update_response", _ret((row.id, "matched")))
    out = await _svc(ScriptedEngine([])).respond(  # push省略=既定None
        auth_provider="google", auth_subject="s", latch_id=row.id, response="yes"
    )
    assert out.status == "matched"
```

- [ ] **Step 2: red確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_latches_service.py -v -k "matched_writes or non_matched_no or push_none"`
Expected: **FAIL/ERROR** — `AttributeError: <module 'latch.latches.store'> has no attribute 'insert_matched_notifications'`(monkeypatch.setattrが対象属性の不存在で失敗)

- [ ] **Step 3: 実装(3ファイル)**

`backend/src/latch/latches/store.py` — 2箇所変更。

①import節へtype定数を追加(latch_engine/sweeperと同じくtype正本をimport・循環なし。notifications.typesは無依存モジュール):

```python
from latch.latches.calibration import EvalRow, PairEvalRow
from latch.notifications.types import NOTIFICATION_MATCHED
```

②`_INSERT_LATCH_EVENT`定数の直後にSQLと関数を追加(§10-2・§10-3と同一SQL形):

```python
# 成立通知の先行書き込み(m3-g3fix design §2.4。payloadは{"latch_id"}の
# 最小参照 — sweeper._INSERT_ATTENDANCE_NOTIFICATION・latch_engine.
# _INSERT_NOTIFICATIONと同一SQL形)
_INSERT_MATCHED_NOTIFICATION = text("""
    INSERT INTO notifications (user_id, type, payload, created_at)
    VALUES (CAST(:user_id AS uuid), :type, CAST(:payload AS jsonb), :now)
""")
```

(関数は`insert_latch_event`の直後に置く)

```python
async def insert_matched_notifications(
    conn: AsyncConnection,
    *,
    latch_id: uuid.UUID,
    user_ids: list[uuid.UUID],
    now: datetime,
) -> None:
    """成立通知の参加者全員への先行書き込み(m3-g3fix design §2.4)。

    宛先は呼び出し側(respond tx)がfetch_participant_user_idsで取得した
    全員(最後にYESを入れた本人を含む・03 §4 D-20同時送信)。typeは
    NOTIFICATION_MATCHED(D-08日次上限の対象外・design §2.3)。
    """
    payload = json.dumps({"latch_id": str(latch_id)})
    for uid in user_ids:
        await conn.execute(
            _INSERT_MATCHED_NOTIFICATION,
            {
                "user_id": uid,
                "type": NOTIFICATION_MATCHED,
                "payload": payload,
                "now": now,
            },
        )
```

`backend/src/latch/latches/service.py` — 5箇所変更。

①import節へ2行追加:

```python
from latch.notifications.sender import PushSender
from latch.notifications.types import NOTIFICATION_MATCHED
```

②`__init__`へpush注入を追加:

```python
    def __init__(
        self,
        *,
        clock: Clock,
        engine: AsyncEngine,
        geo=None,
        block_cache=None,
        push: PushSender | None = None,
    ) -> None:
        self._clock = clock
        self._engine = engine
        self._geo = geo  # GeoService | None(Noneならarea_name=None)
        self._block_cache = block_cache  # BlockCache | None(NoneならDB直読み)
        self._push = push  # PushSender | None(Noneなら成立通知pushはno-op)
```

③`respond()` — 冒頭に`matched_user_ids`初期化を追加(tryの直前):

```python
        matched_user_ids: list[uuid.UUID] = []
        try:
```

④`respond()` — matched分岐へ通知先行書き込みを追加(`await self._on_matched(conn, row, now)`の直後)・末尾の`return _summary(...)`を`out = _summary(...)`へ変更し、with抜け後に送信してreturn:

```python
                if new_status == "matched":
                    await self._on_matched(conn, row, now)
                    # 成立通知の先行書き込み(参加者全員・m3-g3fix design §2.4)。
                    # matched遷移を書く経路は現在respondのみ(05 §6遷移表・
                    # sweeper/latch_engine/deletionはmatchedを書かない)。
                    # 今後経路が増えた場合は通知挿入の追随が必要(design §6裁定6)
                    matched_user_ids = await store.fetch_participant_user_ids(
                        conn, row.intent_ids
                    )
                    await store.insert_matched_notifications(
                        conn, latch_id=row.id, user_ids=matched_user_ids, now=now
                    )
                if new_status in _TERMINAL_FOR_CALIBRATION:
                    await self._create_calibration(
                        conn,
                        row=row,
                        after=after,
                        matched=new_status == "matched",
                        now=now,
                    )
                out = _summary(
                    row=row,
                    status=new_status,
                    my_response=response,
                    after=after,
                )
            # txコミット後の成立通知プッシュ(ws-3 design §2.1案Aと同型・
            # m3-g3fix design §2.4)。未注入(None)は_send_matched_pushes内でno-op
            if matched_user_ids:
                await self._send_matched_pushes(row.id, matched_user_ids)
            return out
```

(=`async with self._engine.begin() as conn:` ブロック内の`return _summary(...)`を`out = _summary(...)`へ替え、with直後にif文2行と`return out`を置く。except節は不変)

⑤`_on_matched`の直後に`_send_matched_pushes`を追加(sweeper._send_attendance_pushesと同型・§10-4):

```python
    async def _send_matched_pushes(
        self, latch_id: uuid.UUID, targets: list[uuid.UUID]
    ) -> None:
        """txコミット後の成立通知プッシュ送信(m3-g3fix design §2.4)。

        未注入(既存試験構成)はno-op。送信例外はsender側で握る(ws-3 §2.7)。
        本文はbuild_pushの第三汎用文(条件サマリなし・08 §2.6)。
        """
        if self._push is None:
            return
        for user_id in targets:
            await self._push.send(
                user_id=user_id,
                notification_type=NOTIFICATION_MATCHED,
                latch_id=latch_id,
            )
```

⑥ファイル末尾の`make_latches_service`へpush引数を追加:

```python
def make_latches_service(
    *, clock: Clock, engine: AsyncEngine, block_cache=None, push=None
) -> LatchesService:
    """main.py lifespan用の構築(GeoServiceはengineから作る)。"""
    from latch.geo.service import GeoService

    return LatchesService(
        clock=clock,
        engine=engine,
        geo=GeoService(engine),
        block_cache=block_cache,
        push=push,
    )
```

`backend/src/latch/main.py` — 2箇所変更。

①notifications import節へ`build_push_sender`を追加(§10-8・パッケージexport済み):

```python
from latch.notifications import (
    NotificationsError,
    build_push_sender,
    make_notifications_service,
    notifications_router,
)
```

②lifespanの`build_latches`ブロックを変更:

```python
    if build_latches:
        # 成立通知のプッシュ(m3-g3fix design §2.4): APIプロセスでも
        # push_mode設定が有効。stubはネットワーク呼び出しゼロ(例外は
        # sender側で握る・API応答への影響は送信先障害時のtimeout上限のみ)
        app.state.latches_service = make_latches_service(
            clock=app.state.clock,
            engine=engine,
            block_cache=block_cache,
            push=build_push_sender(app.state.clock, settings),
        )
```

- [ ] **Step 4: green確認**

Run: `cd backend && uv run pytest tests/unit/latches/test_latches_service.py -v`
Expected: PASS(全件・既存matched試験を含む。**既存試験は無変更で通る** — `fetch_participant_user_ids`未差し替えの場合ScriptedConnが空行を返し`matched_user_ids == []`で通知も送信もスキップされるため)

- [ ] **Step 5: 全unit検査してコミット**

Run: `make lint && make test`
Expected: グリーン(unit **1219**=1216+3)

```bash
git add backend/src/latch/latches/store.py backend/src/latch/latches/service.py backend/src/latch/main.py backend/tests/unit/latches/test_latches_service.py
git commit -m "feat: latch_matched notification on respond match (B/issue-3)"
```

### Task 4: フロント文言分岐(texts.js・view.js)

**Files:**
- Modify: `frontend/src/latch/texts.js`
- Modify: `frontend/src/latch/view.js`
- Test: `frontend/tests/latch-notice.test.js`(describe追記・6試験)

**Interfaces:**
- Consumes: 既存 `closedText`(view.js:87・§10-9)・既存 `ANSWERED_TEXT`・`NEXT_ACTION_TEXT`・`STATUS_TEXT`(texts.js)・既存テスト資産 `notificationLatch(status, overrides)`・`item(type, latch)`・`LATCH_ID`・`NOW`
- Produces: `MATCHED_NOTICE_TITLE = "LATCHが成立しました。"`(texts.js・Task 4内で消費)・`notificationLines`のlatch_matched分岐

- [ ] **Step 1: 失敗試験を書く**

`frontend/tests/latch-notice.test.js` へ2箇所変更する。

①import節(texts.js側)へ`ANSWERED_TEXT`・`MATCHED_NOTICE_TITLE`・`NEXT_ACTION_TEXT`を追加(アルファベット順の適切な位置へ):

```js
import {
  ANSWERED_TEXT,
  ATTENDANCE_QUESTION,
  CLOSED_TEXT,
  HIDDEN_PROPOSAL_TEXT,
  MATCH_NOTICE_TEXT,
  MATCHED_NOTICE_TITLE,
  NEARBY_NOTICE_TEXT,
  NEXT_ACTION_TEXT,
  NOTICE_EMPTY_NOTE,
  NOTICE_EMPTY_TITLE,
  NOTICE_FALLBACK_TEXT,
  PROPOSAL_NOTICE_TITLE,
} from "../src/latch/texts.js";
```

②ファイル末尾へdescribeを追記:

```js
describe("notificationLines(m3-g3fix: my_response分岐+成立通知行)", () => {
  it("proposal×終了はmy_responseで分岐(no=辞退しました/defer=今回は見送りました/未設定・yes=統一文言)", () => {
    expect(
      notificationLines(
        item("proposal", notificationLatch("rejected", { my_response: "no" })), NOW))
      .toEqual([ANSWERED_TEXT.no]);
    expect(
      notificationLines(
        item("proposal", notificationLatch("expired", { my_response: "defer" })), NOW))
      .toEqual([ANSWERED_TEXT.defer]);
    // 未設定(undefined)・yesは統一文言のまま(詳細画面closedTextと同一・design §2.2)
    expect(notificationLines(item("proposal", notificationLatch("cancelled")), NOW))
      .toEqual([CLOSED_TEXT]);
    expect(
      notificationLines(
        item("proposal", notificationLatch("cancelled", { my_response: "yes" })), NOW))
      .toEqual([CLOSED_TEXT]);
  });

  it("latch_matched×matched: 見出し+チャット導線+バッジ成立済み(条件サマリ不含)", () => {
    expect(notificationLines(item("latch_matched", notificationLatch("matched")), NOW))
      .toEqual([MATCHED_NOTICE_TITLE, NEXT_ACTION_TEXT, "成立済み"]);
  });

  it("latch_matched×completed: バッジは完了", () => {
    expect(
      notificationLines(item("latch_matched", notificationLatch("completed")), NOW))
      .toEqual([MATCHED_NOTICE_TITLE, NEXT_ACTION_TEXT, "完了"]);
  });

  it("latch_matched×解散後(cancelled): CLOSED_TEXTの1行(一文統一)", () => {
    expect(
      notificationLines(item("latch_matched", notificationLatch("cancelled")), NOW))
      .toEqual([CLOSED_TEXT]);
  });

  it("latch_matched×latch=null: フォールバック文言(防御)", () => {
    expect(notificationLines(item("latch_matched", null), NOW))
      .toEqual([NOTICE_FALLBACK_TEXT]);
  });

  it("latch_matched行のhrefは#/latches/{id}(FR-46・変更不要の確認)", () => {
    expect(notificationHref(item("latch_matched", notificationLatch("matched"))))
      .toBe(`#/latches/${LATCH_ID}`);
  });
});
```

- [ ] **Step 2: red確認**

Run: `cd frontend && npm test`
Expected: **FAIL** — latch-notice.test.jsが`MATCHED_NOTICE_TITLE`のimportでモジュール解決エラーになりファイル単位でfail(赤の確認として十分)

- [ ] **Step 3: 実装**

`frontend/src/latch/texts.js` — ws-8追記節(`PROPOSAL_NOTICE_TITLE`の定義群)へ1行追加:

```js
export const MATCHED_NOTICE_TITLE = "LATCHが成立しました。"; // 成立通知行(m3-g3fix裁定2)
```

`frontend/src/latch/view.js` — 3箇所変更。

①import節へ`MATCHED_NOTICE_TITLE`・`NEXT_ACTION_TEXT`を追加(アルファベット順の位置へ):

```js
import {
  ANSWERED_TEXT,
  ATTENDANCE_QUESTION,
  CLOSED_TEXT,
  DEADLINE_CLOSED_TEXT,
  DEADLINE_SOON_TEXT,
  HIDDEN_PROPOSAL_TEXT,
  MATCH_NOTICE_TEXT,
  MATCHED_NOTICE_TITLE,
  MATCH_LEVEL_TEXT,
  NEARBY_NOTICE_TEXT,
  NEXT_ACTION_TEXT,
  NOTICE_FALLBACK_TEXT,
  PROPOSAL_NOTICE_TITLE,
  STATUS_TEXT,
} from "./texts.js";
```

②`notificationLines`を次のとおり置き換える(コメント更新+latch_matched分岐+終了行を`closedText(latch)`へ):

```js
// お知らせ行の文言(type×latch.statusのマトリクス・design §2.3・m3-g3fix §2.5)。
// 終了行はclosedText(latch)で自分の操作履歴(no/defer)へ分岐(03 §7・⑫)。
// nearbyは存在通知のみ。latch_matchedは見出し+導線+バッジ(条件サマリ不含)。
export const notificationLines = (item, nowIso) => {
  if (item.type === "nearby_candidate") return [NEARBY_NOTICE_TEXT];
  if (item.type === "attendance_request") return [ATTENDANCE_QUESTION];
  const latch = item.latch;
  if (!latch) return [NOTICE_FALLBACK_TEXT]; // 防御(全type)
  if (item.type === "latch_matched") {
    // 解散後(statusがexpired等)は一文統一(03 §7)。条件サマリは成立通知行に
    // 含めない(hidden_until_match提案は最小構造のままで表示可否が分かれる)
    if (CLOSED_STATUSES.has(latch.status)) return [CLOSED_TEXT];
    return [
      MATCHED_NOTICE_TITLE,
      NEXT_ACTION_TEXT, // 成立後詳細と同一の導線文言(文言単一ソース)
      STATUS_TEXT[latch.status] ?? latch.status, // バッジ(成立済み/完了)
    ];
  }
  if (CLOSED_STATUSES.has(latch.status)) return [closedText(latch)]; // 操作履歴へ分岐
  if (MATCHED_NOTICE_STATUSES.has(latch.status)) {
    const title = isMinimalProposal(latch)
      ? HIDDEN_PROPOSAL_TEXT
      : [latch.proposal.time_summary, latch.proposal.area_name]
          .filter(Boolean)
          .join(" ");
    return [
      title,
      ...(conditionSummaryLines(latch) ?? []),
      STATUS_TEXT[latch.status] ?? latch.status, // バッジ(回答促しは出さない)
    ];
  }
  // proposed / partial_accept
  if (isMinimalProposal(latch)) {
    return [
      HIDDEN_PROPOSAL_TEXT,
      MATCH_NOTICE_TEXT,
      remainingTimeText(latch.response_deadline, nowIso),
    ];
  }
  return [
    PROPOSAL_NOTICE_TITLE,
    ...(conditionSummaryLines(latch) ?? []),
    MATCH_NOTICE_TEXT,
  ];
};
```

(`notificationHref`は変更不要 — latch_matchedは「nearby以外・latchがあればリンク」の現行条件に自動的に当てはまる・design §2.5)

- [ ] **Step 4: green確認**

Run: `cd frontend && npm test`
Expected: PASS(全ファイル緑・**223 passed**=217+6。既存「proposal×終了3種はCLOSED_TEXT」試験はnotificationLatchヘルパーがmy_responseを設定しないためundefined→CLOSED_TEXTのまま無変更で通る)

- [ ] **Step 5: コミット**

```bash
git add frontend/src/latch/texts.js frontend/src/latch/view.js frontend/tests/latch-notice.test.js
git commit -m "feat: notice closed-line my_response branch and latch_matched row"
```

### Task 5: integration成立E2E追記(test_9・収集確認のみ)

**Files:**
- Modify: `backend/tests/integration/test_notifications_api.py`(ヘルパー1つ+試験1件)

**Interfaces:**
- Consumes: Task 1の `PUSH_BODY_MATCHED`(templates.py)・Task 3の `LatchesService(..., push=)`(latches/service.py)・既存テスト資産 `_clock`・`_user`・`_intent`・`_structured`・`_seed_jev`・`_handle`・`_push_records`・`_user_id_of`・`_cli_idp_token`・`_future`・`BASE_HOURS`・`FAR_EXPIRES_H`・`run_candidate_retrieval`・`StubPushSender`・`LOGGER_NAME`
- Produces: `_user_with_subject(api_client, prefix) -> tuple[dict, str]`(headersとauth_subjectのtuple。test_9内で使用)

- [ ] **Step 1: ヘルパーと試験を追記する**

`backend/tests/integration/test_notifications_api.py` — `_user`ヘルパーの直後に`_user_with_subject`を追記し、ファイル末尾(test_8の後)へtest_9を追記する。

```python
async def _user_with_subject(api_client, prefix: str) -> tuple[dict, str]:
    """_userのsubjectつき版(m3-g3fix): LatchesService.respondをテスト
    プロセス内で直接呼ぶためにauth_subjectが必要(JWT不要・DB解決)。"""
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
        json={"display_name": "m3g3fix", "birth_date": "1990-04-01", "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers, subject


async def test_9_matched_notification_e2e(api_client, db_engine, field, caplog):
    """1対1成立E2E(m3-g3fix design §4新設4): 両者yes→matched→
    ①双方のnotificationsへtype=latch_matched行(payload={latch_id})
    ②プッシュドライラン記録2件(第三汎用文のbyte比較・参加者全員=本人含む)
    ③GET /v1/notificationsのlatch.my_responseが回答者視点で正しい値(yes)。

    回答はテストプロセス内のLatchesService(push=StubPushSender)で直接実行
    (test_6のsweeper直接構成と同型・api常設プロセスのpush記録はcaplogへ
    出ないため)。③はapi常設プロセスが新コード(docker compose build api後)
    であることを要する=スーパーバイザー検証時のtest-ciで有効になる検証。
    """
    from latch.latches.service import LatchesService
    from latch.notifications.templates import PUSH_BODY_MATCHED

    clock = _clock()
    start = _future(BASE_HOURS)
    expires = _future(FAR_EXPIRES_H)
    ha, subject_a = await _user_with_subject(api_client, field)
    a = await _intent(
        api_client, db_engine, ha, _structured(start=start, expires=expires)
    )
    hb, subject_b = await _user_with_subject(api_client, field)
    b = await _intent(
        api_client, db_engine, hb, _structured(start=start, expires=expires)
    )
    async with db_engine.begin() as conn:
        await run_candidate_retrieval(conn, clock, uuid_mod.UUID(a["id"]))
    await _seed_jev(db_engine, a["id"], b["id"], 0.9, 0.9)
    await _handle(db_engine, clock, a["id"])  # 提案化(pushなし)
    uid_a = await _user_id_of(db_engine, a["id"])
    uid_b = await _user_id_of(db_engine, b["id"])
    async with db_engine.connect() as conn:
        latch_id = (
            await conn.execute(
                text(
                    "SELECT id FROM latches WHERE intent_ids && ARRAY["
                    " CAST(:a AS uuid), CAST(:b AS uuid)]"
                ),
                {"a": a["id"], "b": b["id"]},
            )
        ).scalar_one()
    # 回答はテストプロセス内のLatchesService(2人目yesでmatched)
    svc = LatchesService(
        clock=clock, engine=db_engine, push=StubPushSender(clock=clock)
    )
    out1 = await svc.respond(
        auth_provider="google",
        auth_subject=subject_a,
        latch_id=latch_id,
        response="yes",
    )
    assert out1.status == "partial_accept"
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        out2 = await svc.respond(
            auth_provider="google",
            auth_subject=subject_b,
            latch_id=latch_id,
            response="yes",
        )
    assert out2.status == "matched"
    # ①双方のnotificationsへlatch_matched行(payload={latch_id})
    async with db_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT user_id, payload->>'latch_id' FROM notifications"
                    " WHERE type = 'latch_matched'"
                    " AND user_id IN (CAST(:a AS uuid), CAST(:b AS uuid))"
                ),
                {"a": uid_a, "b": uid_b},
            )
        ).fetchall()
    assert {str(r[0]) for r in rows} == {uid_a, uid_b}
    assert all(r[1] == str(latch_id) for r in rows)
    # ②プッシュ記録2件(第三汎用文のbyte比較・D-20同時送信)
    recs = [
        r for r in _push_records(caplog) if r["notification_type"] == "latch_matched"
    ]
    assert len(recs) == 2
    assert {r["user_id"] for r in recs} == {uid_a, uid_b}
    for r in recs:
        assert r["status"] == "ok"
        assert r["title"] == "LATCH"
        assert r["body"] == PUSH_BODY_MATCHED
        assert r["latch_id"] == str(latch_id)
    # ③お知らせ一覧のlatch.my_response(回答者視点・双方yesで成立)
    resp = await api_client.get("/v1/notifications", headers=ha)
    assert resp.status_code == 200, resp.text
    matched_a = next(
        i for i in resp.json()["items"] if i["type"] == "latch_matched"
    )
    assert matched_a["latch"]["my_response"] == "yes"
    assert matched_a["latch"]["status"] == "matched"
    resp_b = await api_client.get("/v1/notifications", headers=hb)
    matched_b = next(
        i for i in resp_b.json()["items"] if i["type"] == "latch_matched"
    )
    assert matched_b["latch"]["my_response"] == "yes"
```

- [ ] **Step 2: 構文・収集確認(実行はしない・§0規律)**

Run: `cd backend && uv run python -m py_compile tests/integration/test_notifications_api.py && uv run pytest --collect-only tests/integration/test_notifications_api.py -q`
Expected: exit 0・**13件収集**(test_1〜test_9・test_4a/4b/4c/5a/5b/7a/7b込み=main基準12件+test_9)

- [ ] **Step 3: 全体収集とunit全件の再確認**

Run: `cd backend && uv run pytest --collect-only -q | tail -1`
Expected: **1480件**(=1475+5)

Run: `make lint && make test`
Expected: グリーン(unit 1219件 — 本ファイルはintegrationマークのためunit収集外)

- [ ] **Step 4: コミット**

```bash
git add backend/tests/integration/test_notifications_api.py
git commit -m "test: matched notification e2e (issue-3, collect-only until test-ci)"
```

### Task 6: 報告ファイル + 最終検証

**Files:**
- Create: `docs/plans/M3/m3-g3fix-report.md`

- [ ] **Step 1: §6の完了条件1〜8を全て実行し、証拠(コマンド出力の要点)を採取する**

(コマンド一覧: ①`make lint && make test`(期待unit 1219) ②`cd frontend && npm test`(期待223 passed) ③`cd backend && uv run pytest --collect-only tests/integration/test_notifications_api.py -q`(期待13件)と`uv run pytest --collect-only -q | tail -1`(期待1480) ④basename一意コマンド ⑤`git diff main -- backend/alembic backend/pyproject.toml backend/uv.lock frontend/package.json frontend/package-lock.json frontend/vite.config.mjs frontend/vitest.config.mjs Makefile compose.yaml docker` ⑥`git diff --name-only main | sort`。test-ciは実行しない)

- [ ] **Step 2: 報告ファイルを§7の形式で作成する**

design §6裁定事項(§6-1〜§6-6)の実装対応・§9 IF確定事項からの変更有無を必ず記入する。特に§6-6はrespond()に書いたコメントの実際の行を引用する。

- [ ] **Step 3: コミット**

```bash
git add docs/plans/M3/m3-g3fix-report.md
git commit -m "docs: m3-g3fix report with verification evidence"
```

- [ ] **Step 4: worktreeの状態を整える**

`git status --short` が空(`.venv/`・`__pycache__/`・`node_modules/` はコミットしない)であることを確認。**mainへのマージはスーパーバイザーが行うため、push・mergeはしない**。

---

## 9. IF確定事項(design §2・§6の固定値。ここから変更した場合は報告書に記録)

1. **PUSH_BODY_MATCHED** = `"LATCHが成立しました。\nチャットでメッセージを確認しましょう。"`(design §6裁定3=推奨案で承認。2行目にチャット導線)
2. **MATCHED_NOTICE_TITLE** = `"LATCHが成立しました。"`(design §6裁定2)。導線=既存`NEXT_ACTION_TEXT`「チャットで挨拶を交わしましょう」の流用(成立後詳細と文言単一ソース)。バッジ=既存`STATUS_TEXT`(matched=成立済み・completed=完了)
3. **通知type** = `latch_matched`(design §6裁定4。`NOTIFICATION_MATCHED`定数・スネークケース規律)
4. **my_response**: `NotificationLatchOut.my_response: str | None = None`・proposalの直後(latches APIの`LatchSummaryOut`と同名・同位置)。値域`"yes"/"no"/"defer"/None`。responsesのuser_id比較は`str(user_id)`(latches実装と同一)
5. **通知INSERT SQL**: `INSERT INTO notifications (user_id, type, payload, created_at) VALUES (CAST(:user_id AS uuid), :type, CAST(:payload AS jsonb), :now)`・payload=`{"latch_id": str(latch_id)}`(sweeper._INSERT_ATTENDANCE_NOTIFICATIONと同一形・§10-3)
6. **宛先取得**: 既存`fetch_participant_user_ids`(latches/store.py:623・`SELECT user_id FROM intents WHERE id = ANY(CAST(:ids AS uuid[]))`=sweeperの_SELECT_LATCH_PARTICIPANT_USERSと同一形のため**新規SQL不要**)。latches.intent_ids内のuser_id一意はFR-17・05 §3(集合内user_id一意)で構造担保済み=重複送信なし
7. **プッシュ送信**: respondのtx抜け後・`send(user_id=..., notification_type=NOTIFICATION_MATCHED, latch_id=...)`・push=Noneはno-op(sweeper._send_attendance_pushesと同型・§10-4)
8. **main.py配線**: `build_push_sender(app.state.clock, settings)`(push_mode既定"stub"=StubPushSender。不正値はValueError=静かにスタブへ落ちない規律)
9. **D-08・mutedへの干渉なし**: `_COUNT_DAILY_NOTIFICATIONS`・muted判定は無変更(design §2.3)
10. **試験数の基線**(2026-10-02実査・ws-9終了時点): main現状=unit 1215+integration 260=test-ci 1475・frontend 217。本単位の見込み=**unit +4(test_notifications_service +1・test_latches_service +3)=1219・integration +1=261・test-ci 1480・frontend +6=223**。実測が見込みと異なる場合は報告書に実測値と差異の理由を記録する(試験の追加・統合はagent3の裁量ではなく計画からの逸脱として記録対象)

## 10. 参照実装の引用(agent3が参照漏れしないための全文・2026-10-02 main実査)

**10-1. latches/service.py:539-543 — _my_response(複製元・Task 2)**:

```python
def _my_response(responses: list[dict], user_id: uuid.UUID) -> str | None:
    for r in responses:
        if r.get("user_id") == str(user_id):
            return r.get("response")
    return None
```

notifications/service.pyへ置くのはこの3行ロジックと同一のヘルパー(private関数のimportはモジュール規律に反するため複製)。

**10-2. respond()の現在のtx構造(latches/service.py:78-149・Task 3の変更箇所)**:

```python
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
                user_id = await store.fetch_user_id(conn, auth_provider, auth_subject)
                if user_id is None:
                    raise LatchNotFoundError("user not found")
                row = await store.select_latch_for_update(conn, latch_id)
                if row is None:
                    raise LatchNotFoundError("latch not found")
                now = self._clock.now()  # FOR UPDATE取得後に採取
                ...(中略: 参加検査・二重回答検査・事前分類)...
                updated = await store.update_response(...)
                if updated is None:
                    self._raise_closed_or_expired(row, now)
                await store.insert_latch_event(...)
                if new_status == "matched":
                    await self._on_matched(conn, row, now)
                if new_status in _TERMINAL_FOR_CALIBRATION:
                    await self._create_calibration(...)
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
```

Task 3は ①`try:`の直前に`matched_user_ids: list[uuid.UUID] = []` ②`_on_matched`呼び出しの直後に宛先取得+INSERT ③`return _summary(...)`を`out = _summary(...)`へ替えてwith直後に`if matched_user_ids: await self._send_matched_pushes(...)`+`return out` — の3点のみ。`row`はwith内で束縛されるが関数スコープで参照可。

**10-3. sweeperの通知INSERTと宛先取得(worker/sweeper.py:105-122・322-336 — Task 3のSQLの同一形)**:

```python
_SELECT_LATCH_PARTICIPANT_USERS = text("""
    SELECT user_id FROM intents WHERE id = ANY(CAST(:ids AS uuid[]))
""")

# attendance通知先行書き込み(payload={latch_id}最小参照・引用#16)
_INSERT_ATTENDANCE_NOTIFICATION = text("""
    INSERT INTO notifications (user_id, type, payload, created_at)
    VALUES (CAST(:user_id AS uuid), :type, CAST(:payload AS jsonb), :now)
""")
```

```python
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
```

latches/store.pyの既存`fetch_participant_user_ids`(§10-6)が同一SQLのため、Task 3が新規に追加するのは**INSERT SQLと関数のみ**(宛先取得は既存関数の再利用)。

**10-4. sweeper._send_attendance_pushes(sweeper.py:341-355 — _send_matched_pushesの同型)**:

```python
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

**10-5. PushSender Protocol(notifications/sender.py:22-27 — push注入のIF)**:

```python
class PushSender(Protocol):
    """送信IF(design §2.2)。例外を出さない(記録して握る)。戻り値なし。"""

    name: str

    async def send(self, *, user_id, notification_type: str, latch_id) -> None: ...
```

`build_push_sender(clock: Clock, settings: Settings) -> PushSender`は`settings.push_mode == "stub"`で`StubPushSender(clock=clock, delay_ms=settings.push_stub_delay_ms)`を返す(既定push_mode="stub"・settings.py:136)。不正値はValueError。

**10-6. latches/store.py:623-628 — fetch_participant_user_ids(既存・Task 3が再利用)**:

```python
async def fetch_participant_user_ids(
    conn: AsyncConnection, intent_ids: list[uuid.UUID]
) -> list[uuid.UUID]:
    """参加Intent→user_id一覧(送信時のblocks判定対象・design §2.2)。"""
    res = await conn.execute(_SELECT_PARTICIPANT_USER_IDS, {"ids": list(intent_ids)})
    return [_coerce_uuid(r[0]) for r in res.fetchall()]
```

(send_messageがblocks判定に使っている既存関数。SQLは10-3の_SELECT_LATCH_PARTICIPANT_USERSと同一形)

**10-7. LatchJoinedRow(現状・notifications/store.py:74-83 — Task 2の追加箇所)**:

```python
@dataclass(frozen=True)
class LatchJoinedRow:
    """LEFT JOINで埋めたlatch要素(design §2.5の応答latchブロック)。"""

    id: uuid.UUID
    status: str
    response_deadline: datetime
    expires_at: datetime
    completed_at: datetime | None
    proposal: dict
```

Task 2はこの末尾へ`responses: list[dict] = field(default_factory=list)`を追加(frozenでもmutableフィールドはdefault_factoryで可。`from dataclasses import dataclass, field`へ変更)。`_SELECT_NOTIFICATIONS_PAGE`のSELECT列は`n.id, n.type, n.payload->>'latch_id', n.read_at, n.created_at`(r[0..4])`l.id, l.status, l.response_deadline, l.expires_at, l.completed_at, l.proposal`(r[5..10])に`l.responses`(r[11])を追記。

**10-8. notifications/__init__.pyのexport(該当行・Task 3のmain.py import)**:

`build_push_sender`は既に`__all__`へ含まれる(`from latch.notifications import build_push_sender`が可能・実査済み)。

**10-9. view.js closedText(現状:87-92行 — 終了行分岐の参照元)・detail.js:223-231(一元化の根拠)**:

```js
// 不成立の二値化(03 §7): 自分の操作履歴か統一文言か
export const closedText = (latch) =>
  latch.my_response === "no"
    ? ANSWERED_TEXT.no
    : latch.my_response === "defer"
      ? ANSWERED_TEXT.defer
      : CLOSED_TEXT;
```

```js
  const renderClosed = (latch) => {
    const section = document.createElement("section");
    section.className = "latch-detail latch-closed";
    // 二値化はview.jsの純関数に一元化(自分の操作履歴/統一文言・03 §7)
    section.innerHTML = `
      <p class="section-kicker"><span></span>LATCH</p>
      <p class="closed-text">${escapeHtml(closedText(latch))}</p>
    `;
    root.replaceChildren(section);
  };
```

お知らせ終了行を`closedText(latch)`へ替えるだけで詳細画面と同一の文言・同一の分岐になる(my_responseが同じ値を返すため・design §2.2)。

**10-10. latch_engine._COUNT_DAILY_NOTIFICATIONS(worker/matching/latch_engine.py — D-08不消費の根拠・変更しない)**:

```python
_COUNT_DAILY_NOTIFICATIONS = text("""
    SELECT user_id, COUNT(*) FROM notifications
    WHERE user_id = ANY(CAST(:users AS uuid[]))
      AND type IN ('proposal', 'nearby_candidate')
      ...
```

`type IN ('proposal', 'nearby_candidate')`のためlatch_matchedはカウント対象外=SQL変更なし(design §2.3)。

**10-11. テスト資産の形式(自己見直し用・M3 ws-3教訓)**:

- `test_notifications_service.py`: `_FakeConn(page_rows=..., user_id=...)`・`_StubStore(conn)`は`select_notifications_page(conn, *, me, before, limit)`キーワード専用・`_service(conn)`は`NotificationsService(clock=FakeClockForService(), engine=_FakeEngine(conn))`・`_page_row(n, *, latch=True)`キーワード専用・`items, next_cursor = await service.list(auth_provider=PROVIDER, auth_subject=SUBJECT, limit=20)`のtuple unpack
- `test_latches_service.py`: `_ret(value)`は「常にvalueを返すasync関数」・`_latch_row(**overrides)`はSimpleNamespace・`ScriptedEngine([])`のexecuteは空FakeResultを返す・`_svc(engine)`は`LatchesService(clock=FakeClock(NOW), engine=engine, geo=None)`(push省略)
- `test_notifications_api.py`: `_user_id_of`は**str**を返す・PushSendRecordのuser_id/latch_idも**str**(push_logのJSON)・`_push_records(caplog)`は`json.loads`済みdict
- `latch-notice.test.js`: `notificationLatch(status, overrides = {}, proposal = fullProposal)`のoverridesはスプレッド(`{ my_response: "no" }`)・`item(type, latch, overrides)`

## 11. 既存試験への追随访問(design §4影響調査表+agent2追加調査・ピン試験の期待値変更の有無)

| 試験 | 影響 | 期待値変更 | 根拠 |
|---|---|---|---|
| unit/notifications/test_notifications_service.py | LatchJoinedRowへフィールド追加(default_factory=list) | **なし**(fixtureはそのまま成立) | design §4表 |
| unit/notifications/test_templates.py | 全文ピン3定数・写像・forbiddenループが既存3type前提 | **あり** → Task 1でlatch_matched分を追加 | design §4表 |
| unit/notifications/test_push_sender.py | sender.py無変更 | なし | §5禁止(design §3) |
| unit/latches/test_latches_service.py(既存matched試験) | fetch_participant_user_ids未差し替え時ScriptedConnが空行→通知0件・送信スキップ | **なし**(無変更で通る) | §10-11・agent2追加調査 |
| integration/test_notifications_api.py test_3(muted) | 提案時点の確認。muted側bの0行期待は成立前 | なし | design §4表 |
| integration/test_g3_acceptance_e2e.py test_1(muted) | Bの回答yesでpartial_acceptまで(matchedにならない)→成立通知不発火 | なし | design §4表 |
| integration/test_g3_acceptance_e2e.py test_4(グループ成立×競合) | respond×3でmatched→api常設(新コード)が成立通知3行を書く。**notifications行数の期待はなし**(latches/intents/eventsの確認のみ) | なし(latch_matched 3行assertの追加は任意・本計画では求めない) | design §4表 |
| integration/test_expiry_batches.py test_5(completed通知) | latchはDB直接投入(status=matched)でrespond不経由→成立通知なし。COUNTはtype='attendance_request'絞り込み | なし | design §4表 |
| test_latches_api.py・test_chat_attendance_api.py | notifications参照はteardown(DELETE)のみ | なし | design §4表 |
| frontend latch-notice.test.js 53行(終了3種=CLOSED_TEXT) | notificationLatchヘルパーがmy_responseを設定しなければundefined→CLOSED_TEXTのまま | **なし**(無変更で通る) | design §4表 |
| frontend latch-detail/view等のclosedText試験 | closedText本体は無変更 | なし | §5(design §3) |
| test_app_health.py等のlifespan系 | build_push_sender(clock, Settings())はpush_mode既定"stub"でStubPushSender=例外なし | なし | agent2追加調査(§10-5) |

## Self-Review(計画書レビュー時の機械チェック・2026-10-02実施)

1. **新規テストファイルのbasename衝突**: 本単位は**新規テストファイルゼロ**(§4の5テストファイルはすべて既存ファイルへの追記)。`find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空(2026-10-02実査)
2. **ピン試験への追随访問**(supervisor指示): §11の表にdesign §4の影響調査+dodawaしたagent2追加調査(matched unit試験・lifespan系・push_sender)を列挙。期待値変更が必要なのはtest_templates.pyのみ(Task 1で対応)
3. **テストコードの自己見直し**(supervisor指示・ws-3教訓): §10-11にヘルパー引数形式・unpack・モック応答形式・比較の型を明記。①Task 2のresp()はstr(uuid)格納(Review Focus 1) ②Task 5のrecs比較はstr同士(_user_id_ofとPushSendRecordともstr) ③_user_with_subjectはtuple unpack ④latch-noticeのoverridesはスプレッド — すべて突合済み
4. **DB残存干渉**: integration test_9は既存test_notifications_api.pyのfield fixture(SUBJECT_PREFIX=m3ws3-・FK順teardown)をそのまま使う(_user_with_subjectも同一prefix構成)。worktreeでの新prefix不要
5. **specカバレッジ**(design §2→Taskの逆引き): §2.1(SELECT+複製ヘルパー)→Task 2・§2.2(closedText分岐)→Task 4・§2.3(type・第三汎用文・D-08・muted)→Task 1+§2グローバル制約・§2.4(respond挿入・tx後送信・main.py配線)→Task 3・§2.5(フロントマトリクス)→Task 4・§4新設1〜5→Task 2・1・3・5・4・§6裁定1〜6→§9-1〜9-9+Task 3コメント(裁定6)。design §3の表は§4と1:1(参加者user_id取得SQLのみ「既存関数再利用」と明記した差分あり・§9-6)
6. **プレースホルダスキャン**: TBD/TODO/「適切に」等なし。全コードステップに全文コードあり
7. **型整合**: `insert_matched_notifications(conn, *, latch_id: uuid.UUID, user_ids: list[uuid.UUID], now: datetime) -> None`(Task 3実装)とテストの`_notify_insert(conn, *, latch_id, user_ids, now)`は同一シグネチャ。`fetch_participant_user_ids(conn, intent_ids)`は位置引数(serviceは`store.fetch_participant_user_ids(conn, row.intent_ids)`)。`NotificationLatchOut.my_response: str | None = None`とテストの`== "no"`/`is None`比較整合。`_send_matched_pushes(latch_id: uuid.UUID, targets: list[uuid.UUID])`
8. **frontend整合**: `MATCHED_NOTICE_TITLE`はtexts.jsで定義しview.jsとtestがimport(循環なし・texts.jsは依存ゼロ)。latch_matched×MATCHED_NOTICE_STATUSES(matched/completed)はSTATUS_TEXTが「成立済み」/「完了」を返す(texts.js:31-36実査)。`notificationHref`は現行実装のままlatch_matchedに効く(design §2.5)
