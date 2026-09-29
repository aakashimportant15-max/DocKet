"""Shared Jinja setup so every page renders with the logged-in user in context.

Design choice, not an official requirement: FastAPI's Jinja2Templates has no
context processors, so the small render() helper below injects `user` (and the
optional `active_nav` highlight) into every template uniformly.
"""
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

TEMPLATES = Jinja2Templates(directory=str(TEMPLATES_DIR))

GRADIENT_COUNT = 8


def track_class(track: str | None) -> str:
    """Deterministic gradient palette class from a track name.

    Summing character codes (not Python's hash(), which is salted per
    process) keeps a track on the same gradient across restarts, so the
    gallery looks stable without storing any presentation data.
    """
    key = (track or "").strip().lower()
    return f"grad-{sum(ord(c) for c in key) % GRADIENT_COUNT}"


TEMPLATES.env.globals["track_class"] = track_class


def render(request: Request, name: str, context: dict | None = None, user=None):
    ctx = {"user": user}
    ctx.update(context or {})
    return TEMPLATES.TemplateResponse(request=request, name=name, context=ctx)
