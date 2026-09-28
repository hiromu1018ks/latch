# Lab 3 カウンタを手で動かす: 429を作り、JST 0時を越え、壊して確かめる

- 種別: チュートリアル(手を動かす。このLabではコードを一時的に壊します)
- 前提知識: 第7章(レート制限)。特に7.4(バケットと固定窓)と7.5(INCR先行)。
  Lab 2を済んでいれば、壊し方と復元の手順はおなじみです
- 所要目安: 30〜40分
- このLabでできるようになること: 第7章で「読んだ」カウンタとJSTバケットを、
  自分の手で動かして観察する。429がどう上がり、時計を進めるとどう回復するかを
  目で確かめる。境界判定とJST暦日付の2箇所を壊して、番人テストの反応を予測する
- 次に読むもの: 第7章まででM1のサーバー側は読み終わり。ws-5(フロントエンド)の
  実装とともに追加される教材をお待ちください

## 0. このLabで扱うもの

第7章の主役は `backend/src/latch/ratelimit/` の4ファイルでした。このLabでは
その動きを3つの窓から観察します。

1. **テストを走らせる**(観察): 25件のunit テストの名前を縦に読む
2. **1件のテストを深読みする**: JST 0時の境界を作るテストを単体で動かす
3. **自分で組み立てる**: RateLimiter+偽の時計+偽のRedisを自分のスクリプトで組み、
   429を発生させてから「翌日」を作って回復させる

そのあと、Lab 2と同じ「変えて→予測→確かめる→復元」を2回やります。壊す場所は
このLabの前に必ずクリーンであることを確認してください。

```bash
git status --short
```

何も出力されなければクリーンです。実験のあとの復元は毎回これです。

```bash
git checkout -- backend/
```

## 1. 観察: 25件のテストを縦に読む

`backend/` に移動して、レート制限のunit テストだけを走らせます。

```bash
cd backend && uv run pytest tests/unit/ratelimit -v
```

期待される出力の末尾(2026-09-28に実行しました):

```
tests/unit/ratelimit/test_jst_boundary.py::test_daily_bucket_rolls_at_jst_midnight PASSED [  4%]
tests/unit/ratelimit/test_jst_boundary.py::test_hourly_bucket_rolls_on_the_hour PASSED [  8%]
tests/unit/ratelimit/test_jst_boundary.py::test_minute_bucket_rolls_each_minute PASSED [ 12%]
tests/unit/ratelimit/test_jst_boundary.py::test_date_uses_jst_calendar_not_utc PASSED [ 16%]
tests/unit/ratelimit/test_limiter.py::test_api_allows_60th_and_rejects_61st PASSED [ 20%]
tests/unit/ratelimit/test_limiter.py::test_rejected_request_is_still_counted PASSED [ 24%]
(中略: test_limiter.py と test_ratelimit_deps.py と test_store.py の各行)
============================== 25 passed in 0.09s ==============================
```

試験名を縦に読んでみてください。名前がそのまま仕様になっています(第3章3.6)。
第7章の主張と見比べると、こんな対応になります。ノートに書き写しておくと
あとで便利です。

- `test_api_allows_60th_and_rejects_61st` — 「60件目は受理・61件目で429」(7.2の表)
- `test_rejected_request_is_still_counted` — 「429を返したリクエストもカウント済み」(7.5のINCR先行)
- `test_daily_bucket_rolls_at_jst_midnight` — 「JST 0時で日付キーが切り替わる」(7.4)
- `test_date_uses_jst_calendar_not_utc` — 「日付はUTCでなくJST暦」(7.4)
- `test_redis_failure_is_wrapped_as_503` — 「Redis断絶は503へ包む」(7.5のfail-closed)

## 2. 1件だけ動かす: JST 0時の境界

`test_daily_bucket_rolls_at_jst_midnight` だけを指定して動かします。`::` のあとに
試験名を書くと、その1件だけが走ります(Lab 1で使った手法です)。

```bash
uv run pytest tests/unit/ratelimit/test_jst_boundary.py::test_daily_bucket_rolls_at_jst_midnight -v
```

期待される出力の末尾:

```
tests/unit/ratelimit/test_jst_boundary.py::test_daily_bucket_rolls_at_jst_midnight PASSED [100%]

============================== 1 passed in 0.03s ===============================
```

0.03秒です。この試験の中では「JST 23:59で作成が1件だけOK→2件目が429→1分進めて
0時を突破→作れるようになる→昨日と今日の鍵が2本残っている」までが完走しています。
実時間で0時を待つ必要がない、というのが第3章以来のClock差し替えの実利でした。
次の節で、同じことを自分の手で組み立てます。

## 3. 自分で組み立てる: 429を作って、翌日を作る

テストを読むだけではなく、部品を自分で組みます。次の内容で
`/tmp/rl_lab.py` を作成してください(エディタでも `cat` でも構いません)。

```python
"""Lab 3: 429を発生させ、JST 0时を越えて回復する様子を見る。"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis

from latch.core.clock import FakeClock
from latch.ratelimit import RateLimiter, RateLimits
from latch.ratelimit.errors import RateLimitedError
from latch.ratelimit.store import RateLimitStore


async def main() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    clock = FakeClock(datetime(2026, 9, 28, 14, 59, 30, tzinfo=UTC))  # JST 23:59:30
    limiter = RateLimiter(
        store=RateLimitStore(redis),
        clock=clock,
        limits=RateLimits(create_per_day=2),
    )
    uid = uuid.UUID("00000000-0000-4000-8000-00000000000a")

    await limiter.check_create(user_id=uid)
    print("1件目: 受理")
    await limiter.check_create(user_id=uid)
    print("2件目: 受理")
    try:
        await limiter.check_create(user_id=uid)
        print("3件目: 受理(想定外!)")
    except RateLimitedError as e:
        print(f"3件目: 拒否 status={e.http_status} code={e.code}")

    clock.advance(timedelta(minutes=1))  # JST 0時を越える
    await limiter.check_create(user_id=uid)
    print("JST 0時を越えた直後の作成: 受理(新しい日付キー)")

    for k in sorted(await redis.keys("rl:create:*")):
        print(f"{k} = {await redis.get(k)}")
    await redis.aclose()


asyncio.run(main())
```

実行します(`/tmp` に置いても、`uv run` を `backend/` から叩けばlatchパッケージが
見つかります)。

```bash
cd backend && uv run python /tmp/rl_lab.py
```

期待される出力(2026-09-28に実行しました):

```
1件目: 受理
2件目: 受理
3件目: 拒否 status=429 code=RATE_LIMITED
JST 0時を越えた直後の作成: 受理(新しい日付キー)
rl:create:00000000-0000-4000-8000-00000000000a:20260928 = 3
rl:create:00000000-0000-4000-8000-00000000000a:20260929 = 1
```

出力を下から読んでください。最後の2行がRedisの中身です。**昨日の鍵
(`…:20260928`)の値は3のまま**残っています。429で断られた3件目もカウント済み、
という第7章7.5の説明どおりです。そして**今日の鍵(`…:20260929`)は1から始まって
いる**。「リセット」とは何も消さず、単に別の鍵を開けただけ、という7.4の説明が、
自分の手の中で起きました。

**読者の実験**: 次の予測をしてから、スクリプトを変えて確かめてください
(ノートに予測を書いてから)。

1. `RateLimits(create_per_day=2)` を `create_per_day=3` に変えたら、出力はどう
   変わるでしょう?
2. `FakeClock(...)` の時刻を `15, 0, 30`(JST 0時ちょうどの30秒後)に変えたら、
   鍵の名前はどう変わるでしょう?(`advance` の1分後は何日の鍵になる?)
3. `clock.advance(timedelta(minutes=1))` を消したら、最後の `check_create` は
   どうなるでしょう?(try/exceptで囲んで確かめてもよいです)

## 4. 実験1(壊す): 境界を1つずらす

**予測します**。上限の判定 `if count > limit:` を `if count >= limit:` に変えたら、
何が起こるでしょう?「60件目は受理・61件目で429」という仕様と照らして、
どの試験が何件赤くなるか考えてから進んでください。

`backend/src/latch/ratelimit/limiter.py` の `_check` メソッドを、こう変えます。

```python
    async def _check(self, count: int, limit: int) -> None:
        if count >= limit:
            raise RateLimitedError("rate limit exceeded")
```

実行します(リポジトリのルートで `make test` でも同じです。ここではunitだけを
指定して見やすくします)。

```bash
cd backend && uv run pytest tests/unit -q
```

期待される出力の末尾(2026-09-28に実行して確認):

```
=========================== 11 failed, 452 passed in 3.11s ===========================
```

赤になったのは **11件**。
`tests/unit/ratelimit/` の境界を扱う試験群です(`test_api_allows_60th_and_rejects_61st`
`test_rejected_request_is_still_counted` `test_61st_request_raises_429` ほか)。

**なぜこうなるか**。`>` が `>=` になると、カウントが上限に**等しくなった瞬間**に
拒否が始まります。60件目(カウント60=上限60)は本来受理されるはずなのに拒否され、
`create_per_day=1` の試験では1件目から拒否になります。「ちょうど上限まではOK」
というたった1文字の仕様を、11件の番人が別々の角度から守っていた、という読み方が
できます。1文字の変更に11件が反応するのは、Lab 2実験2と同じく、**試験が仕様の
目録になっている**例です。

**復元します**: `git checkout -- backend/`

## 5. 実験2(壊す): JSTをやめる

**予測します**。日付バケットをJSTでなくUTCで作ったら、どの試験が赤くなるでしょう?
ヒントは、7.4の「TTLの時計はUTC基準で、JSTの0時と9時間ずれる」の話です。

`backend/src/latch/ratelimit/limiter.py` の `_day_bucket` を、こう変えます
(`jst_date()` を使わず、`now()` をそのまま書式化する=UTCの日付になる)。

```python
    def _day_bucket(self) -> str:
        return self._clock.now().strftime("%Y%m%d")
```

実行します(レート制限の試験だけで十分です)。

```bash
cd backend && uv run pytest tests/unit/ratelimit -q
```

期待される結果(2026-09-28に実行して確認):

```
FAILED tests/unit/ratelimit/test_jst_boundary.py::test_daily_bucket_rolls_at_jst_midnight
FAILED tests/unit/ratelimit/test_jst_boundary.py::test_date_uses_jst_calendar_not_utc
2 failed, 23 passed in 0.11s
```

**なぜこうなるか**。赤くなる2件は、どちらも **JST暦の日付そのもの**を検査する
試験です。`test_daily_bucket_rolls_at_jst_midnight` は「UTC 14:59(=JST 23:59)から
1分進めると日付が変わる」ことを期待しますが、UTC基準ではまだ9月28日のまま——
「翌日」が作れず、0時を越えたはずの作成が429のまま失敗します。
`test_date_uses_jst_calendar_not_utc` はさらに直接的で、「UTC 2026-09-28 15:00は
JSTでは9月29日」という事実を鍵名で検査しています。

注目すべきは、**残りの23件は緑のまま**なことです。UTCでも日付バケットとして
「機能」はする——ただし「利用者の1日」とは9時間ずれた、意味の違う1日になる。
壊しても簡単には赤くならない種類の間違いを、JST暦を明示的に検査する2件の番人が
拾っている、という構図です。「なんとなく動いてしまう」バグこそ番人が要る理由です。

**復元します**: `git checkout -- backend/`

## 6. 振り返り

最後に `git status --short` が空であること(学習用の `/tmp/rl_lab.py` は
リポジトリの外なので git には出てきません)を確認してLab終了です。

ノートに書いておくべきこと:

- 429で断られたリクエストのカウントが、鍵の値(3)として残っていたこと。
  「リセット=翌日の鍵を開ける」様子を、鍵2本の出力で確認できたこと
- 予測が当たった実験と外れた実験。**外れた方こそ宝物**(Lab 2の教え)
- `>` と `>=` の1文字に11件が反応したことと、UTC化が2件しか赤にしなかったことの
  対称性——「仕様の目録」と「なんとなく動く間違い」の番人の違い
- 自分で組み立てた `RateLimits(create_per_day=2)` と `Settings` の
  `rate_limit_create_per_day=20` の対応関係(第7章7.5)

このLabで動かした部品(RateLimiter・FakeClock・fakeredis)の組み合わせは、
実装時のTDDそのものが使った形です。`docs/plans/M1/ws-4-plan.md` を開くと、
同じ部品で先に赤を作っていた痕跡が読めます。興味があれば、`git log -p
backend/src/latch/ratelimit/limiter.py` で「試験が先・実装が後」の順を確認
してみてください。
