"""Per-device file quarantine.

A quarantined file type (saves, states) is synced only with that specific
device. The files are stored and delivered normally for that device, but
are never written to the main canonical store and are invisible to all
other devices.

Use case: devices running incompatible emulator cores/versions that can
sync save RAM fine but produce incompatible state files.

Quarantine settings and the device-specific manifest are stored as columns
on the Device model (quarantine_saves, quarantine_states,
quarantine_canonical_json), replacing the previous flat-file approach.
"""

import app.config
from app import manifest as mf
from app.models import Device
from app.store import compute_md5, store_blob


def get_quarantine(device: Device) -> dict[str, bool]:
    """Return quarantine settings for a device."""
    return {"saves": device.quarantine_saves, "states": device.quarantine_states}


def set_quarantine(device: Device, saves: bool, states: bool) -> None:
    """Update quarantine settings. Caller must commit the session."""
    device.quarantine_saves = saves
    device.quarantine_states = states


def is_quarantined(device: Device, file_path: str) -> bool:
    """Return True if this file's category is quarantined for this device."""
    category = file_path.split("/")[0] if "/" in file_path else ""
    if category == "saves":
        return device.quarantine_saves
    if category == "states":
        return device.quarantine_states
    return False


def _load_quarantine_canonical(device: Device) -> mf.Manifest:
    """Load the device-specific quarantine canonical manifest."""
    if not device.quarantine_canonical_json:
        return []
    return mf.parse(device.quarantine_canonical_json)


def _save_quarantine_canonical(device: Device, manifest: mf.Manifest) -> None:
    """Persist the device-specific quarantine canonical manifest. Caller must commit."""
    device.quarantine_canonical_json = mf.serialize(manifest).decode()


def build_hybrid_manifest(device: Device) -> mf.Manifest:
    """Build the manifest to serve to a quarantined device.

    Takes the main canonical, strips quarantined file types, then adds
    the device's own quarantine canonical for those types.
    """
    quarantined = set()
    if device.quarantine_saves:
        quarantined.add("saves")
    if device.quarantine_states:
        quarantined.add("states")

    main_dict = mf.to_dict(mf.load_canonical(app.config.CANONICAL_MANIFEST))
    device_dict = mf.to_dict(_load_quarantine_canonical(device))

    merged = {
        path: h for path, h in main_dict.items()
        if path.split("/")[0] not in quarantined
    }
    merged.update(device_dict)
    return mf.from_dict(merged)


async def handle_quarantined_upload(
    device: Device,
    file_path: str,
    data: bytes,
    sync_event,
) -> None:
    """Accept a file upload into the device-specific quarantine canonical.

    No conflict detection — the device is the sole owner of these files.
    Caller must commit the session after this returns.
    """
    incoming_hash = compute_md5(data)
    qc_dict = mf.to_dict(_load_quarantine_canonical(device))

    if qc_dict.get(file_path) != incoming_hash:
        await store_blob(data)
        qc_dict[file_path] = incoming_hash
        _save_quarantine_canonical(device, mf.from_dict(qc_dict))

    sync_event.files_uploaded += 1


def handle_quarantined_delete(device: Device, file_path: str, sync_event) -> None:
    """Mark a file as deleted in the device-specific quarantine canonical.

    Caller must commit the session.
    """
    qc_dict = mf.to_dict(_load_quarantine_canonical(device))
    qc_dict[file_path] = ""
    _save_quarantine_canonical(device, mf.from_dict(qc_dict))
    sync_event.files_uploaded += 1
