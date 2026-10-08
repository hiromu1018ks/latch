"""厳密な評価キーの構築(計画 §5.3・I2・T6)。

「意味が似ている」入力を流用しない。同じペアIDと、実際にJevへ送る
厳密な入力のfingerprintが一致した時だけcacheを使う。

fingerprint に含める版(§5.3の全項目):
- 入力のA/B方向(sent_inputs の並び)
- 入力を整える規則(normalizer)の版
- 質問(jev questions)の版
- promptの版
- 応答形式(schema)の版
- provider/model(要求側)
- 判定方針(policy)の版
- 使った時刻文脈・外部事実の版(context_version)

Intentのversionは結果の適用先が最新か確認するために使い、キーの一意性に
含めない(resume・表示設定変更で版が進んでも、実際の評価入力が同じなら
数値結果を再利用できる)。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from latch.domain.types import EvaluationKey


@dataclass(frozen=True)
class EvaluationVersions:
    """fingerprint構成要素の版一式(§5.3)。"""

    normalizer: str
    questions: str
    prompt: str
    schema: str
    provider: str
    model: str
    policy: str


def build_evaluation_key(
    *,
    pair: tuple[str, str],
    sent_inputs: tuple[str, str],
    versions: EvaluationVersions,
    context_version: str = "",
) -> EvaluationKey:
    """厳密な評価キーを作る。

    - pair は呼び出し側で正規順序(a<b)にすること(候補一意性と同一規律)。
      sent_inputs は pair に対応する並び(A/B方向は区別する)。
    - Intent版は含めない(適用先確認に使う・キーには入れない)。
    """
    a_id, b_id = pair
    fingerprint_source = "|".join(
        [
            "v1",  # fingerprint方式の版(方式変更で全missにする)
            sent_inputs[0],
            sent_inputs[1],
            versions.normalizer,
            versions.questions,
            versions.prompt,
            versions.schema,
            versions.provider,
            versions.model,
            versions.policy,
        ]
    )
    fingerprint = "sha256:" + hashlib.sha256(fingerprint_source.encode()).hexdigest()
    pair_key = f"jev:{a_id}:{b_id}"
    return EvaluationKey(
        kind="jev",
        pair_key=pair_key,
        fingerprint=fingerprint,
        context_version=context_version,
    )


def cache_key_of(key: EvaluationKey) -> str:
    """evaluation_cache の主キー(キー全体のhash・pair+fingerprint+文脈)。"""
    source = "|".join([key.pair_key, key.fingerprint, key.context_version])
    return hashlib.sha256(source.encode()).hexdigest()
