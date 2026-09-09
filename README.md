# L&D Analytics Platform

Read-only analytics over the company CRM, replacing `L&D Main Reports.xlsx`.
Nothing here writes to any source system: there is no CRM write client in the
codebase, and the credentials are issued read-only.

The CRM is the single source. It was not meant to be — the plan named Microsoft
Forms for feedback and an HRIS for the employee roster — but reading the real
payloads closed both. Feedback already arrives on the programs endpoint as
`survey` and `survey_answers[]`, and `user` carries company, department, sector,
position and job level, so neither integration is needed. See
[`docs/crm-api-field-inventory.md`](docs/crm-api-field-inventory.md).

Specification: [`docs/`](docs/) — BRD v1.1 and the delivery plan.

---

## Run it

```bash
make init     # writes .env from the template, with a generated SESSION_SECRET
make up       # builds, starts all seven services, applies migrations
```

→ <http://localhost:8080> — the dashboard, with the corrected numbers.

`AUTH_DEV_BYPASS=true` is set in the template, so you can sign in before the
Microsoft Entra app registration exists. It is refused at startup in any
environment other than `dev` — the process will not boot, which is the point:
authentication quietly disabled would be worse than a service that will not
start.

```bash
make logs S=api      # tail one service
make check           # everything CI runs on the API
make web-check       # typecheck and build the front end
make evidence        # regenerate the week-10 evidence documents
make psql            # a shell on the database
make down            # stop;  make nuke  also drops the volumes
```

### What updates when

Data moves on a thirty-minute cycle, in two steps five minutes apart:

```
:00 and :30   sync       fetch from the CRM into raw
:05 and :35   transform  rebuild core from raw, then clear the cache
02:15         reconcile  a full pass — only this may conclude a record was deleted
```

A change made in the CRM at 10:07 reaches the dashboard around 10:35. The
transform is a separate job from the sync on purpose: it reads only `raw`, so it
still runs and still produces a correct `core` on a morning when the CRM is down
and no sync succeeded at all.

Nothing is precomputed. A filter nobody has used before is computed on request —
cold about 240ms, warm about 56ms.

### No `make`?

`'make' is not recognized` means it is not installed — it ships with macOS and
Linux but not Windows, and Git Bash does not include it. `winget install
GnuWin32.Make` and reopen the terminal, or work from inside WSL.

Without it, note that plain `docker compose up` is **not** equivalent: there is
no `compose.override.yaml` here, so it loads `compose.yaml` alone — nothing
publishes a port, your source is not mounted, and no migrations run. Every
container reports healthy and nothing works. Both files, every time:

```bash
copy .env.example .env
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
docker compose -f compose.yaml -f compose.dev.yaml run --rm --no-deps api alembic upgrade head
```

Copying `.env` by hand skips the secret generation `make init` does, so open it
and replace `SESSION_SECRET`, `POSTGRES_PASSWORD` and `APP_DB_PASSWORD` with
real values before starting.

---

## Shape

```
compose.yaml            seven services, no ports, no mounts
compose.dev.yaml        + published ports, bind mounts, hot reload
compose.staging.yaml    + pulled images, resource limits
compose.prod.yaml       + TLS on 443, WAL archiving, larger pools

docker/
  api.Dockerfile        two stages; no build toolchain in the runtime layer
  web.Dockerfile        Node build → unprivileged nginx
  nginx/                web.conf, proxy.dev.conf, proxy.tls.conf
  db/01-app-role.sh     creates the least-privilege login role

api/
  src/lnd/
    config.py           settings from environment variables, with startup guards
    logging.py          one line of JSON per event
    db.py               SQLAlchemy 2.0 engine; the four schema names
    middleware.py       request id, access log, security headers
    auth/               OIDC + PKCE, signed session cookie
    api/v1/             health, auth, freshness, raw, kpis, drill, coverage,
                        funnel, scorecards, learners, exports, exceptions,
                        enrichment, views
    analysis/           coverage, funnel, scorecards, learners — no arithmetic
                        of their own; every figure is the registry's
    export/             CSV, XLSX, PDF and the monthly report; retained editions
    quality/            one description per data-quality rule; completeness
                        per period, derived from the programme's own dates
    delivery/           SMTP, and the scheduled job: generate, keep, then send
    models/             SQLAlchemy tables; the shared Source and Entity enums
    sources/crm/        HTTP client and typed models for the two CRM endpoints
    ingest/             payload hashing and the append-only landing of raw
    sync/               the runner, watermarks, retry, breaker, presence
    transform/          raw → core: conform, identity, invariant, exceptions
    metrics/            the 21 KPIs, their populations, breakdowns, drill-through
    enrichment/         the app overlay — supersede, never update
    reference/          frozen dataset, golden values, reconciliation statement
    alerts/             freshness and reconcile rules, with renotify suppression
    worker/             Celery app and the beat schedule
  tests/reference/      dataset.json.gz, golden.json — the CI gate reads these
  alembic/versions/     0001 schemas → 0014 report delivery, alert kinds

web/src/
  filters.ts            the global filter state, held in the URL
  components/           KPI cards, filter bar, freshness badge, drill drawer,
                        record grid, sparklines, coverage, funnel, scorecards,
                        learners, export menu, saved views, reports, exceptions,
                        enrichment

.github/workflows/ci.yml
```

### The seven services

| Service  | Image                    | Role |
|----------|--------------------------|------|
| `proxy`  | `nginx:1.27-alpine`      | TLS, serves the SPA, proxies `/v1/`. The only published port |
| `web`    | built: Node → nginx      | The compiled React bundle |
| `api`    | built: `python:3.12-slim`| FastAPI under Gunicorn, non-root, healthchecked |
| `worker` | same image as `api`      | Celery: sync, transform, exports, email |
| `beat`   | same image as `api`      | 30-min incremental, nightly reconcile, monthly report |
| `db`     | `postgres:16-alpine`     | Internal network only, no host port |
| `redis`  | `redis:7-alpine`         | Internal network only, no host port |

`api`, `worker` and `beat` run **the same image** with different commands, so a
metric computed in a scheduled report is computed by identical code to the one
on screen.

---

## The four schemas

Migration `0001` creates them and grants the application role `SELECT, INSERT`
on `raw` — never `UPDATE` or `DELETE`.

| Schema | Holds | Application role may |
|--------|-------|----------------------|
| `raw`  | Source payloads exactly as received, `jsonb` + `payload_hash` | read, append |
| `core` | The star schema — a pure function of (raw + enrichment) | read, write |
| `app`  | Enrichment overrides and other human-authored state | read, write |
| `ops`  | `sync_run`, `dq_exception`, `alembic_version` | read, write |

Raw immutability is the thing that lets us answer "did this number arrive wrong,
or did we break it?" — so it is enforced by grant rather than by convention. An
`UPDATE raw.*` from the application fails at the database, not at review.

Migrations connect as the **owner**; `api`, `worker` and `beat` connect as
`APP_DB_USER`, which owns nothing.

---

## Authentication

Company SSO over OIDC with PKCE. No local passwords exist anywhere in the
codebase (NFR-04).

```
GET  /v1/auth/login     → redirect to the IdP
GET  /v1/auth/callback  → exchange the code, verify the ID token, set the session
POST /v1/auth/logout    → clear the session
GET  /v1/auth/me        → the signed-in user
GET  /v1/auth/status    → whether this browser is signed in (unauthenticated)
```

The ID token is verified against the provider's JWKS, with issuer, audience,
expiry and nonce all checked. The session is a signed cookie — `HttpOnly`,
`SameSite=Lax`, `Secure` outside dev — carrying only claims the IdP already
asserted. Version 1 has one L&D permission set, but the claims are kept so
row-level scoping is additive later (NFR-05).

Point `OIDC_DISCOVERY_URL` at any provider's discovery document; Entra ID, Okta
and Keycloak are configuration, not code.

---

## The metric layer

Twenty-one KPIs, each defined in exactly one place. The API, the exports and the
golden-value suite all read the same declaration, so there is no second
definition available to disagree with the first.

```
/v1/kpis                       every metric the filters allow
/v1/kpis/{key}/breakdown?by=   one metric sliced by a dimension
/v1/kpis/{key}/trend           month by month
/v1/kpis/dimensions            what the filter bar may offer
/v1/drill/{key}                the rows behind a number
/v1/enrichment/{kind}          the decisions layered over the CRM
```

The week-7 views are assemblies of those same metrics, never a second
computation of them:

```
/v1/programs · /{id}/scorecard  sessions, fill, no-show, quality, every comment
/v1/trainers · /{key}/scorecard what they delivered, and how it was received
/v1/coverage?by=                participation and the gap, sliced
/v1/coverage/untrained          the people with no training, once a scope is set
/v1/funnel · /{stage}           enrolled → attended → evaluated, and its rows
/v1/learners/top                the ranking — names once the view is narrowed
/v1/learners/{key} · /search    one person's record, and the way to find them
```

And week 8 puts them in files:

```
/v1/exports/kpis.csv|.xlsx           every figure the filters allow
/v1/exports/records/{key}.csv|.xlsx  the rows behind one figure
/v1/exports/monthly.xlsx             the workbook's DASHBOARD layout, generated
```

Every export carries a stamp: when it was generated, what filters produced it,
how fresh the data was, how many records were excluded, and the definition of
each figure in it. A dashboard figure can be re-checked against the definition
beside it; a spreadsheet quoted six months later cannot, unless the file says so
itself.

Every response carries **freshness**, the **filters applied**, and how many rows
were **excluded** or merely **flagged** — two different things that share the
data-quality queue and must never be reported as one.

**Every metric declares its population, and no filter is ever inherited.** A
metric asked to honour a dimension it does not define raises rather than
returning the unfiltered number. That, not the ratio type, is what prevents
P-03: the published NPS was not wrong because a ratio was averaged, it was wrong
because it ran over a silently filtered 55 of 77 responses.

Breakdowns re-run the metric under narrower filters rather than grouping inside
it. Slower, and correct by construction — the parts sum to the whole, and that
is a test.

### Figures that moved

Like for like, over the period the workbook actually covers — February to August
2026, which is 50 of the 123 sessions the CRM holds:

| | Workbook | Platform | Responses |
|---|---|---|---|
| Total Programs | 24 | **27** | 27 |
| Participation Rate | 60.4% | **9.3%** | 1,455 |
| NPS | 92.7% | **+83.1** | 118 |
| Logistics Effectiveness | 96.4% | **91.5%** | 118 |

**The window matters as much as the definition.** Over everything the CRM holds
the same metrics read 55 programmes, 13.8% and +88.2 — larger for reasons that
have nothing to do with correctness, because the workbook never covered July
2025 to January 2026 or September 2026. Put that column beside the workbook's
and it invites the reading that the platform discovered enormous amounts of
extra training. It did not; it can simply see more.

Participation was wrong twice over: divided by a hardcoded 192 (P-01), then by
one company's headcount while attendance spanned five (P-13). NPS changed units
as well as value — the workbook's figure was a percentage, and NPS is an index
from −100 to +100. The two are not comparable and must not be shown as if they
were.

`docs/reconciliation.md` is generated from the registry, carries both windows,
and walks every difference with the reason for it.

### The gate

`tests/reference/` holds an anonymised frozen dataset and the expected value of
every figure at every grain. CI runs it as its own named step, so **a change
that moves a published number fails the build** — and the failure names the
metric, the old value and the new one. When a figure legitimately moves, the
golden value is edited in the same commit as the code that moved it.

---

## Health

```
/v1/health/live    process is alive          — never touches the database
/v1/health/ready   can serve a request       — proxy and deploy gating
/v1/health         per-component detail      — humans and the CI smoke test
```

Liveness deliberately ignores Postgres: a store outage degrades freshness, never
availability (NFR-03), so it must not trigger a restart loop.

---

## Deploy and roll back

1. CI builds both images, tags them with the commit SHA, scans them, pushes.
2. **Migrations run as a one-shot container first.** If they fail the deploy
   stops and the running version is untouched.
3. `compose up -d` recreates changed services. `db` and `redis` are not
   recreated.
4. **Images are built once and promoted** — what staging tested is bit-identical
   to production. Rollback re-deploys the previous SHA; migrations stay
   backward-compatible for one release, so no database downgrade is ever needed.

Secrets come from the company secret store as environment variables at deploy
time. None live in source, in an image, or in a Compose file — gitleaks runs on
every push.

---

## Deliberately not used

dbt or any warehouse tooling · a columnar store · a message bus · Kubernetes ·
a component library · GraphQL.

The largest projected table is 15,000 rows. One PostgreSQL instance carries this
for a decade; complexity here buys nothing and costs maintenance forever (R-08).

No charting library either — the sparklines are a path and an area fill, and the
record grid virtualises with a scroll offset and a slice. Both are less code to
read than the dependency they would replace.

---

## What leaves the building

Every export computes through the metric registry — never a second
implementation — and every file states its own scope before its numbers.

| Format | Where | What it carries |
|--------|-------|-----------------|
| CSV / XLSX | any view | the figures or the rows behind one, with the stamp |
| PDF | figures, programme and trainer scorecards, monthly report | the same numbers laid out to be printed or forwarded |
| Monthly XLSX | Overview | the workbook's own DASHBOARD layout, so recipients need no retraining |

The stamp is the generation time, the filters applied, the freshness, the
excluded and flagged counts, and the definition of every figure in the file. A
figure quoted six months later has no badge and no tooltip; the definition has
to be in the file or it is not enforceable.

**Editions.** Generating the monthly report keeps a copy of what was returned
(`/reports`). Regenerating August in October gives the *current* answer for
August — enrichment decisions, corrections and late CRM rows all move it — which
is usually the better number and is never the file that was sent. Only the
monthly report is retained: ad-hoc downloads are one person's question and carry
names. A regeneration whose figures are unchanged is not stored, and the digest
shown is of the figures rather than the bytes, because every export writes its
own generation time into itself.

**Saved views.** A named filter set, stored as the path and query string,
because the URL is already the filter state. Restoring one is navigation, so
there is no second representation to drift. Shared with everyone; renamed and
deleted by whoever saved it.

**PDF rendering** is fpdf2, not the WeasyPrint the plan named. WeasyPrint needs
Pango and Cairo, which `python:3.12-slim` does not have — and a PDF writer that
only runs in the container is one the test suite cannot assert. The fonts are
vendored (`api/src/lnd/export/fonts/`) so a report renders identically in dev,
in CI and in the image.

## Counted or excepted, never neither

The workbook's most dangerous behaviour was silent loss: thirty-eight attendees
had no employee code, so the sector join dropped them, so they vanished from
every sector breakdown — and nothing anywhere said so. The reports were not
wrong in a way anyone could see.

Eleven detection rules run after every transform. Each has **one** description —
what it detects, what it costs, what fixes it — read by the transform that
raises it, the API that serves the console, and the tests that hold the two
together. Five are fixed by authoring an enrichment row, and `/exceptions` links
to that form; the exception then closes **on the next transform**, because the
rule is satisfied rather than because somebody ticked it off.

Three invariants run before any pass may commit, and each raises rather than
warns — a `core` that has lost rows must never be served:

| Check | Catches |
|-------|---------|
| ledger | the payload offered more rows than the writer handled |
| against core | an upsert overwrote instead of inserting |
| no silent exclusion | something is missing from a figure with nothing in the queue to say so |

The last is counted from `core`, never from the exception table. Counting both
sides from the queue would be a tautology: the check would pass whenever the
transform forgot to raise, which is the failure it exists to catch.

## Unattended

Five scheduled tasks, none of them a stub.

| Task | When |
|------|------|
| `lnd.sync.incremental` | every 30 minutes |
| `lnd.transform.core` | :05 and :35 — five minutes behind the sync, not chained to it |
| `lnd.sync.full_reconcile` | 02:15 daily |
| `lnd.reports.monthly` | 07:00 on the 1st |
| `lnd.alerts.evaluate` | every 15 minutes |

The monthly job **generates, keeps the edition, then sends** — in that order. A
relay that is down costs a delivery rather than a report: the file stays in
`/reports`, downloadable and re-sendable, holding the numbers as they were on
the first of the month. `lnd.reports.resend` sends those stored bytes. Re-running
the report to fix a failed send would produce the *current* answer for that
month, which is a different document.

With no `SMTP_HOST` the job still generates and still keeps the edition, and
records "not sent" with the reason. A delivery that reported success because
sending was switched off is the failure discovered in April by somebody asking
why they never got March — so `report_undelivered` is a **critical** alert, and
it is the only week-9 failure that is otherwise silent: no screen changes when a
mail does not arrive.

Procedures are in [`docs/runbooks.md`](docs/runbooks.md), each one rehearsed
against the dev stack with its real output quoted.

## The week-10 evidence

Five documents, each generated from the running system rather than written, and
each able to **fail**. A launch gate whose evidence cannot come back negative is
a formality.

```bash
make parallel-run        # the live platform against the workbook
make security-review     # what we store, what we refuse, what we cannot do
make perf                # p95 at the BRD's 15,000-record ceiling
make restore-rehearsal   # back up, restore elsewhere, prove it is the same platform
make evidence            # the three that need no scratch database
cd web && npm run a11y   # WCAG 2.1 AA, every screen, against the real application
```

| Document | Says | Fails when |
|---|---|---|
| [`parallel-run.md`](docs/parallel-run.md) | Every metric three ways: workbook, signed reference, live | A difference has no written cause |
| [`report-comparison.md`](docs/report-comparison.md) | The generated report against the manual one, cell for cell | A figure is not in the cell its label claims |
| [`security-review.md`](docs/security-review.md) | Stored fields, refused fields, grants, write verbs | Anything forbidden is stored, or `raw` is writable |
| [`performance.md`](docs/performance.md) | Every view at p95 against 15,145 attendance rows | A view exceeds 2s cold, or does not return 200 |
| [`restore-rehearsal.md`](docs/restore-rehearsal.md) | A dump restored elsewhere: grains, grants, figures | A grain, a grant or a figure did not survive |

What they found, in order of how much it mattered:

- **The nightly `pg_dump` was a comment.** WAL archiving was configured and the
  base backup it recovers *from* did not exist. `scripts/backup.sh` is the
  missing half, and the restore is now rehearsed — including the check that
  matters most, which is that `raw` comes back append-only. Roles live in the
  cluster, not the dump.
- **153 accessibility violations, 150 of them one palette token.** `--ink-3` at
  3.26:1 against the surface it sits on, in every secondary line in the
  application. Two `opacity` rules did the rest. Now zero of any impact, with
  axe-core in CI.
- **The signed reconciliation is stale.** Participation Rate reads 9.2% live
  against 9.3% frozen, because the roster moved. Small, and it is the kind of
  small that costs a meeting if somebody opens the dashboard during it. See
  [`cutover.md`](docs/cutover.md) for the two ways to settle it.
- **A replay could still be pointed at a populated database.** It has happened
  twice, and both times the transform invariant was the last line of defence
  rather than the first. `replay()` now refuses before landing anything.

For people rather than for CI: [`user-guide.md`](docs/user-guide.md),
[`uat.md`](docs/uat.md) — three roles, and only one of them ever signs in — and
[`cutover.md`](docs/cutover.md).

## Where it stands

Weeks 1–10 are built and verified against the live CRM; the walkthrough,
sign-off and the retirement itself are the remaining acts, and they are
conversations rather than commits. What blocks an L&D specialist using this
unaided:

- **The Microsoft Entra app registration.** Three blank settings, and the API
  refuses to start outside dev without them. Nothing else blocks staging.
- **An SMTP relay and a recipient list.** `SMTP_HOST` and `REPORT_RECIPIENTS`
  are blank, so the monthly job generates and keeps its editions and sends
  nothing. The path is tested end to end against a real SMTP conversation; what
  is missing is a relay to point it at.
- **The L&D walkthrough.** Every figure computes and every difference has a
  written reason — now provably, since the parallel run fails on one that does
  not. Nobody outside the team has seen 9.2% yet, and the plan is explicit that
  it should not arrive alongside a dashboard.
- **HR's signature** on the security review, and the question in it that is
  genuinely open: there is no erasure path for a named individual, because `raw`
  is append-only by design. That should be raised rather than ticked.
- **Two answers from the CRM team.** A trainer `employee_code`, so 16 spellings
  stop needing an alias table; and `sector` still arrives with a trailing space
  on 948 of 1,060 rows in the programs payload, though `get_users` is now clean.
- **Two from L&D.** History starts 2025-07-29, not September 2025 as the plan
  records — 68 of 123 sessions predate the documented start: real deliveries, or
  data loaded during the CRM's own build? And the **LinkedIn Learning export** —
  its delivery mechanism and column set. Until it arrives, LinkedIn Hours,
  Blended Learner Hours and Unique Reach report *no value* rather than zero:
  there has been no measurement, which is not a measurement of none.
