import { useEffect, useState, type FormEvent } from 'react'
import { api, type KeyProvider, type KeysListing } from '../api'
import { useMe } from '../auth'
import { formatDateTime } from '../forms'
import { Field, StatusLine } from './common'
import { useAction } from './hooks'

function KeyRow({ provider, onChange }: { provider: KeyProvider; onChange: (l: KeysListing) => void }) {
  const me = useMe()
  const [editing, setEditing] = useState(false)
  const [secret, setSecret] = useState('')
  const [accountId, setAccountId] = useState(provider.saved?.account_id ?? '')
  const action = useAction()

  function save(e: FormEvent) {
    e.preventDefault()
    if (secret.trim().length < 8) return action.setStatus({ kind: 'error', text: 'Paste the whole key.' })
    void action.run(async () => {
      onChange(await api<KeysListing>('PUT', `/api/me/keys/${provider.id}`, { secret: secret.trim(), account_id: accountId.trim() }))
      setSecret('')
      setEditing(false)
    }, 'Key saved.')
  }

  function remove() {
    if (!window.confirm(`Delete your saved ${provider.name} key?`)) return
    void action.run(async () => onChange(await api<KeysListing>('DELETE', `/api/me/keys/${provider.id}`)), 'Key deleted.')
  }

  return (
    <div className="key-row">
      <div className="key-head">
        <span className="key-name">{provider.name}</span>
        {provider.saved ? (
          <span className="badge badge-on">Saved · ends in <span className="num">&nbsp;{provider.saved.last4 || '····'}</span></span>
        ) : (
          <span className="badge">Not saved</span>
        )}
      </div>
      {provider.saved && (
        <p className="muted">
          {provider.saved.account_id && <>Account {provider.saved.account_id} · </>}
          Updated {formatDateTime(provider.saved.updated_at, me.settings.display.timezone)}
        </p>
      )}
      {editing ? (
        <form className="form" onSubmit={save}>
          <div className="grid-2">
            <Field label="Key / access token" hint="Stored encrypted. It is never shown again, only its last 4 characters.">
              <input className="input num" type="password" autoComplete="off" spellCheck={false} value={secret} onChange={(e) => setSecret(e.target.value)} />
            </Field>
            <Field label="Account number (optional)" hint="Not secret. Needed later for placing orders.">
              <input className="input num" autoComplete="off" spellCheck={false} maxLength={64} value={accountId} onChange={(e) => setAccountId(e.target.value)} />
            </Field>
          </div>
          <div className="actions">
            <button className="btn btn-primary" disabled={action.busy}>
              Save key
            </button>
            <button className="btn" type="button" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="actions">
          <button className="btn btn-small" onClick={() => setEditing(true)}>
            {provider.saved ? 'Replace key' : 'Add key'}
          </button>
          {provider.saved && (
            <button className="btn btn-small btn-danger" onClick={remove} disabled={action.busy}>
              Delete
            </button>
          )}
        </div>
      )}
      <StatusLine status={action.status} />
    </div>
  )
}

export default function KeysSection() {
  const [listing, setListing] = useState<KeysListing | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api<KeysListing>('GET', '/api/me/keys')
      .then(setListing)
      .catch((e) => setError(e.message))
  }, [])

  return (
    <div className="panel">
      <h2>Keys and connections</h2>
      <p className="muted">
        Keys for your broker and market-data services. Each person uses their own keys. They are encrypted before they
        are stored and are never sent back to the browser. The site starts using them from Stage 2.
      </p>
      {error && <p className="msg msg-error">{error}</p>}
      {listing && !listing.storage_ready && (
        <p className="msg msg-warn">Secure key storage is not set up on the server yet. Keys cannot be saved until it is.</p>
      )}
      {listing?.providers.map((p) => (
        <KeyRow key={p.id} provider={p} onChange={setListing} />
      ))}
    </div>
  )
}
