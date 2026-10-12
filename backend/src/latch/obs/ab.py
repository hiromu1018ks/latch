"""A/B実験の運用CLI(05 v0.8・V1-P4)。python -m latch.obs.ab。

start/stop/listのみを提供する(開始判断=人間領域・公開APIなし)。
- start: ab_experimentsへ登録(既存のstopped行は再開=割付行は維持され
  同一ユーザーが同じ群に戻る。control/treatmentの値変更も可)
- stop: status='stopped'+stopped_at(05 v0.8 規定6: 全群へ対照値を適用する
  通常運用へ戻る=09 §5の判定不能時の持続規定と停止ルールの表現)
- list: 実験ごとの群別割付数(進捗の素材)

適用値の伝播は最大30秒(実験行キャッシュ)+評価周期(05 v0.8 規定7)。
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from latch.ab.rules import (
    KEY_LATCH_THRESHOLD,
    KEY_NOTIFICATION_CONCURRENT,
    KEY_NOTIFICATION_DAILY,
    VARIANTS,
)

KNOWN_KEYS = {
    KEY_LATCH_THRESHOLD: float,
    KEY_NOTIFICATION_DAILY: int,
    KEY_NOTIFICATION_CONCURRENT: int,
}

_SQL_UPSERT = text("""
    INSERT INTO ab_experiments
        (experiment_key, status, control_value, treatment_value, started_at)
    VALUES (:key, 'running', :control, :treatment, CAST(:now AS timestamptz))
    ON CONFLICT (experiment_key) DO UPDATE SET
        status = 'running',
        control_value = EXCLUDED.control_value,
        treatment_value = EXCLUDED.treatment_value,
        started_at = EXCLUDED.started_at,
        stopped_at = NULL
""")

_SQL_STOP = text("""
    UPDATE ab_experiments
    SET status = 'stopped', stopped_at = CAST(:now AS timestamptz)
    WHERE experiment_key = :key
""")

_SQL_LIST = text("""
    SELECT e.experiment_key, e.status, e.control_value, e.treatment_value,
           e.started_at, e.stopped_at,
           count(*) FILTER (WHERE a.variant = 'control') AS control_n,
           count(*) FILTER (WHERE a.variant = 'treatment') AS treatment_n
    FROM ab_experiments e
    LEFT JOIN ab_assignments a ON a.experiment_key = e.experiment_key
    GROUP BY e.experiment_key
    ORDER BY e.experiment_key
""")


def _validate_value(key: str, raw: str, which: str) -> str:
    """既知キーは型検査して正規化(labels: '0.6'等の表記揺れを値へ寄せる)。"""
    typer = KNOWN_KEYS.get(key)
    if typer is float:
        try:
            return repr(float(raw))
        except ValueError as exc:
            raise SystemExit(f"{which}値はfloatに解釈できません: {raw!r}") from exc
    if typer is int:
        try:
            return str(int(raw))
        except ValueError as exc:
            raise SystemExit(f"{which}値はintに解釈できません: {raw!r}") from exc
    return raw


async def _run(engine: AsyncEngine, args) -> dict:
    from latch.core.clock import SystemClock

    now = SystemClock().now()
    if args.cmd == "start":
        control = _validate_value(args.key, args.control, "control")
        treatment = _validate_value(args.key, args.treatment, "treatment")
        if control == treatment:
            raise SystemExit("controlとtreatmentが同値です(1テスト1変数・09 §5)")
        async with engine.begin() as conn:
            await conn.execute(
                _SQL_UPSERT,
                {
                    "key": args.key,
                    "control": control,
                    "treatment": treatment,
                    "now": now,
                },
            )
        return {"started": args.key, "control": control, "treatment": treatment}
    if args.cmd == "stop":
        async with engine.begin() as conn:
            res = await conn.execute(_SQL_STOP, {"key": args.key, "now": now})
        if res.rowcount == 0:
            raise SystemExit(f"未知の実験キーです: {args.key}")
        return {"stopped": args.key}
    async with engine.connect() as conn:
        rows = (await conn.execute(_SQL_LIST)).all()
    return {
        "experiments": [
            {
                "key": r[0],
                "status": r[1],
                "control_value": r[2],
                "treatment_value": r[3],
                "started_at": r[4],
                "stopped_at": r[5],
                "assignments": {"control": int(r[6]), "treatment": int(r[7])},
            }
            for r in rows
        ],
        "variants": list(VARIANTS),
    }


def main() -> int:
    import argparse

    from latch.core.db import create_db_engine
    from latch.settings import Settings

    parser = argparse.ArgumentParser(
        prog="latch.obs.ab", description="A/B実験の運用CLI(05 v0.8)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_start = sub.add_parser("start", help="実験の開始/再開")
    p_start.add_argument(
        "--key", required=True, help=f"実験キー {sorted(KNOWN_KEYS)} 等"
    )
    p_start.add_argument("--control", required=True, help="対照値(原則=現行運用値)")
    p_start.add_argument("--treatment", required=True, help="処置値")
    p_stop = sub.add_parser("stop", help="実験の停止(全群へ対照値)")
    p_stop.add_argument("--key", required=True)
    sub.add_parser("list", help="実験と群別割付数の一覧")
    args = parser.parse_args()

    async def run() -> dict:
        engine = create_db_engine(Settings())
        try:
            return await _run(engine, args)
        finally:
            await engine.dispose()

    print(json.dumps(asyncio.run(run()), ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
