/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{vue,js}'],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          '"Pretendard Variable"',
          'Pretendard',
          '-apple-system',
          'BlinkMacSystemFont',
          '"Segoe UI"',
          'system-ui',
          'sans-serif',
        ],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      colors: {
        canvas: '#0b0d12',
        surface: {
          DEFAULT: '#12141c',
          raised: '#181b26',
          sunken: '#0b0d12',
        },
        border: {
          DEFAULT: '#242836',
          subtle: '#1b1e29',
        },
        accent: {
          DEFAULT: '#5b8cff',
          hover: '#75a1ff',
          muted: '#2a3a63',
        },
        ink: {
          DEFAULT: '#e6e8ef',
          muted: '#9aa1b2',
          faint: '#5f6577',
        },
      },
      boxShadow: {
        panel: '0 1px 2px rgba(0,0,0,0.4), 0 0 0 1px rgba(255,255,255,0.02)',
      },
    },
  },
  plugins: [],
}
