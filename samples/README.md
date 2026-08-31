# Sample documents

Five Samsung Life 약관 (insurance terms) documents, already converted to
markdown, so a new user can try the app immediately without running the
PDF-to-markdown conversion pipeline (`scripts/data_prep/`) first.

| File | Category |
| --- | --- |
| `삼성정기보험_2403_약관.md` | 보장성 (term life) |
| `삼성인터넷급여실손의료비보장보험_2607_약관.md` | 보장성 (health/실손) |
| `삼성파워즉시연금보험_2601_1종_약관.md` | 저축성 (annuity) |
| `삼성플래티넘저축보험_2608_약관.md` | 저축성 (savings) |
| `삼성생명우리아이행복보험_약관.md` | 어린이 (children's) |

## Usage

Copy the ones you want directly into `backend/data/` (flat, no
subdirectories — see `CLAUDE.md`'s backend module notes on
`DATA_DIR.iterdir()`), then restart the stack if it's already running
(virtiofs bind-mount gotcha):

```bash
cp samples/*.md backend/data/
podman-compose down && podman-compose up --build -d
```

They'll then show up in the document list in the UI, ready for schema
generation and extraction.

## Provenance

Converted from PDFs downloaded via `scripts/data_prep/download_samsunglife_terms.py`
and `scripts/data_prep/convert_pdfs_to_markdown.py`. The full raw set
(PDFs + all converted markdown) lives in `data/`, which is git-ignored.
