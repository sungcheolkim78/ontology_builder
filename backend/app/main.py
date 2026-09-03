import json
import os
from pathlib import Path

import anydoc
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from app import graphdb
from app.auth import APP_PASSWORD, is_valid_token, issue_token
from app.chat import (
    MODEL_CATALOG,
    get_chat_model,
    get_model_max_tokens,
    get_model_name,
    set_model_name,
    to_langchain_messages,
)
from app.graphrag import search_graph
from app.ontology import (
    DEFAULT_SCHEMA,
    activate_version,
    apply_domain_schema_changes,
    apply_evolution,
    converge_domain_schema,
    create_schema_version,
    delete_version,
    discover_ontology,
    domain_calibration_stems,
    domain_convergence_history,
    embed_graph,
    evaluate_domain_schema,
    extract_graph,
    find_redundant_type_pairs,
    generate_schema,
    get_active_version,
    list_domains,
    list_schema_stems,
    list_versions,
    load_discovery,
    load_document_manifest,
    load_document_summary,
    load_domain_pending_review,
    load_domain_schema,
    load_graph,
    load_schema,
    measure_schema_stability,
    propose_evolution,
    run_domain_convergence,
    save_discovery,
    save_document_manifest,
    save_document_summary,
    save_graph,
    summarize_document,
    use_domain_schema,
    validate_ontology,
)
from app.chunking import chunk_markdown_file, convert_pdf_to_markdown_file
from app.parser import parse_to_markdown_file
from app.paths import document_dir_for, documents_dir
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

# Only active when APP_PASSWORD is set (e.g. the Render deploy) -- unset
# locally and in every test, so this is a no-op there.
_UNAUTHENTICATED_PATHS = {"/health", "/api/login", "/api/config"}


@app.middleware("http")
async def require_auth(request, call_next):
    # CORS preflight requests never carry the app's own Authorization header
    # (browsers don't attach custom headers to them), and rejecting them here
    # breaks every real cross-origin request behind it -- confirmed in
    # production as OPTIONS /api/parse returning 401 the moment APP_PASSWORD
    # was set, which silently killed the browser's actual POST before it was
    # ever sent. OPTIONS always passes through untouched.
    if request.method != "OPTIONS" and APP_PASSWORD and request.url.path not in _UNAUTHENTICATED_PATHS:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not is_valid_token(token):
            return JSONResponse(status_code=401, content={"detail": "unauthorized"})
    return await call_next(request)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from FastAPI"}


@app.get("/api/config")
def get_config():
    return {
        "model": get_model_name(),
        "models": MODEL_CATALOG,
        "max_tokens": get_model_max_tokens(),
        "auth_required": bool(APP_PASSWORD),
    }


class SetModelRequest(BaseModel):
    model: str


@app.post("/api/config/model")
def set_model(request: SetModelRequest):
    if request.model not in {m["id"] for m in MODEL_CATALOG}:
        raise HTTPException(status_code=400, detail="unknown model")
    set_model_name(request.model)
    return {"model": get_model_name()}


class LoginRequest(BaseModel):
    password: str


@app.post("/api/login")
def login(request: LoginRequest):
    token = issue_token(request.password)
    if token is None:
        raise HTTPException(status_code=401, detail="invalid password")
    return {"token": token}


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
async def parse(file: UploadFile = File(...), converter: str = Form("anydoc")):
    data = await file.read()
    ext = Path(os.path.basename(file.filename)).suffix.lstrip(".").lower()
    # "table_aware" only applies to actual PDFs -- pdfplumber can't parse
    # anything else, so any other extension always falls back to anydoc
    # regardless of what the uploader picked.
    use_table_aware = converter == "table_aware" and ext == "pdf"
    try:
        if use_table_aware:
            result = convert_pdf_to_markdown_file(file.filename, data)
        else:
            result = parse_to_markdown_file(file.filename, data)
    except (anydoc.ConvertError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"PDF 변환 실패: {e}")
    save_document_manifest(
        _stem(result["filename"]), file.filename, converter="table_aware" if use_table_aware else "anydoc"
    )
    return result


def _document_raw_files() -> list[tuple[str, Path]]:
    """(stem, raw.md path) for every registered document, newest first --
    the single place that knows a document is "a folder under documents_dir()
    with a raw.md in it", so /api/files and /api/documents can't drift apart
    on what counts as a document."""
    if not documents_dir().is_dir():
        return []
    entries = [
        (d.name, d / "raw.md")
        for d in documents_dir().iterdir()
        if d.is_dir() and not d.name.startswith(".") and (d / "raw.md").is_file()
    ]
    return sorted(entries, key=lambda entry: entry[1].stat().st_mtime, reverse=True)


@app.get("/api/files")
def list_files():
    return {
        "files": [{"filename": f"{stem}.md"} for stem, _ in _document_raw_files()]
    }


@app.get("/api/files/{filename}", response_class=PlainTextResponse)
def get_file(filename: str):
    safe_path = _document_path(filename)
    if not safe_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return safe_path.read_text()


@app.get("/api/documents")
def list_documents():
    documents = []
    for stem, raw_path in _document_raw_files():
        manifest = load_document_manifest(stem)
        active_version = get_active_version(stem)
        stat = raw_path.stat()
        documents.append(
            {
                "filename": f"{stem}.md",
                "original_filename": (manifest or {}).get("original_filename", f"{stem}.md"),
                "converter": (manifest or {}).get("converter", "anydoc"),
                "size_bytes": stat.st_size,
                "modified_at": stat.st_mtime,
                "summary": load_document_summary(stem),
                "has_chunks": _chunk_path(stem).is_file(),
                "has_schema": active_version is not None,
                "has_graph": active_version is not None
                and graphdb.has_graph(stem, version=active_version),
                "graphdb_name": graphdb.DB_PATH.name,
            }
        )
    return {"documents": documents}


def _document_path(filename: str) -> Path:
    return document_dir_for(_stem(filename)) / "raw.md"


def _stem(filename: str) -> str:
    return Path(os.path.basename(filename)).stem


def _chunk_path(stem: str) -> Path:
    return document_dir_for(stem) / "chunks.json"


@app.post("/api/documents/{filename}/chunk")
def create_chunks(filename: str):
    doc_path = _document_path(filename)
    if not doc_path.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    result = chunk_markdown_file(_stem(filename))
    return result


@app.get("/api/documents/{filename}/chunk")
def get_chunks(filename: str):
    path = _chunk_path(_stem(filename))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="chunks not found")
    return json.loads(path.read_text())


@app.post("/api/documents/{filename}/summary")
def create_summary(filename: str):
    doc_path = _document_path(filename)
    if not doc_path.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    try:
        summary = summarize_document(doc_path.read_text())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    save_document_summary(_stem(filename), summary)
    return {"summary": summary}


@app.get("/api/documents/{filename}/summary")
def get_summary(filename: str):
    summary = load_document_summary(_stem(filename))
    if summary is None:
        raise HTTPException(status_code=404, detail="summary not found")
    return {"summary": summary}


@app.get("/api/ontology/schemas")
def list_schemas():
    return {"schemas": [{"stem": stem} for stem in list_schema_stems()]}


@app.post("/api/ontology/reset-database")
def reset_database():
    graphdb.reset_database()
    return {"status": "ok"}


class DiscoverRequest(BaseModel):
    max_chars: int | None = None


@app.post("/api/ontology/{filename}/discover")
def discover(filename: str, request: DiscoverRequest | None = None):
    doc_path = _document_path(filename)
    if not doc_path.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    max_chars = request.max_chars if request else None
    try:
        report = discover_ontology(doc_path.read_text(), max_chars=max_chars)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    save_discovery(_stem(filename), report)
    return report


@app.get("/api/ontology/{filename}/discover")
def get_discovery(filename: str):
    report = load_discovery(_stem(filename))
    if report is None:
        raise HTTPException(status_code=404, detail="discovery not found")
    return report


class CreateSchemaRequest(BaseModel):
    document_type: str = "general"
    max_chars: int | None = None
    use_discovery: bool = False


@app.post("/api/ontology/{filename}/schema")
def create_schema(filename: str, request: CreateSchemaRequest | None = None):
    doc_path = _document_path(filename)
    if not doc_path.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    document_type = request.document_type if request else "general"
    max_chars = request.max_chars if request else None
    discovery = load_discovery(_stem(filename)) if (request and request.use_discovery) else None
    try:
        schema = generate_schema(
            doc_path.read_text(), document_type=document_type, max_chars=max_chars, discovery=discovery
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


class EvolveRequest(BaseModel):
    validation_report: dict
    max_chars: int | None = None


@app.post("/api/ontology/{filename}/evolve")
def evolve(filename: str, request: EvolveRequest):
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
    try:
        proposal = propose_evolution(
            doc_path.read_text(), schema, graph, request.validation_report, max_chars=request.max_chars
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return proposal


class EvolveApplyRequest(BaseModel):
    changes: list[dict]


@app.post("/api/ontology/{filename}/evolve/apply")
def evolve_apply(filename: str, request: EvolveApplyRequest):
    stem = _stem(filename)
    if get_active_version(stem) is None:
        raise HTTPException(status_code=404, detail="schema not found")
    try:
        result = apply_evolution(stem, request.changes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


class DomainConvergeRequest(BaseModel):
    # Calibration set, in the order each document should be folded into the
    # schema. filenames[0] seeds the schema (via generate_schema) unless
    # seed_schema is given, in which case every filename is folded in.
    filenames: list[str]
    seed_schema: dict | None = None
    document_type: str = "general"
    max_chars: int | None = None


@app.post("/api/ontology/domain-schema/converge")
def converge_domain(request: DomainConvergeRequest):
    if not request.filenames:
        raise HTTPException(status_code=400, detail="filenames must not be empty")
    docs = []
    for filename in request.filenames:
        doc_path = _document_path(filename)
        if not doc_path.is_file():
            raise HTTPException(status_code=404, detail=f"document not found: {filename}")
        docs.append({"stem": _stem(filename), "text": doc_path.read_text()})

    seed_schema = request.seed_schema
    remaining = docs
    try:
        if seed_schema is None:
            seed_schema = generate_schema(
                docs[0]["text"], document_type=request.document_type, max_chars=request.max_chars
            )
            remaining = docs[1:]
        result = converge_domain_schema(remaining, seed_schema, max_chars=request.max_chars)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Free to compute -- evaluate_domain_schema only reads the iteration log
    # converge_domain_schema already produced, no extra LLM/embedding calls.
    evaluation = evaluate_domain_schema(result["schema"], result["iterations"])
    return {"seed_schema": seed_schema, "evaluation": evaluation, **result}


class RedundantTypesRequest(BaseModel):
    node_types: list[dict]
    edge_types: list[dict]
    threshold: float = 0.9


@app.post("/api/ontology/domain-schema/redundant-types")
def redundant_types(request: RedundantTypesRequest):
    schema = {"node_types": request.node_types, "edge_types": request.edge_types}
    pairs = find_redundant_type_pairs(schema, threshold=request.threshold)
    return {"pairs": pairs}


@app.get("/api/ontology/domain-schemas")
def list_domain_schemas():
    return {"domains": list_domains()}


@app.get("/api/ontology/domain-schema/{domain}")
def get_domain_schema(domain: str):
    schema = load_domain_schema(domain)
    if schema is None:
        raise HTTPException(status_code=404, detail="domain schema not found")
    return {
        **schema,
        "calibration_stems": domain_calibration_stems(domain),
        "history": domain_convergence_history(domain),
        "pending_review": load_domain_pending_review(domain),
    }


class DomainRunConvergeRequest(BaseModel):
    # Documents to fold in this run, in order. If the domain has no stored
    # schema yet, filenames[0] seeds it (via generate_schema); otherwise the
    # domain's existing stored schema is the seed and every filename here is
    # folded in on top of it.
    filenames: list[str]
    max_chars: int | None = None


@app.post("/api/ontology/domain-schema/{domain}/converge")
def converge_domain_persisted(domain: str, request: DomainRunConvergeRequest):
    if not request.filenames:
        raise HTTPException(status_code=400, detail="filenames must not be empty")
    docs = []
    for filename in request.filenames:
        doc_path = _document_path(filename)
        if not doc_path.is_file():
            raise HTTPException(status_code=404, detail=f"document not found: {filename}")
        docs.append({"stem": _stem(filename), "text": doc_path.read_text()})
    try:
        result = run_domain_convergence(domain, docs, max_chars=request.max_chars)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    evaluation = evaluate_domain_schema(result["schema"], result["iterations"])
    return {**result, "evaluation": evaluation}


class DomainPendingReviewApplyRequest(BaseModel):
    changes: list[dict]


@app.post("/api/ontology/domain-schema/{domain}/pending-review/apply")
def apply_domain_pending_review(domain: str, request: DomainPendingReviewApplyRequest):
    try:
        result = apply_domain_schema_changes(domain, request.changes)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


class UseDomainSchemaRequest(BaseModel):
    domain: str
    document_type: str = "general"


@app.post("/api/ontology/{filename}/schema/use-domain")
def use_domain_schema_endpoint(filename: str, request: UseDomainSchemaRequest):
    stem = _stem(filename)
    try:
        version = use_domain_schema(stem, request.domain, document_type=request.document_type)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    schema = load_schema(stem, version)
    return {**schema, "version": version}


class StabilityRequest(BaseModel):
    document_type: str = "general"
    runs: int = 3
    max_chars: int | None = None


@app.post("/api/ontology/{filename}/schema/stability")
def schema_stability(filename: str, request: StabilityRequest | None = None):
    doc_path = _document_path(filename)
    if not doc_path.is_file():
        raise HTTPException(status_code=404, detail="document not found")
    document_type = request.document_type if request else "general"
    runs = request.runs if request else 3
    max_chars = request.max_chars if request else None
    try:
        result = measure_schema_stability(
            doc_path.read_text(), document_type=document_type, runs=runs, max_chars=max_chars
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


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
