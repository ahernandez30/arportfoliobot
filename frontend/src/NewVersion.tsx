import { useEffect, useState } from 'react'

const CHECK_EVERY_MS = 5 * 60 * 1000

/** Shows a banner when a newer version of the site has been deployed than the one running in this tab. */
export default function NewVersion() {
  const [newer, setNewer] = useState(false)

  useEffect(() => {
    let stopped = false
    async function check() {
      try {
        const r = await fetch('/version.json', { cache: 'no-store' })
        if (!r.ok) return
        const { build } = (await r.json()) as { build?: string }
        if (!stopped && build && build !== __BUILD_ID__) setNewer(true)
      } catch {
        // Offline or the file is missing: try again later.
      }
    }
    void check()
    const timer = window.setInterval(check, CHECK_EVERY_MS)
    window.addEventListener('focus', check)
    return () => {
      stopped = true
      window.clearInterval(timer)
      window.removeEventListener('focus', check)
    }
  }, [])

  if (!newer) return null
  return (
    <div className="msg msg-warn new-version" role="status">
      <span>A new version of the site is available.</span>
      <button className="btn btn-small btn-primary" onClick={() => window.location.reload()}>
        Reload
      </button>
    </div>
  )
}
