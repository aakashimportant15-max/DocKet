# Docket — Threat model

What could actually go wrong in this app, written for the organizer team in
plain language. This is the spec.md bonus challenge, so it covers only what
is real in this codebase: every behavior below was read from the source and
re-tested against the running container on 2026-09-27 (fresh
`docker compose up`, fresh seed), not assumed. The T3 voting and comment
surface was re-tested the same way on 2026-09-28 (fresh seed, our own
`t3_check.py` 10/10 PASS, plus the manual attempts cited below). Nothing was
changed while writing this document; the one genuine finding is listed at
the end and reported separately.

## What is actually at risk here

Docket is a hackathon judging portal, and two things matter most: a judge's
scores and comments must stay private — from other judges and from
participants — and the submission deadline must refuse late work at the
backend, not just hide a button in the browser. Both are enforced in the
route layer, and both are exercised by `run.py` on every run. With T3 a
third, lower-stakes surface arrived: the community vote. It is a popularity
signal, not the official ranking (that is the judge scores), but a stuffed
ballot would embarrass the result, so it gets its own section below.
Everything else (uneven judge workloads, the duplicate submission, the
advisory quality flags) is honesty bookkeeping, not security.

## The judge-isolation guarantee, from an attacker's point of view

The property rests on `get_current_user` (app/auth.py) and the fixed order
of checks in `api_judge_scores` (app/routers/judging_router.py). What each
attempt actually gets, tried live today:

- **Guessing another judge's code in the URL.** `GET
  /api/judge/scores?judge=jdg_02` with judge_a's cookie → 403 `not your
  scores`. The equality gate runs before any query against the scores
  table, so a refused request never touches score data. An unknown code
  (`jdg_99`) is also 403 for a judge — the gate compares against the
  caller's own code first; only an organizer can reach the 404 path.
- **Guessing a project URL instead.** The HTML routes are gated too:
  judge_b opening `/judge/projects/7`, a project they are not assigned to,
  → 403. The `assigned_project` / `judge_scoring_project` dependencies
  require the judge_assignments row to exist before anything renders.
- **A participant trying the same.** `GET /api/judge/scores` with the
  participant cookie → 403 `judges and organizers only`. Participants have
  no judge_code, so the role check stops them before the judge code is
  even looked at.
- **Forging the session cookie.** A cookie whose value matches no
  `users.session_token` row simply makes `get_current_user` return `None`
  — the lookup is a parameterized SQLAlchemy equality query, so an
  invented value just matches nothing. Verified today by sending `Cookie:
  session=forged_deadbeef99` to nine routes: the scores API answers 401,
  every judge and organizer page and POST (review, conflict, CSV export,
  submit) answers 403, and the public gallery answers 200 — exactly what
  an anonymous visitor gets. A forged cookie grants nothing.

One known limitation, stated plainly: the session tokens are fixed and
never expire. The seed prints them, `run.py` depends on them, and logout
deliberately keeps the stored token so the checker accounts keep working
(app/routers/auth_router.py). Fine for grading, wrong for a real
deployment, which would need token rotation and expiry — and, for
passwords, more than the salted SHA-256 that app/config.py deliberately
chooses. Both are known limitations, not vulnerabilities being fixed now.

## The deadline, from an attacker's point of view

There is nothing client-side to bypass. `is_event_closed` (app/deadline.py)
compares `datetime.now(timezone.utc)` against the event row on every write,
in `POST /projects/new` and in `PATCH /submissions/{id}`
(app/routers/submissions_router.py), so skipping the browser entirely
changes nothing. POSTing directly with the participant cookie today
returned 400 `submissions are closed`. That is the same path `run.py`
exercises as "closed event refuses submissions", which passes on every run
(7/7 today).

## The conflict-of-interest layer, briefly

No new attack surface. The quality flags and normalized totals are
computed per request in the progress dashboard and never write to a Score;
the CSV export and the judge's own score view were byte-compared before
and after the layer landed and did not change. The only write is a judge
declaring a conflict on their own assignment, and the route resolves the
assignment with the caller's own user id first — so declaring on someone
else's assignment is a 404 (tried today: judge_b POSTing to
`/judge/projects/7/conflict` → 404 `project not found`).

## Community voting and comments (T3), from an attacker's point of view

What the rules actually stop, each attempt tried live on 2026-09-28 (fresh
seed; the same attempts are the `t3_check.py` checks):

- **Voting twice for the same project.** Second vote → **400** from the
  route's duplicate check, and the `uq_vote_per_project` UNIQUE constraint
  sits underneath as the backstop — the model docstring says it plainly:
  the constraint is the real defence, the route check exists to turn it into
  a friendly 400 instead of a database error.
- **Voting more than three times.** A fourth vote from the same account →
  **400** (`MAX_VOTES_PER_VOTER = 3`, a design choice — spec.md names no
  number).
- **Voting for your own team's project.** `voter_a` is a member of the team
  that owns project 1; trying to vote for it → **400**. The check resolves
  the caller's team memberships before inserting anything.
- **Voting outside the window.** Before `voting_opens` or after
  `voting_closes` → **400**, from the one `voting_state` function every
  voting route calls, so no two routes can disagree about the window.
- **Voting without a participant account.** Anonymous requests are refused
  live (403), and judge/organizer accounts hit the same
  `require_role("participant")` gate on every voting route.
- **Peeking at the standings early.** While the window is open, `/results`
  does not build the ranking at all — no counts and no ordered list exist in
  the HTML to read off, so there is no early-peek channel even in the raw
  page source. Closing the window on the organizer page reveals the ranked
  list; reopening hides it again.
- **Posting an abusive comment.** Any of the three roles may comment, but a
  body is stripped, empty or over 500 characters → **422**, and a sixth
  comment on the same project → **400** (5-per-user-per-project cap, another
  design choice). Comments render through Jinja autoescape: the self-check
  posts `<script>alert(1)</script>` and the page shows it as literal text
  (also visible in `screenshots/09_comments.png`).
- **Touching the close/reopen buttons without being the organizer.** The
  routes are `require_role("organizer")` → **403** for everyone else. These
  buttons only move `events.voting_closes`; they never touch a score.

One limitation, stated plainly: **nothing binds an account to a real
person.** Someone willing to create several accounts can vote several times
for the same project and comment as different "users". The rules above make
casual single-account ballot-stuffing fail loudly, and the UNIQUE constraint
means one account can never double-vote a project — but no part of this app
verifies identity. Treat these as deterrents, not a proof system: the app is
not cheat-proof, and this document does not claim it is.

## What is explicitly out of scope, and why

- **HTTPS/TLS.** The portal is a local Docker deployment on
  `http://localhost:8000`. TLS belongs to whoever ever hosts it, and the
  cookie's `secure` flag is deliberately unset for the same reason.
- **Rate limiting.** Not required by spec.md, the account set is small and
  fixed, and there is nothing worth brute-forcing on a grading demo.
- **CSRF.** Genuinely low-risk here rather than "future work by default".
  The session cookie is set HttpOnly and SameSite=Lax — that is Starlette
  1.7.0's default, verified in the running container; the login route in
  app/routers/auth_router.py passes only `httponly=True` — so current
  browsers will not attach it to a cross-site POST. Every state-changing
  route is a form POST, except one `PATCH /submissions/{id}`, which an
  HTML form cannot send at all. There are no CSRF tokens; a deployment
  that shared an origin or had to support old browsers would need them.
- **XSS.** Not a gap: every user-supplied string (judge comments, project
  titles, team names, community comments) renders through Jinja with
  autoescaping on (`TEMPLATES.env.autoescape`, verified in the container;
  the T3 self-check posts a `<script>` comment and confirms it comes back
  inert).
- **CSV formula injection — fixed.** `export_csv` now prefixes any comment
  or project-title cell that begins with `=`, `+`, `-` or `@` with a single
  quote, so spreadsheet apps read it as text; verified with a synthetic
  `=...` comment and `@...` title, while the 126 seeded rows and the header
  stayed byte-identical.
