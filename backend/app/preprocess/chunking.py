"""Markdown -> per-article JSON chunking, the second stage of the
pdf -> markdown -> chunked-json pipeline (see app.preprocess.parser for the
first stage, which produces the raw.md this module reads).

Ported from scripts/data_prep/chunk_terms_markdown.py, built and tuned
against Korean insurance-policy PDFs (see that directory's README for the
heading/section heuristics and their known limitations).
"""

from __future__ import annotations

import json
import re

from app.utils.paths import chunk_path_for, raw_path_for
from app.utils.store import write_json

# Anchored to the full line: a TOC duplicate or a mid-sentence PDF line-wrap
# artifact always has extra text trailing the closing bracket/paren, so
# requiring the title bracket to end the line rejects both at once.
#
# The article number is `12` or, in a rider written in parts, `2-1` (제2-1조).
# A line the converter marked `###` vouches for itself, so a `[title]` may hold
# anything; the converter also marks body sentences, so even then a `(title)`
# must balance and end the line. The converter also drops the `###` from some real headings (a
# 일시납 policy's "해당 사항이 없습니다" articles), and nothing vouches for such a
# line, so it must be the whole heading: a `[title]`, or a `(title)` whose own
# parentheses balance and which ends the line -- not a sentence that opens with
# a citation like "제20조(계약내용의 변경 등) 제1항의 절차에 따라 ...".
_ARTICLE_NUMBER = r"제\s*(?P<no>\d+(?:-\d+)?)\s*조(?:\s*의\s*(?P<sub>\d+))?\s*"
_BALANCED_PARENS = r"(?:[^()]|\([^()]*\))*"
MARKED_ARTICLE_HEADING_PATTERN = re.compile(
    rf"^###\s*{_ARTICLE_NUMBER}(?:\[(?P<title_b>.+)\]|\((?P<title_p>{_BALANCED_PARENS})\))\s*$"
)
UNMARKED_ARTICLE_HEADING_PATTERN = re.compile(
    rf"^{_ARTICLE_NUMBER}(?:\[(?P<title_b>[^\[\]]+)\]|\((?P<title_p>{_BALANCED_PARENS})\))\s*$"
)
ARTICLE_HEADING_PATTERNS = (MARKED_ARTICLE_HEADING_PATTERN, UNMARKED_ARTICLE_HEADING_PATTERN)

SECTION_HEADING_PATTERN = re.compile(
    r"^(?:##\s*)?제\s*(?P<no>\d+)\s*"
    r"(?P<level>편|장|절|관)\b\s*(?P<title>.*?)\s*$"
)


def parse_article_heading(line: str) -> dict[str, str | None] | None:
    match = next((m for pattern in ARTICLE_HEADING_PATTERNS if (m := pattern.match(line.strip()))), None)
    if not match:
        return None
    title = match.group("title_b")
    if title is None:
        title = match.group("title_p")
    return {"article_no": match.group("no"), "sub_no": match.group("sub"), "title": title}


_TOC_TAIL = re.compile(r"\s\d{1,4}$|제\s*\d+(?:-\d+)?\s*조")


def parse_section_heading(line: str) -> dict[str, str] | None:
    """Parse a policy hierarchy heading such as ``제 2 관 보험금의 지급``."""
    match = SECTION_HEADING_PATTERN.match(line.strip())
    if not match:
        return None
    # A table-of-contents line: the page number ends it, or the articles under it
    # are listed on the same line. Same rule as for an article heading: the title
    # must be the whole line, with nothing trailing.
    if _TOC_TAIL.search(match.group("title")):
        return None
    return {
        "level": match.group("level"),
        "no": match.group("no"),
        "title": match.group("title").strip(),
    }


_SECTION_LEVELS = {"편": 0, "장": 1, "절": 2, "관": 3}


def section_ancestors(lines: list[str], end: int) -> list[dict[str, str]]:
    """Return the active policy hierarchy immediately before ``end``.

    Section headings are kept as a small stack so a new 관 replaces the prior
    관 while retaining its enclosing 편/장/절.
    """
    stack: list[dict[str, str]] = []
    for line in lines[:end]:
        heading = parse_section_heading(line)
        if heading is None:
            continue
        level = _SECTION_LEVELS[heading["level"]]
        stack = [item for item in stack if _SECTION_LEVELS[item["level"]] < level]
        stack.append(heading)
    return stack


def guess_section_label(lines: list[str], start: int, end: int) -> str | None:
    """Best-effort rider name for the section beginning at `end`.

    A rider's name always appears somewhere in the block of text preceding
    its first article, but its exact position drifts with PDF line-wrapping
    (see scripts/data_prep/README.md), so this takes the last short,
    non-tabular mention of "특약" in the block rather than assuming adjacency
    to any one marker line.
    """
    for index in range(end - 1, start - 1, -1):
        candidate = lines[index].strip()
        if not candidate or candidate.startswith(("<!--", "※")) or "|" in candidate:
            continue
        if "특약" in candidate and len(candidate) < 60:
            return candidate
    return None


# A rider starts at its own title line, "OOO특약 약관" (with or without a `# `),
# followed within RIDER_TITLE_WINDOW lines by the rider's first article. The first
# article is what tells a title from the same words elsewhere: the table of
# contents, a guide page, or a body line that happens to end "특약 약관" are not
# followed by one. Riders written in parts begin at 제2-1조 (the common 제1편 is
# only referred to), the others at 제1조.
RIDER_TITLE_WINDOW = 40
_RIDER_TITLE_PATTERN = re.compile(r"^(?:#+\s*)?(?P<name>[^|]*?)\s*약관$")
_GENERIC_RIDER_TITLES = {"특약", "제도성특약"}
MIN_NAME_FRAGMENT = 4  # a wrapped head of a rider name is long ("갑상선암및기타피부암"), not "본문"
_PRODUCT_CODE = re.compile(r"^[A-Z]{2}\d+\(\d+\)")  # "JG0002003(251212)", printed above some titles
_NOT_A_NAME_START = ("①", "②", "③", "④", "⑤", "-", "※", "·", "<!--", "---")


def _is_first_article(parsed: dict[str, str | None]) -> bool:
    if parsed["sub_no"] is not None:
        return False
    number = parsed["article_no"]
    return number == "1" or re.fullmatch(r"\d+-1", number) is not None


def _article_parts(parsed: dict[str, str | None]) -> tuple[int, ...]:
    return tuple(int(part) for part in parsed["article_no"].split("-"))


_GENERAL_PART_TITLE = re.compile(r"^\[특약\s*약관\]\s*제\s*\d+\s*편\s*일반사항$")


def _brackets_balance(text: str) -> bool:
    return text.count("(") == text.count(")") and text.count("[") == text.count("]")


def _is_name_fragment(line: str) -> bool:
    return bool(line) and " " not in line and not line.startswith(_NOT_A_NAME_START) and not line.endswith(("다.", "다")) and not _PRODUCT_CODE.match(line)


def parse_rider_title(lines: list[str], index: int) -> str | None:
    """The rider named by the title line at `index`, or None if it is not one.

    The name is the title line with any wrapped head joined on: a long name
    wraps in the PDF, sometimes in the middle of a word ("...특약C(무배" /
    "당,저해약환급금형) 약관"), so the lines directly above count as its head when
    they are a name fragment -- long enough not to be a stray word, or with a
    bracket still open."""
    stripped = lines[index].strip()
    if _GENERAL_PART_TITLE.match(stripped):
        return stripped
    match = _RIDER_TITLE_PATTERN.match(stripped)
    if match is None:
        return None
    name = match.group("name").strip()
    if len(name) > 80 or name in _GENERIC_RIDER_TITLES or name.startswith(_NOT_A_NAME_START):
        return None
    for above in range(index - 1, max(index - 3, -1), -1):
        head = lines[above].strip()
        if not _is_name_fragment(head) or (len(head) < MIN_NAME_FRAGMENT and _brackets_balance(head)):
            break
        name = head + name
    if "특약" not in name or len(name) > 120 or name.endswith(("다.", "다")):
        return None
    return name


def contract_title_above(lines: list[str], start: int, end: int) -> str | None:
    """Name of a contract whose title line ("... 2 종(출생아 가입형) 약관", no
    "특약" in it) sits just above the article heading at `end`, or None.

    Only a few lines are read, and only plain text: part headings, page breaks
    and blank lines are skipped over, a sentence stops the search."""
    for index in range(end - 1, max(start, end - 12) - 1, -1):
        stripped = lines[index].strip()
        if not stripped or stripped == "---" or stripped.startswith("<!--") or parse_section_heading(stripped):
            continue
        match = _RIDER_TITLE_PATTERN.match(stripped)
        if match is None or not match.group("name").strip():
            return None
        name = match.group("name").strip()
        if name.startswith(_NOT_A_NAME_START) or name.endswith(("다.", "다")) or len(name) > 80:
            return None
        head = lines[index - 1].strip() if index > start else ""
        if head and not head.startswith(("---", "<!--")) and not head.endswith(("다.", "다", ".")) and len(head) <= 40:
            name = f"{head} {name}"
        return name
    return None


def rider_starts(lines: list[str], headings: list[tuple[int, dict]]) -> dict[int, tuple[str, int]]:
    """Position in `headings` of each rider's first article -> (rider name, the
    line index of its title)."""
    starts: dict[int, tuple[str, int]] = {}
    for position, (heading_index, parsed) in enumerate(headings):
        if not _is_first_article(parsed):
            continue
        previous = headings[position - 1][0] if position else -1
        for index in range(heading_index - 1, max(previous, heading_index - RIDER_TITLE_WINDOW) - 1, -1):
            name = parse_rider_title(lines, index)
            if name is not None:
                starts[position] = (name, index)
                break
    return starts


# The appendix of cited laws opens with a line like "부록 약관에서 인용된 법령 내용
# (가나다 순)", and each law with "- 감염병의 예방 및 관리에 관한 법률" just above its
# first article. Like a rider title, the opening line counts only when an article
# follows it soon: the same words sit in the table of contents with none after.
# The laws number their own articles from 제1조, so each law is a section of its own
# (otherwise ids such as "7::제2조" repeat across laws, and the last rider swallows
# every law).
_APPENDIX_MARKER = re.compile(r"^(?:#+\s*)?\[?부록\]?\s*약관에서\s*인용된\s*법령")
_LAW_TITLE = re.compile(r"^-\s+(?P<name>.{2,60}?(?:법|법률|령|규칙|규정|조례))$")
APPENDIX_WINDOW = 40


def find_appendix_start(lines: list[str], headings: list[tuple[int, dict]]) -> int | None:
    """Index of the line opening the appendix of cited laws, or None. The last
    qualifying one: the appendix is at the end, and the same words earlier are
    the front matter's list of what the policy contains."""
    found = None
    for index, line in enumerate(lines):
        if not _APPENDIX_MARKER.match(line.strip()):
            continue
        if any(index < heading_index <= index + APPENDIX_WINDOW for heading_index, _ in headings):
            found = index
    return found


# A rider sold in plans repeats its articles per plan, each run opened by a line
# such as "《종합통원형》" just above the first article. The plan is part of what
# the article is, and without it the repeated 제3조 would share an id.
_VARIANT = re.compile(r"^《(?P<name>[^《》]+)》$")


def variant_line(lines: list[str], start: int, end: int) -> int | None:
    """Index of the plan-name line directly above the article heading at `end`
    (blank lines and page breaks between them don't count), or None."""
    for index in range(end - 1, start - 1, -1):
        stripped = lines[index].strip()
        if not stripped or stripped == "---" or stripped.startswith("<!--"):
            continue
        return index if _VARIANT.match(stripped) else None
    return None


def law_title_line(lines: list[str], start: int, end: int) -> int | None:
    """Index of the cited law's title line between `start` and the article
    heading at `end`, the last one if there are several."""
    for index in range(end - 1, start - 1, -1):
        if _LAW_TITLE.match(lines[index].strip()):
            return index
    return None


def _body_text(lines: list[str], start: int, end: int) -> str:
    """Join lines[start:end], dropping page-break artifacts, not headings."""
    kept = [
        line
        for line in lines[start:end]
        if line.strip() != "---" and not line.strip().startswith("<!-- page:")
    ]
    return "\n".join(kept).strip()


def chunk_markdown(text: str, source_name: str) -> dict:
    lines = text.splitlines()
    headings = [
        (index, parsed)
        for index, line in enumerate(lines)
        if (parsed := parse_article_heading(line)) is not None
    ]

    # The main contract starts at its 제1조. A heading before that is front matter
    # (a stray line of the table of contents), and it would otherwise become a
    # "주계약" of its own and push the real one onto the rider list.
    first_article = next(
        (position for position, (_, parsed) in enumerate(headings) if _is_first_article(parsed)), 0
    )
    headings = headings[first_article:]

    preamble_end = headings[0][0] if headings else len(lines)
    preamble = {
        "line_start": 1,
        "line_end": preamble_end,
        "text": _body_text(lines, 0, preamble_end),
    }

    titled = rider_starts(lines, headings)
    appendix_start = find_appendix_start(lines, headings)
    chunks = []
    section_index = -1
    section_label = rider_name = ""
    rider_type = "main"
    rider_name_source = "main"
    section_start = 0  # first line the section's 편/관 headings are read from
    section_first_heading = 0
    variant = None
    for position, (index, parsed) in enumerate(headings):
        previous_index = headings[position - 1][0] if position else -1
        in_appendix = appendix_start is not None and index > appendix_start
        law_line = None
        if in_appendix:
            law_line = law_title_line(lines, max(previous_index + 1, appendix_start), index)

        if in_appendix:
            # a law's own numbering is neither a rider's nor evidence of one
            opens_section = law_line is not None or previous_index < appendix_start
        else:
            # A title line decides. With none, a rider still starts where the
            # numbering starts over: a first article (제1조, 제2-1조) no larger
            # than the article before it. Moving on to the next part (제1-15조,
            # then 제2-1조) is not a restart.
            restarts = (
                _is_first_article(parsed)
                and position > 0
                and _article_parts(parsed) <= _article_parts(headings[position - 1][1])
            )
            opens_section = position == 0 or position in titled or restarts

        if opens_section:
            section_index += 1
            if in_appendix:
                rider_type, rider_name_source = "appendix", "appendix"
                rider_name = _LAW_TITLE.match(lines[law_line].strip()).group("name") if law_line is not None else "인용된 법령"
                section_label = f"부록 > {rider_name}"
                section_start = law_line if law_line is not None else appendix_start
            elif section_index == 0:
                rider_type, rider_name_source = "main", "main"
                section_label = rider_name = "주계약"
                section_start = 0
            else:
                rider_type = "rider"
                if position in titled:
                    rider_name, title_index = titled[position]
                    rider_name_source, section_start = "title", title_index
                elif contract := contract_title_above(lines, previous_index + 1, index):
                    rider_name, rider_name_source, section_start = contract, "title", previous_index + 1
                    if "특약" not in contract:
                        rider_type = "main"
                elif guessed := guess_section_label(lines, section_first_heading, index):
                    rider_name, rider_name_source, section_start = guessed, "guess", previous_index + 1
                else:
                    rider_name, rider_name_source, section_start = f"특약_{section_index}", "fallback", previous_index + 1
                if _GENERAL_PART_TITLE.match(rider_name):
                    rider_type = "general"
                section_label = rider_name
            section_first_heading = index

        if opens_section:
            variant = None
        own_variant = variant_line(lines, previous_index + 1, index)
        if own_variant is not None:  # a plan runs until the next plan line or the next section
            variant = _VARIANT.match(lines[own_variant].strip()).group("name")
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        if position + 1 < len(headings):
            next_variant = variant_line(lines, index + 1, headings[position + 1][0])
            if next_variant is not None:
                end = min(end, next_variant)
        # text that is not this article's: the appendix's opening notes, and the
        # title line of the next cited law
        if appendix_start is not None and index < appendix_start < end:
            end = appendix_start
        if position + 1 < len(headings) and headings[position + 1][0] > (appendix_start or len(lines)):
            next_law_line = law_title_line(lines, max(index + 1, appendix_start), headings[position + 1][0])
            if next_law_line is not None:
                end = min(end, next_law_line)

        article_ref = f"제{parsed['article_no']}조" + (
            f"의{parsed['sub_no']}" if parsed["sub_no"] else ""
        )
        ancestors = section_ancestors(lines[section_start:], index - section_start)
        ancestor_path = [
            f"제{item['no']}{item['level']} {item['title']}".rstrip()
            for item in ancestors
        ]
        variant_part = [f"《{variant}》"] if variant else []
        path_parts = [section_label, *ancestor_path, *variant_part, f"{article_ref}({parsed['title']})"]
        chunk_id = f"{section_index}::{article_ref}" + (f"@{variant}" if variant else "")
        chunks.append(
            {
                "id": chunk_id,
                "section_index": section_index,
                "section_label": section_label,
                "rider_type": rider_type,
                "rider_name": rider_name,
                "rider_name_source": rider_name_source,
                "article_no": parsed["article_no"],
                "sub_no": parsed["sub_no"],
                "title": parsed["title"],
                "variant": variant,
                "ancestors": ancestors,
                "path": " > ".join(path_parts),
                "line_start": index + 1,
                "line_end": end,
                "text": _body_text(lines, index + 1, end),
            }
        )

    return {"source": source_name, "preamble": preamble, "chunks": chunks}


def chunk_markdown_file(stem: str) -> dict:
    """Chunk the document's own raw.md (written by parse_to_markdown_file/
    convert_pdf_to_markdown_file, both in app.preprocess.parser) and save the
    result as chunks.json in that same document folder."""
    source_path = raw_path_for(stem)
    if not source_path.is_file():
        raise FileNotFoundError(f"markdown file not found for document: {stem}")

    result = chunk_markdown(source_path.read_text(encoding="utf-8"), stem)

    write_json(chunk_path_for(stem), result, indent=2)

    return {"filename": "chunks.json", "path": f"data/documents/{stem}/chunks.json", **result}
