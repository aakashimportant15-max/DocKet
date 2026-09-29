# Docket — Data Model

Every claim below was re-read from `app/models.py`, `scripts/seed_fixtures.py`,
and `fixtures.json` in this repository on 2026-09-27, and cross-checked against
the live seeded `app.db`. The integrity layer's three `judge_assignments`
columns were re-verified against the same sources the same day. The T3
additions — the two `events` voting columns, `votes`, `comments` — were
re-read from the same files and cross-checked against the live DB again on
2026-09-28. The T3 additions are additive: `votes` and `comments` are new
tables, the two `events` columns are the only change to an existing table,
and every other table is unchanged from the T1/T2 schema. Nothing here is
from memory.

SQLite is the store (a single `app.db`, created by
`Base.metadata.create_all(engine)` in `app/database.py`). The tables below are
listed in the order they appear in `app/models.py`.

## Type note: `UTCTimestamp`

`app/models.py` defines a SQLAlchemy `TypeDecorator` named `UTCTimestamp` used
by every datetime column. SQLite stores naive datetimes, so on write it
refuses naive values (`ValueError: refusing to store a naive datetime`), and
on read it re-attaches `timezone.utc`. Result: every datetime loaded from the
database is tz-aware UTC and can be compared directly against
`datetime.now(timezone.utc)` (see `app/deadline.py`). This is labeled in the
code as a design choice, not an official requirement.

`parse_utc()` in `app/models.py` parses ISO 8601 strings such as
`2026-03-01T18:00:00Z`; a naive string is assumed to already be UTC (also a
design choice).

## Tables

### `users`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK, autoincrement |
| `email` | VARCHAR(255) | no | UNIQUE, indexed |
| `name` | VARCHAR(255) | no | |
| `role` | VARCHAR(32) | no | `organizer` \| `participant` \| `judge` (comment in code) |
| `password_hash` | VARCHAR(128) | no | salted SHA-256 (see `app/auth.py`) |
| `session_token` | VARCHAR(64) | yes | UNIQUE, indexed; `None` until first login or for stub accounts |
| `judge_code` | VARCHAR(32) | yes | UNIQUE, indexed; fixture judge id (`jdg_01`…) for judge-role users only |

One row per account. Three populations coexist in this table:

1. **The six test accounts** created by `TEST_ACCOUNTS` in
   `scripts/seed_fixtures.py`: organizer, judge_a, judge_b, participant
   (T1/T2) and voter_a, voter_b (T3) — each with a fixed, non-rotating
   `session_token` (`org_7f2a`, `jdg_a_91bc`, `jdg_b_44de`, `prt_2e88`,
   `vot_a_51c3`, `vot_b_7d20`).
2. **The remaining 28 fixture judges** (30 in `fixtures.json` minus
   `jdg_01`/`jdg_02`), each their own `users` row with `session_token=None`
   and `judge_code` set to the fixture id.
3. **Stub participant accounts** for fixture team-member emails: one per
   distinct member email, `role="participant"`, `session_token=None`, display
   name derived from the email local part. The seed script calls these "stub"
   accounts explicitly; they exist so `team_members` and gallery member
   counts are real data instead of a phantom column.

`judge_code` is the bridge between the fixture string ids and the integer
foreign keys used everywhere else: `judge_a` carries `judge_code="jdg_01"`
(display name merged to the fixture name "Tomas Varga") and `judge_b`
carries `"jdg_02"` ("Wei Lindqvist"). The judge-isolation check in
`app/routers/judging_router.py` compares this string against the requested
`judge` parameter before any score query runs.

### `events`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK, autoincrement |
| `name` | VARCHAR(255) | no | |
| `submissions_close` | UTCTimestamp (DATETIME) | no | deadline for submissions |
| `voting_opens` | UTCTimestamp (DATETIME) | yes | T3: start of the community-voting window |
| `voting_closes` | UTCTimestamp (DATETIME) | yes | T3: end of the community-voting window |

Exactly one row in the seeded database: "Sample Hack 2026", closing
2026-03-01T18:00:00Z. `app/deadline.py::is_event_closed` compares
`datetime.now(timezone.utc) >= event.submissions_close`; the submission
create/edit routes in `app/routers/submissions_router.py` call it and refuse
late writes.

The two T3 columns are nullable because events created through the T1 API
have no voting dates; `app/voting.py::voting_state` treats a missing bound as
no restriction on that side (design choice, stated in the code). The seed
always sets both: `voting_opens` = the fixture `submissions_close` (already
in the past, so a fresh seed is "open") and `voting_closes` = seed time + 7
days, so a fresh seed starts with voting open and results hidden until
someone closes the window.

### `teams`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK, autoincrement |
| `event_id` | INTEGER | no | FK → `events.id`, indexed |
| `name` | VARCHAR(255) | no | |
| `created_by_user_id` | INTEGER | no | FK → `users.id` |

One row per fixture team. In the seeded DB, `created_by_user_id` is the
participant test account for every team (fixture data carries no creator, so
the seed assigns the participant account — a design choice, not a
requirement).

### `team_members`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `team_id` | INTEGER | no | FK → `teams.id`, part of composite PK |
| `user_id` | INTEGER | no | FK → `users.id`, part of composite PK |

Composite primary key `(team_id, user_id)` — a user can be on many teams, a
team has many members, and the same pair cannot appear twice. Rows point at
the stub participant accounts described under `users`, plus one membership
the seed adds on purpose: `voter_a` joins the team that owns fixture project
`prj_01` ("Glass Signal", DB id 1) so the self-vote rule is exercisable on a
fresh seed. Nothing in `fixtures.json` maps to this row (design choice).

### `projects`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK, autoincrement |
| `team_id` | INTEGER | no | FK → `teams.id`, indexed |
| `track` | VARCHAR(255) | yes | human-readable track name, or raw fixture value if unknown |
| `title` | VARCHAR(255) | no | |
| `summary` | TEXT | no | defaults to `""` |
| `repo_url` | VARCHAR(512) | yes | |
| `submitted_at` | UTCTimestamp (DATETIME) | no | |
| `updated_at` | UTCTimestamp (DATETIME) | no | |

One row per fixture project. The seed stores the **track name** (e.g.
"Security"), not the fixture track id (`trk_04`) — explicitly labeled a design
choice in the seed script, with fallback to the raw value for unknown ids.
Both timestamps start equal to the fixture `submitted_at`.

Verified against the seeded DB: 41 project rows across 40 teams. The one
team with two rows is fixture team `tm_07` ("CopperLedger"): its projects
`prj_07` and `prj_41` both carry the title "Dry Harbour" and are preserved as
**two separate `projects` rows** (DB ids 7 and 41), both pointing at the
single `teams` row with id 7. Nothing collapses or deduplicates them — the
fixtures' awkward cases are seeded exactly as given.

### `judge_assignments`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK, autoincrement |
| `judge_user_id` | INTEGER | no | FK → `users.id`, indexed |
| `project_id` | INTEGER | no | FK → `projects.id`, indexed |
| `conflict_declared` | BOOLEAN | no | defaults to `False`; set by the judge via the COI declaration route |
| `conflict_note` | TEXT | yes | optional short reason; an empty declaration never overwrites an existing note |
| `conflict_declared_at` | UTCTimestamp (DATETIME) | yes | set to `now(UTC)` on every declaration (re-declaring updates it) |

Unique constraint `uq_judge_assignment` on `(judge_user_id, project_id)`:
a judge can be assigned to many projects and a project to many judges, but
the same pair only once. The three `conflict_*` columns are the only schema
change the integrity layer made: they are read by the organizer progress
route and written only by `POST /judge/projects/{id}/conflict`, which never
touches `scores`.

**Review quality flags are computed, not stored.** There is no `ReviewFlag`
table. The three flags (`identical_scores`, `no_comments`, `light_workload`)
are derived from the `scores` table at request time inside
`app/routers/judging_router.py::judging_progress` (the block marked
"integrity layer" there); the rationale and the trade-off (no persisted
"first detected at" audit trail) are stated in a code comment. The integrity
layer added columns, not tables: the schema stayed at eight tables then, and
is ten today after the two T3 tables described below.

**Crucially, there is no assignments list in `fixtures.json`.** The seed
derives an assignment row from the *presence of a score*: the first time a
(judge, project) pair appears in `fixtures["scores"]`, a `JudgeAssignment` row
is inserted. In the seeded DB this yields exactly 126 assignment rows — one
per score. Any judge who appears in the fixtures has scored every project
they are assigned to.

### `rubric_criteria`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK, autoincrement |
| `name` | VARCHAR(64) | no | UNIQUE |
| `weight` | FLOAT | no | defaults to 1.0 |
| `max_score` | INTEGER | no | defaults to 5 |

The three seeded rows (verified live):

| id | name | weight | max_score |
|---|---|---|---|
| 1 | functionality | 1.0 | 5 |
| 2 | quality | 1.0 | 5 |
| 3 | innovation | 1.0 | 5 |

These names match the keys inside each fixture score's `criteria` object
exactly — that match is what lets the CSV export and review form look up
scores by criterion name.

### `scores`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK, autoincrement |
| `judge_user_id` | INTEGER | no | FK → `users.id`, indexed |
| `project_id` | INTEGER | no | FK → `projects.id`, indexed |
| `criteria_json` | TEXT | no | JSON object, e.g. `{"functionality": 2, "quality": 4, "innovation": 2}` |
| `comment` | TEXT | yes | |
| `submitted_at` | UTCTimestamp (DATETIME) | no | |
| `updated_at` | UTCTimestamp (DATETIME) | no | |

Unique constraint `uq_score_per_pair` on `(judge_user_id, project_id)`:
at most one score row per judge per project. Resubmitting a review upserts
the existing row rather than inserting a second one (verified this session:
the review POST keeps the original `submitted_at`).

`judge_user_id` is deliberately duplicated on `Score` instead of being
reachable only through `judge_assignments` — the code comment in
`app/models.py` states the reason: the judge-isolation check must be a
single `WHERE judge_user_id = :id` comparison, with no join.

`fixtures.json` has no score timestamps, so the seed anchors both timestamps
to the project's `submitted_at` (labeled a design choice in the seed script)
to keep the export deterministic. Verified: 126 score rows.

### `votes`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK, autoincrement |
| `voter_user_id` | INTEGER | no | FK → `users.id`, indexed |
| `project_id` | INTEGER | no | FK → `projects.id`, indexed |
| `created_at` | UTCTimestamp (DATETIME) | no | |

Unique constraint `uq_vote_per_project` on `(voter_user_id, project_id)`. The
model docstring states the design: "The UNIQUE constraint is the real defence
against double voting — the route checks are just there to turn it into a
friendly 400 instead of a 500 from the database." The vote budget (max 3 per
voter) is not stored anywhere; it lives in `app/config.py`
(`MAX_VOTES_PER_VOTER = 3`, labeled a design choice) and is counted at request
time. The table seeds empty: nothing in `fixtures.json` votes.

### `comments`

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK, autoincrement |
| `author_user_id` | INTEGER | no | FK → `users.id`, indexed |
| `project_id` | INTEGER | no | FK → `projects.id`, indexed |
| `body` | TEXT | no | plain text; escaped on render (Jinja2 autoescape) |
| `created_at` | UTCTimestamp (DATETIME) | no | |

Written only by `POST /projects/{id}/comments`
(`app/routers/voting_router.py`): the body is stripped, an empty or
over-500-character body is refused with 422, and the 5-comments-per-user-per-
project cap (also in `app/config.py`, a design choice) refuses with 400.
Read by the gallery detail route in `created_at` desc, `id` desc order.
Seeds empty — comments exist only when a user posts one at runtime.

## fixtures.json → database mapping

`fixtures.json` top-level keys: `event`, `tracks`, `teams`, `projects`,
`judges`, `scores`. Counts: 1 event, 8 tracks, 40 teams, 41 projects,
30 judges, 126 scores.

| Fixture key | Destination |
|---|---|
| `event` (name, submissions_close) | one `events` row |
| `tracks` (id, name) | lookup map only; the **name** is stored in `projects.track` |
| `teams[].id` | key of `team_by_fixture_id`; DB id assigned by SQLite |
| `teams[].name` | `teams.name` |
| `teams[].members[]` | stub `users` rows + `team_members` rows (deduplicated per team) |
| `projects[].team` | FK via `team_by_fixture_id` (unknown team → seed raises) |
| `projects[].track` | resolved through the track-name map |
| `projects[].title/summary/repo_url` | same-named columns |
| `projects[].submitted_at` | both `submitted_at` and `updated_at` |
| `judges[].id` | `users.judge_code`; `jdg_01`/`jdg_02` merged into judge_a/judge_b test accounts |
| `judges[].name/email` | `users.name`/`users.email` |
| `judges[].tracks` | **not stored anywhere** — fixture judges' track expertise is not persisted |
| `scores[].judge` | FK via `judge_user_by_fixture_id` (unknown judge → seed raises) |
| `scores[].project` | FK via `project_by_fixture_id` (unknown project → seed raises) |
| `scores[].criteria` | JSON-serialized into `scores.criteria_json` |
| `scores[].comment` | `scores.comment` (empty string becomes `None`) |
| — (absent in fixtures) | score `submitted_at`/`updated_at` anchored to project `submitted_at` |
| — (absent in fixtures) | `judge_assignments` rows derived from each first-seen (judge, project) score pair |
| — (absent in fixtures) | `conflict_declared`/`conflict_note`/`conflict_declared_at` default to `False`/`None`/`None` until a judge declares |
| — (absent in fixtures) | voting window: `voting_opens` = fixture `submissions_close`; `voting_closes` = seed time + 7 days |
| — (absent in fixtures) | `votes` and `comments` tables — seeded empty; both fill only at runtime |
| — (absent in fixtures) | one extra `team_members` row: voter_a in the team owning `prj_01` (self-vote demo) |

Test-account seeding (fixed values from `spec.md`, required by the master
prompt):

| Label | Email | Role | Cookie | judge_code |
|---|---|---|---|---|
| organizer | organizer@example.org | organizer | `session=org_7f2a` | — |
| judge_a | judge_a@example.org | judge | `session=jdg_a_91bc` | jdg_01 (name "Tomas Varga") |
| judge_b | judge_b@example.org | judge | `session=jdg_b_44de` | jdg_02 (name "Wei Lindqvist") |
| participant | participant@example.org | participant | `session=prt_2e88` | — |
| voter_a | voter_a@example.org | participant | `session=vot_a_51c3` | — |
| voter_b | voter_b@example.org | participant | `session=vot_b_7d20` | — |

All six share the test password `dogfood` (same salted hash, computed once
in the seed). The seed prints these cookie lines again at the end of every
run. `voter_a` holds the extra team membership described under `team_members`
(they are on the team that owns project 1); `voter_b` is on no team.

## Entity-relationship summary (text)

- `events` 1 — * `teams` (a team belongs to one event).
- `users` 1 — * `teams` via `created_by_user_id` (who created the team).
- `teams` * — * `users` through `team_members` (composite PK prevents
  duplicate memberships).
- `teams` 1 — * `projects` (a project belongs to one team; `tm_07` currently
  has two).
- `users` (judge) * — * `projects` twice over:
  - through `judge_assignments` — "this judge may review this project" —
    derived from score presence in the seed;
  - through `scores` — "this judge did review this project" — one row per
    (judge, project) pair holding the full criteria JSON + comment.
- `users` 1 — * `votes` and `projects` 1 — * `votes`: a vote belongs to one
  voter and one project, and `uq_vote_per_project` allows each voter at most
  one vote per project.
- `users` 1 — * `comments` and `projects` 1 — * `comments`: a comment is
  authored by one user on one project; no unique constraint (many allowed,
  capped in code).
- `rubric_criteria` stands alone: no foreign keys. Scores reference criteria
  only by name inside `criteria_json`, and the rubric row's `name` is unique
  to make that lookup safe.
- `User.judge_code` is not a foreign key (it is a unique string column) but
  functionally links the merged test accounts to the fixture judge identity
  used by the peer-scores API.

No table stores presentation data (no avatar URLs, no gradient classes) —
`app/templating.py::track_class` derives those from the track name at render
time.
