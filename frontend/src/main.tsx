import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/hanken-grotesk'
import '@fontsource-variable/jetbrains-mono'
import './index.css'
import './ui.css'
import App from './App.tsx'
import { applyTheme, rememberedTheme } from './theme'

applyTheme(rememberedTheme())

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
