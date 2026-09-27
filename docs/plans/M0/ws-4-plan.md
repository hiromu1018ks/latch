# M0 ws-4(地物データ取り込み・ジオコーディング)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依存ハード制約C5を解消する — 位置参照情報(大字・町丁目レベル)CSVとOSM(pbf/xml)を名称付きPOIとしてPostGISの `geofeatures` テーブル(マイグレーション0002)へ取り込むCLIを作り、正転(location.name→座標)・逆転(座標→地域名)のジオコーディングを**PostGIS上のSQLのみ**で完結させる(外部送信経路ゼロ — G0完了条件の4項目め)。保存APIへの組み込み(422 GEOCODING_FAILED契約)はM1、逆転の消費側(Layer 5のproposal.area_name)はM2。本単位は「テーブル+取り込みツール+照合関数+試験」で完結する(ws-2と同じ位置づけ)。

**Architecture:** 地物は source列(`isj_town` / `osm_poi`)で区別する単一テーブル `geofeatures`(design §2.1-A)。取り込みはホスト実行CLI `python -m latch.geo`(subcommand: import-isj / import-osm / verify。design §2.2-A)。ISJ読み取りはcp932/utf-8自動判定の純粋な行パーサ、OSM読み取りはpyosmium(FileProcessor)による名称付きPOI抽出(bboxフィルタ付き)。正転は取り込み時に事前計算した `normalized_name` / `full_normalized_name` への完全一致(決定的順位: osm_poi優先→id昇順)、逆転は約1kmグリッド丸め(3857へ落としてST_SnapToGrid)→代表点への最近傍1件→「市区町村名+地物名」(design §2.4・§2.5)。osmiumは専用dependency-group `geo` へ隔離し、api/workerイメージと `make test` は不変(design §2.7)。

**Tech Stack:** 既存スタック(Python 3.13 / SQLAlchemy async+asyncpg / alembic / pydantic / pytest+pytest-asyncio / ruff)+ **`osmium>=4.3`(pyosmium。新規・`geo` dependency-groupのみ)**。

**Spec:** `docs/plans/M0/ws-4-design.md`(agent1設計メモ。本計画はこの文書の§3ファイル構成・§4テスト方針・§5完了条件を各タスクへ展開したもの。design.md §6の6論点は推奨で固定済み — §6-1暫定エリアはsupervisor承認済み)

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m0-ws-4`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。
- **共有ci-db運用(STATUS.md「運用ルール」より — 最重要)**: ws-4は**マイグレーション0002を追加する単位**であり、ws-3(認証・DB消費単位)がmain作業ツリーで並走中。共有ci-db(compose常設・名前付きボリューム)の `alembic_version` をworktree間で取り合う状態になるため:
  - 開発は **`make test`(unitのみ・DB不要)** で進める(毎コミットの規律)
  - integration試験コードは作成する。**実DBでの動作確認(`make test-ci` や `pytest -m integration`)を実行してもよい(fixtureデータのみの場合)** が、実行すると共有DBのalembic_versionが0002へ進む(ws-3側のtest-ciと同時実行すると衝突する)。実行時は報告ファイルに実行した旨を記録する
  - 実行しない場合は報告ファイルに **「test-ci=スーパーバイザー検証待ち」** と記録してよい(design §2.8・STATUS.md運用ルール1)
  - integration試験の最低検証として、DB不要の `uv run --group geo pytest --collect-only -q`(収集時import・構文検証)は毎コミットで通すこと
  - **`make geo-download` / `make geo-import` / `make geo-verify`(実データのダウンロード・実エリア取り込み)は実行しない** — 実データ確認はG0証拠としてスーパーバイザーが実施する
- **設計判断の固定値**: design.md §6の6論点は推奨で固定済みとして本計画に落としてある(§6-1暫定エリア=鹿児島市天文館周辺約3km四方のsettings既定値はsupervisor承認済み)。**固定値(テーブル構成・正規化規則・SQL・CLI形状・Makefileターゲット等)を変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `docs:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする(12 第8節 運用ル則5)。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` / `uv add` 経由(システムPython 3.14と衝突させないため)。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| ジオコーディング選定=地物データのPostGIS取り込み+自前変換(位置参照情報+OSM)。第1条件は位置情報の外部送信経路なし。Nominatim差し替え可能 | 04 第3節技術スタック表 |
| 正転=location.nameを位置参照情報の町字代表点とOSMの名称付きPOIへ照合しgeo_centerを定める。逆転=geo_centerを約1kmグリッド(投影座標系へ落として丸め)の代表点に最も近い地物の地名を地域名とする。**いずれもPostGIS上の地物テーブルに対するSQLで実装** | 04 第3節「ジオコーディングの変換経路」 |
| 保存APIでの正転呼び出し・**該当地物なしは422 GEOCODING_FAILED**(M1。本単位は供給元としてNoneを返す) | 05 第5節 POST /v1/intents |
| 逆転結果は地域名のみproposalへ格納(座標は格納しない) | 05 第2節「proposalの構造」 / 08 第4節 D-11 |
| ログ・計測出力に座標を含めない(構造化ログは許可リスト方式) — geo-verify出力の座標非表示の根拠 | 08 第2.4節 |
| M0スコープ6「地物データ取り込み(位置参照情報+OSM→PostGIS、対象エリア分)」・G0「ジオコーディング(正転・逆転)がPostGIS上で完結する(外部送信経路ゼロ)」 | 12 第3節 M0 / 12 第2節 C5 |
| 初期エリア=駅周辺半径3km相当。エリア最終指定はT2(人間領域・未確定)→ エリアはsettingsで与える | 11 第2節 D-22 / 12 第4節T2 / design §2.6 |
| 位置参照情報の形式(SHIFT-JIS・10列・世界測地系十進度)・取得元(国土交通省 位置参照情報ダウンロードサービス) | design §1.3・§1.5 |
| OSM取得元(Geofabrik extract)・pyosmium(v4.3.1・wheel提供)・POI抽出条件 | design §1.5・§2.3 |
| 時刻参照はすべてClock経由(arch testがbackend/src全体で強制。取り込みのcreated_atも対象) | 04 第5節(FR-41) / 12 第2節 C2 |
| マイグレーション追加単位とDB消費単位のtest-ci同時実行禁止・test-ci直列実行 | STATUS.md「運用ルール」 |
| 出所明記(ISJ利用約款)・ODbLクレジット(OSM) | design §2.9 |
| 完了条件8項目・テスト方針8項目・ファイル構成 | design.md §3〜§5 |

## 2. グローバル制約(全タスクに暗黙に適用)

- 依存追加は **`[dependency-groups] geo = ["osmium>=4.3"]` のみ**。`backend/Dockerfile`(`uv sync --frozen --no-dev` はgeoグループを入れない)・ルート `compose.yaml` に差分を出さない(design §2.7)
- 製品コード(`backend/src/latch/`)で実時間を直接参照するAPI(`datetime.now` / `datetime.utcnow` / `date.today` / `time.time` / `time.monotonic` / `time.sleep` / `from time import`)の使用禁止。例外は `core/clock.py` のみ(既存arch test `tests/unit/test_arch_no_direct_time.py` がsrc全体を強制)。**取り込みの `created_at` は引数で受け取ったClockの `now()` 由来**(design §1.2確定値11)。CLIの実行時は `SystemClock()` を明示的に渡す
- `backend/src/latch/geo/` 配下で `requests` / `urllib` / `socket` / `httpx` / `aiohttp` / `http.client` のimport禁止(位置情報の外部送信経路ゼロのコード検査。design §4-8。osmium自体のimportは許可 — ローカルファイル読み取りのみに使用)
- 地物テーブルは**単一テーブル `geofeatures`**(source CHECKはdocs明記の2値のみ)。時刻列にDB時刻関数のDEFAULTを付けない。拡張のCREATE/DROPはしない(postgisは0001が作成済み)(design §2.8)
- 正転の照合は**正規化完全一致のみ**(あいまい照合・住所パーサーは作らない)。候補順位は「osm_poi優先→id昇順」で固定(design §2.4)
- 逆転のarea_nameは「市区町村名+地物名」(OSM POIは第2クエリでISJの市区町村名を補完。ISJ町丁目が存在しない場合のみname単独)。グリッド丸めは **3857へ変換→ST_SnapToGrid 1000.0m→4326へ戻す**。tie-breakは距離→source(osm_poi優先)→id(design §2.5)
- エリア設定はsettings 3項目(`geo_area_name` / `geo_isj_city_codes` / `geo_osm_bbox`)。CLI引数(`--city-codes` / `--bbox`)で上書き可能。**ISJ行のフィルタは「市区町村コードが含まれる かつ 代表点がbbox内」のAND、OSM要素は「代表点がbbox内」**(design §2.6)
- geo-verifyの出力に**座標を出さない**(名称・市区町村名・件数のみ — 08 第2.4節の趣旨。design §6-5)
- `backend/tests/conftest.py`・`backend/tests/integration/conftest.py`・既存テストファイル・`main.py`・`worker/`・`core/`・`alembic/versions/0001_initial_schema.py` は**変更しない**(design §3.3。geoのintegration試験は `migrated_db` / `db_engine` / `fake_clock` fixtureを参照のみする)
- naive datetimeを扱わない。created_atはClock(`now()`=tz-aware UTC)由来のみ

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **ISJ実データの形式破損** — UTF-8で配信された年度版・列数不足行・緯度経度欠損行が来ても、取り込みが例外で停止せず該当行をスキップして続行する(合理的期待)→ Task 3の `test_isj_parse.py`(欠損行・列不足行のスキップと件数)
2. **bbox/市コード設定文字列の形式破損** — envでtypoったbbox(`130.5,31.5` 等)が黙って全部通ったり斜め上の解釈をされたりしない(明確なValueErrorで即失敗)→ Task 3の `test_bbox_parse` と Task 8の `_effective_area` 試験
3. **グリッド丸めのSRID混同** — 3857へ落とさず4326のまま丸めると「約1km」が緯度1度刻み(≈111km)になりD-11の丸め粒度が崩れる → Task 6の `test_reverse_grid_snap_within_1km_cell`(代表点と入力の距離≤750mをSQLで実測)
4. **created_atへのDB時刻DEFAULT混入** — マイグレーションに `DEFAULT now()` を書くとClock規律(C2)がDB側で破れる → Task 4のschema試験(`column_default IS NULL` 検査)+ 取り込み試験でFakeClock時刻との一致検証
5. **attrsのjsonb取り込みの型破綻** — dictをそのままバインドしたりCASTを忘れると実データ取り込み時にだけ壊れる → Task 5の取り込み試験でattrsの中身を実読み取り検証

## 4. スコープ(作成・変更するファイル一覧)

作成(design.md §3.1どおり):

```text
backend/alembic/versions/0002_geofeatures.py
backend/src/latch/geo/__init__.py            (Task 2でdocstringのみ作成 → Task 9で公開IFの再exportへ更新)
backend/src/latch/geo/normalize.py
backend/src/latch/geo/ingest.py              (FeatureRow/BBox + import_features)
backend/src/latch/geo/isj.py
backend/src/latch/geo/osm.py
backend/src/latch/geo/service.py
backend/src/latch/geo/__main__.py
backend/tests/unit/geo/test_geo_settings.py
backend/tests/unit/geo/test_normalize.py
backend/tests/unit/geo/test_isj_parse.py
backend/tests/unit/geo/test_cli_args.py
backend/tests/unit/test_arch_geo_no_network.py
backend/tests/integration/test_geo.py
backend/tests/fixtures/geo/isj_sample.csv     (cp932で保存)
backend/tests/fixtures/geo/osm_sample.xml     (UTF-8)
docs/plans/M0/ws-4-report.md                 (報告ファイル。Task 9で作成)
```

変更:

- `backend/src/latch/settings.py` — geo設定3項目を追記(Task 1。追記のみ)
- `backend/pyproject.toml` + `backend/uv.lock` — `[dependency-groups]` へ `geo` グループ追加(Task 1。`uv add --group geo` で更新)
- `Makefile` — `test-ci` 行の修正(`--group geo`)+ geoターゲット3件追記+`.PHONY` 行更新(Task 8)
- `.gitignore` — `backend/data/` 追加(Task 1)
- `backend/README.md` — 地物データの節(取り込み手順+出所・ライセンス明記)を末尾へ追記(Task 8)

生成されるがコミットしないもの: `backend/data/geo/`(実データ置き場・gitignore)・`backend/.venv/`・`__pycache__/`。**`backend/tests/unit/geo/` に `__init__.py`・`conftest.py` は作らない**(design §3.1の一覧にない)。

## 5. 禁止(触ってはいけないもの・スコープ外と判断する基準)

- `backend/src/latch/main.py`・`backend/src/latch/worker/`・`backend/src/latch/core/`(clock.py・db.pyはimportのみ。M1/M2が統合する — design §3.3)
- `backend/alembic/versions/0001_initial_schema.py`・`backend/alembic/env.py`・`backend/alembic.ini`(0002の追加のみ)
- `backend/src/latch/llm/`・`backend/tests/` の既存テストファイル・`backend/tests/conftest.py`・`backend/tests/integration/conftest.py`(並走ws-3との共通ファイル衝突回避。`migrated_db` / `db_engine` / `fake_clock` fixtureは参照のみ)
- `backend/Dockerfile`・`compose.yaml`・`docker/`・`.mise.toml`(イメージ不変 — design §2.7)
- `docs/01〜12`・`docs/reviews/`・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/M0/` の他ファイル・`README.md`(ルート)
- `prototype/` 全体・`.claude/`
- スコープ外と判断する基準: 保存APIへのジオコーディング組み込み・422 GEOCODING_FAILED応答(M1)/ proposal.area_name生成・Layer 1の地理条件(M2)/ 街区レベルISJ・あいまい照合(pg_trgm)・住所パーサー・Nominatim(精度不足判明時の拡張)/ 取り込みの自動実行(CI組み込み・スケジューラ)/ API管理エンドポイント・データマイグレーション化(design §2.2)/ フルリロード以外の差分同期・鮮度管理(M4〜11)/ ポリゴン保持・逆転のキャッシュ・グリッド事前計算テーブル(design §2.10)。これらが必要になったと感じても作らない — design §1.4に列挙された後続単位のスコープ
- mainブランチへのコミット・push・マージ
- **実データのダウンロード(`make geo-download` または手動curl)・実エリア取り込み(`make geo-import`)・実データverify(`make geo-verify`)の実行**(G0証拠としてスーパーバイザーが実施する。§0)

## 6. 完了条件(design.md §5の8項目。検証コマンドつき。Task 9で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン(unit追加分を含む)**
   検証: `make lint && make test` — ともにexit 0
2. **design §4-3〜6・8のintegration試験がグリーン**(実装エージェントはunitで開発し、test-ciは「スーパーバイザー検証待ち」として報告してよい — STATUS.md運用ルール)
   検証: `cd backend && uv run --group geo pytest tests/integration/test_geo.py -v`(compose起動後)。実行しない場合は `uv run --group geo pytest --collect-only -q` で収集が問題ないことを報告
3. **マイグレーション0002が `down_revision="0001"` で追加され、`alembic upgrade head` でgeofeatures+Indexが作成されること・downgradeでテーブルのみDROPされること**
   検証: `cd backend && uv run alembic history` が `0001 -> 0002` を示す。`0002_geofeatures.py` の `downgrade()` が `DROP TABLE geofeatures` のみであることをコードで確認。upgradeの実DB確認は `alembic version` が0002を示す時点で完了(条件2と同じ実行。未実行なら「スーパーバイザー検証待ち」)
4. **`rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットが引き続き `core/clock.py` のみ**
   検証: 当該rgコマンド + `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
5. **`rg -n 'import requests|import urllib|import socket|import httpx|import aiohttp|import http\.client' backend/src/latch/geo/` がゼロヒット**
   検証: 当該rgコマンド(空出力)+ `cd backend && uv run pytest tests/unit/test_arch_geo_no_network.py -v` がexit 0
6. **依存追加が `geo` グループのみ(`backend/Dockerfile`・`compose.yaml` に差分なし)**
   検証: `git diff --stat main -- backend/Dockerfile compose.yaml` — 出力なし。`git diff main -- backend/pyproject.toml` が `[dependency-groups]` のgeo追加のみ
7. **触るファイルが§4の範囲内**(`main.py`・`worker/`・`core/`・`0001`・`llm/`・conftest.py群・既存テストに差分なし)
   検証: `git diff --name-only main | sort` が§4の一覧と完全一致。`git diff main -- backend/src/latch/settings.py` が追記のみ
8. **G0証拠(2)のための手順書が報告書に含まれること**: `make geo-download`→`make geo-import`→`make geo-verify` の実行方法と期待出力(名称・市区町村名。座標なし)
   検証: Task 9で報告ファイルの「スーパーバイザー向けG0証拠手順」節に§7テンプレートどおり記載

## 7. 報告形式

**結果ファイル**: `docs/plans/M0/ws-4-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M0 ws-4(地物データ取り込み・ジオコーディング) 実行報告

- ブランチ: m0-ws-4 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る> |
| 2 | integration試験(design §4-3〜6・8) | PASS / スーパーバイザー検証待ち | <pytestサマリー行 or collect-only結果> |
| 3 | 0002マイグレーション(down_revision=0001・upgrade/downgrade) | PASS/検証待ち | <alembic history出力> |
| 4 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 5 | geo配下のnetwork importゼロ | PASS/FAIL | <rg出力(空なら「空」) + arch test結果> |
| 6 | 依存追加がgeoグループのみ | PASS/FAIL | <git diff --stat出力> |
| 7 | 触るファイルがスコープどおり | PASS/FAIL | <git diff --name-only出力> |
| 8 | G0証拠手順書の記載 | PASS | <本報告書の下位節を指す> |

## test-ciの実行有無(STATUS.md運用ルール)
- 実行した: <実行日時とpytestサマリー> / 実行していない: 「test-ci=スーパーバイザー検証待ち」
- make geo-download / geo-import / geo-verify(実データ)は未実行(G0証拠はスーパーバイザー実施)

## スーパーバイザー向けG0証拠手順(実データ確認・完了条件8)
1. `make geo-download` — OSM九州extractをcurl取得+ISJ(鹿児島県分・大字/町丁目レベル)の手動取得案内表示
2. ISJのzipを解凍したCSVを `backend/data/geo/isj.csv` へ配置(ファイル名は年度で異なるためリネーム)
3. `make geo-import` — settings既定エリア(ci暫定=鹿児島市天文館周辺)で取り込み。期待出力: isj_town/osm_poi別の取り込み件数(名称・市コードは出すが座標は出さない)
4. `make geo-verify` — 期待出力: `forward '天文館': -> 鹿児島市天文館 (osm_poi/...)` 形式の行とsource別件数(座標なし)
5. `make test-ci` — unit+integration全体グリーン(alembic_version=0002)

## 固定値の変更有無(design.md §6・本計画§2)
- ci暫定エリア=鹿児島市天文館周辺約3km四方(bbox既定値): 変更なし / 変更あり(<前→後+理由>)
- 正転=正規化完全一致+決定的順位(osm_poi優先→id昇順): 変更なし / 変更あり(<前→後+理由>)
- 逆転=1kmグリッド丸め(3857)+最近傍+市区町村名補完: 変更なし / 変更あり(<前→後+理由>)
- geo-downloadの取得コマンド=curl(designのwgetから確定): 変更なし / 変更あり(<前→後+理由>)
- osmiumをgeo dependency-groupへ隔離+test-ciの--group geo化: 変更なし / 変更あり(<前→後+理由>)
- 出所・ライセンス明記の置き場所=backend/README.md: 変更なし / 変更あり(<前→後+理由>)

## コミット一覧
<git log --oneline main..HEAD の出力>

## 補足(詰まった点・判断した点があれば)
```

完了後の最終返信は報告ファイルのパスと完了条件8項目のPASS/FAIL一覧(条件2・3は「スーパーバイザー検証待ち」を含めてよい)。

---

### Task 1: 土台 — osmiumのgeoグループ追加・gitignore・geo設定3項目

**Files:**
- Modify: `backend/pyproject.toml` + `backend/uv.lock`(`uv add --group geo` が更新)、`.gitignore`(末尾へ追記)、`backend/src/latch/settings.py`(geo設定3項目を追記)
- Create: `backend/tests/unit/geo/test_geo_settings.py`

**Interfaces:**
- Consumes: なし(最初のタスク)
- Produces: `Settings` の新フィールド — `geo_area_name: str = "ci-provisional"`・`geo_isj_city_codes: str = "46201"`・`geo_osm_bbox: str = "130.5420,31.5825,130.5740,31.6095"`(env prefix `LATCH_`)。dependency-group `geo`(osmium)。以降全タスクとCLIが利用

- [ ] **Step 1: osmiumをgeoグループへ追加**

```bash
cd backend && uv add --group geo "osmium>=4.3"
```

期待: `pyproject.toml` の `[dependency-groups]` に `geo = ["osmium>=4.3"]` が追加され、`uv.lock` が更新される。main依存(`[project] dependencies`)は不変。`uv run pytest`(グループ指定なし)はdevグループのみでsyncし直すため `make test` への影響なし。

- [ ] **Step 2: .gitignore へ追記**

`.gitignore` の「# Python (backend)」節の後へ追記:

```text

# Geospatial source data (ws-4)
backend/data/
```

- [ ] **Step 3: 失敗するテストを書く**

`backend/tests/unit/geo/test_geo_settings.py`:

```python
"""geo設定3項目(design §2.6)。エリアは設定で与える(T2確定後は値変更+再取り込み)。"""

from latch.settings import Settings

GEO_ENV_VARS = ("LATCH_GEO_AREA_NAME", "LATCH_GEO_ISJ_CITY_CODES", "LATCH_GEO_OSM_BBOX")


def _clean_settings(monkeypatch, **overrides) -> Settings:
    for var in GEO_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return Settings(**overrides)


def test_geo_settings_defaults(monkeypatch):
    s = _clean_settings(monkeypatch)
    assert s.geo_area_name == "ci-provisional"
    assert s.geo_isj_city_codes == "46201"
    # ci暫定エリア=鹿児島市天文館周辺約3km四方(design §2.6。中心約(130.558,31.596)±約1.5km)
    assert s.geo_osm_bbox == "130.5420,31.5825,130.5740,31.6095"


def test_geo_settings_env_override(monkeypatch):
    monkeypatch.setenv("LATCH_GEO_ISJ_CITY_CODES", "47201")
    monkeypatch.setenv("LATCH_GEO_OSM_BBOX", "127.0,26.0,128.0,27.0")
    s = Settings()
    assert s.geo_isj_city_codes == "47201"
    assert s.geo_osm_bbox == "127.0,26.0,128.0,27.0"


def test_geo_settings_are_exactly_three_fields(monkeypatch):
    # エリア差し替え(T2後)は設定値変更+再取り込みで済む構造の検査。
    # geo系設定がこの3項目のみであることを機械検査する(過剰な設定項目を防ぐ)。
    _clean_settings(monkeypatch)
    geo_fields = {f for f in Settings.model_fields if f.startswith("geo_")}
    assert geo_fields == {"geo_area_name", "geo_isj_city_codes", "geo_osm_bbox"}
```

- [ ] **Step 4: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/geo/test_geo_settings.py -v`
Expected: FAIL(`AttributeError: 'Settings' object has no attribute 'geo_area_name'`)

- [ ] **Step 5: settings.py へgeo設定3項目を追記(追記のみ)**

`backend/src/latch/settings.py` — 末尾(LLM設定の後)へ追記:

```python

    # --- 地物データ(ws-4。design §2.6)---
    geo_area_name: str = "ci-provisional"  # エリア表示名(記録用。T2確定後に差し替え)
    geo_isj_city_codes: str = "46201"  # カンマ区切り。ISJ取り込みの市区町村コードフィルタ
    geo_osm_bbox: str = "130.5420,31.5825,130.5740,31.6095"  # min_lon,min_lat,max_lon,max_lat
```

(app_env・log_level・database_url・llm_* の行は既存のまま変更しない)

- [ ] **Step 6: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/geo/test_geo_settings.py -v`
Expected: PASS(3件)

- [ ] **Step 7: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/pyproject.toml backend/uv.lock .gitignore backend/src/latch/settings.py backend/tests/unit/geo/test_geo_settings.py
git commit -m "feat: geo依存グループ(osmium)とエリア設定3項目(T2後の差し替え用)"
```

---

### Task 2: 名称正規化 normalize_name(純函数)

**Files:**
- Create: `backend/src/latch/geo/__init__.py`(この時点ではdocstringのみ)、`backend/src/latch/geo/normalize.py`
- Test: `backend/tests/unit/geo/test_normalize.py`

**Interfaces:**
- Consumes: なし
- Produces: `latch.geo.normalize.normalize_name(name: str) -> str`(design §2.4の規則 — NFKC→空白類・記号類除去→「大字」接頭辞除去→カタカナ→ひらがな→丁目の算用数字→漢数字→ASCII小文字化)。取り込み(isj/osm→ingest)と照合(service.geocode_forward)の双方に適用する**同じ関数**。Task 5・6が利用

- [ ] **Step 1: パッケージの土台を作る**

```bash
mkdir -p backend/src/latch/geo backend/tests/unit/geo
printf '"""地物データ取り込み・ジオコーディング(M0 ws-4)。PostGIS上で完結(外部送信経路ゼロ)。"""\n' > backend/src/latch/geo/__init__.py
```

(再exportはTask 9でこのファイルへ追記する。`latch.geo` パッケージ成立のためにこの時点で作る)

- [ ] **Step 2: 失敗するテストを書く**

`backend/tests/unit/geo/test_normalize.py`:

```python
"""normalize_nameの規則(design §2.4)。入力(Parser出力)と地物名の双方に適用する。"""

import pytest

from latch.geo.normalize import normalize_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 無変換(かな・漢字・漢数字はそのまま通る)
        ("天文館一丁目", "天文館一丁目"),
        ("伊敷町", "伊敷町"),
        # 丁目の算用数字→漢数字(ISJの大字町丁目名は漢数字)
        ("天文館1丁目", "天文館一丁目"),
        ("2丁目", "二丁目"),
        ("中央10丁目", "中央十丁目"),
        ("中央21丁目", "中央二十一丁目"),
        # NFKC(全角英数→半角)
        ("Ｂａｒ", "bar"),
        ("ｱｲ", "ai"),  # NFKCで半角カナ→全角カナ→(次段で)ひらがな
        # 空白類(全角含む)・区切り記点の除去
        ("　天文館　一丁目", "天文館一丁目"),
        ("バ・ヨイマチ", "ばよいまち"),  # カタカナも次段でひらがな化される
        ("バー・ヨイマチ", "ばーよいまち"),  # 長音ー(Lm)は保持
        ("伊敷-町", "伊敷町"),
        # 「大字」接頭辞の除去(ISJの大字町丁目名)
        ("大字草牟田", "草牟田"),
        # カタカナ→ひらがな統一
        ("カフェミナミ", "かふぇみなみ"),
        ("ヴィラ", "ゔぃら"),
        # ASCII小文字化
        ("CafeMinami", "cafeminami"),
        # 組み合わせ
        ("鹿児島市 天文館1丁目", "鹿児島市天文館一丁目"),
        # 空文字・記号のみ
        ("", ""),
        ("　", ""),
        # 々(Lm)は保持
        ("代々木町", "代々木町"),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def test_normalize_is_idempotent():
    # 二重適用で変化しない(取り込み側・照合側の適用回数に依存しない)
    once = normalize_name("鹿児島市 天文館1丁目")
    assert normalize_name(once) == once
```

- [ ] **Step 3: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/geo/test_normalize.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.geo.normalize'`)

- [ ] **Step 4: normalize.py を実装**

`backend/src/latch/geo/normalize.py`:

```python
"""地物名称の正規化(design §2.4)。照合(正転)は完全一致のみ — 入力と地物名の
双方にこの同じ関数を適用してから等値比較する。

規則(この順で適用):
1. NFKC正規化(全角英数→半角・半角カナ→全角)
2. 空白類と区切り記号類の除去(Unicodeカテゴリ Z*/P*/S* を除去。
   長音「ー」(Lm)・々(Lm)・漢数字・かな・数字(Nd)は保持)
3. 「大字」接頭辞の除去(ISJの大字町丁目名)
4. カタカナ→ひらがら統一(ァ〜ヴ。ヶ(U+30F6)は地名に常用のため対象外)
5. 「<算用数字>丁目」の算用数字→漢数字(ISJの大字町丁目名は漢数字)
6. ASCII小文字化
"""

from __future__ import annotations

import re
import unicodedata

_DIGITS = "〇一二三四五六七八九"
_CHO_PATTERN = re.compile(r"(\d+)丁目")


def _digits_to_kanji(number: str) -> str:
    """1〜99の算用数字を漢数字へ(丁目の数字用)。範囲外はそのまま。"""
    n = int(number)
    if not 1 <= n <= 99:
        return number
    tens, ones = divmod(n, 10)
    if tens == 0:
        return _DIGITS[ones]
    tens_part = "十" if tens == 1 else _DIGITS[tens] + "十"
    return tens_part + _DIGITS[ones]


def _strip_symbols(text: str) -> str:
    return "".join(
        ch
        for ch in text
        if not unicodedata.category(ch).startswith(("Z", "P", "S"))
    )


def normalize_name(name: str) -> str:
    """地物名称の正規化(design §2.4)。取り込み時と照合時の双方に適用する。"""
    nfkc = unicodedata.normalize("NFKC", name)
    stripped = _strip_symbols(nfkc)
    if stripped.startswith("大字"):
        stripped = stripped[2:]
    hiragana = "".join(
        chr(ord(ch) - 0x60) if "ァ" <= ch <= "ヴ" else ch for ch in stripped
    )
    kanji = _CHO_PATTERN.sub(lambda m: _digits_to_kanji(m.group(1)) + "丁目", hiragana)
    return kanji.lower()
```

(カタカナ→ひらがなはコード差 0x60: ァU+30A1→ぁU+3041。ヴU+30F4→ゔU+3094)

- [ ] **Step 5: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/geo/test_normalize.py -v`
Expected: PASS(21件)

注意: 「ｱｲ」の期待値 — NFKCで半角カナ「ｱｲ」は全角カタカナ「アイ」へ変換され、その後ひらがな「あい」になる。テストが落ちた場合は期待値と実測を確認し、**規則(design §2.4)に反しない場合のみ**期待値を実測へ合わせ、判断を報告ファイルへ記録する。

- [ ] **Step 6: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/geo backend/tests/unit/geo/test_normalize.py
git commit -m "feat: 地物名称正規化(NFKC・記号除去・大字接頭辞・かな統一・丁目漢数字)"
```

---

### Task 3: FeatureRow/BBox + ISJ CSV読み取り

**Files:**
- Create: `backend/src/latch/geo/ingest.py`(この時点ではFeatureRow/BBoxのデータモデルのみ。import_featuresはTask 5)、`backend/src/latch/geo/isj.py`、`backend/tests/fixtures/geo/isj_sample.csv`(cp932)
- Test: `backend/tests/unit/geo/test_isj_parse.py`

**Interfaces:**
- Consumes: Task 1のSettings(テストで既定値を参照)
- Produces: `latch.geo.ingest.FeatureRow`(pydantic: `kind: str`・`name: str`・`pref_name: str | None = None`・`city_name: str | None = None`・`source_code: str`・`lon: float`・`lat: float`・`attrs: dict[str, str] = {}`)・`latch.geo.ingest.BBox`(frozen dataclass: `min_lon/min_lat/max_lon/max_lat`・`parse(cls, s: str) -> BBox`(4数値・範囲検査)・`contains(lon, lat) -> bool`)・`latch.geo.isj.iter_isj_towns(csv_path: Path | str, *, city_codes: set[str] | None = None, bbox: BBox | None = None) -> Iterator[FeatureRow]`(kind="town"・source_code=大字町丁目コード・attrs={source_material_code, town_class_code}。無効行はスキップ)。Task 5〜8が利用

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/geo/test_isj_parse.py`:

```python
"""ISJ(大字・町丁目レベル)CSV読み取り(design §1.5・§4-2)。

fixtureはcp932で保存した合成データ(鹿児島市の実在町名・合成座標):
  伊敷町(市内・bbox内)/ 天文館一丁目(市内・bbox内)/ 大字草牟田(市内・bbox内)/
  中央町(市コード違い・代表点はbbox内 → ANDフィルタで除外)/
  松原町(市内・bbox外)/ 名山町(緯度欠損)/ 欠列町(列数不足)
"""

from pathlib import Path

import pytest

from latch.geo.ingest import BBox
from latch.geo.isj import iter_isj_towns

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "geo" / "isj_sample.csv"
DEFAULT_CODES = {"46201"}  # 鹿児島市(ci暫定エリアの既定値 — Task 1)
DEFAULT_BBOX = BBox.parse("130.5420,31.5825,130.5740,31.6095")


def _rows(**kwargs):
    defaults = {"city_codes": DEFAULT_CODES, "bbox": DEFAULT_BBOX}
    return list(iter_isj_towns(FIXTURE, **{**defaults, **kwargs}))


def test_reads_cp932_csv():
    rows = _rows()
    names = [r.name for r in rows]
    assert names == ["伊敷町", "天文館一丁目", "大字草牟田"]


def test_column_mapping():
    rows = _rows()
    iseki = rows[0]  # 伊敷町
    assert iseki.kind == "town"
    assert iseki.pref_name == "鹿児島県"
    assert iseki.city_name == "鹿児島市"
    assert iseki.source_code == "46201001001"  # 大字町丁目コード(11桁でよい・そのまま文字列)
    assert iseki.lat == pytest.approx(31.605000)
    assert iseki.lon == pytest.approx(130.552000)
    assert iseki.attrs["source_material_code"] == "2"
    assert iseki.attrs["town_class_code"] == "1"


def test_reads_utf8_csv(tmp_path):
    # Review Focus #1: UTF-8で配信された年度版も例外にならず読める(自動判定)
    raw = FIXTURE.read_bytes()
    try:
        raw.decode("utf-8")
        pytest.skip("fixtureがたまたまutf-8互換 — cp932前提のためskip")
    except UnicodeDecodeError:
        pass
    utf8 = tmp_path / "isj_utf8.csv"
    utf8.write_bytes(raw.decode("cp932").encode("utf-8"))
    rows = list(
        iter_isj_towns(utf8, city_codes=DEFAULT_CODES, bbox=DEFAULT_BBOX)
    )
    assert [r.name for r in rows] == ["伊敷町", "天文館一丁目", "大字草牟田"]


def test_city_code_and_bbox_are_and_filter():
    # 中央町=市コード違い(代表点はbbox内)→除外。松原町=市内・bbox外→除外
    rows = _rows()
    assert "中央町" not in [r.name for r in rows]
    assert "松原町" not in [r.name for r in rows]


def test_invalid_rows_are_skipped():
    # 名山町=緯度欠損・欠列町=列数不足 → 例外ではなくスキップ(design §4-2)
    rows = _rows()
    assert "名山町" not in [r.name for r in rows]
    assert "欠列町" not in [r.name for r in rows]


def test_no_filters_returns_all_valid_rows():
    rows = _rows(city_codes=None, bbox=None)
    names = [r.name for r in rows]
    assert names == ["伊敷町", "天文館一丁目", "大字草牟田", "中央町", "松原町"]
    # 緯度欠損・列数不足の2行はフィルタなしでもスキップされる


class TestBBox:
    def test_parse_valid(self):
        b = BBox.parse("130.5420,31.5825,130.5740,31.6095")
        assert (b.min_lon, b.min_lat, b.max_lon, b.max_lat) == (
            130.5420, 31.5825, 130.5740, 31.6095,
        )
        # Review Focus #2: 形式破損は明確なValueError(黙って通さない)
        for bad in ("130.5,31.5", "a,b,c,d", "1,2,3", "3,2,1,0", "1,2,1,2", ""):
            with pytest.raises(ValueError, match="bbox"):
                BBox.parse(bad)

    def test_contains(self):
        b = BBox.parse("130.5420,31.5825,130.5740,31.6095")
        assert b.contains(130.5585, 31.5965)
        assert not b.contains(139.7671, 35.6812)
```

- [ ] **Step 2: fixture(isj_sample.csv・cp932)を作る**

```bash
mkdir -p backend/tests/fixtures/geo
cat > /tmp/isj_sample_utf8.csv <<'EOF'
46,鹿児島県,46201,鹿児島市,46201001001,伊敷町,31.605000,130.552000,2,1
46,鹿児島県,46201,鹿児島市,46201002001,天文館一丁目,31.596500,130.558500,1,3
46,鹿児島県,46201,鹿児島市,46201003001,大字草牟田,31.601000,130.564000,2,1
46,鹿児島県,46202,枕崎市,46202001001,中央町,31.590000,130.560000,1,3
46,鹿児島県,46201,鹿児島市,46201004001,松原町,31.650000,130.600000,2,1
46,鹿児島県,46201,鹿児島市,46201005001,名山町,,130.550000,1,3
46,鹿児島県,46201,鹿児島市,46201006001,欠列町,31.595000,130.556000,1
EOF
iconv -f UTF-8 -t CP932 /tmp/isj_sample_utf8.csv > backend/tests/fixtures/geo/isj_sample.csv
rm /tmp/isj_sample_utf8.csv
```

(実ISJの大字町丁目コードは12桁だが、フィルタ・格納は文字列として扱うため桁数に依存しない。fixtureは11桁の合成コードで列形式のみを忠実に再現する)

- [ ] **Step 3: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/geo/test_isj_parse.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.geo.ingest'`)

- [ ] **Step 4: ingest.py にデータモデルを実装**

`backend/src/latch/geo/ingest.py`:

```python
"""地物のDB取り込み(design §3.1)。

FeatureRow=取り込み行のモデル(ISJ/OSM共通)、BBox=エリアフィルタ(design §2.6)。
import_featuresはTask 5で追加する(フルリロード: DELETE→一括INSERT)。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock


class FeatureRow(BaseModel):
    """geofeaturesへ入る地物1行(代表点+表示名+系統固有の補助列)。"""

    kind: str  # isj_town='town' / osm=タグ由来('restaurant'等・place系='district')
    name: str  # 表示名(ISJ=大字町丁目名、OSM=name)
    pref_name: str | None = None  # ISJ由来(OSMはNone)
    city_name: str | None = None  # 同上
    source_code: str  # ISJ=大字町丁目コード / OSM="node/12345" 等
    lon: float
    lat: float
    attrs: dict[str, str] = {}  # ISJ=原典/区分コード / OSM=主要タグ


@dataclass(frozen=True)
class BBox:
    """エリアの外接矩形(design §2.6)。OSM bboxとISJ代表点フィルタの共通基準。"""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @classmethod
    def parse(cls, raw: str) -> BBox:
        """'min_lon,min_lat,max_lon,max_lat' 文字列を構築。形式破損はValueError。"""
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 4:
            raise ValueError(f"bbox must be 'min_lon,min_lat,max_lon,max_lat': {raw!r}")
        try:
            min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
        except ValueError as exc:
            raise ValueError(f"bbox values must be numbers: {raw!r}") from exc
        if not (-180 <= min_lon < max_lon <= 180) or not (
            -90 <= min_lat < max_lat <= 90
        ):
            raise ValueError(f"invalid bbox range: {raw!r}")
        return cls(min_lon, min_lat, max_lon, max_lat)

    def contains(self, lon: float, lat: float) -> bool:
        return self.min_lon <= lon <= self.max_lon and self.min_lat <= lat <= self.max_lat


async def import_features(
    engine: AsyncEngine,
    clock: Clock,
    rows: Iterable[FeatureRow],
    source: str,
) -> int:
    """sourceの行をフルリロードして件数を返す(Task 5で実装)。"""
    raise NotImplementedError("Task 5で実装")
```

- [ ] **Step 5: isj.py を実装**

`backend/src/latch/geo/isj.py`:

```python
"""位置参照情報(大字・町丁目レベル)CSV読み取り(design §1.5)。

文字コードはSHIFT-JIS(cp932)が公式形式。UTF-8配信も例外にせず自動判定
(Review Focus #1)。10列固定: 都道府県コード/都道府県名/市区町村コード/
市区町村名/大字町丁目コード/大字町丁目名/緯度/経度/原典資料コード/区分コード。
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from latch.geo.ingest import BBox, FeatureRow


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp932")


def _to_float(raw: str) -> float | None:
    value = raw.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def iter_isj_towns(
    csv_path: Path | str,
    *,
    city_codes: set[str] | None = None,
    bbox: BBox | None = None,
) -> Iterator[FeatureRow]:
    """ISJ CSVを読み、フィルタを通る行をFeatureRow(kind='town')として列挙する。

    - フィルタ: 市区町村コードがcity_codesに含まれる かつ 代表点がbbox内(AND)
    - 列数不足行・緯度経度欠損/不正行は例外にせずスキップ(design §4-2)
    """
    text = _decode(Path(csv_path).read_bytes())
    for row in csv.reader(text.splitlines()):
        if len(row) != 10:
            continue  # 列数不足(ヘッダー混入・破損行)
        lat = _to_float(row[6])
        lon = _to_float(row[7])
        name = row[5].strip()
        if lat is None or lon is None or not name:
            continue  # 代表点欠損(代表点を持たない町丁目)
        if city_codes is not None and row[2] not in city_codes:
            continue
        if bbox is not None and not bbox.contains(lon, lat):
            continue
        yield FeatureRow(
            kind="town",
            name=name,
            pref_name=row[1] or None,
            city_name=row[3] or None,
            source_code=row[4],
            lon=lon,
            lat=lat,
            attrs={
                "source_material_code": row[8],
                "town_class_code": row[9],
            },
        )
```

- [ ] **Step 6: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/geo/test_isj_parse.py -v`
Expected: PASS(8件)

- [ ] **Step 7: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/geo/ingest.py backend/src/latch/geo/isj.py backend/tests/unit/geo/test_isj_parse.py backend/tests/fixtures/geo/isj_sample.csv
git commit -m "feat: 位置参照情報CSV読み取り(cp932自動判定・ANDフィルタ・無効行スキップ)"
```

---

### Task 4: マイグレーション0002 — geofeaturesテーブル+Index

**Files:**
- Create: `backend/alembic/versions/0002_geofeatures.py`、`backend/tests/integration/test_geo.py`

**Interfaces:**
- Consumes: 0001(postgis拡張を作成済み — 0002では拡張を触らない)
- Produces: `geofeatures` テーブル(id IDENTITY / source CHECK / geom geography(Point,4326) / Index GIST+btree)。`down_revision="0001"`。Task 5以降のintegration試験と取り込み・照合が依存

- [ ] **Step 1: 失敗するテストを書く(integration)**

`backend/tests/integration/test_geo.py`:

```python
"""ws-4地物データのintegration試験(design §4-3〜7)。

- スキーマ(0002): テーブル・CHECK・Index・created_atのDEFAULTなし
- 取り込み冪等: fixture(isj_sample.csv+osm_sample.xml)のフルリロード
- 正転・逆転・決定性: G0「PostGIS上で完結する(外部送信経路ゼロ)」の証明(fixtureデータ)

migrated_db/db_engine fixtureは tests/integration/conftest.py の既存ものを
参照のみする(変更しない)。fake_clock は tests/conftest.py の既存もの。
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from latch.geo.ingest import BBox, FeatureRow, import_features
from latch.geo.isj import iter_isj_towns

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "geo"
DEFAULT_CODES = {"46201"}
DEFAULT_BBOX = BBox.parse("130.5420,31.5825,130.5740,31.6095")
NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


# --- design §4-3: スキーマ(0002)---

async def test_head_is_0002(db_engine):
    """0002がhead(migrated_dbがheadまで進めた結果)。"""
    async with db_engine.connect() as conn:
        version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    assert version == "0002"


async def test_geofeatures_table_exists(db_engine):
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
        assert "geofeatures" in {row[0] for row in result}


async def test_geofeatures_source_check_constraint(db_engine):
    from sqlalchemy.exc import IntegrityError

    async with db_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text("""
                    INSERT INTO geofeatures
                      (source, kind, name, normalized_name, source_code, attrs, geom, created_at)
                    VALUES
                      ('nominatim', 'x', 'x', 'x', 'x', '{}',
                       ST_SetSRID(ST_MakePoint(130.55, 31.59), 4326)::geography, :t)
                """),
                {"t": NOW},
            )


async def test_geofeatures_indexes_exist(db_engine):
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'geofeatures'"
            )
        )
        names = {row[0] for row in result}
    assert {"idx_geofeatures_geom", "idx_geofeatures_name"} <= names


async def test_geofeatures_created_at_has_no_db_default(db_engine):
    """Review Focus #4: 時刻はClock由来の明示値 — DB時刻関数DEFAULTを持たない。"""
    async with db_engine.connect() as conn:
        result = await conn.execute(
            text("""
                SELECT column_default FROM information_schema.columns
                WHERE table_name = 'geofeatures' AND column_name = 'created_at'
            """)
        )
        assert result.scalar() is None
```

- [ ] **Step 2: テストの収集を確認(DB不要の最低検証)**

Run: `cd backend && uv run --group geo pytest --collect-only -q`
Expected: エラーなし(test_geo.py の6試験が収集される)

- [ ] **Step 3: 0002マイグレーションを実装**

`backend/alembic/versions/0002_geofeatures.py`:

```python
"""geofeatures: 地物テーブル(単一テーブル+source列。design §2.1-A・§3.1)。

- CREATE/DROP EXTENSION は行わない(postgisは0001が作成済み — design §2.8)
- 時刻列にDB時刻関数のDEFAULTを付けない(created_atはClock由来の明示値)
- CHECKはdesignが明記したsource 2値のみ(0001と同じ線)
- データ取り込みはマイグレーションに含めない(CLI import-isj/import-osm)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE geofeatures (
            id bigint GENERATED ALWAYS AS IDENTITY,
            source text NOT NULL
                CHECK (source IN ('isj_town', 'osm_poi')),
            kind text NOT NULL,
            name text NOT NULL,
            normalized_name text NOT NULL,
            full_normalized_name text,
            pref_name text,
            city_name text,
            source_code text NOT NULL,
            attrs jsonb NOT NULL DEFAULT '{}',
            geom geography(Point, 4326) NOT NULL,
            created_at timestamptz NOT NULL,
            CONSTRAINT geofeatures_pkey PRIMARY KEY (id)
        )
    """)
    # 地理検索(逆転の最近傍・将来のLayer 1)と正転の名称等値照合
    op.execute("CREATE INDEX idx_geofeatures_geom ON geofeatures USING GIST (geom)")
    op.execute("CREATE INDEX idx_geofeatures_name ON geofeatures (normalized_name)")


def downgrade() -> None:
    op.execute("DROP TABLE geofeatures")
```

- [ ] **Step 4: (実行する場合)test-ci相当で確認**

§0の運用に従い、共有ci-dbを進めてよい状況なら:

```bash
make test-ci
```

Expected: exit 0(新規6試験を含む全体グリーン)。実行しない場合はスキップし、Step 2の収集確認のみでよい(報告書に「スーパーバイザー検証待ち」)。

- [ ] **Step 5: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/alembic/versions/0002_geofeatures.py backend/tests/integration/test_geo.py
git commit -m "feat: マイグレーション0002 geofeaturesテーブル+Index(GIST/btree)"
```

---

### Task 5: 取り込み import_features(フルリロードの冪等性)

**Files:**
- Modify: `backend/src/latch/geo/ingest.py`(`import_features` を実装)
- Test: `backend/tests/integration/test_geo.py`(追記)

**Interfaces:**
- Consumes: Task 3の `FeatureRow`・Task 4のgeofeaturesテーブル・Task 2の `normalize_name`・雛形のClock/FakeClock
- Produces: `latch.geo.ingest.import_features(engine, clock, rows, source) -> int`(sourceは `'isj_town'` / `'osm_poi'` 以外でValueError。DELETE WHERE source→一括INSERT→件数。`normalized_name` は取り込み時計算、`full_normalized_name` はISJ(city_nameあり)のみ `normalize_name(city_name + name)`)。Task 6〜8と取り込み運用が利用

- [ ] **Step 1: 失敗するテストを書く(test_geo.py へ追記)**

ファイル末尾へ追記:

```python
# --- design §4-4: 取り込みと冪等 ---

def _isj_fixture_rows():
    return list(
        iter_isj_towns(
            FIXTURES / "isj_sample.csv",
            city_codes=DEFAULT_CODES,
            bbox=DEFAULT_BBOX,
        )
    )


async def test_import_rejects_unknown_source(db_engine, fake_clock):
    with pytest.raises(ValueError, match="source"):
        await import_features(db_engine, fake_clock, _isj_fixture_rows(), "nominatim")


async def test_import_isj_fixture(db_engine, fake_clock):
    engine = db_engine
    count = await import_features(engine, fake_clock, _isj_fixture_rows(), "isj_town")
    assert count == 3  # 伊敷町・天文館一丁目・大字草牟田
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT name, normalized_name, full_normalized_name, city_name, "
                    "attrs, created_at FROM geofeatures WHERE source = 'isj_town' "
                    "ORDER BY name"
                )
            )
        ).all()
    assert [r[0] for r in rows] == ["伊敷町", "大字草牟田", "天文館一丁目"]
    # normalized_name(大字接頭辞は除去される)/ full_normalized_name(ISJのみ)
    by_name = {r[0]: r for r in rows}
    assert by_name["大字草牟田"][1] == "草牟田"
    assert by_name["伊敷町"][2] == "鹿児島市伊敷町"
    # Review Focus #5: attrsはjsonbとして正しく入る(dict実読み取り)
    assert by_name["伊敷町"][4]["source_material_code"] == "2"
    # Review Focus #4: created_atはClock由来の明示値(FakeClock時刻と一致)
    assert by_name["伊敷町"][5] == NOW


async def test_import_is_idempotent(db_engine, fake_clock):
    """フルリロードの冪等: 2回実行しても行数・内容が不変(design §4-4)。"""
    engine = db_engine
    first = await import_features(engine, fake_clock, _isj_fixture_rows(), "isj_town")
    second = await import_features(engine, fake_clock, _isj_fixture_rows(), "isj_town")
    assert first == second == 3

    async def _snapshot():
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    text(
                        "SELECT source, name, normalized_name, source_code, "
                        "ST_AsText(geom) FROM geofeatures ORDER BY source, name"
                    )
                )
            ).all()

    snap1 = await _snapshot()
    snap2 = await _snapshot()
    assert snap1 == snap2


async def test_import_reloads_only_same_source(db_engine, fake_clock):
    """source単位のリロード: OSM取り込みはISJ行を消さない(design §3.1)。"""
    engine = db_engine
    await import_features(engine, fake_clock, _isj_fixture_rows(), "isj_town")
    await import_features(
        engine,
        fake_clock,
        [
            FeatureRow(
                kind="bar", name="バー宵待", source_code="node/2",
                lon=130.5575, lat=31.5955, attrs={"amenity": "bar"},
            )
        ],
        "osm_poi",
    )
    async with engine.connect() as conn:
        counts = dict(
            (await conn.execute(
                text("SELECT source, count(*) FROM geofeatures GROUP BY source")
            )).all()
        )
    assert counts == {"isj_town": 3, "osm_poi": 1}
```

- [ ] **Step 2: テストの収集を確認**

Run: `cd backend && uv run --group geo pytest --collect-only -q`
Expected: エラーなし(新規4試験を含む)

- [ ] **Step 3: import_features を実装**

`backend/src/latch/geo/ingest.py` — `import_features` の仮実装(raise NotImplementedError)を次の内容へ置き換える。import節に追記:

```python
import json
```

`import_features` の実装:

```python
SOURCES = ("isj_town", "osm_poi")

_INSERT = """
    INSERT INTO geofeatures
      (source, kind, name, normalized_name, full_normalized_name, pref_name,
       city_name, source_code, attrs, geom, created_at)
    VALUES
      (:source, :kind, :name, :normalized_name, :full_normalized_name, :pref_name,
       :city_name, :source_code, CAST(:attrs AS jsonb),
       ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :created_at)
"""


async def import_features(
    engine: AsyncEngine,
    clock: Clock,
    rows: Iterable[FeatureRow],
    source: str,
) -> int:
    """sourceの行をフルリロード(DELETE WHERE source=... → 一括INSERT)し件数を返す。

    冪等: 同一入力を再実行しても同一状態になる(設計§4-4で試験)。
    normalized_nameはここで計算(design §2.4 — 入力と同じ関数)。
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source: {source!r} (must be one of {SOURCES})")
    created_at = clock.now()
    params = [
        {
            "source": source,
            "kind": row.kind,
            "name": row.name,
            "normalized_name": normalize_name(row.name),
            "full_normalized_name": (
                normalize_name(f"{row.city_name}{row.name}") if row.city_name else None
            ),
            "pref_name": row.pref_name,
            "city_name": row.city_name,
            "source_code": row.source_code,
            "attrs": json.dumps(row.attrs, ensure_ascii=False),
            "lon": row.lon,
            "lat": row.lat,
            "created_at": created_at,
        }
        for row in rows
    ]
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM geofeatures WHERE source = :source"), {"source": source}
        )
        if params:
            await conn.execute(text(_INSERT), params)
    return len(params)
```

import節へ追記(既存のimportと併せてruffのisort順へ):

```python
from sqlalchemy import text

from latch.geo.normalize import normalize_name
```

(`from __future__ import annotations`・dataclass・pydantic等の既存importはそのまま)

- [ ] **Step 4: (実行する場合)test-ci相当で確認**

```bash
make test-ci
```

Expected: exit 0。実行しない場合は収集確認のみ(§0)。

- [ ] **Step 5: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/geo/ingest.py backend/tests/integration/test_geo.py
git commit -m "feat: geofeatures取り込み(source単位フルリロード・冪等・Clock由来created_at)"
```

---

### Task 6: GeoService — 正転・逆転(PostGIS上で完結)

**Files:**
- Create: `backend/src/latch/geo/service.py`
- Test: `backend/tests/integration/test_geo.py`(追記)

**Interfaces:**
- Consumes: Task 4のgeofeatures・Task 2のnormalize_name・Task 5のimport_features
- Produces: `latch.geo.service.Geofeature`(pydantic: `source: Literal["isj_town","osm_poi"]`・`kind: str`・`name: str`・`city_name: str | None`・`pref_name: str | None`・`lon: float`・`lat: float`)・`latch.geo.service.GeoService(engine: AsyncEngine)` の `async geocode_forward(name: str) -> Geofeature | None` と `async reverse_geocode(lon: float, lat: float) -> str | None`。M1(保存API)とM2(Layer 5)がこのIFを消費する

- [ ] **Step 1: 失敗するテストを書く(test_geo.py へ追記)**

import節へ追記:

```python
from latch.geo.service import GeoService
```

ファイル末尾へ追記:

```python
# --- design §4-5: 正転(fixture ISJ取り込み後。期待値は名称照合なので座標に依存しない) ---

async def _import_isj(db_engine, fake_clock):
    return await import_features(
        db_engine, fake_clock, _isj_fixture_rows(), "isj_town"
    )


async def test_forward_isj_town_name(db_engine, fake_clock):
    await _import_isj(db_engine, fake_clock)
    hit = await GeoService(db_engine).geocode_forward("伊敷町")
    assert hit is not None
    assert hit.source == "isj_town"
    assert hit.kind == "town"
    assert hit.city_name == "鹿児島市"
    assert hit.pref_name == "鹿児島県"
    assert hit.lon == pytest.approx(130.552)
    assert hit.lat == pytest.approx(31.605)


async def test_forward_full_city_name(db_engine, fake_clock):
    """市区町村名連結(「鹿児島市○○」形式)でもfull_normalized_name経由でヒット。"""
    await _import_isj(db_engine, fake_clock)
    hit = await GeoService(db_engine).geocode_forward("鹿児島市天文館一丁目")
    assert hit is not None
    assert hit.name == "天文館一丁目"


async def test_forward_normalizes_input(db_engine, fake_clock):
    """入力の表記ゆれ(算用数字丁目)は正規化で吸収される(design §2.4)。"""
    await _import_isj(db_engine, fake_clock)
    hit = await GeoService(db_engine).geocode_forward("天文館1丁目")
    assert hit is not None
    assert hit.name == "天文館一丁目"


async def test_forward_missing_returns_none(db_engine, fake_clock):
    """該当なし=None。M1が422 GEOCODING_FAILEDへ写像する(05 第5節)。"""
    await _import_isj(db_engine, fake_clock)
    assert await GeoService(db_engine).geocode_forward("存在しない町") is None


# --- design §4-6: 逆転(期待値を決定的にするため地物2行を直構築・遠隔配置) ---

_REVERSE_ROWS = [
    FeatureRow(
        kind="town", name="西之段町", pref_name="鹿児島県", city_name="鹿児島市",
        source_code="46201007001", lon=130.5450, lat=31.5850, attrs={},
    ),
    FeatureRow(
        kind="town", name="東之段町", pref_name="鹿児島県", city_name="鹿児島市",
        source_code="46201008001", lon=130.5720, lat=31.6080, attrs={},
    ),
]


async def _import_reverse_rows(db_engine, fake_clock):
    # 2行のみの状態を作る(osm_poiも消す — 試験ごとに状態を構築する原則)
    async with db_engine.begin() as conn:
        await conn.execute(text("DELETE FROM geofeatures"))
    return await import_features(
        db_engine, fake_clock, _REVERSE_ROWS, "isj_town"
    )


async def test_reverse_returns_city_plus_name(db_engine, fake_clock):
    """座標→「市区町村名+地物名」のarea_name(08 D-11の形式)。"""
    await _import_reverse_rows(db_engine, fake_clock)
    svc = GeoService(db_engine)
    # 入力=地物そのものの座標。代表点(≤707m)への最近傍はもう一方(約3.6km先)ではない
    assert await svc.reverse_geocode(130.5450, 31.5850) == "鹿児島市西之段町"
    assert await svc.reverse_geocode(130.5720, 31.6080) == "鹿児島市東之段町"


async def test_reverse_is_deterministic(db_engine, fake_clock):
    """同一入力の反復実行で同一結果(決定性 — design §2.5)。"""
    await _import_reverse_rows(db_engine, fake_clock)
    svc = GeoService(db_engine)
    results = [await svc.reverse_geocode(130.5500, 31.5900) for _ in range(3)]
    assert len(set(results)) == 1


async def test_reverse_grid_snap_within_1km_cell(db_engine, fake_clock):
    """Review Focus #3: 丸めは3857へ落として約1km — 代表点と入力の距離は
    1kmセルの対角の半分(≈707m)以内。4326のまま丸めると≈111km刻みになる。"""
    await _import_reverse_rows(db_engine, fake_clock)
    async with db_engine.connect() as conn:
        distance_m = (
            await conn.execute(
                text("""
                    WITH input AS (SELECT ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) AS g),
                    grid AS (
                      SELECT ST_Transform(ST_SnapToGrid(
                        ST_Transform(input.g, 3857), 1000.0), 4326) AS g FROM input
                    )
                    SELECT ST_Distance(grid.g::geography, input.g::geography)
                    FROM input, grid
                """),
                {"lon": 130.5500, "lat": 31.5900},
            )
        ).scalar()
    assert distance_m is not None and distance_m < 750.0


async def test_reverse_grid_cell_points_share_result(db_engine, fake_clock):
    """(b) 同一の1kmグリッドセルに属す2点が同一のarea_name(design §4-6)。
    入力Pとその代表点R=snap(P)は同じセルに属し、共通の代表点を持つ
    (代表点自身は丸めで不動)→ 逆転結果も一致する。"""
    await _import_reverse_rows(db_engine, fake_clock)
    async with db_engine.connect() as conn:
        snapped = (
            await conn.execute(
                text("""
                    WITH input AS (SELECT ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) AS g),
                    grid AS (
                      SELECT ST_Transform(ST_SnapToGrid(
                        ST_Transform(input.g, 3857), 1000.0), 4326) AS g FROM input
                    )
                    SELECT ST_X(grid.g::geometry), ST_Y(grid.g::geometry) FROM grid
                """),
                {"lon": 130.5500, "lat": 31.5900},
            )
        ).first()
    assert snapped is not None
    svc = GeoService(db_engine)
    from_point = await svc.reverse_geocode(130.5500, 31.5900)
    from_representative = await svc.reverse_geocode(snapped[0], snapped[1])
    assert from_point is not None
    assert from_point == from_representative


async def test_reverse_empty_table_returns_none(db_engine):
    """地物なし(未取り込み)=None(design §2.5)。"""
    async with db_engine.begin() as conn:
        await conn.execute(text("DELETE FROM geofeatures"))
    assert await GeoService(db_engine).reverse_geocode(130.5585, 31.5965) is None
```

- [ ] **Step 2: テストの収集を確認**

Run: `cd backend && uv run --group geo pytest --collect-only -q`
Expected: エラーなし(新規9試験を含む)

- [ ] **Step 3: service.py を実装**

`backend/src/latch/geo/service.py`:

```python
"""正転・逆転ジオコーディング(04 第3節・08 D-11)。PostGIS上のSQLのみ —
位置情報を外部サービスへ送る経路を持たない(geo配下network import禁止の
arch testが強制)。

- 正転: normalize_name(name) で normalized_name / full_normalized_name へ
  完全一致。候補は osm_poi優先→id昇順 の決定的順位で1件(design §2.4)
- 逆転: 約1kmグリッド丸め(3857でST_SnapToGrid)→代表点に最も近い地物→
  「市区町村名+地物名」(design §2.5)。OSM POIは市区町村名を第2クエリで補完
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.geo.normalize import normalize_name

# 約1kmグリッド丸め(04 第3節「投影座標系へ落として丸め」)。原点(0,0)基準で一意。
_GRID_CTE = """
    WITH input AS (SELECT ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) AS g),
    grid AS (
      SELECT ST_Transform(
               ST_SnapToGrid(ST_Transform(input.g, 3857), 1000.0), 4326) AS g
      FROM input
    )
"""

_FORWARD_SQL = text("""
    SELECT source, kind, name, city_name, pref_name,
           ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat
    FROM geofeatures
    WHERE normalized_name = :n OR full_normalized_name = :n
    ORDER BY CASE source WHEN 'osm_poi' THEN 0 ELSE 1 END, id
    LIMIT 1
""")

_NEAREST_SQL = text(
    _GRID_CTE
    + """
    SELECT source, kind, name, city_name
    FROM geofeatures, grid
    ORDER BY ST_Distance(geofeatures.geom, grid.g::geography),
             CASE source WHEN 'osm_poi' THEN 0 ELSE 1 END,
             id
    LIMIT 1
"""
)

_NEAREST_ISJ_CITY_SQL = text(
    _GRID_CTE
    + """
    SELECT city_name
    FROM geofeatures, grid
    WHERE source = 'isj_town' AND city_name IS NOT NULL
    ORDER BY ST_Distance(geofeatures.geom, grid.g::geography), id
    LIMIT 1
"""
)


class Geofeature(BaseModel):
    """正転の結果(geo_centerの供給源。M1がintents.geo_centerへ格納する)。"""

    source: Literal["isj_town", "osm_poi"]
    kind: str
    name: str
    city_name: str | None
    pref_name: str | None
    lon: float
    lat: float


class GeoService:
    """正転・逆転(04 第3節)。外部送信経路を持たない(SQLのみ)。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def geocode_forward(self, name: str) -> Geofeature | None:
        """地名→代表点。該当なし=None(M1が422 GEOCODING_FAILEDへ写像)。"""
        normalized = normalize_name(name)
        if not normalized:
            return None
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(_FORWARD_SQL, {"n": normalized})
            ).mappings().first()
        if row is None:
            return None
        return Geofeature(**row)

    async def reverse_geocode(self, lon: float, lat: float) -> str | None:
        """座標→地域名(「市区町村名+地物名」。08 D-11)。地物なし=None。"""
        params = {"lon": lon, "lat": lat}
        async with self._engine.connect() as conn:
            nearest = (await conn.execute(_NEAREST_SQL, params)).mappings().first()
            if nearest is None:
                return None
            city_name = nearest["city_name"]
            if city_name is None:
                # OSM POIは市区町村名を持たない → 同じ代表点に最も近いISJ町丁目の
                # 市区町村名で補完(design §2.5)。ISJが無い場合のみname単独
                city_row = (
                    await conn.execute(_NEAREST_ISJ_CITY_SQL, params)
                ).mappings().first()
                city_name = city_row["city_name"] if city_row is not None else None
        if city_name:
            return f"{city_name}{nearest['name']}"
        return nearest["name"]
```

- [ ] **Step 4: (実行する場合)test-ci相当で確認**

```bash
make test-ci
```

Expected: exit 0。実行しない場合は収集確認のみ(§0)。

- [ ] **Step 5: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/geo/service.py backend/tests/integration/test_geo.py
git commit -m "feat: 正転・逆転ジオコーディング(PostGIS完結・決定的順位・1kmグリッド丸め)"
```

---

### Task 7: OSM読み取り(pyosmium)— 名称付きPOI抽出とOSM優先順位

**Files:**
- Create: `backend/src/latch/geo/osm.py`、`backend/tests/fixtures/geo/osm_sample.xml`(UTF-8)
- Test: `backend/tests/integration/test_geo.py`(追記。osmium依存のためintegration側 — design §4)

**Interfaces:**
- Consumes: Task 3の `FeatureRow` / `BBox`・Task 5の `import_features`・Task 6の `GeoService`
- Produces: `latch.geo.osm.iter_osm_pois(pbf_path: Path | str, *, bbox: BBox | None = None) -> Iterator[FeatureRow]`(pbf/xml両対応・名称ありPOIのみ・kindはタグ由来・wayはノード座標の平均点を代表点とする)。CLIのimport-osmが利用

- [ ] **Step 1: fixture(osm_sample.xml)を作る**

`backend/tests/fixtures/geo/osm_sample.xml`(UTF-8のまま):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="latch-test-fixture">
  <!-- 採用: place=neighbourhood(district) -->
  <node id="1" lat="31.5962" lon="130.5590">
    <tag k="name" v="天文館"/>
    <tag k="place" v="neighbourhood"/>
  </node>
  <!-- 採用: amenity=bar -->
  <node id="2" lat="31.5955" lon="130.5575">
    <tag k="name" v="バー宵待"/>
    <tag k="amenity" v="bar"/>
  </node>
  <!-- 除外: 名称なし -->
  <node id="3" lat="31.5960" lon="130.5580">
    <tag k="amenity" v="restaurant"/>
  </node>
  <!-- 除外: bbox外(東京) -->
  <node id="4" lat="35.6812" lon="139.7671">
    <tag k="name" v="東京駅"/>
    <tag k="railway" v="station"/>
  </node>
  <!-- 採用: place=quarter・ISJと同名(同名優先順位の試験用) -->
  <node id="5" lat="31.6055" lon="130.5515">
    <tag k="name" v="伊敷町"/>
    <tag k="place" v="quarter"/>
  </node>
  <!-- way構成ノード(タグなし) -->
  <node id="101" lat="31.6040" lon="130.5610"/>
  <node id="102" lat="31.6042" lon="130.5615"/>
  <node id="103" lat="31.6041" lon="130.5612"/>
  <!-- 採用: tourism=hotel(ウェイ。代表点=ノード座標の平均) -->
  <way id="10">
    <nd ref="101"/>
    <nd ref="102"/>
    <nd ref="103"/>
    <nd ref="101"/>
    <tag k="name" v="城山ホテル"/>
    <tag k="tourism" v="hotel"/>
  </way>
</osm>
```

期待される抽出: 天文館(district)・バー宵待(bar)・伊敷町(district)・城山ホテル(hotel)の4行。東京駅はbbox外・node3は名称なしで除外。

- [ ] **Step 2: 失敗するテストを書く(test_geo.py へ追記)**

import節へ追記:

```python
from latch.geo.osm import iter_osm_pois
```

ファイル末尾へ追記:

```python
# --- design §4(OSM読み取り)+ §4-5(a)(e) ---

def _osm_fixture_rows():
    return list(iter_osm_pois(FIXTURES / "osm_sample.xml", bbox=DEFAULT_BBOX))


async def test_osm_fixture_rows_extracted():
    rows = _osm_fixture_rows()
    by_code = {r.source_code: r for r in rows}
    # 名称ありPOI+bbox内のみ(名称なし・東京駅は除外)
    assert set(by_code) == {"node/1", "node/2", "node/5", "way/10"}
    assert by_code["node/1"].kind == "district"
    assert by_code["node/1"].name == "天文館"
    assert by_code["node/2"].kind == "bar"
    assert by_code["node/5"].kind == "district"
    # 主要タグがattrsへ入る(Review Focus #5と同型の実読み取り)
    assert by_code["node/2"].attrs == {"amenity": "bar"}


async def test_osm_way_representative_point_is_node_average():
    row = {r.source_code: r for r in _osm_fixture_rows()}["way/10"]
    # wayの代表点=構成ノード座標の平均(閉ウェイの重複ノード含む — design §2.3)
    assert row.lon == pytest.approx((130.5610 + 130.5615 + 130.5612 + 130.5610) / 4)
    assert row.lat == pytest.approx((31.6040 + 31.6042 + 31.6041 + 31.6040) / 4)


async def test_forward_osm_poi(db_engine, fake_clock):
    """(a) osm_poiの名称がヒットし座標が返る(design §4-5)。"""
    await _import_isj(db_engine, fake_clock)
    await import_features(db_engine, fake_clock, _osm_fixture_rows(), "osm_poi")
    hit = await GeoService(db_engine).geocode_forward("バー宵待")
    assert hit is not None
    assert hit.source == "osm_poi"
    assert hit.kind == "bar"
    assert hit.lon == pytest.approx(130.5575)
    assert hit.lat == pytest.approx(31.5955)


async def test_forward_same_name_prefers_osm(db_engine, fake_clock):
    """(e) POIと町丁目の同名ではosm_poiが選ばれる(決定的順位 — design §2.4)。"""
    await _import_isj(db_engine, fake_clock)
    await import_features(db_engine, fake_clock, _osm_fixture_rows(), "osm_poi")
    hit = await GeoService(db_engine).geocode_forward("伊敷町")
    assert hit is not None
    assert hit.source == "osm_poi"
    assert hit.kind == "district"


async def test_reverse_osm_poi_city_complemented(db_engine, fake_clock):
    """逆転でOSM POIが最近傍のとき、市区町村名をISJから補完(design §2.5)。
    「バー宵待」(130.5575, 31.5955)の代表点は天文館一丁目・伊敷町より
    バー宵待自身に近い(他地物は≥400m離れて配置済み)。"""
    await _import_isj(db_engine, fake_clock)
    await import_features(db_engine, fake_clock, _osm_fixture_rows(), "osm_poi")
    area = await GeoService(db_engine).reverse_geocode(130.5575, 31.5955)
    assert area is not None
    assert area == "鹿児島市バー宵待"
```

注意(期待値の前提): `test_reverse_osm_poi_city_complemented` は「バー宵待」自身の座標を入力とする。1kmグリッド丸めで代表点が最大707m移動しても、地物はバー宵待(距離0)・天文館一丁目(≈300m)・大字草牟田(≈800m)・伊敷町(≈1.1km)であり、代表点がどこに転んでも最近傍はバー宵待または天文館一丁目のいずれかになる。**実行して期待値が「鹿児島市天文館一丁目」になった場合**は、fixtureのバー宵待(node/2)と天文館一丁目(ISJ)をさらに離して(例: node/2 を `lat="31.5840" lon="130.5460"` へ)再実行すること — 判断と変更内容を報告ファイルへ記録する。

- [ ] **Step 3: osm.py を実装**

`backend/src/latch/geo/osm.py`:

```python
"""OSM(pbf/xml)読み取り — 名称付きPOI抽出(design §2.3-A)。

pyosmium(FileProcessor)でローカルファイルを読むのみ(osmiumのネットワーク
機能は使わない — design §1.5)。抽出条件:
  amenity ∈ {bar,pub,biergarten,cafe,restaurant,fast_food,food_court,
              ice_cream,nightclub} / railway=station / highway=bus_stop /
  tourism=hotel / place ∈ {suburb,quarter,neighbourhood}
name(またはname:ja)を持たない要素は除外。geomは node=その点、
way=構成ノード座標の平均(代表点)。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from latch.geo.ingest import BBox, FeatureRow

AMENITY_KINDS = frozenset(
    {
        "bar", "pub", "biergarten", "cafe", "restaurant",
        "fast_food", "food_court", "ice_cream", "nightclub",
    }
)
PLACE_KINDS = frozenset({"suburb", "quarter", "neighbourhood"})
# attrsへ保存する主要タグ(design §3.1: OSM=主要タグ)
_MAJOR_TAGS = ("amenity", "railway", "highway", "tourism", "place")


def classify(tags: dict[str, str]) -> str | None:
    """タグ→kind。抽出対象外(None)は呼び出し側で除外する。"""
    amenity = tags.get("amenity")
    if amenity in AMENITY_KINDS:
        return amenity
    if tags.get("railway") == "station":
        return "station"
    if tags.get("highway") == "bus_stop":
        return "bus_stop"
    if tags.get("tourism") == "hotel":
        return "hotel"
    if tags.get("place") in PLACE_KINDS:
        return "district"
    return None


def iter_osm_pois(
    pbf_path: Path | str, *, bbox: BBox | None = None
) -> Iterator[FeatureRow]:
    """OSMファイル(pbf/xml自動判別)から名称付きPOIを列挙する。"""
    import osmium  # geo dependency-group専用(遅延import — design §2.7)

    fp = osmium.FileProcessor(str(pbf_path)).with_locations()
    for obj in fp:
        tags = dict(obj.tags)
        kind = classify(tags)
        if kind is None:
            continue
        name = tags.get("name") or tags.get("name:ja")
        if not name:
            continue  # 名称なし要素は除外(design §2.3)
        if obj.is_node():
            lon, lat = obj.location.lon, obj.location.lat
            element = "node"
        elif obj.is_way():
            coords = [(n.lon, n.lat) for n in obj.nodes if n.location.valid()]
            if not coords:
                continue
            lon = sum(c[0] for c in coords) / len(coords)
            lat = sum(c[1] for c in coords) / len(coords)
            element = "way"
        else:
            continue  # relationは対象外
        if bbox is not None and not bbox.contains(lon, lat):
            continue
        yield FeatureRow(
            kind=kind,
            name=name,
            source_code=f"{element}/{obj.id}",
            lon=lon,
            lat=lat,
            attrs={k: tags[k] for k in _MAJOR_TAGS if k in tags},
        )
```

(`import osmium` を関数内に置くのは意図的: このモジュールをimportする側(CLIの引数解析など)がgeoグループなし環境でも動くようにする。osmiumが無い状態で `iter_osm_pois` を呼ぶとImportErrorになる — それは `uv run --group geo` を忘れた運用エラー)

- [ ] **Step 4: テストの収集を確認**

Run: `cd backend && uv run --group geo pytest --collect-only -q`
Expected: エラーなし(新規5試験を含む)

- [ ] **Step 5: (実行する場合)test-ci相当で確認**

```bash
make test-ci
```

Expected: exit 0。実行しない場合は収集確認のみ(§0)。

- [ ] **Step 6: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/geo/osm.py backend/tests/fixtures/geo/osm_sample.xml backend/tests/integration/test_geo.py
git commit -m "feat: OSM名称付きPOI抽出(pyosmium・bboxフィルタ・way代表点)"
```

---

### Task 8: CLI(python -m latch.geo)+ Makefile + README(出所・ライセンス)

**Files:**
- Create: `backend/src/latch/geo/__main__.py`
- Modify: `Makefile`(test-ci行修正+geoターゲット3件+.PHONY)、`backend/README.md`(末尾へ節を追記)
- Test: `backend/tests/unit/geo/test_cli_args.py`

**Interfaces:**
- Consumes: Task 1のSettings・Task 3/5/7の読み取り・取り込み・Task 6のGeoService・雛形の `create_db_engine`・`SystemClock`
- Produces: `latch.geo.__main__.build_parser() -> ArgumentParser`・`_effective_area(args, settings) -> tuple[set[str], BBox]`・`main(argv: list[str] | None = None) -> int`。Makeターゲット `geo-download` / `geo-import` / `geo-verify`。`test-ci` の `--group geo` 化

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/geo/test_cli_args.py`:

```python
"""CLI引数解析とsettings既定値の解決(design §4-7)。

サブコマンド実行そのもの(実DB接続)はスーパーバイザーによる make geo-import /
geo-verify 実行で確認する — ここでは引数解析とエリア解決の純函数部分のみ。
"""

import pytest

from latch.geo.__main__ import _effective_area, build_parser
from latch.geo.ingest import BBox
from latch.settings import Settings

GEO_ENV_VARS = ("LATCH_GEO_AREA_NAME", "LATCH_GEO_ISJ_CITY_CODES", "LATCH_GEO_OSM_BBOX")


def _settings(monkeypatch) -> Settings:
    for var in GEO_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return Settings()


def _parse(argv: list[str]):
    return build_parser().parse_args(argv)


def test_subcommand_is_required(capsys):
    with pytest.raises(SystemExit):
        _parse([])


def test_import_isj_args():
    args = _parse(["import-isj", "--csv", "data/geo/isj.csv"])
    assert args.command == "import-isj"
    assert args.csv == "data/geo/isj.csv"
    assert args.city_codes is None
    assert args.bbox is None


def test_import_isj_explicit_overrides():
    args = _parse(
        ["import-isj", "--csv", "x.csv", "--city-codes", "46201,46202",
         "--bbox", "1.0,2.0,3.0,4.0"]
    )
    assert args.city_codes == "46201,46202"
    assert args.bbox == "1.0,2.0,3.0,4.0"


def test_import_osm_args():
    args = _parse(["import-osm", "--pbf", "data/geo/kyushu-latest.osm.pbf"])
    assert args.command == "import-osm"
    assert args.pbf == "data/geo/kyushu-latest.osm.pbf"
    assert args.bbox is None


def test_verify_args_defaults():
    args = _parse(["verify"])
    assert args.command == "verify"
    assert args.forward == "天文館"
    assert args.reverse is None


def test_verify_reverse_takes_lon_lat():
    args = _parse(["verify", "--reverse", "130.5585", "31.5965"])
    assert args.reverse == [130.5585, 31.5965]


def test_effective_area_defaults_to_settings(monkeypatch):
    # Review Focus #2: 引数省略時はsettings既定値(ci暫定エリア)へ解決される
    args = _parse(["import-isj", "--csv", "x.csv"])
    city_codes, bbox = _effective_area(args, _settings(monkeypatch))
    assert city_codes == {"46201"}
    assert bbox == BBox.parse("130.5420,31.5825,130.5740,31.6095")


def test_effective_area_args_override_settings(monkeypatch):
    args = _parse(
        ["import-osm", "--pbf", "x.pbf", "--bbox", "127.0,26.0,128.0,27.0"]
    )
    city_codes, bbox = _effective_area(args, _settings(monkeypatch))
    assert city_codes == {"46201"}  # osm側は未指定 → settings既定
    assert bbox == BBox.parse("127.0,26.0,128.0,27.0")


def test_effective_area_rejects_bad_bbox(monkeypatch):
    # Review Focus #2: 形式破損はValueErrorで即失敗(黙って続けない)
    args = _parse(["import-osm", "--pbf", "x.pbf", "--bbox", "broken"])
    with pytest.raises(ValueError, match="bbox"):
        _effective_area(args, _settings(monkeypatch))
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/geo/test_cli_args.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.geo.__main__'`)

- [ ] **Step 3: __main__.py を実装**

`backend/src/latch/geo/__main__.py`:

```python
"""地物データ取り込みCLI(design §2.2-A・§3.1)。

実行形態はalembic migrateと同じ「ホストのuvからcompose常設DB(127.0.0.1:5432)
へ接続」パターン。Makeターゲット(geo-download/geo-import/geo-verify)から
 `uv run --group geo python -m latch.geo ...` で起動する。

出力規律(08 第2.4節・design §6-5): verifyは名称・市区町村名・件数のみ出力し、
座標は出さない(出力の最小化に統一)。
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from latch.core.clock import SystemClock
from latch.core.db import create_db_engine
from latch.geo.ingest import BBox, import_features
from latch.geo.isj import iter_isj_towns
from latch.geo.service import GeoService
from latch.settings import Settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latch.geo",
        description="地物データの取り込みとジオコーディング確認(ws-4)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_isj = sub.add_parser("import-isj", help="位置参照情報CSVを取り込み")
    p_isj.add_argument("--csv", required=True, help="ISJ CSVパス(cp932/utf-8自動判定)")
    p_isj.add_argument("--city-codes", default=None, help="市区町村コード(カンマ区切り)")
    p_isj.add_argument("--bbox", default=None, help="min_lon,min_lat,max_lon,max_lat")

    p_osm = sub.add_parser("import-osm", help="OSM(pbf/xml)の名称付きPOIを取り込み")
    p_osm.add_argument("--pbf", required=True, help=".osm.pbf / .osm ファイルパス")
    p_osm.add_argument("--bbox", default=None, help="min_lon,min_lat,max_lon,max_lat")

    p_verify = sub.add_parser("verify", help="正転・逆転のサンプル確認(座標は出さない)")
    p_verify.add_argument("--forward", default="天文館", help="正転する地名")
    p_verify.add_argument(
        "--reverse",
        nargs=2,
        type=float,
        metavar=("LON", "LAT"),
        default=None,
        help="逆転する座標(経度 緯度)",
    )
    return parser


def _effective_area(args: argparse.Namespace, settings: Settings) -> tuple[set[str], BBox]:
    """CLI引数 → エリア設定へ解決(省略時はsettings既定値=ci暫定エリア)。"""
    raw_codes = getattr(args, "city_codes", None) or settings.geo_isj_city_codes
    city_codes = {c.strip() for c in raw_codes.split(",") if c.strip()}
    bbox = BBox.parse(getattr(args, "bbox", None) or settings.geo_osm_bbox)
    return city_codes, bbox


async def _run_import_isj(args: argparse.Namespace, settings: Settings) -> int:
    city_codes, bbox = _effective_area(args, settings)
    rows = list(
        iter_isj_towns(args.csv, city_codes=city_codes, bbox=bbox)
    )
    engine = create_db_engine(settings)
    try:
        count = await import_features(engine, SystemClock(), rows, "isj_town")
    finally:
        await engine.dispose()
    print(f"[import-isj] area={settings.geo_area_name} isj_town: {count} rows")
    return 0


async def _run_import_osm(args: argparse.Namespace, settings: Settings) -> int:
    from latch.geo.osm import iter_osm_pois  # 遅延import(osmiumはgeoグループ専用)

    _, bbox = _effective_area(args, settings)
    rows = list(iter_osm_pois(args.pbf, bbox=bbox))
    engine = create_db_engine(settings)
    try:
        count = await import_features(engine, SystemClock(), rows, "osm_poi")
    finally:
        await engine.dispose()
    print(f"[import-osm] area={settings.geo_area_name} osm_poi: {count} rows")
    return 0


async def _run_verify(args: argparse.Namespace, settings: Settings) -> int:
    engine = create_db_engine(settings)
    try:
        async with engine.connect() as conn:
            counts = (
                await conn.execute(
                    text("SELECT source, count(*) FROM geofeatures GROUP BY source")
                )
            ).all()
        print(f"[verify] area={settings.geo_area_name}")
        for source, count in sorted(counts):
            print(f"  {source}: {count} rows")
        service = GeoService(engine)
        hit = await service.geocode_forward(args.forward)
        if hit is None:
            print(f"  forward {args.forward!r}: -> None (422 GEOCODING_FAILED相当)")
        else:
            city = hit.city_name or ""
            print(f"  forward {args.forward!r}: -> {city}{hit.name} ({hit.source}/{hit.kind})")
        if args.reverse is not None:
            lon, lat = args.reverse
            area = await service.reverse_geocode(lon, lat)
            print(f"  reverse: -> {area!r}")
    finally:
        await engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings()
    if args.command == "import-isj":
        return asyncio.run(_run_import_isj(args, settings))
    if args.command == "import-osm":
        return asyncio.run(_run_import_osm(args, settings))
    return asyncio.run(_run_verify(args, settings))


if __name__ == "__main__":
    raise SystemExit(main())
```

(座標はverifyの出力に現れない — 08 第2.4節の趣旨。デバッグで座標確認が必要な場合はpsql等のDB直接確認を想定)

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/geo/test_cli_args.py -v`
Expected: PASS(9件)

- [ ] **Step 5: Makefile へ追記・test-ciを修正**

`Makefile` — `.PHONY` 行を更新:

```makefile
.PHONY: setup up down ps logs lint test test-ci migrate geo-download geo-import geo-verify
```

`test-ci` ターゲットの recipe を差し替え(最終行のみ変更):

```makefile
test-ci: ## ci環境試験(compose起動 → unit+integration。--group geoでosmium込み)
	docker compose up -d --wait
	cd backend && uv run --group geo pytest
```

ファイル末尾へ3ターゲットを追記:

```makefile
geo-download: ## 地物データ取得(OSM九州extractをcurl。ISJ入手先と手順を表示)
	mkdir -p backend/data/geo
	curl -L -C - -o backend/data/geo/kyushu-latest.osm.pbf https://download.geofabrik.de/asia/japan/kyushu-latest.osm.pbf
	@echo "ISJ(大字・町丁目レベル)は下記から手動取得し backend/data/geo/ へ配置:"
	@echo "  https://nlftp.mlit.go.jp/cgi-bin/isj/dls/_choose_method.cgi (都道府県単位のzip→解凍したCSV)"

geo-import: ## settingsのエリア設定でISJ+OSMをPostGISへ取り込み(CSV/PBFはgeo-downloadの配置先)
	cd backend && uv run --group geo python -m latch.geo import-isj --csv data/geo/isj.csv
	cd backend && uv run --group geo python -m latch.geo import-osm --pbf data/geo/kyushu-latest.osm.pbf

geo-verify: ## 正転・逆転のサンプル確認(PostGIS完結の動作確認・座標は出さない)
	cd backend && uv run --group geo python -m latch.geo verify
```

(取得コマンドはcurlで確定 — design §3.1の「wgetをcurlにするかは計画書で確定」の裁定。`-C -` でレジューム対応)

- [ ] **Step 6: backend/README.md へ出所・ライセンスと手順を追記**

`backend/README.md` の末尾へ追記:

```markdown
## 地物データ(ws-4)

ジオコーディング(正転・逆転)はPostGIS上の `geofeatures` テーブルで完結する
(外部送信経路ゼロ — 04 第3節・08 D-11)。

```bash
make geo-download  # OSM九州extractを取得 + ISJ入手先を表示(手動配置)
make geo-import    # settingsのエリア設定でISJ+OSMを取り込み(フルリロード・冪等)
make geo-verify    # 正転・逆転のサンプル確認(名称・市区町村名のみ出力)
```

- エリアは設定で与える(`LATCH_GEO_ISJ_CITY_CODES` / `LATCH_GEO_OSM_BBOX`。
  既定値=ci暫定エリア: 鹿児島市天文館周辺約3km四方)。初期エリアの最終指定はT2
- ISJのCSVは解凍後 `backend/data/geo/isj.csv` へリネーム配置
  (年度によりファイル名が異なるため)
- 取り込みは `source` 単位のフルリロード(冪等)。エリア差し替えは設定変更+
  再実行のみ

### 出所・ライセンス

- 位置参照情報(大字・町丁目レベル): 国土交通省「位置参照情報ダウンロード
  サービス」(https://nlftp.mlit.go.jp/isj/)のデータを利用。利用約款に基づき
  出所を明記する。取り込み時のデータ年度はgeo-importの実行記録に残す
- OpenStreetMap: OpenStreetMap contributors による ODbL ライセンスのデータ
  (Geofabrik extract: https://download.geofabrik.de/asia/japan.html)を利用
```

- [ ] **Step 7: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/geo/__main__.py backend/tests/unit/geo/test_cli_args.py Makefile backend/README.md
git commit -m "feat: 地物取り込みCLI(import-isj/import-osm/verify)とMake geoターゲット・出所明記"
```

---

### Task 9: arch test(geo配下network禁止)+ 公開IF再export + 受渡し検証と報告

**Files:**
- Create: `backend/tests/unit/test_arch_geo_no_network.py`、`docs/plans/M0/ws-4-report.md`
- Modify: `backend/src/latch/geo/__init__.py`(再export)

**Interfaces:**
- Consumes: 全タスク
- Produces: `latch.geo` 公開API(M1/M2はこれ越しにGeoService等を利用する — `iter_osm_pois` はosmium依存のため再exportせず `latch.geo.osm` から直接importする)。完了条件8項目の証拠と報告。G0の「外部送信経路ゼロ」のコード検査

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/test_arch_geo_no_network.py`:

```python
"""geo配下のネットワークimport禁止(04 第3節「位置情報を外部サービスへ送る
経路を持たない」のコード検査。design §4-8)。

osmium自体のimportは許可(ローカルファイル読み取りのみに使用 — design §1.5)。
"""

from pathlib import Path

GEO = Path(__file__).resolve().parents[2] / "src" / "latch" / "geo"

FORBIDDEN_TOKENS = (
    "import requests",
    "import urllib",
    "import socket",
    "import httpx",
    "import aiohttp",
    "import http.client",
)


def test_geo_imports_no_network_modules():
    offenders: list[str] = []
    for path in sorted(GEO.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            if token in text:
                offenders.append(f"{path.relative_to(GEO)}: {token}")
    assert not offenders, (
        "geo配下でネットワーク経路を開いてはならない(04 第3節・外部送信ゼロ): "
        + ", ".join(offenders)
    )
```

Run: `cd backend && uv run pytest tests/unit/test_arch_geo_no_network.py -v`
Expected: PASS(既にgeo配下にnetwork importがないため、この時点で通る — 検査のピン留め)

- [ ] **Step 2: 失敗するテストを書く(再export検査 — test_cli_args.py へ追記)**

`backend/tests/unit/geo/test_cli_args.py` のファイル末尾へ追記:

```python
def test_public_api_reexports():
    import latch.geo as api

    for name in (
        "GeoService",
        "Geofeature",
        "FeatureRow",
        "BBox",
        "SOURCES",
        "import_features",
        "normalize_name",
        "iter_isj_towns",
    ):
        assert getattr(api, name, None) is not None, name
```

Run: `cd backend && uv run pytest tests/unit/geo/test_cli_args.py::test_public_api_reexports -v`
Expected: FAIL(`AssertionError: GeoService` — 再export前)

- [ ] **Step 3: `geo/__init__.py` を再exportへ書き換える**

`backend/src/latch/geo/__init__.py` の内容を丸ごと置き換える:

```python
"""地物データ取り込み・ジオコーディング(M0 ws-4)。PostGIS上で完結(外部送信経路ゼロ)。

M1(保存API)とM2(Layer 5)はこのパッケージ越しにGeoService等を利用する
(design §3.1)。iter_osm_poisのみosmium(geoグループ)依存のため再exportしない
— 必要な場合は latch.geo.osm から直接importすること。
"""

from latch.geo.ingest import BBox, FeatureRow, SOURCES, import_features
from latch.geo.isj import iter_isj_towns
from latch.geo.normalize import normalize_name
from latch.geo.service import Geofeature, GeoService

__all__ = [
    "BBox",
    "FeatureRow",
    "Geofeature",
    "GeoService",
    "SOURCES",
    "import_features",
    "iter_isj_towns",
    "normalize_name",
]
```

Run: `cd backend && uv run pytest tests/unit/geo/test_cli_args.py -v`
Expected: PASS(11件)

- [ ] **Step 4: 完了条件1 — lint / test**

```bash
make lint
make test
```

Expected: 2コマンドともexit 0。出力末尾のpytestサマリー行を記録する。

- [ ] **Step 5: 完了条件2・3 — integration試験(実行する場合)**

§0の運用に従い、共有ci-dbを進めてよい状況なら:

```bash
make test-ci
cd backend && uv run alembic history
```

Expected: pytestがexit 0(geofeatures系integration試験を含む全体)。`alembic history` が `0001 -> 0002` を示す。実行しない場合:

```bash
cd backend && uv run --group geo pytest --collect-only -q
uv run alembic history
```

で収集と履歴を記録し、報告書に「スーパーバイザー検証待ち」と書く。

- [ ] **Step 6: 完了条件4・5 — 実時間参照とnetwork importの所在**

```bash
rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src
rg -n 'import requests|import urllib|import socket|import httpx|import aiohttp|import http\.client' backend/src/latch/geo/ ; echo "exit=$?"
cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py tests/unit/test_arch_geo_no_network.py -v
```

Expected: 1つ目のrgヒットが `backend/src/latch/core/clock.py` の行のみ。2つ目のrgはexit=1(ゼロヒット)。arch test 2件ともexit 0。

- [ ] **Step 7: 完了条件6・7 — 差分の所在**

```bash
git diff --stat main -- backend/Dockerfile compose.yaml
git diff main -- backend/pyproject.toml
git diff --name-only main | sort
git diff main -- backend/src/latch/settings.py
git status --short
```

Expected: 1つ目は出力なし。2つ目は `[dependency-groups]` のgeo追加のみ。3つ目は§4の一覧と完全一致。4つ目は追記のみ(既存行の変更・削除を含まない)。5つ目は空。

- [ ] **Step 8: ruff format を通す**

```bash
cd backend && uv run ruff format . && uv run ruff check .
git status --short
```

変更があった場合のみ:

```bash
git add -u && git commit -m "style: ruff format 適用"
```

- [ ] **Step 9: 報告ファイルを作成してコミット**

`docs/plans/M0/ws-4-report.md` を §7 の形式で作成する。Step 4〜7の出力要点を貼る。「スーパーバイザー向けG0証拠手順」節(§7テンプレートの完了条件8)にはTask 8で整備した `make geo-download`→`make geo-import`→`make geo-verify` の手順と期待出力を記載する。固定値の変更有無を確認し記録する(変更が1つでもあれば「変更あり(前→後+理由)」と書く。全くなければ各行「変更なし」)。

```bash
git add docs/plans/M0/ws-4-report.md
git commit -m "docs: M0 ws-4の実行報告(完了条件8項目の証拠・G0証拠手順)"
```

- [ ] **Step 10: 最終返信**

報告ファイルのパスと完了条件8項目のPASS/FAIL一覧を返信する(条件2・3は「スーパーバイザー検証待ち」を含めてよい)。FAILが1つでもあれば、それも隠さず返信する。

---

## 実行後のセルフレビュー(実装者がTask 9のStep 4に入る前に一度だけ読む)

- design.md §5の8項目がすべて§6(完了条件)に検証コマンドつきで対応しているか
- design.md §4の8項目(正規化・ISJ読み取り・スキーマ・冪等・正転・逆転・CLI・arch)がすべて何れかのタスクの試験に対応しているか
- `normalize_name(name: str) -> str`・`FeatureRow(kind=, name=, pref_name=, city_name=, source_code=, lon=, lat=, attrs=)`・`BBox.parse(s)` / `.contains(lon, lat)`・`iter_isj_towns(path, city_codes=, bbox=)`・`iter_osm_pois(path, bbox=)`・`import_features(engine, clock, rows, source)`・`GeoService(engine).geocode_forward(name)` / `.reverse_geocode(lon, lat)`・`build_parser()` / `_effective_area(args, settings)` の各名前・引数がタスク間・テスト間で一致しているか
- マイグレーション0002の `down_revision` が `"0001"`・downgradeがDROP TABLEのみか。拡張のCREATE/DROPを0002に入れていないか。created_atにDB時刻DEFAULTを付けていないか
- 製品コードに `datetime.now` / `time.sleep` / `from time import` が入っていないか(arch testが自動検出するが、入れた瞬間にレッドになることを自覚しておく)
- geo配下に `requests` / `urllib` / `socket` / `httpx` / `aiohttp` / `http.client` のimportが入っていないか
- `backend/Dockerfile`・`compose.yaml`・`main.py`・`worker/`・`core/`・`0001`・`llm/`・conftest.py群・既存テストが無変更か。`settings.py`・`Makefile`・`backend/README.md` が追記のみか
- 依存追加が `geo` グループ(osmium)のみか(`uv.lock` のmain依存にosmiumが混入していないか)
- 実データのgeo-download/geo-import/geo-verifyを実行していないか(§0 — G0証拠はスーパーバイザー実施)
- 実装中にdesign.mdの固定値を変えた箇所があれば報告ファイルに書いたか
