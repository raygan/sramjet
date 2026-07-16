"""Unit tests for the MiSTer client's sync planning logic."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "mister_sync", Path(__file__).parent.parent / "clients" / "mister_sync.py"
)
mister_sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mister_sync)

plan_sync = mister_sync.plan_sync


def entry(path: str, hash: str, mtime: float = 100.0) -> dict:
    return {"path": path, "hash": hash, "mtime": mtime}


def state(local: str, canonical: str) -> dict:
    return {"local": local, "canonical": canonical}


def test_in_sync_is_noop():
    plan = plan_sync(
        [entry("SNES/a.sav", "c1")],
        {"SNES/a.sav": "l1"},
        {"SNES/a.sav": state("l1", "c1")},
        {},
    )
    assert all(not v for v in plan.values())


def test_new_local_file_uploads():
    plan = plan_sync([], {"SNES/a.sav": "l1"}, {}, {})
    assert plan["upload"] == ["SNES/a.sav"]


def test_new_server_file_downloads():
    plan = plan_sync([entry("SNES/a.sav", "c1")], {}, {}, {})
    assert plan["download"] == ["SNES/a.sav"]


def test_local_change_uploads():
    plan = plan_sync(
        [entry("SNES/a.sav", "c1")],
        {"SNES/a.sav": "l2"},
        {"SNES/a.sav": state("l1", "c1")},
        {},
    )
    assert plan["upload"] == ["SNES/a.sav"]


def test_server_change_downloads():
    plan = plan_sync(
        [entry("SNES/a.sav", "c2")],
        {"SNES/a.sav": "l1"},
        {"SNES/a.sav": state("l1", "c1")},
        {},
    )
    assert plan["download"] == ["SNES/a.sav"]


def test_conflict_local_newer_uploads():
    plan = plan_sync(
        [entry("SNES/a.sav", "c2", mtime=100)],
        {"SNES/a.sav": "l2"},
        {"SNES/a.sav": state("l1", "c1")},
        {"SNES/a.sav": 200},
    )
    assert plan["upload"] == ["SNES/a.sav"]


def test_conflict_server_newer_downloads():
    plan = plan_sync(
        [entry("SNES/a.sav", "c2", mtime=300)],
        {"SNES/a.sav": "l2"},
        {"SNES/a.sav": state("l1", "c1")},
        {"SNES/a.sav": 200},
    )
    assert plan["download"] == ["SNES/a.sav"]


def test_server_tombstone_deletes_local():
    plan = plan_sync(
        [entry("SNES/a.sav", "")],
        {"SNES/a.sav": "l1"},
        {"SNES/a.sav": state("l1", "c1")},
        {},
    )
    assert plan["delete_local"] == ["SNES/a.sav"]


def test_local_delete_propagates():
    plan = plan_sync(
        [entry("SNES/a.sav", "c1")],
        {},
        {"SNES/a.sav": state("l1", "c1")},
        {},
    )
    assert plan["delete_remote"] == ["SNES/a.sav"]


def test_both_deleted_is_noop():
    plan = plan_sync(
        [entry("SNES/a.sav", "")],
        {},
        {"SNES/a.sav": state("l1", "c1")},
        {},
    )
    assert all(not v for v in plan.values())


def test_local_progress_after_server_tombstone_recreates():
    plan = plan_sync(
        [entry("SNES/a.sav", "")],
        {"SNES/a.sav": "l2"},
        {"SNES/a.sav": state("l1", "c1")},
        {},
    )
    assert plan["upload"] == ["SNES/a.sav"]


def test_first_contact_both_exist_uses_mtime():
    plan = plan_sync(
        [entry("SNES/a.sav", "c1", mtime=500)],
        {"SNES/a.sav": "l1"},
        {},
        {"SNES/a.sav": 100},
    )
    assert plan["download"] == ["SNES/a.sav"]


def test_sync_dirs_fallback_parses():
    assert "SNES" in mister_sync.SYNC_DIRS
