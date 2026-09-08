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

export interface Envelope {
  freshness: Freshness
  filters_applied: string
  dimensions_filtered: string[]
  /** Rows a data-quality rule keeps out of the figures. */
  excluded_count: number
  /** Rows a rule flagged and still counted — never presented as a loss. */
  flagged_count: number
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

// ------------------------------------------------- scorecards, coverage, funnel

export interface ProgramSummary {
  crm_program_id: number
  title: string
  type: string | null
  target: string | null
  capacity: number | null
  start_date: string | null
  end_date: string | null
}

export interface ProgramHeader extends ProgramSummary {
  status: string | null
  customised_department_name: string | null
  trainer_names: string[]
}

export interface SessionRow {
  crm_session_id: number
  session_date: string
  duration_hours: string | null
  duration_derivable: boolean
  trainer: string | null
  location: string | null
  attendees: number
}

/** A free-text answer, whole and without the writer's name. */
export interface Comment {
  program_title: string
  responded_date: string | null
  recommend_score: number | null
  nps_band: string | null
  text: string
}

export interface ProgramScorecard extends Envelope {
  program: ProgramHeader
  figures: Metric[]
  sessions: SessionRow[]
  comments: Comment[]
  filters_applied_scorecard: string
}

export interface TrainerSummary {
  trainer_key: number
  canonical_name: string
  /** Not a person. `L&D Team` is what a session names when nobody recorded who
   *  delivered it — marked, never ranked silently among named people. */
  is_placeholder: boolean
  /** An outside vendor rather than a colleague. */
  is_external: boolean
  sessions: number
  programs: number
  attendances: number
}

export interface Contribution {
  crm_program_id: number
  title: string
  metric: Metric
}

export interface TrainerScorecard extends Envelope {
  trainer: Omit<TrainerSummary, 'sessions' | 'programs' | 'attendances'>
  delivery: Metric[]
  programme_level: Metric[]
  nps_by_program: Contribution[]
  program_ids: number[]
  filters_applied_scorecard: string
}

export interface CoverageTail {
  dimension: string
  values_total: number
  values_shown: number
  values_omitted: number
  employees_omitted: number
}

export interface CoverageSlice {
  key: string
  label: string
  participation: Metric
  untrained: Metric
}

export interface CoverageResponse extends Envelope {
  dimension: string
  overall_participation: Metric
  overall_untrained: Metric
  months_since_last_training: Metric
  slices: CoverageSlice[]
  tail: CoverageTail
}

export interface UntrainedResponse extends Envelope {
  total: number
  gated: boolean
  gate_note: string
  columns: string[]
  rows: Record<string, unknown>[]
  truncated: boolean
}

export interface FunnelStep {
  stage: string
  label: string
  count: number
  drop_metric: Metric | null
  drop_count: number | null
  drop_label: string | null
}

export interface FunnelResponse extends Envelope {
  steps: FunnelStep[]
  no_show_rate: Metric
  survey_response_rate: Metric
  walk_ins: number
  filters_applied_funnel: string
}

export interface StageRowsResponse extends Envelope {
  stage: string
  columns: string[]
  rows: Record<string, unknown>[]
  total: number
  truncated: boolean
}

const join = (query: string, extra: string) => (query ? `${query}&${extra}` : `?${extra}`)

export const getPrograms = (query: string) =>
  api<{ programs: ProgramSummary[] }>(`/programs${query}`)
export const getProgramScorecard = (id: number, query: string) =>
  api<ProgramScorecard>(`/programs/${id}/scorecard${query}`)
export const getTrainers = (query: string) =>
  api<{ trainers: TrainerSummary[] }>(`/trainers${query}`)
export const getTrainerScorecard = (key: number, query: string) =>
  api<TrainerScorecard>(`/trainers/${key}/scorecard${query}`)
export const getCoverage = (by: string, query: string) =>
  api<CoverageResponse>(`/coverage${join(query, `by=${by}`)}`)
export const getUntrained = (query: string) => api<UntrainedResponse>(`/coverage/untrained${query}`)
export const getFunnel = (query: string) => api<FunnelResponse>(`/funnel${query}`)
export const getFunnelStage = (stage: string, query: string) =>
  api<StageRowsResponse>(`/funnel/${stage}${query}`)

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
