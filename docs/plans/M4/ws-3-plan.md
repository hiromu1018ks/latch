# M4 ws-3(機能試験27項目のstagingフル実行)実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** docs/plans/M4/ws-3-design.md §1.1どおり、ws-1のstaging環境+ws-2のシード資産上で02 §4のstaging振り分け項目とG3繰越(02#13〜#23)をpytestハーネスとして自動実行し、10 §5の形式の証拠をdocs/testassets/results/へ残す。stub Jev判定値の固定経路(settings+gateway)を製品コード変更として伴う。

**Architecture:** ハーネスはpytestスイート(`backend/staging_tests/`・testpaths外)として実装し、既存のmake test/test-ciの収集に一切触れずに隔離する。検査はAPI(nginx 18000)+DB読取(SELECT専用)+pushログ(docker logs)の3観測経路で行い、固定期待はreference_l1(backend/src/latch/testassets/reference_l1.py)で機械再計算できる量に限定する。製品コード変更はsettings新項目2つ+gateway構築のstub応答組み立てのみ(既定値=現行定数でci挙動不変)。

**Tech Stack:** Python 3.x・pytest(pytest-asyncio auto mode)・httpx・asyncpg・redis・pyyaml(全て既存依存・追加なし)。docker compose(latch-stagingプロジェクト)。

**Spec:** docs/plans/M4/ws-3-design.md(agent1・コミット07a565e)。本計画はdesign §6のsupervisor承認6件+スーパーバイザー引継ぎ(2026-10-03)に従う。designと本計画の読み替えは§2.1に一覧する。

---

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m4-ws-3`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。
- **worktreeの基点はws-5マージ後のmain**(supervisor裁定2026-10-03): ws-3のworktreeは **ws-5がマージ済みでalembic head=0007のmain** から作る。ws-5が未マージの場合は着手せずスーパーバイザーに確認する。worktree作成後、最初に `make setup`(`cd backend && uv sync`)を実行して `.venv` を構築する(**依存追加・マイグレーション追加ともになし**のためlockもalembicも進まない)。
- **毎コミットの検査**: worktree内で**毎コミット `make lint` と `make test` がグリーン**であることを確認してからコミットする。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。staging_testsはbackend配下のためruff対象に含まれる(ruff format/checkで整形・検査しておく)。
- **共有ci-dbへのtest-ci/migrate実行はスーパーバイザー検証時のみ**(2026-10-03スーパーバイザー指示): 本単位は**マイグレーションを追加しない**(design冒頭・head=0007〔ws-5追加分を含む〕不変)ため、**agent3は `make test-ci` も `make migrate`(ci宛)も実行しない**。報告書の該当完了条件には **「make test-ci(基準実数+新規5件維持): スーパーバイザー検証待ち」** と記録する。開発の検証は `make test`(unit)+stagingコンテナ実行(下記)で行う。
- **staging環境の操作はagent3が実施してよい**(latch-stagingプロジェクト・独立ボリュームのためciと干渉しない):
  - 可: `make staging-up` / `staging-down` / `staging-reset`(本計画で追加) / `staging-ps` / `staging-logs` / `staging-migrate` / `staging-geo-import` / `staging-keys` / `staging-token` / `staging-clock` / `staging-publish` / `staging-test`(本計画で追加)、および `docker compose -f compose.staging.yaml` 宛のexec/build/logs。
  - 不可: ci(latch-ciプロジェクト・compose.yaml・Makefileのci系ターゲット)宛の状態変更操作すべて。**`-f` を付け忘れるとciプロジェクト宛に作用する。コマンドを打つ前に毎回 `-f` の有無を確認する**(最重要の事故防止規律)。
  - geo資材(`backend/data/geo/`のCSV/PBF)はgit管理外・ciと共用。worktreeにない場合はリポジトリREADMEどおり `make geo-download` で取得する(WS-1実施済みのマシンでは既存)。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。**計画時点で機械確認済み(2026-10-03)**: 現状 `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空(既存139ファイル)。本計画の新規ファイル(`backend/tests/unit/test_llm_stub_response_settings.py`・`backend/staging_tests/test_s0_*.py`〜`test_s5_*.py`)は既存と衝突しない(staging_tests配下にもtests配下と同名は作らない・新規はtest_sN_*接頭辞)。各Taskのコミット前に同コマンド(staging_testsも含めた下記の形)が空であることを再確認する:

  ```bash
  find backend/tests backend/staging_tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
  # 期待値: 出力なし(空)
  ```

- **integration試験はcollect-onlyのみ実行可**: agent3は `uv run pytest -m integration --collect-only -q`(backend配下)は実行してよいが、**integration試験の実実行はしない**(実DB/実コンテナ経路はスーパーバイザー検証のtest-ciで走る)。staging_testsの実行は本計画本体であるためこの制限外。
- **python / pipコマンドを直接使わない**: すべて `uv run` / `uv sync` 経由。
- **依存追加なし・マイグレーション追加なし**: `backend/pyproject.toml`・`backend/uv.lock`・`backend/alembic/`・`.env`・`.env.example` に触らない。ハーネスの必要物(httpx・asyncpg・redis・pyyaml)は全て既存依存に存在する。**pytestタイムアウトプラグイン(pytest-timeout等)は追加しない** — 待ち上限はstaging_tests内のhelpers_wait(全条件待ちをtimeout引数で上限化)で制御する(§2.4)。
- **Clock契約・実時間参照の規律**: `core/clock.py` は1行も変更しない。arch test(`test_arch_no_direct_time.py`)の対象は`src/latch`のみのため、ハーネスの`time.monotonic`(待ち上限計測)は影響外(設計§4-6)。ただし**シナリオの時間設計(対象日時・前進量)は仮想Clock(ControlledClock)で行い・実時間計測は待ち上限のみ**に限定する。
- **タイムボム回避**(M3 ws-1の実例): stagingのClock操作・シード投入の証拠は実行時刻依存の相対表現(「今夜23:30」等)に固定せず**ISO8601明示**(例: `2026-10-04T11:00:00+09:00`)で行い、実施値を報告書へ記録する。
- **固定値の遵守**: 設計§2の採用判断(案A〔pytestスイート〕・settings新項目2つ・noul=0.90/score=3.6・tick 60秒既定・観測経路3種〔API/DB読取/pushログ〕)は固定値。**変更した場合は必ず報告ファイルに「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `chore:` 等)。
- **全タスクのコミット前に**: `make lint && make test` グリーン・`git status --short` で差分が§4の一覧どおり・basename一意コマンドが空。
- **部分実行(-k)の規律**: 開発中のシナリオ反復は `make staging-reset` 直後に `cd backend && uv run pytest staging_tests -k "s0 or 対象"` の形で**必ずs0を含めて**実行する(シード投入はs0のsession初期化関数が行うため)。Clock前進を伴うシナリオ(#8を含むs1後半・s2以降)の開発検証は**フル実行**で行う(前進済み状態が前提の検査が混在するため)。
- **staging検査の期待値確定手順**: StubLLM embedding(テキストハッシュ・決定的)+YAML順投入+全起点処理済みの収束待ちは**実行ごとに同一の生成集合**を作る。このためS1以降の「生成集合に依存する期待値」は**初回フル実行で実測確定**し、確定値を報告書に記録する。もし実測が本計画の期待と異なる場合は、推測で検査を緩めず**作業を停止し報告書に実測と考察を書いてスーパーバイザーに確認する**(§0「詰まったら」規律)。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| stagingの機能検証は「#5(スモーク)、#6〜#13、#15〜#19、#26、#27(セット共通)」。**「Jevスタブで双方向判定値を固定(応答形式はSystem One互換のanswers)」**が明記 | 10 §3表staging行備考 |
| 追加試験(staging): D-10(#27動作面)・D-23・FR-21。配置ケース: 自己ペア除外・自己重複集合(FR-17)・部分YES・通知順序 | 10 §3「追加試験」・「配置ケース」 |
| 時刻参照はすべてClock経由。stagingではテスト用Clock制御を注入できる(適用範囲6項目)。期限系はClock操作で再現し実時間待ちを排除 | 10 §1「時刻操作」・ws-1実装済み(ControlledClock・make staging-clock) |
| 疑似イベント注入: Match Event 5種+embedding_completedを手動発火。LLMはテストモード(stub)で決定的。FCMはドライラン(記録のみ) | 10 §1「疑似イベント注入」 |
| 証拠: 試験ID・実施日・環境・入力条件・合否でdocs/testassets/results/へ。**CI・stagingの機能試験は自動化し、リリース前ゲートとして全項目グリーンを必須** | 10 §5 |
| 自己ペアの除外(同一ユーザー2 Intent→match_candidates不生成)・自己重複集合の除外(3〜4人集合に同一ユーザー2 Intent→group_candidates不生成)。テストデータの分布には自己重複を検出する意図的配置を含める | 10 §3「配置ケース」 |
| 部分YES: 3〜4人セットの提案に一部のみyes→残り未回答のままresponse_deadline後へClock進行(D-06期限時確定)。通知順序: 同一Poolから複数集合→proposedはaggregate_score降順の1つだけ(group_candidates記録で確認) | 10 §3 |
| #9は二段構成: ciでHard Filter単体試験(G2済み)+**stagingでE2E確認**(予算・時間・距離・人数を意図的に外すペアがいずれの候補にも現れない) | 10 §3表ci行備考 |
| #13(v0.3改訂): mutedのIntentのケース(通知送信なし・proposed遷移・ホームのLATCH候補から回答可)を含める。#22(v0.3改訂): 公開設定2値の分岐確認(hidden_until_matchとsummary_onlyで提案画面の内容比較) | 10 §3表staging行備考・02 §4 #13/#22 |
| D-10: 18〜19歳ユーザーのalcohol_involved=true Intent作成拒否(422 UNDER_AGE)+飲酒Intentが20歳未満ユーザーとの候補に現れない(Layer 1)。D-23: matched LATCHで一方がブロック→進行中提案cancelled・チャット読み取り専用・「このチャットは利用できません」。FR-21: ドライラン記録のプッシュ本文が汎用文+条件サマリ不含・drinkingも同一様式・summary_onlyはお知らせと提案画面にサマリ表示・hiddenは両方に表示なし | 10 §3「追加試験」・08 D-10/D-23/§2.6 |
| MLR(#26)の数え方: 分母=当該期間に通知を送信したLATCH候補数(1候補1・グループも1・期限切れ/NO/競合クローズも除かない)、分子=必要人数全員YESした候補数。**集計はproposed遷移を分母カウント、matched遷移を分子カウント**しlatch_status_eventsから集計 | 01 §23・09 §2.1・02 §4 #26 |
| グループ系(#18ほか)は09 §3の6検証と同一手順(G3でci実装済み=ws-9対応表)。stagingでは同一検査をstaging観測で再実行 | 10 §3表staging行備考 |
| 保存APIはraw_text必須+structured_intent全フィールドOptional・保存経路にParser呼び出しなし・ジオコーディング正転は保存時に組み込み・レート制限=Active 5件・作成20件/日(ユーザー単位) | 05 §5(実装: backend/src/latch/intents/service.py `_create`・08 §5.4) |
| latch_score = C × min(would_a, would_b)・C=1.0・提案閾値0.60。グループaggregate = C × min(ペアMutualScore)。D-06提示順はaggregate降順→同点サイズ昇順→intent_id辞書順・1対1の保留キュー提示順は対象時刻昇順・タイブレークLATCH Score降順 | latch_calc.py/latch_engine.py/group_calc.py・06 §6・06 §10 |
| D-08: 提案は日次6件/ユーザー・同時3件/Intent上限。超過分はlatches.status=candidateのまま保留・JST日付キーのリセットジョブ+保留キュー再評価(60秒周期)で翌日へ昇格 | 06 §10・03 D-08 |
| match_level(score): ≥MATCH_LEVEL_HIGH_MIN=high(0.90ちょうどはhigh) | backend/src/latch/worker/matching/latch_calc.py:58 |
| proposalのvisibility分岐: hidden_until_match→headcountとmatch_levelのみ。summary_only→time_summary・area_name・headcount・category_primary・category_secondary・budget・match_level。全員summary_onlyでなければhidden側になる | backend/src/latch/worker/matching/proposal.py |
| match_candidates.jev_result = 正規化済みJev結果(トップレベルにwould_a_accept_b等の回答キー)+provider+modelキー | backend/src/latch/worker/jev.py:300(`{**judgment.result, "provider": ..., "model": ...}`)・latch_engine.py:609 |
| push_mode=stubはドライランでロガー`latch.push.send`へJSON 1行記録(occurred_at・user_id・notification_type・latch_id・title・body・status・error_code)・取得経路はコンテナログのみ | backend/src/latch/notifications/records.py |
| 同意(ws-5・2026-10-03supervisor裁定): `POST /v1/users/me/consent`(冪等・200)をユーザーごとに呼ぶ。**同意なしユーザーのIntentは422 CONSENT_REQUIREDで遮断されLayer 1も同意条件で除外される** | ws-5-design §2.3・02 §2.2(docs改版候補・docs/plans/M4/ws-5-design.md) |
| staging環境: 別プロジェクトlatch-staging・nginx 18000/api 18001-18002/db 15432/redis 16379/pubsub 18085・api2+worker2・.staging/keysはworktree毎に別実体 | ws-1 design §2.1〜§2.3・ws-1 report |
| worker再評価系のtickは既定60秒(reeval_runner_interval_sec=60・sweeper 60秒)でClock仮想時刻を読む。Clockを進めれば次tick(≤60秒実時間)で発火 | backend/src/latch/settings.py:122・06 §6 |
| JWT発行は同梱ツールを直接使用: `issue_idp_token(provider="google", subject=..., private_key_pem=<pem bytes>)`(issuer/audience既定=Settings値・staging鍵で自動整合) | backend/src/latch/auth/tools.py:34 |
| Layer 1参照実装: `judge_pair`/`judge_pair_group`(people条件を除く・集合クラスタ用)/`judge_all`(無向・idソート済みキー)を提供済み・突合は無向化比較 | backend/src/latch/testassets/reference_l1.py(ws-2) |
| シード投入素材: docs/testassets/m4-seed.yaml(users→POST /v1/users・intents→POST /v1/intents・structured_intent明示)。intended_reject_cases 5件(EX-029〜033)は422試行素材。投入直前に同一コマンドで再生成(差分は対象日付のみ) | docs/plans/M4/ws-2-report.md「ws-3・ws-6への引継ぎ」・m4-seed-plan.md §7 |
| M4スコープ: 27項目のフル実行(ci+staging・10 §3の振り分けどおり)・証拠はdocs/testassets/results/に保管 | 12 M4-3・10 §3/§5 |

## 2. 設計確定値と計画時の機械検証

### 2.1 読み替え済み確定値(designからの差分・全部スーパーバイザー裁定つき)

| # | designの記載 | 本計画の値 | 根拠 |
|---|---|---|---|
| 1 | design §前提「head=0006」・conftest前提「head=0006」 | **head=0007** | ws-5(同意フロー)がusers.consent_at列のマイグレーション0007を追加・ws-3の実装worktreeはws-5マージ後のmainから作る(supervisor裁定2026-10-03・ws-5-design §6-3)。完了条件の「head不変」ピンも0007 |
| 2 | (designに記載なし・シード投入手順のみ) | **helpers_seedはシード100人を全員同意済みにする**(`POST /v1/users/me/consent`をユーザーごとに呼ぶ) | ws-5実装後は同意なしユーザーのIntentが422 CONSENT_REQUIREDで遮断されLayer 1も同意条件で除外されるため。全員同意でreference_l1.py・m4-seed.yaml期待値表は不変(スーパーバイザー引継ぎ2) |
| 3 | design §2.5 #11材料「ユーザー2人×15件(レート制限回避)」 | **6人×5件=30件**(HS-121〜126) | 実装の作成経路はActive 5件/ユーザー制限(service.py `_create`の`_check_active_limit`・rate_limit_active_intents=5・08 §5.4)を持つため2人×15件は6件目で429になる。6人×5件はActive制限内(5件)+作成20件/日制限内。検査内容(1イベントでL3通過K_c=20以内・Jev実行K_j=8以内の記録)は不変。**報告書に「設計表記→計画修正+理由」を記録する** |
| 4 | design §2.5 #16材料「EX-027クラスタ」 | **#16の期限確定観察は専用3人組(HS-181〜183)で行い・EX-027は集約生成の観察(S0/S3のgroup_candidates存在)に使う** | シードclusterはD-08保留に削られるためproposed以降の期限確定観察が実行ごとに揺れる(設計§2.2の対抗策(b)の趣旨)。専用3人組で決定的に観察する。報告書に記録 |
| 5 | design §2.5 #18材料「EX-028(3人)+専用4人組」 | **全員YES成立の観察は専用4人組(HS-161〜164)で行い・EX-028はgroup_candidates生成の観察に使う** | 同上(D-08保留依存の排除) |

### 2.2 D-08事前集計(計画時の実測・2026-10-03・設計§2.2対抗策(c))

m4-seed.yaml(base_datetime=2026-10-04T11:00:00+09:00)にreference_l1.judge_allを実行した結果:

- L1 passペア: **576組**(seed-verifyのaggregate一致)
- passペアの対象日分布(start日): 10-05: 170件・10-06: 73件・10-07: 83件・10-08: 134件・10-09: 116件
- ユーザー×日あたりのpassペア関与数: **最大35件**(SU-010×10-05)・上位はSU-011×10-05: 31件・SU-066×10-05: 26件
- **6件(D-08日次上限)超えのユーザー×日: 74件** → would=0.90固定では保留が大量発生する(設計§2.2の予測どおり)

この集計はS0で機械再計算して証拠に記録する(test_s0_d08_retention_forecast・Task 5)。固定期待を置く検査は「reference_l1で数えられる決定的な量」(候補集合・EX個別ペア・集合構成)に限定し、提案の成否に依存する検査(#15〜#19・D-23等)は専用シナリオIntentで行う(§2.3)。

### 2.3 専用シナリオの隔離方式と対象時刻一覧

シード母集合の提案群はD-08保留で実行ごとにproposed数が揺れる(§2.2)。よって提案の成否・状態遷移・通知内容に依存する検査は**ハーネスが自前で作る専用シナリオ**で行う。隔離の3原則:

1. **時刻隔離**: 専用シナリオIntentの対象時刻は**早朝06:30〜08:30 JST**(end null→start+3h補完=09:30まで)に置く。シード300件の対象時刻は12:00以降(day/evening/night)のためtime交差しない(time0 fail)→専用ペア同士のみがL1通過する。
2. **ユーザー隔離**: 専用シナリオユーザーはシード100人(SU-xxx)と別の**ハーネス専用ユーザー(HS-xxx)**とする。各ユーザーのActive Intent≤5件・作成≤20件/日・日次提案枠6件が空のまま保たれ、提案が決定的にproposedへ昇格する。
3. **枠の隔離**: 専用ユーザーあたりのIntentは1〜5件とし、提案対象ペアは1日あたり1〜2組に限定する(密集群HS-121〜126のみ5件×6人・提案は観察対象外でcandidate/jev観察のみ)。

対象時刻一覧(全てJST・`D+1`等はbase_datetimeからの日オフセット):

| シナリオ | ユーザー(HS-xxx) | 対象時刻 | 用途 |
|---|---|---|---|
| #7 PATCH | 101/102 | D+1 06:30 | PATCHで時刻を近づける→candidate生成観察・離す→新version行不生成観察 |
| #10 意味近接 | 111/112 | D+1 06:30 | 焼肉/肉系2 Intentのcandidate生成(stub下ではL1通過ゆえの生成) |
| #11 密集群 | 121〜126(6人) | D+1 06:30 | 互換Intent 30件(1人5件)・起点1イベントのL3≤20・Jev≤8観察 |
| #13 muted | 131/132 | D+1 06:30 | mutedペアのproposed遷移・push記録0件・回答可 |
| #14 yes/no | 191/192 | D+1 06:30 | 1人yes・1人no→responses記録・latch_status_events遷移観察 |
| #22 hidden | 151/152 | D+2 06:30 | proposal応答キー集合={headcount, match_level} |
| #22 summary_only | 153/154 | D+2 06:30 | proposal応答キー集合=7キー(§1表どおり) |
| #17 可視範囲+#15+#19+#21+D-23 | 141/142 | **D+7 06:30** | summary_onlyペア。S3の前進(D+6)後もresponse_deadline(D+7 05:15)が未来なのでyes応答可。提案詳細(candidate時)→双方YES(matched時)の前後比較・チャット・通報・ブロックまで同一ペアで完結 |
| #18 4人組 | 161〜164 | D+2 06:30 | 全員YES成立(09 §3と同一手順のstaging再実行) |
| #16 3人組 | 181〜183 | D+2 06:30 | 1人yes・2人無回答→response_deadline通過で期限確定観察 |
| #20 ブロック | 171/172 | D+1 06:30 | S5で171をブロック→172のIntent作成→候補生成されない |

(各シナリオユーザーは必ず`POST /v1/users/me/consent`を呼んでからIntentを作る — §2.1読み替え2)

### 2.4 時間旅路(仮想Clockの前進計画・tick 60秒既定のまま)

tick周期(sweeper 60秒・reeval_runner_interval_sec=60)は既定のまま変更しない(設計§2.4・supervisor承認6)。全条件待ちの上限は**120〜180秒**(tickの2〜3倍)。pytestタイムアウトプラグインは追加しない — 全待機はhelpers_wait.wait_until(timeout_s=...)で上限化し、タイムアウト時は何の条件で失敗したか(what)を AssertionErrorに含める。全行程45分目標(実時間待ちの合計: 収束待ち≤180秒+tick待ちを各シナリオ≤180秒ずつ)。

| 旅路ステップ | 仮想時刻 | 実施シナリオ |
|---|---|---|
| 1. S0 | base_datetime当日の実時間(=仮想時刻そのまま・D0 11:00前後) | 環境前提・シード投入・収束待ち(≤180秒)・L1無向突合・D-08事前集計 |
| 2. S1前半 | 時刻不動 | #5・#6・#9・#10・#11・#12(DB観察のみ) |
| 3. S1#7 | 時刻不動(debounce 10秒+tick≤60秒の実時間待ちのみ) | PATCH再評価の生成/不生成 |
| 4. S1#8 | **D1 00:05へ前進**(JST 0時跨ぎ) | リセットジョブ+保留キュー再評価(≤180秒待ち)→保留中candidateのproposed昇格観察 |
| 5. S2 | D1 00:05のまま | #13(muted含む)・#14・#17(提案時)・#22・#23・FR-21・#26の前半集計 |
| 6. S3前半 | D1 00:05のまま | #18(4人組全員YES)・通知順序(EX-026記録)・自己重複集合(EX-025)・#12 |
| 7. S3後半#16 | **D+2 06:00へ前進**(response_deadline D+2 05:15通過) | 専用3人組の期限確定観察(sweeper≤180秒) |
| 8. S3末尾 | **D+6 00:10へ前進** | Intent期限切れ(expiredバッチ)観察(D+5対象Intentがexpires_at経過→expired) |
| 9. S4 | D+6 00:10のまま | #15(双方YES→matched・D+7対象で回答可)・#19(チャット)・#21(通報)・D-23(ブロック→cancelled) |
| 10. S5 | D+6のまま | #20(ブロック→候補不生成)・D-10(422 UNDER_AGE試行+EX-004/005非存在再確認) |

Clock操作はハーネス内でControlledClockを直接構築(redis→16379)しset/advanceを呼ぶ(clock_cliと同一コード経路・サブプロセスなし)。**後退は行わない(前進のみ)** — set後のDB行矛盾を避ける作法(ws-1引継ぎ)。前進の各ステップは少なくとも「リセットジョブ・sweeperの1tick(60秒)+余裕」を待ってから観察する。

### 2.5 決定性の担保(検査に固定期待を置く量と置かない量)

- 固定期間OK: match_candidatesの生成集合⊆reference_l1のpass集合(EX個別ペアの実在・failペアの非実在を含む)・group_candidatesの集合構成(EX-025自己重複の非生成)・latches/latch_status_eventsの遷移系列・pushログの本文バイト一致・notificationsのキー集合。
- 固定期間NG(実行ごとに揺れる): シード母集合からどのペアがproposedになるか(D-08枠の競合順序)・#5の「1イベントあたりのJev実行数」の厳密観察(設計§2.5括弧どおり本格検証はws-6・本単位では総量と分布を記録照合する: 起点Intentあたり評価済み候補数の最大≤8+総Jev実行行数≤300×8)。
- #6の期待集合(EX-006/008/010/013/015/017/020の7組実在)は決定的環境(stub embeddingハッシュ・YAML順投入・全起点処理)の下で成立するはずだが、K_v=50の取得順により生成が絞られる可能性が理論上ゼロでないため、**初回フル実行で7組が揃わない場合は検査を緩めず作業停止→報告書に実測と考察**を書く(§0規律)。

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **同意なしユーザーのIntent**(422 CONSENT_REQUIRED): helpers_seedのconsent呼び出し漏れはシード投入全体を壊す。→ test_s0_seed_ingestionがusers/intents/consentの3応答を201/200/200で全数検査する(Task 5)。シナリオユーザーもhelpers_scenarioの作成関数内で必ずconsentを呼ぶ(同Task 5)。
2. **stub判定値の既定値回帰**(ci挙動不変の根拠): gateway変更が既定値でDEFAULT_JEV_RESPONSEと完全一致し続けること。→ test_settings_default_matches_stub_constant(Task 2・既定値バイト一致ピン)+test_llm_settings_are_exactly_eleven_fields(Task 1・フィールド全数)。
3. **D-08保留を介した非決定性**(提案成否に依存する検査をシード母集合に置かない): 事前集計の機械化+専用シナリオ隔離。→ test_s0_d08_retention_forecast(Task 5)+専用シナリオ検査はHS-xxxユーザーのみを対象(Task 7〜9)。
4. **pushログのマルチサービス混在**(docker logsはapi1/api2/worker1/worker2を混ぜる・JSON部分の抽出): 1行パースで最初の`{`から行末までjson.loadsを試みる・失敗行は無視。→ helpers_pushlogの自検査(Task 4・既存ログ行に対する抽出試験)。
5. **m4-seed.yaml再生成の対象日付漏れ**(旧base_datetimeのYAMLを投入しに行く): YAMLのmeta.base_datetimeとDB投入データの整合は、S0の突合(reference_l1判定のnow=meta.base_datetimeとDB candidate集合の一致)が実質的に検証する。加えてS0でYAML meta.base_datetimeを証拠に記録する(Task 5)。実行手順(staging-reset→seed-generate→staging-test)を報告書に再現可能な形で残す(Task 10)。

## 4. スコープ(作成・変更するファイル一覧)

**作成(12ファイル)**:

| ファイル | 内容 |
|---|---|
| `backend/tests/unit/test_llm_stub_response_settings.py` | unit: stub応答がsettings値に従うこと・既定値がDEFAULT_JEV_RESPONSEと一致すること(回帰ピン) |
| `backend/staging_tests/conftest.py` | 証拠収集器+sessionfinish YAML出力・fixtures(API/DB読取)・Clock構築・JWT発行・環境前提確認ヘルパ |
| `backend/staging_tests/helpers_wait.py` | 条件ポーリング(タイムアウト・間隔・原因表示) |
| `backend/staging_tests/helpers_pushlog.py` | pushドライラン記録のdocker logsパース(JSON抽出) |
| `backend/staging_tests/helpers_seed.py` | m4-seed.yaml読込→投入ペイロード変換・投入実行(users→consent→intents・structured明示)・reference_l1無向化突合ヘルパ・収束待ち・compute_mlr |
| `backend/staging_tests/helpers_scenario.py` | 専用シナリオ作成(HS-xxxユーザー+Intent: §2.3の一覧どおり)・実行キャッシュ・my_latch |
| `backend/staging_tests/test_s0_env_seed.py` | 環境前提・シード投入・収束待ち・L1無向突合+D-08事前集計+#20素材ペア作成 |
| `backend/staging_tests/test_s1_candidate_items.py` | #5・#6・#7・#8・#9・#10・#11・#12 |
| `backend/staging_tests/test_s2_proposal_items.py` | #13(muted含む)・#14・#17(提案時)・#22・#23・FR-21・#26 |
| `backend/staging_tests/test_s3_group_deadline_items.py` | #18・#16・通知順序(EX-026)・自己重複集合(EX-025) |
| `backend/staging_tests/test_s4_aftermatch_items.py` | #15・#19・#21・D-23・#17(成立後) |
| `backend/staging_tests/test_s5_safety_items.py` | #20・D-10(422応答記録)・#26(最終集計) |

**変更(5ファイル)**:

| ファイル | 変更内容 |
|---|---|
| `backend/src/latch/settings.py` | 追記のみ: `llm_stub_jev_noul`(既定0.5)・`llm_stub_jev_score`(既定2.0)の2項目(llm_stub_delay_*の直後) |
| `backend/src/latch/llm/gateway.py` | stub構築へのjev_response渡し+`_stub_jev_response`ヘルパ追加+`import copy`+import文の変更(DEFAULT_JEV_RESPONSE追加) |
| `backend/tests/unit/llm/test_llm_factory.py` | 追随: LLM_ENV_VARS+2項目・test_llm_settings_defaults+2行ピン・test_llm_settings_are_exactly_nine_fields→eleven_fields改名+集合更新(9項目→11項目) |
| `compose.staging.yaml` | x-backend-envアンカーへenv 2行追記(`LATCH_LLM_STUB_JEV_NOUL: "0.90"`・`LATCH_LLM_STUB_JEV_SCORE: "3.6"`) |
| `Makefile` | 追記のみ: `staging-reset`・`staging-test`の2ターゲット+.PHONY行への名前追記 |

**作成(成果物)**:

| ファイル | 内容 |
|---|---|
| `docs/plans/M4/ws-3-report.md` | 報告書(§10の形式) |
| `docs/testassets/results/ws3-staging-result-<ts>.yaml` | 証拠(sessionfinish出力・試験ID=02#・実施日・環境・stub設定値・入力条件・合否・観測値) |

## 5. 既存試験への影響表(スーパーバイザー機械突合用・2026-10-03計画時実調査)

| # | 対象試験 | 機械検査の内容(実調査結果) | 本単位による影響 | 追随の要否 |
|---|---|---|---|---|
| 1 | `backend/tests/unit/llm/test_llm_factory.py::test_llm_settings_are_exactly_nine_fields`(86〜104行) | `{f for f in Settings.model_fields if f.startswith("llm_")}`の集合比較(9項目全数) | settings.pyに`llm_stub_jev_noul`/`llm_stub_jev_score`を追加すると**必壊** | **必須追随**(Task 1): test名を`test_llm_settings_are_exactly_eleven_fields`へ改名・集合へ2項目追加・コメント履歴追記(過去のgemini/typesafe追従と同型) |
| 2 | `backend/tests/unit/llm/test_llm_factory.py::test_llm_settings_defaults`(45〜54行) | llm系設定の既定値ピン(llm_mode・鍵空・base_url・delay 3項目) | 新項目の既定値ピンは任意だが追加する | 追加(Task 1): 2行追記(0.5/2.0) |
| 3 | `backend/tests/unit/llm/test_llm_factory.py::LLM_ENV_VARS`(22〜31行) | llm系env変数のdelenv網羅リスト | 新env名(LATCH_LLM_STUB_JEV_NOUL/SCORE)を追加しないとenv汚れが検査に漏れる | **必須追随**(Task 1): タプルへ2項目追加 |
| 4 | `backend/tests/unit/test_settings.py`(全59行) | llm系ピンを**持たない**(settings.py:41-43のllm_stub_delay_*のピンはtest_llm_factory.py側に集約されている慣行) | llm_接頭辞2項目の追加はこの試験のどのassertにも当たらない | **無変更** |
| 5 | `backend/tests/unit/auth/test_auth_settings.py` | `auth_fields`・`redis_fields`のstartswith集合検査(model_fields全数) | `llm_`接頭辞の追加は集合外 | 無傷 |
| 6 | `backend/tests/unit/geo/test_geo_settings.py`(35〜36行) | `geo_fields`のstartswith集合検査 | 同上 | 無傷 |
| 7 | `backend/tests/unit/test_rate_limit_wiring.py::test_all_v1_routes_are_rate_limited` | 全v1ルートのapi_rate_limited構成検査+パス件数Counter比較 | ws-3はroutes/router/main.pyを一切変更しない(compose.staging.yaml・settings.py・gateway.py・staging_testsのみ)ためCounter集合不変 | **無傷**(注: ws-5が`/v1/users/me/consent`のCounter+2行を行う・ws-3側の関与なし) |
| 8 | `backend/tests/unit/llm/test_gateway_*.py`・`test_llm_factory.py`のbuild系試験(`test_factory_builds_working_stub_gateway`・`test_factory_passes_delay_settings_to_stub`等) | StubLLM直接構築 or build_llm_gateway/build_worker_gateway経由。jev応答**値**のassertを持たない(カテゴリ・レイテンシのみ) | gateway変更は既定値(0.5/2.0)=現行定数のため応答値が不変・`StubLLM.__init__`の契約(jev_response引数・Optional・deepcopy)は不変 | 無傷 |
| 9 | `backend/tests/unit/test_worker_jev.py`等matching系・worker系 | in-process app+FakeClock+StubLLM直接構築・gatewayのenv→settings→stub経路の影響外 | 同上(既定値で挙動不変) | 無傷 |
| 10 | `backend/tests/unit/test_arch_no_direct_time.py` | src/latch配下の実時間参照禁止(SRC.rglobのみ) | 変更コード(settings.py・gateway.py)は実時間参照を含まない・staging_testsはSRC外 | 無傷 |
| 11 | `compose.staging.yaml` | テスト対象外(envはstaging起動時のみ読まれる) | アンカー`x-backend-env`への2行追記のみ(§2.4は§Task 3)・YAMLアンカー構造(`<<: *backend-env`のmerge)は維持 | なし(Task 3で`docker compose -f compose.staging.yaml config`検証) |

unit試験の期待件数: **基準実数+5件**(新規test_llm_stub_response_settings.py 5試験・test_llm_factory.pyは改名のみで件数不変)。基準実数はTask 1着手時にworktreeで`make test`を実行して記録する(計画時実測: main=unit 1300件+integration 261件=1561件・2026-10-03)。

## 6. 禁止(触ってはいけないもの・スコープ外と判断する基準)

- `compose.yaml`(ci)・ci系Makefileターゲット(up/down/migrate/test-ci/geo-*/g1-gate等)・`backend/pyproject.toml`・`backend/uv.lock`・`backend/alembic/`・既存テストの実行系(testpaths含む)。**Makefileは新規ターゲット2つの追記と`.PHONY`行への名前追記のみ**(既存行の削除・変更はしない)。レシピ行はハードタブ(スペースに化すと`make`がmissing separatorで失敗・追記後に`make -n staging-reset`で解釈確認する)。
- パイプライン本体(layer1〜layer4・latch_engine・group_engine・worker handlers・sweeper・API契約・routes)。stub応答値の設定経路(settings.py 2項目+gateway.py)以外の製品ロジック変更はスコープ外。
- `StubLLM`本体(`backend/src/latch/llm/stub.py`)は1行も変更しない(jev_response引数は既存・Optional・deepcopy済み)。
- シード生成器(`backend/src/latch/testassets/`配下)・m4-seed.yaml本体構造(実行直前の`make seed-generate`再生成のみ・差分は対象日付)。**m4-seed.yamlはgit commitしない**(対象日付が実施日依存のためworktree内の未コミット差分として扱う・報告書にその旨を記録する)。
- g1/g2資産(g2-jev-goldset.yaml・g1-*.yaml)・frontend・prototype・docs本文(本単位でdocs改版なし・05/08のdocs改版候補はws-5が管理)。
- ws-1のstaging/load資産の構造(compose.staging.yamlへのenv 2行追記を除く)・compose.load.yaml・docker/nginx/。
- **ws-5のスコープ**(並走単位・ws-3では触らない): `backend/alembic/versions/0007_users_consent.py`・`backend/src/latch/users/consent.py`・`users/routes.py`(consentエンドポイント)・`worker/matching/layer1.py`(consent条件)・`tests/unit/test_rate_limit_wiring.py`・frontend一式。ws-3のworktreeにはws-5マージ済みの形で入っており・**ws-3側から変更を加えない**。
- スコープ外と判断する基準: (a)検査の期待値を満たすための検査外コードの変更(ハーネスは観測のみ・DB直書きもしない — SELECT専用接続) (b)9テーブル(users・intents・match_candidates・group_candidates・latches・latch_status_events・notifications・blocks・reports・match_events・messages・geofeatures)へのINSERT/UPDATE/DELETE(ハーネスの書き込みはAPI正規経路のみ) (c)docs本文・設定項目・docs確定値の変更。

## 7. 完了条件(テストで証明できる形。Task 10で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(unit **基準実数+5件**。既存試験への追随はtest_llm_factory.pyのみ〔§5表1〜3〕)。検証: `make lint && make test` — ともにexit 0。全件数を報告書に記録(計画時実測: main=unit 1300件・2026-10-03)
2. **テストファイルbasenameがbackend/tests+staging_tests全体で一意**。検証: `find backend/tests backend/staging_tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし(空)
3. **stub Jev判定値の固定経路がunitピンで担保されている**。検証: `uv run pytest tests/unit/test_llm_stub_response_settings.py tests/unit/llm/test_llm_factory.py -v`(backend配下) — 既定値バイト一致ピン・env上書き経路ピン・build 2関数の渡しピンが全てPASS
4. **`make staging-test`で全シナリオグリーン・証拠YAMLがdocs/testassets/results/へ出力**(02#20項目+追加試験3件+配置ケースの対応表つき)。検証: `make staging-reset`→`make seed-generate ARGS="--base-datetime <実施日翌日11:00 JST>"`→`make seed-verify`→`make staging-test` — 全PASS・証拠YAMLのitemsに02#ID+合否が記録されている
5. **G3繰越02#13〜#23のstaging再実行証拠が回収されている**。検証: 証拠YAMLに#13・#14・#15・#16・#17・#18・#19・#20・#21・#22・#23のIDつきitemsが存在し、報告書のG3対応表と一致
6. **マイグレーションなし(head=0007不変)・依存追加なし**。検証: `git diff main --name-only -- backend/alembic backend/pyproject.toml backend/uv.lock` — 出力なし(空)。staging DBのhead確認はtest_s0_environment_prerequisitesのassert(version_num == "0007")
7. **変更ファイルが§4の一覧どおり**。検証: 報告コミット後に `git diff --name-only main | sort` が§4の一覧(report込み)と完全一致。`git status --short` はm4-seed.yamlの再生成差分のみ(docs/testassets/m4-seed.yaml)
8. **実行手順が報告書に再現可能な形で記録**(staging-resetからのフル手順・証拠YAMLのパス・実施日・base_datetime・stub設定値)
9. **make test-ci(基準実数維持): スーパーバイザー検証待ち**と報告書に記録(agent3は実行しない — §0規律)

---

## 8. 実装ステップ(TDD。Task 1〜10の順で実行する)

Task 1〜3はunit(毎コミットグリーン)で完結する製品コード+ピン。Task 4〜9はstagingハーネス(staging環境の実行検証を伴う)。Task 10はフル実行+成果物。**staging環境のbuild/upは初回に時間がかかる(初回イメージbuild数分)ため、Task 4の着手時に先回りして `make staging-keys && make staging-reset` を1回実行しておくと以降が速い。**

### Task 1: settings新項目2つ(unitピン先行・Red→Green)

**Files:**
- Modify: `backend/src/latch/settings.py`(llm_stub_delay_*の直後・43行目の次)
- Modify: `backend/tests/unit/llm/test_llm_factory.py`(LLM_ENV_VARS・test_llm_settings_defaults・test_llm_settings_are_exactly_nine_fields)
- Test: `backend/tests/unit/test_llm_stub_response_settings.py`(新規)

**Interfaces:**
- Consumes: なし(settings.pyのみ)
- Produces: `Settings.llm_stub_jev_noul: float`(既定0.5)・`Settings.llm_stub_jev_score: float`(既定2.0)。env名は`LATCH_LLM_STUB_JEV_NOUL`/`LATCH_LLM_STUB_JEV_SCORE`(env_prefix自動写像)。Task 2のgatewayがこの2項目を消費する

- [ ] **Step 1: 基準実数の記録**

Run: `cd backend && uv run pytest -m "not integration" -q`
Expected: `1300 passed`(2026-10-03計画時実測・ws-5マージ後のmainなら変わる可能性があるため実測値を報告書に記録する)

- [ ] **Step 2: test_llm_factory.pyの追随を先に書く(Red)**

`backend/tests/unit/llm/test_llm_factory.py`への3か所の変更:

1. `LLM_ENV_VARS`(22〜31行)へ2項目を追記:

```python
LLM_ENV_VARS = (
    "LATCH_LLM_MODE",
    "LATCH_LLM_STUB_DELAY_PARSER_MS",
    "LATCH_LLM_STUB_DELAY_EMBEDDING_MS",
    "LATCH_LLM_STUB_DELAY_JEV_MS",
    "LATCH_LLM_STUB_JEV_NOUL",
    "LATCH_LLM_STUB_JEV_SCORE",
    "LATCH_ANTHROPIC_API_KEY",
    "LATCH_ANTHROPIC_BASE_URL",
    "LATCH_TYPESAFE_API_KEY",
    "LATCH_TYPESAFE_BASE_URL",
)
```

2. `test_llm_settings_defaults`(45〜54行)の末尾に2行を追記:

```python
    assert s.llm_stub_delay_parser_ms == 0
    assert s.llm_stub_delay_embedding_ms == 0
    assert s.llm_stub_delay_jev_ms == 0
    assert s.llm_stub_jev_noul == 0.5  # M4 ws-3(10 §3 stub判定値固定)
    assert s.llm_stub_jev_score == 2.0
```

3. `test_llm_settings_are_exactly_nine_fields`(86〜104行)を下記へ置換(改名+集合更新+コメント履歴):

```python
def test_llm_settings_are_exactly_eleven_fields(monkeypatch):
    # Review Focus #5: timeout・failフラグのenv経路を作らない(design §2.4・§2.6)。
    # LLM系設定はこの11項目のみであることを機械検査する(base_urlはdesign §3.2の
    # supervisor承認済み拡張・ws-6のANTHROPIC_BASE_URL汚染対策。
    # gemini_api_keyはM2 ws-2のsupervisor許可による追従 — 期待値1項目追加のみ。
    # typesafe_api_key/base_urlはM2 ws-5の機械的追随(supervisor承認事項)。
    # llm_stub_jev_noul/scoreはM4 ws-3(10 §3 stub判定値固定)の機械的追随)。
    _clean_settings(monkeypatch)
    llm_fields = {f for f in Settings.model_fields if f.startswith("llm_")}
    assert llm_fields == {
        "llm_mode",
        "llm_anthropic_api_key",
        "llm_anthropic_base_url",
        "llm_gemini_api_key",
        "llm_typesafe_api_key",
        "llm_typesafe_base_url",
        "llm_stub_delay_parser_ms",
        "llm_stub_delay_embedding_ms",
        "llm_stub_delay_jev_ms",
        "llm_stub_jev_noul",
        "llm_stub_jev_score",
    }
```

Run: `cd backend && uv run pytest tests/unit/llm/test_llm_factory.py -v`
Expected: FAIL(2件 — `test_llm_settings_defaults`と`test_llm_settings_are_exactly_eleven_fields`がAttributeError/集合不一致で落ちる)

- [ ] **Step 3: 新規unitファイルを書く(Red)**

`backend/tests/unit/test_llm_stub_response_settings.py`(新規):

```python
"""stub Jev判定値のsettings固定経路(M4 ws-3・10 §3表staging行)。

「Jevスタブで双方向判定値を固定」のenv→settings→StubLLM応答の経路を
機械ピンする。既定値はstub.py DEFAULT_JEV_RESPONSEと値が完全一致する
(回帰ピン — ci/test-ci挙動不変の根拠・design §2.2)。
"""

from datetime import UTC, datetime

from latch.core.clock import FakeClock
from latch.llm.gateway import (
    _stub_jev_response,
    build_llm_gateway,
    build_worker_gateway,
)
from latch.llm.stub import DEFAULT_JEV_RESPONSE
from latch.settings import Settings

LLM_ENV_VARS = (
    "LATCH_LLM_MODE",
    "LATCH_LLM_STUB_DELAY_PARSER_MS",
    "LATCH_LLM_STUB_DELAY_EMBEDDING_MS",
    "LATCH_LLM_STUB_DELAY_JEV_MS",
    "LATCH_LLM_STUB_JEV_NOUL",
    "LATCH_LLM_STUB_JEV_SCORE",
    "LATCH_ANTHROPIC_API_KEY",
    "LATCH_ANTHROPIC_BASE_URL",
    "LATCH_GEMINI_API_KEY",
    "LATCH_TYPESAFE_API_KEY",
    "LATCH_TYPESAFE_BASE_URL",
)

NOUL_KEYS = ("would_a_accept_b", "would_b_accept_a", "latent_yes")
SCORE_KEYS = ("purpose_fit", "mood_fit", "timing_fit", "social_fit")


def _clean_settings(monkeypatch, **overrides) -> Settings:
    for var in LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return Settings(**overrides)


def test_settings_default_matches_stub_constant(monkeypatch):
    """既定値で組んだ応答がDEFAULT_JEV_RESPONSEと完全一致(ci挙動不変の回帰ピン)。"""
    s = _clean_settings(monkeypatch)
    assert _stub_jev_response(s) == DEFAULT_JEV_RESPONSE


def test_settings_env_override_reads_noul_and_score(monkeypatch):
    # staging composeと同一値のenv("0.90"/"3.6")がfloatへ写像されること
    monkeypatch.setenv("LATCH_LLM_STUB_JEV_NOUL", "0.90")
    monkeypatch.setenv("LATCH_LLM_STUB_JEV_SCORE", "3.6")
    s = Settings()
    assert s.llm_stub_jev_noul == 0.9
    assert s.llm_stub_jev_score == 3.6


def test_settings_env_override_full_clean(monkeypatch):
    # LLM_ENV_VARSに新env名が入っていること( delenv網羅)の別経路確認
    for var in LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LATCH_LLM_STUB_JEV_NOUL", "0.7")
    s = Settings()
    assert s.llm_stub_jev_noul == 0.7
    assert s.llm_stub_jev_score == 2.0  # 未設定なら既定


def test_build_llm_gateway_passes_settings_values_to_stub(monkeypatch):
    s = _clean_settings(
        monkeypatch, llm_stub_jev_noul=0.9, llm_stub_jev_score=3.6
    )
    gw = build_llm_gateway(
        FakeClock(datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)), s
    )
    answers = gw._jev._jev_response["answers"]
    for key in NOUL_KEYS:
        assert answers[key]["noul"] == 0.9, key
    for key in SCORE_KEYS:
        assert answers[key]["score"] == 3.6, key
    # 判定値以外はstub定数のまま(設計§2.2 — confidence・model・usageは固定)
    assert answers["purpose_fit"]["confidence"] == 0.5
    assert gw._jev._jev_response["model"] == "jev-1.13.0"
    assert gw._jev._jev_response["usage"] == {
        "input_tokens": 0,
        "output_tokens": 0,
    }


def test_build_worker_gateway_passes_settings_values_to_stub(monkeypatch):
    s = _clean_settings(
        monkeypatch, llm_stub_jev_noul=0.9, llm_stub_jev_score=3.6
    )
    gw = build_worker_gateway(
        FakeClock(datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)), s
    )
    answers = gw._jev._jev_response["answers"]
    for key in NOUL_KEYS:
        assert answers[key]["noul"] == 0.9, key
    for key in SCORE_KEYS:
        assert answers[key]["score"] == 3.6, key
    assert gw._jev is gw._jev_fallback  # stub時は同一インスタンス(design §2.8)
```

Run: `cd backend && uv run pytest tests/unit/test_llm_stub_response_settings.py -v`
Expected: FAIL 5件(全て`_stub_jev_response`が未定義でImportError→collection error)

- [ ] **Step 4: settings.pyへ2項目を追加(Green)**

`backend/src/latch/settings.py`の`llm_stub_delay_jev_ms`(43行)の直後に追記:

```python
    llm_stub_delay_parser_ms: int = 0
    llm_stub_delay_embedding_ms: int = 0
    llm_stub_delay_jev_ms: int = 0
    # stub Jev判定値の固定(10 §3「Jevスタブで双方向判定値を固定」・M4 ws-3)。
    # noul=would_a/would_b/latent_yesの3キー共通値・score=4軸(purpose/mood/
    # timing/social)共通値。既定=stub.py DEFAULT_JEV_RESPONSEの定数と同一
    # (ci/test-ci挙動不変・回帰ピンはtest_llm_stub_response_settings.py)。
    # confidence(0.5)・model・usageはstub定数のまま・env出し分け経路は作らない
    llm_stub_jev_noul: float = 0.5
    llm_stub_jev_score: float = 2.0
```

Run: `cd backend && uv run pytest tests/unit/llm/test_llm_factory.py tests/unit/test_llm_stub_response_settings.py tests/unit/test_settings.py -v`
Expected: PASS(test_llm_stub_response_settings.pyの5件は`_stub_jev_response`がまだないので4件PASS・build 2件はまだFAIL → この時点でStep 5へ)

- [ ] **Step 5: Task 2の実装へ続く(本タスクはここでunit全体を確認のみ)**

Run: `cd backend && uv run pytest -m "not integration" -q`
Expected: `1305 passed`(基準+5) — ただしTask 2の`_stub_jev_response`未実装のためtest_llm_stub_response_settings.pyはImportErrorになる。**TDDの分離のため、Task 1のコミットはStep 2〜4のみ行い・test_llm_stub_response_settings.pyはTask 2コミットまで収集から外れないためにTask 2と連続でコミットする。よってTask 1単体ではコミットせず、Task 2のStep 4(コミット)で一緒に載せる。**

(備考: Red→Greenの粒度を保つため、Task 1とTask 2は同一のコミット系列で行う。Step 2〜3(Red確認)→Step 4(settings・Green候補)→Task 2のgateway実装→全Green→コミット)

### Task 2: gateway stub分岐(設定値からjev_responseを組む・TDD Green→コミット)

**Files:**
- Modify: `backend/src/latch/llm/gateway.py`(import 2行・ヘルパ関数1つ・build 2関数のstub構築1行ずつ)

**Interfaces:**
- Consumes: `Settings.llm_stub_jev_noul`/`llm_stub_jev_score`(Task 1)
- Produces: `build_llm_gateway`/`build_worker_gateway`がstub構築時にsettings値由来のjev_responseを渡す(stagingのworker/apiがenv値0.90/3.6のstub Jevで動く)。`_stub_jev_response(settings) -> dict`(単一責務のヘルパ・unitピンから参照される)

- [ ] **Step 1: gateway.pyのimport変更**

現在の34行目:

```python
from latch.llm.stub import StubLLM
```

を下記へ変更(かつ13行目付近のimport群に`import copy`を追加 — 標準ライブラリは`import asyncio`の直後に置く・ruff Iルールの並び順に従う):

```python
import asyncio
import copy
```

```python
from latch.llm.stub import DEFAULT_JEV_RESPONSE, StubLLM
```

- [ ] **Step 2: `_stub_jev_response`ヘルパを追加**

`build_llm_gateway`(306行)の直前に追加:

```python
_NOUL_KEYS = ("would_a_accept_b", "would_b_accept_a", "latent_yes")
_SCORE_KEYS = ("purpose_fit", "mood_fit", "timing_fit", "social_fit")


def _stub_jev_response(settings: Settings) -> dict:
    """settingsのstub判定値からDEFAULT_JEV_RESPONSE同一形状の応答を組む(ws-3)。

    10 §3表staging行「Jevスタブで双方向判定値を固定」のenv→settings→
    stub応答の経路。既定値(0.5/2.0)のときはDEFAULT_JEV_RESPONSEと値が
    完全一致する(ci/test-ci挙動不変・回帰ピン:
    tests/unit/test_llm_stub_response_settings.py)。confidence・model・
    usageはstub定数のまま — 応答形状の知識はこの1関数にのみ集約する
    (design §2.2案A・envへJSON全体を差し込む方式は不採用)。
    渡した先のStubLLMがdeepcopyするためこの辞書を消費者へ露出しない。
    """
    response = copy.deepcopy(DEFAULT_JEV_RESPONSE)
    for key in _NOUL_KEYS:
        response["answers"][key]["noul"] = settings.llm_stub_jev_noul
    for key in _SCORE_KEYS:
        response["answers"][key]["score"] = settings.llm_stub_jev_score
    return response
```

- [ ] **Step 3: build 2関数のstub構築にjev_response渡しを追加**

`build_llm_gateway`(320〜324行)と`build_worker_gateway`(355〜359行)の両方で、stub構築を下記へ変更(2関数とも同じ形):

```python
    stub = StubLLM(
        delay_parser_ms=settings.llm_stub_delay_parser_ms,
        delay_embedding_ms=settings.llm_stub_delay_embedding_ms,
        delay_jev_ms=settings.llm_stub_delay_jev_ms,
        jev_response=_stub_jev_response(settings),
    )
```

(備考: stub構築は両関数とも分岐の外の共通箇所にある。ここへ渡すことでstub分岐・real分岐のstub Jevの両方が設定値に従う — real構成のstub JevはAPIプロセス限定(design §2.7)でstagingはllm_mode=stubのため0.90が入るのはstagingのみ。既定値環境(ci)は0.5/2.0で挙動不変。この設計位置はsupervisor承認事項2の趣旨(stub応答がenv→settingsで決まる)を満たす・報告書に記録する)

- [ ] **Step 4: unit全緑を確認してコミット**

Run: `cd backend && uv run pytest tests/unit/llm/ tests/unit/test_llm_stub_response_settings.py tests/unit/test_settings.py -v`
Expected: PASS(全件・test_llm_stub_response_settings.py 5件込み)

Run: `cd backend && uv run pytest -m "not integration" -q && uv run ruff format --check . && uv run ruff check .`
Expected: `1305 passed`・lintグリーン

Run: `find backend/tests backend/staging_tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d`
Expected: 空

```bash
git add backend/src/latch/settings.py backend/src/latch/llm/gateway.py backend/tests/unit/llm/test_llm_factory.py backend/tests/unit/test_llm_stub_response_settings.py
git commit -m "feat: add llm_stub_jev_noul/score settings and stub response wiring"
```

### Task 3: compose.staging.yaml env追記+Makefile追記(staging-reset/staging-test)

**Files:**
- Modify: `compose.staging.yaml`(x-backend-envアンカー内・16行目`LATCH_LLM_MODE: stub`の直後)
- Modify: `Makefile`(.PHONY行+staging-loadセクションのseed-generate直前あたりに2ターゲット追記)

**Interfaces:**
- Consumes: Task 1のenv名(LATCH_LLM_STUB_JEV_NOUL/SCORE)
- Produces: `make staging-reset`(フルリセット)・`make staging-test`(ハーネス全実行)。Task 4以降とTask 10が使う

- [ ] **Step 1: compose.staging.yamlへenv 2行を追記**

`x-backend-env: &backend-env`アンカー内(8〜19行)の`LATCH_LLM_MODE: stub`の直後に追記する。全体は下記のとおり(追記後):

```yaml
x-backend-env: &backend-env
  # 全サービスで接続envを網羅(既知欠陥の再発防止 — design §1.4)。
  # api/workerともこの単一アンカーからmergeする
  LATCH_APP_ENV: staging
  LATCH_DATABASE_URL: postgresql+asyncpg://latch:latch@db:5432/latch
  LATCH_REDIS_URL: redis://redis:6379/0
  LATCH_PUBSUB_EMULATOR_HOST: pubsub:8085
  LATCH_PUBSUB_PROJECT_ID: latch-staging
  LATCH_LLM_MODE: stub
  # stub Jev判定値の固定(10 §3表staging行「Jevスタブで双方向判定値を
  # 固定」・M4 ws-3。latch_score=C×0.90=0.90で提案閾値0.60を超え・
  # match_level=high(≥0.90)経路も通る)
  LATCH_LLM_STUB_JEV_NOUL: "0.90"
  LATCH_LLM_STUB_JEV_SCORE: "3.6"
  LATCH_PUSH_MODE: stub
  LATCH_CLOCK_MODE: controlled
  LATCH_AUTH_TESTKEYS_DIR: /keys
```

(他の行は変更しない。アンカーは1箇所のみ — api/worker両方へ`<<: *backend-env`のmergeで波及する)

- [ ] **Step 2: Makefileへ2ターゲットを追記**

`.PHONY`行の`seed-generate seed-verify`を`seed-generate seed-verify staging-reset staging-test`へ変更(追記のみ・既存の名前は触らない)。`seed-verify`ターゲットの直後に追記(**レシピ行はハードタブ**):

```makefile
staging-reset: ## staging環境をフルリセット(down -v→up→migrate→geo-import。latch-stagingボリュームのみ削除・ci無干渉。ハーネス実行の前提)
	$(STAGING_COMPOSE) down -v
	$(STAGING_COMPOSE) up -d --wait
	$(MAKE) staging-migrate
	$(MAKE) staging-geo-import

staging-test: ## staging機能試験ハーネス全実行(M4 ws-3・staging-reset直後のフル実行が前提・証拠YAMLをdocs/testassets/results/へ出力)
	cd backend && uv run pytest staging_tests
```

- [ ] **Step 3: 構文・解釈の確認**

Run: `docker compose -f compose.staging.yaml config >/dev/null && echo OK`
Expected: OK(YAML構文・アンカー解決の確認)

Run: `make -n staging-reset && make -n staging-test`
Expected: 4行+1行のコマンドエコー(missing separator等が出ない=タブ正常)

Run: `cd backend && uv run python -c "from latch.settings import Settings; s=Settings(); print(s.llm_stub_jev_noul, s.llm_stub_jev_score)"`
Expected: `0.5 2.0`(既定・env無し)

Run: `cd backend && make lint && make test -n 2>/dev/null >/dev/null; make lint`
Expected: lintグリーン(製品コード変更はないが確認)

- [ ] **Step 4: コミット**

```bash
git add compose.staging.yaml Makefile
git commit -m "chore: add staging stub jev env and staging-reset/staging-test targets"
```

### Task 4: staging_tests土台(conftest+helpers_wait)+staging環境の受け入れ確認

**Files:**
- Create: `backend/staging_tests/conftest.py`
- Create: `backend/staging_tests/helpers_wait.py`
- Create: `backend/staging_tests/helpers_pushlog.py`

**Interfaces:**
- Consumes: staging環境(make staging-up・18000/15432/16379)・`.staging/keys/idp_test_private.pem`
- Produces(Task 5〜9が使う):
  - `helpers_wait.wait_until(cond, *, timeout_s, interval_s=5.0, what="condition") -> None`(asynccond・上限化・失敗時what入りAssertionError)
  - `conftest.API_BASE`/`DSN`/`REDIS_URL`/`KEYS_DIR`/`REPO_ROOT`/`EXPECTED_HEAD`
  - `conftest.fixture api`(httpx AsyncClient・base_url=18000・timeout 30s)
  - `conftest.fixture db`(asyncpg接続・`SET default_transaction_read_only = on`・functionスコープ)
  - `conftest.record_item(*, item_id, name, test, verdict, observed)`(証拠1件)
  - `conftest.get_clock() -> ControlledClock`(16379・キャッシュ)
  - `conftest.issue_token(subject) -> str`(google・staging鍵)
  - `conftest.push_log_records(*, tail=5000) -> list[dict]`(docker logs→JSON抽出)
  - `conftest.ensure_env_prerequisites(db)`(head=0007・geo・鍵の名前つき失敗)

- [ ] **Step 1: helpers_wait.pyを作る**

```python
"""条件ポーリング(M4 ws-3・設計§2.4)。

待ち上限はtick(60秒)の2〜3倍=120〜180秒で呼び出す。タイムアウトは
what(何を待っていたか)入りで失敗する。pytestタイムアウトプラグインは
使わない(依存追加禁止・全待機をこの関数で上限化)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic  # 待ち上限計測のみ・arch testはsrc/latchが対象(設計§4-6)


async def wait_until(
    cond: Callable[[], Awaitable[bool]],
    *,
    timeout_s: float,
    interval_s: float = 5.0,
    what: str = "condition",
) -> None:
    """async条件が成立するまでinterval_s間隔でポーリングする。上限で失敗。"""
    deadline = monotonic() + timeout_s
    while True:
        if await cond():
            return
        if monotonic() >= deadline:
            raise AssertionError(f"タイムアウト({timeout_s}s): {what}")
        await asyncio.sleep(interval_s)
```

- [ ] **Step 2: helpers_pushlog.pyを作る**

```python
"""pushドライラン記録の読取(M4 ws-3・引用確定値#16)。

push_mode=stubはロガー'latch.push.send'へJSON 1行で記録され、取得経路は
コンテナログのみ(notifications/records.py)。docker compose logsは
api1/api2/worker1/worker2の行を混ぜて出すため、行中で最初の`{`から行末を
json.loadsで試み・PushSendRecordの形(notification_type+title+bodyを持つ
dict)のみを採る。構造化ログの他行(latch.llm.send等)はnotification_typeを
持たないため自然に除外される。
"""

from __future__ import annotations

import json
import subprocess

from conftest import REPO_ROOT


def push_log_records(*, tail: int = 5000) -> list[dict]:
    """latch-stagingのコンテナログからpush記録(dict)のみを抽出する。"""
    out = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "compose.staging.yaml",
            "logs",
            "--tail",
            str(tail),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    records: list[dict] = []
    for line in out.splitlines():
        idx = line.find("{")
        if idx < 0:
            continue
        try:
            obj = json.loads(line[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and {"notification_type", "user_id", "body"} <= set(obj):
            records.append(obj)
    return records
```

- [ ] **Step 3: conftest.pyを作る**

```python
"""stagingハーネス共通(M4 ws-3・10 §1のstaging観測経路)。

tests/配下ではなく独立ディレクトリ(pyprojectのtestpaths=["tests"]の外側)
のため既存のmake test/test-ciからは収集されない。実行: `cd backend &&
uv run pytest staging_tests`(前提: make staging-resetからのフル起動)。
session状態はモジュール辞書+ヘルパー関数で持つ(sessionスコープのasync
fixtureを使わない — pytest-asyncioのloop scope結合を避ける)。
"""

from __future__ import annotations

from pathlib import Path

import asyncpg
import httpx
import pytest
import yaml

from latch.auth.tools import issue_idp_token
from latch.core.clock import ControlledClock

REPO_ROOT = Path(__file__).resolve().parents[2]  # backend/staging_tests → repoルート
API_BASE = "http://127.0.0.1:18000"  # nginx(設計§2.3観測経路(a))
DSN = "postgresql://latch:latch@127.0.0.1:15432/latch"  # DB読取(SELECT専用)
REDIS_URL = "redis://127.0.0.1:16379/0"  # ControlledClock
KEYS_DIR = REPO_ROOT / ".staging" / "keys"
SEED_PATH = REPO_ROOT / "docs" / "testassets" / "m4-seed.yaml"
RESULTS_DIR = REPO_ROOT / "docs" / "testassets" / "results"
EXPECTED_HEAD = "0007"  # ws-5が0007を追加した後のhead(supervisor裁定2026-10-03)

# --- 証拠収集(10 §5の形式・sessionfinishでYAML出力) ---

_results: list[dict] = []


def record_item(*, item_id: str, name: str, test: str, verdict: str, observed: dict) -> None:
    """証拠1件を収集する(試験ID・入力条件・合否・観測値)。"""
    _results.append(
        {"id": item_id, "name": name, "test": test, "verdict": verdict, "observed": observed}
    )


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001
    """session終了時に証拠YAMLを出力する(失敗時のitemsも含めて出す)。"""
    from datetime import datetime, timedelta, timezone

    jst = timezone(timedelta(hours=9))
    now = datetime.now(jst)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = {
        "unit": "M4-ws-3",
        "executed_at": now.isoformat(),
        "environment": "latch-staging",
        "items": _results,
    }
    ts = now.strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"ws3-staging-result-{ts}.yaml"
    path.write_text(
        yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


# --- fixtures(functionスコープ・sessionスコープasyncは使わない) ---

@pytest.fixture
async def api():
    async with httpx.AsyncClient(base_url=API_BASE, timeout=30.0) as c:
        yield c


@pytest.fixture
async def db():
    conn = await asyncpg.connect(DSN)
    await conn.execute("SET default_transaction_read_only = on")
    try:
        yield conn
    finally:
        await conn.close()


# --- 共通ヘルパ(キャッシュはモジュール辞書) ---

_clock = None


async def get_clock() -> ControlledClock:
    """staging Redis基準の共有制御Clock(clock_cliと同一コード経路)。"""
    global _clock
    if _clock is None:
        import redis as redis_sync

        _clock = ControlledClock(
            redis_sync.Redis.from_url(REDIS_URL, decode_responses=True)
        )
    return _clock


def issue_token(subject: str) -> str:
    """staging鍵でシード/シナリオユーザーのJWTを発行する(ws-1と同一経路)。"""
    pem = (KEYS_DIR / "idp_test_private.pem").read_bytes()
    return issue_idp_token(provider="google", subject=subject, private_key_pem=pem)


def auth_header(subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(subject)}"}


async def ensure_env_prerequisites(db) -> None:  # noqa: ANN001
    """環境前提の名前つき失敗(設計§4-2 — 原因不明の連鎖失敗を防ぐ)。"""
    # API疎通はfixture apiの初期接続エラーで判るためここではDB/geo/鍵/headのみ
    ver = await db.fetchval("SELECT version_num FROM alembic_version")
    if ver != EXPECTED_HEAD:
        raise AssertionError(
            f"alembic headが{EXPECTED_HEAD}ではありません(実際={ver})。"
            "ws-5マージ後のmainからworktreeを作り・make staging-reset(または"
            "staging-migrate)を再実行してください"
        )
    n_geo = await db.fetchval("SELECT count(*) FROM geofeatures")
    if not n_geo:
        raise AssertionError(
            "geofeaturesが空です。make staging-geo-importを実行してください"
        )
    if not (KEYS_DIR / "idp_test_private.pem").exists():
        raise AssertionError(".staging/keysがありません。make staging-keysを実行")
```

(注意: `helpers_pushlog.py`は`from conftest import REPO_ROOT`でconftestの定数を参照する。pytestはルートディレクトリをsys.pathへ載せるため、staging_tests内のモジュールはこの形で相互importできる — conftest.pyはディレクトリ内モジュールとして解決される。うまくいかない場合(ImportError)は、REPO_ROOT等の定数を`helpers_common.py`へ置きconftestと両方からimportする形に直してよい(その場合は報告書に記録する)。)

- [ ] **Step 4: staging環境を起動して受け入れ確認**

Run: `make staging-keys && make staging-reset`
Expected: down -v→up(healthy待ちでexit 0)→migrate(head=0007)→geo-import(exit 0)

Run: `cd backend && uv run pytest staging_tests -q`(まだ試験ファイルがないため)
Expected: `no tests ran`(エラーなし・collection可能=staging_testsがpytestの対象として解釈されている)

Run: `cd backend && uv run python -c "import sys; sys.path.insert(0,'staging_tests'); import helpers_wait; print('ok')"`
Expected: ok

- [ ] **Step 5: コミット**

```bash
git add backend/staging_tests/conftest.py backend/staging_tests/helpers_wait.py backend/staging_tests/helpers_pushlog.py
git commit -m "test: add staging harness base (conftest, wait, pushlog helpers)"
```

### Task 5: シード投入+L1無向突合+S0(test_s0_env_seed.py完結)

**Files:**
- Create: `backend/staging_tests/helpers_seed.py`
- Create: `backend/staging_tests/helpers_scenario.py`
- Create: `backend/staging_tests/test_s0_env_seed.py`

**Interfaces:**
- Consumes: Task 4のconftest全ヘルパ・`reference_l1`(judge_all)・m4-seed.yaml
- Produces(Task 6〜9が使う):
  - `helpers_seed.seed_all(api, db) -> dict`(1回目: 投入+consent+収束待ち+突合基盤計算。2回目以降: キャッシュ即返し)。キー: `meta_base`/`su_to_uuid`/`sd_to_uuid`/`l1_pass`(set[(sd_a, sd_b)]・idソート済み)/`ex_pass_sample`/`pass_pairs_per_user_day`/`users_ok`/`consent_ok`/`intents_ok`
  - `helpers_seed.fetch_candidates_pairs(db, sd_to_uuid) -> set[tuple[str,str]]`(DB match_candidatesの無向化集合・シードペアのみ)
  - `helpers_seed.latch_rows(db, sql_args...)`ではなく検査側が直接asyncpg SQLを書く(シンプルさ優先・下記Task 6〜9のSQLをそのまま使う)
  - `helpers_scenario.make_scenario_pair(api, db, *, users, when, name, category="activity", visibility="summary_only", notification_level="proposals_only", participants=(2,2), location="天文館", alcohol_involved=False) -> dict`(HS-xxxユーザー登録+consent+Intent作成・キャッシュつき)
  - `helpers_scenario.make_scenario_group(api, db, *, users, when, name, min_participants=3) -> list[dict]`

- [ ] **Step 1: helpers_seed.pyを作る**

```python
"""シード投入と突合基盤(M4 ws-3・引用確定値#12・ws-2引継ぎ)。

投入順序はusers→consent→intents(ws-5実装後は同意なしで422 CONSENT_REQUIRED
になるためconsentを先に済ませる)。intended_reject_cases(EX-029〜033)は
投入しない — S5で422試行素材として使う。reference_l1の判定はYAML文字列を
内部表現(birth_date=date・time=datetime)へ変換してから行う(verify.py同型)。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import yaml

from latch.testassets.reference_l1 import judge_all

from conftest import SEED_PATH, ensure_env_prerequisites, auth_header

JST = timezone(timedelta(hours=9))

EX_PASS_SAMPLE = (
    "EX-006",
    "EX-008",
    "EX-010",
    "EX-013",
    "EX-015",
    "EX-017",
    "EX-020",  # 設計§2.5 #6の材料
)

_SEED_STATE: dict | None = None


def _p(value: str) -> datetime:
    return datetime.fromisoformat(value)


def load_seed() -> dict:
    return yaml.safe_load(SEED_PATH.read_text(encoding="utf-8"))


def to_reference_view(seed: dict) -> tuple[dict, dict]:
    """reference_l1用の内部表現(users: birth_date=date実体・intents: time=datetime実体)。"""
    users = {
        u["id"]: {**u, "birth_date": _p(u["birth_date"]).date()} for u in seed["users"]
    }
    intents = []
    for i in seed["intents"]:
        t = i["structured"]["time"]
        intents.append(
            {
                "id": i["id"],
                "user": i["user"],
                "structured": {
                    **i["structured"],
                    "time": {
                        "start": _p(t["start"]),
                        "end": _p(t["end"]) if t.get("end") else None,
                    },
                },
            }
        )
    return users, intents


def _seed_intent_payload(i: dict) -> dict:
    """POST /v1/intentsのbody(05 §5・structured_intent明示・引用確定値#13)。"""
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


async def _n_created_processed(db) -> int:  # noqa: ANN001
    return await db.fetchval(
        "SELECT count(*) FROM match_events"
        " WHERE event_type='created' AND status='processed'"
    )


async def _evaluated_count(db) -> int:  # noqa: ANN001
    return await db.fetchval(
        "SELECT count(*) FROM match_candidates WHERE jev_result IS NOT NULL"
    )


async def wait_pipeline_settled(db) -> None:  # noqa: ANN001
    """created Event 300件処理+Layer1〜5の収束待ち(設計§2.3・上限180秒+安定確認)。"""
    from time import monotonic

    import asyncio

    n = await _n_created_processed(db)
    assert n == 300, f"created processedが300でない: {n}(投入直後でなければリセット)"

    # 安定確認(評価行数が10秒間隔で2回連続不変になるまで・上限90秒)
    prev = await _evaluated_count(db)
    deadline = monotonic() + 90
    while True:
        if monotonic() > deadline:
            raise AssertionError("タイムアウト(90s): match_candidates評価行の安定")
        await asyncio.sleep(10)
        cur = await _evaluated_count(db)
        if cur == prev:
            return
        prev = cur


async def seed_all(api, db) -> dict:  # noqa: ANN001
    """シード投入(users→consent→intents)+収束待ち。2回目以降はキャッシュ。"""
    global _SEED_STATE
    if _SEED_STATE is not None:
        return _SEED_STATE

    await ensure_env_prerequisites(db)
    seed = load_seed()
    meta_base = seed["meta"]["base_datetime"]

    su_to_uuid: dict[str, str] = {}
    users_ok = 0
    consent_ok = 0
    for u in seed["users"]:
        body = {
            "display_name": u["display_name"],
            "birth_date": u["birth_date"],
            "profile": {"bio": (u.get("profile") or {}).get("bio")},
        }
        r = await api.post(
            "/v1/users", headers=auth_header(u["id"]), json=body
        )
        assert r.status_code == 201, f"users投入失敗 {u['id']}: {r.status_code} {r.text}"
        su_to_uuid[u["id"]] = r.json()["user"]["id"]
        users_ok += 1
        # ws-5実装後は同意なしだとIntentが422 CONSENT_REQUIREDで遮断される
        # ため全員を同意済みにする(スーパーバイザー引継ぎ2・期待値表は不変)
        rc = await api.post("/v1/users/me/consent", headers=auth_header(u["id"]))
        assert rc.status_code == 200, f"consent失敗 {u['id']}: {rc.status_code} {rc.text}"
        consent_ok += 1

    sd_to_uuid: dict[str, str] = {}
    intents_ok = 0
    for i in seed["intents"]:
        r = await api.post(
            "/v1/intents",
            headers=auth_header(i["user"]),
            json=_seed_intent_payload(i),
        )
        assert r.status_code == 201, f"intents投入失敗 {i['id']}: {r.status_code} {r.text}"
        sd_to_uuid[i["id"]] = r.json()["intent"]["id"]
        intents_ok += 1

    await wait_pipeline_settled(db)

    # reference_l1の全ペア判定(now=base_datetime・ws-2の突合基準)
    users, intents = to_reference_view(seed)
    judged = judge_all(intents, users, now=_p(meta_base))
    l1_pass = {k for k, (ok, _reason) in judged.items() if ok}

    # D-08事前集計(設計§2.2対抗策(c)・計画時実測: 6件超え74件)
    intent_user = {i["id"]: i["user"] for i in intents}
    per_user_day: dict[tuple[str, str], int] = {}
    for (a, b) in l1_pass:
        day = next(i for i in intents if i["id"] == a)["structured"]["time"]["start"].date().isoformat()
        for iid in (a, b):
            key = (intent_user[iid], day)
            per_user_day[key] = per_user_day.get(key, 0) + 1

    _SEED_STATE = {
        "meta_base": meta_base,
        "su_to_uuid": su_to_uuid,
        "sd_to_uuid": sd_to_uuid,
        "l1_pass": l1_pass,
        "ex_pass_sample": set(EX_PASS_SAMPLE),
        "pass_pairs_per_user_day": per_user_day,
        "users_ok": users_ok,
        "consent_ok": consent_ok,
        "intents_ok": intents_ok,
    }
    return _SEED_STATE


async def fetch_candidates_pairs(db, sd_to_uuid: dict[str, str]) -> set[tuple[str, str]]:  # noqa: ANN001
    """DB match_candidatesの無向化集合(シードペアのみ・設計§2.2対称性注記)。"""
    uuid_to_sd = {v: k for k, v in sd_to_uuid.items()}
    rows = await db.fetch("SELECT intent_a_id, intent_b_id FROM match_candidates")
    result: set[tuple[str, str]] = set()
    for r in rows:
        a = uuid_to_sd.get(str(r["intent_a_id"]))
        b = uuid_to_sd.get(str(r["intent_b_id"]))
        if a is None or b is None:
            continue  # 専用シナリオペアはシード突合の対象外
        result.add(tuple(sorted((a, b))))
    return result


def ex_pair_ids(seed: dict, ex_id: str) -> tuple[str, ...] | None:
    """expectations.casesからEX-xxxのintents(idタプル・ソート済み)を引く。"""
    for case in seed["expectations"]["cases"]:
        if case["id"] == ex_id:
            return tuple(sorted(case["intents"]))
    return None
```

- [ ] **Step 2: helpers_scenario.pyを作る**

```python
"""専用シナリオ作成(M4 ws-3・設計§2.2対抗策(b)/§2.3隔離方式)。

シード母集合の提案群はD-08保留で揺れるため、提案の成否・状態遷移・通知内容
に依存する検査はHS-xxx専用ユーザー+早朝時刻(D+1〜D+7の06:30)のIntentで
行う(§2.3一覧どおり)。2回目以降の呼び出しはキャッシュで即返す(部分実行
はstaging-resetからのやり直しを前提とする・冪等な再入は行わない)。
"""

from __future__ import annotations

from conftest import auth_header

_SCENARIOS: dict[str, dict] = {}


async def _register(api, subject: str, birth_date: str) -> str:  # noqa: ANN001
    r = await api.post(
        "/v1/users",
        headers=auth_header(subject),
        json={"display_name": f"ハーネス{subject}", "birth_date": birth_date},
    )
    assert r.status_code == 201, f"scenario users失敗 {subject}: {r.status_code} {r.text}"
    user_id = r.json()["user"]["id"]
    rc = await api.post("/v1/users/me/consent", headers=auth_header(subject))
    assert rc.status_code == 200, f"scenario consent失敗 {subject}: {rc.status_code}"
    return user_id


async def _create_intent(  # noqa: ANN001
    api,
    subject: str,
    *,
    when: str,
    category: str = "activity",
    secondary: str = "散策",
    visibility: str = "summary_only",
    notification_level: str = "proposals_only",
    participants: tuple[int, int] = (2, 2),
    location: str = "天文館",
    alcohol_involved: bool = False,
    budget_max: int | None = 3000,
    raw_text: str | None = None,
) -> str:
    body = {
        "raw_text": raw_text or f"{when}に{location}で{secondary}。よろしくお願いします。",
        "status": "active",
        "structured_intent": {
            "category": {"primary": category, "secondary": secondary},
            "alcohol_involved": alcohol_involved,
            "time": {"start": when},
            "location": {"name": location, "radius_m": 1000},
            "budget": {"max": budget_max} if budget_max is not None else None,
            "participants": {"min": participants[0], "max": participants[1]},
            "soft_constraints": [],
            "visibility": visibility,
            "notification_level": notification_level,
        },
    }
    r = await api.post("/v1/intents", headers=auth_header(subject), json=body)
    assert r.status_code == 201, f"scenario intents失敗 {subject}: {r.status_code} {r.text}"
    return r.json()["intent"]["id"]


async def make_scenario_pair(
    api,
    db,
    *,
    key: str,
    when: str,
    name: str,
    **kwargs,  # noqa: ANN003
) -> dict:
    """2人のユーザーを登録し各1件のIntentを作る(キャッシュつき)。"""
    global _SCENARIOS
    if key in _SCENARIOS:
        return _SCENARIOS[key]
    birth = kwargs.pop("birth_date", "1995-04-01")
    s1 = f"HS-{name}-1"
    s2 = f"HS-{name}-2"
    u1 = await _register(api, s1, birth)
    u2 = await _register(api, s2, birth)
    i1 = await _create_intent(api, s1, when=when, **kwargs)
    i2 = await _create_intent(api, s2, when=when, **kwargs)
    _SCENARIOS[key] = {
        "key": key,
        "subjects": [s1, s2],
        "user_ids": [u1, u2],
        "intent_ids": [i1, i2],
    }
    return _SCENARIOS[key]


async def make_scenario_group(
    api,
    db,
    *,
    key: str,
    when: str,
    name: str,
    members: int = 4,
    min_participants: int = 3,
    **kwargs,  # noqa: ANN003
) -> dict:
    """members人のユーザー×1件ずつ(集合クラスタ・min_participants≥3)。"""
    global _SCENARIOS
    if key in _SCENARIOS:
        return _SCENARIOS[key]
    birth = kwargs.pop("birth_date", "1995-04-01")
    subjects = [f"HS-{name}-{n}" for n in range(1, members + 1)]
    user_ids = []
    for s in subjects:
        user_ids.append(await _register(api, s, birth))
    intent_ids = []
    for s in subjects:
        intent_ids.append(
            await _create_intent(
                api, s, when=when, participants=(min_participants, members), **kwargs
            )
        )
    _SCENARIOS[key] = {
        "key": key,
        "subjects": subjects,
        "user_ids": user_ids,
        "intent_ids": intent_ids,
    }
    return _SCENARIOS[key]
```

- [ ] **Step 3: test_s0_env_seed.pyを作る**

```python
"""S0: 環境前提・シード投入・収束待ち・L1無向突合+D-08事前集計(設計§2.3)。

ここが落ちたら後続(S1〜S5)は全て連鎖失敗する — 設計§4-2どおり名前つき
失敗で原因を明示する。
"""

from __future__ import annotations

from conftest import EXPECTED_HEAD, record_item
from helpers_seed import ex_pair_ids, fetch_candidates_pairs, load_seed, seed_all


async def test_s0_environment_prerequisites(api, db):
    await seed_all(api, db)  # ensure_env_prerequisites込み(head=0007・geo・鍵)
    r = await api.get("/health")
    assert r.status_code == 200, "staging API(18000)が疎通していません(make staging-up)"
    record_item(
        item_id="S0-env",
        name="環境前提(8サービス・head・geo・鍵)",
        test="test_s0_environment_prerequisites",
        verdict="pass",
        observed={"alembic_head": EXPECTED_HEAD, "health": r.json()["status"]},
    )


async def test_s0_seed_ingestion(api, db):
    state = await seed_all(api, db)
    assert state["users_ok"] == 100, "users投入が100人でない"
    assert state["consent_ok"] == 100, "consent付与が100人でない"
    assert state["intents_ok"] == 300, "intents投入が300件でない"
    record_item(
        item_id="S0-seed",
        name="シード投入(100人×consent+300 Intent・422/429=0)",
        test="test_s0_seed_ingestion",
        verdict="pass",
        observed={"base_datetime": state["meta_base"]},
    )


async def test_s0_l1_unidirectional_match(api, db):
    state = await seed_all(api, db)
    seed = load_seed()
    db_pairs = await fetch_candidates_pairs(db, state["sd_to_uuid"])
    outside = db_pairs - state["l1_pass"]
    assert not outside, (
        f"L1 failペアの生成が{len(outside)}件(無向化突合)。例: {sorted(outside)[:5]}"
    )
    # 代表EX passペアは収束待ち後に実在する(K_v=50余裕・決定的環境)。
    # もし1組でも欠けたら検査を緩めず§0規律どおり作業停止(報告書に実測と考察)
    sample_pairs = {ex_pair_ids(seed, e) for e in state["ex_pass_sample"]}
    missing = {pair for pair in sample_pairs if pair is None or pair not in db_pairs}
    assert not missing, f"代表EX passペア未生成: {sorted(str(p) for p in missing)}(§0規律どおり作業停止)"
    record_item(
        item_id="02#6-土台",
        name="L1無向突合(生成集合⊆pass集合+代表EX実在)",
        test="test_s0_l1_unidirectional_match",
        verdict="pass",
        observed={
            "pass_pairs": len(state["l1_pass"]),
            "db_pairs": len(db_pairs),
            "sample_ok": len(sample_pairs - missing),
        },
    )


async def test_s0_d08_retention_forecast(api, db):
    state = await seed_all(api, db)
    per = state["pass_pairs_per_user_day"]
    over = {f"{u}@{d}": n for (u, d), n in per.items() if n > 6}
    assert over, "6件超えのユーザー×日が0件 — #8(保留昇格)の材料がない(計画§2.2)"
    top = sorted(over.items(), key=lambda kv: -kv[1])[:5]
    record_item(
        item_id="S0-D08",
        name="D-08保留予測の事前集計(設計§2.2対抗策c)",
        test="test_s0_d08_retention_forecast",
        verdict="pass",
        observed={"over_count": len(over), "top5": top},
    )
```

- [ ] **Step 4: lint+部分実行での動作確認**

Run: `cd backend && uv run ruff format staging_tests && uv run ruff check staging_tests`
Expected: グリーン

Run: `make staging-reset`(シードYAMLの対象日を確定するため先に実施日を決める: 下記Step 5)
Run: `make seed-generate ARGS="--base-datetime <実施日の翌日>T11:00:00+09:00" && make seed-verify`
Expected: seed-verify exit 0(`OK:` 出力・YAML差分は対象日付のみ — `git diff --stat docs/testassets/m4-seed.yaml`で確認)

Run: `cd backend && uv run pytest staging_tests -q`(S0のフル投入+突合・**初回の期待値確定手順**)
Expected: 4 passed(S0全検査)。**もし失敗したら**: (a)L1突合のfail=生成集合外れ → docker compose -f compose.staging.yaml logs worker1 --tail 50で確認・§0規律どおり報告書に書いて停止 (b)代表EX未生成 → 同上

- [ ] **Step 5: コミット**

```bash
git add backend/staging_tests/helpers_seed.py backend/staging_tests/helpers_scenario.py backend/staging_tests/test_s0_env_seed.py
git commit -m "test: add staging seed ingestion and L1 unidirectional match (S0)"
```

### Task 6: S1候補系(test_s1_candidate_items.py・#5/#6/#7/#8/#9/#10/#11/#12)

**Files:**
- Create: `backend/staging_tests/test_s1_candidate_items.py`

**Interfaces:**
- Consumes: `seed_all`/`fetch_candidates_pairs`/`ex_pair_ids`(Task 5)・`make_scenario_pair`(Task 5)・`get_clock`/`record_item`(Task 4)・`wait_until`(Task 4)
- Produces: なし(検査のみ)。**ファイル内の実行順は定義順**で、#8(JST 0時跨ぎ)は最後に定義する — 前進はS2以降の仮想時刻前提(D1 00:05)を作るため

- [ ] **Step 1: test_s1_candidate_items.pyを作る**

```python
"""S1: 候補系(02#5・#6・#7・#8・#9・#10・#11・#12・設計§2.5の骨子どおり)。

時刻不動で観察する検査を先に置き、#8(JST 0時跨ぎのClock前進)を最後に
置く(前進はS2以降の前提を作る)。DB観察はSELECT専用接続(conftestの
db fixture)のみ・書き込みはAPI正規経路のみ。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from conftest import get_clock, record_item
from helpers_scenario import make_scenario_pair
from helpers_seed import (
    ex_pair_ids,
    fetch_candidates_pairs,
    load_seed,
    seed_all,
)

FAIL_EX = (  # 設計§2.5 #9の材料(EX fail組)
    "EX-007",
    "EX-009",
    "EX-011",
    "EX-012",
    "EX-014",
    "EX-016",
    "EX-018",
    "EX-019",
    "EX-023",
    "EX-024",
)


async def test_s1_item05_smoke(api, db):
    """#5 スモーク: Intentあたり候補生成数≤K_v=50+Jev実行総量の記録照合。"""
    state = await seed_all(api, db)
    max_per_intent = await db.fetchval(
        "SELECT max(n) FROM ("
        "  SELECT count(*) AS n FROM match_candidates GROUP BY intent_a_id"
        "  UNION ALL"
        "  SELECT count(*) AS n FROM match_candidates GROUP BY intent_b_id"
        ") t"
    )
    assert max_per_intent is not None and max_per_intent <= 50, (
        f"Intentあたり候補数がK_v=50を超えました: {max_per_intent}"
    )
    ev_total = await db.fetchval(
        "SELECT count(*) FROM match_candidates WHERE jev_result IS NOT NULL"
    )
    assert ev_total <= 300 * 8, f"Jev評価済み行が総上限を超えました: {ev_total}"
    # 起点あたり評価行数≤K_j=8の近似観察(FR-07のjev_result IS NULLガードで
    # 同一ペアは1回のみ評価される・厳密なイベント単位観察はws-6が担う)
    max_ev_per_intent = await db.fetchval(
        "SELECT max(n) FROM ("
        "  SELECT count(*) AS n FROM match_candidates"
        "   WHERE jev_result IS NOT NULL GROUP BY intent_a_id"
        "  UNION ALL"
        "  SELECT count(*) AS n FROM match_candidates"
        "   WHERE jev_result IS NOT NULL GROUP BY intent_b_id"
        ") t"
    )
    assert max_ev_per_intent <= 8, f"起点あたりJev実行がK_j=8を超えました: {max_ev_per_intent}"
    record_item(
        item_id="02#5",
        name="総当たりスモーク(K_v=50以内・K_j記録照合)",
        test="test_s1_item05_smoke",
        verdict="pass",
        observed={
            "max_candidates_per_intent": max_per_intent,
            "jev_evaluated_total": ev_total,
            "max_jev_per_intent": max_ev_per_intent,
        },
    )


async def test_s1_item06_ex_pass_bidirectional(api, db):
    """#6 既存Intentを含む候補の生成記録(EX passペアの有向両方向の観察)。"""
    state = await seed_all(api, db)
    seed = load_seed()
    rows = await db.fetch(
        "SELECT intent_a_id, intent_b_id FROM match_candidates"
    )
    uuid_to_sd = {v: k for k, v in state["sd_to_uuid"].items()}
    directed: set[tuple[str, str]] = set()
    for r in rows:
        a = uuid_to_sd.get(str(r["intent_a_id"]))
        b = uuid_to_sd.get(str(r["intent_b_id"]))
        if a is not None and b is not None:
            directed.add((a, b))
    sample = sorted(state["ex_pass_sample"])
    both = [e for e in sample if (lambda p: p in directed and (p[1], p[0]) in directed)(ex_pair_ids(seed, e))]
    assert both, f"両方向の行を持つEX passペアが0組: {sample}"
    record_item(
        item_id="02#6",
        name="EX passペアの生成記録(無向実在7組+両方向観察)",
        test="test_s1_item06_ex_pass_bidirectional",
        verdict="pass",
        observed={"sample": sample, "bidirectional_ex": both},
    )


async def test_s1_item07_patch_reevaluation(api, db):
    """#7 PATCH(時間)→debounce 10秒→updated Event→再評価の生成/不生成。"""
    state = await seed_all(api, db)
    from conftest import auth_header
    from helpers_scenario import _create_intent, _register

    # 最初は時刻を離して作る(A=D+1 06:30・B=D+3 06:30 → time0 failで不在)。
    # make_scenario_pairは2人に同一whenを作るため、この検査では直接作成する
    s_a = "HS-p07-1"
    s_b = "HS-p07-2"
    await _register(api, s_a, "1995-04-01")
    await _register(api, s_b, "1995-04-01")
    a_id = await _create_intent(
        api, s_a, when="2026-10-05T06:30:00+09:00",
        raw_text="木曜の早朝に天文館で散歩したい",
    )
    b_id = await _create_intent(
        api, s_b, when="2026-10-07T06:30:00+09:00",
        raw_text="土曜の早朝に天文館で散歩したい",
    )

    def _count_containing(intent_uuid: str) -> int:
        return db.fetchval(
            "SELECT count(*) FROM match_candidates"
            " WHERE intent_a_id=$1::uuid OR intent_b_id=$1::uuid",
            intent_uuid,
        )

    assert await _count_containing(b_id) == 0, "離れた時刻で作成したのに候補が存在"
    # PATCH: Bの時刻をA(D+1 06:30)へ近づける → candidate生成を観察
    rp = await api.patch(
        f"/v1/intents/{b_id}",
        headers=auth_header(s_b),
        json={
            "raw_text": "木曜の早朝に天文館で散歩したい",
            "structured_intent": {"time": {"start": "2026-10-05T06:30:00+09:00"}},
        },
    )
    assert rp.status_code == 200, f"PATCH失敗: {rp.status_code} {rp.text}"
    from helpers_wait import wait_until

    async def appeared() -> bool:
        return await _count_containing(b_id) > 0

    await wait_until(
        appeared, timeout_s=180, interval_s=10,
        what="PATCH後の候補生成(debounce 10秒+updated Event再評価)",
    )
    after_patch = await _count_containing(b_id)
    # PATCH: 再度時刻を離す → 新versionでは生成されない
    # (旧version行は残る=実装の決定的挙動。証拠に記録する)
    rp2 = await api.patch(
        f"/v1/intents/{b_id}",
        headers=auth_header(s_b),
        json={
            "raw_text": "土曜の早朝に天文館で散歩したい",
            "structured_intent": {"time": {"start": "2026-10-07T06:30:00+09:00"}},
        },
    )
    assert rp2.status_code == 200
    import asyncio as _aio

    await _aio.sleep(70)  # debounce 10秒+tick 60秒+余裕
    final = await _count_containing(b_id)
    assert final == after_patch, (
        f"PATCH離行後に候補行が増えました: {after_patch}→{final}"
        "(新versionで生成されてはならない)"
    )
    record_item(
        item_id="02#7",
        name="PATCH再評価(生成/不生成の記録)",
        test="test_s1_item07_patch_reevaluation",
        verdict="pass",
        observed={
            "before": 0,
            "after_patch": after_patch,
            "final_after_leaving": final,
            "note": "旧version行は残存(実装の決定的挙動)",
        },
    )


async def test_s1_item09_hard_filter_e2e(api, db):
    """#9 Hard Filter E2E: EX failペアがいずれの候補にも現れない。"""
    state = await seed_all(api, db)
    seed = load_seed()
    db_pairs = await fetch_candidates_pairs(db, state["sd_to_uuid"])
    leaked = [
        e for e in FAIL_EX
        if ex_pair_ids(seed, e) is not None and ex_pair_ids(seed, e) in db_pairs
    ]
    assert not leaked, f"EX failペアが候補に存在(無向化突合): {leaked}"
    record_item(
        item_id="02#9",
        name="Hard Filter E2E(EX failペア不生成+pass組はS0突合済み)",
        test="test_s1_item09_hard_filter_e2e",
        verdict="pass",
        observed={"checked": list(FAIL_EX), "db_pairs": len(db_pairs)},
    )


async def test_s1_item10_near_semantic_pair(api, db):
    """#10 意味近接ペア(焼肉/肉系)の候補生成(stub下ではL1通過ゆえの生成)。"""
    state = await seed_all(api, db)
    pair = await make_scenario_pair(
        api,
        db,
        key="s1-10",
        name="p10",
        when="2026-10-05T06:30:00+09:00",
        category="meal",
        secondary="焼肉",
        raw_text="早朝に天文館で焼肉ランチ気分の食事をしたい",
    )
    s2 = pair["subjects"][1]
    from conftest import auth_header
    from helpers_scenario import _create_intent

    i2 = await _create_intent(
        api, s2, when="2026-10-05T06:30:00+09:00", category="meal",
        secondary="肉居酒屋", alcohol_involved=False,
        raw_text="早朝に天文館で肉料理を食べたい",
    )
    pair["intent_ids"][1] = i2
    from helpers_wait import wait_until

    async def paired() -> bool:
        n = await db.fetchval(
            "SELECT count(*) FROM match_candidates"
            " WHERE (intent_a_id=$1::uuid AND intent_b_id=$2::uuid)"
            " OR (intent_a_id=$2::uuid AND intent_b_id=$1::uuid)",
            pair["intent_ids"][0],
            i2,
        )
        return n > 0

    await wait_until(paired, timeout_s=180, interval_s=10, what="#10ペアの候補生成")
    record_item(
        item_id="02#10",
        name="意味近接ペアの候補生成(案A: L1通過ゆえの生成)",
        test="test_s1_item10_near_semantic_pair",
        verdict="pass",
        observed={"pair": pair["intent_ids"]},
    )


async def test_s1_item11_dense_cluster(api, db):
    """#11 密集配置(互換Intent 30件=6人×5件)→L3通過K_c=20・Jev K_j=8以内。"""
    from conftest import auth_header
    from helpers_scenario import _create_intent, _register

    # 6人×5件(Active 5件/ユーザー制限内・設計表記2人×15件は§2.1読み替え3)
    subjects = [f"HS-p11-{n}" for n in range(1, 7)]
    user_ids = []
    for s in subjects:
        user_ids.append(await _register(api, s, "1995-04-01"))
    when = "2026-10-05T06:30:00+09:00"
    intent_ids = []
    for s in subjects:
        for k in range(5):
            intent_ids.append(
                await _create_intent(
                    api, s, when=when, category="activity",
                    secondary=f"朝活散歩{k}",
                    raw_text=f"早朝に天文館で朝活{k}を行いたい",
                )
            )
    # 収束待ち(密集群のイベント処理が終わるまで・上限180秒)
    from helpers_wait import wait_until

    origin = intent_ids[0]

    async def settled() -> bool:
        n = await db.fetchval(
            "SELECT count(*) FROM match_candidates"
            " WHERE jev_result IS NOT NULL AND (intent_a_id=$1::uuid"
            " OR intent_b_id=$1::uuid)",
            origin,
        )
        return n > 0

    await wait_until(settled, timeout_s=180, interval_s=10, what="#11密集群の評価")
    origin_candidates = await db.fetchval(
        "SELECT count(*) FROM match_candidates"
        " WHERE intent_a_id=$1::uuid OR intent_b_id=$1::uuid",
        origin,
    )
    origin_ev = await db.fetchval(
        "SELECT count(*) FROM match_candidates"
        " WHERE jev_result IS NOT NULL AND (intent_a_id=$1::uuid"
        " OR intent_b_id=$1::uuid)",
        origin,
    )
    assert origin_candidates <= 50, f"起点候補数がK_v=50超: {origin_candidates}"
    assert origin_ev <= 8, f"起点Jev実行がK_j=8超: {origin_ev}"
    record_item(
        item_id="02#11",
        name="密集配置(30件・L3通過K_c=20以内・Jev K_j=8以内)",
        test="test_s1_item11_dense_cluster",
        verdict="pass",
        observed={
            "cluster_intents": len(intent_ids),
            "origin_candidates": origin_candidates,
            "origin_jev": origin_ev,
        },
    )


async def test_s1_item12_jev_result_record(api, db):
    """#12 jev_resultにwould_a/would_b双方+provider/modelが記録される。"""
    row = await db.fetchrow(
        "SELECT jev_result FROM match_candidates WHERE jev_result IS NOT NULL LIMIT 1"
    )
    assert row is not None, "評価済み候補が存在しない"
    j = row["jev_result"]
    assert j["would_a_accept_b"] == 0.9, f"would_a={j['would_a_accept_b']}"
    assert j["would_b_accept_a"] == 0.9, f"would_b={j['would_b_accept_a']}"
    assert "provider" in j and "model" in j, f"provider/modelキー欠落: {sorted(j)}"
    record_item(
        item_id="02#12",
        name="jev_result記録(双方判定値+provider/model)",
        test="test_s1_item12_jev_result_record",
        verdict="pass",
        observed={"noul": 0.9, "provider": j["provider"], "model": j["model"]},
    )


async def test_s1_item08_retention_promotion(api, db):
    """#8 時刻到来: JST 0時跨ぎ(リセットジョブ+保留キュー再評価)でproposed昇格。

    ファイル内で最後に実行する(この検査がClockをD1 00:05へ前進し・S2以降の
    仮想時刻前提を作る)。
    """
    state = await seed_all(api, db)
    clock = await get_clock()
    before_retained = await db.fetchval(
        "SELECT count(*) FROM latches WHERE status='candidate'"
    )
    before_promoted = await db.fetchval(
        "SELECT count(*) FROM latch_status_events"
        " WHERE from_status='candidate' AND to_status='proposed'"
    )
    assert before_retained > 0, (
        "保留中candidateが0件 — D-08事前集計(計画§2.2: 6件超え74件)と不整合"
    )
    # D1 00:05 JST へ前進(base_datetimeの翌日・ISO明示・後退は行わない)
    base = datetime.fromisoformat(state["meta_base"])
    target = (base + timedelta(days=1)).replace(hour=0, minute=5)
    clock.set(target)
    from helpers_wait import wait_until

    async def promoted() -> bool:
        cur = await db.fetchval(
            "SELECT count(*) FROM latch_status_events"
            " WHERE from_status='candidate' AND to_status='proposed'"
        )
        return cur > before_promoted

    await wait_until(
        promoted, timeout_s=180, interval_s=10,
        what="JST 0時跨ぎ後の保留→proposed昇格(リセットジョブ+保留キュー再評価・≤180秒)",
    )
    promoted_now = await db.fetchval(
        "SELECT count(*) FROM latch_status_events"
        " WHERE from_status='candidate' AND to_status='proposed'"
    )
    record_item(
        item_id="02#8",
        name="時刻到来再評価(保留→proposed昇格)",
        test="test_s1_item08_retention_promotion",
        verdict="pass",
        observed={
            "retained_before": before_retained,
            "promoted_delta": promoted_now - before_promoted,
            "clock_set_to": target.isoformat(),
        },
    )
```

(備考: `make_scenario_pair`は2人に同一`when`のIntentを作るため、#7では2人目のみ別時刻で作り直す。1人目のIntent(06:30)と2人目の作り直し(10-07 06:30)は交差しない。`_count_containing`等のクロージャでdb fixtureを参照する形はpytestのasync検査内で問題なく動く。)

- [ ] **Step 2: lint+フル実行でのS1確認**

Run: `cd backend && uv run ruff format staging_tests && uv run ruff check staging_tests && make lint && make test`
Expected: グリーン(unitに影響なし)

Run: `make staging-reset && make seed-generate ARGS="--base-datetime <実施日翌日>T11:00:00+09:00" && make seed-verify && cd backend && uv run pytest staging_tests -q`
Expected: S0+S1全PASS(前進は#8のみ・最後)。

Run: `cd backend && uv run pytest staging_tests -k "item05 or item09" -q`(部分実行の例・s0込みではないためstaging-reset後の既存データ上で動く)
Expected: 2 passed

- [ ] **Step 3: コミット**

```bash
git add backend/staging_tests/test_s1_candidate_items.py
git commit -m "test: add staging S1 candidate items (02#5-#12)"
```

### Task 7: S2提案・通知系(test_s2_proposal_items.py・#13/#14/#17/#22/#23/#26/FR-21)

**Files:**
- Modify: `backend/staging_tests/helpers_scenario.py`(ヘルパ`my_latch`を追記)
- Modify: `backend/staging_tests/helpers_seed.py`(ヘルパ`compute_mlr`を追記)
- Create: `backend/staging_tests/test_s2_proposal_items.py`

**Interfaces:**
- Consumes: Task 4〜6の全ヘルパ・専用シナリオ(§2.3一覧: muted HS-131/132・yes/no HS-191/192・S4用summary_only HS-141/142・#22用 HS-151〜154 — いずれもS0収束待ちの中でproposed昇格済み)
- Produces: `helpers_scenario.my_latch(api, subject, *, status="proposed") -> dict`・`helpers_seed.compute_mlr(db) -> dict`(分母/分子/mlr)。Task 8〜10が使う

- [ ] **Step 1: helpers_scenario.pyへ`my_latch`を追記**

```python
async def my_latch(api, subject: str, *, status: str = "proposed") -> dict:  # noqa: ANN001
    """自分(status指定)のlatchを1件引き出す(ハーネス観測用)。"""
    r = await api.get("/v1/latches", headers=auth_header(subject))
    assert r.status_code == 200, f"latches一覧失敗 {subject}: {r.status_code}"
    hits = [i for i in r.json()["items"] if i["status"] == status]
    assert hits, f"{subject}にstatus={status}のlatchがない"
    return hits[0]
```

- [ ] **Step 2: helpers_seed.pyへ`compute_mlr`を追記**

```python
async def compute_mlr(db) -> dict:  # noqa: ANN001
    """MLR集計(01 §23・09 §2.1): 分母=proposed遷移数(1候補1・グループも1・
    期限切れ/NO/競合クローズも除かない)・分子=matched遷移数(全員YES)。
    mutedもproposed遷移のため分母に含まれる(06 §2)。"""
    denominator = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='proposed'"
    )
    numerator = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='matched'"
    )
    return {
        "denominator": denominator,
        "numerator": numerator,
        "mlr": (numerator / denominator) if denominator else None,
    }
```

- [ ] **Step 3: test_s2_proposal_items.pyを作る**

```python
"""S2: 提案・通知系(02#13・#14・#17提案時・#22・#23・#26・FR-21・設計§2.5)。

前提: S1の#8でClockがD1 00:05へ前進済み・専用シナリオはS0収束待ちの中で
proposed昇格済み。時刻はこのファイルでは前進しない。
"""

from __future__ import annotations

from conftest import auth_header, record_item
from helpers_pushlog import push_log_records
from helpers_scenario import make_scenario_pair, my_latch
from helpers_seed import compute_mlr, seed_all

HIDDEN_KEYS = {"headcount", "match_level"}
SUMMARY_KEYS = {
    "time_summary",
    "area_name",
    "headcount",
    "category_primary",
    "category_secondary",
    "budget",
    "match_level",
}


async def test_s2_item13_proposal_notification_and_muted(api, db):
    """#13 通知先行書き込み+pushドライラン。muted提案はpush 0件・proposed・回答可。"""
    state = await seed_all(api, db)
    # (a) 通常提案: proposed latch≥1・notifications(proposal型)≥1・push記録≥1
    n_proposed = await db.fetchval(
        "SELECT count(*) FROM latches WHERE status='proposed'"
    )
    assert n_proposed >= 1, "proposed latchが0件"
    n_notif = await db.fetchval(
        "SELECT count(*) FROM notifications WHERE type='proposal'"
    )
    assert n_notif >= 1, "notifications(proposal型)の先行書き込みがない"
    pushes = push_log_records()
    assert any(p["notification_type"] == "proposal" for p in pushes), (
        "pushドライラン記録(proposal)が0件"
    )
    # (b) mutedペア(HS-131/132・notification_level=muted): push 0件・proposed・回答可
    muted = await make_scenario_pair(
        api, db, key="s2-13", name="p13", when="2026-10-05T06:30:00+09:00",
        notification_level="muted", raw_text="早朝に天文館で静かに朝食をしたい",
    )
    latch = await my_latch(api, muted["subjects"][0], status="proposed")
    latch_id = str(latch["id"])
    muted_pushes = [p for p in push_log_records() if p["latch_id"] == latch_id]
    assert muted_pushes == [], (
        f"muted提案にpush記録が{len(muted_pushes)}件(0件でなければならない)"
    )
    rr = await api.post(
        f"/v1/latches/{latch_id}/response",
        headers=auth_header(muted["subjects"][0]),
        json={"response": "defer"},
    )
    assert rr.status_code == 200, f"muted提案の回答が可でない: {rr.status_code}"
    record_item(
        item_id="02#13",
        name="提案通知(先行書き込み+pushドライラン)+muted(push 0件・proposed・回答可)",
        test="test_s2_item13_proposal_notification_and_muted",
        verdict="pass",
        observed={
            "proposed": n_proposed,
            "notifications": n_notif,
            "push_total": len(pushes),
            "muted_push": 0,
        },
    )


async def test_s2_item14_responses_record(api, db):
    """#14 yes/no回答の記録と遷移(responses+latch_status_events)。"""
    pair = await make_scenario_pair(
        api, db, key="s2-14", name="p19",
        when="2026-10-05T06:30:00+09:00",
        raw_text="早朝に天文館でコーヒーを飲みたい",
    )
    latch = await my_latch(api, pair["subjects"][0], status="proposed")
    latch_id = str(latch["id"])
    r1 = await api.post(
        f"/v1/latches/{latch_id}/response",
        headers=auth_header(pair["subjects"][0]),
        json={"response": "yes"},
    )
    assert r1.status_code == 200, f"yes回答失敗: {r1.status_code} {r1.text}"
    r2 = await api.post(
        f"/v1/latches/{latch_id}/response",
        headers=auth_header(pair["subjects"][1]),
        json={"response": "no"},
    )
    assert r2.status_code == 200, f"no回答失敗: {r2.status_code} {r2.text}"
    # DB観察: responses(jsonb)に2件・遷移イベントに成立しない終端がある
    rows = await db.fetchrow(
        "SELECT responses, status FROM latches WHERE id=$1::uuid", latch_id
    )
    responses = rows["responses"]
    kinds = sorted(r["response"] for r in responses) if isinstance(responses, list) else []
    assert "yes" in kinds and "no" in kinds, f"responses記録: {kinds}"
    events = await db.fetch(
        "SELECT from_status, to_status FROM latch_status_events"
        " WHERE latch_id=$1::uuid ORDER BY created_at",
        latch_id,
    )
    seq = [(e["from_status"], e["to_status"]) for e in events]
    assert seq and seq[0][1] == "proposed", f"遷移系列: {seq}"
    assert rows["status"] != "proposed", "no回答後にproposedのまま(終端遷移なし)"
    record_item(
        item_id="02#14",
        name="yes/no回答の記録と遷移",
        test="test_s2_item14_responses_record",
        verdict="pass",
        observed={"responses": kinds, "transitions": seq, "final": rows["status"]},
    )


async def test_s2_item17_before_match_visibility(api, db):
    """#17(提案時) 可視範囲: participants非解放・summary_onlyはサマリあり。

    成立後(S4)との前後差分はtest_s4_item17_after_matchで検査する。
    """
    pair = await make_scenario_pair(
        api, db, key="s4-17", name="p17",
        when="2026-10-11T06:30:00+09:00",  # D+7(S3の前進後も回答可)
        raw_text="7日後の早朝に天文館で散策したい",
    )
    latch = await my_latch(api, pair["subjects"][0], status="proposed")
    r = await api.get(
        f"/v1/latches/{latch['id']}", headers=auth_header(pair["subjects"][0])
    )
    assert r.status_code == 200
    detail = r.json()["latch"]
    assert detail["participants"] is None, "成立前にparticipantsが解放されている"
    assert detail["proposal"].get("time_summary"), "summary_only提案にサマリがない"
    record_item(
        item_id="02#17",
        name="可視範囲比較(提案時: participants非解放・サマリあり)",
        test="test_s2_item17_before_match_visibility",
        verdict="pass",
        observed={"proposal_keys": sorted(detail["proposal"])},
    )


async def test_s2_item22_visibility_branch(api, db):
    """#22 visibility 2値分岐: hidden=サマリなし/summary_only=サマリあり(キー集合比較)。"""
    hidden = await make_scenario_pair(
        api, db, key="s2-22h", name="p22h",
        when="2026-10-06T06:30:00+09:00",
        visibility="hidden_until_match",
        raw_text="翌朝に天文館で散歩したい",
    )
    summary = await make_scenario_pair(
        api, db, key="s2-22s", name="p22s",
        when="2026-10-06T06:30:00+09:00",
        visibility="summary_only",
        raw_text="翌朝に天文館で朝食を兼ねて歩きたい",
    )
    lh = await my_latch(api, hidden["subjects"][0])
    ls = await my_latch(api, summary["subjects"][0])
    kh = set(lh["proposal"].keys())
    ks = set(ls["proposal"].keys())
    assert kh == HIDDEN_KEYS, f"hidden提案のキー集合: {sorted(kh)}"
    assert ks == SUMMARY_KEYS, f"summary_only提案のキー集合: {sorted(ks)}"
    record_item(
        item_id="02#22",
        name="visibility分岐(hidden 2キー/summary_only 7キー)",
        test="test_s2_item22_visibility_branch",
        verdict="pass",
        observed={"hidden": sorted(kh), "summary_only": sorted(ks)},
    )


async def test_s2_item23_no_precise_coordinates(api, db):
    """#23 座標精度値が応答に現れない(キー集合+値パターン検査)。"""
    summary = await make_scenario_pair(
        api, db, key="s2-23", name="p23",
        when="2026-10-06T06:30:00+09:00",
        raw_text="翌朝に呉服町で散歩したい",
    )
    latch = await my_latch(api, summary["subjects"][0])
    r = await api.get(
        f"/v1/latches/{latch['id']}", headers=auth_header(summary["subjects"][0])
    )
    body_text = r.text
    blob = body_text.lower()
    coord_keys = [k for k in blob if k in ("latitude", "longitude", "lat", "lon", "geometry", "coordinates")]
    assert not coord_keys, f"応答に座標系キーが含まれる: {coord_keys}"
    # 小数点を含む座標らしき数値ペア(lat,lon形式)が文字列に含まれない
    import re

    assert not re.search(r"\d{2,3}\.\d{4,}\s*,\s*\d{2,3}\.\d{4,}", body_text), (
        "応答に座標らしき数値ペアが含まれる"
    )
    record_item(
        item_id="02#23",
        name="座標精度の非表示(キー集合+値パターン)",
        test="test_s2_item23_no_precise_coordinates",
        verdict="pass",
        observed={"detail_url": f"/v1/latches/{latch['id']}"},
    )


async def test_s2_fr21_push_content(api, db):
    """FR-21 push本文=汎用文とバイト一致・条件サマリ不含・drinking同一様式・
    summary_onlyはお知らせ+提案画面にサマリ・hiddenは両方なし。"""
    from latch.notifications.templates import build_push

    pushes = push_log_records()
    assert pushes, "pushドライラン記録がない"
    # (a) 本文=テンプレート定数とバイト一致(条件サマリ混入経路がないことの観察)
    bodies = {(p["notification_type"], p["title"], p["body"]) for p in pushes}
    for ntype, title, body in bodies:
        t, b = build_push(ntype)
        assert title == t and body == b, f"push本文がテンプレートと不一致: {ntype}"
    # (b) 条件サマリ不含(地名・カテゴリ文言が本文に現れない)
    for _ntype, _title, body in bodies:
        for word in ("天文館", "呉服町", "散歩", "焼肉", "飲み"):
            assert word not in body, f"push本文に条件サマリ語が含まれる: {word}"
    # (c) summary_onlyペアのお知らせ(notifications)にproposalサマリキーがある
    summary = await make_scenario_pair(
        api, db, key="s2-fr21s", name="fr21s",
        when="2026-10-06T06:30:00+09:00",
        raw_text="翌朝に天文館で朝食をしたい",
    )
    latch = await my_latch(api, summary["subjects"][0])
    notif = await db.fetchrow(
        "SELECT payload FROM notifications WHERE type='proposal'"
        " AND payload->>'latch_id'=$1 LIMIT 1",
        str(latch["id"]),
    )
    assert notif is not None, "summary_onlyペアのproposal通知が見つからない"
    notif_keys = set(notif["payload"]["proposal"].keys())
    assert SUMMARY_KEYS <= notif_keys, f"お知らせのproposalにサマリキーがない: {sorted(notif_keys)}"
    record_item(
        item_id="FR-21",
        name="push表示規制(汎用文バイト一致・サマリ不含・drinking同一様式)",
        test="test_s2_fr21_push_content",
        verdict="pass",
        observed={
            "distinct_bodies": sorted(b[2] for b in bodies),
            "summary_notif_keys": sorted(notif_keys),
        },
    )


async def test_s2_item26_mlr_computation(api, db):
    """#26 MLR算出(01 §23の数え方・S2時点の値を証拠化・S5で最終値を再記録)。"""
    m = await compute_mlr(db)
    assert m["denominator"] >= 1, f"分母=0(通知送信候補なし): {m}"
    assert m["numerator"] >= 0
    record_item(
        item_id="02#26",
        name="MLR算出(分母=proposed遷移・分子=matched遷移)",
        test="test_s2_item26_mlr_computation",
        verdict="pass",
        observed=m,
    )
```

(備考1: FR-21の(c)で使う`SUMMARY_KEYS <= notif_keys`は、notifications payloadの`proposal`オブジェクトが提案画面と同一のproposal dict(latches/store.pyが`json.dumps(proposal)`で格納)であることに依存する。payload->>'latch_id'で引けない実装だった場合は、payload::textでの部分照合へ書き換えず・helpers_seedにSELECT文を1本追加して実装形を確認してから直す(推測で緩めない)。備考2: `test_s2_item13`のdefer回答は「回答可」の証明(proposed遷移は維持されない実装のため回答種別はdeferを使用)。)

- [ ] **Step 4: lint+フル実行でのS2確認**

Run: `cd backend && uv run ruff format staging_tests && uv run ruff check staging_tests && make lint && make test`
Expected: グリーン

Run: `make staging-reset && make seed-generate ARGS="--base-datetime <実施日翌日>T11:00:00+09:00" && make seed-verify && cd backend && uv run pytest staging_tests -q`
Expected: S0〜S2全PASS

- [ ] **Step 5: コミット**

```bash
git add backend/staging_tests/helpers_scenario.py backend/staging_tests/helpers_seed.py backend/staging_tests/test_s2_proposal_items.py
git commit -m "test: add staging S2 proposal/notification items (02#13-#26, FR-21)"
```

### Task 8: S3グループ・期限系(test_s3_group_deadline_items.py・#18/#16/通知順序/自己重複)

**Files:**
- Create: `backend/staging_tests/test_s3_group_deadline_items.py`

**Interfaces:**
- Consumes: `make_scenario_group`/`my_latch`(Task 5/7)・`ex_pair_ids`と同型の`ex_case_ids`(下記にファイル内ヘルパとして定義)・`get_clock`/`record_item`/`wait_until`
- Produces: なし。**このTaskの#16とIntent期限切れでClockをD+2 06:00→D+6 00:10へ前進する**(S4の前提)。ファイル内の実行順は定義順で・前進2段は後ろに置く

- [ ] **Step 1: test_s3_group_deadline_items.pyを作る**

```python
"""S3: グループ・期限系(02#18・#16・通知順序EX-026・自己重複EX-025・設計§2.5)。

09 §3グループ6検証のうちstaging観察で再実行する分(集合生成・全員YES成立・
期限確定・通知順序・自己重複不生成)を含む。実装値(計画時確認済み):
期限確定=sweeperがproposed/partial_accept×回答期限をstatus='expired'へ
(worker/sweeper.py:50)・部分UNIQUE(uq_group_candidates_intent_ids_open)が
同一集合のopen重複を排除(0005)。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from conftest import auth_header, get_clock, record_item
from helpers_scenario import make_scenario_group, my_latch
from helpers_seed import load_seed, seed_all
from helpers_wait import wait_until


def _ex_intents(seed: dict, ex_id: str) -> list[str]:
    """expectations.casesからEX-xxxのintents(idリスト)を引く。"""
    for case in seed["expectations"]["cases"]:
        if case["id"] == ex_id:
            return list(case["intents"])
    raise AssertionError(f"{ex_id}がexpectationsに存在しない")


async def test_s3_item18_group_all_yes(api, db):
    """#18 3〜4人集合の全員YES成立(09 §3と同一手順のstaging再実行・専用4人組)。"""
    state = await seed_all(api, db)
    group = await make_scenario_group(
        api, db, key="s3-18", name="p18",
        when="2026-10-06T06:30:00+09:00", members=4, min_participants=3,
        raw_text="翌朝に天文館でみんなで散歩したい",
    )
    ids = group["intent_ids"]
    # 集合生成の観察(group_candidatesに4 Intentの集合行)
    row = await db.fetchrow(
        "SELECT id, status FROM group_candidates WHERE intent_ids=$1::uuid[]",
        ids,
    )
    assert row is not None, f"4人集合のgroup_candidatesが生成されていない: {ids}"
    # 全員yes → matched
    latch = await my_latch(api, group["subjects"][0], status="proposed")
    latch_id = str(latch["id"])
    for s in group["subjects"]:
        r = await api.post(
            f"/v1/latches/{latch_id}/response",
            headers=auth_header(s),
            json={"response": "yes"},
        )
        assert r.status_code == 200, f"グループyes回答失敗 {s}: {r.status_code} {r.text}"
    # matched遷移の観察
    async def matched() -> bool:
        st = await db.fetchval(
            "SELECT status FROM latches WHERE id=$1::uuid", latch_id
        )
        return st == "matched"

    await wait_until(matched, timeout_s=120, interval_s=5, what="#18グループのmatched遷移")
    detail = await api.get(
        f"/v1/latches/{latch_id}", headers=auth_header(group["subjects"][0])
    )
    assert detail.status_code == 200
    participants = detail.json()["latch"]["participants"] or []
    assert len(participants) == 4, f"成立後participantsが4人でない: {len(participants)}"
    record_item(
        item_id="02#18",
        name="グループ集合生成+全員YES成立(participants 4人)",
        test="test_s3_item18_group_all_yes",
        verdict="pass",
        observed={"group_candidate_status": row["status"], "members": 4},
    )


async def test_s3_notification_order_ex026(api, db):
    """通知順序: 同一Poolから複数集合→proposedはaggregate_score降順の1つだけ。"""
    state = await seed_all(api, db)
    seed = load_seed()
    ex_ids = _ex_intents(seed, "EX-026")
    ex_uuids = [state["sd_to_uuid"][i] for i in ex_ids]
    # EX-026クラスタの集合生成の観察(部分UNIQUEで同一集合open行は1行)
    rows = await db.fetch(
        "SELECT intent_ids, status, aggregate_score FROM group_candidates"
        " WHERE intent_ids && $1::uuid[]",
        ex_uuids,
    )
    assert rows, "EX-026クラスタの集合候補が生成されていない"
    # 同一メンバー集合に対するproposed latchは1行のみ(競合はcandidate/closedに残る)
    dup = await db.fetchval(
        "SELECT count(*) FROM ("
        "  SELECT array_agg(l.id) FROM latches l, group_candidates g"
        "  WHERE l.group_candidate_id = g.id AND l.status='proposed'"
        "  GROUP BY g.intent_ids HAVING count(*) > 1"
        ") t"
    )
    assert not dup, f"同一集合に対するproposed latchが複数: {dup}"
    record_item(
        item_id="通知順序(EX-026)",
        name="同一Pool複数集合→proposed 1つ(group_candidates記録)",
        test="test_s3_notification_order_ex026",
        verdict="pass",
        observed={
            "cluster_rows": len(rows),
            "statuses": sorted({r["status"] for r in rows}),
        },
    )


async def test_s3_self_duplication_ex025(api, db):
    """自己重複集合(EX-025)のgroup_candidates不生成+対照(EX-028の生成)。"""
    state = await seed_all(api, db)
    seed = load_seed()
    dup_ids = _ex_intents(seed, "EX-025")
    dup_uuids = [state["sd_to_uuid"][i] for i in dup_ids]
    n_dup = await db.fetchval(
        "SELECT count(*) FROM group_candidates WHERE intent_ids @> $1::uuid[]",
        dup_uuids,
    )
    assert n_dup == 0, f"自己重複集合(EX-025)のgroup_candidatesが{n_dup}件"
    ok_ids = _ex_intents(seed, "EX-028")
    ok_uuids = [state["sd_to_uuid"][i] for i in ok_ids]
    n_ok = await db.fetchval(
        "SELECT count(*) FROM group_candidates WHERE intent_ids=$1::uuid[]",
        ok_uuids,
    )
    assert n_ok >= 1, "対照(EX-028正常クラスタ)の集合生成が観察できず"
    record_item(
        item_id="FR-17-自己重複",
        name="自己重複集合不生成(EX-025)+対照生成(EX-028)",
        test="test_s3_self_duplication_ex025",
        verdict="pass",
        observed={"ex025_rows": n_dup, "ex028_rows": n_ok},
    )


async def test_s3_item16_partial_yes_deadline(api, db):
    """#16 部分YESの期限確定(専用3人組・1人yes→Clock前進でresponse_deadline通過)。"""
    state = await seed_all(api, db)
    group = await make_scenario_group(
        api, db, key="s3-16", name="p16",
        when="2026-10-06T06:30:00+09:00", members=3, min_participants=3,
        raw_text="翌朝に天文館で散歩してみたい",
    )
    latch = await my_latch(api, group["subjects"][0], status="proposed")
    latch_id = str(latch["id"])
    r = await api.post(
        f"/v1/latches/{latch_id}/response",
        headers=auth_header(group["subjects"][0]),
        json={"response": "yes"},
    )
    assert r.status_code == 200, f"部分yes失敗: {r.status_code}"
    before_expired = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='expired'"
    )
    clock = await get_clock()
    base = datetime.fromisoformat(state["meta_base"])
    # D+2 06:00へ前進(response_deadline D+2 05:15通過・ISO明示・後退なし)
    target = (base + timedelta(days=2)).replace(hour=6, minute=0)
    clock.set(target)
    latch_row = await db.fetchrow(
        "SELECT id FROM latches WHERE id=$1::uuid AND status='expired'", latch_id
    )

    async def expired_latch() -> bool:
        return await db.fetchval(
            "SELECT status FROM latches WHERE id=$1::uuid", latch_id
        ) == "expired"

    await wait_until(
        expired_latch, timeout_s=180, interval_s=10,
        what="#16の期限確定(sweeperがproposed×回答期限をexpiredへ・≤180秒)",
    )
    events = await db.fetch(
        "SELECT from_status, to_status FROM latch_status_events"
        " WHERE latch_id=$1::uuid ORDER BY created_at",
        latch_id,
    )
    seq = [(e["from_status"], e["to_status"]) for e in events]
    assert ("proposed", "expired") in seq, f"proposed→expired遷移がない: {seq}"
    after_expired = await db.fetchval(
        "SELECT count(*) FROM latch_status_events WHERE to_status='expired'"
    )
    record_item(
        item_id="02#16",
        name="部分YESの期限確定(proposed→expired・1人yes+2人無回答)",
        test="test_s3_item16_partial_yes_deadline",
        verdict="pass",
        observed={
            "transitions": seq,
            "expired_delta": after_expired - before_expired,
            "clock_set_to": target.isoformat(),
        },
    )


async def test_s3_intent_expiry_batch(api, db):
    """Intent期限切れ(expiredバッチ): D+5以降へ前進→sweeperがintentsをexpiredへ。"""
    state = await seed_all(api, db)
    before = await db.fetchval(
        "SELECT count(*) FROM intents WHERE status='expired'"
    )
    clock = await get_clock()
    base = datetime.fromisoformat(state["meta_base"])
    # D+6 00:10へ前進(D+5対象Intentのexpires_at経過後・ISO明示)
    target = (base + timedelta(days=6)).replace(hour=0, minute=10)
    clock.set(target)

    async def expired_grew() -> bool:
        cur = await db.fetchval(
            "SELECT count(*) FROM intents WHERE status='expired'"
        )
        return cur > before

    await wait_until(
        expired_grew, timeout_s=180, interval_s=10,
        what="Intent期限切れバッチ(sweeper 60秒周期・≤180秒)",
    )
    total = await db.fetchval(
        "SELECT count(*) FROM intents WHERE status='expired'"
    )
    assert total > before, "Intent期限切れが観察できなかった"
    record_item(
        item_id="02#16-Intent期限",
        name="Intent期限切れバッチ(D+6前進でsweeper発火)",
        test="test_s3_intent_expiry_batch",
        verdict="pass",
        observed={
            "expired_before": before,
            "expired_total": total,
            "clock_set_to": target.isoformat(),
        },
    )
```

(備考: `test_s3_item18`は`my_latch`で集合提案を引き4人全員が同一latchにyesを送る。groupsの集合行は部分UNIQUE(uq_group_candidates_intent_ids_open)により同一集合open行は1行のみ。)

- [ ] **Step 2: lint+フル実行でのS3確認**

Run: `cd backend && uv run ruff format staging_tests && uv run ruff check staging_tests && make lint && make test`
Expected: グリーン

Run: `make staging-reset && make seed-generate ARGS="--base-datetime <実施日翌日>T11:00:00+09:00" && make seed-verify && cd backend && uv run pytest staging_tests -q`
Expected: S0〜S3全PASS(#16とIntent期限のClock前進込み)

- [ ] **Step 3: コミット**

```bash
git add backend/staging_tests/test_s3_group_deadline_items.py
git commit -m "test: add staging S3 group/deadline items (02#16/#18, EX-025/026)"
```

### Task 9: S4成立後+S5安全系(test_s4_aftermatch_items.py・test_s5_safety_items.py)

**Files:**
- Create: `backend/staging_tests/test_s4_aftermatch_items.py`(#15・#19・#21・D-23・#17成立後)
- Create: `backend/staging_tests/test_s5_safety_items.py`(#20・D-10・#26最終集計)

**Interfaces:**
- Consumes: S2の`test_s2_item17_before_match_visibility`が作ったペア(key=`s4-17`・HS-p17-1/2・D+7 06:30対象 — S3のD+6前進後もresponse_deadline D+7 05:15が未来のためyes回答可)・`my_latch`・`push_log_records`・`compute_mlr`
- Produces: なし(S5が全シーンの最終集計を記録)

- [ ] **Step 1: test_s4_aftermatch_items.pyを作る**

```python
"""S4: 成立後(02#15・#19・#21・D-23・#17成立後・設計§2.5)。

前提: S3でClockがD+6 00:10へ前進済み。#17用ペア(HS-p17・D+7 06:30対象)は
S2でproposed昇格済みで・response_deadline(D+7 05:15)まで回答可能。
実装値(計画時確認済み): ブロック→進行中提案cancelled(safety/service.py
block_userが単一txで実施)・チャット読み取り専用=409 CHAT_READONLY
(latches/errors.py)。
"""

from __future__ import annotations

from conftest import auth_header, record_item
from helpers_scenario import my_latch
from helpers_seed import seed_all

P17 = "s4-17"  # S2のtest_s2_item17_before_match_visibilityと同一key


async def test_s4_item15_matched_and_chat(api, db):
    """#15 双方YES→matched+チャット解放(POST messages 201)。"""
    state = await seed_all(api, db)
    from helpers_scenario import _SCENARIOS

    pair = _SCENARIOS[P17]
    latch = await my_latch(api, pair["subjects"][0], status="proposed")
    latch_id = str(latch["id"])
    for s in pair["subjects"]:
        r = await api.post(
            f"/v1/latches/{latch_id}/response",
            headers=auth_header(s),
            json={"response": "yes"},
        )
        assert r.status_code == 200, f"yes回答失敗 {s}: {r.status_code} {r.text}"
    st = await db.fetchval("SELECT status FROM latches WHERE id=$1::uuid", latch_id)
    assert st == "matched", f"双方YES後のstatus: {st}"
    rm = await api.post(
        f"/v1/latches/{latch_id}/messages",
        headers=auth_header(pair["subjects"][0]),
        json={"body": "よろしくお願いします"},
    )
    assert rm.status_code == 201, f"チャット解放失敗: {rm.status_code} {rm.text}"
    record_item(
        item_id="02#15",
        name="双方YES→matched+チャット解放",
        test="test_s4_item15_matched_and_chat",
        verdict="pass",
        observed={"latch": latch_id, "status": st},
    )


async def test_s4_item19_bidirectional_chat(api, db):
    """#19 成立LATCHで双方向の送受信記録。"""
    from helpers_scenario import _SCENARIOS

    pair = _SCENARIOS[P17]
    latch_id = str(await my_latch(api, pair["subjects"][0], status="matched")["id"])
    r1 = await api.post(
        f"/v1/latches/{latch_id}/messages",
        headers=auth_header(pair["subjects"][0]),
        json={"body": "こちらから1通目"},
    )
    assert r1.status_code == 201
    r2 = await api.post(
        f"/v1/latches/{latch_id}/messages",
        headers=auth_header(pair["subjects"][1]),
        json={"body": "そちらへ返信します"},
    )
    assert r2.status_code == 201
    for s in pair["subjects"]:
        rg = await api.get(
            f"/v1/latches/{latch_id}/messages", headers=auth_header(s)
        )
        assert rg.status_code == 200
        bodies = [m["body"] for m in rg.json()["items"]]
        assert "こちらから1通目" in bodies and "そちらへ返信します" in bodies, bodies
    record_item(
        item_id="02#19",
        name="成立LATCHの双方向送受信",
        test="test_s4_item19_bidirectional_chat",
        verdict="pass",
        observed={"messages": 2},
    )


async def test_s4_item17_after_match(api, db):
    """#17(成立後) 前後差分: participants解放・提案サマリは維持。"""
    from helpers_scenario import _SCENARIOS

    pair = _SCENARIOS[P17]
    latch = await my_latch(api, pair["subjects"][0], status="matched")
    r = await api.get(
        f"/v1/latches/{latch['id']}", headers=auth_header(pair["subjects"][0])
    )
    detail = r.json()["latch"]
    participants = detail["participants"] or []
    assert len(participants) == 2, "成立後にparticipantsが解放されていない"
    assert all(p["display_name"] for p in participants), "display_nameが空"
    assert detail["proposal"].get("time_summary"), "成立後にサマリが消えた"
    record_item(
        item_id="02#17-成立後",
        name="可視範囲比較(成立後: participants解放・サマリ維持)",
        test="test_s4_item17_after_match",
        verdict="pass",
        observed={"participants": len(participants)},
    )


async def test_s4_item21_report(api, db):
    """#21 成立画面経路の通報→reports受付記録。"""
    from helpers_scenario import _SCENARIOS

    pair = _SCENARIOS[P17]
    latch_id = str(await my_latch(api, pair["subjects"][0], status="matched")["id"])
    r = await api.post(
        "/v1/reports",
        headers=auth_header(pair["subjects"][1]),
        json={"reportee_id": pair["user_ids"][0], "latch_id": latch_id,
              "reason": "不適切な発言があった"},
    )
    assert r.status_code == 201, f"通報失敗: {r.status_code} {r.text}"
    row = await db.fetchrow(
        "SELECT status FROM reports WHERE latch_id=$1::uuid AND reporter_id=$2::uuid",
        latch_id, pair["user_ids"][1],
    )
    assert row is not None, "reports受付記録がない"
    record_item(
        item_id="02#21",
        name="成立LATCHからの通報受付",
        test="test_s4_item21_report",
        verdict="pass",
        observed={"report_status": row["status"]},
    )


async def test_s4_d23_block_cancels_latch(api, db):
    """D-23 matched LATCHで一方がブロック→提案cancelled・チャット読み取り専用。"""
    from helpers_scenario import _SCENARIOS

    pair = _SCENARIOS[P17]
    latch_id = str(await my_latch(api, pair["subjects"][0], status="matched")["id"])
    rb = await api.post(
        f"/v1/users/{pair['user_ids'][1]}/block",
        headers=auth_header(pair["subjects"][0]),
    )
    assert rb.status_code == 201, f"ブロック失敗: {rb.status_code} {rb.text}"
    st = await db.fetchval("SELECT status FROM latches WHERE id=$1::uuid", latch_id)
    assert st == "cancelled", f"ブロック後のlatch status: {st}"
    rs = await api.post(
        f"/v1/latches/{latch_id}/messages",
        headers=auth_header(pair["subjects"][1]),
        json={"body": "ブロック後の送信"},
    )
    assert rs.status_code == 409, f"チャット送信の遮断: {rs.status_code}"
    assert rs.json()["error"]["code"] == "CHAT_READONLY", rs.json()
    record_item(
        item_id="D-23",
        name="成立後ブロック(提案cancelled・チャット読み取り専用)",
        test="test_s4_d23_block_cancels_latch",
        verdict="pass",
        observed={"latch_status": st, "chat_error": "CHAT_READONLY"},
    )
```

- [ ] **Step 2: test_s5_safety_items.pyを作る**

```python
"""S5: 安全系(02#20・D-10・#26最終集計・設計§2.5)。

intended_reject_cases(EX-029〜033)は投入せず・ここで422 UNDER_AGE試行素材
として使う(設計§3.1・ws-2引継ぎ)。422応答形式は{"error": {"code": ...}}
(main.py _error_body)。
"""

from __future__ import annotations

from conftest import auth_header, record_item
from helpers_scenario import _SCENARIOS
from helpers_seed import (
    compute_mlr,
    ex_pair_ids,
    fetch_candidates_pairs,
    load_seed,
    seed_all,
)
from helpers_wait import wait_until


async def test_s5_item20_block_prevents_candidates(api, db):
    """#20 ブロック設定後に一致Intentを配置→候補生成されない。"""
    state = await seed_all(api, db)
    pair = _SCENARIOS["s5-20"]
    # 171→172をブロック(171のIntentはS0収束待ちの中で作成済み)
    rb = await api.post(
        f"/v1/users/{pair['user_ids'][1]}/block",
        headers=auth_header(pair["subjects"][0]),
    )
    assert rb.status_code == 201, f"ブロック失敗: {rb.status_code}"
    # 172のIntent(171と一致条件: 同一日時・同一地点・同一カテゴリ)を作成
    from helpers_scenario import _create_intent

    before = await db.fetchval(
        "SELECT count(*) FROM match_events"
        " WHERE event_type='created' AND status='processed'"
    )
    new_id = await _create_intent(
        api, pair["subjects"][1], when="2026-10-05T06:30:00+09:00",
        category="activity", secondary="朝活散歩",
        raw_text="早朝に天文館で朝活を行いたい",
    )
    async def processed() -> bool:
        cur = await db.fetchval(
            "SELECT count(*) FROM match_events"
            " WHERE event_type='created' AND status='processed'"
        )
        return cur > before

    await wait_until(processed, timeout_s=180, interval_s=10, what="#20新Intentの処理")
    import asyncio

    await asyncio.sleep(70)  # 後続層(debounce+評価)の完了猶予
    n = await db.fetchval(
        "SELECT count(*) FROM match_candidates"
        " WHERE (intent_a_id=$1::uuid AND intent_b_id=$2::uuid)"
        " OR (intent_a_id=$2::uuid AND intent_b_id=$1::uuid)",
        pair["intent_ids"][0],
        new_id,
    )
    assert n == 0, f"ブロック後の一致Intentで候補が生成された: {n}"
    record_item(
        item_id="02#20",
        name="ブロック後の一致Intentは候補生成されない",
        test="test_s5_item20_block_prevents_candidates",
        verdict="pass",
        observed={"blocked_pair_candidates": n},
    )


async def test_s5_d10_under_age(api, db):
    """D-10(#27動作面): 18〜19歳ユーザーのalcohol Intentは422 UNDER_AGE+EX-004/005は非生成。"""
    state = await seed_all(api, db)
    seed = load_seed()
    results = []
    for case in seed["intended_reject_cases"]:
        r = await api.post(
            "/v1/intents",
            headers=auth_header(case["user"]),
            json={
                "raw_text": case["raw_text"],
                "status": "active",
                "structured_intent": case["structured_intent"],
            },
        )
        assert r.status_code == 422, (
            f"{case['id']}: 期待422・実際{r.status_code} {r.text}"
        )
        code = r.json()["error"]["code"]
        assert code == case["expected_code"], f"{case['id']}: code={code}"
        results.append({"id": case["id"], "code": code})
    # EX-004/005(age_alcohol)はLayer 1で除外済み(S0突合と同値をD-10として明示記録)
    db_pairs = await fetch_candidates_pairs(db, state["sd_to_uuid"])
    for ex in ("EX-004", "EX-005"):
        assert ex_pair_ids(seed, ex) not in db_pairs, f"{ex}が候補に存在"
    record_item(
        item_id="D-10",
        name="年齢制限(422 UNDER_AGE 5件+EX-004/005非生成)",
        test="test_s5_d10_under_age",
        verdict="pass",
        observed={"rejects": results},
    )


async def test_s5_item26_final_mlr(api, db):
    """#26(最終) 全シーン終了時点のMLR(S2の中間集計との差分も記録)。"""
    state = await seed_all(api, db)
    m = await compute_mlr(db)
    assert m["denominator"] >= 1
    record_item(
        item_id="02#26-最終",
        name="MLR最終集計(全シーン終了時点)",
        test="test_s5_item26_final_mlr",
        verdict="pass",
        observed=m,
    )
```

- [ ] **Step 3: S5用の#20ペアをS0で作る**

`helpers_scenario.py`へ#20用ペアの作成呼び出しを足すのではなく、**test_s0_env_seed.pyに1検査を追記**する(S0の収束待ちの中で171のIntentを作る):

`test_s0_env_seed.py`の末尾に追記:

```python
async def test_s0_s20_pair_prepared(api, db):
    """#20用ペア(HS-p20-1/2)の171側IntentをS0で作る(S5のブロック試験の素材)。"""
    from helpers_scenario import make_scenario_pair

    pair = await make_scenario_pair(
        api, db, key="s5-20", name="p20",
        when="2026-10-05T06:30:00+09:00",
        category="activity", secondary="朝活散歩",
        raw_text="早朝に天文館で朝活を行いたい",
    )
    record_item(
        item_id="S0-s20",
        name="#20素材ペア作成(171側のみ・172はS5で作成)",
        test="test_s0_s20_pair_prepared",
        verdict="pass",
        observed={"subjects": pair["subjects"], "intents": [pair["intent_ids"][0]]},
    )
```

- [ ] **Step 4: lint+フル実行での全体確認**

Run: `cd backend && uv run ruff format staging_tests && uv run ruff check staging_tests && make lint && make test`
Expected: グリーン

Run: `make staging-reset && make seed-generate ARGS="--base-datetime <実施日翌日>T11:00:00+09:00" && make seed-verify && cd backend && uv run pytest staging_tests -q`
Expected: **全PASS(S0〜S5・証拠YAMLがdocs/testassets/results/へ出力)**

Run: `ls -1 ../docs/testassets/results/ws3-staging-result-*.yaml | tail -1 && rg -c "verdict: pass" ../docs/testassets/results/$(ls -1 ../docs/testassets/results/ws3-staging-result-*.yaml | tail -1 | xargs basename) 2>/dev/null || true`
Expected: 証拠YAMLが存在・itemsのverdict行が並ぶ(件数は後で報告書に記録)

- [ ] **Step 5: コミット**

```bash
git add backend/staging_tests/test_s4_aftermatch_items.py backend/staging_tests/test_s5_safety_items.py backend/staging_tests/test_s0_env_seed.py
git commit -m "test: add staging S4 after-match and S5 safety items (02#15/#19/#21/#20, D-23/D-10)"
```

### Task 10: フル実行+証拠YAML確認+報告書執筆

**Files:**
- Create: `docs/plans/M4/ws-3-report.md`(§10の形式)
- 成果物: `docs/testassets/results/ws3-staging-result-<ts>.yaml`(sessionfinish出力)
- 再生成差分(コミットしない): `docs/testassets/m4-seed.yaml`(対象日付のみ)

**Interfaces:**
- Consumes: Task 1〜9の全成果物
- Produces: 報告書・証拠(§7完了条件の証拠)

- [ ] **Step 1: フル手順の実行(staging-resetからのフルリセット)**

```bash
make staging-reset
make seed-generate ARGS="--base-datetime <実施日の翌日>T11:00:00+09:00"
make seed-verify
cd backend && uv run pytest staging_tests -q
```
Expected: seed-verify exit 0(`OK:` 出力)・staging-test全PASS・証拠YAML出力。所要45分目標(§2.4)

- [ ] **Step 2: 証拠YAMLと対応表の確認**

Run: `cd backend && uv run python -c "import yaml,glob; p=sorted(glob.glob('../docs/testassets/results/ws3-staging-result-*.yaml'))[-1]; d=yaml.safe_load(open(p)); ids=[i['id'] for i in d['items']]; print(len(ids), 'items'); [print(x) for x in sorted(set(ids))]"`
Expected: 02#20項目+追加試験(D-10・D-23・FR-21)+配置ケース+前置き検査(S0系)のID一覧。#13〜#23が全て含まれること(G3繰越回収の確認)

Run: `git diff --stat docs/testassets/m4-seed.yaml`
Expected: 対象日付(base_datetime)のみの差分
(**m4-seed.yamlはコミットしない** — 対象日付が実施日依存のため。報告書にその旨を記録する)

- [ ] **Step 3: 報告書の執筆(§10の形式)**

`docs/plans/M4/ws-3-report.md`を作る。内容は§10のテンプレートどおり(実測値・対応表・逸脱記録を含む)。

- [ ] **Step 4: 最終検査とコミット**

Run: `make lint && make test`
Expected: グリーン(unit基準実数+5件)

Run: `find backend/tests backend/staging_tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d`
Expected: 空

Run: `git status --short`
Expected: 変更は§4の一覧+docs/testassets/m4-seed.yaml(再生成差分・コミットしない)+証拠YAML(docs/testassets/results/は**コミットする** — ws-1/2の証拠と同じ扱い)

Run: `git diff --name-only main -- backend/alembic backend/pyproject.toml backend/uv.lock`
Expected: 空(マイグレーションなし・依存追加なし)

```bash
git add docs/plans/M4/ws-3-report.md docs/testassets/results/ws3-staging-result-*.yaml
git commit -m "docs: add ws-3 report and staging result evidence"
```

---

## 9. 検証手順(期待値つき。報告書の証拠としてこのとおり実行する)

1. **unit**: `make lint && make test` — 期待値: lintグリーン・`<基準実数+5> passed`(基準実数はTask 1 Step 1で記録・計画時実測1300)。件数がズレる場合は収集漏れ・既存試験への意図しない影響を報告書に記録して作業停止
2. **stub判定値ピン**: `cd backend && uv run pytest tests/unit/test_llm_stub_response_settings.py tests/unit/llm/test_llm_factory.py -v` — 期待値: 全PASS(既定値バイト一致ピンを含む)
3. **env経路**: `cd backend && LATCH_LLM_STUB_JEV_NOUL=0.90 LATCH_LLM_STUB_JEV_SCORE=3.6 uv run python -c "from latch.settings import Settings; s=Settings(); print(s.llm_stub_jev_noul, s.llm_stub_jev_score)"` — 期待値: `0.9 3.6`
4. **basename一意**: `find backend/tests backend/staging_tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 期待値: 出力なし(空)
5. **フル実行**: `make staging-reset && make seed-generate ARGS="--base-datetime <実施日翌日>T11:00:00+09:00" && make seed-verify && (cd backend && uv run pytest staging_tests -q)` — 期待値: seed-verifyの`OK:`出力・pytest全PASS・所要45分目標
6. **証拠**: `ls ../docs/testassets/results/ws3-staging-result-*.yaml` — 期待値: 実行ごとに1ファイル(itemsに02#ID+verdict)。「全項目グリーン」の証拠(10 §5)
7. **head/依存**: `git diff --name-only main -- backend/alembic backend/pyproject.toml backend/uv.lock` — 期待値: 空。head=0007はtest_s0_environment_prerequisitesのassertでstaging DB上でも検証済み
8. **integration収集**(実行はしない): `cd backend && uv run pytest -m integration --collect-only -q | tail -1` — 期待値: `261/1561 tests collected`(既存261・追加0)

## 10. 報告形式

**結果ファイル**: `docs/plans/M4/ws-3-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M4 ws-3(機能試験27項目のstagingフル実行)実行報告

- ブランチ: m4-ws-3 / ベース: <git rev-parse --short main>(ws-5マージ後・head=0007)
- 日付: <実行日>
- 実行者: agent3

## 結果サマリ

| 完了条件(計画§7) | 結果 | 証拠 |
|---|---|---|
| 1. lint+unit(基準+5) | PASS/FAIL | `<基準> passed`(基準=<Task 1 Step 1実測>) |
| 2. basename一意 | PASS/FAIL | コマンド出力空 |
| 3. stub判定値ピン | PASS/FAIL | pytest出力 |
| 4. staging-test全グリーン | PASS/FAIL | 証拠YAMLパス・所要時間 |
| 5. G3繰越回収(#13〜#23) | PASS/FAIL | 証拠YAMLの該当ID一覧 |
| 6. マイグレーションなし・依存追加なし | PASS/FAIL | git diff空・head=0007 assert |
| 7. 変更ファイル一覧どおり | PASS/FAIL | git diff --name-only mainの結果 |
| 8. 実行手順記録 | PASS | 下記「実行手順」 |
| 9. make test-ci | **スーパーバイザー検証待ち**(agent3は実行しない) |

## 実行手順(再現可能な形)

1. make staging-keys(worktree毎に鍵を生成)
2. make staging-reset(down -v→up→migrate→geo-import)
3. make seed-generate ARGS="--base-datetime <実施日>" + make seed-verify(実施日=<記録>・YAML差分は対象日付のみ)
4. (cd backend && uv run pytest staging_tests -q)(所要=<実測>分)
5. 証拠: docs/testassets/results/ws3-staging-result-<ts>.yaml

## 02#対応表(実行結果)

| 02# | 検査 | シナリオ | 合否 | 観測値(要約) |
|---|---|---|---|---|
| #5 | スモーク | S1 test_s1_item05_smoke | | max_candidates_per_intent=<実測>・jev_total=<実測> |
| ... | (02#20項目+追加試験3件+配置ケース全行を証拠YAMLから転記) | | | |

## G3繰越の回収対応(02#13〜#23)

| G3判定資料 | staging再実行 | 合否 |
|---|---|---|
| 02#13(muted含む) | test_s2_item13 | |
| ...(11行) | | |

## 設計からの読み替え・逸脱の記録(計画§2.1)

1. head=0006→0007(ws-5裁定): 実施済み
2. helpers_seedのconsent呼び出し: 実施済み(100人)
3. #11「2人×15件」→「6人×5件」(Active 5件/ユーザー制限・計画§2.1読み替え3)
4. #16「EX-027」→専用3人組(計画§2.1読み替え4)
5. #18「EX-028全員YES」→専用4人組(計画§2.1読み替え5)
6. (実装中に生じた追加の読み替えがあれば「変更前→変更後+理由」を記録)

## D-08保留の実測(計画§2.2との突合)

- S0集計: 6件超えのユーザー×日=<実測>件(計画時実測74件)・max=<実測>件/ユーザー×日
- #8の昇格件数: <実測>

## 期待値の確定記録(§0「staging検査の期待値確定手順」)

- S0突合の実測: passペア=<実測>・DB実在ペア=<実測>・代表EX 7組=<全生成/停止>
- #6両方向EXペア=<実測>

## コミット一覧

```
<git log --oneline main..HEAD>
```

## 補足(詰まった点・判断した点)
<あれば記載>
```

---

## 11. Self-Review(計画書の自己見出し・2026-10-03 agent2実施)

**(1) spec coverage(design §1.1〜§6との突合)**:

| designの要素 | 本計画の実装箇所 |
|---|---|
| §2.1 pytestスイート(testpaths外) | Task 4〜9(`backend/staging_tests/`・pyproject無変更) |
| §2.2 stub判定値固定(settings+gateway+compose 0.90/3.6) | Task 1〜3 |
| §2.2対抗策(a)(固定期待=reference_l1で数えられる量) | §2.5+Task 5〜9の検査設計 |
| §2.2対抗策(b)(専用シナリオ隔離) | §2.3+helpers_scenario(Task 5) |
| §2.2対抗策(c)(D-08事前集計) | §2.2(計画時実測74件)+test_s0_d08_retention_forecast(Task 5) |
| §2.3観測経路3種(API/DB SELECT専用/pushログ) | conftest fixtures+helpers_pushlog(Task 4) |
| §2.4 tick 60秒既定・待ち上限120〜180秒・ISO明示 | §2.4旅路表+helpers_wait・Task 6〜9の前進実装 |
| §2.5シナリオ→02#対応表(20項目+追加3件+配置) | Task 5〜9の検査ID(record_itemに02#IDを記録) |
| §2.6 embedding=案A(スタブのまま・設定変更なし) | スコープ外(llm_embedding_mode新設をしない・§6禁止) |
| §3.1ファイル構成 | §4スコープ(conftest/helpers 4+test 6+unit 1+settings/gateway/compose/Makefile) |
| §3.2触らないもの | §6禁止 |
| §3.3実行手順 | Task 10+§10報告書テンプレ「実行手順」 |
| §4テスト方針(TDD・回帰ピン・basename一意・lint) | §0規律+Task 1〜3+§7完了条件 |
| §5完了条件 | §7(検証コマンドつきに具体化・test-ciは検証待ち表記) |
| §6 supervisor承認6件 | 全て反映(§2.1に読み替え含めて一覧) |
| スーパーバイザー引継ぎ1〜6 | §2.1(1・2)・§0(3・4)・§4/§5(5)・§2.4(6) |

**(2) placeholder scan**: 「TBD」「TODO」「適切に」「必要に応じて」の文字列は含まない。検査コードは全てStep内に全文を載せた。Task 6 Step 1に一時行を残さないよう、 本文コードから`r_b`行・Task 8のno-opループ・Task 9 test_s5の未使用importは削除済み。

**(3) type consistency(ヘルパの定義と呼び出しの突合)**:

- `seed_all(api, db)`の返すキー(`meta_base`/`su_to_uuid`/`sd_to_uuid`/`l1_pass`/`ex_pass_sample`/`pass_pairs_per_user_day`/`users_ok`/`consent_ok`/`intents_ok`)とTask 6〜9での参照キー(`meta_base`/`sd_to_uuid`/`l1_pass`/`ex_pass_sample`/`pass_pairs_per_user_day`)を突合 — 一致
- `make_scenario_pair(api, db, *, key, name, when, ...)`とTask 6〜9の呼び出し(key=・name=・when=・category=・secondary=・visibility=・notification_level=・raw_text=・participants=は未使用)を突合 — participants引数は未使用のため省略可
- `my_latch(api, subject, *, status)`とTask 7〜9の呼び出し — 一致(status未指定="proposed")
- `make_scenario_group(api, db, *, key, name, when, members, min_participants, raw_text)`とTask 8の呼び出し — 一致
- `compute_mlr(db)`の返すキー(`denominator`/`numerator`/`mlr`)とTask 7/9の参照 — 一致
- `wait_until(cond, *, timeout_s, interval_s, what)`と全呼び出し — 一致(全てtimeout_s+what付き)
- `record_item(*, item_id, name, test, verdict, observed)`と全呼び出し — 一致(全キーワード引数)
- `ex_pair_ids(seed, ex_id)`(helpers_seed)と`_ex_intents(seed, ex_id)`(test_s3内)は別関数だが用途が異なる(無向ペアタプル vs intentsリスト)・名前衝突なし
- `push_log_records(*, tail)`(helpers_pushlog)とTask 7の呼び出し — 一致
- conftestの`EXPECTED_HEAD = "0007"`とtest_s0のassertとws-5の0007 — 一致

**(4) Review Focusの試験への紐付け**: §3の5項目は全てTask 4〜5の検査(同意・D-08・pushログ・YAML整合)とTask 1〜2(既定値回帰)に紐付け済み。

**(5) 自己発見・修正済みの問題(Self-Review過程で直した箇所)**:

1. Task 4 conftest.py: 未使用の`make_clock` importを削除・import並びをruff I順に修正
2. Task 5 helpers_scenario.py: 未使用の`_token()`関数と`import uuid`を削除
3. Task 6 test_s1_item07: 不要な`r_b`行を削除(コード本文を正の形に統一)
4. Task 8 test_s3_item18: no-opループを削除
5. Task 9 test_s5: 未使用import(`my_latch`)を削除
6. helpers_seed.wait_pipeline_settled: 初版のnonlocal方式を直列の安定確認ループへ書き換え(変数宣言順のバグを計画段階で除去)
7. test_s0_l1_unidirectional_match: EX突合の二重`ex_pair_ids`呼び出しを`sample_pairs`集合へ整理
8. **Task 6 test_s1_item07(重大)**: 初版は`make_scenario_pair`でペアを作っていたが、ヘルパは2人に**同一when**のIntentを作るため「最初は時刻を離して作る(time0 failで不在)」の前提と矛盾し最初からcandidateが存在してしまう。`_register`+`_create_intent`での直接作成(A=D+1 06:30・B=D+3 06:30)へ書き換えた
9. §4スコープ表: helpers_pushlog.pyの行が欠落していたため追加・作成ファイル数を12へ訂正・変更ファイル数を5へ訂正

**(6) 既知の残存リスク(agent3が初回フル実行で確定させるもの)**:

- 代表EX 7組(EX-006/008/010/013/015/017/020)の全生成はK_v=50の取得順に理論上依存する(決定的環境では1回確定すれば恒常)。揃わない場合は§0規律どおり作業停止・報告
- EX-028正常クラスタのgroup_candidates生成はD-08保留の影響を受けない(candidate行作成は閾値超過時点)が、実測を初回で記録する
- notifications payloadの`latch_id`キー名の実装照合(FR-21の(c)) — payload::text照合へ変えず実装形を確認してから直す(§Task 7備考)
- pushログのdocker logsパースはlatch-stagingのログがタイル上に長くなるケースでtail=5000で足りない可能性 — 足りなければtail引数を増やす(検査の性質上・緩めない)






