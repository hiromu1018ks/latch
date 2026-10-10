"""D1版・原子的な業務command(計画§3.2・T3)。

設計(§3.2のmarker従属方式):
- 業務更新は「条件付きUPDATE + 従属INSERT..SELECT..WHERE EXISTS」の
  単一batchで構成する。主UPDATEが0行なら従属行は全て0行で入り、
  副作用が部分保存されない(markerに結果だけ残る)。
- 共有Intentの同時成立は intent_match_ownership のUNIQUE制約が防ぐ。
  制約違反でbatch全体がrollbackするため、負けた側は何も確定しない。
- command marker(commands.id=command_id)で内部再配送を冪等化する。
  既存APIの「二度目の回答409」を成功へ変えるものではない。
- 巻き戻し用journalを業務更新と同じbatchで保存する(replayは状態の
  再適用のみ・LLMや通知の再送はしない)。

参照実装(PostgreSQL版 latches/service.py)と同じ業務結果を返す。
"""

from __future__ import annotations

import uuid as uuid_mod
from typing import Any

from latch.adapters.d1.database import LocalD1Database, canonical_json
from latch.domain.time import from_micros, micros
from latch.domain.types import CommandResult, CommandStatus
from latch.latches.calibration import (
    build_prediction,
    classify_segment,
    pick_eval_row,
    pick_min_pair,
    segment_texts,
    select_versioned_pairs,
)

_RESPONSE_ACCEPTING = ("proposed", "partial_accept")
_TERMINAL_FOR_CALIBRATION = ("rejected", "matched")


def _new_id() -> str:
    return str(uuid_mod.uuid4())


def _loads(text: str | None, default: Any) -> Any:
    import json

    if text is None:
        return default
    return json.loads(text)


class D1AtomicRepository:
    """ports.atomic.AtomicRepository のD1実装(4つのapply_*)。"""

    def __init__(self, db: LocalD1Database) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # 共通: 冪等化(marker確認)・結果記録
    # ------------------------------------------------------------------

    async def _marker(self, command_id: str):
        return (
            await self.db.prepare(
                "SELECT applied_at, result_status, result_response FROM commands"
                " WHERE id = ?"
            )
            .bind(command_id)
            .first()
        )

    async def _record_marker(
        self,
        command_id: str,
        kind: str,
        target_id: str,
        now,
        status: str,
        response: dict | None,
        requested_at,
    ) -> None:
        await self.db.batch(
            [
                self.db.prepare(
                    "INSERT OR IGNORE INTO commands"
                    " (id, kind, target_id, requested_at, applied_at,"
                    "  result_status, result_response)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)"
                ).bind(
                    command_id,
                    kind,
                    target_id,
                    micros(requested_at),
                    micros(now),
                    status.value,
                    canonical_json(response) if response else None,
                )
            ]
        )

    async def _finish_marker(
        self, command_id: str, status: str, response: dict | None, now
    ) -> None:
        await self.db.batch(
            [
                self.db.prepare(
                    "UPDATE commands SET applied_at = ?, result_status = ?,"
                    " result_response = ? WHERE id = ?"
                ).bind(
                    micros(now),
                    status,
                    canonical_json(response) if response else None,
                    command_id,
                )
            ]
        )

    def _journal_stmt(
        self,
        command_id: str,
        seq: int,
        *,
        change_kind: str,
        table_name: str,
        row_id: str,
        before: dict | None,
        after: dict | None,
        now,
    ):
        return self.db.prepare(
            "INSERT INTO journal"
            " (command_id, sequence_in_command, change_kind, table_name,"
            "  row_id, before_row, after_row, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        ).bind(
            command_id,
            seq,
            change_kind,
            table_name,
            row_id,
            canonical_json(before) if before is not None else None,
            canonical_json(after) if after is not None else None,
            micros(now),
        )

    # ------------------------------------------------------------------
    # apply_response(回答・全員状態・競合取消・通知job・calibration)
    # ------------------------------------------------------------------

    async def apply_response(
        self,
        command_id: str,
        expected_revision: int | None,
        response: dict[str, Any],
        now,
    ) -> CommandResult:
        """latches.respond と同じ業務結果(design §2.2手順0〜6相当)。

        response = {latch_id, auth_provider, auth_subject, value}
        """
        existing = await self._marker(command_id)
        if existing is not None and existing["applied_at"] is not None:
            stored = _loads(existing["result_response"], None)
            return CommandResult(
                status=CommandStatus(existing["result_status"]),
                response=stored,
            )

        latch_id = response["latch_id"]
        row = (
            await self.db.prepare(
                "SELECT id, intent_ids, group_candidate_id, proposal, score,"
                " responses, status, response_deadline, expires_at, created_at"
                " FROM latches WHERE id = ?"
            )
            .bind(latch_id)
            .first()
        )
        if row is None:
            return CommandResult(CommandStatus.REJECTED, error_code="NOT_FOUND")
        # 認証subject→user_idの解決はroute層の責務(参照実装のfetch_user_id
        # と同じ境界)。commandは解決済みuser_idを受け取る。
        user_id = response["user_id"]
        exists = (
            await self.db.prepare("SELECT 1 AS x FROM users WHERE id = ?")
            .bind(user_id)
            .first()
        )
        if exists is None:
            return CommandResult(CommandStatus.REJECTED, error_code="NOT_FOUND")
        intent_ids: list[str] = _loads(row["intent_ids"], [])
        my_intents = (
            await self.db.prepare(
                "SELECT id FROM intents WHERE user_id = ? AND id IN ("
                " SELECT value FROM json_each(?))"
            )
            .bind(user_id, row["intent_ids"])
            .all()
        )
        if not my_intents:
            return CommandResult(CommandStatus.REJECTED, error_code="FORBIDDEN")
        my_intent = my_intents[0]["id"]

        responses: list[dict] = _loads(row["responses"], [])
        for r in responses:
            if r.get("user_id") == user_id:
                return CommandResult(
                    CommandStatus.REJECTED, error_code="ALREADY_ANSWERED"
                )
        deadline = from_micros(row["response_deadline"])
        expires = from_micros(row["expires_at"])
        if (
            row["status"] not in _RESPONSE_ACCEPTING
            or deadline <= now
            or expires <= now
        ):
            if deadline <= now or expires <= now:
                return CommandResult(CommandStatus.REJECTED, error_code="LATCH_EXPIRED")
            return CommandResult(CommandStatus.REJECTED, error_code="LATCH_CLOSED")

        item = {
            "user_id": user_id,
            "intent_id": my_intent,
            "response": response["value"],
            "answered_at": now.isoformat(),
        }
        # 見送り理由(05 §2・D-07)。defer時のみ受理はroute層のPydanticで検証済み
        if response["value"] == "defer" and response.get("reasons"):
            item["reason_codes"] = list(response["reasons"])
        after = [*responses, item]
        new_status = _compute_new_status(response["value"], after, len(intent_ids))

        # --- 従属データの事前計算(全てguard付きでbatchへ) ---
        # participants(通知先用)
        participants = (
            await self.db.prepare(
                "SELECT DISTINCT user_id FROM intents WHERE id IN ("
                " SELECT value FROM json_each(?))"
            )
            .bind(row["intent_ids"])
            .all()
        )
        participant_ids = [p["user_id"] for p in participants]

        # 競合(openなままの共有Intent latch)
        conflicts = (
            await self.db.prepare(
                "SELECT id, status FROM latches WHERE status IN"
                " ('candidate','proposed','partial_accept') AND id != ?"
                " AND EXISTS (SELECT 1 FROM json_each(latches.intent_ids) je"
                "             WHERE je.value IN (SELECT value FROM json_each(?)))"
            )
            .bind(latch_id, row["intent_ids"])
            .all()
        )

        # calibration評価行(参照実装と同じ選択規則)
        calib_prediction = await self._calibration_prediction(
            row, intent_ids, new_status
        )

        guard = (
            "EXISTS (SELECT 1 FROM latches WHERE id = ? AND status = ?"
            " AND responses = ?)"
        )
        guard_params = (latch_id, new_status, canonical_json(after))
        now_us = micros(now)

        stmts = [
            # marker(冪等化。以降は全てこのcommandに従属)
            self.db.prepare(
                "INSERT OR IGNORE INTO commands"
                " (id, kind, target_id, requested_at, applied_at,"
                "  result_status, result_response)"
                " VALUES (?, 'response', ?, ?, NULL, NULL, NULL)"
            ).bind(command_id, latch_id, now_us),
            # 主更新(条件付き: 参照実装 _UPDATE_RESPONSE と同一条件 —
            # status受付中・snapshot一致・期限有効・全参加Intent活性)
            self.db.prepare(
                "UPDATE latches SET responses = ?, status = ? WHERE id = ?"
                " AND status = ? AND responses = ?"
                " AND response_deadline > ? AND expires_at > ?"
                " AND NOT EXISTS (SELECT 1 FROM json_each(intent_ids) je"
                "   JOIN intents i ON i.id = je.value"
                "   WHERE i.status NOT IN ('active', 'paused'))"
            ).bind(
                canonical_json(after),
                new_status,
                latch_id,
                row["status"],
                row["responses"],
                now_us,
                now_us,
            ),
            # 状態イベント(主更新の結果に従属)
            self.db.prepare(
                "INSERT INTO latch_status_events"
                " (id, latch_id, from_status, to_status, user_id, created_at)"
                " SELECT ?, ?, ?, ?, ?, ? WHERE " + guard
            ).bind(
                _new_id(),
                latch_id,
                row["status"],
                new_status,
                user_id,
                now_us,
                *guard_params,
            ),
        ]
        seq = 1
        stmts.append(
            self._journal_stmt(
                command_id,
                seq,
                change_kind="upsert",
                table_name="latches",
                row_id=latch_id,
                before=dict(row),
                after={
                    "id": latch_id,
                    "status": new_status,
                    "responses": canonical_json(after),
                },
                now=now,
            )
        )

        if new_status == "matched":
            # Intent成立の所有権(UNIQUE仲裁・違反時はbatch全体rollback)
            for iid in intent_ids:
                stmts.append(
                    self.db.prepare(
                        "INSERT INTO intent_match_ownership"
                        " (intent_id, latch_id, confirmed_at)"
                        " SELECT ?, ?, ? WHERE " + guard
                    ).bind(iid, latch_id, now_us, *guard_params)
                )
                stmts.append(
                    self.db.prepare(
                        "UPDATE intents SET status = 'matched',"
                        " updated_at = ? WHERE id = ? AND status IN"
                        " ('active','paused') AND EXISTS ("
                        " SELECT 1 FROM intent_match_ownership"
                        " WHERE intent_id = ? AND latch_id = ?)"
                    ).bind(now_us, iid, iid, latch_id)
                )
                # 成立Intentは再評価機会から外す(§5.4。claimed中の行も
                # 一時化→claim完了の条件付きUPDATEが不成立=次回置かれない)
                stmts.append(
                    self.db.prepare(
                        "UPDATE intent_evaluation_schedule SET status = 'paused',"
                        " lease_owner = NULL, lease_expires_at = NULL,"
                        " next_evaluate_at = 9223372036854775807, updated_at = ?"
                        " WHERE intent_id = ? AND status != 'paused' AND " + guard
                    ).bind(now_us, iid, *guard_params)
                )
            # 競合cancel(既にclosedになったものはguardで0行)
            for conf in conflicts:
                stmts.append(
                    self.db.prepare(
                        "UPDATE latches SET status = 'cancelled' WHERE id = ?"
                        " AND status = ?"
                    ).bind(conf["id"], conf["status"])
                )
                stmts.append(
                    self.db.prepare(
                        "INSERT INTO latch_status_events"
                        " (id, latch_id, from_status, to_status, user_id,"
                        "  created_at) SELECT ?, ?, ?, 'cancelled', NULL, ?"
                        " WHERE EXISTS (SELECT 1 FROM latches WHERE id = ?"
                        " AND status = 'cancelled')"
                    ).bind(_new_id(), conf["id"], conf["status"], now_us, conf["id"])
                )
            # 成立通知の先行書き込み(参加者全員)
            for uid in participant_ids:
                stmts.append(
                    self.db.prepare(
                        "INSERT INTO notifications"
                        " (id, user_id, type, payload, created_at)"
                        " SELECT ?, ?, 'latch_matched', ?, ? WHERE " + guard
                    ).bind(
                        _new_id(),
                        uid,
                        canonical_json({"latch_id": latch_id}),
                        now_us,
                        *guard_params,
                    )
                )
            # 通知job(T5の配送がここを拾う)
            stmts.append(
                self.db.prepare(
                    "INSERT INTO jobs (id, kind, ref_id, status, next_due_at,"
                    " created_at, updated_at)"
                    " SELECT ?, 'notify', ?, 'pending', ?, ?, ? WHERE " + guard
                ).bind(_new_id(), latch_id, now_us, now_us, now_us, *guard_params)
            )

        if new_status in _TERMINAL_FOR_CALIBRATION and calib_prediction is not None:
            stmts.append(
                self.db.prepare(
                    "INSERT INTO calibration_records"
                    " (id, latch_id, intent_ids, prediction, proposal_snapshot,"
                    "  actual_responses, matched, created_at, updated_at)"
                    " SELECT ?, ?, ?, ?, ?, ?, ?, ?, ? WHERE " + guard
                ).bind(
                    _new_id(),
                    latch_id,
                    canonical_json(intent_ids),
                    canonical_json(calib_prediction["prediction"]),
                    canonical_json(calib_prediction["proposal_snapshot"]),
                    canonical_json(after),
                    1 if new_status == "matched" else 0,
                    now_us,
                    now_us,
                    *guard_params,
                )
            )

        results = await self.db.batch(stmts)
        main_update_changes = results[1].changes

        latch_now = (
            await self.db.prepare("SELECT responses, status FROM latches WHERE id = ?")
            .bind(latch_id)
            .first()
        )
        reached = (
            latch_now is not None
            and latch_now["status"] == new_status
            and latch_now["responses"] == canonical_json(after)
        )
        if not reached and main_update_changes == 0:
            # 誰かが先に書いた(latch_responses競合) → 再分類
            classified = await self._classify_response_conflict(latch_now, user_id, now)
            await self._finish_marker(command_id, classified.status.value, None, now)
            return classified
        if not reached:
            # 極めて稀(単一接続では起きない): 状態不明はconflictとして扱う
            await self._finish_marker(command_id, "conflict", None, now)
            return CommandResult(CommandStatus.CONFLICT)

        api_response = {
            "latch": {
                "id": latch_id,
                "status": new_status,
                "my_response": response["value"],
                "remaining_responses": _remaining(after, len(intent_ids)),
            }
        }
        await self._finish_marker(command_id, "applied", api_response, now)
        return CommandResult(
            CommandStatus.APPLIED,
            response=api_response,
            revision=len(after),
        )

    async def _classify_response_conflict(self, latch_now, user_id, now):
        """主UPDATE不採用時の409分類(参照: _raise_closed_or_expired・期限切れが上)。"""
        if latch_now is None:
            return CommandResult(CommandStatus.REJECTED, error_code="NOT_FOUND")
        responses = _loads(latch_now["responses"], [])
        for r in responses:
            if r.get("user_id") == user_id:
                return CommandResult(
                    CommandStatus.REJECTED, error_code="ALREADY_ANSWERED"
                )
        deadline = from_micros(latch_now["response_deadline"])
        expires = from_micros(latch_now["expires_at"])
        if deadline <= now or expires <= now:
            return CommandResult(CommandStatus.REJECTED, error_code="LATCH_EXPIRED")
        if latch_now["status"] not in _RESPONSE_ACCEPTING:
            return CommandResult(CommandStatus.REJECTED, error_code="LATCH_CLOSED")
        return CommandResult(CommandStatus.CONFLICT)

    # ------------------------------------------------------------------
    # apply_response_undo(回答取り消し・05 §5 D-06)
    # ------------------------------------------------------------------

    async def apply_response_undo(
        self,
        command_id: str,
        expected_revision: int | None,
        mutation: dict[str, Any],
        now,
    ) -> CommandResult:
        """latches.undo_response と同じ業務結果(参照実装service.undo_response相当)。

        mutation = {latch_id, user_id}。残った他者のno/deferがある場合は
        状態がrejectedへ戻らないため REJECTED/LATCH_CLOSED。
        """
        existing = await self._marker(command_id)
        if existing is not None and existing["applied_at"] is not None:
            return CommandResult(
                status=CommandStatus(existing["result_status"]),
                response=_loads(existing["result_response"], None),
            )

        latch_id = mutation["latch_id"]
        user_id = mutation["user_id"]
        row = (
            await self.db.prepare(
                "SELECT id, intent_ids, responses, status, response_deadline,"
                " expires_at, proposal, group_candidate_id, score, created_at,"
                " completed_at, left_user_ids FROM latches WHERE id = ?"
            )
            .bind(latch_id)
            .first()
        )
        if row is None:
            return CommandResult(CommandStatus.REJECTED, error_code="NOT_FOUND")
        my_intents = (
            await self.db.prepare(
                "SELECT id FROM intents WHERE user_id = ? AND id IN ("
                " SELECT value FROM json_each(?))"
            )
            .bind(user_id, row["intent_ids"])
            .all()
        )
        if not my_intents:
            # 05 §5 undo: 参加者以外は404(参照実装と同一)
            return CommandResult(CommandStatus.REJECTED, error_code="NOT_FOUND")

        responses: list[dict] = _loads(row["responses"], [])
        mine = next((r for r in responses if r.get("user_id") == user_id), None)
        if mine is None:
            return CommandResult(CommandStatus.REJECTED, error_code="UNANSWERED")
        deadline = from_micros(row["response_deadline"])
        expires = from_micros(row["expires_at"])
        if deadline <= now or expires <= now:
            return CommandResult(CommandStatus.REJECTED, error_code="LATCH_EXPIRED")
        remaining = [r for r in responses if r is not mine]
        new_status = status_after_undo(remaining)
        if row["status"] not in ("proposed", "partial_accept", "rejected") or (
            new_status is None
        ):
            return CommandResult(CommandStatus.REJECTED, error_code="LATCH_CLOSED")

        before_json = canonical_json(responses)
        after_json = canonical_json(remaining)
        guard = (
            "EXISTS (SELECT 1 FROM latches WHERE id = ? AND status = ?"
            " AND responses = ?)"
        )
        guard_params = (latch_id, new_status, after_json)
        now_us = micros(now)
        stmts = [
            self.db.prepare(
                "INSERT OR IGNORE INTO commands"
                " (id, kind, target_id, requested_at, applied_at,"
                "  result_status, result_response)"
                " VALUES (?, 'response_undo', ?, ?, NULL, NULL, NULL)"
            ).bind(command_id, latch_id, now_us),
            # 主更新(条件付き: 参照実装 _UPDATE_RESPONSE_UNDO と同一条件)
            self.db.prepare(
                "UPDATE latches SET responses = ?, status = ? WHERE id = ?"
                " AND status = ? AND responses = ?"
                " AND response_deadline > ? AND expires_at > ?"
                " AND NOT EXISTS (SELECT 1 FROM json_each(intent_ids) je"
                "   JOIN intents i ON i.id = je.value"
                "   WHERE i.status NOT IN ('active', 'paused'))"
            ).bind(
                after_json,
                new_status,
                latch_id,
                row["status"],
                before_json,
                now_us,
                now_us,
            ),
            self.db.prepare(
                "INSERT INTO latch_status_events"
                " (id, latch_id, from_status, to_status, user_id, created_at)"
                " SELECT ?, ?, ?, ?, ?, ? WHERE " + guard
            ).bind(
                _new_id(),
                latch_id,
                row["status"],
                new_status,
                user_id,
                now_us,
                *guard_params,
            ),
            # rejected確定時に作られたcalibrationの破棄(参照実装と同一・
            # 取り消した確定は無かったものとし再確定のUNIQUE衝突を防ぐ)
            *(
                [
                    self.db.prepare(
                        "DELETE FROM calibration_records WHERE latch_id = ?"
                        " AND " + guard
                    ).bind(latch_id, *guard_params)
                ]
                if row["status"] == "rejected"
                else []
            ),
            self._journal_stmt(
                command_id,
                1,
                change_kind="upsert",
                table_name="latches",
                row_id=latch_id,
                before=dict(row),
                after={
                    "id": latch_id,
                    "status": new_status,
                    "responses": after_json,
                },
                now=now,
            ),
        ]
        results = await self.db.batch(stmts)
        main_update_changes = results[1].changes
        latch_now = (
            await self.db.prepare("SELECT responses, status FROM latches WHERE id = ?")
            .bind(latch_id)
            .first()
        )
        reached = (
            latch_now is not None
            and latch_now["status"] == new_status
            and latch_now["responses"] == after_json
        )
        if not reached and main_update_changes == 0:
            classified = await self._classify_response_conflict(latch_now, user_id, now)
            await self._finish_marker(command_id, classified.status.value, None, now)
            return classified
        if not reached:
            await self._finish_marker(command_id, "conflict", None, now)
            return CommandResult(CommandStatus.CONFLICT)

        intent_ids = _loads(row["intent_ids"], [])
        api_response = {
            "latch": {
                "id": latch_id,
                "status": new_status,
                "my_response": None,
                "remaining_responses": _remaining(remaining, len(intent_ids)),
            }
        }
        await self._finish_marker(command_id, "applied", api_response, now)
        return CommandResult(
            CommandStatus.APPLIED,
            response=api_response,
            revision=len(remaining),
        )

    # ------------------------------------------------------------------
    # apply_leave(参加取り消し・05 §5 D-08)
    # ------------------------------------------------------------------

    async def apply_leave(
        self,
        command_id: str,
        expected_revision: int | None,
        mutation: dict[str, Any],
        now,
    ) -> CommandResult:
        """latches.leave と同じ業務結果(参照実装service.leave相当)。

        mutation = {latch_id, user_id, display_name}。
        ①left_user_ids追記②システム行(sender_id NULL)挿入
        ③calibration.cancelled_after=1を単一batchで確定する。
        statusはmatchedのまま(解散しない)。
        """
        existing = await self._marker(command_id)
        if existing is not None and existing["applied_at"] is not None:
            return CommandResult(
                status=CommandStatus(existing["result_status"]),
                response=_loads(existing["result_response"], None),
            )

        latch_id = mutation["latch_id"]
        user_id = mutation["user_id"]
        row = (
            await self.db.prepare(
                "SELECT id, status, left_user_ids FROM latches WHERE id = ?"
            )
            .bind(latch_id)
            .first()
        )
        if row is None:
            return CommandResult(CommandStatus.REJECTED, error_code="NOT_FOUND")
        my_intents = (
            await self.db.prepare(
                "SELECT id FROM intents WHERE user_id = ? AND id IN ("
                " SELECT value FROM json_each((SELECT intent_ids FROM latches"
                "  WHERE id = ?)))"
            )
            .bind(user_id, latch_id)
            .all()
        )
        if not my_intents:
            # 05 §5 leave: 参加者以外は404(参照実装と同一)
            return CommandResult(CommandStatus.REJECTED, error_code="NOT_FOUND")
        if row["status"] != "matched":
            return CommandResult(CommandStatus.REJECTED, error_code="LATCH_CLOSED")
        left: list[str] = _loads(row["left_user_ids"], [])
        if user_id in left:
            return CommandResult(CommandStatus.REJECTED, error_code="ALREADY_LEFT")

        after_left = [*left, user_id]
        left_json = canonical_json(after_left)
        guard = (
            "EXISTS (SELECT 1 FROM latches WHERE id = ? AND status = ?"
            " AND left_user_ids = ?)"
        )
        guard_params = (latch_id, "matched", left_json)
        now_us = micros(now)
        body = f"{mutation.get('display_name') or '参加者'}が参加を取り消しました"
        stmts = [
            self.db.prepare(
                "INSERT OR IGNORE INTO commands"
                " (id, kind, target_id, requested_at, applied_at,"
                "  result_status, result_response)"
                " VALUES (?, 'leave', ?, ?, NULL, NULL, NULL)"
            ).bind(command_id, latch_id, now_us),
            # 主更新(条件付き: matched・未脱退・snapshot一致)
            self.db.prepare(
                "UPDATE latches SET left_user_ids = ? WHERE id = ?"
                " AND status = 'matched' AND left_user_ids = ?"
            ).bind(left_json, latch_id, canonical_json(left)),
            # システム行(sender_id NULL・主更新に従属)
            self.db.prepare(
                "INSERT INTO messages (id, latch_id, sender_id, body, created_at)"
                " SELECT ?, ?, NULL, ?, ? WHERE " + guard
            ).bind(_new_id(), latch_id, body, now_us, *guard_params),
            # 成立後キャンセル記録(行なしは0行で正常・参照実装と同一裁定)
            self.db.prepare(
                "UPDATE calibration_records SET cancelled_after = 1,"
                " updated_at = ? WHERE latch_id = ? AND cancelled_after IS NULL"
                " AND " + guard
            ).bind(now_us, latch_id, *guard_params),
            self._journal_stmt(
                command_id,
                1,
                change_kind="upsert",
                table_name="latches",
                row_id=latch_id,
                before=dict(row),
                after={"id": latch_id, "status": "matched", "left_user_ids": left_json},
                now=now,
            ),
        ]
        results = await self.db.batch(stmts)
        latch_now = (
            await self.db.prepare(
                "SELECT status, left_user_ids FROM latches WHERE id = ?"
            )
            .bind(latch_id)
            .first()
        )
        reached = (
            latch_now is not None
            and latch_now["status"] == "matched"
            and latch_now["left_user_ids"] == left_json
        )
        if not reached and results[1].changes == 0:
            # 並行で先に書いた(再脱退・状態変化)→ 再分類
            if latch_now is None:
                classified = CommandResult(
                    CommandStatus.REJECTED, error_code="NOT_FOUND"
                )
            elif latch_now["status"] != "matched":
                classified = CommandResult(
                    CommandStatus.REJECTED, error_code="LATCH_CLOSED"
                )
            else:
                classified = CommandResult(
                    CommandStatus.REJECTED, error_code="ALREADY_LEFT"
                )
            await self._finish_marker(command_id, classified.status.value, None, now)
            return classified
        if not reached:
            await self._finish_marker(command_id, "conflict", None, now)
            return CommandResult(CommandStatus.CONFLICT)

        api_response = {"latch": {"id": latch_id, "status": "matched", "left": True}}
        await self._finish_marker(command_id, "applied", api_response, now)
        return CommandResult(CommandStatus.APPLIED, response=api_response)

    async def _calibration_prediction(self, row, intent_ids, new_status):
        """参照実装と同じ選択規則(pick_eval_row / pick_min_pair)。"""
        try:
            if row["group_candidate_id"] is None:
                a_id, b_id = sorted(intent_ids)[:2]
                eval_rows = await self._fetch_pair_rows(a_id, b_id)
                hit = pick_eval_row(
                    eval_rows, float(row["score"]), from_micros(row["created_at"])
                )
                pair_ids = (a_id, b_id)
                hit_jev = hit.jev_result if hit is not None else None
            else:
                ms_row = (
                    await self.db.prepare(
                        "SELECT member_scores FROM group_candidates WHERE id = ?"
                    )
                    .bind(row["group_candidate_id"])
                    .first()
                )
                ms = _loads(ms_row["member_scores"], {}) if ms_row else {}
                versions = {
                    uuid_mod.UUID(k): int(v)
                    for k, v in (ms or {}).items()
                    if isinstance(v, int)
                }
                pair_rows = await self._fetch_group_pairs(intent_ids)
                versioned = select_versioned_pairs(pair_rows, versions)
                best = pick_min_pair(versioned)
                hit_jev = best.jev_result if best is not None else None
                pair_ids = (best.a, best.b) if best is not None else None
            if hit_jev is None or pair_ids is None:
                return None
            structured = (
                await self.db.prepare(
                    "SELECT structured_data FROM intents WHERE id IN (?, ?)"
                )
                .bind(pair_ids[0], pair_ids[1])
                .all()
            )
            if len(structured) != 2:
                return None
            segment = classify_segment(
                segment_texts(_loads(structured[0]["structured_data"], {})),
                segment_texts(_loads(structured[1]["structured_data"], {})),
            )
            prediction = build_prediction(hit_jev, float(row["score"]), segment)
            return {
                "prediction": prediction,
                "proposal_snapshot": _loads(row["proposal"], {}),
            }
        except Exception:  # noqa: BLE001 — 評価行欠落は記録なし(参照と同一)
            return None

    async def _fetch_pair_rows(self, a_id: str, b_id: str):
        """1対1評価行材料(jev_resultあり・updated_at DESC — storeと同一)。"""
        from decimal import Decimal

        from latch.latches.calibration import EvalRow

        rows = (
            await self.db.prepare(
                "SELECT latch_score, jev_result, updated_at"
                " FROM match_candidates WHERE intent_a_id = ? AND intent_b_id = ?"
                " AND jev_result IS NOT NULL"
                " ORDER BY updated_at DESC"
            )
            .bind(a_id, b_id)
            .all()
        )
        return [
            EvalRow(
                jev_result=_loads(r["jev_result"], {}),
                latch_score=(
                    Decimal(str(r["latch_score"]))
                    if r["latch_score"] is not None
                    else None
                ),
                updated_at=from_micros(r["updated_at"]),
            )
            for r in rows
        ]

    async def _fetch_group_pairs(self, intent_ids: list[str]):
        """グループ全ペアの評価行(UNIQUE(a,b,va,vb)で一意)。"""
        from latch.latches.calibration import PairEvalRow

        rows = (
            await self.db.prepare(
                "SELECT intent_a_id, intent_b_id, intent_a_version,"
                " intent_b_version, jev_result"
                " FROM match_candidates WHERE jev_result IS NOT NULL"
                " AND intent_a_id IN (SELECT value FROM json_each(?))"
                " AND intent_b_id IN (SELECT value FROM json_each(?))"
            )
            .bind(canonical_json(intent_ids), canonical_json(intent_ids))
            .all()
        )
        return [
            PairEvalRow(
                a=uuid_mod.UUID(r["intent_a_id"]),
                b=uuid_mod.UUID(r["intent_b_id"]),
                va=r["intent_a_version"],
                vb=r["intent_b_version"],
                jev_result=_loads(r["jev_result"], {}),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # apply_intent(保存・版更新・outbox同時確定)
    # ------------------------------------------------------------------

    async def apply_intent(
        self,
        command_id: str,
        expected_revision: int | None,
        mutation: dict[str, Any],
        now,
    ) -> CommandResult:
        """Intent保存(CREATE/UPDATE/PAUSE/RESUME/DELETE)。

        mutation = {op, intent_id?, user_id, fields..., raw_text?,
        structured_data...}
        """
        existing = await self._marker(command_id)
        if existing is not None and existing["applied_at"] is not None:
            return CommandResult(
                CommandStatus(existing["result_status"]),
                response=_loads(existing["result_response"], None),
            )
        op = mutation["op"]
        if op == "create":
            return await self._create_intent(command_id, mutation, now)
        intent_id = mutation["intent_id"]
        row = (
            await self.db.prepare("SELECT * FROM intents WHERE id = ?")
            .bind(intent_id)
            .first()
        )
        if row is None:
            return CommandResult(CommandStatus.REJECTED, error_code="NOT_FOUND")
        if row["user_id"] != mutation["user_id"]:
            return CommandResult(CommandStatus.REJECTED, error_code="FORBIDDEN")
        if expected_revision is not None and row["version"] != expected_revision:
            return CommandResult(CommandStatus.CONFLICT, revision=row["version"])

        if op == "delete":
            return await self._delete_intent(command_id, row, mutation, now)
        return await self._update_intent(command_id, row, mutation, now)

    async def _create_intent(self, command_id, mutation, now) -> CommandResult:
        now_us = micros(now)
        intent_id = _new_id()
        fields = mutation.get("fields", {})
        version = 1
        status = fields.get("status", "active")
        stmts = [
            self.db.prepare(
                "INSERT INTO commands (id, kind, target_id, requested_at)"
                " VALUES (?, 'intent', ?, ?)"
            ).bind(command_id, intent_id, now_us),
            self.db.prepare(
                "INSERT INTO intents (id, user_id, category_primary,"
                " category_secondary, alcohol_involved, raw_text,"
                " structured_data, geo_lon, geo_lat, geo_radius_m, budget_max,"
                " participants_min, participants_max, visibility,"
                " notification_level, status, version, time_start, time_end,"
                " expires_at, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
                " ?, ?, ?, ?, ?, ?)"
            ).bind(
                intent_id,
                mutation["user_id"],
                fields["category_primary"],
                fields.get("category_secondary"),
                1 if fields.get("alcohol_involved") else 0,
                fields.get("raw_text", ""),
                canonical_json(fields.get("structured_data", {})),
                fields.get("geo_lon"),
                fields.get("geo_lat"),
                fields.get("geo_radius_m"),
                fields.get("budget_max"),
                fields.get("participants_min", 2),
                fields.get("participants_max", 2),
                fields.get("visibility", "hidden_until_match"),
                fields.get("notification_level", "proposals_only"),
                status,
                version,
                micros(fields["time_start"]) if fields.get("time_start") else None,
                micros(fields["time_end"]) if fields.get("time_end") else None,
                micros(fields["expires_at"]) if fields.get("expires_at") else None,
                now_us,
                now_us,
            ),
            self._journal_stmt(
                command_id,
                1,
                change_kind="upsert",
                table_name="intents",
                row_id=intent_id,
                before=None,
                after={"id": intent_id, "status": status, "version": version},
                now=now,
            ),
        ]
        # active作成・更新は評価の仕事をoutboxへ(原文・条件本文なし)
        if status in ("active",):
            stmts.append(self._outbox_stmt(intent_id, version, "created", now))
        await self.db.batch(stmts)
        response = {"intent": {"id": intent_id, "status": status, "version": version}}
        await self._finish_marker(command_id, "applied", response, now)
        return CommandResult(CommandStatus.APPLIED, response=response, revision=version)

    async def _update_intent(self, command_id, row, mutation, now) -> CommandResult:
        now_us = micros(now)
        fields = mutation.get("fields", {})
        # version明示指定(本体実装): 参照実装の遷移規則(pause=不変・
        # draft→active同一内容=不変・resume/update=+1)をservice層で再現する
        # ため、mutation["version"]で上書きできる。未指定は従来どおり+1。
        new_version = mutation.get("version", row["version"] + 1)
        new_status = fields.get("status", row["status"])
        sets = ["updated_at = ?", "version = ?", "status = ?"]
        params: list[Any] = [now_us, new_version, new_status]
        column_map = {
            "category_primary": "category_primary",
            "category_secondary": "category_secondary",
            "alcohol_involved": "alcohol_involved",
            "raw_text": "raw_text",
            "structured_data": "structured_data",
            "geo_lon": "geo_lon",
            "geo_lat": "geo_lat",
            "geo_radius_m": "geo_radius_m",
            "budget_max": "budget_max",
            "participants_min": "participants_min",
            "participants_max": "participants_max",
            "visibility": "visibility",
            "notification_level": "notification_level",
            "time_start": "time_start",
            "time_end": "time_end",
            "expires_at": "expires_at",
        }
        for key, col in column_map.items():
            if key in fields:
                value = fields[key]
                if key == "alcohol_involved":
                    value = 1 if value else 0
                if key in ("time_start", "time_end", "expires_at"):
                    value = micros(value) if value else None
                if key == "structured_data":
                    value = canonical_json(value)
                sets.append(f"{col} = ?")
                params.append(value)
        reason = {
            "pause": "paused",
            "resume": "updated",
        }.get(mutation["op"], "updated")
        stmts = [
            self.db.prepare(
                "INSERT INTO commands (id, kind, target_id, requested_at)"
                " VALUES (?, 'intent', ?, ?)"
            ).bind(command_id, row["id"], now_us),
            self.db.prepare(
                f"UPDATE intents SET {', '.join(sets)} WHERE id = ? AND version = ?"
            ).bind(*params, row["id"], row["version"]),
            self._journal_stmt(
                command_id,
                1,
                change_kind="upsert",
                table_name="intents",
                row_id=row["id"],
                before=dict(row),
                after={"id": row["id"], "version": new_version, "status": new_status},
                now=now,
            ),
        ]
        if new_status == "active" and mutation["op"] in ("update", "resume"):
            stmts.append(self._outbox_stmt(row["id"], new_version, reason, now))
        results = await self.db.batch(stmts)
        if results[1].changes == 0:
            await self._finish_marker(command_id, "conflict", None, now)
            return CommandResult(CommandStatus.CONFLICT, revision=row["version"])
        response = {
            "intent": {"id": row["id"], "status": new_status, "version": new_version}
        }
        await self._finish_marker(command_id, "applied", response, now)
        return CommandResult(
            CommandStatus.APPLIED, response=response, revision=new_version
        )

    async def _delete_intent(self, command_id, row, mutation, now) -> CommandResult:
        """削除: 対象を利用不可にする状態と削除jobを同時に確定(§3.2)。

        物理削除・cascade は retention job(T10)が小さなbatchで再開可能に
        進める。ここでは status=cancelled + tombstone + 関連open latchの
        取消 + パートナーIntent復帰を確定する。
        """
        now_us = micros(now)
        conflicts = (
            await self.db.prepare(
                "SELECT id, status FROM latches WHERE status IN"
                " ('candidate','proposed','partial_accept','matched')"
                " AND EXISTS (SELECT 1 FROM json_each(latches.intent_ids)"
                "             WHERE value = ?)"
            )
            .bind(row["id"])
            .all()
        )
        # パートナー(共有Intentを持つlatchの他Intent)復帰
        partner_ids: list[str] = []
        for conf in conflicts:
            rows = (
                await self.db.prepare("SELECT value FROM json_each(?)")
                .bind(
                    canonical_json(
                        _loads(
                            (
                                await self.db.prepare(
                                    "SELECT intent_ids FROM latches WHERE id = ?"
                                )
                                .bind(conf["id"])
                                .first()
                            )["intent_ids"],
                            [],
                        )
                    )
                )
                .all()
            )
            partner_ids.extend(r["value"] for r in rows if r["value"] != row["id"])

        stmts = [
            self.db.prepare(
                "INSERT INTO commands (id, kind, target_id, requested_at)"
                " VALUES (?, 'safety', ?, ?)"
            ).bind(command_id, row["id"], now_us),
            self.db.prepare(
                "UPDATE intents SET status = 'cancelled', updated_at = ?"
                " WHERE id = ? AND status != 'cancelled'"
            ).bind(now_us, row["id"]),
            self.db.prepare(
                "INSERT OR IGNORE INTO deletion_tombstones"
                " (id, target_kind, target_id, reason, state, created_at)"
                " VALUES (?, 'intent', ?, 'intent_delete', 'pending', ?)"
            ).bind(_new_id(), row["id"], now_us),
        ]
        seq = 3
        for conf in conflicts:
            new_status = "cancelled"
            stmts.append(
                self.db.prepare(
                    "UPDATE latches SET status = ? WHERE id = ? AND status = ?"
                ).bind(new_status, conf["id"], conf["status"])
            )
            stmts.append(
                self.db.prepare(
                    "INSERT INTO latch_status_events"
                    " (id, latch_id, from_status, to_status, user_id,"
                    "  created_at) SELECT ?, ?, ?, 'cancelled', NULL, ?"
                    " WHERE EXISTS (SELECT 1 FROM latches WHERE id = ?"
                    " AND status = 'cancelled')"
                ).bind(_new_id(), conf["id"], conf["status"], now_us, conf["id"])
            )
            stmts.append(
                self._journal_stmt(
                    command_id,
                    seq,
                    change_kind="upsert",
                    table_name="latches",
                    row_id=conf["id"],
                    before=None,
                    after={"status": "cancelled"},
                    now=now,
                )
            )
            seq += 1
        # パートナー復帰(期限が来ていないものだけ)
        for pid in dict.fromkeys(partner_ids):
            stmts.append(
                self.db.prepare(
                    "UPDATE intents SET status = 'active', updated_at = ?"
                    " WHERE id = ? AND status = 'matched' AND expires_at > ?"
                ).bind(now_us, pid, now_us)
            )
            stmts.append(
                self.db.prepare(
                    "UPDATE intents SET status = 'expired', updated_at = ?"
                    " WHERE id = ? AND status = 'matched' AND expires_at <= ?"
                ).bind(now_us, pid, now_us)
            )
        # 検索snapshotの無効化(§3.3: 削除・同意撤回時は集合も無効化)
        stmts.append(
            self.db.prepare(
                "DELETE FROM retrieval_snapshot_rows WHERE intent_id = ?"
            ).bind(row["id"])
        )
        stmts.append(
            self.db.prepare(
                "DELETE FROM retrieval_snapshots WHERE id NOT IN ("
                " SELECT DISTINCT snapshot_id FROM retrieval_snapshot_rows)"
            )
        )
        stmts.append(self._outbox_stmt(row["id"], row["version"], "deleted", now))
        await self.db.batch(stmts)
        response = {"deleted": True, "intent_id": row["id"]}
        await self._finish_marker(command_id, "applied", response, now)
        return CommandResult(CommandStatus.APPLIED, response=response)

    def _outbox_stmt(self, intent_id, version, reason, now):
        """意思の保存と同じbatchに入れる配送待ち(§4: 参照のみ・原文なし)。"""
        return self.db.prepare(
            "INSERT OR IGNORE INTO outbox"
            " (id, event_id, intent_id, intent_version, reason,"
            "  due_generation, writer_generation, payload_schema_version,"
            "  status, created_at)"
            " VALUES (?, ?, ?, ?, ?, 1, 1, 1, 'pending', ?)"
        ).bind(
            _new_id(),
            f"{reason}:{intent_id}:{version}",
            intent_id,
            version,
            reason,
            micros(now),
        )

    # ------------------------------------------------------------------
    # apply_safety(block・同意撤回・削除と公開状態の一括確定)
    # ------------------------------------------------------------------

    async def apply_safety(
        self, command_id: str, mutation: dict[str, Any], now
    ) -> CommandResult:
        existing = await self._marker(command_id)
        if existing is not None and existing["applied_at"] is not None:
            return CommandResult(
                CommandStatus(existing["result_status"]),
                response=_loads(existing["result_response"], None),
            )
        kind = mutation["kind"]
        if kind == "block":
            return await self._block(command_id, mutation, now)
        if kind == "unblock":
            return await self._unblock(command_id, mutation, now)
        if kind == "consent_withdraw":
            return await self._consent_withdraw(command_id, mutation, now)
        if kind == "account_delete":
            return await self._account_delete(command_id, mutation, now)
        raise ValueError(f"unknown safety kind: {kind}")

    async def _block(self, command_id, mutation, now) -> CommandResult:
        """D-23: block登録と公開状態(cancel)を一括確定。"""
        now_us = micros(now)
        blocker = mutation["blocker_id"]
        blocked = mutation["blocked_id"]
        target = (
            await self.db.prepare("SELECT id FROM users WHERE id = ?")
            .bind(blocked)
            .first()
        )
        if target is None:
            return CommandResult(CommandStatus.REJECTED, error_code="NOT_FOUND")
        if blocker == blocked:
            return CommandResult(CommandStatus.REJECTED, error_code="VALIDATION_ERROR")
        # block対象と共存するopen latch(両ユーザーのIntentを含むもの)
        shared = (
            await self.db.prepare(
                "SELECT l.id, l.status FROM latches l"
                " JOIN intents ia ON ia.user_id = ?"
                " JOIN intents ib ON ib.user_id = ?"
                " WHERE l.status IN ('candidate','proposed','partial_accept')"
                " AND EXISTS (SELECT 1 FROM json_each(l.intent_ids) ja"
                "             WHERE ja.value = ia.id)"
                " AND EXISTS (SELECT 1 FROM json_each(l.intent_ids) jb"
                "             WHERE jb.value = ib.id)"
            )
            .bind(blocker, blocked)
            .all()
        )
        stmts = [
            self.db.prepare(
                "INSERT INTO commands (id, kind, target_id, requested_at)"
                " VALUES (?, 'safety', ?, ?)"
            ).bind(command_id, blocked, now_us),
            self.db.prepare(
                "INSERT OR IGNORE INTO blocks (id, blocker_id, blocked_id,"
                " created_at) VALUES (?, ?, ?, ?)"
            ).bind(_new_id(), blocker, blocked, now_us),
        ]
        for latch in shared:
            stmts.append(
                self.db.prepare(
                    "UPDATE latches SET status = 'cancelled' WHERE id = ?"
                    " AND status = ?"
                ).bind(latch["id"], latch["status"])
            )
            stmts.append(
                self.db.prepare(
                    "INSERT INTO latch_status_events"
                    " (id, latch_id, from_status, to_status, user_id,"
                    "  created_at) SELECT ?, ?, ?, 'cancelled', ?, ?"
                    " WHERE EXISTS (SELECT 1 FROM latches WHERE id = ?"
                    " AND status = 'cancelled')"
                ).bind(
                    _new_id(),
                    latch["id"],
                    latch["status"],
                    blocker,
                    now_us,
                    latch["id"],
                )
            )
        await self.db.batch(stmts)
        response = {"blocked_id": blocked}
        await self._finish_marker(command_id, "applied", response, now)
        return CommandResult(CommandStatus.APPLIED, response=response)

    async def _unblock(self, command_id, mutation, now) -> CommandResult:
        res = (
            await self.db.prepare(
                "DELETE FROM blocks WHERE blocker_id = ? AND blocked_id = ?"
            )
            .bind(mutation["blocker_id"], mutation["blocked_id"])
            .run()
        )
        if res.changes == 0:
            return CommandResult(CommandStatus.REJECTED, error_code="NOT_FOUND")
        response = {"unblocked_id": mutation["blocked_id"]}
        await self._record_marker(
            command_id,
            "safety",
            mutation["blocked_id"],
            now,
            CommandStatus.APPLIED,
            response,
            now,
        )
        return CommandResult(CommandStatus.APPLIED, response=response)

    async def _consent_withdraw(self, command_id, mutation, now) -> CommandResult:
        """同意撤回: 同意をNULLへし削除jobを同時に確定(§3.2)。"""
        now_us = micros(now)
        stmts = [
            self.db.prepare(
                "INSERT INTO commands (id, kind, target_id, requested_at)"
                " VALUES (?, 'safety', ?, ?)"
            ).bind(command_id, mutation["user_id"], now_us),
            self.db.prepare(
                "UPDATE users SET consent_at = NULL, updated_at = ?"
                " WHERE id = ? AND consent_at IS NOT NULL"
            ).bind(now_us, mutation["user_id"]),
            self.db.prepare(
                "INSERT OR IGNORE INTO deletion_tombstones"
                " (id, target_kind, target_id, reason, state, created_at)"
                " VALUES (?, 'user', ?, 'consent_withdraw', 'pending', ?)"
            ).bind(_new_id(), mutation["user_id"], now_us),
        ]
        await self.db.batch(stmts)
        response = {"consent_withdrawn": True}
        await self._finish_marker(command_id, "applied", response, now)
        return CommandResult(CommandStatus.APPLIED, response=response)

    async def _account_delete(self, command_id, mutation, now) -> CommandResult:
        """退会: session失効・匿名化・open latch cancelを一括確立。

        blocks/reportsは保持(参照実装と同一)。Intentの物理削除・
        message削除はtombstone経由でretention jobが行う(T10)。
        """
        now_us = micros(now)
        user_id = mutation["user_id"]
        user = (
            await self.db.prepare(
                "SELECT id FROM users WHERE id = ?"
                " AND auth_subject NOT LIKE 'deleted:%'"
            )
            .bind(user_id)
            .first()
        )
        if user is None:
            return CommandResult(CommandStatus.REJECTED, error_code="NOT_FOUND")
        anon_subject = f"deleted:{_new_id()}"
        open_latches = (
            await self.db.prepare(
                "SELECT l.id, l.status FROM latches l"
                " JOIN intents i ON i.user_id = ?"
                " WHERE l.status IN ('candidate','proposed','partial_accept','matched')"
                " AND EXISTS (SELECT 1 FROM json_each(l.intent_ids) je"
                "             WHERE je.value = i.id)"
            )
            .bind(user_id)
            .all()
        )
        stmts = [
            self.db.prepare(
                "INSERT INTO commands (id, kind, target_id, requested_at)"
                " VALUES (?, 'safety', ?, ?)"
            ).bind(command_id, user_id, now_us),
            self.db.prepare(
                "UPDATE users SET display_name = '退会したユーザー',"
                " auth_subject = ?, consent_at = NULL, updated_at = ?"
                " WHERE id = ?"
            ).bind(anon_subject, now_us, user_id),
            # session失効(family単位)
            self.db.prepare(
                "UPDATE session_families SET status = 'revoked',"
                " revoked_at = ? WHERE user_id = ? AND status = 'active'"
            ).bind(now_us, user_id),
            self.db.prepare(
                "UPDATE refresh_tokens SET status = 'revoked'"
                " WHERE sid IN (SELECT sid FROM session_families WHERE user_id = ?)"
                " AND status = 'active'"
            ).bind(user_id),
            self.db.prepare(
                "INSERT OR IGNORE INTO deletion_tombstones"
                " (id, target_kind, target_id, reason, state, created_at)"
                " VALUES (?, 'user', ?, 'user_delete', 'pending', ?)"
            ).bind(_new_id(), user_id, now_us),
        ]
        for latch in open_latches:
            stmts.append(
                self.db.prepare(
                    "UPDATE latches SET status = 'cancelled' WHERE id = ?"
                    " AND status = ?"
                ).bind(latch["id"], latch["status"])
            )
        await self.db.batch(stmts)
        response = {"deleted": True}
        await self._finish_marker(command_id, "applied", response, now)
        return CommandResult(CommandStatus.APPLIED, response=response)

    # ------------------------------------------------------------------
    # apply_proposal(最新条件・公開可否・業務上限の確認して提示確定)
    # ------------------------------------------------------------------

    async def apply_proposal(
        self,
        command_id: str,
        expected_revisions: dict[str, int],
        proposal: dict[str, Any],
        now,
    ) -> CommandResult:
        """提示(latch_engine.try_promote相当)。

        proposal = {intent_ids, score, proposal, candidate_id?}
        - 各Intentの版・status(active/paused)・期限を再確認
        - D-08同時進行上限(3/Intent)・日次上限(6/user)を原子カウントで確認
        - openなlatchのintent集合重複(uq_latches_intent_ids_open)が仲裁
        """
        now_us = micros(now)
        intent_ids = proposal["intent_ids"]
        ids_json = canonical_json(intent_ids)
        # 版・status確認
        for iid, expected_ver in expected_revisions.items():
            row = (
                await self.db.prepare(
                    "SELECT version, status, expires_at, user_id FROM intents"
                    " WHERE id = ?"
                )
                .bind(iid)
                .first()
            )
            if row is None or row["version"] != expected_ver:
                return CommandResult(CommandStatus.CONFLICT, error_code="STALE")
            if row["status"] not in ("active", "paused"):
                return CommandResult(
                    CommandStatus.REJECTED, error_code="INTENT_INACTIVE"
                )
            if row["expires_at"] is not None and row["expires_at"] <= now_us:
                return CommandResult(
                    CommandStatus.REJECTED, error_code="INTENT_EXPIRED"
                )
        # D-08: 同時進行3/Intent・日次6/user
        for iid in intent_ids:
            open_count = (
                await self.db.prepare(
                    "SELECT COUNT(*) AS c FROM latches WHERE status IN"
                    " ('proposed','partial_accept') AND EXISTS ("
                    " SELECT 1 FROM json_each(latches.intent_ids) je"
                    " WHERE je.value = ?)"
                )
                .bind(iid)
                .first()
            )
            if open_count["c"] >= 3:
                return CommandResult(
                    CommandStatus.REJECTED, error_code="CONCURRENT_LIMIT"
                )
        day_start_us = micros(_jst_day_start_us(now))
        for uid in await self._intent_owners(intent_ids):
            daily = (
                await self.db.prepare(
                    "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ?"
                    " AND type IN ('proposal','nearby_candidate')"
                    " AND created_at >= ?"
                )
                .bind(uid, day_start_us)
                .first()
            )
            if daily["c"] >= 6:
                return CommandResult(CommandStatus.REJECTED, error_code="DAILY_LIMIT")

        latch_id = _new_id()
        deadline = from_micros(
            proposal.get("response_deadline_us", now_us + 2 * 3_600_000_000)
        )
        stmts = [
            self.db.prepare(
                "INSERT INTO commands (id, kind, target_id, requested_at)"
                " VALUES (?, 'proposal', ?, ?)"
            ).bind(command_id, latch_id, now_us),
            self.db.prepare(
                "INSERT INTO latches (id, intent_ids, group_candidate_id,"
                " proposal, score, responses, status, response_deadline,"
                " expires_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, '[]', 'proposed', ?, ?, ?)"
            ).bind(
                latch_id,
                ids_json,
                proposal.get("group_candidate_id"),
                canonical_json(proposal["proposal"]),
                proposal["score"],
                micros(deadline),
                micros(
                    from_micros(
                        proposal.get("expires_us", now_us + 120 * 3_600_000_000)
                    )
                ),
                now_us,
            ),
        ]
        # 提示と通知jobの同時保存(T10: deliver_notificationが拾う)
        stmts.append(
            self.db.prepare(
                "INSERT INTO jobs (id, kind, ref_id, status, next_due_at,"
                " created_at, updated_at)"
                " SELECT ?, 'notify', ?, 'pending', ?, ?, ?"
                " WHERE EXISTS (SELECT 1 FROM latches WHERE id = ?"
                " AND status = 'proposed')"
            ).bind(_new_id(), latch_id, now_us, now_us, now_us, latch_id)
        )
        seq = 3
        for uid in await self._intent_owners(intent_ids):
            stmts.append(
                self.db.prepare(
                    "INSERT INTO notifications"
                    " (id, user_id, type, payload, created_at)"
                    " SELECT ?, ?, 'proposal', ?, ?"
                    " WHERE EXISTS (SELECT 1 FROM latches WHERE id = ?"
                    " AND status = 'proposed')"
                ).bind(
                    _new_id(),
                    uid,
                    canonical_json({"latch_id": latch_id}),
                    now_us,
                    latch_id,
                )
            )
            seq += 1
        stmts.append(
            self._journal_stmt(
                command_id,
                seq,
                change_kind="upsert",
                table_name="latches",
                row_id=latch_id,
                before=None,
                after={"id": latch_id, "status": "proposed"},
                now=now,
            )
        )
        try:
            await self.db.batch(stmts)
        except Exception as exc:  # noqa: BLE001
            # uq_latches_intent_ids_open 違反等 → 競合
            if "UNIQUE" in str(exc):
                await self._finish_marker(command_id, "conflict", None, now)
                return CommandResult(CommandStatus.CONFLICT)
            raise
        response = {"latch": {"id": latch_id, "status": "proposed"}}
        await self._finish_marker(command_id, "applied", response, now)
        return CommandResult(CommandStatus.APPLIED, response=response)

    async def _intent_owners(self, intent_ids: list[str]) -> list[str]:
        rows = (
            await self.db.prepare(
                "SELECT DISTINCT user_id FROM intents WHERE id IN ("
                " SELECT value FROM json_each(?))"
            )
            .bind(canonical_json(intent_ids))
            .all()
        )
        return [r["user_id"] for r in rows]


def _remaining(responses: list[dict], total: int) -> int:
    yes = sum(1 for r in responses if r.get("response") == "yes")
    return max(0, total - yes)


def _compute_new_status(response: str, responses: list[dict], total: int) -> str:
    """domain の同一実装を使う(T2: 計算の複製をしない)。"""
    from latch.domain.latch_status import compute_new_status

    return compute_new_status(response, responses, total)


def status_after_undo(responses: list[dict]) -> str | None:
    """domain の同一実装を使う(T2: 計算の複製をしない)。"""
    from latch.domain.latch_status import status_after_undo as _impl

    return _impl(responses)


def _jst_day_start_us(now):
    from latch.domain.time import JST, jst_day_start

    return jst_day_start(now.astimezone(JST).date())
