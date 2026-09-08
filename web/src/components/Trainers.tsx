/**
 * The trainer scorecard, and the list you reach it from.
 *
 * TWO ROWS ARE NOT PEOPLE, AND THE LIST SAYS SO
 *
 * `L&D Team` is what a session names when nobody recorded who delivered it, and
 * it ranks sixth by sessions — above four named trainers. `Belton Academy` is
 * an outside vendor. Both are shown, because their sessions are real and their
 * hours are in every total, and both are marked, because a ranking that puts
 * them among colleagues without saying so is a ranking that says something
 * untrue.
 *
 * THREE COUNTS, NOT ONE
 *
 * Sessions delivered, programmes covered and people reached answer different
 * questions, and the shape in this data makes that concrete: the trainer with
 * the most attendances has one of the smallest catalogues. A list showing only
 * sessions invites exactly the wrong comparison.
 */

import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'

import { getTrainerScorecard, getTrainers } from '../api'
import type { Filters } from '../filters'
import { ExclusionBanner } from './ExclusionBanner'
import { ExportMenu } from './ExportMenu'
import { Figures } from './Figure'

function Marks({ placeholder, external }: { placeholder: boolean; external: boolean }) {
  if (placeholder) {
    return (
      <span className="tag tag-corrected" title="Not a person — an unrecorded trainer">
        not a person
      </span>
    )
  }
  if (external) {
    return (
      <span className="tag tag-new" title="An outside vendor, not a colleague">
        external
      </span>
    )
  }
  return null
}

export function TrainerList({ filters }: { filters: Filters }) {
  const list = useQuery({
    queryKey: ['trainers', filters.query],
    queryFn: () => getTrainers(filters.query),
  })

  if (list.isPending) return <p className="muted">Loading trainers…</p>
  if (list.isError) return <p className="warn">The trainers could not be listed.</p>
  if (!list.data) return null

  return (
    <section className="group">
      <div className="group-head">
        <h2>Trainers</h2>
        <p className="muted">
          {list.data.trainers.length} after name variants were merged. Sessions, programmes and
          people reached are three different questions — the order below is by sessions, and it is
          not the order by reach.
        </p>
      </div>

      <table className="table">
        <thead>
          <tr>
            <th scope="col">Trainer</th>
            <th scope="col">Sessions</th>
            <th scope="col">Programmes</th>
            <th scope="col">Attendances</th>
          </tr>
        </thead>
        <tbody>
          {list.data.trainers.map((trainer) => (
            <tr key={trainer.trainer_key}>
              <td>
                <Link to={`/trainers/${trainer.trainer_key}${filters.query}`}>
                  {trainer.canonical_name}
                </Link>{' '}
                <Marks placeholder={trainer.is_placeholder} external={trainer.is_external} />
              </td>
              <td>{trainer.sessions}</td>
              <td>{trainer.programs}</td>
              <td>{trainer.attendances}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

export function TrainerScorecard({ filters }: { filters: Filters }) {
  const { key } = useParams()
  const trainerKey = Number(key)
  const card = useQuery({
    queryKey: ['trainer-scorecard', trainerKey, filters.query],
    queryFn: () => getTrainerScorecard(trainerKey, filters.query),
  })

  if (card.isPending) return <p className="muted">Computing the scorecard…</p>
  if (card.isError) return <p className="warn">That trainer could not be found.</p>
  if (!card.data) return null

  const { trainer, delivery, programme_level, nps_by_program, program_ids } = card.data

  return (
    <>
      <div className="scope">
        <p className="scope-line">
          <Link to={`/trainers${filters.query}`}>← all trainers</Link>
        </p>
        {/* The file carries the same caveat this screen does: quality figures
            are over the programmes this trainer delivered, not attributed to
            them. A scorecard that travels without it is the one that gets read
            as a performance rating. */}
        <ExportMenu
          query={filters.query}
          filtersApplied={card.data.filters_applied_scorecard}
          options={[
            {
              path: `trainers/${trainerKey}/scorecard`,
              label: 'This scorecard, caveat and all',
              formats: ['pdf'],
            },
          ]}
        />
      </div>

      <header className="card-head">
        <h2>
          {trainer.canonical_name}{' '}
          <Marks placeholder={trainer.is_placeholder} external={trainer.is_external} />
        </h2>
        {trainer.is_placeholder && (
          <p className="warn">
            This is not a person. Sessions land here when nobody recorded who delivered them, so
            these figures describe unattributed delivery rather than anyone's work.
          </p>
        )}
        {trainer.is_external && (
          <p className="muted">
            An outside vendor. These scores describe a supplier, not a colleague's facilitation.
          </p>
        )}
      </header>

      <ExclusionBanner excluded={card.data.excluded_count} flagged={card.data.flagged_count} />

      <Figures metrics={delivery} caption="Narrowed to the sessions this trainer delivered." />

      {program_ids.length === 0 ? (
        <p className="muted">Nothing delivered in the filtered period.</p>
      ) : (
        <Figures
          metrics={programme_level}
          caption={
            `Across the ${program_ids.length} programme${program_ids.length === 1 ? '' : 's'} they ` +
            'delivered. A survey response belongs to a programme rather than to a session, so on a ' +
            'programme two trainers shared, both carry the same responses — a weaker claim than ' +
            '"this trainer’s NPS", and the true one.'
          }
        />
      )}

      {nps_by_program.length > 0 && (
        <section className="group">
          <div className="group-head">
            <h3>NPS by programme</h3>
            <p className="muted">
              These combine by summing promoters and responses, never by averaging the scores: 100%
              over two responses and 80% over ninety-eight are 80.4% together, not 90%.
            </p>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Programme</th>
                <th scope="col">NPS</th>
                <th scope="col">Responses</th>
              </tr>
            </thead>
            <tbody>
              {nps_by_program.map((row) => (
                <tr key={row.crm_program_id}>
                  <td>
                    <Link to={`/programs/${row.crm_program_id}${filters.query}`}>{row.title}</Link>
                  </td>
                  <td>{row.metric.formatted}</td>
                  <td>{row.metric.denominator ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  )
}
