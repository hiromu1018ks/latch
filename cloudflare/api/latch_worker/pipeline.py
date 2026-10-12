"""マッチングパイプラインのWorkers組立(非同期処理・2026-10-10)。

意図保存(outbox)→Queues→評価(T4検索+cheap judge+jev)→提案
(atomic.apply_proposal・D-08上限・通知job同時保存)→通知配送→回答成立、
までの非同期一連をWorkers上で動かす。LLMはLATCH_LLM_MODEで切替
(stub=決定的スタブ〔ローカル試験経路はこちら固定〕・real=fetch直書きの
実プロバイダ〔llm_real.py・実配備のみsecretで選択〕・2026-10-12)。

構成(計画§4配送・§5設計規律・実配備probe差異5件へ対応済み):
- dispatch: apply_intentが書いたoutbox(pending)をQueuesへ。API経路は
  post-commitベストエフォート(entry側QueueDispatcher)・cronがpending掃き
  (at-least-once。envelopeはIDのみで原文・条件本文を含めない)
- queue consumer: envelopeを受けて評価を実行。再配送の重複実行は
  match_candidates UNIQUE(a,b,va,vb)・evaluation_claims(I1)・open latch
  UNIQUEの3重で自然冪等(追加LLM呼出しもclaimが防ぐ)
- cron(*/5): ScheduledEvaluator(due回収→評価→完了確定)・jobs lease回収・
  outbox掃き・notify/retention job実行・run_retention
- 評価の中身: ensure embedding(stub/real)→D1RetrievalRepository(T4)→
  layer3 cheap judge→match_candidates→EvaluationJobRunner(jev・試行記録)→
  latch_score→閾値超えをapply_proposal(D-08・通知job込み)
"""

from __future__ import annotations

import json
import uuid as uuid_mod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from latch.adapters.d1.atomic import D1AtomicRepository
from latch.adapters.d1.database import LocalD1Database, canonical_json
from latch.adapters.d1.evaluations import D1EvaluationGateway
from latch.adapters.d1.jobs import D1JobCoordinator
from latch.adapters.d1.notification_jobs import D1NotificationDelivery, PushSenderStub
from latch.adapters.d1.retrieval import D1RetrievalRepository
from latch.adapters.d1.schedule import D1ScheduleRepository
from latch.domain.time import from_micros, micros
from latch.domain.types import EvaluationKey, IntentSnapshot
from latch.llm.jev import (
    JEV_MODEL,
    JevJudgment,
    JevTextInput,
    build_jev_text,
    validate_and_normalize,
)
from latch.llm.stub import StubLLM
from latch.runtime.cloudflare.jobs import EvaluationJobRunner, SendResult
from latch.runtime.cloudflare.retention import run_retention
from latch.runtime.cloudflare.scheduled import ScheduledEvaluator
from latch.worker.matching import layer3, latch_calc
from latch.worker.matching.proposal import LatchIntentInputs, build_proposal

from latch_worker.gjson import loads_json

K_J = 8  # 1評価あたりのJev実行上限(06 §8 D-24・layer4.K_Jと同一)
PRICE_VERSION = "stub-1"  # stub構成のprice_version(realはllm_real.PRICE_VERSION_REAL)

# stub JEV応答(決定的・実LLMと無関係)。E2Eで提案が成立するよう両方向の
# 受諾確率を0.9に固定する(score=1.0×min(0.9,0.9)=0.9≥閾値0.60・high)。
# stubはコンストラクタで応答差替え可能な試験用プロバイダ(10 §1)。stub
# 経路の決定性は変わらない(realはllm_real.pyのfetch直書き実プロバイダ)。
STUB_JEV_RESPONSE: dict = {
    "model": JEV_MODEL,
    "answers": {
        "would_a_accept_b": {"type": "noul", "noul": 0.9},
        "would_b_accept_a": {"type": "noul", "noul": 0.9},
        "latent_yes": {"type": "noul", "noul": 0.8},
        "purpose_fit": {"type": "score", "score": 4.0, "confidence": 0.8},
        "mood_fit": {"type": "score", "score": 4.0, "confidence": 0.8},
        "timing_fit": {"type": "score", "score": 4.0, "confidence": 0.8},
        "social_fit": {"type": "score", "score": 4.0, "confidence": 0.8},
    },
    "usage": {"input_tokens": 0, "output_tokens": 0},
}

_ORIGIN_COLS = (
    "id, version, user_id, status, category_primary, structured_data, embedding,"
    " alcohol_involved, geo_lon, geo_lat, geo_radius_m, budget_max,"
    " participants_min, participants_max, visibility, notification_level,"
    " time_start, time_end, expires_at"
)


def _new_id() -> str:
    return str(uuid_mod.uuid4())


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


@dataclass
class PipelineServices:
    """パイプライン実行に必要なservice一式(すべてstateless・D1が状態の正)。"""

    db: LocalD1Database
    atomic: D1AtomicRepository
    retrieval: D1RetrievalRepository
    schedule: D1ScheduleRepository
    coordinator: D1JobCoordinator
    gateway: D1EvaluationGateway
    delivery: D1NotificationDelivery
    runner: EvaluationJobRunner
    llm: Any  # StubPipelineLLM | RealPipelineLLM(統一IF: embed/judge → JevJudgment)
    clock: Any
    geo: Any = None
    ab: Any = None  # AbService | None(05 v0.8・未注入=実験なしと同一挙動)

    def now(self) -> datetime:
        return self.clock.now()


class StubPipelineLLM:
    """stub実装(従来挙動そのまま・llm_attemptsのembedding記録はなし)。

    judgeはstub envelopeをJevJudgment(provider="stub")へ正規化して返す
    — real(llm_real.RealPipelineLLM)と同じIFで、_evaluate_pairの記録値
    (fingerprint・actual_provider・cost 0)が従来と一字変わらないことを
    単体試験が固定する。
    """

    mode = "stub"

    def __init__(self, stub: StubLLM) -> None:
        self._stub = stub

    async def embed(self, text: str, *, intent_id: str) -> list[float]:
        return await self._stub.embed(text)

    async def judge(
        self, intent_a: str, intent_b: str, *, intent_ids: list[str]
    ) -> JevJudgment:
        raw = await self._stub.judge(intent_a, intent_b)
        return JevJudgment(
            provider="stub",
            model=str(raw.get("model") or JEV_MODEL),
            result=validate_and_normalize(raw),
            usage=raw.get("usage") if isinstance(raw, dict) else None,
        )


def build_pipeline(env) -> PipelineServices:
    """env bindings(DB・JOBS)からpipeline service一式を構築する。

    LATCH_LLM_MODE(実配備ではsecretがvarsを上書き)がrealのときのみ
    fetch直書きの実プロバイダを構築する。鍵欠落はfail-fast
    (build_real_gateway・静かにstubへ落ちない)。
    """
    from latch.adapters.d1.evaluations import D1EvaluationGateway as GW
    from latch.core.clock import SystemClock
    from latch_worker.d1binding import RealD1Binding
    from latch_worker.geocode_d1 import D1GeoService
    from latch_worker.svc_ab import build_ab_service

    db = RealD1Binding(env.DB)
    clock = SystemClock()
    gateway = GW(db)
    mode = str(getattr(env, "LATCH_LLM_MODE", "stub") or "stub")
    if mode == "real":
        from latch_worker.llm_real import build_real_pipeline_llm

        llm = build_real_pipeline_llm(env, clock, db)
    else:
        llm = StubPipelineLLM(StubLLM(jev_response=STUB_JEV_RESPONSE))
    return PipelineServices(
        db=db,
        atomic=D1AtomicRepository(db),
        retrieval=D1RetrievalRepository(db),
        schedule=D1ScheduleRepository(db),
        coordinator=D1JobCoordinator(db),
        gateway=gateway,
        delivery=D1NotificationDelivery(db, push=PushSenderStub()),
        runner=EvaluationJobRunner(gateway, coordinator=D1JobCoordinator(db)),
        llm=llm,
        clock=clock,
        geo=D1GeoService(db),
        ab=build_ab_service(db, clock),
    )


# ======================================================================
# outbox → Queues 配送(§4: envelopeはIDのみ・at-least-once)
# ======================================================================


def _envelope(row: dict) -> dict:
    return {
        "event_id": str(row["event_id"]),
        "intent_id": None if row.get("intent_id") is None else str(row["intent_id"]),
        "intent_version": _int(row.get("intent_version")),
        "reason": str(row["reason"]),
        "due_generation": _int(row.get("due_generation")) or 1,
        "writer_generation": _int(row.get("writer_generation")) or 1,
        "payload_schema_version": _int(row.get("payload_schema_version")) or 1,
    }


async def send_envelope(queue_producer, envelope: dict) -> None:
    """QueuesへはJSON文字列で送る(§4規律・原文・条件本文なし)。"""
    await queue_producer.send(json.dumps(envelope, ensure_ascii=False))


async def dispatch_outbox(
    services: PipelineServices, queue_producer, *, now: datetime, limit: int = 50
) -> dict:
    """pending outbox→Queues掃き(cron)。送信成功後に'sent'化。

    送信失敗・送信後の印付け失敗は行をpendingのまま残す→次周期が再送
    (at-least-once。重複実行は評価冪等性が吸収)。
    """
    rows = (
        await services.db.prepare(
            "SELECT id, event_id, intent_id, intent_version, reason,"
            " due_generation, writer_generation, payload_schema_version"
            " FROM outbox WHERE status = 'pending'"
            " ORDER BY created_at ASC LIMIT ?"
        )
        .bind(limit)
        .all()
    )
    sent = 0
    failed = 0
    for row in rows:
        try:
            await send_envelope(queue_producer, _envelope(row))
        except Exception:  # noqa: BLE001 — 次周期で再送
            failed += 1
            continue
        (
            await services.db.prepare(
                "UPDATE outbox SET status = 'sent', sent_at = ?"
                " WHERE id = ? AND status = 'pending'"
            )
            .bind(micros(now), row["id"])
            .run()
        )
        sent += 1
    return {"pending": len(rows), "sent": sent, "send_failed": failed}


class QueueDispatcher:
    """API経路のpost-commitベストエフォート配送。

    apply_intent確定後に呼ぶ。送信・印付けの失敗は握りつぶし、cronの
    pending掃きに任せる(API応答を配送結果から分離 — §4)。
    """

    def __init__(self, db, jobs, clock) -> None:
        self._db = db
        self._jobs = jobs
        self._clock = clock

    async def dispatch_event(
        self,
        *,
        event_id: str,
        intent_id: str,
        intent_version: int,
        reason: str,
    ) -> None:
        if self._jobs is None:
            return
        try:
            await send_envelope(
                self._jobs,
                {
                    "event_id": event_id,
                    "intent_id": intent_id,
                    "intent_version": intent_version,
                    "reason": reason,
                    "due_generation": 1,
                    "writer_generation": 1,
                    "payload_schema_version": 1,
                },
            )
            (
                await self._db.prepare(
                    "UPDATE outbox SET status = 'sent', sent_at = ?"
                    " WHERE event_id = ? AND status = 'pending'"
                )
                .bind(micros(self._clock.now()), event_id)
                .run()
            )
        except Exception:  # noqa: BLE001 — cron掃きが再送
            pass


# ======================================================================
# 評価(embedding→T4検索→cheap judge→stub jev→提案)
# ======================================================================


async def _ensure_embedding(services: PipelineServices, origin: dict, *, now) -> bool:
    """起源Intentのembeddingを生成・保存(embedding worker相当)。

    stub=決定的スタブ・real=実Gemini(llm_real・embedding試行はllm_attemptsへ
    記録)。raw_textは経路に含めない(確定値#10・07 §3)。既存あれば何もしない。
    """
    if origin["embedding"]:
        return False
    from latch.worker.embedding_text import EmbeddingTextInput, build_embedding_text

    text = build_embedding_text(
        EmbeddingTextInput(
            category_primary=origin["category_primary"] or "",
            structured_data=loads_json(origin["structured_data"], {}),
            participants_min=_int(origin["participants_min"]),
            participants_max=_int(origin["participants_max"]),
            time_start=(
                from_micros(origin["time_start"]) if origin["time_start"] else None
            ),
            time_end=from_micros(origin["time_end"]) if origin["time_end"] else None,
        )
    )
    vector = await services.llm.embed(text, intent_id=str(origin["id"]))
    (
        await services.db.prepare(
            "UPDATE intents SET embedding = ?, updated_at = ?"
            " WHERE id = ? AND embedding IS NULL"
        )
        .bind(json.dumps(vector), micros(now), origin["id"])
        .run()
    )
    return True


def _origin_snapshot(origin: dict) -> IntentSnapshot:
    sd = loads_json(origin["structured_data"], {})
    category = sd.get("category") if isinstance(sd.get("category"), dict) else {}
    neg = tuple(
        item.get("text", "")
        for item in (sd.get("negative_constraints") or [])
        if isinstance(item, dict) and item.get("text")
    )
    ng = tuple(
        item.get("text", "")
        for item in (sd.get("ng_unverifiable") or [])
        if isinstance(item, dict) and item.get("text")
    )
    return IntentSnapshot(
        intent_id=str(origin["id"]),
        version=int(origin["version"]),
        user_id=str(origin["user_id"]),
        status=str(origin["status"]),
        category_primary=origin["category_primary"] or "",
        category_secondary=category.get("secondary"),
        alcohol_involved=bool(origin.get("alcohol_involved")),
        time_start=from_micros(origin["time_start"]),
        time_end=from_micros(origin["time_end"]) if origin["time_end"] else None,
        flexibility_minutes=None,
        geo_lon=float(origin["geo_lon"]) if origin["geo_lon"] is not None else None,
        geo_lat=float(origin["geo_lat"]) if origin["geo_lat"] is not None else None,
        radius_m=_int(origin["geo_radius_m"]),
        budget_max=_int(origin["budget_max"]),
        participants_min=_int(origin["participants_min"]),
        participants_max=_int(origin["participants_max"]),
        soft_constraints=_soft(origin["structured_data"]),
        negative_constraints=neg,
        ng_unverifiable=ng,
    )


def _soft(structured_data: Any) -> tuple[str, ...]:
    return layer3.soft_texts(structured_data)


async def evaluate_intent(
    services: PipelineServices, intent_id: str, *, now: datetime, owner: str = "worker"
) -> dict:
    """Intent 1件の評価(T4検索→cheap judge→stub jev→閾値超え提案)。

    冪等性: 候補行はUNIQUE(a,b,va,vb)・Jevはevaluation_claims+jev未評価行
    選択・提案はopen latch UNIQUE+D-08 — どの回で再実行しても同じ状態に
    収束する(再配送・cron重複ともに安全)。
    """
    origin = (
        await services.db.prepare(f"SELECT {_ORIGIN_COLS} FROM intents WHERE id = ?")
        .bind(str(intent_id))
        .first()
    )
    if origin is None or origin["status"] != "active":
        # 非活性(paused/matched/expired/cancelled)は評価機会から外す
        if origin is not None:
            try:
                await services.schedule.mark_paused(str(origin["id"]), True)
            except Exception:  # noqa: BLE001 — 冪等(行が無ければ清算不要)
                pass
        return {"intent_id": str(intent_id), "skipped": "inactive"}
    if not origin["embedding"] and (
        origin["geo_lon"] is None or origin["geo_lat"] is None or not origin["time_start"]
    ):
        return {"intent_id": str(intent_id), "skipped": "not_retrievable"}

    embedded = await _ensure_embedding(services, origin, now=now)
    snapshot_id = await services.retrieval.create_retrieval_snapshot(
        str(origin["id"]), int(origin["version"]), now
    )
    snapshot = _origin_snapshot(origin)
    result = await services.retrieval.retrieve_exact(snapshot, snapshot_id)

    # cheap judge(06 §4: 0.5/0.3/0.2 — layer3純関数をそのまま再利用)
    origin_soft = _soft(origin["structured_data"])
    scored: list[layer3.ScoredCandidate] = []
    for c in result.candidates:
        rule = layer3.rule_score(
            origin_time_start=snapshot.time_start,
            cand_time_start=c.time_start,
            origin_budget=snapshot.budget_max,
            cand_budget=c.budget_max,
        )
        vocab = layer3.vocab_overlap(origin_soft, c.soft_texts)
        scored.append(
            layer3.ScoredCandidate(
                intent_id=uuid_mod.UUID(c.intent_id),
                version=c.version,
                similarity=c.similarity,
                cheap_score=layer3.cheap_score(c.similarity, rule, vocab),
            )
        )
    top = layer3.select_top_kc(scored)

    stmts = []
    for s in top:
        a, b = sorted((str(origin["id"]), str(s.intent_id)))
        a_ver = int(origin["version"]) if a == origin["id"] else s.version
        b_ver = s.version if a == origin["id"] else int(origin["version"])
        stmts.append(
            services.db.prepare(
                "INSERT OR IGNORE INTO match_candidates"
                " (id, intent_a_id, intent_b_id, intent_a_version,"
                "  intent_b_version, retrieval_score, cheap_judge_score,"
                "  status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)"
            ).bind(
                _new_id(),
                a,
                b,
                a_ver,
                b_ver,
                s.similarity,
                s.cheap_score,
                micros(now),
                micros(now),
            )
        )
    if stmts:
        await services.db.batch(stmts)

    targets = (
        await services.db.prepare(
            "SELECT id, intent_a_id, intent_b_id, intent_a_version,"
            " intent_b_version, cheap_judge_score FROM match_candidates"
            " WHERE (intent_a_id = ? OR intent_b_id = ?)"
            " AND (CASE WHEN intent_a_id = ? THEN intent_a_version"
            "           ELSE intent_b_version END) = ?"
            " AND status = 'pending' AND jev_result IS NULL"
            " ORDER BY cheap_judge_score DESC,"
            " (CASE WHEN intent_a_id = ? THEN intent_b_id"
            "       ELSE intent_a_id END) ASC"
            " LIMIT ?"
        )
        .bind(
            origin["id"], origin["id"], origin["id"], origin["version"],
            origin["id"], K_J,
        )
        .all()
    )
    jev_called = 0
    for target in targets:
        r = await _evaluate_pair(services, origin, target, now=now, owner=owner)
        jev_called += int(r.get("called") or 0)

    proposals = await _promote_scored_pairs(services, origin, now=now)
    return {
        "intent_id": str(origin["id"]),
        "version": int(origin["version"]),
        "embedded": embedded,
        "pass_count": result.pass_count,
        "candidates": len(result.candidates),
        "jev_called": jev_called,
        "proposals": proposals,
    }


def _jev_text_input(row: dict) -> JevTextInput:
    return JevTextInput(
        category_primary=row["category_primary"] or "",
        structured_data=loads_json(row["structured_data"], {}),
        participants_min=_int(row["participants_min"]),
        participants_max=_int(row["participants_max"]),
        time_start=from_micros(row["time_start"]) if row["time_start"] else None,
        time_end=from_micros(row["time_end"]) if row["time_end"] else None,
        budget_max=_int(row["budget_max"]),
        geo_radius_m=_int(row["geo_radius_m"]),
    )


async def _evaluate_pair(
    services: PipelineServices, origin: dict, target: dict, *, now, owner: str
) -> dict:
    """1ペアのJev評価(EvaluationJobRunner: claim→prepare→sender→finish→complete)。"""
    peer_id = (
        target["intent_b_id"]
        if target["intent_a_id"] == origin["id"]
        else target["intent_a_id"]
    )
    peer = (
        await services.db.prepare(f"SELECT {_ORIGIN_COLS} FROM intents WHERE id = ?")
        .bind(peer_id)
        .first()
    )
    if peer is None or peer["status"] != "active":
        return {"peer": str(peer_id), "skipped": "peer_inactive", "called": 0}

    a_id, b_id = sorted((str(origin["id"]), str(peer_id)))
    a_row = origin if origin["id"] == a_id else peer
    b_row = peer if origin["id"] == a_id else origin
    text_a = build_jev_text(_jev_text_input(a_row), label="A")
    text_b = build_jev_text(_jev_text_input(b_row), label="B")
    va = int(target["intent_a_version"])
    vb = int(target["intent_b_version"])
    real_mode = getattr(services.llm, "mode", "stub") == "real"
    # fingerprint: 実送信入力+判定規則+provider/model版(§5.3)。stub構成の値は
    # 従来と同一(stub|jev-1.13.0|jev-v1)・realは構成区分で始まる(再評価冪等は
    # match_candidates.jev_result IS NULLガードが担う)
    fingerprint = (
        f"real|{JEV_MODEL}|jev-v1" if real_mode else f"stub|{JEV_MODEL}|jev-v1"
    )
    key = EvaluationKey(
        kind="jev",
        pair_key=f"jev:{a_id}:{b_id}:{va}:{vb}",
        fingerprint=fingerprint,
    )
    captured: dict = {}

    async def sender(_key: EvaluationKey) -> SendResult:
        judgment = await services.llm.judge(
            text_a, text_b, intent_ids=[a_id, b_id]
        )
        mutual = min(
            float(judgment.result["would_a_accept_b"]),
            float(judgment.result["would_b_accept_a"]),
        )
        score = latch_calc.LATCH_C * mutual
        usage = judgment.usage or {}
        captured["score"] = score
        captured["jev_result"] = {
            **judgment.result,
            "provider": judgment.provider,
            "model": judgment.model,
        }
        return SendResult(
            status="succeeded",
            actual_provider=judgment.provider,
            actual_model=judgment.model or JEV_MODEL,
            input_tokens=_int(usage.get("input_tokens")),
            output_tokens=_int(usage.get("output_tokens")),
            # stubは実費0円(確定)・realは請求確定前のためNULL(0円として計上しない)
            cost_confirmed_micros=None if judgment.provider != "stub" else 0,
            payload=captured,
        )

    out = await services.runner.run(
        key,
        owner=owner,
        sender=sender,
        requested_provider="typesafe" if real_mode else "stub",
        requested_model=JEV_MODEL,
        price_version="real-1" if real_mode else PRICE_VERSION,
        now=now,
    )
    if not out.get("completed") or "score" not in captured:
        return {"peer": str(peer_id), **out}
    res = (
        await services.db.prepare(
            "UPDATE match_candidates SET jev_result = ?, latch_score = ?,"
            " status = 'evaluated', updated_at = ?"
            " WHERE id = ? AND status = 'pending' AND latch_score IS NULL"
        )
        .bind(
            canonical_json(captured["jev_result"]),
            captured["score"],
            micros(now),
            target["id"],
        )
        .run()
    )
    return {
        "peer": str(peer_id),
        "called": out.get("called", 0),
        "score": captured["score"],
        "recorded": res.changes == 1,
    }


def _intent_inputs(row: dict) -> LatchIntentInputs:
    sd = loads_json(row["structured_data"], {})
    secondary = sd.get("category")
    if not isinstance(secondary, dict):
        secondary = {}
    return LatchIntentInputs(
        intent_id=uuid_mod.UUID(str(row["id"])),
        user_id=uuid_mod.UUID(str(row["user_id"])),
        visibility=row["visibility"] or "hidden_until_match",
        notification_level=row["notification_level"] or "proposals_only",
        time_start=from_micros(row["time_start"]),
        expires_at=from_micros(row["expires_at"]) if row["expires_at"] else None,
        budget_max=_int(row["budget_max"]),
        category_primary=row["category_primary"] or "",
        category_secondary=secondary.get("secondary"),
        geo_lon=float(row["geo_lon"]) if row["geo_lon"] is not None else 0.0,
        geo_lat=float(row["geo_lat"]) if row["geo_lat"] is not None else 0.0,
    )


async def _promote_scored_pairs(
    services: PipelineServices, origin: dict, *, now: datetime
) -> list[str]:
    """latch_score閾値超え行の提案(latch_engine.try_promote相当のD1版)。

    - 提案済み(open latchに両Intentを含む)行は対象外(再評価で再提案しない)
    - 75分ルール(06 §10)・D-07再提案抑制(06 §10)をlatch_calc純関数で確認
    - apply_proposalが版・D-08上限・open重複を原子確認して確定
    - A/B(05 v0.8): 抽出は対照・処置の小さい方の閾値で行い、行ごとに
      ペア参加者のvariantで再判定する(混在ペアは対照値・FR-25)
    """
    drain_threshold = (
        await services.ab.drain_threshold()
        if services.ab is not None
        else latch_calc.LATCH_THRESHOLD
    )
    rows = (
        await services.db.prepare(
            "SELECT id, intent_a_id, intent_b_id, intent_a_version,"
            " intent_b_version, latch_score FROM match_candidates"
            " WHERE (intent_a_id = ? OR intent_b_id = ?)"
            " AND (CASE WHEN intent_a_id = ? THEN intent_a_version"
            "           ELSE intent_b_version END) = ?"
            " AND status = 'evaluated' AND latch_score IS NOT NULL"
            " AND latch_score >= ?"
            " AND NOT EXISTS ("
            "   SELECT 1 FROM latches l WHERE l.status IN"
            "   ('candidate','proposed','partial_accept')"
            "   AND EXISTS (SELECT 1 FROM json_each(l.intent_ids) ja"
            "               WHERE ja.value = match_candidates.intent_a_id)"
            "   AND EXISTS (SELECT 1 FROM json_each(l.intent_ids) jb"
            "               WHERE jb.value = match_candidates.intent_b_id))"
            " ORDER BY latch_score DESC, intent_a_id ASC"
        )
        .bind(
            origin["id"], origin["id"], origin["id"], origin["version"],
            drain_threshold,
        )
        .all()
    )
    proposed: list[str] = []
    for row in rows:
        a_id, b_id = str(row["intent_a_id"]), str(row["intent_b_id"])
        a_row = (
            await services.db.prepare(
                "SELECT id, user_id, visibility, notification_level, time_start,"
                " time_end, expires_at, budget_max, category_primary,"
                " structured_data, geo_lon, geo_lat, status FROM intents"
                " WHERE id = ?"
            )
            .bind(a_id)
            .first()
        )
        b_row = (
            await services.db.prepare(
                "SELECT id, user_id, visibility, notification_level, time_start,"
                " time_end, expires_at, budget_max, category_primary,"
                " structured_data, geo_lon, geo_lat, status FROM intents"
                " WHERE id = ?"
            )
            .bind(b_id)
            .first()
        )
        if a_row is None or b_row is None:
            continue
        if a_row["status"] not in ("active", "paused") or b_row["status"] not in (
            "active",
            "paused",
        ):
            continue
        # A/B(05 v0.8 規定4・5): ペア参加者のvariantから適用値を解決
        pair_owners = [a_row["user_id"], b_row["user_id"]]
        if services.ab is not None:
            pair_threshold = await services.ab.pair_threshold(pair_owners)
            daily_limit = await services.ab.pair_daily_limit(pair_owners)
            concurrent_limit = await services.ab.pair_concurrent_limit(pair_owners)
        else:
            pair_threshold = latch_calc.LATCH_THRESHOLD
            daily_limit = None
            concurrent_limit = None
        if float(row["latch_score"]) < pair_threshold:
            continue  # 抽出はmin閾値・適用は行ごとのペア適用値
        if not a_row["time_start"] or not b_row["time_start"]:
            continue
        if not a_row["expires_at"] or not b_row["expires_at"]:
            continue
        inp_a = _intent_inputs(a_row)
        inp_b = _intent_inputs(b_row)
        score = float(row["latch_score"])
        target = latch_calc.pair_target_time(inp_a.time_start, inp_b.time_start)
        # 75分ルール: 通知時点から対象時刻まで75分無い提案は作らない
        if target - now < latch_calc.PROMPT_LEAD_MIN:
            continue
        min_expires = min(inp_a.expires_at, inp_b.expires_at)
        # D-07: 同一組み合わせ履歴(空でないresponses)からの再提案抑制
        hist = (
            await services.db.prepare(
                "SELECT responses FROM latches WHERE intent_ids = ?"
                " AND responses <> '[]'"
            )
            .bind(canonical_json([a_id, b_id]))
            .all()
        )
        merged: list[dict] = []
        for h in hist:
            merged.extend(loads_json(h["responses"], []))
        has_no, latest_defer_at = latch_calc.d07_history_inputs(merged)
        if not latch_calc.d07_allows(
            has_no_response=has_no,
            latest_defer_at=latest_defer_at,
            now=now,
            target_time=target,
            new_score=score,
            prev_latch_score=None,
        ):
            continue
        area_name = None
        if services.geo is not None:
            try:
                area_name = await services.geo.reverse_geocode(
                    (inp_a.geo_lon + inp_b.geo_lon) / 2.0,
                    (inp_a.geo_lat + inp_b.geo_lat) / 2.0,
                )
            except Exception:  # noqa: BLE001 — area名は提案の付加情報
                area_name = None
        deadline = latch_calc.response_deadline(now, target, min_expires)
        proposal = build_proposal(
            origin=inp_a, peer=inp_b, score=score, area_name=area_name
        )
        result = await services.atomic.apply_proposal(
            _new_id(),
            {a_id: int(row["intent_a_version"]), b_id: int(row["intent_b_version"])},
            {
                "intent_ids": [a_id, b_id],
                "score": score,
                "proposal": proposal,
                "response_deadline_us": micros(deadline),
                "expires_us": micros(min_expires),
            },
            now,
            daily_limit=daily_limit,
            concurrent_limit=concurrent_limit,
        )
        if result.status.value == "applied":
            proposed.append(str(result.response["latch"]["id"]))
    return proposed


# ======================================================================
# cron(*/5)と queue consumer
# ======================================================================


async def run_due_jobs(
    services: PipelineServices, *, now: datetime, owner: str, limit: int = 25
) -> dict:
    """due到達のjobs(notify/retention/evaluation)を実行・確定。"""
    claims = await services.coordinator.claim_due(now, owner=owner, limit=limit)
    out = {"claimed": len(claims), "notify": 0, "retention": 0, "evaluation": 0, "failed": 0}
    for claim in claims:
        try:
            if claim.kind == "notify":
                outcome = await services.delivery.deliver_notification(
                    {"job_id": claim.job_id, "kind": "notify", "ref_id": claim.ref_id},
                    now,
                )
                out["notify"] += 1 if (outcome.delivered or outcome.deduped) else 0
            elif claim.kind == "retention":
                await run_retention(services.db, now)
                out["retention"] += 1
            elif claim.kind == "evaluation":
                await evaluate_intent(
                    services, claim.ref_id or "", now=now, owner=owner
                )
                out["evaluation"] += 1
            await services.coordinator.complete(claim, now)
        except Exception:  # noqa: BLE001 — 再試行へ(回収はlease切れでも)
            out["failed"] += 1
            try:
                await services.coordinator.retry_later(
                    claim, delay_seconds=300, now=now
                )
            except Exception:  # noqa: BLE001
                pass
    return out


async def run_cron(
    services: PipelineServices, *, now: datetime, queue_producer=None
) -> dict:
    """cron(*/5)1周期: schedule評価→jobs回収→outbox掃き→due jobs→retention。

    各段は独立失敗(以降の段は実行)。評価例外はschedule claimをlease切れ
    回収へ残す(test_schedule_recoveryと同一規律)。
    """
    summary: dict[str, Any] = {"at": now.isoformat()}
    try:
        async def _evaluator(claim, t: datetime) -> dict:
            return await evaluate_intent(
                services, claim.intent_id, now=t, owner=f"cron:{claim.intent_id[:8]}"
            )

        states: dict[str, dict] = {}
        due_rows = (
            await services.db.prepare(
                "SELECT s.intent_id, i.time_start, i.expires_at"
                " FROM intent_evaluation_schedule s"
                " JOIN intents i ON i.id = s.intent_id"
                " WHERE s.status = 'due' AND s.next_evaluate_at <= ?"
            )
            .bind(micros(now))
            .all()
        )
        for r in due_rows:
            states[r["intent_id"]] = {
                "time_start": (
                    from_micros(r["time_start"]) if r["time_start"] else None
                ),
                "expires_at": (
                    from_micros(r["expires_at"]) if r["expires_at"] else None
                ),
            }
        scheduler = ScheduledEvaluator(services.schedule, _evaluator, batch_limit=50)
        result = await scheduler.run_once(now, intent_states=states)
        summary["schedule"] = {
            "claimed": result.claimed,
            "completed": result.completed,
            "superseded": result.superseded,
        }
    except Exception as exc:  # noqa: BLE001
        summary["schedule_error"] = f"{type(exc).__name__}: {exc}"
    try:
        summary["jobs_recovered"] = await services.coordinator.recover_expired_leases(now)
    except Exception as exc:  # noqa: BLE001
        summary["jobs_recovered_error"] = f"{type(exc).__name__}: {exc}"
    if queue_producer is not None:
        try:
            summary["outbox"] = await dispatch_outbox(
                services, queue_producer, now=now
            )
        except Exception as exc:  # noqa: BLE001
            summary["outbox_error"] = f"{type(exc).__name__}: {exc}"
    try:
        summary["jobs"] = await run_due_jobs(
            services, now=now, owner=f"cron:{now.strftime('%H%M')}"
        )
    except Exception as exc:  # noqa: BLE001
        summary["jobs_error"] = f"{type(exc).__name__}: {exc}"
    try:
        ret = await run_retention(services.db, now)
        summary["retention"] = {
            "candidates_deleted": ret.candidates_deleted,
            "cache_expired": ret.cache_expired,
            "snapshots_deleted": ret.snapshots_deleted,
        }
    except Exception as exc:  # noqa: BLE001
        summary["retention_error"] = f"{type(exc).__name__}: {exc}"
    return summary


async def handle_queue_message(
    services: PipelineServices, body: str, *, now: datetime | None = None
) -> dict:
    """Queues consumer 1通分の処理(envelopeはIDのみ・原文なし)。

    created/updated → 評価実行。paused/deleted・未知reasonは無視(ack)。
    評価の冪等性はevaluate_intent参照(重複配送で状態が壊れない)。
    """
    envelope = json.loads(body)
    reason = str(envelope.get("reason") or "")
    intent_id = envelope.get("intent_id")
    if intent_id is None or reason not in ("created", "updated"):
        return {"ignored": reason or "(empty)"}
    return await evaluate_intent(
        services, str(intent_id), now=now or services.now(), owner="queue"
    )
