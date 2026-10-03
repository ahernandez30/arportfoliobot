import { useMemo, useState, type FormEvent } from 'react'
import { api, type Me, type Theme } from '../api'
import { useAuth, useMe } from '../auth'
import { emailProblem } from '../forms'
import { Field, Segmented, StatusLine } from './common'
import { useAction, useSaveSettings } from './hooks'

function timeZones(current: string): string[] {
  let zones: string[] = []
  try {
    zones = Intl.supportedValuesOf('timeZone')
  } catch {
    zones = ['America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'UTC']
  }
  return zones.includes(current) ? zones : [current, ...zones]
}

export default function ProfileSection() {
  const me = useMe()
  const { setMe } = useAuth()
  const save = useSaveSettings()

  const [name, setName] = useState(me.display_name)
  const nameAction = useAction()
  const [email, setEmail] = useState(me.email)
  const [emailPassword, setEmailPassword] = useState('')
  const emailAction = useAction()
  const displayAction = useAction()
  const zones = useMemo(() => timeZones(me.settings.display.timezone), [me.settings.display.timezone])

  function saveName(e: FormEvent) {
    e.preventDefault()
    if (!name.trim()) return nameAction.setStatus({ kind: 'error', text: 'Enter your name.' })
    void nameAction.run(async () => setMe(await api<Me>('PUT', '/api/me/profile', { display_name: name.trim() })), 'Name saved.')
  }

  function saveEmail(e: FormEvent) {
    e.preventDefault()
    const problem = emailProblem(email) ?? (emailPassword ? null : 'Enter your current password to change your email.')
    if (problem) return emailAction.setStatus({ kind: 'error', text: problem })
    void emailAction
      .run(async () => setMe(await api<Me>('PUT', '/api/me/email', { email: email.trim(), password: emailPassword })), 'Email saved. Use it the next time you sign in.')
      .then(() => setEmailPassword(''))
  }

  return (
    <>
      <div className="panel">
        <h2>Display</h2>
        <div className="grid-2">
          <div className="field">
            <span>Theme</span>
            <Segmented<Theme>
              label="Theme"
              value={me.settings.display.theme}
              options={[
                { value: 'dark', label: 'Dark' },
                { value: 'light', label: 'Light' },
              ]}
              onChange={(theme) => void displayAction.run(() => save({ display: { theme } }))}
            />
          </div>
          <Field label="Time zone" hint="Times on the site are shown in this zone. Market hours always follow New York.">
            <select
              className="select"
              value={me.settings.display.timezone}
              onChange={(e) => void displayAction.run(() => save({ display: { timezone: e.target.value } }), 'Time zone saved.')}
            >
              {zones.map((z) => (
                <option key={z} value={z}>
                  {z.replace(/_/g, ' ')}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <StatusLine status={displayAction.status} />
      </div>

      <form className="panel form form-narrow" onSubmit={saveName}>
        <h2>Name</h2>
        <Field label="Your name">
          <input className="input" maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <StatusLine status={nameAction.status} />
        <div className="actions">
          <button className="btn btn-primary" disabled={nameAction.busy || name === me.display_name}>
            Save name
          </button>
        </div>
      </form>

      <form className="panel form form-narrow" onSubmit={saveEmail}>
        <h2>Email</h2>
        <p className="muted">You sign in with this email.</p>
        <Field label="Email">
          <input className="input" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Current password">
          <input
            className="input"
            type="password"
            autoComplete="current-password"
            value={emailPassword}
            onChange={(e) => setEmailPassword(e.target.value)}
          />
        </Field>
        <StatusLine status={emailAction.status} />
        <div className="actions">
          <button className="btn btn-primary" disabled={emailAction.busy || email.trim().toLowerCase() === me.email}>
            Save email
          </button>
        </div>
      </form>
    </>
  )
}
