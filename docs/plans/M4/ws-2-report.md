# M4 ws-2(共有テスト資産)実行報告

- ブランチ: m4-ws-2 / ベース: cba4931(mainのマージベース。単位途中でmainは40e04e0へ進行 — 下記「補足」参照)
- 日付: 2026-10-03
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test(unit 1255件) | PASS(件数は1257 — 下記逸脱15) | lint: `All checks passed!` exit 0。test: `1257 passed, 261 deselected` exit 0(既存1219+新規38 = test_generator 19+test_reference_l1 19。計画比+2は逸脱7のjudge_pair_group unit 2件) |
| 2 | テストbasename一意 | PASS | `find backend/tests -name "test_*.py" \| awk -F/ '{print $NF}' \| sort \| uniq -d` → 出力空 |
| 3 | seed-generate+seed-verify | PASS | `generated ../docs/testassets/m4-seed.yaml: intents=300 users=100 pair_total=44850 layer1_pass=576` exit 0 → `OK: ../docs/testassets/m4-seed.yaml の機械検査に合格(a構造/b分布/c意図的配置/d aggregate照合/e地名)` exit 0 |
| 4 | 既存3資産無変更 | PASS | `git diff cba4931 -- docs/testassets/g1-parser-struct.yaml g1-alcohol.yaml g2-jev-goldset.yaml g2-jev-goldset-plan.md g2-jev-acceptance-criteria.md` → 空 |
| 5 | backend既存コード無変更・Makefile追記のみ | PASS | `git diff cba4931 --name-only -- backend/src/latch \| rg -v testassets` → 空。Makefile削除行は`.PHONY`旧行1行のみ(§2.5が規定する行統合による置換。ターゲット・レシピの削除なし) |
| 6 | 依存・マイグレーション無変更 | PASS | `git diff cba4931 -- backend/pyproject.toml backend/uv.lock backend/alembic` → 空 |
| 7 | 変更ファイル=§4の12ファイル | PASS | `git diff --name-only cba4931 \| sort` = Makefile・backend/src/latch/testassets/{__init__,vocab,reference_l1,generator,verify}.py・backend/tests/unit/testassets/{test_generator,test_reference_l1}.py・docs/testassets/{m4-seed.yaml,m4-seed-plan.md,README.md} + 本report = 12。`git status --short` 空 |
| 8 | EX-xxx 33件機械突合 | PASS | seed-verify(c)にEX違反なし(OK出力)+unit `test_ex_pair_cases_match_expected` PASS(24ペア)・クラスタ4件はverify構成検査・reject 5件はverify構造検査+unit `test_intended_reject_cases_structure` PASS |

注: 完了条件4〜7のdiff系検証は **マージベースcba4931に対して実施**した。単位の実施中にmainが40e04e0へ進行(ws-1マージ165242e込み)したため、`git diff main`はws-1変更分の逆diff(mainにはあるがworktreeにないstaging/loadターゲット・clock/events/settings等7ファイル)を含んでしまう。本単位由来の変更の判定にはマージベースが正。

## 生成実行の記録(design §4-2・4-4・supervisor指示)
- 生成コマンド: `make seed-generate ARGS="--base-datetime 2026-10-04T11:00:00+09:00"`(実行日2026-10-03の翌日午前・§7-2の指定どおり)
- 分布実測(§7-4):
```
time Counter({'night': 120, 'evening': 90, 'day': 60, 'other': 30})
cat  Counter({'drinking': 150, 'meal': 120, 'activity': 30})
ori  Counter({'one_to_one': 210, 'group': 90})
vis  Counter({'hidden_until_match': 162, 'summary_only': 138})
notif Counter({'proposals_only': 132, 'muted': 87, 'nearby_also': 81})
```
- aggregate照合(§7-5):
```
{'pair_total': 44850, 'layer1_pass': 576, 'layer1_fail': 44274,
 'fail_breakdown': {'self_pair': 305, 'category_mismatch': 25926,
  'time0': 17263, 'distance': 80, 'people': 574, 'budgetmin': 9,
  'blocked': 0, 'age_alcohol': 117}}
```
  fail_breakdown合計=44274=layer1_fail・blocked=0(D8)を確認。seed-verify(d)がreference_l1で全ペア独立再計算し一致済み
- バイト同一再生成(§7-7): 同一引数で再実行 → `git status --short` 変化なし(Task 5完了直後と§7-7の2回確認)。unit `test_generation_is_byte_identical` PASS
- verify fail検出確認(§7-6): 破壊YAML(SD-001のcategoryをmealへ書換)→ `exit 1`・`FAIL: 8件の違反`(カテゴリ分布2件+aggregate系6件)。**計画書の予測「EX-001判定不一致を含む」は成立しなかった**: EX-001はself_pair期待で、self_pairが優先順位第一のためcategory書換でも判定は変化しない(D2の帰結)。fail検出能力自体は分布・aggregateの違反検出で証明された。加えてTask 4 Step 4の別破壊試験(カテゴリ書換+EX-001素材user差し替え+rejectのalcohol false化・`--count 80`生成物)では18件の違反(分布9+EX-029 alcohol違反+aggregate不一致3+件数系4・user差し替えはaggregate不一致として検出)を確認
- lint再実行(§7-8): exit 0

## 固定値の変更有無(本計画§2.0 D1〜D13・§2コード全文)
- **D1〜D13の確定事項: 変更なし**(全て計画どおり実装)
- **EX構成33件・fail_reason優先順位・seed値42: 変更なし**(33件の内訳=ペア24+クラスタ4+reject 5・D2優先順位・seed 42を遵守)
- **§2.1〜2.5のコード全文からの逸脱: あり**(下記15件。いずれも計画書自身のunit/検査/確定事項(D3・D9)・supervisor裁定が解決方向を特定するもので、裁量判断は含まない)

| # | 箇所 | 変更前→変更後 | 理由 |
|---|---|---|---|
| 1 | §2.1 vocab.py LOCATIONS | g2-plan付録Aと同一の9地名・9座標 → **ci-db代表点の7地名・7座標**(天文館(31.59095,130.55543)・高見馬場(31.58926,130.55184)・呉服町(31.59041,130.55693)・加治屋町(31.58544,130.54828)・浜町(31.60224,130.56629)・城山(31.60405,130.54869)・鹿児島駅前(31.60064,130.56216)) | **supervisor裁定2026-10-03**(ci-db read-only実査): 正転geocode_forwardはnormalized_name完全一致のため、天文館通り・鹿児島中央駅前・鹿児島中央駅周辺はci実データに存在せずstaging投入時400/422になる。座標はstaging投入時にサーバの正転が返す代表点と同一値とする(裁定1)。本裁定が§9 Task 1のbboxテストと§2.1固定座標の矛盾(bbox西端130.5420<駅前130.5390)を解消 |
| 2 | §9 Task 1テスト | `assert len(LOCATIONS) == 9` → `== 7`(docstring「9地名」→「7地名」) | 裁定2(地名数9→7の承認) |
| 3 | §2.3 _EX_ROWS・REJECT_CASESの地名 | 廃止地名(天文館通り・鹿児島中央駅前)を含む行 → 呉服町(SD-003/004・SD-039/040・SD-058〜061・EX-030)・加治屋町(SD-009/010・SD-023)・城山(SD-026)・鹿児島駅前(EX-033)へ置換 | 裁定1/2の帰結(正転ヒットしない地名はシードに使えない) |
| 4 | §2.3 EX-012/013 | 旧: 駅前〜浜町(null+null 2000<約2775m fail)・駅前〜天文館(null+null 2000>=約1746m pass) → 新: **加治屋町〜浜町(null+null 2000<2529.7m fail)・天文館〜城山(null+null 2000>=1590.4m pass)**。basis文字列の数値も更新 | 裁定3(距離境界は実座標で再設計・意味論=半径和の内/外とnull補完カバレッジは保存・期待値はreference_l1をoracleとして確定) |
| 5 | §2.3 _boundary_radii docstring | 「実距離は約524m」→「約389m」 | 実座標での再計算(天文館〜高見馬場388.5m・導出ロジック自体は不変で自動追従) |
| 6 | §2.2 judge_pairのblocks照合 | `frozenset(blocks)`(要素の順序を保存)→ `frozenset(tuple(sorted(p)) for p in blocks)`(双方向正規化) | 計画§9 `test_blocked_pair_symmetric`(逆順タプル`{("SU-002","SU-001")}`でもblocked)が§2.2固定コードでは通らない実装バグ。06 §2の`NOT EXISTS ... OR`双方向と同一意味へ修正(TDD Red→Greenで確認) |
| 7 | §2.2にjudge_pair_group追加・§2.4クラスタ検査を変更 | クラスタのall_pairs_pass_l1をjudge_pair(1対1のpeople判定)→ **judge_pair_group(people条件を除くL1判定)**へ。unit 2件追加(test_reference_l1 17→19件) | §2.3 EX-027/028のメンバーはmin=3,4で、固定コードのままでは`2∉[3,4]`→people failとなり**seed-verifyが絶対に通らない**。D9(集合検索は判定対象外・クラスタは構成属性検査で担保)に基づく。人数要件はall_min_ge_3検査が担保 |
| 8 | §2.3 _remaining_slots | 残余割付が常に300件時固定(combos=236件)→ **残余件数へスケール**(バケット比率を_max_remainderでremainingへ配分→バケット内でカテゴリ配分の2段構成) | 固定コードでは計画自身のunit(count=80/100)・Task 4手順(--count 80)・design §3.3(ws-6の--count 1万〜10万)がすべて`残余割付の総数不一致`AssertionErrorで失敗。prose「残余に10 §2の比率を最大剰余法で適用・直交割付(バケット内でカテゴリを配分)」に一般化。**300件時の配分値(昼50/夕76/夜90/その他20・125/99/12・164/72)は完全保存**(手計算+unit test_time_bucket_targets等で確認) |
| 9 | §2.3 _build_remaining_intentのalcohol規則順序 | `drinking→True`が先 → **18-19歳ユーザー判定を最優先**(underage→False・次いでdrinking→True) | 固定コードではSU-008(18-19歳・残余スロットを持つ)が残余drinkingを引くとalcohol trueとなり、設計規則「18-19歳ユーザーのIntentは全てfalse」(design §2.4・§1参照表)とunit `test_underage_intents_all_alcohol_false`に反する |
| 10 | §2.3 OTHER_BUCKET_HOURS | D+1初日も0-23時全帯域 → **D+1初日はOTHER_BUCKET_HOURS_DAY1(9,10,11,14,15,16,17,23)に制限** | 固定コードでは「その他」バケットの0-8時台がD+1に割当たると計画自身の検査(`0.9 < delta < 6.0`日・unit `test_intents_within_d_plus_1_to_5`とverify(a))に違反(300件生成でSD-185=翌日01:00・delta 0.58日として実際に検出) |
| 11 | §9 Task 3テスト test_drinking_all_alcohol_true_and_activity_false | drinking→全true → **drinkingでかつ20歳以上のユーザー→true**(18-19歳ユーザーを除外) | 固定テストはD3素材(SD-008/010=drinking×alcohol false・18-19歳)にtrueを要求し、計画自身の`test_underage_intents_all_alcohol_false`と**同一intentで直接矛盾**(両立不可能)。設計規則の例外規定(同上)が解決 |
| 12 | §2.4 verify.py | YAML読み込み値(ISO文字列)をjudge_*へ直接渡す → **内部表現変換ビュー**(`_intent_for_judge`・users_judge・by_id_judge: birth_date→date・time→datetime実体)を追加しjudge_*へ渡す | 固定コードは`age_years_at(str)`でクラッシュ(実際に実行して確認 — reference_l1 docstringは「intent内部表現(datetime実体)」を契約と明記)。変換は(c)(d)のjudge_*呼び出しのみに適用し、(a)(b)の文字列ベース検査は計画どおり |
| 13 | §2/§9コードのruff整形 | EX note等の88桁超4か所は折返し・test_generatorの中間import(E402)は先頭へ移動・zipへ`strict=True`(B905)・未使用`timezone`除去とimport順(I001/F401) | `make lint`(ruff format --check + ruff check)合格に必須。挙動不変 |
| 14 | §6-1のunit期待件数 | 1255件 → **実測1257件** | 逸脱7のunit追加2件分(1219既存+19+19) |
| 15 | §7-6の予測 | 「EX-001判定不一致と分布違反を含む一覧」→ 実際は**分布違反+aggregate不一致(8件)でEX-001不一致は含まず** | EX-001はself_pair期待・優先順位第一のためcategory書換の影響を受けない(D2の帰結)。exit 1+FAIL一覧という検証目的は達成 |

## design §5 引継ぎ事項の確認
- §5-1(全ペア判定のws-3への提供形態): 本単位はreference_l1をモジュールとして
  提供(import可能・`judge_pair`/`judge_pair_group`/`judge_all`)・さらにm4-seed.yaml自体に
  EX期待値33件とaggregateを記録済み。中間出力JSONLが必要になった場合はws-3設計時に
  生成器へ--jsonlオプション追加で対応可
- §5-3(真のalcohol×20歳未満ペア素材): D3のとおり「18-19歳×drinking×false」を
  正規POSTで作成可能(structured_intent明示投入)と確認済み(service.pyの年齢検査は
  alcohol_involved=true時のみ)。**ws-3設計時の確認事項: raw_text→Parser経由で投入すると
  規則7でalcohol trueとなり422になる点。EX-004/005の素材(SD-007〜010)は
  structured_intent明示で投入すること**
- §5-2(staging投入経路): m4-seed-plan.md §7へ記載(users→POST /v1/users・intents→
  POST /v1/intents・投入直前に同一コマンドで再生成)
- §5-4(字義の整合): supervisor承認済み(2026-10-03)・追加対応なし
- §5-5(細部の裁量): D11どおり33件確定配置。上記逸脱表のとおり全て記録

## ws-3・ws-6への引継ぎ
- ws-3: 投入素材はdocs/testassets/m4-seed.yaml(users→POST /v1/users・intents→
  POST /v1/intents・structured_intent明示)。intended_reject_cases 5件(EX-029〜033)は
  422試行素材。EX-xxxのexpectedとreference_l1(backend/src/latch/testassets/reference_l1.py)で
  match_candidates突合可能(無向化して比較・design §2.2対称性注記)。
  クラスタのall_pairs_pass_l1(EX-027/028)はpeople条件を除く判定(judge_pair_group)が
  正しい突合基準(D9)。**地名7点はci-db代表点と同一座標のため、stagingの正転結果と
  YAML期待座標が一致する**(E2EのL1期待値がそのまま妥当・supervisor裁定1の趣旨)。
  staging投入直前に同一コマンドで再生成すること(YAML差分は対象日付のみ)
- ws-6: 拡張生成は同一生成器の `make seed-generate ARGS="--count 10000 ..."`
  (COUNT_MIN=65以上が条件)。**残余割付は残余件数へ最大剰余法でスケールするため
  任意件数で決定的に動作する**(逸脱8の一般化済み・ws-6側の追加実装は不要の見込み)。
  本体300件との整合は同一コード経路が保証
- embeddingはstub既定(ws-1設計§2.6)。実系統はws-3設計時にユーザー承認を経る

## コミット一覧
```
9bb282d docs: add m4-seed positioning and asset relations to testassets README
bfd8de2 docs: add m4-seed plan with generation rules and measured distribution
50fb411 feat: add seed-generate/seed-verify targets and m4-seed.yaml (300 intents)
c6e2f09 feat: add m4 seed yaml mechanical verification
88940e9 feat: add deterministic m4 seed generator
b8aacae feat: add layer1 reference implementation for m4 seed verification
6b01bc9 feat: add testassets vocab pools for m4 seed generation
```

## 補足(詰まった点・判断した点があれば)
1. **仕様矛盾で1回停止→supervisor裁定を経由**: Task 1のRED段階で§9テストのbbox検証
   (lon≧130.5420)と§2.1固定座標(鹿児島中央駅前130.5390)が矛盾し、指示どおり停止して
   報告。supervisorがci-db実査のうえ7地名への差し替えを裁定し、それに従って再開した。
2. **計画書固定コードの内部不整合を8件追加発見**(逸脱6〜12・13〜15): いずれも
   計画書自身のunit試験・verify検査・確定事項(D3・D9)・proseが解決方向を一意に
   特定する実装バグ(TDDのRed→Greenまたは実行時クラッシュで発見)であり、裁量を
   挟まず最小修正して本報告に全件記録した。**今後の単位への提案: 計画書の「実コード
   全文」はdispatch前に機械検証(ruff + 自身のunit試験を通るか)すると、この種の
   往復を削減できる**(M2/M3の「機械監査」教訓と同型)。
3. **mainの進行**: 単位実施中にmainが40e04e0へ進行(ws-1マージ込み)。本worktreeは
   cba4931ベースのまま完了。**マージ時の注意: Makefileの`.PHONY`行はws-1側も変更して
   いるため競合の可能性が高い**(本単位は `.PHONY: ... g2-gate seed-generate seed-verify`・
   main側は `... g2-gate staging-* load-*` — 統合形は両者を連結した1行で解消可能)。
   なお本単位はDB・Redis・dockerに触れない純Python+unit構成のため、ws-1のstaging/load
   変更との機能的相互作用はない。
4. **運用メモ**: パイプ経由の`make lint | tail`はtailのexit codeを返すため失敗を見逃した
   ことが2回ある(Task 3・4コミット時にruff違反を取り込む → 直後にfixして`--amend`で
   修復)。最終状態はlint/testともグリーン。以後のエージェントはlintの終了コードを
   直接確認することを推奨。
