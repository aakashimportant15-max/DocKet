from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UTCTimestamp(TypeDecorator):
    """DateTime column that always reads back as tz-aware UTC.

    Design choice, not an official requirement: SQLite stores naive datetimes,
    so without this wrapper comparing a loaded value against
    datetime.now(timezone.utc) raises TypeError instead of answering.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("refusing to store a naive datetime; pass UTC-aware")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


def parse_utc(value: str) -> datetime:
    """Parse an ISO 8601 string like '2026-03-01T18:00:00Z' into aware UTC.

    A naive string is assumed to already be UTC (design choice, not an
    official requirement).
    """
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid ISO 8601 timestamp: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32))  # organizer | participant | judge
    password_hash: Mapped[str] = mapped_column(String(128))
    session_token: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    # Fixture judge id (jdg_01 ...) for judge-role users. The judge isolation
    # check compares this string against the requested judge before any score
    # query runs. Merged test accounts: judge_a is jdg_01, judge_b is jdg_02.
    judge_code: Mapped[str | None] = mapped_column(
        String(32), unique=True, index=True, nullable=True
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    submissions_close: Mapped[datetime] = mapped_column(UTCTimestamp())
    # Community-voting window (T3). Nullable because events created through
    # the T1 API have no voting dates; app/voting.py documents what a missing
    # bound means. The seed sets both.
    voting_opens: Mapped[datetime | None] = mapped_column(
        UTCTimestamp(), nullable=True
    )
    voting_closes: Mapped[datetime | None] = mapped_column(
        UTCTimestamp(), nullable=True
    )

    teams: Mapped[list["Team"]] = relationship(back_populates="event")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    event: Mapped["Event"] = relationship(back_populates="teams")
    members: Mapped[list["TeamMember"]] = relationship(back_populates="team")
    projects: Mapped[list["Project"]] = relationship(back_populates="team")


class TeamMember(Base):
    __tablename__ = "team_members"

    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)

    team: Mapped["Team"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship()


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    track: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text, default="")
    repo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(UTCTimestamp())
    updated_at: Mapped[datetime] = mapped_column(UTCTimestamp())

    team: Mapped["Team"] = relationship(back_populates="projects")


class JudgeAssignment(Base):
    __tablename__ = "judge_assignments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    judge_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    # Conflict-of-interest declaration, submitted by the judge themselves.
    # Advisory only: it never blocks scoring and never affects the CSV export.
    conflict_declared: Mapped[bool] = mapped_column(default=False)
    conflict_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    conflict_declared_at: Mapped[datetime | None] = mapped_column(
        UTCTimestamp(), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("judge_user_id", "project_id", name="uq_judge_assignment"),
    )

    judge: Mapped["User"] = relationship()
    project: Mapped["Project"] = relationship()


class RubricCriterion(Base):
    __tablename__ = "rubric_criteria"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    max_score: Mapped[int] = mapped_column(Integer, default=5)


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Duplicated on purpose, not only reachable through JudgeAssignment: the
    # isolation check must be a single WHERE judge_user_id = :id comparison.
    judge_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    criteria_json: Mapped[str] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(UTCTimestamp())
    updated_at: Mapped[datetime] = mapped_column(UTCTimestamp())

    __table_args__ = (
        UniqueConstraint("judge_user_id", "project_id", name="uq_score_per_pair"),
    )

    judge: Mapped["User"] = relationship()
    project: Mapped["Project"] = relationship()


class Vote(Base):
    """One community vote: one voter, one project, at most once.

    The UNIQUE constraint is the real defence against double voting — the
    route checks are just there to turn it into a friendly 400 instead of a
    500 from the database.
    """

    __tablename__ = "votes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    voter_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCTimestamp())

    __table_args__ = (
        UniqueConstraint("voter_user_id", "project_id", name="uq_vote_per_project"),
    )

    voter: Mapped["User"] = relationship()
    project: Mapped["Project"] = relationship()


class Comment(Base):
    """A public comment on a project. Plain text body, escaped on render."""

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCTimestamp())

    author: Mapped["User"] = relationship()
    project: Mapped["Project"] = relationship()
