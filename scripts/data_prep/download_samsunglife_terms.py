#!/usr/bin/env python3
"""Download Samsung Life individual-insurance policy PDFs by category.

The script uses Samsung Life's public product-disclosure API and PCMS viewer.
--per-category is how many NEW distinct products to fetch per category on
this run, skipping ones already recorded in manifest.json -- so re-running
the script grows the corpus over time instead of re-selecting the same top
N products every time. It prefers products that are currently on sale and
supplements them with the most recent historical products when a category
doesn't have enough new ones left. Every run also consults any existing
manifest.json for entries whose local file is missing on disk (never
actually downloaded, e.g. from a --dry-run, or removed since) and
re-downloads those using their already-resolved URL. --overwrite bypasses
the "already downloaded" skip and re-selects/re-fetches the current top N
per category instead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
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
class Product:
    category: str
    name: str
    goods_code: str
    sale_date: str
    document_id: str
    status: str
    classification: str
    currently_listed: bool

    @classmethod
    def from_api(cls, category: str, row: dict[str, Any], currently_listed: bool) -> "Product":
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


class DownloadError(RuntimeError):
    pass


def request_bytes(
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
    raise DownloadError(f"request failed after {retries + 1} attempts: {url}: {last_error}")


def product_form(category: str, page: int, page_size: int) -> dict[str, Any]:
    return {
        "mCode": "개인",
        "gCode": category,
        "sCode": " ",
        "searchYear": "",
        "goodsName": "",
        "pageNo": page,
        "pageRows": page_size,
    }


def fetch_product_page(
    api_url: str,
    category: str,
    page: int,
    page_size: int,
    timeout: float,
) -> tuple[list[dict[str, Any]], int]:
    raw = request_bytes(
        api_url,
        form=product_form(category, page, page_size),
        timeout=timeout,
    )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DownloadError(f"invalid JSON returned for {category} page {page}") from exc
    if str(payload.get("code")) != "200":
        raise DownloadError(
            f"product API failed for {category}: {payload.get('message') or payload.get('response')}"
        )
    rows = payload.get("response") or []
    if not isinstance(rows, list):
        raise DownloadError(f"unexpected product response for {category}")
    total = int(rows[0].get("totalRows", len(rows))) if rows else 0
    return rows, total


def valid_product(product: Product) -> bool:
    return bool(product.name and product.goods_code and product.sale_date and product.document_id)


def unique_products(products: Iterable[Product]) -> list[Product]:
    seen: set[str] = set()
    result: list[Product] = []
    for product in products:
        key = unicodedata.normalize("NFKC", product.name).casefold()
        if key in seen or not valid_product(product):
            continue
        seen.add(key)
        result.append(product)
    return result


def fetch_current_products(category: str, timeout: float) -> list[Product]:
    rows, _ = fetch_product_page(CURRENT_PRODUCTS_API, category, 1, 500, timeout)
    products = (Product.from_api(category, row, True) for row in rows)
    return unique_products(products)


def fetch_all_products(category: str, timeout: float, page_size: int = 100) -> list[Product]:
    first_rows, total = fetch_product_page(ALL_PRODUCTS_API, category, 1, page_size, timeout)
    rows = list(first_rows)
    page_count = (total + page_size - 1) // page_size
    for page in range(2, page_count + 1):
        page_rows, _ = fetch_product_page(ALL_PRODUCTS_API, category, page, page_size, timeout)
        rows.extend(page_rows)
    products = [Product.from_api(category, row, False) for row in rows]
    products.sort(key=lambda product: product.sale_date, reverse=True)
    return unique_products(products)


def select_products(
    category: str,
    count: int,
    timeout: float,
    exclude: frozenset[str] = frozenset(),
) -> list[Product]:
    """Select up to `count` distinct products not already in `exclude`.

    `exclude` holds NFKC-casefolded product names (typically ones already
    present in a prior run's manifest) so repeated runs fetch new products
    instead of re-selecting the same currently-listed top N every time.
    """
    current = [
        product
        for product in fetch_current_products(category, timeout)
        if unicodedata.normalize("NFKC", product.name).casefold() not in exclude
    ]
    if len(current) >= count:
        return current[:count]

    selected = list(current)
    selected_names = {
        unicodedata.normalize("NFKC", product.name).casefold() for product in selected
    } | set(exclude)
    try:
        historical = fetch_all_products(category, timeout)
    except Exception as exc:
        print(
            f"[{category}] warning: failed to load historical products, "
            f"falling back to {len(selected)} currently listed product(s): {exc}",
            file=sys.stderr,
        )
        return selected
    for product in historical:
        key = unicodedata.normalize("NFKC", product.name).casefold()
        if key in selected_names:
            continue
        selected.append(product)
        selected_names.add(key)
        if len(selected) >= count:
            break
    return selected


def resolve_pdf_url(product: Product, timeout: float) -> str:
    query = urlencode(
        {
            "docID": product.document_id,
            "name": product.name,
            "isDown": "false",
            "loadingType": "1",
            "contentType": "",
        }
    )
    viewer_html = request_bytes(f"{VIEWER_URL}?{query}", timeout=timeout).decode(
        "utf-8", errors="replace"
    )
    match = PDF_PATH_PATTERN.search(viewer_html)
    if not match:
        raise DownloadError(
            f"PDF path not found in viewer response: {product.name} ({product.document_id})"
        )
    return urljoin(f"{PCMS_BASE}/", match.group(1))


def safe_filename(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name)
    normalized = re.sub(r"[\\/:*?\"<>|\[\]()]", "_", normalized)
    normalized = re.sub(r"\s+", "_", normalized).strip("._ ")
    normalized = re.sub(r"_+", "_", normalized)
    return (normalized[:180] or "insurance_terms") + "_약관.pdf"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_product(
    product: Product,
    output_dir: Path,
    timeout: float,
    overwrite: bool,
    dry_run: bool,
) -> dict[str, Any]:
    pdf_url = resolve_pdf_url(product, timeout)
    target = output_dir / product.category / safe_filename(product.name)
    target.parent.mkdir(parents=True, exist_ok=True)

    action = "downloaded"
    if dry_run:
        action = "dry_run"
    elif target.exists() and not overwrite:
        action = "reused"
    else:
        content = request_bytes(pdf_url, timeout=timeout, retries=3)
        if not content.startswith(b"%PDF-"):
            raise DownloadError(f"downloaded content is not a PDF: {product.name}")
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.write_bytes(content)
        temporary.replace(target)

    size = target.stat().st_size if target.exists() else None
    checksum = sha256_file(target) if target.exists() else None
    return {
        "category": product.category,
        "product_name": product.name,
        "goods_code": product.goods_code,
        "sale_date": product.sale_date,
        "status": product.status,
        "classification": product.classification,
        "currently_listed": product.currently_listed,
        "document_id": product.document_id,
        "source_url": pdf_url,
        "local_file": str(target),
        "size_bytes": size,
        "sha256": checksum,
        "action": action,
    }


def missing_download_records(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Manifest entries whose local file is absent (never downloaded, or removed since)."""
    return [
        record
        for record in files
        if record.get("local_file") and not Path(record["local_file"]).exists()
    ]


def redownload_from_record(record: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Re-download a manifest entry's PDF using its already-resolved source_url."""
    source_url = record.get("source_url")
    if not source_url:
        raise DownloadError(f"no source_url recorded for {record.get('product_name')}")

    target = Path(record["local_file"])
    target.parent.mkdir(parents=True, exist_ok=True)
    content = request_bytes(source_url, timeout=timeout, retries=3)
    if not content.startswith(b"%PDF-"):
        raise DownloadError(f"downloaded content is not a PDF: {record.get('product_name')}")
    temporary = target.with_suffix(target.suffix + ".part")
    temporary.write_bytes(content)
    temporary.replace(target)

    updated = dict(record)
    updated["size_bytes"] = target.stat().st_size
    updated["sha256"] = sha256_file(target)
    updated["action"] = "downloaded"
    return updated


def load_existing_manifest(manifest_path: Path) -> dict[str, Any] | None:
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"warning: ignoring unreadable manifest {manifest_path}: {exc}", file=sys.stderr)
        return None


def file_record_key(record: dict[str, Any]) -> tuple[str, str]:
    name = unicodedata.normalize("NFKC", str(record.get("product_name", ""))).casefold()
    return (str(record.get("category", "")), name)


def merge_file_records(
    existing: list[dict[str, Any]], new: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged = list(existing)
    index_by_key = {file_record_key(record): i for i, record in enumerate(merged)}
    for record in new:
        key = file_record_key(record)
        if key in index_by_key:
            merged[index_by_key[key]] = record
        else:
            index_by_key[key] = len(merged)
            merged.append(record)
    return merged


def downloaded_names_by_category(files: list[dict[str, Any]]) -> dict[str, set[str]]:
    by_category: dict[str, set[str]] = {}
    for record in files:
        category = str(record.get("category", ""))
        name = unicodedata.normalize("NFKC", str(record.get("product_name", ""))).casefold()
        by_category.setdefault(category, set()).add(name)
    return by_category


def merge_categories(existing: list[str], requested: list[str]) -> list[str]:
    merged = list(existing)
    for category in requested:
        if category not in merged:
            merged.append(category)
    return merged


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download Samsung Life individual-insurance policy PDFs."
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/raw/pdf"), help="output directory"
    )
    parser.add_argument(
        "--per-category",
        type=int,
        default=5,
        help="new distinct products to fetch per category, skipping ones already in manifest.json",
    )
    parser.add_argument(
        "--categories",
        nargs="+",
        default=list(DEFAULT_CATEGORIES),
        help="individual-insurance middle categories",
    )
    parser.add_argument("--timeout", type=float, default=30, help="request timeout in seconds")
    parser.add_argument("--overwrite", action="store_true", help="replace existing PDFs")
    parser.add_argument(
        "--dry-run", action="store_true", help="resolve products and URLs without saving PDFs"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.per_category < 1:
        print("error: --per-category must be at least 1", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("error: --timeout must be positive", file=sys.stderr)
        return 2

    output_dir = args.output_dir.resolve()
    records: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    manifest_path = output_dir / "manifest.json"
    existing_manifest = load_existing_manifest(manifest_path)
    existing_files = existing_manifest.get("files", []) if existing_manifest else []
    already_downloaded = (
        {} if args.overwrite else downloaded_names_by_category(existing_files)
    )

    for category in args.categories:
        exclude = frozenset(already_downloaded.get(category, ()))
        print(
            f"[{category}] selecting {args.per_category} new product(s) "
            f"({len(exclude)} already downloaded)"
        )
        try:
            products = select_products(category, args.per_category, args.timeout, exclude=exclude)
        except Exception as exc:
            failures.append({"category": category, "error": str(exc)})
            print(f"[{category}] failed to load products: {exc}", file=sys.stderr)
            continue

        if len(products) < args.per_category:
            print(
                f"[{category}] warning: only {len(products)} new distinct product(s) were found",
                file=sys.stderr,
            )
        for index, product in enumerate(products, 1):
            print(f"[{category}] {index}/{len(products)} {product.name}")
            try:
                records.append(
                    download_product(
                        product,
                        output_dir,
                        args.timeout,
                        args.overwrite,
                        args.dry_run,
                    )
                )
            except Exception as exc:
                failures.append(
                    {"category": category, "product_name": product.name, "error": str(exc)}
                )
                print(f"[{category}] download failed: {product.name}: {exc}", file=sys.stderr)

    missing_records = missing_download_records(existing_files)
    if missing_records:
        if args.dry_run:
            print(f"skipping re-download of {len(missing_records)} missing file(s) (dry run)")
        else:
            for record in missing_records:
                category = record.get("category", "")
                name = record.get("product_name", "")
                print(f"[{category}] re-downloading missing file: {name}")
                try:
                    records.append(redownload_from_record(record, args.timeout))
                except Exception as exc:
                    failures.append({"category": category, "product_name": name, "error": str(exc)})
                    print(f"[{category}] re-download failed: {name}: {exc}", file=sys.stderr)

    now = datetime.now(timezone.utc).isoformat()

    merged_files = merge_file_records(existing_files, records)
    merged_categories = merge_categories(
        existing_manifest.get("requested_categories", []) if existing_manifest else [],
        args.categories,
    )

    manifest = {
        "format_version": 1,
        "created_at": (existing_manifest or {}).get("created_at", now),
        "updated_at": now,
        "source": "Samsung Life product disclosure",
        "source_page": (
            f"{SITE_BASE}/individual/products/disclosure/sales/PDO-PRPRI010110M"
        ),
        "major_category": "개인",
        "requested_categories": merged_categories,
        "requested_per_category": args.per_category,
        "dry_run": args.dry_run,
        "files": merged_files,
        "failures": failures,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"done: {len(records)} succeeded, {len(failures)} failed; "
        f"manifest: {manifest_path} ({len(merged_files)} total file(s))"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

