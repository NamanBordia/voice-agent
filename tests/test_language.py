from pipecat.transcriptions.language import Language

from app.services import language as lang


def test_coerce_response_language_validates():
    assert lang.coerce_response_language("en") == "en"
    assert lang.coerce_response_language("hi") == "hi"
    assert lang.coerce_response_language("EN") == "en"
    assert lang.coerce_response_language("hi-IN") == "hi"
    assert lang.coerce_response_language("fr", fallback="hi") == "hi"
    assert lang.coerce_response_language("zz") == "en"  # default fallback
    assert lang.coerce_response_language(None) == "en"


def test_parse_prefix_basic_en_and_hi():
    assert lang.try_parse_language_prefix("<en> hello") == ("en", " hello", True)
    assert lang.try_parse_language_prefix("<hi> namaste") == ("hi", " namaste", True)


def test_parse_prefix_case_insensitive_and_leading_ws():
    assert lang.try_parse_language_prefix("<EN> Hi")[0] == "en"
    lang_code, remainder, done = lang.try_parse_language_prefix("  <hi> baat")
    assert (lang_code, done) == ("hi", True)
    assert remainder == " baat"


def test_parse_prefix_split_across_frames_keeps_building():
    # Partial tags are not yet decidable -> done False, caller keeps buffering.
    assert lang.try_parse_language_prefix("<") == (None, "<", False)
    assert lang.try_parse_language_prefix("<e") == (None, "<e", False)
    assert lang.try_parse_language_prefix("<en") == (None, "<en", False)
    assert lang.try_parse_language_prefix("") == (None, "", False)


def test_parse_prefix_gives_up_when_not_a_tag():
    # First real char isn't part of <en>/<hi> -> fall back immediately (done True).
    code, _, done = lang.try_parse_language_prefix("Hello, no tag here")
    assert code is None and done is True
    code, _, done = lang.try_parse_language_prefix("<xy> nope")
    assert code is None and done is True


def test_parse_prefix_gives_up_at_max_chars():
    code, _, done = lang.try_parse_language_prefix("<e" + "x" * 50, max_prefix_chars=8)
    assert code is None and done is True


def test_record_stt_language_hint_updates_state():
    st = lang.new_language_state()
    assert "explicit_language_override" not in st
    lang.record_stt_language_hint(st, "hi")
    assert st["last_user_language"] == "hi"
    assert st["turn_count"] == 1
    # response_language is NOT decided by STT (the LLM tag decides it).
    assert st["response_language"] == "en"


def test_fallback_response_language_order():
    assert lang.fallback_response_language(None) == "en"
    assert lang.fallback_response_language({"response_language": "hi"}) == "hi"
    assert lang.fallback_response_language(
        {"response_language": "zz", "last_user_language": "hi"}
    ) == "hi"
    assert lang.fallback_response_language({"last_user_language": "en"}) == "en"


def test_record_response_language_coerces():
    st = lang.new_language_state()
    lang.record_response_language(st, "hi")
    assert st["response_language"] == "hi"
    lang.record_response_language(st, "garbage")  # keeps last valid (hi)
    assert st["response_language"] == "hi"


def test_hints_mapping_drops_unknown():
    enums = lang.language_hints_to_enums(["en", "hi", "zz"])
    assert Language.EN in enums and Language.HI in enums and len(enums) == 2


def test_detect_from_transcription_normalizes():
    class _F:
        language = "en-US"
    assert lang.detect_language_from_transcription(_F()) == "en"


def test_policy_mentions_both_tags():
    assert "<en>" in lang.RESPONSE_LANGUAGE_POLICY
    assert "<hi>" in lang.RESPONSE_LANGUAGE_POLICY


def test_strip_language_prefix_tag():
    # LANG-1: greeting text bypasses the prefix processor, so a leaked tag must be
    # strippable here. Well-formed leading <en>/<hi> tags are removed; plain text
    # is left intact.
    assert lang.strip_language_prefix_tag("<en>Hi there") == "Hi there"
    assert lang.strip_language_prefix_tag("<hi> Namaste") == "Namaste"
    assert lang.strip_language_prefix_tag("<EN> Hello") == "Hello"
    assert lang.strip_language_prefix_tag("Hi there") == "Hi there"
    assert lang.strip_language_prefix_tag("") == ""


def test_sanitize_prefix_fallback_strips_malformed_tags():
    assert lang.sanitize_prefix_fallback_text("<eng> hello") == "hello"
    assert lang.sanitize_prefix_fallback_text("<<en>> hello") == "hello"
    no_close = lang.sanitize_prefix_fallback_text("<hi hello")
    assert not no_close.startswith("<") and "hello" in no_close
    truncated = lang.sanitize_prefix_fallback_text("<en")
    assert not truncated.startswith("<") and not truncated.startswith(">")
    # Text without a tag is left intact.
    assert lang.sanitize_prefix_fallback_text("Hello there") == "Hello there"
    # Pure bracket junk collapses to empty (caller then speaks a safe line).
    assert lang.sanitize_prefix_fallback_text("<>") == ""


def test_sanitize_language_control_tags_all_cases():
    s = lang.sanitize_language_control_tags
    assert s("<en>Hello") == "Hello"                                      # leading
    assert s("Got it. Which class? <en>Was it philosophy?") == (         # misplaced (mid)
        "Got it. Which class? Was it philosophy?"
    )
    assert s("<en><hi>Okay.") == "Okay."                                  # duplicate
    assert s("<eng>Okay.") == "Okay."                                     # malformed
    assert s("<<en>>Okay.") == "Okay."                                    # malformed
    assert s("<hi Okay.") == "Okay."                                      # unclosed
    assert s("Hi there!") == "Hi there!"                                  # normal text untouched
    assert s("I have 3 < 5 apples") == "I have 3 < 5 apples"              # unrelated brackets kept
    assert s("<en>") == ""                                                # tag-only -> empty


def test_strip_control_tags_inline_preserves_whitespace():
    # Streaming-safe: only tags removed, surrounding spaces kept (no word merge).
    assert lang.strip_control_tags_inline("Hello <en>there") == "Hello  there"
    assert lang.strip_control_tags_inline("Hello ") == "Hello "
    assert lang.strip_control_tags_inline("no tags here") == "no tags here"


def test_split_safe_emit_holds_partial_control_tag():
    assert lang.split_safe_emit("Got it. <e") == ("Got it. ", "<e")        # forming tag held
    assert lang.split_safe_emit("Got it. <") == ("Got it. ", "<")
    assert lang.split_safe_emit("Got it. <en>Was") == ("Got it. <en>Was", "")  # complete -> not held
    assert lang.split_safe_emit("3 < 5") == ("3 < 5", "")                  # not a control prefix
    assert lang.split_safe_emit("plain text") == ("plain text", "")


def test_policy_is_positional_and_guardrail_exists():
    pol = lang.RESPONSE_LANGUAGE_POLICY.lower()
    assert "first" in pol and "middle" in pol            # emphasizes position
    assert "<en>" in lang.RESPONSE_LANGUAGE_POLICY and "<hi>" in lang.RESPONSE_LANGUAGE_POLICY
    assert lang.FACTUAL_GUARDRAIL and "invent" in lang.FACTUAL_GUARDRAIL.lower()
