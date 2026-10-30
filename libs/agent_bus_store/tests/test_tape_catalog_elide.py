"""Tests for IDE catalog elision before tape budget measurement."""

from __future__ import annotations

import pytest
from agent_bus_store.tape_catalog_elide import (
    elide_ide_catalog_blocks,
    excise_turn_one_resume_bootstrap,
    exclude_ide_catalog_turns,
    is_resume_hop_bootstrap_user_content,
)
from agent_bus_store.tape_degrade import payload_bytes
from agent_bus_store.tape_verbal import project_role_content_list

pytestmark = pytest.mark.offline


def test_elides_cursor_commands_outside_user_query() -> None:
    harness = "x" * 200
    content = (
        f"<cursor_commands>{harness}</cursor_commands>\n"
        "<user_query>implement the fix</user_query>"
    )
    messages = [{"role": "user", "content": content, "turn_index": 1}]
    out = elide_ide_catalog_blocks(messages)
    assert "[elided cursor_commands:" in out[0]["content"]
    assert "implement the fix" in out[0]["content"]
    assert harness not in out[0]["content"]


def test_keeps_cursor_commands_when_only_operator_text() -> None:
    content = "<cursor_commands>/checkpoint</cursor_commands>"
    messages = [{"role": "user", "content": content, "turn_index": 1}]
    out = elide_ide_catalog_blocks(messages)
    assert out[0]["content"] == content


def test_elision_runs_before_budget_decision() -> None:
    big = "y" * 4000
    content = (
        f"<manually_attached_skills>{big}</manually_attached_skills>\n"
        "<user_query>hi</user_query>"
    )
    messages = [{"role": "user", "content": content, "turn_index": 1}]
    raw_budget = payload_bytes(messages, [])
    elided = elide_ide_catalog_blocks(messages)
    elided_budget = payload_bytes(elided, [])
    assert elided_budget < raw_budget
    assert "[elided manually_attached_skills:" in elided[0]["content"]


def test_non_user_messages_unchanged() -> None:
    messages = [
        {"role": "assistant", "content": "<cursor_commands>x</cursor_commands>"}
    ]
    assert elide_ide_catalog_blocks(messages) == messages


def test_block_inside_user_query_kept_after_earlier_elision() -> None:
    """A preceding elision must not shift the user_query guard off its span."""
    harness = "x" * 500
    content = (
        f"<cursor_commands>{harness}</cursor_commands>\n"
        "<user_query>keep <dynamic_tool_catalog>inner</dynamic_tool_catalog> tail"
        "</user_query>"
    )
    out = elide_ide_catalog_blocks([{"role": "user", "content": content}])
    assert harness not in out[0]["content"]
    assert "<dynamic_tool_catalog>inner</dynamic_tool_catalog>" in out[0]["content"]


def test_second_block_after_elision_still_elided() -> None:
    content = (
        "<cursor_commands>a</cursor_commands>"
        "<dynamic_tool_catalog>b</dynamic_tool_catalog>"
        "<user_query>q</user_query>"
    )
    out = elide_ide_catalog_blocks([{"role": "user", "content": content}])
    assert "[elided cursor_commands:" in out[0]["content"]
    assert "[elided dynamic_tool_catalog:" in out[0]["content"]
    assert "<user_query>q</user_query>" in out[0]["content"]


def test_every_user_query_span_is_protected() -> None:
    content = (
        "<user_query></user_query>\n"
        "<manually_attached_skills>SKILL</manually_attached_skills>\n"
        "<user_query>second <open_and_recently_viewed_files>f"
        "</open_and_recently_viewed_files></user_query>"
    )
    out = elide_ide_catalog_blocks([{"role": "user", "content": content}])
    assert "SKILL" not in out[0]["content"]
    assert (
        "<open_and_recently_viewed_files>f</open_and_recently_viewed_files>"
        in (out[0]["content"])
    )


def test_unknown_tag_passes_through() -> None:
    content = "<foo_catalog>zzz</foo_catalog><user_query>q</user_query>"
    assert (
        elide_ide_catalog_blocks([{"role": "user", "content": content}])[0]["content"]
        == content
    )


def _catalog_block(tag: str, total_bytes: int) -> str:
    open_tag = f"<{tag}>"
    close_tag = f"</{tag}>"
    inner = total_bytes - len(open_tag.encode()) - len(close_tag.encode())
    return open_tag + ("x" * inner) + close_tag


def _catalog_turn_95d4aea6_shaped() -> str:
    """Sibling blocks sized like transcript 95d4aea6's catalog user message.

    Observed shape: ``available_subagent_types`` 3258 B, a blank line,
    ``available_subagent_models`` 1175 B, a blank line, ``dynamic_tools``
    9142 B. No ``<user_query>``. UTF-8 length 13579.
    """
    text = "\n\n".join(
        (
            _catalog_block("available_subagent_types", 3258),
            _catalog_block("available_subagent_models", 1175),
            _catalog_block("dynamic_tools", 9142),
        )
    )
    assert len(text.encode("utf-8")) == 13579
    return text


def test_95d4aea6_shaped_catalog_turn_excluded_from_verbal_tape() -> None:
    catalog = _catalog_turn_95d4aea6_shaped()
    assert "<user_query>" not in catalog
    messages = [
        {"role": "user", "content": catalog, "turn_index": 1},
        {
            "role": "user",
            "content": "<user_query>resume the house</user_query>",
            "turn_index": 2,
        },
        {"role": "assistant", "content": "picked up", "turn_index": 2},
    ]
    kept = exclude_ide_catalog_turns(messages)
    verbal = project_role_content_list(kept)
    assert catalog not in [row["content"] for row in verbal]
    assert all("available_subagent_types" not in row["content"] for row in verbal)
    assert [row["content"] for row in verbal] == [
        "<user_query>resume the house</user_query>",
        "picked up",
    ]
    assert payload_bytes(kept, []) < payload_bytes(messages, [])


def test_catalog_turn_with_substantive_user_query_kept() -> None:
    content = (
        _catalog_turn_95d4aea6_shaped() + "\n<user_query>resume</user_query>"
    )
    messages = [{"role": "user", "content": content}]
    assert exclude_ide_catalog_turns(messages) == messages


def test_catalog_blocks_plus_operator_prose_kept() -> None:
    content = _catalog_turn_95d4aea6_shaped() + "\n\nplease resume"
    messages = [{"role": "user", "content": content}]
    assert exclude_ide_catalog_turns(messages) == messages


def test_cursor_commands_only_is_not_a_catalog_turn() -> None:
    messages = [{"role": "user", "content": "<cursor_commands>/checkpoint</cursor_commands>"}]
    assert exclude_ide_catalog_turns(messages) == messages


def test_dynamic_tool_catalog_only_turn_excluded() -> None:
    content = "<dynamic_tool_catalog>" + ("z" * 100) + "</dynamic_tool_catalog>"
    messages = [{"role": "user", "content": content}]
    assert exclude_ide_catalog_turns(messages) == []


def _mini_liaison_hop_block() -> str:
    return (
        "Liaison IDE hop (attended register) tip_cp=17. LOAD skill.\n"
        "HOP: hop now.\n"
        "OPERATOR LOOP: close with a situation report to the operator. "
        "A finished watch with no next leg is not a situation report."
    )


def _mini_operator_proxy_preamble() -> str:
    return (
        "# Hop on agent-bus:12286: you are the operator seat\n"
        "Use the cdp-operator-proxy skill at trigger.\n\n"
        "## CURRENT LEG (rendered checkpoint)\n"
        "handoff sha256 abc\n"
        "### OWED / NEXT\n"
        "fixture owed\n"
        "## Lessons in force\n"
        "(none)\n"
        "tail speech"
    )


def test_elides_liaison_hop_bootstrap_outside_user_query() -> None:
    content = _mini_liaison_hop_block() + "\n<user_query>real work</user_query>"
    out = elide_ide_catalog_blocks([{"role": "user", "content": content}])[0]["content"]
    assert "[elided liaison_hop_bootstrap:" in out
    assert "Liaison IDE hop" not in out
    assert "real work" in out


def test_elides_liaison_hop_bootstrap_inside_user_query() -> None:
    hop = _mini_liaison_hop_block()
    content = f"<user_query>{hop}\nreal work</user_query>"
    out = elide_ide_catalog_blocks([{"role": "user", "content": content}])[0]["content"]
    assert "[elided liaison_hop_bootstrap:" in out
    assert "real work" in out


def test_elides_operator_proxy_hop_preamble() -> None:
    content = _mini_operator_proxy_preamble()
    out = elide_ide_catalog_blocks([{"role": "user", "content": content}])[0]["content"]
    assert "[elided operator_proxy_hop_preamble:" in out
    assert "## CURRENT LEG" not in out
    assert "tail speech" in out


def test_operator_proxy_elision_preserves_leading_operator_line() -> None:
    content = (
        "do not touch prod\n"
        + _mini_operator_proxy_preamble()
    )
    out = elide_ide_catalog_blocks([{"role": "user", "content": content}])[0]["content"]
    assert out.startswith("do not touch prod\n")
    assert "[elided operator_proxy_hop_preamble:" in out
    assert "tail speech" in out


@pytest.mark.parametrize(
    "following",
    [
        "**substantive work**",
        "## Section heading",
        "- list item",
        "`inline code`",
    ],
)
def test_rule3_ceremony_elision_stops_before_substantive_paragraph(following: str) -> None:
    from agent_bus_store.tape_catalog_elide import _elide_rule3_ceremony_spans

    content = f"Where we left off: recap line.\n\n{following}\n"
    out = _elide_rule3_ceremony_spans(content)
    assert following in out
    assert "Where we left off" not in out
    assert "[elided rule3_resume_ceremony:" in out


def test_elides_k44_predecessor_paste() -> None:
    content = "prefix\nK44 from predecessor \nlong paste " + ("x" * 200)
    out = elide_ide_catalog_blocks([{"role": "user", "content": content}])[0]["content"]
    assert "[elided k44_predecessor_paste:" in out
    assert "long paste" not in out
    assert content.startswith("prefix")


def test_k44_elision_stops_before_operator_text() -> None:
    content = (
        "prefix\nK44 from predecessor \nlong paste\n\n"
        "operator speech after k44 must survive"
    )
    out = elide_ide_catalog_blocks([{"role": "user", "content": content}])[0]["content"]
    assert "[elided k44_predecessor_paste:" in out
    assert "operator speech after k44 must survive" in out
    assert "long paste" not in out


def test_overlapping_handoff_spans_preserve_prefix() -> None:
    """Overlapping K44/hop spans must not swallow operator prefix text."""
    prefix = "operator speech keep this prefix intact\n"
    k44_tail = "K44 from predecessor \nhandoff body " + ("p" * 80) + "\n"
    hop = _mini_liaison_hop_block()
    content = prefix + k44_tail + hop
    out = elide_ide_catalog_blocks([{"role": "user", "content": content}])[0]["content"]
    assert out.startswith(prefix)
    assert "keep this prefix intact" in out
    assert "Liaison IDE hop" not in out
    assert "handoff body" not in out
    marker_count = out.count("[elided ")
    assert marker_count >= 1
    assert (
        "[elided k44_predecessor_paste:" in out
        or "[elided liaison_hop_bootstrap:" in out
    )


def test_k44_starting_inside_hop_span_preserves_outer_prefix() -> None:
    prefix = "outer operator prefix " + ("x" * 180) + "\n"
    assert len(prefix.encode("utf-8")) >= 200
    hop = (
        "Liaison IDE hop (attended) tip_cp=1.\n"
        "K44 from predecessor \ninner paste line\n"
        "OPERATOR LOOP: close with a situation report to the operator. "
        "A finished watch with no next leg is not a situation report."
    )
    content = prefix + hop
    out = elide_ide_catalog_blocks([{"role": "user", "content": content}])[0]["content"]
    assert out.startswith(prefix)
    assert "outer operator prefix" in out
    assert "inner paste line" not in out


def test_bare_hop_on_agent_bus_line_is_not_bootstrap() -> None:
    content = "# Hop on agent-bus:12286: you are the operator seat\n<user_query>ask</user_query>"
    assert not is_resume_hop_bootstrap_user_content(content)


def test_excise_turn_one_preserves_user_query_after_bootstrap() -> None:
    tid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    hop = _mini_liaison_hop_block()
    messages = [
        {
            "role": "user",
            "content": f"{hop}\n<user_query>keep this ask</user_query>",
            "transcript_id": tid,
            "turn_index": 1,
        },
        {
            "role": "user",
            "content": "<user_query>turn two</user_query>",
            "transcript_id": tid,
            "turn_index": 2,
        },
    ]
    out = excise_turn_one_resume_bootstrap(messages)
    assert any("keep this ask" in str(m.get("content") or "") for m in out)
    assert "[elided liaison_hop_bootstrap:" in str(out[0].get("content") or "")


def test_excise_turn_one_keeps_assistant_work_beyond_ceremony() -> None:
    tid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    messages = [
        {
            "role": "user",
            "content": _mini_liaison_hop_block(),
            "transcript_id": tid,
            "turn_index": 1,
        },
        {
            "role": "assistant",
            "content": "Where we left off: recap.\n\nShipped the fix in turn one.",
            "transcript_id": tid,
            "turn_index": 1,
        },
        {
            "role": "user",
            "content": "<user_query>turn two</user_query>",
            "transcript_id": tid,
            "turn_index": 2,
        },
    ]
    out = excise_turn_one_resume_bootstrap(messages)
    assistant = next(
        m for m in out if m.get("role") == "assistant" and int(m.get("turn_index") or 0) == 1
    )
    assert "Shipped the fix in turn one." in str(assistant.get("content") or "")
    assert "Where we left off" not in str(assistant.get("content") or "")


def test_subagent_tags_elided_outside_user_query() -> None:
    content = (
        "<available_subagent_types>types</available_subagent_types>"
        "<available_subagent_models>models</available_subagent_models>"
        "<user_query>go</user_query>"
    )
    out = elide_ide_catalog_blocks([{"role": "user", "content": content}])
    body = out[0]["content"]
    assert "<available_subagent_types>" not in body
    assert "<available_subagent_models>" not in body
    assert "[elided available_subagent_types:" in body
    assert "go" in body
