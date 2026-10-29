"""Pure explicit progression, independent of providers, memory, and media."""

from __future__ import annotations

import re
from app.interviews.assessment import skill_evidence
from app.interviews.closing import RECOVERY_TEXT, has_no_more_questions
from app.interviews.errors import DECISION_FALLBACK_ISSUE, TRANSCRIPTION_ISSUE, InterviewDecisionError
from app.interviews.models import Action, Answer, AskedQuestion, Decision, Definition, Question, Session
from app.interviews.planning import TEXT
from app.interviews.turns import COMPLETION_TEXT, FOLLOWUP_TEXT, contains_control_data, incomplete_fragment


class InterviewController:
    """Mutate one checkpoint; callers persist transactionally before emitting speech."""

    def __init__(self, definition: Definition, state: Session) -> None:
        self.definition, self.state = definition, state

    @property
    def question(self) -> Question:
        """Return the authoritative reviewed current question."""
        return self.definition.plan.sections[self.state.section_index].questions[self.state.question_index]

    def elapsed(self, now: float) -> float:
        """Count active time only; paused wall time is excluded."""
        return self.state.elapsed_secs + (max(0, now - self.state.active_since) if self.state.active_since is not None else 0)

    def remaining(self, now: float) -> float:
        """Return a nonnegative active-time budget."""
        return max(0, self.definition.job.duration_minutes * 60 - self.elapsed(now))

    def start(self, now: float, call_sid: str) -> str:
        """Start or resume using a new media connection, retaining pending state."""
        state = self.state
        if state.status not in {"created", "device_check", "ready", "paused"} or state.current_call_sid:
            raise ValueError("Interview is already running or terminal")
        resumed = state.status == "paused"
        state.status, state.active_since, state.current_call_sid = "in_progress", now, call_sid
        state.last_call_sid = call_sid
        state.response_in_progress = True
        prefix = TEXT[self.definition.job.language][4 if resumed else 2]
        # A failed decision keeps its already-saved answer; resume can retry it once.
        prompt = state.questions[-1].text if state.questions else self.question.text
        if not state.questions:
            self._asked(prompt, "start")
        return f"<{self.definition.job.language}>{prefix} {prompt}"

    def pause(self, now: float, reason: str = "Media connection ended") -> None:
        """Checkpoint an interruption exactly once and release media ownership."""
        state = self.state
        if state.active_since is not None:
            state.elapsed_secs = self.elapsed(now)
        state.active_since, state.current_call_sid = None, None
        if state.status not in {"completed", "failed", "abandoned"}:
            state.status = "paused"
        state.response_in_progress = False
        if reason and reason not in state.technical_issues:
            state.technical_issues.append(reason[:300])

    def delivered(self) -> None:
        """Release turn reservation only after the bot finishes speaking."""
        self.state.response_in_progress = False

    def accept(self, source_id: str, text: str, now: float) -> bool:
        """Deduplicate before saving and reserve the single response trigger."""
        state = self.state
        if (state.status != "in_progress" or state.response_in_progress or state.pending_event_id is not None
                or not text.strip() or any(answer.source_id == source_id for answer in state.answers)):
            return False
        state.event_sequence += 1
        state.answers.append(Answer(event_id=state.event_sequence, source_id=source_id,
            question_id=self.question.id, prompt=state.questions[-1].text, text=text,
            elapsed_secs=self.elapsed(now)))
        state.pending_event_id = state.event_sequence
        state.response_in_progress = True
        return True

    def pending(self) -> Answer:
        """Find the saved turn awaiting one validated decision."""
        return next(answer for answer in self.state.answers if answer.event_id == self.state.pending_event_id)

    def _asked(self, text: str, action: str) -> None:
        self.state.event_sequence += 1
        self.state.questions.append(AskedQuestion(event_id=self.state.event_sequence,
            question_id=self.question.id, text=text, action=action))

    def _next(self, force_section: bool = False) -> Action:
        state = self.state
        section = self.definition.plan.sections[state.section_index]
        if not force_section and state.question_index + 1 < len(section.questions):
            state.question_index += 1
            return "next_question"
        if state.section_index + 1 < len(self.definition.plan.sections):
            state.section_index += 1
            state.question_index = 0
            return "next_section"
        return "finish"

    def apply(self, decision: Decision, now: float) -> str:
        """Validate atomically, enforce bounds, and return only tagged spoken text."""
        state, question = self.state, self.question
        answer = self.pending()
        if decision.question_id != question.id:
            raise InterviewDecisionError("Decision question ID does not match controller")
        if contains_control_data(decision.spoken_text):
            raise InterviewDecisionError("Spoken text contains control data")
        if decision.turn_kind != "answer" and decision.evidence:
            raise InterviewDecisionError("Requests cannot become assessment evidence")
        for item in decision.evidence:
            if item.criterion not in question.criteria:
                raise InterviewDecisionError("Evidence criterion is outside reviewed rubric")
            if item.excerpt and item.excerpt not in answer.text:
                raise InterviewDecisionError("Evidence excerpt is absent from accepted answer")
            if item.rating in {"developing", "meets", "strong"} and not item.excerpt:
                raise InterviewDecisionError("A rating needs an actual answer excerpt")
        closing = self.definition.plan.sections[state.section_index].kind == "candidate_questions"
        if closing and decision.evidence:
            raise InterviewDecisionError("Candidate questions are not technically assessed")
        action = decision.action
        spoken = decision.spoken_text
        technical_issue = decision.technical_issue
        reviewed_prompt = False
        is_request = decision.turn_kind in {"repeat_request", "clarification_request"}
        if is_request:
            action = "clarify"
            # Repeat the reviewed/current follow-up verbatim; no unreviewed change of topic.
            spoken = state.questions[-1].text if decision.turn_kind == "repeat_request" else spoken
            reviewed_prompt = decision.turn_kind == "repeat_request"
        elif decision.turn_kind == "candidate_question":
            action = "clarify"
        if action in {"clarify", "follow_up"} and not spoken:
            # A valid classification can override a progression action whose text
            # was optional. Repeat the current prompt without inventing a reply.
            action, spoken = "clarify", question.text if DECISION_FALLBACK_ISSUE in technical_issue else state.questions[-1].text
            reviewed_prompt = True
            if DECISION_FALLBACK_ISSUE not in technical_issue:
                if spoken in COMPLETION_TEXT.values() and not incomplete_fragment(answer.text):
                    spoken = FOLLOWUP_TEXT[self.definition.job.language]
                    missing_prompt = "Decision omitted spoken text; requested approach details instead of repeating a completion prompt."
                else:
                    missing_prompt = "Decision omitted spoken text; repeated the current prompt for clarification."
                technical_issue = f"{missing_prompt} {technical_issue}".strip()[:300]
        if action == "follow_up" and decision.turn_kind != "answer":
            raise InterviewDecisionError("Follow-up requires an answer")
        if action == "follow_up" and (not decision.followup_excerpt or decision.followup_excerpt not in answer.text):
            raise InterviewDecisionError("Follow-up must cite actual content from this answer")
        if not reviewed_prompt and action in {"clarify", "follow_up"} and len(re.findall(r"[?？]", spoken)) > 1:
            raise InterviewDecisionError("Ask only one question at a time")
        elapsed = self.elapsed(now)
        sections = self.definition.plan.sections
        reserve_closing = sum(section.seconds for section in sections[-1:])
        section_expired = elapsed - state.section_started_secs >= sections[state.section_index].seconds
        total_expired = self.remaining(now) <= 0
        closing_due = not closing and self.remaining(now) <= reserve_closing
        exhausted = (state.request_count >= 3 if is_request or decision.turn_kind == "candidate_question"
                     else state.followup_count >= question.max_followups)
        # LLM cannot skip reviewed core coverage or finish early outside closing/time limits.
        if action in {"next_question", "next_section", "finish"}:
            action = self._next(force_section=section_expired)
            spoken = ""
        elif exhausted or section_expired:
            action = self._next(force_section=section_expired)
            spoken = ""
        if closing_due:
            state.section_index, state.question_index = len(sections) - 1, 0
            action, spoken = "next_section", ""
        if total_expired:
            action, spoken = "finish", ""
        answer.kind, answer.evidence = decision.turn_kind, decision.evidence
        answer.technical_issue, answer.action = technical_issue, action
        if (answer.kind == "answer" and DECISION_FALLBACK_ISSUE not in technical_issue
                and TRANSCRIPTION_ISSUE not in technical_issue
                and any(item.rating != "not_assessed" for item in answer.evidence)):
            state.skills_covered = sorted(set(state.skills_covered) | {skill for skill in question.skills
                if any(item.rating != "not_assessed" for item in skill_evidence(skill, answer.evidence))})
        state.pending_event_id = None
        if technical_issue:
            state.technical_issues.append(technical_issue)
        if action == "finish":
            state.elapsed_secs, state.active_since = elapsed, None
            state.status, state.finalized = "completed", True
            spoken = TEXT[self.definition.job.language][3]
        elif action in {"next_question", "next_section"}:
            state.followup_count = state.request_count = 0
            if action == "next_section":
                state.section_started_secs = elapsed
            spoken = self.question.text
        elif is_request or decision.turn_kind == "candidate_question":
            state.request_count += 1
        else:
            state.followup_count += 1
        if action != "finish":
            self._asked(spoken, action)
        return f"<{self.definition.job.language}>{spoken}"

    def closing_decision(self) -> Decision | None:
        """End an unambiguous closing reply without waiting for another provider request."""
        if (self.definition.plan.sections[self.state.section_index].kind == "candidate_questions"
                and has_no_more_questions(self.pending().text, self.definition.job.language)):
            return Decision(action="finish", question_id=self.question.id, turn_kind="answer")
        return None

    def fragment_decision(self) -> Decision | None:
        """Request completion of recognized fragments without grading or another LLM call."""
        if (self.definition.plan.sections[self.state.section_index].kind == "technical"
                and incomplete_fragment(self.pending().text)):
            return Decision(action="clarify", question_id=self.question.id, turn_kind="answer",
                spoken_text=COMPLETION_TEXT[self.definition.job.language],
                technical_issue="Incomplete speech fragment; completion requested, no assessment recorded.")
        return None

    def fallback(self, code: str, now: float, rejected: Decision | None = None, diagnostic: str = "") -> str:
        """Retain an unassessed turn and repeat the reviewed question within normal bounds."""
        closing = self.definition.plan.sections[self.state.section_index].kind == "candidate_questions"
        decision = Decision(action="clarify", question_id=self.question.id,
            turn_kind=rejected.turn_kind if rejected is not None else "answer",
            spoken_text=RECOVERY_TEXT[self.definition.job.language] if closing else "",
            technical_issue=f"{DECISION_FALLBACK_ISSUE} ({code}). {diagnostic}".strip()[:300])
        return self.apply(decision, now)
