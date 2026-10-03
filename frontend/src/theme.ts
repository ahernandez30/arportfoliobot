import type { Theme } from './api'

const STORAGE_KEY = 'arpb-theme'

/** Applies the theme to the page. Remembered in this browser so the next visit starts in it. */
export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'light' ? '#f4f6f9' : '#0E1116')
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // Storage blocked: the theme still applies for this visit.
  }
}

export function rememberedTheme(): Theme {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}
