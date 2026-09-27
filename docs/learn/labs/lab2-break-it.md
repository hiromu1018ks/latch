# Lab 2: わざと壊して学ぶ(build-and-break)

- 種別: チュートリアル(予測→実行→観察→復元のサイクルを回す)
- 前提: Lab 1 観了・`concepts/clock-and-testing.md` 読了。所要目安: 30〜40分
- 次に読むもの: 実装が再開したら追加される概念ノート(認証・ジオコーディング)

このLabの目的: **理解は「読む」より「変えて予測が当たるか確かめる」で生まれる**。
テストが約2秒で回るこのリポジトリは、壊して学ぶ練習場として最高の環境です。

## 0. 安全手順(必ず最初に読む)

変更を加える前に、必ず現在の状態を確認します:

```bash
git status --short
# (何も出なければクリーン。出力があったら Lab を始める前に大人に...ではなく
#  スーパーバイザーのClaudeに相談してください)
```

各実験は「変更→`make test`→観察→**復元**」で完結します。復元は次の1つのコマンド:

```bash
git checkout -- backend/
```

(= backend/ 配下の変更を全部捨てて元に戻す、の意味。gitの用語では「作業ツリーの破棄」)

不安なら、実験用ブランチを作ってから始めてもかまいません:

```bash
git switch -c learn-lab2
# ...実験...
git switch main && git branch -D learn-lab2   # 終わったら片付け
```

## 実験1: 本物の時計を止めてみる

**予測**: `SystemClock.now()` が常に「2020年1月1日」を返すようにしたら、
どのテストが赤くなるでしょう? (考えてから実行してください)

`backend/src/latch/core/clock.py` の `SystemClock` を編集:

```python
class SystemClock(Clock):
    """本番用。実時間の現在時刻を返す(実時間参照が許される唯一の実装)。"""

    def now(self) -> datetime:
        return datetime(2020, 1, 1, tzinfo=UTC)   # ← こう変える
```

実行:

```bash
make test
```

観察(2026-09-27に実際に検証済みの結果):

- **赤になるのは1件だけ**: `tests/unit/test_clock.py::test_system_clock_returns_tz_aware_utc`
  (「返す時刻は現在時刻とだいたい一致する」という検査があるため)
- **残り208件は緑のまま** — /health の試験などは FakeClock を使うので影響なし

これは重要な性質です: **DI(時計を外から渡す仕組み)のおかげで、本番用部品を壊しても
偽の部品を使う試験は壊れない**。逆にいえば「偽物が本物の振る舞いを正しく真似ている」
ことを、この分離が保証しやすくしています。

復元: `git checkout -- backend/`

## 実験2: /health の時刻を固定文字列にする

**予測**: `server_time` が常に `"hello"` を返したら?

`backend/src/latch/main.py` の health 関数の return を:

```python
    return {"status": "ok", "server_time": "hello"}
```

実行して観察:

```bash
make test
```

- **4件が赤になります**(すべて `tests/unit/test_app_health.py`):
  `test_health_returns_ok_and_clock_time`(FakeClockの時刻と一致するはずの検査)、
  `test_health_server_time_is_iso8601_utc`(`"hello"` はISO8601として解釈できない)、
  `test_health_create_app_injection` と `test_health_dependency_override`
  (Clock差し替えの両経路の検査 — server_timeが固定なら差し替えの意味が失われるため)

1行変えるだけで4つの試験が「仕様と違う」と教えてくれます。これが**テスト=仕様書**の意味です。

復元: `git checkout -- backend/`

## 実験3: 規律破りを機械が見つける

**予測**: `main.py` に `datetime.now()` を直接書いたら?

`backend/src/latch/main.py` の `logger = logging.getLogger(...)` の行より上に足します:

```python
from datetime import datetime
_BAD = datetime.now()
```

実行して観察:

```bash
make test
```

- `test_arch_no_direct_time` が赤になり、「main.py が datetime.now を使っている」旨の
  メッセージが出ます(2026-09-27検証済み: 赤はこの1件のみ)
- これは「時刻はClock経由」というプロジェクトの規律(C2)を守る番人の試験です
  (概念ノート「規律の強制」の節を参照)

補足(正直な限界の話): 番人は**文字列の形で探している**ため、
`from datetime import datetime as _dt` のような**別名付け**をすると `_dt.now()` は
検出をすり抜けます(実装レビューで既知の制限として記録済み)。だから番人は完璧ではなく、
「規律を守る文化+番人」の二重構えである、と理解してください。

復元: `git checkout -- backend/`

## 実験4(応用): 時刻を進めて期限切れを作る

テストコード側を書いてみます。`backend/tests/unit/` に `test_my_learning.py` を作成:

```python
"""学習用の自分試験(Lab 2 実験4)。終わったら削除してよい。"""

from datetime import UTC, datetime, timedelta

from latch.core.clock import FakeClock


def test_deadline_passes_after_advance():
    fake = FakeClock(datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC))
    deadline = fake.now() + timedelta(hours=1)

    assert fake.now() < deadline                      # まだ間に合う
    fake.advance(timedelta(hours=1, seconds=1))       # 時間を進める
    assert fake.now() >= deadline                     # 期限切れ
```

実行:

```bash
cd backend && uv run pytest tests/unit/test_my_learning.py -v
```

緑になったら成功です。**待ち時間ゼロで「1時間後」を作れた**ことと、
pytestのテストがただの関数であることを体感してください。

遊び終わったら削除:

```bash
rm backend/tests/unit/test_my_learning.py   # リポジトリのルートで実行の場合
```

## 振り返り

- 「予測→実行→観察」のうち、予測が外れた実験はあったか? 外れたらそれが一番の学びどころ
- 「部品を外から渡す(DI)」と「テストが速い」の関係を自分の言葉で
- `git checkout -- backend/` が何をしたか

最後に `git status --short` が空であることを確認してLab終了です。
