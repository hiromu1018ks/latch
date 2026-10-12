# LATCH

> 探さなくていい。条件だけ置いておけばいい。

LATCHは、ユーザーから「探す・募集する・誘う」という行動を取り去るマッチングサービスである。条件付きの意思(Intent)をあらかじめ預かり、条件が成立した瞬間にだけ相手と機会を提示する。MVPは今〜数日以内の食事・飲み・軽いアクティビティに絞り、イベント駆動+段階的絞り込み(5層ファネル)でAIコストを制御しながら、双方がYESと言う提案(Mutual Latch Rate)の精度を検証する。

## ドキュメント索引

| ファイル | 文書 | バージョン | 内容 |
|---|---|---|---|
| [docs/00-document-roadmap.md](docs/00-document-roadmap.md) | ドキュメント作成ロードマップ | v1.2 | 文書群の構成・依存関係・運用ルール(UI v2基準切替済み) |
| [docs/01-requirements.md](docs/01-requirements.md) | 要件定義書 | v0.7 | コンセプト、要件、MVP受け入れ条件、未決事項の解決記録 |
| [docs/02-scope-acceptance.md](docs/02-scope-acceptance.md) | スコープ・受け入れ条件合意書 | v0.4 | 「作る/作らない」の境界、受け入れ条件×検証方法のマッピング |
| [docs/03-ux-spec.md](docs/03-ux-spec.md) | UX仕様書 | v0.5 | 5画面フロー(01〜05)+簡易一覧2画面、通知、状態遷移、「夜と翡翠」デザインシステム |
| [docs/04-system-architecture.md](docs/04-system-architecture.md) | システムアーキテクチャ設計書 | v0.6 | 技術スタック(PostgreSQL/Pub/Sub/FCM等)、認証、コスト保護 |
| [docs/05-data-model-api.md](docs/05-data-model-api.md) | データモデル・API仕様書 | v0.6 | スキーマ、Index、API契約(認証系を含む全エンドポイント・UI v2分の拡張込み) |
| [docs/06-matching-pipeline.md](docs/06-matching-pipeline.md) | マッチングパイプライン設計書 | v0.6 | Layer 1〜5、Embedding第2段トリガー、Jev予算、保留キュー |
| [docs/07-jev-llm-spec.md](docs/07-jev-llm-spec.md) | Jev・LLM利用仕様書 | v0.7 | Parser/Embedding/Jevのプロンプト・スキーマ・timeout |
| [docs/08-privacy-safety.md](docs/08-privacy-safety.md) | プライバシー・安全設計書 | v0.5 | 表示最小化、プッシュ汎用文規制、匿名化、削除範囲 |
| [docs/09-verification-evaluation.md](docs/09-verification-evaluation.md) | 検証・評価計画書 | v0.6 | 5仮説×指標×判定基準、A/B設計、North Star測定定義 |
| [docs/10-test-env-nfr.md](docs/10-test-env-nfr.md) | テスト環境・非機能検証計画書 | v0.4 | 試験環境、機能検証の実行計画、性能・縮退試験 |
| [docs/11-release-plan.md](docs/11-release-plan.md) | リリース計画書 | v0.4 | フェーズ設計、密度要件、受け入れ手順、ロールバック基準 |
| [docs/12-development-roadmap.md](docs/12-development-roadmap.md) | 開発ロードマップ | v0.4 | 実装工程(M0〜M4+UI v2移行P0〜P7)、依存ハード制約、ゲート条件、並行トラック |

レビュー記録(意思決定の経緯)は `docs/reviews/` にある。要件定義書v0.1単体レビュー、文書群全体の総括レビュー(FR-01〜51と対応記録)、UI v2移行の費用・移行調査(cloud-cost-estimate.md・cloudflare-migration-study.md)を含む。

## 読む順序

- **最初に**: 01(要件定義)→ 02(スコープ合意)。何を 作り/作らないか が揃う
- **実装着手前**: 04(アーキテクチャ)→ 05(データモデル・API)→ 06(パイプライン)→ 07(Jev・LLM)。この順で実装の前提が揃う
- **UI実装**: 03(UX仕様)。UIの実装基準は `prototype/v2/`(「夜と翡翠」・00 運用ルール6)
- **検証・運用**: 09(検証・評価)→ 10(テスト環境)→ 11(リリース)

判断に迷ったら01第27節の8原則(設計原則)に戻る。文書間で矛盾を見つけたら、番号の大きい文書(詳細側)を正とし、01への反映を確認する。

## 開発環境

### 前提ツール

| ツール | 用途 |
|---|---|
| Docker + Docker Compose | ci常設環境(api/db/redis/worker/pubsub)・staging/load環境 |
| Node.js 20.19+(または22.12+)+ npm | frontend |
| mise(またはuv直接) | backendのuv・Python 3.13を導入する |

LLMは既定でスタブ(`LATCH_LLM_MODE=stub`・課金なし)で動く。実LLMを使う系統(g1-gate等)だけ `.env` に鍵を置く。

### 初回セットアップ

```bash
mise install                    # .mise.toml の uv を導入(初回のみ)
make setup                      # backend/.venv を構築(uv sync)
cd frontend && npm install      # frontendの依存
cd .. && make up                # compose常設環境を起動(healthy待ち)
make migrate                    # DBをalembic headまで(明示実行のみ)
make geo-import                 # 対象エリアの地物データをPostGISへ(初回のみ。事前に make geo-download)
```

- 地物データ(ISJのCSV・OSMのpbf)の入手先は `make geo-download` が表示する
- Intent保存はジオコーディング済みデータが前提(C5)。geo-importを飛ばすとIntent作成が422 GEOCODING_FAILEDになる

### 開発サーバーの稼働方法

**compose常設環境(backend)**

| 目的 | コマンド |
|---|---|
| 起動(healthy待ち込み) | `make up` |
| 状態確認 | `make ps` |
| ログ追視 | `make logs` |
| 停止(ボリューム保持) | `make down` |

- apiが `http://127.0.0.1:8000` で立ち、db(5432)・redis・worker(マッチングパイプライン)・pubsubエミュレータが揃う
- backendコード変更後はイメージの再buildが必要(ソースマウントなし): `docker compose build api worker && docker compose up -d api worker`
- マイグレーションは起動時に自動適用されない。`make migrate` を明示実行する

**frontend(vite dev・5173)**

```bash
cd frontend && npm run dev      # http://localhost:5173/ ・/v1 を127.0.0.1:8000へproxy
```

- 全APIが認証必須。初回は画面の「開発用トークンパネル」へIdPトークンを貼る:

  ```bash
  cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject demo
  ```

- API全体に60req/分のレート制限がある。429 RATE_LIMITEDが出たら1分待つ
- 詳細は [frontend/README.md](frontend/README.md)・[backend/README.md](backend/README.md)

**miniflare(Cloudflare Workers版・8787)**

```bash
make cf-dev       # 別ターミナルで実行。frontend build同梱・ローカルD1・Queues
make cf-probe     # cf-dev起動中に実行(runt互換probe・結果をdocs/testassets/results/へ)
```

- Workers Assetsでフロント・API・フォントが1オリジンで配信される: `http://127.0.0.1:8787`
- 提案形成まで動かすにはスタブ判定値を上げる(既定0.5では閾値0.60未満):

  ```bash
  cd cloudflare/api && uv run pywrangler dev --port 8787 --var "LATCH_LLM_STUB_JEV_NOUL:0.85"
  ```

- ローカルD1のマイグレーションは `cloudflare/migrations/*.sql` をdevが使う実sqliteへ直接適用する(`wrangler d1 execute --local`は別sqliteを見る。手順は [docs/plans/CLOUDFLARE-MIGRATION-STATUS.md](docs/plans/CLOUDFLARE-MIGRATION-STATUS.md))

### 試験

| 対象 | コマンド |
|---|---|
| backend lint(ruff format+check) | `make lint` |
| backend unit試験(毎コミットの規律) | `make test` |
| backend 全試験(unit+integration・compose環境が必要) | `make test-ci`(workerの停止/復帰は自動) |
| frontend 試験(vitest・実APIに触れない) | `cd frontend && npm test` |
| frontend build | `cd frontend && npm run build` |
| UI E2E(Playwright・実API・録画ギャラリー付き) | `make e2e`(compose常設+`make migrate`が前提) |
| 契約試験(参照実装=PostgreSQL) | `cd backend && uv run pytest tests/contracts/test_api_parity.py -q`(worker停止が前提) |
| 契約試験(Cloudflare D1実装) | `cd backend && uv run pytest tests/contracts/test_cloudflare_parity.py -q` |
| Cloudflare資産の単体試験 | `make cf-unit` |
| Cloudflare runtime probe | `make cf-probe`(cf-dev起動中) |

### 実LLM・精度ゲート(課金が発生する)

`.env` に `LATCH_LLM_MODE=real` と各プロバイダ鍵が必要。実行前にコスト試算を確認すること(STATUS運用メモ)。

| 目的 | コマンド |
|---|---|
| G1精度ゲート(Parser構造化+飲酒判定) | `make g1-gate` |
| Embedding実APIスモーク | `make embed-smoke` |
| Jev実APIスモーク(1呼び出し) | `make jev-smoke` |
| G2日本語評価 | `make g2-gate ARGS="--limit 2 --route both"`(makeは--以降を転送しないためARGS経由) |

### staging・load環境(M4の資産・別composeプロジェクト)

`make staging-up` / `staging-down` / `staging-test` / `staging-nfr-test` / `make load-up` / `loadperf` / `loadbf` ほか。ターゲットの一覧と説明は `Makefile` のコメントを参照(実行前提は docs/10 §1・`docs/plans/M4/`)。

### 実配備(Cloudflare)

```bash
export CLOUDFLARE_API_TOKEN=...   # 実配備は別承認のうえ実行(12 roadmap・CLOUDFLARE-MIGRATION-STATUS)
make cf-deploy                    # frontend build + Workersデプロイ(唯一のデプロイ経路)
```

## 現在の状態

- 開発工程は M4完了・G4承認済み(2026-10-06)→ **UI v2移行(prototype/v2基準)Phase 0〜7完了(2026-10-12・ゲート承認待ち)**。承認後に11 Phase 0(内部α)へ進む
- 進行状態の正本は [docs/plans/STATUS.md](docs/plans/STATUS.md)。UI v2移行の計画は [docs/plans/ui-v2-roadmap.md](docs/plans/ui-v2-roadmap.md)、フェーズ報告は `docs/plans/ui-v2/`
- `prototype/` はUI基準の参照物(`prototype/v2/`=現行基準・`prototype/`=v1)。変更しない
- 実装自動化のプロンプト定型は `.claude/prompts/`(supervisor / agent / reviewer)
