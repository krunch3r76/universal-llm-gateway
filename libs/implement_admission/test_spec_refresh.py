"""Unit tests for spec_sha256 refresh helper (offline, monkeypatched _dispatch)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from implement_admission import spec_refresh as mod
from implement_admission.dense_spec_schema import dense_spec_hash_uri

_NOW = "2026-06-29T12:00:00+00:00"
_TODO = "todo:sample"
_SPEC_PATH = "tasks/specs/sample.md"
_OLD_SHA = "spec_sha256:aaa111"
_NEW_SHA = "spec_sha256:bbb222"
_NEW_SPEC = "updated spec content"


def _item(
    *,
    aid: int,
    status: str,
    evidence_uris: list[str] | None = None,
    predicate_form: str | None = None,
    claim: str | None = None,
    observed_at: str = "2026-01-01T00:00:00Z",
    superseded_by: int | None = None,
    valid_until: str | None = None,
) -> dict[str, Any]:
    pf = predicate_form
    if pf is None and claim is None:
        pf = f"status({_TODO}, {status}, current)"
    if claim is None:
        claim = pf or ""
    return {
        "id": aid,
        "entity_id": _TODO,
        "predicate_form": pf,
        "claim": claim,
        "evidence_uris": evidence_uris or [_SPEC_PATH, _OLD_SHA],
        "observed_at": observed_at,
        "superseded_by": superseded_by,
        "valid_until": valid_until,
    }


class _DispatchRecorder:
    def __init__(self, handlers: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.handlers = handlers or {}

    def __call__(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, arguments))
        handler = self.handlers.get(tool)
        if handler is not None:
            if callable(handler):
                return handler(arguments)
            return handler
        return {}


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _DispatchRecorder:
    rec = _DispatchRecorder()
    monkeypatch.setattr(mod, "_dispatch", rec)
    return rec


def test_no_change_with_current_attrs_makes_no_writes(
    tmp_path: Path, recorder: _DispatchRecorder
) -> None:
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("same content", encoding="utf-8")
    sha = dense_spec_hash_uri("same content")
    recorder.handlers = {
        "entity_get": {
            "attributes": {
                "implement_ready_assertion_id": 10,
                "skeptic_assertion_id": 20,
            }
        },
        "assertions": {
            "items": [
                _item(
                    aid=10, status="implement_ready", evidence_uris=[_SPEC_PATH, sha]
                ),
                _item(
                    aid=20, status="skeptic_ratified", evidence_uris=[_SPEC_PATH, sha]
                ),
            ]
        },
    }
    result = mod.refresh_spec_attestations(todo_id=_TODO, spec_path=spec_file)
    assert result.no_change is True
    mutating = [c for c in recorder.calls if c[0] in {"supersede", "entity_update"}]
    assert mutating == []


def test_no_change_but_stale_attrs_repins_without_supersede(
    tmp_path: Path, recorder: _DispatchRecorder
) -> None:
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("same content", encoding="utf-8")
    sha = dense_spec_hash_uri("same content")

    stored_attrs = {"implement_ready_assertion_id": 99, "skeptic_assertion_id": 88}

    def entity_get(_args: dict[str, Any]) -> dict[str, Any]:
        return {"attributes": dict(stored_attrs)}

    def entity_update(args: dict[str, Any]) -> dict[str, Any]:
        attrs = args["attributes"]
        assert attrs["implement_ready_assertion_id"] == 10
        assert attrs["skeptic_assertion_id"] == 20
        stored_attrs.update(attrs)
        return {"ok": True}

    recorder.handlers = {
        "entity_get": entity_get,
        "entity_update": entity_update,
        "assertions": {
            "items": [
                _item(
                    aid=10, status="implement_ready", evidence_uris=[_SPEC_PATH, sha]
                ),
                _item(
                    aid=20, status="skeptic_ratified", evidence_uris=[_SPEC_PATH, sha]
                ),
            ]
        },
    }
    result = mod.refresh_spec_attestations(todo_id=_TODO, spec_path=spec_file)
    assert result.no_change is True
    assert [c[0] for c in recorder.calls if c[0] == "supersede"] == []
    assert [c[0] for c in recorder.calls if c[0] == "entity_update"] == [
        "entity_update"
    ]


def test_dry_run_makes_no_writes(tmp_path: Path, recorder: _DispatchRecorder) -> None:
    spec_file = tmp_path / "spec.md"
    spec_file.write_text("changed", encoding="utf-8")
    recorder.handlers = {
        "entity_get": {"attributes": {}},
        "assertions": {"items": [_item(aid=1, status="implement_ready")]},
    }
    result = mod.refresh_spec_attestations(
        todo_id=_TODO, spec_path=spec_file, dry_run=True
    )
    assert result.no_change is False
    mutating = [c for c in recorder.calls if c[0] in {"supersede", "entity_update"}]
    assert mutating == []


def test_replace_sha_in_evidence_replaces_old() -> None:
    out = mod._replace_sha_in_evidence([_SPEC_PATH, _OLD_SHA], _OLD_SHA, _NEW_SHA)
    assert out == [_SPEC_PATH, _NEW_SHA]


def test_replace_sha_in_evidence_appends_when_absent() -> None:
    out = mod._replace_sha_in_evidence([_SPEC_PATH], None, _NEW_SHA)
    assert out == [_SPEC_PATH, _NEW_SHA]


def test_replace_sha_in_evidence_deduplicates() -> None:
    out = mod._replace_sha_in_evidence(
        [_SPEC_PATH, _OLD_SHA, _NEW_SHA], _OLD_SHA, _NEW_SHA
    )
    assert out == [_SPEC_PATH, _NEW_SHA]


def test_find_active_assertion_filters_by_predicate_form(
    recorder: _DispatchRecorder,
) -> None:
    recorder.handlers = {
        "assertions": {
            "items": [
                _item(
                    aid=1,
                    status="implement_ready",
                    predicate_form=f"status({_TODO}, implemented, current)",
                ),
                _item(aid=2, status="implement_ready"),
            ]
        }
    }
    found = mod._find_active_assertion(_TODO, "implement_ready", _NOW)
    assert found is not None
    assert found[0] == 2


def test_find_active_assertion_claim_fallback_positive(
    recorder: _DispatchRecorder,
) -> None:
    claim = f"status({_TODO}, skeptic_ratified, current) — ratified v4"
    recorder.handlers = {
        "assertions": {
            "items": [
                _item(
                    aid=7,
                    status="skeptic_ratified",
                    predicate_form=None,
                    claim=claim,
                )
            ]
        }
    }
    found = mod._find_active_assertion(_TODO, "skeptic_ratified", _NOW)
    assert found is not None
    assert found[0] == 7


def test_find_active_assertion_claim_fallback_rejects_explanatory_claim(
    recorder: _DispatchRecorder,
) -> None:
    recorder.handlers = {
        "assertions": {
            "items": [
                _item(
                    aid=8,
                    status="skeptic_ratified",
                    predicate_form=None,
                    claim="skeptic_ratified must be rerun after spec edit",
                )
            ]
        }
    }
    found = mod._find_active_assertion(_TODO, "skeptic_ratified", _NOW)
    assert found is None


def test_find_active_assertion_skips_superseded(recorder: _DispatchRecorder) -> None:
    recorder.handlers = {
        "assertions": {
            "items": [
                _item(
                    aid=1,
                    status="implement_ready",
                    superseded_by=99,
                    observed_at="2026-06-01T00:00:00Z",
                ),
                _item(
                    aid=2, status="implement_ready", observed_at="2026-01-01T00:00:00Z"
                ),
            ]
        }
    }
    found = mod._find_active_assertion(_TODO, "implement_ready", _NOW)
    assert found is not None
    assert found[0] == 2


def test_find_active_assertion_chooses_latest_by_observed_at_then_id(
    recorder: _DispatchRecorder,
) -> None:
    recorder.handlers = {
        "assertions": {
            "items": [
                _item(
                    aid=1, status="implement_ready", observed_at="2026-01-01T00:00:00Z"
                ),
                _item(
                    aid=2, status="implement_ready", observed_at="2026-06-01T00:00:00Z"
                ),
                _item(
                    aid=3,
                    status="implement_ready",
                    observed_at="2026-06-01T00:00:00Z",
                ),
            ]
        }
    }
    found = mod._find_active_assertion(_TODO, "implement_ready", _NOW)
    assert found is not None
    assert found[0] == 3


def test_supersede_assertion_uses_correct_payload(recorder: _DispatchRecorder) -> None:
    old = _item(aid=5, status="implement_ready")
    recorder.handlers = {"supersede": {"new": {"id": 6}}}
    mod._supersede_assertion(
        5, old, [_SPEC_PATH, _NEW_SHA], _TODO, "refresh-spec-hash-2026"
    )
    tool, args = recorder.calls[-1]
    assert tool == "supersede"
    assert args["old_assertion_id"] == 5
    assert args["entity_id"] == _TODO
    assert args["confidence"] == "confirmed"
    assert "refresh-spec-hash" in args["evidence"]
    assert args["session_id"] == "refresh-spec-hash-2026"
    assert args["agent"] == "refresh-spec-hash"
    assert args["evidence_uris"] == [_SPEC_PATH, _NEW_SHA]


def test_supersede_assertion_reads_new_id_from_result_new_id(
    recorder: _DispatchRecorder,
) -> None:
    old = _item(aid=5, status="implement_ready")
    recorder.handlers = {"supersede": {"new": {"id": 42}, "id": 999}}
    resp = mod._supersede_assertion(
        5, old, [_SPEC_PATH, _NEW_SHA], _TODO, "refresh-spec-hash-2026"
    )
    assert resp["new"]["id"] == 42


def test_supersede_concurrent_error_raises_runtime_error(
    recorder: _DispatchRecorder,
) -> None:
    old = _item(aid=5, status="implement_ready")
    recorder.handlers = {"supersede": {"error": "conflict", "status_code": 409}}
    with pytest.raises(RuntimeError, match="supersede failed"):
        mod._supersede_assertion(
            5, old, [_SPEC_PATH, _NEW_SHA], _TODO, "refresh-spec-hash-2026"
        )


def test_no_skeptic_assertion_skips_and_warns(
    tmp_path: Path, recorder: _DispatchRecorder
) -> None:
    spec_file = tmp_path / "spec.md"
    spec_file.write_text(_NEW_SPEC, encoding="utf-8")
    new_sha = dense_spec_hash_uri(_NEW_SPEC)

    def assertions(args: dict[str, Any]) -> dict[str, Any]:
        slug = "implement_ready" if args.get("entity_id") else ""
        if slug or True:
            items = [_item(aid=1, status="implement_ready")]
            if any(c[0] == "supersede" for c in recorder.calls):
                items.append(
                    _item(
                        aid=2,
                        status="implement_ready",
                        evidence_uris=[_SPEC_PATH, new_sha],
                        observed_at="2026-06-02T00:00:00Z",
                    )
                )
            return {"items": items}
        return {"items": []}

    stored_attrs: dict[str, Any] = {}

    def entity_get(_args: dict[str, Any]) -> dict[str, Any]:
        return {"attributes": dict(stored_attrs)}

    def entity_update(args: dict[str, Any]) -> dict[str, Any]:
        stored_attrs.update(args["attributes"])
        return {"ok": True}

    recorder.handlers = {
        "entity_get": entity_get,
        "assertions": assertions,
        "supersede": {"new": {"id": 2}},
        "entity_update": entity_update,
    }

    result = mod.refresh_spec_attestations(todo_id=_TODO, spec_path=spec_file)
    assert result.skipped_skeptic is True
    assert result.warnings
    assert any("skeptic_ratified" in w for w in result.warnings)
    assert result.implement_ready_new_id == 2
    supersede_calls = [c for c in recorder.calls if c[0] == "supersede"]
    assert len(supersede_calls) == 1


def test_verify_refresh_raises_on_sha_mismatch(recorder: _DispatchRecorder) -> None:
    recorder.handlers = {
        "assertions": {
            "items": [
                _item(
                    aid=10,
                    status="implement_ready",
                    evidence_uris=[_SPEC_PATH, _OLD_SHA],
                )
            ]
        },
        "entity_get": {"attributes": {"implement_ready_assertion_id": 10}},
    }
    with pytest.raises(RuntimeError, match="missing new sha"):
        mod._verify_refresh(
            todo_id=_TODO,
            new_sha_uri=_NEW_SHA,
            impl_new_id=10,
            skep_new_id=None,
            now_iso=_NOW,
        )


def test_full_refresh_happy_path(tmp_path: Path, recorder: _DispatchRecorder) -> None:
    spec_file = tmp_path / "spec.md"
    spec_file.write_text(_NEW_SPEC, encoding="utf-8")
    new_sha = dense_spec_hash_uri(_NEW_SPEC)
    state = {"impl_id": 1, "skep_id": 10, "impl_new": 2, "skep_new": 20}

    def assertions(_args: dict[str, Any]) -> dict[str, Any]:
        items = [
            _item(
                aid=state["impl_id"],
                status="implement_ready",
                evidence_uris=[_SPEC_PATH, _OLD_SHA],
            ),
            _item(
                aid=state["skep_id"],
                status="skeptic_ratified",
                evidence_uris=[_SPEC_PATH, _OLD_SHA],
            ),
        ]
        if any(c[0] == "entity_update" for c in recorder.calls):
            items = [
                _item(
                    aid=state["impl_new"],
                    status="implement_ready",
                    evidence_uris=[_SPEC_PATH, new_sha],
                    observed_at="2026-06-02T00:00:00Z",
                ),
                _item(
                    aid=state["skep_new"],
                    status="skeptic_ratified",
                    evidence_uris=[_SPEC_PATH, new_sha],
                    observed_at="2026-06-02T00:00:00Z",
                ),
            ]
        return {"items": items}

    def supersede(args: dict[str, Any]) -> dict[str, Any]:
        if args["old_assertion_id"] == state["skep_id"]:
            return {"new": {"id": state["skep_new"]}}
        return {"new": {"id": state["impl_new"]}}

    stored_attrs = {
        "implement_ready_assertion_id": state["impl_id"],
        "skeptic_assertion_id": state["skep_id"],
    }

    def entity_get(_args: dict[str, Any]) -> dict[str, Any]:
        return {"attributes": dict(stored_attrs)}

    def entity_update(args: dict[str, Any]) -> dict[str, Any]:
        stored_attrs.update(args["attributes"])
        return {"ok": True}

    recorder.handlers = {
        "entity_get": entity_get,
        "assertions": assertions,
        "supersede": supersede,
        "entity_update": entity_update,
    }

    result = mod.refresh_spec_attestations(todo_id=_TODO, spec_path=spec_file)
    assert result.implement_ready_new_id == state["impl_new"]
    assert result.skeptic_new_id == state["skep_new"]
    supersede_calls = [c for c in recorder.calls if c[0] == "supersede"]
    assert len(supersede_calls) == 2
    assert supersede_calls[0][1]["old_assertion_id"] == state["skep_id"]
    assert supersede_calls[1][1]["old_assertion_id"] == state["impl_id"]


def test_skeptic_idempotency_guard_skips_supersede_if_sha_matches(
    tmp_path: Path, recorder: _DispatchRecorder
) -> None:
    spec_file = tmp_path / "spec.md"
    spec_file.write_text(_NEW_SPEC, encoding="utf-8")
    new_sha = dense_spec_hash_uri(_NEW_SPEC)

    def assertions(_args: dict[str, Any]) -> dict[str, Any]:
        items = [
            _item(
                aid=1,
                status="implement_ready",
                evidence_uris=[_SPEC_PATH, _OLD_SHA],
            ),
            _item(
                aid=10,
                status="skeptic_ratified",
                evidence_uris=[_SPEC_PATH, new_sha],
            ),
        ]
        if any(c[0] == "supersede" for c in recorder.calls):
            items.append(
                _item(
                    aid=2,
                    status="implement_ready",
                    evidence_uris=[_SPEC_PATH, new_sha],
                    observed_at="2026-06-02T00:00:00Z",
                )
            )
        return {"items": items}

    stored_attrs: dict[str, Any] = {}

    def entity_get(_args: dict[str, Any]) -> dict[str, Any]:
        return {"attributes": dict(stored_attrs)}

    def entity_update(args: dict[str, Any]) -> dict[str, Any]:
        stored_attrs.update(args["attributes"])
        return {"ok": True}

    recorder.handlers = {
        "entity_get": entity_get,
        "assertions": assertions,
        "supersede": {"new": {"id": 2}},
        "entity_update": entity_update,
    }

    result = mod.refresh_spec_attestations(todo_id=_TODO, spec_path=spec_file)
    assert result.skeptic_new_id == 10
    supersede_calls = [c for c in recorder.calls if c[0] == "supersede"]
    assert len(supersede_calls) == 1
    assert supersede_calls[0][1]["old_assertion_id"] == 1


def test_retry_after_skeptic_success_implement_failure_no_duplicate_skeptic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_file = tmp_path / "spec.md"
    spec_file.write_text(_NEW_SPEC, encoding="utf-8")
    new_sha = dense_spec_hash_uri(_NEW_SPEC)
    calls: list[tuple[str, dict[str, Any]]] = []
    impl_failures = {"count": 1}

    stored_attrs: dict[str, Any] = {}

    def dispatch(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((tool, arguments))
        if tool == "entity_get":
            return {"attributes": dict(stored_attrs)}
        if tool == "assertions":
            items = [
                _item(
                    aid=1,
                    status="implement_ready",
                    evidence_uris=[_SPEC_PATH, _OLD_SHA],
                ),
                _item(
                    aid=10,
                    status="skeptic_ratified",
                    evidence_uris=[_SPEC_PATH, _OLD_SHA],
                ),
            ]
            if any(
                c[0] == "supersede" and c[1]["old_assertion_id"] == 10 for c in calls
            ):
                items = [
                    _item(
                        aid=1,
                        status="implement_ready",
                        evidence_uris=[_SPEC_PATH, _OLD_SHA],
                    ),
                    _item(
                        aid=20,
                        status="skeptic_ratified",
                        evidence_uris=[_SPEC_PATH, new_sha],
                        observed_at="2026-06-02T00:00:00Z",
                    ),
                ]
            if any(c[0] == "entity_update" for c in calls):
                items = [
                    _item(
                        aid=2,
                        status="implement_ready",
                        evidence_uris=[_SPEC_PATH, new_sha],
                        observed_at="2026-06-03T00:00:00Z",
                    ),
                    _item(
                        aid=20,
                        status="skeptic_ratified",
                        evidence_uris=[_SPEC_PATH, new_sha],
                        observed_at="2026-06-02T00:00:00Z",
                    ),
                ]
            return {"items": items}
        if tool == "supersede":
            if arguments["old_assertion_id"] == 10:
                return {"new": {"id": 20}}
            if impl_failures["count"] > 0:
                impl_failures["count"] -= 1
                raise RuntimeError("supersede failed: {'error': 'transient'}")
            return {"new": {"id": 2}}
        if tool == "entity_update":
            stored_attrs.update(arguments["attributes"])
            return {"ok": True}
        return {}

    monkeypatch.setattr(mod, "_dispatch", dispatch)

    with pytest.raises(RuntimeError):
        mod.refresh_spec_attestations(todo_id=_TODO, spec_path=spec_file)

    result = mod.refresh_spec_attestations(todo_id=_TODO, spec_path=spec_file)
    assert result.implement_ready_new_id == 2
    assert result.skeptic_new_id == 20
    skeptic_supersedes = [
        c for c in calls if c[0] == "supersede" and c[1]["old_assertion_id"] == 10
    ]
    assert len(skeptic_supersedes) == 1
