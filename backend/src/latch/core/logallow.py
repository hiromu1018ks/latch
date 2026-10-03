"""構造化ログの許可リスト(M4 ws-5・08 §2.4第1・第3規定)。

出力できるフィールドを列挙型で固定したレコード型(既存2型)を登録表に
一元化し、不許可フィールド(raw_text・geo座標・constraints系)を列挙する。
新規に構造化ログを作る際はレコード型をここへ登録必須
(test_log_allowlist.pyの機械検査が強制する)。既存の
latch.llm.send・latch.push.sendは「許可リスト済み構造化ログ」として
無変更で登録する(設計§2.6)。printf型モジュールログは§2.4第3規定の
運用(例外・エラーはIDのみ)であり変換対象外。
"""

from __future__ import annotations

from pydantic import BaseModel

from latch.llm.records import SendRecord
from latch.notifications.records import PushSendRecord

# 単一真実: ロガー名 → レコード型(Pydanticモデルのフィールド固定が
# 許可リストそのもの)。G4証拠(許可リスト一覧)と問い合わせ開示資料の雛形
ALLOWED_STRUCTURED_LOGS: dict[str, type[BaseModel]] = {
    "latch.llm.send": SendRecord,  # 08 §3 送信記録(M0 ws-2)
    "latch.push.send": PushSendRecord,  # 10 §1 FR-21検証用(M3 ws-3)
}

# 08 §2.4「raw_text・geo座標・constraints系フィールドは不許可」の機械突合用列挙
FORBIDDEN_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "raw_text",
        "geo",
        "lat",
        "lon",
        "geo_center",
        "geo_radius_m",
        "constraints",
        "soft_constraints",
        "ng_conditions",
        "structured_data",
    }
)


def serialize_allowed(logger_name: str, record_dict: dict) -> BaseModel:
    """許可リスト外のキーを落としてから型へ渡す(新規ログ用の規律の入口)。

    08 §2.4「許可リスト外のフィールドはSerializerの段階で落とす」の実体。
    未登録ロガーはKeyError(登録必須を呼び出し側に強制する)。
    """
    model = ALLOWED_STRUCTURED_LOGS[logger_name]
    known = set(model.model_fields)
    return model.model_validate({k: v for k, v in record_dict.items() if k in known})
