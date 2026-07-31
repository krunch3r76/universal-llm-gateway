"""cdp-ask /health self-reports code_version so propagation is checkable."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cdp_ask.app import create_app


@pytest.mark.offline
def test_health_carries_code_version(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setenv("ULG_CODE_VERSION", "a" * 40)
    from deploy_identity import code_version

    code_version.resolve_code_version.cache_clear()
    app = create_app()
    with TestClient(app) as client:
        payload = client.get("/health").json()
    assert payload["code_version"] == "a" * 40
    assert payload["status"] in {"ok", "fail_closed"}
