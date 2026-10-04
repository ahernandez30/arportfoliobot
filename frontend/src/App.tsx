import { lazy } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router'
import { AuthProvider, useAuth } from './auth'
import ConfigPage from './config/ConfigPage'
import InvitePage from './InvitePage'
import LoginPage from './LoginPage'
import { FeedProvider } from './market/feed'
import Placeholder from './Placeholder'
import Shell from './Shell'

// Charts and the dashboard grid are large; load them only once signed in.
const DashboardPage = lazy(() => import('./dashboard/DashboardPage'))
const ChartsPage = lazy(() => import('./market/ChartsPage'))
const CapitalPage = lazy(() => import('./capital/CapitalPage'))
const AccountsPage = lazy(() => import('./accounts/AccountsPage'))
const LiveTraderPage = lazy(() => import('./live/LiveTraderPage'))

function SignedIn() {
  const { me, loading } = useAuth()
  const location = useLocation()
  if (loading) return <div className="boot" aria-busy="true" />
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return (
    <FeedProvider>
      <Shell />
    </FeedProvider>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/invite/:token" element={<InvitePage />} />
          <Route element={<SignedIn />}>
            <Route index element={<DashboardPage />} />
            <Route path="capital" element={<CapitalPage />} />
            <Route path="accounts" element={<AccountsPage />} />
            <Route path="live" element={<LiveTraderPage />} />
            <Route path="charts" element={<ChartsPage />} />
            <Route path="master" element={<Placeholder title="Master Chart" stage={5} />} />
            <Route path="backtest" element={<Placeholder title="Backtest" stage={7} />} />
            <Route path="config" element={<Navigate to="/config/profile" replace />} />
            <Route path="config/:section" element={<ConfigPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
