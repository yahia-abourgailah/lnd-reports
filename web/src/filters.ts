/**
 * The global filter state, held in the URL.
 *
 * The URL is the single source of truth, not React state that happens to be
 * mirrored into it. Three things follow, and the third is why it is worth the
 * small awkwardness of reading everything through `useSearchParams`:
 *
 *   A filtered view is a link. "Participation for MarQ, Feb to Aug" is a URL
 *   somebody can paste into a message, and it will still mean that tomorrow.
 *
 *   Back works. Narrowing a dashboard and pressing back is browsing, not an
 *   accident, and a filter bar that ignores history breaks the one control
 *   every user already knows.
 *
 *   Every view shares one bar. The parameters the API accepts are the
 *   parameters the URL holds, so moving between screens carries the filters
 *   without any screen having to hand them over.
 */

import { useCallback, useEffect, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'

/** The filter parameters, named exactly as the API reads them. */
export const FILTER_KEYS = [
  'sector',
  'department',
  'company',
  'job_level',
  'program',
  'program_type',
  'program_target',
  'trainer',
] as const

export type FilterKey = (typeof FILTER_KEYS)[number]

/** Parameters that are not filters and must survive filter changes. */
const RESERVED = new Set(['by'])

function isoDate(day: Date): string {
  // Built from the local parts, never `toISOString`. That is UTC, and east of
  // Greenwich it returns yesterday for most of the evening — a dashboard that
  // opens on the wrong day after 9pm and is right again in the morning.
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`
}

/** The window a view opens on when the URL names none: this year, to today.
 *
 * WHY THERE IS A DEFAULT AT ALL
 *
 * Opening on everything meant opening on July 2025 to September 2026 — fifteen
 * months, seven of which nobody was asking about. "How are we doing" means this
 * year unless somebody says otherwise, and a reader who has to narrow the bar
 * before the first number means anything will read the first number anyway.
 *
 * WHY IT ENDS TODAY AND NOT AT NEW YEAR
 *
 * Four sessions are already scheduled ahead, and 31 December would count them
 * as delivered and measure recency from a date three months away. This year so
 * far is a window where everything in it has happened.
 */
export function defaultWindow(now: Date = new Date()): { from: string; to: string } {
  return { from: `${now.getFullYear()}-01-01`, to: isoDate(now) }
}

export interface Filters {
  /** The query string to append to an API path, `?…` or empty. */
  query: string
  /** Selected values per dimension. Empty means "not filtered". */
  selected: Record<string, string[]>
  dateFrom: string | null
  dateTo: string | null
  /** How many dimensions are narrowed. Drives the "clear" affordance. */
  activeCount: number
  toggle: (key: string, value: string) => void
  setDates: (from: string | null, to: string | null) => void
  clear: () => void
}

export function useFilters(): Filters {
  const [params, setParams] = useSearchParams()

  const selected = useMemo(() => {
    const out: Record<string, string[]> = {}
    for (const key of FILTER_KEYS) {
      const values = params.getAll(key)
      if (values.length) out[key] = values
    }
    return out
  }, [params])

  const dateFrom = params.get('date_from')
  const dateTo = params.get('date_to')

  // Written into the URL rather than applied behind it. A view is a link here,
  // and a link that carries no dates would mean this year whenever it happened
  // to be opened — the same address answering a different question in January.
  // Replace, not push, so the first press of back leaves the page rather than
  // returning to a URL that redirects again.
  useEffect(() => {
    if (dateFrom !== null || dateTo !== null) return
    const { from, to } = defaultWindow()
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous)
        next.set('date_from', from)
        next.set('date_to', to)
        return next
      },
      { replace: true },
    )
  }, [dateFrom, dateTo, setParams])

  const query = useMemo(() => {
    // Rebuilt from the known keys rather than passed through wholesale, so a
    // stray parameter in the URL cannot reach the API as a filter.
    const out = new URLSearchParams()
    if (dateFrom) out.set('date_from', dateFrom)
    if (dateTo) out.set('date_to', dateTo)
    for (const [key, values] of Object.entries(selected)) {
      for (const value of values) out.append(key, value)
    }
    const text = out.toString()
    return text ? `?${text}` : ''
  }, [dateFrom, dateTo, selected])

  const toggle = useCallback(
    (key: string, value: string) => {
      setParams(
        (previous) => {
          const next = new URLSearchParams(previous)
          const existing = next.getAll(key)
          next.delete(key)
          const kept = existing.includes(value)
            ? existing.filter((v) => v !== value)
            : [...existing, value]
          for (const v of kept) next.append(key, v)
          return next
        },
        { replace: false },
      )
    },
    [setParams],
  )

  const setDates = useCallback(
    (from: string | null, to: string | null) => {
      setParams((previous) => {
        const next = new URLSearchParams(previous)
        if (from) next.set('date_from', from)
        else next.delete('date_from')
        if (to) next.set('date_to', to)
        else next.delete('date_to')
        return next
      })
    },
    [setParams],
  )

  const clear = useCallback(() => {
    setParams((previous) => {
      const next = new URLSearchParams()
      // Dates included, which the effect above then restores to the default.
      // Clearing returns the page to how it opens; it does not widen it to
      // fifteen months of history nobody asked for. To see further back, empty
      // the From field — the effect only fires when both dates are absent.
      //
      // A view's own parameters are not filters. Clearing the bar on a
      // breakdown must not also forget which dimension is being broken down.
      for (const key of RESERVED) {
        const value = previous.get(key)
        if (value) next.set(key, value)
      }
      return next
    })
  }, [setParams])

  // The default window is not something to clear. Counting it would put a
  // "Clear 1" on a bar nobody has touched, which teaches the reader that the
  // count means nothing.
  const isDefaultWindow = useMemo(() => {
    const { from, to } = defaultWindow()
    return dateFrom === from && dateTo === to
  }, [dateFrom, dateTo])

  const activeCount =
    Object.keys(selected).length + (!isDefaultWindow && (dateFrom || dateTo) ? 1 : 0)

  return { query, selected, dateFrom, dateTo, activeCount, toggle, setDates, clear }
}
