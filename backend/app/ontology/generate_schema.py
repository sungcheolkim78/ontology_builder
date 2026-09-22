"""Schema-generation half of the ontology pipeline: propose candidate
classes/relationships for a document (discover_ontology) and, from those (or
independently), propose a node_types/edge_types schema (generate_schema).
Both have chunk-grouped map-reduce variants (see .utils's module comment) for
documents too large to send in one LLM call, plus the discover_for_document/
schema_for_document seams main.py's /discover and /schema routes call.
summarize_document (a lighter, non-JSON LLM call) and the schema-quality
checks at the bottom (find_redundant_type_pairs, measure_schema_stability)
live here too, since they all operate on a document/schema level, before any
node/edge instances exist -- see extract_graph.py for that stage and
evolve_graph.py for validating/evolving what extract_graph produces."""

import contextvars
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor

from langchain_core.messages import HumanMessage, SystemMessage

from app import ontology
from app.utils.paths import document_dir_for
from app.llm.prompts import (
    CONSOLIDATION_PROMPT,
    DISCOVERY_PROMPT,
    SCHEMA_CONSOLIDATION_PROMPT,
    SCHEMA_PROMPTS,
    SUMMARY_PROMPT,
)
from app.llm.telemetry import embed_with_telemetry, invoke_with_telemetry

from .utils import (
    _check_document_length,
    _dedupe_by_key,
    _group_document_text,
    _load_chunk_items,
    _require_document_text,
    group_chunks_by_budget,
    parse_json_response,
    start_progress,
)

# generate_schema_from_chunks()/measure_schema_stability() below each make
# several independent generate_schema() calls whose results are only
# combined afterwards -- nothing forces them onto one call after another, so
# _map_concurrently() below overlaps them in a small thread pool instead of
# looping. OpenRouter (like most LLM providers) rate-limits by concurrent
# in-flight requests, so this caps how many run at once rather than firing
# one thread per item unconditionally.
MAX_CONCURRENT_LLM_CALLS = int(os.environ.get("MAX_CONCURRENT_LLM_CALLS", 5))


def _map_concurrently(fn, items: list, on_item_done=None) -> list:
    """Runs fn(item) for every item in a small thread pool instead of one
    after another, returning results in the same order as `items` (matching
    the sequential list comprehension this replaces). LLM calls are
    I/O-bound (a blocking network round-trip), so overlapping them turns N
    sequential round-trips into roughly ceil(N / MAX_CONCURRENT_LLM_CALLS)
    round-trips' worth of wall-clock time. A single item skips the thread
    pool entirely -- the common case (a document that fits in one chunk
    group) pays no threading overhead at all.

    contextvars.copy_context() is taken once per item, right before
    submitting it, and used to run that item's call -- ThreadPoolExecutor
    does not propagate the calling thread's context on its own, and without
    this every concurrent invoke_with_telemetry call would show up as its
    own orphaned trace instead of nesting under the request's trace() span
    (see app.llm.telemetry.trace's own docstring). Each item gets its own
    fresh copy rather than one shared Context, since a Context object
    cannot be entered by more than one thread at a time.

    `on_item_done`, if given, is called with each item's own result right
    after that item's fn() call returns -- from whichever worker thread ran
    it, in whatever order calls happen to finish. Callers use this to report
    per-group progress (see .utils.ChunkProgress); it's the callback's own
    job to be safe to call concurrently."""

    def call(item):
        result = fn(item)
        if on_item_done is not None:
            on_item_done(result)
        return result

    if len(items) == 1:
        return [call(items[0])]
    with ThreadPoolExecutor(max_workers=min(len(items), MAX_CONCURRENT_LLM_CALLS)) as executor:
        futures = [executor.submit(contextvars.copy_context().run, call, item) for item in items]
        return [future.result() for future in futures]


# Per-chunk-group candidate dump ---------------------------------------------
#
# discover_ontology_from_chunks/generate_schema_from_chunks below write each
# group's own raw candidate output (classes/relationships, or
# node_types/edge_types -- before any cross-group consolidation) to
# documents/{stem}/progress/ as soon as that group's LLM call returns. Same
# "dump what a specific group actually produced, not just the final merged
# result" idea as extract_graph.py's own extraction_progress/{node,edge}_proc_{N}.json,
# just alongside the summary ChunkProgress file (progress/{operation}.json)
# in the same directory rather than a separate one.
#
# The map step runs concurrently (_map_concurrently above), so groups finish
# in whatever order their LLM calls happen to return in -- the file name
# still has to identify *which* group produced it, unambiguously and without
# collisions between concurrent writers. A shared "next available number"
# counter would need its own lock and still wouldn't say which group actually
# ran; instead, each group's index is fixed *before* the concurrent map even
# starts (its position in group_chunks_by_budget's output list), so threads
# only ever write to their own, already-unique filename -- no locking needed
# for this part, unlike ChunkProgress's shared summary state.
# [discover_ontology_from_chunks/generate_schema_from_chunks 공용 보조 함수]
# 이전 실행에서 남은 그룹별 후보 파일(예: discover_classes_5.json)을 지운다 --
# 요약 파일(progress/{operation}.json)은 건드리지 않도록 파일명 패턴으로만 선택.
def _clear_group_candidates(stem: str, operation: str) -> None:
    progress_dir = document_dir_for(stem) / "progress"
    if not progress_dir.is_dir():
        return
    for path in progress_dir.glob(f"{operation}_*.json"):
        path.unlink()


# [discover_ontology_from_chunks/generate_schema_from_chunks 공용 보조 함수]
# 한 그룹이 만들어낸 원시 후보(classes/relationships 또는 node_types/edge_types)를
# progress/{operation}_{필드명}_{index}.json으로 저장한다. index는 병렬 실행 시작
# 전에 미리 정해진 그룹의 고정 위치라서, 완료 순서와 무관하게 충돌 없이 안전하다.
def _write_group_candidates(stem: str | None, operation: str, index: int, **fields) -> None:
    if stem is None:
        return
    progress_dir = document_dir_for(stem) / "progress"
    progress_dir.mkdir(parents=True, exist_ok=True)
    for field_name, value in fields.items():
        (progress_dir / f"{operation}_{field_name}_{index}.json").write_text(
            json.dumps(value, ensure_ascii=False)
        )


# [독립 함수] 문서 전체를 요약하는 가벼운 LLM 호출. JSON이 아닌 순수 텍스트를 반환하며,
# 이 파일의 discover/generate 파이프라인과는 호출 관계가 없다.
def summarize_document(document_text: str, max_chars: int | None = None) -> str:
    _check_document_length(document_text, max_chars)
    # Explicit "summarize_document" operation (not the bare, model-selection-
    # only default every other prose-output call in the app also uses) is
    # what lets app.llm.chat.get_chat_model tell this call apart from a
    # JSON-expecting one and skip adding response_format -- see that
    # module's _JSON_OPERATIONS comment. Model *selection* is unaffected:
    # "summarize_document" isn't in OPERATION_KEYS either, so it still falls
    # through to the same "default" bucket as before.
    model = ontology.get_chat_model("summarize_document")
    response = invoke_with_telemetry(
        "summarize-document", model, SUMMARY_PROMPT.format(document=document_text)
    )
    summary = response.content.strip()
    if not summary:
        raise ValueError("summary generation returned empty content")
    return summary


# [발견 파이프라인의 leaf 함수] 문서 전체를 한 번의 LLM 호출로 보내 후보
# 클래스/관계 등을 발견한다. discover_ontology_from_chunks가 청크 그룹마다
# 이 함수를 호출한다(map 단계).
def discover_ontology(document_text: str, max_chars: int | None = None) -> dict:
    _check_document_length(document_text, max_chars)
    model = ontology.get_chat_model("discover_ontology")
    messages = [SystemMessage(content=DISCOVERY_PROMPT), HumanMessage(content=f"Document:\n{document_text}")]
    response = invoke_with_telemetry("discover-ontology", model, messages)
    report = parse_json_response(response.content)
    if not isinstance(report.get("classes"), list):
        raise ValueError("discovery JSON missing classes list")
    return report


# [discover_ontology_from_chunks 전용 보조 함수] 그룹별 discover_ontology
# 결과에서 classes/relationships만 추려 LLM에게 하나로 통합해 달라고 요청한다
# (reduce 단계 -- 서로 다른 그룹에서 같은 개념이 다른 이름으로 발견된 경우를 병합).
def _consolidate_types(group_reports: list[dict]) -> dict:
    payload = [
        {
            "group": i,
            "classes": [
                {k: c.get(k) for k in ("name", "definition", "category")}
                for c in report.get("classes", [])
            ],
            "relationships": [
                {k: r.get(k) for k in ("name", "definition", "source", "target", "category")}
                for r in report.get("relationships", [])
            ],
        }
        for i, report in enumerate(group_reports)
    ]
    model = ontology.get_chat_model("discover_ontology")
    messages = [
        SystemMessage(content=CONSOLIDATION_PROMPT),
        HumanMessage(
            content=f"Candidate classes and relationships by group:\n{json.dumps(payload, ensure_ascii=False)}"
        ),
    ]
    response = invoke_with_telemetry("consolidate-discovery-types", model, messages)
    consolidated = parse_json_response(response.content)
    if not isinstance(consolidated.get("classes"), list) or not isinstance(
        consolidated.get("relationships"), list
    ):
        raise ValueError("consolidation JSON missing classes/relationships lists")
    return consolidated


# [discover_ontology_from_chunks 전용 보조 함수] 그룹별 domain_model을 LLM 호출
# 없이 코드로만 병합한다(_dedupe_by_key로 중복 제거) -- _consolidate_types와 달리
# 이름 충돌을 판단할 필요가 없는 단순 리스트 필드들이라 LLM이 필요 없다.
def _merge_domain_models(domain_models: list[dict]) -> dict:
    domain = next((d.get("domain") for d in domain_models if d.get("domain")), "")
    merged = {"domain": domain}
    for field in ("subdomains", "document_types", "business_processes", "major_actors"):
        merged[field] = _dedupe_by_key(
            [v for d in domain_models for v in d.get(field, [])], key=lambda v: v
        )
    return merged


# [발견 파이프라인의 오케스트레이터] 청크 단위 map-reduce의 map+reduce를 모두
# 담당: 그룹별로 discover_ontology를 병렬 호출한 뒤(map, _map_concurrently 사용),
# 그룹이 2개 이상이면 _consolidate_types/_merge_domain_models로 통합한다(reduce).
# discover_for_document가 chunks.json이 있는 문서에 대해 이 함수를 호출한다.
def discover_ontology_from_chunks(
    chunk_items: list[dict], max_group_chars: int | None = None, stem: str | None = None
) -> dict:
    """Runs discover_ontology() once per token-budget-sized group of
    consecutive chunks (see group_chunks_by_budget), then consolidates
    every group's classes/relationships into one unified set via
    _consolidate_types. Exists for documents whose full text would exceed
    discover_ontology's own MAX_DOCUMENT_CHARS in a single call; a document
    small enough to fit in one group skips consolidation entirely and
    returns that single group's report untouched, so the common case pays
    for exactly one LLM call, same as discover_ontology(). The map step
    (one discover_ontology() call per group) runs concurrently via
    _map_concurrently -- groups are independent by design (see the module
    comment in .utils), so nothing is gained by waiting for group N's LLM
    call to finish before starting group N+1's.

    `stem`, if given (main.py's /discover route always has it), reports
    per-group progress to documents/{stem}/progress/discover.json via
    .utils.start_progress, so a GET route can be polled from the browser
    while this call is still running, and dumps each group's own raw
    classes/relationships to progress/discover_classes_{N}.json /
    progress/discover_relationships_{N}.json as soon as that group finishes
    (see the module comment above _write_group_candidates) -- `N` is the
    group's fixed position in `groups`, not a completion-order counter."""
    groups = group_chunks_by_budget(chunk_items, max_group_chars=max_group_chars)
    if not groups:
        raise ValueError("no chunks to discover ontology from")

    if stem is not None:
        _clear_group_candidates(stem, "discover")

    def discover_group(item):
        index, group = item
        report = discover_ontology(_group_document_text(group))
        _write_group_candidates(
            stem, "discover", index,
            classes=report.get("classes", []),
            relationships=report.get("relationships", []),
        )
        return report

    with start_progress(stem, "discover", len(groups)) as progress:
        group_reports = _map_concurrently(
            discover_group,
            list(enumerate(groups, start=1)),
            on_item_done=progress.advance,
        )
        if len(group_reports) == 1:
            return group_reports[0]

        progress.set_stage("reduce")
        consolidated_types = _consolidate_types(group_reports)
        return {
            "domain_model": _merge_domain_models([r.get("domain_model", {}) for r in group_reports]),
            "classes": consolidated_types["classes"],
            "relationships": consolidated_types["relationships"],
            "attributes": _dedupe_by_key(
                [a for r in group_reports for a in r.get("attributes", [])],
                key=lambda a: (a.get("name"), a.get("defined_on")),
            ),
            "events": _dedupe_by_key(
                [e for r in group_reports for e in r.get("events", [])], key=lambda e: e.get("name")
            ),
            "rules": _dedupe_by_key(
                [ru for r in group_reports for ru in r.get("rules", [])], key=lambda ru: ru.get("name")
            ),
            "terminology": _dedupe_by_key(
                [t for r in group_reports for t in r.get("terminology", [])],
                key=lambda t: t.get("canonical_term"),
            ),
            "competency_questions": _dedupe_by_key(
                [q for r in group_reports for q in r.get("competency_questions", [])], key=lambda q: q
            ),
            "warnings": _dedupe_by_key(
                [w for r in group_reports for w in r.get("warnings", [])], key=lambda w: w
            ),
        }


# [스키마 생성 파이프라인의 leaf 함수이자 이 파일에서 가장 많이 재사용되는 핵심 함수]
# 문서(또는 그룹 텍스트) 전체를 한 번의 LLM 호출로 보내 node_types/edge_types 스키마를
# 만든다. generate_schema_from_chunks(그룹마다), measure_schema_stability(반복 호출),
# schema_for_document(청크가 없을 때)가 이 함수를 호출하고, app.ontology.domain_schema의
# 도메인 스키마 시딩에서도 그대로 재사용된다.
def generate_schema(
    document_text: str,
    document_type: str = "general",
    max_chars: int | None = None,
    discovery: dict | None = None,
) -> dict:
    _check_document_length(document_text, max_chars)
    system_prompt = SCHEMA_PROMPTS.get(document_type)
    if system_prompt is None:
        raise ValueError(f"unknown document_type: {document_type!r}")
    model = ontology.get_chat_model("generate_schema")
    if discovery:
        # Appended, not merged into SCHEMA_PROMPT/LEGAL_SCHEMA_PROMPT's own
        # text -- keeps those constants completely unchanged when discovery
        # is None (the default), which is the entire point: this is an
        # optional hint layered on top of the existing prompt, not a
        # replacement for it. Still lands in the system message (not the
        # human message alongside the document) since it's identical across
        # every chunk group of one generate_schema_from_chunks call, just
        # like the base prompt itself -- see prompts.py's module comment.
        system_prompt = system_prompt + (
            "\n\nReference -- a prior ontology-discovery pass over this document already "
            "proposed these candidate classes/relationships/terminology. Use them only "
            "as a starting hint; the schema you propose must still be independently "
            "grounded in the document text below, and you may diverge from this "
            "reference where the document doesn't actually support it.\n"
            f"{json.dumps(discovery)}"
        )
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=f"Document:\n{document_text}")]
    response = invoke_with_telemetry("generate-schema", model, messages)
    schema = parse_json_response(response.content)
    if not isinstance(schema.get("node_types"), list) or not isinstance(
        schema.get("edge_types"), list
    ):
        raise ValueError("schema JSON missing node_types/edge_types lists")
    return schema


# [generate_schema_from_chunks 전용 보조 함수] 그룹별 스키마의 node_types/edge_types를
# LLM에게 통합해 달라고 요청한다(reduce 단계 -- _consolidate_types의 스키마 버전).
def _consolidate_schema_types(group_schemas: list[dict]) -> dict:
    payload = [
        {
            "group": i,
            "node_types": schema.get("node_types", []),
            "edge_types": schema.get("edge_types", []),
        }
        for i, schema in enumerate(group_schemas)
    ]
    model = ontology.get_chat_model("generate_schema")
    messages = [
        SystemMessage(content=SCHEMA_CONSOLIDATION_PROMPT),
        HumanMessage(
            content=f"Candidate node_types and edge_types by group:\n{json.dumps(payload, ensure_ascii=False)}"
        ),
    ]
    response = invoke_with_telemetry("consolidate-schema-types", model, messages)
    consolidated = parse_json_response(response.content)
    if not isinstance(consolidated.get("node_types"), list) or not isinstance(
        consolidated.get("edge_types"), list
    ):
        raise ValueError("schema consolidation JSON missing node_types/edge_types lists")
    return consolidated


# [스키마 생성 파이프라인의 오케스트레이터] discover_ontology_from_chunks와 동일한
# map-reduce 구조: 그룹별로 generate_schema를 호출한 뒤(map), 그룹이 2개 이상이면
# _consolidate_schema_types로 통합한다(reduce). schema_for_document가 chunks.json이
# 있는 문서에 대해 이 함수를 호출한다.
def generate_schema_from_chunks(
    chunk_items: list[dict],
    document_type: str = "general",
    max_group_chars: int | None = None,
    discovery: dict | None = None,
    stem: str | None = None,
) -> dict:
    """Runs generate_schema() once per token-budget-sized group of
    consecutive chunks (see group_chunks_by_budget), then consolidates every
    group's node_types/edge_types into one unified schema via
    _consolidate_schema_types. Same shape as discover_ontology_from_chunks:
    a document small enough to fit in one group skips consolidation
    entirely and returns that single group's schema untouched, so the
    common case still costs exactly one LLM call. `discovery`, if given, is
    passed through to every group's generate_schema() call unchanged (it's
    already a document-level hint, not something that needs re-deriving per
    group). The map step (one generate_schema() call per group) runs
    concurrently via _map_concurrently -- groups are independent by design
    (see the module comment in .utils), so nothing is gained by waiting for
    group N's LLM call to finish before starting group N+1's.

    `stem`, if given (main.py's /schema route always has it), reports
    per-group progress to documents/{stem}/progress/schema.json, and dumps
    each group's own raw node_types/edge_types to
    progress/schema_node_types_{N}.json / progress/schema_edge_types_{N}.json
    as soon as that group finishes -- see discover_ontology_from_chunks's
    own docstring for the same mechanism (`N` is the group's fixed position
    in `groups`, not a completion-order counter)."""
    groups = group_chunks_by_budget(chunk_items, max_group_chars=max_group_chars)
    if not groups:
        raise ValueError("no chunks to generate schema from")

    if stem is not None:
        _clear_group_candidates(stem, "schema")

    def generate_group_schema(item):
        index, group = item
        schema = generate_schema(
            _group_document_text(group), document_type=document_type, discovery=discovery
        )
        _write_group_candidates(
            stem, "schema", index,
            node_types=schema.get("node_types", []),
            edge_types=schema.get("edge_types", []),
        )
        return schema

    with start_progress(stem, "schema", len(groups)) as progress:
        group_schemas = _map_concurrently(
            generate_group_schema,
            list(enumerate(groups, start=1)),
            on_item_done=progress.advance,
        )
        if len(group_schemas) == 1:
            return group_schemas[0]

        progress.set_stage("reduce")
        return _consolidate_schema_types(group_schemas)


# [외부 진입점] main.py의 /discover 라우트가 호출하는 seam. 문서 존재 여부를 확인한
# 뒤 chunks.json 유무에 따라 discover_ontology_from_chunks 또는 discover_ontology로
# 라우팅한다 -- 이 파일 밖에서는 이 함수(그리고 schema_for_document)만 호출되는 것이
# 정상적인 사용 방식이다.
def discover_for_document(stem: str, max_chars: int | None = None) -> dict:
    """One seam for main.py's /discover route: owns the document-existence
    check and the chunks.json-vs-whole-document routing that route used to
    duplicate inline (see discover_ontology_from_chunks/discover_ontology).
    Raises FileNotFoundError if the document hasn't been parsed yet. Also
    reports progress for the whole-document (no chunks.json) branch itself
    -- discover_ontology_from_chunks reports its own when there are
    chunks -- so a poller always finds a progress record no matter which
    path this document takes.

    `max_chars` is passed through to discover_ontology() for the
    whole-document branch only -- it deliberately is NOT forwarded as
    discover_ontology_from_chunks's own `max_group_chars`, even though
    they're both "how much text is safe to send in one call" in spirit.
    They're different budgets in practice: `max_chars` is this route's own
    request field, sized for "how big a document can this whole-document
    call handle" (frontend default 1,000,000, effectively "no limit" for
    real documents); `max_group_chars` controls how finely
    group_chunks_by_budget splits an *already-chunked* document, and
    defaults to MAX_CHUNK_GROUP_CHARS (.utils) precisely so an operator can
    tune chunk-group size via that env var alone. Passing the former
    through as the latter used to silently defeat MAX_CHUNK_GROUP_CHARS
    entirely for any document, real or test, since the frontend always
    sends a non-None max_chars far larger than any sane group budget --
    group_chunks_by_budget only falls back to MAX_CHUNK_GROUP_CHARS when
    its own max_group_chars argument is None."""
    document_text = _require_document_text(stem)
    chunk_items = _load_chunk_items(stem)
    if chunk_items is not None:
        return discover_ontology_from_chunks(chunk_items, stem=stem)
    with start_progress(stem, "discover", 1) as progress:
        result = discover_ontology(document_text, max_chars=max_chars)
        progress.advance()
        return result


# [외부 진입점] main.py의 /schema 라우트가 호출하는 seam. discover_for_document와
# 동일한 패턴으로, 문서 존재 여부를 확인한 뒤 chunks.json 유무에 따라
# generate_schema_from_chunks 또는 generate_schema로 라우팅한다.
def schema_for_document(
    stem: str,
    document_type: str = "general",
    max_chars: int | None = None,
    discovery: dict | None = None,
) -> dict:
    """One seam for main.py's /schema route: same shape as
    discover_for_document, for generate_schema/generate_schema_from_chunks --
    including reporting progress for the whole-document branch itself, so a
    poller always finds a progress record no matter which path this
    document takes. Raises FileNotFoundError if the document hasn't been
    parsed yet.

    `max_chars` is NOT forwarded as generate_schema_from_chunks's own
    `max_group_chars` -- see discover_for_document's own docstring for why
    those are different budgets that happen to look alike, and what doing
    so used to silently break (MAX_CHUNK_GROUP_CHARS having no effect,
    since the frontend always sends a non-None max_chars far larger than
    any sane group budget)."""
    document_text = _require_document_text(stem)
    chunk_items = _load_chunk_items(stem)
    if chunk_items is not None:
        return generate_schema_from_chunks(
            chunk_items, document_type=document_type, discovery=discovery, stem=stem
        )
    with start_progress(stem, "schema", 1) as progress:
        result = generate_schema(
            document_text, document_type=document_type, max_chars=max_chars, discovery=discovery
        )
        progress.advance()
        return result


# [find_redundant_type_pairs 전용 보조 함수] 두 임베딩 벡터 사이의 코사인 유사도를
# 계산하는 순수 수학 함수. 외부 의존성 없음.
def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# [독립 기능: 스키마 품질 점검] discover/generate 파이프라인과는 별개로 동작하며,
# 이미 만들어진 스키마 하나를 대상으로 타입 이름+설명을 임베딩해 서로 너무 비슷한
# node_type/edge_type 쌍(_cosine_similarity 사용)을 찾아낸다.
def find_redundant_type_pairs(schema: dict, threshold: float = 0.9) -> list[dict]:
    """Flags node_type/edge_type pairs whose name+description embed to
    near-identical vectors (cosine similarity >= threshold) -- a domain
    schema that has grown two types for what's really one concept. Compares
    node_types against node_types and edge_types against edge_types only,
    never across the two, since a node type and an edge type can't be
    merged regardless of how similar their descriptions read."""
    model = ontology.get_embedding_model()
    pairs = []
    for kind, types in (("node_type", schema["node_types"]), ("edge_type", schema["edge_types"])):
        if len(types) < 2:
            continue
        texts = [f"{t['name']}: {t['description']}" for t in types]
        vectors = embed_with_telemetry(f"find-redundant-type-pairs-{kind}", model, texts)
        for i in range(len(types)):
            for j in range(i + 1, len(types)):
                similarity = _cosine_similarity(vectors[i], vectors[j])
                if similarity >= threshold:
                    pairs.append(
                        {
                            "element_type": kind,
                            "a": types[i]["name"],
                            "b": types[j]["name"],
                            "similarity": similarity,
                        }
                    )
    return pairs


# [독립 기능: 스키마 품질 점검] discover/generate 파이프라인과는 별개로 동작하며,
# 동일한 문서로 generate_schema를 여러 번 반복 호출해 매번 다른 타입 집합이
# 나오는지(Jaccard 유사도)를 측정한다 -- 낮은 안정성은 스키마가 아니라 문서/프롬프트가
# 모호하다는 신호.
def measure_schema_stability(
    document_text: str,
    document_type: str = "general",
    runs: int = 3,
    max_chars: int | None = None,
) -> dict:
    """Regenerates a schema for the same document `runs` times and measures
    how much the proposed type set changes run to run via pairwise Jaccard
    similarity of type-name sets. Low stability signals the *document/prompt*
    is underspecified for schema generation, not that any one generated
    schema is wrong -- see docs/ontology/domain_schema_convergence.md
    section 3. The `runs` calls run concurrently via _map_concurrently --
    each is an independent regeneration of the same document/schema, so
    nothing depends on an earlier run's result the way propose_evolution's
    iterative convergence does."""
    if runs < 2:
        raise ValueError("runs must be at least 2 to compare schemas")
    schemas = _map_concurrently(
        lambda _: generate_schema(document_text, document_type=document_type, max_chars=max_chars),
        list(range(runs)),
    )
    type_name_sets = [
        {t["name"] for t in schema["node_types"]} | {t["name"] for t in schema["edge_types"]}
        for schema in schemas
    ]

    similarities = []
    for i in range(len(type_name_sets)):
        for j in range(i + 1, len(type_name_sets)):
            a, b = type_name_sets[i], type_name_sets[j]
            union = a | b
            similarities.append(len(a & b) / len(union) if union else 1.0)

    return {
        "runs": runs,
        "type_name_sets": [sorted(s) for s in type_name_sets],
        "avg_jaccard_similarity": sum(similarities) / len(similarities) if similarities else 1.0,
    }
