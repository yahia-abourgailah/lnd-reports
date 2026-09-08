/**
 * Top learners, and the search that reaches anybody else.
 *
 * THE RANKING IS SCOPED, THE NUMBERS ARE NOT
 *
 * A named league table of employees by training hours reads as recognition in
 * one meeting and as a performance record in another, and nobody on it asked to
 * be ranked. So the API withholds names until the view is narrowed to a
 * department, sector, company or job level — while the count and the spread
 * stay exact either way.
 *
 * This screen shows that spread whether or not the names are available, because
 * "288 learners, from 19.5 to 60.5 hours, median 44.5" is the useful part of a
 * distribution and carries no individual judgement at all. The gate is a
 * decision about people rather than a technical limit, and the screen says so
 * rather than looking broken.
 *
 * Search is the way to one person without ranking anybody. Looking somebody up
 * because you are about to talk to them is a different act from reading down a
 * list of who is bottom.
 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { getTopLearners, searchLearners } from '../api'
import type { Filters } from '../filters'
import { ExclusionBanner } from './ExclusionBanner'
import { ExportMenu } from './ExportMenu'

function Spread({
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
  // Guard the degenerate case rather than dividing by it: one learner, or a
  // filter where everybody has identical hours, would put the marker at NaN%.
  const span = high - low || 1
  const at = (v: number) => `${((v - low) / span) * 100}%`

  return (
    <figure className="spread">
      <figcaption>
        <strong>{total.toLocaleString()}</strong> learner{total === 1 ? '' : 's'} with any
        attendance, from {low} to {high} hours
      </figcaption>
      <div className="spread-track">
        <span className="spread-fill" />
        <span className="spread-tick" style={{ left: at(mid) }} title={`median ${mid} hours`} />
      </div>
      <div className="spread-scale">
        <span>{low} h</span>
        <span className="spread-median">median {mid} h</span>
        <span>{high} h</span>
      </div>
    </figure>
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
          <p className="muted">Ranked by learner hours — the sum of the sessions a person attended.</p>
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

      <Spread
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

      {data.gated ? (
        <div className="gate">
          <p className="gate-title">The ranking is not shown for the whole company</p>
          <p>{data.gate_note}</p>
          <p className="muted">
            Narrow by department, sector, company or job level in the bar above — or search for
            somebody by name. The figures above are exact either way.
          </p>
        </div>
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

      {/* Only when rows were actually cut short. A gated response also sets
          `truncated`, and saying "showing the first 0 of 288" there describes a
          truncation that did not happen — the ranking is withheld, which the
          panel above already explains. */}
      {data.truncated && !data.gated && data.rows.length > 0 && (
        <p className="muted small">
          Showing the first {data.rows.length} of {data.total_learners.toLocaleString()}. Narrow
          further, or export the attendance behind the figure.
        </p>
      )}
    </>
  )
}
