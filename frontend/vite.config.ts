import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

// A new id for every build. The running app compares it with /version.json to notice a deploy.
const BUILD_ID = new Date().toISOString()

function versionFile(): Plugin {
  return {
    name: 'arpb-version-file',
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'version.json', source: JSON.stringify({ build: BUILD_ID }) })
    },
  }
}

export default defineConfig({
  plugins: [react(), versionFile()],
  define: { __BUILD_ID__: JSON.stringify(BUILD_ID) },
  server: {
    proxy: {
      // Keep the browser's Host header: the backend refuses changes whose Origin differs from it.
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true, changeOrigin: false },
    },
  },
})
