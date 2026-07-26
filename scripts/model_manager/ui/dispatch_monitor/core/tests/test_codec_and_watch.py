"""Projection wire schema, the ``--watch`` sink, and the advisory-hints rule.

Covers the three contracts the graft depends on and cannot see from outside:
round-trip fidelity, the receiver-side version posture, and the drop rule that
keeps ``changed_hints`` honest (falsifier F4).
"""

from __future__ import annotations

import json

import pytest
from .conftest import fixture_path, replay

from scripts.model_manager.ui.dispatch_monitor.core.codec import (
    FRAME_HANDSHAKE,
    FRAME_SNAPSHOT,
    ProjectionCodec,
    from_wire,
    to_wire,
)
from scripts.model_manager.ui.dispatch_monitor.core.dtos import SCHEMA_VERSION
from scripts.model_manager.ui.dispatch_monitor.core.model import Model, hints_after_drop
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event
from scripts.model_manager.ui.dispatch_monitor.core.watch import render


def test_snapshot_round_trip_preserves_everything(any_fixture: str) -> None:
    """Encode then decode must reproduce the projection exactly, fingerprint included."""
    model, now = replay(any_fixture)
    original = model.derive(now)
    kind, decoded = ProjectionCodec.decode_frame(
        ProjectionCodec.encode_snapshot(original)
    )
    assert kind == FRAME_SNAPSHOT
    assert decoded == original
    assert decoded.fingerprint == original.fingerprint


def test_encoding_is_canonical_and_byte_stable(any_fixture: str) -> None:
    """Equal state must yield equal bytes -- the property the fingerprint rests on."""
    first_model, now = replay(any_fixture)
    second_model, _ = replay(any_fixture)
    assert ProjectionCodec.encode_snapshot(
        first_model.derive(now)
    ) == ProjectionCodec.encode_snapshot(second_model.derive(now))


def test_handshake_carries_the_command_endpoint_hint() -> None:
    """One discovery point, two carriers: the View hardcodes no address."""
    kind, data = ProjectionCodec.decode_frame(
        ProjectionCodec.encode_handshake("unix:///tmp/universal-protocol/manage.sock")
    )
    assert kind == FRAME_HANDSHAKE
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["command_endpoint"] == "unix:///tmp/universal-protocol/manage.sock"
    assert data["snapshot_only"] is True


def test_newer_major_schema_is_refused_not_partially_rendered() -> None:
    """Silent partial rendering of a newer schema is the worst outcome, so refuse."""
    model = Model()
    payload = to_wire(model.derive(0))
    payload["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(ValueError, match="exceeds supported"):
        from_wire(payload)


def test_unknown_fields_are_ignored_by_the_decoder() -> None:
    """The receiver half of additive-only versioning: drop what you don't know."""
    model = Model()
    payload = to_wire(model.derive(0))
    payload["health"]["a_field_from_the_future"] = 42
    payload["some_new_top_level"] = ["ignored"]
    assert from_wire(payload).health.records_folded == 0


def test_hints_are_empty_without_a_previous_frame(any_fixture: str) -> None:
    """``derive(now_ms)`` with no previous frame yields no hints."""
    model, now = replay(any_fixture)
    assert model.derive(now).changed_hints == ()


def test_hints_name_only_the_sections_that_changed() -> None:
    """Hints are computed against the frame the caller passes, not remembered state."""
    model = Model()
    first = model.derive(1_000)
    model.apply(
        Event("manage.charter.tick.admitted", 1_100, {"root": "1", "worker_thread": "2"})
    )
    second = model.derive(1_200, previous=first)
    assert "roots" in second.changed_hints
    assert "cdp" not in second.changed_hints
    third = model.derive(1_300, previous=second)
    assert third.changed_hints == (), "no fold happened, so nothing changed"


def test_post_drop_delivery_must_carry_the_wildcard_hint() -> None:
    """F4: after any drop, hints are untrustworthy and must be stamped ``("*",)``."""
    model = Model()
    first = model.derive(1_000)
    model.apply(Event("manage.charter.tick.admitted", 1_100, {"root": "1"}))
    frame = model.derive(1_200, previous=first)
    assert frame.changed_hints != ("*",)
    stamped = hints_after_drop(frame)
    assert stamped.changed_hints == ("*",)
    assert stamped.fingerprint == frame.fingerprint, "hints are not state"


def test_watch_render_is_pure_text_and_names_every_section(any_fixture: str) -> None:
    """The text sink renders all panels and never raises on any fixture."""
    model, now = replay(any_fixture)
    text = render(model.derive(now))
    for heading in ("-- roots", "-- sdk dispatches", "-- cdp legs", "-- arcs",
                    "-- attention"):
        assert heading in text
    assert model.derive(now).fingerprint in text


def test_watch_surfaces_divergence_and_missing_proof() -> None:
    """The reference View must make the two quiet defects visible."""
    gs2, gs2_now = replay("gs2-dual-emitter.jsonl")
    assert "DIVERGENT" in render(gs2.derive(gs2_now))
    cdp, cdp_now = replay("cdp-leg.jsonl")
    assert "NO-PROOF" in render(cdp.derive(cdp_now))


def test_watch_entry_emits_decodable_json_frames(capsys) -> None:
    """``python -m dispatch_monitor_core --watch --format json`` is machine-readable."""
    from scripts.model_manager.ui.dispatch_monitor.core.__main__ import main

    exit_code = main(
        [
            "--watch",
            fixture_path("charter-admit-run-terminal.jsonl"),
            "--format",
            "json",
        ]
    )
    assert exit_code == 0
    lines = [row for row in capsys.readouterr().out.splitlines() if row.strip()]
    kinds = [json.loads(line)["kind"] for line in lines]
    assert kinds == [FRAME_HANDSHAKE, FRAME_SNAPSHOT]
    _, projection = ProjectionCodec.decode_frame(lines[-1])
    assert any(row.root_id == "5852" for row in projection.roots)


def test_watch_entry_suppresses_the_duplicate_redelivery(capsys) -> None:
    """Fingerprint suppression must swallow the fixture's duplicate ``scanned``.

    Fixture 1 carries one deliberate duplicate -- what a ``resume_from`` overlap
    re-delivers. Folding it changes only the ingest odometers, which are excluded
    from the hash, so exactly one frame must disappear under suppression.
    """
    from scripts.model_manager.ui.dispatch_monitor.core.__main__ import main

    path = fixture_path("charter-admit-run-terminal.jsonl")
    main(["--watch", path, "--frames", "each", "--format", "json"])
    unsuppressed = len(capsys.readouterr().out.splitlines())
    main(
        [
            "--watch",
            path,
            "--frames",
            "each",
            "--format",
            "json",
            "--suppress-unchanged",
        ]
    )
    suppressed = len(capsys.readouterr().out.splitlines())
    assert unsuppressed - suppressed == 1


def test_duplicate_redelivery_does_not_move_the_fingerprint() -> None:
    """The same property asserted directly against the Model, without the harness."""
    model = Model()
    scan = Event(
        "manage.charter.tick.scanned", 1_000, {"roots": 2, "admitted": 1}, seq=7
    )
    model.apply(scan)
    before = model.derive(2_000)
    model.apply(scan)
    after = model.derive(2_000)
    assert after.fingerprint == before.fingerprint
    assert after.health.records_folded == before.health.records_folded + 1
