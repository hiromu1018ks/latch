# M0 ws-4(地物データ取り込み・ジオコーディング) 実行報告

- ブランチ: m0-ws-4 / ベース: 2c031d4
- 日付: 2026-09-27
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS | `50 files already formatted` / `All checks passed!` / `122 passed, 53 deselected in 0.56s`(unit) |
| 2 | integration試験(design §4-3〜6・8) | PASS(実行済み) | `make test-ci` → `175 passed in 2.11s`(unit 53件deselectedを含む全収集175件。geo系integration 23件を含む) |
| 3 | 0002マイグレーション(down_revision=0001・upgrade/downgrade) | PASS | `alembic history`: `0001 -> 0002 (head), geofeatures: …`。downgrade()は`DROP TABLE geofeatures`のみ(コード確認)。実DBは`alembic_version=0002`へ進めた |
| 4 | 実時間参照がclock.pyのみ | PASS | `rg -n 'datetime\.now\|utcnow\|time\.time\|time\.monotonic' backend/src` → `core/clock.py:33` の1行のみ。`test_arch_no_direct_time.py` PASS |
| 5 | geo配下のnetwork importゼロ | PASS | rg該当コマンド → ゼロヒット(空出力・exit=1)。`test_arch_geo_no_network.py` PASS |
| 6 | 依存追加がgeoグループのみ | PASS | `git diff --stat main -- backend/Dockerfile compose.yaml` → 出力なし。`git diff main -- backend/pyproject.toml` → `[dependency-groups]`へ`geo = ["osmium>=4.3"]`追加のみ |
| 7 | 触るファイルがスコープどおり | PASS | `git diff --name-only main \| sort` → §4の一覧(本報告書を含む21ファイル)と完全一致。settings.pyは追記のみ |
| 8 | G0証拠手順書の記載 | PASS | 下位節「スーパーバイザー向けG0証拠手順」参照 |

## test-ciの実行有無(STATUS.md運用ルール)
- **実行した**: 2026-09-27・Task 4〜9の各段階で合計5回(実行する都度`make test-ci`)。
  最終: `175 passed in 2.11s`。共有ci-dbの`alembic_version`を0001→0002へ進めた
  (スーパーバイザー指示「並走ws-3はDBに触れないためws-4がalembic状態を扱ってよい」に基づく。
  実行はfixtureデータ(isj_sample.csv+osm_sample.xml)のみ — 実データ不使用)
- make geo-download / geo-import / geo-verify(実データ)は**未実行**(G0証拠はスーパーバイザー実施)

## スーパーバイザー向けG0証拠手順(実データ確認・完了条件8)
1. `make geo-download` — OSM九州extractをcurl取得(`backend/data/geo/kyushu-latest.osm.pbf`。
   `-C -`付きでレジューム可)+ISJ(大字・町丁目レベル・鹿児島県分)の手動取得案内表示
   (https://nlftp.mlit.go.jp/cgi-bin/isj/dls/_choose_method.cgi から都道府県単位zip)
2. ISJのzipを解凍したCSVを `backend/data/geo/isj.csv` へ配置(ファイル名は年度で異なるためリネーム)
3. `make geo-import` — settings既定エリア(ci暫定=鹿児島市天文館周辺bbox)で取り込み。
   期待出力: `[import-isj] area=ci-provisional isj_town: <N> rows` と
   `[import-osm] area=ci-provisional osm_poi: <M> rows`(名称・市コードは出すが座標は出さない)
4. `make geo-verify` — 期待出力: `[verify] area=ci-provisional` /
   `  isj_town: N rows` / `  osm_poi: M rows` /
   `  forward '天文館': -> 鹿児島市天文館 (osm_poi/district)` 形式の行とsource別件数(座標なし)
   (※ ISJ取り込み後の実際の表記: 天文館がOSM place=neighbourhoodとして存在すれば
   osm_poi優先で上記形式。存在しない年度・地域では isj_town 側の町丁目名が出る)
5. `make test-ci` — unit+integration全体グリーン(alembic_version=0002)

## 固定値の変更有無(design.md §6・本計画§2)
- ci暫定エリア=鹿児島市天文館周辺約3km四方(bbox既定値): 変更なし
- 正転=正規化完全一致+決定的順位(osm_poi優先→id昇順): 変更なし
- 逆転=1kmグリッド丸め(3857)+最近傍+市区町村名補完: 変更なし
- geo-downloadの取得コマンド=curl(designのwgetから確定): 変更なし
- osmiumをgeo dependency-groupへ隔離+test-ciの--group geo化: 変更なし
- 出所・ライセンス明記の置き場所=backend/README.md: 変更なし

## コミット一覧
```text
7502d9f feat: geo配下network禁止のarch testとlatch.geo公開IF再export
9295009 feat: 地物取り込みCLI(import-isj/import-osm/verify)とMake geoターゲット・出所明記
bd8d3f4 feat: OSM名称付きPOI抽出(pyosmium・bboxフィルタ・way代表点)
f1b06b7 feat: 正転・逆転ジオコーディング(PostGIS完結・決定的順位・1kmグリッド丸め)
208b9d5 feat: geofeatures取り込み(source単位フルリロード・冪等・Clock由来created_at)
fa7240a feat: マイグレーション0002 geofeaturesテーブル+Index(GIST/btree)
d879426 feat: 位置参照情報CSV読み取り(cp932自動判定・ANDフィルタ・無効行スキップ)
aeb4fcc feat: 地物名称正規化(NFKC・記号除去・大字接頭辞・かな統一・丁目漢数字)
8836878 feat: geo依存グループ(osmium)とエリア設定3項目(T2後の差し替え用)
```
(git log --oneline main..HEAD の出力)

## 補足(詰まった点・判断した点)
1. **計画書Task 2の期待値「ｱｲ→ai」を「あい」へ修正** — NFKCで半角カナ「ｱｲ」は全角カナ
   「アイ」へ変換され、規則4(カタカナ→ひらがな統一・design §2.4)により実測は「あい」。
   計画書Step 5の注意(「規則に反しない場合のみ期待値を実測へ合わせよ」)に従い
   「あい」が正として採用(「ai」は計画書のタイプ。design §2.4の規則自体は不変)
2. **計画書Task 2実装コードの`_digits_to_kanji`バグを修正** — 提示コードでは
   10が「十〇」になり計画書自身のテスト期待「10丁目→十丁目」と矛盾。ones==0の位を
   省略(10→十・20→二十。ISJ大字町丁目名の漢数字表記と整合)
3. **計画書Task 3テストコードのfixtureパス誤りを修正** — `parents[1]`は
   `tests/unit/fixtures`を指すため`parents[2]`(= `tests/fixtures/geo/`)へ。
   §4スコープ一覧・design §3.1の配置と一致
4. **計画書Task 7「注意」の予期した失敗が発生し、指示どおり対処** —
   逆転試験(`test_reverse_osm_poi_city_complemented`)でグリッド丸め後にOSM「天文館」
   (node/1)が最近傍となり期待値不一致。計画書指示に従いfixtureのバー宵待(node/2)を
   (130.5575, 31.5955)→(130.5460, 31.5840)へ移動(全地物から≥約1.8km離し、
   丸め最大707mの2倍より大で決定的に)+`test_forward_osm_poi`の期待座標と逆転入力座標を追従
5. **計画書の試験件数の数え誤り2件**(動作・網羅に影響なし): Task 4の「6試験収集」は
   実際5試験、Task 9の「PASS(11件)」は実際10件(9+再export1)
6. **lint対応**: ruff format/isort適用分(settings.py・__init__.pyほか)と、計画書
   テストコード内のE501超過行の折り返し(SQL文字列の空白変更のみ・意味不変)。
   Task 5で一時的に外した前方import(FeatureRow等)はTask 5で復帰(F401対応)
7. osmium 4.3.1のtransitive依存(requests/urllib3)はgeoグループ内のみに解決。
   `uv sync --frozen --no-dev`(Dockerfile)はgeoを入れないためapi/workerイメージ不変
   (条件6のとおりDockerfile・compose.yamlへの差分なし)
8. **最終レビュー(opus・fresh context)後の修正**: 計画書Review Focus #2の市コード側
   (「市コード設定文字列の形式破損は明確なValueErrorで即失敗」)が計画書自身の
   `_effective_area` 実装コードでは未実装だった(計画書側の欠落)。タイポ
   (`4620l` 等)が通ると0行フルリロード(既存isj_town行のDELETEを含む)がexit 0で
   黙って成功するため、`_effective_area` に検証を追加: 市コードトークンはASCII数字のみ・
   空セットは拒否(いずれもValueError)。テスト2件追加(RED→GREEN・全suite 177 passed)。
   5桁固定や「0行取り込みの警告」は追加せず見送り(固定値を増やさない最小修正。
   レビューのMinorとして記録)
9. **G0実データ実行時の注意(レビュー勧告・スーパーバイザー向け)**:
   `osm.py` の `FileProcessor.with_locations()` はノード位置のインメモリキャッシュを
   持つため、九州extract(数億ノード)の実行時はメモリ使用を注視すること(GB級になり
   得る。必要ならbbox事前extractを検討)。また geo-verify → test-ci の順序が正しい
   (test-ciのfixtureフルリロードがgeofeaturesの実データを上書きするため — 本報告書の
   手順どおり)
