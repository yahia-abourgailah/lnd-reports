/**
 * The application shell: sign-in, chrome, routing.
 *
 * Everything common to every screen lives here rather than in the screens —
 * the freshness badge and the filter bar in particular. A per-screen badge is
 * one the next screen forgets, and the screen somebody builds in a hurry is
 * exactly the one that would then show stale numbers with nothing saying so.
 *
 * Authentication gates the whole shell rather than each route. There is no
 * public view of this data, so a signed-out user has one thing to do.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'

import { getAuthStatus, getKpis, getMe, logout, Unauthorized } from './api'
import { Coverage } from './components/Coverage'
import { FilterBar } from './components/FilterBar'
import { FreshnessBadge } from './components/FreshnessBadge'
import { Enrichment } from './components/Enrichment'
import { Exceptions } from './components/Exceptions'
import { Funnel } from './components/Funnel'
import { Kpis } from './components/Kpis'
import { LearnerProfile } from './components/LearnerProfile'
import { Learners } from './components/Learners'
import { ProgramList, ProgramScorecard } from './components/Programs'
import { Reports } from './components/Reports'
import { TrainerList, TrainerScorecard } from './components/Trainers'
import { useFilters } from './filters'

function SignIn({ loginUrl, mode }: { loginUrl: string; mode: string }) {
  return (
    <main className="signin">
      <p className="eyebrow">L&amp;D Analytics</p>
      <h1>Sign in</h1>
      <p className="muted">
        The platform uses your normal company account. There are no local passwords.
      </p>
      {mode === 'dev-bypass' && (
        <p className="warn">
          Development sign-in shortcut is enabled. This is refused outside a dev environment.
        </p>
      )}
      <a className="button" href={loginUrl}>
        Continue with company SSO
      </a>
    </main>
  )
}

function Dashboard() {
  const queryClient = useQueryClient()
  const filters = useFilters()
  // The bar is identical on every view that reads numbers, and absent from
  // the one that does not. A filter control that renders but changes nothing
  // is worse than no control: it invites somebody to narrow a screen and
  // conclude the data is missing.
  // The bar is absent from the two screens that read no figures. On
  // `/reports` it would be worse than useless: a published edition covers the
  // month it covered, and a filter bar above it would suggest otherwise.
  const path = useLocation().pathname
  const analytical = path !== '/enrichment' && path !== '/reports' && path !== '/exceptions'
  const me = useQuery({ queryKey: ['me'], queryFn: getMe })

  // The badge reads the envelope of the request the page already made, rather
  // than calling /v1/freshness itself. Two sources for one fact would let the
  // badge say "up to date" beside figures computed a minute earlier.
  const kpis = useQuery({
    queryKey: ['kpis', filters.query],
    queryFn: () => getKpis(filters.query),
  })

  return (
    <div className="shell">
      <header className="chrome">
        <div className="chrome-row">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true" />
            <span className="brand-name">L&amp;D Analytics</span>
          </div>

          <nav className="tabs">
            <NavLink to="/" end>
              Overview
            </NavLink>
            <NavLink to="/coverage">Coverage</NavLink>
            <NavLink to="/funnel">Funnel</NavLink>
            <NavLink to="/programs">Programmes</NavLink>
            <NavLink to="/trainers">Trainers</NavLink>
            <NavLink to="/learners">Learners</NavLink>
            <NavLink to="/reports">Reports</NavLink>
            <NavLink to="/exceptions">Exceptions</NavLink>
            <NavLink to="/enrichment">Enrichment</NavLink>
          </nav>

          <div className="chrome-right">
            <FreshnessBadge freshness={kpis.data?.freshness} />
            <button
              type="button"
              className="linkish"
              onClick={async () => {
                await logout()
                await queryClient.invalidateQueries()
              }}
            >
              Sign out{me.data ? ` (${me.data.name})` : ''}
            </button>
          </div>
        </div>

        {analytical && <FilterBar filters={filters} />}
      </header>

      <main className="content">
        <Routes>
          <Route path="/" element={<Kpis filters={filters} />} />
          <Route path="/coverage" element={<Coverage filters={filters} />} />
          <Route path="/funnel" element={<Funnel filters={filters} />} />
          <Route path="/programs" element={<ProgramList filters={filters} />} />
          <Route path="/programs/:id" element={<ProgramScorecard filters={filters} />} />
          <Route path="/trainers" element={<TrainerList filters={filters} />} />
          <Route path="/trainers/:key" element={<TrainerScorecard filters={filters} />} />
          <Route path="/learners" element={<Learners filters={filters} />} />
          <Route path="/learners/:key" element={<LearnerProfile filters={filters} />} />
          <Route path="/reports" element={<Reports />} />
          <Route path="/exceptions" element={<Exceptions />} />
          <Route path="/enrichment" element={<Enrichment />} />
          <Route
            path="*"
            element={<p className="muted">That screen does not exist yet.</p>}
          />
        </Routes>
      </main>
    </div>
  )
}

export default function App() {
  const status = useQuery({
    queryKey: ['auth-status'],
    queryFn: getAuthStatus,
    retry: (count, error) => !(error instanceof Unauthorized) && count < 2,
  })

  const authError = new URLSearchParams(window.location.search).get('auth_error')

  if (status.isPending) return <main className="signin"><p className="muted">Checking session…</p></main>
  if (status.isError) return <main className="signin"><p className="warn">The API is not reachable.</p></main>
  if (!status.data) return null

  if (!status.data.authenticated) {
    return (
      <>
        {authError && (
          <div className="banner bad">
            Sign-in did not complete. The reason is recorded against this request in the API log;
            nothing further is disclosed here on purpose.
          </div>
        )}
        <SignIn loginUrl={status.data.login_url} mode={status.data.mode} />
      </>
    )
  }

  return <Dashboard />
}
