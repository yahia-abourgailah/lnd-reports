# Week 7 — scorecards, coverage, funnel

The analyses Excel could never produce. Every one of them needs a person who
never appeared in the workbook, or a stage nobody counted.

Measured against the live CRM on 8 September 2026, after the transform pass that
introduced the trainer kinds.

## Nothing here computes a number

Every figure on every screen this week is `registry.compute` with the filters
narrowed to one programme, one trainer or one slice. That is the whole design
constraint, and it is worth stating why: a scorecard that recomputed No-show
Rate would be a second definition of it. It would agree on the day it was
written, and the first time somebody corrected a population rule in one place
and not the other, both would keep returning plausible numbers with nothing on
screen to say which was right. That is exactly how the workbook came to publish
six wrong figures.

The CI gate confirms it held: the full suite passes and **no published figure
moved**. `tests/reference/golden.json` is untouched.

## What was measured

### The funnel

| Stage | Count | Movement |
|---|---|---|
| Enrolled | 1,061 | 411 never attended — 38.7% no-show |
| Attended | 650 | 353 never responded — 45.7% response rate |
| Evaluated | 297 | |

Walk-ins: **0**. Every attendee had enrolled, so the stages happen to subtract
cleanly — which is a fact about this data and not a rule, and the view says so.
The drop out of each stage is the naming metric's own numerator, never
`this stage minus the next`: a walk-in cancels a genuine no-show out of a
subtraction, and a programme half the enrolled list skipped can report a drop of
zero.

Every stage is one person on one programme. Attendance is per *session*, so a
stage built by counting attendance rows would report more people attending than
enrolled the moment anybody sits in two sessions of one programme.

### Coverage

| Company | Trained | Active staff | Covered |
|---|---|---|---|
| The Address Investments | 81 | 1,247 | 6.5% |
| The MarQ Communities | 118 | 184 | 64.1% |
| Eclatic Cosmetics | 2 | 14 | 14.3% |
| **Overall** | **201** | **1,445** | **13.9%** |

1,244 of 1,445 active employees have attended nothing. The parts sum to the
whole in both terms — the slices' numerators and denominators add to the overall
figure, because a slice is the same metric under a narrower filter rather than a
`GROUP BY` written beside it. That is asserted in `test_analysis`.

The ten-times gap between two companies is the finding of the week and is not a
data defect: both numerators and both denominators come from the same roster,
which is what P-13 corrected.

### Trainers

15 after the alias merge. The list shows sessions, programmes and attendances
together, because they answer different questions — Mohamed Rashad has the most
attendances (305) on the second-smallest catalogue of the top five (5
programmes), and a list showing only sessions invites the wrong comparison.

Session counts here are over **completed** programmes, consistent with Training
Days, so they are lower than a raw count of every session in the payload.

## Decisions taken this week

### `L&D Team` and `Belton Academy` are marked, not hidden

Migration `0011` adds `is_placeholder` and `is_external` to `dim_trainer`;
`transform/trainer_kind.py` sets them. The flags did not previously exist —
the week-7 brief described them as already present, and they were not.

Both rows are kept and counted: their sessions are real and their hours are in
every total, so removing them would leave a figure that does not add up. What
they get is a label, so a reader can decide what the ranking means with the
label in front of them.

The classifier is an **explicit list, not a keyword rule**. A rule matching
"team", "academy" or "institute" is wrong the first time somebody called Nadia
Akademi delivers a session — and wrong invisibly, because a real trainer would
simply be labelled a vendor. An unrecognised name is a person, so the failure
falls in the direction somebody notices. When L&D want to reclassify one, this
belongs in `app` beside the trainer aliases; it is code today because there are
two entries and no screen to edit them from.

### A trainer's quality figures are stated at programme grain

An attendance joins to its session and a session names its trainer, so Total
Participants and Learner Hours honour a trainer filter — `ATTENDANCE_DIMENSIONS`
in `metrics/definitions.py`. A survey response does not: its grain is one person
on one *programme*, and nothing says which session the respondent sat in.

So NPS, the four quality scores, Survey Response Rate and No-show Rate on a
trainer scorecard are computed over **the programmes that trainer delivered**,
and the screen says so in those words. On a programme two trainers shared, both
carry the same responses. That is a weaker claim than "this trainer's NPS" and
it is the true one; the alternative was attributing feedback to a person the
data does not attribute it to.

The combination is the registry's: asking NPS for a set of programme ids returns
one numerator over one denominator across the set. The per-programme rows
beneath sum to it and do not average to it — asserted in
`test_nps_is_weighted_by_responses_not_averaged`.

### The 129-department tail is named

`MAX_SLICES` is 60 and department has 129 values. The view shows the 60 largest
by headcount with a search box, and states the rest: *"the other 69 hold 102
employees between them — narrow with the filter bar to reach one."* The shared
slice limit is untouched, and no second aggregation computes an "other" bucket —
which would be a population computed somewhere other than the metric.

### The zero-training list is scoped, not published

1,244 people have had no training. The **count is always shown**; the **names**
appear once the view is narrowed to a department, sector, company or job level.
A period alone does not unlock them — "everyone untrained since January" is
still everyone.

This is a judgement about how an individually-named, sortable, exportable list
of people who have done nothing reads in a performance conversation as against a
planning one. It is not a technical limit: `coverage.is_gated` is one predicate,
and lifting it is a one-line change if L&D decide otherwise. **Worth confirming
with them before the screen ships.**

The list and the count come from one statement — `scope.untrained_employees`,
which the Coverage Gap metric also counts — so the rows and the number above
them cannot disagree. Previously the metric built that predicate inline.

## Known, and deliberately left

- **The Coverage Gap drill-through still opens to the eligible population**
  (1,445 rows), not to the 1,244 untrained. `/v1/drill/{key}` derives its rows
  from the metric's declared *grain*, and the platform-wide invariant asserted
  in `test_kpis_api` is `drill.total == metric.sample_size`. Changing it for one
  metric would break that invariant for all of them, so the untrained list is
  served by `/v1/coverage/untrained` instead. Worth revisiting as a
  population-keyed drill in week 9.
- **Three trainer rows are two people** — `Aya Sameh & Amr Alaa`, `Hagar & Amr`,
  `Hussam & Zeyad`. The known "three sessions naming two trainers" case. They
  are not aliases and cannot be merged into either person without inventing an
  attribution; they belong in the exception queue, which is week 9.
- **Two programmes have no trainer on any session** and so cannot appear on a
  trainer scorecard. Already an open exception, not a gap papered over.
