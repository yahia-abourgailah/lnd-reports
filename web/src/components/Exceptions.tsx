/**
 * The exception console: what could not be placed, and what to do about it.
 *
 * LOSSES BEFORE NOTICES
 *
 * The queue is grouped by rule and ordered so the rules that cost figures come
 * first, whatever their size. A console sorted by count buries three
 * unresolvable identities under eleven capacity notices, and only the first
 * three are missing from anybody's numbers.
 *
 * Every group carries the rule's own description — what it detects, what it
 * costs, what fixes it. None of those sentences are written here: they come
 * from `lnd.quality.catalogue`, the same source the API and the tests read, for
 * the same reason a metric's definition travels with its value. A second copy
 * on the screen is a second thing to keep true, and it is the copy somebody
 * would act on.
 *
 * FIXING IS NOT CLOSING
 *
 * Nothing on this screen closes an exception. Five of the eleven rules are
 * fixed by authoring an enrichment row, and the button for those sends you to
 * that form; the row then disappears **on the next transform**, because the
 * rule is satisfied rather than because somebody ticked it off. That is what
 * makes the queue a picture of the present. A row closed by hand while the data
 * still violated the rule would reopen half an hour later looking like a new
 * problem.
 *
 * Dismissal is the one exception, and it is the only write here. It is a person
 * saying "yes, and that is fine" — a fact about the world no amount of
 * re-reading the payload can contradict — so a pass never reopens it, and it
 * demands a reason for exactly that reason.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { dismissException, getCompleteness, getExceptions } from '../api'
import type { DqException, DqRuleInfo } from '../api'

/** Where to go to author the fix. The overlay kinds are the enrichment
 *  screen's own tabs, so a rule that names one can hand somebody the form. */
const FIX_ROUTE: Record<string, string> = {
  identity_mapping: '/enrichment?kind=identity_mapping',
  program_override: '/enrichment?kind=program_override',
  trainer_alias: '/enrichment?kind=trainer_alias',
  survey_question: '/enrichment?kind=survey_question',
  survey_option_score: '/enrichment?kind=survey_option_score',
}

/** The link to the form that fixes this rule, where there is one.
 *
 *  Six of the eleven rules have no overlay: a duplicate scan is a fact about
 *  the CRM, a walk-in is how the session ran. Offering a form for those would
 *  be offering to paper over the source. */
function FixLink({ rule }: { rule: DqRuleInfo }) {
  const route = rule.fixed_by ? FIX_ROUTE[rule.fixed_by] : undefined
  if (!route) return null
  return <Link to={route}>Open the form →</Link>
}

function Dismiss({ row, rule }: { row: DqException; rule: DqRuleInfo }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const send = useMutation({
    mutationFn: () => dismissException(row.exception_key, reason.trim()),
    onSuccess: async () => {
      setOpen(false)
      setReason('')
      setError(null)
      await queryClient.invalidateQueries({ queryKey: ['exceptions'] })
    },
    onError: () => setError('That could not be dismissed. Say a little more about why.'),
  })

  if (!open) {
    return (
      <button type="button" className="linkish" onClick={() => setOpen(true)}>
        {rule.dismissal_is_normal ? 'Accept this' : 'Dismiss…'}
      </button>
    )
  }

  return (
    <form
      className="dismiss"
      onSubmit={(event) => {
        event.preventDefault()
        if (reason.trim().length >= 3) send.mutate()
      }}
    >
      <label className="sr-only" htmlFor={`why-${row.exception_key}`}>
        Why this is acceptable
      </label>
      <input
        id={`why-${row.exception_key}`}
        value={reason}
        placeholder="Why is this acceptable?"
        onChange={(event) => setReason(event.target.value)}
      />
      <button type="submit" disabled={reason.trim().length < 3 || send.isPending}>
        Dismiss
      </button>
      <button type="button" className="linkish" onClick={() => setOpen(false)}>
        Cancel
      </button>
      {/* Required, not optional. An exception dismissed with no stated reason is
          indistinguishable six months later from one dismissed by mistake. */}
      <p className="muted small">
        The reason is kept against your name, and no later pass reopens this.
      </p>
      {error && <p className="warn small">{error}</p>}
    </form>
  )
}

function Trend({ months }: { months: { period: string; excluded: number; flagged: number }[] }) {
  if (months.length === 0) return null
  const worst = Math.max(...months.map((m) => m.excluded + m.flagged), 1)

  return (
    <section className="group">
      <div className="group-head">
        <h2>Completeness by month</h2>
        <p className="muted">
          The months come from the data, not from a range somebody typed — a run of zeroes
          across months with no programmes would read as a collapse in delivery rather than an
          absence of it.
        </p>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Month</th>
            <th scope="col" className="r">
              Excluded
            </th>
            <th scope="col" className="r">
              Flagged
            </th>
            <th scope="col">
              <span className="sr-only">Share</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {months.map((month) => (
            <tr key={month.period}>
              <td className="num">{month.period}</td>
              <td className="num r">{month.excluded || '—'}</td>
              <td className="num r">{month.flagged || '—'}</td>
              <td className="bar-cell">
                {/* Nothing at all for a clean month. A minimum-width stub on
                    every zero row draws fifteen marks that mean nothing and
                    makes the two that do mean something harder to find. */}
                {month.excluded + month.flagged > 0 && (
                  <span
                    className={month.excluded > 0 ? 'bar bar-bad' : 'bar'}
                    style={{ width: `${((month.excluded + month.flagged) / worst) * 100}%` }}
                  />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

export function Exceptions() {
  const queue = useQuery({ queryKey: ['exceptions'], queryFn: () => getExceptions() })
  const trend = useQuery({ queryKey: ['completeness'], queryFn: () => getCompleteness() })

  if (queue.isPending) return <p className="muted">Reading the queue…</p>
  if (queue.isError) return <p className="warn">The exception queue could not be read.</p>
  if (!queue.data) return null

  const { groups, total_open, excluded, flagged, unplaceable } = queue.data

  return (
    <>
      <div className="scope">
        <p className="scope-line">
          Counted or excepted, never neither. Everything the platform could not place is here,
          with the rule that placed it here and what that costs.
        </p>
        <span className="muted">
          {total_open} open · {excluded} excluded from figures · {flagged} flagged and counted
          {unplaceable > 0 && ` · ${unplaceable} belong to no period`}
        </span>
      </div>

      {total_open === 0 && (
        <p className="muted">
          Nothing open. Every record the last pass read is either in the figures or accounted
          for.
        </p>
      )}

      {groups.map((group) => (
        <section className="group" key={group.rule.rule}>
          <div className="group-head">
            <h2>
              {group.rule.title}
              <span className={group.rule.costs_numbers ? 'tag tag-corrected' : 'tag tag-new'}>
                {group.rule.costs_numbers ? 'excluded from figures' : 'counted in full'}
              </span>
            </h2>
            <p className="muted">{group.rule.means}</p>
            <p className="muted">{group.rule.costs}</p>
            <p className="rule-fix">
              <strong>What to do:</strong> {group.rule.resolution} <FixLink rule={group.rule} />
            </p>
            <p className="muted small">
              {group.open_count} open · oldest {group.oldest_days} day
              {group.oldest_days === 1 ? '' : 's'}. Authoring the fix closes these on the next
              transform; nothing here closes them by hand.
            </p>
          </div>

          <table className="table">
            <thead>
              <tr>
                <th scope="col">What happened</th>
                <th scope="col">Programme</th>
                <th scope="col" className="r">
                  Age
                </th>
                <th scope="col" className="r">
                  Seen
                </th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {group.exceptions.map((row) => (
                <tr key={row.exception_key}>
                  <td>{row.summary}</td>
                  <td>
                    {row.crm_program_id ? (
                      <Link to={`/programs/${row.crm_program_id}`}>#{row.crm_program_id}</Link>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="num r">{row.age_days}d</td>
                  <td className="num r" title="How many passes have found this same violation">
                    {row.occurrences}
                  </td>
                  <td>
                    <Dismiss row={row} rule={group.rule} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}

      {trend.data && <Trend months={trend.data.months} />}
    </>
  )
}
