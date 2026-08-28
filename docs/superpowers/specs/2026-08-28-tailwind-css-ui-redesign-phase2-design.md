# Tailwind CSS UI Redesign (Phase 2: Remaining Components)

## Goal

Phase 1 (`docs/superpowers/specs/2026-08-28-tailwind-css-ui-redesign-design.md`,
implemented and reviewed clean) installed Tailwind CSS v4 and established
a new visual language — slate neutral surfaces, a single indigo accent,
red=danger/green=success semantics, unified primary/secondary/danger
buttons, pill-shaped badges, light modal chrome — using `SettingsPanel.vue`
as the reference implementation. This spec applies that same language to
every other frontend file: `App.vue` (the page layout wiring all five
components together) and the four components phase 1 explicitly deferred
— `ChatPanel.vue`, `DocumentPreview.vue`, `OntologyGraph.vue`,
`SchemaGraphPreview.vue`.

Unlike phase 1, these four components currently each have their own
**distinct accent color** per panel header (`ChatPanel` blue `#2563eb`,
`DocumentPreview` green `#059669`, `OntologyGraph` purple `#7c3aed`,
`SchemaGraphPreview` amber `#b45309`) — a deliberate per-quadrant color
scheme phase 1 never touched (it only redesigned `SettingsPanel.vue`).
This phase replaces that scheme with the single indigo accent, per an
explicit decision made during brainstorming (see "Unified" section below)
— the four panels stop being visually distinguished by color entirely,
consistent with how `SettingsPanel.vue` no longer has a colored header.

## What gets unified

Every one of the following moves to phase 1's established language,
exactly as `SettingsPanel.vue` already does:

- **Panel headers** (all four components' `<h2 class="panel-title">`):
  dark colored background → white background, `border-b
  border-slate-200`, `text-slate-900`. No panel keeps a distinct color.
- **Tabs** (`SchemaGraphPreview.vue`'s 스키마/Nodes/Edges tabs): active
  tab `bg-indigo-100 text-indigo-700`; inactive tabs get the same
  secondary-button treatment as phase 1's buttons (`border-slate-300
  bg-white text-slate-700 hover:bg-slate-50`).
- **Scroll-position thumb** (`DocumentPreview.vue`'s `.position-thumb`,
  previously green to match its own header): `bg-indigo-600`.
- **Chat's own-message bubble** (`ChatPanel.vue`'s `.message.user`,
  previously light blue `#dbe9ff`): `bg-indigo-100`. The assistant
  bubble becomes `bg-slate-100` (matching phase 1's neutral-surface
  color), replacing today's `#f0f0f0`.
- **Resizer hover** (`App.vue`'s `.resizer-v`/`.resizer-h`, previously
  light blue `#b8d0ff`): `bg-indigo-200`.
- **Every button** across all four components (OntologyGraph's "스키마
  미리보기"/"리셋" toggle buttons, the chat send button): phase 1's
  primary/secondary/danger classes, chosen per role the same way phase 1
  chose them for `SettingsPanel.vue` (a toggle/utility action is
  secondary; nothing in these four components is a destructive action,
  so no new danger buttons are introduced here).
- **Checkboxes** (OntologyGraph's Node/Edge Label toggles): `accent-indigo-600`,
  matching phase 1's checkbox treatment.
- **Chat's message input** (`ChatPanel.vue`'s `.input-row input`, currently
  unstyled beyond `flex`/`padding` — flagged during phase 1's final
  review as effectively invisible once Preflight strips the native
  border): phase 1's form-input treatment, `border border-slate-300
  rounded-md px-2 py-1 text-sm focus:border-indigo-500
  focus:outline-none focus:ring-1 focus:ring-indigo-500`.
- **Data tables** (`SchemaGraphPreview.vue`'s three `<table>`s): sticky
  header row becomes `bg-slate-50` (was `#f5f5f5`), borders `border-slate-200`,
  row hover `hover:bg-slate-50`.
- **`App.vue`'s panel borders** (`.top-right`/`.bottom-left`/`.bottom-right`'s
  `border: 1px solid #ccc`): `border-slate-200`.

## Documented exceptions (kept as-is, not unified)

These were raised explicitly during brainstorming and are deliberate,
not oversights:

1. **Chat's node/edge type-analysis chips** (`ChatPanel.vue`'s
   `.type-chip.node-type`/`.type-chip.edge-type` — the small pills under
   each assistant message showing which node/edge *types* the answer
   drew on, distinct from the per-instance related-node chips below).
   The user chose to **keep the node-vs-edge color distinction** rather
   than folding both into the indigo pill language — re-expressed with
   Tailwind's built-in scales instead of the current arbitrary hex:
   node-type chips use `emerald` (border/text `emerald-600`, hover fill
   `emerald-600`/white), edge-type chips use `amber` (border/text
   `amber-800`, hover fill `amber-800`/white) as the closest built-in
   approximation of today's green/brown.
2. **Per-instance related-node chips** (`ChatPanel.vue`'s `.node-chip`,
   color bound via `colorForNodeType()` to match that exact node's color
   in the `OntologyGraph.vue` view). This can't become a static Tailwind
   class — the color is computed per node type at runtime from the same
   function the graph view uses for its own node dots, so the two stay
   visually consistent with each other. The `:style="{ '--chip-color':
   ... }"` binding stays exactly as today. Its `:hover` background swap
   also can't be expressed as a static utility (a CSS custom property
   swapped on hover), so a **minimal one-rule `<style scoped>` block
   survives just for this**: `.node-chip:hover { background:
   var(--chip-color); color: white; }`. Every other property on this
   element (padding, radius, font-size, cursor) moves to Tailwind
   utilities.
3. **Custom scrollbars** (`DocumentPreview.vue`'s `.markdown-scroll` and
   `SchemaGraphPreview.vue`'s `.table-wrap`, both using
   `scrollbar-width`/`scrollbar-color` plus `::-webkit-scrollbar`
   pseudo-elements). Tailwind v4 core has no scrollbar-styling utilities
   without an additional plugin (e.g. `tailwind-scrollbar`), and adding
   a plugin for two thin-scrollbar rules is out of proportion to the
   need. Each of these two files keeps a **small `<style scoped>` block
   containing only the scrollbar rules** — nothing else stays in
   `<style>`.
4. **Graph node hover tooltip** (`OntologyGraph.vue`'s `.node-tooltip`,
   currently dark `#1f2937` background + white text). Kept dark
   (re-expressed as `bg-slate-800 text-white` via Tailwind classes,
   value unchanged) rather than converted to the light theme — a dark
   floating tooltip over light content is a common, deliberate contrast
   pattern independent of overall theme, confirmed with the user rather
   than assumed.
5. **Rendered markdown content legibility** (`DocumentPreview.vue`'s
   `.markdown` and `ChatPanel.vue`'s `.message .markdown`, both
   `v-html`-injected `marked.parse()` output). Discovered as a real
   regression during phase 1's final review: Tailwind's Preflight
   zeroes heading font-size/weight and removes list bullets/indentation
   globally, which flattens `v-html`-injected markdown (headings and
   `- ` bullet lists become indistinguishable from plain paragraph
   text) — content phase 1 fixed directly (see its own ledger) since it
   was already live, using a small `:deep()` block rather than adding
   `@tailwindcss/typography`. When phase 2 rewrites these two files'
   `<template>`/`<style>`, the equivalent `:deep()` rules for
   `h1`–`h4`, `p`, and `ul`/`ol` **must be carried forward** into
   whatever minimal `<style scoped>` remnant each file keeps (same
   category as the scrollbar exception below) — this is not optional
   cleanup, it's preventing the same regression from coming back the
   moment these files' old style blocks are deleted.
6. **`v-network-graph` rendering itself** (node/edge shapes, colors,
   arrows — everything inside `OntologyGraph.vue`'s `configs` computed
   object). This is drawn by the third-party graph library from a plain
   JS config object, not CSS classes — Tailwind has no reach into it at
   all. This phase only touches the *chrome* around the graph (header,
   actions bar, tooltip, placeholder text), never the graph's own
   node/edge rendering, which was never in scope.

## `App.vue` layout

The 4-quadrant resizable grid (`.dashboard`/`.main-grid`/`.panel`) is
reproduced as Tailwind utilities the same way `SettingsPanel.vue`'s
sidebar container was in phase 1 — `flex`, `h-screen`, `grid`, etc.
directly on the elements. The grid's column/row split percentages and
the resizer handles' positions are computed at runtime from drag state
(`gridStyle`, `colResizerStyle`, `rowResizerStyle` — all already
`computed()` refs bound via `:style`) and **stay exactly as `:style`
bindings** — this is genuinely dynamic layout math Tailwind classes
cannot express, not a case of using `:style` where a utility class would
do. Only the *static* parts (border colors, resizer hover color, base
flex/grid/overflow rules) move to Tailwind classes.

## No script/logic changes anywhere

Same constraint as phase 1: every file's `<script setup>` block —
`App.vue`'s drag-resize logic, `ChatPanel.vue`'s message/API logic,
`DocumentPreview.vue`'s scroll-position math, `OntologyGraph.vue`'s
d3-force simulation and v-network-graph wiring, `SchemaGraphPreview.vue`'s
tab/data loading — is untouched. Only `<template>` and `<style>` change,
in every file.

## Testing / verification

Same approach as phase 1 — no automated frontend test suite exists.
Verification is a `podman-compose down && podman-compose up --build -d`
rebuild followed by a real Playwright walkthrough:

1. Every panel header now looks the same (no color distinguishes them).
2. Chat: send a message, confirm own-message bubble is `indigo-100`,
   assistant bubble `slate-100`, node/edge type-analysis chips still
   show emerald/amber respectively and still toggle filters on click,
   related-node chips still show their per-type color and still trigger
   `highlight-nodes`.
3. Document Preview: scroll the document, confirm the position thumb
   (now indigo) still tracks scroll position correctly, and the custom
   scrollbar still renders (not the native browser one).
4. Ontology Graph: toggle schema-preview/reset buttons, toggle Node/Edge
   Label checkboxes, hover a node to confirm the tooltip still appears
   (dark, as before) with correct type/label/detail.
5. Schema/GraphDB: switch between the three tabs, confirm active-tab
   highlighting and that all three data tables render with the new
   header/hover styling and their custom scrollbar.
6. App.vue: drag both resizers, confirm the layout still resizes
   correctly and the hover highlight on each resizer is now indigo.
7. Re-run `SettingsPanel.vue`'s own interactions once more (file
   selection, schema generation, extraction, embedding, version
   management, Configurations) to confirm nothing in this phase's
   `App.vue` changes broke the wiring between it and the other panels.

## Out of scope

- Any change to `v-network-graph`'s own node/edge rendering (colors,
  shapes, force-simulation physics) — chrome only, as stated above.
- Adding a Tailwind scrollbar plugin — the two custom-scrollbar rules
  stay as minimal `<style scoped>` exceptions instead.
- Dark mode (still not in scope, same as phase 1).
- Any backend change.
