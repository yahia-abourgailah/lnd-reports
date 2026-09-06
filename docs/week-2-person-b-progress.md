# Week 2 — Person B — progress and handover

**Repo:** `D:\The Marq\lnd-reports` · **Remote:** `github.com/yahia-abourgailah/lnd-reports`
**Branch:** `dev` (the team's integration branch — Person A's week-2 work is merged in)

---

## 1. Context: what the project is

The L&D Analytics Platform replaces a hand-assembled Excel workbook (`L&D Main Reports.xlsx`) with a **read-only** analytics platform over the company's CRM and HRIS. Ten weeks, six phases, two engineers.

The workbook has eleven provable defects (P-01…P-11) — a hardcoded headcount of 192 when HR holds 212, pivots keyed on program *title* so two different programs merge into one, NPS averaged per row when a ratio cannot be averaged. Most of the architecture exists to make each defect **structurally impossible** rather than merely fixed.

**Architectural rules that must not be broken:**

- No CRM write client, ever. Read-only is enforced by its absence.
- `raw` is append-only, enforced by database grant — `SELECT` and `INSERT`, never `UPDATE` or `DELETE`.
- `core` is a pure function of (raw + enrichment).
- Enrichment is an overlay, superseded rather than updated.
- Every ratio metric aggregates numerator and denominator separately, then divides once.
- One metric registry shared by API, exports and tests.
- Rejected on sight: dbt, a warehouse, a columnar store, a message bus, Kubernetes, GraphQL, a component library. The largest projected table is ~15,000 rows.

**Stack:** Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Alembic · PostgreSQL 16 (schemas `raw` `core` `app` `ops`) · Celery + Beat on Redis · React 18 + TS + Vite · Docker Compose · OIDC SSO only.

---

## 2. My role: Person B, week 2 — data sync and monitoring

> **Main objective:** keep the data fresh and reliable without allowing an external source failure to bring down the application.

| # | Task | Status |
|---|------|--------|
| 1 | Worker & Beat | **Done** (week 1 scaffolding, now running real jobs) |
| 2 | Incremental sync every 30 minutes | **Done** |
| 3 | Nightly full reconcile | **Not done** — the only outstanding task |
| 4 | Failure handling — backoff, circuit breaker, last-known-good | **Done** |
| 5 | Audit & logging — `sync_run`, JSON logs | **Done** |
| 6 | Alerting | **Done** |
| 7 | `/v1/freshness` | **Done** |

**Six of seven.** Task 3 is the remaining work and has an open design question — see §7.

---

## 3. What Person A delivered (merged into `dev`)

Relevant because tasks 2 and 3 sit directly on it:

| Module | What it gives us |
|---|---|
| `sources/crm/client.py` | `CrmClient.iter_programs(**filters)` — paged, with `CrmError(retryable=...)` |
| `sources/crm/models.py` | Typed models for the CRM's Learning Program Dataset |
| `sources/fixtures.py` | Record/replay of real CRM responses, so contract tests need no live CRM |
| `ingest/landing.py` | `land()`, `current()`, `history()` — append-only writes into `raw` |
| `ingest/models.py` | `RawRecord`, and the canonical `Source` / `Entity` enums |
| `ingest/hashing.py` | `payload_hash` — what makes re-landing an unchanged record a no-op |
| `api/v1/raw.py` | Read-only inspection of the raw layer |

**Q-03 is answered, in their code:** the CRM's Learning Program Dataset returns `survey`, `survey_answers[]` and `assessment_answers[]` nested inside each program. **Feedback comes from the CRM; Microsoft Forms is not a source.** Their docstring also references **BRD v1.5** — newer than the v1.0 delivery plan this work started from.

**Only programs are implemented.** Sessions, enrollments, attendance and employees have no client yet.

---

## 4. What I built

### 4.1 `ops.sync_run` — the audit trail *and* the watermark store

One row per attempt to pull one entity from one source.

**The grain is `(source, entity)`, not `source`.** The CRM alone yields programs, sessions, enrollments, attendance and evaluations, each with its own pace; one shared watermark would let a fast entity drag a slow one backwards.

**The watermark lives here**, read back as the `watermark_to` of the newest *successful* run. A separate cursor table could claim a position no successful run supports.

Guarantees enforced by the database, not by careful code:

| Constraint | Prevents |
|---|---|
| `uq_sync_run_one_active` (partial unique, `status='running'`) | Two syncs of the same entity overlapping — both would advance the watermark, leaving a **silent** gap |
| `ck_sync_run_terminal_is_finished` | A run "successful" while still in flight, or `running` with a finish time |
| `ck_sync_run_finished_after_started` | Finishing before starting |
| `ck_sync_run_counts_non_negative`, `ck_sync_run_attempts_positive` | Nonsense counts |

Three record counters (`fetched`, `written`, `deleted`), because the gap between them is the signal: if written always equals fetched, the source's `updated_at` is not trustworthy.

`skipped` is a distinct status from `failed` — the breaker declining to call attempted nothing, so it must not inflate the failure count that opened it.

### 4.2 `record_sync_run` — the recorder

```python
with record_sync_run(source, entity, mode) as run:
    ...
    run.count(fetched=..., written=...)
    run.advance_to(position)
```

- **Owns its own transactions**, separate from the work. If it shared the caller's session, a rollback would delete the record of the failure — the row you most want to keep.
- **Both timestamps come from the database.** `finished_at` uses SQL `now()`, not Python's clock, because the app and database containers don't share one. *(Found the hard way: the first tests failed with a run finishing 400µs before it started. In production a drifting NTP would do the same.)*
- **A run that didn't succeed never writes a watermark.** Also bars a backfill from writing one.
- `SyncSkipped` → recorded as `skipped`, swallowed (a breaker doing its job is not a task failure). Anything else → recorded `failed`, re-raised so Celery retries.

### 4.3 The reaper

`uq_sync_run_one_active` has a price: a SIGKILLed worker leaves a `running` row blocking that entity for ever. `reap_abandoned_runs()` closes runs older than `sync_abandoned_after_seconds` (2400s — above Celery's 30-minute hard limit, so a *slow* sync is never mistaken for a dead one). Runs automatically before every new run.

### 4.4 `GET /v1/freshness`

Authenticated, and **always 200** — a stale platform is one still serving, and a 5xx would make the endpoint useless to the badge that renders it.

`ok` → `stale` → `never_synced`, worst wins on rollup.

- **Two different questions**: `last_success_at` (when we last checked) and `data_current_to` (how far through the source's timeline we got). A sync two minutes old that only pulled to an hour-old position is fresh by one measure, behind by the other.
- **Status is about staleness only.** Fresh data with a failing last attempt still reads `ok`, with the failure in `last_attempt_status`. One badge must not mean two things.
- **`catalogue.py` declares what *should* sync**, so an entity that has never synced — the worst case — is reported rather than silently absent.

### 4.5 Backoff and the circuit breaker

**Backoff** handles a blip inside one run: geometric, capped, and **jittered** so five entities failing in the same beat tick don't retry in lockstep and hit a recovering source as one spike.

`retry_on` has no default, and there is a second gate:

```python
retry(..., retry_on=(CrmError,), retry_if=honours_retryable_flag)
```

`CrmError` covers both a 503 and a 401. Type alone would retry the 401 three times — tripling failed logins and risking a lockout. `honours_retryable_flag` defers to the exception's own verdict rather than re-deriving it.

**The breaker** handles the outage across runs:

- **State is derived from `sync_run`, not stored.** No Redis key, no breaker table — so "why was this skipped?" is answerable from the row that skipped it, and no cached state can disagree with history.
- **Scope is the source, not the entity.** If the CRM is down, all its entities are down; five breakers would each need their own timeouts to learn it. Resetting on *any* success for the source also means one flaky entity never trips it.
- **Skipped runs are not failures** — they're the breaker's own output. Counting them would make it self-latching.
- `closed` → `open` (cooldown) → `half_open` (one trial call).
- *Known limitation:* no lock on the half-open trial, so several entities may probe at once. Guarding it needs the shared state this design avoids.

### 4.6 Alerting

`alerts/rules.py` detects; `alerts/notifier.py` decides who hears about it.

Rules mostly re-read existing work — staleness from `platform_freshness`, repeated failure from `breaker_status` — so alert and dashboard cannot disagree.

| Rule | Severity |
|---|---|
| Source the breaker gave up on | critical |
| Entity past the freshness threshold | warning |
| Expected entity that has never synced | warning |
| Reconcile deleted more than the threshold | critical |

A failing source **suppresses its own entities' staleness alerts** — one actionable alert beats a cause buried under five consequences.

**`ops.alert_notification` exists for throttling.** The conditions are derived; what cannot be derived is whether anyone was told. At a 15-minute cadence a three-day outage would send 288 messages, and the result is a muted channel. New → send. Persisting → silent for 6h, then repeated with refreshed evidence. Gone → say so, set `resolved_at`.

**Resolution is a correctness requirement**, not a courtesy: clearing the row is what lets a problem alert promptly if it returns, rather than being silenced by a stale throttle.

Delivery is a `sink`; the default writes a structured log line. **Email is deliberately not wired** — it's outward-facing, SMTP belongs to week 9, and nobody has decided who receives alerts. Adding a webhook or mail sink is a function, not a redesign.

Runs on beat every 15 minutes (`lnd.alerts.evaluate`) — not a stub.

### 4.7 Integration with Person A (migration `0005`)

Two defects found when the halves met:

**The enums disagreed.** Mine assumed Forms carried feedback and spelt two entities differently (`feedback`/`evaluation`, `course_activity`/`course_completion`). Resolved by **deleting mine** and importing `Source`/`Entity` from `ingest/models.py`. `forms` is gone; `(CRM, EVALUATION)` is now in the catalogue. Migration `0005` swaps the CHECK constraints — cheap precisely because `0002` chose `VARCHAR + CHECK` over a native Postgres enum.

**`raw.source_record.sync_run_id` was a UUID.** Written before `ops.sync_run` existed, against a guess; the real key is a BigInteger. As it stood, **no raw row could point at the run that fetched it** — the join the audit trail is for. Now `BigInteger`. Deliberately *not* a foreign key: `raw` must stay writable while `ops` is maintained, and pruning audit rows must never cascade into deleting payloads.

### 4.8 The sync runner (task 2)

`sync_incremental` is no longer a stub:

```
check breaker → read watermark → fetch with backoff → land in raw → count → advance
```

**`pullers.py`** is the contract between runner and client — `CrmClient` stays unaware of watermarks, the runner unaware of HTTP, so a second source is a new puller rather than a second runner.

**The watermark is taken *before* the fetch.** A pull starting 12:00 and finishing 12:04 must resume from 12:00 — a record modified at 12:02 may or may not have been in the page already read, and advancing to 12:04 would step over it with nothing noticing. Re-reading four minutes costs nothing: landing is idempotent by content hash.

**Landing commits in its own transaction**, separate from the audit. Both must be able to fail independently.

**Filter-agnostic.** `crm_changed_since_filter` names the `filter[...]` key for "changed since". Set → the request narrows. Empty (current default) → fetch everything, and the content hash discards what hasn't changed. Both correct; the second is merely wasteful at 55 programs. See §7.

`run_all()` steps over an entity that is already in flight or that fails, so a stuck attendance sync never makes programs stale — but the failure is still recorded, so alerting sees it.

---

## 5. Files

**Mine (new):**
```
api/src/lnd/models/ops.py            SyncRun, AlertNotification, SyncMode/Status/Trigger
api/src/lnd/sync/runs.py             record_sync_run, reap_abandoned_runs, watermark_for
api/src/lnd/sync/freshness.py        platform_freshness + response models
api/src/lnd/sync/catalogue.py        EXPECTED_ENTITIES
api/src/lnd/sync/backoff.py          RetryPolicy, retry, honours_retryable_flag
api/src/lnd/sync/breaker.py          breaker_status, check_breaker
api/src/lnd/sync/pullers.py          SourcePuller protocol, CrmProgramPuller
api/src/lnd/sync/runner.py           run_sync, run_all, configured_pullers
api/src/lnd/alerts/rules.py          Alert, evaluate_alerts
api/src/lnd/alerts/notifier.py       dispatch_alerts, log_sink
api/src/lnd/api/v1/freshness.py      GET /v1/freshness
api/alembic/versions/0002_sync_run.py
api/alembic/versions/0003_alert_notification.py
api/alembic/versions/0005_unify_source_entity.py
api/tests/  test_models_ops, test_sync_run_constraints, test_sync_runs,
            test_freshness, test_backoff, test_breaker, test_alerts, test_runner
```

**Person A's files I modified — tell her:**
```
api/src/lnd/ingest/models.py    sync_run_id: UUID -> BigInteger
api/src/lnd/ingest/landing.py   land(sync_run_id=...) signature follows
api/tests/test_landing.py       the matching test change
```

**Migrations:** `0001` baseline → `0002` sync_run → `0003` alert_notification → `0004` raw landing (hers) → `0005` unify enums + sync_run_id.

---

## 6. Running it

**Full stack:**
```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d
```
→ `http://localhost:8080` · pgAdmin on `:8082` · Postgres on `127.0.0.1:5432`

**Migrations** (as the owner role, not the app role):
```bash
make migrate
```

**Tests.** Database tests skip without `TEST_DATABASE_URL`. Person A opened `db` to a non-internal network in the dev overlay, so the host can now reach it:
```bash
TEST_DATABASE_URL=postgresql+psycopg://<user>:<pw>@127.0.0.1:5432/<db> pytest
```

Or from inside the network (what I used):
```bash
docker compose -f compose.yaml -f compose.dev.yaml run --rm --no-deps --user root \
  -v "$(pwd)/api:/work" -w /work \
  -e TEST_DATABASE_URL="postgresql+psycopg://<user>:<pw>@db:5432/<db>" \
  api sh -c "/opt/venv/bin/pip install --quiet pytest pytest-cov; /opt/venv/bin/python -m pytest -q"
```
Two gotchas: the venv is at `/opt/venv` and a **login shell (`sh -l`) resets PATH and loses it** — call `/opt/venv/bin/python` directly. `--user root` because the venv isn't writable by the runtime user.

**Lint/types** need a local venv (deliberately absent from the runtime image):
```bash
python -m venv .venv && .venv/bin/pip install -e "api[dev]"
ruff check . && ruff format --check . && mypy src
```

**Current state:** full suite passes, **92% coverage** (1813 statements). `alerts/*`, `sync/breaker.py`, `sync/catalogue.py`, `models/ops.py` at 100%; `sync/runs.py` 99%; `sync/freshness.py` 97%; `sync/pullers.py` 96%; `sync/runner.py` 89%.

---

## 7. Open questions

**For Person A — these block task 3 and tune task 2:**

1. **Does the CRM accept a "changed since" filter, and what is the `filter[...]` key?** Set `crm_changed_since_filter` and the sync narrows its request; empty and it fetches all 55 programs each pass. Both work.
2. **How should the nightly reconcile record a deletion, given `raw` is append-only by grant?** You cannot mark a raw row deleted — the database refuses. A **tombstone row** (append "this key was absent at source as of T", transform treats newest-per-key as authoritative) is the only shape that satisfies both rules, but her transform has to read it. **This is what task 3 is waiting on.**

**For the mentor:**

3. **BRD v1.5** is referenced in Person A's code; this work began from the v1.0 delivery plan. Which is authoritative, and where is it?
4. **Is LinkedIn Learning in v1?** It's in the `Source` enum but has no client, and is deliberately absent from `EXPECTED_ENTITIES` so it doesn't raise a permanent `never_synced` alert.
5. **Who receives alerts, on what channel?** Currently structured logs only. A webhook or mail sink is a small addition once someone decides.
6. **`never_synced` alerts fire before go-live** — correct in principle, noisy in practice (currently 6 warnings every 6h). Suppress until launch, or leave?
7. **Database tests in CI.** They skip without `TEST_DATABASE_URL`; without a Postgres service in the workflow, roughly half the suite silently doesn't run.

---

## 8. What to do next

**Task 3 — the nightly full reconcile.** Everything it needs exists except the deletion convention (§7.2):

- `SyncMode.FULL_RECONCILE` is handled by the recorder — no window read, but may advance the watermark
- `records_deleted` is on `sync_run`, and the oversized-delete alert rule is written and tested
- Beat already has the 02:15 slot, pointing at a stub
- `run_sync(mode=FULL_RECONCILE)` already fetches everything and lands it

What's missing is the *comparison*: fetch all, diff against `current()` for that entity, and record what vanished. Roughly:

```python
seen = {source_id for source_id, _ in records}
held = {row.source_id for row in current(session, source=..., entity=...)}
vanished = held - seen          # append a tombstone for each
run.count(deleted=len(vanished))
```

Once the tombstone shape is agreed with Person A this is perhaps an hour, plus tests.

**Then:** sessions, enrollments, attendance and employee pullers as their clients arrive. Each is a new class in `pullers.py` and one line in `configured_pullers()` — nothing else changes.
