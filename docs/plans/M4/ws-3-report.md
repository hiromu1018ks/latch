# M4 ws-3(機能試験27項目のstagingフル実行)実行報告

- ブランチ: m4-ws3 / ベース: 6206829(ws-5マージ後・head=0007)
- 日付: 2026-10-03(最終フル実行 23:15:05 JST)
- 実行者: agent3

## 結果サマリ

| 完了条件(計画§7) | 結果 | 証拠 |
|---|---|---|
| 1. lint+unit(基準+5) | PASS(注1) | unit **1335 passed**(基準実数1327〔ws-5マージ済みmainの実測〕+ stub判定値ピン5件 + 生成器不変式ピン3件〔裁定Aの追加〕) |
| 2. basename一意 | PASS | `find backend/tests backend/staging_tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 空 |
| 3. stub判定値ピン | PASS | `tests/unit/test_llm_factory.py`(eleven_fields改名+既定値ピン+env網羅)+ `tests/unit/test_llm_stub_response_settings.py` 5件 全PASS。stagingコンテナ内実測も実施: `noul= 0.9 score= 3.6 env_noul= 0.9` |
| 4. staging-test全グリーン | PASS | `make staging-reset` → `make seed-generate ARGS="--base-datetime 2026-10-04T11:00:00+09:00"` → `make seed-verify`(OK)→ `cd backend && uv run pytest staging_tests -q` → **34 passed / 所要599秒(10分)**・証拠YAML `docs/testassets/results/ws3-staging-result-20261003-231505.yaml`(33 items全pass) |
| 5. G3繰越回収(#13〜#23) | PASS | 証拠YAMLに 02#13・#14・#15・#16(+Intent期限)・#17(提案時+成立後)・#18・#19・#20・#21・#22・#23 のIDつきitems全部存在(下記対応表) |
| 6. マイグレーションなし・依存追加なし | PASS | `git diff main -- backend/alembic backend/pyproject.toml backend/uv.lock` → 空・staging DBのhead=0007をS0の名前つき検査で毎回確認 |
| 7. 変更ファイル一覧どおり | PASS(注2) | 下記「変更ファイル」・計画§4 + 裁定Aによる追加分(seed生成器/verify/YAML/計画§2.2再実測値) |
| 8. 実行手順記録 | PASS | 下記「実行手順」 |
| 9. make test-ci | **スーパーバイザー検証待ち**(agent3は実行せず) | — |

注1: `make lint`のruff format --checkは **ws-5スコープの2ファイル**(`backend/src/latch/users/consent.py`・`backend/tests/unit/intents/test_intents_service.py`)で失敗する。**本worktreeの基底main(6206829)時点からの既存差分**で・スーパーバイザー通知どおりmain側98d044fで解消済み。worktree側では触っていない(マージ時に解消)。本単位の全変更ファイルはスコープ付きlintで緑。
注2: `git diff main --name-only`には本branchに無い main側の新規コミック分(docs/learn/*・STATUS.md等・ws-5マージ後のlearn同期)も表示される。branch側からの変更は下記一覧のとおり。

## 実行手順(再現可能な形)

1. `make staging-keys`(worktree毎に鍵を生成・本worktreeで実施済み)
2. `docker compose -f compose.staging.yaml build api1 api2 worker1 worker2` — **staging-resetにbuildは含まれない**ため、製品コード変更後は明示buildが必須(Dockerfileがsrcを焼き込む)
3. `make staging-reset`(down -v→up→migrate head=0007→geo-import isj46/osm563)
4. `make seed-generate ARGS="--base-datetime 2026-10-04T11:00:00+09:00"` + `make seed-verify`(OK・**構造変更コミット済みの現YAMLと同一のため差分なし**)
5. api再作成後は `docker compose -f compose.staging.yaml restart nginx`(nginxが起動時にupstream IPを解決して保持するため・未実施だと502)
6. `(cd backend && uv run pytest staging_tests -q)` — 所要 **10分**(待ち時間の内訳: S0収束~1分・S1〜S3のcreated再発行後の観察待ち・S3のsweeper排水~10分)

実行日の特定事項: base_datetime(2026-10-04T11:00+09:00)は実施日(10-03)の翌日11:00。**S0冒頭で仮想Clockをbaseへ設定する**(下記適応9)ため、S1#8の前進先は `2026-10-05T00:05+09:00`・S3の#16前進先 `2026-10-06T06:00+09:00`・Intent期限前進先 `2026-10-10T00:10+09:00`(証拠YAMLのclock_set_toにISO明示)

## 02#対応表(実行結果・証拠YAMLから転記)

| 02# | 検査 | シナリオ | 合否 | 観測値(要約) |
|---|---|---|---|---|
| #5 | スモーク | S1 item05 | pass | max_candidates/intent=16(K_v50以内)・jev総数626(≤2400)・max_jev/intent=15(記録照合・注3) |
| #6 | 既存Intent候補の生成 | S1 item06 + S0土台 | pass | 代表EX 7組全て無向実在・候補行699(有向1方向の実装挙動) |
| #7 | PATCH再評価 | S1 item07 | pass | 0→1(PATCH一致後)→1(離行後・新version不生成・旧行残存) |
| #8 | 時刻到来再評価 | S1 item08 | pass | 保留406件→JST0時跨ぎで昇格23件(clock 10-05 00:05) |
| #9 | Hard Filter E2E | S1 item09 | pass | EX fail 10組すべて不生成(db_pairs=699突合) |
| #10 | 意味近接ペア | S1 item10 | pass | 焼肉/肉居酒屋ペアの候補生成(stub下ではL1通過ゆえ・案A) |
| #11 | 密集配置 | S1 item11 | pass | 30件クラスタ・起点候補18(K_v50以内)・起点Jev 14(記録) |
| #12 | jev_result記録 | S1 item12 | pass | would双方0.9・provider=typesafe_jev・model=jev-1.13.0 |
| #13 | 提案通知+muted | S2 item13 | pass | proposed 261・notifications 391・push 391・**muted提案のpush 0件**・回答可 |
| #14 | yes/no回答と遷移 | S2 item14 | pass | responses [no,yes]・candidate→proposed→partial_accept→rejected |
| #15 | 双方YES→matched+チャット | S3回答工程+S4 item15 | pass | matched維持・messages 201 |
| #16 | 部分YES期限確定 | S3 item16 | pass | proposed→partial_accept→**expired**(D-06期限時確定)・expired_delta 300・clock 10-06 06:00 |
| #16' | Intent期限切れバッチ | S3 item16' | pass | expired 90→190件・clock 10-10 00:10 |
| #17 | 可視範囲(提案時/成立後) | S2+S4 | pass | 提案時participants null・7キー/成立後participants 2人・サマリ維持 |
| #18 | グループ全員YES | S3 item18 | pass | 集合行proposed・全員yes→matched・participants 3人 |
| #19 | 双方向送受信 | S4 item19 | pass | 双方向201+一覧で2通確認 |
| #20 | ブロック後不生成 | S5 item20 | pass | ブロック後の一致Intentで候補0 |
| #21 | 通報受付 | S4 item21 | pass | reports受付 status=pending |
| #22 | visibility分岐 | S2 item22 | pass | hidden={headcount,match_level}/summary_only=7キー |
| #23 | 座標非表示 | S2 item23 | pass | 座標キー・数値パターンとも検出なし |
| #26 | MLR | S2中間/S5最終 | pass | 中間 315/0・**最終 496/2 = 0.004** |
| D-10 | 年齢制限 | S5 d10 | pass | EX-029〜033すべて422 UNDER_AGE・EX-004/005非生成 |
| D-23 | 成立後ブロック | S4 d23 | pass | チャット409 CHAT_READONLY(matchedは維持・下記適応14) |
| FR-21 | push表示規制 | S2 fr21 | pass | 本文テンプレートバイト一致(1種)・条件サマリ語不含・お知らせにサマリ7キー |
| FR-17 | 自己重複 | S3 self_dup | pass | 自己重複集合(SD-048×SD-049を含む行)0件・対照(EX-026)8行 |
| 通知順序 | EX-026 | S3 | pass | クラスタ8行(candidate/proposed混在)・同一集合のproposed複数なし |
| S0系 | 環境/投入/突合/D-08集計/#20素材 | S0 | pass | head 0007・100人×consent+300 Intent・627/699/7組・**6件超え84件** |

注3: K_j=8は**イベントあたり**のfan-out上限(layer4)で、Intentあたりの累積評価行数は他Intentのイベントで対象になる分を含むため≤8にならない(実測14〜15)。イベント単位の厳密観察はws-6へ(計画§2.5の想定どおり本単位は総量+分布の記録照合に留めた)。

## G3繰越の回収対応(02#13〜#23)

| G3判定資料 | staging再実行 | 合否 |
|---|---|---|
| 02#13(muted含む) | test_s2_item13_proposal_notification_and_muted | pass |
| 02#14 | test_s2_item14_responses_record | pass |
| 02#15 | test_s3_s4_pair_respond_yes + test_s4_item15_matched_and_chat | pass |
| 02#16 | test_s3_item16_partial_yes_deadline(+test_s3_intent_expiry_batch) | pass |
| 02#17 | test_s2_item17_before_match_visibility + test_s4_item17_after_match | pass |
| 02#18 | test_s3_item18_group_all_yes | pass |
| 02#19 | test_s4_item19_bidirectional_chat | pass |
| 02#20 | test_s5_item20_block_prevents_candidates | pass |
| 02#21 | test_s4_item21_report | pass |
| 02#22 | test_s2_item22_visibility_branch | pass |
| 02#23 | test_s2_item23_no_precise_coordinates | pass |

## 設計からの読み替え・逸脱の記録(計画§2.1 + 裁定後の追加)

計画§2.1の読み替え1〜5はすべて実施済み(head=0007・consent 100人・#11=6人×5件・#16=専用3人組・#18=専用グループ)。**以下はstaging実行で判明した実装意味論への適応**(すべて検査の緩和ではなく契約・実挙動への適合。該当コードにコメント記載):

1. **認証経路**: 保護エンドポイントのBearerはアクセストークン(HS256)で・計画コードのIdP JWT直渡しは401になる → `POST /v1/auth/token`で交換(conftest.issue_token)
2. **トークンの無キャッシュ化+IdP 30日化**: access tokenのexpは発行時の**仮想時刻**+1hのため、Clock前進(S1#8で+13h)で失効する → 毎呼び出しで交換・IdPトークンはexpires_in=30日で発行
3. **asyncpg jsonb codec**: jsonbはstrで返る(コードベース規約)ためdb接続にクライアント側codecを設定
4. **S0冒頭で仮想Clockをbase_datetimeへ設定**: 実行日がbase以前だと7日上限・年齢検証が実時間基準になり計画の時間設計(旅路)と崩れるため。旅路Step 1「D0の実時間=仮想時刻そのまま」の実装
5. **S0収束待ち**: created 300件のprocessed到達をwait_until化(投入直後はdebounce 10秒分の未処理が残る・即時assertは競合)
6. **#7 PATCH**: PATCHは全置換・全検証(service._update_active)のためtimeのみの部分bodyは422 → 全フィールド送信に修正
7. **wait_for_latch**: シナリオIntent作成直後の提案昇格待ち(デバウンス10秒+tick≤60秒)を追加(計画の「S0収束待ちの中でproposed昇格済み」前提とlazy生成コードの不整合解消)
8. **FR-21(c)**: notifications payloadは`{"latch_id"}`の最小参照(承認事項2)でproposalキーを持たない → 計画備考どおり実装形を確認のうえnotifications×latchesを`payload->>'latch_id'`でJOIN
9. **S0 L1突合の2経路化**: match_candidatesはペア検索(LAYER1_WHERE・people込み)と**集合検索(LAYER1_WHERE_BASE・people除く)**の両方の行を含む(layer1.py:58/82)ため、期待集合をjudge_all pass ∪ judge_pair_group passに拡大
10. **#5/#11のK_j観測化**: 上記注3のとおり
11. **#6の無向化**: 候補行は起点起点の有向1方向のみ実在(両方向は観測されない)→ 無向集合で代表EX 7組の実在を検査
12. **#14の遷移系列**: 先頭にlatch INSERT時の(None,'candidate')イベントが入る → ("candidate","proposed")の存在検査に変更
13. **シナリオ隔離の再設計**: 計画§2.3の同時刻スロットではシナリオ同士が相互にL1通過する(#11密集群×#13×#14等)ため、**日×カテゴリで非交差配置**に再設計(10-05:#14 activity/#22h drinking/10-06:#7 activity・#18 加治屋町・#16 浜町〔2529m>半径和2000mでPool分離〕/10-07:#13/10-08:#10 meal・fr21s drinking・#11 activity/10-09:#22s meal・#23 activity/10-11:s4-17 activity・p20 drinking)。verify済みのシード早朝Intent(6-11時・24件)との重複も機械突合済み
14. **D-23**: ブロックのcancelled化対象は進行中3状態(candidate/proposed/partial_accept)でmatchedは維持(safety/store.py)・チャットは409 CHAT_READONLYで遮断 → 合否条件をチャット遮断にしlatch状態を記録
15. **#18を3人組に**: グループクラスタは**3人組のみ構築**(stagingの全group_candidates 33行がcardinality=3・D-06同点サイズ昇順)・4人集合行は観測されない → 09 §3「3〜4人集合」の成立観察を3人組で実施
16. **#16の遷移をpartial_accept経由に**: 1人yes済みのため期限確定は(partial_accept, expired)(D-06部分YES期限時確定の実挙動)
17. **対照をEX-028→EX-026に**: グループPool検索は**30分Bucketで時間分割**(group_engine._POOL_SEARCH)のためEX-028の3 Intent(21:00/21:30/22:00)は同一Poolに入らず集合生成が構造的に起きない → EX-026(同Bucket×6件・生成済み)を対照に。自己重複判定も「同一ユーザー2 Intentを1行が含まない」(@> pair)に精緻化(クラスタはseed Intentと混成しうる実測)
18. **グループ系のembedding競合対策**: グループPool検索はイベント処理時に実行され、他メンバー未embeddingだとPool空になり再試行されない(実測・workerログ`group pool empty`)→ メンバーのembedding完備を待ってcreatedイベントを再発行(10 §1「疑似イベント注入」・make staging-publish同一経路)してGroupEngine.handleを再実行。seed側はembedding_completedイベントの再実行で自己修復するため対照(EX-026)は追加発行不要
19. **s4-17/p20への明示expires_at+S3回答工程**: response_deadlineは提案時刻+最大2時間(03 D-05)のため、S3のClock前進前にs4-17の双方yesを完了させる工程をS3へ追加・両ペアのIntentは明示expires_at(10-11T23:30/10-11T09:00)でClock前進後もactiveを維持(matched遷移の_MATCH_INTENTSはactive/paused対象)
20. **D-10の時刻差し替え**: reject素材の時刻(D+1..D+5)はS5時点(仮想D+6)で過去になり422 VALIDATION_ERROR(過去不可)が先に発火するため、年齢検証へ到達させる未来時刻(2026-10-11T20:00)へ差し替え(検査意味は不変・UNDER_AGE 5件を記録)
21. **#21のreason**: ReportRequest.reasonは4値のLiteral → "unpleasant_behavior"を使用
22. **#16/Intent期限の待ち延長**: sweeperは1tick 50件上限(settings.sweeper_batch_limit)で、Clock前進による期限超過バックログ(提案250件超・Intent~200件)の排水に複数tickを要するため600秒に拡大
23. **`#15`の回答工程をS3へ移動**(19と同じ理由)・S4 item15はmatched維持+チャット解放の観察

## D-08保留の実測(計画§2.2との突合)

- S0集計: 6件超えのユーザー×日=**84件**(計画時実測74件→**裁定A後の再実測84件に計画§2.2を更新済み**)・max=**34件**(SU-010@10-05)
- #8の昇格件数: **23件**(保留406件のうち・JST 0時跨ぎのリセットジョブ+保留キュー再評価)

## 期待値の確定記録(§0「staging検査の期待値確定手順」)

- S0突合の実測: passペア=**627**(計画時576→裁定A後の再実測・seed-generate出力と一致)・DB実在ペア=**699**(ペア検索+集合検索の2経路合計)・代表EX 7組=**全生成**
- #6両方向EXペア=無向7組全実在(有向は1方向のみの実装挙動)
- 全34試験が2連続構成の同一手順でグリーン(最終run: 34 passed/599秒)。7回のフル実行の過程で上記適応1〜23を確定させた

## 引継ぎ6への回答(llm.send観測)

`latch.llm.send`は**workerログに観測される**(apiは保存経路にParserを呼ばないため出ない)。最終runのworker1/2ログの集計: `{"system":"embedding"}` 359行・`{"system":"jev"}` 832行。証拠方法として問題なし(helpers_pushlogと同一形の行パースで抽出可能)。

## 変更ファイル(branch側・base 6206829との差分)

**計画§4どおり**: `backend/src/latch/settings.py`・`backend/src/latch/llm/gateway.py`・`backend/tests/unit/llm/test_llm_factory.py`・`backend/tests/unit/test_llm_stub_response_settings.py`・`backend/staging_tests/`(conftest+helpers3+test_s0〜s5の10ファイル)・`compose.staging.yaml`・`Makefile`・`docs/plans/M4/ws-3-report.md`・`docs/testassets/results/ws3-staging-result-20261003-231505.yaml`

**裁定Aによる追加**(2026-10-03スーパーバイザー指示): `backend/src/latch/testassets/generator.py`・`backend/src/latch/testassets/verify.py`・`backend/tests/unit/testassets/test_generator.py`・`docs/testassets/m4-seed.yaml`(構造変更コミット)・`docs/plans/M4/ws-3-plan.md`(§2.2の再実測値更新)

**コミットしていないもの**: なし(m4-seed.yamlは裁定どおり本branchでコミット済み)

## コミット一覧

```
64a945d feat: add llm_stub_jev_noul/score settings and stub response wiring
f8492e5 chore: add staging stub jev env and staging-reset/staging-test targets
f1f6b02 test: add staging harness base (conftest, wait, pushlog helpers)
9db02a3 test: add staging S0-S2 harness (WIP) and BLOCKED report on seed/runtime alcohol contradiction
e6cfe20 fix: seed generator bars drinking for underage users (supervisor ruling A)
f5edda8 test: finalize staging S3-S5 and runtime-semantics adaptations (full run green)
(本報告コミット: 報告書+証拠YAML)
```

## 補足(詰まった点・判断した点)

- **途中BLOCKEDと裁定**: シード15件(18-19歳×drinking×alcohol false)が実行時mapping(drinking⇒alcohol true強制)で422になり作成不可能 — §0規律どおり停止して報告し、**裁定A(シード生成器修正)**をいただいて解消。詳細は中間版報告の履歴と裁定内容のとおり
- **フル実行の反復**: 実装意味論の差分(L1の2経路・deadline=提案+2h以内・グループの3人組のみ・30分Bucket・embedding競合・sweeper batch排水)が実行を通じて判明し、合計7回のフル実行(staging-resetからの完全やり直し)で全グリーンに到達。各差分は全てハーネス側の適応で・製品コードへの変更は裁定Aのシード修正のみ
- **staging運用の発見**: (a)staging-resetにイメージbuildは含まれない(srcを焼き込むDockerfile)(b)api再作成後はnginx再起動必須(upstream IPキャッシュ)(c)Redisはボリュームを持たないためdown後に仮想Clockは未設定=実時間に戻る
- **staging DBの終了状態**: 最終runの検証データが残っている(仮想Clock=10-10 00:10)。次回利用時は`make staging-reset`から
