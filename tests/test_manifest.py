"""Tests for manifest parsing and diffing."""

import pytest

from app import manifest as mf


def test_parse_roundtrip():
    raw = b'[{"path": "saves/game.sav", "hash": "abc123"}, {"path": "states/game.state", "hash": "def456"}]'
    parsed = mf.parse(raw)
    assert len(parsed) == 2
    assert parsed[0]["path"] == "saves/game.sav"


def test_parse_sorts_by_path():
    raw = b'[{"path": "z.sav", "hash": "1"}, {"path": "a.sav", "hash": "2"}]'
    parsed = mf.parse(raw)
    assert parsed[0]["path"] == "a.sav"
    assert parsed[1]["path"] == "z.sav"


def test_serialize_sorts():
    manifest = [{"path": "z", "hash": "1"}, {"path": "a", "hash": "2"}]
    data = mf.serialize(manifest)
    reparsed = mf.parse(data)
    assert reparsed[0]["path"] == "a"


def test_to_dict():
    manifest = [{"path": "saves/game.sav", "hash": "abc"}]
    d = mf.to_dict(manifest)
    assert d == {"saves/game.sav": "abc"}


def test_from_dict():
    d = {"saves/game.sav": "abc", "states/game.state": "def"}
    manifest = mf.from_dict(d)
    assert len(manifest) == 2
    assert manifest[0]["path"] == "saves/game.sav"


def test_is_deleted():
    assert mf.is_deleted("") is True
    assert mf.is_deleted("abc123") is False


def test_diff_added():
    server = []
    incoming = [{"path": "saves/new.sav", "hash": "abc"}]
    result = mf.diff(server, incoming)
    assert "saves/new.sav" in result["added"]
    assert result["modified"] == []
    assert result["deleted"] == []


def test_diff_modified():
    server = [{"path": "saves/game.sav", "hash": "old"}]
    incoming = [{"path": "saves/game.sav", "hash": "new"}]
    result = mf.diff(server, incoming)
    assert "saves/game.sav" in result["modified"]


def test_diff_deleted():
    server = [{"path": "saves/game.sav", "hash": "abc"}]
    incoming = [{"path": "saves/game.sav", "hash": ""}]
    result = mf.diff(server, incoming)
    assert "saves/game.sav" in result["deleted"]


def test_diff_unchanged():
    server = [{"path": "saves/game.sav", "hash": "abc"}]
    incoming = [{"path": "saves/game.sav", "hash": "abc"}]
    result = mf.diff(server, incoming)
    assert "saves/game.sav" in result["unchanged"]
    assert result["modified"] == []
