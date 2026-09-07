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

import { useCallback, useMemo } from 'react'
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
      // A view's own parameters are not filters. Clearing the bar on a
      // breakdown must not also forget which dimension is being broken down.
      for (const key of RESERVED) {
        const value = previous.get(key)
        if (value) next.set(key, value)
      }
      return next
    })
  }, [setParams])

  const activeCount =
    Object.keys(selected).length + (dateFrom || dateTo ? 1 : 0)

  return { query, selected, dateFrom, dateTo, activeCount, toggle, setDates, clear }
}
