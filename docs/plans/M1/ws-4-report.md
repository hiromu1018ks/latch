# M1 ws-4(レート制限)実装報告

- 作業単位: ws-4 レート制限(08 §5.4・04 §5)
- 実装: agent3(worktree: m1-ws-4)
- 日付: 2026-09-28
- 計画: docs/plans/M1/ws-4-plan.md / 設計: docs/plans/M1/ws-4-design.md

## コミット一覧

```
8fde35e test(ratelimit): 429/422のci環境統合試験9件を追加(M1 ws-4 Task 6)
5790abf feat(auth): token/refreshへprovider+subject単位の429フックを追加(M1 ws-4 Task 5)
618341c feat(ratelimit): API全体60req/分の依存差し替えとlifespan接続(M1 ws-4 Task 4)
1e6bbb1 feat(intents): Active数422と作成/更新429のフックを追加(M1 ws-4 Task 3)
5c0fad0 feat(ratelimit): RateLimiter上限判定とJSTバケット導入・Settings上限4種(M1 ws-4 Task 2)
bf9ac11 feat(ratelimit): Redisストアとエラー階層を追加(M1 ws-4 Task 1)
```

## 実装サマリ

- ratelimit/パッケージ(errors・store・limiter・deps): `RateLimitStore` がRedisのINCR+EXPIRE(pipeline併発)を `rl:` 接頭辞の固定窓バケットキー(api=分・create=日・update=時・auth=分。TTLは掃除用のみ)へ閉じ込め、`RateLimiter` がClock由来のJSTバケット(日付は `clock.jst_date()`・時分は `now().astimezone(JST)`)でINCR後の値を判定する(INCR先行 — 429を返したリクエスト・422で失敗した作成もカウント済み)。Redis例外は `RateLimitDependencyError`(503)へ包む(fail-closed)。`deps.py` の `api_rate_limited` は `require_authenticated` を内包し401→429の順を構造で保証する。
- intentsフック(Active数422・作成/更新429): `IntentService` へlimiterをOptional注入(既定None=無効)。作成はuser解決直後にINCR(429)、active時はuowトランザクション内の先頭へ検証一式を移動 — users行FOR UPDATE → count_active(期限切れ行除外) → 422 ACTIVE_INTENT_LIMIT → 必須3 → 時刻 → 年齢 → ジオコーディング → insertの順(design §2.3・§2.4の両立)。更新はuser解決直後のINCR(429が404より先)。resumeは更新カウント+Active検証、pause/deleteは対象外。422は既存IntentsErrorハンドラがそのままenvelope化するため新規ハンドラ不要。
- API全体60req/分(依存差し替え・lifespan): users・intents(parse・crud)・auth(logout)の各ルータ依存を `api_rate_limited` へ差し替え。claimsにuser_idは無いため(C3)`app.state.user_lookup` で解決し、未登録JWT保持者はフォールバックキー `rl:api:anon-{sha256(provider:subject)}` で計上(parse連打の源流抑制)。401リクエストはINCRしない。lifespanはredis_client常時生成・`app.state.rate_limiter`/`user_lookup` 構築・`create_app(rate_limiter=)` テスト注入。app.state未載荷/Noneは無効(unit試験のASGITransportはlifespan非実行のため既存試験は無修正)。
- auth系429(provider+subject): `AuthService` へlimiterをOptional注入。tokenはIdP検証の直後・refreshはrotate(検証を兼ねる)の直後にINCRする(401 INVALID_IDP_TOKEN優先を保持)。subjectはsha256でハッシュ化してキーへ埋める(PII不混入)。

## 検証結果

- make lint: クリーン(ruff format --check + ruff check)
- make test(unit): **457 passed**(開始時426件から+31)
  - 新規: test_store 6 / test_limiter 9 / test_jst_boundary 4 /
    test_intents_service追記 11 / test_rate_limit_wiring 1 /
    auth unit追記 6
  - 既存: 無修正で緑(ws-3 CRUD・auth・users・parse・arch test 2件)
- test-ci: **スーパーバイザー検証待ち**(STATUS運用ルール1・4)
  - 検証手順: `docker compose build api` を先行のうえ `make test-ci`
  - 期待: test_ratelimit_api.py 9件緑 + 既存integration回帰
    (test_intents_crud_api・test_auth_api・test_users_api・test_intents_parse_api)
- ファイル名一意性: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` → 空
- マイグレーション: 差分なし(alembic/versions 変更ゼロ)
- arch test: test_arch_no_direct_time.py 緑(ratelimit配下もClock経由のみ)

## 既存試験の保全検算(api 60req/分)

計画Task 6の検算表のとおり(実測と食い違いなし):

| 試験ファイル | 同一ユーザー最大req/分(api) | 作成/日 | 更新/時 | 同時Active |
|---|---|---|---|---|
| test_intents_crud_api(10件) | 8(test_4・test_9) | 4(test_9) | 2(test_3) | 3(test_9) |
| test_users_api | 3程度 | — | — | — |
| test_intents_parse_api | 3程度(token交換+parse) | — | — | — |
| test_auth_api | token/refresh各4回まで(subjectユニーク・auth系別カウンタ) | — | — | — |

いずれも上限(60・20・6・5)未満。unitでは `app.state.rate_limiter` が未載荷(無効経路)のため既存試験は影響なし(457 passedで実証済み)。

## 知見・残余リスク

- **429透過のためのexcept節修正(計画書の欠落)**: `IntentService.create/update/_transition` と `AuthService.token/refresh` は「例外を503へ包む」構造のため、計画書のコード断片どおりでは `RateLimitedError` が503 DEPENDENCY_UNAVAILABLEへ置き換わってしまう(実装中にREDで発見)。各except節を `except (IntentsError, RateLimitedError): raise` / `except (AuthError, RateLimitedError): raise` へ拡張した。design §2.4(401→429→422の順)を満たす最小変更。`latch.ratelimit.errors` の実行時importを伴うが、依存方向はintents/auth→ratelimitの一方向でパッケージ境界(design §2.1)は保持。
- **FastAPI 0.141.1と計画書コードのずれ(2点)**: (1) `include_router` が `_IncludedRouter`(遅延評価)で載るためフラットな `app.routes` からルータのルートが見えない — wiring試験は `original_router.routes`(ルータ依存のマージ済み)を展開する形へ修正。(2) `Depends` の対象フィールドが `.call` から `.dependency` へ変わっている — 同試験で `.dependency` を参照。試験の意図(全v1ルートの依存差し替え網羅・対象外3経路の確認)は不変。
- **import初期化順の循環を切断**: `ratelimit.deps → auth.deps` と `auth.routes → ratelimit.deps` が `latch.auth.__init__` のルータ再export経由で環を作り、`from latch.ratelimit.deps import ...` を最初にimportする経路でImportErrorとなった。`latch/auth/__init__.py` からroutesの再export(logout_router・public_router)を外して解消。属性経由の利用者は存在しない(rg確認済み。main.pyは `latch.auth.routes` から直接import)。auth/__init__.pyは計画書§2.2にないが禁止リストにもなく、計画が要求するimport方向を実装する最小の切断として判断(報告にて記録)。
- **anonフォールバックキー**: `rl:api:anon-{sha256(provider:subject)}`(design §2.7への実装補完 — 計画書補遺1)。anon-接頭辞はUUID形式と文字長が異なるためuser_id空間と衝突しない。
- **refresh 429時のトークン喪失**: rotate後にINCRするため、429を返すrefreshでは回転後の新refresh_tokenが応答に含まれず失われる(旧トークン再提示は族失効→401)。design §2.5の承認済み帰結(access TTL 1時間・正当利用で1分61回のrefreshは発生しないため受容)。
- **ruff format/lint微修正**: 計画書コード断片中のimport順(I001)・未使用ループ変数(B007)・docstring長(E501)をruff準拠へ修正(意味変更なし)。test_jst_boundary.py の `pytest.raises(Exception)` はruff B017により `pytest.raises(RateLimitedError)` へ(より厳密な断定。試験の本体はバケットキー切替の検証)。
- 検証手順メモ: 上限値は `LATCH_RATE_LIMIT_API_PER_MIN` 等の環境変数で上書き可能(compose変更不要)。Redisキー接頭辞は `rl:`(auth:と分離)。429/422のログはcodeのみ(`ratelimit.error code=...`・`intents.error code=...`)でtext・subject・トークンは含まない(08 §2.4)。
