"""Origin-restricted workspace APIs; secrets and access approvals remain local."""

from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.models.runtime_config_models import RuntimeConfig
from app.routers.dashboard import call_context, calls_context
from app.services.runtime_config import load_runtime_config, save_runtime_config
from app.storage import CALL_SID_PATTERN, get_call_log

router = APIRouter(prefix="/api/workspace")


@router.get("/config")
async def get_config() -> RuntimeConfig:
    """Return behavior settings; provider credentials are not part of this model."""
    return await load_runtime_config()


@router.put("/config")
async def put_config(value: RuntimeConfig) -> RuntimeConfig:
    """Save validated behavior while keeping access approvals local."""
    base = await load_runtime_config()
    if (value.ops.candidate_frontend_origin != base.ops.candidate_frontend_origin
            or value.ops.workspace_frontend_origin != base.ops.workspace_frontend_origin):
        raise HTTPException(403, "Change hosted origin approvals only on local Configuration")
    try:
        return await save_runtime_config(value, preserve_access_origins=True)
    except OSError as exc:
        logger.error("Hosted configuration save failed")
        raise HTTPException(503, "Settings could not be written; previous settings remain active") from exc


@router.get("/calls")
async def calls() -> dict[str, Any]:
    """Return existing history summaries from local storage."""
    return await calls_context()


@router.get("/calls/{call_sid}")
async def call(call_sid: str) -> dict[str, Any]:
    """Return a validated local conversation and its existing timing summaries."""
    if not CALL_SID_PATTERN.fullmatch(call_sid):
        raise HTTPException(404, "Conversation not found")
    try:
        log = await get_call_log(call_sid)
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Conversation could not be read") from exc
    if log is None:
        raise HTTPException(404, "Conversation not found")
    return call_context(log)
