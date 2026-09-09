/**
 * The enrichment screen: the decisions people layer over what the CRM says.
 *
 * Every row here is a judgement — which spelling is one trainer, which
 * department a customised programme was built for — so the screen leads with
 * who decided and why, not just with the value. A judgement without its
 * reasoning is one nobody can revisit.
 *
 * Nothing is edited in place. Saving supersedes and inserts, so "what was this
 * before" and "why was it changed" stay answerable; the history is one click
 * away on every row.
 *
 * A saved change corrects the dashboard's cache immediately but `core` still
 * carries the old value until the transform runs, twice an hour. That is said
 * on screen rather than left for somebody to discover by refreshing.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import {
  getEnrichmentForms,
  getOverlay,
  getOverlayHistory,
  putOverlay,
  retireOverlay,
  type FormField,
  type OverlayEntry,
  type OverlayForm,
} from '../api'

const KINDS: { kind: string; label: string; blurb: string; keyFields: string[]; valueFields: string[] }[] = [
  {
    kind: 'trainer_alias',
    label: 'Trainer names',
    blurb:
      'The CRM stores the trainer as free text on each session — the only person in the payload without a key. 16 spellings cover 123 sessions, and two of them are one man.',
    keyFields: ['normalised_name'],
    valueFields: ['trainer_key'],
  },
  {
    kind: 'program_override',
    label: 'Programme overrides',
    blurb:
      'Where the CRM has no answer or the wrong one. The source keeps saying what it says; this wins at one point in the transform.',
    keyFields: ['crm_program_id', 'field'],
    valueFields: ['value'],
  },
  {
    kind: 'survey_question',
    label: 'Survey questions',
    blurb:
      'Which of the five measured dimensions each question asks about. Nothing in the payload says question 5 is the logistics metric — somebody read the wording and decided.',
    keyFields: ['crm_survey_id', 'crm_question_id'],
    valueFields: ['dimension', 'scale_min', 'scale_max', 'question_title'],
  },
  {
    kind: 'identity_mapping',
    label: 'Identity mappings',
    blurb: 'One person appearing under two ids. Empty is the healthy state.',
    keyFields: ['source_odoo_id'],
    valueFields: ['target_odoo_id'],
  },
]

function describe(record: Record<string, unknown>): string {
  return Object.entries(record)
    .map(([name, value]) => `${name.replace(/_/g, ' ')}: ${String(value)}`)
    .join(' · ')
}

function History({ kind, entry }: { kind: string; entry: OverlayEntry }) {
  const past = useQuery({
    queryKey: ['overlay-history', kind, entry.key],
    queryFn: () => getOverlayHistory(kind, entry.key),
  })

  if (past.isPending) return <p className="muted enrich-history">Loading history…</p>
  if (!past.data) return null

  return (
    <ol className="enrich-history">
      {past.data.entries.map((version) => (
        <li key={version.id} className={version.is_live ? 'enrich-live' : ''}>
          <span className="enrich-hvalue">{describe(version.values)}</span>
          <span className="enrich-hmeta">
            {version.authored_by} · {new Date(version.authored_at).toLocaleString()}
            {version.is_live ? ' · in force' : ' · superseded'}
          </span>
          {version.note && <span className="enrich-hnote">{version.note}</span>}
        </li>
      ))}
    </ol>
  )
}

function Table({
  kind,
  label,
  blurb,
  asked,
  form,
}: (typeof KINDS)[number] & { asked: boolean; form?: OverlayForm }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState<number | null>(null)
  const section = useRef<HTMLElement>(null)

  // The exception console links here with `?kind=`, naming the one form that
  // fixes the rule somebody was reading about. Without this the link dropped
  // them at the top of a page with four forms on it and no indication which —
  // which is a link that technically works and practically does not.
  useEffect(() => {
    if (asked && section.current) {
      section.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [asked])
  const entries = useQuery({ queryKey: ['overlay', kind], queryFn: () => getOverlay(kind) })

  const withdraw = useMutation({
    mutationFn: (entry: OverlayEntry) =>
      retireOverlay(kind, entry.key, 'withdrawn from the enrichment screen'),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['overlay', kind] })
      // The overlay is an input to every figure, so the dashboard is stale too.
      await queryClient.invalidateQueries({ queryKey: ['kpis'] })
    },
  })

  const rows = entries.data?.entries ?? []

  return (
    <section className={asked ? 'enrich enrich-asked' : 'enrich'} ref={section}>
      <header className="enrich-head">
        <h2>{label}</h2>
        {asked && <p className="enrich-asked-note">This is the form you were sent to.</p>}
        <p className="muted">{blurb}</p>
      </header>

      {entries.isPending && <p className="muted">Loading…</p>}
      {rows.length === 0 && !entries.isPending && (
        <p className="enrich-empty">Nothing overridden. Every value comes from the CRM.</p>
      )}

      {rows.map((entry) => (
        <div className="enrich-row" key={entry.id}>
          <div className="enrich-main">
            <p className="enrich-key">{describe(entry.key)}</p>
            <p className="enrich-value">{describe(entry.values)}</p>
            <p className="enrich-meta">
              {entry.authored_by} · {new Date(entry.authored_at).toLocaleDateString()}
              {entry.note && <span className="enrich-note"> — {entry.note}</span>}
            </p>
          </div>
          <div className="enrich-actions">
            <button
              type="button"
              className="linkish"
              onClick={() => setOpen((was) => (was === entry.id ? null : entry.id))}
            >
              {open === entry.id ? 'Hide history' : 'History'}
            </button>
            <button
              type="button"
              className="linkish enrich-retire"
              onClick={() => withdraw.mutate(entry)}
              disabled={withdraw.isPending}
            >
              Withdraw
            </button>
          </div>
          {open === entry.id && <History kind={kind} entry={entry} />}
        </div>
      ))}

      {form && <AuthorForm form={form} />}
    </section>
  )
}

/**
 * The form that authors one decision.
 *
 * WHY IT IS DESCRIBED BY THE SERVER
 *
 * This used to be one generic form for all five overlays: a box for the column
 * name, a box for the keys, and a box where you hand-wrote JSON. It worked, and
 * only for whoever built it — the person who actually needs it is a specialist
 * looking at "Programme 77 has no trainer" and wanting to say who the trainer
 * was. They should not have to know that the column is `crm_program_id`.
 *
 * So `/v1/enrichment/forms` says what each form asks for, in words, with its
 * options read from the data: which programmes exist, which trainers, which of
 * the five dimensions a survey question can measure. Nothing about the shape of
 * these forms is written here. A list typed into a front end goes stale
 * silently, and the first symptom is a value somebody cannot select.
 *
 * ONE AT A TIME, EXCEPT WHERE MANY IS THE POINT
 *
 * Only trainer spellings support several keys at once — sixteen spellings, one
 * of which is a duplicate of another, and doing that one at a time invites
 * stopping halfway. Each is still its own authored row: a bulk action is a
 * convenience for the person, not a shortcut through the audit trail.
 */
function Field({
  field,
  value,
  onChange,
}: {
  field: FormField
  value: string
  onChange: (next: string) => void
}) {
  return (
    <label className="bulk-field">
      <span>
        {field.label}
        {!field.required && <em className="field-optional"> · optional</em>}
      </span>
      {field.input === 'select' ? (
        <select value={value} onChange={(event) => onChange(event.target.value)}>
          <option value="">Choose…</option>
          {field.options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      ) : (
        <input
          type={field.input === 'number' ? 'number' : 'text'}
          value={value}
          placeholder={field.placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      {field.help && <em className="field-help">{field.help}</em>}
    </label>
  )
}

function AuthorForm({ form }: { form: OverlayForm }) {
  const queryClient = useQueryClient()
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [bulk, setBulk] = useState('')
  const [note, setNote] = useState('')
  const [done, setDone] = useState<string | null>(null)

  const bulkField = form.supports_bulk ? form.key[0] : null
  const set = (name: string) => (next: string) =>
    setAnswers((was) => ({ ...was, [name]: next }))

  // Numbers go to the API as numbers. A scale of "5" stored as a string sorts
  // before "10" and compares to nothing, which is P-05's shape in a new place.
  const typed = (fields: FormField[]) =>
    Object.fromEntries(
      fields
        .map((field) => [field, (answers[field.name] ?? '').trim()] as const)
        .filter(([, given]) => given !== '')
        .map(([field, given]) => [
          field.name,
          field.input === 'number' ? Number(given) : given,
        ]),
    )

  const missing = [...form.key, ...form.values].filter(
    (field) =>
      field.required &&
      !(bulkField && field.name === bulkField.name) &&
      (answers[field.name] ?? '').trim() === '',
  )
  const keys = bulk
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  const ready = missing.length === 0 && (!bulkField || keys.length > 0)

  const save = useMutation({
    mutationFn: async () => {
      const values = typed(form.values)
      const rest = typed(form.key)
      const targets = bulkField
        ? keys.map((one) => ({ ...rest, [bulkField.name]: one }))
        : [rest]
      // Sequential rather than parallel: each write supersedes the live row for
      // its key, and two racing for one key would leave whichever landed second
      // looking like the earlier decision.
      for (const key of targets) {
        await putOverlay(form.kind, key, values, note || null)
      }
      return targets.length
    },
    onSuccess: async (count) => {
      setAnswers({})
      setBulk('')
      setNote('')
      setDone(
        `Saved. ${count} decision${count === 1 ? '' : 's'} recorded — the figures pick it up when the transform next runs.`,
      )
      await queryClient.invalidateQueries({ queryKey: ['overlay', form.kind] })
      await queryClient.invalidateQueries({ queryKey: ['kpis'] })
      await queryClient.invalidateQueries({ queryKey: ['exceptions'] })
    },
  })

  return (
    <details className="bulk">
      <summary>Add a decision</summary>
      <p className="muted bulk-help">{form.purpose}</p>
      {form.examples.map((example) => (
        <p className="bulk-example" key={example}>
          {example}
        </p>
      ))}

      {form.key
        .filter((field) => !bulkField || field.name !== bulkField.name)
        .map((field) => (
          <Field
            key={field.name}
            field={field}
            value={answers[field.name] ?? ''}
            onChange={set(field.name)}
          />
        ))}

      {bulkField && (
        <label className="bulk-field">
          <span>{bulkField.label}</span>
          <textarea
            rows={4}
            value={bulk}
            placeholder={bulkField.placeholder}
            onChange={(event) => setBulk(event.target.value)}
          />
          <em className="field-help">
            {form.fields_note || 'One per line.'} {bulkField.help}
          </em>
        </label>
      )}

      {form.values.map((field) => (
        <Field
          key={field.name}
          field={field}
          value={answers[field.name] ?? ''}
          onChange={set(field.name)}
        />
      ))}

      <label className="bulk-field">
        <span>Why</span>
        <input
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Where this came from, or who confirmed it"
        />
        {/* Not required by the API, and asked for every time anyway. A decision
            with no stated reason is one nobody can revisit in six months. */}
        <em className="field-help">
          Kept against your name, so this can be explained later.
        </em>
      </label>

      <button
        type="button"
        className="button bulk-go"
        disabled={save.isPending || !ready}
        onClick={() => {
          setDone(null)
          save.mutate()
        }}
      >
        {save.isPending ? 'Saving…' : 'Save'}
      </button>
      {done && <p className="bulk-done">{done}</p>}
      {save.isError && (
        <p className="warn">That could not be saved. Check the values and try again.</p>
      )}
    </details>
  )
}

export function Enrichment() {
  // Which form the visitor was sent to, if they arrived from the exception
  // console. Read from the URL rather than held in state, for the same reason
  // the filter bar is: a link is the thing being followed.
  const [params] = useSearchParams()
  const asked = params.get('kind') ?? ''

  // What each form asks for, and its live options. Fetched once for the page
  // rather than per section: five sections asking the same question five times
  // is five round trips for one answer.
  const forms = useQuery({ queryKey: ['enrichment-forms'], queryFn: getEnrichmentForms })
  const byKind = Object.fromEntries((forms.data ?? []).map((form) => [form.kind, form]))

  return (
    <>
      <div className="scope">
        <p className="scope-line">
          Decisions layered over the CRM. Saving supersedes rather than edits, so nothing is lost.
        </p>
        <p className="scope-excluded">
          A change reaches the figures when the transform next runs — at five and thirty-five past
          the hour.
        </p>
      </div>
      {KINDS.map((entry) => (
        <Table
          key={entry.kind}
          {...entry}
          asked={entry.kind === asked}
          form={byKind[entry.kind]}
        />
      ))}
    </>
  )
}
