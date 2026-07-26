"""English + Kannada language helpers for chat and voice."""
from __future__ import annotations

import re

_KANNADA_RE = re.compile(r"[\u0C80-\u0CFF]")
_PUNJABI_RE = re.compile(r"[\u0A00-\u0A7F]")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")  # Hindi / Marathi

SUPPORTED = ("en-IN", "kn-IN")

# Hard identity for every LLM turn — Crime AI must never claim to be Google/Gemini.
CRIME_AI_IDENTITY = """
IDENTITY (mandatory, never break this):
- You are Crime AI, the official conversational assistant for Karnataka State Police
  and the State Crime Records Bureau (SCRB).
- Help investigators with crime cases, FIRs, accused/victims, networks, trends, and hotspots.
- Never say you are Google, Gemini, ChatGPT, OpenAI, Claude, or a general-purpose chatbot.
- Never say you were "trained by Google" or any other company.
- Never teach unrelated languages or act as a dictionary/translator app.
- If the user only greets you, reply with a short greeting only (e.g. "Hello Officer" / "Hi Officer"). Do not list capabilities.
- Use English or casual Kannada (as instructed). Never reply in Punjabi, Hindi, Marathi, Tamil, or other languages.
""".strip()


def normalize_language(code: str | None) -> str:
    c = (code or "en-IN").strip()
    if c in SUPPORTED:
        return c
    lowered = c.lower()
    if lowered in ("en", "english", "eng"):
        return "en-IN"
    if lowered in ("kn", "kannada", "kan", "ಕನ್ನಡ"):
        return "kn-IN"
    return "en-IN"


# Noise / half-heard ASR fragments that must not trigger a data reply.
_VOICE_FILLER_RE = re.compile(
    r"(?i)^(uh+|um+|ah+|er+|hmm+|mm+|mhm+|ha+|huh+|ya+|yeah|yep|yes|no|nope|ok|okay|k|"
    r"right|sure|fine|cool|what|sorry|"
    r"ಸರಿ|ಹೌದು|ಇಲ್ಲ|ಹಾ|ಹಂ|ಅಂ)$"
)
_VOICE_GREETING_RE = re.compile(
    r"(?i)^(hi|hello|hey|hey\s+there|good\s+morning|good\s+afternoon|good\s+evening|"
    r"namaste|namaskara|thanks?|thank\s+you|bye|goodbye|"
    r"ನಮಸ್ಕಾರ|ಹಲೋ|ಹಾಯ್|ಧನ್ಯವಾದ|ಬೈ)$"
)
_VOICE_ALPHA_RE = re.compile(r"[A-Za-z\u0C80-\u0CFF]")
_VOICE_PLACE_RE = re.compile(
    r"(?i)\b(hubballi|hubli|bengaluru|bangalore|mysuru|mysore|mangaluru|mangalore|"
    r"belagavi|belgaum|ballari|bellary|kalaburagi|gulbarga|shivamogga|shimoga|"
    r"tumakuru|tumkur|dharwad|dakshina\s+kannada)\b"
)


def is_usable_voice_transcript(text: str | None) -> bool:
    """Reject empty / filler / too-short STT so voice does not hallucinate."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return False
    compact = re.sub(r"[.!?,।]+$", "", raw).strip()
    if _VOICE_GREETING_RE.match(compact):
        return True
    letters = re.sub(r"[^\w\u0C80-\u0CFF]+", "", raw, flags=re.UNICODE)
    if len(letters) < 4:
        return False
    tokens = [t for t in re.split(r"\s+", raw) if t]
    if len(tokens) == 1 and _VOICE_FILLER_RE.match(tokens[0].strip(".,!?")):
        return False
    if len(tokens) <= 2 and all(_VOICE_FILLER_RE.match(t.strip(".,!?")) for t in tokens):
        return False
    if not _VOICE_ALPHA_RE.search(raw):
        return False
    return True


def is_answerable_voice_question(text: str | None) -> bool:
    """True when STT text is enough to run a crime-data answer (not a bare fragment)."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not is_usable_voice_transcript(raw):
        return False
    compact = re.sub(r"[.!?,।]+$", "", raw).strip()
    if _VOICE_GREETING_RE.match(compact):
        return True
    if re.search(r"\b\d{6,}\b", raw):
        return True
    # Domain vocabulary lives later in this module — duplicate a lean check here
    # so voice gating does not depend on declaration order.
    if re.search(
        r"(?i)\b(fir|firs|case|cases|crime|crimes|accused|victim|victims|complainant|"
        r"offender|offence|offense|station|police|officer|investigat\w*|charge\s*sheet|"
        r"arrest|court|network|hotspot|trend|pattern|warning|robbery|theft|murder|"
        r"kidnap\w*|assault|cheating|fraud|snatch\w*|cyber\w*|district|report|pending|"
        r"closed|suspect|profile|show|tell|how many|where|list|find|search)\b",
        raw,
    ):
        return True
    if _VOICE_PLACE_RE.search(raw) and len(raw.split()) >= 2:
        return True
    if has_kannada(raw) and len(raw.split()) >= 2:
        return True
    # Pronoun follow-ups need session context, but still must look like a follow-up.
    if re.search(
        r"(?i)\b(this|that|those|these|same|previous|more details?|tell me more|"
        r"only pending|status|timeline|accused|victims?|network)\b",
        raw,
    ):
        return True
    return False


def didnt_catch_reply(language_code: str) -> str:
    if normalize_language(language_code) == "kn-IN":
        return "Sorry Officer, clear ಆಗಿ ಕೇಳಲಿಲ್ಲ. FIR, place, ಅಥವಾ person name ಹೇಳಿ."
    return "Sorry Officer, I did not catch that. Please say an FIR, place, or person name."


def has_kannada(text: str) -> bool:
    return bool(_KANNADA_RE.search(text or ""))


def resolve_reply_language(question: str, preference: str | None) -> str:
    """Prefer explicit UI language; follow the investigator's script when mixed."""
    pref = normalize_language(preference)
    asked = requested_language(question)
    if asked:
        return asked
    if has_kannada(question):
        return "kn-IN"
    return pref


# "Dakshina Kannada" is a district in the data, not a request to switch language.
_DAKSHINA_RE = re.compile(r"(?i)dakshina\s+kannada|ದಕ್ಷಿಣ\s*ಕನ್ನಡ")
_KN_NAME_RE = re.compile(r"(?i)\bkannada\b|ಕನ್ನಡ")
_EN_NAME_RE = re.compile(r"(?i)\benglish\b|ಇಂಗ್ಲಿಷ|ಇಂಗ್ಲೀಷ|ಆಂಗ್ಲ")
_SWITCH_CUE_RE = re.compile(
    r"(?i)\b(speak|speaking|talk|reply|answer|respond|say|switch|change|use|continue|in|please)\b"
    r"|ದಲ್ಲಿ|ನಲ್ಲಿ|ಹೇಳಿ|ಮಾತನಾಡ|ಮಾತಾಡ|ಉತ್ತರಿಸ|ಬದಲಾಯಿಸ"
)


def requested_language(text: str | None) -> str | None:
    """Detect an explicit switch such as 'speak in Kannada' / 'ಇಂಗ್ಲಿಷ್‌ನಲ್ಲಿ ಹೇಳಿ'."""
    probe = _DAKSHINA_RE.sub(" ", (text or "").strip())
    if not probe:
        return None
    kn = _KN_NAME_RE.search(probe)
    en = _EN_NAME_RE.search(probe)
    if not (kn or en) or not _SWITCH_CUE_RE.search(probe):
        return None
    if kn and en:
        # "don't speak English, use Kannada" — the later mention is the request.
        return "kn-IN" if kn.start() > en.start() else "en-IN"
    return "kn-IN" if kn else "en-IN"


_SWITCH_STRIP_RE = re.compile(
    r"(?i)\b(please|kindly|can|could|you|u|now|from|onwards|only|just|talk|talking|speak|"
    r"speaking|reply|answer|respond|say|switch|change|use|continue|to|in|into|me|my|the|"
    r"language|lets|let|us|we|i|want|need|prefer|do)\b"
    r"|ದಯವಿಟ್ಟು|ದಲ್ಲಿ|ನಲ್ಲಿ|ಹೇಳಿ|ಹೇಳು|ಮಾತನಾಡಿ|ಮಾತನಾಡು|ಮಾತಾಡಿ|ಮಾತಾಡು|ಉತ್ತರಿಸಿ|ಉತ್ತರಿಸು"
    r"|ಬದಲಾಯಿಸಿ|ಬದಲಿಸಿ|ಭಾಷೆ|ಮುಂದೆ|ಇನ್ನು|ಈಗ"
)


def is_language_switch_only(text: str | None) -> bool:
    """True when the turn is purely a language request, with no data question."""
    if not requested_language(text):
        return False
    residue = _KN_NAME_RE.sub(" ", _EN_NAME_RE.sub(" ", _DAKSHINA_RE.sub(" ", text or "")))
    residue = _SWITCH_STRIP_RE.sub(" ", residue)
    residue = re.sub(r"[^\w\u0C80-\u0CFF]+", " ", residue)
    # Combining marks orphaned by the strips above are not real words.
    residue = re.sub(r"[\u0C82\u0C83\u0CBC-\u0CD6\u0CE2\u0CE3]", "", residue)
    return len(residue.strip()) <= 2


def kn_locative(place: str) -> str:
    """'Hubballi' -> 'ಹುಬ್ಬಳ್ಳಿಯಲ್ಲಿ'. Kannada words end in a vowel, so the
    locative is ಯಲ್ಲಿ; untranslated Latin names keep a separate particle."""
    name = (place or "").strip()
    if not name:
        return ""
    if "\u0C80" <= name[-1] <= "\u0CFF":
        return f"{name}ಯಲ್ಲಿ"
    return f"{name} ನಲ್ಲಿ"


def language_switch_ack(language_code: str) -> dict:
    kn = normalize_language(language_code) == "kn-IN"
    answer = (
        "Sari, Kannada style ನಲ್ಲಿ ಹೇಳ್ತೀನಿ. ಏನು ಬೇಕು Officer?"
        if kn
        else "Sure, I'll continue in English. What would you like to know?"
    )
    return {"answer": answer, "suggestions": _welcome_chips(kn), "language_code": "kn-IN" if kn else "en-IN"}


def language_instruction(language_code: str) -> str:
    if normalize_language(language_code) == "kn-IN":
        return (
            "LANGUAGE (mandatory): Reply in casual Karnataka police conversation style — "
            "natural Kannada–English mix (code-mixing), NOT pure textbook Kannada. "
            "Examples: 'Namaskara Officer', 'Hubballi ನಲ್ಲಿ 2 robbery cases ಸಿಕ್ಕಿವೆ', "
            "'FIR details ಹೀಗಿದೆ'. Keep FIR, case, robbery, murder, station names in English "
            "when that is how officers normally speak. Short and spoken-friendly. "
            "Kannada script or Roman Kannada both OK. Never use Punjabi, Hindi, or Marathi."
        )
    return (
        "LANGUAGE (mandatory): Reply entirely in clear English only. "
        "Do NOT use Kannada, Punjabi, Hindi, Marathi, or any other language "
        "(except proper nouns from the database)."
    )


def empty_records_message(language_code: str) -> str:
    if normalize_language(language_code) == "kn-IN":
        return "Matching records ಸಿಗಲಿಲ್ಲ Officer."
    return "No matching records were found."


# DB labels are stored in English; these give spoken Kannada replies natural wording.
CRIME_KN = {
    "murder": "ಕೊಲೆ",
    "robbery": "ದರೋಡೆ",
    "theft": "ಕಳ್ಳತನ",
    "assault": "ಹಲ್ಲೆ",
    "kidnapping": "ಅಪಹರಣ",
    "chain snatching": "ಚೈನ್ ಕಿತ್ತುಕೊಳ್ಳುವಿಕೆ",
    "cybercrime": "ಸೈಬರ್ ಅಪರಾಧ",
    "domestic violence": "ಕೌಟುಂಬಿಕ ಹಿಂಸೆ",
    "cheating/fraud": "ವಂಚನೆ",
    "cheating": "ವಂಚನೆ",
    "fraud": "ವಂಚನೆ",
}

STATUS_KN = {
    "under investigation": "ತನಿಖೆಯಲ್ಲಿದೆ",
    "charge sheeted": "ಆರೋಪಪತ್ರ ಸಲ್ಲಿಸಲಾಗಿದೆ",
    "closed": "ಮುಕ್ತಾಯಗೊಂಡಿದೆ",
}

CRIME_GROUP_KN = {
    "crimes against body": "ದೇಹದ ವಿರುದ್ಧದ ಅಪರಾಧ",
    "crimes against property": "ಆಸ್ತಿ ವಿರುದ್ಧದ ಅಪರಾಧ",
    "crimes against women": "ಮಹಿಳೆಯರ ವಿರುದ್ಧದ ಅಪರಾಧ",
}

PLACE_KN = {
    "ballari": "ಬಳ್ಳಾರಿ",
    "belagavi": "ಬೆಳಗಾವಿ",
    "bengaluru": "ಬೆಂಗಳೂರು",
    "bengaluru urban": "ಬೆಂಗಳೂರು ನಗರ",
    "dakshina kannada": "ದಕ್ಷಿಣ ಕನ್ನಡ",
    "dharwad": "ಧಾರವಾಡ",
    "hubballi": "ಹುಬ್ಬಳ್ಳಿ",
    "kalaburagi": "ಕಲಬುರಗಿ",
    "mangaluru": "ಮಂಗಳೂರು",
    "mysuru": "ಮೈಸೂರು",
    "shivamogga": "ಶಿವಮೊಗ್ಗ",
    "tumakuru": "ತುಮಕೂರು",
    "belagavi camp ps": "ಬೆಳಗಾವಿ ಕ್ಯಾಂಪ್ ಠಾಣೆ",
    "belagavi market ps": "ಬೆಳಗಾವಿ ಮಾರ್ಕೆಟ್ ಠಾಣೆ",
    "cubbon park ps": "ಕಬ್ಬನ್ ಪಾರ್ಕ್ ಠಾಣೆ",
    "hubballi city ps": "ಹುಬ್ಬಳ್ಳಿ ನಗರ ಠಾಣೆ",
    "hubballi rural ps": "ಹುಬ್ಬಳ್ಳಿ ಗ್ರಾಮಾಂತರ ಠಾಣೆ",
    "kalaburagi central ps": "ಕಲಬುರಗಿ ಕೇಂದ್ರ ಠಾಣೆ",
    "kalaburagi rural ps": "ಕಲಬುರಗಿ ಗ್ರಾಮಾಂತರ ಠಾಣೆ",
    "mangaluru city ps": "ಮಂಗಳೂರು ನಗರ ಠಾಣೆ",
    "mangaluru north ps": "ಮಂಗಳೂರು ಉತ್ತರ ಠಾಣೆ",
    "mysuru west ps": "ಮೈಸೂರು ಪಶ್ಚಿಮ ಠಾಣೆ",
    "shivamogga town ps": "ಶಿವಮೊಗ್ಗ ಟೌನ್ ಠಾಣೆ",
    "tumakuru city ps": "ತುಮಕೂರು ನಗರ ಠಾಣೆ",
    "tumakuru rural ps": "ತುಮಕೂರು ಗ್ರಾಮಾಂತರ ಠಾಣೆ",
}


def localize_label(value: object, language_code: str) -> str:
    """Keep DB labels in familiar English for casual code-mixed speech.

    Officers already say robbery / Hubballi City PS / under investigation in English.
    Full Kannada gloss maps remain available for optional UI use.
    """
    _ = language_code
    return str(value or "").strip()


# Case narratives are drawn from a fixed set; translating them keeps spoken
# Kannada replies from switching to English mid-sentence.
FACTS_KN = {
    "gold chain snatched from complainant near the local market.":
        "ಸ್ಥಳೀಯ ಮಾರುಕಟ್ಟೆ ಬಳಿ ದೂರುದಾರರಿಂದ ಚಿನ್ನದ ಸರ ಕಿತ್ತುಕೊಳ್ಳಲಾಗಿದೆ.",
    "complainant assaulted by known persons following a dispute near the local market.":
        "ಸ್ಥಳೀಯ ಮಾರುಕಟ್ಟೆ ಬಳಿ ವಿವಾದದ ನಂತರ ಪರಿಚಿತರಿಂದ ದೂರುದಾರರ ಮೇಲೆ ಹಲ್ಲೆ.",
    "victim reported missing, suspected kidnapping near the local market.":
        "ಸ್ಥಳೀಯ ಮಾರುಕಟ್ಟೆ ಬಳಿ ಪೀಡಿತರು ನಾಪತ್ತೆ; ಅಪಹರಣದ ಶಂಕೆ.",
    "complainant's bank account compromised via a phishing link.":
        "ಫಿಶಿಂಗ್ ಲಿಂಕ್ ಮೂಲಕ ದೂರುದಾರರ ಬ್ಯಾಂಕ್ ಖಾತೆಗೆ ಕನ್ನ.",
    "theft of property reported from the local market.":
        "ಸ್ಥಳೀಯ ಮಾರುಕಟ್ಟೆಯಿಂದ ಆಸ್ತಿ ಕಳ್ಳತನ ವರದಿಯಾಗಿದೆ.",
    "domestic dispute escalated to physical violence at residence near the local market.":
        "ಸ್ಥಳೀಯ ಮಾರುಕಟ್ಟೆ ಬಳಿಯ ಮನೆಯಲ್ಲಿ ಕೌಟುಂಬಿಕ ವಿವಾದ ಹಿಂಸೆಗೆ ತಿರುಗಿದೆ.",
    "victim found dead under suspicious circumstances near the local market.":
        "ಸ್ಥಳೀಯ ಮಾರುಕಟ್ಟೆ ಬಳಿ ಅನುಮಾನಾಸ್ಪದ ಸ್ಥಿತಿಯಲ್ಲಿ ಪೀಡಿತರ ಶವ ಪತ್ತೆ.",
    "complainant defrauded of money via a fake investment scheme.":
        "ನಕಲಿ ಹೂಡಿಕೆ ಯೋಜನೆಯ ಮೂಲಕ ದೂರುದಾರರಿಗೆ ಹಣದ ವಂಚನೆ.",
    "complainant robbed of valuables near the local market.":
        "ಸ್ಥಳೀಯ ಮಾರುಕಟ್ಟೆ ಬಳಿ ದೂರುದಾರರ ಬೆಲೆಬಾಳುವ ವಸ್ತುಗಳ ದರೋಡೆ.",
    "complainant robbed at knifepoint near hubballi railway station.":
        "ಹುಬ್ಬಳ್ಳಿ ರೈಲು ನಿಲ್ದಾಣದ ಬಳಿ ಚಾಕು ತೋರಿಸಿ ದೂರುದಾರರ ದರೋಡೆ.",
    "mobile phone stolen from a shop counter.":
        "ಅಂಗಡಿಯ ಕೌಂಟರ್‌ನಿಂದ ಮೊಬೈಲ್ ಫೋನ್ ಕಳ್ಳತನ.",
    "victim found with stab wounds following an altercation.":
        "ಜಗಳದ ನಂತರ ಪೀಡಿತರಿಗೆ ಚೂರಿ ಇರಿತದ ಗಾಯಗಳು ಪತ್ತೆ.",
    "two unidentified men snatched a gold chain and fled on a motorcycle.":
        "ಇಬ್ಬರು ಅಪರಿಚಿತರು ಚಿನ್ನದ ಸರ ಕಿತ್ತು ಬೈಕ್‌ನಲ್ಲಿ ಪರಾರಿಯಾಗಿದ್ದಾರೆ.",
}


def localize_facts(value: object, language_code: str) -> str:
    """Keep brief facts in English — natural for casual spoken briefings."""
    _ = language_code
    return str(value or "").strip()


def found_results_header(language_code: str, n: int, question: str) -> str:
    q = re.sub(r"(?i)^\s*(hey|hi|hello|please|ok|okay)[,!\s]+", "", (question or "").strip())
    q = re.sub(r"\s+", " ", q).strip(" .?!")
    if normalize_language(language_code) == "kn-IN":
        return f"{n} cases ಸಿಕ್ಕಿವೆ."
    return f"Found {n} result(s) for: {q}"


def call_opening_line(language_code: str, *, officer_name: str | None = None) -> str:
    """Very short spoken greeting — Hello/Hi/Namaskara Officer."""
    import random

    _ = officer_name  # kept for call-site compatibility; greeting stays generic
    lang = normalize_language(language_code)
    if lang == "kn-IN":
        return random.choice(["Namaskara Officer.", "Hello Officer.", "Hi Officer."])
    return random.choice(["Hello Officer.", "Hi Officer."])


def canned_greeting(
    question: str,
    language_code: str,
    *,
    officer_name: str | None = None,
) -> str | None:
    """Deterministic greetings — short Hello/Hi/Namaskara Officer only."""
    q = (question or "").strip().lower()
    q_raw = (question or "").strip()
    greetings = {
        "hi", "hello", "hey", "hey there", "good morning", "good afternoon",
        "good evening", "namaste", "namaskara",
        "ನಮಸ್ಕಾರ", "ಹಲೋ", "ಹಾಯ್", "ಏಯ್", "ಹಾಂ", "ಹಾ",
    }
    compact = re.sub(r"[.!?,।]+$", "", q_raw).strip().lower()
    tokens = [p for p in re.split(r"\s+", compact) if p]
    if compact in greetings or q in greetings:
        pass
    elif tokens and all(p in greetings for p in tokens):
        pass
    else:
        return None
    lang = resolve_reply_language(question, language_code)
    _ = officer_name
    if lang == "kn-IN":
        return "Namaskara Officer."
    return "Hello Officer."


def _officer_display_name(name: str | None) -> str:
    cleaned = re.sub(r"\s+", " ", (name or "").strip())
    if not cleaned or cleaned.lower() in {"officer", "inspector", "admin", "user"}:
        return ""
    # Keep first + last token so long ranks don't dominate the greeting.
    parts = cleaned.split()
    if len(parts) > 3:
        return f"{parts[0]} {parts[-1]}"
    return cleaned


CAPABILITY_EN = """I am Crime AI for Karnataka State Police (SCRB).

What I can do
- FIR lookup - "Show FIR 104430006202600001"
- Case details - summary, accused, victims, timeline, similar cases
- Person history - "Tell about Yusuf Ali", "Is Yusuf Ali a repeat offender?"
- Crime search - "Robbery cases in Hubballi", "Murder cases in 2026"
- Counts - "How many cases in total?", "Which station has the most cases?"
- Criminal network - "Show criminal network for Yusuf Ali"
- Analytics - hotspots, crime trends, patterns, early warnings
- Reports - "Export investigation report"

Ask in English or Kannada."""

CAPABILITY_KN = """ನಾನು Crime AI — Karnataka State Police / SCRB.

What I can do
- FIR lookup - "Show FIR 104430006202600001"
- Case details - summary, accused, victims, timeline
- Person history - "Yusuf Ali ಕುರಿತು ಹೇಳಿ"
- Crime search - "Hubballi ನಲ್ಲಿ robbery cases"
- Counts - "ಒಟ್ಟು ಎಷ್ಟು cases?"
- Criminal network - "Yusuf Ali network ತೋರಿಸಿ"
- Analytics - hotspots, trends, patterns
- Reports - "Export investigation report"

English or casual Kannada mix ನಲ್ಲಿ ಕೇಳಿ."""

# Domain vocabulary — presence of any of these means the turn is crime business,
# so the small-talk / out-of-scope guards must stay out of the way.
_DOMAIN_RE = re.compile(
    r"(?i)\b(fir|firs|case|cases|crime|crimes|accused|victim|victims|complainant|"
    r"offender|offence|offense|station|police|officer|io|investigat\w*|charge\s*sheet|"
    r"chargesheet|arrest|court|network|hotspot|hotspots|trend|trends|pattern|patterns|"
    r"warning|warnings|robbery|theft|murder|kidnap\w*|assault|cheating|fraud|snatch\w*|"
    r"cyber\w*|domestic\s+violence|district|report|pending|closed|suspect|profile)\b"
    r"|[\u0C80-\u0CFF]"
)

_IDENTITY_RE = re.compile(
    r"(?i)^\s*(?:hey|hi|hello|so|and)?[,!\s]*"
    r"(?:who\s+(?:are|r)\s+(?:you|u)|what\s+are\s+you|what\s*'?s\s+your\s+name|"
    r"your\s+name|introduce\s+yourself|who\s+am\s+i\s+(?:talking|speaking)\s+to|"
    r"are\s+you\s+(?:a\s+)?(?:bot|human|ai|robot))\b"
    r"|ನೀವು\s*ಯಾರು|ನಿಮ್ಮ\s*ಹೆಸರು"
)

_CAPABILITY_RE = re.compile(
    r"^\s*(?:hey|hi|hello|so|ok|okay)?[,!\s]*"
    r"(?:help|menu|options|commands)\s*[.?!]?\s*$"
    r"|\b(?:what\s+(?:all\s+)?can\s+(?:you|u)\s+do|what\s+do\s+you\s+do|"
    r"how\s+can\s+you\s+help|what\s+can\s+i\s+ask|what\s+(?:kind\s+of\s+)?"
    r"(?:data|information)\s+do\s+you\s+have|your\s+(?:capabilities|features)|"
    r"show\s+me\s+(?:the\s+)?(?:options|menu)|list\s+your\s+features)\b"
    r"|ಏನು\s*ಮಾಡಬಹುದು|ಏನೆಲ್ಲಾ\s*ಮಾಡ|ಸಹಾಯ\s*ಮಾಡ",
    re.I,
)

_THANKS_RE = re.compile(
    r"(?i)^\s*(?:ok|okay)?[,!\s]*(?:thanks?|thank\s+you|thanx|thx|great|good\s+job|"
    r"nice|perfect|awesome|super)\b[\s.!,]*(?:crime\s*ai|bro|team)?\s*[.!]?\s*$"
    r"|ಧನ್ಯವಾದ"
)

_BYE_RE = re.compile(
    r"(?i)^\s*(?:ok|okay|alright)?[,!\s]*(?:bye|goodbye|good\s*night|see\s+you|"
    r"that\s*'?s\s+all|nothing\s+else|exit|quit|end\s+call|stop)\b[\s.!]*$"
    r"|ಬೈ\b|ಸಾಕು"
)

_ACK_RE = re.compile(
    r"(?i)^\s*(?:ok|okay|k|hmm+|yeah|yep|yes|no|nope|sure|fine|got\s+it|alright|cool)"
    r"\s*[.!?]?\s*$|^\s*(?:ಸರಿ|ಹೌದು|ಇಲ್ಲ)\s*[.!?]?\s*$"
)

_OUT_OF_SCOPE_RE = re.compile(
    r"(?i)\b(weather|temperature|rain|cricket|score|ipl|football|movie|film|song|"
    r"joke|recipe|cook|stock|bitcoin|horoscope|election|prime\s+minister|president|"
    r"capital\s+of|translate|poem|essay|write\s+(?:me\s+)?(?:a\s+)?(?:code|program|"
    r"python|java)|who\s+won|birthday|restaurant|hotel\s+near)\b"
)


def _welcome_chips(kn: bool) -> list[dict]:
    if kn:
        return [
            {"label": "FIR lookup", "message": "Show FIR 104430006202600001"},
            {"label": "Hotspots", "message": "Crime hotspots ಎಲ್ಲಿವೆ?"},
            {"label": "Early warnings", "message": "Early warnings ಏನು?"},
            {"label": "Case count", "message": "ಒಟ್ಟು ಎಷ್ಟು cases?"},
        ]
    return [
        {"label": "Look up an FIR", "message": "Show FIR 104430006202600001"},
        {"label": "Hotspots", "message": "Where are the crime hotspots?"},
        {"label": "Early warnings", "message": "What are the early warnings?"},
        {"label": "Case counts", "message": "How many cases are there in total?"},
    ]


def smalltalk_reply(
    question: str,
    language_code: str,
    *,
    officer_name: str | None = None,
) -> dict | None:
    """Deterministic answer for greetings, identity, capability, thanks, bye, off-topic.

    Returns {"answer", "suggestions", "language_code"} or None when the turn is
    real crime business and should go to the data agents.
    """
    q = (question or "").strip()
    if not q:
        return None
    lang = resolve_reply_language(q, language_code)
    kn = lang == "kn-IN"

    if is_language_switch_only(q):
        return language_switch_ack(lang)

    greeting = canned_greeting(q, lang, officer_name=officer_name)
    if greeting:
        return {"answer": greeting, "suggestions": _welcome_chips(kn), "language_code": lang}

    if _IDENTITY_RE.search(q):
        # Who are you — short intro only. Full capability list waits for "what can you do".
        answer = (
            "ನಾನು Crime AI — Karnataka Police / SCRB investigation assistant."
            if kn
            else "I am Crime AI, the investigation assistant for Karnataka State Police "
            "and the State Crime Records Bureau."
        )
        return {"answer": answer, "suggestions": _welcome_chips(kn), "language_code": lang}

    if _CAPABILITY_RE.search(q):
        return {
            "answer": CAPABILITY_KN if kn else CAPABILITY_EN,
            "suggestions": _welcome_chips(kn),
            "language_code": lang,
        }

    if _THANKS_RE.search(q):
        answer = (
            "Sari Officer. ಮುಂದೆ ಏನು ಬೇಕು?"
            if kn
            else "Glad to help. What would you like next?"
        )
        return {"answer": answer, "suggestions": _welcome_chips(kn), "language_code": lang}

    if _BYE_RE.search(q):
        answer = (
            "Sari Officer. Investigation ಗೆ all the best."
            if kn
            else "Understood. Good luck with the investigation."
        )
        return {"answer": answer, "suggestions": [], "language_code": lang}

    if _ACK_RE.search(q):
        answer = (
            "Sari. ಮುಂದೆ ಏನು ಬೇಕು Officer?"
            if kn
            else "Noted. How can I help next?"
        )
        return {"answer": answer, "suggestions": _welcome_chips(kn), "language_code": lang}

    if _OUT_OF_SCOPE_RE.search(q) and not _DOMAIN_RE.search(q):
        answer = (
            "ಅದು ನನ್ನ scope ಅಲ್ಲ Officer. ನಾನು Karnataka Police crime data ಮಾತ್ರ help ಮಾಡ್ತೀನಿ."
            if kn
            else "That is outside my scope. I only assist with Karnataka Police crime data."
        )
        return {"answer": answer, "suggestions": _welcome_chips(kn), "language_code": lang}

    return None


def no_results_message(language_code: str, hints: list[str] | None = None) -> str:
    """Empty-result reply that tells the investigator what will work instead."""
    lang = normalize_language(language_code)
    tips = [h for h in (hints or []) if h][:4]
    if lang == "kn-IN":
        base = "ಈ search ಗೆ records ಸಿಗಲಿಲ್ಲ."
        if tips:
            return base + "\n\nTry these\n" + "\n".join(f"- {t}" for t in tips)
        return base + " Crime number, station, crime type, ಅಥವಾ person name ಹೇಳಿ."
    base = "No records matched that search."
    if tips:
        return base + "\n\nTry\n" + "\n".join(f"- {t}" for t in tips)
    return base + " Name a crime number, police station, crime type, or person."


def strip_vendor_identity(text: str, language_code: str = "en-IN") -> str:
    """If the model leaks Google/Gemini identity, replace with Crime AI line."""
    if not text:
        return text
    bad = re.search(
        r"(?i)\b(google|gemini|trained by google|large language model|openai|chatgpt|claude)\b",
        text,
    )
    lang = normalize_language(language_code)
    if bad:
        if lang == "kn-IN":
            return "Namaskara. ನಾನು Crime AI. ಹೇಗೆ help ಮಾಡ್ಲಿ?"
        return "Hello. I am Crime AI. How can I help?"
    if lang == "en-IN" and (_PUNJABI_RE.search(text) or (_DEVANAGARI_RE.search(text) and not has_kannada(text))):
        return (
            "I am Crime AI for Karnataka State Police. "
            "Please ask in English or Kannada about cases, people, or hotspots."
        )
    if lang == "kn-IN" and (_PUNJABI_RE.search(text) or _DEVANAGARI_RE.search(text)):
        return (
            "ನಾನು Crime AI — Karnataka Police. "
            "Case, person, ಅಥವಾ hotspot ಕುರಿತು English / casual Kannada ನಲ್ಲಿ ಕೇಳಿ."
        )
    return text
