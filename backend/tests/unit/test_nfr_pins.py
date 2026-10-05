"""ws-7の静的ピン(M4 ws-7)。override・Makefile・pyproject・lifespan wiring。

既定環境(compose.staging.yaml等)は無変更のため・「既定に漏れていないこと」
の無傷ピンも本ファイルが担う(loadgen composeピンと同一作法)。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_main_lifespan_registers_lag_alert_when_enabled():
    """lifespanの登録コードが在ることの静的ピン(lifespan発火unitは存在しない)。"""
    text = (ROOT / "backend" / "src" / "latch" / "main.py").read_text(encoding="utf-8")
    assert "settings.alert_queue_enabled" in text
    assert "QueueLagAlert(" in text
    # finallyでの停止・クローズ(event_relayと同型の規律)
    assert "lag_alert_stop.set()" in text
    assert "lag_alert_task.cancel()" in text
    assert "lag_alert_redis.aclose()" in text
