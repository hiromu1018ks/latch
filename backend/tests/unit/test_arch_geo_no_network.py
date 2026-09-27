"""geo配下のネットワークimport禁止(04 第3節「位置情報を外部サービスへ送る
経路を持たない」のコード検査。design §4-8)。

osmium自体のimportは許可(ローカルファイル読み取りのみに使用 — design §1.5)。
"""

from pathlib import Path

GEO = Path(__file__).resolve().parents[2] / "src" / "latch" / "geo"

FORBIDDEN_TOKENS = (
    "import requests",
    "import urllib",
    "import socket",
    "import httpx",
    "import aiohttp",
    "import http.client",
)


def test_geo_imports_no_network_modules():
    offenders: list[str] = []
    for path in sorted(GEO.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            if token in text:
                offenders.append(f"{path.relative_to(GEO)}: {token}")
    assert not offenders, (
        "geo配下でネットワーク経路を開いてはならない(04 第3節・外部送信ゼロ): "
        + ", ".join(offenders)
    )
