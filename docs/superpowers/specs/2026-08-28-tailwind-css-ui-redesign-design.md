# Tailwind CSS UI Redesign (Phase 1: Setup + SettingsPanel)

## Goal

Introduce Tailwind CSS into the Vue 3 frontend (`frontend/src/`), which
currently has no CSS framework at all — every component hand-rolls its
own `<style scoped>` block with plain CSS. This is **phase 1** of a
two-phase redesign:

1. **This spec**: set up Tailwind's build tooling for the first time in
   this project, establish a new visual direction (palette, button/badge/
   modal language), and convert `SettingsPanel.vue` — the largest, most
   varied component (buttons, selects, checkboxes, number inputs, two
   modals, badges, lists) — as the reference implementation of that
   language.
2. **Phase 2 (separate, later spec)**: apply the same Tailwind classes/
   patterns established here to `ChatPanel.vue`, `DocumentPreview.vue`,
   `OntologyGraph.vue`, `SchemaGraphPreview.vue`, and `App.vue`'s overall
   layout.

Splitting this way avoids doing a from-scratch visual redesign across
five components before any direction is validated against real,
non-trivial UI (`SettingsPanel.vue` alone has ~300 lines of existing CSS
and two modals). Between phase 1 and phase 2 shipping, the app will
visibly have two different visual languages side by side — expected and
accepted, not a bug.

This is a **redesign**, not a like-for-like utility-class port: the
current look (dark `#1f2937` header, ad hoc per-component color choices)
is being replaced with a new one, not reproduced pixel-for-pixel in
Tailwind classes.

## Tooling: Tailwind v4 via the Vite plugin

Tailwind v4 removes the separate `tailwind.config.js`/`postcss.config.js`
files v3 required — configuration lives in CSS itself via `@theme`, and
the official `@tailwindcss/vite` plugin handles the rest. Chosen over v3
specifically because this project has deliberately kept its build
tooling minimal (`vite` alone, no PostCSS, no lint pipeline) — v4 fits
that constraint better than v3's extra config files would.

**Changed/new files:**
- `frontend/package.json` — add `tailwindcss` and `@tailwindcss/vite` as
  devDependencies.
- `frontend/vite.config.js` — register the `tailwindcss()` plugin
  alongside the existing `vue()` plugin.
- `frontend/src/style.css` (new) — `@import "tailwindcss";` plus a
  `@theme` block (see below — expected to stay minimal/near-empty, since
  the design direction uses Tailwind's built-in palette rather than
  custom tokens).
- `frontend/src/main.js` — one new `import './style.css'` line.

No other build config changes. `frontend/Dockerfile`'s existing
`RUN npm install` step picks up the new dependencies on the next
`podman-compose up --build`; nothing else in the container setup needs
to change.

**Global side effect to expect:** `@import "tailwindcss"` pulls in
Tailwind's Preflight base-reset layer, which applies document-wide the
moment `style.css` is imported in `main.js` — margins on headings/
paragraphs, default form-element appearance, box-sizing, etc. all
change globally, not just inside `SettingsPanel.vue`. This means
`ChatPanel.vue`, `DocumentPreview.vue`, `OntologyGraph.vue`, and
`SchemaGraphPreview.vue` will show *some* subtle visual shift (e.g.
default button chrome disappearing) even though this phase touches
none of their code — expected from installing Tailwind at all, not
scope creep into phase 2. Worth a quick visual check of those four
during verification so nothing looks accidentally broken (vs. just
"unstyled-looking," which is fine and gets addressed in phase 2).

## Design direction: palette and visual language

Uses Tailwind's **built-in** palette only — no invented custom colors,
in keeping with Tailwind's own utility-first philosophy (referenced by
the user: https://tailwindcss.com/docs/styling-with-utility-classes) and
this project's general preference against unnecessary abstraction:

- **Neutral surfaces**: the `slate` scale — page/sidebar background
  `slate-50`, cards/modals white, borders `slate-200`, secondary text
  `slate-500`/`slate-600`, headings/primary text `slate-900`.
- **Accent**: `indigo` — primary buttons, the active-version badge/row
  highlight, focus rings. One accent color used consistently, not one
  per UI area (a deliberate change from today's ad hoc per-component
  colors, e.g. today's distinct type-swatch blue vs. danger-button red
  vs. header dark-slate were three unrelated choices).
- **Danger** (delete/reset actions): `red` — already the semantic
  intent of today's danger-button, kept.
- **Success messages**: `green` — already the semantic intent of
  today's `.success` text color, kept.
- **Shape**: `rounded-md` for buttons/inputs, `rounded-lg` + `shadow-lg`
  for modal cards.

`src/style.css`'s `@theme` block is expected to stay minimal (possibly
empty beyond the `@import`) — everything above maps directly onto
Tailwind's shipped `slate`/`indigo`/`red`/`green` scales, so no custom
token definitions are anticipated. If implementation reveals a genuine
gap (e.g. a specific shade Tailwind doesn't ship), add it there with a
comment explaining why — don't add tokens speculatively.

No dark mode in this phase (explicitly descoped — see below).

## Component conversion: `SettingsPanel.vue`

`<style scoped>` is removed **entirely** — including layout rules, not
just colors (e.g. today's `.settings { width: 270px; display: flex; ... }`
becomes `w-[270px] flex flex-col overflow-hidden border-r border-slate-200`
directly on the element). Every element in the template gets Tailwind
utility classes applied directly, per
https://tailwindcss.com/docs/styling-with-utility-classes's approach —
no `@apply`-based custom classes, no extracted CSS classes, in this
phase.

**No functional/logic changes** — every `ref`, `computed`, `watch`,
`emit`, and API call in the `<script setup>` block is untouched. This is
a template + style rewrite only.

Concrete mappings:
- **Buttons collapse from five ad hoc classes to three semantic kinds**:
  today's `.workflow-button`, `.explorer-button`, `.danger-button`,
  `.close-button`, and `.version-action-button` are five independently-
  styled button classes. They become three consistent Tailwind
  utility-class combinations applied per button based on its actual
  role: **primary** (indigo background — main workflow actions),
  **secondary** (slate border/text — neutral actions like 파일 선택,
  활성화), **danger** (red border/text — LadybugDB 초기화, 버전 삭제).
- **Modals** (File Explorer, Configurations): today's dark
  `#1f2937` header becomes a white header with a `border-b
  border-slate-200` divider instead — a lighter, less heavy-handed
  modal chrome. Overlay stays `fixed inset-0` with a translucent black
  scrim (`bg-black/40`), card is `bg-white rounded-lg shadow-lg`.
- **Badges** (스키마/그래프/활성 status pills): unified into one pill
  shape — `inline-flex items-center rounded-full px-2 py-0.5 text-xs`
  — with `bg-indigo-100 text-indigo-700` for the "on"/active state and
  `bg-slate-100 text-slate-500` for the "off" state, replacing today's
  per-badge-type ad hoc coloring.
- **Version list rows**: the active version's row gets `bg-indigo-50`;
  other rows get `hover:bg-slate-50` on hover, replacing today's
  `.version-item.active { background: #dbe9ff; }`.
- **Form inputs** (number inputs, select, checkboxes, file input):
  standard Tailwind input styling — `border border-slate-300
  rounded-md px-2 py-1 text-sm focus:border-indigo-500
  focus:ring-1 focus:ring-indigo-500` — replacing today's mostly-default
  browser styling.

## Testing / verification

No automated frontend test suite exists in this repo (per `CLAUDE.md`)
— verification is manual, via the same process used earlier this
session for other frontend changes:

1. `podman-compose down && podman-compose up --build -d` (the new
   devDependencies need a fresh `npm install`, and this project's known
   virtiofs/Vite-staleness gotcha means a plain reload can't be trusted
   to reflect the change).
2. Playwright-driven check confirming every existing interaction still
   works with identical behavior, just new appearance: file selection,
   schema generation (2 스키마 생성), extraction, embedding, the File
   Explorer modal's document list / schema-version list (activate/
   delete) / schema library, the Configurations modal's every control
   (model display, markdown toggle, hop count, max-chars, DB reset),
   and the sidebar's active-version indicator.
3. A quick visual pass over `ChatPanel.vue`, `DocumentPreview.vue`,
   `OntologyGraph.vue`, and `SchemaGraphPreview.vue` to confirm
   Preflight's global reset (see "Global side effect to expect" above)
   didn't break their layout — visually plainer is fine, visually
   broken is not.
4. No backend test involvement — nothing in `backend/` changes.

## Out of scope

- `ChatPanel.vue`, `DocumentPreview.vue`, `OntologyGraph.vue`,
  `SchemaGraphPreview.vue`, and `App.vue`'s overall page layout — phase
  2, a separate spec written after this phase ships and the direction
  is validated.
- Dark mode.
- Extracting repeated utility-class combinations into shared Vue
  components or `@apply` custom classes — utility-first per Tailwind's
  own recommended approach; revisit only if real duplication pain shows
  up during phase 2.
- Any change to backend code, API contracts, or component
  props/emits/logic.
