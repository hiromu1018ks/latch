# M1 ws-1(users API)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** M1スコープ1(12 M1-1)を実装する — POST /v1/users(初回登録: display_name/birth_date必須・18歳未満422 UNDER_AGE・登録済み409 USER_EXISTS)と GET /v1/users/me(本人のみ・200)を作り、token→`profile_complete:false`→登録→me→再token の初回登録フローを通す。M0 ws-3が先送りしたUser行の作成経路(design §1.1)が本単位の成果物。

**Architecture:** `latch.users` パッケージに層を分けて置く — `errors.py`(UsersError階層: UNDER_AGE/USER_EXISTS/NOT_FOUND/503。authとは共通基底を作らない)、`service.py`(満年齢純粋関数 `age_years`・`UserService(register/get_me)`・IntegrityError制約名分類・`make_user_service` の実SQL関数 INSERT 1本+SELECT 1本をCallable注入。ORMモデル・リポジトリ層はintents CRUD(ws-3 M1)で再判断)、`routes.py`(prefix `/v1/users` の保護ルータ+入出力モデル)。プロセス統合は `create_app` への第4引数 `users_service` 追加とlifespanの**サービスごとの独立スキップ判定**変更(既存のauth_service注入済みパターンは無影響)。main.pyが並走ws-2との唯一の交点(マージ時は両側追記保持 — スーパーバイザー裁定)。

**Tech Stack:** 既存のみ(Python 3.13 / FastAPI / pydantic / SQLAlchemy[asyncio]+asyncpg / pytest)。**依存追加なし・マイグレーション追加なし・設定追加なし**(users表はalembic 0001に実装済み — design §1.3)。

**Spec:** `docs/plans/M1/ws-1-design.md`(agent1設計メモ。本計画はこの文書の§3ファイル構成・§4テスト方針・§5完了条件を各タスクへ展開したもの。design.md §6の6論点は推奨どおりsupervisor承認済み — 計画書へ落とし済み)

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m1-ws-1`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する。
- **【最重要・compose常設環境へ触れない】**: 並走ws-2(Intent Parser)が同じcompose環境(latch-ci)を共有している。さらに `make test-ci` が呼ぶ `docker compose up -d --wait` は **apiイメージを再ビルドしない**(compose.yamlでapiはbuild型・ソースマウントなし。design §1.2確定値13)——コード変更後のintegration試験には先に `docker compose build api` が必要であり、apiイメージの再ビルド・コンテナ再作成は並走単位とスーパーバイザーの検証環境に影響する。よって本単位の実装中は **`make test-ci` / `make up` / `make down` / `docker compose …` / `docker build` を一切実行しない**(マイグレーション追加はないため `make migrate` も不要)。**開発は `make lint`・`make test`(unit)で完結させる** — unit試験はFakeClock+スタブ永続化関数で外部プロセス不要(design §4.1)。integration試験ファイルは作成するが**実行しない**(報告書に「**test-ci=スーパーバイザー検証待ち**」と記録する)。`make test` はintegrationファイルの収集(import)まで行うため、構文・importの正当性はunit実行で検証される。
- **並走ws-2との分離**: ws-2は `llm/` 配下とparseルータを作る。交点は `backend/src/latch/main.py` のみ。本単位はmain.pyのusers分(ルータinclude・lifespanのusers構築・UsersErrorハンドラ・第4引数)だけを追記し、**既存のauth関連行を一切消さない**。競合の解消(両側追記保持)はスーパーバイザーがマージ時に行う。
- **設計判断の固定値**: design.md §6の6論点(me不在時404・生SQL継続・エラー優先順序(UNDER_AGEが409に優先)・auth側lookupとのSELECT重複許容・display_name上限なし・test-ci再ビルド問題への対応)は固定済みとして本計画に落としてある。またdesign §3.1のIF案を本計画§8のとおり確定した(IntegrityErrorの制約名検査方法・Callableのシグネチャ・jsonb渡し・UUID正規化・lifespan独立判定)。**これらを変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `docs:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする(12 第8節 運用ルール5)。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由(システムPythonと衝突させないため)。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| POST /v1/users: request `{"display_name", "birth_date": "1990-04-01", "profile": {"bio"}}` → **201** `{"user": {"id": "<uuid>", "display_name"}}`。errors: 422 VALIDATION_ERROR(必須欠落・形式不正)・422 UNDER_AGE(18歳未満)・409 USER_EXISTS | 05 第5節 認証・ユーザー系 POST /v1/users |
| Authorizationヘッダー必須(認証不要2エンドポイントに含まれない)。サーバは認証済みJWTの `auth_provider`/`auth_subject` claimに紐付くUser行を生成(**User.idは新規採番**)。`profile_complete: false` のクライアントが直後に呼ぶ経路 | 05 第5節 POST /v1/users解説 |
| birth_date必須。呼び出し時点のClock.now()から18歳未満は登録拒否(422 UNDER_AGE)。表示名・プロフィールの内容審査はMVP外 | 05 第5節 / 02 D-12 |
| 18歳以上の生年月日は自己申告(users.birth_dateへ保持)・IdPからは取得しない | 08 第4節 D-10 |
| GET /v1/users/me: 200 `{id, display_name, profile: {bio}, birth_date, profile_complete: true}`。**本人のみ**。birth_dateは本人自身にのみ返す | 05 第5節 GET /v1/users/me |
| users表: birth_date date NOT NULL・UNIQUE(auth_provider, auth_subject)=`uq_users_auth_provider_subject`・profile jsonb DEFAULT '{}'。**alembic 0001に実装済み**(マイグレーション追加なし) | 05 第2節 / alembic 0001 |
| エラー形式(共通): `{"error": {code, message, details}}`。409 USER_EXISTS・422 VALIDATION_ERROR・422 UNDER_AGE・404 NOT_FOUND(全API)・503 DEPENDENCY_UNAVAILABLE(全API)・401 UNAUTHENTICATED(全API) | 05 第5節 エラー形式表 |
| 認証は `Authorization: Bearer <JWT>`。JWT claimの `auth_provider`/`auth_subject` でUser行と紐付け。M1以降のドメインルータは `require_authenticated` 依存を**ルータ単位**に付す(C3の強制方法) | 05 第5節冒頭 / auth/deps.py |
| `profile_complete` 列はusers表に存在しない。導出は「User行が存在する」=true(POST /v1/usersは完全な行を一括作成。部分登録状態なし) | ws-3設計 §1.3-3(05 §2に列なし) |
| 時刻参照はすべてClock経由(arch test `test_arch_no_direct_time` が backend/src 全体を強制)。JST暦日付は `clock.jst_date()` が正規経路 | 04 第5節 FR-41 / 10 第1節 / core/clock.py |
| 時刻列にDB時刻関数のDEFAULTを付けない(created_at/updated_atはClock由来の明示値)。uuid PKはDEFAULT gen_random_uuid()(INSERT側の明示値を妨げない) | ws-1(M0)設計 §2.8 / alembic 0001 |
| 実HTTPのintegration試験はcompose常設api(127.0.0.1:8000)へ。IdPトークン発行はws-3の内部ツール(`python -m latch.auth issue-idp-token`)+同梱テスト鍵 | 10 第1節 / auth/tools.py / tests/integration/conftest.py |
| `make test-ci` は `docker compose up -d --wait` のみで**イメージを再ビルドしない**。実HTTP試験はapiイメージ再ビルドが前提 | Makefile / compose.yaml |
| レート制限(429)はM1 ws-4。本単位はerror codeを列挙しない | 12 M1-6 |
| M1スコープ1・依存(M0 usersスキーマ・認証=マージ済み)・完了条件 | 12 第3節 M1 / docs/plans/STATUS.md M1表 |
| 完了条件6項目・テスト方針・ファイル構成 | design.md §3〜§5 |

## 2. グローバル制約(全タスクに暗黙に適用)

- **依存追加なし**(`backend/pyproject.toml`・`backend/uv.lock` は無変更)。**マイグレーション追加なし**(`backend/alembic/` は無変更)。**設定追加なし**(`backend/src/latch/settings.py` は無変更)
- **auth/・core/・llm/・geo/・worker/ は無変更**(auth不変 — design §2.6・§2.8。SELECTのWHERE句重複は許容・design §6-4)。`compose.yaml`・`Makefile`・`docker/`・`.mise.toml`・`backend/tests/conftest.py`・`backend/tests/integration/conftest.py` も無変更
- 永続化は **`sqlalchemy.text()` 生SQLのみ**(INSERT 1本+SELECT 1本・単表)。ORMモデル・リポジトリクラス・Tableメタデータを作らない(design §2.1)
- **idはDBのDEFAULT gen_random_uuid()に任せ `RETURNING id` で受け取る**(Python側でuuid4を採番しない)。**created_at/updated_at は `clock.now()` の明示値**をINSERTする(§2.8)
- 年齢判定は **`age_years(birth_date, clock.jst_date()) < 18` → 422 UNDER_AGE**(18歳の誕生日当日は登録可。design §2.3)。エラー検証の優先順序は **Pydantic検証(422)→ 年齢(422 UNDER_AGE)→ INSERT(409 USER_EXISTS / 503)** に固定(17歳かつ登録済みならUNDER_AGE — design §2.2)
- 409の検出は **事前SELECTなし・INSERT衝突をUNIQUE制約で検出**(TOCTOU排除)。IntegrityErrorは**制約名+sqlstateを検査**し `uq_users_auth_provider_subject` 由来のみ409、他は503へ(§8-1)
- 入力検証はdocsに規定のある範囲のみ: `display_name: str`(min_length=1。**最大長・文字種の制限を付けない**)・`birth_date: date`・`profile.bio: str | None`(bioがNoneならDBには `{}` を格納)。未来日付・高齢上限の特別検証なし(design §2.7)
- エラー応答は共通envelope `{"error": {"code", "message", "details": None}}`。JSON形式不正=400 MALFORMED_REQUEST・必須欠落/値域外=422 VALIDATION_ERROR(既存ハンドラが処理・新コード不要)
- 製品コード(`backend/src/latch/`)で `datetime.now` / `datetime.utcnow` / `date.today` / `time.time` / `time.monotonic` / `time.sleep` / `from time import` の使用禁止(例外は `core/clock.py` のみ — arch test)
- ログ(ロガー `latch.users`)はイベント名と結果/コードのみ。**subject・claim値・display_nameを出さない**(08 第2.4節)
- `backend/tests/unit/users/` に `__init__.py`・`conftest.py` は作らない(design §3.1の一覧にない。各テストファイル内に必要なfixtureを定義する — auth試験と同じ慣例)
- main.pyへの変更はusers分のみ(ルータinclude・lifespanのusers構築分・UsersErrorハンドラ1個・`create_app` 第4引数)。**既存の `/health`・authルータinclude・AuthErrorハンドラ・RequestValidationErrorハンドラは変更しない**

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **JST/UTC境界の年齢判定ずれ** — 基準日付をUTCのまま比較すると日本時間の深夜0時前後で最大9時間ずれ、誕生日当日の18歳が17歳として拒否され得る → Task 2の「JST深夜0時境界」試験が所有(FakeClockをUTC 14:59:59/15:00:00 にsetして `clock.jst_date()` 基準の判定転換を証明)
2. **IntegrityErrorの偽陽性(他制約違反の409誤変換)** — `users_pkey` 違反やCHECK(auth_provider)違反まで409 USER_EXISTSに変換すると、登録失敗の真因が「登録済み」と偽装される → Task 2の `classify_integrity_error` 3パターン試験が所有(sqlstate≠23505・制約名不一致はNone=503扱い)
3. **asyncpg戻り値の型(UUIDインスタンス・jsonb文字列)** — `uuid.UUID(row[0])` はUUIDインスタンスにAttributeErrorになる(M0 ws-3検証で実際に踏んだ失敗1の再発)。jsonb列はtext()経由ではstrで返る → Task 2の `_coerce_user_id`・Task 3のprofile正規化+Task 4のintegration #1(me応答のprofileがdictとして出る)が所有
4. **並行同一subject登録のTOCTOU** — 事前SELECT方式では同一subjectの並行登録をすり抜ける(design §2.2-Bの棄却理由) → INSERT衝突検出そのもの。integration #4(同一subject再登録→409)が実DBのUNIQUE制約経由で所有
5. **`{"bio": null}` の格納** — bioがNoneのとき `{"bio": null}` を格納するとme応答のprofile契約が崩れる → Task 4のroutes試験が所有(profile省略・bio=nullの2パターンでスタブへ渡る値が `{}` になることを検証)

## 4. スコープ(作成・変更するファイル一覧)

作成(design.md §3.1どおり):

```text
backend/src/latch/users/__init__.py        (Task 1でdocstringのみ作成 → Task 5で公開IFの再exportへ更新)
backend/src/latch/users/errors.py          (Task 1)
backend/src/latch/users/service.py         (Task 1でage_years → Task 2でUserService等 → Task 3で実SQL工場)
backend/src/latch/users/routes.py          (Task 4)
backend/tests/unit/users/test_age.py       (Task 1)
backend/tests/unit/users/test_service.py   (Task 2。Task 3で1件追記)
backend/tests/unit/users/test_routes.py    (Task 4)
backend/tests/integration/test_users_api.py (Task 5。作成のみ・実行しない)
docs/plans/M1/ws-1-report.md               (報告ファイル。Task 5で作成)
```

変更:

- `backend/src/latch/main.py` — (1) usersルータのinclude、(2) lifespanでusers_serviceを構築しapp.stateへ載せる(スキップ判定をサービスごとの独立判定へ変更)、(3) `UsersError` ハンドラ1個、(4) `create_app` に第4引数 `users_service` を追加(Task 4)。**これらが本単位の既存コードへの全変更**(design §3.2)

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`(gitignore済み)。

## 5. 禁止(触ってはいけないもの・スコープ外の判断基準)

- **compose常設環境の操作**: `make test-ci` / `make up` / `make down` / `make migrate` / `docker compose …` / `docker build`(§0。apiイメージ再ビルド・コンテナ再作成はスーパーバイザーの検証手順に含まれる)
- `backend/alembic/` 全体(マイグレーション追加なし・design §1.3)、`backend/src/latch/auth/`(make_user_lookup・errors・service すべて不変)、`backend/src/latch/core/`(clock・db・deps)、`backend/src/latch/llm/`(ws-2の領域)、`backend/src/latch/geo/`・`backend/src/latch/worker/`
- `backend/src/latch/settings.py`・`backend/pyproject.toml`・`backend/uv.lock`・`compose.yaml`・`Makefile`・`docker/`・`.mise.toml`・`.gitignore`・ルート `README.md`・`backend/README.md`
- `backend/tests/conftest.py`・`backend/tests/integration/conftest.py`(api_client・db_engineで足りる — design §3.2)・`backend/tests/unit/` の既存テストファイル・`backend/tests/integration/` の既存テストファイル
- `docs/01〜12`・`docs/learn/`・`docs/reviews/`・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/M0/`・`docs/plans/M1/` の他ファイル(**ws-2-design.md・ws-2-plan.md は並走単位の資産 — 絶対に触らない**)
- `prototype/` 全体・`.claude/`・`.agents/`・`.hermes/`
- スコープ外と判断する基準: レート制限429の実装(M1 ws-4)/ intents系API・Parser(ws-2・ws-3。users APIの完了を依存とする)/ users行の更新・削除・退会API・birth_date変更・年齢再検証(05 §5のAPI一覧に存在しない)/ 表示名・プロフィールの内容審査(02 D-12によりMVP外)/ ORMモデル・リポジトリ層の導入(intents CRUD(ws-3 M1)の設計で判断 — design §2.1)/ auth側 `make_user_lookup` のusers側への統合(auth不変・design §6-4)/ フロントエンドの登録画面接続(M1 ws-5)。これらが必要になったと感じても作らない — design §1.4に列挙された後続単位のスコープ
- mainブランチへのコミット・push・マージ

## 6. 完了条件(design.md §5の6項目。検証コマンドつき。Task 5で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン(unit/users 追加分を含む)**
   検証: `make lint && make test` — ともにexit 0
2. **`make test-ci` で §4.2-1〜6 がグリーン** — **ただし実装エージェントは実行しない**(§0。apiイメージの再ビルドが必要なためスーパーバイザー検証時に実施)。integration試験ファイルの収集が `make test` でエラーなく通ること(構文・importの正当性)をもって代替検証とし、報告書に「**test-ci=スーパーバイザー検証待ち**」と記録する
   検証(実装側): `make test` の収集エラーなし + `cd backend && uv run pytest --collect-only tests/integration/test_users_api.py -q` がexit 0(6件を収集。§4.2-7の事後DELETEは各試験のtry/finallyに組み込み)
3. **実時間参照が `core/clock.py` のみ(users/配下はヒットしない)**
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0(users/配下が自動スキャン対象に含まれる)
4. **`alembic/`・`pyproject.toml`・`uv.lock`・`settings.py`・`compose.yaml`・`Makefile` に差分なし**
   検証: `git diff --stat main -- backend/alembic backend/pyproject.toml backend/uv.lock backend/src/latch/settings.py compose.yaml Makefile` — 出力なし
5. **触るファイルが §4 の一覧どおり(auth/・core/・llm/・geo/・worker/に差分なし)**
   検証: `git diff --name-only main | sort` が§4の一覧と完全一致(main.pyを含む9ファイル)。`git status --short` が空(未コミット変更なし)
6. **§4.2-1の初回登録フローが1本の試験として通る**(me応答のbirth_date・profile_completeと、再token時の `profile_complete:true` まで含む)— 試験コードの検証はTask 5の収集確認で代替し、実行証拠はスーパーバイザー検証時に採取(完了条件2と同一経路)

## 7. 報告形式

**結果ファイル**: `docs/plans/M1/ws-1-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M1 ws-1(users API) 実行報告

- ブランチ: m1-ws-1 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る> |
| 2 | make test-ci §4.2-1〜6 | test-ci=スーパーバイザー検証待ち | <make test 収集結果 + --collect-only 出力> |
| 3 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 4 | alembic等に差分なし | PASS/FAIL | <git diff --stat 出力(空なら「空」)> |
| 5 | 触るファイルがスコープどおり | PASS/FAIL | <git diff --name-only出力> |
| 6 | 初回登録フロー1本試験 | 収集確認済み/実行は検証待ち | <collect-onlyの該当試験名> |

## 固定値の変更有無(design.md §6・本計画§8)
- me不在時404(design §6-1): 変更なし / 変更あり(<前→後+理由>)
- 生SQL継続・ORM導入はws-3へ委譲(design §6-2): 変更なし / 変更あり(<前→後+理由>)
- エラー優先順序 UNDER_AGE>USER_EXISTS(design §6-3): 変更なし / 変更あり(<前→後+理由>)
- auth側lookupとのSELECT重複許容(design §6-4): 変更なし / 変更あり(<前→後+理由>)
- display_name上限なし(design §6-5): 変更なし / 変更あり(<前→後+理由>)
- 本計画§8のIF確定事項(IntegrityError分類・Callableシグネチャ・jsonb渡し・UUID正規化・lifespan独立判定): 変更なし / 変更あり(<前→後+理由>)

## スーパーバイザー検証手順(test-ci実行時)
1. `docker compose build api` — **apiイメージの再ビルドが必須**(make test-ci の compose up は再ビルドしないため・design §1.2確定値13)
2. `docker compose up -d --wait`
3. `make test-ci` — test_users_api.py(#1〜#6)を含む全体グリーンで完了条件2・6を検証
   (マイグレーション追加なしのため `make migrate` は不要。実行しても冪等)
4. マージ時のmain.py競合(ws-2と交点)は両側追記保持で解消する

## コミット一覧
<git log --oneline main..HEAD の出力>

## 補足(詰まった点・判断した点があれば)
```

完了後の最終返信は報告ファイルのパスと完了条件6項目の結果一覧(項目2・6は「test-ci=スーパーバイザー検証待ち」)。

---

### Task 1: usersパッケージ土台 + errors.py + age_years純粋関数

**Files:**
- Create: `backend/src/latch/users/__init__.py`(この時点ではdocstringのみ。再exportはTask 5)、`backend/src/latch/users/errors.py`、`backend/src/latch/users/service.py`(この時点では `age_years` のみ)
- Test: `backend/tests/unit/users/test_age.py`

**Interfaces:**
- Consumes: なし(最初のタスク)
- Produces: `latch.users.errors.UsersError(Exception)`(属性 `http_status: int`・`code: str`)+ 4サブクラス — `UnderAgeError`(422/"UNDER_AGE")・`UserExistsError`(409/"USER_EXISTS")・`UserNotFoundError`(404/"NOT_FOUND")・`DependencyUnavailableError`(503/"DEPENDENCY_UNAVAILABLE")。`latch.users.service.age_years(birth_date: date, today: date) -> int`(満年齢・design §2.3の式)。以降全タスクが利用

- [ ] **Step 1: パッケージ土台を作る**

```bash
mkdir -p backend/src/latch/users backend/tests/unit/users
printf '"""usersドメイン(M1 ws-1)。初回登録(POST /v1/users)と本人取得(GET /v1/users/me)。"""\n' > backend/src/latch/users/__init__.py
```

- [ ] **Step 2: 失敗するテストを書く**

`backend/tests/unit/users/test_age.py`:

```python
"""満年齢純粋関数 age_years: 暦日境界の全パターン(design §2.3・§4.1-1)。

判定は常に `age_years(birth, clock.jst_date()) < 18` の組合せで使う
(18歳の誕生日当日=18=登録可・前日=17=UNDER_AGE)。
"""

from datetime import date

from latch.users.errors import (
    DependencyUnavailableError,
    UnderAgeError,
    UserExistsError,
    UserNotFoundError,
)
from latch.users.service import age_years


def test_error_statuses_and_codes():
    # 05 第5節 エラー形式表の対応。ハンドラはこれを信じてstatus/codeを出す
    assert (UnderAgeError("x").http_status, UnderAgeError("x").code) == (
        422,
        "UNDER_AGE",
    )
    assert (UserExistsError("x").http_status, UserExistsError("x").code) == (
        409,
        "USER_EXISTS",
    )
    assert (UserNotFoundError("x").http_status, UserNotFoundError("x").code) == (
        404,
        "NOT_FOUND",
    )
    assert (
        DependencyUnavailableError("x").http_status,
        DependencyUnavailableError("x").code,
    ) == (503, "DEPENDENCY_UNAVAILABLE")


def test_18th_birthday_today_is_18():
    # 18歳の誕生日当日=18(登録可 — design §2.3)
    assert age_years(date(2008, 9, 28), date(2026, 9, 28)) == 18


def test_day_before_18th_birthday_is_17():
    assert age_years(date(2008, 9, 28), date(2026, 9, 27)) == 17


def test_feb29_birthday_in_common_year_adds_on_mar1():
    # 2月29日生まれ: うるう年でない年は3月1日に年齢が加算(タプル比較。
    # 法律の前日主義(2/28)と1日ずれるが自己申告値のため許容 — design §2.3)
    assert age_years(date(2008, 2, 29), date(2026, 2, 28)) == 17
    assert age_years(date(2008, 2, 29), date(2026, 3, 1)) == 18


def test_feb29_birthday_on_leap_year_today():
    assert age_years(date(2008, 2, 29), date(2028, 2, 29)) == 20


def test_future_birth_date_is_negative():
    # 未来日付=負の年齢=18歳未満としてUNDER_AGEで拒否(特別な検証を足さない)
    assert age_years(date(2030, 1, 1), date(2026, 9, 27)) == -4


def test_dec31_birthday_on_jan1_counts_previous_birthday():
    # 年またぎ: 12/31生まれの1/1は直前の誕生日まで数える
    assert age_years(date(2008, 12, 31), date(2026, 1, 1)) == 17
    assert age_years(date(2008, 12, 31), date(2026, 12, 31)) == 18


def test_adult_birth_date_various():
    assert age_years(date(1990, 4, 1), date(2026, 9, 27)) == 36
    assert age_years(date(2008, 9, 27), date(2026, 9, 27)) == 18
```

- [ ] **Step 3: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/users/test_age.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.users'`)

- [ ] **Step 4: 最小実装**

`backend/src/latch/users/errors.py`:

```python
"""users例外階層(M1 ws-1 design §3.1)。

auth/errors.py と同型 — 各例外は http_status と code(05 第5節のerror code)を
固定で持つ。auth側(AuthError)との共通基底は作らない(auth不変 — design §2.6。
main.py に users_error_handler を1個追加するだけ)。
例外メッセージにsubject・claim内容・display_nameを含めない(08 第2.4節)。
"""

from __future__ import annotations


class UsersError(Exception):
    """users系エラーの基底。http_status/code を持つ(ハンドラが消費する)。"""

    http_status: int
    code: str


class UnderAgeError(UsersError):
    """18歳未満の初回登録拒否(05 第5節・08 第4節 D-10)。"""

    http_status = 422
    code = "UNDER_AGE"


class UserExistsError(UsersError):
    """登録済みauth_subjectでの初回登録(05 第5節)。"""

    http_status = 409
    code = "USER_EXISTS"


class UserNotFoundError(UsersError):
    """User行不在(GET /v1/users/me — design §2.4の404解釈)。"""

    http_status = 404
    code = "NOT_FOUND"


class DependencyUnavailableError(UsersError):
    """DB等の依存障害(05 第5節「全API」)。"""

    http_status = 503
    code = "DEPENDENCY_UNAVAILABLE"
```

`backend/src/latch/users/service.py`:

```python
"""usersユースケース(M1 ws-1。design §2.1〜§2.5)。

永続化は text() 生SQLの関数(INSERT 1本+SELECT 1本)をCallableで注入する
(auth の user_lookup 注入と同型。ORMモデル・リポジトリ層の導入判断は
intents CRUD(ws-3 M1)に委ねる — design §2.1)。時刻列はClock由来の明示値
(DB時刻関数のDEFAULTは使わない)。UserService・make_user_serviceは後続タスクで
このファイルへ追加する。
"""

from __future__ import annotations

from datetime import date


def age_years(birth_date: date, today: date) -> int:
    """満年齢(design §2.3)。(月, 日)タプル比較 — 誕生日当日に加算。

    2月29日生まれは、その暦日に2月29日を持たない年は3月1日に加算される
    (自己申告値のため1日の差は許容して比較を単純に保つ)。
    """
    return today.year - birth_date.year - (
        (today.month, today.day) < (birth_date.month, birth_date.day)
    )
```

- [ ] **Step 5: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/users/test_age.py -v`
Expected: PASS(8件)

- [ ] **Step 6: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/users backend/tests/unit/users
git commit -m "feat: users例外階層と満年齢純粋関数(JST暦日タプル比較)"
```

---

### Task 2: UserService(register/get_me)+ IntegrityError制約名分類

**Files:**
- Modify: `backend/src/latch/users/service.py`(データクラス・UserService・`classify_integrity_error` を追加)
- Test: `backend/tests/unit/users/test_service.py`

**Interfaces:**
- Consumes: Task 1の例外階層と `age_years`、`latch.auth.tokens.AccessTokenClaims`(dataclass: `auth_provider`・`auth_subject`・`jti`・`sid`・`iat`・`exp`)、`latch.core.clock.Clock`/`FakeClock`
- Produces: `NewUser`(frozen dataclass: `display_name: str`・`birth_date: date`・`profile: dict`・`auth_provider: str`・`auth_subject: str`・`created_at: datetime`・`updated_at: datetime`)・`UserCreated`(`id: uuid.UUID`・`display_name: str`)・`UserRow`(`id`・`display_name`・`profile: dict`・`birth_date: date`)・`UserMe`(`id`・`display_name`・`profile`・`birth_date`・`profile_complete: bool`)・`CreateUserFn = Callable[[NewUser], Awaitable[uuid.UUID]]`・`FetchByAuthFn = Callable[[str, str], Awaitable[UserRow | None]]`・`classify_integrity_error(exc: IntegrityError) -> UsersError | None`・`UserService(*, clock: Clock, create_user: CreateUserFn, fetch_by_auth: FetchByAuthFn)` の `async register(*, claims, display_name, birth_date, profile) -> UserCreated` と `async get_me(*, claims) -> UserMe`。Task 3(make_user_service)・Task 4(routes)が利用

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/users/test_service.py`:

```python
"""UserService: 年齢検証・INSERT委譲・エラー変換・me導出(design §4.1-2・§4.1-3)。

永続化はスタブ関数で差し替え(外部プロセス不要・決定的)。IntegrityErrorの
制約名分類はasyncpg例外を偽装して純粋関数として検証する(実DB経路は
integration #4が所有 — design §2.2)。
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.users.errors import (
    DependencyUnavailableError,
    UserExistsError,
    UserNotFoundError,
    UnderAgeError,
)
from latch.users.service import (
    NewUser,
    UserCreated,
    UserRow,
    UserService,
    classify_integrity_error,
)

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-27 21:00
USER_ID = uuid.uuid4()

CLAIMS = AccessTokenClaims(
    auth_provider="google",
    auth_subject="sub-1",
    jti="jti-1",
    sid="sid-1",
    iat=NOW,
    exp=NOW + timedelta(seconds=3600),
)


class CreateRecorder:
    """スタブcreate_user: 呼び出し記録+結果/例外の差し込み。"""

    def __init__(self, result=USER_ID, error=None):
        self.calls: list[NewUser] = []
        self._result = result
        self._error = error

    async def __call__(self, new_user: NewUser) -> uuid.UUID:
        self.calls.append(new_user)
        if self._error is not None:
            raise self._error
        return self._result


class FetchRecorder:
    """スタブfetch_by_auth: 呼び出し記録+結果/例外の差し込み。"""

    def __init__(self, result=None, error=None):
        self.calls: list[tuple[str, str]] = []
        self._result = result
        self._error = error

    async def __call__(self, provider: str, subject: str) -> UserRow | None:
        self.calls.append((provider, subject))
        if self._error is not None:
            raise self._error
        return self._result


ROW = UserRow(
    id=USER_ID,
    display_name="テスト太郎",
    profile={"bio": "よろしく"},
    birth_date=date(1990, 4, 1),
)


def _svc(clock, create, fetch) -> UserService:
    return UserService(clock=clock, create_user=create, fetch_by_auth=fetch)


async def test_register_under_age_raises_and_never_persists():
    # design §2.2: 年齢検証が先。17歳ならINSERT(list)は呼ばれない
    clock = FakeClock(NOW)  # jst_date() = 2026-09-27
    create = CreateRecorder()
    svc = _svc(clock, create, FetchRecorder())
    with pytest.raises(UnderAgeError):
        await svc.register(
            claims=CLAIMS,
            display_name="17歳",
            birth_date=date(2008, 9, 28),  # 誕生日前日=17歳
            profile={},
        )
    assert create.calls == []


async def test_register_adult_persists_with_clock_timestamps():
    clock = FakeClock(NOW)
    create = CreateRecorder()
    svc = _svc(clock, create, FetchRecorder())
    created = await svc.register(
        claims=CLAIMS,
        display_name="テスト太郎",
        birth_date=date(1990, 4, 1),
        profile={"bio": "よろしく"},
    )
    assert created == UserCreated(id=USER_ID, display_name="テスト太郎")
    (new_user,) = create.calls
    assert new_user.display_name == "テスト太郎"
    assert new_user.birth_date == date(1990, 4, 1)
    assert new_user.profile == {"bio": "よろしく"}
    assert new_user.auth_provider == CLAIMS.auth_provider  # JWT claim由来
    assert new_user.auth_subject == CLAIMS.auth_subject
    # 時刻はClock由来の明示値(design §2.5・確定値11)
    assert new_user.created_at == NOW
    assert new_user.updated_at == NOW


async def test_register_jst_midnight_boundary():
    # Review Focus #1: JST暦日の深夜0時で判定が転換する(UTC比較の9時間ずれ排除)
    birth = date(2008, 9, 28)  # 2026-09-28が18歳の誕生日
    clock = FakeClock(NOW)
    clock.set(datetime(2026, 9, 27, 14, 59, 59, tzinfo=UTC))  # JST 9/27 23:59:59
    svc = _svc(clock, CreateRecorder(), FetchRecorder())
    with pytest.raises(UnderAgeError):
        await svc.register(
            claims=CLAIMS, display_name="境界", birth_date=birth, profile={}
        )
    clock.set(datetime(2026, 9, 27, 15, 0, 0, tzinfo=UTC))  # JST 9/28 00:00:00
    ok = await svc.register(
        claims=CLAIMS, display_name="境界", birth_date=birth, profile={}
    )
    assert ok.display_name == "境界"
    assert ok.id == USER_ID


async def test_register_propagates_user_exists():
    create = CreateRecorder(error=UserExistsError("user already registered"))
    svc = _svc(FakeClock(NOW), create, FetchRecorder())
    with pytest.raises(UserExistsError):
        await svc.register(
            claims=CLAIMS,
            display_name="x",
            birth_date=date(1990, 4, 1),
            profile={},
        )


async def test_register_wraps_generic_error_as_503():
    create = CreateRecorder(error=RuntimeError("db down"))
    svc = _svc(FakeClock(NOW), create, FetchRecorder())
    with pytest.raises(DependencyUnavailableError):
        await svc.register(
            claims=CLAIMS,
            display_name="x",
            birth_date=date(1990, 4, 1),
            profile={},
        )


async def test_get_me_row_present_returns_complete_true():
    fetch = FetchRecorder(result=ROW)
    svc = _svc(FakeClock(NOW), CreateRecorder(), fetch)
    me = await svc.get_me(claims=CLAIMS)
    assert me.id == USER_ID
    assert me.display_name == "テスト太郎"
    assert me.profile == {"bio": "よろしく"}
    assert me.birth_date == date(1990, 4, 1)
    assert me.profile_complete is True  # 行の存在=true(design §1.2-9)
    assert fetch.calls == [("google", "sub-1")]  # claims由来の照会


async def test_get_me_row_missing_raises_404():
    # design §2.4: 初回登録待ちJWTのme → 404 NOT_FOUND
    svc = _svc(FakeClock(NOW), CreateRecorder(), FetchRecorder(result=None))
    with pytest.raises(UserNotFoundError):
        await svc.get_me(claims=CLAIMS)


async def test_get_me_wraps_generic_error_as_503():
    svc = _svc(FakeClock(NOW), CreateRecorder(), FetchRecorder(error=RuntimeError("db down")))
    with pytest.raises(DependencyUnavailableError):
        await svc.get_me(claims=CLAIMS)


class _FakeAsyncpgError(Exception):
    """asyncpg例外の偽装(sqlstate・constraint_name 属性を持つ)。"""

    def __init__(self, sqlstate: str, constraint_name: str | None = None):
        super().__init__(f"fake dbapi error {sqlstate}")
        self.sqlstate = sqlstate
        self.constraint_name = constraint_name


def _integrity_error(sqlstate: str, constraint_name: str | None = None):
    return IntegrityError(
        "INSERT INTO users ...", {}, _FakeAsyncpgError(sqlstate, constraint_name)
    )


def test_classify_unique_violation_on_subject_constraint():
    # Review Focus #2: 対象制約のunique_violation(23505)のみ409へ
    err = classify_integrity_error(
        _integrity_error("23505", "uq_users_auth_provider_subject")
    )
    assert isinstance(err, UserExistsError)
    assert err.http_status == 409


def test_classify_other_constraint_returns_none():
    # 主キー違反等は409にせらず503扱い(None → 再送出)
    assert (
        classify_integrity_error(_integrity_error("23505", "users_pkey")) is None
    )


def test_classify_non_unique_violation_returns_none():
    # CHECK違反(23514)等は制約名が一致してもNone
    assert (
        classify_integrity_error(
            _integrity_error("23514", "uq_users_auth_provider_subject")
        )
        is None
    )
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/users/test_service.py -v`
Expected: FAIL(`ImportError: cannot import name 'NewUser'`)

- [ ] **Step 3: service.py へ UserService等を実装**

`backend/src/latch/users/service.py` — Task 1の内容に追加(最終形。Task 3で `_INSERT`/`_SELECT`/`make_user_service` がさらに追記される):

```python
"""usersユースケース(M1 ws-1。design §2.1〜§2.5)。

永続化は text() 生SQLの関数(INSERT 1本+SELECT 1本)をCallableで注入する
(auth の user_lookup 注入と同型。ORMモデル・リポジトリ層の導入判断は
intents CRUD(ws-3 M1)に委ねる — design §2.1)。時刻列はClock由来の明示値
(DB時刻関数のDEFAULTは使わない)。検証順序は年齢(422)→INSERT(409/503)に
固定(design §2.2 — 17歳かつ登録済みならUNDER_AGE)。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.exc import IntegrityError

from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import Clock
from latch.users.errors import (
    DependencyUnavailableError,
    UnderAgeError,
    UserExistsError,
    UserNotFoundError,
    UsersError,
)


def age_years(birth_date: date, today: date) -> int:
    """満年齢(design §2.3)。(月, 日)タプル比較 — 誕生日当日に加算。

    2月29日生まれは、その暦日に2月29日を持たない年は3月1日に加算される
    (自己申告値のため1日の差は許容して比較を単純に保つ)。
    """
    return today.year - birth_date.year - (
        (today.month, today.day) < (birth_date.month, birth_date.day)
    )


# users表のUNIQUE制約名(alembic 0001)。IntegrityError分類に使う
_USERS_SUBJECT_CONSTRAINT = "uq_users_auth_provider_subject"


def classify_integrity_error(exc: IntegrityError) -> UsersError | None:
    """UNIQUE(auth_provider, auth_subject)違反のみ409 USER_EXISTSへ変換。

    asyncpgの例外(exc.orig)は sqlstate と constraint_name 属性を持つ。
    sqlstate=23505(unique_violation)+ 対象制約名のときのみ UserExistsError。
    それ以外はNone(呼び出し側で再送出 → UserServiceが503へ包む — design §2.2)。
    """
    orig = exc.orig
    if getattr(orig, "sqlstate", None) != "23505":
        return None
    if getattr(orig, "constraint_name", None) == _USERS_SUBJECT_CONSTRAINT:
        return UserExistsError("user already registered")
    return None


@dataclass(frozen=True)
class NewUser:
    """registerの中間表現(INSERT列・時刻はClock由来の明示値 — design §2.5)。"""

    display_name: str
    birth_date: date
    profile: dict
    auth_provider: str
    auth_subject: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class UserCreated:
    """POST /v1/users の201応答本体 {"user": {...}}(05 第5節)。"""

    id: uuid.UUID
    display_name: str


@dataclass(frozen=True)
class UserRow:
    """fetch_by_auth の戻り(SELECT全列 — design §3.1)。"""

    id: uuid.UUID
    display_name: str
    profile: dict
    birth_date: date


@dataclass(frozen=True)
class UserMe:
    """GET /v1/users/me の200応答本体(05 第5節)。profile_completeは常にTrue。"""

    id: uuid.UUID
    display_name: str
    profile: dict
    birth_date: date
    profile_complete: bool


CreateUserFn = Callable[[NewUser], Awaitable[uuid.UUID]]
FetchByAuthFn = Callable[[str, str], Awaitable[UserRow | None]]


class UserService:
    """register / get_me ユースケース(05 第5節)。"""

    def __init__(
        self,
        *,
        clock: Clock,
        create_user: CreateUserFn,
        fetch_by_auth: FetchByAuthFn,
    ) -> None:
        self._clock = clock
        self._create_user = create_user
        self._fetch_by_auth = fetch_by_auth

    async def register(
        self,
        *,
        claims: AccessTokenClaims,
        display_name: str,
        birth_date: date,
        profile: dict,
    ) -> UserCreated:
        """初回登録。User行はJWT claim(auth_provider/auth_subject)に紐けて生成。"""
        try:
            return await self._register(
                claims=claims,
                display_name=display_name,
                birth_date=birth_date,
                profile=profile,
            )
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("users dependency unavailable") from exc

    async def _register(
        self,
        *,
        claims: AccessTokenClaims,
        display_name: str,
        birth_date: date,
        profile: dict,
    ) -> UserCreated:
        if age_years(birth_date, self._clock.jst_date()) < 18:
            raise UnderAgeError("under 18 years old")
        now = self._clock.now()
        new_user = NewUser(
            display_name=display_name,
            birth_date=birth_date,
            profile=profile,
            auth_provider=claims.auth_provider,
            auth_subject=claims.auth_subject,
            created_at=now,
            updated_at=now,
        )
        user_id = await self._create_user(new_user)
        return UserCreated(id=user_id, display_name=display_name)

    async def get_me(self, *, claims: AccessTokenClaims) -> UserMe:
        """本人の行を返す。行なし=初回登録待ち → 404(design §2.4)。"""
        try:
            row = await self._fetch_by_auth(
                claims.auth_provider, claims.auth_subject
            )
        except UsersError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("users dependency unavailable") from exc
        if row is None:
            raise UserNotFoundError("user not found")
        return UserMe(
            id=row.id,
            display_name=row.display_name,
            profile=row.profile,
            birth_date=row.birth_date,
            profile_complete=True,  # 行の存在=true(design §1.2-9)
        )
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/users/test_service.py -v`
Expected: PASS(11件)

- [ ] **Step 5: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/users/service.py backend/tests/unit/users/test_service.py
git commit -m "feat: UserService(register/get_me)とIntegrityError制約名分類"
```

---

### Task 3: make_user_service — 実SQL関数の工場

**Files:**
- Modify: `backend/src/latch/users/service.py`(`_coerce_user_id`・`_INSERT`・`_SELECT`・`make_user_service` を追加)
- Test: `backend/tests/unit/users/test_service.py`(1件追記)

**Interfaces:**
- Consumes: Task 2の `UserService`・`CreateUserFn`/`FetchByAuthFn`・`classify_integrity_error`、`latch.core.db` の `AsyncEngine`(引数型のみ・unit試験では接続しない)
- Produces: `make_user_service(*, clock: Clock, engine: AsyncEngine) -> UserService`(実SQL関数をクロージャで束ねて構築 — design §2.5。INSERTは `RETURNING id`・SELECTは全列。**SQLの実行はintegration #1/#4/#5が所有**。Task 4のmain.py lifespanが利用

- [ ] **Step 1: 失敗するテストを書く(test_service.py に追記)**

import節へ追記(既存の `from latch.users.service import (...)` へ `make_user_service` を加える):

```python
from latch.core.db import create_db_engine
from latch.settings import Settings
from latch.users.service import make_user_service
```

ファイル末尾へ追記:

```python
async def test_make_user_service_returns_service():
    # 実SQL関数の実行はintegrationが所有。unitでは構築がUserServiceを
    # 返すことのみ(engineの接続は呼び出し時まで発生しない)
    engine = create_db_engine(Settings(app_env="ci"))
    try:
        svc = make_user_service(clock=FakeClock(NOW), engine=engine)
        assert isinstance(svc, UserService)
    finally:
        await engine.dispose()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/users/test_service.py -v`
Expected: FAIL(収集エラー `ImportError: cannot import name 'make_user_service'` — import節に含まれるためファイル全体がerror扱いになる)

- [ ] **Step 3: service.py へ実SQL工場を追加**

import節へ追記(ruffのisort順へ並べ替える):

```python
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
```

ファイル末尾(`UserService` クラスの後)へ追加:

```python
def _coerce_user_id(value: object) -> uuid.UUID:
    """行のid値をUUIDへ正規化する。

    asyncpgはuuid列をUUID「インスタンス」で返す(uuid.UUID(row[0]) は
    AttributeErrorになる — M0 ws-3検証のtest-ci失敗1と同じ落ち穴)。
    auth/service.py と同じ対応(coreへ共有化せずauth不変を優先 — design §2.6)。
    """
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


# idはDEFAULT gen_random_uuid()に任せRETURNINGで受け取る(§2.5)。profileは
# text()経由ではSQLAlchemyの型変換が入らないためJSON文字列で渡してCASTする
_INSERT = text("""
    INSERT INTO users
        (display_name, profile, birth_date, auth_provider, auth_subject,
         created_at, updated_at)
    VALUES
        (:display_name, CAST(:profile AS jsonb), :birth_date, :auth_provider,
         :auth_subject, :created_at, :updated_at)
    RETURNING id
""")

# auth_provider+auth_subject はUNIQUE(uq_users_auth_provider_subject)なので高々1行
_SELECT = text("""
    SELECT id, display_name, profile, birth_date
    FROM users
    WHERE auth_provider = :provider AND auth_subject = :subject
""")


def make_user_service(*, clock: Clock, engine: AsyncEngine) -> UserService:
    """実SQL関数(text())を束ねてUserServiceを構築する(design §2.5)。

    engineはクロージャで束縛する(UserService自身はengineを知らない)。
    INSERT衝突はUNIQUE制約で検出し、対象制約のみUserExistsErrorへ変換
    (それ以外のIntegrityErrorは再送出 → UserServiceが503へ包む — design §2.2)。
    """

    async def create_user(new_user: NewUser) -> uuid.UUID:
        try:
            async with engine.begin() as conn:
                row_id = await conn.scalar(
                    _INSERT,
                    {
                        "display_name": new_user.display_name,
                        "profile": json.dumps(new_user.profile),
                        "birth_date": new_user.birth_date,
                        "auth_provider": new_user.auth_provider,
                        "auth_subject": new_user.auth_subject,
                        "created_at": new_user.created_at,
                        "updated_at": new_user.updated_at,
                    },
                )
                return _coerce_user_id(row_id)
        except IntegrityError as exc:
            classified = classify_integrity_error(exc)
            if classified is not None:
                raise classified from exc
            raise

    async def fetch_by_auth(provider: str, subject: str) -> UserRow | None:
        async with engine.connect() as conn:
            result = await conn.execute(
                _SELECT, {"provider": provider, "subject": subject}
            )
            row = result.first()
            if row is None:
                return None
            profile = row.profile
            if isinstance(profile, str):
                # Review Focus #3: asyncpgのjsonbはstrで返る場合がある
                profile = json.loads(profile)
            return UserRow(
                id=_coerce_user_id(row.id),
                display_name=row.display_name,
                profile=profile,
                birth_date=row.birth_date,
            )

    return UserService(clock=clock, create_user=create_user, fetch_by_auth=fetch_by_auth)
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/users/test_service.py -v`
Expected: PASS(12件)

- [ ] **Step 5: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/users/service.py backend/tests/unit/users/test_service.py
git commit -m "feat: make_user_service(実SQL束ね・RETURNING id・制約名分類接続)"
```

---

### Task 4: routes.py + main.py統合(ルータ・ハンドラ・lifespan独立判定)

**Files:**
- Create: `backend/src/latch/users/routes.py`
- Modify: `backend/src/latch/main.py`(usersルータinclude・lifespanへusers_service構築+独立スキップ判定・UsersErrorハンドラ・`create_app` 第4引数。**auth関連行は変更しない**)
- Test: `backend/tests/unit/users/test_routes.py`

**Interfaces:**
- Consumes: `latch.auth.deps.require_authenticated`(C3の強制点・auth不変)、Task 2/3の `UserService`・`UserCreated`・`UserMe`、`latch.users.errors.UsersError`、既存 `create_app`
- Produces: `latch.users.routes.users_router`(prefix `/v1/users`・`dependencies=[Depends(require_authenticated)]`・POST ""(201)・GET "/me")・`latch.users.routes.get_users_service(request) -> UserService`(app.state.users_service へのアクセス)・入出力モデル(`UserCreateRequest`・`UserProfileInput`・`UserCreatedResponse`・`UserMeResponse`)。`main.create_app(clock=None, settings=None, auth_service=None, users_service=None)`(第4引数追加・指定時はlifespanのusers構築をスキップ)。Task 5のintegrationとws-3(intents CRUD)が利用

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/users/test_routes.py`:

```python
"""usersルータ: 応答形状・エラーenvelope・400/422振り分け(design §4.1-4)。

create_app へスタブUserServiceを注入し、require_authenticated は
dependency_overrides で差し替える(users単体の試験でauthスタックを構築しない
— design §2.5。401経路の実証はintegration #6が所有)。
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.main import create_app
from latch.settings import Settings
from latch.users.errors import UnderAgeError, UserNotFoundError
from latch.users.service import UserCreated, UserMe

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
USER_ID = uuid.uuid4()

CLAIMS = AccessTokenClaims(
    auth_provider="google",
    auth_subject="sub-1",
    jti="jti-1",
    sid="sid-1",
    iat=NOW,
    exp=NOW + timedelta(seconds=3600),
)

ME = UserMe(
    id=USER_ID,
    display_name="テスト太郎",
    profile={"bio": "よろしく"},
    birth_date=date(1990, 4, 1),
    profile_complete=True,
)


class StubUserService:
    """ルーティング試験用のUserServiceスタブ(design §4.1-4)。"""

    def __init__(self):
        self.registered: list[dict] = []
        self.register_error: Exception | None = None
        self.me: UserMe | None = ME

    async def register(self, *, claims, display_name, birth_date, profile):
        self.registered.append(
            {
                "claims": claims,
                "display_name": display_name,
                "birth_date": birth_date,
                "profile": profile,
            }
        )
        if self.register_error is not None:
            raise self.register_error
        return UserCreated(id=USER_ID, display_name=display_name)

    async def get_me(self, *, claims):
        if self.me is None:
            raise UserNotFoundError("user not found")
        return self.me


@pytest.fixture
def stub():
    return StubUserService()


@pytest.fixture
def app(stub):
    app = create_app(
        clock=FakeClock(NOW),
        settings=Settings(app_env="ci"),
        users_service=stub,
    )
    app.dependency_overrides[require_authenticated] = lambda: CLAIMS
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _assert_envelope(body: dict, code: str) -> None:
    # 05 第5節 エラー形式: {"error": {code, message, details}}
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    assert body["error"]["details"] is None
    assert body["error"]["message"]


def test_users_service_injected_on_state(app, stub):
    # create_app 第4引数 → app.state.users_service(テスト注入の経路)
    assert app.state.users_service is stub


async def test_register_returns_201_shape(client, stub):
    resp = await client.post(
        "/v1/users",
        json={
            "display_name": "テスト太郎",
            "birth_date": "1990-04-01",
            "profile": {"bio": "よろしく"},
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == {"user"}
    assert set(body["user"]) == {"id", "display_name"}
    assert body["user"]["id"] == str(USER_ID)  # UUID文字列
    assert body["user"]["display_name"] == "テスト太郎"
    (call,) = stub.registered
    assert call["claims"] is CLAIMS
    assert call["display_name"] == "テスト太郎"
    assert str(call["birth_date"]) == "1990-04-01"
    assert call["profile"] == {"bio": "よろしく"}


async def test_register_profile_omitted_normalizes_empty(client, stub):
    # Review Focus #5: profile省略 → サービスへ渡るのは {}
    resp = await client.post(
        "/v1/users",
        json={"display_name": "bioなし", "birth_date": "1990-04-01"},
    )
    assert resp.status_code == 201
    (call,) = stub.registered
    assert call["profile"] == {}


async def test_register_bio_none_normalizes_empty(client, stub):
    # Review Focus #5: {"bio": null} → {"bio": null} を格納しない(design §2.7)
    resp = await client.post(
        "/v1/users",
        json={
            "display_name": "bio-null",
            "birth_date": "1990-04-01",
            "profile": {"bio": None},
        },
    )
    assert resp.status_code == 201
    (call,) = stub.registered
    assert call["profile"] == {}


async def test_register_missing_display_name_422(client):
    resp = await client.post("/v1/users", json={"birth_date": "1990-04-01"})
    assert resp.status_code == 422
    _assert_envelope(resp.json(), "VALIDATION_ERROR")


async def test_register_empty_display_name_422(client):
    # 空文字は「必須欠落」相当(min_length=1 — design §2.7)
    resp = await client.post(
        "/v1/users", json={"display_name": "", "birth_date": "1990-04-01"}
    )
    assert resp.status_code == 422
    _assert_envelope(resp.json(), "VALIDATION_ERROR")


async def test_register_bad_birth_date_format_422(client):
    resp = await client.post(
        "/v1/users",
        json={"display_name": "形式不正", "birth_date": "1990/04/01"},
    )
    assert resp.status_code == 422
    _assert_envelope(resp.json(), "VALIDATION_ERROR")


async def test_register_under_age_422_envelope(client, stub):
    # UNDER_AGE(ユースケース内の検証)のenvelope化 — design §2.2の順序1〜2
    stub.register_error = UnderAgeError("under 18 years old")
    resp = await client.post(
        "/v1/users",
        json={"display_name": "17歳", "birth_date": "2008-09-28"},
    )
    assert resp.status_code == 422
    _assert_envelope(resp.json(), "UNDER_AGE")


async def test_me_returns_200_shape(client):
    resp = await client.get("/v1/users/me")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {
        "id",
        "display_name",
        "profile",
        "birth_date",
        "profile_complete",
    }
    assert body["id"] == str(USER_ID)
    assert body["display_name"] == "テスト太郎"
    assert body["profile"] == {"bio": "よろしく"}
    assert body["birth_date"] == "1990-04-01"  # ISO文字列(本人のみ — 05 §5)
    assert body["profile_complete"] is True


async def test_me_missing_row_404_envelope(client, stub):
    stub.me = None
    resp = await client.get("/v1/users/me")
    assert resp.status_code == 404
    _assert_envelope(resp.json(), "NOT_FOUND")


async def test_malformed_json_400(client):
    # 既存ハンドラの振り分けがusersルータにも効く(確認値7)
    resp = await client.post(
        "/v1/users",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    _assert_envelope(resp.json(), "MALFORMED_REQUEST")
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/users/test_routes.py -v`
Expected: FAIL(app fixtureの実行時に `TypeError: create_app() got an unexpected keyword argument 'users_service'`。収集自体は成功する)

- [ ] **Step 3: routes.py を実装**

`backend/src/latch/users/routes.py`:

```python
"""usersルータ(M1 ws-1。05 第5節 認証・ユーザー系)。

POST /v1/users(初回登録・201)と GET /v1/users/me(本人のみ・200)。
ルータ単位で require_authenticated を付す(C3の強制方法 — 05 第5節冒頭)。
profileの正規化(bio=None → bioキーなし)はこの層で行う(design §2.7)。
ログはイベント名と結果/コードのみ — subject・claim値・display_nameは出さない
(08 第2.4節)。
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.users.service import UserCreated, UserMe, UserService

logger = logging.getLogger("latch.users")

users_router = APIRouter(
    prefix="/v1/users",
    tags=["users"],
    dependencies=[Depends(require_authenticated)],  # C3(05 第5節冒頭)
)


def get_users_service(request: Request) -> UserService:
    """app.state.users_service へのアクセス(lifespanまたはテスト注入で載る)。"""
    return request.app.state.users_service


class UserProfileInput(BaseModel):
    """登録リクエストのprofileオブジェクト(bioのみ — 05 第5節)。"""

    bio: str | None = None


class UserCreateRequest(BaseModel):
    # 上限なし・文字種検証なし(docsに規定なし — design §2.7)
    display_name: str = Field(min_length=1)  # 空文字=必須欠落相当(422)
    birth_date: date  # YYYY-MM-DD以外は422(既存ハンドラがenvelope化)
    profile: UserProfileInput | None = None


class UserCreatedBody(BaseModel):
    id: uuid.UUID
    display_name: str


class UserCreatedResponse(BaseModel):
    """POST /v1/users の201応答(05 第5節・入れ子)。"""

    user: UserCreatedBody


class UserMeResponse(BaseModel):
    """GET /v1/users/me の200応答(05 第5節)。birth_dateは本人のみに返す。"""

    id: uuid.UUID
    display_name: str
    profile: dict
    birth_date: date
    profile_complete: bool


@users_router.post("", status_code=201, response_model=UserCreatedResponse)
async def register_users(
    body: UserCreateRequest,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[UserService, Depends(get_users_service)],
) -> UserCreatedResponse:
    """初回登録。auth_provider/auth_subject はJWT claimに紐付く(05 第5節)。"""
    bio = body.profile.bio if body.profile is not None else None
    profile = {"bio": bio} if bio is not None else {}
    created: UserCreated = await svc.register(
        claims=claims,
        display_name=body.display_name,
        birth_date=body.birth_date,
        profile=profile,
    )
    logger.info("users.register ok")
    return UserCreatedResponse(
        user=UserCreatedBody(id=created.id, display_name=created.display_name)
    )


@users_router.get("/me", response_model=UserMeResponse)
async def get_users_me(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[UserService, Depends(get_users_service)],
) -> UserMeResponse:
    """本人のみ(05 第5節)。me応答にjti/sid等のセッション情報を含めない。"""
    me: UserMe = await svc.get_me(claims=claims)
    logger.info("users.me ok")
    return UserMeResponse(
        id=me.id,
        display_name=me.display_name,
        profile=me.profile,
        birth_date=me.birth_date,
        profile_complete=me.profile_complete,
    )
```

(注: ルータ単位の `dependencies=[Depends(require_authenticated)]` に加え各エンドポイントが同じ依存をclaims受取で宣言するが、FastAPIは同一依存をキャッシュするため検証は1回だけ走る)

- [ ] **Step 4: main.py を書き換える**

`backend/src/latch/main.py` を次のとおり変更する(変更点は4箇所のみ。`/health`・authルータinclude・AuthErrorハンドラ・RequestValidationErrorハンドラはそのまま):

docstringの差し替え:

```python
"""FastAPIアプリケーションファクトリ(M0 ws-3で認証・M1 ws-1でusers APIを統合)。

/health は運用プローブ用でありv1 API契約の外に置く(C3の対象外)。
server_time は get_clock() 由来 — Clock差し替えが全経路で効くことの生の消費者。
lifespanでredis・db engine・AuthService・UserServiceを構築する。auth_service /
users_service はcreate_app引数で注入済みなら該当サービスの構築を個別にスキップ
する(サービスごとの独立判定 — M1 ws-1 design §2.5)。
"""
```

import節へ追記:

```python
from latch.users.errors import UsersError
from latch.users.routes import users_router
from latch.users.service import make_user_service
```

ロガー定義の行へ追記:

```python
logger = logging.getLogger("latch.auth")
users_logger = logging.getLogger("latch.users")
```

`_lifespan` を丸ごと置き換え:

```python
@asynccontextmanager
async def _lifespan(app: FastAPI):
    skip_auth = hasattr(app.state, "auth_service")
    skip_users = hasattr(app.state, "users_service")
    if skip_auth and skip_users:
        # テスト注入済み(create_app 引数)— 依存リソースごと構築しない
        yield
        return
    settings: Settings = app.state.settings
    redis_client = aioredis.Redis.from_url(settings.redis_url, decode_responses=True)
    engine = create_db_engine(settings)
    app.state.db_engine = engine
    if not skip_auth:
        app.state.auth_service = build_auth_service(
            clock=app.state.clock,
            settings=settings,
            redis_client=redis_client,
            user_lookup=make_user_lookup(engine),
        )
    if not skip_users:
        app.state.users_service = make_user_service(
            clock=app.state.clock, engine=engine
        )
    try:
        yield
    finally:
        await redis_client.aclose()
        await engine.dispose()
```

`create_app` へ第4引数とstate設定を追加:

```python
def create_app(
    clock: Clock | None = None,
    settings: Settings | None = None,
    auth_service=None,
    users_service=None,
) -> FastAPI:
    app = FastAPI(title="LATCH API", lifespan=_lifespan)
    app.state.clock = clock if clock is not None else SystemClock()
    app.state.settings = settings if settings is not None else Settings()
    if auth_service is not None:
        app.state.auth_service = auth_service
    if users_service is not None:
        app.state.users_service = users_service
```

ルータincludeの行へ追記(`app.include_router(logout_router)` の直後):

```python
    app.include_router(users_router)
```

AuthErrorハンドラの後にUsersErrorハンドラを追加:

```python
    @app.exception_handler(UsersError)
    async def users_error_handler(request: Request, exc: UsersError) -> JSONResponse:
        users_logger.warning("users.error code=%s", exc.code)
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(exc.code, str(exc)),
        )
```

- [ ] **Step 5: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/users/ tests/unit/auth/test_routes.py tests/unit/test_app_health.py -v`
Expected: PASS(users新規: test_age 8件 + test_service 12件 + test_routes 11件 + 既存authルート試験 + `/health` 試験もグリーン — main.py変更の回帰)

- [ ] **Step 6: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/users/routes.py backend/src/latch/main.py backend/tests/unit/users/test_routes.py
git commit -m "feat: usersルータ(POST /v1/users・GET /v1/users/me)とmain統合"
```

---

### Task 5: 公開IFの再export + integration試験 + 受渡し検証と報告

**Files:**
- Modify: `backend/src/latch/users/__init__.py`(再export)
- Create: `backend/tests/integration/test_users_api.py`(作成のみ・実行しない)、`docs/plans/M1/ws-1-report.md`(§7の形式)

**Interfaces:**
- Consumes: 全タスク
- Produces: `latch.users` 公開API(ws-3 intents CRUD以降はこれ越しにusersを利用する)。§4.2-1〜6のintegration試験コード(実行はスーパーバイザー)。完了条件6項目の証拠と報告

- [ ] **Step 1: `users/__init__.py` を再exportへ書き換える**

`backend/src/latch/users/__init__.py` の内容を丸ごと置き換える:

```python
"""usersドメイン(M1 ws-1)。初回登録(POST /v1/users)と本人取得(GET /v1/users/me)。

M1以降の呼び出し側(intents CRUD等)はこのパッケージ越しにUserService・
users_router・例外を利用する(design §3.1)。
"""

from latch.users.errors import (
    DependencyUnavailableError,
    UnderAgeError,
    UserExistsError,
    UserNotFoundError,
    UsersError,
)
from latch.users.routes import users_router
from latch.users.service import (
    UserCreated,
    UserMe,
    UserRow,
    UserService,
    age_years,
    make_user_service,
)

__all__ = [
    "DependencyUnavailableError",
    "UnderAgeError",
    "UserCreated",
    "UserExistsError",
    "UserMe",
    "UserNotFoundError",
    "UserRow",
    "UserService",
    "UsersError",
    "age_years",
    "make_user_service",
    "users_router",
]
```

- [ ] **Step 2: integration試験ファイルを作成(作成のみ・実行しない)**

`backend/tests/integration/test_users_api.py`:

```python
"""users API(POST /v1/users・GET /v1/users/me)のci環境実証(M1 ws-1 design §4.2)。

実HTTP(compose api=127.0.0.1:8000)・実DB。**実行はapiイメージ再ビルド後の
make test-ci のみ**(make test-ci の compose up はイメージを再ビルドしない
— design §1.2確定値13。ws-2並走中は作成のみで、スーパーバイザーが検証時に
実行)。subjectは実行ごとにユニークな値(uuid接尾辞)を用い、User行は試験内で
DELETE(共有ci-dbの汚染回避 — M0 ws-3と同じ規約)。
"""

import asyncio
import sys
import uuid as uuid_mod
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock

pytestmark = pytest.mark.integration


def _unique_subject(prefix: str) -> str:
    return f"m1ws1-{prefix}-{uuid_mod.uuid4().hex[:12]}"


async def _cli_idp_token(provider: str, subject: str) -> str:
    """内部ツール(python -m latch.auth)でテストユーザーJWTを発行(10 第1節)。"""
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


async def _exchange(api_client, provider: str, subject: str) -> dict:
    idp_token = await _cli_idp_token(provider, subject)
    resp = await api_client.post(
        "/v1/auth/token", json={"provider": provider, "idp_token": idp_token}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _birth_17() -> str:
    """実行日(jst_date)時点で常に17歳になるbirth_date(design §4.2-2)。

    「18歳の誕生日の前日」= 今日の18年前同月同日の翌日。12/31実行(年跨ぎ)・
    2/28実行(うるう日近傍)のいずれでも age_years の式により常に17になる。
    """
    today = SystemClock().jst_date()
    tomorrow_birthday = date(today.year - 18, today.month, today.day) + timedelta(
        days=1
    )
    return tomorrow_birthday.isoformat()


async def _delete_users_by_subject(db_engine, subject: str) -> None:
    async with db_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM users WHERE auth_subject = :s"), {"s": subject}
        )


async def test_1_registration_full_flow(api_client, db_engine):
    """§4.2-1: token(false)→登録201→me 200→再token(true)。完了条件6の本体"""
    provider, subject = "google", _unique_subject("flow")
    try:
        first = await _exchange(api_client, provider, subject)
        assert first["user"] == {"id": None, "profile_complete": False}

        headers = {"Authorization": f"Bearer {first['access_token']}"}
        created = await api_client.post(
            "/v1/users",
            headers=headers,
            json={
                "display_name": "テスト太郎",
                "birth_date": "1990-04-01",
                "profile": {"bio": "よろしく"},
            },
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert set(body) == {"user"}
        assert set(body["user"]) == {"id", "display_name"}
        user_id = body["user"]["id"]
        uuid_mod.UUID(user_id)  # UUID文字列形式(新規採番)
        assert body["user"]["display_name"] == "テスト太郎"

        me = await api_client.get("/v1/users/me", headers=headers)
        assert me.status_code == 200, me.text
        me_body = me.json()
        assert set(me_body) == {
            "id",
            "display_name",
            "profile",
            "birth_date",
            "profile_complete",
        }
        assert me_body["id"] == user_id
        assert me_body["birth_date"] == "1990-04-01"  # 申告どおりのISO文字列
        assert me_body["profile"] == {"bio": "よろしく"}  # dictとして出る
        assert me_body["profile_complete"] is True

        again = await _exchange(api_client, provider, subject)
        assert again["user"]["id"] == user_id
        assert again["user"]["profile_complete"] is True
    finally:
        await _delete_users_by_subject(db_engine, subject)


async def test_2_under_age_rejected_and_no_row(api_client, db_engine):
    """§4.2-2: 17歳相当→422 UNDER_AGE・DBに行なし"""
    subject = _unique_subject("u17")
    try:
        body = await _exchange(api_client, "google", subject)
        resp = await api_client.post(
            "/v1/users",
            headers={"Authorization": f"Bearer {body['access_token']}"},
            json={"display_name": "17歳", "birth_date": _birth_17()},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == "UNDER_AGE"
        async with db_engine.connect() as conn:
            count = await conn.scalar(
                text("SELECT count(*) FROM users WHERE auth_subject = :s"),
                {"s": subject},
            )
        assert count == 0
    finally:
        await _delete_users_by_subject(db_engine, subject)


async def test_3_validation_errors(api_client):
    """§4.2-3: display_name欠落・birth_date形式不正→422 VALIDATION_ERROR"""
    body = await _exchange(api_client, "google", _unique_subject("v"))
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    missing = await api_client.post(
        "/v1/users", headers=headers, json={"birth_date": "1990-04-01"}
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "VALIDATION_ERROR"
    bad_format = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "形式不正", "birth_date": "1990/04/01"},
    )
    assert bad_format.status_code == 422
    assert bad_format.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_4_duplicate_registration_409(api_client, db_engine):
    """§4.2-4: 同一subjectでの再登録→409 USER_EXISTS(UNIQUE制約経路)"""
    subject = _unique_subject("dup")
    try:
        first = await _exchange(api_client, "google", subject)
        ok = await api_client.post(
            "/v1/users",
            headers={"Authorization": f"Bearer {first['access_token']}"},
            json={"display_name": "先着", "birth_date": "1990-04-01"},
        )
        assert ok.status_code == 201, ok.text
        # 同一subjectで新JWTを取得して再登録
        second = await _exchange(api_client, "google", subject)
        retry = await api_client.post(
            "/v1/users",
            headers={"Authorization": f"Bearer {second['access_token']}"},
            json={"display_name": "後着", "birth_date": "1990-04-01"},
        )
        assert retry.status_code == 409, retry.text
        assert retry.json()["error"]["code"] == "USER_EXISTS"
    finally:
        await _delete_users_by_subject(db_engine, subject)


async def test_5_me_without_user_row_404(api_client):
    """§4.2-5: 未登録subjectでGET /v1/users/me→404 NOT_FOUND(design §2.4)"""
    body = await _exchange(api_client, "google", _unique_subject("nf"))
    resp = await api_client.get(
        "/v1/users/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_6_unauthenticated_rejected_on_both_endpoints(api_client):
    """§4.2-6: Authorization欠落・改ざんJWT→401 UNAUTHENTICATED(両エンドポイント)"""
    no_auth_post = await api_client.post(
        "/v1/users", json={"display_name": "x", "birth_date": "1990-04-01"}
    )
    assert no_auth_post.status_code == 401
    assert no_auth_post.json()["error"]["code"] == "UNAUTHENTICATED"
    no_auth_me = await api_client.get("/v1/users/me")
    assert no_auth_me.status_code == 401
    assert no_auth_me.json()["error"]["code"] == "UNAUTHENTICATED"

    body = await _exchange(api_client, "google", _unique_subject("t"))
    token = body["access_token"]
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    resp = await api_client.get(
        "/v1/users/me", headers={"Authorization": f"Bearer {tampered}"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"
```

(§4.2-7の事後DELETEは各試験のtry/finallyに組み込み済み。18歳の当日境界はunit(test_register_jst_midnight_boundary)が証明する — compose api実プロセスはClock固定のためintegrationでは17歳・成人の代表値のみ、design §4.2後段どおり)

- [ ] **Step 3: 収集確認(unit実行でintegrationファイルのimportが通る)**

```bash
make test
cd backend && uv run pytest --collect-only tests/integration/test_users_api.py -q
```

Expected: `make test` がグリーン(収集エラーなし)。collect-only は6件の試験を表示(`-m "not integration"` でdeselectされるが収集はされる)

- [ ] **Step 4: 完了条件3 — 実時間参照の所在**

```bash
rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src
cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v
```

Expected: rgのヒットが `backend/src/latch/core/clock.py` の行のみ。arch test はexit 0(users/配下が自動スキャン対象に含まれる)

- [ ] **Step 5: 完了条件4・5 — 差分の所在**

```bash
git diff --stat main -- backend/alembic backend/pyproject.toml backend/uv.lock backend/src/latch/settings.py compose.yaml Makefile
git diff --name-only main | sort
git status --short
```

Expected: 1つ目は出力なし。name-onlyは§4の一覧と完全一致(main.py + users 4ファイル + unit/users 3ファイル + test_users_api.py + ws-1-report.md)。statusは空

- [ ] **Step 6: ruff format を通す**

```bash
cd backend && uv run ruff format . && uv run ruff check .
git status --short
```

変更があった場合のみ:

```bash
git add -u && git commit -m "style: ruff format 適用"
```

- [ ] **Step 7: 報告ファイルを作成してコミット**

`docs/plans/M1/ws-1-report.md` を §7 の形式で作成する。Step 3〜5の出力要点を貼る。項目2・6は「**test-ci=スーパーバイザー検証待ち**」(§0)と記録する。固定値の変更有無(design §6の6論点+本計画§8のIF確定事項)を確認し記録する。

```bash
git add docs/plans/M1/ws-1-report.md
git commit -m "docs: M1 ws-1の実行報告(完了条件6項目の証拠・test-ciは検証待ち)"
```

- [ ] **Step 8: 最終返信**

報告ファイルのパスと完了条件6項目の結果一覧を返信する(項目2・6は「test-ci=スーパーバイザー検証待ち」)。FAILが1つでもあれば、それも隠さず返信する。

---

## 8. design IF案からの確定事項(本計画が固定した実装詳細。変更時は報告必須)

design.md §3.1のインターフェース案は「実装詳細は計画書・TDDで確定」とされており、本計画は次のとおり確定した:

1. **IntegrityErrorの制約名検査**: `exc.orig` の `sqlstate == "23505"`(unique_violation)と `constraint_name == "uq_users_auth_provider_subject"` の2点検査(asyncpg例外の属性経由)。一致しないIntegrityErrorは再送出しUserServiceが503へ包む。分類は純粋関数 `classify_integrity_error` として切り出し、unitで偽装例外により検証(design §2.2「計画書・TDDで確定」の回答)
2. **永続化Callableのシグネチャ**: `CreateUserFn = (NewUser) -> Awaitable[uuid.UUID]`・`FetchByAuthFn = (provider, subject) -> Awaitable[UserRow | None]`。design §2.5の `(engine, NewUser)` 表記からengineを外し、**engineは `make_user_service` のクロージャで束縛**(design §3.1要旨どおり。UserServiceはengineを知らない)
3. **profileのjsonb渡し**: INSERTは `json.dumps(profile)` をパラメータに渡しSQLで `CAST(:profile AS jsonb)`(text()経由ではSQLAlchemyの型変換が入らないため)。SELECT側は `isinstance(profile, str)` なら `json.loads` で正規化
4. **UUID正規化 `_coerce_user_id` をusers側にも定義**(asyncpgはuuid列をUUIDインスタンスで返す。auth/service.py と同じ対応だがcoreへの共有化はせずauth不変を優先 — design §2.6)
5. **profileの正規化(bio=None→`{}`)はroutes層で行う**(サービスはdictを素通し。design §2.7)
6. **lifespanのスキップ判定はサービスごとに独立**(auth_service注入済み→auth構築スキップ、users_service注入済み→users構築スキップ。両方注入済みならredis/engineも構築しない。片方のみ注入の場合はredis/engineを構築し未注入側だけ構築する)
7. **ロガーは `latch.users`**(成功 `users.register ok` / `users.me ok`・ハンドラは `users.error code=<CODE>`。main.py は `users_logger` を追加。subject・claim・display_nameは出さない)
8. **me不在時は404 code "NOT_FOUND"**(design §2.4の解釈確定。`UserNotFoundError` — 05 §5の全API共通code値)

## 実行後のセルフレビュー(実装者がTask 5のStep 3に入る前に一度だけ読む)

- design.md §5の6項目がすべて§6(完了条件)に検証コマンドつきで対応しているか(項目2・6の実行主体はスーパーバイザー)
- design.md §4.1の5項目がすべて何れかのタスクの試験に対応しているか(1=test_age・2・3=test_service・4=test_routes・5=arch test)
- design.md §4.2の7項目がすべてintegration試験に対応しているか(1〜6=test_users_api.pyの各試験・7=try/finallyのDELETE)
- `age_years(birth_date, today)`・`classify_integrity_error(exc)`・`UserService(*, clock, create_user, fetch_by_auth)` の2メソッド・`make_user_service(*, clock, engine)`・`users_router`・`get_users_service(request)`・`create_app(..., users_service=None)` の各名前・引数がタスク間・テスト間で一致しているか
- 製品コードに `datetime.now` / `date.today` / `from time import` が入っていないか(arch testが自動検出するが、入れた瞬間にレッドになることを自覚しておく)
- `make test-ci` / `docker compose` / `make migrate` を実行していないか(§0)
- `backend/alembic/`・`auth/`・`core/`・`llm/`・`geo/`・`worker/`・`settings.py`・`pyproject.toml`・`compose.yaml`・`Makefile`・既存テスト・`tests/conftest.py`・`integration/conftest.py` が無変更か
- main.pyのauth関連行(既存include・AuthErrorハンドラ・`/health`)を消していないか(§2・ws-2との両側追記保持の前提)
- 実装中にdesign.md §6の6論点または§8の確定事項を変えた箇所があれば報告ファイルに書いたか
