import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'
import { api, ApiError, type LoginResult } from './api'
import { useAuth } from './auth'
import { emailProblem } from './forms'
import './LoginPage.css'

export function Brand({ subtitle }: { subtitle: string }) {
  return (
    <div className="brand">
      <img src="/favicon.svg" alt="" width={36} height={36} />
      <div>
        <h1>AR Portfolio Bot</h1>
        <p className="muted">{subtitle}</p>
      </div>
    </div>
  )
}

/** Second sign-in step: the 6-digit code from the authenticator app. */
export function CodeStep({ onDone, onRestart }: { onDone: () => void; onRestart: () => void }) {
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    const digits = code.replace(/\D/g, '')
    if (digits.length !== 6) return setError('Enter the 6-digit code from your authenticator app.')
    setBusy(true)
    setError(null)
    try {
      await api('POST', '/api/auth/code', { code: digits })
      onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.')
      if (err instanceof ApiError && err.status === 401 && /expired/.test(err.message)) onRestart()
      setCode('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="login-panel" onSubmit={submit} noValidate>
      <Brand subtitle="Two-step sign-in" />
      <p className="muted">Open your authenticator app and enter the code for AR Portfolio Bot.</p>
      <label className="field">
        <span>6-digit code</span>
        <input
          className="input code"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={7}
          autoFocus
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      </label>
      {error && (
        <p className="msg msg-error" role="alert">
          {error}
        </p>
      )}
      <button className="btn btn-primary" type="submit" disabled={busy}>
        {busy ? 'Checking…' : 'Continue'}
      </button>
      <button className="btn" type="button" onClick={onRestart}>
        Back
      </button>
    </form>
  )
}

export default function LoginPage() {
  const { me, refresh } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [needCode, setNeedCode] = useState(false)

  const from = (location.state as { from?: string } | null)?.from ?? '/'
  if (me) return <Navigate to={from} replace />

  async function finish() {
    await refresh()
    navigate(from, { replace: true })
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    const problem = emailProblem(email) ?? (password ? null : 'Enter your password.')
    if (problem) return setError(problem)
    setBusy(true)
    setError(null)
    try {
      const r = await api<LoginResult>('POST', '/api/auth/login', { email: email.trim(), password })
      setPassword('')
      if (r.status === 'code_required') setNeedCode(true)
      else await finish()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login">
      {needCode ? (
        <CodeStep onDone={finish} onRestart={() => setNeedCode(false)} />
      ) : (
        <form className="login-panel" onSubmit={onSubmit} noValidate>
          <Brand subtitle="Sign in to continue" />
          <label className="field">
            <span>Email</span>
            <input
              className="input"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {error && (
            <p className="msg msg-error" role="alert">
              {error}
            </p>
          )}
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
          <p className="muted small">Forgot your password? Ask the admin for a reset link.</p>
        </form>
      )}
    </main>
  )
}
