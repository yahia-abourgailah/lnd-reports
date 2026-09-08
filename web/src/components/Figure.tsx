/**
 * One figure on a scorecard: compact, and still honest.
 *
 * The KPI card is the front page's version of this — bigger, with a trend and
 * a drill-through. A scorecard shows a dozen at once, so this one drops the
 * chart and keeps the two things that cannot be dropped: the sample the figure
 * rests on, and its definition one click away.
 *
 * 98% over 297 responses and 98% over 4 are different claims. A scorecard for a
 * programme with seven responses is mostly the second kind, which is exactly
 * why the sample travels with the number here rather than only on the
 * dashboard.
 */

import { useId, useState } from 'react'

import type { Metric } from '../api'

function sample(metric: Metric): string {
  if (metric.value === null) return 'no measurement'
  if (metric.numerator !== null && metric.denominator !== null) {
    return `${Number(metric.numerator).toLocaleString()} of ${Number(
      metric.denominator,
    ).toLocaleString()}`
  }
  return `over ${metric.sample_size.toLocaleString()} rows`
}

export function Figure({ metric }: { metric: Metric }) {
  const [open, setOpen] = useState(false)
  const tipId = useId()

  return (
    <article className={`figure${metric.value === null ? ' figure-empty' : ''}`}>
      <header className="figure-head">
        <h4>{metric.title}</h4>
        <button
          type="button"
          className="kpi-info"
          aria-expanded={open}
          aria-controls={tipId}
          onClick={() => setOpen((was) => !was)}
        >
          <span aria-hidden="true">i</span>
          <span className="sr-only">What does {metric.title} mean?</span>
        </button>
      </header>

      <p className={`figure-value kpi-${metric.unit}`}>{metric.formatted}</p>
      <p className="figure-sample">{sample(metric)}</p>

      {open && (
        <div className="kpi-tip" id={tipId} role="region" aria-label={`${metric.title} definition`}>
          <p className="kpi-tip-definition">{metric.definition}</p>
          <p className="kpi-tip-population">
            <strong>Counted over</strong> {metric.population}
          </p>
          {metric.note && <p className="kpi-tip-note">{metric.note}</p>}
        </div>
      )}
    </article>
  )
}

export function Figures({ metrics, caption }: { metrics: Metric[]; caption?: string }) {
  if (metrics.length === 0) return null
  return (
    <>
      {caption && <p className="muted figures-caption">{caption}</p>}
      <div className="figure-grid">
        {metrics.map((metric) => (
          <Figure key={metric.key} metric={metric} />
        ))}
      </div>
    </>
  )
}
