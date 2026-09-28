# M1 ws-5(フロントエンド)実装報告

- 作成: 2026-09-28(agent3)
- 計画: docs/plans/M1/ws-5-plan.md / 設計: docs/plans/M1/ws-5-design.md
- 状態: **BLOCKED(仕様矛盾 — Task 2の途中で停止)**

## 停止の経緯と理由

### 発生した矛盾

Task 2(backend GET /v1/intents/expiry-options)の実装中、計画書内の2つの指示が
両立しないことを確認した。

1. 計画書Task 2・完了条件2は expiry-options ルートの追加を指示し、
   「expiry-options追加を含む」状態での `make test` 緑を要求する
2. 一方で計画書§3(禁止)は「既存テストファイル一切(修正禁止・無修正で緑である
   ことを確認するだけ)」と明文で定める

ws-4で追加された backend/tests/unit/test_rate_limit_wiring.py の
test_all_v1_routes_are_rate_limited は、保護対象パスの件数カウンタを
**「M1 ws-4時点の全契約」のスナップショットに固定**している(コメント明記)。
そのため expiry-options を追加すると、ルートが正しく api_rate_limited で
保護されていても(=差し替え漏れがなくても)、カウンタの期待値と実測が
1件分ずれて失敗する。

### 失敗の切り分け(実装ミスではないことの証明)

```
E       AssertionError: assert Counter({'/v1...}/resume': 1}) == {...}
E         Left contains 1 more item:
E         {'/v1/intents/expiry-options': 1}
```

- 「差し替え漏れ: /v1/intents/expiry-options」は発生していない
  (expiry_options は parse_router に追加したため、ルータ依存の
  api_rate_limited が効いている)
- 失敗は期待値Counterに新パス1件が多いことのみが原因

### 解決に必要な変更(スーパーバイザーの判断を仰ぐ)

backend/tests/unit/test_rate_limit_wiring.py の期待値Counterへ
`"/v1/intents/expiry-options": 1` の1行を追加する以外に解がない。
これは§3「既存テストファイル修正禁止」と§0.9「コミット前に make test 緑」の
どちらを優先すべきかの判断であり、実装者が独断で行うものではないため、
指示「仕様矛盾を発見したら実装を止めて報告せよ」に従い停止した。

## 現在の作業状態

### コミット済み

| コミット | 内容 |
|---|---|
| 21a4246 | Task 1: prototypeをfrontend/へ持ち上げnpm環境とvitest基盤を整える |

Task 1 は完遂している(`npm test` 3 passed・dev server配信確認済み)。
ただし計画書の雛形smoke試験コードに2点の環境起因の不備があり、
テストの意図を変えずに修正した(報告書の「知見」参照)。

### 未コミット(Task 2・ワークツリーに残置)

- `backend/src/latch/intents/routes.py` — expiry_options ルート実装済み
- `backend/tests/unit/intents/test_expiry_options_routes.py` — 4件緑
- `backend/tests/integration/test_expiry_options_api.py` — 作成済み(実行はスーパーバイザー)
- `docs/05-data-model-api.md` — §5表へexpiry-options行追記済み

上記の状態: `make lint` 緑・expiry-options unit試験4件緑・
`make test` は test_rate_limit_wiring 1件のみ失敗(上記の理由)。

§0.9「コミット前に make lint と make test が緑であること」を満たさないため
Task 2 のコミットは行っていない。

### 着手条件確認(§0.1)

- `git log --oneline -8` でws-4マージコミット(b4c4740)を確認済み
- `backend/src/latch/ratelimit/` に `__init__.py errors.py store.py limiter.py deps.py`
  の5ファイルの存在を確認済み

## Task 2 での実装上の補足(再開時の参考)

1. **expires_atの応答表記**: `now+72h` はUTCのままZ表記でシリアライズされるため、
   他3選択肢(+09:00)と揃えるべく routes.py 側で `at.astimezone(JST)` してから
   応答モデルへ渡している(completion.py は§3で触れられないため)
2. **Query の B008**: ruff が `Query(...)` の引数デフォルト呼び出しを弾くため、
   既存の StatusFilter と同じ Annotated + モジュールレベル定義(TimeStartParam)にした
3. **RED確認時の失敗形式**: ルート未定義の状態では404ではなく
   `GET /v1/intents/{intent_id}` へのフォールスルーによるAttributeError(500)になる
   (dependency解決がlifespan未載入の app.state.intent_service に触れるため)。
   機能不在による失敗でありTDDのREDとしては成立した

## 知見

- **happy-dom環境の import.meta.url**: vitest 3 + happy-dom では import.meta.url が
  http スキームへ書き換わるため、smoke試験の readFileSync(new URL(...)) が
  動かない。process.cwd() 基準のパス解決に修正した(テストの意図は不変)
- **paper-milk.png の参照元**: 計画書のsmoke試験は index.html 内の参照を期待するが、
  prototype実態では styles.css(CSS変数 --paper-texture)から参照される。
  styles.css を読んで検証するよう修正した(テストの意図は不変)

## 残作業(再開時)

Task 3〜12(フロントエンド本体・結合確認・報告完成)は未着手。
Task 2 は上記の1行追加許可後にコミットすれば完了する。
