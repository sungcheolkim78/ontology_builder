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

// Concatenates normalized item text into one search haystack, keeping a
// parallel index of which item produced each haystack character range --
// so a matched substring can be mapped back to the pdf.js text item(s) that
// produced it (and from there to an on-page bounding box).
export function buildHaystack(items, itemText = (item) => item.str) {
  let haystack = ''
  const ranges = []
  for (const item of items) {
    const normalized = normalizeForMatch(itemText(item))
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
