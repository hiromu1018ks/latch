# M4 ws-6 完了報告(性能負荷試験+総当たり検出の器)

計画: docs/plans/M4/ws-6-plan.md・設計: docs/plans/M4/ws-6-design.md(7f8cc75)。本報告は計画§7の報告形式に従う。**性能5目標・総当たりの量的合格線の正式判定はG4の人間側**(design §2.7)であり・本報告と証拠YAMLは測定値と機械判定の記録である。

## 1. 実施サマリ

- **実施日**: 2026-10-04(実装+スーパーバイザー検証時の実施・同日)。
- **環境**: worktree `/home/misty/.herdr/worktrees/latch/m4-ws-6`・ブランチ `m4-ws-6`・基点 main head 6a0f943(alembic 0008・unit 1386 passed)。実施環境はローカルdocker(latch-loadプロジェクト・ci一時停止運用・Linux 7.2.5-3-omarchy・Python 3.13.15)。
- **`make test`(unit)実数**: 1438 passed・282 deselected(基準1386+新規unit 52件)。実施時の修正コミット投入後も再実行で1438の緑維持を確認。
- **`make test-ci` 実数**: 1720 passed(既存1664+新規unit 52+新規integration 4)。スーパーバイザー検証で2回実行・マージ後のmainでも1720を確認(**検証済み・検証待ち解消**)。
- **コミット一覧(実装・ブランチm4-ws-6)**:

| ハッシュ | 要約 |
|---|---|
| 1cfca37 | feat: settingsへstub遅延分布env 4本追加(既定0=無効)とllm集合ピン15項目化 |
| bce8e83 | feat: stub遅延の決定的ハッシュ分布化(50/44/6割付・p50>0で有効・既存固定msは無変更) |
| ce826f8 | feat: 層別/E2E/通知ヒストグラム3本追加とhttp bucket拡張(既存bucket値不変) |
| c3842e0 | feat: Embedding区間・Retrieval区間の層別観測挿入(本体ロジック無変更) |
| ef3e997 | feat: LatchEngineへL5+通知区間・初期判定E2E(route=create/activate)・通知区間の観測 |
| 05dc644 | feat: loadgen/schedule — 決定的送出時刻列とオープンモデル実行器 |
| 2ac0391 | feat: loadgen/client・massload — 注入クライアントと母集合DB直接投入(イベント発行ゼロ) |
| 3d2cf50 | style: ruff format適用(Task 7ファイル・整形のみ・挙動変更なし) |
| bca83af | feat: loadgen/collect・CLI — 証拠採取(Prometheus照合+機械判定)と4サブコマンド |
| c0e47ce | feat: load環境のPrometheus接続(別名targets)・stub遅延の10 §4.1既定・Makefileターゲット7本 |

- **実施時の修正コミット(main・すべてスーパーバイザー検証で投入)**: httpxプール上限500・証拠出力先のファイル位置固定・トークン事前取得(ウォームアップ)・compose.load.yamlへの`LATCH_LLM_STUB_JEV_NOUL=0.90`/`LATCH_LLM_STUB_JEV_SCORE=3.6`追加・収束タイムアウト600→2400秒・Makefile鍵パス明示・生成器の万件対応(残余割付の均等化+件数>2000時の全ペア集計省略)。これらは実施で判明した律速・設定漏れの是正であり・単位の確定値(design §2.2〜§2.5の採用判断)は変更していない。

## 2. 完了条件別の証拠(計画§6の1〜7)

1. **stub遅延の分布化が決定的で・p50/p95注入値が分位点として現れる**: unit `test_llm_stub_latency.py` 9件グリーン(決定性・固定key集合のnearest-rank分位点=計画§2.4の実計算値 {1000: 5058, 1750: 4329, 2500: 613}・優先順位「分布>固定ms」・embedding無分布)。既存`test_stub.py`・`test_gateway_*.py`・`test_llm_stub_response_settings.py`・`test_llm_factory.py`(集合ピン15項目化)は全て不変でグリーン。実負荷での分布再現はperf 4回目のparse p95=2.7378秒(注入分布p95=2.5秒込みのhttpレイテンシ分布)・jev_p95=5.4834秒(注入分布p95=5秒込み)として観測。
2. **層別・初期判定E2E・通知のヒストグラムが露出**: unit `test_obs_metrics.py`(許可リスト+9名・le集合に注入値と目標境界)・`test_obs_pipeline_metrics.py`(embedding区間=handle全体・retrieval区間=両経路)・`test_latch_engine.py`(L5+通知区間=提案確定経路のみ・通知区間=proposed遷移時のみ)がグリーン。Jev区間は既存`jev_latency_seconds`(ws-4)。実測はperf 4回目証拠YAMLのmeasurementsに露出。
3. **初期判定E2E観測の絞り込み**: unit `test_latch_engine.py` 新試験6件(created種Event+初回提案+通知記録までの3条件・route=create/activate分離・起点読取SQLのUNIQUE索引ピン)がグリーン。route判定の同一コミット等値比較はintents/service.pyの_create・_update_draft実装との突合を計画§2.1読み替え1に記録済み。実測は目標④参照(観測自体は解像度上限の張り付きあり・§3とG4確認事項)。
4. **注入器が決定レートで・応答を待たずに送出**: unit `test_loadgen_schedule.py` 6件グリーン(決定的時刻列・オープンモデル性=応答遅延下で送出間隔維持・エラー集計)+`test_loadgen_collect.py` 6件グリーン(load側jobのみのクエリ・window差し替え・Vector≤50機械判定)+CLIの`--help`確認済み。実負荷での送出はperf 4回目 ok=6090/6090・error=0で確認。
5. **総当たり母集合がLayer 1対象条件で決定的・べき等に投入・Event発行なし**: unit `test_loadgen_massload.py` 9件グリーン+integration `test_loadgen_massload_ci.py`(実INSERT・active条件・冪等性・自前掃除)がtest-ci 1720内でグリーン(2回)。実施では母集合1万→5万→10万の累積投入が追加分のみINSERTで成立(uuid5べき等・各段階の`intents_inserted`は追加分のみ)。
6. **load環境がPrometheusへ接続・stub遅延が10 §4.1値で立ち上がる**: unit `test_loadgen_compose_pins.py` 7件グリーン+既存`test_obs_compose_pins.py`・`test_compose_ports.py`(integration・収集確認)無傷。実スクレイプは手順§8観察どおり`up=1`(load-api1/2・load-worker1/2)を確認・perf 4回目のjev_p95=5.48秒(注入p95=5秒)で遅延既定値の有効性を裏付け。
7. **unit+integration試験グリーン・実施証拠がdocs/testassets/results/へ保存**: `make test` 1438グリーン・`make test-ci` 1720グリーン(2回・検証済み)。証拠YAML: `ws6-load-20261004-123006.yaml`(perf本命)・`ws6-bruteforce-10000/50000/100000-20261004-*.yaml`(3段階)・経緯として`ws6-load-20261004-105203/105538/113320.yaml`。

## 3. 性能5目標の測定値と合否の記録(perf 4回目・ws6-load-20261004-123006・窓60m)

| 目標 | 測定値 | 10 §4.1の目標 | 備考 |
|---|---|---|---|
| ① Intent保存 | p95=0.0589秒・p99=0.8031秒 | p95 500ms | 3回目はp95=0.547秒・p99=10.0秒(認証CPU競合の混入値として記録) |
| ② Parser構造化プレビュー | p95=2.7378秒・p99=2.9476秒 | p95 3秒・p99 8秒 | parse独立相で採取 |
| ③ Candidate Retrieval | p95=0.6250秒 | p95 1秒 | 層別=Retrieval区間 |
| ④ 初期判定E2E | route=create p95=10.0秒(上限張り付き)・route=activate 観測なし(null) | p95 10秒 | **解像度上限の張り付き・縮退込み値**(後述) |
| ⑤ 通知 | p95=4.9230秒 | 判定後5秒以内 | D-16縮退込みの分布 |

- **層別4区間の内訳**: embedding p95=10.0秒(上限張り付き)・retrieval p95=0.6250秒・proposal p95=10.0秒(上限張り付き)・Jev p95=5.4834秒。embedding・proposal・E2E(create)がbucket表の上限10.0に張り付いたのは・**run4がD-16日次上限(30000)到達による評価繰越し縮退(仕様動作)を含むため**であり・設計§2.3のbucket選択の解像度上限でもある(観察事項③・G4確認候補)。
- **queue pendingの推移**: 窓内最大1486・採取時点1476(繰越し分)。収束判定(pending=0安定30秒)は・縮退下の30分バケット再評価循環により2400秒延長後も成立せず・**収束成立を待たずに手動採取**した(読み替え・G4確認事項へ)。
- **クライアント側応答時間の参考値**: client_p95=1787.5ms(投入6090件全成功・error=0)。測定の主系統はサーバ側ヒストグラム(計画§2.5)。
- 提案6316件・通知4888件を生成した後・D-16日次上限へ到達し評価繰越しに縮退(仕様動作)。
- **合否の判定はG4の人間側**(design §2.7)。本表は測定値の記録であり・合格宣言を含まない。

## 4. 総当たりの段階比較表(1万→5万→10万・各段階100件注入・収束35秒×3)

| 指標 | 1万 | 5万 | 10万 | 5倍段階の増幅(×5に対し) | 10倍段階の増幅(×10に対し) |
|---|---|---|---|---|---|
| Vector件数(30分総数・le50率=inf率) | 120.00 | 218.82 | 319.66 | 1.82倍 | 2.66倍 |
| Retrieval p95 | 0.8812秒 | 0.9292秒 | 0.5125秒 | 1.05倍 | 0.58倍 |
| L1通過率(pass/input・参考素材) | 160.59 | 109.68 | 99.34 | 0.68倍 | 0.62倍 |
| Jev実行回数増分(30分総数) | 1124.37 | 1868.57 | 2673.28 | 1.66倍 | 2.38倍 |
| イベントあたりJev回数(増分÷100注入) | 11.24 | 18.69 | 26.73 | 1.66倍 | 2.38倍 |

- **Vector件数≤50の機械判定**: 3段階とも `vector_items_le_50_ok: true`(le="50.0"累積=総累積+Inf。上限50超えの観測ゼロ)。
- **観測値の前提**: 全指標は30分バケットの**母集合定期再評価分(母集合比例の背景流量)を含む**。Vector件数・Jev回数が母集合に比例して増えるのは背景流量の寄与であり・注入100件への応答の急増(増幅)と区別した判定をG4で行うこと。Retrieval p95は段階間で増幅せず(最終段階でむしろ0.51秒)・K_v=50固定でIntent数と無関係に安定する方向の観測。L1通過率が1超えの値は・30分窓内のcounter増分比(通過の積算が入力runsを上回る・定期再評価の段階積算)による参考素材である。
- base-datetimeは**2026-10-05T00:00:00+09:00**(計画例示の2026-10-04T11:00から変更: 注入窓=実行時刻+3時間の深夜帯と母集合のtime範囲の重なりを確保するため・べき等投入のため最初の投入前に確定が必要だった)。
- 正式判定(増幅率の許容解釈を含む)はG4の人間側・量的合格線の新設は10 §6のdocs改版が前提(機械判定YAMLのnoteどおり)。

## 5. designからの読み替え・逸脱の記録(計画§2.1の1〜9の実施結果)

| # | 項目 | 実施結果 |
|---|---|---|
| 1 | 起点列=match_events.created_at(created種・version一致) | **実装どおり**。_SELECT_INJECTION_ORIGINのSQLピン試験がUNIQUE索引列を検証・active作成ではintents.created_atと同一値・draft→active化ではactive化コミット時刻 |
| 2 | チェーン判定=LatchEngineが提案確定時にDB照合(worker側状態保持なし) | **実装どおり**。照合結果None(created種Eventなし)で観測対象外。unit試験のassert表現を「照合は走る・初回判定カウントは走らない」に整合させた(§9の実装時差分2) |
| 3 | perf区間=3相+収束待ち | **実装どおり**(相A parse→相B save(active2980+draft10+active化10)→相C burst 100→収束待ち)。実施時に**収束待たずの採取**へ運用読み替え(run4・§3参照・G4確認事項へ) |
| 4 | 注入ユーザー=負荷ユーザー(loadusers1000人) | **実装どおり**。perf・総当たりとも負荷ユーザーから注入・段階間は--first-user 1→101→201で範囲変更 |
| 5 | stub遅延既定=10 §4.1値をcompose既定(`${VAR:-}`形式)+load-stub-flatで0切替 | **実装どおり**。実施時に既存stub Jev判定値`LATCH_LLM_STUB_JEV_NOUL=0.90`/`SCORE=3.6`をcompose.load.yamlへ追加(staging同一値・計画時に存在を見落とした設定で・新規の機構追加ではない) |
| 6 | prometheus target名=load側別名(load-api1等) | **実装どおり**。compose.load.yamlのaliases+prometheus.ymlのload job2本・Docker DNSあいまい解決を回避・実スクレイプup=1確認 |
| 7 | Makefile追加ターゲット7本 | **実装どおり**(loadusers/loadperf/loadbf/loadmass/load-stub-flat/load-migrate/load-geo-import)。実施時に鍵パスの明示修正を検証側で投入 |
| 8 | massload=純関層(unit)+実DB検証(integration)の2層 | **実装どおり**。unit 9件・integration 4件(test-ci 1720で2回グリーン) |
| 9 | token発行=issue_idp_token関数直呼び | **実装どおり**。実施時に測定前トークン事前取得(ウォームアップ・キャッシュTTL1時間)を追加(RSA署名のAPI側直列化対策・§6参照) |

**計画§0固定値への変更**: なし。上記は実装・運用上の追加・是正であり・自作asyncio注入器・決定的ハッシュ割付50/44/6・ヒストグラム3本+bucket値・massload列設計・総当たりレイテンシ注入なし・ci停止/復帰のMakefile外置き等の確定値はすべて計画どおり。

## 6. 実施パラメータの実測記録(手順§8との差分)

| 項目 | 手順初期値 | 実測 |
|---|---|---|
| perf投入 | 6090件(3000+2980+10+100) | 4回目: ok=6090・error=0・client_p95=1787.5ms(1回目ok=595[PoolTimeout]→2回目ok=595相当でトークン律速確認→3回目ok=6017→4回目全成功) |
| perf収束(wait_settled) | 上限600秒 | 2400秒へ延長後も不成立(D-16縮退下の30分バケット再評価循環)→pending=1476残存時点で手動採取・窓60m |
| 母集合投入所要 | 実測を記録 | 1万=0.2秒・10万=2.0秒(生成器の万件対応後・残余割付均等化+全ペア集計省略) |
| 総当たり収束 | 上限600秒 | 各段階35秒で成立(×3) |
| 総当たり注入 | ok=100/段階 | ok=100×3(全段階で201) |
| base-datetime | 例示2026-10-04T11:00+09:00 | 2026-10-05T00:00:00+09:00(§4の理由) |
| geo-import・loadusers・stub-flat | 手順どおり | 手順どおり実施 |
| 片付け | load-down・dashboard-down・make up | 実施済み(ci常設復帰・healthy確認) |

## 7. スーパーバイザー検証の記録(test-ci)

計画時は「test-ci: スーパーバイザー検証待ち」(STATUS.md運用ルール1)だったが・**検証済み**: `make test-ci` 1720 passed(既存1664+unit 52+integration 4)を検証時に2回実行し・マージ後のmainでも1720を確認。worktree側のlint・unit 1438も修正コミット後に再実行して緑維持。

## 8. G4時確認事項の付記依頼(スーパーバイザー側で記録)

計画§7-8の2件に加え・実施時に判明した読み替え・観察を確認候補として付す。

1. **(計画§7-8)層別・E2E・通知の計測をCloud Monitoring+OTLPでなくPrometheusヒストグラムで実装した代替差分**(G4時確認事項2の延長)。
2. **(計画§7-8)目標②をparse独立相で採取した負荷構成の読み替え**(負荷条件=相B+相Cで字義どおり・目標②のみ負荷なし独立相)。
3. **収束成立を待たない証拠採取**: run4はD-16縮退下の30分バケット再評価循環でpending=0が確定せず(pending=1476残存時点)・窓60mで手動採取した。採取時点の分布に繰越し分が含まれる。
4. **stub Jev判定値のcompose.load.yaml追加**(LATCH_LLM_STUB_JEV_NOUL=0.90/SCORE=3.6・stagingと同一値): 計画時にload composeへの設定が漏れていた既存envの是正。3回目はnoul=0.5の既定のまま提案0件(閾値0.60未達)となり・目標④⑤が測定不能だった。
5. **測定前トークン事前取得(ウォームアップ)**: IdPトークン→access_token交換のRSA署名がAPIイベントループで直列化し約27req/sが上限だった(2回目)。認証スループット約27req/s(2インスタンス)自体を実特性として確認する候補。
6. **bucket上限10秒の張り付き=解像度上限**: embedding・proposal・E2E(create)のp95が10.0に張り付いたのは・run4の縮退込み値に加え・設計§2.3のbucket選択(上限10秒)では縮退時の長区間が解像できないため。D-16縮退時の分布観察を要件に入れる場合はbucket拡張が候補。
7. **D-16縮退時の30分バケット再評価循環でqueue pendingが残存し続ける**: ws-7の縮退試験と関連する構造的観察(仕様動作の縮退と収束判定の相互作用)。
8. **dashboardのstaging外部ネットワークはstaging停止時の手动作成で代替**: staging停止中はstagingジョブがload側を二重採取するがjob labelで分離済み(nginx別名解決の名前衝突はaliasesで回避済み)。**load-stub-flat後のnginx再起動が必要**(ws-3の知見・手順に明記なし)。
9. **base-datetimeの変更**(2026-10-05T00:00:00+09:00): 注入窓と母集合time範囲の重なり確保のため(§4)。

## 9. 詰まった箇所と判断

### 9.1 実装時(agent3・計画書からの軽微な差分・仕様変更なし)

1. **prometheus-client 0.26の遅延生成対応**: 計画の試験コードはseries未生成状態でle集合・family名を採取する箇所があり・実挙動では空になった。`test_injection_value_buckets_are_exposed`へobserve1回を追加・`_e2e_observed_total`/`_notify_observed_total`のfamily名フィルタをnamespace込み(`latch_initial_e2e_seconds`等)へ修正(既存`_sample_names`ヘルパーのdocstringと同一実挙動)。
2. **E2E観測試験のassert整合**: 計画の`test_handle_passes_none_ctx_without_created_event`は「起点読取自体が走らない」ことを要求したが・読み替え2で確定済みのDB照合方式では提案確定時に照合が常に走り・照合結果Noneで観測対象外になる。試験の意図(観測対象外)を保ち・「照合は走る/初回判定カウントは走らない」のassertへ修正。
3. **seed実値との突合**: 計画のmassload試験はseed intents[0]のuserを`SU-001`と仮定したが・決定的生成の実値は`SU-010`(かつ複数userが混在)。試験の意図(user_idマップ引き)を保ち・マップをseed全userから組む`_test_ids_map`へ修正。
4. **軽微な補整**: observe3関数の未使用import回避(既存慣行どおりmodule経由)・`_read_e2e_context`の型注釈`Origin`のTYPE_CHECKING import追加・collect.pyのdocstring文言がarch testの禁止トークン検索(`datetime.now`/`time.time`)に引っかかったため言い換え・E501行長対応・ruff format整形差分(各コミットでlint+testグリーンを確認)。

### 9.2 実施時(スーパーバイザー検証・4回のperf実行と運用事故)

1. **1回目(ws6-load-20261004-105203)**: 注入器httpxプール既定100本でPoolTimeout(ok=595/6090・client_p95=2032500.1ms)→プール上限500へ修正。
2. **2回目(ws6-load-20261004-105538・部分投入確認)**: トークン交換(/v1/auth/token)のRSA署名がAPIイベントループで直列化し約27req/sが上限・ローテーションsubjectでの都度交換が律速→perf/bruteforceへ測定前トークン事前取得(ウォームアップ・キャッシュTTL1時間)を追加。
3. **3回目(ws6-load-20261004-113320)**: 投入ok=6017/6090。ただしload composeにstub Jev判定値の設定がなく既定noul=0.5のまま→全ペアlatch_score=0.5で閾値0.60未達・提案0件(目標④⑤測定不能)と判明→compose.load.yamlへLATCH_LLM_STUB_JEV_NOUL=0.90/SCORE=3.6を追加(stagingと同一)。save p95=0.547秒は認証CPU競合の混入値として記録。
4. **4回目(本命・123006)**: 投入6090件全成功・error=0(§3参照)。パイプライン消化中にD-16日次上限へ到達し評価繰越し縮退(仕様動作)・30分バケット再評価循環のため収束不成立→pending=1476残存時点で手動採取(窓60m)。
5. **運用事故の記録(正直な記録)**: 総当たり初回実行はシェルの作業ディレクトリがbackend/のままだったため全ステップが失敗していたのに・パイプ(`| tail`)が終了コードを隠し「成功」と誤認。汚染環境(perf run4の残存3,090 intents・枯渇予算)でmassload 1万だけ実行された状態で気付き・全`down -v`再リセット後に3段階をやり直した(検証は件数実査で実施)。**以後の検証チェーンからパイプを排除**。
6. 生成器の万件対応(残余割付の均等化+件数>2000時の全ペア集計省略)は実施時修正としてmainへ投入(§1の実施時修正コミット一覧)。

### 9.3 その他

- なし(上記のとおり実装時の停止・確認はすべて解消済み・仕様矛盾による実装停止は発生しなかった)。
