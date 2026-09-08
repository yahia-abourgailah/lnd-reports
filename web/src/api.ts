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
  // A 204 carries no body, and `response.json()` on one throws. Deleting a
  // saved view is the first route in this API that returns one.
  if (response.status === 204) return undefined as T
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
  /** The zero-training list is still gated: a named list of people who have
   *  had nothing is a document about individuals in a way the top-learners
   *  ranking is not. The count is exact either way. */
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

// ------------------------------------------------------------------- learners

export interface TopLearnerRow {
  rank: number
  employee_key: number
  employee_code: string | null
  name: string
  department: string | null
  company: string | null
  programs: number
  sessions: number
  hours: string
}

export interface TopLearnersResponse extends Envelope {
  total_learners: number
  rows: TopLearnerRow[]
  hours_max: string | null
  hours_median: string | null
  hours_min: string | null
  truncated: boolean
}

export interface LearnerProgram {
  crm_program_id: number
  title: string
  sessions: number
  hours: string
  first_attended: string | null
  last_attended: string | null
}

export interface LearnerProfileResponse extends Envelope {
  employee_key: number
  employee_code: string | null
  name: string
  department: string | null
  company: string | null
  sector: string | null
  job_level: string | null
  position: string | null
  /** False for somebody who trained and has since left. They stay in the
   *  dimension so their attendance still keys; they are not in any
   *  denominator. Worth saying on the profile rather than leaving the reader
   *  to wonder why they are absent from coverage. */
  on_current_roster: boolean
  figures: Metric[]
  programs: LearnerProgram[]
}

/** One search hit. Named the way `TopLearnerRow` names the same attributes,
 *  because the two lists sit on one screen — and because they once did not:
 *  the API sent `full_name` and `department_name` to a component reading
 *  `name` and `department`, so every match rendered as an empty row. */
export interface SearchResult {
  employee_key: number
  employee_code: string | null
  name: string | null
  department: string | null
  company: string | null
}

export const getTopLearners = (query: string, limit = 25) =>
  api<TopLearnersResponse>(`/learners/top${query ? `${query}&` : '?'}limit=${limit}`)

export const getLearner = (key: number, query: string) =>
  api<LearnerProfileResponse>(`/learners/${key}${query}`)

export const searchLearners = (q: string) =>
  api<{ query: string; results: SearchResult[] }>(`/learners/search?q=${encodeURIComponent(q)}`)

// -------------------------------------------------------------------- exports

/** Export URLs are plain links, not fetches.
 *
 * The browser downloads them directly, so the session cookie goes with the
 * request and the file never passes through JavaScript. A fetch-then-blob
 * would put a 1,450-row XLSX through memory to achieve the same thing, and
 * would lose the filename the server already sets in Content-Disposition. */
export const exportUrl = (path: string, query: string) => `${API_BASE}/exports/${path}${query}`

// ------------------------------------------------------------------ editions

/** One monthly report as it was published.
 *
 * The listing never carries the file. `figures_sha256` digests the numbers the
 * edition contains, never its bytes: every export writes its own generation
 * time into itself, so two renderings of an unchanged month would never match
 * byte for byte. Equal digests mean the figures did not move — across formats
 * too, since the workbook and the PDF of one month share it. */
export interface Edition {
  id: number
  kind: 'monthly_xlsx' | 'monthly_pdf'
  trigger: 'manual' | 'scheduled'
  /** `YYYY-MM`: the month covered, not the month generated. */
  period: string
  filename: string
  content_type: string
  filters_applied: string
  generated_at: string
  /** Null when the schedule made it. Not a service account standing in for a
   *  person — the difference is the whole value of the column. */
  generated_by: string | null
  byte_size: number
  figures_sha256: string
}

export interface EditionsResponse {
  editions: Edition[]
  total_editions: number
  total_bytes: number
  retained_per_period: number
}

export const getEditions = () => api<EditionsResponse>('/exports/editions')

// --------------------------------------------------------------- saved views

/** A named filter set. What is stored is the URL, because the URL is already
 *  this application's filter state — see `filters.ts`. */
export interface SavedView {
  id: number
  name: string
  path: string
  /** Without a leading `?`. Empty means the unfiltered view. */
  query: string
  /** The scope in words, resolved by the server when it was saved. */
  describes: string
  owner_email: string
  /** Whether the signed-in user may rename or delete this one. */
  mine: boolean
  created_at: string
  updated_at: string
}

export const getViews = () => api<{ views: SavedView[] }>('/views')

export const saveView = (name: string, path: string, query: string) =>
  api<SavedView>('/views', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, path, query }),
  })

export const renameView = (id: number, name: string) =>
  api<SavedView>(`/views/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })

export const deleteView = (id: number) =>
  api<void>(`/views/${id}`, { method: 'DELETE' })
