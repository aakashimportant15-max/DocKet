from datetime import datetime, timezone


def is_event_closed(event) -> bool:
    return datetime.now(timezone.utc) >= event.submissions_close
