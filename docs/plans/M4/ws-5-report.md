# M4 ws-5 実装報告

- 実装: agent3(本計画docs/plans/M4/ws-5-plan.mdどおり)
- ブランチ/worktree: m4-ws5・実装最終コミット: 3985b27(報告コミットの直前HEAD)
  - コミット一覧: a744ecf(logallow)→3a25feb(0007)→28dd7d2(consent service)→
    7a3a5a2(consent API)→684e20e(同意ゲート)→1157e22(parseゲート)→
    ce16cd8(Layer 1+ヘルパー同意付与)→7163dc7(integration試験)→
    3985b27(lint整形)
- unit試験: 1327 passed(pytest tests/unit)/ ruff: エラーなし(`ruff check src tests`・All checks passed)
- frontend: vitest 233 passed(24ファイル)/ npm run build: 成功
- integration: collect-only成功(269試験)。**test-ci=スーパーバイザー検証待ち**
  (apiイメージ再構築後に実施。ヘルパー同意付与20ファイル21箇所・test_14・
  test_consent_api.pyが対象)

## 完了条件に対する自己検証

| # | 完了条件 | agent3実行分 | スーパーバイザー検証分 |
|---|---|---|---|
| 1 | 0007存在+rev/down_revision+up/down | マイグレーションファイル構文確認ok(revision="0007"・down_revision="0006")・collect-only成功 | test-ciのtest_head_is_0007・test_users_consent_at_column_exists |
| 2 | 遮断3経路422・同意後通過・draft許可 | unit 209(intents)+routes試験16グリーン | test-ciのtest_consent_api.py 3経路試験 |
| 3 | Layer 1 peer除外 | test_layer1_where_requires_peer_consentグリーン(agent3実行)・matching全206グリーン | test-ciのtest_14 |
| 4 | 許可リスト機械検査+既存2ログ無変更 | test_log_allowlist.py 5 passed(agent3実行)・`git diff main -- backend/src/latch/llm/records.py backend/src/latch/notifications/records.py backend/src/latch/notifications/templates.py`が空出力を確認 | G4証拠採取(下記手順4) |
| 5 | フロント動作 | latch-consent.test.js全試験+全vitest 233 passed+build成功 | — |
| 6 | レート制限Counter追従 | test_rate_limit_wiring.pyグリーン(Counterに`"/v1/users/me/consent": 2`追加済み) | — |
| 7 | 既存全unit/vitestが無変更期待値で通る | 全unit 1327 passed・全vitest 233 passed(追従変更はUserRow構築7箇所とsettings試験引数追加のみ) | — |
| 8 | マージ後mainでtest-ci+G4証拠3点 | — | 下記「スーパーバイザー検証時の手順」 |

## スーパーバイザー検証時の手順

1. `docker compose build api worker`(同意ゲート・consent APIは新コードが必要 —
   常設コンテナが旧コードのままだとintegrationが全滅する)
2. `alembic upgrade head`(conftestのmigrated_dbが自動実行・head=0007を確認)
3. `make test-ci`(全グリーンを確認。特にtest_consent_api.py・test_matching_hardfilter
   test_14・test_geo headピン)
4. G4証拠3点の採取(docs/testassets/results/へ・10 §5):
   ①同意フローのstaging手順実行記録(未同意Intentの422→同意→active可の順序観察
   ・test-ci試験のstaging再現+ログ添付)
   ②test_log_allowlistの実行結果+登録表の写し
   (ALLOWED_STRUCTURED_LOGS=latch.llm.send→SendRecord・latch.push.send→
   PushSendRecord。許可リスト一覧=問い合わせ開示資料の雛形)
   ③`docker logs`からlatch.llm.send等の出力にraw_text相当が含まれないことの
   サンプル照合(ws-3のpushログ読取と同型)

## 計画書からの逸脱(軽微・挙動不変 — 確認用記録)

1. **Task 3 試験コード修正**: `test_record_returns_state_with_clock_time`は計画書
   のままだと`StubClaims()`を別々に2回生成して同値比較で失敗する(時刻の比較は
   一致)。同一インスタンスを使い回す形に修正(検証意図「Clock由来の時刻が渡る」は
   不変)。
2. **Task 4 期待値表記修正**: POST応答の時刻比較を`NOW.isoformat()`
   (`+00:00`形式)からリテラル`"2026-09-27T12:00:00Z"`へ変更。Pydantic v2は
   UTCを`Z`形式で直列化するため(既存のbirth_date試験と同型のリテラル比較)。
3. **Task 6 lint対応**: 計画書コードの未使用変数`result`(test_parse_without_
   consent_lookup_keeps_current_behavior)を削除・`make_intent_parse_service`の
   戻りを複数行へ整形(ruff E501/F841対応・検証内容不変)。
4. **Task 5・8 lint整形**: UserRow構築とjson引数の1行が88文字超のため複数行へ
   折り返し(E501対応・挙動不変)。
5. **Task 8 試験数表記**: 計画書Step 2は「collect成功(8試験)」だが、計画書に
   記載された試験コード本体は7試験(schema・基本挙動3・遮断3経路)。コードは
   計画書どおり作成しcollect=7を確認(表記側の数え違いと判断)。

## 既知の限界・残置事項(設計§6で承認済み)

- origin前段スキップ省略: DB直書きで起点だけ未同意の運用異常時、その起点の
  評価(Embedding送信を含む)は走り得る(Layer 1はpeer側のみ検査 — 計画§4-1)
- 未登録JWTのparseは現行どおり(user_id=Noneで送信記録。計画§4-2)
- 同意の取り消しは非実装(§6-2)
- docs改版候補(05 §2・05 §5・08 D-14・03 §2)は次回docs改版にまとめる(§3.4)
- users側`ConsentRequiredError`は本単位で送出する経路なし(05 §5対向配置・計画§4-4)

## 引き継ぎ(watcher向け)

- **ws-5マージ後〜ws-3実装完了まで、seed投入→マッチング検証のフロー
  (seed-verify・期待値表検証)は実行しないこと。** seedのユーザーは同意未記録で
  Layer 1除外される。ws-3がhelpers_seed.pyへ同意API呼び出しを追加するまで
  機能しない(直列化裁定・設計§6-3の既定の経過状態)
- ws-3のstaging conftestのhead前提ピンは0007へ読み替え(設計§6-3)
- integrationヘルパーへ同意2行を追加した20ファイル21箇所の全数は計画§Task 7
  Step 5の表どおり(test_notifications_api.pyのみ2箇所・test_account_api.pyは
  `_user`のみで`_user_tracked`は`_user`経由のため1箇所)。挿入後の`rg`目視確認済み
