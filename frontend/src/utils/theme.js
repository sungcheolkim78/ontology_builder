import { reactive } from 'vue'

// Same shape as api.js's authState/token handling: a reactive module-level
// state object backed by localStorage, so any component can read
// themeState.mode reactively and any component can call setTheme without
// prop/emit plumbing through App.vue.
const THEME_KEY = 'theme'

function readStoredTheme() {
  return localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light'
}

export const themeState = reactive({
  mode: readStoredTheme(),
})

// Tailwind's darkMode: 'class' config expects this exact class on an
// ancestor element (see tailwind.config.js / style.css's CSS variables).
function applyTheme(mode) {
  document.documentElement.classList.toggle('dark', mode === 'dark')
}

applyTheme(themeState.mode)

export function setTheme(mode) {
  themeState.mode = mode
  localStorage.setItem(THEME_KEY, mode)
  applyTheme(mode)
}
