"""CHECKPOINT Sidecar: resolution — admit must not freeze on spill stubs."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
from scripts.model_manager.ui.controller.charter_runner.checkpoint_admit_gate import (
    validate_checkpoint_for_admit,
)
from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
    extract_sidecar_uri,
    materialize_checkpoint_turn,
    resolve_checkpoint_body,
    strip_sidecar_frontmatter,
)
from scripts.model_manager.ui.controller.charter_runner.admission import evaluate_root

_RESUME = (
    "— RESUME (any seat, no command): load agent-bus-discipline "
    "→ this is the latest CHECKPOINT."
)

_FULL = """\
# CHECKPOINT wave 3 — G3 DONE

## Steps
1. [x] G3 R-admit
2. [ ] G3b fold amendments

## In-flight / WIP
none

## Next pickup
1. G3b — fold A1–A7 into dense spec · executor_lane: judgment

## Frictions
_None this window._

## Sidecars
_None this window._

""" + _RESUME

_STUB = (
    "G3 R-admit complete. Merits ADMIT_WITH_AMENDMENTS. "
    "Full CHECKPOINT in sidecar. Next: G3b fold A1–A7 then G4.\n\n"
    "Sidecar: cortex://notes/system/threads/5918-checkpoint-wave-3-g3-r-admit.md"
)


@pytest.mark.offline
def test_extract_sidecar_uri_from_body_and_field() -> None:
    assert extract_sidecar_uri(_STUB) == (
        "cortex://notes/system/threads/5918-checkpoint-wave-3-g3-r-admit.md"
    )
    assert (
        extract_sidecar_uri(
            "brief",
            sidecar_uri="cortex://notes/system/threads/x.md",
        )
        == "cortex://notes/system/threads/x.md"
    )


@pytest.mark.offline
def test_strip_sidecar_frontmatter() -> None:
    wrapped = (
        "---\nthread: 5918\nsubject: CHECKPOINT\n---\n"
        "# CHECKPOINT\n\n## Steps\n1. [ ] G1\n"
    )
    assert strip_sidecar_frontmatter(wrapped).startswith("# CHECKPOINT")


@pytest.mark.offline
def test_resolve_prefers_inline_checkpoint(tmp_path: Path) -> None:
    assert resolve_checkpoint_body(_FULL, cortex_root=tmp_path) == _FULL


@pytest.mark.offline
def test_resolve_follows_sidecar_stub(tmp_path: Path) -> None:
    path = (
        tmp_path
        / "notes"
        / "system"
        / "threads"
        / "5918-checkpoint-wave-3-g3-r-admit.md"
    )
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nthread: 5918\nsha256: abc\n---\n" + _FULL,
        encoding="utf-8",
    )
    resolved = resolve_checkpoint_body(_STUB, cortex_root=tmp_path)
    assert "## Next pickup" in resolved
    assert "G3b" in resolved
    assert validate_checkpoint_for_admit(resolved).ok is True


@pytest.mark.offline
def test_evaluate_root_admits_when_cortex_root_patched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = (
        tmp_path
        / "notes"
        / "system"
        / "threads"
        / "5918-checkpoint-wave-3-g3-r-admit.md"
    )
    path.parent.mkdir(parents=True)
    path.write_text("---\nthread: 5918\n---\n" + _FULL, encoding="utf-8")
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.checkpoint_schema.body.cortex_files_root",
        lambda: tmp_path,
    )
    turns = [
        {
            "turn_number": 12,
            "subject": "CHECKPOINT wave 3",
            "body": _STUB,
        }
    ]
    decision = evaluate_root(
        "5918", turns, CapStore(intent_dir=tmp_path / "intent"), admission_mode="autonomous"
    )
    assert decision.eligible is True
    assert decision.parsed is not None
    assert any("G3b" in row for row in decision.parsed.next_pickup)


@pytest.mark.offline
def test_materialize_checkpoint_turn_mutates_body_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "notes" / "system" / "threads" / "x.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\n---\n" + _FULL, encoding="utf-8")
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.checkpoint_schema.body.cortex_files_root",
        lambda: tmp_path,
    )
    turn = {
        "turn_number": 1,
        "subject": "CHECKPOINT",
        "body": "brief\n\nSidecar: cortex://notes/system/threads/x.md",
    }
    out = materialize_checkpoint_turn(turn)
    assert out is not turn
    assert out["body"].startswith("# CHECKPOINT")
    assert turn["body"].startswith("brief")


@pytest.mark.offline
def test_normalize_strips_backticks_and_trailing_punct() -> None:
    from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
        normalize_checkpoint_machine_fields,
    )

    raw = (
        "## Sidecars\n"
        "- Dense: `cortex://notes/system/specs/friction-26332.md`.\n"
        "- Also: `cortex://notes/system/specs/friction-26332.md`).\n"
        "- Pin: `spec_sha256:" + ("a" * 64) + "`\n"
    )
    out = normalize_checkpoint_machine_fields(raw)
    assert "`cortex://" not in out
    assert "cortex://notes/system/specs/friction-26332.md" in out
    assert "cortex://notes/system/specs/friction-26332.md)." not in out
    assert "cortex://notes/system/specs/friction-26332.md`." not in out
    assert "`spec_sha256:" not in out
    assert "spec_sha256:" + ("a" * 64) in out
    # idempotent
    assert normalize_checkpoint_machine_fields(out) == out


@pytest.mark.offline
def test_materialize_normalizes_inline_sidecar_uris() -> None:
    from scripts.model_manager.ui.controller.charter_runner.checkpoint_schema import (
        materialize_checkpoint_turn,
        normalize_checkpoint_machine_fields,
    )

    body = _FULL.replace(
        "_None this window._",
        "- Dense: `cortex://notes/system/specs/friction-26332.md` · "
        "`spec_sha256:" + ("b" * 64) + "`",
    )
    turn = {"turn_number": 1, "subject": "CHECKPOINT", "body": body}
    out = materialize_checkpoint_turn(turn)
    assert "`" not in out["body"].split("## Sidecars", 1)[1].split("— RESUME", 1)[0]
    assert "cortex://notes/system/specs/friction-26332.md" in out["body"]
    assert out["body"] == normalize_checkpoint_machine_fields(body)


@pytest.mark.offline
def test_extract_sidecar_uri_strips_backticks() -> None:
    stub = (
        "brief\n\n"
        "Sidecar: `cortex://notes/system/threads/5918-checkpoint-wave-3-g3-r-admit.md`."
    )
    assert extract_sidecar_uri(stub) == (
        "cortex://notes/system/threads/5918-checkpoint-wave-3-g3-r-admit.md"
    )