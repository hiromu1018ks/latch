# M0雛形(scaffold) 実行報告

- ブランチ: m0-scaffold / ベース: 8323498
- 日付: 2026-09-27
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make setup/lint/test | PASS | `rm -rf backend/.venv && make setup && make lint && make test` すべてexit 0。lint: `18 files already formatted` + `All checks passed!`。test: `30 passed, 3 deselected in 0.04s` |
| 2 | 4サービスhealthy + /health ok | **FAIL(検証不能)** | `make up` → `permission denied while trying to connect to the docker API at unix:///var/run/docker.sock`。実行ユーザー(uid=1000 misty, groups=misty,wheel)がdockerグループに未所属のためdocker APIに接続不能(root:docker の rw-rw---- ソケット)。`docker compose ps`・`curl http://127.0.0.1:8000/health` も同じ理由で実行不能。**注記: workerサービスにhealthcheckが未定義(composeは計画書Task10のコードブロックどおり)のため、再検証時もworkerのSTATUS列は `healthy` 表記にならない。完了条件2の文言とcompose構成の不整合は計画書由来 — workerへのhealthcheck追加の要否はsupervisor判断とし、本実装では計画書どおり変更していない** |
| 3 | make test-ci | **FAIL(検証不能)** | 同上。`make test-ci` → `docker compose up -d --wait` の時点で permission denied。代替検証: `uv run pytest -m "not integration" -v --collect-only` で integration 3件がunit実行から除外されること(`not collected (ok)`)、全収集33件(unit 30 + integration 3)を確認済み |
| 4 | 6点再現性+arch test | PASS | `uv run pytest tests/unit/test_clock_reproducibility.py tests/unit/test_arch_no_direct_time.py -v` → `8 passed in 0.01s`(期限/バッチ/Bucket/debounce/JSTリセット/時刻検証×2 + arch test) |
| 5 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` → `backend/src/latch/core/clock.py:33:        return datetime.now(UTC)` の1行のみ |
| 6 | 禁止領域差分なし | PASS | `git diff --name-only main -- prototype README.md .claude 'docs/0*.md' 'docs/1*.md' docs/reviews docs/plans/STATUS.md` → 出力なし(空)。`git diff main -- .gitignore` → 既存行変更なしの追記のみ(`+# Python (backend)` ブロック9行)。`git status --short` → 空 |

## 固定値の変更有無(design.md §6)
- ci環境=ローカルcompose常設: 変更なし
- DB共用=ci内API/Worker共有: 変更なし
- Python 3.13 / PostgreSQL 17 / Redis 8 / uv 0.12.19: 変更なし(uv 0.12.19 はmise shimで確認。実行Pythonは uv導入の 3.13.15)

## コミット一覧
```
c4f60b3 fix: FakeClockの非UTC入力をUTCへ正規化(Clock契約now()=tz-aware UTCの強制)
2ccd19d docs: M0雛形の実行報告(完了条件6項目の証拠)
6ac2eca test: ci常設環境へのintegration到達試験(db/redis/api)
42d6720 feat: ci常設環境(compose 4サービス)とMakeターゲット・README
0f77bf5 feat: Worker本体と python -m latch.worker 入口(graceful shutdown)
0bc8871 feat: create_app ファクトリと /health(Clock差し替え両経路)
4f71e6a feat: Settings(app_env/log_levelのみ・LATCH_プレフィックス)
6858256 test: 実時間直接参照を禁止するarch test(C2・G0コード検査の自動化)
04d2a0e test: Clock適用範囲6点の再現性証明(期限/バッチ/Bucket/debounce/JSTリセット/時刻検証)
acfa77f test: JST日付境界(0時・月初・UTCとの9時間ずれ)の立証
2ea8dfb feat: FakeClock(set/advance・決定的・スレッド安全)
39039ac feat: Clock ABCとSystemClock(C2・tz-aware UTC・JST固定オフセット)
198f32d chore: uvプロジェクトとruff/pytest設定の初期化(M0雛形 Task1)
```

## 補足(詰まった点・判断した点)

### 完了条件2・3が検証不能になった環境的事実
- このセッションの実行ユーザーは `docker` グループに所属していない(`id` → groups=misty,wheel)。`/var/run/docker.sock` は `root:docker` の `rw-rw----`。
- sudoはパスワード必須(非対話実行不可)。rootless docker(`~/.docker/run`・`/run/user/1000/docker.sock`)は存在せず、docker contextは default のみ。
- design.md §2.2「開発機(動作確認済み: Docker 29 + Compose v5)」との差異は、実行主体(対話ユーザー vs 本セッション)の権限差と推定される。**dockerグループへの参加(`sudo usermod -aG docker <user>` + 再ログイン)または同等の権限付与後、`make up && make test-ci` を再実行すれば完了条件2・3は検証可能**。ファイル・構成側の修正は不要(compose.yaml・Dockerfile・Makefileは計画書§Task10のコードブロックどおり作成済み)。

### 実装上の裁定(計画書コードと「毎コミットlintグリーン」規律の衝突を解消した箇所)
計画書§0は毎コミットで `make lint`(ruff format --check + ruff check)グリーンを要求するが、計画書掲載のコードブロックにはruff違反が数件含まれていた。いずれも意味を変えない機械修正とし、設定側(pyprojectのruff select)は計画書どおり不改変とした:
- Task 2/3: `import threading`・`from datetime import date` は、そのタスク時点で未使用(F401)になるため、実際に使用するタスク(Task 3/Task 4)で追加。
- Task 2以降: UP017 により `timezone.utc` → `datetime.UTC` 表記に統一(ruff --fix 相当)。
- Task 5: E501(88文字超)2箇所を docstring 3文字短縮・上限試験の式の変数化で解消。
- Task 8: `clock: Clock = Depends(get_clock)` が B008 違反のため `Annotated[Clock, Depends(get_clock)]` 形式に変更(ruff設定不改変・FastAPI推奨形。create_app(clock=) と dependency_overrides の両経路試験はGREEN)。
- Task 9: E501 2箇所(シグネチャ折返し・コメント行分離)と F401(FakeClock未使用import削除)を解消。

### arch testの検出力確認(Task 6 Step 2)
プローブファイルによる赤の演出を両パターンで実施し、期待どおりの失敗メッセージを確認済み:
- `import time / print(time.time())` → `_arch_probe.py: time.time` でFAIL
- `from time import monotonic` → `_arch_probe.py: from time import` でFAIL(迂回import検出)
- 削除後は GREEN

### 最終レビュー(fresh-context reviewer)による指摘と対応
- **Important(修正済み)**: FakeClockがtz-aware非UTC(JST等)の初期値・set値をそのまま保持し、`now()` がClock契約「tz-aware UTC」(design §2.4・計画書§2)に反する値を返せる — `/health` の `server_time` が `+09:00` になり得た。**対応**: `FakeClock.__init__/set` で `astimezone(UTC)` 正規化を追加(TDD: `test_fake_clock_normalizes_non_utc_to_utc` RED→GREEN、全unit 30件GREEN)。
- **Minor(先送り)**: arch testは `import time as t; t.time()` 等のエイリアスimportを抜ける(計画書指定トークンの限界)。ASTベース検査への強化は後続課題。
- **Minor(先送り・supervisor判断)**: workerにhealthcheckが無いため完了条件2「4サービスともhealthy」が字義どおり成立しない(上表の注記参照)。
- reviewerが判断を見送った事項: PGDGパッケージ名の実解決・composeランタイム挙動(SIGTERM処理等) — いずれもdocker権限回復後のビルド・起動で証明される。

### その他
- 依存は fastapi / uvicorn / pydantic-settings + dev 4種(pytest / pytest-asyncio / httpx / ruff)のみ。スコープ外コード(DB接続・Gateway・認証等)は未作成。
- unit試験は30件(全GREEN)、integration試験は3件(compose到達・docker権限回復後に `make test-ci` で実行)。

## スーパーバイザー検証記録(2026-09-27、マージ d2b53d7 時点)

実行ユーザーをdockerグループへ参加(usermod+セッション再起動)させた後、完了条件2・3を検証し、
全6項目PASSとしてmainへマージした。上表のFAIL(検証不能)2項目はこの時点で解消済み。

| # | 再検証結果 | 証拠 |
|---|---|---|
| 2 | **PASS** | `make up` → compose ps: db/redis/api `Up (healthy)`・worker `Up`。`curl 127.0.0.1:8000/health` → `{"status":"ok","server_time":"2026-09-27T06:06:31.063848+00:00"}`(tz-aware UTC)。判定はdesign/plan修正済み文言(5c11be1)による |
| 3 | **PASS** | `make test-ci` → `33 passed in 0.10s`(unit 30 + integration 3) |

- 1/4/5/6もスーパーバイザーがworktree・main両側で独立再実行しPASSを確認
- 追加確認: `pg_available_extensions` で postgis 3.6.4 / vector 0.8.6 がdbイメージに同梱(PGDGパッケージ名の実解決を立証)
- マージ後のmainから `make up`・`make test` を実行し、常設環境が正規位置から運用できることを確認(30 passed)
