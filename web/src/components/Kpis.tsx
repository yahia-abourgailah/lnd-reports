/**
 * The KPI summary — the platform's front page.
 *
 * Grouped by provenance rather than by subject, and that is the week-4
 * conversation made visible: the figures somebody has to be walked through are
 * together at the top, and the ones that did not move are together at the
 * bottom where they need no explanation.
 *
 * A metric absent under the current filters is absent, not blank. Asked for one
 * trainer, Participation Rate has no population and does not appear — its
 * absence is the honest answer to a question that has none.
 */

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { getKpis, type Metric } from '../api'
import type { Filters } from '../filters'
import { DrillDrawer } from './DrillDrawer'
import { ExclusionBanner } from './ExclusionBanner'
import { ExportMenu } from './ExportMenu'
import { KpiCard } from './KpiCard'

const GROUPS: { provenance: Metric['provenance'][]; title: string; note: string }[] = [
  {
    provenance: ['corrected'],
    title: 'Corrected',
    note: 'The published figure was wrong. Walk L&D through each of these.',
  },
  {
    provenance: ['restated', 'renamed'],
    title: 'Restated',
    note: 'Same intent, a definition that now says exactly what it counts.',
  },
  { provenance: ['new'], title: 'New', note: 'The workbook did not have these.' },
  {
    provenance: ['unchanged'],
    title: 'Unchanged',
    note: 'Same definition, same number. Nothing to explain.',
  },
]

export function Kpis({ filters }: { filters: Filters }) {
  const [drilling, setDrilling] = useState<string | null>(null)
  const kpis = useQuery({
    queryKey: ['kpis', filters.query],
    queryFn: () => getKpis(filters.query),
  })

  if (kpis.isPending) return <p className="muted">Computing…</p>
  if (kpis.isError) return <p className="warn">The metrics could not be computed.</p>
  if (!kpis.data) return null

  const { metrics, excluded_count, flagged_count, filters_applied, dimensions_filtered } =
    kpis.data

  return (
    <>
      <div className="scope">
        <p className="scope-line">
          <strong>{metrics.length}</strong> metrics over {filters_applied}
          {dimensions_filtered.length > 0 && (
            <span className="scope-filtered"> · filtered by {dimensions_filtered.join(', ')}</span>
          )}
        </p>
        {/* The monthly report is offered here and nowhere else. It is the one
            export that ignores the filter bar — it covers a calendar month by
            definition — so it belongs beside the figures it summarises rather
            than on a screen where somebody has just narrowed to one sector and
            would reasonably expect the file to match. */}
        <ExportMenu
          query={filters.query}
          filtersApplied={filters_applied}
          options={[
            {
              path: 'kpis',
              label: 'These figures, with definitions',
              formats: ['csv', 'xlsx', 'pdf'],
            },
            {
              path: 'monthly',
              label: 'Monthly report',
              formats: ['xlsx', 'pdf'],
              note: 'last complete month — ignores the filter bar · kept as an edition',
            },
          ]}
        />
      </div>

      <ExclusionBanner excluded={excluded_count} flagged={flagged_count} />

      {GROUPS.map((group) => {
        const shown = metrics.filter((m) => group.provenance.includes(m.provenance))
        if (shown.length === 0) return null
        return (
          <section key={group.title} className="group">
            <div className="group-head">
              <h2>{group.title}</h2>
              <p className="muted">{group.note}</p>
            </div>
            <div className="kpi-grid">
              {shown.map((metric) => (
                <KpiCard
                  key={metric.key}
                  metric={metric}
                  query={filters.query}
                  onDrill={setDrilling}
                />
              ))}
            </div>
          </section>
        )
      })}

      {drilling && (
        <DrillDrawer
          metricKey={drilling}
          query={filters.query}
          onClose={() => setDrilling(null)}
        />
      )}
    </>
  )
}
