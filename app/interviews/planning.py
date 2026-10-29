"""Free deterministic draft planning; recruiter review supplies role specificity."""

import re
import unicodedata
from uuid import uuid4
from app.interviews.models import Definition, Job, Plan, Question, Section

# Templates deliberately quote recruiter topics, never execute their instructions.
TEXT = {
    "en": ("What is one concrete task you personally implemented involving {topic}?", "What questions do you have about the role?", "Welcome. Please answer after I stop speaking.", "Thank you. The interview is complete.", "We are resuming. Here is the pending question."),
    "hi": ("{topic} से जुड़ा कौन सा एक ठोस काम आपने खुद किया है?", "इस भूमिका के बारे में आपके क्या सवाल हैं?", "स्वागत है। मेरे बोलना बंद करने के बाद उत्तर दें।", "धन्यवाद। साक्षात्कार पूरा हो गया है।", "हम फिर शुरू कर रहे हैं। लंबित प्रश्न यह है।"),
    "kn": ("{topic} ಕುರಿತು ನೀವು ಸ್ವತಃ ಮಾಡಿದ ಒಂದು ನಿರ್ದಿಷ್ಟ ಕೆಲಸ ಯಾವುದು?", "ಈ ಹುದ್ದೆಯ ಬಗ್ಗೆ ನಿಮಗೆ ಯಾವ ಪ್ರಶ್ನೆಗಳಿವೆ?", "ಸ್ವಾಗತ. ನಾನು ಮಾತನಾಡುವುದನ್ನು ನಿಲ್ಲಿಸಿದ ನಂತರ ಉತ್ತರಿಸಿ.", "ಧನ್ಯವಾದಗಳು. ಸಂದರ್ಶನ ಪೂರ್ಣಗೊಂಡಿದೆ.", "ನಾವು ಮುಂದುವರಿಸುತ್ತಿದ್ದೇವೆ. ಬಾಕಿ ಇರುವ ಪ್ರಶ್ನೆ ಇಲ್ಲಿದೆ."),
    "mr": ("{topic} संदर्भात तुम्ही स्वतः केलेले एक ठोस काम कोणते?", "या भूमिकेबद्दल तुम्हाला कोणते प्रश्न आहेत?", "स्वागत आहे. माझे बोलणे संपल्यावर उत्तर द्या.", "धन्यवाद. मुलाखत पूर्ण झाली आहे.", "आपण पुन्हा सुरू करत आहोत. प्रलंबित प्रश्न असा आहे."),
    "te": ("{topic} గురించి మీరు స్వయంగా చేసిన ఒక నిర్దిష్ట పని ఏమిటి?", "ఈ ఉద్యోగం గురించి మీకు ఏ ప్రశ్నలు ఉన్నాయి?", "స్వాగతం. నేను మాట్లాడటం ఆపిన తర్వాత సమాధానం ఇవ్వండి.", "ధన్యవాదాలు. ఇంటర్వ్యూ పూర్తయింది.", "మనం కొనసాగిస్తున్నాము. పెండింగ్ ప్రశ్న ఇది."),
}

PROBLEM_TEXT = {
    "en": "How would you solve this problem: {topic}?",
    "hi": "आप इस समस्या को कैसे हल करेंगे: {topic}?",
    "kn": "ಈ ಸಮಸ್ಯೆಯನ್ನು ನೀವು ಹೇಗೆ ಪರಿಹರಿಸುತ್ತೀರಿ: {topic}?",
    "mr": "तुम्ही ही समस्या कशी सोडवाल: {topic}?",
    "te": "మీరు ఈ సమస్యను ఎలా పరిష్కరిస్తారు: {topic}?",
}


def closing_topic(topic: str) -> bool:
    """Recognize administrative closing labels, not technical topics about candidates."""
    value = " ".join("".join(char if char.isalnum() or unicodedata.category(char).startswith("M")
        else " " for char in unicodedata.normalize("NFKC", topic).casefold()).split())
    return value in {"candidate questions and closing", "candidate questions", "closing", "wrap up",
        "questions and closing", "सवाल और समापन", "उम्मीदवार के सवाल", "समारोप", "ಮುಕ್ತಾಯ", "ముగింపు"}


def _terms(text: str) -> set[str]:
    aliases = {"arrays": "array", "strings": "string", "maps": "map", "sets": "set", "stacks": "stack",
        "pointers": "pointer", "techniques": "technique", "algorithms": "algorithm", "cases": "case"}
    ignored = {"and", "or", "the", "of", "in", "with", "for", "an", "a", "technique", "analysis"}
    return {aliases.get(word, word) for word in re.findall(r"\w+", text.casefold())} - ignored


def suggested_skills(job: Job, topic: str) -> list[str]:
    """Suggest only supplied skills; the recruiter confirms mappings during review."""
    topic_terms = _terms(topic)
    criteria_terms = _terms(" ".join(job.criteria))
    return [skill for skill in job.skills if _terms(skill) & topic_terms
        or (_terms(skill) & {"complexity", "correctness", "edge"} & criteria_terms)]


def review_problems(definition: Definition) -> list[str]:
    """Reject incomplete mappings at review/activation without invalidating saved history."""
    problems = []
    for section in definition.plan.sections[:-1]:
        if closing_topic(section.title):
            problems.append(f"{section.id}: remove the technical closing section; closing is already reserved")
        for question in section.questions:
            if not question.skills:
                problems.append(f"{question.id}: assign at least one recruiter-provided skill")
    return problems


def draft(job: Job) -> Definition:
    """Create stable question IDs without making a provider request."""
    total = job.duration_minutes * 60
    topics = [topic for topic in job.topics if not closing_topic(topic)]
    if not topics:
        raise ValueError("Provide at least one technical topic; candidate questions are reserved automatically")
    closing = max(45, int(total * .12))
    budget, remainder = divmod(total - closing, len(topics))
    sections = []
    for index, topic in enumerate(topics):
        skills = suggested_skills(job, topic)
        is_problem = bool(re.search(r"palindrome|balanced brackets|find two|two sum|determine whether", topic, re.I))
        template = PROBLEM_TEXT[job.language] if is_problem else TEXT[job.language][0]
        sections.append(Section(id=f"section_{index + 1}", title=topic,
            seconds=budget + (index < remainder), questions=[Question(
                id=f"q_{index + 1}", text=template.format(topic=topic.rstrip("?？")),
                skills=skills, criteria=job.criteria.copy())]))
    sections.append(Section(id="closing", title="Candidate questions", seconds=closing,
        kind="candidate_questions", questions=[Question(id="candidate_questions",
            text=TEXT[job.language][1], criteria=["Candidate questions; no technical rating"], max_followups=1)]))
    return Definition(id=uuid4().hex, job=job, plan=Plan(sections=sections))
