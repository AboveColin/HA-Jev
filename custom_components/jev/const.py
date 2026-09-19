"""Constants for the Jev integration."""

from typing import Final

DOMAIN: Final = "jev"

CONF_QUESTIONS: Final = "questions"
CONF_STATE_TEMPLATE: Final = "state"
CONF_INSTRUCTIONS: Final = "instructions"
CONF_CRITERIA: Final = "criteria"
CONF_TRUE: Final = "true"
CONF_FALSE: Final = "false"
CONF_THRESHOLD: Final = "threshold"
CONF_TRIGGER_ENTITIES: Final = "trigger_entities"
CONF_DAILY_TOKEN_BUDGET: Final = "daily_token_budget"
CONF_PRICE_PER_MILLION: Final = "price_per_million"

TYPE_NOUL: Final = "noul"
TYPE_CHOICE: Final = "choice"
TYPE_SCORE: Final = "score"

ATTR_CONFIDENCE: Final = "confidence"
ATTR_PROBABILITIES: Final = "probabilities"
ATTR_LEGEND: Final = "legend"
ATTR_NEAREST_LEVEL: Final = "nearest_level"
ATTR_STATE_TEXT: Final = "evaluated_state"
ATTR_QUESTIONS: Final = "questions"
ATTR_ANSWERS: Final = "answers"
ATTR_USAGE: Final = "usage"
ATTR_LATENCY_MS: Final = "latency_ms"
ATTR_CONFIG_ENTRY: Final = "config_entry"

SERVICE_ASK: Final = "ask"

# A context is re-evaluated at most this often, whatever the triggers do. Each
# evaluation is a paid API call, so a flapping entity must not be able to spend
# money in a loop.
MIN_UPDATE_INTERVAL_SECONDS: Final = 30
DEFAULT_SCAN_INTERVAL_SECONDS: Final = 300
TRIGGER_DEBOUNCE_SECONDS: Final = 5.0

# Issue raised when the daily token budget stops evaluations.
ISSUE_BUDGET_EXCEEDED: Final = "daily_budget_exceeded"

# Usage totals are written this long after a change, so a burst of evaluations
# makes one write rather than one per call.
STORE_SAVE_DELAY_SECONDS: Final = 15
STORAGE_VERSION: Final = 1

SERVICE_NOUL: Final = "noul"
SERVICE_CHOICE: Final = "choice"
SERVICE_SCORE: Final = "score"

CONF_TRUE_MEANS: Final = "true_means"
CONF_FALSE_MEANS: Final = "false_means"
CONF_OPTIONS: Final = "options"
CONF_OPTION_DESCRIPTIONS: Final = "option_descriptions"
CONF_LEVELS: Final = "levels"

# A target can be an area or a whole device, so one picker click can pull in a lot.
# Measured 2026-09-17 against the live API: 1 entity cost 339 input tokens, 5 cost
# 559 and 10 cost 931, so an entity record is 65.8 tokens. This cap is therefore
# about 16,500 tokens per evaluation, or $0.0007 at the published price. It sits far
# past any question that means something, and stops someone pointing a one minute
# context at the whole house.
MAX_TARGET_ENTITIES: Final = 250

CONF_INCLUDE_ATTRIBUTES: Final = "include_attributes"
CONF_ENTITIES: Final = "entities"

CONF_BACKGROUND: Final = "background"

# --- Conversation agent ---

CONF_FALLBACK_AGENT: Final = "fallback_agent"
CONF_MIN_CONFIDENCE: Final = "min_confidence"
CONF_ALLOW_WHOLE_HOME: Final = "allow_whole_home"

# Below this, the router hands the sentence to the fallback agent rather than
# guessing. 0.6 is a starting point and not a calibrated figure: TypeSafe publishes
# no calibration evidence for confidence, so treat it as an ordering and measure it
# on your own phrasing before moving it.
DEFAULT_MIN_CONFIDENCE: Final = 0.6

# How many exposed entities one spoken command may describe.
#
# Measured locally on the payload this builds: an entity adds 114 bytes to the
# state and 76 bytes to the options, 190 in total, because it appears both as a
# reading and as something to choose between. Against the 65.8 input tokens per
# entity record measured on the live API for a state-only record, that scales to
# about 110 tokens per entity per command, so this cap is roughly 16,500 input
# tokens or $0.0007. It also stays under the 255 option ceiling a Choice question
# has. A house past it should narrow what is exposed to Assist, which is the list
# a voice assistant should have been given anyway.
MAX_CONVERSATION_ENTITIES: Final = 150

# How many routed sentences are kept for diagnostics. Enough to see a pattern in
# what is being misread, small enough that it cannot grow into a leak.
CONVERSATION_TRACE_LENGTH: Final = 20

# --- Questions configured in the UI ---

# One subentry is one question. The grouping into API calls is derived rather
# than declared: see subentry.py.
SUBENTRY_QUESTION: Final = "question"

CONF_TARGET: Final = "target"
CONF_LEVELS_TEXT: Final = "levels"
CONF_OPTIONS_TEXT: Final = "options"
