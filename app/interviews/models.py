"""Versioned interview records; camera observations never enter answer ratings."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Language = Literal["en", "hi", "kn", "mr", "te"]
Status = Literal["created", "device_check", "ready", "in_progress", "paused", "completed", "failed", "abandoned"]
Action = Literal["clarify", "follow_up", "next_question", "next_section", "finish"]
Rating = Literal["not_assessed", "insufficient_evidence", "developing", "meets", "strong"]


class Record(BaseModel):
    """Reject unknown fields, including accidental image payloads."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CameraConfig(Record):
    """Development thresholds requiring human validation on target hardware."""
    fps: float = Field(default=4, ge=3, le=5)
    confidence: float = Field(default=.6, ge=.3, le=1)
    absent_secs: float = Field(default=3, ge=1, le=30)
    multiple_secs: float = Field(default=2, ge=1, le=30)
    displacement_secs: float = Field(default=3, ge=1, le=30)
    displacement_ratio: float = Field(default=.25, ge=.1, le=1)
    cooldown_secs: float = Field(default=15, ge=5, le=120)
    phone_enabled: bool = False
    phone_fps: float = Field(default=2, ge=.5, le=3)
    phone_confidence: float = Field(default=.7, ge=.3, le=1)
    phone_sustain_secs: float = Field(default=2, ge=1, le=30)
    phone_cooldown_secs: float = Field(default=15, ge=5, le=120)

    @model_validator(mode="after")
    def phone_cadence(self) -> CameraConfig:
        if self.phone_fps > self.fps:
            raise ValueError("Phone analysis cannot exceed camera analysis cadence")
        return self


class Job(Record):
    """Recruiter-provided source data, never system instructions."""
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=20000)
    skills: list[str] = Field(min_length=1, max_length=20)
    experience: str = Field(min_length=1, max_length=300)
    duration_minutes: int = Field(default=20, ge=5, le=90)
    language: Language = "en"
    topics: list[str] = Field(min_length=1, max_length=12)
    criteria: list[str] = Field(min_length=1, max_length=12)
    resume: str = Field(default="", max_length=20000)
    camera: CameraConfig = Field(default_factory=CameraConfig)

    @model_validator(mode="after")
    def bounded_lists(self) -> Job:
        for values in (self.skills, self.topics, self.criteria):
            if any(not value.strip() or len(value) > 300 for value in values):
                raise ValueError("Skills, topics, and criteria must be nonempty and at most 300 characters")
            if len(set(values)) != len(values):
                raise ValueError("Remove duplicate skills, topics, and criteria")
        return self


class Question(Record):
    """Reviewed core prompt with stable identity and question-specific rubric."""
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    text: str = Field(min_length=1, max_length=1200)
    skills: list[str] = Field(default_factory=list, max_length=20)
    criteria: list[str] = Field(min_length=1, max_length=12)
    max_followups: int = Field(default=2, ge=0, le=3)


class Section(Record):
    """Reviewed section and active-time allocation."""
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    title: str = Field(min_length=1, max_length=300)
    seconds: int = Field(ge=15, le=5400)
    kind: Literal["technical", "candidate_questions"] = "technical"
    questions: list[Question] = Field(min_length=1, max_length=20)


class Plan(Record):
    """Validated plan reviewed before candidate activation."""
    sections: list[Section] = Field(min_length=2, max_length=13)

    @model_validator(mode="after")
    def identities_and_closing(self) -> Plan:
        ids = [section.id for section in self.sections]
        questions = [q.id for section in self.sections for q in section.questions]
        if len(set(ids)) != len(ids) or len(set(questions)) != len(questions):
            raise ValueError("Section and question IDs must be unique")
        if self.sections[-1].kind != "candidate_questions" or any(s.kind != "technical" for s in self.sections[:-1]):
            raise ValueError("Reserve the final section for candidate questions")
        return self


class Definition(Record):
    """Separate interview document; existing CallLog files are untouched."""
    version: Literal[1] = 1
    id: str
    job: Job
    plan: Plan
    reviewed: bool = False

    @model_validator(mode="after")
    def grounded_coverage(self) -> Definition:
        if sum(s.seconds for s in self.plan.sections) > self.job.duration_minutes * 60:
            raise ValueError("Section budgets exceed interview duration")
        for section in self.plan.sections:
            for question in section.questions:
                if not set(question.skills) <= set(self.job.skills):
                    raise ValueError("Question skills must come from recruiter inputs")
        return self


class Evidence(Record):
    """Concise rubric judgment supported by an exact accepted-answer excerpt."""
    criterion: str = Field(min_length=1, max_length=300)
    rating: Rating
    excerpt: str = Field(default="", max_length=700)
    justification: str = Field(min_length=1, max_length=500)


class Decision(Record):
    """Internal decision; only spoken_text can enter speech processing."""
    action: Action
    question_id: str
    turn_kind: Literal["answer", "repeat_request", "clarification_request", "candidate_question"]
    spoken_text: str = Field(default="", max_length=1500)
    followup_excerpt: str = Field(default="", max_length=700)
    evidence: list[Evidence] = Field(default_factory=list, max_length=12)
    technical_issue: str = Field(default="", max_length=300)


class Answer(Record):
    """One accepted final, including requests explicitly distinguished from answers."""
    event_id: int
    source_id: str
    question_id: str
    prompt: str
    text: str = Field(min_length=1, max_length=10000)
    kind: Literal["pending", "answer", "repeat_request", "clarification_request", "candidate_question"] = "pending"
    elapsed_secs: float
    evidence: list[Evidence] = Field(default_factory=list)
    technical_issue: str = ""
    action: Action | None = None


class AskedQuestion(Record):
    """A checkpointed core prompt or follow-up; delivery may be interrupted."""
    event_id: int
    question_id: str
    text: str
    action: str


class CameraEvent(Record):
    """Compact client assertion, without images or identity information."""
    interview_id: str
    event_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    sequence: int = Field(ge=1)
    type: Literal["face_absent", "multiple_faces_detected", "face_position_changed", "camera_unavailable", "monitoring_unavailable", "phone_detected"]
    start_secs: float = Field(ge=0, le=86400)
    duration_secs: float = Field(ge=0, le=86400)
    face_count: int | None = Field(default=None, ge=0, le=100)
    confidence: float | None = Field(default=None, ge=0, le=1)
    received_at: float | None = None


VisionStatus = Literal["not_started", "active", "unavailable", "stopped"]
VisionReason = Literal["loading", "no_consent", "camera_unavailable", "unsupported_browser",
    "model_load_failed", "inference_failed", "frame_timeout", "worker_error",
    "consent_withdrawn", "connection_ended", "updates_missing", "page_hidden", "unknown"]


class VisionGap(Record):
    """A client-reported interval without confirmed analysis; no scene inference."""
    start_secs: float = Field(ge=0, le=86400, allow_inf_nan=False)
    end_secs: float = Field(ge=0, le=86400, allow_inf_nan=False)
    reason: VisionReason

    @model_validator(mode="after")
    def ordered(self) -> VisionGap:
        if self.end_secs < self.start_secs:
            raise ValueError("Gap end precedes start")
        return self


class DetectorStats(Record):
    """Cumulative counters for one detector in one media call."""
    enabled: bool = False
    status: VisionStatus = "not_started"
    frames_analyzed: int = Field(default=0, ge=0, le=100000)
    first_analyzed_secs: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)
    last_analyzed_secs: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)
    covered_secs: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False)
    unknown_secs: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False)
    last_error: VisionReason | None = None
    gaps: list[VisionGap] = Field(default_factory=list, max_length=64)
    history_truncated: bool = False

    @model_validator(mode="after")
    def consistent(self) -> DetectorStats:
        if not self.enabled and (self.frames_analyzed or self.covered_secs or self.unknown_secs
                or self.status != "not_started" or self.first_analyzed_secs is not None or self.last_analyzed_secs is not None
                or self.gaps or self.last_error is not None or self.history_truncated):
            raise ValueError("Disabled detector cannot claim analysis")
        if not self.frames_analyzed:
            if self.first_analyzed_secs is not None or self.last_analyzed_secs is not None or self.covered_secs or self.status == "active":
                raise ValueError("Analysis status/times require successful frames")
        elif self.first_analyzed_secs is None or self.last_analyzed_secs is None or self.first_analyzed_secs > self.last_analyzed_secs:
            raise ValueError("Successful analysis requires ordered timestamps")
        if self.frames_analyzed == 1 and self.covered_secs:
            raise ValueError("A single frame cannot establish coverage")
        return self


class VisionRun(Record):
    """Versioned client observations scoped to a server-owned media call."""
    version: Literal[1] = 1
    call_sid: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    start_secs: float = Field(ge=0, le=86400, allow_inf_nan=False)
    observed_until_secs: float = Field(ge=0, le=86400, allow_inf_nan=False)
    end_secs: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)
    face: DetectorStats = Field(default_factory=DetectorStats)
    phone: DetectorStats = Field(default_factory=DetectorStats)

    @model_validator(mode="after")
    def consistent(self) -> VisionRun:
        if self.observed_until_secs < self.start_secs or (self.end_secs is not None and self.end_secs != self.observed_until_secs):
            raise ValueError("Run times are inconsistent")
        duration = self.observed_until_secs - self.start_secs
        for stats in (self.face, self.phone):
            if stats.enabled and abs(stats.covered_secs + stats.unknown_secs - duration) > .05:
                raise ValueError("Coverage and unknown time must account for the run duration")
            if stats.first_analyzed_secs is not None and (stats.first_analyzed_secs < self.start_secs
                    or stats.last_analyzed_secs > self.observed_until_secs
                    or stats.covered_secs > stats.last_analyzed_secs - stats.first_analyzed_secs + .01):
                raise ValueError("Analysis lies outside the reported run")
            if any(gap.start_secs < self.start_secs or gap.end_secs > self.observed_until_secs for gap in stats.gaps):
                raise ValueError("Gap lies outside the reported run")
        return self


class Monitoring(Record):
    """Explicit monitoring status; silence is never evidence of availability."""
    status: VisionStatus = "not_started"
    sequence: int = Field(default=0, ge=0)
    received_at: float | None = None
    runs: list[VisionRun] = Field(default_factory=list, max_length=100)


class MonitoringUpdate(Record):
    """One cumulative heartbeat; the client cannot replace other calls' histories."""
    status: VisionStatus
    sequence: int = Field(ge=1)
    received_at: float | None = None
    run: VisionRun | None = None


class Session(Record):
    """Controller checkpoint: all fields JSON-safe and versioned."""
    version: Literal[1] = 1
    id: str
    definition_id: str
    token_hash: str
    status: Status = "created"
    section_index: int = 0
    question_index: int = 0
    followup_count: int = 0
    request_count: int = 0
    skills_covered: list[str] = Field(default_factory=list)
    answers: list[Answer] = Field(default_factory=list)
    questions: list[AskedQuestion] = Field(default_factory=list)
    event_sequence: int = 0
    response_in_progress: bool = False
    pending_event_id: int | None = None
    elapsed_secs: float = 0
    section_started_secs: float = 0
    active_since: float | None = None
    current_call_sid: str | None = None
    last_call_sid: str | None = None
    technical_issues: list[str] = Field(default_factory=list)
    camera_events: list[CameraEvent] = Field(default_factory=list)
    monitoring: Monitoring = Field(default_factory=Monitoring)
    camera_consent: bool = False
    recruiter_notes: str = Field(default="", max_length=10000)
    human_decision: str = Field(default="", max_length=1000)
    finalized: bool = False
