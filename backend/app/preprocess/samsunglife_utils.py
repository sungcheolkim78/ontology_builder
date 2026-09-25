"""Samsung Life (삼성생명) 약관 (policy-terms document) lookup/download utilities.

Extracted from `scripts/data_prep/download_samsunglife_terms.py`'s
product-search and PDF-download logic -- just the three operations a future
File Explorer feature needs (list what's available, download one named
document, search-then-download), without that script's own manifest /
incremental-corpus-growth bookkeeping (`--per-category`, `manifest.json`
merge/re-download-missing), which only makes sense for its offline bulk-corpus
CLI, not an on-demand single-document fetch triggered from the UI.

`download_term`'s returned dict is the metadata this app records per
download (from Samsung Life's own product-disclosure API fields, via
`SamsungLifeTerm`, plus what the download itself produces -- source URL,
local path, size, checksum). Add fields here (and to `SamsungLifeTerm`/
`SamsungLifeTerm.from_api` upstream, if a field comes from the product API
rather than the download step) as more metadata is needed.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

SITE_BASE = "https://www.samsunglife.com"
PCMS_BASE = "https://pcms.samsunglife.com"
CURRENT_PRODUCTS_API = (
    f"{SITE_BASE}/gw/api/product/disclosure/product/prdt/salesPrdtList"
)
ALL_PRODUCTS_API = (
    f"{SITE_BASE}/gw/api/product/disclosure/product/prdt/salesAllPrdtList"
)
VIEWER_URL = f"{PCMS_BASE}/XView.do"

DEFAULT_CATEGORIES = ("보장성", "저축성", "어린이")
USER_AGENT = "ontology-builder-data-prep/1.0"
PDF_PATH_PATTERN = re.compile(r'"filepath"\s*:\s*["\']([^"\']+\.pdf)["\']')


@dataclass(frozen=True)
class SamsungLifeTerm:
    """One 약관, as listed by Samsung Life's product-disclosure API."""

    category: str
    name: str
    goods_code: str
    sale_date: str
    document_id: str
    status: str
    classification: str
    currently_listed: bool

    @classmethod
    def from_api(
        cls, category: str, row: dict[str, Any], currently_listed: bool
    ) -> "SamsungLifeTerm":
        return cls(
            category=category,
            name=str(row.get("goodsName", "")).strip(),
            goods_code=str(row.get("goodsCode", "")).strip(),
            sale_date=str(row.get("fromdate", "")).strip(),
            document_id=str(row.get("filename3", "")).strip(),
            status=str(row.get("status", "")).strip(),
            classification=str(row.get("gubun", "")).strip(),
            currently_listed=currently_listed,
        )


class SamsungLifeDownloadError(RuntimeError):
    pass


def _request_bytes(
    url: str,
    *,
    form: dict[str, Any] | None = None,
    timeout: float = 30,
    retries: int = 2,
) -> bytes:
    data = None
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if form is not None:
        data = urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    request = Request(url, data=data, headers=headers)

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
    raise SamsungLifeDownloadError(
        f"request failed after {retries + 1} attempts: {url}: {last_error}"
    )


def _product_form(
    category: str, page: int, page_size: int, *, name_filter: str = ""
) -> dict[str, Any]:
    return {
        "mCode": "개인",
        "gCode": category,
        "sCode": " ",
        "searchYear": "",
        "goodsName": name_filter,
        "pageNo": page,
        "pageRows": page_size,
    }


def _fetch_product_page(
    api_url: str,
    category: str,
    page: int,
    page_size: int,
    timeout: float,
    *,
    name_filter: str = "",
) -> tuple[list[dict[str, Any]], int]:
    raw = _request_bytes(
        api_url,
        form=_product_form(category, page, page_size, name_filter=name_filter),
        timeout=timeout,
    )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SamsungLifeDownloadError(
            f"invalid JSON returned for {category} page {page}"
        ) from exc
    if str(payload.get("code")) != "200":
        raise SamsungLifeDownloadError(
            f"product API failed for {category}: {payload.get('message') or payload.get('response')}"
        )
    rows = payload.get("response") or []
    if not isinstance(rows, list):
        raise SamsungLifeDownloadError(f"unexpected product response for {category}")
    total = int(rows[0].get("totalRows", len(rows))) if rows else 0
    return rows, total


def _valid_term(term: SamsungLifeTerm) -> bool:
    return bool(term.name and term.goods_code and term.sale_date and term.document_id)


def _unique_terms(terms: list[SamsungLifeTerm]) -> list[SamsungLifeTerm]:
    seen: set[str] = set()
    result: list[SamsungLifeTerm] = []
    for term in terms:
        key = unicodedata.normalize("NFKC", term.name).casefold()
        if key in seen or not _valid_term(term):
            continue
        seen.add(key)
        result.append(term)
    return result


def _fetch_current_terms(
    category: str, timeout: float, *, name_filter: str = ""
) -> list[SamsungLifeTerm]:
    rows, _ = _fetch_product_page(
        CURRENT_PRODUCTS_API, category, 1, 500, timeout, name_filter=name_filter
    )
    terms = [SamsungLifeTerm.from_api(category, row, True) for row in rows]
    return _unique_terms(terms)


def _fetch_all_terms(
    category: str, timeout: float, page_size: int = 100, *, name_filter: str = ""
) -> list[SamsungLifeTerm]:
    first_rows, total = _fetch_product_page(
        ALL_PRODUCTS_API, category, 1, page_size, timeout, name_filter=name_filter
    )
    rows = list(first_rows)
    page_count = (total + page_size - 1) // page_size
    for page in range(2, page_count + 1):
        page_rows, _ = _fetch_product_page(
            ALL_PRODUCTS_API, category, page, page_size, timeout, name_filter=name_filter
        )
        rows.extend(page_rows)
    terms = [SamsungLifeTerm.from_api(category, row, False) for row in rows]
    terms.sort(key=lambda term: term.sale_date, reverse=True)
    return _unique_terms(terms)


def list_terms(
    category: str,
    *,
    include_historical: bool = False,
    timeout: float = 30,
    name_filter: str = "",
) -> list[SamsungLifeTerm]:
    """List 약관 available for `category`.

    Only currently-on-sale products by default; pass `include_historical=True`
    to also fetch past (no-longer-sold) products via the paginated "all
    products" API, appended after the current ones with duplicates (by
    NFKC/casefolded name) dropped.

    `name_filter`, when non-empty, is sent straight to Samsung Life's own API
    (its `goodsName` form field) so the filtering happens server-side --
    confirmed experimentally against the live API to already do the same
    substring/case-insensitive match this module re-applies locally (e.g.
    `goodsName=료비`, a mid-word fragment, matches `실손의료비보장보험`
    server-side exactly like our own check does). Leaving it empty (the
    default) fetches the category's full list, unfiltered, as before --
    for one category with no filter, the historical ("all products") side
    alone can be ~2900 rows / ~30 paginated requests, which is what made
    every search fetch the entire catalog before this parameter existed.
    """
    current = _fetch_current_terms(category, timeout, name_filter=name_filter)
    if not include_historical:
        return current
    seen = {unicodedata.normalize("NFKC", term.name).casefold() for term in current}
    historical = [
        term
        for term in _fetch_all_terms(category, timeout, name_filter=name_filter)
        if unicodedata.normalize("NFKC", term.name).casefold() not in seen
    ]
    return current + historical


def find_terms_by_name(
    query: str,
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
    *,
    include_historical: bool = True,
    timeout: float = 30,
) -> list[SamsungLifeTerm]:
    """Search `categories` for terms whose name contains `query`.

    Passes `query` through as `list_terms`'s `name_filter` so each category
    fetch is already narrowed server-side (see there) instead of pulling the
    full catalog and filtering here -- this local substring/NFKC check stays
    as a defensive re-check on the now-small result set, not as the primary
    filter, so behavior is unchanged if the server's own matching ever turns
    out looser than ours for some edge case.
    """
    key = unicodedata.normalize("NFKC", query).casefold()
    matches: list[SamsungLifeTerm] = []
    for category in categories:
        for term in list_terms(
            category,
            include_historical=include_historical,
            timeout=timeout,
            name_filter=query,
        ):
            if key in unicodedata.normalize("NFKC", term.name).casefold():
                matches.append(term)
    return matches


def _resolve_pdf_url(term: SamsungLifeTerm, timeout: float) -> str:
    query = urlencode(
        {
            "docID": term.document_id,
            "name": term.name,
            "isDown": "false",
            "loadingType": "1",
            "contentType": "",
        }
    )
    viewer_html = _request_bytes(f"{VIEWER_URL}?{query}", timeout=timeout).decode(
        "utf-8", errors="replace"
    )
    match = PDF_PATH_PATTERN.search(viewer_html)
    if not match:
        raise SamsungLifeDownloadError(
            f"PDF path not found in viewer response: {term.name} ({term.document_id})"
        )
    return urljoin(f"{PCMS_BASE}/", match.group(1))


def _safe_filename(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name)
    normalized = re.sub(r'[\\/:*?"<>|\[\]()]', "_", normalized)
    normalized = re.sub(r"\s+", "_", normalized).strip("._ ")
    normalized = re.sub(r"_+", "_", normalized)
    return (normalized[:180] or "insurance_terms") + "_약관.pdf"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_term(
    term: SamsungLifeTerm,
    output_dir: Path,
    *,
    timeout: float = 30,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Download one 약관's PDF to `output_dir/{category}/{safe_filename}`.

    Returns a metadata dict combining `term`'s own fields with what the
    download step produces (source URL, local path, size, checksum) -- see
    this module's docstring for extending it with more meta info later.
    """
    pdf_url = _resolve_pdf_url(term, timeout)
    target = output_dir / term.category / _safe_filename(term.name)
    target.parent.mkdir(parents=True, exist_ok=True)

    if overwrite or not target.exists():
        content = _request_bytes(pdf_url, timeout=timeout, retries=3)
        if not content.startswith(b"%PDF-"):
            raise SamsungLifeDownloadError(
                f"downloaded content is not a PDF: {term.name}"
            )
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.write_bytes(content)
        temporary.replace(target)

    return {
        **asdict(term),
        "source_url": pdf_url,
        "local_file": str(target),
        "size_bytes": target.stat().st_size,
        "sha256": _sha256_file(target),
    }


def download_term_by_name(
    name: str,
    output_dir: Path,
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
    *,
    include_historical: bool = True,
    timeout: float = 30,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Download the 약관 whose name exactly matches `name`.

    Matching is NFKC/casefold-insensitive but otherwise exact (not
    substring), since the caller already knows the specific document they
    want -- use `find_terms_by_name`/`search_and_download_terms` instead
    when the name is a search query rather than a known exact title. Raises
    `SamsungLifeDownloadError` if zero or more than one distinct term
    matches across `categories`, since silently picking one in either case
    would download the wrong document.
    """
    key = unicodedata.normalize("NFKC", name).casefold()
    matches = [
        term
        for category in categories
        for term in list_terms(
            category,
            include_historical=include_historical,
            timeout=timeout,
            name_filter=name,
        )
        if unicodedata.normalize("NFKC", term.name).casefold() == key
    ]
    if not matches:
        raise SamsungLifeDownloadError(f"no term found named {name!r}")
    if len(matches) > 1:
        categories_found = ", ".join(sorted({m.category for m in matches}))
        raise SamsungLifeDownloadError(
            f"{len(matches)} terms match {name!r} across categories ({categories_found}); "
            "narrow `categories` to disambiguate"
        )
    return download_term(matches[0], output_dir, timeout=timeout, overwrite=overwrite)


def search_and_download_terms(
    query: str,
    output_dir: Path,
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
    *,
    include_historical: bool = True,
    timeout: float = 30,
    overwrite: bool = False,
) -> list[dict[str, Any]]:
    """Search `categories` for terms matching `query`, then download every match.

    Pairs `find_terms_by_name` with `download_term` for the common "search by
    name" flow; returns one metadata dict (see `download_term`) per matched
    term, in the same order `find_terms_by_name` returned them.
    """
    matches = find_terms_by_name(
        query, categories, include_historical=include_historical, timeout=timeout
    )
    return [
        download_term(term, output_dir, timeout=timeout, overwrite=overwrite)
        for term in matches
    ]
