/**
 * The drawer that opens when somebody clicks a number.
 *
 * It shows the figure and the rows it is made of on one screen, so a
 * disagreement between them is visible rather than something you would have to
 * hold in your head across two requests. That is the whole point: the
 * workbook's numbers were unauditable because the question was unreadable, and
 * six of the nine turned out to need reading.
 */

import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'

import { getDrill } from '../api'
import { RecordGrid } from './RecordGrid'

export function DrillDrawer({
  metricKey,
  query,
  onClose,
}: {
  metricKey: string
  query: string
  onClose: () => void
}) {
  const drill = useQuery({
    queryKey: ['drill', metricKey, query],
    queryFn: () => getDrill(metricKey, query),
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
        aria-label={`Records behind ${drill.data?.metric.title ?? metricKey}`}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="drawer-head">
          <div>
            <h2>{drill.data?.metric.title ?? metricKey}</h2>
            <p className="muted">{drill.data?.filters_applied}</p>
          </div>
          <button type="button" className="drawer-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        {drill.isPending && <p className="muted drawer-pad">Fetching the records…</p>}
        {drill.isError && <p className="warn drawer-pad">Those records could not be fetched.</p>}

        {drill.data && (
          <>
            <div className="drawer-figure">
              <span className="drawer-value">{drill.data.metric.formatted}</span>
              <span className="drawer-grain">
                over {drill.data.total.toLocaleString()} {drill.data.grain} row
                {drill.data.total === 1 ? '' : 's'}
              </span>
            </div>

            {drill.data.truncated && (
              <p className="drawer-truncated">
                Showing the first {drill.data.returned.toLocaleString()} of{' '}
                {drill.data.total.toLocaleString()}. Narrow the filters to see the rest — the
                export covers what is shown.
              </p>
            )}

            <RecordGrid
              columns={drill.data.columns}
              rows={drill.data.rows}
              filename={`${metricKey}.csv`}
            />
          </>
        )}
      </aside>
    </div>
  )
}
