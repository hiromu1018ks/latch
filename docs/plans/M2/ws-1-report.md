# M2 ws-1(イベント駆動基盤) 実行報告

- ブランチ: m2-ws-1 / ベース: a34fa4d
- 日付: 2026-09-28
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | `All checks passed!` / `569 passed, 102 deselected in 3.94s` |
| 2 | integration 8試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | `uv run pytest --collect-only tests/integration/test_events_pipeline.py -q` → `8 tests collected`・exit 0(実行は§0によりスーパーバイザー検証時) |
| 3 | 実時間参照がclock.pyのみ | PASS | rg ヒットは `backend/src/latch/core/clock.py:33: return datetime.now(UTC)` の1行のみ。`uv run pytest tests/unit/test_arch_no_direct_time.py -v` → `1 passed` |
| 4 | alembic・docs無変更 | PASS | `git diff --stat main -- backend/alembic docs` → 出力なし(空) |
| 5 | 触るファイルがスコープどおり | PASS | `git diff --name-only main \| sort` → 計画§4の作成11+変更9+report.mdの21ファイルのみ(下記コミット一覧参照・過不足なし)。`git status --short` → 空 |
| 6 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 出力なし(空) |
| 7 | compose.yaml・Makefileの規定 | 記載確認/make -n test-ci出力 | `make -n test-ci` → `docker compose up -d --wait` → `docker compose stop worker` → `cd backend && uv run --group geo pytest; rc=$?; docker compose start worker; exit $rc`(pytest成否にかかわらずworker復帰)。compose.yamlへpubsubサービス(127.0.0.1:8085)とapi/workerの `LATCH_PUBSUB_EMULATOR_HOST: pubsub:8085` を記録 |

## design §5 実装時確認事項の結果
1. エミュレータイメージ: **旧パス `gcr.io/google-cloudsdk/cloud-pubsub-emulator:latest` は匿名pull拒否**(エラー: `Unauthenticated request. ... projects/google-cloudsdk/locations/us/repositories/gcr.io`)。計画Task 1 Step 1の規定経路(ミラー鏡像へ替え+記録)を実行し、**公式cloud-sdk-dockerの現行置き場所 `gcr.io/google.com/cloudsdktool/google-cloud-cli:emulators` を採用**(pull成功・digest `sha256:7617d937…`)。出典: GitHub GoogleCloudPlatform/cloud-sdk-docker#486(公式メンテナ回答: エミュレータは `:NNN.0.0-emulators`・`:emulators` タグで提供)・<https://cloud.google.com/sdk/docs/downloads-docker>。起動引数 `gcloud beta emulators pubsub start --host-port=0.0.0.0:8085` は公式ドキュメント(<https://cloud.google.com/pubsub/docs/emulator>)で現行有効(コンテナ内起動例はissue内の実例と同一形式)。SDKのエミュレータ切替 `PUBSUB_EMULATOR_HOST` も同ドキュメントで現行有効(Pythonクライアントは自動切替)
2. 初期値(debounce 10秒・retry 5回・relay 30秒/5秒・ack_deadline 600秒): settings.py実装値どおり — `event_debounce_window_sec: int = 10` / `event_retry_max: int = 5` / `event_fallback_relay_after_sec: int = 30` / `event_fallback_poll_sec: int = 5` / `pubsub_ack_deadline_sec: int = 600`(バックオフ実定値 `BACKOFF_SEC = (1.0, 2.0, 4.0, 8.0, 16.0)` — stage1.py)
3. 試験subscriptionのdrain実装: **試験専用subscriptionの作成/削除**を採用(design §5-3の確定)。`worker_env` fixtureが試験ごとに `match-events-test-<hex8>` をcreateし、teardownで `bus.delete_subscription()`。新規subscriptionは作成以降の配信のみを受けるため空引き不要(残余メッセージ干渉の構造的排除)

## 固定値の変更有無(design.md §2・本計画§8)
- ci Queue具象=Pub/Subエミュレータ(design §2.1): **変更なし**(コンテナイメージの置き場所のみ上記のとおり鏡像へ。機能・SDK経路・起動引数は不变)
- コミット直後publish+フォールバックリレー(design §2.2): 変更なし
- Worker内subscribe+debounce・再試行はWorker内ループ(design §2.3): 変更なし
- 本計画§8のIF確定事項(IncomingEvent契約・Stage1 API/SQL・debouncer API・relay SQL・設定既定値・Makefile/compose形式): **変更あり(1件・テストコード側)** — Task 4: 計画書test_worker_debounce.pyの `advance(seconds=4)` はトレーリング窓仕様(design §2.3: 解放時刻=未知version到着時刻+10秒)では解放に届かないタイポのため `advance(seconds=10)` へ修正(コメント「v4到着から10秒」との整合。test_release_at_is_arrival_plus_window が同実装を別経路で検証)。製品コードのIF・SQL・設定値はすべて変更なし

## スーパーバイザー検証手順(test-ci実行時)
1. `docker compose build api worker` — **api・workerイメージの再ビルドが必須**(make test-ci の compose up は再ビルドしないため・STATUS運用ルール4)
2. `make test-ci` — Makefileがworkerを停止してからpytestを実行し、成否にかかわらずworkerを復帰する。test_events_pipeline.py(#1〜#8)を含む全体グリーンで完了条件2を検証
   (マイグレーション追加なしのため `make migrate` は不要。実行しても冪等)
3. 常設worker復帰の確認: `docker compose ps` で worker が running に戻っていること(コンテナ起動自体がworker/main.py配線の起動確認 — design §4.2)

## コミット一覧
```
ba7e230 test: イベントパイプラインE2E 8試験(作成のみ・実行はスーパーバイザー)
2916d32 feat: ci環境へPub/Subエミュレータ追加とtest-ciのworker停止/復帰
b0a2861 feat: Workerへsubscribe・debounce・Stage1配線(ackはstatus遷移後)
b1e0156 feat: コミット直後publishとフォールバックリレー起動をAPIへ接続
74789a8 feat: フォールバックリレー(30秒超のpending行を5秒周期で再publish)
71e5999 feat: Stage1第1段処理(行確保・version検査・種別処理・再試行→quarantined)
3648e64 feat: TrailingDebouncer(更新Eventの10秒トレーリング窓・再受信非延長)
babad95 feat: PubsubEventBus(エミュレータ切替・ensure・ストリーミングpull)
a460085 feat: EventBusポートとIncomingEvent契約(eventsパッケージ)
e8e3968 feat: イベント駆動の設定とgoogle-cloud-pubsub依存を追加(M2 ws-1)
```

## 補足(詰まった点・判断した点があれば)
1. **エミュレータイメージの置き場所**(上記§5-1のとおり)。旧パスの匿名pullが拒否されるため公式の現行鏡像へ替えた。`:emulators` タグは流動(tag更新で内容が変わる)なため、スーパーバイザー検証時に起動しない場合はバージョン付きタグ(`:NNN.0.0-emulators`)へ固定する修正を推奨
2. **google-cloud-pubsub 2.41.0 の `pubsub_v1.__version__` 属性廃止**: Task 1 Step 3の確認コマンド `python -c "import google.cloud.pubsub_v1; print(pubsub_v1.__version__)"` は AttributeError となるため `importlib.metadata.version("google-cloud-pubsub")` で確認(2.41.0)。import自体と PublisherClient 到達は問題なし
3. **計画書テストコードの軽微な修正3件**(いずれもテスト側・製品IF不変):
   - Task 4: debounce窓解放時刻のタイポ(上記固定値欄に記録)
   - Task 5/8: 計画書追記コードのimport節(ファイル途中配置)がruff E402に抵触するためファイル先頭へ統合
   - Task 7: テストのFakeConnにexecuteメソッドが無くoutbox INSERT呼び出しで落ちるため、既存test_intents_service.pyと同じ流儀(async executeで記録)へ完成
   - Task 10: `# noqa: E402(説明)` の書式違反を直前コメント行へ分離・UP041(asyncio.TimeoutError→TimeoutError)を適用
4. **make lint の `ruff format --check` 整形差分**: 計画§0の規定フロー(`cd backend && uv run ruff format .` を当てて再実行)で各タスク対応済み
5. **PubsubEventBus構築はunit試験のlifespanで失敗しないことを確認**(Task 7 Step 5注記の検証どおり、クライアント生成のみでRPCしないため。relay初回run_onceのDB接続失敗も握り込まれる)
