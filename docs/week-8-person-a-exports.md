# Week 8 — Person A: learner views and export

What leaves the building. Until now every figure has lived on a screen next to
its definition and its freshness badge; this week they become files on other
people's desktops, where none of that context follows unless it is written into
the file.

Measured against the live CRM on 8 September 2026.

## The one rule, and how it is enforced

Every number in every export comes from `lnd.metrics.registry` or from
`lnd.metrics.drilldown`, which takes its rows from the metric's own declared
population. Nothing in `lnd/export/` computes a figure.

`test_export.py::test_every_exported_figure_equals_the_metric` asserts it for
all twenty-one metrics rather than spot-checking, and
`test_csv_and_xlsx_hold_the_same_numbers` asserts the two formats agree — there
is one `ExportTable` and two writers, not two builders.

The rule matters more here than it did on week 7's scorecards, and the reason is
time. A dashboard figure can be re-checked against the definition sitting beside
it. A spreadsheet emailed in March and quoted in June cannot, and an exporter
with its own copy of a population rule would leave the wrong number in the
attachment, where nobody can see which definition produced it.

## 01 — Learner profile and the top-learners ranking

288 people have attended something. The ranking is derived from
`fact_attendance`: P-09 was a hand-typed Top Learner sheet whose headline
formula `=B95` pointed at a count cell, and P-10 was the same sheet referencing
a workbook that is not in the file. Both had one cause — the ranking was
maintained rather than derived — and the fix is that nobody types it.

**Ties.** Ordering by hours alone is not deterministic: PostgreSQL is under no
obligation to return equal rows in the same order twice, so the "top ten"
reshuffles between runs and somebody drops off a recognition list because of a
plan change. The sort is `(hours DESC, employee_key ASC)` — the tiebreak is
arbitrary and, crucially, stable.

**Joint rankings.** Ranks are competition-style: equal hours share a rank and
the next rank skips. Sequence numbering would publish a difference the data does
not contain, decided by a tiebreak that exists only for stability.

**Measure.** Ranked on Learner Hours, which is what the delivery plan names.
Programmes and sessions are shown beside it rather than hidden, because the
three orderings disagree — Hany attended more sessions than Gehad and fewer
programmes — and a reader who can see that will not mistake one for the truth.

Live, in Accounts Receivable:

| Rank | Learner | Programmes | Sessions | Hours |
|---|---|---|---|---|
| 1 | Gehad Badr El Sayed Bayoumi | 9 | 18 | 60.5 |
| 2 | Hany Refaat Abdallah Mohamed | 8 | 19 | 60.0 |
| 3 | Abdelrahman Mohamed Saad Ahmed | 7 | 13 | 45.0 |

**The names are scoped.** A ranked, named list of employees by training hours is
a leaderboard: recognition in one meeting, a performance record in another, and
nobody on it chose to be ranked. It follows the rule week 7 set for the
zero-training list, which is the same question from the other end — the total
and the spread of hours are always returned, and names appear once the view is
narrowed to a department, sector, company or job level. One predicate
(`learners.is_gated`), reversible the moment L&D say it is a celebration they
want published.

`/v1/learners/search` is the way into a profile that is not a ranking, and it
requires something to search for: an empty query returns nothing rather than the
first twenty people alphabetically, which would be the roster the gate withholds.

### A new dimension, and what it may not narrow

Profiles needed `Dimension.LEARNER`, which did not exist. It narrows attendance,
enrollment and evaluation — the three grains that record what a person did — and
is deliberately absent from `enrollable_employees`.

Participation Rate, Coverage Gap and every programme-grain metric **refuse** it.
One person's participation rate is 1/1, which is not a rate; a session is not
attributable to one attendee, so "their Training Days" would silently mean the
days somebody else also sat in. A metric that accepted the filter on one side
only would be P-13 at the grain of an individual.

It is accepted by the API and deliberately not offered by the filter bar. As a
global control it would turn every screen into a search for an individual, which
is a different product from the one L&D asked for.

## 03 — The export service

| Route | What it holds |
|---|---|
| `/v1/exports/kpis.csv` / `.xlsx` | Every figure the filters allow, with definition, population, exclusions and sample |
| `/v1/exports/records/{key}.csv` / `.xlsx` | The rows behind one figure — the drill-through, as a file |
| `/v1/exports/monthly.xlsx` | The workbook's DASHBOARD layout, generated |

Filenames carry the date they were produced. Two exports of the same view a
month apart hold different numbers, and a download folder with one `kpis.xlsx`
in it is a folder where the older one silently won.

### The monthly report

Read out of `docs/L&D Main Reports.xlsx`: a title across the top, labels in row
7 and their values in row 8, participation in column Z, the public/customised
split in AA/AB. The column positions are the workbook's, so a reader's eye lands
where it always has. August 2026 generates as 3 programmes, 8 training days, 35
participants, 21 hours, NPS +86.4, participation 2.4%.

Only DASHBOARD is reproduced. Attendance Master Sheet, Head Count 212,
Attendance Prep, Calc, Linked In no and the pivot tabs are the machinery that
produced it — they are what the platform replaces, and reproducing them would be
reproducing eight hours of manual assembly in code.

**One cell is deliberately not faithful.** L8 held `0.9272…` formatted as 92.7%
and labelled "NPS Overall Score". NPS is an index from −100 to +100 and the
workbook's figure was a percentage of something else. Writing +86.4 into the
cell that used to show 92.7% invites exactly the reading the metric layer exists
to prevent, so the label carries the unit and the sheet carries the
reconciliation note.

The report also gains an "All figures" sheet. The workbook's DASHBOARD was the
whole report because assembling more was manual; here the detail costs nothing.

## 04 — Provenance stamping

Every file answers, from its own contents: what period, what filters, how fresh,
how many rows excluded, how many flagged, what each figure means, and the
reconciliation wording for anyone about to set a restated figure beside an old
one.

In CSV the stamp sits above the header, each line prefixed `#`. A reader opening
it in Excel sees the scope before the numbers; a parser pointed at row 1 sees
the stamp instead of the header, which is the cost, and `skip_stamp` exists for
a machine feed. Stamping the bottom instead would put the scope where a reader
scrolling a thousand-row extract never reaches it.

In XLSX the stamp is its own sheet, always first.

The definition strings travel with the numbers. A definition is worth more in an
attachment, where it cannot be looked up, than on a screen where it was one
hover away.

## 02 — LinkedIn ingest: not built

The only task this week waiting on somebody else, and it is still waiting. The
delivery mechanism and column set are an open question with L&D from week 1;
nothing in the repository defines either, and `sync/catalogue.py` still declines
to declare `(LINKEDIN, COURSE_COMPLETION)` for the same reason.

LinkedIn Hours, Blended Learner Hours and Unique Reach therefore continue to
return no value rather than zero — there has been no measurement, and that is
not a measurement of none. No classroom metric depends on them, which is why the
plan named this as the first thing to cut.

**What it needs to start:** the export's delivery mechanism and its column set.
The identity rule is already settled and is a join rather than a search —
`employee_code` first, `odoo_id` as the fallback — and anything that will not
resolve goes to `ops.dq_exception` quarantined, never dropped. When the feed
arrives, the golden file moves for the first time on purpose, and
`docs/reconciliation.md` has to say what appeared and why in the same commit.

## Open

- **The gate on the ranking is provisional.** It follows week 7's precedent
  rather than a decision L&D have made. The plan says "automatic ranking" and
  does not say who sees it.
- **No front end.** These are Person A's queries and endpoints; the learner
  profile, the top-learners view and the export buttons are Person B's half of
  week 8.
