"""Budget envelope, bus client, and liaison policy gears for digest assembly."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from bus_watch.fable_lock import MAX_HOPS_PER_NIGHT

_AGENT_BUS_SOCK = os.environ.get(
    "AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock"
)
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"

_GIW_USAGE_LIVE = os.environ.get(
    "LIAISON_GIW_USAGE_LIVE", "http://127.0.0.1:8091/api/v1/cursor/dispatch-usage-live"
)
_BUDGET_SCOPE = "liaison_seat"
_BUDGET_RATIO_THRESHOLD = 0.80

POLICY_DEFAULTS: dict[str, Any] = {
    "gear": "1-fable-mvp",
    # House successor is orientation (harvest/fold/classify), not judgment.
    # SDK Fable is blocked; do not inherit the closed credit-window default.
    "successor_model": "cursor/grok-4.7",
    "successor_cost_intent": None,
    "max_ticks_per_hop": 5,
    "max_hop_minutes": 60,
    # Wall-clock backstop for pending_spawn release — strictly longer than max_hop.
    "pending_stale_backstop_minutes": 180,
    "poll_seconds": 600,
    "max_hops_per_night": MAX_HOPS_PER_NIGHT,
    # Opt-in ceiling: non-positive means no dispatch cap (operator 2026-09-13).
    "max_dispatches_per_night": 0,
    "wake_on_attention_only": False,
    "spawn_grace_seconds": 900,
    "successor_seat": "cursor-sdk",
    "successor_contract": "none",
    "budget_max_age_s": 300,
    "gui_host": None,
    "ready": False,
    "post_digest": False,
    # Attended-tab window class. The operator picks the tab model in the picker,
    # so the harness cannot read it; 256k is the Grok 4.7 class the houses ran on.
    "ide_window_tokens": 256_000,
    # Tool results are not stored in the tab transcript; this stands in for them.
    "ide_tokens_per_tool_call": 1500,
}
GEAR_PRESETS: dict[str, dict[str, Any]] = {
    "1-fable-mvp": {},
    "2-opus-hops": {
        "successor_model": "cursor/claude-opus-5",
        "successor_cost_intent": None,
        "max_ticks_per_hop": 6,
    },
    "3-wake-on-attention": {
        # No implicit premium successor: the ticker spawns only a model the
        # operator bound with --set successor_model=… (10534 2026-09-12 minted
        # four unasked Opus liaisons at 12–24M tokens each on this preset).
        "successor_model": None,
        "successor_cost_intent": None,
        "wake_on_attention_only": True,
        "poll_seconds": 120,
        "spawn_grace_seconds": 900,
        "post_digest": True,
    },
    "4-cdp-liaison": {
        "successor_seat": "cdp",
        "successor_model": "cdp/opus-5",
        "successor_cost_intent": None,
        "successor_contract": "conductor",
        "ready": False,
    },
}

__all__ = [
    "GEAR_PRESETS",
    "POLICY_DEFAULTS",
    "_bus",
    "agent_bus_bearer_headers",
    "build_budget_block",
    "effective_policy",
    "health_probe",
]


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def agent_bus_bearer_headers() -> dict[str, str]:
    """Bearer for agent-bus HTTP. Env wins; else ``~/.gateway/mcp.yaml``.

    Agent shells are fresh and often have no ``AGENT_BUS_TOKEN``. The hop
    seal wait used env-only headers and 401'd (``wait_http_401``). Ticker
    harvest already reads the yaml file via ``_token``.
    """
    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    if not token and _MCP_YAML.is_file():
        cfg = yaml.safe_load(_MCP_YAML.read_text(encoding="utf-8")) or {}
        token = str(cfg.get("AGENT_BUS_TOKEN") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _token() -> str:
    cfg = yaml.safe_load(_MCP_YAML.read_text(encoding="utf-8")) or {}
    token = str(cfg.get("AGENT_BUS_TOKEN") or "").strip()
    if not token:
        raise SystemExit(f"AGENT_BUS_TOKEN missing in {_MCP_YAML}")
    return token


def _bus() -> httpx.Client:
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=_AGENT_BUS_SOCK),
        timeout=20.0,
        headers={"Authorization": f"Bearer {_token()}"},
        base_url="http://localhost",
    )


def _get(client: httpx.Client, path: str, **params: Any) -> dict[str, Any] | None:
    try:
        r = client.get(path, params={k: v for k, v in params.items() if v is not None})
    except httpx.HTTPError as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"[:200]}
    if r.status_code >= 400:
        return {"_error": f"http_{r.status_code}", "_body": r.text[:200]}
    try:
        data = r.json()
    except ValueError:
        return {"_error": "non_json"}
    return data if isinstance(data, dict) else {"_list": data}


def health_probe(url: str) -> str:
    try:
        r = httpx.get(url, timeout=3.0)
    except httpx.HTTPError as exc:
        return f"down:{type(exc).__name__}"
    return "ok" if r.status_code < 400 else f"http_{r.status_code}"


def digest_fingerprint(root: dict[str, Any], lanes: list[dict[str, Any]]) -> str:
    key = [(root.get("id"), root.get("turn_count"), root.get("status"))]
    key += [
        (lane["id"], lane["turns"], lane["status"], lane["lifecycle"]) for lane in lanes
    ]
    return hashlib.sha256(
        json.dumps(key, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def _holder_dispatch_id(holder: str | None) -> str | None:
    text = str(holder or "")
    if text.startswith("sdk:"):
        return text.split(":", 1)[1] or None
    return None


def _read_sdk_usage_live(dispatch_id: str) -> dict[str, Any] | None:
    """Fetch live stream usage for the holder dispatch from GIW."""
    try:
        response = httpx.get(
            _GIW_USAGE_LIVE,
            params={"dispatch_id": dispatch_id},
            timeout=3.0,
        )
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    usage_live = payload.get("usage_live")
    if not isinstance(usage_live, dict):
        return None
    return usage_live


def build_budget_block(
    *,
    used_tokens: int,
    window_limit_tokens: int,
    model: str,
    source: str,
    scope: str,
    epoch: str,
    as_of: str,
    **basis: Any,
) -> dict[str, Any]:
    """Status-basis budget envelope for the liaison digest.

    ``stop_class`` is raised only for sources that measure one seat's window —
    the GIW stream for a headless holder or the tab transcript for an IDE seat —
    never for the cumulative ``digest.estimate``. Extra ``basis`` fields
    (transcript id, tool calls, …) ride along so a reader can audit the number.
    """
    ratio = used_tokens / max(window_limit_tokens, 1)
    stop_class = (
        "CONTEXT_BUDGET"
        if source in _SEAT_SCOPED_SOURCES and ratio >= _BUDGET_RATIO_THRESHOLD
        else None
    )
    return {
        "used_tokens": used_tokens,
        "window_limit_tokens": window_limit_tokens,
        "model": model,
        "as_of": as_of,
        "source": source,
        "scope": scope,
        "epoch": epoch,
        "stop_class": stop_class,
        **basis,
    }


_SEAT_SCOPED_SOURCES = frozenset({"giw.sdk_stream", "ide.transcript"})


def effective_policy(state: dict[str, Any]) -> dict[str, Any]:
    """Defaults ← gear preset ← explicit ``policy`` overrides stored in state.

    ``successor_model_source`` records which layer bound the successor model
    (``override`` · ``gear_preset`` · ``default``) so the spawn predicate can
    refuse a model nobody chose.
    """
    overrides = dict(state.get("policy") or {})
    gear = str(overrides.get("gear") or POLICY_DEFAULTS["gear"])
    preset = GEAR_PRESETS.get(gear, {})
    merged = {
        **POLICY_DEFAULTS,
        **preset,
        **overrides,
        "gear": gear,
    }
    if "successor_model" in overrides:
        merged["successor_model_source"] = "override"
    elif "successor_model" in preset:
        merged["successor_model_source"] = "gear_preset"
    else:
        merged["successor_model_source"] = "default"
    merged["successor_is_fable"] = "fable" in str(merged.get("successor_model") or "")
    if gear == "3-wake-on-attention" and "ready" not in overrides:
        # ``ready`` is the explicit ticker arm (A7). The register cannot arm it:
        # an IDE-hop chain also runs ``register=autonomous`` with the ticker
        # deliberately policy-only (10479 all night 2026-09-13; a register-armed
        # ticker put a second driver on that house). ``--go-under`` sets it.
        merged["ready"] = False
    merged["ready_source"] = "override" if "ready" in overrides else "default"
    return merged
