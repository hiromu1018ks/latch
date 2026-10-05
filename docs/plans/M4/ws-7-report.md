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

staging環境での実施(§8の手順)は**2026-10-05にスーパーバイザーが完了**した(§3に実測値・§9に経過。マージ後main=3f9b16b+検証fix 4コミットで実施・test-ci 1754 passed)。ハーネス全19件(n5=6・n4=2・n3=5・n2prep=1・n2=4・n1=1)が合格・証拠は `docs/testassets/results/ws7-nfr-result-*.yaml`(5本)+`ws7-probe-*.yaml`(1本)。実装本体への追加修正はlag_alertのresolveログレベル1件のみ(それ以外の修正はすべてハーネス・試験手順側)。

## 2. 完了条件別の証拠(計画§6の1〜7)

1. **Queue lag alert発報器**(条件成立中1回・解除resolve・2インスタンスで二重発報なし・既定無効): unit `test_obs_lag_alert.py` 8件パス(発報1回・再発報抑制・resolve+再武装・クロスインスタンス単発化・Clock年齢・Redis故障fail-open・DB失敗握り・アームTTL)。`test_nfr_pins.py` のlifespan静的ピン・compose.staging.yaml無傷ピン・`test_settings.py` のalert系集合ピン3関数もパス。staging実経路(test_n5_50)は検証待ち。
2. **合成監視プローブ**: unit `test_loadgen_probe.py` 8件パス(タグ分母除外・タイムアウト集計・連続失敗・dense分離・空集計・run_probe決定性・マーカータグ付け・token再発行)。実測(§8手順6のYAML+test_n1_10)は検証待ち。
3. **D-16カウンタ操作**: unit `test_nfrtools_d16.py` 8件パス(Clock導出キー2種・往復・上書きTTL・削除・scope検証・CLI配線2種)。`uv run latch-nfrtools --help` と `python -m latch.nfrtools --help` の両経路で動作確認済み。実経路(test_n3_10〜50)は検証待ち。
4. **resume再評価とcatch-up回収**: ハーネス `test_n4_reeval.py` 2件を作成・収集確認済み。staging実行は検証待ち。
5. **バックアップ復旧**: Makefile手順5本(staging-dump・db2-up・restore・db2-switch・db2-down)+ハーネス `test_n2_prep.py`(1件)・`test_n2_backup.py`(4件)を作成・収集確認済み。実施は検証待ち。
6. **Queue障害**: ハーネス `test_n5_queue.py` 6件を作成・収集確認済み。実施は検証待ち。
7. **unitグリーン・ハーネスtestpaths外・証拠YAML**: `make test` 1472 passed(実数。基準との差分は「4. 逸脱の記録」)。`pytest -m "not integration" --co -q` で1472/1754 collected(1754=1472+deselected 282)・`make test`の収集にstaging_tests_nfrは含まれない。**test-ciは新規unit 34件の分だけ実数が動く(1720→1754の期待)** — 本報告書初稿の「test-ci 変更なし・実行不要」は誤り(supervisor検証時に訂正・unit追加はtest-ciの収集対象)。証拠YAML(`ws7-nfr-result-*.yaml`・`ws7-probe-*.yaml`)は§8実施時に生成される。

## 3. 4領域の実施結果

2026-10-05にスーパーバイザーが§8の手順で実施(マージ後main・staging-nfr-reset環境)。
証拠: `docs/testassets/results/ws7-nfr-result-20261005-{154948,155216,160332,161102}.yaml` + `ws7-probe-*.yaml`(n1)。

### (a) 可用性(10 §4.3)— n1(2026-10-05 17:24開始・証拠 ws7-probe-20261005-104449.yaml)

- **main成功率 100%(38/38サンプル)・機械判定 verdict_995=true**。dense(10秒間隔)260サンプル併設。計画停止タグ除外 6サンプル(分母外・タグ機構の実証)
- 自動復旧の実測: api1プロセス死亡(SIGTERM・17:39:39)→**30秒後 running+healthy**(restarts=3・StartedAt更新・restartポリシーによる自動再起動)。残存api2での処理継続はdenseプローブの連続成功で観察
- api2計画停止: 17:41:09〜17:44:10(3分・マーカーON)→復帰後30秒でマーカー除去。当窓6サンプルがtagged除外
- **試験窓の実効44分(main 44サンプル)**: 実施マシンが約18:08〜19:44スリープし・ホストごと停止(サービス起因ではない)。プローブは復帰後にduration超過を検知して正常終了。15分安定→api1死亡と自動復帰→api2計画停止→事後安定24分の**全観察点は網羅**(2026-10-05ユーザー裁定: 44サンプルで成立とし理由を本報告書に記録)
- **実施上の発見(手順の読み替え・報告書記録義務)**: 当初手順の`docker kill`(SIGKILL)はDockerの仕様で手動停止扱いとなり`restart: unless-stopped`の再起動対象外(実測: 15分以上 exited のまま非復帰・試験窓1回目を破棄)。よって「稼働中インスタンスの強制停止→自動復旧」の演出は**コンテナ内PID 1へのSIGTERM**(uvicornのハンドラで受信→終了=自然死→ポリシーが自動再起動)に変更。SIGKILLと異なりgraceful終了だが・「インスタンス死亡→残存での処理継続→ポリシーによる自動復帰」の検証本体は同じ(10 §4.3字義からの読み替え・G4時確認事項へ記録)

### (b) バックアップ復旧(10 §4.4) — n2

- RTO実測: db2起動開始→healthy **9秒**(15:52:42→15:52:51)・復元開始(docker cp+pg_restore)→完了 **1秒**(15:52:51→15:52:52)・切替(up --force-recreate api/worker)→API healthy **8秒**(15:52:52→15:53:00)。復元開始→スモーク提案確定 = n2ハーネス完了時点(15:5x〜16:0x・テスト所要込み)
- NULL化件数: active 8件(識別子 embedding_model=gemini-embedding-001 で対象特定)→ backfill回収 8件(**回収所要約10秒**・recovery overrideのinterval 5秒)
- cross-check: 復旧前(15432・静止後)=6 / 復旧後(16543)=6 で一致(n2_40のスモーク2件は復旧後に追加)
- スモーク: 新規ペア→提案(proposed 257)→通知記録(384→386)→GET /v1/latches 200(実測値はws7-nfr-result-20261005-160332.yaml)

### (c) 縮退 D-16(10 §4.5残部) — n3

- 日次80%: set 23,999→実更新→INCR=24,000で **daily_80pct alert 1回**(源流レポート添付をalert行JSONで確認)
- 日次上限: set 30,000→INCR=30,001で deny(global_daily)・候補 skip(status=skipped・jev_result NULL=「Jev未判定」保留)・提案数不変(244→244)
- 日次リセット: 0時JST越えで新キー(jev:daily:20261007→20261008)切替・INCR=1で実行復帰・旧キー値(30001)はTTL内残留
- 月次80%: set 479,999→INCR=480,000で **monthly_80pct alert 1回のみ**(日次alert増分0=独立性の証明)
- 月次リセット: 暦月初越えで jev:monthly:202610→202611 切替・INCR=1で復帰・旧日次・旧月次キーは残留
- **上限到達後のpending推移観察(ws-6 G4確認事項8の裏取り)**: 実施終了直後の観察で pending=4→**60秒後に0**。本試験の規模(ペア単位・上限はカウンタ直接セット)では・deny中もEvent処理自体は完了するためpendingは残存しない(ws-6の「30分バケット再評価循環でpendingが残り続ける」は1万件級の母集団と再評価循環が重なる場合の挙動。条件下の差を報告書に記録)

### (d) Queue障害(10 §4.7) — n5

- 毒ペイロード: raw publish→再試行5回→quarantined 1行(failure_reason="unextractable triple..." 保持)
- 参照不在Event: 削除済みIntentへの updated v2→ **processed**(discard_reason=intent_not_found・quarantinedでない)
- UNIQUE重複排除: 同一3点組再投下で match_events 1行・候補 700→700(不変)
- 非波及: 毒投入後の正常ペアが提案まで到達(proposed 228→230)
- **lag alert往復**: worker停止+API経由でpending 1行→仮想時計310秒前進で年齢条件成立→**alert 1回**(api両コンテナ合算・アームキーで二重発報なし)→worker復帰・消化後 **resolve 1回**

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

- ハーネス n5→n4→n3(13試験・シード投入300件込み): **15分49秒**(staging-nfr-reset直後のクリーン環境)
- n2 prep(母体確保): 31秒 / n2本体(3試験): 10分16秒(dump・restore・切替の手順時間は別記のRTO実測)/ n2_30再実行(active限定修正後): 10秒
- dump: 904KB・1秒未満 / restore+切替: RTO実測のとおり(計18秒)
- プローブ窓: §3(a)へ記録(実時間61分強=3600秒窓+終了処理)
- **staging実施で発見・修正した試験コード欠陥(supervisor直接修正・mainコミット)**: ①wait_untilの同期/非同期混在(TypeError・全滅系)②CLI publish version+1直投げはstage1のversion検査でquarantined→API PATCH正規経路へ ③create_user_nfrの409経路の応答形状({"user":…}でない平オブジェクト) ④IdPトークン30日→365日(仮想Clockが月跨ぎで実時間+30日を追い越すため・サーバはexpを仮想Clockで検証) ⑤lag alert resolveログINFO→WARNING(API側の既定ログ構成ではlatch系INFOが出ない) ⑥n4_10候補照会のuuid昇順正規化対応 ⑦n5_50件数の基準値差分化(dockerログ累積) ⑧n2_30のNULL化対象をactiveへ限定(backfillの回収対象はactiveのみ)。いずれもハーネス・試験手順側の欠陁で・**実装本体(lag_alert.pyのログレベルを除く)への変更はゼロ**

## 6. スーパーバイザー検証待ちの記録

- **test-ci: supervisor検証時に実施(期待値1754=1720+新規unit 34)。** 本単位はintegration試験を追加していないが・test-ciはunit試験も収集するため新規unit 34件の分だけ実数が動く(本節の初稿記載「変更なし・実行不要」はsupervisor検証時に誤りと判定して訂正)。ci-dbへの書込み経路は追加していない(ハーネスの接続先はstaging宛15432/16543/16379のみ・UPDATEはn4とn2のembedding NULL化の2箇所)。
- staging環境の実施(起動・ci一時停止・試験実行・ci復帰)は§8の手順でスーパーバイザーが行う。

## 7. G4時確認事項の付記依頼(supervisor側で記録)

1. lag alert発報器はworker内collector(docstringの「gaugeを消費する」)でなくAPI側周期タスクに新設した(同じDB集計値を消費する意と解釈)
2. alert媒体は構造化ログ(latch.cost.alert/latch.pipeline.alert)のまま・alertmanager等への差し替えはPhase 0以降
3. circuit breakerのRedis共有(M2 ws-8引継ぎ)は本単位スコープ外・staging worker2台でbreaker状態はプロセス独立
4. **可用性試験のインスタンス強制停止はdocker kill(SIGKILL)でなくコンテナ内PID 1へのSIGTERM**: docker killは手動停止扱いでrestartポリシーの対象外(Docker仕様・実測15分非復帰)。SIGTERM死=自然死扱いで自動再起動を検証
5. **可用性の試験窓は実効44分(main 44サンプル)で判定**: 実施マシンのスリープ(96分)による窓短縮・全観察点は網羅(2026-10-05ユーザー裁定)
6. **n2のbackfill回収対象はactive Intentのみ**(期限切れ行は再エンベディング対象外・backfill.py対象抽出の実装どおり。01 §18の「記録で特定」の検証はactive母体で実施)
7. **D-16上限到達時のpending残存は本試験規模では発生せず**(実測4→60秒で0。ws-6観察の「pending残存し続ける」は1万件級母集団と30分再評価循環の重畳時の挙動)

## 8. 詰まった箇所と判断

- **ci常設環境の停止による`make test`9件失敗(Task 1の検査時)**: 作業開始直後の`make test`がDB接続拒否(127.0.0.1:5432)でtest_worker.py系9件が落ちた。ci-dbのコンテナが停止していたのが原因で、`make up`(ci常設環境のhealthy待ち)で復帰させたうえで再実行し、全件パスを確認した。staging環境は一度も触れていない。失敗の全件は私の変更と無関係な既存試験で、環境要因と特定できてから次へ進んだ。
- **lint未グリーンのままコミットした2件の訂正**: Task 7とTask 11で、整形・行長の残差を確認する前にコミットした。どちらも直後に`ruff format`/`ruff check --fix`を当て、同じ内容のamendでコミットを置き換えた。最終状態では全11コミットがlint+testグリーンで、ワークツリーはクリーンである。以後のタスクではlint通過を確認してからコミットする順序に徹した。
- **収集確認の副産物**: `pytest --collect-only`でもconftestの`pytest_sessionfinish`が空の証拠YAMLを書き出すため、都度削除した。§8本番実行時のresults/ディレクトリを汚さないための措置である。

それ以外に、推測で仕様を変えた箇所はない。計画書内部の矛盾はすべて「4-2. 欠陥修正」に記録したとおりで、10分以上詰まる事態は発生しなかった。

## 9. スーパーバイザー実施(§8)の経過と運用上の失敗の記録(2026-10-05)

- **ハーネス4回のランで収束**: 1回目11失敗(wait_untilの同期/非同期混在)→2回目11失敗(冪等経路の応答形状・トークン期限)→3回目2失敗(resolveログレベル・候補照会の向き)→4回目**13全合格**。以降の修正は個別試験単位(n2_30・n1)
- **n1試験窓の2回破棄**: ①`docker kill`が自動再起動しないDocker仕様の発見(15分非復帰を観測して破棄・SIGTERM手法を実験確立)②監視バックグラウンドコマンドの30分上限killがプロセスグループごとnohupプローブを道連れにした(1回目の「生存確認」はpgrepパターンの自己マッチによる誤検知だった)。3回目はプローブをsetsidで独立セッション化して完走
- **検証チェーンのパイプ再犯**: 出力を`tail`へ通したため失敗明細を2回失った(2026-10-04の教訓の再犯・以後完全排除)。本報告書の失敗分析は個別再実行で取得し直した
- **実施マシンのスリープ**: n1の試験窓中(約96分)にホストがスリープ。試験窓は実効44分となった(§3(a)の記録どおり・ユーザー裁定で成立扱い)
