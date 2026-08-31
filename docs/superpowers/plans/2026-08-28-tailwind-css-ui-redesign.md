# Tailwind CSS UI Redesign (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce Tailwind CSS (v4) into the Vue 3 + Vite frontend for the first time, and redesign `SettingsPanel.vue` — the largest, most varied component — as the reference implementation of a new slate+indigo visual language, replacing its ~300-line hand-rolled `<style scoped>` block entirely.

**Architecture:** Tailwind v4 via the official `@tailwindcss/vite` plugin (no separate `tailwind.config.js`/`postcss.config.js` — configuration lives in a single imported CSS file). `SettingsPanel.vue`'s `<script setup>` logic is untouched; only its `<template>` and `<style>` change.

**Tech Stack:** Vue 3 (`<script setup>`), Vite, Tailwind CSS v4, `@tailwindcss/vite`.

**Spec:** `docs/superpowers/specs/2026-08-28-tailwind-css-ui-redesign-design.md`

## Global Constraints

- No `tailwind.config.js`, no `postcss.config.js` — v4's CSS-based `@theme` config only, and in this phase `@theme` is expected to stay minimal since the palette uses Tailwind's built-in `slate`/`indigo`/`red`/`green` scales with no custom tokens.
- No dark mode in this phase.
- No changes to `SettingsPanel.vue`'s `<script setup>` block — every `ref`, `computed`, `watch`, `emit`, and API call stays byte-for-byte identical. Only `<template>` and `<style>` change.
- No extraction of repeated utility-class combinations into shared components or `@apply` classes in this phase — utility-first, applied directly in markup.
- `ChatPanel.vue`, `DocumentPreview.vue`, `OntologyGraph.vue`, `SchemaGraphPreview.vue`, and `App.vue`'s overall layout are out of scope (phase 2) — this plan only touches `SettingsPanel.vue` and the shared Tailwind tooling files.
- No automated frontend test suite exists in this repo — every verification step in this plan is a manual/Playwright check against the running `podman-compose` stack, not a unit test.

---

### Task 1: Install and wire up Tailwind CSS tooling

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/vite.config.js`
- Create: `frontend/src/style.css`
- Modify: `frontend/src/main.js`

**Interfaces:**
- Produces: a working Tailwind build pipeline — any `class="..."` utility class used anywhere under `frontend/src/` from this point on is picked up and generates real CSS. Task 2 depends on this being wired up correctly before it can produce any visible result.

- [ ] **Step 1: Add the Tailwind devDependencies**

In `frontend/package.json`, change:

```json
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.1.0",
    "vite": "^5.4.0"
  }
```

to:

```json
  "devDependencies": {
    "@tailwindcss/vite": "^4.0.0",
    "@vitejs/plugin-vue": "^5.1.0",
    "tailwindcss": "^4.0.0",
    "vite": "^5.4.0"
  }
```

- [ ] **Step 2: Register the Tailwind Vite plugin**

In `frontend/vite.config.js`, change:

```js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
```

to:

```js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
```

(The rest of the file — the `server`/`proxy` block — is unchanged.)

- [ ] **Step 3: Create the Tailwind entry CSS file**

Create `frontend/src/style.css`:

```css
@import "tailwindcss";
```

Per the spec, no `@theme` customization is added here — the design direction (slate neutrals, indigo accent, red/green semantics) maps directly onto Tailwind's shipped palette, so there's nothing to override yet. If Task 2 turns up a genuine gap, add a `@theme` block here then, with a comment explaining why — don't add one speculatively now.

- [ ] **Step 4: Import the CSS file once, globally**

In `frontend/src/main.js`, change:

```js
import { createApp } from 'vue'
import App from './App.vue'

createApp(App).mount('#app')
```

to:

```js
import { createApp } from 'vue'
import App from './App.vue'
import './style.css'

createApp(App).mount('#app')
```

- [ ] **Step 5: Rebuild and verify the pipeline builds cleanly**

```bash
podman-compose down && podman-compose up --build -d
```

Then check the frontend container didn't fail to start and the page still loads:

```bash
podman logs ontology_builder_frontend_1 --tail 30
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173/
```

Expected: no Vite/Tailwind error in the logs, `200` from curl. This only proves the pipeline *builds* — since no Tailwind utility class is used anywhere yet, there's nothing visually different to check until Task 2. Full visual confirmation that Tailwind is actually generating correct CSS happens in Task 3.

- [ ] **Step 6: Commit**

```bash
cd frontend && git add package.json vite.config.js src/style.css src/main.js
git commit -m "Add Tailwind CSS v4 tooling via @tailwindcss/vite"
```

---

### Task 2: Rewrite `SettingsPanel.vue`'s template and remove its scoped styles

**Files:**
- Modify: `frontend/src/components/SettingsPanel.vue`

**Interfaces:**
- Consumes: the Tailwind pipeline from Task 1 (every class used below must resolve to real generated CSS once that's wired up).
- Consumes (unchanged): every `ref`/`computed`/function name already defined in this file's `<script setup>` block (`showFileExplorer`, `showConfigurations`, `generateSchema`, `extractGraph`, `embed`, `activeVersionLabel`, `isGeneratingSchema`, `isExtracting`, `isEmbedding`, `currentFile`, `workflowProgress`, `workflowMessage`, `workflowError`, `schemaDocumentType`, `handleFileChange`, `isUploading`, `uploadError`, `files`, `selectFile`, `selectedFilename`, `schemaVersions`, `activateVersion`, `deleteVersion`, `versionActionError`, `schemas`, `isUsingSchema`, `useSchema`, `schemaUseError`, `model`, `renderMarkdown`, `onMarkdownToggle`, `graphRagHops`, `onHopsInput`, `maxSchemaChars`, `onMaxSchemaCharsInput`, `isResettingDb`, `resetDatabase`, `resetDbError`, `availableTypes`, `enabledTypes`, `toggleType`, `colorForType`, `availableEdgeTypes`, `enabledEdgeTypes`, `toggleEdgeType`, `colorForEdgeType`). None of these names change.
- Produces: no new public interface — `App.vue` and its props/emits contract with this component are unaffected.

**Design decisions made while translating the spec's mappings to concrete markup** (each is a direct, minor extension of an explicit spec mapping, not a new undocumented choice):
- The sidebar's own title bar (`<h1>Ontology Builder</h1>`) gets the same "dark header → white + bottom border" treatment the spec specifies for modal headers — the spec didn't call this out separately, but leaving one lone dark bar while every modal header nearby turns light would contradict the redesign's own stated direction.
- Checkboxes get `accent-indigo-600` (a native Tailwind utility, no plugin) rather than a `border`/`focus:ring` treatment — the spec's "Form inputs" mapping explicitly includes checkboxes, but the border/focus-ring pattern it describes is written for text-like inputs; `accent-indigo-600` is the direct Tailwind-native equivalent for checkboxes specifically.
- The file list's hover state (`hover:bg-slate-50`) reuses the exact hover treatment the spec specifies for the version list, for consistency between the two lists living in the same modal.

- [ ] **Step 1: Replace the `<template>` block**

Everything from `<template>` to `</template>` (currently `frontend/src/components/SettingsPanel.vue:413-687`) becomes:

```html
<template>
  <aside class="flex w-[270px] shrink-0 flex-col overflow-hidden border-r border-slate-200 bg-white">
    <h1 class="shrink-0 border-b border-slate-200 px-4 py-3 text-base font-semibold text-slate-900">
      Ontology Builder
    </h1>
    <div class="flex-1 overflow-y-auto p-3">
      <div class="mb-3 border-b border-slate-200 pb-2">
        <h2 class="mb-1.5 text-sm font-semibold text-slate-900">워크플로우</h2>
        <section class="mb-2.5">
          <button
            type="button"
            class="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            @click="showFileExplorer = true"
          >
            1 파일 선택
          </button>
        </section>

        <section class="mb-2.5">
          <div class="flex items-center gap-2">
            <button
              type="button"
              class="inline-flex items-center justify-center rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-default disabled:opacity-50 disabled:hover:bg-indigo-600"
              :disabled="!selectedFilename || isGeneratingSchema"
              @click="generateSchema"
            >
              {{ isGeneratingSchema ? '생성 중...' : '2 스키마 생성' }}
            </button>
            <select
              v-model="schemaDocumentType"
              :disabled="isGeneratingSchema"
              class="min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            >
              <option value="general">일반 문서</option>
              <option value="legal">법률·보험 문서</option>
            </select>
          </div>
          <p v-if="activeVersionLabel" class="mt-1 text-xs text-slate-500">{{ activeVersionLabel }}</p>
        </section>

        <section class="mb-2.5">
          <button
            type="button"
            class="inline-flex items-center justify-center rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-default disabled:opacity-50 disabled:hover:bg-indigo-600"
            :disabled="!selectedFilename || isExtracting"
            @click="extractGraph"
          >
            {{ isExtracting ? '추출 중...' : '3 그래프 추출' }}
          </button>
        </section>

        <section class="mb-2.5">
          <button
            type="button"
            class="inline-flex items-center justify-center rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:cursor-default disabled:opacity-50 disabled:hover:bg-indigo-600"
            :disabled="!selectedFilename || isEmbedding || !currentFile?.has_graph"
            @click="embed"
          >
            {{ isEmbedding ? '임베딩 생성 중...' : '4 임베딩 생성' }}
          </button>
        </section>

        <p v-if="workflowProgress" class="text-sm italic text-slate-500">{{ workflowProgress }}</p>
        <p v-if="workflowMessage" class="text-sm text-green-600">{{ workflowMessage }}</p>
        <p v-if="workflowError" class="text-sm text-red-600">{{ workflowError }}</p>
      </div>

      <div class="mb-3 border-b border-slate-200 pb-2">
        <h2 class="mb-1.5 text-sm font-semibold text-slate-900">실행 설정</h2>
        <section>
          <button
            type="button"
            class="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            @click="showConfigurations = true"
          >
            Configurations
          </button>
        </section>
      </div>

      <div>
        <h2 class="mb-1.5 text-sm font-semibold text-slate-900">온톨로지 설정</h2>
        <section class="mb-2.5">
          <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">그래프 노드 필터</h3>
          <p v-if="availableTypes.length === 0" class="text-sm text-slate-400">
            아직 추출된 그래프가 없습니다
          </p>
          <label v-for="type in availableTypes" :key="type" class="mb-1 flex items-center justify-between gap-2">
            <span class="flex items-center gap-1.5 break-words">
              <input
                type="checkbox"
                class="accent-indigo-600"
                :checked="enabledTypes.has(type)"
                @change="toggleType(type)"
              />
              {{ type }}
            </span>
            <span class="h-3 w-3 shrink-0 rounded-full" :style="{ background: colorForType(type) }"></span>
          </label>
        </section>

        <section>
          <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">그래프 엣지 필터</h3>
          <p v-if="availableEdgeTypes.length === 0" class="text-sm text-slate-400">
            아직 추출된 그래프가 없습니다
          </p>
          <label v-for="type in availableEdgeTypes" :key="type" class="mb-1 flex items-center justify-between gap-2">
            <span class="flex items-center gap-1.5 break-words">
              <input
                type="checkbox"
                class="accent-indigo-600"
                :checked="enabledEdgeTypes.has(type)"
                @change="toggleEdgeType(type)"
              />
              {{ type }}
            </span>
            <span class="h-[5px] w-5 shrink-0 rounded" :style="{ background: colorForEdgeType(type) }"></span>
          </label>
        </section>
      </div>
    </div>

    <div v-if="showFileExplorer" class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/40">
      <div class="flex max-h-[80vh] w-[480px] max-w-[90vw] flex-col overflow-hidden rounded-lg bg-white shadow-lg">
        <div class="flex shrink-0 items-center justify-between border-b border-slate-200 px-4 py-3">
          <h2 class="text-base font-semibold text-slate-900">File Explorer</h2>
          <button
            type="button"
            class="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
            @click="showFileExplorer = false"
          >닫기</button>
        </div>
        <div class="flex-1 overflow-y-auto p-4">
          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">문서 업로드</h3>
            <input
              type="file"
              @change="handleFileChange"
              :disabled="isUploading"
              class="text-sm text-slate-600 file:mr-2 file:rounded-md file:border-0 file:bg-slate-100 file:px-2 file:py-1 file:text-sm file:font-medium file:text-slate-700 hover:file:bg-slate-200"
            />
            <p v-if="isUploading" class="mt-1 text-sm text-slate-500">업로드 중...</p>
            <p v-if="uploadError" class="mt-1 text-sm text-red-600">{{ uploadError }}</p>
          </section>

          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">업로드된 문서</h3>
            <p v-if="files.length === 0" class="text-sm text-slate-400">문서가 없습니다</p>
            <ul v-else class="m-0 list-none p-0">
              <li
                v-for="f in files"
                :key="f.filename"
                class="cursor-pointer rounded-md px-2 py-1.5 text-sm break-words hover:bg-slate-50"
                :class="{ 'bg-indigo-50 font-semibold': f.filename === selectedFilename }"
                @click="selectFile(f.filename)"
              >
                <div class="break-words">{{ f.original_filename }}</div>
                <div class="mt-1 flex gap-1.5">
                  <span
                    class="inline-flex items-center rounded-full px-2 py-0.5 text-xs"
                    :class="f.has_schema ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-500'"
                  >스키마</span>
                  <span
                    class="inline-flex items-center rounded-full px-2 py-0.5 text-xs"
                    :class="f.has_graph ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-500'"
                    :title="f.has_graph ? `그래프DB: ${f.graphdb_name}` : ''"
                  >그래프</span>
                </div>
              </li>
            </ul>
          </section>

          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">선택된 문서의 스키마 버전</h3>
            <p v-if="!selectedFilename" class="text-sm text-slate-400">문서를 먼저 선택하세요</p>
            <p v-else-if="schemaVersions.length === 0" class="text-sm text-slate-400">생성된 버전이 없습니다</p>
            <ul v-else class="m-0 list-none p-0">
              <li
                v-for="v in schemaVersions"
                :key="v.version"
                class="flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-sm"
                :class="{ 'bg-indigo-50': v.is_active }"
              >
                <div class="flex items-center gap-1.5 break-words">
                  <span class="font-semibold text-slate-900">v{{ v.version }} · {{ v.document_type }}</span>
                  <span
                    v-if="v.is_active"
                    class="inline-flex items-center rounded-full bg-indigo-100 px-2 py-0.5 text-xs text-indigo-700"
                  >활성</span>
                  <span
                    class="inline-flex items-center rounded-full px-2 py-0.5 text-xs"
                    :class="v.has_graph ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-100 text-slate-500'"
                  >그래프</span>
                </div>
                <div class="flex shrink-0 gap-1">
                  <button
                    v-if="!v.is_active"
                    type="button"
                    class="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
                    @click="activateVersion(v.version)"
                  >활성화</button>
                  <button
                    type="button"
                    class="inline-flex items-center justify-center rounded-md border border-red-300 bg-white px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50"
                    @click="deleteVersion(v.version)"
                  >삭제</button>
                </div>
              </li>
            </ul>
            <p v-if="versionActionError" class="mt-1 text-sm text-red-600">{{ versionActionError }}</p>
          </section>

          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">스키마 라이브러리</h3>
            <p v-if="schemas.length === 0" class="text-sm text-slate-400">생성된 스키마가 없습니다</p>
            <ul v-else class="m-0 list-none p-0">
              <li
                v-for="s in schemas"
                :key="s.stem"
                class="cursor-pointer rounded-md px-2 py-1.5 text-sm break-words hover:bg-slate-50"
                :class="{ 'pointer-events-none opacity-50': isUsingSchema || !selectedFilename }"
                @click="useSchema(s.stem)"
              >
                {{ s.stem }}
              </li>
            </ul>
            <p v-if="isUsingSchema" class="mt-1 text-sm text-slate-500">적용 중...</p>
            <p v-if="schemaUseError" class="mt-1 text-sm text-red-600">{{ schemaUseError }}</p>
          </section>
        </div>
      </div>
    </div>

    <div v-if="showConfigurations" class="fixed inset-0 z-[1000] flex items-center justify-center bg-black/40">
      <div class="flex max-h-[80vh] w-[480px] max-w-[90vw] flex-col overflow-hidden rounded-lg bg-white shadow-lg">
        <div class="flex shrink-0 items-center justify-between border-b border-slate-200 px-4 py-3">
          <h2 class="text-base font-semibold text-slate-900">Configurations</h2>
          <button
            type="button"
            class="inline-flex items-center justify-center rounded-md border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
            @click="showConfigurations = false"
          >닫기</button>
        </div>
        <div class="flex-1 overflow-y-auto p-4">
          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">LLM 모델</h3>
            <p class="inline-block rounded-md bg-slate-100 px-2 py-1 font-mono text-sm text-slate-700">{{ model }}</p>
          </section>

          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">채팅 표시 설정</h3>
            <label class="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                class="accent-indigo-600"
                :checked="renderMarkdown"
                @change="onMarkdownToggle"
              />
              마크다운 HTML로 렌더링
            </label>
          </section>

          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">GraphRAG 설정</h3>
            <label class="flex items-center gap-2 text-sm text-slate-700">
              검색 hop 수
              <input
                type="number"
                min="1"
                max="5"
                :value="graphRagHops"
                @change="onHopsInput"
                class="w-16 rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </label>
          </section>

          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">스키마 생성 설정</h3>
            <label class="flex items-center gap-2 text-sm text-slate-700">
              최고 문자수
              <input
                type="number"
                min="1"
                step="1000"
                :value="maxSchemaChars"
                @change="onMaxSchemaCharsInput"
                class="w-24 rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </label>
            <p class="mt-1 text-xs text-slate-500">이 값을 넘는 문서는 스키마 생성 시 오류가 발생합니다. 필요시 늘리세요.</p>
          </section>

          <section class="mb-6">
            <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">데이터베이스 관리</h3>
            <button
              type="button"
              class="inline-flex items-center justify-center rounded-md border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50 disabled:cursor-default disabled:opacity-50 disabled:hover:bg-white"
              :disabled="isResettingDb"
              @click="resetDatabase"
            >
              {{ isResettingDb ? '초기화 중...' : 'LadybugDB 초기화' }}
            </button>
            <p class="mt-1 text-xs text-slate-500">WAL 파일 손상 등으로 그래프 조회가 계속 실패할 때 사용하세요. 모든 문서의 추출된 그래프가 삭제됩니다.</p>
            <p v-if="resetDbError" class="mt-1 text-sm text-red-600">{{ resetDbError }}</p>
          </section>
        </div>
      </div>
    </div>
  </aside>
</template>
```

- [ ] **Step 2: Delete the entire `<style scoped>` block**

Delete everything from `<style scoped>` to `</style>` (currently `frontend/src/components/SettingsPanel.vue:689-1014` — the very end of the file). Nothing replaces it; the file ends immediately after `</template>`.

- [ ] **Step 3: Confirm the `<script setup>` block is untouched**

Diff the file against its pre-Task-2 state and confirm the only hunks are within `<template>`/`<style>` — `git diff frontend/src/components/SettingsPanel.vue` should show zero changes between `<script setup>` and `</script>` (lines 1-411 in the pre-Task-2 file).

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/components/SettingsPanel.vue
git commit -m "Redesign SettingsPanel.vue with Tailwind utility classes"
```

---

### Task 3: Rebuild and verify every interaction still works

**Files:** none (verification only)

**Interfaces:** none — this is the integration check confirming Tasks 1-2 together produce a working, visually-redesigned, functionally-identical `SettingsPanel.vue`.

- [ ] **Step 1: Rebuild the stack**

```bash
podman-compose down && podman-compose up --build -d
```

- [ ] **Step 2: Confirm the served file reflects the new template**

Per this project's known virtiofs/Vite-staleness gotcha, diff what's served against the source before trusting anything:

```bash
curl -s http://localhost:5173/src/components/SettingsPanel.vue | grep -c "bg-indigo-600"
```

Expected: a non-zero count (the new primary-button class appears in the served source).

- [ ] **Step 3: Visually verify every interaction in the browser**

Using Playwright (or equivalent browser automation), against `http://localhost:5173`:

1. Sidebar renders with the new light look (white background, slate borders, no dark header bar) — three groups visible: 워크플로우, 실행 설정, 온톨로지 설정.
2. Click "1 파일 선택" — File Explorer modal opens with a white header (not dark) and a 닫기 button in the new secondary-button style.
3. Upload a document (or select an existing one from 업로드된 문서) — confirm the file list, 스키마/그래프 status pills (now rounded pill shapes, indigo when "on"), and selection highlight (`bg-indigo-50`) all work.
4. Confirm "선택된 문서의 스키마 버전" section shows version rows correctly, with 활성화/삭제 buttons in their new styles, and that clicking them still calls the version endpoints correctly (activate/delete still function — check via a subsequent version-list reload showing the change).
5. Confirm "스키마 라이브러리" list and its disabled/greyed-out state (when no file selected or applying) still render correctly.
6. Close the modal, click "2 스키마 생성" (indigo primary button) — confirm it still triggers schema generation, shows the progress message, and updates the active-version indicator.
7. Click "3 그래프 추출" and "4 임베딩 생성" — confirm both still work end-to-end (these depend on step 6 completing first, same as before this change).
8. Click "Configurations" — modal opens with the new light header; confirm LLM 모델 display, 마크다운 렌더링 checkbox (now indigo-tinted), GraphRAG hop-count number input, 최고 문자수 number input, and "LadybugDB 초기화" danger button (red outline) all still work and still emit the same events (`hops-changed`, `markdown-changed`, `database-reset`) that `App.vue` depends on.
9. Toggle a node/edge type filter checkbox in "온톨로지 설정" (only visible once a graph is extracted) — confirm the type/edge swatch colors (still inline `:style`-bound, unaffected by the class rewrite) and filter toggling still work.

- [ ] **Step 4: Visual spot-check of the four out-of-scope components**

Per the spec's documented Preflight side effect, briefly load a document with an extracted graph and check `ChatPanel.vue`, `DocumentPreview.vue`, `OntologyGraph.vue`, and `SchemaGraphPreview.vue` — confirm none of them look *broken* (overlapping elements, unreadable text, collapsed layout). Looking visually plainer/unstyled relative to the new `SettingsPanel.vue` is expected and fine; that mismatch is resolved in phase 2.

- [ ] **Step 5: Report findings**

Note any visual bugs or regressions found. If something is broken (not just "still looks like the old CSS," which is expected for the four out-of-scope components), fix it in Task 2 before proceeding — this task is the acceptance gate for the whole plan.
