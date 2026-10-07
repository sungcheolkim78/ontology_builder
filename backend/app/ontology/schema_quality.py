"""Schema-quality checks: standalone diagnostics over a schema or over the
schema-generation step, independent of the discover/schema/extract stages.
find_redundant_type_pairs flags near-duplicate types by embedding similarity;
measure_schema_stability regenerates a schema from the same document several
times and measures how much the proposed type set changes run to run."""

import math

from app.llm.calls import embed

from .chunk_groups import map_concurrently
from .generate_schema import generate_schema


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
    pairs = []
    for kind, types in (("node_type", schema["node_types"]), ("edge_type", schema["edge_types"])):
        if len(types) < 2:
            continue
        texts = [f"{t['name']}: {t['description']}" for t in types]
        vectors = embed(f"find-redundant-type-pairs-{kind}", texts)
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
    section 3. The `runs` calls run concurrently via map_concurrently --
    each is an independent regeneration of the same document/schema, so
    nothing depends on an earlier run's result the way propose_evolution's
    iterative convergence does."""
    if runs < 2:
        raise ValueError("runs must be at least 2 to compare schemas")
    schemas = map_concurrently(
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
