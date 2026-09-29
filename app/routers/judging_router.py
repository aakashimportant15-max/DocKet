import csv
import io
import json
import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_role
from app.database import get_db
from app.models import (
    Event,
    JudgeAssignment,
    Project,
    RubricCriterion,
    Score,
    Team,
    User,
)
from app.templating import render
from app.voting import voting_state

router = APIRouter()


# --- API ---------------------------------------------------------------------


def _resolve_judge(db: Session, code: str | None) -> User | None:
    if not code:
        return None
    return db.query(User).filter(User.judge_code == code).first()


@router.get("/api/judge/scores")
def api_judge_scores(
    judge: str | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """A judge reads their own scores; an organizer reads anyone's.

    The isolation decision is made from the caller's identity alone and runs
    BEFORE any query against the scores table: a participant has no judge
    code, so any request of theirs already fails the role check, and a judge
    asking for another judge's code fails the equality gate.
    """
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if user.role not in ("judge", "organizer"):
        raise HTTPException(status_code=403, detail="judges and organizers only")
    requested = judge if judge is not None else user.judge_code
    if requested != user.judge_code and user.role != "organizer":
        raise HTTPException(status_code=403, detail="not your scores")
    target = _resolve_judge(db, requested)
    if target is None:
        raise HTTPException(status_code=404, detail="unknown judge")
    scores = (
        db.query(Score)
        .filter(Score.judge_user_id == target.id)
        .order_by(Score.project_id)
        .all()
    )
    return [
        {
            "project_id": score.project_id,
            "criteria": json.loads(score.criteria_json),
            "comment": score.comment,
            "submitted_at": score.submitted_at.isoformat(),
        }
        for score in scores
    ]


def _csv_text(value: str) -> str:
    # CSV formula injection guard: spreadsheet apps execute cells beginning
    # with =, +, - or @, so those get a leading single quote.
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


@router.get("/api/export.csv")
def export_csv(
    user: User = Depends(require_role("organizer")),
    db: Session = Depends(get_db),
):
    criteria = db.query(RubricCriterion).order_by(RubricCriterion.id).all()
    names = [c.name for c in criteria]
    weights = {c.name: float(c.weight) for c in criteria}

    scores = (
        db.query(Score)
        .join(Project, Score.project_id == Project.id)
        .join(Team, Project.team_id == Team.id)
        .join(User, Score.judge_user_id == User.id)
        .order_by(Score.project_id, Score.judge_user_id)
        .all()
    )

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        [
            "event_id",
            "project_id",
            "project_title",
            "team_id",
            "track",
            "judge_id",
            "judge_name",
            *names,
            "weighted_total",
            "comment",
            "submitted_at",
        ]
    )
    for score in scores:
        data = json.loads(score.criteria_json)
        total = sum(
            float(data.get(name) or 0) * weights.get(name, 1.0) for name in names
        )
        writer.writerow(
            [
                score.project.team.event_id if score.project.team else "",
                score.project_id,
                _csv_text(score.project.title),
                score.project.team_id,
                score.project.track or "",
                score.judge.judge_code or score.judge_user_id,
                score.judge.name,
                *[data.get(name, "") for name in names],
                f"{total:g}",
                _csv_text(score.comment or ""),
                score.submitted_at.isoformat(),
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="scores_export.csv"'},
    )


# --- shared authorization gates for the HTML routes --------------------------


def _load_project(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def _assignment(db: Session, user: User, project_id: int) -> JudgeAssignment | None:
    return (
        db.query(JudgeAssignment)
        .filter(
            JudgeAssignment.judge_user_id == user.id,
            JudgeAssignment.project_id == project_id,
        )
        .first()
    )


async def assigned_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("judge", "organizer")),
) -> Project:
    """Viewing gate: judges only open projects assigned to them; organizers
    may open anything."""
    project = _load_project(db, project_id)
    if user.role == "organizer":
        return project
    if _assignment(db, user, project_id) is None:
        raise HTTPException(
            status_code=403, detail="this project is not assigned to you"
        )
    return project


async def judge_scoring_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("judge")),
) -> Project:
    """Scoring gate: only an assigned judge may create or update a Score."""
    project = _load_project(db, project_id)
    if _assignment(db, user, project_id) is None:
        raise HTTPException(
            status_code=403, detail="this project is not assigned to you"
        )
    return project


def _rubric(db: Session) -> list[RubricCriterion]:
    return db.query(RubricCriterion).order_by(RubricCriterion.id).all()


def _weighted_total(data: dict, criteria: list[RubricCriterion]) -> float:
    return sum(float(data.get(c.name) or 0) * float(c.weight) for c in criteria)


# --- judge pages -------------------------------------------------------------


@router.get("/judge/projects", response_class=HTMLResponse)
def judge_projects(
    request: Request,
    user: User = Depends(require_role("judge")),
    db: Session = Depends(get_db),
):
    assignments = (
        db.query(JudgeAssignment)
        .filter(JudgeAssignment.judge_user_id == user.id)
        .order_by(JudgeAssignment.project_id)
        .all()
    )
    scored = {
        s.project_id: s
        for s in db.query(Score).filter(Score.judge_user_id == user.id).all()
    }
    rows = [{"assignment": a, "score": scored.get(a.project_id)} for a in assignments]
    return render(
        request,
        "judge/projects.html",
        {"rows": rows, "active_nav": "judge_projects"},
        user=user,
    )


@router.get("/judge/projects/{project_id}", response_class=HTMLResponse)
def judge_project_detail(
    request: Request,
    project: Project = Depends(assigned_project),
    user: User | None = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    score = (
        db.query(Score)
        .filter(Score.judge_user_id == user.id, Score.project_id == project.id)
        .first()
    )
    existing = json.loads(score.criteria_json) if score else {}
    assignment = _assignment(db, user, project.id) if user else None
    return render(
        request,
        "judge/project_detail.html",
        {
            "project": project,
            "criteria": _rubric(db),
            "existing": existing,
            "score": score,
            "saved": request.query_params.get("saved"),
            "assignment": assignment,
            "conflict_saved": request.query_params.get("conflict"),
            "active_nav": "judge_projects",
        },
        user=user,
    )


@router.post("/judge/projects/{project_id}/review")
async def submit_review(
    request: Request,
    project: Project = Depends(judge_scoring_project),
    user: User = Depends(require_role("judge")),
    db: Session = Depends(get_db),
):
    form = await request.form()
    values = {}
    for criterion in _rubric(db):
        raw = str(form.get(f"crit_{criterion.id}", "")).strip()
        try:
            value = int(raw)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"{criterion.name} must be an integer",
            )
        if not 0 <= value <= criterion.max_score:
            raise HTTPException(
                status_code=422,
                detail=f"{criterion.name} must be between 0 and {criterion.max_score}",
            )
        values[criterion.name] = value
    comment = str(form.get("comment", "")).strip() or None

    now = datetime.now(timezone.utc)
    score = (
        db.query(Score)
        .filter(Score.judge_user_id == user.id, Score.project_id == project.id)
        .first()
    )
    if score is None:
        db.add(
            Score(
                judge_user_id=user.id,
                project_id=project.id,
                criteria_json=json.dumps(values),
                comment=comment,
                submitted_at=now,
                updated_at=now,
            )
        )
    else:
        score.criteria_json = json.dumps(values)
        score.comment = comment
        score.updated_at = now
    db.commit()
    return RedirectResponse(
        url=f"/judge/projects/{project.id}?saved=1", status_code=303
    )


@router.post("/judge/projects/{project_id}/conflict")
async def declare_conflict(
    request: Request,
    project_id: int,
    user: User = Depends(require_role("judge")),
    db: Session = Depends(get_db),
):
    """Judge flags a conflict of interest on their own assignment.

    Advisory only: the declaration is visible to the organizer on the
    progress dashboard, but it never blocks this judge from also scoring,
    never auto-reassigns anything, and never touches the CSV export.
    """
    assignment = _assignment(db, user, project_id)
    if assignment is None:
        raise HTTPException(status_code=404, detail="project not found")
    form = await request.form()
    note = str(form.get("conflict_note", "")).strip() or None
    assignment.conflict_declared = True
    if note is not None:
        assignment.conflict_note = note
    assignment.conflict_declared_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(
        url=f"/judge/projects/{project_id}?conflict=1", status_code=303
    )


@router.get("/judge/reviews", response_class=HTMLResponse)
def judge_reviews(
    request: Request,
    user: User = Depends(require_role("judge")),
    db: Session = Depends(get_db),
):
    scores = (
        db.query(Score)
        .filter(Score.judge_user_id == user.id)
        .order_by(Score.project_id)
        .all()
    )
    criteria = _rubric(db)
    rows = []
    for s in scores:
        data = json.loads(s.criteria_json)
        rows.append(
            {
                "score": s,
                "criteria": data,
                "total": sum(
                    float(data.get(c.name) or 0) * float(c.weight) for c in criteria
                ),
            }
        )
    return render(
        request,
        "judge/reviews.html",
        {"rows": rows, "criteria": criteria, "active_nav": "judge_reviews"},
        user=user,
    )


# --- organizer pages ---------------------------------------------------------


@router.get("/organizer/judging/progress", response_class=HTMLResponse)
def judging_progress(
    request: Request,
    user: User = Depends(require_role("organizer")),
    db: Session = Depends(get_db),
):
    judges = (
        db.query(User)
        .filter(User.role == "judge", User.judge_code.isnot(None))
        .order_by(User.judge_code)
        .all()
    )
    assignments = db.query(JudgeAssignment).all()
    scores = db.query(Score).all()

    assigned_count = {j.id: 0 for j in judges}
    scored_count = {j.id: 0 for j in judges}
    for a in assignments:
        assigned_count[a.judge_user_id] = assigned_count.get(a.judge_user_id, 0) + 1
    for s in scores:
        scored_count[s.judge_user_id] = scored_count.get(s.judge_user_id, 0) + 1

    judge_rows = [
        {
            "judge": j,
            "assigned": assigned_count.get(j.id, 0),
            "scored": scored_count.get(j.id, 0),
        }
        for j in judges
    ]

    projects = db.query(Project).order_by(Project.id).all()
    score_count_by_project: dict[int, int] = {}
    for s in scores:
        score_count_by_project[s.project_id] = (
            score_count_by_project.get(s.project_id, 0) + 1
        )
    project_rows = [
        {"project": p, "reviews": score_count_by_project.get(p.id, 0)} for p in projects
    ]

    total_assigned = len(assignments)
    total_scored = len(scores)

    # --- integrity layer: conflicts, quality flags, normalization -------------
    # Quality flags are computed on the fly at request time instead of being
    # stored in a ReviewFlag table (design choice, not an official
    # requirement): the dataset is ~130 scores, this page already computes
    # its stats per request, and on-the-fly flags can never go stale. The
    # trade-off is no persisted "first detected at" audit trail.

    criteria = _rubric(db)

    declared_conflicts = [a for a in assignments if a.conflict_declared]
    conflict_by_pair = {(a.judge_user_id, a.project_id): a for a in declared_conflicts}
    conflict_count_by_judge: dict[int, int] = {}
    for a in declared_conflicts:
        conflict_count_by_judge[a.judge_user_id] = (
            conflict_count_by_judge.get(a.judge_user_id, 0) + 1
        )

    scores_by_judge: dict[int, list[Score]] = {}
    for s in scores:
        scores_by_judge.setdefault(s.judge_user_id, []).append(s)

    # Flag 3 is named light_workload, not fast_reviews: there is no real
    # timing data, so we honestly measure review-count share instead of
    # pretending to measure speed. Threshold = nearest-rank 10th percentile
    # of per-judge review counts; ties at the threshold are all flagged.
    review_counts = sorted(len(v) for v in scores_by_judge.values())
    n_reviewed_judges = len(review_counts)
    workload_rank = max(1, math.ceil(0.10 * n_reviewed_judges))
    workload_threshold = review_counts[workload_rank - 1]
    if n_reviewed_judges % 2:
        median_count: float = float(review_counts[n_reviewed_judges // 2])
    else:
        median_count = (
            review_counts[n_reviewed_judges // 2 - 1]
            + review_counts[n_reviewed_judges // 2]
        ) / 2

    totals_by_judge: dict[int, list[float]] = {}
    flags_by_judge: dict[int, list[dict]] = {}
    for judge_id, judge_scores in scores_by_judge.items():
        totals = [
            _weighted_total(json.loads(s.criteria_json), criteria) for s in judge_scores
        ]
        totals_by_judge[judge_id] = totals
        flags = []
        if len(totals) >= 2 and all(t == totals[0] for t in totals):
            flags.append(
                {
                    "type": "identical_scores",
                    "label": "Identical scores",
                    "detail": f"Gave the same score ({totals[0]:g}) on all "
                    f"{len(totals)} reviews.",
                }
            )
        if not any((s.comment or "").strip() for s in judge_scores):
            flags.append(
                {
                    "type": "no_comments",
                    "label": "No comments",
                    "detail": f"Left no comment on any of {len(judge_scores)} "
                    f"reviews.",
                }
            )
        if len(totals) <= workload_threshold:
            flags.append(
                {
                    "type": "light_workload",
                    "label": "Light workload",
                    "detail": f"Reviewed only {len(totals)} project"
                    f"{'s' if len(totals) != 1 else ''}, in the bottom 10% "
                    f"by workload (median {median_count:g}).",
                }
            )
        if flags:
            flags_by_judge[judge_id] = flags

    judge_means = {
        judge_id: sum(t) / len(t) for judge_id, t in totals_by_judge.items()
    }
    all_totals = [t for totals in totals_by_judge.values() for t in totals]
    overall_mean = sum(all_totals) / len(all_totals) if all_totals else 0.0

    # Read-only, advisory view: adjusted = raw - (judge_mean - overall_mean).
    # Nothing here writes back to Score or the CSV export.
    normalized = request.query_params.get("view") == "normalized"
    review_rows = []
    for s in sorted(scores, key=lambda s: (s.project_id, s.judge_user_id)):
        raw = _weighted_total(json.loads(s.criteria_json), criteria)
        review_rows.append(
            {
                "score": s,
                "raw": raw,
                "adjusted": round(
                    raw - (judge_means[s.judge_user_id] - overall_mean), 1
                ),
                "conflict": conflict_by_pair.get((s.judge_user_id, s.project_id)),
            }
        )

    # Community voting state (T3) for the close/reopen controls. Scoring is
    # untouched by these controls: they only move the event's voting_closes
    # timestamp, which hides or reveals /results.
    event = db.query(Event).order_by(Event.id).first()

    return render(
        request,
        "organizer/progress.html",
        {
            "judge_rows": judge_rows,
            "project_rows": project_rows,
            "voting_state": voting_state(event) if event else None,
            "voting_closes": event.voting_closes if event else None,
            "total_assigned": total_assigned,
            "total_scored": total_scored,
            "total_pending": total_assigned - total_scored,
            "completion": (
                round(100 * total_scored / total_assigned) if total_assigned else 0
            ),
            "flags_by_judge": flags_by_judge,
            "conflict_count_by_judge": conflict_count_by_judge,
            "review_rows": review_rows,
            "normalized": normalized,
            "overall_mean": overall_mean,
            "active_nav": "judging_progress",
        },
        user=user,
    )
