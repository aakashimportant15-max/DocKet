import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DB_PATH = Path(os.environ.get("DOCKET_DB") or PROJECT_ROOT / "app.db")

FIXTURES_PATH = Path(
    os.environ.get("DOCKET_FIXTURES") or PROJECT_ROOT / "fixtures.json"
)

SESSION_COOKIE = "session"

# Pepper for the plain sha256 password hash. A design choice the master prompt
# explicitly allows ("keep it simple, this is not a banking app").
PASSWORD_SALT = "docket-t1"

# Community voting limits (T3). The spec asks for anti-cheat measures but does
# not name numbers; these are design choices, not spec requirements, chosen to
# keep a demo hackathon fair without needing an admin UI:
#   - 3 votes per voter: enough to express a preference, few enough that a
#     single account cannot promote a whole ballot.
#   - 5 comments per user per project and 500 chars: stops one account from
#     flooding a project page with text.
MAX_VOTES_PER_VOTER = 3
MAX_COMMENTS_PER_USER_PER_PROJECT = 5
MAX_COMMENT_LENGTH = 500
