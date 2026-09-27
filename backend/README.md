# LATCH backend

FastAPIバックエンド(uv・srcレイアウト・Python 3.13)。仕様は `../docs/` を参照。

## クイックスタート

```bash
mise install            # .mise.toml の uv を導入(初回のみ)
make setup              # uv sync(backend/.venv 構築)
make lint && make test  # 毎コミットの規律
make up && make ps      # ci常設環境(db/redis/api/worker)
make test-ci            # unit+integration
```

## マイグレーション(ws-1)

- `make migrate` — ci常設DBへマイグレーションを適用(`alembic upgrade head`)。**明示実行のみ**(API/Workerの起動時自動実行はしない)
- 接続先は `LATCH_DATABASE_URL`(デフォルト `postgresql+asyncpg://latch:latch@127.0.0.1:5432/latch`)
- 現行版の確認: `cd backend && uv run alembic current`
- 新しいマイグレーション追加: `cd backend && uv run alembic revision -m "説明"`(0001_initial_schema は手作業で作成済み)

## 規律

- 製品コード(`src/latch/`)で実時間を直接参照しない — すべて `latch.core.clock` 経由(C2)。
  `tests/unit/test_arch_no_direct_time.py` が毎コミットで強制する
- `python`/`pip` を直接使わない。`uv run` 経由
- 新しい依存は計画書のスコープ確認を経て追加する(現状: fastapi/uvicorn/pydantic-settings のみ)

## 認証(ws-3)

### テストユーザーのトークン発行(10 第1節のテスト用認証構成)

```bash
# テストユーザーのIdPトークン(JWT)を発行(同梱テスト鍵 kid=test-idp-1 で署名)
cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject demo

# ci環境のapiでアクセストークンへ交換(M0はUser行が無いため user.id=null・profile_complete=false が正当動作)
curl -s -X POST http://127.0.0.1:8000/v1/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"provider":"google","idp_token":"<発行したトークン>"}'
# → {"access_token":"…","token_type":"Bearer","expires_in":3600,"refresh_token":"…",
#    "user":{"id":null,"profile_complete":false}}

# リフレッシュ(回転式: 応答のrefresh_tokenで再度呼べる。旧トークンの再利用は族失効+401)
curl -s -X POST http://127.0.0.1:8000/v1/auth/refresh \
  -H 'Content-Type: application/json' -d '{"refresh_token":"…"}'

# ログアウト(AuthorizationのJWTをRedis失効リストへ+リフレッシュ族を失効。204)
curl -s -i -X POST http://127.0.0.1:8000/v1/auth/logout \
  -H 'Authorization: Bearer <access_token>'
```

### 鍵管理(design §2.5)

- ci/試験用のIdP鍵ペアは `src/latch/auth/testkeys/` にコミット(kid=test-idp-1)。
  prodでは `build_auth_service` の起動ガードがテスト既定値の残存をValueErrorで拒否する
- staging専用ペアの生成と注入: `uv run python -m latch.auth gen-keypair --out-dir <dir>` →
  生成された `idp_test_jwks.json` をHTTP(S)で配信し、そのURLを
  `LATCH_AUTH_IDP_JWKS_URL_GOOGLE` / `_APPLE` へ設定(検証側PyJWKClientは
  http/httpsのみ受け付ける — file:// は拒否される)。
  発行時は `--private-key <dir>/idp_test_private.pem` で指定
- prodは `LATCH_AUTH_ACCESS_SECRET`(HS256 secret)・`LATCH_AUTH_IDP_JWKS_URL_*`・
  `LATCH_AUTH_IDP_ISSUER_*`・`LATCH_AUTH_IDP_AUDIENCE_*` の実値が必須(未設定・テスト値は起動失敗)
- アクセストークンを直接発行するツールは存在しない — 常に POST /v1/auth/token 経由で取得する
