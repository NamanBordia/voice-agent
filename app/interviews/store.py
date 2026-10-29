"""Atomic interview storage isolated from existing conversation envelopes."""

import asyncio
from pathlib import Path
import re
from typing import TypeVar
from pydantic import BaseModel
from app import storage
from app.interviews.models import Definition, Session

T = TypeVar("T", bound=BaseModel)


class InterviewStore:
    """Read/write validated records with path-safe generated identities."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or storage.DATA_DIR / "interviews"

    def path(self, kind: str, identity: str) -> Path:
        """Reject identifiers that could escape the interview directory."""
        if kind not in {"definitions", "sessions", "reports"} or not re.fullmatch(r"[0-9a-f]{32}", identity):
            raise ValueError("Invalid interview record identifier")
        return self.root / kind / f"{identity}.json"

    async def read(self, kind: str, identity: str, model: type[T]) -> T:
        """Load one typed record; corrupt data is never silently replaced."""
        path = self.path(kind, identity)
        value = await asyncio.to_thread(path.read_text, encoding="utf-8")
        result = model.model_validate_json(value)
        if getattr(result, "id", None) != identity:
            raise ValueError("Record identity mismatch")
        return result

    async def write(self, kind: str, identity: str, value: BaseModel) -> None:
        """Keep the atomic replacement alive if the HTTP caller disconnects."""
        task = asyncio.create_task(asyncio.to_thread(storage._atomic_write,
            self.path(kind, identity), value.model_dump(mode="json")))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def definitions(self) -> list[Definition]:
        """List local definitions without skipping malformed records."""
        paths = await asyncio.to_thread(lambda: list((self.root / "definitions").glob("*.json")))
        return [await self.read("definitions", path.stem, Definition) for path in paths]

    async def sessions(self) -> list[Session]:
        """Read checkpoints for restart recovery and recruiter listing."""
        paths = await asyncio.to_thread(lambda: list((self.root / "sessions").glob("*.json")))
        return [await self.read("sessions", path.stem, Session) for path in paths]
