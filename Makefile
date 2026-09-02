PYTHON ?= backend/.venv/bin/python
INPUT_DIR ?= data/raw/md
OUTPUT_DIR ?= data/goldenset
QUESTIONS_PER_DOCUMENT ?= 10
MODEL ?= nvidia/nemotron-3-ultra-550b-a55b:free
QUESTION_CONTEXT_CHARS ?= 24000
LOG_FILE ?=
MAX_PROC_FILEN ?=

GOLDENSET_SCRIPT := scripts/prepare_goldenset/prepare_goldenset.py
MODEL_ARG := $(if $(strip $(MODEL)),--model "$(MODEL)",)
LOG_ARG := $(if $(strip $(LOG_FILE)),--log-file "$(LOG_FILE)",)
MAX_FILES_ARG := $(if $(strip $(MAX_PROC_FILEN)),--max-process-files "$(MAX_PROC_FILEN)",)

.PHONY: help goldenset goldenset-overwrite goldenset-test samsunglife-data samsunglife-data-test pdf-to-md pdf-to-md-test chunk-terms chunk-terms-test

help:
	@echo "Available targets:"
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
	@echo "  make pdf-to-md-test"
	@echo "      Run unit tests for the PDF-to-Markdown converter."
	@echo "  make chunk-terms"
	@echo "      Chunk data/raw/md files into per-article JSON under data/chunks."
	@echo "  make chunk-terms-test"
	@echo "      Run unit tests for the Markdown article chunker."
	@echo ""
	@echo "Optional variables:"
	@echo "  OUTPUT_DIR=./goldenset QUESTIONS_PER_DOCUMENT=10 MODEL=openai/gpt-4o-mini"
	@echo "  QUESTION_CONTEXT_CHARS=24000 LOG_FILE=./goldenset/run.log"
	@echo "  MAX_PROC_FILEN=10"
	@echo "  PYTHON=backend/.venv/bin/python"

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
	python3 scripts/data_prep/convert_pdfs_to_markdown.py

pdf-to-md-test:
	PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest \
		scripts/data_prep/test_convert_pdfs_to_markdown.py -q

chunk-terms:
	python3 scripts/data_prep/chunk_terms_markdown.py --input-dir "$(INPUT_DIR)" --output-dir data/chunks

chunk-terms-test:
	PYTHONDONTWRITEBYTECODE=1 python3 -B -m pytest \
		scripts/data_prep/test_chunk_terms_markdown.py -q
