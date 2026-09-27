"""C2強制のarch test(12 M0・G0「コード検査」の毎コミット自動化。design §4.3)。

backend/src/latch/ 配下の製品コードで実時間への直接参照を禁止する。
唯一の例外は core/clock.py(SystemClock の実装場所)。
tests/ はスキャン対象外(実時間の使用が正当なのは試験のみ)。
"""

from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "latch"

# datetime.now / datetime.utcnow / date.today は「from datetime import datetime」
# 経由の呼び出しでもトークンにヒットする。"from time import" は
# 「from time import time」等の迂回importを検出するためのトークン。
FORBIDDEN_TOKENS = (
    "datetime.now",
    "datetime.utcnow",
    "date.today",
    "time.time",
    "time.monotonic",
    "time.sleep",
    "from time import",
)

# 実時間参照を許される唯一の場所。将来アダプタ層で待機等を限定的に許可する場合は
# ここに明示的に追加する(追加は報告ファイルに記録する)。
ALLOWED = {SRC / "core" / "clock.py"}


def test_no_direct_time_reference_in_product_code():
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            if token in text:
                offenders.append(f"{path.relative_to(SRC)}: {token}")
    assert not offenders, (
        "実時間の直接参照は禁止(C2)。Clock(latch.core.clock)経由に修正すること: "
        + ", ".join(offenders)
    )
