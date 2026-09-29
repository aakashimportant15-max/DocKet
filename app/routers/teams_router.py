from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.models import Event, Team, TeamMember, User
from app.templating import render

router = APIRouter()


class TeamCreate(BaseModel):
    name: str


class MemberAdd(BaseModel):
    email: str


@router.post("/teams")
def create_team(
    payload: TeamCreate,
    user: User = Depends(require_role("participant")),
    db: Session = Depends(get_db),
):
    event = db.query(Event).order_by(Event.id).first()
    if event is None:
        raise HTTPException(status_code=400, detail="no event exists yet")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="team name is required")
    team = Team(event_id=event.id, name=name, created_by_user_id=user.id)
    db.add(team)
    db.flush()
    # The creator automatically joins their own team (design choice, not an
    # official requirement).
    db.add(TeamMember(team_id=team.id, user_id=user.id))
    db.commit()
    db.refresh(team)
    return {"id": team.id, "name": team.name, "event_id": team.event_id}


@router.post("/teams/{team_id}/members")
def add_member(
    team_id: int,
    payload: MemberAdd,
    user: User = Depends(require_role("participant")),
    db: Session = Depends(get_db),
):
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="team not found")
    # Only the team's creator (or an organizer) may add members. A design
    # choice, not an official requirement.
    if user.id != team.created_by_user_id and user.role != "organizer":
        raise HTTPException(
            status_code=403, detail="only the team creator or an organizer can add members"
        )
    member = (
        db.query(User)
        .filter(User.email == payload.email.strip().lower())
        .first()
    )
    if member is None:
        raise HTTPException(
            status_code=404, detail="no user account with that email"
        )
    already = (
        db.query(TeamMember)
        .filter(TeamMember.team_id == team.id, TeamMember.user_id == member.id)
        .first()
    )
    if already is None:
        db.add(TeamMember(team_id=team.id, user_id=member.id))
        db.commit()
    return {"team_id": team.id, "user_id": member.id, "name": member.name}


@router.get("/teams/mine", response_class=HTMLResponse)
def my_teams(
    request: Request,
    user: User = Depends(require_role("participant", "organizer")),
    db: Session = Depends(get_db),
):
    """Read-only view of the teams the current user belongs to (membership or
    creation), with members and projects. Creation and member invites stay on
    the JSON API routes, as T1 built them."""
    team_ids = (
        {row[0] for row in db.query(TeamMember.team_id).filter(TeamMember.user_id == user.id)}
        | {row[0] for row in db.query(Team.id).filter(Team.created_by_user_id == user.id)}
    )
    teams = (
        db.query(Team).filter(Team.id.in_(team_ids)).order_by(Team.id).all()
        if team_ids
        else []
    )
    return render(
        request,
        "teams/mine.html",
        {"teams": teams, "active_nav": "my_team"},
        user=user,
    )
