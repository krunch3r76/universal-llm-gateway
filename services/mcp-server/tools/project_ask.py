"""MCP project_ask tool — thin httpx relay to the Jupiter CDP-ask satellite.

Split submit / poll / abort only (F-2). No server-side poll loop; no Playwright
or claude_bundles imports in this module.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Literal

import httpx
from mcp_events import record

if TYPE_CHECKING:
    from fastmcp import FastMCP


def _project_ask_url() -> str:
    return os.environ.get("PROJECT_ASK_URL", "").strip()


def _relay(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    base = _project_ask_url()
    if not base:
        return {
            "error": (
                "PROJECT_ASK_URL not configured. Start the cdp-ask satellite on "
                "Jupiter and set PROJECT_ASK_URL=http://HOST:PORT in the MCP "
                "server environment."
            )
        }
    url = f"{base.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.request(method, url, json=json_body)
            resp.raise_for_status()
            if resp.content:
                return resp.json()
            return {"ok": True}
    except httpx.HTTPStatusError as exc:
        record(
            "mcp.project_ask.relay.failed",
            path=path,
            kind="http_status",
            status=exc.response.status_code,
        )
        detail = exc.response.text[:400]
        return {
            "error": f"project-ask HTTP {exc.response.status_code}",
            "detail": detail,
        }
    except httpx.RequestError as exc:
        record("mcp.project_ask.relay.failed", path=path, kind="unreachable")
        return {"error": f"project-ask unreachable: {exc}"}


def register_project_ask_tool(mcp: FastMCP) -> None:
    """Register the project_ask relay on *mcp*."""

    @mcp.tool(title="CDP Project Ask")
    def project_ask(
        op: Literal["submit", "poll", "abort"],
        execution_id: str | None = None,
        prompt_text: str | None = None,
        prompt_uri: str | None = None,
        prompt_path: str | None = None,
        holder: str = "mcp-project-ask",
        purpose: str = "ask",
        model: str = "opus-4.8",
        converse: bool = False,
        no_project_uuid: bool = False,
        project_uuid: str = "",
        ensure_cowork_auto: bool = True,
        chat_compose: bool = False,
        archive_path: str | None = None,
        delete_after: bool | None = None,
        timeout_s: int = 360,
    ) -> dict[str, Any]:
        """Submit, poll, or abort a Jupiter CDP sealed project-ask execution.

        Thin relay to the ``PROJECT_ASK_URL`` satellite (``libs/cdp_ask/``).
        Use from any vortex-code seat without hub checkout SSH.

        Ops (client must poll — no server-side wait loop):
          submit — POST execution; returns ``execution_id`` + ``status: running``
          poll — GET execution status; ``archive_uri`` present when completed
          abort — cancel in-flight execution and release CDP lane

        POLL GUARDRAIL — project-ask executions:
          NEVER curl, fetch, or HTTP GET/POST to localhost/127.0.0.1 — especially
          :8765 — for ``/v1/project-ask/*``. Port 8765 is web-fetcher, NOT
          project-ask. The cdp-ask satellite listens on :8770 (``PROJECT_ASK_URL``).
          Client polls ONLY via this MCP tool: ``project_ask(op="poll",
          execution_id="<id>")`` — repeat until ``archive_uri`` is set. This handler
          is a thin relay to ``PROJECT_ASK_URL``; there is NO server-side poll loop.
          Completion proof: poll response ``archive_uri`` (cortex:// harvest). Verify
          via ``mcp.project_ask.poll`` events. CLI/SSH dogfood
          (``claude-ai-sync-jupiter project-ask``) is hub-checkout fallback only.

        Prompt ingress (priority: inline > cortex URI > Jupiter path):
          prompt_text — inline sealed prompt body
          prompt_uri — ``cortex://…`` resolved on Jupiter under CORTEX_FILES_ROOT
          prompt_path — checkout-relative or absolute path on Jupiter PROJECT_ROOT

        Path-sim R-admit defaults: ``converse=true``, ``no_project_uuid=true``,
        ``model=opus-4.8``, ``purpose=ask``. Converse retains the Cowork chat by
        default; set ``delete_after=false`` on single-ask to retain for inspect.

        Args:
            op: submit | poll | abort
            execution_id: Required for poll/abort
            prompt_text: Inline prompt for submit
            prompt_uri: cortex:// prompt for submit
            prompt_path: Jupiter-readable file path for submit
            holder: Registry holder id
            purpose: Registry purpose tag (default ask)
            model: Live CDP picker model pattern
            converse: Multi-turn /new consult
            no_project_uuid: Use https://claude.ai/new instead of Project UUID
            project_uuid: Explicit Cowork Project UUID (when not no_project_uuid)
            ensure_cowork_auto: Cowork + Automatically approve on /new (default).
                Set false only with chat_compose=true (operator opt-in — friction 25051).
            chat_compose: Operator opt-in Chat on /new. Agents must not set without
                explicit operator override (CDP Send path broken — friction 25051).
            archive_path: Optional harvest archive path on Jupiter
            delete_after: Retain (false) or delete (true) chat after harvest; omit
                for satellite defaults (converse/turn-2 retain, single-ask delete)
            timeout_s: Idle completion budget forwarded to satellite

        Returns:
            submit: {execution_id, status, registration_id?}
            poll: {execution_id, status, archive_uri?, ok?, body?, error?, …}
            abort: {execution_id, status, aborted, attested, still_attached,
                abort_outcome, stop_clicked?}
        """
        if op == "submit":
            if chat_compose:
                ensure_cowork_auto = False
            body = {
                k: v
                for k, v in {
                    "prompt_text": prompt_text,
                    "prompt_uri": prompt_uri,
                    "prompt_path": prompt_path,
                    "holder": holder,
                    "purpose": purpose,
                    "model": model,
                    "converse": converse,
                    "no_project_uuid": no_project_uuid,
                    "project_uuid": project_uuid,
                    "ensure_cowork_auto": ensure_cowork_auto,
                    "archive_path": archive_path,
                    "delete_after": delete_after,
                    "timeout_s": timeout_s,
                }.items()
                if v is not None and v != ""
            }
            if not any(
                body.get(k) for k in ("prompt_text", "prompt_uri", "prompt_path")
            ):
                return {
                    "error": "submit requires prompt_text, prompt_uri, or prompt_path"
                }
            result = _relay("POST", "/v1/project-ask/executions", json_body=body)
            record("mcp.project_ask.submit", ok="error" not in result)
            return result

        if not execution_id:
            return {"error": f"{op} requires execution_id"}

        if op == "poll":
            result = _relay("GET", f"/v1/project-ask/executions/{execution_id}")
            record(
                "mcp.project_ask.poll",
                execution_id=execution_id,
                status=result.get("status"),
            )
            return result

        result = _relay(
            "POST",
            f"/v1/project-ask/executions/{execution_id}/abort",
        )
        record(
            "mcp.project_ask.abort",
            execution_id=execution_id,
            status=result.get("status"),
            aborted=result.get("aborted"),
            attested=result.get("attested"),
            still_attached=result.get("still_attached"),
            abort_outcome=result.get("abort_outcome"),
        )
        return result
