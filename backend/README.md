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

## 認証(ws-3)

### テストユーザーのトークン発行(10 第1節のテスト用認証構成)

```bash
# テストユーザーのIdPトークン(JWT)を発行(同梱テスト鍵 kid=test-idp-1 で署名)
cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject demo

# ci環境のapiでアクセストークンへ交換(M0はUser行が無いため user.id=null・profile_complete=false が正当動作)
curl -s -X POST http://127.0.0.1:8000/v1/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"provider":"google","idp_token":"<発行したトークン>"}'
# → {"access_token":"…","token_type":"Bearer","expires_in":3600,"refresh_token":"…",
#    "user":{"id":null,"profile_complete":false}}

# リフレッシュ(回転式: 応答のrefresh_tokenで再度呼べる。旧トークンの再利用は族失効+401)
curl -s -X POST http://127.0.0.1:8000/v1/auth/refresh \
  -H 'Content-Type: application/json' -d '{"refresh_token":"…"}'

# ログアウト(AuthorizationのJWTをRedis失効リストへ+リフレッシュ族を失効。204)
curl -s -i -X POST http://127.0.0.1:8000/v1/auth/logout \
  -H 'Authorization: Bearer <access_token>'
```

### 鍵管理(design §2.5)

- ci/試験用のIdP鍵ペアは `src/latch/auth/testkeys/` にコミット(kid=test-idp-1)。
  prodでは `build_auth_service` の起動ガードがテスト既定値の残存をValueErrorで拒否する
- staging専用ペアの生成と注入: `uv run python -m latch.auth gen-keypair --out-dir <dir>` →
  生成された `idp_test_jwks.json` をHTTP(S)で配信し、そのURLを
  `LATCH_AUTH_IDP_JWKS_URL_GOOGLE` / `_APPLE` へ設定(検証側PyJWKClientは
  http/httpsのみ受け付ける — file:// は拒否される)。
  発行時は `--private-key <dir>/idp_test_private.pem` で指定
- prodは `LATCH_AUTH_ACCESS_SECRET`(HS256 secret)・`LATCH_AUTH_IDP_JWKS_URL_*`・
  `LATCH_AUTH_IDP_ISSUER_*`・`LATCH_AUTH_IDP_AUDIENCE_*` の実値が必須(未設定・テスト値は起動失敗)
- アクセストークンを直接発行するツールは存在しない — 常に POST /v1/auth/token 経由で取得する

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
