# M1 ws-3(intents CRUD・draft→active・保存時検証)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** M1スコープ4〜5(12 M1-4・M1-5)を実装する — POST /v1/intents(active/draft)・GET一覧/取得・PATCH(全置換・draft→active遷移)・DELETE・pause/resume の7エンドポイントと、保存経路特有の3処理(ジオコーディング正転の保存組み込み・alcohol_involvedのサーバ側確定・時刻検証=過去不可/現在+7日上限/active時のみ)を通す。active作成確定・active化・active更新・resume の各タイミングでmatch_eventsへEventを発行する(§2.2表)。

**Architecture:** `latch.intents` パッケージを責務別ファイルで拡張する(design §2.1A) — `intent_input.py`(Pydantic入力検証)・`mapping.py`(入力→保存列変換・応答再構成)・`events.py`(outbox INSERT)・`store.py`(text()生SQL一式)の新規4ファイルと、`service.py` への IntentService 追記・`routes.py` へのCRUDルータ追記・`errors.py` への例外追記。match_eventsをoutboxとしてIntent保存と同一トランザクションでINSERT(status='pending'。Pub/SubリレーはM2-1)。検証は「静的=Pydantic / 動的(時刻・年齢・地物)=サービス層」に分離(design §2.3)。ORM・リポジトリ層は導入しない(ws-1流儀のtext()生SQL継続・design §2.9)。**マイグレーション追加なし**(alembic 0001で全列・索引が既存)。既存コードへの変更は `main.py` と `intents/__init__.py` のみ。

**Tech Stack:** 既存のみ(Python 3.13 / FastAPI / pydantic v2 / SQLAlchemy[asyncio]+asyncpg / pytest(asyncio_mode=auto))。**依存追加なし・マイグレーション追加なし・設定追加なし**。

**Spec:** `docs/plans/M1/ws-3-design.md`(agent1設計メモ・確定値1〜35は本計画に反映済み。design §6の確認事項は設計推奨どおりに確定済み — §0.5参照)

---

## 0. 前提と規律(実装着手前に必ず読む)

1. **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m1-ws-3`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する。
2. **コミット規律**: 各タスク末尾のコミットステップを必ず実行する(タスク単位でworktreeブランチ `m1-ws-3` へコミット)。コミットメッセージは各タスクに指定したもの(Convention Commits形式)を使う。コミット前に `make lint` と `make test` がグリーンであることを確認する(12 第8節 運用ルール5)。`ruff format --check` の整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。**worktreeブランチ外へのコミット・mainの変更は一切行わない**。
3. **【最重要・compose常設環境へ触れない】**: 共有ci-db(compose常設)はスーパーバイザーの直列検証が使う。`make test-ci` が呼ぶ `docker compose up -d --wait` は **apiイメージを再ビルドしない**(compose.yamlのapiはbuild型・ソースマウントなし)。コード変更後のintegration試験には先に `docker compose build api` が必要であり、apiイメージの再ビルド・コンテナ再作成は検証環境に影響する。よって本単位の実装中は **`make test-ci` / `make up` / `make down` / `make migrate` / `docker compose …` / `docker build` を一切実行しない**。**開発は `make lint`・`make test`(unit)で完結させる** — unit試験はFakeClock+スタブstore+スタブgeocoderで外部プロセス不要(design §4.1)。integration試験ファイルは作成するが**実行しない**(報告ファイルに「**test-ci=スーパーバイザー検証待ち**」と記録する)。`make test` はintegrationファイルの収集(import)まで行うため、構文・importの正当性はunit実行で検証される。
4. **テストファイルのbasename一意(STATUS運用ルール5)**: `backend/tests` は `__init__.py` なし(pytest prepend import mode)のため、テストファイルのbasenameはtests配下全体で一意にする。M1 ws-2マージ時に `users/test_service.py` と `intents/test_service.py` の衝突が実際に発生した。本計画の新規テストファイル名は§3.1の一覧(既存 `unit/intents/test_service.py`=parse専用と衝突しない `test_intents_service.py` 等)に固定されており、**勝手に改名・複製しない**。コミット前に `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空であることを確認する。
5. **design §6の確認事項は確定済み**: §6-1(structured_dataへのlocation_nameキー追加)・§6-2(G1判定の#4扱い)はスーパーバイザーが処理中であり、いずれも設計推奨どおりで進行する。§6-3(cancelled遷移+deleted Event)・§6-4(event_type文字列=created/updated/deleted/expired/scheduled)・§6-5(PATCH契約の未規定部分)・§6-6(応答intentオブジェクトの形状)も設計どおり本計画へ落とし済み。**未解決論点として扱わないこと**。
6. **開始時の健全確認**: 作業開始時に `make lint && make test` を実行する。**本計画作成時点(2026-09-27)のmainは lintクリーン・unit 302 passed(既知の既存失敗なし)**。それ以外の失敗があった場合は着手せず、報告ファイルに状況を記録して作業を中止する。
7. **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由(システムPythonと衝突させないため)。
8. **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。
9. **本計画のコードは実機検証済み(2026-09-28)**: 計画作成時に、本計画に記載のコード(intents/ 新規4ファイル+3ファイル追記・unit/intents 新規6ファイル+test_errors.py追記・integration 1ファイル・main.py・__init__.py)をmain作業コピー上に一時的に組み立て、`make lint`(クリーン)・`make test`(416 passed = 既存302+新規114・除外なし)・arch test(時刻参照 core/clock.py のみ)・テストbasename一意(重複なし)を確認してから全差分を破棄してある。コードは記載のとおり使えば動く(改行位置の整形差分は `ruff format` が吸収する)。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| POST /v1/intents契約: request `{"raw_text", "status", "structured_intent"}` → 201 `{"intent": {"id", "status", "version", "expires_at", …}}`。statusはactive(既定・省略可)またはdraft | 05 §5 POST /v1/intents |
| active作成: 確認フロー経由のデータのみ受理・作成確定の直後にMatch Event発行 | 05 §5・06 §9 |
| draft作成: raw_textのみ必須(最大300字)・structured_intent部分可・検証はraw_text必須に限定(形式不正のみ弾く)・Embedding/Eventなし・visibility既定値はdraftでも格納 | 05 §5・06 §9-0・07 §1 |
| expires_at: クライアントは選択値の絶対時刻を送る。nullなら「time_start+3時間に最も近い選択肢」を補完。draftでは補完せずNULL | 05 §5・03 §3 |
| locationは`{"name", "radius_m"}`のみで座標を受けない。保存処理内で正転ジオコーディングしgeo_center確定。該当地物なし=422 GEOCODING_FAILED。検証はactiveの作成・更新のみ適用(draftのgeo_centerはNULL可) | 05 §5・04 §3 |
| structured_intentは全置換。raw_textも同時に送り直す。PATCHとPOSTは同一契約 | 05 §5 |
| 時刻検証: time_start過去・現在+7日超は422。expires_atも同様。draftは形式検証(ISO 8601)のみ | 05 §5 |
| その他422: 必須3欠落(category/time.start/location・activeのみ)・alcohol_involved=trueかつ20歳未満(422 UNDER_AGE)。visibility欠落時は既定値格納で422対象外 | 05 §5・03 D-19・08 D-10 |
| alcohol_involved確定: category_primary=drinkingならサーバ側でtrue確定(クライアント修正値より優先)。他カテゴリはリクエスト値 | 05 §5・07 §2規則7 |
| 07出力由来の値を05 §2構造へ格納(ng_unverifiableはdowngraded_from_ng:trueのsoft_constraintsへ) | 05 §5・§2・07 §2 |
| draft→active遷移(PATCH): active作成と同一の全検証通過で受理・不通なら422でdraft据え置き。受理時に初回Match Event発行。実質変更なきactive化はversion据え置き。active→draft逆遷移不可 | 05 §5・§6・06 §9-0 |
| draft中のPATCHはraw_text必須以外の検証・ジオコーディング・Embedding・Event発行を行わない | 05 §5・06 §9-0 |
| PATCH(全置換): version+1・Event発行・検証はPOSTと同一。alcohol=trueとなる変更は年齢検査 | 05 §5 |
| draft→active初回投入Eventは作成種(create)。resumeはversion+1の更新種Event | 06 §9-0・05 §6 |
| match_events: UNIQUE(event_type, source_intent_id, payload->>'version')=冪等キー。statusはpending/processed/quarantined | 05 §2・01 §5・06 §9 |
| Intent遷移表: (作成)→active/draft・draft→active/expired/cancelled・active→paused・paused→active・active・paused→cancelled/expired・active→matched | 05 §6 |
| GET /v1/intents一覧: statusフィルタ可・ページネーション共通規定・既定ソートcreated_at降順 | 05 §5 |
| DELETE: 01 §21の削除範囲。遷移表上はdraft・active・paused→cancelled | 05 §5・§6・01 §21 |
| ページネーション共通規定: `?cursor=<opaque>&limit=<1〜100>`(limit既定20・超過は422)。応答は`{"items": [...], "next_cursor": ...}`(次がなければnull) | 05 §5 |
| エラーcode: 400 MALFORMED_REQUEST・401 UNAUTHENTICATED・403 FORBIDDEN・404 NOT_FOUND・422 VALIDATION_ERROR・422 UNDER_AGE・422 GEOCODING_FAILED・503 DEPENDENCY_UNAVAILABLE | 05 §5エラー形式表 |
| 422 ACTIVE_INTENT_LIMIT・429 RATE_LIMITED はws-4(本単位では実装しない) | 12 M1-6・08 §5.4 |
| 時刻参照はすべてClock経由(now()=tz-aware UTC・JST暦日付はjst_date())。arch testが毎コミットで強制 | 04 §5 FR-41・12 C2 / core/clock.py |
| ログ・例外にraw_text・地名・条件文言を含めない(許可リスト方式。例外メッセージはIDとエラーコードで表現) | 08 §2.4・01 §21 |
| 満年齢は(月,日)タプル比較(誕生日当日に加算) — ws-1実装 `users/service.age_years` を再利用 | ws-1実装・08 D-10 |
| 正転資産: `GeoService.geocode_forward(name) -> Geofeature | None`(決定的順位で1件・該当なし=None) | geo/service.py(M0 ws-4) |
| D-19補完の単一実装(本単位が消費): default_time_end(+3h)・DEFAULT_RADIUS_M=1000・DEFAULT_PARTICIPANTS=(2,2)・nearest_expires_at(time_start, now) | intents/completion.py・07 §2 |
| 保存APIは同期LLM非依存(DB書き込み+Event発行に絞る) | 12 C8・04 §7 |
| intents表・match_events表・Index一式・idempotency式UNIQUE索引はalembic 0001で作成済み(欠落なし) | alembic 0001・05 §2〜§3 |
| 全API認証済みのみ。参照・更新・削除は所有者本人のみサーバ側で強制。`require_authenticated`(C3の強制点) | 05 §5冒頭・01 §22 / auth/deps.py |
| structured_dataは5キー+**location_name(第6キー・design §6-1推奨どおり確定。JSONBのためマイグレーション不要)** | 05 §2・design §6-1 |
| `make test-ci` はapiイメージを再ビルドしない(検証時に `docker compose build api` が前提) | Makefile / compose.yaml / STATUS運用ルール4 |
| integration試験はgeofeaturesをfixtureでフルリロードする(実行後に実データは `make geo-import` で復旧) | STATUS運用メモ・tests/integration/test_geo.py |

## 2. グローバル制約(全タスクに暗黙に適用)

- **依存追加なし**(`backend/pyproject.toml`・`backend/uv.lock` 無変更)。**マイグレーション追加なし**(`backend/alembic/` 無変更)。**設定追加なし**(`backend/src/latch/settings.py` 無変更)
- **geo/・auth/・core/・llm/・worker/・users/ は無変更**。例外はimportのみ: `users/service.age_years`(満年齢の単一実装)・`geo/service.Geofeature`(Protocol戻り値型。design §2.5「型参照のためのlatch.geo importはservice.pyとファクトリに限る」)
- `completion.py`・`schema.py`・`prompt.py`・既存の `IntentParseService`・`parse_router` は**消費のみ・無変更**(design §1.3)
- 永続化は **`sqlalchemy.text()` 生SQLのみ**(design §2.9)。ORMモデル・リポジトリクラス・Tableメタデータを作らない
- **idはDBのDEFAULT gen_random_uuid()に任せ `RETURNING id` で受け取る**。**created_at/updated_at は `clock.now()` の明示値**(DB時刻関数のDEFAULTを使わない)。uuid列の戻り値はUUIDインスタンス正規化を通す(asyncpg実績 — users/service._coerce_user_idと同型)
- Event発行は **Intent保存と同一トランザクション**(design §2.2A)。payloadは`{"version": N}`のみ・status='pending'・created_atはClock由来明示値
- 検証順序は **形式(Pydantic)→ 必須3 → 時刻 → 年齢 → ジオコーディング → 保存** に固定(design §2.5)。422の発生元が一意に定まるよう、各検証はこの順で行う
- 競合制御は **`SELECT … FOR UPDATE` → 検証 → 条件付きUPDATE(WHEREにidと現status)** の同一トランザクション(design §2.8・C9先行適用)
- エラー応答は共通envelope `{"error": {"code", "message", "details": null}}`(main.py既存ハンドラ。ハンドラ追加なし)。JSON形式不正=400・値域外/必須欠落=422(既存RequestValidationErrorハンドラ)
- 製品コード(`backend/src/latch/`)で `datetime.now` / `datetime.utcnow` / `date.today` / `time.time` / `time.monotonic` の使用禁止(例外は `core/clock.py` のみ — arch test `test_arch_no_direct_time.py` が自動強制)
- ログ(ロガー `latch.intents`)はイベント名と結果/コードのみ。**raw_text・location.name・条件文言・soft/ng要素を出さない**(08 §2.4)。例外メッセージも固定文言のみ
- `backend/tests/unit/intents/`・`backend/tests/integration/` に `conftest.py`・`__init__.py` は作らない(design §3.1の一覧にない。必要なfixtureは各テストファイル内に定義 — 既存慣例)
- main.pyへの変更は§3.2に列挙した3点のみ。**既存の `/health`・auth/usersルータinclude・各エラーハンドラは変更しない**(IntentsErrorハンドラはws-2が置いた1個のまま)
- ジオコーディングは保存トランザクションの**前**(検証の最後)に実行し、結果の座標を保存列へ渡す。`ST_SetSRID(ST_MakePoint(:lon,:lat),4326)::geography` で格納(design §2.5)

## 3. スコープ(作成・変更するファイル)

### 3.1 作成

```text
backend/src/latch/intents/
├── intent_input.py    # 05 §5保存API契約の入力検証モデル(parseのParserOutputとは別契約)
├── mapping.py         # 入力→保存列変換(alcohol確定・補完消費・draft正規化)・応答再構成
├── events.py          # MatchEvent発行(outbox INSERT・design §2.2)
└── store.py           # 永続化(text()生SQL一式・design §2.10)
backend/tests/unit/intents/
├── test_intent_input.py    # モデル受入・拒否(§4.1-1)
├── test_mapping.py         # 変換(§4.1-2)
├── test_events.py          # event_type定数・insertパラメータ(§4.1-3)
├── test_intents_service.py # ユースケース網羅(§4.1-4・※basename一意のためこの命名)
├── test_crud_routes.py     # ルーティング(§4.1-5)
└── test_time_validation.py # 時刻検証の境界(§4.1-6)
backend/tests/integration/
└── test_intents_crud_api.py  # 実HTTP・実DB・実ジオコーディング(§4.2・作成のみで実行しない)
docs/plans/M1/ws-3-report.md  # 報告ファイル(§8参照)
```

### 3.2 変更(既存ファイル)

- `backend/src/latch/intents/errors.py` — 例外6種の追記(§7 Task 1)
- `backend/src/latch/intents/service.py` — SupportsForwardGeocoding(Protocol)・IntentService・cursor関数・make_intent_service の追記(parse資産は無変更)
- `backend/src/latch/intents/routes.py` — intents_crud_router(7エンドポイント)・入出力モデルの追記(parse_routerは無変更)
- `backend/src/latch/intents/__init__.py` — 新規公開IFの再export追記
- `backend/src/latch/main.py` — (1) intents_crud_routerのinclude、(2) lifespanへ `intent_service` 構築追加、(3) `create_app` へ `intent_service` テスト注入引数を追加。**これらが既存コードへの全変更**

## 4. 禁止(触れてはいけないもの・スコープ外判断基準)

### 4.1 触れてはいけないファイル

- `backend/src/latch/intents/schema.py`・`prompt.py`・`completion.py`(parse専用資産・消費のみ)
- `backend/src/latch/geo/`(GeoService無変更)・`auth/`・`core/`・`llm/`・`worker/`・`users/`(importのみ)
- `backend/alembic/`(マイグレーション追加なし)・`backend/src/latch/settings.py`・`backend/pyproject.toml`・`backend/uv.lock`
- `compose.yaml`・`Makefile`・`backend/Dockerfile`・`.mise.toml`
- `docs/01〜12`・`docs/learn/`(agent4運用中)・`docs/reviews/`・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/M0/`・`docs/plans/M1/ws-3-design.md`(入力文書)
- `prototype/`・`README.md`・`backend/README.md`・`.claude/`・`.agents/`・`.hermes/`
- 既存テストファイル(tests/unit/auth・users・llm・geo・test_*.py・tests/integration/test_*.py 等)への変更。**例外**: `backend/tests/unit/intents/test_errors.py` への追記のみ認められる(§7 Task 1)

### 4.2 スコープ外と判断する基準(実装中に迷ったら作らない)

- Active 5件上限(422 ACTIVE_INTENT_LIMIT)・作成20件/日・更新6回/時・API 60req/分(429 RATE_LIMITED)— ws-4(12 M1-6)
- expiry_sweeper・期限切れバッチ・catch-upスキャン(draft→expired・active→expired)— M3-3(12 M3-3)
- Pub/Subパブリッシュ・Worker(debounce・version検査・再試行→quarantined)・Embedding Worker・Layer 1〜5 — M2(12 M2-1〜8)。本単位のEvent発行はmatch_eventsへのINSERTまで
- 削除範囲の完全実装(match_candidates・group_candidates削除・30日定期削除・latchesのcancelledクローズ)・退会処理 — M3-8。本単位のDELETEはcancelled遷移+deleted Event
- フロントエンド(条件リスト・下書き一覧・保存API接続)— ws-5(12 M1-7)
- ORM・リポジトリ層(design §2.9で生SQL継続を判断済み)・created_at順一覧用の追加Index(design §2.10で不要と判断済み)
- PATCHの部分更新(フィールド単位)・ETag/If-Match・楽観排他の409応答(docsに規定なし)
- GET応答でのgeo_center座標・embeddingの返却(design §2.10「出力最小化」)
- match_eventsのpolling消化・pending行の掃除(M2の消費者実装と一体)
- 指定時刻Event(scheduled)・期限切れEvent(expired)の発行経路(定数のみ定義。発行者はM2/M3)
- 正規化テキスト生成・embedding列への書き込み(M2-2)

判断に迷う変更が出たら、その時点で作らずに報告ファイルの「計画からの逸脱・判断」へ記録する。

## 5. Review Focus(specが暗示するが、各タスクの代表試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **PATCHでalcohol_involved=trueに変わる変更(drinkingへのカテゴリ変更を含む)+19歳作成者** → 422 UNDER_AGE(05 §5「構造データの変更でalcohol_involved=trueとなる場合」)。→ Task 8 の試験でピン留め
2. **draft再保存でstructured_intentを省略しつつstatus=activeを同時指定** → active化は全量必須なので422 VALIDATION_ERROR(§2.10分岐の組合せ)。→ Task 8
3. **同一Intentへの2回目のDELETE**(1回目でcancelled遷移済み)→ 422 VALIDATION_ERROR(遷移表にcancelled→cancelledはない)。→ Task 9
4. **他人のIntent由来のcursor値**(beforeの(created_at, id)が自分の行に存在しない)→ 自分の一覧が空で正常応答(list_pageはuser_id固定なので他人の位置を参照しても漏れません)。→ Task 7
5. **limit=0 / limit=101 / status=drafty(綴り不正)** → いずれも422 VALIDATION_ERROR envelope(値域・Literal検証)。→ Task 10

## 6. 完了条件(テストで証明できる形。報告ファイルに項目別に記録する)

1. `make lint` がグリーン(ruff format検査+lint)。
2. `make test` がグリーン(全件。新規unit試験を含む。新規試験の件数をファイル別に報告ファイルへ記録)。
3. design §2.2表のEvent発行(POST active=created v1・PATCH active更新=updated v+1・draft→active=created 据え置きor+1・resume=updated v+1・DELETE=deleted 不変・draft作成/再保存/pause=発行なし)が `tests/unit/intents/test_intents_service.py` の関数として存在し全て通過。
4. 検証のactive/draft切替(必須3・時刻・年齢・ジオコーディング)がunitの対照試験で立証済み(activeで422になる入力がdraftで受理される)。
5. 時刻検証の境界(nowちょうど=受理・now-1秒=422・now+7日ちょうど=受理・now+7日+1秒=422。expires_atも同様)が `tests/unit/intents/test_time_validation.py` で通過。
6. `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットが `core/clock.py` のみ(arch test も自動強制)。
7. ログ・例外メッセージにraw_text・地名・条件文言が混入しないことをunit試験(caplog)で検証済み(08 §2.4)。
8. `git status` の差分が§3.1・§3.2の一覧どおり(§4.1の禁止ファイルに差分なし)。
9. `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空(STATUS運用ルール5)。
10. `backend/tests/integration/test_intents_crud_api.py` が design §4.2-1〜10をカバーして存在する。**実行(test-ci)はスーパーバイザー検証待ちでよい**(§0.3)。報告ファイルの検証手順に「`docker compose build api` → `make test-ci`」と「test-ci後に `make geo-import` でgeo実データ復旧」を明記する。

## 7. タスク一覧(TDD。§7.1〜§7.13の順で遂行する)

| # | タスク | 主な成果物 | コミットメッセージ |
|---|---|---|---|
| 1 | CRUD例外の追加 | errors.py追記+test_errors.py追記 | `feat(intents): CRUD用例外6種を追加` |
| 2 | 入力モデル | intent_input.py+test_intent_input.py | `feat(intents): 保存API入力検証モデルを追加` |
| 3 | 保存列変換とSQL | store.py+mapping.py+test_mapping.py | `feat(intents): 保存列変換と永続化SQLを追加` |
| 4 | MatchEvent発行 | events.py+test_events.py | `feat(intents): match_events outbox発行を追加` |
| 5 | create(active/draft) | service.py追記+test_intents_service.py | `feat(intents): IntentService.createと検証切替を追加` |
| 6 | 時刻検証の境界 | test_time_validation.py | `test(intents): 時刻検証の境界をピン留め` |
| 7 | get/list(cursor) | service.py追記+test_intents_service.py追記 | `feat(intents): IntentService get/listとキーセットcursorを追加` |
| 8 | update(PATCH分岐) | service.py追記+test_intents_service.py追記 | `feat(intents): IntentService.update(PATCH分岐・draft→active)を追加` |
| 9 | pause/resume/delete | service.py追記+test_intents_service.py追記 | `feat(intents): pause/resume/deleteを追加` |
| 10 | CRUDルーティング | routes.py追記+test_crud_routes.py | `feat(intents): intents CRUDルータ(7エンドポイント)を追加` |
| 11 | アプリ統合 | main.py・__init__.py変更 | `feat(app): intents CRUDをアプリへ統合` |
| 12 | integration試験 | test_intents_crud_api.py(実行しない) | `test(intents): CRUD integration試験を追加` |
| 13 | 報告ファイル | ws-3-report.md | `docs: M1 ws-3実装報告` |

実行コマンドの凡例: unit試験の単文件実行は `cd backend && uv run pytest tests/unit/intents/<file>.py -v`、全体は `make test`(リポジトリルート)。

## 7.1 Task 1: CRUD例外の追加

**Files:**
- Modify: `backend/src/latch/intents/errors.py`(ファイル末尾へ追記)
- Test: `backend/tests/unit/intents/test_errors.py`(既存ファイルへ追記)

**Interfaces:**
- Produces: `IntentValidationError`(422 VALIDATION_ERROR)・`UnderAgeError`(422 UNDER_AGE)・`GeocodingFailedError`(422 GEOCODING_FAILED)・`IntentNotFoundError`(404 NOT_FOUND)・`ForbiddenError`(403 FORBIDDEN)・`InvalidTransitionError`(422 VALIDATION_ERROR)。すべて `IntentsError` 直下(既存ハンドラがそのままenvelope化)

- [ ] **Step 1: 失敗する試験を既存 test_errors.py へ追記する**

既存ファイルの末尾に追記する。import節は既存の `from latch.intents import …` 形式を踏襲して新例外を追記し、`import pytest` を先頭へ追加する。追記後のファイル全体:

```python
"""intents例外階層(design §2.7)。http_status/codeは05 §5エラー形式表の固定値。"""

import pytest

from latch.intents import (
    DependencyUnavailableError,
    ForbiddenError,
    GeocodingFailedError,
    IntentNotFoundError,
    IntentValidationError,
    IntentsError,
    InvalidTransitionError,
    LLMUnavailableError,
    UnderAgeError,
    UnstructurableError,
)


def test_llm_unavailable_is_503_llm_unavailable():
    exc = LLMUnavailableError("intent parser unavailable")
    assert isinstance(exc, IntentsError)
    assert exc.http_status == 503
    assert exc.code == "LLM_UNAVAILABLE"


def test_unstructurable_is_422_validation_error():
    exc = UnstructurableError("structured intent is not extractable")
    assert isinstance(exc, IntentsError)
    assert exc.http_status == 422
    assert exc.code == "VALIDATION_ERROR"


def test_dependency_unavailable_is_503():
    exc = DependencyUnavailableError("intent parse dependency unavailable")
    assert isinstance(exc, IntentsError)
    assert exc.http_status == 503
    assert exc.code == "DEPENDENCY_UNAVAILABLE"


# --- M1 ws-3: CRUD例外(design §3.1・05 §5エラー形式表)---


@pytest.mark.parametrize(
    ("exc_type", "http_status", "code"),
    [
        pytest.param(IntentValidationError, 422, "VALIDATION_ERROR", id="validation"),
        pytest.param(UnderAgeError, 422, "UNDER_AGE", id="under-age"),
        pytest.param(GeocodingFailedError, 422, "GEOCODING_FAILED", id="geocoding"),
        pytest.param(IntentNotFoundError, 404, "NOT_FOUND", id="not-found"),
        pytest.param(ForbiddenError, 403, "FORBIDDEN", id="forbidden"),
        pytest.param(InvalidTransitionError, 422, "VALIDATION_ERROR", id="transition"),
    ],
)
def test_crud_error_codes(exc_type, http_status, code):
    exc = exc_type("fixed message")
    assert isinstance(exc, IntentsError)
    assert exc.http_status == http_status
    assert exc.code == code
```

(既存3試験の本文は変更しない — 上記は追記後の全体形の提示)

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_errors.py -v`
Expected: FAIL(`ImportError: cannot import name 'IntentValidationError'`)

- [ ] **Step 3: errors.py へ追記する**

```python
class IntentValidationError(IntentsError):
    """保存APIの入力検証422(必須3フィールド欠落・時刻範囲外・cursor不正)。

    parse経路のUnstructurableError(LLM出力不正)と違い、保存経路のリクエスト
    検証用(design §2.3)。
    """

    http_status = 422
    code = "VALIDATION_ERROR"


class UnderAgeError(IntentsError):
    """20歳未満の飲酒Intent作成・更新(08 D-10)。"""

    http_status = 422
    code = "UNDER_AGE"


class GeocodingFailedError(IntentsError):
    """location.nameに該当する地物が存在しない(05 §5・04 §3)。"""

    http_status = 422
    code = "GEOCODING_FAILED"


class IntentNotFoundError(IntentsError):
    """対象Intentが存在しない。未登録JWT(User行なし)も同じ404(design §2.6)。"""

    http_status = 404
    code = "NOT_FOUND"


class ForbiddenError(IntentsError):
    """所有者以外の操作(05 §5)。存在秘匿の404ではなく403(design §6-5f)。"""

    http_status = 403
    code = "FORBIDDEN"


class InvalidTransitionError(IntentsError):
    """遷移表にない操作(draftのpause・matched行のDELETE・逆遷移等・design §6-5e)。"""

    http_status = 422
    code = "VALIDATION_ERROR"
```

- [ ] **Step 4: 実行して通過を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_errors.py -v`
Expected: PASS(既存試験込みで全緑)

- [ ] **Step 5: lint してコミット**

```bash
make lint
git add backend/src/latch/intents/errors.py backend/tests/unit/intents/test_errors.py
git commit -m "feat(intents): CRUD用例外6種を追加"
```

## 7.2 Task 2: 入力検証モデル(intent_input.py)

**Files:**
- Create: `backend/src/latch/intents/intent_input.py`
- Test: `backend/tests/unit/intents/test_intent_input.py`

**Interfaces:**
- Produces: `StructuredIntentInput`(全フィールドOptional・extra=ignore)・`IntentCreateRequest(raw_text: 1〜300字, status: Literal["active","draft"]="active", structured_intent: | None)`・`IntentPatchRequest(status: Literal | None)`。後続タスクは `StructuredIntentInput.model_validate(dict)` で構築する

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/intents/test_intent_input.py` を新規作成:

```python
"""intent_inputモデルの受入・拒否(design §4.1-1・§2.3〜§2.4)。

拒否はRequestValidationError→既存422ハンドラ経路(FastAPIに載せるのは
Task 10)。ここではモデル単体の検証を確定する。
"""

import pytest
from pydantic import ValidationError

from latch.intents.intent_input import (
    IntentCreateRequest,
    IntentPatchRequest,
    StructuredIntentInput,
)


def _full_structured() -> dict:
    """05 §5 POST /v1/intents応答例と同じ形の全フィールド値。"""
    return {
        "category": {"primary": "drinking", "secondary": "焼肉"},
        "alcohol_involved": True,
        "time": {"start": "2026-09-27T21:00:00+09:00",
                 "end": "2026-09-28T00:00:00+09:00"},
        "location": {"name": "天文館", "radius_m": 2000},
        "budget": {"max": 5000, "currency": "JPY"},
        "participants": {"min": 2, "max": 4},
        "visibility": "hidden_until_match",
        "notification_level": "proposals_only",
        "expires_at": "2026-09-27T23:30:00+09:00",
        "soft_constraints": ["軽く飲みたい"],
        "ng_unverifiable": ["会社関係の人は避けたい"],
        "negative_constraints": [],
    }


# --- 受入 ---


def test_create_request_accepts_full_structured_intent():
    req = IntentCreateRequest.model_validate(
        {
            "raw_text": "今夜20時から天文館で軽く飲みたい",
            "structured_intent": _full_structured(),
        }
    )
    assert req.status == "active"
    assert req.structured_intent.category.primary == "drinking"
    assert req.structured_intent.location.radius_m == 2000


def test_create_status_defaults_to_active_and_structured_optional():
    """status省略=active・structured_intent省略可(draftのraw_textのみ保存)。"""
    req = IntentCreateRequest.model_validate({"raw_text": "下書き"})
    assert req.status == "active"
    assert req.structured_intent is None


def test_partial_structured_intent_is_accepted_for_draft():
    """draftの部分的な中途データ(カテゴリのみ等)も形式正なら受理(確定値3)。"""
    inp = StructuredIntentInput.model_validate(
        {"category": {"primary": "meal"}, "soft_constraints": ["静かな店"]}
    )
    assert inp.category.primary == "meal"
    assert inp.time is None
    assert inp.location is None
    assert inp.soft_constraints == ["静かな店"]


def test_patch_request_accepts_null_status_and_structured():
    req = IntentPatchRequest.model_validate({"raw_text": "下書き"})
    assert req.status is None
    assert req.structured_intent is None


def test_extra_keys_are_ignored():
    inp = StructuredIntentInput.model_validate({"unknown_key": 1})
    assert inp.alcohol_involved is None


# --- 拒否(値域・形式)---


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.update(raw_text=""), id="raw-text-empty"),
        pytest.param(lambda d: d.update(raw_text="あ" * 301), id="raw-text-301"),
        pytest.param(
            lambda d: d["structured_intent"]["category"].update(primary="shopping"),
            id="primary-not-3-values",
        ),
        pytest.param(
            lambda d: d["structured_intent"]["participants"].update(min=0),
            id="participants-min-0",
        ),
        pytest.param(
            lambda d: d["structured_intent"]["participants"].update(max=5),
            id="participants-max-5",
        ),
        pytest.param(
            lambda d: d["structured_intent"]["budget"].update(max=-1),
            id="budget-max-minus",
        ),
        pytest.param(
            lambda d: d["structured_intent"]["location"].update(radius_m=0),
            id="radius-0",
        ),
        pytest.param(
            lambda d: d["structured_intent"]["time"].update(
                start="2026-09-27T21:00:00"
            ),
            id="naive-time-start",
        ),
        pytest.param(
            lambda d: d["structured_intent"]["time"].update(end="2026-09-27T23:00:00"),
            id="naive-time-end",
        ),
        pytest.param(
            lambda d: d["structured_intent"].update(expires_at="2026-09-27T23:30:00"),
            id="naive-expires-at",
        ),
        pytest.param(
            lambda d: d["structured_intent"].update(visibility="public"),
            id="visibility-invalid",
        ),
        pytest.param(
            lambda d: d["structured_intent"].update(notification_level="all"),
            id="notification-level-invalid",
        ),
        pytest.param(
            lambda d: d["structured_intent"]["location"].update(name=""),
            id="location-name-empty",
        ),
    ],
)
def test_invalid_values_are_rejected(mutate):
    data = {"raw_text": "本文", "structured_intent": _full_structured()}
    mutate(data)
    with pytest.raises(ValidationError):
        IntentCreateRequest.model_validate(data)


@pytest.mark.parametrize("status", ["drafty", "ACTIVE", None])
def test_create_status_must_be_active_or_draft(status):
    data = {"raw_text": "本文", "status": status}
    with pytest.raises(ValidationError):
        IntentCreateRequest.model_validate(data)


def test_patch_raw_text_is_required():
    with pytest.raises(ValidationError):
        IntentPatchRequest.model_validate({"status": "draft"})
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_intent_input.py -v`
Expected: FAIL(モジュール不在の `ModuleNotFoundError`)

- [ ] **Step 3: intent_input.py を実装する**

```python
"""05 §5保存API契約の入力検証モデル(design §2.3〜§2.4・§3.1)。

parseのParserOutput(schema.py)とは別契約。1契約1モデル: 全フィールド
OptionalのStructuredIntentInputをactive/draft両経路で使う(05 §5はPATCHと
POSTを同一契約と規定)。Pydanticは形式・値域のみを検証し、必須3フィール
ド・時刻(過去/7日)・年齢・ジオコーディングはactive経路のサービス層検証
(design §2.3)。draftは「Pydantic通過=形式検証完了」(確定値3・7)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _IgnoreExtraModel(BaseModel):
    """余分キーを無視する(07 §4の失敗分類に揃える・schema.pyと同設定)。"""

    model_config = ConfigDict(extra="ignore")


class CategoryInput(_IgnoreExtraModel):
    primary: Literal["meal", "drinking", "activity"] | None = None
    secondary: str | None = None


class TimeInput(_IgnoreExtraModel):
    start: datetime | None = None
    end: datetime | None = None
    flexibility_minutes: None = None  # MVPでは常にnull(03 D-19の固定扱い)

    @field_validator("start", "end")
    @classmethod
    def _must_be_tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("time must be tz-aware ISO8601")
        return v


class LocationInput(_IgnoreExtraModel):
    name: str | None = Field(default=None, min_length=1)
    radius_m: int | None = Field(default=None, ge=1)


class BudgetInput(_IgnoreExtraModel):
    max: int | None = Field(default=None, ge=0)
    currency: Literal["JPY"] = "JPY"


class ParticipantsInput(_IgnoreExtraModel):
    min: int | None = Field(default=None, ge=1, le=4)
    max: int | None = Field(default=None, ge=1, le=4)


class StructuredIntentInput(_IgnoreExtraModel):
    """保存APIのstructured_intent(全フィールドOptional — design §2.4)。"""

    category: CategoryInput | None = None
    alcohol_involved: bool | None = None
    time: TimeInput | None = None
    location: LocationInput | None = None
    budget: BudgetInput | None = None
    participants: ParticipantsInput | None = None
    visibility: Literal["hidden_until_match", "summary_only"] | None = None
    notification_level: Literal["proposals_only", "nearby_also", "muted"] | None = None
    expires_at: datetime | None = None
    soft_constraints: list[str] | None = None
    negative_constraints: list[str] | None = None  # 受け取るが保存は常に空配列(FR-42)
    ng_unverifiable: list[str] | None = None

    @field_validator("expires_at")
    @classmethod
    def _expires_must_be_tz_aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("time must be tz-aware ISO8601")
        return v


class IntentCreateRequest(BaseModel):
    """POST /v1/intents(05 §5)。statusはactive(既定・省略可)またはdraft。"""

    raw_text: str = Field(min_length=1, max_length=300)
    status: Literal["active", "draft"] = "active"
    structured_intent: StructuredIntentInput | None = None


class IntentPatchRequest(BaseModel):
    """PATCH /v1/intents/{id}(design §2.10の契約詳細)。status未指定=現状維持。"""

    raw_text: str = Field(min_length=1, max_length=300)
    status: Literal["active", "draft"] | None = None
    structured_intent: StructuredIntentInput | None = None
```

- [ ] **Step 4: 実行して通過を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_intent_input.py -v`
Expected: PASS(全件)

- [ ] **Step 5: lint してコミット**

```bash
make lint
git add backend/src/latch/intents/intent_input.py backend/tests/unit/intents/test_intent_input.py
git commit -m "feat(intents): 保存API入力検証モデルを追加"
```

## 7.3 Task 3: 保存列変換(mapping.py)と永続化SQL(store.py)

**Files:**
- Create: `backend/src/latch/intents/store.py`
- Create: `backend/src/latch/intents/mapping.py`
- Test: `backend/tests/unit/intents/test_mapping.py`

**Interfaces:**
- Produces(store.py): `UserRow(id: UUID, birth_date: date)`・`IntentRow`(保存行列挙・§7.3 Step 3の定義どおり)・`IntentStore(engine)` — `fetch_user_row(provider, subject)`・`fetch(conn, intent_id)`・`fetch_for_update(conn, intent_id)`・`insert(conn, cols, *, user_id, status, now) -> UUID`・`update(conn, intent_id, cols, *, status, version, now, expected_status) -> int`・`update_status(conn, intent_id, *, status, version, now, expected_status) -> int`・`list_page(conn, user_id, *, status, before, limit) -> list[IntentRow]`
- Produces(mapping.py): `ResolvedColumns`(design §3.1 + `raw_text: str = ""`)・`resolve_for_draft(inp)`・`resolve_for_active(inp, *, now)`・`differs_from_row(cols, row) -> bool`・`columns_from_row(row)`(draft再保存でstructured_intent省略時の保持用)・`to_response_structured(row) -> dict`
- 注意: mapping.py と store.py は互いを `if TYPE_CHECKING:` でのみ import する(実行時の循環import回避。アノテーションは `from __future__ import annotations` で遅延評価)。store.py のSQL実行はintegration(test-ci)で検証し、unitではスタブIntentStoreに差し替える(design §2.9・§4.1)

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/intents/test_mapping.py` を新規作成:

```python
"""mapping変換(design §4.1-2)。alcohol確定・ng降格・補完消費・draft正規化・
実質変更判定・応答再構成を純関数レベルで確定する。"""

import uuid
from datetime import UTC, datetime

import pytest

from latch.intents.completion import (
    DEFAULT_PARTICIPANTS,
    DEFAULT_RADIUS_M,
    default_time_end,
    nearest_expires_at,
)
from latch.intents.intent_input import StructuredIntentInput
from latch.intents.mapping import (
    columns_from_row,
    differs_from_row,
    resolve_for_active,
    resolve_for_draft,
    to_response_structured,
)
from latch.intents.store import IntentRow

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-27 21:00
START = datetime(2026, 9, 27, 21, 0, 0, tzinfo=UTC)  # tz表現はどちらでも同じ瞬間


def _input(**overrides) -> StructuredIntentInput:
    data = {
        "category": {"primary": "drinking", "secondary": "焼肉"},
        "alcohol_involved": True,
        "time": {"start": START.isoformat(),
                 "end": "2026-09-28T00:00:00+09:00"},
        "location": {"name": "天文館", "radius_m": 2000},
        "budget": {"max": 5000, "currency": "JPY"},
        "participants": {"min": 2, "max": 4},
        "visibility": "summary_only",
        "notification_level": "nearby_also",
        "expires_at": "2026-09-27T23:30:00+09:00",
        "soft_constraints": ["軽く飲みたい"],
        "ng_unverifiable": ["会社関係の人は避けたい"],
        "negative_constraints": [],
    }
    data.update(overrides)
    return StructuredIntentInput.model_validate(data)


def _row(**overrides) -> IntentRow:
    base = dict(
        id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        user_id=uuid.UUID("00000000-0000-4000-8000-000000000002"),
        category_primary="drinking",
        alcohol_involved=True,
        raw_text="原文",
        structured_data={},
        geo_radius_m=2000,
        budget_max=5000,
        participants_min=2,
        participants_max=4,
        visibility="summary_only",
        notification_level="nearby_also",
        status="draft",
        version=1,
        time_start=START,
        time_end=datetime(2026, 9, 28, 0, 0, 0, tzinfo=UTC),
        expires_at=datetime(2026, 9, 27, 23, 30, 0, tzinfo=UTC),
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(overrides)
    return IntentRow(**base)
```

```python
# --- (a) resolve_for_active ---


def test_active_drinking_forces_alcohol_true_over_client_value():
    """07規則7: drinkingはクライアント修正値(false)より優先してtrue確定。"""
    cols = resolve_for_active(_input(alcohol_involved=False), now=NOW)
    assert cols.alcohol_involved is True


def test_active_non_drinking_keeps_client_alcohol():
    cols = resolve_for_active(
        _input(category={"primary": "meal"}, alcohol_involved=True), now=NOW
    )
    assert cols.alcohol_involved is True
    cols2 = resolve_for_active(
        _input(category={"primary": "meal"}, alcohol_involved=False), now=NOW
    )
    assert cols2.alcohol_involved is False


def test_active_unspecified_alcohol_defaults_false():
    data = _input().model_dump(exclude_none=True)
    data["category"] = {"primary": "meal"}
    data["alcohol_involved"] = None
    cols = resolve_for_active(StructuredIntentInput.model_validate(data), now=NOW)
    assert cols.alcohol_involved is False


def test_active_completes_time_end_from_start_plus_3h():
    inp = _input()
    inp = StructuredIntentInput.model_validate(
        {**inp.model_dump(exclude_none=True), "time": {"start": START.isoformat()}}
    )
    cols = resolve_for_active(inp, now=NOW)
    assert cols.time_end == default_time_end(START)


def test_active_completes_participants_radius_and_expires_at():
    inp = StructuredIntentInput.model_validate(
        {"category": {"primary": "meal"}, "time": {"start": START.isoformat()},
         "location": {"name": "天文館"}}
    )
    cols = resolve_for_active(inp, now=NOW)
    assert (cols.participants_min, cols.participants_max) == DEFAULT_PARTICIPANTS
    assert cols.geo_radius_m == DEFAULT_RADIUS_M
    assert cols.expires_at == nearest_expires_at(START, NOW)
    assert cols.visibility == "hidden_until_match"
    assert cols.notification_level == "proposals_only"


def test_active_keeps_explicit_expires_at():
    explicit = "2026-09-28T12:00:00+09:00"
    inp = _input(expires_at=explicit)
    cols = resolve_for_active(inp, now=NOW)
    assert cols.expires_at is not None
    assert cols.expires_at.isoformat() == explicit


def test_structured_data_shape_with_location_name_and_downgraded():
    """05 §2の5キー+location_name・ng由来はdowngraded_from_ng=true(確定値10・34)。"""
    cols = resolve_for_active(_input(), now=NOW)
    sd = cols.structured_data
    assert set(sd) == {
        "category_secondary",
        "soft_constraints",
        "negative_constraints",
        "time_flexibility_minutes",
        "location_flexibility",
        "location_name",
    }
    assert sd["category_secondary"] == "焼肉"
    assert sd["location_name"] == "天文館"
    assert sd["soft_constraints"] == [
        {"text": "軽く飲みたい", "downgraded_from_ng": False},
        {"text": "会社関係の人は避けたい", "downgraded_from_ng": True},
    ]
    assert sd["negative_constraints"] == []
    assert sd["time_flexibility_minutes"] is None
    assert sd["location_flexibility"] is None


def test_non_empty_negative_constraints_are_saved_as_empty():
    """FR-42: negative_constraintsを受け取っても保存は常に空配列。"""
    cols = resolve_for_active(
        _input(negative_constraints=["上司は避けたい"]), now=NOW
    )
    assert cols.structured_data["negative_constraints"] == []


def test_active_without_required_fields_raises_value_error():
    """resolve_for_activeは検証済み前提(design §2.3)。未検証入力はValueError。"""
    with pytest.raises(ValueError):
        resolve_for_active(StructuredIntentInput.model_validate({}), now=NOW)


# --- (b) resolve_for_draft ---


def test_draft_resolves_without_completion():
    inp = _input()
    cols = resolve_for_draft(inp)
    assert cols.category_primary == "drinking"
    assert cols.time_start == START
    assert cols.time_end is not None  # 指定値のみ(この入力はend付き)


def test_draft_empty_input_normalizes_to_defaults():
    """§2.4表: 未指定は ''/False/(2,2)/既定値/NULL。補完なし。"""
    cols = resolve_for_draft(StructuredIntentInput.model_validate({}))
    assert cols.category_primary == ""
    assert cols.alcohol_involved is False
    assert (cols.participants_min, cols.participants_max) == DEFAULT_PARTICIPANTS
    assert cols.visibility == "hidden_until_match"
    assert cols.notification_level == "proposals_only"
    assert cols.time_start is None
    assert cols.time_end is None
    assert cols.expires_at is None
    assert cols.geo_radius_m is None
    assert cols.structured_data["soft_constraints"] == []
    assert cols.structured_data["location_name"] is None


def test_draft_does_not_force_alcohol_for_drinking():
    """draftではdrinkingでもクライアント値のまま(確定はactive保存時のみ)。"""
    cols = resolve_for_draft(_input(alcohol_involved=False))
    assert cols.alcohol_involved is False


# --- 実質変更判定(design §2.7)---


def test_differs_from_row_false_for_identical_draft_columns():
    cols = resolve_for_draft(_input())
    row = _row(
        raw_text="原文",
        category_primary=cols.category_primary,
        alcohol_involved=cols.alcohol_involved,
        structured_data=cols.structured_data,
        budget_max=cols.budget_max,
        participants_min=cols.participants_min,
        participants_max=cols.participants_max,
        visibility=cols.visibility,
        notification_level=cols.notification_level,
        time_start=cols.time_start,
        time_end=cols.time_end,
        expires_at=cols.expires_at,
        geo_radius_m=cols.geo_radius_m,
    )
    assert differs_from_row(_with_raw(cols, "原文"), row) is False


def _with_raw(cols, raw_text):
    from dataclasses import replace

    return replace(cols, raw_text=raw_text)


def _row_of(cols) -> IntentRow:
    """colsと同一の保存列を持つ行(differs_from_rowの整合確認用)。"""
    return _row(
        raw_text=cols.raw_text,
        category_primary=cols.category_primary,
        alcohol_involved=cols.alcohol_involved,
        structured_data=cols.structured_data,
        budget_max=cols.budget_max,
        participants_min=cols.participants_min,
        participants_max=cols.participants_max,
        visibility=cols.visibility,
        notification_level=cols.notification_level,
        time_start=cols.time_start,
        time_end=cols.time_end,
        expires_at=cols.expires_at,
        geo_radius_m=cols.geo_radius_m,
    )


def test_differs_from_row_true_for_any_compared_field_change():
    from dataclasses import replace as dc_replace

    cols = _with_raw(resolve_for_draft(_input()), "原文")
    same = _row_of(cols)
    assert differs_from_row(_with_raw(cols, "変更後"), same) is True  # raw_text
    assert differs_from_row(cols, same) is False  # 整合確認
    assert differs_from_row(cols, dc_replace(same, category_primary="meal")) is True
    assert differs_from_row(cols, dc_replace(same, geo_radius_m=999)) is True


# --- columns_from_row / to_response_structured ---


def test_columns_from_row_round_trips_saved_columns():
    row = _row(structured_data={"location_name": "天文館"})
    cols = columns_from_row(row)
    assert cols.raw_text == "原文"
    assert cols.category_primary == "drinking"
    assert cols.structured_data == {"location_name": "天文館"}
    assert cols.geo_lon is None and cols.geo_lat is None


def test_to_response_structured_splits_downgraded_and_location_name():
    sd = {
        "category_secondary": "焼肉",
        "soft_constraints": [
            {"text": "軽く飲みたい", "downgraded_from_ng": False},
            {"text": "会社関係の人は避けたい", "downgraded_from_ng": True},
        ],
        "negative_constraints": [],
        "time_flexibility_minutes": None,
        "location_flexibility": None,
        "location_name": "天文館",
    }
    out = to_response_structured(_row(structured_data=sd))
    assert out["category"] == {"primary": "drinking", "secondary": "焼肉"}
    assert out["location"]["name"] == "天文館"
    assert out["location"]["radius_m"] == 2000
    assert out["location"]["flexibility"] is None
    assert out["soft_constraints"] == ["軽く飲みたい"]
    assert out["ng_unverifiable"] == ["会社関係の人は避けたい"]
    assert out["negative_constraints"] == []
    assert out["visibility"] == "summary_only"
    assert out["budget"] == {"max": 5000, "currency": "JPY"}
    assert out["participants"] == {"min": 2, "max": 4}


def test_to_response_structured_partial_draft_row():
    """draft部分行(category=''・時刻NULL)の再構成(§4.1-2c)。"""
    out = to_response_structured(
        _row(
            category_primary="",
            alcohol_involved=False,
            structured_data={},
            geo_radius_m=None,
            budget_max=None,
            time_start=None,
            time_end=None,
            expires_at=None,
        )
    )
    assert out["category"]["primary"] is None
    assert out["category"]["secondary"] is None
    assert out["time"]["start"] is None
    assert out["location"]["name"] is None
    assert out["location"]["radius_m"] is None
    assert out["expires_at"] is None
    assert out["alcohol_involved"] is False
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_mapping.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.intents.store'`)

- [ ] **Step 3: store.py を実装する**

`backend/src/latch/intents/store.py` を新規作成:

```python
"""intents永続化(design §2.9〜§2.10・§2.5の競合制御)。

text()生SQLのみ(ws-1流儀の継続 — design §2.9)。SQL一式をこのモジュールへ
集約し、トランザクション境界はserviceが持つ(fetch_user_row以外はconnを
受け取る)。fetch_user_rowはトランザクション不要のためengine直
(uq_users_auth_provider_subject索引へのSELECT 1本 — design §2.6)。
時刻列はClock由来の明示値。geo_centerはST_SetSRID(ST_MakePoint(lon,lat),
4326)のgeography(座標はserviceがジオコーディング結果を渡す)。実SQLの試験
はintegration(test-ci)。unitはスタブstoreで置き換える(design §4.1)。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

if TYPE_CHECKING:
    from latch.intents.mapping import ResolvedColumns


@dataclass(frozen=True)
class UserRow:
    """fetch_user_rowの戻り(年齢検証に必要な最小列 — design §2.6)。"""

    id: uuid.UUID
    birth_date: date


@dataclass(frozen=True)
class IntentRow:
    """intents表の保存行(geo_center座標・embeddingは含めない — design §2.10)。"""

    id: uuid.UUID
    user_id: uuid.UUID
    category_primary: str
    alcohol_involved: bool
    raw_text: str
    structured_data: dict
    geo_radius_m: int | None
    budget_max: int | None
    participants_min: int
    participants_max: int
    visibility: str
    notification_level: str
    status: str
    version: int
    time_start: datetime | None
    time_end: datetime | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _coerce_uuid(value: object) -> uuid.UUID:
    """asyncpgはuuid列をUUIDインスタンスで返す(M0 ws-3と同じ落ち穴対策)。"""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


_ROW_COLS = (
    "id, user_id, category_primary, alcohol_involved, raw_text, structured_data, "
    "geo_radius_m, budget_max, participants_min, participants_max, visibility, "
    "notification_level, status, version, time_start, time_end, expires_at, "
    "created_at, updated_at"
)

_GEO_CENTER_EXPR = (
    "CASE WHEN :geo_lon::float8 IS NULL THEN NULL "
    "ELSE ST_SetSRID(ST_MakePoint(:geo_lon, :geo_lat), 4326)::geography END"
)

_SELECT_BY_ID = text(f"SELECT {_ROW_COLS} FROM intents WHERE id = :intent_id")

_SELECT_FOR_UPDATE = text(
    f"SELECT {_ROW_COLS} FROM intents WHERE id = :intent_id FOR UPDATE"
)

_SELECT_USER = text(
    "SELECT id, birth_date FROM users "
    "WHERE auth_provider = :provider AND auth_subject = :subject"
)

_INSERT = text(f"""
    INSERT INTO intents (
        user_id, category_primary, alcohol_involved, raw_text, structured_data,
        geo_center, geo_radius_m, budget_max, participants_min, participants_max,
        visibility, notification_level, status, version,
        time_start, time_end, expires_at, created_at, updated_at
    ) VALUES (
        :user_id, :category_primary, :alcohol_involved, :raw_text,
        CAST(:structured_data AS jsonb),
        {_GEO_CENTER_EXPR},
        :geo_radius_m, :budget_max, :participants_min, :participants_max,
        :visibility, :notification_level, :status, :version,
        :time_start, :time_end, :expires_at, :created_at, :updated_at
    )
    RETURNING id
""")

_UPDATE = text(f"""
    UPDATE intents SET
        category_primary = :category_primary,
        alcohol_involved = :alcohol_involved,
        raw_text = :raw_text,
        structured_data = CAST(:structured_data AS jsonb),
        geo_center = {_GEO_CENTER_EXPR},
        geo_radius_m = :geo_radius_m,
        budget_max = :budget_max,
        participants_min = :participants_min,
        participants_max = :participants_max,
        visibility = :visibility,
        notification_level = :notification_level,
        status = :status,
        version = :version,
        time_start = :time_start,
        time_end = :time_end,
        expires_at = :expires_at,
        updated_at = :updated_at
    WHERE id = :intent_id AND status = :expected_status
""")

_UPDATE_STATUS = text("""
    UPDATE intents
    SET status = :status, version = :version, updated_at = :updated_at
    WHERE id = :intent_id AND status = :expected_status
""")


def _list_sql(*, with_status: bool, with_before: bool):
    """キーセット一覧(design §2.10)。ROW比較 (created_at, id) < (:ts, :id)。"""
    clauses = ["user_id = :user_id"]
    if with_status:
        clauses.append("status = :status")
    if with_before:
        clauses.append("(created_at, id) < (:before_ts, :before_id)")
    return text(
        f"SELECT {_ROW_COLS} FROM intents WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at DESC, id DESC LIMIT :limit"
    )


_LIST = {
    (False, False): _list_sql(with_status=False, with_before=False),
    (True, False): _list_sql(with_status=True, with_before=False),
    (False, True): _list_sql(with_status=False, with_before=True),
    (True, True): _list_sql(with_status=True, with_before=True),
}


def _row_from(mapping) -> IntentRow:
    structured = mapping.structured_data
    if isinstance(structured, str):
        structured = json.loads(structured)  # asyncpgのjsonbがstrで返る場合(users先例)
    return IntentRow(
        id=_coerce_uuid(mapping.id),
        user_id=_coerce_uuid(mapping.user_id),
        category_primary=mapping.category_primary,
        alcohol_involved=mapping.alcohol_involved,
        raw_text=mapping.raw_text,
        structured_data=structured,
        geo_radius_m=mapping.geo_radius_m,
        budget_max=mapping.budget_max,
        participants_min=mapping.participants_min,
        participants_max=mapping.participants_max,
        visibility=mapping.visibility,
        notification_level=mapping.notification_level,
        status=mapping.status,
        version=mapping.version,
        time_start=mapping.time_start,
        time_end=mapping.time_end,
        expires_at=mapping.expires_at,
        created_at=mapping.created_at,
        updated_at=mapping.updated_at,
    )


def _col_params(cols: "ResolvedColumns") -> dict:
    """INSERT/UPDATE共通の保存列パラメータ(structured_dataはJSON文字列で渡す)。"""
    return {
        "raw_text": cols.raw_text,
        "category_primary": cols.category_primary,
        "alcohol_involved": cols.alcohol_involved,
        "structured_data": json.dumps(cols.structured_data),
        "geo_lon": cols.geo_lon,
        "geo_lat": cols.geo_lat,
        "geo_radius_m": cols.geo_radius_m,
        "budget_max": cols.budget_max,
        "participants_min": cols.participants_min,
        "participants_max": cols.participants_max,
        "visibility": cols.visibility,
        "notification_level": cols.notification_level,
        "time_start": cols.time_start,
        "time_end": cols.time_end,
        "expires_at": cols.expires_at,
    }


class IntentStore:
    """intents表へのSQL実行(conn注入・トランザクションなし — design §2.10)。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def fetch_user_row(
        self, provider: str, subject: str
    ) -> UserRow | None:
        async with self._engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        _SELECT_USER, {"provider": provider, "subject": subject}
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return UserRow(id=_coerce_uuid(row.id), birth_date=row.birth_date)

    async def fetch(
        self, conn: AsyncConnection, intent_id: uuid.UUID
    ) -> IntentRow | None:
        row = (
            (await conn.execute(_SELECT_BY_ID, {"intent_id": intent_id}))
            .mappings()
            .first()
        )
        return None if row is None else _row_from(row)

    async def fetch_for_update(
        self, conn: AsyncConnection, intent_id: uuid.UUID
    ) -> IntentRow | None:
        row = (
            (await conn.execute(_SELECT_FOR_UPDATE, {"intent_id": intent_id}))
            .mappings()
            .first()
        )
        return None if row is None else _row_from(row)

    async def insert(
        self,
        conn: AsyncConnection,
        cols: "ResolvedColumns",
        *,
        user_id: uuid.UUID,
        status: str,
        now: datetime,
    ) -> uuid.UUID:
        row_id = await conn.scalar(
            _INSERT,
            {
                **_col_params(cols),
                "user_id": user_id,
                "status": status,
                "version": cols.version,
                "created_at": now,
                "updated_at": now,
            },
        )
        return _coerce_uuid(row_id)

    async def update(
        self,
        conn: AsyncConnection,
        intent_id: uuid.UUID,
        cols: "ResolvedColumns",
        *,
        status: str,
        version: int,
        now: datetime,
        expected_status: str,
    ) -> int:
        """全置換UPDATE。戻りは影響行数(0=競合でstatusが変わっていた)。"""
        result = await conn.execute(
            _UPDATE,
            {
                **_col_params(cols),
                "intent_id": intent_id,
                "status": status,
                "version": version,
                "updated_at": now,
                "expected_status": expected_status,
            },
        )
        return result.rowcount

    async def update_status(
        self,
        conn: AsyncConnection,
        intent_id: uuid.UUID,
        *,
        status: str,
        version: int,
        now: datetime,
        expected_status: str,
    ) -> int:
        """pause/resume/delete(cancelled)の条件付きUPDATE(pause/resume用)。"""
        result = await conn.execute(
            _UPDATE_STATUS,
            {
                "intent_id": intent_id,
                "status": status,
                "version": version,
                "updated_at": now,
                "expected_status": expected_status,
            },
        )
        return result.rowcount

    async def list_page(
        self,
        conn: AsyncConnection,
        user_id: uuid.UUID,
        *,
        status: str | None,
        before: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[IntentRow]:
        """created_at降順・id降順(design §2.10)。limit+1件を呼び出し側が取る。"""
        sql = _LIST[(status is not None, before is not None)]
        params: dict = {"user_id": user_id, "limit": limit}
        if status is not None:
            params["status"] = status
        if before is not None:
            params["before_ts"], params["before_id"] = before
        rows = (await conn.execute(sql, params)).mappings().all()
        return [_row_from(r) for r in rows]
```

- [ ] **Step 4: mapping.py を実装する**

`backend/src/latch/intents/mapping.py` を新規作成:

```python
"""入力→保存列への変換(design §2.3〜§2.4・§2.6〜§2.7)。

alcohol確定(07規則7)・補完規則の消費(completion.py)・draft正規化を
1箇所に集約する。検証(必須3・時刻・年齢)はserviceが先行する前提の純変換。
structured_dataは05 §2の5キー+location_name(design §6-1)。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import TYPE_CHECKING

from latch.intents.completion import (
    DEFAULT_PARTICIPANTS,
    DEFAULT_RADIUS_M,
    default_time_end,
    nearest_expires_at,
)
from latch.intents.intent_input import StructuredIntentInput

if TYPE_CHECKING:
    from latch.intents.store import IntentRow

DEFAULT_VISIBILITY = "hidden_until_match"
DEFAULT_NOTIFICATION_LEVEL = "proposals_only"


@dataclass(frozen=True)
class ResolvedColumns:
    """保存列一式(INSERT/UPDATEの直接の入力 — design §3.1)。

    raw_textはリクエスト直下の値のためserviceがreplaceで設定する。
    geo_lon/geo_latはactive経路でジオコーディング結果をserviceが設定
    (draft・未確定はNone)。versionはserviceが上書き(design §2.7)。
    """

    raw_text: str = ""
    category_primary: str = ""
    alcohol_involved: bool = False
    structured_data: dict = field(default_factory=dict)
    budget_max: int | None = None
    participants_min: int = DEFAULT_PARTICIPANTS[0]
    participants_max: int = DEFAULT_PARTICIPANTS[1]
    visibility: str = DEFAULT_VISIBILITY
    notification_level: str = DEFAULT_NOTIFICATION_LEVEL
    time_start: datetime | None = None
    time_end: datetime | None = None
    expires_at: datetime | None = None
    geo_radius_m: int | None = None
    geo_lon: float | None = None
    geo_lat: float | None = None
    version: int = 1


def _soft_constraints(inp: StructuredIntentInput) -> list[dict]:
    """soft由来が先・ng_unverifiable由来はdowngraded_from_ng=true(05 §2)。"""
    return [
        {"text": t, "downgraded_from_ng": False}
        for t in (inp.soft_constraints or [])
    ] + [
        {"text": t, "downgraded_from_ng": True}
        for t in (inp.ng_unverifiable or [])
    ]


def _structured_data(inp: StructuredIntentInput) -> dict:
    """05 §2の5キー+location_name(design §6-1)。negative_constraintsは常に空配列。"""
    secondary = inp.category.secondary if inp.category else None
    location_name = inp.location.name if inp.location else None
    return {
        "category_secondary": secondary,
        "soft_constraints": _soft_constraints(inp),
        "negative_constraints": [],  # FR-42(常に空配列が正常系)
        "time_flexibility_minutes": None,  # MVP固定扱い(03 D-19)
        "location_flexibility": None,
        "location_name": location_name,
    }


def resolve_for_draft(inp: StructuredIntentInput) -> ResolvedColumns:
    """draft正規化(design §2.4表)。補完なし・意味検証なし(形式はPydantic済み)。"""
    participants = inp.participants
    return ResolvedColumns(
        category_primary=(
            inp.category.primary if inp.category and inp.category.primary else ""
        ),
        alcohol_involved=inp.alcohol_involved or False,
        structured_data=_structured_data(inp),
        budget_max=inp.budget.max if inp.budget else None,
        participants_min=(
            participants.min
            if participants and participants.min is not None
            else DEFAULT_PARTICIPANTS[0]
        ),
        participants_max=(
            participants.max
            if participants and participants.max is not None
            else DEFAULT_PARTICIPANTS[1]
        ),
        visibility=inp.visibility or DEFAULT_VISIBILITY,
        notification_level=inp.notification_level or DEFAULT_NOTIFICATION_LEVEL,
        time_start=inp.time.start if inp.time else None,
        time_end=inp.time.end if inp.time else None,
        expires_at=inp.expires_at,
        geo_radius_m=inp.location.radius_m if inp.location else None,
    )


def resolve_for_active(
    inp: StructuredIntentInput, *, now: datetime
) -> ResolvedColumns:
    """active正規化。必須3検証済み前提(service)。補完を消費(design §2.3表)。"""
    if (
        inp.category is None
        or inp.category.primary is None
        or inp.time is None
        or inp.time.start is None
        or inp.location is None
        or inp.location.name is None
    ):
        raise ValueError("resolve_for_active requires validated input")
    primary = inp.category.primary
    time_start = inp.time.start
    cols = resolve_for_draft(inp)  # 共通部分はdraft正規化を再利用
    return replace(
        cols,
        category_primary=primary,
        alcohol_involved=(
            True
            if primary == "drinking"
            else (inp.alcohol_involved or False)
        ),
        time_end=inp.time.end or default_time_end(time_start),
        expires_at=inp.expires_at or nearest_expires_at(time_start, now),
        geo_radius_m=(
            inp.location.radius_m
            if inp.location.radius_m is not None
            else DEFAULT_RADIUS_M
        ),
    )


_COMPARE_FIELDS = (
    "raw_text",
    "category_primary",
    "alcohol_involved",
    "structured_data",
    "budget_max",
    "participants_min",
    "participants_max",
    "visibility",
    "notification_level",
    "time_start",
    "time_end",
    "expires_at",
    "geo_radius_m",
)


def differs_from_row(cols: ResolvedColumns, row: "IntentRow") -> bool:
    """draft表現での実質変更判定(design §2.7)。geo_center(導出値)は比較外。"""
    for name in _COMPARE_FIELDS:
        if getattr(cols, name) != getattr(row, name):
            return True
    return False


def columns_from_row(row: "IntentRow") -> ResolvedColumns:
    """保存行→ResolvedColumns(draft再保存でstructured_intent省略時の保持用)。"""
    return ResolvedColumns(
        raw_text=row.raw_text,
        category_primary=row.category_primary,
        alcohol_involved=row.alcohol_involved,
        structured_data=dict(row.structured_data or {}),
        budget_max=row.budget_max,
        participants_min=row.participants_min,
        participants_max=row.participants_max,
        visibility=row.visibility,
        notification_level=row.notification_level,
        time_start=row.time_start,
        time_end=row.time_end,
        expires_at=row.expires_at,
        geo_radius_m=row.geo_radius_m,
        version=row.version,
    )


def to_response_structured(row: "IntentRow") -> dict:
    """保存行→応答structured_intent(リクエストと同形に再構成 — design §2.10)。

    soft_constraintsをdowngraded_from_ngでsoft/ng_unverifiableへ分割し、
    location.nameはstructured_data.location_nameから戻す(design §6-1)。
    """
    sd = row.structured_data or {}
    soft = sd.get("soft_constraints") or []
    return {
        "category": {
            "primary": row.category_primary or None,
            "secondary": sd.get("category_secondary"),
        },
        "alcohol_involved": row.alcohol_involved,
        "time": {
            "start": row.time_start,
            "end": row.time_end,
            "flexibility_minutes": None,
        },
        "location": {
            "name": sd.get("location_name"),
            "radius_m": row.geo_radius_m,
            "flexibility": None,
        },
        "budget": {"max": row.budget_max, "currency": "JPY"},
        "participants": {"min": row.participants_min, "max": row.participants_max},
        "visibility": row.visibility,
        "notification_level": row.notification_level,
        "expires_at": row.expires_at,
        "soft_constraints": [
            c["text"] for c in soft if not c.get("downgraded_from_ng")
        ],
        "ng_unverifiable": [c["text"] for c in soft if c.get("downgraded_from_ng")],
        "negative_constraints": [],
    }
```

- [ ] **Step 5: 実行して通過を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_mapping.py -v`
Expected: PASS(全件)

- [ ] **Step 6: lint してコミット**

```bash
make lint
git add backend/src/latch/intents/store.py backend/src/latch/intents/mapping.py backend/tests/unit/intents/test_mapping.py
git commit -m "feat(intents): 保存列変換と永続化SQLを追加"
```

## 7.4 Task 4: MatchEvent発行(events.py)

**Files:**
- Create: `backend/src/latch/intents/events.py`
- Test: `backend/tests/unit/intents/test_events.py`

**Interfaces:**
- Produces: `EVENT_CREATED="created"`・`EVENT_UPDATED="updated"`・`EVENT_DELETED="deleted"`・`EVENT_EXPIRED="expired"`(定数のみ)・`EVENT_SCHEDULED="scheduled"`(定数のみ)・`insert_match_event(conn, *, event_type, intent_id, version, now) -> None`。service・後続単位はこの定数と関数を使う

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/intents/test_events.py` を新規作成:

```python
"""events(design §4.1-3)。event_type定数のピン留めとinsertパラメータの検証。
実INSERTはintegration(test-ci)。ここではconnスタブの呼び出し記録で検証する。
"""

import json
import uuid
from datetime import UTC, datetime

from latch.intents import events

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
INTENT_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


class FakeConn:
    def __init__(self):
        self.executes = []  # (stmt, params)

    async def execute(self, stmt, params=None):
        self.executes.append((stmt, params))


def test_event_type_constants_are_pinned():
    """design §2.2: embedding_completed(05 §2)の過去分詞形に揃えた確定値。"""
    assert events.EVENT_CREATED == "created"
    assert events.EVENT_UPDATED == "updated"
    assert events.EVENT_DELETED == "deleted"
    assert events.EVENT_EXPIRED == "expired"
    assert events.EVENT_SCHEDULED == "scheduled"


async def test_insert_match_event_passes_version_payload_and_pending():
    conn = FakeConn()
    await events.insert_match_event(
        conn,
        event_type=events.EVENT_CREATED,
        intent_id=INTENT_ID,
        version=1,
        now=NOW,
    )
    assert len(conn.executes) == 1
    stmt, params = conn.executes[0]
    assert stmt is events._INSERT_EVENT
    assert params["event_type"] == "created"
    assert params["source_intent_id"] == INTENT_ID
    assert json.loads(params["payload"]) == {"version": 1}
    assert params["created_at"] == NOW  # Clock由来の明示値


def test_insert_sql_pins_pending_status_literal():
    """status='pending'はSQLリテラル(design §2.2A)。"""
    assert "'pending'" in str(events._INSERT_EVENT)
    assert "match_events" in str(events._INSERT_EVENT)
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_events.py -v`
Expected: FAIL(`ModuleNotFoundError`)

- [ ] **Step 3: events.py を実装する**

```python
"""MatchEvent発行(outbox — design §2.2A)。

match_eventsへのINSERTがM1時点の発行の実体(Pub/SubリレーはM2-1)。Intent
保存と同一トランザクションで呼ぶ(原子性 — 保存が成功した行に必ずEvent行が
伴う)。event_typeの文字列はembedding_completed(05 §2唯一の確定文字列)の
過去分詞形に揃えた(design §2.2・§6-4。DB永続値のためM2設計が引き継ぐ)。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

EVENT_CREATED = "created"
EVENT_UPDATED = "updated"
EVENT_DELETED = "deleted"
EVENT_EXPIRED = "expired"  # 発行経路はM3-3(expiry_sweeper)
EVENT_SCHEDULED = "scheduled"  # 発行経路はM2以降(§2.11)

_INSERT_EVENT = text("""
    INSERT INTO match_events
        (event_type, source_intent_id, payload, status, created_at)
    VALUES
        (:event_type, :source_intent_id, CAST(:payload AS jsonb), 'pending',
         :created_at)
""")


async def insert_match_event(
    conn: AsyncConnection,
    *,
    event_type: str,
    intent_id: uuid.UUID,
    version: int,
    now: datetime,
) -> None:
    """payloadは{"version": N}のみ(06 §9。WorkerはIntentを再読込して使う)。"""
    await conn.execute(
        _INSERT_EVENT,
        {
            "event_type": event_type,
            "source_intent_id": intent_id,
            "payload": json.dumps({"version": version}),
            "created_at": now,
        },
    )
```

- [ ] **Step 4: 実行して通過を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_events.py -v`
Expected: PASS(全件)

- [ ] **Step 5: lint してコミット**

```bash
make lint
git add backend/src/latch/intents/events.py backend/tests/unit/intents/test_events.py
git commit -m "feat(intents): match_events outbox発行を追加"
```

## 7.5 Task 5: IntentService.create(active/draft・検証切替)

**Files:**
- Modify: `backend/src/latch/intents/service.py`(ファイル末尾へ追記。既存parse資産は無変更。モジュール冒頭のimport節へ追記)
- Test: `backend/tests/unit/intents/test_intents_service.py`(新規作成。共通スタブはこのファイルで定義し、Task 7〜9でも使い回す)

**Interfaces:**
- Consumes: Task 1〜4の例外・`StructuredIntentInput`・`IntentStore`/`IntentRow`/`UserRow`・`resolve_for_active/resolve_for_draft`・`insert_match_event`/`EVENT_*`・`users/service.age_years`・`geo/service.Geofeature`
- Produces: `IntentService(clock, store, uow, reader, geocoder)` — `create(*, auth_provider, auth_subject, raw_text, status, structured_intent) -> IntentRow`(以後のタスクで get/list/update/pause/resume/delete を追加)。`uow`/`reader` は「呼び出すとAsyncConnectionのコンテキストマネージャを返すCallable」(`engine.begin` / `engine.connect` を束ねる)
- 注意: design §2.10のIF案から2点を計画で確定する(報告ファイル§4へ記録): (1) `make_intent_service(*, clock, engine)`(settings引数なし — 利用する設定がないため)、(2) `ResolvedColumns.raw_text` フィールドと `columns_from_row`(draft再保存の省略時保持)

- [ ] **Step 1: service.py のimport節へ追記する**

`from __future__ import annotations` の既存import群へ次を追加(既存行は変更しない。ruff isort が順序を検査するので `cd backend && uv run ruff format . && uv run ruff check --fix .` を当てて整える)。追記後のimport節の全体形(既存の `ParserOutput`・`build_llm_gateway`・`Settings` 等も含む):

```python
import base64
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError

from latch.core.clock import Clock
from latch.geo.service import Geofeature
from latch.intents.errors import (
    DependencyUnavailableError,
    ForbiddenError,
    GeocodingFailedError,
    IntentNotFoundError,
    IntentValidationError,
    IntentsError,
    InvalidTransitionError,
    LLMUnavailableError,
    UnderAgeError,
    UnstructurableError,
)
from latch.intents.events import (
    EVENT_CREATED,
    EVENT_DELETED,
    EVENT_UPDATED,
    insert_match_event,
)
from latch.intents.intent_input import StructuredIntentInput
from latch.intents.mapping import (
    ResolvedColumns,
    columns_from_row,
    differs_from_row,
    resolve_for_active,
    resolve_for_draft,
)
from latch.intents.schema import WARNING_MESSAGE_NG_DOWNGRADED, ParserOutput
from latch.intents.store import IntentRow, IntentStore, UserRow
from latch.llm.gateway import build_llm_gateway
from latch.settings import Settings
from latch.users.service import age_years

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
```

(注意点2つ — 実機検証で確認済み: (1) `AbstractAsyncContextManager` は **`contextlib`** からimportする(`collections.abc` には存在しない)。型エイリアス `UnitOfWork` の右辺は実行時評価されるため実importが必須。(2) `AsyncConnection`/`AsyncEngine` は TYPE_CHECKING でよい(アノテーションとエイリアス内の文字列参照のみ))

- [ ] **Step 2: 失敗する試験を書く(共通スタブ+create系)**

`backend/tests/unit/intents/test_intents_service.py` を新規作成:

```python
"""IntentServiceユースケース(design §4.1-4)。スタブstore・スタブgeocoder・
FakeClock・FakeUowで決定的にdesign §2.2表の全操作を網羅する(Task 5〜9で
段階的に追記)。uowとreaderは別々のFakeConnを返し、Event INSERTが書き込み
トランザクション内で呼ばれたことを検証できるようにする。
"""

import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.geo.service import Geofeature
from latch.intents import events as events_mod
from latch.intents.errors import (
    DependencyUnavailableError,
    ForbiddenError,
    GeocodingFailedError,
    IntentNotFoundError,
    IntentValidationError,
    InvalidTransitionError,
    UnderAgeError,
)
from latch.intents.intent_input import StructuredIntentInput
from latch.intents.service import IntentService, encode_cursor
from latch.intents.store import IntentRow, UserRow

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-27 21:00
USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
OTHER_USER_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
START = datetime(2026, 9, 27, 21, 0, 0, tzinfo=UTC)  # NOW+9h(active妥当値)
TENMONKAN = Geofeature(
    source="osm_poi",
    kind="amenity",
    name="天文館",
    city_name=None,
    pref_name=None,
    lon=130.5581,
    lat=31.5965,
)


class FakeConn:
    def __init__(self):
        self.executes = []

    async def execute(self, stmt, params=None):
        self.executes.append((stmt, params))


class FakeCtx:
    """uow/reader用(engine.begin / engine.connect と同じ形のCallable)。"""

    def __init__(self, conn: FakeConn):
        self.conn = conn
        self.entries = 0

    def __call__(self):
        self.entries += 1
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


_UNSET = object()  # user_row=None(404試験)とデフォルト(26歳)を区別する


class StubStore:
    """IntentStoreのテストスタブ(結果を仕込む・呼び出しを記録する)。"""

    def __init__(self, *, user_row=_UNSET, rows=None, page=None):
        self.user_row = (
            UserRow(id=USER_ID, birth_date=date(2000, 1, 1))  # 26歳
            if user_row is _UNSET
            else user_row
        )
        self.rows = dict(rows or {})
        self.page = list(page or [])
        self.inserts = []
        self.updates = []
        self.status_updates = []
        self.list_calls = []
        self.next_id = uuid.uuid4()

    async def fetch_user_row(self, provider, subject):
        return self.user_row

    async def fetch(self, conn, intent_id):
        return self.rows.get(intent_id)

    async def fetch_for_update(self, conn, intent_id):
        return self.rows.get(intent_id)

    async def insert(self, conn, cols, *, user_id, status, now):
        self.inserts.append(
            {"cols": cols, "user_id": user_id, "status": status, "now": now}
        )
        return self.next_id

    async def update(
        self, conn, intent_id, cols, *, status, version, now, expected_status
    ):
        self.updates.append(
            {
                "intent_id": intent_id,
                "cols": cols,
                "status": status,
                "version": version,
                "now": now,
                "expected_status": expected_status,
            }
        )
        return 1

    async def update_status(
        self, conn, intent_id, *, status, version, now, expected_status
    ):
        self.status_updates.append(
            {
                "intent_id": intent_id,
                "status": status,
                "version": version,
                "now": now,
                "expected_status": expected_status,
            }
        )
        return 1

    async def list_page(self, conn, user_id, *, status, before, limit):
        self.list_calls.append(
            {"user_id": user_id, "status": status, "before": before, "limit": limit}
        )
        return self.page[:limit]


class StubGeocoder:
    def __init__(self, feature=None):
        self.feature = feature
        self.calls = []

    async def geocode_forward(self, name):
        self.calls.append(name)
        return self.feature


def _service(*, store=None, geocoder=None, clock=None):
    uow_conn = FakeConn()
    read_conn = FakeConn()
    store = store if store is not None else StubStore()
    geocoder = geocoder if geocoder is not None else StubGeocoder(TENMONKAN)
    svc = IntentService(
        clock=clock or FakeClock(NOW),
        store=store,
        uow=FakeCtx(uow_conn),
        reader=FakeCtx(read_conn),
        geocoder=geocoder,
    )
    return svc, store, geocoder, uow_conn, read_conn


def _event_calls(conn: FakeConn):
    """uowコネクション上のEvent INSERT呼び出し(event_type, params)一覧。"""
    out = []
    for stmt, params in conn.executes:
        if stmt is events_mod._INSERT_EVENT:
            out.append((params["event_type"], params))
    return out


def _active_input(**overrides) -> StructuredIntentInput:
    data = {
        "category": {"primary": "drinking", "secondary": "焼肉"},
        "alcohol_involved": True,
        "time": {"start": START.isoformat()},
        "location": {"name": "天文館", "radius_m": 2000},
        "budget": {"max": 5000},
        "participants": {"min": 2, "max": 4},
        "visibility": "summary_only",
        "notification_level": "nearby_also",
        "expires_at": (START + timedelta(hours=3)).isoformat(),
        "soft_constraints": ["軽く飲みたい"],
        "ng_unverifiable": ["会社関係の人は避けたい"],
        "negative_constraints": [],
    }
    data.update(overrides)
    return StructuredIntentInput.model_validate(data)


def _row(**overrides) -> IntentRow:
    base = dict(
        id=uuid.uuid4(),
        user_id=USER_ID,
        category_primary="drinking",
        alcohol_involved=True,
        raw_text="原文",
        structured_data={"location_name": "天文館"},
        geo_radius_m=2000,
        budget_max=5000,
        participants_min=2,
        participants_max=4,
        visibility="hidden_until_match",
        notification_level="proposals_only",
        status="draft",
        version=1,
        time_start=START,
        time_end=None,
        expires_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(overrides)
    return IntentRow(**base)


# --- (a) POST activeハッピー(design §4.1-4a)---


async def test_post_active_inserts_and_emits_created_event_v1():
    svc, store, geo, uow_conn, _ = _service()
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="今夜天文館で飲みたい",
        status="active",
        structured_intent=_active_input(),
    )
    assert row.status == "active"
    assert row.version == 1
    assert row.expires_at is not None  # 補完済み(expires_at指定値)
    assert len(store.inserts) == 1
    ins = store.inserts[0]
    assert ins["user_id"] == USER_ID
    assert ins["status"] == "active"
    assert ins["cols"].geo_lon == TENMONKAN.lon  # ジオコーディング結果
    assert ins["cols"].geo_lat == TENMONKAN.lat
    assert geo.calls == ["天文館"]
    evs = _event_calls(uow_conn)
    assert len(evs) == 1
    etype, params = evs[0]
    assert etype == "created"
    assert json.loads(params["payload"]) == {"version": 1}
    assert params["status"] == "pending"
    assert params["created_at"] == NOW


async def test_post_active_completes_defaults_in_saved_columns():
    """補完消費: time_end+3h・radius既定・expires_at補完(§4.1-2aと対の確認)。"""
    from latch.intents.completion import default_time_end, nearest_expires_at

    inp = _active_input(
        time={"start": START.isoformat()},
        location={"name": "天文館"},
        expires_at=None,
    )
    svc, store, _, _, _ = _service()
    await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="active",
        structured_intent=inp,
    )
    cols = store.inserts[0]["cols"]
    assert cols.time_end == default_time_end(START)
    assert cols.expires_at == nearest_expires_at(START, NOW)
    assert cols.geo_radius_m == 1000


# --- (b) POST draft(design §4.1-4b)---


async def test_post_draft_saves_without_event_and_geocoding():
    svc, store, geo, uow_conn, _ = _service()
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="下書きだけ",
        status="draft",
        structured_intent=None,
    )
    assert row.status == "draft"
    assert row.version == 1
    cols = store.inserts[0]["cols"]
    assert cols.geo_lon is None and cols.geo_lat is None
    assert cols.time_end is None
    assert cols.expires_at is None
    assert cols.visibility == "hidden_until_match"  # draftでも格納(確定値3)
    assert _event_calls(uow_conn) == []
    assert geo.calls == []


# --- (c) 検証切替 active/draft(design §4.1-4c)---


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"category": None}, id="category-missing"),
        pytest.param({"time": None}, id="time-missing"),
        pytest.param({"location": None}, id="location-missing"),
    ],
)
async def test_active_required3_missing_rejects_but_draft_accepts(overrides):
    """同一の欠落入力がactive=422・draft=受理になる対照(確定値3・8)。"""
    svc, store, _, _, _ = _service()
    with pytest.raises(IntentValidationError) as ei:
        await svc.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=_active_input(**overrides),
        )
    assert ei.value.code == "VALIDATION_ERROR"
    assert store.inserts == []  # 保存まで到達していない
    # draftは同じ入力を受理する(時刻・ジオコーディングも行わない)
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="draft",
        structured_intent=_active_input(**overrides),
    )
    assert row.status == "draft"


async def test_active_past_time_rejected_draft_accepted():
    past = (NOW - timedelta(hours=1)).isoformat()
    svc, _, _, _, _ = _service()
    with pytest.raises(IntentValidationError):
        await svc.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=_active_input(time={"start": past}),
        )
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="draft",
        structured_intent=_active_input(time={"start": past}),
    )
    assert row.status == "draft"


async def test_under_age_drinking_rejected_but_birthday_20_accepted():
    """19歳=422 UNDER_AGE・20歳の誕生日当日=受理(08 D-10・age_years仕様)。"""
    svc19, _, _, _, _ = _service(
        store=StubStore(user_row=UserRow(id=USER_ID, birth_date=date(2006, 9, 28)))
    )
    with pytest.raises(UnderAgeError) as ei:
        await svc19.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=_active_input(),  # drinking
        )
    assert ei.value.code == "UNDER_AGE"
    svc20, _, _, _, _ = _service(
        store=StubStore(user_row=UserRow(id=USER_ID, birth_date=date(2006, 9, 27)))
    )  # JST 2026-09-27時点で20歳ちょうど
    row = await svc20.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="active",
        structured_intent=_active_input(),
    )
    assert row.status == "active"


async def test_under_age_not_checked_for_non_drinking():
    """meal等のalcohol=falseでは19歳でも受理(確定値8はalcohol=trueのみ)。"""
    svc, _, _, _, _ = _service(
        store=StubStore(user_row=UserRow(id=USER_ID, birth_date=date(2006, 9, 28)))
    )
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="active",
        structured_intent=_active_input(
            category={"primary": "meal"}, alcohol_involved=False
        ),
    )
    assert row.status == "active"


async def test_geocoding_failure_rejects_active_but_not_draft():
    """ジオコーダNone=422 GEOCODING_FAILED(draftはジオコーディングしない)。"""
    svc, store, geo, _, _ = _service(geocoder=StubGeocoder(feature=None))
    with pytest.raises(GeocodingFailedError) as ei:
        await svc.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=_active_input(),
        )
    assert ei.value.code == "GEOCODING_FAILED"
    assert store.inserts == []
    assert geo.calls == ["天文館"]
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="draft",
        structured_intent=None,
    )
    assert row.status == "draft"


async def test_unregistered_jwt_returns_404():
    """user_lookup None=404(GET /v1/users/meと同一挙動 — design §2.6)。"""
    svc, _, _, _, _ = _service(store=StubStore(user_row=None))
    with pytest.raises(IntentNotFoundError):
        await svc.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=_active_input(),
        )


async def test_store_failure_maps_to_503_dependency_unavailable():
    from latch.intents.errors import DependencyUnavailableError

    class FailingStore(StubStore):
        async def fetch_user_row(self, provider, subject):
            raise RuntimeError("db down")

    svc, _, _, _, _ = _service(store=FailingStore())
    with pytest.raises(DependencyUnavailableError):
        await svc.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=_active_input(),
        )


async def test_raw_text_and_location_never_appear_in_logs_or_errors(caplog):
    """08 §2.4: ログ・例外に本文・地名を出さない(design §4.1-7)。"""
    secret_raw = "内緒の飲み会の件"
    secret_place = "秘密の場所"
    svc, _, _, _, _ = _service(geocoder=StubGeocoder(feature=None))
    with caplog.at_level(logging.INFO):
        with pytest.raises(GeocodingFailedError) as ei:
            await svc.create(
                auth_provider="google",
                auth_subject="s",
                raw_text=secret_raw,
                status="active",
                structured_intent=_active_input(location={"name": secret_place}),
            )
    assert secret_raw not in caplog.text
    assert secret_place not in caplog.text
    assert secret_raw not in str(ei.value)
    assert secret_place not in str(ei.value)
```

- [ ] **Step 3: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_intents_service.py -v`
Expected: FAIL(`ImportError: cannot import name 'IntentService'`)

- [ ] **Step 4: service.py へ IntentService.create を実装する**

service.py の末尾(`make_intent_parse_service` の後)へ追記する:

```python
# ---------------------------------------------------------------------------
# M1 ws-3: intents CRUD(design §2.2・§2.5〜§2.10)


class SupportsForwardGeocoding(Protocol):
    """正転ジオコーディングの構造的Protocol(design §2.5)。GeoServiceと適合。

    該当なしはNone(422 GEOCODING_FAILEDへ写像はサービス側)。例外は
    DB障害のみ(サービスが503へ包む)。
    """

    async def geocode_forward(self, name: str) -> Geofeature | None: ...


UnitOfWork = Callable[[], AbstractAsyncContextManager["AsyncConnection"]]
Reader = Callable[[], AbstractAsyncContextManager["AsyncConnection"]]

_MAX_AHEAD = timedelta(days=7)  # 対象領域上限(05 §5時刻検証)


def encode_cursor(created_at: datetime, intent_id: uuid.UUID) -> str:
    """キーセットcursor(design §2.10): base64url("ISO8601|uuid")。"""
    raw = f"{created_at.isoformat()}|{intent_id}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """cursorの復元。形式不正は422 VALIDATION_ERROR(design §2.10)。"""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        ts_part, id_part = raw.split("|")
        return datetime.fromisoformat(ts_part), uuid.UUID(id_part)
    except (ValueError, UnicodeDecodeError) as exc:
        raise IntentValidationError("invalid cursor") from exc


class IntentService:
    """intents CRUDユースケース(05 §5〜§6・design §2.2のEvent発行表)。

    検証順序は形式(Pydantic・routes)→ 必須3 → 時刻 → 年齢 → ジオコーディン
    グ → 保存に固定(design §2.5)。書き込みはuow(engine.begin)のトランザク
    ション内でstore・insert_match_eventを呼び、読み取りはreaderで行う。
    """

    def __init__(
        self,
        *,
        clock: Clock,
        store: IntentStore,
        uow: UnitOfWork,
        reader: Reader,
        geocoder: SupportsForwardGeocoding,
    ) -> None:
        self._clock = clock
        self._store = store
        self._uow = uow
        self._reader = reader
        self._geocoder = geocoder

    # -- 共通の検証・参照ヘルパー --

    async def _require_user(self, provider: str, subject: str) -> UserRow:
        row = await self._store.fetch_user_row(provider, subject)
        if row is None:
            # 未登録JWT=初回登録待ち。users/meと同一挙動(design §2.6)
            raise IntentNotFoundError("user not found")
        return row

    def _require_age_20(self, user: UserRow) -> None:
        if age_years(user.birth_date, self._clock.jst_date()) < 20:
            raise UnderAgeError("under 20 years old")

    @staticmethod
    def _validate_required3(inp: StructuredIntentInput) -> None:
        if inp.category is None or inp.category.primary is None:
            raise IntentValidationError("category is required")
        if inp.time is None or inp.time.start is None:
            raise IntentValidationError("time.start is required")
        if inp.location is None or inp.location.name is None:
            raise IntentValidationError("location is required")

    @classmethod
    def _validate_times(cls, inp: StructuredIntentInput, *, now: datetime) -> None:
        """過去不可・現在+7日上限(05 §5。境界: nowちょうど/now+7日ちょうどは受理)。"""
        start = inp.time.start if inp.time else None
        if start is not None:
            if start < now:
                raise IntentValidationError("time.start is in the past")
            if start > now + cls._MAX_AHEAD:
                raise IntentValidationError("time.start exceeds 7 days")
        if inp.expires_at is not None:
            if inp.expires_at < now:
                raise IntentValidationError("expires_at is in the past")
            if inp.expires_at > now + cls._MAX_AHEAD:
                raise IntentValidationError("expires_at exceeds 7 days")

    def _resolve_active_or_raise(
        self, inp: StructuredIntentInput, *, now: datetime
    ) -> ResolvedColumns:
        self._validate_required3(inp)
        self._validate_times(inp, now=now)
        return resolve_for_active(inp, now=now)

    async def _geocode_or_raise(
        self, name: str, cols: ResolvedColumns
    ) -> ResolvedColumns:
        feature = await self._geocoder.geocode_forward(name)
        if feature is None:
            raise GeocodingFailedError("geocoding failed")
        return replace(cols, geo_lon=feature.lon, geo_lat=feature.lat)

    @staticmethod
    def _row_from_cols(
        cols: ResolvedColumns,
        *,
        intent_id: uuid.UUID,
        user_id: uuid.UUID,
        status: str,
        now: datetime,
    ) -> IntentRow:
        """書き込んだ内容からそのまま応答行を組み立てる(DB再SELECTしない)。"""
        return IntentRow(
            id=intent_id,
            user_id=user_id,
            category_primary=cols.category_primary,
            alcohol_involved=cols.alcohol_involved,
            raw_text=cols.raw_text,
            structured_data=dict(cols.structured_data),
            geo_radius_m=cols.geo_radius_m,
            budget_max=cols.budget_max,
            participants_min=cols.participants_min,
            participants_max=cols.participants_max,
            visibility=cols.visibility,
            notification_level=cols.notification_level,
            status=status,
            version=cols.version,
            time_start=cols.time_start,
            time_end=cols.time_end,
            expires_at=cols.expires_at,
            created_at=now,
            updated_at=now,
        )

    # -- POST /v1/intents --

    async def create(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        raw_text: str,
        status: str,
        structured_intent: StructuredIntentInput | None,
    ) -> IntentRow:
        try:
            return await self._create(
                auth_provider=auth_provider,
                auth_subject=auth_subject,
                raw_text=raw_text,
                status=status,
                inp=structured_intent or StructuredIntentInput(),
            )
        except IntentsError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("intents dependency unavailable") from exc

    async def _create(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        raw_text: str,
        status: str,
        inp: StructuredIntentInput,
    ) -> IntentRow:
        user = await self._require_user(auth_provider, auth_subject)
        now = self._clock.now()
        if status == "active":
            cols = self._resolve_active_or_raise(inp, now=now)
            if cols.alcohol_involved:
                self._require_age_20(user)
            cols = await self._geocode_or_raise(inp.location.name, cols)
            cols = replace(cols, raw_text=raw_text)
            async with self._uow() as conn:
                intent_id = await self._store.insert(
                    conn, cols, user_id=user.id, status="active", now=now
                )
                await insert_match_event(
                    conn,
                    event_type=EVENT_CREATED,
                    intent_id=intent_id,
                    version=cols.version,
                    now=now,
                )
            return self._row_from_cols(
                cols, intent_id=intent_id, user_id=user.id, status="active", now=now
            )
        cols = replace(resolve_for_draft(inp), raw_text=raw_text)
        async with self._uow() as conn:
            intent_id = await self._store.insert(
                conn, cols, user_id=user.id, status="draft", now=now
            )
        return self._row_from_cols(
            cols, intent_id=intent_id, user_id=user.id, status="draft", now=now
        )


def make_intent_service(
    *, clock: Clock, engine: AsyncEngine
) -> IntentService:
    """実SQL束ねてIntentServiceを構築する(design §2.10)。

    latch.geoへのimportはこのファクトリとProtocol戻り値型に限る
    (design §2.5)。保存APIは同期LLM非依存(C8)のためllm/を参照しない。
    """
    from latch.geo.service import GeoService

    return IntentService(
        clock=clock,
        store=IntentStore(engine),
        uow=engine.begin,
        reader=engine.connect,
        geocoder=GeoService(engine),
    )
```

(注: `AsyncEngine` は既存の users 流儀に合わせ `from sqlalchemy.ext.asyncio import AsyncEngine` をimportへ追加。`Geofeature` はProtocol戻り値用にトップレベルimport済みのためファクトリ内のimportは `GeoService` のみでよい — どちらも service.py 内に収まる)

- [ ] **Step 5: 実行して通過を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_intents_service.py -v`
Expected: PASS(create系全件)

- [ ] **Step 6: lint して回帰確認してコミット**

```bash
make lint && make test   # 既存(parse試験等)も全緑であること
git add backend/src/latch/intents/service.py backend/tests/unit/intents/test_intents_service.py
git commit -m "feat(intents): IntentService.createと検証切替を追加"
```

## 7.6 Task 6: 時刻検証の境界(test_time_validation.py)

**Files:**
- Test: `backend/tests/unit/intents/test_time_validation.py`(新規作成。実装はTask 5済み — 境界値のピン留め。落ちた場合はTask 5の `_validate_times` を最小修正する)

**Interfaces:**
- Consumes: `IntentService.create`(Task 5)

- [ ] **Step 1: 境界試験を書く**

```python
"""時刻検証の境界詳細(design §4.1-6)。FakeClockでnowちょうど/now-1秒/
now+7日ちょうど/now+7日+1秒の4境界とexpires_at同一境界・draftの対照を
確定する(05 §5「過去(呼び出し時点より前)」「現在+7日を超える」の字義)。
test_intents_service.py とスタブを共有しない(相互import依存を避ける —
時刻境界に必要な最小の部品のみここで定義する)。
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from latch.core.clock import FakeClock
from latch.geo.service import Geofeature
from latch.intents.errors import IntentValidationError
from latch.intents.intent_input import StructuredIntentInput
from latch.intents.service import IntentService
from latch.intents.store import UserRow

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
START = datetime(2026, 9, 27, 21, 0, 0, tzinfo=UTC)  # 妥当値(NOW+9h)

TENMONKAN = Geofeature(
    source="osm_poi",
    kind="amenity",
    name="天文館",
    city_name=None,
    pref_name=None,
    lon=130.5581,
    lat=31.5965,
)


class _Conn:
    async def execute(self, stmt, params=None):
        return None


class _Ctx:
    def __init__(self):
        self.conn = _Conn()

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _Store:
    async def fetch_user_row(self, provider, subject):
        return UserRow(
            id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
            birth_date=date(2000, 1, 1),
        )

    async def insert(self, conn, cols, *, user_id, status, now):
        return uuid.uuid4()


class _Geocoder:
    async def geocode_forward(self, name):
        return TENMONKAN


def _svc():
    ctx = _Ctx()
    return IntentService(
        clock=FakeClock(NOW),
        store=_Store(),
        uow=ctx,
        reader=ctx,
        geocoder=_Geocoder(),
    )


def _input(
    start: datetime | None,
    expires_at: datetime | None = START + timedelta(hours=3),
) -> StructuredIntentInput:
    data = {
        "category": {"primary": "meal"},
        "time": {"start": start.isoformat()} if start else None,
        "location": {"name": "天文館"},
        "expires_at": expires_at.isoformat() if expires_at else None,
    }
    return StructuredIntentInput.model_validate(data)


async def _create(svc, inp):
    return await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="active",
        structured_intent=inp,
    )


async def test_time_start_equal_now_is_accepted():
    await _create(_svc(), _input(NOW))  # 「過去(より前)」のみ422


async def test_time_start_one_second_past_is_rejected():
    with pytest.raises(IntentValidationError):
        await _create(_svc(), _input(NOW - timedelta(seconds=1)))


async def test_time_start_exactly_7days_is_accepted():
    await _create(_svc(), _input(NOW + timedelta(days=7)))  # 「超える」のみ422


async def test_time_start_7days_plus_one_second_is_rejected():
    with pytest.raises(IntentValidationError):
        await _create(_svc(), _input(NOW + timedelta(days=7, seconds=1)))


async def test_expires_at_boundaries_mirror_time_start():
    svc = _svc()
    await _create(svc, _input(START, expires_at=NOW))  # nowちょうど=受理
    await _create(svc, _input(START, expires_at=NOW + timedelta(days=7)))
    with pytest.raises(IntentValidationError):
        await _create(svc, _input(START, expires_at=NOW - timedelta(seconds=1)))
    with pytest.raises(IntentValidationError):
        await _create(
            svc,
            _input(START, expires_at=NOW + timedelta(days=7, seconds=1)),
        )


async def test_draft_accepts_past_and_far_future_times():
    """draftは形式検証のみ — 過去も+8日も受理される対照(確定値7)。"""
    svc = _svc()
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="draft",
        structured_intent=_input(NOW - timedelta(days=1), expires_at=None),
    )
    assert row.status == "draft"
    row2 = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="draft",
        structured_intent=_input(NOW + timedelta(days=8)),
    )
    assert row2.status == "draft"
```

(注: `_Store` は時刻検証の試験で実際に呼ばれるのは `fetch_user_row` と `insert`(受理側の裏取り)のみのため、この2メソッドだけ定義する。`IntentService` のコンストラクタはProtocol的構造のみ検査するため、不完全なスタブでも動作する)

- [ ] **Step 2: 実行して通過を確認(実装済みのはず)**

Run: `cd backend && uv run pytest tests/unit/intents/test_time_validation.py -v`
Expected: PASS(全件)。**落ちた場合はTask 5の `_validate_times` の境界判定(`<` と `>` の向き)を修正する — 仕様側を変えないこと**

- [ ] **Step 3: lint してコミット**

```bash
make lint
git add backend/tests/unit/intents/test_time_validation.py
git commit -m "test(intents): 時刻検証の境界をピン留め"
```

## 7.7 Task 7: IntentService.get / list(cursor)

**Files:**
- Modify: `backend/src/latch/intents/service.py`(IntentServiceクラスへ追記)
- Test: `backend/tests/unit/intents/test_intents_service.py`(追記)

**Interfaces:**
- Consumes: Task 5の IntentService・`encode_cursor`/`decode_cursor`(実装済み)
- Produces: `get(*, auth_provider, auth_subject, intent_id) -> IntentRow`・`list(*, auth_provider, auth_subject, status, cursor, limit) -> PageResult`。`PageResult(rows: list[IntentRow], next_cursor: str | None)`(dataclass)

- [ ] **Step 1: 失敗する試験を追記する**

`test_intents_service.py` の末尾へ追記:

```python
# --- get・list・認可・cursor(design §4.1-4h・§2.10)---


async def test_get_returns_own_intent_row():
    row = _row(status="active")
    svc, _, _, _, read_conn = _service(store=StubStore(rows={row.id: row}))
    got = await svc.get(
        auth_provider="google", auth_subject="s", intent_id=row.id
    )
    assert got.id == row.id


async def test_get_other_users_intent_forbidden():
    row = _row(user_id=OTHER_USER_ID)
    svc, _, _, _, _ = _service(store=StubStore(rows={row.id: row}))
    with pytest.raises(ForbiddenError) as ei:
        await svc.get(auth_provider="google", auth_subject="s", intent_id=row.id)
    assert ei.value.code == "FORBIDDEN"


async def test_get_missing_intent_404():
    svc, _, _, _, _ = _service()
    with pytest.raises(IntentNotFoundError):
        await svc.get(
            auth_provider="google",
            auth_subject="s",
            intent_id=uuid.uuid4(),
        )


async def test_list_builds_next_cursor_when_more_rows_exist():
    """limit+1件取得し、limit件返して余りがあればnext_cursorを組み立てる。"""
    rows = [_row(status="active"), _row(status="draft")]
    svc, store, _, _, _ = _service(store=StubStore(page=rows))
    page = await svc.list(
        auth_provider="google", auth_subject="s", status=None, cursor=None, limit=1
    )
    assert [r.id for r in page.rows] == [rows[0].id]  # created_at降順の先頭
    assert page.next_cursor == encode_cursor(rows[0].created_at, rows[0].id)
    assert store.list_calls[0]["limit"] == 2  # limit+1
    assert store.list_calls[0]["before"] is None


async def test_list_last_page_has_null_next_cursor():
    rows = [_row()]
    svc, _, _, _, _ = _service(store=StubStore(page=rows))
    page = await svc.list(
        auth_provider="google", auth_subject="s", status=None, cursor=None, limit=1
    )
    assert len(page.rows) == 1
    assert page.next_cursor is None


async def test_list_decodes_cursor_and_passes_before_to_store():
    """cursor→(created_at, id)を復元しbeforeとして渡す(design §2.10)。"""
    cursor = encode_cursor(NOW, USER_ID)
    svc, store, _, _, _ = _service()
    await svc.list(
        auth_provider="google", auth_subject="s", status="draft",
        cursor=cursor, limit=20,
    )
    call = store.list_calls[0]
    assert call["before"] == decode_cursor(cursor) == (NOW, USER_ID)
    assert call["status"] == "draft"


async def test_list_invalid_cursor_rejected_422():
    """Review Focus #4と同型: 形式不正cursorは422(他人由来の正当な形式は通る)。"""
    svc, _, _, _, _ = _service()
    with pytest.raises(IntentValidationError):
        await svc.list(
            auth_provider="google",
            auth_subject="s",
            status=None,
            cursor="!!!not-a-cursor!!!",
            limit=20,
        )


async def test_list_with_foreign_before_returns_own_rows_normally():
    """Review Focus #4: 他人のcursor位置でも自分の一覧が壊れない(user_id固定)。"""
    from latch.intents.service import encode_cursor

    foreign = encode_cursor(NOW, OTHER_USER_ID)  # 自分の行に存在しない位置
    rows = [_row(status="draft")]
    svc, _, _, _, _ = _service(store=StubStore(page=rows))
    page = await svc.list(
        auth_provider="google", auth_subject="s", status=None,
        cursor=foreign, limit=20,
    )
    assert [r.id for r in page.rows] == [rows[0].id]
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_intents_service.py -v`
Expected: 新規分のみFAIL(`AttributeError: 'IntentService' object has no attribute 'get'`)

- [ ] **Step 3: service.py のIntentServiceへ get/list を実装する**

`PageResult` はクラス定義の直前(モジュールレベル)へ、`get`/`list` は IntentService の `create` の後に追記する:

```python
@dataclass(frozen=True)
class PageResult:
    """GET /v1/intents のページ結果(routesが応答へ変換 — design §2.10)。"""

    rows: list[IntentRow]
    next_cursor: str | None
```

IntentService への追記(クラス内):

```python
    # -- GET /v1/intents/{id} --

    async def get(
        self, *, auth_provider: str, auth_subject: str, intent_id: uuid.UUID
    ) -> IntentRow:
        try:
            user = await self._require_user(auth_provider, auth_subject)
            async with self._reader() as conn:
                row = await self._store.fetch(conn, intent_id)
            if row is None:
                raise IntentNotFoundError("intent not found")
            if row.user_id != user.id:
                raise ForbiddenError("not owner")
            return row
        except IntentsError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("intents dependency unavailable") from exc

    # -- GET /v1/intents --

    async def list(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        status: str | None,
        cursor: str | None,
        limit: int,
    ) -> PageResult:
        try:
            user = await self._require_user(auth_provider, auth_subject)
            before = decode_cursor(cursor) if cursor else None
            async with self._reader() as conn:
                rows = await self._store.list_page(
                    conn, user.id, status=status, before=before, limit=limit + 1
                )
            if len(rows) > limit:
                last = rows[limit - 1]
                return PageResult(
                    rows=rows[:limit],
                    next_cursor=encode_cursor(last.created_at, last.id),
                )
            return PageResult(rows=rows, next_cursor=None)
        except IntentsError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("intents dependency unavailable") from exc
```

- [ ] **Step 4: 実行して通過を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_intents_service.py -v`
Expected: PASS

- [ ] **Step 5: lint してコミット**

```bash
make lint
git add backend/src/latch/intents/service.py backend/tests/unit/intents/test_intents_service.py
git commit -m "feat(intents): IntentService get/listとキーセットcursorを追加"
```

## 7.8 Task 8: IntentService.update(PATCH分岐・draft→active)

**Files:**
- Modify: `backend/src/latch/intents/service.py`(IntentServiceクラスへ追記)
- Test: `backend/tests/unit/intents/test_intents_service.py`(追記)

**Interfaces:**
- Consumes: Task 5・7の資産 + `columns_from_row`/`differs_from_row`(Task 3)
- Produces: `update(*, auth_provider, auth_subject, intent_id, raw_text, status, structured_intent) -> IntentRow`

- [ ] **Step 1: 失敗する試験を追記する**

`test_intents_service.py` の末尾へ追記:

```python
# --- (d) PATCH分岐(design §4.1-4d・§2.10・§2.7)---


def _draft_row_of(inp: StructuredIntentInput, raw_text: str = "原文") -> IntentRow:
    """resolve_for_draft(inp)と同一の保存列を持つdraft行(実質変更判定用)。"""
    from latch.intents.mapping import resolve_for_draft

    cols = resolve_for_draft(inp)
    return _row(
        raw_text=raw_text,
        category_primary=cols.category_primary,
        alcohol_involved=cols.alcohol_involved,
        structured_data=cols.structured_data,
        budget_max=cols.budget_max,
        participants_min=cols.participants_min,
        participants_max=cols.participants_max,
        visibility=cols.visibility,
        notification_level=cols.notification_level,
        time_start=cols.time_start,
        time_end=cols.time_end,
        expires_at=cols.expires_at,
        geo_radius_m=cols.geo_radius_m,
    )


async def _update(svc, row, *, raw_text="原文", status=None, structured=None):
    return await svc.update(
        auth_provider="google",
        auth_subject="s",
        intent_id=row.id,
        raw_text=raw_text,
        status=status,
        structured_intent=structured,
    )


async def test_patch_draft_resave_bumps_version_without_event():
    """draft再保存: version+1・Eventなし・ジオコーディングなし(確定値12)。"""
    row = _draft_row_of(_active_input())
    svc, store, geo, uow_conn, _ = _service(store=StubStore(rows={row.id: row}))
    result = await _update(svc, row, raw_text="下書き改", structured=_active_input())
    assert result.status == "draft"
    assert result.version == 2
    assert result.raw_text == "下書き改"
    assert _event_calls(uow_conn) == []
    assert geo.calls == []
    upd = store.updates[0]
    assert upd["status"] == "draft"
    assert upd["version"] == 2
    assert upd["expected_status"] == "draft"


async def test_patch_draft_resave_without_structured_keeps_columns():
    """structured_intent省略=既存の構造データを保持しraw_textのみ更新。"""
    row = _draft_row_of(_active_input(), raw_text="元の下書き")
    svc, store, _, _, _ = _service(store=StubStore(rows={row.id: row}))
    result = await _update(svc, row, raw_text="rawだけ変更", structured=None)
    assert result.raw_text == "rawだけ変更"
    cols = store.updates[0]["cols"]
    assert cols.category_primary == "drinking"  # 保持されている
    assert cols.structured_data == row.structured_data
    assert cols.version == 1  # cols.versionはstore側で上書き(ここでは素の値)


async def test_patch_draft_to_active_same_content_keeps_version_and_emits_created():
    """同一内容のactive化: version据え置き・初回投入は作成種(確定値11・14)。"""
    inp = _active_input()
    row = _draft_row_of(inp)
    svc, store, _, uow_conn, _ = _service(store=StubStore(rows={row.id: row}))
    result = await _update(
        svc, row, raw_text="原文", status="active", structured=inp
    )
    assert result.status == "active"
    assert result.version == 1  # 据え置き
    evs = _event_calls(uow_conn)
    assert len(evs) == 1
    etype, params = evs[0]
    assert etype == "created"
    assert json.loads(params["payload"]) == {"version": 1}
    upd = store.updates[0]
    assert upd["status"] == "active"
    assert upd["expected_status"] == "draft"
    assert upd["cols"].geo_lon == TENMONKAN.lon  # active化でジオコーディング


async def test_patch_draft_to_active_changed_content_bumps_version():
    """内容変更を伴うactive化: version+1・created Event version=2(確定値11・14)。"""
    inp = _active_input()
    row = _draft_row_of(inp)
    changed = _active_input(participants={"min": 3, "max": 4})
    svc, _, _, uow_conn, _ = _service(store=StubStore(rows={row.id: row}))
    result = await _update(
        svc, row, raw_text="原文", status="active", structured=changed
    )
    assert result.version == 2
    etype, params = _event_calls(uow_conn)[0]
    assert etype == "created"
    assert json.loads(params["payload"]) == {"version": 2}


async def test_patch_draft_to_active_failing_validation_keeps_draft():
    """検証不通なら422でstatusはdraftのまま(確定値11)。store.update不呼び出し。"""
    past = (NOW - timedelta(hours=1)).isoformat()
    row = _row(status="draft")
    svc, store, _, _, _ = _service(store=StubStore(rows={row.id: row}))
    with pytest.raises(IntentValidationError):
        await _update(
            svc,
            row,
            status="active",
            structured=_active_input(time={"start": past}),
        )
    assert store.updates == []


async def test_patch_draft_to_active_requires_full_structured():
    """Review Focus #2: structured_intent省略のactive化は422(全量必須)。"""
    row = _row(status="draft")
    svc, _, _, _, _ = _service(store=StubStore(rows={row.id: row}))
    with pytest.raises(IntentValidationError):
        await _update(svc, row, status="active", structured=None)


async def test_patch_active_content_update_bumps_version_and_emits_updated():
    """active内容更新(全置換): version+1・updated Event(確定値13)。"""
    row = _row(status="active")
    svc, store, _, uow_conn, _ = _service(store=StubStore(rows={row.id: row}))
    result = await _update(
        svc,
        row,
        raw_text="変更後",
        structured=_active_input(location={"name": "天文館", "radius_m": 1500}),
    )
    assert result.status == "active"
    assert result.version == 2
    assert result.raw_text == "変更後"
    etype, params = _event_calls(uow_conn)[0]
    assert etype == "updated"
    assert json.loads(params["payload"]) == {"version": 2}
    assert store.updates[0]["expected_status"] == "active"


async def test_patch_active_requires_structured_intent():
    """active/paused更新でのstructured_intent省略は422(全置換契約・確定値6)。"""
    row = _row(status="active")
    svc, store, _, _, _ = _service(store=StubStore(rows={row.id: row}))
    with pytest.raises(IntentValidationError):
        await _update(svc, row, structured=None)
    assert store.updates == []


async def test_patch_active_to_draft_rejected():
    """逆遷移不可(確定値11)。"""
    row = _row(status="active")
    svc, _, _, _, _ = _service(store=StubStore(rows={row.id: row}))
    with pytest.raises(IntentValidationError):
        await _update(
            svc, row, status="draft", structured=_active_input()
        )


async def test_patch_paused_content_update_keeps_paused_and_emits_updated():
    """paused行への内容更新はactiveと同一扱い(status は paused のまま)。"""
    row = _row(status="paused")
    svc, _, _, uow_conn, _ = _service(store=StubStore(rows={row.id: row}))
    result = await _update(svc, row, structured=_active_input())
    assert result.status == "paused"
    assert result.version == 2
    assert _event_calls(uow_conn)[0][0] == "updated"


async def test_patch_matched_intent_rejected():
    """matched行へのPATCHは422 InvalidTransitionError(code=VALIDATION_ERROR)。"""
    row = _row(status="matched")
    svc, store, _, _, _ = _service(store=StubStore(rows={row.id: row}))
    with pytest.raises(InvalidTransitionError):
        await _update(svc, row, structured=_active_input())
    assert store.updates == []


async def test_patch_category_change_to_drinking_under_age_rejected():
    """Review Focus #1: meal→drinking変更+19歳=422 UNDER_AGE(確定値13)。"""
    row = _row(status="active", category_primary="meal", alcohol_involved=False)
    svc19, _, _, _, _ = _service(
        store=StubStore(
            user_row=UserRow(id=USER_ID, birth_date=date(2006, 9, 28)),
            rows={row.id: row},
        )
    )
    with pytest.raises(UnderAgeError):
        await _update(
            svc19,
            row,
            structured=_active_input(category={"primary": "drinking"}),
        )


async def test_patch_other_users_intent_forbidden():
    row = _row(user_id=OTHER_USER_ID, status="active")
    svc, store, _, _, _ = _service(store=StubStore(rows={row.id: row}))
    with pytest.raises(ForbiddenError):
        await _update(svc, row, structured=_active_input())
    assert store.updates == []


async def test_patch_missing_intent_404():
    missing = uuid.uuid4()
    svc, _, _, _, _ = _service()
    with pytest.raises(IntentNotFoundError):
        await svc.update(
            auth_provider="google",
            auth_subject="s",
            intent_id=missing,
            raw_text="r",
            status=None,
            structured_intent=_active_input(),
        )
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_intents_service.py -v`
Expected: 新規分のみFAIL(`AttributeError: … 'update'`)

- [ ] **Step 3: service.py のIntentServiceへ update を実装する**

IntentService への追記(クラス内・`list` の後):

```python
    # -- PATCH /v1/intents/{id}(design §2.10のPATCH分岐・§2.7)--

    async def update(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        intent_id: uuid.UUID,
        raw_text: str,
        status: str | None,
        structured_intent: StructuredIntentInput | None,
    ) -> IntentRow:
        try:
            return await self._update(
                auth_provider=auth_provider,
                auth_subject=auth_subject,
                intent_id=intent_id,
                raw_text=raw_text,
                status=status,
                structured_intent=structured_intent,
            )
        except IntentsError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("intents dependency unavailable") from exc

    async def _update(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        intent_id: uuid.UUID,
        raw_text: str,
        status: str | None,
        structured_intent: StructuredIntentInput | None,
    ) -> IntentRow:
        user = await self._require_user(auth_provider, auth_subject)
        now = self._clock.now()
        async with self._uow() as conn:
            row = await self._store.fetch_for_update(conn, intent_id)
            if row is None:
                raise IntentNotFoundError("intent not found")
            if row.user_id != user.id:
                raise ForbiddenError("not owner")
            target = status if status is not None else row.status
            if row.status == "draft":
                return await self._update_draft(
                    conn,
                    row=row,
                    target=target,
                    raw_text=raw_text,
                    inp=structured_intent,
                    user=user,
                    now=now,
                )
            if row.status in ("active", "paused"):
                return await self._update_active(
                    conn,
                    row=row,
                    target=target,
                    raw_text=raw_text,
                    inp=structured_intent,
                    user=user,
                    now=now,
                )
            raise InvalidTransitionError("intent is not editable")

    async def _update_active(
        self,
        conn,
        *,
        row: IntentRow,
        target: str,
        raw_text: str,
        inp: StructuredIntentInput | None,
        user: UserRow,
        now: datetime,
    ) -> IntentRow:
        """active/paused行の内容更新(全置換・全検証・version+1・updated Event)。"""
        if target == "draft":
            raise IntentValidationError("active to draft is not allowed")
        if inp is None:
            raise IntentValidationError("structured_intent is required")
        cols = self._resolve_active_or_raise(inp, now=now)
        if cols.alcohol_involved:
            self._require_age_20(user)
        cols = await self._geocode_or_raise(inp.location.name, cols)
        cols = replace(cols, raw_text=raw_text)
        new_version = row.version + 1
        count = await self._store.update(
            conn,
            row.id,
            cols,
            status=row.status,
            version=new_version,
            now=now,
            expected_status=row.status,
        )
        if count == 0:
            raise InvalidTransitionError("intent status changed")
        await insert_match_event(
            conn,
            event_type=EVENT_UPDATED,
            intent_id=row.id,
            version=new_version,
            now=now,
        )
        return self._row_with_version(
            self._row_from_cols(
                cols,
                intent_id=row.id,
                user_id=row.user_id,
                status=row.status,
                now=now,
            ),
            new_version,
        )

    async def _update_draft(
        self,
        conn,
        *,
        row: IntentRow,
        target: str,
        raw_text: str,
        inp: StructuredIntentInput | None,
        user: UserRow,
        now: datetime,
    ) -> IntentRow:
        """draft再保存(検証なし)とdraft→active化(全検証 — design §2.10)。"""
        if target != "active":
            # 下書き再保存: structured_intent省略=既存保持・送れば全置換
            if inp is None:
                cols = replace(columns_from_row(row), raw_text=raw_text)
            else:
                cols = replace(resolve_for_draft(inp), raw_text=raw_text)
            new_version = row.version + 1
            count = await self._store.update(
                conn,
                row.id,
                cols,
                status="draft",
                version=new_version,
                now=now,
                expected_status="draft",
            )
            if count == 0:
                raise InvalidTransitionError("intent status changed")
            return self._row_with_version(
                self._row_from_cols(
                    cols,
                    intent_id=row.id,
                    user_id=row.user_id,
                    status="draft",
                    now=now,
                ),
                new_version,
            )
        # draft→active化: 全量必須・全検証(不通なら422でdraft据え置き)
        if inp is None:
            raise IntentValidationError("structured_intent is required")
        cols = self._resolve_active_or_raise(inp, now=now)
        if cols.alcohol_involved:
            self._require_age_20(user)
        cols = await self._geocode_or_raise(inp.location.name, cols)
        cols = replace(cols, raw_text=raw_text)
        # version判定はdraft表現での列比較(§2.7): 補完込みで比較すると
        # 同一内容でも常に相違となるため、リクエストをdraft用resolveした結果と比較
        draft_cols = replace(resolve_for_draft(inp), raw_text=raw_text)
        new_version = row.version + 1 if differs_from_row(draft_cols, row) else row.version
        count = await self._store.update(
            conn,
            row.id,
            cols,
            status="active",
            version=new_version,
            now=now,
            expected_status="draft",
        )
        if count == 0:
            raise InvalidTransitionError("intent status changed")
        await insert_match_event(
            conn,
            event_type=EVENT_CREATED,  # 初回投入は作成種(06 §9-0・確定値14)
            intent_id=row.id,
            version=new_version,
            now=now,
        )
        return self._row_with_version(
            self._row_from_cols(
                cols,
                intent_id=row.id,
                user_id=row.user_id,
                status="active",
                now=now,
            ),
            new_version,
        )

    @staticmethod
    def _row_with_version(row: IntentRow, version: int) -> IntentRow:
        return replace(row, version=version)
```

(注: `_row_from_cols` は `cols.version`(=1)をそのまま行へ入れるため、versionを上書きした行を返すのに `replace(row, version=…)`(`_row_with_version`)を使う)

- [ ] **Step 4: 実行して通過を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_intents_service.py -v`
Expected: PASS

- [ ] **Step 5: lint してコミット**

```bash
make lint
git add backend/src/latch/intents/service.py backend/tests/unit/intents/test_intents_service.py
git commit -m "feat(intents): IntentService.update(PATCH分岐・draft→active)を追加"
```

## 7.9 Task 9: IntentService.pause / resume / delete

**Files:**
- Modify: `backend/src/latch/intents/service.py`(IntentServiceクラスへ追記)
- Test: `backend/tests/unit/intents/test_intents_service.py`(追記)

**Interfaces:**
- Produces: `pause(*, auth_provider, auth_subject, intent_id) -> IntentRow`・`resume(…) -> IntentRow`・`delete(…) -> None`

- [ ] **Step 1: 失敗する試験を追記する**

`test_intents_service.py` の末尾へ追記:

```python
# --- (e)(f)(g) pause・resume・delete(design §4.1-4e〜g・§2.8)---


async def _transition(svc, intent_id):
    return await svc.pause(
        auth_provider="google", auth_subject="s", intent_id=intent_id
    )


async def test_pause_active_sets_paused_keeps_version_no_event():
    row = _row(status="active")
    svc, store, _, uow_conn, _ = _service(store=StubStore(rows={row.id: row}))
    result = await svc.pause(
        auth_provider="google", auth_subject="s", intent_id=row.id
    )
    assert result.status == "paused"
    assert result.version == 1  # 不変
    assert _event_calls(uow_conn) == []  # 発行規定なし(§2.2表)
    upd = store.status_updates[0]
    assert upd["status"] == "paused"
    assert upd["version"] == 1
    assert upd["expected_status"] == "active"


async def test_pause_draft_rejected():
    row = _row(status="draft")
    svc, store, _, _, _ = _service(store=StubStore(rows={row.id: row}))
    with pytest.raises(InvalidTransitionError):
        await _transition(svc, row.id)
    assert store.status_updates == []


async def test_resume_paused_bumps_version_and_emits_updated():
    """resume: version+1・updated Event(確定値15 — idempotencyキー衝突回避)。"""
    row = _row(status="paused")
    svc, store, _, uow_conn, _ = _service(store=StubStore(rows={row.id: row}))
    result = await svc.resume(
        auth_provider="google", auth_subject="s", intent_id=row.id
    )
    assert result.status == "active"
    assert result.version == 2
    etype, params = _event_calls(uow_conn)[0]
    assert etype == "updated"
    assert json.loads(params["payload"]) == {"version": 2}
    assert store.status_updates[0]["expected_status"] == "paused"


async def test_resume_active_rejected():
    row = _row(status="active")
    svc, _, _, _, _ = _service(store=StubStore(rows={row.id: row}))
    with pytest.raises(InvalidTransitionError):
        await svc.resume(auth_provider="google", auth_subject="s", intent_id=row.id)


async def test_delete_active_transitions_cancelled_with_deleted_event():
    row = _row(status="active")
    svc, store, _, uow_conn, _ = _service(store=StubStore(rows={row.id: row}))
    result = await svc.delete(
        auth_provider="google", auth_subject="s", intent_id=row.id
    )
    assert result is None
    upd = store.status_updates[0]
    assert upd["status"] == "cancelled"
    assert upd["version"] == 1  # 不変
    etype, params = _event_calls(uow_conn)[0]
    assert etype == "deleted"
    assert json.loads(params["payload"]) == {"version": 1}


async def test_delete_draft_and_paused_also_cancelled():
    for status in ("draft", "paused"):
        row = _row(status=status)
        svc, store, _, _, _ = _service(store=StubStore(rows={row.id: row}))
        await svc.delete(auth_provider="google", auth_subject="s", intent_id=row.id)
        assert store.status_updates[0]["status"] == "cancelled"


async def test_delete_matched_rejected():
    row = _row(status="matched")
    svc, store, _, _, _ = _service(store=StubStore(rows={row.id: row}))
    with pytest.raises(InvalidTransitionError):
        await svc.delete(auth_provider="google", auth_subject="s", intent_id=row.id)
    assert store.status_updates == []


async def test_delete_twice_second_rejected():
    """Review Focus #3: cancelled行への再DELETEは422(遷移表にない)。"""
    row = _row(status="cancelled")
    svc, store, _, _, _ = _service(store=StubStore(rows={row.id: row}))
    with pytest.raises(InvalidTransitionError):
        await svc.delete(auth_provider="google", auth_subject="s", intent_id=row.id)
    assert store.status_updates == []


async def test_pause_other_users_intent_forbidden():
    row = _row(status="active", user_id=OTHER_USER_ID)
    svc, store, _, _, _ = _service(store=StubStore(rows={row.id: row}))
    with pytest.raises(ForbiddenError):
        await _transition(svc, row.id)
    assert store.status_updates == []


async def test_pause_missing_intent_404():
    svc, _, _, _, _ = _service()
    with pytest.raises(IntentNotFoundError):
        await _transition(svc, uuid.uuid4())
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_intents_service.py -v`
Expected: 新規分のみFAIL(`AttributeError: … 'pause'`)

- [ ] **Step 3: service.py のIntentServiceへ pause/resume/delete を実装する**

IntentService への追記(クラス内・`update` の後):

```python
    # -- pause / resume / DELETE(§2.8・05 §6遷移表)--

    async def pause(
        self, *, auth_provider: str, auth_subject: str, intent_id: uuid.UUID
    ) -> IntentRow:
        return await self._transition(
            auth_provider=auth_provider,
            auth_subject=auth_subject,
            intent_id=intent_id,
            allowed_from=("active",),
            new_status="paused",
            version_delta=0,
            event_type=None,  # 発行規定なし(pausedはLayer 1対象外)
        )

    async def resume(
        self, *, auth_provider: str, auth_subject: str, intent_id: uuid.UUID
    ) -> IntentRow:
        return await self._transition(
            auth_provider=auth_provider,
            auth_subject=auth_subject,
            intent_id=intent_id,
            allowed_from=("paused",),
            new_status="active",
            version_delta=1,  # 確定値15: キー衝突回避のため必ず+1
            event_type=EVENT_UPDATED,
        )

    async def delete(
        self, *, auth_provider: str, auth_subject: str, intent_id: uuid.UUID
    ) -> None:
        await self._transition(
            auth_provider=auth_provider,
            auth_subject=auth_subject,
            intent_id=intent_id,
            allowed_from=("draft", "active", "paused"),
            new_status="cancelled",  # 物理削除しない(05 §6・M3-8参照)
            version_delta=0,
            event_type=EVENT_DELETED,
        )

    async def _transition(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        intent_id: uuid.UUID,
        allowed_from: tuple[str, ...],
        new_status: str,
        version_delta: int,
        event_type: str | None,
    ) -> IntentRow:
        try:
            user = await self._require_user(auth_provider, auth_subject)
            now = self._clock.now()
            async with self._uow() as conn:
                row = await self._store.fetch_for_update(conn, intent_id)
                if row is None:
                    raise IntentNotFoundError("intent not found")
                if row.user_id != user.id:
                    raise ForbiddenError("not owner")
                if row.status not in allowed_from:
                    raise InvalidTransitionError("invalid status transition")
                new_version = row.version + version_delta
                count = await self._store.update_status(
                    conn,
                    intent_id,
                    status=new_status,
                    version=new_version,
                    now=now,
                    expected_status=row.status,
                )
                if count == 0:
                    raise InvalidTransitionError("invalid status transition")
                if event_type is not None:
                    await insert_match_event(
                        conn,
                        event_type=event_type,
                        intent_id=intent_id,
                        version=new_version,
                        now=now,
                    )
                return replace(
                    row,
                    status=new_status,
                    version=new_version,
                    updated_at=now,
                )
        except IntentsError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("intents dependency unavailable") from exc
```

- [ ] **Step 4: 実行して通過を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_intents_service.py -v`
Expected: PASS

- [ ] **Step 5: lint してコミット**

```bash
make lint
git add backend/src/latch/intents/service.py backend/tests/unit/intents/test_intents_service.py
git commit -m "feat(intents): pause/resume/deleteを追加"
```

## 7.10 Task 10: CRUDルーティング(routes.py)

**Files:**
- Modify: `backend/src/latch/intents/routes.py`(ファイル末尾へ追記。parse_router・既存モデルは無変更)
- Test: `backend/tests/unit/intents/test_crud_routes.py`

**Interfaces:**
- Consumes: `IntentService`(Task 5〜9)・`IntentCreateRequest`/`IntentPatchRequest`(Task 2)・`to_response_structured`(Task 3)・`require_authenticated`
- Produces: `intents_crud_router`(7エンドポイント)・`get_intent_service`(app.state.intent_serviceへのアクセス)・`make_intent_out(row) -> IntentOut`。応答モデル一式(`IntentOut`・`IntentEnvelope`・`IntentListResponse`)

- [ ] **Step 1: 失敗する試験を書く**

`backend/tests/unit/intents/test_crud_routes.py` を新規作成:

```python
"""intents CRUDルーティング(design §4.1-5)。スタブサービス注入+
dependency_overrides。7エンドポイントの応答形状・envelope・認証を検証。
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.errors import UnauthenticatedError
from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.intents.errors import (
    ForbiddenError,
    GeocodingFailedError,
    IntentNotFoundError,
    UnderAgeError,
)
from latch.intents.service import PageResult
from latch.intents.store import IntentRow
from latch.main import create_app

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
INTENT_ID = uuid.uuid4()


def _claims() -> AccessTokenClaims:
    return AccessTokenClaims(
        auth_provider="google",
        auth_subject="sub-1",
        jti="jti-1",
        sid="sid-1",
        iat=NOW,
        exp=NOW + timedelta(hours=1),
    )


def _row(**overrides) -> IntentRow:
    base = dict(
        id=INTENT_ID,
        user_id=uuid.uuid4(),
        category_primary="drinking",
        alcohol_involved=True,
        raw_text="今夜天文館で飲みたい",
        structured_data={
            "category_secondary": "焼肉",
            "soft_constraints": [
                {"text": "軽く飲みたい", "downgraded_from_ng": False},
                {"text": "会社関係の人は避けたい", "downgraded_from_ng": True},
            ],
            "negative_constraints": [],
            "time_flexibility_minutes": None,
            "location_flexibility": None,
            "location_name": "天文館",
        },
        geo_radius_m=2000,
        budget_max=5000,
        participants_min=2,
        participants_max=4,
        visibility="hidden_until_match",
        notification_level="proposals_only",
        status="active",
        version=1,
        time_start=datetime(2026, 9, 27, 21, 0, 0, tzinfo=UTC),
        time_end=datetime(2026, 9, 28, 0, 0, 0, tzinfo=UTC),
        expires_at=datetime(2026, 9, 27, 23, 30, 0, tzinfo=UTC),
        created_at=NOW,
        updated_at=NOW,
    )
    base.update(overrides)
    return IntentRow(**base)


class StubService:
    """IntentServiceのテストスタブ(引数を記録・結果を仕込む)。"""

    def __init__(self, row=None, page=None, error=None):
        self.row = row if row is not None else _row()
        self.page = page if page is not None else PageResult(
            rows=[self.row], next_cursor=None
        )
        self.error = error
        self.calls = []

    async def _handle(self, name, kwargs):
        self.calls.append((name, kwargs))
        if self.error is not None:
            raise self.error
        return self.row if name != "list" else self.page

    async def create(self, **kw):
        return await self._handle("create", kw)

    async def list(self, **kw):
        return await self._handle("list", kw)

    async def get(self, **kw):
        return await self._handle("get", kw)

    async def update(self, **kw):
        return await self._handle("update", kw)

    async def pause(self, **kw):
        return await self._handle("pause", kw)

    async def resume(self, **kw):
        return await self._handle("resume", kw)

    async def delete(self, **kw):
        return await self._handle("delete", kw)


def _client(service=None, auth_override=None):
    service = service or StubService()
    app = create_app(clock=FakeClock(NOW), intent_service=service)
    app.dependency_overrides[require_authenticated] = (
        auth_override if auth_override is not None else _claims
    )
    return app, AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _full_body():
    return {
        "raw_text": "今夜20時から天文館で軽く飲みたい",
        "status": "active",
        "structured_intent": {
            "category": {"primary": "drinking", "secondary": "焼肉"},
            "alcohol_involved": True,
            "time": {"start": "2026-09-27T21:00:00+09:00"},
            "location": {"name": "天文館", "radius_m": 2000},
            "budget": {"max": 5000, "currency": "JPY"},
            "participants": {"min": 2, "max": 4},
            "visibility": "hidden_until_match",
            "notification_level": "proposals_only",
            "expires_at": "2026-09-27T23:30:00+09:00",
            "soft_constraints": ["軽く飲みたい"],
            "ng_unverifiable": ["会社関係の人は避けたい"],
            "negative_constraints": [],
        },
    }


async def test_post_returns_201_with_intent_envelope_shape():
    service = StubService()
    app, client = _client(service)
    async with client:
        resp = await client.post("/v1/intents", json=_full_body())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == {"intent"}
    intent = body["intent"]
    assert set(intent) == {
        "id",
        "status",
        "version",
        "raw_text",
        "structured_data",
        "structured_intent",
        "expires_at",
        "created_at",
        "updated_at",
    }
    assert intent["id"] == str(INTENT_ID)
    si = intent["structured_intent"]
    assert si["location"]["name"] == "天文館"
    assert si["soft_constraints"] == ["軽く飲みたい"]
    assert si["ng_unverifiable"] == ["会社関係の人は避けたい"]
    assert intent["structured_data"]["location_name"] == "天文館"
    name, kw = service.calls[0]
    assert name == "create"
    assert kw["raw_text"] == "今夜20時から天文館で軽く飲みたい"
    assert kw["status"] == "active"
    assert kw["auth_provider"] == "google"


async def test_get_list_returns_items_and_next_cursor():
    service = StubService(
        page=PageResult(rows=[_row()], next_cursor="abc")
    )
    app, client = _client(service)
    async with client:
        resp = await client.get("/v1/intents", params={"status": "draft",
                                                       "limit": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"items", "next_cursor"}
    assert len(body["items"]) == 1
    assert body["next_cursor"] == "abc"
    name, kw = service.calls[0]
    assert name == "list"
    assert kw["status"] == "draft"
    assert kw["limit"] == 2
    assert kw["cursor"] is None


async def test_get_by_id_patch_pause_resume_return_intent_envelope():
    app, client = _client(StubService())
    async with client:
        got = await client.get(f"/v1/intents/{INTENT_ID}")
        assert got.status_code == 200
        patched = await client.patch(
            f"/v1/intents/{INTENT_ID}", json=_full_body()
        )
        assert patched.status_code == 200
        paused = await client.post(f"/v1/intents/{INTENT_ID}/pause")
        assert paused.status_code == 200
        resumed = await client.post(f"/v1/intents/{INTENT_ID}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["intent"]["status"] == "active"


async def test_delete_returns_204_empty_body():
    app, client = _client(StubService())
    async with client:
        resp = await client.delete(f"/v1/intents/{INTENT_ID}")
    assert resp.status_code == 204
    assert resp.content == b""


async def test_cursor_query_param_forwarded():
    service = StubService()
    app, client = _client(service)
    async with client:
        await client.get("/v1/intents", params={"cursor": "xyz"})
    assert service.calls[0][1]["cursor"] == "xyz"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        pytest.param(IntentNotFoundError("x"), 404, "NOT_FOUND", id="404"),
        pytest.param(ForbiddenError("x"), 403, "FORBIDDEN", id="403"),
        pytest.param(UnderAgeError("x"), 422, "UNDER_AGE", id="under-age"),
        pytest.param(GeocodingFailedError("x"), 422, "GEOCODING_FAILED",
                     id="geocoding"),
    ],
)
async def test_domain_errors_map_to_envelope(error, status, code):
    app, client = _client(StubService(error=error))
    async with client:
        resp = await client.post("/v1/intents", json=_full_body())
    assert resp.status_code == status
    assert resp.json()["error"]["code"] == code


async def test_unauthenticated_returns_401_envelope():
    async def _raise() -> AccessTokenClaims:
        raise UnauthenticatedError("missing bearer token")

    app, client = _client(StubService(), auth_override=_raise)
    async with client:
        for method, path in [
            ("post", "/v1/intents"),
            ("get", "/v1/intents"),
            ("get", f"/v1/intents/{INTENT_ID}"),
            ("patch", f"/v1/intents/{INTENT_ID}"),
            ("delete", f"/v1/intents/{INTENT_ID}"),
            ("post", f"/v1/intents/{INTENT_ID}/pause"),
            ("post", f"/v1/intents/{INTENT_ID}/resume"),
        ]:
            kwargs = {"json": _full_body()} if method in ("post", "patch") else {}
            resp = await getattr(client, method)(path, **kwargs)
            assert resp.status_code == 401, (method, path)
            assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("limit", [0, 101])
async def test_list_limit_out_of_range_returns_422(limit):
    """Review Focus #5: limit=0/101は422(05 §5ページネーション規定)。"""
    app, client = _client(StubService())
    async with client:
        resp = await client.get("/v1/intents", params={"limit": limit})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_list_invalid_status_filter_returns_422():
    """Review Focus #5: status=drafty(綴り不正)は422 Literal検証。"""
    app, client = _client(StubService())
    async with client:
        resp = await client.get("/v1/intents", params={"status": "drafty"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_post_raw_text_301_chars_returns_422():
    body = _full_body()
    body["raw_text"] = "あ" * 301
    app, client = _client(StubService())
    async with client:
        resp = await client.post("/v1/intents", json=body)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_raw_text_never_appears_in_logs(caplog):
    """08 §2.4: ログに本文を出さない。"""
    secret = "内緒の飲み会の件"
    body = _full_body()
    body["raw_text"] = secret
    app, client = _client(StubService())
    async with client:
        with caplog.at_level(logging.INFO):
            resp = await client.post("/v1/intents", json=body)
    assert resp.status_code == 201
    assert secret not in caplog.text
```

- [ ] **Step 2: 実行して失敗を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_crud_routes.py -v`
Expected: FAIL(create_appに `intent_service` 引数がないため `TypeError`、またはルータ不在で404)

- [ ] **Step 3: routes.py へ追記する**

routes.py の既存import節へ追記(既存行は変更しない):

```python
import uuid
from datetime import datetime
from typing import Literal

from fastapi import Query, Response
from latch.intents.intent_input import (
    IntentCreateRequest,
    IntentPatchRequest,
)
from latch.intents.mapping import to_response_structured
from latch.intents.service import IntentService, PageResult
from latch.intents.store import IntentRow
```

(注: `Response` は `from fastapi import Response`。`BaseModel`・`Field`・`APIRouter`・`Depends`・`Request`・`Annotated` は既存import済み)

ファイル末尾へ追記:

```python
# ---------------------------------------------------------------------------
# M1 ws-3: intents CRUD(design §2.10・05 §5)


class CategoryOut(BaseModel):
    primary: str | None = None
    secondary: str | None = None


class TimeOut(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    flexibility_minutes: None = None


class LocationOut(BaseModel):
    name: str | None = None
    radius_m: int | None = None
    flexibility: None = None


class BudgetOut(BaseModel):
    max: int | None = None
    currency: Literal["JPY"] = "JPY"


class ParticipantsOut(BaseModel):
    min: int | None = None
    max: int | None = None


class StructuredIntentOut(BaseModel):
    """応答structured_intent(リクエストと同形の再構成 — design §2.10・§6-6)。"""

    category: CategoryOut
    alcohol_involved: bool
    time: TimeOut
    location: LocationOut
    budget: BudgetOut
    participants: ParticipantsOut
    visibility: str
    notification_level: str
    expires_at: datetime | None = None
    soft_constraints: list[str]
    ng_unverifiable: list[str]
    negative_constraints: list[str]


class IntentOut(BaseModel):
    """全CRUD応答で同形(design §6-6)。座標・embeddingは返さない(出力最小化)。"""

    id: uuid.UUID
    status: str
    version: int
    raw_text: str
    structured_data: dict
    structured_intent: StructuredIntentOut
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class IntentEnvelope(BaseModel):
    intent: IntentOut


class IntentListResponse(BaseModel):
    items: list[IntentOut]
    next_cursor: str | None = None  # 次ページがなければnull(05 §5)


intents_crud_router = APIRouter(
    prefix="/v1/intents",
    tags=["intents"],
    dependencies=[Depends(require_authenticated)],  # C3(05 §5全API認証済み)
)


def get_intent_service(request: Request) -> IntentService:
    """app.state.intent_service へのアクセス(lifespanまたはテスト注入で載る)。"""
    return request.app.state.intent_service


def make_intent_out(row: IntentRow) -> IntentOut:
    return IntentOut(
        id=row.id,
        status=row.status,
        version=row.version,
        raw_text=row.raw_text,
        structured_data=row.structured_data,
        structured_intent=StructuredIntentOut.model_validate(
            to_response_structured(row)
        ),
        expires_at=row.expires_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


StatusFilter = Annotated[
    Literal["draft", "active", "paused", "matched", "expired", "cancelled"] | None,
    Query(description="statusフィルタ(未指定は全status)"),
]


@intents_crud_router.post("", status_code=201, response_model=IntentEnvelope)
async def create_intent(
    body: IntentCreateRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentService, Depends(get_intent_service)],
) -> IntentEnvelope:
    """POST /v1/intents(05 §5)。active=全検証+created Event / draft=raw_textのみ。"""
    row = await svc.create(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        raw_text=body.raw_text,
        status=body.status,
        structured_intent=body.structured_intent,
    )
    logger.info("intents.create ok status=%s", body.status)
    return IntentEnvelope(intent=make_intent_out(row))


@intents_crud_router.get("", response_model=IntentListResponse)
async def list_intents(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentService, Depends(get_intent_service)],
    status: StatusFilter = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> IntentListResponse:
    """GET /v1/intents(自Intent一覧・design §2.10キーセットcursor)。"""
    page: PageResult = await svc.list(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        status=status,
        cursor=cursor,
        limit=limit,
    )
    return IntentListResponse(
        items=[make_intent_out(r) for r in page.rows],
        next_cursor=page.next_cursor,
    )


@intents_crud_router.get("/{intent_id}", response_model=IntentEnvelope)
async def get_intent(
    intent_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentService, Depends(get_intent_service)],
) -> IntentEnvelope:
    row = await svc.get(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        intent_id=intent_id,
    )
    logger.info("intents.get ok")
    return IntentEnvelope(intent=make_intent_out(row))


@intents_crud_router.patch("/{intent_id}", response_model=IntentEnvelope)
async def patch_intent(
    intent_id: uuid.UUID,
    body: IntentPatchRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentService, Depends(get_intent_service)],
) -> IntentEnvelope:
    """PATCH /v1/intents/{id}(全置換・draft再保存・draft→active — design §2.10)。"""
    row = await svc.update(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        intent_id=intent_id,
        raw_text=body.raw_text,
        status=body.status,
        structured_intent=body.structured_intent,
    )
    logger.info("intents.update ok")
    return IntentEnvelope(intent=make_intent_out(row))


@intents_crud_router.delete("/{intent_id}", status_code=204)
async def delete_intent(
    intent_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentService, Depends(get_intent_service)],
) -> Response:
    """DELETE /v1/intents/{id}(cancelled遷移+deleted Event・物理削除しない)。"""
    await svc.delete(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        intent_id=intent_id,
    )
    logger.info("intents.delete ok")
    return Response(status_code=204)


@intents_crud_router.post("/{intent_id}/pause", response_model=IntentEnvelope)
async def pause_intent(
    intent_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentService, Depends(get_intent_service)],
) -> IntentEnvelope:
    row = await svc.pause(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        intent_id=intent_id,
    )
    logger.info("intents.pause ok")
    return IntentEnvelope(intent=make_intent_out(row))


@intents_crud_router.post("/{intent_id}/resume", response_model=IntentEnvelope)
async def resume_intent(
    intent_id: uuid.UUID,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[IntentService, Depends(get_intent_service)],
) -> IntentEnvelope:
    row = await svc.resume(
        auth_provider=claims.auth_provider,
        auth_subject=claims.auth_subject,
        intent_id=intent_id,
    )
    logger.info("intents.resume ok")
    return IntentEnvelope(intent=make_intent_out(row))
```

- [ ] **Step 4: 実行して通過を確認**

Run: `cd backend && uv run pytest tests/unit/intents/test_crud_routes.py -v`
Expected: PASS(全件)。※この時点で create_app に `intent_service` 引数が必要になるため、Step 3の後に main.py への追記(Task 11)を先に部分的に行わないと通らない場合は、main.py の変更(§7.11 Step 3)をこのステップの前に実施してよい(§7.11のステップ順に従わなくてもよい — ただしコミットは分ける)

- [ ] **Step 5: lint してコミット**

```bash
make lint
git add backend/src/latch/intents/routes.py backend/tests/unit/intents/test_crud_routes.py
git commit -m "feat(intents): intents CRUDルータ(7エンドポイント)を追加"
```

## 7.11 Task 11: アプリ統合(main.py・__init__.py)

**Files:**
- Modify: `backend/src/latch/main.py`
- Modify: `backend/src/latch/intents/__init__.py`

**Interfaces:**
- Consumes: `intents_crud_router`・`make_intent_service`(Task 5・10)
- Produces: `create_app(…, intent_service=None)` テスト注入引数・lifespanの `app.state.intent_service` 構築

- [ ] **Step 1: main.py を変更する(3点)**

(1) import節: 既存の `from latch.intents import …` 行へ `intents_crud_router` を追記し、ファクトリを追加import:

```python
from latch.intents import IntentsError, make_intent_parse_service, parse_router
from latch.intents.routes import intents_crud_router
from latch.intents.service import make_intent_service
```

(2) `create_app` シグネチャへ引数追加(既存引数の後ろへ):

```python
def create_app(
    clock: Clock | None = None,
    settings: Settings | None = None,
    auth_service=None,
    users_service=None,
    intent_parse_service=None,
    intent_service=None,
) -> FastAPI:
```

`if intent_parse_service is not None:` ブロックの隣へ:

```python
    if intent_service is not None:
        app.state.intent_service = intent_service
```

(3) `_lifespan` 内。既存の `build_intents` 行の下へ独立スキップ判定を追加:

```python
    build_intents = not hasattr(app.state, "intent_parse_service")
    build_intents_crud = not hasattr(app.state, "intent_service")
    if not (build_auth or build_users or build_intents or build_intents_crud):
        yield
        return
```

`if build_intents:` ブロックの後へ:

```python
    if build_intents_crud:
        app.state.intent_service = make_intent_service(
            clock=app.state.clock, engine=engine
        )
```

(4) ルータinclude(`app.include_router(parse_router)` の次へ):

```python
    app.include_router(intents_crud_router)
```

- [ ] **Step 2: __init__.py へexportを追記する**

`backend/src/latch/intents/__init__.py` のimport節と `__all__` へ追記:

```python
from latch.intents.events import (
    EVENT_CREATED,
    EVENT_DELETED,
    EVENT_EXPIRED,
    EVENT_SCHEDULED,
    EVENT_UPDATED,
    insert_match_event,
)
from latch.intents.intent_input import (
    IntentCreateRequest,
    IntentPatchRequest,
    StructuredIntentInput,
)
from latch.intents.mapping import (
    ResolvedColumns,
    columns_from_row,
    differs_from_row,
    resolve_for_active,
    resolve_for_draft,
    to_response_structured,
)
from latch.intents.routes import intents_crud_router
from latch.intents.service import (
    IntentParseService,
    IntentService,
    ParseResult,
    ParseWarning,
    SupportsParseIntent,
    make_intent_parse_service,
    make_intent_service,
)
from latch.intents.store import IntentRow, IntentStore, UserRow
```

`__all__` へ追記(既存要素を消さない。アルファベット順の維持):

```python
    "EVENT_CREATED",
    "EVENT_DELETED",
    "EVENT_EXPIRED",
    "EVENT_SCHEDULED",
    "EVENT_UPDATED",
    "ForbiddenError",
    "GeocodingFailedError",
    "IntentCreateRequest",
    "IntentNotFoundError",
    "IntentPatchRequest",
    "IntentRow",
    "IntentService",
    "IntentStore",
    "IntentValidationError",
    "InvalidTransitionError",
    "ResolvedColumns",
    "StructuredIntentInput",
    "UnderAgeError",
    "UserRow",
    "columns_from_row",
    "differs_from_row",
    "insert_match_event",
    "intents_crud_router",
    "make_intent_service",
    "resolve_for_active",
    "resolve_for_draft",
    "to_response_structured",
```

また、errorsのimport節を新例外込みの形へ更新する:

```python
from latch.intents.errors import (
    DependencyUnavailableError,
    ForbiddenError,
    GeocodingFailedError,
    IntentNotFoundError,
    IntentValidationError,
    IntentsError,
    InvalidTransitionError,
    LLMUnavailableError,
    UnderAgeError,
    UnstructurableError,
)
```

- [ ] **Step 3: 全unit試験で回帰確認**

Run: `make test`
Expected: PASS(既存全件+新規全件。main.pyのlifespan改変はASGITransportがlifespanを実行しないため既存unit試験に影響しない — ws-1・ws-2と同一根拠)

- [ ] **Step 4: lint してコミット**

```bash
make lint
git add backend/src/latch/main.py backend/src/latch/intents/__init__.py
git commit -m "feat(app): intents CRUDをアプリへ統合"
```

## 7.12 Task 12: integration試験の作成(実行しない)

**Files:**
- Create: `backend/tests/integration/test_intents_crud_api.py`

**Interfaces:**
- Consumes: compose常設api(127.0.0.1:8000)・db_engine fixture(tests/integration/conftest.py)・`python -m latch.auth issue-idp-token`(test_users_api.py流儀)

- [ ] **Step 1: integration試験ファイルを作成する**

`backend/tests/integration/test_intents_crud_api.py` を新規作成:

```python
"""intents CRUD APIのci環境実証(M1 ws-3 design §4.2)。

実HTTP(compose api=127.0.0.1:8000)・実DB・実ジオコーディング。
**実行はapiイメージ再ビルド後の make test-ci のみ**(compose upはapiイメージ
を再ビルドしない — STATUS運用ルール4。スーパーバイザー検証時に実行)。
正転地名は「天文館」に固定(fixture osm_sample.xml と実取り込みデータの双方
に存在 — design §4.2前提)。subjectは実行ごとにユニーク、行は試験内で後始末
(共有ci-db汚染回避 — M0 ws-3と同じ規約)。Intent行は試験データの後始末として
物理DELETEする(仕様の削除経路ではない — design §4.2-10)。
"""

import asyncio
import json
import sys
import uuid as uuid_mod
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock

pytestmark = pytest.mark.integration


def _unique_subject(prefix: str) -> str:
    return f"m1ws3-{prefix}-{uuid_mod.uuid4().hex[:12]}"


async def _cli_idp_token(provider: str, subject: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "latch.auth",
        "issue-idp-token",
        "--provider",
        provider,
        "--subject",
        subject,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    return stdout.decode().strip()


async def _register(api_client, subject: str, birth_date: str = "1990-04-01"):
    """token発行→User登録→(headers, user_id)を返す(test_users_api流儀)。"""
    idp_token = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp_token}
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "ws3", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers, created.json()["user"]["id"]


def _birth_19() -> str:
    """実行日(jst_date)時点で常に19歳(design §4.2-4。usersの_birth_17と同型)。"""
    today = SystemClock().jst_date()
    return (
        date(today.year - 20, today.month, today.day) + timedelta(days=1)
    ).isoformat()


def _future(hours: float) -> str:
    """api実Clock(SystemClock)基準の未来/過去ISO時刻。境界から離れた値だけ
    使う(apiとテストプロセスの時計は数秒ずれ得る)。"""
    now = SystemClock().now()
    return (now + timedelta(hours=hours)).isoformat()


def _structured() -> dict:
    return {
        "category": {"primary": "drinking", "secondary": "焼肉"},
        "alcohol_involved": True,
        "time": {"start": _future(3), "end": _future(6)},
        "location": {"name": "天文館", "radius_m": 2000},
        "budget": {"max": 5000, "currency": "JPY"},
        "participants": {"min": 2, "max": 4},
        "visibility": "hidden_until_match",
        "notification_level": "proposals_only",
        "expires_at": _future(6),
        "soft_constraints": ["軽く飲みたい"],
        "ng_unverifiable": ["会社関係の人は避けたい"],
        "negative_constraints": [],
    }


def _active_payload(structured: dict | None = None, raw: str = "今夜20時から天文館で軽く飲みたい") -> dict:
    return {
        "raw_text": raw,
        "status": "active",
        "structured_intent": structured if structured is not None else _structured(),
    }


async def _cleanup(db_engine, user_id, subject: str) -> None:
    async with db_engine.begin() as conn:
        if user_id:
            await conn.execute(
                text(
                    "DELETE FROM match_events WHERE source_intent_id IN "
                    "(SELECT id FROM intents WHERE user_id = :uid)"
                ),
                {"uid": user_id},
            )
            await conn.execute(
                text("DELETE FROM intents WHERE user_id = :uid"), {"uid": user_id}
            )
        await conn.execute(
            text("DELETE FROM users WHERE auth_subject = :s"), {"s": subject}
        )


def _load_jsonb(value):
    return json.loads(value) if isinstance(value, str) else value


# --- §4.2-1 active作成フルフロー ---


async def test_1_active_create_full_flow(api_client, db_engine):
    subject = _unique_subject("flow")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        resp = await api_client.post(
            "/v1/intents", headers=headers, json=_active_payload()
        )
        assert resp.status_code == 201, resp.text
        intent = resp.json()["intent"]
        assert intent["status"] == "active"
        assert intent["version"] == 1
        assert intent["expires_at"]
        intent_id = intent["id"]

        got = await api_client.get(f"/v1/intents/{intent_id}", headers=headers)
        assert got.status_code == 200, got.text
        body = got.json()["intent"]
        assert body["raw_text"] == "今夜20時から天文館で軽く飲みたい"
        assert body["structured_intent"]["location"]["name"] == "天文館"
        assert body["structured_intent"]["time"]["end"]  # 補完後(指定値)
        assert body["structured_data"]["location_name"] == "天文館"

        async with db_engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT geo_center IS NOT NULL AS has_geo, "
                            "structured_data FROM intents WHERE id = :i"
                        ),
                        {"i": intent_id},
                    )
                )
                .mappings()
                .first()
            )
            assert row["has_geo"] is True
            assert _load_jsonb(row["structured_data"])["location_name"] == "天文館"
            evs = (
                (
                    await conn.execute(
                        text(
                            "SELECT event_type, payload, status FROM match_events "
                            "WHERE source_intent_id = :i"
                        ),
                        {"i": intent_id},
                    )
                )
                .mappings()
                .all()
            )
        assert len(evs) == 1
        assert evs[0]["event_type"] == "created"
        assert evs[0]["status"] == "pending"
        assert _load_jsonb(evs[0]["payload"]) == {"version": 1}
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-2 draft作成 ---


async def test_2_draft_create(api_client, db_engine):
    subject = _unique_subject("draft")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        resp = await api_client.post(
            "/v1/intents",
            headers=headers,
            json={"raw_text": "後で書く", "status": "draft"},
        )
        assert resp.status_code == 201, resp.text
        intent_id = resp.json()["intent"]["id"]
        async with db_engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT geo_center IS NULL AS no_geo, expires_at, "
                            "visibility FROM intents WHERE id = :i"
                        ),
                        {"i": intent_id},
                    )
                )
                .mappings()
                .first()
            )
            assert row["no_geo"] is True
            assert row["expires_at"] is None
            assert row["visibility"] == "hidden_until_match"
            count = await conn.scalar(
                text(
                    "SELECT count(*) FROM match_events "
                    "WHERE source_intent_id = :i"
                ),
                {"i": intent_id},
            )
        assert count == 0
        listed = await api_client.get(
            "/v1/intents", headers=headers, params={"status": "draft"}
        )
        assert listed.status_code == 200
        assert [i["id"] for i in listed.json()["items"]] == [intent_id]
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-3 draft→active ---


async def test_3_draft_to_active(api_client, db_engine):
    subject = _unique_subject("d2a")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        structured = _structured()

        # (a) 同一内容のactive化: version=1据え置き・created Event version=1
        payload = _active_payload(structured=structured)
        created = await api_client.post(
            "/v1/intents",
            headers=headers,
            json={"raw_text": payload["raw_text"], "status": "draft",
                  "structured_intent": structured},
        )
        assert created.status_code == 201, created.text
        same_id = created.json()["intent"]["id"]
        activated = await api_client.patch(
            f"/v1/intents/{same_id}",
            headers=headers,
            json=payload,
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["intent"]["status"] == "active"
        assert activated.json()["intent"]["version"] == 1
        async with db_engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text(
                            "SELECT geo_center IS NOT NULL AS has_geo, expires_at "
                            "FROM intents WHERE id = :i"
                        ),
                        {"i": same_id},
                    )
                )
                .mappings()
                .first()
            )
            assert row["has_geo"] is True
            assert row["expires_at"] is not None  # 補完
            ev = (
                (
                    await conn.execute(
                        text(
                            "SELECT event_type, payload FROM match_events "
                            "WHERE source_intent_id = :i"
                        ),
                        {"i": same_id},
                    )
                )
                .mappings()
                .one()
            )
        assert ev["event_type"] == "created"
        assert _load_jsonb(ev["payload"]) == {"version": 1}

        # (b) 内容変更を伴うactive化: version=2+created Event version=2
        changed = dict(structured)
        changed["participants"] = {"min": 3, "max": 4}
        created2 = await api_client.post(
            "/v1/intents",
            headers=headers,
            json={"raw_text": payload["raw_text"], "status": "draft",
                  "structured_intent": structured},
        )
        assert created2.status_code == 201
        changed_id = created2.json()["intent"]["id"]
        activated2 = await api_client.patch(
            f"/v1/intents/{changed_id}",
            headers=headers,
            json=_active_payload(structured=changed, raw=payload["raw_text"]),
        )
        assert activated2.status_code == 200, activated2.text
        assert activated2.json()["intent"]["version"] == 2
        async with db_engine.connect() as conn:
            ev2 = (
                (
                    await conn.execute(
                        text(
                            "SELECT event_type, payload FROM match_events "
                            "WHERE source_intent_id = :i"
                        ),
                        {"i": changed_id},
                    )
                )
                .mappings()
                .one()
            )
        assert ev2["event_type"] == "created"
        assert _load_jsonb(ev2["payload"]) == {"version": 2}
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-4 検証422一式(active/draft対照) ---


async def test_4_validation_errors_active_vs_draft(api_client, db_engine):
    subject = _unique_subject("val")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)

        async def _code(payload):
            resp = await api_client.post("/v1/intents", headers=headers, json=payload)
            return resp.status_code, (
                resp.json().get("error", {}).get("code")
            )

        base = _structured()
        no_category = dict(base, category=None)
        assert await _code(_active_payload(structured=no_category)) == (
            422, "VALIDATION_ERROR",
        )
        past = dict(base, time={"start": _future(-1)})
        assert await _code(_active_payload(structured=past)) == (
            422, "VALIDATION_ERROR",
        )
        far = dict(base, time={"start": _future(8 * 24)})
        assert await _code(_active_payload(structured=far)) == (
            422, "VALIDATION_ERROR",
        )
        past_exp = dict(base, expires_at=_future(-1))
        assert await _code(_active_payload(structured=past_exp)) == (
            422, "VALIDATION_ERROR",
        )
        unknown_place = dict(base, location={"name": "存在しない地名テスト2026"})
        assert await _code(_active_payload(structured=unknown_place)) == (
            422, "GEOCODING_FAILED",
        )
        # draftは同内容(過去時刻・必須3欠落)が受理される対照
        ok_past = await _code(
            {"raw_text": "下書きなら過去でも", "status": "draft",
             "structured_intent": past}
        )
        assert ok_past[0] == 201
        ok_missing = await _code(
            {"raw_text": "下書きなら欠けてても", "status": "draft",
             "structured_intent": no_category}
        )
        assert ok_missing[0] == 201
    finally:
        await _cleanup(db_engine, user_id, subject)


async def test_4b_under_age_drinking_rejected(api_client, db_engine):
    subject = _unique_subject("u19")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject, birth_date=_birth_19())
        resp = await api_client.post(
            "/v1/intents", headers=headers, json=_active_payload()
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "UNDER_AGE"
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-5 PATCH active更新 ---


async def test_5_patch_active_update(api_client, db_engine):
    subject = _unique_subject("upd")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        created = await api_client.post(
            "/v1/intents", headers=headers, json=_active_payload()
        )
        intent_id = created.json()["intent"]["id"]

        async def _geo_lon():
            async with db_engine.connect() as conn:
                return await conn.scalar(
                    text("SELECT ST_X(geo_center::geometry) FROM intents WHERE id = :i"),
                    {"i": intent_id},
                )

        before_lon = await _geo_lon()
        moved = dict(_structured())
        moved["location"] = {"name": "鹿児島市天文館一丁目", "radius_m": 1000}
        resp = await api_client.patch(
            f"/v1/intents/{intent_id}",
            headers=headers,
            json=_active_payload(structured=moved, raw="明日も天文館で"),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["intent"]
        assert body["version"] == 2
        assert body["structured_intent"]["location"]["name"] == "鹿児島市天文館一丁目"
        after_lon = await _geo_lon()
        assert after_lon is not None and after_lon != before_lon  # 再ジオコーディング
        async with db_engine.connect() as conn:
            ev = (
                (
                    await conn.execute(
                        text(
                            "SELECT event_type, payload FROM match_events "
                            "WHERE source_intent_id = :i ORDER BY created_at"
                        ),
                        {"i": intent_id},
                    )
                )
                .mappings()
                .all()
            )
        assert len(ev) == 2
        assert ev[1]["event_type"] == "updated"
        assert _load_jsonb(ev[1]["payload"]) == {"version": 2}
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-6 pause/resume ---


async def test_6_pause_resume(api_client, db_engine):
    subject = _unique_subject("pr")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        created = await api_client.post(
            "/v1/intents", headers=headers, json=_active_payload()
        )
        intent_id = created.json()["intent"]["id"]

        paused = await api_client.post(
            f"/v1/intents/{intent_id}/pause", headers=headers
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["intent"]["status"] == "paused"
        assert paused.json()["intent"]["version"] == 1  # 不変
        async with db_engine.connect() as conn:
            count1 = await conn.scalar(
                text("SELECT count(*) FROM match_events WHERE source_intent_id = :i"),
                {"i": intent_id},
            )
        assert count1 == 1  # pauseでは増えない

        resumed = await api_client.post(
            f"/v1/intents/{intent_id}/resume", headers=headers
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["intent"]["status"] == "active"
        assert resumed.json()["intent"]["version"] == 2
        async with db_engine.connect() as conn:
            evs = (
                (
                    await conn.execute(
                        text(
                            "SELECT event_type, payload FROM match_events "
                            "WHERE source_intent_id = :i ORDER BY created_at"
                        ),
                        {"i": intent_id},
                    )
                )
                .mappings()
                .all()
            )
        assert len(evs) == 2
        assert evs[1]["event_type"] == "updated"
        assert _load_jsonb(evs[1]["payload"]) == {"version": 2}
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-7 DELETE ---


async def test_7_delete(api_client, db_engine):
    subject = _unique_subject("del")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        active = await api_client.post(
            "/v1/intents", headers=headers, json=_active_payload()
        )
        active_id = active.json()["intent"]["id"]
        draft = await api_client.post(
            "/v1/intents",
            headers=headers,
            json={"raw_text": "消す下書き", "status": "draft"},
        )
        draft_id = draft.json()["intent"]["id"]

        for target in (active_id, draft_id):
            resp = await api_client.delete(
                f"/v1/intents/{target}", headers=headers
            )
            assert resp.status_code == 204
            async with db_engine.connect() as conn:
                status = await conn.scalar(
                    text("SELECT status FROM intents WHERE id = :i"), {"i": target}
                )
                ev = (
                    (
                        await conn.execute(
                            text(
                                "SELECT event_type, payload FROM match_events "
                                "WHERE source_intent_id = :i"
                            ),
                            {"i": target},
                        )
                    )
                    .mappings()
                    .one()
                )
            assert status == "cancelled"
            assert ev["event_type"] == "deleted"
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-8 認可 ---


async def test_8_authorization(api_client, db_engine):
    owner_subject = _unique_subject("own")
    other_subject = _unique_subject("oth")
    owner_id = None
    other_id = None
    try:
        owner_headers, owner_id = await _register(api_client, owner_subject)
        other_headers, other_id = await _register(api_client, other_subject)
        created = await api_client.post(
            "/v1/intents", headers=owner_headers, json=_active_payload()
        )
        intent_id = created.json()["intent"]["id"]
        for method, path, kwargs in [
            ("get", f"/v1/intents/{intent_id}", {}),
            ("patch", f"/v1/intents/{intent_id}", {"json": _active_payload()}),
            ("delete", f"/v1/intents/{intent_id}", {}),
            ("post", f"/v1/intents/{intent_id}/pause", {}),
        ]:
            resp = await getattr(api_client, method)(
                path, headers=other_headers, **kwargs
            )
            assert resp.status_code == 403, (method, resp.text)
            assert resp.json()["error"]["code"] == "FORBIDDEN"
        missing = await api_client.get(
            f"/v1/intents/{uuid_mod.uuid4()}", headers=owner_headers
        )
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "NOT_FOUND"
        no_auth = await api_client.get("/v1/intents")
        assert no_auth.status_code == 401
        assert no_auth.json()["error"]["code"] == "UNAUTHENTICATED"
    finally:
        await _cleanup(db_engine, other_id, other_subject)
        await _cleanup(db_engine, owner_id, owner_subject)


# --- §4.2-9 cursor ---


async def test_9_cursor_pagination_and_status_filter(api_client, db_engine):
    subject = _unique_subject("cur")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        made = []
        for i in range(3):
            resp = await api_client.post(
                "/v1/intents",
                headers=headers,
                json=_active_payload(raw=f"ページング試験{i}"),
            )
            assert resp.status_code == 201, resp.text
            made.append(resp.json()["intent"]["id"])
        page1 = await api_client.get(
            "/v1/intents", headers=headers, params={"limit": 2}
        )
        assert page1.status_code == 200
        body1 = page1.json()
        assert len(body1["items"]) == 2
        assert body1["next_cursor"]
        page2 = await api_client.get(
            "/v1/intents",
            headers=headers,
            params={"limit": 2, "cursor": body1["next_cursor"]},
        )
        body2 = page2.json()
        assert len(body2["items"]) == 1
        assert body2["next_cursor"] is None
        collected = {i["id"] for i in body1["items"] + body2["items"]}
        assert collected == set(made)  # 重複・欠落なし
        # statusフィルタの絞り込み
        draft = await api_client.post(
            "/v1/intents",
            headers=headers,
            json={"raw_text": "下書き1件", "status": "draft"},
        )
        draft_id = draft.json()["intent"]["id"]
        filtered = await api_client.get(
            "/v1/intents", headers=headers, params={"status": "draft"}
        )
        assert [i["id"] for i in filtered.json()["items"]] == [draft_id]
    finally:
        await _cleanup(db_engine, user_id, subject)
```

- [ ] **Step 2: 構文・importの検証のみ行う(実行しない)**

Run: `cd backend && uv run pytest tests/integration/test_intents_crud_api.py --collect-only -q`
Expected: 収集成功(10件の試験関数)。`-m "not integration"` の `make test` でも収集されるため、そこでImportErrorが出ないことを確認する

- [ ] **Step 3: lint してコミット**

```bash
make lint
git add backend/tests/integration/test_intents_crud_api.py
git commit -m "test(intents): CRUD integration試験を追加"
```

## 7.13 Task 13: 報告ファイルの作成

**Files:**
- Create: `docs/plans/M1/ws-3-report.md`

- [ ] **Step 1: 報告ファイルを書く(§8のテンプレートどおり)**

§8の形式で作成する。全タスクのコミットハッシュ・テスト件数・完了条件§6の1〜10への達成状況・逸脱記録を必ず含める。

- [ ] **Step 2: 最終確認してコミット**

```bash
make lint && make test
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d   # 空であること
git status   # §3.1・§3.2の一覧どおりであること
git add docs/plans/M1/ws-3-report.md
git commit -m "docs: M1 ws-3実装報告"
```

## 8. 報告形式

成果物: `docs/plans/M1/ws-3-report.md`(Task 13で作成)。以下の節を含める。

```markdown
# M1 ws-3 実装報告(intents CRUD・draft→active・保存時検証)

- 作業単位: ws-3(design: docs/plans/M1/ws-3-design.md / plan: docs/plans/M1/ws-3-plan.md)
- 実装日: <日付>
- worktree / ブランチ: <worktreeパス> / m1-ws-3

## 1. 実施タスクとコミット一覧
| Task | コミット | 概要 |
|---|---|---|
| 1 | <hash> | CRUD例外6種 |
| …(Task 1〜13の全体) | | |

## 2. テスト結果
- make lint: <結果>
- make test: <N> passed(新規 <M> 件・内訳: test_errors +n / test_intent_input +n /
  test_mapping +n / test_events +n / test_intents_service +n / test_crud_routes +n /
  test_time_validation +n)
- 既知の既存失敗: <なし、または内容>

## 3. 完了条件の達成状況(計画§6の1〜10に対して項目別)

## 4. 計画からの逸脱・判断
- make_intent_service(*, clock, engine) — settings引数を省略(design §2.10 IF案からの
  確定。利用する設定がないため)
- ResolvedColumns.raw_text / columns_from_row の追加(design §3.1 IF案からの確定)
- その他実装中の判断(あればすべて。黙って変えない)

## 5. 検証手順(スーパーバイザー向け)
1. `docker compose build api` — **必須**(make test-ci の compose up はapiイメージを
   再ビルドしない — STATUS運用ルール4)
2. `make test-ci`(unit+integration。test_intents_crud_api.py 10件を含む)
3. test-ci実行後、geo実データはfixtureリロードで失われるため `make geo-import`
   で復旧する
4. `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空

## 6. test-ci=スーパーバイザー検証待ち
本単位の実装中は compose常設環境へ触れていない(計画§0.3)。integration試験は
作成のみで未実行。
```

---

## 9. 計画作成時の自己検証メモ(Self-Review・実機検証結果 2026-09-28)

**実機検証**: 本計画のコードを一時的に組み合わせて `make lint`(クリーン)・`make test`(416 passed = 既存302 + 新規114)を確認済み。検証で発見・修正した点(計画へ反映済み):

- `AbstractAsyncContextManager` は `contextlib` からimportする(型エイリアス右辺は実行時評価のため実importが必須)
- `list_intents` の依存引数はデフォルト付きQuery引数の**前**に置く(Python構文上の必須)
- 遷移系の拒否試験は `InvalidTransitionError` を期待する(`IntentValidationError` とは兄弟クラスで code は同一)
- `StubStore` の user_row=None(404試験)と未指定(デフォルト26歳)の区別に `_UNSET` センチネルを使う
- httpx の `AsyncClient.get/delete` は `json=` 引数を受けない(401試験のkwargs条件分岐)
- POST /v1/intents の受入試験は structured_intent をネストさせて渡す


- **仕様被覆**: design §1.2確定値1〜35はすべて§1参照仕様表・§7各タスクへ反映(1=Task 10・2=Task 5・3=Task 2/5・4=Task 3・5=Task 5/8・6=Task 8・7=Task 5/6・8=Task 5・9=Task 3・10=Task 3・11=Task 8・12=Task 8・13=Task 8・14=Task 8・15=Task 9・16=Task 4・17=Task 9・18=Task 7・19=Task 7・20=Task 9・21=Task 9・22=Task 7/10・23=Task 1・24=§4.2スコープ外・25=§2制約・26=Task 5/10・27=Task 5・28=Task 5・29=Task 3・30=§2制約・31=§4.2スコープ外・32=§0前提・33=Task 10・34=Task 3・35=Task 3)。§6-1〜§6-6は§0.5のとおり確定済み
- **Review Focus 5項目**は§5に記載のとおりTask 8(1・2番)・Task 9(3番)・Task 7(4番)・Task 10(5番)の試験にピン留めした
- **型一貫性**: `IntentStore` のメソッドシグネチャはTask 3(定義)・Task 5〜9(スタブ実装)で同一。`ResolvedColumns` フィールドはTask 3(定義)・store(Task 3)・service(Task 5)で同一

