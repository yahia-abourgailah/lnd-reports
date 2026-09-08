/**
 * Who has been reached, and who has not.
 *
 * The headline is a gap, not an average: 6.4% at one company against 64.1% at
 * another, from the same roster and the same arithmetic. That is the first
 * thing anybody will ask about, so the slices are shown as bars against a
 * common scale rather than as a table somebody has to scan for the outlier.
 *
 * THE TAIL IS NAMED
 *
 * 129 departments against a slice limit of 60. The breakdown reports what it
 * left out rather than stopping quietly, and this screen says so in a sentence
 * with the number of people involved, plus where to go to reach them. A chart
 * whose bottom is invisible is worse than a shorter chart.
 *
 * THE NAMES ARE SCOPED
 *
 * 1,268 people have had no training. The count is always shown; the list of
 * names appears once somebody has narrowed to a department, sector, company or
 * job level. That is a judgement about how an individually-named list of people
 * who have done nothing reads in a performance conversation as against a
 * planning one — not a technical limit, and reversible in one predicate if L&D
 * decide otherwise.
 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { getCoverage, getUntrained } from '../api'
import type { Filters } from '../filters'
import { ExclusionBanner } from './ExclusionBanner'
import { ExportMenu } from './ExportMenu'
import { Figure } from './Figure'
import { RecordGrid } from './RecordGrid'

const DIMENSIONS = [
  { key: 'company', label: 'Company' },
  { key: 'sector', label: 'Sector' },
  { key: 'job_level', label: 'Job level' },
  { key: 'department', label: 'Department' },
]

export function Coverage({ filters }: { filters: Filters }) {
  const [by, setBy] = useState('company')
  const [search, setSearch] = useState('')

  const coverage = useQuery({
    queryKey: ['coverage', by, filters.query],
    queryFn: () => getCoverage(by, filters.query),
  })
  const untrained = useQuery({
    queryKey: ['untrained', filters.query],
    queryFn: () => getUntrained(filters.query),
  })

  if (coverage.isPending) return <p className="muted">Computing coverage…</p>
  if (coverage.isError) return <p className="warn">Coverage could not be computed.</p>
  if (!coverage.data) return null

  const data = coverage.data
  const shown = data.slices.filter((slice) =>
    slice.label.toLowerCase().includes(search.trim().toLowerCase()),
  )
  // A common scale, so the gap between two companies is a length rather than
  // two numbers to subtract. Against each slice's own maximum every bar would
  // be full and the finding would disappear.
  const ceiling = Math.max(...data.slices.map((s) => Number(s.participation.value ?? 0)), 1)

  return (
    <>
      <div className="view-head">
        <div>
          <h1>Coverage</h1>
          <p className="muted">Who has been reached, and who has not.</p>
        </div>
        <ExportMenu
          query={filters.query}
          filtersApplied={data.filters_applied}
          options={[
            { path: 'records/coverage_gap', label: 'The roster this is measured against' },
            { path: 'kpis', label: 'Every figure, with definitions' },
          ]}
        />
      </div>

      <ExclusionBanner excluded={data.excluded_count} flagged={data.flagged_count} />

      <div className="figure-grid">
        <Figure metric={data.overall_participation} />
        <Figure metric={data.overall_untrained} />
        <Figure metric={data.months_since_last_training} />
      </div>

      <section className="group">
        <div className="group-head">
          <h2>Participation by {DIMENSIONS.find((d) => d.key === by)?.label.toLowerCase()}</h2>
          <p className="muted">
            Each slice is the same metric recomputed under a narrower filter, so the parts sum to
            the whole above rather than approximating it.
          </p>
          <div className="segmented" role="group" aria-label="Break down by">
            {DIMENSIONS.map((dimension) => (
              <button
                key={dimension.key}
                type="button"
                className={by === dimension.key ? 'segmented-on' : undefined}
                aria-pressed={by === dimension.key}
                onClick={() => setBy(dimension.key)}
              >
                {dimension.label}
              </button>
            ))}
          </div>
          {data.tail.values_omitted > 0 && (
            <p className="warn">
              Showing the {data.tail.values_shown} largest of {data.tail.values_total}{' '}
              {data.tail.dimension}s by headcount. The other {data.tail.values_omitted} hold{' '}
              {data.tail.employees_omitted.toLocaleString()} employees between them — narrow with
              the filter bar above to reach one.
            </p>
          )}
          {data.slices.length > 12 && (
            <input
              type="search"
              className="search"
              placeholder={`Search the ${data.slices.length} shown`}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              aria-label="Search the breakdown"
            />
          )}
        </div>

        <ul className="bars">
          {shown.map((slice) => {
            const value = Number(slice.participation.value ?? 0)
            return (
              <li key={slice.key} className="bar-row">
                <span className="bar-label" title={slice.label}>
                  {slice.label}
                </span>
                <span className="bar-track">
                  <span className="bar-fill" style={{ width: `${(value / ceiling) * 100}%` }} />
                </span>
                <span className="bar-value">{slice.participation.formatted}</span>
                <span className="bar-note">
                  {Number(slice.participation.numerator ?? 0).toLocaleString()} of{' '}
                  {Number(slice.participation.denominator ?? 0).toLocaleString()} ·{' '}
                  {slice.untrained.formatted} untrained
                </span>
              </li>
            )
          })}
        </ul>
      </section>

      <section className="group">
        <div className="group-head">
          <h2>No training at all</h2>
          {untrained.data && (
            <p className="muted">
              {untrained.data.total.toLocaleString()} people in scope have attended nothing. This is
              the Coverage Gap figure above, as a list — the same statement, so the count and the
              rows cannot disagree.
            </p>
          )}
        </div>

        {untrained.isPending && <p className="muted">Counting…</p>}
        {untrained.data?.gated && (
          <p className="exclusion exclusion-clear">
            <span className="exclusion-dot" aria-hidden="true" />
            <span>{untrained.data.gate_note}</span>
          </p>
        )}
        {untrained.data && !untrained.data.gated && untrained.data.rows.length > 0 && (
          <>
            {untrained.data.truncated && (
              <p className="drawer-truncated">
                Showing the first {untrained.data.rows.length.toLocaleString()} of{' '}
                {untrained.data.total.toLocaleString()}. Narrow further to see the rest.
              </p>
            )}
            <RecordGrid
              columns={untrained.data.columns}
              rows={untrained.data.rows}
              filename="no-training.csv"
            />
          </>
        )}
      </section>
    </>
  )
}
