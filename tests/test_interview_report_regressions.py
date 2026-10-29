"""Observed interview failures, exercised without providers or microphone claims."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.config import get_settings
from app.interviews.decisions import normalize_decision
from app.interviews.errors import DECISION_FALLBACK_ISSUE
from app.interviews.models import Decision, Evidence, Job, MonitoringUpdate
from app.interviews.planning import draft
from app.interviews.report import build_report
from app.interviews.service import InterviewBinding, InterviewService
from app.interviews.store import InterviewStore
from app.interviews.turns import FOLLOWUP_TEXT, explains_approach, incomplete_fragment
from app.interviews.vision import extend_unknown
from app.runtime import build_config_snapshot
from test_interview_controller import controller, job
from test_interview_service import activated
from test_vision_monitoring import setup_vision, snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("speech,excerpt", [
    ('{"private": "assessment notes"}', "JWT"),
    ("Where was JWT checked? Why there?", "JWT"),
    ("Why did you use OAuth?", "OAuth"),
])
async def test_invalid_speech_retains_grounded_assessment_and_never_speaks_rejected_text(tmp_path, speech, excerpt):
    item = Evidence(criterion="Reasoning", rating="meets", excerpt="JWT", justification="Identifies the token")
    raw = Decision(action="follow_up", question_id="q_1", turn_kind="answer", spoken_text=speech,
        followup_excerpt=excerpt, evidence=[item])
    svc, _, state, _ = await activated(tmp_path, AsyncMock(return_value=raw))
    binding = InterviewBinding(svc, state.id)
    await binding.start("c", build_config_snapshot(get_settings())); await binding.delivered()
    await binding.accept("1", "I used JWT")
    assert await binding.respond() == f"<en>{FOLLOWUP_TEXT['en']}"
    saved = await svc.session(state.id)
    assert saved.answers[0].evidence == [item]
    assert "Spoken response repaired" in saved.answers[0].technical_issue
    assert DECISION_FALLBACK_ISSUE not in saved.answers[0].technical_issue
    assert (await svc.report(state.id)).skills[0].status == "evidence_available"
    await binding.stop()


def test_procedural_answer_is_not_a_request_but_mixed_questions_still_are():
    c = controller(); c.accept("1", "What approach I will follow is we can place one pointer at the beginning", 110)
    raw = Decision(action="next_question", question_id="q_1", turn_kind="clarification_request",
        evidence=[Evidence(criterion="Reasoning", rating="developing", excerpt="place one pointer",
            justification="Describes pointer placement")])
    normalized = normalize_decision(raw, c.question, c.pending(), closing=False)
    assert normalized.turn_kind == "answer" and normalized.evidence
    c.apply(normalized, 111)
    assert build_report(c.definition, c.state).skills[0].status == "evidence_available"
    for text in ["Could you explain how we can use a hash map?", "I used JWT; what does validation mean?", "Can you repeat that?"]:
        assert not explains_approach(text)


@pytest.mark.parametrize("text", ["However,", "Okay so", "The most efficient way is using a", "If they don't match,"])
@pytest.mark.asyncio
async def test_fragment_requests_completion_without_provider_or_negative_ratings(tmp_path, text):
    decisions = AsyncMock(side_effect=AssertionError("Fragments are handled locally"))
    svc, _, state, _ = await activated(tmp_path, decisions)
    binding = InterviewBinding(svc, state.id)
    await binding.start("c", build_config_snapshot(get_settings())); await binding.delivered()
    await binding.accept("1", text)
    assert "finish explaining" in await binding.respond()
    saved = await svc.session(state.id)
    assert not saved.answers[0].evidence and saved.answers[0].action == "clarify"
    assert "Incomplete speech" in (await svc.report(state.id)).answers[0].technical_issue
    decisions.assert_not_awaited(); await binding.stop()


@pytest.mark.parametrize("text", ["No", "I don't know", "O(n)", "Use a stack", "That is incorrect", "नहीं"])
def test_short_valid_answers_are_not_fragments(text):
    assert not incomplete_fragment(text)


@pytest.mark.parametrize("language", ["en", "hi", "kn", "mr", "te"])
def test_missing_model_prompt_does_not_repeat_completion_request_after_substantive_continuation(language):
    c = controller(language); c.accept("1", "So", 110)
    c.apply(c.fragment_decision(), 111); c.delivered()
    c.accept("2", "I used a hash map to store item prices and find a matching gift-card balance", 112)
    item = Evidence(criterion="Reasoning", rating="developing", excerpt="hash map", justification="Names the lookup structure")
    spoken = c.apply(Decision(action="clarify", question_id="q_1", turn_kind="answer", evidence=[item]), 113)
    assert spoken == f"<{language}>{FOLLOWUP_TEXT[language]}"
    assert c.state.answers[-1].evidence == [item]
    assert "instead of repeating a completion prompt" in c.state.answers[-1].technical_issue


def test_bracket_notation_is_allowed_but_control_objects_are_not():
    c = controller(); c.accept("1", "Use a stack", 110)
    spoken = "How would you handle {}, [] and () with a stack?"
    assert c.apply(Decision(action="clarify", question_id="q_1", turn_kind="answer", spoken_text=spoken), 111) == f"<en>{spoken}"


def test_repeated_completion_prompts_are_not_evidence_of_interviewer_hints():
    c = controller(); c.accept("1", "I used JWT", 110)
    criterion = "Distinguishes independently explained solutions from solutions developed after interviewer hints"
    c.question.criteria.append(criterion)
    raw = Decision(action="next_question", question_id="q_1", turn_kind="answer", evidence=[
        Evidence(criterion=criterion, rating="insufficient_evidence", justification="Was asked to finish speaking")])
    normalized = normalize_decision(raw, c.question, c.pending(), closing=False)
    assert not normalized.evidence and "human review" in normalized.technical_issue


def dsa_job():
    return Job(title="SDE Intern", description="Implement algorithms", experience="Intern", duration_minutes=5,
        skills=["Arrays and strings", "Hash maps and hash sets", "Stacks", "Two-pointer techniques",
            "Time and space complexity analysis", "Algorithm correctness and edge cases"],
        topics=["Arrays and hash maps: find two distinct elements whose sum equals a target",
            "Strings and two pointers: check whether a string is a palindrome",
            "Stacks: determine whether a string containing (), [], and {} has balanced brackets",
            "Candidate questions and closing"],
        criteria=["Explains a correct algorithm", "Correctly analyses time and space complexity", "Identifies edge cases"])


def test_dsa_draft_has_direct_questions_mapped_supplied_skills_and_one_closing():
    value = draft(dsa_job())
    assert len(value.plan.sections) == 4
    first, second, third, closing = value.plan.sections
    assert "Hash maps and hash sets" in first.questions[0].skills
    assert "Two-pointer techniques" in second.questions[0].skills
    assert "Stacks" in third.questions[0].skills
    for section in (first, second, third):
        assert section.questions[0].text.startswith("How would you solve this problem:")
        assert "Time and space complexity analysis" in section.questions[0].skills
        assert set(section.questions[0].skills) <= set(value.job.skills)
    assert closing.kind == "candidate_questions" and not closing.questions[0].skills
    assert sum(s.seconds for s in value.plan.sections) == 300


def test_positive_data_structure_evidence_does_not_claim_complexity_coverage():
    c = controller()
    skill = "Time and space complexity analysis"
    c.definition.job.skills.append(skill); c.question.skills.append(skill)
    criterion = "Correctly analyses time and space complexity"
    c.question.criteria.append(criterion)
    c.accept("1", "I used JWT", 110)
    c.apply(Decision(action="next_question", question_id="q_1", turn_kind="answer", evidence=[
        Evidence(criterion="Reasoning", rating="meets", excerpt="JWT", justification="Names the token"),
        Evidence(criterion=criterion, rating="insufficient_evidence", justification="No complexity explanation")]), 111)
    report = build_report(c.definition, c.state)
    assert report.skills[0].status == "evidence_available"
    assert next(item for item in report.skills if item.skill == skill).status == "insufficient_evidence"


@pytest.mark.asyncio
async def test_review_requires_mapping_but_legacy_unmapped_reports_remain_readable(tmp_path):
    svc = InterviewService(get_settings(), InterviewStore(tmp_path / "interviews"))
    value = await svc.create(job())
    value.plan.sections[0].questions[0].skills = []
    with pytest.raises(HTTPException, match="assign at least one") as rejected:
        await svc.review(value.id, value.plan)
    assert rejected.value.status_code == 422
    value.reviewed = True
    await svc.store.write("definitions", value.id, value)
    with pytest.raises(HTTPException): await svc.activate(value.id)
    assert (await svc.definition(value.id)).plan == value.plan


@pytest.mark.asyncio
@pytest.mark.parametrize("long_speech", [False, True])
async def test_long_model_diagnostic_does_not_erase_other_valid_fields(tmp_path, long_speech):
    svc, definition, state, _ = await activated(tmp_path)
    binding = InterviewBinding(svc, state.id)
    await binding.start("c", build_config_snapshot(get_settings())); await binding.delivered()
    await binding.accept("1", "I used JWT")
    content = {"action": "next_question", "question_id": "q_1", "turn_kind": "answer",
        "technical_issue": "Model diagnostic. " * 100,
        "evidence": [{"criterion": "Reasoning", "rating": "meets", "excerpt": "JWT", "justification": "Names the token"}]}
    if long_speech:
        content["spoken_text"] = "Private verbose output. " * 100
    client = AsyncMock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
        message=SimpleNamespace(content=json.dumps(content), refusal=None))])
    with patch("app.interviews.service.VoiceCerebrasLLMService", return_value=SimpleNamespace(_client=client, request_timeout_seconds=20)):
        await binding.respond()
    saved = await svc.session(state.id)
    assert saved.answers[0].evidence
    if long_speech:
        assert saved.answers[0].action == "clarify" and "length limit" in saved.answers[0].technical_issue
        assert "Private verbose" not in saved.questions[-1].text
    else:
        assert len(saved.answers[0].technical_issue) == 300 and saved.answers[0].action == "next_section"
    await binding.stop()


def test_report_warns_about_all_technical_issues_unmapped_questions_and_time_limit():
    c = controller(); c.question.skills = []; c.accept("1", "An answer", 110)
    c.apply(Decision(action="clarify", question_id="q_1", turn_kind="answer", spoken_text="Please finish?",
        technical_issue="Spoken response repaired: multiple questions"), 500)
    report = build_report(c.definition, c.state)
    assert "turns 2" in report.uncertainty
    assert "without skill mappings: q_1" in report.uncertainty
    assert "active-time limit" in report.uncertainty
    assert "not asked: q_2" in report.uncertainty


@pytest.mark.asyncio
async def test_camera_failure_tail_is_unknown_until_voice_stops(tmp_path):
    with patch("app.interviews.service.time.time", return_value=100) as clock:
        svc, binding, state, _ = await setup_vision(tmp_path)
        run = snapshot((await svc.session(state.id)).monitoring.runs[0])
        extend_unknown(run, 2, "camera_unavailable", close=True)
        clock.return_value = 102
        await svc.camera(state.id, MonitoringUpdate(sequence=1, status="unavailable", run=run))
        clock.return_value = 120
        report = await svc.report(state.id)
        assert report.monitoring.runs[0].end_secs == 20
        assert report.monitoring.runs[0].phone.covered_secs == .75
        assert report.monitoring.runs[0].phone.unknown_secs == 19.25
        assert (await svc.session(state.id)).monitoring.runs[0].end_secs == 2
        await binding.stop()
        saved = await svc.session(state.id)
        assert saved.monitoring.runs[0].end_secs == saved.elapsed_secs == 20
        assert saved.monitoring.runs[0].face.unknown_secs == 19.25
