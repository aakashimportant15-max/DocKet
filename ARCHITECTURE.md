# ARCHITECTURE

How Docket is actually built. Every claim below was checked against the
current code, not written from memory.

## Directory tree

```
.
├── .dockerignore
├── .dogfood.toml            acceptance checker configuration
├── .gitignore
├── Dockerfile               python:3.11-slim; seeds then serves on :8000
├── LICENSE                  MIT
├── README.md
├── SUBMISSION-DESCRIPTION.txt  hackathon form text (<250 words)
├── acceptance-report.txt    committed checker output
├── docker-compose.yml       one web service, port 8000:8000
├── fixtures.json            official DOGFOOD 2026 fixture data
├── requirements.txt         fastapi, uvicorn, SQLAlchemy, Jinja2, multipart
├── run.py                   official acceptance checker (not our code)
├── spec.md                  official DOGFOOD 2026 spec (not our code)
├── t3-check-report.txt      committed output of t3_check.py
├── t3_check.py              our own T3 verification script (stdlib only)
├── app/
│   ├── __init__.py
│   ├── auth.py              password hashing, session lookup, require_role
│   ├── config.py            paths, cookie name, password salt
│   ├── database.py          SQLite engine, session factory, get_db
│   ├── deadline.py          is_event_closed — the one deadline check
│   ├── main.py              FastAPI app, /static mount, router includes
│   ├── models.py            all ten tables + UTCTimestamp wrapper
│   ├── templating.py        shared Jinja render(), track_class() global
│   ├── voting.py            voting_state — the one voting-window check (T3)
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── auth_router.py       /login, /auth/login, /auth/logout
│   │   ├── events_router.py     /organizer/events (POST JSON, GET HTML)
│   │   ├── gallery_router.py    /gallery, /gallery/{id}
│   │   ├── judging_router.py    scores API, CSV export, judge+organizer pages, integrity layer
│   │   ├── submissions_router.py  /projects/new, PATCH /submissions/{id}
│   │   ├── teams_router.py      /teams (POST), /teams/{id}/members, /teams/mine
│   │   └── voting_router.py     /vote, vote/unvote, comments, /results, organizer close/reopen
│   ├── static/
│   │   ├── app.js           gallery search/filter/pagination (DOM only)
│   │   └── style.css        all styling, no external assets
│   └── templates/
│       ├── base.html            topbar + role-grouped sidebar
│       ├── gallery.html         hero, stat pills, toolbar, card grid
│       ├── project_detail.html  + community comments (form, list, escaped bodies)
│       ├── login.html
│       ├── results.html         public results: hidden message or ranked list
│       ├── submit.html
│       ├── vote.html            participant ballot in per-voter shuffled order
│       ├── judge/projects.html        review queue
│       ├── judge/project_detail.html  scoring form + conflict-of-interest declaration
│       ├── judge/reviews.html         own scores with totals
│       ├── organizer/progress.html    coverage dashboard + quality flags, raw/normalized view
│       ├── organizer/events.html      read-only event list
│       └── teams/mine.html            read-only team list
└── scripts/
    └── seed_fixtures.py       deletes app.db, reseeds from fixtures.json
```

## Request flow: T1 example — `GET /gallery`

Defined in `app/routers/gallery_router.py:gallery`.

1. FastAPI resolves two dependencies first: `get_db()` (opens a SQLAlchemy
   session from `SessionLocal`, closed after the request) and
   `get_current_user(request, db)` (see below; returns a `User` or `None` —
   either is fine here, the gallery is public).
2. One query loads every project: `SELECT ... FROM projects ORDER BY
   submitted_at DESC, id DESC`. No pagination at the SQL level — the full
   list is rendered.
3. Four `COUNT` queries compute the hero stat pills (projects, teams,
   distinct tracks, events) from the live database; nothing is hardcoded.
4. A `DISTINCT` query on `Project.track` (sorted, non-null) builds the track
   filter options.
5. `render(request, "gallery.html", {...}, user=user)` in
   `app/templating.py` injects `user` and `active_nav` into the context and
   returns a `TemplateResponse`. Jinja renders every project card, including
   a `track_class(project.track)` gradient class from a template global.
6. `app/static/app.js` runs in the browser afterward and only *hides* cards
   (search text, track filter, grid/list toggle, 12-per-page pagination) by
   setting `style.display` on the already-rendered DOM. The raw HTML
   response already contains all 41 fixture titles as literal text — which is
   exactly what the acceptance checker reads.

## Request flow: T2 example — `GET /api/judge/scores?judge=jdg_01`

Defined in `app/routers/judging_router.py:api_judge_scores`. The order of
checks matters and is deliberate:

1. Dependencies: `get_db()`, then `get_current_user`. If no valid session
   cookie maps to a user, this route raises **401** itself (it does not use
   `require_role`, so it can distinguish anonymous from authenticated).
2. **Role check**: `user.role` must be `"judge"` or `"organizer"`, else
   **403**. This runs before anything touches the `scores` table, so a
   participant is turned away even if they craft the query string.
3. **The isolation gate**: `requested = judge if judge is not None else
   user.judge_code`, then `if requested != user.judge_code and user.role !=
   "organizer": raise 403`. A judge asking for another judge's code fails
   here, before any score query. An organizer passes. A judge with no `judge`
   param defaults to their own code and passes.
4. `_resolve_judge(db, requested)` looks the code up in `users.judge_code`.
   Unknown code → **404**. (An organizer calling the endpoint with no param
   lands here with `requested=None` → 404; organizers consume results via the
   CSV export instead.)
5. Only now a query runs: `SELECT ... FROM scores WHERE judge_user_id =
   :target_id ORDER BY project_id`. `judge_user_id` is stored directly on
   `Score` (not only reachable through `JudgeAssignment`) so this is a
   single-column comparison.
6. Each row is serialized to `{"project_id", "criteria" (parsed from
   `criteria_json`), "comment", "submitted_at"}` and returned as JSON.

`POST /judge/projects/{id}/review` goes through a different gate,
`judge_scoring_project`: `require_role("judge")` first, then the project must
exist (404), then an explicit `JudgeAssignment` row for this judge/project
pair must exist (403 otherwise). Only then does it read the form, validate
each criterion as an integer within `0..max_score` (422 otherwise), and
insert or update the single `Score` row allowed by the
`uq_score_per_pair` unique constraint.

## Request flow: voting (T3) — `POST /projects/{id}/vote`

Defined in `app/routers/voting_router.py:86` (`cast_vote`). The order of the
checks is exactly:

1. Dependencies, in parameter order: `require_role("participant")` first
   (anonymous or any other role → **403**, before the database is asked
   anything about the project), then `get_db()`.
2. `_first_event(db)` — the demo runs one event; no event row → **404**
   (the seed always has one).
3. `db.get(Project, project_id)` — unknown project → **404**.
4. `voting_state(event) != OPEN` → **400** `voting is not open`. This is the
   same single function (`app/voting.py`) the ballot and `/results` use, so
   "before the window" and "after the window" stop here and can never
   disagree with what the pages show.
5. **Self-vote guard**: a `team_members` row for (project's team, caller) →
   **400**. Deliberately membership-based, not `Team.created_by_user_id` —
   every member of the team is blocked, not just its creator.
6. **Duplicate guard**: an existing `votes` row for (caller, project) →
   **400**.
7. **Budget guard**: the caller's total vote count ≥ `MAX_VOTES_PER_VOTER`
   (3, from `app/config.py`) → **400**.
8. Insert the `Vote` row and `db.commit()`. The `uq_vote_per_project` UNIQUE
   constraint backstops a race: an `IntegrityError` is caught and turned
   into the same 400, so two rows can never be stored.
9. **303** redirect to `/vote`.

The other voting routes follow the same shape: `GET /vote` (ballot) requires
a participant, shuffles all projects with `random.Random(user.id)` and
renders no counts at all; `POST /projects/{id}/unvote` deletes only the
caller's own row and only while OPEN; `POST /projects/{id}/comments` accepts
any of the three roles, validates 1–500 chars (**422**) and the 5-per-user
limit (**400**), and redirects to `/gallery/{id}#comments`. `GET /results`
is public and builds the ranked `rows` list **only when `voting_state` says
`closed`** — while open, the route never queries vote counts. `POST
/organizer/voting/close` and `/reopen` are organizer-only (**403**
otherwise) and move exactly one column, `events.voting_closes`.

## Request flow: the integrity layer — `POST /judge/projects/{id}/conflict`

Defined in `app/routers/judging_router.py:309` (`declare_conflict`). This is
the one integrity-layer route a judge can call; everything else in the layer
is organizer-only or computed inside the progress route.

1. Dependencies: `get_db()`, then `require_role("judge")` — anonymous or
   non-judge → **403** (same as the review route; only the scores API
   bothers to distinguish anonymous with a 401).
2. `_assignment(db, user, project_id)` looks for the `JudgeAssignment` row
   for this (judge, project) pair. None → **404**. Note the deliberate
   difference from the scoring gate, which returns 403 for the same
   situation: declaring a conflict on a project you don't have is reported
   as "project not found," not as an authorization leak.
3. The form is read; `conflict_note` is stripped and empty becomes `None`.
4. The write is exactly three assignments on that one `JudgeAssignment` row:
   `conflict_declared = True`, `conflict_note = note` **only if a note was
   provided** (an empty submission never wipes an existing note), and
   `conflict_declared_at = datetime.now(timezone.utc)` (a tz-aware value, as
   `UTCTimestamp` requires). `db.commit()`. No `Score` row is read, created,
   modified, or deleted anywhere in this route.
5. Redirect 303 to `/judge/projects/{id}?conflict=1`, where the detail
   template shows a "conflict declared" banner.

What does **not** happen is the point of the layer: no reassignment, no
exclusion from the CSV export, no block on scoring. Verified by hand:
after declaring, the same judge resubmitted a review successfully and the
CSV export was byte-for-byte identical to before.

The other two parts of the layer live inside the existing
`GET /organizer/judging/progress` route (`judging_router.py:373`): the
quality flags and the raw/normalized numbers are computed from the already-
loaded `scores`/`judge_assignments` rows at request time (no separate flag
store; the trade-off is noted in a code comment at `judging_router.py:417`),
and the `view=normalized` query param only changes which column the template
renders — the route writes nothing.

## Session and authorization model

`app/auth.py`, ~45 lines, is the whole mechanism:

- **Login**: `POST /auth/login` looks the user up by email, verifies the
  salted sha256 password hash with `secrets.compare_digest`, mints a session
  token only if the user doesn't have one, and sets the `session` cookie
  (httponly). Seeded test accounts already carry fixed tokens
  (`org_7f2a`, `jdg_a_91bc`, `jdg_b_44de`, `prt_2e88`) which are never rotated;
  logout only deletes the browser cookie (`app/routers/auth_router.py`).
- **`get_current_user`** reads the `session` cookie, looks up
  `User.session_token`, and returns the `User` or `None`. No expiry, no
  revocation list.
- **`require_role(*roles)`** is a dependency factory: anonymous or wrong role
  → **403**. Most HTML and write routes use it. (The scores API handles
  anonymous itself to return 401, since "who is asking" is the whole point of
  that endpoint.)
- **Judge isolation** is not a separate dependency — it is three lines inside
  the scores endpoint (role → equality gate → then query), in that order, so
  that no code path reaches the `scores` table for another judge's data.
  HTML judge pages additionally require an assignment row via
  `assigned_project` / `judge_scoring_project`.
- **Deadline enforcement** is one function, `is_event_closed` in
  `app/deadline.py`, called by both `POST /projects/new` and
  `PATCH /submissions/{id}` (400 once the fixture event's close date,
  2026-03-01T18:00:00Z, has passed).
- **Community voting (T3)** reuses the same dependencies rather than a new
  mechanism: `require_role("participant")` on the ballot, vote, and unvote
  routes; any of the three roles on comments;
  `require_role("organizer")` on close/reopen; `/results` is public
  (`get_current_user` optional). The voting window itself is one function,
  `voting_state` in `app/voting.py`, called by every one of those routes.

## Why these choices (design choices, not official requirements)

- **SQLite**: one file, zero services, works identically on Windows and in
  the container. The whole dataset is small (10 tables, ~550 rows seeded), so
  nothing about the workload needs a bigger database. `UTCTimestamp` papers
  over SQLite's lack of tz-aware datetimes by storing naive UTC and reading
  back aware UTC, refusing naive writes.
- **Jinja2 server-rendering**: the acceptance checker reads raw HTML with no
  JavaScript, so the source of truth for every page must be complete in the
  first response. Server-rendering also keeps the app usable with scripts
  disabled.
- **Client-side gallery pagination**: the gallery renders all cards and the
  shipped JS only toggles visibility. This keeps search/filter/pagination
  instant, avoids query-string state that the checker wouldn't send, and —
  the deciding constraint — guarantees the raw HTML contains every fixture
  title, which the T1 check requires.

## Explicitly not built

- **T4** — public REST API for external clients, webhooks, certificates,
  embeddable gallery, bulk import/export.
- **T3 extras** — the five spec items (voting, comments, hidden results,
  shuffled ballot, anti-cheat rules) are built and described above. Not
  built around them: comment editing or deletion (no route exists — clearing
  a test comment means re-seeding), vote analytics or a vote export, and any
  sockpuppet detection: the rules stop double votes, self-votes, budget
  overruns and early peeking, not one person with several accounts
  (THREAT-MODEL.md states this plainly).
- **Integrity-layer sub-pieces** — the layer itself (COI declarations,
  quality flags, normalized view) is built and described above. Still not
  built around it: acting on a declared conflict is entirely manual
  (reassignment happens off-app; the badge is all the UI offers), and the
  quality flags have no persisted audit trail (`detected_at`) because they
  are computed on the fly rather than stored in a `ReviewFlag` table.
