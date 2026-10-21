// Filtering of the 삼성생명 약관 search results (GET /api/samsunglife/terms).
// Each result carries `category` and `currently_listed`; the search always
// covers every category, on sale or not, so narrowing is done here.

// The backend's DEFAULT_CATEGORIES (app.preprocess.samsunglife_utils).
export const SAMSUNGLIFE_CATEGORIES = ['보장성', '저축성', '어린이']

export const SAMSUNGLIFE_STATUSES = [
  { key: 'listed', label: '판매중' },
  { key: 'delisted', label: '판매중지' },
]

export function termStatus(term) {
  return term.currently_listed ? 'listed' : 'delisted'
}

export function filterSamsunglifeTerms(terms, categories, statuses) {
  return terms.filter(
    (term) => categories.includes(term.category) && statuses.includes(termStatus(term)),
  )
}

export function countSamsunglifeTerms(terms) {
  const categories = Object.fromEntries(SAMSUNGLIFE_CATEGORIES.map((c) => [c, 0]))
  const statuses = Object.fromEntries(SAMSUNGLIFE_STATUSES.map((s) => [s.key, 0]))
  for (const term of terms) {
    if (term.category in categories) categories[term.category] += 1
    statuses[termStatus(term)] += 1
  }
  return { categories, statuses }
}

// A chip toggles its key in the selection, but the last selected one stays on:
// with nothing selected every result would disappear while the list says there
// are some.
export function toggleSelection(selected, key) {
  if (!selected.includes(key)) return [...selected, key]
  if (selected.length === 1) return selected
  return selected.filter((item) => item !== key)
}
