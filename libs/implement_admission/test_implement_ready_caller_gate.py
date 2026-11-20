"""AC1, AC4, AC7: Gate A acceptance proofs on require_implement_ready."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from implement_admission.dense_spec_schema import dense_spec_hash_uri

from implement_admission.implement_ready_gate import (
    ImplementReadyGateError,
    require_implement_ready,
)

_TODO = "todo:densification-implement-admission-gate"
_SPEC = "cortex://notes/system/specs/densification-implement-admission-gate.md"
_SPEC_REL = "tasks/specs/densification-implement-admission-gate.md"

_VALID_DENSE_SPEC = """\
# Dense test spec

## 1. Problem

A problem exists.

## 2. Non-goals / scope exclusions

Out of scope items.

## 3. Source-of-truth / provenance

| Source | Role |
|---|---|
| spec | authoritative |

## 4. Touch-point inventory

- module.py

## 5. Bound design decisions / fork table

| Fork | Decision |
|---|---|
| 1 | resolved |

## 6. Implementation guidance

Build the validator.

## 7. Acceptance criteria

1. Validator passes dense specs.

## 8. Verification / quality gates

- pytest green

<reasoning_trace>

No fork remains OPEN.

</reasoning_trace>
"""


class _FakeCortex:
    def __init__(
        self,
        *,
        entity: dict | None = None,
        assertion: dict | None = None,
    ) -> None:
        self._entity = entity or {}
        self._assertion = assertion

    def entity_get(self, entity_id: str, **_kwargs: object) -> dict:
        return self._entity

    def assertion_get(self, assertion_id: int) -> dict:
        if self._assertion is None:
            return {"error": "missing"}
        return self._assertion

    def assertions(self, entity_id: str, **_kwargs: object) -> dict:
        return {"items": []}


def test_admit_refuses_undeclared_exposed_lib(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", "/tmp/unused-cortex-root")
    with pytest.raises(ImplementReadyGateError) as excinfo:
        require_implement_ready(
            request_id="req-ac1",
            source_ref="todo:leg-e-caller-gate-fixture",
            cortex=_FakeCortex(
                entity={
                    "name": "fixture",
                    "attributes": {
                        "density_triage": "mechanical",
                        "files_expected": ["libs/claude_bundles/holder_strings.py"],
                    },
                },
                assertion={"error": "missing"},
            ),
        )
    exc = excinfo.value
    assert exc.code == "caller_inventory_undeclared"
    assert exc.status_code == 422
    assert "libs/cdp_ask/work_projection.py:124" in exc.reason


def test_existing_refusal_precedes_caller_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", "/tmp/unused-cortex-root")
    with pytest.raises(ImplementReadyGateError) as excinfo:
        require_implement_ready(
            request_id="req-order",
            source_ref="todo:leg-e-caller-gate-fixture",
            cortex=_FakeCortex(
                entity={
                    "name": "fixture",
                    "attributes": {
                        "density_triage": "recon_pending",
                        "files_expected": ["libs/claude_bundles/holder_strings.py"],
                    },
                },
            ),
        )
    assert excinfo.value.code == "implement_blocked_recon_pending"


def test_admit_refuses_undeclared_route_test_obligation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", "/tmp/unused-cortex-root")
    attrs = {
        "density_triage": "mechanical",
        "files_expected": ["libs/claude_bundles/holder_strings.py"],
        "domain_contract_change": "false",
    }
    with pytest.raises(ImplementReadyGateError) as excinfo:
        require_implement_ready(
            request_id="req-ac4",
            source_ref="todo:leg-e-caller-gate-fixture",
            cortex=_FakeCortex(entity={"name": "fixture", "attributes": attrs}),
        )
    assert excinfo.value.code == "route_probe_undeclared"
    assert "pytest libs/cdp_ask/test_active_work.py -q" in excinfo.value.reason

    attrs_ok = {**attrs, "route_probe_tests": ["libs/cdp_ask/test_active_work.py"]}
    require_implement_ready(
        request_id="req-ac4-ok",
        source_ref="todo:leg-e-caller-gate-fixture",
        cortex=_FakeCortex(entity={"name": "fixture", "attributes": attrs_ok}),
    )


def _write_ac7_fixtures(tmp_path: Path, extra_attrs: dict) -> _FakeCortex:
    import json

    spec_path = tmp_path / "universal-llm-gateway" / _SPEC_REL
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(_VALID_DENSE_SPEC, encoding="utf-8")
    archive_rel = Path("notes/system/threads/archives/ready.md")
    archive = tmp_path / archive_rel
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive_body = b"ready archive\n"
    archive.write_bytes(archive_body)
    archive_sha = hashlib.sha256(archive_body).hexdigest()
    record = {
        "todo": _TODO,
        "consult_thread": "agent-bus:8801#12",
        "verdict": "ADMIT",
        "adjudication_assertion_id": 1,
        "consultant_model": "claude-fable-5-1",
        "consultant_effort": "high",
        "consultant_substrate": "cdp",
        "archive_uri": f"cortex://{archive_rel.as_posix()}",
        "archive_sha256": archive_sha,
        "satellite_execution_id": "sat-1",
        "stargate_execution_id": "sg-1",
        "written_by": "test",
        "written_at": "2026-06-17T07:00:00Z",
    }
    record_path = (
        tmp_path
        / "notes/system/threads/todo-consult-provenance/densification-implement-admission-gate.json"
    )
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(record), encoding="utf-8")
    attrs = {
        "density_triage": "judgment_required",
        "implement_ready_assertion_id": 99,
        "files_expected": ["libs/claude_bundles/holder_strings.py"],
        "acceptance_criteria": ["Validator passes dense specs."],
        **extra_attrs,
    }
    assertion = {
        "entity_id": _TODO,
        "superseded_by": None,
        "valid_until": None,
        "evidence_uris": [
            f"workspaces://universal-llm-gateway/{_SPEC_REL}",
            dense_spec_hash_uri(_VALID_DENSE_SPEC),
        ],
    }
    return _FakeCortex(
        entity={
            "name": "densification-implement-admission-gate",
            "source_uri": _SPEC,
            "attributes": attrs,
        },
        assertion=assertion,
    )


def test_judgment_required_admit_refuses_undeclared_exposed_lib(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    with pytest.raises(ImplementReadyGateError) as excinfo:
        require_implement_ready(
            request_id="req-ac7",
            source_ref=_TODO,
            cortex=_write_ac7_fixtures(tmp_path, {}),
            skip_doc_validate_guard=True,
        )
    assert excinfo.value.code == "caller_inventory_undeclared"
    assert "libs/cdp_ask/work_projection.py:124" in excinfo.value.reason


def test_judgment_required_admit_refuses_undeclared_route_test_obligation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    with pytest.raises(ImplementReadyGateError) as excinfo:
        require_implement_ready(
            request_id="req-ac7b",
            source_ref=_TODO,
            cortex=_write_ac7_fixtures(tmp_path, {"domain_contract_change": "false"}),
            skip_doc_validate_guard=True,
        )
    assert excinfo.value.code == "route_probe_undeclared"
    assert "pytest libs/cdp_ask/test_active_work.py -q" in excinfo.value.reason