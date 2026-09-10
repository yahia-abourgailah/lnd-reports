/**
 * The reports that were published, kept as they were sent.
 *
 * WHY THIS SCREEN EXISTS AT ALL
 *
 * Everywhere else in this application, a number is the current answer. Here it
 * deliberately is not. A monthly report regenerated in October over August's
 * window is the *current* answer for August — enrichment decisions, a merged
 * trainer alias, CRM rows arriving late all move it, usually toward the truth.
 * That newer answer is the better one for deciding anything, and it is not the
 * file somebody has in their inbox. Once the moment passes, only one of those
 * two can still be produced, so it is the one that gets kept.
 *
 * WHAT THE DIGEST IS FOR
 *
 * It is a digest of the figures, not of the file. Every export writes its own
 * generation time into itself, so hashing the bytes would report two renderings
 * of an unchanged month as different every time and answer nothing. Equal
 * digests here mean the numbers did not move. A regeneration that matches the
 * newest edition is not stored at all, so every row on this screen is a real
 * change.
 *
 * ONE FORMAT, SO NO FORMAT COLUMN
 *
 * The month used to be published twice, as a workbook and as a PDF of the same
 * numbers, which put two rows here per month and left the reader to work out
 * which of them was the report. It is a PDF now, so the column that named the
 * format has nothing left to say.
 *
 * Editions from before that change are not listed. They are still stored and
 * still downloadable by id — nothing is deleted to make a screen tidier — but
 * a format this application no longer publishes does not belong in a list of
 * what it published.
 *
 * WHAT IS NOT HERE
 *
 * Ad-hoc downloads. A drill-through of named attendees is one person's
 * question; retaining every one would accumulate a second copy of the roster in
 * a table nobody audits, and would answer nothing this list is for.
 */

import { useQuery } from '@tanstack/react-query'

import { exportUrl, getEditions } from '../api'

const PUBLISHED = 'monthly_pdf'

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function when(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function Reports() {
  const editions = useQuery({ queryKey: ['editions'], queryFn: getEditions })

  if (editions.isPending) return <p className="muted">Fetching the editions…</p>
  if (editions.isError) return <p className="warn">The editions could not be listed.</p>
  if (!editions.data) return null

  const { editions: all, retained_per_period } = editions.data
  const rows = all.filter((row) => row.kind === PUBLISHED)
  // Summed over what is listed, not taken from the response. The server counts
  // every stored edition, and a size that includes rows this table does not
  // show is a total nobody can reconcile against what is in front of them.
  const bytes = rows.reduce((sum, row) => sum + row.byte_size, 0)

  // Grouped by the month covered, not the month generated. Two rows for August
  // are two editions of August, and putting them under one heading is what
  // makes that legible rather than confusing.
  const periods = [...new Set(rows.map((row) => row.period))]

  return (
    <>
      <div className="scope">
        <p className="scope-line">
          Each of these is a report <strong>as it was published</strong> — not a fresh answer for
          that month. Regenerating a month later gives the current figures, which is usually the
          better number and is never the file that was sent.
        </p>
        <span className="muted">
          {rows.length} kept · {size(bytes)} · newest {retained_per_period} per month
        </span>
      </div>

      {rows.length === 0 && (
        <p className="muted">
          Nothing published yet. Generating the monthly report from the Overview screen keeps an
          edition here.
        </p>
      )}

      {periods.map((period) => (
        <section className="group" key={period}>
          <div className="group-head">
            <h2>{period}</h2>
            <p className="muted">the month covered, not the month generated</p>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Generated</th>
                <th scope="col">By</th>
                <th scope="col">Scope</th>
                <th scope="col" className="r">
                  Size
                </th>
                <th scope="col">Figures</th>
                <th scope="col">
                  <span className="sr-only">Download</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows
                .filter((row) => row.period === period)
                .map((row) => (
                  <tr key={row.id}>
                    <td>{when(row.generated_at)}</td>
                    <td>
                      {/* Null means the schedule made it. Naming a service
                          account as the author would put a person's shape on
                          something no person did. */}
                      {row.generated_by ?? <span className="muted">on schedule</span>}
                    </td>
                    <td className="muted">{row.filters_applied}</td>
                    <td className="r num">{size(row.byte_size)}</td>
                    <td>
                      <code
                        className="digest"
                        title={`Figures digest ${row.figures_sha256} — equal digests mean the numbers did not move`}
                      >
                        {row.figures_sha256.slice(0, 8)}
                      </code>
                    </td>
                    <td>
                      <a href={exportUrl(`editions/${row.id}`, '')}>Download</a>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </section>
      ))}
    </>
  )
}
