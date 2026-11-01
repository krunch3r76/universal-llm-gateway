"""Tests for logical boot identity registry and resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_seat.boot_identity import (
    BootIdentity,
    BootIdentityConfigError,
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
    assert ident.logical_seat_id == "seat:alpha-work"
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
    ident = resolve_boot_identity_for_seat("beta")
    assert isinstance(ident, BootIdentity)
    assert ident.logical_seat_id == "seat:beta-work"


def test_registry_no_cache_sequence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "boot-seats.yaml"
    cfg.write_text(
        """
schema_version: 1
seats:
  alpha-work:
    aliases: [alpha]
    family: gpt
    platform: web
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CORTEX_BOOT_IDENTITIES_PATH", str(cfg))
    assert isinstance(resolve_boot_identity_for_seat("alpha"), BootIdentity)
    cfg.write_text(
        "schema_version: 1\nseats:\n  Alpha_Work:\n    family: gpt\n    platform: web\n",
        encoding="utf-8",
    )
    err = resolve_boot_identity_for_seat("alpha")
    assert err.get("reason") == "noncanonical_key"
    cfg.unlink()
    missing = resolve_boot_identity_for_seat("alpha")
    assert missing.get("reason") == "missing_configured_file"
    cfg.write_text(
        """
schema_version: 1
seats:
  alpha-work:
    family: gpt
    platform: web
""".strip(),
        encoding="utf-8",
    )
    unresolved = resolve_boot_identity_for_seat("alpha")
    assert unresolved.get("reason") == "seat_unresolved"


@pytest.mark.parametrize(
    ("yaml_text", "code"),
    [
        ("schema_version: 1\nseats:\n  alpha-work:\n    family: gpt\n    platform: web\n  alpha-work:\n    family: claude\n    platform: cursor\n", "duplicate_yaml_key"),
        ("schema_version: 1\nprincipals: {}\nseats: {}\n", "unknown_fields"),
        ("schema_version: 1\nseats:\n  Alpha_Work:\n    family: gpt\n    platform: web\n", "noncanonical_key"),
        ("schema_version: 1\nseats:\n  inspect-alpha:\n    family: gpt\n    platform: web\n", "reserved_key"),
        ("schema_version: 1\nseats:\n  alpha-work:\n    family: gpt\n    platform: web\n    aliases: [\"\"]\n", "malformed_aliases"),
        ("schema_version: 1\nseats:\n  web:\n    family: gpt\n    platform: web\n", "alias_collision"),
        ("schema_version: 1\nseats:\n  gpt:\n    family: gpt\n    platform: web\n", "alias_collision"),
    ],
)
def test_registry_loader_rejects(tmp_path: Path, yaml_text: str, code: str) -> None:
    cfg = tmp_path / "boot-seats.yaml"
    cfg.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(BootIdentityConfigError) as exc:
        load_boot_identity_registry(cfg)
    assert exc.value.code == code


@pytest.mark.parametrize(
    ("yaml_text", "code"),
    [
        ("schema_version: 1\nseats:\n  alpha-work:\n    family: gpt\n    platform: web\n    house: null\n", "bad_field_type"),
        ("schema_version: 1\nseats:\n  alpha-work:\n    family: gpt\n    platform: web\n    surface: null\n", "bad_field_type"),
        ("schema_version: 1\nseats:\n  alpha-work:\n    family: gpt\n    platform: web\n    inbox_recipient: null\n", "bad_field_type"),
        ("schema_version: 1\nseats:\n  alpha-work:\n    family: gpt\n    platform: web\n    aliases: null\n", "malformed_aliases"),
        ("schema_version: 1\nseats:\n  alpha-work:\n    family: gpt\n    platform: web\n    aliases: {}\n", "malformed_aliases"),
        ("schema_version: true\nseats: {}\n", "unsupported_schema_version"),
        ("null\n", "malformed_yaml"),
    ],
)
def test_registry_optional_and_root_strictness(
    tmp_path: Path, yaml_text: str, code: str
) -> None:
    cfg = tmp_path / "boot-seats.yaml"
    cfg.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(BootIdentityConfigError) as exc:
        load_boot_identity_registry(cfg)
    assert exc.value.code == code


def test_registry_invalid_utf8_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "boot-seats.yaml"
    cfg.write_bytes(b"schema_version: 1\nseats:\n  \xff\xfe\n")
    with pytest.raises(BootIdentityConfigError) as exc:
        load_boot_identity_registry(cfg)
    assert exc.value.code == "invalid_utf8"


def test_registry_immutable_after_load(tmp_path: Path) -> None:
    cfg = tmp_path / "boot-seats.yaml"
    cfg.write_text(
        """
schema_version: 1
seats:
  alpha-work:
    family: gpt
    platform: web
""".strip(),
        encoding="utf-8",
    )
    reg = load_boot_identity_registry(cfg)
    with pytest.raises(TypeError):
        reg.by_alias["alpha-work"] = "beta-work"  # type: ignore[index]


def test_gpt_web_capability_non_lead_no_dispatch_grant() -> None:
    from agent_seat.profiles import get_profile, load_lead_agent_slugs

    profile = get_profile("gpt", "web")
    assert profile.api_dispatchable is False
    assert profile.auto_dispatchable is False
    assert profile.manual_handoff is False
    assert "gpt-web" not in load_lead_agent_slugs()
