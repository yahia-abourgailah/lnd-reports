# History verification and the comparison window

Week 4, task 1. This is the decision every figure in the reconciliation
statement rests on, so it is written down rather than carried in somebody's
head.

## What history the CRM actually holds

Verified against the live API, and asserted in the golden suite so that a change
at source is a failing test rather than a surprise:

| | |
|---|---|
| First session | **2025-07-29** |
| Last session | 2026-09-23 |
| Sessions | 123 — **68 in 2025**, 55 in 2026 |
| Sessions missing a time | **0** |

Two things follow.

**Training Hours Delivered is fully derivable.** Every session carries both a
start and an end, so no hour is estimated and `DURATION_UNDERIVABLE` fires zero
times.

**Nothing in `core` came from the workbook.** There is no workbook ingestion
path in the codebase — the only sources are the CRM's program and employee
endpoints. If a spreadsheet-derived figure is ever loaded it must carry a source
tag, so that a number from a CRM and a number from a spreadsheet are never
indistinguishable downstream. Today the question is moot, and the golden suite
pins the session count so it stays that way.

### Open question for L&D: history starts in July, not September

The plan records history as beginning in September 2025 (Q-10, Q-12). It begins
on **29 July 2025**, and **68 of 123 sessions — more than half — fall in 2025**.

That is too many to wave through as a rounding error in somebody's recollection.
Either the CRM was in use earlier than the plan assumes, or some of those
sessions are test data loaded during the CRM's own build. The two readings lead
to very different published figures.

**This does not block the reconciliation**, because the like-for-like window
below excludes 2025 entirely. It does block publishing anything over the full
dataset, and it needs an answer before launch.

## The comparison window

**1 February to 31 August 2026**, inclusive.

That is the period the workbook covers. It is the only window in which
"the workbook said 24 programs and the platform says 27" is a statement about
*definitions* rather than about how much more data the platform can see.

Month boundaries rather than first and last session dates, deliberately: a
window pinned to actual session dates would move whenever a session is
rescheduled, and a comparison window that moves is not a comparison.

Encoded once in `lnd.reference.windows` and read by both the golden suite and
the reconciliation generator, so the two cannot disagree about what was
compared.

### Why both windows are computed

| | Feb–Aug 2026 | Full dataset |
|---|---|---|
| Training Days | 50 | 119 |
| Total Programs | 27 | 55 |
| Training Hours Delivered | 135.6 | 365.2 |
| Learner Hours | 1,153.0 | 3,686.5 |
| Participation Rate | 9.4% | 14.1% |

The full dataset is what the platform will publish once live. It is **not**
comparable to the workbook's column — it covers roughly twice the period — and
presenting it as though it were attributes to *correction* what is mostly
*coverage*.

The statement therefore leads with the narrow window and shows the full dataset
separately, labelled.

### What the narrow window reveals

Two figures deserve attention, and neither is visible when comparing against the
full dataset:

**Training Hours Delivered: 130.5 → 135.6.** A 4% gap, not a threefold one. On
the full dataset the same metric reads 365.2, which looks like an enormous
overstatement and is nothing of the kind.

**Learner Hours: 1,386.0 → 1,153.0.** The platform is *lower*. This is the only
figure that moves downward, it is not explained by coverage, and it will be
asked about. Learner Hours is session duration summed once per attendance, so
the likely cause is the workbook's single `hours` column being read as both
delivered hours and learner hours in different places — but that is a
hypothesis, and it needs confirming before the meeting rather than in it.

## Done when

- [x] Date range confirmed against the live CRM and pinned in the golden suite
- [x] Comparison window chosen, written down, and encoded in one place
- [x] Verified nothing in `core` came from the workbook
- [ ] **L&D confirms the July–August 2025 sessions are real deliveries**
- [ ] **L&D confirms Total Programs was published as 24 or 25** — the BRD and
      the week-4 handover disagree
