"""Conservative transcript guards; never infer missing technical words."""

import re
from app.interviews.models import Language

COMPLETION_TEXT: dict[Language, str] = {
    "en": "Your answer may be incomplete. Could you finish explaining your approach?",
    "hi": "आपका उत्तर अधूरा हो सकता है। क्या आप अपनी बात पूरी कर सकते हैं?",
    "kn": "ನಿಮ್ಮ ಉತ್ತರ ಅಪೂರ್ಣವಾಗಿರಬಹುದು. ನಿಮ್ಮ ವಿವರಣೆಯನ್ನು ಪೂರ್ಣಗೊಳಿಸಬಹುದೇ?",
    "mr": "तुमचे उत्तर अपूर्ण असू शकते. तुम्ही तुमचे स्पष्टीकरण पूर्ण करू शकता का?",
    "te": "మీ సమాధానం అసంపూర్ణంగా ఉండవచ్చు. మీ వివరణను పూర్తి చేయగలరా?",
}
FOLLOWUP_TEXT: dict[Language, str] = {
    "en": "Could you explain the approach you just described in more detail?",
    "hi": "क्या आप अभी बताए गए तरीके को थोड़ा और समझा सकते हैं?",
    "kn": "ನೀವು ಈಗ ವಿವರಿಸಿದ ವಿಧಾನವನ್ನು ಇನ್ನಷ್ಟು ವಿವರಿಸಬಹುದೇ?",
    "mr": "तुम्ही आत्ता सांगितलेली पद्धत आणखी स्पष्ट करू शकता का?",
    "te": "మీరు ఇప్పుడే వివరించిన విధానాన్ని మరింత వివరించగలరా?",
}


def explicit_request(text: str) -> bool:
    """Recognize request wording before correcting a contradictory classification."""
    return bool(re.search(
        r"\b(?:can|could|would|will) you\b|\b(?:please (?:repeat|explain|clarify)|"
        r"what (?:is|does|do|are)|how (?:does|do|should)|tell me|did you mean|"
        r"repeat (?:that|the|your)|explain (?:that|the question)|help me|"
        r"i (?:don't|do not) understand)\b|"
        r"(?:दोहराइए|समझाइए|क्या आप|पुन्हा सांगा|समजावून|ದಯವಿಟ್ಟು|ವಿವರಿಸಬಹುದೇ|మళ్ళీ చెప్ప|వివరించగలరా)",
        text, re.I))


def explains_approach(text: str) -> bool:
    """Correct only clear procedural explanations, not mixed requests or questions."""
    if explicit_request(text) or "?" in text or "？" in text:
        return False
    return bool(re.search(
        r"\b(?:i|we) (?:can |will |would |have )?(?:use[ds]?|place|store|iterate|compare|"
        r"check|push|pop|move|skip|keep|implemented|built|calculate)\b|"
        r"\b(?:my approach|the approach i|what approach i will follow)\b|"
        r"(?:मैंने|हम कर सकते|मी वापर|ನಾನು ಬಳಸ|మనం ఉపయోగి|నేను ఉపయోగి)", text, re.I))


def incomplete_fragment(text: str) -> bool:
    """Catch narrow, observed fragments; short valid answers remain normal turns."""
    value = text.casefold().strip(" \t\r\n.,!?।")
    if value in {"however", "so", "okay so", "uh", "um", "लेकिन", "मगर", "ಆದರೆ", "पण", "అయితే"}:
        return True
    return bool(re.search(r"\b(?:using a|find (?:some|sum) of|to detect whether|"
        r"if they don['’]t match|the string is|we we)$", value))


def contains_control_data(text: str) -> bool:
    """Reject control tags and serialized objects while allowing bracket notation."""
    return bool(re.search(r"\[/?LANG|</?(?:en|hi|kn|mr|te)>|```|\{\s*[\"'][^\"']+[\"']\s*:", text, re.I))
