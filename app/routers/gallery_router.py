from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import MAX_COMMENT_LENGTH
from app.database import get_db
from app.models import Comment, Event, Project, Team, TeamMember, User
from app.templating import render

router = APIRouter()


@router.get("/gallery", response_class=HTMLResponse)
def gallery(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    projects = (
        db.query(Project)
        .order_by(Project.submitted_at.desc(), Project.id.desc())
        .all()
    )
    # Stat pills are computed from the database at request time, never
    # hardcoded.
    stats = {
        "projects": db.query(func.count(Project.id)).scalar() or 0,
        "teams": db.query(func.count(Team.id)).scalar() or 0,
        "tracks": db.query(func.count(func.distinct(Project.track))).scalar() or 0,
        "events": db.query(func.count(Event.id)).scalar() or 0,
    }
    tracks = [
        row[0]
        for row in db.query(Project.track)
        .distinct()
        .order_by(Project.track)
        .all()
        if row[0]
    ]
    return render(
        request,
        "gallery.html",
        {
            "projects": projects,
            "stats": stats,
            "tracks": tracks,
            "active_nav": "gallery",
        },
        user=user,
    )


@router.get("/gallery/{project_id}", response_class=HTMLResponse)
def project_detail(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    members = (
        db.query(User)
        .join(TeamMember, TeamMember.user_id == User.id)
        .filter(TeamMember.team_id == project.team_id)
        .order_by(User.name)
        .all()
    )
    # Community comments, newest first (T3). Readable by anyone; posting is
    # a separate route that requires a login.
    comments = (
        db.query(Comment)
        .filter(Comment.project_id == project_id)
        .order_by(Comment.created_at.desc(), Comment.id.desc())
        .all()
    )
    return render(
        request,
        "project_detail.html",
        {
            "project": project,
            "members": members,
            "comments": comments,
            "max_comment_length": MAX_COMMENT_LENGTH,
            "active_nav": "gallery",
        },
        user=user,
    )
