# M2 ws-1(イベント駆動基盤) 実行報告

- ブランチ: m2-ws-1 / ベース: a34fa4d
- 日付: 2026-09-28
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | `All checks passed!` / `569 passed, 102 deselected in 3.94s`(スーパーバイザー検証後のfixコミット適用後は **`572 passed`** — 回帰ピン3件追加。下記「スーパーバイザー検証で検出した欠陥と修正」参照) |
| 2 | integration 8試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | `uv run pytest --collect-only tests/integration/test_events_pipeline.py -q` → `8 tests collected`・exit 0(実行は§0によりスーパーバイザー検証時) |
| 3 | 実時間参照がclock.pyのみ | PASS | rg ヒットは `backend/src/latch/core/clock.py:33: return datetime.now(UTC)` の1行のみ。`uv run pytest tests/unit/test_arch_no_direct_time.py -v` → `1 passed` |
| 4 | alembic・docs無変更 | PASS | `git diff --stat main -- backend/alembic docs` → 本reportファイル1件のみ(`docs/plans/M2/ws-1-report.md`。※report自身がdocs/配下のためコミット後はこの1行が出力される。計画§6-4の採取時点=reportコミット前では空。alembic・docs 01〜12・learn・reviews・STATUS・M0/M1計画書は無変更) |
| 5 | 触るファイルがスコープどおり | PASS | `git diff --name-only main \| sort` → 計画§4の作成11+変更9+report.mdの21ファイルのみ(下記コミット一覧参照・過不足なし)。`git status --short` → 空(※fixコミットにより `backend/tests/unit/test_pubsub_bus_sdk_calls.py` を1件追加 — 検出欠陥の回帰ピン。下記「スーパーバイザー検証で検出した欠陥と修正」参照) |
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
119b72b fix: PubsubEventBusのgapic呼び出し形式(create_topic・delete_subscriptionをrequest辞書へ)
(本節追記のdocsコミット)
be4ba58 fix: integration試験の時計進行レース修正と報告書の証拠訂正(最終レビュー対応)
0145099 docs: M2 ws-1の実行報告(完了条件7項目の証拠・test-ciは検証待ち)
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
5. **PubsubEventBus構築とlifespanについて(最終レビューで訂正)**: 当初「unit試験のlifespanで構築が失敗しないことを確認」と記載したが、unit試験はhttpx ASGITransportを使用しており**lifespanを実行していない**ため未検証だった(誤記載を訂正)。最終レビューでの実機検証の結果、`PUBSUB_EMULATOR_HOST`未設定かつGCP ADC(アプリケーションデフォルト認証情報)無しの環境では `PublisherClient()` 構築自体が `DefaultCredentialsError` で失敗し得る。**ci(compose)ではapi/workerへ `LATCH_PUBSUB_EMULATOR_HOST: pubsub:8085` を設定済みのため発火しない**。compose外でAPI/Workerを起動する場合はエミュレータhost設定またはADCが必須という前提を後続単位へ引き継ぐ

## 最終レビュー(final review)の結果と引継ぎ事項

ブランチ全体のコードレビュー(独立レビュアー・Critical 0件)。修正対応したもの:

- **integration試験#2・#8の時計進行レースを修正**: PATCH/resume応答直後の `clock.advance` がWorker受信チェーン(publish→エミュレータpush→intake→debouncer.submit)より先に走ると、submit時の `release_at = clock.now()+10` がClock進行後の時刻基準となり到達不能(窓が解放されず `_wait_status` がタイムアウト)。計画書テストコード由来の欠陥のため、各応答後に `await asyncio.sleep(1.0)` のsettle(test_7と同じ手法)を追加し、#2のrow2参照も `_wait_status` 待ちへ変更。**実行検証はスーパーバイザーのtest-ci時**

修正せず引継ぐもの(スコープ外・後続単位の改善事項):

- **publish経路にタイムアウトなし**: google-cloud-pubsubのPublisherClientは一時障害を無限再試行するため、Pub/Sub到達不能時のpublishは例外ではなく未解決futureのまま滞り得る(設計§2.2-Bの「失敗は握り」は即座に返る前提)。`asyncio.wait_for(publish, timeout=5秒)` 等での包装をws-2以降またはM4前の改善として記録(ciではpubsub常設のため発火しない)
- **main.py build_events分岐が注入済みintent_serviceを無条件上書き**: `create_app(intent_service=...)` 注入+event_bus未注入+lifespan実行の組合せで注入スタブが置換される(計画書Task 7 Step 4指定の形。現状のunit試験はlifespan未実行のため潜在)。lifespan系試験を追加する単位で `if build_intents_crud:` ガードを検討
- **Stage1.intakeのclaim系一時障害にin-process再試行なし**: `_claim` 失敗はackなしで再配信(at-least-onceで正しい)だが、回収がrelay(約35秒後)またはack_deadline 600秒待ちになる。対称性のためclaimも再試行ループへ入れる価値をws-2で検討

## スーパーバイザー検証で検出した欠陥と修正(2026-09-28・fixコミット 119b72b)

**検出事象**(スーパーバイザーtest-ci検証・実SDK経路):

1. 常設workerコンテナが起動時クラッシュループ(Restarting exit 1)。traceback: `worker/main.py run → bus.ensure() → pubsub_bus.py _ensure → create_topic(self._topic_path)` が `TypeError: Invalid constructor input for Topic: 'projects/latch-ci/topics/match-events'`
2. これによりintegration 8試験も全件setupエラー。`worker_env` fixtureがensure()の例外を握った結果のリトライ40回→「pubsub emulator not reachable at 127.0.0.1:8085」**誤表示**(エミュレータ自体は正常起動・ログで "Server started, listening on 8085" 確認済み)

**原因**: google-cloud-pubsub 2.41.0 のgapicクライアント(`create_topic`・`create_subscription`・`delete_subscription`)は第1位置引数を `request` と解釈する。素の文字列パスを位置引数に渡すと protobuf メッセージのコンストラクタ(`Topic(request)`)へ文字列が流れ TypeError になる。Task 3のunit試験を書かなかった経路(design §4.1: SDKの実RPCをunitで代替できないため・integrationで担保する計画)の欠陥が、integration実行前に一度もSDK呼び出しが成功しない形で顕在化した

**修正**(commit 119b72b・TDD: 回帰ピン試験RED→修正→GREEN):

- `create_topic(request={"name": self._topic_path})` へ(request辞書形式)
- `delete_subscription(request={"subscription": self._subscription_path})` へ(同じ形式問題・未検出のまま残っていた)
- `create_subscription(name=…, topic=…, ack_deadline_seconds=…)` は **kw-only形式でgapic正式・無修正**(位置引数なし)

**SDK呼び出しの全数点検**(pubsub_bus.py内7種):

| 呼び出し | 形式 | 点検結果 |
|---|---|---|
| `create_topic` | gapic(位置引数=request) | **修正**(request辞書へ) |
| `create_subscription` | gapic | 無修正(kw-only正式形式・試験でピン留め) |
| `delete_subscription` | gapic | **修正**(request辞書へ) |
| `publish(topic, data)` | クライアント独自メソッド | 無修正(位置引数が正式形式) |
| `subscribe(subscription, callback=…)` | クライアント独自メソッド | 無修正(同上) |
| `topic_path` / `subscription_path` | パス構築ヘルパー | 無整改(位置引数が正式形式) |
| `api_client.close()` / `subscriber.close()` | 引数なし | 無整改 |

**回帰ピンunit試験の追加**(`backend/tests/unit/test_pubsub_bus_sdk_calls.py`・3件): 修正要求にあった「SDKの呼び出し形式が正しいことの検証」を実SDK不要の形で追加した — SDKクライアントを記録スタブへ差し替え、`ensure()`/`delete_subscription()` が①位置引数なし・②`request` 辞書/kw-onlyの所定形式で呼ぶことをassert(修正前に2件が実際にFAILすることを確認=検出欠陥の再現)。実RPCの挙動自体は引き続きintegration(test-ci)が担保する。`bus_with_stubs` fixtureはPUBSUB_EMULATOR_HOSTを一時設定してクライアント生成の認証をバイパスしテスト終了時に復元(§最終レビュー補足5の前提と同じ)

**検証**: `make lint` グリーン / `make test` **572 passed**(569+回帰ピン3)/ テストbasename一意(新ファイル `test_pubsub_bus_sdk_calls.py`)。**実機test-ciの再実行はスーパーバイザーが行う**(本修正の実SDK検証は `docker compose build api worker` → `make test-ci` で完了条件2・worker起動を再検証)

## スーパーバイザー検証2巡目で検出した欠陥と修正(2026-09-28・fixコミット 132dd0e)

前回のgapic形式修正の効果は出た(エミュレータ接続・setup解消・workerコンテナ起動)。しかしintegration 8試験が全件失敗に変化したため、次の3点を修正・切り分けした。

### 検出事象1(確定・テストコード欠陥): IntentEnvelopeラップ参照のミスマッチ

- **現象**: `_create_active` が `resp.json()` をそのまま返し、呼び出し側の `intent["id"]` が `KeyError` になる(全8試験がこの型で失敗)
- **原因**: POST /v1/intents の応答は `{"intent": {...}}` ラップ構造(IntentEnvelope。M1 `test_intents_crud_api.py:141` の `resp.json()["intent"]` が既存流儀)
- **修正**(3箇所): `_create_active` → `resp.json()["intent"]`、test_3のdraft作成 → `draft.json()["intent"]["id"]`・active化 → `act.json()["intent"]["version"]`、test_8のresume → `resp.json()["intent"]["version"]`
- **全数点検**(全8試験+ヘルパーの応答参照): `tok.json()['access_token']`(auth)・`created.json()["user"]["id"]`(users)はM1流儀どおりで修正不要。PATCH r2/r3・pause・DELETEはstatus_codeのみの参照で本文参照なし。bus直publishのtest_5〜7は応答参照なし。**上記3箇所以外に同型ミスマッチなし**

### 検出事象2(原因切り分け): Retryable "claim conflicted but row not found" の繰り返し

- **結論: 実装側(stage1.py・main.py)に欠陥なし。事象1の修正で解消する見込み**(テスト失敗に起因するteardown競合のアーティファクト)
- **判断根拠**(確認事実):
  1. UNIQUE索引の定義(alembic 0001:295)は `ux_match_events_idempotency ON match_events (event_type, source_intent_id, (payload->>'version'))` であり、`_SELECT_CLAIM` のWHERE(event_type = :et AND source_intent_id = :iid AND payload->>'version' = :v)は**索引式と完全一致**する
  2. `insert_match_event`(intents/events.py:47)のpayloadは `{"version": N}` のみで、Stage1._claim のINSERTと同一形式(`payload->>'version'` の値も一致)
  3. よって「INSERTがUNIQUE競合したのにSELECT FOR UPDATEで行が見つからない」は、**その行が競合検出後に削除された場合にしか発生しない**。製品コードはmatch_events行を削除しない(status遷移のみ・cancelled遷移も行残存)。行を削除するのはテストteardownの `DELETE FROM match_events ...`(user_env fixture)のみ
  4. 再現経路: 事象1のKeyErrorでテストが応答直後に失敗 → teardownのDELETE(user_env)が、並走中のテストWorkerのclaim(SELECT FOR UPDATE)と競合 → DELETEが先ならSELECT 0行→Retryable。Retryableはintakeの再試行ループ(_validateのPayloadInvalidのみ)の対象外のため例外伝播→`Worker._dispatch` が握り(ackなし)→ 再配信のたびに同じログ(**「繰り返し」の正体は再試行5回ではなく再配信の反復**)。2回目の受信では既に競合行がないためINSERTが成功し自然回復するが、teardownがsubscriptionを削除・Workerを停止するため観察上「pendingのまま残る」
- 事象1修正後はテストが成功しteardownが正常順序(テスト完了→後始末)で走るため、この競合は発生しない。**実装修正は行っていない**(设计どおりのat-least-once回収経路)

### test_6の実機失敗2点(スーパーバイザー追加情報・同一コミットで修正)

1. **teardownの `ValueError: Cannot invoke RPC on closed channel!`**: `worker_env` teardownが `bus.close()` の後に `bus.delete_subscription()` を呼んでいたため、**delete→closeの順へ入れ替え**(close後のgRPCチャネルでは削除RPCが不可能)
2. **`assert 0 >= 6` 失敗(caplog件数)**: stage1のWARNログ(初回+再試行5回)のcaplog到達が、DBのquarantined行ポーリング検知より後に出るタイミングがあるため、`_wait_invalid_event_logs` ヘルパ(caplogの該当件数が揃うまで最大10秒ポーリング)を追加してからassertする形へ変更。※caplogはロギング伝播設定に依存するため、実機で本修正後も0件が続く場合は計画Task 10注記(「DBポーリングでquarantined行を待つ検証を主とし、ログ確認は報告の補足とする」)に従いログ検証を補足扱いへ緩める判断をスーパーバイザーが行う余地あり

**検証**: `make lint` グリーン / `make test` **572 passed** / integration収集8件・basename一意。**実機test-ciの再実行(3巡目)はスーパーバイザーが行う**

## スーパーバイザー検証3巡目で検出した欠陥と修正(2026-09-29・fixコミット de09111)

検証結果 8 failed / 666 passed。単独実行で根原因特定済みの実装バグ(M0 ws-3・M1 ws-1と同種のasyncpg系統)を修正した。

**検出事象**: `stage1.py:358` の `_quarantine_direct` 内で `AttributeError: 'asyncpg.pgproto.pgproto.UUID' object has no attribute 'replace'`。`uuid.UUID(row[0])` 形式の再構築が、asyncpgが返すUUIDインスタンス(uuid.UUIDのサブクラス)に対して走ったため。unit試験のスタブconnは `str(ROW_ID)` を返すため検出不能だった経路(STATUS.md M0 ws-3の記録と同種の落ち穴)。

**再現状況**(スーパーバイザー記録): DBに `event_type='create'`(6値外)の古いfixture残行があり、フォールバックリレーが再publish → Workerがinvalid eventとして `_quarantine_direct` で隔離を試み → UUID再構築でAttributeError → ackされず再配信ループ。該当pending行126行はスーパーバイザーがDB掃除済みのため再現しないが、`_quarantine_direct` 自体の欠陥は残っていた。

**修正**(commit de09111・TDD: 回帰ピンRED→修正→GREEN):

- `_coerce_uuid(value)` ヘルパを導入(UUIDインスタンスはそのまま・それ以外は `uuid.UUID(str(value))` — `intents/store.py` の同名対策と同じ流儀)
- **全数点検**: stage1.py内のSELECT/RETURNING結果のUUID列再構築は3箇所あり、すべて置換 — ①`_claim` のINSERT RETURNING id(挿入成功経路)・②`_claim` のUNIQUE競合後のSELECT id(duplicate判定経路)・③`_quarantine_direct` のUNIQUE競合後のSELECT id(検出箇所)。`_NIL_UUID` 定数(int=0生成)は無関係
- **回帰ピンunit試験3件**(`test_worker_stage1.py`): スタブconnの結果行にUUIDインスタンス(pgproto.UUID相当)を返すケースで ①claim RETURNING経路・②duplicate経路・③quarantine_direct競合経路 が落ちないことを検証。修正前に3件とも実際にFAIL(`AttributeError: 'UUID' object has no attribute 'replace'` — 検出事象の正確な再現)したことを確認

**検証**: `make lint` グリーン / `make test` **575 passed**(572+回帰ピン3)/ `test_worker_stage1.py` 21 passed。**3巡目ではテストの応答参照(test_1等の結果)は未確認のため、UUID修正後のtest-ci(4巡目)でまだ失敗が残る可能性がある**(その場合は次のスーパーバイザー指示に従う)。実機test-ciの再実行はスーパーバイザーが実施
