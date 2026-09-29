import random
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_role
from app.config import (
    MAX_COMMENTS_PER_USER_PER_PROJECT,
    MAX_COMMENT_LENGTH,
    MAX_VOTES_PER_VOTER,
)
from app.database import get_db
from app.models import Comment, Event, Project, TeamMember, User, Vote
from app.templating import render
from app.voting import OPEN, voting_state

router = APIRouter()


def _first_event(db: Session) -> Event:
    """The demo runs one event; every voting route resolves it the same way."""
    event = db.query(Event).order_by(Event.id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="no event configured")
    return event


# --- voting ------------------------------------------------------------------


@router.get("/vote", response_class=HTMLResponse)
def ballot(
    request: Request,
    user: User = Depends(require_role("participant")),
    db: Session = Depends(get_db),
):
    """The community ballot.

    Design choice, not an official requirement: the ballot is shuffled with a
    seed derived from the voter's own user id. That gives every voter a
    different order (no position bias) while keeping each voter's own order
    stable across refreshes (no flicker), and it costs three lines instead of
    a stored per-user permutation.

    No vote counts appear anywhere on this page - not even for the caller's
    own votes, which are shown only as their own choices.
    """
    event = _first_event(db)
    state = voting_state(event)
    projects = list(db.query(Project).order_by(Project.id).all())
    order = list(projects)
    random.Random(user.id).shuffle(order)
    my_votes = {
        v.project_id
        for v in db.query(Vote).filter(Vote.voter_user_id == user.id).all()
    }
    # Own-team projects get a badge instead of a button: the server refuses
    # that vote anyway, so the ballot should not pretend to offer it.
    own_team_ids = {
        tm.team_id
        for tm in db.query(TeamMember).filter(TeamMember.user_id == user.id).all()
    }
    return render(
        request,
        "vote.html",
        {
            "projects": order,
            "state": state,
            "votes_cast": len(my_votes),
            "votes_remaining": max(0, MAX_VOTES_PER_VOTER - len(my_votes)),
            "max_votes": MAX_VOTES_PER_VOTER,
            "voted_project_ids": my_votes,
            "own_team_ids": own_team_ids,
            "voting_opens": event.voting_opens,
            "voting_closes": event.voting_closes,
            "active_nav": "vote",
        },
        user=user,
    )


@router.post("/projects/{project_id}/vote")
def cast_vote(
    project_id: int,
    user: User = Depends(require_role("participant")),
    db: Session = Depends(get_db),
):
    """Cast one vote. Every failure has its own status:

    403 not a participant, 404 unknown project, 400 window not open, 400
    self-vote, 400 duplicate, 400 budget exhausted. The UNIQUE constraint on
    (voter, project) backs the duplicate check so a race still cannot store
    two rows.
    """
    event = _first_event(db)
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if voting_state(event) != OPEN:
        raise HTTPException(status_code=400, detail="voting is not open")
    # Self-vote guard: membership in the project's team. Deliberately checked
    # against TeamMember rows only (not Team.created_by_user_id) - the real
    # rule is "do not vote for your own team", and every member of a team is
    # prevented, not just its creator.
    member = (
        db.query(TeamMember)
        .filter(
            TeamMember.team_id == project.team_id,
            TeamMember.user_id == user.id,
        )
        .first()
    )
    if member is not None:
        raise HTTPException(
            status_code=400, detail="you cannot vote for your own team's project"
        )
    existing = (
        db.query(Vote)
        .filter(Vote.voter_user_id == user.id, Vote.project_id == project_id)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=400, detail="you already voted for this project"
        )
    cast = db.query(Vote).filter(Vote.voter_user_id == user.id).count()
    if cast >= MAX_VOTES_PER_VOTER:
        raise HTTPException(status_code=400, detail="vote budget exhausted")
    db.add(
        Vote(
            voter_user_id=user.id,
            project_id=project_id,
            created_at=datetime.now(timezone.utc),
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400, detail="you already voted for this project"
        )
    return RedirectResponse(url="/vote", status_code=303)


@router.post("/projects/{project_id}/unvote")
def retract_vote(
    project_id: int,
    user: User = Depends(require_role("participant")),
    db: Session = Depends(get_db),
):
    event = _first_event(db)
    if voting_state(event) != OPEN:
        raise HTTPException(status_code=400, detail="voting is not open")
    vote = (
        db.query(Vote)
        .filter(Vote.voter_user_id == user.id, Vote.project_id == project_id)
        .first()
    )
    if vote is None:
        raise HTTPException(status_code=404, detail="no vote to remove")
    db.delete(vote)
    db.commit()
    return RedirectResponse(url="/vote", status_code=303)


# --- comments ----------------------------------------------------------------


@router.post("/projects/{project_id}/comments")
async def add_comment(
    request: Request,
    project_id: int,
    user: User = Depends(require_role("participant", "organizer", "judge")),
    db: Session = Depends(get_db),
):
    """Comment on a project. Any logged-in user may write; anyone may read.

    The body is stored as plain text and escaped by Jinja autoescape when it
    is rendered, so a comment containing markup appears as text, never as
    script. Limits (length, per-user count) are design choices from
    app/config.py, not spec requirements.
    """
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    form = await request.form()
    body = str(form.get("body", "")).strip()
    if not body or len(body) > MAX_COMMENT_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"comment must be 1..{MAX_COMMENT_LENGTH} characters",
        )
    mine = (
        db.query(Comment)
        .filter(
            Comment.author_user_id == user.id,
            Comment.project_id == project_id,
        )
        .count()
    )
    if mine >= MAX_COMMENTS_PER_USER_PER_PROJECT:
        raise HTTPException(
            status_code=400, detail="comment limit reached for this project"
        )
    db.add(
        Comment(
            author_user_id=user.id,
            project_id=project_id,
            body=body,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return RedirectResponse(url=f"/gallery/{project_id}#comments", status_code=303)


# --- results -----------------------------------------------------------------


@router.get("/results", response_class=HTMLResponse)
def results(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """Public results. Hidden until the window closes - hidden means hidden.

    While not closed the page is a plain message and the ranked list is never
    built at all, so no count or ranking can leak through HTML, whitespace or
    a commented-out block. After closing, ranks and counts are public.
    """
    event = _first_event(db)
    state = voting_state(event)
    rows: list[dict] = []
    if state == "closed":
        counts = dict(
            db.query(Vote.project_id, func.count(Vote.id))
            .group_by(Vote.project_id)
            .all()
        )
        projects = db.query(Project).order_by(Project.id).all()
        rows = sorted(
            ({"project": p, "votes": counts.get(p.id, 0)} for p in projects),
            key=lambda r: (-r["votes"], r["project"].id),
        )
    return render(
        request,
        "results.html",
        {
            "state": state,
            "rows": rows,
            "voting_closes": event.voting_closes,
            "active_nav": "results",
        },
        user=user,
    )


# --- organizer controls ------------------------------------------------------


@router.post("/organizer/voting/close")
def close_voting(
    user: User = Depends(require_role("organizer")),
    db: Session = Depends(get_db),
):
    """End the voting window now, revealing results."""
    event = _first_event(db)
    event.voting_closes = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(
        url="/organizer/judging/progress?voting=closed", status_code=303
    )


@router.post("/organizer/voting/reopen")
def reopen_voting(
    user: User = Depends(require_role("organizer")),
    db: Session = Depends(get_db),
):
    """Reopen the window for another seven days (demo-friendly duration)."""
    event = _first_event(db)
    event.voting_closes = datetime.now(timezone.utc) + timedelta(days=7)
    db.commit()
    return RedirectResponse(
        url="/organizer/judging/progress?voting=reopened", status_code=303
    )
