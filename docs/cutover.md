# Cutover: moving distribution, and freezing the workbook

The last thing week 10 does. It is deliberately the last: two systems producing
the same figures is how a team ends up with two answers and a preference.

---

## The order, and why it cannot be reordered

```
  the walkthrough   →   sign-off   →   distribution moves   →   the workbook freezes
```

Each one is a precondition for the next, and the first has been owed since week
4. Nothing below can start until the Director has signed
[`reconciliation.md`](reconciliation.md), because switching distribution is the
act that makes the restated figures *the* figures — and doing that before
anybody agreed to them is how a correction becomes a dispute.

---

## Gate 1 — the walkthrough

Owed since week 4, and it has now blocked five phase exits. Nobody outside the
build team has seen participation at 9.2% having published 60.4%.

Bring the evidence, not the summary: the hardcoded 192, the two `Hard Talks`
programme ids, the two spellings of one trainer's name, and the pivot filter
that drops the two largest programmes from every quality score. Let the file
make the argument.

Order: coverage first, because it explains most of the movement and nobody
disputes it. Then the restated figures. Then the corrections, each with its
defect number. Participation Rate and NPS last, with room.

Three things must be said out loud before their numbers:

- *Participation was never measured. It is not a fall from 60.4%.*
- *NPS is now reported on the standard −100 to +100 scale.*
- *Learner Hours is the one figure lower than the workbook, and here is why.*

**Settle the stale figures first.** The parallel run reports two: the roster has
moved since the dataset was frozen, so Participation Rate reads 9.2% live
against 9.3% in the signed document, and Coverage Gap 1,326 against 1,320. A
tenth of a point is nothing to the argument and everything to the credibility of
the file if somebody opens the dashboard during the meeting.

Regenerating `reconciliation.md` does **not** fix this — it is generated from the
frozen dataset, which is what lets CI pin it. Two real options:

- **Re-freeze**, and update `golden.json` in the same reviewed commit. The right
  choice if the meeting is more than a day away.
- **Present the live figure**, with the difference stated. The right choice if it
  is not, and it costs one sentence: *the roster has gained seven people since
  this document was generated.*

Run the evidence first either way, so you know what has moved on the day:

```bash
make evidence
```

## Gate 2 — sign-off

The three checkboxes and a signature at the foot of `reconciliation.md`. Plus
HR's, at the foot of [`security-review.md`](security-review.md) — that one has a
question in it that is genuinely open, and it should be raised rather than
ticked: there is no erasure path for a named individual today.

Also acknowledge what is *not* signed, rather than omitting it: LinkedIn Hours,
Blended Learner Hours and Unique Reach report no value, because the export's
delivery mechanism and column set are still open with L&D. They ship visible and
blocked, not hidden.

## Gate 3 — distribution moves

Only after gate 2.

1. The monthly job's recipient list becomes the real one. **Blocked on IT:** the
   generate-keep-send path is tested end to end against a live SMTP conversation
   and there is no relay to send through.
2. Send one cycle and confirm it arrived — at the recipients, not in a log.
3. Stop assembling the workbook. This is the actual moment of cutover, and it is
   a decision by a person, not a deployment.

## Gate 4 — the workbook freezes

**Read-only, never deleted.**

- Set the file read-only where it lives, and say so in its filename or its
  banner sheet: *frozen 2026-09-xx — superseded by the L&D Analytics platform*.
- Retire the SOP that describes assembling it. A procedure nobody follows and
  nobody withdrew is a procedure somebody will follow.
- Keep it restorable for one further cycle.

Reverting means resuming manual assembly — eight to twelve hours a cycle, and
every defect the platform exists to fix comes back with it. So the trigger for
reverting is a **correctness failure**, demonstrated, not a preference. Write
that down before anybody is under pressure to invoke it.

---

## What is carried over, and what is not

**Carried:** every figure the DASHBOARD tab published, in its own layout — see
[`report-comparison.md`](report-comparison.md), where all ten land in the cell
their label claims.

**Not carried, on purpose:** Attendance Master Sheet, Head Count 212, Attendance
Prep, Calc, Linked In no and the pivot tabs. They are the machinery that produced
DASHBOARD — the eight hours the platform replaces. Reproducing them would be
reproducing the manual assembly in code.

**Not carried, and still open:** the `Linked In no` sheet's content. Until the
LinkedIn export arrives, that hand-typed tracking has no successor, and P-14
stays open. Say so at the freeze rather than letting somebody discover it.

---

## After the cutover

The first month is the one that matters. Watch for:

- The report arriving on the 1st without anybody touching it.
- The exception queue being cleared by L&D rather than by the build team. If the
  team is still clearing it in month two, the console is not usable and that is a
  defect, not a training gap.
- Somebody quoting 60.4% in a meeting. It will happen; the workbook is frozen,
  not forgotten, and the number is in slide decks that were made before any of
  this.

---

## Checklist

| | |
|---|---|
| Reconciliation regenerated against live data | ☐ |
| Walkthrough held with L&D | ☐ |
| Director signature on the reconciliation | ☐ |
| HR signature on the security review, erasure question raised | ☐ |
| UAT complete, blocking issues fixed — [`uat.md`](uat.md) | ☐ |
| SMTP relay and recipient list in place | ☐ |
| One cycle delivered and confirmed received | ☐ |
| Workbook frozen read-only, banner added | ☐ |
| SOP for manual assembly retired | ☐ |
| Revert trigger written down and agreed | ☐ |
