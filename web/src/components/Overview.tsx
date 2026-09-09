/**
 * The board, laid out the way L&D's own workbook lays it out.
 *
 * One strip of headline figures across the top, then a row of three charts,
 * then a row of two — the shape of the DASHBOARD tab in `L&D Main Reports.xlsx`,
 * because that is the screen these people have opened every month for years.
 *
 * WHY REPRODUCE A LAYOUT AT ALL
 *
 * Week 10 asks L&D to accept eleven restated figures, one of which moved by a
 * factor of six. Handing them that on a screen which also looks nothing like
 * the one they know puts two unfamiliar things in front of them at once, and
 * only one of those is the point. The layout is the part we can make free.
 *
 * WHAT IS DELIBERATELY NOT THE SAME
 *
 * Nothing here is a `#REF!`. Four of the workbook's nine headline cells are
 * that today, because they point at a pivot that moved.
 *
 * The pie has two slices, not three: the workbook drew `Grand Total` as a slice
 * of the same pie it totalled.
 *
 * And every chart states its own maximum. The workbook printed a number beside
 * each bar and no axis, so two charts of very different magnitudes read as the
 * same size.
 *
 * The provenance-grouped detail underneath is ours and has no counterpart —
 * it is how somebody sees at a glance which figures changed and why.
 */

import { useQuery } from '@tanstack/react-query'

import { getPrograms, getTrainers, getTrend, type Metric } from '../api'
import type { Filters } from '../filters'
import { Bars, Donut, GroupedBars } from './Charts'

/** The nine the workbook puts across the top, in its order. */
const STRIP: string[] = [
  'total_programs',
  'training_days',
  'total_participants',
  'training_hours_delivered',
  'nps',
  'facilitator_performance',
  'knowledge_relevance',
  'activity_effectiveness',
  'logistics_effectiveness',
]

/** The figure without its unit word.
 *
 *  "365.2 hours" is wider than a ninth of the strip and collided with the
 *  figure beside it. The label already says Hours — the workbook's own cell
 *  reads 130.5 and nothing else — so the word is the part that goes. */
function compact(metric: Metric): string {
  if (metric.value === null) return 'no value'
  if (metric.unit === 'hours' || metric.unit === 'months') {
    return Number(metric.value).toLocaleString(undefined, { maximumFractionDigits: 1 })
  }
  return metric.formatted
}

function Strip({ metrics }: { metrics: Metric[] }) {
  const by = new Map(metrics.map((metric) => [metric.key, metric]))
  const participation = by.get('participation_rate')
  const split = by.get('public_program_share')

  return (
    <section className="strip">
      <div className="strip-figures">
        {STRIP.map((key) => {
          const metric = by.get(key)
          if (!metric) return null
          return (
            <div className="strip-figure" key={key}>
              <p className="strip-label">{metric.title}</p>
              <p className="strip-value">{compact(metric)}</p>
            </div>
          )
        })}
      </div>

      <div className="strip-side">
        {participation && (
          <div className="strip-figure strip-lead">
            <p className="strip-label">{participation.title}</p>
            <p className="strip-value">
              {participation.value === null ? 'no value' : participation.formatted}
            </p>
            {/* The workbook's cell divided by the number 192, typed in. This
                one states its denominator, because the denominator is the
                whole of what was wrong with it. */}
            {participation.denominator && (
              <p className="strip-sample">
                {Number(participation.numerator).toLocaleString()} of{' '}
                {Number(participation.denominator).toLocaleString()} employees
              </p>
            )}
          </div>
        )}
        {/* Stacked under Participation Rate, these read as if they continued
            its sentence — "201 of 1,487 employees ... 87.3%". They do not: that
            line counts people and these count programmes. So they get their own
            caption, and the counts sit beside the shares, because "48 of 55" is
            unambiguous in a way a bare percentage next to a headcount is not. */}
        {split && split.value !== null && split.denominator !== null && (
          <div className="strip-split">
            <p className="strip-split-head">
              Of {Number(split.denominator).toLocaleString()} programmes
            </p>
            <dl>
              <div>
                <dt>Public calendar</dt>
                <dd>
                  <b>{split.formatted}</b>
                  <em>{Number(split.numerator).toLocaleString()}</em>
                </dd>
              </div>
              <div>
                <dt>Customised</dt>
                <dd>
                  <b>{(100 - Number(split.value)).toFixed(1)}%</b>
                  <em>
                    {(
                      Number(split.denominator) - Number(split.numerator)
                    ).toLocaleString()}
                  </em>
                </dd>
              </div>
            </dl>
          </div>
        )}
      </div>
    </section>
  )
}

function Panel({
  title,
  note,
  children,
}: {
  title: string
  note?: string
  children: React.ReactNode
}) {
  return (
    <section className="panel">
      <h3>{title}</h3>
      {note && <p className="panel-note">{note}</p>}
      {children}
    </section>
  )
}

export function Overview({ filters, metrics }: { filters: Filters; metrics: Metric[] }) {
  const programs = useQuery({
    queryKey: ['programs', filters.query],
    queryFn: () => getPrograms(filters.query),
  })
  const trainers = useQuery({
    queryKey: ['trainers', filters.query],
    queryFn: () => getTrainers(filters.query),
  })
  // Three metrics, one month axis. Each is the registry's own trend — the same
  // series the sparkline on that metric's card draws.
  const days = useQuery({
    queryKey: ['trend', 'training_days', filters.query],
    queryFn: () => getTrend('training_days', filters.query),
  })
  const people = useQuery({
    queryKey: ['trend', 'total_participants', filters.query],
    queryFn: () => getTrend('total_participants', filters.query),
  })
  const count = useQuery({
    queryKey: ['trend', 'total_programs', filters.query],
    queryFn: () => getTrend('total_programs', filters.query),
  })

  const by = new Map(metrics.map((metric) => [metric.key, metric]))
  const delivered = by.get('lnd_delivered_share')

  // Keyed by period rather than zipped by index: the three trends are three
  // requests, and a metric with no value in a month is absent from its own
  // series. Lining them up by position would put January's participants under
  // February's training days the first time that happened.
  const value = (points: { key: string; metric: { value: string | null } }[] | undefined, key: string) =>
    Number(points?.find((point) => point.key === key)?.metric.value ?? 0)

  const months = (days.data?.points ?? []).map((point) => ({
    // Fifteen months in a 380px panel is 25px a column. "Jul 2025" overlapped
    // its neighbour, so the month stands alone and the year appears once, on
    // the column where it changes.
    label: point.label.slice(0, 3),
    year: point.label.slice(-4),
    values: [
      Number(point.metric.value ?? 0),
      value(people.data?.points, point.key),
      value(count.data?.points, point.key),
    ],
  }))

  const ranked = (programs.data?.programs ?? [])
    .filter((program) => program.participants > 0)
    .sort((a, b) => b.participants - a.participants)

  return (
    <>
      <Strip metrics={metrics} />

      <div className="board board-3">
        <Panel
          title="Delivered by L&D, or not"
          note="Counted on completed programmes. The workbook drew its own total as a third slice of this."
        >
          {delivered && delivered.numerator !== null && delivered.denominator !== null ? (
            <Donut
              total={Number(delivered.denominator)}
              parts={[
                { label: 'By L&D', value: Number(delivered.numerator), tone: 'a' },
                {
                  label: 'By someone else',
                  value: Number(delivered.denominator) - Number(delivered.numerator),
                  tone: 'b',
                },
              ]}
              caption="Programmes delivered by L&D against everyone else"
            />
          ) : (
            <p className="chart-empty">Not measurable under these filters.</p>
          )}
        </Panel>

        <Panel title="By month" note="Each series is that metric's own trend, not a second count.">
          <GroupedBars
            groups={months}
            series={['Training days', 'Participants', 'Programmes']}
            caption="Training days, participants and programmes by month"
          />
        </Panel>

        <Panel
          title="Trainers"
          note="Two of these are not people: L&D Team is an unrecorded trainer, Belton Academy a vendor."
        >
          <Bars
            rows={(trainers.data?.trainers ?? [])
              .slice()
              .sort((a, b) => b.sessions - a.sessions)
              .map((trainer) => ({
                label: trainer.canonical_name,
                values: [trainer.sessions, trainer.programs],
              }))}
            series={['Sessions', 'Programmes']}
            limit={10}
            caption="Sessions and programmes per trainer"
          />
        </Panel>
      </div>

      <div className="board board-2">
        <Panel title="Participation per programme" note="Distinct people who attended.">
          <Bars
            rows={ranked.map((program) => ({
              label: program.title,
              values: [program.participants],
            }))}
            series={['Participants']}
            limit={14}
            caption="Participants per programme"
          />
        </Panel>

        <Panel
          title="Enrolled against attended"
          note="The gap is the no-show. Enrolments are rows; participants are people."
        >
          <Bars
            rows={ranked
              .slice()
              .sort((a, b) => b.enrollments - a.enrollments)
              .map((program) => ({
                label: program.title,
                values: [program.enrollments, program.participants],
              }))}
            series={['Enrolled', 'Attended']}
            limit={14}
            caption="Enrolments against participants, per programme"
          />
        </Panel>
      </div>
    </>
  )
}
