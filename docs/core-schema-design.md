# The `core` star schema — designed against the real payload

Week 3's data model, derived from what the CRM actually returns rather than
what the BRD assumed. Every decision below is traceable to a measurement on the
live dataset: 57 programs, 123 sessions, 417 people, 1,607 survey answers.

The endpoint returns one deeply nested tree per program. `core` flattens it into
dimensions and facts at three grains, so a metric is a `GROUP BY` rather than a
walk through JSON.

---

## The five decisions that matter

**1. The survey is one survey.** All 53 programs that have one use survey `id=2`,
"Workshop Evaluation", with the same six questions. The question ids are stable,
so each published metric maps to exactly one `question_id`. That mapping lives in
a dimension row, not in code.

**2. Answers are stored long, not wide.** The BRD assumed `fact_evaluation` at
"response × program" with `q1..q5` as columns. The payload gives one answer per
question per person, and one of the six is free text. Storing a row per answer
means a new survey question needs no migration, and text and ratings coexist
without a nullable column each.

**3. `q7` is a 0–10 scale, the other four are 1–5.** Banding NPS as
promoter/passive/detractor only makes sense for `q7`. Deriving `nps_band` once,
on ingest, means no query ever re-derives it and no two queries can disagree.

**4. Identity is `employee_code`.** Verified 1:1 across 2,218 user objects —
417 people, 417 codes, 417 odoo ids, none missing, no code carrying two names.
The BRD's four-step resolution collapses to a join.

**5. `computed_status`, never `status`.** They disagree on 55 of 57 programs.
Counting Total Programs off `status` yields 2; the answer is 55.

---

## Dimensions

### `core.dim_employee` — SCD Type 2

| Column | Source | Note |
|---|---|---|
| `employee_key` | surrogate | |
| `employee_code` | `user.employee_code` | **The business key.** `TM4-3716` |
| `odoo_id` | `user.odoo_id` | The cross-system id; 1:1 with the code |
| `full_name`, `email`, `mobile` | `user.*` | |
| `department_name`, `department_odoo_id` | `user.department` | |
| `company_name`, `company_odoo_id` | `user.company` | 5 companies — this is P-13 |
| `sector` | `user.sector` | **Trimmed on ingest.** 940 of 1,052 arrive with a trailing space; 28 raw values collapse to 23 (P-05) |
| `position_name` | `user.position.name` | 240 distinct |
| `job_level_name` | `user.job_level_name` | null for ~10% |
| `job_level_grade` | `user.job_level_grade` | **Cast to integer.** Arrives as text, so `"10"` sorts before `"9"` |
| `status` | `user.status` | |
| `valid_from`, `valid_to`, `is_current` | — | SCD2 |
| `is_estimated` | — | True for periods before we started snapshotting |

The CRM has no employee history, so versioning starts now and pre-launch periods
are labelled. At 417 rows, versioning is free.

### `core.dim_program`

Keyed on `crm_program_id`, **never on title** — the direct fix for P-02, where
two separately-run "Hard Talks" merged into one.

`title`, `subtitle`, `description`, `status`, `computed_status`, `capacity`,
`type`, `target`, `start_date`, `end_date`, `track_id`, `track_title`,
`parent_program_id`, `created_at`, `updated_at`.

`departments[]` and `companies[]` are arrays on the source, so they become
`core.bridge_program_department` — populated only when `target = 'department'`
(7 of 57 today).

### `core.dim_session`

Keyed on `crm_session_id`. This is the answer to P-12: the workbook's `#` column
was a row counter and a session id in one, so `COUNT(DISTINCT session_key)` over
it was meaningless.

`program_key`, `session_date`, `time_from`, `time_to`, `trainer_key`,
`location_id`, `location_name`, plus two derived columns:

- `duration_hours` — from the times. **0 of 123 sessions are missing one**, so
  Training Hours Delivered is fully derivable today.
- `duration_derivable` — the flag that drives `DURATION_UNDERIVABLE`.

### `core.dim_trainer`

`trainer_key`, `canonical_name`, `is_external`, `is_placeholder`.

The CRM stores the trainer as free text per session — the only person in the
payload without a key. 16 strings across 123 sessions, and `Ahmed Elshiaty` (10)
and `Ahmed ElShiaty` (4) are one person (P-04).

`core.dim_trainer_alias` maps every observed spelling to one key. It is a
stopgap: the durable fix is a trainer `employee_code` from the CRM, at which
point this table retires without a migration. Two flags matter for reporting:
`Belton Academy` is external, `L&D Team` is a placeholder pending L&D's answer.

### `core.dim_survey_question` — the important one

| `question_id` | `answer_type` | `scale_min` | `scale_max` | `metric_key` |
|---|---|---|---|---|
| 3 | rating | 1 | 5 | `knowledge_relevance` |
| 4 | rating | 1 | 5 | `activity_effectiveness` |
| 5 | rating | 1 | 5 | `logistics_effectiveness` |
| 6 | rating | 1 | 5 | `facilitator_performance` |
| 7 | rating | 0 | 10 | `nps` |
| 8 | text | — | — | `comment` |

Every quality metric becomes `WHERE metric_key = '…'`. A question added to the
survey is a row here, not a code change — and if the survey is ever renumbered,
one row moves and every metric follows.

### `core.dim_date`

Calendar with month, quarter and fiscal attributes.

---

## Facts — three grains, deliberately

The workbook's core fault was one flat table carrying three grains (P-06), which
is why sums were only correct by convention.

### `core.fact_enrollment` — one employee × one program

From `users[]` **where `is_enrolled = true`**. The roster is the union of
enrolled people, walk-ins and survey-only respondents, so treating it as the
enrollment list would inflate the funnel's first step and understate No-show Rate.

`employee_key`, `program_key`, `enrolled_at`, `attendance_rate`, `is_enrolled`.

### `core.fact_attendance` — one employee × one session

From `sessions[].attendance[]`.

`employee_key`, `session_key`, `program_key`, `attended_at`, `learning_hours`.

`UNIQUE (employee_key, session_key)` catches duplicate scans — and it is safe
only because `session_key` is the CRM session id. Keyed on the workbook's `#`
it would reject 90 rows rather than 7.

### `core.fact_survey_answer` — one employee × one program × one question

From `users[].survey_answers[]`.

`employee_key`, `program_key`, `question_key`, `answered_at`,
`rating_value` (int, null for text), `text_value` (null for ratings),
`nps_band` (`promoter` / `passive` / `detractor`, derived once, only for q7).

`UNIQUE (employee_key, program_key, question_key)` — one person answers each
question once per program.

---

## What the metrics become

```sql
-- NPS: q7, 0-10, banded on ingest
SELECT (count(*) FILTER (WHERE nps_band = 'promoter')
      - count(*) FILTER (WHERE nps_band = 'detractor'))::numeric
      / count(*) AS nps
FROM core.fact_survey_answer a
JOIN core.dim_survey_question q USING (question_key)
WHERE q.metric_key = 'nps';

-- any quality score: same shape, one value changes
SELECT count(*) FILTER (WHERE rating_value >= 4)::numeric / count(*) AS score
FROM core.fact_survey_answer a
JOIN core.dim_survey_question q USING (question_key)
WHERE q.metric_key = 'logistics_effectiveness';
```

Every quality metric is the same query with a different `metric_key`. That is
what makes one definition per KPI enforceable rather than aspirational.

### Computed from live data today

| Metric | Value | Responses |
|---|---|---|
| **NPS** | **+88.1** | 303 |
| Knowledge Relevance | 97.7% | 303 |
| Activity Effectiveness | 98.0% | 303 |
| Logistics Effectiveness | 92.1% | 303 |
| Facilitator Performance | 98.7% | 303 |

Against the workbook's published 92.7% NPS, 100.0%, 98.2%, 96.4%, 100.0% — but
these come from a different and wider period, so they are not yet the
reconciliation. They do show every figure is now computable.

---

## Transform notes

**Conform on the way in, never on the way out.** `sector` trimmed,
`job_level_grade` cast to integer, trainer names resolved through the alias
table, `nps_band` derived. A report that trimmed at query time would be one
forgotten `trim()` away from inventing five sectors.

**`raw` is the only input.** The transform is a pure function of
(raw + enrichment), so any fix replays over history without re-querying the CRM.

**Assert the invariant before commit.**
`count(raw active) = count(core included) + count(quarantined)`. Off by one and
the run fails loudly with the dashboard still serving the previous good state.

**Query the current version only.** `raw.source_record` is append-only and
already holds two versions of every program. Every transform read starts:

```sql
SELECT DISTINCT ON (source_id) payload FROM raw.source_record
ORDER BY source_id, fetched_at DESC, id DESC
```
