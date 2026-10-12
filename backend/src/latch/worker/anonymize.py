"""D-13第二段の月次匿名化バッチ(08 §3.4・backlog V-02・V1-P5)。

第一段(intents/deletion.py: 退会・削除時のID系NULL化)を済ませた行
(latch_id IS NULL AND intent_ids IS NULL)のうち、anonymized_atがまだ無い行を
暦月初JST 0時に一括変換する(08 §3.4「即時の個別匿名化は高識別性の仮名データを
恒久残存させるため、月次バッチで実行する」)。変換内容は08 §3.4の3点:

1. 時刻系を日単位へ丸める(actual_responsesの回答時刻→日付・created_at列→JST日境界)
2. proposal_snapshotの粒度低下(時間帯=昼/夕/夜のビン・地域名=市区町村単位・
   category_secondaryと対象日時(time_summary)の除去・人数=2〜3人/4人のビン)
3. actual_responsesは回答種別と丸め済み日付のみ保持(user_idは第一段で除去済み)

純関数(変換本体)とSQL実行層(AnonymizeJob)の2層。jobはRetentionJobと同一契約
(次JST 0時待機→月初のみrun_once・失敗時retry_sec再試行・注入sleepでunit決定的)。
冪等ガード=anonymized_at IS NOT NULL行は対象外(再実行で二重変換しない)。
ビンの切れ目(昼10時〜/夕16時〜/他は夜)と市区町村抽出(最初の[市区町村]まで)は
08 §3.4が規定しない実装詳細としてこのモジュールに固定する(設計判断)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.worker.reset import next_jst_midnight

logger = logging.getLogger(__name__)

BATCH_LIMIT = 500

# 市区町村単位の抽出: 最初の[市区町村]までを残す(「鹿児島市泉町」→「鹿児島市」。
# 「〜郡〜町」も同型で処理される。マッチしない地名はそのまま残す=丸め過ぎない安全側)
_MUNICIPALITY = re.compile(r"^(.*?[市区町村])")

# 時間帯ビン(08 §3.4「昼/夕/夜」)。JST時刻で判定
DAY_PARTS = (("day", 10, 16), ("evening", 16, 19))  # 他はnight
DAY_PART_LABEL = {"day": "昼", "evening": "夕", "night": "夜"}


def daypart_label(hour: int) -> str:
    """JST時刻→昼/夕/夜のビンラベル。10<=h<16=昼・16<=h<19=夕・他=夜。"""
    for key, lo, hi in DAY_PARTS:
        if lo <= hour < hi:
            return DAY_PART_LABEL[key]
    return DAY_PART_LABEL["night"]


def daypart_of_time_summary(time_summary: object) -> str | None:
    """time_summary(JST YYYY-MM-DD HH:MM)→時間帯ビン。形式不明はNone(除去)。"""
    if not isinstance(time_summary, str) or len(time_summary) < 13:
        return None
    try:
        hour = int(time_summary[11:13])
    except ValueError:
        return None
    return daypart_label(hour)


def municipality_of(area_name: object) -> str | None:
    """地域名→市区町村単位。非文字列・空はNone(キー除去)。"""
    if not isinstance(area_name, str) or not area_name:
        return None
    m = _MUNICIPALITY.match(area_name)
    return m.group(1) if m else area_name


def headcount_bin(headcount: object) -> str | None:
    """人数→「2-3人」/「4人」のビン(08 §3.4)。値不明はNone(キー除去)。"""
    if not isinstance(headcount, int) or isinstance(headcount, bool):
        return None
    return "2-3人" if headcount <= 3 else "4人"


def answered_date(answered_at: object) -> str | None:
    """回答時刻ISO→日付文字列(YYYY-MM-DD)。形式不明はNone(キー除去)。"""
    if not isinstance(answered_at, str) or len(answered_at) < 10:
        return None
    return answered_at[:10]


def anonymize_responses(actual_responses: object) -> list[dict]:
    """actual_responses→回答種別と丸め済み日付のみ(08 §3.4の3)。"""
    if isinstance(actual_responses, str):
        try:
            actual_responses = json.loads(actual_responses)
        except json.JSONDecodeError:
            return []
    if not isinstance(actual_responses, list):
        return []
    out: list[dict] = []
    for e in actual_responses:
        if not isinstance(e, dict):
            continue
        row: dict = {"value": e.get("value")}
        day = answered_date(e.get("answered_at"))
        if day is not None:
            row["answered_date"] = day
        out.append(row)
    return out


def anonymize_snapshot(proposal_snapshot: object) -> dict:
    """proposal_snapshot→粒度低下版(08 §3.4の2)。

    保持: category_primary・budget・match_level(統計値)。
    変換: 時間帯ビン(time_of_day)・市区町村(area_municipality)・
    人数ビン(headcount_bin)。
    除去: time_summary(対象日時)・area_name・headcount(生値)・category_secondary。
    """
    if isinstance(proposal_snapshot, str):
        try:
            proposal_snapshot = json.loads(proposal_snapshot)
        except json.JSONDecodeError:
            return {}
    if not isinstance(proposal_snapshot, dict):
        return {}
    out: dict = {}
    if isinstance(proposal_snapshot.get("category_primary"), str):
        out["category_primary"] = proposal_snapshot["category_primary"]
    if "budget" in proposal_snapshot and proposal_snapshot["budget"] is not None:
        out["budget"] = proposal_snapshot["budget"]
    if isinstance(proposal_snapshot.get("match_level"), str):
        out["match_level"] = proposal_snapshot["match_level"]
    tod = daypart_of_time_summary(proposal_snapshot.get("time_summary"))
    if tod is not None:
        out["time_of_day"] = tod
    muni = municipality_of(proposal_snapshot.get("area_name"))
    if muni is not None:
        out["area_municipality"] = muni
    hbin = headcount_bin(proposal_snapshot.get("headcount"))
    if hbin is not None:
        out["headcount_bin"] = hbin
    return out


_SELECT_PENDING = text("""
    SELECT id, actual_responses, proposal_snapshot
      FROM calibration_records
     WHERE latch_id IS NULL AND intent_ids IS NULL AND anonymized_at IS NULL
     ORDER BY id
     LIMIT :batch_limit
""")

# created_atはJST日境界のtimestamptzへ丸める(08 §3.4の1)。日付丸めは
# 行データの変換であり現在時刻参照ではない(C2の対象外)。
_UPDATE_ANONYMIZED = text("""
    UPDATE calibration_records
       SET actual_responses = CAST(:responses AS jsonb),
           proposal_snapshot = CAST(:snapshot AS jsonb),
           created_at = date_trunc('day', created_at AT TIME ZONE 'Asia/Tokyo')
                        AT TIME ZONE 'Asia/Tokyo',
           updated_at = CAST(:now AS timestamptz),
           anonymized_at = CAST(:now AS timestamptz)
     WHERE id = CAST(:row_id AS uuid)
""")


class AnonymizeJob:
    """次のJST 0時まで待機→月初(1日)のときのみrun_once(月次)。独立task。"""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        retry_sec: int = 300,
        batch_limit: int = BATCH_LIMIT,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._retry_sec = retry_sec
        self._batch_limit = batch_limit
        self._sleep = sleep

    async def run_once(self) -> int:
        """窓で空になるまで第一段済み行を変換。戻り=変換行数。"""
        now = self._clock.now()
        total = 0
        async with self._engine.begin() as conn:
            while True:
                rows = (
                    await conn.execute(
                        _SELECT_PENDING, {"batch_limit": self._batch_limit}
                    )
                ).fetchall()
                for row_id, responses, snapshot in rows:
                    await conn.execute(
                        _UPDATE_ANONYMIZED,
                        {
                            "row_id": row_id,
                            "responses": json.dumps(
                                anonymize_responses(responses), ensure_ascii=False
                            ),
                            "snapshot": json.dumps(
                                anonymize_snapshot(snapshot), ensure_ascii=False
                            ),
                            "now": now,
                        },
                    )
                total += len(rows)
                if len(rows) < self._batch_limit:
                    break
        logger.info("anonymize.run_once transformed calibration_records=%d", total)
        return total

    async def run(self, *, stop: asyncio.Event | None = None) -> None:
        """0時tickごとに月初を判定し・月初のみrun_once(失敗時retry_sec再試行)。"""
        while stop is None or not stop.is_set():
            now = self._clock.now()
            await self._wait((next_jst_midnight(now) - now).total_seconds(), stop)
            if stop is not None and stop.is_set():
                break
            if self._clock.jst_date().day != 1:
                continue  # 月中の0時は素通り(翌0時まで待機し直す)
            while True:
                try:
                    await self.run_once()
                    break
                except Exception:
                    logger.warning("anonymize run_once failed", exc_info=True)
                    await self._wait(self._retry_sec, stop)
                    if stop is not None and stop.is_set():
                        return

    async def _wait(self, seconds: float, stop: asyncio.Event | None) -> None:
        """待機(RetentionJob._waitと同一契約 — 注入sleepでunit試験が決定的に回る)。"""
        if stop is None:
            await self._sleep(seconds)
            return
        sleep_task = asyncio.create_task(self._sleep(seconds))
        stop_task = asyncio.create_task(stop.wait())
        done, pending = await asyncio.wait(
            {sleep_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
