// Local dev leaves VITE_API_BASE_URL unset, so API_BASE is '' and apiFetch
// hits the same relative /api/* paths Vite's dev-server proxy already
// forwards to the backend container (see vite.config.js). A production
// static-site build (no dev-server proxy available) sets VITE_API_BASE_URL
// at build time to the deployed backend's own origin, so the same relative
// paths resolve to an absolute cross-origin URL instead.
export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? ''

export function apiFetch(path, options) {
  return fetch(`${API_BASE}${path}`, options)
}
