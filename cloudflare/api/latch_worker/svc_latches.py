"""latchesユースケースのD1版サービス(本体実装)。

respond は atomic.apply_response(marker従属・全効果を1batchで確定)を
使う。読取(一覧・詳細・chat)は参照実装(latches/store.py)と同一の
SQL意味・順序をD1向けに書き直す。応答構成(LatchSummaryOut等)は本体
schemasをそのまま使う。
"""

from __future__ import annotations

import uuid as uuid_mod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from latch.adapters.d1.atomic import D1AtomicRepository
from latch.core.clock import JST
from latch.domain.time import from_micros, micros
from latch.domain.types import CommandStatus
from latch.latches.schemas import (
    AttendanceResponse,
    LatchDetailOut,
    LatchSummaryOut,
    ParticipantOut,
)

from latch_worker.gjson import loads_json

_RESPONSE_ACCEPTING = ("proposed", "partial_accept")


@dataclass(frozen=True)
class MessageRow:
    """messages行(messages応答の1件 — latches/store.MessageRow相当)。"""

    id: uuid_mod.UUID
    latch_id: uuid_mod.UUID
    sender_id: uuid_mod.UUID
    body: str
    created_at: datetime


def encode_cursor(target_time: datetime, created_at: datetime, latch_id) -> str:
    import base64

    raw = f"{target_time.isoformat()}|{created_at.isoformat()}|{latch_id}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def decode_cursor(cursor: str):
    import base64

    from latch.latches.errors import LatchValidationError

    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        tt, ct, lid = raw.split("|")
        return datetime.fromisoformat(tt), datetime.fromisoformat(ct), uuid_mod.UUID(lid)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LatchValidationError("invalid cursor") from exc


def encode_message_cursor(created_at: datetime, message_id) -> str:
    import base64

    raw = f"{created_at.isoformat()}|{message_id}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def decode_message_cursor(cursor: str):
    import base64

    from latch.latches.errors import LatchValidationError

    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        ct, mid = raw.split("|")
        return datetime.fromisoformat(ct), uuid_mod.UUID(mid)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LatchValidationError("invalid cursor") from exc


def is_attendance_window_open(completed_at: datetime | None, now: datetime) -> bool:
    if completed_at is None:
        return False
    return now <= completed_at + timedelta(days=3)


def _yes_count(responses: list[dict]) -> int:
    return sum(1 for r in responses if r.get("response") == "yes")


def _my_response(responses: list[dict], user_id) -> str | None:
    for r in responses:
        if r.get("user_id") == str(user_id):
            return r.get("response")
    return None


@dataclass(frozen=True)
class _LatchRow:
    id: uuid_mod.UUID
    status: str
    intent_ids: list
    responses: list
    response_deadline: datetime
    expires_at: datetime
    score: Any
    proposal: dict
    group_candidate_id: str | None
    created_at: datetime
    completed_at: datetime | None


class D1LatchesService:
    """回答・一覧・詳細・チャット・実施申告のD1版ユースケース。"""

    def __init__(self, *, clock, db, atomic: D1AtomicRepository, geo=None, push=None) -> None:  # noqa: E501
        self._clock = clock
        self._d1 = db
        self._atomic = atomic
        self._geo = geo
        self._push = push

    # -- 共通 --

    async def _fetch_user_id(self, provider: str, subject: str):
        row = (
            await self._d1.prepare(
                "SELECT id FROM users WHERE auth_provider = ? AND auth_subject = ?"
            )
            .bind(provider, subject)
            .first()
        )
        return uuid_mod.UUID(str(row["id"])) if row is not None else None

    async def _select_latch(self, latch_id) -> _LatchRow | None:
        row = (
            await self._d1.prepare(
                "SELECT id, status, intent_ids, responses, response_deadline,"
                " expires_at, score, proposal, group_candidate_id, created_at,"
                " completed_at FROM latches WHERE id = ?"
            )
            .bind(str(latch_id))
            .first()
        )
        if row is None:
            return None
        return _LatchRow(
            id=uuid_mod.UUID(str(row["id"])),
            status=row["status"],
            intent_ids=loads_json(row["intent_ids"], []),
            responses=loads_json(row["responses"], []),
            response_deadline=from_micros(row["response_deadline"]),
            expires_at=from_micros(row["expires_at"]),
            score=row["score"],
            proposal=loads_json(row["proposal"], {}),
            group_candidate_id=row["group_candidate_id"],
            created_at=from_micros(row["created_at"]),
            completed_at=from_micros(row["completed_at"]) if row["completed_at"] else None,
        )

    async def _participant_intent_id(self, intent_ids_json: str, user_id) -> str | None:
        row = (
            await self._d1.prepare(
                "SELECT id FROM intents WHERE user_id = ? AND id IN"
                " (SELECT value FROM json_each(?)) LIMIT 1"
            )
            .bind(str(user_id), intent_ids_json)
            .first()
        )
        return str(row["id"]) if row is not None else None

    # -- 回答(design §2.2手順0〜6相当 — atomic.apply_response) --

    async def respond(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        latch_id,
        response: str,
    ) -> LatchSummaryOut:
        from latch.latches.errors import (
            AlreadyAnsweredError,
            DependencyUnavailableError,
            ForbiddenError,
            LatchClosedError,
            LatchExpiredError,
            LatchNotFoundError,
            LatchesError,
        )

        try:
            user_id = await self._fetch_user_id(auth_provider, auth_subject)
            if user_id is None:
                raise LatchNotFoundError("user not found")
            now = self._clock.now()
            result = await self._atomic.apply_response(
                str(uuid_mod.uuid4()),
                None,
                {
                    "latch_id": str(latch_id),
                    "user_id": str(user_id),
                    "value": response,
                },
                now,
            )
            if result.status == CommandStatus.REJECTED:
                mapping = {
                    "NOT_FOUND": LatchNotFoundError,
                    "FORBIDDEN": ForbiddenError,
                    "ALREADY_ANSWERED": AlreadyAnsweredError,
                    "LATCH_EXPIRED": LatchExpiredError,
                    "LATCH_CLOSED": LatchClosedError,
                }
                exc_cls = mapping.get(result.error_code or "", LatchClosedError)
                raise exc_cls(result.error_code or "latch closed")  # type: ignore[call-arg]
            if result.status == CommandStatus.CONFLICT:
                raise AlreadyAnsweredError("already answered")
            row = await self._select_latch(latch_id)
            if row is None:  # 通常起きない防御
                raise DependencyUnavailableError("latch state unreadable")
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
                my_response=response,
                remaining_responses=remaining,
            )
        except LatchesError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DependencyUnavailableError(
                "latches dependency unavailable"
            ) from exc

    # -- 一覧 --

    async def list(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        cursor: str | None = None,
        limit: int = 20,
    ):
        from latch.latches.errors import (
            DependencyUnavailableError,
            LatchNotFoundError,
            LatchesError,
        )

        try:
            user_id = await self._fetch_user_id(auth_provider, auth_subject)
            if user_id is None:
                raise LatchNotFoundError("user not found")
            before = decode_cursor(cursor) if cursor else None
            target_expr = (
                "(SELECT max(i.time_start) FROM intents i"
                " WHERE i.id IN (SELECT value FROM json_each(l.intent_ids)))"
            )
            clauses = [
                "l.status != 'candidate'",
                "EXISTS (SELECT 1 FROM intents i WHERE i.user_id = ?"
                " AND i.id IN (SELECT value FROM json_each(l.intent_ids)))",
            ]
            params: list = [str(user_id)]
            if before is not None:
                clauses.append(
                    f"({target_expr} > ? OR ({target_expr} = ? AND"
                    " (l.created_at < ? OR (l.created_at = ? AND l.id > ?))))"
                )
                params.extend(
                    [
                        micros(before[0]),
                        micros(before[0]),
                        micros(before[1]),
                        micros(before[1]),
                        str(before[2]),
                    ]
                )
            rows = (
                await self._d1.prepare(
                    "SELECT l.id, l.status, l.intent_ids, l.responses,"
                    " l.response_deadline, l.expires_at, l.proposal, l.created_at,"
                    " l.completed_at, l.group_candidate_id, "
                    + target_expr
                    + " AS target_time FROM latches l WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY target_time ASC, l.created_at DESC, l.id ASC"
                    + " LIMIT ?"
                )
                .bind(*params, limit + 1)
                .all()
            )
            items = []
            for r in rows[:limit]:
                responses = loads_json(r["responses"], [])
                total = len(loads_json(r["intent_ids"], []))
                status_ = r["status"]
                remaining = (
                    0
                    if status_ in ("matched", "completed")
                    else total - _yes_count(responses)
                )
                items.append(
                    LatchSummaryOut(
                        id=uuid_mod.UUID(str(r["id"])),
                        status=status_,
                        response_deadline=from_micros(r["response_deadline"]),
                        expires_at=from_micros(r["expires_at"]),
                        created_at=from_micros(r["created_at"]),
                        completed_at=(
                            from_micros(r["completed_at"]) if r["completed_at"] else None
                        ),
                        proposal=loads_json(r["proposal"], {}),
                        is_group=r["group_candidate_id"] is not None,
                        my_response=_my_response(responses, user_id),
                        remaining_responses=remaining,
                    )
                )
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit - 1]
                next_cursor = encode_cursor(
                    from_micros(last["target_time"]),
                    from_micros(last["created_at"]),
                    last["id"],
                )
            return items, next_cursor
        except LatchesError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DependencyUnavailableError(
                "latches dependency unavailable"
            ) from exc

    # -- 詳細 --

    async def get(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        latch_id,
    ) -> LatchDetailOut:
        from latch.latches.errors import (
            DependencyUnavailableError,
            ForbiddenError,
            LatchNotFoundError,
            LatchesError,
        )

        try:
            user_id = await self._fetch_user_id(auth_provider, auth_subject)
            if user_id is None:
                raise LatchNotFoundError("user not found")
            row = await self._select_latch(latch_id)
            if row is None:
                raise LatchNotFoundError("latch not found")
            raw_ids = (
                await self._d1.prepare(
                    "SELECT intent_ids FROM latches WHERE id = ?"
                )
                .bind(str(latch_id))
                .first()
            )
            if (
                await self._participant_intent_id(raw_ids["intent_ids"], user_id)
                is None
            ):
                raise ForbiddenError("not a participant")
            total = len(row.intent_ids)
            remaining = (
                0
                if row.status in ("matched", "completed")
                else total - _yes_count(row.responses)
            )
            summary = LatchSummaryOut(
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
            if row.status not in ("matched", "completed"):
                return LatchDetailOut(**summary.model_dump())
            participants = (
                await self._d1.prepare(
                    "SELECT i.user_id, u.display_name, u.profile FROM intents i"
                    " JOIN users u ON u.id = i.user_id"
                    " WHERE i.id IN (SELECT value FROM json_each(?))"
                )
                .bind(raw_ids["intent_ids"])
                .all()
            )
            geo_rows = (
                await self._d1.prepare(
                    "SELECT time_start, geo_lon, geo_lat FROM intents"
                    " WHERE id IN (SELECT value FROM json_each(?))"
                )
                .bind(raw_ids["intent_ids"])
                .all()
            )
            detail_extra: dict = {}
            if geo_rows:
                target = max(from_micros(g["time_start"]) for g in geo_rows)
                detail_extra["time_summary"] = target.astimezone(JST).strftime(
                    "%Y-%m-%d %H:%M"
                )
                if self._geo is not None:
                    lon = sum(float(g["geo_lon"] or 0.0) for g in geo_rows) / len(
                        geo_rows
                    )
                    lat = sum(float(g["geo_lat"] or 0.0) for g in geo_rows) / len(
                        geo_rows
                    )
                    detail_extra["area_name"] = await self._geo.reverse_geocode(
                        lon, lat
                    )
            detail_extra["participants"] = [
                ParticipantOut(
                    user_id=uuid_mod.UUID(str(p["user_id"])),
                    display_name=p["display_name"],
                    profile=loads_json(p["profile"], {}),
                )
                for p in participants
            ]
            return LatchDetailOut(**summary.model_dump(), **detail_extra)
        except LatchesError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DependencyUnavailableError(
                "latches dependency unavailable"
            ) from exc

    # -- チャット --

    async def send_message(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        latch_id,
        body: str,
    ) -> MessageRow:
        from latch.latches.errors import (
            ChatReadonlyError,
            DependencyUnavailableError,
            ForbiddenError,
            LatchNotFoundError,
            LatchesError,
        )

        try:
            user_id = await self._fetch_user_id(auth_provider, auth_subject)
            if user_id is None:
                raise LatchNotFoundError("user not found")
            row = await self._select_latch(latch_id)
            if row is None:
                raise LatchNotFoundError("latch not found")
            raw_ids = (
                await self._d1.prepare(
                    "SELECT intent_ids FROM latches WHERE id = ?"
                )
                .bind(str(latch_id))
                .first()
            )
            if (
                await self._participant_intent_id(raw_ids["intent_ids"], user_id)
                is None
            ):
                raise ForbiddenError("not a participant")
            now = self._clock.now()
            if row.status != "matched":
                raise ChatReadonlyError("chat readonly")
            # blocks双方向判定(自分以外の参加者)
            blocked = (
                await self._d1.prepare(
                    "SELECT 1 AS x FROM blocks WHERE"
                    " (blocker_id = ? AND blocked_id IN"
                    "  (SELECT user_id FROM intents WHERE id IN"
                    "   (SELECT value FROM json_each(?))))"
                    " OR (blocked_id = ? AND blocker_id IN"
                    "  (SELECT user_id FROM intents WHERE id IN"
                    "   (SELECT value FROM json_each(?))))"
                )
                .bind(
                    str(user_id),
                    raw_ids["intent_ids"],
                    str(user_id),
                    raw_ids["intent_ids"],
                )
                .first()
            )
            if blocked is not None:
                raise ChatReadonlyError("chat readonly")
            message_id = str(uuid_mod.uuid4())
            await self._d1.batch(
                [
                    self._d1.prepare(
                        "INSERT INTO messages"
                        " (id, latch_id, sender_id, body, created_at)"
                        " VALUES (?, ?, ?, ?, ?)"
                    ).bind(
                        message_id, str(latch_id), str(user_id), body, micros(now)
                    )
                ]
            )
            return MessageRow(
                id=uuid_mod.UUID(message_id),
                latch_id=uuid_mod.UUID(str(latch_id)),
                sender_id=user_id,
                body=body,
                created_at=now,
            )
        except LatchesError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DependencyUnavailableError(
                "latches dependency unavailable"
            ) from exc

    async def list_messages(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        latch_id,
        cursor: str | None = None,
        limit: int = 20,
    ):
        from latch.latches.errors import (
            DependencyUnavailableError,
            ForbiddenError,
            LatchNotFoundError,
            LatchesError,
        )

        try:
            user_id = await self._fetch_user_id(auth_provider, auth_subject)
            if user_id is None:
                raise LatchNotFoundError("user not found")
            row = await self._select_latch(latch_id)
            if row is None:
                raise LatchNotFoundError("latch not found")
            raw_ids = (
                await self._d1.prepare(
                    "SELECT intent_ids FROM latches WHERE id = ?"
                )
                .bind(str(latch_id))
                .first()
            )
            if (
                await self._participant_intent_id(raw_ids["intent_ids"], user_id)
                is None
            ):
                raise ForbiddenError("not a participant")
            before = decode_message_cursor(cursor) if cursor else None
            clauses = ["latch_id = ?"]
            params: list = [str(latch_id)]
            if before is not None:
                clauses.append("(created_at > ? OR (created_at = ? AND id > ?))")
                params.extend(
                    [micros(before[0]), micros(before[0]), str(before[1])]
                )
            rows = (
                await self._d1.prepare(
                    "SELECT id, latch_id, sender_id, body, created_at FROM messages"
                    " WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY created_at ASC, id ASC LIMIT ?"
                )
                .bind(*params, limit + 1)
                .all()
            )
            items = [
                MessageRow(
                    id=uuid_mod.UUID(str(r["id"])),
                    latch_id=uuid_mod.UUID(str(r["latch_id"])),
                    sender_id=uuid_mod.UUID(str(r["sender_id"])),
                    body=r["body"],
                    created_at=from_micros(r["created_at"]),
                )
                for r in rows[:limit]
            ]
            next_cursor = None
            if len(rows) > limit:
                last = items[limit - 1]
                next_cursor = encode_message_cursor(last.created_at, last.id)
            return items, next_cursor
        except LatchesError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DependencyUnavailableError(
                "latches dependency unavailable"
            ) from exc

    # -- 実施自己申告 --

    async def submit_attendance(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        latch_id,
        attended: bool,
    ) -> AttendanceResponse:
        from latch.latches.errors import (
            AttendanceAlreadySubmittedError,
            AttendanceWindowClosedError,
            DependencyUnavailableError,
            LatchClosedError,
            LatchNotFoundError,
            LatchesError,
        )

        try:
            user_id = await self._fetch_user_id(auth_provider, auth_subject)
            if user_id is None:
                raise LatchNotFoundError("user not found")
            row = await self._select_latch(latch_id)
            if row is None:
                raise LatchNotFoundError("latch not found")
            raw_ids = (
                await self._d1.prepare(
                    "SELECT intent_ids FROM latches WHERE id = ?"
                )
                .bind(str(latch_id))
                .first()
            )
            if (
                await self._participant_intent_id(raw_ids["intent_ids"], user_id)
                is None
            ):
                # messagesの403と扱いを分ける(関与の有無を開示しない — 引用#9)
                raise LatchNotFoundError("not a participant")
            now = self._clock.now()
            if row.status != "completed":
                raise LatchClosedError("latch closed")
            if not is_attendance_window_open(row.completed_at, now):
                raise AttendanceWindowClosedError("attendance window closed")
            cal = (
                await self._d1.prepare(
                    "SELECT actual_attended FROM calibration_records"
                    " WHERE latch_id = ?"
                )
                .bind(str(latch_id))
                .first()
            )
            if cal is None:
                raise DependencyUnavailableError("calibration record missing")
            if cal["actual_attended"] is not None:
                raise AttendanceAlreadySubmittedError("already submitted")
            updated = (
                await self._d1.prepare(
                    "UPDATE calibration_records"
                    " SET actual_attended = ?, cancelled_after = ?, updated_at = ?"
                    " WHERE latch_id = ? AND actual_attended IS NULL"
                    " RETURNING id"
                )
                .bind(
                    1 if attended else 0,
                    0 if attended else 1,
                    micros(now),
                    str(latch_id),
                )
                .first()
            )
            if updated is None:
                raise AttendanceAlreadySubmittedError("already submitted")
            return AttendanceResponse(
                latch_id=uuid_mod.UUID(str(latch_id)), actual_attended=attended
            )
        except LatchesError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DependencyUnavailableError(
                "latches dependency unavailable"
            ) from exc
