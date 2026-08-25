"""Contract tests -- the self-check items, asserted rather than claimed.

These are the ones that fail if a later edit quietly breaks the seam: an import of
``libs.`` sneaking into the core, a CHECKPOINT parser appearing, the README's signal
table drifting from the handler table, or a mutable DTO.
"""

from __future__ import annotations

import ast
import os
import sys

import pytest

from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.dtos import SEVERITIES, Thresholds
from scripts.model_manager.ui.dispatch_monitor.core.model import Model
from scripts.model_manager.ui.dispatch_monitor.core.replay import load_fixture

from .conftest import FIXTURE_NAMES, fixture_path, replay

_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Modules permitted to import from the standard library only. Everything in the
#: package is in scope; the test walks the tree rather than a hardcoded list so a
#: new module cannot escape the check by not being listed.
FORBIDDEN_ROOTS = ("libs", "services", "scripts", "universal_protocol",
                   "universal_transport", "event_store", "httpx", "pydantic",
                   "fastapi", "sqlalchemy", "anyio", "yaml", "requests")


def _core_modules() -> list[str]:
    """Return every ``.py`` file in the package except the test package."""
    found = []
    for directory, _dirs, files in os.walk(_PACKAGE_ROOT):
        if os.path.basename(directory) == "tests":
            continue
        found.extend(
            os.path.join(directory, name) for name in files if name.endswith(".py")
        )
    return sorted(found)


def _imported_roots(path: str) -> set[str]:
    """Return the top-level module names ``path`` imports."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_core_imports_stdlib_only() -> None:
    """Self-check 1: zero ``libs.`` / ``services.`` / third-party imports in core."""
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    assert stdlib, "this check needs Python 3.10+ stdlib_module_names"
    offenders: dict[str, set[str]] = {}
    for path in _core_modules():
        bad = {
            root
            for root in _imported_roots(path)
            if root not in stdlib and root not in {"dispatch_monitor_core", "scripts"}
        }
        if bad:
            offenders[os.path.relpath(path, _PACKAGE_ROOT)] = bad
    assert offenders == {}, f"non-stdlib imports in core: {offenders}"


def test_no_forbidden_root_appears_anywhere_in_core() -> None:
    """Belt and braces: the named forbidden roots must not appear as imports."""
    for path in _core_modules():
        overlap = _imported_roots(path) & set(FORBIDDEN_ROOTS)
        assert not overlap, f"{os.path.relpath(path, _PACKAGE_ROOT)} imports {overlap}"


def test_no_checkpoint_parsing_in_core() -> None:
    """Self-check 4: the core contains no CHECKPOINT parser.

    Asserted structurally rather than by grepping for the word, since the word
    appears legitimately in docstrings that *forbid* parsing. What must be absent is
    any parse-shaped operation over CHECKPOINT text.
    """
    suspicious = ("re.search", "re.match", "re.compile", "re.findall", "splitlines()")
    for path in _core_modules():
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        for token in suspicious:
            assert token not in source, (
                f"{os.path.relpath(path, _PACKAGE_ROOT)} contains {token!r}; "
                "text parsing does not belong in the core"
            )
        assert "checkpoint_folded" not in source.replace(
            "# ", ""
        ) or "GP1" in source, "checkpoint_folded may only appear as a deferral note"


def test_handler_table_matches_the_declared_signal_registry() -> None:
    """Self-check 3: the registry and the handler table agree exactly.

    The registry is what ``README.md`` documents, so this test is what stops the
    README from drifting away from the code that implements it.
    """
    declared = set(signals.ALL_HANDLED)
    handled = set(Model().handled_signals)
    assert handled == declared, {
        "declared_not_handled": sorted(declared - handled),
        "handled_not_declared": sorted(handled - declared),
    }


def test_every_family_is_represented_in_the_handler_table() -> None:
    """Each of the three families plus the plane counters must be covered."""
    handled = set(Model().handled_signals)
    for family, label in (
        (signals.CHARTER_FAMILY, "charter"),
        (signals.SDK_FAMILY, "sdk"),
        (signals.CDP_FAMILY, "cdp"),
        (signals.META_FAMILY, "plane"),
    ):
        missing = set(family) - handled
        assert not missing, f"{label} family not fully handled: {sorted(missing)}"


def test_cdp_observation_signals_disjoint_from_handled_registry() -> None:
    """Observation emitters are declared but not folded (I-7 / I-9)."""
    observation = set(signals.CDP_OBSERVATION_SIGNALS)
    handled = set(Model().handled_signals)
    assert observation.isdisjoint(handled)
    assert observation.isdisjoint(set(signals.ALL_HANDLED))
    assert observation.isdisjoint(set(signals.CDP_FAMILY))


def test_readme_documents_every_handled_signal() -> None:
    """Self-check 3, documentation half: the README names each handled signal."""
    readme = os.path.join(_PACKAGE_ROOT, "README.md")
    if not os.path.exists(readme):  # pragma: no cover - README ships with the sidecar
        pytest.skip("README.md not present in this checkout")
    with open(readme, encoding="utf-8") as handle:
        text = handle.read()
    missing = [s for s in signals.ALL_HANDLED if s not in text]
    assert not missing, f"README omits handled signals: {missing}"


def test_projection_dtos_are_frozen(any_fixture: str) -> None:
    """Frames are immutable, so a View cannot mutate what it renders."""
    import dataclasses

    model, now = replay(any_fixture)
    frame = model.derive(now)
    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.fingerprint = "tampered"  # type: ignore[misc]
    if frame.roots:
        with pytest.raises(dataclasses.FrozenInstanceError):
            frame.roots[0].state = "tampered"  # type: ignore[misc]


def test_thresholds_are_all_idle_windows() -> None:
    """Every threshold is an idle or count window; none is a completion deadline."""
    for field_name in Thresholds.__dataclass_fields__:
        assert field_name.endswith(("_ms", "_warn")), (
            f"{field_name} is not named as an idle window or a count; "
            "wall-clock completion budgets are forbidden"
        )


def test_attention_is_totally_ordered_by_severity(any_fixture: str) -> None:
    """Attention is sorted strongest-first with a deterministic tiebreak."""
    model, now = replay(any_fixture)
    items = model.derive(now + 10_000_000).attention
    ranks = [SEVERITIES.index(i.severity) for i in items]
    assert ranks == sorted(ranks, reverse=True)
    keys = [(-SEVERITIES.index(i.severity), i.kind, i.subject, i.key) for i in items]
    assert keys == sorted(keys)
    assert len({i.key for i in items}) == len(items), "attention keys must be unique"


def test_every_fixture_loads_and_folds(any_fixture: str) -> None:
    """All shipped fixtures parse and fold without error."""
    records = load_fixture(fixture_path(any_fixture))
    assert records, f"{any_fixture} yielded no records"
    model, now = replay(any_fixture)
    assert model.records_folded == len(records)
    assert model.derive(now).schema_version == 1


def test_fixture_inventory_is_complete() -> None:
    """The fixture directory holds exactly the fixtures the suite claims."""
    directory = os.path.join(_PACKAGE_ROOT, "fixtures")
    on_disk = sorted(f for f in os.listdir(directory) if f.endswith(".jsonl"))
    assert on_disk == sorted(FIXTURE_NAMES)


def test_model_does_not_read_the_clock() -> None:
    """The Model has no clock. Time enters only as the ``now_ms`` argument."""
    view_modules = {"__main__.py", "replay.py", "board_lines.py", "curses_board.py", "watch.py"}
    for path in _core_modules():
        name = os.path.relpath(path, _PACKAGE_ROOT)
        if name in view_modules:
            continue
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        for token in ("time.time", "time.monotonic", "datetime.now", "utcnow"):
            assert token not in source, f"{name} reads a clock via {token}"


#: I/O builtins whose *call* is forbidden outside the harness.
_IO_CALLS = ("open", "input", "print")

#: Modules whose *import* is forbidden anywhere in the core.
_IO_IMPORTS = ("socket", "urllib", "subprocess", "http", "asyncio", "selectors",
               "ssl", "shutil", "pathlib")


def test_only_the_harness_touches_the_filesystem() -> None:
    """``replay.py`` and ``__main__.py`` are the only modules allowed to do I/O.

    Checked over the AST rather than the raw text. A text scan flags the many
    docstrings that *forbid* sockets, which is the opposite of a finding -- the
    prose is the invariant being documented, not a violation of it.
    """
    offenders: dict[str, set[str]] = {}
    for path in _core_modules():
        name = os.path.relpath(path, _PACKAGE_ROOT)
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        found: set[str] = set()
        for module in _imported_roots(path) & set(_IO_IMPORTS):
            found.add(f"import {module}")
        if name not in ("__main__.py", "replay.py"):
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in _IO_CALLS
                ):
                    found.add(f"{node.func.id}()")
        if found:
            offenders[name] = found
    assert offenders == {}, f"I/O in the pure core: {offenders}"
