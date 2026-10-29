"""Regression cases from real evidence rejections and closing-loop reports."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import json

import pytest
from app.config import get_settings
from app.interviews.closing import END_PHRASES, RECOVERY_TEXT, has_no_more_questions
from app.interviews.decisions import normalize_decision
from app.interviews.errors import DECISION_FALLBACK_ISSUE, EVIDENCE_OMITTED_ISSUE, TRANSCRIPTION_ISSUE
from app.interviews.models import Decision, Evidence
from app.interviews.report import build_report
from app.interviews.service import InterviewBinding
from app.runtime import build_config_snapshot
from test_interview_controller import controller
from test_interview_service import activated


def evidence(**updates):
    return Evidence.model_validate({"criterion": "Reasoning", "rating": "meets", "excerpt": "used JWT",
        "justification": "Describes a token implementation", **updates})


@pytest.mark.parametrize("language", ["en", "hi", "kn", "mr", "te"])
def test_only_unambiguous_whole_closing_replies_finish(language):
    for phrase in END_PHRASES[language]:
        assert has_no_more_questions(f"{phrase}.", language)
    assert has_no_more_questions("No, I don't have any question.", language)
    for phrase in ["No, what is the salary?", "I don't have questions about Python, but what is the role?",
            "Tell me more about this role", "Could you repeat that?", "नहीं, वेतन कितना है?", ""]:
        assert not has_no_more_questions(phrase, language)
    c = controller(language); c.accept("1", "no", 110)
    assert c.closing_decision() is None  # A technical don't-know answer does not finish early.


@pytest.mark.parametrize("language", ["en", "hi", "kn", "mr", "te"])
@pytest.mark.asyncio
async def test_no_questions_finishes_closing_without_provider_or_scoring(tmp_path, language):
    decisions = AsyncMock(side_effect=AssertionError("Closing exit must not call the provider"))
    svc, definition, state, _ = await activated(tmp_path, decisions)
    definition.job.language = language; await svc.store.write("definitions", definition.id, definition)
    state.section_index = len(definition.plan.sections) - 1; await svc.save(state)
    binding = InterviewBinding(svc, state.id)
    await binding.start("c", build_config_snapshot(get_settings())); await binding.delivered()
    await binding.accept("1", END_PHRASES[language][0])
    spoken = await binding.respond()
    saved = await svc.session(state.id)
    assert saved.status == "completed" and saved.finalized
    assert saved.answers[0].action == "finish" and not saved.answers[0].evidence
    assert not saved.skills_covered and saved.pending_event_id is None
    assert spoken.startswith(f"<{language}>") and "Candidate questions" not in (await svc.report(state.id)).topics
    decisions.assert_not_awaited()
    await binding.stop()


def test_normalization_restores_exact_transcript_substrings_without_paraphrasing():
    c = controller(); c.accept("1", "I used  JWT for login", 110)
    raw = Decision(action="follow_up", question_id=c.question.id, turn_kind="answer",
        spoken_text="Where did you validate JWT?", followup_excerpt="USED jwt",
        evidence=[evidence(criterion="reasoning", excerpt="used jwt"),
            evidence(criterion="Concrete example", excerpt="I implemented OAuth")])
    normalized = normalize_decision(raw, c.question, c.pending(), closing=False)
    assert normalized.evidence == [evidence(excerpt="used  JWT")]
    assert normalized.followup_excerpt == "used  JWT"
    assert EVIDENCE_OMITTED_ISSUE in normalized.technical_issue
    assert len(raw.evidence) == 2 and raw.evidence[0].excerpt == "used jwt"
    assert c.apply(normalized, 111) == "<en>Where did you validate JWT?"
    report = build_report(c.definition, c.state)
    assert report.skills[0].status == "evidence_available"
    assert "incomplete for turns 2" in report.uncertainty


@pytest.mark.parametrize("bad", [
    {"criterion": "invented rubric"}, {"excerpt": "made up quote"}, {"excerpt": ""},
])
@pytest.mark.asyncio
async def test_bad_evidence_does_not_discard_the_reply_or_valid_criteria(tmp_path, bad):
    raw = Decision(action="clarify", question_id="q_1", turn_kind="answer", spoken_text="Could you describe your implementation?",
        evidence=[evidence(criterion="Concrete example"), evidence(**bad)])
    decisions = AsyncMock(return_value=raw)
    svc, _, state, _ = await activated(tmp_path, decisions)
    binding = InterviewBinding(svc, state.id)
    await binding.start("c", build_config_snapshot(get_settings())); await binding.delivered()
    await binding.accept("1", "I used JWT")
    assert await binding.respond() == "<en>Could you describe your implementation?"
    saved = await svc.session(state.id)
    assert saved.answers[0].evidence == [evidence(criterion="Concrete example")]
    assert EVIDENCE_OMITTED_ISSUE in saved.answers[0].technical_issue
    assert DECISION_FALLBACK_ISSUE not in saved.answers[0].technical_issue
    assert saved.status == "in_progress" and saved.pending_event_id is None
    decisions.assert_awaited_once(); await binding.stop()


def test_no_evidence_or_only_not_assessed_ratings_never_become_insufficient_evidence():
    for items in [[], [evidence(rating="not_assessed", excerpt="")]]:
        c = controller(); c.accept("1", "A substantive answer", 110)
        c.apply(Decision(action="next_question", question_id=c.question.id, turn_kind="answer", evidence=items), 111)
        report = build_report(c.definition, c.state)
        assert report.skills[0].status == "not_assessed" and not report.skills[0].answer_event_ids
        assert not c.state.skills_covered


def test_ambiguous_transcription_is_confirmed_before_assessment_and_never_rewritten():
    c = controller(); c.accept("1", "I used rack", 110)
    raw = Decision(action="follow_up", question_id=c.question.id, turn_kind="answer", spoken_text="Did you mean RAG?",
        followup_excerpt="RAG", technical_issue="possible transcription ambiguity: rack or RAG",
        evidence=[evidence(excerpt="RAG")])
    normalized = normalize_decision(raw, c.question, c.pending(), closing=False)
    assert normalized.action == "clarify" and not normalized.evidence
    assert TRANSCRIPTION_ISSUE in normalized.technical_issue
    assert c.apply(normalized, 111) == "<en>Did you mean RAG?"
    assert c.state.answers[0].text == "I used rack" and not c.state.skills_covered
    assert build_report(c.definition, c.state).skills[0].status == "not_assessed"


def test_duplicate_assessments_and_ambiguous_rubric_names_do_not_produce_a_score():
    c = controller(); c.accept("1", "I used JWT", 110)
    raw = Decision(action="next_question", question_id=c.question.id, turn_kind="answer",
        evidence=[evidence(rating="strong"), evidence(rating="insufficient_evidence")])
    normalized = normalize_decision(raw, c.question, c.pending(), closing=False)
    assert not normalized.evidence and "duplicate" in normalized.technical_issue


@pytest.mark.parametrize("kind", ["answer", "candidate_question", "clarification_request"])
@pytest.mark.asyncio
async def test_closing_evidence_is_discarded_without_losing_the_role_answer(tmp_path, kind):
    raw = Decision(action="clarify", question_id="candidate_questions", turn_kind=kind,
        spoken_text="The supplied role involves building APIs. Do you have another question?", evidence=[evidence()])
    svc, _, state, _ = await activated(tmp_path, AsyncMock(return_value=raw))
    state.section_index = 2; await svc.save(state)
    binding = InterviewBinding(svc, state.id)
    await binding.start("c", build_config_snapshot(get_settings())); await binding.delivered()
    await binding.accept("1", "Tell me more about this role")
    assert "role involves building APIs" in await binding.respond()
    saved = await svc.session(state.id)
    assert not saved.answers[0].evidence and saved.status == "in_progress"
    assert DECISION_FALLBACK_ISSUE not in saved.answers[0].technical_issue
    assert all(skill.status == "not_assessed" for skill in (await svc.report(state.id)).skills)
    await binding.stop()


@pytest.mark.parametrize("language", ["en", "hi", "kn", "mr", "te"])
def test_closing_fallback_acknowledges_unavailable_information_instead_of_repeating(language):
    c = controller(language); c.state.section_index = 2; c._asked(c.question.text, "next_section")
    c.accept("1", "What is the salary?", 110)
    assert c.fallback("decision_schema", 111) == f"<{language}>{RECOVERY_TEXT[language]}"
    assert "omitted spoken text" not in c.state.answers[0].technical_issue


@pytest.mark.asyncio
async def test_closing_request_uses_focused_prompt_and_excludes_assessment_history(tmp_path):
    svc, definition, state, _ = await activated(tmp_path)
    state.section_index = 2; await svc.save(state)
    binding = InterviewBinding(svc, state.id)
    await binding.start("c", build_config_snapshot(get_settings())); await binding.delivered()
    await binding.accept("1", "What will I do in this role?")
    decision = Decision(action="clarify", question_id="candidate_questions", turn_kind="candidate_question",
        spoken_text="The role involves APIs. Do you have another question?")
    client = AsyncMock()
    client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
        message=SimpleNamespace(content=decision.model_dump_json(), refusal=None))])
    with patch("app.interviews.service.VoiceCerebrasLLMService", return_value=SimpleNamespace(_client=client, request_timeout_seconds=20)):
        assert "role involves APIs" in await binding.respond()
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert "evidence MUST be []" in messages[0]["content"]
    assert "Do not fill rubric ratings" in messages[0]["content"]
    data = json.loads(messages[1]["content"])
    assert "resume" not in data["job_source"] and not data["earlier_turns"] and not data["conversation_memory"]
    assert definition.job.skills[0] in data["terminology_hints"]
    client.close.assert_awaited_once(); await binding.stop()
