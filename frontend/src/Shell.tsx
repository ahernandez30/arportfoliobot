import { useState } from 'react'
import { NavLink, Outlet } from 'react-router'
import { useMe, useAuth } from './auth'
import './Shell.css'

const TABS = [
  { to: '/', label: 'Dashboard', icon: 'M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z' },
  { to: '/capital', label: 'Capital Tracking', icon: 'M4 19V9M10 19V5M16 19v-7M22 19H2' },
  { to: '/accounts', label: 'Account Manager', icon: 'M4 5h16M4 10h16M4 15h10M4 20h7' },
  { to: '/live', label: 'Live Trader', icon: 'M13 2 4 14h7l-1 8 9-12h-7z' },
  { to: '/charts', label: 'Charts', icon: 'M3 3h8v8H3zM13 3h8v8h-8zM3 13h8v8H3zM13 13h8v8h-8z' },
  { to: '/master', label: 'Master Chart', icon: 'M3 17l5-6 4 3 6-8 3 3M3 21h18' },
  { to: '/backtest', label: 'Backtest', icon: 'M12 8v5l3 2M21 12a9 9 0 1 1-3-6.7M21 3v5h-5' },
  { to: '/config', label: 'Config', icon: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM4 12h2M18 12h2M12 4v2M12 18v2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4' },
]

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  )
}

export default function Shell() {
  const me = useMe()
  const { signOut } = useAuth()
  const [open, setOpen] = useState(false)

  return (
    <div className={`shell ${open ? 'menu-open' : ''}`}>
      <header className="topbar">
        <button className="menu-btn" aria-label={open ? 'Close menu' : 'Open menu'} aria-expanded={open}
          onClick={() => setOpen((o) => !o)}>
          <Icon d={open ? 'M6 6l12 12M18 6 6 18' : 'M4 7h16M4 12h16M4 17h16'} />
        </button>
        <img src="/favicon.svg" alt="" width={28} height={28} />
        <span className="topbar-title">AR Portfolio Bot</span>
      </header>

      <nav className="sidemenu" aria-label="Main">
        <div className="side-brand">
          <img src="/favicon.svg" alt="" width={30} height={30} />
          <span>AR Portfolio Bot</span>
        </div>
        <ul>
          {TABS.map((t) => (
            <li key={t.to}>
              <NavLink
                to={t.to}
                end={t.to === '/'}
                className={({ isActive }) => (isActive ? 'active' : '')}
                onClick={() => setOpen(false)}
              >
                <Icon d={t.icon} />
                <span>{t.label}</span>
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="side-user">
          <div className="side-user-name">
            <span>{me.display_name}</span>
            {me.role === 'admin' && <span className="badge badge-accent">Admin</span>}
          </div>
          <span className="muted side-user-email">{me.email}</span>
          <button className="btn btn-small" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </nav>
      <div className="scrim" onClick={() => setOpen(false)} aria-hidden="true" />

      <main className="content">
        <Outlet />
      </main>
    </div>
  )
}
