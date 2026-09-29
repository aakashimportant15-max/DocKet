from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.routers import (
    auth_router,
    events_router,
    gallery_router,
    judging_router,
    submissions_router,
    teams_router,
    voting_router,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Docket", version="0.2.0")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(gallery_router.router)
app.include_router(auth_router.router)
app.include_router(events_router.router)
app.include_router(teams_router.router)
app.include_router(submissions_router.router)
app.include_router(judging_router.router)
app.include_router(voting_router.router)


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse(url="/gallery")
