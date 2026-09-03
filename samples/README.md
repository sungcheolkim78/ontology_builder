# Sample documents

Samsung Life 약관 (insurance terms) documents, already converted to
markdown, so a new user can try the app immediately without running the
PDF-to-markdown conversion pipeline (`scripts/data_prep/`) first.

## Quick start: five loose files

Five documents sit here as plain `.md` files for a minimal single-file try:

| File | Category |
| --- | --- |
| `삼성정기보험_2403_약관.md` | 보장성 (term life) |
| `삼성인터넷급여실손의료비보장보험_2607_약관.md` | 보장성 (health/실손) |
| `삼성파워즉시연금보험_2601_1종_약관.md` | 저축성 (annuity) |
| `삼성플래티넘저축보험_2608_약관.md` | 저축성 (savings) |
| `삼성생명우리아이행복보험_약관.md` | 어린이 (children's) |

`backend/data/documents/{stem}_raw/` (holding `raw.md` plus any
`chunks.json`/`goldenset.json`/schema files) is the per-document layout
`app.paths.document_dir_for` expects — see `CLAUDE.md`'s backend module
notes. The easiest way to add one of these is uploading it through the
UI's own upload button, which lays out that folder for you. To place one
by hand instead:

```bash
stem="삼성정기보험_2403_약관"
mkdir -p "backend/data/documents/${stem}_raw"
cp "samples/${stem}.md" "backend/data/documents/${stem}_raw/raw.md"
podman-compose down && podman-compose up --build -d  # virtiofs bind-mount gotcha
```

It'll then show up in the document list in the UI, ready for schema
generation and extraction.

## Full set: three archives

`data_raw_md.tar.gz`, `data_chunks.tar.gz`, and `goldenset.tar.gz` hold the
fuller 15-document set (all three 보장성/저축성/어린이 categories), each
archive one per artifact kind — raw markdown, article-level chunks
(`app.chunking.chunk_markdown_file` output), and per-document golden QA
sets (`app.goldenset`) — mirroring `data/raw/md/`, `data/chunks/`, and
`data/goldenset/` at the repo root (git-ignored; these tarballs are the
committed copies). Not every document has a golden set yet — golden QA
generation is a separate, per-document step (see `app.goldenset`'s
module docstring).

`scripts/unpack_samples.sh` extracts all three into
`backend/data/documents/{stem}_raw/` (creating it if missing), renaming
each file to what `app.paths.document_dir_for` expects (`raw.md`,
`chunks.json`, `goldenset.json`) and re-deriving each document's folder
name the same way `app.parser.parse_to_markdown_file` does (append
`_raw` to the file's own stem) so the result is indistinguishable from
having uploaded each document through the UI:

```bash
./scripts/unpack_samples.sh
podman-compose down && podman-compose up --build -d  # virtiofs bind-mount gotcha
```

Safe to re-run — it only ever overwrites `raw.md`/`chunks.json`/
`goldenset.json` inside a `{stem}_raw/` folder, never touches any other
per-document file (schema versions, manifest, discovery, summary) an
extraction may have since added there.

## Provenance

Converted from PDFs downloaded via `scripts/data_prep/download_samsunglife_terms.py`
and `scripts/data_prep/convert_pdfs_to_markdown.py`. The full raw set
(PDFs + all converted markdown), the chunked JSON, and the golden QA sets
live under `data/` at the repo root, which is git-ignored — the archives
here are what make that data available to a fresh clone.
