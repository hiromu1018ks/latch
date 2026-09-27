# M1 ws-1(users API) 実行報告

- ブランチ: m1-ws-1 / ベース: de30cc8
- 日付: 2026-09-27
- 実行者: agent3

## 完了条件の検証結果

| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | lint PASS / test PASS(条件付き・補記1参照) | `make lint` → `81 files already formatted` `All checks passed!`。`make test` → `1 failed, 239 passed, 67 deselected`。失敗の1件は本単位と無関係の既存タイムボムテスト `tests/unit/auth/test_tokens.py::test_token_payload_structure`(詳細は補記1)。当該1件を `--deselect` で除外した全体実行は `239 passed, 68 deselected` |
| 2 | make test-ci §4.2-1〜6 | **test-ci=スーパーバイザー検証待ち** | unit相当実行(`-m "not integration"`・上記deselect付き)で収集エラーなし=integrationファイルのimport正当性を検証済み。`uv run pytest --collect-only tests/integration/test_users_api.py -q` → `test_1_registration_full_flow`〜`test_6_unauthenticated_rejected_on_both_endpoints` の `6 tests collected` |
| 3 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now\|utcnow\|time\.time\|time\.monotonic' backend/src` のヒットは `backend/src/latch/core/clock.py:33` の1行のみ。`uv run pytest tests/unit/test_arch_no_direct_time.py -v` → `1 passed`(users/配下はスキャン対象に含まれる) |
| 4 | alembic等に差分なし | PASS | `git diff --stat main -- backend/alembic backend/pyproject.toml backend/uv.lock backend/src/latch/settings.py compose.yaml Makefile` → 空(出力なし) |
| 5 | 触るファイルがスコープどおり | PASS(1件のファイル名変更あり・補記2) | `git diff --name-only main \| sort` → main.py + users 4ファイル + unit/users 3ファイル(うち `test_users_routes.py` は計画書記載の `test_routes.py` から改名・補記2)+ integration 1ファイル + 本報告ファイル。`git status --short` → 空(全コミット済み) |
| 6 | 初回登録フロー1本試験 | 収集確認済み/実行は検証待ち | `tests/integration/test_users_api.py::test_1_registration_full_flow`(token false → 登録201 → me 200 → 再token true までを1本で含む) |

## 固定値の変更有無(design.md §6・本計画§8)

- me不在時404(design §6-1): 変更なし
- 生SQL継続・ORM導入はws-3へ委譲(design §6-2): 変更なし
- エラー優先順序 UNDER_AGE>USER_EXISTS(design §6-3): 変更なし
- auth側lookupとのSELECT重複許容(design §6-4): 変更なし
- display_name上限なし(design §6-5): 変更なし
- 本計画§8のIF確定事項(IntegrityError分類・Callableシグネチャ・jsonb渡し・UUID正規化・lifespan独立判定): 変更なし

## スーパーバイザー検証手順(test-ci実行時)

1. `docker compose build api` — **apiイメージの再ビルドが必須**(make test-ci の compose up は再ビルドしないため・design §1.2確定値13)
2. `docker compose up -d --wait`
3. `make test-ci` — test_users_api.py(#1〜#6)を含む全体グリーンで完了条件2・6を検証
   (マイグレーション追加なしのため `make migrate` は不要。実行しても冪等)
   ※ 補記1のタイムボムテストは 2026-09-27 13:00 UTC 以降の実行では常に失敗する。本単位のスコープ外のため修正していない。検証時は `cd backend && uv run --group geo pytest --deselect tests/unit/auth/test_tokens.py::test_token_payload_structure` で除外するか、失敗が本単位起因でないことを踏まえて判断されたい
4. マージ時のmain.py競合(ws-2と交点)は両側追記保持で解消する

## コミット一覧

```text
aec2bda feat: users公開IFの再exportとintegration試験(作成のみ・実行はスーパーバイザー)
09424e5 docs: M1 ws-1の実行報告(完了条件6項目の証拠・test-ciは検証待ち)
b8fb984 feat: usersルータ(POST /v1/users・GET /v1/users/me)とmain統合
ce497a5 feat: make_user_service(実SQL束ね・RETURNING id・制約名分類接続)
942784a feat: UserService(register/get_me)とIntegrityError制約名分類
2036c09 feat: users例外階層と満年齢純粋関数(JST暦日タプル比較)
```

(本報告ファイル自体の更新コミットがこの後に1件加わる)

## 補足(詰まった点・判断した点)

1. **既存タイムボムテストの失敗(本単位と無関係)** — Task 4の `make test` 実行時(2026-09-27 13:00:53 UTC)、既存の `tests/unit/auth/test_tokens.py::test_token_payload_structure` が `jwt.exceptions.ExpiredSignatureError` で失敗した。原因は同テストが FakeClock 固定時刻(2026-09-27 12:00 UTC)で発行したJWT(exp=13:00 UTC)を、システムクロックで期限検証する `pyjwt.decode`(verify_exp有効)に通している構造で、実行時刻が 13:00:00 UTC を超えた時点で必ず赤化する。Task 1〜3のコミット時(同日 12:00 UTC 前)は `make test` がグリーンだったことが、本単位の変更が原因でないことを裏付ける。`git diff`(コミット済み+作業ツリー)でも auth 領域が本単位で無変更であることを確認した。計画書§5により既存テストファイルは修正禁止のため対応せず、検証時の当該1件を `--deselect` で除外し、ここに記録した。恒久対応(verify_exp 無効化または時刻の引上げ)はスーパーバイザーの判断を待つ。
2. **テストファイル名の変更: `test_routes.py` → `test_users_routes.py`** — 計画書Task 4が作成を指定した `backend/tests/unit/users/test_routes.py` は、pytest の既定importモード(prepend)が `__init__.py` のないディレクトリのテストをbasenameでimportするため、既存の `tests/unit/auth/test_routes.py` とモジュール名が衝突し `make test` の収集が `import file mismatch` で落ちる(実測済み)。代替案のうち、`__init__.py` 作成は計画書§2(design §3.1一覧外+auth慣例=なし)違反、`pyproject.toml` への importmode 設定は§5禁止のため、ファイル名変更が最小の解とした。試験内容は計画書記載のものから一字一句変更していない。design.md §3.1のファイル構成から見た変更であるため、ここに「変更前: `test_routes.py` → 変更後: `test_users_routes.py` + 理由(上記)」を記録する。
3. **ruff format / isort の整形差分** — Task 1〜3で計画書記載コードに整形差分が発生したため、計画書§0の規定どおり `uv run ruff format .` と `uv run ruff check --fix .` を適用した(§0が予定する手順の範囲内。論理変更なし)。
4. **それ以外の計画書からの逸脱なし** — compose常設環境への操作(`make test-ci` / `docker compose` 系)は一切実行していない。`alembic/`・`auth/`・`core/`・`llm/`・`geo/`・`worker/`・`settings.py`・`pyproject.toml`・`uv.lock`・`compose.yaml`・`Makefile`・既存テスト・conftest類は無変更(完了条件4・5の検証どおり)。
