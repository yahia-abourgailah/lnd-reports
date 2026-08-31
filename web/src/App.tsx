/**
 * Week 1 shell.
 *
 * Deliberately not a dashboard. Its whole job is to prove the chain end to end
 * — browser → nginx → FastAPI → session cookie → Postgres and Redis — so that
 * week 5 starts on a route that is already authenticated rather than on one
 * that still needs to be.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getAuthStatus, getHealth, getMe, logout, Unauthorized } from './api'

function StatusDot({ status }: { status: string }) {
  const colour =
    status === 'ok' ? 'var(--ok)' : status === 'degraded' ? 'var(--warn)' : 'var(--bad)'
  return <span className="dot" style={{ background: colour }} aria-hidden="true" />
}

function SignIn({ loginUrl, mode }: { loginUrl: string; mode: string }) {
  return (
    <div className="card">
      <h2>Sign in</h2>
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
    </div>
  )
}

function SignedIn() {
  const queryClient = useQueryClient()
  const me = useQuery({ queryKey: ['me'], queryFn: getMe })
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth, refetchInterval: 30_000 })

  return (
    <>
      <div className="card">
        <h2>Signed in</h2>
        {me.data && (
          <dl className="facts">
            <div>
              <dt>Name</dt>
              <dd>{me.data.name}</dd>
            </div>
            <div>
              <dt>Email</dt>
              <dd>{me.data.email}</dd>
            </div>
            <div>
              <dt>Subject</dt>
              <dd className="mono">{me.data.subject}</dd>
            </div>
          </dl>
        )}
        <button
          className="button secondary"
          onClick={async () => {
            await logout()
            await queryClient.invalidateQueries()
          }}
        >
          Sign out
        </button>
      </div>

      <div className="card">
        <h2>Platform health</h2>
        {health.isError && <p className="warn">Health endpoint unreachable.</p>}
        {health.data && (
          <>
            <p className="muted">
              version {health.data.version} · {health.data.environment}
            </p>
            <ul className="components">
              {Object.entries(health.data.components).map(([name, component]) => (
                <li key={name}>
                  <StatusDot status={component.status} />
                  <span>{name}</span>
                  <span className="muted mono">
                    {component.latency_ms !== null
                      ? `${component.latency_ms} ms`
                      : (component.detail ?? '—')}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </>
  )
}

export default function App() {
  const status = useQuery({
    queryKey: ['auth-status'],
    queryFn: getAuthStatus,
    retry: (count, error) => !(error instanceof Unauthorized) && count < 2,
  })

  const authError = new URLSearchParams(window.location.search).get('auth_error')

  return (
    <main>
      <header>
        <p className="eyebrow">L&amp;D Analytics Platform</p>
        <h1>Week 1 — foundation</h1>
      </header>

      {authError && (
        <div className="card error">
          <h2>Sign-in did not complete</h2>
          <p className="mono">{authError}</p>
          <p className="muted">
            The reason is recorded in the API log against this request. Nothing further is
            disclosed here on purpose.
          </p>
        </div>
      )}

      {status.isPending && <p className="muted">Checking session…</p>}
      {status.isError && <p className="warn">The API is not reachable.</p>}
      {status.data &&
        (status.data.authenticated ? (
          <SignedIn />
        ) : (
          <SignIn loginUrl={status.data.login_url} mode={status.data.mode} />
        ))}
    </main>
  )
}
