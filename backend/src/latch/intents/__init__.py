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
    ForbiddenError,
    GeocodingFailedError,
    IntentNotFoundError,
    IntentsError,
    IntentValidationError,
    InvalidTransitionError,
    LLMUnavailableError,
    UnderAgeError,
    UnstructurableError,
)
from latch.intents.prompt import PARSER_SYSTEM_PROMPT, format_parser_system_prompt
from latch.intents.routes import parse_router
from latch.intents.schema import (
    WARNING_MESSAGE_NG_DOWNGRADED,
    ParserBudget,
    ParserCategory,
    ParserLocation,
    ParserOutput,
    ParserParticipants,
    ParserTime,
)
from latch.intents.service import (
    IntentParseService,
    ParseResult,
    ParseWarning,
    SupportsParseIntent,
    make_intent_parse_service,
)

__all__ = [
    "DEFAULT_PARTICIPANTS",
    "DEFAULT_RADIUS_M",
    "DependencyUnavailableError",
    "ForbiddenError",
    "GeocodingFailedError",
    "IntentNotFoundError",
    "IntentParseService",
    "IntentValidationError",
    "IntentsError",
    "InvalidTransitionError",
    "LLMUnavailableError",
    "PARSER_SYSTEM_PROMPT",
    "ParseResult",
    "ParseWarning",
    "ParserBudget",
    "ParserCategory",
    "ParserLocation",
    "ParserOutput",
    "ParserParticipants",
    "ParserTime",
    "SupportsParseIntent",
    "UnderAgeError",
    "UnstructurableError",
    "WARNING_MESSAGE_NG_DOWNGRADED",
    "default_time_end",
    "expires_at_candidates",
    "format_parser_system_prompt",
    "make_intent_parse_service",
    "nearest_expires_at",
    "parse_router",
]
