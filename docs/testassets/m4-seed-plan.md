# M4シード(m4-seed)整備計画・実測

- ステータス: draft(M4 ws-2。README status規約に従いconfirmed化はスーパーバイザーレビュー経由)
- 作成: 2026-10-03(agent2計画)・生成: 2026-10-03(agent3)
- 参照: 10 §2・§3 / 09 §4・§4.1 / 06 §2 / 12 M4スコープ2 / docs/plans/M4/ws-2-design.md・ws-2-plan.md
- 方式: テンプレート直交生成の**決定的生成器**(design §2.1案B・supervisor承認2026-10-03)。
  g2-jev-goldsetは語彙・時刻パターン・カテゴリ配分の母集合として参照(本体YAML不変)

## 1. 生成コマンドと決定性

- コマンド: `make seed-generate ARGS="--base-datetime 2026-10-04T11:00:00+09:00"`
- 基準日時(base_datetime)・乱数シード(42)はYAML metaに記録。**同一引数での再生成はバイト同一**
  (unit test_generation_is_byte_identical+再生成時のgit diff無しで保証・
  2026-10-03の再生成実行で `git status --short` に変化なしを確認済み)
- 対象時刻は基準日D+1〜D+5に割り付け(+7日上限の2日マージン)。staging投入日はws-3の
  実施日のため、投入直前に同一コマンドで再生成する(YAML差分は対象日付のみ)
- 拡張生成(ws-6): `--count 10000/50000/100000`で同一コード経路(COUNT_MIN=65以上。
  残余割付は残余件数へ最大剰余法でスケールするため任意件数で決定的)

## 2. 生成規則(design §2.4・ws-2-plan §2.0〜2.3の要約)

- 件数: 300件(SD-001〜300)+合成ユーザー100人(SU-001〜100)
- 分布(10 §2完全一致): 時間帯=昼12-13時60/夕18-19時90/夜20-22時120/その他30。
  カテゴリ=drinking 150/meal 120/activity 30。人数志向=1対1 210/グループ(min≧3)90
- 構成: 意図的配置(EX素材64 intents・SD-001〜064)を先取りし、残余236件を比率配分
  (最大剰余法・時間帯×カテゴリは直交割付+乱数シャッフル)
- 18-19歳8人(SU-001〜008・生年月日はbase+7日でも18-19歳を保つマージン)。
  うちSU-001/002はD-10ペア素材・SU-003〜007はreject素材(EX-029〜033)・SU-008は通常
- alcohol規約: drinking→全true(20歳以上のユーザー)・activity→全false・mealは20歳以上の
  み一部true・**18-19歳ユーザーのIntentは全てfalse**(design §2.4)
- 地名: **検証済み7地名の許可リスト**(vocab.LOCATIONS)。
  **出典: ci-db代表点(2026-10-03 supervisor実査)・正転geocode_forwardは
  normalized_name完全一致(_FORWARD_SQL・ORDER BY osm_poi優先,id)の仕様に基づき、
  正転ヒットが確認できた地名のみを採用**。座標はstaging投入時(POST /v1/intents)に
  サーバの正転が返す代表点と同一値のため、ws-3のE2EでもL1期待値が妥当。
  7点ともsettings.py geo_osm_bbox=130.5420,31.5825,130.5740,31.6095 内。
  地名数9→7はsupervisor裁定2026-10-03で承認(10 §2は地名数を規定しない・
  距離多様性は最遠=加治屋町〜浜町約2.5km・最近=天文館〜高見馬場約0.4kmで確保)。
  geo結合検査は本単位スコープ外(design §4-5・資産改訂時のみ)
- expires_at: シードに含めない(省略=NULL・expired自動遷移を発生させない)

## 3. 期待値表の構成(design §2.2の2層)

- case expectations(EX-xxx・33件): ペア24件(EX-001〜024・L1判定を機械突合)+
  クラスタ4件(EX-025〜028・構成属性を機械検査)+reject 5件(EX-029〜033・422素材)。
  詳細はm4-seed.yamlのexpectations.cases(expected・fail_reason・basis〔根拠節番号〕つき)
- aggregate expectations: 総ペア44850・L1通過数・fail内訳8種・下記§4の実測のとおり。
  機械突合はLayer 1まで(Layer 2以降はws-3ハーネス側)
- fail_reason優先順位: self_pair > category_mismatch > time0 > distance > people >
  budgetmin > blocked > age_alcohol(ws-2-plan §2.0 D2)。blockedは0(シードにblocksなし)
- クラスタのall_pairs_pass_l1(EX-027/028)はpeople条件を除くL1判定
  (reference_l1.judge_pair_group・D9: 集合検索は1対1の2∈[min,max]判定の対象外。
  クラスタの人数要件はall_min_ge_3構成検査が担保)

## 4. 分布実測とaggregate照合(seed-verify結果・2026-10-03)

```
time Counter({'night': 120, 'evening': 90, 'day': 60, 'other': 30})
cat  Counter({'drinking': 150, 'meal': 120, 'activity': 30})
ori  Counter({'one_to_one': 210, 'group': 90})
vis  Counter({'hidden_until_match': 162, 'summary_only': 138})
notif Counter({'proposals_only': 132, 'muted': 87, 'nearby_also': 81})

agg {'pair_total': 44850, 'layer1_pass': 576, 'layer1_fail': 44274,
     'fail_breakdown': {'self_pair': 305, 'category_mismatch': 25926,
      'time0': 17263, 'distance': 80, 'people': 574, 'budgetmin': 9,
      'blocked': 0, 'age_alcohol': 117}}
```

- fail_breakdownの合計=44274=layer1_fail・blocked=0(D8)を確認。
  seed-verify(d)がreference_l1で全ペア独立再計算し一致を機械検査済み

## 5. 検証方式

- unit: test_generator.py(19件: 語彙1+生成器18)・test_reference_l1.py
  (19件: 06 §2確定表の既知ペア17+judge_pair_group 2〔D9・ws-2-plan固定コードからの逸脱〕)
- 機械検査: `make seed-verify`(verify.py): (a)構造(b)分布(c)意図的配置〔EX突合〕
  (d)aggregate照合〔reference_l1で全ペア独立再計算〕(e)地名許可リスト
- 二重実装の突合: 生成器(EX_CASES期待値は静的定義)とreference_l1(SQL・layer1.pyの
  LAYER1_WHEREのPython独立実装)を、生成時とseed-verify時の両方で突合
- 故意破壊確認(2026-10-03): `--count 80`生成物に3か所の破壊(カテゴリ書換・
  EX-001素材user差し替え・rejectのalcohol false化)を施したところ
  `FAIL: 18件の違反`(分布違反9件・EX-029 alcohol違反・aggregate不一致3件・
  件数系4件)でexit 1を確認 — verifyは壊れたYAMLを通さない

## 6. 既存3資産との関係整理(design §1.3・README.mdへも要約を記載)

| 資産 | 状態 | m4-seedとの関係 |
|---|---|---|
| g2-jev-goldset.yaml(304 Intent+520ペア) | confirmed・不変 | 評価ペア500件以上(09 §4.1)を充足済み。intents側は語彙・時刻パターン・カテゴリ配分の母集合として参照(design §2.1案B)。**ペア構成を変えるとG2評価結果の前提が崩れるため一切編集しない**(g2-plan §7)。なお地名はg2実績値をそのまま使わず、正転ヒット実査済みの7地名へ差し替えた(上記§2・supervisor裁定2026-10-03) |
| g1-alcohol.yaml(36件=true24/false12) | confirmed・不変 | 飲酒判定専用入力セット30件以上(10 §2 FR-20)を充足済み。期待値表はこれを参照(新規作成しない) |
| g1-parser-struct.yaml(32+3件) | confirmed・不変 | Parser構造化精度ゲート(09 §4.3)。id規約の衝突回避は新prefix(SU-/SD-/EX-)で担保 |

10 §2の「シードIntentは09のゴールドセットを共用」の読み: 評価ペア=g2(520ペア)、
機能試験素材=m4-seed。両方がdocs/testassets/配下でバージョン管理=「同一の資産を参照」。
g2 intentsをそのまま機能試験に投入しない根拠(実測・design §1.3): 10 §2分布から乖離
(時間帯その他23%・activity 21%・夜20-22時29%)・FR-17自己重複配置・合成users・
raw_textを欠く。

## 7. ws-3・ws-6への引継ぎ(design §3.3)

- ws-3: 投入素材はdocs/testassets/m4-seed.yaml(users→POST /v1/users・intents→
  POST /v1/intents・structured_intent明示)。intended_reject_cases 5件は422試行素材。
  EX-xxxのexpectedとreference_l1(backend/src/latch/testassets/reference_l1.py)で
  match_candidates突合可能(無向化して比較・design §2.2対称性注記)。
  **EX-004/005の素材(SD-007〜010)はstructured_intent明示で投入すること**
  (raw_text→Parser経由では規則7でalcohol trueとなり18-19歳側が422になるため)
- ws-6: 拡張生成は同一生成器の `make seed-generate ARGS="--count 10000 ..."`
  (COUNT_MIN=65以上が条件)。残余割付は残余件数へスケール済みで任意件数が決定的。
  本体300件との整合は同一コード経路が保証
- embeddingはstub既定(ws-1設計§2.6)。実系統はws-3設計時にユーザー承認を経る
