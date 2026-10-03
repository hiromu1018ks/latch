"""clock_cli(制御Clock操作CLI)のunit試験(M4 ws-1 design §4.1)。

Redisはfakeredis(同期)へ差し替え、実Redis不要でキーへの書込値を検証する。
"""

import json
from datetime import UTC, datetime

import fakeredis
import pytest

import latch.core.clock_cli as clock_cli
from latch.core.clock import ControlledClock, SystemClock

CONTROL_KEY = "latch:clock:control"
JST_10_04_0 = datetime.fromisoformat("2026-10-04T00:00:00+09:00")
UTC_10_03_15 = datetime(2026, 10, 3, 15, 0, 0, tzinfo=UTC)


def _clock() -> ControlledClock:
    return ControlledClock(fakeredis.FakeRedis(decode_responses=True))


def test_parse_iso_accepts_jst_offset():
    dt = clock_cli._parse_iso("2026-10-04T00:00:00+09:00")
    assert dt.isoformat() == "2026-10-04T00:00:00+09:00"


def test_parse_iso_rejects_naive():
    # naiveは拒否(JST意図の入力がUTC解釈される9時間ずれのタイムボム防止)
    with pytest.raises(ValueError):
        clock_cli._parse_iso("2026-10-04T00:00:00")


def test_parser_advance_accepts_float_seconds():
    args = clock_cli.build_parser().parse_args(["advance", "86400.5"])
    assert args.seconds == 86400.5


def test_run_set_writes_anchor_json():
    clock = _clock()
    assert clock_cli.run_set(clock, JST_10_04_0) == 0
    base = clock.read_base()
    assert base[1] == UTC_10_03_15.timestamp()  # JST入力がUTC基準点へ正規化
    assert abs(base[0] - SystemClock().now().timestamp()) < 60


def test_run_advance_moves_virtual_and_prints(capsys):
    clock = _clock()
    clock_cli.run_set(clock, JST_10_04_0)
    assert clock_cli.run_advance(clock, 86400) == 0
    base = clock.read_base()
    assert base[1] == pytest.approx(
        datetime(2026, 10, 4, 15, 0, 0, tzinfo=UTC).timestamp()
    )
    assert "advanced by 86400.0s" in capsys.readouterr().out


def test_run_show_uncontrolled_prints_unset(capsys):
    assert clock_cli.run_show(_clock()) == 0
    out = capsys.readouterr().out
    assert "unset" in out
    assert "virtual now" in out


def test_run_show_controlled_prints_base(capsys):
    clock = _clock()
    clock_cli.run_set(clock, JST_10_04_0)
    assert clock_cli.run_show(clock) == 0
    out = capsys.readouterr().out
    assert "control base" in out
    assert CONTROL_KEY in out


def test_main_set_dispatches_to_fakeredis(monkeypatch):
    clients = []

    def _fake_build() -> ControlledClock:
        client = fakeredis.FakeRedis(decode_responses=True)
        clients.append(client)
        return ControlledClock(client)

    monkeypatch.setattr(clock_cli, "_build_clock", _fake_build)
    assert clock_cli.main(["set", "2026-10-04T00:00:00+09:00"]) == 0
    data = json.loads(clients[0].get(CONTROL_KEY))
    assert data["virtual"] == UTC_10_03_15.timestamp()


def test_main_set_rejects_naive_with_exit_2(monkeypatch, capsys):
    monkeypatch.setattr(clock_cli, "_build_clock", lambda: _clock())
    assert clock_cli.main(["set", "2026-10-04T00:00:00"]) == 2
    assert "error" in capsys.readouterr().out


def test_main_show_dispatches(monkeypatch, capsys):
    monkeypatch.setattr(clock_cli, "_build_clock", lambda: _clock())
    assert clock_cli.main(["show"]) == 0
    assert "virtual now" in capsys.readouterr().out
