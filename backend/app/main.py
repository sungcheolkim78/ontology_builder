import os
from pathlib import Path

import anydoc
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app import graphdb
from app.chat import get_chat_model, get_model_name, to_langchain_messages
from app.graphrag import search_graph
from app.ontology import (
    DEFAULT_SCHEMA,
    activate_version,
    create_schema_version,
    delete_version,
    embed_graph,
    extract_graph,
    generate_schema,
    get_active_version,
    list_schema_stems,
    list_versions,
    load_document_manifest,
    load_graph,
    load_schema,
    save_document_manifest,
    save_graph,
    validate_ontology,
)
from app.parser import DATA_DIR, parse_to_markdown_file
from app.telemetry import configure_telemetry, invoke_with_telemetry

configure_telemetry()

app = FastAPI()

# Comma-separated so a production deploy (e.g. Render, where frontend and
# backend are separate services on different origins) can list its actual
# frontend URL without touching code -- defaults to the local podman-compose
# frontend origin.
_cors_origins = [
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from FastAPI"}


@app.get("/api/config")
def get_config():
    return {"model": get_model_name()}


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    filename: str | None = None
    hops: int = 1


@app.post("/api/chat")
def chat(request: ChatRequest):
    messages = [m.model_dump() for m in request.messages]

    if request.filename and messages:
        stem = _stem(request.filename)
        version = get_active_version(stem)
        schema = load_schema(stem, version) if version is not None else None
        if schema and graphdb.has_graph(stem, version=version):
            hops = max(1, min(5, request.hops))
            try:
                result = search_graph(
                    messages[-1]["content"], schema, stem, version=version, hops=hops
                )
            except ValueError:
                result = None

            if result is not None:
                if result["context"]:
                    augmented = [
                        {
                            "role": "system",
                            "content": f"다음은 문서에서 추출된 관련 정보입니다:\n{result['context']}",
                        }
                    ] + messages
                    model = get_chat_model()
                    response = invoke_with_telemetry(
                        "chat.answer", model, to_langchain_messages(augmented)
                    )
                    content = response.content
                else:
                    content = "관련된 내용을 찾을 수 없습니다."
                return {
                    "role": "assistant",
                    "content": content,
                    "node_types": result["node_types"],
                    "edge_types": result["edge_types"],
                    "related_nodes": result["related_nodes"],
                    "related_edges": result["related_edges"],
                }

    model = get_chat_model()
    lc_messages = to_langchain_messages(messages)
    response = invoke_with_telemetry("chat.answer", model, lc_messages)
    return {"role": "assistant", "content": response.content}


@app.post("/api/parse")
async def parse(file: UploadFile = File(...)):
    data = await file.read()
    try:
        result = parse_to_markdown_file(file.filename, data)
    except (anydoc.ConvertError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    save_document_manifest(_stem(result["filename"]), file.filename)
    return result


@app.get("/api/files")
def list_files():
    if not DATA_DIR.is_dir():
        return {"files": []}
    paths = sorted(DATA_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    return {
        "files": [
            {"filename": p.name}
            for p in paths
            if p.is_file() and not p.name.startswith(".")
        ]
    }


@app.get("/api/files/{filename}", response_class=PlainTextResponse)
def get_file(filename: str):
    safe_path = DATA_DIR / os.path.basename(filename)
    if not safe_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return safe_path.read_text()


@app.get("/api/documents")
def list_documents():
    if not DATA_DIR.is_dir():
        return {"documents": []}
    paths = sorted(DATA_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    documents = []
    for p in paths:
        if not p.is_file() or p.name.startswith("."):
            continue
        stem = p.stem
        manifest = load_document_manifest(stem)
        active_version = get_active_version(stem)
        documents.append(
            {
                "filename": p.name,
                "original_filename": (manifest or {}).get("original_filename", p.name),
                "has_schema": active_version is not None,
                "has_graph": active_version is not None
                and graphdb.has_graph(stem, version=active_version),
                "graphdb_name": graphdb.DB_PATH.name,
            }
        )
    return {"documents": documents}


def _document_path(filename: str) -> Path:
    return DATA_DIR / os.path.basename(filename)


def _stem(filename: str) -> str:
    return Path(os.path.basename(filename)).stem


@app.get("/api/ontology/schemas")
def list_schemas():
    return {"schemas": [{"stem": stem} for stem in list_schema_stems()]}


@app.post("/api/ontology/reset-database")
def reset_database():
    graphdb.reset_database()
    return {"status": "ok"}


class CreateSchemaRequest(BaseModel):
    document_type: str = "general"
    max_chars: int | None = None


@app.post("/api/ontology/{filename}/schema")
def create_schema(filename: str, request: CreateSchemaRequest | None = None):
    doc_path = _document_path(filename)
    if not doc_path.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    document_type = request.document_type if request else "general"
    max_chars = request.max_chars if request else None
    try:
        schema = generate_schema(
            doc_path.read_text(), document_type=document_type, max_chars=max_chars
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    version = create_schema_version(_stem(filename), schema, document_type=document_type)
    return {**schema, "version": version}


class UseSchemaRequest(BaseModel):
    source_stem: str


@app.post("/api/ontology/{filename}/schema/use")
def use_schema(filename: str, request: UseSchemaRequest):
    source_version = get_active_version(request.source_stem)
    if source_version is None:
        raise HTTPException(status_code=404, detail="source schema not found")
    schema = load_schema(request.source_stem, source_version)
    source_document_type = next(
        (
            v["document_type"]
            for v in list_versions(request.source_stem)
            if v["version"] == source_version
        ),
        "general",
    )
    version = create_schema_version(_stem(filename), schema, document_type=source_document_type)
    return {**schema, "version": version}


@app.get("/api/ontology/{filename}/schema")
def get_schema(filename: str):
    stem = _stem(filename)
    version = get_active_version(stem)
    if version is None:
        raise HTTPException(status_code=404, detail="schema not found")
    return load_schema(stem, version)


@app.post("/api/ontology/{filename}/extract")
def create_extraction(filename: str):
    doc_path = _document_path(filename)
    if not doc_path.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    stem = _stem(filename)
    version = get_active_version(stem)
    if version is None:
        version = create_schema_version(stem, DEFAULT_SCHEMA, document_type="default")
    schema = load_schema(stem, version)
    try:
        graph = extract_graph(doc_path.read_text(), schema)
        save_graph(stem, graph, version=version)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return graph


@app.post("/api/ontology/{filename}/embed")
def create_embeddings(filename: str):
    stem = _stem(filename)
    version = get_active_version(stem)
    if version is None or not graphdb.has_graph(stem, version=version):
        raise HTTPException(status_code=404, detail="ontology not extracted yet")
    embedded = embed_graph(stem, version=version)
    return {"embedded": embedded}


class ValidateRequest(BaseModel):
    max_chars: int | None = None


@app.post("/api/ontology/{filename}/validate")
def validate(filename: str, request: ValidateRequest | None = None):
    doc_path = _document_path(filename)
    if not doc_path.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    stem = _stem(filename)
    version = get_active_version(stem)
    if version is None:
        raise HTTPException(status_code=404, detail="schema not found")
    schema = load_schema(stem, version)
    graph = load_graph(stem, version=version)
    if graph is None:
        raise HTTPException(status_code=404, detail="ontology not extracted yet")
    max_chars = request.max_chars if request else None
    try:
        report = validate_ontology(doc_path.read_text(), schema, graph, max_chars=max_chars)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return report


@app.get("/api/ontology/{filename}")
def get_ontology(filename: str):
    stem = _stem(filename)
    version = get_active_version(stem)
    if version is None:
        raise HTTPException(status_code=404, detail="ontology not extracted yet")
    graph = load_graph(stem, version=version)
    if graph is None:
        raise HTTPException(status_code=404, detail="ontology not extracted yet")
    return graph


@app.get("/api/ontology/{filename}/schema/versions")
def get_schema_versions(filename: str):
    stem = _stem(filename)
    active = get_active_version(stem)
    return {
        "versions": [
            {
                **v,
                "is_active": v["version"] == active,
                "has_graph": graphdb.has_graph(stem, version=v["version"]),
            }
            for v in list_versions(stem)
        ]
    }


@app.post("/api/ontology/{filename}/schema/versions/{version}/activate")
def activate_schema_version(filename: str, version: int):
    try:
        activate_version(_stem(filename), version)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "ok"}


@app.delete("/api/ontology/{filename}/schema/versions/{version}")
def delete_schema_version(filename: str, version: int):
    try:
        delete_version(_stem(filename), version)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "ok"}
