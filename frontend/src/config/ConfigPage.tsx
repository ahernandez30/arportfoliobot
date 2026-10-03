import { Navigate, NavLink, useParams } from 'react-router'
import { useMe } from '../auth'
import KeysSection from './KeysSection'
import ProfileSection from './ProfileSection'
import SecuritySection from './SecuritySection'
import { PaperSection, TradingSection, WatchlistSection } from './SettingsSections'
import UsersSection from './UsersSection'
import './Config.css'

const SECTIONS = [
  { id: 'profile', label: 'Profile' },
  { id: 'security', label: 'Security' },
  { id: 'keys', label: 'Keys & connections' },
  { id: 'paper', label: 'Paper account' },
  { id: 'trading', label: 'Trading limits' },
  { id: 'watchlist', label: 'Watchlist' },
  { id: 'users', label: 'Users', adminOnly: true },
]

export default function ConfigPage() {
  const { section = 'profile' } = useParams()
  const me = useMe()
  const visible = SECTIONS.filter((s) => !s.adminOnly || me.role === 'admin')
  if (!visible.some((s) => s.id === section)) return <Navigate to="/config/profile" replace />

  return (
    <section className="page">
      <h1 className="page-title">Config</h1>
      <nav className="config-tabs" aria-label="Config sections">
        {visible.map((s) => (
          <NavLink key={s.id} to={`/config/${s.id}`} className={({ isActive }) => (isActive ? 'active' : '')}>
            {s.label}
          </NavLink>
        ))}
      </nav>
      {section === 'profile' && <ProfileSection />}
      {section === 'security' && <SecuritySection />}
      {section === 'keys' && <KeysSection />}
      {section === 'paper' && <PaperSection />}
      {section === 'trading' && <TradingSection />}
      {section === 'watchlist' && <WatchlistSection />}
      {section === 'users' && <UsersSection />}
    </section>
  )
}
