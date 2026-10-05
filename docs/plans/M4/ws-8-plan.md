# M4 ws-8(ロールバック手順+G4証拠整備)実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** docs/plans/M4/ws-8-design.md §1.1どおり、コード変更ゼロの文書整備単位として、(1)ロールバック手順書(11 §7の運用手順化・DB拡張のみ原則)・(2)G4 7項目の証拠一覧(docs/testassets/results/への整理)・(3)担当・連絡経路の確定欄(人間領域の入力用)の3成果物を作る。

**Architecture:** 11 §7が定めるのは「基準と区分手順」であり・運用の実現手段(現環境ではdocker compose操作)は基準から独立に更新される。このため基準(docs/11)・手順(docs/plans/M4/ws-8-rollback.md・本単位の本体)・実行記録(docs/testassets/results/)の3層に分離する(design §2.1案A)。手順書は11 §7の4区分+データ事故復旧を章立てとし・各章を「対象事象→判断→手順(現環境stagingの具体コマンド)→確認→記録」で書く。本番環境の手順と担当・連絡経路は確定欄(空欄)として残し・Phase 0で埋める(12 §6の引継ぎ物)。G4 7項目×現存証拠の対応表はresults/のREADMEとして新設し・欠けが判明しているws-5分3点の採取手順を明記する(design §2.4案A・§2.5)。新規コード・Makefile・composeは1行も書かない。

**Tech Stack:** Markdown(docker compose・Makefile・curlのコマンド記述のみ・新規スクリプトなし)。追加依存ゼロ。

**Spec:** docs/plans/M4/ws-8-design.md(agent1・コミットd231d60)。本計画はdesign §5のsupervisor承認8件を確定事項として落とし込む(design §6「未解決の論点なし」)。supervisorからの本単位固有の補足: ①実装ステップはTDDではなく「文書作成→実在照合(design §4.3)→make lint・make test現行値維持(1472)」の順 ②staging実施(1世代戻しwalkthrough・ws-5証拠3点採取)はスーパーバイザーの検証フェーズで・計画書には別節(§8)にまとめ・agent3のスコープから除外 ③長文の日本語ドキュメント作成にnatural-japaneseスキルを使用(手順書・対応表の草案執筆時に適用)。

## Global Constraints

- **コード変更ゼロ**: backend/配下・compose*.yaml・Makefile・docker/nginx/staging.conf・pyproject.toml・frontend・prototypeは1行も変更しない。手順書に書く操作はすべて既存のMakefileターゲット・既存composeサービス・直のコマンドで構成する。欠けは手順書側(文書)で補い・環境側に手を入れない(design §3.2)。
- **docs/11本体の無変更**: 11は基準文書。改版せず・手順化は別文書(design §2.1案Cの拒否理由)。
- **既存証拠の無変更**: docs/testassets/results/の既存YAMLは追記・改変しない。新設するのは索引のREADMEのみ。walkthrough記録・ws-5分3点のYAMLはスーパーバイザーが§8実施時に生成する(agent3は生成しない)。
- **STATUS.md・G4チェックリストの無変更**: 判定・記録はスーパーバイザー側(design §3.2)。G4時確認事項への追記(本単位の解釈)もスーパーバイザーが検証時に実施する(§8手順5)。
- **確定欄は空欄のまま**: 本番手順(手順書§8)・担当・連絡経路(§7)・D-01/D-02運用値(§5)は人間領域の入力欄であり・agent3が値を書いてはならない(design §5-6・12 §6「開発側で値を前倒し確定しない」)。空欄はすべて `(空欄)` の表記で統一し・件数照合を可能にする(§2.2)。
- **合格宣言を機械側からしない**: 性能5目標・総当たり等の量的合格線の正式判定はG4の人間側(ws-6 design §2.7の再掲・design §5-7)。対応表では機械判定の記録値と証拠パスを引用するだけ。
- **検証チェーンにパイプを書かない**: 実在照合(§5・Task 3)とスーパーバイザー手順(§8)の各コマンドは単一コマンドで結果を出し・`| tail`等で終了コードを隠さない。件数は実査する(2026-10-04に2回発生した失敗隠蔽の教訓)。
- **毎コミットの検査**: `make lint && make test` グリーン・`git status --short` で差分が§4の一覧どおり。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。Convention Commits形式・末尾に `Co-Authored-By: Claude Code <noreply@anthropic.com>`。
- **詰まったら**: 10分以上詰まった場合・推測で文書を変えず・報告ファイルに状況を書いて作業を停止する。
- **長文日本語の執筆規律**: Task 1・Task 2の草案執筆では・結論から書き・専門用語の初出に一文説明を添え・同じ鋳型の反復を避ける(natural-japanese文体憲法の適用。手順書は走査性が命のため・見出しは区分名そのもの=「いつ開くか」を語る形とする)。

## Review Focus

specが暗示するが・単純な文書作成のチェックだけでは拾いきれない入力クラス。最も噛みつきやすい順。各項は所有タスクの照合手順でピン留めする(§3の紐付け表も参照)。

1. **遮断confが実効serverにならない事故**: 00-drop.confが既存default.confより後に評価されると・重大事故時に受付が遮断されない最悪事態になる。conf草案は「includeのアルファベット順で先に読まれる00-接頭辞」で確定するが・字面では検証できない。→ 手順書§3.2に「適用後は必ずPOST 503・GET 200を観察する」確認手順を書き・§8手順3のドライランで実証する。もし想定どおり遮断されなければ実施時確認事項として持ち帰る。
2. **GET一覧の巻き込み**: `location = /v1/intents` をメソッド条件なしで書くと・GET /v1/intents(一覧・routes.py実在)も503になり「残すべき経路が生きている」の規律が崩れる。→ conf草案を `if ($request_method = POST) { return 503; }` 方式で確定し・ドライランの観察項目にGET一覧200を入れる。
3. **「1世代」の定義ズレ**: 直近コミットがdocsのみの系列だと・git checkout HEAD~1の戻しはコード差分ゼロになり・「旧版が新スキーマで動く」実証が空洞化する(計画時点のmain先頭5コミットは全てdocs)。→ 手順書§2に「1世代=直近のデプロイ時点のgit SHA」のデプロイ記録規律を新設し・§8手順2で「walkthroughの戻し先は直近コード変更コミットをgit logで特定する」規律を書く。
4. **証拠YAMLの項目不足**: 10 §5の5項目(試験ID・実施日・環境・入力条件・合否)が欠けたYAMLはG4判定で使い物にならない。→ 対応表§3・§4の採取手順に5項目の雛形を明記し・手順書§9に記録様式を置く。
5. **確定欄への機械側書き込み**: 人間領域の空欄に機械側が値を書き込むと・G4判定時に「誰がいつ決めた値か」が不明になる。→ 空欄を `(空欄)` 表記に統一し・件数照合(rg -c・期待20)を完了条件に組み込む。

---

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m4-ws-8`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。
- **worktreeの基点はmain(head=d231d60)**: ws-7マージ後の・コード変更ゼロ単位の直前状態。`make test` 1472 passed・`make test-ci` 1754件のmainから作る。worktree作成後・まず `make setup`(`cd backend && uv sync`)で `.venv` を構築する。
- **毎コミットの検査**: worktree内で毎コミット `make lint` と `make test` がグリーンであることを確認してからコミットする。
- **本単位はコード試験を追加しない**: 新規pytest試験はゼロ。検証は「文書照合コマンド+現行値維持」(design §4.1・supervisor補足)。**`make test-ci` はagent3は実行しない**(コード変更ゼロのため・報告書に「test-ci: 実行不要(コード変更ゼロ)」を記録する)。基点mainのtest-ci実数1754はスーパーバイザーがマージ後mainで確認する(§8手順0)。
- **staging環境に触れない**: walkthrough・ws-5証拠3点採取・環境の起動停止はすべてスーパーバイザー検証時(§8)。agent3は `make down`・`make staging-*`(起動系)・dockerコマンドを一切実行しない。
- **共有ci-dbへのmigrate実行禁止**: 本単位はマイグレーションを追加しない・DBを消費する試験も書かない。
- **python / pipコマンドを直接使わない**: すべて `uv run` / `uv sync` 経由(make test・make lintはMakefile経由なのでこの制約外)。
- **報告書は実装完了時に作る**: staging実施分は「実施待ち」として骨格を残す(ws-7のd9556c6「報告書骨格・実施待ち」と同型)。実測値の記入はスーパーバイザー検証時。
- **コミット前の全タスク共通手順**(各Taskには明記しない): `make lint && make test` グリーン・`git status --short` で差分が§4の一覧どおり。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| G4第7項「ロールバック手順(第7節)が整備され・担当と連絡経路が確定していること」。末尾「証拠はdocs/testassets/results/の試験記録(10第5節)で確認する」。Phase 0の完了条件であり・M4ではstagingで先行確認 | 11 §5(第7項)・12 M4前置き |
| 4区分の基準: 重大事故(即時停止: 個人情報の漏洩・ブロック/公開設定hidden_until_match/summary_only・表示最小化の破損で新規Intent受付と通知を停止。判断は当直→プロダクトオーナー。Safety系は品質問題より優先)/自動縮退(LLM API障害・04 D-16上限・Jev予算超過は06 D-15のとおり自動で提案見送り。停止ではなく挙動の一部)/品質劣化(D-02警報線下回りで通知閾値引き上げ・D-08上限絞り込み。設計値変更は変更管理)/ロールバック(デプロイ単位を基本。DBスキーマは拡張のみ(後方互換)。旧版が新スキーマで動く運用を04と合同で守る。データ事故は10 §4.4) | 11 §7 |
| 単位表: 「ロールバック手順の整備・担当・連絡経路の確定(11 第5節)」/完了条件(G4)(7)「ロールバック手順・担当・連絡経路確定。証拠はdocs/testassets/results/に保管(10 第5節)」 | 12 M4-7 |
| 手順書はPhase 0で本番環境向けに更新される成長文書(引継ぎ物「試験証拠・ロールバック手順」) | 12 §6 |
| 証拠: 試験ID・実施日・環境・入力条件・合否をdocs/testassets/results/へ保管 | 10 §5 |
| 本番側前提: マネージドコンテナ基盤・API常時2インスタンス以上・ヘルスチェック自動復旧・バックアップ=マネージドPostgreSQLの日次バックアップ+PITR・VectorはIntentに記録したEmbeddingモデル識別子(01 §18)から再エンベディングで再構築。基盤未確定のため手順書では確定欄として残す | 04 §6・§7 |
| 警報線(YES率10%未満・通知オフ率急増)。D-01/D-02の値はPhase 2〜3に確定(開発側で値を前倒し確定しない)・手順書では出典と変更管理の起点のみ書き運用値欄は空欄 | 09 D-02・12 §6 |
| バックアップ復旧のstaging読み替え: staging-dump→staging-db2-up→staging-restore→staging-db2-switch→確認→staging-db2-down(RTO実測9秒/1秒/8秒・ws-7報告書§3(b)) | 10 §4.4・Makefile(ws-7) |
| 自動縮退の回収: 復旧後のバックフィル・skipped再評価はリセットジョブ後の最初のEvent・LLM回復後のEventで自動再評価(FR-18) | 06 D-15・FR-18 |
| 構造化ログの許可リスト: 出力フィールドを列挙型で固定・raw_text・geo座標・constraints系フィールドは不許可 | 08 §2.4 |
| 現行値: `make test` 1472 passed・`make test-ci` 1754件(2026-10-05時点)。本単位は両値を維持するだけ | design §4.1 |
| 重大事故停止の手段: 機能フラグは存在しない(settings.pyに停止系envなし・alert_queue_enabledはlag alertの制御のみ)。「新規Intent受付と通知を停止」は環境操作で実現。解釈はG4時確認事項として記録 | design §1.3・§2.3(承認§5-2) |
| ws-5分3点(同意フロー実行記録・許可リスト登録表+機械検査結果・ログサンプルraw_text非混入照合)の採取手順を本単位で用意・staging実施はスーパーバイザー | design §1.3(⑤)・§2.5(承認§5-3) |
| walkthrough(1世代戻し+停止手順ドライラン)で手順書を証拠化・デプロイ時のgit SHA+ビルド日時記録の規律を手順書で新設 | design §2.6(承認§5-4) |
| nfrtools d16 CLIはテスト専用・運用での手動カウンタ操作は原則しない(0時JSTリセット待ち・手動削除は判定済み事故例でのみ・記録必須) | design §5-5 |

## 2. 設計確定値と計画時の機械検証

### 2.1 計画時に実施した実在照合(design §4.3の突合を計画段階で先行実施)

手順書・対応表の草案に書く参照名は・計画時にmain作業ツリー(d231d60)に対して突合済み。agent3はTask 3で同一の照合を再実行し・結果を報告書へ記録する(2重照合・drift検出)。

| # | 参照名(草案で使う値) | 照合方法 | 結果(計画時) |
|---|---|---|---|
| 1 | Makefileターゲット11種: staging-reset・staging-ps・staging-probe・staging-d16・staging-dump・staging-db2-up・staging-restore・staging-db2-switch・staging-db2-down・test・test-ci・lint | `rg -o '^(ターゲット名):' Makefile` | 全実在 |
| 2 | compose.staging.yamlのサービス8種: api1・api2・nginx・worker1・worker2・db・redis・pubsub | `rg -n '^  (サービス名):' compose.staging.yaml` | 全実在(ポート: nginx=18000・api1=18001・api2=18002・worker1=18101・worker2=18102) |
| 3 | env名: alert_queue_enabled(停止系envの不在確認を含む) | `rg -n 'alert_queue_enabled' backend/src/latch/settings.py` + `rg -n 'MAINTENANCE\|STOP\|FREEZE' backend/src/latch/settings.py` | 実在(lag alert制御のみ・既定False)・停止系envなし=design §1.3と一致 |
| 4 | 許可リスト: `ALLOWED_STRUCTURED_LOGS = {"latch.llm.send": SendRecord, "latch.push.send": PushSendRecord}` | `backend/src/latch/core/logallow.py:21` | 実在(ws-5報告書手順4の文言と一致) |
| 5 | 許可リスト試験: backend/tests/unit/test_log_allowlist.py | `find` | 実在 |
| 6 | マイグレーション0001〜0008 | backend/migrations/versions/(実パスでls) | 8ファイル実在(すべて拡張系=design §1.3記載と一致) |
| 7 | API経路: POST /v1/intents(作成・routes.py:259)・**GET /v1/intents(一覧・routes.py:277実在)**・POST /v1/intents/parse(routes.py:81)・GET /v1/users/me・POST /v1/users(display_name+birth_date必須・routes.py:86)・POST /v1/users/me/consent(users/routes.py:135) | routes.py突合 | 実在。**GET一覧の実在により・遮断はメソッド条件付きが必須**(Review Focus 2) |
| 8 | 遮断confの前提: docker/nginx/staging.confは `listen 80`・server_name無指定・location / でproxy_pass http://latch_api+proxy_next_upstream。compose.staging.yamlで `/etc/nginx/conf.d/default.conf:ro` としてread-onlyマウント | ファイル突合 | 実在。**default.confはコンテナ内で上書き不可のため・遮断は新規ファイル `/etc/nginx/conf.d/00-drop.conf` の追加+reload** で行う(includeはアルファベット順=00-drop.confが先に読まれ・実効serverになる想定。nginx -tは「conflicting server name」warningを出すがerrorではない。実証は§8手順3のドライラン) |
| 9 | staging鍵・JWT: `.staging/keys/idp_test_private.pem`(git管理外)・JWT発行コマンド `cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject <SUBJECT> --private-key ../.staging/keys/idp_test_private.pem`(make staging-tokenの定義と同一) | Makefile:96・.gitignore:47 | 実在 |
| 10 | Intent作成bodyの実例: docs/testassets/m4-seed.yaml(SD-001: raw_text・structured_intent一式・location=天文館・category primary=drinking/meal/activity・visibility=hidden_until_match/summary_only・notification_level=proposals_only/nearby_also/muted) | m4-seed.yaml・intent_input.py:66・:67 | 実在(ws-3のstaging実績300件と同型) |
| 11 | walkthroughの戻し先候補: 直近コード変更コミット | `git log --oneline -5 -- backend/ compose.yaml compose.staging.yaml Makefile` | cf9e612(fix: n2_30の再エンベディング対象をactive Intentへ限定)が先頭。**main先頭5コミット(d231d60〜d8cfb3e)は全てdocsのみ=HEAD~1の戻しは実証にならない**(Review Focus 3) |
| 12 | 現行値 | `make test` の実行で確認する(収集件数の単独照合は・パイプ規律との兼ね合いから行わず・testの全実行の結果を見る) | 1472 passed(design §4.1の基準値と一致・Task 3で再確認) |

### 2.2 design §5承認事項8件の落とし込み

| # | 承認事項 | 本計画での落とし先 |
|---|---|---|
| 1 | 手順書=docs/plans/M4/ws-8-rollback.md・対応表=docs/testassets/results/README.md新設 | §4.1・Task 1/2 |
| 2 | 停止は環境操作の手順化(3段階)・新実装しない・「機能フラグ不在を環境操作で読み替える」解釈はG4時確認事項へ | 手順書§3(3段階表+conf草案)・§8手順5(スーパーバイザーがG4時確認事項13へ記録) |
| 3 | ws-5分3点の採取を本単位スコープに含める・staging実施はスーパーバイザー | 対応表§3(採取手順)・§8手順4(実施) |
| 4 | walkthrough証拠化+デプロイ記録規律の新設 | 手順書§2(規律)・§6.2(手順)・付録B(合否基準)・§8手順2〜3(実施) |
| 5 | nfrtools d16 CLIはテスト専用と明記 | 手順書§4(注記) |
| 6 | 担当・連絡経路・D-01/D-02運用値は人間領域(G4判定時)・機械側完了条件は手順書整備+確定欄用意+walkthrough実証まで | 手順書§5(運用値空欄)・§7(担当表)・§8(本番欄) |
| 7 | 量的合格線の正式判定はG4の人間側・対応表は記録値引用のみ | 対応表の前置き(§0) |
| 8 | ci分の証拠=test-ci常設緑(1754)+G1/G2時証拠YAML+M2 ws-8報告書の参照で充足・staging再実行はしない | 対応表①・⑥の行 |

**確定欄の空欄表記の固定値**: 手順書内の `(空欄)` は**合計20個**(§2デプロイ記録雛形5個・§5品質劣化2個・§7担当表8個〔4行×担当+連絡手段〕・§8本番欄5個)。Task 3で `rg -c '\(空欄\)'` の件数照合をする。対応表READMEは空欄を持たない(期待0件)。

## 3. Review Focus

見出し直下に記載(§「Review Focus」)。5項目それぞれの所有タスクの照合: 1→Task 1付録A+§8手順3(ドライラン実証)・2→Task 1付録A(if方式)+§8手順3(GET一覧観察)・3→Task 1§2(デプロイ記録規律)+§8手順2(git log特定)・4→Task 2§3/§4(雛形明記)・5→Task 3 Step 2(件数照合)。

## 4. スコープ(作成・変更するファイル一覧)

### 4.1 作るもの(新規)

| ファイル | 内容 | Task |
|---|---|---|
| `docs/plans/M4/ws-8-rollback.md` | ロールバック・縮退運用手順書(11 §7の4区分+データ事故・3段階遮断表・遮断conf草案・デプロイ記録規律・担当・連絡経路確定欄・本番向け確定欄・walkthrough合否基準) | 1 |
| `docs/testassets/results/README.md` | G4 7項目×証拠の対応表+欠け証拠(ws-5分3点)の採取手順+walkthrough証拠の位置づけ+ファイル一覧 | 2 |
| `docs/plans/M4/ws-8-report.md` | 報告書(§7の形式・実施後に作成。staging分は実施待ちの骨格) | 3 |

### 4.2 変更するもの(既存・追従)

**なし**。本単位の変更対象は新規ファイル3本のみ。

### 4.3 触らないもの(禁止・スコープ外と判断する基準)

- **backend/配下の全コード・マイグレーション・tests**: 実装変更なしの単位。受付停止フラグ等の新実装はしない(承認§5-2)。Phase 0以降の変更管理として持ち帰る論点は手順書§8の確定欄に記載するだけ。
- **compose*.yaml・Makefile・docker/nginx/staging.conf・docker/prometheus/prometheus.yml・dashboard JSON**: 既存ターゲット(staging-dump/db2系・staging-probe・staging-d16・staging-reset等)を**参照して使うだけで改変しない**。手順に不足コマンドがある場合は手順書側で直のコマンドを書く(composeファイル側へ手を入れない)。nginx遮断はコンテナ内の `/etc/nginx/conf.d/00-drop.conf` 追加+reloadで完結し・ホストのconfファイル・composeのマウント定義は無変更。
- **docs/11本体**: 基準文書。改版せず・手順化は別文書(design §2.1案Cの拒否理由と対)。
- **docs/testassets/results/の既存証拠YAML**: 追記・改変しない(索引のREADMEを新設するのみ)。ws5-・ws8-接頭辞のYAMLはスーパーバイザーが§8実施時に生成する。
- **docs/plans/STATUS.md**: G4チェックリスト・G4時確認事項の記録はスーパーバイザー側。
- **docs/learn/・docs/01〜10・12**: 本単位はコードを増やさないため学習資産の新章は生じない(マージ時にagent4で要否を自問するのみ・design §3.2)。docs本文の改版も不要(仕様変更なし)。
- **docs/plans/M4/ws-8-design.md**: 設計メモは読み専。
- **スコープ外と判断する基準**: ①受付停止フラグ(MAINTENANCE_MODE等)の新実装(承認§5-2・Phase 0以降の変更管理へ) ②本番環境の手順の確定(基盤未確定=手順書§8の確定欄として残す) ③担当・連絡経路の値の入力(人間領域=G4判定時) ④性能5目標・総当たり等の量的合格線の判定(G4の人間側) ⑤walkthrough・ws-5証拠3点のstaging実施(スーパーバイザー検証時=§8)。

## 5. タスク(実装ステップ)

supervisor補足どおり・TDDではなく「文書作成→実在照合→現行値維持」の順で構成する。**検証の主体は「照合コマンドの実行と出力の実査」**(コード単位でいうテストに相当)。各Taskの手順1が文書作成・手順2以降がその文書に対する検証。

### Task 1: ロールバック手順書 docs/plans/M4/ws-8-rollback.md(本単位の本体)

**Files:**
- Create: `docs/plans/M4/ws-8-rollback.md`

**Interfaces:**
- Consumes: 既存Makefileターゲット・composeサービス名・routes.py経路(§2.1の照合済み参照名)
- Produces: 対応表README(Task 2)が参照する手順書の節番号(§2・§3・§6.2・§9・付録B)・walkthrough証拠(§8手順2〜3)の合否基準(付録B)

- [ ] **Step 1: 手順書を書く**

以下の全文をそのまま作成する(計画時の実在照合§2.1に基づく確定値)。ただし・最後に自然な日本語の通読点検(読点・一文の長さ・同じ鋳型の反復)をしてからコミットする。

````markdown
# LATCH ロールバック・縮退運用手順書(staging先行版)

- 版: v0.1(2026-10-05・M4 ws-8)
- 本書の位置づけ: docs/11 §7(ロールバック・縮退基準)を・実際に操作できる手順に落とした運用文書。基準と手順は分離して管理する。基準=docs/11(改版は設計変更時のみ)・手順=本書(環境の変化に合わせて更新)・実行の記録=docs/testassets/results/(証拠)の3層。
- 本書は成長文書である。現版はstaging環境(127.0.0.1:18000・docker composeプロジェクトlatch-staging・ローカルビルド)を対象とした先行確認版であり・Phase 0(内部α)の開始時に本番環境向けに更新する(12 §6の引継ぎ物)。本番向けの未確定項目は§8の確定欄として空欄のまま残してある。空欄は「まだ決まっていない」の意であり・誤って埋めたり・空白を削ったりしないこと。
- 読み方: 事象が起きたら§1で区分を判断し・対応する節の手順を実行する。手順の実行可否はws-7のstaging試験で実証済みの資産(Makefileターゲット・復旧手順)に依拠している。

## 1. 区分の判断基準(11 §7)

事象の起きたら最初に区分を判定する。Safety系の破損は品質問題より優先する(11 §7)。

| 区分 | 対象事象 | 基本対応 | 本書の節 |
|---|---|---|---|
| 重大事故(即時停止) | 個人情報の漏洩・ブロックと公開設定(hidden_until_match / summary_onlyの開示範囲)・表示最小化(プッシュ本文の表示規制を含む)の破損が確認された場合 | 新規Intent受付と通知の停止。判断は当直を経てプロダクトオーナーが行う | §3 |
| 自動縮退 | LLM API障害・D-16の回数上限到達・Jev予算超過 | 自動で提案見送りに落ちる。停止ではなく挙動の一部・手動の停止・ロールバックはしない | §4 |
| 品質劣化 | D-02の警報線を下回った場合 | 通知閾値の引き上げ・D-08上限の絞り込み。設計値の変更は変更管理による | §5 |
| ロールバック | デプロイ後の不具合 | デプロイ単位の1世代戻し。DBスキーマは拡張のみ(後方互換) | §6 |
| データ事故 | 誤削除・破損等のDB障害 | バックアップからの復旧手順(10 §4.4) | §6.3 |

判断フロー: (1) Safety系の破損か → はい=§3。(2) LLM・予算・回数上限に由来する提案ゼロか → はい=§4(手動対応不要・観察のみ)。(3) 質の指標の劣化か → はい=§5。(4) データの損失か → はい=§6.3。(5) それ以外のデプロイ起因の不具合 → §6。

## 2. 事前規律: デプロイ記録(1世代の定義)

ロールバックの「1世代戻し」は・戻す先の版が記録されていなければ実行できない。stagingの実体はローカルビルドで・版の記録がどこにも残らないため・本書で記録の規律を定める。

**規律(新設)**: stagingへデプロイ(compose up・再ビルドを伴う再作成)をしたときは・そのたびに次の記録を採る。

| デプロイ日時(UTC) | git SHA | 対象環境 | 実行者 | 備考 |
|---|---|---|---|---|
| (空欄) | (空欄) | (空欄) | (空欄) | (空欄) |

- 記録の置き場所: 運用記録の整備までは `.staging/deploy-log.md`(git管理外)へ追記する。本番では運用記録の流儀へ移す(§8)。
- 記録の採り方: `git rev-parse HEAD` と `date -Is` の出力をそのまま写す。
- 「1世代」とは・この記録の直前の行(git SHA)を指す。

## 3. 区分1: 重大事故(即時停止)

### 3.1 停止の手段(3段階)

stagingには「受付停止」「通知停止」を切り替える機能フラグが存在しない(停止系のenvはsettings.pyにない)。11 §7の「新規Intent受付と通知を停止する」は・次の環境操作で実現する(本番の手段は§8で確定する)。遮断の幅が違う3段階を表に示す。

| 段 | 操作 | 止まるもの | 生きているもの |
|---|---|---|---|
| 1 | worker1・worker2の停止 | 通知の書き込み・提案生成・パイプライン全体 | 全API(受付・閲覧・回答を含む) |
| 2 | nginxの受付遮断conf適用 | POST /v1/intents(新規作成)・POST /v1/intents/parse(503応答) | 閲覧・回答等の既存APIのすべて(GET一覧を含む) |
| 3 | api1・api2の停止(最終手段) | 全API(受付・parse・閲覧・回答のすべて。nginx経由は502) | DB・Redis・Pub/Subの実体は生きる |

「受付と通知の両方を止める」=段1+段2。「全面停止」=段1+段2+段3。段3はサービス全停止であり・閲覧も死ぬ点を承知した上で使う。

### 3.2 実施手順

**段1(通知・提案生成の停止)**:

```bash
docker compose -f compose.staging.yaml stop worker1 worker2
```

- このコマンドはstagingの2ワーカーを停止する(exitedになる・ボリュームは消えない)。
- 確認: `make staging-ps` でworker1・worker2がexited・db・redis・api1・api2・nginxはrunning。GET /v1/users/me が200を返す(APIは継続)。

**段2(新規Intent受付の遮断・nginx)**:

1. 遮断用confを書く。付録Aの全文を `.staging/drop-intents.conf` に保存する(ホスト側・git管理外)。
2. コンテナ内へ置いてreloadする:

```bash
docker compose -f compose.staging.yaml cp .staging/drop-intents.conf nginx:/etc/nginx/conf.d/00-drop.conf
docker compose -f compose.staging.yaml exec nginx nginx -t
docker compose -f compose.staging.yaml exec nginx nginx -s reload
```

- nginx -t は「conflicting server name」の**warningを出す**(既存default.confと同一listen・server_name無指定のため。errorでなければ適用できる)。このwarningは想定内であり・驚いてabortしないこと。00-接頭辞のconfがincludeの順(アルファベット順)で先に読まれ・実効的なserverになる仕組みである。
- 確認(必ず観察する): POST /v1/intents → **503**。GET /v1/users/me → **200**。GET /v1/intents/{id} → **200**(一覧・個別の参照系は遮断しない)。
- もしPOSTが503にならない(=遮断confが実効serverになっていない)場合は・この手順は不成立である。実施を中止し・発見内容を証拠記録に書いて持ち帰ること(design §6「実装時確認事項」)。

**段3(全API停止・最終手段)**:

```bash
docker compose -f compose.staging.yaml stop api1 api2
```

- 確認: nginx経由の全リクエストが502を返す。

### 3.3 解除(逆順)

```bash
docker compose -f compose.staging.yaml start api1 api2
docker compose -f compose.staging.yaml exec nginx rm /etc/nginx/conf.d/00-drop.conf
docker compose -f compose.staging.yaml exec nginx nginx -s reload
docker compose -f compose.staging.yaml start worker1 worker2
```

- 確認: POST /v1/intents が503でなくなる(201に戻る)・worker1・worker2がrunning・全サービスhealthy。

### 3.4 記録(時刻4点)

遮断と解除には次の4つの時刻を必ず残す。記録様式は§9。

- 発見(何を・どこで見つけたか)
- 判断(区分判定と段の選択・判断者)
- 実施(段1/段2/段3の実行時刻)
- 解除(解除時刻と解除後の確認結果)

## 4. 区分2: 自動縮退(停止ではなく挙動の一部)

LLM API障害・D-16の回数上限(日次30,000回/月次600,000回)到達・Jev予算超過では・06 D-15のとおり自動で提案見送りに落ちる。これは停止ではなく挙動の一部であり・この区分では手動の停止・ロールバックを**しない**。

運用者がすることは観察と原因把握だけである。

1. 検知: D-16の80%(日次24,000回/月次480,000回)でalertが1回出る(源流上位ユーザーのレポート添付)。媒体は構造化ログ(latch.cost.alert)・Queue lagはlatch.pipeline.alert。ダッシュボード(prometheus 19090・grafana 13000)でも確認できる。
2. 原因と復旧見通しの確認: プロバイダ側の障害なら復旧を待つ。D-16は0時(JST)のリセットで復帰する(月次は暦月初JST 0時)。
3. 復旧確認: 復旧後・保留になった提案(skipped)はリセットジョブ後の最初のEvent・LLM回復後のEventで自動再評価される(FR-18)・catch-upスキャン(60秒周期)で期限前のIntentも回収される。回収の様子をダッシュボードのqueue pendingと提案数で観察する。

**注意(テスト資産と運用の分離)**: `make staging-d16`(D-16カウンタ操作CLI)は**テスト専用**である。運用でカウンタを手動操作(set/delete)することは原則禁止であり・0時のリセット待ちが原則。手動削除が許されるのは判定済みデータ事故の復旧時のみで・その場合も理由と時刻を§9の様式で記録する。試験資産を運用手順へ混在させないための注記である(design §5-5)。

## 5. 区分3: 品質劣化

判断基準はD-02の警報線(YES率10%未満・通知オフ率急増)。**運用値はまだ確定していない**。D-01(通知閾値)・D-02(MLR合格ライン)の値はPhase 2〜3で運用データから確定する(12 §6「開発側で値を前倒し確定しない」)ため・本書では出典と変更管理の起点だけを書き・運用値は空欄とする。

| 項目 | 運用値 | 出典 |
|---|---|---|
| 通知閾値(D-01)の運用値 | (空欄) | 09 D-01・11 §6 |
| 警報線(D-02)の運用値(YES率・通知オフ率) | (空欄) | 09 D-02 |

対応(11 §7): 通知閾値の引き上げ・D-08上限(日6件・同時3件)の絞り込みで提案品質を守る。**設計値の変更は変更管理による**。docsの該当文書の改版を先に行い・本書は追従する(00 運用ルール7・10 §6)。この区分でも即時遮断・ロールバックはしない。

## 6. 区分4: ロールバック(デプロイ単位)

### 6.1 前提規律(DB拡張のみ)

- DBスキーマ変更は拡張のみ(後方互換)を原則とする。現行のマイグレーション0001〜0008はすべて拡張系(新規表・列・索引・制約の追加)で・原則と整合している。
- **alembic downgradeは運用では使わない**。スキーマは前進させたまま・旧版のコードで動かす。マイグレーションファイルにdowngrade()関数が存在することと・運用でそれを使わない規律であることは別の話であり・本書では後者を規律とする(design §2.6-4)。
- これが「アプリの旧版が新スキーマで動作できる運用」(11 §7・04と合同)である。

### 6.2 実施手順(stagingの1世代戻し)

前提: §2のデプロイ記録があること。手順は次のとおり。

1. 現行版の動作確認: 全サービスhealthy・GET /v1/users/me 200。
2. 戻す先の特定: §2の記録から「戻したい1世代」のgit SHAを確認する。**注意**: 直近コミットがdocsのみの系列である場合は・checkoutしてもコードが変わらず実証にならない。その場合は「直近のコード変更コミット」を `git log --oneline -- backend/ compose.yaml compose.staging.yaml Makefile` で特定し・それを「実質の1世代」として扱う(デプロイ記録に理由を追記する)。
3. 旧版へのcheckout: 未コミットの変更があれば先に退避してから・`git checkout <SHA>`(detached HEADになる・これは想定どおり)。
4. 再ビルドと再作成:

```bash
docker compose -f compose.staging.yaml build api worker
docker compose -f compose.staging.yaml up -d --force-recreate api1 api2 worker1 worker2
```

5. 確認: 全サービスhealthy・GET /v1/users/me 200。**旧版が新スキーマ(0008)のDBで起動し・動くこと**がこの手順の実証点である。
6. 復帰: `git checkout main` して・手順4と同じ再ビルド・再作成を行い・手順5の確認を繰り返す。
7. 記録: 戻し側・復帰側の両方で・healthyまでの時間と確認結果を§9の様式で残す。

### 6.3 データ事故(10 §4.4)

定期バックアップから新しいDBインスタンスへ復元する手順。stagingではMakefileターゲット群が実証済み(ws-7のn2・RTO実測: dump→復元9秒・切替8秒)。

```bash
make staging-dump        # 定期バックアップ1サイクルの読み替え(pg_dump)
make staging-db2-up      # 復旧試験用db2の起動(16543・新規ボリューム)
make staging-restore     # db2へpg_restore
make staging-db2-switch  # api/workerのDB向き先をdb2へ切替(nginx・db・redis・pubsubは触らない)
# (動作確認後)
make staging-db2-down    # db2を停止・削除(ボリュームlatch-staging-db2は証拠確保のため残す)
```

- 復旧後の必須確認(10 §4.4): 復旧前後のactive Intent数の一致(cross-check)・Intent検索・マッチング・通知の正常動作。
- Vectorの再構築: Intentに記録したEmbeddingモデル識別子(01 §18)から再エンベディングする(backfill。ws-7のn2で実証・対象はactive Intent)。
- 本番環境(マネージドPostgreSQLの日次バックアップ+PITR)の手順は§8の確定欄としてPhase 0で記入する。

## 7. 担当・連絡経路の確定欄

11 §7の「判断は当直を経てプロダクトオーナーが行う」を・役割ごとの表にしたもの。**値の入力は人間領域であり・G4判定時のユーザー作業である**。機械側の完了条件はこの表の用意まで。

| 役割 | 担当(記入欄) | 連絡手段(記入欄) | 判断権限・補足 |
|---|---|---|---|
| 検知・一次対応(当直) | (空欄) | (空欄) | 重大事故の暫定遮断までを実施・オーナーへ即報 |
| 最終判断(プロダクトオーナー) | (空欄) | (空欄) | 停止・ロールバックの承認(11 §7) |
| 技術実行(デプロイ・復旧) | (空欄) | (空欄) | 手順の実行・証拠記録 |
| 外部通知(必要時) | (空欄) | (空欄) | 漏洩等の対外報告要否の相談先 |

記入基準の注記: 担当には氏名を書く。連絡手段には到達できる手段(電話・チャット等)と応答時間の目安(例: 30分以内)を書く。当直の実態(ローテーションの有無)に合わせて・ひとまず1行に1名の形でよい。

## 8. 本番環境向け確定欄(Phase 0で記入)

現版はstaging先行確認版である。Phase 0(内部α)の開始時に・本番環境(マネージドコンテナ基盤・API常時2インスタンス以上・ヘルスチェック自動復旧・04 §6・§7)向けに本書を更新する。更新時に記入すべき欄:

| 項目 | 確定欄 | 出典 |
|---|---|---|
| 本番の新規Intent受付停止手段(機能フラグ・LB遮断・基盤のスケール0のいずれにするか) | (空欄) | 11 §7・design §2.3 |
| 本番の通知停止手段 | (空欄) | 11 §7 |
| 本番のデプロイ単位と1世代戻しの実手段 | (空欄) | 04 §6 |
| マネージドPostgreSQLの日次バックアップ+PITRからの復旧手順(RPO・RTOの目標も) | (空欄) | 04 §7・10 §4.4 |
| Vector再構築(Embeddingモデル識別子からの再エンベディング)の運用手順 | (空欄) | 04 §7・01 §18 |

記入時に合わせてやること: §2のデプロイ記録を本番の運用記録の流儀へ移す・受付停止を機能フラグで行うと決めた場合はその実装を変更管理として起案する。

## 9. 記録のしかた(10 §5)

- 実施・演習の記録は・**試験ID・実施日・環境・入力条件・合否**をdocs/testassets/results/へ保管する(YAML形式)。
- 重大事故の遮断記録には§3.4の時刻4点(発見・判断・実施・解除)+対象段+理由+実行者を含める。
- walkthrough証拠の試験IDは `ws8-rollback-walkthrough` とする(採取手順はdocs/testassets/results/README.mdの§4)。
- 対応するG4項目はdocs/testassets/results/README.mdの対応表で参照される・記録は対応表の行を満たす最小構成でよい。

## 付録A: 受付遮断用nginx conf(一時物)

コンテナ内の `/etc/nginx/conf.d/00-drop.conf` として置く。**ホストのdocker/nginx/staging.confは変更しない**(このファイルはコンテナ内にのみ存在する一時物で・解除時は削除する)。00-で始めるのは・includeがアルファベット順に読まれるため・既存default.confより先に評価させるためである。

```nginx
# 受付遮断用(重大事故時のみ・解除時は削除してreloadする)
# 既存default.confと同一listenのため・nginx -tは「conflicting server name」の
# warningを出す(errorではない・先に読まれた本confが実効serverになる)。
server {
    listen 80;

    # 新規Intent作成(POST)だけ503にする。GET一覧・GET/{id}・PATCH等は
    # 従来どおりupstreamへ流すため・メソッドを条件にする。
    location = /v1/intents {
        if ($request_method = POST) { return 503; }
        proxy_pass http://latch_api;
        proxy_next_upstream error timeout http_502 http_503 http_504;
        proxy_next_upstream_tries 2;
        proxy_connect_timeout 2s;
        proxy_read_timeout 30s;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # parse(POSTのみのエンドポイント)を遮断する。
    location = /v1/intents/parse {
        if ($request_method = POST) { return 503; }
        proxy_pass http://latch_api;
        proxy_next_upstream error timeout http_502 http_503 http_504;
        proxy_next_upstream_tries 2;
        proxy_connect_timeout 2s;
        proxy_read_timeout 30s;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location / {
        proxy_pass http://latch_api;
        proxy_next_upstream error timeout http_502 http_503 http_504;
        proxy_next_upstream_tries 2;
        proxy_connect_timeout 2s;
        proxy_read_timeout 30s;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

- location `= /v1/intents` は完全一致のみに当たるため・GET /v1/intents/{id}・PATCH・DELETE・pause/resumeには当たらない。GET /v1/intents(一覧)はメソッド条件の内側にないため・503にならず200を返す。
- GET /v1/intents/expiry-options も完全一致には当たらないので生きる。

## 付録B: walkthroughの合否判定基準(design §4.2)

「手順書が整備された」ことの実証として・staging環境で次の演習を1回実行する(採取手順はREADME.md §4・実施はスーパーバイザー)。

1. 1世代戻し(§6.2): 戻し側と復帰側の両方で・API healthy+GET /v1/users/me 200。
2. 停止手順のドライラン(§3.2): 各遮断段(段1・段2・段3)で「止まるべき経路が止まり・残すべき経路が生きている」ことの観察記録。
3. alembic downgradeを実行していないこと(§6.1の規律どおり)。
4. 記録: 10 §5の全項目(試験ID・実施日・環境・入力条件・合否)が揃っていること。

- 実施時の運用: staging-reset(フルリセット)から開始する。walkthrough自体は性能測定を伴わないため・ci常設環境の一時停止(STATUS運用ルール6)は原則不要だが・実施環境の負荷が高い場合は実施者が止めてよい(実施記録に明記)。
````

- [ ] **Step 2: 通読点検**

全文を通読し・(a)表記のゆれ(「段1」「区1」等の呼び名が混在していないか)・(b)手順内のコマンドが付録A・§6.2・§3.2で同じものを指しているか・(c) `(空欄)` が20個あるかを確認する。読みにくい一文(読点の位置・一文二義)があれば直す。確認コマンド:

```bash
rg -c '\(空欄\)' docs/plans/M4/ws-8-rollback.md
```

期待: `20`(件数実査。1つでも違えば・どこを書き換えたか特定して修正する)

- [ ] **Step 3: コミット**

```bash
git add docs/plans/M4/ws-8-rollback.md
git commit -m "docs: M4 ws-8 ロールバック・縮退運用手順書(11 §7の4区分+データ事故・3段階遮断・確定欄)"
```

### Task 2: G4証拠対応表 docs/testassets/results/README.md

**Files:**
- Create: `docs/testassets/results/README.md`

**Interfaces:**
- Consumes: 手順書の節番号(§2・§3・§6.2・§9・付録B)・既存証拠YAMLの実在パス・ws-5報告書「スーパーバイザー検証時の手順4」の3点
- Produces: G4判定(人間)が読む対応表・欠け3点とwalkthroughの採取手順(§8手順3〜4がこの手順で実施される)

- [ ] **Step 1: 対応表READMEを書く**

まず・対応表に書く証拠パスの実在を確認する(この確認はTask 3の照合の元にもなる):

```bash
ls docs/testassets/results/
```

期待(計画時の実在一覧): g1-result-*.yaml 4点・g2-jev-result-*.yaml 3点・ws3-staging-result-20261003-231505.yaml・ws4-staging-result-20261004-124823.yaml・ws6-bruteforce-10000/50000/100000-*.yaml 3点・ws6-load-*.yaml 4点(123006が本命)・ws7-nfr-result-*.yaml 5点・ws7-probe-20261005-104449.yaml。実在が異なる場合は実在する方を対応表に書く(ws5-・ws8-接頭辞は現時点で存在しないのが正常)。

続いて・以下の全文を作成する(パスはStep 1の実在確認に合わせる):

````markdown
# docs/testassets/results/ — 証拠索引とG4 7項目対応表

- 位置づけ: 10 §5「証拠は試験ID・実施日・環境・入力条件・合否とともにdocs/testassets/results/へ保管する」の置き場。本READMEはこのディレクトリの索引であり・G4(12 M4=11 §5の7項目をstagingで先行確認)の判定用に証拠を対応付けた表。
- 読み方の注意: 対応表は「どの証拠がどの項目を支えるか」の索引である。機械判定の記録値と証拠パスの引用が本体で・**量的合格線の正式判定(性能5目標・総当たり等)はG4の人間側が行う**(ws-6 design §2.7)。対応表から合格宣言をしない。

## 1. ファイルの命名規則

`{単位プレフィックス}-{領域}-{タイムスタンプYYYYMMDD-HHMMSS}.yaml`(例: ws7-probe-20261005-104449.yaml)。タイムスタンプは実施完了時刻・UTC実時間(プローブ等は試験窓完了時)。

## 2. G4 7項目×証拠対応表

| # | G4項目(11 §5) | 証拠 | 状態・備考 |
|---|---|---|---|
| ① | 27項目グリーン | ci分=**test-ci常設緑(最新1754 passed・2026-10-05)**。10 §3「ci(毎コミット)」の字義どおり・毎コミットで再実行されていること自体が証拠で・個別YAMLは作らない。ゲート時の証拠YAML: g1-result-*.yaml 4点(202115・203258・203517・210042)・g2-jev-result-*.yaml 3点(122004・134810・144518)。staging分=ws3-staging-result-20261003-231505.yaml(33 items全pass)・#5本格検証=ws6-bruteforce-10000/50000/100000-*.yaml(3点) | 充足(承認§5-8: ci分はtest-ci+G1/G2時YAML+M2 ws-8報告書の参照で充足・staging再実行はしない) |
| ② | 追加試験グリーン(D-10年齢制限・D-23成立後ブロック・FR-21表示規制・FR-17自己ペア除外) | ws3-staging-result-20261003-231505.yaml 内のD-10(422 UNDER_AGE)・D-23(409 CHAT_READONLY)・FR-21(本文テンプレートバイト一致・サマリ語不含)・FR-17のitems。実施記録: docs/plans/STATUS.md「G3判定資料」③(2026-10-02) | 充足 |
| ③ | 非機能試験グリーン | 性能5目標=ws6-load-20261004-123006.yaml(本命)+ws6-load-20261004-{105203,105538,113320}.yaml(経過3本)。総当たり=ws6-bruteforce-*.yaml(3点)。可用性=ws7-probe-20261005-104449.yaml。バックアップ復旧・縮退・Queue障害=ws7-nfr-result-*.yaml(5点・n2/n3/n5ほか) | 充足(記録値の引用のみ。正式判定はG4の人間側) |
| ④ | 測定開始状態(Observability稼働・MLR日次集計稼働) | ws4-staging-result-20261004-124823.yaml(items 8全pass=3群+アクセス系メトリクス露出・prometheus 4 target up・MLR日次集計ジョブJST 0時発火+実測一致) | 充足 |
| ⑤ | 告知・同意とログ許可リスト | **現存の証拠YAMLなし**。参考: docs/plans/STATUS.md ws-5節にunit 5 passed(2026-10-03)の実行記録への言及があるが・10 §5形式の証拠はresults/に未整備。→ 本READMEの§3の採取手順で採取する | 欠け=本単位で採取(スーパーバイザー実施) |
| ⑥ | alert/circuit breakerの動作確認 | D-16 alert=ws7-nfr-result-*.yaml(n3: 80%で発報1回+源流レポート添付)。Queue lag alert=同n5(発報→resolve往復)。circuit breaker=M2 ws-8のci環境E2E(切替・双障害skipped・開放/半開往復・マージf1eb6eb)+test-ci常設で緑維持。ci分は証拠YAMLでなくtest-ci+M2 ws-8報告書の参照(design §1.3の表⑥) | 充足 |
| ⑦ | ロールバック手順・担当・連絡経路 | docs/plans/M4/ws-8-rollback.md(手順書本体)+walkthrough証拠ws8-rollback-walkthrough-*.yaml(§4の採取手順・スーパーバイザー実施)+確定欄(手順書§7・§8=人間領域・G4判定時に入力) | 本単位で整備(実施済み要素と確定欄を区別して読むこと) |

## 3. 欠け証拠(ws-5分3点)の採取手順(スーパーバイザー実施)

ws-5報告書「スーパーバイザー検証時の手順4」の3点を・そのまま採取手順として示す。証拠形式は10 §5準拠(試験ID・実施日・環境・入力条件・合否)。staging環境の実施はスーパーバイザーが行う(ws-5〜ws-7と同一の役割分担)。**実施の順序は3-1→3-2→3-3**(3-3のログ照合は・3-1で作成した本文を含むIntentのログが流れた後に行う)。

### 3-1. 同意フローのstaging実行記録(試験ID: ws5-consent)

未同意ユーザーのIntent作成が422で拒まれ・同意POST後にactiveで受理される順序を観察する。前提: staging環境がup済み(`make staging-reset` 直後でよい)・.staging/keys/に鍵ペアがある。

```bash
# 1) 未同意ユーザーのJWTを発行する(標準出力にJWTが出る・それをTOKENへ)
cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject ws8-consent-u1 --private-key ../.staging/keys/idp_test_private.pem
cd ..
#    ※表示されたJWT文字列をコピーして TOKEN='<JWT>' と設定する(単独実行してから使う)

# 2) usersの作成(未同意のまま。display_name+birth_date必須)
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"display_name":"ws8-c1","birth_date":"1990-06-15"}' http://127.0.0.1:18000/v1/users
#    期待: 201

# 3) 未同意でのIntent作成→422(エラーコードCONSENT_REQUIREDを含む応答本文を残す)
curl -s -w '\n%{http_code}\n' -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"raw_text":"今夜天文館あたりで居酒屋に行きたい。のんびり席を温めたい","status":"active","structured_intent":{"category":{"primary":"drinking","secondary":"居酒屋"},"alcohol_involved":true,"time":{"start":"<実施日以降の未来時刻+09:00>"},"location":{"name":"天文館","radius_m":1000},"budget":{"max":3500},"participants":{"min":2,"max":2},"soft_constraints":["のんびり席を温めたい"],"visibility":"hidden_until_match","notification_level":"proposals_only"}}' http://127.0.0.1:18000/v1/intents
#    期待: 422 + CONSENT_REQUIRED(※time.startは実施日の未来・+7日上限内に差し替える)

# 4) 同意の記録
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/v1/users/me/consent
#    期待: 200

# 5) 同意後のIntent作成→201
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{同一のbody}' http://127.0.0.1:18000/v1/intents
#    期待: 201
```

記録するもの: 各curlの時刻・ステータス(422の応答本文を含む)・JWTのsubject(機微値のJWT自体は記録しない)。Intent作成bodyはm4-seed.yamlの実例(SD-001と同型・staging実績のある構造)を流用している。

### 3-2. 許可リスト登録表+機械検査結果(試験ID: ws5-logallowlist)

1. 機械検査の実行結果:

```bash
cd backend && uv run pytest tests/unit/test_log_allowlist.py -v
```

期待: 5 passed(許可リスト外のログ・禁止フィールドの突合等)。

2. 登録表の写し: ALLOWED_STRUCTURED_LOGS=latch.llm.send→SendRecord・latch.push.send→PushSendRecord(出典: backend/src/latch/core/logallow.py:21)。許可リスト一覧=問い合わせ開示資料の雛形として・各レコードのフィールド一覧を添付する。

### 3-3. ログサンプルのraw_text非混入照合(試験ID: ws5-logsample)

docker logsから構造化ログの実サンプルを取り・raw_text相当が含まれないことを確認する(ws-3報告書のpushログ読取と同型)。

```bash
docker compose -f compose.staging.yaml logs --no-color worker1 --tail 2000 > /tmp/ws8-w1.log
rg -c 'latch.llm.send|latch.push.send' /tmp/ws8-w1.log
#    期待: 1以上(対象ログが現れる。0ならapi側のログも見る)
rg -c '居酒屋|raw_text' /tmp/ws8-w1.log
#    期待: 0(本文相当と生値のキー名がログに現れないこと)
```

記録するもの: 照合したログの範囲(行数・期間)・対象ログのサンプル(latch.llm.send等のフィールド一覧)・検出0件の出力。lint相当の機械突合(08 §2.4のFORBIDDEN_FIELD_NAMES=raw_text・geo・lat等が含まれない)はtests/unit/test_log_allowlist.pyが担保しているため・本採取は実ログでの観察補完である。

## 4. walkthrough証拠(試験ID: ws8-rollback-walkthrough)

「ロールバック手順書が整備された」ことの実証(design §2.6・承認§5-4)。docs/plans/M4/ws-8-rollback.mdの手順をstagingで1回通して・10 §5形式で記録する。実施はスーパーバイザー・採取手順は手順書§6.2・§3.2・付録Bのとおり。合否判定基準:

- 1世代戻し: 戻し側と復帰側の両方でAPI healthy+GET /v1/users/me 200。
- 停止手順のドライラン: 段1(worker停止)・段2(nginx受付遮断)・段3(api全停止)の各段で「止まるべき経路が止まり・残すべき経路が生きている」ことの観察記録。
- alembic downgradeを1度も実行していないこと(スキーマ前進の規律)。
- 10 §5の全項目(試験ID・実施日・環境・入力条件・合否)が揃っていること。

## 5. 既存ファイル一覧

| プレフィックス | 内容 | 実在数(2026-10-05時点) |
|---|---|---|
| g1- | G1精度ゲート(Parser構造化・alcohol)の実測 | 4 |
| g2-jev- | G2日本語評価(Jev)の実測 | 3 |
| ws3-staging- | 機能試験27項目のstaging実行 | 1 |
| ws4-staging- | Observability受け入れ | 1 |
| ws6-load- | 性能5目標(123006が本命・他3点は経過) | 4 |
| ws6-bruteforce- | 総当たり3段階 | 3 |
| ws7-nfr-result- | バックアップ復旧・縮退・Queue障害等 | 5 |
| ws7-probe- | 可用性(合成監視) | 1 |
| ws5-・ws8- | (未整備) | 0(本READMEの§3・§4で採取) |
````

- [ ] **Step 2: 対応表の各行のパス実在を機械照合**

対応表に書いたパスの実在を1回照合する(design §2.4「機械検査は1回の照合で済む」):

```bash
ls docs/testassets/results/g1-result-20260928-210042.yaml docs/testassets/results/g2-jev-result-20260930-144518.yaml docs/testassets/results/ws3-staging-result-20261003-231505.yaml docs/testassets/results/ws4-staging-result-20261004-124823.yaml docs/testassets/results/ws6-load-20261004-123006.yaml docs/testassets/results/ws6-bruteforce-100000-20261004-133456.yaml docs/testassets/results/ws7-probe-20261005-104449.yaml
```

期待: 7ファイルの実在(エラー0)。対応表の「〜等」「〜(3点)」の形で参照している残りのパスも・`ls docs/testassets/results/ | rg 'ws6-bruteforce|ws7-nfr'` 等で点数を実査する(期待: bruteforce 3・nfr 5)。

- [ ] **Step 3: コミット**

```bash
git add docs/testassets/results/README.md
git commit -m "docs: G4 7項目×証拠の対応表+欠け証拠(ws-5分3点)の採取手順をresults/READMEへ新設"
```

### Task 3: 自己点検(実在照合)+現行値維持+報告書

**Files:**
- Create: `docs/plans/M4/ws-8-report.md`

**Interfaces:**
- Consumes: Task 1・Task 2の成果物(照合の対象)・§2.1の照合コマンド一覧
- Produces: 報告書(スーパーバイザー検証・§8実施の前提資料)

- [ ] **Step 1: 実在照合(design §4.3)を再実行し・結果を記録する**

Task 1・Task 2の文書に書いた参照名を一つずつ突合する(計画時の§2.1と同一コマンド・drift検出)。**検証チェーンにパイプを書かない**・各コマンドの件数を目視実査する。

```bash
# (a) 手順書が参照するMakefileターゲットの実在(13種)
rg -n '^(staging-reset|staging-ps|staging-probe|staging-d16|staging-dump|staging-db2-up|staging-restore|staging-db2-switch|staging-db2-down|staging-token|test|test-ci|lint):' Makefile
```

期待: 13行実在(staging-tokenを含む13種。手順書本文とREADMEで参照する全ターゲット)。

```bash
# (b) compose.staging.yamlのサービス実在(手順書でstop/startする名前)
rg -n '^  (api1|api2|nginx|worker1|worker2):' compose.staging.yaml
```

期待: 5行実在。

```bash
# (c) 停止系envの不在(手順書§3.1の記述の根拠)とalert_queue_enabledの実在
rg -n 'MAINTENANCE|STOP|FREEZE' backend/src/latch/settings.py
```

期待: 出力なし(不在=手順書の記述どおり)。

```bash
# (d) 遮断対象経路と守るべき経路の実在(手順書付録A・README §3-1の根拠)
rg -n '@(parse_router|intents_crud_router)\.(get|post)' backend/src/latch/intents/routes.py
```

期待: POST /parse(81)・GET /expiry-options(122)・POST ""(259)・GET ""(277)等が実在(GET一覧が存在=遮断にメソッド条件が必須の根拠)。

```bash
# (e) 許可リストの実在(README §3-2の根拠)
rg -n 'ALLOWED_STRUCTURED_LOGS' backend/src/latch/core/logallow.py backend/tests/unit/test_log_allowlist.py
```

期待: 定義(21行目)と試験の両方で実在。

```bash
# (f) マイグレーション8本の実在(手順書§6.1「すべて拡張系」の根拠)
ls backend/migrations/versions/
```

期待: 0001〜0008の8ファイル。

照合結果(期待値・実測値・一致/不一致)を報告書へ書く。**不一致が出たら・文書を修正してから照合をやり直す**(環境側・コード側は絶対に直さない)。

- [ ] **Step 2: 確定欄の空欄照合**

```bash
rg -c '\(空欄\)' docs/plans/M4/ws-8-rollback.md
```

期待: `20`(§2.2の固定値)。対応表READMEは空欄を持たない:

```bash
rg -c '\(空欄\)' docs/testassets/results/README.md
```

期待: 出力なし(0件=機械検査の終了コード1はこの照合では期待どおり・「matches not found」を確認する)。

- [ ] **Step 3: 現行値の維持を確認(コード変更ゼロの証明)**

```bash
make lint
```

期待: ruffエラーなし(All checks passed)。

```bash
make test
```

期待: **1472 passed**(基点mainと同値・design §4.1「現行値を維持するだけ」)。実数を報告書に記録する。

```bash
git diff main --stat -- backend/ compose.yaml compose.staging.yaml compose.load.yaml compose.dashboard.yaml Makefile docker/ backend/pyproject.toml
```

期待: **空出力**(コード・環境資産の変更ゼロの証明)。

- [ ] **Step 4: 差分の範囲確認**

```bash
git status --short
```

期待: §4.1の一覧どおり(3ファイル追加のみ・既存ファイルの変更行がない)。

- [ ] **Step 5: 報告書を書く**

`docs/plans/M4/ws-8-report.md` を§7の形式で作成する。staging実施分(§8手順1〜5)は「スーパーバイザー検証待ち」として骨格を残す。

- [ ] **Step 6: コミット**

```bash
git add docs/plans/M4/ws-8-report.md
git commit -m "docs: M4 ws-8 実施報告(文書整備2本+実在照合の記録・staging実施は検証待ち)"
```

## 6. 完了条件(照合コマンドで証明できる形)

design §4(テスト方針)を完了条件へ引き取る。各条件の証明方法を明記する。

1. **手順書が11 §7の4区分+データ事故の5区分構造を持ち・3段階遮断表・遮断conf草案・デプロイ記録規律・確定欄(空欄20個)を含む**
   証明: `rg -n '^## ' docs/plans/M4/ws-8-rollback.md` で§1(判断基準)〜§9(記録)+付録A・Bの章立てが実在。`rg -c '\(空欄\)'` が20。3段階表は手順書§3.1の行(段1/段2/段3)3行。付録Aにconf全文がコードブロックで実在。
2. **対応表READMEがG4 7項目×証拠の行を持ち・欠け3点とwalkthroughの採取手順を含む**
   証明: `rg -n '^\| ①|^\| ②|^\| ③|^\| ④|^\| ⑤|^\| ⑥|^\| ⑦'` で7行実在。§3に3-1・3-2・3-3・§4にwalkthroughの合否基準が実在。
3. **対応表の各行が参照する証拠パスが実在する**
   証明: Task 2 Step 2のls照合(7ファイル)がエラー0・点数実査(bruteforce 3・nfr 5)。
4. **実在照合(design §4.3)で・手順書・READMEが参照するターゲット・サービス名・env名・パスがすべて実在する**
   証明: Task 3 Step 1の(a)〜(f)の照合コマンド実行・期待値との一致を報告書へ記録。
5. **現行値の維持(コード変更ゼロ)**
   証明: `make lint` グリーン・`make test` **1472 passed**(実数を報告書に記録)・`git diff main --stat -- backend/ compose*.yaml Makefile docker/ pyproject.toml` 空出力。
6. **差分の範囲が§4どおり**
   証明: `git status --short` が新規3ファイルのみ。
7. **報告書が§7の形式で存在し・スーパーバイザー検証待ちの事項を明記している**
   証明: docs/plans/M4/ws-8-report.md の実在+「test-ci: 実行不要(コード変更ゼロ)」の記録行。

## 7. 報告形式

報告書: `docs/plans/M4/ws-8-report.md`。以下の節をこの順で書く。

1. **実施サマリ**: 実施日・環境(worktree/ブランチm4-ws-8・基点main head=d231d60)・コミット一覧(ハッシュ+1行要約)。
2. **完了条件別の証拠**(§6の1〜7の各項に): 実行した照合コマンドと出力の要約(件数・一致/不一致)。
3. **実在照合の結果一覧**(design §4.3): §2.1の表12行をこの順に・「参照名→期待→実測(照合コマンドの実出力)→一致/不一致」の形で記録。不一致があった場合は修正内容も。
4. **確定欄の空欄照合**: 20件照合の結果とREADMEの0件照合の結果。
5. **コード変更ゼロの証明**: `git diff main --stat`(対象指定)の空出力・`git status --short` の全行。
6. **make lint・make testの実数**: 1472の基準値と実数(ズレがあれば原因の考察)。
7. **スーパーバイザー検証待ちの記録**: (a)「test-ci: 実行不要(コード変更ゼロ)」の明記 (b)staging実施(§8手順1〜5: walkthrough・ws-5証拠3点)の実施待ち (c)G4時確認事項13への追記依頼(§8手順5)。
8. **詰まった箇所と判断**: 作業中に停止・確認した点(なければ「なし」)。

## 8. スーパーバイザー実施手順(walkthrough+ws-5証拠3点。検証時)

前提: 本単位マージ後のmain。全コマンドはリポジトリルートで実行(`cd` したら戻す)。検証チェーンはパイプを書かない・各段で件数・ステータスを実査する。**本手順の成果物は・証拠YAML(docs/testassets/results/ws8-rollback-walkthrough-*.yaml)とdocs/plans/STATUS.mdへの記録**である。ws-5分3点(README §3)のYAMLも本手順で採取する。

```bash
# 0. マージ後mainの現行値確認
make lint
make test
#    期待: 1472 passed
make test-ci
#    期待: 1754 passed(コード変更ゼロだが・マージ規律として実施し・記録する)

# 1. staging環境のフルリセットと起動
make staging-reset
make staging-ps
#    期待: db・redis・pubsub・api1・api2・nginx・worker1・worker2がrunning

# 2. walkthroughの前段: デプロイ記録の採取(手順書§2の規律を最初に適用する)
date -Is
git rev-parse HEAD
#    → .staging/deploy-log.md に記録(手順書§2の雛形どおり)
#    あわせて「1世代」の特定: docsのみのコミットを戻しても実証にならないため・
#    直近のコード変更コミットを特定して「実質の1世代」として記録に残す
git log --oneline -3 -- backend/ compose.yaml compose.staging.yaml Makefile
#    計画時の期待: cf9e612 が先頭(実施時点の直近コード変更コミットを使う)

# 3. walkthrough手順1: 1世代戻し(手順書§6.2)
#    3-1: JWT発行(単独実行して出力を記録・以後 TOKEN='<JWT>' として使う)
cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject ws8-walk --private-key ../.staging/keys/idp_test_private.pem
cd ..
#    3-2: 現行版の動作確認
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/v1/users/me
#    期待: 200
#    3-3: 旧版へcheckout→再ビルド・再作成
git checkout <手順2で特定したSHA>
docker compose -f compose.staging.yaml build api worker
docker compose -f compose.staging.yaml up -d --force-recreate api1 api2 worker1 worker2
sleep 20
make staging-ps
#    期待: 全サービスrunning・healthy
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/v1/users/me
#    期待: 200(旧版が新スキーマ0008で動作=手順書§6.1の実証点)
#    3-4: 復帰
git checkout main
docker compose -f compose.staging.yaml build api worker
docker compose -f compose.staging.yaml up -d --force-recreate api1 api2 worker1 worker2
sleep 20
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/v1/users/me
#    期待: 200
#    ※戻し側・復帰側の時刻とhealthyまでの所要を記録する

# 4. walkthrough手順2: 停止手順のドライラン(手順書§3.2)
#    4-1(段1): worker停止→GET生きる確認→解除
docker compose -f compose.staging.yaml stop worker1 worker2
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/v1/users/me
#    期待: 200(API継続)
docker compose -f compose.staging.yaml start worker1 worker2
#    4-2(段2): 受付遮断conf適用(付録A全文を.staging/drop-intents.confに置いてから)
docker compose -f compose.staging.yaml cp .staging/drop-intents.conf nginx:/etc/nginx/conf.d/00-drop.conf
docker compose -f compose.staging.yaml exec nginx nginx -t
#    期待: 「conflicting server name」のwarning付きで successful(warningは想定内・errorはNG)
docker compose -f compose.staging.yaml exec nginx nginx -s reload
#    段2の観察(止まる/生きるを各1回ずつ):
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{README §3-1手順3と同一のbody}' http://127.0.0.1:18000/v1/intents
#    期待: 503
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/v1/users/me
#    期待: 200
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/v1/intents
#    期待: 200(GET一覧は遮断しない=Review Focus 2の実証点)
#    段2解除:
docker compose -f compose.staging.yaml exec nginx rm /etc/nginx/conf.d/00-drop.conf
docker compose -f compose.staging.yaml exec nginx nginx -s reload
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{同一body}' http://127.0.0.1:18000/v1/intents
#    期待: 201に戻る(遮断解除・作成したIntentはwalkthrough記録に含めてよい)
#    4-3(段3): 全API停止(最終手段)→解除
docker compose -f compose.staging.yaml stop api1 api2
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18000/v1/users/me
#    期待: 502(nginxがupstream不在を返す)
docker compose -f compose.staging.yaml start api1 api2
sleep 20
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/v1/users/me
#    期待: 200
#    ※各段の時刻(実施・解除)と観察値を記録(手順書§3.4の4点のうち・演習では「実施・解除」)

# 5. ws-5証拠3点の採取(README §3の手順をそのまま実行)
#    5-1: 同意フロー(README §3-1・手順1〜5のcurl順序観察)
#    5-2: 許可リスト(README §3-2・pytest実行結果+登録表の写し)
#    5-3: ログサンプル(README §3-3・docker logs照合)
#    → 各試験ID(ws5-consent・ws5-logallowlist・ws5-logsample)のYAMLを
#      docs/testassets/results/へ作成(10 §5形式・5項目)

# 6. walkthrough証拠YAMLの作成
#    docs/testassets/results/ws8-rollback-walkthrough-<ts>.yaml
#    試験ID=ws8-rollback-walkthrough・実施日・環境(staging)・入力条件(1世代SHA・
#    遮断段の各操作)・合否(付録Bの4基準の各判定)・観察値(時刻とステータス一式)

# 7. ci復帰確認(運用ルール6・stagingの演習でciを止めた場合のみ)
make up && make ps

# 8. STATUS.mdへの記録(スーパーバイザー側)
#    (a) G4時確認事項13へ追記: 「11 §7『新規Intent受付と通知を停止』の現実的手段は
#        機能フラグではなく環境操作(worker停止+nginx受付遮断)で読み替えた。
#        本番手段はPhase 0確定欄(手順書§8)で確定する」(承認§5-2)
#    (b) G4チェックリスト⑦の行へ: 手順書パス・walkthrough証拠パス・確定欄は
#        人間領域(G4判定時入力)である旨を記録
#    (c) 単位表ws-8の状態更新
```

- walkthrough中に遮断が想定どおり効かない(段2のPOSTが503にならない等)場合は・その場で中止し・観察結果をそのまま証拠YAMLに「不成立」と記録して持ち帰る(design §6・実施時確認事項)。手順書やconfの修正は・次の計画単位を立てて行う(検証中の環境側・コード側の改変はしない)。

## 9. Self-Reviewの結果(計画完成時点で実施済み)

1. **spec適合**: design §1.1の3成果物→手順書(Task 1)・対応表(Task 2)・確定欄(手順書§7・§8=人間領域)。design §5の8件→§2.2の落とし込み表。design §6「未解決論点なし」を確認・**BLOCKED相当の論点は残っていない**。supervisor補足3点(TDDでない順序・staging実施のスーパーバイザー分離・natural-japanese)はSpec欄と§5・§8に反映。
2. **プレースホルダー検査**: 手順書・対応表の草案には「(空欄)」20個の**意図した空白**(承認§5-6の人間領域)以外に・未確定値・TBD・「適宜」「必要に応じて」を置いていない。実施手順のbody実例はm4-seed.yamlの実在構造で書き切った。
3. **参照名の型整合**: 手順書・README・§8手順が参照するターゲット名・サービス名・経路名は§2.1の12行で突合済み。「staging-test」(ターゲット実在・本手順では不使用)と「staging_reset」(アンダーバー表記・非実在)等の混用はない。手順書の節参照(README→手順書§2・§3・§6.2・§9・付録B)はTask 1の草案章立てと一致。
4. **Review Focus**: 5項目→Task 1付録A・§2・README §3・Task 3 Step 2・§8手順4に照合手順を割り当て済み(§3の紐付け表)。
5. **数値の整合**: 空欄20個の内訳=§2雛形5+§5運用値2+§7担当表8+§8本番欄5(草案本文で再カウント済み)。test値1472・test-ci 1754はSTATUS(d231d60)とdesign §4.1で一致。
