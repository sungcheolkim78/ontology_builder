// Shared with OntologyGraph.vue and ChatPanel.vue so a node type gets the
// same color in the graph view and in chat's related-node chips. Color is
// assigned by a type's index in the *current document's* sorted type list
// (typeOrder), not a fixed per-type mapping, so both call sites must pass
// the same typeOrder to agree.
//
// Two palettes, same hues: the dark set is bright/saturated (reads well on
// the near-black canvas), the light set is the same hues pulled down to a
// darker, more saturated shade -- ChatPanel uses these colors as actual
// text/border color on a light chip background, where the dark set's bright
// pastels fall well under WCAG contrast (e.g. #f7a24f on white is ~1.6:1).
export const NODE_TYPE_COLORS_DARK = ['#4f8ef7', '#f7a24f', '#4fbf7a', '#c96fd6', '#e0555a', '#5ac8d8']
export const NODE_TYPE_COLORS_LIGHT = ['#1d4ed8', '#b45309', '#047857', '#7e22ce', '#b91c1c', '#0e7490']

const FALLBACK_COLOR_DARK = '#999999'
const FALLBACK_COLOR_LIGHT = '#6b7280'

export function colorForNodeType(type, typeOrder, mode = 'dark') {
  const colors = mode === 'light' ? NODE_TYPE_COLORS_LIGHT : NODE_TYPE_COLORS_DARK
  const index = typeOrder.indexOf(type)
  if (index === -1) return mode === 'light' ? FALLBACK_COLOR_LIGHT : FALLBACK_COLOR_DARK
  return colors[index % colors.length]
}
