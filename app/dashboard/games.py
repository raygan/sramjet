"""Dashboard games list and game detail pages."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.config
from app import manifest as mf
from app.database import get_db
from app.models import Version
from app.dashboard.templates import templates
from app.dashboard.utils import (
    extract_game_name,
    format_game_name,
    names_match,
    state_slot_sort_key,
)

router = APIRouter()


@router.get("/games", response_class=HTMLResponse)
async def dashboard_games(
    request: Request,
    sort: str = "recent",
    db: AsyncSession = Depends(get_db),
):
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)

    game_files: dict[str, list] = {}
    for path, hash_val in canonical_dict.items():
        if path.split('/')[0] not in ('saves', 'states'):
            continue
        name = extract_game_name(path)
        if name:
            game_files.setdefault(name, []).append(path)

    result = await db.execute(
        select(Version.file_path, Version.received_at).where(
            or_(
                Version.file_path.like('saves/%'),
                Version.file_path.like('states/%'),
            )
        )
    )

    epoch = datetime(1970, 1, 1)
    game_stats: dict[str, dict] = {}
    for file_path, received_at in result.all():
        name = extract_game_name(file_path)
        if not name or name not in game_files:
            continue
        stats = game_stats.setdefault(name, {'last_activity': epoch, 'activity_count': 0})
        stats['activity_count'] += 1
        if received_at > stats['last_activity']:
            stats['last_activity'] = received_at

    thumb_boxarts: dict[str, str] = {}
    for path, hash_val in canonical_dict.items():
        if '/Named_Boxarts/' not in path or mf.is_deleted(hash_val):
            continue
        tname = extract_game_name(path)
        if tname:
            thumb_boxarts[tname] = hash_val

    boxart: dict[str, str] = {}
    for game_name in game_files:
        for tname, hash_val in thumb_boxarts.items():
            if names_match(game_name, tname):
                boxart[game_name] = hash_val
                break

    games = []
    for name, files in game_files.items():
        base, meta = format_game_name(name)
        stats = game_stats.get(name, {'last_activity': epoch, 'activity_count': 0})
        games.append({
            'name': name,
            'base': base,
            'meta': meta,
            'file_count': len(files),
            'last_activity': stats['last_activity'],
            'activity_count': stats['activity_count'],
            'boxart_hash': boxart.get(name),
        })

    if sort == 'alpha':
        games.sort(key=lambda g: g['name'].lower())
    elif sort == 'activity':
        games.sort(key=lambda g: g['activity_count'], reverse=True)
    else:
        games.sort(key=lambda g: g['last_activity'], reverse=True)

    return templates.TemplateResponse(
        request, "games.html",
        context={"games": games, "sort": sort},
    )


@router.get("/games/{name:path}", response_class=HTMLResponse)
async def dashboard_game_detail(name: str, request: Request):
    canonical = mf.load_canonical(app.config.CANONICAL_MANIFEST)
    canonical_dict = mf.to_dict(canonical)

    saves, states, roms = [], [], []
    boxarts, snaps, titles = [], [], []

    for path, hash_val in canonical_dict.items():
        extracted = extract_game_name(path)
        if not extracted or not names_match(extracted, name):
            continue
        entry = {'path': path, 'hash': hash_val, 'display': path.split('/')[-1]}
        top = path.split('/')[0]
        if top == 'saves':
            saves.append(entry)
        elif top == 'states' and not mf.is_deleted(hash_val):
            states.append(entry)
        elif top == 'system':
            roms.append(entry)
        elif top == 'thumbnails' and not mf.is_deleted(hash_val):
            if '/Named_Boxarts/' in path:
                boxarts.append(entry)
            elif '/Named_Snaps/' in path:
                snaps.append(entry)
            elif '/Named_Titles/' in path:
                titles.append(entry)

    state_paths = {e['path'] for e in states if not e['path'].endswith('.png')}
    state_png_map = {}
    states_no_png = []
    for e in states:
        if e['path'].endswith('.png'):
            base_path = e['path'][:-4]
            if base_path in state_paths:
                state_png_map[base_path] = e['hash']
        else:
            states_no_png.append(e)
    states_no_png.sort(key=state_slot_sort_key)

    base, meta = format_game_name(name)
    return templates.TemplateResponse(
        request, "game_detail.html",
        context={
            "name": name, "base": base, "meta": meta,
            "saves": saves, "states": states_no_png, "state_png_map": state_png_map,
            "roms": roms, "boxarts": boxarts, "snaps": snaps, "titles": titles,
        },
    )
