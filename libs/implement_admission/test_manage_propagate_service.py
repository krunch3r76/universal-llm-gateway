"""Propagate admits manage as an external reexec, not an in-process sync_restart."""

from __future__ import annotations

import pytest

from implement_admission.propagation_admit_validation import (
    MANAGE_SERVICE_SLUGS,
    legal_proof_classes,
    validate_service_slug,
)
from implement_admission.propagation_row import default_proof_class, default_safe_window

pytestmark = pytest.mark.offline

_BODY = """\
TYPE: DIRECTIVE
contract: propagate
scope: propagation sync_restart manage
effects_expected: manage whoami pid changes and code_version matches the row
"""


def test_manage_is_a_propagate_slug_and_not_an_in_process_service() -> None:
    assert validate_service_slug("manage") is None
    assert "manage" in MANAGE_SERVICE_SLUGS
    assert "process_live" in legal_proof_classes("manage")
    assert default_proof_class("manage") == "process_live"
    assert default_safe_window("manage") == "standalone_ok"

    from scripts.model_manager.ui.api_dispatch import (
        SYNC_RESTART_SERVICES,
        VALID_SERVICES,
    )

    assert "manage" not in VALID_SERVICES
    assert "manage" not in SYNC_RESTART_SERVICES


