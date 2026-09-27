# 概念ノート: Clock(時計)とテスト — このリポジトリ一番の見どころ

- 種別: 解説(なぜそう設計されているか)
- 前提: `01-reading-paths.md` トレース1 読了
- 次に読むもの: `labs/lab2-break-it.md`
- 対応コード: `backend/src/latch/core/clock.py`(61行・全部読める量です)
- 設計の根拠: `docs/plans/M0/scaffold-design.md` §2.4(判断の詳細はこちら)

## 問題: 「今何時?」はなぜ特別なのか

このサービスには時刻で決まる処理がたくさんあります:

- Intentの有効期限が切れたか(期限判定)
- 提案への回答締め切り
- 「同じ30分の枠」にいるかの判定
- 連続操作をまとめる10秒の窓
- 日本時間の0時にリセットする処理

ここで各所が `datetime.now()`(実時間)を直接呼ぶと、**テストで困ります**。
「10分後に期限切れ」を試すのに10分待つ? 時間ぎりぎりの処理はどう再現する?

解決策: 「時刻を知る」経路を1本にまとめ、**中身を差し替え可能にする**。それが Clock です。
プロジェクトの依存制約C2として最優先で実装されました(12-development-roadmap.md 第2節)。

## 設計: 3つのクラス

`clock.py` から要点だけ引きます:

```python
JST = timezone(timedelta(hours=+9))   # 日本に夏時間なし。固定オフセットで確定

class Clock(ABC):                     # 「時計とは何か」の定義(抽象基底クラス)
    @abstractmethod
    def now(self) -> datetime: ...    # tz-aware UTC の現在時刻(実装は子クラス)

    def jst_date(self) -> date:       # now() を日本の暦日付へ変換
        return self.now().astimezone(JST).date()

class SystemClock(Clock):             # 本番用。実時間を返す「本物の時計」
    def now(self) -> datetime:
        return datetime.now(UTC)

class FakeClock(Clock):               # テスト用。「好きな時刻の世界」を作る時計
    def set(self, when): ...          # 任意の時刻へ移動(過去にも戻れる)
    def advance(self, delta): ...     # 時刻を delta だけ進める
```

### ABC(抽象基底クラス)とは

「この形のクラスは必ず `now()` を持つ」という**契約**です。
Clockを真似た新しい時計を作るとき、`now()` の書き忘しを import した瞬間に教えてくれます
(用語: 継承・抽象メソッド。詳細はPython公式の abc モジュール解説へ)。

### なぜ tz-aware UTC に統一するのか

時刻の表現には「タイムゾーン付き(aware)」と「なし(naive)」があります。
naive は比較や変換で静かなバグを生むため、このプロジェクトでは:

- 内部の保持・比較は **UTC(aware)で統一**
- 日本の暦日付が必要な処理だけ `jst_date()` で変換して使う

FakeClock は naive を渡すと例外で拒否し、非UTCのawareは自動でUTCへ正規化します
(これは実装中にレビューで指摘されて追加された堅牢化です — `git log` の
`c4f60b3` を参照)。

## DI(依存の注入): 時計を「渡す」しくみ

部品が自分で時計を作ると差し替えられません。**外から渡す**ことで、本番と試験で
中身だけ入れ替えられます。このプロジェクトの渡し方:

- **API**: `create_app(clock=...)` で受け取り `app.state.clock` へ → 各endpointは
  `Depends(get_clock)` で受け取る(main.py の /health が最小例)
- **Worker**: 起動時に1回 `SystemClock()` を明示的に構築(worker/main.py)
- **試験**: `create_app(clock=FakeClock(...))` か、`app.dependency_overrides[get_clock]`
  の差し替え(両方の経路に試験があります: test_app_health.py)

「DI」という言葉は仰々しいですが、実体は**「使う部品は外から渡す。作らせない」**
というただのルールです。

## テストで何がうれしいか(実例)

`tests/unit/test_clock_reproducibility.py` から、期限切れの再現:

```python
deadline = fake.now() + timedelta(hours=1)      # 1時間後が締め切り
assert fake.now() < deadline                     # まだ期限内
fake.advance(timedelta(hours=1, seconds=1))      # 時間を1時間1秒進める
assert fake.now() >= deadline                    # 期限切れ!
```

**待ち時間ゼロ・いつ実行しても同じ結果**。これが「決定的なテスト」です。
他にも、0時の境界・月初・「過去の日時は拒否する」検証が同じ調子で書かれています。

## 規律の強制: arch test(建築試験)

「Clockを経由する」は口約束ではなく**機械的に強制**されています。
`tests/unit/test_arch_no_direct_time.py` は製品コード全体を走査し、
`datetime.now` / `time.time` 等の直接参照を見つけたら失敗します(例外は clock.py のみ)。

つまり誰か(未来のあなたを含む)が規約を破るコードを書いた瞬間、
`make test` が赤くなって気づけます。**ルールはテストで守る**という
このプロジェクトの反復的な手法の代表例です。

## このノートで扱わなかったこと

- pytestのフィクスチャの仕組み(トレース5で予定)
- asyncio(async/await)の詳細
- なぜABCでProtocolにしないのか → `docs/plans/M0/scaffold-design.md` §2.4
