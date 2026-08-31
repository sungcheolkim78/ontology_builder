# Simple Shared-Password Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate the deployed app behind one fixed shared password (no accounts) — a login screen before the main app renders, using a stateless derived token so no session storage is needed. All users already see the same data (one shared backend/data disk); this plan only adds the access gate.

**Architecture:** Backend: a new `app/auth.py` module (`APP_PASSWORD` env var, `issue_token`/`is_valid_token`), a `POST /api/login` endpoint, and an HTTP middleware gating every `/api/*` route except `/api/login`/`/api/config` (added after `CORSMiddleware` so CORS preflight still works). The gate is a no-op whenever `APP_PASSWORD` is unset, so local dev and the existing test suite are unaffected. Frontend: `utils/api.js` gains token attachment + 401 handling via a `reactive` `authState`; a new `LoginScreen.vue`; `App.vue` fetches `/api/config`'s new `auth_required` flag once on mount and conditionally renders the login screen or the existing app, with zero changes to any existing `ref`/function in `App.vue`.

**Tech Stack:** FastAPI (Python 3.14), stdlib `hashlib`/`secrets` (no new backend dependency), Vue 3 (`<script setup>`), Vite, Tailwind CSS (existing dark slate/accent tokens, no new ones), Render (`render.yaml`).

**Spec:** `docs/superpowers/specs/2026-09-01-simple-shared-password-login-design.md`

## Global Constraints

- `APP_PASSWORD` unset ⇒ the whole feature is a no-op: `/api/config`'s `auth_required` is `False`, the auth middleware never rejects anything, `App.vue` never renders `LoginScreen`. This must hold with zero special-casing in `App.vue`/`main.py` beyond reading that one flag — no `NODE_ENV`/local-dev branch anywhere.
- No new backend dependency — `hashlib`/`secrets` are stdlib.
- No cookies, no server-side session store — token is `sha256(APP_PASSWORD)`, re-derived per request, never persisted server-side.
- `app.add_middleware(CORSMiddleware, ...)` must remain the **first** middleware registered in `main.py` — the new `require_auth` middleware is added via `@app.middleware("http")` immediately after it, never before, so CORS stays outermost and still intercepts preflight `OPTIONS` requests before auth logic runs.
- No existing test's assertions change — every current test runs with `APP_PASSWORD` unset (never set by `conftest.py` or any existing test), so the auth gate stays inactive for all of them.
- No changes to `SettingsPanel.vue`'s existing `/api/config` fetch (it only reads `model` today, and `auth_required` being added to the response doesn't break that).
- No logout button, no token expiry, no login rate limiting — out of scope per spec.

---

### Task 1: Backend — `app/auth.py`, `/api/login`, `/api/config` flag, and the gating middleware

**Files:**
- Create: `backend/app/auth.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_auth.py`
- Modify: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `POST /api/login` (`{password}` → `{token}` or 401), `GET /api/config` now also returns `auth_required: bool`, and a gate that 401s any other `/api/*` route when `APP_PASSWORD` is set and the request lacks a valid `Authorization: Bearer {token}` header.
- Consumes: nothing new — reads `APP_PASSWORD` from the environment, same pattern as every other env-backed module constant in this codebase (`parser.DATA_DIR`, `graphdb.DB_PATH`).

- [ ] **Step 1: Create `backend/app/auth.py`**

```python
import hashlib
import os
import secrets

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")


def issue_token(password: str) -> str | None:
    if not APP_PASSWORD or not secrets.compare_digest(password, APP_PASSWORD):
        return None
    return _expected_token()


def is_valid_token(token: str) -> bool:
    return bool(APP_PASSWORD) and secrets.compare_digest(token, _expected_token())


def _expected_token() -> str:
    return hashlib.sha256(APP_PASSWORD.encode()).hexdigest()
```

- [ ] **Step 2: Wire the middleware and `/api/login` into `main.py`**

Add to the imports:

```python
from app.auth import APP_PASSWORD, is_valid_token, issue_token
```

Immediately after the existing `app.add_middleware(CORSMiddleware, ...)` block, add:

```python
_UNAUTHENTICATED_PATHS = {"/health", "/api/login", "/api/config"}


@app.middleware("http")
async def require_auth(request, call_next):
    if APP_PASSWORD and request.url.path not in _UNAUTHENTICATED_PATHS:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not is_valid_token(token):
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=401, content={"detail": "unauthorized"})
    return await call_next(request)
```

(Prefer moving `from fastapi.responses import JSONResponse` up to the top-level import block alongside the existing `from fastapi.responses import PlainTextResponse` rather than importing inline — written inline above only to show exactly where it's needed.)

Change the existing `/api/config` endpoint:

```python
@app.get("/api/config")
def get_config():
    return {"model": get_model_name(), "auth_required": bool(APP_PASSWORD)}
```

Add the login endpoint near it:

```python
class LoginRequest(BaseModel):
    password: str


@app.post("/api/login")
def login(request: LoginRequest):
    token = issue_token(request.password)
    if token is None:
        raise HTTPException(status_code=401, detail="invalid password")
    return {"token": token}
```

- [ ] **Step 3: `backend/tests/test_auth.py`**

```python
from fastapi.testclient import TestClient

from app.main import app


def test_login_rejects_when_app_password_unset(monkeypatch):
    monkeypatch.setattr("app.auth.APP_PASSWORD", "")
    monkeypatch.setattr("app.main.APP_PASSWORD", "")
    client = TestClient(app)

    response = client.post("/api/login", json={"password": "anything"})

    assert response.status_code == 401


def test_login_accepts_correct_password_and_protected_route_requires_it(monkeypatch):
    monkeypatch.setattr("app.auth.APP_PASSWORD", "hunter2")
    monkeypatch.setattr("app.main.APP_PASSWORD", "hunter2")
    client = TestClient(app)

    unauthenticated = client.get("/api/hello")
    assert unauthenticated.status_code == 401

    login = client.post("/api/login", json={"password": "hunter2"})
    assert login.status_code == 200
    token = login.json()["token"]

    authenticated = client.get("/api/hello", headers={"Authorization": f"Bearer {token}"})
    assert authenticated.status_code == 200


def test_login_rejects_wrong_password(monkeypatch):
    monkeypatch.setattr("app.auth.APP_PASSWORD", "hunter2")
    monkeypatch.setattr("app.main.APP_PASSWORD", "hunter2")
    client = TestClient(app)

    response = client.post("/api/login", json={"password": "wrong"})

    assert response.status_code == 401


def test_health_and_config_and_login_stay_open_when_app_password_set(monkeypatch):
    monkeypatch.setattr("app.auth.APP_PASSWORD", "hunter2")
    monkeypatch.setattr("app.main.APP_PASSWORD", "hunter2")
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/api/config").status_code == 200
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
```

Note: `monkeypatch.setattr` on both `app.auth.APP_PASSWORD` and
`app.main.APP_PASSWORD` is required because `main.py` imports the name
directly (`from app.auth import APP_PASSWORD, ...`), which binds its
own separate reference at import time — patching only
`app.auth.APP_PASSWORD` would leave `app.main.APP_PASSWORD` (used by
the middleware) unchanged. `issue_token`/`is_valid_token` read the
module-level `app.auth.APP_PASSWORD` internally, so both patches are
needed for the two different call sites (middleware vs. `issue_token`)
to see the same patched value.

- [ ] **Step 4: Add one assertion to `test_config.py`**

```python
def test_get_config_returns_configured_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    client = TestClient(app)

    response = client.get("/api/config")

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "openai/gpt-4o-mini"
    assert body["auth_required"] is False
```

(Changed from an exact-dict `==` to field assertions since the dict now
has two keys — the `model` assertion is unchanged in spirit.)

- [ ] **Step 5: Run the backend test suite**

```bash
cd backend && source .venv/bin/activate
OPENROUTER_API_KEY=dummy python -m pytest tests/ -v
```

Expected: all tests pass, including the new `test_auth.py` and the
updated `test_config.py`, with zero changes needed to any other test
file.

- [ ] **Step 6: Commit**

```bash
git add backend/app/auth.py backend/app/main.py backend/tests/test_auth.py backend/tests/test_config.py
git commit -m "Add shared-password login gate to the backend"
```

---

### Task 2: Frontend — `LoginScreen.vue`, token plumbing in `api.js`, gating in `App.vue`

**Files:**
- Modify: `frontend/src/utils/api.js`
- Create: `frontend/src/components/LoginScreen.vue`
- Modify: `frontend/src/App.vue`

**Interfaces:**
- Consumes: `POST /api/login`, `/api/config`'s new `auth_required` field (Task 1).
- Produces: `authState` (reactive `{token}`), `setToken`/`clearToken` exported from `utils/api.js`, consumed only by `App.vue` and `LoginScreen.vue` — every other component keeps importing only `apiFetch`/`API_BASE` exactly as today, unaware auth exists.

- [ ] **Step 1: Add token handling to `frontend/src/utils/api.js`**

Replace the whole file with:

```js
import { reactive } from 'vue'

// Local dev leaves VITE_API_BASE_URL unset, so API_BASE is '' and apiFetch
// hits the same relative /api/* paths Vite's dev-server proxy already
// forwards to the backend container (see vite.config.js). A production
// static-site build (no dev-server proxy available) sets VITE_API_BASE_URL
// at build time to the deployed backend's own origin, so the same relative
// paths resolve to an absolute cross-origin URL instead.
export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

const TOKEN_KEY = 'auth_token'

// reactive (not a plain module-level variable) so App.vue re-renders the
// moment a 401 anywhere clears the token, without any component besides
// App.vue/LoginScreen.vue needing to know auth exists at all.
export const authState = reactive({
  token: localStorage.getItem(TOKEN_KEY),
})

export function setToken(token) {
  authState.token = token
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken() {
  authState.token = null
  localStorage.removeItem(TOKEN_KEY)
}

export async function apiFetch(path, options = {}) {
  const headers = { ...options.headers }
  if (authState.token) {
    headers.Authorization = `Bearer ${authState.token}`
  }
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers })
  if (response.status === 401 && path !== '/api/login') {
    clearToken()
  }
  return response
}
```

- [ ] **Step 2: Create `frontend/src/components/LoginScreen.vue`**

```html
<script setup>
import { ref } from 'vue'
import { apiFetch, setToken } from '../utils/api'

const password = ref('')
const error = ref('')
const isSubmitting = ref(false)

async function submit() {
  isSubmitting.value = true
  error.value = ''
  try {
    const response = await apiFetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: password.value }),
    })
    if (!response.ok) {
      error.value = '비밀번호가 올바르지 않습니다.'
      return
    }
    const data = await response.json()
    setToken(data.token)
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <div class="flex h-screen w-screen items-center justify-center bg-canvas">
    <form
      class="flex w-80 flex-col gap-3 rounded-lg border border-border bg-surface-raised p-6"
      @submit.prevent="submit"
    >
      <h1 class="text-sm font-semibold text-ink">Ontology Builder</h1>
      <input
        v-model="password"
        type="password"
        placeholder="비밀번호"
        autofocus
        class="field"
      />
      <button type="submit" class="btn-primary" :disabled="isSubmitting">
        {{ isSubmitting ? '확인 중...' : '입장' }}
      </button>
      <p v-if="error" class="text-xs text-red-400">{{ error }}</p>
    </form>
  </div>
</template>
```

- [ ] **Step 3: Gate `App.vue` on `auth_required`**

At the top of `<script setup>`, add to the existing imports:

```js
import { onMounted, ref } from 'vue'
import { apiFetch, authState } from './utils/api'
import LoginScreen from './components/LoginScreen.vue'
```

(`computed`/`ref` are already imported from `'vue'` — merge `onMounted`
into that same import line rather than adding a second one.)

Add near the other top-level `ref`s (no existing `ref` is removed or
renamed):

```js
const authRequired = ref(false)
const configLoaded = ref(false)

onMounted(async () => {
  const response = await apiFetch('/api/config')
  const data = await response.json()
  authRequired.value = data.auth_required
  configLoaded.value = true
})
```

Change the template's root from:

```html
<template>
  <div class="flex h-screen w-screen flex-col overflow-hidden bg-canvas text-ink">
```

to:

```html
<template>
  <div v-if="!configLoaded"></div>
  <LoginScreen v-else-if="authRequired && !authState.token" />
  <div v-else class="flex h-screen w-screen flex-col overflow-hidden bg-canvas text-ink">
```

...and add the matching closing `</div>` for the new `v-else` branch at
the very end of the file (the existing closing `</div></template>` at
the bottom of the current root `<div>` becomes the close of this
`v-else` branch — no other structural change to the template between
the header and the final tags).

- [ ] **Step 4: Rebuild and manually verify**

```bash
podman-compose down && podman-compose up --build -d
```

With `APP_PASSWORD` unset (local `.env` default):
1. Load `http://localhost:5173` — the app renders immediately, no login
   screen (confirms the no-op path).

Then set `APP_PASSWORD=testpass123` in `backend/.env`, rebuild, and:
2. Load the app — the login screen appears instead.
3. Enter the wrong password — error message shown, still on the login
   screen.
4. Enter `testpass123` — app renders normally; confirm a real action
   (e.g. selecting a document, sending a chat message) still works,
   proving the token is actually attached to subsequent requests.
5. Reload the page — app renders directly (token persisted in
   `localStorage`), no login screen shown again.
6. In devtools, clear `localStorage`'s `auth_token` and reload — login
   screen reappears.

Unset `APP_PASSWORD` again afterward (or leave it, per what the user
wants for their own local dev going forward) and rebuild once more to
leave the stack in a clean state.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/utils/api.js src/components/LoginScreen.vue src/App.vue
git commit -m "Add login screen gated on backend's auth_required flag"
```

---

### Task 3: Deploy — `render.yaml` and the real password

**Files:**
- Modify: `render.yaml`

**Interfaces:** none — this only wires the already-implemented gate into the production deploy.

- [ ] **Step 1: Add `APP_PASSWORD` to the backend service in `render.yaml`**

```yaml
    envVars:
      - key: OPENROUTER_API_KEY
        sync: false # set the real value in the Render dashboard, not here
      - key: APP_PASSWORD
        sync: false # set the real value in the Render dashboard, not here
      - key: OPENROUTER_MODEL
        value: google/gemini-3.7-flash
```

- [ ] **Step 2: Commit**

```bash
git add render.yaml
git commit -m "Wire APP_PASSWORD into the Render backend service"
```

- [ ] **Step 3: Set the real password in the Render dashboard (manual, not committed)**

In the Render dashboard, on `ontology-builder-backend`'s environment
settings, set `APP_PASSWORD` to the actual shared password. This step
has no corresponding code change — call it out to the user rather than
silently assuming it's done, since a push alone does not enable the
gate without this.

- [ ] **Step 4: Verify against the live deploy**

Once both services have redeployed (`render.yaml` changes require a
push + Render auto-deploy, or a manual deploy trigger), load the
production frontend URL and confirm the login screen appears, and that
the correct password grants access to a working app (documents load,
chat responds).
