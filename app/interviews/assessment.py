"""Select evidence relevant to shared rubric skills without inventing ratings."""

import re
from app.interviews.models import Evidence


def skill_evidence(skill: str, evidence: list[Evidence]) -> list[Evidence]:
    """Complexity/correctness coverage requires its own criterion, not any positive answer."""
    if re.search(r"\bcomplexity\b", skill, re.I):
        return [item for item in evidence if re.search(r"complexity|time and.*space", item.criterion, re.I)]
    if re.search(r"\bcorrectness\b|\bedge cases\b", skill, re.I):
        return [item for item in evidence if re.search(r"correct|edge case", item.criterion, re.I)]
    return evidence
