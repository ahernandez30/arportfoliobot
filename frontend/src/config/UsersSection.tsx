import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, type AdminUser, type InviteRow } from '../api'
import { useMe } from '../auth'
import { emailProblem, formatDateTime } from '../forms'
import { Field, Segmented, StatusLine } from './common'
import { useAction } from './hooks'

function LinkResult({ link, label, onClose }: { link: string; label: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }
  return (
    <div className="msg msg-ok link-result">
      <p>✓ {label} Send it yourself by text or email. It is shown only now and works once.</p>
      <div className="link-box">
        <input className="input" readOnly value={link} onFocus={(e) => e.target.select()} />
        <button className="btn" type="button" onClick={copy}>
          {copied ? 'Copied' : 'Copy'}
        </button>
        <button className="btn" type="button" onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  )
}

const STATUS_LABEL: Record<InviteRow['status'], string> = {
  open: 'Waiting',
  used: 'Used',
  revoked: 'Cancelled',
  expired: 'Expired',
}

export default function UsersSection() {
  const me = useMe()
  const tz = me.settings.display.timezone
  const [users, setUsers] = useState<AdminUser[]>([])
  const [invites, setInvites] = useState<InviteRow[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [link, setLink] = useState<{ url: string; label: string } | null>(null)
  const list = useAction()
  const form = useAction()

  const [email, setEmail] = useState('')
  const [note, setNote] = useState('')
  const [role, setRole] = useState<'user' | 'admin'>('user')
  const [hours, setHours] = useState('72')

  const reload = useCallback(async () => {
    const [u, i] = await Promise.all([
      api<AdminUser[]>('GET', '/api/admin/users'),
      api<InviteRow[]>('GET', '/api/admin/invites'),
    ])
    setUsers(u)
    setInvites(i)
  }, [])

  useEffect(() => {
    Promise.all([api<AdminUser[]>('GET', '/api/admin/users'), api<InviteRow[]>('GET', '/api/admin/invites')])
      .then(([u, i]) => {
        setUsers(u)
        setInvites(i)
      })
      .catch((e) => setLoadError(e.message))
  }, [])

  function createInvite(e: FormEvent) {
    e.preventDefault()
    if (email.trim() && emailProblem(email)) return form.setStatus({ kind: 'error', text: 'That email address does not look right.' })
    if (role === 'admin' && !window.confirm('Admins can invite people and manage every account. Create an admin invite?')) return
    void form.run(async () => {
      const r = await api<{ link: string }>('POST', '/api/admin/invites', {
        email: email.trim(),
        role,
        note: note.trim(),
        valid_hours: Number(hours),
      })
      setLink({ url: r.link, label: 'Invite link created.' })
      setEmail('')
      setNote('')
      setRole('user')
      await reload()
    })
  }

  function change(u: AdminUser, body: Partial<Pick<AdminUser, 'role' | 'is_active'>>, question: string) {
    if (!window.confirm(question)) return
    void list.run(async () => {
      await api('PATCH', `/api/admin/users/${u.id}`, body)
      await reload()
    })
  }

  function resetPassword(u: AdminUser) {
    if (!window.confirm(`Create a one-time link for ${u.display_name} to choose a new password?`)) return
    void list.run(async () => {
      const r = await api<{ link: string }>('POST', `/api/admin/users/${u.id}/reset-password`)
      setLink({ url: r.link, label: `Password reset link for ${u.email} created (works for 24 hours).` })
      await reload()
    })
  }

  function resetTwoStep(u: AdminUser) {
    if (!window.confirm(`Turn off two-step sign-in for ${u.display_name}? Use this if they lost their phone.`)) return
    void list.run(async () => {
      await api('POST', `/api/admin/users/${u.id}/reset-two-step`)
      await reload()
    }, `Two-step sign-in turned off for ${u.email}.`)
  }

  function revoke(i: InviteRow) {
    if (!window.confirm('Cancel this link? It will stop working.')) return
    void list.run(async () => {
      await api('DELETE', `/api/admin/invites/${i.id}`)
      await reload()
    })
  }

  return (
    <>
      {link && <LinkResult link={link.url} label={link.label} onClose={() => setLink(null)} />}

      <div className="panel">
        <h2>Users</h2>
        {loadError && <p className="msg msg-error">{loadError}</p>}
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Role</th>
                <th>Status</th>
                <th>Two-step</th>
                <th>Last active</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const self = u.id === me.id
                return (
                  <tr key={u.id}>
                    <td>{u.display_name}</td>
                    <td>{u.email}</td>
                    <td>
                      <span className={`badge ${u.role === 'admin' ? 'badge-accent' : ''}`}>{u.role === 'admin' ? 'Admin' : 'User'}</span>
                    </td>
                    <td>
                      <span className={`badge ${u.is_active ? 'badge-on' : 'badge-off'}`}>{u.is_active ? 'Active' : 'Disabled'}</span>
                    </td>
                    <td>{u.two_step ? 'On' : 'Off'}</td>
                    <td>{formatDateTime(u.last_seen_at, tz)}</td>
                    <td>
                      {self ? (
                        <span className="muted">You</span>
                      ) : (
                        <div className="actions">
                          <button
                            className="btn btn-small"
                            disabled={list.busy}
                            onClick={() =>
                              change(
                                u,
                                { role: u.role === 'admin' ? 'user' : 'admin' },
                                u.role === 'admin' ? `Make ${u.display_name} a regular user?` : `Make ${u.display_name} an admin?`,
                              )
                            }
                          >
                            {u.role === 'admin' ? 'Make user' : 'Make admin'}
                          </button>
                          <button
                            className={`btn btn-small ${u.is_active ? 'btn-danger' : ''}`}
                            disabled={list.busy}
                            onClick={() =>
                              change(
                                u,
                                { is_active: !u.is_active },
                                u.is_active
                                  ? `Disable ${u.display_name}? They are signed out at once and cannot sign in.`
                                  : `Enable ${u.display_name} again?`,
                              )
                            }
                          >
                            {u.is_active ? 'Disable' : 'Enable'}
                          </button>
                          {u.is_active && (
                            <button className="btn btn-small" disabled={list.busy} onClick={() => resetPassword(u)}>
                              Reset password
                            </button>
                          )}
                          {u.two_step && (
                            <button className="btn btn-small" disabled={list.busy} onClick={() => resetTwoStep(u)}>
                              Reset two-step
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        <StatusLine status={list.status} />
      </div>

      <form className="panel form" onSubmit={createInvite}>
        <h2>Invite someone</h2>
        <p className="muted">Creates a sign-up link that works once. The site does not send email; you pass the link on.</p>
        <div className="grid-2">
          <Field label="Their email (optional)" hint="If set, the account must use this email.">
            <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Field label="Note (optional)" hint="Only you see this.">
            <input className="input" maxLength={120} value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <Field label="Link works for">
            <select className="select" value={hours} onChange={(e) => setHours(e.target.value)}>
              <option value="24">1 day</option>
              <option value="72">3 days</option>
              <option value="168">7 days</option>
            </select>
          </Field>
          <div className="field">
            <span>Role</span>
            <Segmented
              label="Role"
              value={role}
              options={[
                { value: 'user', label: 'User' },
                { value: 'admin', label: 'Admin' },
              ]}
              onChange={setRole}
            />
          </div>
        </div>
        <StatusLine status={form.status} />
        <div className="actions">
          <button className="btn btn-primary" disabled={form.busy}>
            Create invite link
          </button>
        </div>
      </form>

      <div className="panel">
        <h2>Recent links</h2>
        {invites.length === 0 ? (
          <p className="muted">No links yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Email</th>
                  <th>Note</th>
                  <th>Status</th>
                  <th>Expires</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {invites.map((i) => (
                  <tr key={i.id}>
                    <td>{i.kind === 'reset' ? 'Password reset' : i.role === 'admin' ? 'Invite (admin)' : 'Invite'}</td>
                    <td>{i.email ?? 'Any'}</td>
                    <td>{i.note || '—'}</td>
                    <td>
                      <span className={`badge ${i.status === 'open' ? 'badge-accent' : i.status === 'used' ? 'badge-on' : ''}`}>
                        {STATUS_LABEL[i.status]}
                      </span>
                    </td>
                    <td>{formatDateTime(i.expires_at, tz)}</td>
                    <td>
                      {i.status === 'open' && (
                        <button className="btn btn-small btn-danger" disabled={list.busy} onClick={() => revoke(i)}>
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
