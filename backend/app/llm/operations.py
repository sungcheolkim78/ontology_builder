"""The registry of operations: one entry per named kind of LLM call, holding
everything about that call that isn't the prompt. See CONTEXT.md ("Operation").

Being registered here is what makes an operation a JSON operation. That's an
allowlist rather than "everything except a known-prose set" because several
call sites whose replies are prose (main.py's /api/chat, app.graph.graphrag's
answer_question, summarize_document) call get_chat_model() with no operation
or an unregistered one, and an allowlist can never accidentally catch one of
those. For exactly the registered operations, app.llm.chat.get_chat_model adds
response_format={"type": "json_object"} and asks for low reasoning effort.

Low reasoning effort was verified live against every model in MODEL_CATALOG
plus two not-yet-cataloged ones (qwen/qwen3.8-flash, google/gemini-3.8-flash):
a generate_schema()-shaped call spends the overwhelming majority of its
response on hidden "reasoning" tokens before ever emitting the actual JSON
(e.g. 2812 of 3063 output tokens for z-ai/glm-5.3-flash on a *17-character*
document -- reasoning-token volume tracks how demanding the prompt's
instructions are, not input size), and that reasoning generation, being
sequential decoding, is the dominant cost of the 60s+ per-chunk-group
latency this was chasing down, not chunk size or network overhead. Effort
is NOT a uniform fix: it cut z-ai/glm-5.3-flash from ~60-90s to ~5-7s and
google/gemini-3.8-flash's reasoning to 0 entirely, but qwen/qwen3.8-flash
barely responds to it (137.9s -> 116.0s) -- some models' effort floor is
just high regardless of this hint. Every model tested still accepts the
parameter without erroring even when it has little effect, so this is
applied unconditionally rather than per-model.

Model selection: only the operations marked `selectable` (the four
ontology-pipeline ones) get their own model picker in the settings UI; every
other operation uses the shared "default" bucket. An operation with a
`model_key` follows the selection made for that other operation instead. The
consolidation operations (the reduce step of discover_ontology_from_chunks/
generate_schema_from_chunks) used to reuse "discover_ontology"/
"generate_schema" outright; they now have their own entry purely so they can
have a much larger token ceiling than a single group's call needs, and
`model_key` is what keeps that from also silently changing *which model* they
use -- without it, an operation nobody ever picks a model for in the settings
UI would fall through to the unrelated "default" bucket instead of following
whatever a person picked for "discover_ontology"/"generate_schema".
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Operation:
    name: str
    # Name of the Langfuse observation this operation's calls are recorded under.
    telemetry_name: str
    # Safety ceiling on output tokens -- see the comment above _ENTRIES.
    max_tokens: int
    # Top-level keys the response must contain, and the type each must have.
    required: dict = field(default_factory=dict)
    # Which operation's model selection this one follows, if not its own.
    model_key: str | None = None
    # Whether the settings UI lets a person pick a model for this operation.
    selectable: bool = False


# Hard ceilings well below each model's own max_completion_tokens
# (app.llm.chat.MODEL_CATALOG) for the ontology-pipeline operations whose prompts
# (app/llm/prompts.py) describe a realistically bounded output shape (a
# schema's own "~5-12 node_types" guidance, a validation report's handful of
# issues, ...) -- requesting a model's full ceiling on every call risks
# runaway generation on a malformed/looping response going undetected for as
# long as possible, for no benefit on the normal case (a well-formed
# response stops at its own natural end regardless of how high the ceiling
# is). Sized generously, as a safety ceiling rather than a token budget the
# model is expected to actually hit; app.llm.chat.get_model_max_tokens takes
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
#
# extract_graph got the same 40_000 bump for the same reason, just without
# a separate operation key -- unlike consolidation, every chunk group's own
# extract_graph() call needs it, not just one reduce step. Reproduced live
# against a real 24-node-type/63-edge-type schema (the same document as
# consolidate_schema's numbers above, after that fix let it actually
# converge to a schema this rich): 8 of 12 real chunk groups failed at
# 16_000 with the same mid-string cutoff, needing 10,300-21,200 output
# tokens once given the room -- a richer schema means more declared types
# an extraction pass can match against, hence more nodes/edges to emit
# regardless of chunk-group size.
_ENTRIES = (
    Operation(
        "discover_ontology", "discover-ontology", 16_000,
        required={"classes": list}, selectable=True,
    ),
    Operation(
        "consolidate_discovery", "consolidate-discovery-types", 40_000,
        required={"classes": list, "relationships": list}, model_key="discover_ontology",
    ),
    Operation(
        "generate_schema", "generate-schema", 8_000,
        required={"node_types": list, "edge_types": list}, selectable=True,
    ),
    Operation(
        "consolidate_schema", "consolidate-schema-types", 40_000,
        required={"node_types": list, "edge_types": list}, model_key="generate_schema",
    ),
    Operation(
        "extract_graph", "extract-graph", 40_000,
        required={"nodes": list, "edges": list}, selectable=True,
    ),
    Operation(
        "validate_ontology", "validate-ontology", 8_000,
        required={"validation_summary": dict, "issues": list}, selectable=True,
    ),
    Operation("propose_evolution", "propose-evolution", 8_000, required={"changes": list}),
    # graphrag's per-question analysis and goldenset generation: new to the
    # registry, so they now get the same JSON mode, low reasoning effort and
    # a ceiling as the ontology operations. Generous ceilings -- these were
    # previously unconstrained -- and no `selectable`: they use the default model.
    Operation(
        "analyze_question", "analyze-question", 8_000,
        required={"node_types": list, "edge_types": list},
    ),
    Operation("generate_goldenset_questions", "generate-goldenset-questions", 16_000),
    Operation("generate_goldenset_answers", "generate-goldenset-answers", 40_000),
)

OPERATIONS: dict[str, Operation] = {op.name: op for op in _ENTRIES}

# The operations the settings UI offers a model picker for, in display order.
OPERATION_KEYS = tuple(op.name for op in _ENTRIES if op.selectable)
