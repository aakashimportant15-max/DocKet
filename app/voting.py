"""Community-voting window logic (T3).

One function, deliberately: the same rules decide whether voting is allowed,
whether results are visible, and what the UI shows, so they can never
disagree with each other.
"""

from datetime import datetime, timezone

from app.models import Event

BEFORE = "before"
OPEN = "open"
CLOSED = "closed"


def voting_state(event: Event) -> str:
    """Return "before" | "open" | "closed" for the event's voting window.

    Design choice, not an official requirement: a missing bound imposes no
    restriction on that side. Events created through the T1 API have no
    voting dates at all, and the seed always sets both — so in practice a
    missing bound only shows up for API-created events, where treating
    voting as always open (until an organizer closes it) is the friendlier
    default than silently locking it.
    """
    now = datetime.now(timezone.utc)
    if event.voting_opens is not None and now < event.voting_opens:
        return BEFORE
    if event.voting_closes is not None and now >= event.voting_closes:
        return CLOSED
    return OPEN
