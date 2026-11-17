"""team_dispatch MCP wire for per-dispatch Cowork Project selection."""

from __future__ import annotations

import asyncio
from typing import Any, get_args, get_type_hints
from unittest.mock import patch

import pytest
from pydantic.fields import FieldInfo

from tools.frontier import register_frontier_tools

_PROJECT_FIELDS = ("project_uuid", "project_binding", "claude_project_name")
_EXPLICIT_UUID = "0198c0de-1111-7000-8000-00000000beef"


class _Recorder:
    """FastMCP duck-type that keeps the registered tool function."""

    def __init__(self) -> None:
        self.functions: dict[str, Any] = {}

    def tool(self, **_kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.functions[fn.__name__] = fn
            return fn

        return decorator


def _team_dispatch() -> Any:
    recorder = _Recorder()
    register_frontier_tools(recorder)  # type: ignore[arg-type]
    return recorder.functions["team_dispatch"]


def _description(name: str) -> str:
    """Schema description FastMCP publishes for one team_dispatch parameter."""
    hint = get_type_hints(_team_dispatch(), include_extras=True)[name]
    infos = [arg for arg in get_args(hint) if isinstance(arg, FieldInfo)]
    assert infos, f"{name} must carry Annotated[..., Field(description=...)]"
    return infos[0].description or ""


@pytest.mark.parametrize("name", _PROJECT_FIELDS)
def test_project_field_scoped_to_cdp_cowork(name: str) -> None:
    """Breaks when a Project field loses its CDP-only / code-surface scope note."""
    desc = _description(name)
    assert "model=cdp/" in desc, desc
    assert "claude_surface=code" in desc, desc


def test_project_fields_document_precedence_and_refusals() -> None:
    """Breaks when the schema stops telling a caller which field wins, or why 422."""
    uuid_desc = _description("project_uuid")
    assert (
        "project_uuid > project_binding > claude_project_name > default Project"
        in uuid_desc
    ), uuid_desc
    assert "cdp_project_uuid_invalid" in uuid_desc, uuid_desc

    binding_desc = _description("project_binding")
    assert "project_uuid is omitted" in binding_desc, binding_desc
    assert "outranks claude_project_name" in binding_desc, binding_desc
    assert "cdp_project_binding_missing" in binding_desc, binding_desc

    name_desc = _description("claude_project_name")
    assert "Lowest precedence" in name_desc, name_desc
    assert "mission_kind=hop" in name_desc, name_desc


def _relayed_generate_body(**project_fields: str) -> dict[str, Any]:
    """Call team_dispatch generate; return the body relayed to Stargate."""
    relayed: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relayed.append(body)
        return {"execution_id": "exec-project-bind", "thread_id": "24076"}

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record", side_effect=lambda *_a, **_k: None),
    ):
        result = asyncio.run(
            _team_dispatch()(
                op="generate",
                contract="freeform",
                dispatch_thread_id="24076",
                model="cdp/opus-5.5-extra",
                prompt="hello",
                **project_fields,
            )
        )
    assert "error" not in result, result
    assert len(relayed) == 1
    return relayed[0]


def test_generate_relays_project_fields_verbatim() -> None:
    """All three reach Stargate: precedence is decided at admit, not in the relay."""
    body = _relayed_generate_body(
        project_uuid=_EXPLICIT_UUID,
        project_binding="impresario",
        claude_project_name="Grand Impresario",
    )
    assert body["project_uuid"] == _EXPLICIT_UUID
    assert body["project_binding"] == "impresario"
    assert body["claude_project_name"] == "Grand Impresario"


def test_generate_omits_unset_project_fields() -> None:
    """Breaks when an ordinary dispatch starts carrying null Project keys."""
    body = _relayed_generate_body()
    for name in _PROJECT_FIELDS:
        assert name not in body
