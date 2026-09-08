/**
 * The programme scorecard, and the list you reach it from.
 *
 * The comments are the reason this screen exists. Eighty-eight free-text
 * answers across thirty-two programmes are the only qualitative signal the
 * platform has, and the only place a 91.9% logistics score has an explanation.
 * They are shown whole — no clamp, no "read more" — because truncating the one
 * thing a reader would act on turns it into decoration.
 */

import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'

import { getProgramScorecard, getPrograms } from '../api'
import type { Filters } from '../filters'
import { ExclusionBanner } from './ExclusionBanner'
import { Figures } from './Figure'

function hours(value: string | null): string {
  return value === null ? '—' : `${Number(value).toFixed(1)}h`
}

export function ProgramList({ filters }: { filters: Filters }) {
  const list = useQuery({
    queryKey: ['programs', filters.query],
    queryFn: () => getPrograms(filters.query),
  })

  if (list.isPending) return <p className="muted">Loading programmes…</p>
  if (list.isError) return <p className="warn">The programmes could not be listed.</p>
  if (!list.data) return null

  return (
    <section className="group">
      <div className="group-head">
        <h2>Programmes</h2>
        <p className="muted">
          {list.data.programs.length} completed programmes in scope. Open one for its sessions,
          fill rate, no-show, quality scores and every comment left on it.
        </p>
      </div>

      <table className="table">
        <thead>
          <tr>
            <th scope="col">Programme</th>
            <th scope="col">Ran</th>
            <th scope="col">Delivered by</th>
            <th scope="col">Audience</th>
            <th scope="col">Capacity</th>
          </tr>
        </thead>
        <tbody>
          {list.data.programs.map((program) => (
            <tr key={program.crm_program_id}>
              <td>
                <Link to={`/programs/${program.crm_program_id}${filters.query}`}>
                  {program.title}
                </Link>
              </td>
              <td>{program.start_date ?? '—'}</td>
              <td>{program.type ?? '—'}</td>
              <td>{program.target ?? '—'}</td>
              <td>{program.capacity ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

export function ProgramScorecard({ filters }: { filters: Filters }) {
  const { id } = useParams()
  const programId = Number(id)
  const card = useQuery({
    queryKey: ['program-scorecard', programId, filters.query],
    queryFn: () => getProgramScorecard(programId, filters.query),
  })

  if (card.isPending) return <p className="muted">Computing the scorecard…</p>
  if (card.isError) return <p className="warn">That programme could not be found.</p>
  if (!card.data) return null

  const { program, figures, sessions, comments, excluded_count, flagged_count } = card.data

  return (
    <>
      <div className="scope">
        <p className="scope-line">
          <Link to={`/programs${filters.query}`}>← all programmes</Link>
        </p>
      </div>

      <header className="card-head">
        <h2>{program.title}</h2>
        <p className="muted">
          {program.status ?? 'status unknown'} · {program.type ?? 'unknown delivery'} ·{' '}
          {program.target ?? 'unknown audience'}
          {program.customised_department_name && ` · built for ${program.customised_department_name}`}
          {' · '}
          {program.start_date ?? '—'} to {program.end_date ?? '—'}
        </p>
        <p className="muted">
          {/* Plural on purpose: a programme two people shared has no single
              trainer, which is why the programme-level trainer is often unset. */}
          {program.trainer_names.length > 0
            ? `Delivered by ${program.trainer_names.join(', ')}`
            : 'No trainer named on any session — this programme is in the exception queue.'}
        </p>
      </header>

      <ExclusionBanner excluded={excluded_count} flagged={flagged_count} />

      {/* An upcoming programme is reachable by URL and is in no figure: every
          programme metric counts on `computed_status = completed`. Without this
          line the card is a wall of zeros, and a zero is a measurement — the
          one reading it must not invite. */}
      {program.status !== 'completed' && (
        <p className="warn">
          This programme is <strong>{program.status ?? 'not marked completed'}</strong>. Every
          figure below counts completed programmes only, so it contributes nothing to them yet —
          these zeros are an absence of delivery, not a measurement of none.
        </p>
      )}

      <Figures metrics={figures} />

      <section className="group">
        <div className="group-head">
          <h3>Sessions</h3>
          <p className="muted">
            {sessions.length} session{sessions.length === 1 ? '' : 's'}. Head counts come from the
            same scope as the figures above, so the column adds up to them.
          </p>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Date</th>
              <th scope="col">Trainer</th>
              <th scope="col">Location</th>
              <th scope="col">Duration</th>
              <th scope="col">Attended</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((session) => (
              <tr key={session.crm_session_id}>
                <td>{session.session_date}</td>
                <td>{session.trainer ?? '—'}</td>
                <td>{session.location ?? '—'}</td>
                <td>
                  {hours(session.duration_hours)}
                  {!session.duration_derivable && (
                    <span className="tag tag-corrected" title="Times would not subtract">
                      no duration
                    </span>
                  )}
                </td>
                <td>{session.attendees}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="group">
        <div className="group-head">
          <h3>Comments</h3>
          <p className="muted">
            {comments.length === 0
              ? 'Nobody left a written answer on this programme.'
              : `${comments.length} written answer${comments.length === 1 ? '' : 's'}, shown whole and without the writer's name.`}
          </p>
        </div>
        <ul className="comments">
          {comments.map((comment, index) => (
            <li key={index} className={`comment comment-${comment.nps_band ?? 'none'}`}>
              <p className="comment-text">{comment.text}</p>
              <p className="comment-meta">
                {comment.responded_date ?? 'undated'}
                {comment.recommend_score !== null && ` · recommended ${comment.recommend_score}/10`}
                {comment.nps_band && ` · ${comment.nps_band}`}
              </p>
            </li>
          ))}
        </ul>
      </section>
    </>
  )
}
