"""プロンプト定数のピン留め(design §2.6・§4.1-1)。

docs/07-jev-llm-spec.md の最初の```textブロックと完全一致させる — プロンプト
変更は仕様変更(07 §2改版)であり、本試験の失敗として検出する。ゲート(G1)
再実行の起点(09 §4.3)。
"""

import re
from datetime import date
from pathlib import Path

from latch.intents import PARSER_SYSTEM_PROMPT, format_parser_system_prompt

DOCS_07 = Path(__file__).resolve().parents[4] / "docs" / "07-jev-llm-spec.md"


def _first_text_block(doc: str) -> str:
    """07 §2のプロンプト本文(ファイル中最初の```textブロック)を取り出す。"""
    match = re.search(r"```text\n(.*?)\n```", doc, flags=re.DOTALL)
    assert match is not None
    return match.group(1)


def test_prompt_is_pinned_to_docs_07_section2():
    doc = DOCS_07.read_text(encoding="utf-8")
    assert PARSER_SYSTEM_PROMPT == _first_text_block(doc)


def test_prompt_contains_current_date_placeholder_exactly_once():
    assert PARSER_SYSTEM_PROMPT.count("{current_date}") == 1


def test_formatter_substitutes_iso_date():
    formatted = format_parser_system_prompt(date(2026, 9, 27))
    assert "2026-09-27" in formatted
    assert "{current_date}" not in formatted
    # ほかの{...}(出力JSONスキーマ)が壊れていない
    assert '"category"' in formatted
    assert '"ng_unverifiable"' in formatted
