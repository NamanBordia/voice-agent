"""Controller tests use no provider connections or audio claims."""

import pytest
from pydantic import ValidationError
from app.interviews.controller import InterviewController
from app.interviews.errors import DECISION_FALLBACK_ISSUE
from app.interviews.models import Decision, Evidence, Job, Session
from app.interviews.planning import draft
from app.interviews.report import build_report


def job(language="en", **updates):
    return Job(title="API developer", description="Build APIs", skills=["API", "SQL"],
        experience="2 years", duration_minutes=5, language=language,
        topics=["API", "SQL"], criteria=["Concrete example", "Reasoning"], **updates)


def controller(language="en"):
    definition = draft(job(language))
    state = Session(id="a" * 32, definition_id=definition.id, token_hash="hash")
    result = InterviewController(definition, state)
    result.start(100, "call1")
    result.delivered()
    return result


def decision(c, action="next_question", **updates):
    return Decision(action=action, question_id=c.question.id, turn_kind="answer", **updates)


def test_plan_requires_unique_ids_grounded_skills_budget_and_closing():
    value = draft(job())
    for change in ("duplicate", "skill", "budget", "closing"):
        data = value.model_dump()
        if change == "duplicate": data["plan"]["sections"][1]["questions"][0]["id"] = "q_1"
        if change == "skill": data["plan"]["sections"][0]["questions"][0]["skills"] = ["invented"]
        if change == "budget": data["plan"]["sections"][0]["seconds"] = 500
        if change == "closing": data["plan"]["sections"][-1]["kind"] = "technical"
        with pytest.raises(ValidationError): type(value).model_validate(data)


@pytest.mark.parametrize("language", ["en", "hi", "kn", "mr", "te"])
def test_selected_language_prefix_and_reviewed_progression(language):
    c = controller(language)
    assert c.accept("call1:1", "I used JWT", 110)
    spoken = c.apply(decision(c, "finish"), 111)
    assert spoken.startswith(f"<{language}>")
    assert c.question.id == "q_2"  # An LLM finish cannot skip reviewed coverage.
    assert c.state.answers[0].question_id == "q_1"


def test_duplicate_and_late_finals_and_response_reservation():
    c = controller()
    assert c.accept("call1:1", "I used JWT", 110)
    assert not c.accept("call1:2", "late final", 111)
    c.apply(decision(c), 112)
    assert not c.accept("call1:3", "while speaking", 113)
    c.delivered()
    assert not c.accept("call1:1", "duplicate", 114)
    assert len(c.state.answers) == 1


def test_followup_limits_and_requests_do_not_score_or_advance():
    c = controller()
    assert c.accept("1", "Repeat that", 110)
    request = Decision(action="next_question", question_id=c.question.id,
        turn_kind="repeat_request")
    c.apply(request, 111)
    assert c.question.id == "q_1" and not c.state.skills_covered
    assert c.state.questions[-1].text == c.question.text
    c.delivered()
    for index in range(3):
        assert c.accept(str(index + 2), "I used JWT", 112 + index)
        c.apply(decision(c, "follow_up", spoken_text="Where did you check the JWT?", followup_excerpt="JWT"), 113 + index)
        c.delivered()
    assert c.question.id == "q_2"
    assert len(c.state.answers) == 4


def test_decision_validation_does_not_mutate_on_invalid_evidence_or_question():
    c = controller()
    c.accept("1", "I used JWT", 110)
    before = c.state.model_dump()
    bad = decision(c, evidence=[Evidence(criterion="Reasoning", rating="strong", excerpt="invented", justification="x")])
    with pytest.raises(ValueError): c.apply(bad, 111)
    assert c.state.model_dump() == before
    with pytest.raises(ValueError): c.apply(bad.model_copy(update={"question_id": "unknown"}), 111)
    assert c.state.pending_event_id is not None


def test_timing_reserves_closing_and_excludes_pauses():
    c = controller()
    c.pause(120)
    assert c.elapsed(999) == 20
    c.start(1000, "call2"); c.delivered()
    c.accept("2", "I don't know", 1240)
    c.apply(decision(c), 1241)
    assert c.question.id == "candidate_questions"
    c.delivered(); c.accept("3", "No questions", 1280)
    c.apply(decision(c, "finish"), 1281)
    assert c.state.status == "completed" and c.state.finalized
    assert c.remaining(10000) == 0
    assert not c.accept("4", "late", 10001)


def test_report_does_not_penalize_uncovered_skills_or_rate_requests():
    c = controller()
    c.accept("1", "I used JWT", 110)
    c.apply(decision(c, evidence=[Evidence(criterion="Concrete example", rating="meets",
        excerpt="used JWT", justification="Describes an implementation")]), 111)
    report = build_report(c.definition, c.state)
    assert report.skills[0].status == "evidence_available"
    assert report.skills[1].status == "not_assessed"
    assert "unverified" in report.camera_notice


def test_repeat_request_at_followup_limit_still_repeats_pending_question():
    c = controller(); c.state.followup_count = c.question.max_followups
    c.accept("1", "Please repeat", 110)
    c.apply(Decision(action="next_question", question_id="q_1", turn_kind="repeat_request"), 111)
    assert c.question.id == "q_1" and c.state.followup_count == c.question.max_followups
    assert not c.state.skills_covered


def test_followup_requires_actual_answer_grounding_and_one_question():
    c = controller(); c.accept("1", "I used JWT", 110)
    with pytest.raises(ValueError):
        c.apply(decision(c, "follow_up", spoken_text="Why use SQL?", followup_excerpt="SQL"), 111)
    with pytest.raises(ValueError):
        c.apply(decision(c, "follow_up", spoken_text="Where was JWT checked? Why?", followup_excerpt="JWT"), 111)
    assert c.state.answers[0].kind == "pending"


def test_closing_question_cannot_be_silently_dropped_by_finish():
    c = controller(); c.state.section_index = 2; c.state.question_index = 0
    c._asked(c.question.text, "next_section")
    c.accept("1", "What is the role about?", 110)
    spoken = c.apply(Decision(action="finish", question_id="candidate_questions", turn_kind="candidate_question",
        spoken_text="The role involves APIs. What other question do you have?"), 111)
    assert "role involves APIs" in spoken and c.state.status == "in_progress"


def test_insufficient_evidence_is_explicit_in_missing_criteria():
    c = controller(); c.accept("1", "I don't know", 110)
    c.apply(decision(c, evidence=[Evidence(criterion="Reasoning", rating="insufficient_evidence",
        excerpt="I don't know", justification="No explanation was provided")]), 111)
    report = build_report(c.definition, c.state)
    assert report.skills[0].status == "insufficient_evidence"
    assert "q_1: Reasoning" in report.missing_evidence


@pytest.mark.parametrize("language", ["en", "hi", "kn", "mr", "te"])
@pytest.mark.parametrize(("kind", "action"), [
    ("clarification_request", "next_question"), ("candidate_question", "finish"),
    ("answer", "clarify"), ("answer", "follow_up"),
])
def test_missing_spoken_prompt_repeats_current_prompt_and_retains_answer(language, kind, action):
    c = controller(language)
    prompt = c.state.questions[-1].text
    assert c.accept("1", "I used JWT", 110)
    spoken = c.apply(Decision(action=action, question_id=c.question.id, turn_kind=kind,
        spoken_text="  "), 111)
    assert spoken == f"<{language}>{prompt}"
    assert c.question.id == "q_1" and c.state.pending_event_id is None
    assert len(c.state.answers) == 1 and c.state.answers[0].text == "I used JWT"
    assert c.state.answers[0].kind == kind and c.state.answers[0].evidence == []
    assert c.state.answers[0].action == "clarify"
    assert "omitted spoken text" in c.state.answers[0].technical_issue
    assert c.state.answers[0].technical_issue == c.state.technical_issues[-1]
    assert c.state.response_in_progress  # Still waits for spoken delivery.


def test_missing_prompt_retains_valid_evidence_and_cannot_bypass_safety_checks():
    c = controller(); c.accept("1", "I used JWT", 110)
    item = Evidence(criterion="Reasoning", rating="meets", excerpt="JWT", justification="Uses a token")
    c.apply(decision(c, "follow_up", evidence=[item], technical_issue="Provider diagnostic"), 111)
    assert c.state.answers[0].evidence == [item]
    assert "Provider diagnostic" in c.state.answers[0].technical_issue
    c.delivered(); c.accept("2", "Still JWT", 112)
    before = c.state.model_dump()
    with pytest.raises(ValueError):
        c.apply(decision(c, "follow_up", evidence=[item.model_copy(update={"excerpt": "fabricated"})]), 113)
    assert c.state.model_dump() == before


def test_missing_prompt_does_not_bypass_followup_or_request_limits():
    c = controller(); c.state.followup_count = c.question.max_followups
    c.accept("1", "I used JWT", 110)
    c.apply(decision(c, "follow_up"), 111)
    assert c.question.id == "q_2" and c.state.answers[0].action == "next_section"
    c.delivered(); c.state.request_count = 3; c.accept("2", "What does that mean?", 112)
    c.apply(Decision(action="next_question", question_id="q_2", turn_kind="clarification_request"), 113)
    assert c.question.id == "candidate_questions"


@pytest.mark.parametrize("language", ["en", "hi", "kn", "mr", "te"])
def test_repeated_invalid_decisions_are_bounded_and_never_score_the_candidate(language):
    c = controller(language)
    first_prompt = c.question.text
    for index in range(c.question.max_followups + 1):
        assert c.accept(str(index), "unrelated reply", 110 + index)
        spoken = c.fallback("decision_schema", 111 + index)
        assert spoken.startswith(f"<{language}>")
        if index < 2: assert spoken == f"<{language}>{first_prompt}"
        assert not c.state.skills_covered
        assert all(not answer.evidence for answer in c.state.answers)
        assert c.state.pending_event_id is None
        assert c.state.response_in_progress
        assert not c.accept(str(index), "duplicate", 111 + index)
        c.delivered()
    assert c.question.id == "q_2" and len(c.state.answers) == 3
    report = build_report(c.definition, c.state)
    assert all(skill.status == "not_assessed" and not skill.answer_event_ids for skill in report.skills)
    assert not report.topics and report.missing_evidence
    assert all(DECISION_FALLBACK_ISSUE in answer.technical_issue for answer in report.answers)


def test_invalid_decision_fallback_respects_deadline_and_reviewed_question_text():
    c = controller()
    c.question.text = "A reviewed prompt? With another question?"
    c.state.questions[-1].text = c.question.text
    c.accept("1", "unrelated", 110)
    assert c.fallback("decision_rejected", 111) == "<en>A reviewed prompt? With another question?"
    c.delivered(); c.accept("2", "still unrelated", 112)
    c.fallback("decision_schema", 500)
    assert c.state.status == "completed" and c.state.finalized
    assert c.state.pending_event_id is None and not c.state.skills_covered
