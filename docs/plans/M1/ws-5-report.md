# M1 ws-5(フロントエンド)実装報告

- 作成: 2026-09-28(agent3)
- 計画: docs/plans/M1/ws-5-plan.md / 設計: docs/plans/M1/ws-5-design.md

## コミット一覧

| コミット | タスク | 内容 |
|---|---|---|
| 21a4246 | Task 1 | prototypeをfrontend/へ持ち上げnpm環境とvitest基盤を整える |
| 896b328 | (BLOCKED時) | 仕様矛盾の記録(後の裁定で解消・経緯は下記) |
| 6bb20bf | Task 2 | 有効期限選択肢API(expiry-options)を追加し05へ契約を追記 |
| e2f4266 | Task 3 | 表示文言とAPIボディ組立の純関数format.js |
| 200fc17 | Task 4 | フォーム状態と必須3フィールドの有効条件 |
| f47f94f | Task 5 | トークン管理とAPIクライアント(401リフレッシュ・envelope解釈) |
| 13aa380 | Task 6 | parse送信制御(debounce・同一text抑制・in-flight abort) |
| 0b5fddb | Task 7 | 条件リストの動的構成と催促・soft/NG行 |
| 54f7460 | Task 8 | タイプ別インラインエディタと条件の追加 |
| f406f17 | Task 9 | 期限選択肢の取得とselect構成(expiry-options経由) |
| 544c51c | Task 10 | active/draft保存とエラー表示マップ |
| db47e82 | Task 11 | 入力フローの本配線(parse・期限・催促・NG行・保存) |

## 実装サマリ

frontend/ を新設し prototype/ の資産から本実装へ持ち上げた(prototype/ は無変更)。
モジュール構成は design §2 のとおり:

- `src/ui/chrome.js` — テーマ・popover・トースト・モーダル(app.jsから分離。
  トースト文言とモーダルsummaryを引数化)
- `src/api/session.js` — トークン管理(access=sessionStorage+メモリ /
  refresh=localStorage)・exchangeIdpToken・ApiError
- `src/api/client.js` — request()(Authorization・envelope解釈・401→refresh→1回再送)
- `src/intent/format.js` — 表示文言・JST変換・APIボディ組立の純関数
- `src/intent/state.js` — フォーム状態・canSubmit/canDraft/missingRequired
- `src/intent/parseFlow.js` — debounce 1秒・同一text抑制・in-flight abort・retry
- `src/intent/conditions.js` — 条件リストDOM(催促・soft/NG行)とタイプ別エディタ
- `src/intent/expiry.js` — expiry-options取得・select構成(disabled・既定)
- `src/intent/save.js` — active/draft保存・エラー表示マップ・二重送信防止
- `src/main.js` — DOM配線(03 §3の入力フロー全体)

backend は routes.py へ GET /v1/intents/expiry-options を追加
(completion.py の expires_at_candidates・nearest_expires_at を呼ぶだけ・
新規計算ロジックなし)。05 §5「その他のエンドポイント」表へ1行追記した。

## 計画からの逸脱(裁定・修正の記録)

### スーパーバイザー裁定(2026-09-28)

Task 2 で既存 backend/tests/unit/test_rate_limit_wiring.py の契約カウンタ試験
(M1 ws-4時点の全契約スナップショット固定)が失敗する件について、期待値Counterへ
`"/v1/intents/expiry-options": 1` の1行追加が許可された(報告書896b328でBLOCKED報告
→裁定により解消)。試験の意図・他の期待値は不変。

### 実装上の修正(計画書コードの不備・いずれもテストの意図は不変)

1. **smoke試験のパス解決**: happy-dom環境は import.meta.url を http スキームへ
   書き換えるため readFileSync(new URL(...)) が動かない。process.cwd() 基準へ変更
2. **paper-milk.png の検証先**: index.html ではなく styles.css(CSS変数)から
   参照されるため、styles.css を読んで検証するよう変更
3. **vitest設定の分離**: worktree(パスが .herdr 隠しディレクトリ配下)で
   vite.config.mjs の test キーが自動検出されず、vitest.config.mjs へ分離
   (内容は計画書どおり environment: happy-dom / include: tests/**)
4. **parseFlow.retry()**: flush が pending をクリアした後の再送で、pending が空の
   まま送信されない計画書実装のバグを修正(pending = pending ?? lastText)
5. **NG行の注意文言の位置**: 計画書実装は .condition-value 内へ append するが
   計画書テストは値のみを期待する(相互矛盾)。テストを正として行直下へ配置
6. **attachAddCondition試験の入力値**: 計画書テストが「落ち着いた雰囲気」入力で
   「雰囲気を含まない」ことを検証する自己矛盾。入力を「落ち着いた席」へ変更
7. **expires_at応答のJST表記**: now+72h がUTCのままZ表記でシリアライズされるのを
   routes.py 側で astimezone(JST) に統一(completion.py は§3で触れられないため)
8. **Query の B008**: ruff が引数デフォルトの Query(...) を弾くため、既存
   StatusFilter と同じ Annotated + モジュールレベル定義(TimeStartParam)にした

## 検証結果

- frontend: `npm test` 81 passed(9ファイル: smoke・format・state・session・client・
  parseFlow・conditions・expiry・save)/ `npm run build` 成功(dist/client)
- backend: `make lint` 緑 / `make test` 467 passed(既存試験は契約カウンタの
  許可された1行追加のみ・それ以外無修正)
- 着手条件確認(§0.1): ws-4マージコミット(b4c4740)・ratelimit/ 5ファイルの
  存在を確認済み
- 完了条件5: `rg -n "console\.(log|info|debug)" frontend/src/` 空(ログ規律・確定値16)
- 完了条件6: `rg -n "天文館" frontend/src/ frontend/tests/` は
  format・state・conditions・save 各テストのdocs掲載例文由来のみに命中
- テストファイル名一意性: 重複なし

## 結合確認(実機・design §4.2)— 一部実施・残りはapi再ビルド後に実施

### 実施済み

- トークン取得(Task 12 Step 2): 内部CLI `python -m latch.auth issue-idp-token
  --provider google --subject ws5-manual-1` → POST /v1/auth/token が200・
  access-token-length=401 で取得成功
- 現行 latch-ci-api(本worktreeコードで未ビルド)の /v1/intents/expiry-options は
  422(request validation failed = /v1/intents/{intent_id} へのUUIDフォールスルー)。
  api再ビルド後に200が期待できる

### api再ビルド後の実施手順(スーパーバイザー用)

apiイメージ再ビルド(compose.yaml の build context は backend=worktreeソース)は
共有常設環境の操作となるため、実装者の権限では実施しない(権限システムにより
拒否確認済み)。次の手順で実施されたい:

```
docker compose build api && docker compose up -d --wait
# Step 3: expiry-options実応答(トークンは上記CLI+POST /v1/auth/tokenで取得)
curl -sf "http://127.0.0.1:8000/v1/intents/expiry-options" -H "Authorization: Bearer $ACCESS" | python3 -m json.tool
curl -sf "http://127.0.0.1:8000/v1/intents/expiry-options?time_start=2026-09-27T23:00:00%2B09:00" -H "Authorization: Bearer $ACCESS" | python3 -c "import json,sys; print(json.load(sys.stdin)['default_index'])"
# Step 4: parse・保存(active 201・draft 201・GEOCODING_FAILED 422)— 計画書Task 12 Step 4のコマンドどおり
# Step 5: DB確認 — docker compose exec db psql -U latch -d latch -c "SELECT status, expires_at IS NOT NULL AS has_expiry FROM intents ORDER BY created_at DESC LIMIT 3;"
# Step 6: vite proxy — cd frontend && npm run dev 後に curl -sf -X POST http://127.0.0.1:5173/v1/intents/parse ...(計画書Step 6どおり)
```

vite dev server の配信と /src/main.js の配信(index.html・本配線コード)は
worktree内で確認済み(tokenPanel・createParseFlow の配信を検証)。

## test-ci=スーパーバイザー検証待ち

- backend/tests/integration/test_expiry_options_api.py 3件(401必須・応答形状・
  既定選択)。実行手順: `make test-ci`(STATUS運用ルール1〜3により実装者は未実行)

## UI画面確認手順(スーパーバイザー用・告白1によりE2Eなし)

1. api再ビルド後、repo root で `make up` → `cd frontend && npm run dev` →
   http://localhost:5173/
2. トークン発行(README記載のCLI)→ 開発用トークンパネルへ貼付 → 接続する
   (パネルが閉じて入力可能になる)
3. 例文「今日20時以降、天文館で2〜4人くらいなら軽く飲みたい。予算は5000円くらい。
   会社関係の人は避けたい。」を入力 → 1秒後に条件リストが再構成される
4. 行編集(鉛筆→入力→Enter/✓・Escape取消)・催促表示(必須3欠落行は赤字+注記)
5. NG行に注意文言が付くこと・期限の既定選択(入力後 select が有効化)・
   モーダルのsummary(期限label・公開設定)
6. 下書き保存(トースト「下書きを保存しました」)・預ける(確認モーダル→
   Intentを確認する→201で同一画面に留まる)
7. 場所を存在しない地名へ編集して預ける → モーダルが閉じ場所行へエラー表示
8. topbar「Intent 3件」はプレースホルダのまま(M3-10スコープ外)

## 文言対応表(告白5・本実装が固定した文言)

| 箇所 | 文言 | 出典・備考 |
|---|---|---|
| 催促注記(requiredNote) | 時間・場所・目的を指定すると預けられます | 03 §3・計画書Task 7 |
| NG行注意書き(ng-note) | この条件は確実には除外できません。参考条件として扱います | 02 D-04・schema.py WARNING_MESSAGE_NG_DOWNGRADED と同一 |
| 期限ラベル(初期) | 有効期限(入力後に選べます) | 選択肢はAPI応答から構成するため。取得成功後「有効期限」へ戻す |
| 初期テキスト | (空・カウンタ 0 / 300) | prototypeはサンプル文言入り。API接続後は空スタートが03 §3のフロー |
| トークンパネル | 「開発用トークンパネル」+説明文+「接続する」 | design §2.3・手順はREADMEへ |
| トークンエラー(401) | トークンが無効です。再発行して貼り直してください。 | |
| トークンエラー(その他) | 接続できませんでした。APIの起動を確認してください。 | |
| parse 429 | 操作が集中しています。少し時間をおいてから入力し直してください。 | 再試行ボタンなし(1分待機) |
| parse 503 | ただいま条件を読み取れません。+「もう一度読み取る」ボタン | 07 D-17 |
| parse その他 | 通信エラーが発生しました。+「もう一度読み取る」ボタン | |
| 保存 GEOCODING_FAILED | 場所が見つかりません。条件リストの場所を修正してください。(場所行へ) | design §2.9 |
| 保存 UNDER_AGE | 飲酒を含むIntentは20歳以上の方のみ作成できます。 | |
| 保存 ACTIVE_INTENT_LIMIT | 預けられるIntentはActive 5件までです。停止中・期限切れのIntentを確認してください。 | ws-4 |
| 保存 RATE_LIMITED | 操作が集中しています。少し時間をおいてもう一度お試しください。 | ws-4 |
| 保存 503系 | ただいま混み合っています。もう一度お試しください。 | |
| 保存 VALIDATION_ERROR(時刻) | 時刻を修正してください(現在〜7日以内)。(時間行へ) | message先頭一致のみ |
| 保存 VALIDATION_ERROR(その他) | 入力内容を確認してください。 | |
| 保存 既定 | 保存できませんでした。もう一度お試しください。 | |
| 下書きトースト | 下書きを保存しました | プロトタイプ固定 |

## 05改版実施済み(後日ユーザー確認待ち)

05-data-model-api.md §5「その他のエンドポイント」表へ GET /v1/intents/expiry-options
の1行を追記した(運用ル則7。design §5告白2)。v0.4表記・契約は実装どおり。

## 知見・残余リスク

- **happy-dom と import.meta.url**: vitest 3 + happy-dom では http スキームへ
  書き換わる。ファイル読み取り試験は process.cwd() 基準が安全
- **worktree パスと vitest 設定**: .herdr 隠しディレクトリ配下のworktreeで
  vite.config.mjs の test キーが自動検出されなかった。vitest.config.mjs 分離で解決
- **契約カウンタ試験の運用**: ルート追加時に期待値Counterの更新が必要
  (スーパーバイザー裁定により追加)。次回以降のワークストリームでも同様
- **FastAPI の 404 と 422**: 未定義パスが /{param} ルートへフォールスルーすると
  パラメータ変換失敗の422になる。ルート有無の確認には注意
- **残余リスク**: 実機結合(Step 3〜6)がapi再ビルド待ちのため、expiry-optionsの
  実応答・vite proxy経由の通信は未検証(unit試験・実装レビューでは網羅)。
  また UI 画面操作は手順書のみ(告白1)。api再ビルド後に Step 3〜6 と画面確認を
  行えば完了条件7が満たされる
