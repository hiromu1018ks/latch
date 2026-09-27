# M0 ws-4(地物データ取り込み・ジオコーディング)設計メモ

- 作業単位: ws-4 — 地物データ取り込み(位置参照情報+OSM→PostGIS)・正転/逆転ジオコーディング(docs/plans/STATUS.md M0表・依存「ws-1(PostGIS)」)
- 作成: 2026-09-27(agent1 / superpowers:brainstorming使用)
- 次工程: 計画書(ws-4-plan.md)へ変換 → 実装エージェントがworktree内でTDD実装
- 実装言語: Python (FastAPI)(main a247243時点の基盤: 雛形+ws-1(0001・PostGIS拡張済み)+ws-2の上に載せる)
- 制約: マイグレーション0002を追加する単位であるため、共有ci-dbでのtest-ci実行はws-3(並走)と同時にしない(STATUS.md「運用ルール」)

## 1. 目的と前提となるdocsの確定値

### 1.1 この単位の目的

依存ハード制約**C5(地物データのPostGIS取り込みがIntent active作成の前提。ジオコーディング不成立は422 GEOCODING_FAILEDで作成拒否。MVP対象エリア分の初期取り込みが必要)**を解消し、G0完了条件の4項目め**「対象エリアの地物データでジオコーディング(正転・逆転)がPostGIS上で完結する(外部送信経路ゼロ)」**を証明できる状態を作る。具体的には (1)地物テーブル(マイグレーション0002)、(2)取り込みツール(位置参照情報CSV+OSM抽出→PostGIS)、(3)正転(location.name→座標)・逆転(座標→地域名)のクエリ関数、の3点を作る。**保存APIへの組み込み(422 GEOCODING_FAILEDの契約実装)はM1**(12 M1スコープ5「ジオコーディング(正転)の保存処理組み込み」)。本単位は「関数+取り込み+試験」で完結を証明する(ws-2と同じ位置づけ — 呼び出し側は後続フェーズ)。

### 1.2 引用確定値(節番号つき)

| # | 確定値 | 出典 |
|---|---|---|
| 1 | ジオコーディング(正転・逆転)の選定は**「地物データのPostGIS取り込み+自前変換(国土地理院位置参照情報+OSM)」**。第1条件は位置情報の外部送信経路を持たないこと(08 D-11)。既存PostgreSQL+PostGISへ地物テーブルを追加するだけでコンポーネント数が増えない。地名解決の精度が不足した段階でセルフホストNominatimへ差し替え可能 | 04 第3節技術スタック表 |
| 2 | 正転(地名→座標)はParser通過後・Intent保存前の処理で、location.nameを**位置参照情報の町字・街区代表点とOSMの名称付きPOI(ランドマーク)へ照合**し、geo_centerを定める。逆転(座標→地域名)はLayer 5の提案生成で、geo_centerを**約1kmグリッド(投影座標系へ落として丸め)の代表点に最も近い地物の地名**(例: 鹿児島市天文館)を地域名としてproposalへ格納する。**いずれもPostGIS上の地物テーブルに対するSQLで実装し、位置情報を外部サービスへ送る経路を持たない。**地物データはMVP対象エリア分を初期取り込みし、展開地域の拡大時に追加する | 04 第3節「ジオコーディングの変換経路」 |
| 3 | API保存処理内でlocation.nameをジオコーディングし(04 第3節の正転、セルフホスト地物データ)、intents.geo_centerを確定する。**該当地物が存在しない場合は422 GEOCODING_FAILED**。クライアントは条件リストへ戻して修正を促す。この検証はstatus=activeの作成・更新に適用(draftではgeo_centerはNULL可、active化時に確定) | 05 第5節 POST /v1/intents「受け渡しと検証の規則」 |
| 4 | intents.geo_centerは `geography(Point,4326)`。active行ではNULL不可(active化経路で保証)。geo_radius_mは未指定1,000m既定(03 D-19) | 05 第2節 |
| 5 | proposal.area_nameの生成元: geo_centerを約1kmグリッドへ丸めた代表点の地物名(04 第3節・08 D-11の逆転ジオコーディング)。**座標は格納しない** | 05 第2節「proposalの構造」 |
| 6 | 相手への位置表示は地域名のみ。内部のgeo_centerから表示への変換は、中心点を約1kmのグリッドへ丸め、その代表点の地名を用いる。変換結果の地域名のみをproposalへ格納。ジオコーディングの手段はセルフホストの地物データ(位置参照情報+OSM)による内部変換(04 v0.2で確定済み・位置情報の外部送信経路なし) | 08 第4節 D-11 |
| 7 | ログ・計測・Analyticsの出力に、Intent原文、正確な位置(座標)、NG条件を含めない。構造化ログは許可リスト方式(raw_text・geo座標・constraints系フィールドは不許可) | 08 第2.4節 |
| 8 | M0スコープ6の文言: 「地物データ取り込み(国土地理院位置参照情報+OSM→PostGIS、対象エリア分。04 第3節)」。G0完了条件: 「対象エリアの地物データでジオコーディング(正転・逆転)がPostGIS上で完結する(外部送信経路ゼロ。08 第4節)」 | 12 第3節 M0 |
| 9 | 初期エリア=駅周辺半径3km相当(行政単位でなく「駅周辺3km」のエリア定義で運用)。候補都市を4基準でスコアリングしプロダクトオーナーが確定 — **T2(人間領域・未確定)**。地物データ取り込み範囲の確定がT2の締切 | 11 第2節 D-22・12 第4節T2 |
| 10 | ci環境: 最小構成・常設(API 1/Worker 1/DB共用)。DBはPostGIS 3.6.4同梱(compose常設・名前付きボリューム) | 10 第1節・STATUS.md完了記録 |
| 11 | 時刻参照はすべてClockインターフェース経由(arch testが `backend/src` 全体で強制。取り込みのcreated_atも対象) | 04 第5節(FR-41)・雛形 test_arch_no_direct_time |
| 12 | マイグレーション追加単位(ws-4)とDB消費単位(ws-3)のtest-ci実行は同時にしない。スーパーバイザーは検証・マージ時にtest-ciを直列実行(古い単位→マイグレーション追加単位の順)。マージ済みmainのtest-ciがDB状態の唯一の真実 | STATUS.md「運用ルール」 |

### 1.3 docs表記と実データ配信元の対応(位置参照情報)

docs(04 第3節ほか)は「国土地理院位置参照情報」と呼称する。現行の**配信元は国土交通省の「位置参照情報ダウンロードサービス」(nlftp.mlit.go.jp)**であり、2026-09時点で稼働している(2026-05-29に令和7年度版 街区レベル・大字・町丁目レベルの提供開始を告知)。データは同一の位置参照情報系列であり、本設計はこのサービスから取得する。呼称の相違はdocs側の総称表記として扱い、docs改版は不要と判断する(データの中身・形式は下記§1.5の公式仕様どおり)。

### 1.4 スコープ外(後続単位へ渡すもの。ws-4では作らない)

- **保存APIへの組み込み**(POST/PATCH /v1/intentsでのジオコーディング呼び出し・422 GEOCODING_FAILED応答・draft扱い分岐) → **M1**(12 M1スコープ5)
- **逆転の消費側**(Layer 5のproposal.area_name生成への組み込み) → **M2**(06 Layer 5。05 第2節の生成元定義に従う)
- Layer 1の地理条件(ST_DWithin等) → **M2**(12 M2スコープ3)
- あいまい照合(trigram・表記ゆれ辞書)・Nominatim差し替え → 精度不足が判明した段階(04 第3節の差し替え規定。§6論点3)
- 展開地域拡大時の追加取り込み運用(エリア追加は設定変更+再実行で対応できる構造のみ作る)
- ステージング/本番への取り込み実行(本単位の証明はci環境。暫定エリア=§2.6)

### 1.5 外部データの確定事実(実装が依存する取得元・形式)

**位置参照情報(大字・町丁目レベル)— 名称付き住所代表点。**

- 取得元: 位置参照情報ダウンロードサービス https://nlftp.mlit.go.jp/cgi-bin/isj/dls/_choose_method.cgi (都道府県単位のzip。解凍して得られるCSVをツールへ渡す)。形式仕様: https://nlftp.mlit.go.jp/isj/data.html
- 形式: CSV。**文字コードはSHIFT-JIS**(数字コードはASCII)。緯度経度は世界測地系(JGD2000)の十進度(小数第6位)
- 項目(10列、この順): 都道府県コード / 都道府県名 / 市区町村コード / 市区町村名 / **大字町丁目コード(12桁)** / **大字町丁目名(町丁目の数字は漢数字)** / 緯度 / 経度 / 原典資料コード / 大字・字・丁目区分コード(1:大字 2:字 3:丁目 0:不明)
- 全国を網羅し毎年度更新。利用約款は出所明記を要求(§2.9)

**OSM(OpenStreetMap)— 名称付きPOI(ランドマーク)。**

- 取得元: Geofabrik extract https://download.geofabrik.de/asia/japan.html 。日本全体 `japan-latest.osm.pbf`(約2.3GB)+ **地方別extract**(例: 暫定エリアが九州なら `https://download.geofabrik.de/asia/japan/kyushu-latest.osm.pbf`)
- 形式: osm.pbf(バイナリ)。pyosmiumで直接読む。テスト用にはOSM XML(.osm/.xml)も同じ経路で読める
- ライセンス: ODbL。クレジット表記が必要(§2.9)

**解析ライブラリ: pyosmium(PyPIパッケージ名 `osmium`)。**

- v4.3.1・Python >=3.8・**cp313のmanylinux wheelあり**(ローカルビルド不要)。sdistにlibosmium/protozero/pybind11同梱
- 注意: パッケージのランタイム依存に `requests` が含まれるが、これはpyosmium自身のダウンロード補助機能用。本設計では**ネットワーク機能を使わずローカルファイル読み取りのみ**に用いる(§4 arch試験で「geo配下のコードがrequests/urllib/socket/httpx等をimportしない」ことを強制する)

## 2. 実装方式の選択肢と推奨

### 2.1 地物テーブルの構成 — 推奨: source列を持つ単一テーブル `geofeatures`

選択肢:

- **A(推奨). 単一テーブル `geofeatures`(source列でISJ/OSMを区別)+ 正規化済み名称列**
- B. 系統別テーブル2つ(isj_towns / osm_pois)+ UNION ALLビュー
- C. PostGISの外部テーブル(FDW)やgeometryの都度計算(テーブルなし)

推奨の根拠: 04 第3節は正転・逆転とも「PostGIS上の**地物テーブル**に対するSQL」で単数形のテーブル像で書かれており、照合対象(町字代表点・POI)は同じ「名称+点ジオメトリ」の形をする。単一テーブルは (1)正転・逆転の両SQLが1つのテーブルへの1クエリで書ける(UNION不要=決定性・性能・保守性)、(2)取り込みのフルリロードが「source値でDELETE→INSERT」の1パターンで冪等、(3)将来エリア拡大・街区レベル追加(§6論点2)が行追加で済む。Bは系統別制約をDB型で表せる利点があるが、クエリが必ずUNION/ビューを挟み、逆転の「代表点に最も近い地物」(04 第3節)が全地物横断の単一ランキングである要件と形が合わない。Cは取り込み成果の永続化を持たず、検証可能性(G0)に反する。

トレードオフ: Aは系統固有の列(ISJのコード類/OSMのタグ)を`attrs jsonb`に寄せるため、系統別の型保証はアプリ層になる。ws-1設計と同じ線(CHECKはdocs明記分のみ)で扱う。

### 2.2 取り込みの実行形態 — 推奨: ホスト実行のCLI(`python -m latch.geo`)+Makeターゲット

選択肢:

- **A(推奨). `backend/src/latch/geo/__main__.py` のCLI(subcommand: import-isj / import-osm / verify)。`make geo-import` 等のMakeターゲットから `uv run --group geo python -m latch.geo ...` で実行(alembic migrateと同じ「ホストのuvからcompose常設DB(127.0.0.1:5432)へ接続」パターン)**
- B. alembicのデータマイグレーションとして取り込み
- C. APIの管理エンドポイント(POST /admin/geo/import 等)

推奨の根拠: 04 第3節は取り込みを「初期取り込み」「拡大時に追加」の**運用操作**と位置づけており、実行形態はスクリプトが自然。Bはデータマイグレーションに大量INSERT・外部ファイル依存・再実行(鮮度更新)を持たせることになり、alembicの役割(スキーマ版数管理)と合わない。Cは認証・エラー処理・実行時間(API同期)の課題を増やし、05 第5節のAPI一覧に存在しない契約を増やす(12 運用ル則4「完了条件にない追加作業」に触れる)。Aは既存の`migrate`ターゲットと同じ実行形態で、DockerイメージにもAPI経路にも影響しない。

トレードオフ: 取り込みをCIで自動実行することはできない(実行は人間/スーパーバイザー)。G0の証明は「geo-import実行→geo-verify実行」の記録で行うため支障なし(§5)。

### 2.3 OSM抽出 — 推奨: pyosmiumでpbf/xmlを直接フィルタ読み取り

選択肢:

- **A(推奨). pyosmium(osmiumパッケージ)でpbfを読み、タグ条件(名称付きPOI)+bboxでフィルタしながら行を生成**
- B. osmium-tool CLIをサブプロセス実行(extract→export→CSV)
- C. Overpass APIから対象bboxのPOIを取得

推奨の根拠: Aは (1)ciホストのvenvにwheelで入る(osmium-toolのシステム導入不要)、(2)pbfとテスト用XMLを同じコードパスで読める、(3)フィルタがPython関数として型・試験で守れる、(4)完全オフライン・決定的(同じpbfから同じ行集合)。Bはシステム依存(osmium-toolのapt導入)をホストに課し、Docker desktop環境での再現性を損なう。CはネットワークAPIへの依存(可用性・非決定性・レート制限)を取り込み経路に持ち込み、決定的な再取得ができない。

トレードオフ: pbfの読み取りは1回数分(九州extract数百MB)。取り込みは頻繁に実行する操作ではないため許容する。

POI抽出条件(名称ありのノード/ウェイのみ。kindはこの判定に由来):

- `amenity` ∈ {bar, pub, biergarten, cafe, restaurant, fast_food, food_court, ice_cream, nightclub}
- `railway=station` / `highway=bus_stop` / `tourism=hotel`
- `place` ∈ {suburb, quarter, neighbourhood}(天文館・一番町のような商業地名・ランドマークの多くはこの階層)
- `name`(または`name:ja`)を持たない要素は除外。geomはノード=その点、ウェイ=ST_Centroid相当(代表点)

### 2.4 正転(location.name→geo_center)の照合 — 推奨: 正規化完全一致+決定的順位

04 第3節は「照合」とだけ規定し照合アルゴリズムを確定しない。M0の選択肢:

- **A(推奨). 取り込み時に名称を正規化して `normalized_name`(単独名)と `full_normalized_name`(市区町村名連結、ISJのみ)を事前計算し、入力も同じ正規化関数を通して**完全一致(どちらかの列)**で検索。複数候補は「osm_poi優先→id昇順」の決定的順位で1件**
- B. pg_trgm等のあいまい一致
- C. 形態素解析による住所分割(geocoder的パース)

推奨の根拠: 検証対象は「地名解決がPostGISで完結する」ことであり、一致率の最適化はM1のParser精度評価(07 D-17のlocation 90%ゲート)とセットで初めて測れる。Aは (1)照合がインデックス効く等値クエリ(btree)で決定的、(2)正規化関数は純函数としてunit試験で守れる、(3)取り込み対象が単一エリア(表に載るのはそのエリアの地物のみ)なので市を跨ぐ同名衝突が構造的に起きない。B/Cは精度不足が実測された段階の拡張(§6論点3)。完全一致で候補0件→`None`を返し、M1が422 GEOCODING_FAILEDへ写像する(05 第5節)。

名称正規化関数(`normalize.py`・純函数): NFKC変換 → 空白類(全角含む)・区切り記点の除去 → 「大字」接頭辞の除去 → カタカナ→ひらがな統一 → 丁目の算用数字→漢数字変換(「1丁目」→「一丁目」。ISJの大字町丁目名は漢数字) → ASCII小文字化。入力(07 Parser出力)と地物名の双方に適用する同じ関数とする。

### 2.5 逆転(geo_center→area_name)のクエリ — 推奨: 1kmグリッド丸め+最近傍1件

04 第3節・08 D-11の規定(約1kmグリッドへ丸め→代表点に最も近い地物の地名)をSQLで直接表す:

```sql
WITH input AS (
  SELECT ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) AS g
),
grid AS (  -- 約1kmグリッド丸め(投影座標系へ落として丸め — 04 第3節)
  SELECT ST_Transform(
           ST_SnapToGrid(ST_Transform(input.g, 3857), 1000.0), 4326) AS g
  FROM input
)
SELECT source, kind, name, city_name, pref_name
FROM geofeatures, grid
ORDER BY ST_Distance(geofeatures.geom, grid.g),  -- geography距離(メートル)
         CASE source WHEN 'osm_poi' THEN 0 ELSE 1 END,
         id
LIMIT 1;
```

- **area_name = 市区町村名 + 地物名**(08 D-11の例「鹿児島市天文館」の形式)。地物がOSM POI(city_nameを持たない)の場合は、同じグリッド代表点に最も近い`isj_town`行の市区町村名を第2クエリで得て連結する(エリア内にISJ町丁目が存在しない場合のみname単独)
- グリッドの基準は`ST_SnapToGrid`の原点(0,0)基準=一意に決まる(代表点の決定性)
- 順位のtie-breakは距離→source(osm_poi優先)→idで固定し、同一入力に対する応答を決定的にする(10 第1節の「決定的にする」と同じ精神)
- 地物テーブルが空(未取り込み)の場合は`None`(エリア内は取り込み済みが前提。M1/M2側で扱う)
- 数千行規模のテーブルに対する距離ソート+GIST索引でミリ秒 ORDER(04 第7節のRetrieval予算に影響しない規模)

### 2.6 エリアの与え方とci暫定エリア — 推奨: settingsでエリア定義・**最終指定はT2**

T2(11 第2節の4基準で候補都市をスコアリングしプロダクトオーナーが確定)は未確定のため、**エリアは設定で与える構成**とする(supervisor指示)。取り込みフィルタとして次をsettingsに持つ:

```python
# settings.py(追記分)
geo_area_name: str = "ci-provisional"   # エリアの表示名(記録用)
geo_isj_city_codes: str = "46201"       # カンマ区切り。ISJ取り込みの市区町村コードフィルタ
geo_osm_bbox: str = "130.5420,31.5825,130.5740,31.6095"  # min_lon,min_lat,max_lon,max_lat
```

- **フィルタの意味**: ISJ行は「市区町村コードがgeo_isj_city_codesに含まれる **かつ** 代表点(緯度経度)がbbox内」。OSM要素は「代表点がbbox内」。地物の代表点がbbox内、という同じ基準で両系統を切る(境界の町丁目の取りこぼしは逆転の最近傍が拾う)
- **ci暫定エリア(提案・T2確定後に差し替え)**: docsの例示地名(04 第3節・08 D-11「鹿児島市天文館」、11 第2節の候補形「地方中核都市の中心市街地」)に合わせ、**鹿児島市天文館周辺・約3km四方**をbboxで暫定指定する(上記既定値=中心約(130.558, 31.596)±約1.5km)。11 第2節の「駅周辺半径3km」相当の1/2角。見積件数: ISJ(鹿児島市分・bbox内)数十〜百件強、OSM POI数千件 — テーブル全体で数千行
- **エリア差し替えは設定値変更+`make geo-import`再実行のみ**(コード変更不要)。これが「展開地域の拡大時に追加」(04 第3節)への対応形
- CLI引数(`--city-codes` / `--bbox`)でsettings値を上書きできる(取り込み時の柔軟性。テスト・T2後の切替に使う)

### 2.7 依存追加 — 推奨: `osmium` を専用dependency-group `geo` へ

選択肢:

- **A(推奨). `[dependency-groups] geo = ["osmium>=4.3"]` を追加。geo系の実行(`make geo-*`・geoのintegration試験)は `uv run --group geo` で明示的に組む**
- B. main dependenciesへ追加(api/workerイメージに組込み)

推奨の根拠: `uv sync --frozen --no-dev`(backend/Dockerfile)はデフォルトグループ(dev)以外を入れないため、**Aならapi/workerイメージはosmium(とその依存requests)を含まず不変**(既存Dockerfile・compose.yaml無変更)。osmiumは取り込みツールとその試験だけが必要とする。`make test`(unitのみ・osmium不要)も無変更のまま、`make test-ci`だけ `uv run --group geo pytest` に変える。

トレードオフ: Makefileのtest-ci行を修正する(既存ターゲットへの触れ。ws-3はMakefileを触る予定がなく、衝突リスクは低い。マージ順はスーパーバイザー管理)。

### 2.8 マイグレーション0002の位置づけ

- `down_revision = "0001"`。内容は`geofeatures`テーブル+Index2つ(§3.1)のみ。**データ取り込みはマイグレーションに含めない**(§2.2)
- ws-1の規約を引き継ぐ: 時刻列にDB時刻関数のDEFAULTを付けない(created_atはClock由来の明示値)、拡張のCREATE/DROPは行わない(postgisは0001が作成済み)
- downgradeは`DROP TABLE geofeatures`のみ
- 0002を追加する関係で、STATUS.md運用ルールどおり**ws-4のtest-ciはws-3と同時実行しない**。実装エージェントはunit試験で開発し、報告書に「test-ci=スーパーバイザー検証待ち」と記録してよい

### 2.9 出所・ライセンスの明記(取り込みに付随する義務)

- ISJ(位置参照情報): 利用約款が出所明記を要求 → README(またはbackend/README)に「位置参照情報(国土交通省)を利用」の明記と、データの年度を記録
- OSM: ODbL → READMEにOpenStreetMap contributorsクレジットを記載
- 実装でREADMEへの追記を1ブロックにまとめる(新設ファイルにするかREADME末尾への追記は計画書で確定)

### 2.10 採用しないもの(YAGNIによる切り捨て一覧)

- 街区レベル位置参照情報の取り込み(04 第3節の「町字・街区代表点」の文言に対し、M0は大字・町丁目レベル+OSM POIで開始。街区レベルは同一テーブルへのsource追加+取り込み拡張で後から足せる。§6論点2)
- あいまい照合(pg_trgm)・住所パーサー・よみがな辞書(§2.4。精度はM1のゲートで実測してから)
- 逆転のグリッド事前計算テーブル・キャッシュ(数千行×ミリ秒クエリに不要)
- geofeaturesの更新・差分同期(フルリロードで十分。鮮度運用は§6論点4)
- 地物のポリゴン保持(04 第3節は代表点への照合のみ要求。面ジオメトリの用途はdocsにない)
- API/Workerプロセスからの取り込み・geoモジュールのプロセス統合(M1で保存経路が、M2でLayer 5が統合する)
- Overpass API・osmium-tool・Nominatim(§2.3・§2.1)

## 3. ファイル構成

### 3.1 作るもの

```text
backend/alembic/versions/0002_geofeatures.py   # geofeaturesテーブル+Index(§2.8)
backend/src/latch/geo/
├── __init__.py            # 公開IFの再export(GeoService, ingest関数, FeatureNotFound等)
├── normalize.py           # normalize_name(純函数。§2.4の規則)
├── isj.py                 # 位置参照情報CSV読み取り(cp932/utf-8自動判定・10列対応・フィルタ)
├── osm.py                 # OSM読み取り(pbf/xml・名称付きPOI抽出・bboxフィルタ)
├── ingest.py              # DB取り込み(source単位のフルリロード: DELETE→一括INSERT・件数返却)
├── service.py             # 正転・逆転のクエリ実装(§2.4・§2.5のSQL)
└── __main__.py            # CLI: import-isj / import-osm / verify(subcommand+argparse)
backend/tests/unit/geo/
├── test_normalize.py      # 正規化規則(NFKC・空白・カナ・大字接頭辞・丁目数字)
└── test_isj_parse.py      # cp932読み取り・列対応・市コード/bboxフィルタ(fixture CSV)
backend/tests/integration/
└── test_geo.py            # 0002スキーマ・取り込み冪等・正転/逆転・決定性(§4)
backend/tests/fixtures/geo/
├── isj_sample.csv         # cp932・合成データ(市内bbox内/市コード違い/bbox外の行を含む)
└── osm_sample.xml         # OSM XML・合成データ(名称付きPOI/名称なし/bbox外を含む)
backend/tests/unit/test_arch_geo_no_network.py  # geo配下のnetwork import禁止(§4)
```

スキーマ(0002)とインターフェースの要旨(実装詳細は計画書・TDDで確定):

```sql
CREATE TABLE geofeatures (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source text NOT NULL CHECK (source IN ('isj_town', 'osm_poi')),
    kind text NOT NULL,                    -- isj_town='town' / osm=タグ由来('restaurant','bar',…'district'等)
    name text NOT NULL,                    -- 表示名(ISJ=大字町丁目名、OSM=name)
    normalized_name text NOT NULL,         -- normalize_name(name)
    full_normalized_name text,             -- ISJのみ normalize_name(市区町村名||name)。OSMはNULL
    pref_name text,                        -- ISJ由来(OSMはNULL)
    city_name text,                        -- 同上
    source_code text NOT NULL,             -- ISJ=大字町丁目コード(12桁) / OSM="node/12345"等
    attrs jsonb NOT NULL DEFAULT '{}',     -- ISJ=原典資料コード・区分コード / OSM=主要タグ
    geom geography(Point, 4326) NOT NULL,  -- 代表点
    created_at timestamptz NOT NULL        -- Clock由来(§1.2確定値11)
);
CREATE INDEX idx_geofeatures_geom ON geofeatures USING GIST (geom);
CREATE INDEX idx_geofeatures_name ON geofeatures (normalized_name);
```

```python
# service.py
class Geofeature(BaseModel):
    source: Literal["isj_town", "osm_poi"]
    kind: str
    name: str
    city_name: str | None
    pref_name: str | None
    lon: float
    lat: float

class GeoService:
    """正転・逆転(04 第3節・08 D-11)。外部送信経路を持たない(SQLのみ)。"""
    def __init__(self, engine: AsyncEngine) -> None: ...

    async def geocode_forward(self, name: str) -> Geofeature | None:
        """正転。normalize_name(name)で完全一致(normalized_name / full_normalized_name)。
        候補は osm_poi優先→id昇順で1件。該当なし=None(M1が422 GEOCODING_FAILEDへ写像)。"""

    async def reverse_geocode(self, lon: float, lat: float) -> str | None:
        """逆転。1kmグリッド丸め→最近傍地物→'市区町村名+name'(§2.5)。地物なし=None。"""

# ingest.py
async def import_features(engine: AsyncEngine, clock: Clock,
                          rows: Iterable[FeatureRow], source: str) -> int:
    """sourceの行をフルリロード(DELETE WHERE source=... → 一括INSERT)し件数を返す。
    冪等: 同一入力を再実行しても同一状態(§4で試験)。"""

# __main__.py(実行形態は§2.2)
#   uv run --group geo python -m latch.geo import-isj  --csv <path> [--city-codes 46201] [--bbox ...]
#   uv run --group geo python -m latch.geo import-osm  --pbf <path> [--bbox ...]
#   uv run --group geo python -m latch.geo verify [--forward 天文館] [--reverse LON LAT]
#   (引数省略時はsettings既定値=ci暫定エリア。verifyは名称・市区町村名・件数のみ出力し
#    座標は出力しない — 08 第2.4節の趣旨で出力の最小化に統一)
```

Makefile追記(test-ci行の変更+3ターゲット):

```makefile
geo-download: ## 地物データ取得(OSM九州extractをcurl。ISJ入手先と手順を表示)
	wget -c -O backend/data/geo/kyushu-latest.osm.pbf https://download.geofabrik.de/asia/japan/kyushu-latest.osm.pbf
	@echo "ISJ(大字・町丁目レベル)は下記から手動取得し backend/data/geo/ へ配置:"
	@echo "  https://nlftp.mlit.go.jp/cgi-bin/isj/dls/_choose_method.cgi (都道府県単位のzip→解凍したCSV)"

geo-import: ## settingsのエリア設定でISJ+OSMをPostGISへ取り込み(CSV/PBFはgeo-downloadの配置先)
	cd backend && uv run --group geo python -m latch.geo import-isj --csv data/geo/isj.csv
	cd backend && uv run --group geo python -m latch.geo import-osm --pbf data/geo/kyushu-latest.osm.pbf

geo-verify: ## 正転・逆転のサンプル確認(PostGIS完結の動作確認・座標は出さない)
	cd backend && uv run --group geo python -m latch.geo verify

test-ci: ## ci環境試験(compose起動 → unit+integration。--group geoでosmium込み)
	docker compose up -d --wait
	cd backend && uv run --group geo pytest
```

(`backend/data/geo/` はgitignoreへ追加。ダウンロード済みファイルはコミットしない。wgetをcurlにするかは計画書で確定)

### 3.2 触るもの(既存ファイルへの追記・修正)

- `backend/src/latch/settings.py` — geo設定3項目の追記(§2.6。ws-2と同じ追記スタイル)
- `Makefile` — test-ci行の修正(§2.7)+geoターゲット3件の追記
- `backend/pyproject.toml` — `[dependency-groups]` へ `geo = ["osmium>=4.3"]` 追記(uv.lock更新)
- `README.md` または `backend/README.md` — 出所・ライセンス明記(§2.9)
- `.gitignore` — `backend/data/` 追加

### 3.3 触らないもの

- `backend/src/latch/main.py`・`worker/`・`core/`(clock.py・db.pyはimportのみ。M1/M2が統合する)
- `backend/alembic/versions/0001_initial_schema.py`(0002を追加するのみ)
- `backend/src/latch/llm/`・既存テスト群・`tests/conftest.py`・`tests/integration/conftest.py`(geoのintegration試験は`migrated_db`/`db_engine`fixtureを**参照のみ**する。フィクスチャ自体に触ると並走ws-3との共通ファイル衝突になる)
- `backend/Dockerfile`・`compose.yaml`・`docker/`(§2.7。イメージ不変)
- `docs/01〜12`・`docs/reviews/`・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/M0/`の他ファイル
- `prototype/`・`.claude/`・`.mise.toml`

## 4. テスト方針

外部プロセス不要のunit(毎コミット)と、compose常設DBを使うintegration(test-ci・スーパーバイザー直列実行)に分ける。OSM解析はosmium依存のためintegration側に置く(`make test`はグループなしで動くまま)。

1. **unit: 正規化規則**(normalize.py)— NFKC(全角英数→半角等)・空白・区切り記点除去・「大字」接頭辞除去・カタカナ↔ひらがら統一・「1丁目」→「一丁目」変換・ASCII小文字化。表形式の期待値試験
2. **unit: ISJ読み取り**(isj.py)— cp932読み取り(fixtureはcp932で保存)+utf-8フォールバック判定・10列の列対応・市区町村コードフィルタ・bboxフィルタ・無効行(緯度経度欠損)の除外と件数
3. **integration: スキーマ**(0002)— geofeaturesテーブル・CHECK(source)・Index(GIST/btree)の存在。`migrated_db`fixtureでhead=0002まで進むことを兼ねる
4. **integration: 取り込みと冪等** — fixture(isj_sample.csv+osm_sample.xml)を`import_features`で取り込み、件数・source別行数を検証。**2回実行して行数・内容が不変**(フルリロードの冪等性)
5. **integration: 正転**(G0証明の前半)— (a)osm_poiの名称がヒットし座標が返る、(b)ISJ町丁目名がヒットする、(c)市区町村名連結(「鹿児島市○○町」形式)でも`full_normalized_name`経由でヒットする、(d)存在しない名称→`None`(422 GEOCODING_FAILEDの供給元)、(e)POIと町丁目の同名ではosm_poiが選ばれる(決定的順位)
6. **integration: 逆転**(G0証明の後半)— (a)座標→「市区町村名+地物名」のarea_nameが返る、(b)**同一の1kmグリッドセルに属す2点が同一のarea_nameを返す**(08 D-11の丸め粒度)、(c)隣接セルで別の地物が最近傍になる場合に名前が変わる、(d)同一入力の反復実行で同一結果(決定性)、(e)空テーブル(取り込み前)→`None`
7. **unit: CLI引数解析** — `import-isj`/`import-osm`/`verify`の引数解析とsettings既定値の解決を関数として切り出して試験(subcommand実行そのもの=実DB接続は、スーパーバイザーによる`make geo-import`/`make geo-verify`実行で確認。サブプロセス試験は速度・DB併合の観点で作らない)
8. **arch: geo配下のネットワーク禁止**(`test_arch_geo_no_network.py`)— `backend/src/latch/geo/` のソースに `requests`・`urllib`・`socket`・`httpx`・`aiohttp`・`http.client` のimportが出現しないことを機械検査(「位置情報を外部サービスへ送る経路を持たない」04 第3節のコード検査。osmium自体のimportは許可 — §1.5のとおりローカルファイル読み取りのみに使用)。既存`test_arch_no_direct_time`はsrc全体を自動スキャンするため、geo配下の時刻Clock経由も既存試験で強制される

G0「対象エリアの地物データでジオコーディング(正転・逆転)がPostGIS上で完結する(外部送信経路ゼロ)」の証拠は、(1)§4-5・6のintegration試験グリーン(fixtureデータでの完結)+ (2)**スーパーバイザーによる実データでの確認**(`make geo-import`(暫定エリア)→ `make geo-verify` の実行記録)+ (3)§4-8のarch試験(経路ゼロのコード検査)の3点とする。

## 5. 完了条件(この単位の受渡し判定。計画書が参照する)

1. `make lint`・`make test` がグリーン(unit追加分を含む)
2. §4-3〜6・8のintegration試験がグリーン(実装エージェントはunitで開発し、test-ciは「スーパーバイザー検証待ち」として報告してよい — STATUS.md運用ルール)
3. マイグレーション0002が`down_revision="0001"`で追加され、`alembic upgrade head`でgeofeatures+Indexが作成されること・downgradeでテーブルのみDROPされること
4. `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットが引き続き `core/clock.py` のみ(geo配下を含む全体規律の維持)
5. `rg -n 'import requests|import urllib|import socket|import httpx|import aiohttp|import http\.client' backend/src/latch/geo/` がゼロヒット(§4-8と同一内容の手動確認)
6. 依存追加が `geo` グループのみ(`backend/Dockerfile`・`compose.yaml` に差分なし)
7. 触るファイルが§3.2の範囲内(main.py・worker/・core/・0001・llm/・conftest.py群に差分なし)
8. G0証拠(2)のための手順書が報告書に含まれること: `make geo-download`→`make geo-import`→`make geo-verify` の実行方法と期待出力(名称・市区町村名。座標なし)

## 6. 未解決の論点(設計は推奨で固定済み。supervisor確認事項)

1. **T2初期エリアの最終指定(人間領域・未確定)**: ci暫定エリア=鹿児島市天文館周辺約3km四方を設計提案としてsettings既定値に置いた(§2.6)。T2確定後は設定値変更+`make geo-import`再実行で差し替え。**暫定値の採用と、T2確定前のG0判定を暫定エリアで行うことの承認をsupervisorに求める**
2. **街区レベル位置参照情報の取り込み省略**(§2.10): 04 第3節の照合先文言は「位置参照情報の町字・街区代表点」を併記するが、M0は大字・町丁目レベル+OSM POIで開始する設計判断とした。理由: 街区レベルは市区町村単位の配信で件数が桁違い(1市数万行)であり、正転の照合に新たな名称を追加しない(街区行の名称は町字名+街区符号で、名称照合の単位は町字のまま)。丁目番地精度が必要と判明した場合は`source='isj_block'`の追加+取り込み拡張(テーブル構造不変)で対応できる。精度不足時のNominatim差し替え(04 第3節)と同じ拡張経路上に置く
3. **正転の一致率とあいまい照合の導入タイミング**(§2.4): 完全一致のみのM0開始。M1のParser精度ゲート(location 90%・07 D-17)とジオコーディングヒット率を実測したうえで、pg_trgm・表記ゆれ拡張をM1以降の改善として計画する
4. **地物データの鮮度管理**(§2.10): docsに再取り込み周期の規定なし。M0は初回取り込みのみとし、更新運用(年度更新のISJ・OSMの再取得周期)は本番運用設計(M4〜11)で確定する
5. **geo-verify出力の座標非表示**(§3.1): 08 第2.4節はログ・計測の座標排除をIntent由来データへの規定と読めるが、公開地物であっても出力最小化に統一した。デバッグで座標確認が必要になった場合はpsql等のDB直接確認を想定
6. **`make test-ci`の`--group geo`化**(§2.7): Makefile既存行の修正を伴う。ws-3がMakefileに触らない前提だが、マージ順序はスーパーバイザーの直列検証(STATUS.md運用ルール2)に従う
