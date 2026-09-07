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

// ---------------------------------------------------------------- the dashboard

/** One figure, with everything needed to read it honestly.
 *
 * `formatted` comes from the server rather than being rendered here. NPS is an
 * index and shows a sign, percentages show one decimal, hours show a thousands
 * separator — and the export, the API and this screen must agree on all of it.
 * A second formatter in TypeScript is a second definition of what the number
 * looks like, which is how a dashboard and a PDF come to disagree.
 */
export interface Metric {
  key: string
  title: string
  definition: string
  population: string
  excludes: string[]
  provenance: 'unchanged' | 'restated' | 'renamed' | 'corrected' | 'new'
  note: string | null
  unit: 'count' | 'hours' | 'percent' | 'nps' | 'months'
  value: string | null
  formatted: string
  numerator: string | null
  denominator: string | null
  sample_size: number
  is_estimated: boolean
}

export interface EntityFreshness {
  source: string
  entity: string
  status: 'ok' | 'stale' | 'never_synced'
  last_success_at: string | null
  lag_seconds: number | null
  in_flight: boolean
}

export interface SourceFreshness {
  source: string
  status: 'ok' | 'stale' | 'never_synced'
  lag_seconds: number | null
  entities: EntityFreshness[]
}

/** Note there is no platform-level `lag_seconds`. The lag is per source and
 *  per entity, because "how far behind is the platform" has no single answer
 *  when one source is current and another has never run — which is exactly the
 *  state today. The badge derives the worst one rather than inventing a field.
 */
export interface Freshness {
  status: 'ok' | 'stale' | 'never_synced'
  generated_at: string
  stale_after_seconds: number
  sources: SourceFreshness[]
}

interface Envelope {
  freshness: Freshness
  filters_applied: string
  dimensions_filtered: string[]
  excluded_count: number
  cached: boolean
}

export interface KpisResponse extends Envelope {
  metrics: Metric[]
}

export interface Slice {
  key: string
  label: string
  metric: Metric
}

export interface BreakdownResponse extends Envelope {
  metric_key: string
  dimension: string
  overall: Metric
  slices: Slice[]
  omitted: number
}

export interface TrendResponse extends Envelope {
  metric_key: string
  overall: Metric
  points: Slice[]
}

export interface DimensionValue {
  value: string
  label: string
  count: number
}

export interface DimensionOptions {
  dimension: string
  label: string
  counts: string
  values: DimensionValue[]
}

export const getKpis = (query: string) => api<KpisResponse>(`/kpis${query}`)
export const getDimensions = () =>
  api<{ dimensions: DimensionOptions[] }>('/kpis/dimensions')
export const getBreakdown = (key: string, by: string, query: string) =>
  api<BreakdownResponse>(`/kpis/${key}/breakdown${query ? `${query}&` : '?'}by=${by}`)
export const getTrend = (key: string, query: string) =>
  api<TrendResponse>(`/kpis/${key}/trend${query}`)

export interface DrillResponse {
  metric_key: string
  metric: Metric
  grain: string
  columns: string[]
  rows: Record<string, unknown>[]
  total: number
  returned: number
  truncated: boolean
  filters_applied: string
}

export interface OverlayEntry {
  id: number
  kind: string
  key: Record<string, unknown>
  values: Record<string, unknown>
  authored_by: string
  authored_at: string
  superseded_at: string | null
  is_live: boolean
  note: string | null
}

export const getDrill = (key: string, query: string, limit = 500) =>
  api<DrillResponse>(`/drill/${key}${query ? `${query}&` : '?'}limit=${limit}`)

export const getOverlay = (kind: string) =>
  api<{ kind: string; entries: OverlayEntry[] }>(`/enrichment/${kind}`)

export const getOverlayHistory = (kind: string, key: Record<string, unknown>) =>
  api<{ kind: string; entries: OverlayEntry[] }>(`/enrichment/${kind}/history`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key }),
  })

export const putOverlay = (
  kind: string,
  key: Record<string, unknown>,
  values: Record<string, unknown>,
  note: string | null,
) =>
  api<OverlayEntry>(`/enrichment/${kind}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, values, note }),
  })

export const retireOverlay = (kind: string, key: Record<string, unknown>, note: string | null) =>
  api<OverlayEntry | null>(`/enrichment/${kind}/retire`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key, note }),
  })
