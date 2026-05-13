"""Per-device file quarantine.

A quarantined file type (saves, states) is synced only with that specific
device. The files are stored and delivered normally for that device, but
are never written to the main canonical store and are invisible to all
other devices.

Use case: devices running incompatible emulator cores/versions that can
sync save RAM fine but produce incompatible state files.

Storage layout:
  devices/{name}/quarantine.json         — quarantine config  {"saves": bool, "states": bool}
  devices/{name}/quarantine_canonical.json — device-specific canonical manifest
"""

import json

import app.config
from app import manifest as mf
from app.store import compute_md5, store_blob


def get_quarantine(device_name: str) -> dict[str, bool]:
    """Return quarantine settings for a device. Default: nothing quarantined."""
    path = app.config.DEVICES_DIR / device_name / "quarantine.json"
    if not path.exists():
        return {"saves": False, "states": False}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return {"saves": False, "states": False}


def set_quarantine(device_name: str, saves: bool, states: bool) -> None:
    device_dir = app.config.DEVICES_DIR / device_name
    device_dir.mkdir(parents=True, exist_ok=True)
    (device_dir / "quarantine.json").write_text(
        json.dumps({"saves": saves, "states": states})
    )


def is_quarantined(device_name: str, file_path: str) -> bool:
    """Return True if this file's category is quarantined for this device."""
    category = file_path.split("/")[0] if "/" in file_path else ""
    return get_quarantine(device_name).get(category, False)


def _quarantine_canonical_path(device_name: str):
    return app.config.DEVICES_DIR / device_name / "quarantine_canonical.json"


def build_hybrid_manifest(device_name: str) -> mf.Manifest:
    """Build the manifest to serve to a quarantined device.

    Takes the main canonical, strips quarantined file types, then adds
    the device's own quarantine canonical for those types.
    """
    q = get_quarantine(device_name)
    quarantined = {cat for cat, is_q in q.items() if is_q}

    main_dict = mf.to_dict(mf.load_canonical(app.config.CANONICAL_MANIFEST))
    device_dict = mf.to_dict(mf.load_canonical(_quarantine_canonical_path(device_name)))

    merged = {
        path: h for path, h in main_dict.items()
        if path.split("/")[0] not in quarantined
    }
    merged.update(device_dict)
    return mf.from_dict(merged)


async def handle_quarantined_upload(
    device_name: str,
    file_path: str,
    data: bytes,
    sync_event,
) -> None:
    """Accept a file upload into the device-specific quarantine canonical.

    No conflict detection — the device is the sole owner of these files.
    """
    incoming_hash = compute_md5(data)
    qc_path = _quarantine_canonical_path(device_name)
    qc_dict = mf.to_dict(mf.load_canonical(qc_path))

    if qc_dict.get(file_path) != incoming_hash:
        await store_blob(data)
        qc_dict[file_path] = incoming_hash
        mf.save_canonical(qc_path, mf.from_dict(qc_dict))

    sync_event.files_uploaded += 1


def handle_quarantined_delete(device_name: str, file_path: str, sync_event) -> None:
    """Mark a file as deleted in the device-specific quarantine canonical."""
    qc_path = _quarantine_canonical_path(device_name)
    qc_dict = mf.to_dict(mf.load_canonical(qc_path))
    qc_dict[file_path] = ""
    mf.save_canonical(qc_path, mf.from_dict(qc_dict))
    sync_event.files_uploaded += 1
