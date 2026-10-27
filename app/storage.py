"""Local JSON storage for validated settings and finalized conversations."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import tempfile
from datetime import UTC, datetime
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, ValidationError

from app.models.runtime_config_models import RuntimeConfig
from app.models.schemas import CallLog

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CALL_SID_PATTERN = re.compile(r"browser_[0-9a-f]{32}\Z")


class StoredConversation(BaseModel):
    """Versioned file envelope; timestamps are explicit instead of database IDs."""

    schema_version: Literal[1] = 1
    created_at: datetime
    call: CallLog


def conversation_path(call_sid: str) -> Path:
    """Resolve only an application's generated conversation identifier."""
    if not CALL_SID_PATTERN.fullmatch(call_sid):
        raise ValueError("Invalid conversation identifier")
    return DATA_DIR / "conversations" / f"{call_sid}.json"


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


async def initialize_storage() -> None:
    """Create and check storage without contacting any external service."""
    def initialize() -> None:
        (DATA_DIR / "conversations").mkdir(parents=True, exist_ok=True)
        for directory in (DATA_DIR, DATA_DIR / "conversations"):
            with tempfile.TemporaryFile(dir=directory):
                pass
    await asyncio.to_thread(initialize)


async def read_config() -> RuntimeConfig | None:
    """Read existing settings; only a missing file means first-run defaults."""
    def read() -> RuntimeConfig | None:
        try:
            return RuntimeConfig.model_validate_json(
                (DATA_DIR / "runtime_config.json").read_text(encoding="utf-8"),
                context={"migrate_saved_config": True},
            )
        except FileNotFoundError:
            return None
    return await asyncio.to_thread(read)


async def write_config(config: RuntimeConfig) -> None:
    """Atomically replace the settings document."""
    await asyncio.to_thread(_atomic_write, DATA_DIR / "runtime_config.json", config.model_dump(mode="json"))


async def persist_call_log(call: CallLog) -> str:
    """Atomically write one finalized conversation using its stable identifier."""
    envelope = StoredConversation(created_at=datetime.now(UTC), call=call)
    await asyncio.to_thread(_atomic_write, conversation_path(call.call_sid), envelope.model_dump(mode="json"))
    return call.call_sid


def _read_conversation(path: Path) -> StoredConversation:
    result = StoredConversation.model_validate_json(path.read_text(encoding="utf-8"))
    if path.stem != result.call.call_sid:
        raise ValueError("Conversation filename does not match its identifier")
    if result.created_at.utcoffset() is None:
        raise ValueError("Conversation timestamp requires a timezone")
    return result


def _as_dashboard_log(stored: StoredConversation) -> dict[str, Any]:
    log = stored.call.model_dump(mode="json")
    log["created_at"] = stored.created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return log


async def list_call_logs(limit: int = 50) -> list[dict[str, Any]]:
    """Return the latest valid records, skipping damaged files with a warning."""
    def read() -> list[dict[str, Any]]:
        records: list[StoredConversation] = []
        for path in (DATA_DIR / "conversations").glob("browser_*.json"):
            try:
                records.append(_read_conversation(path))
            except (OSError, ValueError, ValidationError):
                logger.warning("Skipping unreadable conversation file", filename=path.name)
        records.sort(key=lambda r: (r.created_at, r.call.call_sid), reverse=True)
        return [_as_dashboard_log(item) for item in records[:limit]]
    return await asyncio.to_thread(read)


async def get_call_log(call_sid: str) -> dict[str, Any] | None:
    """Read a specific record; missing files are distinct from corrupt files."""
    path = conversation_path(call_sid)
    def read() -> dict[str, Any] | None:
        try:
            return _as_dashboard_log(_read_conversation(path))
        except FileNotFoundError:
            return None
    return await asyncio.to_thread(read)
