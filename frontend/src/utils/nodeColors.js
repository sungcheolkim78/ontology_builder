// Shared with OntologyGraph.vue and ChatPanel.vue so a node type gets the
// same color in the graph view and in chat's related-node chips. Color is
// assigned by a type's index in the *current document's* sorted type list
// (typeOrder), not a fixed per-type mapping, so both call sites must pass
// the same typeOrder to agree.
export const NODE_TYPE_COLORS = ['#4f8ef7', '#f7a24f', '#4fbf7a', '#c96fd6', '#e0555a', '#5ac8d8']

const FALLBACK_COLOR = '#999999'

export function colorForNodeType(type, typeOrder) {
  const index = typeOrder.indexOf(type)
  if (index === -1) return FALLBACK_COLOR
  return NODE_TYPE_COLORS[index % NODE_TYPE_COLORS.length]
}
