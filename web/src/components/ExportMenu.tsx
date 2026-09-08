/**
 * Export this view, as it is on screen.
 *
 * One component on every view rather than a button written per screen. The
 * filters come from the same hook the view reads, so an export cannot silently
 * cover a different population from the page above it — which is the failure
 * that matters here: a spreadsheet is quoted later, by someone who was not in
 * the room when the filters were set.
 *
 * These are links, not fetches. The browser downloads them directly, the
 * session cookie travels with the request, and the filename the server sets in
 * Content-Disposition survives. Fetching to a blob would put a 1,450-row XLSX
 * through JavaScript memory to achieve the same result and lose the name.
 *
 * Every file the server produces carries its provenance in the first rows:
 * generated at, filters applied, freshness, excluded and flagged counts, and
 * the definition of every figure in it. Nothing here needs to add that, and
 * nothing here should — a second implementation of the stamp would be a second
 * thing to keep true.
 */

import { useEffect, useRef, useState } from 'react'

import { exportUrl } from '../api'

export interface ExportOption {
  /** Path under /v1/exports, without the extension — e.g. `kpis`. */
  path: string
  label: string
  /** Only xlsx for the monthly report; both for everything else. */
  formats?: ('csv' | 'xlsx')[]
  /** Said out loud when a file covers something narrower than the view. */
  note?: string
}

export function ExportMenu({
  query,
  options,
  filtersApplied,
}: {
  query: string
  options: ExportOption[]
  /** The server's own sentence for the active filters, echoed here so the
   *  reader sees what the file will say before opening it. */
  filtersApplied?: string
}) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const away = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => event.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  return (
    <div className="export" ref={box}>
      <button
        type="button"
        className="export-button"
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
      >
        Export
      </button>

      {open && (
        <div className="export-menu" role="menu">
          <p className="export-scope">
            {filtersApplied ?? 'the view as it is on screen'}
          </p>
          {options.map((option) => (
            <div className="export-row" key={option.path}>
              <span className="export-label">
                {option.label}
                {option.note && <span className="export-note">{option.note}</span>}
              </span>
              <span className="export-formats">
                {(option.formats ?? ['csv', 'xlsx']).map((fmt) => (
                  <a
                    key={fmt}
                    role="menuitem"
                    href={exportUrl(`${option.path}.${fmt}`, query)}
                    onClick={() => setOpen(false)}
                  >
                    {fmt.toUpperCase()}
                  </a>
                ))}
              </span>
            </div>
          ))}
          <p className="export-stamp">
            Every file states when it was generated, what it was filtered to, how fresh the
            data was, how many records were excluded, and what each figure means.
          </p>
        </div>
      )}
    </div>
  )
}
