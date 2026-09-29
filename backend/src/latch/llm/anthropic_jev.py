"""フォールバックLLMプロバイダ(Anthropic Sonnet 5。07 §4切替節・design §1.3 A1〜A4)。

第一候補(TypeSafe Jev)の429/529/timeout/接続障害で切替される先。第一候補と
同じstate・7質問にキー同一・noul→[0,1]・score→[0,4]のJSONをstructured output
で返す。sampling系(temperature/top_p/top_k)はAnthropic 5系で廃止され送信すると
400のため送らない(A2)。max_retriesは既定2→0へ(A4: 再試行なし)。
design §5-8(max_tokens等)は実装定義初期値の先送り記録あり。
スキーマはFallbackJevAnswers.model_json_schema()を単一の真実とし、
anthropic.pyのadapt_schema_for_anthropicをimportして流用する(手書き複製なし)。
SDK例外はこの層で握らず素通り — Gatewayの既存wrapと送信記録が最終関門。
"""

from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from latch.llm.anthropic import ANTHROPIC_PARSER_BASE_URL, adapt_schema_for_anthropic
from latch.llm.providers import JevProvider

ANTHROPIC_JEV_FALLBACK_MODEL = "claude-sonnet-5"
# Gateway TIMEOUT_JEV_Sと同値(unit試験が同値性を強制・循環import回避のため
# 自前定義 — anthropic.pyのANTHROPIC_PARSER_TIMEOUT_Sと同一判断)
ANTHROPIC_JEV_FALLBACK_TIMEOUT_S = 6.0
ANTHROPIC_JEV_FALLBACK_MAX_TOKENS = 512  # design §5-8(実装定義初期値)
ANTHROPIC_JEV_BASE_URL = ANTHROPIC_PARSER_BASE_URL

FALLBACK_SYSTEM_PROMPT = (
    "あなたは2つのIntentペアの相互受け入れ可能性を評価する判定器です。"
    "入力される2つのIntent(正規化テキスト)に対し、次の7つの質問に答えるJSONのみを"
    "出力します。JSON以外の文章・説明・根拠文は一切出力しません。\n"
    "\n"
    "質問と値域:\n"
    "- would_a_accept_b: Intent Aの作成者の立場でBとの成立にyesと答える確率"
    "(0〜1の数値)\n"
    "- would_b_accept_a: Intent Bの作成者の立場でAとの成立にyesと答える確率"
    "(0〜1の数値)\n"
    "- purpose_fit: 目的・カテゴリの適合度(0〜4の数値)\n"
    "- mood_fit: 雰囲気・軽さの適合度(0〜4の数値)\n"
    "- timing_fit: 時間帯・所要時間の適合度(0〜4の数値)\n"
    "- social_fit: 人数・社会的文脈の適合度(0〜4の数値)\n"
    "- latent_yes: どちらかが明示していないが、そのIntentの記述の範囲内でYESになり得る"
    "可能性(0〜1の数値)\n"
    "\n"
    "判定規則:\n"
    "- would_*は相手側の条件も考慮し、相手の[hard]条件を満たさない場合、または相手の"
    "[soft]条件・(システムで判定不能)と付いた条件に触れる場合はyesから遠ざけます\n"
    "- [hard]条件との意味的矛盾(予算感の著しい乖離が食事内容を成立させない等)は"
    "yesから遠ざけます\n"
    "- 根拠の説明は出力しません(値のみ)\n"
)

_NOUL_KEYS = ("would_a_accept_b", "would_b_accept_a", "latent_yes")
_SCORE_KEYS = ("purpose_fit", "mood_fit", "timing_fit", "social_fit")


class FallbackJevAnswers(BaseModel):
    """フォールバック応答のスキーマ(7キー全てnumber)。

    min/max等の数値制約を付けない — A3(anthropic.pyの_STRIP_KEYS知見:
    Anthropic structured outputsが数値制約を拒否する)。値域検証は
    validate_and_normalize(検証失敗=切替条件外の縮退)が担う。
    """

    would_a_accept_b: float
    would_b_accept_a: float
    purpose_fit: float
    mood_fit: float
    timing_fit: float
    social_fit: float
    latent_yes: float


class AnthropicJevFallbackProvider(JevProvider):
    """フォールバックLLM(07 §4)。name="anthropic"(08 §3送信記録・§9-1)。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = ANTHROPIC_JEV_BASE_URL,
        client: AsyncAnthropic | None = None,
    ) -> None:
        if not api_key:
            # fail-fast: 鍵の不在を静かに握りつぶさない(anthropic.pyと同一)
            raise ValueError("AnthropicJevFallbackProvider requires api_key")
        self.name = "anthropic"
        self._schema = adapt_schema_for_anthropic(
            FallbackJevAnswers.model_json_schema()
        )
        self._client = (
            client
            if client is not None
            else AsyncAnthropic(
                api_key=api_key,
                base_url=base_url,
                timeout=ANTHROPIC_JEV_FALLBACK_TIMEOUT_S,
                max_retries=0,
            )
        )

    async def judge(self, intent_a: str, intent_b: str) -> dict:
        response = await self._client.messages.create(
            model=ANTHROPIC_JEV_FALLBACK_MODEL,
            system=FALLBACK_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"{intent_a}\n\n{intent_b}"}],
            output_config={"format": {"type": "json_schema", "schema": self._schema}},
            # sampling系(temperature/top_p/top_k)は廃止のため送らない(A2: 400)
            thinking={"type": "disabled"},
            max_tokens=ANTHROPIC_JEV_FALLBACK_MAX_TOKENS,
        )
        first_text = next(
            block.text for block in response.content if block.type == "text"
        )
        data = json.loads(first_text)
        return {
            "model": None,  # 引用#8: フォールバック時のmodelはnull
            "answers": self._answers(data),
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        }

    @staticmethod
    def _answers(data: Any) -> dict:
        """JSON→System One answers形式(キー同一・noul→[0,1]・score→[0,4])。"""
        answers = {key: {"type": "noul", "noul": data[key]} for key in _NOUL_KEYS}
        answers.update(
            {
                key: {"type": "score", "score": data[key], "confidence": None}
                for key in _SCORE_KEYS
            }
        )
        return answers
