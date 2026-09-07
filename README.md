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
    api/v1/             health, auth, freshness, raw, kpis, drill, enrichment
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
  alembic/versions/     0001 schemas → 0010 dashboard indexes

web/src/
  filters.ts            the global filter state, held in the URL
  components/           KPI cards, filter bar, freshness badge, drill drawer,
                        record grid, sparklines, enrichment screen

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

## Where it stands

Weeks 1–6 are built and verified against the live CRM. What remains before an
L&D specialist can use this unaided:

- **The Microsoft Entra app registration.** Three blank settings, and the API
  refuses to start outside dev without them. Nothing else blocks staging.
- **The L&D walkthrough.** Every figure computes and every difference has a
  written reason; nobody outside the team has seen 9.3% yet, and the plan is
  explicit that it should not arrive alongside a dashboard.
- **Two answers from the CRM team.** A trainer `employee_code`, so 16 spellings
  stop needing an alias table; and `sector` still arrives with a trailing space
  on 948 of 1,060 rows in the programs payload, though `get_users` is now clean.
- **One from L&D.** History starts 2025-07-29, not September 2025 as the plan
  records — 68 of 123 sessions predate the documented start. Real deliveries, or
  data loaded during the CRM's own build?
