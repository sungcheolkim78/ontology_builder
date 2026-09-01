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
MODEL_CATALOG = [
    "google/gemini-3.7-flash",
    "openai/gpt-5.5",
    "openai/gpt-5.4-mini",
    "anthropic/claude-opus-5",
    "anthropic/claude-sonnet-5",
    "z-ai/glm-5.3",
    "z-ai/glm-5.2",
    "deepseek/deepseek-v4-pro",
    "deepseek/deepseek-v4-flash",
]

# 1M matches modern frontier models' default context window, so long schema/extraction
# outputs aren't truncated by a small API-side default max_tokens.
MAX_TOKENS = 1_000_000

# Model picked at runtime from the settings UI; None falls back to OPENROUTER_MODEL.
_selected_model: str | None = None


def get_model_name():
    return _selected_model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)


def set_model_name(model: str | None) -> None:
    global _selected_model
    _selected_model = model


def get_chat_model():
    return ChatOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        model=get_model_name(),
        max_tokens=MAX_TOKENS,
    )


def to_langchain_messages(messages):
    return [ROLE_TO_MESSAGE[m["role"]](content=m["content"]) for m in messages]
