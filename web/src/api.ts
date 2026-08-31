/**
 * Thin client for /v1.
 *
 * Every request carries the session cookie, and a 401 means "not signed in"
 * rather than "error" — the shell renders the sign-in prompt instead of an
 * error state.
 */

export const API_BASE = '/v1'

export class Unauthorized extends Error {
  constructor() {
    super('Not authenticated')
    this.name = 'Unauthorized'
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    headers: { Accept: 'application/json', ...(init.headers ?? {}) },
    ...init,
  })

  if (response.status === 401) throw new Unauthorized()
  if (!response.ok) {
    throw new Error(`${init.method ?? 'GET'} ${path} failed: ${response.status}`)
  }
  return (await response.json()) as T
}

export interface AuthStatus {
  authenticated: boolean
  mode: 'oidc' | 'dev-bypass'
  login_url: string
}

export interface Me {
  email: string
  name: string
  subject: string
}

export interface ComponentHealth {
  status: 'ok' | 'degraded' | 'error'
  latency_ms: number | null
  detail: string | null
}

export interface Health {
  status: 'ok' | 'degraded' | 'error'
  version: string
  environment: string
  components: Record<string, ComponentHealth>
}

export const getAuthStatus = () => api<AuthStatus>('/auth/status')
export const getMe = () => api<Me>('/auth/me')
export const getHealth = () => api<Health>('/health')
export const logout = () => api<{ status: string }>('/auth/logout', { method: 'POST' })
