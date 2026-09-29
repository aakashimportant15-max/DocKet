<div align="center">

DocKet: The Judge Layer

<img src="https://readme-typing-svg.demolab.com?font=Inter&weight=600&size=20&duration=3000&pause=900&color=FF0000&center=true&vCenter=true&width=850&lines=Auditable+Hackathon+Judging+Platform;Judging+Integrity+Engine" alt="Auditable Hackathon Judging Platform | Judging Integrity Engine" />

</div>

Claimed tiers: T1, T2. Verified tiers: T1, T2 (from a fresh run.py run,
pasted below). T3 (public voting) and T4 (stretch features) are not built and
not claimed.

Run it

docker compose up --build

The container seeds fixtures.json into a fresh SQLite database on every boot
and serves on http://localhost:8000. Open http://localhost:8000/gallery.
No external network is needed after the image is built.

Test account cookies (copied from .dogfood.toml; all accounts use the
password dogfood):

account

header (cookie)

organizer

Cookie: session=org_7f2a

judge_a

Cookie: session=jdg_a_91bc

judge_b

Cookie: session=jdg_b_44de

participant

Cookie: session=prt_2e88

judge_a acts as fixture judge jdg_01 (Tomas Varga); judge_b acts as
jdg_02 (Wei Lindqvist).

Run the acceptance checker

python run.py .dogfood.toml

Fresh output (run against the current code, today):

DOGFOOD 2026 acceptance report
portal: http://localhost:8000
claimed: T1 T2
fixtures: fixtures.json

T1  gallery is public ................. PASS
T1  project from fixtures shown ....... PASS
T1  closed event refuses submissions .. PASS
T2  judge sees own scores ............. PASS
T2  judge cannot see peer scores ...... PASS
T2  participant blocked ............... PASS
T2  csv export works .................. PASS

claimed T1 T2, verified T1 T2

Tech stack

Exactly what's in requirements.txt:

FastAPI 0.141.1

uvicorn [standard] 0.54.0

SQLAlchemy 2.1.1

Jinja2 3.1.6

python-multipart 0.32

SQLite (via Python's standard library) is the database. All HTML is rendered
server-side by Jinja2; the only client-side JavaScript manipulates the
already-rendered gallery DOM (search, filter, grid/list toggle, pagination).

What's implemented

T1. Login with three roles, event creation with a real submission
deadline enforced in the backend, team creation and membership, project
submission and editing (refused after the deadline), public server-rendered
gallery with project detail pages.

T2. Judge assignments, a weighted scoring rubric, per-judge score
isolation enforced in the backend (not hidden in templates), a scoring form
for assigned judges, an organizer progress dashboard, and CSV export of all
results.

Integrity layer. Conflict-of-interest declarations (a judge flags
their own assignment; the organizer sees a badge, nothing is reassigned or
blocked), three deterministic review-quality flags computed from the seeded
data (identical_scores, no_comments, light_workload), and an
organizer-only raw/normalized toggle on the progress dashboard (each score
adjusted toward the judge's own mean). None of it changes the CSV export
or a judge's own score view: both were verified byte-for-byte identical to
the pre-layer responses (see JUDGING.md).

Not built. T3 community voting and T4 stretch features (REST API for
external clients, webhooks, certificates, embeddable gallery).

Known limitations and design choices

Each of these was a choice, not an oversight; none is required by spec.md:

SQLite in a single app.db file — no separate database container;
setup on Windows and in Docker is trivial. Timestamps are stored as naive
UTC and read back as tz-aware UTC via a UTCTimestamp wrapper that refuses
naive writes.

Salted sha256 password hashes — the spec's own "keep it simple, this is
not a banking app" license.

Score criteria stored as JSON text (criteria_json) keyed by rubric
criterion name, not as a normalized per-criterion table — simpler schema,
and the rubric is small and fixed at seed time.

Judge assignments derived from the fixture scores — fixtures.json has
no assignment list, so every (judge, project) pair with a score becomes an
assignment. Awkward fixture cases (the duplicate tm_07 submission, judge
jdg_07's all-4 scores, workloads from 1 to 11 reviews per judge) are
seeded exactly as written, not cleaned up.

Seeded score timestamps anchor to the project's submission time,
because fixture scores carry no timestamps of their own.

Fixed, non-expiring session tokens for the four seeded test accounts,
because the acceptance checker reuses the same cookie for a whole grading
run. Logout clears the cookie only; the stored token is kept.

Client-side gallery pagination/filtering over fully server-rendered
HTML — the acceptance checker reads raw HTML without JavaScript, so every
fixture project title must appear in the initial response.

Quality flags are computed on the fly, not stored — the progress route
derives identical_scores / no_comments / light_workload from the
scores table at request time instead of persisting them in a ReviewFlag
table. With ~130 scores this costs nothing, flags can never go stale, and it
matches the route's existing compute-per-request style; the trade-off is no
persisted "first detected at" audit trail.

Card header gradients hashed from the track name (deterministic
character-code sum), so colours are stable across restarts without storing
presentation data. No external images, fonts, or CDNs anywhere.

License

MIT (see LICENSE).

More depth

ARCHITECTURE.md — how it's built: directory tree,
request flows, the session/authorization model.

DATA-MODEL.md — every table and field, and how
fixtures.json maps onto them.

JUDGING.md — the rubric, the weighted total, the isolation
guarantee, the integrity layer (COI, quality flags, normalization), and
honest notes on fixture fair
