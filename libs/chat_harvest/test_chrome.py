"""Tests for shared Cowork/CDP harvest chrome helpers."""

from __future__ import annotations

import pytest

from chat_harvest.chrome import (
    RELAY_ENVELOPE_SUBJECT_RE,
    badge_scrape_change_key,
    ends_with_tool_row,
    is_chrome_only,
    is_failed_relay_envelope_subject,
    is_prompt_echo,
    is_relay_envelope_subject,
    is_tool_status_body,
    strip_chrome,
    substantive_reply_body,
)

# Specimen #346 — streaming tool-badge stub; must NOT satisfy proof_reply_from.
SPECIMEN_346_BODY = """\
# CDP generate result (fable-5.1-high)

- execution_id: `a76a67d3-8fba-46f8-93d7-058e29288104`
- satellite_execution_id: `93f3d511a30e4de08a1de65bf6320c0e`
- substrate: `web-anthropic-cdp`
- cost_source: `unavailable`
- archive_uri: `cortex://notes/system/threads/cdp-ask-archive-cdp-recover-93f3d511a30e4de08a1de65bf6320c0e.md`

Used toys integration, used 3 skills, loaded tools
Used toys integration, used 3 skills, loaded tools
"""

# Specimen #347 — substantive reply after envelope metadata.
SPECIMEN_347_BODY = """\
# CDP generate result (fable-5.1-high)

- execution_id: `b87b78e4-9gcb-57g9-a4e8-04f4e40399215`
- satellite_execution_id: `a4e4e622b41f5ef19c2g7cg7431d1f`
- substrate: `web-anthropic-cdp`
- cost_source: `unavailable`
- archive_uri: `cortex://notes/system/threads/cdp-ask-archive-cdp-recover-a4e4e622.md`

Bind complete. F1 = merge wins on the sidecar question; proceed with the
implementation plan as written.
"""


def test_specimen_346_is_chrome_only() -> None:
    assert is_chrome_only(SPECIMEN_346_BODY)
    assert substantive_reply_body(SPECIMEN_346_BODY) == ""


def test_specimen_347_has_substantive_prose() -> None:
    assert not is_chrome_only(SPECIMEN_347_BODY)
    prose = substantive_reply_body(SPECIMEN_347_BODY)
    assert "Bind complete" in prose
    assert "implementation plan" in prose


# 12887 turn 27 — Cowork progress badges posted as a cdp reply. Not a closeout.
SPECIMEN_12887_PROGRESS_BODY = """\
# CDP generate result (fable-5.1-high)

- execution_id: `260e806d-9782-4cd9-8697-2f7ae349787a`
- satellite_execution_id: `eeb3c8bff4494bc0bd911db7f193444c`
- substrate: `web-anthropic-cdp`
- cost_source: `unavailable`
- archive_uri: `cortex://notes/system/threads/cdp-ask-archive-cdp-fable-eeb3c8bff4494bc0bd911db7f193444c.md`

Ran a command, loaded tools
\ue027
Ran a command, loaded tools
\ue056
\ue0e4
\ue0fb
\ue0f9
just now
"""


def test_specimen_12887_progress_is_chrome_only() -> None:
    assert is_chrome_only(SPECIMEN_12887_PROGRESS_BODY)
    assert substantive_reply_body(SPECIMEN_12887_PROGRESS_BODY) == ""


def test_strip_chrome_drops_tool_badges_including_loaded_tools() -> None:
    text = (
        "Used toys integration, used 3 skills, loaded tools\n\nActual answer paragraph."
    )
    assert strip_chrome(text) == "Actual answer paragraph."


def test_strip_chrome_drops_responded_label() -> None:
    text = "Claude responded: hello\n\nReal reply."
    assert strip_chrome(text) == "Real reply."


def test_is_prompt_echo() -> None:
    assert is_prompt_echo("You said: /reasoning-posture\n\n/reasoning-posture")
    assert not is_prompt_echo("Here is the analysis.")


def test_relay_envelope_subject_re() -> None:
    assert RELAY_ENVELOPE_SUBJECT_RE.match("cdp reply — a76a67d3")
    assert RELAY_ENVELOPE_SUBJECT_RE.match("cdp FAILED — a76a67d3")
    assert RELAY_ENVELOPE_SUBJECT_RE.match("cdp UNVERIFIED — a76a67d3")
    assert not RELAY_ENVELOPE_SUBJECT_RE.match("re: handoff")


# Phrase-list preamble from archive 4089d9a6 (badge line twice, glyph, timestamp).
ARCHIVE_BADGE_PREAMBLE = """\
Used toys integration, loaded tools
\ue027
Used toys integration, loaded tools
just now
"""

# a:37034 line. Outside TOOL_BADGE_LINE_RE (no "updated tasks" / "loaded a skill").
UPDATED_TASKS_PREAMBLE = """\
Updated tasks, loaded tools, loaded a skill
\ue027
Updated tasks, loaded tools, loaded a skill
just now
"""

OPEN_FORK_CONSULT = """\
Question: where may harvest completion become the deliverable?

OPEN FORK:
- DOM-WAIT
"""


def test_archive_badge_preamble_is_chrome_only_not_shape() -> None:
    assert is_chrome_only(ARCHIVE_BADGE_PREAMBLE)
    assert not is_tool_status_body(ARCHIVE_BADGE_PREAMBLE)


def test_shape_lines_outside_phrase_list_are_tool_status() -> None:
    browsed = "Browsed files, edited a note."
    edited_twice = "Edited a note\nEdited a note"
    assert not is_chrome_only(browsed)
    assert is_tool_status_body(browsed)
    assert is_tool_status_body(edited_twice)
    assert not is_chrome_only(UPDATED_TASKS_PREAMBLE)
    assert is_tool_status_body(UPDATED_TASKS_PREAMBLE)
    glyph_once = "Edited a note\n\ue027"
    assert is_tool_status_body(glyph_once)


def test_terse_prose_and_consult_are_not_tool_status() -> None:
    assert not is_tool_status_body("Fixed it, reran tests.")
    assert not is_tool_status_body("Done.")
    assert not is_tool_status_body(SPECIMEN_347_BODY)
    assert not is_tool_status_body(OPEN_FORK_CONSULT)
    assert not is_tool_status_body("Edited a note")


def test_badge_scrape_change_key_drops_timestamp_keeps_badge() -> None:
    same = "Browsed files, edited a note."
    other = "Updated files, edited a note."
    assert len(same) == len(other)
    stamped = f"{same}\njust now"
    ticked = f"{same}\n1 minute ago"
    rewritten = f"{other}\njust now"
    assert badge_scrape_change_key(stamped) == badge_scrape_change_key(ticked)
    assert badge_scrape_change_key(stamped) != badge_scrape_change_key(rewritten)
    labeled = f"Claude responded: Bound route\n{same}"
    assert badge_scrape_change_key(labeled) == badge_scrape_change_key(same)


def test_is_failed_relay_envelope_subject() -> None:
    assert is_failed_relay_envelope_subject("cdp FAILED — abc12345")
    assert is_failed_relay_envelope_subject("cdp UNVERIFIED — abc12345")
    assert not is_failed_relay_envelope_subject("cdp reply — abc12345")
    assert is_relay_envelope_subject("cdp reply — abc12345")


# a:37508 — doubled Cowork badges + timestamp around a dollar-sign command.
SPECIMEN_37508_CHROME = """\
Claude responded: VERDICT: Change
Used toys integration, loaded tools, loaded a skill
\ue027
Used toys integration, loaded tools, loaded a skill

VERDICT: Change

not `$ULG_REPO`: HOME="$(getent passwd)

3 minutes ago
"""


def test_strip_chrome_drops_37508_badges_and_timestamp() -> None:
    cleaned = strip_chrome(SPECIMEN_37508_CHROME)
    assert "Used toys integration" not in cleaned
    assert "3 minutes ago" not in cleaned
    assert "Claude responded:" not in cleaned
    assert "VERDICT: Change" in cleaned
    assert "$ULG_REPO" in cleaned


def test_strip_chrome_keeps_code_punctuation_lines() -> None:
    text = "```python\ndef f():\n    return 1\n}\n```\n---\n"
    cleaned = strip_chrome(text)
    assert "}" in cleaned
    assert "---" in cleaned


SPECIMEN_A36886_TAIL = """\
Some assistant prose here.

Loaded tools
Loaded tools
"""


def test_ends_with_tool_row_specimen_a36886() -> None:
    assert ends_with_tool_row(SPECIMEN_A36886_TAIL)


@pytest.mark.parametrize(
    "body",
    [
        "Used toys integration, ran 8 commands, loaded tools · 1 note\n"
        "Used toys integration, ran 8 commands, loaded tools · 1 note",
        "Listed scheduled tasks · 1 note\nListed scheduled tasks · 1 note",
        "Agent Bus\nAgent Bus",
        "Agent Bus\n\ue027\nAgent Bus\njust now",
    ],
)
def test_ends_with_tool_row_true_cases(body: str) -> None:
    assert ends_with_tool_row(body)


@pytest.mark.parametrize(
    "body",
    [
        "Listed scheduled tasks · 1 note",
        "Updated the spec.",
        "Updated the spec.\nUpdated the spec.",
        "Updated the spec, ran the tests.",
        "}\n}",
        "Thanks!\nThanks!",
        "Claude responded: Approved\nApproved",
    ],
)
def test_ends_with_tool_row_false_cases(body: str) -> None:
    assert not ends_with_tool_row(body)
