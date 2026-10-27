"""Browser demo page and its SmallWebRTC signaling boundary."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.models.browser_models import CandidateRequest, EndResult, OfferRequest
from app.services.browser_session import BrowserSessionManager

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _manager(request: Request) -> BrowserSessionManager:
    return request.app.state.browser_sessions


@router.get("/", response_class=HTMLResponse)
async def demo(request: Request) -> HTMLResponse:
    """Render the local microphone conversation page."""
    return templates.TemplateResponse(request=request, name="demo.html", context={})


@router.post("/api/offer")
async def offer(request: Request, payload: OfferRequest) -> dict[str, str]:
    """Initialize or renegotiate the one browser peer."""
    return await _manager(request).offer(payload)


@router.patch("/api/offer")
async def candidates(request: Request, payload: CandidateRequest) -> dict[str, bool]:
    """Apply additional candidates supplied by the browser SDK."""
    await _manager(request).candidates(payload)
    return {"ok": True}


@router.post("/api/sessions/{call_sid}/end", response_model=EndResult)
async def end(request: Request, call_sid: str) -> EndResult:
    """End speech and wait for the final save before returning its link."""
    return await _manager(request).end(call_sid)
