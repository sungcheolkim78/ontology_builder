# Simple Shared-Password Login

## Goal

The app is now served publicly on Render. Add the lightest possible
access gate: one fixed password (not per-user accounts), shown as a
login screen before the main app renders. Every user who knows the
password sees the exact same data — this is already true today (one
shared backend, one shared `backend/data` disk/graph DB, no per-user
scoping anywhere in the schema), so this spec adds **only** the access
gate itself, nothing about data isolation.

This is a casual-access deterrent, not a security boundary: it exists
to keep the app from being stumbled into or scraped by anyone with the
URL, not to protect sensitive data or defend against a motivated
attacker. Explicitly out of scope: per-user accounts, login-attempt
rate limiting/lockout, token expiry, a logout button, password
rotation tooling.

## Auth mechanism: stateless derived token, no session store

Two shapes were considered:

- **(Rejected) Server-side session store** (random session id →
  in-memory dict or a new DB table, returned as a cookie). Rejected as
  overkill for one shared password with no per-user state to track,
  and cookies would need `SameSite=None; Secure` to survive the
  frontend/backend being different origins in production (static site
  vs. FastAPI service, per `render.yaml`) — an `Authorization` header
  avoids that entirely.
- **(Chosen) Stateless derived token**: `POST /api/login` checks the
  submitted password against `APP_PASSWORD` (a new env var) with
  `secrets.compare_digest`, and on success returns
  `token = sha256(APP_PASSWORD)`. The frontend stores this token in
  `localStorage` and sends it as `Authorization: Bearer {token}` on
  every subsequent API call. The backend re-derives the same hash from
  its own `APP_PASSWORD` and compares — no server-side state, no DB
  row, works identically across process restarts or (hypothetically)
  multiple backend instances.

The token is a fixed value for as long as `APP_PASSWORD` doesn't
change — it never expires and isn't tied to a browser session. That's
an accepted tradeoff for "아주 간단한" scope (see Goal): anyone holding
the token has exactly the same access as anyone holding the password,
forever, until `APP_PASSWORD` is rotated on Render (which invalidates
every previously-issued token at once, since it's derived, not stored).

## Opt-in gate, keyed off `APP_PASSWORD` being set

The gate is **only active when `APP_PASSWORD` is a non-empty env var**.
Local dev (`podman-compose`, no `APP_PASSWORD` in `backend/.env`) and
the existing backend test suite both run with it unset, so the
middleware becomes a no-op and every request passes through exactly as
it does today. This mirrors the existing pattern in `telemetry.py`,
where instrumentation is only active if `OTEL_EXPORTER_OTLP_ENDPOINT`
is set and otherwise the no-op tracer runs — same shape, different
concern. This also means **no existing test needs to change**: none of
them set `APP_PASSWORD`, so the auth middleware never triggers for the
current suite.

## Backend changes

**New module `backend/app/auth.py`** (mirrors the single-purpose,
module-level-env-read style of `app/parser.py`/`app/embeddings.py`):

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

**`main.py` changes:**

1. New request model + endpoint, placed near the top alongside other
   simple endpoints:

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

2. New HTTP middleware, added **immediately after** the existing
   `app.add_middleware(CORSMiddleware, ...)` call (order matters: CORS
   must stay the outermost middleware so it still intercepts
   preflight `OPTIONS` requests directly, before this one runs —
   `CORSMiddleware` is registered first today and this spec preserves
   that by only ever appending after it):

```python
from starlette.requests import Request
from starlette.responses import JSONResponse

_UNAUTHENTICATED_PATHS = {"/health", "/api/login"}


@app.middleware("http")
async def require_auth(request: Request, call_next):
    if APP_PASSWORD and request.url.path not in _UNAUTHENTICATED_PATHS:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not is_valid_token(token):
            return JSONResponse(status_code=401, content={"detail": "unauthorized"})
    return await call_next(request)
```

`/health`, `/api/login`, and `/api/config` are exempt; every other
route requires the token once `APP_PASSWORD` is set. `/health` stays
exempt because Render's own `healthCheckPath` (see `render.yaml`) hits
it with no auth context and must keep succeeding for the service to be
considered up. `/api/config` stays exempt because the frontend must be
able to call it *before* any login has happened, to learn whether a
login is even required (see Frontend changes) — neither value it
returns (`model`, `auth_required`) is sensitive.

**`/api/config` gains one field:**

```python
@app.get("/api/config")
def get_config():
    return {"model": get_model_name(), "auth_required": bool(APP_PASSWORD)}
```

## Frontend changes

**`frontend/src/utils/api.js`** — the single existing choke point
every API call already goes through — gains token attachment and 401
handling:

```js
import { reactive } from 'vue'

export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

const TOKEN_KEY = 'auth_token'

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

`authState` is a `reactive` object (not a plain module-level `let`) so
`App.vue` can react to `clearToken()`/`setToken()` calls that happen
deep inside `apiFetch` — every other component already imports
`apiFetch` from this same module, so no component besides `App.vue`
needs to know auth exists at all.

**New `frontend/src/components/LoginScreen.vue`** — a single centered
card, reusing the existing dark slate/accent design tokens from
`style.css` (`bg-canvas`, `bg-surface-raised`, `border-border`,
`.field`, `.btn-primary`) so it matches the rest of the app with no
new CSS tokens:

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

**`App.vue`** — fetches `/api/config` once on mount to learn whether a
login is required at all, so local dev (`APP_PASSWORD` unset) never
shows the screen — no second env var, no sentinel token, just the same
flag the backend already exposes:

```html
<script setup>
import { onMounted, ref } from 'vue'
import { apiFetch, authState } from './utils/api'
import LoginScreen from './components/LoginScreen.vue'
// ...existing imports unchanged

const authRequired = ref(false)
const configLoaded = ref(false)

onMounted(async () => {
  const response = await apiFetch('/api/config')
  const data = await response.json()
  authRequired.value = data.auth_required
  configLoaded.value = true
})
// ...existing script unchanged
</script>

<template>
  <div v-if="!configLoaded" />
  <LoginScreen v-else-if="authRequired && !authState.token" />
  <div v-else class="flex h-screen w-screen flex-col overflow-hidden bg-canvas text-ink">
    <!-- existing template, unchanged -->
  </div>
</template>
```

The `v-if="!configLoaded"` branch is a brief blank frame while the one
`/api/config` round-trip resolves — avoids a flash of the main app (or
of the login screen) before the backend has actually said which one is
correct.

## `render.yaml` changes

Add one env var to the backend service, alongside the existing
`OPENROUTER_API_KEY` secret:

```yaml
      - key: APP_PASSWORD
        sync: false # set the real value in the Render dashboard, not here
```

The frontend service needs no changes — it never sees the password,
only the derived token round-tripped through `/api/login`.

## Testing

- `backend/tests/test_auth.py` (new): `issue_token`/`is_valid_token`
  round-trip; wrong password rejected; empty `APP_PASSWORD` always
  rejects (gate never accidentally "open" with a blank password);
  `/api/login` endpoint success/failure via `TestClient` with
  `monkeypatch.setenv("APP_PASSWORD", ...)` — must `importlib.reload`
  or otherwise re-read `app.auth.APP_PASSWORD` after patching, since
  it's read once at module import like every other env-backed module
  constant in this codebase (see `graphdb.DB_PATH`, `parser.DATA_DIR`
  for the existing pattern this follows).
- One test confirming `/health`, `/api/login`, and `/api/config` stay
  reachable with no `Authorization` header even when `APP_PASSWORD` is
  set, and one confirming a protected route (`/api/hello` is enough)
  401s without a valid token and 200s with one.
- `test_config.py`'s existing test gains an assertion that
  `auth_required` is `False` when `APP_PASSWORD` is unset (the default
  in that test today) — no existing assertion needs to change, only a
  new one added.
- No new frontend tests (none exist in this repo today — manual/
  Playwright verification only, per existing project practice).

## Out of scope

- Per-user accounts, rate limiting/lockout on failed logins, token
  expiry, logout button, password rotation UI.
- Any change to data isolation/scoping — already single-shared by
  construction (see Goal).
