// line_start/line_end follow the backend's convention (app.chunking): a
// 1-based, inclusive line range -- so the line count is always
// line_end - line_start + 1, uniformly for both the preamble and every
// chunk.
function lineCount(range) {
  return range.line_end - range.line_start + 1
}

export function preambleLineCount(preamble) {
  return lineCount(preamble)
}

export function chunkLineCount(chunk) {
  return lineCount(chunk)
}
