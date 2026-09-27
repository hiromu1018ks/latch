# M1 ws-3 実装報告(intents CRUD・draft→active・保存時検証)

- 作業単位: ws-3(design: docs/plans/M1/ws-3-design.md / plan: docs/plans/M1/ws-3-plan.md)
- 実装日: 2026-09-28
- worktree / ブランチ: /home/misty/.herdr/worktrees/latch/m1-ws-3 / m1-ws-3

## 1. 実施タスクとコミット一覧
| Task | コミット | 概要 |
|---|---|---|
| 1 | 1482cdc | CRUD例外6種(errors.py・test_errors.py。__init__.pyのexport追記を同梱 — §4参照) |
| 2 | c466ea6 | 保存API入力検証モデル(intent_input.py・test_intent_input.py) |
| 3 | 99c5cb2 | 保存列変換と永続化SQL(store.py・mapping.py・test_mapping.py) |
| 4 | 95093cd | match_events outbox発行(events.py・test_events.py) |
| 5 | d704333 | IntentService.createと検証切替(service.py・test_intents_service.py) |
| 6 | 01f9279 | 時刻検証の境界をピン留め(test_time_validation.py) |
| 7 | 9f1331a | IntentService get/listとキーセットcursor(service.py・test_intents_service.py) |
| 8 | 337acfd | IntentService.update(PATCH分岐・draft→active)(service.py・test_intents_service.py) |
| 9 | a9a2679 | pause/resume/delete(service.py・test_intents_service.py) |
| 10 | c94b8cd | intents CRUDルータ(7エンドポイント)(routes.py・test_crud_routes.py) |
| 11 | 310f99b | intents CRUDをアプリへ統合(main.py・__init__.py) |
| 12 | 40aa9a3 | CRUD integration試験を追加(test_intents_crud_api.py・実行せず) |
| 13 | 本コミット | 報告ファイル(ws-3-report.md) |

## 2. テスト結果
- make lint: クリーン(ruff format --check + ruff check)
- make test: 416 passed(既存302 + 新規114・72+10件deselect=integration収集のみ)
- 新規内訳: test_errors +6 / test_intent_input +22 / test_mapping +17 / test_events +3 /
  test_intents_service +45 / test_time_validation +6 / test_crud_routes +15
- 既知の既存失敗: なし

## 3. 完了条件の達成状況(計画§6の1〜10に対して項目別)
1. **make lintグリーン**: 達成(106 files formatted・All checks passed)
2. **make testグリーン**: 達成(416 passed・新規114件の内訳は§2のとおり)
3. **design §2.2表のEvent発行**: 達成。test_intents_service.py 内の関数として
   POST active=created v1(test_post_active_inserts_and_emits_created_event_v1)・
   active更新=updated v+1(test_patch_active_content_update_bumps_version_and_emits_updated)・
   draft→active=created 据え置き/変更有=+1(test_patch_draft_to_active_same_content_keeps_version_and_emits_created /
   test_patch_draft_to_active_changed_content_bumps_version)・
   resume=updated v+1(test_resume_paused_bumps_version_and_emits_updated)・
   DELETE=deleted 不変(test_delete_active_transitions_cancelled_with_deleted_event)・
   draft作成/再保存/pause=発行なし(test_post_draft_saves_without_event_and_geocoding /
   test_patch_draft_resave_bumps_version_without_event / test_pause_active_sets_paused_keeps_version_no_event)
   がすべて通過
4. **検証のactive/draft切替**: 達成(activeで422になる入力=必須3欠落・過去時刻・ジオコーディング失敗が
   draftで受理される対照試験4系統が通過)
5. **時刻検証の境界**: 達成(test_time_validation.py 6件。nowちょうど/now-1秒/
   now+7日ちょうど/now+7日+1秒・expires_at同一境界・draft対照)
6. **時刻参照の検査**: 達成(rg のヒットは core/clock.py:33 のみ。arch test もmake testに含まれ通過)
7. **ログ・例外への機密混入防止**: 達成(test_raw_text_and_location_never_appear_in_logs_or_errors・
   test_raw_text_never_appears_in_logs のcaplog検証)
8. **git statusの差分**: 達成(§3.1・§3.2の一覧どおり・禁止ファイルへの差分なし・作業ツリークリーン)
9. **テストbasename一意**: 達成(uniq -d 出力空)
10. **integration試験の存在**: 達成(test_intents_crud_api.py 10関数・--collect-onlyで収集成功。
    実行はスーパーバイザー検証待ち)

## 4. 計画からの逸脱・判断
- **make_intent_service(*, clock, engine)** — settings引数を省略(design §2.10 IF案からの確定。
  利用する設定がないため)。計画書Task 5注記どおり
- **ResolvedColumns.raw_text / columns_from_row の追加** — design §3.1 IF案からの確定
  (draft再保存のstructured_intent省略時の保持用)。計画書Task 5注記どおり
- **Task 1で __init__.py のerrors exportを追記** — 計画書Task 1のFilesには無いが、test_errors.pyが
  `from latch.intents import …`(パッケージ経由)で新例外をimportするため、追記なしにはTask 1の試験が
  成立しない。Task 11 Step 2で予定されていた変更のうちerrors分を前倒しした(判断の余地なし)
- **`cls._MAX_AHEAD` → `_MAX_AHEAD`(service.py `_validate_times`)** — 計画書のコードは
  モジュールレベル定数 `_MAX_AHEAD` をクラスメソッド内で `cls._MAX_AHEAD` として参照しており、
  AttributeErrorになる(実行すれば単純なバグ)。仕様(05 §5の境界値)はそのままに参照方法のみ修正
- **test_intents_service.py の `assert params["status"] == "pending"` を削除** — 計画書の当該行は
  events.pyの実装(design §2.2A: status='pending' はSQLリテラルでありbind paramsにstatusキーは
  存在しない)と噛み合わない。pendingであることの担保はtest_events.py の
  test_insert_sql_pins_pending_status_literal(SQLリテラルのピン留め)が担う
- **未使用importの削除と再追加** — 各タスクコミット前に ruff check --fix を実行した結果、
  当該タスクで未使用のimport(後続タスクで使用予定の ForbiddenError・encode_cursor等)がF401で
  削除されたため、使用タスク(Task 7/8/9)で都度import節へ再追加した。最終状態のimport構成は計画書の
  全体形どおり
- **Task 10の試験を通すためmain.py変更を先に実施** — 計画書§7.10 Step 4注記の許可範囲内
  (create_appのintent_service引数がないと全試験がTypeErrorになるため)。コミットはTask 10と
  Task 11で分離

## 5. 検証手順(スーパーバイザー向け)
1. `docker compose build api` — **必須**(make test-ci の compose up はapiイメージを再ビルド
   しない — STATUS運用ルール4)
2. `make test-ci`(unit+integration。test_intents_crud_api.py 10件を含む)
3. test-ci実行後、geo実データはfixtureリロードで失われるため `make geo-import` で復旧する
4. `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空

## 6. test-ci=スーパーバイザー検証待ち
本単位の実装中は compose常設環境へ触れていない(計画§0.3)。integration試験は
作成のみで未実行。
