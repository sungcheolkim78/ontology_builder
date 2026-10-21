# Chunking quality

How well `app.preprocess.chunking.chunk_markdown` recovers a policy's structure
(주계약 → 특약 → 편/관 → 조), how that was measured, and what changed because of it.
Numbers below are reproducible and free (no model call):

```bash
cd backend && source .venv/bin/activate
python ../scripts/evaluate_chunking.py [--verbose] [--json out.json]   # development set
python ../scripts/evaluate_chunking.py --holdout                       # held-out documents, run once
```

## 1. Method

**Reference.** Each policy prints its own "상품 목차" near the front: 주계약 / 특약일반사항 /
특약 / 제도성특약 / 유첨 / 부록 headings with `name page` lines under them
(`chunking_eval.parse_product_toc`). It is used only to *check* the chunker, never as its
input ([ADR 0005](../adr/0005-chunker-reads-title-lines-the-table-of-contents-only-checks.md)).
It spans several pages in a large policy, wraps long names over lines, and sometimes loses
a wrapped entry's page number; the reader handles all three. A policy with no such block
(`우리아이행복`) is reported `no-toc`.

**Readings**, reported side by side and never merged into one score:

| column | meaning |
|---|---|
| `secs` | sections the chunker found (주계약 + 특약일반사항 + riders) / sections the table of contents lists |
| `laws` | appendix sections (one per cited law) |
| `match` | rider names equal to the table of contents' (spaces, `# `, trailing "약관" and the middle dot ignored) |
| `fallbk` | riders still named `특약_N` because no title was found |
| `susp` | rider labels that look like body text (a bullet, a sentence, a `#`) |
| `cover` | heading-shaped lines that became a chunk / heading-shaped lines in the text |
| `dup`, `gap`, `regr` | repeated articles, skipped numbers (a warning, not an error), numbering going backwards, within a rider |
| `h.reg`, `h.skip` | a 편/관 number going backwards / skipping one inside a rider |

**Documents.** Nine, of different products (종신, 건강, 연금, 저축, 정기, 실손, 어린이): seven
for development, two held out (`삼성정기보험_2403`, `삼성인터넷급여실손의료비보장보험_2607`)
and scored once at the end. Two of the nine are the same policy converted twice
(`삼성파워즉시연금보험`, in `backend/data` and in `samples/`).

## 2. Result

Before is the chunker as of `e3b22f5`; after is the chunker at the end of this work; both
scored by the same final evaluator.

| document | chunks | `secs` | `match` | `susp` | `cover` | `dup` | `regr` |
|---|---|---|---|---|---|---|---|
| 파워즉시연금 (data) | 184 → 188 | 4/3 → 3/3 | 2/2 → 2/2 | 2 → 0 | 184/190 → 188/188 | 44 → 0 | 32 → 0 |
| The안심종신 (어린이전환) | 256 → 591 | 10/26 → 26/26 | 7/25 → 25/25 | 7 → 0 | 256/591 → 591/591 | 49 → 0 | 34 → 0 |
| 올백 종신 | 227 → 238 | 8/8 → 8/8 | 7/7 → 7/7 | 5 → 0 | 227/238 → 238/238 | 42 → 0 | 32 → 0 |
| 플래티넘플러스원 건강 | 228 → 2258 | 7/90 → 90/90 | 5/89 → 89/89 | 5 → 0 | 228/2258 → 2258/2258 | 42 → 0 | 32 → 0 |
| 우리아이행복 | 245 → 245 | 5/? → 5/? | no-toc | 2 → 0 | 245/245 | 35 → 0 | 22 → 0 |
| 파워즉시연금 (samples) | 189 → 188 | 4/3 → 3/3 | 2/2 → 2/2 | 1 → 0 | 188/190 → 188/188 | 46 → 0 | 33 → 0 |
| 플래티넘저축 | 180 → 182 | 2/2 → 2/2 | 1/1 → 1/1 | 1 → 0 | 180/182 → 182/182 | 46 → 0 | 32 → 0 |
| **held out** 정기보험 | 237 → 239 | 9/9 → 9/9 | 8/8 → 8/8 | 1 → 0 | 237/239 → 239/239 | 43 → 0 | 32 → 0 |
| **held out** 인터넷급여실손 | 300 → 300 | 11/10 → 10/10 | 8/9 → 9/9 | 2 → 0 | 300/302 → 300/300 | 53 → 0 | 37 → 0 |

`h.reg` and `h.skip` are 0 after on every document; `fallbk` is 0 after everywhere (it was 1
on three). The held-out documents were not looked at while writing rules; every rule was
written against the development set, and the two held-out ones came out at 0 on every
column on the first run.

## 3. What was wrong, in order of size, and what fixed it

1. **Articles not recognised as headings (largest).** The converter drops the `###` from
   some real headings, and a rider written in parts numbers its articles `제2-1조`,
   `제2-1조의2`. Both were folded into the previous chunk: 플래티넘플러스원 had 228 chunks
   for 2258 headings. `parse_article_heading` now takes an unmarked line when it is the whole
   heading (`[title]`, or a `(title)` whose own parentheses balance and end the line) and
   takes `제N-M조`. A `###` line must balance its `(title)` too, since the converter also marks
   body sentences.
2. **Rider boundaries.** The old rule ("제1조 appears again") missed every part-numbered
   rider and, with them merged, 120+ chunks sat under one label. A rider now starts at its
   `OOO특약 약관` title line followed within 40 lines by its first article (`제1조` or `제N-1조`);
   with no title, where numbering starts over (a first article no larger than the one before
   it). Names wrap over lines in the PDF, sometimes in the middle of a word
   (`…특약C(무배` / `당,저해약환급금형) 약관`) or leave only `약관` on the last line, so the
   lines above are joined while they are a name fragment (no spaces, 4+ characters or an open
   bracket, not a sentence, not a product code like `JG0002003(251212)`).
3. **The appendix of cited laws.** ~127 chunks of statute text (every law numbering its own
   `제2조`) were filed under the last rider. It opens at the last `부록 약관에서 인용된 법령 내용`
   line, and each law is its own section, named from its `- 법령명` line. This alone took
   duplicates from 42–53 to 0 and regressions from 32–37 to 0 on five documents.
4. **Front matter taken as the main contract.** Stray `###` lines of the table of contents
   (`제8조`, `제20조`) became a "주계약" and pushed the real one onto the rider list; the main
   contract now starts at its 제1조.
5. **Plans.** A rider sold in plans repeats `제3조/제4조` under `《상해입원형》`-style lines;
   the plan is recorded as `variant`, in the chunk id and in `path`, so ids stay unique.
6. **Hierarchy.** Table-of-contents lines (`## 제5관 보험료의 납입 20`, or articles listed
   on the same line) no longer count as 관 headings. The stack is read from the section's own
   start, not from the top of the document.
7. **A second contract** in one policy (`우리아이행복` 1종 and 2종 (출생아 가입형)) is a
   `main` section named by its own title line, not by whatever sentence mentioned 특약.

## 4. What the data did not support

- **Running headers.** The first look at 플래티넘플러스원 counted `제N관 분쟁의 조정 등` 175 times
  and `## 제1편 일반사항` 248 times, which read like page headers repeated on every page. They
  are not: the first is the same-named 관 that each of the ~90 riders has for itself (with its
  own number), the second is a line every rider carries to say it uses the common 제1편.
  Neither flips the hierarchy, `h.reg` is 0, and no running-header rule was written.
- **장/절.** `장` shows up inside a rider (the plans above); `절` and `편`-over-`장` do not appear in
  these nine documents. The parser still reads them; they are covered by the existing
  synthetic tests only.

## 5. Not done, and limits

- **Name wrapping is a heuristic.** A rider name wrapped over three or more lines, or whose
  head is a short word, would be named from its last line only. It would show up as a
  mismatch in `match` (or as `susp`) rather than silently.
- **`chunks.json` on disk is not regenerated.** `chunk_markdown_file` writes it on demand;
  chunk ids changed (`variant`, `부록 > 법령`), and any stored graph is untouched. Re-extraction
  is a paid, separate decision.
- **Gaps** inside an appendix law are expected (the policy quotes excerpts) and not counted;
  elsewhere a skipped number is only a warning for a person to read.
- **No `parts` field.** The plan was `rider_type`/`rider_name`/`parts`; `ancestors`
  (`[{level, no, title}]`) already is the structured hierarchy, so it was kept rather than
  duplicated. `path` keeps its old shape.
- **Chunk size** (splitting a very long 조, merging a very short one) was out of scope.
- **Converter.** Every lost title this work met could be read back by the chunker, so no
  `parser.py` issue was raised. 우리아이행복 has no 상품 목차 block, so its names are
  unchecked beyond looking sensible.
