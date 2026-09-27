# Requirements Review

- 対象文書: `01 LATCH_requirements_v0.1.md`(LATCH 要件定義書 v0.1)
- レビュー実施日: 2026-09-26
- 方式: Herdr 上の独立レビューAgent 4名(A: Requirements Quality / B: Product・Domain / C: NFR・Security・Operations / D: Adversarial・Consistency)+ Lead による統合・判定・修正 + Second-pass Agent E による再レビュー
- このリポジトリに関連資料は存在せず、対象文書のみを根拠に判定した

## Executive Summary

- Critical: 5件(F-01〜F-05)
- Major: 24件(初回19件 + Second-pass 5件)
- Minor: 13件(初回8件 + Second-pass 5件)
- 修正済み(Fixed / 部分Fixed): 35件
- 未決事項(Decision Required): 24件(D-01〜D-24、要件定義書 第26節に集約)
- Rejected(誤検出・対象外): 5件

総括: コンセプト・原則の明文化の水準は高い一方、(1) スコープ記述の内部矛盾(Event種と外部連携、安全要件と受け入れ条件)、(2) 判定に必要なデータモデルの欠落(友人関係・所属・年齢)、(3) 成立・期限・通知ライフサイクルの数値規定の欠如、(4) スコアリング中核(MutualScore合成式・Calibration係数C)の未確定、が主要な弱点だった。このうち文書内の根拠から確定できるものは修正し、プロダクト・ポリシー判断を要するものは第26節の未決事項一覧(D-01〜D-23)に集約した。

## Findings

severity は統合後に Lead が再評価した値(元Reviewerの判定と異なる場合は備考に記す)。Status の Fixed は Safe Fix または Derived Fix(導出内容は備考に記録)。

### Critical

#### F-01 visibility(friends_only / friend_of_friend)の判定に必要な友人関係データと機能が存在しない

- Severity: Critical
- Category: 完全性 / データモデル
- Source section: 第6節・第9節・第7節・第17節
- Finding: visibilityはHard Constraintとして「コードとDBで厳密に落とす」対象でMVPユースケース(「友人限定なら参加したい」)にも登場するが、友人関係を表すEntityも管理機能も実装範囲にも存在しない。3値の型(単一選択かboolean並列か)と、双方のvisibilityの交差判定規則も未定義。
- Risk: 第6節のユースケースが実装・検証ともに不可能。Hard Filter要件が確定しない。
- Resolution: (a) visibilityは public / friends_only / friend_of_friend から単一選択であることを明記(解釈固定)。(b) 友人関係データ・機能をMVPに含めるかどうかは文書から確定できないため未決事項化。
- Status: TBD(D-03)+ 部分Fixed
- Reviewer: A, B, D

#### F-02 Event定義(第5節・第11節)と第7節「外部連携を実装しない」の矛盾

- Severity: Critical(A/B: Major〜Critical, D: Major → Lead再評価でCritical)
- Category: 矛盾 / スコープ
- Source section: 第5節・第7節・第11節
- Finding: 店舗の価格変更・空席・在庫変更等の外部由来Eventが定義されているが、そのデータソースとなる外部連携(外部店舗在庫連携)は第7節で「実装しない」と明記され、直接矛盾する。MVPのMatch Event種が確定しない。
- Risk: Event Queue・MatchEventスキーマ・外部依存の実装量が確定せず、実装者・テスターの解釈が割れる。
- Resolution: 第5節・第11節に「MVPで発生するのは内部由来5種(作成・更新・削除・期限切れ、指定時刻の到来)。外部データに依存するEventは将来バージョンで追加」を明記。根拠は第7節の明示的な実装除外リスト。
- Status: Fixed
- Reviewer: A, B, D

#### F-03 スコアリング中核の未定義(MutualScore合成式・Calibration係数Cの初期値と値域)

- Severity: Critical(D: Critical, A/B: Major → Lead再評価でCritical)
- Category: 曖昧性 / 検証可能性
- Source section: 第13節・第10節
- Finding: P(A accepts B) が would_a_accept_b の出力か7軸の合成か不明で、残り5軸(purpose_fit等)の扱いも未定義。Cの値域・初期値もなく、閾値0.80との比較の意味が実装ごとに変わる(C>1を許す実装も文書違反にならない)。
- Risk: 通知判定の唯一の数値基準が確定せず、「L ≥ 0.80 で通知する」という受け入れテストすら書けない。
- Resolution(Derived Fix として記録): MVPでは P(accepts) = would_*_accept_* の判定値とし、残る5軸はJev内部の判断材料としてMutualScoreに直接寄与させない。Cの値域は(0, 1]、Calibrationデータ蓄積前の初期値は1、Lの値域は[0, 1]とする。導出根拠: 閾値0.80との比較が機能するにはL∈[0,1]が必要であり、データ蓄積前に補正をかけない(C=1)のが自然な初期状態。Hは判定時点のHard Constraintの再検証としても機能すると明記。
- Status: Fixed(Derived)
- Reviewer: A, B, D

#### F-04 グループLATCHの成立条件の未確定と例のMVP上限矛盾

- Severity: Critical(D: Critical, A/B: Major)
- Category: 矛盾 / 完全性
- Source section: 第15節・第10節・第17節・第25節・第7節
- Finding: (a) 第15節の例「A+B+C+D+E」は5人で同節の「MVPでは最大4人」と矛盾。(b) partial_acceptの確定・失効条件が未定義。(c) MatchCandidateが2 Intent固定でグループ候補を表現できない。(d) 第7節「4人程度」が「最大4人」と揺れている。(e) 人数条件が自分自身を含むか不明。
- Risk: 第25節「最大4人のグループLATCHを成立できる」の受け入れテストが主観的になる。
- Resolution: (a)(d)(e)を修正(例の5人集合を対象外と明記、「最大4人」に統一、participantsのmin/maxは提案者自身を含む人数と定義)。(b)(c)は体験設計を含むため未決事項化。
- Status: 部分Fixed + TBD(D-06)
- Reviewer: A, B, D

#### F-05 外部LLM送信時のセンシティブデータの扱いが未規定

- Severity: Critical
- Category: Security / Privacy
- Source section: 第21節・第9節・第13節・第19節
- Finding: raw_text(潜在意思・転職・売買意図を含む)を外部LLM・Embedding APIへ送信する構成ながら、外部送信に関する要件(同意・プロバイダ選定・学習利用・送信範囲)が文書全体に存在しない。第21節「通常のSNSより厳しい」宣言と実態が接続されていない。
- Risk: プライバシー宣言の根本的な反故、第三者提供の法的リスク。
- Resolution: 送信範囲の最小化と送信記録を第21節に追加。同意・学習利用制限・プロバイダ選定基準は未決事項化。
- Status: 部分Fixed + TBD(D-14)
- Reviewer: C

### Major

#### F-06 同一Intentへの並行LATCH成立のレースコンディション

- Severity: Major(D: Critical → Lead再評価。第8節「他のLATCH成立による競合」から排他の意図は読めるため)
- Category: 並行性
- Source section: 第8節・第10節
- Finding: 1対1を望むIntentが複数の提案を同時に持ち得る場合の排他規定がなく、二重成立する実装が「双方YESで成立」の素直な実装として成立してしまう。
- Resolution: 「同一のIntentが同時に複数の成立済みLATCHに属することはない。あるIntentのLATCHが成立した時点で、そのIntentに関係する回答待ちの提案は競合として閉じる」を第8節に明記(Derived: 第8節競合の記述から導出)。
- Status: Fixed(Derived)
- Reviewer: D

#### F-07 回答期限が要件として一度も定義されていない

- Severity: Major
- Category: 完全性
- Source section: 第8節・第24節・第25節
- Finding: 回答期限が不成立経路・A/B対象に登場するのに、値・設定規則・Intent期限との関係が存在しない。第25節「期限切れ処理」が回答期限とIntent期限のどちらを指すかも不明。
- Resolution: 回答期限の概念定義(Intentのexpires_atとは別)と第25節の表記を明確化。初期値・導出規則は未決事項化。
- Status: 部分Fixed + TBD(D-05)
- Reviewer: A, B, D

#### F-08 「今回は見送る」の意味論・再提案規則・通知頻度制御の未定義

- Severity: Major
- Category: 完全性 / UX
- Source section: 第10節・第4節(仮説4)・第8節
- Finding: 見送りがNOか一時的抑制か不明。同一ペアへの再提案規則、複数候補への同時YESの優先規則、通知頻度上限のいずれもない。仮説4(通知頻度を抑えれば反応率が上がる)の検証装置そのものが未定義。
- Resolution: 未決事項化(D-07・D-08)。
- Status: TBD
- Reviewer: A, B, D

#### F-09 会社関係者等のNG条件の判定データが存在しない

- Severity: Major
- Category: 完全性 / 矛盾(原則2と)
- Source section: 第9節・第5節・第17節
- Finding: 「会社関係者」をコードとDBで判定するには所属情報と照合方法が必要だが、User Entityに存在しない。文書の典型例が文書の分類原則で処理できない。
- Resolution: 未決事項化(D-04)。第9節に未決である旨を明記。
- Status: TBD
- Reviewer: A, B, D

#### F-10 第22節安全要件と第7節実装範囲・第25節受け入れ条件の不一致(ミュート等)

- Severity: Major
- Category: 矛盾
- Source section: 第22節・第7節・第25節
- Finding: 第22節が「必須」とするミュート・年齢制限・スパム検知等が、第7節実装リストと第25節Safety受け入れ条件の双方に存在しない。「対応する受け入れ条件は第25節のSafetyにある」という自称とも矛盾。
- Resolution: 第22節を「第25節Safetyに対応するのは4項目。残りの受け入れ条件への包含は未決」と整合化し、未決事項化。
- Status: 部分Fixed + TBD(D-12)
- Reviewer: A, D

#### F-11 Mutual Latch Rate の分母の数え方が未定義

- Severity: Major
- Category: 検証可能性
- Source section: 第23節
- Finding: 「ユーザーへ提示したLATCH候補数」の数え方(提示=通知送信か画面表示か、1候補を2人に提示したときの数え方、グループの扱い)が未定義で、数値が実装ごとに倍違いになり得る。
- Resolution(Derived Fix として記録): 分母=当該期間に通知を送信したLATCH候補数(1候補1カウント、グループ候補も1候補、クローズ済みを除かない)、分子=必要人数全員がYESした候補数。A・B両Reviewerとも「分析設計として決定可」(確認不要)と判定。
- Status: Fixed(Derived)
- Reviewer: A, B

#### F-12 要求強度(MUST/SHOULD/MAY)の規約の欠如

- Severity: Minor(A: Major → Lead再評価。既に「目標」「必須」「絶対に」の語彙で強度は概ね区別されており、解釈分岐の実例が挙げられなかった)
- Category: 曖昧性
- Source section: 文書全体
- Finding: 要求の強制度を示す規約がなく、調整可能な初期値(閾値0.80)と絶対制約(ブロック非マッチ)が同じ書式。
- Resolution: 冒頭に用語規約(「必須」=第25節受け入れ条件対応、「目標」=運用調整値)を追加。第22節の「必須」表記はF-10の修正で整理。
- Status: Fixed
- Reviewer: A

#### F-13 Intent / LATCH状態機器の遷移トリガーと状態の意味が未定義

- Severity: Major
- Category: 完全性
- Source section: 第10節・第8節
- Finding: 状態の一覧のみで、遷移トリガー(candidate→proposed、rejected/expired/cancelledの使い分け、draft→active、completedの条件)が1つも書かれていない。
- Resolution(Derived Fix として記録): 主要な遷移と状態の意味を第10節に定義。draft→active(ユーザー確認保存)、candidate→proposed(閾値超過通知)、completed(対象時刻経過)等。ユーザーに提示するLATCHはproposed以降と固定(第10節「閾値を超えたら通知する」と仮説4の通知抑制から導出)。
- Status: Fixed(Derived)
- Reviewer: B, D

#### F-14 カテゴリがHard Constraint定義(第5節)に含まれず第12節と不一致

- Severity: Major
- Category: 矛盾 / 用語
- Source section: 第5節・第12節
- Finding: 第5節のHard Constraint列挙にカテゴリがないのに、第12節Layer 1の判定対象にカテゴリが含まれる。
- Resolution: 第5節の列挙に「カテゴリ」を追加。category.primaryの値の一覧列挙は詳細設計に委ねる(第6節のMVP対象「食事・飲み・軽いアクティビティ」が実質の範囲)。
- Status: Fixed(列挙の不一致解消)/ 一覧は対象外
- Reviewer: A

#### F-15 曖昧数量のHard Constraint変換規則・時間補完の不在、例示の不整合

- Severity: Major
- Category: 曖昧性
- Source section: 第9節・第10節
- Finding: 「5000円くらい」入力が構造化では「5,000円以下」、通知では「5,000円前後」と場面ごとに異なる解釈で例示される。終了時刻のない入力(「今日20時以降」)への「23:30」補完の出所も不明。
- Resolution: 通知例を「予算上限5,000円」に統一、確認UI例を「20:00以降」に入力に忠実化。丸め幅・デフォルト終了時刻の規則は未決事項化。
- Status: 部分Fixed + TBD(D-19)
- Reviewer: A, B, D

#### F-16 ユーザー削除とCalibrationデータ保持(独自データ資産)の矛盾

- Severity: Major
- Category: 矛盾 / Privacy / Data
- Source section: 第21節・第14節
- Finding: 削除時にIntent・回答・行動ログをどうするか(物理削除/匿名化/ログ残存)未定義。embedding(Vector DB)や未処理MatchCandidateを削除対象に含むかも不明。保持期間の規定もない。
- Resolution: 削除範囲(Intent本文・構造化データ・embedding・未処理MatchCandidate)を第21節に明記(Derived: 「削除できる」と「マッチング処理のみで利用」から導出)。Calibrationデータの匿名化保持可否と保持期間の詳細は未決事項化。
- Status: 部分Fixed + TBD(D-13)
- Reviewer: B, C, D

#### F-17 年齢制限の実体が不明(年齢データの取得手段・飲酒Intentの扱い)

- Severity: Major
- Category: Safety / 法務
- Source section: 第22節・第6節・第17節
- Finding: 「年齢制限」に言及するがUserモデルに年齢フィールドがなく、登録年齢下限・飲酒目的Intent制限のいずれも未定義。未成年の飲酒機会への関与は法的リスク。
- Resolution: 未決事項化(D-10)。
- Status: TBD
- Reviewer: A, B

#### F-18 位置情報の丸め粒度・ジオコーディング外部依存の未規定

- Severity: Major
- Category: Safety / Privacy / 依存管理
- Source section: 第22節・第9節・第18節
- Finding: 「位置情報の丸め」の粒度が未定義で「正確な位置情報を公開しない」の検証基準がない。地名→座標変換の手段と外部依存の棚卸しもない。
- Resolution: 未決事項化(D-11)。
- Status: TBD
- Reviewer: A, B

#### F-19 外部LLM API障害・遅延時の挙動(timeout・graceful degradation)の未定義

- Severity: Major
- Category: Availability / Reliability
- Source section: 第20節・第16節・第19節
- Finding: Jev・Intent Parserのtimeout、失敗時retry、失敗時に提案を出すか保留するかの方針がない。時間敏感なIntentでパイプライン停止は機能不全。
- Resolution(Derived Fix として記録): 「外部LLM呼び出しにはtimeoutを設定する」「失敗した候補は判定を保留して提案しない(誤った提案より提示見送りを優先)」を明記。障害継続時の縮退運転の内容は未決事項化。
- Status: 部分Fixed + TBD(D-15)
- Reviewer: C, D

#### F-20 Event処理失敗時のretry・隔離(dead letter)の要件がない

- Severity: Major
- Category: Reliability
- Source section: 第16節・第17節
- Finding: 重複防止(debounce・idempotency・version)はあるが、処理失敗時のretry回数・隔離・再処理の要件がない。毒イベントがQueue滞留全体を停滞させるリスク。
- Resolution(Derived Fix として記録): 「上限回数まで再試行し、失敗するイベントは失敗理由を保持して隔離」を第16節に追加。回数の具体的値は詳細設計に委ねる。
- Status: Fixed(Derived)
- Reviewer: C

#### F-21 LLMコストの予算上限と自動保護がない

- Severity: Major
- Category: Operations / Cost
- Source section: 第20節・第23節
- Finding: costを計測・KPI視認する規定はあるが、予算上限と超過時の自動保護(Jev縮小・停止、Cheap Judge以下で完結)の要件がない。異常系(Event洪水)への動的なブレーキがない。
- Resolution(Derived Fix として記録): 上限・alert・縮退運用の方針を第20節に追加。上限値は未決事項化。
- Status: 部分Fixed + TBD(D-16)
- Reviewer: C

#### F-22 Intent Parser(同期LLM処理)のlat要件が性能目標に含まれない

- Severity: Major
- Category: Performance
- Source section: 第20節・第8節・第9節
- Finding: 「Intent保存: p95 500ms」は保存処理のみと読め、Parser自身の目標時間・timeoutがない。登録体験は仮説1の検証対象そのもの。
- Resolution: 未決事項化(D-17)。timeoutを設定すること自体はF-19の修正で明記済み。
- Status: TBD
- Reviewer: C

#### F-23 認証・認可の要件が存在しない

- Severity: Major
- Category: Security
- Source section: 第7節・第19節・第22節
- Finding: アカウント機能とユーザー紐付けAPIがあるのに、認証方式・「本人以外がIntent・回答を操作できない」認可要件がない。「なりすまし対策」は認証基盤の代替にならない。
- Resolution(Derived Fix として記録): 「全APIは認証済みユーザーのみ。Intent操作は所有者本人のみ許可。公開範囲・ブロック判定はサーバ側で強制」を第22節に追加。認証方式は未決事項化。
- Status: 部分Fixed + TBD(D-21)
- Reviewer: C

#### F-24 ログ・計測・Analyticsへのセンシティブデータ混入の禁止規定がない

- Severity: Major
- Category: Security / Privacy
- Source section: 第20節・第21節・第23節
- Finding: 相手への表示最小化はあるが、自社内のログ・計測出力におけるraw_text・正確な位置・NG条件の扱いが規定されない。センシティブデータの典型的事故はログからの漏洩。
- Resolution(Derived Fix として記録): 第21節に「ログ・計測・Analytics出力にIntent原文・正確な位置・NG条件を含めない」を追加(表示最小化の内部システム適用)。
- Status: Fixed(Derived)
- Reviewer: C

#### F-25 バックアップ・リカバリの要件がない

- Severity: Major
- Category: Availability / Data
- Source section: (欠落)第20節
- Finding: 失われる対象が「ユーザーが預けた意思」で再作成不能、かつ第14節が正解データを独自データ資産と位置づけるのに、保全要件がない。可用性99.5%はデータ保全の規定ではない。
- Resolution(Derived Fix として記録): 「Intent DBとVector DBは定期的にバックアップし、復旧でVector DBを再構築できること」を第20節に追加。RPO等の数値は運用設計に委ねる。
- Status: Fixed(Derived)
- Reviewer: C

#### F-26 Embeddingモデルの版数記録と移行方針がない

- Severity: Major
- Category: Maintainability / Data
- Source section: 第18節・第12節
- Finding: どのモデル版でベクトル化したかの記録要件がなく、モデル変更時にどのIntentを再エンベディングすべきか判別できない(後付けが効かない)。
- Resolution(Derived Fix として記録): 第18節に「IntentにEmbeddingのモデル識別子と版を記録する」を追加。
- Status: Fixed(Derived)
- Reviewer: C

#### F-27 Intent削除APIとresume APIが存在しない

- Severity: Major
- Category: 完全性(API)
- Source section: 第19節・第5節・第21節・第10節
- Finding: Event種と削除権に「Intentの削除」があり、pause APIもあるのに、DELETEとresumeに相当するAPIがない。「削除はPATCHのstatus=cancelledで代用」「pauseは不可逆」という実装が成立してしまう。
- Resolution: `DELETE /v1/intents/{intent_id}` と `POST /v1/intents/{intent_id}/resume` をAPI一覧に追加(既存要件から直接導出)。
- Status: Fixed
- Reviewer: B, D

#### F-28 閾値未満候補の可視性・GET /v1/latchesの範囲・candidate/proposed境界の不明確さ

- Severity: Major
- Category: 曖昧性
- Source section: 第10節・第19節
- Finding: 通知は閾値超過のみだが、未満のMatchCandidateを一覧画面に見せる実装と見せない実装の両方が成立する。candidateとproposedの境界定義がない。
- Resolution(Derived Fix として記録): 第10節の状態定義で「candidate=候補生成時点、proposed=閾値超過で通知済み」「ユーザーに提示するLATCHはproposed以降」に固定(第10節の通知規定と仮説4の通知抑制から導出)。
- Status: Fixed(Derived)
- Reviewer: D

#### F-29 「実際に参加したか」(Calibration Ground Truth)の収集方法が未定義

- Severity: Major
- Category: 完全性 / 検証可能性
- Source section: 第14節・第27節・第25節
- Finding: 参加実績をどう取得するか(自己申告・アンケート等)が未定義で、Offline評価(Calibration Error・Brier Score)が実行できない。原則6が文書の根幹であるのに収集経路が不明。
- Resolution: 未決事項化(D-09)。第14節に未決である旨を明記。
- Status: TBD
- Reviewer: A

#### F-30 通知タイミング(同時/順次)と不成立時の相手への開示範囲が不明

- Severity: Major
- Category: UX / Privacy
- Source section: 第8節・第10節
- Finding: A・Bへの通知が同時か順次か不明。BがNO・無回答のときA側に何を見せるかの規定がない。
- Resolution: 未決事項化(D-20)。
- Status: TBD
- Reviewer: D

#### F-31 trust_scoreの用途未定義・draftのライフサイクル不明

- Severity: Minor(B: Minor)
- Category: 完全性(小項目)
- Source section: 第17節・第10節
- Finding: trust_scoreがいつ・どう更新され何に使うか不明でMVP機能範囲に現れない。draftがいつ作られ破棄されるかも不明。
- Resolution: trust_scoreは「MVPでは使用しない予約フィールド」と明記、draft→activeは状態遷移定義(F-13)で対応。
- Status: Fixed
- Reviewer: B

#### F-32 ユーザー単位のIntent大量更新によるJevコスト消費の抜け穴

- Severity: Minor
- Category: Abuse prevention
- Source section: 第22節・第16節・第11節
- Finding: 新規投稿の大量制限とdebounceはあるが、反復更新でMatch Eventを大量発生させK上限の総量を消費させる行為へのユーザー単位制限がない。
- Resolution(Derived Fix として記録): 第22節のプラットフォーム防護に「ユーザーあたりのIntent作成数・更新頻度の上限」を追加(既存のIntent大量投稿制限の自然な拡大)。
- Status: Fixed(Derived)
- Reviewer: C

#### F-33 可用性99.5%の測定定義(対象・除外)が不明確

- Severity: Minor
- Category: 曖昧性(NFR)
- Source section: 第20節
- Finding: 何の可用性か(API成功率かパイプライン継続か)、計画メンテナンスを除外するか不明。
- Resolution(Derived Fix として記録): 「計画停止を除くAPIリクエスト成功率99.5%以上」に明確化。
- Status: Fixed(Derived)
- Reviewer: C

#### F-34 通知チャネルと到達性の要件が不明確

- Severity: Minor
- Category: UX / Operations
- Source section: 第10節・第20節
- Finding: 通知手段(プッシュ/アプリ内)・許可の取得・許可されない場合のフォールバックが規定されない。「5秒以内」が送信か到達かも不明。
- Resolution: 未決事項化(D-18)。
- Status: TBD
- Reviewer: C

#### F-35 timezone・単位系の未規定

- Severity: Minor
- Category: 曖昧性
- Source section: 第9節ほか全文
- Finding: 「今日20時以降」のタイムゾーンが未規定(User Entityにtimezoneなし)。radius等の単位も未規定。
- Resolution(Derived Fix として記録): 冒頭に「本書の時刻表現は特に断らない限りJST」と明記(円・日本の地名のみを使う文書からの導出)。単位系は詳細設計に委ねる。
- Status: Fixed(Derived)/ 単位系は対象外
- Reviewer: D

#### F-36 第11節K上限と第12節ファネル図の数値不一致

- Severity: Minor
- Category: 整合性
- Source section: 第11節・第12節
- Finding: K上限(Vector 50 / Cheap Judge 20 / Jev 5〜10)と図の件数(20 / 5 / 2)が一致せず、実装者の混乱源。
- Resolution: 第12節に「図中の件数は規模感を示す例であり、上限は第11節のK上限に従う」を明記(両者は上限と通過結果の例として矛盾しない解釈の明示)。
- Status: Fixed
- Reviewer: D

#### F-37 天文館等の例示が初期対象地域の暗黙前提に見える

- Severity: Minor
- Category: assumptions
- Source section: 第26節・第9節ほか
- Finding: 例示が天文館に固定され、初期リリース地域は未決とあり、例示か前提か判別できない。
- Resolution: D-22に「本書の例示に登場する地名は特定の地域の予定を意味しない」を明記。
- Status: Fixed
- Reviewer: B

#### F-38 Intent Parserの異常系(必須欠落・期限自動補完)の未定義

- Severity: Major
- Category: 完全性
- Source section: 第9節・第25節
- Finding: 時間・場所・予算を含まない入力を拒否するか黙って受け付けるか、expires_at未指定時の扱い(必須か自動設定か無期限許可か)が不明。MVP対象(今〜数日)と無期限許可は不整合になり得る。
- Resolution: 未決事項化(D-19)。
- Status: TBD
- Reviewer: B, D

### Rejected(誤検出・対象外と判定)

#### R-1 MatchCandidateのペア順序規則(id正規化・ユニーク制約)

- Reviewer: D
- 判定理由: データモデルの実装規約であり、要件定義書の粒度を超える詳細設計。グループ表現の欠落(F-04)のみ要件レベルで扱う。

#### R-2 時間Bucketの帰属詳細(「20時以降」がどのBucketで評価されるか)

- Reviewer: D(補足)
- 判定理由: 第16節の時間Bucket設計の実装詳細。starts_atによる帰属は実装で一意に決まる。

#### R-3 通報調査用の監査ログ(スナップショット保持)

- Reviewer: C
- 判定理由: 正当な指摘だが、保持期間(未決)と絡む運用要件であり、MVP受け入れ条件への追加はD-12・D-13の決定後に検討すべき。次版で再検討。

#### R-4 時間イベント由来の再評価の提示までの時間上限(分単位)

- Reviewer: C
- 判定理由: Queue lag計測(第20節)とalert方針(F-21)で検知・対処の枠組みは確保済み。分単位の上限値は運用設計。

#### R-5 category.primary の値列挙の要件化

- Reviewer: A
- 判定理由: 第6節のMVP対象(食事・飲み・軽いアクティビティ)が実質の範囲を規定しており、enum列挙は詳細設計。第5節と第12節の定義不整合(F-14)のみ修正。

## Decisions Required

要件定義書 第26節の未決事項一覧(D-01〜D-23)に集約している。人間による判断が必要な主要項目(抜粋):

| ID | Question | Why it matters |
|---|---|---|
| D-03 | visibility(friends_only / friend_of_friend)に必要な友人関係データ・管理機能をMVPに含めるか | 含めなければ第6節のユースケース「友人限定なら参加したい」が実装できない。含めれば実装スコープが増える |
| D-04 | 会社関係者等、判定データを持たないNG条件の扱い(所属収集かSoft Constraint降格か) | 原則2(Hard Constraintはコードで判定)との整合。誤マッチは「通常のSNSより厳しい」宣言の信頼を損なう事故になる |
| D-05 | 回答期限の初期値と導出規則 | 成立率・キャンセル率・Mutual Latch Rateが期限設計に強く依存する。「今夜20時」の提案に長い回答期限は無意味 |
| D-06 | グループLATCHの成立確定条件(部分YES・通知順序・集合選択) | 第25節「最大4人のグループLATCH成立」の受け入れテストが主観のまま |
| D-07/D-08 | 「今回は見送る」の意味・再提案条件・通知頻度上限 | 仮説4(通知頻度を抑えれば反応率が上がる)の検証装置そのもの。「待つだけ」体験を壊す通知過多は解約直結 |
| D-09 | 参加実績(実際に参加したか)の収集方法 | Calibration Ground Truthの収集経路が無いと仮説3の検証が立ちいかない |
| D-10/D-11 | 年齢制限の内容・位置情報の丸め粒度 | 未成年の飲酒機会への関与(法的リスク)と、最も機微性の高いデータの保護水準 |
| D-12 | ミュート等の安全要件をMVP受け入れ条件に含めるか | 現状、第22節と第25節のMVP完了定義が不一致 |
| D-13 | 削除・退会時のCalibrationデータの扱い(匿名化保持か) | プライバシー宣言と「独自データ資産」の衝突。後からの変更はデータ設計に遡る |
| D-14 | 外部LLM送信の同意・学習利用制限・プロバイダ選定基準 | 潜在意思の第三者提供に当たる。プロバイダ選定とアーキテクチャを後から制約する |
| D-15/D-16 | LLM障害時の縮退運転・コスト上限値 | 時間敏感な提案の価値を守るか、コストを守るかの優先順位付け |

推奨デフォルト(根拠が明確な場合のみ):

- D-03: MVPを1対1・public中心で検証するなら、visibilityを一旦 public / friends_only の2値に縮小し、友人関係は最小の相互承認のみ実装する案が、第6節ユースケースを満たす最小構成となる(ただしスコープ増は否めないため判断が必要)
- D-16: 当初は Jev 実行回数ベース(金額でなく)の上限を推奨。第11節のK上限と同じ次元で運用開始前に調整できる

## Second-pass Review

修正後の要件定義書を、修正前のレビュー結果を見せずに新しいAgent E(second)にレビューさせた。結果: **Critical なし、Major 5件・Minor 5件**。節番号参照・Markdown構造・目次の破損は確認されなかった。Lead判定と対応:

| # | 指摘(severity) | Lead判定 | 対応 |
|---|---|---|---|
| SP-1 | K上限の「向き」(入力か出力か)と段階間切り詰め規則・幅付き値の未確定(Major) | 向きの解釈固定は意味確定を伴い文書からは読み取れない → Decision Required | 未決事項 D-24 を新設 |
| SP-2 | グループLATCHのスコア集約と通知閾値の適用対象がD-06の未決範囲に含まれない(Major) | 正当。集約規則の決定自体は体験・設計判断 | D-06の未決範囲を拡張 |
| SP-3 | Mutual YES率・成立率とNorth Starの関係が不明、KPIの分母分子が未定義(Major) | 1対1・グループとも「必要人数全員YES=成立」のため同一指標と導出可能(Derived)。個別KPI(Candidate生成率等)の数え方は分析設計 | 表記をMutual Latch Rateに統一(第20・23・25節)。KPIの数え方の詳細は残余課題(下記) |
| SP-4 | API一覧が「確認してから保存」フロー(第8節)を表現できない(Major) | 第8節・第9節・第10節の確認フロー要件からparse/作成の分離は導出可能(Derived) | `POST /v1/intents/parse`(保存しないプレビュー)を追加し、`POST /v1/intents`を「確認済み構造データで作成」に修正 |
| SP-5 | Intent Entityにversionがなく、geo/budget/participant_countがstructured_data内と読める(Major) | 第16節(version必須)・第18節(Index必須)との整合 | Intent Entityに version / geo / budget / participant_count を追加、MatchEventのpayloadにversionを含める旨を注記 |
| SP-6 | D-01・D-02・D-20・D-23が本文中に(D-xx)参照を持たない(Minor) | 冒頭で宣言した参照運用の未遵守 | 第10節(D-01, D-20)・第23節(D-02)・第22節(D-23)・第12節(D-22)に参照を追加 |
| SP-7 | 用語の揺れ: Event/Match Event、Candidate/MatchCandidate/LATCH候補、Match Worker/Matching Worker(Minor) | 正当 | 第5節を「Match Event」に変更しCandidate系の粒度関係を一文で定義、第19節図をMatching Workerに統一 |
| SP-8 | 第15節「可能な集合」が網羅列挙に見え、選択規則を推測させる(Minor) | 正当 | 「可能な集合の例」に変更(D-06参照が既存) |
| SP-9 | 仮説3のJev定義の参照先が第13節だが定義は第12節(Minor) | 正当 | 第12節に修正 |
| SP-10 | 第25節「構造化できる」に合格基準がなく主観的(Minor) | 精度基準の数値はプロダクト判断 | D-17の未決範囲に「構造化精度の合格基準」を追加 |

### 残余課題(要件定義書の範囲外と判断したもの)

- 主要KPI(Candidate生成率・LATCH候補提示率・実行率・キャンセル率)の分母・分子の定義(SP-3の一部)。測定定義は分析設計の領域で、North Star(Mutual Latch Rate)の数え方は本レビューで固定済み。実行率はD-09(参加データの収集方法)に依存する。次版で第23節に測定定義を集約することを推奨する。

## 備考

- このリポジトリはGit管理されていないため、修正前後の比較は `/tmp/latch-review/original.md`(修正前の原本コピー)と `diff` によって検証した。
- 4レビューAgentの生のレビュー結果は `/tmp/latch-review/result-{reqa,prodb,nfrc,advd}.md` に保存されている(一時領域)。
