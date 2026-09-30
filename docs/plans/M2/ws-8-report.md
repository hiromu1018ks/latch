# M2 ws-8(縮退運転+G2ハーネス)実行報告

- ブランチ: m2-ws-8 / ベース: cc259bc
- 日付: 2026-09-30
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | lint: `220 files already formatted` `All checks passed!`(exit 0)。test: `1016 passed, 172 deselected in 7.16s`(main時点977相当+breaker12+stub3+gateway11+events_cli3+g2gate21の新規39件増・全グリーン) |
| 2 | integration 12試験の収集 | 収集確認済み/test-ci=スーパーバイザー検証待ち | `uv run pytest --collect-only tests/integration/test_degraded_e2e.py tests/integration/test_k_limits_e2e.py -q` → `12 tests collected in 0.01s`(8+4) |
| 3 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic|time\.sleep|from time import' backend/src` → ヒット `core/clock.py:33: return datetime.now(UTC)` のみ(breaker.py・g2gate配下はヒットなし)。`pytest tests/unit/test_arch_no_direct_time.py -v` → `1 passed` |
| 4 | alembic・依存無変更 | PASS | `git diff main -- backend/alembic backend/pyproject.toml backend/uv.lock` → 空(diff-empty=YES) |
| 5 | 変更ファイル=§4の25ファイル | PASS | `git diff --name-only main | sort` 24ファイル+本報告書(report)=25。§4の作成13+変更11と完全一致(下記コミット一覧参照・`git status --short` 空) |
| 6 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` → 空(basename-empty=YES) |
| 7 | 既存unit試験グリーン維持 | PASS | make test 1016 passed。§4列挙(test_stub・test_gateway_jev_switch・test_group_engineへの追記)以外の既存試験期待値変更なし。変更した3試験はいずれも本単位の新規追記試験(breaker test_5・fb失敗不計上・AUC部分タイ=補足1〜3) |

**test-ci=スーパーバイザー検証待ち**(STATUS運用ルール1〜3。docker系・実APIは
実装側で未実行。wave単独のため並走とのDB取り合いは計画上なし)

## スーパーバイザー検証手順(design §4.3をそのまま実施)

1. `make lint && make test` — unit全件グリーン
2. `docker compose build api worker` — STATUS運用ルール4(composeのapi/workerは
   build型・ソースマウントなし。コード変更後の検証では再ビルドが先行必須)
3. `make test-ci` — 既存1125+新規12前後がグリーン・**worker復帰前にpurge-match-sub
   が走ること**(Makefile出力で「[events] purged subscription:」を確認)
4. test-ci後の残存・孤立行確認: users/intents/match_candidates系は従来手順
   (subject prefix等)+group_candidates/latchesの孤立行(intent_ids全要素が
   intentsに存在しない行)が**0件** — 対抗策の実効性実証(design §2.7)。
   1度目は既存残存の掃除SQLを先行実行:
   ```sql
   DELETE FROM group_candidates
   WHERE NOT (intent_ids <@ (SELECT array_agg(id) FROM intents));
   DELETE FROM latches
   WHERE NOT (intent_ids <@ (SELECT array_agg(id) FROM intents));
   ```
   (<@ = 包含演算子。「intent_idsの全要素がintentsに存在する」行を残す)
5. `make g2-gate -- --limit 2 --route both`(部分実行・実課金≈$0.02)でexit 0・
   docs/testassets/results/へレポート生成・partial=true明記。
   **520全件実行はG2時(本単位では実施しない)**
6. lint再実行(test-ciにlintが含まれないため・ws-4運用メモ): `make lint`
7. basename一意確認(運用ルール5)・alembic head=0005不変・時刻参照がclock.pyのみ
   (arch test)

## design §5 実装時確認事項の結果
- (なし — design §5の8件はすべて承認済みで実装時確認事項の留保はない。
  本計画§9のIF確定事項から変更した場合は下に記録する)

## 固定値の変更有無(design.md §2・本計画§9)
- breaker=Gateway内・プロセス内メモリ(案A・design §2.1): 変更なし
- BreakerParams既定値60.0/0.5/60.0/2+latency_threshold_s(§9-1): 変更なし
- JevJudgment.usage追加(§9-5): 変更なし
- worker_envのconftest移設+factory化(§9-11): 変更なし
- K上限E2E配置=計118 Intent・layer1_pass_count=101(§9-15): 変更なし
- 本計画§9のその他のIF確定事項: 変更なし(計画書テストコードの入力誤りに対する
  テスト側機械修正3件のみ — 補足1〜3。実装のIF・固定値は§9どおり)

## (G2・M3/M4への引継ぎ)
- G2: ハーネス(make g2-gate)と--limit動作確認まで実施済み。評価実行(520全件・
  実課金上限$15)・合格基準確定・オーナー判定がG2時の待ち事項(STATUS)
- M4: breakerのRedis共有(Worker複数構成確定時・IF不変)・D-16上限到達試験・
  #5本格負荷・性能5目標・レイテンシ分布注入
- M3-1: ws-7 Minor引継ぎ(b)(c)(d)

## コミット一覧
```
324a47c test: K上限裏付け+冪等性+02#7更新E2E+02#8 Bucket再評価の4試験(dense配置118 Intent・決定性2回実行)
dfcb917 test: 縮退E2E 8試験(切替・双障害skipped・breaker開放/半開往復・p95分離・復旧再評価・Worker一気通貫)
43a5813 refactor: worker_envをconftestへ移設+JevWorker注入factory(縮退E2E試験8の土台)
240b266 feat: g2gate runner/report/CLI+make g2-gate(両経路評価の1コマンド実行・部分実行partial明記)
9b1b769 feat: g2gate compare(P/R・ECE・Brier・Mann-Whitney AUC・band診断の純関数)
b768fab feat: g2gate cases(goldset読込・JevTextInput変換・SHA・fail-fast検査)
3a1867e feat: purge-match-sub CLIとtest-ciへの挿入(常設subscription掃除で孤立行を構造的に防止)
e420c75 refactor: _SELECT_GROUP_PAIRSへORDER BY追加(同ペア複数行の新行優先を明文化)
20c0939 feat: judge_pair第一候補側へcircuit breaker組み込み(開放中スキップ・p95計上・build_worker_gatewayで生成)
7a2d0a9 feat: Gatewayへcall_jev_first/call_jev_fallback公開IF+JevJudgment.usage(jev_smokeのプライベートアクセス解消)
7b5c40c feat: StubLLMへfail_jev_exc追加(429/529/timeout/接続障害の例外種別注入)
cf3193a feat: circuit breakerの状態機(llm/breaker.py)とunit試験12件
```
(この報告書コミットを含め13コミット)

## 補足(詰まった点・判断した点)

1. **Task 1 test_5のrecord順修正(テスト入力の機械修正)**: 計画書のtest_5は
   「2失敗→3成功」の順でrecordするが、2失敗目の時点でエラー率2/2=100%>50%と
   なり正しく開放する(test_3が検証するdesign §2.2の正仕様)。最終構成2/5=40%
   で不開放というtest_5の意図を分離検証するため「成功3→失敗2」の順(全時点で
   エラー率≤50%)へ並べ替えた。実装(breaker.py)は§9どおり無修正。
2. **Task 4 test_fallback_failure_does_not_touch_breakerの構成修正(同上)**:
   計画書の構成(第一候補=正常StubLLM・フォールバック=timeout)ではjudge_pairが
   第一候補の正常結果を返して終わるためフォールバックが呼ばれず、期待の
   LLMTimeoutErrorがraiseされない(DID NOT RAISEで失敗)。Review Focus 5の意図
   (フォールバック側の失敗がbreakerに計上されない)を検証する「第一候補429
   (切替発生)→フォールバックtimeout(双障害)」構成へ修正し、期待値を
   `((True, 0.0),)`(第一候補の失敗1件のみ計上・N=1<min_samplesで不開放)へ。
   実装・design §2.2の文言は無修正。
3. **Task 8 AUC部分タイの期待値修正(手計算誤りの訂正)**: 計画書コメント
   「U=(勝1+タイ0.5)/2=0.75」は手計算誤り。pos=[0.9,0.5]・neg=[0.5,0.1]の
   ペア勝率は「0.9>0.5勝・0.9>0.1勝・0.5=0.5タイ・0.5>0.1勝」=(3+0.5)/4
   =0.875。実装(_rankdata平均順位+U統計量)はMann-Whitney Uの標準形として
   正しいため、テスト期待値のみ0.875へ修正。
4. **Task 9 build_reportのキーワード引数名**: 計画書テストは`questions_sha256=`
   だが計画書実装のシグネチャは`questions_sha:`(計画書内で不一致)。実装と
   __main__.pyの呼び出しに合わせ`questions_sha=`へ統一。
5. **Task 2の既存試験数の食い違い**: 計画書「既存19件+新規3件」に対し実測は
   既存16件+新規3件=計19件。数値報告の誤りと判断(挙動・追加内容は計画書どおり)。
6. **作業開始直後の一時的test_worker.py失敗(環境起因)**: 最初のmake test実行時、
   共有ci-db(postgresコンテナ)が起動途中(接続拒否)でtest_worker.pyの9試験が
   ConnectionRefusedErrorで失敗した。コンテナhealthy後の再実行で全面グリーン。
   本単位の変更(breaker.py新規追加のみの時点)と無関係の環境タイミング。
7. **lint機械対応**: import順序(I001/E402)・行長(E501)・`zip()`への
   `strict=True`(B905・scores/goldsは同長生成のためTrueが意味的に正しい)・
   ruff format整形を都度適用(§0の規律どおり)。compare.pyの計画書局部import
   (PairExpected)はduck-typingで不要かつ循環しないため省略。
8. **jev_smoke.pyのdocstring**: FALLBACK分岐の説明を「直接呼び出し」から
   「call_jev_fallback公開IF経由(送信記録1件が出る)」へ更新(§9-18の趣旨の明文化)。
9. **test_degraded_e2e.py/test_k_limits_e2e.pyのlint対応**: 計画書コードから
   未使用import(LLMConnectionError・LLMOverloadedError)を除去・E501行長2箇所を
   分割。試験のロジック・期待値は無変更。

## 補足2(スーパーバイザー検証test-ci 20失敗+2エラーの修正 — 2026-09-30)

スーパーバイザーのtest-ci診断に基づく修正(integration再実行はスーパーバイザー
側・unit/収集で検証)。DB干渁のみの失敗(test_1/7・k_limits 2/3・既存ws-4/5/6)
には未対応(漏洩行はスーパーバイザーが掃除済み):

1. **teardownのFK欠落(カスケードの根本原因・両ファイル)**: latches提案時に
   latch_status_events行が書かれfk_latch_status_events_latchでFK違反→teardown
   エラー→行漏洩→他試験へのDB干渉。field fixture teardown(test_degraded_e2e)
   と_teardown_prefix(test_k_limits_e2e・fieldから呼ばれる)へlatches削除の前に
   latch_status_events削除を挿入。順序=match_candidates→latch_status_events→
   latches→group_candidates→match_events→intents→users。
2. **test_3・test_5のAttributeError**: first=StubLLM(fail_jev_exc=...)は
   judge_calls属性を持たないため計数assertでAttributeError。常時失敗+計数の
   _FlakyFirst(fail_calls=10**9, exc=LLMRateLimitError("429")/
   LLMOverloadedError("529"))へ置換(LLMOverloadedErrorをimportへ復帰)。
3. **test_6のoff-by-one**: timeout_at={18,19}では18呼び出し目がフォールバック
   切替となり冒頭のrange(18)のprovider=='typesafe_jev'assertが失敗。
   timeout_at={19,20}へ変更(呼び出し1〜18=第一候補成功・19・20=timeout・
   N=20でp95位置=6.0→開放・エラー率2/20=10%でp95単独条件の分離は維持)。
4. **k_limits test_1の除外時間組422**: 時間交差なし3名のstart=
   _future(BASE_HOURS+72)=192h=8日で+7日(expires_at)上限に抵触し422。
   _future(BASE_HOURS+43)(=163h<168h・基準120hとのΔ=43h>3h flexで非交差維持)
   へ変更。
5. 検証: `make lint`グリーン・`make test` 1016 passed・`pytest --collect-only`
   integration 2ファイル12件収集・差分は上記2ファイルのみ。
6. **test_schema.py由来の残行掃除(supervisor承認のスコープ追加)**:
   test_schema.pyは固定TS(2026-09-27 12:00:00+00)リテラルで行をINSERTして
   COMMITするが削除しないため、①latches/group_candidatesの構造的孤立行
   (intent_idsが参照先不在のランダムuuid)②match_eventsのpayload={}行
   (api relayのint(None)による永久再送ループの毒)が毎test-ci実行ごとに
   ci-db へ累積し、design §2.7の対抗策実証(検証手順4・孤立行0件)を成立
   不能にしていた。対処=ファイル末尾(全試験終了後)に走るautouse(module)
   teardownを追加し、削除条件 created_at = TS固定値 でこのファイルの挿入行を
   一意識別してFK依存の葉→根の順(calibration_records・latch_status_events
   〔latch_id経由〕→notifications〔user_id経由・防御〕→latches→
   group_candidates→match_events→intents→users)で削除。rollbackするCHECK
   試験は行を残さないため影響なし・試験本体の検証内容は無変更(teardownのみ)。
   削除順序について: 指示の列挙「users/intents/…の順でよい」に対し、
   intents.fk_intents_userがusersを参照するため文字どおりusers先だとFK違反に
   なることから、FK依存の実態どおりusersを最後にする逆順で実装した
   (latches→group_candidatesもfk_latches_group_candidateのため同順序)。
   スコープ追加の理由: 本単位の対抗策(purge-match-sub・field teardown)の
   実証条件「test-ci後の孤立行0件」がM0由来のこの汚染によって構造的に
   成立しないため(design §2.7・検証手順4の前提回復)。
   検証: `make lint`グリーン・`make test` 1016 passed・test_schema.py
   25件収集・実行はスーパーバイザー検証時(integration実行禁止のため)。
