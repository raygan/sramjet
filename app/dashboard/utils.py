"""Shared helpers for the dashboard — pure functions, no FastAPI or DB dependencies."""

import re
from datetime import date, timedelta

# ─── File type patterns ───────────────────────────────────────────────────────

_SAVE_EXT  = re.compile(r'\.(srm|sav|mcr|fla|rtc)$', re.IGNORECASE)
_STATE_EXT = re.compile(r'\.state.*$',               re.IGNORECASE)
_ROM_EXT   = re.compile(
    r'\.(zip|sfc|smc|gba|gb|gbc|nds|nes|md|gen|bin|z64|v64|n64|iso|pce|gg|smd|rom)$',
    re.IGNORECASE,
)
_THUMB_EXT = re.compile(r'\.png$', re.IGNORECASE)


# ─── State slot helpers ───────────────────────────────────────────────────────

def state_slot(path: str) -> str | None:
    """Return a human-readable slot label for a state file path, or None."""
    name = path.split("/")[-1]
    if re.search(r"\.state\.auto$", name, re.IGNORECASE):
        return "Auto"
    m = re.search(r"\.state(\d*)$", name, re.IGNORECASE)
    if m:
        return f"Slot {m.group(1) or '0'}"
    return None


def state_slot_sort_key(entry: dict) -> tuple:
    slot = state_slot(entry["path"])
    if slot == "Auto":
        return (0, 0)
    if slot is not None:
        try:
            return (1, int(slot.split()[-1]))
        except ValueError:
            return (1, 0)
    return (2, 0)


# ─── Device color ─────────────────────────────────────────────────────────────

_DEVICE_COLORS = ["blue", "violet", "emerald", "orange", "pink", "teal", "red", "indigo"]

def device_color(name: str) -> str:
    idx = sum(ord(c) for c in (name or "")) % len(_DEVICE_COLORS)
    return _DEVICE_COLORS[idx]


# ─── Game name helpers ────────────────────────────────────────────────────────

def extract_game_name(path: str) -> str | None:
    """Return the game name from a canonical path, or None if not a game file."""
    parts = path.split('/')
    if len(parts) < 2:
        return None
    top, filename = parts[0], parts[-1]
    if top == 'saves':
        name = _SAVE_EXT.sub('', filename)
    elif top == 'states':
        name = _STATE_EXT.sub('', filename)
    elif top == 'system':
        name = _ROM_EXT.sub('', filename)
    elif top == 'thumbnails':
        name = _THUMB_EXT.sub('', filename)
    else:
        return None
    return name if name != filename else None


def format_game_name(name: str) -> tuple[str, str]:
    """Split 'Foo (Bar) [Baz]' into ('Foo', '(Bar) [Baz]') for display."""
    m = re.search(r'[\(\[]', name)
    if m:
        return name[:m.start()].rstrip(), name[m.start():]
    return name, ''


def names_match(a: str, b: str) -> bool:
    """Return True if two game names refer to the same game.

    'Mother 3 (Japan)' matches 'Mother 3 (Japan) [T-En...]' because
    the shorter name is a word-boundary prefix of the longer one.
    """
    a, b = a.strip(), b.strip()
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if longer.startswith(shorter):
        return longer[len(shorter):len(shorter) + 1] == ' '
    return False


# ─── Formatting helpers ───────────────────────────────────────────────────────

def fmt_size(n: int) -> tuple[str, str]:
    if n < 1024:        return str(n), "B"
    if n < 1024 ** 2:   return f"{n / 1024:.1f}", "KB"
    if n < 1024 ** 3:   return f"{n / 1024 ** 2:.1f}", "MB"
    return f"{n / 1024 ** 3:.2f}", "GB"


def fmt_date(d) -> str:
    return d.strftime("%b %-d") if d else ""


def fmt_date_long(d) -> str:
    return d.strftime("%b %-d, %Y") if d else ""


def streak_icon(n: int) -> str:
    if n == 0:   return "/static/icons/streak-0.png"
    if n == 1:   return "/static/icons/streak-1.png"
    if n <= 4:   return "/static/icons/streak-2.png"
    if n <= 10:  return "/static/icons/streak-3.png"
    if n <= 21:  return "/static/icons/streak-4.png"
    return "/static/icons/streak-sword.png"


# ─── Streak calculation ───────────────────────────────────────────────────────

def compute_streaks(upload_dates: list[date], today: date) -> dict:
    """Compute current and longest streaks from a sorted list of unique gaming dates."""
    dates_set = set(upload_dates)
    yesterday = today - timedelta(days=1)
    anchor = today if today in dates_set else (yesterday if yesterday in dates_set else None)

    current_streak, current_start, current_end = 0, None, None
    if anchor is not None:
        current_end = anchor
        d = anchor
        while d in dates_set:
            current_streak += 1
            current_start = d
            d -= timedelta(days=1)

    longest, longest_start, longest_end = 0, None, None
    if upload_dates:
        run, run_start = 1, upload_dates[0]
        for i in range(1, len(upload_dates)):
            if (upload_dates[i] - upload_dates[i - 1]).days == 1:
                run += 1
            else:
                if run > longest:
                    longest, longest_start, longest_end = run, run_start, upload_dates[i - 1]
                run, run_start = 1, upload_dates[i]
        if run > longest:
            longest, longest_start, longest_end = run, run_start, upload_dates[-1]

    return {
        "current": current_streak,
        "current_start": current_start,
        "current_end": current_end,
        "longest": longest,
        "longest_start": longest_start,
        "longest_end": longest_end,
    }
