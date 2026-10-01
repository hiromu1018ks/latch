# M3 ws-3(通知の媒体確定)実行報告

- ブランチ: m3-ws-3 / ベース: 0916af6(main先端・merge-base=0481f9d=ws-3計画書コミット)
- 日付: 2026-10-01
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | `250 files already formatted` `All checks passed!` / `1129 passed, 213 deselected in 7.55s`(期待unit 1129と一致・201→213は本単位integration 12件のdeselect増分) |
| 2 | integration収集 | PASS | `uv run pytest --collect-only tests/integration/test_notifications_api.py -q` → `12 tests collected`・exit 0(test_1・test_2・test_3・test_4a・test_4b・test_4c・test_5a・test_5b・test_6・test_7a・test_7b・test_8) |
| 3 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 空 |
| 4 | alembic・依存・Makefile無変更 | PASS | `git diff main -- backend/alembic backend/pyproject.toml backend/uv.lock Makefile` → 空(head=0005不変・依存追加なし) |
| 5 | 変更ファイル=§4の23ファイル | PASS | `git diff 0481f9d..HEAD --name-only \| sort`(=merge-base..HEAD・報告コミット前22ファイル+本報告書で23)が§4一覧と完全一致。※`git diff main`(作業ツリー比較)では `docs/plans/STATUS.md` が追加で出るが、これはmain側のスーパーバイザーコミット0916af6(ws-3/ws-4着手記録)由来で、自ブランチのコミットにはSTATUS.md変更が0件(`git log 0481f9d..HEAD -- docs/plans/STATUS.md` → 0) |
| 6 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now\|utcnow\|time\.time\|time\.monotonic\|from time import' backend/src` → `backend/src/latch/core/clock.py:33` の1件のみ / `uv run pytest tests/unit/test_arch_no_direct_time.py -q` → `1 passed` |
| 7 | 既存ピン試験グリーン維持 | PASS | make test 1129 passed(=main 1100+新規29)。§4列挙以外の試験変更なし・test_llm_factory(llm 9項目ピン)もTask 2 Step 4で26件緑確認・test_worker_reeval・test_expiry_batches相当はworker系49件緑で確認 |
| 8 | test-ci未実行 | PASS | test-ci=スーパーバイザー検証待ち(ws-4並走・0006取り合い回避)。docker系コマンド・make migrate・make g1-gate等も未実行 |

## design §5 実装時確認事項の結果
- §5-7(type定数import切替後の既存試験の無傷): 無傷を確認。test_latch_engine.py 62件(既存58+新規4)・test_sweeper.py 22件(既存20+新規2)が全緑。既存試験は `NOTIFICATION_PROPOSAL`/`NOTIFICATION_NEARBY`/`NOTIFICATION_ATTENDANCE_REQUEST` をlatch_engine/sweeperモジュールからの再参照(import切替後も同名のモジュール属性として残る)・`"proposal"` 等の文字列リテラル比較・SQL定数importのいずれかで参照しており、定数値が不変のため影響なし(全件緑で証明)
- §5-8(test_rate_limit_wiring.py追記のws-4との衝突): 追記はCounter辞書リテラルの `/v1/latches/{latch_id}/response` 行の直後(アルファベット順でlatchesとusersの間)へ2行(`"/v1/notifications": 1`・`"/v1/notifications/{notification_id}/read": 1`)の機械的追記のみ。ws-4の3行追記とは行位置が分かれるためマージ時の両側保持で解消可能(スーパーバイザー担当)。※実ファイルパスは `backend/tests/unit/test_rate_limit_wiring.py`(計画書§4の表記 `tests/unit/ratelimit/` は実在しないディレクトリ・同一ファイルへ追記)

## 固定値の変更有無(design.md §2・本計画§9)
- build_pushの写像・文言3定数(§9-2): 変更なし
- StubPushSender・PUSH_TIMEOUT_S=3.0(§9-4): 変更なし(実コードは計画書全文どおり)
- store SQL 3本+LEFT JOIN正規表現ガード(§9-6): 変更なし(SQL本文は計画書どおり)
- cursor 2キー・スキーマ(§9-7/8): 変更なし
- その他§9のIF確定事項: 変更なし(下記は実装コードではなく試験コード側の修正)
  - テスト側修正1: test_push_senderのok記録フルピンにおけるoccurred_at期待値 `NOW.isoformat()`(`2026-10-01T12:00:00+00:00`)→`"2026-10-01T12:00:00Z"`。§9-3固定値どおり`model_dump_json()`でシリアライズするとpydantic標準のRFC 3339形式(Zサフィックス)になるため。時刻値自体は等価・実装コード不変
  - テスト側修正2: store句ピン2試験の検証対象をdialect compiled文字列→生文字列(`str(store._XXX)`)へ。compiled後はbind paramが`%(me)s`形式に置換され、SQL原文の改行で`~`演算子が次行に置かれるため計画書のassert文字列が通らない。latches/test_latches_store_sql.pyの流儀(compiled検査=bind param部分認識ゼロ専用・句ピン=生文字列)と同一形に統合。bind param部分認識ゼロ検査(compiled)は計画書どおり残置
  - テスト側修正3: test_list_builds_items_with_latchのlatch_id期待値 `rows[0].latch_id_raw`(文字列)→`uuid_mod.UUID(rows[0].latch_id_raw)`。serviceは_to_uuid_or_noneでuuidへ変換するため文字列との比較は恒久False(計画書テストのタイポ・「生文字列→uuid変換」の検証意図は保持)
  - lint適用分: ruff format(複数行折り返し位置)+F401未使用import(PushSendRecord)+B007(`for i`→`for _i`)+I001(import並べ替え)+UP037(ctorアノテーション引用符除去 — `from __future__ import annotations` 下では不要)。いずれも意味不変

## (スーパーバイザー検証時の手順・結果記入欄)
- docker compose build api → make test-ci(期待 1342 = main 1301 + 新規41)
- 残存確認(m3ws3-%): <SQL 5本の結果(すべて0件)>
- Redis掃除確認: <SCAN結果(0件)>

## コミット一覧
```
1e9ef61 test: integration tests for notifications API and push dry-run
91a2ced feat: GET/POST /v1/notifications routes and app wiring
3e5c501 feat: notifications service and schemas (cursor, list, read)
9c9ba0c feat: notifications store (LEFT JOIN page, owned select, mark read)
a201832 feat: build PushSender in Worker and inject into engine and sweeper
f739e98 feat: wire PushSender into ExpirySweeper attendance notifications
5eea6d8 feat: wire PushSender into LatchEngine (post-commit send)
d56ea40 feat: PushSender protocol and StubPushSender with dry-run records
cee79d7 feat: push_mode and push_stub_delay_ms settings
f5a9aa8 feat: notification type constants and push templates (FR-21 structural guard)
```
(Task 1〜10の各コミット時点で make lint && make test グリーンを確認。Task 3のみ初回コミット時にパイプ経由でlint失敗を見過ごし、即座にformat+F401修正をamendで反映 — 最終コミットd56ea40はlint/testグリーン確認済み)

## 補足(詰まった点・判断した点があれば)
- 計画書からの逸脱はすべて上記「テスト側修正」欄のとおり実装コードの固定値は不変。TDDは全TaskでRED(期待エラーの確認)→GREEN(全緑確認)→コミットの順で実施
- Task 10のintegration試験は§0規律どおり収集確認(12件・exit 0)まで。実行はスーパーバイザーのtest-ci時
- unit期待1129/実測1129・integration期待12収集/実測12収集・test-ci見込み1342(§9-12と一致)
- `_count_daily_notifications`・`_insert_notification`・`_INSERT_ATTENDANCE_NOTIFICATION`・try_promote/_nearby_in_txの上限判定ロジックは無変更(§9-11)。latch_engineの変更はimport切替・ctor・pending収集1行・tx後送信呼び出し・_nearby_in_tx戻り値拡張のみ
