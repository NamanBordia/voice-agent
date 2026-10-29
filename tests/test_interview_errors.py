"""Failure diagnostics preserve the saved turn and never log rejected JSON."""

import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from loguru import logger
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import ValidationError
import pytest

from app.config import get_settings
from app.interviews.errors import DECISION_FALLBACK_ISSUE, InterviewGenerationError, describe_failure
from app.interviews.models import Decision
from app.interviews.service import InterviewBinding
from app.interviews.service import decision_schema
from app.runtime import build_config_snapshot
from test_interview_service import activated
from test_interview_controller import job
from app.interviews.planning import draft


def test_wire_schema_anchors_question_and_rubric_to_reviewed_plan():
    question = draft(job()).plan.sections[0].questions[0]
    schema = decision_schema(question)
    assert schema["properties"]["question_id"]["enum"] == [question.id]
    assert schema["$defs"]["Evidence"]["properties"]["criterion"]["enum"] == question.criteria


@pytest.mark.parametrize("exception, code, fragment", [
    (TimeoutError(), "decision_timeout", "20s"),
    (ValueError("Decision question ID does not match controller"), "decision_rejected", "question ID"),
    (ValueError("private rejected JSON must not be displayed"), "decision_rejected", "Unrecognized"),
    (OSError("private path must not be displayed"), "checkpoint_io", "storage"),
    (RuntimeError("private output"), "decision_internal", "RuntimeError"),
])
def test_safe_error_classification(exception, code, fragment):
    failure = describe_failure(exception, get_settings(), 20)
    assert failure.code == code and fragment in failure.summary
    assert "private" not in failure.summary + failure.provider_detail


def test_validation_errors_do_not_include_model_output_or_assessment_notes():
    with pytest.raises(ValidationError) as captured:
        Decision.model_validate({"action": "private assessment", "question_id": "q_1", "turn_kind": "answer"})
    failure = describe_failure(captured.value, get_settings(), 20)
    assert failure.code == "decision_schema"
    assert "action: literal_error" in failure.summary
    assert "private assessment" not in failure.summary + failure.provider_detail


def test_provider_http_error_redacts_keys_and_keeps_provider_detail_local():
    settings = get_settings()
    request = httpx.Request("POST", "https://api.cerebras.ai/v1/chat/completions")
    exception = APIStatusError("raw SDK message must not be used",
        response=httpx.Response(400, request=request), body={"error": {
            "code": "invalid_schema", "param": "response_format",
            "message": f"Unsupported schema keyword. {settings.cerebras_api_key} {settings.sarvam_api_key}"}})
    failure = describe_failure(exception, settings, 20)
    assert "HTTP 400" in failure.summary and "response_format" in failure.summary
    assert "Unsupported schema" not in failure.summary
    assert "Unsupported schema" in failure.provider_detail
    assert settings.cerebras_api_key not in failure.provider_detail
    assert settings.sarvam_api_key not in failure.provider_detail
    assert "[REDACTED]" in failure.provider_detail


def test_sdk_network_and_timeout_errors_are_distinguished():
    request = httpx.Request("POST", "https://api.cerebras.ai/v1/chat/completions")
    assert describe_failure(APIConnectionError(request=request), get_settings(), 20).code == "provider_connection"
    assert describe_failure(APITimeoutError(request=request), get_settings(), 20).code == "decision_timeout"


@pytest.mark.parametrize("reason, content, expected", [
    ("length", '{"action": "clarify"', "decision_truncated"),
    ("stop", "", "decision_empty"),
    ("stop", '{"action": "clarify"', "decision_schema"),
    ("stop", '{"action": "reject", "question_id": "q_1", "turn_kind": "off_topic"}', "decision_schema"),
])
@pytest.mark.asyncio
async def test_incomplete_decision_repeats_prompt_without_assessment_or_extra_request(tmp_path, reason, content, expected):
    service, definition, state, _ = await activated(tmp_path)
    binding = InterviewBinding(service, state.id)
    await binding.start("call1", build_config_snapshot(get_settings()))
    await binding.delivered(); await binding.accept("call1:1", "Saved candidate answer")
    client = AsyncMock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=reason, message=SimpleNamespace(content=content, refusal=None))])
    llm = SimpleNamespace(_client=client, request_timeout_seconds=20)
    with patch("app.interviews.service.VoiceCerebrasLLMService", return_value=llm):
        spoken = await binding.respond()
    assert spoken == f"<en>{definition.plan.sections[0].questions[0].text}"
    checkpoint = await service.session(state.id)
    assert checkpoint.pending_event_id is None and len(checkpoint.answers) == 1
    assert checkpoint.status == "in_progress" and checkpoint.section_index == 0
    assert checkpoint.answers[0].text == "Saved candidate answer" and not checkpoint.answers[0].evidence
    assert expected in checkpoint.technical_issues[-1]
    assert not checkpoint.skills_covered
    report = await service.report(state.id)
    assert report.skills[0].status == "not_assessed"
    assert not report.skills[0].answer_event_ids
    client.chat.completions.create.assert_awaited_once()
    client.close.assert_awaited_once()
    prompt = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "Wrong, unrelated, nonsensical" in prompt
    await binding.stop()


@pytest.mark.parametrize("kind", ["empty_choices", "refused"])
@pytest.mark.asyncio
async def test_empty_or_refused_provider_output_uses_unassessed_fallback(tmp_path, kind):
    svc, definition, state, _ = await activated(tmp_path)
    binding = InterviewBinding(svc, state.id)
    await binding.start("c", build_config_snapshot(get_settings())); await binding.delivered()
    await binding.accept("1", "purple bananas")
    client = AsyncMock()
    choices = [] if kind == "empty_choices" else [SimpleNamespace(finish_reason="stop",
        message=SimpleNamespace(content=None, refusal="private provider refusal"))]
    client.chat.completions.create.return_value = SimpleNamespace(choices=choices)
    with patch("app.interviews.service.VoiceCerebrasLLMService", return_value=SimpleNamespace(_client=client, request_timeout_seconds=20)):
        spoken = await binding.respond()
    assert spoken == f"<en>{definition.plan.sections[0].questions[0].text}"
    checkpoint = await svc.session(state.id)
    assert checkpoint.pending_event_id is None and not checkpoint.answers[0].evidence
    assert "private provider refusal" not in " ".join(checkpoint.technical_issues)
    client.close.assert_awaited_once(); client.chat.completions.create.assert_awaited_once()
    await binding.stop()


@pytest.mark.parametrize("invalid", ["question", "followup", "speech", "questions"])
@pytest.mark.asyncio
async def test_rejected_decision_keeps_answer_unassessed_and_logs_no_private_output(tmp_path, invalid):
    decision = Decision(action="clarify", question_id="q_1", turn_kind="answer", spoken_text="Could you give an example?")
    if invalid == "question": decision.question_id = "unknown"
    if invalid == "followup": decision.action = "follow_up"; decision.followup_excerpt = "invented"
    if invalid == "speech": decision.spoken_text = '{"private": "assessment notes"}'
    if invalid == "questions": decision.spoken_text = "Why? How?"
    decisions = AsyncMock(return_value=decision)
    svc, _, state, _ = await activated(tmp_path, decisions)
    binding = InterviewBinding(svc, state.id)
    snapshot = build_config_snapshot(get_settings())
    await binding.start("c", snapshot); await binding.delivered(); await binding.accept("1", "purple bananas")
    prompt = (await svc.session(state.id)).questions[-1].text
    sink = io.StringIO(); handle = logger.add(sink, serialize=True)
    try: spoken = await binding.respond()
    finally: logger.remove(handle)
    checkpoint = await svc.session(state.id)
    if invalid == "question":
        assert spoken == f"<en>{prompt}"
    else:
        assert "explain the approach" in spoken
    assert checkpoint.status == "in_progress" and checkpoint.pending_event_id is None
    assert len(checkpoint.answers) == 1 and checkpoint.answers[0].text == "purple bananas"
    assert not checkpoint.answers[0].evidence and not checkpoint.skills_covered
    assert (DECISION_FALLBACK_ISSUE if invalid == "question" else "Spoken response repaired") in checkpoint.answers[0].technical_issue
    assert all(skill.status == "not_assessed" for skill in (await svc.report(state.id)).skills)
    assert "private" not in sink.getvalue() + spoken
    record = json.loads(sink.getvalue())["record"]
    assert record["extra"]["event_id"] == checkpoint.answers[0].event_id
    if invalid == "question":
        assert record["extra"]["error_code"] == "decision_rejected"
    else:
        assert record["extra"]["evidence_after"] == 0
    decisions.assert_awaited_once()
    await binding.stop()


@pytest.mark.parametrize("failure", ["timeout", "connection", "http", "storage", "internal", "config"])
@pytest.mark.asyncio
async def test_actual_failures_keep_pending_turn_instead_of_using_fallback(tmp_path, failure):
    request = httpx.Request("POST", "https://api.cerebras.ai/v1/chat/completions")
    exception = {"timeout": TimeoutError(), "connection": APIConnectionError(request=request),
        "http": APIStatusError("failure", response=httpx.Response(401, request=request), body={}),
        "storage": OSError("disk failed"), "internal": RuntimeError("bug"),
        "config": ValidationError.from_exception_data("Config", [
            {"type": "int_parsing", "loc": ("max_tokens",), "input": "invalid"}])}[failure]
    decisions = AsyncMock(side_effect=exception)
    svc, _, state, _ = await activated(tmp_path, decisions)
    binding = InterviewBinding(svc, state.id)
    await binding.start("c", build_config_snapshot(get_settings())); await binding.delivered()
    await binding.accept("1", "Accepted once")
    if failure == "storage":
        decisions.side_effect = None
        decisions.return_value = Decision(action="next_question", question_id="bad", turn_kind="answer")
        with patch.object(svc, "save", new=AsyncMock(side_effect=exception)):
            with pytest.raises(type(exception)): await binding.respond()
    else:
        with pytest.raises(type(exception)): await binding.respond()
    checkpoint = await svc.session(state.id)
    assert checkpoint.pending_event_id is not None and checkpoint.answers[0].kind == "pending"
    assert not checkpoint.technical_issues
    decisions.assert_awaited_once()
    await binding.stop()


@pytest.mark.asyncio
async def test_resume_reports_safe_failure_and_does_not_duplicate_answer(tmp_path):
    async def failed(*args):
        raise TimeoutError("raw network diagnostic")
    service, _, state, _ = await activated(tmp_path, failed)
    binding = InterviewBinding(service, state.id)
    await binding.start("call1", build_config_snapshot(get_settings()))
    await binding.delivered(); await binding.accept("call1:1", "Saved candidate answer"); await binding.stop()
    resumed = InterviewBinding(service, state.id)
    with pytest.raises(HTTPException) as captured:
        await resumed.start("call2", build_config_snapshot(get_settings()))
    assert captured.value.status_code == 503 and "timed out" in captured.value.detail
    assert "raw network diagnostic" not in captured.value.detail
    await resumed.stop(captured.value.detail)
    checkpoint = await service.session(state.id)
    assert checkpoint.status == "paused" and checkpoint.pending_event_id is not None
    assert len(checkpoint.answers) == 1


@pytest.mark.asyncio
async def test_resume_recovers_malformed_saved_turn_once_without_duplicating_or_scoring(tmp_path):
    async def invalid(*args):
        raise InterviewGenerationError("decision_schema", "Decision JSON failed validation")
    decisions = AsyncMock(side_effect=invalid)
    service, definition, state, _ = await activated(tmp_path, decisions)
    binding = InterviewBinding(service, state.id)
    await binding.start("c1", build_config_snapshot(get_settings())); await binding.delivered()
    await binding.accept("1", "I like cricket"); await binding.stop()
    resumed = InterviewBinding(service, state.id)
    snapshot = build_config_snapshot(get_settings())
    await resumed.start("c2", snapshot)
    checkpoint = await service.session(state.id)
    assert checkpoint.status == "in_progress" and checkpoint.pending_event_id is None
    assert len(checkpoint.answers) == 1 and not checkpoint.answers[0].evidence
    assert definition.plan.sections[0].questions[0].text in snapshot["GREETING_TEXT"]
    assert DECISION_FALLBACK_ISSUE not in snapshot["GREETING_TEXT"]
    assert not await resumed.response_pending()
    assert all(skill.status == "not_assessed" for skill in (await service.report(state.id)).skills)
    decisions.assert_awaited_once()
    await resumed.stop()


def test_structured_diagnostics_include_scope_without_private_validation_input():
    binding = InterviewBinding(SimpleNamespace(settings=get_settings()), "interview-test")
    binding.call_sid = "call-test"
    binding.snapshot = build_config_snapshot(get_settings())
    sink = io.StringIO()
    handle = logger.add(sink, serialize=True)
    try:
        with pytest.raises(ValidationError) as captured:
            Decision.model_validate({"action": "private assessment notes", "question_id": "q_1", "turn_kind": "answer"})
        message = binding.failure_message(captured.value)
    finally:
        logger.remove(handle)
    record = json.loads(sink.getvalue())["record"]
    assert record["extra"]["interview_id"] == "interview-test"
    assert record["extra"]["error_code"] == "decision_schema"
    assert "private assessment notes" not in sink.getvalue() + message
    assert "Interview paused" in message and len(message) < 280
