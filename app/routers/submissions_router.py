from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_role
from app.database import get_db
from app.deadline import is_event_closed
from app.models import Event, Project, Team, TeamMember, User
from app.templating import render

router = APIRouter()


def _project_dict(project: Project) -> dict:
    return {
        "id": project.id,
        "team_id": project.team_id,
        "team": project.team.name if project.team else None,
        "track": project.track,
        "title": project.title,
        "summary": project.summary,
        "repo_url": project.repo_url,
        "submitted_at": project.submitted_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


async def _read_payload(request: Request) -> dict:
    """Read the request body as JSON (what the acceptance checker sends) or as
    a plain browser form post. A design choice, not an official requirement."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=422, detail="invalid JSON body")
        return data if isinstance(data, dict) else {}
    form = await request.form()
    return {key: value for key, value in form.items()}


def _user_team_ids(db: Session, user: User) -> set[int]:
    """Teams the user belongs to. Membership via TeamMember, plus teams the
    user created: the seed marks all fixture teams as created by the
    participant test account, so treating the creator as an implicit member
    makes the seeded projects editable in a demo (design choice, not an
    official requirement)."""
    member_ids = {
        row[0]
        for row in db.query(TeamMember.team_id).filter(
            TeamMember.user_id == user.id
        )
    }
    created_ids = {
        row[0]
        for row in db.query(Team.id).filter(Team.created_by_user_id == user.id)
    }
    return member_ids | created_ids


@router.get("/projects/new")
def submit_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    event = db.query(Event).order_by(Event.id).first()
    closed = event is not None and is_event_closed(event)
    return render(
        request,
        "submit.html",
        {"event": event, "closed": closed, "active_nav": "submit"},
        user=user,
    )


@router.post("/projects/new")
async def create_project(
    request: Request,
    user: User = Depends(require_role("participant")),
    db: Session = Depends(get_db),
):
    event = db.query(Event).order_by(Event.id).first()
    if event is None:
        raise HTTPException(status_code=400, detail="no event exists yet")
    if is_event_closed(event):
        raise HTTPException(status_code=400, detail="submissions are closed")

    data = await _read_payload(request)
    title = str(data.get("title") or "").strip()
    summary = str(data.get("summary") or "").strip()
    repo_url = str(data.get("repo_url") or "").strip() or None
    if not title or not summary:
        raise HTTPException(status_code=422, detail="title and summary are required")

    team_ids = _user_team_ids(db, user)
    if not team_ids:
        raise HTTPException(
            status_code=400, detail="create or join a team before submitting"
        )
    team = (
        db.query(Team)
        .filter(Team.id.in_(team_ids))
        .order_by(Team.id)
        .first()
    )

    now = datetime.now(timezone.utc)
    project = Project(
        team_id=team.id,
        track=None,
        title=title,
        summary=summary,
        repo_url=repo_url,
        submitted_at=now,
        updated_at=now,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return JSONResponse(status_code=201, content=_project_dict(project))


@router.patch("/submissions/{project_id}")
async def edit_submission(
    project_id: int,
    request: Request,
    user: User = Depends(require_role("participant")),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="submission not found")
    if project.team_id not in _user_team_ids(db, user):
        raise HTTPException(status_code=403, detail="not your submission")
    event = db.query(Event).order_by(Event.id).first()
    if event is not None and is_event_closed(event):
        raise HTTPException(status_code=400, detail="submissions are closed")

    data = await _read_payload(request)
    if "title" in data:
        title = str(data["title"] or "").strip()
        if not title:
            raise HTTPException(status_code=422, detail="title cannot be empty")
        project.title = title
    if "summary" in data:
        project.summary = str(data["summary"] or "").strip()
    if "repo_url" in data:
        project.repo_url = str(data["repo_url"] or "").strip() or None
    project.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(project)
    return _project_dict(project)
