"""Tests for shared Cowork/CDP harvest chrome helpers."""

from __future__ import annotations

from chat_harvest.chrome import (
    RELAY_ENVELOPE_SUBJECT_RE,
    is_chrome_only,
    is_failed_relay_envelope_subject,
    is_prompt_echo,
    is_relay_envelope_subject,
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


def test_strip_chrome_drops_tool_badges_including_loaded_tools() -> None:
    text = (
        "Used toys integration, used 3 skills, loaded tools\n\n"
        "Actual answer paragraph."
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


def test_is_failed_relay_envelope_subject() -> None:
    assert is_failed_relay_envelope_subject("cdp FAILED — abc12345")
    assert is_failed_relay_envelope_subject("cdp UNVERIFIED — abc12345")
    assert not is_failed_relay_envelope_subject("cdp reply — abc12345")
    assert is_relay_envelope_subject("cdp reply — abc12345")
