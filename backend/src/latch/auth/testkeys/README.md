# テスト用IdP鍵ペア(ci/staging)

- `idp_test_private.pem` / `idp_test_jwks.json`(kid=test-idp-1)は**ci・試験専用**のRS256鍵ペア。
  テスト専用で公開を前提とした値であり、本番(prod)では使用できない(`build_auth_service` の起動ガードが拒否)
- テストユーザーのIdPトークン発行: `uv run python -m latch.auth issue-idp-token --provider google --subject <sub>`
- 再生成: `uv run python -m latch.auth gen-keypair --out-dir <dir>`(10 第1節のテスト用認証構成。staging専用ペアの注入にも使用)
