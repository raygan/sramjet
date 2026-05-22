"""Unit tests for dashboard utility functions."""

from datetime import date

import pytest

from app.dashboard.utils import (
    compute_streaks,
    extract_game_name,
    fmt_date,
    fmt_size,
    format_game_name,
    names_match,
    state_slot,
    state_slot_sort_key,
    streak_icon,
)


# ─── extract_game_name ────────────────────────────────────────────────────────

class TestExtractGameName:
    def test_save_file(self):
        assert extract_game_name("saves/mGBA/Mother 3.srm") == "Mother 3"

    def test_save_strips_extension(self):
        assert extract_game_name("saves/SNES/Super Metroid.sav") == "Super Metroid"

    def test_state_file(self):
        assert extract_game_name("states/mGBA/Mother 3.state1") == "Mother 3"

    def test_state_auto(self):
        assert extract_game_name("states/mGBA/Mother 3.state.auto") == "Mother 3"

    def test_state_with_tags(self):
        name = extract_game_name("states/mGBA/Mother 3 (Japan) [T-En].state1")
        assert name == "Mother 3 (Japan) [T-En]"

    def test_system_file_rom(self):
        assert extract_game_name("system/roms/game.sfc") == "game"

    def test_thumbnail_boxart(self):
        assert extract_game_name("thumbnails/Named_Boxarts/Mother 3.png") == "Mother 3"

    def test_unknown_top_dir_returns_none(self):
        assert extract_game_name("config/retroarch.cfg") is None

    def test_no_slash_returns_none(self):
        assert extract_game_name("game.sav") is None

    def test_bare_extension_returns_none(self):
        # file whose name IS the extension — stripping leaves empty string
        assert extract_game_name("saves/core/.sav") is None or \
               extract_game_name("saves/core/.sav") == ""


# ─── format_game_name ─────────────────────────────────────────────────────────

class TestFormatGameName:
    def test_plain_name(self):
        assert format_game_name("Mother 3") == ("Mother 3", "")

    def test_with_parentheses(self):
        base, meta = format_game_name("Mother 3 (Japan)")
        assert base == "Mother 3"
        assert meta == "(Japan)"

    def test_with_brackets(self):
        base, meta = format_game_name("Mother 3 [T-En]")
        assert base == "Mother 3"
        assert meta == "[T-En]"

    def test_with_both(self):
        base, meta = format_game_name("Mother 3 (Japan) [T-En by Chewy]")
        assert base == "Mother 3"
        assert "(Japan)" in meta

    def test_no_trailing_space_in_base(self):
        base, _ = format_game_name("Game Title (Region)")
        assert not base.endswith(" ")


# ─── names_match ──────────────────────────────────────────────────────────────

class TestNamesMatch:
    def test_exact_match(self):
        assert names_match("Mother 3", "Mother 3")

    def test_prefix_match(self):
        assert names_match("Mother 3 (Japan)", "Mother 3 (Japan) [T-En by Chewy]")

    def test_reverse_prefix_match(self):
        assert names_match("Mother 3 (Japan) [T-En by Chewy]", "Mother 3 (Japan)")

    def test_no_match(self):
        assert not names_match("Mother 3", "Earthbound")

    def test_mid_token_prefix_rejected(self):
        # "Mother" is a prefix of "Mother 3" but not at a word boundary
        # This should NOT match — "Mother" followed by " 3", which does
        # start with a space, so actually it WOULD match.
        # Test a genuine non-word-boundary case:
        assert not names_match("Moth", "Mother 3")

    def test_case_sensitive(self):
        assert not names_match("mother 3", "Mother 3")

    def test_whitespace_stripped(self):
        assert names_match("  Mother 3  ", "Mother 3")


# ─── state_slot ───────────────────────────────────────────────────────────────

class TestStateSlot:
    def test_auto_state(self):
        assert state_slot("states/mGBA/game.state.auto") == "Auto"

    def test_slot_zero_implicit(self):
        assert state_slot("states/mGBA/game.state") == "Slot 0"

    def test_slot_one(self):
        assert state_slot("states/mGBA/game.state1") == "Slot 1"

    def test_slot_ten(self):
        assert state_slot("states/mGBA/game.state10") == "Slot 10"

    def test_save_file_returns_none(self):
        assert state_slot("saves/mGBA/game.srm") is None

    def test_non_state_returns_none(self):
        assert state_slot("saves/core/game.sav") is None

    def test_case_insensitive(self):
        assert state_slot("states/mGBA/game.STATE.AUTO") == "Auto"


class TestStateSlotSortKey:
    def test_auto_sorts_first(self):
        auto = state_slot_sort_key({"path": "states/core/game.state.auto"})
        slot1 = state_slot_sort_key({"path": "states/core/game.state1"})
        assert auto < slot1

    def test_slot_order(self):
        slot1 = state_slot_sort_key({"path": "states/core/game.state1"})
        slot2 = state_slot_sort_key({"path": "states/core/game.state2"})
        assert slot1 < slot2


# ─── fmt_size ─────────────────────────────────────────────────────────────────

class TestFmtSize:
    def test_bytes(self):
        val, unit = fmt_size(512)
        assert unit == "B"
        assert val == "512"

    def test_kilobytes(self):
        val, unit = fmt_size(2048)
        assert unit == "KB"

    def test_megabytes(self):
        val, unit = fmt_size(5 * 1024 * 1024)
        assert unit == "MB"

    def test_gigabytes(self):
        val, unit = fmt_size(2 * 1024 ** 3)
        assert unit == "GB"

    def test_zero(self):
        val, unit = fmt_size(0)
        assert unit == "B"
        assert val == "0"


# ─── compute_streaks ──────────────────────────────────────────────────────────

class TestComputeStreaks:
    def test_empty(self):
        s = compute_streaks([], date(2026, 5, 21))
        assert s["current"] == 0
        assert s["longest"] == 0
        assert s["current_start"] is None

    def test_single_day_today(self):
        today = date(2026, 5, 21)
        s = compute_streaks([today], today)
        assert s["current"] == 1
        assert s["longest"] == 1

    def test_single_day_yesterday(self):
        today = date(2026, 5, 21)
        yesterday = date(2026, 5, 20)
        s = compute_streaks([yesterday], today)
        assert s["current"] == 1

    def test_consecutive_days(self):
        today = date(2026, 5, 21)
        days = [date(2026, 5, d) for d in [19, 20, 21]]
        s = compute_streaks(days, today)
        assert s["current"] == 3
        assert s["longest"] == 3

    def test_broken_streak(self):
        today = date(2026, 5, 21)
        # Gap on the 20th — current streak is just today
        days = [date(2026, 5, 18), date(2026, 5, 19), date(2026, 5, 21)]
        s = compute_streaks(days, today)
        assert s["current"] == 1
        assert s["longest"] == 2

    def test_longest_is_historical(self):
        today = date(2026, 5, 21)
        # A 5-day run in the past, and only today active now
        days = [date(2026, 5, d) for d in [1, 2, 3, 4, 5, 21]]
        s = compute_streaks(days, today)
        assert s["current"] == 1
        assert s["longest"] == 5

    def test_no_active_streak(self):
        today = date(2026, 5, 21)
        # Last played 3 days ago
        days = [date(2026, 5, 18)]
        s = compute_streaks(days, today)
        assert s["current"] == 0

    def test_streak_dates_recorded(self):
        today = date(2026, 5, 21)
        days = [date(2026, 5, 19), date(2026, 5, 20), date(2026, 5, 21)]
        s = compute_streaks(days, today)
        assert s["current_start"] == date(2026, 5, 19)
        assert s["current_end"] == date(2026, 5, 21)


# ─── streak_icon ──────────────────────────────────────────────────────────────

class TestStreakIcon:
    def test_zero(self):
        assert "streak-0" in streak_icon(0)

    def test_one(self):
        assert "streak-1" in streak_icon(1)

    def test_sword_at_22(self):
        assert "sword" in streak_icon(22)

    def test_sword_at_100(self):
        assert "sword" in streak_icon(100)


# ─── fmt_date ─────────────────────────────────────────────────────────────────

class TestFmtDate:
    def test_none_returns_empty(self):
        assert fmt_date(None) == ""

    def test_date_object(self):
        result = fmt_date(date(2026, 5, 21))
        assert "May" in result
        assert "21" in result
