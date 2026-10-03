# M4 ws-3(機能試験27項目のstagingフル実行)実行報告 — **BLOCKED(仕様矛盾)**

- ブランチ: m4-ws3 / ベース: 6206829(ws-5マージ後・head=0007)
- 日付: 2026-10-03
- 実行者: agent3
- **状態: S0のシード投入が実行時API契約と矛盾しBLOCKED。スーパーバイザーの裁定を待つ(下記「BLOCKED事由」)**

## BLOCKED事由(最優先・仕様矛盾の報告)

**事象**: m4-seed.yamlのうち**15件**のIntent(18〜19歳ユーザー×category.primary=drinking×alcohol_involved=false)が、stagingの保存APIで**422 UNDER_AGEになり1件も作成できない**。計画の前提(300件全件投入・`intents_ok == 300`・created processed == 300・L1突合576ペア)が成立せず、S0以降が実行不能。

**根拠(実測・再現コマンドつき)**:

1. 実行時の正規化規則が「drinkingカテゴリはalcohol_involved=Trueへ強制」:
   `backend/src/latch/intents/mapping.py:123-125`
   ```python
   alcohol_involved=(
       True if primary == "drinking" else (inp.alcohol_involved or False)
   ),
   ```
   同ruleはM1時点の実装(99c5cb2・保存列変換の初回コミット)から存在。raw_text/structured_intentの**経路非依存**で適用される。
2. 作成経路の年齢検証: `backend/src/latch/intents/service.py:638-639`(create・`_update_active`719行も同型)が `alcohol_involved=True` かつ20歳未満で `UnderAgeError`(422 UNDER_AGE)。
3. 再現(本worktree・staging稼働中):
   ```
   POST /v1/intents (SU-001・SD-008のpayload)
   → 422 {"error":{"code":"UNDER_AGE","message":"under 20 years old",...}}
   ```
4. 該当15件(YAML上はverify.py:189-198の規則7〔18-19歳ユーザーの全intentがalcohol false〕を満たすが、drinkingカテゴリゆえ実行時はalcohol True扱い):
   SD-008, SD-010, SD-065, SD-068, SD-069, SD-071, SD-166, SD-167, SD-168, SD-169, SD-170, SD-172, SD-266, SD-268, SD-269
   (SU-001〜SU-008の18-19歳8名・`SU-001: 2008-04-04`等)
5. 突合基準側はYAMLフラグを直接参照し導出を行わない: `reference_l1.py:131`(`sa/sb.get("alcohol_involved")`)・`verify.py:189-198`。このためws-2のseed-verifyは合格した(機械検査はYAML内整合のみ見る)。
6. 影響定量(reference_l1で再計算):
   - L1 passペア576のうち**4ペア**が上記15Intentを含む(残572は影響なし)
   - EX-025/026/027/028(集合系素材)は**無傷**(dead Intentを含まない)
   - 計画の固定期待への影響: `test_s0_seed_ingestion`の300件assert・`wait_pipeline_settled`のcreated processed==300・S0突合(576)・D-10素材(EX-004/005の若年側SD-008/SD-010が作成不可のため「Layer 1年齢条件で除外」の観察が素材そのものが欠落した形になる)

**計画書の前提と矛盾する点**: 計画§1引用確定値#12「EX-004/005素材はraw_text経由だとParser規則7でalcohol true化し422のためstructured_intent明示が必須」— structured明示でも**mapping.pyのdrinking導出は回避できない**ため、この回避策の前提が崩れている。

**裁定を求める選択肢**(agent3は単独では決めない・いずれも計画§6の禁止欄に触れる変更を含む):

| 案 | 内容 | 影響 |
|---|---|---|
| A. シード生成器修正(ws-2資産の改版) | 若年8名のIntentをdrinking以外(meal/activity)へ振り替え、再生成+verify。EX-004/005素材の再設計(20歳以上側のみdrinking)を含む | 576・D-08集計74件等の計画固定値が全て振り替え後の再実測へ。計画§6「シード生成器・m4-seed.yaml本体構造は触らない」の解除が必要 |
| B. ハーネス側で422許容(計画固定値の読み替え) | 285件のみ投入・15件の422を**D-10の422応答記録として証拠化**(実はD-10前半「18-19歳のalcohol Intent作成拒否422」の直接的証拠になる)。300→285・576→572へ計画期待値を読み替え | 製品・シード資産は無変更。ただしEX-004/005の「Layer 1年齢条件による除外」観察は、若年側Intent不在ゆえ暗黙成立になりLayer 1経路の実証はci(unit)頼みになる。計画固定値の変更を報告書に「変更前→変更後+理由」で記録する形 |
| C. 製品側mapping変更 | drinking強制規則の撤廃・変更 | API契約・docs確定値の再審が必要。M1以来の製品挙動変更につき現実的でないと考える |

**推奨**: A(シード再設計)がEX-004/005・Layer 1年齢条件のstaging実証を保つ。ただしws-2資産の改版権限と再verifyはスーパーバイザーの指示範囲。

## 結果サマリ(BLOCKED時点)

| 完了条件(計画§7) | 結果 | 証拠 |
|---|---|---|
| 1. lint+unit(基準+5) | **unit PASS / lintは既存2ファイルの整形差分あり(下記「既存lint差分」)** | `1327 passed`(基準実数・ws-5マージ込み)→ 本単位追加後 `1332 passed`(=基準+5・計画§5の期待どおり) |
| 2. basename一意 | PASS(到達時点) | `find backend/tests backend/staging_tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 空 |
| 3. stub判定値ピン | PASS | `tests/unit/llm/test_llm_factory.py + test_llm_stub_response_settings.py` 全PASS(TDD Red→Green確認済み) |
| 4. staging-test全グリーン | **未達(BLOCKED)** | S0のシード投入が上記矛盾で失敗(再現ログはBLOCKED事由) |
| 5. G3繰越回収(#13〜#23) | 未達(BLOCKEDの下流) | — |
| 6. マイグレーションなし・依存追加なし | PASS(到達時点) | 変更ファイルは§4どおり・alembic/pyproject/uv.lock無変更・staging DBのhead=0007をconftest assert経路で確認 |
| 7. 変更ファイル一覧どおり | PASS(到達時点) | 下記「コミット一覧」 |
| 8. 実行手順記録 | 一部(下記) | staging環境の構築・検収まで実施済み |
| 9. make test-ci | **スーパーバイザー検証待ち**(agent3は実行せず) | — |

## 既存lint差分(本単位以前から存在・要・スーパーバイザー判断)

`make lint`(ruff format --check)が**ws-5スコープの2ファイル**で失敗する(main時点から・本単位の変更ではない):

- `backend/src/latch/users/consent.py:151`
- `backend/tests/unit/intents/test_intents_service.py:797`

計画§6の禁止(ws-5スコープに触らない)を優先し**整形を当てていない**。解消は `cd backend && uv run ruff format .` 1命令(ws-5側での対応か、ws-3への許諾を指示ください)。

## 実装の進捗(コミット済み)

1. `64a945d` feat: settings新項目2つ(llm_stub_jev_noul/score・既定0.5/2.0)+gateway stub応答組立(`_stub_jev_response`)+unitピン5件(TDD Red→Green)・既存ピン追随(eleven_fields改名含む)
2. `f8492e5` chore: compose.staging.yamlへ `LATCH_LLM_STUB_JEV_NOUL: "0.90"` / `LATCH_LLM_STUB_JEV_SCORE: "3.6"`、Makefileへstaging-reset/staging-test追記(config・`make -n`解釈確認済み)
3. `f1f6b02` test: stagingハーネス土台(conftest/helpers_wait/helpers_pushlog)
4. (WIP・未pushの作業分)helpers_seed/helpers_scenario/test_s0/test_s1/test_s2 + compute_mlr/my_latch/wait_for_latch — S0のBLOCKEDにより計画のTask 5コミット条件(グリーン後)を満たさないため、**作業消失防止の目的でこの報告と一緒にコミットする**

## 環境の検収までに確認済みの事実(BLOCKEDまでに到達した検証)

- stagingリセット: `make staging-reset` 成功(migrate head=0007・geo-import isj 46行/osm_poi 563行)
- シード: `make seed-generate ARGS="--base-datetime 2026-10-04T11:00:00+09:00"` → `intents=300 users=100 pair_total=44850 layer1_pass=576`(計画§2.2の576と一致)・`make seed-verify` OK・YAML差分なし(既コミット分と同対象日)
- **stub判定値のコンテナ内実測**: `docker compose -f compose.staging.yaml exec api1 python -c ...` → `noul= 0.9 score= 3.6 env_noul= 0.9`(env→settings→stub応答の経路がstagingコンテナで実働)
- unit 1332 passed(lint既存差分を除き緑)

## 実行上の発見(最終フル実行の手順に繰り込むべき運用注意)

1. **stagingイメージは`staging-reset`でも再buildされない**: Dockerfileが`COPY src`でコードを焼き込むため、製品コード変更後は `docker compose -f compose.staging.yaml build api1 api2 worker1 worker2` が別途必要(本実施では実行済み・コンテナ内実測で新コード確認)。staging-resetのupは既存イメージを使う
2. **apiコンテナ再作成後はnginxの再起動が必要**: nginxが起動時にupstream名を解決してIPを保持するため、api1/api2の再作成(`up -d`)後に旧IPへconnection refused→502。`docker compose -f compose.staging.yaml restart nginx` で解消(本実施で経験・解消済み)
3. **スーパーバイザー引継ぎ6(llm.sendログ)**: 保存APIはParserを呼ばないため、`latch.llm.send`はworker(embedding/Jev)のログに出る。BLOCKEDによりS0収束待ちまで到達できず観察記録は未回収。次回フル実行時に `docker compose -f compose.staging.yaml logs --tail 5000` からの`latch.llm.send`抽出で確認する(手順はhelpers_pushlogと同一形)

## 計画コードからの適応(実装判明分・全てハーネス内の修正/計画備考の許諾範囲)

1. **conftest: IdPトークンの交換経路**(最重要): 保護エンドポイントのBearerはアクセストークン(HS256・`auth/service.py authenticate`)であり、計画コードの「issue_idp_tokenを直接Bearerにする」は401 UNAUTHENTICATEDになる。`POST /v1/auth/token`で交換してから使うよう修正(既存integration試験`tests/integration/test_users_api.py`と同一経路)。交換はユーザー行を作らないためシード投入と干渉しない
2. **conftest: jsonb codec**: asyncpgはjsonbをstrで返す(コードベース規約・`worker/matching/layer3.py:45`)のため、db fixtureに`set_type_codec("jsonb", ...)`を追加(クライアント側変換のみ・SELECT専用規律不変)。plan FR-21(c)の`notif["payload"]["proposal"]`等がこれに依存
3. **FR-21(c)のSELECT実装形**: notifications payloadは`{"latch_id"}`の最小参照(`latch_engine.py:422`・承認事項2)で`proposal`キーを持たない。計画備考どおり実装形を確認のうえ、notifications×latchesを`payload->>'latch_id'`でJOINして`l.proposal`を見る形へ修正
4. **#7 PATCHのbody**: PATCHは全置換・全検証(`service._update_active`・必須3+ジオコード再実行)のため、timeのみの部分bodyは422になる。`_create_intent`既定と同一の全フィールドを送る形へ修正
5. **wait_for_latch追加**: シナリオIntentは検査内作成のため、作成直後の`my_latch(status="proposed")`は昇格待ち(デバウンス10秒+tick≤60秒)のレースで落ちる。helpers_scenarioに`wait_for_latch`(timeout 180秒)を追加しS2/S3の観察前に待つ(計画Interfaces節の「S0収束待ちの中でproposed昇格済み」とlazy生成コードの不整合の解消)
6. **helpers_scenarioのmy_latch/compute_mlrの先行追加**: Task 7で追加予定だった関数をTask 5/6の依存として先に配置(内容は計画どおり)

## 02#対応表 / G3繰越の回収対応 / D-08保留の実測 / 期待値の確定記録

**BLOCKEDにより未回収**(S0以降未実行)。裁定後の再開時に本節を埋める。

## コミット一覧

```
(確定後に記載 — 現時点)
64a945d feat: add llm_stub_jev_noul/score settings and stub response wiring
f8492e5 chore: add staging stub jev env and staging-reset/staging-test targets
f1f6b02 test: add staging harness base (conftest, wait, pushlog helpers)
(本報告コミット: WIP harness + 本報告書)
```

## 補足(詰まった点・判断した点)

- 詰まった点1(502): api再作成後のnginx upstreamキャッシュ(上記「実行上の発見2」)
- 詰まった点2(401): 計画コードの認証経路(上記「計画コードからの適応1」)
- **判断した点(停止の根拠)**: 計画§0「もし実測が本計画の期待と異なる場合は、推測で検査を緩めず作業を停止し報告書に実測と考察を書いてスーパーバイザーに確認する」+ タスク指令「仕様矛盾を発見したら実装を止めて報告せよ」。本BLOCKED事由はこの規律の適用
- staging DBにはBLOCKED時点の部分投入データ(ユーザー106・Intent 37等)が残っている。再開時は `make staging-reset` からやり直すこと(汚染の影響はボリューム内に限られる・ci無干渉)
