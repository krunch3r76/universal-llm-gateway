"""MCP project_ask tool — thin httpx relay to the Jupiter CDP-ask satellite.

Split submit / poll / abort only (F-2). No server-side poll loop; no Playwright
or claude_bundles imports in this module.

Transport vs bus (operator-proxy):
  ``project_ask`` is IDE↔Cowork **converse transport** (submit/poll/abort per
  ``execution_id``). ``abort`` cancels **only** that satellite execution — it does
  **not** stop ``agent_bus.request`` episodes on the operator's private request
  thread (cursor-auto admits, nested cursor-sdk, CLOSEOUT relay). After mission
  ``submit``, continuity for commissions is the **bus thread**, not this handle.
  Reconnect: warm ``submit`` with the same ``holder`` and ``converse=true`` may
  reattach a retained Cowork CSE; a dead ``execution_id`` cannot be re-polled.
  Doctrine: ``session-abort-authorization_ulg.mdc`` · ``cdp-operator-proxy``.
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
            "status_code": exc.response.status_code,
            "detail": detail,
        }
    except httpx.RequestError as exc:
        record("mcp.project_ask.relay.failed", path=path, kind="unreachable")
        return {"error": f"project-ask unreachable: {exc}"}


def register_project_ask_tool(mcp: FastMCP) -> None:
    """Register the project_ask relay on *mcp*."""

    @mcp.tool(title="CDP Project Ask")
    def project_ask(
        op: Literal["submit", "poll", "abort", "active_work"],
        execution_id: str | None = None,
        prompt_text: str | None = None,
        prompt_uri: str | None = None,
        prompt_path: str | None = None,
        holder: str = "mcp-project-ask",
        purpose: str = "ask",
        model: str = "opus-5",
        converse: bool = False,
        no_project_uuid: bool = False,
        project_uuid: str = "",
        ensure_cowork_auto: bool = True,
        chat_compose: bool = False,
        archive_path: str | None = None,
        delete_after: bool | None = None,
        timeout_s: int = 360,
        expected_size: Literal["small", "large", "auto"] = "auto",
        harvest_source: Literal["chat", "output-file", "auto"] = "auto",
        download_output: bool = False,
    ) -> dict[str, Any]:
        """Submit, poll, or abort a Jupiter CDP sealed project-ask execution.

        Thin relay to the ``PROJECT_ASK_URL`` satellite (``libs/cdp_ask/``).
        Use from any vortex-code seat without hub checkout SSH.

        Ops (client must poll — no server-side wait loop):
          submit — POST execution; returns ``execution_id`` + ``status: running``
          poll — GET execution status; dual-completion ladder on every response:
            ``completion_phase`` (running → turn_idle → content_proof → archiving →
            terminal | failed), optional ``content_proof_uri`` / ``content_proof_sha256`` /
            ``turn_idle_at``, and ``stall_stage`` on failed terminals. ``archive_uri``
            remains archive-proof after successful harvest.
          abort — cancel in-flight execution and release CDP lane
          active_work — in-flight satellite executions (no execution_id needed);
            the discovery path when you hold a Stargate/dispatch execution id
            rather than a satellite one. The two id spaces are disjoint: a
            ``cdp/*`` dispatch mints its own id and the satellite mints another,
            and only the satellite id polls (friction a:26175). Stargate-side
            correlation is the ``cdp.generate.submitted`` event, which carries
            both ids and now publishes at submit time.

        ``purpose``: default ``ask``. For operator-proxy missions set
        ``purpose=operator-proxy`` (or ``mission``) — the cdp-ask runner
        auto-ensures ``/cdp-operator-proxy`` + ``/reasoning-posture`` +
        ``/frontier-reasoning-discipline`` chips and the Opus-operator /
        Fable-advisor seat-map briefing
        (``libs/claude_bundles/operator_proxy_mission.py``).

        POLL GUARDRAIL — project-ask executions:
          NEVER curl, fetch, or HTTP GET/POST to localhost/127.0.0.1 — especially
          :8765 — for ``/v1/project-ask/*``. Port 8765 is web-fetcher, NOT
          project-ask. The cdp-ask satellite listens on :8770 (``PROJECT_ASK_URL``).
          Client polls ONLY via this MCP tool: ``project_ask(op="poll",
          execution_id="<id>")`` — repeat until ``archive_uri`` **or** consumer-verified
          ``content_proof`` (path-sim R-admit). This handler is a thin relay to
          ``PROJECT_ASK_URL``; there is NO server-side poll loop.
          Completion proof: poll ``archive_uri`` (archive-proof) **or**
          ``completion_phase=content_proof`` with consumer fs-read + sha re-verify
          (content-proof). Verify via ``mcp.project_ask.poll`` events.
          CLI/SSH dogfood (``claude-ai-sync-jupiter project-ask``) is hub-checkout
          fallback only.

        Prompt ingress (priority: inline > cortex URI > Jupiter path):
          prompt_text — inline sealed prompt body
          prompt_uri — ``cortex://…`` resolved on Jupiter under CORTEX_FILES_ROOT
          prompt_path — checkout-relative or absolute path on Jupiter PROJECT_ROOT

        Path-sim R-admit defaults: ``converse=true``, ``no_project_uuid=true``,
        ``model=opus-5``, ``purpose=ask``. Converse retains the Cowork chat by
        default; set ``delete_after=false`` on single-ask to retain for inspect.

        Harvest knobs (Cowork Outputs — ``todo:cdp-cowork-outputs-local-harvest``):
          expected_size — ``small`` | ``large`` | ``auto`` (default ``auto``).
            ``large`` with ``harvest_source=auto`` attempts Cowork Output download
            before archive; ``small`` never forces Output download.
          harvest_source — ``chat`` | ``output-file`` | ``auto`` (default ``auto``).
            Submit-time knob (distinct from poll ``harvest_provenance``). ``chat``
            archives scraped chat body only (legacy). ``output-file`` requires
            Output download — hard fail on miss. ``auto`` tries Output when
            ``expected_size=large`` or ``download_output=true``; on miss tries
            chat ``cortex://`` pointer; under ``expected_size=large`` refuses
            thin chat fallback (fail-closed).
          download_output — bool (default ``false``). When true (or with
            ``expected_size=large``), attempt Cowork Output download into the
            archive path before ``content_proof``.

        Args:
            op: submit | poll | abort | active_work
            execution_id: Required for poll/abort; satellite-minted id only
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
            expected_size: Deliverable size signal (small | large | auto)
            harvest_source: Submit-time harvest source (chat | output-file | auto); distinct from poll harvest_provenance
            download_output: Attempt Cowork Output download when true or large mode

        Returns:
            submit: {execution_id, status, registration_id?}
            poll: {execution_id, status, completion_phase, archive_uri?, content_proof_uri?,
                content_proof_sha256?, turn_idle_at?, stall_stage?, harvest_provenance?,
                streaming?, stop?, tool_pause?, liveness_observed_at?, ok?, body?, error?, …}
            abort: {execution_id, status, aborted, attested, still_attached,
                abort_outcome, stop_clicked?}
            active_work: {busy, running_count, execution_ids, soft_limit,
                hard_limit, free_slots, at_soft_limit, at_hard_limit}
        """
        if op == "active_work":
            result = _relay("GET", "/v1/project-ask/active-work")
            record(
                "mcp.project_ask.active_work",
                busy=result.get("busy"),
                running_count=result.get("running_count"),
            )
            return result

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
                    "expected_size": expected_size,
                    "harvest_source": harvest_source,
                    "download_output": download_output if download_output else None,
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
            if result.get("status_code") == 404:
                result["hint"] = (
                    "Unknown to the satellite. A Stargate/dispatch execution id "
                    "does not poll here — the satellite mints its own id. "
                    'Discover it via project_ask(op="active_work"), the '
                    "cdp.generate.submitted event (carries both ids), or "
                    "agent_bus wait from_agent=cdp."
                )
            record(
                "mcp.project_ask.poll",
                execution_id=execution_id,
                status=result.get("status"),
                completion_phase=result.get("completion_phase"),
                content_proof_uri=result.get("content_proof_uri"),
                stall_stage=result.get("stall_stage"),
                streaming=result.get("streaming"),
                stop=result.get("stop"),
                tool_pause=result.get("tool_pause"),
                liveness_observed_at=result.get("liveness_observed_at"),
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
