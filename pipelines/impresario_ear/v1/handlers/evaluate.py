"""impresario_ear_evaluate_v1 — in-handler ephemeral cursor-sdk generate + harvest."""

from __future__ import annotations

import json
import re
from typing import Any, override

from implement_admission.impresario_policy import load_impresario_policy
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

_BUS_FORBIDDEN = (
    "You MUST NOT call agent_bus, post, or reply on any thread. "
    "Return your evaluation only in this message body."
)
_SUGGESTIONS_RE = re.compile(r"\bSUGGESTIONS\b", re.I)


def _scrub_suggestions(text: str) -> str:
    return _SUGGESTIONS_RE.sub("ADVICE", text)


_METADATA_KEYS = frozenset({"status", "residue", "reason"})


def _harvest_core_text(raw: str) -> str:
    """Return harvest prose after sidecar/frontmatter trim, before metadata line strip."""
    if "## closeout_body\n" in raw:
        start = raw.index("## closeout_body\n") + len("## closeout_body\n")
        tail = raw[start:]
        for marker in ("\n## structured_closeout_full", "\n## effects_manifest"):
            if marker in tail:
                tail = tail[: tail.index(marker)]
                break
        return tail.strip()
    if "\n## effects_manifest" in raw:
        text = raw[: raw.index("\n## effects_manifest")].strip()
    else:
        text = raw
    if text.startswith("---\n"):
        end_fm = text.find("\n---\n", 4)
        if end_fm != -1:
            text = text[end_fm + len("\n---\n") :].lstrip()
    return text.strip()


def _leading_metadata_lines(core_text: str) -> tuple[list[str], list[str]]:
    """Split leading status/residue/reason lines from the remainder."""
    lines = core_text.splitlines()
    meta: list[str] = []
    while lines:
        key = lines[0].split(":", 1)[0].strip().lower()
        if key not in _METADATA_KEYS:
            break
        meta.append(lines.pop(0))
    return meta, lines


def _parse_degraded_harvest_notice(core_text: str) -> dict[str, str] | None:
    """Detect SDK degraded/provider_error closeout before verdict parsing.

    Matches leading ``status: degraded`` or a ``reason:`` value starting with
    ``provider_error``. Returns notice body (status/reason lines) and subject
    suffix token when matched; otherwise None.
    """
    meta_lines, _rest = _leading_metadata_lines(core_text)
    if not meta_lines:
        return None
    status_val = ""
    reason_val = ""
    status_line = ""
    reason_line = ""
    for line in meta_lines:
        key, _, val = line.partition(":")
        k = key.strip().lower()
        v = val.strip()
        if k == "status":
            status_val = v.lower()
            status_line = line.strip()
        elif k == "reason":
            reason_val = v
            reason_line = line.strip()
    is_degraded = status_val == "degraded"
    is_provider = reason_val.lower().startswith("provider_error")
    if not is_degraded and not is_provider:
        return None
    body_parts = [ln for ln in (status_line, reason_line) if ln]
    if not body_parts and is_degraded and status_line:
        body_parts = [status_line]
    if reason_val.lower().startswith("provider_error"):
        reason_token = "provider_error"
    elif reason_val:
        reason_token = reason_val.split(":", 1)[0].strip()
    else:
        reason_token = "degraded"
    return {
        "body": "\n".join(body_parts),
        "degraded_reason_token": reason_token,
    }


def _extract_harvest_prose(raw: str) -> str:
    core = _harvest_core_text(raw)
    _meta, remainder = _leading_metadata_lines(core)
    return "\n".join(remainder).strip() if remainder else core.strip()


def _build_evaluate_prompt(
    *,
    root_thread: str,
    mode: str,
    trigger: str,
    gather: dict[str, Any],
    prescreen: dict[str, Any] | None = None,
) -> str:
    gather_for_prompt = gather
    if isinstance(gather, dict):
        ordered: dict[str, Any] = {}
        for key in ("jsonl", "cdp"):
            if key in gather:
                ordered[key] = gather[key]
        for key, value in gather.items():
            if key not in ordered:
                ordered[key] = value
        gather_for_prompt = ordered
    payload: dict[str, Any] = {"mode": mode, "gather": gather_for_prompt}
    if prescreen:
        sig = prescreen.get("signals")
        imp = prescreen.get("imperative")
        if sig or imp:
            payload["prescreen"] = {
                "signals": sig,
                "imperative": imp,
            }
    gather_json = json.dumps(payload, default=str)
    instructions = (
        "You are the Impresario Ear critic evaluating house continuity.\n"
        f"Thread: {root_thread}\n"
        f"Mode: {mode}\n"
        f"Trigger: {trigger}\n\n"
        "Required output format (plain text in your reply body only):\n"
        "VERDICT: healthy | remiss_duty | drift | blocked\n"
        "DIAGNOSIS: one short paragraph\n"
        "STEER: imperative next action for the house tab\n"
        "NEXT_ADMIT: one line the tab should dispatch (or none if healthy)\n\n"
        "If the house tab responds to an operator observation or issue merely by "
        "confirming, describing, or diagnosing it and parking on STAY / "
        "Going. Stay here / empty or quiet NOW without proactively offering "
        "concrete actions and executing in-lock autonomous actions in the same "
        "turn, classify as VERDICT: remiss_duty and name the concrete remedial "
        "action to offer or dispatch in STEER and NEXT_ADMIT.\n\n"
        f"<untrusted_context>\n{gather_json}\n</untrusted_context>"
    )
    return _BUS_FORBIDDEN + "\n\n" + instructions


def _pick_critic(mode: str, options: dict[str, Any]):
    policy = load_impresario_policy()
    override = str(options.get("model") or "").strip()
    if mode == "patrol":
        seat = policy.ear_patrol
        if override and override in policy.allowed_critics:
            from implement_admission.impresario_policy import _seat

            seat = _seat(override, seat.effort_rung, seat=seat.seat)
        return seat, None
    seat = policy.ear
    if override:
        if override not in policy.allowed_critics:
            return None, {
                "skip": True,
                "error": {"code": "ear.model_not_allowed", "model": override},
            }
        from implement_admission.impresario_policy import _seat

        seat = _seat(override, seat.effort_rung, seat=seat.seat)
    return seat, None


def _parse_verdict(body: str) -> str:
    for line in body.splitlines():
        if line.upper().startswith("VERDICT:"):
            return line.split(":", 1)[1].strip().lower() or "unknown"
    lowered = body.lower()
    if "remiss" in lowered:
        return "remiss_duty"
    if "healthy" in lowered:
        return "healthy"
    return "unknown"


async def evaluate_impresario_ear(
    *,
    resolve: dict[str, Any],
    gather: dict[str, Any],
    options: dict[str, Any],
    patrol_prescreen: Any | None = None,
    sdk_generate: Any | None = None,
    sdk_poll: Any | None = None,
    sdk_harvest: Any | None = None,
) -> dict[str, Any]:
    mode = str(resolve.get("mode") or "deep")
    pre: dict[str, Any] | None = None
    if mode == "patrol" and isinstance(options.get("patrol_prescreen_result"), dict):
        pre = options["patrol_prescreen_result"]
    elif patrol_prescreen is not None:
        pre = await patrol_prescreen(
            resolve=resolve,
            gather=gather,
            fetch_turns=options.get("_fetch_turns"),
        )
    if pre is not None:
        if pre.get("skip_model"):
            signals = pre.get("signals") or []
            if signals:
                body = str(pre.get("imperative") or "").strip() or (
                    "NEXT_ADMIT: team_dispatch for pending house work — do not stop at narration."
                )
                return {
                    "skip": False,
                    "body": body,
                    "verdict": "remiss_duty",
                    "model": "zero-token",
                    "effort": "none",
                    "signals": signals,
                }
            return {"skip": True, "reason": pre.get("reason") or "prescreen_skip"}
    seat, err = _pick_critic(mode, options)
    if err:
        return err
    assert seat is not None
    prompt = _build_evaluate_prompt(
        root_thread=str(resolve.get("root_thread") or ""),
        mode=mode,
        trigger=str(resolve.get("trigger") or ""),
        gather=gather,
        prescreen=pre,
    )
    if sdk_generate is None:
        from systems.frontier_consult.cdp_cursor_reroute import (
            harvest_sdk_body,
            poll_dispatch_status,
        )
        from systems.frontier_consult.cursor_sdk_generate import (
            dispatch_cursor_sdk_generate,
        )

        sdk_generate = dispatch_cursor_sdk_generate
        sdk_poll = poll_dispatch_status
        sdk_harvest = harvest_sdk_body

    knobs: dict[str, str] = {"effort": seat.effort_rung}
    if seat.fast:
        knobs["fast"] = "true"
    admit = await sdk_generate(
        request_id=f"impresario-ear-{resolve.get('root_thread', '')[:8]}",
        role="cursor-sdk",
        model=seat.model,
        subject=None,
        caller_agent="impresario-ear",
        contract="freeform",
        packet_path=None,
        message_text=prompt,
        bus_lifecycle="ephemeral",
        dispatch_thread_id=None,
        read_only=True,
        lane="B",
        model_knobs=knobs,
    )
    dispatch_id = str(admit.get("dispatch_id") or "")
    thread_id = str(admit.get("thread_id") or resolve.get("root_thread") or "")
    polled = await sdk_poll(dispatch_id=dispatch_id, timeout_s=120.0)
    status = str(polled.get("status") or "")
    if not polled.get("terminal") or status != "completed":
        return {
            "skip": True,
            "error": {"code": "ear.sdk_incomplete", "status": status},
            "model": seat.model,
            "effort": seat.effort_rung,
        }
    harvested = await sdk_harvest(thread_id=thread_id, dispatch_id=dispatch_id)
    core = _harvest_core_text(str(harvested or ""))
    notice = _parse_degraded_harvest_notice(core)
    if notice:
        return {
            "skip": False,
            "degraded_notice": True,
            "body": notice["body"],
            "degraded_reason_token": notice["degraded_reason_token"],
            "model": seat.model,
            "effort": seat.effort_rung,
            "bus_lifecycle": "ephemeral",
            "dispatch_id": dispatch_id,
        }
    body = _scrub_suggestions(_extract_harvest_prose(str(harvested or "")))
    verdict = _parse_verdict(body)
    return {
        "skip": False,
        "body": body,
        "verdict": verdict,
        "model": seat.model,
        "effort": seat.effort_rung,
        "bus_lifecycle": "ephemeral",
        "dispatch_id": dispatch_id,
    }


class ImpresarioEarEvaluateHandler(BaseHandler):
    step_type = "impresario_ear_evaluate_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "outputs", {}) or {}
        resolve = (outputs.get("resolve") or {}).json or {}
        gather = (outputs.get("gather") or {}).json or {}
        if resolve.get("skip") or gather.get("skip"):
            return StepOutput(raw="{}", json={"skip": True})
        options = dict(getattr(context, "options", {}) or {})
        prescreen = options.get("_patrol_prescreen")
        if prescreen is None and str(resolve.get("mode")) == "patrol":
            from bus_watch.impresario_ear_patrol import patrol_prescreen_for_evaluate

            prescreen = patrol_prescreen_for_evaluate
        payload = await evaluate_impresario_ear(
            resolve=resolve,
            gather=gather,
            options=options,
            patrol_prescreen=prescreen,
            sdk_generate=options.get("_sdk_generate"),
            sdk_poll=options.get("_sdk_poll"),
            sdk_harvest=options.get("_sdk_harvest"),
        )
        return StepOutput(raw=json.dumps(payload, default=str), json=payload)
