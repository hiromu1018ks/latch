# M3 ws-1(LATCH応答系コア)実行報告

- ブランチ: m3-ws-1 / ベース: 1d16211
- 日付: 2026-09-30
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | lint: `All checks passed!` / test: `1062 passed, 172 deselected in ~7s`(unit全件) |
| 2 | docker compose build api → make test-ci | PASS | build: `Image latch-ci-api Built` exit 0 → test-ci: `1253 passed in 321.31s`(failed 0。既存1188+新規integration 18(latches 17+13b)+groupengine世代交代1+追従分を含む) |
| 3 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` → 空 |
| 4 | alembic・依存・Makefile無変更 | PASS | `git diff main -- backend/alembic backend/pyproject.toml backend/uv.lock Makefile` → 空 |
| 5 | 変更ファイル=§4の21ファイル | PASS※ | §4の21ファイル(作成13+変更8・report込み)+ `backend/tests/unit/test_rate_limit_wiring.py` の1件追加=22ファイル。`git status --short` は空。※追加1件の理由は補足1 |
| 6 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now|...' backend/src` → `backend/src/latch/core/clock.py:33` の1行のみ。`pytest tests/unit/test_arch_no_direct_time.py -v` → 1 passed |
| 7 | 既存unit試験グリーン維持 | PASS | make test 全件数=1062(全グリーン)。既存試験の期待値変更はなし。追従は test_worker_stage1.py への新規2試験追加と既存2試験のFakeResult列追記(呼び出し順序拡張の機械的追従)・test_rate_limit_wiring.py の期待dict追記(補足1)のみ。§4列挙外の既存試験は触っていない |

## design §5 実装時確認事項の結果
- (なし — design §5の8件はすべて承認済み。本計画§9のIF確定事項から
  変更した場合は下に「変更前→変更後+理由」を記録する)

## 固定値の変更有無(design.md §2・本計画§9)
- 競合クローズSELECTのORDER BY id追加(§9-1): **変更なし**
- 期限切れintegration試験=DB値操作(§9-2): **変更なし**(試験4はresponse_deadline過去化・answered_atはISO形式+Clock由来の検証どおり)
- cursor 3キーとLatchValidationError新設(§9-3/4): **変更あり**(下記・その他§9は変更なし)
- その他§9のIF確定事項: **変更あり1件**(§9-3のWHERE実装細部)・それ以外変更なし
  - §9-3: `_SELECT_LATCHES_PAGE` の先頭ページガードを `:tt IS NULL OR ...` から
    `CAST(:tt AS timestamptz) IS NULL OR ...` へ変更。
    理由: asyncpgのprepared statementが裸の `:tt IS NULL` からパラメータ型を
    推論できず `ProgrammingError: could not determine data type of parameter $2`
    で一覧APIが503になる。cursor形式(3キーbase64url)・WHERE論理・422挙動は不変

## (G3・ws-2〜ws-4への引継ぎ)
- ws-2: expiry_sweeperは本単位の_UPDATE_RESPONSEと同一WHERE(06 §6・C9)。
  matched→completed・Intent期限切れバッチもws-2
- ws-3: 通知はlatch_status_events(to_status='matched'/'rejected')と
  latches.statusを参照して実装
- 観測: 回答APIの固定文言一覧は計画§9-13

## コミット一覧
```
b096e2d fix: integration test fixes (pair orientation, asyncpg null param, matched delete path)
e8fbef1 test: latches response integration (17 cases + gid promotion)
b8bf4e1 feat: stage1 closes latches on intent deletion (dissolve + restore)
0eb9b90 fix: promotion update writes group_candidate_id (ws-7 handover)
fa1536b feat: wire latches router into app (3 endpoints, error handler)
c9460a6 feat: latch list/detail endpoints service (cursor, release info)
d43d580 feat: latch response flow (0-6 steps, 409 classification, calibration)
1bcb33a feat: latches store (serialization SQL + read queries)
b5ce8dd feat: calibration prediction assembly (3-stage lookup, min pair)
bf64322 feat: segment classification for calibration records
320b585 feat: latches domain skeleton (errors + schemas)
8c18c21 feat: expose layer3 bigrams for segment classification
```

## 補足(詰まった点・判断した点があれば)

1. **§4外ファイル `backend/tests/unit/test_rate_limit_wiring.py` への追記(完了条件5への影響)**:
   既存ピン試験 `test_all_v1_routes_are_rate_limited` は「全v1ルートがapi_rate_limited付き」
   を期待dictで強制する。新ルート3件(`/v1/latches`・`/v1/latches/{latch_id}`・
   `/v1/latches/{latch_id}/response`)が計画書routes.pyどおり `api_rate_limited` 付きで
   登録されたため、期待dictへの3行追記が必須になった(計画書の機械チェックで
   影響訪問が漏れていた)。期待値の緩和ではなく強化であり機械的追従として実施。
   完了条件5の合致判定は22ファイル(報告書込み23)となる。

2. **計画書テストコードの執筆不整合の修正(unit・仕様不変)**:
   - service/storeモックのスタブシグネチャが実装の呼び出し形(第1引数conn・
     回答起因イベントはキーワード引数)と合っていなかったため、テストヘルパ側を
     実装呼び出し形へ合わせた。
   - `test_respond_matched_intent_shortfall_503` と
     `test_respond_yes_full_flow_partial_then_matched` の2人目のfixture:
     事前responsesに自分(ME)の回答が入っており二重回答検査(手順2)が先に発火する
     ため、他者(PEER)の回答へ修正(PEER定数は元々この意図で定義されていた)。
   - `test_respond_no_creates_rejected_calibration` と full_flow 2人目:
     `fetch_pair_rows` が空だと「全段失敗=レコード不作成+ログ」(design §2.11・
     計画§9-5)になりCalibration作成を検証できないため、latch_score一致の評価行
     1件を返すよう修正。

3. **試験9のremaining_responses期待値(計画書テストの誤記)**:
   計画書テストは1人目yes後に `== 1` を期待するが、§9-10の定義
   (matched/completedで0・それ以外は `len(intent_ids) − yes数`)では 3−1=2 が正。
   unit試験と試験15は§9-10どおりのため、試験9の期待値を2へ修正(実装は変更なし)。

4. **試験17(b)(c)の削除経路(§9-6からの置換)**:
   matched IntentはDELETE APIで削除できない(intents/service.pyの `allowed_from=
   ("draft","active","paused")` のM1実装・422)。§9-6の「実HTTP DELETEの後に
   close_latches_on_delete を直接呼ぶ」は(b)(c)では実行不可のため、
   (a)は計画書どおり実HTTP DELETE(204)→直呼び、(b)(c)はDBで
   `intents.status='cancelled'` にUPDATEしてから直呼びへ置換した。
   検証内容(latches cancelled遷移・from='matched'のイベント・残Intentの
   expires_at分岐復帰)はdesign §2.7どおりで不変。

5. **ペア行の向き契約の確認(test-ci 1回目の7 failedの主因)**:
   実物パイプラインは `candidates.py` の `normalize_pair`(`a_id, b_id = sorted(...)`)
   によりmatch_candidates行を常に a<b 正規化でUPSERTする。初回実装時、試験fixtureが
   作成順向きでINSERTしており、serviceの`sorted(intent_ids)`検索・finalizeの
   expected_pairs照合と向きが合わずCalibration不作成・昇格不発となった。
   試験fixture(`_pair_eval`・groupengineの6ペアループ)を実物と同一の
   a<b正規化+`ON CONFLICT ... DO UPDATE`(workerが同キー行を書いても上書き可能)へ
   修正した。store側は§9-5どおりの単一向き検索のまま変更なし。

6. **試験17の時間窓**:
   Intent作成APIの `time.start` 上限は7日(168h)のため、BASE_HOURS=120のまま
   +60h/+72hは422(`time.start exceeds 7 days`)になった。+40h/+44hへ変更。

7. **schemas.pyへのfrom_attributes追加(§9に記載のない追加)**:
   unit試験のスタブ(データクラス/namespace)から応答モデルを直接構築するため
   `LatchSummaryOut` に `model_config = ConfigDict(from_attributes=True)` を付与
   (LatchDetailOutは継承)。model_fieldsは不変で「responses非持出」の構造ピン
   (Task 2・試験15)に影響なし。

8. **test_worker_stage1.py のFakeResultへのfetchall()追加**:
   既存FakeResultは first()/rowcount のみだったが、`close_latches_on_delete` の
   `SELECT ... FOR UPDATE` 結果読取(fetchall)が必要になり、既存試験の挙動を
   変えない形(行なしまたは空行は空リスト)で追加した。

9. **運用経緯(STATUS運用ルール4の再確認)**:
   test-ci 1回目: 7 failed(apiイメージが向き修正前コードのままrespond処理が
   旧SELECTで動作)→ `docker compose build api` 後2回目: 4 failed
   (nullパラメータ型推論・試験9/17の期待値・時間窓)→ 上記修正後3回目:
   **1253 passed / 0 failed**。apiコンテナはコード変更後に必ず再ビルドする。

10. **test-ci後の残存確認(検証手順6)**:
    users / intents / latches / match_candidates / group_candidates いずれも
    `auth_subject LIKE 'm3ws1-%'` 起点で **0件**、Redisは `m3ws1-*` パターンの
    SCANで0件(規律どおり掃除済み)。
