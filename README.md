# L&D Analytics Platform

Read-only analytics over the CRM, Microsoft Forms, the HRIS and LinkedIn
Learning, replacing `L&D Main Reports.xlsx`. Nothing here writes to any source
system: there is no CRM write client in the codebase, and the credentials are
issued read-only.

Specification: [`docs/`](docs/) — BRD v1.1 and the delivery plan.

---

## Run it

```bash
make init     # writes .env from the template, with a generated SESSION_SECRET
make up       # builds, starts all seven services, applies migrations
```

→ <http://localhost:8080>

`AUTH_DEV_BYPASS=true` is set in the template, so you can sign in before the
Microsoft Entra app registration exists (Q-14). It is refused at startup in any
environment other than `dev` — the process will not boot.

```bash
make logs S=api      # tail one service
make check           # everything CI runs on the API
make psql            # a shell on the database
make down            # stop;  make nuke  also drops the volumes
```

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
    api/v1/             /v1/health, /v1/auth/*
    worker/             Celery app and the beat schedule
  alembic/versions/0001_baseline_schemas.py

web/                    React 18 + TS + Vite + TanStack Query

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
