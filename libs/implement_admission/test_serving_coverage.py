"""Unmapped serving coverage is distinguishable from declared-unserved."""

from __future__ import annotations

import implement_admission.service_lib_ownership as ownership
from implement_admission.injector_map import nominations_for_lib_path
from implement_admission.propagation_row import rows_from_lib_consumers
from implement_admission.serving_coverage import (
    UNMAPPED_PREFIX,
    path_serving_coverage,
    unmapped_serving_line,
    unmapped_top_level_libs,
    unserved_line,
)
from implement_admission.service_lib_ownership import declared_services_for_lib


_UNMAPPED_INSTANCE = "libs/implement_admission/service_lib_ownership.py"
_REPLAY_WAIT_STATUS = "libs/agent_bus_store/wait_status.py"


def test_implement_admission_is_unmapped_worked_instance() -> None:
    """Worked instance: implement_admission has no serves/CONSUMERS/INJECTORS."""
    assert nominations_for_lib_path(_UNMAPPED_INSTANCE) == ()
    assert path_serving_coverage(_UNMAPPED_INSTANCE) == "unmapped"
    line = unmapped_serving_line(_UNMAPPED_INSTANCE)
    assert line.startswith(UNMAPPED_PREFIX)
    assert "implement_admission" in line
    assert "declared unserved" not in line


def test_declared_unserved_is_distinguishable_from_unmapped(monkeypatch) -> None:
    """Correctly-nothing must not share the unmapped prefix or escalation."""
    monkeypatch.setattr(
        ownership, "UNSERVED_LIBS", frozenset({"implement_admission"})
    )
    assert path_serving_coverage(_UNMAPPED_INSTANCE) == "unserved"
    served = unserved_line(_UNMAPPED_INSTANCE)
    unmapped = unmapped_serving_line(_UNMAPPED_INSTANCE)
    assert served.startswith("libs_touched:")
    assert "declared unserved" in served
    assert not served.startswith(UNMAPPED_PREFIX)
    assert unmapped.startswith(UNMAPPED_PREFIX)
    assert served != unmapped


def test_unmapped_structured_escalation_is_not_a_restart_row() -> None:
    rows, escalations = rows_from_lib_consumers(
        [_UNMAPPED_INSTANCE],
        code_ref="unmapped-sha",
    )
    assert rows == []
    assert any(line.startswith(UNMAPPED_PREFIX) for line in escalations)
    assert all("sync_restart:" not in line for line in escalations)


def test_declared_unserved_structured_mint_is_silent(monkeypatch) -> None:
    """Correctly-nothing: no row and no unmapped escalation."""
    monkeypatch.setattr(
        ownership, "UNSERVED_LIBS", frozenset({"implement_admission"})
    )
    rows, escalations = rows_from_lib_consumers(
        [_UNMAPPED_INSTANCE],
        code_ref="unserved-sha",
    )
    assert rows == []
    assert escalations == []


def test_wait_status_replay_33083d61_still_nominates_agent_bus() -> None:
    """Replay must stay nominated; coverage must not rewrite a declared serve."""
    serving_rows, escalations = rows_from_lib_consumers(
        [_REPLAY_WAIT_STATUS],
        code_ref="33083d61",
    )
    services = {row.service for row in serving_rows}
    owned = set(declared_services_for_lib("agent_bus_store"))
    assert "agent_bus" in services
    assert services <= {"agent_bus", "mcp"}
    assert not owned <= services
    assert path_serving_coverage(_REPLAY_WAIT_STATUS) == "nominated"
    assert all(not line.startswith(UNMAPPED_PREFIX) for line in escalations)


def test_census_lists_implement_admission_not_agent_bus_store() -> None:
    """Worker-hosted census is a query, not a fail-closed empty-set gate."""
    census = unmapped_top_level_libs()
    assert "implement_admission" in census
    assert "agent_bus_store" not in census
    assert "cortex_store" not in census
    assert "cdp_ask" not in census
