/**
 * The workbook's five charts, redrawn.
 *
 * The class names carry a `c` prefix because `.bars`, `.bar-row`, `.bar-label`
 * and `.bar-track` were already taken by the coverage screen. Two rule sets
 * over one name is a cascade collision, and the first version of this file had
 * one: coverage's track background painted a grey strip behind every bar here.
 *
 * WHY THEY ARE HAND-DRAWN
 *
 * The same reason the record grid is: what these need is four shapes, and a
 * charting library is a dependency, a bundle and an API to learn in exchange
 * for shapes that are a rect and a path. The sparkline has been hand-drawn
 * since week 6 and has not been touched since.
 *
 * WHAT THEY HAVE THAT THE WORKBOOK'S DID NOT
 *
 * A scale that every label names. The workbook's bars carry a number printed
 * beside each one and no axis at all, so two charts side by side with different
 * maxima look like the same magnitude. Each of these states its own maximum,
 * and every bar is drawn against it.
 *
 * And a sample. `Internal vs External` in the workbook reads 3 and 22 with a
 * `Grand Total` slice of 25 sitting in the same pie — a total drawn as if it
 * were a third category, which is why that pie appears to have three parts
 * when it describes two. This one draws the two, and states the total once.
 *
 * COLOUR
 *
 * The workbook's own — green #92D050 and its blue, lightened to clear AA on the
 * navy these are drawn on. Marks take `--series` and `--series-2`; nothing here
 * picks a colour of its own.
 */

/** A number formatted the way the rest of the dashboard formats numbers. */
const n = (value: number) => value.toLocaleString()

function Empty({ label }: { label: string }) {
  return <p className="chart-empty">{label}</p>
}

/* ------------------------------------------------------------------- donut */
export function Donut({
  parts,
  total,
  caption,
}: {
  parts: { label: string; value: number; tone: 'a' | 'b' }[]
  total: number
  caption?: string
}) {
  const sum = parts.reduce((carry, part) => carry + part.value, 0)
  if (sum === 0) return <Empty label="Nothing in scope." />

  // A ring rather than a pie: the hole is where the total goes, so the total
  // is stated once instead of being drawn as a third slice the way the
  // workbook's was.
  const R = 54
  const C = 2 * Math.PI * R
  let offset = 0

  return (
    <div className="donut">
      <svg viewBox="0 0 140 140" role="img" aria-label={caption ?? 'Share'}>
        <g transform="translate(70 70) rotate(-90)">
          {parts.map((part) => {
            const length = (part.value / sum) * C
            const dash = `${length} ${C - length}`
            const mark = (
              <circle
                key={part.label}
                r={R}
                fill="none"
                stroke={part.tone === 'a' ? 'var(--series)' : 'var(--series-2)'}
                strokeWidth="20"
                strokeDasharray={dash}
                strokeDashoffset={-offset}
              />
            )
            offset += length
            return mark
          })}
        </g>
        <text x="70" y="66" className="donut-total" textAnchor="middle">
          {n(total)}
        </text>
        <text x="70" y="82" className="donut-total-label" textAnchor="middle">
          in total
        </text>
      </svg>
      <ul className="chart-key">
        {parts.map((part) => (
          <li key={part.label}>
            <span className={`key-swatch key-${part.tone}`} />
            {part.label}
            <b>{n(part.value)}</b>
          </li>
        ))}
      </ul>
    </div>
  )
}

/* ------------------------------------------------------------ grouped bars */
export interface Group {
  label: string
  /** Printed once, under the column where it changes. */
  year?: string
  values: number[]
}

export function GroupedBars({
  groups,
  series,
  caption,
}: {
  groups: Group[]
  series: string[]
  caption?: string
}) {
  if (groups.length === 0) return <Empty label="No months in scope." />
  const top = Math.max(...groups.flatMap((group) => group.values), 1)

  return (
    <div className="grouped">
      <ul className="chart-key">
        {series.map((name, index) => (
          <li key={name}>
            <span className={`key-swatch key-${['a', 'b', 'c'][index]}`} />
            {name}
          </li>
        ))}
      </ul>
      {/* Focusable, because it scrolls. A region a mouse can pan and a keyboard
          cannot reach is content nobody navigating by keyboard can see — the
          accessibility pass called it, and it was right: fifteen months do not
          fit in a third of the board. `role="group"` rather than `img`, since
          the bars inside carry their own titles. */}
      <div
        className="grouped-plot"
        role="group"
        tabIndex={0}
        aria-label={caption ?? 'By month'}
      >
        {groups.map((group, index) => (
          <div className="grouped-col" key={group.label + (group.year ?? '')}>
            <div className="grouped-bars">
              {group.values.map((value, index) => (
                <div
                  className={`gbar gbar-${['a', 'b', 'c'][index]}`}
                  key={series[index]}
                  style={{ height: `${(value / top) * 100}%` }}
                  title={`${group.label} · ${series[index]}: ${n(value)}`}
                >
                  <span>{value ? n(value) : ''}</span>
                </div>
              ))}
            </div>
            <span className="grouped-label">
              {group.label}
              {group.year && group.year !== groups[index - 1]?.year && (
                <em>{group.year.slice(2)}</em>
              )}
            </span>
          </div>
        ))}
      </div>
      {/* The workbook printed a number beside every bar and drew no axis at
          all, so two charts of different magnitudes read as the same size.
          Stating the maximum is the cheapest way to stop that. */}
      <p className="chart-scale">tallest bar = {n(top)}</p>
    </div>
  )
}

/* --------------------------------------------------------- horizontal bars */
export interface Row {
  label: string
  values: number[]
  href?: string
}

export function Bars({
  rows,
  series,
  caption,
  limit = 12,
}: {
  rows: Row[]
  series: string[]
  caption?: string
  limit?: number
}) {
  if (rows.length === 0) return <Empty label="Nothing in scope." />
  const shown = rows.slice(0, limit)
  const top = Math.max(...rows.flatMap((row) => row.values), 1)

  return (
    <div className="cbars">
      {series.length > 1 && (
        <ul className="chart-key">
          {series.map((name, index) => (
            <li key={name}>
              <span className={`key-swatch key-${['a', 'b'][index]}`} />
              {name}
            </li>
          ))}
        </ul>
      )}
      <div role="img" aria-label={caption ?? 'Ranked'}>
        {shown.map((row) => (
          <div className="cbar-row" key={row.label}>
            <span className="cbar-name" title={row.label}>
              {row.label}
            </span>
            <span className="cbar-track">
              {row.values.map((value, index) => (
                <span
                  className={`cbar cbar-${['a', 'b'][index]}`}
                  key={series[index]}
                  style={{ width: `${(value / top) * 100}%` }}
                  title={`${row.label} · ${series[index]}: ${n(value)}`}
                >
                  <b>{n(value)}</b>
                </span>
              ))}
            </span>
          </div>
        ))}
      </div>
      <p className="chart-scale">
        longest bar = {n(top)}
        {rows.length > shown.length && ` · showing the top ${shown.length} of ${rows.length}`}
      </p>
    </div>
  )
}
