# Week 2 — Person B — progress and handover

**Repo:** `D:\The Marq\lnd-reports`
**Branch:** `main` · **HEAD:** `584f033` ("feat: week 1 foundation — docker, schemas, CI, health, SSO")
**Status of this work: uncommitted.** Everything below exists in the working tree only. Nothing has been committed or pushed.

---

## 1. Context: what the project is

The L&D Analytics Platform replaces a hand-assembled Excel workbook (`L&D Main Reports.xlsx`) with a **read-only** analytics platform over the company's CRM and HRIS. Ten weeks, six phases, two engineers.

The workbook has eleven provable defects (P-01…P-11) — a hardcoded headcount of 192 when HR holds 212, pivots keyed on program *title* so two different programs merge into one, NPS averaged per row when a ratio cannot be averaged, and so on. Most of the architecture exists specifically to make each defect **structurally impossible** rather than merely fixed.

**Architectural rules that must not be broken:**

- No CRM write client, ever. Read-only is enforced by its absence.
- `raw` schema is append-only, enforced by database grant (not convention).
- `core` is a pure function of (raw + enrichment).
- Enrichment is an overlay, superseded rather than updated — never an edit to synced data.
- Every ratio metric aggregates numerator and denominator separately, then divides once.
- One metric registry shared by API, exports and tests. No parallel implementations.
- Rejected on sight: dbt, a warehouse, a columnar store, a message bus, Kubernetes, GraphQL, a component library. The largest projected table is ~15,000 rows.

**Stack:** Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Alembic · PostgreSQL 16 (schemas `raw` `core` `app` `ops`) · Celery + Beat on Redis · React 18 + TS + Vite · Docker Compose (7 services) · OIDC SSO only.

---

## 2. My role: Person B, week 2 — data sync and monitoring

> **Main objective:** keep the data fresh and reliable without allowing an external source failure to bring down the application.

Seven tasks:

1. **Worker & Beat** — beat schedules jobs, worker executes them
2. **Incremental sync every 30 minutes** — fetch only changed records, using a watermark
3. **Nightly full reconcile** — detect deletions and silent changes incremental misses
4. **Failure handling** — backoff, circuit breaker, last-known-good
5. **Audit & logging** — record every sync in `sync_run`; structured JSON logs
6. **Alerting** — repeated failures, stale data, unexpected reconcile differences
7. **`/v1/freshness`** — last successful sync and freshness per source

---

## 3. Status of each task

| # | Task | Status |
|---|------|--------|
| 1 | Worker & Beat | **Mostly done in week 1.** Containers run, schedule declared. The jobs it calls are still empty stubs that log "not implemented" |
| 2 | Incremental sync, 30 min | **Not started.** The watermark half is built and tested |
| 3 | Nightly full reconcile | **Not started.** The mode exists and the recorder handles it correctly |
| 4 | Failure handling | **Done.** Backoff, circuit breaker and last-known-good |
| 5 | Audit & logging | **Done** |
| 6 | Alerting | **Done** |
| 7 | `/v1/freshness` | **Done** |

**Nothing calls the recorder yet.** The machinery is built and proven, but the scheduled jobs remain stubs. This is deliberate — see §7. Consequently `/v1/freshness` currently reports `never_synced` for everything, which is correct: nothing has ever synced.

---

## 4. What was built

### 4.1 `ops.sync_run` — the table

One row per attempt to pull **one entity from one source**. It is simultaneously the audit trail *and* the watermark store.

**Key design decision — the grain is `(source, entity)`, not `source`.** The CRM alone yields programs, sessions, enrollments, attendance and feedback, each with its own `updated_at` and its own pace. One shared watermark would let a fast entity drag a slow one backwards, or skip it entirely.

**Key design decision — the watermark lives in this table.** It is read back as the `watermark_to` of the most recent *successful* run. A separate cursor table could claim to have caught up to 14:00 with no successful 14:00 run to support it, and then nobody can say whether the gap was real.

Columns:

| Group | Columns |
|---|---|
| What ran | `source`, `entity`, `mode`, `triggered_by`, `status` |
| When | `started_at`, `finished_at` |
| Window covered | `watermark_from`, `watermark_to` |
| What moved | `records_fetched`, `records_written`, `records_deleted` |
| How it went | `attempts`, `error_type`, `error_message` |
| Diagnostics | `task_id`, `details` (JSONB) |

Enums: `SyncSource` (crm, forms, hris, linkedin) · `SyncEntity` (program, session, enrollment, attendance, feedback, employee, course_activity) · `SyncMode` (incremental, full_reconcile, backfill) · `SyncStatus` (running, success, failed, skipped) · `SyncTrigger` (scheduled, manual).

**Three counters, not one**, because the difference between them is the signal. `fetched − written` is how much came back unchanged — the normal shape of an incremental run. If written *equals* fetched every time, the source's `updated_at` is not trustworthy. A spike in `deleted` from a reconcile is the "unexpected difference" worth alerting on (task 6).

**`skipped` is not `failed`.** The circuit breaker declining to call a source it believes is down attempted nothing, so it must not inflate the consecutive-failure count that opened the breaker in the first place.

**Non-native enums** (`VARCHAR(32)` + `CHECK`) rather than PostgreSQL enum types. `ALTER TYPE ... ADD VALUE` has transaction restrictions that fight migrations run as a single transactional one-shot container; a CHECK gives the same guarantee and extending it is an ordinary constraint swap. The Python side still types as the enum, so mypy rejects a wrong string before the database sees it.

### 4.2 Guarantees enforced by the database

These are the point of the table — certain wrong states cannot be written down.

| Constraint | What it prevents |
|---|---|
| `uq_sync_run_one_active` (partial unique on `source, entity` where `status='running'`) | Two syncs of the same data overlapping. Both would advance the watermark, leaving a window nobody pulled — a **silent** gap |
| `ck_sync_run_terminal_is_finished` — `(status='running') = (finished_at IS NULL)` | A run recorded as successful while still claiming to be in flight, or sitting `running` with a finish time |
| `ck_sync_run_finished_after_started` | A run that finished before it began |
| `ck_sync_run_counts_non_negative` | Negative record counts |
| `ck_sync_run_attempts_positive` | An attempt count below 1 |
| `sync_source`, `sync_entity`, `sync_mode`, `sync_status`, `sync_trigger` CHECKs | Unknown values, including from raw SQL that bypasses the ORM |

Two supporting indexes:
- `ix_sync_run_last_success` — partial on `status='success'`, covering `(source, entity, finished_at)`. Serves both the watermark read and `/v1/freshness`. No DESC needed: PostgreSQL walks a btree backwards at the same cost as forwards.
- `ix_sync_run_started_at` — the operator's "what has this been doing lately" view.

### 4.3 `record_sync_run()` — the recorder

```python
with record_sync_run(SyncSource.CRM, SyncEntity.PROGRAM, SyncMode.INCREMENTAL) as run:
    page = client.programs(changed_since=run.watermark_from)
    run.count(fetched=len(page), written=append_to_raw(page))
    run.advance_to(page.high_water_mark)
```

Behaviour:
- Reaps abandoned runs for this `(source, entity)`, then reads the watermark, then inserts the `running` row — and **commits**, before the body starts.
- On clean exit: closes as `success`.
- On `SyncSkipped`: closes as `skipped` and **swallows** the exception. A breaker doing its job is not a task failure and must not trigger a Celery retry against a source known to be down.
- On any other exception: closes as `failed` and **re-raises**, so Celery still sees it and applies its retry policy.
- Raises `SyncAlreadyRunning` (not a raw `IntegrityError`) if another worker holds this entity.

**Three decisions worth understanding:**

1. **The recorder owns its own transactions, separate from the work.** If it shared the caller's session, a rollback of the failed data write would take the record of the failure with it — the one row you most want to survive. Committing the `running` row immediately is also what makes the unique index able to block an overlap, and what leaves something behind to reap when a worker is killed.

2. **Both timestamps come from the database.** `started_at` is a server default; `finished_at` is set with SQL `now()`, not Python's clock. The app container and the database container do not share a clock. *(This was found the hard way — the first version of the tests failed because a Python-computed `finished_at` preceded the database-stamped `started_at` by ~400µs. In production a drifting NTP would do the same thing and take out every sync.)*

3. **A run that did not succeed never writes a watermark.** The read query already filters to successful runs, but declining to write the column at all means a failure cannot advance the position even if a future query forgets that filter. The same rule bars a `backfill` from writing one — loading Feb–Aug 2026 must not drag the live position back half a year. Counts *are* written whatever happened: 50 of 100 records is the difference between a flaky source and one refusing outright.

### 4.4 The reaper

`reap_abandoned_runs()` closes any run still `running` past a threshold, marking it `failed` with `error_type='AbandonedRun'`.

This exists because `uq_sync_run_one_active` has a price: a SIGKILLed worker leaves an orphaned `running` row that blocks that entity permanently. The threshold defaults to **2400s (40 min)**, deliberately above Celery's 30-minute hard `task_time_limit`, so a merely *slow* sync is never mistaken for a dead one.

It runs automatically before every new run, scoped to that source and entity. It can also be called unscoped for a periodic sweep.

### 4.5 `GET /v1/freshness`

Authenticated (unlike `/v1/health`, which a load balancer must reach before anyone signs in — freshness describes internal systems). **Always 200**: a stale platform is one that is still serving, deliberately, and turning staleness into an error status would make the endpoint useless to the badge that has to render it.

Reports per entity, grouped by source, with a rollup where **worst wins**:

| Status | Meaning |
|---|---|
| `ok` | Last successful sync is inside the threshold |
| `stale` | Last successful sync is older than the threshold |
| `never_synced` | No successful sync has ever happened — outranks `stale`, because no data is a bigger problem than old data |

**Two different questions are answered per entity, deliberately.** `last_success_at` is when we last successfully checked; `data_current_to` is how far through the source's timeline that check got (its watermark). A sync that ran two minutes ago but only pulled to an hour-old position is fresh by one measure and behind by the other. `lag_seconds` is measured from `last_success_at` — that is what the badge is about, whether the pipeline is running.

**`status` is about staleness only.** An entity whose data is fresh but whose most recent attempt failed still reads `ok`, with the failure visible in `last_attempt_status`. Folding the two together would make one badge mean two things and leave nobody able to act on it. Repeated-failure alerting is a separate rule (task 6) over the same table.

The response echoes `stale_after_seconds` so the client does not hardcode the threshold in a second place.

**The catalogue** (`lnd/sync/catalogue.py`) declares what *should* be syncing. Reporting only what appears in `sync_run` would make an entity that has never synced — the worst case — the one case the endpoint stays silent about. The answer is the union of declared and observed, so an undeclared pair with history still appears rather than being hidden. `feedback` and LinkedIn are deliberately not declared yet (Q-03 and the scope question in §8).

### 4.6 Backoff and the circuit breaker

Two defences at different scales, deliberately separate because the right response differs.

**Backoff** (`lnd/sync/backoff.py`) handles the blip inside one run — a dropped connection, one request that timed out. Delays grow geometrically, are capped so a slow source costs a bounded slice of the sync window, and are **jittered**: without it, five entities that failed in the same beat tick retry at the same instant, three times over, and hit a recovering source as one spike each time.

`retry_on` has **no default**, on purpose. A 500 or a timeout deserves another go; a 401 does not and will not until someone rotates a credential — retrying it triples the failed logins, delays the honest error by the whole backoff budget, and can lock the account. The caller names what is transient; everything else propagates on the first raise. The original exception is re-raised rather than wrapped, so `record_sync_run` stamps the source's own error type.

**The circuit breaker** (`lnd/sync/breaker.py`) handles the outage across runs. Once a source has failed enough times in a row, calling it every thirty minutes is just waiting for a timeout on a schedule.

| Decision | Why |
|---|---|
| **State is derived, not stored** — a query over `sync_run`, no Redis key, no breaker table | The audit trail and the breaker become the same fact, so "why was this skipped?" is answerable from the row that skipped it. No cached state to disagree with history, expire at the wrong moment, or vanish on restart. Costs one query of ≤50 rows per attempt |
| **Scope is the source, not the entity** | If the CRM is down, all five CRM entities are down. Five independent breakers would each need their own timeouts to learn the same fact. Resetting on *any* success for the source also gets the converse right: one flaky entity among four healthy ones never trips it |
| **Skipped runs are not failures** | They are the breaker's own output. Counting them would make it self-latching — it opens, every run is skipped, the skips count as failures, and it never closes again |
| **Running runs are not failures either** | They have no outcome yet |

States: `closed` (calls pass) → `open` (cooldown not elapsed; the run is recorded `skipped`) → `half_open` (cooldown elapsed; one call allowed to prove it). A trial failure restarts the cooldown; a trial success closes it.

Known limitation, accepted: there is **no lock on the half-open trial**. Several entities of one source can enter it in the same beat tick and each make a call, so a source that is still down gets a handful of probes rather than exactly one. Guarding it would need the shared state this design exists to avoid, for a saving of a few requests per cooldown.

Usage — `check_breaker` belongs in the shared sync runner, written once, so no source client can forget it:

```python
with record_sync_run(CRM, PROGRAM, INCREMENTAL) as run:
    status = check_breaker(CRM)     # raises SyncSkipped if open
    run.note(**status.as_details()) # what tripped it, and when it lifts
    ...
```

### 4.7 Alerting

Split in two: `alerts/rules.py` detects, `alerts/notifier.py` decides who hears about it and how often.

**The rules mostly re-read existing work.** Staleness comes from `platform_freshness` — the same function behind the badge, so the alert and the screen can never disagree about whether something is stale. Repeated failure is `breaker_status` — an open breaker *is* the alert condition. Recomputing either would create a second definition that could drift, which is the mistake the workbook made with its nine unauditable KPI formulas.

| Rule | Severity | Key |
|---|---|---|
| A source the breaker has given up on | critical | `source_failing:{source}` |
| An entity behind the freshness threshold | warning | `data_stale:{source}:{entity}` |
| An expected entity that has never synced | warning | `never_synced:{source}:{entity}` |
| A nightly reconcile that deleted more than the threshold | critical | `reconcile_deletes:{run_id}` |

**A failing source suppresses its own entities' staleness alerts.** If the CRM is down, its five entities are all going stale — reporting six problems where there is one buries the cause under its consequences. The source-level alert is the actionable one.

**Throttling is why `ops.alert_notification` exists.** The conditions are derived, like the breaker; what cannot be derived is whether anyone has been told. Evaluated every 15 minutes, a three-day outage would send 288 messages, and the practical result of that is a muted channel and the next real alert going unread.

- **New problem** → send, open a row
- **Same problem, still there** → silent until `alert_renotify_seconds` (6h), then repeated once with refreshed evidence
- **Problem gone** → say so, set `resolved_at`

**Resolution is a correctness requirement, not a courtesy.** Clearing the row is what lets a problem alert *promptly* if it returns: without it, a source that fails, recovers, and fails again twenty minutes later would be silenced by the throttle left over from the first failure. A partial unique index (`uq_alert_notification_live`) enforces one live row per key while letting the key recur through history.

**Delivery is a `sink`; the default writes a structured log line.** Sending email is deliberately not wired in: it is an outward-facing action, the SMTP path belongs to week-9 report delivery, and a log line is what the container runtime already collects and monitoring already watches (NFR-08). Severity maps to log level so an existing log-based monitor can route on it without understanding this module. Adding a webhook or mail sink is a function, not a redesign.

`lnd.alerts.evaluate` runs on beat every 15 minutes — **not a stub**. It does real work today and will keep doing it unchanged once the sources are connected. Repetition is the notifier's job, not the schedule's, so it can run as often as is useful without multiplying messages.

### 4.8 New settings

```python
sync_overlap_seconds: int = 300       # re-read 5 min for source clock skew
sync_abandoned_after_seconds: int = 2400
freshness_stale_after_seconds: int = 3600   # two missed 30-minute runs

retry_attempts: int = 3
retry_base_delay_seconds: float = 1.0
retry_max_delay_seconds: float = 30.0
retry_jitter: float = 0.25                  # spread, to avoid a thundering herd

breaker_failure_threshold: int = 3          # consecutive failures per source
breaker_cooldown_seconds: int = 600

alert_renotify_seconds: int = 21600         # 6h — the anti-muting knob
alert_reconcile_delete_threshold: int = 25
alert_reconcile_window_seconds: int = 86400
```

The overlap exists because source clocks are not our clock, and a record written at the exact moment of the last watermark must not fall between two windows. Re-reading a few minutes is free — the transform is idempotent (FR-A10).

---

## 5. Files

**New:**
```
api/src/lnd/models/__init__.py          package; re-exports the ops models
api/src/lnd/models/ops.py               SyncRun + the five enums
api/src/lnd/sync/__init__.py            package; public surface of the sync layer
api/src/lnd/sync/runs.py                record_sync_run, reap_abandoned_runs,
                                        last_successful_run, watermark_for,
                                        SyncSkipped, SyncAlreadyRunning
api/src/lnd/sync/backoff.py             RetryPolicy, retry
api/src/lnd/sync/breaker.py             BreakerState, BreakerStatus,
                                        breaker_status, check_breaker
api/src/lnd/sync/catalogue.py           EXPECTED_ENTITIES, ordering_key
api/src/lnd/sync/freshness.py           platform_freshness + the response models
api/src/lnd/api/v1/freshness.py         the endpoint (thin)
api/src/lnd/alerts/rules.py             Alert, evaluate_alerts
api/src/lnd/alerts/notifier.py          dispatch_alerts, log_sink, DispatchResult
api/alembic/versions/0002_sync_run.py   the migration
api/alembic/versions/0003_alert_notification.py
api/tests/test_models_ops.py            20 tests, no database needed
api/tests/test_sync_run_constraints.py  23 tests, real PostgreSQL
api/tests/test_sync_runs.py             23 tests, real PostgreSQL
api/tests/test_freshness.py             19 tests, real PostgreSQL
api/tests/test_backoff.py               12 tests, no database needed
api/tests/test_breaker.py               18 tests, real PostgreSQL
api/tests/test_alerts.py                29 tests, real PostgreSQL
docs/week-2-person-b-progress.md        this file
```

**Modified:**
```
api/alembic/env.py             imports lnd.models so autogenerate sees the tables
api/src/lnd/config.py          the three sync/freshness settings
api/src/lnd/api/v1/__init__.py mounts the freshness router
api/src/lnd/worker/celery_app.py  the alerting task and its beat entry
api/tests/conftest.py          db_engine, db_connection and live_db fixtures
```

The freshness *computation* lives in `lnd/sync/`, not in the API layer, so task 6 (alerting) can reuse it without going through HTTP. The router is four lines.

`api/src/lnd/models.py` (a flat draft file) was replaced by the `models/` package — Person A adds a dozen `dim_*`/`fact_*` models in week 3, and a single shared file would be a merge-conflict factory.

Note: `0002` issues no grants. `0001` set `ALTER DEFAULT PRIVILEGES` on `ops` for `lnd_app`, so the new table and its sequence were covered automatically (verified).

---

## 6. Verification

- **144 new tests**, all passing. Coverage 89.0% overall; `alerts/rules.py`, `alerts/notifier.py` and `sync/breaker.py` all 100%, `sync/runs.py` 99%, `sync/freshness.py` and `models/ops.py` 97%, `sync/backoff.py` 96%.
- The alerting task run for real in the worker container: 5 raised, then 5 suppressed on immediate re-run, then 1 resolved after an entity synced — with the notification history left in `ops.alert_notification`.
- `/v1/freshness` exercised end to end against the running stack: 401 unauthenticated, then 200 with a planted history showing `ok`, `stale` and `never_synced` side by side.
- `ruff check`, `ruff format --check`, `mypy src` — all clean.
- Migration applied to the dev database; table structure inspected by eye.
- Every constraint proved by hand in `psql` before being encoded as a test.
- Database tests roll back or clean up by id — the dev database has **0 rows** left behind.

### How to run the tests — important quirk

`compose.yaml` declares the `backend` network as `internal: true`, and `db`/`redis` sit only on it. **Docker silently ignores port publishing for a container solely on an internal network** — so the `ports:` entries in `compose.dev.yaml` for `db` and `redis` have never worked, despite their comment. Postgres is *not* reachable from the host.

So the database tests run from *inside* the network:

```bash
docker compose -f compose.yaml -f compose.dev.yaml run --rm --no-deps --user root \
  -v "/path/to/api:/work" -w /work \
  -e TEST_DATABASE_URL="postgresql+psycopg://<user>:<pw>@db:5432/<db>" \
  api sh -c "/opt/venv/bin/pip install --quiet pytest pytest-cov; /opt/venv/bin/python -m pytest -q"
```

Two gotchas: the venv is at `/opt/venv` and a **login shell (`sh -l`) resets PATH and loses it** — call `/opt/venv/bin/python` directly. And `--user root` is needed because the venv isn't writable by the non-root runtime user.

Without `TEST_DATABASE_URL`, the database tests **skip** rather than fail, so `pytest` still works on a machine with nothing running. The fixture sets `connect_timeout=5`; without it psycopg waits forever on a dead port and the suite hangs.

`ruff` and `mypy` are not in the runtime image (deliberately — no build toolchain in the runtime layer), so they need a local venv:

```bash
python -m venv .venv && .venv/bin/pip install -e "api[dev]"
```

---

## 7. Decisions deliberately deferred

**The Celery stubs were left alone.** Wiring `sync_incremental` to the recorder now would write `success` rows that fetched nothing — and `/v1/freshness` would then report everything as fresh while nothing actually syncs. That is worse than an obviously-empty stub. They get wired when a real source client exists.

**No scheduled reaper task.** The recorder reaps before each run and beat fires every 30 minutes, so it self-heals. A standalone beat entry only helps for an entity that never runs again.

---

## 8. Open issues

1. **`compose.dev.yaml` cannot publish db or redis** (see §6). Options: attach `db` to a non-internal network in the dev overlay only (gives it a default route in dev), or drop those `ports:` blocks and keep access via `docker compose exec`. CI is unaffected — a service container there is not on an internal network. **Undecided.**

2. **The stack must be started with the dev overlay.** It was found running from `compose.yaml` alone, so no ports were published at all and `http://localhost:8080` did not work. Use `make up`, or `docker compose -f compose.yaml -f compose.dev.yaml up -d`. Note that compose only *restarts* containers when a port mapping is added — `--force-recreate` is needed for the change to take effect.

3. **The `lnd_app_rw` login role was missing from the dev database — now fixed.** `docker/db/01-app-role.sh` creates it, but `docker-entrypoint-initdb.d` scripts run **only on first initialisation of an empty data volume**. The volume already existed, so the role was never created and the API could not connect at all: `/v1/health` had been reporting `"database": "error"` unnoticed, because the container healthcheck only hits `/v1/health/live`, which deliberately ignores the database. Repaired by running the script's SQL by hand (create role, `GRANT CONNECT`, `GRANT lnd_app`). **Anyone else whose volume predates the role will hit the same thing** — `make nuke` and a rebuild also fixes it, at the cost of the data.

4. **BRD version drift.** The delivery plan followed here is v1.0 (30 Aug 2026). The repo README references BRD v1.1, a `docs/` directory that did not exist, Microsoft Forms and LinkedIn Learning as sources (v1.0 puts LinkedIn out of scope), and a "Q-14" not present in v1.0's Q-01…Q-05. The Person B task brief names all four sources, so the model supports all four. **Which document is authoritative is unresolved.**

5. **Q-03 is still open** — whether feedback arrives from the CRM or from Microsoft Forms. The table stores the `(source, entity)` pair and does not hardcode which source owns which entity, so the answer is configuration, not a migration. It is also why `feedback` is absent from `EXPECTED_ENTITIES`: declaring the wrong source would report a permanent `never_synced` against a source that was never meant to carry it.

---

## 9. What to build next

**All seven tasks are done bar the two that need a source client.** What remains is not Person B work:

| Outstanding | Owner |
|---|---|
| CRM and HRIS clients, then tasks 2 and 3 | Person A, week 2 |
| Report-not-delivered alert rule | Week 9, with report scheduling |
| Transform invariant failure alert | Week 3, Person A |
| An email or webhook alert sink, if logs are not enough | Whenever ops asks — a function, not a redesign |

Tasks 2 and 3 remain blocked on Person A's week-2 CRM/HRIS clients. Once one exists, the incremental job is thin, and every piece it needs is now built and tested:

```python
with record_sync_run(source, entity, INCREMENTAL) as run:
    status = check_breaker(source)
    run.note(**status.as_details())
    page = retry(lambda: client.fetch(since=run.watermark_from),
                 retry_on=TRANSIENT, describe=f"{source}.{entity}")
    run.count(fetched=len(page), written=append_to_raw(page))
    run.advance_to(page.high_water_mark)
```

That is also the point at which the beat stubs get wired and `/v1/freshness` starts showing real data. **The `check_breaker` call belongs in that shared runner, written once** — not in each client, where it can be forgotten.

Once a source client exists, the incremental job (task 2) is thin: ask the recorder where to resume, call the client, count what came back, advance the position.
