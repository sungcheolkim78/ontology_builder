import os

from langchain_openai import OpenAIEmbeddings

DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"

# Must match this model's actual output size -- LadybugDB node tables
# declare a fixed-width FLOAT[EMBEDDING_DIM] column (see graphdb.py), so
# switching to a model with a different dimension requires re-extracting
# every document (the column width can't be changed after the fact).
EMBEDDING_DIM = 1536


def get_embedding_model_name() -> str:
    return os.environ.get("OPENROUTER_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


def get_embedding_model() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        model=get_embedding_model_name(),
    )


def node_embedding_text(node: dict) -> str:
    """The text embedded for a node -- label plus detail (when present),
    the same fields _build_context_text puts in front of the LLM, so
    similarity search matches against what the model actually sees."""
    if node.get("detail"):
        return f"{node['label']}: {node['detail']}"
    return node["label"]
