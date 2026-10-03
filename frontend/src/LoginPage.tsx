import { useEffect, useState, type FormEvent } from 'react'
import { validateLogin } from './login'
import './LoginPage.css'

type ServerState = 'checking' | 'ok' | 'down'

export default function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [server, setServer] = useState<ServerState>('checking')

  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.json())
      .then((h) => setServer(h.database === 'ok' ? 'ok' : 'down'))
      .catch(() => setServer('down'))
  }, [])

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    // Real sign-in arrives in Stage 1; until then the form only checks what was typed.
    setMessage(validateLogin({ email, password }) ?? 'Sign-in is not switched on yet.')
  }

  return (
    <main className="login">
      <form className="login-panel" onSubmit={onSubmit} noValidate>
        <div className="brand">
          <img src="/favicon.svg" alt="" width={36} height={36} />
          <div>
            <h1>AR Portfolio Bot</h1>
            <p className="muted">Sign in to continue</p>
          </div>
        </div>

        <label>
          <span>Email</span>
          <input
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label>
          <span>Password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {message && (
          <p className="message" role="alert">
            {message}
          </p>
        )}

        <button type="submit">Sign in</button>

        <p className={`server server-${server}`}>
          <span className="dot" aria-hidden="true" />
          {server === 'checking' && 'Checking server…'}
          {server === 'ok' && 'Server connected'}
          {server === 'down' && 'Server not reachable'}
        </p>
      </form>
    </main>
  )
}
