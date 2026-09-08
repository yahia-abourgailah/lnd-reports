/**
 * One person's learning history.
 *
 * The figures are the platform's own metrics filtered to this employee, not a
 * separate calculation — so a learner's NPS is computed exactly as the
 * company-wide one is, over exactly the population the metric declares. A
 * profile that summed its own averages would be a second definition, and the
 * one somebody would quote in a conversation with the person themselves.
 *
 * SOMEBODY WHO HAS LEFT STILL HAS A PROFILE
 *
 * 149 of the 419 people who have trained are no longer on the roster. Their
 * attendance is real and stays counted; they are simply not in any denominator.
 * The profile says which they are, because a manager looking somebody up and
 * finding them absent from coverage deserves the reason rather than a
 * discrepancy.
 */

import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'

import { getLearner } from '../api'
import type { Filters } from '../filters'
import { ExportMenu } from './ExportMenu'
import { Figures } from './Figure'

export function LearnerProfile({ filters }: { filters: Filters }) {
  const { key } = useParams()
  const employeeKey = Number(key)
  const profile = useQuery({
    queryKey: ['learner', employeeKey, filters.query],
    queryFn: () => getLearner(employeeKey, filters.query),
    enabled: Number.isFinite(employeeKey),
  })

  if (!Number.isFinite(employeeKey)) return <p className="warn">That is not a learner.</p>
  if (profile.isPending) return <p className="muted">Loading…</p>
  if (profile.isError || !profile.data)
    return <p className="warn">That learner could not be found.</p>

  const p = profile.data
  const attributes = [
    ['Employee code', p.employee_code],
    ['Department', p.department],
    ['Company', p.company],
    ['Sector', p.sector],
    ['Job level', p.job_level],
    ['Position', p.position],
  ].filter(([, value]) => value)

  return (
    <>
      <p className="crumb">
        <Link to="/learners">← Learners</Link>
      </p>

      <div className="view-head">
        <div>
          <h1>{p.name}</h1>
          <p className="muted">
            {attributes.map(([label, value]) => `${label}: ${value}`).join(' · ')}
          </p>
          {!p.on_current_roster && (
            <p className="left-roster">
              No longer on the roster. Their attendance is still counted in every total; they are
              not in any participation denominator.
            </p>
          )}
        </div>
        <ExportMenu
          query={filters.query}
          filtersApplied={p.filters_applied}
          options={[{ path: 'records/learner_hours', label: 'Attendance records' }]}
        />
      </div>

      <Figures
        metrics={p.figures}
        caption="Each figure is the platform's own metric, filtered to this person."
      />

      <h2 className="section">Programmes attended</h2>
      {p.programs.length === 0 ? (
        <p className="muted">No attendance in this period.</p>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Programme</th>
                <th className="r">Sessions</th>
                <th className="r">Hours</th>
                <th>First</th>
                <th>Last</th>
              </tr>
            </thead>
            <tbody>
              {p.programs.map((row) => (
                <tr key={row.crm_program_id}>
                  <td>
                    <Link to={`/programs/${row.crm_program_id}`}>{row.title}</Link>
                  </td>
                  <td className="num r">{row.sessions}</td>
                  <td className="num r">{row.hours}</td>
                  <td className="num muted">{row.first_attended ?? '—'}</td>
                  <td className="num muted">{row.last_attended ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
