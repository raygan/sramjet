from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_sync: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    sync_events: Mapped[list["SyncEvent"]] = relationship(back_populates="device")


class StoredFile(Base):
    """A single blob in the content-addressable store."""

    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String, nullable=False)
    hash: Mapped[str] = mapped_column(String, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    stored_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Version(Base):
    """One version of a canonical file path."""

    __tablename__ = "versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_path: Mapped[str] = mapped_column(String, nullable=False, index=True)
    hash: Mapped[str] = mapped_column(String, nullable=False)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    device: Mapped["Device"] = relationship()



class SyncEvent(Base):
    __tablename__ = "sync_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    files_uploaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    files_downloaded: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    had_conflicts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    device: Mapped["Device"] = relationship(back_populates="sync_events")
    event_files: Mapped[list["SyncEventFile"]] = relationship(back_populates="sync_event")
    snapshot: Mapped["ManifestSnapshot | None"] = relationship(back_populates="sync_event")


class SyncEventFile(Base):
    __tablename__ = "sync_event_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_event_id: Mapped[int] = mapped_column(ForeignKey("sync_events.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    # uploaded | downloaded | deleted | conflicted
    action: Mapped[str] = mapped_column(String, nullable=False)
    hash: Mapped[str] = mapped_column(String, nullable=False)

    sync_event: Mapped["SyncEvent"] = relationship(back_populates="event_files")


class ManifestSnapshot(Base):
    __tablename__ = "manifest_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_event_id: Mapped[int] = mapped_column(ForeignKey("sync_events.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False)

    sync_event: Mapped["SyncEvent"] = relationship(back_populates="snapshot")
