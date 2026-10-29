"""Local recruiter management and strictly scoped candidate participation."""

from pathlib import Path
import time
from typing import Any
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import Field
from app.interviews.controller import InterviewController
from app.interviews.models import CameraEvent, Job, MonitoringUpdate, Plan, Record, Session
from app.interviews.service import InterviewBinding, InterviewService
from app.interviews.vision import extend_unknown
from app.models.browser_models import CandidateRequest, OfferRequest

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def service(request: Request) -> InterviewService:
    """Resolve the application's interview service."""
    return request.app.state.interviews


async def candidate(session_id: str, request: Request, authorization: str = Header(default="")) -> Session:
    """Tokens authorize participation in exactly one session, never reports."""
    token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    return await service(request).authorize(session_id, token)


class Preparation(Record):
    """Candidate declaration; device capabilities are checked in the browser."""
    phase: str = Field(pattern=r"^(device_check|ready)$")
    camera_consent: bool


class HumanReview(Record):
    """Human-only notes and decision, independent of generated technical evidence."""
    recruiter_notes: str = Field(default="", max_length=10000)
    human_decision: str = Field(default="", max_length=1000)


@router.post("/api/candidate/{session_id}/revoke-camera-consent")
async def revoke_camera_consent(request: Request, state: Session = Depends(candidate)) -> dict[str, bool]:
    """Allow immediate withdrawal during participation without stopping voice."""
    svc = service(request)
    async with svc.lock:
        state = await svc.session(state.id)
        state.camera_consent = False
        state.monitoring.status = "unavailable"
        state.monitoring.received_at = time.time()
        definition = await svc.definition(state.definition_id)
        elapsed = InterviewController(definition, state).elapsed(time.time())
        for run in state.monitoring.runs:
            if run.call_sid == state.current_call_sid and run.end_secs is None:
                extend_unknown(run, elapsed, "consent_withdrawn", close=True)
        await svc.save(state)
    return {"saved": True}


@router.get("/interviews", response_class=HTMLResponse)
async def recruiter_page(request: Request) -> HTMLResponse:
    """Render the local setup/review/report interface."""
    return templates.TemplateResponse(request=request, name="interviews.html")


@router.get("/api/interviews")
async def list_interviews(request: Request) -> dict[str, Any]:
    """List local definitions and safe session summaries."""
    svc = service(request)
    return {"definitions": [d.model_dump(mode="json") for d in await svc.store.definitions()],
        "sessions": [{"id": s.id, "definition_id": s.definition_id, "status": s.status} for s in await svc.store.sessions()]}


@router.post("/api/interviews")
async def create_interview(job: Job, request: Request) -> dict[str, Any]:
    """Draft questions from explicit recruiter inputs without an API call."""
    return (await service(request).create(job)).model_dump(mode="json")


@router.put("/api/interviews/{definition_id}/plan")
async def review_plan(definition_id: str, plan: Plan, request: Request) -> dict[str, Any]:
    """Validate and freeze the recruiter-reviewed plan before activation."""
    return (await service(request).review(definition_id, plan)).model_dump(mode="json")


@router.post("/api/interviews/{definition_id}/activate")
async def activate(definition_id: str, request: Request) -> dict[str, Any]:
    """Return the bearer link once; fragments do not reach access logs or referrers."""
    state, token = await service(request).activate(definition_id)
    return {"session_id": state.id, "candidate_url": f"/candidate/{state.id}#token={token}"}


@router.get("/api/interviews/sessions/{session_id}/report")
async def report(session_id: str, request: Request) -> dict[str, Any]:
    """Serve evidence and camera timelines exclusively to local management."""
    return (await service(request).report(session_id)).model_dump(mode="json")


@router.put("/api/interviews/sessions/{session_id}/review")
async def human_review(session_id: str, value: HumanReview, request: Request) -> dict[str, Any]:
    """Persist recruiter notes and an explicitly human decision."""
    svc = service(request)
    async with svc.lock:
        state = await svc.session(session_id)
        state.recruiter_notes, state.human_decision = value.recruiter_notes, value.human_decision
        await svc.save(state)
    return {"saved": True}


@router.get("/candidate/{session_id}", response_class=HTMLResponse)
async def candidate_page(session_id: str, request: Request) -> HTMLResponse:
    """Serve a data-free shell; all interview data requires the bearer token."""
    if len(session_id) != 32 or any(c not in "0123456789abcdef" for c in session_id):
        raise HTTPException(404, "Unknown candidate page")
    return templates.TemplateResponse(request=request, name="candidate.html", context={"candidate_mode": True, "session_id": session_id})


@router.get("/api/candidate/{session_id}")
async def candidate_info(request: Request, state: Session = Depends(candidate)) -> dict[str, Any]:
    """Return only participation data, omitting resume, rubrics, answers, and reports."""
    definition = await service(request).definition(state.definition_id)
    controller = InterviewController(definition, state)
    return {"id": state.id, "title": definition.job.title, "language": definition.job.language,
        "duration_minutes": definition.job.duration_minutes, "status": state.status,
        "elapsed_secs": controller.elapsed(time.time()), "remaining_secs": controller.remaining(time.time()),
        "camera": definition.job.camera.model_dump(), "camera_sequence": max((e.sequence for e in state.camera_events), default=0),
        "heartbeat_sequence": state.monitoring.sequence, "call_sid": state.current_call_sid or state.last_call_sid,
        "monitoring_run": next((run.model_dump(mode="json") for run in reversed(state.monitoring.runs)
            if run.call_sid == state.current_call_sid and run.end_secs is None), None)}


@router.post("/api/candidate/{session_id}/prepare")
async def prepare(value: Preparation, request: Request, state: Session = Depends(candidate)) -> dict[str, Any]:
    """Record consent and preparation without changing a running interview."""
    svc = service(request)
    async with svc.lock:
        state = await svc.session(state.id)
        if state.status not in {"created", "device_check", "ready", "paused"}:
            raise HTTPException(409, "Interview is running or terminal")
        if state.status != "paused":
            state.status = value.phase
        state.camera_consent = value.camera_consent
        await svc.save(state)
    return {"status": state.status}


@router.post("/api/candidate/{session_id}/offer")
async def offer(value: OfferRequest, request: Request, state: Session = Depends(candidate)) -> dict[str, str]:
    """Use the sole existing audio manager, with a scoped controller binding."""
    if not value.pc_id and state.status not in {"ready", "paused"}:
        raise HTTPException(409, "Complete device preparation before starting")
    return await request.app.state.browser_sessions.offer(value, interview=InterviewBinding(service(request), state.id))


@router.patch("/api/candidate/{session_id}/offer")
async def ice(value: CandidateRequest, request: Request, state: Session = Depends(candidate)) -> dict[str, bool]:
    """Prevent tokens from signaling a different interview or the general demo."""
    manager = request.app.state.browser_sessions
    if not manager.owns_interview(state.id, value.pc_id):
        raise HTTPException(403, "Peer connection is outside this participation scope")
    await manager.candidates(value)
    return {"ok": True}


@router.post("/api/candidate/{session_id}/end")
async def end(request: Request, state: Session = Depends(candidate)) -> dict[str, Any]:
    """Pause media and permit intentional resume; do not expose conversation history."""
    call_sid = state.current_call_sid or state.last_call_sid
    if not call_sid:
        return {"saved": True, "status": state.status}
    try:
        result = await request.app.state.browser_sessions.end(call_sid)
    except HTTPException as exc:
        if exc.status_code == 404 and state.current_call_sid is None:
            return {"saved": True, "status": state.status}
        raise
    return {"saved": result.saved, "error": result.error,
        "status": (await service(request).session(state.id)).status}


@router.post("/api/candidate/{session_id}/abandon")
async def abandon(request: Request, state: Session = Depends(candidate)) -> dict[str, Any]:
    """Explicitly end participation after media cleanup; a terminal state cannot resume."""
    await end(request, state)
    svc = service(request)
    async with svc.lock:
        current = await svc.session(state.id)
        if current.current_call_sid:
            raise HTTPException(409, "Media is still ending; retry shortly")
        if current.status != "completed":
            current.status, current.finalized = "abandoned", True
        await svc.save(current)
    return {"saved": True, "status": current.status}


@router.post("/api/candidate/{session_id}/camera")
async def camera(value: CameraEvent, request: Request, state: Session = Depends(candidate)) -> dict[str, bool]:
    """Accept neutral observations, requiring recorded consent for face analysis."""
    if value.type.startswith("face_") or value.type == "multiple_faces_detected":
        if not state.camera_consent:
            raise HTTPException(403, "Face monitoring requires consent")
    await service(request).camera(state.id, value)
    return {"saved": True}


@router.post("/api/candidate/{session_id}/heartbeat")
async def heartbeat(value: MonitoringUpdate, request: Request, state: Session = Depends(candidate)) -> dict[str, bool]:
    """Persist monitoring availability separately from observation events."""
    if value.status == "active" and not state.camera_consent:
        raise HTTPException(403, "Monitoring requires consent")
    await service(request).camera(state.id, value)
    return {"saved": True}
