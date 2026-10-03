# M4 ws-2(共有テスト資産)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 10 §2のシードIntent 300件+合成ユーザー100人を**決定的生成器**(固定シード・再生成可能)で整備し、(1)シード定義`m4-seed.yaml`(2)期待値表(EX-xxx個別期待+aggregate集計の2層)(3)Layer 1参照実装(`reference_l1.py`・06 §2確定表のPython独立実装)をdocs/testassets/とbackend/src/latch/testassets/に作る。既存3資産(g1-parser-struct・g1-alcohol・g2-jev-goldset)は**1行も変更しない**。ws-3(機能試験stagingフル実行)とws-6(総当たり検出の拡張生成`--count 1万〜10万`)が同一コード経路で消費する資産を作る — design §1.1・§3.3。

**Architecture:** design §2.1案B・§2.2案B(supervisor承認2026-10-03)。**テンプレート直交生成の決定的生成器**を新設し、g2 intents(304件)は語彙・時刻パターン・カテゴリ配分の**母集合として参照**(g2本体YAMLは不変)。分布は10 §2完全一致(時間帯60/90/120/30・カテゴリ150/120/30・人数志向210/90)を生成規則として組み込み、意図的配置(FR-17自己重複・D-10・境界ケース)を先取りした残余を直交割付(最大剰余法)。全ペアL1判定(44,850ペア)は`reference_l1.py`が決定的に再計算し、YAMLには意図的配置の個別期待(EX-xxx)と集計値のみを記録 — 生成時と検証時の二重実装突合(g2-plan §10・付録A方式の拡張)。embeddingはstub既定(ws-1設計§2.6・実課金ゼロ)で決定対象外。

**Tech Stack:** 変更なし(Python 3.13 / 標準ライブラリ `random`・`math`・`datetime` / pyyaml>=6.0.3 は既存依存)。DB・Redis・外部SDK・Clockは**不要**(純Python・ローカル完結。design §3.2)。

**Spec:** `docs/plans/M4/ws-2-design.md`(agent1設計メモ。**未解決論点なし** — §6のsupervisor承認2026-10-03済み: ①§2.1案B(決定的生成器・g2 intentsは母集合参照)②embedding経路はstub既定訂正③§5-1〜3・5-5はws-3/ws-6設計時の判断点として引継ぎ確認済み)。design.mdが本計画より優先。

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m4-ws-2`。**agent3はworktree内でコミットする**(mainへの直接コミット・pushは禁止。マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する(依存追加・マイグレーション追加ともになし)。
- **worktree内で毎コミット `make lint` + `make test` をグリーンにする**: 本単位は純Python+unit試験のみで完了する。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **DB・Redis・docker系は全て実行禁止**: `make test-ci`・`make up`・`make down`・`make migrate`・`docker compose build`・`docker build` 等は**一切実行しない**。本単位にci試験は不要(生成器とunitのみ・supervisor確認済み)。**geo結合検査(design §4-5)は本計画のスコープ外**(supervisor指示: オプション扱いで除外 — 資産改訂時に必要になったとき別途)。
- **実API呼び出し禁止**: `make g1-gate`・`make g2-gate`・`make embed-smoke`・`make jev-smoke` は起動しない(本単位は外部SDKなし・LLM Gatewayに触らない。生成器はParser/Jevを呼ばない)。
- **テストファイルのbasename一意**(STATUS運用ルール5): tests配下は `__init__.py` なしのためbasenameがimport名になる。**計画時点で機械確認済み(2026-10-03)**: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` は空(既存133ファイル・unit 1219件)。本計画の新規2ファイル(`tests/unit/testassets/test_generator.py`・`tests/unit/testassets/test_reference_l1.py`)は既存と衝突しない(既存に同名・類似なし)。各Taskのコミット前に同コマンドが空であることを再確認する。
- **`backend/tests/unit/testassets/` ディレクトリは新規作成するが `__init__.py` は置かない**(prepend mode運用・M3 ws-2の `tests/unit/worker/` と同一)。
- **絶対に編集しない3資産**(design §3.2の硬い制約): `docs/testassets/g1-parser-struct.yaml`・`docs/testassets/g1-alcohol.yaml`・`docs/testassets/g2-jev-goldset.yaml`(+`docs/testassets/g2-jev-goldset-plan.md`)。**g2-plan §7「評価ペア520件の構成は変えない」を硬い制約として受ける**(supervisor承認§6・g2本体YAML不変は両案共通)。Task 8で `git diff main -- docs/testassets/g1-parser-struct.yaml docs/testassets/g1-alcohol.yaml docs/testassets/g2-jev-goldset.yaml docs/testassets/g2-jev-goldset-plan.md` が空であることを検証する。
- **backend既存コードへ変更なし**: `backend/src/latch/testassets/` 配下の新規追加とMakefile追記のみ。`git diff main -- backend/src/latch | rg -v testassets` が空であることをTask 8で検証する(arch test・依存追加も発生しない)。
- **生成物のコミット**: `docs/testassets/m4-seed.yaml`・`docs/testassets/m4-seed-plan.md` は**worktreeブランチへコミットする資産**(docs/testassets/でバージョン管理される。README status規約に従い `status: draft` でコミットし、confirmed化はスーパーバイザーレビュー経由)。
- **固定値の遵守**: 本計画§2の実コード全文・§9の確定事項(EX構成33件・fail_reason優先順位・seed値42等)は固定値。**変更した場合は必ず報告ファイル(§8)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConventional Commits形式(`feat:` `test:` `chore:` `docs:` 等)。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` 経由。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| シードIntent 200〜300件+期待値表をdocs/testassets/でバージョン管理し、09(Offline評価)と10(機能・非機能)で同一資産を参照 | 10 §2・参照値1 |
| 分布: 時間帯=昼12–13時20%/夕18–20時30%/夜20–23時40%/その他10%。カテゴリ=drinking 50%/meal 40%/activity 10%。人数志向=1対1 70%/グループ(min≧3)30% | 10 §2・参照値2 |
| 合成ユーザー(実在しない氏名)・生年月日はD-10試験のため18〜19歳を意図的に含める | 10 §2・参照値3 |
| FR-17: 自己ペア(同一ユーザーの2 Intentでペア生成されない)・自己重複集合(同一ユーザーのIntentを含む構成要件で集合生成されない)の意図的配置を分布に含める | 10 §3・参照値4 |
| D-10(#27): 18〜19歳によるalcohol_involved=trueのIntent作成拒否(422 UNDER_AGE)・alcohol_involved=trueのIntentが20歳未満ユーザーとの候補に現れない(Layer 1) | 10 §3・参照値5 |
| Layer 1 Hard Filter確定表: status=active・embedding IS NOT NULL・自己除外・category完全一致・時間交差(end nullはstart+3h補完)・ST_DWithin(r_origin+COALESCE(r_target,1000))・ペア予算LEAST(NULL無視)≧500円・人数2∈[min,max]双方(1対1検索)・ブロック双方向・飲酒は双方20歳以上 | 06 §2(実装: layer1.pyのLAYER1_WHERE)・参照値12 |
| ペア予算500円(未満のみfail・500ちょうどは通る)・比較演算は06 §2の確定値そのまま | 06 §2・layer1.py:23 |
| 保存API契約: raw_text 1〜300字必須・必須3フィールド(category・time.start・location)・時刻検証(過去不可・+7日上限)・年齢(alcohol_involved=true時のみ20歳以上)・participants 1〜4・visibility 2値・notification_level 3値 | 05 §5(実装: intent_input.py・service.py:278/283/300/430) |
| visibility=`hidden_until_match`/`summary_only`・notification_level=`proposals_only`/`nearby_also`/`muted`(3値はL1判定外・C13でLayer 5領域) | intent_input.py:66-67・C13 |
| シード規模200〜300件・テンプレートから典型表現のバリエーション・文言はテスト資産のためログ制限対象外 | 09 §4.1・参照値7 |
| M4開始時点でゴールドセット完成。G2の実YES供給源は既存520ペアのgold_mutual(本単位では再構成しない) | 12 T3・09 §4.2・参照値8 |
| 生成物は全件機械検査。layer1_passは生成時判定+検証スクリプトが独立再計算して一致確認(前例) | g2-plan §10・付録A・参照値10 |
| 既存部分セットのid(P-/A-/E-/SI-/U-)と衝突しない新prefix・フルセット全体でid一意 | docs/testassets/README.md・参照値11 |
| ジオコーディング対象エリア: ci暫定=鹿児島市天文館周辺・bbox=130.5420,31.5825,130.5740,31.6095 | STATUS G0記録・settings.py:63・参照値15 |
| レート制限はユーザー単位: Active 5件・作成20件/日(シード100人×最大3件で制限内) | 08 §5.4・参照値14 |
| 証拠はdocs/testassets/results/へ保管(本単位はseed-verify出力を必要に応じて置く) | 10 §5・参照値16 |
| 生成方式は案B(決定的生成器・g2 intentsは母集合参照)。g2本体YAML不変は硬い制約 | design §2.1・§6(supervisor承認2026-10-03) |
| 期待値表は2層(EX-xxx約50件の個別期待+aggregate集計)・機械突合はLayer 1まで(Layer 2以降はws-3ハーネスへ譲る) | design §2.2 |
| 決定性: `--base-datetime`(JST)引数・対象時刻は基準日D+1〜D+5・同一引数でバイト同一・YAML metaにbase_datetimeと乱数シードを記録・embedding実値は決定対象外(stub既定) | design §2.3 |
| id規約: ユーザー=SU-xxx・Intent=SD-xxx・期待値ケース=EX-xxx | design §2.4 |
| 時間帯バケット解釈: 昼=12:00-13:59・夕=18:00-19:59・夜=20:00-22:59開始・その他=上記以外 | design §2.4(解釈の告白) |
| 人数志向分類: グループ=participants.min≧3・1対1=それ以外 | design §2.4 |
| 場所はg2実績9地名の許可リストから選択(正転ジオコーディングヒット実績・bbox内) | design §2.4 |
| drinking 150件は全てalcohol=true・mealの一部true(20歳以上のみ)・18〜19歳ユーザーのIntentは全てfalse・作成拒否5件はintended_reject_cases | design §2.4(規則7) |
| 生成器の置き場所: backend/src/latch/testassets/(vocab・generator・reference_l1・verify)+Makefileターゲット | design §2.4 |
| テスト方針: 生成器unit(決定性)・分布機械検査・参照実装unit・seed-verify(a〜e) | design §4-1〜4-4 |
| ws-3接続: シードYAML→staging投入→reference_l1+EX-xxx期待値と突合。ws-6接続: `--count 10000/50000/100000`で同一コード経路 | design §3.3 |

## 2. 実コード全文(Task 1〜4でこのとおり作る。変更した場合は§8報告書に記録)

### §2.0 実装時確定事項(designからの落とし込み・designに書かれていない細部の確定)

| # | 確定事項 | 理由・根拠 |
|---|---|---|
| D1 | fail_reason分類は8種: `self_pair` / `category_mismatch` / `time0` / `distance` / `people` / `budgetmin` / `blocked` / `age_alcohol`。design §2.2の内訳列挙に**`distance`を追加**する | design §2.2の期待値構成が「距離境界(ST_DWithin半径和の内外)」を含めるため、距離failの機械分類が必須(g2-plan付録Aも`distance`を使用) |
| D2 | 複数条件に該当するペアのfail_reasonは**優先順位第一の1つ**を記録する。優先順位: `self_pair` > `category_mismatch` > `time0` > `distance` > `people` > `budgetmin` > `blocked` > `age_alcohol`(実SQLのWHEREは単一述語で順序なしのため分類順序は実装定義 — この順で確定) | aggregate照合(fail内訳)を決定的にするため |
| D3 | D-10ペア(EX-004/005)はdesign §2.2の「18-19歳の**meal**×20歳以上のdrinking」を「18-19歳の**drinking(alcohol_involved=false)**×20歳以上のdrinking(alcohol_involved=true)」に設計変更し、**同一カテゴリ**で構成する | meal×drinkingでは`category_mismatch`が優先順位で先に立ち`fail_reason=age_alcohol`が確定しない。API作成可能性は確認済み: service.pyの年齢検査は`alcohol_involved=true`のときのみ走る(service.py:430-431)ため、18-19歳×drinking×falseは正規POSTで作成可能(422にならない)。規則7(drinking→true)はParser経由の規則であり、structured_intent明示投入では適用されない点をws-3設計時の確認事項として§8に記録する |
| D4 | シードYAMLのintentに`expires_at`は**含めない**(DB列はnullable・省略=NULL) | 作成からexpiredへの自動遷移(idx_intents_expires抽出条件)を発生させず、ws-3のスモーク期間猶予を最大化する。時刻検証(service.py:300-304)はexpires_at=Noneなら走らない。design §2.3は対象時刻(D+1〜D+5)のみ規定しexpires_atには触れない |
| D5 | visibility 2値・notification_level 3値(muted含む)の全値カバレッジは**EX-xxxに入れず、分布の機械検査**(unit+seed-verify)で担保する | L1判定外(C13)のため個別期待の対象にならない。design §2.2列挙の「の各数件」の担保方法がこれ |
| D6 | 18-19歳8人の配分: SU-001(EX-004素材・18歳)・SU-002(EX-005素材・19歳)・SU-003〜SU-007(intended_reject素材5人・intentを持たない)・SU-008(通常ユーザー・残余スロットのintentを持つ・全intentでalcohol_involved=false) | design §2.4(18-19歳8人)・reject 5件(§2.2) |
| D7 | reject素材(EX-029〜033)の構造: `id`・`user`・`raw_text`・`structured_intent`(category+alcohol_involved=true+time.start+location)・`expected_status: 422`・`expected_code: UNDER_AGE`・`basis`。作成試行素材としてYAMLに定義のみ置き(シードintents配列には含めない)、ws-3がPOST試行する | design §2.4「『作成拒否』はintended_reject_casesとして資産に含める(5件)」 |
| D8 | ブロック条件はreference_l1に実装するが、シードにはblocksを作らない(全ペア`blocked`該当なし・aggregateの`blocked`=0)。unit試験で合成blocksを渡して動作を検証する | 正規APIでblocksを事前作成する素材がなく、ws-3側の試験領域(05 §5のblocks・D-23は別試験) |
| D9 | reference_l1は**1対1検索基準**(2∈[min,max]が双方)。集合検索(group_candidates・起点max≧3)は判定対象外とし、クラスタEX(EX-025〜028)は構成属性の機械検査(verifyが直接計算)で担保する | design §2.2「機械突合はLayer 1まで」・ws-3の集合試験に譲る(§3.3) |
| D10 | 乱数は`random.Random(42)`1インスタンスに集約し、生成順序が固定の箇所でだけ消費する。意図的配置(EX素材64 intents)は乱数を使わない固定値 | design §2.3(同一引数でバイト同一) |
| D11 | EXケースは33件(ペア24+クラスタ4+reject 5)。design §2.2の「約50件」に対し下限33件を確定配置とする(§5-5の「細部の裁量」・design §2.2の列挙要素はすべて含む — 対応表はTask 3の検証で確認) | 機械突合できるケースを確定配置する方が、件数を水増しするより資産として強い |
| D12 | seed値=`42`・`SEED_CREATED='2026-10-03'`(meta.created固定・date.today()を使わない) | バイト同一性のため実行日をYAMLに書かない |
| D13 | 意図的配置のうち同一ユーザー2 intents(SU-053のSD-041×SD-042・SU-059のSD-048×SD-049)は**仕様どおりのself_pair**としてaggregateのfail_breakdownに含まれる(EX casesには含めない) | 競合クローズ素材(EX-021/022)と自己重複クラスタ(EX-025)は同一ユーザー2 intentsが本質。unit `test_aggregate_matches_reference`・seed-verify(d)で整合確認 |

### §2.1 `backend/src/latch/testassets/vocab.py`(全文)

```python
"""シードIntent生成の語彙プール(M4 ws-2・design §2.4)。

g2-jev-goldset(machine方式)を母集合として参照する(design §2.1案B・
supervisor承認2026-10-03): 地名・カテゴリsecondary・soft文言はg2実績値から
採用。地名の座標はg2-plan付録Aと同一値(正転ジオコーディングヒット実績・
settings.py geo_osm_bbox=130.5420,31.5825,130.5740,31.6095 内)。
文言はテスト資産のためログ制限の対象外(09 §4.1)。
"""

from __future__ import annotations

# 地名許可リスト(g2実績9地名。(lat, lon)はg2-plan付録Aと同一)
LOCATIONS: dict[str, tuple[float, float]] = {
    "天文館": (31.5960, 130.5565),
    "天文館通り": (31.5945, 130.5560),
    "高見馬場": (31.5965, 130.5510),
    "呉服町": (31.5985, 130.5535),
    "鹿児島中央駅前": (31.5910, 130.5390),
    "鹿児島中央駅周辺": (31.5915, 130.5405),
    "加治屋町": (31.5970, 130.5490),
    "浜町": (31.6025, 130.5650),
    "城山": (31.6035, 130.5580),
}

# カテゴリ別secondary(g2実績値から採用)
CATEGORY_SECONDARY: dict[str, list[str]] = {
    "drinking": [
        "居酒屋", "バー", "立ち飲み", "ワイン", "ビール",
        "ハイボール", "日本酒", "焼酎", "バル", "おしゃれなバル",
    ],
    "meal": [
        "とんこつラーメン", "焼肉", "海鮮丼", "しゃぶしゃぶ", "イタリアン",
        "おむらいす", "定食", "カフェ", "甘味処", "ハンバーガー", "カレー", "寿司",
    ],
    "activity": ["カラオケ", "読書", "観光", "映画", "水族館", "街歩き", "ハイキング"],
}

# soft文言(g2実績値からカテゴリ別に採用)
SOFT_CONSTRAINTS: dict[str, list[str]] = {
    "drinking": [
        "のんびり席を温めたい", "ちょい飲みして帰りたい", "しっぽりと飲みたい",
        "軽く飲みたい", "まずは1杯だけ様子を見たい", "大人の雰囲気で",
        "しっとり静かな店がいい", "まったり語りたい", "締めの一杯",
        "銘酒をゆっくり味わいたい",
    ],
    "meal": [
        "がっつり食べたい", "軽めの食事で済ませたい", "さっと食べたい",
        "しっかり食べるつもり", "お腹を空かせてる", "野菜中心で",
        "肉系ならどこでもいい", "昼はサクッと", "昼は手早く", "早めに済ませたい",
    ],
    "activity": [
        "にぎやかに騒ぎたい", "わいわい盛り上がりたい", "静かにやりたい",
        "落ち着いた雰囲気で", "ゆっくり過ごしたい", "時間をかけて楽しみたい",
        "1時間くらいで切り上げたい", "サクッと帰るつもり", "短時間で済ませたい",
        "雰囲気良ければどこでも",
    ],
}

# raw_textテンプレート(カテゴリ別。{place}/{secondary}/{soft}を埋め込む)
RAW_TEMPLATES: dict[str, list[str]] = {
    "drinking": [
        "{time_prefix}{place}あたりで{secondary}に行きたい。{soft}",
        "{place}あたりで{secondary}を探してます。{soft}",
        "{time_prefix}{place}で{secondary}、一緒に行きませんか。{soft}",
    ],
    "meal": [
        "{time_prefix}{place}あたりで{secondary}を食べたい。{soft}",
        "{place}あたりで{secondary}に行きたいです。{soft}",
        "{time_prefix}{place}で{secondary}、同世代と一緒に。{soft}",
    ],
    "activity": [
        "{time_prefix}{place}あたりで{secondary}を楽しみたい。{soft}",
        "{place}あたりで{secondary}しませんか。{soft}",
        "{time_prefix}{place}で{secondary}、気軽に集まりたい。{soft}",
    ],
}

# 時間帯別のraw_text接頭辞
TIME_PREFIX: dict[str, str] = {
    "day": "昼に",
    "evening": "仕事終わりに",
    "night": "今夜",
    "other": "今度の休みに",
}

# 時間帯バケット(design §2.4の解釈: 開始時刻で分類。10 §2の分布=60/90/120/30)
# (name, 開始時刻の範囲[hour_from, hour_to)。"other"は範囲外判定)
TIME_BUCKETS: list[tuple[str, tuple[int, int] | None]] = [
    ("day", (12, 14)),  # 昼=12:00-13:59開始
    ("evening", (18, 20)),  # 夕=18:00-19:59開始
    ("night", (20, 23)),  # 夜=20:00-22:59開始
    ("other", None),  # その他=上記以外
]

def time_bucket_of(start_hour: int) -> str:
    """開始時刻→時間帯バケット(design §2.4の解釈。verifyも同一関数を使用)。"""
    for name, hours in TIME_BUCKETS:
        if hours is None:
            return name
        if hours[0] <= start_hour < hours[1]:
            return name
    raise AssertionError("unreachable")  # otherがNone到達を吸収する

### §2.2 `backend/src/latch/testassets/reference_l1.py`(全文)

```python
"""Layer 1 Hard Filter参照実装(M4 ws-2・06 §2確定表・design §2.2)。

layer1.pyのLAYER1_WHERE(SQL+PostGIS)の**Python独立実装**(二重実装の突合・
g2-plan §10方式の拡張)。シードYAMLの全ペア判定(無向・C(n,2))とEX-xxx
期待値の機械突合に使う。DB・embeddingに依存しない(06 §2はAI不使用)。

LAYER1_WHEREとの1対1対応(design §4-3・06 §2確定表):
- status='active'                              → シード全件active前提(判定対象外)
- embedding IS NOT NULL                        → stub付与前提(判定対象外・ws-1 §2.6)
- i.user_id <> :origin_user_id                 → self_pair
- i.category_primary = :origin_category        → category_mismatch
- 時間交差(end nullはstart+3h補完)           → time0
- ST_DWithin(r_origin+COALESCE(r_target,1000)) → distance(haversine近似)
- ペア予算LEAST(NULL無視)>=500円              → budgetmin
- 人数2∈[min,max]双方(1対1検索)             → people
- ブロック双方向                               → blocked(シードにblocksは作らない・D8)
- 飲酒は双方20歳以上                           → age_alcohol

fail_reason優先順位: self_pair > category_mismatch > time0 > distance >
people > budgetmin > blocked > age_alcohol(§2.0 D2)。
本判定は1対1検索基準(2∈[min,max]双方)。集合検索(group_candidates・起点
max≧3)は判定対象外(D9・ws-3側の試験)。判定条件は全て対称のため期待値は
無向ペア集合で定義する(design §2.2の対称性注記)。
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone

from latch.testassets.vocab import LOCATIONS

# core.clock.JSTと同一値(testassetsを単一モジュール経路に保つため自前定義)
JST = timezone(timedelta(hours=9))

EARTH_RADIUS_M = 6_371_000.0
PAIR_BUDGET_MIN_YEN = 500  # 06 §2・layer1.py PAIR_BUDGET_MIN_YENと同一
END_NULL_COMPLETION = timedelta(hours=3)  # 03 D-19・LAYER1_WHEREの interval '3 hours'
DEFAULT_RADIUS_M = 1000  # LAYER1_WHEREの COALESCE(i.geo_radius_m, 1000)

FAIL_PRIORITY = (
    "self_pair",
    "category_mismatch",
    "time0",
    "distance",
    "people",
    "budgetmin",
    "blocked",
    "age_alcohol",
)

def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """2地点(lat, lon)間の大円距離m(ST_DWithin geographyの球面近似)。"""
    lat1, lon1 = a
    lat2, lon2 = b
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2.0 * EARTH_RADIUS_M * math.asin(math.sqrt(h))

def completed_end(start: datetime, end: datetime | None) -> datetime:
    """end nullのstart+3h補完(COALESCE(i.time_end, i.time_start + 3h))。"""
    return start + END_NULL_COMPLETION if end is None else end

def age_years_at(birth: date, on: date) -> int:
    """満年齢(EXTRACT(YEAR FROM AGE(... AT TIME ZONE 'Asia/Tokyo'))と同一規則)。"""
    years = on.year - birth.year
    if (on.month, on.day) < (birth.month, birth.day):
        years -= 1
    return years

def _fail_reasons(
    a: dict, b: dict, *, users: dict[str, dict], now: datetime, blocks: frozenset[tuple[str, str]]
) -> list[str]:
    """該当する全fail条件(FAIL_PRIORITY順)。passなら空リスト。

    a/bはシードのintent内部表現(datetime実体・§2.3の_Slot形式)。
    """
    sa, sb = a["structured"], b["structured"]
    reasons: list[str] = []
    # 自己除外(LAYER1_WHERE: i.user_id <> :origin_user_id)。
    # 同一ユーザーペアは他条件と無関係に除外確定(早期return)
    if a["user"] == b["user"]:
        return ["self_pair"]
    # category完全一致(i.category_primary = :origin_category)
    if sa["category"]["primary"] != sb["category"]["primary"]:
        reasons.append("category_mismatch")
    # 時間交差(i.time_start < :origin_time_end AND :origin_time_start < COALESCE(...))
    a_end = completed_end(sa["time"]["start"], sa["time"]["end"])
    b_end = completed_end(sb["time"]["start"], sb["time"]["end"])
    if not (sa["time"]["start"] < b_end and sb["time"]["start"] < a_end):
        reasons.append("time0")
    # ST_DWithin(r_origin + COALESCE(r_target, 1000))。geographyの大円距離
    dist = haversine_m(LOCATIONS[sa["location"]["name"]], LOCATIONS[sb["location"]["name"]])
    r_a = sa["location"]["radius_m"] or DEFAULT_RADIUS_M
    r_b = sb["location"]["radius_m"] or DEFAULT_RADIUS_M
    if dist > r_a + r_b:
        reasons.append("distance")
    # 人数2∈[min,max]双方(1対1検索・06 §2)
    pa, pb = sa["participants"], sb["participants"]
    if not (pa["min"] <= 2 <= pa["max"] and pb["min"] <= 2 <= pb["max"]):
        reasons.append("people")
    # ペア予算LEAST(NULL無視)>=500(NULL無視=双方制約なしはfailしない)
    budgets = [
        x["budget"]["max"]
        for x in (sa, sb)
        if x["budget"] is not None and x["budget"]["max"] is not None
    ]
    if budgets and min(budgets) < PAIR_BUDGET_MIN_YEN:
        reasons.append("budgetmin")
    # ブロック双方向(NOT EXISTS blocks ...)
    if tuple(sorted((a["user"], b["user"]))) in blocks:
        reasons.append("blocked")
    # 飲酒は双方20歳以上((NOT (a_alc OR b_alc)) OR (a_ge20 AND b_ge20))
    if sa.get("alcohol_involved") or sb.get("alcohol_involved"):
        jst_today = now.astimezone(JST).date()
        ge20_a = age_years_at(users[a["user"]]["birth_date"], jst_today) >= 20
        ge20_b = age_years_at(users[b["user"]]["birth_date"], jst_today) >= 20
        if not (ge20_a and ge20_b):
            reasons.append("age_alcohol")
    return reasons

def judge_pair(
    a: dict,
    b: dict,
    *,
    users: dict[str, dict],
    now: datetime,
    blocks: Iterable[tuple[str, str]] = (),
) -> tuple[bool, str | None]:
    """無向ペア1組のL1判定。戻り値=(pass, fail_reason|None)。"""
    reasons = _fail_reasons(a, b, users=users, now=now, blocks=frozenset(blocks))
    if not reasons:
        return True, None
    return False, reasons[0]  # 優先順位第一のみを記録(D2)

def judge_all(
    intents: list[dict],
    users: dict[str, dict],
    *,
    now: datetime,
    blocks: Iterable[tuple[str, str]] = (),
) -> dict[tuple[str, str], tuple[bool, str | None]]:
    """全無向ペアのL1判定。キー=(id_a, id_b)(idソート済み・n*(n-1)/2組)。"""
    result: dict[tuple[str, str], tuple[bool, str | None]] = {}
    ordered = sorted(intents, key=lambda i: i["id"])
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            result[(a["id"], b["id"])] = judge_pair(
                a, b, users=users, now=now, blocks=blocks
            )
    return result
```

### §2.3 `backend/src/latch/testassets/generator.py`(全文・その1/3)

```python
"""決定的シード生成器(M4 ws-2・design §2.3・10 §2)。

固定シード(42)のテンプレート直交生成。--base-datetime(JST)を基準に対象
時刻をD+1〜D+5へ割り付け、同一引数での再生成はバイト同一(design §2.3)。
--count変更時も決定的(ws-6の拡張生成1万〜10万が同一コード経路・§3.3)。

配分(10 §2完全一致・300件時):
- 時間帯: 昼60/夕90/夜120/その他30(開始時刻・design §2.4の解釈)
- カテゴリ: drinking 150/meal 120/activity 30
- 人数志向: 1対1 210/グループ(min≧3)90

構成: 意図的配置(EX素材64 intents・SD-001〜064)を先に組み立て、残余を
比率配分(最大剰余法)+乱数シャッフルで割り当てる(D10・意図的配置は乱数
不使用)。全ペアL1判定はreference_l1(独立実装)で計算し、YAMLには
EX-xxx個別期待+aggregate集計のみを書き出す(design §2.2の2層)。
"""

from __future__ import annotations

import argparse
import copy
import math
import random
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from latch.testassets.reference_l1 import (
    JST,
    FAIL_PRIORITY,
    haversine_m,
    judge_all,
    judge_pair,
)
from latch.testassets.vocab import (
    CATEGORY_SECONDARY,
    LOCATIONS,
    RAW_TEMPLATES,
    SOFT_CONSTRAINTS,
    TIME_PREFIX,
    time_bucket_of,
)

SEED = 42
SEED_CREATED = "2026-10-03"
USER_COUNT = 100
DEFAULT_COUNT = 300
EX_SLOT_COUNT = 64
COUNT_MIN = EX_SLOT_COUNT + 1  # 意図的配置64件+残余1件未満は生成不可
DEFAULT_OUT = "../docs/testassets/m4-seed.yaml"

# 分布目標(10 §2・300件時の実現値)。残余には消費差し引き後の比率を適用する
TIME_TARGETS = {"day": 60, "evening": 90, "night": 120, "other": 30}
CATEGORY_TARGETS = {"drinking": 150, "meal": 120, "activity": 30}
ORIENTATION_TARGETS = {"one_to_one": 210, "group": 90}
CATEGORY_ORDER = ["drinking", "meal", "activity"]
BUCKET_ORDER = ["day", "evening", "night", "other"]

# 残余スロットの対象日割付(D+1〜D+5をラウンドロビン・design §2.3)
DAY_OFFSETS = [1, 2, 3, 4, 5]

# 残余スロットの値域(実装契約・参照値13)
BUDGET_CHOICES = [None, None, 1500, 2000, 2500, 3000, 3500, 5000]  # null率25%
RADIUS_CHOICES = [None, None, None, 1000, 2000, 3000]  # null率60%(補完カバレッジ)
END_NULL_RATIO = 0.1  # end null(start+3h補完)のカバレッジ
MEAL_ALCOHOL_RATIO = 0.15  # meal×alcohol true(20歳以上のユーザーのみ)
VISIBILITY_CHOICES = ["hidden_until_match", "summary_only"]
NOTIFICATION_CHOICES = ["proposals_only", "nearby_also", "muted"]
OTHER_BUCKET_HOURS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 14, 15, 16, 17, 23]
NAME_POOL = [
    "さくら", "はると", "ゆい", "そうた", "あおい",
    "かえで", "りく", "ひな", "ゆうと", "みお",
]

# 18-19歳8人の生年月日基準(D6・base年月日から年月を引いた値)。
# (ユーザー, 年, 月) — base+7日時点でも18-19歳を保つ3ヶ月以上のマージン
UNDERAGE_BIRTHS = [
    ("SU-001", 18, 6),   # EX-004素材(18歳)
    ("SU-002", 19, 6),   # EX-005素材(19歳)
    ("SU-003", 18, 4),   # EX-029 reject素材
    ("SU-004", 18, 7),   # EX-030
    ("SU-005", 18, 10),  # EX-031
    ("SU-006", 19, 2),   # EX-032
    ("SU-007", 19, 8),   # EX-033
    ("SU-008", 19, 9),   # 通常ユーザー(残余スロット・全intentでalcohol false)
]

# reject素材(EX-029〜033・D7)。作成試行のみでシードintents配列には含めない
REJECT_CASES: list[dict] = [
    {
        "id": "EX-029", "user": "SU-003",
        "raw_text": "今夜ワインを飲みたい。落ち着いた雰囲気で",
        "structured_intent": {
            "category": {"primary": "drinking", "secondary": "ワイン"},
            "alcohol_involved": True,
            "time": {"start": "D+1T20:00"},
            "location": {"name": "天文館"},
        },
        "expected_status": 422, "expected_code": "UNDER_AGE",
        "basis": "D-10(10 §3 #27)・05 §5(年齢検証)",
    },
    {
        "id": "EX-030", "user": "SU-004",
        "raw_text": "居酒屋でしっぽりと飲みたい",
        "structured_intent": {
            "category": {"primary": "drinking", "secondary": "居酒屋"},
            "alcohol_involved": True,
            "time": {"start": "D+2T20:00"},
            "location": {"name": "天文館通り"},
        },
        "expected_status": 422, "expected_code": "UNDER_AGE",
        "basis": "同上",
    },
    {
        "id": "EX-031", "user": "SU-005",
        "raw_text": "ハイボール片手に1杯だけ。ちょい飲みして帰りたい",
        "structured_intent": {
            "category": {"primary": "drinking", "secondary": "ハイボール"},
            "alcohol_involved": True,
            "time": {"start": "D+3T18:00"},
            "location": {"name": "高見馬場"},
        },
        "expected_status": 422, "expected_code": "UNDER_AGE",
        "basis": "同上",
    },
    {
        "id": "EX-032", "user": "SU-006",
        "raw_text": "焼肉のあと日本酒を飲みたい。まったり語りたい",
        "structured_intent": {
            "category": {"primary": "drinking", "secondary": "日本酒"},
            "alcohol_involved": True,
            "time": {"start": "D+4T20:00"},
            "location": {"name": "加治屋町"},
        },
        "expected_status": 422, "expected_code": "UNDER_AGE",
        "basis": "同上",
    },
    {
        "id": "EX-033", "user": "SU-007",
        "raw_text": "おしゃれなバルでバル巡り。まずは1杯だけ様子を見たい",
        "structured_intent": {
            "category": {"primary": "drinking", "secondary": "おしゃれなバル"},
            "alcohol_involved": True,
            "time": {"start": "D+5T19:00"},
            "location": {"name": "鹿児島中央駅前"},
        },
        "expected_status": 422, "expected_code": "UNDER_AGE",
        "basis": "同上",
    },
]

# EXケース期待値(EX-001〜028・D11)。kind=pairはreference_l1と機械突合、
# kind=group_clusterはverifyが構成属性を機械検査する
EX_CASES: list[dict] = [
    {"id": "EX-001", "kind": "pair", "intents": ["SD-001", "SD-002"], "expected": "fail", "fail_reason": "self_pair", "basis": "10 §3(FR-17自己ペア)・06 §2(自己除外)"},
    {"id": "EX-002", "kind": "pair", "intents": ["SD-003", "SD-004"], "expected": "fail", "fail_reason": "self_pair", "basis": "同上"},
    {"id": "EX-003", "kind": "pair", "intents": ["SD-005", "SD-006"], "expected": "fail", "fail_reason": "self_pair", "basis": "同上"},
    {"id": "EX-004", "kind": "pair", "intents": ["SD-007", "SD-008"], "expected": "fail", "fail_reason": "age_alcohol", "basis": "D-10(10 §3 #27)・06 §2(飲酒は双方20歳以上)"},
    {"id": "EX-005", "kind": "pair", "intents": ["SD-009", "SD-010"], "expected": "fail", "fail_reason": "age_alcohol", "basis": "同上"},
    {"id": "EX-006", "kind": "pair", "intents": ["SD-011", "SD-012"], "expected": "pass", "fail_reason": None, "basis": "06 §2(end nullはstart+3h補完で交差成立)"},
    {"id": "EX-007", "kind": "pair", "intents": ["SD-013", "SD-014"], "expected": "fail", "fail_reason": "time0", "basis": "06 §2(時間交差・境界隣接は交差0)"},
    {"id": "EX-008", "kind": "pair", "intents": ["SD-015", "SD-016"], "expected": "pass", "fail_reason": None, "basis": "06 §2(交差1分は境界以上で通る)"},
    {"id": "EX-009", "kind": "pair", "intents": ["SD-017", "SD-018"], "expected": "fail", "fail_reason": "time0", "basis": "06 §2(完全非交差)"},
    {"id": "EX-010", "kind": "pair", "intents": ["SD-019", "SD-020"], "expected": "pass", "fail_reason": None, "basis": "06 §2(ST_DWithin・半径和>=実距離[切り上げ])"},
    {"id": "EX-011", "kind": "pair", "intents": ["SD-021", "SD-022"], "expected": "fail", "fail_reason": "distance", "basis": "06 §2(半径和<実距離[切り下げ])"},
    {"id": "EX-012", "kind": "pair", "intents": ["SD-023", "SD-024"], "expected": "fail", "fail_reason": "distance", "basis": "06 §2(radius null補完1000+1000<実距離約2775m)"},
    {"id": "EX-013", "kind": "pair", "intents": ["SD-025", "SD-026"], "expected": "pass", "fail_reason": None, "basis": "06 §2(radius null補完1000+1000>=実距離約1746m)"},
    {"id": "EX-014", "kind": "pair", "intents": ["SD-027", "SD-028"], "expected": "fail", "fail_reason": "budgetmin", "basis": "06 §2(LEAST=400<500)"},
    {"id": "EX-015", "kind": "pair", "intents": ["SD-029", "SD-030"], "expected": "pass", "fail_reason": None, "basis": "06 §2(500ちょうどは通る)"},
    {"id": "EX-016", "kind": "pair", "intents": ["SD-031", "SD-032"], "expected": "fail", "fail_reason": "budgetmin", "basis": "06 §2(NULL無視=実効499)"},
    {"id": "EX-017", "kind": "pair", "intents": ["SD-033", "SD-034"], "expected": "pass", "fail_reason": None, "basis": "06 §2(双方NULL=予算制約なし)"},
    {"id": "EX-018", "kind": "pair", "intents": ["SD-035", "SD-036"], "expected": "fail", "fail_reason": "people", "basis": "06 §2(2∉[3,4])"},
    {"id": "EX-019", "kind": "pair", "intents": ["SD-037", "SD-038"], "expected": "fail", "fail_reason": "people", "basis": "06 §2(2∉[1,1])"},
    {"id": "EX-020", "kind": "pair", "intents": ["SD-039", "SD-040"], "expected": "pass", "fail_reason": None, "basis": "06 §2(2∈[2,4])"},
    {"id": "EX-021", "kind": "pair", "intents": ["SD-041", "SD-043"], "expected": "pass", "fail_reason": None, "basis": "10 §3(競合クローズ素材・同一ユーザーの2 Intentが同一相手と双方候補)"},
    {"id": "EX-022", "kind": "pair", "intents": ["SD-042", "SD-043"], "expected": "pass", "fail_reason": None, "basis": "同上(SD-041×SD-042は同一ユーザーでself_pair)"},
    {"id": "EX-023", "kind": "pair", "intents": ["SD-044", "SD-045"], "expected": "fail", "fail_reason": "category_mismatch", "basis": "06 §2(category完全一致)"},
    {"id": "EX-024", "kind": "pair", "intents": ["SD-046", "SD-047"], "expected": "fail", "fail_reason": "category_mismatch", "basis": "同上"},
    {"id": "EX-025", "kind": "group_cluster", "intents": ["SD-048", "SD-049", "SD-050", "SD-051"],
     "expected": {"distinct_users": 3, "all_min_ge_3": True,
                  "note": "同一ユーザー(SU-040)の2 Intentを含む構成要件 — group_candidatesに生成されないこと(FR-17自己重複集合)。集合生成自体はws-3で確認"},
     "basis": "10 §3(FR-17)・design §2.2"},
    {"id": "EX-026", "kind": "group_cluster", "intents": ["SD-052", "SD-053", "SD-054", "SD-055", "SD-056", "SD-057"],
     "expected": {"distinct_users": 6, "all_min_ge_3": True, "same_bucket": "night", "same_place": "天文館", "same_category": "drinking",
                  "note": "同Bucket・同地域・同カテゴリのmin≧3×6件 — 複数集合が立つ通知順序素材(D-06)をws-3で消費"},
     "basis": "10 §3(通知順序)・design §2.2"},
    {"id": "EX-027", "kind": "group_cluster", "intents": ["SD-058", "SD-059", "SD-060", "SD-061"],
     "expected": {"distinct_users": 4, "all_min_ge_3": True, "all_pairs_pass_l1": True,
                  "note": "部分YESクラスタ(3〜4人集合素材) — 一部のみyes回答でresponse_deadline後へ(10 §3)をws-3で消費"},
     "basis": "10 §3(部分YES)・design §2.2"},
    {"id": "EX-028", "kind": "group_cluster", "intents": ["SD-062", "SD-063", "SD-064"],
     "expected": {"distinct_users": 3, "all_min_ge_3": True, "all_pairs_pass_l1": True, "note": "同上(3人集合)"},
     "basis": "同上"},
]

(§2.3の続き・全文その2/3: ヘルパーと意図的配置)

```python
def _at(base: datetime, day_offset: int, hour: int, minute: int = 0) -> datetime:
    """baseの日(JST)からday_offset日後のhour:minute(JST)を返す(design §2.3)。"""
    d = base.astimezone(JST) + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=JST)

def _minus_ym(d: date, years: int, months: int) -> date:
    """日付から年月を引く(月末クランプ・うるう年対応)。"""
    total = d.year * 12 + d.month - 1 - years * 12 - months
    y, m0 = divmod(total, 12)
    m = m0 + 1
    if m == 12:
        next_first = date(y + 1, 1, 1)
    else:
        next_first = date(y, m + 1, 1)
    return date(y, m, min(d.day, (next_first - timedelta(days=1)).day))

def _boundary_radii() -> tuple[int, int, int, int]:
    """EX-010/011の半径境界値(haversine実距離から決定的に導出)。

    天文館〜高見馬場の実距離は約524m: hi側=合計ceil(d)(pass)・
    lo側=合計floor(d)-1(fail)。verifyが同一計算で再現する。
    """
    d = haversine_m(LOCATIONS["天文館"], LOCATIONS["高見馬場"])
    hi_a, hi_b = math.ceil(d) - 1, 1  # 合計=ceil(d) >= d → pass
    lo_a, lo_b = math.floor(d) - 1, 1  # 合計=floor(d)-1 < d → fail
    return hi_a, hi_b, lo_a, lo_b

# 意図的配置テーブル(EX素材64 intents・乱数不使用・D10)。
# 行: (day, hh, mm, dur_min|None(end null), 地名, カテゴリ, secondary,
#      alcohol, radius|int|"HI_A"|"HI_B"|"LO_A"|"LO_B", budget|None, pmin, pmax)
# radius文字列は_boundary_radii()の実行時計算値へ置換する。
# 同列の並行リスト: _EX_USERS(ユーザー)・_EX_GROUPS(ex_group表記)
_EX_ROWS: list[tuple] = [
    # -- EX-001..003 自己ペア3組(同一ユーザーの2 intents・10 §3 FR-17)
    (1, 20, 0, 180, "天文館", "drinking", "居酒屋", True, 1000, 3500, 2, 2),       # SD-001 SU-010
    (1, 20, 30, 180, "天文館", "drinking", "居酒屋", True, 1000, 3500, 2, 2),      # SD-002 SU-010
    (1, 18, 0, 120, "天文館通り", "meal", "カフェ", False, 1000, 2000, 2, 2),      # SD-003 SU-011
    (1, 18, 30, 120, "天文館通り", "meal", "カフェ", False, 1000, 2000, 2, 2),     # SD-004 SU-011
    (1, 12, 0, 120, "高見馬場", "activity", "街歩き", False, 1000, 2000, 2, 2),    # SD-005 SU-012
    (1, 12, 30, 120, "高見馬場", "activity", "街歩き", False, 1000, 2000, 2, 2),   # SD-006 SU-012
    # -- EX-004/005 D-10ペア(18-19歳×drinking false vs 20歳以上×true・D3)
    (1, 20, 0, 180, "天文館", "drinking", "居酒屋", True, 1000, 3500, 2, 2),       # SD-007 SU-021
    (1, 20, 30, 180, "天文館", "drinking", "ハイボール", False, 1000, 3500, 2, 2), # SD-008 SU-001(18歳)
    (2, 20, 0, 180, "天文館通り", "drinking", "バー", True, 1000, 3000, 2, 2),     # SD-009 SU-022
    (2, 20, 30, 180, "天文館通り", "drinking", "ビール", False, 1000, 3000, 2, 2), # SD-010 SU-002(19歳)
    # -- EX-006..009 時間交差境界
    (1, 12, 0, 120, "呉服町", "meal", "定食", False, 1000, 1500, 2, 2),            # SD-011 SU-023
    (1, 13, 0, None, "呉服町", "meal", "定食", False, 1000, 1500, 2, 2),           # SD-012 SU-024(end null→補完16:00で交差pass)
    (2, 18, 0, 90, "天文館", "meal", "とんこつラーメン", False, 1000, 2000, 2, 2), # SD-013 SU-025
    (2, 19, 30, 90, "天文館", "meal", "とんこつラーメン", False, 1000, 2000, 2, 2),# SD-014 SU-026(境界隣接→time0)
    (2, 20, 0, 61, "天文館", "drinking", "ビール", True, 1000, 2500, 2, 2),        # SD-015 SU-027
    (2, 21, 0, 90, "天文館", "drinking", "ビール", True, 1000, 2500, 2, 2),        # SD-016 SU-028(交差1分→pass)
    (3, 10, 0, 60, "浜町", "activity", "読書", False, 1000, 1500, 2, 2),           # SD-017 SU-029
    (3, 15, 0, 60, "浜町", "activity", "読書", False, 1000, 1500, 2, 2),           # SD-018 SU-030(完全非交差→time0)
    # -- EX-010..013 距離境界
    (3, 18, 0, 120, "天文館", "activity", "観光", False, "HI_A", 2000, 2, 2),      # SD-019 SU-031
    (3, 18, 30, 120, "高見馬場", "activity", "観光", False, "HI_B", 2000, 2, 2),   # SD-020 SU-032(半径和切り上げ→pass)
    (4, 18, 0, 120, "天文館", "activity", "観光", False, "LO_A", 2000, 2, 2),      # SD-021 SU-033
    (4, 18, 30, 120, "高見馬場", "activity", "観光", False, "LO_B", 2000, 2, 2),   # SD-022 SU-034(切り下げ→distance)
    (3, 10, 0, 120, "鹿児島中央駅前", "drinking", "バル", True, None, 2500, 2, 2), # SD-023 SU-035(radius null)
    (3, 10, 30, 120, "浜町", "drinking", "バル", True, None, 2500, 2, 2),          # SD-024 SU-036(null補完1000+1000<約2775m→distance)
    (4, 10, 0, 120, "天文館", "activity", "水族館", False, None, 2000, 2, 2),      # SD-025 SU-037(radius null)
    (4, 10, 30, 120, "鹿児島中央駅前", "activity", "水族館", False, None, 2000, 2, 2), # SD-026 SU-038(補完2000>=約1746m→pass)
    # -- EX-014..017 予算境界
    (4, 12, 0, 120, "加治屋町", "meal", "定食", False, 1000, 400, 2, 2),           # SD-027 SU-039
    (4, 12, 30, 120, "加治屋町", "meal", "定食", False, 1000, 400, 2, 2),          # SD-028 SU-040(LEAST=400→budgetmin)
    (5, 12, 0, 120, "加治屋町", "meal", "定食", False, 1000, 500, 2, 2),           # SD-029 SU-041
    (5, 12, 30, 120, "加治屋町", "meal", "定食", False, 1000, 500, 2, 2),          # SD-030 SU-042(500ちょうど→pass)
    (5, 10, 0, 120, "城山", "drinking", "日本酒", True, 1000, None, 2, 2),         # SD-031 SU-043(budget null)
    (5, 10, 30, 120, "城山", "drinking", "日本酒", True, 1000, 499, 2, 2),         # SD-032 SU-044(実効499→budgetmin)
    (5, 10, 0, 120, "浜町", "activity", "ハイキング", False, 1000, None, 2, 2),    # SD-033 SU-045
    (5, 10, 30, 120, "浜町", "activity", "ハイキング", False, 1000, None, 2, 2),   # SD-034 SU-046(双方null→pass)
    # -- EX-018..020 人数境界(1対1検索の2∈[min,max])
    (5, 20, 0, 180, "天文館", "drinking", "立ち飲み", True, 1000, 3000, 2, 2),     # SD-035 SU-047
    (5, 20, 30, 180, "天文館", "drinking", "立ち飲み", True, 1000, 3000, 3, 4),    # SD-036 SU-048(3/4→people)
    (5, 21, 0, 120, "呉服町", "meal", "寿司", False, 1000, 4000, 1, 1),            # SD-037 SU-049(1/1)
    (5, 21, 30, 120, "呉服町", "meal", "寿司", False, 1000, 4000, 2, 2),           # SD-038 SU-050(2∉[1,1]→people)
    (5, 22, 0, 120, "天文館通り", "activity", "カラオケ", False, 1000, 2500, 2, 2),# SD-039 SU-051
    (5, 22, 30, 120, "天文館通り", "activity", "カラオケ", False, 1000, 2500, 2, 4), # SD-040 SU-052(2∈[2,4]→pass)
    # -- EX-021/022 競合クローズ素材(SU-053の2 intents×SU-054。SD-041×SD-042はself_pair)
    (1, 21, 0, 150, "天文館", "drinking", "ハイボール", True, 1000, 3000, 2, 2),   # SD-041 SU-053
    (1, 22, 30, 150, "天文館", "drinking", "ハイボール", True, 1000, 3000, 2, 2),  # SD-042 SU-053(22:30-翌1:00・日跨ぎ)
    (1, 21, 30, 150, "天文館", "drinking", "ハイボール", True, 1000, 3000, 2, 2),  # SD-043 SU-054
    # -- EX-023/024 カテゴリ不一致(同時間・同地域でcategory違い)
    (2, 18, 0, 120, "呉服町", "drinking", "ワイン", True, 1000, 3000, 2, 2),       # SD-044 SU-055
    (2, 18, 30, 120, "呉服町", "meal", "焼肉", False, 1000, 3000, 2, 2),           # SD-045 SU-056
    (3, 12, 0, 120, "加治屋町", "activity", "映画", False, 1000, 2000, 2, 2),      # SD-046 SU-057
    (3, 12, 30, 120, "加治屋町", "drinking", "焼酎", True, 1000, 2000, 2, 2),      # SD-047 SU-058
    # -- EX-025 自己重複集合クラスタ(SU-059の2 intentsを含む4 intents・min≧3)
    (1, 20, 0, 180, "天文館", "meal", "しゃぶしゃぶ", False, 1000, 4000, 3, 4),    # SD-048 SU-059
    (1, 20, 30, 180, "天文館", "meal", "しゃぶしゃぶ", False, 1000, 4000, 3, 4),   # SD-049 SU-059(同ユーザー)
    (1, 21, 0, 180, "天文館", "meal", "しゃぶしゃぶ", False, 1000, 4000, 3, 4),    # SD-050 SU-060
    (1, 21, 30, 180, "天文館", "meal", "しゃぶしゃぶ", False, 1000, 4000, 3, 4),   # SD-051 SU-061
    # -- EX-026 通知順序クラスタ(同Bucket・同地域・同カテゴリのmin≧3×6件)
    (2, 20, 30, 180, "天文館", "drinking", "居酒屋", True, 1000, 3500, 3, 4),      # SD-052 SU-062
    (2, 20, 30, 180, "天文館", "drinking", "居酒屋", True, 1000, 3500, 3, 4),      # SD-053 SU-063
    (2, 20, 30, 180, "天文館", "drinking", "居酒屋", True, 1000, 3500, 3, 4),      # SD-054 SU-064
    (2, 20, 30, 180, "天文館", "drinking", "居酒屋", True, 1000, 3500, 3, 4),      # SD-055 SU-065
    (2, 20, 30, 180, "天文館", "drinking", "居酒屋", True, 1000, 3500, 3, 4),      # SD-056 SU-066
    (2, 20, 30, 180, "天文館", "drinking", "居酒屋", True, 1000, 3500, 3, 4),      # SD-057 SU-067
    # -- EX-027 部分YESクラスタ(4人集合・相互交差で全ペアL1 pass)
    (3, 18, 0, 180, "天文館通り", "meal", "海鮮丼", False, 1000, 4000, 3, 4),      # SD-058 SU-068
    (3, 18, 30, 180, "天文館通り", "meal", "海鮮丼", False, 1000, 4000, 3, 4),     # SD-059 SU-069
    (3, 19, 0, 180, "天文館通り", "meal", "海鮮丼", False, 1000, 4000, 3, 4),      # SD-060 SU-070
    (3, 19, 30, 180, "天文館通り", "meal", "海鮮丼", False, 1000, 4000, 3, 4),     # SD-061 SU-071
    # -- EX-028 部分YESクラスタ(3人集合)
    (4, 21, 0, 150, "加治屋町", "activity", "カラオケ", False, 1000, 3000, 3, 4),  # SD-062 SU-072
    (4, 21, 30, 150, "加治屋町", "activity", "カラオケ", False, 1000, 3000, 3, 4), # SD-063 SU-073
    (4, 22, 0, 150, "加治屋町", "activity", "カラオケ", False, 1000, 3000, 3, 4),  # SD-064 SU-074
]

# _EX_ROWSと同列の並行リスト(ユーザー・ex_group表記)。要素数=64
_EX_USERS: list[str] = [
    "SU-010", "SU-010", "SU-011", "SU-011", "SU-012", "SU-012",
    "SU-021", "SU-001", "SU-022", "SU-002",
    "SU-023", "SU-024", "SU-025", "SU-026", "SU-027", "SU-028",
    "SU-029", "SU-030", "SU-031", "SU-032", "SU-033", "SU-034",
    "SU-035", "SU-036", "SU-037", "SU-038", "SU-039", "SU-040",
    "SU-041", "SU-042", "SU-043", "SU-044", "SU-045", "SU-046",
    "SU-047", "SU-048", "SU-049", "SU-050", "SU-051", "SU-052",
    "SU-053", "SU-053", "SU-054", "SU-055", "SU-056", "SU-057", "SU-058",
    "SU-059", "SU-059", "SU-060", "SU-061",
    "SU-062", "SU-063", "SU-064", "SU-065", "SU-066", "SU-067",
    "SU-068", "SU-069", "SU-070", "SU-071", "SU-072", "SU-073", "SU-074",
]
_EX_GROUPS: list[str] = [
    "EX-001", "EX-001", "EX-002", "EX-002", "EX-003", "EX-003",
    "EX-004", "EX-004", "EX-005", "EX-005",
    "EX-006", "EX-006", "EX-007", "EX-007", "EX-008", "EX-008",
    "EX-009", "EX-009", "EX-010", "EX-010", "EX-011", "EX-011",
    "EX-012", "EX-012", "EX-013", "EX-013", "EX-014", "EX-014",
    "EX-015", "EX-015", "EX-016", "EX-016", "EX-017", "EX-017",
    "EX-018", "EX-018", "EX-019", "EX-019", "EX-020", "EX-020",
    "EX-021", "EX-022", "EX-021,EX-022", "EX-023", "EX-023", "EX-024", "EX-024",
    "EX-025", "EX-025", "EX-025", "EX-025",
    "EX-026", "EX-026", "EX-026", "EX-026", "EX-026", "EX-026",
    "EX-027", "EX-027", "EX-027", "EX-027", "EX-028", "EX-028", "EX-028",
]

def _intentional_slots(base: datetime) -> list[dict]:
    """意図的配置intents(SD-001〜064)を組み立てる(乱数不使用・D10)。

    消費集計(残余割付が差し引く前提値・unit試験の期待値):
    時間帯=昼10/夕14/夜30/その他10・カテゴリ=drinking 25/meal 21/
    activity 18・志向=1対1 46/グループ 18。
    """
    if not (len(_EX_ROWS) == len(_EX_USERS) == len(_EX_GROUPS) == EX_SLOT_COUNT):
        raise AssertionError("EX素材テーブルの要素数不一致")
    radii = _boundary_radii()
    radius_map = {"HI_A": radii[0], "HI_B": radii[1], "LO_A": radii[2], "LO_B": radii[3]}
    intents: list[dict] = []
    for i, row in enumerate(_EX_ROWS):
        day, hh, mm, dur_min, place, cat, sec, alc, radius, budget, pmin, pmax = row
        radius = radius_map.get(radius, radius)
        start = _at(base, day, hh, mm)
        end = None if dur_min is None else start + timedelta(minutes=dur_min)
        bucket = time_bucket_of(start.hour)
        soft = SOFT_CONSTRAINTS[cat][i % len(SOFT_CONSTRAINTS[cat])]
        raw = RAW_TEMPLATES[cat][i % len(RAW_TEMPLATES[cat])].format(
            time_prefix=TIME_PREFIX[bucket], place=place, secondary=sec, soft=soft
        )
        intents.append({
            "id": f"SD-{i + 1:03d}",
            "user": _EX_USERS[i],
            "ex_group": _EX_GROUPS[i],
            "raw_text": raw,
            "structured": {
                "category": {"primary": cat, "secondary": sec},
                "alcohol_involved": alc,
                "time": {"start": start, "end": end},
                "location": {"name": place, "radius_m": radius},
                "budget": {"max": budget},
                "participants": {"min": pmin, "max": pmax},
                "soft_constraints": [soft],
                "visibility": "hidden_until_match",
                "notification_level": "proposals_only",
            },
        })
    return intents
```

(§2.3の続き・全文その3/3: users・残余スロット・build_seed・main)

```python
# 18-19歳ユーザー(残余スロットのalcohol割付でFalse強制する集合・D6)
UNDERAGE_USERS = {u for u, _, _ in UNDERAGE_BIRTHS}

def _build_users(base_date: date, rng: random.Random) -> list[dict]:
    """合成ユーザー100人(SU-001〜100・10 §2・design §2.4)。

    SU-001〜008は18-19歳(UNDERAGE_BIRTHS・base+7日でも18-19歳を保つ
    マージン)。SU-009〜100は20〜50歳から決定的選択。display_nameは
    仮名+番号(実在しない氏名・10 §2)。連絡先は実装契約に存在しない
    (users: display_name/birth_date/profile.bioのみ)ため持たない。
    """
    users: list[dict] = []
    for i in range(1, USER_COUNT + 1):
        uid = f"SU-{i:03d}"
        if i <= len(UNDERAGE_BIRTHS):
            _, years, months = UNDERAGE_BIRTHS[i - 1]
            birth = _minus_ym(base_date, years, months)
        else:
            birth = _minus_ym(base_date, rng.randint(20, 50), rng.randint(0, 11))
        users.append({
            "id": uid,
            "display_name": f"合成{NAME_POOL[i % len(NAME_POOL)]}{i:03d}",
            "birth_date": birth,
            "profile": {"bio": f"テスト用合成ユーザー({uid})"},
        })
    return users

def _max_remainder(total: int, weights: list[int]) -> list[int]:
    """最大剰余法(合計が厳密にtotal・同点は元順序・決定的)。"""
    s = sum(weights)
    if s == 0 or total == 0:
        return [0] * len(weights)
    raw = [w * total / s for w in weights]
    out = [int(math.floor(x)) for x in raw]
    rest = total - sum(out)
    order = sorted(range(len(weights)), key=lambda i: (raw[i] - out[i], -i), reverse=True)
    for k in range(rest):
        out[order[k]] += 1
    return out

def _assign_users(remaining: int, ex_counts: Counter) -> list[str]:
    """残余スロットのユーザー割付(決定的・SU-001から順に循環)。

    EX素材を持つユーザーは残余最大2件・それ以外は最大3件 — ユーザーごとの
    総intent数をActive上限5件未満に保つ(08 §5.4・参照値14。ws-3の
    staging投入がrate limitに触れない条件)。容量: EX素材ユーザー59人×2+
    その他41人×3=241 >= 残余236(300件時)。
    """
    caps = {
        f"SU-{i:03d}": (2 if ex_counts.get(f"SU-{i:03d}", 0) > 0 else 3)
        for i in range(1, USER_COUNT + 1)
    }
    counts: dict[str, int] = dict.fromkeys(caps, 0)
    assignments: list[str] = []
    while len(assignments) < remaining:
        progressed = False
        for u in caps:
            if len(assignments) >= remaining:
                break
            if counts[u] < caps[u]:
                assignments.append(u)
                counts[u] += 1
                progressed = True
        if not progressed:
            raise ValueError("残余スロットがユーザーキャップ内に収まらない")
    return assignments

def _build_remaining_intent(
    base: datetime,
    rng: random.Random,
    idx: int,
    sd_id: str,
    user: str,
    bucket: str,
    cat: str,
    ori: str,
) -> dict:
    """残余スロット1件を決定的に組み立てる(rngはこの関数順に消費・D10)。

    対象日はD+1〜D+5のラウンドロビン・開始hourはバケット内ラウンドロビン
    (偏りを避ける決定的配分)・分と語彙はrng選択。
    """
    hour = (
        rng.choice([12, 13]) if bucket == "day"
        else rng.choice([18, 19]) if bucket == "evening"
        else rng.choice([20, 21, 22]) if bucket == "night"
        else OTHER_BUCKET_HOURS[idx % len(OTHER_BUCKET_HOURS)]
    )
    start = _at(base, DAY_OFFSETS[idx % len(DAY_OFFSETS)], hour, rng.choice([0, 15, 30, 45]))
    if rng.random() < END_NULL_RATIO:
        end = None  # end null(start+3h補完)のカバレッジ
    else:
        end = start + timedelta(minutes=rng.choice([60, 90, 120, 180]))
    place = rng.choice(list(LOCATIONS))
    secondary = rng.choice(CATEGORY_SECONDARY[cat])
    # alcohol割付(規則7・design §2.4): drinking→常にtrue・activity→常にfalse・
    # meal→18-19歳ユーザーは常にfalse(作成拒否相当を避ける)・
    # それ以外は確率MEAL_ALCOHOL_RATIO(20歳以上のみtrueとなり規約を満たす)
    if cat == "drinking":
        alcohol = True
    elif cat == "activity" or user in UNDERAGE_USERS:
        alcohol = False
    else:
        alcohol = rng.random() < MEAL_ALCOHOL_RATIO
    if ori == "group":
        pmin, pmax = 3, rng.choice([3, 4])  # グループ=min≧3(design §2.4)
    else:
        pmin, pmax = rng.choice([(2, 2), (2, 3), (2, 4)])  # 1対1=それ以外
    budget = rng.choice(BUDGET_CHOICES)
    radius = rng.choice(RADIUS_CHOICES)
    soft = rng.sample(SOFT_CONSTRAINTS[cat], k=rng.choice([1, 2]))
    raw = rng.choice(RAW_TEMPLATES[cat]).format(
        time_prefix=TIME_PREFIX[bucket], place=place, secondary=secondary,
        soft=soft[0],
    )
    return {
        "id": sd_id,
        "user": user,
        "raw_text": raw,
        "structured": {
            "category": {"primary": cat, "secondary": secondary},
            "alcohol_involved": alcohol,
            "time": {"start": start, "end": end},
            "location": {"name": place, "radius_m": radius},
            "budget": {"max": budget},
            "participants": {"min": pmin, "max": pmax},
            "soft_constraints": soft,
            "visibility": rng.choice(VISIBILITY_CHOICES),
            "notification_level": rng.choice(NOTIFICATION_CHOICES),
        },
    }

def _remaining_slots(
    base: datetime, rng: random.Random, remaining: int, ex_intents: list[dict]
) -> list[dict]:
    """残余スロット(300件時236件)を比率配分+乱数シャッフルで組み立てる。

    意図的配置の消費(§2.3その2のdocstring・_intentional_slots)を先取りした
    残余に10 §2の比率を最大剰余法で適用する。時間帯×カテゴリは直交割付
    (バケット内でカテゴリを配分)・人数志向は残余全体へ配分。300件時の
    残余配分: 時間帯=昼50/夕76/夜90/その他20・カテゴリ=drinking 125/
    meal 99/activity 12・志向=1対1 164/グループ 72。
    """
    consumed_bucket = Counter(
        time_bucket_of(i["structured"]["time"]["start"].astimezone(JST).hour)
        for i in ex_intents
    )
    consumed_cat = Counter(i["structured"]["category"]["primary"] for i in ex_intents)
    consumed_ori = Counter(
        "group" if i["structured"]["participants"]["min"] >= 3 else "one_to_one"
        for i in ex_intents
    )
    time_rem = {b: TIME_TARGETS[b] - consumed_bucket.get(b, 0) for b in BUCKET_ORDER}
    cat_rem = {c: CATEGORY_TARGETS[c] - consumed_cat.get(c, 0) for c in CATEGORY_ORDER}
    ori_rem = {
        "one_to_one": ORIENTATION_TARGETS["one_to_one"] - consumed_ori.get("one_to_one", 0),
        "group": ORIENTATION_TARGETS["group"] - consumed_ori.get("group", 0),
    }
    if min(*time_rem.values(), *cat_rem.values(), *ori_rem.values()) < 0:
        raise AssertionError("意図的配置の消費が分布目標を超過")
    combos: list[tuple[str, str]] = []
    for bucket in BUCKET_ORDER:
        cells = _max_remainder(time_rem[bucket], [cat_rem[c] for c in CATEGORY_ORDER])
        for cat, n in zip(CATEGORY_ORDER, cells):
            combos.extend([(bucket, cat)] * n)
    ori_counts = _max_remainder(remaining, [ori_rem["one_to_one"], ori_rem["group"]])
    ori_list = ["one_to_one"] * ori_counts[0] + ["group"] * ori_counts[1]
    if len(combos) != remaining or len(ori_list) != remaining:
        raise AssertionError("残余割付の総数不一致")
    slots = list(zip(combos, ori_list))
    rng.shuffle(slots)  # D10: 固定シードで決定的
    user_assign = _assign_users(remaining, Counter(i["user"] for i in ex_intents))
    return [
        _build_remaining_intent(
            base, rng, idx, f"SD-{EX_SLOT_COUNT + idx + 1:03d}",
            user_assign[idx], bucket, cat, ori,
        )
        for idx, ((bucket, cat), ori) in enumerate(slots)
    ]

def _resolve_reject(base: datetime) -> list[dict]:
    """reject素材の"D+1T20:00"表記を実日時(JST・ISO文字列)へ解決する。"""
    out: list[dict] = []
    for case in REJECT_CASES:
        c = copy.deepcopy(case)
        token = c["structured_intent"]["time"]["start"]
        d_part, h_part = token.split("T")
        start = _at(base, int(d_part[1:]), int(h_part.split(":")[0]), int(h_part.split(":")[1]))
        c["structured_intent"]["time"]["start"] = _iso(start)
        out.append(c)
    return out

def _iso(dt: datetime) -> str:
    return dt.astimezone(JST).isoformat()

def build_seed(
    base_datetime: datetime, *, seed: int = SEED, count: int = DEFAULT_COUNT
) -> dict:
    """シード定義(内部表現dict)を組み立てる。同一引数→同一出力(design §2.3)。

    生成時にEX-xxxペアケースをreference_l1で判定し、EX_CASESの期待値と
    不一致ならValueError(二重実装の突合・生成側ゲート)。
    """
    if count < COUNT_MIN:
        raise ValueError(
            f"--count は {COUNT_MIN} 以上が必要(意図的配置{EX_SLOT_COUNT}件+残余1件)"
        )
    base = base_datetime.astimezone(JST)
    rng = random.Random(seed)
    users = _build_users(base.date(), rng)
    users_by_id = {u["id"]: u for u in users}
    ex_intents = _intentional_slots(base)
    intents = ex_intents + _remaining_slots(base, rng, count - len(ex_intents), ex_intents)

    verdicts = judge_all(intents, users_by_id, now=base)
    breakdown = Counter(r for ok, r in verdicts.values() if not ok)
    n_pass = sum(1 for ok, _ in verdicts.values() if ok)

    by_id = {i["id"]: i for i in intents}
    for case in EX_CASES:
        if case["kind"] != "pair":
            continue
        a, b = (by_id[i] for i in case["intents"])
        ok, reason = judge_pair(a, b, users=users_by_id, now=base)
        exp_pass = case["expected"] == "pass"
        if ok != exp_pass or (not exp_pass and reason != case["fail_reason"]):
            raise ValueError(
                f"{case['id']}: 期待不一致 expected={case['expected']}/{case['fail_reason']} "
                f"actual={'pass' if ok else 'fail'}/{reason}"
            )
    aggregate = {
        "pair_total": len(verdicts),
        "layer1_pass": n_pass,
        "layer1_fail": len(verdicts) - n_pass,
        "fail_breakdown": {r: breakdown.get(r, 0) for r in FAIL_PRIORITY},
    }
    return {
        "meta": {
            "status": "draft",
            "created": SEED_CREATED,
            "generator": "latch.testassets.generator",
            "base_datetime": _iso(base),
            "random_seed": seed,
            "user_count": len(users),
            "intent_count": len(intents),
            "pair_total": len(verdicts),
            "time_bucket_targets": dict(TIME_TARGETS),
            "category_targets": dict(CATEGORY_TARGETS),
            "orientation_targets": dict(ORIENTATION_TARGETS),
        },
        "users": users,
        "intents": intents,
        "intended_reject_cases": _resolve_reject(base),
        "expectations": {"cases": EX_CASES, "aggregate": aggregate},
    }

def _to_plain(value: object) -> object:
    """datetime/date→ISO文字列へ再帰変換(YAML決定化・JST表記)。"""
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}  # type: ignore[union-attr]
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    return value

def write_yaml(seed: dict, out_path: str) -> None:
    """YAML書き出し(挿入順序固定・allow_unicode・バイト同一性)。"""
    text = yaml.safe_dump(
        _to_plain(seed), allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    Path(out_path).write_text(text, encoding="utf-8")

def main(argv: list[str] | None = None) -> int:
    """CLI: make seed-generate ARGS="--base-datetime 2026-10-04T11:00:00+09:00"。"""
    parser = argparse.ArgumentParser(description="決定的シード生成器(M4 ws-2)")
    parser.add_argument(
        "--base-datetime", required=True,
        help="基準日時(タイムゾーン付きISO8601。例: 2026-10-04T11:00:00+09:00)",
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    base = datetime.fromisoformat(args.base_datetime)
    if base.tzinfo is None:
        raise SystemExit("--base-datetime はタイムゾーン付きISO8601で指定してください")
    seed = build_seed(base, seed=args.seed, count=args.count)
    write_yaml(seed, args.out)
    agg = seed["expectations"]["aggregate"]
    print(
        f"generated {args.out}: intents={len(seed['intents'])} "
        f"users={len(seed['users'])} pair_total={agg['pair_total']} "
        f"layer1_pass={agg['layer1_pass']}"
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

(実装時の注記: 300件フル生成には全ペアL1判定(44,850組)が含まれるが、純Pythonで1〜2秒程度でありunit試験でフル生成を2回実行しても実用上問題ない。)

### §2.4 `backend/src/latch/testassets/verify.py`(全文)

```python
"""m4-seed.yaml の機械検査(M4 ws-2・design §4-4・g2-plan §10方式の拡張)。

使い方: make seed-verify ARGS="--path ../docs/testassets/m4-seed.yaml"
検査項目(design §4-4):
(a) 構造(id一意・必須3フィールド・時刻D+1〜D+5内・raw_text300字以内・
    participants値域1-4・visibility/notification_level列挙内・users構造)
(b) 分布比率(時間帯60/90/120/30・カテゴリ150/120/30・志向210/90・
    18-19歳8人とbase+7日でも不変のマージン・visibility/notification全値)
(c) 意図的配置の成立(EX-xxxペアケースをreference_l1で再判定してexpected
    と突合・クラスタの構成属性検査・reject素材5件の構造)
(d) aggregate照合(reference_l1で全ペア独立再計算しYAML集計値と一致)
(e) 地名が許可リスト内
違反はすべてerrorsへ集めて一覧出力し、1件でもあればexit 1。
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from latch.testassets.reference_l1 import (
    FAIL_PRIORITY,
    JST,
    age_years_at,
    judge_all,
    judge_pair,
)
from latch.testassets.vocab import LOCATIONS, time_bucket_of

BUCKET_ORDER = ["day", "evening", "night", "other"]
CATEGORY_ORDER = ["drinking", "meal", "activity"]
TIME_TARGETS = {"day": 60, "evening": 90, "night": 120, "other": 30}
CATEGORY_TARGETS = {"drinking": 150, "meal": 120, "activity": 30}
ORIENTATION_TARGETS = {"one_to_one": 210, "group": 90}
VISIBILITY_VALUES = ["hidden_until_match", "summary_only"]
NOTIFICATION_VALUES = ["proposals_only", "nearby_also", "muted"]
USER_COUNT = 100
INTENT_COUNT = 300
UNDERAGE_COUNT = 8
REJECT_COUNT = 5
SEED_VALUE = 42

def _p(s: str) -> datetime:
    return datetime.fromisoformat(s)

def verify(path: str) -> list[str]:
    """検査を実行し違反メッセージの一覧を返す(空=合格)。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    meta = data["meta"]
    users = data["users"]
    intents = data["intents"]
    rejects = data["intended_reject_cases"]
    cases = data["expectations"]["cases"]
    aggregate = data["expectations"]["aggregate"]
    by_id = {i["id"]: i for i in intents}
    users_by_id = {u["id"]: u for u in users}
    base = _p(meta["base_datetime"])
    errs: list[str] = []

    # (a) 構造
    ids = [i["id"] for i in intents] + [u["id"] for u in users]
    if len(ids) != len(set(ids)):
        errs.append("id重複")
    if meta["intent_count"] != INTENT_COUNT or len(intents) != INTENT_COUNT:
        errs.append(f"intent数: meta={meta['intent_count']} 実測={len(intents)}")
    if meta["user_count"] != USER_COUNT or len(users) != USER_COUNT:
        errs.append(f"user数: meta={meta['user_count']} 実測={len(users)}")
    if meta["random_seed"] != SEED_VALUE:
        errs.append(f"random_seed={meta['random_seed']}(固定値42でない)")
    for u in users:
        if not u["display_name"] or u["birth_date"] is None:
            errs.append(f"{u['id']}: users契約欠落(display_name/birth_date)")
        if "profile" not in u:
            errs.append(f"{u['id']}: profile欠落")
    for i in intents:
        s = i["structured"]
        if s["category"]["primary"] is None or s["time"]["start"] is None or s["location"]["name"] is None:
            errs.append(f"{i['id']}: 必須3フィールド(category/time.start/location)欠落")
            continue
        if not (1 <= len(i["raw_text"]) <= 300):
            errs.append(f"{i['id']}: raw_text字数外({len(i['raw_text'])})")
        start = _p(s["time"]["start"])
        delta_days = (start - base).total_seconds() / 86400
        if not (0.9 < delta_days < 6.0):  # D+1〜D+5(design §2.3)
            errs.append(f"{i['id']}: 時刻がD+1〜D+5外(delta={delta_days:.2f}日)")
        if s["time"]["end"] is not None and _p(s["time"]["end"]) <= start:
            errs.append(f"{i['id']}: end<=start")
        pmin, pmax = s["participants"]["min"], s["participants"]["max"]
        if not (1 <= pmin <= 4 and 1 <= pmax <= 4 and pmin <= pmax):
            errs.append(f"{i['id']}: participants値域外({pmin}/{pmax})")
        if s["visibility"] not in VISIBILITY_VALUES:
            errs.append(f"{i['id']}: visibility値外")
        if s["notification_level"] not in NOTIFICATION_VALUES:
            errs.append(f"{i['id']}: notification_level値外")
        b = s["budget"]
        if b is not None and b["max"] is not None and b["max"] < 0:
            errs.append(f"{i['id']}: budget負値")
        r = s["location"]["radius_m"]
        if r is not None and r < 1:
            errs.append(f"{i['id']}: radius_m<1")
    # case→intent参照整合(EX素材が存在するか)
    for c in cases:
        for sd in c["intents"]:
            if sd not in by_id:
                errs.append(f"{c['id']}: 参照先{sd}が存在しない")

    # (b) 分布比率
    bucket_count = Counter(
        time_bucket_of(_p(i["structured"]["time"]["start"]).astimezone(JST).hour)
        for i in intents
    )
    cat_count = Counter(i["structured"]["category"]["primary"] for i in intents)
    ori_count = Counter(
        "group" if i["structured"]["participants"]["min"] >= 3 else "one_to_one"
        for i in intents
    )
    for name, counts, targets in [
        ("時間帯", bucket_count, TIME_TARGETS),
        ("カテゴリ", cat_count, CATEGORY_TARGETS),
        ("人数志向", ori_count, ORIENTATION_TARGETS),
    ]:
        for key, target in targets.items():
            if counts.get(key, 0) != target:
                errs.append(f"{name}分布: {key} 実測{counts.get(key, 0)} vs 目標{target}")
    underage_ages = Counter(
        age_years_at(_p(u["birth_date"]).date(), base.astimezone(JST).date())
        for u in users
        if age_years_at(_p(u["birth_date"]).date(), base.astimezone(JST).date()) < 20
    )
    n_underage = sum(underage_ages.values())
    if n_underage != UNDERAGE_COUNT:
        errs.append(f"18-19歳ユーザー: 実測{n_underage} vs 目標{UNDERAGE_COUNT}")
    for u in users:
        birth = _p(u["birth_date"]).date()
        if age_years_at(birth, base.astimezone(JST).date()) < 20:
            # D6: base+7日(設計§2.3のマージン上限+2日)でも18-19歳を保つ
            if age_years_at(birth, (base + timedelta(days=7)).astimezone(JST).date()) >= 20:
                errs.append(f"{u['id']}: 18-19歳マージン不足(base+7日で20歳)")
    for field, values in [("visibility", VISIBILITY_VALUES), ("notification_level", NOTIFICATION_VALUES)]:
        seen = {i["structured"][field] for i in intents}
        for v in values:
            if v not in seen:
                errs.append(f"{field}={v}が1件もない(全値カバレッジ)")
    # 18-19歳ユーザーの全intentがalcohol false(design §2.4・規則7の例外)
    for i in intents:
        if i["user"] in {u["id"] for u in users if age_years_at(_p(u["birth_date"]).date(), base.astimezone(JST).date()) < 20}:
            if i["structured"]["alcohol_involved"]:
                errs.append(f"{i['id']}: 18-19歳ユーザー×alcohol true")

    # (c) 意図的配置の成立(EX突合)
    for c in cases:
        if c["kind"] == "pair":
            ok, reason = judge_pair(
                by_id[c["intents"][0]], by_id[c["intents"][1]],
                users=users_by_id, now=base,
            )
            exp_pass = c["expected"] == "pass"
            if ok != exp_pass:
                errs.append(f"{c['id']}: 判定不一致 expected={c['expected']} actual={'pass' if ok else 'fail'}")
            elif not exp_pass and reason != c["fail_reason"]:
                errs.append(f"{c['id']}: fail_reason不一致 expected={c['fail_reason']} actual={reason}")
        elif c["kind"] == "group_cluster":
            members = [by_id[sd] for sd in c["intents"]]
            exp = c["expected"]
            if exp.get("distinct_users") is not None and len({m["user"] for m in members}) != exp["distinct_users"]:
                errs.append(f"{c['id']}: distinct_users不一致")
            if exp.get("all_min_ge_3") and not all(m["structured"]["participants"]["min"] >= 3 for m in members):
                errs.append(f"{c['id']}: min>=3不成立")
            if exp.get("all_pairs_pass_l1"):
                for x in range(len(members)):
                    for y in range(x + 1, len(members)):
                        ok, reason = judge_pair(members[x], members[y], users=users_by_id, now=base)
                        if not ok:
                            errs.append(f"{c['id']}: 全ペアpass違反({members[x]['id']}×{members[y]['id']}: {reason})")
            if exp.get("same_bucket"):
                for m in members:
                    if time_bucket_of(_p(m["structured"]["time"]["start"]).astimezone(JST).hour) != exp["same_bucket"]:
                        errs.append(f"{c['id']}: 同Bucket違反({m['id']})")
            if exp.get("same_place") and len({m["structured"]["location"]["name"] for m in members}) != 1:
                errs.append(f"{c['id']}: 同地域違反")
            if exp.get("same_category") and len({m["structured"]["category"]["primary"] for m in members}) != 1:
                errs.append(f"{c['id']}: 同カテゴリ違反")
        else:
            errs.append(f"{c['id']}: 不明なkind({c['kind']})")
    if len(rejects) != REJECT_COUNT:
        errs.append(f"intended_reject_cases: 実測{len(rejects)} vs 目標{REJECT_COUNT}")
    for rc in rejects:
        birth = _p(users_by_id[rc["user"]]["birth_date"]).date()
        if age_years_at(birth, base.astimezone(JST).date()) >= 20:
            errs.append(f"{rc['id']}: reject素材ユーザーが20歳以上")
        if not rc["structured_intent"]["alcohol_involved"]:
            errs.append(f"{rc['id']}: alcohol_involved=trueでない")
        if rc["expected_status"] != 422 or rc["expected_code"] != "UNDER_AGE":
            errs.append(f"{rc['id']}: expected 422/UNDER_AGEでない")

    # (d) aggregate照合(reference_l1で全ペア独立再計算)
    verdicts = judge_all(intents, users_by_id, now=base)
    recalc = Counter(r for ok, r in verdicts.values() if not ok)
    n_pass = sum(1 for ok, _ in verdicts.values() if ok)
    checks = [
        ("pair_total", aggregate["pair_total"], len(verdicts)),
        ("layer1_pass", aggregate["layer1_pass"], n_pass),
        ("layer1_fail", aggregate["layer1_fail"], len(verdicts) - n_pass),
    ]
    for name, declared, actual in checks:
        if declared != actual:
            errs.append(f"aggregate.{name}不一致: 記載{declared} vs 再計算{actual}")
    if aggregate["pair_total"] != INTENT_COUNT * (INTENT_COUNT - 1) // 2:
        errs.append(f"aggregate.pair_total={aggregate['pair_total']}(C(n,2)と不一致)")
    for reason in FAIL_PRIORITY:
        if aggregate["fail_breakdown"].get(reason, 0) != recalc.get(reason, 0):
            errs.append(
                f"fail_breakdown.{reason}不一致: 記載{aggregate['fail_breakdown'].get(reason, 0)}"
                f" vs 再計算{recalc.get(reason, 0)}"
            )

    # (e) 地名許可リスト
    for i in intents:
        if i["structured"]["location"]["name"] not in LOCATIONS:
            errs.append(f"{i['id']}: 地名が許可リスト外")

    return errs

def main(argv: list[str] | None = None) -> int:
    """CLI: make seed-verify ARGS="--path ../docs/testassets/m4-seed.yaml"。"""
    parser = argparse.ArgumentParser(description="m4-seed.yaml機械検査(M4 ws-2)")
    parser.add_argument("--path", default="../docs/testassets/m4-seed.yaml")
    args = parser.parse_args(argv)
    errs = verify(args.path)
    if errs:
        print(f"FAIL: {len(errs)}件の違反")
        for e in errs:
            print(f"  - {e}")
        return 1
    print(f"OK: {args.path} の機械検査に合格(a構造/b分布/c意図的配置/d aggregate照合/e地名)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

### §2.5 `Makefile` 追記分(既存ターゲット・.PHONY行に追記のみ)

`.PHONY` の先頭行へ `seed-generate seed-verify` を追記する:

```makefile
.PHONY: setup up down ps logs lint test test-ci migrate geo-download geo-import geo-verify g1-gate embed-smoke jev-smoke g2-gate seed-generate seed-verify
```

ファイル末尾(`g2-gate`ターゲットの後)へ追記する:

```makefile
seed-generate: ## M4シード生成(決定的。ローカル・DB不要。ARGS="--base-datetime 2026-10-04T11:00:00+09:00" — tz付きISO必須。--count既定300)
	cd backend && uv run python -m latch.testassets.generator $(ARGS)

seed-verify: ## M4シード機械検査(構造・分布・意図的配置・aggregate照合・地名。ローカル・DB不要)
	cd backend && uv run python -m latch.testassets.verify $(ARGS)
```

(注: 既存の `g2-gate` と同様、makeは`--`以降を転送しないため `ARGS=` で渡す。)

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **「決定的」が実行環境や実行順序で崩れる**(design §2.3) — dict反復順序・`rng`消費順序・`created`に実行日を使うことによる非決定性。意図的配置は乱数不使用・残余は`random.Random(42)`1インスタンスへ集約・`SEED_CREATED`は固定定数(D10/D12)→ Task 3 unit(`test_generation_is_byte_identical`: 同一引数2回のバイト同一+`--count`違いもそれぞれ決定的)
2. **意図的配置の消費が分布目標を超えて残余割付が負になる/超えた分が黙って目標から漏れる** — EX素材64件の時間帯・カテゴリ・志向消費を先取りせず残余を比率配分すると300件時の総計が10 §2からズレる。`_remaining_slots`は消費を差し引いた後assertし、300件時の正確な配分(60/90/120/30・150/120/30・210/90)をunitがピン留めする → Task 3 unit(`test_time_bucket_targets`・`test_category_targets`・`test_orientation_targets`)
3. **EX素材ユーザーが境界条件を壊す**(同一ユーザー化による意図しないself_pair・18-19歳ユーザーへのalcohol true割当・1人あたりActive上限超過) — 境界EX(EX-006〜020)の2 intentsは必ず別ユーザー・`UNDERAGE_USERS`は残余でもalcohol false強制・`_assign_users`のキャップ(総intent 4件未満)はws-3のstaging投入をrate limitから守る → Task 3 unit(`test_underage_intents_all_alcohol_false`)・Task 5(seed-verify(c)の全EX突合+`(b)`の18-19歳検査)。`SD-041×SD-042`(SU-053内)と`SD-048×SD-049`(SU-059内)は**仕様どおりのself_pair**でaggregateに含まれる(§2.3テーブルコメント)
4. **end null・radius nullの補完条件がSQLと逆に実装される**(LAYER1_WHEREとの乖離) — end nullはstart+3hで**交差を広げる**・radius nullは1000で**判定半径を広げる**。逆に実装するとEX-006/012/013と全ペアaggregateがズレる → Task 2 unit(`test_pass_end_null_completion`・`test_fail_distance_far`のnullケース)+Task 5(aggregate照合)。時間境界は隣接=endでfail・交差1分でpass(狭い側ではない)をunitでピン留め
5. **seed-verifyが壊れたYAMLを通す/参照実装と生成器が同じバグを共有する** — verifyはYAMLを読んで**独立に**再計算する(g2-plan §10方式)。生成器のEX突合は生成時のみ走るため、YAMLを手で壊した場合にverifyが検出できることを、故意の破壊試験(§7検証手順6)で確認する。二重実装の独立性は、reference_l1がSQL(`layer1.py` LAYER1_WHERE)を1対1対応で写したこと+SQLピンunit(`02#9`の既存ci資産)で担保する

## 4. スコープ(作成・変更するファイル一覧)

作成(§0のbasename機械確認済み・design §3.1):

```text
backend/src/latch/testassets/__init__.py             (Task 1。空ファイル)
backend/src/latch/testassets/vocab.py                (Task 1。§2.1)
backend/src/latch/testassets/reference_l1.py         (Task 2。§2.2)
backend/src/latch/testassets/generator.py            (Task 3。§2.3)
backend/src/latch/testassets/verify.py               (Task 4。§2.4)
backend/tests/unit/testassets/test_reference_l1.py   (Task 2。15試験)
backend/tests/unit/testassets/test_generator.py      (Task 3。17試験)
docs/testassets/m4-seed.yaml                         (Task 5。seed-generateの生成物・300件)
docs/testassets/m4-seed-plan.md                      (Task 6。生成規則・分布実測・検証方式の記録)
docs/plans/M4/ws-2-report.md                         (Task 8。報告ファイル)
```

変更:

| ファイル | 変更内容 | Task |
|---|---|---|
| `Makefile` | `.PHONY`へ`seed-generate seed-verify`追記+ターゲット2件追記(§2.5) | 5 |
| `docs/testassets/README.md` | 新資産の位置づけ+既存3資産との関係整理(design §1.3・§8の雛形)を追記 | 7 |

`backend/tests/unit/testassets/` ディレクトリは新規作成するが `__init__.py` は置かない(§0)。

生成されるが**コミットしない**もの: `backend/.venv/`・`__pycache__/`。`docs/testassets/results/` への置き場は本単位では原則不要(seed-verify出力は報告書へ記録・必要に応じて `docs/testassets/results/m4-seed-verify-<日付>.txt` に保存してよい — 置いた場合はコミットする・10 §5)。

## 5. 禁止(触ってはいけないもの・スコープ外と判断する基準)

- **絶対に編集しない**(design §3.2の硬い制約): `docs/testassets/g1-parser-struct.yaml`・`docs/testassets/g1-alcohol.yaml`・`docs/testassets/g2-jev-goldset.yaml`・`docs/testassets/g2-jev-goldset-plan.md`・`docs/testassets/g2-jev-acceptance-criteria.md`。既存3資産は「評価ペア500件以上・飲酒判定30件以上・Parser35件」を既に充足しており、ペア構成を変えるとG2評価結果の前提が崩れる(g2-plan §7)
- **backend既存コードへの変更は一切なし**: `backend/src/latch/testassets/` 新規追加とMakefile追記のみ。`intents`・`users`・`matching`(layer1〜4含む)・`worker`・`g1gate`・`g2gate`・`settings.py`・`alembic/`・`pyproject.toml`・`uv.lock`・`.env`・`.env.example` に触れない(arch test・依存追加もなし)
- **docker系コマンド実行禁止**(§0): `make test-ci`・`make up`・`make down`・`make migrate`・`docker compose build` など。共有ci-dbに触れない(ws-1並行)
- **実API呼び出し禁止**: `make g1-gate`・`make g2-gate`・`make embed-smoke`・`make jev-smoke`。生成器はParser/Jev/Embeddingを呼ばない(テンプレート生成・LLM不使用)
- **geo結合検査(design §4-5)はスコープ外**(supervisor指示): 正転ジオコーディングでの全地名ヒット確認は実施しない。地名はg2実績9地名に限定することで正転ヒット実績が担保される(design §2.4)
- **staging投入・DB突合(ws-3)・拡張生成の実行(ws-6)は実施しない**(design §3.3・§4-6): 本単位はYAML資産と検証スクリプトまで。ws-3ハーネス・ws-6負荷ツールからの消費はそれぞれの単位で実施する
- **Layer 2以降の期待値を書かない**(design §2.2): HNSW上位K_v=50・cheap_score順位・提案数はembedding実値とJevスタブに依存し決定的期待を書くと実装結合になる。期待値の機械突合はLayer 1まで
- **embedding実系統を呼ばない**: staging投入時のembeddingはstubが既定(design §2.3・supervisor訂正)。実系統(gemini-embedding-001)での付与はコスト発生のためws-3設計時にユーザーの承認を経る
- **スコープ外と判断する基準**(必要になったと感じても作らない):
  - `m4-seed.yaml`の手編集・手作りの300件YAML → 生成器の実行成果物のみ(手編集が必要になったら生成規則を直す)
  - EXケースの50件への水増し → D11のとおり33件確定配置(design §5-5の裁量)
  - blocks素材の作成・blockedペアのシード内配置 → D8(ws-3側の試験領域)
  - `docs/10`・`09`・`12`の改版 → 参照のみ(design §3.2・境界値解釈はdesign §2.4に記録済み)
  - `docs/plans/STATUS.md`の編集 → スーパーバイザー管理
  - `docs/plans/`の既存ファイル(M0〜M3・M4 ws-1)の編集
  - `backend/tests/`の§4列挙以外の既存試験への追従変更(本単位は既存コードに触れないため追随不要のはず。発生したら作業停止して報告書に記録)
- mainブランチへのコミット・push・マージ

## 6. 完了条件(テストで証明できる形。Task 8で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン**(unit **1255件 = 既存1219件+新規36件〔test_generator.py 19件〔vocab検査1+生成器18〕+test_reference_l1.py 17件〕**。既存試験への追従変更は発生しない — backend既存コード無変更のため)
   検証: `make lint && make test` — ともにexit 0。全件数を報告書に記録(2026-10-03時点のmainはunit 1219件)
2. **テストファイルbasenameがbackend/tests配下全体で一意**
   検証: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` — 出力なし(空)
3. **seed-generate+seed-verifyがグリーン**(m4-seed.yaml 300件・分布完全一致・aggregate照合一致)
   検証: `make seed-generate ARGS="--base-datetime <JST ISO>"` exit 0 → `make seed-verify` exit 0(`OK:` 出力)。**同一コマンドで再生成すると `git status --short` が変化しない**(バイト同一の実証)
4. **既存3資産が1行も変わっていない**
   検証: `git diff main -- docs/testassets/g1-parser-struct.yaml docs/testassets/g1-alcohol.yaml docs/testassets/g2-jev-goldset.yaml docs/testassets/g2-jev-goldset-plan.md docs/testassets/g2-jev-acceptance-criteria.md` — 出力なし(空)
5. **backend既存コードへ変更なし・Makefileは追記のみ**
   検証: `git diff main --name-only -- backend/src/latch | rg -v testassets` — 出力なし(空)。かつ `git diff main -- Makefile | rg '^-[^-]'` が空(削除行なし=追記のみ)
6. **依存・マイグレーションへ差分なし**
   検証: `git diff main -- backend/pyproject.toml backend/uv.lock backend/alembic` — 出力なし(空)
7. **変更ファイルが§4の一覧どおり**(作成10+変更2=12ファイル)
   検証: Task 8の報告コミット後に `git diff --name-only main | sort` が§4の一覧(report込み)と完全一致。`git status --short` が空
8. **EX-xxx 33件が全て機械突合を通過する**(ペア24件はreference_l1との判定一致・クラスタ4件は構成属性検査・reject 5件は構造検査)
   検証: seed-verify出力にEX違反が含まれない(FAIL時は一覧に出る)。unit `test_ex_pair_cases_match_expected` がPASS

## 7. 検証手順(期待値つき。報告書の証拠としてこのとおり実行する)

1. `make lint && make test` — unit全件グリーン。**期待値: `1255 passed`(既存1219+新規36)**。件数がズレる場合は収集漏れ・既存試験への意図しない影響を報告書に記録して作業停止
2. 生成(基準日は実行日から見て未来の「翌日午前」を指定する — 例: 実行日が2026-10-03なら `2026-10-04T11:00:00+09:00`。D+1〜D+5割付が+7日上限内に収まる):
   ```bash
   make seed-generate ARGS="--base-datetime 2026-10-04T11:00:00+09:00"
   ```
   期待値: exit 0・標準出力 `generated ../docs/testassets/m4-seed.yaml: intents=300 users=100 pair_total=44850 layer1_pass=<実測>`
3. 検査: `make seed-verify` — 期待値: exit 0・標準出力 `OK: ../docs/testassets/m4-seed.yaml の機械検査に合格(a構造/b分布/c意図的配置/d aggregate照合/e地名)`
4. **分布実測の記録**(報告書へ): m4-seed.yamlのmetaから下記を貼る。期待値: `time_bucket_targets {day: 60, evening: 90, night: 120, other: 30}`・`category_targets {drinking: 150, meal: 120, activity: 30}`・`orientation_targets {one_to_one: 210, group: 90}`(seed-verify(b)が実測との一致を機械検査済み)。加えて分布実現値を手動集計した結果を報告書に載せる:
   ```bash
   cd backend && uv run python -c "
   import yaml, collections
   d = yaml.safe_load(open('../docs/testassets/m4-seed.yaml'))
   ins = d['intents']
   def b(h):
       return 'day' if 12 <= h < 14 else 'evening' if 18 <= h < 20 else 'night' if 20 <= h < 23 else 'other'
   print('time', collections.Counter(b(__import__('datetime').datetime.fromisoformat(i['structured']['time']['start']).hour) for i in ins))
   print('cat', collections.Counter(i['structured']['category']['primary'] for i in ins))
   print('ori', collections.Counter('group' if i['structured']['participants']['min'] >= 3 else 'one_to_one' for i in ins))
   print('vis', collections.Counter(i['structured']['visibility'] for i in ins))
   print('notif', collections.Counter(i['structured']['notification_level'] for i in ins))
   "
   ```
   (期待値: time Counter({night: 120, evening: 90, day: 60, other: 30})・cat Counter({drinking: 150, meal: 120, activity: 30})・ori Counter({one_to_one: 210, group: 90})・vis/notifは全値が出ること)
5. **aggregate照合の記録**(報告書へ): m4-seed.yamlの`expectations.aggregate`を貼る。期待値: `pair_total: 44850`(=300×299/2)・`layer1_pass`+`layer1_fail`=44850・`fail_breakdown`は8種(`self_pair`/`category_mismatch`/`time0`/`distance`/`people`/`budgetmin`/`blocked`/`age_alcohol`)のうち実測非ゼロ種を全て記載。**`blocked`は必ず0**(D8)
6. **verifyのfail検出確認**(verifyが壊れたYAMLを通さないことの証明・本体YAMLには触れない):
   ```bash
   cp docs/testassets/m4-seed.yaml /tmp/m4-seed-broken.yaml
   cd backend && uv run python -c "
   import yaml
   p = '/tmp/m4-seed-broken.yaml'
   d = yaml.safe_load(open(p))
   d['intents'][0]['structured']['category']['primary'] = 'meal'  # 分布崩し+EX-001の期待崩し
   yaml.safe_dump(d, open(p, 'w'), allow_unicode=True, sort_keys=False)
   "
   uv run python -m latch.testassets.verify --path /tmp/m4-seed-broken.yaml; echo "exit=$?"
   ```
   期待値: exit 1・`FAIL: N件の違反`でEX-001判定不一致と分布違反を含む一覧。実行後 `rm /tmp/m4-seed-broken.yaml`
7. **バイト同一の実証**: `make seed-generate ARGS="--base-datetime <手順2と同一引数>"` を再実行 → `git status --short` が手順3完了時点と同一(変更なし)。unit `test_generation_is_byte_identical` と合わせて報告書に記録
8. lint再実行: `make lint`

## 8. 報告形式

**結果ファイル**: `docs/plans/M4/ws-2-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M4 ws-2(共有テスト資産)実行報告

- ブランチ: m4-ws-2 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test(unit 1255件) | PASS/FAIL | <出力末尾(全件数も)> |
| 2 | テストbasename一意 | PASS/FAIL | <find+uniq -d 出力(空なら「空」)> |
| 3 | seed-generate+seed-verify | PASS/FAIL | <両コマンドの出力> |
| 4 | 既存3資産無変更 | PASS/FAIL | <git diff出力(空なら「空」)> |
| 5 | backend既存コード無変更・Makefile追記のみ | PASS/FAIL | <git diff出力> |
| 6 | 依存・マイグレーション無変更 | PASS/FAIL | <git diff出力(空なら「空」)> |
| 7 | 変更ファイル=§4の12ファイル | PASS/FAIL | <git diff --name-only出力> |
| 8 | EX-xxx 33件機械突合 | PASS/FAIL | <seed-verify出力+unit結果> |

## 生成実行の記録(design §4-2・4-4・supervisor指示)
- 生成コマンド: make seed-generate ARGS="--base-datetime <値>"
- 分布実測: <§7-4の集計出力を貼る>
- aggregate照合: <§7-5のaggregate実測を貼る。layer1_pass/fail・fail_breakdown内訳>
- バイト同一再生成: <§7-7の結果(git status 変化なし)>
- verify fail検出確認: <§7-6の結果(破壊YAMLがexit 1)>

## 固定値の変更有無(本計画§2.0 D1〜D12・§2コード全文)
- D1〜D12の確定事項: 変更なし / 変更あり(<前→後+理由>)
- EX構成33件・fail_reason優先順位・seed値42: 変更なし / 変更あり(<同上>)
- §2.1〜2.5のコード全文からの逸脱: なし / あり(<箇所と理由>)

## design §5 引継ぎ事項の確認
- §5-1(全ペア判定のws-3への提供形態): 本単位はreference_l1をモジュールとして
  提供(import可能)・さらにm4-seed.yaml自体にEX期待値とaggregateを記録済み。
  中間出力JSONLが必要になった場合はws-3設計時に生成器へ--jsonlオプション追加で対応可
- §5-3(真のalcohol×20歳未満ペア素材): D3のとおり「18-19歳×drinking×false」を
  正規POSTで作成可能(structured_intent明示投入)と確認済み。**ws-3設計時の確認事項:
  raw_text→Parser経由で投入すると規則7でalcohol trueとなり422になる点。
  EX-004/005の素材はstructured_intent明示で投入すること**
- §5-2(staging投入経路)・§5-4(字義の整合=supervisor承認済み)・§5-5(細部裁量)

## ws-3・ws-6への引継ぎ
- ws-3: 投入素材はdocs/testassets/m4-seed.yaml(users→POST /v1/users・intents→
  POST /v1/intents・structured_intent明示)。intended_reject_cases 5件は422試行素材。
  EX-xxxのexpectedとreference_l1(backend/src/latch/testassets/reference_l1.py)で
  match_candidates突合可能(無向化して比較・design §2.2対称性注記)
- ws-6: 拡張生成は同一生成器の `make seed-generate ARGS="--count 10000 ..."`
  (COUNT_MIN以上が条件)。本体300件との整合は同一コード経路が保証
- embeddingはstub既定(ws-1設計§2.6)。実系統はws-3設計時にユーザー承認を経る

## コミット一覧
<git log --oneline main..HEAD>

## 補足(詰まった点・判断した点があれば)
<自由記述>
```

## 9. 実装ステップ(TDD。Task 1〜8の順で実行する)

### Task 1: testassetsパッケージ土台+vocab.py(語彙プール)

**Files:**
- Create: `backend/src/latch/testassets/__init__.py`(空ファイル)
- Create: `backend/src/latch/testassets/vocab.py`(§2.1の全文をそのまま書く)
- Create: `backend/tests/unit/testassets/test_generator.py`(このTaskではvocab検査1件のみ。Task 3で追記)

**Interfaces:**
- Produces: `latch.testassets.vocab` の `LOCATIONS`(9地名→(lat, lon))・`CATEGORY_SECONDARY`・`SOFT_CONSTRAINTS`・`RAW_TEMPLATES`・`TIME_PREFIX`・`TIME_BUCKETS`・`time_bucket_of(start_hour) -> str`(Task 2〜4が消費)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/testassets/test_generator.py` を新規作成:

```python
"""決定的シード生成器のunit試験(M4 ws-2・design §4-1・4-2)。

決定性(バイト同一)・分布比率の実現・意図的配置の成立・副次分布を検証する。
300件フル生成は全ペアL1判定(44,850組)を含むが純Pythonで1〜2秒程度。
"""

from latch.testassets.vocab import (
    CATEGORY_SECONDARY,
    LOCATIONS,
    RAW_TEMPLATES,
    SOFT_CONSTRAINTS,
    TIME_PREFIX,
    time_bucket_of,
)

# --- 0. 語彙プール(M4 ws-2 Task 1) ---


def test_vocab_pools_are_valid():
    """語彙プールの基本性質(9地名・bbox内・カテゴリ網羅・テンプレート整合)。"""
    assert len(LOCATIONS) == 9
    for lat, lon in LOCATIONS.values():  # settings.py geo_osm_bbox
        assert 31.5825 <= lat <= 31.6095
        assert 130.5420 <= lon <= 130.5740
    assert set(CATEGORY_SECONDARY) == {"drinking", "meal", "activity"}
    assert set(SOFT_CONSTRAINTS) == {"drinking", "meal", "activity"}
    assert set(RAW_TEMPLATES) == {"drinking", "meal", "activity"}
    for tpls in RAW_TEMPLATES.values():
        for t in tpls:
            assert t.format(time_prefix="x", place="y", secondary="z", soft="w")
    assert set(TIME_PREFIX) == {"day", "evening", "night", "other"}
    assert time_bucket_of(12) == "day"  # 昼=12:00-13:59(design §2.4)
    assert time_bucket_of(18) == "evening"  # 夕=18:00-19:59
    assert time_bucket_of(20) == "night"  # 夜=20:00-22:59
    assert time_bucket_of(13) == "day"
    assert time_bucket_of(19) == "evening"
    assert time_bucket_of(22) == "night"
    assert time_bucket_of(11) == "other"  # その他=上記以外
    assert time_bucket_of(14) == "other"
    assert time_bucket_of(23) == "other"
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/testassets/test_generator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'latch.testassets'`

- [ ] **Step 3: 最小実装**

`backend/src/latch/testassets/__init__.py`(空)と `backend/src/latch/testassets/vocab.py`(§2.1の全文)を作成する。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/testassets/test_generator.py -v`
Expected: PASS(1件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/testassets/__init__.py backend/src/latch/testassets/vocab.py backend/tests/unit/testassets/test_generator.py
git commit -m "feat: add testassets vocab pools for m4 seed generation"
```

### Task 2: reference_l1.py(Layer 1参照実装・SQLピン相当のunit)

**Files:**
- Create: `backend/src/latch/testassets/reference_l1.py`(§2.2の全文)
- Create: `backend/tests/unit/testassets/test_reference_l1.py`

**Interfaces:**
- Consumes: Task 1の `vocab.LOCATIONS`
- Produces: `latch.testassets.reference_l1` の `JST`・`FAIL_PRIORITY`・`haversine_m(a, b) -> float`・`completed_end(start, end) -> datetime`・`age_years_at(birth, on) -> int`・`judge_pair(a, b, *, users, now, blocks=()) -> tuple[bool, str | None]`・`judge_all(intents, users, *, now, blocks=()) -> dict[(id, id), (bool, str | None)]`(Task 3〜4が消費)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/testassets/test_reference_l1.py` を新規作成:

```python
"""Layer 1参照実装のunit試験(M4 ws-2・design §4-3・06 §2確定表)。

既知の小ペア集合(時間交差境界・end null補完・距離境界・予算500円・人数・
飲酒年齢・自己ペア・ブロック)でpass/fail+fail_reasonを確認する。
実装はlayer1.pyのLAYER1_WHERE(06 §2)と1対1対応をモジュールdocstringで
明記している前提で、本試験がその挙動をピン留めする。
"""

from datetime import date, datetime

from latch.testassets.reference_l1 import (
    JST,
    age_years_at,
    judge_all,
    judge_pair,
)

BASE = datetime(2026, 10, 4, 11, 0, tzinfo=JST)

def _intent(
    sd: str,
    user: str,
    *,
    cat="drinking",
    alc=True,
    start="2026-10-05T20:00",
    end="2026-10-05T23:00",
    place="天文館",
    radius=1000,
    budget=3500,
    pmin=2,
    pmax=2,
):
    return {
        "id": sd,
        "user": user,
        "structured": {
            "category": {"primary": cat, "secondary": "x"},
            "alcohol_involved": alc,
            "time": {
                "start": datetime.fromisoformat(start).replace(tzinfo=JST),
                "end": None if end is None else datetime.fromisoformat(end).replace(tzinfo=JST),
            },
            "location": {"name": place, "radius_m": radius},
            "budget": {"max": budget},
            "participants": {"min": pmin, "max": pmax},
        },
    }

def _users(*ages: int) -> dict:
    """指定年齢のユーザー辞書(BASE時点の満年齢)。"""
    out = {}
    for i, a in enumerate(ages, 1):
        uid = f"SU-{i:03d}"
        out[uid] = {"id": uid, "birth_date": date(BASE.year - a, BASE.month, BASE.day)}
    return out

# --- 1. passの基本形と各fail_reason(06 §2確定表の1条件ずつ) ---

def test_pass_basic_overlap():
    """同カテゴリ・交差・同地名・予算・人数・20歳以上 → pass。"""
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-002", start="2026-10-05T20:30")
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (True, None)

def test_fail_category_mismatch():
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-002", cat="meal", alc=False)
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (False, "category_mismatch")

def test_fail_time_adjacent_boundary():
    """終了=開始の隣接は交差0(SQL: :origin_time_start < i.time_end が偽)。"""
    a = _intent("SD-001", "SU-001", start="2026-10-05T20:00", end="2026-10-05T21:30")
    b = _intent("SD-002", "SU-002", start="2026-10-05T21:30", end="2026-10-05T23:00")
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (False, "time0")

def test_pass_end_null_completion():
    """end nullはstart+3h補完で交差成立(COALESCE(i.time_end, +3h))。"""
    a = _intent("SD-001", "SU-001", cat="meal", alc=False,
                start="2026-10-05T12:00", end="2026-10-05T14:00")
    b = _intent("SD-002", "SU-002", cat="meal", alc=False,
                start="2026-10-05T13:00", end=None)
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (True, None)

def test_fail_time_disjoint():
    a = _intent("SD-001", "SU-001", start="2026-10-05T10:00", end="2026-10-05T11:00")
    b = _intent("SD-002", "SU-002", start="2026-10-05T15:00", end="2026-10-05T16:00")
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (False, "time0")

# --- 2. 距離境界(ST_DWithin r_origin+COALESCE(r_target,1000)) ---

def test_fail_distance_far_with_null_radius():
    """radius null×null=1000+1000 < 駅前〜浜町(約2775m)→ distance。"""
    a = _intent("SD-001", "SU-001", place="鹿児島中央駅前", radius=None)
    b = _intent("SD-002", "SU-002", place="浜町", radius=None)
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (False, "distance")

def test_pass_distance_exact_ceil():
    """半径和が実距離以上(haversineでceil側)→ pass・切る側はfail。"""
    import math

    from latch.testassets.reference_l1 import haversine_m
    from latch.testassets.vocab import LOCATIONS

    d = haversine_m(LOCATIONS["天文館"], LOCATIONS["高見馬場"])
    a = _intent("SD-001", "SU-001", place="天文館", radius=math.ceil(d) - 1)
    b = _intent("SD-002", "SU-002", place="高見馬場", radius=1)
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (True, None)
    a2 = _intent("SD-003", "SU-003", place="天文館", radius=math.floor(d) - 1)
    assert judge_pair(a2, b, users=_users(30, 28, 27), now=BASE) == (False, "distance")

# --- 3. 予算・人数(06 §2のLEAST NULL無視・2∈[min,max]) ---

def test_fail_budget_below_500():
    a = _intent("SD-001", "SU-001", budget=499)
    b = _intent("SD-002", "SU-002", budget=499)
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (False, "budgetmin")

def test_pass_budget_500_exact_and_null_ignored():
    """500ちょうどは通る。NULLは無視(片方のみでもmin(有効値)>=500ならpass)。"""
    a = _intent("SD-001", "SU-001", budget=500)
    b = _intent("SD-002", "SU-002", budget=None)
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (True, None)

def test_fail_people_out_of_range():
    """2∉[min,max]の双方パターン(min3超過・max1不足)。"""
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-002", pmin=3, pmax=4)
    assert judge_pair(a, b, users=_users(30, 28), now=BASE) == (False, "people")
    c = _intent("SD-003", "SU-003", pmin=1, pmax=1)
    d = _intent("SD-004", "SU-004", pmin=2, pmax=2)
    assert judge_pair(c, d, users=_users(30, 28, 27, 26), now=BASE) == (False, "people")

# --- 4. 飲酒年齢(D-10・双方20歳以上) ---

def test_fail_age_alcohol_under20():
    """drinking×drinkingで他条件pass・片側19歳 → age_alcohol(D3の構成)。"""
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-002", alc=False, start="2026-10-05T20:30")
    users = _users(30, 19)
    assert judge_pair(a, b, users=users, now=BASE) == (False, "age_alcohol")

def test_pass_age_alcohol_both_20():
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-002", start="2026-10-05T20:30")
    assert judge_pair(a, b, users=_users(30, 20), now=BASE) == (True, None)

def test_age_boundary_on_20th_birthday():
    """満20歳の誕生日当日は条件を満たす(EXTRACT(YEAR FROM AGE())の規則)。"""
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-002", start="2026-10-05T20:30")
    users = _users(30, 20)
    users["SU-002"]["birth_date"] = date(2006, 10, 4)  # BASE日時点で満20歳
    assert judge_pair(a, b, users=users, now=BASE) == (True, None)

# --- 5. 自己ペア・ブロック・無向全ペア ---

def test_fail_self_pair():
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-001")
    assert judge_pair(a, b, users=_users(30), now=BASE) == (False, "self_pair")

def test_blocked_pair_symmetric():
    """ブロックは双方向(どちらがブロッカーでもfail)。"""
    a = _intent("SD-001", "SU-001")
    b = _intent("SD-002", "SU-002")
    assert judge_pair(a, b, users=_users(30, 28), now=BASE, blocks={("SU-001", "SU-002")}) == (False, "blocked")
    assert judge_pair(a, b, users=_users(30, 28), now=BASE, blocks={("SU-002", "SU-001")}) == (False, "blocked")

def test_judge_all_counts_undirected_pairs():
    """judge_allは無向ペア集合(n=3なら3組・id順キー)。"""
    a = _intent("SD-002", "SU-001")
    b = _intent("SD-001", "SU-002")
    c = _intent("SD-003", "SU-003", cat="meal", alc=False)
    got = judge_all([a, b, c], _users(30, 28, 27), now=BASE)
    assert set(got) == {("SD-001", "SD-002"), ("SD-001", "SD-003"), ("SD-002", "SD-003")}
    assert got[("SD-001", "SD-002")] == (True, None)
    assert got[("SD-001", "SD-003")] == (False, "category_mismatch")

def test_age_years_at_matches_full_age():
    """満年齢(誕生日前は-1)。users.service.age_yearsと同一規則。"""
    assert age_years_at(date(2006, 10, 4), date(2026, 10, 4)) == 20
    assert age_years_at(date(2006, 10, 5), date(2026, 10, 4)) == 19
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/testassets/test_reference_l1.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'latch.testassets.reference_l1'`

- [ ] **Step 3: 最小実装**

`backend/src/latch/testassets/reference_l1.py` を作成する(§2.2の全文)。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/testassets/test_reference_l1.py -v`
Expected: PASS(17件)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/testassets/reference_l1.py backend/tests/unit/testassets/test_reference_l1.py
git commit -m "feat: add layer1 reference implementation for m4 seed verification"
```

### Task 3: generator.py(決定的生成器・TDD Red→Green)

**Files:**
- Create: `backend/src/latch/testassets/generator.py`(§2.3の全文)
- Modify: `backend/tests/unit/testassets/test_generator.py`(§9 Task 1の末尾へ追記)

**Interfaces:**
- Consumes: Task 1のvocab全IF・Task 2の`judge_all`・`judge_pair`・`FAIL_PRIORITY`・`haversine_m`・`JST`
- Produces: `latch.testassets.generator` の `SEED`・`SEED_CREATED`・`DEFAULT_COUNT`・`EX_SLOT_COUNT`・`COUNT_MIN`・`build_seed(base_datetime, *, seed=42, count=300) -> dict`・`write_yaml(seed, out_path) -> None`・`main(argv) -> int`(Task 4〜5が消費)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/testassets/test_generator.py` の末尾へ追記:

```python
# --- 1〜3. 生成器本体(M4 ws-2 Task 3) ---

from collections import Counter
from datetime import datetime, timedelta

import pytest

from latch.testassets.generator import (
    DEFAULT_COUNT,
    EX_SLOT_COUNT,
    SEED,
    build_seed,
    write_yaml,
)
from latch.testassets.reference_l1 import JST, age_years_at, judge_all, judge_pair

BASE = datetime(2026, 10, 4, 11, 0, tzinfo=JST)

@pytest.fixture(scope="module")
def seed():
    """300件フル生成(全ペアL1判定込み・module scopeで1回だけ実行)。"""
    return build_seed(BASE, seed=SEED, count=DEFAULT_COUNT)

def _by_id(seed):
    return {i["id"]: i for i in seed["intents"]}

def test_generation_is_byte_identical(tmp_path):
    """同一引数での2回生成がバイト同一(design §2.3・Review Focus 1)。"""
    p1, p2 = tmp_path / "a.yaml", tmp_path / "b.yaml"
    write_yaml(build_seed(BASE, seed=SEED, count=80), str(p1))
    write_yaml(build_seed(BASE, seed=SEED, count=80), str(p2))
    assert p1.read_bytes() == p2.read_bytes()

def test_small_count_is_deterministic(seed):
    """--count変更時も決定的(低件数生成・design §4-1)。count違いは別出力。"""
    a = build_seed(BASE, seed=SEED, count=100)
    b = build_seed(BASE, seed=SEED, count=100)
    assert a == b
    assert a != seed  # count違いは異なる生成物

def test_count_below_ex_pool_raises():
    """意図的配置(EX素材64件)未満のcountはValueError。"""
    with pytest.raises(ValueError, match="64"):
        build_seed(BASE, seed=SEED, count=30)

def test_intent_ids_unique_and_prefixes(seed):
    """SD-001〜300が一意連番・SU一意・ex_groupはEX素材64件のみ。"""
    ids = [i["id"] for i in seed["intents"]]
    assert ids == [f"SD-{n:03d}" for n in range(1, 301)]
    assert len({i["user"] for i in seed["intents"]}) <= 100
    assert all(u["id"].startswith("SU-") for u in seed["users"])
    assert {i["id"] for i in seed["intents"] if "ex_group" in i} == {
        f"SD-{n:03d}" for n in range(1, EX_SLOT_COUNT + 1)
    }

def test_time_bucket_targets(seed):
    """時間帯分布 60/90/120/30(10 §2・Review Focus 2)。"""
    def bucket(i):
        h = i["structured"]["time"]["start"].astimezone(JST).hour
        return time_bucket_of(h)
    got = Counter(bucket(i) for i in seed["intents"])
    assert got == Counter({"day": 60, "evening": 90, "night": 120, "other": 30})

def test_category_targets(seed):
    """カテゴリ分布 150/120/30(10 §2)。"""
    got = Counter(i["structured"]["category"]["primary"] for i in seed["intents"])
    assert got == Counter({"drinking": 150, "meal": 120, "activity": 30})

def test_orientation_targets(seed):
    """人数志向分布 1対1 210/グループ90(10 §2・min≧3=グループ)。"""
    got = Counter(
        "group" if i["structured"]["participants"]["min"] >= 3 else "one_to_one"
        for i in seed["intents"]
    )
    assert got == Counter({"one_to_one": 210, "group": 90})

def test_underage_users_are_8_and_stay_underage(seed):
    """18-19歳8人・base+7日でも18-19歳(D6・design §2.4)。"""
    underage = [
        u for u in seed["users"]
        if age_years_at(u["birth_date"], BASE.astimezone(JST).date()) < 20
    ]
    assert len(underage) == 8
    later = (BASE + timedelta(days=7)).astimezone(JST).date()
    for u in underage:
        assert age_years_at(u["birth_date"], later) < 20

def test_underage_intents_all_alcohol_false(seed):
    """18-19歳ユーザーの全intentがalcohol_involved=false(design §2.4)。"""
    underage_ids = {
        u["id"] for u in seed["users"]
        if age_years_at(u["birth_date"], BASE.astimezone(JST).date()) < 20
    }
    for i in seed["intents"]:
        if i["user"] in underage_ids:
            assert i["structured"]["alcohol_involved"] is False

def test_drinking_all_alcohol_true_and_activity_false(seed):
    """drinking→全true・activity→全false(規則7・design §2.4)。"""
    for i in seed["intents"]:
        cat = i["structured"]["category"]["primary"]
        alc = i["structured"]["alcohol_involved"]
        if cat == "drinking":
            assert alc is True
        if cat == "activity":
            assert alc is False

def test_visibility_notification_coverage(seed):
    """visibility 2値・notification_level 3値の全値カバレッジ(D5)。"""
    assert {i["structured"]["visibility"] for i in seed["intents"]} == {
        "hidden_until_match", "summary_only",
    }
    assert {i["structured"]["notification_level"] for i in seed["intents"]} == {
        "proposals_only", "nearby_also", "muted",
    }

def test_intended_reject_cases_structure(seed):
    """reject素材5件: alcohol true・expected 422/UNDER_AGE・18-19歳ユーザー。"""
    users = {u["id"]: u for u in seed["users"]}
    rejects = seed["intended_reject_cases"]
    assert [r["id"] for r in rejects] == [f"EX-{n:03d}" for n in range(29, 34)]
    for r in rejects:
        assert r["expected_status"] == 422
        assert r["expected_code"] == "UNDER_AGE"
        assert r["structured_intent"]["alcohol_involved"] is True
        assert age_years_at(users[r["user"]]["birth_date"], BASE.astimezone(JST).date()) < 20

def test_self_pair_materials(seed):
    """自己ペア3組素材: 同一ユーザー2 intents・同カテゴリ・同地名・交差(§2.2)。"""
    by_id = _by_id(seed)
    for a_id, b_id in [("SD-001", "SD-002"), ("SD-003", "SD-004"), ("SD-005", "SD-006")]:
        a, b = by_id[a_id], by_id[b_id]
        assert a["user"] == b["user"]
        assert a["structured"]["category"]["primary"] == b["structured"]["category"]["primary"]
        assert a["structured"]["location"]["name"] == b["structured"]["location"]["name"]
        assert a["structured"]["time"]["start"] < b["structured"]["time"]["end"]

def test_self_dup_cluster_material(seed):
    """自己重複クラスタ: 4 intents・distinct users=3・min≧3(EX-025・FR-17)。"""
    by_id = _by_id(seed)
    members = [by_id[f"SD-{n:03d}"] for n in range(48, 52)]
    assert len({m["user"] for m in members}) == 3
    assert all(m["structured"]["participants"]["min"] >= 3 for m in members)

def test_ex_pair_cases_match_expected(seed):
    """EXペア全24件がreference_l1判定と一致(生成時ゲートの裏取り)。"""
    users = {u["id"]: u for u in seed["users"]}
    by_id = _by_id(seed)
    for case in seed["expectations"]["cases"]:
        if case["kind"] != "pair":
            continue
        a, b = (by_id[i] for i in case["intents"])
        ok, reason = judge_pair(a, b, users=users, now=BASE)
        if case["expected"] == "pass":
            assert (ok, reason) == (True, None), case["id"]
        else:
            assert (ok, reason) == (False, case["fail_reason"]), case["id"]

def test_intents_within_d_plus_1_to_5(seed):
    """全intentの時刻がbaseのD+1〜D+5内(design §2.3)。"""
    for i in seed["intents"]:
        delta = (i["structured"]["time"]["start"] - BASE).total_seconds() / 86400
        assert 0.9 < delta < 6.0, i["id"]

def test_raw_text_constraints(seed):
    """raw_textは1〜300字(参照値13)。"""
    for i in seed["intents"]:
        assert 1 <= len(i["raw_text"]) <= 300

def test_aggregate_matches_reference(seed):
    """aggregateがjudge_all再計算と一致・pair_total=44850(design §2.2)。"""
    users = {u["id"]: u for u in seed["users"]}
    verdicts = judge_all(seed["intents"], users, now=BASE)
    got_pass = sum(1 for ok, _ in verdicts.values() if ok)
    agg = seed["expectations"]["aggregate"]
    assert agg["pair_total"] == len(verdicts) == 44850
    assert agg["layer1_pass"] == got_pass
    assert agg["layer1_fail"] == len(verdicts) - got_pass
    assert sum(agg["fail_breakdown"].values()) == agg["layer1_fail"]
    assert agg["fail_breakdown"]["blocked"] == 0  # D8(シードにblocksなし)
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/testassets/test_generator.py -v -k "not vocab"`
Expected: FAIL with `ModuleNotFoundError: No module named 'latch.testassets.generator'`

- [ ] **Step 3: 最小実装**

`backend/src/latch/testassets/generator.py` を作成する(§2.3の全文)。

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/testassets/test_generator.py -v`
Expected: PASS(18件+vocab1件=19件。実行時間は300件フル生成込みで数十秒以内)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/testassets/generator.py backend/tests/unit/testassets/test_generator.py
git commit -m "feat: add deterministic m4 seed generator"
```

### Task 4: verify.py(YAML機械検査・CLI)

**Files:**
- Create: `backend/src/latch/testassets/verify.py`(§2.4の全文)

**Interfaces:**
- Consumes: Task 1の`vocab.LOCATIONS`・`vocab.time_bucket_of`、Task 2の`reference_l1`全IF、Task 3が書き出した`docs/testassets/m4-seed.yaml`
- Produces: `latch.testassets.verify.verify(path) -> list[str]`・`main(argv) -> int`

本タスクはTDDのunit試験を書かない(design §3.1はunit試験2ファイルのみ)。verify自体の正しさは **Step 3〜5の動作確認**(正常系=合格・故意破壊=FAIL検出)とTask 5のseed-verify実行で担保し、結果を報告書に記録する。verifyの照合先(`reference_l1`)はunit試験済み。

- [ ] **Step 1: 実装を書く**

`backend/src/latch/testassets/verify.py` を作成する(§2.4の全文)。

- [ ] **Step 2: 実行して構文・importを確認**

Run: `cd backend && uv run python -c "from latch.testassets.verify import verify; print('import ok')"`
Expected: `import ok`

- [ ] **Step 3: 正常系の確認(Task 5の生成物がまだ無い場合は一時的に小件数で)**

Run(Task 5の実行後ならそれを使う・先回りで確認する場合):
```bash
cd backend && uv run python -m latch.testassets.generator --base-datetime 2026-10-04T11:00:00+09:00 --out /tmp/m4-seed-check.yaml && uv run python -m latch.testassets.verify --path /tmp/m4-seed-check.yaml; echo "exit=$?"; rm -f /tmp/m4-seed-check.yaml
```
Expected: `OK: /tmp/m4-seed-check.yaml の機械検査に合格(...)`・`exit=0`

- [ ] **Step 4: 故意破壊でFAIL検出を確認(Review Focus 5)**

Run:
```bash
cd backend && uv run python -m latch.testassets.generator --base-datetime 2026-10-04T11:00:00+09:00 --count 80 --out /tmp/m4-seed-small.yaml
uv run python - << 'PY'
import yaml
p = "/tmp/m4-seed-broken.yaml"
d = yaml.safe_load(open("/tmp/m4-seed-small.yaml"))
# 3か所を壊す: カテゴリ目標崩し・EX-001の素材user差し替え・rejectのalcohol false化
d["intents"][70]["structured"]["category"]["primary"] = "drinking"
d["intents"][1]["user"] = d["intents"][0]["user"]  # EX-001素材を同ユーザー化(self_pair素材の破壊は困難のためuser重複で検査)
d["intended_reject_cases"][0]["structured_intent"]["alcohol_involved"] = False
yaml.safe_dump(d, open(p, "w"), allow_unicode=True, sort_keys=False)
PY
uv run python -m latch.testassets.verify --path /tmp/m4-seed-broken.yaml; echo "exit=$?"
rm -f /tmp/m4-seed-small.yaml /tmp/m4-seed-broken.yaml
```
Expected: exit 1・`FAIL: N件の違反` 一覧に `(b)分布違反`(カテゴリ内訳のズレ)と `(c)reject` 違反を含む。(70番目は残余スロットのためdrinking→drinkingは変化しないことがある — その場合は `70` を `71`〜`75` に替えてカテゴリが変わるスロットを探す。`counts`を変えることが目的)

- [ ] **Step 5: コミット**

```bash
git add backend/src/latch/testassets/verify.py
git commit -m "feat: add m4 seed yaml mechanical verification"
```

### Task 5: Makefile追記+seed-generate実行+seed-verify実行(生成物の作成)

**Files:**
- Modify: `Makefile`(§2.5のとおり)
- Create: `docs/testassets/m4-seed.yaml`(生成物・コミットする)

**Interfaces:**
- Consumes: Task 3の`generator.main`・Task 4の`verify.main`

- [ ] **Step 1: Makefileに追記する**(§2.5のとおり。`.PHONY`行へ追記+末尾へターゲット2件)

- [ ] **Step 2: 動作確認(lint・既存ターゲットを壊していないこと)**

Run: `make lint && make -n seed-generate && make -n seed-verify`
Expected: lintグリーン・`-n`(ドライラン: 実行せず実行されるコマンドだけ表示)で
`cd backend && uv run python -m latch.testassets.generator $(ARGS)` と
`cd backend && uv run python -m latch.testassets.verify $(ARGS)` が出る(実行はしない)

- [ ] **Step 3: 生成する**

Run: `make seed-generate ARGS="--base-datetime 2026-10-04T11:00:00+09:00"`
Expected: exit 0・`generated ../docs/testassets/m4-seed.yaml: intents=300 users=100 pair_total=44850 layer1_pass=<実測>`。`docs/testassets/m4-seed.yaml` が作成される(300件生成は数秒)

- [ ] **Step 4: 検査する**

Run: `make seed-verify`
Expected: exit 0・`OK: ../docs/testassets/m4-seed.yaml の機械検査に合格(a構造/b分布/c意図的配置/d aggregate照合/e地名)`

- [ ] **Step 5: バイト同一を確認してコミット**

Run:
```bash
make seed-generate ARGS="--base-datetime 2026-10-04T11:00:00+09:00"
git status --short  # 手順4完了時点と同一(差分なし)を確認
git add Makefile docs/testassets/m4-seed.yaml
git commit -m "feat: add seed-generate/seed-verify targets and m4-seed.yaml (300 intents)"
```

### Task 6: m4-seed-plan.md執筆(生成規則・分布実測・検証方式の記録)

**Files:**
- Create: `docs/testassets/m4-seed-plan.md`(雛形に実測値を記入して作る・g2-planと同型)

- [ ] **Step 1: 下記の雛形を作成し、`<>`部をTask 5の実測値で埋める**

```markdown
# M4シード(m4-seed)整備計画・実測

- ステータス: draft(M4 ws-2。README status規約に従いconfirmed化はスーパーバイザーレビュー経由)
- 作成: 2026-10-03(agent2計画)・生成: <実行日(agent3)>
- 参照: 10 §2・§3 / 09 §4・§4.1 / 06 §2 / 12 M4スコープ2 / docs/plans/M4/ws-2-design.md・ws-2-plan.md
- 方式: テンプレート直交生成の**決定的生成器**(design §2.1案B・supervisor承認2026-10-03)。
  g2-jev-goldsetは語彙・時刻パターン・カテゴリ配分の母集合として参照(本体YAML不変)

## 1. 生成コマンドと決定性

- コマンド: `make seed-generate ARGS="--base-datetime 2026-10-04T11:00:00+09:00"`
- 基準日時(base_datetime)・乱数シード(42)はYAML metaに記録。**同一引数での再生成はバイト同一**
  (unit test_generation_is_byte_identical+再生成時のgit diff無しで保証・<実測を記載>)
- 対象時刻は基準日D+1〜D+5に割り付け(+7日上限の2日マージン)。staging投入日はws-3の
  実施日のため、投入直前に同一コマンドで再生成する(YAML差分は対象日付のみ)
- 拡張生成(ws-6): `--count 10000/50000/100000`で同一コード経路(COUNT_MIN=65以上)

## 2. 生成規則(design §2.4・ws-2-plan §2.0〜2.3の要約)

- 件数: 300件(SD-001〜300)+合成ユーザー100人(SU-001〜100)
- 分布(10 §2完全一致): 時間帯=昼12-13時60/夕18-19時90/夜20-22時120/その他30。
  カテゴリ=drinking 150/meal 120/activity 30。人数志向=1対1 210/グループ(min≧3)90
- 構成: 意図的配置(EX素材64 intents・SD-001〜064)を先取りし、残余236件を比率配分
  (最大剰余法・時間帯×カテゴリは直交割付+乱数シャッフル)
- 18-19歳8人(SU-001〜008・生年月日はbase+7日でも18-19歳を保つマージン)。
  うちSU-001/002はD-10ペア素材・SU-003〜007はreject素材(EX-029〜033)・SU-008は通常
- alcohol規約: drinking→全true・activity→全false・mealは20歳以上のみ一部true・
  **18-19歳ユーザーのIntentは全てfalse**(design §2.4)
- 地名: g2実績9地名の許可リスト(vocab.LOCATIONS・正転ヒット実績・bbox内)。
  geo結合検査は本単位スコープ外(design §4-5・資産改訂時のみ)
- expires_at: シードに含めない(省略=NULL・expired自動遷移を発生させない)

## 3. 期待値表の構成(design §2.2の2層)

- case expectations(EX-xxx・33件): ペア24件(EX-001〜024・L1判定を機械突合)+
  クラスタ4件(EX-025〜028・構成属性を機械検査)+reject 5件(EX-029〜033・422素材)。
  詳細はm4-seed.yamlのexpectations.cases(expected・fail_reason・basis〔根拠節番号〕つき)
- aggregate expectations: 総ペア44850・L1通過数・fail内訳8種・<実測値を§4から貼る>。
  機械突合はLayer 1まで(Layer 2以降はws-3ハーネス側)
- fail_reason優先順位: self_pair > category_mismatch > time0 > distance > people >
  budgetmin > blocked > age_alcohol(ws-2-plan §2.0 D2)。blockedは0(シードにblocksなし)

## 4. 分布実測とaggregate照合(seed-verify結果・<実行日>)

<ws-2-plan §7-4/7-5の実行結果をここに貼る: 時間帯/カテゴリ/志向/visibility/notification
の実測Counter・aggregate(layer1_pass・layer1_fail・fail_breakdown 8種の内訳)>

## 5. 検証方式

- unit: test_generator.py(19件: 語彙1+決定性・分布・意図的配置18)・test_reference_l1.py
  (17件: 06 §2確定表の既知ペア)
- 機械検査: `make seed-verify`(verify.py): (a)構造(b)分布(c)意図的配置〔EX突合〕
  (d)aggregate照合〔reference_l1で全ペア独立再計算〕(e)地名許可リスト
- 二重実装の突合: 生成器(EX_CASES期待値は静的定義)とreference_l1(SQL・layer1.pyの
  LAYER1_WHEREのPython独立実装)を、生成時とseed-verify時の両方で突合
- 故意破壊確認: <ws-2-plan §7-6の実行結果(exit 1)を記載>

## 6. 既存3資産との関係整理(design §1.3・README.mdへも要約を記載)

| 資産 | 状態 | m4-seedとの関係 |
|---|---|---|
| g2-jev-goldset.yaml(304 Intent+520ペア) | confirmed・不変 | 評価ペア500件以上(09 §4.1)を充足済み。intents側は語彙・時刻パターン・カテゴリ配分の母集合として参照(design §2.1案B)。**ペア構成を変えるとG2評価結果の前提が崩れるため一切編集しない**(g2-plan §7) |
| g1-alcohol.yaml(36件=true24/false12) | confirmed・不変 | 飲酒判定専用入力セット30件以上(10 §2 FR-20)を充足済み。期待値表はこれを参照(新規作成しない) |
| g1-parser-struct.yaml(32+3件) | confirmed・不変 | Parser構造化精度ゲート(09 §4.3)。id規約の衝突回避は新prefix(SU-/SD-/EX-)で担保 |

10 §2の「シードIntentは09のゴールドセットを共用」の読み: 評価ペア=g2(520ペア)、
機能試験素材=m4-seed。両方がdocs/testassets/配下でバージョン管理=「同一の資産を参照」。
g2 intentsをそのまま機能試験に投入しない根拠(実測・design §1.3): 10 §2分布から乖離
(時間帯その他23%・activity 21%・夜20-22時29%)・FR-17自己重複配置・合成users・
raw_textを欠く。

## 7. ws-3・ws-6への引継ぎ(design §3.3)

<ws-2-report.mdの「ws-3・ws-6への引継ぎ」と同じ内容を簡潔に記載>
```

- [ ] **Step 2: コミット**

```bash
git add docs/testassets/m4-seed-plan.md
git commit -m "docs: add m4-seed plan with generation rules and measured distribution"
```

### Task 7: README.md更新(新資産の位置づけ+関係整理)

**Files:**
- Modify: `docs/testassets/README.md`(追記のみ・既存節は変更しない)

- [ ] **Step 1: 「ファイル一覧」テーブルへ2行追記**

既存の「## ファイル一覧」テーブルの行末に追加:

```markdown
| m4-seed.yaml | M4シード300件+合成ユーザー100人+期待値表(EX-xxx 33件+aggregate)。生成器`make seed-generate`の決定的生成物 | intents 300件 / users 100人 |
| m4-seed-plan.md | m4-seedの生成規則・分布実測・検証方式の記録 | — |
```

- [ ] **Step 2: 新節「## M4シード(m4-seed)」を追記**

ファイル末尾(「## 要確認(needs_review)の扱い」の後)へ追記:

```markdown
## M4シード(m4-seed)

10 §2の規定(シードIntent 200〜300件・分布完全一致・18〜19歳含む)を満たす機能・非機能試験用のシード。**決定的生成器**(backend/src/latch/testassets/・`make seed-generate`/`make seed-verify`)で生成し、YAMLには生成コマンド引数(base_datetime・seed 42)を記録 — 同一引数での再生成はバイト同一。期待値表は2層(EX-xxx個別期待33件+aggregate集計)で、Layer 1の機械突合はbackend/src/latch/testassets/reference_l1.py(06 §2確定表のPython独立実装)が担う。詳細はm4-seed-plan.md。

### 既存3資産との関係整理

| 資産 | 状態 | m4-seedとの関係 |
|---|---|---|
| g2-jev-goldset.yaml(304 Intent+520ペア) | confirmed・**不変** | 評価ペア500件以上(09 §4.1)を充足済み。ペア構成を変えるとG2評価結果の前提が崩れるため一切編集しない。intents側は語彙・時刻パターン・カテゴリ配分の母集合として参照(g2-plan §7「母集合に再編」は「母集合を参照した決定的配分」の意・supervisor承認2026-10-03) |
| g1-alcohol.yaml(36件) | confirmed・**不変** | 飲酒判定専用入力セット30件以上(10 §2 FR-20)を充足済み。期待値表はこれを参照(新規作成しない) |
| g1-parser-struct.yaml(32+3件) | confirmed・**不変** | Parser構造化精度ゲート(09 §4.3)。m4-seedは新prefix(SU-/SD-/EX-)でid衝突を回避(上記のid一意規約どおり) |

10 §2の「シードIntentは09のゴールドセットを共用」の読み: 09側の評価ペア=g2(520ペア)、10側の機能試験素材=m4-seed。両方が本ディレクトリ配下でバージョン管理される状態が「同一の資産を参照する」の意。g2 intentsをそのまま機能試験に投入しない根拠(実測): 10 §2分布からの乖離(時間帯その他23%〔目標10%〕・activity 21%〔目標10%〕・夜20-22時台29%〔目標40%〕)+FR-17自己重複配置・合成users(生年月日)・raw_textを欠くため、そのままでは10 §3の配置ケースに使えない。

### id規約と基準日時

- ユーザー=SU-xxx・Intent=SD-xxx・期待値ケース=EX-xxx(既存P-/A-/E-/SI-/U-と衝突しない)
- 基準日時はYAML metaのbase_datetime(生成コマンド引数)。対象時刻は基準日のD+1〜D+5。staging投入(ws-3)の直前に同一コマンドで再生成する(YAML差分は対象日付のみ)
- 合成ユーザーは実在しない氏名(「合成+仮名+番号」)・連絡先フィールドは実装契約(users: display_name/birth_date/profile.bioのみ)に存在しないため持たない(10 §2は精神規定として充足)
```

- [ ] **Step 3: コミット**

```bash
git add docs/testassets/README.md
git commit -m "docs: add m4-seed positioning and asset relations to testassets README"
```

### Task 8: 完了条件の検証+報告書

**Files:**
- Create: `docs/plans/M4/ws-2-report.md`(§8の形式)

- [ ] **Step 1: §6の完了条件1〜8を順に実行し、§8の表に証拠を記入する**

(§7の検証手順1〜8・§6の検証コマンドどおり。全てPASSであることを確認する)

- [ ] **Step 2: 生成物の最終状態を確認**

Run: `git status --short && git diff --name-only main | sort`
Expected: §4の一覧(12ファイル)と完全一致・未コミット差分なし

- [ ] **Step 3: 報告書をコミット**

```bash
git add docs/plans/M4/ws-2-report.md
git commit -m "docs: add m4 ws-2 execution report"
```
