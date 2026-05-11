import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

from app.api.router import router as api_router
from app.config import ensure_dirs
from app.dashboard.router import router as dashboard_router
from app.database import init_db
from app.webdav.router import router as webdav_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    await init_db()
    yield


app = FastAPI(title="SRAMjet", lifespan=lifespan)

app.include_router(webdav_router)
app.include_router(api_router)
app.include_router(dashboard_router)
