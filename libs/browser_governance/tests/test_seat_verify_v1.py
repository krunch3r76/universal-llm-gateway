from __future__ import annotations

import http.server
import threading
import tomllib
from pathlib import Path

import pytest
from browser_governance.contract import STANDING_HOUSES

G1_TEST_PORT_RANGE = range(19100, 19110)
EVENT_SINK = "browser_governance.events.emit_browser_event"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PINS_PATH = _REPO_ROOT / "services" / "jupiter-cdp" / "pins.toml"


@pytest.fixture
def emitted(monkeypatch):
    events = []
    monkeypatch.setattr(EVENT_SINK, lambda e, **kw: events.append(e) or True)
    return events


def _serve_cdp_json_list(port):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'[{"type":"page"}]'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _stop(srv):
    srv.shutdown()
    srv.server_close()


def test_v1_4a_seat_snapshot_diff_reports_lost_house(g1_registry_isolation, emitted):
    from browser_governance.seat_verify import seat_snapshot_diff, verify_seats

    port_a, port_b = G1_TEST_PORT_RANGE[2], G1_TEST_PORT_RANGE[3]
    catalog = []
    for owner, port in (("fake-a", port_a), ("fake-b", port_b)):
        profile = g1_registry_isolation.tmp_path / "houses" / owner
        profile.mkdir(parents=True)
        catalog.append({"owner_id": owner, "cdp_port": port, "profile": str(profile)})
    srv_a, srv_b = _serve_cdp_json_list(port_a), _serve_cdp_json_list(port_b)
    try:
        before = verify_seats(catalog=catalog)
        assert [(r["owner_id"], r["reachable"], r["page_targets"], r["profile_present"]) for r in before] == [
            ("fake-a", True, 1, True),
            ("fake-b", True, 1, True),
        ]
        assert seat_snapshot_diff(before, verify_seats(catalog=catalog)) == []
        _stop(srv_b)
        srv_b = None
        after = verify_seats(catalog=catalog)
    finally:
        for srv in (srv_a, srv_b):
            if srv is not None:
                _stop(srv)
    diff = seat_snapshot_diff(before, after)
    assert [d["owner_id"] for d in diff if d.get("lost")] == ["fake-b"]
    seat_events = [e.payload for e in emitted if e.signal == "browser.seat.verification"]
    assert [(p["diff_count"], p["houses_checked"], p["lost_owner_ids"]) for p in seat_events] == [
        (0, 2, []),
        (1, 2, ["fake-b"]),
    ]


def test_v1_4b_standing_house_catalog_from_pins():
    from browser_governance.seat_verify import standing_house_catalog

    io_expected = {
        owner: owner
        for owner in STANDING_HOUSES.values()
        if str(owner).startswith("io:")
    }
    jupiter_expected = {
        owner: owner
        for owner in STANDING_HOUSES.values()
        if str(owner).startswith("jupiter:")
    }

    io_rows = standing_house_catalog(host="io", pins_path=_PINS_PATH)
    jupiter_rows = standing_house_catalog(host="jupiter", pins_path=_PINS_PATH)

    assert len(io_rows) == len(io_expected) == 5
    assert len(jupiter_rows) == len(jupiter_expected) == 4
    assert {r["owner_id"] for r in io_rows} == set(io_expected)
    assert {r["owner_id"] for r in jupiter_rows} == set(jupiter_expected)

    for row in io_rows + jupiter_rows:
        port = int(str(row["owner_id"]).rsplit(":", 1)[-1])
        assert row["cdp_port"] == port

    with _PINS_PATH.open("rb") as f:
        lanes = tomllib.load(f).get("lanes", {})
    standing_ports = {
        int(lane["port"])
        for lane in lanes.values()
        if isinstance(lane, dict) and lane.get("standing") is True
    }
    jupiter_ports = {r["cdp_port"] for r in jupiter_rows}
    assert jupiter_ports == standing_ports
    for row in jupiter_rows:
        assert row.get("profile") is not None
