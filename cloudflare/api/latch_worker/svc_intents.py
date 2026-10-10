"""intents CRUD・parseのD1版サービス(本体実装)。

検証フロー(必須3・時刻・年齢・同意・active上限・ジオコーディング)と
応答構成(IntentRow)は backend/src/latch/intents/service.py の本体業務規則
を同じ順序で再現する。書き込みは adapters/d1/atomic.apply_intent
(command marker・outbox・journalと同じbatch確定)。

parseは本体IntentParseServiceをStubLLMアダプタで構築する
(実LLM呼出し無し — LATCH_LLM_MODE=stub相当・承認待ちのまま)。
"""

from __future__ import annotations

import uuid as uuid_mod
from dataclasses import replace
from datetime import date, datetime, timedelta

from latch.adapters.d1.atomic import D1AtomicRepository
from latch.domain.time import from_micros, micros
from latch.intents.intent_input import StructuredIntentInput
from latch.intents.mapping import (
    ResolvedColumns,
    columns_from_row,
    differs_from_row,
    resolve_for_active,
    resolve_for_draft,
)
from latch.intents.service import (
    IntentParseService,
    IntentService,
    PageResult,
    decode_cursor,
    encode_cursor,
)
from latch.intents.store import IntentRow

from latch_worker.gjson import loads_json

_MAX_AHEAD = timedelta(days=7)

_ROW_COLS = (
    "id, user_id, category_primary, alcohol_involved, raw_text, structured_data, "
    "geo_radius_m, budget_max, participants_min, participants_max, visibility, "
    "notification_level, status, version, time_start, time_end, expires_at, "
    "created_at, updated_at"
)


class _UserRowI:
    """intents用user行(id・birth_date・consent_at — intents/store.UserRow相当)。"""

    __slots__ = ("id", "birth_date", "consent_at")

    def __init__(self, id, birth_date, consent_at):  # noqa: A002, ANN001
        self.id = id
        self.birth_date = birth_date
        self.consent_at = consent_at


class _StubParserAdapter:
    """StubLLM → SupportsParseIntent(LMWGateway.parse_intent相当)。

    stub応答のみ(実LLM呼出しは承認待ち — 到達しない)。遅延なし。
    """

    def __init__(self, stub) -> None:
        self._stub = stub
        self.name = "stub"

    async def parse_intent(
        self, *, text: str, current_date: date, user_id: str | None = None
    ) -> dict:
        return await self._stub.complete_structured(text, current_date)


def build_parse_service(clock, user_lookup, consent_lookup) -> IntentParseService:
    """StubLLMでparseサービスを構築(make_intent_parse_serviceのstub経路)。"""
    from latch.llm.stub import StubLLM

    return IntentParseService(
        clock=clock,
        parser=_StubParserAdapter(StubLLM()),
        user_lookup=user_lookup,
        consent_lookup=consent_lookup,
    )


def _row_from_db(row: dict) -> IntentRow:
    return IntentRow(
        id=uuid_mod.UUID(str(row["id"])),
        user_id=uuid_mod.UUID(str(row["user_id"])),
        category_primary=row["category_primary"] or "",
        alcohol_involved=bool(row["alcohol_involved"]),
        raw_text=row["raw_text"],
        structured_data=loads_json(row["structured_data"], {}),
        geo_radius_m=row["geo_radius_m"],
        budget_max=row["budget_max"],
        participants_min=row["participants_min"],
        participants_max=row["participants_max"],
        visibility=row["visibility"],
        notification_level=row["notification_level"],
        status=row["status"],
        version=row["version"],
        time_start=from_micros(row["time_start"]) if row["time_start"] else None,
        time_end=from_micros(row["time_end"]) if row["time_end"] else None,
        expires_at=from_micros(row["expires_at"]) if row["expires_at"] else None,
        created_at=from_micros(row["created_at"]),
        updated_at=from_micros(row["updated_at"]),
    )


def _row_from_cols(
    cols: ResolvedColumns,
    *,
    intent_id: uuid_mod.UUID,
    user_id: uuid_mod.UUID,
    status: str,
    now: datetime,
    version: int,
) -> IntentRow:
    return IntentRow(
        id=intent_id,
        user_id=user_id,
        category_primary=cols.category_primary,
        alcohol_involved=cols.alcohol_involved,
        raw_text=cols.raw_text,
        structured_data=dict(cols.structured_data),
        geo_radius_m=cols.geo_radius_m,
        budget_max=cols.budget_max,
        participants_min=cols.participants_min,
        participants_max=cols.participants_max,
        visibility=cols.visibility,
        notification_level=cols.notification_level,
        status=status,
        version=version,
        time_start=cols.time_start,
        time_end=cols.time_end,
        expires_at=cols.expires_at,
        created_at=now,
        updated_at=now,
    )


def _fields_from_cols(cols: ResolvedColumns, *, status: str) -> dict:
    sd = cols.structured_data or {}
    return {
        "category_primary": cols.category_primary,
        "category_secondary": sd.get("category_secondary"),
        "alcohol_involved": cols.alcohol_involved,
        "raw_text": cols.raw_text,
        "structured_data": sd,
        "geo_lon": cols.geo_lon,
        "geo_lat": cols.geo_lat,
        "geo_radius_m": cols.geo_radius_m,
        "budget_max": cols.budget_max,
        "participants_min": cols.participants_min,
        "participants_max": cols.participants_max,
        "visibility": cols.visibility,
        "notification_level": cols.notification_level,
        "status": status,
        "time_start": cols.time_start,
        "time_end": cols.time_end,
        "expires_at": cols.expires_at,
    }


class D1IntentService(IntentService):
    """intents CRUDのD1版(本体検証規則 + atomic.apply_intent書込)。

    schedule/dispatcher(任意): 意図確定後のパイプラインhook
    (request_evaluation/mark_paused + outbox envelopeのベストエフォート配送)。
    hook失敗はAPI応答に影響しない(cronのoutbox掃きとschedule dueが評価を保証)。
    """

    def __init__(
        self,
        *,
        clock,
        db,
        atomic: D1AtomicRepository,
        geocoder,
        limiter=None,
        schedule=None,
        dispatcher=None,
    ) -> None:
        self._clock = clock
        self._d1 = db
        self._atomic = atomic
        self._geocoder = geocoder
        self._limiter = limiter
        self._schedule = schedule
        self._dispatcher = dispatcher

    async def _after_pipeline_event(
        self, intent_id: str, version: int, event: str, now
    ) -> None:
        """意図確定後のpipeline hook(§4配送・§5.4 I3・ベストエフォート)。"""
        if self._schedule is None and self._dispatcher is None:
            return
        try:
            if self._schedule is not None:
                if event in ("created", "updated", "resumed"):
                    await self._schedule.request_evaluation(
                        intent_id,
                        version,
                        "initial" if event == "created" else "updated",
                        now,
                    )
                else:  # paused / deleted
                    await self._schedule.mark_paused(intent_id, True)
            if self._dispatcher is not None and event in (
                "created",
                "updated",
                "resumed",
            ):
                reason = "updated" if event == "resumed" else event
                await self._dispatcher.dispatch_event(
                    event_id=f"{reason}:{intent_id}:{version}",
                    intent_id=intent_id,
                    intent_version=version,
                    reason=reason,
                )
        except Exception:  # noqa: BLE001 — cron(outbox掃き+schedule due)が保証
            pass

    # -- 共通ヘルパー(本体IntentServiceと同一規則) --

    async def _require_user(self, provider: str, subject: str) -> _UserRowI:
        from latch.intents.errors import IntentNotFoundError

        row = (
            await self._d1.prepare(
                "SELECT id, birth_date, consent_at FROM users"
                " WHERE auth_provider = ? AND auth_subject = ?"
            )
            .bind(provider, subject)
            .first()
        )
        if row is None:
            raise IntentNotFoundError("user not found")
        return _UserRowI(
            uuid_mod.UUID(str(row["id"])),
            date.fromisoformat(str(row["birth_date"])),
            from_micros(row["consent_at"]) if row["consent_at"] else None,
        )

    def _require_age_20(self, user: _UserRowI) -> None:
        from latch.domain.time import age_years
        from latch.intents.errors import UnderAgeError

        if age_years(user.birth_date, self._clock.jst_date()) < 20:
            raise UnderAgeError("under 20 years old")

    @staticmethod
    def _require_consent(user: _UserRowI) -> None:
        from latch.intents.errors import ConsentRequiredError

        if user.consent_at is None:
            raise ConsentRequiredError("consent required")

    @staticmethod
    def _validate_required3(inp: StructuredIntentInput) -> None:
        from latch.intents.errors import IntentValidationError

        if inp.category is None or inp.category.primary is None:
            raise IntentValidationError("category is required")
        if inp.time is None or inp.time.start is None:
            raise IntentValidationError("time.start is required")
        if inp.location is None or inp.location.name is None:
            raise IntentValidationError("location is required")

    @classmethod
    def _validate_times(cls, inp: StructuredIntentInput, *, now: datetime) -> None:
        from latch.intents.errors import IntentValidationError

        start = inp.time.start if inp.time else None
        if start is not None:
            if start < now:
                raise IntentValidationError("time.start is in the past")
            if start > now + _MAX_AHEAD:
                raise IntentValidationError("time.start exceeds 7 days")
        if inp.expires_at is not None:
            if inp.expires_at < now:
                raise IntentValidationError("expires_at is in the past")
            if inp.expires_at > now + _MAX_AHEAD:
                raise IntentValidationError("expires_at exceeds 7 days")

    def _resolve_active_or_raise(
        self, inp: StructuredIntentInput, *, now: datetime
    ) -> ResolvedColumns:
        self._validate_required3(inp)
        self._validate_times(inp, now=now)
        return resolve_for_active(inp, now=now)

    async def _geocode_or_raise(
        self, name: str, cols: ResolvedColumns
    ) -> ResolvedColumns:
        from latch.intents.errors import GeocodingFailedError

        feature = await self._geocoder.geocode_forward(name)
        if feature is None:
            raise GeocodingFailedError("geocoding failed")
        return replace(cols, geo_lon=feature.lon, geo_lat=feature.lat)

    async def _check_active_limit(self, user_id, *, now: datetime) -> None:
        from latch.intents.errors import ActiveIntentLimitError

        row = (
            await self._d1.prepare(
                "SELECT count(*) AS c FROM intents"
                " WHERE user_id = ? AND status = 'active'"
                " AND (expires_at IS NULL OR expires_at > ?)"
            )
            .bind(str(user_id), micros(now))
            .first()
        )
        count = int(row["c"]) if row is not None else 0
        if count >= self._limiter.active_limit:  # type: ignore[union-attr]
            raise ActiveIntentLimitError(
                "active intent limit reached; pause or let expire an intent"
            )

    async def _fetch(self, intent_id) -> IntentRow | None:
        row = (
            await self._d1.prepare(f"SELECT {_ROW_COLS} FROM intents WHERE id = ?")
            .bind(str(intent_id))
            .first()
        )
        return _row_from_db(row) if row is not None else None

    def _apply_update(
        self,
        *,
        intent_id,
        op: str,
        fields: dict,
        version: int,
        now: datetime,
    ):
        return self._atomic.apply_intent(
            str(uuid_mod.uuid4()),
            None,
            {
                "op": op,
                "intent_id": str(intent_id),
                "user_id": fields["_user_id"],
                "fields": fields,
                "version": version,
            },
            now,
        )

    @staticmethod
    def _map_conflict(result):
        """apply_intentのconflict → 参照実装のInvalidTransitionError(422)。"""
        from latch.domain.types import CommandStatus
        from latch.intents.errors import InvalidTransitionError

        if result.status == CommandStatus.CONFLICT:
            raise InvalidTransitionError("intent status changed")
        if result.status == CommandStatus.REJECTED:
            if result.error_code == "NOT_FOUND":
                from latch.intents.errors import IntentNotFoundError

                raise IntentNotFoundError("intent not found")
            if result.error_code == "FORBIDDEN":
                from latch.intents.errors import ForbiddenError

                raise ForbiddenError("not owner")
            if result.error_code in ("STALE", None):
                raise InvalidTransitionError("intent status changed")
        return result

    # -- POST /v1/intents --

    async def create(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        raw_text: str,
        status: str,
        structured_intent: StructuredIntentInput | None,
    ) -> IntentRow:
        from latch.intents.errors import DependencyUnavailableError

        try:
            return await self._create(
                auth_provider=auth_provider,
                auth_subject=auth_subject,
                raw_text=raw_text,
                status=status,
                inp=structured_intent or StructuredIntentInput(),
            )
        except Exception as exc:
            from latch.intents.errors import IntentsError
            from latch.ratelimit.errors import RateLimitedError

            if isinstance(exc, (IntentsError, RateLimitedError)):
                raise
            raise DependencyUnavailableError(
                "intents dependency unavailable"
            ) from exc

    async def _create(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        raw_text: str,
        status: str,
        inp: StructuredIntentInput,
    ) -> IntentRow:
        user = await self._require_user(auth_provider, auth_subject)
        now = self._clock.now()
        if self._limiter is not None:
            await self._limiter.check_create(user_id=user.id)
        if status == "active":
            # 検証順序は本体と同一: 同意→active上限→必須3/時刻→年齢→ジオコーディング
            self._require_consent(user)
            if self._limiter is not None:
                await self._check_active_limit(user.id, now=now)
            cols = self._resolve_active_or_raise(inp, now=now)
            if cols.alcohol_involved:
                self._require_age_20(user)
            cols = await self._geocode_or_raise(inp.location.name, cols)
            cols = replace(cols, raw_text=raw_text)
            result = await self._atomic.apply_intent(
                str(uuid_mod.uuid4()),
                None,
                {
                    "op": "create",
                    "user_id": str(user.id),
                    "fields": _fields_from_cols(cols, status="active"),
                },
                now,
            )
            intent_id = uuid_mod.UUID(result.response["intent"]["id"])
            await self._after_pipeline_event(
                result.response["intent"]["id"],
                int(result.response["intent"]["version"]),
                "created",
                now,
            )
            return _row_from_cols(
                cols,
                intent_id=intent_id,
                user_id=user.id,
                status="active",
                now=now,
                version=1,
            )
        cols = replace(resolve_for_draft(inp), raw_text=raw_text)
        result = await self._atomic.apply_intent(
            str(uuid_mod.uuid4()),
            None,
            {
                "op": "create",
                "user_id": str(user.id),
                "fields": _fields_from_cols(cols, status="draft"),
            },
            now,
        )
        intent_id = uuid_mod.UUID(result.response["intent"]["id"])
        return _row_from_cols(
            cols,
            intent_id=intent_id,
            user_id=user.id,
            status="draft",
            now=now,
            version=1,
        )

    # -- GET /v1/intents/{id} --

    async def get(
        self, *, auth_provider: str, auth_subject: str, intent_id
    ) -> IntentRow:
        from latch.intents.errors import (
            DependencyUnavailableError,
            ForbiddenError,
            IntentNotFoundError,
        )

        try:
            user = await self._require_user(auth_provider, auth_subject)
            row = await self._fetch(intent_id)
            if row is None:
                raise IntentNotFoundError("intent not found")
            if row.user_id != user.id:
                raise ForbiddenError("not owner")
            return row
        except Exception as exc:
            from latch.intents.errors import IntentsError

            if isinstance(exc, IntentsError):
                raise
            raise DependencyUnavailableError(
                "intents dependency unavailable"
            ) from exc

    # -- GET /v1/intents --

    async def list(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        status: str | None,
        cursor: str | None,
        limit: int,
    ) -> PageResult:
        from latch.intents.errors import DependencyUnavailableError

        try:
            user = await self._require_user(auth_provider, auth_subject)
            before = decode_cursor(cursor) if cursor else None
            clauses = ["user_id = ?"]
            params: list = [str(user.id)]
            if status is not None:
                clauses.append("status = ?")
                params.append(status)
            if before is not None:
                clauses.append("(created_at < ? OR (created_at = ? AND id < ?))")
                params.extend(
                    [micros(before[0]), micros(before[0]), str(before[1])]
                )
            rows = (
                await self._d1.prepare(
                    f"SELECT {_ROW_COLS} FROM intents WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY created_at DESC, id DESC LIMIT ?"
                )
                .bind(*params, limit + 1)
                .all()
            )
            items = [_row_from_db(r) for r in rows[:limit]]
            next_cursor = None
            if len(rows) > limit:
                last = items[limit - 1]
                next_cursor = encode_cursor(last.created_at, last.id)
            return PageResult(rows=items, next_cursor=next_cursor)
        except Exception as exc:
            from latch.intents.errors import IntentsError

            if isinstance(exc, IntentsError):
                raise
            raise DependencyUnavailableError(
                "intents dependency unavailable"
            ) from exc

    # -- PATCH /v1/intents/{id} --

    async def update(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        intent_id,
        raw_text: str,
        status: str | None,
        structured_intent: StructuredIntentInput | None,
    ) -> IntentRow:
        try:
            return await self._update(
                auth_provider=auth_provider,
                auth_subject=auth_subject,
                intent_id=intent_id,
                raw_text=raw_text,
                status=status,
                structured_intent=structured_intent,
            )
        except Exception as exc:
            from latch.intents.errors import (
                DependencyUnavailableError,
                IntentsError,
            )
            from latch.ratelimit.errors import RateLimitedError

            if isinstance(exc, (IntentsError, RateLimitedError)):
                raise
            raise DependencyUnavailableError(
                "intents dependency unavailable"
            ) from exc

    async def _update(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        intent_id,
        raw_text: str,
        status: str | None,
        structured_intent: StructuredIntentInput | None,
    ) -> IntentRow:
        user = await self._require_user(auth_provider, auth_subject)
        now = self._clock.now()
        if self._limiter is not None:
            await self._limiter.check_update(intent_id=intent_id)
        row = await self._fetch(intent_id)
        if row is None:
            from latch.intents.errors import IntentNotFoundError

            raise IntentNotFoundError("intent not found")
        if row.user_id != user.id:
            from latch.intents.errors import ForbiddenError

            raise ForbiddenError("not owner")
        target = status if status is not None else row.status
        if row.status == "draft":
            return await self._update_draft(
                row=row,
                target=target,
                raw_text=raw_text,
                inp=structured_intent,
                user=user,
                now=now,
            )
        if row.status in ("active", "paused"):
            return await self._update_active(
                row=row,
                raw_text=raw_text,
                inp=structured_intent,
                user=user,
                now=now,
            )
        from latch.intents.errors import InvalidTransitionError

        raise InvalidTransitionError("intent is not editable")

    async def _update_active(
        self,
        *,
        row: IntentRow,
        raw_text: str,
        inp: StructuredIntentInput | None,
        user: _UserRowI,
        now: datetime,
    ) -> IntentRow:
        """active/paused行の内容更新(全置換・全検証・version+1・status維持)。"""
        from latch.intents.errors import IntentValidationError

        if inp is None:
            raise IntentValidationError("structured_intent is required")
        cols = self._resolve_active_or_raise(inp, now=now)
        if cols.alcohol_involved:
            self._require_age_20(user)
        cols = await self._geocode_or_raise(inp.location.name, cols)
        cols = replace(cols, raw_text=raw_text)
        new_version = row.version + 1
        fields = _fields_from_cols(cols, status=row.status)
        fields["_user_id"] = str(user.id)
        result = self._map_conflict(
            await self._apply_update(
                intent_id=row.id,
                op="update",
                fields=fields,
                version=new_version,
                now=now,
            )
        )
        await self._after_pipeline_event(str(row.id), new_version, "updated", now)
        out = _row_from_cols(
            cols,
            intent_id=row.id,
            user_id=row.user_id,
            status=row.status,
            now=now,
            version=new_version,
        )
        return out

    async def _update_draft(
        self,
        *,
        row: IntentRow,
        target: str,
        raw_text: str,
        inp: StructuredIntentInput | None,
        user: _UserRowI,
        now: datetime,
    ) -> IntentRow:
        """draft再保存(検証なし)とdraft→active化(全検証)。"""
        from latch.intents.errors import IntentValidationError

        if target != "active":
            if inp is None:
                cols = replace(columns_from_row(row), raw_text=raw_text)
            else:
                cols = replace(resolve_for_draft(inp), raw_text=raw_text)
            new_version = row.version + 1
            fields = _fields_from_cols(cols, status="draft")
            fields["_user_id"] = str(user.id)
            self._map_conflict(
                await self._apply_update(
                    intent_id=row.id,
                    op="update",
                    fields=fields,
                    version=new_version,
                    now=now,
                )
            )
            return _row_from_cols(
                cols,
                intent_id=row.id,
                user_id=row.user_id,
                status="draft",
                now=now,
                version=new_version,
            )
        # draft→active化: 全量必須・全検証(不通なら422でdraft据え置き)
        self._require_consent(user)
        if self._limiter is not None:
            await self._check_active_limit(user.id, now=now)
        if inp is None:
            raise IntentValidationError("structured_intent is required")
        cols = self._resolve_active_or_raise(inp, now=now)
        if cols.alcohol_involved:
            self._require_age_20(user)
        cols = await self._geocode_or_raise(inp.location.name, cols)
        cols = replace(cols, raw_text=raw_text)
        # version判定はdraft表現での列比較(§2.7)
        draft_cols = replace(resolve_for_draft(inp), raw_text=raw_text)
        new_version = (
            row.version + 1 if differs_from_row(draft_cols, row) else row.version
        )
        fields = _fields_from_cols(cols, status="active")
        fields["_user_id"] = str(user.id)
        self._map_conflict(
            await self._apply_update(
                intent_id=row.id,
                op="update",
                fields=fields,
                version=new_version,
                now=now,
            )
        )
        await self._after_pipeline_event(str(row.id), new_version, "updated", now)
        return _row_from_cols(
            cols,
            intent_id=row.id,
            user_id=row.user_id,
            status="active",
            now=now,
            version=new_version,
        )

    # -- pause / resume / DELETE --

    async def pause(
        self, *, auth_provider: str, auth_subject: str, intent_id
    ) -> IntentRow:
        return await self._transition(
            auth_provider=auth_provider,
            auth_subject=auth_subject,
            intent_id=intent_id,
            allowed_from=("active",),
            new_status="paused",
            version_delta=0,
            op="pause",
        )

    async def resume(
        self, *, auth_provider: str, auth_subject: str, intent_id
    ) -> IntentRow:
        return await self._transition(
            auth_provider=auth_provider,
            auth_subject=auth_subject,
            intent_id=intent_id,
            allowed_from=("paused",),
            new_status="active",
            version_delta=1,
            op="resume",
            rate_limit_update=True,
            check_active=True,
        )

    async def delete(
        self, *, auth_provider: str, auth_subject: str, intent_id
    ) -> None:
        await self._transition(
            auth_provider=auth_provider,
            auth_subject=auth_subject,
            intent_id=intent_id,
            allowed_from=(
                "draft",
                "active",
                "paused",
                "matched",
                "expired",
                "cancelled",
            ),
            new_status="cancelled",
            version_delta=0,
            op="delete",
        )

    async def _transition(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        intent_id,
        allowed_from: tuple[str, ...],
        new_status: str,
        version_delta: int,
        op: str,
        rate_limit_update: bool = False,
        check_active: bool = False,
    ) -> IntentRow:
        from latch.intents.errors import (
            DependencyUnavailableError,
            ForbiddenError,
            IntentNotFoundError,
            InvalidTransitionError,
            IntentsError,
        )
        from latch.ratelimit.errors import RateLimitedError

        try:
            user = await self._require_user(auth_provider, auth_subject)
            now = self._clock.now()
            if self._limiter is not None and rate_limit_update:
                await self._limiter.check_update(intent_id=intent_id)
            row = await self._fetch(intent_id)
            if row is None:
                raise IntentNotFoundError("intent not found")
            if row.user_id != user.id:
                raise ForbiddenError("not owner")
            if row.status not in allowed_from:
                raise InvalidTransitionError("invalid status transition")
            if self._limiter is not None and check_active:
                await self._check_active_limit(user.id, now=now)
            new_version = row.version + version_delta
            if op == "delete":
                result = await self._atomic.apply_intent(
                    str(uuid_mod.uuid4()),
                    None,
                    {
                        "op": "delete",
                        "intent_id": str(row.id),
                        "user_id": str(user.id),
                    },
                    now,
                )
            else:
                fields = {"status": new_status, "_user_id": str(user.id)}
                result = self._map_conflict(
                    await self._apply_update(
                        intent_id=row.id,
                        op=op,
                        fields=fields,
                        version=new_version,
                        now=now,
                    )
                )
            self._map_conflict(result)
            event = {"pause": "paused", "resume": "resumed", "delete": "deleted"}.get(
                op, "updated"
            )
            await self._after_pipeline_event(str(row.id), new_version, event, now)
            return replace(
                row,
                status=new_status,
                version=new_version,
                updated_at=now,
            )
        except Exception as exc:
            if isinstance(exc, (IntentsError, RateLimitedError)):
                raise
            raise DependencyUnavailableError(
                "intents dependency unavailable"
            ) from exc
