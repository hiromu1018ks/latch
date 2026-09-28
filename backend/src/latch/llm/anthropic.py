"""Parser系統の実プロバイダ(Anthropic Claude API・Haiku 4.5。T1 v0.2・07 §1)。

design §2.1-A(公式SDK)・§2.2-A(structured outputs)・§2.5(SDK timeout=10秒・
max_retries=0)・§2.6(thinkingなし・max_tokens=1024。temperature=0はSDK 1.x
が引数を廃止したため省略 — 決定性はstructured outputsが保証)。
SDK例外はこの層で握らず素通り — Gatewayの既存wrap(LLMTimeoutError/
LLMProviderError)と送信記録が最終関門(design §2.5)。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from anthropic import AsyncAnthropic

from latch.llm.providers import ParserProvider

ANTHROPIC_PARSER_MODEL = "claude-haiku-4-5"  # T1 v0.2 Parser契約(2026-09-28確定)
# Gateway TIMEOUT_PARSER_Sと同値(design §2.5: SDK側で過剰に待つ時間を作らない)。
# 循環import回避のため値を自前定義し、同値性はunit試験が強制する
ANTHROPIC_PARSER_TIMEOUT_S = 10.0
ANTHROPIC_PARSER_MAX_TOKENS = 1024  # §2.6: 出力想定0.4k+マージンの打ち切り防御

# 後加工で保持しないキー。前半=pydanticメタデータ(出力保証に不要・スキーマを
# 小さく保つ)。後半=Anthropic structured outputsが対応しない文字列/数値制約
# (残すと実APIが400で拒否し得る — Review Focus #1。検証関門はサービス層の
# ParserOutput.model_validateが保持する)
_STRIP_KEYS = (
    "title",
    "description",
    "default",
    "$defs",
    "examples",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "multipleOf",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
)


def _resolve_refs(node: Any, defs: dict) -> Any:
    """$ref(#/$defs/X)を定義内容へ再帰的にインライン展開する(design §2.2)。"""
    if isinstance(node, dict):
        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            return _resolve_refs(defs[name], defs)
        return {
            key: _resolve_refs(value, defs)
            for key, value in node.items()
            if key not in _STRIP_KEYS
        }
    if isinstance(node, list):
        return [_resolve_refs(item, defs) for item in node]
    return node


def _normalize_objects(node: Any) -> None:
    """object型ノードすべてでrequired=全プロパティ・additionalProperties=falseへ。"""
    if isinstance(node, dict):
        properties = node.get("properties")
        if node.get("type") == "object" and isinstance(properties, dict):
            node["required"] = sorted(properties)
            node["additionalProperties"] = False
        for value in node.values():
            _normalize_objects(value)
    elif isinstance(node, list):
        for item in node:
            _normalize_objects(item)


def adapt_schema_for_anthropic(schema: dict) -> dict:
    """pydantic生成JSON Schemaをstructured outputs用へ機械的に後加工(design §2.2)。

    ParserOutput.model_json_schema()を単一の真実とし続けるため、手書き複製
    ではなく決定的導出のみを行う: $defs/$ref解決・メタデータ除去・required
    埋め・additionalProperties=false(Anthropic側の制約)。最終検証関門は
    サービス層のParserOutput.model_validateのまま(ずれは422として表面化)。
    """
    adapted = _resolve_refs(schema, schema.get("$defs", {}))
    _normalize_objects(adapted)
    return adapted


class AnthropicParserProvider(ParserProvider):
    """Parser系統の実プロバイダ(07 §1・T1 v0.2)。name="anthropic"(08 §3送信記録)。"""

    def __init__(
        self,
        *,
        api_key: str,
        system_prompt: str,
        output_schema: dict,
        client: AsyncAnthropic | None = None,
    ) -> None:
        if not api_key:
            # fail-fast: 鍵の不在を静かに握りつぶさない(design §2.4)
            raise ValueError("AnthropicParserProvider requires api_key")
        self.name = "anthropic"
        self._system_prompt = system_prompt
        self._schema = adapt_schema_for_anthropic(output_schema)
        self._client = (
            client
            if client is not None
            else AsyncAnthropic(
                api_key=api_key,
                timeout=ANTHROPIC_PARSER_TIMEOUT_S,
                max_retries=0,
            )
        )

    async def complete_structured(self, text: str, current_date: date) -> dict:
        """07 §2。出力JSONはAPI側が保証(§2.2) — 最初のtextブロックがvalid JSON。"""
        response = await self._client.messages.create(
            model=ANTHROPIC_PARSER_MODEL,
            system=self._system_prompt.replace(
                "{current_date}", current_date.isoformat()
            ),
            messages=[{"role": "user", "content": text}],
            output_config={"format": {"type": "json_schema", "schema": self._schema}},
            # design §2.6のtemperature=0はSDK 1.xがcreate()から引数を廃止
            # (渡すとTypeError)したため省略 — 出力の決定性はstructured
            # outputs(output_config.format)がAPI側で保証する
            max_tokens=ANTHROPIC_PARSER_MAX_TOKENS,
        )
        # structured outputsの保証: 最初のtextブロックはvalid JSON(design §2.2)。
        # この層では再検証しない — 検証関門はサービス層ParserOutput.model_validate
        first_text = next(
            block.text for block in response.content if block.type == "text"
        )
        return json.loads(first_text)
