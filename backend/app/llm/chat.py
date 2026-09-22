import os

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

ROLE_TO_MESSAGE = {
    "system": SystemMessage,
    "user": HumanMessage,
    "assistant": AIMessage,
}


DEFAULT_MODEL = "z-ai/glm-5.3-flash"

# Curated via OpenRouter's /api/v1/models listing (verified 2026-09): one
# Google, two each of OpenAI / Anthropic / DeepSeek, three GLM (including the
# flash variant used as DEFAULT_MODEL), all large-context. max_tokens is each
# model's true max_completion_tokens from the same listing -- providers
# reject a requested max_tokens above this per-model cap.
MODEL_CATALOG = [
    {"id": "google/gemini-3.7-flash", "max_tokens": 65_536},
    {"id": "openai/gpt-5.5", "max_tokens": 128_000},
    {"id": "openai/gpt-5.4-mini", "max_tokens": 128_000},
    {"id": "anthropic/claude-opus-5", "max_tokens": 128_000},
    {"id": "anthropic/claude-sonnet-5", "max_tokens": 128_000},
    {"id": "z-ai/glm-5.3-flash", "max_tokens": 131_072},
    {"id": "z-ai/glm-5.3", "max_tokens": 131_072},
    {"id": "z-ai/glm-5.2", "max_tokens": 262_144},
    {"id": "deepseek/deepseek-v4-pro", "max_tokens": 393_216},
    {"id": "deepseek/deepseek-v4-flash", "max_tokens": 384_000},
]

# Ontology pipeline steps that can each run on their own model, distinct from
# every other LLM call (chat answers, golden QA generation, summarization,
# evolution proposals, ...) which all share the "default" bucket below.
OPERATION_KEYS = ("discover_ontology", "generate_schema", "extract_graph", "validate_ontology")

# Consolidation calls (generate_schema.py's _consolidate_types/
# _consolidate_schema_types, the reduce step of discover_ontology_from_chunks/
# generate_schema_from_chunks) used to reuse "discover_ontology"/
# "generate_schema" as their own operation key outright. They now get their
# own key each, purely so OPERATION_MAX_TOKENS can give them a much larger
# ceiling than a single group's own call needs (see that dict's own
# comment) -- _MODEL_SELECTION_ALIAS below is what keeps this from also
# silently changing *which model* a consolidation call uses: without it, an
# operation key nobody ever explicitly selects a model for in the settings
# UI would just fall through to the unrelated "default" bucket instead of
# following whatever a person picked for "discover_ontology"/
# "generate_schema" -- the behavior before this split existed.
_MODEL_SELECTION_ALIAS = {
    "consolidate_discovery": "discover_ontology",
    "consolidate_schema": "generate_schema",
}

# Operations whose prompt always asks for a JSON object back (every
# app.ontology.generate_schema/extract_graph/evolve_graph call site, plus
# their reduce-step consolidation calls above) -- get_chat_model below adds
# response_format for exactly these, an allowlist rather than "everything
# except a known-prose set" specifically because several call sites the ONLY
# way to know is prose (main.py's /api/chat, app.graph.graphrag's
# analyze_question/answer_question, app.preprocess.goldenset's question/answer
# generation) all call get_chat_model() with operation=None too -- an
# allowlist keyed on these exact strings can never accidentally catch one of
# those.
#
# get_chat_model also asks for low reasoning effort on exactly these
# operations -- verified live against every model in MODEL_CATALOG plus two
# not-yet-cataloged ones (qwen/qwen3.8-flash, google/gemini-3.8-flash): a
# generate_schema()-shaped call spends the overwhelming majority of its
# response on hidden "reasoning" tokens before ever emitting the actual JSON
# (e.g. 2812 of 3063 output tokens for z-ai/glm-5.3-flash on a *17-character*
# document -- reasoning-token volume tracks how demanding the prompt's
# instructions are, not input size), and that reasoning generation, being
# sequential decoding, is the dominant cost of the 60s+ per-chunk-group
# latency this was chasing down, not chunk size or network overhead. Effort
# is NOT a uniform fix: it cut z-ai/glm-5.3-flash from ~60-90s to ~5-7s and
# google/gemini-3.8-flash's reasoning to 0 entirely, but qwen/qwen3.8-flash
# barely responds to it (137.9s -> 116.0s) -- some models' effort floor is
# just high regardless of this hint. Every model tested still accepts the
# parameter without erroring even when it has little effect, so this is
# applied unconditionally rather than per-model.
_JSON_OPERATIONS = frozenset(
    {
        "discover_ontology", "generate_schema", "extract_graph", "validate_ontology", "propose_evolution",
        "consolidate_discovery", "consolidate_schema",
    }
)

# Hard ceilings well below each model's own max_completion_tokens
# (MODEL_CATALOG above) for the ontology-pipeline operations whose prompts
# (app/llm/prompts.py) describe a realistically bounded output shape (a
# schema's own "~5-12 node_types" guidance, a validation report's handful of
# issues, ...) -- requesting a model's full ceiling on every call risks
# runaway generation on a malformed/looping response going undetected for as
# long as possible, for no benefit on the normal case (a well-formed
# response stops at its own natural end regardless of how high the ceiling
# is). Sized generously, as a safety ceiling rather than a token budget the
# model is expected to actually hit; get_model_max_tokens below takes
# whichever of this and the model's own cap is smaller, so this only ever
# tightens, never loosens, what a model would otherwise allow.
#
# consolidate_discovery/consolidate_schema get a much bigger share than a
# single group's own generate_schema/discover_ontology call (8_000/16_000)
# -- reproduced live, merging a real 12-group document's 274 candidate
# types (118 node_types + 156 edge_types) needed ~16,700 output tokens, of
# which ~6,700-8,000 were reasoning alone; 8_000 and even 16_000 both cut
# the response off mid-string before it produced any/complete JSON. 40_000
# leaves comfortable headroom above the ~16,700 actually observed, while
# staying under the smallest cataloged model's own ceiling (65_536, google/
# gemini-3.7-flash) so get_model_max_tokens's "smaller of the two" logic
# doesn't quietly reintroduce a lower cap for that one model.
OPERATION_MAX_TOKENS = {
    "discover_ontology": 16_000,
    "generate_schema": 8_000,
    "extract_graph": 16_000,
    "validate_ontology": 8_000,
    "propose_evolution": 8_000,
    "consolidate_discovery": 40_000,
    "consolidate_schema": 40_000,
}

# Models picked at runtime from the settings UI, keyed by operation (see
# OPERATION_KEYS) plus "default" for every operation not listed there.
# In-memory only, same as the single-model global this replaced -- a
# backend restart resets to OPENROUTER_MODEL/DEFAULT_MODEL, matching prior
# behavior.
_selected_models: dict[str, str] = {}


def get_model_name(operation: str | None = None) -> str:
    operation = _MODEL_SELECTION_ALIAS.get(operation, operation)
    if operation and operation in _selected_models:
        return _selected_models[operation]
    return _selected_models.get("default") or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)


def set_model_name(model: str | None, operation: str | None = None) -> None:
    key = operation or "default"
    if model is None:
        _selected_models.pop(key, None)
    else:
        _selected_models[key] = model


def get_model_max_tokens(operation: str | None = None) -> int | None:
    """Output-token cap for the active model, or None when uncataloged
    (custom OPENROUTER_MODEL) and `operation` has no entry in
    OPERATION_MAX_TOKENS either -- sending no cap there matches the provider
    default instead of guessing a limit that may be rejected. When both a
    model cap and an operation cap apply, the smaller of the two wins -- an
    operation's own cap is a tighter safety ceiling *within* whatever the
    model already allows, never an excuse to exceed it."""
    catalog = {m["id"]: m["max_tokens"] for m in MODEL_CATALOG}
    caps = [catalog.get(get_model_name(operation)), OPERATION_MAX_TOKENS.get(operation)]
    caps = [c for c in caps if c is not None]
    return min(caps) if caps else None


def get_chat_model(operation: str | None = None):
    kwargs = {}
    max_tokens = get_model_max_tokens(operation)
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if operation in _JSON_OPERATIONS:
        kwargs["model_kwargs"] = {"response_format": {"type": "json_object"}}
        # A first-class ChatOpenAI field (not another model_kwargs entry) --
        # passing it through model_kwargs instead works but logs a
        # "should be specified explicitly" warning on every call. Once this
        # is set, langchain-openai routes the request through OpenAI's
        # Responses API instead of Chat Completions, which returns
        # `response.content` as a list of content blocks (reasoning + text,
        # order not guaranteed) rather than a plain string -- see
        # app.ontology.utils.parse_json_response's own comment for the
        # normalization this requires downstream.
        kwargs["reasoning"] = {"effort": "low"}
    return ChatOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        model=get_model_name(operation),
        **kwargs,
    )


def to_langchain_messages(messages):
    return [ROLE_TO_MESSAGE[m["role"]](content=m["content"]) for m in messages]
