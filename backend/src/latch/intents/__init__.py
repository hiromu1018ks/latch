"""Intentドメイン(M1 ws-2)。Parser本体・補完規則・parse API。"""

from latch.intents.completion import (
    DEFAULT_PARTICIPANTS,
    DEFAULT_RADIUS_M,
    default_time_end,
    expires_at_candidates,
    nearest_expires_at,
)
from latch.intents.errors import (
    DependencyUnavailableError,
    IntentsError,
    LLMUnavailableError,
    UnstructurableError,
)
from latch.intents.prompt import PARSER_SYSTEM_PROMPT, format_parser_system_prompt
from latch.intents.schema import (
    WARNING_MESSAGE_NG_DOWNGRADED,
    ParserBudget,
    ParserCategory,
    ParserLocation,
    ParserOutput,
    ParserParticipants,
    ParserTime,
)

__all__ = [
    "DEFAULT_PARTICIPANTS",
    "DEFAULT_RADIUS_M",
    "DependencyUnavailableError",
    "IntentsError",
    "LLMUnavailableError",
    "PARSER_SYSTEM_PROMPT",
    "ParserBudget",
    "ParserCategory",
    "ParserLocation",
    "ParserOutput",
    "ParserParticipants",
    "ParserTime",
    "UnstructurableError",
    "WARNING_MESSAGE_NG_DOWNGRADED",
    "default_time_end",
    "expires_at_candidates",
    "format_parser_system_prompt",
    "nearest_expires_at",
]
