"""Refuse a team_dispatch prompt that is only an unexpanded ``$file:`` token.

Breaks when prompt admission forwards the literal to the generate branch,
including a whitespace-padded token. A prompt that merely mentions ``$file:``
must still be admitted.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline

_FIX_HINT = (
    "the $file: token was not expanded by the caller; "
    "send the body inline or via sidecar_content"
)
_TOKEN = "$file:/workspace/rev4/done39520.txt"
_CODE = "prompt_unexpanded_file_token"


def _client() -> TestClient:
    root = Path(__file__).resolve().parents[4]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "services" / "universal-stargate"))
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError

    from systems.frontier_consult.route import team_router
    from systems.proxy.app import validation_exception_handler

    surface = FastAPI()
    surface.include_router(team_router)
    surface.add_exception_handler(RequestValidationError, validation_exception_handler)
    return TestClient(surface)


def _body(prompt: str) -> dict[str, str]:
    return {
        "op": "generate",
        "job": "freeform",
        "role": "synthesizer",
        "prompt": prompt,
        "dispatch_thread_id": "dt-file-token",
    }


def _assert_refused(response) -> None:
    assert response.status_code == 422, response.text
    payload = response.json()
    assert payload["error"]["code"] == _CODE
    assert payload["details"]["fix_hint"] == _FIX_HINT


def test_team_dispatch_refuses_whole_value_file_token_prompt() -> None:
    """Whole-value ``$file:`` prompt is 422; mid-sentence prompt is admitted."""
    client = _client()
    with patch(
        "systems.frontier_consult.api_role_generate.dispatch_api_role_generate",
        new_callable=AsyncMock,
        return_value={"admitted": True},
    ) as admitted:
        exact = client.post("/api/v1/team/dispatch", json=_body(_TOKEN))
        _assert_refused(exact)

        padded = client.post(
            "/api/v1/team/dispatch",
            json=_body(f"  {_TOKEN}\n"),
        )
        _assert_refused(padded)

        mixed = client.post(
            "/api/v1/team/dispatch",
            json=_body(f"The sender left {_TOKEN} in the prompt."),
        )
        assert mixed.status_code == 202, mixed.text
        assert mixed.json().get("admitted") is True
        assert _CODE not in mixed.text
        assert admitted.await_count == 1
