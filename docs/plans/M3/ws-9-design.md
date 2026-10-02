# M3 ws-9(G3ハーネス)設計メモ

- 作成: 2026-10-02(agent1)
- 前提: M3 ws-1〜ws-8マージ済み(マージ後main test-ci 1469 passed・frontend vitest 217 passed・
  alembic head=0006・プロトタイプ6画面はws-8マージ時点でG3条件4充足済み)。実行wave上の後続なし
  (M3最終単位)で並走なし——共有ci-dbのalembic_version取り合いは計画上なし
- 参照仕様: 12 §3 M3(完了条件G3) / 02 §4(#13〜#23) / 09 §3(グループLATCH 6検証) /
  10 §1・§3・§5 / 08 §2.6(FR-21)・§5 / 03 §2・§4〜§8(画面系は対応付けのみ)
- supervisor前提(2026-10-02指示): ①02#13〜#23の実施環境はG2解釈④(=ci環境で実施・staging再実行は
  M4-3)と同型と読み**ci前提で設計する**(G3判定時にユーザー確認=STATUS「G3判定の待ち事項」2)。
  ②FCM実送信はスタブのまま(同待ち事項1・人間領域)。FR-21の表示規制は通知本文のスタブ経路
  (StubPushSenderのcaplog記録)で検証可能な範囲で設計
- 本単位の実装は外部SDKのAPI事実に依存しない(新規SDK事実を記さない)。よって**context7での
  新規一次確認は不要**(2026-09-29指示は「設計に外部SDKのAPI事実を記すとき」)
- 大前提: **製品コード(src/)・マイグレーション・Makefile・composeへの変更はゼロ**。
  本単位は試験(ハーネス)と試験基盤の整備のみ(M2 ws-8のG2ハーネスと同型。ただしbreakerのような
  実装追加はなく、不足試験の新設とteardown修正のみ)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

G3完了条件4項のうち機械側の判定材料(条件1〜3)を「既存試験への対応付け+不足分の新設」で揃え、
ゲート承認へ判定資料を整備する。

1. **02#13〜#23 E2E整備**(02 §4・10 §3): 11項目を既存試験へ対応付け、不足(#13のmuted回答可能
   チェーン・#22の公開設定2値A/B比較・#23の座標非開示)を新設する
2. **グループLATCH 6検証**(09 §3): 6項目のうち既存4項目(集合生成・全員YES成立・部分成立禁止の
   no側・通知順序)を対応付け、不足2項目(期限確定・競合クローズ)+部分成立禁止のdefer側を新設する
3. **追加試験**(10 §3): D-10・D-23・FR-21・FR-17の4項目のうち、FR-17の自己重複集合
   (グループ側)の統合試験を新設し、他3項目は既存試験へ対応付ける
4. **試験基盤の恒久対策**: STATUS ws-1節の観察(m2ws1-/m2ws2-系users約12件/回のteardown漏れ蓄積。
   「ws-9かM4での恒久対策候補」)を本単位で解消する(§2.4)
5. **条件4(プロトタイプ6画面)**: 完了済み(STATUS ws-8マージ記録)。本単位は検証手順として
   frontend vitestの再実行(217 passed)を証拠化するのみ

### 1.2 引用確定値(節番号つき)

**A. 02#13〜#23の検証方法と合格証拠(02 §4マッピング表・原文)**

| # | 検証方法(02 §4) | 合格証拠(02 §4) |
|---|---|---|
| 13 | 閾値超過の候補を発生させる(お知らせ設定がproposals_onlyのIntentとmutedのIntentの双方を含める、v0.3) | 通知の到達記録。mutedのIntentでは通知ドライラン記録に提案通知が出力されないこと、latches.statusがproposedへ遷移しホームのLATCH候補から回答可能なことを確認する(v0.3) |
| 14 | 提案に対して両ユーザーが回答操作する | 回答の記録と状態遷移 |
| 15 | 両者YES後の状態と解放情報を確認する | matched遷移とチャット解放 |
| 16 | 期限経過後の提案状態を確認する | 提案がexpiredで閉じる |
| 17 | 成立前後で相手情報の可視範囲を比較する | 成立前に詳細が見えず、成立後に解放される |
| 18 | 3〜4人のIntentセットで必要人数のYESを揃える | 必要人数の matched 成立 |
| 19 | 成立LATCHでメッセージを送受信する | 双方向の送受信記録 |
| 20 | ブロック設定後に一致Intentを配置する | 候補が生成されない |
| 21 | 提案画面から通報操作する | 通報の受付記録 |
| 22 | 提案画面の表示情報を01第21節の最小化規則と照合する。v0.3で公開設定2値の分岐を追加 — 同一候補をvisibility=hidden_until_matchとsummary_onlyの双方で配置し、提案画面の内容を比較する | raw_text相当・NG条件が表示されていない。hidden_until_matchでは条件サマリも表示されず(「条件が合う候補があります」・一致度・回答期限のみ)、summary_onlyでは条件サマリのみ表示される。いずれも名前・プロフィールは成立まで表示されない |
| 23 | 提案画面の位置表現を確認する | 座標精度の値が表示されない |

**B. グループLATCH 6検証(09 §3・原文)**

> 02第4節#18を、06 D-06の確定内容(全員YES必要・部分成立なし・貪欲法による集合選択・aggregate降順の1集合通知)に基づき補完する。

| 検証項目 | 検証方法 | 合格証拠(09 §3) |
|---|---|---|
| 集合の生成 | 参加者にmin>=3のIntentを含む3〜4人セット(同一時間Bucket・地域・カテゴリ)を配置する | group_candidatesに生成された集合が全員の[min,max]条件を満たす |
| 全員YESでの成立 | 全員がyesと回答する | matched遷移・チャット解放 |
| 部分成立の禁止 | 一部がnoまたはdefer、残りがyes | rejectedで閉じ、部分成立しない |
| 期限時の確定条件 | 全員YESが揃わないままresponse_deadlineを経過させる | expiredで閉じる(3人YESでも成立しない) |
| 通知順序 | 同一Poolから複数の成立可能集合を発生させる | aggregate_score降順の最上位1集合のみproposed、他はPoolに残留 |
| 競合クローズ | あるIntentが属する2つの提案を用意し、片方を成立させる | もう片方はcancelledで閉じる |

**C. 追加試験(10 §3・原文。「両仕様は次のとおり本書の追加試験としてstaging(機能検証フェーズ)で実行する」)**

- **D-10(#27・飲酒Intentの年齢制限)**: 18〜19歳のテストユーザー(第2節)による
  alcohol_involved=trueのIntent作成が拒否されること(422 UNDER_AGE)、および
  alcohol_involved=trueのIntentが20歳未満ユーザーとの候補に現れないこと(Layer 1)を確認する。
  判定規則の精度面は第2節の飲酒判定専用入力セット(09 v0.3第4.3節のゲート: recall 100%・
  precision下限90%)で検証する
- **D-23(成立後のブロック適用)**: matchedのLATCHで一方がブロックを設定した際、そのユーザーの
  進行中の提案がcancelledで閉じること、当該LATCHのチャットが読み取り専用化され新規送信が不可に
  なること、表示が「このチャットは利用できません」であることを確認する
- **プッシュ通知の表示規制(FR-21、08 第2.6節)**: 閾値超過の候補を発生させ、通知ドライラン記録
  (第1節)のプッシュ本文が汎用文「LATCH候補があります。詳細はアプリでご確認ください」であること、
  および条件サマリ(人数・時間帯・地域名・カテゴリ・予算)がプッシュ本文に含まれないことを確認する。
  飲酒カテゴリ(drinking)の提案でも様式が同一であることを含める。summary_onlyの提案については、
  アプリ内通知(お知らせ)・提案画面に条件サマリが表示されること(03 第4節)を、
  hidden_until_matchの提案については、お知らせにも提案画面にも条件サマリを表示しないこと
  (02 第3節D-03・05のproposal生成分岐)も併せて確認する(v0.3)
- **機能試験の配置ケース(FR-17・10 §3)**: ①自己ペアの除外——同一ユーザーの2 Intent(同一時間帯・
  地域・カテゴリ、embedding上の類似も高い)を配置し、いずれのEvent処理でも当該ペアが
  match_candidatesに生成されないこと、提案通知が発生しないことを確認する(06 第2節のLayer 1自己除外)。
  ②自己重複集合の除外——同一ユーザーのIntentを2つ含むような3〜4人集合の構成要件を作り、
  group_candidatesに当該集合が生成されないことを確認する(06 第7節のHard互換条件・05 第3節の
  集合内user_id一意制約)

**D. 実施環境と証拠の規定(10 §1・§3・§5)**

| # | 確定値 | 出典 |
|---|---|---|
| 0 | **G3完了条件(12 §3 M3・原文)**: ①02#13〜#23がstagingでグリーン(#22は公開設定2値分岐・#13はmuted含むドライラン確認。10 第3節) ②グループLATCHの6検証がグリーン(集合生成・全員YES成立・部分成立禁止・期限確定・通知順序・競合クローズ。09 第3節) ③追加試験がグリーン: D-10年齢制限・D-23成立後ブロック・プッシュ表示規制(FR-21)・自己ペア除外(FR-17)(10 第3節) ④プロトタイプ6画面が03に従い実装済み(実装基準はprototype側を正とする。00 運用ルール6) | 12 §3 M3 |
| 1 | ci=最小構成(常設。API 1・Worker 1・DB共用)で自回帰の機能試験(毎コミット)。LLM呼び出しはLLM Gatewayのテストモード(スタブ)で決定的に。**FCMはドライラン(送信内容を記録のみ)とし、通知の到達と本文の表示規制(FR-21)は記録で検証する** | 10 §1 |
| 2 | 10 §3の振り分け: ci(毎コミット)=#1〜#4、#9、**#14、#20〜#25**、#27/ staging=#5(スモーク)、#6〜**#13**、**#15〜#19**、#26、#27(セット共通)。#22は公開設定2値の分岐確認を含める。#13はmutedのIntentのケース(通知送信なし・proposed遷移・ホームのLATCH候補からの回答可。通知ドライラン記録で通知0件を確認)を含める | 10 §3 |
| 3 | 機能試験27項目の合格基準の出典は02 §4マッピング表、証拠=**API応答・DB記録・通知ドライラン記録**。証拠は試験ごとにdocs/testassets/results/へ試験ID・実施日・環境・入力条件・合否とともに保管。CI・stagingの機能試験は自動化しリリース前ゲートとして全項目グリーンを必須とする | 10 §5 |
| 4 | G3完了条件4項は引用#0のとおり。「02#13〜#23がstagingでグリーン」だが、staging構築はM4-1(12 §3)でありM3時点で存在しない——G2解釈④と同型に**ci環境で実施してG3証拠とする**(supervisor前提①。G3判定時にユーザー確認=STATUS待ち事項2) | 12 §3 M3・G2時確認事項④ |
| 5 | 時刻参照はすべてClock経由(FakeClock注入で実時間待ちを排除)。期限系はClock操作/DB値操作で再現 | 10 §1・C2 |

### 1.3 既存試験資産との接続(2026-10-02 main実査)

G3条件1〜3の過半はws-1〜ws-8で実装済み試験がすでに満たす。対応の実査結果(試験IDはmain現状):

1. **回答・成立系(02#14・#15・#17)**: test_latches_api test_1(双方YES matched)・test_2(no/defer
   rejected)・test_3(二重回答409)・test_16(成立後のみparticipants解放)・test_15(一覧は
   candidate除外)
2. **期限系(02#16)**: test_expiry_batches test_2(proposed×deadline/expires切れのマトリクス×
   candidate挙動)・test_3(期限切れ直後の回答409)
3. **グループ回答(02#18)**: test_latches_api test_9(3人: 2人YES未成立→3人目matched・noで即
   rejected)・test_10(4人・group_candidate_id保持)
4. **チャット(02#19)**: test_chat_attendance_api test_1(双方向ラウンドトリップ)・
   test_17(chat→attendance一気通貫)
5. **ブロック(02#20)**: test_matching_hardfilter test_5(双方向ブロックでHard Filter fail)・
   test_13(run_candidate_retrieval経由で候補不生成)
6. **通報(02#21)**: test_safety_api test_8(受付サイクル)・test_9(latch参加性検査)・
   test_10(案X=reportee_id省略時のlatch_idサーバ解決——「提案画面から通報」の経路)
7. **通知(02#13の下地)**: test_notifications_api test_1(push送信+お知らせ一覧)・
   test_3(muted側push 0件・notifications 0行・latches.status=proposed)・test_7a/7b(nearby)
8. **FR-21(プッシュ表示規制)**: test_notifications_api test_2(summary_only×2・hidden混在・
   drinkingの3ケースで本文がbyte同一・`PUSH_BODY_LATCH`完全一致=汎用文と条件サマリ不含の証明)・
   test_4c(hidden行のnotifications応答がheadcount+match_levelのみ)
9. **D-10(年齢制限)**: test_intents_crud_api test_4b(19歳の飲酒Intent作成422 UNDER_AGE)・
   test_matching_hardfilter test_7(19歳×20歳飲酒ペア双方向fail・20歳×20歳通過)・
   test_8(満年齢の誕生日当日境界)
10. **D-23(成立後ブロック)**: test_safety_api test_6(candidate/proposed/partial_accept→cancelled・
    matchedは維持+送信409 CHAT_READONLY・user_id=blockerイベント)・test_7(グループlatch・
    非参加提案は閉じない)・test_4(キャッシュ経路の409)・test_5(解除で送信復帰)。
    表示「このチャットは利用できません」はfrontend latch-chat.test.js(409でdisabled+文言)
11. **FR-17自己ペア(1対1)**: test_matching_hardfilter test_10(同一ユーザー2 IntentがHard・run
    両面で候補不生成)。自己重複集合はunit(test_group_engine「user_id重複メンバー→_INSERT_GROUP
    不呼出」+`i1.user_id <> i2.user_id`SQLピン)のみ——統合配置ケースは未実施(§2.3試験6で新設)
12. **6検証のうち4項目**: 集合生成=test_matching_groupengine test_1(3人E2E)・test_5
    (種min=4の4人確定・min=5はPool除外=全員の[min,max]条件)・test_11(純min=3起点)。
    全員YES=test_latches_api test_9/test_10。部分成立禁止(no側)=test_9。通知順序=test_6
    (D-06: メンバー重複2集合→aggregate上位1件のみproposed・他はcandidate残留→drainで回収)
13. **#22の部品**: test_matching_latchengine test_2(visibility分岐のproposal内容)・
    test_matching_groupengine test_7(集合版)・frontend latch-detail.test.js(表示分岐=
    time_summaryキー有無)。**「同一候補を双方で配置して比較する」一体の試験は未実施**
14. **#23**: area_name(geo中点の逆転ジオコーディング)実装済み・proposal構造ピン(unit:
    LatchIntentInputsのdataclasses.fields検証=格納禁止フィールド不在)。**API応答レベルの
    「座標的値が存在しない」否定的検査は未実施**
15. **teardown基盤**: field fixture(FK順完全削除・prefix分離)・BASE_HOURS=120(now+5日統一)・
    StubPushSender+caplog→`latch.notifications`記録(通知ドライラン記録の実体)・make test-ciの
    purge-match-sub——いずれもM2 ws-8〜M3 ws-3の確立資産をそのまま再利用

### 1.4 スコープ外(後続・人間領域へ渡すもの。本単位では作らない)

1. **staging環境の構築と27項目フル実行** — M4-1/M4-3(12 §3)。本単位はci環境(引用D-4)
2. **非機能試験(10 §4全系)・性能・負荷** — M4
3. **FCM実送信** — G3判定の待ち事項1(人間領域・Firebase資材)。本単位はスタブ経路のみ
4. **MLR日次集計・Observability** — M4-5(09 §2.1の集計実装はlatch_status_eventsが既に供給)
5. **02#24〜#26** — G3完了条件に含まれない。#24(記録値)はM2のK上限試験・test_7_record_execution
   _breakdownが、#25(YES/NO記録)はtest_13(calibration_records)が、#26(MLR)は上記4の集計で担う
6. **精度面の再実行(Parser/alcoholゲート)** — プロンプト変更がないため不要(09 §4.3は変更時のみ)
7. **m2ws1-/m2ws2-蓄積usersの既存分掃除** — 恒久対策(§2.4)とは別に1回限りの掃除SQLを検証手順に
   記載するのみ(実施はスーパーバイザー検証時)

## 2. 実装方式の選択と推奨

### 2.1 G3証拠の全体マップ(対応表)

**表1: 02#13〜#23(G3条件1)**

| # | 担当試験(新=ws-9新設) | 備考 |
|---|---|---|
| 13 | 既存: notifications test_1(proposals_only側の到達記録)・test_3(muted: push0・通知0・proposed)/**新: 試験1(muted回答可能チェーン)** | 合格証拠の「ホームのLATCH候補から回答可能」をmuted Intentで一気に実証(§2.3) |
| 14 | 既存: latches test_1/2/3 | 回答の記録と状態遷移 |
| 15 | 既存: latches test_1(matched)・test_16(解放情報)・chat test_1(チャット解放) | |
| 16 | 既存: expiry test_2/3 | 提案がexpiredで閉じる |
| 17 | 既存: latches test_16 | 成立前後の可視範囲比較 |
| 18 | 既存: latches test_9/test_10 | 必要人数のmatched成立(3・4人) |
| 19 | 既存: chat test_1/test_17 | 双方向の送受信記録 |
| 20 | 既存: hardfilter test_5+test_13 | ブロック後の候補不生成 |
| 21 | 既存: safety test_8/9/10 | 提案画面(案X)からの通報受付 |
| 22 | 既存: latchengine test_2・groupengine test_7・frontend latch-detail/**新: 試験2(公開設定2値A/B比較)** | 同一条件2組を実際のパイプラインでproposed化しAPI応答を比較(§2.3) |
| 23 | **新: 試験2に統合**(応答キー規定集合ピン=座標系フィールド不存在) | 位置表現はarea_name(地名)のみ(§2.3) |

**表2: グループLATCH 6検証(G3条件2・09 §3)**

| 検証項目 | 担当試験(新=ws-9新設) |
|---|---|
| 集合の生成 | 既存: groupengine test_1/test_5/test_11 |
| 全員YESでの成立 | 既存: latches test_9(3人)・test_10(4人)・chat test_1(チャット解放) |
| 部分成立の禁止 | 既存: latches test_9(no側)/**新: 試験5(defer側)** |
| 期限時の確定条件 | **新: 試験3**(4人組3人YES→deadline経過→expired) |
| 通知順序 | 既存: groupengine test_6(D-06上位1集合) |
| 競合クローズ | 既存: latches test_5/test_11(1対1)/**新: 試験4(グループ成立側)** |

**表3: 追加試験(G3条件3・10 §3)**

| 項目 | 担当試験(新=ws-9新設) |
|---|---|
| D-10年齢制限 | 既存: intents test_4b(422 UNDER_AGE)+hardfilter test_7/test_8(Layer 1) |
| D-23成立後ブロック | 既存: safety test_4/5/6/7+frontend latch-chat(CHAT_UNAVAILABLE_TEXT) |
| プッシュ表示規制FR-21 | 既存: notifications test_2(byte同一・汎用文)+test_4c(hidden行最小構造)+latchengine test_2(summary側表示)+frontend latch-detail |
| 自己ペア除外FR-17 | 既存: hardfilter test_10(1対1)/**新: 試験6(自己重複集合)** |

**G3条件4(6画面)**: 完了済(STATUS ws-8マージ記録)。検証手順でfrontend vitest 217 passedを再実行
し証拠とする(§4.2)。

### 2.2 新規試験のファイル構成 — 推奨: G3集約の単独ファイル(案A)

| 案 | 内容 | 評価 |
|---|---|---|
| **A(推奨)** | 新規1ファイル `tests/integration/test_g3_acceptance_e2e.py` に新設6試験を集約(M2 ws-8の`*_e2e.py`命名規約) | G3判定資料のトレース性が最高(対応表↔試験IDが1:1)。既存ファイルをほぼ触らないため影響範囲最小。basename一意(2026-10-02実査: 既存と衝突なし) |
| B | 既存ファイルへ分散追加(latchesへ競合クローズ等) | 文脈的近接だがG3証拠が散在し判定資料の参照が煩雑。既存ファイル改変の影響調査が増える |
| C | 2ファイル(表示系/グループ系)に分割 | M2 ws-8前例に近いが6試験規模では過分割。単一ファイルでも各試験は既存ファイル1本分と同程度の分量 |

案Aを採用。ヘルパー(_user/_intent/_structured/_seed_jev/_handle/_latch/_clock)は
test_notifications_api・test_latches_api・test_expiry_batchesの確立形式を自前定義で複製
(既存試験ファイルの流儀——共通化リファクタは既存試験への波及が大きく本単位外)。

### 2.3 新設6試験の構成(tests/integration/test_g3_acceptance_e2e.py)

共通規律: SUBJECT_PREFIX="m3ws9-"(実査: 衝突なし)・field fixture(FK順完全削除)・
BASE_HOURS=120(now+5日系の時間窓分離)・FakeClock(SystemClock().now())・pytestmark=integration。
パイプライン系(試験1・2・6)はrun_candidate_retrieval+_seed_jev+LatchEngine/GroupEngine.handleの
テストプロセス内直接構成(notifications/groupengine試験と同型)。Workerプロセスは立てない。

**試験1: #13 muted含むドライラン+回答可能チェーン**

- 配置: ユーザーA(Intent a・proposals_only)×ユーザーB(Intent b・notification_level=muted)。
  同一条件(時間・地域・カテゴリ)で閾値超過(jev 0.9/0.9)
- 実行: run_candidate_retrieval(a)→_seed_jev→LatchEngine.handle(a・StubPushSender注入)→
  GET /v1/latches(headers=B)→POST /v1/latches/{id}/response(yes・headers=B)
- 検証: ①push記録(caplog)はA側1件のみ・B側0件(=mutedのIntentで通知ドライラン記録に提案通知が
  出力されない)②notifications行もB側0行③GET /v1/latchesのB視点一覧に当該latchがstatus=proposedで
  含まれる(=ホームのLATCH候補)④Bの回答が200で受理されmy_response="yes"(=回答可能)。
  既存test_3との差分=③④(同試験はstatus=proposedのDB確認まで)

**試験2: #22 公開設定2値のA/B比較+#23 座標非開示**

- 配置: 同一structured(時間・地域・カテゴリ・予算)の2組——組1=a1,b1(双方summary_only)・
  組2=a2,b2(a2のみhidden_until_match・b2はsummary_only。分岐は「いずれかがhidden」で発動する
  ため相手はsummary_onlyでよい)。両組ともjev 0.9/0.9でproposed化
- 実行: 各組run_candidate_retrieval→_seed_jev→LatchEngine.handle→GET /v1/latches/{id}(各組の
  参加者双方の視点)
- 検証(#22): ①組1(summary_only)のproposalキー集合=={time_summary, area_name, headcount,
  category_primary, category_secondary, budget, match_level}(=条件サマリ: 人数・時間帯・地域名・
  カテゴリ・ペア予算+一致度)②組2(hidden)のproposal=={headcount, match_level}のみ(=「条件が合う
  候補があります」・一致度・回答期限のみ——一致度=match_level・回答期限=response_deadlineは
  proposal外のトップレベルフィールドで双方に存在)③キー集合の差分==条件サマリ5情報のみ④
  raw_text相当・NG条件(soft_constraints文言)・表示名・プロフィールに対応するフィールドが
  両組の応答全体に存在しない
- 検証(#23): ⑤応答トップレベルのキー集合が規定集合(id/status/response_deadline/expires_at/
  created_at/completed_at/proposal/is_group/my_response/remaining_responses。proposed詳細は
  participants/time_summary/area_nameがNULL)と完全一致——geo座標(緯度経度・座標精度に相当する
  数値フィールド)が構造上存在しないことの否定的証明。位置表現はproposal.area_name(逆転
  ジオコーディングの地名文字列)のみ
- フロント表示の分岐(time_summaryキー有無での出し分け)は既存frontend latch-detail.test.jsが
  担う(対応表に記録)。実機ブラウザE2Eは作らない(§2.6-3)

**試験3: 6検証・期限時の確定条件(3人YESでも成立しない)**

- 配置: 4ユーザー×4 Intent(min=4想定の4人組・test_latches_apiの_latch直接INSERT流儀で
  status=proposed・response_deadline=未来)
- 実行: 3人がyes回答(→partial_accept・remaining_responses=1)→response_deadlineをDB直書きで
  過去へ(test_expiry_batches test_3と同一手法)→ExpirySweeper(FakeClock).run_once
- 検証: ①run_onceが当該latchを処理②status==expired(matchedにならない=3人YESでも成立しない)
  ③latch_status_eventsに(partial_accept→expired・user_id=NULL=システム起因)④4人目の回答は
  409。FakeClockを進めないためクローズ検知drainは不発(test_2と同型の docstring で明記)

**試験4: 6検証・競合クローズ(グループ成立側)**

- 配置: 3人グループlatch(proposed・i1,i2,i3)+i2を共有する1対1latch(proposed・{i2,iX}・iXは
  外部ユーザーU4のIntent)。「あるIntent(i2)が属する2つの提案」——競合クローズの対象抽出は
  `intent_ids && member_ids`(同一Intent含有・store._SELECT_CONFLICTING_LATCHES実査)のため
  共有はIntent単位で作る(test_11と同一構成原理のグループ成立側)
- 実行: グループ3人yes→matched
- 検証: ①グループlatch==matched・i1〜i3==matched(05 §6 Intent遷移)②1対1latch==cancelled
  (=もう片方はcancelledで閉じる)③latch_status_events(1対1側: proposed→cancelled・
  user_id=NULL=システム起因)④iXはactiveのまま(閉じるのはi2を含む提案のみ)——
  test_11(1対1×1対1)のグループ成立側対になる

**試験5: 6検証・部分成立禁止(defer側)**

- 配置: 3人グループlatch(proposed)
- 実行: 2人yes(→partial_accept)→1人defer
- 検証: 応答200でlatch==rejected(即時・部分成立なし)。09 §3の「一部が**noまたはdefer**」の
  defer側(no側は既存test_9)

**試験6: FR-17・自己重複集合の除外(統合配置ケース)**

- 配置: 同一ユーザーUのIntent 2つ(種s=min(3,4)・s2=同条件)+別ユーザーV・WのIntent各1
  (全員同一時間Bucket=now+5日・同地域・同カテゴリ・participants=(2,4)系)。groupengine試験と
  同一のPool構成手順
- 実行: GroupEngine.handle(s)
- 検証: ①group_candidatesに{s,s2}を同時に含む行==0件(=同一ユーザーのIntentを2つ含む集合が
  生成されない)②対照: {s,V,W}(Uの2つ目を含まない3人集合)は生成される——全員の[min,max]に
  3が∈のため成立し、検証環境が通過状態であることを保証する③sとs2のペア行(match_candidates)
  が生成されない(`i1.user_id <> i2.user_id`の実効——1対1側は既存hardfilter test_10担当の
  ためこちらはグループ経路の確認)

### 2.4 m2ws1-/m2ws2- users teardown漏れの恒久対策(STATUS ws-1節の観察)

- 現象: test_events_pipeline.pyとtest_embedding_pipeline.pyのuser_env fixtureが
  match_events/intentsを削除するが**users行を削除しない**——test-ci 1回あたり約12件蓄積
  (M2由来・ws-1検証時に400件掃除。「ws-9かM4での恒久対策候補」)
- 対策: 両fixtureのteardown末尾へ `DELETE FROM users WHERE id = :uid` を1行追加(FK順として
  intents削除の後)。usersはauth_*系テーブルから参照されないため追加削除は不要
- 代替(不採用): 検証手順の掃除SQLで毎回掃除——恒久でなく、うっかり蓄積が再発する
- 効果実証は検証手順5(§4.2): test-ci後にm2ws1-%/m2ws2-%のusers==0件(既存蓄積分は事前掃除1回)

### 2.5 G3充足マップ(本単位の成果物→G3判定)

| G3条件(12 M3) | 担当 |
|---|---|
| ①02#13〜#23グリーン | §2.1表1(既存対応付け+新設試験1・2)。ci環境・スタブLLM(supervisor前提①) |
| ②グループ6検証グリーン | §2.1表2(既存+新設試験3・4・5) |
| ③追加試験グリーン(D-10・D-23・FR-21・FR-17) | §2.1表3(既存+新設試験6) |
| ④プロトタイプ6画面 | 完了済(STATUS ws-8)。frontend vitest再実行217 passedで証拠化 |

### 2.6 採用しないもの(YAGNIによる切り捨て一覧)

1. **新規makeターゲット(g3-gate等)** — G3証拠はtest-ci+frontend vitestで足りる。g1/g2-gateが
   必要だったのは実API・実課金の分離のためで、G3にその必要なし
2. **テスト資産YAMLの新設** — G2のgoldsetと違いG3の入力はコード内fixtureで完結する
3. **実機ブラウザE2E(Playwright等)** — 02#22の「提案画面の内容」はAPI応答+frontend vitest
   (表示分岐の単体試験)の組で証拠化。新しいテストスタック導入はM3最終単位で行う規模でない
   (10 §5の証拠定義=API応答・DB記録・通知ドライラン記録の範囲内)
4. **Worker経由の一気通貫試験の追加** — 試験1・2・6で観察したいのはLayer 5・GroupEngine・
   API応答の内容で、イベント経路の実証はM2 ws-8試験8・M3 ws-2試験7が既に持つ。エンジン直接
   構成で十分かつ決定的
5. **#24〜#26・MLR・非機能の試験** — §1.4のとおりM4・G3条件外
6. **既存ヘルパーの共通モジュール化** — 試験ファイル間複製は既存流儀。リファクタは既存
   100超試験へ波及し本単位の目的外

## 3. ファイル構成

### 3.1 作るもの(新規ファイル)

| ファイル | 内容 |
|---|---|
| backend/tests/integration/test_g3_acceptance_e2e.py | G3受け入れE2E 6試験(§2.3)。m3ws9-プレフィックス・field fixture(FK順teardown)・ヘルパー自前定義 |

### 3.2 触るもの(既存ファイルへの変更)

| ファイル | 変更 |
|---|---|
| backend/tests/integration/test_events_pipeline.py | user_env teardownへusers削除1行(§2.4) |
| backend/tests/integration/test_embedding_pipeline.py | 同上 |

### 3.3 触らないもの(明示)

- backend/src/** — 製品コード変更ゼロ(breakerのような実装追加なし。不足は試験のみ)
- alembic/(head=0006不変)・Makefile・compose.yaml・docker/
- frontend/**(本単位は変更なし。vitest再実行は検証手順のみ)
- docs/testassets/(G3新規資産なし。証拠YAMLはtest-ciの実行結果と本設計§2.1対応表で担い、
  ゲート証拠保管(docs/testassets/results/)はG3判定時にスーパーバイザーがtest-ciログ等で実施)
- 既存試験ファイル(test_latches_api等)への追加変更なし(対応付けのみ)

## 4. テスト方針

### 4.1 新設integration試験の規律(計画書§0へ引き継ぐ制約)

1. **収集と実行の分離**(ws-5規律): 試験ファイルは実行不要の構成で書く。実行は
   スーパーバイザー検証のmake test-ciのみ(agent3はunit相当の構文確認まで)
2. **basename一意**(運用ルール5): test_g3_acceptance_e2e.pyは2026-10-02実査で既存と衝突なし。
   計画書レビュー時にも機械チェック(`find backend/tests -name "test_*.py" | ... | uniq -d`が空)
3. **DB干渉対抗策**: m3ws9-プレフィックス+FK順teardown(field fixtureをnotifications流儀で複製・
   latch_status_events→calibration_records→notifications→latches→match_candidates→
   group_candidates→match_events→intents→users)。時間値はnow+5日系(BASE_HOURS=120)で統一し
   既存試験(catch-up対象=expires_atから2時間以内等)と干渉させない。期限切れ再現はDB値操作
   (deadline過去化)で実時間待ちを作らない
4. **ピン試験への影響**: 製品コード・設定を変えないため既存ピン(test_settings・
   test_rate_limit_wiring・test_llm_factory等)は無影響。計画書レビューで追随访問を明記すること
5. **試験数の基線**: main現状=unit 1215+integration 254でtest-ci 1469。新規6試験で
   **test-ci期待値1475**(integration 260)。frontend 217不変。計画書で機械検査すること

### 4.2 検証手順(報告書への明記用)

1. `make lint && make test` — unit 1215 passed(不変)
2. `docker compose build api worker` — 運用ルール4(製品コード不変でも形式どおり)
3. `make test-ci` — **1475 passed**(=1469+6)・purge-match-sub実行をMakefile出力で確認
4. `cd frontend && npm test` — 217 passed(G3条件4の証拠化・再実行)
5. test-ci後の残存確認: m3ws9-系は従来手順+**m2ws1-%/m2ws2-% users==0件を確認**
   (§2.4の効果実証。1回目は既存蓄積分の掃除SQLを先行実行)
6. lint再実行(test-ciにlintが含まれないため・ws-4運用メモ)
7. basename一意確認・alembic head=0006不変・時刻参照がclock.pyのみ(arch test)
8. G3判定資料への反映: 本設計§2.1対応表+test-ci/frontendの実行ログをスーパーバイザーが
   STATUS「G3判定資料」節へ集約(G2判定資料と同型)

## 5. 未解決の論点(supervisor承認事項)

設計判断のうち、文言の読み替え・ゲート証拠の読み替えにあたるもの。docs内の根拠から自分で
答えたが、G3判定の証拠に関わるため承認を求める(回答は設計書の該当節へ確定記録する)。

1. **02#13〜#23・6検証・追加試験はci環境(compose常設・スタブLLM)で実施してG3証拠とする**
   (§1.2-D4): supervisor前提どおり。10 §3は#13・#15〜#19をstaging割り当てだが、staging構築は
   M4-1でありM3時点で存在しない(G2解釈④と同型)。staging再実行はM4-3。FCM実送信も待ち事項1の
   ためスタブ経路。**G3時ユーザー確認事項(STATUS待ち事項2)として記録を残す**
2. **新設試験はtest_g3_acceptance_e2e.pyへ集約**(§2.2案A): G3判定資料のトレース性を優先。
   既存ファイルへ分散しない
3. **#22「提案画面の内容を比較」の観測レイヤー=API応答+frontend vitest**(§2.3試験2):
   02#22の合格証拠は表示情報の内容であり、API応答のキー集合比較(proposal内容のA/B)とフロント
   表示分岐の単体試験(既存latch-detail.test.js)の組で証拠化する。実機ブラウザE2Eは新規
   スタック導入となるため作らない(10 §5の証拠定義の範囲内)。#23も同様に応答キー規定集合の
   一致で「座標精度の値が表示されない」を構造的に証明する
4. **競合クローズ(グループ)の構成=グループ成立×1対1閉塞**(§2.3試験4): 09 §3の文言は
   「あるIntentが属する2つの提案を用意し、片方を成立させる」——成立側をグループlatchとし
   もう片方を共有Intent(i2)を含む1対1latchとする構成で文言を満たす(競合クローズの対象抽出は
   Intent含有・§2.3実査)。グループ×グループにしない(group_candidatesを2つ実INSERTする手間に
   対し証拠の増分がない)
5. **期限時の確定条件=4人組の3人YES**(§2.3試験3): 09 §3の「3人YESでも成立しない」を
   必要人数4に対する3人YES(partial_accept)で直接実装する。3人組では3人YES=成立となるため
   4人組が文言に忠実
6. **m2ws1-/m2ws2- teardown恒久対策を本単位に含める**(§2.4): STATUS ws-1節が「ws-9かM4での
   恒久対策候補」と記録。変更はテストコード2行で「E2E整備」の範囲内と読む
7. **#24〜#26・MLR集計・非機能試験はG3判定資料に含めない**(§1.4): 12 M3の完了条件4項に
   含まれないものはM4へ回す

その他の設計判断(単独ファイル構成・ヘルパー自前定義・新規makeターゲットなし・Worker経由
試験の不採用)はdocs根拠と§2のトレードオフ記述のとおり。
