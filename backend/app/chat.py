import os

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

ROLE_TO_MESSAGE = {
    "system": SystemMessage,
    "user": HumanMessage,
    "assistant": AIMessage,
}


DEFAULT_MODEL = "openai/gpt-4o-mini"

# Curated via OpenRouter's /api/v1/models listing (verified 2026-09): one
# Google, two each of OpenAI / Anthropic / GLM / DeepSeek, all large-context.
# max_tokens is each model's true max_completion_tokens from the same listing --
# providers reject a requested max_tokens above this per-model cap.
MODEL_CATALOG = [
    {"id": "google/gemini-3.7-flash", "max_tokens": 65_536},
    {"id": "openai/gpt-5.5", "max_tokens": 128_000},
    {"id": "openai/gpt-5.4-mini", "max_tokens": 128_000},
    {"id": "anthropic/claude-opus-5", "max_tokens": 128_000},
    {"id": "anthropic/claude-sonnet-5", "max_tokens": 128_000},
    {"id": "z-ai/glm-5.3", "max_tokens": 131_072},
    {"id": "z-ai/glm-5.2", "max_tokens": 262_144},
    {"id": "deepseek/deepseek-v4-pro", "max_tokens": 393_216},
    {"id": "deepseek/deepseek-v4-flash", "max_tokens": 384_000},
]

# Model picked at runtime from the settings UI; None falls back to OPENROUTER_MODEL.
_selected_model: str | None = None


def get_model_name():
    return _selected_model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)


def set_model_name(model: str | None) -> None:
    global _selected_model
    _selected_model = model


def get_model_max_tokens() -> int | None:
    """Output-token cap for the active model, or None when uncataloged
    (custom OPENROUTER_MODEL) -- sending no cap there matches the provider
    default instead of guessing a limit that may be rejected."""
    catalog = {m["id"]: m["max_tokens"] for m in MODEL_CATALOG}
    return catalog.get(get_model_name())


def get_chat_model():
    kwargs = {}
    max_tokens = get_model_max_tokens()
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    return ChatOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        model=get_model_name(),
        **kwargs,
    )


def to_langchain_messages(messages):
    return [ROLE_TO_MESSAGE[m["role"]](content=m["content"]) for m in messages]
