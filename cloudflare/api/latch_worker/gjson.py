"""D1 TEXT(JSON)列の読取helper(本体実装)。"""

from __future__ import annotations

import json
from typing import Any


def loads_json(text: str | None, default: Any) -> Any:
    """JSON列を読む。None/空は既定値・文字列以外はそのまま(dict化は呼び出し側)。"""
    if text is None or text == "":
        return default
    if isinstance(text, (dict, list)):
        return text
    return json.loads(str(text))
