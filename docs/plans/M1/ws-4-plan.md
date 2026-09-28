# M1 ws-4(レート制限)実装計画書

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 08 §5.4の上限4種 — Active Intent 5件(422 ACTIVE_INTENT_LIMIT)・Intent作成20件/日(429)・Intent更新6回/時(429)・API全体60req/分(429)— をRedis INCR+JST日付キーとDB計上でAPI Layerに強制する。あわせて05 §5が契約上明示するauth/token・refreshの429(provider+subject単位・60req/分)も実装する。

**Architecture:** 新規 `ratelimit/` パッケージ(横断関心事。auth/と対称な「StoreにRedis操作を閉じ込め・limiterが判断する」構成)。API全体は依存関数 `api_rate_limited`(require_authenticated を内包)、作成/更新/resume/authはサービスへlimiterをOptional注入(既定None=無効・既存unit試験は無修正)。Active数のみDB COUNT+users行FOR UPDATEで直列化し、422 ACTIVE_INTENT_LIMITはintents/errors.pyへIntentsError派生として追加。カウンタは固定窓バケットキー(リセット=キー切替。TTLは掃除用のみ・リセット表現に使わない)。

**Tech Stack:** Python 3.13 / FastAPI / redis.asyncio(pipeline INCR+EXPIRE)/ SQLAlchemy text() / pytest + fakeredis(dev依存済み)

**Spec:** docs/plans/M1/ws-4-design.md(本計画はdesign §2〜§4をタスク分解する。design §5の告白5件 — resume計上・期限切れactive除外・provider+subject単位・INCR先行・固定窓許容 — はスーパーバイザー承認済みであり、本計画はそれを前提に組み立てる)

---

## 0. 作業規律(worktree・コミット・共有資産)

1. **worktree**: 実装はスーパーバイザーが用意したgit worktree内で行う。なければ `superpowers:using-git-worktrees` スキルに従って作成する(ブランチ名 `ws-4-ratelimit`)。mainには直接触らない。
2. **コミット規律(必須)**: タスク単位でworktreeブランチへコミットする。各Taskの最終ステップにコミットコマンドを用意してあるので必ず実行すること(1タスク=1コミットを基本とする)。mainへのマージ・pushはスーパーバイザーが行う(実装者は行わない)。
3. **共有ci-db**: 本単位はマイグレーションを追加しないが、共有ci-dbはworktree間で取り合う状態にある(STATUS運用ルール1〜3)。開発はunit試験(`make lint` / `make test`)で進め、**`make test-ci` と `make migrate` は実行しない**。報告には「test-ci=スーパーバイザー検証待ち」と記録する。
4. **apiイメージ**: `make test-ci`(compose up)はapiイメージを再ビルドしない(STATUS運用ルール4)。実HTTPによる検証はスーパーバイザーが `docker compose build api` を先行させて実行する。
5. **テストファイル名**: backend/tests配下全体でbasenameが一意であること(STATUS運用ルール5)。本計画の新規ファイル(`test_store.py`・`test_limiter.py`・`test_jst_boundary.py`・`test_rate_limit_wiring.py`・`test_ratelimit_api.py`)はいずれも既存と重複なし。Task 6のコミット前に `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空であることを確認する。
6. **時刻参照**: 製品コードで実時間を直接参照することは禁止(arch test `test_arch_no_direct_time.py` が毎コミットで強制)。バケット文字列はすべて `clock.now().astimezone(JST)` と `clock.jst_date()` から導出する(FakeClockで境界試験が決定的に再現できる — design確定値8)。
7. **ログ**: 429/422のログにはcodeと固定文言のみを出し、text・subject・トークン等のユーザー由来の内容を含めない(08 §2.4許可リスト方式)。

## 1. 参照仕様節

| # | 確定値 | 出典 |
|---|---|---|
| 1 | 上限4種: Active 5件/ユーザー(422)・作成20件/日/ユーザー(429)・更新6回/時/Intent(429)・API全体60req/分/ユーザー(429) | 08 §5.4 |
| 2 | カウンタはRedisで持ちAPI Layerで強制。通過後のJev消費抑制は06 v0.3 §5のJev予算が担う(二層の保護) | 08 §5.4・04 §5 |
| 3 | error code: 422 ACTIVE_INTENT_LIMIT(適用=Intents作成)/ 429 RATE_LIMITED(適用=全API) | 05 §5エラー形式表 |
| 4 | POST /v1/auth/token・refreshのerrorsに429 RATE_LIMITEDが列挙(認証不要2エンドポイントにも適用) | 05 §5認証・ユーザー系 |
| 5 | 認証不要はtoken/refreshのみ。JWT claimはprovider+subjectでUser行と紐付く | 05 §5冒頭・C3 |
| 6 | Redisは1インスタンスで多用途(レート制限カウンタが追加される)。接頭辞で名前空間分離 | 04 §3 |
| 7 | 時刻参照はすべてClock経由(FR-41)。適用範囲に「RedisカウンタのJST日付キー」を含む | 04 §5 |
| 8 | TTL方式はUTC基準となりJST 0時と9時間ずれるため用いない。リセット=バケットキー切替 | 04 §5・12 M1-6 |
| 9 | resumeはversion+1の再評価Event(update種)を発行(更新カウントの対象とする根拠) | 05 §6・06 §9 |
| 10 | expiry_sweeperはM3。期限切れ行のstatusは残留するが遷移表上expired相当(Active計上から除外) | 05 §6・12 M3-3 |
| 11 | DB・Redis等の依存障害は503 DEPENDENCY_UNAVAILABLE(全API) | 05 §5エラー形式表 |
| 12 | Intent遷移表: active→paused・paused→active(resume)・draft→active | 05 §6 |
| 13 | /health は運用プローブ用でv1 API契約の外(C3対象外) | M0設計 |
| 14 | Intent保存 p95 500ms(レート制限チェックの追加はRedis INCR 1〜2本) | 04 §7 |

## 2. スコープ(作成・変更するファイル一覧)

### 2.1 新規作成

```
backend/src/latch/ratelimit/
  __init__.py     # make_rate_limiter ファクトリ・export(Task 2)
  errors.py       # RateLimitError基底・RateLimitedError(429)・RateLimitDependencyError(503)
  store.py        # RateLimitStore(Redis INCR/EXPIRE・rl:キー組立を閉じ込める)
  limiter.py      # RateLimiter・RateLimits(上限判定・clockからバケット導出)
  deps.py         # api_rate_limited(require_authenticatedを内包する依存関数)
backend/tests/unit/ratelimit/
  test_store.py         # fakeredisでINCR積算・TTL・キー形式・subjectハッシュ化
  test_limiter.py       # 上限境界・429後もカウント進行・Redis例外→503・上限差し替え
  test_jst_boundary.py  # FakeClockでJST 0時/時/分バケット切替(リセット再現)
backend/tests/unit/
  test_rate_limit_wiring.py  # 全v1ルートの依存差し替え網羅・対象外3経路の確認
backend/tests/integration/
  test_ratelimit_api.py      # 実api・実Redis・実DBでの429/422のE2E(design §4.2)
```

### 2.2 変更(既存ファイル)

| ファイル | 変更内容 |
|---|---|
| `backend/src/latch/settings.py` | 上限4種のフィールド追加(既定=仕様値。LATCH_環境変数で上書き可) |
| `backend/src/latch/main.py` | lifespanでredis_client常時生成・app.state.rate_limiter/user_lookup構築・RateLimitErrorハンドラ追加・create_appへrate_limiter引数 |
| `backend/src/latch/intents/errors.py` | ActiveIntentLimitError(422 ACTIVE_INTENT_LIMIT)追加 |
| `backend/src/latch/intents/store.py` | count_active(COUNT・期限切れ行除外)・lock_user_row(users行FOR UPDATE)追加 |
| `backend/src/latch/intents/service.py` | limiter注入(Optional)・create/update/resumeの3箇所へフック(§2.4の順序) |
| `backend/src/latch/intents/__init__.py` | ActiveIntentLimitErrorをexportへ追加 |
| `backend/src/latch/intents/routes.py` | parse_router・intents_crud_routerの依存をapi_rate_limitedへ差し替え |
| `backend/src/latch/users/routes.py` | users_routerの依存をapi_rate_limitedへ差し替え |
| `backend/src/latch/auth/routes.py` | logout_routerの依存をapi_rate_limitedへ差し替え |
| `backend/src/latch/auth/service.py` | limiter注入(Optional)・_token/_refreshへIdP検証後のフック・build_auth_serviceへlimiter引数 |
| `backend/tests/unit/intents/test_intents_service.py` | StubStoreへlock_user_row/count_active追加・StubLimiter導入・レート制限試験追記 |
| `backend/tests/unit/auth/test_service_token.py` | limiterフック試験を追記(_serviceヘルパーへlimiter引数) |
| `backend/tests/unit/auth/test_service_refresh.py` | limiterフック試験を追記 |

## 3. 禁止(触れてはいけないもの・スコープ外の判断基準)

**触れてはいけないファイル**(design §3.3):

- `backend/src/latch/core/`(clock.py・db.py・deps.py — JST・Clockはimportのみ)
- `backend/src/latch/users/service.py`・`backend/src/latch/geo/`・`backend/src/latch/llm/`・`backend/src/latch/worker/`
- `backend/alembic/`(マイグレーション追加なし。スキーマ変更なし)
- `compose.yaml`・`Makefile`・`docker/`(上限の上書き口はLATCH_環境変数で既存パターンどおり・compose変更不要)
- `prototype/`・`docs/`(仕様書群・docs/learn/はagent4管轄)
- `backend/src/latch/intents/`のparse系資産(schema.py・completion.py・prompt.py・mapping.py・events.py・IntentParseService)
- `backend/src/latch/auth/`のtokens.py・idp.py・sessions.py・deps.py・tools.py・testkeys/
- 既存テストファイルのうち上記2.2に挙げた3つ以外のテストコード(修正禁止・無修正で緑であることを確認するだけ)

**スコープ外と判断する基準**(design §1.4。以下を見つけても作らない・報告に記録のみ):

- Jev予算(1Intent日次40回・1ユーザー日次120回・頻度制限30分)・D-16回数上限・リセットジョブ — M2-9
- ブロック・通報 — M3
- expiry_sweeper(期限切れIntentのexpired遷移)— M3-3。本単位は期限切れ残留行を**計上から除外するだけ**で遷移させない
- Retry-Afterヘッダ等のクライアント再試行契約 — 05 §5に規定なし
- フロントエンドの429/422表示 — ws-5
- sliding window化・リセットジョブ導入 — 固定窓の境界2倍は承認済み(告白5)

## 4. Global Constraints

- 上限の既定値は `rate_limit_api_per_min=60`・`rate_limit_create_per_day=20`・`rate_limit_update_per_hour=6`・`rate_limit_active_intents=5`(08 §5.4の字義。Settingsフィールド・LATCH_プレフィックス環境変数で上書き可)
- Redisキーはdesign §2.7どおり `rl:api:{user_key}:{yyyymmddHHMM}`(TTL 120秒)/ `rl:create:{user_id}:{yyyymmdd}`(TTL 48時間)/ `rl:update:{intent_id}:{yyyymmddHH}`(TTL 13時間)/ `rl:auth:{provider}:{sha256(subject)}:{yyyymmddHHMM}`(TTL 120秒)。yyyymmdd/HH/MMはJST暦
- 接頭辞は `rl:`(auth:と名前空間分離)。INCRとEXPIREはpipelineで毎回併発。INCR後の値で判定する(INCR先行 — 429を返したリクエストもカウント済み)
- 検証順序は 401(認証)→ 429(レート制限)→ 404/403(リソース特定)→ 422(ドメイン検証)。422系では ACTIVE_INTENT_LIMIT が必須3検証より先(design §2.4・§4.1)
- 更新6回/時の対象: PATCH全分岐(内容更新・draft再保存・draft→active化)+resume。pause・deleteは対象外
- Redis断絶は fail-closed(503 DEPENDENCY_UNAVAILABLE)。fail-openにしない(design §2.6)
- authの429単位はprovider+subject(IdP検証で確定。User行の有無によらない)。subjectはsha256でハッシュ化してキーへ埋める
- Active数計上SQL: `status='active' AND (expires_at IS NULL OR expires_at > :now)`(期限切れ残留行の除外)
- コミット毎に `make lint` と `make test` が緑であること

## 5. Review Focus(specが暗示するが各タスクの試験だけでは拾い切れない入力クラス)

1. **JST境界での窓リセット**(23:59JSTに20件作り切ったユーザーが00:00JSTにまた作れる/分窓・時窓も同様)— Task 2のtest_jst_boundary.pyが全ケースをピン留め
2. **未登録JWT保持者(初回登録待ち)の連打**(user_idが存在しないままparse等を打ち続ける)— Task 4のanonフォールバックキーとtest_limiter.pyの分離試験で担保
3. **Redis断絶時に全APIが穴を開けない**(fail-closed)— Task 2のBrokenRedis試験(503ラップ)
4. **429後の連打がすべて429・窓が進めば自動回復**(INCR先行の実効性)— Task 2 test_limiter.py・test_jst_boundary.py
5. **期限切れactive残留行がActive枠を占有し続けない**(M3までの過渡期)— Task 6のtest_6(DB直接UPDATEで再現)
6. **単位の分離**(別ユーザー・別Intentは互いに制限されない)— Task 6のtest_2・test_3
7. **401優先**(429超過状態でも無効JWTは401・INCRされない)— Task 6のtest_3末尾・Task 5のauth unit
8. **差し替え漏れ**(将来ルータ追加時にapi_rate_limitedを付け忘れる)— Task 4のwiring試験が構成を静的に強制

---

## Task 1: ratelimitパッケージのエラー階層とRedisストア

**Files:**
- Create: `backend/src/latch/ratelimit/__init__.py`(Task 1ではdocstringのみ。exportはTask 2で追加)
- Create: `backend/src/latch/ratelimit/errors.py`
- Create: `backend/src/latch/ratelimit/store.py`
- Test: `backend/tests/unit/ratelimit/test_store.py`

**Interfaces:**
- Produces: `RateLimitError`(基底。`http_status: int`・`code: str`クラス属性)/ `RateLimitedError`(429 RATE_LIMITED)/ `RateLimitDependencyError`(503 DEPENDENCY_UNAVAILABLE)/ `RateLimitStore(redis: aioredis.Redis)` — `async incr_api(user_key: str, bucket: str) -> int`・`async incr_create(user_id: str, bucket: str) -> int`・`async incr_update(intent_id: str, bucket: str) -> int`・`async incr_auth(provider: str, subject_sha: str, bucket: str) -> int`。戻りはINCR後のカウント値。Task 2のRateLimiterが消費する

- [ ] **Step 1: 失敗するテストを書く** — `backend/tests/unit/ratelimit/test_store.py` を作成:

```python
"""RateLimitStore: INCR積算・TTL設定・rl:キー形式・subjectハッシュ化(design §2.7)。

fakeredis(redis-pyのコマンド解釈経路をそのまま実行)で決定的に検証する
(tests/unit/auth/test_sessions.py と同じ手法 — design §3.1の「スタブRedis」に相当)。
"""

import pytest
import fakeredis.aioredis

from latch.ratelimit.store import RateLimitStore


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


def _store(redis) -> RateLimitStore:
    return RateLimitStore(redis)


async def test_incr_api_accumulates_and_sets_ttl(redis):
    store = _store(redis)
    assert await store.incr_api("u-1", "202609282159") == 1
    assert await store.incr_api("u-1", "202609282159") == 2  # 積算
    key = "rl:api:u-1:202609282159"
    assert await redis.get(key) == "2"
    assert 0 < await redis.ttl(key) <= 120  # TTL=2分(掃除用・design §2.7)


async def test_incr_create_key_uses_day_bucket_and_48h_ttl(redis):
    store = _store(redis)
    await store.incr_create(str(1), "20260928")
    key = "rl:create:1:20260928"
    assert await redis.get(key) == "1"
    assert 0 < await redis.ttl(key) <= 48 * 3600


async def test_incr_update_key_uses_hour_bucket_and_13h_ttl(redis):
    store = _store(redis)
    await store.incr_update("i-1", "2026092821")
    key = "rl:update:i-1:2026092821"
    assert await redis.get(key) == "1"
    assert 0 < await redis.ttl(key) <= 13 * 3600


async def test_incr_auth_hashes_subject_in_key(redis):
    store = _store(redis)
    subject_sha = "a" * 64  # limiterが計算したsha256(ここでは任意のhex)
    await store.incr_auth("google", subject_sha, "202609282159")
    keys = await redis.keys("rl:auth:*")
    assert keys == [f"rl:auth:google:{subject_sha}:202609282159"]
    assert 0 < await redis.ttl(keys[0]) <= 120


async def test_different_buckets_are_separate_counters(redis):
    store = _store(redis)
    await store.incr_api("u-1", "202609282159")
    assert await store.incr_api("u-1", "202609282200") == 1  # 分が進む=新キー
    assert await store.incr_api("u-2", "202609282159") == 1  # ユーザーが違う=別鍵


async def test_namespace_is_separated_from_auth_prefix(redis):
    store = _store(redis)
    await store.incr_api("u-1", "202609282159")
    assert await redis.keys("auth:*") == []  # auth:と衝突しない(04 §3)
```

- [ ] **Step 2: テストが失敗することを確認** — `cd backend && uv run pytest tests/unit/ratelimit/test_store.py -v` → FAIL(ModuleNotFoundError: latch.ratelimit)

- [ ] **Step 3: 実装する**

`backend/src/latch/ratelimit/__init__.py`:

```python
"""レート制限(M1 ws-4)。08 §5.4の上限4種をRedisカウンタとDB計上で強制する。

横断関心事のためintentsドメインの外に置く(design §2.1-A)。StoreにRedis操作を
閉じ込め・limiterが判断する(auth/のSessionStoreと対称な構成)。
"""
```

`backend/src/latch/ratelimit/errors.py`:

```python
"""レート制限例外階層(design §2.6)。

各例外は http_status と code(05 第5節のerror code)を固定で持つ。main.py の
ハンドラが共通envelopeへ変換する。例外メッセージにはユーザー由来の内容
(subject・text)を含めない(08 §2.4)— 固定文言のみ。
"""

from __future__ import annotations


class RateLimitError(Exception):
    """レート制限系エラーの基底。http_status/code を持つ(ハンドラが消費する)。"""

    http_status: int
    code: str


class RateLimitedError(RateLimitError):
    """上限超過(05 第5節。適用範囲=全API)。"""

    http_status = 429
    code = "RATE_LIMITED"


class RateLimitDependencyError(RateLimitError):
    """Redis接続障害(fail-closed — 05 第5節 DEPENDENCY_UNAVAILABLE・design §2.6)。"""

    http_status = 503
    code = "DEPENDENCY_UNAVAILABLE"
```

`backend/src/latch/ratelimit/store.py`:

```python
"""Redis上のレート制限カウンタ(design §2.7)。

鍵(接頭辞 rl: — auth: と名前空間を分ける。JevカウンタはM2で別接頭辞):
  API全体:   INCR rl:api:{user_key}:{yyyymmddHHMM}   TTL 120秒
  作成:      INCR rl:create:{user_id}:{yyyymmdd}      TTL 48時間
  更新:      INCR rl:update:{intent_id}:{yyyymmddHH}  TTL 13時間
  auth系:    INCR rl:auth:{provider}:{sha256(subject)}:{yyyymmddHHMM}  TTL 120秒

INCRとEXPIREはpipelineで毎回併発する。バケットキーは時間の進行とともに新キーへ
切替わるため(=リセット)、EXPIREの毎回上書きは無害。TTLは掃除用に留め、リセット
表現には使わない(04 §5 — TTL方式はUTC基準になりJST 0時とずれるため不採用)。
バケット文字列(yyyymmddHHMM等)は呼び出し側(limiter)がClockから導出する。
"""

from __future__ import annotations

import redis.asyncio as aioredis

_TTL_API_S = 120
_TTL_AUTH_S = 120
_TTL_CREATE_S = 48 * 3600
_TTL_UPDATE_S = 13 * 3600


class RateLimitStore:
    """Redis操作を閉じ込めるStore(decode_responses=True のRedisを注入)。"""

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def _incr(self, key: str, ttl_s: int) -> int:
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl_s)
        result = await pipe.execute()
        return int(result[0])

    async def incr_api(self, user_key: str, bucket: str) -> int:
        """API全体60req/分(user_key=user_id文字列 or anon-… — design §2.7)。"""
        return await self._incr(f"rl:api:{user_key}:{bucket}", _TTL_API_S)

    async def incr_create(self, user_id: str, bucket: str) -> int:
        """Intent作成20件/日(bucket=yyyymmdd)。"""
        return await self._incr(f"rl:create:{user_id}:{bucket}", _TTL_CREATE_S)

    async def incr_update(self, intent_id: str, bucket: str) -> int:
        """Intent更新6回/時(bucket=yyyymmddHH)。"""
        return await self._incr(f"rl:update:{intent_id}:{bucket}", _TTL_UPDATE_S)

    async def incr_auth(self, provider: str, subject_sha: str, bucket: str) -> int:
        """auth/token・refresh 60req/分(subjectはsha256hex — design §2.5)。"""
        return await self._incr(
            f"rl:auth:{provider}:{subject_sha}:{bucket}", _TTL_AUTH_S
        )
```

- [ ] **Step 4: テストが通ることを確認** — `cd backend && uv run pytest tests/unit/ratelimit/test_store.py -v` → PASS(6件)

- [ ] **Step 5: lintとunit全体を確認してコミット**

```bash
make lint && make test
git add backend/src/latch/ratelimit/ backend/tests/unit/ratelimit/test_store.py
git commit -m "feat(ratelimit): Redisストアとエラー階層を追加(M1 ws-4 Task 1)"
```

---

## Task 2: RateLimiter(上限判定・JSTバケット導出)とSettings上限

**Files:**
- Modify: `backend/src/latch/settings.py`(レート制限セクションを追加)
- Create: `backend/src/latch/ratelimit/limiter.py`
- Modify: `backend/src/latch/ratelimit/__init__.py`(make_rate_limiter・export追加)
- Test: `backend/tests/unit/ratelimit/test_limiter.py`
- Test: `backend/tests/unit/ratelimit/test_jst_boundary.py`

**Interfaces:**
- Consumes: Task 1の `RateLimitStore`
- Produces: `RateLimits`(dataclass: `api_per_min=60`・`create_per_day=20`・`update_per_hour=6`・`active_intents=5`)/ `RateLimiter(store, clock, limits)` — `async check_api(*, user_key: str) -> None`・`async check_create(*, user_id: uuid.UUID) -> None`・`async check_update(*, intent_id: uuid.UUID) -> None`・`async check_auth(*, provider: str, subject: str) -> None`・プロパティ `active_limit: int`。超過時 `RateLimitedError`、Redis例外時 `RateLimitDependencyError` を送出。`make_rate_limiter(*, clock, redis_client, settings) -> RateLimiter`(Task 4のmain.pyが使用)

- [ ] **Step 1: 失敗するテストを書く** — `backend/tests/unit/ratelimit/test_limiter.py` を作成:

```python
"""RateLimiter: 上限境界・INCR先行・503ラップ・上限差し替え・種別分離(design §2.4・§2.6)。"""

import uuid
from datetime import UTC, datetime

import fakeredis.aioredis
import pytest

from latch.core.clock import FakeClock
from latch.ratelimit import RateLimiter, RateLimits
from latch.ratelimit.errors import RateLimitDependencyError, RateLimitedError
from latch.ratelimit.store import RateLimitStore

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)  # JST 2026-09-28 21:00
UID = uuid.UUID("00000000-0000-4000-8000-00000000000a")
IID = uuid.UUID("00000000-0000-4000-8000-00000000000b")


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def _limiter(redis, clock, **overrides) -> RateLimiter:
    limits = RateLimits(**overrides) if overrides else RateLimits()
    return RateLimiter(store=RateLimitStore(redis), clock=clock, limits=limits)


async def test_api_allows_60th_and_rejects_61st(redis, clock):
    limiter = _limiter(redis, clock)
    for _ in range(60):
        await limiter.check_api(user_key=str(UID))
    with pytest.raises(RateLimitedError) as ei:
        await limiter.check_api(user_key=str(UID))
    assert ei.value.http_status == 429
    assert ei.value.code == "RATE_LIMITED"


async def test_rejected_request_is_still_counted(redis, clock):
    """INCR先行(design §2.4告白4): 429を返したリクエストもカウント済み。"""
    limiter = _limiter(redis, clock, api_per_min=2)
    await limiter.check_api(user_key=str(UID))
    await limiter.check_api(user_key=str(UID))
    with pytest.raises(RateLimitedError):
        await limiter.check_api(user_key=str(UID))
    with pytest.raises(RateLimitedError):
        await limiter.check_api(user_key=str(UID))  # 連打はすべて429
    key = (await redis.keys("rl:api:*"))[0]
    assert await redis.get(key) == "4"  # 429分も加算されている


async def test_failed_create_still_consumes_count(redis, clock):
    """422で失敗する作成もカウントを消費(INCR先行 — design §2.4)。"""
    limiter = _limiter(redis, clock, create_per_day=1)
    await limiter.check_create(user_id=UID)
    with pytest.raises(RateLimitedError):
        await limiter.check_create(user_id=UID)


async def test_counter_kinds_are_separated(redis, clock):
    limiter = _limiter(redis, clock, api_per_min=1, create_per_day=1)
    await limiter.check_api(user_key=str(UID))  # api種で上限到達
    with pytest.raises(RateLimitedError):
        await limiter.check_api(user_key=str(UID))
    await limiter.check_create(user_id=UID)  # 作成種は無関係
    await limiter.check_update(intent_id=IID)  # 更新種も無関係


async def test_users_are_separated(redis, clock):
    limiter = _limiter(redis, clock, api_per_min=1)
    other = uuid.UUID("00000000-0000-4000-8000-00000000000c")
    await limiter.check_api(user_key=str(UID))
    with pytest.raises(RateLimitedError):
        await limiter.check_api(user_key=str(UID))
    await limiter.check_api(user_key=str(other))  # 別ユーザーは受理


async def test_auth_check_hashes_subject_in_key(redis, clock):
    limiter = _limiter(redis, clock)
    await limiter.check_auth(provider="google", subject="sub@example.com")
    keys = await redis.keys("rl:auth:*")
    assert len(keys) == 1
    assert "sub@example.com" not in keys[0]  # PII不混入(design §2.5)


async def test_redis_failure_is_wrapped_as_503(clock):
    """fail-closed(design §2.6): Redis断絶は503へ包む。"""

    class BrokenRedis:
        def pipeline(self):
            raise ConnectionError("redis down")

    limiter = RateLimiter(
        store=RateLimitStore(BrokenRedis()),  # type: ignore[arg-type]
        clock=clock,
        limits=RateLimits(),
    )
    with pytest.raises(RateLimitDependencyError) as ei:
        await limiter.check_api(user_key=str(UID))
    assert ei.value.http_status == 503
    assert ei.value.code == "DEPENDENCY_UNAVAILABLE"


async def test_limit_override_via_limits(redis, clock):
    limiter = _limiter(redis, clock, update_per_hour=3)
    for _ in range(3):
        await limiter.check_update(intent_id=IID)
    with pytest.raises(RateLimitedError):
        await limiter.check_update(intent_id=IID)


def test_active_limit_property(redis, clock):
    """serviceは limiter.active_limit で422判定の閾値を知る(design §2.3)。"""
    limiter = _limiter(redis, clock)
    assert limiter.active_limit == 5
    assert _limiter(redis, clock, active_intents=2).active_limit == 2
```

続けて `backend/tests/unit/ratelimit/test_jst_boundary.py` を作成:

```python
"""JSTバケット境界: 日次・時・分の切替=リセットのClock再現(design §2.2・確定値8)。

日付は clock.jst_date()、時・分は clock.now().astimezone(JST) から導出することを
FakeClockの固定時刻で検証する(UTC文字列をキーに混ぜない)。
"""

import uuid
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import pytest

from latch.core.clock import FakeClock
from latch.ratelimit import RateLimiter, RateLimits
from latch.ratelimit.store import RateLimitStore

UID = uuid.UUID("00000000-0000-4000-8000-00000000000a")


@pytest.fixture
async def redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


def _limiter(redis, clock, **overrides) -> RateLimiter:
    return RateLimiter(
        store=RateLimitStore(redis), clock=clock, limits=RateLimits(**overrides)
    )


async def test_daily_bucket_rolls_at_jst_midnight(redis):
    # JST 2026-09-28 23:59 → 00:00 で日付キーが切り替わる
    clock = FakeClock(datetime(2026, 9, 28, 14, 59, 0, tzinfo=UTC))
    limiter = _limiter(redis, clock, create_per_day=1)
    await limiter.check_create(user_id=UID)  # 当日1件目OK
    with pytest.raises(Exception):
        await limiter.check_create(user_id=UID)  # 同日2件目429
    clock.advance(timedelta(minutes=1))  # JST 0時突破
    await limiter.check_create(user_id=UID)  # 新しい日付キー→受理
    keys = sorted(await redis.keys("rl:create:*"))
    assert len(keys) == 2
    assert "20260928" in keys[0] and "20260929" in keys[1]  # JST暦日付


async def test_hourly_bucket_rolls_on_the_hour(redis):
    # JST 21:59 → 22:00 で時キーが切り替わる
    clock = FakeClock(datetime(2026, 9, 28, 12, 59, 0, tzinfo=UTC))
    limiter = _limiter(redis, clock, update_per_hour=1)
    await limiter.check_update(intent_id=UID)
    with pytest.raises(Exception):
        await limiter.check_update(intent_id=UID)
    clock.advance(timedelta(minutes=1))
    await limiter.check_update(intent_id=UID)
    keys = sorted(await redis.keys("rl:update:*"))
    assert "2026092821" in keys[0] and "2026092822" in keys[1]


async def test_minute_bucket_rolls_each_minute(redis):
    # JST 21:00:59 → 21:01:00 で分キーが切り替わる
    clock = FakeClock(datetime(2026, 9, 28, 12, 0, 59, tzinfo=UTC))
    limiter = _limiter(redis, clock, api_per_min=1)
    await limiter.check_api(user_key=str(UID))
    with pytest.raises(Exception):
        await limiter.check_api(user_key=str(UID))
    clock.advance(timedelta(seconds=1))
    await limiter.check_api(user_key=str(UID))
    keys = sorted(await redis.keys("rl:api:*"))
    assert "202609282100" in keys[0] and "202609282101" in keys[1]


async def test_date_uses_jst_calendar_not_utc(redis):
    # UTC 2026-09-28 15:00 = JST 2026-09-29 00:00(UTC日付とは異なる暦日)
    clock = FakeClock(datetime(2026, 9, 28, 15, 0, 0, tzinfo=UTC))
    limiter = _limiter(redis, clock, create_per_day=1)
    await limiter.check_create(user_id=UID)
    keys = await redis.keys("rl:create:*")
    assert "20260929" in keys[0]  # jst_date()由来(20260928でない)
```

- [ ] **Step 2: テストが失敗することを確認** — `cd backend && uv run pytest tests/unit/ratelimit/ -v` → FAIL(limiter存在なし・RateLimits import error)

- [ ] **Step 3: 実装する**

`backend/src/latch/settings.py` へ末尾(geoセクションの後)に追記:

```python
    # --- レート制限(M1 ws-4。08 §5.4)---
    rate_limit_api_per_min: int = 60
    rate_limit_create_per_day: int = 20
    rate_limit_update_per_hour: int = 6
    rate_limit_active_intents: int = 5
```

`backend/src/latch/ratelimit/limiter.py` を作成:

```python
"""レート制限の判断(design §2.2・§2.4・§2.7)。

INCR先行: storeでカウントを進めてから上限と比較する(判定とカウントの間の
並行すり抜けをRedis上で潰す — 429を返したリクエスト・422で失敗した作成も
カウント済み)。バケット文字列はClockから導出する(実時間参照禁止 — C2)。
Redis例外はこの層で503へ包む(fail-closed — design §2.6)。
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from latch.core.clock import Clock, JST
from latch.ratelimit.errors import RateLimitDependencyError, RateLimitedError
from latch.ratelimit.store import RateLimitStore


@dataclass(frozen=True)
class RateLimits:
    """上限4種(08 §5.4)。Settings(LATCH_環境変数)から構築される。"""

    api_per_min: int = 60
    create_per_day: int = 20
    update_per_hour: int = 6
    active_intents: int = 5


class RateLimiter:
    """カウンタ種別ごとのINCR+上限判定。上限値はRateLimitsで注入。"""

    def __init__(
        self, *, store: RateLimitStore, clock: Clock, limits: RateLimits
    ) -> None:
        self._store = store
        self._clock = clock
        self._limits = limits

    @property
    def active_limit(self) -> int:
        """Active Intent数上限(serviceが422判定に使う — design §2.3)。"""
        return self._limits.active_intents

    # -- バケット導出(JST暦。日付はjst_date()・時分はnow()のJST変換 --

    def _minute_bucket(self) -> str:
        return self._clock.now().astimezone(JST).strftime("%Y%m%d%H%M")

    def _hour_bucket(self) -> str:
        return self._clock.now().astimezone(JST).strftime("%Y%m%d%H")

    def _day_bucket(self) -> str:
        return self._clock.jst_date().strftime("%Y%m%d")

    async def _check(self, count: int, limit: int) -> None:
        if count > limit:
            raise RateLimitedError("rate limit exceeded")

    async def _guard(self, incr: Callable[[], Awaitable[int]]) -> int:
        try:
            return await incr()
        except RateLimitDependencyError:
            raise
        except Exception as exc:
            raise RateLimitDependencyError(
                "rate limit dependency unavailable"
            ) from exc

    async def check_api(self, *, user_key: str) -> None:
        """API全体60req/分(user_key=user_id or anon-… — design §2.4)。"""
        count = await self._guard(
            lambda: self._store.incr_api(user_key, self._minute_bucket())
        )
        await self._check(count, self._limits.api_per_min)

    async def check_create(self, *, user_id: uuid.UUID) -> None:
        """Intent作成20件/日(draft・active両方。POST /v1/intents全体)。"""
        count = await self._guard(
            lambda: self._store.incr_create(str(user_id), self._day_bucket())
        )
        await self._check(count, self._limits.create_per_day)

    async def check_update(self, *, intent_id: uuid.UUID) -> None:
        """Intent更新6回/時(PATCH全分岐+resume — design §2.4告白1)。"""
        count = await self._guard(
            lambda: self._store.incr_update(str(intent_id), self._hour_bucket())
        )
        await self._check(count, self._limits.update_per_hour)

    async def check_auth(self, *, provider: str, subject: str) -> None:
        """auth/token・refresh 60req/分(provider+subject単位 — design §2.5)。

        subject(メールアドレス等のPIIになり得る)はsha256でハッシュ化して
        キーへ埋める(SessionStoreがrefresh tokenをSHA-256保持するのと同規律)。
        """
        subject_sha = hashlib.sha256(subject.encode()).hexdigest()
        count = await self._guard(
            lambda: self._store.incr_auth(provider, subject_sha, self._minute_bucket())
        )
        await self._check(count, self._limits.api_per_min)
```

`backend/src/latch/ratelimit/__init__.py` を差し替え:

```python
"""レート制限(M1 ws-4)。08 §5.4の上限4種をRedisカウンタとDB計上で強制する。

横断関心事のためintentsドメインの外に置く(design §2.1-A)。StoreにRedis操作を
閉じ込め・limiterが判断する(auth/のSessionStoreと対称な構成)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from latch.ratelimit.errors import (
    RateLimitDependencyError,
    RateLimitError,
    RateLimitedError,
)
from latch.ratelimit.limiter import RateLimiter, RateLimits
from latch.ratelimit.store import RateLimitStore

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from latch.core.clock import Clock
    from latch.settings import Settings


def make_rate_limiter(
    *, clock: Clock, redis_client: aioredis.Redis, settings: Settings
) -> RateLimiter:
    """設定からRateLimiterを構築する(main.py lifespanが呼ぶ)。"""
    return RateLimiter(
        store=RateLimitStore(redis_client),
        clock=clock,
        limits=RateLimits(
            api_per_min=settings.rate_limit_api_per_min,
            create_per_day=settings.rate_limit_create_per_day,
            update_per_hour=settings.rate_limit_update_per_hour,
            active_intents=settings.rate_limit_active_intents,
        ),
    )


__all__ = [
    "RateLimitDependencyError",
    "RateLimitError",
    "RateLimitedError",
    "RateLimiter",
    "RateLimitStore",
    "RateLimits",
    "make_rate_limiter",
]
```

- [ ] **Step 4: テストが通ることを確認** — `cd backend && uv run pytest tests/unit/ratelimit/ -v` → PASS(test_store 6件+test_limiter 9件+test_jst_boundary 4件)

- [ ] **Step 5: lintとunit全体を確認してコミット**

```bash
make lint && make test
git add backend/src/latch/ratelimit/ backend/src/latch/settings.py backend/tests/unit/ratelimit/
git commit -m "feat(ratelimit): RateLimiter上限判定とJSTバケット導入・Settings上限4種(M1 ws-4 Task 2)"
```

---

## Task 3: intentsフック(Active数422・作成429・更新429)

**Files:**
- Modify: `backend/src/latch/intents/errors.py`(ActiveIntentLimitError追加)
- Modify: `backend/src/latch/intents/store.py`(lock_user_row・count_active追加)
- Modify: `backend/src/latch/intents/service.py`(limiter注入・create/update/resumeフック)
- Modify: `backend/src/latch/intents/__init__.py`(export追加)
- Test: `backend/tests/unit/intents/test_intents_service.py`(StubStore拡張+試験追記)

**Interfaces:**
- Consumes: Task 2の `RateLimiter`(メソッド `check_create(user_id=)`・`check_update(intent_id=)`・プロパティ `active_limit`)。serviceはlimiterへ依存するがimportはTYPE_CHECKINGのみ(ratelimit→intents方向のimportを作らない)
- Produces: `ActiveIntentLimitError(IntentsError)`(http_status=422・code=ACTIVE_INTENT_LIMIT — 既存のIntentsErrorハンドラがそのままenvelope化するためmain.py変更不要)/ `IntentStore.lock_user_row(conn, user_id)`・`IntentStore.count_active(conn, user_id, *, now)` / `IntentService(..., limiter=None)`・`make_intent_service(*, clock, engine, limiter=None)`

**実装の骨子(design §2.3・§2.4の両立)**: Active数検証は「422が必須3検証より先」(§2.4)かつ「users行ロックでCOUNT→書き込みを直列化」(§2.3)するため、active化系の検証一式を**uowトランザクション内の先頭へ移動**する。順序は `uow開始 → lock_user_row → count_active → 422判定 → 必須3 → 時刻 → 年齢 → ジオコーディング → insert → event` となる。ジオコーディング(GeoService)は別コネクションでgeofeaturesのみ参照するためusers行ロックと競合しない。INCR(429)はuser解決の直後・uow開始の前に置く(Redis I/OをDBトランザクション内に入れない)。

- [ ] **Step 1: 失敗するテストを書く** — `backend/tests/unit/intents/test_intents_service.py` へ追記する。まず既存 `StubStore` クラスの `__init__` 末尾(`self.next_id = uuid.uuid4()` の行の後)に以下を追加:

```python
        self.active_count = 0  # count_activeの仕込み値
        self.locked_users = []  # lock_user_rowの呼び出し記録
```

`StubStore` クラスへ以下の2メソッドを追加(`list_page` メソッドの後が目安):

```python
    async def lock_user_row(self, conn, user_id):
        self.locked_users.append(user_id)

    async def count_active(self, conn, user_id, *, now):
        return self.active_count
```

既存ヘルパー `_service(*, store=None, geocoder=None, clock=None)` をlimiter引数対応へ拡張(`limiter` をキーワード引数に追加し、IntentService構築へ渡す):

```python
def _service(*, store=None, geocoder=None, clock=None, limiter=None):
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
        limiter=limiter,
    )
    return svc, store, geocoder, uow_conn, read_conn
```

import部へ追加: 既存の `from latch.intents.errors import (...)` ブロックへ `ActiveIntentLimitError` を加え、`from latch.ratelimit.errors import RateLimitedError` を追加。

ファイル末尾へ以下を追記(既存ヘルパー `_active_input()`・`_row(status=...)` を再利用する):

```python
# --- M1 ws-4: レート制限フック(design §2.3・§2.4)---


class StubLimiter:
    """RateLimiterのスタブ(active_limitを公開・呼び出しを記録・例外を仕込む)。

    serviceはRateLimiterのこの面のみに依存する(構造的型 — 実クラスをimport
    しない)。429相当には実例外 RateLimitedError を使う(serviceはこれを
    ラップせず透過させるため、本物と同じ例外で検証する)。
    """

    def __init__(self, *, active_limit: int = 5, fail_create=False, fail_update=False):
        self.active_limit = active_limit
        self._fail_create = fail_create
        self._fail_update = fail_update
        self.create_calls = []
        self.update_calls = []

    async def check_create(self, *, user_id):
        self.create_calls.append(user_id)
        if self._fail_create:
            raise RateLimitedError("rate limit exceeded")

    async def check_update(self, *, intent_id):
        self.update_calls.append(intent_id)
        if self._fail_update:
            raise RateLimitedError("rate limit exceeded")


async def test_create_active_consumes_create_count():
    limiter = StubLimiter()
    svc, *_ = _service(limiter=limiter)
    await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="active",
        structured_intent=_active_input(),
    )
    assert len(limiter.create_calls) == 1
    assert limiter.create_calls[0] == USER_ID


async def test_create_429_propagates():
    svc, *_ = _service(limiter=StubLimiter(fail_create=True))
    with pytest.raises(RateLimitedError):
        await svc.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=_active_input(),
        )


async def test_create_active_locks_user_row_before_rejecting():
    """uow内で users行ロック→COUNT→判定 の順(design §2.3)。"""
    limiter = StubLimiter()
    store = StubStore()
    store.active_count = 5  # 既にActive満杯
    svc, *_ = _service(limiter=limiter, store=store)
    with pytest.raises(ActiveIntentLimitError) as ei:
        await svc.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=_active_input(),
        )
    assert ei.value.http_status == 422
    assert ei.value.code == "ACTIVE_INTENT_LIMIT"
    assert store.locked_users == [USER_ID]  # 判定前にロック取得


async def test_create_draft_skips_active_check():
    limiter = StubLimiter()
    store = StubStore()
    store.active_count = 5
    svc, *_ = _service(limiter=limiter, store=store)
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="draft",
        structured_intent=None,
    )
    assert row.status == "draft"
    assert store.locked_users == []  # draftはActive数検証なし


async def test_active_limit_precedes_required3():
    """§2.4順序: Active満杯+必須3欠落では ACTIVE_INTENT_LIMIT が先。"""
    limiter = StubLimiter()
    store = StubStore()
    store.active_count = 5
    svc, *_ = _service(limiter=limiter, store=store)
    missing = StructuredIntentInput()  # 必須3すべて欠落
    with pytest.raises(ActiveIntentLimitError):
        await svc.create(
            auth_provider="google",
            auth_subject="s",
            raw_text="r",
            status="active",
            structured_intent=missing,
        )


async def test_update_consumes_update_count():
    limiter = StubLimiter()
    intent_id = uuid.uuid4()
    store = StubStore(rows={intent_id: _row(status="active")})
    svc, *_ = _service(limiter=limiter, store=store)
    await svc.update(
        auth_provider="google",
        auth_subject="s",
        intent_id=intent_id,
        raw_text="r2",
        status=None,
        structured_intent=_active_input(),
    )
    assert limiter.update_calls == [intent_id]


async def test_update_429_precedes_404():
    """INCRはuser解決直後・行取得の前(design §2.4 — 429が先)。"""
    svc, *_ = _service(limiter=StubLimiter(fail_update=True))
    with pytest.raises(RateLimitedError):
        await svc.update(
            auth_provider="google",
            auth_subject="s",
            intent_id=uuid.uuid4(),  # 存在しない行
            raw_text="r",
            status=None,
            structured_intent=None,
        )


async def test_draft_to_active_checks_active_limit():
    intent_id = uuid.uuid4()
    store = StubStore(rows={intent_id: _row(status="draft")})
    store.active_count = 5
    svc, *_ = _service(limiter=StubLimiter(), store=store)
    with pytest.raises(ActiveIntentLimitError):
        await svc.update(
            auth_provider="google",
            auth_subject="s",
            intent_id=intent_id,
            raw_text="r",
            status="active",
            structured_intent=_active_input(),
        )


async def test_resume_consumes_update_count_and_checks_active():
    intent_id = uuid.uuid4()
    store = StubStore(rows={intent_id: _row(status="paused")})
    store.active_count = 5
    limiter = StubLimiter()
    svc, *_ = _service(limiter=limiter, store=store)
    with pytest.raises(ActiveIntentLimitError):
        await svc.resume(
            auth_provider="google", auth_subject="s", intent_id=intent_id
        )
    assert limiter.update_calls == [intent_id]


async def test_pause_does_not_consume_update_count():
    intent_id = uuid.uuid4()
    store = StubStore(rows={intent_id: _row(status="active")})
    limiter = StubLimiter()
    svc, *_ = _service(limiter=limiter, store=store)
    await svc.pause(auth_provider="google", auth_subject="s", intent_id=intent_id)
    assert limiter.update_calls == []


async def test_limiter_none_keeps_existing_behavior():
    """limiter=None(既定)ではロックも判定も走らない(ws-3回帰 — design §2.8)。"""
    store = StubStore()
    store.active_count = 99  # 満杯でも
    svc, *_ = _service(store=store)  # limiter省略=既定None
    row = await svc.create(
        auth_provider="google",
        auth_subject="s",
        raw_text="r",
        status="active",
        structured_intent=_active_input(),
    )
    assert row.status == "active"
    assert store.locked_users == []
```

- [ ] **Step 2: テストが失敗することを確認** — `cd backend && uv run pytest tests/unit/intents/test_intents_service.py -v` → 追加分FAIL(`ActiveIntentLimitError` import error または `limiter` 引数なし)

- [ ] **Step 3: 実装する**

`backend/src/latch/intents/errors.py` へ `InvalidTransitionError` の後に追加:

```python
class ActiveIntentLimitError(IntentsError):
    """Active Intent数上限(08 §5.4。Active 5件/ユーザー)。

    促し文言は「既存Intentの停止・期限切れを促す」趣旨の固定メッセージ。
    レート制限4種のうちActive数のみ422(05 §5 — リソース状態の検証)。
    """

    http_status = 422
    code = "ACTIVE_INTENT_LIMIT"
```

`backend/src/latch/intents/store.py` へ `_LIST` 辞書の後にSQL 2本を追加:

```python
_COUNT_ACTIVE = text("""
    SELECT count(*) FROM intents
    WHERE user_id = :user_id AND status = 'active'
      AND (expires_at IS NULL OR expires_at > :now)
""")
# 期限切れ残留行(sweeperがM3-3未実装のためstatus='active'のまま)は遷移表上
# expired相当のため計上から除外する(design §2.3告白2 — 枠の解放を機能させる)

_SELECT_USER_ROW_FOR_UPDATE = text("SELECT id FROM users WHERE id = :user_id FOR UPDATE")
```

`IntentStore` クラスへ `list_page` の後に2メソッドを追加:

```python
    async def lock_user_row(
        self, conn: AsyncConnection, user_id: uuid.UUID
    ) -> None:
        """users行をFOR UPDATEで確保(同一ユーザーのActive化操作を直列化 — §2.3)。"""
        await conn.execute(_SELECT_USER_ROW_FOR_UPDATE, {"user_id": user_id})

    async def count_active(
        self, conn: AsyncConnection, user_id: uuid.UUID, *, now: datetime
    ) -> int:
        """Active数の計上(design §2.3。期限切れ行は除外)。"""
        return int(
            await conn.scalar(_COUNT_ACTIVE, {"user_id": user_id, "now": now})
        )
```

`backend/src/latch/intents/service.py` を変更する。変更点は5つ:

(1) import部へ追加(`from latch.intents.errors import (...)` ブロックへ `ActiveIntentLimitError` を、TYPE_CHECKING節へlimiter型を):

```python
if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
    from latch.ratelimit import RateLimiter  # 実行時importなし(循環回避)
```

(2) `IntentService.__init__` へ `limiter` 引数を追加(既定None — 既存の直接構築は無変更で動く):

```python
    def __init__(
        self,
        *,
        clock: Clock,
        store: IntentStore,
        uow: UnitOfWork,
        reader: Reader,
        geocoder: SupportsForwardGeocoding,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._clock = clock
        self._store = store
        self._uow = uow
        self._reader = reader
        self._geocoder = geocoder
        self._limiter = limiter
```

(3) `_geocode_or_raise` の後に共通ヘルパーを追加:

```python
    async def _check_active_limit(
        self, conn: AsyncConnection, user_id: uuid.UUID, *, now: datetime
    ) -> None:
        """Active数検証(§2.3: users行ロック→COUNT→判定を同一トランザクションで)。"""
        await self._store.lock_user_row(conn, user_id)
        count = await self._store.count_active(conn, user_id, now=now)
        if count >= self._limiter.active_limit:  # type: ignore[union-attr]
            raise ActiveIntentLimitError(
                "active intent limit reached; pause or let expire an intent"
            )
```

(4) `_create` を次の形へ書き換える(active時のみ検証一式をuow内の先頭へ移動。draftは従来どおり):

```python
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
        if self._limiter is not None:
            # INCR先行(§2.4): 429を返すリクエスト・422で失敗する作成も消費
            await self._limiter.check_create(user_id=user.id)
        if status == "active":
            # 検証一式をuow内へ: 422Activeが必須3より先(§2.4)かつ
            # COUNT→INSERTをusers行ロックで直列化(§2.3)。ジオコーディングは
            # 別コネクションのgeofeatures参照のみでusersロックと競合しない
            async with self._uow() as conn:
                if self._limiter is not None:
                    await self._check_active_limit(conn, user.id, now=now)
                cols = self._resolve_active_or_raise(inp, now=now)
                if cols.alcohol_involved:
                    self._require_age_20(user)
                cols = await self._geocode_or_raise(inp.location.name, cols)
                cols = replace(cols, raw_text=raw_text)
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
```

(5) `_update` の冒頭(user解決・now取得の後、`async with self._uow() as conn:` の前)へINCRを挿入:

```python
        user = await self._require_user(auth_provider, auth_subject)
        now = self._clock.now()
        if self._limiter is not None:
            await self._limiter.check_update(intent_id=intent_id)
        async with self._uow() as conn:
```

`_update_draft` のactive化分岐(`# draft→active化: 全量必須・全検証(不通なら422でdraft据え置き)` コメント直後の `if inp is None:` の前)へActive検証を挿入:

```python
        # draft→active化: 全量必須・全検証(不通なら422でdraft据え置き)
        if self._limiter is not None:
            await self._check_active_limit(conn, user.id, now=now)
        if inp is None:
            raise IntentValidationError("structured_intent is required")
```

(6) `resume` を `_transition` にオプションを渡す形へ変更し、`_transition` へ2つのフラグを追加:

```python
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
            rate_limit_update=True,  # resumeはupdate種Eventを発行(告白1)
            check_active=True,
        )
```

`_transition` のシグネチャと本体(該当行のみ抜粋 — 他行は無変更):

```python
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
        rate_limit_update: bool = False,
        check_active: bool = False,
    ) -> IntentRow:
        try:
            user = await self._require_user(auth_provider, auth_subject)
            now = self._clock.now()
            if self._limiter is not None and rate_limit_update:
                await self._limiter.check_update(intent_id=intent_id)
            async with self._uow() as conn:
                row = await self._store.fetch_for_update(conn, intent_id)
                if row is None:
                    raise IntentNotFoundError("intent not found")
                if row.user_id != user.id:
                    raise ForbiddenError("not owner")
                if row.status not in allowed_from:
                    raise InvalidTransitionError("invalid status transition")
                if self._limiter is not None and check_active:
                    await self._check_active_limit(conn, user.id, now=now)
                new_version = row.version + version_delta
                ...(以下既存のupdate_status・insert_match_event・returnは無変更)
```

(7) `make_intent_service` へlimiter引数を追加:

```python
def make_intent_service(
    *, clock: Clock, engine: AsyncEngine, limiter: RateLimiter | None = None
) -> IntentService:
    ...(docstring・importは無変更。returnへ limiter=limiter を追加)
    return IntentService(
        clock=clock,
        store=IntentStore(engine),
        uow=engine.begin,
        reader=engine.connect,
        geocoder=GeoService(engine),
        limiter=limiter,
    )
```

`backend/src/latch/intents/__init__.py` へ `from latch.intents.errors import (...)` ブロックに `ActiveIntentLimitError` を追加し、`__all__` へ `"ActiveIntentLimitError"` を追加(アルファベット順の先頭付近)。

- [ ] **Step 4: テストが通ることを確認** — `cd backend && uv run pytest tests/unit/intents/test_intents_service.py -v` → 全PASS(既存+追加分)。この時点で `make test` 全体も緑であること(limiter=None経路の回帰 — 既存試験は無修正のまま)

- [ ] **Step 5: lintとunit全体を確認してコミット**

```bash
make lint && make test
git add backend/src/latch/intents/ backend/tests/unit/intents/test_intents_service.py
git commit -m "feat(intents): Active数422と作成/更新429のフックを追加(M1 ws-4 Task 3)"
```

---

## Task 4: API全体60req/分の強制(依存差し替え・main.py接続)

**Files:**
- Create: `backend/src/latch/ratelimit/deps.py`(api_rate_limited)
- Modify: `backend/src/latch/main.py`(lifespan常時Redis生成・app.state.rate_limiter/user_lookup・RateLimitErrorハンドラ・create_app引数)
- Modify: `backend/src/latch/intents/routes.py`(parse_router・intents_crud_routerの依存差し替え)
- Modify: `backend/src/latch/users/routes.py`(users_router)
- Modify: `backend/src/latch/auth/routes.py`(logout_router)
- Test: `backend/tests/unit/test_rate_limit_wiring.py`

**Interfaces:**
- Consumes: Task 2の `make_rate_limiter`・`RateLimiter.check_api(user_key=)`、Task 3の `make_intent_service(limiter=)`、既存 `require_authenticated`(auth/deps.py)・`make_user_lookup`(auth/service.py)
- Produces: `api_rate_limited(request, claims) -> AccessTokenClaims`(FastAPI依存関数。require_authenticatedを内包し401→429の順を保証)。`app.state.rate_limiter`(未載荷/Noneなら無効 — unit試験のASGITransportはlifespanを走らせないため既存試験は無修正)・`app.state.user_lookup`。`create_app(..., rate_limiter=None)`(テスト注入用)

**キー単位の設計補完(design §2.7への実装補完として明記)**: AccessTokenClaimsはprovider+subjectのみでuser_idを持たない(C3)。api_rate_limitedは `app.state.user_lookup` でuser_idを解決し、登録済みならdesign §2.7どおり `rl:api:{user_id}`、未登録(初回登録待ち。parse等を叩ける)ならフォールバックキー `rl:api:anon-{sha256(provider:subject)}` でカウントする。未登録のままparse連打する経路を放置しない(08 §5.4源流抑制の趣旨)。anon-はUUID形式と文字長が異なるためuser_id空間と衝突しない。401リクエストはINCRしない(認証が先・確定値の401優先)。

- [ ] **Step 1: 失敗するテストを書く** — `backend/tests/unit/test_rate_limit_wiring.py` を作成:

```python
"""依存差し替えの網羅(design §2.4「差し替え忘れは試験で網羅確認」)。

v1 API契約の全ルートが api_rate_limited を持ち、認証不要2経路+/healthが
対象外であることを構成で強制する(実HTTP不要の静的検査)。
"""

from collections import Counter

from fastapi.routing import APIRoute

from latch.main import create_app
from latch.ratelimit.deps import api_rate_limited

# C3(05 §5): 認証不要はtoken/refreshのみ。/health はv1契約の外(確定値13)
UNPROTECTED = {"/v1/auth/token", "/v1/auth/refresh", "/health"}


def _has_rate_limit(route: APIRoute) -> bool:
    return any(
        getattr(dep, "call", None) is api_rate_limited for dep in route.dependencies
    )


def test_all_v1_routes_are_rate_limited():
    app = create_app()
    protected = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in UNPROTECTED:
            assert not _has_rate_limit(route), f"対象外のはず: {route.path}"
        else:
            assert _has_rate_limit(route), f"差し替え漏れ: {route.path}"
            protected.append(route.path)
    # method別にAPIRouteが分かれるためパス件数で数える(M1 ws-4時点の全契約)
    assert Counter(protected) == {
        "/v1/auth/logout": 1,
        "/v1/intents": 2,  # GET + POST
        "/v1/intents/parse": 1,
        "/v1/intents/{intent_id}": 3,  # GET + PATCH + DELETE
        "/v1/intents/{intent_id}/pause": 1,
        "/v1/intents/{intent_id}/resume": 1,
        "/v1/users": 1,
        "/v1/users/me": 1,
    }
```

- [ ] **Step 2: テストが失敗することを確認** — `cd backend && uv run pytest tests/unit/test_rate_limit_wiring.py -v` → FAIL(latch.ratelimit.deps 不在)

- [ ] **Step 3: 実装する**

`backend/src/latch/ratelimit/deps.py` を作成:

```python
"""API全体60req/分の強制点(design §2.4)。

require_authenticated を内包する(401→429の順を構造で保証 — 攻撃的な流量に
ドメイン検証を消費させる前に429で返す)。claimsにuser_idは無いため(C3)、
app.state.user_lookup で解決し、未登録はanonフォールバックキーへ(計画§Task 4)。
ルータ単位で付すためミドルウェア不採用(v1 API契約にのみ適用を構造で表現)。
"""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import Depends, Request

from latch.auth.deps import require_authenticated
from latch.auth.tokens import AccessTokenClaims
from latch.ratelimit.errors import RateLimitDependencyError


def _anon_key(provider: str, subject: str) -> str:
    return "anon-" + hashlib.sha256(f"{provider}:{subject}".encode()).hexdigest()


async def api_rate_limited(
    request: Request,
    claims: Annotated[AccessTokenClaims, Depends(require_authenticated)],
) -> AccessTokenClaims:
    """認証成立後に user_id 単位でINCRする(未載荷/Noneのlimiterは素通り)。"""
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None:
        return claims
    lookup = getattr(request.app.state, "user_lookup", None)
    if lookup is None:
        user_key = _anon_key(claims.auth_provider, claims.auth_subject)
    else:
        try:
            user_id = await lookup(claims.auth_provider, claims.auth_subject)
        except Exception as exc:
            raise RateLimitDependencyError(
                "rate limit dependency unavailable"
            ) from exc
        user_key = (
            str(user_id)
            if user_id is not None
            else _anon_key(claims.auth_provider, claims.auth_subject)
        )
    await limiter.check_api(user_key=user_key)
    return claims
```

`backend/src/latch/main.py` を変更する。変更点:

(1) import部へ追加:

```python
from latch.ratelimit import make_rate_limiter
from latch.ratelimit.deps import api_rate_limited
from latch.ratelimit.errors import RateLimitError
```

(2) ロガー定義の下へ1行追加:

```python
ratelimit_logger = logging.getLogger("latch.ratelimit")
```

(3) `_lifespan` を次の形へ書き換え(redis生成の常時化・rate_limiter構築・user_lookupのapp.state載荷):

```python
@asynccontextmanager
async def _lifespan(app: FastAPI):
    # サービスごとの独立スキップ判定(テスト注入があれば構築しない)
    build_auth = not hasattr(app.state, "auth_service")
    build_users = not hasattr(app.state, "users_service")
    build_intents = not hasattr(app.state, "intent_parse_service")
    build_intents_crud = not hasattr(app.state, "intent_service")
    build_rate_limit = not hasattr(app.state, "rate_limiter")
    if not (
        build_auth
        or build_users
        or build_intents
        or build_intents_crud
        or build_rate_limit
    ):
        yield
        return
    settings: Settings = app.state.settings
    redis_client = None
    if build_auth or build_rate_limit:
        # Redisはauth(失効リスト)とレート制限カウンタの共用(design §2.8)
        redis_client = aioredis.Redis.from_url(
            settings.redis_url, decode_responses=True
        )
    # engine は auth・users・intents の共有資産
    engine = getattr(app.state, "db_engine", None)
    if engine is None:
        engine = create_db_engine(settings)
        app.state.db_engine = engine
    # user_lookup は auth と intents の共有。api_rate_limited も消費する
    # (user_id単位のカウント — design §2.4)
    user_lookup = make_user_lookup(engine)
    app.state.user_lookup = user_lookup
    if build_rate_limit:
        app.state.rate_limiter = make_rate_limiter(
            clock=app.state.clock,
            redis_client=redis_client,
            settings=settings,
        )
    if build_auth:
        app.state.auth_service = build_auth_service(
            clock=app.state.clock,
            settings=settings,
            redis_client=redis_client,
            user_lookup=user_lookup,
        )
    if build_users:
        app.state.users_service = make_user_service(
            clock=app.state.clock, engine=engine
        )
    if build_intents:
        app.state.intent_parse_service = make_intent_parse_service(
            clock=app.state.clock,
            settings=settings,
            user_lookup=user_lookup,
        )
    if build_intents_crud:
        app.state.intent_service = make_intent_service(
            clock=app.state.clock,
            engine=engine,
            limiter=app.state.rate_limiter if build_rate_limit else None,
        )
    try:
        yield
    finally:
        if redis_client is not None:
            await redis_client.aclose()
        await engine.dispose()
```

注: `make_intent_service(limiter=...)` に渡すのは「このlifespanで構築したlimiter」。auth_serviceへのlimiter渡しはTask 5で追加する(Task 4時点ではbuild_auth_service呼び出しは現状のまま)。

(4) `create_app` へ `rate_limiter=None` 引数を追加し、state載荷を追加:

```python
def create_app(
    clock: Clock | None = None,
    settings: Settings | None = None,
    auth_service=None,
    users_service=None,
    intent_parse_service=None,
    intent_service=None,
    rate_limiter=None,
) -> FastAPI:
    ...
    if intent_service is not None:
        app.state.intent_service = intent_service
    if rate_limiter is not None:
        app.state.rate_limiter = rate_limiter
```

`rate_limiter=None` を渡してもapp.stateには載らない(=lifespan構築に委ねる)。unit試験(ASGITransport・lifespan非実行)ではapp.state.rate_limiterが常に未載荷のため `api_rate_limited` は素通りする — design §2.8「rate_limiter=Noneで無効」の実態はこの挙動(依存とserviceともapp.state未載荷/Noneを無効と扱う)。明示的に無効化したいテストは「載せない」ことが無効化である。

(5) ハンドラを `intents_error_handler` の後に追加:

```python
    @app.exception_handler(RateLimitError)
    async def rate_limit_error_handler(
        request: Request, exc: RateLimitError
    ) -> JSONResponse:
        ratelimit_logger.warning("ratelimit.error code=%s", exc.code)
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_body(exc.code, str(exc)),
        )
```

(6) モジュールdocstringの「統合履歴」行へ `M1 ws-4 レート制限` を追記。

(7) 3つのルータファイルで依存を差し替え。`backend/src/latch/intents/routes.py`(2箇所 — 各ルータ定義):

```python
from latch.ratelimit.deps import api_rate_limited

parse_router = APIRouter(
    prefix="/v1/intents",
    tags=["intents"],
    dependencies=[Depends(api_rate_limited)],  # 401→429(M1 ws-4・design §2.4)
)

intents_crud_router = APIRouter(
    prefix="/v1/intents",
    tags=["intents"],
    dependencies=[Depends(api_rate_limited)],  # 401→429(M1 ws-4・design §2.4)
)
```

`backend/src/latch/users/routes.py`:

```python
from latch.ratelimit.deps import api_rate_limited

users_router = APIRouter(
    prefix="/v1/users",
    tags=["users"],
    dependencies=[Depends(api_rate_limited)],  # 401→429(M1 ws-4)
)
```

`backend/src/latch/auth/routes.py`:

```python
from latch.ratelimit.deps import api_rate_limited

logout_router = APIRouter(
    prefix="/v1/auth", tags=["auth"], dependencies=[Depends(api_rate_limited)]
)
```

(public_routerは依存を持たせない — 認証不要2経路。各エンドポイント関数シグネチャの `Depends(require_authenticated)` はそのまま残す。FastAPIの依存キャッシュにより同一リクエスト内でrequire_authenticatedは1回だけ実行される)

- [ ] **Step 4: テストが通ることを確認** — `cd backend && uv run pytest tests/unit/test_rate_limit_wiring.py tests/unit/test_app_health.py -v` → PASS。続けて `make test` 全体(unit)が緑であること — 特に tests/unit/intents/test_crud_routes.py・test_parse_routes.py・tests/unit/users/test_users_routes.py・tests/unit/auth/test_routes.py が無修正で緑であることを確認する(app.state.rate_limiter未載荷=無効経路の回帰)

- [ ] **Step 5: lintとunit全体を確認してコミット**

```bash
make lint && make test
git add backend/src/latch/ratelimit/deps.py backend/src/latch/main.py \
  backend/src/latch/intents/routes.py backend/src/latch/users/routes.py \
  backend/src/latch/auth/routes.py backend/tests/unit/test_rate_limit_wiring.py
git commit -m "feat(ratelimit): API全体60req/分の依存差し替えとlifespan接続(M1 ws-4 Task 4)"
```

---

## Task 5: auth/token・refreshへの429フック

**Files:**
- Modify: `backend/src/latch/auth/service.py`(limiter注入・_token/_refreshのIdP検証後フック・build_auth_serviceへlimiter引数)
- Modify: `backend/src/latch/main.py`(build_auth_service呼び出しへlimiter渡し)
- Test: `backend/tests/unit/auth/test_service_token.py`(追記)
- Test: `backend/tests/unit/auth/test_service_refresh.py`(追記)

**Interfaces:**
- Consumes: Task 2の `RateLimiter.check_auth(provider=, subject=)`
- Produces: `AuthService(..., limiter=None)`・`build_auth_service(..., limiter: RateLimiter | None = None)`。importはTYPE_CHECKINGのみ(ratelimit/deps.py→auth/deps.py方向のimportがあり実行時循環を避ける)

**適用位置と注意(design §2.5)**: IdP検証(refreshではrotate=検証を兼ねる)の**直後**にINCRする — 検証より前に置くと401 INVALID_IDP_TOKEN優先の原則に反するため。refreshはrotate後でしかprovider+subjectが確定しない(不透明トークン)ため、rotate後にINCRする。帰結として429を返すrefreshでは回転後の新refresh_tokenが応答に含まれず失われる(旧トークン再提示は族失効→401)。正当利用で1分61回のrefreshは発生しない(access TTL 1時間・refreshは期限切れ直前に1回)ため受容する。統合試験の429検証はtoken側で行い(design §4.2)、refresh側の429はunitのみで検証する。

- [ ] **Step 1: 失敗するテストを書く** — `backend/tests/unit/auth/test_service_token.py` へ追記。まず `_service` ヘルパーへlimiter引数を追加(既存呼び出しは無影響):

```python
def _service(clock, redis, *, lookup=None, idp=None, limiter=None) -> AuthService:
    return AuthService(
        clock=clock,
        secret=SECRET,
        sessions=SessionStore(redis),
        idp=idp if idp is not None else _idp(),
        user_lookup=lookup if lookup is not None else _none_lookup,
        limiter=limiter,
    )
```

ファイル末尾へ追記(importへ `from latch.ratelimit import RateLimiter` は不要 — スタブを使う):

```python
# --- M1 ws-4: token発行の429フック(design §2.5)---


class _StubAuthLimiter:
    """check_authのスタブ(呼び出し記録・任意回数でRateLimitedError)。"""

    def __init__(self, *, allowed: int = 60):
        self.allowed = allowed
        self.calls = []

    async def check_auth(self, *, provider: str, subject: str):
        self.calls.append((provider, subject))
        self.allowed -= 1
        if self.allowed < 0:
            raise RateLimitedError("rate limit exceeded")


async def test_token_counts_provider_subject_after_idp_verify(redis, clock):
    limiter = _StubAuthLimiter()
    await _service(clock, redis, limiter=limiter).token(
        provider="google", idp_token=_idp_token("sub-1")
    )
    assert limiter.calls == [("google", "sub-1")]


async def test_token_61st_is_rate_limited(redis, clock):
    limiter = _StubAuthLimiter(allowed=1)
    svc = _service(clock, redis, limiter=limiter)
    await svc.token(provider="google", idp_token=_idp_token("sub-1"))
    with pytest.raises(RateLimitedError) as ei:
        await svc.token(provider="google", idp_token=_idp_token("sub-1"))
    assert ei.value.http_status == 429
    assert ei.value.code == "RATE_LIMITED"


async def test_token_401_precedes_rate_limit(redis, clock):
    """無効idp_tokenはIdP検証で401(INCRされない — design §2.5の401優先)。"""
    limiter = _StubAuthLimiter(allowed=0)
    with pytest.raises(InvalidIdpTokenError):
        await _service(clock, redis, limiter=limiter).token(
            provider="google", idp_token="garbage"
        )
    assert limiter.calls == []


async def test_token_no_limiter_keeps_behavior(redis, clock):
    """limiter=None(既定)は既存挙動のまま(M0回帰 — design §2.8)。"""
    result = await _service(clock, redis).token(
        provider="google", idp_token=_idp_token("sub-1")
    )
    assert result.token_type == "Bearer"
```

import部へ追加: `from latch.ratelimit.errors import RateLimitedError`

`backend/tests/unit/auth/test_service_refresh.py` へ同様に追記。まず既存 `_service(clock, redis)` ヘルパーへlimiter引数を追加:

```python
def _service(clock, redis, *, limiter=None) -> AuthService:
    return AuthService(
        clock=clock,
        secret=SECRET,
        sessions=SessionStore(redis),
        idp=IdPVerifier(
            configs={"google": IdPVerifyConfig(issuer=ISSUER_G, audience=AUDIENCE)}
        ),
        user_lookup=_none_lookup,
        limiter=limiter,
    )
```

ファイル末尾へ追記(importへ `from latch.ratelimit.errors import RateLimitedError` を追加。`_StubAuthLimiter` は下記定義をそのまま使う):

```python
# --- M1 ws-4: refreshの429フック(design §2.5)---


class _StubAuthLimiter:
    """check_authのスタブ(呼び出し記録・任意回数でRateLimitedError)。"""

    def __init__(self, *, allowed: int = 60):
        self.allowed = allowed
        self.calls = []

    async def check_auth(self, *, provider: str, subject: str):
        self.calls.append((provider, subject))
        self.allowed -= 1
        if self.allowed < 0:
            raise RateLimitedError("rate limit exceeded")


async def test_refresh_counts_after_rotation(redis, clock):
    """rotate(検証を兼ねる)の後にINCR — provider+subjectは回転結果由来。"""
    limiter = _StubAuthLimiter()
    issue_svc = _service(clock, redis)  # 発行側はlimiterなし
    _, refresh = await _token_pair(issue_svc)
    svc = _service(clock, redis, limiter=limiter)
    await svc.refresh(refresh_token=refresh)
    assert limiter.calls == [("google", "sub-1")]


async def test_refresh_invalid_token_is_401_not_counted(redis, clock):
    limiter = _StubAuthLimiter(allowed=0)
    svc = _service(clock, redis, limiter=limiter)
    with pytest.raises(InvalidRefreshTokenError):
        await svc.refresh(refresh_token="unknown-token-value")
    assert limiter.calls == []
```

- [ ] **Step 2: テストが失敗することを確認** — `cd backend && uv run pytest tests/unit/auth/ -v` → 追加分FAIL(`limiter` 引数なし)

- [ ] **Step 3: 実装する** — `backend/src/latch/auth/service.py` を変更:

(1) TYPE_CHECKING節を追加しlimiter型をimport(実行時importなし — ratelimit/deps.pyがauthをimportするため循環回避):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from latch.ratelimit import RateLimiter
```

(2) `AuthService.__init__` へ `limiter: RateLimiter | None = None` 引数と `self._limiter = limiter` を追加(最後の引数として)。

(3) `_token` のIdP検証の直後へフック:

```python
    async def _token(self, *, provider: str, idp_token: str) -> TokenResult:
        _, subject = await self._idp.verify(
            provider=provider, idp_token=idp_token, clock=self._clock
        )
        if self._limiter is not None:
            # IdP検証の直後(design §2.5): 401優先を保ったうえで429
            await self._limiter.check_auth(provider=provider, subject=subject)
        user_id = await self._user_lookup(provider, subject)
        ...(既存は無変更)
```

(4) `_refresh` のrotateの直後へフック:

```python
    async def _refresh(self, *, refresh_token: str) -> RefreshResult:
        rotated = await self._sessions.rotate_refresh(
            token=refresh_token, now=self._clock.now()
        )
        if self._limiter is not None:
            # rotate(検証を兼ねる)の後にprovider+subjectが確定する(design §2.5)
            await self._limiter.check_auth(
                provider=rotated.provider, subject=rotated.subject
            )
        ...(既存は無変更)
```

(5) `build_auth_service` へ `limiter: RateLimiter | None = None` 引数を追加し、`AuthService(...)` へ `limiter=limiter` を渡す。docstringへ「limiterはNone=無効(ci試験既定)。main.py lifespanがrate_limiterを渡す」を追記。

(6) `backend/src/latch/main.py` の `build_auth_service` 呼び出しへ渡す(Task 4でlifespanを書き換えた箇所):

```python
    if build_auth:
        app.state.auth_service = build_auth_service(
            clock=app.state.clock,
            settings=settings,
            redis_client=redis_client,
            user_lookup=user_lookup,
            limiter=app.state.rate_limiter if build_rate_limit else None,
        )
```

- [ ] **Step 4: テストが通ることを確認** — `cd backend && uv run pytest tests/unit/auth/ -v` → 全PASS(既存+追記)。`make test` 全体も緑

- [ ] **Step 5: lintとunit全体を確認してコミット**

```bash
make lint && make test
git add backend/src/latch/auth/service.py backend/src/latch/main.py \
  backend/tests/unit/auth/test_service_token.py backend/tests/unit/auth/test_service_refresh.py
git commit -m "feat(auth): token/refreshへprovider+subject単位の429フックを追加(M1 ws-4 Task 5)"
```

---

## Task 6: 統合試験(実api・実Redis・実DB)と回帰検算

**Files:**
- Create: `backend/tests/integration/test_ratelimit_api.py`

**Interfaces:**
- Consumes: 実compose環境(api 127.0.0.1:8000・Redis・DB — `api_client`・`db_engine`・`redis_client` フィクスチャはtests/integration/conftest.py既存)
- 前提: 本試験の実行にはapiイメージ再ビルドが必須(STATUS運用ルール4)。**agent3はtest-ciを実行しない**ため、本タスクでは「作成とunit緑の確認」までを行い、実行はスーパーバイザー検証に委ねる

**既存試験の保全検算(design §2.8 — agent3はこれを確認し報告へ記載する)**: api 60req/分はuser_id単位の分バケット。既存integration試験はsubject(uuid接尾辞)ごとにユーザーが分散するため、同一ユーザーの同一分内リクエスト数が問題になる。実測値:

| 試験ファイル | 同一ユーザー最大req/分(api) | 作成/日 | 更新/時 | 同時Active |
|---|---|---|---|---|
| test_intents_crud_api(10件) | 8(test_4・test_9) | 4(test_9) | 2(test_3) | 3(test_9) |
| test_users_api | 3程度 | — | — | — |
| test_intents_parse_api | 3程度(token交換+parse) | — | — | — |
| test_auth_api | token/refresh各4回まで(subjectユニーク・auth系別カウンタ) | — | — | — |

いずれも上限(60・20・6・5)未満。agent3はTask 6完了時に `make test`(unit)が緑であることと、この検算表を報告へ記録する。もしunitで壊れた場合は `app.state.rate_limiter` 未載荷(無効)経路を疑うこと。

- [ ] **Step 1: 統合試験ファイルを作成する** — `backend/tests/integration/test_ratelimit_api.py`:

```python
"""レート制限4種+auth系429のci環境実証(M1 ws-4 design §4.2)。

実HTTP(compose api=127.0.0.1:8000)・実Redis・実DB。**実行はapiイメージ
再ビルド後の make test-ci のみ**(compose upはapiイメージを再ビルドしない —
STATUS運用ルール4。スーパーバイザー検証時に実行)。subjectは実行ごとに
ユニーク、DB行は試験内で後始末(共有ci-db汚染回避 — M1 ws-3と同じ規約)。
Redis鍵(rl:*)はTTL付きで自動消滅するため追加掃除はしない(test_auth_api規約)。
api 60req/分の検証ではregister(users POST 1回)もカウントに計上されるため
GET 59回で到達・60回目で429になる(INCR先行 — design §2.4)。
"""

import asyncio
import sys
import uuid as uuid_mod
from datetime import timedelta

import pytest
from sqlalchemy import text

from latch.core.clock import SystemClock

pytestmark = pytest.mark.integration


def _unique_subject(prefix: str) -> str:
    return f"m1ws4-{prefix}-{uuid_mod.uuid4().hex[:12]}"


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
    """token発行→User登録→(headers, user_id)を返す(M1 ws-3流儀)。"""
    idp_token = await _cli_idp_token("google", subject)
    tok = await api_client.post(
        "/v1/auth/token", json={"provider": "google", "idp_token": idp_token}
    )
    assert tok.status_code == 200, tok.text
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}"}
    created = await api_client.post(
        "/v1/users",
        headers=headers,
        json={"display_name": "ws4", "birth_date": birth_date, "profile": {}},
    )
    assert created.status_code == 201, created.text
    return headers, created.json()["user"]["id"]


def _future(hours: float) -> str:
    """api実Clock(SystemClock)基準の未来ISO時刻(境界から離れた値のみ使う)。"""
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
        "ng_unverifiable": [],
        "negative_constraints": [],
    }


def _active_payload(
    structured: dict | None = None, raw: str = "今夜20時から天文館で軽く飲みたい"
) -> dict:
    return {
        "raw_text": raw,
        "status": "active",
        "structured_intent": structured if structured is not None else _structured(),
    }


def _draft_payload(raw: str = "下書き") -> dict:
    return {"raw_text": raw, "status": "draft"}


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


async def _post_active(api_client, headers, raw: str):
    return await api_client.post(
        "/v1/intents", headers=headers, json=_active_payload(raw=raw)
    )


# --- §4.2-1 作成20件/日 ---


async def test_1_create_daily_limit(api_client, db_engine):
    subject = _unique_subject("c20")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        for i in range(20):
            resp = await api_client.post(
                "/v1/intents", headers=headers, json=_draft_payload(f"下書き{i}")
            )
            assert resp.status_code == 201, (i, resp.text)
        exceeded = await api_client.post(
            "/v1/intents", headers=headers, json=_draft_payload("21件目")
        )
        assert exceeded.status_code == 429, exceeded.text
        assert exceeded.json()["error"]["code"] == "RATE_LIMITED"
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-2 更新6回/時(単位=Intent) ---


async def test_2_update_hourly_limit(api_client, db_engine):
    subject = _unique_subject("u6")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        created = await _post_active(api_client, headers, "更新対象")
        assert created.status_code == 201, created.text
        intent_id = created.json()["intent"]["id"]
        moved = dict(_structured())
        moved["participants"] = {"min": 3, "max": 4}
        for i in range(6):
            resp = await api_client.patch(
                f"/v1/intents/{intent_id}",
                headers=headers,
                json=_active_payload(structured=moved, raw=f"更新{i}"),
            )
            assert resp.status_code == 200, (i, resp.text)
        exceeded = await api_client.patch(
            f"/v1/intents/{intent_id}",
            headers=headers,
            json=_active_payload(structured=moved, raw="更新7"),
        )
        assert exceeded.status_code == 429, exceeded.text
        assert exceeded.json()["error"]["code"] == "RATE_LIMITED"
        # 別Intentは単位分離(同一ユーザーでも別行は受理)
        other = await _post_active(api_client, headers, "別件")
        assert other.status_code == 201, other.text
        other_id = other.json()["intent"]["id"]
        resp = await api_client.patch(
            f"/v1/intents/{other_id}",
            headers=headers,
            json=_active_payload(structured=moved, raw="別件更新"),
        )
        assert resp.status_code == 200, resp.text
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-3 API 60req/分(単位=ユーザー)・401優先 ---


async def test_3_api_per_min_and_401_priority(api_client, db_engine):
    subject = _unique_subject("a60")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        # register(users POST)を含め同一分バケットの61リクエスト目→429
        for i in range(59):
            resp = await api_client.get("/v1/intents", headers=headers)
            assert resp.status_code == 200, (i, resp.text)
        exceeded = await api_client.get("/v1/intents", headers=headers)
        assert exceeded.status_code == 429, exceeded.text
        assert exceeded.json()["error"]["code"] == "RATE_LIMITED"
        # 401優先: 超過状態でも無効JWTは401(認証が先でINCRされない)
        unauthorized = await api_client.get(
            "/v1/intents", headers={"Authorization": "Bearer invalid.jwt.value"}
        )
        assert unauthorized.status_code == 401, unauthorized.text
        assert unauthorized.json()["error"]["code"] == "UNAUTHENTICATED"
        # 別ユーザーは同時刻に受理される(単位=ユーザー)
        other_subject = _unique_subject("a60b")
        other_id = None
        try:
            other_headers, other_id = await _register(api_client, other_subject)
            for i in range(5):
                resp = await api_client.get("/v1/intents", headers=other_headers)
                assert resp.status_code == 200, resp.text
        finally:
            await _cleanup(db_engine, other_id, other_subject)
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-4 Active 5件: 作成・枠解放 ---


async def test_4_active_limit_create_and_release(api_client, db_engine):
    subject = _unique_subject("act5")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        first_id = None
        for i in range(5):
            resp = await _post_active(api_client, headers, f"act{i}")
            assert resp.status_code == 201, (i, resp.text)
            if first_id is None:
                first_id = resp.json()["intent"]["id"]
        exceeded = await _post_active(api_client, headers, "6件目")
        assert exceeded.status_code == 422, exceeded.text
        assert exceeded.json()["error"]["code"] == "ACTIVE_INTENT_LIMIT"
        # 枠解放: 1件pauseすると6件目が受理(pauseはActive数を減らす)
        paused = await api_client.post(
            f"/v1/intents/{first_id}/pause", headers=headers
        )
        assert paused.status_code == 200, paused.text
        after = await _post_active(api_client, headers, "pause後")
        assert after.status_code == 201, after.text
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-4b Active 5件: draft→active化・resume ---


async def test_5_active_limit_on_activate_and_resume(api_client, db_engine):
    subject = _unique_subject("d2a")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        first_id = None
        for i in range(5):
            resp = await _post_active(api_client, headers, f"a{i}")
            assert resp.status_code == 201, (i, resp.text)
            if first_id is None:
                first_id = resp.json()["intent"]["id"]
        # draft→active化は6件目として422
        draft = await api_client.post(
            "/v1/intents", headers=headers, json=_draft_payload("後でactive化")
        )
        assert draft.status_code == 201, draft.text
        draft_id = draft.json()["intent"]["id"]
        activated = await api_client.patch(
            f"/v1/intents/{draft_id}",
            headers=headers,
            json=_active_payload(raw="active化"),
        )
        assert activated.status_code == 422, activated.text
        assert activated.json()["error"]["code"] == "ACTIVE_INTENT_LIMIT"
        # 1件pause→draft→active化は受理(5件目として)
        paused = await api_client.post(
            f"/v1/intents/{first_id}/pause", headers=headers
        )
        assert paused.status_code == 200, paused.text
        activated2 = await api_client.patch(
            f"/v1/intents/{draft_id}",
            headers=headers,
            json=_active_payload(raw="active化2"),
        )
        assert activated2.status_code == 200, activated2.text
        # paused行のresumeは6件目として422
        resumed = await api_client.post(
            f"/v1/intents/{first_id}/resume", headers=headers
        )
        assert resumed.status_code == 422, resumed.text
        assert resumed.json()["error"]["code"] == "ACTIVE_INTENT_LIMIT"
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-4c 期限切れactive残留行は計上から除外 ---


async def test_6_active_limit_excludes_expired_rows(api_client, db_engine):
    subject = _unique_subject("exp")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        ids = []
        for i in range(5):
            resp = await _post_active(api_client, headers, f"e{i}")
            assert resp.status_code == 201, (i, resp.text)
            ids.append(resp.json()["intent"]["id"])
        exceeded = await _post_active(api_client, headers, "6件目")
        assert exceeded.status_code == 422, exceeded.text
        # 1件を期限切れへ(DB直接UPDATE — sweeperはM3-3のため残留する行)
        async with db_engine.begin() as conn:
            await conn.execute(
                text("UPDATE intents SET expires_at = :past WHERE id = :i"),
                {"past": SystemClock().now() - timedelta(hours=1), "i": ids[0]},
            )
        # 期限切れ行は計上から除外→6件目が受理(design §2.3告白2)
        after = await _post_active(api_client, headers, "期限切れ後")
        assert after.status_code == 201, after.text
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-5 resume連打の更新カウント ---


async def test_7_resume_counts_toward_update_limit(api_client, db_engine):
    subject = _unique_subject("res")
    user_id = None
    try:
        headers, user_id = await _register(api_client, subject)
        created = await _post_active(api_client, headers, "resume対象")
        assert created.status_code == 201, created.text
        intent_id = created.json()["intent"]["id"]
        for i in range(6):
            paused = await api_client.post(
                f"/v1/intents/{intent_id}/pause", headers=headers
            )
            assert paused.status_code == 200, (i, paused.text)
            resumed = await api_client.post(
                f"/v1/intents/{intent_id}/resume", headers=headers
            )
            assert resumed.status_code == 200, (i, resumed.text)
        # 7回目のresumeは429(pauseは対象外・更新6回/時)
        paused = await api_client.post(
            f"/v1/intents/{intent_id}/pause", headers=headers
        )
        assert paused.status_code == 200, paused.text
        exceeded = await api_client.post(
            f"/v1/intents/{intent_id}/resume", headers=headers
        )
        assert exceeded.status_code == 429, exceeded.text
        assert exceeded.json()["error"]["code"] == "RATE_LIMITED"
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-6 auth/token 60req/分(provider+subject単位) ---


async def test_8_auth_token_rate_limit(api_client, db_engine):
    subject = _unique_subject("tok")
    user_id = None
    try:
        idp_token = await _cli_idp_token("google", subject)
        for i in range(60):
            resp = await api_client.post(
                "/v1/auth/token",
                json={"provider": "google", "idp_token": idp_token},
            )
            assert resp.status_code == 200, (i, resp.text)
        exceeded = await api_client.post(
            "/v1/auth/token", json={"provider": "google", "idp_token": idp_token}
        )
        assert exceeded.status_code == 429, exceeded.text
        assert exceeded.json()["error"]["code"] == "RATE_LIMITED"
        # 無効idp_tokenの連打は401のまま(IdP検証後INCRのため — design §2.5)
        invalid = await api_client.post(
            "/v1/auth/token", json={"provider": "google", "idp_token": "garbage"}
        )
        assert invalid.status_code == 401, invalid.text
        assert invalid.json()["error"]["code"] == "INVALID_IDP_TOKEN"
    finally:
        await _cleanup(db_engine, user_id, subject)


# --- §4.2-7 /health は対象外 ---


async def test_9_health_is_not_rate_limited(api_client):
    for i in range(65):
        resp = await api_client.get("/health")
        assert resp.status_code == 200, i
```

- [ ] **Step 2: 収集試験の構文確認** — `cd backend && uv run pytest --collect-only tests/integration/test_ratelimit_api.py -q` → 9件が収集される(collect-onlyはcompose不要)。`make lint` も通す

- [ ] **Step 3: unit全緑の最終確認**

```bash
make lint && make test
```

→ lintクリーン・unit全緑。既存試験(intents CRUD・auth・users・parse)が無修正で含まれていることを確認。

- [ ] **Step 4: ファイル名一意性の確認(STATUS運用ルール5)**

```bash
find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d
```

→ 空(出力なし)であること。

- [ ] **Step 5: コミット**

```bash
git add backend/tests/integration/test_ratelimit_api.py
git commit -m "test(ratelimit): 429/422のci環境統合試験9件を追加(M1 ws-4 Task 6)"
```

---

## Task 7: 報告ファイルの作成

**Files:**
- Create: `docs/plans/M1/ws-4-report.md`

- [ ] **Step 1: 報告ファイルを書く** — 内容は§「報告形式」のテンプレートどおり。コミット一覧は `git log --oneline main..HEAD` で取得する

- [ ] **Step 2: コミット**

```bash
git add docs/plans/M1/ws-4-report.md
git commit -m "docs: M1 ws-4実装報告(M1 ws-4 Task 7)"
```

---

## 完了条件(テストで証明できる形)

agent3の完了時点で以下(1)〜(4)を満たす。(5)〜(7)はスーパーバイザー検証項目。

1. **unit全緑**: `make lint` クリーン・`make test` 全緑。内訳として以下が存在する:
   - `tests/unit/ratelimit/test_store.py`(6件): INCR積算・TTL4種・キー形式・subjectハッシュ化・バケット/ユーザー分離・auth:名前空間分離
   - `tests/unit/ratelimit/test_limiter.py`(9件): 60件目OK・61件目429・429後もカウント進行(INCR先行)・失敗作成の消費・種別分離・ユーザー分離・subjectハッシュ・Redis断絶503(fail-closed)・上限差し替え・active_limit
   - `tests/unit/ratelimit/test_jst_boundary.py`(4件): JST 0時・時・分の各バケット切替(リセット再現)・JST暦日付(UTC非依存)
   - `tests/unit/intents/test_intents_service.py` 追記(11件): 作成429・Active満杯422(uow内ロック→COUNT順)・draft除外・422Active優先(必須3より先)・更新429(404より先)・draft→active化422・resume 429・pause非計上・limiter=None回帰
   - `tests/unit/test_rate_limit_wiring.py`(1件): v1全8パス11ルートの依存差し替え・対象外3経路(/health・token・refresh)の確認
   - `tests/unit/auth/test_service_token.py` 追記(4件)・`test_service_refresh.py` 追記(2件): IdP検証後INCR・61回目429・401優先(無効idp_tokenは計上外)・limiter=None回帰・rotate後INCR
   - **既存試験がすべて無修正で緑**(ws-3 CRUD unit・auth unit・users unit・parse unit・routes unit・arch test 2件)
2. **arch test**: `test_arch_no_direct_time.py` が自動的にratelimit配下も検査し緑(バケット導出がClock経由であることの構造証明)
3. **ファイル名一意性**: `find backend/tests -name "test_*.py" | awk -F/ '{print $NF}' | sort | uniq -d` が空
4. **マイグレーション不変**: `alembic/versions/` に差分なし(スキーマ変更ゼロ)
5. **(スーパーバイザー)統合試験**: `docker compose build api` 実行後 `make test-ci` で `test_ratelimit_api.py` 9件が緑 — 作成21件目429・更新7回目429(別Intent分離込み)・API 61req目429+401優先+別ユーザー受理・Active 6件目422(pause解放・draft→active化・resume・期限切れ除外込み)・resume連打429・auth token 61回目429+無効idp_token 401・/health対象外
6. **(スーパーバイザー)既存integration回帰**: test_intents_crud_api・test_auth_api・test_users_api・test_intents_parse_api・test_geo・test_schema がデフォルト上限のまま無修正で緑(検算表はTask 6参照)
7. **(スーパーバイザー)ログ検証**: 429/422時に `latch.ratelimit` / `latch.intents` ログへcodeのみ出力され、text・subject・トークンが混入しない

## 報告形式

成果物の報告は `docs/plans/M1/ws-4-report.md` に書く。以下の構成で:

```markdown
# M1 ws-4(レート制限)実装報告

- 作業単位: ws-4 レート制限(08 §5.4・04 §5)
- 実装: agent3(worktree: ws-4-ratelimit)
- 日付: <実施日>
- 計画: docs/plans/M1/ws-4-plan.md / 設計: docs/plans/M1/ws-4-design.md

## コミット一覧

(git log --oneline main..HEAD の出力をそのまま貼る)

## 実装サマリ

- ratelimit/パッケージ(errors・store・limiter・deps): <概要>
- intentsフック(Active数422・作成/更新429): <概要・検証順序の実装>
- API全体60req/分(依存差し替え・lifespan): <概要・anonフォールバックキー>
- auth系429(provider+subject): <概要>

## 検証結果

- make lint: <結果>
- make test(unit): <件数・結果>
  - 新規: test_store 6 / test_limiter 9 / test_jst_boundary 4 /
    test_intents_service追記 11 / test_rate_limit_wiring 1 /
    auth unit追記 6
  - 既存: 無修正で緑(ws-3 CRUD・auth・users・parse)
- test-ci: **スーパーバイザー検証待ち**(STATUS運用ルール1・4)
  - 検証手順: cd <リポジトリルート> && docker compose build api && make test-ci
  - 期待: test_ratelimit_api.py 9件緑 + 既存integration回帰
    (test_intents_crud_api・test_auth_api・test_users_api・test_intents_parse_api)
- ファイル名一意性: find ... | uniq -d → 空
- マイグレーション: 差分なし

## 既存試験の保全検算(api 60req/分)

(計画Task 6の検算表を写し、実測と食い違いがあれば記録)

## 知見・残余リスク

- <実装中に判明したこと・designからの補完(anonフォールバックキー・
  refresh 429時のトークン喪失の注意など)を記録>
```

報告ファイルには鍵・上限値・検証手順を含めること。秘密情報(トークン類)は含めない。

---

## 補遺: designからの実装補完(計画がdesignの字義に対して決めた細目)

designが指定せず計画が決めた点。レビュー・検証時に参照:

1. **api 60req/分の未登録フォールバックキー** `rl:api:anon-{sha256(provider:subject)}`(design §2.7は`rl:api:{user_id}`形式のみ記載)。claimsにuser_idが無く(C3)未登録JWT保持者のparse連打を放置しないため(08 §5.4趣旨)
2. **INCRの位置はuser解決直後・uow開始前**(design §2.4はフロー概略)。Redis I/OをDBトランザクション内に入れない
3. **Active化系の検証一式をuowトランザクション内へ移動**(design §2.4の「422Activeが必須3より先」と§2.3の「COUNT→書き込み直列化」の両立)。ジオコーディングは別コネクションのgeofeatures参照のみでusers行ロックと競合しない
4. **check_activeの判定はservice内**(`limiter.active_limit`プロパティで閾値を取得し`ActiveIntentLimitError`をserviceがraise)。ratelimitパッケージからintentsへのimport依存を作らない(design §2.1のパッケージ境界保持)
5. **refresh系429はrotate後にINCR**(design §2.5「IdP検証の直後」— refreshではrotateが検証を兼ねる)。429時の新refreshトークン喪失(旧トークン再提示は族失効→401)は正当利用で発生しない(access TTL 1時間)ため受容
6. **unit試験のRedisスタブはfakeredis**(design §3.1は「スタブRedis(dict)」と表記。tests/unit/auth/test_sessions.pyの前例どおりRedisコマンド解釈が正確なfakeredisを採用)
