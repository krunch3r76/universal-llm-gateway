#!/usr/bin/env python3
"""Cursor keystroke bridge — io-side lane executor (claude.ai ↔ Cursor Composer).

Polls one agent-bus thread and turns lane vocabulary into side effects:

  BRIDGE_OPEN  (web-anthropic → cursor)  open a Cursor tab on jupiter via SSH keystroke
  MSG          (web-anthropic → cursor)  wake the tab: paste ``BRIDGE_WAKE thread=T turn=N``
  TAB_READY    (cursor → web-anthropic)  record lane liveness; nudge claude.ai
  TAB_ALIVE    (cursor → web-anthropic)  heartbeat — refreshes TAB_READY TTL
  TAB_GONE     (cursor → web-anthropic)  explicit tab death — clears readiness
  REPLY        (cursor → web-anthropic)  nudge claude.ai (Jupiter followups) to read the inbox
  BRIDGE_ACK   (this watcher)            ignored on read — our own receipts

The MCP handler never touches evdev or SSH; this process is the sole executor
(spec: cortex://notes/system/specs/cursor-keystroke-bridge-v1.md).

Arm:
  scripts/watch-supervise.sh start --label cursor-bridge-<T> -- \\
    scripts/watch-cursor-bridge-inbox.py --thread <T> --label cursor-bridge-<T>
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import time
from pathlib import Path
from typing import Any

import httpx
import yaml
from bus_watch.state import paths_for, read_state, write_state
from cursor_bridge.lane_ready import (
    TAB_READY_WAIT_S,
    assess_lane_readiness,
    wait_for_lane_ready,
)

_REPO = Path(__file__).resolve().parents[1]
_AGENT_BUS_SOCK = os.environ.get(
    "AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock"
)
_MCP_YAML = Path.home() / ".gateway" / "mcp.yaml"
_TRANSPORT_ERRORS = (httpx.TransportError, httpx.TimeoutException)
_SELF = "cursor"
_PEER = "web-anthropic"
_WAKE_FOCUS_OPENER = os.environ.get("CURSOR_BRIDGE_FOCUS_OPENER", "ctrl_k")


def _load_launch():
    """Import the hyphenated launcher module by path (not importable by name)."""
    spec = importlib.util.spec_from_file_location(
        "cursor_bridge_launch", _REPO / "scripts" / "cursor-bridge-launch.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _mcp_cfg() -> dict[str, Any]:
    return yaml.safe_load(_MCP_YAML.read_text(encoding="utf-8")) or {}


def _bus_client(token: str) -> httpx.Client:
    return httpx.Client(
        transport=httpx.HTTPTransport(uds=_AGENT_BUS_SOCK),
        timeout=30.0,
        headers={"Authorization": f"Bearer {token}"},
    )


def _latest_turn(client: httpx.Client, thread: str) -> int:
    r = client.get(
        "http://localhost/turns",
        params={"thread": thread, "last": 1, "compact": "true"},
    )
    r.raise_for_status()
    turns = r.json().get("turns") or []
    return int(turns[-1].get("turn_number") or 0) if turns else 0


def _turns_after(
    client: httpx.Client, thread: str, after_turn: int
) -> list[dict[str, Any]]:
    r = client.get(
        "http://localhost/turns",
        params={
            "thread": thread,
            "after_turn": after_turn,
            "last": 50,
            "compact": "false",
        },
    )
    r.raise_for_status()
    rows = [
        t
        for t in (r.json().get("turns") or [])
        if int(t.get("turn_number") or 0) > after_turn
    ]
    return sorted(rows, key=lambda t: int(t["turn_number"]))


def _post(
    client: httpx.Client,
    thread: str,
    *,
    to: str,
    subject: str,
    body: str,
    after_turn: int,
) -> int:
    payload = {
        "thread": thread,
        "from": _SELF,
        "to": to,
        "subject": subject,
        "body": body,
        "status": "open",
        "after_turn": after_turn,
    }
    r = client.post("http://localhost/turns", json=payload)
    r.raise_for_status()
    return int(r.json().get("turn_number") or 0)


def _parse_kv(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in body.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _fetch_lane_turns(client: httpx.Client, thread: str) -> list[dict[str, Any]]:
    r = client.get(
        "http://localhost/turns",
        params={"thread": thread, "last": 100, "compact": "false"},
    )
    r.raise_for_status()
    return list(r.json().get("turns") or [])


def _format_ack(res: dict[str, Any], *, prefix: str) -> str:
    ok = bool(res.get("ok"))
    parts = [f"{prefix} ok={ok}"]
    for key in (
        "reason",
        "phase",
        "skipped",
        "focus_verified",
        "focus_probe",
        "tab_ready_turn",
        "tab_ready_age_s",
        "tab_ready_ttl_s",
        "waited_s",
    ):
        val = res.get(key)
        if val is not None and val != "":
            parts.append(f"{key}={val}")
    ks = res.get("keystroke") if isinstance(res.get("keystroke"), dict) else {}
    for key in ("reason", "focus_verified", "focus_probe", "observed_title"):
        val = ks.get(key)
        if val is not None and val != "":
            parts.append(f"{key}={val}")
    return " ".join(parts)


def _nudge_claude(project_ask_url: str, chat_url: str, text: str) -> dict[str, Any]:
    """Warm followup paste into the claude.ai chat (same route as cse_session followup)."""
    if not project_ask_url or not chat_url:
        return {
            "ok": False,
            "skipped": "no_cowork_url" if not chat_url else "no_project_ask_url",
        }
    body = {
        "chat_url": chat_url,
        "prompt_text": text,
        "timeout_s": 90,
        "reattach": True,
    }
    try:
        with httpx.Client(timeout=150.0) as c:
            r = c.post(
                f"{project_ask_url.rstrip('/')}/v1/project-ask/followups", json=body
            )
        if r.status_code >= 400:
            return {"ok": False, "status": r.status_code, "detail": r.text[:300]}
        data = r.json()
        return {
            "ok": bool(data.get("ok", True)),
            "send_verified": data.get("send_verified"),
        }
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


class Lane:
    def __init__(
        self,
        *,
        thread: str,
        slug: str,
        cowork_url: str,
        project_ask_url: str,
        state_path: Path,
    ):
        self.thread = thread
        self.slug = slug
        self.cowork_url = cowork_url
        self.project_ask_url = project_ask_url
        self.state_path = state_path
        self.launch = _load_launch()

    @property
    def title(self) -> str:
        return f"{self.thread} {self.slug}" if self.slug else ""

    def _assess_tab_ready(self, client: httpx.Client) -> dict[str, Any]:
        return assess_lane_readiness(_fetch_lane_turns(client, self.thread))

    def _wait_for_tab_ready(self, client: httpx.Client) -> dict[str, Any]:
        return wait_for_lane_ready(
            lambda: _fetch_lane_turns(client, self.thread),
            timeout_s=TAB_READY_WAIT_S,
        )

    def _open_tab_for_bridge(self, client: httpx.Client, *, cowork_url: str) -> dict[str, Any]:
        """Open or reuse a live tab; ``ok`` means lane-scoped TAB_READY within TTL."""
        res = self.launch.open_tab(
            thread=self.thread, slug=self.slug, cowork_url=cowork_url
        )
        if res.get("skipped") and res.get("reason") == "cooldown":
            readiness = self._assess_tab_ready(client)
            if readiness["ready"]:
                return {
                    **res,
                    "ok": True,
                    "tab_ready": True,
                    "tab_ready_turn": readiness.get("turn"),
                    "reason": "cooldown_tab_ready",
                    "tab_ready_age_s": readiness.get("tab_ready_age_s"),
                    "tab_ready_ttl_s": readiness.get("tab_ready_ttl_s"),
                }
            res = self.launch.open_tab(
                thread=self.thread,
                slug=self.slug,
                cowork_url=cowork_url,
                force=True,
            )
        if not res.get("ok") or res.get("dry_run"):
            return res
        readiness = self._wait_for_tab_ready(client)
        if readiness["ready"]:
            return {
                **res,
                "ok": True,
                "tab_ready": True,
                "tab_ready_turn": readiness.get("turn"),
                "tab_ready_age_s": readiness.get("tab_ready_age_s"),
                "tab_ready_ttl_s": readiness.get("tab_ready_ttl_s"),
                "waited_s": readiness.get("waited_s"),
            }
        return {
            **res,
            "ok": False,
            "phase": "tab_ready_wait",
            "reason": readiness.get("reason", "tab_ready_timeout"),
            "tab_ready_ttl_s": readiness.get("tab_ready_ttl_s"),
            "waited_s": readiness.get("waited_s"),
        }

    def handle(self, client: httpx.Client, turn: dict[str, Any]) -> None:
        n = int(turn["turn_number"])
        subject = str(turn.get("subject") or "").strip().upper()
        sender = str(turn.get("from") or "")
        body = str(turn.get("body") or "")
        if subject.startswith("BRIDGE_ACK") or subject.startswith("BRIDGE_WAKE"):
            return
        if subject.startswith("BRIDGE_OPEN") and sender != _SELF:
            kv = _parse_kv(body)
            self.slug = kv.get("slug") or self.slug or f"bridge-{self.thread}"
            self.cowork_url = kv.get("cowork_url") or self.cowork_url
            res = self._open_tab_for_bridge(client, cowork_url=self.cowork_url)
            tab_ready_turn = res.get("tab_ready_turn")
            write_state(
                self.state_path,
                slug=self.slug,
                cowork_url=self.cowork_url,
                last_open=res,
                bridge_open_turn=n,
                tab_ready_turn=tab_ready_turn,
                tab_ready_at=None,
            )
            self._ack(
                client,
                n,
                _format_ack(
                    res,
                    prefix=f"open_tab title={self.title!r}",
                ),
            )
            print(
                f"BRIDGE_OPEN turn={n} → open_tab ok={res.get('ok')} "
                f"reason={res.get('reason', '')} tab_ready_turn={tab_ready_turn}",
                flush=True,
            )
            return
        if subject.startswith("MSG") and sender != _SELF:
            readiness = self._assess_tab_ready(client)
            if not readiness["ready"]:
                res = {
                    "ok": False,
                    "reason": readiness["reason"],
                    "phase": "preflight",
                    "tab_ready_age_s": readiness.get("tab_ready_age_s"),
                    "tab_ready_ttl_s": readiness.get("tab_ready_ttl_s"),
                }
                write_state(self.state_path, last_wake={"turn": n, **res})
                self._ack(
                    client,
                    n,
                    _format_ack(res, prefix=f"wake turn={n}"),
                )
                print(
                    f"MSG turn={n} → refused ok=False reason={readiness['reason']}",
                    flush=True,
                )
                return
            wake = f"BRIDGE_WAKE thread={self.thread} turn={n}"
            res = self.launch.paste(
                thread=self.thread,
                message=wake,
                focus_title=self.title if _WAKE_FOCUS_OPENER != "none" else "",
                focus_opener=_WAKE_FOCUS_OPENER
                if _WAKE_FOCUS_OPENER != "none"
                else None,
            )
            write_state(
                self.state_path,
                last_wake={"turn": n, **res},
                tab_ready_turn=readiness.get("turn"),
            )
            self._ack(
                client,
                n,
                _format_ack(res, prefix=f"wake turn={n}"),
            )
            print(
                f"MSG turn={n} → wake ok={res.get('ok')} reason={res.get('reason', '')}",
                flush=True,
            )
            return
        if (
            subject.startswith("TAB_READY") or subject.startswith("TAB_ALIVE")
        ) and sender == _SELF:
            write_state(
                self.state_path,
                tab_ready_turn=n,
                tab_ready_at=str(turn.get("created_at") or ""),
            )
            if subject.startswith("TAB_READY"):
                res = _nudge_claude(
                self.project_ask_url,
                self.cowork_url,
                f"TAB_READY on agent-bus thread {self.thread} (turn {n}) — Cursor tab is live. "
                    f"Send MSG turns with cursor_bridge(op=send_msg) or agent_bus send subject MSG.",
                )
                print(f"TAB_READY turn={n} → nudge {res}", flush=True)
            else:
                print(f"TAB_ALIVE turn={n} → heartbeat recorded", flush=True)
            return
        if subject.startswith("TAB_GONE") and sender == _SELF:
            write_state(
                self.state_path,
                tab_ready_turn=None,
                tab_ready_at=None,
                tab_gone_turn=n,
            )
            print(f"TAB_GONE turn={n} → readiness cleared", flush=True)
            return
        if subject.startswith("REPLY") and sender == _SELF:
            res = _nudge_claude(
                self.project_ask_url,
                self.cowork_url,
                f"REPLY landed on agent-bus thread {self.thread} turn {n} — "
                f'agent_bus(tool="get", arguments=\'{{"thread": "{self.thread}", "turn_number": {n}}}\') and continue.',
            )
            write_state(self.state_path, last_reply_turn=n, last_nudge=res)
            print(f"REPLY turn={n} → nudge {res}", flush=True)
            return

    def _ack(self, client: httpx.Client, after_turn: int, text: str) -> None:
        try:
            _post(
                client,
                self.thread,
                to=_PEER,
                subject="BRIDGE_ACK",
                body=text,
                after_turn=after_turn,
            )
        except httpx.HTTPError as exc:
            print(f"ack failed: {exc}", flush=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--thread", required=True)
    p.add_argument("--label", default="")
    p.add_argument(
        "--slug", default="", help="Tab slug if BRIDGE_OPEN already happened"
    )
    p.add_argument(
        "--cowork-url", default="", help="claude.ai chat_url for REPLY nudges"
    )
    p.add_argument(
        "--after-turn", type=int, default=-1, help="-1 = state file, else latest"
    )
    p.add_argument("--poll-s", type=float, default=4.0)
    p.add_argument("--max-hours", type=float, default=0.0, help="0 = run forever")
    p.add_argument("--state-file", default="")
    args = p.parse_args()

    thread = str(args.thread).strip()
    label = args.label.strip() or f"cursor-bridge-{thread}"
    state_path = (
        Path(args.state_file)
        if args.state_file.strip()
        else paths_for(label).state_file
    )
    prior = read_state(state_path)
    cfg = _mcp_cfg()
    token = str(cfg.get("AGENT_BUS_TOKEN") or "").strip()
    if not token:
        raise SystemExit(f"AGENT_BUS_TOKEN missing in {_MCP_YAML}")

    lane = Lane(
        thread=thread,
        slug=args.slug or str(prior.get("slug") or ""),
        cowork_url=args.cowork_url or str(prior.get("cowork_url") or ""),
        project_ask_url=str(cfg.get("PROJECT_ASK_URL") or "").strip(),
        state_path=state_path,
    )
    client = _bus_client(token)
    after_turn = int(args.after_turn)
    if after_turn < 0:
        after_turn = int(prior.get("after_turn") or -1)
    if after_turn < 0:
        after_turn = _latest_turn(client, thread)

    print(
        f"cursor-bridge watcher thread={thread} after_turn={after_turn} label={label} title={lane.title!r}",
        flush=True,
    )
    write_state(
        state_path,
        status="polling",
        thread=thread,
        after_turn=after_turn,
        label=label,
        pid=os.getpid(),
    )
    started = time.monotonic()
    max_s = args.max_hours * 3600.0
    beat = 0
    while True:
        if max_s > 0 and time.monotonic() - started >= max_s:
            write_state(state_path, status="expired")
            return 2
        try:
            rows = _turns_after(client, thread, after_turn)
        except _TRANSPORT_ERRORS as exc:
            print(
                f"bus transport error ({type(exc).__name__}); reconnecting", flush=True
            )
            client.close()
            client = _bus_client(token)
            time.sleep(3.0)
            continue
        except httpx.HTTPStatusError as exc:
            print(f"bus http {exc.response.status_code}; retry", flush=True)
            time.sleep(args.poll_s)
            continue
        for turn in rows:
            try:
                lane.handle(client, turn)
            except Exception as exc:  # one bad turn must not kill the lane
                print(
                    f"handle turn={turn.get('turn_number')} failed: {type(exc).__name__}: {exc}",
                    flush=True,
                )
            after_turn = int(turn["turn_number"])
            write_state(state_path, status="polling", after_turn=after_turn)
        beat += 1
        if beat % 15 == 0:
            print(
                f"… heartbeat thread={thread} after_turn={after_turn} elapsed={time.monotonic() - started:.0f}s",
                flush=True,
            )
            write_state(state_path, status="polling", after_turn=after_turn)
        time.sleep(args.poll_s)


if __name__ == "__main__":
    raise SystemExit(main())
