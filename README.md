# Docket

An auditable hackathon submission and judging platform, built for DOGFOOD 2026.

**Claimed tiers: T1, T2. Verified tiers: T1, T2** (from a fresh `run.py` run,
pasted below). T3 (community voting, comments, hidden results, random ballot
order, anti-cheat rules) is **built and verified by our own `t3_check.py`
(10/10 PASS), deliberately not claimed** — the official checker has no T3
checks, so a T3 claim could only appear as "claimed but not verified", and
overclaiming is penalized. T4 (stretch features) is not built and not claimed.

## Run it

```
docker compose up --build
```

The container seeds `fixtures.json` into a fresh SQLite database on every boot
and serves on <http://localhost:8000>. Open <http://localhost:8000/gallery>.
No external network is needed after the image is built.

Test account cookies (copied from `.dogfood.toml`; all accounts use the
password `dogfood`):

| account     | header (cookie)               |
| ----------- | ----------------------------- |
| organizer   | `Cookie: session=org_7f2a`    |
| judge_a     | `Cookie: session=jdg_a_91bc`  |
| judge_b     | `Cookie: session=jdg_b_44de`  |
| participant | `Cookie: session=prt_2e88`    |
| voter_a     | `Cookie: session=vot_a_51c3`  |
| voter_b     | `Cookie: session=vot_b_7d20`  |

`judge_a` acts as fixture judge `jdg_01` (Tomas Varga); `judge_b` acts as
`jdg_02` (Wei Lindqvist). `voter_a` is a member of the team that owns project
1 ("Glass Signal"), so the self-vote rule can be exercised; `voter_b` is on
no team.

## Run the acceptance checker

```
python run.py .dogfood.toml
```

Fresh output (run against the current code, today):

```
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
```

Our own T3 self-check — the official checker contains no T3 checks, so T3 is
verified by `t3_check.py` instead. It is our script (not the official one),
it changes state while checking (votes and a comment), restores the OPEN
window at the end, and is meant to be run against a fresh seed:

```
python t3_check.py .dogfood.toml
```

Fresh output (same fresh seed as above):

```
DOGFOOD 2026 T3 self-check (not the official checker; run.py is)
portal: http://localhost:8000
fixtures: fixtures.json

T3  vote accepted (303) ............. PASS
T3  duplicate vote refused (400) .... PASS
T3  vote budget enforced (3 max) .... PASS
T3  self-vote refused (400) ......... PASS
T3  anonymous vote refused (403) .... PASS
T3  results hidden while open ....... PASS
T3  ballot order differs per voter .. PASS
T3  comment stored and escaped ...... PASS
T3  close reveals ranked results .... PASS
T3  reopen hides results again ...... PASS

cleanup: removed 3/3 test votes (window left OPEN)
cleanup: test comment(s) remain on /gallery/2 - comments have no delete route; re-seed to clear

T3 (own checker): 10/10 PASS
```

## Tech stack

Exactly what's in `requirements.txt`:

- FastAPI 0.141.1
- uvicorn [standard] 0.54.0
- SQLAlchemy 2.1.1
- Jinja2 3.1.6
- python-multipart 0.0.32

SQLite (via Python's standard library) is the database. All HTML is rendered
server-side by Jinja2; the only client-side JavaScript manipulates the
already-rendered gallery DOM (search, filter, grid/list toggle, pagination).

## What's implemented

- **T1.** Login with three roles, event creation with a real submission
  deadline enforced in the backend, team creation and membership, project
  submission and editing (refused after the deadline), public server-rendered
  gallery with project detail pages.
- **T2.** Judge assignments, a weighted scoring rubric, per-judge score
  isolation enforced in the backend (not hidden in templates), a scoring form
  for assigned judges, an organizer progress dashboard, and CSV export of all
  results.
- **Integrity layer.** Conflict-of-interest declarations (a judge flags
  their own assignment; the organizer sees a badge, nothing is reassigned or
  blocked), three deterministic review-quality flags computed from the seeded
  data (`identical_scores`, `no_comments`, `light_workload`), and an
  organizer-only raw/normalized toggle on the progress dashboard (each score
  adjusted toward the judge's own mean). None of it changes the CSV export
  or a judge's own score view: both were verified byte-for-byte identical to
  the pre-layer responses (see JUDGING.md).
- **T3 — community voting, built and verified by our own checker, not
  claimed.** A participant-only ballot (`/vote`) in a per-voter shuffled
  order with a 3-vote budget and a self-vote block; public comments on
  `/gallery/{id}` (1–500 chars, escaped on render); a public `/results` page
  that contains no counts or ranking at all until the window closes; and
  organizer-only close/reopen controls on the judging-progress page. The
  official checker has no T3 checks, so this tier is verified by
  `t3_check.py` (10/10 PASS) and deliberately left out of `claimed`.
- **Not built.** T4 stretch features (REST API for external clients,
  webhooks, certificates, embeddable gallery).

## Known limitations and design choices

Each of these was a choice, not an oversight; none is required by spec.md:

- **SQLite in a single `app.db` file** — no separate database container;
  setup on Windows and in Docker is trivial. Timestamps are stored as naive
  UTC and read back as tz-aware UTC via a `UTCTimestamp` wrapper that refuses
  naive writes.
- **Salted sha256 password hashes** — the spec's own "keep it simple, this is
  not a banking app" license.
- **Score criteria stored as JSON text** (`criteria_json`) keyed by rubric
  criterion name, not as a normalized per-criterion table — simpler schema,
  and the rubric is small and fixed at seed time.
- **Judge assignments derived from the fixture scores** — `fixtures.json` has
  no assignment list, so every (judge, project) pair with a score becomes an
  assignment. Awkward fixture cases (the duplicate `tm_07` submission, judge
  `jdg_07`'s all-4 scores, workloads from 1 to 11 reviews per judge) are
  seeded exactly as written, not cleaned up.
- **Seeded score timestamps anchor to the project's submission time**,
  because fixture scores carry no timestamps of their own.
- **Fixed, non-expiring session tokens** for the six seeded test accounts,
  because the acceptance checker reuses the same cookie for a whole grading
  run. Logout clears the cookie only; the stored token is kept.
- **Client-side gallery pagination/filtering over fully server-rendered
  HTML** — the acceptance checker reads raw HTML without JavaScript, so every
  fixture project title must appear in the initial response.
- **Quality flags are computed on the fly, not stored** — the progress route
  derives `identical_scores` / `no_comments` / `light_workload` from the
  scores table at request time instead of persisting them in a `ReviewFlag`
  table. With ~130 scores this costs nothing, flags can never go stale, and
  it matches the route's existing compute-per-request style; the trade-off is
  no persisted "first detected at" audit trail.
- **Card header gradients hashed from the track name** (deterministic
  character-code sum), so colours are stable across restarts without storing
  presentation data. No external images, fonts, or CDNs anywhere.
- **Voting limits (3 votes per voter, 5 comments per user per project, 500
  characters per comment) are design choices**, not spec numbers — enough to
  express a preference, too few for a single account to move a whole ballot.
  They live in `app/config.py` and are labeled there as such. What the rules
  provably do **not** stop — one person registering several accounts
  (sockpuppets) — is stated plainly in THREAT-MODEL.md.
- **The ballot is shuffled with a seed derived from the voter's user id** —
  different voters see different orders, each voter's own order stays stable
  across refreshes, and it costs three lines instead of storing a per-user
  permutation.
- **Hidden results are never built while the window is open.** The `/results`
  route computes the ranked list (with counts) only after `voting_state`
  says `closed`; while open the page is a plain message, so no count, rank,
  or list exists in the HTML to leak through source, whitespace, or a
  commented-out block.

## License

MIT (see `LICENSE`).

## More depth

- [ARCHITECTURE.md](ARCHITECTURE.md) — how it's built: directory tree,
  request flows, the session/authorization model.
- [DATA-MODEL.md](DATA-MODEL.md) — every table and field, and how
  `fixtures.json` maps onto them.
- [JUDGING.md](JUDGING.md) — the rubric, the weighted total, the isolation
  guarantee, the integrity layer (COI, quality flags, normalization), and
  honest notes on fixture fairness.
- [THREAT-MODEL.md](THREAT-MODEL.md) — what could go wrong, what was tested
  live, the sockpuppet limitation of community voting, and the CSV
  formula-injection fix.
