"""Persistent settings and OCR history for GlyphSnap."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import uuid

from PySide6.QtCore import QSettings, QStandardPaths


def app_data_dir() -> Path:
    """Return a writable per-user data directory in development and frozen builds."""
    location = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation
    )
    root = Path(location) if location else Path.home() / "AppData" / "Local" / "GlyphSnap"
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass(frozen=True)
class HistoryRecord:
    id: str
    created_at: str
    source_type: str
    source_name: str
    source_path: str
    languages: list[str]
    word_count: int
    processing_seconds: float
    text: str

    @classmethod
    def create(
        cls,
        *,
        source_type: str,
        source_name: str,
        source_path: str,
        languages: list[str],
        processing_seconds: float,
        text: str,
    ) -> "HistoryRecord":
        return cls(
            id=uuid.uuid4().hex,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            source_type=source_type,
            source_name=source_name,
            source_path=source_path,
            languages=list(languages),
            word_count=len(text.split()),
            processing_seconds=round(max(0.0, processing_seconds), 3),
            text=text,
        )

    @classmethod
    def from_dict(cls, value: dict) -> "HistoryRecord | None":
        try:
            return cls(
                id=str(value["id"]),
                created_at=str(value["created_at"]),
                source_type=str(value.get("source_type", "image")),
                source_name=str(value.get("source_name", "Untitled capture")),
                source_path=str(value.get("source_path", "")),
                languages=[str(item) for item in value.get("languages", [])],
                word_count=int(value.get("word_count", 0)),
                processing_seconds=float(value.get("processing_seconds", 0.0)),
                text=str(value.get("text", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None


class HistoryStore:
    """Small, resilient JSON history store with atomic replacement."""

    def __init__(self, path: Path | None = None, limit: int = 100) -> None:
        self.path = path or app_data_dir() / "history.json"
        self.limit = limit

    def load(self) -> list[HistoryRecord]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        records = [HistoryRecord.from_dict(item) for item in payload if isinstance(item, dict)]
        return [record for record in records if record is not None][: self.limit]

    def add(self, record: HistoryRecord) -> None:
        records = [record]
        records.extend(item for item in self.load() if item.id != record.id)
        self._write(records[: self.limit])

    def delete(self, record_id: str) -> None:
        self._write([item for item in self.load() if item.id != record_id])

    def clear(self) -> None:
        self._write([])

    def _write(self, records: list[HistoryRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix="history-", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(
                    [asdict(record) for record in records],
                    stream,
                    ensure_ascii=False,
                    indent=2,
                )
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)


class GlyphSnapSettings:
    """Typed access to user preferences stored by Qt."""

    def __init__(self) -> None:
        self.values = QSettings("GlyphSnap", "GlyphSnap")

    def languages(self) -> list[str]:
        value = self.values.value("ocr/default_languages", ["ara", "eng"])
        if isinstance(value, str):
            return [item for item in value.split(",") if item]
        return [str(item) for item in value]

    def set_languages(self, languages: list[str]) -> None:
        self.values.setValue("ocr/default_languages", list(languages))

    def pdf_dpi(self) -> int:
        return self.values.value("pdf/dpi", 300, int)

    def set_pdf_dpi(self, value: int) -> None:
        self.values.setValue("pdf/dpi", value)

    def preprocess(self) -> bool:
        return self.values.value("ocr/preprocess", True, bool)

    def set_preprocess(self, enabled: bool) -> None:
        self.values.setValue("ocr/preprocess", enabled)

    def reset(self) -> None:
        for key in ("ocr/default_languages", "ocr/preprocess", "pdf/dpi"):
            self.values.remove(key)
        self.values.sync()

    def sync(self) -> None:
        self.values.sync()
