"""Path mapping between MiSTer save trees and SRAMjet canonical paths.

MiSTer stores saves per system:      <saves root>/GBA/game.sav
RetroArch (canonical) per core:      saves/mGBA/game.srm

Each system maps ALL of its MiSTer directories to ONE configured RetroArch
core directory (overridable via the MISTER_CORES env var — see app.config).
Sibling MiSTer directories (GAMEBOY/GBC/SGB) map to the same canonical path,
and every canonical file is exposed under all of its system's MiSTer
directories, mirroring how the MiSTer core simply ignores saves for games it
never loads.
"""

from dataclasses import dataclass

import app.config


@dataclass(frozen=True)
class System:
    id: str
    mister_dirs: tuple[str, ...]
    default_core: str
    ra_ext: str
    converter: str


SYSTEMS: tuple[System, ...] = (
    System("nes", ("NES",), "FCEUmm", "srm", "nes"),
    System("snes", ("SNES",), "Snes9x", "srm", "identity"),
    System("gb", ("GAMEBOY", "GBC", "SGB"), "Gambatte", "srm", "identity"),
    System("gba", ("GBA",), "mGBA", "srm", "gba"),
    System("genesis", ("Genesis", "MegaDrive"), "Genesis Plus GX", "srm", "genesis"),
    # SMS/GG default to dedicated cores rather than Genesis Plus GX: if they
    # shared the genesis core folder the reverse mapping would need content
    # discrimination to tell the formats apart (out of scope for v1).
    System("sms", ("SMS",), "SMS Plus GX", "srm", "sms"),
    System("gg", ("GameGear",), "Gearsystem", "srm", "sms"),
    System("psx", ("PSX",), "Beetle PSX HW", "srm", "identity"),
)

MISTER_EXT = "sav"


def core_for(system: System) -> str:
    return app.config.MISTER_CORES.get(system.id, system.default_core)


def _system_by_mister_dir(dir_name: str) -> System | None:
    for system in SYSTEMS:
        if dir_name in system.mister_dirs:
            return system
    return None


def mister_to_canonical(path: str) -> tuple[str, System] | None:
    """'GBA/game.sav' -> ('saves/mGBA/game.srm', gba). None if unmapped."""
    parts = path.split("/")
    if len(parts) != 2:
        return None
    dir_name, filename = parts
    system = _system_by_mister_dir(dir_name)
    if system is None or not filename.endswith(f".{MISTER_EXT}"):
        return None
    stem = filename[: -len(MISTER_EXT) - 1]
    return f"saves/{core_for(system)}/{stem}.{system.ra_ext}", system


def canonical_to_mister(path: str) -> tuple[list[str], System] | None:
    """'saves/mGBA/game.srm' -> (['GBA/game.sav'], gba). None if unmapped.

    Returns one MiSTer path per system directory (GAMEBOY/GBC/SGB siblings
    each get a copy). When several systems share a core folder (the
    multi-system Sega cores), the first matching system wins — content
    discrimination is out of scope for v1.
    """
    parts = path.split("/")
    if len(parts) != 3 or parts[0] != "saves":
        return None
    core, filename = parts[1], parts[2]
    for system in SYSTEMS:
        if core_for(system) == core and filename.endswith(f".{system.ra_ext}"):
            stem = filename[: -len(system.ra_ext) - 1]
            return [f"{d}/{stem}.{MISTER_EXT}" for d in system.mister_dirs], system
    return None
