"""latchesユースケース(M3 ws-1 design §2.2・§2.4・§2.8・§2.9)。

回答(respond)はdesign §2.2の手順0〜6を単一トランザクションで実行する。
:nowはFOR UPDATE取得後にClock.now()で採取し検査とUPDATEで同一値を使う
(latch_engine.try_promoteと同一規律)。予期しない例外はintentsと同じ
ラップ方針(例外クラス名のみログへ残して503)。
"""

from __future__ import annotations

import base64
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import JST, Clock
from latch.latches import calibration, store
from latch.latches.errors import (
    AlreadyAnsweredError,
    DependencyUnavailableError,
    ForbiddenError,
    LatchClosedError,
    LatchesError,
    LatchExpiredError,
    LatchNotFoundError,
    LatchValidationError,
)
from latch.latches.schemas import LatchDetailOut, LatchSummaryOut, ParticipantOut

logger = logging.getLogger("latch.latches")

RESPONSE_YES = "yes"
_RESPONSE_ACCEPTING = ("proposed", "partial_accept")
_TERMINAL_FOR_CALIBRATION = ("rejected", "matched")


def _wrap_unexpected(exc: Exception) -> DependencyUnavailableError:
    """予期しない例外を503へ包む。例外のクラス名のみログへ残す(08 §2.4)。"""
    logger.warning("latches.unexpected class=%s", type(exc).__name__)
    return DependencyUnavailableError("latches dependency unavailable")


def compute_new_status(response: str, responses: list[dict], total: int) -> str:
    """手順3の純計算(design §2.4)。responsesは追記後の全量。

    yes→全員yesでmatched・未満でpartial_accept(必要人数=|S|・D-06)。
    no/defer→即rejected(グループでも部分成立なし)。
    """
    if response != RESPONSE_YES:
        return "rejected"
    yes = sum(1 for r in responses if r.get("response") == RESPONSE_YES)
    return "matched" if yes == total else "partial_accept"


class LatchesService:
    """回答・一覧・詳細のユースケース(design §2.2・§2.8)。"""

    def __init__(self, *, clock: Clock, engine: AsyncEngine, geo=None) -> None:
        self._clock = clock
        self._engine = engine
        self._geo = geo  # GeoService | None(Noneならarea_name=None)

    # -- 回答(design §2.2手順0〜6) --

    async def respond(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        latch_id: uuid.UUID,
        response: str,
    ) -> LatchSummaryOut:
        try:
            async with self._engine.begin() as conn:
                user_id = await store.fetch_user_id(conn, auth_provider, auth_subject)
                if user_id is None:
                    raise LatchNotFoundError("user not found")
                row = await store.select_latch_for_update(conn, latch_id)
                if row is None:
                    raise LatchNotFoundError("latch not found")
                now = self._clock.now()  # FOR UPDATE取得後に採取
                my_intent = await store.select_participant_intent(
                    conn, row.intent_ids, user_id
                )
                if my_intent is None:
                    raise ForbiddenError("not a participant")
                for r in row.responses:
                    if r.get("user_id") == str(user_id):
                        raise AlreadyAnsweredError("already answered")
                if row.status not in _RESPONSE_ACCEPTING:
                    self._raise_closed_or_expired(row, now)
                item = {
                    "user_id": str(user_id),
                    "intent_id": str(my_intent),
                    "response": response,
                    "answered_at": now.isoformat(),
                }
                after = [*row.responses, item]
                new_status = compute_new_status(response, after, len(row.intent_ids))
                updated = await store.update_response(
                    conn,
                    latch_id=row.id,
                    item=item,
                    new_status=new_status,
                    now=now,
                )
                if updated is None:  # 手順5: 影響0→事前検査の分類で409
                    self._raise_closed_or_expired(row, now)
                await store.insert_latch_event(
                    conn,
                    row.id,
                    from_status=row.status,
                    to_status=new_status,
                    user_id=user_id,
                    now=now,
                )
                if new_status == "matched":
                    await self._on_matched(conn, row, now)
                if new_status in _TERMINAL_FOR_CALIBRATION:
                    await self._create_calibration(
                        conn,
                        row=row,
                        after=after,
                        matched=new_status == "matched",
                        now=now,
                    )
                return _summary(
                    row=row,
                    status=new_status,
                    my_response=response,
                    after=after,
                )
        except LatchesError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

    # -- 一覧・詳細(design §2.8) --

    async def list(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[LatchSummaryOut], str | None]:
        try:
            async with self._engine.connect() as conn:
                user_id = await store.fetch_user_id(conn, auth_provider, auth_subject)
                if user_id is None:
                    raise LatchNotFoundError("user not found")
                before = decode_cursor(cursor) if cursor else None
                rows = await store.select_latches_page(
                    conn, me=user_id, before=before, limit=limit + 1
                )
            items = [_summary_from_page(r, user_id) for r in rows[:limit]]
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit - 1]
                next_cursor = encode_cursor(last.target_time, last.created_at, last.id)
            return items, next_cursor
        except LatchesError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

    async def get(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        latch_id: uuid.UUID,
    ) -> LatchDetailOut:
        try:
            async with self._engine.connect() as conn:
                user_id = await store.fetch_user_id(conn, auth_provider, auth_subject)
                if user_id is None:
                    raise LatchNotFoundError("user not found")
                row = await store.select_latch(conn, latch_id)
                if row is None:
                    raise LatchNotFoundError("latch not found")
                if (
                    await store.select_participant_intent(conn, row.intent_ids, user_id)
                    is None
                ):
                    raise ForbiddenError("not a participant")
                summary = _summary_from_page(_page_view_of(row), user_id)
                if row.status not in ("matched", "completed"):
                    return LatchDetailOut(**summary.model_dump())
                participants = await store.fetch_participants(conn, row.intent_ids)
                geo_rows = await store.fetch_intents_geo(conn, row.intent_ids)
            # connを閉じた後でgeo呼び出し(GeoServiceは別接続を開く)
            detail_extra: dict = {}
            if geo_rows:
                target = max(g.time_start for g in geo_rows)
                detail_extra["time_summary"] = target.astimezone(JST).strftime(
                    "%Y-%m-%d %H:%M"
                )
                if self._geo is not None:
                    lon = sum(g.lon for g in geo_rows) / len(geo_rows)
                    lat = sum(g.lat for g in geo_rows) / len(geo_rows)
                    detail_extra["area_name"] = await self._geo.reverse_geocode(
                        lon, lat
                    )
            detail_extra["participants"] = [
                ParticipantOut(
                    user_id=p.user_id,
                    display_name=p.display_name,
                    profile=p.profile,
                )
                for p in participants
            ]
            return LatchDetailOut(**summary.model_dump(), **detail_extra)
        except LatchesError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

    @staticmethod
    def _raise_closed_or_expired(row, now) -> None:
        """手順2c/5の409分類(期限切れが上・design §2.2)。"""
        if row.response_deadline <= now or row.expires_at <= now:
            raise LatchExpiredError("response deadline passed")
        raise LatchClosedError("latch closed")

    async def _on_matched(self, conn, row, now) -> None:
        """手順6c: 参加Intent matched化+競合クローズ(design §2.3)。"""
        matched = await store.match_intents(conn, row.intent_ids, now)
        if len(matched) != len(row.intent_ids):
            # 手順4のEXISTSでactive/pausedは担保済みのため通常は起きない防御
            raise DependencyUnavailableError("intents changed during match")
        conflicts = await store.select_conflicting_latches(conn, row.id, row.intent_ids)
        for latch_id, from_status in conflicts:
            if await store.cancel_latch(conn, latch_id):
                await store.insert_latch_event(
                    conn, latch_id, from_status, "cancelled", None, now
                )

    async def _create_calibration(
        self, conn, *, row, after: list[dict], matched: bool, now
    ) -> None:
        """Calibration作成(design §2.5・§2.11・§2.12)。

        評価行が特定できない場合はレコードを作らず構造化ログ1行のみ
        (回答成立を落とす損失のほうが大きい — design §2.5)。
        """
        if row.group_candidate_id is None:
            a_id, b_id = sorted(row.intent_ids)[:2]
            eval_rows = await store.fetch_pair_rows(conn, a_id, b_id)
            hit = calibration.pick_eval_row(eval_rows, row.score, row.created_at)
            pair_ids: tuple | None = (a_id, b_id)
            hit_jev = hit.jev_result if hit is not None else None
        else:
            ms = await store.fetch_member_scores(conn, row.group_candidate_id)
            versions = _versions_of(ms)
            pair_rows = await store.fetch_group_pairs(conn, row.intent_ids)
            versioned = calibration.select_versioned_pairs(pair_rows, versions)
            best = calibration.pick_min_pair(versioned)
            hit_jev = best.jev_result if best is not None else None
            pair_ids = (best.a, best.b) if best is not None else None
        if hit_jev is None or pair_ids is None:
            logger.warning("latch.calibration.missing latch_id=%s", row.id)
            return
        structured = await store.fetch_intents_structured(conn, list(pair_ids))
        if len(structured) != 2:
            logger.warning("latch.calibration.missing latch_id=%s", row.id)
            return
        segment = calibration.classify_segment(
            calibration.segment_texts(structured[0]),
            calibration.segment_texts(structured[1]),
        )
        prediction = calibration.build_prediction(hit_jev, float(row.score), segment)
        await store.insert_calibration(
            conn,
            latch_id=row.id,
            intent_ids=row.intent_ids,
            prediction=prediction,
            proposal=row.proposal,
            responses=after,
            matched=matched,
            now=now,
        )


def _versions_of(member_scores: dict | None) -> dict[uuid.UUID, int]:
    """member_scores.versions({str(uuid): version})→{uuid: int}(design §2.12-1)。"""
    if not member_scores:
        return {}
    raw = member_scores.get("versions")
    if not isinstance(raw, dict):
        return {}
    out: dict[uuid.UUID, int] = {}
    for key, value in raw.items():
        try:
            out[uuid.UUID(key)] = int(value)
        except (ValueError, TypeError):
            continue
    return out


def _summary(
    *, row, status: str, my_response: str | None, after: list[dict]
) -> LatchSummaryOut:
    """応答要素(responses配列は出さない・引用#22)。"""
    total = len(row.intent_ids)
    yes = sum(1 for r in after if r.get("response") == RESPONSE_YES)
    if status in ("matched", "completed"):
        remaining = 0
    else:
        remaining = total - yes
    return LatchSummaryOut(
        id=row.id,
        status=status,
        response_deadline=row.response_deadline,
        expires_at=row.expires_at,
        created_at=row.created_at,
        completed_at=None,  # completed遷移はws-2
        proposal=row.proposal,
        is_group=row.group_candidate_id is not None,
        my_response=my_response,
        remaining_responses=remaining,
    )


def encode_cursor(
    target_time: datetime, created_at: datetime, latch_id: uuid.UUID
) -> str:
    """キーセットcursor 3キー(design §2.8): base64url("ISO|ISO|uuid")。

    ソート順(target_time ASC, created_at DESC, id ASC)をタプル比較で表現する。
    intentsのencode_cursorパターンの拡張。
    """
    raw = f"{target_time.isoformat()}|{created_at.isoformat()}|{latch_id}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> tuple[datetime, datetime, uuid.UUID]:
    """cursor復元。形式不正は422 VALIDATION_ERROR(intentsと同型)。"""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        tt, ct, lid = raw.split("|")
        return datetime.fromisoformat(tt), datetime.fromisoformat(ct), uuid.UUID(lid)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LatchValidationError("invalid cursor") from exc


def encode_message_cursor(created_at: datetime, message_id: uuid.UUID) -> str:
    """messagesのキーセットcursor 2キー(design §2.1): base64url("ISO|uuid")。

    latches一覧のencode_cursor(3キー)の縮小版。ソート順
    (created_at ASC, id ASC)をタプル比較で表現する。
    """
    raw = f"{created_at.isoformat()}|{message_id}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def decode_message_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """messages cursor復元。形式不正は422 VALIDATION_ERROR(同型)。"""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        ct, mid = raw.split("|")
        return datetime.fromisoformat(ct), uuid.UUID(mid)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LatchValidationError("invalid cursor") from exc


def is_attendance_window_open(completed_at: datetime | None, now: datetime) -> bool:
    """D-09の3日窓(閉区間: now <= completed_at + 3日・design §2.3手順5)。

    「3日以内」の以内を閉区間として読む(supervisor承認・design §2.3)。
    completed_at未設定(None)は窓閉と扱う(通常起きない防御)。
    """
    if completed_at is None:
        return False
    return now <= completed_at + timedelta(days=3)


def _yes_count(responses: list[dict]) -> int:
    return sum(1 for r in responses if r.get("response") == RESPONSE_YES)


def _my_response(responses: list[dict], user_id: uuid.UUID) -> str | None:
    for r in responses:
        if r.get("user_id") == str(user_id):
            return r.get("response")
    return None


def _summary_from_page(row, user_id: uuid.UUID) -> LatchSummaryOut:
    """一覧・詳細行→応答要素(responses配列は出さない・引用#22)。"""
    total = len(row.intent_ids)
    if row.status in ("matched", "completed"):
        remaining = 0
    else:
        remaining = total - _yes_count(row.responses)
    return LatchSummaryOut(
        id=row.id,
        status=row.status,
        response_deadline=row.response_deadline,
        expires_at=row.expires_at,
        created_at=row.created_at,
        completed_at=row.completed_at,
        proposal=row.proposal,
        is_group=row.group_candidate_id is not None,
        my_response=_my_response(row.responses, user_id),
        remaining_responses=remaining,
    )


def _page_view_of(row):
    """LatchRow→_summary_from_page互換のビュー(completed_atを持たせる)。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        id=row.id,
        status=row.status,
        intent_ids=row.intent_ids,
        responses=row.responses,
        response_deadline=row.response_deadline,
        expires_at=row.expires_at,
        created_at=row.created_at,
        completed_at=None,
        proposal=row.proposal,
        group_candidate_id=row.group_candidate_id,
    )


def make_latches_service(*, clock: Clock, engine: AsyncEngine) -> LatchesService:
    """main.py lifespan用の構築(GeoServiceはengineから作る)。"""
    from latch.geo.service import GeoService

    return LatchesService(clock=clock, engine=engine, geo=GeoService(engine))
