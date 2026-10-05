# M4 ws-7(非機能試験・障害系)実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** docs/plans/M4/ws-7-design.md §1.1どおり、非機能試験の残り4領域 — 可用性(10 §4.3)・バックアップ復旧(10 §4.4)・縮退残部(D-16上限/resume再評価/catch-up回収・10 §4.5)・Queue障害(10 §4.7) — をstaging環境で実施する器を作り、証拠(10 §5形式)をdocs/testassets/results/へ残せる状態にする。

**Architecture:** プローブ(loadgen/probe.py)とQueue lag alert発報器(obs/lag_alert.py)はAPI/CLI側の新設でworker本体には触らない。D-16カウンタ操作はnfrtools小CLI(jev:daily/jev:monthlyへのSET/GET/DEL・Clock導出キー)。staging環境への差し込みはoverride 2本(compose.staging.nfr.yaml=lag alert有効化・compose.staging.recovery.yaml=db2復旧試験)とし、compose.staging.yaml自体は1行も変更しない。検証はstaging_tests_nfr/(testpaths外・順序固定ハーネスn5→n4→n3→n2→n1)が担い、プローブ実行だけを`make staging-probe`(手順§8・60分試験窓)が担う。追加依存はゼロ。

**Tech Stack:** Python 3.13・FastAPI・SQLAlchemy(asyncpg)+asyncpg(既存)・redis/fakeredis(既存)・httpx(既存)・pyyaml(既存)・pytest(既存)。Docker Compose override・pg_dump/pg_restore(pg16同梱)。

**Spec:** docs/plans/M4/ws-7-design.md(agent1・コミット9945ffd)。本計画はdesign §5のsupervisor承認9件とレビュー訂正3件(§1.3日次TTL=48時間・§2.4(a)相手Intent別ユーザー配置・§2.5 lag alert単発化=RedisアームキーSET NX+TTL)を確定事項として落とし込む(design §6「未解決の論点なし」)。designに対する本計画の読み替えは§2.1に一覧する(計画裁量の決定9件・いずれも報告書への記録義務つき)。

## Global Constraints

- **Clock契約(C2)**: `core/clock.py`は1行も変更しない。新規コードの時刻参照はすべてClock経由。`time.time`/`time.monotonic`/`time.sleep`/`datetime.now`はsrc/latch配下で禁止(test_arch_no_direct_time.py)。**新規probe.py・lag_alert.py・d16.py・nfrtools/__main__.pyも対象**。待機のasyncio.sleepは時刻参照ではない(stub.py・collector.pyと同一規律)。**年齢・成功率集計・キー日付のすべてはClock差分/JST日付で計算する**。
- **依存追加ゼロ**: pyprojectのdependencies・dependency-groupsは1つも増やさない(uv.lock更新なし・追加したら設計逸脱として報告)。pyprojectへの追記は`[project.scripts]`のみ。
- **既定環境資産の無変更**: compose.staging.yaml・compose.load.yaml・compose.yaml(ci)・compose.dashboard.yaml・docker/nginx/staging.conf・docker/prometheus/prometheus.yml・dashboard JSONは無変更。stagingへの差し込みはoverrideファイル(compose.staging.nfr.yaml/compose.staging.recovery.yaml)のみ。
- **worker本体ロジック無変更**: catch-up(reeval.py)・quarantine(stage1.py)・D-16(guard.py/store.py)・backfill.py・Jev・embeddingは1行も変更しない。試験で欠陥を発見した場合は実装を止めて報告ファイルに記録する(ws-6 report §9.1と同型の実装時確認事項)。
- **既存staging_tests/・staging_tests_obs/・tests/配下の既存試験はbasenameを変えない**(追随変更はtest_settings.pyへの3関数追加のみ・既存assertは1行も触らない)。
- **ハーネスはtestpaths外**: staging_tests_nfr/はpyproject `testpaths=["tests"]`の外。`make test`/`make test-ci`からは収集されない。実行は`make staging-nfr-test`のみ。
- **agent3はintegration実行禁止・unit(collect-only含む)までの規律**(スーパーバイザー補足・ws-3/ws-6どおり): `make test`とcollect-only確認のみ。staging環境の実施・ci一時停止・ci復帰はスーパーバイザー検証時(§8)。**`make down`・staging環境の起動停止をagent3は実行しない**。
- **毎コミットの検査**: `make lint && make test`グリーン・basename一意コマンド空・`git status --short`で差分が§4の一覧どおり。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。Convention Commits形式・末尾に `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- **固定値の遵守**: §2.1の読み替え・§2.2〜§2.6の確定値(試験窓60分・閾値1000/300秒・TTL等)は固定値。変更する場合は報告ファイルに「変更前→変更後+理由」を記録する。黙って変えない。
- **検証チェーンにパイプを書かない**: 実施手順(§8)の各コマンドと、ハーネス内の検査コードは単一コマンドで結果を出し、`| tail`等で終了コードを隠さない。件数は実査する(2026-10-04に2回発生した失敗隠蔽の教訓)。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## Review Focus

specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。最も噛みつきやすい順。各項は所有タスクの試験でピン留めする。

1. **lag alertの二重発報**: api1/api2の2インスタンスでlifespanタスクが並走するため、Redisアームキーが効かないと同一滞留でalertが2回出て「条件成立中1回だけ」の規律が崩れ、証拠の信頼性を失う。→ Task 2の`test_redis_arm_key_prevents_cross_instance_duplicate`(同一fakeredisを2インスタンスで共有して発報合計1を検証)+staging実施時はworker1/worker2両ログの合算件数実査(test_n5_50)。
2. **プローブの計画停止タグ漏れ**: 意図的停止窓(api2のstop→start)のサンプルが分母に残ると成功率が過小に出て99.5%判定を誤る。→ Task 4の`test_summarize_excludes_tagged_from_denominator`+`test_marker_presence_tags_sample`。staging実施時はmain側サンプルがマーカー窓で除外された件数をYAMLで実査(test_n1_10)。
3. **D-16キー導出のClockずれ**: キー日付がJSTでない・prefixが付かない等のずれがあると、setが別キーに当たって境界試験が静かに無効化される(workerは正しいキーのカウンタで通常実行を続ける)。→ Task 5の`test_daily_key_derived_from_clock`・`test_monthly_key_derived_from_clock`(FakeClockのJST日付で期待キー文字列をピン)+staging実施時は`d16 get`でset値の読み戻し実査(test_n3_10)。
4. **アクセストークン失効**: 試験窓60分とaccess token TTL 1時間が一致するため、最後のプローブが401で失敗し、成功率がAPIの状態ではなく認証で落ちる。→ Task 4の`test_token_cache_reissues_after_50min`(FakeClockで50分経過時の再発行を検証)。
5. **worker停止の戻し忘れ**: staging試験はworker停止を伴う。finallyなしでテストが失敗するとworkerが止まったまま後続試験が全滅する。→ Task 7のworkers_stop/workers_startヘルパーを「必ずtry/finallyで対にする」規律としてtest_n4_10・test_n4_20・test_n5_50に明記し、test_n4_20の冒頭の収束待ち(wait_pipeline_settled・helpers_nfr)がworker停止を名前つきタイムアウト失敗として検出する。

---

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m4-ws-7`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。
- **worktreeの基点はmain(head=e02446d)**: ws-6がマージ済み・**alembic head=0008・`make test` 1438 passed・`make test-ci` 1720件のmain**から作る。**本単位はマイグレーションを追加しない**(design §1.2・EXPECTED_HEAD="0008"は不変)。worktree作成後、まず `make setup`(`cd backend && uv sync`)で `.venv` を構築する。Task 5でpyprojectの`[project.scripts]`追記後に**もう一度 `uv sync` を実行する**(スクリプト生成のため。依存は増えない)。
- **毎コミットの検査**: worktree内で**毎コミット `make lint` と `make test` がグリーン**であることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **共有ci-dbへのtest-ci/migrate実行はスーパーバイザー検証時のみ(STATUS.md運用ルール1)**: 本単位はマイグレーションを追加せず・unit試験はDB不使用のためci-dbへの書込み経路はない。**agent3は `make test-ci` も実行しない**(本単位の新規integration試験はゼロで・test-ciの実数は既存1720件のまま)。**報告書には「test-ci: 変更なし(実行不要)」を記録する**(必須記録)。並走単位は本計画時点で存在しない。
- **staging環境の実施はスーパーバイザー検証時**(実環境試験=証拠が本体・design §4.2/§4.3): agent3はハーネス・CLI・override・Makefileまでを作り、**staging環境の起動・ci一時停止・試験実行・ci復帰はしない**。実施は§8の手順でスーパーバイザーが行う。**`make down`・`make staging-*`(起動系)・docker kill/stopをagent3は実行しない**。
- **python / pipコマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **Clock契約・実時間参照の規律**: Global Constraintsのとおり。ハーネス(staging_tests_nfr/)の実時間待ち上限(wait_untilのtimeout・300秒級の観察待ち)は制限外(staging_tests/helpers_wait.py `monotonic`と同一作法)。**ハーネス内のClock操作(set/advance)は前進のみ**(ws-3時間旅路と同一規律)。
- **ハーネスの証拠YAML**: conftestの`pytest_sessionfinish`のみが出力する(実行ごとにdocs/testassets/results/ws7-nfr-result-<ts>.yaml・10 §5形式)。部分実行でもその部分のitemsを出す。
- **コミット前の全タスク共通手順**(各Taskには明記しない): `make lint && make test`グリーン・`find backend/tests backend/staging_tests backend/staging_tests_obs backend/staging_tests_nfr -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空・`git status --short` で差分確認。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| 可用性: 計画停止(デプロイ)を除外タグ付きで区別し、APIリクエスト成功率を合成監視プローブ(1分間隔)で測定。稼働中のAPIコンテナを1インスタンス強制停止し、残存インスタンスでの処理継続と自動復旧を確認。合格=成功率99.5%以上。試験期間の値はdocs未規定 | 10 §4.3・01 §20 |
| バックアップ復旧: 定期バックアップから新しいDBインスタンスへ復元し、Embeddingモデル識別子の記録(01 §18)を使ったVector再構築(再エンベディング)を経てサービス再開。復旧後、Intent検索・マッチング・通知が正常、cross-check=復旧前後のactive Intent数一致。RPO・RTOは目標値なし・実測記録。バックアップの取得方式・周期はdocs未規定 | 10 §4.4 |
| D-16: 日次30,000回・月次600,000回・80%(24,000/480,000)到達でalert(Event源流上位ユーザーのJev消費レポート添付)・上限到達は「Jev実行をスキップして候補を『Jev未判定』として保留」・0時(JST)リセットで復帰・月次上限は暦月初JST 0時リセットで日次と独立。alertに源流レポート添付 | 01 §20・04 §5 D-16 |
| カウンタはJev Workerではなく実行要求側(Matching Worker)で増やす | 04 §5 |
| resume再評価: 一方をpause→対象開始時刻のBucketをClockで経過→resumeでversion+1の再評価Event(05 §6)→Embeddingの再実行なくCandidate Retrievalから再評価・提案が発生し得る状態に復帰 | 10 §4.5(a)・01 §16 |
| catch-up回収: Bucket到来Eventのスケジューラ停止で欠落を作り、期限前catch-upスキャン(60秒周期・Event発行せずDB直接投入)が回収。expires_atまで2時間を切った未処理のIntentが、直近の詳細評価から30分経過後に再評価される。embedding済みIntentが対象に含まれ・embedding IS NULLのIntentが除外される | 10 §4.5(b)・06 §9 |
| Queue障害: 毒ペイロード→retry上限5回→quarantined・滞留が後続イベントに波及しない。削除済みIntent参照Eventはprocessedで破棄。UNIQUE制約による同一Event重複排除。Queue滞留を作りQueue lagメトリックのalert発報を確認。alert閾値の値はdocs未規定 | 10 §4.7 |
| 処理失敗: 5回まで再試行→失敗理由を保持して隔離(match_events.status=quarantined)。参照先Intent不在(削除済み)=破棄しstatus=processedで閉じる・payload不正=5回再試行後に失敗理由保持でquarantined | 06 §9 |
| JevコストカウンタのRedisキー: 日次`jev:daily:{yyyymmdd}` TTL 48時間・月次`jev:monthly:{yyyymm}` TTL 45日。バケット文字列はClock導出。TTLは掃除用・リセット表現は日付キー切替 | worker/cost/store.py docstring(実装・設計§1.3) |
| D-16 alert発報: INCR戻り値が丁度しきい値(24,000/480,000)に等しいとき1回だけ。媒体=構造化ログ(latch.cost.alert)。deny条件は`daily_count > DAILY_LIMIT`(30,000ちょうどは許可) | worker/cost/guard.py(実装・設計§1.3) |
| resumeはembeddingをクリアしない(_UPDATE_STATUSはembedding非触り)・EmbeddingWorkerは既存embedding行を再エンベディングせずembedding_completedを再発行 | 06 §9・worker/embedding.py(実装) |
| 証拠: 試験ID・実施日・環境・入力条件・合否をdocs/testassets/results/へ保管。非機能試験はリリース前に1回 | 10 §5 |
| 実施はws-6と環境共有のため直列(staging-reset開始・測定中はci一時停止=運用ルール6) | design §1.1・STATUS運用ルール6 |
| staging環境: api1+api2+nginx(127.0.0.1:18000)・restart: unless-stopped(自動復旧の器)・`LATCH_CLOCK_MODE: controlled`・nginxのproxy_next_upstream再送(ws-7がdocker killで検証する旨コメント済み) | compose.staging.yaml・docker/nginx/staging.conf(ws-1) |

## 2. 設計確定値と計画時の機械検証

### 2.1 読み替え済み確定値(designからの差分・計画裁量の決定)

| # | designの記載 | 本計画の値 | 根拠・記録義務 |
|---|---|---|---|
| 1 | §2.3「29,999へセット→実Event 1件(INCR=30,000)→Jevスキップ」 | **30,000へセット→実Event 1件(INCR=30,001)→deny・Jevスキップ**。guard.py L126は`daily_count > DAILY_LIMIT`で判定し、30,000ちょうどは許可(01 §20「日次上限(30,000回)を超えると」の字義と一致)。alert境界は23,999へセット→INCR=24,000(=DAILY_ALERT・丁度等値で1回発報)で実現 | 実コード突合(guard.py L102・L125-128)。**報告書に記録する** |
| 2 | §2.1「`--planned-outage`実行(または実行中のタグ記入)で計画停止サンプルを除外対象としてマーク」 | **実行中のマーカーファイル方式に一本化**。probeは`--outage-marker <path>`(既定`<repo>/.staging/probe-outage.marker`・ファイル位置基準でcwd非依存)を指定し、**各サンプル記録時にマーカーの存在を確認してtagged_planned_outage=True**。api2のstop前に`touch`・start後に`rm`する(§8手順6)。起動時に窓が不明な長時間実行のため事前窓指定方式は使えない | 実行中タグ記入の実直な実装。**報告書に記録する** |
| 3 | §3.1「compose.staging.recovery.yaml 新設: 復旧試験用のDB向き先差し替え」 | **overrideは2本に分ける**: `compose.staging.nfr.yaml`(api1/api2へ`LATCH_ALERT_QUEUE_ENABLED: "true"`・n1〜n5全般で使用)と`compose.staging.recovery.yaml`(db2定義+api/workerのLATCH_DATABASE_URL差し替え+backfill周期短縮・n2専用)。lag_alertは**既定無効(settings.alert_queue_enabled=False)**のため、stagingで有効化する器がdesign §3.1のままだと存在しない。§3.2「compose.staging.yamlを触らない」の規律を守る正攻法 | design §2.5案A(lifespan新設タスク)の必然の帰結。**報告書に記録する** |
| 4 | §3.1「pyproject.toml 追記: nfrtoolsのエントリ(latch.nfrtools CLI)」 | `[project.scripts]`へ`latch-nfrtools = "latch.nfrtools.__main__:main"`を追加し、Makefileのstaging-d16は`uv run latch-nfrtools $(ARGS)`で呼ぶ(uv sync再実行でスクリプト生成)。あわせて既存慣行どおり`python -m latch.nfrtools`でも同じmainが動く | design §3.1字義どおり+既存`python -m`慣行の併存(clock_cli・loadgenと同型)。**報告書に記録する** |
| 5 | §2.3「実Event 1件投入(INCR=…)」 | **実Event=Embedding済みペアの一方へ`publish updated --version <現行+1>`**(staging-publish既存経路)。1ペアの新規作成はJev 1回(jev_result IS NULLガードで起点重複評価を排除・jev.py実突合)だが、**updated投下の前に`staging-clock advance 1900`(reevalガード30分窓の脱出)**が必要(main.py `_run_matching`のguard.allow・SET NX EX 1800)。ペアは1組をupdated連用(v2〜v6)で使い回す(1ユーザー作成20件/日・Jev予算1Intent 40回/日を節約)。updated種はdebounce 10秒窓を経由するため収束待ちを挟む | reevalガード・debounceの実装突合(worker/main.py L437・stage1.py intake)。**報告書に記録する** |
| 6 | §2.2手順(ダンプ→db2復元→切替)にworkerの扱いなし | **ダンプ直前にworker1/2をdocker compose stop**(15432側の書込みを静止=active数cross-checkの「前」値が機械的に確定する)。db2-switchでapi1/api2/worker1/worker2を一括再作成(=切替)。n2後に既定composeで再作成して戻す | cross-checkの機械性担保。静止しないとdump後〜切替前の再評価循環で「前」値が動く。**報告書に記録する** |
| 7 | §2.4(b)「`staging-clock advance`で30分Bucket時刻を通過させる」 | **テスト内でDB照合から必要なadvance幅を計算**する。対象Intentのtime_startを仮想時刻+6日(7日上限内)に置きexpires_atを≈+72時間候補にし、advance=(expires_at残秒−6000)秒で「expires_atまで2時間を切る」状態を機械的に作る(仮想時刻の実施時刻依存を排除)。reevalガード30分窓もこの前進で自然脱出 | 仮想時刻は実施時刻から導出されるため固定advance幅では対象条件の成立が保証されない(FR-13既定則は実施時刻に依存)。**報告書に記録する** |
| 8 | §4.3手順1「staging-reset→シード投入(n1用プローブ対象を含む)」 | シード投入は**test_n5_00の冒頭でべき等ヘルパー`ensure_seed`**が担う(users件数>0ならスキップ・無ければws-2生成のdocs/testassets/m4-seed.yaml(300件)をhelpers_seed型でAPI投入)。プローブユーザー(load-u0001)は§8手順2で`loadgen users --count 1`をstaging宛に実行。部分実行(NFR_FILES)でも再投入・二重投入が起きない | ハーネスは部分実行が前提(make staging-nfr-test NFR_FILES=…)で・プロセス単位のキャッシュは機能しないため。**報告書に記録する** |
| 9 | §2.4(b)「catch-upが回収する…ことをDB/ログで観察」 | catch-upは**Eventを発行しない**ため、観察痕跡は2つに確定: ①**reevalガードキー**`reeval:{intent_id}`(SET NX EX 1800)の立ちは worker再開前に明示DELしたうえで、再開後のget結果で「embedding済み対象は立つ・NULL除外対象は立たない」を照合 ②**embedding列の状態照合**(対象はembedding非NULLのまま・除外対象はbackfill回収までNULLのまま) | catch-upの実装突合(reeval.py `_SELECT_CATCHUP_TARGETS`の`embedding IS NOT NULL`条件・worker/main.py `_run_direct_pipeline`のEvent無し投入)。**報告書に記録する** |
| 10 | §2.5「滞留はworker1/2停止+Event連続投入で作る(投入は既存手段)」 | **滞留の作り方=worker停止中のAPI作成(created)**に読み替え。match_events行(status=pending)のINSERTは(a)API CRUDのoutbox(intents/events.py・tx内INSERT)か(b)worker受信時のstage1._claimのどちらかであり・CLI publish(events __main__)はPub/Subへ送るのみでworker停止中は行を作らない。「Event投入」をAPI作成へ読み替える。pending行の年齢はlag_alertのClock差分(=APIのControlledClock)で計算されるため・staging clock advanceで年齢条件を仮想時計で成立させる(fallback relayがClock前進でpending行を再publishし続けるが・worker再開後の3点組重複(UNIQUE・duplicate=ack)で冪等=無害) | events `__main__`の実突合(publishは`publish_raw`/`publish_match_event`でPub/Sub送信のみ)。**報告書に記録する**(supervisor計画レビューF1・2026-10-05) |
| 11 | §2.3「D-16カウンタの直接セット+実行1〜2回で境界を通す」の母体 | **n3の各試験は毎回freshペア**を作る(updated連用のキャッシュペアをやめる)。n3の月跨ぎClockジャンプ(翌日0:05・月初0:05)でsweeperがexpires_at超過のIntentをexpired化し・expiredへのpublish updatedはLayer 1のstatus='active'条件で候補を作らずJevが走らないため・キャッシュペアでは3_30以降の観察が決定的に失敗する。リセット後の試験(3_30/3_50)はfreshペア作成分のINCRを`delete_counter`で掃除してからpublishし・「INCR==1」を厳密に検証する | FR-13既定則(expires≈作成+数時間)・sweeperの実突合・Layer 1 active条件。**報告書に記録する**(supervisor計画レビューF2・2026-10-05) |
| 12 | §4.2実行順序(n2はn3の後)は維持 | **dump直前の母体作成をtest_n2_prep.pyへ分離**(手順5-0)。n3の月跨ぎで全既存Intentがexpired化済みのため・そのままのactive数は0になりcross-checkが0==0で自明化する。prepはfreshペア2組を作りactive母体(≥4件)を確保してからworker停止→dumpへ進む | 実行順序のdesign字義は維持(順序変更はしない)。**報告書に記録する**(supervisor計画レビューF3・2026-10-05) |

### 2.2 機械チェックSelf-Reviewの証拠(計画レビュー時点・スーパーバイザー補記2対応)

**(a) テストファイルbasename一意(計画時点の機械確認・2026-10-05)**

本計画の実行結果(現在のmain・コマンド `find backend/tests backend/staging_tests backend/staging_tests_obs backend/staging_tests_nfr -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d`):

```text
(出力なし = 衝突ゼロ)
```

新規unit 4ファイル(test_obs_lag_alert・test_loadgen_probe・test_nfrtools_d16・test_nfr_pins)と新規ハーネス6ファイル(test_n1_availability〜test_n5_queue・test_n2_prep)は、既存の`backend/tests`(unit/integration全体)・`backend/staging_tests`・`backend/staging_tests_obs`全ファイルbasenameとの突合で衝突ゼロを確認済み(既存にtest_obs_lag_alert*・test_loadgen_probe*・test_nfrtools*・test_nfr_pins*・test_n*は存在しない。既存の類似名test_obs_collector.py・test_loadgen_collect.pyとは別名)。**既存ファイルへの追随変更**(test_settings.py)はbasenameを変えない。コミット前には次コマンドが空であることを毎回再確認する:

```bash
find backend/tests backend/staging_tests backend/staging_tests_obs backend/staging_tests_nfr -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
# 期待値: 出力なし(空)
```

**(b) ピン試験への影響事前特定(洗い出し表)**

| 既存ピン | 内容 | 本単位の影響 | 追随访問 |
|---|---|---|---|
| tests/unit/test_settings.py `test_obs_settings_are_exactly_three_fields` | obs_・kpi_接頭辞の完全一致集合ピン | **無影響**(新envはalert_接頭辞でobs_・kpi_集合に含まれない・実突合)。test_settings.pyへの追随は**追加のみ**(`test_alert_settings_defaults`・`test_alert_settings_are_exactly_five_fields`・`test_alert_settings_env_override`の3関数)・既存assertは1行も触らない | Task 1で追加 |
| tests/unit/cost/test_cost_guard.py `test_limit_constants_pin_docs_values` | D-16上限定数の値ピン | **無影響**(D-16上限定数は変更しない)。staging試験(n3)は既定数値での実経路確認のみ | なし(無傷確認のみ) |
| tests/unit/test_app_health.py ほか create_app系unit | ASGITransport経由のunit(`tests/conftest.py`実突合) | **無影響** — ASGITransportはlifespanプロトコルを発火させないため、main.pyのlifespan追記は既存unit試験に到達しない(tests配下にlifespan発火試験ゼロ・`rg "lifespan_context" backend/tests`で確認済み)。ci環境ではalert_queue_enabled既定Falseのためタスクは起動しない | なし |
| tests/unit/test_loadgen_compose_pins.py `test_makefile_has_load_targets` ほか | Makefileターゲット存在検査・load compose・dashboard・prometheus.ymlの静的ピン | **無影響**(Makefileへの追記は存在検査と非衝突・load系composeとdashboard/prometheusは無変更)。無傷確認のみ | なし(Task 6で無傷確認) |
| tests/integration/test_compose_ports.py | compose全系のports形状ピン(127.0.0.1明示バインド) | **無影響**(compose.staging.yamlは無変更・新overrideのdb2は127.0.0.1:16543明示・本ピンの走査対象はstaging/load/dashboard composeのみでrecovery/nfr overrideは対象外)。**実施確認**: test_compose_ports.pyが新overrideを走査しないことをTask 6でcollect-only後に無傷実行で確認 | なし |
| tests/unit/test_arch_no_direct_time.py | src/latch配下の実時間参照禁止 | 新規4モジュール(loadgen/probe.py・obs/lag_alert.py・nfrtools/d16.py・nfrtools/__main__.py)は**Clock経由のみ**で実装。実時間表示が必要なnfrtools CLIの表示はSystemClock経由(clock_cli.pyと同一作法)。asyncio.sleepは待機であり禁止対象外(collector.py既存と同一規律) | なし(Task 2/4/5のコード規律) |
| tests/unit/test_log_allowlist.py `test_registered_log_types_have_no_forbidden_fields` | 構造化ログ許可リスト(`ALLOWED_STRUCTURED_LOGS`)の完全一致集合ピン(`== {"latch.llm.send", "latch.push.send"}`) | **無影響** — `latch.pipeline.alert`ロガーはlogallow登録表に**登録しない**。logallowの規律(08 §2.4)はLLM送信・push送信の個人データ記録の落下防止であり、既存の`latch.cost.alert`(D-16・guard.py)も登録表外(実突合)であるためalert系は規律の対象外。個人不含の数値集計ログという同型 | なし |
| tests/unit/test_events_cli.py・test_events_publish_cli.py | events CLIのピン | **無影響**(events CLIは1行も変更しない・`--raw-payload`は既存) | なし |
| tests/unit/test_obs_collector.py | MetricsCollectorの契約(例外握り・stop即応) | **無影響**(collector.pyは無変更)。新設のlag_alert.run()は**collector.runと同一契約**で実装する(起動時即check→interval待ち・例外握ってwarning+次tick・stop即応待機) | なし(Task 2で契約を踏襲) |
| tests/unit/test_loadgen_schedule.py ほかloadgen系 | loadgen既存サブコマンド(users/perf/massload/bruteforce) | **無影響**(`__main__.py`への追記は既存サブコマンド定義の後にprobeを追加するのみ)。無傷確認のみ | なし(Task 4で無傷確認) |
| staging_tests/・staging_tests_obs/ | ws-3/ws-4ハーネス | **無変更**(型だけ踏襲。nfrハーネスはstaging_testsのhelpersをimportせず・独立完結するためobs側のsys.pathハックも不要) | なし |

**追随访問(本計画が追加する新ピン)**: tests/unit/test_nfr_pins.py が main.pyのlifespan登録コードの静的ピン・pyproject `[project.scripts]`・compose.staging.nfr.yamlのalert有効化・compose.staging.recovery.yamlのdb2定義(16543・container_name・profile)/DB向き先/backfill周期短縮・compose.staging.yamlの無傷(既定環境に`LATCH_ALERT_QUEUE_ENABLED`が漏れないこと)・Makefile新規ターゲット9本の存在を機械検査する(§Task 3/5/6)。

**(c) DB・環境干渉の対抗策(マイグレーションなし・staging専用・ci無干渉)**

- **本単位はマイグレーションを追加しない**(design冒頭前提)。alembic head=0008・既存headピン(staging_tests/conftest.py EXPECTED_HEAD="0008"と同型をnfrハーネスにも置く)への追随変更は**なし**。
- **unit試験はDB不使用**(FakeClock・fakeredis・モックのみ)。`make test`(unit)は毎コミットで回す。ci-dbには書かない。
- **staging環境はci環境と完全分離**(latch-stagingプロジェクト・15432/16379/18085・ciは5432/6379/8085)。ハーネスの接続先はstaging宛定数のみ(DSN=15432・RECOVERY_DSN=16543・REDIS_URL=16379)。ハーネスのUPDATEは2箇所のみ: n2のembedding NULL化(16543宛・db2)とn4のembedding NULL化(15432宛・対象1件を絞り込み)。いずれもstaging専用DBで・ci-dbには一切触らない。
- **db2は独立ボリューム・別ポート**(`latch-staging-db2`・127.0.0.1:16543)。既定のlatch-staging-dbボリュームはn2でも一度も触らない(dumpは読取のみ)。
- **n2のDB向き先切替はcompose overrideのみ**: 既定composeの立ち上げ・migrate・geo-importは手順の最初(staging-nfr-reset)で完結し、n2の切替は`-f ... -f compose.staging.nfr.yaml -f compose.staging.recovery.yaml up -d --force-recreate`の再作成のみ。既定composeファイル・イメージ・ボリュームは不変。n2終了後に既定構成へ再作成して戻す(§8手順5末尾・戻し確認のpsql実査つき)。
- **ハーネスのworker停止は必ずtry/finallyで戻す**(Review Focus 5)。テスト失敗時もworkerが停止したまま残らない。
- **staging試験はci一時停止中(運用ルール6)に実施**(§8手順3)・試験後は`make up`でci復帰(必須)。

### 2.3 試験パラメータの確定値(design §5承認事項の落とし込み)

| 項目 | 値 | 根拠 |
|---|---|---|
| 可用性の試験窓 | 60分・プローブ1分間隔(main)・failover観察用10秒間隔(dense・合否対象外) | design §5-1・10 §4.3字義 |
| 成功率の定義 | 成功プローブ数÷総プローブ数(main側・マーカータグ付きサンプルは分母外)・合格=99.5%以上の機械判定 | design §5-2・01 §20 |
| プローブ対象 | GET /health(認証外)+GET /v1/users/me(認証付き・load-u0001) | design §2.1案A |
| プローブタイムアウト | 10秒 | design §2.1(nginx再送が収まる値) |
| トークン再発行 | 発行から50分経過で再発行(access token TTL 1時間>窓60分対策) | Review Focus 4 |
| D-16 alert境界 | 日次: set 23,999→実Event→INCR=24,000・月次: set 479,999→実Event→INCR=480,000 | guard.py DAILY_ALERT/MONTHLY_ALERT実突合 |
| D-16上限到達 | set 30,000→実Event→INCR=30,001>30,000→deny(global_daily) | §2.1読み替え1 |
| lag alert発報条件 | pending件数≧1,000 **または** 最古pending年齢≧300秒(OR・env設定可・既定値はstaging composeで変更しない) | design §5-5 |
| lag alert単発化 | プロセス内フラグ+Redisアームキー`latch:alert:queue:arm`(SET NX EX 180秒)・解除=resolveログ+DEL | supervisor訂正3・2026-10-05 |
| 復旧試験のDB | db2(新規ボリュームlatch-staging-db2・127.0.0.1:16543・container_name latch-staging-db2-1) | design §2.2案A |
| 復旧後の再エンベディング | 復元DB上で`embedding IS NOT NULL AND embedding_model IS NOT NULL`の行をUPDATE NULL化→backfillで回収(worker周期・recovery overrideでinterval 5秒に短縮) | design §2.2案A・01 §18・06 §9 |
| n2のbackfill周期 | `LATCH_EMBEDDING_BACKFILL_INTERVAL_SEC: "5"`(既存env・既定300のoverride上書きのみ) | 数百件を数十秒で回収するため(§2.1読み替え3) |
| staging実行順序 | n5→n4→n3→(n2 prep)→n2→n1(固定。MakefileのNFR_FILES既定がこの順・n2 prepはdump直前の母体作成) | design §4.2・§4.3・§2.1読み替え12 |

## 3. Review Focus

見出し直下に記載(§「Review Focus」)。5項目それぞれに所有タスクの試験を割り当て済み: 1→Task 2・2→Task 4・3→Task 5・4→Task 4・5→Task 7(各ハーネス試験のfinally規律)。

## 4. スコープ(作成・変更するファイル一覧)

### 4.1 作るもの(新規)

| ファイル | 内容 | Task |
|---|---|---|
| `backend/src/latch/obs/lag_alert.py` | Queue lag alert発報器(DB直SELECT・閾値判定・アームキー単発化・resolve) | 2 |
| `backend/tests/unit/test_obs_lag_alert.py` | 発報1回・解除resolve+再武装・クロスインスタンス単発化・Clock年齢・Redis故障fail-open(8件) | 2 |
| `backend/tests/unit/test_nfr_pins.py` | main.py wiring静的ピン・pyproject・override・Makefile・既定環境無傷(7件・Task 3/5/6で増分追加) | 3,5,6 |
| `backend/src/latch/loadgen/probe.py` | 合成監視プローブ(main/dense集計・マーカータグ・TokenCache・YAML) | 4 |
| `backend/tests/unit/test_loadgen_probe.py` | 成功率集計・タグ分母除外・連続失敗・タイムアウト・token再発行・run_probe決定性(8件) | 4 |
| `backend/src/latch/nfrtools/__init__.py` | 空パッケージ | 5 |
| `backend/src/latch/nfrtools/d16.py` | D-16カウンタ操作(Clock導出キー・set/get/delete) | 5 |
| `backend/src/latch/nfrtools/__main__.py` | CLI: `latch-nfrtools d16 {get,set,delete} {daily,monthly} [value]` | 5 |
| `backend/tests/unit/test_nfrtools_d16.py` | キー導出・上書きTTL・削除・CLI配線(6件) | 5 |
| `compose.staging.nfr.yaml` | 試験全般override(api1/api2へlag alert有効化) | 6 |
| `compose.staging.recovery.yaml` | n2専用override(db2定義・DB向き先・backfill周期) | 6 |
| `backend/staging_tests_nfr/conftest.py` | ハーネス土台(証拠YAML・fixtures・Clock・compose/ログヘルパー) | 7 |
| `backend/staging_tests_nfr/helpers_nfr.py` | 共通ヘルパ(べき等シード・ペア作成・収束待ち・publish) | 7 |
| `backend/staging_tests_nfr/test_n5_queue.py` | Queue障害(毒・参照不在・重複・非波及・lag alert往復) | 7 |
| `backend/staging_tests_nfr/test_n4_reeval.py` | resume再評価・catch-up回収とNULL除外 | 8 |
| `backend/staging_tests_nfr/test_n3_d16.py` | D-16(alert・上限・日次リセット・月次独立性) | 9 |
| `backend/staging_tests_nfr/test_n2_prep.py` | dump直前のactive母体確保(freshペア2組・§2.1読み替え12) | 10 |
| `backend/staging_tests_nfr/test_n2_backup.py` | cross-check・識別子NULL化→backfill・スモーク | 10 |
| `backend/staging_tests_nfr/test_n1_availability.py` | プローブYAML検証(成功率・タグ除外) | 11 |
| `docs/plans/M4/ws-7-report.md` | 報告書(§7の形式・実施後に作成) | 11 |

### 4.2 変更するもの(既存・追従)

| ファイル | 変更内容 | Task |
|---|---|---|
| `backend/src/latch/settings.py` | alert_系設定5本追記(alert_queue_enabled/interval_sec/pending/oldest_seconds/arm_ttl_sec・既定False/60/1000/300/180) | 1 |
| `backend/tests/unit/test_settings.py` | alert_系の既定値・集合ピン・env上書きの3関数を追加(既存assert無変更) | 1 |
| `backend/src/latch/main.py` | lifespanへlag alertタスク登録(enabled時のみ・独立Redis接続・finallyで停止) | 3 |
| `backend/src/latch/loadgen/__main__.py` | probeサブコマンド+cmd_probe追加 | 4 |
| `backend/pyproject.toml` | `[project.scripts]`へlatch-nfrtools追加(依存は無変更) | 5 |
| `Makefile` | staging-nfr-reset・staging-nfr-test・staging-probe・staging-d16・staging-dump・staging-db2-up・staging-restore・staging-db2-switch・staging-db2-downの9ターゲット+.PHONY+NFR_FILES定数 | 6 |

### 4.3 触らないもの(禁止・スコープ外と判断する基準)

- **compose.staging.yaml・compose.load.yaml・compose.yaml(ci)・compose.dashboard.yaml・docker/nginx/staging.conf・docker/prometheus/prometheus.yml・dashboard JSON**: 既定環境の既定値を変えない。lag alert有効化とDB向き先差し替えはoverrideで完結する(§2.1読み替え3)。
- **worker本体の処理ロジック**(stage1・embedding・jev・latch_engine・group_engine・reeval・sweeper・reset・retention・backfill・cost/guard・cost/store): 本単位で改修する仕様上の欠陥は想定していない。試験で欠陥が見つかった場合は実装を止め・報告ファイルに「実装時確認事項」として持ち帰る。
- **既存メトリクス(obs/metrics.py)・collector.py・kpi.py**: lag alert判定はDB直SELECTで足り・新gaugeは作らない(design §3.2)。collector.docstringの「このgaugeを消費する」は「同じDB集計値を消費する」の意と読む(G4時確認事項候補・§7)。
- **既存staging_tests/・staging_tests_obs/・tests/配下の既存試験の既存assert**: 変更しない。追随はtest_settings.pyへの追加のみ。
- **events CLI・auth・ratelimit・intents本体**: publish `--raw-payload`は既存機能・変更不要。
- **DBスキーマ**(マイグレーションなし)・frontend・prototype・docs本文(本単位でdocs改版なし)。
- **スコープ外と判断する基準**: ①circuit breakerのRedis共有(M2 ws-8引継ぎ・design §5-6で本単位スコープ外確定。staging worker2台でbreaker状態がプロセス独立である点はG4時確認事項として記録) ②メトリクスbucket上限10秒の拡張(ws-6 G4確認事項7・design §5-9で本単位では実施せず・D-16縮退時のpending推移観察で代替) ③alertの媒体差し替え(alertmanager等・04 §5の「媒体は運用設計に委ねる」の範囲・Phase 0以降) ④本番バックアップ運用の自動化(pg_dump1回=「定期バックアップ1サイクル」の読み替え・本番側はws-8ロールバック手順とPhase 0) ⑤試験窓の絶対値合否の最終判定(機械判定99.5%を出すが・正式合否はG4の人間側)。

## 5. タスク(実装ステップ・TDD)

タスク実行の共通手順(各Taskに明記しない): 作業はworktree内・コミット前に `make lint && make test` グリーン・basename一意コマンド空・`git status --short` で差分確認。pytestの実行は `cd backend && uv run pytest tests/unit/<path>.py -v` の形。新規unit試験の総数は**33件**(内訳: Task 1=3・Task 2=8・Task 3=1・Task 4=8・Task 5=8・Task 6=5)。**増減後の期待値は1438+33=1471件**(計画時点。実数を報告書に記録する)。

### Task 1: settingsへalert_系env 5本追加(unitピン先行・Red→Green)

**Files:**
- Modify: `backend/src/latch/settings.py`
- Test: `backend/tests/unit/test_settings.py`(追随追加のみ)

**Interfaces:**
- Consumes: 既存Settings(env_prefix="LATCH_"・pydantic-settings)
- Produces: `Settings.alert_queue_enabled: bool`(既定False)・`Settings.alert_queue_interval_sec: int`(既定60)・`Settings.alert_queue_pending: int`(既定1000)・`Settings.alert_queue_oldest_seconds: int`(既定300)・`Settings.alert_queue_arm_ttl_sec: int`(既定180)。Task 2(lag_alert)・Task 3(lifespan)・Task 6(compose override)が消費する。env名は`LATCH_ALERT_QUEUE_ENABLED`/`LATCH_ALERT_QUEUE_INTERVAL_SEC`/`LATCH_ALERT_QUEUE_PENDING`/`LATCH_ALERT_QUEUE_OLDEST_SECONDS`/`LATCH_ALERT_QUEUE_ARM_TTL_SEC`

- [ ] **Step 1: 失敗する追随試験をtest_settings.pyへ追加する**

ファイル末尾に追加する(既存の関数・assertは1行も触らない):

```python
def test_alert_settings_defaults(monkeypatch):
    monkeypatch.delenv("LATCH_ALERT_QUEUE_ENABLED", raising=False)
    monkeypatch.delenv("LATCH_ALERT_QUEUE_INTERVAL_SEC", raising=False)
    monkeypatch.delenv("LATCH_ALERT_QUEUE_PENDING", raising=False)
    monkeypatch.delenv("LATCH_ALERT_QUEUE_OLDEST_SECONDS", raising=False)
    monkeypatch.delenv("LATCH_ALERT_QUEUE_ARM_TTL_SEC", raising=False)
    s = Settings()
    assert s.alert_queue_enabled is False  # M4 ws-7(design §2.5案A・ciは無効のまま)
    assert s.alert_queue_interval_sec == 60  # 発報器のtick(collectorと同一周期)
    assert s.alert_queue_pending == 1000  # design §5-5(docs未規定の初期値)
    assert s.alert_queue_oldest_seconds == 300  # design §5-5
    assert s.alert_queue_arm_ttl_sec == 180  # アームキーTTL(§2.3)


def test_alert_settings_are_exactly_five_fields(monkeypatch):
    # alert系設定の過剰供給を防ぐ機械検査(obs系集合ピンと同一作法)
    for key in (
        "LATCH_ALERT_QUEUE_ENABLED",
        "LATCH_ALERT_QUEUE_INTERVAL_SEC",
        "LATCH_ALERT_QUEUE_PENDING",
        "LATCH_ALERT_QUEUE_OLDEST_SECONDS",
        "LATCH_ALERT_QUEUE_ARM_TTL_SEC",
    ):
        monkeypatch.delenv(key, raising=False)
    alert_fields = {f for f in Settings.model_fields if f.startswith("alert_")}
    assert alert_fields == {
        "alert_queue_enabled",
        "alert_queue_interval_sec",
        "alert_queue_pending",
        "alert_queue_oldest_seconds",
        "alert_queue_arm_ttl_sec",
    }


def test_alert_settings_env_override(monkeypatch):
    monkeypatch.setenv("LATCH_ALERT_QUEUE_ENABLED", "true")
    monkeypatch.setenv("LATCH_ALERT_QUEUE_INTERVAL_SEC", "30")
    monkeypatch.setenv("LATCH_ALERT_QUEUE_PENDING", "5")
    monkeypatch.setenv("LATCH_ALERT_QUEUE_OLDEST_SECONDS", "10")
    monkeypatch.setenv("LATCH_ALERT_QUEUE_ARM_TTL_SEC", "60")
    s = Settings()
    assert s.alert_queue_enabled is True
    assert s.alert_queue_interval_sec == 30
    assert s.alert_queue_pending == 5
    assert s.alert_queue_oldest_seconds == 10
    assert s.alert_queue_arm_ttl_sec == 60
```

- [ ] **Step 2: 試験を実行して失敗を確認する(Red)**

Run: `cd backend && uv run pytest tests/unit/test_settings.py -v`
Expected: FAIL with "AttributeError: ... 'alert_queue_enabled'"(3関数がエラー・既存関数は全パス)

- [ ] **Step 3: settings.pyへ5本を追加する**

`kpi_backfill_days`の直後(ファイル末尾)に追加する:

```python
    # --- Queue lag alert発報器(M4 ws-7・design §2.5案A)---
    # API側の周期タスク(worker内collectorと同じDB集計値を消費・worker停止中も
    # 発報が続く)。既定無効(ci干渉ゼロ)・stagingのみcompose.staging.nfr.yamlで
    # 有効化。発報は条件成立中1回だけ(プロセス内フラグ+Redisアームキー)
    alert_queue_enabled: bool = False
    # 発報器のtick秒(collectorと同一60秒)
    alert_queue_interval_sec: int = 60
    # 発報条件(OR): match_events pending件数>=1,000 または 最古pending年齢
    # >=300秒(docs未規定の初期値・承認事項§5-5。計測を見て調整する)
    alert_queue_pending: int = 1000
    alert_queue_oldest_seconds: int = 300
    # 発報の単発化アームキー(Redis SET NX)のTTL秒。条件成立中の1回発報を
    # 2インスタンス間で担保し・プロセス死亡後の再武装はTTL失効で回復する
    alert_queue_arm_ttl_sec: int = 180
```

- [ ] **Step 4: 試験を実行して通ることを確認する(Green)**

Run: `cd backend && uv run pytest tests/unit/test_settings.py -v`
Expected: PASS(test_settings.py全関数)

- [ ] **Step 5: 既存ピンの無傷を確認する**

Run: `cd backend && uv run pytest tests/unit/test_settings.py tests/unit/llm/test_llm_factory.py -v`
Expected: PASS(obs_・kpi_・llm_集合ピンが非干渉であることの確認)

- [ ] **Step 6: コミット**

```bash
git add backend/src/latch/settings.py backend/tests/unit/test_settings.py
git commit -m "feat: settingsへQueue lag alert系env 5本追加(M4 ws-7・既定無効)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 2: obs/lag_alert.py(Queue lag alert発報器)

**Files:**
- Create: `backend/src/latch/obs/lag_alert.py`
- Test: `backend/tests/unit/test_obs_lag_alert.py`

**Interfaces:**
- Consumes: `Settings.alert_queue_*`(Task 1)・`Clock`(core/clock.py)・SQLAlchemy engine・`redis.asyncio.Redis`(decode_responses=True)
- Produces: `QueueLagAlert(engine, clock, redis, pending_threshold: int, oldest_threshold: int, arm_ttl_s: int = 180, alert_emitter=structured_log_alert, resolve_emitter=structured_log_resolve)` — `async check_once() -> None`・`async run(stop: asyncio.Event | None = None) -> None`。定数`ARM_KEY = "latch:alert:queue:arm"`。Task 3(lifespan)とtest_n5_50が消費する。発報ログ=logger `"latch.pipeline.alert"` warning「queue lag alert {json}」・resolve=同logger info「queue lag resolve {json}」(イベント辞書: kind・pending・oldest_pending_seconds)

- [ ] **Step 1: 失敗するunit試験を書く**

`backend/tests/unit/test_obs_lag_alert.py`を新規作成する:

```python
"""QueueLagAlert: 閾値判定・単発化(アーム)・解除resolve・fail-open(M4 ws-7)。

collector.pyと同一のDB集計値(pending件数・最古pending年齢)を消費するが、
発報器自体はAPI側に置く(滞留の作り方=worker停止のため・design §2.5案A)。
年齢はClock差分で計算する(アプリ側・C2)。
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.obs.lag_alert import ARM_KEY, QueueLagAlert

NOW = datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC)


class _StubResult:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _StubConn:
    def __init__(self, row):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        return _StubResult(self._row)


class _StubEngine:
    def __init__(self, rows):
        self._rows = list(rows) if rows is not None else None
        self.connect_calls = 0

    def connect(self):
        if self._rows is None:
            raise RuntimeError("stub db down")
        row = self._rows.pop(0) if self._rows else (0, None)
        self.connect_calls += 1
        return _StubConn(row)


class _Events:
    def __init__(self):
        self.alerts: list[dict] = []
        self.resolves: list[dict] = []

    def alert(self, event):
        self.alerts.append(event)

    def resolve(self, event):
        self.resolves.append(event)


def _alert(engine, redis, *, pending=1000, oldest=300, events=None, interval=60):
    events = events or _Events()
    q = QueueLagAlert(
        engine=engine,
        clock=FakeClock(NOW),
        redis=redis,
        pending_threshold=pending,
        oldest_threshold=oldest,
        arm_ttl_s=180,
        interval_sec=interval,
        alert_emitter=events.alert,
        resolve_emitter=events.resolve,
    )
    return q, events


@pytest.fixture
async def redis():
    import fakeredis.aioredis

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


async def test_threshold_exceeded_emits_alert_once(redis):
    """pending件数閾値超過で1回発報・イベント辞書は数値を含む。"""
    engine = _StubEngine([(1500, None)])
    q, events = _alert(engine, redis, pending=1000, oldest=300)
    await q.check_once()
    assert len(events.alerts) == 1
    assert events.alerts[0]["kind"] == "queue_lag"
    assert events.alerts[0]["pending"] == 1500
    assert events.alerts[0]["oldest_pending_seconds"] == 0.0


async def test_alert_not_repeated_while_condition_holds(redis):
    """条件成立が続く限り2回目のcheckで再発報しない(プロセス内フラグ)。"""
    engine = _StubEngine([(1500, None), (1500, None)])
    q, events = _alert(engine, redis, pending=1000, oldest=300)
    await q.check_once()
    await q.check_once()
    assert len(events.alerts) == 1


async def test_condition_cleared_emits_resolve_and_rearms(redis):
    """条件解除でresolve 1回+フラグ解除・再び超過すると再発報できる。"""
    engine = _StubEngine([(1500, None), (0, None), (0, None), (9999, None)])
    q, events = _alert(engine, redis, pending=1000, oldest=300)
    await q.check_once()  # alert
    await q.check_once()  # cleared → resolve
    await q.check_once()  # cleared → 何もしない
    await q.check_once()  # 再超過 → alert
    assert len(events.alerts) == 2
    assert len(events.resolves) == 1
    assert events.resolves[0]["kind"] == "queue_lag_resolve"
    assert await redis.get(ARM_KEY) is None  # resolve時にアーム解除


async def test_redis_arm_key_prevents_cross_instance_duplicate(redis):
    """api1が発報済み(アーム済み)のとき・同一Redisを参照するapi2は発報しない。"""
    events1, events2 = _Events(), _Events()
    q1, _ = _alert(_StubEngine([(1500, None)]), redis, pending=1000, oldest=300, events=events1)
    q2, _ = _alert(_StubEngine([(1500, None)]), redis, pending=1000, oldest=300, events=events2)
    await q1.check_once()
    await q2.check_once()
    assert len(events1.alerts) == 1
    assert len(events2.alerts) == 0
    assert await redis.get(ARM_KEY) is not None


async def test_age_computed_from_clock_difference(redis):
    """最古pending年齢はClock差分で計算する(SQL側でなく・C2)。"""
    old = NOW - timedelta(seconds=400)
    engine = _StubEngine([(2, old)])
    q, events = _alert(engine, redis, pending=1000, oldest=300)
    await q.check_once()
    # 件数(2)は閾値未満・年齢(400秒)が閾値超過で発報する
    assert len(events.alerts) == 1
    assert events.alerts[0]["oldest_pending_seconds"] == 400.0


async def test_redis_failure_still_alerts(redis, monkeypatch):
    """Redis故障時も発報は落とさない(観測系fail-open・単発化のみ劣化)。"""
    import unittest.mock

    engine = _StubEngine([(1500, None)])
    q, events = _alert(engine, redis, pending=1000, oldest=300)
    with unittest.mock.patch.object(redis, "set", side_effect=ConnectionError("down")):
        await q.check_once()
    assert len(events.alerts) == 1


async def test_db_error_is_swallowed_and_run_continues(redis):
    """DB失敗は握って次tickへ(collector.runと同一契約)。stopで確実に抜ける。"""
    engine = _StubEngine([])
    engine._rows = None  # connect()が例外を投げる=DB故障の注入
    q, events = _alert(engine, redis, pending=1000, oldest=300, interval=0.01)
    stop = asyncio.Event()

    async def stop_soon():
        await asyncio.sleep(0.05)
        stop.set()

    task = asyncio.create_task(stop_soon())
    await q.run(stop=stop)  # 例外が外へ漏れない・stopで終了
    await task
    assert events.alerts == []


async def test_arm_ttl_is_set_on_redis_key(redis):
    """アームキーはSET NX+TTLで立てる(TTL失効で再武装できる)。"""
    engine = _StubEngine([(1500, None)])
    q, _ = _alert(engine, redis, pending=1000, oldest=300)
    await q.check_once()
    assert await redis.get(ARM_KEY) == "1"
    assert 0 < await redis.ttl(ARM_KEY) <= 180
```

- [ ] **Step 2: 試験を実行して失敗を確認する(Red)**

Run: `cd backend && uv run pytest tests/unit/test_obs_lag_alert.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'latch.obs.lag_alert'"

- [ ] **Step 3: 実装を書く**

`backend/src/latch/obs/lag_alert.py`を新規作成する:

```python
"""Queue lag alert発報器(M4 ws-7・design §2.5案A)。API側の周期タスク。

滞留の作り方がworker停止(10 §4.7)のため・worker内のcollector
(docstring「ws-7のQueue障害試験はこのgaugeを消費する」)を発報器にすると
worker停止で発報器も止まる。同じDB集計値(match_events pending件数・最古
pending年齢)をAPIプロセスから直接SELECTして発報する。
発報は条件成立中1回だけ: プロセス内フラグ+Redisアームキー(SET NX EX)で
api1/api2の2インスタンス間でも単発化を担保(supervisor訂正・2026-10-05)。
解除(resolve)はアーム済みプロセスが条件不成立を観測したときにログ出力+キー
削除(発報→解除の往復を証拠化・D-16 alertと同型の規律)。アームした
プロセスが死亡した場合はTTL失効後に別インスタンスが再発報できる(観測の
回復)。Redis故障時も発報は落とさない(fail-open・単発化のみ劣化)・DB失敗
は握って次tick(collector.runと同一契約)。年齢はClock差分(C2)。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import text

from latch.core.clock import Clock

logger = logging.getLogger("latch.pipeline.alert")

# アームキー(staging Redisはstaging専用のため接頭辞なし)
ARM_KEY = "latch:alert:queue:arm"

_SQL_QUEUE_LAG = text("""
    SELECT count(*) AS pending, min(created_at) AS oldest
    FROM match_events WHERE status = 'pending'
""")


def structured_log_alert(event: dict) -> None:
    """alert媒体の段階実体(構造化ログ・guard.structured_log_alertと同型)。"""
    logger.warning("queue lag alert %s", json.dumps(event, ensure_ascii=False))


def structured_log_resolve(event: dict) -> None:
    logger.info("queue lag resolve %s", json.dumps(event, ensure_ascii=False))


class QueueLagAlert:
    """周期タスク: check_onceをinterval秒ごとに繰り返す。"""

    def __init__(
        self,
        *,
        engine,
        clock: Clock,
        redis,
        pending_threshold: int,
        oldest_threshold: int,
        arm_ttl_s: int = 180,
        interval_sec: int = 60,
        alert_emitter: Callable[[dict], None] = structured_log_alert,
        resolve_emitter: Callable[[dict], None] = structured_log_resolve,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._redis = redis
        self._pending_threshold = pending_threshold
        self._oldest_threshold = oldest_threshold
        self._arm_ttl_s = arm_ttl_s
        self._alert_emitter = alert_emitter
        self._resolve_emitter = resolve_emitter
        self._interval_sec = interval_sec
        self._alerted = False

    async def check_once(self) -> None:
        """DB集計1回分: 閾値判定→発報/解除。例外は握らずrunが握る。"""
        now = self._clock.now()
        async with self._engine.connect() as conn:
            row = (await conn.execute(_SQL_QUEUE_LAG)).first()
        pending = int(row[0]) if row is not None else 0
        oldest = row[1] if row is not None else None
        age = (now - oldest).total_seconds() if oldest is not None else 0.0
        event = {
            "kind": "queue_lag",
            "pending": pending,
            "oldest_pending_seconds": round(age, 1),
        }
        if pending >= self._pending_threshold or age >= self._oldest_threshold:
            if self._alerted:
                return  # 条件成立中の再発報を抑制(プロセス内フラグ)
            if not await self._arm():
                return  # 別インスタンスが発報済み(クロスインスタンス単発化)
            self._alerted = True
            self._alert_emitter(event)
            return
        if self._alerted:
            self._alerted = False
            await self._disarm()
            self._resolve_emitter(event)

    async def _arm(self) -> bool:
        """アームキー確保(SET NX+TTL)。Redis故障はfail-openでTrue扱い。"""
        try:
            return bool(
                await self._redis.set(
                    ARM_KEY, "1", nx=True, ex=self._arm_ttl_s
                )
            )
        except Exception:
            logger.warning("queue alert arm failed", exc_info=True)
            return True

    async def _disarm(self) -> None:
        try:
            await self._redis.delete(ARM_KEY)
        except Exception:
            logger.warning("queue alert disarm failed", exc_info=True)

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """起動時即check→interval待ち→check→…。例外は握ってwarning+次tick
        (MetricsCollector.runと同一契約)。待機はstop即応(MetricsCollector
        ._wait_intervalと同一契約)。"""
        while stop is None or not stop.is_set():
            try:
                await self.check_once()
            except Exception:
                logger.warning("queue lag alert check failed", exc_info=True)
            await self._wait_interval(stop)

    async def _wait_interval(self, stop: asyncio.Event | None) -> None:
        if stop is None:
            await asyncio.sleep(self._interval_sec)
            return
        sleep_task = asyncio.create_task(asyncio.sleep(self._interval_sec))
        stop_task = asyncio.create_task(stop.wait())
        done, pending = await asyncio.wait(
            {sleep_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
```

- [ ] **Step 4: 試験を実行して通ることを確認する(Green)**

Run: `cd backend && uv run pytest tests/unit/test_obs_lag_alert.py -v`
Expected: PASS(8件)

- [ ] **Step 5: 既存collector試験・arch testの無傷を確認する**

Run: `cd backend && uv run pytest tests/unit/test_obs_collector.py tests/unit/test_arch_no_direct_time.py -v`
Expected: PASS

- [ ] **Step 6: コミット**

```bash
git add backend/src/latch/obs/lag_alert.py backend/tests/unit/test_obs_lag_alert.py
git commit -m "feat: Queue lag alert発報器(API側周期タスク・アームキー単発化)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 3: main.pyのlifespanへlag alertタスク登録(静的ピン先行)

**Files:**
- Modify: `backend/src/latch/main.py`
- Test: `backend/tests/unit/test_nfr_pins.py`(新規作成・本Task分は1件)

**Interfaces:**
- Consumes: `QueueLagAlert`(Task 2)・`Settings.alert_queue_*`(Task 1)
- Produces: `alert_queue_enabled=True`の構成で起動したAPIプロセス内に周期タスクが立つ(unit試験からは到達しない — ASGITransportはlifespanを発火させないため、正しさの証明はstagingの発報観察(test_n5_50)が担う)。app.stateへの格納はしない(既存event_relayと同型のローカル変数運用)

- [ ] **Step 1: 失敗する静的ピンを書く**

`backend/tests/unit/test_nfr_pins.py`を新規作成する:

```python
"""ws-7の静的ピン(M4 ws-7)。override・Makefile・pyproject・lifespan wiring。

既定環境(compose.staging.yaml等)は無変更のため・「既定に漏れていないこと」
の無傷ピンも本ファイルが担う(loadgen composeピンと同一作法)。
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_main_lifespan_registers_lag_alert_when_enabled():
    """lifespanの登録コードが在ることの静的ピン(lifespan発火unitは存在しない)。"""
    text = (ROOT / "backend" / "src" / "latch" / "main.py").read_text(encoding="utf-8")
    assert "settings.alert_queue_enabled" in text
    assert "QueueLagAlert(" in text
    # finallyでの停止・クローズ(event_relayと同型の規律)
    assert "lag_alert_stop.set()" in text
    assert "lag_alert_task.cancel()" in text
    assert "lag_alert_redis.aclose()" in text
```

- [ ] **Step 2: 試験を実行して失敗を確認する(Red)**

Run: `cd backend && uv run pytest tests/unit/test_nfr_pins.py -v`
Expected: FAIL with "assert 'settings.alert_queue_enabled' in ..."

- [ ] **Step 3: main.pyのlifespanへ登録コードを追記する**

(a) importの追記。`from latch.obs.middleware import HTTPMetricsMiddleware`の直後に追加:

```python
from latch.obs.lag_alert import QueueLagAlert
```

(b) `_lifespan`内・`event_relay_task`/`event_relay_stop`の初期化文(`event_relay_task = None`の行)を次のように書き換える(lag alert分のローカル変数を追加):

```python
    event_relay_task = None
    event_relay_stop = None
    lag_alert_task = None
    lag_alert_stop = None
    lag_alert_redis = None
```

(c) `if build_events:`ブロックの直前に追加する(build_eventsと独立した無効化スイッチのため):

```python
    if settings.alert_queue_enabled:
        # Queue lag alert発報器(M4 ws-7・design §2.5案A)。既定無効(ci無効
        # 維持)・stagingのみcompose.staging.nfr.yamlで有効化。独立Redis接続
        # (既存redis_clientのbuild条件には触らない)・engineはlifespan共有資産
        lag_alert_redis = aioredis.Redis.from_url(
            settings.redis_url, decode_responses=True
        )
        queue_alert = QueueLagAlert(
            engine=engine,
            clock=app.state.clock,
            redis=lag_alert_redis,
            pending_threshold=settings.alert_queue_pending,
            oldest_threshold=settings.alert_queue_oldest_seconds,
            arm_ttl_s=settings.alert_queue_arm_ttl_sec,
            interval_sec=settings.alert_queue_interval_sec,
        )
        lag_alert_stop = asyncio.Event()
        lag_alert_task = asyncio.create_task(queue_alert.run(stop=lag_alert_stop))
```

(d) `finally:`ブロックの先頭へ停止処理を追加する(event_relayの停止より前):

```python
        if lag_alert_stop is not None:
            lag_alert_stop.set()
        if lag_alert_task is not None:
            lag_alert_task.cancel()
            try:
                await lag_alert_task
            except asyncio.CancelledError:
                pass
        if lag_alert_redis is not None:
            await lag_alert_redis.aclose()
```

- [ ] **Step 4: 試験を実行して通ることを確認する(Green)+既存無傷**

Run: `cd backend && uv run pytest tests/unit/test_nfr_pins.py tests/unit/test_app_health.py tests/unit/test_rate_limit_wiring.py -v`
Expected: PASS(静的ピン・既存unit無傷)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/main.py backend/tests/unit/test_nfr_pins.py
git commit -m "feat: lifespanへQueue lag alertタスク登録(enabled時のみ・独立Redis接続)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 4: loadgen/probe.py(合成監視プローブ)+CLI probeサブコマンド

**Files:**
- Create: `backend/src/latch/loadgen/probe.py`
- Modify: `backend/src/latch/loadgen/__main__.py`
- Test: `backend/tests/unit/test_loadgen_probe.py`

**Interfaces:**
- Consumes: `Clock`・`httpx.AsyncClient`・`latch.auth.tools.issue_idp_token`(client.pyと同一のトークン交換経路)・`loadgen.collect.write_evidence`(証拠YAML出力の再利用)
- Produces: `ProbeSample(kind, at, path, ok, error, tagged_planned_outage)`(frozen dataclass)・`summarize(samples, *, pass_rate_threshold: float = 0.995) -> dict`(verdict_995等)・`TokenCache(http, keys_dir, subject, clock)`(`async bearer() -> str`)・`async run_probe(*, http, base_url, token_cache, duration_s, clock, main_interval_s=60.0, dense_interval_s=10.0, timeout_s=10.0, marker: Path | None) -> list[ProbeSample]`・`parse_outages(raw: str) -> list[tuple[datetime, datetime]]`(事後集計のオプション入力・§8手順では未使用)。`__main__.py`の`probe`サブコマンドは`--duration/--subject/--planned-outage/--outage-marker/--out`を受け、`cmd_probe`から`collect.write_evidence(report, prefix="ws7-probe", clock=clock)`で証拠を出す

- [ ] **Step 1: 失敗するunit試験を書く**

`backend/tests/unit/test_loadgen_probe.py`を新規作成する:

```python
"""プローブ集計の純関数・TokenCache・run_probeの決定性(M4 ws-7)。

成功率=成功プローブ数÷総プローブ数(main側・マーカータグ付きは分母外)。
合否は99.5%の機械判定(10 §4.3・01 §20)。実HTTPはunit対象外で・run_probeは
sender注入で決定的に検証する。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from latch.core.clock import FakeClock
from latch.loadgen.probe import ProbeSample, run_probe, summarize

NOW = datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC)


def _s(kind="main", ok=True, error=None, tagged=False, offset_s=0.0, path="/health"):
    return ProbeSample(
        kind=kind,
        at=NOW + timedelta(seconds=offset_s),
        path=path,
        ok=ok,
        error=error,
        tagged_planned_outage=tagged,
    )


def test_summarize_excludes_tagged_from_denominator():
    """マーカータグ付きサンプルは分母外(承認事項§5-2)。"""
    samples = [
        _s(ok=True, offset_s=i * 60) for i in range(6)
    ] + [_s(ok=False, error="timeout", tagged=True, offset_s=400)]
    result = summarize(samples)
    assert result["main_total"] == 7
    assert result["main_excluded_planned_outage"] == 1
    assert result["main_counted"] == 6
    assert result["main_ok"] == 6
    assert result["main_success_rate"] == 1.0
    assert result["verdict_995"] is True


def test_summarize_counts_timeout_as_failure():
    samples = [_s(ok=True), _s(ok=False, error="timeout"), _s(ok=False, error="HTTP 502")]
    result = summarize(samples)
    assert result["main_ok"] == 1
    assert result["main_error"] == 2
    assert result["main_success_rate"] == pytest.approx(1 / 3)
    assert result["verdict_995"] is False
    assert result["timeouts"] == 1
    assert result["errors_by_kind"] == {"timeout": 1, "HTTP 502": 1}


def test_summarize_records_max_consecutive_failures():
    """連続失敗最長の記録(開始/終了時刻つき)。"""
    samples = [
        _s(ok=True),
        _s(ok=False, error="timeout", offset_s=60),
        _s(ok=False, error="timeout", offset_s=120),
        _s(ok=True, offset_s=180),
    ]
    result = summarize(samples)
    assert result["max_consecutive_failures"]["count"] == 2
    assert result["max_consecutive_failures"]["started_at"] == (NOW + timedelta(seconds=60)).isoformat()
    assert result["max_consecutive_failures"]["ended_at"] == (NOW + timedelta(seconds=120)).isoformat()


def test_summarize_separates_dense_from_verdict():
    """dense側は合否対象外(design §2.1)。"""
    samples = [_s(ok=True), _s(kind="dense", ok=False, error="HTTP 503")]
    result = summarize(samples)
    assert result["verdict_995"] is True
    assert result["dense_total"] == 1
    assert result["dense_ok"] == 0


def test_summarize_empty_main_returns_no_verdict():
    result = summarize([])
    assert result["main_success_rate"] is None
    assert result["verdict_995"] is False


async def test_run_probe_collects_main_and_dense_samples():
    """main/dense両ループが指定間隔で回り・durationで終了する(sleep注入で決定的)。"""
    clock = FakeClock(NOW)

    async def fake_sleep(seconds: float) -> None:
        clock.advance(timedelta(seconds=seconds))

    async def fake_request(path: str, bearer: str):
        clock.advance(timedelta(milliseconds=50))

    samples = await run_probe(
        http=None,
        base_url="http://test",
        token_cache=None,
        duration_s=5.0,
        clock=clock,
        main_interval_s=2.0,
        dense_interval_s=1.0,
        request_path=fake_request,
        marker=None,
        bearer_factory=lambda: "Bearer x",
        sleep=fake_sleep,
    )
    mains = [s for s in samples if s.kind == "main"]
    dense = [s for s in samples if s.kind == "dense"]
    assert len(mains) == 3  # t=0, 2, 4(各回で+50ms)
    assert len(dense) == 5  # t=0..4
    assert all(s.ok for s in samples)
    assert all(s.tagged_planned_outage is False for s in samples)


async def test_marker_presence_tags_sample(tmp_path: Path):
    """マーカーファイルの存在がサンプルをタグ付けする(§2.1読み替え2)。"""
    clock = FakeClock(NOW)
    marker = tmp_path / "outage.marker"

    async def fake_sleep(seconds: float) -> None:
        clock.advance(timedelta(seconds=seconds))

    async def fake_request(path: str, bearer: str):
        pass

    def flip_marker():
        marker.write_text("1")

    samples = await run_probe(
        http=None,
        base_url="http://test",
        token_cache=None,
        duration_s=2.0,
        clock=clock,
        main_interval_s=1.0,
        dense_interval_s=1.0,
        request_path=fake_request,
        marker=marker,
        bearer_factory=lambda: "Bearer x",
        on_tick=flip_marker,
        sleep=fake_sleep,
    )
    assert any(s.tagged_planned_outage for s in samples)
    assert not all(s.tagged_planned_outage for s in samples)


def test_token_cache_reissues_after_50min():
    """50分経過で再発行する(access token TTL 1時間>窓60分対策)。"""
    from latch.loadgen.probe import TOKEN_REISSUE_SEC, TokenCache

    assert TOKEN_REISSUE_SEC == 50 * 60
    clock = FakeClock(NOW)
    issued: list[int] = []

    class _StubCache(TokenCache):
        async def _refresh(self):
            issued.append(1)
            self._issued_at = self._clock.now()
            self._token = f"tok-{len(issued)}"

    cache = _StubCache(http=None, keys_dir=None, subject="load-u0001", clock=clock)
    import asyncio

    async def main():
        await cache.bearer()
        await cache.bearer()  # 40分後: 再発行しない
        clock.advance(timedelta(minutes=11))  # 合計51分
        await cache.bearer()  # 再発行

    asyncio.run(main())
    assert issued == [1, 1, 2]
    assert cache._token == "tok-2"
```

- [ ] **Step 2: 試験を実行して失敗を確認する(Red)**

Run: `cd backend && uv run pytest tests/unit/test_loadgen_probe.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'latch.loadgen.probe'"

- [ ] **Step 3: probe.pyを実装する**

`backend/src/latch/loadgen/probe.py`を新規作成する:

```python
"""合成監視プローブ(10 §4.3・M4 ws-7 design §2.1案A)。

nginx入口へGET /health(認証外)とGET /v1/users/me(認証付き)を打つ。合否対象
は1分間隔(main)側のみ・failover精度観察用の10秒間隔(dense)を併設する
(合否判定外)。成功率=成功プローブ数÷総プローブ数・計画停止のサンプルは
マーカーファイル(実行中タグ記入・§2.1読み替え2)の存在でtaggedとし分母外
(承認事項§5-2)。タイムアウトは10秒(nginxのproxy_next_upstream再送が収まる
値)。トークンはsubjectのaccess_tokenをキャッシュし・発行から50分経過で
再発行する(access token TTL=1時間と試験窓60分の一致を避ける)。
実行時刻はClock経由(C2)・待機はasyncio.sleep(stub.pyと同一規律)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from latch.auth.tools import issue_idp_token
from latch.core.clock import Clock

MAIN_INTERVAL_S = 60.0
DENSE_INTERVAL_S = 10.0
REQUEST_TIMEOUT_S = 10.0
TOKEN_REISSUE_SEC = 50 * 60.0
PASS_RATE_THRESHOLD = 0.995  # 01 §20(機械判定・正式合否はG4の人間側)
PROBE_PATHS = ("/health", "/v1/users/me")
# マーカーの既定位置(ファイル位置基準・cwd非依存。loadgenのSEED_DIR教訓)
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_MARKER = REPO_ROOT / ".staging" / "probe-outage.marker"


@dataclass(frozen=True)
class ProbeSample:
    """1リクエストの記録(1プローブ=1リクエスト・集計はmain側合計)。"""

    kind: str  # "main" | "dense"
    at: datetime
    path: str
    ok: bool
    error: str | None
    tagged_planned_outage: bool


def summarize(
    samples: list[ProbeSample], *, pass_rate_threshold: float = PASS_RATE_THRESHOLD
) -> dict:
    """main側の成功率と機械判定・dense側の参考集計(純関数)。"""
    main = [s for s in samples if s.kind == "main"]
    dense = [s for s in samples if s.kind == "dense"]
    counted = [s for s in main if not s.tagged_planned_outage]
    ok = sum(1 for s in counted if s.ok)
    rate = (ok / len(counted)) if counted else None
    verdict = rate is not None and rate >= pass_rate_threshold

    max_streak = {"count": 0, "started_at": None, "ended_at": None}
    streak = 0
    for s in counted:
        if s.ok:
            streak = 0
            continue
        streak += 1
        if streak == 1:
            streak_started = s.at.isoformat()
        if streak > max_streak["count"]:
            max_streak = {
                "count": streak,
                "started_at": streak_started,
                "ended_at": s.at.isoformat(),
            }
    timeouts = sum(1 for s in counted if s.error == "timeout")
    errors: dict[str, int] = {}
    for s in counted:
        if not s.ok and s.error is not None:
            errors[s.error] = errors.get(s.error, 0) + 1
    return {
        "main_total": len(main),
        "main_excluded_planned_outage": len(main) - len(counted),
        "main_counted": len(counted),
        "main_ok": ok,
        "main_error": len(counted) - ok,
        "main_success_rate": rate,
        "verdict_995": verdict,
        "dense_total": len(dense),
        "dense_ok": sum(1 for s in dense if s.ok),
        "max_consecutive_failures": max_streak,
        "timeouts": timeouts,
        "errors_by_kind": dict(sorted(errors.items())),
    }


class TokenCache:
    """subject→access_tokenのキャッシュ(50分で再発行・client.py同型の交換)。"""

    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        keys_dir: str | Path,
        subject: str,
        clock: Clock,
    ) -> None:
        self._http = http
        self._private_key = (Path(keys_dir) / "idp_test_private.pem").read_bytes()
        self._subject = subject
        self._clock = clock
        self._token: str | None = None
        self._issued_at: datetime | None = None

    async def bearer(self) -> str:
        now = self._clock.now()
        if (
            self._token is None
            or self._issued_at is None
            or (now - self._issued_at).total_seconds() >= TOKEN_REISSUE_SEC
        ):
            await self._refresh()
        return f"Bearer {self._token}"

    async def _refresh(self) -> None:
        idp_token = issue_idp_token(
            provider="google",
            subject=self._subject,
            private_key_pem=self._private_key,
        )
        r = await self._http.post(
            "/v1/auth/token", json={"provider": "google", "idp_token": idp_token}
        )
        r.raise_for_status()
        self._token = r.json()["access_token"]
        self._issued_at = self._clock.now()


async def run_probe(
    *,
    http: httpx.AsyncClient | None,
    base_url: str,
    token_cache: TokenCache | None,
    duration_s: float,
    clock: Clock,
    main_interval_s: float = MAIN_INTERVAL_S,
    dense_interval_s: float = DENSE_INTERVAL_S,
    timeout_s: float = REQUEST_TIMEOUT_S,
    marker: Path | None = DEFAULT_MARKER,
    request_path=None,
    bearer_factory=None,
    on_tick=None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[ProbeSample]:
    """main/denseの2ループをduration_sまで回す。

    request_path/bearer_factory/on_tick/sleepはunit注入用(既定は実HTTPと
    TokenCache.bearerとasyncio.sleep・collector.pyと同一の注入作法)。
    マーカーは各サンプル記録時の存在確認で判定する(実行中タグ記入・
    §2.1読み替え2)。タイムアウトはerror="timeout"として集計(summarizeの
    timeouts/errors_by_kindと同一キー)。待機はasyncio.sleep(時刻参照では
    ない・stub.pyと同一規律)で・unitはsleep注入でClockを進めて決定的にする。
    """

    async def _request(path: str, bearer: str) -> tuple[bool, str | None]:
        if request_path is not None:
            request_path(path, bearer)
            return True, None
        try:
            r = await http.get(
                path, headers={"Authorization": bearer}, timeout=timeout_s
            )
        except httpx.TimeoutException:
            return False, "timeout"
        except Exception as exc:  # 接続失敗等・プローブは止めない
            return False, type(exc).__name__
        return r.status_code == 200, (
            None if r.status_code == 200 else f"HTTP {r.status_code}"
        )

    async def _bearer() -> str:
        if bearer_factory is not None:
            return bearer_factory()
        return await token_cache.bearer()

    samples: list[ProbeSample] = []

    async def _once(kind: str, at: datetime, bearer: str) -> None:
        tagged = marker.exists() if marker is not None else False
        for path in PROBE_PATHS:
            ok, error = await _request(path, bearer)
            samples.append(
                ProbeSample(
                    kind=kind,
                    at=at,
                    path=path,
                    ok=ok,
                    error=error,
                    tagged_planned_outage=tagged,
                )
            )
        if on_tick is not None:
            on_tick()

    async def _loop(interval: float, kind: str) -> None:
        start = clock.now()
        while (clock.now() - start).total_seconds() < duration_s:
            now = clock.now()
            bearer = await _bearer()
            await _once(kind, now, bearer)
            await sleep(interval)

    await asyncio.gather(
        _loop(main_interval_s, "main"), _loop(dense_interval_s, "dense")
    )
    return samples


def parse_outages(raw: str) -> list[tuple[datetime, datetime]]:
    """事後集計用の窓指定パース("ISO:ISO,ISO:ISO")。§8手順では未使用
    (マーカー方式に一本化・§2.1読み替え2)だが、証拠の窓記録に残す。"""
    windows: list[tuple[datetime, datetime]] = []
    if not raw:
        return windows
    for part in raw.split(","):
        start_s, _, end_s = part.partition(":")
        start = datetime.fromisoformat(start_s)
        end = datetime.fromisoformat(end_s)
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("タイムゾーン付きISO8601で指定する")
        windows.append((start, end))
    return windows
```

注: FakeClock下では`asyncio.sleep`は実時間で待つため・unit試験のinterval(1〜2秒)は実時間で数秒かかる(許容・staging_tests/helpers_waitと同一の作法)。unit試験の`run_probe`呼び出しは・引数`on_tick=flip_marker`(マーカー試験のみ)と・既定None(それ以外)を使う。

- [ ] **Step 4: 試験を実行して通ることを確認する(Green)**

Run: `cd backend && uv run pytest tests/unit/test_loadgen_probe.py -v`
Expected: PASS(8件)

- [ ] **Step 5: __main__.pyへprobeサブコマンドを追加する**

importの追記。`from latch.loadgen import collect, massload`の行を:

```python
from latch.loadgen import collect, massload, probe
```

に変え・冒頭の標準ライブラリimport群へ`import yaml`を追加する。

`cmd_bruteforce`の直後に追加:

```python
async def cmd_probe(args) -> int:
    """合成監視プローブ(60分窓・main 1分+dense 10秒・証拠YAML出力)。"""
    clock = SystemClock()
    marker = (
        Path(args.outage_marker)
        if args.outage_marker
        else probe.DEFAULT_MARKER
    )
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=probe.REQUEST_TIMEOUT_S
    ) as http:
        token_cache = probe.TokenCache(
            http=http, keys_dir=args.keys, subject=args.subject, clock=clock
        )
        await token_cache.bearer()  # 起動時に1回発行(失敗は即Abort)
        samples = await probe.run_probe(
            http=http,
            base_url=args.base_url,
            token_cache=token_cache,
            duration_s=args.duration,
            clock=clock,
            marker=marker,
        )
    outages = probe.parse_outages(args.planned_outage or "")
    summary = probe.summarize(samples)
    report = {
        "unit": "M4-ws-7",
        "test_id": "availability-probe",
        "executed_at": clock.now().isoformat(),
        "environment": {
            "entry": "nginx(127.0.0.1:18000)・api1+api2+restart unless-stopped"
        },
        "input_conditions": {
            "duration_s": args.duration,
            "main_interval_s": probe.MAIN_INTERVAL_S,
            "dense_interval_s": probe.DENSE_INTERVAL_S,
            "subject": args.subject,
            "paths": list(probe.PROBE_PATHS),
            "marker": str(marker),
            "post_analysis_outages": [
                f"{s.isoformat()}..{e.isoformat()}" for s, e in outages
            ],
            "note": (
                "計画停止サンプルはマーカー存在でtagged・分母外(§2.1読み替え2)。"
                "api1強制停止(docker kill・restart unless-stopped自動復旧)と"
                "api2計画停止(stop→start)は§8手順どおり"
            ),
        },
        "verdict": summary,
        "samples": [
            {
                "kind": s.kind,
                "at": s.at.isoformat(),
                "path": s.path,
                "ok": s.ok,
                "error": s.error,
                "tagged_planned_outage": s.tagged_planned_outage,
            }
            for s in samples
        ],
    }
    print(
        f"probe done: main={summary['main_total']} "
        f"rate={summary['main_success_rate']} verdict={summary['verdict_995']}"
    )
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            yaml.safe_dump(report, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        print(f"evidence: {out_path}")
    else:
        path = collect.write_evidence(report, prefix="ws7-probe", clock=clock)
        print(f"evidence: {path}")
    return 0
```

`main()`のサブコマンド定義(`p_bf`の後)に追加:

```python
    p_probe = sub.add_parser("probe", help="合成監視プローブ(1分+10秒・YAML出力)")
    p_probe.add_argument("--duration", type=float, default=3600.0)
    p_probe.add_argument("--subject", default="load-u0001")
    p_probe.add_argument(
        "--planned-outage",
        default="",
        help='事後集計用の窓記録("ISO:ISO,ISO:ISO"。tagged本体はマーカー方式)',
    )
    p_probe.add_argument(
        "--outage-marker",
        default=None,
        help="実行中タグのマーカーファイル(既定=<repo>/.staging/probe-outage.marker)",
    )
    p_probe.add_argument("--out", default=None, help="証拠YAMLパス(既定はRESULTS_DIR)")
    p_probe.set_defaults(func=lambda a: asyncio.run(cmd_probe(a)))
```

- [ ] **Step 6: 試験を実行して通ることを確認する(Green)+既存無傷**

Run: `cd backend && uv run pytest tests/unit/test_loadgen_probe.py tests/unit/test_loadgen_schedule.py tests/unit/test_loadgen_collect.py -v && uv run python -m latch.loadgen probe --help`
Expected: PASS+probeの--help表示(引数一覧)

- [ ] **Step 7: コミット**

```bash
git add backend/src/latch/loadgen/probe.py backend/src/latch/loadgen/__main__.py backend/tests/unit/test_loadgen_probe.py
git commit -m "feat: 合成監視プローブ(main/dense・マーカータグ・token再発行)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 5: nfrtools(D-16カウンタ操作CLI)

**Files:**
- Create: `backend/src/latch/nfrtools/__init__.py`(空)・`backend/src/latch/nfrtools/d16.py`・`backend/src/latch/nfrtools/__main__.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/unit/test_nfrtools_d16.py`・`backend/tests/unit/test_nfr_pins.py`(1件追加)

**Interfaces:**
- Consumes: `Clock`(jst_date)・`redis.asyncio.Redis`(decode_responses=True)
- Produces: `daily_key(clock) -> str`(=`jev:daily:{yyyymmdd}`)・`monthly_key(clock) -> str`(=`jev:monthly:{yyyymm}`)・`async set_counter(redis, clock, scope: str, value: int) -> None`・`async get_counter(redis, clock, scope: str) -> int | None`・`async delete_counter(redis, clock, scope: str) -> int`。scopeは`"daily"`/`"monthly"`のみ(それ以外はValueError)。CLI: `latch-nfrtools d16 {get,set,delete} {daily,monthly} [value]`。Task 9(test_n3)は関数を直接importして消費する

- [ ] **Step 1: 失敗するunit試験を書く**

`backend/tests/unit/test_nfrtools_d16.py`を新規作成する:

```python
"""nfrtools.d16: Clock導出キー・set/get/delete(M4 ws-7・design §2.3案A)。

キー形式はworker/cost/store.pyと同一(jev:daily:{yyyymmdd}・jev:monthly:
{yyyymm})。バケット文字列はClock(JST)導出(guard._day_bucketと同一・C2)。
"""

from datetime import UTC, datetime

import pytest

from latch.core.clock import FakeClock
from latch.nfrtools.d16 import (
    daily_key,
    delete_counter,
    get_counter,
    monthly_key,
    set_counter,
)

NOW = datetime(2026, 10, 5, 9, 0, 0, tzinfo=UTC)  # JST 2026-10-05 18:00


@pytest.fixture
async def redis():
    import fakeredis.aioredis

    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


def test_daily_key_derived_from_clock():
    clock = FakeClock(NOW)
    assert daily_key(clock) == "jev:daily:20261005"


def test_monthly_key_derived_from_clock():
    clock = FakeClock(NOW)
    assert monthly_key(clock) == "jev:monthly:202610"


async def test_set_get_roundtrip(redis):
    clock = FakeClock(NOW)
    await set_counter(redis, clock, "daily", 23999)
    assert await get_counter(redis, clock, "daily") == 23999
    assert await get_counter(redis, clock, "monthly") is None


async def test_set_overwrites_and_sets_ttl(redis):
    clock = FakeClock(NOW)
    await set_counter(redis, clock, "daily", 100)
    await set_counter(redis, clock, "daily", 23999)
    assert await get_counter(redis, clock, "daily") == 23999
    ttl = await redis.ttl("jev:daily:20261005")
    assert 0 < ttl <= 48 * 3600
    await set_counter(redis, clock, "monthly", 479999)
    ttl = await redis.ttl("jev:monthly:202610")
    assert 0 < ttl <= 45 * 24 * 3600


async def test_delete_returns_count_and_missing_is_zero(redis):
    clock = FakeClock(NOW)
    await set_counter(redis, clock, "daily", 30000)
    assert await delete_counter(redis, clock, "daily") == 1
    assert await delete_counter(redis, clock, "daily") == 0
    assert await get_counter(redis, clock, "daily") is None


async def test_scope_validation():
    clock = FakeClock(NOW)
    import fakeredis.aioredis

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with pytest.raises(ValueError):
        await set_counter(redis, clock, "yearly", 1)
    await redis.aclose()


def test_cli_main_dispatch(capsys):
    """CLI配線(subprocess不要・run_*を直接呼ぶ)。"""
    from latch.nfrtools import __main__ as cli
    from latch.nfrtools.__main__ import run_set

    class _StubClock:
        def jst_date(self):
            return datetime(2026, 10, 5, tzinfo=UTC).date()

    class _StubRedis:
        def __init__(self):
            self.store: dict[str, str] = {}

        async def set(self, key, value, ex=None):
            self.store[key] = value
            return True

    r = _StubRedis()
    import asyncio

    asyncio.run(run_set(r, _StubClock(), "daily", "23999"))
    out = capsys.readouterr().out
    assert "jev:daily:20261005" in out
    assert "23999" in out
    assert cli  # CLIモジュールのmain/build_parser存在


def test_cli_build_parser_has_three_operations():
    from latch.nfrtools.__main__ import build_parser

    parser = build_parser()
    args = parser.parse_args(["d16", "set", "monthly", "479999"])
    assert args.command == "d16"
    assert args.operation == "set"
    assert args.scope == "monthly"
    assert args.value == "479999"
```

- [ ] **Step 2: 試験を実行して失敗を確認する(Red)**

Run: `cd backend && uv run pytest tests/unit/test_nfrtools_d16.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'latch.nfrtools'"

- [ ] **Step 3: nfrtoolsを実装する**

`backend/src/latch/nfrtools/__init__.py`(空ファイル・docstringのみ):

```python
"""staging非機能試験の運用CLIパッケージ(M4 ws-7・design §3.1)。"""
```

`backend/src/latch/nfrtools/d16.py`:

```python
"""D-16カウンタ操作(M4 ws-7・design §2.3案A)。

対象キーはworker/cost/store.pyと同一形式(jev:daily:{yyyymmdd} TTL 48時間・
jev:monthly:{yyyymm} TTL 45日)。バケット文字列はClock(JST)から導出する
(guard._day_bucketと同一・C2)。SETは値の上書き+TTL再設定・GETは不在None・
DELETEはキー削除。境界試験(alert 24,000・上限30,000・月次480,000)の母体。
実Event投入(publish updated)と組み合わせてalert発報と上限到達を通す。
"""

from __future__ import annotations

from latch.core.clock import Clock

DAY_TTL_S = 48 * 3600
MONTH_TTL_S = 45 * 24 * 3600


def daily_key(clock: Clock) -> str:
    return f"jev:daily:{clock.jst_date().strftime('%Y%m%d')}"


def monthly_key(clock: Clock) -> str:
    return f"jev:monthly:{clock.jst_date().strftime('%Y%m')}"


def _key_for(clock: Clock, scope: str) -> str:
    if scope == "daily":
        return daily_key(clock)
    if scope == "monthly":
        return monthly_key(clock)
    raise ValueError(f"unknown scope: {scope!r} (daily/monthly)")


async def set_counter(redis, clock: Clock, scope: str, value: int) -> None:
    """カウンタ上書き+TTL再設定(workerの値と同一TTL・掃除用)。"""
    key = _key_for(clock, scope)
    ttl = DAY_TTL_S if scope == "daily" else MONTH_TTL_S
    await redis.set(key, str(value), ex=ttl)


async def get_counter(redis, clock: Clock, scope: str) -> int | None:
    raw = await redis.get(_key_for(clock, scope))
    return int(raw) if raw is not None else None


async def delete_counter(redis, clock: Clock, scope: str) -> int:
    return int(await redis.delete(_key_for(clock, scope)))
```

`backend/src/latch/nfrtools/__main__.py`:

```python
"""staging非機能試験の運用CLI(M4 ws-7・design §3.1)。

latch-nfrtools d16 get daily           # 現在のJST日付キーの値を表示
latch-nfrtools d16 set daily 23999     # 日次カウンタを上書き
latch-nfrtools d16 delete monthly      # 月次キーを削除

宛先RedisはLATCH_REDIS_URL(Settings・env上書き可。Makefileのstaging-d16が
staging宛16379を設定)。実時間の表示はSystemClock経由(clock_cliと同一作法)。
"""

from __future__ import annotations

import argparse

from latch.core.clock import ControlledClock, SystemClock
from latch.nfrtools.d16 import delete_counter, get_counter, set_counter
from latch.settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latch-nfrtools",
        description="staging非機能試験の運用CLI(M4 ws-7)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    d16 = sub.add_parser("d16", help="D-16カウンタ操作(jev:daily/jev:monthly)")
    op = d16.add_subparsers(dest="operation", required=True)
    p_get = op.add_parser("get", help="値を表示(不在はNone)")
    p_get.add_argument("scope", choices=["daily", "monthly"])
    p_set = op.add_parser("set", help="値を上書き+TTL再設定")
    p_set.add_argument("scope", choices=["daily", "monthly"])
    p_set.add_argument("value", type=int)
    p_del = op.add_parser("delete", help="キーを削除")
    p_del.add_argument("scope", choices=["daily", "monthly"])
    return parser


def _build():
    """Settings.redis_url宛の同期起点・非同期RedisとControlledClock。"""
    import redis.asyncio as aioredis

    settings = Settings()
    client = aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
    import redis as redis_sync

    sync_client = redis_sync.Redis.from_url(settings.redis_url, decode_responses=True)
    return client, ControlledClock(sync_client)


async def run_get(redis, clock, scope: str) -> int:
    value = await get_counter(redis, clock, scope)
    key = f"{scope}:{clock.jst_date().strftime('%Y%m%d')}"
    print(f"[d16] {key} = {value}")
    return 0


async def run_set(redis, clock, scope: str, value: str) -> int:
    await set_counter(redis, clock, scope, int(value))
    print(
        f"[d16] set {scope} -> {value} "
        f"(key=daily/monthly by clock {clock.jst_date().isoformat()})"
    )
    return 0


async def run_delete(redis, clock, scope: str) -> int:
    n = await delete_counter(redis, clock, scope)
    print(f"[d16] deleted {scope} ({n} key)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    redis, clock = _build()

    async def dispatch():
        try:
            if args.operation == "get":
                return await run_get(redis, clock, args.scope)
            if args.operation == "set":
                return await run_set(redis, clock, args.scope, args.value)
            return await run_delete(redis, clock, args.scope)
        finally:
            await redis.aclose()

    import asyncio

    return asyncio.run(dispatch())


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: pyprojectへエントリを追加し・collectで収集するピンを足す**

`backend/pyproject.toml`へ`[dependency-groups]`の直前に追加:

```toml
[project.scripts]
latch-nfrtools = "latch.nfrtools.__main__:main"
```

`backend/tests/unit/test_nfr_pins.py`へ追加:

```python
def test_pyproject_has_nfrtools_entry():
    text = (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    assert '[project.scripts]' in text
    assert 'latch-nfrtools = "latch.nfrtools.__main__:main"' in text
```

- [ ] **Step 5: `uv sync`を再実行する(スクリプト生成・依存無変更)**

Run: `make setup`
Expected: Resolved N packages(依存の追加なし・uv.lockのdependencies差分なし)

- [ ] **Step 6: 試験を実行して通ることを確認する(Green)+CLI確認**

Run: `cd backend && uv run pytest tests/unit/test_nfrtools_d16.py tests/unit/test_nfr_pins.py -v && uv run latch-nfrtools --help && uv run python -m latch.nfrtools --help`
Expected: PASS+両経路でhelp表示

- [ ] **Step 7: コミット**

```bash
git add backend/src/latch/nfrtools backend/tests/unit/test_nfrtools_d16.py backend/tests/unit/test_nfr_pins.py backend/pyproject.toml uv.lock
git commit -m "feat: nfrtools d16 CLI(D-16カウンタ操作・Clock導出キー)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 6: compose override 2本+Makefile 9ターゲット

**Files:**
- Create: `compose.staging.nfr.yaml`・`compose.staging.recovery.yaml`
- Modify: `Makefile`
- Test: `backend/tests/unit/test_nfr_pins.py`(4件追加)

**Interfaces:**
- Consumes: compose.staging.yaml(既存・無変更)・settings(env名・Task 1)
- Produces: `STAGING_NFR_COMPOSE`起動構成(lag alert有効)・`RECOVERY_COMPOSE`起動構成(db2+切替)・Makefileターゲット9本。§8実施手順が消費する

- [ ] **Step 1: 失敗するピンを書く**

`backend/tests/unit/test_nfr_pins.py`へ追加:

```python
NFR_COMPOSE = yaml.safe_load(
    (ROOT / "compose.staging.nfr.yaml").read_text(encoding="utf-8")
)


def test_nfr_compose_enables_lag_alert_on_both_apis():
    """lag alertの有効化はoverrideのみ(compose.staging.yamlは無傷)。"""
    for svc in ("api1", "api2"):
        assert NFR_COMPOSE["services"][svc]["environment"][
            "LATCH_ALERT_QUEUE_ENABLED"
        ] == "true", svc


def test_compose_staging_untouched_by_nfr():
    """既定環境にalert envが漏れていないこと+既定envの無傷ピン。"""
    staging = yaml.safe_load(
        (ROOT / "compose.staging.yaml").read_text(encoding="utf-8")
    )
    for svc in ("api1", "api2", "worker1", "worker2"):
        env = staging["services"][svc]["environment"]
        assert "LATCH_ALERT_QUEUE_ENABLED" not in env, svc
        assert env["LATCH_DATABASE_URL"].endswith("@db:5432/latch"), svc
    # nginx・db・redis・pubsubの既定は無変更(ports既存値)
    assert staging["services"]["nginx"]["ports"] == ["127.0.0.1:18000:80"]
    assert staging["services"]["db"]["ports"] == ["127.0.0.1:15432:5432"]


def test_recovery_compose_defines_db2_with_profile_and_loopback_port():
    recovery = yaml.safe_load(
        (ROOT / "compose.staging.recovery.yaml").read_text(encoding="utf-8")
    )
    db2 = recovery["services"]["db2"]
    assert db2["container_name"] == "latch-staging-db2-1"
    assert db2["profiles"] == ["recovery"]  # --profile recovery指定時のみ起動
    assert db2["ports"] == ["127.0.0.1:16543:5432"]
    assert "latch-staging-db2:/var/lib/postgresql/data" in db2["volumes"]
    assert recovery["volumes"] == {"latch-staging-db2": None}
    assert db2["build"] == {"context": "docker/postgres"}


def test_recovery_compose_switches_db_and_speeds_backfill():
    """api/workerのDB向き先差し替え+backfill周期短縮(既存envの上書きのみ)。"""
    recovery = yaml.safe_load(
        (ROOT / "compose.staging.recovery.yaml").read_text(encoding="utf-8")
    )
    for svc in ("api1", "api2", "worker1", "worker2"):
        env = recovery["services"][svc]["environment"]
        assert env["LATCH_DATABASE_URL"] == (
            "postgresql+asyncpg://latch:latch@db2:5432/latch"
        ), svc
        assert env["LATCH_EMBEDDING_BACKFILL_INTERVAL_SEC"] == "5", svc


def test_makefile_has_nfr_targets():
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in (
        "staging-nfr-reset:",
        "staging-nfr-test:",
        "staging-probe:",
        "staging-d16:",
        "staging-dump:",
        "staging-db2-up:",
        "staging-restore:",
        "staging-db2-switch:",
        "staging-db2-down:",
    ):
        assert target in mk, target
    phony = mk.split(".PHONY:")[1].split("\n")[0]
    for name in (
        "staging-nfr-reset",
        "staging-nfr-test",
        "staging-probe",
        "staging-d16",
        "staging-dump",
        "staging-db2-up",
        "staging-restore",
        "staging-db2-switch",
        "staging-db2-down",
    ):
        assert name in phony, name
```

- [ ] **Step 2: 試験を実行して失敗を確認する(Red)**

Run: `cd backend && uv run pytest tests/unit/test_nfr_pins.py -v`
Expected: FAIL(新規5件がassert失敗・overrideファイル不在)

- [ ] **Step 3: compose.staging.nfr.yamlを作る**

```yaml
# staging NFR試験用override(M4 ws-7)。compose.staging.yamlは触らず・起動時の
# env上書きのみ(design §2.5案A・§3.2)。Queue lag alert発報器はAPI側新設タスク
# で既定無効(settings.alert_queue_enabled=False・ci無効維持)のため・stagingのみ
# 有効化する。n1〜n5の全試験は -f compose.staging.yaml -f compose.staging.nfr.yaml
# で立ち上げた構成に対して実施する(Makefileのstaging-nfr-reset / NFR系ターゲット)。
services:
  api1:
    environment:
      LATCH_ALERT_QUEUE_ENABLED: "true"
  api2:
    environment:
      LATCH_ALERT_QUEUE_ENABLED: "true"
```

- [ ] **Step 4: compose.staging.recovery.yamlを作る**

```yaml
# staging復旧試験用override(M4 ws-7・design §2.2案A)。n2の期間のみ
# -f compose.staging.yaml -f compose.staging.nfr.yaml -f compose.staging.recovery.yaml
# で読む。db2=pg_dump復元先(新規ボリューム・別ポート16543)・api/workerのDB向き先を
# db2へ差し替え・backfill周期を試験用に短縮(既存envの上書きのみ=design §2.2案A
# 「compose.staging.yamlは触らず、起動時のenv上書きのみ」の範囲内)。
# db2はprofile recovery指定時のみ起動(staging-db2-upターゲット・profiles指定で
# 意図しない起動を構造的に防止)。
services:
  db2:
    build:
      context: docker/postgres
    container_name: latch-staging-db2-1
    profiles:
      - "recovery"
    environment:
      POSTGRES_USER: latch
      POSTGRES_PASSWORD: latch
      POSTGRES_DB: latch
    ports:
      - "127.0.0.1:16543:5432"
    volumes:
      - latch-staging-db2:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U latch -d latch"]
      interval: 5s
      timeout: 3s
      retries: 12
    restart: unless-stopped

  api1:
    environment:
      LATCH_DATABASE_URL: postgresql+asyncpg://latch:latch@db2:5432/latch
      LATCH_EMBEDDING_BACKFILL_INTERVAL_SEC: "5"
  api2:
    environment:
      LATCH_DATABASE_URL: postgresql+asyncpg://latch:latch@db2:5432/latch
      LATCH_EMBEDDING_BACKFILL_INTERVAL_SEC: "5"
  worker1:
    environment:
      LATCH_DATABASE_URL: postgresql+asyncpg://latch:latch@db2:5432/latch
      LATCH_EMBEDDING_BACKFILL_INTERVAL_SEC: "5"
  worker2:
    environment:
      LATCH_DATABASE_URL: postgresql+asyncpg://latch:latch@db2:5432/latch
      LATCH_EMBEDDING_BACKFILL_INTERVAL_SEC: "5"

volumes:
  latch-staging-db2:
```

- [ ] **Step 5: Makefileへ9ターゲットを追加する**

`.PHONY`行のstaging-load系の末尾(`load-geo-import`の後)へ`staging-nfr-reset staging-nfr-test staging-probe staging-d16 staging-dump staging-db2-up staging-restore staging-db2-switch staging-db2-down`を追加する。定数部(`STAGING_DB_URL`の後)へ追加:

```makefile
STAGING_NFR_COMPOSE = docker compose -f compose.staging.yaml -f compose.staging.nfr.yaml
RECOVERY_COMPOSE = docker compose -f compose.staging.yaml -f compose.staging.nfr.yaml -f compose.staging.recovery.yaml
STAGING_BACKUP_DIR = .staging/backup
NFR_FILES = staging_tests_nfr/test_n5_queue.py staging_tests_nfr/test_n4_reeval.py staging_tests_nfr/test_n3_d16.py staging_tests_nfr/test_n2_prep.py staging_tests_nfr/test_n2_backup.py staging_tests_nfr/test_n1_availability.py
```

ターゲット群(`staging-reset`ターゲットの直後に挿入):

```makefile
staging-nfr-reset: ## NFR試験用フルリセット(staging-reset+nfr overrideでapiを再作成=lag alert有効。ws-7手順の前提)
	$(MAKE) staging-reset
	$(STAGING_NFR_COMPOSE) up -d --force-recreate --wait api1 api2

staging-nfr-test: ## ws-7 NFRハーネス(順序固定 n5→n4→n3→n2→n1。部分実行は make staging-nfr-test NFR_FILES="staging_tests_nfr/test_n1_availability.py")
	cd backend && uv run pytest $(NFR_FILES) --import-mode=importlib

staging-probe: ## 合成監視プローブ(60分・main1分+dense10秒。バックグラウンドは手順§8のnohup)
	cd backend && uv run python -m latch.loadgen --base-url http://127.0.0.1:18000 --keys ../.staging/keys probe $(ARGS)

staging-d16: ## staging RedisのD-16カウンタ操作(make staging-d16 ARGS="d16 set daily 23999")
	cd backend && LATCH_REDIS_URL=redis://127.0.0.1:16379/0 uv run latch-nfrtools $(ARGS)

staging-dump: ## staging DBのpg_dumpを.staging/backup/へ(定期バックアップ1サイクルの読み替え・10 §4.4)
	mkdir -p $(STAGING_BACKUP_DIR)
	docker exec latch-staging-db-1 pg_dump -U latch -Fc latch > $(STAGING_BACKUP_DIR)/latch-staging.dump

staging-db2-up: ## 復旧試験用db2を起動(16543・新規ボリュームlatch-staging-db2)
	$(RECOVERY_COMPOSE) --profile recovery up -d --wait db2

staging-restore: ## db2へpg_restore(dumpファイルをdocker cpして復元)
	docker cp $(STAGING_BACKUP_DIR)/latch-staging.dump latch-staging-db2-1:/tmp/restore.dump
	docker exec latch-staging-db2-1 pg_restore -U latch -d latch --no-owner /tmp/restore.dump

staging-db2-switch: ## api/workerをdb2向きへ再作成(切替。nginx/db/redis/pubsubは触らない)
	$(RECOVERY_COMPOSE) up -d --force-recreate --wait api1 api2 worker1 worker2

staging-db2-down: ## db2を停止・削除(ボリュームlatch-staging-db2は残す=証拠確保)
	$(RECOVERY_COMPOSE) --profile recovery rm -sf db2
```

- [ ] **Step 6: 試験を実行して通ることを確認する(Green)+compose設定の妥当性確認+既存無傷**

Run: `docker compose -f compose.staging.yaml -f compose.staging.nfr.yaml config --quiet && echo NFR_OK; docker compose -f compose.staging.yaml -f compose.staging.nfr.yaml -f compose.staging.recovery.yaml --profile recovery config --quiet && echo RECOVERY_OK`
Expected: `NFR_OK`と`RECOVERY_OK`が表示される(config検査のみ・起動はしない)

Run: `cd backend && uv run pytest tests/unit/test_nfr_pins.py tests/integration/test_compose_ports.py --collect-only -q && uv run pytest tests/unit/test_nfr_pins.py -v`
Expected: 収集エラーなし+PASS

- [ ] **Step 7: コミット**

```bash
git add compose.staging.nfr.yaml compose.staging.recovery.yaml Makefile backend/tests/unit/test_nfr_pins.py
git commit -m "feat: staging NFR/recovery override+Makefile 9ターゲット(ws-7)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 7: staging_tests_nfr土台+test_n5_queue.py(Queue障害)

**Files:**
- Create: `backend/staging_tests_nfr/conftest.py`・`backend/staging_tests_nfr/helpers_nfr.py`・`backend/staging_tests_nfr/test_n5_queue.py`

**Interfaces:**
- Consumes: staging環境(staging-nfr-reset後のフル起動・既定DB 15432/Redis 16379/PubSub 18085/nginx 18000)・docs/testassets/m4-seed.yaml(ws-2生成・300件)・`.staging/keys`(staging鍵)・nfrtools.d16(Task 5・n3で消費)
- Produces(conftest・ハーネス全体で共用): `REPO_ROOT`・`API_BASE`(18000)・`DSN`(15432・SELECT専用)・`RECOVERY_DSN`(16543・RW)・`REDIS_URL`(16379)・`KEYS_DIR`・`SEED_PATH`・`RESULTS_DIR`・`EXPECTED_HEAD="0008"`・`record_item(*, item_id, name, test, verdict, observed)`・fixtures `api`/`db`/`db_recovery_rw`・`get_clock()`・`issue_token(subject)`・`auth_header(subject)`・`compose(*args)`・`workers_stop()`/`workers_start()`・`docker_logs(container)`・`publish_event(event_type, intent_id, *, version=None, raw_payload=None)`・`connect_rw(dsn)`。helpers_nfr: `wait_until`・`ensure_env_prerequisites`・`ensure_seed`・`intent_payload`・`create_user_nfr`・`create_intent_nfr`・`wait_events_done`・`wait_candidates_stable`・`current_version`。Task 8〜11の全テストが消費する(※`redis_nfr` fixtureはTask 9で追加)

- [ ] **Step 1: conftest.pyを書く**

`backend/staging_tests_nfr/conftest.py`を新規作成する:

```python
"""ws-7 NFRハーネス共通(M4 ws-7・ws-3ハーネス型の踏襲)。

tests/配下でもstaging_tests/でもない独立ディレクトリ(pyproject testpaths外)
のため既存のmake test/test-ciからは収集されない。実行: make staging-nfr-test
(順序固定 n5→n4→n3→n2→n1・部分実行はNFR_FILES)。実行前提はmake
staging-nfr-resetからのフル起動(compose.staging.nfr.yaml併用=lag alert有効)。
証拠YAMLは本conftestのsessionfinishのみが出力する(10 §5形式・実行ごとに
ws7-nfr-result-<ts>.yaml)。session状態はモジュール辞書+ヘルパー関数で持つ
(sessionスコープのasync fixtureを使わない — pytest-asyncioのloop scope結合
を避ける・ws-3と同一作法)。staging_testsのhelpersはimportしない(独立完結
のためobs側のsys.pathハックは不要)。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import asyncpg
import httpx
import pytest
import yaml

from latch.auth.tools import issue_idp_token
from latch.core.clock import ControlledClock

REPO_ROOT = Path(__file__).resolve().parents[2]  # backend/staging_tests_nfr → repoルート
API_BASE = "http://127.0.0.1:18000"  # nginx入口
DSN = "postgresql://latch:latch@127.0.0.1:15432/latch"  # 既定DB(SELECT専用)
RECOVERY_DSN = "postgresql://latch:latch@127.0.0.1:16543/latch"  # db2(RW可)
REDIS_URL = "redis://127.0.0.1:16379/0"
KEYS_DIR = REPO_ROOT / ".staging" / "keys"
SEED_PATH = REPO_ROOT / "docs" / "testassets" / "m4-seed.yaml"
RESULTS_DIR = REPO_ROOT / "docs" / "testassets" / "results"
EXPECTED_HEAD = "0008"
COMPOSE_BASE = ("docker", "compose", "-f", "compose.staging.yaml",
                "-f", "compose.staging.nfr.yaml")
WORKER_CONTAINERS = ("latch-staging-worker1-1", "latch-staging-worker2-1")
API_CONTAINERS = ("latch-staging-api1-1", "latch-staging-api2-1")

# --- 証拠収集(10 §5の形式・sessionfinishでYAML出力) ---

_results: list[dict] = []


def record_item(
    *, item_id: str, name: str, test: str, verdict: str, observed: dict
) -> None:
    """証拠1件を収集する(試験ID・入力条件・合否・観測値)。"""
    _results.append(
        {
            "id": item_id,
            "name": name,
            "test": test,
            "verdict": verdict,
            "observed": observed,
        }
    )


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001
    """session終了時に証拠YAMLを出力する(失敗時のitemsも含めて出す)。"""
    from datetime import datetime, timedelta, timezone

    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "unit": "M4-ws-7",
        "executed_at": now.isoformat(),
        "environment": "latch-staging(nfr override)",
        "items": list(_results),
    }
    ts = now.strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"ws7-nfr-result-{ts}.yaml"
    path.write_text(
        yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


# --- fixtures ---

@pytest.fixture
async def api():
    async with httpx.AsyncClient(base_url=API_BASE, timeout=30.0) as c:
        yield c


@pytest.fixture
async def db():
    conn = await asyncpg.connect(DSN)
    # jsonbはstrで返りうる(asyncpg・コードベース規約)ためクライアント側codec
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    await conn.execute("SET default_transaction_read_only = on")
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def db_recovery_rw():
    """n2専用: db2(16543)へのRW接続(embedding NULL化UPDATEで使う)。"""
    conn = await asyncpg.connect(RECOVERY_DSN)
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    try:
        yield conn
    finally:
        await conn.close()


# --- 共通ヘルパ ---

_clock = None


def get_clock() -> ControlledClock:
    """staging Redis基準の共有制御Clock(clock_cliと同一コード経路)。"""
    global _clock
    if _clock is None:
        import redis as redis_sync

        _clock = ControlledClock(
            redis_sync.Redis.from_url(REDIS_URL, decode_responses=True)
        )
    return _clock


def issue_token(subject: str) -> str:
    """staging鍵でIdP JWTを発行し/v1/auth/tokenでaccess_tokenへ交換する。

    ws-3ハーネスと同一経路。仮想Clock前進対策の30日IdPトークンも同一。
    """
    pem = (KEYS_DIR / "idp_test_private.pem").read_bytes()
    idp_token = issue_idp_token(
        provider="google",
        subject=subject,
        private_key_pem=pem,
        expires_in=30 * 24 * 3600,
    )
    r = httpx.post(
        f"{API_BASE}/v1/auth/token",
        json={"provider": "google", "idp_token": idp_token},
        timeout=30.0,
    )
    if r.status_code != 200:
        raise AssertionError(f"auth/token交換失敗 {subject}: {r.status_code} {r.text}")
    return r.json()["access_token"]


def auth_header(subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(subject)}"}


def compose(*args: str) -> str:
    """staging NFR構成のcomposeコマンド実行(cwd=リポジトリルート)。"""
    r = subprocess.run(
        [*COMPOSE_BASE, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise AssertionError(
            f"compose {' '.join(args)} failed: rc={r.returncode} {r.stderr[:500]}"
        )
    return r.stdout


def workers_stop() -> None:
    compose("stop", "worker1", "worker2")


def workers_start() -> None:
    compose("start", "worker1", "worker2")


def docker_logs(container: str) -> str:
    """コンテナの全ログを取得する(発報検査用・パイプ禁止規律はPython内照合)。"""
    r = subprocess.run(
        ["docker", "logs", container],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout + r.stderr


def publish_event(
    event_type: str,
    intent_id: str,
    *,
    version: int | None = None,
    raw_payload: str | None = None,
) -> None:
    """Match Eventを手動発行する(make staging-publishと同一経路・3点組)。"""
    cmd = [
        "uv", "run", "python", "-m", "latch.events", "publish", event_type,
        "--intent-id", str(intent_id),
    ]
    if version is not None:
        cmd += ["--version", str(version)]
    if raw_payload is not None:
        cmd += ["--raw-payload", raw_payload]
    import os

    env = dict(os.environ)
    env["LATCH_PUBSUB_EMULATOR_HOST"] = "127.0.0.1:18085"
    env["LATCH_PUBSUB_PROJECT_ID"] = "latch-staging"
    r = subprocess.run(
        cmd,
        cwd=REPO_ROOT / "backend",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise AssertionError(
            f"publish {event_type} intent={intent_id} 失敗: rc={r.returncode} {r.stderr}"
        )
```

- [ ] **Step 2: helpers_nfr.pyを書く**

`backend/staging_tests_nfr/helpers_nfr.py`を新規作成する:

```python
"""ws-7ハーネスの共通ヘルパ(べき等シード・ペア作成・収束待ち)。

投入順序はusers→consent→intents(ws-5同意必須)。仮想Clockはシードbaseへset
する(ws-3ハーネスと同一の適応: 実行日がbase以降でも、Redis不在(down -v
直後)からのsetは制御開始であり・以後の判定はbase基準で決定的になる)。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta

import yaml

from conftest import (
    EXPECTED_HEAD,
    KEYS_DIR,
    SEED_PATH,
    auth_header,
    get_clock,
)

JST = timedelta(hours=9)
_PROBE_PLACE = "天文館"  # 対配置Intentの地名(ジオコーディング成立・ws-2語彙)


async def wait_until(
    cond,
    *,
    timeout_s: float,
    interval_s: float = 5.0,
    what: str = "condition",
) -> None:
    """async条件が成立するまでinterval_s間隔でポーリングする(上限で失敗)。"""
    deadline = time.monotonic() + timeout_s
    while True:
        if await cond():
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"タイムアウト({timeout_s}s): {what}")
        await asyncio.sleep(interval_s)


async def ensure_env_prerequisites(db) -> None:  # noqa: ANN001
    """環境前提の名前つき失敗(原因不明の連鎖失敗を防ぐ)。"""
    ver = await db.fetchval("SELECT version_num FROM alembic_version")
    if ver != EXPECTED_HEAD:
        raise AssertionError(
            f"alembic headが{EXPECTED_HEAD}ではありません(実際={ver})。"
            "make staging-nfr-resetを再実行してください"
        )
    n_geo = await db.fetchval("SELECT count(*) FROM geofeatures")
    if not n_geo:
        raise AssertionError("geofeaturesが空です。staging-geo-importを実行")
    if not (KEYS_DIR / "idp_test_private.pem").exists():
        raise AssertionError(".staging/keysがありません。make staging-keysを実行")


def load_seed() -> dict:
    return yaml.safe_load(SEED_PATH.read_text(encoding="utf-8"))


def _seed_intent_payload(i: dict) -> dict:
    """POST /v1/intentsのbody(ws-3 helpers_seedと同型の組立)。"""
    s = i["structured"]
    time_part: dict = {"start": s["time"]["start"]}
    if s["time"].get("end"):
        time_part["end"] = s["time"]["end"]
    location_part: dict = {"name": s["location"]["name"]}
    if s["location"].get("radius_m"):
        location_part["radius_m"] = s["location"]["radius_m"]
    budget_part = None
    if s.get("budget", {}).get("max") is not None:
        budget_part = {"max": s["budget"]["max"]}
    return {
        "raw_text": i["raw_text"],
        "status": "active",
        "structured_intent": {
            "category": {
                "primary": s["category"]["primary"],
                "secondary": s["category"].get("secondary"),
            },
            "alcohol_involved": s["alcohol_involved"],
            "time": time_part,
            "location": location_part,
            "budget": budget_part,
            "participants": {
                "min": s["participants"]["min"],
                "max": s["participants"]["max"],
            },
            "soft_constraints": s.get("soft_constraints") or [],
            "visibility": s["visibility"],
            "notification_level": s["notification_level"],
        },
    }


async def _pending_events(db) -> int:  # noqa: ANN001
    return await db.fetchval(
        "SELECT count(*) FROM match_events WHERE status='pending'"
    )


async def _evaluated_count(db) -> int:  # noqa: ANN001
    return await db.fetchval(
        "SELECT count(*) FROM match_candidates WHERE jev_result IS NOT NULL"
    )


async def wait_events_done(db, *, timeout_s: float = 240) -> None:  # noqa: ANN001
    await wait_until(
        lambda: _pending_events(db) == 0,
        timeout_s=timeout_s,
        interval_s=5,
        what="Event処理完了(pending=0・debounce 10秒+worker処理込み)",
    )


async def wait_candidates_stable(
    db, *, timeout_s: float = 150, interval_s: float = 10
) -> None:  # noqa: ANN001
    """候補評価数が10秒間隔で2回連続不変になるまで待つ(ws-3と同型)。"""
    prev = await _evaluated_count(db)
    deadline = time.monotonic() + timeout_s
    while True:
        if time.monotonic() > deadline:
            raise AssertionError(f"タイムアウト({timeout_s}s): 候補評価の安定")
        await asyncio.sleep(interval_s)
        cur = await _evaluated_count(db)
        if cur == prev:
            return
        prev = cur


async def wait_pipeline_settled(db, *, timeout_s: float = 240) -> None:  # noqa: ANN001
    await wait_events_done(db, timeout_s=timeout_s)
    await wait_candidates_stable(db)


async def ensure_seed(api, db) -> dict:  # noqa: ANN001
    """シード投入(べき等・design §4.3手順1の器)。

    users行がシード人数以上ならスキップ(部分実行での再投入防止)。無ければ
    ws-3 helpers_seed.seed_allと同一経路(users→consent→intents)で投入し、
    仮想Clockをシードbaseへsetする。
    """
    await ensure_env_prerequisites(db)
    seed = load_seed()
    n_users = await db.fetchval("SELECT count(*) FROM users")
    if n_users >= len(seed["users"]):
        return {"skipped": True, "users": n_users, "intents": None}

    from datetime import timezone

    clock = get_clock()
    clock.set(datetime.fromisoformat(seed["meta"]["base_datetime"]))
    # ↑baseが実時間より過去でも、down -v直後のRedis不在からのset=制御開始
    #  (オフセット作成)であり後退ではない。以後の判定はbase基準で決定的

    for u in seed["users"]:
        body = {
            "display_name": u["display_name"],
            "birth_date": u["birth_date"],
            "profile": {"bio": (u.get("profile") or {}).get("bio")},
        }
        r = await api.post("/v1/users", headers=auth_header(u["id"]), json=body)
        assert r.status_code == 201, (
            f"users投入失敗 {u['id']}: {r.status_code} {r.text}"
        )
        rc = await api.post("/v1/users/me/consent", headers=auth_header(u["id"]))
        assert rc.status_code == 200, f"consent失敗 {u['id']}: {rc.status_code}"

    su_to_uuid: dict[str, str] = {}
    for i in seed["intents"]:
        r = await api.post(
            "/v1/intents",
            headers=auth_header(i["user"]),
            json=_seed_intent_payload(i),
        )
        assert r.status_code == 201, (
            f"intents投入失敗 {i['id']}: {r.status_code} {r.text}"
        )
        su_to_uuid[i["id"]] = r.json()["intent"]["id"]

    await wait_pipeline_settled(db)
    return {"skipped": False, "users": len(seed["users"]), "intents": su_to_uuid}


def intent_payload(*, seq: int, now: datetime, start_hours: float = 3.0) -> dict:
    """対配置Intentのペイロード(同地名・同カテゴリ・stub Jevで提案成立)。"""
    start = now + timedelta(hours=start_hours)
    return {
        "raw_text": f"ws7試験{seq}: {_PROBE_PLACE}あたりで飲みながら話したい",
        "status": "active",
        "structured_intent": {
            "category": {"primary": "drinking", "secondary": None},
            "alcohol_involved": True,
            "time": {
                "start": start.isoformat(),
                "end": (start + timedelta(hours=2)).isoformat(),
            },
            "location": {"name": _PROBE_PLACE},
            "budget": {"max": 3000},
            "participants": {"min": 2, "max": 4},
            "soft_constraints": [],
            "visibility": "hidden_until_match",
            "notification_level": "proposals_only",
        },
    }


async def create_user_nfr(api, subject: str) -> str:  # noqa: ANN001
    """users作成+consent(既存は409許容=べき等)。user idを返す。"""
    body = {
        "display_name": f"ws7-{subject}",
        "birth_date": "1990-01-01",
        "profile": {"bio": "ws-7 NFR試験用"},
    }
    r = await api.post("/v1/users", headers=auth_header(subject), json=body)
    assert r.status_code in (201, 409), f"users {subject}: {r.status_code} {r.text}"
    if r.status_code == 201:
        user_id = r.json()["user"]["id"]
    else:
        r = await api.get("/v1/users/me", headers=auth_header(subject))
        assert r.status_code == 200
        user_id = r.json()["user"]["id"]
    rc = await api.post("/v1/users/me/consent", headers=auth_header(subject))
    assert rc.status_code == 200, f"consent {subject}: {rc.status_code}"
    return user_id


async def create_intent_nfr(api, subject: str, payload: dict) -> str:  # noqa: ANN001
    """POST /v1/intents(active)・intent idを返す。"""
    r = await api.post("/v1/intents", headers=auth_header(subject), json=payload)
    assert r.status_code == 201, f"intents {subject}: {r.status_code} {r.text}"
    return r.json()["intent"]["id"]


async def create_pair_nfr(
    api, db, *, prefix: str, seq: int, start_hours: float = 3.0
) -> dict:  # noqa: ANN001
    """対配置ペア(別ユーザー2人×Intent 2件)を作り収束待ちする。

    stub Jev固定値(compose.staging.yaml env・noul=0.90/score=3.6)で提案が
    成立する条件。ユーザーsubjectはws7-{prefix}a/b・返り値はintent uuidと
    現行version(=1)。
    """
    now = get_clock().now()
    subject_a, subject_b = f"ws7-{prefix}a", f"ws7-{prefix}b"
    await create_user_nfr(api, subject_a)
    await create_user_nfr(api, subject_b)
    intent_a = await create_intent_nfr(
        api, subject_a, intent_payload(seq=seq, now=now, start_hours=start_hours)
    )
    intent_b = await create_intent_nfr(
        api, subject_b, intent_payload(seq=seq + 1, now=now, start_hours=start_hours)
    )
    await wait_pipeline_settled(db)
    return {
        "subject_a": subject_a,
        "subject_b": subject_b,
        "intent_a": intent_a,
        "intent_b": intent_b,
        "version_a": 1,
        "version_b": 1,
    }


async def current_version(db, intent_id: str) -> int:  # noqa: ANN001
    v = await db.fetchval(
        "SELECT version FROM intents WHERE id=$1", uuid.UUID(intent_id)
    )
    assert v is not None, f"intent {intent_id} が見つかりません"
    return int(v)
```

- [ ] **Step 3: test_n5_queue.pyを書く**

`backend/staging_tests_nfr/test_n5_queue.py`を新規作成する:

```python
"""n5: Queue障害(10 §4.7)。毒ペイロード・参照不在・UNIQUE重複・非波及・lag alert。

実行前提: staging-nfr-reset直後のフル起動(lag alert有効)+ci一時停止。
worker停止を伴う試験は必ずtry/finallyでworkers_start()へ戻す(Review Focus 5)。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

from conftest import (
    API_CONTAINERS,
    auth_header,
    docker_logs,
    get_clock,
    publish_event,
    record_item,
    workers_start,
    workers_stop,
)
from helpers_nfr import (
    create_intent_nfr,
    create_pair_nfr,
    create_user_nfr,
    ensure_env_prerequisites,
    ensure_seed,
    intent_payload,
    wait_events_done,
    wait_until,
)

NIL_UUID = "00000000-0000-0000-0000-000000000000"


def _api_log_matches(word: str) -> int:
    """api1/api2の両コンテナログ合算でのword出現件数(単一コマンド照合)。"""
    logs = "".join(docker_logs(c) for c in API_CONTAINERS)
    return logs.count(word)


async def test_n5_00_seed_and_env(api, db):
    """環境前提+シード投入(べき等・design §4.3手順1)。"""
    result = await ensure_seed(api, db)
    n_active = await db.fetchval("SELECT count(*) FROM intents WHERE status='active'")
    record_item(
        item_id="ws7-n5-00",
        name="環境前提とシード投入(べき等)",
        test="test_n5_00_seed_and_env",
        verdict="pass",
        observed={
            "skipped": result["skipped"],
            "users": result["users"],
            "active_intents": n_active,
        },
    )


async def test_n5_10_poison_payload_quarantined(db):
    """毒ペイロード(構造違反JSON)が5回再試行の末にquarantined隔離される
    (10 §4.7・06 §9)。失敗理由はpayload.failure_reasonへ保持。"""
    await ensure_env_prerequisites(db)
    before = await db.fetchval(
        "SELECT count(*) FROM match_events WHERE status='quarantined'"
    )
    publish_event("created", str(uuid.uuid4()), raw_payload='{"event_type": "created"}')
    await asyncio.sleep(45)  # 再試行5回(バックオフ1,2,4,8,16秒)+余裕
    row = await db.fetchrow(
        "SELECT status, payload->>'failure_reason' AS reason "
        "FROM match_events WHERE source_intent_id=$1 ORDER BY created_at DESC LIMIT 1",
        uuid.UUID(NIL_UUID),
    )
    assert row is not None, "毒ペイロードの行が挿入されていません"
    assert row["status"] == "quarantined", row
    assert row["reason"], "失敗理由がpayloadに保持されていません"
    after = await db.fetchval(
        "SELECT count(*) FROM match_events WHERE status='quarantined'"
    )
    record_item(
        item_id="ws7-n5-10",
        name="毒ペイロード→5回再試行→quarantined隔離+理由保持",
        test="test_n5_10_poison_payload_quarantined",
        verdict="pass",
        observed={
            "failure_reason": row["reason"],
            "quarantined_before": before,
            "quarantined_after": after,
        },
    )


async def test_n5_20_deleted_reference_processed_discard(api, db):
    """削除済みIntent参照Eventはquarantinedでなくprocessedで閉じる
    (06 §9・破棄理由payload.discard_reason=intent_not_found)。

    同一version再publishは既存processed行とのUNIQUE衝突でduplicate=ackに
    なり理由が記録されないため・削除後に存在しないversion(updated v2)を
    publishして新3点組を作る(§2.1読み替えに相当する観察設計)。
    """
    await ensure_env_prerequisites(db)
    subject = "ws7-n5-del"
    await create_user_nfr(api, subject)
    intent_id = await create_intent_nfr(
        api, subject, intent_payload(seq=9100, now=get_clock().now())
    )
    r = await api.delete(f"/v1/intents/{intent_id}", headers=auth_header(subject))
    assert r.status_code in (200, 204), r.text
    await asyncio.sleep(15)  # 削除Event処理(cascade物理削除)
    publish_event("updated", intent_id, version=2)
    await asyncio.sleep(30)  # INSERT→worker処理
    row = await db.fetchrow(
        "SELECT status, payload->>'discard_reason' AS reason "
        "FROM match_events WHERE source_intent_id=$1 AND event_type='updated' "
        "AND payload->>'version'='2'",
        uuid.UUID(intent_id),
    )
    assert row is not None, "updated v2の行がありません"
    assert row["status"] == "processed", row
    assert row["reason"] == "intent_not_found", row
    record_item(
        item_id="ws7-n5-20",
        name="削除済み参照Event→processed破棄(理由=intent_not_found)",
        test="test_n5_20_deleted_reference_processed_discard",
        verdict="pass",
        observed={"status": row["status"], "discard_reason": row["reason"]},
    )


async def test_n5_30_unique_duplicate_no_double_candidates(api, db):
    """同一Event 2回投入でUNIQUE制約により重複排除
    (match_events 1行のまま・候補二重生成なし)。"""
    pair = await create_pair_nfr(api, db, prefix="n5dup", seq=9200)
    n_events = await db.fetchval(
        "SELECT count(*) FROM match_events WHERE source_intent_id=$1 "
        "AND event_type='created' AND payload->>'version'='1'",
        uuid.UUID(pair["intent_a"]),
    )
    n_cand = await db.fetchval("SELECT count(*) FROM match_candidates")
    publish_event("created", pair["intent_a"], version=1)  # 同一3点組の再投下
    await asyncio.sleep(30)
    n_events2 = await db.fetchval(
        "SELECT count(*) FROM match_events WHERE source_intent_id=$1 "
        "AND event_type='created' AND payload->>'version'='1'",
        uuid.UUID(pair["intent_a"]),
    )
    n_cand2 = await db.fetchval("SELECT count(*) FROM match_candidates")
    assert n_events == 1, n_events
    assert n_events2 == 1, f"重複行が生成された: {n_events2}"
    assert n_cand2 == n_cand, f"候補が二重生成された: {n_cand}→{n_cand2}"
    record_item(
        item_id="ws7-n5-30",
        name="UNIQUE重複排除(同一Event再投下で行・候補とも不変)",
        test="test_n5_30_unique_duplicate_no_double_candidates",
        verdict="pass",
        observed={
            "match_events": n_events2,
            "match_candidates": f"{n_cand}→{n_cand2}",
        },
    )


async def test_n5_40_no_propagation_after_poison(api, db):
    """毒投入後も後続イベントは滞留せず提案まで到達する(非波及・10 §4.7)。"""
    before = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='proposed'"
    )
    await create_pair_nfr(api, db, prefix="n5prop", seq=9300)
    after = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='proposed'"
    )
    assert after > before, "毒後の正常ペアで提案が発生していません(波及の疑い)"
    record_item(
        item_id="ws7-n5-40",
        name="毒後の非波及(正常ペアが提案まで到達)",
        test="test_n5_40_no_propagation_after_poison",
        verdict="pass",
        observed={"proposed_before": before, "proposed_after": after},
    )


async def test_n5_50_lag_alert_and_resolve(api, db):
    """worker停止で滞留を作り・API側のlag alertが1回発報→解除resolve
    (10 §4.7・design §2.5案A・単発化はRedisアームキー)。

    滞留の作り方はAPI作成(created)である(§2.1読み替え10): match_events行
    (status=pending)はAPI側のoutbox(tx内INSERT・intents/events.py)で作られ・
    worker停止中も残る。CLI publishはPub/Sub送信のみでworker停止中は行を
    作らないため使わない。年齢はlag_alertのClock差分(=APIのControlledClock)
    で計算されるため・staging clock advanceで年齢条件を仮想時計で成立させ・
    tick(60秒・実時間)の発報はwait_untilで待つ。fallback relayが仮想時計
    前進でpending行を再publishし続けるが・worker再開後の3点組重複(UNIQUE・
    duplicate=ack)で冪等=無害。
    """
    await ensure_env_prerequisites(db)
    clock = get_clock()
    await create_user_nfr(api, "ws7-n5lag")  # users作成はworker停止前に
    workers_stop()
    try:
        # pending行を作る(API outbox=tx内INSERT・worker停止中は消化されない)
        await create_intent_nfr(
            api, "ws7-n5lag", intent_payload(seq=9700, now=clock.now())
        )
        n_pending = await db.fetchval(
            "SELECT count(*) FROM match_events WHERE status='pending'"
        )
        assert n_pending >= 1, f"pending行が生えていない: {n_pending}"
        # 年齢条件(>=300秒)を仮想Clockで成立させる(tickは実時間・発報を待つ)
        clock.advance(timedelta(seconds=310))
        await wait_until(
            lambda: _api_log_matches("queue lag alert") == 1,
            timeout_s=120,
            interval_s=10,
            what="queue lag alertの発報(api両コンテナ合算で1回)",
        )
    finally:
        workers_start()
    await wait_events_done(db, timeout_s=300)  # 消化(年齢条件の解除)
    await wait_until(
        lambda: _api_log_matches("queue lag resolve") >= 1,
        timeout_s=120,
        interval_s=10,
        what="queue lag resolveの記録",
    )
    n_alert = _api_log_matches("queue lag alert")
    n_resolve = _api_log_matches("queue lag resolve")
    assert n_alert == 1, f"lag alertの発報は1回: {n_alert}"
    record_item(
        item_id="ws7-n5-50",
        name="Queue滞留→lag alert 1回発報→解除resolve(worker停止含む)",
        test="test_n5_50_lag_alert_and_resolve",
        verdict="pass",
        observed={
            "alert_count": n_alert,
            "resolve_count": n_resolve,
            "pending_rows_at_stop": n_pending,
        },
    )
```

注: `test_n5_00`は他テストの前提を整える。部分実行で`test_n5_50`単体を回す場合も・user作成(停止前)とIntent作成(停止後・API outbox)を自前で行うため独立して動く。`wait_pipeline_settled`を呼ばないのは・滞留(pending残置)が試験の目的であるため。

- [ ] **Step 4: 収集確認(unit規律の範囲内)+コミット**

staging実行はスーパーバイザー検証時のため・本Taskではcollect-onlyとlintまでを行う:

Run: `cd backend && uv run pytest staging_tests_nfr --collect-only -q --import-mode=importlib && make lint && make test`
Expected: test_n5_queue.pyの6件が収集される(このTaskではn1〜n4ファイルは未作成)+lint/testグリーン(testpaths外のためmake testの件数は不変1438+Task 1〜6分)

```bash
git add backend/staging_tests_nfr/conftest.py backend/staging_tests_nfr/helpers_nfr.py backend/staging_tests_nfr/test_n5_queue.py
git commit -m "feat: ws-7 NFRハーネス土台+n5 Queue障害試験(毒・参照不在・重複・非波及・lag alert)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 8: test_n4_reeval.py(resume再評価・catch-up回収)

**Files:**
- Create: `backend/staging_tests_nfr/test_n4_reeval.py`

**Interfaces:**
- Consumes: conftest(workers_stop/workers_start/publish_event/get_clock/record_item)・helpers_nfr(create_user_nfr/create_intent_nfr/create_pair_nfr/wait_pipeline_settled/wait_events_done/current_version/intent_payload)・`redis.asyncio`(guardキー観察)
- Produces: test_n4_10・test_n4_20の2件(証拠ws7-n4-10・ws7-n4-20)

- [ ] **Step 1: test_n4_reeval.pyを書く**

```python
"""n4: resume再評価とcatch-up回収(10 §4.5・FR-18)。

(a) resume: version+1のupdated EventでEmbeddingの再実行なくCandidate
Retrievalから再評価(embedding3列不変+embedding_completed再発行)。
(b) catch-up: worker停止+Clock前進でBucket到来Eventの欠落を作り、worker再開
後のcatch-upスキャン(60秒周期・Event発行せず直接投入)が回収する。観察は
reevalガードキー(§2.1読み替え9)+embedding列の状態照合。
worker停止は必ずtry/finallyで戻す(Review Focus 5)。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import redis.asyncio as aioredis

from conftest import (
    REDIS_URL,
    auth_header,
    get_clock,
    publish_event,
    record_item,
    workers_start,
    workers_stop,
)
from helpers_nfr import (
    create_intent_nfr,
    create_pair_nfr,
    create_user_nfr,
    current_version,
    ensure_env_prerequisites,
    intent_payload,
    wait_events_done,
    wait_pipeline_settled,
    wait_until,
)


async def test_n4_10_resume_reeval_without_embedding(api, db):
    """(a) pause→Bucket経過→resume: Embedding再実行なく再評価へ復帰
    (10 §4.5(a)・相手Intentは別ユーザー配置=supervisor訂正2)。"""
    await ensure_env_prerequisites(db)
    pair = await create_pair_nfr(api, db, prefix="n4res", seq=9400)
    intent_a = pair["intent_a"]
    subject_a = pair["subject_a"]
    # resume前の状態記録(embedding3列+completed行数+相手候補)
    row_before = await db.fetchrow(
        "SELECT embedding::text AS emb, embedding_model, updated_at "
        "FROM intents WHERE id=$1",
        uuid.UUID(intent_a),
    )
    completed_before = await db.fetchval(
        "SELECT count(*) FROM match_events WHERE event_type='embedding_completed' "
        "AND source_intent_id=$1",
        uuid.UUID(intent_a),
    )
    # pause(仮想時刻基準のtransition)
    r = await api.post(f"/v1/intents/{intent_a}/pause", headers=auth_header(subject_a))
    assert r.status_code == 200, r.text
    paused = await db.fetchval(
        "SELECT status FROM intents WHERE id=$1", uuid.UUID(intent_a)
    )
    assert paused == "paused"
    # 対象開始時刻のBucketをClockで経過させる(10 §4.5(a)字義)。前進幅は
    # DB照合で計算する: (time_start−now)+600秒でstartを確実に通過する
    # (n4_20と同方式・仮想時刻依存を排除)。reevalガードの30分窓脱出
    # (1800秒)もこの前進で自然に満たされる(time_start=作成+3時間のため)
    time_start = await db.fetchval(
        "SELECT time_start FROM intents WHERE id=$1", uuid.UUID(intent_a)
    )
    assert time_start is not None
    advance_s = (time_start - get_clock().now()).total_seconds() + 600
    get_clock().advance(timedelta(seconds=advance_s))
    # resume(version+1のupdated Event・embedding不変の経路)
    r = await api.post(f"/v1/intents/{intent_a}/resume", headers=auth_header(subject_a))
    assert r.status_code == 200, r.text
    v_after_resume = await current_version(db, intent_a)
    assert v_after_resume == 2, f"resume後のversionは+1: {v_after_resume}"
    row_after_resume = await db.fetchrow(
        "SELECT embedding::text AS emb, embedding_model, updated_at "
        "FROM intents WHERE id=$1",
        uuid.UUID(intent_a),
    )
    await wait_pipeline_settled(db)
    row_after = await db.fetchrow(
        "SELECT embedding::text AS emb, embedding_model, updated_at "
        "FROM intents WHERE id=$1",
        uuid.UUID(intent_a),
    )
    completed_after = await db.fetchval(
        "SELECT count(*) FROM match_events WHERE event_type='embedding_completed' "
        "AND source_intent_id=$1",
        uuid.UUID(intent_a),
    )
    # Embeddingの再実行なし(embedding3列不変・updated_atも動かない)
    assert row_after["emb"] == row_after_resume["emb"], "embeddingが変化した"
    assert row_after["emb"] == row_before["emb"]
    assert row_after["embedding_model"] == row_before["embedding_model"]
    assert row_after["updated_at"] == row_after_resume["updated_at"]
    # 再評価(embedding_completedの再発行=resume由来の正規経路)
    assert completed_after > completed_before, "embedding_completedが再発行されていない"
    # 提案が発生し得る状態への復帰(候補の再評価完遂)
    cand = await db.fetchrow(
        "SELECT status, jev_result FROM match_candidates "
        "WHERE intent_a_id=$1 AND intent_b_id=$2 "
        "AND cheap_judge_score IS NOT NULL",
        uuid.UUID(intent_a),
        uuid.UUID(pair["intent_b"]),
    )
    assert cand is not None, "resume後の候補が見つかりません"
    assert cand["status"] == "evaluated", cand
    assert cand["jev_result"] is not None, "Jevまで走っていない"
    record_item(
        item_id="ws7-n4-10",
        name="resume→version+1→Embedding再実行なしで再評価・復帰",
        test="test_n4_10_resume_reeval_without_embedding",
        verdict="pass",
        observed={
            "version_after_resume": v_after_resume,
            "embedding_unchanged": True,
            "embedding_model": row_after["embedding_model"],
            "completed_before": completed_before,
            "completed_after": completed_after,
            "candidate_status": cand["status"],
        },
    )


async def test_n4_20_catchup_recovers_and_excludes_null_embedding(api, db):
    """(b) worker停止中のClock前進で欠落を作り・catch-upが回収する。
    embedding済み対象は処理され(reevalキー立)・embedding NULL対象は除外され
    backfillで回収される(06 §9・design §2.4(b))。"""
    await ensure_env_prerequisites(db)
    redis = aioredis.Redis.from_url(REDIS_URL, decode_responses=True)
    clock = get_clock()
    # 対象(i_n4c・expires_atを遠くへ=time_start=now+6日・FR-13で≈+72h候補)
    # 相手(i_n4e)・NULL化対象(i_n4d)の3件を別ユーザーで作る
    now = clock.now()
    await create_user_nfr(api, "ws7-n4c")
    await create_user_nfr(api, "ws7-n4e")
    await create_user_nfr(api, "ws7-n4d")
    intent_c = await create_intent_nfr(
        api, "ws7-n4c", intent_payload(seq=9500, now=now, start_hours=6 * 24)
    )
    intent_e = await create_intent_nfr(
        api, "ws7-n4e", intent_payload(seq=9501, now=now, start_hours=6 * 24)
    )
    intent_d = await create_intent_nfr(
        api, "ws7-n4d", intent_payload(seq=9502, now=now, start_hours=6 * 24)
    )
    await wait_pipeline_settled(db)
    expires_c = await db.fetchval(
        "SELECT expires_at FROM intents WHERE id=$1", uuid.UUID(intent_c)
    )
    expires_d = await db.fetchval(
        "SELECT expires_at FROM intents WHERE id=$1", uuid.UUID(intent_d)
    )
    assert expires_c is not None and expires_d is not None
    remaining = (expires_c - clock.now()).total_seconds()
    assert remaining > 7200, f"expires_at残が短い: {remaining}s"
    advance_s = remaining - 6000  # 「expires_atまで2時間を切る」状態を機械的に作る
    # ガードキー前掃除(観察の一意性)
    await redis.delete(f"reeval:{intent_c}")
    await redis.delete(f"reeval:{intent_d}")
    workers_stop()
    try:
        # NULL化(対象抽出条件の逆経路=embedding NULLはcatch-up除外・06 §9)
        import asyncpg

        conn = await asyncpg.connect(
            "postgresql://latch:latch@127.0.0.1:15432/latch"
        )
        n_null = await conn.execute(
            "UPDATE intents SET embedding=NULL, embedding_model=NULL WHERE id=$1",
            uuid.UUID(intent_d),
        )
        await conn.close()
        assert "UPDATE 1" in n_null, n_null
        # Bucket到来Eventのスケジューラが止まっている間にClockを前進=欠落
        clock.advance(timedelta(seconds=advance_s))
        workers_start()
        await asyncio.sleep(90)  # catch-upスキャン(60秒周期)+マージン
        key_c = await redis.get(f"reeval:{intent_c}")
        key_d = await redis.get(f"reeval:{intent_d}")
        assert key_c == "1", "catch-upがembedding済み対象を処理していない"
        assert key_d is None, "embedding NULLがcatch-upで処理された(除外が効いていない)"
        emb_d = await db.fetchval(
            "SELECT embedding IS NULL FROM intents WHERE id=$1", uuid.UUID(intent_d)
        )
        assert emb_d is True, "除外対象のembeddingが変化している"
        # backfillへの戻し(NULL化Intentがbackfill周期で回収される=観察完了後)
        await wait_events_done(db, timeout_s=30)

        async def recovered_now() -> bool:
            v = await db.fetchval(
                "SELECT embedding IS NOT NULL AND embedding_model IS NOT NULL "
                "FROM intents WHERE id=$1",
                uuid.UUID(intent_d),
            )
            return bool(v)

        await asyncio.sleep(300)  # backfill初回周期(300秒)
        await wait_until(
            recovered_now,
            timeout_s=600,
            what="backfillによるNULL化Intentの回収",
        )
        expires_d_after = await db.fetchval(
            "SELECT expires_at FROM intents WHERE id=$1", uuid.UUID(intent_d)
        )
        assert expires_d_after is not None
    finally:
        workers_start()
        await redis.aclose()
    record_item(
        item_id="ws7-n4-20",
        name="catch-up回収(embedding済み対象は処理・NULLは除外→backfill回収)",
        test="test_n4_20_catchup_recovers_and_excludes_null_embedding",
        verdict="pass",
        observed={
            "advance_s": advance_s,
            "reeval_key_c": key_c,
            "reeval_key_d_before_backfill": None,
            "backfill_recovered": True,
        },
    )
```

観察の対(対で証明): 「embedding済みIntentが対象に含まれる」=key_c=="1"・「embedding IS NULLのIntentが除外される」=key_d is None+embedding NULLのまま・「backfillへ戻す」=wait_untilの完了自体(回収しなければタイムアウト失敗=それ自体が正しい失敗)・の3点(承認事項§5-7)。

- [ ] **Step 2: 収集確認+コミット**

Run: `cd backend && uv run pytest staging_tests_nfr/test_n4_reeval.py --collect-only -q --import-mode=importlib && make lint`
Expected: 2件収集+lintグリーン

```bash
git add backend/staging_tests_nfr/test_n4_reeval.py
git commit -m "feat: n4ハーネス(resume再評価・catch-up回収とNULL除外)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 9: test_n3_d16.py(D-16縮退)

**Files:**
- Create: `backend/staging_tests_nfr/test_n3_d16.py`

**Interfaces:**
- Consumes: `latch.nfrtools.d16`(set_counter/get_counter/delete_counter/daily_key/monthly_key・Task 5)・conftest(docker_logs/publish_event/get_clock/record_item・redis_nfr fixtureはStep 2で追加)・helpers_nfr(create_pair_nfr/current_version/wait_pipeline_settled/ensure_env_prerequisites)
- Produces: test_n3_10〜test_n3_50の5件(証拠ws7-n3-10〜50)。母体は**試験ごとのfreshペア**(§2.1読み替え11・n3の月跨ぎClockジャンプで既存Intentはexpired化され・expiredへのupdatedはLayer 1のactive条件で候補を作らずJevが走らないため)

- [ ] **Step 1: test_n3_d16.pyを書く**

```python
"""n3: D-16縮退(10 §4.5残部・04 §5 D-16)。

alert境界(INCR戻り値==しきい値で1回)・上限到達(INCR>上限でdeny・skip・
提案ゼロ)・0時JSTリセット復帰・月次80%と日次の独立性・月初リセット。
実EventはEmbedding済みペアの一方へのpublish updated(§2.1読み替え5)・
各updatedの前にclock advance 1900秒(reevalガード30分窓の脱出)。
母体は試験ごとのfreshペア(§2.1読み替え11): 月跨ぎClockジャンプで既存
Intentはsweeperにexpired化されるため・キャッシュペアではupdatedが候補を
作らずJevが走らない。リセット後の試験(3_30/3_50)はfreshペア作成分の
INCRをdelete_counterで掃除してからpublishし・「INCR==1」を厳密に検証する。
旧キー残留のassertはそのまま(Redis TTLは実時間駆動のため仮想ジャンプで
消えない)。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from conftest import (
    WORKER_CONTAINERS,
    docker_logs,
    get_clock,
    publish_event,
    record_item,
)
from helpers_nfr import (
    create_pair_nfr,
    current_version,
    ensure_env_prerequisites,
    wait_pipeline_settled,
)
from latch.core.clock import JST
from latch.nfrtools.d16 import (
    daily_key,
    delete_counter,
    get_counter,
    monthly_key,
    set_counter,
)


def _next_day_jst_0005():
    now = get_clock().now().astimezone(JST)
    d = (now + timedelta(days=1)).date()
    return datetime(d.year, d.month, d.day, 0, 5, tzinfo=JST)


def _next_month_start_jst_0005():
    now = get_clock().now().astimezone(JST)
    year = now.year + (1 if now.month == 12 else 0)
    month = 1 if now.month == 12 else now.month + 1
    return datetime(year, month, 1, 0, 5, tzinfo=JST)


async def _publish_updated(api, db, pair) -> int:
    """ペアの一方へ次のversionのupdatedを投下し・収束を待つ(戻り値=投下version)。

    直近評価から30分以上経過させるため・投下前にclock advance 1900秒
    (reevalガード SET NX EX 1800の脱出)。
    """
    get_clock().advance(timedelta(seconds=1900))
    v = await current_version(db, pair["intent_a"]) + 1
    publish_event("updated", pair["intent_a"], version=v)
    await wait_pipeline_settled(db)
    return v


def _worker_log_matches(word: str) -> int:
    """worker1/worker2の両コンテナログ合算でのword出現件数。"""
    logs = "".join(docker_logs(c) for c in WORKER_CONTAINERS)
    return logs.count(word)


async def test_n3_10_daily_alert_at_24000(api, db, redis_nfr):
    """23,999→実Event→INCR=24,000でdaily_80pct alert(源流レポート添付)が
    丁度1回発報する(04 §5・guard実装の実経路確認)。"""
    await ensure_env_prerequisites(db)
    clock = get_clock()
    pair = await create_pair_nfr(api, db, prefix="n3d1", seq=9010)
    before_daily = await get_counter(redis_nfr, clock, "daily")
    before_alert = _worker_log_matches("daily_80pct")
    await set_counter(redis_nfr, clock, "daily", 23999)
    assert await get_counter(redis_nfr, clock, "daily") == 23999  # 読み戻し実査
    version = await _publish_updated(api, db, pair)
    assert await get_counter(redis_nfr, clock, "daily") == 24000
    assert _worker_log_matches("daily_80pct") - before_alert == 1, (
        "daily_80pct alertは1回"
    )
    # alert行のJSONをパースして源流レポート添付を確認(引用#7)
    import json as _json

    all_logs = "".join(docker_logs(c) for c in WORKER_CONTAINERS)
    alert_line = next(
        line for line in all_logs.splitlines() if "daily_80pct" in line
    )
    payload = _json.loads(alert_line[alert_line.index("{") :])
    assert "report" in payload, "alertに源流レポートが添付されていない"
    assert payload["report"]["intent_top"] is not None
    record_item(
        item_id="ws7-n3-10",
        name="D-16日次80%(24,000)でalert 1回発報+源流レポート添付",
        test="test_n3_10_daily_alert_at_24000",
        verdict="pass",
        observed={
            "daily_before": before_daily,
            "set_value": 23999,
            "daily_after": 24000,
            "alert_count": 1,
            "updated_version": version,
        },
    )


async def test_n3_20_daily_limit_deny_skip(api, db, redis_nfr):
    """30,000→実Event→INCR=30,001>30,000でdeny(global_daily)・候補skip・
    提案ゼロ(§2.1読み替え1・jev.py _skip_rowの実経路確認)。"""
    clock = get_clock()
    pair = await create_pair_nfr(api, db, prefix="n3d2", seq=9020)
    await set_counter(redis_nfr, clock, "daily", 30000)
    assert await get_counter(redis_nfr, clock, "daily") == 30000  # 読み戻し実査
    proposed_before = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='proposed'"
    )
    version = await _publish_updated(api, db, pair)
    daily_after = await get_counter(redis_nfr, clock, "daily")
    assert daily_after == 30001, daily_after
    skipped = await db.fetchrow(
        "SELECT status, skip_reason, jev_result FROM match_candidates "
        "WHERE (intent_a_id=$1 OR intent_b_id=$1) AND skip_reason='global_daily' "
        "ORDER BY id DESC LIMIT 1",
        uuid.UUID(pair["intent_a"]),
    )
    assert skipped is not None, "skip(global_daily)の候補行がありません"
    assert skipped["status"] == "skipped"
    assert skipped["jev_result"] is None, "Jev未判定保留がjev_result付きになっていない"
    proposed_after = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='proposed'"
    )
    assert proposed_after == proposed_before, "deny状態で提案が発生している"
    record_item(
        item_id="ws7-n3-20",
        name="D-16日次上限到達→Jevスキップ・『Jev未判定』保留・提案ゼロ",
        test="test_n3_20_daily_limit_deny_skip",
        verdict="pass",
        observed={
            "set_value": 30000,
            "daily_after": daily_after,
            "skip_reason": skipped["skip_reason"],
            "proposed_unchanged": proposed_before,
            "updated_version": version,
        },
    )


async def test_n3_30_daily_reset_recovers_at_jst_midnight(api, db, redis_nfr):
    """0時(JST)リセットで新日キーへ切替・実行が復帰する(04 §5)。

    月跨ぎ前の既存Intentはexpired化済みのため母体はfreshペア(読み替え11)。
    freshペア作成分のINCRをdelete_counterで掃除し・publishでINCR==1を厳密検証。
    """
    clock = get_clock()
    old_key = daily_key(clock)
    old_value = await redis_nfr.get(old_key)
    clock.set(_next_day_jst_0005())
    new_key = daily_key(clock)
    assert new_key != old_key
    assert await redis_nfr.get(new_key) is None  # 新キーは0から
    pair = await create_pair_nfr(api, db, prefix="n3d3", seq=9030)
    await delete_counter(redis_nfr, clock, "daily")  # 作成分のINCRを掃除
    version = await _publish_updated(api, db, pair)
    assert await get_counter(redis_nfr, clock, "daily") == 1  # 実行復帰(INCR=1)
    cand = await db.fetchrow(
        "SELECT status FROM match_candidates "
        "WHERE (intent_a_id=$1 OR intent_b_id=$1) AND status='evaluated' "
        "AND jev_result IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        uuid.UUID(pair["intent_a"]),
    )
    assert cand is not None, "日次リセット後の実行復帰が確認できません"
    record_item(
        item_id="ws7-n3-30",
        name="D-16日次リセット(0時JST)で新日キー切替・実行復帰",
        test="test_n3_30_daily_reset_recovers_at_jst_midnight",
        verdict="pass",
        observed={
            "old_key": old_key,
            "old_value": old_value,
            "new_key": new_key,
            "new_value": 1,
            "updated_version": version,
        },
    )


async def test_n3_40_monthly_alert_independence(api, db, redis_nfr):
    """479,999→実Event→INCR=480,000でmonthly_80pctのみ発報
    (日次は未到達=両者の独立性の証明・10 §4.5字義)。"""
    clock = get_clock()
    pair = await create_pair_nfr(api, db, prefix="n3d4", seq=9040)
    daily_before = await get_counter(redis_nfr, clock, "daily")
    m_before = _worker_log_matches("monthly_80pct")
    d_before = _worker_log_matches("daily_80pct")
    await set_counter(redis_nfr, clock, "monthly", 479999)
    assert await get_counter(redis_nfr, clock, "monthly") == 479999
    version = await _publish_updated(api, db, pair)
    assert await get_counter(redis_nfr, clock, "monthly") == 480000
    assert _worker_log_matches("monthly_80pct") - m_before == 1, (
        "monthly alertが1回出ていない"
    )
    assert _worker_log_matches("daily_80pct") == d_before, "daily alertが同時に発報した"
    assert await get_counter(redis_nfr, clock, "daily") == daily_before + 1
    record_item(
        item_id="ws7-n3-40",
        name="D-16月次80%(480,000)alert発報・日次との独立性",
        test="test_n3_40_monthly_alert_independence",
        verdict="pass",
        observed={
            "monthly_set": 479999,
            "monthly_after": 480000,
            "monthly_alerts": 1,
            "daily_alerts_delta": 0,
            "daily_before": daily_before,
            "updated_version": version,
        },
    )


async def test_n3_50_monthly_reset_independence(api, db, redis_nfr):
    """暦月初JST 0時リセットで月次キーが新yyyymmへ切替・月次実行が復帰し・
    旧日次・旧月次キーはTTL内で残留する(04 §5・Clock操作で月初境界を進める)。

    月跨ぎ前の既存Intentはexpired化済みのため母体はfreshペア(読み替え11)。
    freshペア作成分のINCRをdelete_counterで掃除し・publishでINCR==1を厳密検証。
    """
    clock = get_clock()
    old_month_key = monthly_key(clock)
    old_day_key = daily_key(clock)
    old_month_value = await redis_nfr.get(old_month_key)
    old_day_value = await redis_nfr.get(old_day_key)
    clock.set(_next_month_start_jst_0005())
    new_month_key = monthly_key(clock)
    assert new_month_key != old_month_key
    assert await redis_nfr.get(new_month_key) is None
    assert await redis_nfr.get(old_month_key) == old_month_value, "旧月次キーが消えた"
    assert await redis_nfr.get(old_day_key) == old_day_value, "旧日次キーが消えた"
    pair = await create_pair_nfr(api, db, prefix="n3d5", seq=9050)
    await delete_counter(redis_nfr, clock, "monthly")  # 作成分のINCRを掃除
    version = await _publish_updated(api, db, pair)
    assert await get_counter(redis_nfr, clock, "monthly") == 1  # 月次実行復帰
    cand = await db.fetchrow(
        "SELECT status FROM match_candidates "
        "WHERE (intent_a_id=$1 OR intent_b_id=$1) AND status='evaluated' "
        "AND jev_result IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        uuid.UUID(pair["intent_a"]),
    )
    assert cand is not None, "月次リセット後の実行復帰が確認できません"
    record_item(
        item_id="ws7-n3-50",
        name="D-16月次リセット(暦月初JST)・旧キー残留と実行復帰の独立性",
        test="test_n3_50_monthly_reset_independence",
        verdict="pass",
        observed={
            "old_month_key": old_month_key,
            "old_month_value": old_month_value,
            "new_month_key": new_month_key,
            "old_day_key_value_kept": old_day_value is not None,
            "new_month_value": 1,
            "updated_version": version,
        },
    )
```

注: `redis_nfr`はTask 7のconftest.pyへfixturesとして追加する(本TaskのStep 2)。`ORDER BY id DESC`は最新世代の候補行を取る(candidatesのid列はIDENTITY挿入順・起点がintent_b側の行でも照合可能なため・version照合は行わない)。

- [ ] **Step 2: conftestへredis_nfr fixtureを追加する(Task 7ファイルへの追従)**

Task 7のconftest.pyのfixtures部(`db_recovery_rw`の後)へ追加:

```python
@pytest.fixture
async def redis_nfr():
    """n3用: staging Redis(async・D-16カウンタ操作とguardキー観察)。"""
    import redis.asyncio as aioredis

    r = aioredis.Redis.from_url(REDIS_URL, decode_responses=True)
    yield r
    await r.aclose()
```

- [ ] **Step 3: 収集確認+コミット**

Run: `cd backend && uv run pytest staging_tests_nfr/test_n3_d16.py --collect-only -q --import-mode=importlib && make lint`
Expected: 5件収集+lintグリーン

```bash
git add backend/staging_tests_nfr/test_n3_d16.py backend/staging_tests_nfr/conftest.py
git commit -m "feat: n3ハーネス(D-16 alert境界・上限deny・日次/月次リセットと独立性)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 10: test_n2_prep.py+test_n2_backup.py(バックアップ復旧)

**Files:**
- Create: `backend/staging_tests_nfr/test_n2_prep.py`(dump直前の母体作成・§2.1読み替え12)・`backend/staging_tests_nfr/test_n2_backup.py`

**Interfaces:**
- Consumes: conftest(db/db_recovery_rw/api/record_item)・helpers_nfr(create_pair_nfr/create_user_nfr/create_intent_nfr/wait_pipeline_settled/intent_payload/wait_until/get_clock/ensure_env_prerequisites)
- Produces: test_n2_00(prep・1件)+test_n2_10〜test_n2_40の4件(計5件・証拠ws7-n2-00〜40)。実施前提は§8手順5(5-0 prep→worker停止→dump→db2-up→restore→db2-switch)

- [ ] **Step 1: test_n2_prep.pyを書く**

`backend/staging_tests_nfr/test_n2_prep.py`を新規作成する:

```python
"""n2 prep: 復旧試験の母体作成(10 §4.4・§2.1読み替え12)。

実行順序はn5→n4→n3→n2(design §4.2・順序変更なし)で・n3の月跨ぎClock
ジャンプにより既存Intentはexpired化済み。そのままではactive数0でdumpし
cross-checkが0==0で自明化するため・dump直前にfreshペア2組を作りactive
母体を確保する(手順§8の5-0で実行)。
"""

from __future__ import annotations

from conftest import record_item
from helpers_nfr import create_pair_nfr, ensure_env_prerequisites


async def test_n2_00_prep_active_population(api, db):
    """dump直前のactive母体を確保する(freshペア2組)。"""
    await ensure_env_prerequisites(db)
    await create_pair_nfr(api, db, prefix="n2p1", seq=9650)
    await create_pair_nfr(api, db, prefix="n2p2", seq=9652)
    n_active = await db.fetchval(
        "SELECT count(*) FROM intents WHERE status='active'"
    )
    assert n_active >= 4, f"active母体が足りません: {n_active}"
    record_item(
        item_id="ws7-n2-00",
        name="dump直前のactive母体確保(freshペア2組)",
        test="test_n2_00_prep_active_population",
        verdict="pass",
        observed={"active_intents": n_active},
    )
```

- [ ] **Step 2: test_n2_backup.pyを書く**

```python
"""n2: バックアップ復旧(10 §4.4)。

前提(§8手順5): 5-0 prep(freshペア2組・test_n2_prep.py)→worker停止→
make staging-dump→staging-db2-up→staging-restore→staging-db2-switch済み。
既定DB(15432)はworker停止中に静止したため・そのactive数=ダンプ時点の値
=cross-checkの「前」。db2(16543)が「後」。
復旧後の再エンベディングは識別子記録(embedding_model)で対象を特定して
NULL化し・backfillで回収する(01 §18・design §2.2案A)。
"""

from __future__ import annotations

import uuid

from conftest import (
    auth_header,
    record_item,
)
from helpers_nfr import (
    create_intent_nfr,
    create_user_nfr,
    get_clock,
    intent_payload,
    wait_pipeline_settled,
    wait_until,
)


async def test_n2_10_preconditions(db, db_recovery_rw):
    """実施前提の名前つき失敗: 両DB到達・既定DBの静止(dump時点の保証)。"""
    ver = await db_recovery_rw.fetchval("SELECT version_num FROM alembic_version")
    assert ver == "0008", f"db2のheadが0008ではありません: {ver}(staging-restoreを実行)"
    n_pending_default = await db.fetchval(
        "SELECT count(*) FROM match_events WHERE status='pending'"
    )
    assert n_pending_default == 0, (
        "既定DBにpendingが残っています(worker停止→dump→switchの手順を再確認)"
    )
    n_active_default = await db.fetchval(
        "SELECT count(*) FROM intents WHERE status='active'"
    )
    assert n_active_default > 0, "既定DBのactive Intentが0件です(手順1のシード投入を確認)"


async def test_n2_20_cross_check_active_count(db, db_recovery_rw):
    """cross-check: 復旧前後のactive Intent数一致(10 §4.4)。"""
    before = await db.fetchval(
        "SELECT count(*) FROM intents WHERE status='active'"
    )
    after = await db_recovery_rw.fetchval(
        "SELECT count(*) FROM intents WHERE status='active'"
    )
    assert before == after, f"active数不一致: 既定DB={before} db2={after}"
    record_item(
        item_id="ws7-n2-20",
        name="cross-check: 復旧前後のactive Intent数一致",
        test="test_n2_20_cross_check_active_count",
        verdict="pass",
        observed={"before(15432)": before, "after(16543)": after},
    )


async def test_n2_30_reembedding_via_identifier_null_and_backfill(db_recovery_rw):
    """識別子記録(embedding_model)で再エンベディング対象を特定→NULL化→
    backfill回収(01 §18・design §2.2案A)。"""
    model_before = await db_recovery_rw.fetchval(
        "SELECT DISTINCT embedding_model FROM intents "
        "WHERE embedding IS NOT NULL AND embedding_model IS NOT NULL LIMIT 1"
    )
    assert model_before, "embedding済み行がありません(staging-restoreを確認)"
    targets = await db_recovery_rw.fetchval(
        "SELECT count(*) FROM intents "
        "WHERE embedding IS NOT NULL AND embedding_model IS NOT NULL"
    )
    result = await db_recovery_rw.execute(
        "UPDATE intents SET embedding=NULL, embedding_model=NULL "
        "WHERE embedding IS NOT NULL AND embedding_model IS NOT NULL"
    )
    assert f"UPDATE {targets}" in result, (targets, result)
    assert targets > 0

    async def recovered() -> bool:
        n = await db_recovery_rw.fetchval(
            "SELECT count(*) FROM intents "
            "WHERE embedding IS NOT NULL AND embedding_model IS NOT NULL"
        )
        return int(n) == targets

    await wait_until(
        recovered,
        timeout_s=600,
        interval_s=10,
        what=f"backfillによるembedding回収({targets}件・recovery overrideでinterval 5秒)",
    )
    record_item(
        item_id="ws7-n2-30",
        name="識別子特定→embedding NULL化→backfillで再エンベディング",
        test="test_n2_30_reembedding_via_identifier_null_and_backfill",
        verdict="pass",
        observed={
            "embedding_model": model_before,
            "nulled_rows": targets,
            "recovered_rows": targets,
        },
    )


async def test_n2_40_smoke_proposal_after_recovery(api, db_recovery_rw):
    """復旧後のスモーク: Intent検索・マッチング・通知が正常
    (新規ペア→提案→通知記録・10 §4.4)。"""
    before_notif = await db_recovery_rw.fetchval(
        "SELECT count(*) FROM notifications WHERE type='proposal'"
    )
    now = get_clock().now()
    await create_user_nfr(api, "ws7-n2a")
    await create_user_nfr(api, "ws7-n2b")
    intent_a = await create_intent_nfr(
        api, "ws7-n2a", intent_payload(seq=9600, now=now)
    )
    intent_b = await create_intent_nfr(
        api, "ws7-n2b", intent_payload(seq=9601, now=now)
    )
    await wait_pipeline_settled(db_recovery_rw)
    r = await api.get("/v1/latches", headers=auth_header("ws7-n2a"))
    assert r.status_code == 200, r.text
    proposed = await db_recovery_rw.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='proposed'"
    )
    assert proposed >= 1, "復旧後に提案が発生していません(マッチング不成立)"
    after_notif = await db_recovery_rw.fetchval(
        "SELECT count(*) FROM notifications WHERE type='proposal'"
    )
    assert after_notif > before_notif, "通知記録が発生していません"
    r = await api.get("/v1/notifications", headers=auth_header("ws7-n2a"))
    assert r.status_code == 200, r.text
    record_item(
        item_id="ws7-n2-40",
        name="復旧後スモーク(検索・マッチング・通知が正常)",
        test="test_n2_40_smoke_proposal_after_recovery",
        verdict="pass",
        observed={
            "latches_http": r.status_code,
            "proposed": proposed,
            "notifications": f"{before_notif}→{after_notif}",
            "intents": [intent_a, intent_b],
        },
    )
```

- [ ] **Step 3: 収集確認+コミット**

Run: `cd backend && uv run pytest staging_tests_nfr/test_n2_prep.py staging_tests_nfr/test_n2_backup.py --collect-only -q --import-mode=importlib && make lint`
Expected: 5件収集(prep=1+backup=4)+lintグリーン

```bash
git add backend/staging_tests_nfr/test_n2_prep.py backend/staging_tests_nfr/test_n2_backup.py
git commit -m "feat: n2ハーネス(母体prep・cross-check・識別子NULL化→backfill・復旧後スモーク)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 11: test_n1_availability.py+全収集確認+報告書(実施待ち)

**Files:**
- Create: `backend/staging_tests_nfr/test_n1_availability.py`・`docs/plans/M4/ws-7-report.md`

**Interfaces:**
- Consumes: `RESULTS_DIR`(conftest)のws7-probe-*.yaml(§8手順6の出力)・yaml
- Produces: test_n1_10の1件(証拠ws7-n1-10)・報告書

- [ ] **Step 1: test_n1_availability.pyを書く**

```python
"""n1: 可用性(10 §4.3)。プローブ出力YAMLの検証(ハーネス側)。

プローブ実行自体はmake staging-probe(§8手順6・60分試験窓)が担い・本テストは
結果YAMLの機械検証(成功率99.5%・マーカータグ除外・dense記録)を担う。
自動復旧の観察(docker inspect時刻)は手順の記録を報告書へ転記する。
"""

from __future__ import annotations

from pathlib import Path

from conftest import RESULTS_DIR, record_item


def _latest_probe_yaml() -> Path:
    candidates = sorted(RESULTS_DIR.glob("ws7-probe-*.yaml"))
    if not candidates:
        raise AssertionError(
            "ws7-probe-*.yamlがありません。make staging-probe(§8手順6)を実行してください"
        )
    return candidates[-1]


def test_n1_10_probe_evidence():
    """プローブ結果の機械検証(成功率99.5%・タグ除外・dense併設)。"""
    import yaml

    path = _latest_probe_yaml()
    report = yaml.safe_load(path.read_text(encoding="utf-8"))
    verdict = report["verdict"]
    assert verdict["verdict_995"] is True, f"成功率が99.5%未満: {verdict['main_success_rate']}"
    assert verdict["main_excluded_planned_outage"] >= 1, (
        "計画停止タグの除外が効いていない(§8手順6のマーカー操作を確認)"
    )
    assert verdict["dense_total"] > 0, "denseプローブ(10秒間隔)の記録がない"
    assert verdict["main_counted"] > 0
    conditions = report["input_conditions"]
    assert conditions["duration_s"] >= 3500, f"試験窓が短い: {conditions['duration_s']}"
    record_item(
        item_id="ws7-n1-10",
        name="可用性プローブ検証(成功率99.5%・タグ除外・dense記録)",
        test="test_n1_10_probe_evidence",
        verdict="pass",
        observed={
            "evidence": str(path),
            "main_success_rate": verdict["main_success_rate"],
            "main_counted": verdict["main_counted"],
            "excluded_planned_outage": verdict["main_excluded_planned_outage"],
            "dense_total": verdict["dense_total"],
            "max_consecutive_failures": verdict["max_consecutive_failures"],
        },
    )
```

- [ ] **Step 2: 全収集確認(最終形の機械確認)**

Run: `cd backend && uv run pytest staging_tests_nfr --collect-only -q --import-mode=importlib`
Expected: 19件収集(n5=6・n4=2・n3=5・n2prep=1・n2=4・n1=1)

Run: `cd backend && uv run pytest -m "not integration" --co -q | tail -1`
Expected: 約`1471/1846 tests collected`(1438+新unit33=1471・ハーネス19件は含まれない。実数を報告書へ記録)

- [ ] **Step 3: コミット(実施待ちの状態まで)**

```bash
git add backend/staging_tests_nfr/test_n1_availability.py
git commit -m "feat: n1ハーネス(プローブYAML検証)+ハーネス全収集確認

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

- [ ] **Step 4: 報告書の骨格を書く(実施証拠は§8実施後に追記)**

`docs/plans/M4/ws-7-report.md`を§7の形式で作成する。実施前の時点では「結果サマリ・完了条件別の証拠・実測記録」の欄に「スーパーバイザー検証待ち」と記録してよい(§0規律)。実施後に骨格へ実測値を追記して最終コミットする。

- [ ] **Step 5: 最終検査+コミット**

```bash
make lint && make test
find backend/tests backend/staging_tests backend/staging_tests_obs backend/staging_tests_nfr -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
# 期待値: 出力なし
git status --short
git add docs/plans/M4/ws-7-report.md
git commit -m "docs: M4 ws-7実施報告(骨格・実施待ち)

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

## 6. 完了条件(テストで証明できる検証可能な形)

design §4(テスト方針)を計画の完了条件へ引き取る。各条件の証明方法を明記する。

1. **Queue lag alert発報器が・条件成立中1回だけ発報し・解除でresolveを出し・2インスタンス間で二重発報しない(既定無効・ci干渉ゼロ)**
   証明: unit test_obs_lag_alert(8件: 発報1回・再発報抑制・resolve+再武装・クロスインスタンス単発化・Clock年齢・Redis故障fail-open・DB失敗握り・アームTTL)+test_nfr_pins(main.py wiring静的ピン・compose.staging.yaml無傷)+test_settings(alert_系集合ピン5本)。stagingでの実経路はtest_n5_50(worker停止310秒で発報1・消化後resolve)。
2. **合成監視プローブが・1分間隔の成功率をマーカータグ分母外で集計し・99.5%の機械判定とdense併設をYAML化する**
   証明: unit test_loadgen_probe(8件: タグ分母除外・タイムアウト集計・連続失敗・dense分離・空集計・run_probe決定性・マーカータグ付け・token再発行)。実測は§8手順6の出力YAML+test_n1_10。
3. **D-16カウンタ操作が・Clock導出キーへset/get/deleteでき・実経路でalert境界・上限deny・日次/月次リセットと独立性が確認できる**
   証明: unit test_nfrtools_d16(8件: キー導出2種・往復・上書きTTL・削除・scope検証・CLI配線2種)。実経路はtest_n3_10〜50(set 23999→INCR=24,000 alert 1回・set 30,000→INCR=30,001 deny/skip/提案ゼロ・0時JSTリセット復帰・月次480,000 alertと日次独立性・月初リセットで旧キー残留)。
4. **resume再評価とcatch-up回収が・Embedding再実行なし・NULL除外込みでstagingの実環境で確認できる**
   証明: test_n4_10(embedding3列不変・completed再発行・候補evaluated=復帰)+test_n4_20(worker停止+Clock前進で欠落を作り・reevalキーでembedding済み対象の処理とNULL除外を照合・backfillでNULL化Intent回収)。
5. **バックアップ復旧の復旧手順(復元→識別子特定→再エンベディング→再開→cross-check)がstagingで完結する**
   証明: §8手順5(Makefile手順・実測記録)+test_n2_10〜40(前提名前つき失敗・active数一致・識別子NULL化→backfill回収・復旧後スモーク=検索/マッチング/通知)。
6. **Queue障害(毒・参照不在・UNIQUE重複・非波及)がstagingの実経路で確認できる**
   証明: test_n5_10〜40(quarantined+failure_reason・processed+intent_not_found・行/候補不変・毒後の提案到達)+test_n5_50(lag alert往復)。
7. **unit試験グリーン・ハーネス収集がtestpaths外・証拠がdocs/testassets/results/へ保存**
   証明: `make test`グリーン(**基準1438+新規unit33=1471件**・実数を報告書に記録)。`cd backend && uv run pytest staging_tests_nfr --collect-only -q --import-mode=importlib`で19件(n5=6・n4=2・n3=5・n2prep=1・n2=4・n1=1)収集。`make test`の収集にstaging_tests_nfrが含まれないこと。**test-ci: 本単位はintegrationを追加しないため実行不要**(報告書へ明記)。実施証拠(ws7-nfr-result-*.yaml・ws7-probe-*.yaml)は§8実施時に生成。

## 7. 報告形式

報告書: `docs/plans/M4/ws-7-report.md`。以下の節をこの順で書く。

1. **実施サマリ**: 実施日・環境(worktree/ブランチm4-ws-7・基点main head=e02446d)・`make test`の実数(基準1471・実数)・コミット一覧(ハッシュ+1行要約)。
2. **完了条件別の証拠**(§6の1〜7の各項に): 実施した試験と実行結果(pytest出力の要約)・証拠YAMLのファイル名と機械判定値(verdict_995・alert件数・active数等)。
3. **4領域の実施結果**: (a)可用性=試験窓・main成功率・除外サンプル数・dense観察・docker kill時刻とhealthy復帰時刻(手順§8の記録転記)・api2計画停止窓のタグ確認 (b)バックアップ復旧=RTO実測(復元開始→pg_restore完了・復元開始→スモーク提案確帰)・NULL化件数・backfill回収所要・cross-check数値 (c)D-16=alert/上限/リセットの各観察値・**上限到達後のqueue pending推移の時系列観察**(ws-6 G4確認事項8の裏取り・収束判定は要求しない) (d)Queue障害=毒・参照不在・重複・非波及・lag alert往復の各観察値。
4. **designからの読み替え・逸脱の記録**: §2.1の読み替え表の各項(1〜9)について「実装どおりか・変更した場合は変更前→変更後+理由」を実施結果として記録する。特に読み替え1(上限到達の境界=30,000投下→INCR 30,001)・3(override 2本)・5(updated連用+advance 1900秒)・7(catch-up対象化のadvance計算)は必須記録。
5. **実施パラメータの実測記録**: 手順§8の実測値(シード投入所要・n5〜n3の各所要・dump/restore所要・プローブ窓実時間)と・手順初期値との差分。
6. **スーパーバイザー検証待ちの記録**: test-ciは本単位integration追加ゼロのため実行不要であることを明記(§0規律)。
7. **G4時確認事項の付記依頼**(supervisor側で記録): ①「lag alert発報器はworker内collector(docstringの『gaugeを消費する』)でなくAPI側周期タスクに新設した(同じDB集計値を消費する意と解釈)」②「alert媒体は構造化ログ(latch.cost.alert/latch.pipeline.alert)のまま・alertmanager等への差し替えはPhase 0以降」③「circuit breakerのRedis共有(M2 ws-8引継ぎ)は本単位スコープ外・staging worker2台でbreaker状態はプロセス独立」の3項目。
8. **詰まった箇所と判断**: 作業中に停止・確認した点があれば「状況・採った判断・根拠」を書く(なければ「なし」)。

## 8. 実施手順(非機能試験の実行。スーパーバイザー検証時)

前提: Task 1〜11完了・worktreeはマージ済みmain。全コマンドはリポジトリルートで実行(明記のない`cd`は戻す)。STATUS運用ルール6どおり・**測定中(n1の60分)はci常設環境を止める**(design §1.1「staging-resetから開始し・測定中はci一時停止」)。検証チェーンはパイプを書かない・各段で件数実査。

```bash
# 1. staging環境のリセットと起動(nfr override=lag alert有効)
#    実行内容: staging-reset(down -v→up→migrate→geo-import)の後・apiのみnfr composeで再作成
make staging-nfr-reset
#    確認1: make staging-ps で db/redis/pubsub/api1/api2/nginx/worker1/worker2 がrunning
#    確認2(lag alert有効の実査):
docker compose -f compose.staging.yaml -f compose.staging.nfr.yaml exec -T api1 printenv LATCH_ALERT_QUEUE_ENABLED
#    期待: true と出る(1行・件数実査)

# 2. プローブ用ユーザー作成(load-u0001・n1でGET /v1/users/meを打つユーザー)
cd backend && uv run python -m latch.loadgen --base-url http://127.0.0.1:18000 --keys ../.staging/keys users --count 1
#    期待: users ready: created=1 existing=0 (consent済み)
cd ..

# 3. ci一時停止(運用ルール6)
make down

# 4. n5→n4→n3(ハーネス・直列。worker停止・Clock操作を含む・所要30〜60分)
make staging-nfr-test NFR_FILES="staging_tests_nfr/test_n5_queue.py staging_tests_nfr/test_n4_reeval.py staging_tests_nfr/test_n3_d16.py"
#    期待: 13 passed(n5=6+n4=2+n3=5)・証拠YAML docs/testassets/results/ws7-nfr-result-*.yaml
#    補足: n3の上限到達後のpending推移(30分再評価循環・ws-6 G4確認事項8)は・この直後に
#    次のコマンドを数分おきに実行して値を記録する(収束判定は要求しない):
docker exec latch-staging-db-1 psql -U latch -t -c "SELECT count(*) FROM match_events WHERE status='pending'"

# 5. n2(バックアップ復旧・Makefile手順+ハーネス検証)
#    5-0: dump直前のactive母体を作る(§2.1読み替え12・n3の月跨ぎで既存Intent
#    はexpired化済みのため・active数0でdumpするとcross-checkが自明化する)
make staging-nfr-test NFR_FILES="staging_tests_nfr/test_n2_prep.py"
#    期待: 1 passed(freshペア2組・active数>=4)・証拠YAMLにws7-n2-00を記録
#    5-1: worker停止(既定DBを静止=active数の「前」値を機械的に確定)
docker compose -f compose.staging.yaml -f compose.staging.nfr.yaml stop worker1 worker2
#    5-2: ダンプ(定期バックアップ1サイクルの読み替え・10 §4.4)
date -Is && make staging-dump
#    期待: .staging/backup/latch-staging.dump が作られる(docker cp可能なサイズ)
ls -l .staging/backup/
#    5-3: 件数実査(cross-checkの「前」・値を記録)
docker exec latch-staging-db-1 psql -U latch -t -c "SELECT count(*) FROM intents WHERE status='active'"
#    5-4: db2起動→復元→切替
make staging-db2-up
make staging-restore
#    期待: 終了コード0(エラーなし。docker execのrcを実査)
make staging-db2-switch
#    5-5: ハーネス検証(cross-check・NULL化→backfill・スモーク)
make staging-nfr-test NFR_FILES="staging_tests_nfr/test_n2_backup.py"
#    期待: 4 passed(cross-check一致・backfill回収・スモーク提案・通知)
#    5-6: db2停止+既定DBへ戻す(切替の解除)
make staging-db2-down
docker compose -f compose.staging.yaml -f compose.staging.nfr.yaml up -d --force-recreate --wait api1 api2 worker1 worker2
#    確認(戻しの実査): 5-3と同一値が出る
docker exec latch-staging-db-1 psql -U latch -t -c "SELECT count(*) FROM intents WHERE status='active'"

# 6. n1(可用性・60分試験窓)
#    6-1: マーカー初期化+プローブ起動(バックグラウンド)
rm -f .staging/probe-outage.marker
cd backend && nohup uv run python -m latch.loadgen --base-url http://127.0.0.1:18000 --keys ../.staging/keys probe --duration 3600 > ../.staging/probe-console.log 2>&1 &
echo "probe started pid=$!"
cd ..
#    6-2: 安定15分
sleep 900
#    6-3: 強制停止(SIGKILL)→restart: unless-stoppedの自動復旧を観察
date -Is && docker kill latch-staging-api1-1
#    kill時刻を記録・約60秒待ってhealthy復帰を確認(数回実行して遷移を記録)
sleep 60
docker inspect -f '{{.State.Health.Status}} restarted={{.State.StartedAt}}' latch-staging-api1-1
#    期待: healthy(自動復旧。StartedAtがkill時刻より後=再起動の証拠)
#    6-4: 計画停止(タグ動作確認を兼ねる)・api2をstop→約3分→start
touch .staging/probe-outage.marker
date -Is && docker stop latch-staging-api2-1
#    stop時刻を記録(マーカー窓の開始)
sleep 180
date -Is && docker start latch-staging-api2-1
#    start時刻を記録(窓の終了・時差≈3分を記録)
sleep 30
rm .staging/probe-outage.marker
#    6-5: 試験窓の残りを待つ(合計3600秒超になるまで)
sleep 1500
#    6-6: プローブ完了確認
tail .staging/probe-console.log
#    期待: probe done: main=120 rate=... verdict=True と evidence: docs/testassets/results/ws7-probe-*.yaml

# 7. n1検証(プローブYAML)
make staging-nfr-test NFR_FILES="staging_tests_nfr/test_n1_availability.py"
#    期待: 1 passed(成功率99.5%・タグ除外・dense記録)

# 8. ci復帰(運用ルール6どおり・必須)
make up
make ps
#    確認: ci環境がhealthy

# 9. 報告書(Task 11 Step 4の骨格へ実測値を記録)
```

実測パラメータ(所要時間・件数・時刻)は手順実行時に記録し・報告書へ残す。プローブ窓の実時間がduration 3600を超えた分は報告書に記録する(YAMLのverdictは試験窓内のサンプルのみを集計する)。

## 9. Self-Reviewの結果(計画完成時点で実施済み)

1. **Spec coverage**: design §1.1の4領域→Task 7(n5=Queue障害)・Task 8(n4=resume/catch-up)・Task 9(n3=D-16)・Task 10(n2=バックアップ復旧)・Task 11+§8手順6(n1=可用性)。design §2.1〜§2.5の採用案→Task 4(probe=§2.1案A)・Task 10+§8手順5(§2.2案A)・Task 9(§2.3案A)・Task 8(§2.4)・Task 2/3/6(§2.5案A・lag alert API側)。design §3.1ファイル表→§4.1/4.2(全行にTask番号付き・nfrtools/__main__.pyとoverride 2本の追加は§2.1読み替え3/4で明記)。design §4テスト方針→Task 1〜6(unit)+Task 7〜11+§6。design §5承認9件→§2.3の確定値表・訂正3件(日次TTL=48hは§1参照仕様とTask 9のTTL assert・相手Intent別ユーザー配置はtest_n4_10のpair構成・アームキー単発化はTask 2/6の実装とピン)。design §4.3実施手順(n5→n4→n3→n2→n1固定・ci一時停止・パイプ禁止・件数実査)→§8。**漏れなし**。
2. **Placeholder scan**: なし。全試験コード・実装コードは最終形で書き切った(初稿の「注記で訂正」形式はSelf-Reviewで解消し・本文をクリーンな最終形へ書き直した)。未定義参照なし・「TBD」「適切に実装」等の文言なし。`test_nfr_pins.py`はTask 3/5/6の3段階で増分追加されるが・各段の関数名は§4.1と重複しない。
3. **Type consistency**: `QueueLagAlert`のIF(Task 2定義・interval_sec引数)とTask 3の消費(lifespan)・Task 7のテスト(conftestなしの単体)と一致。`ProbeSample`/`summarize`/`TokenCache`/`run_probe`はTask 4定義とtest_n1・cmd_probe消費と一致。`daily_key`/`monthly_key`/`set_counter`/`get_counter`/`delete_counter`はTask 5定義とTask 9消費と一致。conftest定数(REPO_ROOT・DSN・RECOVERY_DSN・REDIS_URL・WORKER_CONTAINERS・API_CONTAINERS)とhelpers_nfr関数(wait_until・ensure_seed・create_pair_nfr・current_version・intent_payload・create_user_nfr・create_intent_nfr・wait_pipeline_settled・wait_events_done)はTask 7定義とTask 8〜11消費で一致(n3のfreshペアはcreate_pair_nfr直呼び・`ensure_d16_pair`は削除済み)。test_n5の`_api_log_matches`・test_n3の`_worker_log_matches`/`_publish_updated`・test_n3のredis_nfr fixture(Task 9 Step 2でconftestへ追加)は定義と消費で一致。`record_item`の引数(item_id・name・test・verdict・observed)は全テストで同一。
4. **Review Focus**: 5項目それぞれに所有タスクの試験を割り当て済み(1→Task 2・2→Task 4・3→Task 5・4→Task 4・5→Task 7のfinally規律)。
5. **機械チェック**: §2.2の(a)basename一意(実行結果=衝突ゼロを記録)・(b)ピン影響表(test_settings/test_app_health/test_loadgen_compose_pins/test_compose_ports/test_arch_no_direct_time/test_log_allowlistの追随と無傷系を明示)・(c)DB干渉対抗策(マイグレーションなし・staging専用・unitはDB不使用・UPDATEは2箇所のみ・ci無干渉)を記録済み。
6. **実突合済みのコード主張**(計画時に実コードで確認): guard.py L102/L125-128(INCR戻り値等値でalert・`>`でdeny)・jev.py L48-60(_complete_rowはjev_result IS NULLガード・_skip_rowはskip_reason記録)・embedding.py handle(has_embedding→再エンベディングせず_emit_completed)・store.py TTL(日次48h・月次45d)・main.py L437(reevalガード)・stage1.py intake(PayloadInvalid→5回→quarantined直行・payload.failure_reason)/_process_once(intent_not_found→processed・payload.discard_reason)・reeval.py catch-up SQL(embedding IS NOT NULL条件)・worker/main.py backfill interval(settings.embedding_backfill_interval_sec=300)・routes.py pause/resume(L350/365)・**intents/events.py insert_match_event(created Eventのpending行はAPI側tx内INSERT=滞留作成はAPI経路・events __main__のpublishはPub/Sub送信のみでINSERTしない)**・test_embedding_clear_on_update.py(resumeはembedding非クリア)・ASGITransportのlifespan非発火(tests/conftest.py)・logallow集合ピン(`latch.llm.send`/`latch.push.send`の2種のみ=alertログ非対象)・compose.staging.yaml(env構造・ports・restart unless-stopped)・nginx staging.conf(proxy_next_upstream)・helpers_seed.py(seed投入とwait作法・実績)・events __main__(--raw-payload)・clock_cli(操作CLI作法)。unit基準数1438(実収集確認済み2026-10-05)。
7. **計画内整合の訂正記録**: §5冒頭の「新規unit試験の総数」は初稿で29件と書いたが・全テスト関数を実数えした結果は**33件**(Task 1=3・Task 2=8・Task 3=1・Task 4=8・Task 5=8・Task 6=5)であり・本文の期待値を「**1438+33=1471**」へ統一した(§6-7・§7-1・Task 11 Step 2も同値)。コミット時に実数を報告書へ記録する。
8. **supervisor計画レビュー7件(F1〜F7・2026-10-05)の修正記録**: F1=滞留の作り方をworker停止中のCLI publishから**API作成(created・API outboxのtx内INSERT)**へ変更(test_n5_50全面書き換え・年齢条件は仮想Clockで成立・発報はwait_until・§2.1読み替え10)。F2=n3を**試験ごとのfreshペア**へ変更(月跨ぎClockジャンプでのexpired化対策・3_30/3_50はdelete_counterでINCR掃除してから「INCR==1」を厳密検証・`ensure_d16_pair`削除・§2.1読み替え11)。F3=**test_n2_prep.py新設**(dump直前のactive母体・手順5-0新設・§2.1読み替え12・ハーネス収集19件)。F4=n4_10の前進幅をDB照合((time_start−now)+600秒)へ変更(10 §4.5(a)字義どおりstart通過)。F5=test_n5_20のSystemClock混入をget_clock().now()へ統一。F6=recovery overrideのdb2へ`profiles: ["recovery"]`追加+ピン。F7=読み替え表(10〜12)と本節への反映。いずれもunit試験の件数は不変(33件)・ハーネスは19件。
