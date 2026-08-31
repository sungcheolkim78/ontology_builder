PYTHON ?= backend/.venv/bin/python
INPUT_DIR ?= docs
OUTPUT_DIR ?= goldenset
QUESTIONS_PER_DOCUMENT ?= 10
MODEL ?=

GOLDENSET_SCRIPT := scripts/prepare_goldenset/prepare_goldenset.py
MODEL_ARG := $(if $(strip $(MODEL)),--model "$(MODEL)",)

.PHONY: help goldenset goldenset-overwrite goldenset-test samsunglife-data samsunglife-data-test pdf-to-md pdf-to-md-test

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
	@echo ""
	@echo "Optional variables:"
	@echo "  OUTPUT_DIR=./goldenset QUESTIONS_PER_DOCUMENT=10 MODEL=openai/gpt-4o-mini"
	@echo "  PYTHON=backend/.venv/bin/python"

goldenset:
	@test -n "$(INPUT_DIR)" || (echo "INPUT_DIR is required" >&2; exit 2)
	$(PYTHON) $(GOLDENSET_SCRIPT) "$(INPUT_DIR)" --output-dir "$(OUTPUT_DIR)" --questions-per-document "$(QUESTIONS_PER_DOCUMENT)" $(MODEL_ARG)

goldenset-overwrite:
	@test -n "$(INPUT_DIR)" || (echo "INPUT_DIR is required" >&2; exit 2)
	$(PYTHON) $(GOLDENSET_SCRIPT) "$(INPUT_DIR)" --output-dir "$(OUTPUT_DIR)" --questions-per-document "$(QUESTIONS_PER_DOCUMENT)" $(MODEL_ARG) --overwrite

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
