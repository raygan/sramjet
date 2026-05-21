"""Dashboard help page."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.dashboard.templates import templates

router = APIRouter()


@router.get("/help", response_class=HTMLResponse)
async def dashboard_help(request: Request):
    return templates.TemplateResponse(request, "help.html")
