"""JevWorker(Layer 4実行部。M2 ws-5・design §2.1案B・§2.5・§2.9の表)。

embedding.pyと同一の2フェーズ構成(短tx読取+選択+H再検証 → tx外API呼び出し →
短txガード付きUPDATE)。起動はembedding_completed起点(Worker._kick_jev)のみだが、
handle(intent_id)は起点非依存のIF(ws-6がBucket再評価・catch-up起点から同一部品
を呼ぶ — design §2.10)。

Guard契約(ws-4引継ぎ): request_execution(intent_id, user_id) を実行直前に呼ぶ
(denyもINCR消費・起点側のみ課税 — 承認済み解釈記録)。record_execution(provider,
day) は実際のAPI呼び出しベースで切替完了側(本worker)が呼ぶ — 計上は1回・
最終経路のみ(design §2.5-3)。

例外方針(design §2.9の表): LLMError・JevOutputInvalidErrorはskipped記録に変換
(例外にしない)。DB書き込み失敗・JevCostDependencyError(Guard Redis失敗)は
伝播させる(fail-closed→_dispatchの既存except→ackなし再配信→duplicate経由で
_kick_jev再実行。冪等ガード jev_result IS NULL で完了分は飛ばす)。
SQLはtext()生SQL・CAST(:x AS ...)形式(§2グローバル制約)。
モジュール属性経由でorigin/layer4を呼ぶ(runnerと同一規律 — unit試験が
monkeypatchで差し替え可能)。
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.core.clock import Clock
from latch.intents.completion import default_time_end
from latch.llm.errors import JevOutputInvalidError, LLMError
from latch.llm.jev import JevTextInput, build_jev_text
from latch.worker.cost import JevCostGuard, JevCostStore
from latch.worker.matching import layer4
from latch.worker.matching import origin as origin_mod
from latch.worker.matching.layer4 import JevCandidateRow, jst_day_start, jst_month_start
from latch.worker.matching.origin import Origin, OriginLoad

logger = logging.getLogger(__name__)

_SKIP_REASON_LLM_FAILURE = "llm_failure"
_SKIP_REASON_INVALID_OUTPUT = "invalid_output"

# 評価結果のガード付きUPDATE(冪等: jev_result IS NULL が二重排除)
_COMPLETE = text("""
    UPDATE match_candidates
    SET jev_result = CAST(:jev AS jsonb), status = 'evaluated',
        skip_reason = NULL, updated_at = :now
    WHERE id = CAST(:row_id AS uuid) AND jev_result IS NULL
    RETURNING id
""")
# deny・LLM失敗・検証失敗の共通skip(jev_resultはNULLのまま)
_SKIP = text("""
    UPDATE match_candidates
    SET status = 'skipped', skip_reason = :reason, updated_at = :now
    WHERE id = CAST(:row_id AS uuid) AND jev_result IS NULL
    RETURNING id
""")
# 相手Intent(または起点)のjev入力列読取(version照合付き)
_SELECT_PEER = text("""
    SELECT version, category_primary, structured_data, participants_min,
           participants_max, time_start, time_end, budget_max, geo_radius_m
    FROM intents WHERE id = CAST(:intent_id AS uuid)
""")


async def _fetch_jev_input(conn, intent_id: uuid.UUID) -> tuple | None:
    """起点自身のjev入力列(_SELECT_PEERと同一SQL・§9-9)。

    origin.load_originがstructured_data全体をOriginへ載せないため
    (origin.pyは変更禁止)、同一tx内で追加読取する。
    """
    return (await conn.execute(_SELECT_PEER, {"intent_id": intent_id})).first()


async def _read_peer(engine: AsyncEngine, peer_id: uuid.UUID) -> tuple | None:
    """相手Intentのjev入力列読取(短tx・読取のみ)。"""
    async with engine.begin() as conn:
        return (await conn.execute(_SELECT_PEER, {"intent_id": peer_id})).first()


async def _complete_row(
    engine: AsyncEngine, row_id: uuid.UUID, jev_result: dict, now
) -> bool:
    """評価結果のガード付きUPDATE(短tx)。False=競合負け(他が先に書いた)。"""
    async with engine.begin() as conn:
        res = await conn.execute(
            _COMPLETE,
            {
                "row_id": row_id,
                "jev": json.dumps(jev_result, ensure_ascii=False),
                "now": now,
            },
        )
        return res.first() is not None


async def _skip_row(engine: AsyncEngine, row_id: uuid.UUID, reason: str, now) -> bool:
    """skip記録のガード付きUPDATE(短tx)。False=競合負け。"""
    async with engine.begin() as conn:
        res = await conn.execute(
            _SKIP, {"row_id": row_id, "reason": reason, "now": now}
        )
        return res.first() is not None


def _parse_structured(value: object) -> dict:
    """structured_dataがstrで返る場合のjson.loads(embedding.pyと同一規律)。"""
    return json.loads(value) if isinstance(value, str) else value


def _jev_input_from_row(row: tuple) -> JevTextInput:
    """_SELECT_PEERの9タプル→JevTextInput(相手側)。

    time_endはNULLならdefault_time_end(time_start)(intents/completion.pyの
    補完規則・load_originと同一)。
    """
    _, category, structured, pmin, pmax, t_start, t_end, budget_max, radius = row
    time_end = t_end
    if time_end is None and t_start is not None:
        time_end = default_time_end(t_start)
    return JevTextInput(
        category_primary=category,
        structured_data=_parse_structured(structured),
        participants_min=pmin,
        participants_max=pmax,
        time_start=t_start,
        time_end=time_end,
        budget_max=budget_max,
        geo_radius_m=radius,
    )


def _origin_input(org: Origin, origin_row: tuple) -> JevTextInput:
    """起点のJevTextInput(Originの補完済み値+追加読取の3列・§9-9)。

    time_endはOrigin.time_end(補完済み契約)をそのまま使う。
    """
    return JevTextInput(
        category_primary=org.category_primary,
        structured_data=_parse_structured(origin_row[2]),
        participants_min=org.participants_min,
        participants_max=org.participants_max,
        time_start=org.time_start,
        time_end=org.time_end,
        budget_max=origin_row[7],
        geo_radius_m=origin_row[8],
    )


class JevWorker:
    """Layer 4実行部(§2.5)。冪等(handle再実行可・jev_result IS NULLガード)。"""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        clock: Clock,
        gateway,
        guard: JevCostGuard,
        cost_store: JevCostStore,
    ) -> None:
        self._engine = engine
        self._clock = clock
        self._gateway = gateway
        self._guard = guard
        self._cost_store = cost_store

    async def handle(self, intent_id: uuid.UUID) -> None:
        """embedding_completed起点(またはws-6の再評価起点)のLayer 4実行。

        フェーズ1(短tx)→フェーズ2+3(ペア毎・直列)。起点が読み取れない
        (削除・非active・embedding NULL等)場合はno-op(構造化ログ)。
        起点のversionガードは選択SQLの「行の起点version == 現在version」
        条件で実現される(handleにversion引数はなく・旧世代行は選ばれない)。
        """
        loaded, rows, origin_row = await self._phase1(intent_id)
        if loaded.skip_reason is not None or loaded.origin is None:
            logger.info(
                "jev origin no-op intent_id=%s reason=%s",
                intent_id,
                loaded.skip_reason,
            )
            return
        if origin_row is None:
            logger.info("jev origin input missing intent_id=%s", intent_id)
            return
        org = loaded.origin
        origin_inp = _origin_input(org, origin_row)
        # 選択はSELECT時点でK_j件に確定(tx外で配分純関数を適用)
        for row in layer4.select_jev_targets(rows):
            await self._evaluate(org, origin_inp, row)

    # -- フェーズ1(短tx・読取+close)--

    async def _phase1(
        self, intent_id: uuid.UUID
    ) -> tuple[OriginLoad, list[JevCandidateRow], tuple | None]:
        async with self._engine.begin() as conn:
            loaded = await origin_mod.load_origin(conn, self._clock, intent_id)
            if loaded.skip_reason is not None or loaded.origin is None:
                return loaded, [], None
            org = loaded.origin
            closed = await layer4.close_broken_pairs(conn, org, org.evaluated_at)
            if closed:
                logger.info(
                    "jev closed broken pairs intent_id=%s count=%d",
                    intent_id,
                    closed,
                )
            origin_row = await _fetch_jev_input(conn, intent_id)
            day_start = jst_day_start(self._clock.jst_date())
            month_start = jst_month_start(self._clock.jst_date())
            rows = await layer4.select_jev_rows(
                conn, org.intent_id, org.version, day_start, month_start
            )
            return loaded, rows, origin_row

    # -- フェーズ2+3(ペア毎・直列)--

    async def _evaluate(
        self, org: Origin, origin_inp: JevTextInput, row: JevCandidateRow
    ) -> None:
        """1ペアの実行(§9-9の手順a〜i)。例外は分岐表(design §2.9)どおり。"""
        origin_is_a = row.intent_a_id == org.intent_id
        peer_id = row.intent_b_id if origin_is_a else row.intent_a_id
        expected_version = row.intent_b_version if origin_is_a else row.intent_a_version
        peer_row = await _read_peer(self._engine, peer_id)
        if peer_row is None:
            logger.info("jev peer missing row_id=%s peer_id=%s", row.row_id, peer_id)
            return
        if peer_row[0] != expected_version:
            # 不一致なら何もせず次の行へ(新世代行が別Eventで作られる。
            # 旧世代行はjev_result保持のまま)
            logger.info(
                "jev peer version mismatch row_id=%s peer_id=%s", row.row_id, peer_id
            )
            return
        async with self._engine.begin() as conn:  # H再検証(読取のみ)
            holds = await layer4.hard_constraint_holds(conn, org, peer_id)
        if not holds:
            # 評価せず次の行へ(pendingのまま・Guardも呼ばない — 実行しない
            # ものには課税しない)
            logger.info("jev hard constraint failed row_id=%s", row.row_id)
            return
        decision = await self._guard.request_execution(org.intent_id, org.user_id)
        if not decision.allowed:
            # deny: INCR先行(消費済み)・record_executionは呼ばない。
            # 補充しない — 選択はSELECT時点で確定しており、denyが出ても
            # 順位を繰り上げて枠外の行を追加しない(残り行はそのまま消化)
            logger.info(
                "jev guard denied row_id=%s reason=%s",
                row.row_id,
                decision.deny_reason,
            )
            await _skip_row(
                self._engine, row.row_id, decision.deny_reason, self._clock.now()
            )
            return
        peer_inp = _jev_input_from_row(peer_row)
        inp_a, inp_b = (origin_inp, peer_inp) if origin_is_a else (peer_inp, origin_inp)
        intent_a = build_jev_text(inp_a, label="Intent A")
        intent_b = build_jev_text(inp_b, label="Intent B")
        try:
            judgment = await self._gateway.judge_pair(
                intent_a=intent_a,
                intent_b=intent_b,
                intent_ids=[str(row.intent_a_id), str(row.intent_b_id)],
            )
        except JevOutputInvalidError as exc:
            # 検証失敗は例外にせずskipped記録へ(実装不整合)
            await self._record_and_skip(row, exc.provider, _SKIP_REASON_INVALID_OUTPUT)
            return
        except LLMError:
            # 双障害(第一候補の切替条件4種→フォールバック失敗)もskipped記録へ
            await self._record_and_skip(row, "fallback_llm", _SKIP_REASON_LLM_FAILURE)
            return
        jev_result = {
            **judgment.result,
            "provider": judgment.provider,
            "model": judgment.model,
        }
        await _complete_row(self._engine, row.row_id, jev_result, self._clock.now())
        # 計上はUPDATE成功後に1回・最終経路のみ(§2.5-3)。競合負けでも
        # API呼び出しは発生している(at-least-once受容)
        await self._record(judgment.provider)

    async def _record_and_skip(
        self, row: JevCandidateRow, provider: str | None, reason: str
    ) -> None:
        """LLM失敗・検証失敗の計上+skipped記録(§9-9 g/h)。

        計上は実際に最後に呼んだ経路(検証失敗=exc.provider・双障害=
        fallback_llm)。UPDATEが競合負けしても計上は行う。
        """
        await self._record(provider or "fallback_llm")
        await _skip_row(self._engine, row.row_id, reason, self._clock.now())

    async def _record(self, provider: str) -> None:
        day = self._clock.jst_date().strftime("%Y%m%d")
        await self._cost_store.record_execution(provider, day)
