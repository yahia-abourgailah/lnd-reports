# Learning Program Dataset API

Read-only access to the whole Learning dataset: programs and their tracks, every
session, every person a program touched, who attended which session, the survey
and assessment definitions, and all of the answers people gave.

**One endpoint**, returning every program with everything nested inside it. A
single program is reached by filtering the list down to it, in the same shape.

Built for machine-to-machine use. There is no login, no OAuth flow and no user
session: you authenticate with a shared key in a header.

---

## 1. Endpoint

```
GET {BASE_URL}/api/learning-integration/programs
```

`GET`, `application/json`, behind the shared key, 120 req/min per IP.

One call returns a page of programs, each with its sessions, attendance, roster
and everyone's answers. Walk the pages and you have the entire learning history.

**Want one program?** Filter the list down to it — same shape, one entry:

```
GET /api/learning-integration/programs?filter[id]=16
```

An id that matches nothing is an empty page (`programs: []`, `meta.total: 0`),
not a `404`.

## 2. Authentication

Send the key you were given in the `X-Learning-Key` header:

```
X-Learning-Key: <YOUR_SERVICE_KEY>
```

That is the only header the endpoint reads the key from. `Authorization` is
**not** accepted — it means an OAuth access token everywhere else in this API,
and putting the service key there gets you a `401 missing_service_key`.

The key is a static shared secret. It is not a JWT, it does not expire, and it
does not need refreshing. Keep it in your server-side config — never ship it to a
browser or a mobile client.

## 3. Pagination

**`page` and `per_page` walk the programs** — the app's own pagination, so
`per_page` behaves exactly as it does everywhere else in the CRM. Omit it and you
get `config('crm.pagination')`, currently **25**. One `meta` sits beside the list:

```json
"meta": {
  "current_page": 2,
  "per_page": 2,
  "total": 8,
  "last_page": 4,
  "from": 3,
  "to": 4,
  "has_more_pages": true
}
```

Loop until `has_more_pages` is `false`. `from`/`to` are `null` on an empty page.

Everything nested inside a program — `sessions`, each session's `attendance`,
and `users` — comes back **complete, as a plain array**. Those are bounded by
their parent: a program has a handful of sessions and its roster is bounded by
its capacity, so burying a `meta` block beside every one-row list would buy
nothing.

### Headers or query string

`page` and `per_page` are read from **either** — send them as headers if that
suits your client, exactly as Postman's Headers tab does:

```
X-Learning-Key: <YOUR_SERVICE_KEY>
Accept: application/json
per_page: 10
page: 2
```

or on the URL:

```
GET /api/learning-integration/programs?per_page=10&page=2
```

The query string wins if you send both, so a URL you typed is never silently
overridden by a header your client left set.

A page past the end returns **an empty `programs` array with the page you asked
for echoed back** in `current_page`, so `current_page` never lies to you.

There is **no ceiling on `per_page`** — you can pull a large page in one call if
you want to. Bear in mind each entry carries a program's whole roster and every
answer on it, so a big `per_page` is a big response; 10–25 is a comfortable
working size.

### Filtering

The endpoint runs through the CRM's own learning-programs repository, so it
accepts the same `filter[...]` contract as the internal API — which is how you
ingest a slice instead of the world, and how you fetch one program:

| Query | Effect |
|---|---|
| `filter[id]=3` | One program by id |
| `filter[title]=negotiation` | Partial, case-insensitive title match |
| `filter[track_id]=4` | Programs on one track |
| `filter[type]=internal` | `internal` / `external` |
| `filter[current_status]=completed` | `upcoming` / `running` / `completed` / `cancelled`, computed from the session dates |
| `filter[startDateBetween]=2026-01-01,2026-03-31` | Programs whose earliest session falls in the range |
| `filter[learningSessionDates.trainer_name]=mona` | Programs a trainer ran |

```
GET /programs?filter[current_status]=completed&filter[startDateBetween]=2026-01-01,2026-03-31
```

**There is no `sort` parameter.** The repository declares no allowed sorts, so
`?sort=id` is rejected with a `400`. The order is always **id descending**
(newest program first) and is stable, so paging through it is safe. Ask the CRM
team if you need another order.

## 4. Quick start

```bash
# every program, first page
curl -s \
  -H "X-Learning-Key: $LEARNING_KEY" \
  -H "Accept: application/json" \
  "https://<crm-host>/api/learning-integration/programs?per_page=5"

# one program
curl -s \
  -H "X-Learning-Key: $LEARNING_KEY" \
  -H "Accept: application/json" \
  "https://<crm-host>/api/learning-integration/programs?filter[id]=16"
```

Ingesting everything:

```python
import os, requests

BASE = os.environ["CRM_BASE_URL"]
session = requests.Session()
session.headers.update({
    "X-Learning-Key": os.environ["LEARNING_KEY"],
    "Accept": "application/json",
})

page = 1
while True:
    r = session.get(f"{BASE}/api/learning-integration/programs",
                    params={"page": page, "per_page": 10}, timeout=60)
    r.raise_for_status()
    body = r.json()

    for entry in body["programs"]:
        ingest(entry)          # program + statistics + sessions + users + answers

    if not body["meta"]["has_more_pages"]:
        break
    page += 1
```

### Postman

Method `GET`, URL `{{base_url}}/api/learning-integration/programs`. Under
**Headers** add `X-Learning-Key` = `<key>` and `Accept` = `application/json`,
and optionally `per_page` / `page`. Leave the **Authorization** tab set to
*No Auth* — that tab only writes an `Authorization` header, which this endpoint
ignores.

## 5. Response shape

Everything a program owns is **nested inside the program object** — its own
columns first, then `statistics`, `sessions`, `users`, `survey` and `assessment`
hanging off the same node. One tree per program, nothing to stitch together.

```json
{
  "programs": [
    {
      "id": 2,
      "title": "TAI Nader Program",
      "status": "upcoming",
      "computed_status": "completed",
      "track": null,
      "departments": [],
      "companies": [],
      "...": "the rest of the program's own columns",

      "statistics": { "...": "counts and rates" },
      "sessions":   [ { "...": "session", "attendance": [ "...rows" ] } ],
      "users":      [ "...the whole roster" ],
      "survey":     null,
      "assessment": { "...": "definition and questions" }
    }
  ],
  "meta": { "current_page": 2, "per_page": 2, "total": 8, "last_page": 4, "from": 3, "to": 4, "has_more_pages": true }
}
```

### 5.1 Full example

One program, fetched with `?filter[id]=125`:

```json
{
  "programs": [
    {
      "id": 718,
      "title": "Negotiation Essentials",
      "subtitle": "Closing with confidence",
      "description": "Two half-days on discovery and objection handling.",
      "status": "upcoming",
      "computed_status": "upcoming",
      "capacity": 20,
      "type": "internal",
      "target": "public",
      "start_date": "2026-09-10",
      "end_date": "2026-09-11",
      "created_at": "2026-09-02T16:01:23+03:00",
      "updated_at": "2026-09-02T16:01:23+03:00",
      "parent": null,
      "track": {
        "id": 17,
        "title": "Sales Excellence",
        "subtitle": "Core track",
        "description": "Foundational sales skills.",
        "status": "upcoming"
      },
      "departments": [],
      "companies": [],
      "statistics": {
        "capacity": 20,
        "sessions_count": 2,
        "enrolled_users_count": 2,
        "attended_users_count": 1,
        "enrolled_attended_users_count": 1,
        "attendance_records_count": 1,
        "attendance_rate": 50,
        "survey_respondents_count": 1,
        "survey_answers_count": 1,
        "assessment_respondents_count": 1,
        "assessment_answers_count": 1
      },
      "sessions": [
        {
          "id": 543,
          "session_date": "2026-09-10",
          "session_time_from": "09:00:00",
          "session_time_to": "13:00:00",
          "trainer_name": "Mona Saeed",
          "location": {
            "id": 15,
            "name": "Training Room A"
          },
          "attendance_count": 1,
          "attendance": [
            {
              "id": 1167,
              "user_odoo_id": "4821",
              "user": {
                "id": 38366,
                "odoo_id": "4821",
                "name": "Ahmed Kamal",
                "full_name": "Ahmed Kamal Ibrahim",
                "email": "ahmed.kamal@example.test",
                "mobile": "+201001234567",
                "employee_code": "10422",
                "status": "active",
                "department": {
                  "id": 214413,
                  "odoo_id": "77",
                  "name": "Sales"
                },
                "company": {
                  "id": 1,
                  "odoo_id": "1",
                  "name": "The Address Investments"
                },
                "position": {
                  "id": 60,
                  "name": "Senior Property Consultant"
                }
              },
              "attended_at": "2026-09-02T16:01:23+03:00"
            }
          ]
        },
        {
          "id": 544,
          "session_date": "2026-09-11",
          "session_time_from": "09:00:00",
          "session_time_to": "13:00:00",
          "trainer_name": "Mona Saeed",
          "location": {
            "id": 15,
            "name": "Training Room A"
          },
          "attendance_count": 0,
          "attendance": []
        }
      ],
      "users": [
        {
          "user_odoo_id": "4821",
          "user": {
            "id": 38366,
            "odoo_id": "4821",
            "name": "Ahmed Kamal",
            "full_name": "Ahmed Kamal Ibrahim",
            "email": "ahmed.kamal@example.test",
            "mobile": "+201001234567",
            "employee_code": "10422",
            "status": "active",
            "department": {
              "id": 214413,
              "odoo_id": "77",
              "name": "Sales"
            },
            "company": {
              "id": 1,
              "odoo_id": "1",
              "name": "The Address Investments"
            },
            "position": {
              "id": 60,
              "name": "Senior Property Consultant"
            }
          },
          "is_enrolled": true,
          "enrolled_at": "2026-09-02T16:01:23+03:00",
          "attendance_rate": 50,
          "survey_answers": [
            {
              "id": 282,
              "question_id": 42,
              "question_title": "How useful was the program?",
              "answer_type": "select",
              "answer": "Very useful",
              "selected_option": {
                "id": 35,
                "value": "Very useful"
              },
              "answered_at": "2026-09-02T16:01:23+03:00"
            }
          ],
          "assessment_answers": [
            {
              "id": 282,
              "question_id": 42,
              "question_title": "Describe the BATNA of your last deal.",
              "answer": "We walked away and re-anchored a week later.",
              "answered_at": "2026-09-02T16:01:23+03:00"
            }
          ]
        },
        {
          "user_odoo_id": "4977",
          "user": {
            "id": 38367,
            "odoo_id": "4977",
            "name": "Sara Nabil",
            "full_name": "Sara Nabil Fouad",
            "email": "sara.nabil@example.test",
            "mobile": "+201009876543",
            "employee_code": "10538",
            "status": "active",
            "department": {
              "id": 214413,
              "odoo_id": "77",
              "name": "Sales"
            },
            "company": {
              "id": 1,
              "odoo_id": "1",
              "name": "The Address Investments"
            },
            "position": {
              "id": 60,
              "name": "Senior Property Consultant"
            }
          },
          "is_enrolled": true,
          "enrolled_at": "2026-09-02T16:01:23+03:00",
          "attendance_rate": 0,
          "survey_answers": [],
          "assessment_answers": []
        }
      ],
      "survey": {
        "id": 43,
        "title": "Post-program feedback",
        "status": "active",
        "questions": [
          {
            "id": 42,
            "title": "How useful was the program?",
            "answer_type": "select",
            "required": true,
            "options": [
              {
                "id": 35,
                "value": "Very useful",
                "description": null
              }
            ]
          }
        ]
      },
      "assessment": {
        "id": 42,
        "name": "Negotiation knowledge check",
        "questions": [
          {
            "id": 42,
            "title": "Describe the BATNA of your last deal.",
            "is_required": true
          }
        ]
      }
    }
  ],
  "meta": {
    "current_page": 1,
    "per_page": 25,
    "total": 1,
    "last_page": 1,
    "from": 1,
    "to": 1,
    "has_more_pages": false
  }
}
```

## 6. Field reference

### The program's own columns

| Field | Type | Notes |
|---|---|---|
| `id` | int | `learning_programs.id` |
| `title`, `subtitle`, `description` | string \| null | |
| `status` | string | Stored value: `upcoming`, `running`, `completed`, `cancelled` |
| `computed_status` | string | Derived from the session dates and the current time. Use this one for "is it done?" — `status` can still say `upcoming` after the last session has ended |
| `capacity` | number \| null | Max seats |
| `type` | string \| null | `internal` or `external` |
| `target` | string \| null | `public` (open to everyone) or `department` (limited to `departments[]`) |
| `start_date`, `end_date` | date \| null | Earliest / latest session date. `null` when the program has no sessions yet |
| `parent` | object \| null | `{ id, title }` when this program is a child of another |
| `track` | object \| null | `{ id, title, subtitle, description, status }` |
| `departments[]` | array | `{ id, odoo_id, name, company_odoo_id }`. Empty when `target` is `public` |
| `companies[]` | array | `{ id, odoo_id, name }` — distinct companies behind those departments |

### `statistics`

Whole-program, always. **It does not change as you page** — page 3 of the roster
reports the same totals as page 1, so every count you read is the real one.

| Field | Notes |
|---|---|
| `sessions_count` | Number of session dates |
| `enrolled_users_count` | Enrollment rows |
| `attended_users_count` | Distinct people who attended at least one session — **walk-ins included**, so this can exceed `enrolled_users_count` |
| `enrolled_attended_users_count` | Distinct people who were both enrolled and attended |
| `attendance_records_count` | Total attendance rows (a person attending 2 sessions counts twice) |
| `attendance_rate` | `enrolled_attended_users_count / enrolled_users_count * 100`, rounded to 2 dp. `null` when nobody is enrolled. Deliberately **not** `attended_users_count / enrolled` — that mixes two populations and can exceed 100% |
| `survey_respondents_count` / `survey_answers_count` | Distinct people vs. total answers |
| `assessment_respondents_count` / `assessment_answers_count` | Same, for the assessment |

### `sessions[]`

| Field | Notes |
|---|---|
| `id` | `learning_session_dates.id` |
| `session_date` | `YYYY-MM-DD` |
| `session_time_from`, `session_time_to` | `HH:MM:SS` |
| `trainer_name` | Free text, may be `null` |
| `location` | `{ id, name }` or `null` |
| `attendance_count` | The session's attendee count — the length of `attendance[]` |
| `attendance[]` | Rows of `{ id, user_odoo_id, user, attended_at }`; `attended_at` is when the attendance was recorded, not the session start |

Sessions are ordered by date, then start time.

### `users[]`

Everyone the program touched, deduplicated by `user_odoo_id` and ordered by it.
That is the union of three groups, so **do not assume every entry is enrolled**:

1. people with an enrollment row,
2. people marked attended without an enrollment (HR can mark a walk-in present),
3. people who answered the survey or the assessment.

| Field | Notes |
|---|---|
| `user_odoo_id` | String. The stable cross-system identifier — join on this, not on `user.id` |
| `user` | The employee record, or `null` if no CRM user carries that `odoo_id` |
| `is_enrolled` | `false` for walk-ins and survey-only respondents |
| `enrolled_at` | ISO-8601, `null` when not enrolled |
| `attendance_rate` | Of that person, across all the program's sessions. `null` when the program has none. Note this is a **different formula** from `statistics.attendance_rate`. Which sessions they actually attended is in `sessions[].attendance[]` |
| `survey_answers[]` | Every survey answer this person gave on this program |
| `assessment_answers[]` | Every assessment answer this person gave on this program |

The `user` object carries `id`, `odoo_id`, `name`, `full_name`, `email`,
`mobile`, `employee_code`, `status`, plus:

| Field | Notes |
|---|---|
| `department` | `{ id, odoo_id, name }` or `null` |
| `company` | `{ id, odoo_id, name }` or `null`. Taken from the user's own `company_id`, falling back to their department's company — many user rows never had `company_id` filled in by the Odoo sync, and the department knows the same answer |
| `position` | `{ id, name }` or `null` |

`survey_answers[]` entries:

| Field | Notes |
|---|---|
| `question_id`, `question_title` | The question answered |
| `answer_type` | `text`, `select` or `rating` |
| `answer` | The raw stored value |
| `selected_option` | `{ id, value }` for `select` questions, `null` otherwise |
| `answered_at` | ISO-8601 |

`assessment_answers[]` entries carry `question_id`, `question_title`, `answer`
and `answered_at`.

### `survey` / `assessment`

The definitions — the questions as configured, whether or not anyone answered.
Both are `null` when the program has none attached. Use these to render or
interpret the answers found under each user.

## 7. Errors

The gate's own errors are JSON carrying a single machine-readable `error` code
and nothing else:

```json
{ "error": "invalid_service_key" }
```

| Status | `error` | Meaning |
|---|---|---|
| `401` | `missing_service_key` | No `X-Learning-Key` header. Note a key sent in `Authorization` reads as missing — see §2 |
| `401` | `invalid_service_key` | The key does not match |
| `503` | `integration_not_configured` | No key configured on the server — contact the CRM team; do not retry in a tight loop |

Framework errors keep Laravel's own shape, with a `message`:

| Status | Meaning |
|---|---|
| `400` | A `filter[...]` or `sort` the repository does not allow. The `message` names what is allowed |
| `429` | Rate limit exceeded (120/min); back off and retry |

An id matching no program is **not** an error — it is an empty page. See §1.

## 8. Notes and gotchas

- **Join on `user_odoo_id`, not `user.id`.** `odoo_id` is the identifier shared
  with the HR system and is what every learning table stores. `user.id` is a
  CRM-local primary key.
- **`user` can be `null`.** An attendance or answer row can reference an
  `odoo_id` with no matching CRM user (a departed employee, an unsynced record).
  The row still appears with its `user_odoo_id`; handle the `null`.
- **Use `computed_status`, not `status`,** to decide whether a program is
  finished — `status` is the stored value and is not recomputed as sessions pass.
- **Order is newest first** (id descending) and cannot be changed — see §3.
- **Sessions, attendance and rosters are complete arrays.** Only the programs are
  paged — see §3.
- **`attendance_rate` means two different things** depending on where you read
  it: on `statistics` it is enrolled-and-attended over enrolled; on a user it is
  that person's attended sessions over the program's total sessions.
- **`learning_topics` is not in this payload.** Topics have no foreign key to
  programs — they are a standalone library that quizzes attach to — so shipping
  the same catalogue inside every program was noise. Ask if you need it.
- **Timestamps are ISO-8601 with a timezone offset** (`+03:00`). Dates and times
  on sessions are plain local `YYYY-MM-DD` / `HH:MM:SS` strings with no zone.
- **Empty vs. null.** Collections are always arrays (`[]` when empty); optional
  single objects are `null`.
- Use gzip (`Accept-Encoding: gzip`) — the payload is repetitive JSON and
  compresses well.
- **Read-only.** `GET` only. Nothing in this integration can change CRM data.
- **No salary, banking or HR-sensitive fields** are exposed here.

## 9. For the CRM team

- Route: `src/Domain/Learning/Routes/api/public.php` (one route, `index` only)
- Controller: `src/Domain/Learning/Http/Controllers/SAC/GetLearningProgramDatasetController.php`
- Middleware: `src/Domain/Learning/Http/Middleware/LearningIntegrationKeyAuth.php` (alias `learning.service`)
- Config: `services.learning_integration.service_key` ← `LEARNING_INTEGRATION_SERVICE_KEY`
- Tests: `src/Domain/Learning/Tests/Feature/LearningProgramDatasetEndpointTest.php`

The endpoint goes through `LearningProgramRepository::spatie()->paginate(paginationPerPage())`,
the same as every other index in the domain, so:

- the filters it exposes are whatever that repository's `$allowedFilters`,
  `$allowedFiltersExact` and `$allowedFilterScopes` allow — adding one there adds
  it here, and adding an entry to `$allowedDefaultSorts` is what would enable
  `sort`;
- the page size is `paginationPerPage()`, i.e. the request's `per_page` falling
  back to `config('crm.pagination')` (a hard-coded 25 in `config/crm.php`, not an
  env var). There is no ceiling — a caller can ask for a very large page.

`page` / `per_page` sent as **headers** are folded into the query string by
`LearningIntegrationKeyAuth::promotePaginationHeaders()`, which is also where the
`Accept: application/json` header is forced so framework errors never come back
as HTML. Keeping both in the middleware is what lets the controller use the
app's ordinary pagination with no bespoke reader of its own.

`statistics` is counted off the rows already loaded, and `computed_status` /
`start_date` / `end_date` are derived from the eager-loaded sessions rather than
`LearningProgram`'s accessors — those issue a query per call, three per program.
`test_the_list_issues_the_same_number_of_queries_however_many_programs_it_returns`
is the guard; it fails loudly if that regresses.

Set the secret on each deployment that should serve the integration:

```
LEARNING_INTEGRATION_SERVICE_KEY=<long random string>
```

With the variable unset the endpoint returns `503` — an unconfigured key closes
the endpoint, it never opens it.
