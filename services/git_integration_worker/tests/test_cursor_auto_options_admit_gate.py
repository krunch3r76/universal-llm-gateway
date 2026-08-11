"""Admit-gate tests for symmetric ``## options`` menus (todo:operator-packet-ac-and-menu-shapes)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.git_integration_worker.cursor_auto.admit_gates import blocking_admit_gate
from services.git_integration_worker.cursor_auto.options_admission import (
    admit_options_body,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob

_BASE_DIRECTIVE = (
    "TYPE: DIRECTIVE\n"
    "density: dense\n"
    "arc: agent-bus:6655 / options-gate\n"
    "## Scope\n"
    "libs/implement_admission/\n"
    "vision: mechanical — options symmetry gate test\n"
)

_SYMMETRIC_OPTIONS = """\
## options

```yaml
options:
  - id: lean_path
    cost: low effort
    benefit: fast delivery
    falsifier: tests pass without refactor
  - id: full_path
    cost: higher effort
    benefit: durable structure
    falsifier: tests pass after modular split
```
"""

_ASYMMETRIC_OPTIONS = """\
## options

```yaml
options:
  - id: lean_path
    cost: low effort
    benefit: fast delivery
    falsifier: tests pass without refactor
  - id: full_path
    cost: higher effort
    benefit: durable structure
```
"""


def _pass_through_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_status",
        AsyncMock(return_value="active"),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
        AsyncMock(return_value=[]),
    )


def _bus_client() -> AsyncMock:
    client = AsyncMock()
    client.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    return client


def _implement_job(body: str, *, contract: str = "implement") -> AutoJob:
    return AutoJob(
        job_id="j-options-gate",
        thread_id="6655",
        turn_number=1,
        subject="options gate",
        body=body,
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract=contract,
    )


@pytest.mark.asyncio
async def test_state3_ac1_asymmetric_options_blocked_after_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1 post-fix: asymmetric required keys refuse nest."""
    _pass_through_gates(monkeypatch)
    blocked = await blocking_admit_gate(
        _implement_job(_BASE_DIRECTIVE + _ASYMMETRIC_OPTIONS),
        client=_bus_client(),
        queue=MagicMock(),
    )
    assert blocked is not None
    assert blocked["terminal_status"] == "status:blocked"
    assert "full_path" in blocked["summary"]
    assert "falsifier" in blocked["summary"]


def test_state1_no_options_block_admits() -> None:
    admission = admit_options_body(_BASE_DIRECTIVE)
    assert admission.approved


def test_state2_symmetric_options_admit() -> None:
    admission = admit_options_body(_BASE_DIRECTIVE + _SYMMETRIC_OPTIONS)
    assert admission.approved


def test_state3_required_key_sets_differ_blocked() -> None:
    admission = admit_options_body(_BASE_DIRECTIVE + _ASYMMETRIC_OPTIONS)
    assert not admission.approved
    assert admission.error is not None
    assert admission.error["reason"] == "options_required_key_missing"
    assert admission.error["option_id"] == "full_path"
    assert admission.error["missing_key"] == "falsifier"


def test_state4_single_option_blocked() -> None:
    body = (
        _BASE_DIRECTIVE
        + """\
## options

```yaml
options:
  - id: only_path
    cost: low
    benefit: fast
    falsifier: tests pass
```
"""
    )
    admission = admit_options_body(body)
    assert not admission.approved
    assert admission.error is not None
    assert admission.error["reason"] == "options_single_option"
    assert admission.error["option_id"] == "only_path"


def test_state5_empty_options_list_blocked() -> None:
    body = (
        _BASE_DIRECTIVE
        + """\
## options

```yaml
options: []
```
"""
    )
    admission = admit_options_body(body)
    assert not admission.approved
    assert admission.error is not None
    assert admission.error["reason"] == "options_block_empty"


def test_state6_unparseable_yaml_blocked() -> None:
    body = (
        _BASE_DIRECTIVE
        + """\
## options

```yaml
options:
  - id: broken
    cost: [unclosed
```
"""
    )
    admission = admit_options_body(body)
    assert not admission.approved
    assert admission.error is not None
    assert admission.error["reason"] == "options_block_unparseable"
    assert "options_yaml_parse_error" in admission.error["parse_error"]


def test_state7_symmetric_extras_admit() -> None:
    body = (
        _BASE_DIRECTIVE
        + """\
## options

```yaml
options:
  - id: a
    cost: low
    benefit: fast
    falsifier: smoke
    note: same on both
  - id: b
    cost: high
    benefit: durable
    falsifier: integration
    note: same on both
```
"""
    )
    admission = admit_options_body(body)
    assert admission.approved


def test_state8_asymmetric_extras_blocked() -> None:
    body = (
        _BASE_DIRECTIVE
        + """\
## options

```yaml
options:
  - id: a
    cost: low
    benefit: fast
    falsifier: smoke
    note: only on a
  - id: b
    cost: high
    benefit: durable
    falsifier: integration
```
"""
    )
    admission = admit_options_body(body)
    assert not admission.approved
    assert admission.error is not None
    assert admission.error["reason"] == "options_key_set_asymmetric"
    assert admission.error["option_id"] == "b"
    assert admission.error["missing_key"] == "note"


def test_state9_empty_required_value_blocked() -> None:
    body = (
        _BASE_DIRECTIVE
        + """\
## options

```yaml
options:
  - id: a
    cost: low
    benefit: fast
    falsifier: "   "
  - id: b
    cost: high
    benefit: durable
    falsifier: integration
```
"""
    )
    admission = admit_options_body(body)
    assert not admission.approved
    assert admission.error is not None
    assert admission.error["reason"] == "options_required_key_empty"
    assert admission.error["option_id"] == "a"
    assert admission.error["missing_key"] == "falsifier"


@pytest.mark.asyncio
async def test_state10_no_directive_type_gate_does_not_fire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = (
        "density: dense\n"
        "## Scope\n"
        "libs/foo\n"
        + _ASYMMETRIC_OPTIONS
    )
    _pass_through_gates(monkeypatch)
    result = await blocking_admit_gate(
        _implement_job(body),
        client=_bus_client(),
        queue=MagicMock(),
    )
    assert result is None


@pytest.mark.asyncio
async def test_state11_verify_contract_gate_does_not_fire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_through_gates(monkeypatch)
    result = await blocking_admit_gate(
        _implement_job(_BASE_DIRECTIVE + _ASYMMETRIC_OPTIONS, contract="verify"),
        client=_bus_client(),
        queue=MagicMock(),
    )
    assert result is None


@pytest.mark.asyncio
async def test_gate_fires_on_investigate_and_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_through_gates(monkeypatch)
    for contract in ("investigate", "seed"):
        blocked = await blocking_admit_gate(
            _implement_job(_BASE_DIRECTIVE + _ASYMMETRIC_OPTIONS, contract=contract),
            client=_bus_client(),
            queue=MagicMock(),
        )
        assert blocked is not None, contract
        assert "falsifier" in blocked["summary"], contract
