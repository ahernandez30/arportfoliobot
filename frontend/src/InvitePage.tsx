import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router'
import { api, ApiError, type InviteInfo, type LoginResult } from './api'
import { useAuth } from './auth'
import { emailProblem, MIN_PASSWORD, newPasswordProblem } from './forms'
import { Brand, CodeStep } from './LoginPage'
import './LoginPage.css'

/** Opens a one-time link: create an account, or choose a new password. */
export default function InvitePage() {
  const { token = '' } = useParams()
  const { refresh } = useAuth()
  const navigate = useNavigate()
  const [info, setInfo] = useState<InviteInfo | null>(null)
  const [linkError, setLinkError] = useState<string | null>(null)
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [needCode, setNeedCode] = useState(false)

  useEffect(() => {
    api<InviteInfo>('GET', `/api/auth/invite/${encodeURIComponent(token)}`)
      .then(setInfo)
      .catch((e) => setLinkError(e instanceof ApiError ? e.message : 'This link could not be checked.'))
  }, [token])

  async function finish() {
    await refresh()
    navigate('/', { replace: true })
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!info) return
    const signup = info.kind === 'signup'
    const problem =
      (signup && !info.email ? emailProblem(email) : null) ??
      (signup && !name.trim() ? 'Enter your name.' : null) ??
      newPasswordProblem(password, repeat)
    if (problem) return setError(problem)
    setBusy(true)
    setError(null)
    try {
      const r = await api<LoginResult>('POST', `/api/auth/invite/${encodeURIComponent(token)}`, {
        email: email.trim(),
        display_name: name.trim(),
        password,
      })
      if (r.status === 'code_required') setNeedCode(true)
      else await finish()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  if (needCode) {
    return (
      <main className="login">
        <CodeStep onDone={finish} onRestart={() => navigate('/login', { replace: true })} />
      </main>
    )
  }

  const signup = info?.kind !== 'reset'
  return (
    <main className="login">
      <form className="login-panel" onSubmit={submit} noValidate>
        <Brand subtitle={signup ? 'Create your account' : 'Choose a new password'} />
        {linkError && (
          <>
            <p className="msg msg-error" role="alert">
              {linkError}
            </p>
            <button className="btn" type="button" onClick={() => navigate('/login')}>
              Go to sign in
            </button>
          </>
        )}
        {!info && !linkError && <p className="muted">Checking the link…</p>}
        {info && (
          <>
            {info.role === 'admin' && signup && <p className="msg">This account will be an admin.</p>}
            {info.email ? (
              <p className="muted">
                Email: <strong>{info.email}</strong>
              </p>
            ) : (
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
            )}
            {signup && (
              <label className="field">
                <span>Your name</span>
                <input
                  className="input"
                  autoComplete="name"
                  maxLength={80}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
            )}
            <label className="field">
              <span>{signup ? 'Password' : 'New password'}</span>
              <input
                className="input"
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <span className="hint">At least {MIN_PASSWORD} characters. A few words in a row work well.</span>
            </label>
            <label className="field">
              <span>Type it again</span>
              <input
                className="input"
                type="password"
                autoComplete="new-password"
                value={repeat}
                onChange={(e) => setRepeat(e.target.value)}
              />
            </label>
            {error && (
              <p className="msg msg-error" role="alert">
                {error}
              </p>
            )}
            <button className="btn btn-primary" type="submit" disabled={busy}>
              {busy ? 'Saving…' : signup ? 'Create account' : 'Save new password'}
            </button>
          </>
        )}
      </form>
    </main>
  )
}
