"""Dashboard router — aggregates all sub-routers into a single APIRouter.

Sub-modules:
  templates.py  — Jinja2Templates instance and filter registration
  utils.py      — shared pure helpers (game names, formatting, streaks, etc.)
  home.py       — GET /
  timeline.py   — GET /timeline
  devices.py    — GET /devices, POST /devices/{name}/...
  games.py      — GET /games, GET /games/{name}
  files.py      — GET /files, GET /files/{path}, POST /files/{path}/revert/{id}
  help.py       — GET /help
"""

from fastapi import APIRouter

from app.dashboard.devices import router as devices_router
from app.dashboard.files import router as files_router
from app.dashboard.games import router as games_router
from app.dashboard.help import router as help_router
from app.dashboard.home import router as home_router
from app.dashboard.timeline import router as timeline_router

router = APIRouter()

router.include_router(home_router)
router.include_router(timeline_router)
router.include_router(devices_router)
router.include_router(games_router)
router.include_router(files_router)
router.include_router(help_router)
