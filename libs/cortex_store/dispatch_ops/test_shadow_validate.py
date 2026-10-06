"""Shadow validation of dispatch args against stamped routes (S3)."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from cortex_store.dispatch_ops import _OPS, execute_op
from cortex_store.dispatch_ops import _shadow_validate as shadow

_SECRET = "shadow-pii-9f3c1a-do-not-record"
_CALLS = 200


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    captured: list[tuple[str, dict]] = []

    def _fake(signal: str, **payload: object) -> None:
        captured.append((signal, dict(payload)))

    monkeypatch.setattr("cortex_store.dispatch_ops.record", _fake)
    monkeypatch.setattr(shadow, "record", _fake)
    return captured


def _shadow_events(captured: list[tuple[str, dict]]) -> list[dict]:
    return [
        payload
        for signal, payload in captured
        if signal == "mcp.cortex.dispatch.shadow"
    ]


def _install(tool: str, result: dict) -> None:
    def _handler(**_kwargs: object) -> dict:
        return result

    _OPS._cache[tool] = _handler  # type: ignore[attr-defined]


@pytest.mark.offline
def test_shadow_never_changes_result(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    echoed: list[dict] = []

    def _handler(**kwargs: object) -> dict:
        echoed.append(dict(kwargs))
        return {"echo": dict(kwargs)}

    _OPS._cache["entity_get"] = _handler  # type: ignore[attr-defined]
    args = {"entity_id": "todo:s3", "shadow_unknown_param": _SECRET}
    with_shadow = execute_op("entity_get", args, caller="cursor-sdk")
    monkeypatch.setattr(shadow, "shadow_validate", lambda *_a, **_k: None)
    without_shadow = execute_op("entity_get", args, caller="cursor-sdk")
    assert with_shadow == without_shadow
    assert echoed[0] == echoed[1] == args
    assert _shadow_events(captured)


@pytest.mark.offline
def test_shadow_would_reject_unknown_param(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    _install("entity_get", {"ok": True})
    execute_op(
        "entity_get",
        {"entity_id": "todo:s3", "shadow_unknown_param": "alias-flood"},
        caller="cursor-sdk",
    )
    event = _shadow_events(captured)[-1]
    assert event["outcome"] == "would_reject"
    assert "shadow_unknown_param" in event["error_locs"]
    assert event["caller"] == "cursor-sdk"
    assert event["tool"] == "entity_get"


@pytest.mark.offline
def test_shadow_unmapped_for_unstamped_op(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    synthetic = "__shadow_unmapped_synthetic_op__"
    _OPS._specs[synthetic] = "ops_misc:_op_stats"  # type: ignore[attr-defined]
    try:
        _install(synthetic, {"ok": True})
        result = execute_op(synthetic, {"any": "value"}, caller="cursor-sdk")
        assert result.get("ok") is True
        event = _shadow_events(captured)[-1]
        assert event["outcome"] == "unmapped"
        assert event["error_locs"] == []
        assert event["tool"] == synthetic
    finally:
        del _OPS._specs[synthetic]  # type: ignore[attr-defined]
        _OPS._cache.pop(synthetic, None)  # type: ignore[attr-defined]


@pytest.mark.offline
def test_shadow_records_no_values(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    _install("entity_get", {"ok": True})
    execute_op(
        "entity_get",
        {
            "entity_id": _SECRET,
            "intent": _SECRET,
            "shadow_unknown_param": _SECRET,
        },
        caller="cursor-sdk",
    )
    event = _shadow_events(captured)[-1]
    assert event["outcome"] == "would_reject"
    assert "intent" in event["error_locs"]
    assert "shadow_unknown_param" in event["error_locs"]
    assert _SECRET not in json.dumps(event)


@pytest.mark.offline
def test_shadow_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture(monkeypatch)

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError(_SECRET)

    monkeypatch.setattr(shadow, "shadow_validate", _boom)
    _install("stats", {"ok": True})
    result = execute_op("stats", {})
    assert result["ok"] is True
    assert _SECRET not in json.dumps(result)


@pytest.mark.offline
def test_shadow_route_lookup_cached_without_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route table is the live app object. No second create_app, no lifespan."""
    import cortex_store.main as main

    _capture(monkeypatch)
    shadow.reset_shadow_cache()
    calls = {"create_app": 0, "migrations": 0, "conn": 0, "task": 0, "vector": 0}

    def _create_app(*_a: object, **_k: object) -> None:
        calls["create_app"] += 1
        raise AssertionError("create_app")

    def _mark(name: str):
        def _inner(*_a: object, **_k: object) -> None:
            calls[name] += 1

        return _inner

    monkeypatch.setattr(main, "create_app", _create_app)
    monkeypatch.setattr(main, "run_migrations", _mark("migrations"))
    monkeypatch.setattr(main, "cortex_conn", _mark("conn"))
    monkeypatch.setattr(main, "check_cortex_db", _mark("conn"))
    monkeypatch.setattr(main, "_init_vector_subsystem", _mark("vector"))
    monkeypatch.setattr(asyncio, "create_task", _mark("task"))
    _install("entity_get", {"ok": True})
    execute_op("entity_get", {"entity_id": "todo:s3"}, caller="cursor-sdk")
    execute_op("entity_get", {"entity_id": "todo:s3"}, caller="cursor-sdk")
    assert calls == {
        "create_app": 0,
        "migrations": 0,
        "conn": 0,
        "task": 0,
        "vector": 0,
    }
    assert shadow._index_builds == 1
    assert shadow._contract_builds == 1
    oid = shadow._tool_to_oid["entity_get"]
    spec = main.app.openapi()["paths"]["/entities/{entity_id}"]["get"]
    assert oid == spec["operationId"]
    assert shadow._contracts[oid].operation_id == oid


@pytest.mark.offline
def test_shadow_p50_overhead_under_2ms(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture(monkeypatch)
    _install("stats", {"ok": True})
    execute_op("stats", {}, caller="cursor-sdk")  # warm the operationId cache

    def _sample(enabled: bool) -> list[float]:
        if not enabled:
            monkeypatch.setattr(shadow, "shadow_validate", lambda *_a, **_k: None)
        samples: list[float] = []
        for _ in range(_CALLS):
            start = time.perf_counter()
            execute_op("stats", {}, caller="cursor-sdk")
            samples.append(time.perf_counter() - start)
        return samples

    on_samples = _sample(True)
    off_samples = _sample(False)
    on_p50 = sorted(on_samples)[len(on_samples) // 2]
    off_p50 = sorted(off_samples)[len(off_samples) // 2]
    overhead = on_p50 - off_p50
    print(
        f"shadow_p50 calls={_CALLS} on={on_p50 * 1000:.4f}ms "
        f"off={off_p50 * 1000:.4f}ms overhead={overhead * 1000:.4f}ms"
    )
    assert overhead < 0.002


@pytest.mark.offline
def test_shadow_operation_ids_match_served_ops() -> None:
    """Nested stamps (audit) and optional body models (edge_retire) resolve."""
    shadow.reset_shadow_cache()
    from cortex_store.openapi_mcp.generated_adapter_manifest import SERVED_OPS

    mismatches: list[tuple[str, str | None, str]] = []
    unresolved_bodies: list[str] = []
    for op, spec in sorted(SERVED_OPS.items()):
        oid = shadow._operation_id_for(op)
        if oid != spec["operation_id"]:
            mismatches.append((op, oid, spec["operation_id"]))
            continue
        route = shadow._routes_by_oid[oid]
        contract = shadow._contract_for(oid)
        for field in route.dependant.body_params:
            if (
                shadow._as_model(field.field_info.annotation) is not None
                and contract.body_model is None
            ):
                unresolved_bodies.append(op)
    assert mismatches == []
    assert unresolved_bodies == []
    assert shadow._operation_id_for("audit") == (
        "boot_audit_counters_boot_audit_counters_get"
    )


@pytest.mark.offline
def test_shadow_optional_body_match_and_reject(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    _install("edge_retire", {"ok": True})
    valid = execute_op(
        "edge_retire",
        {"edge_id": 5, "valid_until": "2026-10-05T00:00:00Z"},
        caller="cursor-sdk",
    )
    bad = execute_op(
        "edge_retire",
        {"edge_id": 5, "valid_until": 12345},
        caller="cursor-sdk",
    )
    assert valid == {"ok": True}
    assert bad == {"ok": True}
    shadows = _shadow_events(captured)
    assert shadows[0]["outcome"] == "match"
    assert shadows[0]["error_locs"] == []
    assert shadows[1]["outcome"] == "would_reject"
    assert shadows[1]["error_locs"] == ["valid_until"]
    assert "12345" not in json.dumps(shadows[1])
