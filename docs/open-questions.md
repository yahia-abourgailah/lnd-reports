# Three messages to send

Everything blocking weeks 3 and 4, written to be copied and sent as-is.
Every number below is measured from the live CRM, not estimated.

---

## 1 → CRM team

> **Subject: Learning Program Dataset API — three follow-ups**
>
> Thanks for adding sector and job level; both landed and we are already
> reporting on them. Three things left.
>
> **1. We need an employees endpoint.**
>
> The dataset endpoint returns the people who have *touched* a program — 417 of
> them, each with their `company`. That is a complete numerator, and it is
> exactly right for attendance.
>
> What is missing is the denominator. An employee who has never attended
> anything does not appear in the payload at all, so we cannot see that they
> exist. Participation rate is `trained / eligible`, and we can currently
> compute only the top half:
>
> | Company | Trained | Total staff |
> |---|---:|---:|
> | The Address Investments | 224 | ? |
> | The MarQ Communities | 167 | ? |
> | The Address Developments | 13 | ? |
> | The Address Holding | 10 | ? |
> | Eclatic Cosmetics | 3 | ? |
>
> If The Address Investments has 250 staff that is 90%. If it has 2,500 it is
> 9%. Same numerator, ten times the difference — so the figure cannot be
> published at all until the right-hand column exists.
>
> **What would solve it, in order of preference:**
>
> - **Best** — `GET /api/learning-integration/employees`, returning every
>   active employee in the same shape as the `user` object you already build:
>   `employee_code`, `odoo_id`, `full_name`, `company`, `department`, `sector`,
>   `position`, `job_level_name`, `job_level_grade`, `status`. Paged like the
>   programs endpoint. This also gives us the coverage report — "who has had no
>   training this year" — which is a list of names we simply cannot derive from
>   attendance data.
> - **Enough for now** — a count of active employees per company. Five numbers.
>   That alone unblocks participation rate, and the coverage report can wait.
> - **Acceptable** — a scheduled CSV with the same columns.
>
> We are not asking for anything sensitive: no salary, no national ID, no
> banking. Exactly the fields the `user` object already carries.
>
> **Why we cannot solve this ourselves:** L&D's headcount sheet holds 212
> employees of one company. We checked it against the CRM — **272 of the 417
> people who have actually trained are not in that sheet**, and they come from
> five different companies. It cannot serve as the denominator, which is why
> the ask has to come to you.
>
> **2. Could `sessions[].trainer` return a user object with `employee_code`, as
> `attendance[].user` already does?**
>
> The trainer is the only person in the payload that arrives as a typed name
> rather than a record. The cost is measurable: `Ahmed Elshiaty` (10 sessions)
> and `Ahmed ElShiaty` (4) are one person counted twice, so 14 sessions and 158
> attendances are split across two rows in every trainer report, with his
> quality scores computed over two smaller samples.
>
> We cannot fix this our side. Matching on name resolves only 6 of 16 trainers —
> it cannot see `Mohamed Rashad` at all, at 23 sessions, because he has never
> attended a program, and it wrongly matches `Ahmed Sobhy` to
> `Mohamed Sobhy Ahmed` because four-part names repeat given names.
>
> **3. Two small data-quality fixes**
>
> - `user.sector` arrives with a trailing space on **941 of 1,053** records, so
>   `"Finance "` and `"Finance"` read as two sectors. 28 distinct values become
>   23 once trimmed.
> - `user.job_level_grade` arrives as text, so `"10"` sorts before `"9"` and
>   ordering by seniority breaks.
>
> We normalise both on ingest, so neither is urgent — but trimming at source
> means nobody downstream has to remember.

---

## 2 → L&D

> **Subject: Four questions about the CRM training data**
>
> We are now reading the CRM directly and can see the whole picture — 57
> programs, 123 sessions, 417 people. Four things only you can settle.
>
> **1. `L&D Team` is recorded as the trainer on 10 sessions.** Is that a real
> delivery unit, or does it mean "not yet assigned"? It currently ranks as the
> third-busiest trainer, above four named people. If it means unassigned, those
> sessions should drop out of trainer scorecards and appear in the data-quality
> queue instead.
>
> **2. Three sessions name two trainers** — `Aya Sameh & Amr Alaa`,
> `Hagar & Amr`, `Hussam & Zeyad`. Should one be recorded as the primary
> trainer, or do you want co-delivery tracked properly? Three sessions is a
> small number, so we would suggest a primary unless co-delivery is becoming
> common.
>
> **3. Does the CRM's Public / Department flag match your Public Calendar
> versus Customised split?** The CRM says 50 public and 7 department. If those
> line up with how you classify programs, we can drop a screen from the plan and
> you will never have to maintain that field by hand. If they do not, tell us
> what the difference is.
>
> **4. Job level is missing for 74 people, about one in ten.** Expected, or
> should HR be filling those in? Those people will not appear in any
> "participation by seniority" breakdown.
>
> Also, for the record: `Belton Academy` appears as a trainer on 4 sessions. We
> will mark it as an external vendor rather than a person, so it does not sit in
> the internal trainer league table. Say if you would rather it appeared.

---

## 3 → L&D Director

> **Subject: Two decisions needed before the numbers are presented**
>
> The platform now computes every published figure from the CRM. Before we put
> old against new, two decisions are yours.
>
> **1. NPS is changing units, not just value.**
>
> The workbook reports **92.7%**. That is a percentage. Real NPS runs from −100
> to +100, and on the CRM's data ours is **+88** — 273 promoters, 24 passives, 6
> detractors of 303 responses.
>
> These two numbers are not comparable, and if they are shown side by side
> without explanation it reads as a fall from 92.7 to 88. It is not. Nothing
> about satisfaction has changed; the earlier figure was a different calculation
> on a different scale.
>
> We would like to present it as: "NPS is now reported on the standard −100 to
> +100 scale and stands at +88, which is a strong score." Please confirm, or
> tell us how you would rather frame it.
>
> **2. Which period does the comparison cover?**
>
> The workbook covers February to August 2026. The CRM holds sessions back to
> September 2025. We can either compare like for like on Feb–Aug, or restate the
> full period the CRM knows about.
>
> Like for like is the honest comparison and we would recommend it. Restating
> the wider period makes almost every total go up, which is true but makes the
> comparison harder to read.
>
> **For information:** the CRM holds **57** Learning Programs. The workbook
> reports 25. That gap is the single biggest change coming, and it moves every
> total upwards.

---

## Why the first one matters most

Everything else has a workaround. Question 1 to the CRM team does not.

Participation rate was wrong twice over in the workbook: divided by a hardcoded
192, and divided by a roster covering one company while attendees span five. We
can fix the first. The second needs a population the CRM can enumerate — and
until it can, participation rate cannot be published at all.

It is also the most politically visible number in the reconciliation.
