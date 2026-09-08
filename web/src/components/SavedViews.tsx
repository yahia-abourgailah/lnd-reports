/**
 * Saved views: the filter set somebody rebuilds every month, named once.
 *
 * WHAT IS SAVED IS THE ADDRESS
 *
 * The path and the query string, because `filters.ts` already keeps the filter
 * state in the URL. Restoring a view is a navigation, so there is no second
 * representation of a filter set that could drift from the first — and a view
 * saved on the coverage screen returns to the coverage screen, which is what
 * "MarQ, this quarter, by department" actually meant.
 *
 * THE SERVER'S SENTENCE, NOT OURS
 *
 * Each row shows `describes`, which the API resolved from the parsed filters
 * when the view was saved. Rendering the query string instead would ask the
 * reader to decode `?sector=Finance&sector=Sales`, and building our own
 * sentence here would be a second phrasing of one fact — the failure the
 * envelope's `filters_applied` exists to avoid everywhere else.
 *
 * SHARED, AND OWNED
 *
 * Everyone's views are listed, the signed-in user's first, with the author on
 * anything that is not theirs. One permission set in v1: a view called "what we
 * present to the board" is worth more shared than hidden. Renaming and deleting
 * are the author's, so a shared list cannot be quietly rearranged.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { deleteView, getViews, saveView } from '../api'
import type { Filters } from '../filters'

/** The screens the API will accept a view for. Mirrors `PATHS` in views.py —
 *  a list saved against `/programs/12` would be a bookmark to one programme,
 *  which is a different thing and already has a URL. */
const SAVEABLE = new Set(['/', '/coverage', '/funnel', '/programs', '/trainers', '/learners'])

export function SavedViews({ filters }: { filters: Filters }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const views = useQuery({ queryKey: ['views'], queryFn: getViews })

  const save = useMutation({
    mutationFn: () => saveView(name.trim(), location.pathname, filters.query),
    onSuccess: async () => {
      setName('')
      setError(null)
      await queryClient.invalidateQueries({ queryKey: ['views'] })
    },
    // The server refuses a duplicate name with a 409 and an unparseable filter
    // with a 422. Both are things the person can fix, so both are said rather
    // than swallowed into a silent no-op.
    onError: () => setError('That name is already taken, or those filters could not be saved.'),
  })

  const remove = useMutation({
    mutationFn: (id: number) => deleteView(id),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ['views'] }),
  })

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

  const saveable = SAVEABLE.has(location.pathname)
  const rows = views.data?.views ?? []

  return (
    <div className="views" ref={box}>
      <button
        type="button"
        className="filter-button"
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
      >
        Views
        {rows.length > 0 && <span className="filter-count">{rows.length}</span>}
      </button>

      {open && (
        <div className="views-menu">
          {rows.length === 0 && (
            <p className="filter-empty">
              No saved views yet. Set the filters you use, then name them here.
            </p>
          )}

          {rows.map((view) => (
            <div className="views-row" key={view.id}>
              <button
                type="button"
                className="views-open"
                onClick={() => {
                  navigate(`${view.path}${view.query ? `?${view.query}` : ''}`)
                  setOpen(false)
                }}
              >
                <span className="views-name">{view.name}</span>
                <span className="views-describes">
                  {view.path} · {view.describes}
                </span>
                {!view.mine && <span className="views-owner">saved by {view.owner_email}</span>}
              </button>
              {view.mine && (
                <button
                  type="button"
                  className="views-delete"
                  aria-label={`Delete ${view.name}`}
                  onClick={() => remove.mutate(view.id)}
                >
                  ×
                </button>
              )}
            </div>
          ))}

          <form
            className="views-save"
            onSubmit={(event) => {
              event.preventDefault()
              if (name.trim()) save.mutate()
            }}
          >
            <label className="sr-only" htmlFor="view-name">
              Name for this view
            </label>
            <input
              id="view-name"
              value={name}
              placeholder={saveable ? 'Name this view…' : 'This screen cannot be saved'}
              disabled={!saveable}
              onChange={(event) => setName(event.target.value)}
            />
            <button type="submit" disabled={!saveable || !name.trim() || save.isPending}>
              Save
            </button>
          </form>

          {/* Said before saving, not discovered after. The bar reads the URL,
              so what gets stored is exactly the scope on screen right now. */}
          {saveable && (
            <p className="views-scope">
              Saves {location.pathname} with{' '}
              {filters.activeCount > 0
                ? `${filters.activeCount} filter${filters.activeCount === 1 ? '' : 's'}`
                : 'no filters'}
              .
            </p>
          )}
          {error && <p className="warn views-error">{error}</p>}
        </div>
      )}
    </div>
  )
}
