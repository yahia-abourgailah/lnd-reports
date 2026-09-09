# Runbooks

What to do when the platform tells you something is wrong, and how to check it
is telling the truth.

Every procedure here has been run against the dev stack and its real output is
quoted. A runbook nobody has executed is a document that describes a system
somebody imagined.

**Rehearsed:** 9 September 2026, against 57 CRM programmes and 1,469 active
employees. Restore-from-backup is week 10 and is deliberately absent — see the
last section.

Two things that were unproven when this was first written are proven now: the
mail relay has a preflight (§1) and single sign-on has run end to end against a
real identity provider (§6).

---

## How the platform tells you

Two channels, and they answer different questions.

| Channel | Question it answers | Where |
|---|---|---|
| **Alerts** | Has something broken since I last looked? | `ops.alert_notification`, one JSON log line per notification |
| **Exception console** | What is the platform currently unable to place? | `/exceptions` |

An alert is pushed and repeats at most every six hours
(`ALERT_RENOTIFY_SECONDS`) while the problem persists. The console is pulled and
is always the present state. Neither is a log: both resolve themselves when the
condition clears, which is what makes them worth reading.

Six alert kinds:

| Kind | Severity | Means |
|---|---|---|
| `source_failing` | critical | The CRM has failed repeatedly; the breaker may be open |
| `never_synced` | critical | An entity the catalogue expects has never been fetched |
| `data_stale` | warning | A source last succeeded longer ago than `FRESHNESS_STALE_AFTER_SECONDS` |
| `reconcile_deletes` | warning | A nightly reconcile marked more records gone than `ALERT_RECONCILE_DELETE_THRESHOLD` |
| `exclusions_ageing` | warning | Records have been missing from figures for over `ALERT_EXCLUSION_AGE_SECONDS` (7 days) |
| `report_undelivered` | critical | A period's report was generated and did not reach anybody |

---

## 1. The monthly report did not send

`report_undelivered` is **critical** on purpose. It is the only week-9 failure
that is silent by construction: no screen changes when a mail does not arrive,
and the people who would notice are the ones who did not receive it.

### Check what actually happened

The reason is stored on the edition, not in a log:

```bash
docker compose exec db psql -U lnd -d lnd -c \
  "SELECT period_year, period_month, kind, trigger, delivered_at, delivery_error
     FROM ops.export_edition ORDER BY id DESC LIMIT 6;"
```

Rehearsed output:

```
 period_year | period_month |     kind     |  trigger  | delivered_at |            delivery_error
-------------+--------------+--------------+-----------+--------------+---------------------------------------
        2026 |            6 | monthly_xlsx | scheduled |              | no SMTP host configured — the report…
```

`delivery_error` is never a bare "failed". `SMTPAuthenticationError` and
`timed out` send you to different places.

| Error | What it means | Do this |
|---|---|---|
| `no SMTP host configured` | `SMTP_HOST` is blank | Set it; the guard refuses unencrypted SMTP in production |
| `no recipients configured` | `REPORT_RECIPIENTS` is blank | Set it — comma-separated, trailing commas are ignored |
| `SMTPAuthenticationError` | Credentials rejected | Check `SMTP_USERNAME`/`SMTP_PASSWORD` against the relay |
| `timed out` / `ConnectionRefusedError` | The relay is unreachable | Network or egress allowlist; the relay must be reachable from the worker |
| `SSLCertVerificationError` | The relay's certificate does not verify | Do not disable verification. The attachment carries employee names |

### Before any of that: check the relay

Do not wait for the first of the month to find out whether delivery works.

```bash
docker compose exec worker python -m lnd.delivery.preflight
docker compose exec worker python -m lnd.delivery.preflight --send
```

The first connects, negotiates TLS and authenticates without sending anything.
The second delivers one short message to the report's real recipients. Rehearsed
against a relay:

```
  host        smtp.example.com:587
  encryption  STARTTLS
  username    lnd-analytics
  recipients  ld@example.com, director@example.com

OK   connected and authenticated — nothing was sent; add --send to deliver a test message
OK   sent to ld@example.com, director@example.com
```

Unconfigured it says so in one line rather than in a stack trace:

```
FAIL SMTP_HOST is not set, so nothing is sent and nothing can be.
```

Run it the moment IT hand over credentials, and again after any change to the
relay. It is not an endpoint on purpose: anyone able to trigger mail from a
browser can use this platform to send mail.

### Send it again — do not regenerate it

```bash
docker compose exec worker python -c \
 "from lnd.db import session_scope; from lnd.delivery import monthly; \
  print(monthly.resend(session_scope().__enter__(), year=2026, month=6).as_dict())"
```

or, through the broker:

```bash
docker compose exec worker celery -A lnd.worker.celery_app call \
  lnd.reports.resend --args='[2026, 6]'
```

Rehearsed, against a period with no editions and then a period with two:

```
no editions -> no edition was kept for 2026-11; there is nothing to re-send
2026-06 -> {'period': '2026-06', 'sent': True, 'detail': 'sent to ld@example.com'}
```

**Never re-run `lnd.reports.monthly` to fix a failed send.** It regenerates, and
a report regenerated in October over August's window is *October's* answer for
August — enrichment decisions, corrections and late CRM rows all move it. The
recipients would receive a document that differs from the one the platform
records as published. `resend` reads the stored bytes.

### Verify

```bash
curl -s --cookie jar http://localhost:8080/v1/exports/editions | jq '.editions[0]'
```

`delivered_at` set, `delivered_to` holding the addresses **as they were resolved
at send time** — which is the only way to answer "who got the March report"
after somebody joins the list in April.

---

## 2. Records are being excluded and nobody has fixed them

`exclusions_ageing` fires when a **quarantined** exception has been open longer
than seven days. Flagged exceptions never raise it: those records are in the
figures, and alerting on them would train people to ignore the alert that means
something.

### Look at the queue

`/exceptions`, or:

```bash
curl -s --cookie jar http://localhost:8080/v1/exceptions/summary
```

Rehearsed output:

```json
{"open": 3, "excluded": 0, "flagged": 3, "oldest_excluded_at": null}
```

The console groups by rule, **losses first**, and every group carries what the
rule detects, what it costs, and what fixes it. Those sentences come from
`lnd.quality.catalogue` — the same source the API and the tests read.

### Fix it, do not close it

Five of the eleven rules are fixed by authoring an enrichment row, and the
console links to the form:

| Rule | Fixed by |
|---|---|
| `identity_unresolved` | an identity mapping |
| `survey_question_unmapped` | a question map |
| `survey_option_unscored` | an option score |
| `trainer_missing` | a programme override |
| `customised_dept_missing` | a programme override |

The exception then closes **on the next transform**, because the rule is
satisfied — not because somebody ticked it off. A row closed by hand while the
data still violated the rule reopens half an hour later looking like a new
problem.

The other six cannot be fixed here. A duplicate scan is a fact about the CRM; a
walk-in is how the session ran. For those, either correct the source or dismiss
with a reason.

### Dismissing

A dismissal is a person saying "yes, and that is fine" — something no amount of
re-reading the payload can contradict — so no pass ever reopens it. The reason
is required and is kept against the dismisser's name. Rehearsed:

```
capacity_exceeded:83 -> dismissed | by ld.specialist@example.com
  | "The room genuinely took 14; capacity in the CRM is understated."
```

A transform run immediately afterwards left it dismissed: `raised: 0,
resolved: 0`.

---

## 3. The transform refuses to commit

Three invariants run before any pass is allowed to commit, and each raises
rather than warns. A `core` that has lost rows must never be served: stale and
correct beats fresh and wrong (NFR-03).

| Message begins | What it caught |
|---|---|
| `transform lost records` | The payload offered more rows than the writer handled |
| `core does not match the payloads` | An upsert overwrote instead of inserting |
| `records are excluded from figures with no exception registered` | Something is missing from a figure with nothing in the queue to say so |

The third is week 9's, and it is the one that closes the workbook's worst
defect: thirty-eight attendees present in the sheet and absent from the sector
report, with nothing anywhere saying so.

Rehearsed by breaking it deliberately inside a rolled-back transaction:

```
before: 0 underivable sessions
check passes on the real data
caught: records are excluded from figures with no exception registered;
        the pass will not commit — 1 sessions with no derivable duration, 0 exceptions open
after rollback: 0 underivable sessions
```

**What to do:** nothing urgent. The dashboard is still serving the previous
coherent `core`. Read the counts in the message — they name the grain and the
shortfall — and look at what the last pass changed. The likely causes are a
detection rule that stopped firing while the condition it detects persisted, or
a transform change that started excluding rows without raising for them. Both
are code faults, not data faults.

---

## 4. A source is failing, or data is stale

Week 2's rules, unchanged.

```bash
curl -s --cookie jar http://localhost:8080/v1/freshness | jq '.status, .sources[].entities[]'
```

The platform serves last-known-good throughout. A failed pull never triggers a
transform, the badge shows the lag, and nothing on screen silently becomes
current. If the breaker is open it closes itself after
`BREAKER_COOLDOWN_SECONDS`; there is no manual reset, deliberately — a breaker
somebody can force is a breaker somebody forces.

---

## 5. Checking the schedule is actually running

```bash
docker compose exec worker celery -A lnd.worker.celery_app inspect scheduled
docker compose logs beat --tail 20
```

Five entries, and none of them is a stub any more:

| Task | When |
|---|---|
| `lnd.sync.incremental` | every 30 minutes |
| `lnd.transform.core` | :05 and :35, five minutes behind the sync |
| `lnd.sync.full_reconcile` | 02:15 daily |
| `lnd.reports.monthly` | 07:00 on the 1st |
| `lnd.alerts.evaluate` | every 15 minutes |

The transform is five minutes behind the sync rather than chained to it, so it
still runs — and still produces a correct `core` — on a morning when the CRM is
down and no sync succeeded.

---

## 6. Single sign-on

The OIDC flow has been exercised end to end — discovery, PKCE with S256, code
exchange, RS256 signature against the provider's JWKS, issuer, audience, expiry
and nonce — against a **real identity provider** running in the test suite
(`tests/idp.py`). It also refuses what it should: a tampered state, a replayed
code, a callback with no in-flight request, and an error from the provider. None
of those issues a session.

What that does **not** prove is that Microsoft Entra is configured correctly. It
proves the client is. When the registration arrives, three settings go in:

```
OIDC_DISCOVERY_URL   https://login.microsoftonline.com/<tenant>/v2.0/.well-known/openid-configuration
OIDC_CLIENT_ID       the application (client) ID
OIDC_CLIENT_SECRET   a client secret
```

and the redirect URI registered with Entra must equal `OIDC_REDIRECT_URI`
exactly, including the scheme. The API refuses to start outside dev without all
three, which is the guard working rather than a fault.

**A failed sign-in redirects; it does not show an error page.** The browser
lands back on the application with `?auth_error=<reason>` and the detail goes to
the log — never into the query string. The reasons are `provider_error`,
`expired`, `invalid_response`, `state_mismatch`, `verification_failed` and
`unavailable`, and each names a different place to look.

To rehearse the whole flow by hand without Entra:

```bash
python -m tests.idp 9500          # a real OIDC provider on localhost
```

then point `OIDC_DISCOVERY_URL` at
`http://127.0.0.1:9500/.well-known/openid-configuration`, set the client id to
`lnd-analytics` and the secret to `test-secret`, and sign in with a browser.

---

## Not yet rehearsed

**Restore from backup.** Nightly `pg_dump -Fc` plus WAL archiving is configured;
restoring it for real is week 10 and is on the plan as an explicit exit
criterion. Configured is not proven, and this section will not claim otherwise
until somebody has done it and pasted the output here.
