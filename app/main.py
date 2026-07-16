import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

from app.api.router import router as api_router
from app.auth import require_ui_auth, require_webdav_auth
from app.config import ensure_dirs
from app.dashboard.router import router as dashboard_router
from app.database import init_db
from app.mister.router import client_router as mister_client_router
from app.mister.router import router as mister_router
from app.webdav.router import router as webdav_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    await init_db()
    yield


app = FastAPI(title="SRAMjet", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(webdav_router, dependencies=[Depends(require_webdav_auth)])
app.include_router(mister_router, dependencies=[Depends(require_webdav_auth)])
app.include_router(mister_client_router, dependencies=[Depends(require_ui_auth)])
app.include_router(api_router, dependencies=[Depends(require_ui_auth)])
app.include_router(dashboard_router, dependencies=[Depends(require_ui_auth)])
