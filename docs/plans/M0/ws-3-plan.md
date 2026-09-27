# M0 ws-3(認証)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 依存ハード制約C3を解消する — 認証3エンドポイント(POST /v1/auth/token / refresh / logout)、独自JWT(HS256・1時間)の発行・検証、Redis失効リスト+回転式リフレッシュトークン(30日・族失効つき)、IdPトークンのJWKS検証(RS256・同梱/URL両方式)、テスト用認証構成(同梱テスト鍵ペア+`python -m latch.auth` 発行ツール)をM0で作る。G0文言「認証3エンドポイントとJWKS検証経路がci環境でグリーン」の実装物。

**Architecture:** `latch.auth` パッケージに層を分けて置く — `tokens.py`(アクセスJWT発行/検証・HS256ピン・expはClock手動比較)、`idp.py`(IdPトークン検証・RS256+JWKS kid照合・同梱JWKSと `PyJWKClient` URLの2方式)、`sessions.py`(Redis 4鍵型: 失効リスト/リフレッシュ(SHA-256ハッシュ格納+GETDELアトミック消費)/族索引/消費済みマーカー)、`service.py`(3ユースケース+users読み取り生SQL1本+prodガードつき工場)、`routes.py`(public 2エンドポイント+logout保護ルータ)+`deps.py`(`require_authenticated` — M1以降の全ドメインルータが付すC3の強制点)。APIプロセス統合は `create_app` へのlifespan(redis・engine・AuthService構築)で行い、テスト注入用に第3引数 `auth_service` を設ける(ASGITransport試験ではlifespan非実行のため既存 `/health` 試験は無影響)。

**Tech Stack:** 既存(Python 3.13 / FastAPI / pydantic-settings / SQLAlchemy[asyncio])+ 追加 **`pyjwt[crypto]>=2.10`**(JWT・`PyJWKClient`)・**`redis>=5.2`**(redis.asyncio)/ devに **`fakeredis>=2.26`**(unit試験をプロセス外Redisなしで決定的に)。

**Spec:** `docs/plans/M0/ws-3-design.md`(agent1設計メモ。本計画はこの文書の§3ファイル構成・§4テスト方針・§5完了条件を各タスクへ展開したもの。design.md §6の5論点は推奨で固定済み — supervisor確認により計画書へ落とし済み)

## 0. 前提と規律(実装着手前に必ず読む)

- **作業場所**: 割り当てられたgit worktree内のみ。ブランチは `m0-ws-3`。**mainへの直接コミット・pushは禁止**(マージはスーパーバイザーが行う)。worktreeは `superpowers:using-git-worktrees` に従って作成する。worktreeでは最初に `make setup`(`uv sync`)を実行して `.venv` を構築する。
- **【最重要・STATUS運用ルール】compose常設環境へ触れない**: ws-4(地物データ)が並走中であり、共有ci-db(compose常設・名前付きボリューム)の `alembic_version` とコンテナ群(latch-ci)を取り合う状態にある。よって本単位の実装中は **`make test-ci` / `make migrate` / `make up` / `docker compose …` を一切実行しない**(コンテナ再作成・DBスキーマ状態の変更は並走単位とスーパーバイザーの検証を壊す)。**開発は `make test`(unit)で完結させる** — unit試験はfakeredis+FakeClock+スタブで外部プロセス不要(design §4.1)。integration試験ファイルは作成するが**実行しない**(報告書に「test-ci=スーパーバイザー検証待ち」と記録してよい)。`make test` はintegrationファイルの収集(import)まで行うため、構文・importの正当性はunit実行で検証される。
- **マイグレーションを追加しない**(design §2.6): ws-3はusers表の読み取りのみ(生SQL 1本)。`backend/alembic/` は無変更。並走するws-4がマイグレーション0002を追加する単位であるため、`alembic/versions/` へのいかなる差分も出さない。
- **設計判断の固定値**: design.md §6の5論点(HS256・リフレッシュRedis保持・profile_complete導出・refresh/logoutの503・prod実URLは設定経路のみ)は固定済みとして本計画に落としてある。またdesign §3.1のIF案を本計画§8(末尾の「design IF案からの確定事項」)のとおり確定した(tokens.pyは同期関数・rt鍵はSET+JSON・AuthServiceはsecret直受取・depsはAccessTokenClaims返し・`AuthService.authenticate` 追加)。**これらを変更した場合は必ず報告ファイル(§7)に「変更前→変更後+理由」を記録する**。黙って変えない。
- **コミット規律**: 各タスク末尾のコミットを必ず行う。コミットメッセージはConvention Commits形式(`feat:` `test:` `docs:` 等)。
- **毎コミットの検査**: `make lint` と `make test` がグリーンであることを確認してからコミットする(12 第8節 運用ルール5)。`make lint` は `ruff format --check` を含む — 整形差分を指摘されたら `cd backend && uv run ruff format .` を当ててから再実行する。
- **`python` / `pip` コマンドを直接使わない**: すべて `uv run` / `uv sync` 経由(システムPython 3.14と衝突させないため)。
- **詰まったら**: 10分以上詰まった場合、推測で仕様を変えず、報告ファイルに状況を書いて作業を停止する。

## 1. 参照仕様節(本計画の根拠。矛盾した場合はdesign.mdが本計画より優先)

| テーマ | 出典 |
|---|---|
| 認証は `Authorization: Bearer <JWT>`。認証不要エンドポイントは POST /v1/auth/token と POST /v1/auth/refresh の2つのみ。JWT claimに `auth_provider`/`auth_subject` | 05 第5節冒頭 |
| POST /v1/auth/token: `{provider, idp_token}` → 200 `{access_token, token_type:"Bearer", expires_in:3600, refresh_token, user:{id, profile_complete}}`。User紐付けなくても200。errors: 401 INVALID_IDP_TOKEN・429・503 DEPENDENCY_UNAVAILABLE(JWKS取得失敗等) | 05 第5節 認証・ユーザー系 |
| POST /v1/auth/refresh: 回転式・応答時に旧トークン無効化。無効化済み再提示=盗難疑いで族全失効+401 INVALID_REFRESH_TOKEN | 05 第5節 |
| POST /v1/auth/logout: 204。JWTをRedis失効リストへ+族無効化。以後同JWTは401 UNAUTHENTICATED | 05 第5節 |
| D-21: IdP=Google/Apple・アプリがIdP認証・APIはJWKSで検証しauth_provider+auth_subjectでUser紐付け。独自JWT 1時間・リフレッシュ30日回転式。ブロック即時反映はRedis失効リスト | 04 第4節 D-21 |
| Redisはセッション失効リスト等4用途を1で賄う | 04 第3節 |
| エラー形式 `{"error":{code, message, details}}`。401 UNAUTHENTICATED(失効リスト掲載含む)・401 INVALID_IDP_TOKEN・401 INVALID_REFRESH_TOKEN・503 DEPENDENCY_UNAVAILABLE(DB・Redis等・全API) | 05 第5節 エラー形式 |
| テスト用認証構成: 本番と同じJWT検証経路(JWKS参照)+staging専用鍵ペア注入+テストユーザーJWT発行内部ツール。実IdPに依存しない | 10 第1節 |
| M0スコープ4(認証3エンドポイント・Redis失効リスト・JWKS検証)・スコープ7(テスト用認証構成)・G0「認証3エンドポイントとJWKS検証経路がci環境でグリーン」 | 12 第3節 M0 |
| 時刻参照はすべてClock経由(arch test `test_arch_no_direct_time` が backend/src 全体を強制) | 04 第5節 FR-41・10 第1節 |
| users表: UNIQUE(auth_provider, auth_subject)・`profile_complete` 列は存在しない(行の存在から導出) | 05 第2節・design §1.3 |
| レート制限(429)の実装はM1。本単位はerror code列挙に含めない | 12 第3節 M1-6・design §1.2-13 |
| users API(POST /v1/users・GET /v1/users/me)はM1。ws-3はusers表読み取りのみ | 12 第3節 M1-1・design §1.3 |
| ログは許可リスト思想 — トークン文字列・claim内容・subjectを出さない | 08 第2.4節・design §2.7 |
| 完了条件7項目・テスト方針・ファイル構成 | design.md §3〜§5 |

## 2. グローバル制約(全タスクに暗黙に適用)

- 依存追加は **実行 `pyjwt[crypto]>=2.10`・`redis>=5.2`、dev `fakeredis>=2.26` の3本のみ**。それ以外の依存を追加しない
- アクセストークン(独自JWT)は **HS256固定**(発行・検索とも `algorithms=["HS256"]` ピン)。claimは `iss="latch-api"`・`aud="latch-app"`・`sub="{provider}:{subject}"`・`auth_provider`・`auth_subject`・`jti`(uuid4)・`sid`・`iat`/`exp`(Clock由来)。TTL **3600秒**
- リフレッシュトークンは **不透明トークン**(`secrets.token_urlsafe(32)`)・**RedisにはSHA-256ハッシュで格納**(生値をRedisに置かない)・TTL **30日(2592000秒)**・回転式
- Redis鍵は接頭辞 `auth:` の4鍵型: `auth:revoked:{jti}` / `auth:rt:{sha256}` / `auth:family:{fid}` / `auth:used:{sha256}`(design §2.3)
- **exp・iatの判定はClock手動比較**(PyJWTへは `options={"verify_exp": False}` を渡す。`datetime.now` 等の直接参照はarch test違反)
- 製品コード(`backend/src/latch/`)で `datetime.now` / `datetime.utcnow` / `date.today` / `time.time` / `time.monotonic` / `time.sleep` / `from time import` の使用禁止(例外は `core/clock.py` のみ。`tools.py` も `backend/src` 内 therefore 対象 — 発行時刻は `SystemClock` 経由+`--iat` 上書き)
- エラー応答は共通envelope `{"error": {"code": str, "message": str, "details": None}}`。JSON形式不正=400 MALFORMED_REQUEST・必須欠落/値域外=422 VALIDATION_ERROR(design §2.7)
- `backend/tests/conftest.py` は変更しない。auth用fixtureは各テストファイル内に定義する(ws-2と同じ判断)
- ログ(ロガー `latch.auth`)はイベント名と結果/コードのみ。トークン文字列・subject・claim値を出さない
- **`backend/alembic/`・`Makefile`・`docker/`・`.mise.toml`・`backend/tests/conftest.py`・`core/`・`worker/`・`llm/` は無変更**
- compose.yamlの変更は **apiサービスへの環境変数2件の追記のみ**(コンテナ再作成はスーパーバイザーが行うため、実行しない)

## 3. Review Focus(specが暗示するが、各タスクの試験だけでは拾いきれない入力クラス。所有タスクの試験でピン留めする)

1. **alg confusion(アルゴリズム混同攻撃)** — `alg:none` トークンやRS256署名トークンをHS256検証に通そうとすると認証バイパスになり得る → Task 3の「alg:none拒否」「RS256トークン拒否」試験が所有(`algorithms=["HS256"]` ピンの実効)
2. **回転済みリフレッシュの再利用検知と族失効の連鎖** — 旧トークン再提示時に族全体を失効させないと、盗難後の回転後新トークンが生き残る(05 第5節 確定値3の違反)→ Task 5の「再利用→族全失効」「回転後の新トークンも401」・Task 7のservice経由連鎖試験が所有
3. **logout済みJWTの失効リスト寿命** — revoked鍵のTTLが残り有効期限(exp−now)を超えると失効リストが無限に伸び、逆に短いと生存期間中に失効が消える → Task 5の「revoked鍵のTTL≤残り有効期限」試験が所有
4. **機微(トークン・subject)のログ混入** — 認証系ログにトークン文字列やIdP subjectが出ると08 第2.4節違反 → Task 9の「ログにトークン/subjectが出ない」試験が所有(caplog実測)
5. **prodでのテスト鍵誤用** — 同梱テスト鍵・空secret・テストissuer/audienceのままprod起動すると誰でもトークンを偽装できる → Task 8のprodガード4系統の拒否試験が所有

## 4. スコープ(作成・変更するファイル一覧)

作成(design.md §3.1どおり):

```text
backend/src/latch/auth/__init__.py        (Task 1でdocstringのみ作成 → Task 11で公開IFの再exportへ更新)
backend/src/latch/auth/__main__.py        (Task 10)
backend/src/latch/auth/errors.py          (Task 1)
backend/src/latch/auth/testkeys/__init__.py        (Task 1)
backend/src/latch/auth/testkeys/idp_test_private.pem   (Task 1で生成・コミット)
backend/src/latch/auth/testkeys/idp_test_jwks.json      (Task 1で生成・コミット)
backend/src/latch/auth/testkeys/README.md               (Task 1)
backend/src/latch/auth/tokens.py          (Task 3)
backend/src/latch/auth/idp.py             (Task 4)
backend/src/latch/auth/sessions.py        (Task 5)
backend/src/latch/auth/service.py         (Task 6〜8)
backend/src/latch/auth/routes.py          (Task 9)
backend/src/latch/auth/deps.py            (Task 9)
backend/src/latch/auth/tools.py           (Task 10)
backend/tests/unit/auth/test_idp.py              (Task 1でtestkeys分を作成 → Task 4で追記)
backend/tests/unit/auth/test_auth_settings.py    (Task 2。既存test_settings.pyとは別ファイル=並走ws-4との衝突回避。ws-2設計§3.3と同一判断)
backend/tests/unit/auth/test_tokens.py           (Task 3)
backend/tests/unit/auth/test_sessions.py         (Task 5)
backend/tests/unit/auth/test_service_token.py    (Task 6)
backend/tests/unit/auth/test_service_refresh.py  (Task 7)
backend/tests/unit/auth/test_prod_guard.py       (Task 8)
backend/tests/unit/auth/test_deps.py             (Task 9)
backend/tests/unit/auth/test_routes.py           (Task 9)
backend/tests/unit/auth/test_tools.py            (Task 10)
backend/tests/integration/test_auth_api.py       (Task 11。作成のみ・実行しない)
docs/plans/M0/ws-3-report.md              (報告ファイル。Task 11で作成)
```

変更(追記のみ):

- `backend/pyproject.toml` / `backend/uv.lock` — 依存3本(Task 1)
- `backend/src/latch/settings.py` — 認証設定8項目を追記(Task 2)。既存項目は変更しない
- `backend/src/latch/main.py` — lifespan・auth 2ルータinclude・エラーハンドラ・`create_app` 第3引数 `auth_service`(Task 9)。`/health` と既存引数は変更しない
- `compose.yaml` — apiサービスへ環境変数 `LATCH_DATABASE_URL`・`LATCH_REDIS_URL` 追記(Task 9)
- `backend/tests/integration/conftest.py` — redis・HTTP client fixtureを追記(Task 11。既存migrated_db/db_engineは温存)
- `backend/README.md` — 認証ツール使用方法・鍵管理の追記(Task 11)

生成されるがコミットしないもの: `backend/.venv/`・`__pycache__/`(gitignore済み)。**`backend/tests/unit/auth/` に `__init__.py`・`conftest.py` は作らない**(design §3.1の一覧にない。各テストファイル内に必要なfixtureを定義する)。

## 5. 禁止(触ってはいけないもの・スコープ外の判断基準)

- **compose常設環境の操作**: `make test-ci` / `make migrate` / `make up` / `make down` / `docker compose …` / `docker …`(§0のSTATUS運用ルール)
- `backend/alembic/` 全体(マイグレーション追加なし)、`backend/src/latch/core/`(clock.py・db.py・deps.py は再利用のみ・不改変)、`backend/src/latch/worker/`・`backend/src/latch/llm/`
- `backend/tests/conftest.py`・`backend/tests/unit/` の既存テストファイル(test_app_health・test_arch_no_direct_time・test_clock*・test_settings・test_worker)
- `backend/Makefile`(ルートのMakefileも)・`docker/`・`.mise.toml`・`.gitignore`・ルート `README.md`
- `docs/01〜12`・`docs/reviews/`・`docs/plans/STATUS.md`(スーパーバイザー管理)・`docs/plans/M0/` の他ファイル(ws-4系ファイル・過去のdesign/plan/report)
- `prototype/` 全体・`.claude/`
- スコープ外と判断する基準: users API(POST /v1/users・GET /v1/users/me・USER_EXISTS・UNDER_AGE)(M1)/ レート制限429の実装(M1・error列挙に入れない)/ 保護リソース側のドメインAPI(intents等・M1〜M3。`require_authenticated` を提供するのみ)/ 実IdPのJWKS URL・client_idの確定値運用(デプロイ設計M4/11。設定経路とprodガードのみ)/ 鍵ローテーション運用サイクル設計(M4/11)/ Worker側のRedis・DB消費(M2)/ アクセストークンRS256化・自己JWKS公開・introspection(MVP対象外・design §2.9)/ SQLAlchemyモデル・リポジトリ層(M1)/ SessionStore抽象IF・失効リスト掃除ジョブ(YAGNI)。これらが必要になったと感じても作らない — design §1.4に列挙された後続単位のスコープ
- mainブランチへのコミット・push・マージ

## 6. 完了条件(design.md §5の7項目。検証コマンドつき。Task 11で全て実行し報告ファイルに証拠を残す)

1. **`make lint`・`make test` がグリーン(unit/auth 追加分を含む)**
   検証: `make lint && make test` — ともにexit 0
2. **`make test-ci` で §4.2-1〜7 がグリーン(G0文言の証拠)** — **ただしws-4並走中のため実装エージェントは実行しない**(§0)。integration試験ファイルの収集が `make test` でエラーなく通ること(構文・importの正当性)をもって代替検証とし、報告書に「**test-ci=スーパーバイザー検証待ち**」と記録する
   検証(実装側): `make test` の収集エラーなし + `cd backend && uv run pytest --collect-only tests/integration/test_auth_api.py -q` がexit 0
3. **実時間参照が `core/clock.py` のみ(auth/配下はヒットしない)**
   検証: `rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src` のヒットが `backend/src/latch/core/clock.py` の行のみ。かつ `cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v` がexit 0
4. **依存追加が `pyjwt[crypto]`・`redis`(devに `fakeredis`)のみ**
   検証: `git diff main -- backend/pyproject.toml` の差分が3行(実行2・dev1)のみ。`git diff --stat main -- backend/uv.lock` が依存解決の差分のみ(関係ない既存行の変更を含まない)
5. **alembic/versions に差分なし(マイグレーション追加なし)**
   検証: `git diff --stat main -- backend/alembic` — 出力なし
6. **触るファイルが §4 の一覧どおり(core/・worker/・llm/・Makefile・docs/prototype・docker/に差分なし)**
   検証: `git diff --name-only main | sort` が§4の一覧と完全一致。`git status --short` が空(未コミット変更なし)
7. **`uv run python -m latch.auth issue-idp-token --provider google --subject demo` の出力がIdPVerifier(同梱JWKS)で検証できる**(compose apiでの200交換はスーパーバイザー検証時。READMEに手順を記載)
   検証: `cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject demo` がJWT文字列を出力(exit 0)+ Task 10の `test_cli_issue_token_verifies` がPASS(CLI出力→IdPVerifier検証のラウンドトリップ)

## 7. 報告形式

**結果ファイル**: `docs/plans/M0/ws-3-report.md`(worktree内で作成・コミットする。スーパーバイザーがマージ時にmainへ持ち込む)。内容(この形式で書く):

```markdown
# M0 ws-3(認証) 実行報告

- ブランチ: m0-ws-3 / ベース: <git rev-parse --short main>
- 日付: <実行日>
- 実行者: agent3

## 完了条件の検証結果
| # | 条件 | 結果 | 証拠(コマンド出力の要点) |
|---|---|---|---|
| 1 | make lint / make test | PASS/FAIL | <出力末尾を貼る> |
| 2 | make test-ci §4.2-1〜7 | test-ci=スーパーバイザー検証待ち | <make test 収集結果+ --collect-only 出力> |
| 3 | 実時間参照がclock.pyのみ | PASS/FAIL | <rg出力 + arch test結果> |
| 4 | 依存追加3本のみ | PASS/FAIL | <git diff 出力> |
| 5 | alembic差分なし | PASS/FAIL | <git diff --stat 出力(空なら「空」)> |
| 6 | 触るファイルがスコープどおり | PASS/FAIL | <git diff --name-only出力> |
| 7 | CLI発行トークンが検証できる | PASS/FAIL | <CLI出力先頭10文字+ test_cli_issue_token_verifies結果> |

## 固定値の変更有無(design.md §6・本計画§8)
- アクセストークンHS256(design §6-1): 変更なし / 変更あり(<前→後+理由>)
- リフレッシュRedis保持(design §6-2): 変更なし / 変更あり(<前→後+理由>)
- profile_complete=行存在導出(design §6-3): 変更なし / 変更あり(<前→後+理由>)
- refresh/logoutの503(design §6-4): 変更なし / 変更あり(<前→後+理由>)
- prod実URLは設定経路のみ(design §6-5): 変更なし / 変更あり(<前→後+理由>)
- 本計画§8のIF確定事項(tokens同期関数・rt鍵SET+JSON・AuthService secret直受取・deps AccessTokenClaims返し・authenticate追加): 変更なし / 変更あり(<前→後+理由>)

## スーパーバイザー検証手順(test-ci実行時)
1. ws-4マージ後に main で `docker compose up -d --build api`(compose.yaml の環境変数追加と新コードを反映)
2. `make migrate`(必要に応じ。ws-4の0002がhead)
3. `make test-ci` — §4.2-1〜7(test_auth_api.py)を含む全体グリーンで完了条件2を検証
4. READMEのcurl例(`python -m latch.auth issue-idp-token …` → POST /v1/auth/token)で200交換を確認(完了条件7)

## コミット一覧
<git log --oneline main..HEAD の出力>

## 補足(詰まった点・判断した点があれば)
```

完了後の最終返信は報告ファイルのパスと完了条件7項目の結果一覧(項目2は「test-ci=スーパーバイザー検証待ち」)。

---

### Task 1: 依存追加 + authパッケージ土台 + テスト鍵ペア + errors.py / testkeys ローダ

**Files:**
- Modify: `backend/pyproject.toml`(依存3本)、`backend/uv.lock`(`uv sync` が更新)
- Create: `backend/src/latch/auth/__init__.py`(この時点ではdocstringのみ。再exportはTask 11)、`backend/src/latch/auth/errors.py`、`backend/src/latch/auth/testkeys/__init__.py`、`backend/src/latch/auth/testkeys/idp_test_private.pem`(生成)、`backend/src/latch/auth/testkeys/idp_test_jwks.json`(生成)、`backend/src/latch/auth/testkeys/README.md`
- Test: `backend/tests/unit/auth/test_idp.py`(この時点ではtestkeysとerrorsの試験のみ。Task 4でIdPVerifierの試験を追記)

**Interfaces:**
- Consumes: なし(最初のタスク)
- Produces: `latch.auth.errors.AuthError(Exception)`(属性 `http_status: int`・`code: str`)+ 4サブクラス — `InvalidIdpTokenError`(401/"INVALID_IDP_TOKEN")・`UnauthenticatedError`(401/"UNAUTHENTICATED")・`InvalidRefreshTokenError`(401/"INVALID_REFRESH_TOKEN")・`DependencyUnavailableError`(503/"DEPENDENCY_UNAVAILABLE")。`latch.auth.testkeys` の定数 `TEST_ACCESS_SECRET = "latch-ci-test-access-secret-0123456789abcdef"`・`TEST_ISSUER_PREFIX = "https://idp.ci.latch.test/"`・`TEST_AUDIENCE = "latch-test-app"`・`DEFAULT_KID = "test-idp-1"` と `load_private_key() -> RSAPrivateKey`・`load_jwks() -> dict`。以降全タスクが利用

- [ ] **Step 1: 依存を追加して同期**

`backend/pyproject.toml` の `[project]` dependencies と `[dependency-groups]` dev へ追記(既存行は変更しない):

```toml
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.32",
    "pydantic-settings>=2.6",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pyjwt[crypto]>=2.10",
    "redis>=5.2",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
    "ruff>=0.7",
    "fakeredis>=2.26",
]
```

```bash
cd backend && uv sync
```

Expected: exit 0(uv.lockが更新される)。`uv run python -c "import jwt, redis, fakeredis; print(jwt.__version__, redis.__version__)"` が版本を表示

- [ ] **Step 2: パッケージ土台とテスト鍵ペアを生成**

```bash
mkdir -p backend/src/latch/auth/testkeys backend/tests/unit/auth
printf '"""認証(M0 ws-3)。token/refresh/logout・Redis失効リスト・JWKS検証・テスト用認証構成。"""\n' > backend/src/latch/auth/__init__.py
cd backend && uv run python - <<'EOF'
import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
out = Path("src/latch/auth/testkeys")
out.joinpath("idp_test_private.pem").write_bytes(
    key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
)


def b64u(value: int) -> str:
    size = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(size, "big")).rstrip(b"=").decode()


numbers = key.public_key().public_numbers()
jwk = {
    "kty": "RSA",
    "kid": "test-idp-1",
    "use": "sig",
    "alg": "RS256",
    "n": b64u(numbers.n),
    "e": b64u(numbers.e),
}
out.joinpath("idp_test_jwks.json").write_text(json.dumps({"keys": [jwk]}, indent=2) + "\n")
print("generated:", sorted(p.name for p in out.iterdir()))
EOF
```

Expected: `generated: ['idp_test_jwks.json', 'idp_test_private.pem']`

`backend/src/latch/auth/testkeys/README.md` を作成:

```markdown
# テスト用IdP鍵ペア(ci/staging)

- `idp_test_private.pem` / `idp_test_jwks.json`(kid=test-idp-1)は**ci・試験専用**のRS256鍵ペア。
  テスト専用で公開を前提とした値であり、本番(prod)では使用できない(`build_auth_service` の起動ガードが拒否)
- テストユーザーのIdPトークン発行: `uv run python -m latch.auth issue-idp-token --provider google --subject <sub>`
- 再生成: `uv run python -m latch.auth gen-keypair --out-dir <dir>`(10 第1節のテスト用認証構成。staging専用ペアの注入にも使用)
```

- [ ] **Step 3: 失敗するテストを書く**

`backend/tests/unit/auth/test_idp.py`:

```python
"""テスト用IdP鍵ペアと例外階層(design §2.5・§3.1)。

IdPVerifier本体の試験はTask 4でこのファイルへ追記する。
"""

import base64

from cryptography.hazmat.primitives.asymmetric import rsa

from latch.auth.errors import (
    DependencyUnavailableError,
    InvalidIdpTokenError,
    InvalidRefreshTokenError,
    UnauthenticatedError,
)
from latch.auth.testkeys import (
    DEFAULT_KID,
    TEST_ACCESS_SECRET,
    TEST_AUDIENCE,
    TEST_ISSUER_PREFIX,
    load_jwks,
    load_private_key,
)


def _b64u_int(value: str) -> int:
    padding = "=" * (-len(value) % 4)
    return int.from_bytes(base64.urlsafe_b64decode(value + padding), "big")


def test_error_statuses_and_codes():
    # 05 第5節 エラー形式の対応表。ハンドラはこれを信じてstatus/codeを出す
    assert (InvalidIdpTokenError("x").http_status, InvalidIdpTokenError("x").code) == (
        401,
        "INVALID_IDP_TOKEN",
    )
    assert (UnauthenticatedError("x").http_status, UnauthenticatedError("x").code) == (
        401,
        "UNAUTHENTICATED",
    )
    assert (
        InvalidRefreshTokenError("x").http_status,
        InvalidRefreshTokenError("x").code,
    ) == (401, "INVALID_REFRESH_TOKEN")
    assert (
        DependencyUnavailableError("x").http_status,
        DependencyUnavailableError("x").code,
    ) == (503, "DEPENDENCY_UNAVAILABLE")


def test_test_key_constants():
    # design §2.5: ci既定値(prodガードがこの残存を検出する)
    assert DEFAULT_KID == "test-idp-1"
    assert TEST_ISSUER_PREFIX == "https://idp.ci.latch.test/"
    assert TEST_AUDIENCE == "latch-test-app"
    assert len(TEST_ACCESS_SECRET) >= 32  # RFC 7518 HS256推奨鍵長


def test_jwks_shape_matches_default_kid():
    jwks = load_jwks()
    (jwk,) = jwks["keys"]
    assert jwk["kid"] == DEFAULT_KID
    assert jwk["kty"] == "RSA"
    assert jwk["alg"] == "RS256"
    assert jwk["use"] == "sig"


def test_private_key_is_rsa_and_matches_jwks():
    # 秘密鍵とJWKS公開値の整合(検証経路が成立する前提)
    priv = load_private_key()
    assert isinstance(priv, rsa.RSAPrivateKey)
    assert priv.key_size == 2048
    numbers = priv.public_key().public_numbers()
    jwk = load_jwks()["keys"][0]
    assert _b64u_int(jwk["n"]) == numbers.n
    assert _b64u_int(jwk["e"]) == numbers.e
```

- [ ] **Step 4: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_idp.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.auth.errors'`)

- [ ] **Step 5: 最小実装**

`backend/src/latch/auth/errors.py`:

```python
"""認証例外階層(design §3.1)。

各例外は http_status と code(05 第5節のerror code)を固定で持つ。
routes のハンドラはこれを共通envelopeへ変換する。例外メッセージには
トークン文字列・claim内容・subjectを含めない(08 第2.4節)。
"""

from __future__ import annotations


class AuthError(Exception):
    """認証系エラーの基底。http_status/code を持つ(ハンドラが消費する)。"""

    http_status: int
    code: str


class InvalidIdpTokenError(AuthError):
    """JWKS署名検証失敗・iss/aud不一致・期限切れ等(05 第5節)。"""

    http_status = 401
    code = "INVALID_IDP_TOKEN"


class UnauthenticatedError(AuthError):
    """API発行JWTの無効・期限切れ・失効リスト掲載(05 第5節)。"""

    http_status = 401
    code = "UNAUTHENTICATED"


class InvalidRefreshTokenError(AuthError):
    """リフレッシュトークン不在・期限切れ・回転済み再提示(05 第5節)。"""

    http_status = 401
    code = "INVALID_REFRESH_TOKEN"


class DependencyUnavailableError(AuthError):
    """DB・Redis・JWKS取得等の依存障害(05 第5節。全API)。"""

    http_status = 503
    code = "DEPENDENCY_UNAVAILABLE"
```

`backend/src/latch/auth/testkeys/__init__.py`:

```python
"""ci/試験用のIdP鍵ペアとテスト既定値のローダ(design §2.5)。

鍵ファイルはテスト専用でコミットされている。prodでの使用は
build_auth_service の起動ガードが拒否する(README.md参照)。
"""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import RSAPrivateKey

_DIR = Path(__file__).resolve().parent

DEFAULT_KID = "test-idp-1"
TEST_ACCESS_SECRET = "latch-ci-test-access-secret-0123456789abcdef"
TEST_ISSUER_PREFIX = "https://idp.ci.latch.test/"
TEST_AUDIENCE = "latch-test-app"


def load_private_key() -> RSAPrivateKey:
    """同梱のci/試験用RS256秘密鍵を返す(テストユーザーJWT発行の署名鍵)。"""
    key = serialization.load_pem_private_key(
        (_DIR / "idp_test_private.pem").read_bytes(), password=None
    )
    assert isinstance(key, RSAPrivateKey)
    return key


def load_jwks() -> dict:
    """同梱JWKS(kid=DEFAULT_KID)をdictで返す(検証側の静的鍵ソース)。"""
    return json.loads((_DIR / "idp_test_jwks.json").read_text(encoding="utf-8"))
```

- [ ] **Step 6: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_idp.py -v`
Expected: PASS(4件)

- [ ] **Step 7: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/pyproject.toml backend/uv.lock backend/src/latch/auth backend/tests/unit/auth
git commit -m "feat: 認証依存(pyjwt/redis/fakeredis)とテスト鍵ペア・例外階層"
```

---

### Task 2: 認証設定8項目(Settings追記)

**Files:**
- Modify: `backend/src/latch/settings.py`(認証設定8項目を追記。既存項目は変更しない)
- Test: `backend/tests/unit/auth/test_auth_settings.py`

**Interfaces:**
- Consumes: Task 1の `latch.auth.testkeys` 定数(試験が既定値照合に使用)
- Produces: `Settings` の新フィールド8件 — `redis_url: str = "redis://127.0.0.1:6379/0"`・`auth_access_secret: str = ""`(空=同梱テスト鍵)・`auth_idp_jwks_url_google: str = ""`・`auth_idp_jwks_url_apple: str = ""`(空=同梱テストJWKS)・`auth_idp_issuer_google: str = "https://idp.ci.latch.test/google"`・`auth_idp_issuer_apple: str = "https://idp.ci.latch.test/apple"`・`auth_idp_audience_google: str = "latch-test-app"`・`auth_idp_audience_apple: str = "latch-test-app"`(env prefix `LATCH_`)。Task 8の `build_auth_service` とTask 10のCLIが利用

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/auth/test_auth_settings.py`:

```python
"""認証設定8項目の既定値と環境変数上書き(design §2.5-2)。

既存tests/unit/test_settings.pyとは別ファイル(並走ws-4との共通ファイル衝突回避。
ws-2設計§3.3と同一判断)。
"""

from latch.auth.testkeys import TEST_ACCESS_SECRET, TEST_AUDIENCE, TEST_ISSUER_PREFIX
from latch.settings import Settings

AUTH_ENV_VARS = (
    "LATCH_REDIS_URL",
    "LATCH_AUTH_ACCESS_SECRET",
    "LATCH_AUTH_IDP_JWKS_URL_GOOGLE",
    "LATCH_AUTH_IDP_JWKS_URL_APPLE",
    "LATCH_AUTH_IDP_ISSUER_GOOGLE",
    "LATCH_AUTH_IDP_ISSUER_APPLE",
    "LATCH_AUTH_IDP_AUDIENCE_GOOGLE",
    "LATCH_AUTH_IDP_AUDIENCE_APPLE",
)


def _clean_settings(monkeypatch, **overrides) -> Settings:
    for var in AUTH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return Settings(**overrides)


def test_auth_settings_defaults(monkeypatch):
    s = _clean_settings(monkeypatch)
    assert s.redis_url == "redis://127.0.0.1:6379/0"
    assert s.auth_access_secret == ""  # 空=同梱テスト鍵(prod拒否対象)
    assert s.auth_idp_jwks_url_google == ""
    assert s.auth_idp_jwks_url_apple == ""
    assert s.auth_idp_issuer_google == f"{TEST_ISSUER_PREFIX}google"
    assert s.auth_idp_issuer_apple == f"{TEST_ISSUER_PREFIX}apple"
    assert s.auth_idp_audience_google == TEST_AUDIENCE
    assert s.auth_idp_audience_apple == TEST_AUDIENCE


def test_auth_settings_env_override(monkeypatch):
    monkeypatch.setenv("LATCH_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("LATCH_AUTH_ACCESS_SECRET", "prod-secret-0123456789abcdef012345")
    monkeypatch.setenv("LATCH_AUTH_IDP_JWKS_URL_GOOGLE", "https://example.com/jwks")
    for var in AUTH_ENV_VARS:
        if var not in (
            "LATCH_REDIS_URL",
            "LATCH_AUTH_ACCESS_SECRET",
            "LATCH_AUTH_IDP_JWKS_URL_GOOGLE",
        ):
            monkeypatch.delenv(var, raising=False)
    s = Settings()
    assert s.redis_url == "redis://redis:6379/0"
    assert s.auth_access_secret == "prod-secret-0123456789abcdef012345"
    assert s.auth_idp_jwks_url_google == "https://example.com/jwks"


def test_auth_settings_are_exactly_eight_fields(monkeypatch):
    # 設定の過剰供給を防ぐ機械検査(design §2.5-2の一覧どおり)
    _clean_settings(monkeypatch)
    auth_fields = {f for f in Settings.model_fields if f.startswith("auth_")}
    redis_fields = {f for f in Settings.model_fields if f.startswith("redis_")}
    assert auth_fields == {
        "auth_access_secret",
        "auth_idp_jwks_url_google",
        "auth_idp_jwks_url_apple",
        "auth_idp_issuer_google",
        "auth_idp_issuer_apple",
        "auth_idp_audience_google",
        "auth_idp_audience_apple",
    }
    assert redis_fields == {"redis_url"}


def test_defaults_never_equal_test_secret(monkeypatch):
    # 既定(空)がTEST_ACCESS_SECRETそのものにならない(空文字での判別を維持)
    s = _clean_settings(monkeypatch)
    assert s.auth_access_secret != TEST_ACCESS_SECRET
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_auth_settings.py -v`
Expected: FAIL(`pydantic.errors.ValidationError` または `AttributeError: 'Settings' object has no attribute 'redis_url'`)

- [ ] **Step 3: settings.py へ認証設定8項目を追記(追記のみ)**

`backend/src/latch/settings.py` — LLM設定ブロックの後に追記(既存の app_env・log_level・database_url・llm_* 行は変更しない):

```python
    # --- 認証(ws-3。design §2.5)---
    redis_url: str = "redis://127.0.0.1:6379/0"
    # HS256用secret。空=同梱テスト鍵(ci/staging)。prodで空/テスト鍵は起動拒否
    auth_access_secret: str = ""
    # IdP JWKS URL。空=同梱テストJWKS(ci/staging)。prodは実URL必須
    auth_idp_jwks_url_google: str = ""
    auth_idp_jwks_url_apple: str = ""
    # IdP検証のissuer/audience。既定=テストIdP値。prodは実IdPの値必須
    auth_idp_issuer_google: str = "https://idp.ci.latch.test/google"
    auth_idp_issuer_apple: str = "https://idp.ci.latch.test/apple"
    auth_idp_audience_google: str = "latch-test-app"
    auth_idp_audience_apple: str = "latch-test-app"
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_auth_settings.py -v`
Expected: PASS(4件)

- [ ] **Step 5: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/settings.py backend/tests/unit/auth/test_auth_settings.py
git commit -m "feat: 認証設定8項目(redis_url・アクセスsecret・IdP検証設定)"
```

---

### Task 3: tokens.py — アクセスJWT発行/検証(HS256・Clock手動期限判定)

**Files:**
- Create: `backend/src/latch/auth/tokens.py`
- Test: `backend/tests/unit/auth/test_tokens.py`

**Interfaces:**
- Consumes: `latch.core.clock.Clock` / `FakeClock`、Task 1の `UnauthenticatedError`、`latch.auth.testkeys.load_private_key`(RS256攻撃トークン作成は試験側)
- Produces: `ACCESS_TTL_S = 3600`・`REFRESH_TTL_S = 30 * 24 * 3600`(04 D-21)・`AccessTokenClaims`(frozen dataclass: `auth_subject: str`・`auth_provider: str`・`jti: str`・`sid: str`・`iat: datetime`・`exp: datetime`)・`issue_access_token(*, clock: Clock, secret: str, provider: str, subject: str, sid: str) -> str`(同期)・`verify_access_token(*, clock: Clock, secret: str, token: str) -> AccessTokenClaims`(同期・失敗は `UnauthenticatedError`・**失効リスト照会はしない**)。Task 5〜10が利用

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/auth/test_tokens.py`:

```python
"""アクセスJWT: 発行/検証・claim構成・期限(FakeClock)・algピン(design §2.2・§2.8・§4.1-1)。"""

import base64
import json
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest

from latch.auth.errors import UnauthenticatedError
from latch.auth.testkeys import load_private_key
from latch.auth.tokens import (
    ACCESS_TTL_S,
    REFRESH_TTL_S,
    issue_access_token,
    verify_access_token,
)
from latch.core.clock import FakeClock

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
SECRET = "unit-test-access-secret-0123456789abcdef"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _issue(clock: FakeClock, *, provider="google", subject="sub-1", sid="sid-1") -> str:
    return issue_access_token(
        clock=clock, secret=SECRET, provider=provider, subject=subject, sid=sid
    )


def test_ttl_constants_match_d21():
    assert ACCESS_TTL_S == 3600  # 04 D-21: 独自JWT 有効期限1時間
    assert REFRESH_TTL_S == 30 * 24 * 3600  # 04 D-21: リフレッシュ30日


def test_roundtrip_returns_expected_claims(clock):
    token = _issue(clock)
    claims = verify_access_token(clock=clock, secret=SECRET, token=token)
    assert claims.auth_provider == "google"
    assert claims.auth_subject == "sub-1"
    assert claims.jti
    assert claims.sid == "sid-1"
    assert claims.iat == NOW
    assert claims.exp == NOW + timedelta(seconds=ACCESS_TTL_S)


def test_token_payload_structure(clock):
    # 05 第5節: auth_provider/auth_subject が必須claim。他はdesign §2.2の構成
    token = _issue(clock, provider="apple", subject="apple-sub", sid="f-1")
    payload = pyjwt.decode(token, SECRET, algorithms=["HS256"])
    assert payload["iss"] == "latch-api"
    assert payload["aud"] == "latch-app"
    assert payload["sub"] == "apple:apple-sub"
    assert payload["auth_provider"] == "apple"
    assert payload["auth_subject"] == "apple-sub"
    assert payload["sid"] == "f-1"
    assert set(payload) == {
        "iss", "aud", "sub", "auth_provider", "auth_subject", "jti", "sid", "iat", "exp",
    }
    # 発行ごとにjtiは一意(uuid4)
    other = pyjwt.decode(_issue(clock, provider="apple", subject="apple-sub", sid="f-1"), SECRET, algorithms=["HS256"])
    assert other["jti"] != payload["jti"]


def test_expired_after_ttl(clock):
    # design §2.8: expはClock手動比較(FakeClock.advanceで決定的に再現)
    token = _issue(clock)
    clock.advance(timedelta(seconds=ACCESS_TTL_S + 1))
    with pytest.raises(UnauthenticatedError):
        verify_access_token(clock=clock, secret=SECRET, token=token)


def test_valid_just_before_expiry(clock):
    token = _issue(clock)
    clock.advance(timedelta(seconds=ACCESS_TTL_S - 1))
    claims = verify_access_token(clock=clock, secret=SECRET, token=token)
    assert claims.auth_subject == "sub-1"


def test_tampered_token_rejected(clock):
    token = _issue(clock)
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(UnauthenticatedError):
        verify_access_token(clock=clock, secret=SECRET, token=tampered)


def test_wrong_secret_rejected(clock):
    token = _issue(clock)
    with pytest.raises(UnauthenticatedError):
        verify_access_token(clock=clock, secret="different-secret-0123456789abcdef", token=token)


def test_rejects_alg_none_token(clock):
    # Review Focus #1: alg:none攻撃(手組みの署名なしトークン)
    header = {"alg": "none", "typ": "JWT"}

    def b64u(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    payload = {
        "iss": "latch-api", "aud": "latch-app", "sub": "google:sub-1",
        "auth_provider": "google", "auth_subject": "sub-1", "jti": "x", "sid": "s",
        "iat": int(NOW.timestamp()), "exp": int((NOW + timedelta(hours=2)).timestamp()),
    }
    unsigned = f"{b64u(json.dumps(header).encode())}.{b64u(json.dumps(payload).encode())}."
    with pytest.raises(UnauthenticatedError):
        verify_access_token(clock=clock, secret=SECRET, token=unsigned)


def test_rejects_rs256_signed_token(clock):
    # Review Focus #1: テスト秘密鍵でRS256署名したトークンをHS256検証に通そうする混入
    header = {"alg": "RS256", "typ": "JWT", "kid": "test-idp-1"}
    unsigned = pyjwt.encode(
        {
            "iss": "latch-api", "aud": "latch-app", "sub": "google:sub-1",
            "auth_provider": "google", "auth_subject": "sub-1", "jti": "x", "sid": "s",
            "iat": int(NOW.timestamp()), "exp": int((NOW + timedelta(hours=2)).timestamp()),
        },
        load_private_key(),
        algorithm="RS256",
        headers=header,
    )
    with pytest.raises(UnauthenticatedError):
        verify_access_token(clock=clock, secret=SECRET, token=unsigned)


def test_garbage_token_rejected(clock):
    with pytest.raises(UnauthenticatedError):
        verify_access_token(clock=clock, secret=SECRET, token="not-a-jwt")
    with pytest.raises(UnauthenticatedError):
        verify_access_token(clock=clock, secret=SECRET, token="")
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_tokens.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.auth.tokens'`)

- [ ] **Step 3: tokens.py を実装**

`backend/src/latch/auth/tokens.py`:

```python
"""アクセスJWTの発行/検証(design §2.2・§2.8)。

HS256(対称鍵)にピン留め — 検証者=発行者=API Layerのみ(04 第2節)であり、
alg confusionの余地を構造的に塞ぐ。exp/iatはPyJWTに検証させず
Clock.now() との手動比較で行う(C2: 時刻参照の単一経路。FakeClockで
期限切れを決定的に再現できる)。失効リスト照会は行わない(service.authenticate の責務)。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt as pyjwt

from latch.auth.errors import UnauthenticatedError
from latch.core.clock import Clock

ACCESS_TTL_S = 3600  # 04 D-21: 独自JWT 有効期限1時間
REFRESH_TTL_S = 30 * 24 * 3600  # 04 D-21: リフレッシュトークン30日

_ISSUER = "latch-api"
_AUDIENCE = "latch-app"
_ALGORITHM = "HS256"


@dataclass(frozen=True)
class AccessTokenClaims:
    """検証済みアクセスJWTのclaim(require_authenticated の戻り型)。"""

    auth_provider: str
    auth_subject: str
    jti: str
    sid: str
    iat: datetime
    exp: datetime


def issue_access_token(
    *, clock: Clock, secret: str, provider: str, subject: str, sid: str
) -> str:
    """アクセスJWTを発行する(05 第5節: auth_provider/auth_subject を必須claimとして運ぶ)。"""
    now = clock.now()
    payload = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": f"{provider}:{subject}",
        "auth_provider": provider,
        "auth_subject": subject,
        "jti": str(uuid.uuid4()),
        "sid": sid,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ACCESS_TTL_S)).timestamp()),
    }
    return pyjwt.encode(payload, secret, algorithm=_ALGORITHM)


def verify_access_token(*, clock: Clock, secret: str, token: str) -> AccessTokenClaims:
    """署名・iss・aud・algを検証し、expをClock手動比較で判定する。

    失敗はすべて UnauthenticatedError(署名改ざん・alg混入・期限切れ等の
    区別を応答に漏らさない)。
    """
    try:
        payload = pyjwt.decode(
            token,
            secret,
            algorithms=[_ALGORITHM],
            issuer=_ISSUER,
            audience=_AUDIENCE,
            options={"verify_exp": False, "verify_iat": False},
        )
    except pyjwt.InvalidTokenError as exc:
        raise UnauthenticatedError("access token rejected") from exc
    exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
    if exp <= clock.now():
        raise UnauthenticatedError("access token expired")
    return AccessTokenClaims(
        auth_provider=payload["auth_provider"],
        auth_subject=payload["auth_subject"],
        jti=payload["jti"],
        sid=payload["sid"],
        iat=datetime.fromtimestamp(payload["iat"], tz=UTC),
        exp=exp,
    )
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_tokens.py -v`
Expected: PASS(10件)

- [ ] **Step 5: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/auth/tokens.py backend/tests/unit/auth/test_tokens.py
git commit -m "feat: アクセスJWT発行/検証(HS256ピン・Clock手動期限判定)"
```

---

### Task 4: idp.py — IdPトークンのJWKS検証(同梱/URL両方式)

**Files:**
- Create: `backend/src/latch/auth/idp.py`
- Test: `backend/tests/unit/auth/test_idp.py`(追記)

**Interfaces:**
- Consumes: Task 1の `InvalidIdpTokenError` / `DependencyUnavailableError` / testkeys(`load_jwks`・`DEFAULT_KID`・`load_private_key`)、`latch.core.clock.Clock`
- Produces: `IdPVerifyConfig`(frozen dataclass: `issuer: str`・`audience: str`・`jwks_url: str | None = None`。None=同梱テストJWKS)・`IdPVerifier(configs: dict[str, IdPVerifyConfig])` の `async def verify(*, provider: str, idp_token: str, clock: Clock) -> tuple[str, str]`(=(provider, subject)。失敗は `InvalidIdpTokenError` / `DependencyUnavailableError`)。Task 6のAuthServiceとTask 10のtools試験が利用

- [ ] **Step 1: 失敗するテストを書く(test_idp.py に追記)**

ファイル先頭のimport節へ追記:

```python
import json
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest

from latch.auth.errors import (
    DependencyUnavailableError,
    InvalidIdpTokenError,
)
from latch.auth.idp import IdPVerifyConfig, IdPVerifier
from latch.core.clock import FakeClock
```

(`from latch.auth.testkeys import ...` は既存行に `load_private_key` が既に含まれる。`DEFAULT_KID` も既にimport済み)

ファイル末尾へ追記:

```python
NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
ISSUER_G = "https://idp.ci.latch.test/google"
AUDIENCE = "latch-test-app"

_KEY = load_private_key()


def _clock() -> FakeClock:
    return FakeClock(NOW)


def _sign(
    *,
    subject="sub-1",
    issuer=ISSUER_G,
    audience=AUDIENCE,
    expires_in=3600,
    kid=DEFAULT_KID,
    extra=None,
) -> str:
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(seconds=expires_in)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return pyjwt.encode(payload, _KEY, algorithm="RS256", headers={"kid": kid})


def _verifier(jwks_url: str | None = None) -> IdPVerifier:
    return IdPVerifier(
        configs={"google": IdPVerifyConfig(issuer=ISSUER_G, audience=AUDIENCE, jwks_url=jwks_url)}
    )


async def test_verify_returns_provider_and_subject():
    provider, subject = await _verifier().verify(
        provider="google", idp_token=_sign(subject="user-abc"), clock=_clock()
    )
    assert (provider, subject) == ("google", "user-abc")


async def test_rejects_wrong_issuer():
    token = _sign(issuer="https://evil.example/")
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=token, clock=_clock())


async def test_rejects_wrong_audience():
    token = _sign(audience="someone-elses-app")
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=token, clock=_clock())


async def test_rejects_bad_signature():
    # 別鍵で署名(検証側は同梱JWKSのみ知る)
    from cryptography.hazmat.primitives.asymmetric import rsa

    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    payload = {
        "iss": ISSUER_G, "aud": AUDIENCE, "sub": "sub-1",
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(seconds=3600)).timestamp()),
    }
    token = pyjwt.encode(payload, other_key, algorithm="RS256", headers={"kid": DEFAULT_KID})
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=token, clock=_clock())


async def test_rejects_unknown_kid():
    token = _sign(kid="other-kid")
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=token, clock=_clock())


async def test_rejects_expired_token():
    clock = _clock()
    token = _sign(expires_in=600)
    clock.advance(timedelta(seconds=601))
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=token, clock=clock)


async def test_rejects_missing_or_empty_subject():
    token = _sign(subject="")
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=token, clock=_clock())
    payload = {
        "iss": ISSUER_G, "aud": AUDIENCE,
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(seconds=3600)).timestamp()),
    }
    no_sub = pyjwt.encode(payload, _KEY, algorithm="RS256", headers={"kid": DEFAULT_KID})
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=no_sub, clock=_clock())


async def test_url_mode_with_file_jwks(tmp_path):
    # URL方式(PyJWKClient)の結合試験 — file:// はurlopenが対応(design §2.4)
    jwks_path = tmp_path / "jwks.json"
    jwks_path.write_text(json.dumps(load_jwks()))
    verifier = IdPVerifier(
        configs={"google": IdPVerifyConfig(issuer=ISSUER_G, audience=AUDIENCE, jwks_url=jwks_path.as_uri())}
    )
    provider, subject = await verifier.verify(
        provider="google", idp_token=_sign(subject="url-mode"), clock=_clock()
    )
    assert subject == "url-mode"


async def test_url_mode_unreachable_is_dependency_unavailable(tmp_path):
    verifier = IdPVerifier(
        configs={
            "google": IdPVerifyConfig(
                issuer=ISSUER_G, audience=AUDIENCE, jwks_url="file:///nonexistent-ws3/jwks.json"
            )
        }
    )
    with pytest.raises(DependencyUnavailableError):
        await verifier.verify(provider="google", idp_token=_sign(), clock=_clock())


async def test_url_mode_kid_not_found_is_invalid(tmp_path):
    # JWKSは取得できるがkidが一致しない → 401(503ではない)
    jwks = load_jwks()
    jwks["keys"][0]["kid"] = "rotated-key"
    p = tmp_path / "jwks-rotated.json"
    p.write_text(json.dumps(jwks))
    verifier = IdPVerifier(
        configs={
            "google": IdPVerifyConfig(issuer=ISSUER_G, audience=AUDIENCE, jwks_url=p.as_uri())
        }
    )
    with pytest.raises(InvalidIdpTokenError):
        await verifier.verify(provider="google", idp_token=_sign(), clock=_clock())


async def test_unknown_provider_raises_value_error():
    # provider値はroutesのLiteral検証が守る(内部契約違反は素のValueError)
    with pytest.raises(ValueError):
        await _verifier().verify(provider="line", idp_token=_sign(), clock=_clock())
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_idp.py -v`
Expected: 既存4件はPASSのまま、新規11件がFAIL(`ModuleNotFoundError: No module named 'latch.auth.idp'`)

- [ ] **Step 3: idp.py を実装**

`backend/src/latch/auth/idp.py`:

```python
"""IdPトークン(Google/Apple)のJWKS検証(design §2.4)。

- 署名検証はRS256にピン留め(実IdPのJWKSはRS256公開鍵配布モデル)
- JWKSソースは2方式: jwks_url指定=PyJWKClient(URL取得・キャッシュ)=本番経路 /
  未指定=同梱テストJWKSから静的構築(ci/staging既定。「取得失敗」という状態が存在しない)
- expはClock手動比較(§2.8)、sub(=auth_subject)の非空を検証
- JWKS取得の接続失敗は503 DEPENDENCY_UNAVAILABLE(05 第5節)、kid不一致は401
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import jwt as pyjwt

from latch.auth.errors import (
    DependencyUnavailableError,
    InvalidIdpTokenError,
)
from latch.auth.testkeys import load_jwks
from latch.core.clock import Clock


@dataclass(frozen=True)
class IdPVerifyConfig:
    """providerごとの検証設定(issuer/audience/JWKSソース)。"""

    issuer: str
    audience: str
    jwks_url: str | None = None  # None=同梱テストJWKS


class IdPVerifier:
    """IdPトークンを検証し (provider, subject) を返す(D-21のJWKS検証経路)。"""

    def __init__(self, *, configs: dict[str, IdPVerifyConfig]) -> None:
        self._configs = configs
        # 同梱方式の静的鍵(kid→PyJWK)。URL方式はURLごとにPyJWKClientを遅延生成
        self._static_keys = {
            jwk["kid"]: pyjwt.PyJWK.from_dict(jwk, algorithm="RS256")
            for jwk in load_jwks()["keys"]
        }
        self._clients: dict[str, pyjwt.PyJWKClient] = {}

    async def verify(
        self, *, provider: str, idp_token: str, clock: Clock
    ) -> tuple[str, str]:
        cfg = self._configs.get(provider)
        if cfg is None:
            raise ValueError(f"unknown idp provider: {provider!r}")
        key = await self._resolve_key(cfg, idp_token)
        try:
            payload = pyjwt.decode(
                idp_token,
                key.key,
                algorithms=["RS256"],
                audience=cfg.audience,
                issuer=cfg.issuer,
                options={"verify_exp": False, "verify_iat": False},
            )
        except pyjwt.InvalidTokenError as exc:
            raise InvalidIdpTokenError("idp token rejected") from exc
        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        if exp <= clock.now():
            raise InvalidIdpTokenError("idp token expired")
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject:
            raise InvalidIdpTokenError("idp token subject missing")
        return provider, subject

    async def _resolve_key(
        self, cfg: IdPVerifyConfig, token: str
    ) -> pyjwt.PyJWK:
        if cfg.jwks_url:
            client = self._clients.get(cfg.jwks_url)
            if client is None:
                client = pyjwt.PyJWKClient(cfg.jwks_url, timeout=5.0)
                self._clients[cfg.jwks_url] = client
            try:
                # PyJWT>=2.10 のasync取得経路(urlopenをawaitで包む)
                return await client.fetch_signing_key_from_jwt(token)
            except pyjwt.PyJWKClientConnectionError as exc:
                raise DependencyUnavailableError("jwks fetch failed") from exc
            except pyjwt.PyJWKClientError as exc:
                raise InvalidIdpTokenError("signing key not found for kid") from exc
        header = pyjwt.get_unverified_header(token)
        key = self._static_keys.get(header.get("kid", ""))
        if key is None:
            raise InvalidIdpTokenError("signing key not found for kid")
        return key
```

(注: `fetch_signing_key_from_jwt` はPyJWT 2.10+のasync API。万一 `AttributeError` になる場合は `await asyncio.to_thread(client.get_signing_key_from_jwt, token)` に置き換えてよい — その場合報告ファイルの補足に記録する)

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_idp.py -v`
Expected: PASS(15件 — Task 1分4件+本タスク11件)

- [ ] **Step 5: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/auth/idp.py backend/tests/unit/auth/test_idp.py
git commit -m "feat: IdPトークンのJWKS検証(RS256・同梱/URL両方式・503分岐)"
```

---

### Task 5: sessions.py — RedisセッションStore(回転・族失効・失効リスト)

**Files:**
- Create: `backend/src/latch/auth/sessions.py`
- Test: `backend/tests/unit/auth/test_sessions.py`

**Interfaces:**
- Consumes: `redis.asyncio.Redis`(注入・`decode_responses=True` で構築されたもの)、Task 1の `InvalidRefreshTokenError`、Task 3の `REFRESH_TTL_S`
- Produces: `RefreshIssued`(frozen dataclass: `token: str`・`sid: str`)・`RotationResult`(frozen dataclass: `token: str`・`sid: str`・`provider: str`・`subject: str`)・`SessionStore(redis)` の5メソッド — `async create_refresh(*, provider: str, subject: str) -> RefreshIssued`・`async rotate_refresh(*, token: str, now: datetime) -> RotationResult`(失敗は `InvalidRefreshTokenError`)・`async revoke_access(*, jti: str, exp: datetime, now: datetime) -> None`・`async revoke_family(*, sid: str) -> None`・`async is_revoked(*, jti: str) -> bool`。Task 6以降とM1+の全認証経路が利用

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/auth/test_sessions.py`:

```python
"""RedisセッションStore: 回転・再利用検知→族失効・不在401・失効TTL(design §2.3・§4.1-2)。

fakeredis(redis-pyのコマンド解釈経路をそのまま実行)で決定的に検証する。
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import pytest

from latch.auth.errors import InvalidRefreshTokenError
from latch.auth.sessions import REFRESH_TTL_S, SessionStore
from latch.core.clock import FakeClock

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _store(redis) -> SessionStore:
    return SessionStore(redis)


async def test_create_refresh_stores_hashed_token_with_ttl(redis):
    store = _store(redis)
    issued = await store.create_refresh(provider="google", subject="sub-1")
    assert issued.token
    assert issued.sid
    # 生値をRedisに置かない: 鍵名はSHA-256ハッシュのみ
    keys = sorted(await redis.keys("auth:*"))
    assert issued.token not in "".join(keys)
    raw = await redis.get(f"auth:rt:{_sha(issued.token)}")
    data = json.loads(raw)
    assert data == {"provider": "google", "subject": "sub-1", "family": issued.sid}
    ttl = await redis.ttl(f"auth:rt:{_sha(issued.token)}")
    assert 0 < ttl <= REFRESH_TTL_S  # 30日(design §2.3)
    assert await redis.smembers(f"auth:family:{issued.sid}") == {_sha(issued.token)}


async def test_rotate_returns_new_token_and_keeps_family(redis, clock):
    store = _store(redis)
    issued = await store.create_refresh(provider="google", subject="sub-1")
    result = await store.rotate_refresh(token=issued.token, now=clock.now())
    assert result.token != issued.token
    assert result.sid == issued.sid  # 族は不変
    assert result.provider == "google"
    assert result.subject == "sub-1"
    # 旧トークンは消費済(GETDEL)・消費済みマーカーに族id・新トークン格納
    assert await redis.get(f"auth:rt:{_sha(issued.token)}") is None
    assert await redis.get(f"auth:used:{_sha(issued.token)}") == issued.sid
    assert await redis.get(f"auth:rt:{_sha(result.token)}") is not None
    assert await redis.smembers(f"auth:family:{issued.sid}") == {
        _sha(issued.token),
        _sha(result.token),
    }


async def test_reuse_of_consumed_token_revokes_whole_family(redis, clock):
    # Review Focus #2: 回転済みトークンの再提示=盗難疑い → 族全失効(05 第5節 確定値3)
    store = _store(redis)
    issued = await store.create_refresh(provider="google", subject="sub-1")
    rotated = await store.rotate_refresh(token=issued.token, now=clock.now())
    with pytest.raises(InvalidRefreshTokenError):
        await store.rotate_refresh(token=issued.token, now=clock.now())
    # 回転後の新トークンも同一族なので失効している
    with pytest.raises(InvalidRefreshTokenError):
        await store.rotate_refresh(token=rotated.token, now=clock.now())
    assert await redis.exists(f"auth:family:{issued.sid}") == 0
    assert await redis.get(f"auth:rt:{_sha(rotated.token)}") is None


async def test_unknown_token_rejected_without_family_revocation(redis, clock):
    # 不在・期限切れトークンから族を特定できない → 族失効しない(design §2.3)
    store = _store(redis)
    other = await store.create_refresh(provider="google", subject="sub-2")
    with pytest.raises(InvalidRefreshTokenError):
        await store.rotate_refresh(token="totally-unknown-token", now=clock.now())
    assert await redis.get(f"auth:rt:{_sha(other.token)}") is not None
    assert await redis.exists(f"auth:family:{other.sid}") == 1


async def test_revoke_access_ttl_is_remaining_lifetime(redis, clock):
    # Review Focus #3: 失効リストのTTLは残り有効期限(exp−now)以下 — 失効リストが無限に伸びない
    store = _store(redis)
    exp = NOW + timedelta(seconds=1200)
    await store.revoke_access(jti="jti-1", exp=exp, now=clock.now())
    ttl = await redis.ttl("auth:revoked:jti-1")
    assert 0 < ttl <= 1200
    assert await redis.get("auth:revoked:jti-1") == "1"


async def test_is_revoked(redis, clock):
    store = _store(redis)
    assert await store.is_revoked(jti="jti-x") is False
    await store.revoke_access(
        jti="jti-x", exp=NOW + timedelta(seconds=60), now=clock.now()
    )
    assert await store.is_revoked(jti="jti-x") is True


async def test_revoke_family_deletes_rt_keys_and_index(redis, clock):
    store = _store(redis)
    issued = await store.create_refresh(provider="google", subject="sub-1")
    rotated = await store.rotate_refresh(token=issued.token, now=clock.now())
    await store.revoke_family(sid=issued.sid)
    for token in (issued.token, rotated.token):
        assert await redis.get(f"auth:rt:{_sha(token)}") is None
    assert await redis.exists(f"auth:family:{issued.sid}") == 0
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_sessions.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.auth.sessions'`)

- [ ] **Step 3: sessions.py を実装**

`backend/src/latch/auth/sessions.py`:

```python
"""Redis上のセッション状態(design §2.3の4鍵型)。

鍵(接頭辞 auth: — Jevカウンタ等M1+と名前空間を分ける):
  失効リスト:   SET  auth:revoked:{jti}        "1"            PX <(exp−now)ms>
  リフレッシュ: SET  auth:rt:{sha256(token)}   <JSON>          PX 30d
  族索引:       SADD auth:family:{fid}          {sha256(token)} PX 30d
  消費済み:     SET  auth:used:{sha256(token)}  {fid}          PX 30d

リフレッシュは不透明トークン(secret.token_urlsafe(32))をRedisにはSHA-256
ハッシュで保持する(生値を置かない。ダンプ漏洩時の悪用防止)。
回転はGETDELでアトミック消費し、消費済みトークンの再提示は盗難疑いとして
族(当該IdPセッションのトークン族)全体を失効する(05 第5節。安全側)。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

import redis.asyncio as aioredis

from latch.auth.errors import InvalidRefreshTokenError
from latch.auth.tokens import REFRESH_TTL_S


def _sha256_hex(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class RefreshIssued:
    """create_refresh の結果(トークン本体と族id=sid)。"""

    token: str
    sid: str


@dataclass(frozen=True)
class RotationResult:
    """rotate_refresh の結果(新トークン・族id・紐付け情報)。"""

    token: str
    sid: str
    provider: str
    subject: str


class SessionStore:
    """Redis操作を閉じ込めるStore(decode_responses=True のRedisを注入)。"""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def create_refresh(self, *, provider: str, subject: str) -> RefreshIssued:
        """token発行時: 族idを生成し、rt鍵と族索引(初期メンバー)を書く。"""
        token = secrets.token_urlsafe(32)
        sid = uuid.uuid4().hex
        data = json.dumps({"provider": provider, "subject": subject, "family": sid})
        pipe = self._redis.pipeline()
        pipe.set(f"auth:rt:{_sha256_hex(token)}", data, ex=REFRESH_TTL_S)
        pipe.sadd(f"auth:family:{sid}", _sha256_hex(token))
        pipe.expire(f"auth:family:{sid}", REFRESH_TTL_S)
        await pipe.execute()
        return RefreshIssued(token=token, sid=sid)

    async def rotate_refresh(self, *, token: str, now: datetime) -> RotationResult:
        """refresh回転: GETDELでアトミック消費 → 新トークン発行+消費済みマーカー。

        消費済みトークンの再提示は盗難疑いとして族全失効のうえ401。
        不在・期限切れは族失効せず401(族を特定できないため。
        また期限切れは盗難と区別できないが対象外 — design §2.3)。
        """
        sha = _sha256_hex(token)
        raw = await self._redis.getdel(f"auth:rt:{sha}")
        if raw is None:
            used = await self._redis.get(f"auth:used:{sha}")
            if used is not None:
                await self._revoke_family(used)
                raise InvalidRefreshTokenError("refresh token reuse detected")
            raise InvalidRefreshTokenError("refresh token not found or expired")
        data = json.loads(raw)
        sid = data["family"]
        new_token = secrets.token_urlsafe(32)
        new_sha = _sha256_hex(new_token)
        pipe = self._redis.pipeline()
        pipe.set(f"auth:rt:{new_sha}", raw, ex=REFRESH_TTL_S)
        pipe.sadd(f"auth:family:{sid}", new_sha)
        pipe.expire(f"auth:family:{sid}", REFRESH_TTL_S)
        pipe.set(f"auth:used:{sha}", sid, ex=REFRESH_TTL_S)
        await pipe.execute()
        return RotationResult(
            token=new_token,
            sid=sid,
            provider=data["provider"],
            subject=data["subject"],
        )

    async def revoke_access(self, *, jti: str, exp: datetime, now: datetime) -> None:
        """logout: 失効リストへ登録(TTL=残り有効期限 — expで自動消滅)。"""
        remaining_ms = max(int((exp - now).total_seconds() * 1000), 1)
        await self._redis.set(f"auth:revoked:{jti}", "1", px=remaining_ms)

    async def revoke_family(self, *, sid: str) -> None:
        """logout・族失効: 族メンバーのrt鍵と族索引を消す(usedは残す=再検知の連鎖)。"""
        await self._revoke_family(sid)

    async def is_revoked(self, *, jti: str) -> bool:
        """認証Dependency: 失効リスト掲載の照会(05 第5節)。"""
        return bool(await self._redis.exists(f"auth:revoked:{jti}"))

    async def _revoke_family(self, sid: str) -> None:
        members = await self._redis.smembers(f"auth:family:{sid}")
        pipe = self._redis.pipeline()
        for sha in members:
            pipe.delete(f"auth:rt:{sha}")
        pipe.delete(f"auth:family:{sid}")
        await pipe.execute()
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_sessions.py -v`
Expected: PASS(7件)

- [ ] **Step 5: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/auth/sessions.py backend/tests/unit/auth/test_sessions.py
git commit -m "feat: RedisセッションStore(GETDEL回転・族失効・失効リストTTL)"
```

---

### Task 6: service.py(1/3)— tokenユースケース + users読み取り

**Files:**
- Create: `backend/src/latch/auth/service.py`(この時点で `make_user_lookup`・`TokenResult`・`AuthService.__init__`・`token` を実装)
- Test: `backend/tests/unit/auth/test_service_token.py`

**Interfaces:**
- Consumes: Task 3(tokens)・Task 4(IdPVerifier)・Task 5(SessionStore)・`latch.core.db` の `AsyncEngine`(make_user_lookupの引数型のみ・unit試験ではengineを使わない)
- Produces: `UserLookup = Callable[[str, str], Awaitable[uuid.UUID | None]]`・`make_user_lookup(engine: AsyncEngine) -> UserLookup`(生SQL 1本・行なければNone)・`TokenResult`(frozen dataclass: `access_token: str`・`token_type: str`・`expires_in: int`・`refresh_token: str`・`user_id: uuid.UUID | None`・`profile_complete: bool`)・`AuthService(*, clock: Clock, secret: str, sessions: SessionStore, idp: IdPVerifier, user_lookup: UserLookup)` と `async token(*, provider: str, idp_token: str) -> TokenResult`。Task 7でrefresh/logout/authenticateを追記、Task 8でbuild_auth_serviceを追記

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/auth/test_service_token.py`:

```python
"""tokenユースケース: user応答分岐・claim連鎖・IdP失敗・依存障害503(design §4.1-3)。"""

import uuid
from datetime import UTC, datetime

import fakeredis.aioredis
import jwt as pyjwt
import pytest

from latch.auth.errors import DependencyUnavailableError, InvalidIdpTokenError
from latch.auth.idp import IdPVerifyConfig, IdPVerifier
from latch.auth.service import AuthService
from latch.auth.sessions import SessionStore
from latch.auth.testkeys import DEFAULT_KID, load_private_key
from latch.auth.tokens import verify_access_token
from latch.core.clock import FakeClock
from latch.settings import Settings

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
SETTINGS = Settings(app_env="ci")
ISSUER_G = SETTINGS.auth_idp_issuer_google
AUDIENCE = SETTINGS.auth_idp_audience_google
SECRET = "unit-test-access-secret-0123456789abcdef"
USER_ID = uuid.uuid4()


async def _none_lookup(provider: str, subject: str) -> uuid.UUID | None:
    return None


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _idp() -> IdPVerifier:
    return IdPVerifier(
        configs={
            "google": IdPVerifyConfig(issuer=ISSUER_G, audience=AUDIENCE),
            "apple": IdPVerifyConfig(
                issuer=SETTINGS.auth_idp_issuer_apple, audience=SETTINGS.auth_idp_audience_apple
            ),
        }
    )


def _idp_token(subject: str = "sub-1", provider: str = "google") -> str:
    issuer = ISSUER_G if provider == "google" else SETTINGS.auth_idp_issuer_apple
    payload = {
        "iss": issuer,
        "aud": AUDIENCE,
        "sub": subject,
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 3600,
    }
    return pyjwt.encode(payload, load_private_key(), algorithm="RS256", headers={"kid": DEFAULT_KID})


def _service(clock, redis, *, lookup=None, idp=None) -> AuthService:
    return AuthService(
        clock=clock,
        secret=SECRET,
        sessions=SessionStore(redis),
        idp=idp if idp is not None else _idp(),
        user_lookup=lookup if lookup is not None else _none_lookup,
    )


async def test_token_without_user_returns_null_and_incomplete(redis, clock):
    # §1.3-3/4: User行なし → id=null・profile_complete=false(契約上正当な初回登録待ち)
    result = await _service(clock, redis).token(provider="google", idp_token=_idp_token("sub-new"))
    assert result.user_id is None
    assert result.profile_complete is False
    assert result.token_type == "Bearer"
    assert result.expires_in == 3600
    assert result.refresh_token


async def test_token_with_user_returns_id_and_complete(redis, clock):
    async def lookup(provider: str, subject: str) -> uuid.UUID | None:
        assert (provider, subject) == ("google", "sub-1")
        return USER_ID

    result = await _service(clock, redis, lookup=lookup).token(
        provider="google", idp_token=_idp_token("sub-1")
    )
    assert result.user_id == USER_ID
    assert result.profile_complete is True


async def test_token_uses_settings_default_idp_config(redis, clock):
    # Settings既定issuer/audience(テストIdP値)と同梱JWKSが最初から噛み合っていること
    result = await _service(clock, redis).token(provider="apple", idp_token=_idp_token("a-1", "apple"))
    assert result.user_id is None


async def test_access_token_carries_provider_subject_sid(redis, clock):
    result = await _service(clock, redis).token(provider="google", idp_token=_idp_token("sub-1"))
    claims = verify_access_token(clock=clock, secret=SECRET, token=result.access_token)
    assert claims.auth_provider == "google"
    assert claims.auth_subject == "sub-1"
    assert claims.sid  # リフレッシュ族id(logoutが族を特定する)


async def test_token_invalid_idp_token_is_401(redis, clock):
    with pytest.raises(InvalidIdpTokenError):
        await _service(clock, redis).token(provider="google", idp_token="garbage")


async def test_token_lookup_failure_is_503(redis, clock):
    async def broken(provider: str, subject: str) -> uuid.UUID | None:
        raise RuntimeError("db down")

    with pytest.raises(DependencyUnavailableError):
        await _service(clock, redis, lookup=broken).token(provider="google", idp_token=_idp_token())


async def test_token_redis_failure_is_503(clock):
    import redis as redis_lib

    class _BrokenSessions:
        async def create_refresh(self, *, provider: str, subject: str):
            raise redis_lib.exceptions.ConnectionError("redis down")

    svc = AuthService(
        clock=clock,
        secret=SECRET,
        sessions=_BrokenSessions(),  # type: ignore[arg-type]
        idp=_idp(),
        user_lookup=_none_lookup,
    )
    with pytest.raises(DependencyUnavailableError):
        await svc.token(provider="google", idp_token=_idp_token())
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_service_token.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.auth.service'`)

- [ ] **Step 3: service.py を実装(tokenまで)**

`backend/src/latch/auth/service.py`:

```python
"""認証ユースケース(design §2.6・§1.3)。

token / refresh / logout の3ユースケースとusers読み取り(生SQL 1本)。
SQLAlchemyモデル・リポジトリ層はM1(design §1.3-2)。DB・Redis等の依存障害は
DependencyUnavailableError(503)へ包む(05 第5節「全API」・design §2.7)。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.auth.errors import AuthError, DependencyUnavailableError
from latch.auth.idp import IdPVerifier
from latch.auth.sessions import SessionStore
from latch.auth.tokens import ACCESS_TTL_S, issue_access_token
from latch.core.clock import Clock

UserLookup = Callable[[str, str], Awaitable[uuid.UUID | None]]

# auth_provider+auth_subject はUNIQUE(uq_users_auth_provider_subject)なので高々1行
_USER_SELECT = text(
    "SELECT id FROM users WHERE auth_provider = :provider AND auth_subject = :subject"
)


def make_user_lookup(engine: AsyncEngine) -> UserLookup:
    """users表の読み取り専用lookup(行なければNone=初回登録待ち)。

    マイグレーションは追加しない(design §2.6)。User作成経路はM1。
    """

    async def user_lookup(provider: str, subject: str) -> uuid.UUID | None:
        async with engine.connect() as conn:
            result = await conn.execute(
                _USER_SELECT, {"provider": provider, "subject": subject}
            )
            row = result.first()
            return uuid.UUID(row[0]) if row is not None else None

    return user_lookup


@dataclass(frozen=True)
class TokenResult:
    """POST /v1/auth/token の200応答本体(05 第5節)。"""

    access_token: str
    token_type: str
    expires_in: int
    refresh_token: str
    user_id: uuid.UUID | None
    profile_complete: bool


@dataclass(frozen=True)
class RefreshResult:
    """POST /v1/auth/refresh の200応答本体(回転式・05 第5節)。"""

    access_token: str
    token_type: str
    expires_in: int
    refresh_token: str


class AuthService:
    """token/refresh/logout ユースケース(05 第5節)。"""

    def __init__(
        self,
        *,
        clock: Clock,
        secret: str,
        sessions: SessionStore,
        idp: IdPVerifier,
        user_lookup: UserLookup,
    ) -> None:
        self._clock = clock
        self._secret = secret
        self._sessions = sessions
        self._idp = idp
        self._user_lookup = user_lookup

    async def token(self, *, provider: str, idp_token: str) -> TokenResult:
        try:
            return await self._token(provider=provider, idp_token=idp_token)
        except AuthError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("auth dependency unavailable") from exc

    async def _token(self, *, provider: str, idp_token: str) -> TokenResult:
        _, subject = await self._idp.verify(
            provider=provider, idp_token=idp_token, clock=self._clock
        )
        user_id = await self._user_lookup(provider, subject)
        issued = await self._sessions.create_refresh(provider=provider, subject=subject)
        access = issue_access_token(
            clock=self._clock,
            secret=self._secret,
            provider=provider,
            subject=subject,
            sid=issued.sid,
        )
        # profile_completeは「User行が存在する」から導出(列は存在しない — design §1.3-3)
        return TokenResult(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL_S,
            refresh_token=issued.token,
            user_id=user_id,
            profile_complete=user_id is not None,
        )
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_service_token.py -v`
Expected: PASS(7件)

- [ ] **Step 5: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/auth/service.py backend/tests/unit/auth/test_service_token.py
git commit -m "feat: tokenユースケースとusers読み取り(user.id応答分岐・503包み)"
```

---

### Task 7: service.py(2/3)— refresh / logout / authenticate

**Files:**
- Modify: `backend/src/latch/auth/service.py`(`refresh` / `logout` / `authenticate` を追加)
- Test: `backend/tests/unit/auth/test_service_refresh.py`

**Interfaces:**
- Consumes: Task 6の `AuthService`・Task 3の `verify_access_token`・Task 1の `UnauthenticatedError`
- Produces: `async refresh(*, refresh_token: str) -> RefreshResult`・`async logout(*, claims: AccessTokenClaims) -> None`・`async authenticate(*, token: str) -> AccessTokenClaims`(JWT検証+失効リスト照会 — Task 9の `require_authenticated` が呼ぶ。失敗は `UnauthenticatedError` / `InvalidRefreshTokenError` / `DependencyUnavailableError`)

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/auth/test_service_refresh.py`:

```python
"""refresh/logoutユースケース: 回転応答・族失効連鎖・logout一連(design §4.1-4)。

test_service_token.py と同じ構成のヘルパー群を使う(ws-2の慣例: conftestなし)。
"""

from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import jwt as pyjwt
import pytest

from latch.auth.errors import InvalidRefreshTokenError, UnauthenticatedError
from latch.auth.idp import IdPVerifyConfig, IdPVerifier
from latch.auth.service import AuthService
from latch.auth.sessions import SessionStore
from latch.auth.testkeys import DEFAULT_KID, load_private_key
from latch.core.clock import FakeClock
from latch.settings import Settings

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
SETTINGS = Settings(app_env="ci")
ISSUER_G = SETTINGS.auth_idp_issuer_google
AUDIENCE = SETTINGS.auth_idp_audience_google
SECRET = "unit-test-access-secret-0123456789abcdef"


async def _none_lookup(provider: str, subject: str):
    return None


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _idp_token(subject: str = "sub-1") -> str:
    payload = {
        "iss": ISSUER_G,
        "aud": AUDIENCE,
        "sub": subject,
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 3600,
    }
    return pyjwt.encode(payload, load_private_key(), algorithm="RS256", headers={"kid": DEFAULT_KID})


def _service(clock, redis) -> AuthService:
    return AuthService(
        clock=clock,
        secret=SECRET,
        sessions=SessionStore(redis),
        idp=IdPVerifier(
            configs={"google": IdPVerifyConfig(issuer=ISSUER_G, audience=AUDIENCE)}
        ),
        user_lookup=_none_lookup,
    )


async def _token_pair(svc: AuthService) -> tuple[str, str]:
    result = await svc.token(provider="google", idp_token=_idp_token())
    return result.access_token, result.refresh_token


async def test_refresh_returns_rotated_pair(redis, clock):
    svc = _service(clock, redis)
    access, refresh = await _token_pair(svc)
    rotated = await svc.refresh(refresh_token=refresh)
    assert rotated.token_type == "Bearer"
    assert rotated.expires_in == 3600
    assert rotated.access_token
    assert rotated.refresh_token != refresh  # 回転(旧トークンは無効化済)


async def test_refresh_reuse_revokes_family_chain(redis, clock):
    # Review Focus #2(service経由): 旧再利用→401・回転後の新トークンも401
    svc = _service(clock, redis)
    _, refresh = await _token_pair(svc)
    rotated = await svc.refresh(refresh_token=refresh)
    with pytest.raises(InvalidRefreshTokenError):
        await svc.refresh(refresh_token=refresh)
    with pytest.raises(InvalidRefreshTokenError):
        await svc.refresh(refresh_token=rotated.refresh_token)


async def test_refresh_unknown_token_rejected(redis, clock):
    svc = _service(clock, redis)
    with pytest.raises(InvalidRefreshTokenError):
        await svc.refresh(refresh_token="unknown-token")


async def test_logout_revokes_access_and_family(redis, clock):
    svc = _service(clock, redis)
    access, refresh = await _token_pair(svc)
    claims = await svc.authenticate(token=access)
    assert claims.auth_subject == "sub-1"  # logout前は有効
    await svc.logout(claims=claims)
    # 同JWTの再提示 → 401 UNAUTHENTICATED(失効リスト掲載)
    with pytest.raises(UnauthenticatedError):
        await svc.authenticate(token=access)
    # 当族のリフレッシュトークンも無効
    with pytest.raises(InvalidRefreshTokenError):
        await svc.refresh(refresh_token=refresh)


async def test_authenticate_expired_token(redis, clock):
    # design §4.2後段(FakeClock再現): 1時間超過で401
    svc = _service(clock, redis)
    access, _ = await _token_pair(svc)
    clock.advance(timedelta(seconds=3601))
    with pytest.raises(UnauthenticatedError):
        await svc.authenticate(token=access)


async def test_authenticate_garbage_token(redis, clock):
    svc = _service(clock, redis)
    with pytest.raises(UnauthenticatedError):
        await svc.authenticate(token="not-a-jwt")
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_service_refresh.py -v`
Expected: FAIL(`AttributeError: 'AuthService' object has no attribute 'refresh'`)

- [ ] **Step 3: service.py へ refresh / logout / authenticate を追加**

`backend/src/latch/auth/service.py` のimport節を変更:

```python
from latch.auth.errors import (
    AuthError,
    DependencyUnavailableError,
    UnauthenticatedError,
)
from latch.auth.tokens import (
    ACCESS_TTL_S,
    AccessTokenClaims,
    issue_access_token,
    verify_access_token,
)
```

`AuthService` クラスの `token` / `_token` の後に追加:

```python
    async def refresh(self, *, refresh_token: str) -> RefreshResult:
        try:
            return await self._refresh(refresh_token=refresh_token)
        except AuthError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("auth dependency unavailable") from exc

    async def _refresh(self, *, refresh_token: str) -> RefreshResult:
        rotated = await self._sessions.rotate_refresh(
            token=refresh_token, now=self._clock.now()
        )
        access = issue_access_token(
            clock=self._clock,
            secret=self._secret,
            provider=rotated.provider,
            subject=rotated.subject,
            sid=rotated.sid,
        )
        return RefreshResult(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL_S,
            refresh_token=rotated.token,
        )

    async def logout(self, *, claims: AccessTokenClaims) -> None:
        try:
            await self._logout(claims=claims)
        except AuthError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("auth dependency unavailable") from exc

    async def _logout(self, *, claims: AccessTokenClaims) -> None:
        # 対象JWTをRedis失効リストへ(TTL=残り有効期限)+リフレッシュ族を全失効(05 第5節)
        await self._sessions.revoke_access(
            jti=claims.jti, exp=claims.exp, now=self._clock.now()
        )
        await self._sessions.revoke_family(sid=claims.sid)

    async def authenticate(self, *, token: str) -> AccessTokenClaims:
        """保護エンドポイント用: JWT検証(署名・iss・alg・exp手動)+失効リスト照会。

        05 第5節「失効リスト掲載」を含む401 UNAUTHENTICATEDの判定点。
        require_authenticated(deps)はこれを呼ぶだけ(design §2.3)。
        """
        try:
            claims = verify_access_token(
                clock=self._clock, secret=self._secret, token=token
            )
            if await self._sessions.is_revoked(jti=claims.jti):
                raise UnauthenticatedError("access token revoked")
            return claims
        except AuthError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("auth dependency unavailable") from exc
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_service_refresh.py -v`
Expected: PASS(6件)

- [ ] **Step 5: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/auth/service.py backend/tests/unit/auth/test_service_refresh.py
git commit -m "feat: refresh/logout/authenticateユースケース(回転応答・失効一連)"
```

---

### Task 8: service.py(3/3)— build_auth_service + prodガード

**Files:**
- Modify: `backend/src/latch/auth/service.py`(`build_auth_service` を追加)
- Test: `backend/tests/unit/auth/test_prod_guard.py`

**Interfaces:**
- Consumes: Task 2のSettings・Task 4/5/6/7の構成要素・Task 1の `TEST_ACCESS_SECRET` / `TEST_ISSUER_PREFIX` / `TEST_AUDIENCE`・`redis.asyncio.Redis`
- Produces: `build_auth_service(*, clock: Clock, settings: Settings, redis_client: aioredis.Redis, user_lookup: UserLookup) -> AuthService`(prod ガードつき。redis_client の生成・解体は呼び出し側=main.py lifespan の責務)。Task 9のmain.pyとテストのapp組み立てが利用

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/auth/test_prod_guard.py`:

```python
"""build_auth_serviceのprod拒否とci/staging既定受理(design §2.5-3・§4.1-8)。"""

import base64
import json
from datetime import UTC, datetime

import fakeredis.aioredis
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from latch.auth.service import build_auth_service
from latch.auth.testkeys import DEFAULT_KID, load_private_key
from latch.core.clock import FakeClock
from latch.settings import Settings

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
REAL_SECRET = "prod-like-secret-0123456789abcdef0123456789"
REAL_JWKS = "https://real-idp.example.com/jwks.json"
REAL_ISSUER = "https://accounts.example.com"
REAL_AUDIENCE = "latch-prod-app"

AUTH_ENV_VARS = (
    "LATCH_REDIS_URL",
    "LATCH_AUTH_ACCESS_SECRET",
    "LATCH_AUTH_IDP_JWKS_URL_GOOGLE",
    "LATCH_AUTH_IDP_JWKS_URL_APPLE",
    "LATCH_AUTH_IDP_ISSUER_GOOGLE",
    "LATCH_AUTH_IDP_ISSUER_APPLE",
    "LATCH_AUTH_IDP_AUDIENCE_GOOGLE",
    "LATCH_AUTH_IDP_AUDIENCE_APPLE",
)


async def _none_lookup(provider: str, subject: str):
    return None


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _clean(monkeypatch, **overrides) -> Settings:
    for var in AUTH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return Settings(**overrides)


def _prod(**overrides) -> Settings:
    base = dict(
        app_env="prod",
        auth_access_secret=REAL_SECRET,
        auth_idp_jwks_url_google=REAL_JWKS,
        auth_idp_jwks_url_apple=REAL_JWKS,
        auth_idp_issuer_google=REAL_ISSUER,
        auth_idp_issuer_apple=REAL_ISSUER,
        auth_idp_audience_google=REAL_AUDIENCE,
        auth_idp_audience_apple=REAL_AUDIENCE,
    )
    base.update(overrides)
    return Settings(**base)


async def test_ci_defaults_build_and_issue(clock, redis, monkeypatch):
    # Review Focus #5: ciは同梱JWKS+テストissuer/audience+テストsecretでそのまま動く
    settings = _clean(monkeypatch, app_env="ci")
    svc = build_auth_service(
        clock=clock, settings=settings, redis_client=redis, user_lookup=_none_lookup
    )
    payload = {
        "iss": "https://idp.ci.latch.test/google",
        "aud": "latch-test-app",
        "sub": "sub-ci",
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 3600,
    }
    idp_token = pyjwt.encode(
        payload, load_private_key(), algorithm="RS256", headers={"kid": DEFAULT_KID}
    )
    result = await svc.token(provider="google", idp_token=idp_token)
    assert result.token_type == "Bearer"
    assert result.user_id is None


def test_staging_with_injected_pair_builds(clock, redis, tmp_path):
    # staging: gen-keypairで生成した専用ペアをfile:// URLで注入(design §2.5-1)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def b64u(value: int) -> str:
        size = (value.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(value.to_bytes(size, "big")).rstrip(b"=").decode()

    numbers = key.public_key().public_numbers()
    jwk = {
        "kty": "RSA", "kid": DEFAULT_KID, "use": "sig", "alg": "RS256",
        "n": b64u(numbers.n), "e": b64u(numbers.e),
    }
    jwks_path = tmp_path / "idp_test_jwks.json"
    jwks_path.write_text(json.dumps({"keys": [jwk]}))
    settings = Settings(
        app_env="staging",
        auth_access_secret="staging-secret-0123456789abcdef",
        auth_idp_jwks_url_google=jwks_path.as_uri(),
        auth_idp_jwks_url_apple=jwks_path.as_uri(),
    )
    svc = build_auth_service(
        clock=clock, settings=settings, redis_client=redis, user_lookup=_none_lookup
    )
    assert svc is not None  # 構築成功(発行検証はtools試験が所有)


def test_prod_rejects_empty_secret(clock, redis):
    with pytest.raises(ValueError, match="auth_access_secret"):
        build_auth_service(
            clock=clock, settings=_prod(auth_access_secret=""),
            redis_client=redis, user_lookup=_none_lookup,
        )


def test_prod_rejects_test_secret(clock, redis):
    from latch.auth.testkeys import TEST_ACCESS_SECRET

    with pytest.raises(ValueError, match="auth_access_secret"):
        build_auth_service(
            clock=clock, settings=_prod(auth_access_secret=TEST_ACCESS_SECRET),
            redis_client=redis, user_lookup=_none_lookup,
        )


def test_prod_rejects_missing_jwks_url(clock, redis):
    with pytest.raises(ValueError, match="jwks_url"):
        build_auth_service(
            clock=clock, settings=_prod(auth_idp_jwks_url_google=""),
            redis_client=redis, user_lookup=_none_lookup,
        )


def test_prod_rejects_test_issuer(clock, redis):
    with pytest.raises(ValueError, match="issuer"):
        build_auth_service(
            clock=clock,
            settings=_prod(auth_idp_issuer_google="https://idp.ci.latch.test/google"),
            redis_client=redis, user_lookup=_none_lookup,
        )


def test_prod_rejects_test_audience(clock, redis):
    with pytest.raises(ValueError, match="audience"):
        build_auth_service(
            clock=clock, settings=_prod(auth_idp_audience_apple="latch-test-app"),
            redis_client=redis, user_lookup=_none_lookup,
        )


def test_prod_with_real_values_builds(clock, redis):
    svc = build_auth_service(
        clock=clock, settings=_prod(), redis_client=redis, user_lookup=_none_lookup
    )
    assert svc is not None
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_prod_guard.py -v`
Expected: FAIL(`ImportError: cannot import name 'build_auth_service'`)

- [ ] **Step 3: service.py へ build_auth_service を追加**

`backend/src/latch/auth/service.py` のimport節へ追記(ruffのisort順に並べ替える):

```python
import redis.asyncio as aioredis

from latch.auth.testkeys import (
    TEST_ACCESS_SECRET,
    TEST_AUDIENCE,
    TEST_ISSUER_PREFIX,
)
from latch.settings import Settings
```

ファイル末尾(`AuthService` クラスの後)へ追加:

```python
def build_auth_service(
    *,
    clock: Clock,
    settings: Settings,
    redis_client: aioredis.Redis,
    user_lookup: UserLookup,
) -> AuthService:
    """設定からAuthServiceを構築する(design §2.5-3)。

    prod(app_env="prod")でテスト既定値(空secret・同梱JWKS・テストissuer/audience)
    が残存する場合はValueErrorで拒否する(ws-2のllm_mode拒否と同一パターン)。
    ci/stagingは既定値でそのまま動く。redis_client の生成・解体は呼び出し側
    (main.py lifespan)の責務 — この関数は純粋な構築のみ行う。
    """
    if settings.app_env == "prod":
        if (
            not settings.auth_access_secret
            or settings.auth_access_secret == TEST_ACCESS_SECRET
        ):
            raise ValueError(
                "prod requires a dedicated auth_access_secret (empty/test secret is not allowed)"
            )
        for provider in ("google", "apple"):
            if not getattr(settings, f"auth_idp_jwks_url_{provider}"):
                raise ValueError(f"prod requires auth_idp_jwks_url_{provider}")
            if getattr(settings, f"auth_idp_issuer_{provider}").startswith(
                TEST_ISSUER_PREFIX
            ):
                raise ValueError(f"prod requires a real issuer for {provider}")
            if getattr(settings, f"auth_idp_audience_{provider}") == TEST_AUDIENCE:
                raise ValueError(f"prod requires a real audience for {provider}")
    secret = settings.auth_access_secret or TEST_ACCESS_SECRET
    configs: dict[str, IdPVerifyConfig] = {}
    for provider in ("google", "apple"):
        url = getattr(settings, f"auth_idp_jwks_url_{provider}")
        configs[provider] = IdPVerifyConfig(
            issuer=getattr(settings, f"auth_idp_issuer_{provider}"),
            audience=getattr(settings, f"auth_idp_audience_{provider}"),
            jwks_url=url or None,
        )
    return AuthService(
        clock=clock,
        secret=secret,
        sessions=SessionStore(redis_client),
        idp=IdPVerifier(configs=configs),
        user_lookup=user_lookup,
    )
```

(`IdPVerifyConfig` をimport節の `from latch.auth.idp import IdPVerifier` に追加する)

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_prod_guard.py -v`
Expected: PASS(8件)

- [ ] **Step 5: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/auth/service.py backend/tests/unit/auth/test_prod_guard.py
git commit -m "feat: build_auth_service(prodガードつき工場)"
```

---

### Task 9: deps.py + routes.py + main.py統合 + compose.yaml

**Files:**
- Create: `backend/src/latch/auth/deps.py`、`backend/src/latch/auth/routes.py`
- Modify: `backend/src/latch/auth/errors.py`(変更なし・そのまま)、`backend/src/latch/main.py`(lifespan・2ルータinclude・エラーハンドラ・第3引数)、`compose.yaml`(apiサービスへ環境変数2件)
- Test: `backend/tests/unit/auth/test_deps.py`、`backend/tests/unit/auth/test_routes.py`

**Interfaces:**
- Consumes: Task 6/7の `AuthService`・`AccessTokenClaims`・Task 8の `build_auth_service` / `make_user_lookup`・雛形の `create_app` / `get_clock`・`latch.core.db.create_db_engine`
- Produces: `deps.get_auth_service(request) -> AuthService`・`deps.require_authenticated(...) -> AccessTokenClaims`(欠落・非Bearer・無効・期限切れ・失効リスト掲載は `UnauthenticatedError`)。`routes.public_router`(prefix `/v1/auth`・POST /token・POST /refresh)・`routes.logout_router`(prefix `/v1/auth`・dependencies=[require_authenticated]・POST /logout・204)。`main.create_app(clock=None, settings=None, auth_service=None)`(第3引数追加・指定時はlifespan構築をスキップ)。M1以降のドメインルータは `require_authenticated` をルータ単位に付す(C3の強制方法)

- [ ] **Step 1: 失敗するテストを書く(test_deps.py)**

`backend/tests/unit/auth/test_deps.py`:

```python
"""require_authenticated: 401系バリエーション・ダミー保護ルート(design §4.1-6)。"""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import fakeredis.aioredis
import jwt as pyjwt
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from latch.auth.deps import require_authenticated
from latch.auth.service import build_auth_service
from latch.auth.testkeys import DEFAULT_KID, load_private_key
from latch.auth.tokens import AccessTokenClaims
from latch.core.clock import FakeClock
from latch.main import create_app
from latch.settings import Settings

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


async def _none_lookup(provider: str, subject: str):
    return None


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def svc(clock, redis):
    return build_auth_service(
        clock=clock, settings=Settings(app_env="ci"),
        redis_client=redis, user_lookup=_none_lookup,
    )


@pytest.fixture
def app(clock, svc) -> FastAPI:
    a = create_app(clock=clock, settings=Settings(app_env="ci"), auth_service=svc)

    @a.get("/_test/protected")
    async def protected(
        claims: Annotated[AccessTokenClaims, Depends(require_authenticated)]
    ) -> dict:
        return {
            "provider": claims.auth_provider,
            "subject": claims.auth_subject,
            "jti": claims.jti,
        }

    return a


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _idp_token(subject: str = "sub-1") -> str:
    s = Settings(app_env="ci")
    payload = {
        "iss": s.auth_idp_issuer_google,
        "aud": s.auth_idp_audience_google,
        "sub": subject,
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 3600,
    }
    return pyjwt.encode(
        payload, load_private_key(), algorithm="RS256", headers={"kid": DEFAULT_KID}
    )


async def _access(svc) -> str:
    result = await svc.token(provider="google", idp_token=_idp_token())
    return result.access_token


async def test_missing_authorization_header(client):
    resp = await client.get("/_test/protected")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_non_bearer_scheme(client, svc):
    access = await _access(svc)
    resp = await client.get(
        "/_test/protected", headers={"Authorization": f"Basic {access}"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_garbage_bearer_token(client):
    resp = await client.get("/_test/protected", headers={"Authorization": "Bearer junk"})
    assert resp.status_code == 401


async def test_valid_token_passes(client, svc):
    access = await _access(svc)
    resp = await client.get("/_test/protected", headers={"Authorization": f"Bearer {access}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "google"
    assert body["subject"] == "sub-1"
    assert body["jti"]


async def test_tampered_token_rejected(client, svc):
    access = await _access(svc)
    tampered = access[:-4] + ("AAAA" if not access.endswith("AAAA") else "BBBB")
    resp = await client.get("/_test/protected", headers={"Authorization": f"Bearer {tampered}"})
    assert resp.status_code == 401


async def test_expired_token_rejected(client, svc, clock):
    access = await _access(svc)
    clock.advance(timedelta(seconds=3601))
    resp = await client.get("/_test/protected", headers={"Authorization": f"Bearer {access}"})
    assert resp.status_code == 401


async def test_revoked_token_rejected(client, svc):
    access = await _access(svc)
    claims = await svc.authenticate(token=access)
    await svc.logout(claims=claims)
    resp = await client.get("/_test/protected", headers={"Authorization": f"Bearer {access}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"
```

- [ ] **Step 2: 失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_deps.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.auth.deps'`)

- [ ] **Step 3: deps.py を実装**

`backend/src/latch/auth/deps.py`:

```python
"""認証Dependency(design §2.7)。

require_authenticated がC3の強制点 — M1以降のドメインルータはルータ単位で
この依存を付す(v1契約上の認証不要2エンドポイントのみが依存を持たない)。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from latch.auth.errors import UnauthenticatedError
from latch.auth.service import AuthService
from latch.auth.tokens import AccessTokenClaims


def get_auth_service(request: Request) -> AuthService:
    """app.state.auth_service へのアクセス(lifespanまたはテスト注入で載る)。"""
    return request.app.state.auth_service


async def require_authenticated(
    svc: Annotated[AuthService, Depends(get_auth_service)],
    authorization: Annotated[str | None, Header()] = None,
) -> AccessTokenClaims:
    """Authorization: Bearer <JWT> を検証しclaimsを返す(05 第5節)。

    欠落・非Bearer・無効・期限切れ・失効リスト掲載は UnauthenticatedError
    (401 UNAUTHENTICATED。ハンドラが共通envelopeへ出す)。
    """
    if authorization is None or not authorization.startswith("Bearer "):
        raise UnauthenticatedError("missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    return await svc.authenticate(token=token)
```

- [ ] **Step 4: 失敗するテストを書く(test_routes.py)**

`backend/tests/unit/auth/test_routes.py`:

```python
"""3エンドポイントのAPI挙動・エラーenvelope・400/422振り分け・ログ規約(design §4.1-5)。"""

import logging
from datetime import UTC, datetime

import fakeredis.aioredis
import jwt as pyjwt
from httpx import ASGITransport, AsyncClient
import pytest

from latch.auth.service import build_auth_service
from latch.auth.testkeys import DEFAULT_KID, load_private_key
from latch.core.clock import FakeClock
from latch.main import create_app
from latch.settings import Settings

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


async def _none_lookup(provider: str, subject: str):
    return None


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def svc(clock, redis):
    return build_auth_service(
        clock=clock, settings=Settings(app_env="ci"),
        redis_client=redis, user_lookup=_none_lookup,
    )


@pytest.fixture
def app(clock, svc):
    return create_app(clock=clock, settings=Settings(app_env="ci"), auth_service=svc)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _idp_token(subject: str = "sub-1") -> str:
    s = Settings(app_env="ci")
    payload = {
        "iss": s.auth_idp_issuer_google,
        "aud": s.auth_idp_audience_google,
        "sub": subject,
        "iat": int(NOW.timestamp()),
        "exp": int(NOW.timestamp()) + 3600,
    }
    return pyjwt.encode(
        payload, load_private_key(), algorithm="RS256", headers={"kid": DEFAULT_KID}
    )


def _assert_envelope(body: dict, code: str) -> None:
    # 05 第5節 エラー形式: {"error": {code, message, details}}
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    assert body["error"]["details"] is None
    assert body["error"]["message"]


async def _token_pair(client, subject: str = "sub-1") -> dict:
    resp = await client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": _idp_token(subject)}
    )
    assert resp.status_code == 200
    return resp.json()


async def test_token_success_shape(client):
    body = await _token_pair(client)
    assert set(body) == {"access_token", "token_type", "expires_in", "refresh_token", "user"}
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["user"] == {"id": None, "profile_complete": False}


async def test_token_invalid_idp_token_envelope(client):
    resp = await client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": "garbage"}
    )
    assert resp.status_code == 401
    _assert_envelope(resp.json(), "INVALID_IDP_TOKEN")


async def test_refresh_success_shape_and_rotation(client):
    first = await _token_pair(client)
    resp = await client.post(
        "/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"access_token", "token_type", "expires_in", "refresh_token"}
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["refresh_token"] != first["refresh_token"]
    # 旧トークン再利用 → 401(回転の実効)
    reuse = await client.post(
        "/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert reuse.status_code == 401
    _assert_envelope(reuse.json(), "INVALID_REFRESH_TOKEN")


async def test_refresh_unknown_token(client):
    resp = await client.post("/v1/auth/refresh", json={"refresh_token": "unknown"})
    assert resp.status_code == 401
    _assert_envelope(resp.json(), "INVALID_REFRESH_TOKEN")


async def test_logout_returns_204_and_revokes(client):
    body = await _token_pair(client)
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    resp = await client.post("/v1/auth/logout", headers=headers)
    assert resp.status_code == 204
    assert resp.content == b""
    # 同JWTの再提示 → 401 UNAUTHENTICATED(失効リスト掲載)
    again = await client.post("/v1/auth/logout", headers=headers)
    assert again.status_code == 401
    _assert_envelope(again.json(), "UNAUTHENTICATED")
    # 当族のリフレッシュも無効
    rotated = await client.post(
        "/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert rotated.status_code == 401


async def test_logout_without_token(client):
    resp = await client.post("/v1/auth/logout")
    assert resp.status_code == 401
    _assert_envelope(resp.json(), "UNAUTHENTICATED")


async def test_invalid_provider_returns_422(client):
    resp = await client.post(
        "/v1/auth/token", json={"provider": "line", "idp_token": "x"}
    )
    assert resp.status_code == 422
    _assert_envelope(resp.json(), "VALIDATION_ERROR")


async def test_missing_required_field_returns_422(client):
    resp = await client.post("/v1/auth/token", json={"provider": "google"})
    assert resp.status_code == 422
    _assert_envelope(resp.json(), "VALIDATION_ERROR")


async def test_malformed_json_returns_400(client):
    resp = await client.post(
        "/v1/auth/token",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    _assert_envelope(resp.json(), "MALFORMED_REQUEST")


async def test_logs_contain_no_token_or_subject(client, caplog):
    # Review Focus #4: ログはイベント名と結果/コードのみ(08 第2.4節)
    secret_subject = "secret-subject-42"
    with caplog.at_level(logging.INFO, logger="latch.auth"):
        resp = await client.post(
            "/v1/auth/token",
            json={"provider": "google", "idp_token": _idp_token(secret_subject)},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert secret_subject not in caplog.text
    assert body["access_token"] not in caplog.text
    assert body["refresh_token"] not in caplog.text
    assert body["access_token"].split(".")[1] not in caplog.text  # ペイロード断片も不出
    assert "auth.token ok" in caplog.text  # 成功ログは出る
```

- [ ] **Step 5: routes.py を実装**

`backend/src/latch/auth/routes.py`:

```python
"""認証ルータ(design §2.7)。

public_router: token/refresh — v1契約で認証不要の2エンドポイントのみ(05 第5節)
logout_router: require_authenticated 依存の保護ルータ
ログはイベント名と結果/コードのみ — トークン・claim・subjectは出さない(08 第2.4節)。
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from latch.auth.deps import get_auth_service, require_authenticated
from latch.auth.service import AuthService
from latch.auth.tokens import ACCESS_TTL_S, AccessTokenClaims

logger = logging.getLogger("latch.auth")

public_router = APIRouter(prefix="/v1/auth", tags=["auth"])
logout_router = APIRouter(prefix="/v1/auth", tags=["auth"])


class TokenRequest(BaseModel):
    provider: Literal["google", "apple"]  # 04 D-21: IdPはGoogle/Appleの2種
    idp_token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserInfo(BaseModel):
    id: uuid.UUID | None
    profile_complete: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = ACCESS_TTL_S
    refresh_token: str
    user: UserInfo


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = ACCESS_TTL_S
    refresh_token: str


@public_router.post("/token", response_model=TokenResponse)
async def token(
    body: TokenRequest,
    svc: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenResponse:
    result = await svc.token(provider=body.provider, idp_token=body.idp_token)
    logger.info("auth.token ok")
    return TokenResponse(
        access_token=result.access_token,
        token_type=result.token_type,
        expires_in=result.expires_in,
        refresh_token=result.refresh_token,
        user=UserInfo(id=result.user_id, profile_complete=result.profile_complete),
    )


@public_router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    body: RefreshRequest,
    svc: Annotated[AuthService, Depends(get_auth_service)],
) -> RefreshResponse:
    result = await svc.refresh(refresh_token=body.refresh_token)
    logger.info("auth.refresh ok")
    return RefreshResponse(
        access_token=result.access_token,
        token_type=result.token_type,
        expires_in=result.expires_in,
        refresh_token=result.refresh_token,
    )


@logout_router.post("/logout", status_code=204)
async def logout(
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
    svc: Annotated[AuthService, Depends(get_auth_service)],
) -> None:
    """request {}(AuthorizationヘッダーのJWTが対象)→ 204(05 第5節)。冪等。"""
    await svc.logout(claims=claims)
    logger.info("auth.logout ok")
```

(注: `logout_router` のルータ単位保護はエンドポイントの `Depends(require_authenticated)` が担う形にした — `claims` 引数で同じ依存を取得する。design §2.7の「`dependencies=[Depends(require_authenticated)]`」と等価の強制であり、claimsも同時に取れる。この確定を報告欄「本計画§8」へ記録する)

- [ ] **Step 6: main.py を書き換え + compose.yaml へ環境変数を追記**

`backend/src/latch/main.py` を丸ごと置き換える(`/health` の応答と既存引数の意味は不変):

```python
"""FastAPIアプリケーションファクトリ(ws-3で認証を統合)。

/health は運用プローブ用でありv1 API契約の外に置く(C3の対象外)。
server_time は get_clock() 由来 — Clock差し替えが全経路で効くことの生の消費者。
lifespanでredis・db engine・AuthServiceを構築する(design §2.6)。
第3引数 auth_service 指定時は構築をスキップ(テスト注入)。
"""

import logging
from contextlib import asynccontextmanager
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from latch.auth.errors import AuthError
from latch.auth.routes import logout_router, public_router
from latch.auth.service import build_auth_service, make_user_lookup
from latch.core.clock import Clock, SystemClock
from latch.core.db import create_db_engine
from latch.core.deps import get_clock
from latch.settings import Settings

logger = logging.getLogger("latch.auth")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if hasattr(app.state, "auth_service"):
        # テスト注入済み(create_app 第3引数)— 構築しない
        yield
        return
    settings: Settings = app.state.settings
    redis_client = aioredis.Redis.from_url(
        settings.redis_url, decode_responses=True
    )
    engine = create_db_engine(settings)
    app.state.db_engine = engine
    app.state.auth_service = build_auth_service(
        clock=app.state.clock,
        settings=settings,
        redis_client=redis_client,
        user_lookup=make_user_lookup(engine),
    )
    try:
        yield
    finally:
        await redis_client.aclose()
        await engine.dispose()


def _error_body(code: str, message: str) -> dict:
    # 05 第5節 エラー形式
    return {"error": {"code": code, "message": message, "details": None}}


def create_app(
    clock: Clock | None = None,
    settings: Settings | None = None,
    auth_service=None,
) -> FastAPI:
    app = FastAPI(title="LATCH API", lifespan=_lifespan)
    app.state.clock = clock if clock is not None else SystemClock()
    app.state.settings = settings if settings is not None else Settings()
    if auth_service is not None:
        app.state.auth_service = auth_service

    @app.get("/health")
    async def health(
        clock: Annotated[Clock, Depends(get_clock)],
    ) -> dict[str, str]:
        return {"status": "ok", "server_time": clock.now().isoformat()}

    app.include_router(public_router)
    app.include_router(logout_router)

    @app.exception_handler(AuthError)
    async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
        logger.warning("auth.error code=%s", exc.code)
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(exc.code, str(exc)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 05 第5節の使い分け: JSON形式不正=400 / 必須欠落・値域外=422
        if any(err.get("type") == "json_invalid" for err in exc.errors()):
            return JSONResponse(
                status_code=400,
                content=_error_body("MALFORMED_REQUEST", "request body is not valid JSON"),
            )
        return JSONResponse(
            status_code=422,
            content=_error_body("VALIDATION_ERROR", "request validation failed"),
        )

    return app


# composeのapiサービスが参照するエントリポイント(uvicorn latch.main:app)
app = create_app()
```

`compose.yaml` の `api` サービスへ `environment` ブロックを追記(`command` 行の後。worker・db・redisサービスは触らない):

```yaml
  api:
    build:
      context: backend
    # 単一プロセス = API 1インスタンス(10 第1節)
    command: uvicorn latch.main:app --host 0.0.0.0 --port 8000
    environment:
      LATCH_DATABASE_URL: postgresql+asyncpg://latch:latch@db:5432/latch
      LATCH_REDIS_URL: redis://redis:6379/0
    ports:
      - "127.0.0.1:8000:8000"
```

(**コンテナ再作成は行わない** — §0のとおりcomposeコマンドは実行禁止。スーパーバイザー検証時に `docker compose up -d --build api` で反映される)

- [ ] **Step 7: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_deps.py tests/unit/auth/test_routes.py tests/unit/test_app_health.py -v`
Expected: PASS(test_deps 7件 + test_routes 10件 + 既存 `/health` 試験もグリーン)

- [ ] **Step 8: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/auth/deps.py backend/src/latch/auth/routes.py backend/src/latch/main.py compose.yaml backend/tests/unit/auth/test_deps.py backend/tests/unit/auth/test_routes.py
git commit -m "feat: 認証ルータ・require_authenticated・main lifespan統合(エラーenvelope)"
```

---

### Task 10: tools.py + __main__.py — テスト用JWT発行ツール(CLI)

**Files:**
- Create: `backend/src/latch/auth/tools.py`、`backend/src/latch/auth/__main__.py`
- Test: `backend/tests/unit/auth/test_tools.py`

**Interfaces:**
- Consumes: Task 1のtestkeys(`load_private_key`・`DEFAULT_KID`)・Task 2のSettings・Task 4のIdPVerifier(試験側)・`latch.core.clock.SystemClock`
- Produces: `issue_idp_token(*, provider: str, subject: str, expires_in: int = 3600, private_key_pem: bytes | None = None, issuer: str | None = None, audience: str | None = None, iat: int | None = None) -> str`(issuer/audience既定=Settingsと同一=API側検証設定と自動一致。アクセストークンの直接発行は**持たない**)・`generate_keypair(*, out_dir: Path) -> None`(秘密鍵PEM+JWKS JSON・kid=DEFAULT_KID)・`build_arg_parser() -> ArgumentParser`・`main(argv: list[str] | None = None) -> int`。`python -m latch.auth issue-idp-token / gen-keypair`

- [ ] **Step 1: 失敗するテストを書く**

`backend/tests/unit/auth/test_tools.py`:

```python
"""ツール: issue-idp-token↔検証・gen-keypair・CLI経由(design §2.5-4・§4.1-7)。"""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from latch.auth.errors import InvalidIdpTokenError
from latch.auth.idp import IdPVerifyConfig, IdPVerifier
from latch.auth.testkeys import DEFAULT_KID
from latch.auth.tools import generate_keypair, issue_idp_token, main
from latch.core.clock import FakeClock
from latch.settings import Settings

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
SETTINGS = Settings(app_env="ci")
ISSUER_G = SETTINGS.auth_idp_issuer_google
AUDIENCE = SETTINGS.auth_idp_audience_google


def _verifier(jwks_url: str | None = None) -> IdPVerifier:
    return IdPVerifier(
        configs={
            "google": IdPVerifyConfig(issuer=ISSUER_G, audience=AUDIENCE, jwks_url=jwks_url)
        }
    )


async def test_issue_idp_token_verifies_with_default_settings():
    token = issue_idp_token(provider="google", subject="sub-x", iat=int(NOW.timestamp()))
    provider, subject = await _verifier().verify(
        provider="google", idp_token=token, clock=FakeClock(NOW)
    )
    assert (provider, subject) == ("google", "sub-x")


async def test_issue_idp_token_custom_issuer_rejected_by_default_config():
    # issuer/audience既定がSettingsと一致していることの裏返し(ずらすと拒否)
    token = issue_idp_token(
        provider="google", subject="s", issuer="https://other.example/",
        iat=int(NOW.timestamp()),
    )
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=token, clock=FakeClock(NOW))


async def test_issue_idp_token_expiry():
    token = issue_idp_token(
        provider="google", subject="s", expires_in=10, iat=int(NOW.timestamp())
    )
    clock = FakeClock(NOW)
    await _verifier().verify(provider="google", idp_token=token, clock=clock)  # 10秒内
    clock.advance(timedelta(seconds=11))
    with pytest.raises(InvalidIdpTokenError):
        await _verifier().verify(provider="google", idp_token=token, clock=clock)


async def test_gen_keypair_roundtrip(tmp_path):
    generate_keypair(out_dir=tmp_path)
    pem = (tmp_path / "idp_test_private.pem").read_bytes()
    jwks = json.loads((tmp_path / "idp_test_jwks.json").read_text())
    assert jwks["keys"][0]["kid"] == DEFAULT_KID
    # staging注入ペアで発行 → そのJWKSで検証(専用ペア経路の立証)
    token = issue_idp_token(
        provider="google", subject="staging-sub", private_key_pem=pem,
        iat=int(NOW.timestamp()),
    )
    jwks_path: Path = tmp_path / "idp_test_jwks.json"
    provider, subject = await _verifier(jwks_path.as_uri()).verify(
        provider="google", idp_token=token, clock=FakeClock(NOW)
    )
    assert subject == "staging-sub"


def test_main_issue_idp_token_prints_jwt(capsys):
    assert main(["issue-idp-token", "--provider", "google", "--subject", "demo"]) == 0
    out = capsys.readouterr().out.strip()
    assert out.count(".") == 2  # header.payload.signature


def test_main_gen_keypair(tmp_path):
    assert main(["gen-keypair", "--out-dir", str(tmp_path)]) == 0
    assert (tmp_path / "idp_test_private.pem").exists()
    assert (tmp_path / "idp_test_jwks.json").exists()


def test_main_requires_known_command():
    with pytest.raises(SystemExit):
        main(["no-such-command"])


async def test_cli_issue_token_verifies():
    # 完了条件7のunit立証: python -m latch.auth のCLI出力がIdPVerifierで検証できる
    proc = subprocess.run(
        [
            sys.executable, "-m", "latch.auth", "issue-idp-token",
            "--provider", "google", "--subject", "demo",
        ],
        capture_output=True, text=True, check=True,
    )
    token = proc.stdout.strip()
    provider, subject = await _verifier().verify(
        provider="google", idp_token=token, clock=FakeClock(NOW)
    )
    assert (provider, subject) == ("google", "demo")
```

(注: CLI発行時の `iat` はSystemClockの現在時刻。検証側のFakeClock(NOW=2026-09-27)が「未来」側になるためexp判定は問題なく通る — `exp <= clock.now()` が偽であるため。もし日付がずれて失敗する場合は `--iat` を指定する設計になっている)

- [ ] **Step 2: テストが失敗することを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_tools.py -v`
Expected: FAIL(`ModuleNotFoundError: No module named 'latch.auth.tools'`)

- [ ] **Step 3: tools.py と __main__.py を実装**

`backend/src/latch/auth/tools.py`:

```python
"""テスト用認証ツール(design §2.5-4。10 第1節の「テストユーザーのJWTを発行する内部ツール」)。

python -m latch.auth のCLIサブコマンド:
  issue-idp-token — テストユーザーのIdPトークン(JWT)を発行(staging鍵ペアで署名)
  gen-keypair     — staging専用のRS256鍵ペア(秘密鍵PEM+JWKS JSON)を生成

発行時刻はClock(SystemClock)経由。--iat で上書き可(arch test準拠 —
本モジュールも backend/src 内のため実時間の直接参照は書かない)。
アクセストークンの直接発行サブコマンドは持たない — 常により本物の経路
(POST /v1/auth/token)を残す(design §2.5-4)。
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from latch.auth.testkeys import DEFAULT_KID, load_private_key
from latch.core.clock import SystemClock
from latch.settings import Settings


def _b64u(value: int) -> str:
    size = (value.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(value.to_bytes(size, "big")).rstrip(b"=").decode()


def issue_idp_token(
    *,
    provider: str,
    subject: str,
    expires_in: int = 3600,
    private_key_pem: bytes | None = None,
    issuer: str | None = None,
    audience: str | None = None,
    iat: int | None = None,
) -> str:
    """IdPトークンを発行する(issuer/audience既定=Settings と同一=API側検証設定と自動一致)。"""
    settings = Settings()
    issuer = issuer if issuer is not None else getattr(settings, f"auth_idp_issuer_{provider}")
    audience = (
        audience if audience is not None else getattr(settings, f"auth_idp_audience_{provider}")
    )
    now = SystemClock().now()
    issued_at = iat if iat is not None else int(now.timestamp())
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "iat": issued_at,
        "exp": issued_at + expires_in,
    }
    if private_key_pem is None:
        key = load_private_key()
    else:
        key = serialization.load_pem_private_key(private_key_pem, password=None)
    return pyjwt.encode(payload, key, algorithm="RS256", headers={"kid": DEFAULT_KID})


def generate_keypair(*, out_dir: Path) -> None:
    """staging専用のRS256鍵ペア(秘密鍵PEM+JWKS JSON・kid=DEFAULT_KID)を書き出す。

    鍵ローテーション時の差し替えにも使用する(design §1.4)。
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.joinpath("idp_test_private.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    numbers = key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": DEFAULT_KID,
        "use": "sig",
        "alg": "RS256",
        "n": _b64u(numbers.n),
        "e": _b64u(numbers.e),
    }
    out_dir.joinpath("idp_test_jwks.json").write_text(
        json.dumps({"keys": [jwk]}, indent=2) + "\n"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m latch.auth", description="LATCHテスト用認証ツール(10 第1節)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    issue = sub.add_parser("issue-idp-token", help="テストユーザーのIdPトークン(JWT)を発行")
    issue.add_argument("--provider", required=True, choices=["google", "apple"])
    issue.add_argument("--subject", required=True)
    issue.add_argument("--expires-in", type=int, default=3600)
    issue.add_argument("--private-key", default=None, help="秘密鍵PEMパス(既定=同梱テスト鍵)")
    issue.add_argument("--issuer", default=None, help="既定=Settings の provider別issuer")
    issue.add_argument("--audience", default=None, help="既定=Settings の provider別audience")
    issue.add_argument("--iat", type=int, default=None, help="発行unix秒(既定=現在時刻)")

    gen = sub.add_parser("gen-keypair", help="RS256鍵ペア(秘密鍵PEM+JWKS JSON)を生成")
    gen.add_argument("--out-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.command == "issue-idp-token":
        pem = Path(args.private_key).read_bytes() if args.private_key else None
        token = issue_idp_token(
            provider=args.provider,
            subject=args.subject,
            expires_in=args.expires_in,
            private_key_pem=pem,
            issuer=args.issuer,
            audience=args.audience,
            iat=args.iat,
        )
        print(token)
        return 0
    if args.command == "gen-keypair":
        generate_keypair(out_dir=Path(args.out_dir))
        return 0
    return 1
```

`backend/src/latch/auth/__main__.py`:

```python
"""CLI入口: python -m latch.auth → tools.main()"""

import sys

from latch.auth.tools import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: テストが通ることを確認**

Run: `cd backend && uv run pytest tests/unit/auth/test_tools.py -v`
Expected: PASS(8件)

- [ ] **Step 5: `make lint && make test` を確認してCommit**

```bash
make lint && make test
git add backend/src/latch/auth/tools.py backend/src/latch/auth/__main__.py backend/tests/unit/auth/test_tools.py
git commit -m "feat: 認証CLI(issue-idp-token・gen-keypair。10 第1節テスト用認証構成)"
```

---

### Task 11: 公開IFの再export + integration試験 + README + 受渡し検証と報告

**Files:**
- Modify: `backend/src/latch/auth/__init__.py`(再export)、`backend/tests/integration/conftest.py`(fixture追記)、`backend/README.md`(認証ツール手順)
- Create: `backend/tests/integration/test_auth_api.py`(作成のみ・実行しない)、`docs/plans/M0/ws-3-report.md`(§7の形式)

**Interfaces:**
- Consumes: 全タスク
- Produces: `latch.auth` 公開API(M1以降はこれ越しに認証を利用する)。G0「認証3エンドポイントとJWKS検証経路がci環境でグリーン」の試験コード(実行はスーパーバイザー)。完了条件7項目の証拠と報告

- [ ] **Step 1: `auth/__init__.py` を再exportへ書き換える**

`backend/src/latch/auth/__init__.py` の内容を丸ごと置き換える:

```python
"""認証(M0 ws-3)。token/refresh/logout・Redis失効リスト・JWKS検証・テスト用認証構成。

M1以降の呼び出し側はこのパッケージ越しにAuthService・require_authenticated・
ルータを利用する(design §3.1)。
"""

from latch.auth.deps import get_auth_service, require_authenticated
from latch.auth.errors import (
    AuthError,
    DependencyUnavailableError,
    InvalidIdpTokenError,
    InvalidRefreshTokenError,
    UnauthenticatedError,
)
from latch.auth.idp import IdPVerifyConfig, IdPVerifier
from latch.auth.routes import logout_router, public_router
from latch.auth.service import (
    AuthService,
    RefreshResult,
    TokenResult,
    build_auth_service,
    make_user_lookup,
)
from latch.auth.sessions import RefreshIssued, RotationResult, SessionStore
from latch.auth.tokens import (
    ACCESS_TTL_S,
    REFRESH_TTL_S,
    AccessTokenClaims,
    issue_access_token,
    verify_access_token,
)

__all__ = [
    "ACCESS_TTL_S",
    "AuthError",
    "AuthService",
    "AccessTokenClaims",
    "DependencyUnavailableError",
    "IdPVerifyConfig",
    "IdPVerifier",
    "InvalidIdpTokenError",
    "InvalidRefreshTokenError",
    "REFRESH_TTL_S",
    "RefreshIssued",
    "RefreshResult",
    "RotationResult",
    "SessionStore",
    "TokenResult",
    "UnauthenticatedError",
    "build_auth_service",
    "get_auth_service",
    "issue_access_token",
    "logout_router",
    "make_user_lookup",
    "public_router",
    "require_authenticated",
    "verify_access_token",
]
```

- [ ] **Step 2: integration/conftest.py へ fixtureを追記**

`backend/tests/integration/conftest.py` — docstringと既存fixture(migrated_db・db_engine)は温存し、**import2行とfixture2件の追記のみ**行う。追記後の全体(★が追記箇所):

```python
"""integration試験の共通フィクスチャ(design §3.1・§4.1)。

compose常設DBに対し、sessionスコープで alembic upgrade head(冪等)してから
functionスコープの AsyncEngine を提供する。エンジンは試験ごとに生成・破棄する
(イベントループをまたぐ接続再利用を避ける)。
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine

import redis.asyncio as redis_async  # ★(ws-3)
from httpx import AsyncClient  # ★(ws-3)

from latch.core.db import create_db_engine
from latch.settings import Settings

BACKEND_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def migrated_db() -> None:
    """DBをheadまでマイグレーションする(冪等・sessionで1回)。"""
    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")


@pytest.fixture
async def db_engine(migrated_db) -> AsyncEngine:
    engine = create_db_engine(Settings())
    yield engine
    await engine.dispose()


@pytest.fixture  # ★(ws-3)
async def redis_client():
    """compose常設Redis(127.0.0.1:6379)へのクライアント(design §4.2-7)。"""
    client = redis_async.Redis.from_url(Settings().redis_url, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture  # ★(ws-3)
async def api_client():
    """compose常設api(127.0.0.1:8000)へのHTTPクライアント(G0の実HTTP証拠)。"""
    async with AsyncClient(base_url="http://127.0.0.1:8000") as c:
        yield c
```

(★のコメントは書かなくてよい — 追記位置の示唆。最終的に `cd backend && uv run ruff check .` が通るimport順へ並べ替える)

- [ ] **Step 3: integration試験ファイルを作成(作成のみ・実行しない)**

`backend/tests/integration/test_auth_api.py`:

```python
"""認証3エンドポイント+JWKS検証経路のci環境実証(design §4.2。G0の主要証拠)。

実HTTP(compose api=127.0.0.1:8000)・実Redis・実DB。実行は make test-ci
(ws-4並走中は作成のみ — 計画書§0のSTATUS運用ルール。スーパーバイザーが検証時に実行)。
subjectは実行ごとにユニークな値(uuid接尾辞)を用い、User行は試験内でDELETE
(共有ci-dbの汚染回避)。Redis鍵はTTL付きで自動消滅するため追加掃除はしない。
"""

import asyncio
import hashlib
import sys
import uuid as uuid_mod
from datetime import UTC, date, datetime, timedelta

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from latch.auth.service import build_auth_service
from latch.auth.tools import generate_keypair, issue_idp_token
from latch.core.clock import FakeClock
from latch.main import create_app
from latch.settings import Settings

pytestmark = pytest.mark.integration


def _unique_subject(prefix: str) -> str:
    return f"ws3-{prefix}-{uuid_mod.uuid4().hex[:12]}"


async def _cli_idp_token(provider: str, subject: str) -> str:
    """内部ツール(python -m latch.auth)でテストユーザーJWTを発行(10 第1節)。"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "latch.auth", "issue-idp-token",
        "--provider", provider, "--subject", subject,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    assert proc.returncode == 0, stderr.decode()
    return stdout.decode().strip()


def _decode_unverified(token: str) -> dict:
    return pyjwt.decode(token, options={"verify_signature": False})


async def _exchange(api_client, provider: str, subject: str) -> dict:
    idp_token = await _cli_idp_token(provider, subject)
    resp = await api_client.post(
        "/v1/auth/token", json={"provider": provider, "idp_token": idp_token}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_1_token_exchange_via_cli_for_both_providers(api_client):
    """§4.2-1: ツール発行IdPトークン→200・Bearer・3600・user=null/false"""
    for provider in ("google", "apple"):
        body = await _exchange(api_client, provider, _unique_subject(provider))
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] == 3600
        assert body["refresh_token"]
        assert body["user"] == {"id": None, "profile_complete": False}


async def test_2_token_with_inserted_user_row(api_client, db_engine):
    """§4.2-2: User行INSERT→user.id=<uuid>・profile_complete=true"""
    provider, subject = "google", _unique_subject("u")
    ts = datetime(2026, 9, 27, 0, 0, 0, tzinfo=UTC)  # 固定リテラル時刻(ws-1試験規約)
    async with db_engine.begin() as conn:
        user_id = await conn.scalar(
            text("""
                INSERT INTO users
                    (display_name, birth_date, auth_provider, auth_subject,
                     created_at, updated_at)
                VALUES (:dn, :bd, :p, :s, :ts, :ts)
                RETURNING id
            """),
            {"dn": "ws3試験ユーザー", "bd": date(2000, 1, 1), "p": provider, "s": subject, "ts": ts},
        )
    try:
        body = await _exchange(api_client, provider, subject)
        assert body["user"]["id"] == str(user_id)
        assert body["user"]["profile_complete"] is True
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})


async def test_3_wrong_key_idp_token_rejected(api_client, tmp_path):
    """§4.2-3: gen-keypairの別鍵で署名→401 INVALID_IDP_TOKEN(envelope)"""
    generate_keypair(out_dir=tmp_path)
    token = issue_idp_token(
        provider="google",
        subject=_unique_subject("k"),
        private_key_pem=(tmp_path / "idp_test_private.pem").read_bytes(),
    )
    resp = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": token}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_IDP_TOKEN"


async def test_4_refresh_rotation_and_reuse_revokes_family(api_client):
    """§4.2-4: 旧refresh再利用→401・回転後の新refreshも同一族なので401"""
    body = await _exchange(api_client, "google", _unique_subject("r"))
    old_refresh = body["refresh_token"]
    rotated = await api_client.post(
        "/v1/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert rotated.status_code == 200, rotated.text
    new_refresh = rotated.json()["refresh_token"]
    reuse = await api_client.post(
        "/v1/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"
    after = await api_client.post(
        "/v1/auth/refresh", json={"refresh_token": new_refresh}
    )
    assert after.status_code == 401


async def test_5_logout_revokes_access_and_family(api_client):
    """§4.2-5: 204→同JWT再利用401→当族refresh 401"""
    body = await _exchange(api_client, "google", _unique_subject("l"))
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    resp = await api_client.post("/v1/auth/logout", headers=headers)
    assert resp.status_code == 204
    again = await api_client.post("/v1/auth/logout", headers=headers)
    assert again.status_code == 401
    assert again.json()["error"]["code"] == "UNAUTHENTICATED"
    rotated = await api_client.post(
        "/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
    )
    assert rotated.status_code == 401


async def test_6_unknown_refresh_token_rejected(api_client):
    """§4.2-6: 不正なrefresh_token文字列→401"""
    resp = await api_client.post(
        "/v1/auth/refresh", json={"refresh_token": "unknown-token-value"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_REFRESH_TOKEN"


async def test_7_redis_keys_directly(api_client, redis_client):
    """§4.2-7: revoked鍵の存在とTTL上限(≤3600s)・族鍵・rt鍵の削除"""
    body = await _exchange(api_client, "google", _unique_subject("rv"))
    access, refresh = body["access_token"], body["refresh_token"]
    claims = _decode_unverified(access)
    jti, sid = claims["jti"], claims["sid"]
    assert not await redis_client.exists(f"auth:revoked:{jti}")
    resp = await api_client.post(
        "/v1/auth/logout", headers={"Authorization": f"Bearer {access}"}
    )
    assert resp.status_code == 204
    assert await redis_client.exists(f"auth:revoked:{jti}")
    ttl = await redis_client.ttl(f"auth:revoked:{jti}")
    assert 0 < ttl <= 3600  # TTL上限=残り有効期限
    assert not await redis_client.exists(f"auth:family:{sid}")  # 族索引削除
    sha = hashlib.sha256(refresh.encode()).hexdigest()
    assert not await redis_client.exists(f"auth:rt:{sha}")  # 族メンバーのrt鍵削除


async def test_8_expired_access_token_asgi_real_redis(redis_client):
    """design §4.2後段: ASGI+実Redis+FakeClockで期限切れを再現(compose api実プロセスはClock固定のため)"""
    now = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now)

    async def _lookup(provider: str, subject: str):
        return None

    settings = Settings(app_env="ci")
    svc = build_auth_service(
        clock=clock, settings=settings, redis_client=redis_client, user_lookup=_lookup
    )
    app = create_app(clock=clock, settings=settings, auth_service=svc)
    idp_token = issue_idp_token(provider="google", subject="ws3-exp", iat=int(now.timestamp()))
    result = await svc.token(provider="google", idp_token=idp_token)
    headers = {"Authorization": f"Bearer {result.access_token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.post("/v1/auth/logout", headers=headers)
        assert ok.status_code == 204  # 期限内は通る(実Redisの失効照会経路も踏む)
    clock.advance(timedelta(seconds=3601))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        expired = await client.post("/v1/auth/logout", headers=headers)
        assert expired.status_code == 401  # FakeClock.advanceで期限切れ再現
```

- [ ] **Step 4: 収集確認(unit実行でintegrationファイルのimportが通る)**

```bash
make test
cd backend && uv run pytest --collect-only tests/integration/test_auth_api.py -q
```

Expected: `make test` がグリーン(収集エラーなし)。collect-only は8件の試験を表示(`-m "not integration"` でdeselectされるが収集はされる)

- [ ] **Step 5: README へ認証手順を追記**

`backend/README.md` の末尾へ追記:

```markdown

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
  生成された `idp_test_jwks.json` を `LATCH_AUTH_IDP_JWKS_URL_GOOGLE` / `_APPLE`(file:// URL可)へ設定し、
  発行時は `--private-key <dir>/idp_test_private.pem` で指定
- prodは `LATCH_AUTH_ACCESS_SECRET`(HS256 secret)・`LATCH_AUTH_IDP_JWKS_URL_*`・
  `LATCH_AUTH_IDP_ISSUER_*`・`LATCH_AUTH_IDP_AUDIENCE_*` の実値が必須(未設定・テスト値は起動失敗)
- アクセストークンを直接発行するツールは存在しない — 常に POST /v1/auth/token 経由で取得する
```

- [ ] **Step 6: 完了条件1・2(unit側)— lint / test / 収集**

```bash
make lint
make test
cd backend && uv run pytest tests/unit/auth -v
uv run pytest --collect-only tests/integration/test_auth_api.py -q
```

Expected: 3コマンドともexit 0。pytestサマリー行(unit/auth は10ファイル)を報告書へ記録

- [ ] **Step 7: 完了条件3 — 実時間参照の所在**

```bash
rg -n 'datetime\.now|utcnow|time\.time|time\.monotonic' backend/src
cd backend && uv run pytest tests/unit/test_arch_no_direct_time.py -v
```

Expected: rgのヒットが `backend/src/latch/core/clock.py` の行のみ。arch test はexit 0(auth/・tools.py が自動スキャン対象に含まれる)

- [ ] **Step 8: 完了条件4・5・6・7 — 差分の所在とCLI**

```bash
git diff main -- backend/pyproject.toml
git diff --stat main -- backend/uv.lock
git diff --stat main -- backend/alembic
git diff --name-only main | sort
git diff main -- backend/src/latch/settings.py
git status --short
cd backend && uv run python -m latch.auth issue-idp-token --provider google --subject demo
```

Expected: pyprojectの差分は依存3行。uv.lockは依存解決差分のみ。alembicは出力なし。name-onlyは§4の一覧と完全一致。settings.pyは追記のみ。statusは空。CLIはJWT文字列を出力(exit 0)

- [ ] **Step 9: ruff format を通す**

```bash
cd backend && uv run ruff format . && uv run ruff check .
git status --short
```

変更があった場合のみ:

```bash
git add -u && git commit -m "style: ruff format 適用"
```

- [ ] **Step 10: 報告ファイルを作成してコミット**

`docs/plans/M0/ws-3-report.md` を §7 の形式で作成する。Step 6〜8の出力要点を貼る。項目2は「**test-ci=スーパーバイザー検証待ち**」(§0のSTATUS運用ルール)と記録する。固定値の変更有無(design §6の5論点+本計画§8のIF確定事項)を確認し記録する。

```bash
git add docs/plans/M0/ws-3-report.md
git commit -m "docs: M0 ws-3の実行報告(完了条件7項目の証拠・test-ciは検証待ち)"
```

- [ ] **Step 11: 最終返信**

報告ファイルのパスと完了条件7項目の結果一覧を返信する(項目2は「test-ci=スーパーバイザー検証待ち」)。FAILが1つでもあれば、それも隠さず返信する。

---

## 8. design IF案からの確定事項(本計画が固定した実装詳細。変更時は報告必須)

design.md §3.1のインターフェース案は「実装詳細は計画書・TDDで確定」とされており、本計画は次のとおり確定した:

1. **`tokens.py` の発行/検証は同期関数**(design案はasync表記。PyJWTは同期APIでI/Oを含まないため)
2. **`auth:rt:{sha}` はHASH型でなく SET+JSON文字列**(design §2.3はHSET表記だが、GETDELは文字列型専用のため原子性の要件と両立しない。値は `{"provider","subject","family"}` のJSON)
3. **`AuthService` は `settings` ではなく `secret: str` を直受取**(設定解決とprodガードは `build_auth_service` に閉じる。テスト容易性と依存最小化)
4. **`require_authenticated` は `AuthContext` でなく `AccessTokenClaims` を返す**(design §2.3のAuthContext(provider, subject, jti, sid)が運ぶ情報をclaimsがそのまま持つため新型を作らない)
5. **`AuthService.authenticate` を追加**(JWT検証+失効リスト照会。design §2.3の「認証Dependency」のフローをAuthService内に置き、depsは薄くする)
6. **redis client・db engine の生成と解体はlifespan(main.py)の責務**(`build_auth_service` は引数で受けて純構築のみ)
7. **logoutルータの保護はエンドポイント引数の `Depends(require_authenticated)` で表現**(design §2.7の「ルータ依存」`dependencies=[...]` と等価の強制で、claims取得を兼ねる)
8. **ログはロガー `latch.auth`・`auth.token ok` 等のイベント名+結果、`auth.error code=<CODE>` のみ**(成功3種・エラー1種。トークン・subject・claimは出さない)

## 実行後のセルフレビュー(実装者がTask 11のStep 6に入る前に一度だけ読む)

- design.md §5の7項目がすべて§6(完了条件)に検証コマンドつきで対応しているか(項目2の実行主体はスーパーバイザー)
- design.md §4.1の9項目がすべて何れかのタスクの試験に対応しているか(1=tokens・2=sessions・3=service_token・4=service_refresh・5=routes・6=deps・7=tools・8=prod_guard/auth_settings・9=arch test)
- `issue_access_token(*, clock, secret, provider, subject, sid)`・`verify_access_token(*, clock, secret, token)`・`AccessTokenClaims(auth_provider, auth_subject, jti, sid, iat, exp)`・`IdPVerifier(configs).verify(*, provider, idp_token, clock)`・`SessionStore(redis)` の5メソッド・`AuthService(clock, secret, sessions, idp, user_lookup)` の4メソッド・`build_auth_service(*, clock, settings, redis_client, user_lookup)`・`require_authenticated`・`main(argv)` の各名前・引数がタスク間・テスト間で一致しているか
- 製品コードに `datetime.now` / `time.sleep` / `from time import` が入っていないか(arch testが自動検出するが、入れた瞬間にレッドになることを自覚しておく。`tools.py` の発行時刻はSystemClock経由)
- `make test-ci` / `make migrate` / `docker compose` を実行していないか(§0。compose.yamlの編集はしたが適用はしていない)
- `backend/alembic/`・`core/`・`worker/`・`llm/`・`Makefile`・`tests/conftest.py`・既存テストが無変更か
- 依存追加が3本のみか。`settings.py` が追記のみか
- 実装中にdesign.md §6の5論点または§8の確定事項を変えた箇所があれば報告ファイルに書いたか

