"""Bounded in-call memory for 20-30 turn calls.

Memory = recent window (kept in the LLM context) + pinned facts + a small
running summary that only kicks in once the call gets long + optional recall
snippets when the user explicitly refers to earlier context. The full transcript
is persisted for the Call Detail page but is NEVER injected wholesale.

Everything here is deterministic and JSON-safe. Pinned-fact extraction is
rule-based (no extra LLM call). The running summary is a cheap deterministic
compaction updated AFTER the reply (never before), so it cannot add latency to
the hot STT -> LLM -> TTS path.
"""

from __future__ import annotations

import re
from typing import Any

_FILLER = {
    "haan", "han", "ok", "okay", "k", "hmm", "uh", "um", "yeah", "ya", "yes",
    "no", "nope", "sure", "wait", "hello", "hi", "bro", "sir", "bhai", "acha", "accha",
}

_PINNED_RE = re.compile(
    r"\b(i want|i need|i'd like|i would like|i prefer|please (?:use|make|keep|don'?t)"
    r"|don'?t|do not|make sure|remember that|my name is|i am|i'm|use .+ not )\b",
    re.IGNORECASE,
)

# Explicit back-reference cues (English + romanized/Devanagari Hindi). NOTE: this
# no longer GATES recall — recall always runs a lexical search and injects on
# score. This only signals "the user is explicitly asking to recall", which
# RELAXES the match threshold so an explicit ask reliably surfaces something.
_RECALL_RE = re.compile(
    r"(what did i say|as i (?:said|told|mentioned)|earlier i|like i said"
    r"|remember what i|go back to that|the thing i mentioned"
    r"|you (?:already |just )?told me|you (?:already |just )?said|did(?:n'?t)? you say"
    r"|don'?t you remember|you remember|we (?:talked|spoke|discussed)"
    r"|i (?:already |just )?(?:said|told|mentioned)|mentioned (?:earlier|before)"
    r"|pehle|pichhle|yaad|bola tha|kaha tha|bataya|starting mein|shuru mein"
    r"|पहले|याद|बताया|बोला था|कहा था)",
    re.IGNORECASE,
)

# Function/stop words dropped before lexical recall scoring so only CONTENT tokens
# count. Includes the romanized-Hindi fillers above, common English function
# words, recall meta-words ("told", "remember", "earlier" — which must not match
# themselves), and frequent Hindi grammatical particles so Hinglish content
# tokens dominate.
_STOPWORDS = _FILLER | {
    "the", "a", "an", "and", "or", "but", "if", "then", "so", "to", "of", "in", "on", "at",
    "for", "with", "is", "am", "are", "was", "were", "be", "do", "does", "did", "have", "has",
    "had", "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them", "my",
    "your", "his", "its", "our", "their", "this", "that", "these", "those", "what", "which",
    "who", "when", "where", "why", "how", "not", "can", "could", "would", "should", "will",
    "just", "now", "about", "like", "mean", "said", "say", "tell", "told", "get", "got",
    "already", "again", "still", "very", "please", "remember", "earlier", "before", "mentioned",
    "discussed", "talked", "spoke", "dont", "don't", "na", "hai", "tha", "thi", "ko", "ka", "ki",
    "ke", "mein", "se", "par", "kya", "kar", "raha", "rahi",
}

_ENTITY_RUN_RE = re.compile(r"[A-Z][a-z]+(?:[\s,]+[A-Z][a-z]+)+")


def _salient_tokens(text: str) -> set[str]:
    """Content tokens for lexical matching: lowercased, stop/filler words dropped.

    The Devanagari block (U+0900–U+097F) is included explicitly because Python's
    ``\\w`` excludes Devanagari combining vowel marks, which would otherwise split
    a Hindi word like "हिंदी" into single base consonants and drop them. With the
    range, Hindi/Hinglish text tokenizes whole, the same as English.
    """
    toks = re.findall(r"[\w\u0900-\u097F\u0C00-\u0CFF']+", (text or "").lower())
    return {t for t in toks if (t.isdigit() or len(t) >= 3) and t not in _STOPWORDS}


def _has_concrete_fact(text: str) -> bool:
    """True if the line carries a number or a multi-word proper-noun run (e.g. a
    place or a list like 'Delhi Mall, Mumbai Central') — used to rank concrete
    claims above chatty lines."""
    return bool(re.search(r"\d", text)) or bool(_ENTITY_RUN_RE.search(text))


def new_call_memory() -> dict[str, Any]:
    return {"pinned_facts": [], "running_summary": None, "summary_last_updated_turn": 0}


def _is_filler(text: str) -> bool:
    words = [w for w in re.findall(r"[\w']+", text.lower())]
    if len(words) < 3:
        return True
    return all(w in _FILLER for w in words)


def extract_pinned_facts(
    user_text: str, existing: list[str], *, max_pinned: int
) -> list[str]:
    """Return the updated pinned-fact list after considering one user turn."""
    cleaned = " ".join((user_text or "").split())
    if not cleaned or _is_filler(cleaned) or not _PINNED_RE.search(cleaned):
        return existing
    fact = cleaned[:160]
    lowered = {f.lower() for f in existing}
    if fact.lower() in lowered:
        return existing
    updated = existing + [fact]
    # Keep the most recent facts within the cap.
    return updated[-max_pinned:]


def recall_triggered(user_text: str) -> bool:
    return bool(_RECALL_RE.search(user_text or ""))


def search_recall_with_sources(
    transcript: list[dict[str, Any]],
    *,
    query: str,
    max_snippets: int,
    min_overlap: int = 2,
    exclude_recent: int = 0,
) -> list[dict[str, Any]]:
    """Lexical recall over the full transcript, returning snippet + source turn.

    Same scoring as :func:`search_recall_snippets` but each result is a dict
    ``{"text": "role: ...", "source_event_index": int | None}`` so the dashboard can
    link a recalled quote back to the turn it came from. ``source_event_index`` is the
    transcript event's 1-based ``event_index`` (may be None for legacy events).
    """
    if max_snippets <= 0:
        return []
    q = _salient_tokens(query)
    if not q:
        return []
    events = transcript[:-exclude_recent] if exclude_recent > 0 else list(transcript)
    q_norm = " ".join((query or "").split()).lower()
    scored: list[tuple[int, int, str, Any]] = []
    for idx, ev in enumerate(events):
        role = ev.get("role")
        if role not in ("user", "assistant"):
            continue
        text = " ".join(str(ev.get("text", "")).split())
        if not text or text.lower() == q_norm:  # skip empties and the query turn itself
            continue
        overlap = len(q & _salient_tokens(text))
        if overlap < min_overlap:
            continue
        score = overlap + (1 if _has_concrete_fact(text) else 0)
        scored.append((score, idx, f"{role}: {text}", ev.get("event_index")))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)  # score desc, then recency
    return [
        {"text": snippet, "source_event_index": src}
        for _score, _idx, snippet, src in scored[:max_snippets]
    ]


def search_recall_snippets(
    transcript: list[dict[str, Any]],
    *,
    query: str,
    max_snippets: int,
    min_overlap: int = 2,
    exclude_recent: int = 0,
) -> list[str]:
    """Lexical recall over the full transcript (BOTH user and assistant turns).

    Finds earlier turns whose content-token overlap with ``query`` is at least
    ``min_overlap``, and returns the top ``max_snippets`` as role-tagged quotes
    (``"user: ..."`` / ``"assistant: ..."``), ranked by overlap then recency. The
    most recent ``exclude_recent`` events are skipped — they are already verbatim
    in the recent window — so recall only surfaces OLDER context. Returns ``[]``
    when nothing clears the bar: callers ALWAYS search but inject only what comes
    back (always search, conditionally inject). Assistant lines are returned AS
    quotes (claims the bot made), never asserted as truth.
    """
    return [
        r["text"]
        for r in search_recall_with_sources(
            transcript,
            query=query,
            max_snippets=max_snippets,
            min_overlap=min_overlap,
            exclude_recent=exclude_recent,
        )
    ]


def should_update_summary(turn_count: int, memory_cfg: dict[str, Any]) -> bool:
    enable_after = int(memory_cfg.get("enable_running_summary_after_turn", 10))
    every = max(1, int(memory_cfg.get("summary_update_every_turns", 5)))
    if turn_count < enable_after:
        return False
    return turn_count % every == 0


def build_running_summary(
    transcript: list[dict[str, Any]],
    *,
    recent_turn_pairs: int,
    max_summary_tokens: int,
) -> str:
    """Deterministically compact the OLDER portion of the call (BOTH roles).

    Older = every turn except the most recent ``2 * recent_turn_pairs`` events
    (still verbatim in the recent window). Role-tagged so a recap reflects what the
    assistant said too, not just the user — otherwise the bot's own earlier claims
    vanish from any summary. Cheap string work only (token ~ 4 chars as the length
    budget); no LLM.
    """
    events = [
        ev for ev in transcript
        if ev.get("text") and ev.get("role") in ("user", "assistant")
    ]
    window = max(0, recent_turn_pairs * 2)
    older = events[: max(0, len(events) - window)]
    if not older:
        return ""
    lines = [
        f"{ev['role']}: {' '.join(str(ev.get('text', '')).split())}" for ev in older
    ]
    body = "; ".join(lines)
    budget = max_summary_tokens * 4
    if len(body) > budget:
        body = body[:budget].rsplit(" ", 1)[0] + "…"
    return f"Earlier in this call: {body}"


def update_summary_if_due(
    memory: dict[str, Any],
    *,
    turn_count: int,
    transcript: list[dict[str, Any]],
    memory_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Post-turn summary refresh (never called before a reply)."""
    if not should_update_summary(turn_count, memory_cfg):
        return memory
    summary = build_running_summary(
        transcript,
        recent_turn_pairs=int(memory_cfg.get("recent_turn_pairs", 6)),
        max_summary_tokens=int(memory_cfg.get("max_summary_tokens", 250)),
    )
    if summary:
        memory["running_summary"] = summary
        memory["summary_last_updated_turn"] = turn_count
    return memory


def build_system_block(
    base_system_prompt: str,
    *,
    language_policy: str,
    language_hint: str | None = None,
    pinned_facts: list[str],
    running_summary: str | None,
    recall_snippets: list[str],
    factual_guardrail: str | None = None,
    max_dynamic_tokens: int | None = None,
) -> str:
    """Assemble the dynamic system message for one turn.

    ``language_policy`` is the always-injected, code-owned <en>/<hi> contract and
    ``factual_guardrail`` is the persona-independent no-fake-specifics rule; both
    are placed BEFORE the persona prompt so a persona's own prefix-less examples or
    creative leeway cannot override them. ``language_hint`` is an optional weak
    STT-derived hint. The persona prompt stays user-editable while these system
    rules cannot be edited away.

    The protocol/guardrail/persona/hint are always kept. The dynamic memory
    sections (facts, recall, running summary) are bounded by ``max_dynamic_tokens``
    (token ~ 4 chars). Under budget they are kept highest-value first — durable
    facts and query-relevant recall before the lossy running summary — so a big
    memory block cannot silently inflate LLM TTFB. ``None`` means no cap (back-compat).
    """
    parts = [language_policy.strip()]
    if factual_guardrail:
        parts.append(factual_guardrail.strip())
    parts.append(base_system_prompt.strip())
    if language_hint:
        parts.append(language_hint.strip())

    # Highest-value first: facts + query-relevant recall before the lossy summary.
    dynamic: list[str] = []
    if pinned_facts:
        dynamic.append("Known facts for this call:\n- " + "\n- ".join(pinned_facts))
    if recall_snippets:
        dynamic.append("Possibly relevant earlier turns (quotes):\n- " + "\n- ".join(recall_snippets))
    if running_summary:
        dynamic.append(running_summary)

    budget = max_dynamic_tokens * 4 if max_dynamic_tokens else None
    for text in dynamic:
        if budget is None:
            parts.append(text)
        elif len(text) <= budget:
            parts.append(text)
            budget -= len(text)
        elif budget > 40:  # partial room left: truncate this section, then stop
            parts.append(text[:budget].rsplit(" ", 1)[0] + "…")
            budget = 0
        # else: drop this (and any lower-priority) section entirely
    return "\n\n".join(p for p in parts if p)


def trim_recent_messages(
    messages: list[Any], *, recent_turn_pairs: int
) -> list[Any]:
    """Keep only the most recent ``2 * recent_turn_pairs`` non-system messages."""
    non_system = [m for m in messages if _role(m) != "system"]
    keep = max(0, recent_turn_pairs * 2)
    return non_system[-keep:] if keep else []


def _role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "role", ""))
