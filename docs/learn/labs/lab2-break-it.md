# Lab 2 わざと壊して学ぶ: 予測→実行→観察→復元のサイクル

- 種別: チュートリアル(手を動かす。このLabではコードを一時的に壊します)
- 前提知識: Lab 1・第3章(Clockとテスト)。特に3.5(DI)と3.7(番人テスト)
- 所要目安: 30〜40分
- このLabでできるようになること: 「小さく変えて、テストで予測を確かめる」開発の基本ループを
  体で覚える。失敗メッセージの読み方と、元に戻す手順に慣れる
- 次に読むもの: `concepts/04-users-write-path.md`(第4章。学んだループを、書き込みの経路で使い回します)

## 0. なぜ「壊す」のが学びになるのか

コードを読むだけの理解は、実際に挙動を当てられるか試すまで自分のものになりません。
**「これを変えたら、この試験が赤くなるはず」と予測して、実行して、当たったか確かめる**。
この1サイクルが、読解を確信に変えます。しかもこのリポジトリのテストは467件が約3秒で回るので、
何度でも試せます。壊すのが怖いときの安心材料も含めて、まず安全手順を整えます。

### 安全手順(必ず最初に)

最初に、今の状態が「変更が1つもない状態(クリーン)」か確認します。

```bash
git status --short
```

何も出力されなければクリーンです。何か表示されたら、自分の変更が残っているので、
このLabの前に片付けてください(分からなければスーパーバイザーのClaudeに聞いてください)。

このLabの各実験は「変更 → `make test` → 観察 → 復元」で完結します。復元は次の1コマンドです。

```bash
git checkout -- backend/
```

意味は「`backend/` 配下の変更を全部捨てて、最後のコミット時点に戻す」。gitは変更を
コミットしていない限り、この1コマンドで簡単に元に戻せます。**コミットしたものだけが
永続する**。だから、コミットしなければ何を試しても安全です。

さらに慎重に行きたい場合は、実験用の分身(ブランチ)を作ってから始める方法もあります。

```bash
git switch -c learn-lab2      # learn-lab2 という名前のブランチを作って移動
# ...実験...
git switch main               # 元のブランチに戻る
git branch -D learn-lab2      # 実験ブランチを削除
```

ブランチは「同じリポジトリのもう一つの作業机」です。mainという本棚を汚さずに試せます。

## 実験1: 本物の時計を止める

**予測します**。`SystemClock.now()` が、どんなときでも「2020年1月1日」を返すように変えたら、
どの試験が赤くなるでしょう? 第3章3.5(DI)を思い出して、考えてから進んでください。

(ヒント: `/health` の試験が使う時計は、`create_app(clock=...)` で渡される FakeClock です)

`backend/src/latch/core/clock.py` の SystemClock を、次のように変えます。

```python
class SystemClock(Clock):
    """本番用。実時間の現在時刻を返す(実時間参照が許される唯一の実装)。"""

    def now(self) -> datetime:
        return datetime(2020, 1, 1, tzinfo=UTC)   # ← こう変える
```

実行します。

```bash
make test
```

**期待される結果**(2026-09-28に再実行して確認):

```
FAILED tests/unit/test_clock.py::test_system_clock_returns_tz_aware_utc - ass...
================= 1 failed, 466 passed, 94 deselected in 3.19s =================
```

**なぜこうなるか**。赤になったのは `test_clock.py` の「SystemClockはtz-aware UTCで、現在時刻と
だいたい一致する値を返す」という試験1件だけです。2020年固定の時計は「現在時刻とだいたい一致」を
満たせません。一方、`/health` の試験群を含む残り466件は緑のままです。それらは
`create_app(clock=FakeClock(...))` で偽時計を使っているため、本物の時計の壊れ方に影響しないのです。
**依存が差し替え可能に分離されているので、壊れ方が局所に閉じる**。これがDIの実利です。

失敗の詳細を見たい場合は、1件だけ指定して実行します。

```bash
cd backend && uv run pytest tests/unit/test_clock.py -v
```

失敗メッセージには「どのassertが・どんな値の違いで落ちたか」が出ます。この読み方に慣れるのが
このLabの副目的です。

**復元します**: `git checkout -- backend/`(このあとの実験でも毎回同じです)

## 実験2: /health の時刻を固定文字列にする

**予測します**。 `server_time` が常に `"hello"` を返したら、何件赤になるでしょう?

`backend/src/latch/main.py` の health 関数の return を1行変えます。

```python
    return {"status": "ok", "server_time": "hello"}
```

実行: `make test`

**期待される結果**(実検証済み): `test_app_health.py` の **4件** が赤になります。

- `test_health_returns_ok_and_clock_time` — FakeClockの時刻と一致するはず、の検査
- `test_health_server_time_is_iso8601_utc` — `"hello"` はISO8601形式として解釈できない
- `test_health_create_app_injection` と `test_health_dependency_override` —
  Clock差し替えの**両経路**(第3章3.5)の検査。server_timeが固定なら、どちらの経路で
  差し替えても結果が変わらない=差し替えが効いていないことがバレる

1行の変更に対して4件が反応するのは、**試験が仕様の目録になっている**からです
(第3章3.6)。「/healthはClockに由来する時刻をISO8601で返す」という仕様の側面が4つあり、
それぞれに番人がいる、と読めます。

## 実験3: 規律破りを番人に見つけてもらう

**予測します**。第3章3.7の番人テストは、`datetime.now()` を直接書いたら反応するでしょうか?

`backend/src/latch/main.py` の `logger = logging.getLogger(...)` という行の**上**に、
次の2行を足します。

```python
from datetime import datetime
_BAD = datetime.now()
```

実行: `make test`

**期待される結果**(実検証済み): 赤になるのは `test_arch_no_direct_time` の1件だけです。
メッセージに「main.py が datetime.now を使っている」旨が出ます。製品コードで実時間を
直接参照してよいのは `clock.py` の1行だけ、という規律(C2)の番人が、main.pyの2行目で
反応したわけです。

**正直な限界も試しておきましょう**。今度は同じ場所を、別名importに変えてみてください。

```python
from datetime import datetime as _dt
_BAD = _dt.now()
```

実行すると、**今度は緑のまま**のはずです。番人は `datetime.now` という文字の並びを探す
ので、`_dt.now()` という別の書き方は検出をすり抜けます。この限界は実装時のレビューで
把握・記録済みです(第3章3.7)。番人は完璧ではない、という実感は、過信を防ぐ知識として
コスト以上の価値があります。

## 実験4(応用): 自分でテストを書く

テストを読むだけでなく、書いてみます。`backend/tests/unit/test_my_learning.py` を
新規作成してください。

```python
"""学習用の自分試験(Lab 2 実験4)。終わったら削除してよい。"""

from datetime import UTC, datetime, timedelta

from latch.core.clock import FakeClock


def test_deadline_passes_after_advance():
    fake = FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))
    deadline = fake.now() + timedelta(hours=1)

    assert fake.now() < deadline                      # まだ間に合う
    fake.advance(timedelta(hours=1, seconds=1))       # 時間を1時間1秒進める
    assert fake.now() >= deadline                     # 期限切れ
```

実行します。

```bash
cd backend && uv run pytest tests/unit/test_my_learning.py -v
```

緑になったら成功です。確かめてほしいことは2つ。**待ち時間ゼロで「1時間後」を作れた**こと。
そして、pytestのテストが「`test_`で始まるただの関数+assert」であること。
試しに `deadline` の行を `+ timedelta(days=1)` に変えたり、assertの不等号を逆にしたりして、
赤くなる様子も観察してみてください(TDDで最初に赤を見る体験、第3章3.6)。

遊び終わったら削除します。

```bash
rm backend/tests/unit/test_my_learning.py
```

## 5. 振り返り

最後に `git status --short` が空であることを確認してLab終了です。

ノートに書いておくべきこと:

- 予測が当たった実験と外れた実験。**外れた方こそ宝物**です。何を誤解していたか書き留める
- 「1行の変更に4件が反応する」と「1件しか反応しない」の違いが何から来るか(DIの分離と、
  仕様の側面数)
- `git checkout -- backend/` が何をしてくれたか。コミットしていない変更は簡単に捨てられること
- 番人が検出できるものとできないもの

このLabで身につけた「変えて→予測して→確かめる」は、この教科書の残りの章でも、
実装が再開された後も、ずっと使う基本技術です。
