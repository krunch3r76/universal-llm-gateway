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


def test_registry_alias_resolves_logical_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_registry_no_cache_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        (
            "schema_version: 1\nseats:\n  alpha-work:\n    family: gpt\n    platform: web\n  alpha-work:\n    family: claude\n    platform: cursor\n",
            "duplicate_yaml_key",
        ),
        ("schema_version: 1\nprincipals: {}\nseats: {}\n", "unknown_fields"),
        (
            "schema_version: 1\nseats:\n  Alpha_Work:\n    family: gpt\n    platform: web\n",
            "noncanonical_key",
        ),
        (
            "schema_version: 1\nseats:\n  inspect-alpha:\n    family: gpt\n    platform: web\n",
            "reserved_key",
        ),
        (
            'schema_version: 1\nseats:\n  alpha-work:\n    family: gpt\n    platform: web\n    aliases: [""]\n',
            "malformed_aliases",
        ),
        (
            "schema_version: 1\nseats:\n  web:\n    family: gpt\n    platform: web\n",
            "alias_collision",
        ),
        (
            "schema_version: 1\nseats:\n  gpt:\n    family: gpt\n    platform: web\n",
            "alias_collision",
        ),
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
        (
            "schema_version: 1\nseats:\n  alpha-work:\n    family: gpt\n    platform: web\n    house: null\n",
            "bad_field_type",
        ),
        (
            "schema_version: 1\nseats:\n  alpha-work:\n    family: gpt\n    platform: web\n    surface: null\n",
            "bad_field_type",
        ),
        (
            "schema_version: 1\nseats:\n  alpha-work:\n    family: gpt\n    platform: web\n    inbox_recipient: null\n",
            "bad_field_type",
        ),
        (
            "schema_version: 1\nseats:\n  alpha-work:\n    family: gpt\n    platform: web\n    aliases: null\n",
            "malformed_aliases",
        ),
        (
            "schema_version: 1\nseats:\n  alpha-work:\n    family: gpt\n    platform: web\n    aliases: {}\n",
            "malformed_aliases",
        ),
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
    from agent_seat.profiles import (
        get_profile,
        load_lead_agent_slugs,
        seat_capabilities,
    )
    from agent_seat.registry import is_lead_agent

    profile = get_profile("gpt", "web")
    assert profile.api_dispatchable is False
    assert profile.auto_dispatchable is False
    assert profile.manual_handoff is False
    assert profile.admits_generate() is False
    assert profile.admits_handoff() is False
    assert "gpt-web" not in load_lead_agent_slugs()
    assert is_lead_agent("gpt-web") is False
    assert seat_capabilities(profile) == frozenset(
        {"mcp_life", "mcp_code", "local_fs_write"}
    )


def _write_registry(tmp_path: Path, yaml_text: str) -> Path:
    cfg = tmp_path / "boot-seats.yaml"
    cfg.write_text(yaml_text.strip() + "\n", encoding="utf-8")
    return cfg


def test_registry_duplicate_field_and_root_keys(tmp_path: Path) -> None:
    field_dup = """
schema_version: 1
seats:
  alpha-work:
    family: gpt
    family: claude
    platform: web
"""
    root_dup = """
schema_version: 1
schema_version: 2
seats: {}
"""
    for text in (field_dup, root_dup):
        with pytest.raises(BootIdentityConfigError) as exc:
            load_boot_identity_registry(_write_registry(tmp_path, text))
        assert exc.value.code == "duplicate_yaml_key"


@pytest.mark.parametrize(
    ("yaml_text", "code"),
    [
        (
            """
schema_version: 1
seats:
  on:
    family: gpt
    platform: web
""",
            "noncanonical_key",
        ),
        (
            """
schema_version: 1
seats:
  null:
    family: gpt
    platform: web
""",
            "noncanonical_key",
        ),
        (
            """
schema_version: 1
seats:
  ? [not, hashable]
  : {family: gpt, platform: web}
""",
            "bad_yaml_key",
        ),
        (
            """
schema_version: 1
? {nested: key}
: 1
seats: {}
""",
            "bad_yaml_key",
        ),
        ("42\n", "malformed_yaml"),
        ("- just-a-list\n", "malformed_yaml"),
        (
            """
schema_version: 1
seats:
  alpha-work:
    family: gpt
    platform: web
    aliases: [yes]
""",
            "malformed_aliases",
        ),
        (
            """
schema_version: 1
seats:
  alpha-work:
    family: gpt
    platform: web
    aliases: [null]
""",
            "malformed_aliases",
        ),
        (
            """
schema_version: 1
seats:
  alpha-work:
    family: gpt
    platform: web
    aliases: [false]
""",
            "malformed_aliases",
        ),
        (
            """
schema_version: 1
seats:
  alpha-work:
    family: gpt
    platform: web
    aliases: [0]
""",
            "malformed_aliases",
        ),
        (
            """
schema_version: 1
seats:
  alpha-work:
    family: gpt
    platform: web
    aliases:
      - {name: mapped}
""",
            "malformed_aliases",
        ),
        (
            """
schema_version: 1
seats:
  alpha-work:
    family: gpt
    platform: web
    house: 21207
""",
            "bad_field_type",
        ),
        (
            """
schema_version: 1
seats:
  alpha-work:
    family: gpt
    platform: web
    inbox_recipient: 0
""",
            "bad_field_type",
        ),
        (
            """
schema_version: 1
seats:
  alpha-work:
    family: gpt
    platform: web
    1: mixed
""",
            "unknown_fields",
        ),
        (
            """
schema_version: false
seats: {}
""",
            "unsupported_schema_version",
        ),
    ],
)
def test_registry_strict_matrix(tmp_path: Path, yaml_text: str, code: str) -> None:
    with pytest.raises(BootIdentityConfigError) as exc:
        load_boot_identity_registry(_write_registry(tmp_path, yaml_text))
    assert exc.value.code == code


@pytest.mark.parametrize(
    "name",
    [
        "web",
        "grok",
        "superheavy",
        "cdp",
        "skeptic",
        "gpt",
        "web-openai",
        "grok-bot-gc1",
    ],
)
def test_registry_collision_classes(tmp_path: Path, name: str) -> None:
    text = f"""
schema_version: 1
seats:
  {name}:
    family: gpt
    platform: web
"""
    with pytest.raises(BootIdentityConfigError) as exc:
        load_boot_identity_registry(_write_registry(tmp_path, text))
    assert exc.value.code == "alias_collision"


def test_registry_missing_unreadable_and_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "absent.yaml"
    with pytest.raises(BootIdentityConfigError) as missing_exc:
        load_boot_identity_registry(missing)
    assert missing_exc.value.code == "missing_configured_file"

    with pytest.raises(BootIdentityConfigError) as traversal_exc:
        load_boot_identity_registry(Path("../x.yaml"))
    assert traversal_exc.value.code == "path_traversal"

    cfg = _write_registry(
        tmp_path,
        """
schema_version: 1
seats: {}
""",
    )
    original = Path.read_text

    def _deny(self: Path, *args: object, **kwargs: object) -> str:
        if Path(self) == cfg:
            raise OSError("denied")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _deny)
    with pytest.raises(BootIdentityConfigError) as unread_exc:
        load_boot_identity_registry(cfg)
    assert unread_exc.value.code == "unreadable_configured_file"


def test_registry_and_identity_are_immutable(tmp_path: Path) -> None:
    reg = load_boot_identity_registry(
        _write_registry(
            tmp_path,
            """
schema_version: 1
seats:
  alpha-work:
    family: gpt
    platform: web
""",
        )
    )
    with pytest.raises(TypeError):
        reg.by_alias["alpha-work"] = "other"  # type: ignore[index]
    with pytest.raises(TypeError):
        reg.seats["beta-work"] = reg.seats["alpha-work"]  # type: ignore[index]
    ident = reg.lookup("alpha-work")
    assert isinstance(ident, BootIdentity)
    with pytest.raises(AttributeError):
        ident.agent = "mutated"  # type: ignore[misc]
