"""Save format conversion between MiSTer FPGA cores and emulator (RetroArch) formats.

Format facts (sizes, padding, layouts) researched from the MiSTer community and
the save-file-converter project's documentation:

- SNES, GB/GBC: byte-identical on both sides.
- PSX: raw 128 KiB memory card image on both sides.
- NES, SMS, GG: emulator saves are raw; MiSTer pads files to a 32 KiB minimum
  (uninitialized tail is harmless to emulators, so MiSTer->emulator is a copy).
- GBA: MiSTer may append RTC data after the save (file size becomes a
  non-power-of-2); emulators ignore/lack it, so it is stripped going to
  canonical. 512-byte EEPROM saves must be padded to 8 KiB for the MiSTer core.
- Genesis: emulator SRAM/FRAM saves are byte-expanded (each data byte becomes a
  16-bit word, fill byte 0x00 or 0xFF, or the data byte repeated); MiSTer wants
  plain bytes padded to 64 KiB with 0xFF. EEPROM saves (< 512 bytes) are not
  byte-expanded on either side.

Both directions must be deterministic: canonical hashes drive sync state.
"""

_GENESIS_SMALLEST_SRAM = 512
_GENESIS_MISTER_SIZE = 65536
_NES_SMS_MISTER_MIN_SIZE = 32768
_GBA_MISTER_MIN_SIZE = 8192


def _identity(data: bytes) -> bytes:
    return data


def _pad_to_min(data: bytes, size: int, fill: int) -> bytes:
    if len(data) >= size:
        return data
    return data + bytes([fill]) * (size - len(data))


def _is_power_of_2(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def _trailing_uniform_padding(data: bytes) -> int:
    """Number of trailing uniform 0x00 or 0xFF bytes that are padding.

    A save chip's true size is a power of 2, so padding never eats below the
    next power-of-2 boundary. A file that is entirely one value has no
    padding at all (an empty-but-real save).
    """
    if not data:
        return 0

    def run(value: int) -> int:
        n = 0
        for b in reversed(data):
            if b != value:
                break
            n += 1
        return n

    ff = run(0xFF)
    count = ff if ff > 0 else run(0x00)
    if count == len(data):
        return 0

    remaining = len(data) - count
    real_remaining = 1
    while real_remaining < remaining:
        real_remaining *= 2
    return max(count - (real_remaining - remaining), 0)


def _genesis_is_byte_expanded(data: bytes) -> bool:
    """True if every 16-bit big-endian word's high byte is 0x00, 0xFF, or a
    repeat of its low byte — the three known emulator expansion styles."""
    if len(data) % 2 != 0:
        return False
    for i in range(0, len(data), 2):
        high, low = data[i], data[i + 1]
        if high != low and high != 0x00 and high != 0xFF:
            return False
    return True


def _genesis_byte_expand(data: bytes) -> bytes:
    out = bytearray(len(data) * 2)
    out[1::2] = data
    return bytes(out)


def _genesis_byte_collapse(data: bytes) -> bytes:
    return data[1::2]


# ─── Per-system converters ────────────────────────────────────────────────────


def _nes_sms_to_mister(data: bytes) -> bytes:
    return _pad_to_min(data, _NES_SMS_MISTER_MIN_SIZE, 0x00)


def _gba_to_canonical(data: bytes) -> bytes:
    # A non-power-of-2 size means the MiSTer core appended RTC data; trim to
    # the largest power of 2 below the file size.
    if len(data) < 2 or _is_power_of_2(len(data)):
        return data
    size = 1
    while size * 2 < len(data):
        size *= 2
    return data[:size]


def _gba_to_mister(data: bytes) -> bytes:
    return _pad_to_min(data, _GBA_MISTER_MIN_SIZE, 0x00)


def _genesis_to_canonical(data: bytes) -> bytes:
    unpadded = data[: len(data) - _trailing_uniform_padding(data)]
    if len(unpadded) < _GENESIS_SMALLEST_SRAM:
        return unpadded  # EEPROM save — not byte-expanded on either side
    return _genesis_byte_expand(unpadded)


def _genesis_to_mister(data: bytes) -> bytes:
    plain = _genesis_byte_collapse(data) if _genesis_is_byte_expanded(data) else data
    return _pad_to_min(plain, _GENESIS_MISTER_SIZE, 0xFF)


# converter id → (to_canonical, to_mister)
CONVERTERS: dict[str, tuple] = {
    "identity": (_identity, _identity),
    "nes": (_identity, _nes_sms_to_mister),
    "sms": (_identity, _nes_sms_to_mister),
    "gba": (_gba_to_canonical, _gba_to_mister),
    "genesis": (_genesis_to_canonical, _genesis_to_mister),
}


def to_canonical(converter_id: str, data: bytes) -> bytes:
    """Convert MiSTer-form bytes to canonical (emulator) form."""
    return CONVERTERS[converter_id][0](data)


def to_mister(converter_id: str, data: bytes) -> bytes:
    """Convert canonical (emulator) bytes to MiSTer form."""
    return CONVERTERS[converter_id][1](data)
