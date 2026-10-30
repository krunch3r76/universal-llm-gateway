"""Scope-membership merge: tracked prefixes union into the box scopes.

Hermetic tests monkeypatch the box path and the tracked path. They do not
read ``~/.gateway/rag.yaml`` and they do not open ``docs/research/``.
The prefix-coverage case is a fixture guard, not the live orphan census
(agent-bus 15851#1150).
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from services.rag.config import _loader as loader
from services.rag.search_scope.prefix_filter import matches_source_prefix

LLM_PREFIX = "/mnt/torus/projects/universal-llm-gateway/docs/research/llm/prompting"
SMALL_PREFIX = (
    "/mnt/torus/projects/universal-llm-gateway/docs/research/small_llm/prompting"
)
BOX_DESCRIPTION = "box prompting description"
ORPHAN_PATH = (
    "/mnt/torus/projects/universal-llm-gateway/docs/research/not-a-scope/orphan.pdf"
)


def _bind_paths(monkeypatch: pytest.MonkeyPatch, box: Path, tracked: Path) -> None:
    monkeypatch.setattr(loader, "_CONFIG_PATH", box)
    if hasattr(loader, "_TRACKED_MEMBERSHIP_PATH"):
        monkeypatch.setattr(loader, "_TRACKED_MEMBERSHIP_PATH", tracked)


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _box_scopes_yaml() -> str:
    return (
        "scopes:\n"
        "  llm_prompting:\n"
        "    prefixes:\n"
        f"      - {LLM_PREFIX}\n"
        f"    description: {BOX_DESCRIPTION}\n"
        "    vocab_mode: frontier\n"
        "  box_only:\n"
        "    prefixes:\n"
        f"      - {LLM_PREFIX}\n"
        "    description: box only stays whole\n"
    )


def _forbid_tracked_read(monkeypatch: pytest.MonkeyPatch, tracked: Path) -> None:
    """Raise if load_config opens the tracked path (read_text or open)."""
    real_read_text = Path.read_text
    real_open = Path.open
    banned = tracked.resolve()

    def _read_text(self: Path, *args: object, **kwargs: object) -> str:
        if Path(self).resolve() == banned:
            raise AssertionError(f"tracked file was read: {self}")
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    def _open(self: Path, *args: object, **kwargs: object) -> object:
        if Path(self).resolve() == banned:
            raise AssertionError(f"tracked file was opened: {self}")
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", _read_text)
    monkeypatch.setattr(Path, "open", _open)


@pytest.fixture
def cap_membership(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG, logger="services.rag.config._loader")
    return caplog


def test_missing_tracked_membership_keeps_box_scopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cap_membership: pytest.LogCaptureFixture,
) -> None:
    box = tmp_path / "rag.yaml"
    tracked = tmp_path / "scope_membership.yaml"
    _write(box, _box_scopes_yaml())
    _bind_paths(monkeypatch, box, tracked)

    config = loader.load_config()

    prefixes = config.scopes["llm_prompting"].prefixes
    assert prefixes == [LLM_PREFIX]
    assert "box_only" in config.scopes
    assert "Tracked scope membership missing" in cap_membership.text
    assert str(tracked) in cap_membership.text


@pytest.mark.parametrize(
    "body",
    [":\n", "[]\n", "scopes: []\n"],
    ids=["yaml-error", "root-list", "scopes-list"],
)
def test_malformed_tracked_membership_keeps_box_scopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cap_membership: pytest.LogCaptureFixture,
    body: str,
) -> None:
    box = tmp_path / "rag.yaml"
    tracked = tmp_path / "scope_membership.yaml"
    _write(box, _box_scopes_yaml())
    _write(tracked, body)
    _bind_paths(monkeypatch, box, tracked)

    config = loader.load_config()

    assert config.scopes["llm_prompting"].prefixes == [LLM_PREFIX]
    assert str(tracked) in cap_membership.text
    assert any(record.levelno >= logging.ERROR for record in cap_membership.records)


@pytest.mark.parametrize(
    "prefixes_body",
    [
        "    prefixes: not-a-list\n",
        "    prefixes: []\n",
        "    prefixes:\n      - ''\n      - 3\n",
    ],
    ids=["string", "empty-list", "no-string"],
)
def test_invalid_tracked_scope_entry_keeps_box_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cap_membership: pytest.LogCaptureFixture,
    prefixes_body: str,
) -> None:
    box = tmp_path / "rag.yaml"
    tracked = tmp_path / "scope_membership.yaml"
    _write(box, _box_scopes_yaml())
    _write(tracked, "scopes:\n  llm_prompting:\n" + prefixes_body)
    _bind_paths(monkeypatch, box, tracked)

    config = loader.load_config()

    assert config.scopes["llm_prompting"].prefixes == [LLM_PREFIX]
    assert "llm_prompting" in cap_membership.text
    assert "Skipping tracked scope" in cap_membership.text


def test_tracked_unicode_decode_error_keeps_box_scopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cap_membership: pytest.LogCaptureFixture,
) -> None:
    box = tmp_path / "rag.yaml"
    tracked = tmp_path / "scope_membership.yaml"
    _write(box, _box_scopes_yaml())
    tracked.write_bytes(b"\xff\xfe not-utf8")
    _bind_paths(monkeypatch, box, tracked)

    config = loader.load_config()

    assert config.scopes["llm_prompting"].prefixes == [LLM_PREFIX]
    assert "Failed to read tracked scope membership" in cap_membership.text
    assert str(tracked) in cap_membership.text


def test_tracked_oserror_keeps_box_scopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cap_membership: pytest.LogCaptureFixture,
) -> None:
    box = tmp_path / "rag.yaml"
    tracked = tmp_path / "scope_membership_dir"
    tracked.mkdir()
    _write(box, _box_scopes_yaml())
    _bind_paths(monkeypatch, box, tracked)

    config = loader.load_config()

    assert config.scopes["llm_prompting"].prefixes == [LLM_PREFIX]
    assert "Failed to read tracked scope membership" in cap_membership.text
    assert str(tracked) in cap_membership.text


def test_missing_box_rag_yaml_still_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    box = tmp_path / "rag.yaml"
    tracked = tmp_path / "scope_membership.yaml"
    _write(
        tracked,
        f"scopes:\n  llm_prompting:\n    prefixes:\n      - {SMALL_PREFIX}\n",
    )
    _bind_paths(monkeypatch, box, tracked)
    _forbid_tracked_read(monkeypatch, tracked)

    with pytest.raises(ValueError, match="rag.yaml not found at ~/.gateway/rag.yaml"):
        loader.load_config()


@pytest.mark.parametrize("body", [":\n", "[1]\n"], ids=["yaml-error", "root-list"])
def test_malformed_box_rag_yaml_still_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    body: str,
) -> None:
    box = tmp_path / "rag.yaml"
    tracked = tmp_path / "scope_membership.yaml"
    _write(box, body)
    _write(
        tracked,
        f"scopes:\n  llm_prompting:\n    prefixes:\n      - {SMALL_PREFIX}\n",
    )
    _bind_paths(monkeypatch, box, tracked)
    _forbid_tracked_read(monkeypatch, tracked)

    with pytest.raises(ValueError):
        loader.load_config()


def test_probe_merged_llm_prompting_covers_lost_in_the_middle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    box = tmp_path / "rag.yaml"
    tracked = tmp_path / "scope_membership.yaml"
    _write(box, _box_scopes_yaml())
    _write(
        tracked,
        f"scopes:\n  llm_prompting:\n    prefixes:\n      - {SMALL_PREFIX}\n",
    )
    _bind_paths(monkeypatch, box, tracked)

    prefixes = loader.load_config().scopes["llm_prompting"].prefixes
    short = f"{SMALL_PREFIX}/lost-in-the-middle.pdf"
    long = f"{LLM_PREFIX}/lost-in-the-middle-how-language-models-use-long-contexts.pdf"
    assert matches_source_prefix(short, prefixes)
    assert matches_source_prefix(long, prefixes)
    assert prefixes[0] == LLM_PREFIX
    assert prefixes[1] == SMALL_PREFIX


def test_merge_locks_vocab_mode_description_and_non_scope_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cap_membership: pytest.LogCaptureFixture,
) -> None:
    box_watch = tmp_path / "box-watch"
    tracked_watch = tmp_path / "tracked-watch"
    box_watch.mkdir()
    tracked_watch.mkdir()
    box = tmp_path / "rag.yaml"
    tracked = tmp_path / "scope_membership.yaml"
    _write(
        box,
        "embedding_model: box-embed-model\n"
        "index_workers: 3\n"
        "watch_directories:\n"
        f"  - path: {box_watch}\n"
        "    recursive: true\n"
        "scopes:\n"
        "  llm_prompting:\n"
        "    prefixes:\n"
        f"      - {LLM_PREFIX}\n"
        f"    description: {BOX_DESCRIPTION}\n"
        "    vocab_mode: frontier\n"
        "  box_only:\n"
        "    prefixes:\n"
        f"      - {LLM_PREFIX}\n"
        "    description: box only stays whole\n",
    )
    _write(
        tracked,
        "embedding_model: tracked-embed-model\n"
        "index_workers: 99\n"
        "watch_directories:\n"
        f"  - path: {tracked_watch}\n"
        "scopes:\n"
        "  llm_prompting:\n"
        "    prefixes:\n"
        f"      - {LLM_PREFIX}\n"
        f"      - {SMALL_PREFIX}\n"
        "    description: tracked description must be ignored\n"
        "    vocab_mode: local\n",
    )
    _bind_paths(monkeypatch, box, tracked)

    config = loader.load_config()
    scope = config.scopes["llm_prompting"]

    assert "Ignoring non-scope keys in tracked scope membership" in cap_membership.text
    assert "index_workers" in cap_membership.text
    assert "watch_directories" in cap_membership.text
    assert "embedding_model" in cap_membership.text
    assert scope.vocab_mode == "frontier"
    assert scope.description == BOX_DESCRIPTION
    assert "Ignoring tracked scope keys" in cap_membership.text
    assert config.index_workers == 3
    assert config.embedding_model == "box-embed-model"
    assert [item.path for item in config.watch_directories] == [
        str(box_watch.resolve())
    ]
    assert scope.prefixes == [LLM_PREFIX, SMALL_PREFIX]
    assert config.scopes["box_only"].description == "box only stays whole"
    assert config.scopes["box_only"].prefixes == [LLM_PREFIX]


def test_tracked_prefixes_skip_union_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cap_membership: pytest.LogCaptureFixture,
) -> None:
    box = tmp_path / "rag.yaml"
    tracked = tmp_path / "scope_membership.yaml"
    only_on_union = (
        "/mnt/torus/projects/universal-llm-gateway/docs/research/not-a-scope"
    )
    _write(
        box,
        "scopes:\n"
        "  llm_prompting:\n"
        "    prefixes:\n"
        f"      - {LLM_PREFIX}\n"
        "  all:\n"
        "    union: true\n"
        "    description: everything\n",
    )
    _write(
        tracked,
        "scopes:\n"
        "  llm_prompting:\n"
        "    prefixes:\n"
        f"      - {SMALL_PREFIX}\n"
        "  all:\n"
        "    prefixes:\n"
        f"      - {only_on_union}\n"
        "    description: tracked union description\n"
        "    union: false\n",
    )
    _bind_paths(monkeypatch, box, tracked)

    config = loader.load_config()
    union = config.scopes["all"]

    assert union.is_union is True
    assert union.description == "everything"
    assert only_on_union not in union.prefixes
    assert SMALL_PREFIX in union.prefixes
    assert "Skipping tracked prefixes for union scope 'all'" in cap_membership.text


def test_prefix_coverage_guard_names_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fixture orphan list, not a census of the live property index."""
    from services.rag.config.prefix_coverage import orphan_source_prefixes

    box = tmp_path / "rag.yaml"
    tracked = tmp_path / "scope_membership.yaml"
    _write(box, _box_scopes_yaml())
    _write(
        tracked,
        f"scopes:\n  llm_prompting:\n    prefixes:\n      - {SMALL_PREFIX}\n",
    )
    _bind_paths(monkeypatch, box, tracked)
    config = loader.load_config()
    scope_prefixes = [
        prefix for scope in config.scopes.values() for prefix in scope.prefixes
    ]
    unscoped_root = (
        "/mnt/torus/projects/universal-llm-gateway/docs/research/intentional-unscoped"
    )
    covered_short = f"{SMALL_PREFIX}/lost-in-the-middle.pdf"
    covered_long = (
        f"{LLM_PREFIX}/lost-in-the-middle-how-language-models-use-long-contexts.pdf"
    )
    unscoped = f"{unscoped_root}/skip.pdf"
    indexed = [covered_short, covered_long, unscoped, ORPHAN_PATH]

    orphans = orphan_source_prefixes(indexed, scope_prefixes, [unscoped_root])
    assert orphans == [ORPHAN_PATH]

    remaining = [covered_short, covered_long, unscoped]
    assert orphan_source_prefixes(remaining, scope_prefixes, [unscoped_root]) == []


def _capture_load_failed(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    bus = MagicMock()
    monkeypatch.setattr(
        "services.rag.rag_service.state.get_event_bus",
        lambda: bus,
    )
    return bus


def _assert_one_load_failed(bus: MagicMock, path: Path, stage: str, error: str) -> None:
    bus.publish_from_sync.assert_called_once()
    event = bus.publish_from_sync.call_args.args[0]
    assert event.signal == "rag.scope.membership.load.failed"
    assert event.payload == {"path": str(path), "stage": stage, "error": error}


def test_read_failure_emits_load_failed_and_returns_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracked = tmp_path / "scope_membership.yaml"
    tracked.write_bytes(b"\xff")
    bus = _capture_load_failed(monkeypatch)

    assert loader._load_tracked_scopes(tracked) is None

    _assert_one_load_failed(bus, tracked, "read", "UnicodeDecodeError")


def test_parse_failure_emits_load_failed_and_returns_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracked = tmp_path / "scope_membership.yaml"
    tracked.write_text(":\n", encoding="utf-8")
    bus = _capture_load_failed(monkeypatch)

    assert loader._load_tracked_scopes(tracked) is None

    _assert_one_load_failed(bus, tracked, "parse", "ParserError")


def test_root_type_failure_emits_load_failed_and_returns_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracked = tmp_path / "scope_membership.yaml"
    tracked.write_text("- 1\n", encoding="utf-8")
    bus = _capture_load_failed(monkeypatch)

    assert loader._load_tracked_scopes(tracked) is None

    _assert_one_load_failed(bus, tracked, "root_type", "list")


def test_factory_import_failure_still_returns_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracked = tmp_path / "scope_membership.yaml"
    tracked.write_text("- 1\n", encoding="utf-8")
    import services.rag.events.query as query

    monkeypatch.delattr(query, "rag_scope_membership_load_failed")

    assert loader._load_tracked_scopes(tracked) is None


def test_emitter_raise_still_returns_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracked = tmp_path / "scope_membership.yaml"
    tracked.write_text("- 1\n", encoding="utf-8")
    bus = _capture_load_failed(monkeypatch)
    bus.publish_from_sync.side_effect = RuntimeError("bus down")

    assert loader._load_tracked_scopes(tracked) is None


def test_committed_scope_membership_adds_small_llm_prefix() -> None:
    path = Path(loader.__file__).with_name("scope_membership.yaml")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    scopes = loaded["scopes"]
    assert scopes["llm_prompting"] == {"prefixes": [SMALL_PREFIX]}
    student = scopes["student_loans"]
    assert student["prefixes"] == ["/mnt/torus/mcp-data/files/legal/student-loans"]
    assert student.get("vocab_mode") == "frontier"
    assert "StudentAid.gov" in student.get("description", "")
