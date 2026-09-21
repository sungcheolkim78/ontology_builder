// Pure text-matching helpers behind the PDF viewer's "jump to this line"
// feature (see PdfViewer.vue). A raw.md line has already been run through
// app.preprocess.parser's markdown_text() -- headings, bullets, table pipes
// -- so it can't be matched verbatim against pdf.js's own getTextContent()
// output for the same page, which carries none of that reformatting.
// normalizeForMatch() strips both sides down to bare, whitespace-free
// characters before comparing.

const LEADING_HEADING = /^#{1,6}\s*/
const LEADING_BULLET = /^[-*]\s+/
const LEADING_QUOTE = /^>\s?/
const DECORATION_CHARS = /[|*`_]/g
const WHITESPACE = /\s+/g

export function normalizeForMatch(text) {
  return (text ?? '')
    .replace(LEADING_HEADING, '')
    .replace(LEADING_BULLET, '')
    .replace(LEADING_QUOTE, '')
    .replace(DECORATION_CHARS, '')
    .replace(WHITESPACE, '')
}

// A gentler normalization for the PDF viewer's own keyword search box (as
// opposed to normalizeForMatch's line-quote matching above): lowercased and
// whitespace-collapsed rather than whitespace-stripped, since a literal user
// query like "다음 조항" needs its word boundary preserved to avoid matching
// across unrelated adjacent words.
export function normalizeForSearch(text) {
  return (text ?? '').toLowerCase().replace(WHITESPACE, ' ').trim()
}

// Concatenates normalized item text into one search haystack, keeping a
// parallel index of which item produced each haystack character range --
// so a matched substring can be mapped back to the pdf.js text item(s) that
// produced it (and from there to an on-page bounding box). `normalize`
// defaults to the line-quote matcher above; the keyword search box passes
// normalizeForSearch instead.
export function buildHaystack(items, itemText = (item) => item.str, normalize = normalizeForMatch) {
  let haystack = ''
  const ranges = []
  for (const item of items) {
    const normalized = normalize(itemText(item))
    if (!normalized) continue
    ranges.push({ start: haystack.length, end: haystack.length + normalized.length, item })
    haystack += normalized
  }
  return { haystack, ranges }
}

// A long line can fail an exact match -- our own markdown reformatting, or
// a pdf.js item boundary landing mid-word, can shift a handful of
// characters -- so a shortened prefix is tried before giving up entirely.
const SHORTENED_PREFIX_LENGTH = 20

export function findMatchRange(haystack, needle) {
  if (!needle) return null
  let index = haystack.indexOf(needle)
  let length = needle.length
  if (index === -1 && needle.length > SHORTENED_PREFIX_LENGTH) {
    const shortened = needle.slice(0, SHORTENED_PREFIX_LENGTH)
    index = haystack.indexOf(shortened)
    length = shortened.length
  }
  if (index === -1) return null
  return { start: index, end: index + length }
}

export function itemsInRange(ranges, matchRange) {
  return ranges
    .filter((r) => r.start < matchRange.end && r.end > matchRange.start)
    .map((r) => r.item)
}

const PAGE_MARKER_PATTERN = /^<!--\s*page:\s*(\d+)\s*-->$/

// Maps a 1-indexed line number in raw.md back to the PDF page it came from,
// using the `<!-- page: N -->` markers app.preprocess.parser's
// page_to_markdown emits once per page during PDF conversion. Markers are
// literal text in raw.md (a comment, invisible once rendered), so this
// scans the same line array the viewer already tracks scroll position over.
export function pageForLine(lines, lineNumber) {
  let page = 1
  for (let i = 0; i < lineNumber && i < lines.length; i++) {
    const match = PAGE_MARKER_PATTERN.exec(lines[i].trim())
    if (match) page = Number(match[1])
  }
  return page
}

// The reverse of pageForLine, backing the PDF viewer's "Sync" button (PDF
// page -> markdown scroll position, as opposed to every other helper here,
// which goes markdown -> PDF). Returns the 1-indexed line of that page's own
// `<!-- page: N -->` marker, or line 1 if the page has no marker (shouldn't
// happen for a real PDF-derived document, since page_to_markdown emits one
// per page, but a page number past the end of a shorter/mismatched document
// falls back gracefully rather than throwing).
export function lineForPage(lines, page) {
  for (let i = 0; i < lines.length; i++) {
    const match = PAGE_MARKER_PATTERN.exec(lines[i].trim())
    if (match && Number(match[1]) === page) return i + 1
  }
  return 1
}

// The highest page number markered anywhere in raw.md -- used to show
// "page N of TOTAL" in the status bar without PreviewView needing to ask
// PdfViewer (which owns the actual PDF document) for its own page count.
export function totalPagesInLines(lines) {
  let max = 1
  for (const line of lines) {
    const match = PAGE_MARKER_PATTERN.exec(line.trim())
    if (match) max = Math.max(max, Number(match[1]))
  }
  return max
}

// Maps an evidence_text quote (verbatim per the backend's own verification --
// see app.ontology.extraction._find_evidence_span) straight back to a raw.md
// line number by locating it as a substring and counting newlines up to that
// point, then reuses pageForLine for the line->page step. Silently falls back
// to page 1 (letting PdfViewer's own on-page search no-op) if the quote isn't
// found verbatim -- same graceful-miss behavior PdfViewer.vue already has for
// any jump request whose text doesn't match on the target page.
export function pageForQuote(rawText, lines, quote) {
  if (!quote) return 1
  const index = rawText.indexOf(quote)
  if (index === -1) return 1
  const lineNumber = rawText.slice(0, index).split('\n').length
  return pageForLine(lines, lineNumber)
}
