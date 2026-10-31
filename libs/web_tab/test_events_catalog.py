"""AC4: webtab.probe.completed is registered in the event factory catalog."""

from __future__ import annotations

from web_tab.events import webtab_probe_completed


def test_webtab_probe_completed_signal() -> None:
    event = webtab_probe_completed(house_id="21207", target_id="t", tab_count=1)
    assert event.signal == "webtab.probe.completed"
    assert event.role == "observation"
