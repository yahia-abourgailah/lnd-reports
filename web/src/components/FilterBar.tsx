/**
 * The global filter bar. One bar, identical on every view.
 *
 * It reads its options from `/v1/kpis/dimensions` rather than holding a list.
 * Sectors and departments come from the CRM and are whatever the CRM currently
 * says; a hardcoded list goes stale silently, and the first symptom is a new
 * department that cannot be selected.
 *
 * Only values that exist are offered, with their row counts. Filtering to a
 * department nobody is in returns an empty dashboard that looks like a bug, and
 * "Sales (312)" tells you the shape of the answer before you ask for it.
 *
 * All state lives in the URL — see `filters.ts`.
 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { getDimensions } from '../api'
import type { Filters } from '../filters'
import { SavedViews } from './SavedViews'

function DimensionMenu({
  label,
  dimension,
  values,
  counts,
  filters,
}: {
  label: string
  dimension: string
  values: { value: string; label: string; count: number }[]
  counts: string
  filters: Filters
}) {
  const [open, setOpen] = useState(false)
  const chosen = filters.selected[dimension] ?? []

  return (
    <div className="filter">
      <button
        type="button"
        className={`filter-button${chosen.length ? ' filter-active' : ''}`}
        onClick={() => setOpen((was) => !was)}
        aria-expanded={open}
      >
        {label}
        {chosen.length > 0 && <span className="filter-count">{chosen.length}</span>}
      </button>

      {open && (
        <div className="filter-menu" role="group" aria-label={label}>
          {values.length === 0 && <p className="filter-empty">No values yet.</p>}
          {values.map((option) => (
            <label key={option.value} className="filter-option">
              <input
                type="checkbox"
                checked={chosen.includes(option.value)}
                onChange={() => filters.toggle(dimension, option.value)}
              />
              <span className="filter-label">{option.label}</span>
              {/* Programs and trainers are individually named, so a count of
                  one beside each would be noise rather than information. */}
              {counts !== 'programs' && counts !== 'trainers' && (
                <span className="filter-n">{option.count.toLocaleString()}</span>
              )}
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

export function FilterBar({ filters }: { filters: Filters }) {
  const dimensions = useQuery({ queryKey: ['dimensions'], queryFn: getDimensions })

  return (
    <div className="filterbar">
      <div className="filter-dates">
        <label>
          <span className="sr-only">From</span>
          <input
            type="date"
            value={filters.dateFrom ?? ''}
            onChange={(event) => filters.setDates(event.target.value || null, filters.dateTo)}
          />
        </label>
        <span className="filter-to" aria-hidden="true">to</span>
        <label>
          <span className="sr-only">To</span>
          <input
            type="date"
            value={filters.dateTo ?? ''}
            onChange={(event) => filters.setDates(filters.dateFrom, event.target.value || null)}
          />
        </label>
      </div>

      {dimensions.data?.dimensions.map((option) => (
        <DimensionMenu
          key={option.dimension}
          label={option.label}
          dimension={option.dimension}
          values={option.values}
          counts={option.counts}
          filters={filters}
        />
      ))}

      {filters.activeCount > 0 && (
        <button type="button" className="filter-clear" onClick={filters.clear}>
          Clear {filters.activeCount}
        </button>
      )}

      {/* In the bar rather than the chrome: what a saved view stores is the
          state these controls are in, and putting the name for it anywhere
          else would separate the two. */}
      <SavedViews filters={filters} />
    </div>
  )
}
