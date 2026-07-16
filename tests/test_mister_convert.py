"""Tests for MiSTer <-> canonical save format conversion."""

from app.mister.convert import (
    CONVERTERS,
    to_canonical,
    to_mister,
    _genesis_byte_expand,
    _genesis_is_byte_expanded,
    _trailing_uniform_padding,
)


# ─── Identity systems ─────────────────────────────────────────────────────────

def test_identity_both_directions():
    data = bytes(range(256)) * 32  # 8 KiB SNES-style save
    assert to_canonical("identity", data) == data
    assert to_mister("identity", data) == data


# ─── NES / SMS padding ────────────────────────────────────────────────────────

def test_nes_pads_small_save_to_32k():
    data = bytes([0xAB]) * 8192
    out = to_mister("nes", data)
    assert len(out) == 32768
    assert out[:8192] == data
    assert out[8192:] == bytes(24576)


def test_nes_leaves_large_save_alone():
    data = bytes([0xCD]) * 131072  # 128 KiB save — some games do this
    assert to_mister("nes", data) == data


def test_nes_mister_to_canonical_is_copy():
    data = bytes([0x11]) * 32768
    assert to_canonical("nes", data) == data


# ─── GBA ──────────────────────────────────────────────────────────────────────

def test_gba_strips_rtc_tail():
    save = bytes([0x22]) * 32768
    with_rtc = save + bytes([0x33]) * 68  # non-power-of-2 => RTC appended
    assert to_canonical("gba", with_rtc) == save


def test_gba_power_of_2_untouched():
    save = bytes([0x22]) * 32768
    assert to_canonical("gba", save) == save


def test_gba_pads_eeprom_to_8k():
    eeprom = bytes([0x44]) * 512
    out = to_mister("gba", eeprom)
    assert len(out) == 8192
    assert out[:512] == eeprom


def test_gba_large_save_not_padded():
    save = bytes([0x55]) * 65536
    assert to_mister("gba", save) == save


# ─── Genesis ──────────────────────────────────────────────────────────────────

def test_genesis_byte_expand_layout():
    assert _genesis_byte_expand(b"HELLO") == b"\x00H\x00E\x00L\x00L\x00O"


def test_genesis_is_byte_expanded():
    assert _genesis_is_byte_expanded(b"\x00H\x00E")          # 0x00 fill
    assert _genesis_is_byte_expanded(b"\xffH\xffE")          # 0xFF fill
    assert _genesis_is_byte_expanded(b"HHEE")                # repeat fill
    assert not _genesis_is_byte_expanded(b"HELLO!")          # plain data
    assert not _genesis_is_byte_expanded(b"\x00H\x00")       # odd length


def test_genesis_mister_to_canonical_expands():
    plain = bytes([0x10, 0x20, 0x30, 0x40]) * 2048  # 8 KiB SRAM
    padded = plain + bytes([0xFF]) * (65536 - len(plain))
    out = to_canonical("genesis", padded)
    assert len(out) == len(plain) * 2
    assert out[1::2] == plain
    assert set(out[0::2]) == {0x00}


def test_genesis_eeprom_not_expanded():
    eeprom = bytes([0x66]) * 128  # < 512 bytes => EEPROM
    assert to_canonical("genesis", eeprom) == eeprom


def test_genesis_canonical_to_mister_collapses_and_pads():
    plain = bytes([0x10, 0x20, 0x30, 0x40]) * 2048
    expanded = _genesis_byte_expand(plain)
    out = to_mister("genesis", expanded)
    assert len(out) == 65536
    assert out[: len(plain)] == plain
    assert set(out[len(plain):]) == {0xFF}


def test_genesis_ff_expanded_canonical_also_collapses():
    plain = bytes([0x10, 0x20]) * 4096
    ff_expanded = bytes(b for byte in plain for b in (0xFF, byte))
    out = to_mister("genesis", ff_expanded)
    assert out[: len(plain)] == plain


def test_genesis_round_trip_stable():
    plain = bytes(range(1, 256)) * 33  # arbitrary data, > 512 bytes, odd content
    mister = to_mister("genesis", _genesis_byte_expand(plain))
    canonical = to_canonical("genesis", mister)
    assert to_canonical("genesis", to_mister("genesis", canonical)) == canonical


# ─── Padding heuristic edge cases ─────────────────────────────────────────────

def test_padding_all_uniform_file_is_not_padding():
    assert _trailing_uniform_padding(bytes([0xFF]) * 65536) == 0


def test_padding_respects_power_of_2_boundary():
    # 8 KiB of data whose last byte happens to be 0xFF must stay 8 KiB
    data = bytes([0x01]) * 8191 + bytes([0xFF])
    padded = data + bytes([0xFF]) * (65536 - len(data))
    assert len(data) - 0 + _trailing_uniform_padding(padded) == 65536
    assert padded[: len(padded) - _trailing_uniform_padding(padded)] == data


def test_padding_no_padding_odd_size():
    data = bytes([0x01]) * 100
    assert _trailing_uniform_padding(data) == 0


def test_converters_table_complete():
    assert set(CONVERTERS) == {"identity", "nes", "sms", "gba", "genesis"}
