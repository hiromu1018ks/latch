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

## E2E(Playwright・実API)

前提: repo rootで `make up` + `make migrate` 済み(compose常設api=127.0.0.1:8000)・
chromium導入済み(初回のみ `npx playwright install chromium`)。

| 目的 | コマンド |
|---|---|
| E2E実行のみ | `npm run test:e2e` |
| E2E実行+録画ギャラリー生成 | `npm run test:e2e:report`(repo rootなら `make e2e`) |
| ゆっくり再生で録画(視聴補助) | `LATCH_E2E_SLOWMO=300 npm run test:e2e:report` |
| 対話型レポート(失敗トリアージ) | `npx playwright show-report` |

### 録画ギャラリー(人間が俯瞰して閲覧するためのもの)

`npm run test:e2e:report` を実行すると `frontend/e2e-recordings/<実行日時>/` に
**テスト単位の動画(webm)** と `index.html` が生成される。**index.html をブラウザで
開くだけ**(file:// で動く純静的。サーバー不要)で:

- 機能群ごとのセクション(認証 / 同意 / 主幹フロー / 回答・undo / 成立・チャット …)と目次
- 各カード = 動画プレイヤー + テスト題名 + 合否 + 所要秒数 + 「注目点」1行
- 失敗テストは赤枠・赤badgeで強調される
- 過去runは最新3件まで自動削除(動画はgit管理外)
