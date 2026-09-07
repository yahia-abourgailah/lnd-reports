/**
 * One figure, and everything a reader needs to trust it.
 *
 * The tooltip is not decoration. Nine of these KPIs lived inside GETPIVOTDATA
 * strings nobody could read, and six were wrong — so the definition, the
 * population and what it excludes travel with the number and are one hover
 * away. A figure whose definition is somewhere else is a figure two people can
 * argue about while both being right.
 *
 * Three things are shown without asking:
 *
 *   the sample it rests on — 98% over 297 responses and 98% over 4 are
 *   different claims, and only this distinguishes them.
 *
 *   whether it changed — anything corrected or restated carries a mark, so the
 *   figures L&D has to be walked through are visible before the meeting.
 *
 *   an em dash, never a zero, when there is no value. Zero is a measurement.
 */

import { useId, useState } from 'react'

import type { Metric } from '../api'

const PROVENANCE_LABEL: Record<Metric['provenance'], string | null> = {
  unchanged: null,
  new: 'New',
  renamed: 'Renamed',
  restated: 'Restated',
  corrected: 'Corrected',
}

function sampleWording(metric: Metric): string {
  if (metric.numerator !== null && metric.denominator !== null) {
    return `${Number(metric.numerator).toLocaleString()} of ${Number(
      metric.denominator,
    ).toLocaleString()}`
  }
  return `${metric.sample_size.toLocaleString()} rows`
}

export function KpiCard({ metric }: { metric: Metric }) {
  const [open, setOpen] = useState(false)
  const tooltipId = useId()
  const changed = PROVENANCE_LABEL[metric.provenance]

  return (
    <article className={`kpi${metric.value === null ? ' kpi-empty' : ''}`}>
      <header className="kpi-head">
        <h3>{metric.title}</h3>
        <button
          type="button"
          className="kpi-info"
          aria-expanded={open}
          aria-controls={tooltipId}
          onClick={() => setOpen((was) => !was)}
        >
          <span aria-hidden="true">i</span>
          <span className="sr-only">What does {metric.title} mean?</span>
        </button>
      </header>

      <p className={`kpi-value kpi-${metric.unit}`}>{metric.formatted}</p>

      <p className="kpi-sample">
        {metric.value === null ? 'no measurement yet' : sampleWording(metric)}
        {metric.is_estimated && <span className="kpi-flag"> · estimated</span>}
      </p>

      {changed && <span className={`tag tag-${metric.provenance}`}>{changed}</span>}

      {open && (
        <div className="kpi-tip" id={tooltipId} role="region" aria-label={`${metric.title} definition`}>
          <p className="kpi-tip-definition">{metric.definition}</p>
          <p className="kpi-tip-population">
            <strong>Counted over</strong> {metric.population}
          </p>
          {metric.excludes.length > 0 && (
            <ul className="kpi-tip-excludes">
              {metric.excludes.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
          {metric.note && <p className="kpi-tip-note">{metric.note}</p>}
        </div>
      )}
    </article>
  )
}
