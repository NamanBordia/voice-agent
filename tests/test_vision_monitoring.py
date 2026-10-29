"""Metadata ownership, cumulative updates, bounds and legacy record compatibility."""

from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from app.config import get_settings
from app.interviews.models import CameraConfig, CameraEvent, DetectorStats, Monitoring, MonitoringUpdate, Session, VisionRun
from app.interviews.service import InterviewBinding
from app.interviews.vision import extend_unknown
from app.runtime import build_config_snapshot
from main import create_app
from test_interview_controller import job
from test_interview_service import activated


async def setup_vision(tmp_path, phone=True, consent=True):
    svc, definition, state, token = await activated(tmp_path)
    definition.job.camera.phone_enabled = phone
    await svc.store.write("definitions", definition.id, definition)
    state.camera_consent = consent; await svc.save(state)
    binding = InterviewBinding(svc, state.id)
    await binding.start("call1", build_config_snapshot(get_settings()))
    return svc, binding, state, token


def snapshot(run, until=2, frames=4):
    data = run.model_dump()
    start = run.start_secs; data["observed_until_secs"] = start + until
    for name in ("face", "phone"):
        if data[name]["enabled"]:
            data[name].update(status="active", frames_analyzed=frames, first_analyzed_secs=start + .25,
                last_analyzed_secs=start + 1, covered_secs=.75, unknown_secs=until - .75)
    return VisionRun.model_validate(data)


@pytest.mark.asyncio
async def test_snapshots_are_cumulative_and_resume_uses_distinct_runs(tmp_path):
    with patch("app.interviews.service.time.time", return_value=100) as clock:
        svc, binding, state, _ = await setup_vision(tmp_path)
        original = (await svc.session(state.id)).monitoring.runs[0]
        update = MonitoringUpdate(status="active", sequence=1, run=snapshot(original))
        clock.return_value = 102
        await svc.camera(state.id, update.model_copy(deep=True))
        await svc.camera(state.id, update.model_copy(deep=True))
        update.sequence = 2; await svc.camera(state.id, update)
        saved = await svc.session(state.id)
        assert len(saved.monitoring.runs) == 1 and saved.monitoring.runs[0].phone.frames_analyzed == 4
        report = await svc.report(state.id)
        assert report.monitoring.runs[0].phone.status == "active"
        clock.return_value = 103; await binding.stop()
        saved = await svc.session(state.id)
        assert saved.monitoring.runs[0].end_secs == 3
        assert saved.monitoring.runs[0].phone.covered_secs == .75
        assert saved.monitoring.runs[0].phone.unknown_secs == 2.25
        clock.return_value = 1000
        resumed = InterviewBinding(svc, state.id)
        await resumed.start("call2", build_config_snapshot(get_settings()))
        saved = await svc.session(state.id)
        assert len(saved.monitoring.runs) == 2 and saved.monitoring.runs[1].start_secs == 3
        assert saved.monitoring.runs[1].phone.frames_analyzed == 0
        clock.return_value = 1001; await resumed.stop()
        assert (await svc.session(state.id)).monitoring.runs[1].phone.unknown_secs == 1


@pytest.mark.parametrize("problem", ["ownership", "reset", "first_timestamp", "coverage_without_frames", "disabled_phone", "future", "cadence"])
@pytest.mark.asyncio
async def test_invalid_snapshots_cannot_replace_confirmed_run(tmp_path, problem):
    with patch("app.interviews.service.time.time", return_value=100) as clock:
        svc, binding, state, _ = await setup_vision(tmp_path)
        original = (await svc.session(state.id)).monitoring.runs[0]
        clock.return_value = 102
        await svc.camera(state.id, MonitoringUpdate(status="active", sequence=1, run=snapshot(original)))
        before = (await svc.session(state.id)).monitoring.model_dump()
        data = snapshot(original).model_dump()
        if problem == "ownership": data["call_sid"] = "someone_else"
        if problem == "reset": data["phone"]["frames_analyzed"] = 3
        if problem == "first_timestamp": data["phone"].update(first_analyzed_secs=.2, frames_analyzed=5)
        if problem == "coverage_without_frames": data["phone"].update(last_analyzed_secs=1.25, covered_secs=1, unknown_secs=1)
        if problem == "disabled_phone": data["phone"] = DetectorStats().model_dump()
        if problem == "future":
            data["observed_until_secs"] = 30
            for name in ("face", "phone"): data[name]["unknown_secs"] = 29.25
        if problem == "cadence": data["phone"]["frames_analyzed"] = 1000
        with pytest.raises(HTTPException):
            await svc.camera(state.id, MonitoringUpdate(status="active", sequence=2, run=VisionRun.model_validate(data)))
        assert (await svc.session(state.id)).monitoring.model_dump() == before
        await binding.stop()


@pytest.mark.parametrize("changes", [
    {"face": {"enabled": True, "status": "active"}},
    {"face": {"enabled": True, "unknown_secs": float('nan')}},
    {"face": {"enabled": True, "unknown_secs": 1, "covered_secs": 10}},
    {"face": {"enabled": False, "frames_analyzed": 1, "first_analyzed_secs": 0, "last_analyzed_secs": 0}},
    {"face": {"enabled": True, "gaps": [{"start_secs": 1, "end_secs": 0, "reason": "unknown"}]}},
    {"face": {"enabled": True, "image": "not allowed"}},
])
def test_schema_rejects_impossible_counters_images_and_times(changes):
    with pytest.raises(ValidationError):
        VisionRun.model_validate({"call_sid": "call", "start_secs": 0, "observed_until_secs": 1, **changes})


@pytest.mark.asyncio
async def test_phone_events_require_explicit_enablement_consent_and_sustained_score(tmp_path):
    svc, binding, state, _ = await setup_vision(tmp_path, phone=False)
    event = CameraEvent(interview_id=state.id, event_id="phone1", sequence=1, type="phone_detected",
        start_secs=0, duration_secs=2, confidence=.9)
    with pytest.raises(HTTPException) as denied: await svc.camera(state.id, event)
    assert denied.value.status_code == 403
    definition = await svc.definition(state.definition_id); definition.job.camera.phone_enabled = True
    await svc.store.write("definitions", definition.id, definition)
    for updates in ({"duration_secs": .5}, {"confidence": .2}, {"face_count": 1}):
        with pytest.raises(HTTPException) as invalid:
            await svc.camera(state.id, event.model_copy(update=updates))
        assert invalid.value.status_code == 422
    await svc.camera(state.id, event.model_copy(deep=True)); await svc.camera(state.id, event.model_copy(deep=True))
    saved = await svc.session(state.id)
    assert len(saved.camera_events) == 1 and not saved.answers and not saved.skills_covered
    saved.camera_consent = False; await svc.save(saved)
    with pytest.raises(HTTPException): await svc.camera(state.id, event.model_copy(update={"event_id": "phone2", "sequence": 2}))
    await binding.stop()


def test_legacy_records_load_unknown_metrics_without_inventing_zero_coverage():
    session = Session.model_validate({"id": "a" * 32, "definition_id": "b" * 32, "token_hash": "test",
        "monitoring": {"status": "stopped", "sequence": 31}})
    assert session.monitoring.runs == []
    assert not CameraConfig.model_validate({"fps": 4}).phone_enabled


def test_stale_run_tail_is_unknown_and_history_is_bounded():
    run = snapshot(VisionRun(call_sid="call", start_secs=0, observed_until_secs=0,
        face=DetectorStats(enabled=True), phone=DetectorStats(enabled=True)))
    for until in range(3, 80): extend_unknown(run, until, "updates_missing")
    assert run.face.covered_secs == .75 and run.face.unknown_secs == 78.25
    assert len(run.face.gaps) == 64 and run.face.history_truncated
    assert run.face.status == "unavailable"
    VisionRun.model_validate(run.model_dump())


def test_candidate_heartbeat_route_enforces_run_scope_and_rejects_images():
    app = create_app()
    with patch("main.run_local_warmup", new=AsyncMock()), TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        data = job().model_dump(); data["camera"]["phone_enabled"] = True
        definition = client.post("/api/interviews", json=data).json()
        client.put(f"/api/interviews/{definition['id']}/plan", json=definition["plan"])
        activation = client.post(f"/api/interviews/{definition['id']}/activate").json()
        identity = activation["session_id"]; headers = {"Authorization": f"Bearer {activation['candidate_url'].split('#token=')[1]}"}
        client.post(f"/api/candidate/{identity}/prepare", headers=headers, json={"phase": "ready", "camera_consent": True})
        binding = InterviewBinding(app.state.interviews, identity)
        client.portal.call(binding.start, "browser_test", build_config_snapshot(get_settings()))
        run = client.get(f"/api/candidate/{identity}", headers=headers).json()["monitoring_run"]
        body = {"status": "not_started", "sequence": 1, "run": run}
        endpoint = f"/api/candidate/{identity}/heartbeat"
        assert client.post(endpoint, json=body).status_code == 403
        assert client.post(endpoint, headers=headers, json=body).status_code == 200
        assert client.post(f"/api/candidate/{identity}/revoke-camera-consent", headers=headers).status_code == 200
        saved = client.portal.call(app.state.interviews.session, identity)
        assert not saved.camera_consent and saved.monitoring.runs[0].end_secs is not None
        run["call_sid"] = "other_call"; body["sequence"] = 2
        assert client.post(endpoint, headers=headers, json=body).status_code == 403
        run["image"] = "forbidden"
        assert client.post(endpoint, headers=headers, json=body).status_code == 422
        client.portal.call(binding.stop)
