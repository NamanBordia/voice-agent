"""Repair formatting and discard unsupported evidence without inventing an assessment."""

import re
from collections import Counter
from app.interviews.errors import EVIDENCE_OMITTED_ISSUE, TRANSCRIPTION_ISSUE
from app.interviews.models import Answer, Decision, Evidence, Language, Question
from app.interviews.turns import FOLLOWUP_TEXT, contains_control_data, explains_approach


def transcript_excerpt(excerpt: str, transcript: str) -> str | None:
    """Resolve case/whitespace differences to an exact original transcript substring."""
    if not excerpt or excerpt in transcript:
        return excerpt
    pattern = r"\s+".join(re.escape(part) for part in excerpt.split())
    match = re.search(pattern, transcript, flags=re.IGNORECASE)
    return match.group() if match else None


def normalize_decision(decision: Decision, question: Question, answer: Answer, *, closing: bool,
                       language: Language = "en") -> Decision:
    """Keep valid speech/evidence independently; the controller validates the resulting decision."""
    if decision.question_id != question.id:
        return decision  # Never transfer an assessment from a different question.
    result = decision.model_copy(deep=True)
    issues: list[str] = []
    if not closing and result.turn_kind != "answer" and explains_approach(answer.text):
        result.turn_kind = "answer"
        issues.append("Turn classification corrected: procedural explanation retained as an answer.")
    if closing or result.turn_kind != "answer":
        if result.evidence:
            issues.append("Assessment evidence discarded for a non-assessed turn.")
        result.evidence = []
    elif TRANSCRIPTION_ISSUE.casefold() in result.technical_issue.casefold():
        result.evidence = []  # Confirmation precedes any assessment of uncertain words.
        result.action = "clarify"
        result.technical_issue = f"{TRANSCRIPTION_ISSUE}. {result.technical_issue}"[:300]
    else:
        criterion_counts = Counter(" ".join(item.casefold().split()) for item in question.criteria)
        criteria = {" ".join(item.casefold().split()): item for item in question.criteria
            if criterion_counts[" ".join(item.casefold().split())] == 1}
        retained: list[Evidence] = []
        for item in result.evidence:
            criterion = item.criterion if item.criterion in question.criteria else criteria.get(" ".join(item.criterion.casefold().split()))
            excerpt = transcript_excerpt(item.excerpt, answer.text)
            if criterion and re.search(r"independen|interviewer hints", criterion, re.I):
                issues.append("independence criterion requires human review of actual hints")
            elif criterion is None:
                issues.append("unreviewed criterion")
            elif excerpt is None:
                issues.append("excerpt absent from the accepted transcript")
            elif item.rating in {"developing", "meets", "strong"} and not excerpt:
                issues.append("rating without an answer excerpt")
            else:
                retained.append(item.model_copy(update={"criterion": criterion, "excerpt": excerpt}))
        counts = Counter(item.criterion for item in retained)
        if any(count > 1 for count in counts.values()):
            issues.append("duplicate criterion assessments")
        result.evidence = [item for item in retained if counts[item.criterion] == 1]
        if issues:
            issues = [f"{EVIDENCE_OMITTED_ISSUE}: {'; '.join(dict.fromkeys(issues))}."]
    if result.followup_excerpt:
        excerpt = transcript_excerpt(result.followup_excerpt, answer.text)
        if excerpt is not None:
            result.followup_excerpt = excerpt
    speech_issue = ""
    if contains_control_data(result.spoken_text):
        speech_issue = "control data"
    elif result.action in {"clarify", "follow_up"} and len(re.findall(r"[?？]", result.spoken_text)) > 1:
        speech_issue = "multiple questions"
    elif result.action == "follow_up" and result.spoken_text and (not result.followup_excerpt or result.followup_excerpt not in answer.text):
        speech_issue = "ungrounded follow-up"
    if speech_issue:
        # Evidence already passed its independent checks. Never speak rejected output.
        from app.interviews.closing import RECOVERY_TEXT
        result.action = "clarify"
        result.spoken_text = RECOVERY_TEXT[language] if closing else FOLLOWUP_TEXT[language]
        result.followup_excerpt = ""
        issues.append(f"Spoken response repaired: {speech_issue}; validated evidence retained.")
    if issues:
        result.technical_issue = " ".join([*issues, result.technical_issue]).strip()[:300]
    return result
