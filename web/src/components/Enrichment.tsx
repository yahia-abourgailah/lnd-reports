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
import { useState } from 'react'

import {
  getOverlay,
  getOverlayHistory,
  putOverlay,
  retireOverlay,
  type OverlayEntry,
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

function Table({ kind, label, blurb, valueFields }: (typeof KINDS)[number]) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState<number | null>(null)
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
    <section className="enrich">
      <header className="enrich-head">
        <h2>{label}</h2>
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

      {valueFields.length > 0 && <BulkAssign kind={kind} label={label} />}
    </section>
  )
}

/**
 * Assign many keys to one value in a single pass.
 *
 * The case this exists for is trainer names: sixteen spellings, one of which
 * is a duplicate of another, and doing that one at a time invites stopping
 * halfway. Each assignment is still its own authored row — a bulk action is a
 * convenience for the person, not a shortcut through the audit trail.
 */
function BulkAssign({ kind, label }: { kind: string; label: string }) {
  const queryClient = useQueryClient()
  const [keys, setKeys] = useState('')
  const [value, setValue] = useState('')
  const [note, setNote] = useState('')
  const [field, setField] = useState('')

  const assign = useMutation({
    mutationFn: async () => {
      const lines = keys
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
      // Sequential rather than parallel: each write supersedes the live row for
      // its key, and two writes racing for one key would leave whichever landed
      // second looking like the earlier decision.
      for (const line of lines) {
        const [keyName, keyValue] = line.includes('=') ? line.split('=', 2) : [field, line]
        if (!keyName || keyValue === undefined) continue
        await putOverlay(kind, { [keyName.trim()]: keyValue.trim() }, JSON.parse(value), note || null)
      }
    },
    onSuccess: async () => {
      setKeys('')
      await queryClient.invalidateQueries({ queryKey: ['overlay', kind] })
      await queryClient.invalidateQueries({ queryKey: ['kpis'] })
    },
  })

  return (
    <details className="bulk">
      <summary>Bulk assign</summary>
      <p className="muted bulk-help">
        One key per line. Each becomes its own authored row with the note below, so the history
        records the decision rather than the batch.
      </p>
      <label className="bulk-field">
        <span>Key field</span>
        <input value={field} onChange={(e) => setField(e.target.value)} placeholder="normalised_name" />
      </label>
      <label className="bulk-field">
        <span>Keys, one per line</span>
        <textarea rows={4} value={keys} onChange={(e) => setKeys(e.target.value)} />
      </label>
      <label className="bulk-field">
        <span>Value, as JSON</span>
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder='{"trainer_key": 3}' />
      </label>
      <label className="bulk-field">
        <span>Why</span>
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="two spellings, one person" />
      </label>
      <button
        type="button"
        className="button bulk-go"
        disabled={assign.isPending || !keys.trim() || !value.trim() || !field.trim()}
        onClick={() => assign.mutate()}
      >
        {assign.isPending ? 'Assigning…' : `Assign to ${label.toLowerCase()}`}
      </button>
      {assign.isError && <p className="warn">{String(assign.error)}</p>}
    </details>
  )
}

export function Enrichment() {
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
        <Table key={entry.kind} {...entry} />
      ))}
    </>
  )
}
