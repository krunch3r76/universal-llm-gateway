"""Tests for ``contract: propagate`` operator restart requests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_auto.directive import has_actionable_scope
from services.git_integration_worker.cursor_auto.propagate_admission import (
    admit_propagate_body,
)

_RESOLVED_HEAD_SHA = "deadbeef00000000000000000000000000000000"


@pytest.fixture(autouse=True)
def _resolve_propagate_test_code_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pad test tokens to resolvable 40-char SHAs for admit enrichment paths."""

    def _fake(code_ref: str, *, service: str | None = None) -> str:
        text = str(code_ref).strip().lower()
        if text == "head":
            return _RESOLVED_HEAD_SHA
        if len(text) >= 40 and all(c in "0123456789abcdef" for c in text):
            return text[:40]
        return (text + "0" * 40)[:40]

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.propagate_admission.require_resolvable_code_ref",
        _fake,
    )
    monkeypatch.setattr(
        "charter_runner_store.propagation_code_ref_mint.require_resolvable_code_ref",
        _fake,
    )


_MCP_YAML_BODY = """\
TYPE: DIRECTIVE
contract: propagate
scope: propagation sync_restart
effects_expected: propagation row persisted; restart executed or deferred

## propagation
```yaml
propagation:
  - service: mcp
    code_ref: deadbeef
    proof_class: client_visible
```
"""

_SHORTHAND_BODY = """\
TYPE: DIRECTIVE
contract: propagate
scope: propagation sync_restart mcp
code_ref: cafebabe
effects_expected: propagation row persisted; restart executed or deferred
"""


def test_propagation_scope_is_actionable() -> None:
    assert has_actionable_scope("scope: propagation sync_restart mcp")
    assert has_actionable_scope("## propagation\n```yaml\npropagation: []\n```")


def test_admit_yaml_propagation_rows() -> None:
    admission = admit_propagate_body(_MCP_YAML_BODY)
    assert admission.approved
    assert len(admission.rows) == 1
    assert admission.rows[0].service == "mcp"
    assert admission.rows[0].code_ref == "deadbeef00000000000000000000000000000000"


def test_admit_shorthand_propagation() -> None:
    admission = admit_propagate_body(_SHORTHAND_BODY)
    assert admission.approved
    assert admission.rows[0].service == "mcp"
    assert admission.rows[0].code_ref == "cafebabe00000000000000000000000000000000"


def test_admit_shorthand_allow_self_preempt_false() -> None:
    """Shorthand path must parse allow_self_preempt, not default it to True."""
    body = """\
TYPE: DIRECTIVE
contract: propagate
scope: propagation sync_restart mcp
code_ref: HEAD
allow_self_preempt: false
effects_expected: propagation row persisted; restart executed or deferred
"""
    admission = admit_propagate_body(body)
    assert admission.approved
    assert admission.rows[0].allow_self_preempt is False
    assert "allow_self_preempt" in admission.consumed_keys


def test_admit_yaml_allow_self_preempt_false() -> None:
    body = _MCP_YAML_BODY.replace(
        "proof_class: client_visible",
        "proof_class: client_visible\n    allow_self_preempt: false",
    )
    admission = admit_propagate_body(body)
    assert admission.approved
    assert admission.rows[0].allow_self_preempt is False


def test_admit_yaml_allow_self_preempt_defaults_true() -> None:
    admission = admit_propagate_body(_MCP_YAML_BODY)
    assert admission.approved
    assert admission.rows[0].allow_self_preempt is True
    body = _SHORTHAND_BODY.replace("effects_expected:", "expected:")
    admission = admit_propagate_body(body)
    assert not admission.approved
    assert admission.error is not None
    assert admission.error["reason"] == "propagate_effects_expected_missing"


def test_admit_rejects_missing_rows() -> None:
    body = """\
TYPE: DIRECTIVE
contract: propagate
effects_expected: nothing to do
"""
    admission = admit_propagate_body(body)
    assert not admission.approved
    assert admission.error["reason"] == "propagate_rows_missing"


def test_admit_operator_mcp_version_pin_mints_mcp_health_only() -> None:
    """AC1: HEAD / version-pin operator propagate composes mcp_health + exclusion."""
    body = _SHORTHAND_BODY.replace("cafebabe", "HEAD")
    admission = admit_propagate_body(body)
    assert admission.approved
    row = admission.rows[0]
    assert row.close_surfaces == ("mcp_health",)
    assert row.excluded_surfaces is not None
    assert row.excluded_surfaces[0]["surface"] == "cortex_api"
    assert row.excluded_surfaces[0]["import_path"] == "not_probed"
    assert "cortex-api" not in row.proof.lower()
    assert "GET /health" in row.proof


def test_admit_operator_merge_sha_uses_paths_in_commit(tmp_path: Path) -> None:
    """AC3: explicit merge SHA recovers paths via diff-tree -m before compose-empty."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@e.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )
    lib_path = tmp_path / "libs" / "deploy_identity" / "__init__.py"
    lib_path.parent.mkdir(parents=True)
    lib_path.write_text("# base\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "libs/deploy_identity/__init__.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "base"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "-b", "feat"],
        check=True,
        capture_output=True,
    )
    lib_path.write_text("# changed\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "libs/deploy_identity/__init__.py"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "feat"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "checkout", "-"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "merge", "--no-ff", "feat", "-m", "merge"],
        check=True,
        capture_output=True,
    )
    merge_sha = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
    ).stdout.decode().strip()
    body = _MCP_YAML_BODY.replace("deadbeef", merge_sha)
    with patch(
        "implement_admission.propagation_close_surfaces.verify_consumer_import",
        side_effect=lambda slug, _path: "verified" if slug == "cortex_api" else "contradicted",
    ), patch(
        "services.git_integration_worker.cursor_auto.propagate_admission.require_resolvable_code_ref",
        return_value=merge_sha,
    ), patch(
        "services.git_integration_worker.cursor_auto.propagate_admission.load_config",
    ) as load_config:
        from services.git_integration_worker.config import WorkerConfig

        load_config.return_value = WorkerConfig(
            host="127.0.0.1",
            port=8091,
            source_repo=tmp_path,
            worktree_root=tmp_path / "wt",
            dispatch_workspace=tmp_path,
            green_gate_cmd=["true"],
        )
        admission = admit_propagate_body(body)
    assert admission.approved
    row = admission.rows[0]
    assert row.close_surfaces == ("mcp_health", "cortex_api")
    assert row.excluded_surfaces in (None, ())


@pytest.mark.asyncio
async def test_run_propagation_queues_when_manage_defers() -> None:
    from services.git_integration_worker.cursor_auto.handler_propagation import (
        run_propagation_in_seat,
    )
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    job = AutoJob(
        job_id="job-1",
        thread_id=6339,
        turn_number=1,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="restart git_integration_worker",
        body=_SHORTHAND_BODY.replace("mcp", "git_integration_worker"),
        contract="propagate",
        desired_model="auto",
        desired_effort="medium",
        require_attended=False,
        request_id="req-1",
    )
    calls: list[str] = []

    class _Queue:
        def mark_done(
            self,
            job_id: str,
            *,
            failed: bool = False,
            terminal_reason: str | None = None,
        ) -> None:
            calls.append(job_id)

    class _Client:
        async def reply(self, **kwargs):  # type: ignore[no-untyped-def]
            return type("R", (), {"status_code": 200, "body": ""})()

    with (
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.upsert_open_rows",
            return_value=["git_integration_worker:cafebabe:sync_restart"],
        ),
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.sync_restart_service",
            return_value={
                "status": "deferred",
                "state": "draining",
                "restart_intent_id": "intent-giw-1",
                "reason": "draining; completion delivered via git_worker.drain events",
            },
        ),
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.set_defer_reason",
        ),
    ):
        result = await run_propagation_in_seat(
            job,
            client=_Client(),
            queue=_Queue(),
            model={"requested": "auto", "resolved_model_id": "cursor/composer-2.5"},
            effort={"requested": None, "resolved_effort": "medium"},
            gate_plan={"action": "in_seat"},
        )
    assert result["disposition"] == "queued"
    summary = str(result.get("summary") or "")
    assert "queued" in summary.lower()
    assert "manage drain" in summary.lower()
    assert calls == ["job-1"]


@pytest.mark.asyncio
async def test_run_propagation_self_preempts_mcp_busy_deferral() -> None:
    """Operator bind: mcp busy=own CSE → auto force once; advise disconnect."""
    from services.git_integration_worker.cursor_auto.handler_propagation import (
        run_propagation_in_seat,
    )
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    job = AutoJob(
        job_id="job-mcp-busy",
        thread_id=6339,
        turn_number=2,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="restart mcp",
        body=_SHORTHAND_BODY,
        contract="propagate",
        desired_model="auto",
        desired_effort="medium",
        require_attended=False,
        request_id="req-mcp-busy",
    )

    class _Queue:
        def mark_done(
            self,
            job_id: str,
            *,
            failed: bool = False,
            terminal_reason: str | None = None,
        ) -> None:
            pass

    posted: list[dict[str, object]] = []

    class _Client:
        async def reply(self, **kwargs):  # type: ignore[no-untyped-def]
            posted.append(kwargs)
            return type("R", (), {"status_code": 200, "body": ""})()

    manage_calls: list[dict[str, object]] = []

    def _manage(service: str, *, reason: str = "", force: bool = False):
        manage_calls.append({"service": service, "force": force, "reason": reason})
        if not force:
            return {
                "status": "deferred",
                "state": "busy",
                "reason": "cdp_ask_live",
                "retry_after_s": 30,
            }
        return {"status": "ok", "service": service}

    with (
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.upsert_open_rows",
            return_value=["mcp:cafebabe:sync_restart"],
        ),
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.sync_restart_service",
            side_effect=_manage,
        ),
        patch(
            "scripts.model_manager.ui.controller.charter_runner.propagation_execute.dispatch_proof_probe",
            side_effect=[
                type(
                    "P",
                    (),
                    {
                        "error": None,
                        "payload": {"code_version": "cafebabe", "pid": 1},
                        "proof_class_requested": "client_visible",
                        "proof_class_executed": "client_visible",
                    },
                )(),
                type(
                    "P",
                    (),
                    {
                        "error": None,
                        "payload": {
                            "code_version": "cafebabe",
                            "pid": 2,
                            "process_start_time": "t2",
                        },
                        "proof_class_requested": "client_visible",
                        "proof_class_executed": "client_visible",
                    },
                )(),
            ],
        ),
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.proof_observed",
            return_value=True,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.close_row",
        ),
    ):
        result = await run_propagation_in_seat(
            job,
            client=_Client(),
            queue=_Queue(),
            model={"requested": "auto", "resolved_model_id": "cursor/composer-2.5"},
            effort={"requested": None, "resolved_effort": "medium"},
            gate_plan={"action": "in_seat"},
        )
    assert result["disposition"] == "propagated"
    assert len(manage_calls) == 2
    assert manage_calls[0]["force"] is False
    assert manage_calls[1]["force"] is True
    summary = str(result.get("summary") or "")
    assert "self-preempt" in summary.lower()
    assert "disconnect momentarily" in summary.lower()
    assert "MCP will disconnect momentarily" in summary

    assert posted
    payload = json.loads(str(posted[-1]["body"]))
    escalations = payload.get("self_preempt_escalations")
    assert escalations
    assert escalations[0]["service"] == "mcp"
    assert escalations[0]["preempted"] == "cdp_ask_live"


@pytest.mark.asyncio
async def test_run_propagation_self_preempt_vetoed_by_allow_self_preempt_false() -> None:
    """allow_self_preempt: false suppresses auto force on self-preemptable deferral."""
    from services.git_integration_worker.cursor_auto.handler_propagation import (
        run_propagation_in_seat,
    )
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    body = _MCP_YAML_BODY.replace(
        "proof_class: client_visible",
        "proof_class: client_visible\n    allow_self_preempt: false",
    )
    job = AutoJob(
        job_id="job-mcp-veto",
        thread_id=6339,
        turn_number=3,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="restart mcp no force",
        body=body,
        contract="propagate",
        desired_model="auto",
        desired_effort="medium",
        require_attended=False,
        request_id="req-mcp-veto",
    )

    class _Queue:
        def mark_done(
            self,
            job_id: str,
            *,
            failed: bool = False,
            terminal_reason: str | None = None,
        ) -> None:
            pass

    class _Client:
        async def reply(self, **kwargs):  # type: ignore[no-untyped-def]
            return type("R", (), {"status_code": 200, "body": ""})()

    manage_calls: list[dict[str, object]] = []

    def _manage(service: str, *, reason: str = "", force: bool = False):
        manage_calls.append({"service": service, "force": force})
        return {
            "status": "deferred",
            "state": "busy",
            "reason": "cdp_ask_live",
        }

    with (
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.upsert_open_rows",
            return_value=["mcp:deadbeef:sync_restart"],
        ),
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.sync_restart_service",
            side_effect=_manage,
        ),
        patch(
            "services.git_integration_worker.cursor_auto.handler_propagation.mark_harvest_wanted",
        ),
    ):
        result = await run_propagation_in_seat(
            job,
            client=_Client(),
            queue=_Queue(),
            model={"requested": "auto", "resolved_model_id": "cursor/composer-2.5"},
            effort={"requested": None, "resolved_effort": "medium"},
            gate_plan={"action": "in_seat"},
        )
    assert result["disposition"] == "harvest_wanted"
    assert len(manage_calls) == 1
    assert manage_calls[0]["force"] is False
    summary = str(result.get("summary") or "")
    assert "self-preempt vetoed" in summary.lower()
