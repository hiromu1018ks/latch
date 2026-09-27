# M0 ws-3(認証) 実行報告

- ブランチ: m0-ws-3 / ベース: 2c031d4
- 日付: 2026-09-27
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | `make lint` → "59 files already formatted / All checks passed!"。`make test` → "161 passed, 38 deselected in 2.09s"(unit全体。auth分は82件: tests/unit/auth -q → "82 passed in 1.57s") |
| 2 | make test-ci §4.2-1〜7 | **test-ci=スーパーバイザー検証待ち** | `make test` 収集エラーなし(上記)。`uv run pytest --collect-only tests/integration/test_auth_api.py -q` → "8 tests collected in 0.00s"(§0のSTATUS運用ルールにより実行せず) |
| 3 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` → `backend/src/latch/core/clock.py:33: return datetime.now(UTC)` の1行のみ。`uv run pytest tests/unit/test_arch_no_direct_time.py -v` → "1 passed" |
| 4 | 依存追加3本のみ | PASS | `git diff main -- backend/pyproject.toml` → `+ "pyjwt[crypto]>=2.10"` `+ "redis>=5.2"`(dependencies)/ `+ "fakeredis>=2.26"`(dev)の3行のみ。`git diff --stat main -- backend/uv.lock` → "1 file changed, 183 insertions(+)"(依存解決差分のみ) |
| 5 | alembic差分なし | PASS | `git diff --stat main -- backend/alembic` → 出力なし(空) |
| 6 | 触るファイルがスコープどおり | PASS | `git diff --name-only main | sort` → §4の一覧と完全一致(auth配下15ファイル+tests/unit/auth 10ファイル+integration 2ファイル+pyproject/uv.lock/settings/main/README+compose.yaml+本報告書。core/・worker/・llm/・Makefile・docker/・docs 01-12・prototype に差分なし)。`git status --short` → 空。**追記**: 59cd61b で `backend/alembic/env.py` が追加(スーパーバイザー明示許可 — 補足10参照) |
| 7 | CLI発行トークンが検証できる | PASS | `uv run python -m latch.auth issue-idp-token --provider google --subject demo` → `eyJhbGciOi...`(JWT文字列・exit 0)。`test_cli_issue_token_verifies` → PASS(CLI出力→IdPVerifier検証のラウンドトリップ) |

**追記(2026-09-27)**: スーパーバイザー検証(test-ci)で失敗2件を特定されたため、指示に従い修正(コミット 59cd61b)。修正後 `make lint` / `make test`(164 passed)グリーン。test-ci の再検証はスーパーバイザー実施待ち。

## 固定値の変更有無(design.md §6・本計画§8)
- アクセストークンHS256(design §6-1): 変更なし
- リフレッシュRedis保持(design §6-2): 変更なし
- profile_complete=行存在導出(design §6-3): 変更なし
- refresh/logoutの503(design §6-4): 変更なし
- prod実URLは設定経路のみ(design §6-5): 変更なし
- 本計画§8のIF確定事項(tokens同期関数・rt鍵SET+JSON・AuthService secret直受取・deps AccessTokenClaims返し・authenticate追加): 変更なし

## スーパーバイザー検証手順(test-ci実行時)
1. ws-4マージ後に main で `docker compose up -d --build api`(compose.yaml の環境変数追加と新コードを反映)
2. `make migrate`(必要に応じ。ws-4の0002がhead)
3. `make test-ci` — §4.2-1〜7(test_auth_api.py 8件)を含む全体グリーンで完了条件2を検証
4. READMEのcurl例(`python -m latch.auth issue-idp-token …` → POST /v1/auth/token)で200交換を確認(完了条件7)

## コミット一覧
```
59cd61b fix: test-ci失敗2件の修正(asyncpg UUID行値・alembic logger無効化)
8af4c96 feat: latch.auth公開IF再export・integration試験(G0証拠)・README認証手順
de9b3ef feat: 認証CLI(issue-idp-token・gen-keypair。10 第1節テスト用認証構成)
ecaa8fa feat: 認証ルータ・require_authenticated・main lifespan統合(エラーenvelope)
cda69e9 feat: build_auth_service(prodガードつき工場)
b99f782 feat: refresh/logout/authenticateユースケース(回転応答・失効一連)
e0b9a6b feat: tokenユースケースとusers読み取り(user.id応答分岐・503包み)
7a56135 feat: RedisセッションStore(GETDEL回転・族失効・失効リストTTL)
24f73e0 feat: IdPトークンのJWKS検証(RS256・同梱/URL両方式・503分岐)
bdb844e feat: アクセスJWT発行/検証(HS256ピン・Clock手動期限判定)
ff1765f feat: 認証設定8項目(redis_url・アクセスsecret・IdP検証設定)
fb5b071 feat: 認証依存(pyjwt/redis/fakeredis)とテスト鍵ペア・例外階層
```

## 補足(詰まった点・判断した点があれば)

計画§8/design §6の固定値は不変。以下は計画書コードの軽微な修正・実行環境(PyJWT 2.15.0)に起因する対応で、いずれもテスト対象の仕様(状態遷移・応答形式)は不変:

1. **PyJWT 2.15.0 は file:// スキームのJWKS URLを拒否**(`Invalid JWKS URI scheme 'file'` — jkuヘッダインジェクション対策)。design §2.4「file:// はurlopenが対応」の前提が成立しないため、URL方式のunit試験(test_idp.py 3件・test_tools.py 1件)は **in-process のローカルHTTPサーバー(127.0.0.1の空きポート・daemonスレッド)** でJWKSを配信する形に変更した。外部プロセス不要・決定的(design §4.1のunit方針は維持)。**READMEの鍵管理手順も「file:// URL 可」を「http/httpsのみ(file:// は拒否)」に修正**した — stagingでのローカルJWKSファイル利用はHTTP配信が必要
2. **`PyJWKClient.fetch_signing_key_from_jwt` が PyJWT 2.15.0 に存在しない**(計画書Task 4注記の予見どおり)。`await asyncio.to_thread(client.get_signing_key_from_jwt, token)` へ置き換えた(注記が指定する代替経路)
3. **`idp.py` の鍵解決をdecode例外網の内側へ移動**(計画書コードのバグ修正)。元実装では `_resolve_key`(get_unverified_header)が try の外のため、形式不正トークン("garbage")が素の `DecodeError` で素通りし `AuthService.token` のcatch-allで503に包まれていた。05 第5節の401 INVALID_IDP_TOKENが正しい挙動で、Task 6のTDDがこの欠陥を捕捉した
4. **`testkeys/__init__.py` の `RSAPrivateKey` import元を `cryptography.hazmat.primitives.asymmetric` 直下→ `.rsa` へ修正**(直下にはexportされておらずImportErrorになるため)
5. **`test_tokens.py` のclaim構成検査で decode に `verify_iat/verify_aud: False` を渡すよう修正**(テスト側)。PyJWT既定のiat検証はシステムクロック比較のため実行時刻によって非決定的に失敗する( ImmatureSignatureError )。audience未指定decodeもtokenのaud存在だけで拒否される。構成検査に両検証は不要
6. **`test_tools.py::test_cli_issue_token_verifies` に `--iat` を追加**(計画書Task 10注記の予見どおり)。CLI発行時刻(SystemClock=実行時刻)と検証側FakeClock(NOW=2026-09-27T12:00Z)の取り合わせで、実行時刻がNOWの1時間以上前だとexpired判定になるため、発行時刻をNOWに固定して決定的にした
7. その他: 計画書テストコードの docstring/コメント行長(ruff E501 88字制限)を数件短縮、import順をruff isortに合わせて自動修正(`ruff check --fix`)。意味の変更なし

### 追記(2026-09-27): スーパーバイザー検証フィードバックによる修正(コミット 59cd61b)

スーパーバイザーのtest-ci検証で失敗2件の原因を特定していただき、**計画書外の修正指示**を受けた(修正対象はいずれも本単位の実装範囲内または明示許可済み)。指示内容と修正を以下に記録する:

8. **test_2_token_with_inserted_user_row の503 — `make_user_lookup` のUUID変換**。asyncpg はuuid列をUUID「インスタンス」で返すため `uuid.UUID(row[0])` が `AttributeError('UUID' object has no attribute 'replace')` になり、`AuthService.token` のcatch-allで503に包まれていた。行が存在しない場合は変換が実行されないため test_1 だけ通っていた。unit試験はスタブlookupのためこの変換経路が実行されていなかった。**修正**: UUIDインスタンスはそのまま返し、文字列等其他型の場合のみ `uuid.UUID(str(value))` で構築する `_coerce_user_id` を導入。回帰試験として unit 3件(UUIDインスタンス・文字列・行なし)を `test_service_token.py` へ追加(修正前にREDを確認 — 指摘と同一のAttributeErrorで再現)
9. **test_logs_contain_no_token_or_subject のcaplog空 — alembicの `fileConfig`**。`backend/alembic/env.py` の `fileConfig(config.config_file_name)` は既定で `disable_existing_loggers=True` であり、integration試験のconftestが `alembic upgrade` を実行した時点で収集済みimportの `latch.*` ロガーが無効化される。このため後続のunit試験でcaplogに何も入らなかった(make test 単体では通る・test-ciでのみ失敗)。**修正**: `fileConfig(config.config_file_name, disable_existing_loggers=False)` を渡す
10. **`backend/alembic/env.py` は計画書§5の禁止欄に含まれるが、この修正はスーパーバイザーが明示的に許可した**(マイグレーション追加ではなくロギング設定の修正であるため)。`backend/alembic/versions/` への差分は引き続きなし(完了条件5は不変)。修正後 `make lint` / `make test`(164 passed・test_service_token 7→10件)グリーン。test-ci の再検証はスーパーバイザー実施待ち
