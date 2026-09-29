from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_role
from app.database import get_db
from app.deadline import is_event_closed
from app.models import Event, User, parse_utc
from app.templating import render

router = APIRouter()


class EventCreate(BaseModel):
    name: str
    submissions_close: str  # ISO 8601, e.g. "2026-03-01T18:00:00Z"


@router.post("/organizer/events")
def create_event(
    payload: EventCreate,
    user: User = Depends(require_role("organizer")),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="event name is required")
    try:
        close = parse_utc(payload.submissions_close)
    except ValueError:
        raise HTTPException(
            status_code=422, detail="submissions_close must be ISO 8601"
        )
    event = Event(name=name, submissions_close=close)
    db.add(event)
    db.commit()
    db.refresh(event)
    return {
        "id": event.id,
        "name": event.name,
        "submissions_close": event.submissions_close.isoformat(),
    }


@router.get("/organizer/events", response_class=HTMLResponse)
def organizer_events(
    request: Request,
    user: User = Depends(require_role("organizer")),
    db: Session = Depends(get_db),
):
    """Read-only dashboard of events and their deadline status. Event
    creation stays on the JSON API route, as T1 built it."""
    events = db.query(Event).order_by(Event.submissions_close.desc()).all()
    rows = [{"event": e, "closed": is_event_closed(e)} for e in events]
    return render(
        request,
        "organizer/events.html",
        {"rows": rows, "active_nav": "manage_event"},
        user=user,
    )
