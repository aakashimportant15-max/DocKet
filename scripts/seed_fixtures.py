"""Seed the Docket database from the official fixtures.json.

Run as:  python scripts/seed_fixtures.py
Deletes and recreates app.db so seeding is repeatable.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.auth import hash_password  # noqa: E402
from app.config import DB_PATH, FIXTURES_PATH  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    Event,
    JudgeAssignment,
    Project,
    RubricCriterion,
    Score,
    Team,
    TeamMember,
    User,
    parse_utc,
)

TEST_PASSWORD = "dogfood"

# Fixed, non-expiring session tokens for the checker accounts. The T1/T2
# values are the exact examples from spec.md, as the master prompt requires.
# voter_a and voter_b are the fixed T3 tokens from the master prompt; they
# exist so the T3 checks have two distinct participants to vote with.
# (label, email, display name, role, session token)
TEST_ACCOUNTS = [
    ("organizer", "organizer@example.org", "Organizer", "organizer", "org_7f2a"),
    ("judge_a", "judge_a@example.org", "Judge A", "judge", "jdg_a_91bc"),
    ("judge_b", "judge_b@example.org", "Judge B", "judge", "jdg_b_44de"),
    ("participant", "participant@example.org", "Participant", "participant", "prt_2e88"),
    ("voter_a", "voter_a@example.org", "Voter A", "participant", "vot_a_51c3"),
    ("voter_b", "voter_b@example.org", "Voter B", "participant", "vot_b_7d20"),
]

# The judge test accounts stand in for two real fixture judges. judge_a reads
# and writes scores as jdg_01, judge_b as jdg_02; peer_scores in
# .dogfood.toml points at jdg_01 and must refuse judge_b.
JUDGE_ACCOUNT_MAP = {"judge_a": "jdg_01", "judge_b": "jdg_02"}

# Default rubric, matching the criteria keys used in fixtures["scores"]
# exactly. Weights are 1.0 until an organizer configures otherwise.
DEFAULT_RUBRIC = [
    ("functionality", 1.0, 5),
    ("quality", 1.0, 5),
    ("innovation", 1.0, 5),
]


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        with open(FIXTURES_PATH, encoding="utf-8") as f:
            fixtures = json.load(f)

        pw_hash = hash_password(TEST_PASSWORD)
        users = {}
        for label, email, name, role, token in TEST_ACCOUNTS:
            user = User(
                email=email,
                name=name,
                role=role,
                password_hash=pw_hash,
                session_token=token,
            )
            db.add(user)
            users[label] = user
        db.flush()

        event_data = fixtures["event"]
        # Voting window (T3): opens when submissions close (already in the
        # past, so seeded state is "open") and closes seven days from seed
        # time, so every fresh seed starts with voting open and results
        # hidden until someone explicitly closes the window.
        event = Event(
            name=event_data["name"],
            submissions_close=parse_utc(event_data["submissions_close"]),
            voting_opens=parse_utc(event_data["submissions_close"]),
            voting_closes=datetime.now(timezone.utc) + timedelta(days=7),
        )
        db.add(event)
        db.flush()

        participant = users["participant"]

        # One User row per fixture judge. jdg_01/jdg_02 are merged into the
        # judge_a/judge_b test accounts (real fixture name, fixed token,
        # judge_code set); the rest get their own rows with no session token.
        judge_fixture = {j["id"]: j for j in fixtures.get("judges", [])}
        judge_user_by_fixture_id = {}
        for label, fixture_id in JUDGE_ACCOUNT_MAP.items():
            account = users[label]
            account.judge_code = fixture_id
            fx = judge_fixture.get(fixture_id)
            if fx is not None:
                account.name = fx["name"]
            judge_user_by_fixture_id[fixture_id] = account
        mapped = set(JUDGE_ACCOUNT_MAP.values())
        for fx in fixtures.get("judges", []):
            if fx["id"] in mapped:
                continue
            user = User(
                email=fx["email"],
                name=fx["name"],
                role="judge",
                password_hash=pw_hash,
                session_token=None,
                judge_code=fx["id"],
            )
            db.add(user)
            judge_user_by_fixture_id[fx["id"]] = user
        db.flush()

        # Stub participant accounts for fixture team members so TeamMember is
        # real data (gallery member counts, team pages) instead of a column
        # we pretend exists. Design choice, not an official requirement.
        member_stub_cache = {}

        def member_stub(email: str) -> User:
            user = member_stub_cache.get(email)
            if user is None:
                user = User(
                    email=email,
                    name=email.split("@")[0],
                    role="participant",
                    password_hash=pw_hash,
                    session_token=None,
                )
                db.add(user)
                db.flush()
                member_stub_cache[email] = user
            return user

        team_by_fixture_id = {}
        for t in fixtures["teams"]:
            team = Team(
                event_id=event.id,
                name=t["name"],
                created_by_user_id=participant.id,
            )
            db.add(team)
            db.flush()
            seen = set()
            for email in t.get("members", []):
                email = email.strip().lower()
                if not email or email in seen:
                    continue
                seen.add(email)
                db.add(TeamMember(team_id=team.id, user_id=member_stub(email).id))
            team_by_fixture_id[t["id"]] = team
        db.flush()

        # Store the human-readable track name (design choice, not an official
        # requirement); fall back to the raw value if the id is unknown.
        track_names = {t["id"]: t["name"] for t in fixtures.get("tracks", [])}
        project_by_fixture_id = {}
        project_count = 0
        for p in fixtures["projects"]:
            team = team_by_fixture_id.get(p["team"])
            if team is None:
                raise ValueError(
                    f"project {p['id']} references unknown team {p['team']}"
                )
            submitted_at = parse_utc(p["submitted_at"])
            project = Project(
                team_id=team.id,
                track=track_names.get(p.get("track"), p.get("track")),
                title=p["title"],
                summary=p.get("summary") or "",
                repo_url=p.get("repo_url"),
                submitted_at=submitted_at,
                updated_at=submitted_at,
            )
            db.add(project)
            project_by_fixture_id[p["id"]] = project
            project_count += 1
        db.flush()

        # voter_a is a member of the team that owns project id 1 ("Glass
        # Signal", fixture prj_01), so the self-vote rule is demonstrable:
        # voter_a tries to vote for their own team's project and is refused.
        db.add(
            TeamMember(
                team_id=project_by_fixture_id["prj_01"].team_id,
                user_id=users["voter_a"].id,
            )
        )
        db.flush()

        for name, weight, max_score in DEFAULT_RUBRIC:
            db.add(RubricCriterion(name=name, weight=weight, max_score=max_score))
        db.flush()

        # Assignment is implied by the presence of a score in the fixture:
        # there is no separate assignments list, so derive it this way. The
        # awkward cases (jdg_07's flat pattern, uneven review counts) are
        # seeded exactly as they appear in fixtures.json.
        assignment_keys = set()
        score_count = 0
        for s in fixtures.get("scores", []):
            judge_user = judge_user_by_fixture_id.get(s["judge"])
            project = project_by_fixture_id.get(s["project"])
            if judge_user is None:
                raise ValueError(f"score references unknown judge {s['judge']}")
            if project is None:
                raise ValueError(f"score references unknown project {s['project']}")
            key = (judge_user.id, project.id)
            if key not in assignment_keys:
                db.add(JudgeAssignment(judge_user_id=judge_user.id, project_id=project.id))
                assignment_keys.add(key)
            # fixtures.json has no score timestamps; anchor the seeded score
            # to the project's submission time (design choice, not a
            # requirement) so the columns stay deterministic.
            db.add(
                Score(
                    judge_user_id=judge_user.id,
                    project_id=project.id,
                    criteria_json=json.dumps(s.get("criteria") or {}),
                    comment=s.get("comment") or None,
                    submitted_at=project.submitted_at,
                    updated_at=project.submitted_at,
                )
            )
            score_count += 1
        db.commit()

        print(
            f"seeded {event.name}: {len(fixtures['teams'])} teams, "
            f"{project_count} projects"
        )
        print(
            f"seeded judging: {len(judge_user_by_fixture_id)} judges, "
            f"{len(assignment_keys)} assignments, {score_count} scores"
        )
        print()
        print("seeded. test logins:")
        for label, _email, _name, _role, token in TEST_ACCOUNTS:
            print(f"  {label.ljust(12)} Cookie: session={token}")
        print()
        print(f"database: {DB_PATH}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
