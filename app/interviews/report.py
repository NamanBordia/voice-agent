"""Reports expose excerpts, defined rubrics, uncertainty, and separate camera claims."""

from typing import Literal
from pydantic import Field
from app.interviews.assessment import skill_evidence
from app.interviews.errors import DECISION_FALLBACK_ISSUE, EVIDENCE_OMITTED_ISSUE, TRANSCRIPTION_ISSUE
from app.interviews.models import Answer, AskedQuestion, CameraEvent, Definition, Monitoring, Plan, Record, Session

RUBRIC = {
    "not_assessed": "No usable assessment was recorded for this skill; check technical issues and unassessed answers.",
    "insufficient_evidence": "The answer does not supply enough evidence for this criterion.",
    "developing": "The cited content partly addresses the criterion, with an identified gap.",
    "meets": "The cited content addresses the reviewed criterion with a concrete explanation.",
    "strong": "The cited content addresses the criterion and explains reasoning or tradeoffs.",
}
CAMERA_NOTICE = "Browser observations are unverified client assertions and can be manipulated. They do not establish cheating, attention, honesty, emotion, or suitability and never affect technical ratings."
UNCERTAINTY_NOTICE = "Ratings concern transcript content only. STT can be wrong; missing evidence is not proof of missing ability. A human must review excerpts and unanswered criteria."


class SkillAssessment(Record):
    """Evidence links remain explicit rather than inventing aggregate numeric scores."""
    skill: str
    status: Literal["not_assessed", "evidence_available", "insufficient_evidence"]
    answer_event_ids: list[int] = Field(default_factory=list)


class Report(Record):
    """Versioned recruiter-only report with human-owned decision fields."""
    version: Literal[1] = 1
    id: str
    role: str
    language: str
    duration_secs: float
    status: str
    topics: list[str]
    skills: list[SkillAssessment]
    questions: list[AskedQuestion]
    reviewed_plan: Plan
    missing_evidence: list[str]
    answers: list[Answer]
    rubric: dict[str, str] = Field(default_factory=lambda: RUBRIC.copy())
    uncertainty: str = UNCERTAINTY_NOTICE
    technical_issues: list[str]
    camera_notice: str = CAMERA_NOTICE
    camera_events: list[CameraEvent]
    monitoring: Monitoring
    recruiter_notes: str
    human_decision: str


def build_report(definition: Definition, state: Session) -> Report:
    """Derive coverage only from accepted answers, never camera or accent."""
    questions = {q.id: q for section in definition.plan.sections for q in section.questions}
    assessed = [answer for answer in state.answers
        if answer.kind == "answer" and DECISION_FALLBACK_ISSUE not in answer.technical_issue
        and TRANSCRIPTION_ISSUE not in answer.technical_issue
        and any(item.rating != "not_assessed" for item in answer.evidence)]
    skills = []
    for skill in definition.job.skills:
        answers = [answer for answer in assessed if skill in questions[answer.question_id].skills
            and any(item.rating != "not_assessed" for item in skill_evidence(skill, answer.evidence))]
        skills.append(SkillAssessment(skill=skill,
            status="evidence_available" if any(e.excerpt and e.rating in {"developing", "meets", "strong"}
                for a in answers for e in skill_evidence(skill, a.evidence))
                else "insufficient_evidence" if answers else "not_assessed",
            answer_event_ids=[a.event_id for a in answers]))
    covered = {answer.question_id for answer in assessed}
    technical_ids = {q.id for section in definition.plan.sections if section.kind == "technical" for q in section.questions}
    interrupted = [str(answer.event_id) for answer in state.answers
        if answer.question_id in technical_ids and answer.technical_issue]
    uncertainty = UNCERTAINTY_NOTICE
    if interrupted:
        uncertainty += f" Technical assessment was unavailable or incomplete for turns {', '.join(interrupted)}; review their transcripts manually. Those failures are not evidence of missing ability."
    unmapped = sorted(qid for qid in technical_ids if not questions[qid].skills)
    if unmapped:
        uncertainty += f" Questions without skill mappings: {', '.join(unmapped)}; their answers cannot establish skill coverage."
    unasked = sorted(technical_ids - {asked.question_id for asked in state.questions})
    if unasked:
        uncertainty += f" Technical questions not asked: {', '.join(unasked)}."
    if state.status == "completed" and state.elapsed_secs >= definition.job.duration_minutes * 60:
        uncertainty += " Interview ended at the active-time limit; completed does not mean every question or assessment was completed."
    if state.status == "completed" and "Connection ended before completion" in state.technical_issues:
        uncertainty += " An earlier connection ended before completion; its interruption remains in the history after resume."
    return Report(id=state.id, role=definition.job.title, language=definition.job.language,
        duration_secs=state.elapsed_secs, status=state.status,
        topics=[s.title for s in definition.plan.sections if s.kind == "technical" and any(q.id in covered for q in s.questions)],
        skills=skills, questions=state.questions, reviewed_plan=definition.plan,
        missing_evidence=[f"{q.id}: {criterion}" for s in definition.plan.sections if s.kind == "technical"
            for q in s.questions for criterion in q.criteria
            if not any(a.question_id == q.id and any(e.criterion == criterion and e.excerpt
                and e.rating in {"developing", "meets", "strong"} for e in a.evidence) for a in assessed)],
        uncertainty=uncertainty,
        answers=state.answers,
        technical_issues=state.technical_issues, camera_events=state.camera_events,
        monitoring=state.monitoring, recruiter_notes=state.recruiter_notes, human_decision=state.human_decision)
