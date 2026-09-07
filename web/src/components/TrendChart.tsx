/**
 * One KPI, month by month.
 *
 * A sparkline rather than a full chart: the job is "is this going up or down",
 * beside a number that already says where it stands. Axes and a legend would
 * cost more space than the answer is worth, and a single series needs no
 * legend — the card title names it.
 *
 * Each point is that month alone, not a running total. A cumulative line rises
 * forever and says nothing about whether March was better than February.
 *
 * Months with no data are gaps, not zeros. A programme ran in February and
 * nothing ran in March; drawing March at zero would show a collapse in
 * satisfaction that did not happen.
 */

import { useQuery } from '@tanstack/react-query'
import { useId, useState } from 'react'

import { getTrend, type Slice } from '../api'

const WIDTH = 200
const HEIGHT = 44
const PAD = 4

interface Point {
  x: number
  y: number
  label: string
  formatted: string
  value: number
}

function layout(points: Slice[]): { plotted: Point[]; low: number; high: number } {
  const withValues = points
    .map((p, index) => ({ index, slice: p, value: p.metric.value === null ? null : Number(p.metric.value) }))
    .filter((p): p is { index: number; slice: Slice; value: number } => p.value !== null)

  if (withValues.length === 0) return { plotted: [], low: 0, high: 0 }

  const values = withValues.map((p) => p.value)
  const low = Math.min(...values)
  const high = Math.max(...values)
  // A flat series would divide by zero and, worse, draw at the top of the box
  // as if it were a maximum. Centred instead, which is what "unchanged" looks
  // like.
  const span = high - low || 1
  const step = points.length > 1 ? (WIDTH - PAD * 2) / (points.length - 1) : 0

  return {
    low,
    high,
    plotted: withValues.map((p) => ({
      x: PAD + p.index * step,
      y: high === low
        ? HEIGHT / 2
        : PAD + (HEIGHT - PAD * 2) * (1 - (p.value - low) / span),
      label: p.slice.label,
      formatted: p.slice.metric.formatted,
      value: p.value,
    })),
  }
}

export function TrendChart({ metricKey, query }: { metricKey: string; query: string }) {
  const gradientId = useId()
  const [hover, setHover] = useState<Point | null>(null)
  const trend = useQuery({
    queryKey: ['trend', metricKey, query],
    queryFn: () => getTrend(metricKey, query),
    // A trend is a second request per card. Kept longer than the KPI itself
    // because it moves only when the transform runs.
    staleTime: 5 * 60_000,
  })

  if (trend.isPending) return <div className="spark spark-idle" aria-hidden="true" />
  if (trend.isError || !trend.data) return <div className="spark spark-idle" aria-hidden="true" />

  const { plotted } = layout(trend.data.points)
  if (plotted.length < 2) {
    return (
      <p className="spark-none">
        {plotted.length === 1 ? 'one month of data' : 'no monthly history'}
      </p>
    )
  }

  // Narrowed rather than asserted: `plotted.length >= 2` is checked above, but
  // noUncheckedIndexedAccess does not carry that through an index expression.
  const first = plotted[0]
  const last = plotted[plotted.length - 1]
  if (!first || !last) return null

  const line = plotted.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ')
  const area = `${line} L ${last.x} ${HEIGHT} L ${first.x} ${HEIGHT} Z`
  const shown = hover ?? last

  return (
    <figure className="spark">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`${trend.data.points.length} months, from ${first.formatted} in ${first.label} to ${last.formatted} in ${last.label}`}
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--series)" stopOpacity="0.22" />
            <stop offset="100%" stopColor="var(--series)" stopOpacity="0" />
          </linearGradient>
        </defs>

        <path d={area} fill={`url(#${gradientId})`} />
        <path
          d={line}
          fill="none"
          stroke="var(--series)"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {/* The endpoint is where the eye lands, so it is the one emphasised. */}
        <circle cx={last.x} cy={last.y} r="3" fill="var(--series)" />
        {hover && <circle cx={hover.x} cy={hover.y} r="4" fill="var(--series)" stroke="var(--surface)" strokeWidth="2" />}

        {/* Hit targets wider than the marks, so a 2px line is still hoverable. */}
        {plotted.map((p) => (
          <rect
            key={p.label}
            x={p.x - 6}
            y={0}
            width={12}
            height={HEIGHT}
            fill="transparent"
            onMouseEnter={() => setHover(p)}
          />
        ))}
      </svg>
      <figcaption className="spark-caption">
        <span className="spark-month">{shown.label}</span>
        <span className="spark-value">{shown.formatted}</span>
      </figcaption>
    </figure>
  )
}
