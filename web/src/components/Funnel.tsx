/**
 * Enrolled → attended → evaluated.
 *
 * Three numbers and two drops, and resisting a fourth is most of the design.
 * The temptation is to decorate this into a conversion dashboard; what it has
 * to do is make two facts legible — that 411 people enrolled and never came,
 * and that 353 who came never said anything.
 *
 * The drop is the metric's own numerator and never `this stage minus the next`.
 * A walk-in attends without enrolling, so a subtraction cancels one against a
 * genuine no-show and can report a clean funnel for a programme half the
 * enrolled list skipped. Walk-ins are shown on their own line for the same
 * reason: netted off, they are invisible; stated, they explain why the stages
 * do not simply subtract.
 *
 * Every stage opens to its own rows, at the stage's own grain — one person on
 * one programme, not the attendance rows underneath them.
 */

import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { getFunnel, getFunnelStage } from '../api'
import type { Filters } from '../filters'
import { ExclusionBanner } from './ExclusionBanner'
import { ExportMenu } from './ExportMenu'
import { RecordGrid } from './RecordGrid'

function StageDrawer({
  stage,
  label,
  query,
  onClose,
}: {
  stage: string
  label: string
  query: string
  onClose: () => void
}) {
  const rows = useQuery({
    queryKey: ['funnel-stage', stage, query],
    queryFn: () => getFunnelStage(stage, query),
  })

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="drawer-scrim" onClick={onClose} role="presentation">
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`People ${label.toLowerCase()}`}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="drawer-head">
          <div>
            <h2>{label}</h2>
            <p className="muted">{rows.data?.filters_applied ?? ''}</p>
          </div>
          <button type="button" className="drawer-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        {rows.isPending && <p className="muted drawer-pad">Fetching the records…</p>}
        {rows.isError && <p className="warn drawer-pad">Those records could not be fetched.</p>}

        {rows.data && (
          <>
            <div className="drawer-figure">
              <span className="drawer-value">{rows.data.total.toLocaleString()}</span>
              <span className="drawer-grain">one person on one programme</span>
            </div>
            {rows.data.truncated && (
              <p className="drawer-truncated">
                Showing the first {rows.data.rows.length.toLocaleString()} of{' '}
                {rows.data.total.toLocaleString()}.
              </p>
            )}
            <RecordGrid
              columns={rows.data.columns}
              rows={rows.data.rows}
              filename={`${stage}.csv`}
            />
          </>
        )}
      </aside>
    </div>
  )
}

export function Funnel({ filters }: { filters: Filters }) {
  const [opened, setOpened] = useState<{ stage: string; label: string } | null>(null)
  const funnel = useQuery({
    queryKey: ['funnel', filters.query],
    queryFn: () => getFunnel(filters.query),
  })

  if (funnel.isPending) return <p className="muted">Computing the funnel…</p>
  if (funnel.isError) return <p className="warn">The funnel could not be computed.</p>
  if (!funnel.data) return null

  const { steps, walk_ins, excluded_count, flagged_count, filters_applied } = funnel.data
  const widest = Math.max(...steps.map((step) => step.count), 1)

  return (
    <>
      <div className="scope">
        <p className="scope-line">
          One person on one programme at every stage. Somebody at three sessions of one programme
          is one attendee, not three.
        </p>
        <ExportMenu
          query={filters.query}
          filtersApplied={filters_applied}
          options={[
            { path: 'records/no_show_rate', label: 'Enrollments behind the first stage' },
            { path: 'records/survey_response_rate', label: 'Attendance behind the second' },
          ]}
        />
      </div>

      <ExclusionBanner excluded={excluded_count} flagged={flagged_count} />

      <ol className="funnel">
        {steps.map((step) => (
          <li key={step.stage} className="funnel-step">
            <button
              type="button"
              className="funnel-bar"
              style={{ width: `${Math.max((step.count / widest) * 100, 12)}%` }}
              onClick={() => setOpened({ stage: step.stage, label: step.label })}
              title={`Show the ${step.count} people behind ${step.label.toLowerCase()}`}
            >
              <span className="funnel-label">{step.label}</span>
              <span className="funnel-count">{step.count.toLocaleString()}</span>
            </button>
            {step.drop_label && (
              <p className="funnel-drop">
                ↓ {step.drop_label}
                {step.drop_metric && (
                  <span className="muted"> · {step.drop_metric.title}</span>
                )}
              </p>
            )}
          </li>
        ))}
      </ol>

      <p className="muted funnel-note">
        {walk_ins === 0
          ? 'No walk-ins: everybody who attended had enrolled. The stages therefore subtract cleanly here, which is a fact about this data and not a rule.'
          : `${walk_ins.toLocaleString()} attended without ever enrolling. They are counted in the attended stage and are why it is not simply enrolled minus no-shows.`}
      </p>

      {opened && (
        <StageDrawer
          stage={opened.stage}
          label={opened.label}
          query={filters.query}
          onClose={() => setOpened(null)}
        />
      )}
    </>
  )
}
