# Learning Integration API

A read-only, machine-to-machine view of the CRM's Learning module, built for an
external AI consumer that has no CRM login and no user of its own.

Two endpoints:

| Endpoint | What it gives you |
|---|---|
| `GET /api/learning-integration/programs` | Every training program, with its sessions, attendance, roster, and every survey/assessment answer given on it |
| `GET /api/learning-integration/get_users` | The employee roster on its own — name, department, sector, employee code, company, franchise, job level, status |

Both are **read-only**. There is no POST, PUT or DELETE anywhere in this
integration, and nothing here can write to the CRM.

---

## 1. Authentication

Send a static shared secret in the **`X-Learning-Key`** header on every request:

```http
GET /api/learning-integration/programs HTTP/1.1
Host: <crm-host>
X-Learning-Key: <the-shared-secret>
Accept: application/json
```

The key is issued by the CRM team and configured server-side as
`services.learning_integration.service_key`.

### Deliberately NOT `Authorization`

`Authorization: Bearer …` means an OAuth access token everywhere else in this
API. Putting the shared key there returns `401 missing_service_key` — the two
credential kinds are kept apart on purpose so neither can be mistaken for the
other. Use `X-Learning-Key` and nothing else.

### Auth failures

| Status | `error` | Meaning |
|---|---|---|
| 401 | `missing_service_key` | The `X-Learning-Key` header was absent or empty |
| 401 | `invalid_service_key` | The header was present but the value is wrong |
| 503 | `integration_not_configured` | No key is configured on the server. **This is closed, not open** — retry later and tell the CRM team |

```json
{ "error": "invalid_service_key" }
```

There is no user behind the key. Nothing in either payload is filtered by "who
is asking" — the same key always sees the same data.

### Rate limit

**120 requests per minute**, per client. Exceeding it returns `429` with the
standard `Retry-After` / `X-RateLimit-*` headers. Ingest jobs should stay well
under this; one request every 500ms is a safe pace.

### Content type

The middleware forces `Accept: application/json` for you, so **every** response
— including framework errors and 500s — comes back as JSON, never an HTML error
page. You can still send the header yourself; it changes nothing.

---

## 2. Pagination (identical on both endpoints)

| Parameter | Default | Notes |
|---|---|---|
| `page` | `1` | 1-indexed |
| `per_page` | `25` | Server default comes from `config('crm.pagination')` |

Send them **either** in the query string **or** as headers — the server promotes
headers into the query for you:

```bash
# equivalent
curl "…/programs?page=2&per_page=50" -H "X-Learning-Key: $KEY"
curl "…/programs" -H "X-Learning-Key: $KEY" -H "page: 2" -H "per_page: 50"
```

If a value appears in both places **the query string wins** — a URL a person
typed is never overridden by a header their client left set.

Both endpoints return the same `meta` block beside the list:

```json
"meta": {
  "current_page": 1,
  "per_page": 25,
  "total": 137,
  "last_page": 6,
  "from": 1,
  "to": 25,
  "has_more_pages": true
}
```

Walk pages until `has_more_pages` is `false`. Overshooting the last page returns
an empty list and a valid `meta`, not an error. Both endpoints order by **`id`
descending**, so the newest records are on page 1.

---

## 3. `GET /api/learning-integration/programs`

Returns `{ "programs": [ … ], "meta": { … } }`. Everything belonging to a program
— its sessions, each session's attendance, its roster, its survey and assessment
— is nested **inside** that program as a plain array. You walk one tree per
program; you never have to stitch sibling keys back together.

### Filters

Uses the CRM's standard `filter[…]` contract:

| Filter | Kind | Example |
|---|---|---|
| `filter[id]` | exact | `filter[id]=42` — returns exactly that one program, in the same shape as any other entry |
| `filter[type]` | exact | `filter[type]=internal` |
| `filter[track_id]` | exact | `filter[track_id]=3` |
| `filter[learningSessionDates.id]` | exact | a program by one of its session ids |
| `filter[title]` | partial match | `filter[title]=negotiation` |
| `filter[learningSessionDates.trainer_name]` | partial match | `filter[trainer_name]` is **not** a filter — use the full path |
| `filter[current_status]` | scope | `completed`, `running`, `upcoming`, `cancelled`; anything else falls through to a literal `status` match |
| `filter[startDateBetween]` | scope | `filter[startDateBetween]=2026-01-01,2026-03-31` — on the program's **earliest session date** |

**Two things that will bite you:**

- `filter[status]` is **not** allowed (only `filter[current_status]`). An
  unknown filter key is a `400`.
- **Never send `filter[program_status]`.** It is technically in the repository's
  allow-list but it resolves "attended / registered" against the *logged-in
  user*, and there is no logged-in user here — it will error. Use
  `filter[current_status]`.

`sort=` is **rejected with a 400**. The order is always `id` descending.

### Incremental ingestion

There is no `updated_since` filter. The practical patterns are:

- **Full re-ingest**, paging with `per_page=50` — the endpoint issues a fixed
  number of queries regardless of how many programs are on the page, so a large
  `per_page` is cheap.
- **Window by date** with `filter[startDateBetween]`, or by lifecycle with
  `filter[current_status]=completed`, to re-read only the slice you care about.

### Response shape

```json
{
  "programs": [
    {
      "id": 42,
      "title": "Negotiation Essentials",
      "subtitle": "Closing with confidence",
      "description": "Two half-days on discovery and objection handling.",
      "status": "upcoming",
      "computed_status": "completed",
      "capacity": 20,
      "type": "internal",
      "target": "public",
      "start_date": "2026-02-10",
      "end_date": "2026-02-11",
      "created_at": "2026-01-04T09:12:33+00:00",
      "updated_at": "2026-02-11T17:40:02+00:00",

      "parent": { "id": 11, "title": "Sales Academy" },
      "track":  { "id": 3, "title": "Commercial", "subtitle": null, "description": null, "status": "active" },
      "departments": [
        { "id": 8, "odoo_id": "310", "name": "Sales - New Cairo", "company_odoo_id": "3" }
      ],
      "companies": [
        { "id": 1, "odoo_id": "3", "name": "The Address Investments" }
      ],

      "statistics": {
        "capacity": 20,
        "sessions_count": 2,
        "enrolled_users_count": 18,
        "attended_users_count": 15,
        "enrolled_attended_users_count": 14,
        "attendance_records_count": 27,
        "attendance_rate": 77.78,
        "survey_respondents_count": 12,
        "survey_answers_count": 48,
        "assessment_respondents_count": 10,
        "assessment_answers_count": 30
      },

      "sessions": [
        {
          "id": 91,
          "session_date": "2026-02-10",
          "session_time_from": "09:00:00",
          "session_time_to": "13:00:00",
          "trainer_name": "Trainer One",
          "location": { "id": 4, "name": "Training Room A" },
          "attendance_count": 14,
          "attendance": [
            {
              "id": 501,
              "user_odoo_id": "4521",
              "user": { "…see user summary below…": null },
              "attended_at": "2026-02-10T09:04:11+00:00"
            }
          ]
        }
      ],

      "users": [
        {
          "user_odoo_id": "4521",
          "user": { "…see user summary below…": null },
          "is_enrolled": true,
          "enrolled_at": "2026-01-20T11:02:00+00:00",
          "attendance_rate": 100,
          "survey_answers": [
            {
              "id": 8801,
              "question_id": 12,
              "question_title": "How useful was the program?",
              "answer_type": "select",
              "answer": null,
              "selected_option": { "id": 55, "value": "Very useful" },
              "answered_at": "2026-02-11T13:30:00+00:00"
            }
          ],
          "assessment_answers": [
            {
              "id": 3301,
              "question_id": 7,
              "question_title": "Name three closing techniques.",
              "answer": "Assumptive, summary, urgency",
              "answered_at": "2026-02-11T13:45:00+00:00"
            }
          ]
        }
      ],

      "survey": {
        "id": 5,
        "title": "Post-program feedback",
        "status": "active",
        "questions": [
          {
            "id": 12,
            "title": "How useful was the program?",
            "answer_type": "select",
            "required": true,
            "options": [ { "id": 55, "value": "Very useful", "description": null } ]
          }
        ]
      },

      "assessment": {
        "id": 2,
        "name": "Negotiation check",
        "questions": [ { "id": 7, "title": "Name three closing techniques.", "is_required": true } ]
      }
    }
  ],
  "meta": { "…": null }
}
```

### Field notes — read these before modelling the data

- **`status` vs `computed_status`.** `status` is the column a human set.
  `computed_status` is derived from the session dates as of *now*:
  `cancelled` → `completed` (last session has ended) → `running` (first session
  has started) → otherwise the stored `status`. **Use `computed_status`** for
  anything lifecycle-related.
- **`start_date` / `end_date`** are the min and max session dates, not stored
  columns. A program with no sessions has `null` for both.
- **`survey` and `assessment` are nullable** — a program may have neither.
- **`selected_option` vs `answer`** on a survey answer: a `select` question fills
  `selected_option` and leaves `answer` null; every other type is the reverse.
- **`attendance_rate`** on the program is `enrolled-and-attended ÷ enrolled`,
  deliberately *not* `all attendees ÷ enrolled` — a program with walk-ins would
  otherwise report over 100%. Per-user `attendance_rate` is
  `sessions attended ÷ total sessions`. Both are `null` when the denominator is
  zero.
- **`statistics` is whole-program**, so it does not shift as you walk the pages.
- **The `users` roster is everyone the program touched**: the enrolled, plus
  walk-ins marked present without an enrollment row (`is_enrolled: false`), plus
  anyone who answered the survey or assessment. Nobody's answers ever hang off a
  person you were not given.
- **`user` may be `null`** where a `user_odoo_id` has no matching CRM user row.
  Always null-check it; `user_odoo_id` is always present.
- **`learning_topics` is deliberately absent.** Topics have no foreign key to
  programs — they are a standalone library that quizzes attach to via a morph —
  so repeating the whole catalogue on every program would be noise, not content.
- Nested collections are always **plain JSON arrays**, never objects keyed by id.

### The embedded user summary

Every `user` node inside `/programs` (on attendance rows and on the roster) has
this shape:

```json
{
  "id": 12,
  "odoo_id": "4521",
  "name": "Mona Farid",
  "full_name": "Mona Farid Hassan",
  "email": "mona.farid@example.com",
  "mobile": "+201000000000",
  "employee_code": "50231",
  "status": "active",
  "department": { "id": 8, "odoo_id": "310", "name": "Sales - New Cairo" },
  "company":    { "id": 1, "odoo_id": "3", "name": "The Address Investments" },
  "sector": "Commercial",
  "position": { "id": 21, "name": "Senior Sales Consultant" },
  "job_level_name": "Senior Specialist",
  "job_level_grade": "G7"
}
```

---

## 4. `GET /api/learning-integration/get_users`

The employee roster on its own. `/programs` identifies people by `user_odoo_id`
alone, so slicing attendance or survey answers by department, sector, company,
franchise or job level meant walking every program first just to collect the
people. This endpoint hands you the roster directly.

Returns `{ "users": [ … ], "meta": { … } }`.

### Filter

| Filter | Values |
|---|---|
| `filter[status]` | `active`, `inactive`, or both comma-separated: `active,inactive` |

A bare `?status=active` works too. Omit it and you get everyone.

An unrecognised value is a **`400`**, not an empty page — a silent empty result
would read as "nobody is employed":

```json
{
  "error": "invalid_filter",
  "message": "filter[status] must be one of: active, inactive."
}
```

Soft-deleted users are excluded automatically.

### Response shape

```json
{
  "users": [
    {
      "id": 12,
      "odoo_id": "4521",
      "name": "Mona Farid",
      "employee_code": "50231",
      "status": "active",
      "department": { "id": 8, "odoo_id": "310", "name": "Sales - New Cairo" },
      "sector": "Commercial",
      "company": { "id": 1, "odoo_id": "3", "name": "The Address Investments" },
      "franchise": { "id": 3, "odoo_id": "77", "name": "New Cairo Branch", "code": "NC-01" },
      "job_level_name": "Senior Specialist",
      "job_level_grade": "G7"
    }
  ],
  "meta": { "…": null }
}
```

### Field notes

- **`odoo_id` is the join key.** It is what `/programs` uses for
  `user_odoo_id` on enrollments, attendance and every answer. It is a **string**
  in both payloads — compare as strings, not integers.
- **`department`, `company`, `franchise` and both `job_level_*` fields are
  nullable.** Not every employee row is fully synced from Odoo.
- **`sector`** is derived, not a column: it is the first segment of the
  department's Odoo display path. `"Commercial / Sales / Sales - New Cairo"` →
  `"Commercial"`. A department with no slash is its own sector. `null` when
  there is no department or the path was never synced. Trimmed, so
  `"Commercial "` and `"Commercial"` are never two different buckets.
- **`company` falls back to the department's company** when the user's own
  `company_id` was never filled in by the Odoo sync — the department's company is
  the same answer.
- **`status`** is `active` or `inactive` only; it is the employment flag, not a
  learning status.

---

## 5. Recommended ingestion flow

1. `GET /get_users?per_page=100`, page through to `has_more_pages: false`. Key your
   people store by `odoo_id`.
2. `GET /programs?per_page=50`, page through the same way.
3. Resolve every `user_odoo_id` in the program payload against step 1. The
   embedded `user` summary is a convenience — treat step 1's roster as the
   source of truth for org attributes, since it is the one you refreshed
   wholesale.
4. Re-run step 2 with `filter[startDateBetween]` or
   `filter[current_status]=completed` for incremental top-ups between full
   passes.

Both endpoints are stable under paging within a run: `statistics` is
whole-program, and the id-descending order means new records appear at the front
rather than shifting rows you have already read. A record created *between* your
page requests can still shift the window by one — if that matters, take the full
pass in one go, or de-duplicate on `id`.

---

## 6. Error summary

| Status | Body | Cause |
|---|---|---|
| 200 | payload | OK |
| 400 | `{"error":"invalid_filter", "message":"…"}` | Bad `filter[status]` on `/get_users` |
| 400 | framework validation error | Unknown `filter[…]` key, or any `sort=`, on `/programs` |
| 401 | `{"error":"missing_service_key"}` | No `X-Learning-Key` header |
| 401 | `{"error":"invalid_service_key"}` | Wrong key |
| 429 | rate-limit body | Over 120 requests/minute |
| 503 | `{"error":"integration_not_configured"}` | No key configured server-side |

Every error is JSON. There is no HTML error page on this integration.

---

## 7. Where this lives in the codebase

| Piece | Path |
|---|---|
| Routes | `src/Domain/Learning/Routes/api/public.php` |
| Auth middleware | `src/Domain/Learning/Http/Middleware/LearningIntegrationKeyAuth.php` |
| Programs controller | `src/Domain/Learning/Http/Controllers/SAC/GetLearningProgramDatasetController.php` |
| Users controller | `src/Domain/Learning/Http/Controllers/SAC/GetLearningUserDatasetController.php` |
| Tests | `src/Domain/Learning/Tests/Feature/LearningProgramDatasetEndpointTest.php`, `…/LearningUserDatasetEndpointTest.php` |
