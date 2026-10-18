PYTHON ?= backend/.venv/bin/python
PODMAN_COMPOSE ?= podman-compose
PYTHON314 ?= $(shell command -v /opt/homebrew/opt/python@3.14/bin/python3.14 2>/dev/null || command -v python3.14 2>/dev/null)
INPUT_DIR ?= data/raw/md
OUTPUT_DIR ?= data/goldenset
QUESTIONS_PER_DOCUMENT ?= 10
MODEL ?= nvidia/nemotron-3-ultra-550b-a55b:free
QUESTION_CONTEXT_CHARS ?= 24000
LOG_FILE ?=
MAX_PROC_FILEN ?=

# Golden-set evaluation of one extracted document (scripts/evaluate_golden_set.py).
# It always works on a COPY of backend/data (EVAL_DATA_DIR), never the live one.
EVAL_STEM ?= 삼성올백(ALL100)종신보험(2601)(무배당,무해약환급금형)_약관_raw
EVAL_DATA_DIR ?= backups/eval_data
EVAL_QUESTIONS ?= 50
EVAL_CONTEXT_CHARS ?= 40000
EVAL_HOPS ?= 1
GROUP_CHARS ?= 3000
GROUP_SIZES ?= 6000 1500
EVAL_VERSION ?=
VARIANT ?= scripts/prompt_variants/extract_clause_content.txt
EVAL_VERSION_ARG := $(if $(strip $(EVAL_VERSION)),--version $(EVAL_VERSION),)
EVAL_RUNS_DIR ?= backups/eval_runs
EVAL_SCRIPT := scripts/evaluate_golden_set.py
# LADYBUG_BUFFER_POOL_SIZE: the graph database's default pool (256MB) is too small for a data copy holding
# several extracted versions; the server runs with 1GB (backend/.env.example).
EVAL_BUFFER_POOL_SIZE ?= 1073741824
EVAL_ENV := ONTOLOGY_DATA_DIR="$(abspath $(EVAL_DATA_DIR))" LADYBUG_BUFFER_POOL_SIZE=$(EVAL_BUFFER_POOL_SIZE)

GOLDENSET_SCRIPT := scripts/prepare_goldenset/prepare_goldenset.py
MODEL_ARG := $(if $(strip $(MODEL)),--model "$(MODEL)",)
LOG_ARG := $(if $(strip $(LOG_FILE)),--log-file "$(LOG_FILE)",)
MAX_FILES_ARG := $(if $(strip $(MAX_PROC_FILEN)),--max-process-files "$(MAX_PROC_FILEN)",)

.PHONY: help eval-schema golden-generate prompt-probe golden-score golden-curve golden-judge eval-reextract-dry eval-data eval-data-reset eval-check eval-reextract golden-density golden-eval golden-probe-chunks golden-eval-generate podman-start podman-up podman-down podman-restart setup goldenset goldenset-overwrite goldenset-test samsunglife-data samsunglife-data-test pdf-to-md pdf-to-md-verify pdf-to-md-test chunk-terms chunk-terms-test

help:
	@echo "Available targets:"
	@echo "  make podman-start"
	@echo "      Start the Podman machine."
	@echo "  make podman-up"
	@echo "      Start Podman and run the podman-compose stack in the background."
	@echo "  make podman-down"
	@echo "      Stop the podman-compose stack."
	@echo "  make podman-restart"
	@echo "      Restart the podman-compose stack."
	@echo "  make setup"
	@echo "      Create backend/.venv (python3.14) and install backend/requirements-dev.txt."
	@echo "  make goldenset INPUT_DIR=./documents"
	@echo "      Generate a golden QA set from Markdown files."
	@echo "  make goldenset-overwrite INPUT_DIR=./documents"
	@echo "      Regenerate and overwrite existing per-document results."
	@echo "  make goldenset-test"
	@echo "      Run unit tests for the golden-set pipeline."
	@echo "  make samsunglife-data"
	@echo "      Download Samsung Life individual-insurance policy PDFs."
	@echo "  make samsunglife-data-test"
	@echo "      Run unit tests for the Samsung Life downloader."
	@echo "  make pdf-to-md"
	@echo "      Convert data/raw/pdf files to table-aware Markdown."
	@echo "  make pdf-to-md-verify"
	@echo "      Convert a single PDF from data/raw/pdf/보장성 as a smoke test."
	@echo "  make pdf-to-md-test"
	@echo "      Run unit tests for the PDF-to-Markdown converter."
	@echo "  make chunk-terms"
	@echo "      Chunk data/raw/md files into per-article JSON under data/chunks."
	@echo "  make chunk-terms-test"
	@echo "      Run unit tests for the Markdown article chunker."
	@echo ""
	@echo "  make eval-data"
	@echo "      Copy backend/data to EVAL_DATA_DIR (backups/eval_data) for golden-set evaluation."
	@echo "  make eval-data-reset"
	@echo "      Delete that copy and copy backend/data again."
	@echo "  make eval-check"
	@echo "      Check the evaluation setup (no LLM call)."
	@echo "  make eval-schema EVAL_STEM=<stem>"
	@echo "      Generate a schema for a document that has none (in the eval copy)."
	@echo "  make golden-generate EVAL_STEM=<stem>"
	@echo "      Generate the golden set only (no scoring), for a document with no graph yet."
	@echo "  make golden-eval"
	@echo "      Score the document's existing golden set (1 LLM call per answerable question)."
	@echo "  make eval-reextract-dry GROUP_CHARS=3000"
	@echo "      Report how many chunk groups (LLM calls) extraction would make at that group size."
	@echo "  make eval-reextract GROUP_CHARS=3000"
	@echo "      Extract the document again at that group size under a copy of the active schema (new version)."
	@echo "  make golden-density GROUP_CHARS=3000"
	@echo "      eval-reextract, then golden-score the new version (the run's report is labelled with the size)."
	@echo "  make golden-curve GROUP_SIZES='6000 1500'"
	@echo "      golden-density at each size in turn."
	@echo "  make golden-score"
	@echo "      Score the active version with every reading except the judge (no probe)."
	@echo "  make prompt-probe VARIANT=<file>"
	@echo "      Re-extract the chunks of the never-answered golden questions with and without the variant prompt text."
	@echo "  make golden-judge"
	@echo "      Score the active version with every reading, including the LLM judge (EVAL_VERSION=N picks a version)."
	@echo "  make golden-probe-chunks"
	@echo "      Score the existing golden set, then re-extract only the chunks quoting each missed passage."
	@echo "  make golden-eval-generate"
	@echo "      Generate a new golden set (EVAL_QUESTIONS), score it, and re-extract the groups behind the misses."
	@echo ""
	@echo "Optional variables:"
	@echo "  EVAL_STEM=<document folder name> EVAL_QUESTIONS=50 EVAL_CONTEXT_CHARS=40000 EVAL_HOPS=1"
	@echo "  EVAL_DATA_DIR=backups/eval_data EVAL_RUNS_DIR=backups/eval_runs (each run's report is saved there)"
	@echo "  OUTPUT_DIR=./goldenset QUESTIONS_PER_DOCUMENT=10 MODEL=openai/gpt-4o-mini"
	@echo "  QUESTION_CONTEXT_CHARS=24000 LOG_FILE=./goldenset/run.log"
	@echo "  MAX_PROC_FILEN=10"
	@echo "  PYTHON=backend/.venv/bin/python"
	@echo "  PODMAN_COMPOSE=podman-compose"

podman-start:
	@if ! podman machine inspect >/dev/null 2>&1; then \
		podman machine init; \
	fi
	@if [ "$$(podman machine inspect --format '{{.State}}' 2>/dev/null)" != "running" ]; then \
		podman machine start; \
	fi

podman-up: podman-start
	$(PODMAN_COMPOSE) up --build -d

podman-down:
	$(PODMAN_COMPOSE) down

podman-restart: podman-down podman-up

setup:
	@test -n "$(PYTHON314)" || (echo "python3.14 not found; install it (e.g. 'brew install python@3.14') and retry" >&2; exit 2)
	@test -x "$(PYTHON314)" || (echo "$(PYTHON314) is not executable" >&2; exit 2)
	$(PYTHON314) -m venv backend/.venv
	backend/.venv/bin/pip install --upgrade pip
	backend/.venv/bin/pip install -r backend/requirements-dev.txt

goldenset:
	@test -n "$(INPUT_DIR)" || (echo "INPUT_DIR is required" >&2; exit 2)
	$(PYTHON) $(GOLDENSET_SCRIPT) "$(INPUT_DIR)" --output-dir "$(OUTPUT_DIR)" --questions-per-document "$(QUESTIONS_PER_DOCUMENT)" --question-context-chars "$(QUESTION_CONTEXT_CHARS)" $(MODEL_ARG) $(LOG_ARG) $(MAX_FILES_ARG)

goldenset-overwrite:
	@test -n "$(INPUT_DIR)" || (echo "INPUT_DIR is required" >&2; exit 2)
	$(PYTHON) $(GOLDENSET_SCRIPT) "$(INPUT_DIR)" --output-dir "$(OUTPUT_DIR)" --questions-per-document "$(QUESTIONS_PER_DOCUMENT)" --question-context-chars "$(QUESTION_CONTEXT_CHARS)" $(MODEL_ARG) $(LOG_ARG) $(MAX_FILES_ARG) --overwrite

goldenset-test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B -m pytest \
		scripts/prepare_goldenset/test_prepare_goldenset.py -q

samsunglife-data:
	$(PYTHON) scripts/data_prep/download_samsunglife_terms.py

samsunglife-data-test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B -m pytest \
		scripts/data_prep/test_download_samsunglife_terms.py -q

pdf-to-md:
	$(PYTHON) scripts/data_prep/convert_pdfs_to_markdown.py

pdf-to-md-verify:
	$(PYTHON) scripts/data_prep/convert_pdfs_to_markdown.py \
		--input-dir data/raw/pdf/보장성 --output-dir data/raw/md/보장성 --limit 1 --overwrite

pdf-to-md-test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B -m pytest \
		scripts/data_prep/test_convert_pdfs_to_markdown.py -q

chunk-terms:
	python3 scripts/data_prep/chunk_terms_markdown.py --input-dir "$(INPUT_DIR)" --output-dir data/chunks

chunk-terms-test:
	PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest \
		scripts/data_prep/test_chunk_terms_markdown.py -q

eval-data:
	@if [ -e "$(EVAL_DATA_DIR)" ]; then \
		echo "$(EVAL_DATA_DIR) already exists (make eval-data-reset copies backend/data again)"; \
	else \
		if [ -n "$$(podman ps -q 2>/dev/null)" ]; then \
			echo "warning: containers are running; stop them first (make podman-down) for a consistent copy of the graph database" >&2; \
		fi; \
		mkdir -p "$(dir $(EVAL_DATA_DIR))" && cp -R backend/data "$(EVAL_DATA_DIR)" && echo "copied backend/data -> $(EVAL_DATA_DIR)"; \
	fi

eval-data-reset:
	@test "$(abspath $(EVAL_DATA_DIR))" != "$(abspath backend/data)" || (echo "EVAL_DATA_DIR must not be backend/data" >&2; exit 2)
	rm -rf "$(EVAL_DATA_DIR)"
	@$(MAKE) --no-print-directory eval-data

eval-check: eval-data
	@$(EVAL_ENV) $(PYTHON) scripts/check_eval_setup.py "$(EVAL_STEM)" $(if $(CHECK_STAGE),--stage $(CHECK_STAGE),)

# For a document that has no schema yet (EVAL_STEM=<stem>): generate one, then a golden set, then
# make golden-density GROUP_CHARS=... to extract and score it.
eval-schema:
	@$(MAKE) --no-print-directory eval-check CHECK_STAGE=schema
	$(EVAL_ENV) $(PYTHON) scripts/generate_schema_for_eval.py "$(EVAL_STEM)" --save-dir "$(EVAL_RUNS_DIR)"

golden-generate:
	@$(MAKE) --no-print-directory eval-check CHECK_STAGE=generate
	$(EVAL_ENV) $(PYTHON) $(EVAL_SCRIPT) "$(EVAL_STEM)" --generate $(EVAL_QUESTIONS) --context-chars $(EVAL_CONTEXT_CHARS) --generate-only

# Does smaller chunk groups (GROUP_CHARS, default 3000) raise the score? Extracts the document again under a
# copy of the active schema as a new version (the old version keeps its graph), in the eval copy only.
eval-reextract:
	@$(MAKE) --no-print-directory eval-check CHECK_STAGE=reextract
	$(EVAL_ENV) $(PYTHON) scripts/reextract_for_eval.py "$(EVAL_STEM)" --group-chars $(GROUP_CHARS) --save-dir "$(EVAL_RUNS_DIR)"

eval-reextract-dry:
	@$(MAKE) --no-print-directory eval-check CHECK_STAGE=reextract
	$(EVAL_ENV) $(PYTHON) scripts/reextract_for_eval.py "$(EVAL_STEM)" --group-chars $(GROUP_CHARS) --dry-run

golden-density: eval-reextract
	$(MAKE) --no-print-directory golden-score EVAL_LABEL="group_chars=$(GROUP_CHARS)"

# Every reading except the LLM judge, no probe: one LLM call per answerable question (the question analysis).
golden-score: eval-check
	$(EVAL_ENV) $(PYTHON) $(EVAL_SCRIPT) "$(EVAL_STEM)" --hops $(EVAL_HOPS) $(EVAL_VERSION_ARG) $(if $(EVAL_LABEL),--label "$(EVAL_LABEL)",) --save-dir "$(EVAL_RUNS_DIR)"

# golden-density at each of GROUP_SIZES in turn (each extracts a new schema version): how the score varies with group size.
golden-curve:
	@for size in $(GROUP_SIZES); do $(MAKE) --no-print-directory golden-density GROUP_CHARS=$$size || exit 1; done

# Try a variant of the extraction instructions on the golden passages that no extraction ever answered
# (QUESTIONS=q016,q025,... to pick others): current prompt vs VARIANT, chunk by chunk.
prompt-probe: eval-check
	$(EVAL_ENV) $(PYTHON) scripts/probe_prompt_variant.py "$(EVAL_STEM)" --variant "$(VARIANT)" $(EVAL_VERSION_ARG) $(if $(QUESTIONS),--questions "$(QUESTIONS)",) --save-dir "$(EVAL_RUNS_DIR)"

golden-eval: eval-check
	$(EVAL_ENV) $(PYTHON) $(EVAL_SCRIPT) "$(EVAL_STEM)" --hops $(EVAL_HOPS) $(EVAL_VERSION_ARG) --probe-misses --save-dir "$(EVAL_RUNS_DIR)"

# Re-extract only the chunk(s) quoting each missed passage (no new golden set): does extraction
# lose facts when given a whole chunk group, rather than the schema being unable to hold them?
# Scores with all readings including the LLM judge (one extra call per answerable question), no probe.
golden-judge: eval-check
	$(EVAL_ENV) $(PYTHON) $(EVAL_SCRIPT) "$(EVAL_STEM)" --hops $(EVAL_HOPS) $(EVAL_VERSION_ARG) --judge --save-dir "$(EVAL_RUNS_DIR)"

golden-probe-chunks: eval-check
	$(EVAL_ENV) $(PYTHON) $(EVAL_SCRIPT) "$(EVAL_STEM)" --hops $(EVAL_HOPS) --probe-misses chunk --save-dir "$(EVAL_RUNS_DIR)"

golden-eval-generate: eval-check
	$(EVAL_ENV) $(PYTHON) $(EVAL_SCRIPT) "$(EVAL_STEM)" --generate $(EVAL_QUESTIONS) --context-chars $(EVAL_CONTEXT_CHARS) --hops $(EVAL_HOPS) --probe-misses --save-dir "$(EVAL_RUNS_DIR)"
