"""Worker本体(design §3.1)。ci環境では api と同じイメージ・別プロセス(Worker 1)。

M2 ws-1: Pub/Sub subscriptionをストリーミングpullで消費し、第1段
(Stage1)+更新Eventのdebounce(TrailingDebouncer)へ配線する。ackは
DBのstatus遷移コミット後(design §2.3)。bus/engine/stage1/debouncerは
注入可能(unit試験)、未注入分はrun()で構築する。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

import redis.asyncio as redis_async

from latch.core.clock import Clock, SystemClock
from latch.core.db import create_db_engine
from latch.events import EventBus, IncomingEvent, make_event_bus
from latch.geo.service import GeoService
from latch.intents.events import EVENT_CREATED, EVENT_EMBEDDING_COMPLETED, EVENT_UPDATED
from latch.llm.gateway import build_worker_gateway
from latch.notifications.sender import build_push_sender
from latch.settings import Settings
from latch.worker.backfill import BackfillRunner
from latch.worker.cost import JevCostGuard, JevCostStore, ReevalGuard
from latch.worker.debounce import DebounceEntry, DebounceGroup, TrailingDebouncer
from latch.worker.embedding import EmbeddingWorker
from latch.worker.jev import JevWorker
from latch.worker.matching import run_candidate_retrieval
from latch.worker.matching.group_engine import GroupEngine
from latch.worker.matching.latch_engine import LatchEngine
from latch.worker.reeval import ReevalRunner
from latch.worker.reset import ResetJob
from latch.worker.retention import RetentionJob
from latch.worker.stage1 import Stage1
from latch.worker.sweeper import ExpirySweeper

logger = logging.getLogger(__name__)

DEBOUNCE_TICK_SEC = 0.05  # 窓判定のtick(実時間の待機間隔。判定はClock基準)


class Worker:
    """継続実行の土台。Clock は起動時の1回の明示的構築(design §2.4 DI方式)。"""

    def __init__(
        self,
        clock: Clock | None = None,
        settings: Settings | None = None,
        *,
        bus: EventBus | None = None,
        engine=None,
        stage1: Stage1 | None = None,
        debouncer: TrailingDebouncer | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        embedding: EmbeddingWorker | None = None,
        backfill: BackfillRunner | None = None,
        reeval: ReevalGuard | None = None,
        jev: JevWorker | None = None,
        latch: LatchEngine | None = None,
        reeval_runner: ReevalRunner | None = None,
        group: GroupEngine | None = None,
    ) -> None:
        self.clock: Clock = clock if clock is not None else SystemClock()
        self.settings: Settings = settings if settings is not None else Settings()
        self._bus = bus
        self._engine = engine
        self._stage1 = stage1
        self._debouncer = debouncer
        self._sleep = sleep
        self._embedding = embedding
        self._backfill = backfill
        self._reeval = reeval
        self._jev = jev
        self._latch = latch
        self._reeval_runner = reeval_runner
        self._group = group
        self._stop = asyncio.Event()
        self._subscription = None

    def request_shutdown(self) -> None:
        self._stop.set()

    @property
    def bus(self) -> EventBus:
        """注入/構築済みのbus(テスト・配線確認用)。"""
        assert self._bus is not None, "bus未構築(run()未実施・未注入)"
        return self._bus

    async def run(self) -> None:
        owns_bus = self._bus is None
        owns_engine = self._engine is None
        owns_redis = self._reeval is None
        if owns_bus:
            self._bus = make_event_bus(self.settings)
        bus = self._bus
        engine = (
            self._engine
            if self._engine is not None
            else create_db_engine(self.settings)
        )
        redis_client = (
            redis_async.Redis.from_url(self.settings.redis_url, decode_responses=True)
            if owns_redis
            else None
        )
        if self._reeval is None and redis_client is not None:
            self._reeval = ReevalGuard(redis_client)
        try:
            stage1 = (
                self._stage1
                if self._stage1 is not None
                else Stage1(
                    engine=engine,
                    clock=self.clock,
                    settings=self.settings,
                    sleep=self._sleep,
                    matching_hook=self._run_matching,
                )
            )
            debouncer = (
                self._debouncer
                if self._debouncer is not None
                else TrailingDebouncer(
                    clock=self.clock,
                    window_sec=self.settings.event_debounce_window_sec,
                    tick_sec=DEBOUNCE_TICK_SEC,
                    on_release=self._on_release,
                )
            )
            self._stage1 = stage1
            self._debouncer = debouncer
            # GatewayはembeddingとJevWorkerで同一インスタンスを共有(§9-14)
            gateway = build_worker_gateway(self.clock, self.settings)
            embedding = (
                self._embedding
                if self._embedding is not None
                else EmbeddingWorker(
                    engine=engine,
                    clock=self.clock,
                    gateway=gateway,
                    bus=bus,
                )
            )
            backfill = (
                self._backfill
                if self._backfill is not None
                else BackfillRunner(
                    engine=engine,
                    embedding=embedding,
                    interval_sec=self.settings.embedding_backfill_interval_sec,
                    batch_limit=self.settings.embedding_backfill_batch_limit,
                )
            )
            self._embedding = embedding
            self._backfill = backfill
            # JevWorker DI(M2 ws-5・§9-14): embeddingと同一Gatewayインスタンス
            # (build_worker_gatewayは1本化済み)・guard/cost_storeはreevalと
            # 同一redis接続から構築。注入済み(ws-1資産の試験・integration)は
            # 再構築しない。redis不使用の構成では構築しない
            if self._jev is None and redis_client is not None:
                cost_store = JevCostStore(redis_client)
                self._jev = JevWorker(
                    engine=engine,
                    clock=self.clock,
                    gateway=gateway,
                    guard=JevCostGuard(store=cost_store, clock=self.clock),
                    cost_store=cost_store,
                )
            # PushSender(M3 ws-3・design §2.1案A): StubPushSender(ドライラン)。
            # LatchEngineとExpirySweeperの両方へ注入(notifications書き込みtxの
            # コミット直後に送信・失敗はsender側で握る)。real(実FCM)はG3後
            push = build_push_sender(self.clock, self.settings)
            # LatchEngine DI(M2 ws-6・design §2.1案A): JevWorker直後のLayer 5。
            # redis非依存のため常に構築(注入済み資産は再構築しない)
            if self._latch is None:
                self._latch = LatchEngine(
                    engine=engine,
                    clock=self.clock,
                    geo=GeoService(engine),
                    push=push,
                )
            # GroupEngine DI(M2 ws-7・design §2.1案A): LatchEngine直後の
            # グループ生成・集約。latchはtry_promote委譲用(Noneなら提案化なし)
            if self._group is None:
                self._group = GroupEngine(
                    engine=engine,
                    clock=self.clock,
                    geo=GeoService(engine),
                    latch=self._latch,
                )
            # ReevalRunner DI(design §2.8): catch-up・Bucket再評価の周期task。
            # pipelineはengineを閉包した直接投入(_run_direct_pipeline)。
            # M3 ws-2(design §2.1案A): ExpirySweeperを注入し60秒tickを1本化
            # (latches・Intent期限切れ・catch-upの切替・停止は単一ジョブ)
            if self._reeval_runner is None and redis_client is not None:

                async def _pipeline(intent_id: uuid.UUID) -> None:
                    await self._run_direct_pipeline(engine, intent_id)

                sweeper = ExpirySweeper(
                    engine=engine,
                    clock=self.clock,
                    latch=self._latch,
                    batch_limit=self.settings.sweeper_batch_limit,
                    push=push,
                )
                self._reeval_runner = ReevalRunner(
                    engine=engine,
                    clock=self.clock,
                    guard=self._reeval,  # この時点でrun()内構築済み(ReevalGuard)
                    pipeline=_pipeline,
                    interval_sec=self.settings.reeval_runner_interval_sec,
                    batch_limit=self.settings.reeval_runner_batch_limit,
                    sweeper=sweeper,
                )
            reeval_runner = self._reeval_runner
            # ResetJob DI(M3-4・design §2.5): 次JST 0時の掃除+drain。60秒系
            # とは別周期の独立task。cost_storeはJevWorker用と別インスタンス
            # (掃除専用・redis接続は共用)。sleepは渡さない(asyncio.sleep=本番待機)
            reset_job = None
            if redis_client is not None:
                reset_job = ResetJob(
                    cost_store=JevCostStore(redis_client),
                    clock=self.clock,
                    latch=self._latch,
                    retry_sec=self.settings.reset_retry_sec,
                )
            # RetentionJob DI(M3 ws-6・design §2.5案A): 30日定期削除。
            # engineのみで動く(redis不要)のためResetJobと違い無条件構築
            retention_job = RetentionJob(engine=engine, clock=self.clock)
            await bus.ensure()
            self._subscription = await bus.subscribe(self._dispatch)
            debouncer_task = asyncio.create_task(debouncer.run(stop=self._stop))
            backfill_task = asyncio.create_task(backfill.run(stop=self._stop))
            reeval_task = None
            if reeval_runner is not None:
                reeval_task = asyncio.create_task(reeval_runner.run(stop=self._stop))
            reset_task = None
            if reset_job is not None:
                reset_task = asyncio.create_task(reset_job.run(stop=self._stop))
            retention_task = asyncio.create_task(retention_job.run(stop=self._stop))
            logger.info("worker started (app_env=%s)", self.settings.app_env)
            await self._stop.wait()
            # graceful shutdown: 窓内entryは解放せず未ack再配信へ(design §2.3)
            if self._subscription is not None:
                self._subscription.stop()
            await debouncer_task
            await backfill_task
            if reeval_runner is not None:
                await reeval_task
            if reset_job is not None:
                await reset_task
            await retention_task
            logger.info("worker stopped")
        finally:
            if owns_engine and engine is not None:
                await engine.dispose()
            if owns_redis and redis_client is not None:
                await redis_client.aclose()
            if owns_bus:
                await bus.close()

    # -- 配線(bus.callback→ここ。asyncioループ内で実行される)--

    async def _dispatch(self, event: IncomingEvent) -> None:
        try:
            result = await self._stage1.intake(event)
            if result.kind in ("processed", "duplicate", "quarantined"):
                if (
                    result.kind in ("processed", "duplicate")
                    and result.triple is not None
                ):
                    await self._kick_embedding(*result.triple)
                    await self._kick_jev(*result.triple)
                event.ack()
                return
            if result.kind == "debounce":
                assert result.triple is not None and result.row_id is not None
                accepted = self._debouncer.submit(
                    DebounceEntry(
                        event=event, triple=result.triple, row_id=result.row_id
                    )
                )
                if not accepted:
                    # 既知versionの再受信: 窓を延長せず即ack(代表は最初のメッセージ)
                    event.ack()
                return
        except Exception:
            # ackせず終了 → ack_deadline後に再配信(at-least-onceの回収)
            logger.exception("event dispatch failed message_id=%s", event.message_id)

    async def _on_release(self, group: DebounceGroup) -> None:
        """窓解放: latestを処理し、吸収行は統合理由でprocessed閉包(design §2.3)。"""
        try:
            await self._stage1.process(
                group.latest.event, group.latest.triple, group.latest.row_id
            )
            await self._kick_embedding(*group.latest.triple)
            group.latest.event.ack()
            for entry in group.absorbed:
                await self._stage1.discard(entry.row_id, reason="debounced_superceded")
                entry.event.ack()
        except Exception:
            logger.exception("debounce release failed intent_id=%s", group.intent_id)

    async def _kick_embedding(
        self, event_type: str, intent_id: uuid.UUID, version: int
    ) -> None:
        """Stage1処理コミット後・ack前のEmbeddingキック(design §2.1-B)。

        created/updatedのみ(06 §9「処理はEmbedding要求のキックまで」)。
        LLM失敗はhandle内で握られ(embedding NULL=バックフィル対象)、DB失敗は
        ここから伝播して_dispatch/_on_releaseの既存exceptが受け、ackなし
        再配信が回収する。embedding未注入(ws-1資産の試験)は何もしない。
        """
        if self._embedding is None or event_type not in (EVENT_CREATED, EVENT_UPDATED):
            return
        await self._embedding.handle(intent_id, version)

    async def _run_post_retrieval(self, intent_id: uuid.UUID) -> None:
        """L1〜3後の共通チェーン(design §2.1案A)。

        GroupEngine.handle(生成)→ JevWorker(group_ctx付き)→ LatchEngine(1対1)
        → GroupEngine.finalize(集約)。各部品は未注入なら何もしない
        (ws-1/ws-5/ws-6資産の試験互換)。DB失敗は伝播し_dispatch/_on_release
        の既存except・Runnerの握りへ載る(各部のガードで冪等)。
        """
        group_ctx = None
        if self._group is not None:
            group_ctx = await self._group.handle(intent_id)
        if self._jev is not None:
            await self._jev.handle(intent_id, group_ctx)
        if self._latch is not None:
            await self._latch.handle(intent_id)
        if self._group is not None:
            await self._group.finalize(intent_id)

    async def _kick_jev(
        self, event_type: str, intent_id: uuid.UUID, version: int
    ) -> None:
        """Stage1処理コミット後・ack前のLayer 4→5→集約キック(design §2.1案A)。

        embedding_completedのみ(06 §1「Layer 1〜5はembedding_completed起点」)。
        GroupEngine.handle→Jev→Latch→GroupEngine.finalizeの共通チェーン
        (_run_post_retrieval)を直列実行(Layer 5+通知の層別予算≤2秒を1連の
        流れで守る)。DB失敗はここから伝播して_dispatch/_on_releaseの
        既存exceptが受け、ackなし再配信が回収する(冪等ガード
        0005部分UNIQUE・latch_score IS NULL・ON CONFLICT・条件付きUPDATE)。
        各部品それぞれ未注入(ws-1/ws-5/ws-6/ws-7資産の試験)は何もしない。
        version引数はhandleが起点読取で再検証するため使わない(IFは起点非依存)。
        """
        if event_type != EVENT_EMBEDDING_COMPLETED:
            return
        await self._run_post_retrieval(intent_id)

    async def _run_direct_pipeline(self, engine, intent_id: uuid.UUID) -> None:
        """Bucket/catch-up起点の直接投入(design §2.8-3・Eventを発行しない)。

        L1〜3を自前トランザクションで実行し、コミット後に共通チェーン
        (_run_post_retrieval: group→jev→latch→finalize)を直列キック
        (stage1の_run_matchingはstage1トランザクションに同乗する構造のため
        流用しない)。例外は握らずRunnerへ伝播(Runnerが握って次周期で回収)。
        冪等は各部のガードで担保済み。
        """
        async with engine.begin() as conn:
            await run_candidate_retrieval(conn, self.clock, intent_id)
        await self._run_post_retrieval(intent_id)

    async def _run_matching(self, conn, intent_id: uuid.UUID) -> None:
        """embedding_completed 起点の Layer 1〜3 実行(design §2.6・§2.7)。

        reevalガードで30分以内の再評価をスキップする(06 §5(c)「Event自体は
        処理済みとし、再評価は行わない」 — スキップ理由は構造化ログ)。
        run_candidate_retrieval は stage1 のトランザクションに同乗する
        (失敗→ロールバック→再試行5回→quarantinedの既存経路)。reevalの
        Redis呼び出しがDBトランザクション内に入るが、1回のSET NX(低レイテン
        シ・compose内ネットワーク)で接続の長期保持を生まないため許容
        (design §2.7)。Redis例外はfail-closed(ReevalGuardが専用例外へ包む)。
        """
        if self._reeval is not None and not await self._reeval.allow(intent_id):
            logger.info(
                "matching reeval suppressed intent_id=%s (within 30min window)",
                intent_id,
            )
            return
        await run_candidate_retrieval(conn, self.clock, intent_id)
