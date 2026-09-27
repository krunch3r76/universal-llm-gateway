"""G7 master-land lease names the holder when a second land waits."""

from __future__ import annotations

import pytest
from git_integrate.schema import RC_LAND_LEASE_TIMEOUT

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_land_lease import (
    LandLeaseAcquireTimeout,
    acquire_land_lease_blocking,
    land_lease_timeout_envelope,
    release_land_lease,
    try_acquire_land_lease,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


@pytest.mark.asyncio
async def test_waiter_timeout_report_names_holder() -> None:
    lease_key = "/tmp/parallel-conductors-master"
    assert try_acquire_land_lease(lease_key=lease_key, holder_op_id="holder-a")
    with pytest.raises(LandLeaseAcquireTimeout) as excinfo:
        await acquire_land_lease_blocking(
            lease_key=lease_key,
            holder_op_id="waiter-b",
            timeout_s=0.05,
            poll_interval_s=0.01,
        )
    exc = excinfo.value
    assert exc.holder_op_id == "holder-a"
    assert exc.waiter_op_id == "waiter-b"
    assert exc.report["status"] == "timeout"
    envelope = land_lease_timeout_envelope(exc=exc)
    assert envelope["status"] == "rejected"
    assert envelope["reason_code"] == RC_LAND_LEASE_TIMEOUT
    assert envelope["land_lease_waiter"]["holder_op_id"] == "holder-a"
    assert release_land_lease(lease_key=lease_key, holder_op_id="holder-a")
