"""FastAPI依存: app.state.clock へのアクセスを一元化(design §2.4 DI方式)。"""

from fastapi import Request

from latch.core.clock import Clock


def get_clock(request: Request) -> Clock:
    return request.app.state.clock
