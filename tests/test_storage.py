"""File persistence boundaries: atomicity, corrupt files, and safe lookups."""

import json
from unittest.mock import patch
from uuid import uuid4

import pytest

from app import storage
from app.models.schemas import CallLog


def _call() -> CallLog:
    return CallLog(call_sid=f"browser_{uuid4().hex}", stream_sid=f"browser_stream_{uuid4().hex}",
                   config_snapshot={}, transcript=[])


@pytest.mark.asyncio
async def test_save_list_and_detail_use_same_call_shape():
    call = _call()
    await storage.persist_call_log(call)
    raw = json.loads(storage.conversation_path(call.call_sid).read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1
    assert raw["created_at"].endswith("Z")
    assert raw["call"]["call_sid"] == call.call_sid
    assert (await storage.list_call_logs())[0] == await storage.get_call_log(call.call_sid)


@pytest.mark.asyncio
async def test_failed_replace_preserves_previous_file_and_removes_temporary():
    call = _call()
    await storage.persist_call_log(call)
    before = storage.conversation_path(call.call_sid).read_bytes()
    call.error = "new error"
    with patch("app.storage.os.replace", side_effect=OSError("disk failed")):
        with pytest.raises(OSError):
            await storage.persist_call_log(call)
    assert storage.conversation_path(call.call_sid).read_bytes() == before
    assert list((storage.DATA_DIR / "conversations").glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_corrupt_history_is_skipped_but_detail_reports_it():
    call = _call()
    await storage.persist_call_log(call)
    storage.conversation_path(call.call_sid).write_text("broken", encoding="utf-8")
    assert await storage.list_call_logs() == []
    with pytest.raises(ValueError):
        await storage.get_call_log(call.call_sid)


@pytest.mark.asyncio
async def test_history_order_is_explicit_and_missing_is_none():
    older, newer = _call(), _call()
    await storage.persist_call_log(older)
    await storage.persist_call_log(newer)
    assert [r["call_sid"] for r in await storage.list_call_logs()] == [newer.call_sid, older.call_sid]
    assert await storage.get_call_log(f"browser_{uuid4().hex}") is None


@pytest.mark.parametrize("identifier", ["../secret", "browser_../../x", "browser_abc", "C:/data/config"])
def test_path_lookup_rejects_arbitrary_input(identifier):
    with pytest.raises(ValueError):
        storage.conversation_path(identifier)
