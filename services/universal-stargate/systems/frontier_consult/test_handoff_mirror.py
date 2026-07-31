"""Tests for handoff workspaces→cortex auto-mirror (life surface / a23964)."""

from __future__ import annotations

from pathlib import Path

from systems.frontier_consult.handoff import build_pointer_body
from systems.frontier_consult.handoff_life_mirror import (
    mirror_packet_file_to_cortex,
    mirror_workspaces_pointers_for_web,
    packet_mirror_uri,
)


def test_mirror_rewrites_workspaces_pointer_without_io() -> None:
    text = "See workspaces://universal-llm-gateway/tasks/specs/foo.md for context."
    updated, rewrites = mirror_workspaces_pointers_for_web(
        text,
        thread_id="4976",
    )
    assert rewrites
    assert "workspaces://" not in updated
    assert updated.startswith(
        "See cortex://ephemeral/handoffs/4976-tasks-specs-foo.md"
    )


def test_mirror_with_io_copies_and_rewrites() -> None:
    ws = "workspaces://universal-llm-gateway/tasks/specs/foo.md"
    store: dict[str, str] = {}

    def read_ws(uri: str) -> str:
        assert uri == ws
        return "# foo"

    def write_cortex(uri: str, body: str) -> None:
        store[uri] = body

    updated, rewrites = mirror_workspaces_pointers_for_web(
        f"Load {ws}",
        thread_id="4976",
        read_workspaces=read_ws,
        write_cortex=write_cortex,
    )
    assert rewrites == [(ws, "cortex://ephemeral/handoffs/4976-tasks-specs-foo.md")]
    assert "cortex://ephemeral/handoffs/4976-tasks-specs-foo.md" in updated
    assert store


def test_packet_mirror_uri_uses_stem() -> None:
    uri = packet_mirror_uri("tmp/reviews/agent-bus-api-ergonomics-fable-consult-packet.md")
    assert uri == (
        "cortex://ephemeral/handoffs/"
        "agent-bus-api-ergonomics-fable-consult-packet.md"
    )


def test_mirror_packet_file_writes_cortex(tmp_path: Path) -> None:
    packet = tmp_path / "packet.md"
    packet.write_text("<scope>x</scope>\n", encoding="utf-8")
    cortex_root = tmp_path / "cortex"
    uri = mirror_packet_file_to_cortex(
        packet,
        packet_path="tmp/reviews/packet.md",
        cortex_root=cortex_root,
    )
    assert uri == "cortex://ephemeral/handoffs/packet.md"
    dest = cortex_root / "ephemeral/handoffs/packet.md"
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == "<scope>x</scope>\n"


def test_build_pointer_web_uses_cortex_read() -> None:
    body = build_pointer_body(
        request_id="req-ptr-web-mirror",
        packet_path="cortex://ephemeral/handoffs/foo-packet.md",
        subject="review topic",
        pointer_body=None,
        handoff_contract="consult",
        to_agent="claude-web",
    )
    assert 'path="cortex://ephemeral/handoffs/foo-packet.md"' in body
    assert 'sandbox="workspaces"' not in body
    assert "life surface" in body


def test_build_pointer_web_anthropic_alias_uses_cortex_read() -> None:
    """Admission posts as web-anthropic; mirror gate must still fire (a24046)."""
    body = build_pointer_body(
        request_id="req-ptr-web-anthropic",
        packet_path="tmp/reviews/friction-24001-fable-skill-recipe-packet.md",
        subject="Fable P0 — friction 24001",
        pointer_body=None,
        handoff_contract="consult",
        to_agent="web-anthropic",
    )
    assert 'sandbox="workspaces"' not in body
    assert "cortex://" in body
    assert "life surface" in body
    assert "friction-24001-fable-skill-recipe-packet" in body


def test_is_life_web_receiver_aliases() -> None:
    from systems.frontier_consult.handoff_life_mirror import is_life_web_receiver

    assert is_life_web_receiver("claude-web")
    assert is_life_web_receiver("web-anthropic")
    assert is_life_web_receiver("web")
    assert is_life_web_receiver("web-claude")
    assert not is_life_web_receiver("claude-cursor")
    assert not is_life_web_receiver("cursor")
    assert not is_life_web_receiver(None)


def test_build_pointer_cursor_keeps_workspaces_read() -> None:
    body = build_pointer_body(
        request_id="req-ptr-cursor-ws",
        packet_path="tmp/reviews/foo.md",
        subject="review topic",
        pointer_body=None,
        handoff_contract="consult",
        to_agent="claude-cursor",
    )
    assert 'sandbox="workspaces"' in body
    assert 'path="tmp/reviews/foo.md"' in body
