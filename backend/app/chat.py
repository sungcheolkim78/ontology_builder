import os

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

ROLE_TO_MESSAGE = {
    "system": SystemMessage,
    "user": HumanMessage,
    "assistant": AIMessage,
}


DEFAULT_MODEL = "openai/gpt-4o-mini"

# 1M matches modern frontier models' default context window, so long schema/extraction
# outputs aren't truncated by a small API-side default max_tokens.
MAX_TOKENS = 1_000_000


def get_model_name():
    return os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)


def get_chat_model():
    return ChatOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        model=get_model_name(),
        max_tokens=MAX_TOKENS,
    )


def to_langchain_messages(messages):
    return [ROLE_TO_MESSAGE[m["role"]](content=m["content"]) for m in messages]
