"""create_app・worker入口のClock既定経路(M4 ws-1 design §2.2の組立差し替え)。

既存DI試験(clock引数明示)は無傷であることの裏面として、未注入時に
make_clock(Settings)を通ること(=LATCH_CLOCK_MODE設定がapi/workerの両入口で
効くこと)をピンする。controlled構築はredis-pyの遅延接続のためI/Oなし。
"""

import latch.main as main_module
from latch.core.clock import ControlledClock, SystemClock
from latch.main import create_app
from latch.settings import Settings


def test_create_app_default_clock_goes_through_make_clock(monkeypatch):
    sentinel = SystemClock()
    seen = {}

    def _spy(settings):
        seen["settings"] = settings
        return sentinel

    monkeypatch.setattr(main_module, "make_clock", _spy)
    app = create_app()
    assert app.state.clock is sentinel
    assert isinstance(seen["settings"], Settings)


def test_create_app_default_is_system_clock_when_mode_unset(monkeypatch):
    monkeypatch.delenv("LATCH_CLOCK_MODE", raising=False)
    app = create_app()
    assert isinstance(app.state.clock, SystemClock)


def test_worker_main_builds_worker_with_make_clock(monkeypatch):
    import latch.worker.__main__ as worker_main

    captured = {}

    class _StubWorker:
        def __init__(self, settings=None, clock=None):
            captured["settings"] = settings
            captured["clock"] = clock

        def request_shutdown(self) -> None:
            return None

        async def run(self) -> None:
            return None

    monkeypatch.setattr(worker_main, "Worker", _StubWorker)
    monkeypatch.delenv("LATCH_CLOCK_MODE", raising=False)
    worker_main.main()
    assert isinstance(captured["clock"], SystemClock)


def test_worker_main_controlled_mode_uses_redis_url(monkeypatch):
    # staging composeの経路: LATCH_CLOCK_MODE=controlled + LATCH_REDIS_URLで
    # worker入口がControlledClockを構築すること(遅延接続のためI/Oは起きない)
    import latch.worker.__main__ as worker_main

    captured = {}

    class _StubWorker:
        def __init__(self, settings=None, clock=None):
            captured["settings"] = settings
            captured["clock"] = clock

        def request_shutdown(self) -> None:
            return None

        async def run(self) -> None:
            return None

    monkeypatch.setattr(worker_main, "Worker", _StubWorker)
    monkeypatch.setenv("LATCH_CLOCK_MODE", "controlled")
    monkeypatch.setenv("LATCH_REDIS_URL", "redis://127.0.0.1:16379/0")
    worker_main.main()
    assert isinstance(captured["clock"], ControlledClock)
