"""Packet fill golden tests — template fields and verbatim detail."""

from __future__ import annotations

from life_intent.packet_fill import entity_seed_payload, fill_recon_packet, slug_from_subject


def test_slug_from_subject() -> None:
    assert slug_from_subject("Reminder Double Fire!") == "reminder-double-fire"


def test_fill_recon_packet_quotes_detail_verbatim() -> None:
    detail = 'The "reminder" fires twice — see logs.'
    intent = {
        "verb": "investigate",
        "subject": "reminder double-fire",
        "detail": detail,
        "refs": ["todo:reminder"],
        "urgency": "normal",
    }
    packet = fill_recon_packet(intent)
    assert detail in packet
    assert "contract: consult" in packet
    assert "dispatch_lane: code" in packet
    assert "authority_fork:stop" in packet
    assert "todo:life-intent-reminder-double-fire" in packet


def test_entity_seed_floor_for_fix() -> None:
    intent = {
        "verb": "fix",
        "subject": "login timeout",
        "detail": "Users logged out after thirty seconds consistently.",
        "urgency": "normal",
    }
    seed = entity_seed_payload(intent)
    assert seed is not None
    assert seed["type"] == "todo"
    assert seed["source_uri"].startswith("cortex://notes/system/specs/")
    assert seed["attributes"]["required_skills"]
    assert seed["attributes"]["priority"] == "medium"


def test_entity_seed_none_for_investigate() -> None:
    intent = {
        "verb": "investigate",
        "subject": "latency spike",
        "detail": "Dashboard loads slowly on Monday mornings.",
    }
    assert entity_seed_payload(intent) is None
