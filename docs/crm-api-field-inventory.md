# CRM Learning Program Dataset — what the endpoint actually returns

`GET https://apicrm.theaddress.app/api/api/learning-integration/programs`

Captured 2026-09-03 09:14 UTC over **57 programs**. 
Personal values are masked; field names, types and null counts are exact.


## Outstanding requests

| # | Request | What the endpoint returns today | Status |
|---|---|---|---|
| 1 | Return the session trainer as a **user object** with `employee_code` / `odoo_id`, as already done for attendees | `sessions[].trainer_name` — a free-text string | **not yet** |
| 2 | **Trim** `user.sector` | `"Finance "` — trailing space on ~89% of rows | **not yet** |
| 3 | Return `user.job_level_grade` as an **integer** | `"9"` — a string, so `"10"` sorts before `"9"` | **not yet** |

### Why request 1 matters

The trainer is the only person in the payload that arrives as a name rather than a record. 
Two spellings of one trainer (`Ahmed Elshiaty`, `Ahmed ElShiaty`) are counted as two people, 
splitting 14 sessions and 158 attendances across two rows. An `employee_code` resolves it at 
source, exactly as `user.employee_code` already does for every attendee.


## Every field returned

| Field | Type | Seen | Null | Sample |
|---|---|---:|---:|---|
| `assessment` | object | 57 | 56 |  |
| `assessment.id` | int | 1 | 0 | `2` |
| `assessment.name` | str | 1 | 0 | `"(masked)"` |
| `assessment.questions` | array | 1 | 0 |  |
| `assessment.questions[].id` | int | 1 | 0 | `3` |
| `assessment.questions[].is_required` | bool | 1 | 0 | `true` |
| `assessment.questions[].title` | str | 1 | 0 | `"(masked)"` |
| `capacity` | int | 57 | 0 | `20` |
| `companies` | array | 57 | 0 |  |
| `companies[].id` | int | 22 | 0 | `1` |
| `companies[].name` | str | 22 | 0 | `"(masked)"` |
| `companies[].odoo_id` | str | 22 | 0 | `"1"` |
| `computed_status` | str | 57 | 0 | `"upcoming"` |
| `created_at` | str | 57 | 0 | `"2026-09-02T17:00:02+03:00"` |
| `departments` | array | 57 | 0 |  |
| `departments[].company_odoo_id` | str | 26 | 19 | `"1"` |
| `departments[].id` | int | 26 | 0 | `2138` |
| `departments[].name` | str | 26 | 0 | `"(masked)"` |
| `departments[].odoo_id` | str | 26 | 0 | `"1823"` |
| `description` | str | 57 | 0 | `"(masked)"` |
| `end_date` | str | 57 | 0 | `"2026-09-23"` |
| `id` | int | 57 | 0 | `93` |
| `parent` | null | 57 | 57 |  |
| `sessions` | array | 57 | 0 |  |
| `sessions[].attendance` | array | 111 | 0 |  |
| `sessions[].attendance[].attended_at` | str | 267 | 0 | `"2026-08-03T12:59:50+03:00"` |
| `sessions[].attendance[].id` | int | 267 | 0 | `1092` |
| `sessions[].attendance[].user` | object | 267 | 0 |  |
| `sessions[].attendance[].user.company` | object | 267 | 0 |  |
| `sessions[].attendance[].user.company.id` | int | 267 | 0 | `7` |
| `sessions[].attendance[].user.company.name` | str | 267 | 0 | `"(masked)"` |
| `sessions[].attendance[].user.company.odoo_id` | str | 267 | 0 | `"10"` |
| `sessions[].attendance[].user.department` | object | 267 | 0 |  |
| `sessions[].attendance[].user.department.id` | int | 267 | 0 | `753` |
| `sessions[].attendance[].user.department.name` | str | 267 | 0 | `"(masked)"` |
| `sessions[].attendance[].user.department.odoo_id` | str | 267 | 0 | `"1084"` |
| `sessions[].attendance[].user.email` | str | 267 | 0 | `"(masked)"` |
| `sessions[].attendance[].user.employee_code` | str | 267 | 0 | `"TM4-32770"` |
| `sessions[].attendance[].user.full_name` | str | 267 | 0 | `"(masked)"` |
| `sessions[].attendance[].user.id` | int | 267 | 0 | `23517` |
| `sessions[].attendance[].user.job_level_grade` | str | 267 | 24 | `"9"` |
| `sessions[].attendance[].user.job_level_name` | str | 267 | 24 | `"Senior Specialist"` |
| `sessions[].attendance[].user.mobile` | str | 267 | 3 | `"(masked)"` |
| `sessions[].attendance[].user.name` | str | 267 | 0 | `"(masked)"` |
| `sessions[].attendance[].user.odoo_id` | str | 267 | 0 | `"70633"` |
| `sessions[].attendance[].user.position` | object | 267 | 0 |  |
| `sessions[].attendance[].user.position.id` | int | 267 | 0 | `2336` |
| `sessions[].attendance[].user.position.name` | str | 267 | 0 | `"(masked)"` |
| `sessions[].attendance[].user.sector` | str | 267 | 0 | `"Projects "` |
| `sessions[].attendance[].user.status` | str | 267 | 0 | `"active"` |
| `sessions[].attendance[].user_odoo_id` | str | 267 | 0 | `"70633"` |
| `sessions[].attendance_count` | int | 111 | 0 | `0` |
| `sessions[].id` | int | 111 | 0 | `169` |
| `sessions[].location` | object | 111 | 9 |  |
| `sessions[].location.id` | int | 102 | 0 | `1` |
| `sessions[].location.name` | str | 102 | 0 | `"(masked)"` |
| `sessions[].session_date` | str | 111 | 0 | `"2026-09-21"` |
| `sessions[].session_time_from` | str | 111 | 0 | `"11:00:00"` |
| `sessions[].session_time_to` | str | 111 | 0 | `"13:00:00"` |
| `sessions[].trainer_name` | str | 111 | 2 | `"(masked)"` |
| `start_date` | str | 57 | 0 | `"2026-09-21"` |
| `statistics` | object | 57 | 0 |  |
| `statistics.assessment_answers_count` | int | 57 | 0 | `0` |
| `statistics.assessment_respondents_count` | int | 57 | 0 | `0` |
| `statistics.attendance_rate` | float\|int | 57 | 0 | `0` |
| `statistics.attendance_records_count` | int | 57 | 0 | `0` |
| `statistics.attended_users_count` | int | 57 | 0 | `0` |
| `statistics.capacity` | int | 57 | 0 | `20` |
| `statistics.enrolled_attended_users_count` | int | 57 | 0 | `0` |
| `statistics.enrolled_users_count` | int | 57 | 0 | `1` |
| `statistics.sessions_count` | int | 57 | 0 | `3` |
| `statistics.survey_answers_count` | int | 57 | 0 | `0` |
| `statistics.survey_respondents_count` | int | 57 | 0 | `0` |
| `status` | str | 57 | 0 | `"upcoming"` |
| `subtitle` | str | 57 | 0 | `"(masked)"` |
| `survey` | object | 57 | 4 |  |
| `survey.id` | int | 53 | 0 | `2` |
| `survey.questions` | array | 53 | 0 |  |
| `survey.questions[].answer_type` | str | 159 | 0 | `"rating"` |
| `survey.questions[].id` | int | 159 | 0 | `3` |
| `survey.questions[].options` | array | 159 | 0 |  |
| `survey.questions[].required` | bool | 159 | 0 | `true` |
| `survey.questions[].title` | str | 159 | 0 | `"(masked)"` |
| `survey.status` | str | 53 | 0 | `"active"` |
| `survey.title` | str | 53 | 0 | `"(masked)"` |
| `target` | str | 57 | 0 | `"public"` |
| `title` | str | 57 | 0 | `"(masked)"` |
| `track` | object | 57 | 51 |  |
| `track.description` | str | 6 | 4 | `"(masked)"` |
| `track.id` | int | 6 | 0 | `6` |
| `track.status` | str | 6 | 0 | `"upcoming"` |
| `track.subtitle` | str | 6 | 3 | `"(masked)"` |
| `track.title` | str | 6 | 0 | `"(masked)"` |
| `type` | str | 57 | 0 | `"internal"` |
| `updated_at` | str | 57 | 0 | `"2026-09-02T17:00:02+03:00"` |
| `users` | array | 57 | 0 |  |
| `users[].assessment_answers` | array | 165 | 0 |  |
| `users[].assessment_answers[].answer` | str | 1 | 0 | `"(masked)"` |
| `users[].assessment_answers[].answered_at` | str | 1 | 0 | `"2026-04-09T14:47:21+02:00"` |
| `users[].assessment_answers[].id` | int | 1 | 0 | `10` |
| `users[].assessment_answers[].question_id` | int | 1 | 0 | `3` |
| `users[].assessment_answers[].question_title` | str | 1 | 0 | `"(masked)"` |
| `users[].attendance_rate` | float\|int | 165 | 0 | `0` |
| `users[].enrolled_at` | str | 165 | 0 | `"2026-09-02T17:12:51+03:00"` |
| `users[].is_enrolled` | bool | 165 | 0 | `true` |
| `users[].survey_answers` | array | 165 | 0 |  |
| `users[].survey_answers[].answer` | str | 132 | 0 | `"(masked)"` |
| `users[].survey_answers[].answer_type` | str | 132 | 0 | `"rating"` |
| `users[].survey_answers[].answered_at` | str | 132 | 0 | `"2026-08-10T10:55:54+03:00"` |
| `users[].survey_answers[].id` | int | 132 | 0 | `1574` |
| `users[].survey_answers[].question_id` | int | 132 | 0 | `3` |
| `users[].survey_answers[].question_title` | str | 132 | 0 | `"(masked)"` |
| `users[].survey_answers[].selected_option` | null | 132 | 132 |  |
| `users[].user` | object | 165 | 0 |  |
| `users[].user.company` | object | 165 | 0 |  |
| `users[].user.company.id` | int | 165 | 0 | `7` |
| `users[].user.company.name` | str | 165 | 0 | `"(masked)"` |
| `users[].user.company.odoo_id` | str | 165 | 0 | `"10"` |
| `users[].user.department` | object | 165 | 0 |  |
| `users[].user.department.id` | int | 165 | 0 | `675` |
| `users[].user.department.name` | str | 165 | 0 | `"(masked)"` |
| `users[].user.department.odoo_id` | str | 165 | 0 | `"1082"` |
| `users[].user.email` | str | 165 | 0 | `"(masked)"` |
| `users[].user.employee_code` | str | 165 | 0 | `"TM4-3716"` |
| `users[].user.full_name` | str | 165 | 0 | `"(masked)"` |
| `users[].user.id` | int | 165 | 0 | `213` |
| `users[].user.job_level_grade` | str | 165 | 8 | `"9"` |
| `users[].user.job_level_name` | str | 165 | 8 | `"Senior Specialist"` |
| `users[].user.mobile` | str | 165 | 1 | `"(masked)"` |
| `users[].user.name` | str | 165 | 0 | `"(masked)"` |
| `users[].user.odoo_id` | str | 165 | 0 | `"44260"` |
| `users[].user.position` | object | 165 | 0 |  |
| `users[].user.position.id` | int | 165 | 0 | `4080` |
| `users[].user.position.name` | str | 165 | 0 | `"(masked)"` |
| `users[].user.sector` | str | 165 | 0 | `"Finance "` |
| `users[].user.status` | str | 165 | 0 | `"active"` |
| `users[].user_odoo_id` | str | 165 | 0 | `"44260"` |
