from app.models.runtime_config_models import MemoryCfg
from app.services import memory as mem


def test_recall_min_overlap_default_is_2():
    # 5 effectively disables lexical recall in short spoken turns; default stays 2.
    assert MemoryCfg().recall_min_overlap == 2


def test_extract_pinned_accepts_durable_statement():
    out = mem.extract_pinned_facts("I want low latency replies", [], max_pinned=12)
    assert out == ["I want low latency replies"]


def test_extract_pinned_rejects_filler():
    assert mem.extract_pinned_facts("haan ok", [], max_pinned=12) == []
    assert mem.extract_pinned_facts("wait", [], max_pinned=12) == []
    assert mem.extract_pinned_facts("bro listen", [], max_pinned=12) == []


def test_extract_pinned_dedupes_and_caps():
    existing = ["I want low latency replies"]
    same = mem.extract_pinned_facts("i want low latency replies", existing, max_pinned=12)
    assert same == existing  # case-insensitive dedupe
    capped = existing
    for i in range(20):
        capped = mem.extract_pinned_facts(f"please keep fact number {i} in mind", capped, max_pinned=5)
    assert len(capped) == 5


def test_recall_trigger():
    assert mem.recall_triggered("what did I say earlier about the voice?")
    assert mem.recall_triggered("as I mentioned, use Hindi")
    assert not mem.recall_triggered("okay let's continue")


def test_recall_trigger_broadened_and_hinglish():
    # Broadened cues (used only to RELAX the recall threshold, never to gate).
    assert mem.recall_triggered("you already told me the demo locations")
    assert mem.recall_triggered("don't you remember?")
    assert mem.recall_triggered("pehle kya bola tha")
    assert mem.recall_triggered("पहले बताया था")
    assert not mem.recall_triggered("okay let's continue")


def test_search_recall_finds_assistant_line_past_window():
    # The exact demo-locations failure: an older ASSISTANT claim is retrievable by
    # lexical overlap and returned as a role-tagged quote (claim, not truth).
    transcript = [
        {"role": "assistant",
         "text": "We have demo stations at Delhi Mall, Mumbai Central, Bangalore Tech Park."},
    ] + [{"role": "user", "text": t}
         for t in ("okay", "sure", "thanks", "got it", "no problem", "alright")]
    out = mem.search_recall_snippets(
        transcript, query="you already told me the demo locations, don't you remember?",
        max_snippets=2, min_overlap=1, exclude_recent=0,
    )
    assert out, "should retrieve the demo line"
    assert out[0].startswith("assistant: ")
    assert "Delhi Mall" in out[0]


def test_search_recall_excludes_recent_window():
    transcript = [
        {"role": "assistant", "text": "demo stations at Delhi Mall"},
        {"role": "user", "text": "x"}, {"role": "user", "text": "y"},
    ]
    keep = mem.search_recall_snippets(
        transcript, query="demo", max_snippets=2, min_overlap=1, exclude_recent=2)
    assert any("Delhi Mall" in s for s in keep)
    drop = mem.search_recall_snippets(
        transcript, query="demo", max_snippets=2, min_overlap=1, exclude_recent=3)
    assert drop == []


def test_search_recall_empty_on_unrelated_query():
    transcript = [{"role": "assistant", "text": "demo stations at Delhi Mall"}]
    out = mem.search_recall_snippets(
        transcript, query="what is the weather today", max_snippets=2, min_overlap=1)
    assert out == []


def test_search_recall_matches_hinglish_and_devanagari():
    transcript = [
        {"role": "user", "text": "mujhe ball pen chahiye likhne ke liye"},
        {"role": "assistant", "text": "हिंदी में बात करते हैं"},
        {"role": "user", "text": "filler one"}, {"role": "user", "text": "filler two"},
    ]
    romanized = mem.search_recall_snippets(
        transcript, query="konsa pen use karu", max_snippets=2, min_overlap=1)
    assert any("ball pen" in s for s in romanized)
    deva = mem.search_recall_snippets(
        transcript, query="हिंदी", max_snippets=2, min_overlap=1)
    assert any("हिंदी" in s for s in deva)


def test_build_system_block_token_cap_bounds_block_and_trims_summary_last():
    big_summary = "Earlier in this call: " + ("word " * 400) + " ENDMARKER"
    block = mem.build_system_block(
        "Base.", language_policy="POLICY", language_hint=None,
        pinned_facts=["User uses a ball pen."],
        running_summary=big_summary,
        recall_snippets=["assistant: We have demo stations at Delhi Mall."],
        max_dynamic_tokens=60,  # ~240 chars of dynamic budget
    )
    assert "Base." in block and "POLICY" in block          # essentials always kept
    assert "User uses a ball pen." in block                # durable facts survive
    assert "Delhi Mall" in block                           # query-relevant recall survives
    assert "ENDMARKER" not in block                        # lossy summary tail trimmed by the cap
    assert len(block) < 600                                # block stays bounded (no TTFB blowup)


def test_build_system_block_no_cap_keeps_all_sections():
    block = mem.build_system_block(
        "Base.", language_policy="POLICY", pinned_facts=["F"],
        running_summary="S", recall_snippets=["assistant: R"],
    )
    assert all(s in block for s in ("Base.", "POLICY", "F", "S", "assistant: R"))


def test_should_update_summary_threshold():
    cfg = {"enable_running_summary_after_turn": 10, "summary_update_every_turns": 5}
    assert not mem.should_update_summary(9, cfg)
    assert mem.should_update_summary(10, cfg)
    assert not mem.should_update_summary(11, cfg)
    assert mem.should_update_summary(15, cfg)


def test_build_running_summary_compacts_older_only():
    transcript = [{"role": "user", "text": f"point {i}"} for i in range(10)]
    transcript += [{"role": "assistant", "text": "ok"}]
    summary = mem.build_running_summary(transcript, recent_turn_pairs=3, max_summary_tokens=250)
    assert summary.startswith("Earlier in this call:")
    assert "point 0" in summary
    assert "point 9" not in summary  # in the recent window (2*pairs events), excluded


def test_running_summary_includes_assistant_turns():
    # The bot's earlier claims must survive into a recap — summary is role-tagged
    # over BOTH roles, not user-only.
    transcript = [
        {"role": "user", "text": "i use a ball pen"},
        {"role": "assistant", "text": "We have demo stations at Delhi Mall and Mumbai Central."},
    ] + [{"role": "user", "text": f"filler {i}"} for i in range(2)]
    summary = mem.build_running_summary(transcript, recent_turn_pairs=1, max_summary_tokens=250)
    assert "assistant: We have demo stations" in summary
    assert "user: i use a ball pen" in summary


def test_build_running_summary_respects_token_budget():
    transcript = [{"role": "user", "text": "word " * 50} for _ in range(20)]
    summary = mem.build_running_summary(transcript, recent_turn_pairs=2, max_summary_tokens=20)
    assert len(summary) <= 20 * 4 + 40  # budget (chars) + prefix slack


def test_trim_recent_messages_keeps_two_per_pair():
    msgs = [{"role": "system", "content": "s"}]
    for i in range(10):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.append({"role": "assistant", "content": f"a{i}"})
    trimmed = mem.trim_recent_messages(msgs, recent_turn_pairs=3)
    assert len(trimmed) == 6
    assert all(m["role"] != "system" for m in trimmed)
    assert trimmed[-1] == {"role": "assistant", "content": "a9"}


def test_build_system_block_includes_sections():
    block = mem.build_system_block(
        "Base prompt.",
        language_policy="Begin every reply with <en> or <hi>.",
        language_hint="(Weak hint: user looked like Hindi.)",
        pinned_facts=["User wants low latency."],
        running_summary="Earlier the user discussed voice config.",
        recall_snippets=["use hindi"],
    )
    assert "Base prompt." in block
    assert "<en> or <hi>" in block
    assert "Weak hint" in block
    assert "User wants low latency." in block
    assert "Earlier the user discussed voice config." in block
    assert "use hindi" in block
