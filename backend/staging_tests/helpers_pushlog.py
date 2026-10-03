"""pushドライラン記録の読取(M4 ws-3・引用確定値#16)。

push_mode=stubはロガー'latch.push.send'へJSON 1行で記録され、取得経路は
コンテナログのみ(notifications/records.py)。docker compose logsは
api1/api2/worker1/worker2の行を混ぜて出すため、行中で最初の`{`から行末を
json.loadsで試み・PushSendRecordの形(notification_type+title+bodyを持つ
dict)のみを採る。構造化ログの他行(latch.llm.send等)はnotification_typeを
持たないため自然に除外される。
"""

from __future__ import annotations

import json
import subprocess

from conftest import REPO_ROOT


def push_log_records(*, tail: int = 5000) -> list[dict]:
    """latch-stagingのコンテナログからpush記録(dict)のみを抽出する。"""
    out = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "compose.staging.yaml",
            "logs",
            "--tail",
            str(tail),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    records: list[dict] = []
    for line in out.splitlines():
        idx = line.find("{")
        if idx < 0:
            continue
        try:
            obj = json.loads(line[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and {"notification_type", "user_id", "body"} <= set(
            obj
        ):
            records.append(obj)
    return records
