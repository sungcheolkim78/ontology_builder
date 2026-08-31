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
