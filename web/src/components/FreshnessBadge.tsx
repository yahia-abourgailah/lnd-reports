/**
 * How old the data is. On every screen, from the shell rather than per-page.
 *
 * The platform serves last-known-good when a source is down (NFR-03), which is
 * the right behaviour and is indistinguishable from healthy unless something
 * says so. Putting the badge in the shell means a new screen cannot forget it —
 * the failure mode of a per-page badge is that the page somebody built in a
 * hurry is the page that shows stale numbers silently.
 *
 * It never blocks. A stale dashboard is still a dashboard; an empty one because
 * freshness could not be read would be worse than an old figure honestly
 * labelled.
 */

import type { Freshness } from '../api'

/** The worst lag across every entity, which is what the badge must show.
 *
 * The platform is only as current as its most behind entity: one source
 * syncing every thirty minutes does not make the dashboard fresh if another
 * has never run. An entity that has never succeeded has no lag at all, and
 * that is a stronger statement than a large number — it is reported as such
 * rather than as an enormous one.
 */
function worstLag(freshness: Freshness): number | null {
  const lags = freshness.sources
    .flatMap((source) => source.entities)
    .map((entity) => entity.lag_seconds)
  if (lags.length === 0 || lags.some((lag) => lag === null)) return null
  return Math.max(...(lags as number[]))
}

function ago(seconds: number | null): string {
  if (seconds === null) return 'never'
  if (seconds < 90) return 'just now'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours} h ago`
  return `${Math.round(hours / 24)} days ago`
}

const WORDING: Record<Freshness['status'], { label: string; tone: string }> = {
  ok: { label: 'Up to date', tone: 'ok' },
  stale: { label: 'Data is behind', tone: 'warn' },
  never_synced: { label: 'Never synced', tone: 'bad' },
}

export function FreshnessBadge({ freshness }: { freshness: Freshness | undefined }) {
  if (!freshness) {
    // Not an error state. The badge simply has nothing to say yet, and a
    // flashing "unknown" on every navigation would train people to ignore it.
    return <span className="badge" aria-hidden="true" />
  }

  const { label, tone } = WORDING[freshness.status]
  const lag = ago(worstLag(freshness))
  const threshold = Math.round(freshness.stale_after_seconds / 60)

  return (
    <span
      className={`badge badge-${tone}`}
      title={
        freshness.status === 'ok'
          ? `Last successful sync ${lag}. Counts as behind after ${threshold} minutes.`
          : `Last successful sync ${lag}. The platform is still serving the last good data.`
      }
    >
      <span className="badge-dot" aria-hidden="true" />
      {label}
      <span className="badge-lag">{lag}</span>
    </span>
  )
}
