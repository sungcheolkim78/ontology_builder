# LLM tracing via Langfuse

`app/telemetry.py` (`invoke_with_telemetry`/`embed_with_telemetry`, used at
every LLM/embedding call site — see `docs/SPEC.md`'s "Telemetry" section)
sends traces to [Langfuse](https://langfuse.com), not Jaeger. This replaced
a raw-OpenTelemetry-to-Jaeger setup that only ever showed generic spans —
no cost/token accounting, no prompt/response side-by-side view, no
per-question scoring — none of which is much use for debugging *why* an
LLM call produced a bad answer, as opposed to *how long* it took.

Langfuse's own Python SDK (the `langfuse` package) is itself
OpenTelemetry-based: it registers a real OTEL `TracerProvider` and exports
spans to Langfuse's ingestion endpoint over standard OTLP
(`{host}/api/public/otel/v1/traces`) — confirmed by pointing it at an
unreachable host and observing the OTLP exporter's own retry/timeout
logging, not some Langfuse-specific protocol. In other words this is a
full SDK swap (spans become Langfuse `generation`/`embedding`
observations with structured model/input/output/usage fields, not
generic span attributes), but it stays interoperable with anything else
OTEL-instrumented rather than a proprietary format.

## The server: self-hosted, shared, not part of this repo

Langfuse's self-hosted stack is Postgres + ClickHouse + Redis + MinIO +
two Langfuse containers (~4 cores / 16 GiB recommended) — too heavy to
justify bundling into `podman-compose.yml` and running once per project.
It's set up as a single instance shared across every project on this
machine, at **`~/services/langfuse/`** (outside any git repo, since it's
host-level infrastructure, not project code). That directory's own
`README.md` has full setup/run/upgrade/backup/troubleshooting
instructions — read that before touching the server itself; this file
only covers how `ontology_builder` connects to it.

## Connecting this project

1. Bring up `~/services/langfuse/` (see its README) and open
   http://localhost:3000.
2. Create a project there for `ontology_builder` (Settings → Projects),
   then Settings → API Keys → Create new API key.
3. In `backend/.env` (git-ignored; copy from `backend/.env.example` if you
   haven't already), set:
   ```
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   LANGFUSE_HOST=http://host.containers.internal:3000
   ```
   `host.containers.internal` is how the `backend` container (running
   inside this project's own `podman-compose.yml`) reaches the *host's*
   port 3000, which is where the separate, globally-running Langfuse
   stack's `langfuse-web` container has its port mapped. There is no
   shared podman network between the two `podman-compose` projects — this
   works purely because both ultimately run on the same podman machine
   and `langfuse-web`'s port is published to the host.
4. `podman-compose up --build -d` (or restart the `backend` service if
   it's already running) to pick up the new env vars.

All three vars are optional and independent of everything else in this
app: leaving `LANGFUSE_PUBLIC_KEY` unset disables tracing entirely (see
`configure_telemetry()` in `telemetry.py`) with zero behavior change
otherwise — this is also why backend tests never set it and never talk to
a Langfuse server.

## Render (production): off by default

`render.yaml` declares no `LANGFUSE_*` env vars for the backend service,
so `LANGFUSE_PUBLIC_KEY` is unset there and tracing simply never turns on
— no code change or explicit "disable" flag needed, and nothing to do to
keep it that way. This is also the right call independent of wanting it
off: the self-hosted server this project points at locally only exists on
this machine (`~/services/langfuse/`, bound to `127.0.0.1`/this podman
machine) and isn't reachable from Render regardless.

If you ever *do* want Render traces, that requires standing up a Langfuse
instance actually reachable from Render (Langfuse Cloud, or a self-hosted
instance on a public host) and adding `LANGFUSE_PUBLIC_KEY`/
`LANGFUSE_SECRET_KEY`/`LANGFUSE_HOST` as Render dashboard env vars (`sync:
false` in `render.yaml`, same pattern as `OPENROUTER_API_KEY`) pointed at
that instance — not at `~/services/langfuse/`, which Render can never
reach.

## What shows up

Every chat answer, schema generation, graph extraction, discovery/summary
call, and embedding call becomes one `generation` (or `embedding`)
observation at http://localhost:3000, per project, with:

- the model name, full prompt/response text, and token usage
  (`input_tokens`/`output_tokens`) — this is local dev only; the same
  caveat the old Jaeger setup carried still applies if you ever point
  `LANGFUSE_HOST` at anything other than a server you control, since the
  full document/prompt text is sent, not just metadata.
- a `retry_count` metadata field and an `ERROR`-level status with
  `status_message` set, for calls that hit
  `langchain_core.exceptions.ModelConnectionError` or any other exception
  (mirrors the retry behavior `docs/SPEC.md` documents in detail).

### Trace hierarchy

A single HTTP request often makes several of the calls above -- `/api/chat`
against a document with an extracted graph runs `analyze-question` (which
type/keywords are relevant) then `answer-chat`, sometimes with an
`embed-query` embedding call in between when keyword matching finds
nothing; a chunked document's `/schema` request runs one `generate-schema`
call per chunk group plus a `consolidate-schema-types` reduce. Each of
these route handlers wraps its body in `telemetry.trace(name, session_id=,
tags=, metadata=, input=)`, which opens one root Langfuse **span** so every
call made inside nests under it as a child **generation**/**embedding**
instead of each becoming its own unrelated top-level trace -- confirmed by
sending a real chat request against a document with a graph and fetching it
back via `langfuse-cli`: `chat-turn` (span, root) contained
`analyze-question` → `embed-query` → `answer-chat` (all three generations,
correctly parented, all carrying the same `session_id`/`tags`/`filename`
metadata). `session_id` on `/api/chat` comes from the frontend
(`ChatPanel.vue` generates one `crypto.randomUUID()` per component
lifetime, since `messages` there is never reset on document switch --
grouping every chat turn in one browser session).

`trace()`'s `input`/`output` are deliberately a short, human-readable
summary (the user's actual question and the final answer; a node/edge
count for extraction; a class/relationship count for discovery) rather
than the raw request body -- per Langfuse's own trace-instrumentation
[best practices](https://langfuse.com/docs/observability/best-practices),
the root observation's input/output are "what a reviewer needs at a
glance," not everything a route handler happened to receive. Operation
names for individual generations (`answer-chat`, `generate-schema`,
`extract-graph`, ...) were renamed from their earlier dotted-module style
(`ontology.generate_schema`) to short, verb-first, dash-case names for the
same reason -- the old names were fine for grepping this codebase but not
for Langfuse's own naming guidance around stable, filterable trace/span
names.

## The `langfuse` skill

`.claude/skills/langfuse/` (vendored from
[github.com/langfuse/skills](https://github.com/langfuse/skills)) is
installed in this repo for future Langfuse work in Claude Code sessions --
instrumentation audits, prompt migration, dataset/eval setup, CLI usage,
etc. Its `references/instrumentation.md` is what the trace-hierarchy design
above was built and self-audited against (fetch a fresh trace via
`langfuse-cli` after any instrumentation change and check it against
https://langfuse.com/docs/observability/best-practices, not from memory --
Langfuse's guidance changes over time). To update it, re-download
`skills/langfuse` from that repo's `main` branch and overwrite this
directory.

## Migration note

`podman-compose.yml` no longer runs a `jaeger` service or sets
`OTEL_EXPORTER_OTLP_ENDPOINT` — both are gone, replaced by the
`LANGFUSE_*` env vars above. `backend/requirements.txt` dropped the three
`opentelemetry-*` packages in favor of the single `langfuse` package
(which pulls in its own compatible OpenTelemetry dependencies
transitively — nothing in `app/` imports `opentelemetry` directly
anymore).
