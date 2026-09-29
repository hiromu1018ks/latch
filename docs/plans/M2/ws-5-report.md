# M2 ws-5(Layer 4 Jev)実行報告

- ブランチ: m2-ws-5 / ベース: 71135e9
- 日付: 2026-09-29
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | lint: `All checks passed!`(ruff format --check + check)/ test: `787 passed, 139 deselected`(integration 139件は実行除外・うち本単位追加8件) |
| 2 | integration 8試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | `uv run pytest --collect-only tests/integration/test_matching_jev.py -q` → `8 tests collected in 0.02s`・exit 0 |
| 3 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now\|utcnow\|time\.time\|time\.monotonic\|time\.sleep\|from time import' backend/src` のヒットは `backend/src/latch/core/clock.py:33` の1行のみ。`pytest tests/unit/test_arch_no_direct_time.py` → `1 passed` |
| 4 | alembic 0001/0002・docs無変更 | PASS | `git diff --stat main -- backend/alembic/versions/0001_initial_schema.py backend/alembic/versions/0002_geofeatures.py docs` → 出力なし(空) |
| 5 | 変更ファイル=§4の31ファイル | PASS(報告書コミット後の最終再確認済み) | `git diff --name-only main \| sort` が§4の30ファイル(報告書除く)+報告書=31と完全一致。`git status --short` 空 |
| 6 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 出力なし(空) |
| 7 | 既存unit試験グリーン維持 | PASS | `make test` 全件数 787 passed(ws-4報告時の既存818はintegration込みの数値で、unit実行分は787。§4列挙以外の期待値変更は無し — 変更ファイル一覧と突合済み) |

**test-ci=スーパーバイザー検証待ち**(STATUS運用ルール1〜3・マイグレーション0003追加のため
migrate/test-ci/migrateは実装側で未実行。wave単独のため並走とのDB取り合いは計画上なし
〔運用ルール1・2は本単位で該当なし〕)

## design §5 実装時確認事項の結果
3. typesafe-sdkのPyPIパッケージ名とバージョン: **`typesafe-sdk`==0.7.2**
   (import名 `typesafe_sdk` と一致。`uv add typesafe-sdk` で取得成功。
   httpx直実装代替〔§9-12〕は不使用)
4. AsyncTypeSafeClientのretry引数名: **`retry`**(design §5-4の予測どおり)。
   `AsyncTypeSafeClient(api_key=..., base_url=..., timeout=6.0, retry=RetryPolicy(max_retries=0))`
   で渡す。SDK既定は `RetryPolicy(max_retries=2, backoff_initial=0.5, backoff_max=5.0)` —
   max_retries=0で無効化済み(実SDK署名を `inspect.signature` で確認済み)
7. noul応答のJSON形状: スモーク時に確認(スーパーバイザー検証時)。SDK型(float)採用のまま
   (応答はpydanticの `NoulAnswer.noul` / `ScoreAnswer.score` 属性。`_envelope` が
   answersをそのまま通し、`validate_and_normalize._field` のオブジェクト対応で読む)

**その他の実装時確認(SDK署名準拠の差分・§0規律「定数・値は不変」)**:
- `system_one()` の `questions` 引数は `Mapping[str, Noul | Choice | Score]`
  (pydanticモデル)を期待 → `llm/jev.py` の `JEV_QUESTIONS`(plain dict・全文ピン対象)
  は**不変**のまま、`llm/typesafe.py` 内で `Noul.model_validate(q) / Score.model_validate(q)`
  により1回だけ変換(`_SDK_QUESTIONS`)。wire形式は定数と完全一致(unit試験
  `test_system_one_call_args_pin` が `model_dump(mode="json", exclude_none=True)` で強制)
- 応答は `SystemOneResponse(model: str, usage: Usage, answers: dict[str, Answer])`。
  `usage.input_tokens/output_tokens` は `int | None`
- クライアントは `async with`(async context manager)対応確認済み
- `TypeSafeAPIError(status, body, headers)` — `status` は第1引数の属性(529判別に使用)

その他(design §5-8〜12)は先送り・引継ぎ記録として無変更(§5-8のmax_tokens=512は
`ANTHROPIC_JEV_FALLBACK_MAX_TOKENS` として実装済み)

## 固定値の変更有無(design.md §2・本計画§9)
- Gateway内切替+切替条件4種(design §2.2の表): **変更なし**
- score正規化分母=4(design §2.4・supervisor承認の解釈記録): **変更なし**
- Guard課税は起点Intent側のみ(design §2.5-1・承認済み解釈記録): **変更なし**
- K_j選択は永続行から・補充なし(design §2.5): **変更なし**
- H再検証はLAYER1_WHERE再利用・evaluated行のみclose(design §2.6): **変更なし**
- 再選択規則4分岐(design §2.7): **変更なし**
- 本計画§9のIF確定事項(質問定数・プロンプト全文・テキスト行形式・計上ルール・SQL全文):
  **変更なし**(§9-3のtime行書式表記については下記「補足」参照 — design §2.3表の
  実装であり値の変更ではない)

## (ws-6・ws-7・ws-8への引継ぎ)
- ws-6: JevWorker.handle(intent_id) は起点非依存。Bucket再評価・catch-upから同一部品を呼ぶ
  (reevalガードは入口で共用 — ws-4報告書§5-7)。latch_score計算・保留キューはws-6。
  handleのno-op判定は構造化ログのみ(`jev origin no-op`・`jev origin input missing`)
- ws-7: select_jev_targets の純関数と K_J=8・ONE_ON_ONE_MIN=4・pair_kind タグが拡張点。
  グループ由来ペア(規則2〜4)・未判定ペア継続優先・group_candidates保持はws-7
- ws-8: circuit breakerはjudge_pairの第一候補呼び出し(_jev_call の第一候補側)を包む位置に
  注入する(gateway._jev_call のdocstringに注入位置を記載済み)。開放中の第一候補停止・
  半開試験・p95レイテンシ測定はws-8

## スーパーバイザー検証手順(test-ci実行時)
1. `.env` の `LATCH_TYPESAFE_API_KEY` に実値が設定済みであることを確認(値の有無まで確認。
   キー名の存在だけでは「設定済み」ではない。jev-smoke用・test-ci自体には不要)
2. `docker compose build api` — apiイメージ再ビルド(STATUS運用ルール4・依存追加
   `typesafe-sdk==0.7.2` があるため必須。workerイメージも同様に再ビルド要)
3. `make migrate` — 0003適用確認(alembic_version=0003・match_candidates.skip_reason列の存在)
4. `make test-ci` — 既存全数+本単位integration 8件がグリーン
5. `make jev-smoke` — 実API 1呼び出し(課金)。`make jev-smoke FALLBACK=1` でフォールバック直接
   呼び出しも任意。noul応答のJSON形状確認(design §5-7)を併せて実施
6. マージ前: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空
   (運用ルール5)
7. test-ci後、Redis残存(prefix掃除)とuser残存(subject LIKE 'm2ws5-%'=0件)を1回手動確認

## コミット一覧
```
61da62a style: integration試験の未使用変数整理(lintグリーン化)
068bdc7 style: integration試験の未使用変数整理(lintグリーン化)
ebf0e99 style: integration試験の未使用変数を整理(lintグリーン化)
c6c59c3 style: integration試験の行長・未使用変数整理(lintグリーン化)
d4cab00 style: integration試験の未使用変数整理(lintグリーン化)
14c1479 test: Layer 4 Jev integration試験(test-ci=スーパーバイザー検証待ち)
236b55c style: jev_smokeのruff format整形
7ad2668 feat: make jev-smoke(実APIスモーク・スーパーバイザー検証用)
829cfdc style: Worker配線の行長・整形修正(lintグリーン化)
38326e5 feat: WorkerへJevWorker配線(_kick_jev・DI)
cff835d style: JevWorker試験・実装のlint整形追従
3c3d96f feat: JevWorker(2フェーズ実行・Guard課税・jev_result記録・世代スキップ)
1909e2a style: test_layer4のimport順整理(lintグリーン化)
add9543 feat: layer4(K_j選択SQL・H再検証・一括close・配分純関数)
e4b3773 feat: マイグレーション0003(match_candidates.skip_reason列追加)
592a0a0 style: Task 6成果物のruff format整形追従
29dcc05 style: llm/__init__のimport順整理(lintグリーン化)
055e68d feat: build_worker_gateway改名拡張(embedding+jev+フォールバックreal化・typesafe設定追加)
ec8f856 style: test_gateway_jev_switchの整形追従(lintグリーン化)
aa0385e style: test_gateway_jev_switchの行長・整形修正
6fcc558 style: judge_pair切替の行長修正(lintグリーン化)
d698e74 feat: judge_pair実装化(TypeSafe Jev第一候補+フォールバック切替)
c3762d8 style: test_stubのruff format適用
b6b1a3e feat: StubJevをSystem One answers形式へ・切替条件例外3種新設
3cac217 fix: test_anthropic_jev_fallbackの行長修正(lintグリーン化)
0f1b54d fix: FallbackJevProviderの行長を88字以内へ(lint追従)
3f6c0f4 feat: AnthropicJevFallbackProvider(structured output・sampling不送・envelope組立て)
a324cbe feat: TypeSafeJevProvider(typesafe-sdk・retry無効化・例外翻訳)
881c30e feat: llm/jev.py System One共通部品(質問定数・正規化テキスト・出力検証)
```

## 補足(詰まった点・判断した点があれば)

1. **計画書§9-3のtime行書式とテスト期待値の矛盾(design優先で解決)**: §9-3は
   `{YYYY-MM-DD HH:MM}–{YYYY-MM-DD HH:MM}` と書くが、Task 1のテスト期待値(計画書自身の
   記載)とdesign §2.3表(`YYYY-MM-DD HH:MM–HH:MM`・07 §4例「20:00–23:00」)は
   終了側が時刻のみ。計画書§1「矛盾した場合はdesign.mdが本計画より優先」により
   design §2.3表どおり実装(`_jst_hm_only`)。07 §4例の再現テストがそのまま通る。
2. **errors.pyの新例外3種の先行追加**: 計画書ではTask 4追加だが、Task 2・3のテストが
   `LLMRateLimitError/LLMOverloadedError/LLMConnectionError` をimportする依存が
   あるためTask 2コミットで先行追加した(内容・クラス定義はTask 4計画分と同一)。
   Task 4ではstub・providers・__init__の残りを実施。
3. **`questions`引数のSDKモデル化(上記design §5欄のとおり)**: §9-6の
   `questions=JEV_QUESTIONS`(plain dict渡し)はSDK署名と不整合のため、
   `llm/typesafe.py` 内でSDK pydanticモデルへ変換して渡す。§9-2定数は不変
   (wire一致をunit試験で強制)。docs確定値に変更なし。
4. **`_RecordingStub`のname上書き**: 計画書の試験コードでは `name = "fb"`(クラス属性)
   が `StubLLM.__init__` の `self.name = "stub"`(インスタンス属性)で隠れるため、
   `__init__` 内で `self.name = "fb"` を明示する形に修正。
5. **test_gateway_jev_switch・test_llm_factory等の試験コード軽微な補完**:
   計画書記載コードの `caplog.at_level` 未指定箇所(既存流儀に合わせて追加)・
   `LLMError` 未import(関数内importへ追加)・test_llm_geminiのreal構成試験は
   3鍵必須化(§9-13)に伴い3鍵を設定して構築する形に置換。
6. **lint失敗の混入コミット**: Task 3・5・6でruff format/checkの指摘が残ったまま
   featコミットを1度ずつ行い、直後にstyle/fixコミットで修正した(lintグリーン化の
   履歴は上記コミット一覧のとおり)。最終状態は `make lint` グリーン。
7. **integration試験の収集数**: 計画書§4.2-9のとおり8試験(design §4.2-9の
   「既存全数グリーン維持」は試験を置かずtest-ciで確認)。実行は未実施
   (test-ci=スーパーバイザー検証待ち)。
