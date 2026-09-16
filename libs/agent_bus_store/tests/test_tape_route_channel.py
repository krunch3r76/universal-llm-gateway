"""Tape route channel validation (422 negative space)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from agent_bus_store.routes.threads.tape import tape_route

pytestmark = pytest.mark.offline


def test_tape_rejects_channel_hop() -> None:
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            tape_route(
                thread_id="10479",
                channel="hop",
            )
        )
    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert detail["code"] == "tape.channel_unknown"


def test_tape_rejects_unknown_channel() -> None:
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            tape_route(
                thread_id="10479",
                channel="banana",
            )
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "tape.channel_unknown"


def test_tape_accepts_continuity_channel() -> None:
    with (
        patch("agent_bus_store.routes.threads.tape.load_thread_tags", return_value={"role": "root"}),
        patch("agent_bus_store.routes.threads.tape.classify_thread", return_value={"spine": "root"}),
        patch(
            "agent_bus_store.routes.threads.tape.render_tape_with_harvest",
            return_value={"open_line": {}, "cells": []},
        ),
    ):
        result = asyncio.run(
            tape_route(
                thread_id="10479",
                channel="continuity",
            )
        )
    assert result["cells"] == []
