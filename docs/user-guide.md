# Using the L&D Analytics platform

For the L&D team. It assumes you know the training and not the software.

The short version: **the number is the control**. Anything on a screen that
looks like a figure can be clicked, and clicking it shows you the rows it was
made of. If you only remember one thing, remember that — it is the answer to
almost every "where did this come from?" you will be asked.

---

## Signing in

<https://lnd-analytics.themarq.local> — your normal work account. There is no
separate password, and there never will be.

The Director does not sign in. They receive the monthly report by email.

---

## The screens

| Where | What it answers |
|---|---|
| **Overview** | The twelve headline figures, each with its trend |
| **Coverage** | Who has *not* been trained — by sector, department, company, job level |
| **Funnel** | Enrolled → attended → evaluated, and where people fall out |
| **Programmes** | One programme: its sessions, fill, no-shows, scores and every comment |
| **Trainers** | One trainer: what they delivered, and how it was rated |
| **Learners** | One person's whole history, and the ranking by hours |
| **Reports** | Generate a file now, or fetch a report that was already sent |
| **Exceptions** | Everything the platform could not place, and what to do about it |
| **Enrichment** | The decisions you have layered over the CRM |

The **filter bar** at the top applies to every screen and stays as you move
between them. It is in the address bar too, so a filtered view is a link you can
paste to somebody.

The **freshness badge**, top right, says how long ago the data was fetched. Green
under an hour, amber over. Data moves on a thirty-minute cycle, so a change made
in the CRM at 10:07 reaches you around 10:35.

---

## Reading a figure honestly

Three things travel with every number, and all three are on screen:

**The definition.** Click the *i* beside any figure. It says what the metric
counts, what population it counts over, and what it leaves out. The old workbook
had none of this, which is how a score computed over 55 of 77 responses was
published for a year as if it covered everything.

**The sample.** The line under each figure — "over 297 responses". 98% over 297
and 98% over 4 are different claims. If the sample drops sharply between months,
that is the story, not the percentage.

**What it excludes.** Where records could not be placed, a banner says so and
gives two separate numbers: how many were *excluded* from the figures, and how
many were *flagged* but still counted. Only the first costs you anything.

Some figures read **no measurement yet** rather than a zero. That is deliberate:
LinkedIn Hours has no source connected, and "nobody has measured this" is not
the same claim as "this is zero".

---

## The month

About twenty-five minutes, once a month. The report sends itself.

**1. Clear the exception queue** — Exceptions, roughly ten minutes.

Each row says what the platform could not place and suggests what to do. Three
ways to close one:

- **Map it** — "this spelling means this person". Use this when the source is
  inconsistent and you know the answer.
- **Override it** — "the CRM has no answer here; this is the right one". Use
  this when the source is silent, not when it is wrong.
- **Dismiss it** — with a reason, always. The reason box is the audit trail, and
  a dismissal with an empty one is how a figure becomes unexplainable in March.

Nothing you do here edits the CRM. Your decision sits *over* the source, so
"what did the CRM say" and "what did we decide" both stay answerable.

**2. Fill in what is missing** — Enrichment, roughly ten minutes.

Programmes without a department or a trainer. Every entry keeps who decided and
why; changing one supersedes the old entry rather than replacing it, so the
history survives. Withdrawing one blanks its figure and raises a named exception
saying which question can no longer be answered — that is the system working, not
breaking.

Changes reach the figures at the next transform: five and thirty-five past the
hour.

**3. Check completeness and freshness** — five minutes.

Exceptions → Completeness shows, per period, how much of what should be there
actually is. Green means the month is whole.

**4. Nothing.** The report generates and sends on the 1st at 07:00. If it did
not arrive, you will have been alerted before anybody asks.

---

## Answering the questions you actually get

**"How many people have we trained?"** Overview, Total Participants. Say the
period out loud — the figure means nothing without it.

**"What's our participation rate?"** 9.2% over the workbook's window. It is not
a fall from 60.4%: that figure divided five companies' attendance by one
company's headcount, after dividing by a number typed into a formula. See
[`reconciliation.md`](reconciliation.md) before you quote either.

**"Is NPS down?"** No. It is now reported on the standard −100 to +100 index
rather than as a percentage. Say that sentence *before* you say the number.

**"Who hasn't been trained?"** Coverage. Give the slice's own denominator every
time — 52.8% over 53 people in HR and 1.7% over 904 in Sales are not the same
kind of claim, and the company average of 13.5% describes neither.

**"Send me the numbers."** Export from the screen you are looking at; the file
carries your filters, the definitions and the freshness. Or, if they want last
month's, fetch the edition that was actually sent rather than regenerating it.

---

## Things that will look wrong and are not

- **Two trainers with the same name merged into one.** They are one person. The
  CRM stores the trainer as free text and sixteen spellings cover fifteen people.
- **`L&D Team` in the trainer list.** Not a person — it is what a session says
  when nobody recorded who delivered it. It ranks sixth by sessions, and it is
  marked.
- **`Belton Academy` in the trainer list.** An outside vendor. Also marked, and
  its hours are in every total, which is why it is shown rather than hidden.
- **Total Programs 55, not 2.** The CRM's own `status` field says "upcoming" for
  55 programmes that have already happened. Only `computed_status` is
  trustworthy, and that is what the platform counts.
- **Training history starting in July 2025.** The plan recorded September. 68 of
  123 sessions predate it, and whether those are real deliveries or data loaded
  during the CRM's own build is still an open question for L&D.

---

## When something is wrong

The platform tells you in two places, and they answer different questions.

**Alerts** — has something broken since I last looked? Pushed, and they repeat
while the problem lasts.

**The exception console** — what can the platform not place *right now*? Always
the present state.

If the freshness badge goes amber and stays there, the CRM connection is
failing. Nothing you see becomes silently wrong: the platform keeps serving the
last good data and the badge keeps saying how old it is. Tell whoever runs the
platform; [`runbooks.md`](runbooks.md) is what they will work from.

Nothing here can change the CRM. There is no write client in the software and
the credential is read-only, so no mistake made on this platform can reach the
source.
