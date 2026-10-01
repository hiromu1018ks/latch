# LATCH 実装進行状態

このファイルが実装の現在位置。スーパーバイザー(メインセッション)は各作業単位の完了・
ゲート承認をここに記録する。新セッションは .claude/prompts/supervisor.md を貼って
開始し、本ファイルから状況を復元する。

## 現在

- フェーズ: **M3実装中**(2026-09-30 M3単位表Goサイン・ws-1〜ws-9)
- 実装言語: Python (FastAPI) — 2026-09-27決定
- 並列構成: worktree完全分離(herdr worktree)。ゲート毎に人間承認
- 学習資産: docs/learn/(Diátaxis・初心者向け)を運用開始。**各マージ後にagent4で同期**(規約は .claude/prompts/agent4-learn.md に一元化)。M1の6単位分+M2 ws-1〜ws-8分すべて同期済み
- 次の着手: **M3 ws-2(バッチ群)の実装から**(単位表は2026-09-30ユーザーGoサインで確定・ws-1〜ws-9)。**外部SDK(FCM/Firebase Admin等)の設計・実装ではcontext7で一次確認**(2026-09-29ユーザー指示)。**Sonnet級の高単価モデルを含む実行・運用は原則なし**(2026-09-30ユーザー指示・フォールバックはHaiku 4.5へ変更済み)
- M3実装への引き継ぎメモ(G2承認済みの解釈18件の要約・詳細は各design.md。ws-3/ws-7の通知・表示系には②の書式・区分が関連): ①07 §4のscore正規化は分母=4の解釈で実装(ws-5設計§5-5・/5が意図ならdocs修正が必要) ②Layer 5解釈5件(対象開始時刻=max(time_start)・area_name=geo中点の逆転ジオコーディング・nearby通知のno履歴検査とmatch_level='low'・time_summary書式=JST YYYY-MM-DD HH:MM・category_secondaryは種Intentの値 — ws-6設計§5-4〜8) ③ws-7解釈9件(Pool人数緩和=min<=4 AND max>=3の専用検索+Layer 3同一計算・種=起点で起点max>=3がトリガー・aggregateのHは集合単位再検証でペア行latch_scoreは不記入・D-06通知順序はメンバー重複の開いている集合の上位1近似・グループ候補へnearby適用なし・member_scores=seed_id+versionsのみ・Poolの同一Bucket=time_startの30分Bucket・Pool検索HNSW上限=50・area_name=全メンバーgeo_center平均点 — ws-7設計§5) ④ws-8解釈3件(p95条件はtimeout呼び出しをレイテンシ=6秒で母集団に入れp95位置≧timeoutで開放・エラー率判定の最小サンプル=2〔単発不発動の根拠節から〕・02#5〜#12はci環境で実施=staging再実行はM4-3 — ws-8設計§5-1/2/4・§6)
- プロバイダ前提(2026-09-28解消): 3系統とも契約済み(ユーザー申告)。API鍵3本の実値をスーパーバイザーが確認済み(Anthropic・Gemini・TypeSafe)。マイグレーションは原則不要(idempotency UNIQUE索引・embedding vector(768)+HNSW・評価世代UNIQUEともM0で作成済み)

## M0 作業単位

| 単位 | 内容 | 出典(12) | 依存 | 状態 |
|---|---|---|---|---|
| 雛形 | リポジトリ構成・FastAPI最小構成・テスト基盤・ci環境(API 1/Worker 1/DB共用)・Clock+モック | M0-1, M0-3 / C2 | — | 完了 |
| ws-1 | PostgreSQLスキーマ+Index+マイグレーション(friendshipsはEntity定義のみ) | M0-2 / C1 | 雛形 | 完了 |
| ws-2 | LLM Gateway: 3系統集約・送信記録・プロバイダ抽象・スタブ(応答記録+レイテンシ注入) | M0-5 / C4 | 雛形 | 完了 |
| ws-3 | 認証: token/refresh/logout・Redis失効リスト・JWKS検証・テスト用認証構成(staging鍵ペア+JWT発行ツール) | M0-4, M0-7 / C3 | ws-1(users) | 完了 |
| ws-4 | 地物データ取り込み(位置参照情報+OSM→PostGIS)・正転/逆転ジオコーディング | M0-6 / C5 | ws-1(PostGIS) | 完了 |

実行wave: 雛形 → (ws-1 ∥ ws-2) → (ws-3 ∥ ws-4)

## M3 作業単位

| 単位 | 内容 | 出典(12) | 依存 | 状態 |
|---|---|---|---|---|
| ws-1 | LATCH応答系コア: GET /v1/latches・/v1/latches/{id}・POST response(yes/no/defer・FOR UPDATE+条件付きUPDATE・409分岐)・Intent/LATCH遷移表・latch_status_events同時挿入・競合クローズ・グループ回答(全員YES成立・部分成立禁止・期限確定)・D-05回答期限式・Calibration記録(回答確定時に作成・segmentフラグlexical/semantic)。M2 ws-7引継ぎの設計確認候補(ON CONFLICT昇格でlatches.group_candidate_idが旧gidのまま残りうる)を含む | M3-1, M3-2, M3-9 / 05 §5〜§6・06 §6・07 §6・09 §2.3 | M2(latches・proposals・group_candidates) | 未着手 |
| ws-2 | バッチ群: expiry_sweeper(60秒周期・SKIP LOCKED)・Intent期限切れバッチ(同一スケジューラ)・catch-upスキャン(60秒・Event発行せず直接投入)・リセットジョブ(JST 0時日次・暦月初JST 0時月次・完了時に保留キュー再評価イベント)。G1引継ぎ02#4(期限経過後expired遷移)の実確認 | M3-3, M3-4 / 06 §6・§9〜§10・04 §5 | ws-1(C9同一直列化方式) | 未着手 |
| ws-3 | 通知: FCM(汎用文のみ・条件サマリ禁止・drinkingも同様式)・アプリ内通知(お知らせ)・D-05回答期限の通知・D-08上限(日6件・同時3件)と保留の提示順制御。notifications先行書き込み(M2 ws-6)の媒体確定 | M3-5 / 03 第4節・第8節・08 §2.6 | ws-1 | 未着手 |
| ws-4 | チャット+実施自己申告: チャットAPI(matched以降のみ書込可・ブロック時409 CHAT_READONLY)・実施自己申告(D-09・3日以内・cancelledには通知しない) | M3-6 / 05 §5・09 第2.2節 | ws-1 | 未着手 |
| ws-5 | ブロック・通報: blocks(双方向判定・Redisキャッシュ)・D-23成立後即時適用(提案cancelled・チャット読取専用・「このチャットは利用できません」)・reports | M3-7 / 08 第5節・06 §6 | ws-1・ws-4 | 未着手 |
| ws-6 | 削除・退会: FR-19/FR-22の削除範囲(候補を処理済み含め全削除・30日定期削除)・calibration_records匿名化の第一段(D-13) | M3-8 / 08 §2.5・05 §6 | ws-1〜ws-5 | 未着手 |
| ws-7 | フロントエンド(コア3画面): ホーム・提案詳細(visibility分岐・一致度区分・残時間表示・グループ必要人数・回答操作3択[参加する]/[今回は見送る]/[辞退する])・成立済み詳細(チャット) | M3-10 / 03 第2節・第5〜7節 | ws-1・ws-4 | 未着手 |
| ws-8 | フロントエンド(周辺2画面): お知らせ一覧・設定(通知許可・ブロック管理)・不成立表示「この提案は成立しませんでした」の一文統一(T4整合) | M3-10 / 03 第2節・第8節・00 運用ルール6 | ws-3・ws-5・ws-7 | 未着手 |
| ws-9 | G3ハーネス: 02#13〜#23 E2E整備(#22は公開設定2値分岐・#13はmuted含むドライラン)・グループLATCH 6検証(集合生成・全員YES成立・部分成立禁止・期限確定・通知順序・競合クローズ)・追加試験(D-10年齢制限・D-23成立後ブロック・プッシュ表示規制FR-21・自己ペア除外FR-17) | 12 M3完了条件 / 10 第3節・09 第3節 | ws-1〜ws-8 | 未着手 |

実行wave: ws-1 → (ws-2 ∥ ws-3 ∥ ws-4) → ws-5 → (ws-6 ∥ ws-7) → ws-8 → ws-9

## G3(完了条件 — 12 M3より)

- [ ] 02#13〜#23がstagingでグリーン(#22は公開設定2値分岐・#13はmuted含むドライラン確認。10 第3節)
- [ ] グループLATCHの6検証がグリーン(集合生成・全員YES成立・部分成立禁止・期限確定・通知順序・競合クローズ。09 第3節)
- [ ] 追加試験がグリーン: D-10年齢制限・D-23成立後ブロック・プッシュ表示規制(FR-21)・自己ペア除外(FR-17)(10 第3節)
- [ ] プロトタイプ6画面が03に従い実装済み(実装基準はprototype側を正とする。00 運用ルール6)

## G3判定の待ち事項(人間領域)

1. **FCM実送信の前提資材**(Firebaseプロジェクト・サーバー認証情報・Web Push用VAPID鍵) — 実装はスタブで進行可(LLM Gatewayスタブと同型)。実機送信確認の頃までにユーザー準備(2026-09-30 supervisor判断でM3着手の阻害外と置く・要確認)
2. **02#13〜#23の実施環境(ci/staging)** — G2解釈④と同型の判断をG3時にユーザー確認

## G3時確認事項(実装解釈の累積・ゲート承認時にまとめて確認)

1. ws-1: **paused参加Intentを含む成立**(05 §6遷移表のmatched行は「active→matched」表記だがcancelled/expired行は「active・paused」のため、提示後にpauseされた提案も回答可能と読む。design §5-3)
2. ws-1: **segmentフラグの計算時点=回答確定時の現行Intent文言**(09 §2.3「提案化時に判定し記録」を判定規則の定義と読み、レコード作成は07 §6の回答確定時。提案〜回答間にIntent更新がある場合のみ評価行〔バージョン固定〕とsegment〔現行文言〕の時点がずれうる。design §5-4)

## M2 作業単位

| 単位 | 内容 | 出典(12) | 依存 | 状態 |
|---|---|---|---|---|
| ws-1 | イベント駆動基盤: Match Event 5種の発行網羅(intents CRUD組み込み・resumeはversion+1のupdate種発行)・Pub/Sub連携(ci環境の具象は設計で確定)・Matching Worker第1段(draft対象外・作成即時/更新のみdebounce 10秒トレーリング窓・idempotency key(event_type, source_intent_id, version)・version検査・5回再試行→quarantined・削除Eventの候補無効化・参照先不在=processed破棄とpayload不正=隔離の区別) | M2-1 / 06 §9・01 §16・10 §4.7 | M1 ws-3(intents)・M0(worker・Redis) | 完了 |
| ws-2 | Embedding Worker: 正規化テキスト生成(raw_text不使用)・LLM Gateway Embedding系統real化(gemini-embedding-001・timeout 2秒・再試行なし)・intents.embedding/embedding_model書き込み・embedding_completed発行・バックフィル・embedding既存はスキップして第2段相当へ直接投入 | M2-2 / 07 §3・06 §9・D-15 | ws-1 | 完了 |
| ws-3 | Layer 1 Hard Filter+Layer 2 Candidate Retrieval: SQL+PostGIS判定(自己除外・時間交差+flexibility・ST_DWithin(r_a+r_b)・ペア予算min 500円未満fail・人数2∈双方・ブロック・category_primary完全一致・飲酒ペアは双方20歳以上)・正規化テキストHNSW cosine上位K_v=50(同点intent_id昇順)・embedding IS NULL/draft対象外。02#9 Hard Filter単体試験 | M2-3, M2-4 / 06 §2〜§3・05 §3 | ws-2(fixture直入れで並行可) | 完了 |
| ws-4 | Layer 3 Cheap Judge+コスト保護: cheap_score=0.5×類似度+0.3×ルール+0.2×語彙重なり(D-04降格NGは計算対象外)上位K_c=20・Jev予算(1Intent 40回/日・1ユーザー120回/日・Redis JST日付キー)・再評価頻度30分(reeval:{intent_id} TTL)・D-16カウンタ(日次30,000・月次600,000・80% alertに第一候補/フォールバック内訳) | M2-5, M2-9 / 06 §4〜§5・04 §5 | ws-3 | 完了 |
| ws-5 | Layer 4 Jev: LLM GatewayへSystem One IF追加(state+型つき質問→answers)・TypeSafe Jev(jev-1.13.0)・429/529/timeoutでフォールバックLLM(Sonnet 5)へ切替(SDK backoff無効化・再試行なし)・jev_resultへprovider/model記録・K_j=8配分(1対1最低4回保証・未判定ペア継続優先)・同一評価世代スキップ(同バージョン組はH再検証のみ) | M2-6 / 06 §5・07 v0.5 §1・§4・04 §4 D-16 | ws-4・T1(確定済み) | 完了 |
| ws-6 | Layer 5 LATCH Engine: L=H×MutualScore×C(mutual=min)・閾値0.80・D-08上限(日6件/ユーザー・同時3件/Intent)超過はlatches candidate保留・提示順(対象時刻昇順・Score降順)・提示時D-05式再計算・proposal生成(visibility分岐: summary_only全フィールド/hidden_until_matchはheadcount+match_level)・nearby_also存在通知・muted通知抑制・D-07再提案制御(defer抑制min(24時間,残時間/2)・\|Δscore\|≧0.05・世代変化は無条件)・latch_status_events記録・再評価経路(30分Bucket・catch-upスキャン2時間/30分) | M2-7 / 06 §6・§9〜§10・03 D-05/D-07/D-08 | ws-5 | 完了 |
| ws-7 | グループマッチ: 候補Pool(同一Bucket・地域・カテゴリ・Layer 3通過・上限15・cheap_score降順)・貪欲法(種max>=3+Hard互換追加・3〜4人・作成user_id相異)・group_candidates記録+全ペアmatch_candidates生成・集約=H×min(ペアMutualScore)×C・通知はaggregate降順1集合のみ・未判定ペアはstatus=candidate保持し次評価のJev予算最優先 | M2-8 / 06 §5・§7〜§8・D-06・D-24 | ws-6 | 完了 |
| ws-8 | 縮退運転+G2ハーネス: circuit breaker(窓1分・第一候補エラー率50%超 or p95>6秒で開放・開放中フォールバックLLM継続・60秒後半開・1リクエスト試験)・フォールバックも失敗でskipped保留・02#5〜#12 E2E・K上限裏付け試験・冪等性(同一Event2回投入)・障害注入 | M2-10 / 06 D-15・10 §4.5〜§4.7 | ws-1〜ws-7 | 完了 |

実行wave: ws-1 → (ws-2 ∥ ws-3) → ws-4 → ws-5 → ws-6 → ws-7 → ws-8

## G2(完了条件 — 12 M2より)

- [ ] 02#5〜#12がci/stagingでグリーン(#9はHard Filter単体試験。10 第3節)
- [ ] K上限の裏付け試験: 密集配置で Vector ≤50 / Cheap Judge ≤20 / Jev ≤8回 / Pool ≤15が記録で守られ、切り詰めが決定的(K_v超過時の同点intent_id昇順。10 第4.6節)
- [ ] 冪等性: 同一Event2回投入でmatch_candidatesが二重生成しない(10 第4.7節)
- [ ] 縮退: 第一候補TypeSafe Jevの障害注入でフォールバックLLMへ切替し判定継続、フォールバックLLMも障害でskipped保留し提案ゼロ、circuit breakerが開放→半開する(10 第4.5節)
- [ ] 日本語評価(09 v0.5 第4節のG2必須): TypeSafe Jevのゴールドセット(日本語・評価ペア500件以上)による較正・精度評価を、オーナーが確定した合格基準で実施。不合格時はフォールバックLLM繰上げ判断をオーナーへ持ち帰る

## G2判定の待ち事項(人間領域)

1. **評価ペア500件+の整備と合格基準の確定** — 2026-09-29ユーザー確定: ①評価実施は**実課金で可**(実行前に金額試算をスーパーバイザーが報告してから動かす) ②**正解ラベルは原則スーパーバイザー/エージェント側で確定**(各ケースに判断根拠を記録しユーザー確認は可能な限りゼロへ寄せる。残る境界はまとめて1回だけ確認) ③合格基準はスーパーバイザー草案→**オーナーが最終決定**(G2直前で可)。**整備完了(2026-09-29・5ddb04a)**: g2-jev-goldset.yaml 520ペア=status confirmed(ラベル機械確定+rationale・境界flag6件はsupervisor裁定でfalse・機械検査ALL PASS。コスト試算: 両経路≈$4.75・上限$15)。評価実行(harness)はG2時・合格基準草案はG2前
2. **TypeSafe契約詳細の確認記録**(任意) — 2026-09-28ユーザー申告「すべて契約済み」。ZDR・漏洩通知・Telemetry条項の確認内容をdocs/reviews/へ文書化するならG2まで

## M1 作業単位

| 単位 | 内容 | 出典(12) | 依存 | 状態 |
|---|---|---|---|---|
| ws-1 | users API: POST /v1/users(初回登録・birth_date必須・18歳未満422 UNDER_AGE)・GET /v1/users/me | M1-1 / 05 §5 | M0(usersスキーマ・認証) | 完了 |
| ws-2 | Intent Parser(07 §2: 確定済みシステムプロンプト実装・アプリ層補完の単一規則・D-04連携ng_unverifiable→warnings)+ POST /v1/intents/parse(同期LLM・timeout 10秒・再試行なし・503 LLM_UNAVAILABLE/422 VALIDATION_ERROR切替 D-17) | M1-2, M1-3 / 07 §2・05 §5 | M0(LLM Gateway) | 完了 |
| ws-3 | intents CRUD: POST(active/draft)・GET・PATCH・DELETE・pause/resume・draft→active遷移(全検証通過後に受理・初回MatchEvent発行)。ジオコーディング正転の保存組み込み・alcohol_involvedのサーバ側確定・時刻検証(過去不可・+7日上限・active時のみ) | M1-4, M1-5 / 05 §5〜§6 | ws-1・ws-2 | 完了 |
| ws-4 | レート制限: Active 5件・作成20件/日・更新6回/時・API 60req/分(Redis・JST日付キー) | M1-6 / 08 §5.4・04 §5 | ws-3・M0(Redis) | 完了 |
| ws-5 | フロントエンド(prototype準拠): parse連携・条件リストの動的連結・有効期限の既定選択計算+disabled化・必須3フィールド催促・判定不能NG条件のNG行・注意表示・保存API接続(active/draft。03 第10節の既知差分解消) | M1-7 / 03 §3・§10 | ws-2〜ws-4 | 完了 |
| ws-6 | 実プロバイダadapter(Parser=Anthropic Haiku 4.5・llm_mode=real・鍵はLATCH_ANTHROPIC_API_KEY)+G1精度ゲートharness(Parser入力セット+飲酒判定セットをdocs/testassets/で実行・合格基準は07 D-17/09 §4.3) | 12 M1完了条件 / 07 §1〜§2・09 §4.3・T1 v0.2 | ws-2・T1 Parser契約(2026-09-28済) | 完了 |

実行wave: (ws-1 ∥ ws-2) → ws-3 → ws-4 → ws-5

## G1(完了条件 — 12 M1より)

- [x] 02#1(下書き経路含む)〜#3がci環境でグリーン(2026-09-28。マージ後main test-ci 561 passed。下書き経路=CRUD試験のdraft系。10 第3節の振り分けどおりbackend integration試験が本体。**02#4は保存時検証(過去不可・+7日)まで実施済み・「期限経過後expired遷移」の確認はexpiry_sweeper不在(M3-3)のため未実施〔ws-3設計§6-2の裁定待ち〕**)
- [x] Parser構造化精度ゲート: 入力セット30件以上で category 85% / time.start 90% / location 90% / participants 80% / budget 90%(07 D-17)。**2026-09-28実測合格**(g1-result-20260928-210042: category 100%・time.start 93.75%・location 100%・participants 84.4%・budget 100%)
- [x] alcohol_involved精度ゲート: recall 100%・precision下限90%(09 第4.3節)。**2026-09-28実測合格**(g1-result-20260928-210042: recall 100%・precision 100%・FN=0。初回203517はrecall 95.8%〔FN=A-034〕→規則7改訂〔07 v0.6〕で是正)
  - **G1実測(2026-09-28・make g1-gate・証拠=docs/testassets/results/g1-result-20260928-203517.yaml)**: Parser構造化=**合格**(category 32/32・time.start 29/32=90.6%・location 32/32・participants 28/32=87.5%・budget 32/32)。alcohol=**不合格**(precision 100%〔fp=0〕・**recall 23/24=95.8%〔fn=1〕**。FN=A-034「barでコーラだけ飲むつもり。今日21時、天文館のbarで」をfalse判定)。**G1全体は未達**。打ち手は07 §2規則7のプロンプト改善のみ(12 §7)で、プロンプト変更時は両ゲートを再実行(09 §4.3)。背景: A-034はT3確定時のユーザー確認ケース(barでコーラ=true)だが、規則7内の「バー等の語→true」と「アルコールを指さない用法はfalse」の**優先関係がプロンプトに明示されていない**
- [x] プロンプト変更のたびに両ゲートを再実行できる状態(2026-09-28。PARSER_SYSTEM_PROMPT定数+全文ピン試験+docs/testassets/入力セット〔confirmed〕。**ゲートharness実装済み=ws-6マージ 8fbf91b・make g1-gateで1コマンド再実行可**)

## G1判定の待ち事項(人間領域)

1. **T1 LLMプロバイダ契約**(08 §3のD-14基準6件+契約5条件) — 精度ゲートは実プロバイダでの実測が前提。契約未確定プロバイダは技術基準を満たしても不採用。**2026-09-28: Jev系統(C案裁定)・Parser系統(Anthropic Haiku 4.5・API鍵設定済み)とも確定しG1は実施可能。残るEmbedding系統(gemini-embedding-001)の契約はM2開始前が締切**
2. ~~T3草案の確認~~ → **確定済み**(2026-09-28 ユーザー委任によりスーパーバイザーが裁定・要確認7件とも草案どおり。A-033=false/A-034=trueの根拠はYAML末尾の裁定記録)
3. ~~02#4の期限経過確認の扱い~~ → **(a)で裁定済み**(2026-09-28 ユーザー裁定。M1は保存時検証+期限選択UIで#4の本体を検証済みとし、「期限経過後のexpired遷移」はM3-3のexpiry_sweeper実装時にG3で確認。G1記録に注記する)

## G0(完了条件 — 12 M0より)

- [x] 認証3エンドポイントとJWKS検証経路がci環境でグリーン(2026-09-27。test-ci 270 passedにtest_auth_api.py#1〜7=実HTTP・実Redis・実DB含む。スーパーバイザー独立検証でasyncpg UUID変換とalembic fileConfigの両欠陥を発見・修正済み 59cd61b)
- [x] 時刻参照がすべてClock経由であることをコード検査で確認(arch test test_arch_no_direct_time.py が毎コミットで強制。rg直接確認でも core/clock.py:33 のみ)
- [x] LLM Gatewayがスタブで3系統の呼び出しを記録できる(test_gateway_{parse,embed,jev}.py=ok/timeout/error全記録・機微非混入・Clock由来時刻)
- [x] 対象エリアの地物データでジオコーディングがPostGIS上で完結する(外部送信経路ゼロ。**暫定エリア=鹿児島市天文館周辺約3km四方・T2確定後に差し替え**: 実データISJ令和7年46201分46行+OSM九州extract・bbox内563行を取り込み、正転「天文館」→天文館(osm_poi)・逆転2点→「鹿児島市泉町」/「鹿児島市祇園之洲町」(別1kmセルで別地名)を確認。geo配下network import禁止arch testあり)

## 完了記録

(形式: 単位 / コミット / 証拠パス / 日付 — マージごとに追記)

- 雛形 / マージ d2b53d7(14コミット・設計 406aa53・計画 8323498・条件文言修正 5c11be1)/ docs/plans/M0/scaffold-report.md(+scaffold-design.md・scaffold-plan.md)/ 2026-09-27
  - 完了条件6項目をスーパーバイザーが独立検証(unit 30・test-ci 33・arch testでC2強制・compose常設環境3healthy+worker running・PostGIS 3.6.4/pgvector 0.8.6同梱確認)
  - 実行環境メモ: dockerグループ参加(usermod+再起動)を実施済み。以降のセッションはdocker APIを直接利用可

- ws-1 / マージ 28fde85(設計 117236e・計画 fb4a189・実装は0552ec9まで)/ docs/plans/M0/ws-1-report.md / 2026-09-27
  - 完了条件5項目を独立検証(migrate=0001(head)・test-ci 62 passed・スキーマ25試験・§1.2対応表トレース済み・禁止領域差分なし)
- ws-2 / マージ 9eba5be(設計 bea2e02・計画 e21138c)/ docs/plans/M0/ws-2-report.md / 2026-09-27
  - 完了条件6項目を独立検証(unit 77・llm 47・3系統送信記録ok/timeout/error・依存追加なし・時刻参照clock.pyのみ)
  - supervisor承認: 送信記録の保存先は構造化ログ(`latch.llm.send`)で開始(docsは媒体未確定・08 §2.4許可リスト準拠。DB化はSendRecord差し替えで再計画可能)
- マージ後main: test-ci 109 passed(unit 79 + integration 30)。settings.py競合は両追記保持で解消

- ws-3 / マージ 215105c(設計 317b4a3・計画 c44aa67・検証時fix 59cd61b)/ docs/plans/M0/ws-3-report.md / 2026-09-27
  - スーパーバイザー独立検証(test-ci)で2欠陥を発見しagent3に修正させた: (1) asyncpgが返すUUIDインスタンスへのuuid.UUID()再構築で503(unit試験はスタブlookupのため未検出だった経路)(2) alembic env.pyのfileConfig既定によりlatch.*ロガー無効化で後続unit試験のcaplogが空
  - 修正後test-ci 202 passed(G0-1証拠)。依存: pyjwt[crypto]・redis(dev: fakeredis)
- ws-4 / マージ b5fcc5c(設計 efca595・計画 2c031d4)/ docs/plans/M0/ws-4-report.md / 2026-09-27
  - test-ci(fixture)177 passed。実データ確認=スーパーバイザー実施: ISJ令和7年(2025)鹿児島県分(46000-19.0b.zip)・OSM九州extract 315MBを取得し geo-import(46+563行)・geo-verify(正転・逆転2点)でG0-4証拠採取
  - supervisor承認: ci暫定エリア=鹿児島市天文館周辺約3km四方でT2確定前にG0判定(差し替えは設定変更+再取り込みのみ)
- マージ後main最終状態: lint クリーン・unit 209 passed・alembic 0002(head)・test-ci 270 passed。settings/README/pyprojectは両側追記保持で解消、uv.lockはuv lock再生成
- 運用メモ: geo integration試験はgeofeaturesをfixtureでフルリロードするため、test-ci実行後に実データが失われる。実運用データは make geo-import 再実行で復旧(証拠採取はtest-ciの後に行うこと)

### M1(2026-09-27〜)

- ws-1 / マージ e5f830d(設計 2fe23fb・計画 de30cc8・実装は0895411まで・9コミット)/ docs/plans/M1/ws-1-report.md / 2026-09-27
  - スーパーバイザー独立検証(実HTTP test-ci)で1欠陥を発見しagent3に修正させた: IntegrityError制約名分類がSQLAlchemy asyncpg dialectのドライバ例外ラップを認識できず(.orig直でなくdriver_exception先に生asyncpg例外)、重複登録が409 USER_EXISTSでなく503になる(test_4で発見・unitの合成エラーでは未検出。M0 ws-3と同系統)。修正0895411
  - 修正後: worktree基準308 passed・test_users_api 6件全グリーン(初回登録フロー・UNDER_AGE・VALIDATION_ERROR・409・404・401)
- タイムボム修正 / マージ 0ae0ec6(実装 30afa9a)/ 2026-09-27
  - M0由来の既存欠陥: tests/unit/auth/test_tokens.py::test_token_payload_structure がFakeClock固定時刻(12:00 UTC)発行のJWT(exp=13:00 UTC)をシステム実刻で期限検証する構造で、2026-09-27 13:00 UTC以降必ず赤化。ws-1実装中に発見。decode時にverify_exp無効化で解消(verify_iat・verify_audと同型の最小修正・テスト意図はclaim構成検査のまま)
- ws-2 / マージ 326ae51(設計 f48f577・計画 12bb7cc・9コミット)/ docs/plans/M1/ws-2-report.md / 2026-09-27
  - worktree基準334 passed(当時main未反映のタイムボム1件のみdeselect)。実装は10分で完了(計画書がコードレベルまで詳細化されていたため)
  - スーパーバイザー検証: test_intents_parse_api 5件グリーン(ハッピーパス・300字境界・形式不正・401・未登録subjectでの200)
  - マージ時main.py競合は両側追記保持で解消(auth・users・intentsの3サービス独立スキップ判定へ統合)。**マージ後のみ顕在化したpytest basename衝突(users/intents両方のtest_service.py・__init__.pyなしのprepend mode)をusers側test_users_service.pyへ改名してマージコミットへ繰り込み**(個別worktreeでは検出不能な類)
- マージ後main最終状態(2026-09-27): lintクリーン・unit 302 passed・**test-ci 374 passed(除外なし・users 6件+parse 5件integration込み)**・alembic 0002(マイグレーション追加なし)・geo実データ復旧済み(46+563行)
- 学習資産追従: 12200b5(第4章「1本の書き込みの経路」・第5章「LLMという外部の協力者」新設・既存6ファイル更新)
- ws-3 / 設計 cdf313a・計画(コード全文記載・事前検証416 passed)/ 2026-09-28実装着手
- supervisor承認・ユーザー決定(2026-09-28):
  - **T3ゴールドセットは「エージェント草案→ユーザー確認で確定」方式**(G1精度ゲート入力=Parser入力セット30件+・飲酒判定セット30件+)
  - **location.name格納=structured_data(JSONB)へlocation_nameキー追加**を承認。05 §2「structured_data保持キー」への同キー追記は次回のdocs改版に含める(ユーザー承認済み・ws-3設計§6-1)
  - §6-2(G1 02#4の期限経過確認はexpiry_sweeperがM3-3のため、G1では保存時検証+Clock操作による期限切れ値保存での代替とするか)は**G1判定時にあらためてユーザー承認**とする

- ws-3 / マージ f222416(設計 cdf313a・計画 0b44582・実装は934849fまで・13+3コミット)/ docs/plans/M1/ws-3-report.md / 2026-09-28
  - スーパーバイザー独立検験(実HTTP test-ci)で2欠陥を発見しagent3に修正させた:
    (1) store.pyの `:geo_lon::float8` をSQLAlchemy text()のbind param正規化が認識せずリテラル落ち→INSERT構文エラーで全保存系が503(unit 416緑でも検出不能。CAST(:x AS float8)へ修正+実dialect compileによる回帰試験 test_store_sql.py・eeb5263。あわせて503ラップ時のログに例外クラス名のみ残す運用改善)
    (2) test_7_deleteがmatch_events検証をイベント種で絞らず.one()(active intentはcreated+deletedの2行が正当)→テスト側を修正(6835d9b)
  - 修正後: worktree基準502 passed(test_intents_crud_api 10件込み)。マージ後main基準でも502 passed・geo実データ復旧済み
- T3部分資産(草案)/ 5a28a0e / docs/testassets/(README・g1-parser-struct.yaml 35件・g1-alcohol.yaml 36件=true24/false12)/ 2026-09-28
  - エージェント草案→ユーザー確認方式(同日承認)。要確認フラグ7件(セット1=5件・セット2=2件)。**確定までG1入力として未使用**

- ws-4 / マージ b4c4740(設計 1199d86・計画 6d7c8a7・実装はb152fe8まで)/ docs/plans/M1/ws-4-report.md / 2026-09-28
  - スーパーバイザー独立検証: **一発グリーン**(worktree・マージ後mainとも554 passed・api 60req/分が既存試験に干渉せず)
  - 経過メモ: 実装中にエージェント側API接続断(EAI_AGAIN)でターン中断 → スーパーバイザーが再開指示して完了( Task 4の途中から継続・成果物に影響なし)
  - supervisor承認(design §5の5件): resumeも更新6回/時に計上・期限切れactiveはActive数計上から除外・auth系429はprovider+subject単位・INCR先行(429・422失敗も消費)・固定バケット窓の境界2倍は許容
- ws-5 / 設計 79718c0(supervisor承認: E2E不導入・expiry-options API追加〔05 §5追記は後日ユーザー確認〕・トークン保存場所固定・debounce 1秒・frontend/新設+prototype温存)/ 2026-09-28

- ws-5 / マージ b9769ff(計画 c38ff80・実装はad684cdまで・13コミット)/ docs/plans/M1/ws-5-report.md / 2026-09-28
  - 実装中に1回BLOCKED: expiry-options追加がws-4の契約カウンタ試験(test_rate_limit_wiring.py)の期待値と衝突 → スーパーバイザーが期待値1行追加を許可(承認済み設計変更の機械的追随)して完了
  - スーパーバイザー独立検証: backend 561 passed(api再ビルド後・マージ後mainでも同値)・frontend vitest 81 passed・npm build成功・preview実機結合確認(フロント配信200・API無認証401)
- 学習資産追従: ws-4分 89922d6(第7章「レート制限」+Lab 3新設)。ws-5分 d301868(第8章「フロントエンドとbackendの合流」+Lab 4新設)。**M1の5単位分すべて同期完了**

## M1 実装完了時点のサマリ(2026-09-28 13:05)

- マージ済み単位: ws-1〜ws-5(マージコミット e5f830d・326ae51・f222416・b4c4740・b9769ff)
- 検証最終値: マージ後main test-ci **561 passed(除外なし)**・frontend vitest 81 passed・lintクリーン・alembic 0002(マイグレーション追加なし)・geo実データ復旧済み
- 検証で発見・修正した欠陥: 計5件(ws-1 IntegrityError分類503化・タイムボム・ws-3 bind paramリテラル落ち503化・test_7_delete期待値・ws-5契約カウンタ期待値〔裁定〕)
- G1判定は「G1判定の待ち事項」の3項目(T1・T3・02#4扱い)が揃い次第実行
- T3確定(2026-09-28・ユーザー委任): 要確認7件とも草案どおり(parser側: 「夜」→20:00・「2人くらい」→2対2・「30分くらい」→end=null・「午後」→13:00。alcohol側: ノンアルコールビール=false・barでコーラ=true)。docs/testassets/のstatusをconfirmedへ更新
- 02#4裁定(2026-09-28・ユーザー): **(a)代替検証**でG1を判定。期限経過後のexpired遷移の確認はM3-3(expiry_sweeper)実装時にG3で実施

- **Jev系統の確定とdocs v0.5全面改版** / 改版 132ee7b(改版メモ docs/reviews/jev-systemone-revision.md・T1選定資料v0.2)/ 2026-09-28
  - 経緯: ユーザー指摘により「Jev」がTypeSafe AIの実在製品(System Oneモデル・2026-09-15発表)と判明。07旧仕様の「LLMプロンプトで7軸JSON生成」設計は実物のインターフェース(state+型つき質問→較正済み確率)と用途が一致していたため、**ユーザー裁定(C案): 第一候補=TypeSafe Jev(jev-1.13.0)・フォールバック=Anthropic Sonnet 5**
  - 改版範囲: 07(主体)・01・04・05・06・08・09・10・12・T1資料。主な内容: reason廃止・出力検証再試行の廃止(スキーマ保証)・429/529/timeoutでフォールバック切替・SDK backoff無効化・D-15/D-16の読み替え(フォールバック呼び出しも1実行回数・80% alertに内訳)・契約面の受容記録(米国所在・SLAなしはフォールバック構成で担保。ZDR・漏洩通知・Telemetry条項はM2で詳細確認)・**日本語評価をG2必須化**(公式のCJK留保に対処)
  - 事実確認: context7(/websites/typesafe_ai)とexaでdocs.typesafe.ai・typesafe.ai等の一次資料を直接確認(ユーザー指示)。未確認事項は改版メモ§3に正直に申告(MCA/DPA全条精読・SOC2レポート本体等はM2で契約前確認)
  - **T1資料v0.1のコスト試算に1/1000の計算誤りを発見・訂正**(Jev $1.80→$1,800等。TypeSafe Jev採用で通常時約$140/月・フォールバック全件長期化で約$1,915/月)。スーパーバイザーのレビュー不足も起因(数值の検算を怠った)。以後、報告前に数値は検算する
  - 実装への影響: なし(M0・M1にJev呼び出しは未実装。GatewayのSystem One IF追加はM2で実施)

- ws-6 / 2026-09-28着手(T1 Parser契約確定: Anthropic Haiku 4.5。LATCH_ANTHROPIC_API_KEY設定済み・gemini/typesafe鍵は未設定)
- ws-6 / 設計 7522662(supervisor承認・design §6の7件: temperature=0・structured outputs+スキーマ供給源=ParserOutput.model_json_schema()の段階的判断・realはParser系統のみでEmbedding/Jevはstub継続〔M2で系統別へ拡張〕・.env読み込みはmake g1-gateのuv run --env-file経路のみ・alcohol参考値は集計のみで判定外・error_casesは422相当の発生まで・レポートへ生応答記録)。supervisor検算でコスト試算の100倍誤りを設計書§2.11に訂正(71回全体で$0.3未満・1回あたり約$0.004)/ 2026-09-28
- ws-6 / 計画 61ddd77(コード全文記載・Task 9・47ステップ。§0に鍵規律・worktreeコミット規律・basename一意性を規定)/ 2026-09-28実装着手
- ws-6 / マージ 8fbf91b(実装はe195324まで・14コミット)/ docs/plans/M1/ws-6-report.md(+スモーク証拠 docs/testassets/results/g1-result-20260928-202115.yaml)/ 2026-09-28
  - supervisor独立検証: lint緑・unit 533 passed(再実行で一致)・差分スコープ準拠(依存追加はanthropic・pyyamlのみ)・実APIスモーク(--limit 5)exit 0・**マージ後main test-ci 627 passed**(api再ビルド後)・geo実データ復旧済み(46+563行)
  - 経過: 実APIスモークが401で一度BLOCKED → 原因はAPI鍵ではなく**環境変数ANTHROPIC_BASE_URL(z.aiプロキシ)をSDKが自動採用**したため(supervisorが特定・公式API直接curl=200で鍵の有効性を証明)。Settingsへllm_anthropic_base_url(既定=公式API)追加+AsyncAnthropicへの明示渡しで解消(b769308・supervisor裁定=design §3.2承認済み拡張)。詳細は報告書§5.1
- 学習資産追従: ws-6分 cbb5e9c(how-to「G1精度ゲートを実行するには」新設・第5章拡張・00-environment.mdコマンド表へmake g1-gate追加・lab追従。教材検証の部分実行レポート203258も保管)
- G1プロンプト修正(ws-6-g1fix) / マージ 064053b(規則7改訂 ee54bb4・実測証拠 e491661・報告書 a164e4f)/ docs/plans/M1/ws-6-g1fix-report.md + docs/testassets/results/g1-result-20260928-210042.yaml / 2026-09-28
  - 学習資産追従(g1fix分): 5002500(第5章へピン試験の仕組みと規則7改訂の実例・how-toへ「不合格原因の切り分け(揺らぎと仕様の隙)」節を新設)。当初この追従を飛ばしていたのをユーザー指摘で補正
  - 規則7へ「場所の語の優先」と「ノンアルコール明示」を追記(07 v0.6・ユーザー文面承認)。両ゲート再実行=**Parser合格・alcohol合格(recall/precision 100%・A-034はTP是正)・overall_passed=true**
  - supervisor独立検証: 証拠数値・プロンプトSHA変更(改訂版で実測された証左)・unit 533 passed再実行一致・**マージ後main test-ci 627 passed**(api再ビルド後)・geo復旧済み

### M2(2026-09-28〜)

- M2開始 / 2026-09-28ユーザーGoサイン+単位表承認(ws-1〜ws-8)。プロバイダ3系統契約済み申告とAPI鍵実値確認を記録
- ws-1 / マージ 0bb6a2d(設計 7743e05・計画 a34fa4d・実装は5a59208まで・16コミット)/ docs/plans/M2/ws-1-report.md / 2026-09-29
  - スーパーバイザー独立検証(実機test-ci 5巡)で欠陥5件を検出(4件はagent3へ修正指示・1件はsupervisor直接修正):
    (1) PubsubEventBusのgapic呼び出し形式(create_topic等への素の文字列渡しでTypeError・**worker起動クラッシュループ**。unitのスタブでは検出不能。回帰ピン3件追加)
    (2) integration試験のAPI応答ラップ参照ミス3箇所({"intent":{}}を外さずKeyError)
    (3) stage1のUUID復元3箇所がasyncpg UUIDインスタンスでAttributeError(**M0 ws-3・M1 ws-1と同種の3度目**。_coerce_uuid導入+回帰ピン3件)
    (4) test_6のteardown順序(close→delete)と処理ログ待ち不足
    (5) user_env teardownのmatch_candidates削除漏れFK違反(supervisor直接修正 5a59208)
  - 運用メモ: ci-dbに残存した古いpending行126行(9/27由来・event_type='create'等6値外)をフォールバックリレーが再publishし続け障害に見えたため掃除。以後の同種残行は6値外→quarantinedで自然終端する設計
  - 修正後: worktree基準 test-ci 677 passed・**マージ後main test-ci 677 passed**(api再ビルド後)・worker常設復帰確認・pubsubエミュレータ導入(旧イメージパスの匿名pull拒否により公式鏡像 gcr.io/google.com/cloudsdktool/google-cloud-cli:emulators へ)
- 学習資産追従: ws-1分 5906168(第9章「知らせを運ぶ仕組み: outboxからWorkerまでのイベント駆動」新設+既存8ファイル更新。実機観察Labはdocker制約で見送り・ws-2マージ時に再検討)
- ws-2 / マージ 059aece(設計・計画は 42d3236・3a3950eに込み・実装は06b47d1まで・12コミット)/ docs/plans/M2/ws-2-report.md / 2026-09-29
  - 実装中BLOCKED 1件(計画書内部矛盾): settings.pyへllm_gemini_api_key追加がM1 ws-6のllm_*6項目機械ピン試験(test_llm_factory.py)と衝突 → supervisor裁定で期待値7項目への機械的追従を許可(M1 ws-5前例)
  - スーパーバイザー独立検証: worktree test-ci **727 passed一発グリーン**・**実APIスモークOK(gemini-embedding-001・768次元・478ms)**
- ws-3 / マージ 0c45aa7(実装は23c35b5まで・8コミット)/ docs/plans/M2/ws-3-report.md / 2026-09-29
  - 実装中BLOCKED 1件(計画書内部矛盾): 計画書指定のtest_runner.pyがg1gate既存試験とbasename衝突 → supervisor裁定でtest_matching_runner.pyへのリネームを許可
  - スーパーバイザー独立検験(両単位マージ後main)で3系統の問題を検出・対応:
    (1) ci-db残存データ干渉(ws-1系試験・学習資産検証由来のactive+embedding済みIntent 55ユーザー分がLayer 2の検索に引っかかり「結果が空」型の期待4件を破壊)→掃除で解消。**integration試験のteardown完全性とDB残存への感度が課題として浮上**(次単位で対抗策検討)
    (2) 19歳fixtureがサーバ側alcohol確定(M1・07 §2)で422になる試験設計ミス2件 → supervisor直接修正 d3b0e07(meal作成→DB強制の手法へ)
  - 修正後: **マージ後main test-ci 769 passed**・worker復帰確認
- 学習資産追従: ws-2・ws-3分 5fc092b(第10章「Intentを意味の数値へ変える: Embedding」・第11章「マッチング前半: SQLで確実に落とし、意味で上位を取る」新設+既存11ファイル更新。実APIスモークの出力を実測掲載。pgvector直接観察Labは次回候補)
- ws-4 / マージ 90a83d4+296e9fe(設計 5c26e64・計画 45e2c85・実装は813f479まで・12コミット)/ docs/plans/M2/ws-4-report.md / 2026-09-29
  - supervisor承認(design §5の3件): alert媒体=構造化ログで開始(M0 ws-2同型)・リセットジョブ本体はM3-4(カウンタのJSTリセット正確性は日付キー切替で常時担保・roadmap C12と一致)・既存試験期待値の機械的追随
  - スーパーバイザー独立検証(test-ci初回)で新規integration 4件失敗を検出 → **試験設計の潜伏欠陥5系統**を特定しagent3へ修正委任: (1)テスト専用カテゴリws4cheapはサーバLiteral[meal,drinking,activity]で422 (2)Layer 1時間交差は狭義比較でΔ180分+end無しは境界落ち (3)ペア予算LEAST≥500でbudget=0候補は落ち (4)期待値計算誤り2件(0.7→0.85・0.8→0.85) (5)同点順序検証の前提が誤り。**修正方針もsupervisor裁定: 残存データ隔離は専用カテゴリ→時間窓分離(全Fixtureをnow+5日へ統一)**。agent3はintegration実行禁止のため検出不能だった系統(ws-3の19歳fixtureと同型・実行されたことのない試験コードの宿命)
  - 修正後: worktreeで5件PASS → マージ後main **test-ci 818 passed**(769+unit44+integration5)・残存確認(Redis ws4-*・users m2ws4-%・intents ws4cheapとも0件=teardown対抗策の実効性を実証)
  - 運用メモ: mainのtest_matching_hardfilter.pyがws-3マージ由来のruff format落ち(意味変化なし。agent3が独立choreコミット0479b77で解消。**test-ciにlintが含まれないためws-3検証時は未検出** — マージ後のlint再実行を検証手順に足す価値あり)
- 学習資産追従: ws-4分 c488846(第12章「費用ゼロの審査と、お金を守る番人: Layer 3 Cheap Judgeとコスト保護」新設+既存5ファイル更新。guard系の动手Labはws-5でdenyが観察できるようになってから、として見送り)
- ws-5 / マージ d18efb7+修正207c59e(設計 41b7ab8・計画 71135e9・実装は30コミット+報告)/ docs/plans/M2/ws-5-report.md / 2026-09-29
  - **context7運用の初適用**(2026-09-29ユーザー指示): agent1がSDK事実を出典つきで記録(§1.3・RetryPolicy(max_retries=0)でretry無効化を解消)・スーパーバイザーも独立突合で一致確認。実装時確認の結果: **typesafe-sdk==0.7.2・retry引数名=retry**。実APIスモークで**model=jev-1.13.0が実証**(noul応答はSDK型どおりfloat確率 — design §5-7解消)。フォールバック直接呼び出しもOK
  - supervisor承認(design §5): skip_reason列+マイグレーション0003・既存試験機械的追随。解釈記録2件(score正規化分母=4・Guard課税は起点側のみ)は上記G2時確認事項へ注記
  - スーパーバイザー独立検証(test-ci初回)で4件失敗を検出 → supervisor直接修正207c59e: (1)test_geoのheadピン0002→0003もれ(機械的追随) (2)ペア行のintent_a_id<intent_b_id正規化に対する方向バイアスクエリ(test_2・4) (3)asyncpgへstrでなくdatetimeを渡す(test_4) (4)active保存時のtime_end補完(start+3h)により窓全体を動かす必要(test_4) (5)同一関数内パート間の時間窓分離もれ(test_7・fallback計上3倍) (6)K_j=8/イベントの再実行期待は「残りpendingは次処理で消化」が仕様(test_2)
  - 経過: 実装中にエージェント側API接続断(EAI_AGAIN・テザリング切替)で1度中断 → 再開指示で完走(M1 ws-4と同様・成果物への影響なし)
  - 修正後: **マージ後main test-ci 926 passed**・lint緑・alembic 0003・jev-smoke両経路OK・残存ゼロ(Redis ws5-*・users m2ws5-%)
- 学習資産追従: ws-5分 43825c6(第13章「お金を払う判定と、信用しない作法: Layer 4 Jevとフォールバック切替」新設+既存6ファイル更新。演習4本は実行済み出力つき・実API課金のjev-smoke手順は教材化せず案内のみ)
- ws-6 / マージ 8804a13+修正19150b4(設計 c1ef8b7〔agent1が直接コミット — 以後agent1定型へコミット禁止を追記済み〕・計画 57e2e3e・実装は9コミット+最終レビュー対応)/ docs/plans/M2/ws-6-report.md / 2026-09-29
  - supervisor承認(design §5): マイグレーション0004(latches部分UNIQUE索引)・notifications先行書き込み(payload={latch_id}最小参照・媒体はM3-5)・スコープ分担はSTATUS単位表が正(12 M3-5側記載との不整合は次回12改版で解消)。解釈記録5件はG2時確認事項へ追記済み
  - agent3が最終レビュー(新鮮な文脈)でCritical 2件を自力発見・TDD修正(日次カウント1要素ValueError・test_8対象時刻)。**引継ぎ(I-1・ws-7設計確認候補)**: peer読取失敗行がlatch_score計算済みのまま再選択されない仕様の空白(design §2.9に規定なし。頻度低・通常経路はM1の削除Event closeとM3-8全削除が回収)
  - スーパーバイザー独立検証(test-ci初回)で11失敗+10エラーを検出 → supervisor直接修正19150b4: **実装欠陥1件(latch_engineのcategory_secondary読取が保存形式の平キーと不一致 — unitのスタブ経由では検出不能だった本番コード欠陥)**+試験設計6系統(ユーザー登録もれ・teardown括弧・headピン0004・visibility既定hidden・PATCH必須項目・+7日上限/行選択tie)
  - 修正後: **マージ後main test-ci 1030 passed**(926+unit94+integration10)・lint緑・alembic 0004・残存ゼロ(users・latches・notifications・Redis)
- 学習資産追従: ws-6分 ccd2029(第14章「評価を提案に変える関所: Layer 5 LATCH Engineと提案を守る枠」新設+既存6ファイル更新。usage limitで1度中断→再開指示で完了)
- ws-7 / 設計 69b6ed7(supervisor承認: design §5の4件=①Pool人数緩和解釈〔06 §2の人数行が「人数(1対1)」と明記され06 §7 Poolの「Layer 3通過」との整合読み。1対1検索は文字列不変〕②種=起点・起点max>=3トリガー ③マイグレーション0005(group_candidates部分UNIQUE・05 §2追記は次回docs改版) ④ws-6引継ぎI-1の1対1側改修を本単位で実施〔tx統合・観測不変〕。解釈記録9件はG2時確認事項③へ追記)/ 2026-09-29
- ws-7 / 計画 bfc3580(2,690行・Task 1〜10・SQL/コード全文記載。計画書レビュー機械チェック合格: basename一意〔新規3ファイル既存96と衝突なし〕・ピン試験追随访問済み〔LAYER1_WHEREバイト同一・test_layer_sql/layer4/worker_jev/latch_engineの追従を§4に明記〕・完了条件7項目コマンド付き。agent2が計画中に発見のorigin.load_origin人数ガード問題はGroupEngine専用起点読取load_group_origin〔max>=3ガード・origin.py無変更〕として§9-4で確定 — 承認事項①②から必然の実装詳細とsupervisor突合で確認)/ 2026-09-29実装着手
- ws-7 / マージ 1563752(設計 69b6ed7・計画 bfc3580・実装は8bfd329まで・14コミット)/ docs/plans/M2/ws-7-report.md / 2026-09-29
  - agent3実装(1h09m・11コミット)の最終外部レビューでImportant3件を申告受け**supervisor裁定で全件修正実施**(d87dec7): ①JevWorker起点読取の人数ガードfallback(純min>=3集合が永久に評価されない欠陥) ②finalizeのD-06順ソート(同一実行内順序依存で重複proposed化) ③select_jev_rowsの両端人数条件(1対1最低4枠の浪費)。integration試験11(純min=3集合E2E)を追加
  - スーパーバイザー独立検証(test-ci初回)で20件失敗を検出 → **直接修正 ff771ed**: 実装欠陥1件=uuid[]バインドの文字列リテラルがasyncpgで不通(計画§9-14の規律がws-6実態=要素毎スカラーbindと異なる読み。unitのスタブ/compile検査では検出不能)+試験設計7系統(visibility既定・4人集合強制×2・D-06の2集合決定化・DELETE認証・llm_failure型限定評価・(3,4)で1対1干渉阻止・latches照会sorted化・headピン0005)
  - 修正後: **マージ後main test-ci 1125 passed**(1030+unit84+integration11・api再ビルド後)・lint緑・alembic 0005・残存ゼロ・geo復旧済み(609行)
  - 運用メモ(観察): ci常設workerがテストのAPI発行Intentを非同期処理し、teardown後にgroup_candidates/latchesの孤立行を作ることがある(uuidは毎回新規なので試験結果には無影響・掃除で対処。ws-8のE2E整備で対抗策を検討)
  - 引継ぎ(Minor・ws-8/M3): _SELECT_GROUP_PAIRSのORDER BYなし・世代リセット後のメンバー間ペア再生成は相手起点経路のみ・aggregate計算済み集合の早期continue・ON CONFLICT昇格でlatches.group_candidate_idが旧gidのまま残りうる(M3-1設計確認候補)

- 学習資産追従: ws-7分 55518d6(第15章「3人以上を結ぶ: グループマッチ」新設+既存5ファイル更新。演習は実行出力つき・agent4がmake test 965・test-ci 1125を再実行確認。純min=3起点fallbackの話題はM3回答APIの章で拾う予定)

- ws-8 / 設計 675edcc(supervisor承認: design §5の8件=①p95実装解釈②min_samples=2③breakerプロセス内メモリ④02#5〜#12ci実施⑤D-16上限試験はM4⑥Gateway公開IF追加⑦Minor 4件処置〔(a)のみws-8〕⑧test-ciへpurge-match-sub挿入。独立突合は設計§6に記録。解釈3件はG2時確認事項④へ)/ 2026-09-30
  - 経過: 実装中にAPI接続断で1度中断(agent1・調査完了直後の執筆開始時)→再開指示で完走(影響なし)
- ws-8 / 計画 dd01544(3,940行・Task 1〜13・SQL/コード全文記載。§9にIF確定事項20件+Self-Review記録。機械チェック合格: basename一意〔新規5ファイル既存96と衝突なし〕・ピン試験追随访問〔JevJudgment構成ピンなしでusage追加無傷・test_llm_factoryは§9-20で対応規定・group_engineへORDER BYピン追加〕・DB干渉対抗策〔prefix teardown+&&掃除・now+5日統一・Redis prefix分離〕。design配置102→118 Intent・冪等試験分離の修正は§9-15/16に理由つき記録)/ 2026-09-30実装着手
- ws-8 / マージ f1eb6eb(計画 dd01544・実装はb43258fまで・22コミット)/ docs/plans/M2/ws-8-report.md(+g2部分実行証拠 docs/testassets/results/g2-jev-result-20260930-122004.yaml)/ 2026-09-30
  - agent3実装(36分・13コミット)+検証7ランの修正サイクル(test-ci初回20失敗+2エラー→1188 passedまで)。**テスト設計・環境の欠陥を大量に検出・修正**(報告書補足2〜4に全記録): ①Makefile purgeのエミュレータenv欠落(fe22042・supervisor直接修正)②teardownのlatch_status_events/notifications FK欠落(2段階で判明・0b622d6+b779821)③**test_schema.pyのM0由来残行汚染**(固定TS孤立行+payload={}のrelay毒eventを毎run累積——61b0de3の掃除teardownで恒久解消・api relay26万行ループの正体)④**HNSW死エントリ汚染**(全テスト同一ベクトルE1の削除行が近似探索予算を食う→テスト毎一意ベクトル5ed3c41)⑤k2/k3手動teardownのassert失敗時スキップ→field fixture化⑥k1⑧掃除のFK順序(e1e92bb)⑦test_3相手Intent欠落・k1⑧のnotifications.latch_id不在列・k3のversion不在列・k2比較のペア集合化(6eb1b78)⑧g2-gateのmake引数転送欠陥(853f92f・**`make --`転送なしの事故で520全件実行が開始され約9分で停止——実行前金額報告の条件を一時破った。沈没コストあり・報告書に正直記録**)
  - スーパーバイザー独立検証: worktree 1016 unit passed・**test-ci run 7=1188 passed一発のち、マージ後main test-ci 1188 passed**(api・worker再ビルド後)・**残存・孤立行すべて0件**(test_schema掃除でM0以来の累積も解消・purge-match-sub実効性実証)・g2-gate --limit 2 --route both=exit 0(4ペア成功・partial=true・≈$0.02)・lint再実行緑・basename一意・alembic head=0005不変
  - 環境の既存欠陥(スコープ外・ユーザー報告済み): compose.yaml workerにLATCH_DATABASE_URL不在でコンテナworker稼働不能(test-ciはin-process workerのため影響なし・対処方針はユーザー判断待ち)

- **G2改版(2026-09-30ユーザー裁定)** / 改版 c132cd4・コード追随マージ 8da6423 / docs/reviews/g2-threshold-fallback-revision.md / 2026-09-30
  - 背景のJev評価実測: 520ペア・失敗0・296秒・**実コスト$0.03**。閾値0.80では提案1件(recall 0.5%)・MutualScore最大0.81。AUC 0.7475・ECE 0.0905・Brier 0.2096(証拠 g2-jev-result-20260930-134810.yaml)
  - ユーザー裁定: ①提案閾値0.80→**0.60**(Jev第一候補のまま)②フォールバックSonnet 5→**Haiku 4.5**(コスト方針「マネタイズできない」・全適用範囲)
  - 改版範囲: 01/04/05/06/07/09/11/12・T1 v0.3(Haikuフォールバック全件継続$900/月・上限$2,700と検算)
  - コード追随ミニ単位: LATCH_THRESHOLD=0.60・**match_levelのmedium境界0.80を提案閾値から分離**(0.80一致の偶然に依存していた)・フォールバックモデルclaude-haiku-4-5・g2gate測定閾値0.50/0.60/0.70・ピン追随。マージ後main test-ci 1188 passed
  - 合格基準書 v0.2(eefdf34): 必須=①Precision@0.60≥0.60②AUC≥0.70③**提案数n≥30**(v0.1の退化合格抜け穴へ新設)。オーナー確定欄つき
- 学習資産追従: ws-8分 6896d52(第16章「呼ぶのをやめる判断: circuit breakerと続く障害への備え」新設+既存4ファイル更新。演習は実行出力つき・agent4がmake test 1016を再実行確認。g2-gate手順書は実API検証不可のためG2実行時に作成見送り・usage limitで1度中断→再開指示で完了)

### M3(2026-09-30〜)

- M3開始 / 2026-09-30ユーザーGoサイン+単位表承認(ws-1〜ws-9・実行waveは単位表参照)。FCM資材(人間領域)と02#13〜#23実施環境は「G3判定の待ち事項」へ分離
- ws-1 / 設計 54f22b7(supervisor承認: design §5の8件=①Calibration作成はrejected/matched時のみ〔07 §6「回答確定時」の直接読み〕 ②回答UPDATEのWHEREへ参加Intent検査NOT EXISTS追加〔06 §6確定値への厳格化・05 §5のLATCH_CLOSED「参加Intent変化」の実装〕 ③成立はpaused参加Intentを含む ④segment=layer3文字bigram再利用・soft_constraintsは降格文言を含む ⑤1対1評価行特定=score一致3段階・全段失敗はレコード不作成+構造化ログ ⑥グループprediction=minペア準用 ⑦stage1削除Event処理へlatchesクローズ+解散復帰追加〔06 §1「保留無効化」の未実装追随〕 ⑧group_engine昇格UPDATEへgroup_candidate_id列追加〔ws-7引継ぎ確定〕。引用確定値28件とコード接続資産はスーパーバイザーがdocs・実コードと突合済み。②のうちpaused成立とsegment計算時点は「G3時確認事項」へ記録)/ 2026-09-30
- ws-1 / 計画 c7c3d18(4,528行・Task 1〜12・SQL/コード全文記載。機械チェック合格: basename一意〔既存106+新規5・スーパーバイザー再実行で空を確認〕・ピン試験追随访問〔group_engine列追加は既存期待値なし・layer3は_bigrams削除せず・stage1はFakeResult機械的追従〕・DB干渉対抗策〔m3ws1-プレフィックス+FK順teardown・now+5日系BASE_HOURS=120分離〕。Review Focus 5点にデッドロック回避のORDER BY id〔§9-1〕と期限切れ試験のDB値操作〔§9-2〕を含む。**supervisor修正: Task 8のtest_latches_routes.pyが§0/§4/完了条件5の計数から漏れていた内部矛盾を整合(新規5ファイル・計21ファイル)**)/ 2026-09-30実装着手
- ws-1 / マージ 54e40d5(実装はae5141eまで・14コミット)/ docs/plans/M3/ws-1-report.md / 2026-09-30
  - agent3実装(1h37m・12コミット)。test-ci 3ランの修正サイクル(初回7失敗=api旧イメージ+fixture向き→2回目4失敗→3回目1253 passed)。報告書の逸脱記録は誠実(§9-3のCAST句・試験fixtureのa<b正規化・matched削除経路のDB値操作置換など)
  - supervisor承認(計画外追記1件): test_rate_limit_wiring.pyへ新ルート3行(全v1ルートのapi_rate_limitedピンへの機械的追従・M1 ws-5前例と同型)
  - スーパーバイザー独立検証で2件を検出・解決(いずれもws-1実装コードの欠陥ではない):
    (1) **test_k_limits_e2e test_4の日次時限爆弾(M2 ws-8由来のテスト設計欠陥)**: 期限null補完が「今夜JST23:30/翌日12:00/翌日23:30/now+72h」の最寄りへスナップするため、**21:30〜23:30 JST帯の実行では**now+45分startのfixture期限がnow+2h以内(今夜23:30)になりa/bがcatch-up対象化してrun_once()!=0で失敗。supervisor検証(21:44 JST)のみ赤・agent3実行(≤21:25 JST)は窓外で緑。明示expires_at追加(ae5141e)で解消・**失敗窓内(22:11 JST)の単体実行で合格確認**。原因特定はrunner選択SQLの直接実行+pytest -l のローカル変数(calls=fixture自身のid)で実施
    (2) **agent3デバッグ行残存(m3ws1dbg-*)+M2由来users蓄積420件**: 報告書「残存0件」主張はm3ws1-%照合でも不正確だった(自己申告を信じない原則の実証例)。FK順完全削除で全テーブル0件化
  - 検証最終値: worktree test-ci 1253 passed(修正後独立実行)・**マージ後main test-ci 1253 passed**(api再ビルド後・lint緑)・basename一意・alembic head=0005不変・依存追加なし・geo実データ復旧(make geo-import)
  - 観察(既知・スコープ外): test-ci 1回あたりm2ws1-/m2ws2-系users約12件がteardownから漏れ蓄積する(M2由来・今回400件掃除。ws-9かM4での恒久対策候補)
- 学習資産追従: ws-1分 a96b8b3(第17章「提案への返事がシステムを動かす: LATCH応答系とCalibration」+Lab 5〔curlで回答から成立まで〕新設・既存6ファイル更新〔README順路・00-environment・第9/14/15/16章〕。**第14・15章への閾値0.60追従はG2改版8da6423時の追従漏れの回収** — 計画外マージでもagent4追従を自問する教訓の再実証。グループ3人回答のLab実体験は後続候補として見送り。agent4はusage limitで1度中断→再開指示で完走・make test 1062 passed・lint収束)/ 2026-09-30
- ws-2 / 設計 0491dd9(supervisor承認: design §5の5件=①保留キュー再評価イベントの実体=drain直接実行〔event_type 6値固定・catch-upと同型のEvent不発行判断。G3時確認候補〕 ②attendance通知type='attendance_request'は実装定義 ③クローズ検知drainをsweeper tickへ包含〔06 §10クローズ側トリガーの消費実装〕 ④統合スケジューラの遅延許容〔正しさへの影響なし・案B分割は後から可能〕 ⑤Intent期限切れ時の候補closed化なし〔Layer 1 status='active'条件とlatches側sweeperで自然無力化。G3時確認候補〕。引用確定値23件と既存実装接続8点〔Layer1 status条件・cost/store.pyキー体系・ws-1設計§1.4引継ぎ・latch_engine._drain/try_promote・reeval.py統合前提docstring・Clock.jst_date・completed_at列(0001)〕はスーパーバイザーがdocs・実コードと突合済み。スケジューラ統合=ReevalRunnerへExpirySweeper注入・マイグレーション追加なし)/ 2026-10-01
- ws-2 / 計画 647bb37(2,953行・Task 1〜10・SQL/コード全文記載。機械チェック合格: basename一意〔既存111+新規3ファイル〕・_drain参照は4箇所のみ正確〔module関数_drain_candidatesは影響外と区別〕・ピン試験追随访問〔test_latch_engine 952/969・test_worker_reeval・test_settings・test_llm_factory無傷・test_worker即shutdown〕・DB干渉対抗策〔m3ws2-プレフィックス+FK順teardown+BASE_HOURS=120+Redis m3ws2r-〕。§9にIF確定事項14件+Self-Review記録・Review Focus 5点〔競合影響0・暫定deadline不使用・cancelled除外・last_tick更新順序・Intent/latch同時閉鎖〕・試験数基準値1062+191=1253整合)/ 2026-10-01実装着手

## G2判定資料(2026-09-30・承認済み)

- **①02#5〜#12**: ci環境でグリーン(解釈④=ci実施/staging再実行はM4-3。マージ後main test-ci 1188 passed・改版後も1188 passed)
- **②K上限裏付け**: ws-8 K上限E2E(118 Intent・全層同時・決定性2回実行)グリーン
- **③冪等性**: 同一Event2回投入でペア集合不変のE2Eグリーン
- **④縮退**: circuit breaker開放→半開・切替・skipped保留・復旧再評価のE2Eグリーン(フォールバックはスタブでモデル非依存=Haiku変更の影響なし)
- **⑤日本語評価(Jevのみ実施・ユーザー指示)**: 2回実行いずれも520ペア全成功・失敗0(1回目296秒/2回目323秒・各$0.03)。
  - 1回目(134810): Precision@0.60=0.7949・AUC=0.7475・**n=39(tp31/fp8)**
  - 2回目(144518・測定閾値改版後の正式証拠): **Precision@0.60=0.7381・AUC=0.7509・n=42(tp31/fp11)**
  - 独立検算: mutual=min(would_a,would_b)の不一致0/520・2回目レポートの@0.60指標とper_pairからの再計算が完全一致。tpは2回とも31で安定(変動は閾値近傍のfpのみ: 8↔11)
  - ~~当初記録の「n=31」はtpの読み誤り~~(2026-09-30訂正・n=42)
- **⑤の判定: 合格(スーパーバイザー裁定・2026-09-30ユーザー委任「jevの判定についても君が確認してくれ」)**:
  必須①Precision@0.60=0.79/0.74≥0.60✓ ②AUC=0.747/0.751≥0.70✓ ③n=39/42≥30✓。
  参考もECE 0.091/0.099≤0.10・Brier 0.209/0.210<0.25をクリア。基準値はv0.2(0.60/0.70/30)のまま確定
- **G2時確認事項①〜④**(18件の解釈記録)の承認を含む
- ~~ユーザー確定待ち: G2ゲート承認のみ~~ → **2026-09-30承認済み**(「ゲート承認」参照)

## 運用ルール(並列worktree × ci環境DB共有。ws-1レビューの引継ぎ事項より裁定)

共有ci-db(compose常設・名前付きボリューム)の `alembic_version` はworktree間で取り合う状態になる。
マイグレーションを追加する単位のworktreeが `test-ci`/`migrate` を実行するとDBがそのworktreeのheadに進み、
他worktree(より古いマイグレーション一式)のconftest `upgrade head` は "Can't locate revision" 系で失敗する。

1. マイグレーションを追加する単位(ws-4等)とDBを消費する単位(ws-3等)のtest-ci実行は**同時にしない**。
   実装エージェントには計画書§0と起動定型で「共有ci-dbへの test-ci/migrate はunit試験で開発を進め、
   報告ファイルに『test-ci=スーパーバイザー検証待ち』と記録してよい」ことを条件付ける
2. スーパーバイザーは検証・マージ時にtest-ciを**直列**で実行する(古い単位→マイグレーション追加単位の順)
3. マージ済みmainでのtest-ci実行がスキーマ状態の唯一の真実。worktreeでのDB状態は検証途中の経過とみなす
4. make test-ci(compose up)はapiイメージを再ビルドしない(compose.yamlのapiはbuild型・ソースマウントなし)。コード変更後の検証では docker compose build api が先行必須(M1 ws-1で確定。各計画書の報告形式に検証手順として明記)
5. backend/testsは__init__.pyなし(pytest prepend import mode)のため、テストファイルのbasenameはtests配下全体で一意にすること。並走単位のマージ時は `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空であることを確認(個別worktreeでは検出できない・M1 ws-2マージで実際に発生)

## ゲート承認

- G0: **承認済み**(2026-09-27 ユーザー承認。暫定エリア・送信記録=構造化ログ・test-ci後のgeo再取り込みの各supervisor判断を含む承認)。**M1以降の実装は同時にユーザー指示で一時停止**
- G2: **承認済み**(2026-09-30 ユーザー承認。5条件=①02#5〜#12 ciグリーン〔1188 passed〕②K上限裏付け③冪等性④縮退⑤日本語評価〔Jevのみ実施・委任によりスーパーバイザー判定で合格: Precision@0.60=0.79/0.74・AUC=0.747/0.751・n=39/42・ECE/Brierも参考基準クリア〕。G2時確認事項①〜④の18件の解釈を含む承認。提議閾値0.60改版・フォールバックHaiku 4.5変更・合格基準v0.2確定〔委任〕を含む)
- G1: **承認済み**(2026-09-28 ユーザー承認。4条件=①02#1〜#3 ci緑〔マージ後main test-ci 627 passed〕②Parser構造化ゲート合格〔g1-result-20260928-210042: category 100%/time.start 93.75%/location 100%/participants 84.4%/budget 100%〕③alcoholゲート合格〔recall 100%・precision 100%〕④両ゲート再実行harness稼働〔make g1-gate〕。**02#4は(a)代替検証の注記どおり期限経過後のexpired遷移はM3-3実装時にG3で確認**。規則7改訂07 v0.6〔FN=A-034対応・当日うちに両ゲート再実行で合格〕を含む。実測は実プロバイダHaiku 4.5・プロンプトSHAで改訂版を確認済み)

## 並行トラック(開発外・人間領域)

- T1 LLMプロバイダ契約(D-14の6基準+契約5条件): **完了**(2026-09-28ユーザー申告「すべて契約済み」)。Parser=Anthropic Haiku 4.5・Embedding=gemini-embedding-001・Layer 4=TypeSafe Jev+フォールバックSonnet 5。API鍵3本とも実値確認済み(スーパーバイザー確認)。TypeSafe契約詳細の文書化は「G2判定の待ち事項」2参照
- T2 初期エリアの最終指定(11 第2節の4基準でスコアリング): 未着手
- T3 ゴールドセット整備: Parser入力セット35件+飲酒判定セット36件は確定済み(G1で使用)。**G2日本語評価用の評価ペア500件+とM4共有資産(シード200〜300件・期待値表)の整備が残る**(前者はG2前・進め方協議待ち)
