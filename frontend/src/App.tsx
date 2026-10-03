import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router'
import { AuthProvider, useAuth } from './auth'
import ConfigPage from './config/ConfigPage'
import InvitePage from './InvitePage'
import LoginPage from './LoginPage'
import Placeholder from './Placeholder'
import Shell from './Shell'

function SignedIn() {
  const { me, loading } = useAuth()
  const location = useLocation()
  if (loading) return <div className="boot" aria-busy="true" />
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return <Shell />
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/invite/:token" element={<InvitePage />} />
          <Route element={<SignedIn />}>
            <Route index element={<Placeholder title="Dashboard" stage={2} />} />
            <Route path="capital" element={<Placeholder title="Capital Tracking" stage={3} />} />
            <Route path="accounts" element={<Placeholder title="Account Manager" stage={3} />} />
            <Route path="live" element={<Placeholder title="Live Trader" stage={4} />} />
            <Route path="charts" element={<Placeholder title="Charts" stage={2} />} />
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
