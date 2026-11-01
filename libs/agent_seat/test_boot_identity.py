"""Tests for logical boot identity registry and resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_seat.boot_identity import (
    BootIdentity,
    BootIdentityConfigError,
    clear_boot_identity_registry_cache,
    legacy_boot_identity,
    load_boot_identity_registry,
    resolve_boot_identity_for_seat,
)
from agent_seat.registry import normalize_bus_address


def test_legacy_boot_identity_matches_capability_cell() -> None:
    ident = legacy_boot_identity("claude", "web")
    assert ident.agent == "claude-web"
    assert ident.session_prefix == normalize_bus_address("claude-web")
    assert ident.inbox_recipient == "claude-web"
    assert ident.self_entity_id == "family:claude"
    assert not ident.is_logical


def test_resolve_legacy_cursor_unchanged() -> None:
    ident = resolve_boot_identity_for_seat("cursor")
    assert isinstance(ident, BootIdentity)
    assert ident.family == "claude"
    assert ident.platform == "cursor"


def test_resolve_grok_bot_legacy() -> None:
    ident = resolve_boot_identity_for_seat("grok-bot")
    assert ident.family == "grok"
    assert ident.platform == "bot"
    assert ident.agent == "grok-bot"


def test_registry_alias_resolves_logical_seat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "boot-seats.yaml"
    cfg.write_text(
        """
schema_version: 1
seats:
  alpha-work:
    aliases: [alpha]
    family: gpt
    platform: web
    house: "9999"
    surface: chatgpt
    inbox_recipient: alpha-work
""".strip(),
        encoding="utf-8",
    )
    loaded = load_boot_identity_registry(cfg)
    ident = loaded.lookup("alpha")
    assert ident is not None
    assert ident.logical_seat_id == "alpha-work"
    assert ident.agent == "alpha-work"
    assert ident.self_entity_id == "seat:alpha-work"
    assert ident.inbox_recipient == "alpha-work"


def test_registry_unknown_field_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "boot-seats.yaml"
    cfg.write_text(
        """
schema_version: 1
seats:
  alpha-work:
    family: gpt
    platform: web
    secret_grant: true
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(BootIdentityConfigError) as exc:
        load_boot_identity_registry(cfg)
    assert exc.value.code == "unknown_fields"


def test_malformed_registry_does_not_break_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = tmp_path / "boot-seats.yaml"
    cfg.write_text("schema_version: 2\nseats: {}\n", encoding="utf-8")
    monkeypatch.setenv("CORTEX_BOOT_IDENTITIES_PATH", str(cfg))
    clear_boot_identity_registry_cache()
    ident = resolve_boot_identity_for_seat("web-anthropic")
    assert ident.agent == "claude-web"
    err = resolve_boot_identity_for_seat("not-a-registered-logical-seat")
    assert err.get("reason") == "unsupported_schema_version"


def test_env_registry_lookup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "boot-seats.yaml"
    cfg.write_text(
        """
schema_version: 1
seats:
  beta-work:
    aliases: [beta]
    family: gpt
    platform: web
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CORTEX_BOOT_IDENTITIES_PATH", str(cfg))
    clear_boot_identity_registry_cache()
    ident = resolve_boot_identity_for_seat("beta")
    assert isinstance(ident, BootIdentity)
    assert ident.logical_seat_id == "beta-work"
