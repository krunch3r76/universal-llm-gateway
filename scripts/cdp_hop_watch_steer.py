"""Steering helpers shared by CDP hop reactor + backup watcher."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
from cdp_hop_reactor_wait import http_json_status

_STEER_FILENAME = "steer-latest.md"


def steer_path(state_dir: Path) -> Path:
    return state_dir / _STEER_FILENAME


def load_steer_hint(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def write_steer_hint(path: Path, *, reason: str, judgment: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"---\nreason: {reason}\nsteered_at: "
        f"{datetime.now(UTC).isoformat(timespec='seconds')}\n---\n\n"
        f"{judgment.strip()}\n"
    )
    path.write_text(body, encoding="utf-8")


def bus_token(mcp_yaml: Path) -> str:
    cfg = yaml.safe_load(mcp_yaml.read_text(encoding="utf-8"))
    token = str(cfg.get("AGENT_BUS_TOKEN") or "").strip()
    if not token:
        raise RuntimeError(f"AGENT_BUS_TOKEN missing in {mcp_yaml}")
    return token


def post_steer_turn(*, sock: str, token: str, thread: str, reason: str, judgment: str) -> bool:
    body = (
        f"**STEER** (backup watcher · reason={reason})\n\n"
        f"{judgment.strip()}\n\n"
        "Binding for next Fable episode — also in tmp/watch/cdp-hop-reactor/steer-latest.md"
    )
    payload = {"thread": thread, "to": "cursor", "from": "cursor", "subject": f"Steer — {reason[:80]}", "body": body}
    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=sock), timeout=30.0,
                        headers={"Authorization": f"Bearer {token}"}) as client:
            return client.post("http://localhost/threads/send", json=payload).status_code < 400
    except httpx.HTTPError:
        return False


def gather_steer_context(
    *,
    fable_thread: str,
    reason: str,
    reactor_state_path: Path,
    reactor_log_path: Path,
    cdp_ask_base: str,
) -> str:
    chunks = [f"stall_reason: {reason}", f"fable_thread: {fable_thread}"]
    if reactor_state_path.is_file():
        try:
            state_raw = json.loads(reactor_state_path.read_text())
            chunks.append(
                "reactor_ids: "
                + json.dumps({
                    "fable_chat_url": state_raw.get("fable_chat_url"),
                    "fable_stargate_execution_id": state_raw.get("fable_stargate_execution_id"),
                    "fable_satellite_execution_id": state_raw.get("fable_satellite_execution_id"),
                    "fable_last_turn_ordinal": state_raw.get("fable_last_turn_ordinal"),
                })
            )
        except (OSError, json.JSONDecodeError):
            pass
    if reactor_log_path.is_file():
        lines = reactor_log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = [ln for ln in lines if ln.strip().startswith("{")][-6:]
        if tail:
            chunks.append("reactor_log_tail:\n" + "\n".join(tail))
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{cdp_ask_base.rstrip('/')}/v1/project-ask/active-work")
            env = http_json_status(resp.status_code, resp.text, parsed=resp.json() if resp.status_code < 400 else None)
            if resp.status_code < 400 and isinstance(env.get("body"), dict):
                data = env["body"]
                rows = [r for r in (data.get("rows") or []) if str(r.get("parent_thread") or "") == fable_thread]
                seated = data.get("seated_rows") or []
                chunks.append(f"active_work_rows: {json.dumps(rows)[:800]}")
                chunks.append(f"seated_rows: {json.dumps(seated)[:400]}")
            else:
                chunks.append(f"active_work_error: status={resp.status_code} code={env.get('code')}")
    except httpx.HTTPError as exc:
        chunks.append(f"active_work_error: {exc}")
    return "\n\n".join(chunks)


def confer_steer_judgment(*, stargate_base: str, fable_thread: str, reason: str, context: str, wait_s: float = 120.0) -> str | None:
    prompt = (
        f"You are the backup steer seat for overnight CDP hop reactor on thread {fable_thread}. "
        f"One bounded judgment only.\n\nTrigger: {reason}\n\nContext:\n{context}\n\n"
        "Output: STEER_VERDICT + NEXT_LEG. No implement."
    )
    body = {"op": "generate", "seat": "cursor-auto", "contract": "confer",
            "dispatch_thread_id": fable_thread, "prompt": prompt, "caller_agent": "cdp-hop-backup-watch"}
    dispatch_url = f"{stargate_base.rstrip('/')}/api/v1/team/dispatch"
    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(dispatch_url, json=body)
            env = http_json_status(resp.status_code, resp.text, parsed=resp.json() if resp.status_code < 400 else None)
            if resp.status_code >= 400:
                return f"STEER_VERDICT: dispatch http {resp.status_code} code={env.get('code')}"
            data = env.get("body") if isinstance(env.get("body"), dict) else {}
            exec_id = str(data.get("execution_id") or "").strip()
            if not exec_id:
                return None
            deadline = datetime.now(UTC).timestamp() + wait_s
            while datetime.now(UTC).timestamp() < deadline:
                poll = client.get(f"{stargate_base.rstrip('/')}/api/v1/pipelines/executions/{exec_id}",
                                  params={"wait": min(30.0, wait_s)}, timeout=45.0)
                penv = http_json_status(poll.status_code, poll.text, parsed=poll.json() if poll.status_code < 400 else None)
                if poll.status_code >= 400:
                    return f"STEER_VERDICT: poll http {poll.status_code} code={penv.get('code')}"
                rec = penv.get("body") if isinstance(penv.get("body"), dict) else {}
                if str(rec.get("status") or "") in {"completed", "failed", "cancelled"}:
                    result = rec.get("result") or {}
                    if isinstance(result, dict) and result.get("content"):
                        return str(result["content"])[:4000]
                    return None
    except httpx.HTTPError:
        return None
    return None
