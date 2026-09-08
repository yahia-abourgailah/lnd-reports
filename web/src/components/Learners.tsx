/**
 * Top learners, and the search that reaches anybody else.
 *
 * THE RANKING WAS SCOPED, AND IS NOT ANY MORE
 *
 * This screen used to withhold the names unless the view was narrowed to a
 * department, sector, company or job level. A screen headed "Top learners" that
 * showed no learners is not a careful version of the feature, and the
 * requirement it was hiding is explicit: a top-learners ranking, derived
 * automatically, replacing the hand-typed sheet — a sheet the workbook already
 * published company-wide every cycle.
 *
 * The caution behind the gate is still true, so it is said rather than
 * enforced: a named list ordered by training hours reads as recognition in one
 * meeting and as a record in another. The line under the title says what the
 * ranking is and is not, and the three measures sit side by side because they
 * disagree — more sessions is not more programmes, and neither is more hours.
 *
 * THE POPULATION LINE IS NOT THE VISIBLE ROWS
 *
 * "288 learners, from 1 to 60.5 hours, median 8" is computed over the whole
 * ranked population, not the twenty-five on screen. A median of the visible
 * rows would be a different statistic wearing the same label.
 *
 * Search is still the way to one person without reading a ranking at all.
 * Looking somebody up because you are about to talk to them is a different act
 * from reading down a list.
 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { getTopLearners, searchLearners } from '../api'
import type { Filters } from '../filters'
import { ExclusionBanner } from './ExclusionBanner'
import { ExportMenu } from './ExportMenu'

function Population({
  min,
  median,
  max,
  total,
}: {
  min: string | null
  median: string | null
  max: string | null
  total: number
}) {
  if (min === null || median === null || max === null) return null
  const low = Number(min)
  const mid = Number(median)
  const high = Number(max)

  // A sentence, not a bar. This was a gradient track with a tick on it, and the
  // geometry kept saying things the numbers did not: the median label was
  // pinned to the centre of the bar wherever the median actually was, and a
  // single learner got a full-width gradient implying a spread of one point.
  // Three numbers in a line carry everything the drawing did and cannot be
  // misread by position.
  return (
    <p className="population">
      <strong>{total.toLocaleString()}</strong> learner{total === 1 ? '' : 's'} with any
      attendance
      {high === low ? (
        <>
          , {total === 1 ? 'on' : 'all on'} {low} hour{low === 1 ? '' : 's'}
        </>
      ) : (
        <>
          , from {low} to {high} hours · median {mid}
        </>
      )}
    </p>
  )
}

export function Learners({ filters }: { filters: Filters }) {
  const [term, setTerm] = useState('')
  const top = useQuery({
    queryKey: ['top-learners', filters.query],
    queryFn: () => getTopLearners(filters.query),
  })
  const found = useQuery({
    queryKey: ['learner-search', term],
    queryFn: () => searchLearners(term),
    enabled: term.trim().length >= 2,
  })

  if (top.isPending) return <p className="muted">Ranking…</p>
  if (top.isError || !top.data) return <p className="warn">The ranking could not be computed.</p>
  const data = top.data

  return (
    <>
      <div className="view-head">
        <div>
          <h1>Top learners</h1>
          <p className="muted">
            Ranked by learner hours — the sum of the sessions a person attended. It says who
            received the most training, not who performed best, and the three measures below
            disagree: more sessions is not more programmes, and neither is more hours.
          </p>
        </div>
        <ExportMenu
          query={filters.query}
          filtersApplied={data.filters_applied}
          options={[
            { path: 'records/learner_hours', label: 'Attendance behind these hours' },
          ]}
        />
      </div>

      <ExclusionBanner excluded={data.excluded_count} flagged={data.flagged_count} />

      <Population
        min={data.hours_min}
        median={data.hours_median}
        max={data.hours_max}
        total={data.total_learners}
      />

      <div className="learner-search">
        <label>
          <span className="sr-only">Find a learner by name or employee code</span>
          <input
            type="search"
            placeholder="Find a learner by name or code…"
            value={term}
            onChange={(event) => setTerm(event.target.value)}
          />
        </label>
        {found.data && term.trim().length >= 2 && (
          <ul className="learner-results">
            {found.data.results.length === 0 && <li className="muted">Nobody matches that.</li>}
            {found.data.results.map((person) => (
              <li key={person.employee_key}>
                <Link to={`/learners/${person.employee_key}`}>{person.name}</Link>
                <span className="muted">
                  {[person.employee_code, person.department, person.company]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {data.rows.length === 0 ? (
        <p className="muted">
          Nobody has attended anything in this scope, so there is nothing to rank.
        </p>
      ) : (
        <div className="scroll">
          <table className="ranked">
            <thead>
              <tr>
                <th className="r">#</th>
                <th>Learner</th>
                <th>Department</th>
                <th className="r">Programmes</th>
                <th className="r">Sessions</th>
                <th className="r">Hours</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => (
                <tr key={row.employee_key}>
                  <td className="num r">{row.rank}</td>
                  <td>
                    <Link to={`/learners/${row.employee_key}`}>{row.name}</Link>
                  </td>
                  <td className="muted">{row.department ?? '—'}</td>
                  <td className="num r">{row.programs}</td>
                  <td className="num r">{row.sessions}</td>
                  <td className="num r">{row.hours}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Only when rows were actually cut short, so it can never read
          "showing the first 0 of 288". */}
      {data.truncated && data.rows.length > 0 && (
        <p className="muted small">
          Showing the first {data.rows.length} of {data.total_learners.toLocaleString()}. Narrow
          further, or export the attendance behind the figure.
        </p>
      )}
    </>
  )
}
