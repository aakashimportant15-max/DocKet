# Docket — Judging

How judging works in this codebase: the rubric, the weighted total, the
isolation guarantee, and what the seed data does and does not correct.
Everything below was re-read from `app/routers/judging_router.py`,
`app/models.py`, `scripts/seed_fixtures.py`, and the live seeded `app.db` on
2026-09-27. The T3 boundary section ("Community votes vs. judge scores") was
re-read from `app/routers/voting_router.py` and `app/voting.py` on
2026-09-28.

## The rubric (verified live)

The `rubric_criteria` table in the seeded `app.db` contains exactly:

| id | name | weight | max_score |
|---|---|---|---|
| 1 | functionality | 1.0 | 5 |
| 2 | quality | 1.0 | 5 |
| 3 | innovation | 1.0 | 5 |

These are the rows the review form renders and the CSV export reads. Scores
are stored as a JSON object in `scores.criteria_json` keyed by these names,
e.g. `{"functionality": 2, "quality": 4, "innovation": 2}`. Each criterion
accepts an integer from 0 to `max_score` (the form inputs enforce
`min="0" max="{{ c.max_score }}"`).

## The weighted total

Both the CSV export (`export_csv` in `app/routers/judging_router.py`) and the
judge's review page compute:

```
total = sum( float(criteria[name]) * weight[name] for each criterion )
```

The exact code in the CSV export (line 111–113):

```python
total = sum(
    float(data.get(name) or 0) * weights.get(name, 1.0) for name in names
)
```

where `names` is the ordered list of rubric criterion names and `weights`
comes from the `weight` column. **All three weights are 1.0 in the seeded
data**, so today the weighted total is simply the plain sum of the three
criterion scores — maximum 15, minimum 0. The formula is implemented
generally (a different `weight` column value would change the result), but no
organizer UI for editing weights exists; the rubric is seeded and read-only.

The CSV column is `weighted_total`, formatted with `f"{total:g}"`. A missing
criterion key counts as 0 (`data.get(name) or 0`), though the seeded data has
no such gaps — all 126 fixture scores carry all three criteria.

## What one judge can see (isolation, in plain language)

The rule: **a judge can read only their own scores. An organizer can read
anyone's. Nobody else can read anything.**

Concretely, `GET /api/judge/scores?judge=jdg_01` with judge_a's cookie returns
jdg_01's scores. The same request with judge_b's cookie returns `403 not your
scores`. An anonymous request gets `401`; a participant cookie gets `403
judges and organizers only`. This was verified by hand this session: a
forged `judge=jdg_01` request on judge_b's session returned 403 with none of
jdg_01's data in the body.

The enforcement order in `api_judge_scores` matters and is fixed:

1. No valid session cookie → **401** (handled first, before anything else).
2. Role is not `judge` or `organizer` → **403**. A participant has no
   `judge_code`, so they are already stopped here.
3. The requested judge code does not equal the caller's `judge_code`, and the
   caller is not an organizer → **403**. This equality gate runs **before any
   query against the `scores` table** — the database is never touched for a
   refused request, so there is no side channel via error messages or timing
   from score data.
4. Unknown judge code → **404**.
5. Only then: `SELECT ... WHERE judge_user_id = :id` for the resolved judge.

The database itself also enforces the pair structure: `uq_score_per_pair`
(unique on `judge_user_id, project_id`) means one score row per judge per
project, and `judge_assignments` (with `uq_judge_assignment`) means a judge
can open the review form only for a project they are explicitly assigned to —
the `assigned_project` / `judge_scoring_project` dependencies in the router
require the assignment row, so guessing a project URL slug as a judge is a
403, not a review page.

## What the seed data preserves (and what it deliberately does not fix)

`fixtures.json` is intentionally imperfect — it models what a real hackathon
export looks like. The seed preserves its awkward cases exactly, rather than
cleaning them up:

**Uneven judge workloads.** Review counts per judge range from 1
(`jdg_01`) to 11 (`jdg_24`). The progress dashboard shows this spread
honestly; nothing rebalances it.

**Flat scoring patterns.** `jdg_07` gave every criterion a 4 on all three of
their assigned projects (`{"functionality": 4, "quality": 4, "innovation":
4}` for all three rows — verified against `app.db`). With equal 1.0 weights
and no normalization, that pattern flows straight into the export.

**Duplicate submissions.** Team `tm_07` ("CopperLedger") submitted the
project "Dry Harbour" twice (`prj_07` and `prj_41`). Both rows exist in
`projects` and both are scored; nothing merges them, so the export contains
two rows for "Dry Harbour".

**What is not fixed because of this:** the seed deliberately does not
rebalance workloads, flatten or perturb flat scoring patterns, merge
duplicates, or plausibility-check scores. The integrity layer (below) now
*surfaces* some of these patterns as advisory flags, but the underlying
rows are preserved exactly as written and the CSV export still reports them
raw.

## The integrity layer

Three features, all advisory, all organizer-facing except the judge's own
conflict button. Everything in this section was verified against the running
app on 2026-09-27 (fresh checker run 7/7 PASS, plus the manual checks
cited below). The code lives in `app/routers/judging_router.py`: the
conflict route at line 309, the flag/normalization block at lines 417–514
inside `judging_progress`.

### Conflict-of-interest declarations

`POST /judge/projects/{id}/conflict` (judge role only) lets a judge flag
their own assignment. The route looks up the judge's `JudgeAssignment` row
for that project — **404 if they have none** — and writes exactly three
things: `conflict_declared = True`, an optional `conflict_note` (only if
one was provided; an empty form never erases a stored note), and
`conflict_declared_at = now(UTC)`. Then a 303 back to the project page.

What it does **not** do, confirmed against the route code and by hand:

- **It does not block scoring.** After declaring, the same judge submitted
  a review update on the same project successfully (303, score saved).
- **It does not exclude the score from the CSV export.** The export route
  reads only the `scores` table and never looks at `judge_assignments`;
  the export was byte-for-byte identical before and after a declaration.
- **It does not auto-reassign anything.** The only organizer-side effect is
  a badge on the progress dashboard (a per-judge count in the coverage
  table, and a per-row badge in the review details table with the note in
  its tooltip). Acting on it — reassigning, discounting the score — is a
  manual, off-app decision.

### Review quality flags (deterministic, computed at request time)

Computed inside `judging_progress` from the already-loaded score rows; no
flag table exists and nothing is persisted (the trade-off, no `detected_at`
audit trail, is noted in a code comment). Each flag is a small amber badge
with a one-sentence detail on hover. The page also carries the explicit
disclaimer: *"The flags below are signals for your attention, not
accusations — review the actual scores before acting."*

Trigger conditions, in plain English, and the counts they produce on the
current seeded data (re-derived from `app.db` on 2026-09-27; 30 judges, 126
scores, review counts 1–11):

- **`identical_scores`** — 2+ reviews and every review has the exact same
  weighted total. **Triggers once:** `jdg_07` gave a 4 on every criterion
  of all 3 assigned projects (raw total 12 each). Badge detail: "Gave the
  same score (12) on all 3 reviews."
- **`no_comments`** — 1+ reviews and not a single one has a non-empty
  comment. **Triggers twice:** `jdg_07` (3 comment-less reviews) and
  `jdg_23` (1). Badge detail: "Left no comment on any of N reviews."
- **`light_workload`** — deliberately *not* named "fast reviews": there is
  no timing data, so the flag honestly measures review-count share instead
  of pretending to measure speed. A judge is flagged when their review
  count is at or below the nearest-rank 10th percentile of all judges with
  at least one review (threshold 2 on this data; ties at the threshold are
  all included). **Triggers 8 times:** `jdg_01`, `jdg_03`, `jdg_05`,
  `jdg_12`, `jdg_17`, `jdg_23`, `jdg_27`, `jdg_28` — the judges with 1–2
  reviews against a median of 3.5. Badge detail: "Reviewed only N projects,
  in the bottom 10% by workload (median 3.5)."

Total on fresh seed: 11 badges across 11 judges (`jdg_07` carries two).

### The raw/normalized view

`GET /organizer/judging/progress?view=normalized` renders the same page
with an extra "Adjusted total" column in the review-details table, next to
the raw total. The formula, per review:

```
adjusted = raw_total - (judge_mean - overall_mean)
```

where `judge_mean` is that judge's mean weighted total across their own
reviews and `overall_mean` is the mean across all 126 reviews
(10.6984… on the current seed). Mean-centering only: a harsh judge's scores
nudge up toward the overall mean, a lenient judge's nudge down. Display is
rounded to one decimal; the overall mean is unchanged by construction.

Worked example, from data pulled live from `app.db` on 2026-09-27 —
`jdg_07` on "Hollow Signal" (project 9):

- `jdg_07`'s raw total there: 4 + 4 + 4 = **12** (all weights 1.0)
- `jdg_07`'s mean across their 3 reviews: **12.0** (all three are all-4s)
- overall mean: **10.6984**
- adjusted = 12 − (12 − 10.6984) = 10.6984 → displayed **10.7**

The lenient judge (mean 12 > overall 10.7) is pulled down, as intended; a
spot check of both extremes (`jdg_01`, mean 6.0, raw 6 → adjusted 10.7)
and a full programmatic pass over all 126 rendered rows found zero rows
adjusting in the wrong direction. The default view stays raw, and the
column header carries the tooltip: "Adjusted to account for judges who
score harshly or leniently overall. Raw scores remain the official record —
see CSV export."

### What this layer provably does not touch

Verified by byte comparison on 2026-09-27, old build vs. new, same seed:

- `GET /api/export.csv` — **11,933 bytes, byte-for-byte identical** to the
  pre-layer response (and identical *again* after a conflict declaration
  plus a score resubmit).
- `GET /api/judge/scores` for judge_a — **146 bytes, byte-for-byte
  identical**; judge_b identical as well. No flag, conflict, or normalized
  value appears anywhere in either response.

The layer is read-only with respect to scored data: the only table any of
it writes is `judge_assignments`' three `conflict_*` columns.

## Community votes vs. judge scores (T3 boundary)

Community voting shares the event and project pages with judging but not a
single number. Votes live in their own `votes` table (see DATA-MODEL.md);
comments in their own table. What that means concretely, re-read from the
code on 2026-09-28:

- **Votes never enter the weighted total.** The rubric total is computed from
  `scores.criteria_json` only; no vote count appears in the formula, the
  review form, or the review-edit route.
- **Votes never enter the normalized view.** `judge_mean` and `overall_mean`
  are means across the 126 `scores` rows; `judging_progress` reads the
  event's voting window only to render the close/reopen buttons, never the
  vote rows (confirmed by grepping the router: the only voting references
  are the `voting_state` import and that display block).
- **Votes never enter the CSV export.** `export_csv` reads `scores`,
  `projects`, `teams`, and rubric names; it never queries `votes` or
  `comments`. Verified after T3 landed: the export is still **11,933 bytes,
  md5 `6be4aec1f81e67499fb0f0cd640bba14`** — the same bytes as before T3 —
  and judge_a's `/api/judge/scores` response is byte-for-byte unchanged.
- **Close/reopen only move the clock.** The two organizer buttons on
  `/organizer/judging/progress` set `events.voting_closes` to now (close) or
  now + 7 days (reopen) and nothing else; the code comment next to them says
  exactly that. Judge scores, flags, and the export ignore the voting window
  entirely.
- **The revealed ranking is just votes.** When the window is closed,
  `/results` ranks projects by vote count (project id breaks ties). No
  rubric, weight, or judge input appears there — and while the window is
  open, no ranking is built at all.

## Verified, not built

Everything in this document describes behavior that exists in the code and
was exercised this session (fresh checker run: 7/7 PASS, plus the manual
isolation, upsert, conflict, flag, and byte-comparison checks described
above; the voting boundary was verified by the T3 self-check, 10/10 PASS).
Still not built, by design: organizer-configurable rubric weights; acting on
a declared conflict beyond seeing its badge (reassignment is manual/off-
app); a persisted flag audit trail; and the T4 stretch features listed in
README.md's "not built" section.
