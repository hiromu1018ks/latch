"""parseユースケース(design §2.7)。

IntentParseService 本体は LLM 非依存・DB非依存: Gateway は
SupportsParseIntent Protocol への構造的適合(design §2.2)、user_lookup は
Callable注入。フロー: user_lookup(失敗→503)→ parse_intent(current_date=
clock.jst_date()、失敗→503 LLM_UNAVAILABLE)→ 規則5正規化 →
ParserOutput.model_validate(ValidationError→422)→ warnings構築。
"""

from __future__ import annotations

import base64
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError

from latch.core.clock import Clock
from latch.geo.service import Geofeature
from latch.intents.errors import (
    DependencyUnavailableError,
    ForbiddenError,
    GeocodingFailedError,
    IntentNotFoundError,
    IntentsError,
    IntentValidationError,
    InvalidTransitionError,
    LLMUnavailableError,
    UnderAgeError,
    UnstructurableError,
)
from latch.intents.events import (
    EVENT_CREATED,
    EVENT_DELETED,
    EVENT_UPDATED,
    insert_match_event,
)
from latch.intents.intent_input import StructuredIntentInput
from latch.intents.mapping import (
    ResolvedColumns,
    columns_from_row,
    differs_from_row,
    resolve_for_active,
    resolve_for_draft,
)
from latch.intents.schema import WARNING_MESSAGE_NG_DOWNGRADED, ParserOutput
from latch.intents.store import IntentRow, IntentStore, UserRow
from latch.llm.gateway import build_llm_gateway
from latch.settings import Settings
from latch.users.service import age_years

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

logger = logging.getLogger("latch.intents")

UserLookup = Callable[[str, str], Awaitable[uuid.UUID | None]]


class SupportsParseIntent(Protocol):
    """Parser系統の構造的Protocol(design §2.2)。LLMGateway.parse_intent と適合。

    失敗は Gateway 契約どおり LLMTimeoutError / LLMProviderError
    (latch.llm.errors)を送出する(サービスは基底をimportせず
    Exception として受ける — intents/のllm非依存を守るため)。
    """

    async def parse_intent(
        self, *, text: str, current_date: date, user_id: str | None = None
    ) -> dict: ...


@dataclass(frozen=True)
class ParseWarning:
    """D-04の注意表示1件(05 §5応答例のwarnings要素と同形)。"""

    code: str  # "NG_CONDITION_DOWNGRADED"
    condition: str
    message: str  # WARNING_MESSAGE_NG_DOWNGRADED


@dataclass(frozen=True)
class ParseResult:
    """parseユースケースの結果(routes が応答へ変換する)。"""

    structured_intent: ParserOutput
    warnings: list[ParseWarning]


def _normalize_rule5(raw: dict) -> dict:
    """規則5違反の回復(design §2.5)。

    negative_constraints 非空(LLM違反)の要素を ng_unverifiable へ結合し、
    negative_constraints は空配列で応答する(FR-42の不変式回復。「常に空」の
    回復できる唯一の場所=parse境界)。結合由来の条件は warnings 生成対象に
    なるため D-04 の注意表示も出る(黙って降格させない)。
    """
    normalized = dict(raw)
    negative = normalized.get("negative_constraints") or []
    ng = list(normalized.get("ng_unverifiable") or [])
    normalized["negative_constraints"] = []
    normalized["ng_unverifiable"] = [*ng, *negative]
    return normalized


class IntentParseService:
    """POST /v1/intents/parse のユースケース(同期・再試行なしはGateway側)。"""

    def __init__(
        self,
        *,
        clock: Clock,
        parser: SupportsParseIntent,
        user_lookup: UserLookup,
    ) -> None:
        self._clock = clock
        self._parser = parser
        self._user_lookup = user_lookup

    async def parse(
        self, *, text: str, auth_provider: str, auth_subject: str
    ) -> ParseResult:
        try:
            user_id = await self._user_lookup(auth_provider, auth_subject)
        except Exception as exc:
            raise DependencyUnavailableError(
                "intent parse dependency unavailable"
            ) from exc
        try:
            raw = await self._parser.parse_intent(
                text=text,
                current_date=self._clock.jst_date(),
                user_id=str(user_id) if user_id is not None else None,
            )
        except Exception as exc:
            # Gateway契約上ここで飛ぶのはLLMError系(timeout・API障害)のみ
            # (design §2.7)。検証(ValidationError)は後段なので含まれない。
            raise LLMUnavailableError("intent parser unavailable") from exc
        if not isinstance(raw, dict):
            raise UnstructurableError("structured intent is not extractable")
        try:
            structured = ParserOutput.model_validate(_normalize_rule5(raw))
        except ValidationError as exc:
            raise UnstructurableError("structured intent is not extractable") from exc
        warnings = [
            ParseWarning(
                code="NG_CONDITION_DOWNGRADED",
                condition=condition,
                message=WARNING_MESSAGE_NG_DOWNGRADED,
            )
            for condition in structured.ng_unverifiable
        ]
        return ParseResult(structured_intent=structured, warnings=warnings)


def make_intent_parse_service(
    *, clock: Clock, settings: Settings, user_lookup: UserLookup
) -> IntentParseService:
    """設定からIntentParseServiceを構築する(design §2.7)。

    llm/へのimport(build_llm_gateway)はこのファクトリに限る — サービス本体は
    LLM非依存(design §2.2)。llm_mode="stub" は build_llm_gateway が検証する
    (M0と同一パターン)。
    """
    gateway = build_llm_gateway(clock, settings)
    return IntentParseService(clock=clock, parser=gateway, user_lookup=user_lookup)


# ---------------------------------------------------------------------------
# M1 ws-3: intents CRUD(design §2.2・§2.5〜§2.10)


def _wrap_unexpected(exc: Exception) -> DependencyUnavailableError:
    """予期しない例外を503へ包む。例外のクラス名のみログへ残す(08 §2.4)。

    メッセージ本文・ユーザー由来の内容は出さない。クラス名は検証・障害時に
    原因の切り分けへ使う最小の情報(実検証で原因例外不明の503が発生した
    欠陥の運用性改善)。
    """
    logger.warning("intents.unexpected class=%s", type(exc).__name__)
    return DependencyUnavailableError("intents dependency unavailable")


class SupportsForwardGeocoding(Protocol):
    """正転ジオコーディングの構造的Protocol(design §2.5)。GeoServiceと適合。

    該当なしはNone(422 GEOCODING_FAILEDへ写像はサービス側)。例外は
    DB障害のみ(サービスが503へ包む)。
    """

    async def geocode_forward(self, name: str) -> Geofeature | None: ...


UnitOfWork = Callable[[], AbstractAsyncContextManager["AsyncConnection"]]
Reader = Callable[[], AbstractAsyncContextManager["AsyncConnection"]]

_MAX_AHEAD = timedelta(days=7)  # 対象領域上限(05 §5時刻検証)


def encode_cursor(created_at: datetime, intent_id: uuid.UUID) -> str:
    """キーセットcursor(design §2.10): base64url("ISO8601|uuid")。"""
    raw = f"{created_at.isoformat()}|{intent_id}"
    return base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """cursorの復元。形式不正は422 VALIDATION_ERROR(design §2.10)。"""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        ts_part, id_part = raw.split("|")
        return datetime.fromisoformat(ts_part), uuid.UUID(id_part)
    except (ValueError, UnicodeDecodeError) as exc:
        raise IntentValidationError("invalid cursor") from exc


@dataclass(frozen=True)
class PageResult:
    """GET /v1/intents のページ結果(routesが応答へ変換 — design §2.10)。"""

    rows: list[IntentRow]
    next_cursor: str | None


class IntentService:
    """intents CRUDユースケース(05 §5〜§6・design §2.2のEvent発行表)。

    検証順序は形式(Pydantic・routes)→ 必須3 → 時刻 → 年齢 → ジオコーディン
    グ → 保存に固定(design §2.5)。書き込みはuow(engine.begin)のトランザク
    ション内でstore・insert_match_eventを呼び、読み取りはreaderで行う。
    """

    def __init__(
        self,
        *,
        clock: Clock,
        store: IntentStore,
        uow: UnitOfWork,
        reader: Reader,
        geocoder: SupportsForwardGeocoding,
    ) -> None:
        self._clock = clock
        self._store = store
        self._uow = uow
        self._reader = reader
        self._geocoder = geocoder

    # -- 共通の検証・参照ヘルパー --

    async def _require_user(self, provider: str, subject: str) -> UserRow:
        row = await self._store.fetch_user_row(provider, subject)
        if row is None:
            # 未登録JWT=初回登録待ち。users/meと同一挙動(design §2.6)
            raise IntentNotFoundError("user not found")
        return row

    def _require_age_20(self, user: UserRow) -> None:
        if age_years(user.birth_date, self._clock.jst_date()) < 20:
            raise UnderAgeError("under 20 years old")

    @staticmethod
    def _validate_required3(inp: StructuredIntentInput) -> None:
        if inp.category is None or inp.category.primary is None:
            raise IntentValidationError("category is required")
        if inp.time is None or inp.time.start is None:
            raise IntentValidationError("time.start is required")
        if inp.location is None or inp.location.name is None:
            raise IntentValidationError("location is required")

    @classmethod
    def _validate_times(cls, inp: StructuredIntentInput, *, now: datetime) -> None:
        """過去不可・現在+7日上限(05 §5。境界: nowちょうど/now+7日ちょうどは受理)。"""
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
        feature = await self._geocoder.geocode_forward(name)
        if feature is None:
            raise GeocodingFailedError("geocoding failed")
        return replace(cols, geo_lon=feature.lon, geo_lat=feature.lat)

    @staticmethod
    def _row_from_cols(
        cols: ResolvedColumns,
        *,
        intent_id: uuid.UUID,
        user_id: uuid.UUID,
        status: str,
        now: datetime,
    ) -> IntentRow:
        """書き込んだ内容からそのまま応答行を組み立てる(DB再SELECTしない)。"""
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
            version=cols.version,
            time_start=cols.time_start,
            time_end=cols.time_end,
            expires_at=cols.expires_at,
            created_at=now,
            updated_at=now,
        )

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
        try:
            return await self._create(
                auth_provider=auth_provider,
                auth_subject=auth_subject,
                raw_text=raw_text,
                status=status,
                inp=structured_intent or StructuredIntentInput(),
            )
        except IntentsError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

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
        if status == "active":
            cols = self._resolve_active_or_raise(inp, now=now)
            if cols.alcohol_involved:
                self._require_age_20(user)
            cols = await self._geocode_or_raise(inp.location.name, cols)
            cols = replace(cols, raw_text=raw_text)
            async with self._uow() as conn:
                intent_id = await self._store.insert(
                    conn, cols, user_id=user.id, status="active", now=now
                )
                await insert_match_event(
                    conn,
                    event_type=EVENT_CREATED,
                    intent_id=intent_id,
                    version=cols.version,
                    now=now,
                )
            return self._row_from_cols(
                cols, intent_id=intent_id, user_id=user.id, status="active", now=now
            )
        cols = replace(resolve_for_draft(inp), raw_text=raw_text)
        async with self._uow() as conn:
            intent_id = await self._store.insert(
                conn, cols, user_id=user.id, status="draft", now=now
            )
        return self._row_from_cols(
            cols, intent_id=intent_id, user_id=user.id, status="draft", now=now
        )

    # -- GET /v1/intents/{id} --

    async def get(
        self, *, auth_provider: str, auth_subject: str, intent_id: uuid.UUID
    ) -> IntentRow:
        try:
            user = await self._require_user(auth_provider, auth_subject)
            async with self._reader() as conn:
                row = await self._store.fetch(conn, intent_id)
            if row is None:
                raise IntentNotFoundError("intent not found")
            if row.user_id != user.id:
                raise ForbiddenError("not owner")
            return row
        except IntentsError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

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
        try:
            user = await self._require_user(auth_provider, auth_subject)
            before = decode_cursor(cursor) if cursor else None
            async with self._reader() as conn:
                rows = await self._store.list_page(
                    conn, user.id, status=status, before=before, limit=limit + 1
                )
            if len(rows) > limit:
                last = rows[limit - 1]
                return PageResult(
                    rows=rows[:limit],
                    next_cursor=encode_cursor(last.created_at, last.id),
                )
            return PageResult(rows=rows, next_cursor=None)
        except IntentsError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

    # -- PATCH /v1/intents/{id}(design §2.10のPATCH分岐・§2.7)--

    async def update(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        intent_id: uuid.UUID,
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
        except IntentsError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc

    async def _update(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        intent_id: uuid.UUID,
        raw_text: str,
        status: str | None,
        structured_intent: StructuredIntentInput | None,
    ) -> IntentRow:
        user = await self._require_user(auth_provider, auth_subject)
        now = self._clock.now()
        async with self._uow() as conn:
            row = await self._store.fetch_for_update(conn, intent_id)
            if row is None:
                raise IntentNotFoundError("intent not found")
            if row.user_id != user.id:
                raise ForbiddenError("not owner")
            target = status if status is not None else row.status
            if row.status == "draft":
                return await self._update_draft(
                    conn,
                    row=row,
                    target=target,
                    raw_text=raw_text,
                    inp=structured_intent,
                    user=user,
                    now=now,
                )
            if row.status in ("active", "paused"):
                return await self._update_active(
                    conn,
                    row=row,
                    target=target,
                    raw_text=raw_text,
                    inp=structured_intent,
                    user=user,
                    now=now,
                )
            raise InvalidTransitionError("intent is not editable")

    async def _update_active(
        self,
        conn,
        *,
        row: IntentRow,
        target: str,
        raw_text: str,
        inp: StructuredIntentInput | None,
        user: UserRow,
        now: datetime,
    ) -> IntentRow:
        """active/paused行の内容更新(全置換・全検証・version+1・updated Event)。"""
        if target == "draft":
            raise IntentValidationError("active to draft is not allowed")
        if inp is None:
            raise IntentValidationError("structured_intent is required")
        cols = self._resolve_active_or_raise(inp, now=now)
        if cols.alcohol_involved:
            self._require_age_20(user)
        cols = await self._geocode_or_raise(inp.location.name, cols)
        cols = replace(cols, raw_text=raw_text)
        new_version = row.version + 1
        count = await self._store.update(
            conn,
            row.id,
            cols,
            status=row.status,
            version=new_version,
            now=now,
            expected_status=row.status,
        )
        if count == 0:
            raise InvalidTransitionError("intent status changed")
        await insert_match_event(
            conn,
            event_type=EVENT_UPDATED,
            intent_id=row.id,
            version=new_version,
            now=now,
        )
        return self._row_with_version(
            self._row_from_cols(
                cols,
                intent_id=row.id,
                user_id=row.user_id,
                status=row.status,
                now=now,
            ),
            new_version,
        )

    async def _update_draft(
        self,
        conn,
        *,
        row: IntentRow,
        target: str,
        raw_text: str,
        inp: StructuredIntentInput | None,
        user: UserRow,
        now: datetime,
    ) -> IntentRow:
        """draft再保存(検証なし)とdraft→active化(全検証 — design §2.10)。"""
        if target != "active":
            # 下書き再保存: structured_intent省略=既存保持・送れば全置換
            if inp is None:
                cols = replace(columns_from_row(row), raw_text=raw_text)
            else:
                cols = replace(resolve_for_draft(inp), raw_text=raw_text)
            new_version = row.version + 1
            count = await self._store.update(
                conn,
                row.id,
                cols,
                status="draft",
                version=new_version,
                now=now,
                expected_status="draft",
            )
            if count == 0:
                raise InvalidTransitionError("intent status changed")
            return self._row_with_version(
                self._row_from_cols(
                    cols,
                    intent_id=row.id,
                    user_id=row.user_id,
                    status="draft",
                    now=now,
                ),
                new_version,
            )
        # draft→active化: 全量必須・全検証(不通なら422でdraft据え置き)
        if inp is None:
            raise IntentValidationError("structured_intent is required")
        cols = self._resolve_active_or_raise(inp, now=now)
        if cols.alcohol_involved:
            self._require_age_20(user)
        cols = await self._geocode_or_raise(inp.location.name, cols)
        cols = replace(cols, raw_text=raw_text)
        # version判定はdraft表現での列比較(§2.7): 補完込みで比較すると
        # 同一内容でも常に相違となるため、リクエストをdraft用resolveした結果と比較
        draft_cols = replace(resolve_for_draft(inp), raw_text=raw_text)
        new_version = (
            row.version + 1 if differs_from_row(draft_cols, row) else row.version
        )
        count = await self._store.update(
            conn,
            row.id,
            cols,
            status="active",
            version=new_version,
            now=now,
            expected_status="draft",
        )
        if count == 0:
            raise InvalidTransitionError("intent status changed")
        await insert_match_event(
            conn,
            event_type=EVENT_CREATED,  # 初回投入は作成種(06 §9-0・確定値14)
            intent_id=row.id,
            version=new_version,
            now=now,
        )
        return self._row_with_version(
            self._row_from_cols(
                cols,
                intent_id=row.id,
                user_id=row.user_id,
                status="active",
                now=now,
            ),
            new_version,
        )

    @staticmethod
    def _row_with_version(row: IntentRow, version: int) -> IntentRow:
        return replace(row, version=version)

    # -- pause / resume / DELETE(§2.8・05 §6遷移表)--

    async def pause(
        self, *, auth_provider: str, auth_subject: str, intent_id: uuid.UUID
    ) -> IntentRow:
        return await self._transition(
            auth_provider=auth_provider,
            auth_subject=auth_subject,
            intent_id=intent_id,
            allowed_from=("active",),
            new_status="paused",
            version_delta=0,
            event_type=None,  # 発行規定なし(pausedはLayer 1対象外)
        )

    async def resume(
        self, *, auth_provider: str, auth_subject: str, intent_id: uuid.UUID
    ) -> IntentRow:
        return await self._transition(
            auth_provider=auth_provider,
            auth_subject=auth_subject,
            intent_id=intent_id,
            allowed_from=("paused",),
            new_status="active",
            version_delta=1,  # 確定値15: キー衝突回避のため必ず+1
            event_type=EVENT_UPDATED,
        )

    async def delete(
        self, *, auth_provider: str, auth_subject: str, intent_id: uuid.UUID
    ) -> None:
        await self._transition(
            auth_provider=auth_provider,
            auth_subject=auth_subject,
            intent_id=intent_id,
            allowed_from=("draft", "active", "paused"),
            new_status="cancelled",  # 物理削除しない(05 §6・M3-8参照)
            version_delta=0,
            event_type=EVENT_DELETED,
        )

    async def _transition(
        self,
        *,
        auth_provider: str,
        auth_subject: str,
        intent_id: uuid.UUID,
        allowed_from: tuple[str, ...],
        new_status: str,
        version_delta: int,
        event_type: str | None,
    ) -> IntentRow:
        try:
            user = await self._require_user(auth_provider, auth_subject)
            now = self._clock.now()
            async with self._uow() as conn:
                row = await self._store.fetch_for_update(conn, intent_id)
                if row is None:
                    raise IntentNotFoundError("intent not found")
                if row.user_id != user.id:
                    raise ForbiddenError("not owner")
                if row.status not in allowed_from:
                    raise InvalidTransitionError("invalid status transition")
                new_version = row.version + version_delta
                count = await self._store.update_status(
                    conn,
                    intent_id,
                    status=new_status,
                    version=new_version,
                    now=now,
                    expected_status=row.status,
                )
                if count == 0:
                    raise InvalidTransitionError("invalid status transition")
                if event_type is not None:
                    await insert_match_event(
                        conn,
                        event_type=event_type,
                        intent_id=intent_id,
                        version=new_version,
                        now=now,
                    )
                return replace(
                    row,
                    status=new_status,
                    version=new_version,
                    updated_at=now,
                )
        except IntentsError:
            raise
        except Exception as exc:
            raise _wrap_unexpected(exc) from exc


def make_intent_service(*, clock: Clock, engine: AsyncEngine) -> IntentService:
    """実SQL束ねてIntentServiceを構築する(design §2.10)。

    latch.geoへのimportはこのファクトリとProtocol戻り値型に限る
    (design §2.5)。保存APIは同期LLM非依存(C8)のためllm/を参照しない。
    """
    from latch.geo.service import GeoService

    return IntentService(
        clock=clock,
        store=IntentStore(engine),
        uow=engine.begin,
        reader=engine.connect,
        geocoder=GeoService(engine),
    )
