import { useEffect, useState, type FormEvent } from 'react'
import { api, type Me, type SessionRow } from '../api'
import { useAuth, useMe } from '../auth'
import { describeDevice, formatDateTime, MIN_PASSWORD, newPasswordProblem } from '../forms'
import { Field, StatusLine } from './common'
import { useAction } from './hooks'

function PasswordForm() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const action = useAction()

  function submit(e: FormEvent) {
    e.preventDefault()
    const problem = (current ? null : 'Enter your current password.') ?? newPasswordProblem(next, repeat)
    if (problem) return action.setStatus({ kind: 'error', text: problem })
    void action
      .run(async () => {
        const r = await api<{ other_sessions_ended: number }>('PUT', '/api/me/password', {
          current_password: current,
          new_password: next,
        })
        action.setStatus({
          kind: 'ok',
          text:
            r.other_sessions_ended > 0
              ? `Password changed. ${r.other_sessions_ended} other device(s) were signed out.`
              : 'Password changed.',
        })
      })
      .then((ok) => {
        if (ok) {
          setCurrent('')
          setNext('')
          setRepeat('')
        }
      })
  }

  return (
    <form className="panel form form-narrow" onSubmit={submit}>
      <h2>Password</h2>
      <Field label="Current password">
        <input className="input" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
      </Field>
      <Field label="New password" hint={`At least ${MIN_PASSWORD} characters. Other devices are signed out when it changes.`}>
        <input className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
      </Field>
      <Field label="Type the new password again">
        <input className="input" type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
      </Field>
      <StatusLine status={action.status} />
      <div className="actions">
        <button className="btn btn-primary" disabled={action.busy}>
          Change password
        </button>
      </div>
    </form>
  )
}

function TwoStep() {
  const me = useMe()
  const { setMe } = useAuth()
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [setup, setSetup] = useState<{ qr: string; secret: string } | null>(null)
  const action = useAction()

  function start(e: FormEvent) {
    e.preventDefault()
    if (!password) return action.setStatus({ kind: 'error', text: 'Enter your current password.' })
    void action.run(async () => {
      setSetup(await api<{ qr: string; secret: string }>('POST', '/api/me/two-step/setup', { password }))
      setPassword('')
    })
  }

  function confirm(e: FormEvent) {
    e.preventDefault()
    void action
      .run(async () => {
        setMe(await api<Me>('POST', '/api/me/two-step/enable', { code }))
        setSetup(null)
      }, 'Two-step sign-in is on. You will be asked for a code each time you sign in.')
      .then(() => setCode(''))
  }

  function turnOff(e: FormEvent) {
    e.preventDefault()
    if (!password || !code) return action.setStatus({ kind: 'error', text: 'Enter your password and a current code.' })
    void action
      .run(async () => setMe(await api<Me>('POST', '/api/me/two-step/disable', { password, code })), 'Two-step sign-in is off.')
      .then((ok) => {
        if (ok) setPassword('')
        setCode('')
      })
  }

  return (
    <div className="panel form-narrow">
      <div className="key-head">
        <h2>Two-step sign-in</h2>
        <span className={`badge ${me.two_step ? 'badge-on' : 'badge-off'}`}>{me.two_step ? 'On' : 'Off'}</span>
      </div>
      <p className="muted">
        After your password, the site asks for a 6-digit code from an authenticator app on your phone (Google
        Authenticator, Authy, 1Password, or similar). It will be required for real trading later.
      </p>

      {!me.two_step && !setup && (
        <form className="form" onSubmit={start}>
          <Field label="Current password">
            <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <div className="actions">
            <button className="btn btn-primary" disabled={action.busy}>
              Set up two-step sign-in
            </button>
          </div>
        </form>
      )}

      {!me.two_step && setup && (
        <form className="form" onSubmit={confirm}>
          <p>1. In your authenticator app, add an account and scan this code.</p>
          <div className="qr">
            <img src={setup.qr} alt="QR code for the authenticator app" />
            <div className="field">
              <span>Cannot scan? Type this key instead:</span>
              <span className="qr-secret">{setup.secret.replace(/(.{4})/g, '$1 ').trim()}</span>
            </div>
          </div>
          <p>2. Enter the 6-digit code the app now shows.</p>
          <Field label="6-digit code">
            <input className="input code" inputMode="numeric" autoComplete="one-time-code" maxLength={7} value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
          <div className="actions">
            <button className="btn btn-primary" disabled={action.busy}>
              Turn on
            </button>
            <button className="btn" type="button" onClick={() => setSetup(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {me.two_step && (
        <form className="form" onSubmit={turnOff}>
          <p className="muted">To turn it off, enter your password and a current code.</p>
          <Field label="Current password">
            <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          <Field label="6-digit code">
            <input className="input code" inputMode="numeric" autoComplete="one-time-code" maxLength={7} value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
          <div className="actions">
            <button className="btn btn-danger" disabled={action.busy}>
              Turn off two-step sign-in
            </button>
          </div>
        </form>
      )}
      <StatusLine status={action.status} />
    </div>
  )
}

function Sessions() {
  const me = useMe()
  const { signOut } = useAuth()
  const [rows, setRows] = useState<SessionRow[] | null>(null)
  const action = useAction()

  useEffect(() => {
    api<SessionRow[]>('GET', '/api/me/sessions')
      .then(setRows)
      .catch(() => setRows([]))
  }, [])

  function endAll() {
    if (!window.confirm('Sign out on every device, including this one?')) return
    void action.run(async () => {
      await api('POST', '/api/me/sessions/end-all')
      await signOut()
    })
  }

  const tz = me.settings.display.timezone
  return (
    <div className="panel">
      <h2>Signed-in devices</h2>
      {rows && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Device</th>
                <th>Last active</th>
                <th>Signed in</th>
                <th>Address</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td>
                    {describeDevice(r.user_agent)} {r.current && <span className="badge badge-accent">This device</span>}
                  </td>
                  <td>{formatDateTime(r.last_seen_at, tz)}</td>
                  <td>{formatDateTime(r.created_at, tz)}</td>
                  <td className="num">{r.ip ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <StatusLine status={action.status} />
      <div className="actions">
        <button className="btn btn-danger" onClick={endAll} disabled={action.busy}>
          Sign out everywhere
        </button>
      </div>
    </div>
  )
}

export default function SecuritySection() {
  return (
    <>
      <TwoStep />
      <PasswordForm />
      <Sessions />
    </>
  )
}
