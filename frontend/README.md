# LATCH frontend

M1 ws-5: `prototype/` を出発点とする本実装(00 運用ルール6・03 §10)。プロトタイプは
`prototype/` に温存されている(実装基準の参照物・変更しない)。

## セットアップ

1. Node.js 20.19+(または22.12+)とnpmが必要(`node --version` で確認)
2. `npm install`
3. repo rootで `make up`(compose常設環境: api 127.0.0.1:8000)
4. `npm run dev` → http://localhost:5173/

## API接続(開発)

- vite dev proxy が `/v1` を `http://127.0.0.1:8000`(compose常設api)へ転送する
  (vite.config.mjs。backendにCORS設定はないため同一オリジンで開発する)
- 全APIが認証必須(05 §5)。初回は画面上の「開発用トークンパネル」へIdPトークンを貼る。
  IdPトークンの発行(repo rootで実行・10 §1の内部CLI):

  ```
  cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject <任意のsubject文字列>
  ```

- apiはbuild型・ソースマウントなし。backendコード変更後は
  `docker compose build api && docker compose up -d api` が必要(STATUS運用ルール4)
- **API全体60req/分のレート制限(ws-4)が適用されている**。画面リロードやテキスト連打で
  429 RATE_LIMITED が出たら1分待って再操作すること

## テスト

`npm test`(vitest run・happy-dom・fetchモック。実api・実DB・実Redisを消費しない)
