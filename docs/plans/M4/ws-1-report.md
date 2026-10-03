# M4 ws-1(staging・load環境構築)実行報告

- ブランチ: m4-ws-1 / ベース: e1eabca(merge-base main…HEAD)
- 日付: 2026-10-03
- 実行者: agent3

## 完了条件の検証結果

| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test(unit件数) | PASS | lint `279 files already formatted / All checks passed!`(rc=0)・test `1262 passed, 261 deselected`(rc=0)・**unit全件数=1262**(既存1219+新規43=計画の設計値と一致) |
| 2 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → **空** |
| 3 | 実時間参照がclock.pyのみ・arch test | PASS | rgヒットは `backend/src/latch/core/clock.py` の5行のみ(36/105/118/119/126行)・`uv run pytest tests/unit/test_arch_no_direct_time.py -v` → **1 passed** |
| 4 | ci無変更・依存・alembic無変更 | PASS | `git diff e1eabca -- compose.yaml docker/postgres backend/alembic backend/pyproject.toml backend/uv.lock .env.example` → **空**・`git diff main -- Makefile \| rg "^-[^-]"` → **`.PHONY:` 行の削除1行のみ** |
| 5 | compose config妥当性 | PASS | `docker compose -f compose.staging.yaml config -q; echo $?` → **0**・`docker compose -f compose.load.yaml config -q; echo $?` → **0**(ci宛 `docker compose config -q` も0=読み取り検証のみ) |
| 6 | 受け入れ手順1〜9 | PASS | 下の受け入れ記録参照(全9手順PASS・手順5はClock戻し+トークン再発行の上再実行 — 補足) |
| 7 | 変更ファイル=§4の一覧どおり | PASS | merge-base e1eabcaとのdiff一覧が §4の19ファイル+**計画外追記1件(test_auth_settings.py・supervisor裁定2026-10-03)**=20ファイルと完全一致(下の一覧・`git status --short` 空) |
| 8 | worker実稼働確認 | PASS | worker2ログ: `pubsub topic created: projects/latch-staging/topics/match-events`・`pubsub subscription created: …/subscriptions/match-events-sub`・`worker started (app_env=staging)`+手順5のcreated→`processed`(worker→Pub/Sub→DBの実到達) |

## make test-ci(1480維持)

- **スーパーバイザー検証待ち**(§0の指示によりagent3は未実行。本単位はマイグレーション追加なし)

## 受け入れ確認の記録(10 第5節の形式: 試験ID・実施日・環境・入力条件・合否)

| 試験ID | 手順 | 実施日 | 環境 | 入力条件 | 合否 | 証拠(要点) |
|---|---|---|---|---|---|---|
| ws1-acc-1 | 手順1 staging-up+worker実稼働 | 2026-10-03 | latch-staging(worktree) | make staging-keys + staging-up | PASS | `ls .staging/keys`=`idp_test_jwks.json`・`idp_test_private.pem`。up がhealthy待ちでexit 0。ps=8サービス(db/redis/pubsub/api1/api2/nginx/worker1/worker2)Running・うちapi1/api2/nginx/db/redis healthy。worker2ログに `pubsub topic created`・`pubsub subscription created`・`worker started (app_env=staging)`。restart 0 |
| ws1-acc-2 | 手順2 migrate+別DB確認 | 2026-10-03 | 両プロジェクト | make staging-migrate | PASS | staging alembic_version=`0006`・ci=`0006`(実体別: staging users=`0`・ci users=`0`)。0005→0006適用ログ確認。ci users件数は手順9で不変を再確認 |
| ws1-acc-3 | 手順3 nginx越しAPI | 2026-10-03 | latch-staging | staging-token(SUBJECT=u1)→token→users | PASS | users POST=`201`。/health 3ポート(18001=api1直・18002=api2直・18000=nginx経由)とも `{"status":"ok","server_time":…}` |
| ws1-acc-4 | 手順4 Clock制御 | 2026-10-03 | latch-staging | 実施ISO: `2026-10-04T00:00:00+09:00` | PASS | set前show=`control: unset (real time)`(server_time=実時間04:19)。set後server_time=`2026-10-03T15:00:00.049+00:00`(期待の15:00近傍に追随)。advance 86400後=`2026-10-04T15:00:00.273+00:00`。2回目show=`control base: real=1791001149.489 virtual=1791126000.000` |
| ws1-acc-5 | 手順5 イベント注入 | 2026-10-03 | latch-staging | created/参照不在/毒 | PASS | (a)実在Intent(`4459bef0-…`)宛created→`processed`。(b)全ゼロUUID→`processed`(破棄・**quarantinedでない**)。(c)raw-payload(102 bytes・version型不正)→最終`quarantined`(sleep 40で再試行完了後) |
| ws1-acc-6 | 手順6 worker複数台冪等 | 2026-10-03 | latch-staging | 同一Event2回 | PASS | 重複SELECT(`GROUP BY event_type, source_intent_id, payload->>'version' HAVING COUNT(*)>1`)=**0行**。worker1/worker2ともRunning・restart 0 |
| ws1-acc-7 | 手順7 隔離(ci起動確認) | 2026-10-03 | ci+latch-staging | staging起動中 | PASS | ci(latch-ci)ps=api/db/redis healthy・pubsub/worker Upのまま継続。unit試験 `1262 passed`。**make test-ci は実行せず**(スーパーバイザー検証待ち) |
| ws1-acc-8 | 手順8 load環境up+同時稼働 | 2026-10-03 | latch-load | make load-up | PASS | load 8サービスRunning(28000系: 28000/28001/28002/25432/26379/28085)・healthy基準はstagingと同一。staging(18000系)と同時起動で無干渉(双方ps全Running)。確認後load-down(ボリューム保持) |
| ws1-acc-9 | 手順9 既存回帰+ci継続 | 2026-10-03 | 両方 | lint/test/staging-down | PASS | lint rc=0・test `1262 passed`。staging-down後もci常設環境継続稼働(全サービスUp)。ci users=`0`(手順2記録と不変)。staging/loadボリューム保持(downのみ・`down -v` 未使用) |

## 固定値の変更有無(design §2・本計画)

- 案B(別プロジェクト)・ポート表・`latch:clock:control`・基準点JSON形式・6種choices・`.staging/keys`: **変更なし**
- 本計画の追加2点(test_clock_wiring.py・.gitignore): **実施どおり**

## 逸脱記録(計画外の変更)

### 計画外追記(supervisor裁定・2026-10-03)

- `backend/tests/unit/auth/test_auth_settings.py` の
  `test_auth_settings_are_exactly_eight_fields` — 期待集合(auth_fields)へ
  `"auth_testkeys_dir"` を1行追記(既存7行は変更なし・インラインコメントで
  auth_系8項目=関数名eight_fieldsの件数一致を記録)。
- 経緯: Task 1実行中に、本追加が同機械検査と必ず衝突すること(計画書§3の
  事前特定の漏れ)を発見し一度BLOCKED報告。supervisorが案A(機械的追従)を承認
  (M1 ws-5のtest_rate_limit_wiring・M2 ws-2のtest_llm_factoryと同型)。
  コミット e841e25 に同梱。

### 計画書記載コードの軽微な修正(テスト期待値・検査規約を満たすため・挙動不変)

1. `core/clock.py` — `ControlledClock.set` のdocstringを複数行化(計画書記載の
   1行docstringがE501行長88超のため)
2. `core/clock_cli.py` — `run_advance` の表示を `{float(seconds)}s` へ(計画書の
   テストが `"advanced by 86400.0s"` を期待する一方、実装スニペットは
   `{seconds}s` でint引数時に`86400s`と表示される齟齬の解消)
3. `core/clock_cli.py` — モジュールdocstringの「datetime.nowを直接書かない」という
   **説明文自体**がarch testのFORBIDDEN_TOKENS(部分文字列検索)にヒットしたため、
   「実時間APIの直接呼び出しを書かない」へ言い換え
4. `tests/unit/auth/test_tools.py` — 計画書記載テストコード内の
   `from latch.auth import testkeys` が未使用のため削除(F401)

### 受け入れ手順の実運用での補正(期待結果はすべて得られた)

- 手順5実行時、手順4の `advance 86400` により手順3で発行したアクセストークンが
  **期限切れ**になりIntent作成が401となった(=Clock追随で期限判定が効いている
  ことの副次立証)。Clockを `set <実時間>` で戻し、トークンを再発行した上で
  (a)(c)を再実行した(計画書の手順設計上、Clock進行後のトークン使い回しは
  考慮されていなかった・機械的解消・テスト目的に影響なし)。
- 計画書Task 7 Step 5の検証コマンド
  `docker compose -f compose.load.yaml config | rg "127.0.0.1:(28000|…)"` は、
  config出力がポートをロング形式(`published: "28000"`・`host_ip: 127.0.0.1`)へ
  展開するためヒットしない。`rg "published"` で6ポート×2プロジェクトの列挙と
  `host_ip: 127.0.0.1` の両面で代替確認した(28000系6ポート・18000系6ポート・
  ともにhost_ip=127.0.0.1限定)。
- 同Stepの `rg -c "LATCH_CLOCK_MODE: controlled"` の期待値4は、config出力に
  x-backend-env・x-api-common・x-worker-commonの共通定義ブロックも含まれるため
  実測7。4サービスへの配線は `config --format json` で
  api1/api2/worker1/worker2 のenvironmentを厳密確認した
  (全4サービス: clock_mode=controlled・app_env=staging(loadではload)・
  project_id・stub・stub・/keys)。

## 変更ファイル一覧(merge-base e1eabcaとのdiff・20ファイル)

```text
作成(§4の9):
  backend/src/latch/core/clock_cli.py
  backend/tests/unit/test_controlled_clock.py
  backend/tests/unit/test_clock_cli.py
  backend/tests/unit/test_clock_wiring.py
  backend/tests/unit/test_events_publish_cli.py
  compose.staging.yaml
  compose.load.yaml
  docker/nginx/staging.conf
  docs/plans/M4/ws-1-report.md
変更(§4の10+計画外追記1):
  .gitignore
  Makefile
  backend/src/latch/auth/testkeys/__init__.py
  backend/src/latch/core/clock.py
  backend/src/latch/events/__main__.py
  backend/src/latch/main.py
  backend/src/latch/settings.py
  backend/src/latch/worker/__main__.py
  backend/tests/unit/auth/test_auth_settings.py   (計画外追記・supervisor裁定2026-10-03)
  backend/tests/unit/auth/test_tools.py
  backend/tests/unit/test_settings.py
```

## コミット一覧

```text
bde9963 chore: add staging and load make targets
01989c9 chore: add staging and load compose projects with nginx front end
c8cf47d feat: add manual match event publisher to events CLI
82d791a feat: allow staging testkeys dir override via env
1860699 feat: wire default clock through make_clock in api and worker entry
8f08f4e feat: add clock control CLI (show/set/advance)
1c0831d feat: add ControlledClock with redis-backed shared anchor
e7859c4 docs: record supervisor ruling (option A) and deviation log for ws-1 task 1
e841e25 feat: add clock_mode and auth_testkeys_dir settings for ws-1
f420010 docs: record ws-1 BLOCKED report — new auth_testkeys_dir setting breaks existing auth field machine check
```

## 補足(詰まった点・判断した点があれば)

- 本作業中にmainが進んだ(cba4931・M4 ws-2計画書追加)。完了条件4/7の突合は
  分岐点(merge-base e1eabca)とのdiffで実施した。スーパーバイザーのマージ時に
  docs側は共存するはず(本ブランチはdocs/plans/の既存ファイルに触れていない)。
- BLOCKED(2026-10-03)→supervisor裁定(案A)→継続の経緯は上の逸脱記録のとおり。
  指示4(同種衝突は機械的追従なら実装継続可)に基づき、以後の同種衝突は
  発生しなかった(逸脱は上記1件のみ)。
- staging環境は手順9でdown済み・load環境もdown済み(ボリュームは保持)。
  ci常設環境は全工程で無変更・継続稼働を確認済み。
