# User acceptance testing

Three roles, three different weeks, and only one of them ever signs in.

This is a script to run *with* somebody, not a checklist to tick on their
behalf. The one thing no test suite can assert is the word **unaided**, and that
is the whole of what UAT is for here: 690 tests already prove the figures are
right, and none of them prove a specialist can find the answer to a question
somebody asked them in a corridor.

**Run it after the reconciliation walkthrough, never before.** A specialist
meeting participation at 9.2% for the first time on a screen, having published
60.4%, will report it as a defect — and they will be right to, because nobody
told them. See [`reconciliation.md`](reconciliation.md).

---

## Before the session

- The dev or staging stack is up and the freshness badge is green.
- The person has not seen the platform before. Somebody who watched it being
  built cannot tell you whether it is discoverable.
- You are taking notes, and you are not touching the keyboard. The moment you
  reach over, the test is over.
- Time the tasks. "Under 30 minutes of human work per month" is an acceptance
  criterion (NFR), and it is measured here, not estimated.

Record for each task: **done unaided / done with a hint / not done**, the time,
and the exact words they used when they got stuck. The words matter more than
the outcome — "where's the attendance sheet?" tells you the navigation is
labelled in the platform's vocabulary rather than theirs.

---

## 1. L&D Specialist — daily

The product owner in practice. If this person cannot work the platform, nothing
else matters, because they are the one who clears the queue and fills the gaps
every other figure depends on.

| # | Task | Passes when |
|---|---|---|
| 1.1 | Find how many people attended training in August 2026 | The filter bar is used rather than the browser's find; the sample size beside the figure is noticed |
| 1.2 | Say who those people were | The number is clicked. If they hunt for an "export" or a "reports" menu first, the number-as-a-control idea has not landed |
| 1.3 | Find out why Logistics Effectiveness is 91.5% and not the 96.4% in the old workbook | The definition panel is opened, and the population is read out loud |
| 1.4 | Clear the exception queue | Each open exception is resolved by mapping, override or a dismissal *with a reason*. A dismissal with an empty reason box is a fail — the box is the audit trail |
| 1.5 | A programme is missing its department. Fix it | Enrichment. The form is filled without asking what JSON is (this was a defect, fixed at `5359cc6`) |
| 1.6 | Send last month's report to the Director | The retained edition is found under Reports, not regenerated. Regenerating August in October gives the current answer for August, never the file that was sent |
| 1.7 | Somebody asks "has Sales had any training?" | Coverage, filtered. 1.7% over 904 people, and the denominator is stated |

**Timed:** tasks 1.4, 1.5 and a freshness check, back to back. That is the
monthly routine, and the criterion is under 30 minutes.

---

## 2. L&D Manager — weekly

Reads more than they do. The risk here is not that they cannot operate it — it
is that they read a figure without its scope, which is the defect this whole
platform was built to end.

| # | Task | Passes when |
|---|---|---|
| 2.1 | Which trainer delivered the most sessions this quarter? | The trainer index is found. **`L&D Team` ranks sixth and is not a person** — if that is read as a name, the placeholder marking is not doing its job |
| 2.2 | Is that trainer any good? | The scorecard, and the caveat is noticed: quality figures are computed over the *programmes* they delivered, because a survey response never says which session the respondent sat in |
| 2.3 | Compare two programmes' NPS | Both samples are read. 100% over 7 responses and 100% over 297 must not be reported as the same claim |
| 2.4 | Where is the biggest coverage gap? | The *spread* is quoted, not the 13.5% average. The MarQ Communities at 64.1% over 184 against The Address Investments at 6.3% over 1,287 — an average describes neither |
| 2.5 | 412 people enrolled and never came. Which programmes? | Funnel, drilled at the no-show stage, at the stage's own grain |
| 2.6 | Send a filtered view to a colleague | The URL is copied. If they screenshot it, the shareable-link idea has not landed |

---

## 3. L&D Director — monthly

**Never signs in.** They receive files, so their test is the email and the
attachment, and running it on a dashboard would test something they will never
do.

| # | Task | Passes when |
|---|---|---|
| 3.1 | The monthly report arrives by email with no human step | It is in the inbox on the 1st. Blocked today: there is no SMTP relay — see the caveat below |
| 3.2 | Open the attachment and read the headline figures | The DASHBOARD sheet is recognised as the one they already know |
| 3.3 | Ask what period it covers and how fresh it is | Answered from the **Provenance** sheet, without asking anybody |
| 3.4 | Ask why NPS is +83 and not 92.7% | Answered from the sheet's own note: the unit changed, and it is stated on the file |
| 3.5 | Ask how many records were excluded | Provenance sheet. Flagged and excluded are stated as two different counts, because only one of them costs a figure |
| 3.6 | Sign the reconciliation | The three checkboxes at the foot of `reconciliation.md`, and a date |

**3.1 cannot be tested today.** The generate-keep-send path is tested end to end
against a live SMTP conversation, and there is no relay to send through. Test it
with a local catcher, and record that the production leg is untested until IT
provides one — do not tick it.

---

## What a fail means

Not that the platform is wrong. Every task above has a correct answer and the
platform computes it; a fail means the person could not get to it, which is a
defect in the screen and not in the figure.

Fix the ones that block a task. Log the ones that slowed somebody down, and be
honest that a list of small frictions nobody schedules is a list of small
frictions nobody fixes.

---

## Sign-off

| Role | Ran on | Tasks unaided | Blocking issues | Accepted |
|---|---|---|---|---|
| L&D Specialist | | / 7 | | ☐ |
| L&D Manager | | / 6 | | ☐ |
| L&D Director | | / 6 | | ☐ |

| | |
|---|---|
| The monthly routine timed under 30 minutes | ☐ |
| Every blocking issue fixed and retested | ☐ |
| Accessibility pass green — see [`accessibility.md`](accessibility.md) | ☐ |
