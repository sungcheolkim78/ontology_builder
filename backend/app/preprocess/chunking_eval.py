"""Scores how well `app.preprocess.chunking.chunk_markdown` recovered a policy's
structure (main contract, riders, 관/편 hierarchy, 조 sequence).

The reference is the document's own "상품 목차" block -- the product table of
contents near the front, a list of `name page` lines under 주계약 / 특약 /
제도성특약 / 유첨 / 부록 headings. It is used only here, never by the chunker, so
a wrong table of contents cannot leak into the chunks it is meant to check.

Four readings are reported side by side and never merged into one score:
rider names against the table of contents, 조 numbering within each rider,
the 관/편 hierarchy, and labels that look like body text rather than a name.
Free and deterministic: no model call.
"""

from __future__ import annotations

import re

PRODUCT_TOC_TITLE = re.compile(r"^(?:#+\s*)?상품\s*목차\s*$")
_TOC_ENTRY = re.compile(r"^(?P<name>.+?)\s+(?P<page>\d{1,4})$")
_PAGE_MARKER = re.compile(r"^<!--\s*page:")

# TOC heading -> what the entries under it are.
_TOC_SECTIONS = (
    ("주계약", "main"),
    ("특약일반사항", "general"),
    ("제도성특약", "rider"),
    ("특약", "rider"),
    ("유첨", "attachment"),
    ("부록", "appendix"),
)

_MIDDLE_DOTS = str.maketrans({"∙": "·", "ㆍ": "·", "•": "·", "‧": "·"})
_FALLBACK_LABEL = re.compile(r"^특약_\d+$")


def normalize_rider_name(name: str) -> str:
    """Comparable form of a rider name: no heading marker, no spaces, one kind
    of middle dot, no trailing '약관'."""
    name = name.strip().lstrip("#").strip().translate(_MIDDLE_DOTS)
    name = re.sub(r"\s+", "", name).removesuffix("약관")
    # the table of contents leaves out a middle dot the title line keeps
    # ("운전탑승부담보" / "운전·탑승부담보"), so the dot is not part of the name
    return name.replace("·", "")


def _brackets_balance(text: str) -> bool:
    return text.count("(") == text.count(")") and text.count("[") == text.count("]")


def parse_product_toc(text: str) -> dict | None:
    """The 상품 목차 block as {"main": str, "general": [..], "riders": [..],
    "attachments": [..], "appendix": [..]}, or None when the document has no
    such block (the caller reports the table of contents as unusable)."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if PRODUCT_TOC_TITLE.match(line.strip()):
            # the last one wins: the front matter also names it in a list
            following = [item.strip() for item in lines[index + 1 : index + 4]]
            if "주계약" in following:
                start = index
    if start is None:
        return None

    toc: dict = {"main": None, "general": [], "riders": [], "attachments": [], "appendix": []}
    keys = {"general": "general", "rider": "riders", "attachment": "attachments", "appendix": "appendix"}
    kind = None
    wrapped = ""  # head of an entry whose name wrapped onto the next line
    at_page_start = False
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped == "---" or _PAGE_MARKER.match(stripped):
            at_page_start = True
            continue
        if not stripped:
            continue
        heading = next(
            (k for title, k in _TOC_SECTIONS if stripped == title or stripped.startswith(f"{title} ※")),
            None,
        )
        match = _TOC_ENTRY.match(stripped)
        if at_page_start:
            # the list goes on over the page break only while the next page
            # opens with another entry, a heading, or the head of a wrapped
            # rider name; anything else ends it
            if heading is None and match is None and not wrapped and "특약" not in stripped:
                break
            at_page_start = False
        if heading is not None:
            kind, wrapped = heading, ""
            continue
        if match is None:
            wrapped = "" if stripped.startswith(("※", "[")) else wrapped + stripped
            # the converter sometimes loses the page number of a wrapped entry:
            # a name whose brackets close is then complete without one
            if kind in ("rider", "attachment") and "특약" in wrapped and _brackets_balance(wrapped):
                toc[keys[kind]].append(wrapped)
                wrapped = ""
            continue
        if kind is None:
            continue
        name = wrapped + match.group("name").strip()
        wrapped = ""
        if kind == "main":
            toc["main"] = name
        else:
            toc[keys[kind]].append(name)
    return toc if toc["main"] else None


def rider_name_of(chunk: dict) -> str:
    """The rider a chunk belongs to: the chunker's `rider_name` when it records
    one, else the older `section_label`."""
    return chunk.get("rider_name") or chunk.get("section_label") or ""


def _sections(chunks: list[dict]) -> list[tuple[int, str, list[dict]]]:
    grouped: dict[int, list[dict]] = {}
    for chunk in chunks:
        grouped.setdefault(chunk["section_index"], []).append(chunk)
    return [(index, rider_name_of(items[0]), items) for index, items in sorted(grouped.items())]


def looks_like_body_text(label: str) -> bool:
    """A rider label that is a sentence, a bullet or a leftover marker, not a name."""
    label = label.strip()
    if not label or label.startswith(("-", "#", "※", "·", "|")):
        return True
    return len(label) > 90 or label.endswith(("다.", "니다", "습니다")) or "특약" not in label


def score_rider_names(chunks: list[dict], toc: dict | None) -> dict:
    # the cited laws of the appendix are sections too, but not riders to name
    appendix = [s for s in _sections(chunks) if s[2][0].get("rider_type") == "appendix"]
    sections = [s for s in _sections(chunks) if s[2][0].get("rider_type") != "appendix"]
    found = [normalize_rider_name(name) for _, name, _ in sections]
    fallback = [name for _, name, _ in sections if _FALLBACK_LABEL.match(name)]
    suspicious = [
        name
        for position, (_, name, _) in enumerate(sections)
        if position > 0 and not _FALLBACK_LABEL.match(name) and looks_like_body_text(name)
    ]
    result = {
        "sections": len(sections),
        "appendix_sections": len(appendix),
        "fallback_labels": len(fallback),
        "suspicious_labels": suspicious,
        "toc_available": toc is not None,
    }
    if toc is None:
        return result
    # the common part ("[특약약관]제1편일반사항") is a section of its own, listed apart
    expected = [normalize_rider_name(name) for name in toc["general"] + toc["riders"]]
    expected_set, found_set = set(expected), set(found[1:])
    result.update(
        {
            "toc_riders": len(expected),  # riders and general parts
            "matched": len(expected_set & found_set),
            "missing": sorted(expected_set - found_set),
            "extra": sorted(found_set - expected_set),
            "chunk_sections_vs_toc": f"{len(sections)} vs {1 + len(expected)}",
        }
    )
    return result


def _join(number: tuple[int, ...]) -> str:
    return "-".join(str(part) for part in number)


def _is_gap(previous: tuple[int, ...], number: tuple[int, ...]) -> bool:
    """`제12조` after `제10조` skips one; `제2-4조` after `제2-2조` too. A new part
    (`제3-1조` after `제2-9조`) restarts at 1; only an article that is neither
    the next in its part nor the first of the next part is a gap."""
    if len(previous) == 1 or len(number) == 1:
        return number[0] > previous[0] + 1
    if number[0] == previous[0]:
        return number[1] > previous[1] + 1
    return number[0] > previous[0] + 1 or number[1] != 1


def score_article_sequence(chunks: list[dict]) -> dict:
    """Within each rider, 조 numbers should run 1, 2, 3 ...; `제N조의M` may repeat N."""
    duplicates = gaps = regressions = 0
    details: list[str] = []
    for index, name, items in _sections(chunks):
        seen: set[tuple] = set()
        previous = None
        variant = None
        for chunk in items:
            if chunk.get("variant") != variant:  # a plan numbers its articles over again
                variant, previous = chunk.get("variant"), None
            number = tuple(int(part) for part in chunk["article_no"].split("-"))
            key = (chunk["article_no"], chunk.get("sub_no"), chunk.get("variant"))
            if key in seen:
                duplicates += 1
            seen.add(key)
            if previous is not None:
                if number < previous:
                    regressions += 1
                    details.append(f"{index}:{name[:20]} 제{_join(previous)}조 -> 제{_join(number)}조")
                elif _is_gap(previous, number) and items[0].get("rider_type") != "appendix":
                    # a cited law is quoted in excerpts, so gaps there are expected
                    gaps += 1
            previous = number
    return {
        "chunks": len(chunks),
        "duplicate_articles": duplicates,
        "gaps": gaps,
        "regressions": regressions,
        "regression_examples": details[:5],
    }


# Any line that reads as an article heading, with or without the markdown `###`,
# numbered `제12조`, `제12조의2` or `제2-1조`. Anchored to the whole line, like the
# chunker's own pattern, so a table-of-contents line (page number after the
# title) and a cross-reference inside a sentence are not candidates.
_PARENS = r"(?:[^()]|\([^()]*\))*"
_ARTICLE_CANDIDATE = re.compile(
    r"^(?:###\s*제\s*\d+(?:-\d+)?\s*조(?:\s*의\s*\d+)?\s*(?:\[.+\]|\(" + _PARENS + r"\))"
    r"|제\s*\d+(?:-\d+)?\s*조(?:\s*의\s*\d+)?\s*(?:\[[^\[\]]+\]|\(" + _PARENS + r"\)))\s*$"
)


def score_article_coverage(chunk_result: dict, raw_text: str) -> dict:
    """Lines of raw text that look like an article heading versus the ones the
    chunker turned into a chunk: the rest are articles it dropped into the
    previous chunk's body."""
    started = {chunk["line_start"] for chunk in chunk_result["chunks"]}
    # what comes before the contract's first chunk is front matter (the table of
    # contents has heading-shaped lines), not articles that were missed
    first = min(started, default=0)
    candidates = [
        number
        for number, line in enumerate(raw_text.splitlines(), start=1)
        if number >= first and _ARTICLE_CANDIDATE.match(line.strip())
    ]
    missed = [number for number in candidates if number not in started]
    lines = raw_text.splitlines()
    return {
        "heading_lines": len(candidates),
        "chunked": len(candidates) - len(missed),
        "missed": len(missed),
        "missed_examples": [f"{number}: {lines[number - 1].strip()[:50]}" for number in missed[:5]],
    }


def score_hierarchy(chunks: list[dict]) -> dict:
    """The 편/관 stack of each chunk must never go backwards inside a rider (a
    관 number that drops is a running header or a table-of-contents line taken
    for a heading) and must hold at most one entry per level."""
    regressions = repeated_level = 0
    examples: list[str] = []
    for index, name, items in _sections(chunks):
        last_by_level: dict[str, int] = {}
        for chunk in items:
            levels = [item["level"] for item in chunk.get("ancestors", [])]
            if len(levels) != len(set(levels)):
                repeated_level += 1
            for item in chunk.get("ancestors", []):
                number = int(item["no"])
                if number < last_by_level.get(item["level"], 0):
                    regressions += 1
                    examples.append(f"{chunk['id']} 제{number}{item['level']} after {last_by_level[item['level']]}")
                last_by_level[item["level"]] = number
    return {
        "hierarchy_regressions": regressions,
        "repeated_level": repeated_level,
        "hierarchy_examples": examples[:5],
    }


def score_chunking(chunk_result: dict, raw_text: str) -> dict:
    chunks = chunk_result["chunks"]
    toc = parse_product_toc(raw_text)
    return {
        "toc": toc,
        "riders": score_rider_names(chunks, toc),
        "articles": score_article_sequence(chunks),
        "coverage": score_article_coverage(chunk_result, raw_text),
        "hierarchy": score_hierarchy(chunks),
    }
