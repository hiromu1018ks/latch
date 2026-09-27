# LATCH

> 探さなくていい。条件だけ置いておけばいい。

LATCHは、ユーザーから「探す・募集する・誘う」という行動を取り去るマッチングサービスである。条件付きの意思(Intent)をあらかじめ預かり、条件が成立した瞬間にだけ相手と機会を提示する。MVPは今〜数日以内の食事・飲み・軽いアクティビティに絞り、イベント駆動+段階的絞り込み(5層ファネル)でAIコストを制御しながら、双方がYESと言う提案(Mutual Latch Rate)の精度を検証する。

## ドキュメント索引

| ファイル | 文書 | バージョン | 内容 |
|---|---|---|---|
| [docs/00-document-roadmap.md](docs/00-document-roadmap.md) | ドキュメント作成ロードマップ | v1.0 | 文書群の構成・依存関係・運用ルール |
| [docs/01-requirements.md](docs/01-requirements.md) | 要件定義書 | v0.3 | コンセプト、要件、MVP受け入れ条件、未決事項の解決記録 |
| [docs/02-scope-acceptance.md](docs/02-scope-acceptance.md) | スコープ・受け入れ条件合意書 | v0.2 | 「作る/作らない」の境界、受け入れ条件×検証方法のマッピング |
| [docs/03-ux-spec.md](docs/03-ux-spec.md) | UX仕様書 | v0.3 | 画面・通知・状態遷移、回答期限・見送り・通知上限の確定値 |
| [docs/04-system-architecture.md](docs/04-system-architecture.md) | システムアーキテクチャ設計書 | v0.3 | 技術スタック(PostgreSQL/Pub/Sub/FCM等)、認証、コスト保護 |
| [docs/05-data-model-api.md](docs/05-data-model-api.md) | データモデル・API仕様書 | v0.3 | スキーマ、Index、API契約(認証系を含む全エンドポイント) |
| [docs/06-matching-pipeline.md](docs/06-matching-pipeline.md) | マッチングパイプライン設計書 | v0.3 | Layer 1〜5、Embedding第2段トリガー、Jev予算、保留キュー |
| [docs/07-jev-llm-spec.md](docs/07-jev-llm-spec.md) | Jev・LLM利用仕様書 | v0.3 | Parser/Embedding/Jevのプロンプト・スキーマ・timeout |
| [docs/08-privacy-safety.md](docs/08-privacy-safety.md) | プライバシー・安全設計書 | v0.3 | 表示最小化、プッシュ汎用文規制、匿名化、削除範囲 |
| [docs/09-verification-evaluation.md](docs/09-verification-evaluation.md) | 検証・評価計画書 | v0.3 | 5仮説×指標×判定基準、A/B設計、North Star測定定義 |
| [docs/10-test-env-nfr.md](docs/10-test-env-nfr.md) | テスト環境・非機能検証計画書 | v0.2 | 試験環境、機能検証の実行計画、性能・縮退試験 |
| [docs/11-release-plan.md](docs/11-release-plan.md) | リリース計画書 | v0.2 | フェーズ設計、密度要件、受け入れ手順、ロールバック基準 |
| [docs/12-development-roadmap.md](docs/12-development-roadmap.md) | 開発ロードマップ | v0.1 | 実装工程(M0〜M4)、依存ハード制約、ゲート条件、並行トラック |

レビュー記録(意思決定の経緯)は `docs/reviews/` にある。要件定義書v0.1単体レビューと、文書群全体の総括レビュー(final-review.md、FR-01〜51と対応記録)を含む。

## 読む順序

- **最初に**: 01(要件定義)→ 02(スコープ合意)。何を 作り/作らないか が揃う
- **実装着手前**: 04(アーキテクチャ)→ 05(データモデル・API)→ 06(パイプライン)→ 07(Jev・LLM)。この順で実装の前提が揃う
- **UI実装**: 03(UX仕様)。05/06のフィールド・期限規定と対応して読む
- **検証・運用**: 09(検証・評価)→ 10(テスト環境)→ 11(リリース)

判断に迷ったら01第27節の8原則(設計原則)に戻る。文書間で矛盾を見つけたら、番号の大きい文書(詳細側)を正とし、01への反映を確認する。

## 現在の状態

- 文書群は総括レビュー(APPROVE収束)を経た実装引き渡し可能な水準
- 未決事項(D-xx)は全24項目解消済み。D-01/D-02(通知閾値・合格ラインの値)は運用データ待ちとして手順のみ確定
- `prototype/` は別作業のプロトタイプ。ドキュメント群とは独立
- 実装フェーズ(12のM0〜M4)に移行。進行状態は `docs/plans/STATUS.md`、実装自動化のプロンプト定型は `.claude/prompts/`(supervisor / worker / reviewer)
