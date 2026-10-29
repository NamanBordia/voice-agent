"""Conservative end-of-interview intent matching and localized recovery speech."""

import unicodedata
from app.interviews.models import Language

END_PHRASES: dict[Language, tuple[str, ...]] = {
    "en": ("no", "no thanks", "no thank you", "no questions", "no more questions",
        "no I don't have any question", "no I don't have any questions", "I have no questions",
        "I don't have any questions", "I have no further questions", "that's all thank you",
        "no I don't have any questions thank you", "no further questions thank you", "not at the moment", "nothing else", "that's all"),
    "hi": ("नहीं", "कोई सवाल नहीं", "मेरे कोई सवाल नहीं हैं", "नहीं मेरे कोई सवाल नहीं हैं", "नहीं धन्यवाद", "मेरे पास कोई सवाल नहीं है"),
    "kn": ("ಇಲ್ಲ", "ಪ್ರಶ್ನೆಗಳಿಲ್ಲ", "ನನಗೆ ಯಾವುದೇ ಪ್ರಶ್ನೆಗಳಿಲ್ಲ", "ಇಲ್ಲ ಧನ್ಯವಾದಗಳು", "ನನಗೆ ಯಾವುದೇ ಪ್ರಶ್ನೆ ಇಲ್ಲ"),
    "mr": ("नाही", "माझे कोणतेही प्रश्न नाहीत", "मला काही प्रश्न नाहीत", "नाही धन्यवाद", "नाही माझे काही प्रश्न नाहीत"),
    "te": ("లేదు", "ప్రశ్నలు లేవు", "నాకు ఏ ప్రశ్నలు లేవు", "లేదు ధన్యవాదాలు", "లేదు నాకు ఎలాంటి ప్రశ్నలు లేవు"),
}
RECOVERY_TEXT: dict[Language, str] = {
    "en": "I could not confirm an answer from the supplied role details. Please ask the recruiter for clarification. Do you have another question?",
    "hi": "दी गई भूमिका की जानकारी से उत्तर की पुष्टि नहीं हो सकी। कृपया भर्तीकर्ता से स्पष्ट करें। क्या आपका कोई और सवाल है?",
    "kn": "ನೀಡಿದ ಹುದ್ದೆಯ ವಿವರಗಳಿಂದ ಉತ್ತರವನ್ನು ಖಚಿತಪಡಿಸಲಾಗಲಿಲ್ಲ. ದಯವಿಟ್ಟು ನೇಮಕಾತಿದಾರರನ್ನು ಕೇಳಿ. ನಿಮಗೆ ಬೇರೆ ಪ್ರಶ್ನೆ ಇದೆಯೇ?",
    "mr": "दिलेल्या भूमिकेच्या तपशिलांवरून उत्तराची खात्री करता आली नाही. कृपया भरतीकर्त्याला विचारा. तुमचा आणखी एखादा प्रश्न आहे का?",
    "te": "ఇచ్చిన ఉద్యోగ వివరాల నుండి సమాధానాన్ని నిర్ధారించలేకపోయాను. దయచేసి రిక్రూటర్‌ను అడగండి. మీకు మరో ప్రశ్న ఉందా?",
}


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    return " ".join("".join(char for char in value if not unicodedata.category(char).startswith("P")).split())


def has_no_more_questions(text: str, language: Language) -> bool:
    """Match whole closing replies only; mixed replies containing questions do not match."""
    return _normalize(text) in {_normalize(phrase) for phrase in (*END_PHRASES["en"], *END_PHRASES[language])}
