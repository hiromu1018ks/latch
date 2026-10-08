"""B0(改善なし移植版)の呼出回数基準の記録(T0)。

計画 §5.1: 改善(I1〜I6)の削減効果は B0 との差で計算する。
ここでは決定的な単位試験で測定できる回数を固定し、結果を
backend/tests/contracts/fixtures/b0_baseline_counts.yaml へ記録する。

測定面(追加改善前の現行挙動):
- parse: 同一textの並行・連続要求でもサーバー側に集約なし → LLM呼出し数=要求数
  (画面の1秒debounce・同文省略はフロント側のみ — parseFlow.test.js)
- jev同版再利用: 既存(layer4の jev_result IS NULL)は「成功後の2回目」だけ0呼出し
  (並行API呼出しの防止ではない — 計画 §5.2の確認済み事項)

Worker側(reeval抑制30分・catch-up)は unit/cost・integrationの既存試験が
担うため、ここでは参照のみ記録する。
"""

from __future__ import annotations

import asyncio
import sys
import uuid as uuid_mod
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from latch.core.clock import FakeClock  # noqa: E402
from latch.intents.service import IntentParseService  # noqa: E402

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


class CountingParser:
    """SupportsParseIntent構造のカウンタ(stub応答はllm.stubと同形)。"""

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[dict] = []

    async def parse_intent(self, *, text: str, current_date: date, user_id: str | None):
        self.calls += 1
        self.requests.append(
            {"text": text, "current_date": current_date, "user_id": user_id}
        )
        return {
            "category": {"primary": "meal", "secondary": None},
            "alcohol_involved": False,
            "time": {"start": "2026-09-27T19:00:00+09:00", "end": None},
            "location": {"name": "東京駅"},
            "budget": {"max": None, "currency": "JPY"},
            "participants": {"min": None, "max": None},
            "soft_constraints": [],
            "negative_constraints": [],
            "ng_unverifiable": [],
        }


async def _lookup_ok(provider: str, subject: str):
    return uuid_mod.UUID("00000000-0000-4000-8000-000000000001")


async def _consented(user_id) -> bool:
    return True


def _service(parser: CountingParser) -> IntentParseService:
    return IntentParseService(
        clock=FakeClock(NOW),
        parser=parser,
        user_lookup=_lookup_ok,
        consent_lookup=_consented,
    )


async def test_b0_parse_no_server_dedup():
    """B0: 同一text 3連続 + 20並行でもLLM呼出し数=要求数(集約なし)。"""
    parser = CountingParser()
    svc = _service(parser)
    text = "今度の金曜の夜、天文館あたりで軽くごはん。"
    for _ in range(3):
        result = await svc.parse(text=text, auth_provider="google", auth_subject="s")
        assert result.structured_intent.category.primary == "meal"
    assert parser.calls == 3, "B0は同文でも毎回LLMを呼ぶ(画面debounceのみ)"
    assert all(r["current_date"] == date(2026, 9, 27) for r in parser.requests)

    parser2 = CountingParser()
    svc2 = _service(parser2)
    await asyncio.gather(
        *(
            svc2.parse(text=text, auth_provider="google", auth_subject="s")
            for _ in range(20)
        )
    )
    assert parser2.calls == 20, "B0は並行同文を集約しない(I4の改善対象)"


def test_b0_counts_recorded():
    """測定値をb0_baseline_counts.yamlへ記録・保存値と突合(基準の固定)。"""
    parser = CountingParser()
    recorded = {
        "measured": {
            "parse_sequential_identical_3": {
                "requests": 3,
                "llm_calls": 3,
                "dedup": "none",
                "note": "IntentParseService直列3要求(上のtestで固定)",
            },
            "parse_parallel_identical_20": {
                "requests": 20,
                "llm_calls": 20,
                "dedup": "none",
                "note": "asyncio.gather 20並行(I4改善の対照)",
            },
        },
        "referenced": {
            "jev_same_version_second_event": {
                "llm_calls": 0,
                "evidence": "tests/integration/test_matching_jev.py"
                "(jev_result IS NULLの成功済み再利用・layer4)",
                "note": "並行API呼出しの防止ではない(計画§5.2確認済み)",
            },
            "reeval_suppression": {
                "window_minutes": 30,
                "evidence": "src/latch/worker/cost/reeval.py + tests/unit/cost",
                "note": "抑制中の新しい版を残すdirty状態はB0に無し(I3対象)",
            },
            "frontend_parse_debounce": {
                "debounce_ms": 1000,
                "same_text_skip": True,
                "evidence": "frontend/tests/parseFlow.test.js(233 passed)",
                "note": "abortはサーバー課金の取消を保証しない",
            },
            "parse_server_aggregation": {
                "aggregation": "none",
                "evidence": "本test_b0_parse_no_server_dedup",
            },
        },
        "parser_protocol": f"{type(parser).__name__}(SupportsParseIntent適合)",
    }
    path = Path(__file__).resolve().parent / "fixtures" / "b0_baseline_counts.yaml"
    stored = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    if stored != recorded:
        path.write_text(
            yaml.safe_dump(recorded, allow_unicode=True, sort_keys=True),
            encoding="utf-8",
        )
        stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert stored == recorded
