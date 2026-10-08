"""解析(parse)の再利用キーと集約・手動修正保護(計画 §5.5・I4・T7)。

規律(§5.5):
- Parser cacheのキーは認証主体・厳密なtext・JST基準日・timezone・
  prompt/schema/model/normalizer版。「明日」「今夜」はJST日跨ぎでhitさせない
- 最初に導入するのは同一実行内の並行集約のみ(非永続)。異なる実行間の
  重複を必ず防げるとは限らない旨を記録する
- 保存済みIntent編集時の再利用で、手動修正を無断で全置換しない
  (field_provenance で manual/parser を区別しmanual優先)
- raw_textが同じでも手動条件を変更した場合は条件を優先する
- 永続cacheは原文・構造化結果の両方に機微が残るため、保存契約の承認
  までは導入しない(本モジュールは非永続の範囲のみ)
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Any

PROVENANCE_MANUAL = "manual"
PROVENANCE_PARSER = "parser"


@dataclass(frozen=True)
class ParseVersions:
    prompt: str
    schema: str
    model: str
    normalizer: str


@dataclass(frozen=True)
class ParseKey:
    """同一解析の識別子(非永続集約に使う。秘密情報を含まない)。"""

    actor_scope: str  # 認証主体(ユーザーscope — 他人の結果を流用しない)
    text_fingerprint: str
    basis_date: date  # JST基準日(日跨ぎで別キー)
    timezone: str  # 'Asia/Tokyo'
    versions_fingerprint: str

    def cache_id(self) -> str:
        source = "|".join(
            [
                self.actor_scope,
                self.text_fingerprint,
                self.basis_date.isoformat(),
                self.timezone,
                self.versions_fingerprint,
            ]
        )
        return hashlib.sha256(source.encode()).hexdigest()


def build_parse_key(
    *,
    actor_scope: str,
    text: str,
    current_date: date,
    timezone_name: str = "Asia/Tokyo",
    versions: ParseVersions,
) -> ParseKey:
    """厳密なtext(正規化しない)とJST基準日を含む解析キー。"""
    text_fp = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    versions_fp = (
        "sha256:"
        + hashlib.sha256(
            "|".join(
                [versions.prompt, versions.schema, versions.model, versions.normalizer]
            ).encode()
        ).hexdigest()
    )
    return ParseKey(
        actor_scope=actor_scope,
        text_fingerprint=text_fp,
        basis_date=current_date,
        timezone=timezone_name,
        versions_fingerprint=versions_fp,
    )


@dataclass
class FieldProvenance:
    """フォームフィールドの由来(manual/parser)と元の解析世代。"""

    fields: dict[str, str] = field(default_factory=dict)

    def mark(self, name: str, provenance: str) -> None:
        if provenance not in (PROVENANCE_MANUAL, PROVENANCE_PARSER):
            raise ValueError(f"invalid provenance: {provenance}")
        self.fields[name] = provenance


def merge_reparse(
    *,
    current_values: dict[str, Any],
    new_parsed: dict[str, Any],
    provenance: FieldProvenance,
) -> dict[str, Any]:
    """再解析結果の反映規則: 手動修正(manual)は上書きしない。

    - manualのフィールドは現行値を優先(無断全置換しない)
    - parser由来のフィールドは新解析結果で置き換わる
    - raw_textが同じでも手動条件変更は条件側が勝つ(§5.5)
    """
    merged: dict[str, Any] = dict(new_parsed)
    for name, value in current_values.items():
        if provenance.fields.get(name) == PROVENANCE_MANUAL:
            merged[name] = value
    return merged


class ParseRequestAggregator:
    """同一実行内の並行解析集約(非永続・I4の先行範囲)。

    同じParseKeyの並行要求は1回のLLM呼出しに集約し、全員が同じ結果を
    受け取る。実行(isolate)をまたぐ集約はしない — 「保存しない」契約を
    守れる範囲はここまで(計画§5.5: 実行をまたぐ集約は別方式の比較と
    承認が必要)。
    """

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Future] = {}
        self.calls = 0  # 実LLM呼出し回数(試験が数える)

    async def parse(self, key: ParseKey, send) -> Any:
        """send は ParseKey→結果の_coroutine(実際のparse呼出し)。"""
        ck = key.cache_id()
        existing = self._inflight.get(ck)
        if existing is not None:
            return await asyncio.shield(existing)
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._inflight[ck] = fut
        try:
            self.calls += 1
            result = await send(key)
            if not fut.done():
                fut.set_result(result)
            return result
        except BaseException as exc:  # noqa: BLE001 — 集約失敗は全員へ伝播
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(ck, None)
