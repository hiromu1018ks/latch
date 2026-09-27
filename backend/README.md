# LATCH backend

FastAPIバックエンド(uv・srcレイアウト・Python 3.13)。仕様は `../docs/` を参照。

## クイックスタート

```bash
mise install            # .mise.toml の uv を導入(初回のみ)
make setup              # uv sync(backend/.venv 構築)
make lint && make test  # 毎コミットの規律
make up && make ps      # ci常設環境(db/redis/api/worker)
make test-ci            # unit+integration
```

## マイグレーション(ws-1)

- `make migrate` — ci常設DBへマイグレーションを適用(`alembic upgrade head`)。**明示実行のみ**(API/Workerの起動時自動実行はしない)
- 接続先は `LATCH_DATABASE_URL`(デフォルト `postgresql+asyncpg://latch:latch@127.0.0.1:5432/latch`)
- 現行版の確認: `cd backend && uv run alembic current`
- 新しいマイグレーション追加: `cd backend && uv run alembic revision -m "説明"`(0001_initial_schema は手作業で作成済み)

## 規律

- 製品コード(`src/latch/`)で実時間を直接参照しない — すべて `latch.core.clock` 経由(C2)。
  `tests/unit/test_arch_no_direct_time.py` が毎コミットで強制する
- `python`/`pip` を直接使わない。`uv run` 経由
- 新しい依存は計画書のスコープ確認を経て追加する(現状: fastapi/uvicorn/pydantic-settings のみ)

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
