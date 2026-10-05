# M4 ws-7 実施報告(非機能試験・障害系)

## 1. 実施サマリ

- 実施日: 2026-10-05
- 環境: git worktree `m4-ws-7` / ブランチ `m4-ws-7` / 基点 main HEAD `8f8089d`(計画書§0表記の`e02446d`にdocsコミット2件が乗った状態・コード状態は同一)
- `make test` 実数: **1472 passed**(基準1438+新規unit 34件。計画§5の期待値1471に対する+1の内訳は「4. 逸脱の記録」の末尾)
- `make lint`: 全コミットでグリーン(ruff format --check + ruff check)
- ハーネス収集: `staging_tests_nfr` 19件(n5=6・n4=2・n3=5・n2prep=1・n2=4・n1=1・`--import-mode=importlib`で確認)
- コミット一覧(計11件・いずれも `make lint && make test` グリーン後に作成):

| ハッシュ | 内容 |
|---|---|
| `15bbf8f` | settingsへQueue lag alert系env 5本追加(既定無効) |
| `3d06871` | Queue lag alert発報器(API側周期タスク・アームキー単発化) |
| `2c33ef6` | lifespanへQueue lag alertタスク登録(enabled時のみ・独立Redis接続) |
| `191601d` | 合成監視プローブ(main/dense・マーカータグ・token再発行) |
| `51a4365` | nfrtools d16 CLI(D-16カウンタ操作・Clock導出キー) |
| `a9dd314` | staging NFR/recovery override+Makefile 9ターゲット |
| `db8c939` | NFRハーネス土台+n5 Queue障害試験 |
| `bbb8f05` | n4ハーネス(resume再評価・catch-up回収とNULL除外) |
| `7b0c620` | n3ハーネス(D-16 alert境界・上限deny・日次/月次リセット) |
| `24c9e38` | n2ハーネス(prep・cross-check・識別子NULL化→backfill・スモーク) |
| `3ed917f` | n1ハーネス(プローブYAML検証)+ハーネス全収集確認 |

staging環境での実施(§8の手順・n1の60分試験窓を含む)は本報告の対象外で、スーパーバイザー検証時に実施する。本報告の時点で完成しているのは、試験を実行するための器(unit試験34件・ハーネス19件・override 2本・Makefile 9ターゲット・CLI 2種)までである。

## 2. 完了条件別の証拠(計画§6の1〜7)

1. **Queue lag alert発報器**(条件成立中1回・解除resolve・2インスタンスで二重発報なし・既定無効): unit `test_obs_lag_alert.py` 8件パス(発報1回・再発報抑制・resolve+再武装・クロスインスタンス単発化・Clock年齢・Redis故障fail-open・DB失敗握り・アームTTL)。`test_nfr_pins.py` のlifespan静的ピン・compose.staging.yaml無傷ピン・`test_settings.py` のalert系集合ピン3関数もパス。staging実経路(test_n5_50)は検証待ち。
2. **合成監視プローブ**: unit `test_loadgen_probe.py` 8件パス(タグ分母除外・タイムアウト集計・連続失敗・dense分離・空集計・run_probe決定性・マーカータグ付け・token再発行)。実測(§8手順6のYAML+test_n1_10)は検証待ち。
3. **D-16カウンタ操作**: unit `test_nfrtools_d16.py` 8件パス(Clock導出キー2種・往復・上書きTTL・削除・scope検証・CLI配線2種)。`uv run latch-nfrtools --help` と `python -m latch.nfrtools --help` の両経路で動作確認済み。実経路(test_n3_10〜50)は検証待ち。
4. **resume再評価とcatch-up回収**: ハーネス `test_n4_reeval.py` 2件を作成・収集確認済み。staging実行は検証待ち。
5. **バックアップ復旧**: Makefile手順5本(staging-dump・db2-up・restore・db2-switch・db2-down)+ハーネス `test_n2_prep.py`(1件)・`test_n2_backup.py`(4件)を作成・収集確認済み。実施は検証待ち。
6. **Queue障害**: ハーネス `test_n5_queue.py` 6件を作成・収集確認済み。実施は検証待ち。
7. **unitグリーン・ハーネスtestpaths外・証拠YAML**: `make test` 1472 passed(実数。基準との差分は「4. 逸脱の記録」)。`pytest -m "not integration" --co -q` で1472/1754 collected(1754=1472+deselected 282)・`make test`の収集にstaging_tests_nfrは含まれない。証拠YAML(`ws7-nfr-result-*.yaml`・`ws7-probe-*.yaml`)は§8実施時に生成される。

## 3. 4領域の実施結果

**スーパーバイザー検証待ち。** §8の手順に沿って実施した時点で、次の欄に実測値を追記する。

- (a) 可用性: 試験窓・main成功率・除外サンプル数・dense観察・docker kill時刻とhealthy復帰時刻・api2計画停止窓のタグ確認 — 未実施
- (b) バックアップ復旧: RTO実測(復元開始→pg_restore完了・復元開始→スモーク提案確帰)・NULL化件数・backfill回収所要・cross-check数値 — 未実施
- (c) D-16: alert/上限/リセットの各観察値・上限到達後のqueue pending推移の時系列観察 — 未実施
- (d) Queue障害: 毒・参照不在・重複・非波及・lag alert往復の各観察値 — 未実施

## 4. designからの読み替え・逸脱の記録

### 4-1. 計画§2.1の読み替え12項目の実装結果

読み替え1〜12は、いずれも計画の値どおりに実装した。変更はしていない。必須記録の4項目について実装の事実を記す。

- **読み替え1(上限到達の境界)**: 読み替えどおり「set 30,000→実Event→INCR=30,001でdeny」を試験が要求する形に実装した(`test_n3_20_daily_limit_deny_skip`)。alert境界は「set 23,999→INCR=24,000」(`test_n3_10`)。guard.pyの判定式(`> DAILY_LIMIT`)には触れていない。
- **読み替え3(override 2本)**: `compose.staging.nfr.yaml`(api1/api2へ`LATCH_ALERT_QUEUE_ENABLED: "true"`のみ)と`compose.staging.recovery.yaml`(db2定義+api/workerのDB向き先+backfill周期)の2本に分けた。`compose.staging.yaml`は1行も変更していない(無傷ピン`test_compose_staging_untouched_by_nfr`で機械担保)。
- **読み替え5(updated連用+advance 1900秒)**: `_publish_updated`ヘルパが「clock advance 1900秒→現行version+1のupdated投下→収束待ち」の順で実行する(`test_n3_d16.py`)。
- **読み替え7(catch-upのadvance計算)**: `test_n4_20`は`expires_at`をDB照合し、`advance_s = remaining - 6000`で「2時間を切る」状態を機械的に作る。固定値は使っていない。

### 4-2. 計画書テストコード・実装コードの欠陥修正(スーパーバイザー補足の慣行による)

計画に書かれたテストコードと実装コードの間で矛盾が13件見つかり、いずれも「計画書テストコードの欠陥」として修正した。design.md・docs本文に抵触する変更はない。

| # | ファイル | 欠陥の内容 | 修正 |
|---|---|---|---|
| 1 | `lag_alert.py` | 計画テストはresolveイベントの`kind == "queue_lag_resolve"`を要求するが、計画実装はalertと同じ`kind: "queue_lag"`を流用していた | resolve時に`kind`を`queue_lag_resolve`へ差し替えて発行 |
| 2 | `test_obs_lag_alert.py` | 「解除→再武装」試験の最終assertが、再発報後(=再アーム後)のキーにNoneを要求し、`test_arm_ttl_is_set_on_redis_key`と矛盾していた | 最終assertを「再アーム済み(`== "1"`)」へ訂正。解除の証明は2回目の発報成功(SET NXが成功=解除済み)が担う |
| 3 | `lag_alert.py`/`test_obs_lag_alert.py` | 計画実装は注入`request_path`を同期呼び出しする一方、計画テストはasync関数を注入しており、coroutine未awaitの警告が出ていた | `await request_path(...)`に変更(注入側との契約をasyncへ統一) |
| 4 | `test_loadgen_probe.py` | 「3ticks/5ticks」を期待するが、実装(1tick=2パス記録)では6/10サンプルになる。§8手順6の期待「main=120」(=60ticks×2パス)とも実装側が整合する | 期待値を6/10へ訂正し、数え方をdocstringに明記 |
| 5 | `test_loadgen_probe.py` | token再発行試験がコメント「合計51分」に対し実際の経過は11分しかなく、3回目のbearerが再発行せず`issued == [1, 1, 2]`を満たせなかった | 51分到達のadvanceを追加(あわせて11分時点の非再発行も追検証) |
| 6 | `test_loadgen_probe.py` | 同試験の期待`[1, 1, 2]`は、スタブが毎回リテラル`1`をappendする仕様と矛盾し(2回の再発行なら`[1, 1]`が正)、決して出ない | 期待を`[1, 1]`へ訂正(最終トークン`tok-2`のassertと合わせ「再発行2回」を検証) |
| 7 | `probe.py` | 計画の`TokenCache.__init__`が鍵ファイルを即時読むため、計画テストが`keys_dir=None`でサブクラス化できなかった | 鍵の読み込みを`_refresh`へ遅延。CLIは起動直後の`bearer()`で即読みのためfail-fast挙動は不変 |
| 8 | `nfrtools/__main__.py` | 計画テストはset出力に`jev:daily:20261005`を要求するが、計画実装の`run_set`はキー名を出力していなかった | `run_set`が実際のキー名を出力するよう変更(stagingでの読み戻し実査にも資する) |
| 9 | `nfrtools/__main__.py` | 計画の`build_parser`が`value`に`type=int`を付け、計画テストの`args.value == "479999"`(文字列)および`run_set(value: str)`のシグネチャと矛盾した | `type=int`を外し、`run_set`内でint変換 |
| 10 | `nfrtools/__main__.py` | 未使用import `SystemClock`(F401)と、docstringの「実時間の表示はSystemClock経由」(実態はControlledClock)の食い違い | import除去+docstringを実態どおり訂正 |
| 11 | `test_n4_reeval.py` | 未使用変数`intent_e`(F841) | 代入を外し作成のみに(相手Intentのidは照合に使わない) |
| 12 | `test_n2_backup.py` ほか | 計画コード由来の未使用import(`uuid`・`json`・`Awaitable`)とE501行長超過が数件 | import除去・行分割。挙動は不変 |
| 13 | `staging_tests_nfr/conftest.py` | 計画の「obs側のsys.pathハックは不要」は成立しない。`--import-mode=importlib`では収集ディレクトリがsys.pathに入らず、テストモジュールの`from helpers_nfr import`が解決できない(収集確認で機械的に確認) | conftest冒頭へ自ディレクトリのsys.path挿入+`sys.modules.setdefault("conftest", ...)`を追加(obs側conftestと同一作法)。テストの`from conftest import`がpytest読み込み済みの同一実体を指すよう保証 |

### 4-3. unit試験の総数

計画§5の「新規unit試験の総数33件」に対し、実数は**34件**である。Task 5が`test_nfr_pins.py`へ追加するpyprojectピン1件(`test_pyproject_has_nfrtools_entry`)が§5の集計から漏れていた(Task 5の節自体には「1件追加」と明記がある)。内訳: Task 1=3・Task 2=8・Task 3=1・Task 4=8・Task 5=9・Task 6=5。`make test`は**1472 passed**(1438+34)。

## 5. 実施パラメータの実測記録

**スーパーバイザー検証待ち。** §8実施時に、シード投入所要・n5〜n3の各所要・dump/restore所要・プローブ窓実時間を記録する。

## 6. スーパーバイザー検証待ちの記録

- **test-ci: 変更なし(実行不要)。** 本単位はintegration試験を追加していないため、test-ciの実数は既存1720件のまま動かない。ci-dbへの書込み経路も追加していない(ハーネスの接続先はstaging宛15432/16543/16379のみ・UPDATEはn4とn2のembedding NULL化の2箇所)。
- staging環境の実施(起動・ci一時停止・試験実行・ci復帰)は§8の手順でスーパーバイザーが行う。

## 7. G4時確認事項の付記依頼(supervisor側で記録)

1. lag alert発報器はworker内collector(docstringの「gaugeを消費する」)でなくAPI側周期タスクに新設した(同じDB集計値を消費する意と解釈)
2. alert媒体は構造化ログ(latch.cost.alert/latch.pipeline.alert)のまま・alertmanager等への差し替えはPhase 0以降
3. circuit breakerのRedis共有(M2 ws-8引継ぎ)は本単位スコープ外・staging worker2台でbreaker状態はプロセス独立

## 8. 詰まった箇所と判断

- **ci常設環境の停止による`make test`9件失敗(Task 1の検査時)**: 作業開始直後の`make test`がDB接続拒否(127.0.0.1:5432)でtest_worker.py系9件が落ちた。ci-dbのコンテナが停止していたのが原因で、`make up`(ci常設環境のhealthy待ち)で復帰させたうえで再実行し、全件パスを確認した。staging環境は一度も触れていない。失敗の全件は私の変更と無関係な既存試験で、環境要因と特定できてから次へ進んだ。
- **lint未グリーンのままコミットした2件の訂正**: Task 7とTask 11で、整形・行長の残差を確認する前にコミットした。どちらも直後に`ruff format`/`ruff check --fix`を当て、同じ内容のamendでコミットを置き換えた。最終状態では全11コミットがlint+testグリーンで、ワークツリーはクリーンである。以後のタスクではlint通過を確認してからコミットする順序に徹した。
- **収集確認の副産物**: `pytest --collect-only`でもconftestの`pytest_sessionfinish`が空の証拠YAMLを書き出すため、都度削除した。§8本番実行時のresults/ディレクトリを汚さないための措置である。

それ以外に、推測で仕様を変えた箇所はない。計画書内部の矛盾はすべて「4-2. 欠陥修正」に記録したとおりで、10分以上詰まる事態は発生しなかった。
