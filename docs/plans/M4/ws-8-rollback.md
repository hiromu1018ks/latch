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

- 確認: nginx経由の全リクエストが502/504を返す(upstream不在。2026-10-05 walkthroughでは504を観測)。
  復帰(start)はコンテナIPが不変のためnginx再起動なしで到達できる(再作成した場合は§6.2の再起動が必須)。

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
docker compose -f compose.staging.yaml build api1 api2 worker1 worker2
docker compose -f compose.staging.yaml up -d --force-recreate api1 api2 worker1 worker2
docker compose -f compose.staging.yaml restart nginx
```

- サービス名はapi1・api2・worker1・worker2(`api`・`worker`というサービスは存在しない)。
- **apiを再作成(force-recreate)したらnginxの再起動が必須**である。nginxはupstream(api1・api2)の
  IPを起動時に解決して持ち続けるため・再作成でIPが変わると旧IPへ流し続け502になる
  (2026-10-05 walkthroughで再実証。stop/startはコンテナIPが不変のため再起動不要)。
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
