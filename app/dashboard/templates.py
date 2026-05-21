"""Jinja2 template engine — single instance shared across all dashboard routes."""

from urllib.parse import quote

from fastapi.templating import Jinja2Templates

from app.dashboard.utils import _SAVE_EXT, _ROM_EXT, state_slot

templates = Jinja2Templates(directory="templates")

templates.env.filters["basename"]     = lambda p: p.split("/")[-1]
templates.env.filters["dirname"]      = lambda p: "/".join(p.split("/")[:-1])
templates.env.filters["url_encode"]   = lambda s: quote(str(s), safe="")
templates.env.filters["is_save_file"] = lambda p: bool(_SAVE_EXT.search(p))
templates.env.filters["is_rom_file"]  = lambda p: bool(_ROM_EXT.search(p))
templates.env.filters["state_slot"]   = state_slot
