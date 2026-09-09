/**
 * The rows behind a number: virtualised, sortable, exportable in place.
 *
 * VIRTUALISED BY HAND, NOT BY LIBRARY
 *
 * Only the visible slice is in the DOM. 1,450 employees behind Participation
 * Rate is 1,450 rows the browser would otherwise lay out to show forty of
 * them, and the drawer opens on a click from a card — a second of jank there
 * reads as the number being expensive rather than the list being long.
 *
 * Roughly forty lines of arithmetic against a dependency: the windowing a
 * fixed-height table needs is a scroll offset and a slice, and a library would
 * be more code to read than the thing it replaces.
 *
 * SORTING IS CLIENT-SIDE, DELIBERATELY
 *
 * The rows are already here. Sorting on the server would mean a request per
 * click and a sorted page of a truncated list, which is a different and worse
 * thing: the first forty by name, not the first forty of the sorted whole.
 */

import { useMemo, useRef, useState } from 'react'

const ROW_HEIGHT = 33
const OVERSCAN = 6

type Row = Record<string, unknown>

function cell(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  return String(value)
}

/** Numbers sort as numbers, everything else as text, blanks always last.
 *
 * Without this `10` sorts before `9` — the same defect as `job_level_grade`
 * arriving as text, one layer up. */
function compare(a: unknown, b: unknown): number {
  if (a === null || a === undefined) return 1
  if (b === null || b === undefined) return -1
  const na = Number(a)
  const nb = Number(b)
  if (!Number.isNaN(na) && !Number.isNaN(nb) && String(a).trim() !== '' && String(b).trim() !== '') {
    return na - nb
  }
  return String(a).localeCompare(String(b))
}

function toCsv(columns: string[], rows: Row[]): string {
  const escape = (value: unknown) => {
    const text = value === null || value === undefined ? '' : String(value)
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
  }
  return [
    columns.join(','),
    ...rows.map((row) => columns.map((column) => escape(row[column])).join(',')),
  ].join('\n')
}

export function RecordGrid({
  columns,
  rows,
  filename,
  height = 380,
}: {
  columns: string[]
  rows: Row[]
  filename: string
  height?: number
}) {
  const [sort, setSort] = useState<{ column: string; ascending: boolean } | null>(null)
  const [offset, setOffset] = useState(0)
  const viewport = useRef<HTMLDivElement>(null)

  const ordered = useMemo(() => {
    if (!sort) return rows
    const sorted = [...rows].sort((a, b) => compare(a[sort.column], b[sort.column]))
    return sort.ascending ? sorted : sorted.reverse()
  }, [rows, sort])

  const first = Math.max(0, Math.floor(offset / ROW_HEIGHT) - OVERSCAN)
  const visible = Math.ceil(height / ROW_HEIGHT) + OVERSCAN * 2
  const window = ordered.slice(first, first + visible)

  function download() {
    // A real download, not a data: URI — this is served by nginx, and a blob
    // keeps a 1,450-row export out of the URL bar.
    const blob = new Blob([toCsv(columns, ordered)], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="grid-wrap">
      <div className="grid-tools">
        <span className="grid-count">
          {ordered.length.toLocaleString()} row{ordered.length === 1 ? '' : 's'}
          {sort && (
            <span className="muted">
              {' '}
              · sorted by {sort.column} {sort.ascending ? '↑' : '↓'}
            </span>
          )}
        </span>
        <button type="button" className="linkish" onClick={download}>
          Export CSV
        </button>
      </div>

      {/* A real table structure, not rows floating in a div. The virtualiser
          puts two positioning elements between the scroll container and the
          rows — a spacer that keeps the scrollbar honest and an absolutely
          placed slice — and an ARIA row must be owned by a rowgroup, so both
          are marked presentational and disappear from the tree. Without that
          every row reports "aria-required-parent" and a screen reader is handed
          a list of cells belonging to nothing. */}
      <div className="grid" role="table" aria-rowcount={ordered.length + 1}>
      <div className="grid-head" role="rowgroup">
      <div className="grid-headrow" role="row">
        {columns.map((column) => (
          <button
            key={column}
            type="button"
            role="columnheader"
            className={`grid-th${sort?.column === column ? ' grid-sorted' : ''}`}
            aria-sort={
              sort?.column === column ? (sort.ascending ? 'ascending' : 'descending') : 'none'
            }
            onClick={() =>
              setSort((was) =>
                was?.column === column
                  ? { column, ascending: !was.ascending }
                  : { column, ascending: true },
              )
            }
          >
            {column.replace(/_/g, ' ')}
            {sort?.column === column && <span aria-hidden="true"> {sort.ascending ? '↑' : '↓'}</span>}
          </button>
        ))}
      </div>
      </div>

      <div
        className="grid-body"
        role="rowgroup"
        // Focusable, because it scrolls. A region a mouse can scroll and a
        // keyboard cannot reach is the whole of `scrollable-region-focusable`,
        // and here it is the only way to see rows 41 onwards.
        tabIndex={0}
        aria-label="Records"
        ref={viewport}
        style={{ height }}
        onScroll={(event) => setOffset(event.currentTarget.scrollTop)}
      >
        {/* One tall spacer holds the scrollbar honest while only `window` is
            rendered — the scroll position must reflect the whole list, not the
            slice. */}
        <div
          role="presentation"
          style={{ height: ordered.length * ROW_HEIGHT, position: 'relative' }}
        >
          <div
            role="presentation"
            style={{ position: 'absolute', top: first * ROW_HEIGHT, left: 0, right: 0 }}
          >
            {window.map((row, index) => (
              <div className="grid-row" role="row" key={first + index}>
                {columns.map((column) => (
                  <span className="grid-td" role="cell" key={column} title={cell(row[column])}>
                    {cell(row[column])}
                  </span>
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>
      </div>
    </div>
  )
}
