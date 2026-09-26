"""IDE keystroke hop for the attended liaison seat.

The attended liaison lives in one Cursor IDE tab whose context grows every
turn, and every turn re-sends that context. A hop ends the tab right after a
CHECKPOINT and opens a fresh tab on the graphical host that resumes the same
root (``resume <R>``), rebuilds watcher start+tail (pollers torn down on the
departing tab) plus the ``--loop --heartbeat 1200``, and continues the cadence
Plan -> Dispatch -> Hop -> Arm -> Harvest.

This is a third rotation next to the conductor row-hop and the headless
cursor-sdk successor in ``spawn_on_wake``: it is seat-level, attended, and
targets a GUI, so it needs neither the seat lock nor a dispatch admit.

Why keystrokes, and why no ``cursor -r``: the IDE window is a Remote-SSH
window (GUI on the graphical host, cursor-server on the hub). ``cursor -r
<repo>`` on the GUI host would open the NFS path as a *local* workspace
instead of raising the remote window, so the launch pastes into the Cursor
window that already has focus and the attended operator owns that focus.

The GUI host is **policy** (``liaison-tick.py --set gui_host=<ssh host>``), never
a constant: the operator may sit at any of several graphical hosts, each with a
Remote-SSH window into the hub, and a wrong default lands the hop — and the next
turn of premium spend — on a window nobody is watching (hops 1–2, 2026-09-11).
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
from durable_io.atomic import durable_write_text
from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_STARGATE_URL,
    make_sync_client,
)

from bus_watch.digest_budget import agent_bus_bearer_headers
from bus_watch.doorbell_skills import primary_liaison_slug
from bus_watch.fable_lock import HOUSE_LABEL_PREFIX, WATCH_DIR
from bus_watch.ide_budget import AGENT_TRANSCRIPTS, first_line_matches
from bus_watch.ide_hop_landing import (
    AGENTS_WINDOW_APP_ID,
    focus_title_for,
    hop_header_line,
    wait_for_landed_transcript,
)
from bus_watch.liaison_digest import effective_policy
from bus_watch.state import read_state

_REPO = Path(__file__).resolve().parents[2]
HANDOFF_MSG_DIR = WATCH_DIR / "handoff-messages"
KEYSTROKE_SCRIPT = "scripts/orchestrator_tab_keystroke.py"
# predicate_unmet is not terminal — CDP consults sit there until the first
# qualifying reply (a:33284; hop 15 ARM: none live while G6 was in_flight).
LIVE_WATCHER_STATUSES = frozenset({"polling", "running", "predicate_unmet"})
DEFAULT_REMOTE_REPO = os.environ.get("ORCHESTRATOR_REPO", str(_REPO))
_CHECKPOINT_TIMEOUT_S = 15.0
_WAIT_SLICE_S = 55.0


def policy_gui_host(root_id: str, watch_dir: Path = WATCH_DIR) -> str | None:
    """``policy.gui_host`` from the liaison tick state; None when the operator never set it."""
    state = read_state(watch_dir / f"liaison-{root_id}.tick.json")
    host = effective_policy(state).get("gui_host")
    return str(host) if host else None


def tick_register(root_id: str, watch_dir: Path = WATCH_DIR) -> str:
    """``register`` from the liaison tick state (``attended`` when unset)."""
    state = read_state(watch_dir / f"liaison-{root_id}.tick.json")
    return str(state.get("register") or "attended")


def policy_focus_title(root_id: str, watch_dir: Path = WATCH_DIR) -> str | None:
    """``policy.hop_focus_title`` — operator override of the compositor title substring.

    Default is ``Cursor Agents``. Override only when the live toplevel title differs.
    """
    state = read_state(watch_dir / f"liaison-{root_id}.tick.json")
    title = effective_policy(state).get("hop_focus_title")
    return str(title) if title else None


TAIL_RECIPE = (
    "tail {label}: watch-supervise.sh tail --label {label} (background Shell, "
    "block_until_ms 0, notify_on_output: closeout turn=|consult complete|stall-pop:)"
)
LOOP_REBUILD = (
    "LOOP: rebuild `scripts/liaison-tick.py --root {root} --loop --heartbeat 1200 "
    "--holder ide:<this-tab-uuid>` only while a watcher is live or a row is playable "
    "(20 min backup: re-arm / watcher health). No playable row and no live watcher "
    "→ do not arm; SIGTERM this root's --loop. A harvested conductor tail printing "
    "stall-pop is not a watcher: watch-supervise.sh stop --label <label>."
)
# Last line of every attended hop and monitor wake. Seats stop after a close
# with a "nothing new" note and no next action (specimen tab
# 053245b7-918a-476d-a3d7-7eab2ae15e49; house 12606 after 12749).
OPERATOR_LOOP = (
    "OPERATOR LOOP: after a dispatch closes, or after a steer, tell the operator "
    "what you changed and the move you are taking. You abort, close, and re-hire "
    "threads. The operator does not. Do not ask the operator to abort a thread, "
    "set hire, or edit the harness. A conductor admitted to re-implement a row "
    "already on master is aborted by this seat, then reported. "
    "Close with what you are watching and when you will report. "
    "A status with no watch is a stop."
)
def _pid_alive(pid: Any) -> bool:
    try:
        n = int(pid)
    except (TypeError, ValueError):
        return False
    if n <= 0:
        return False
    try:
        os.kill(n, 0)
    except OSError:
        return False
    return True


def _poller_alive(stem: str, state: dict[str, Any], watch_dir: Path) -> bool:
    """Status is not liveness — a dead ``predicate_unmet`` stall must not ARM a hang-tail."""
    pid: Any = state.get("pid")
    pid_path = watch_dir / f"{stem}.pid"
    if pid_path.is_file():
        try:
            text = pid_path.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
        if text:
            pid = text
    return _pid_alive(pid)


def live_watcher_labels(
    root_id: str,
    watch_dir: Path = WATCH_DIR,
    exclude_threads: Iterable[str] = (),
) -> list[str]:
    """Labels of pollers still running for ``root_id`` — the tails a successor tab must re-arm.

    A label is the state-file stem (what ``watch-supervise.sh tail --label`` takes).
    A poller belongs to the root when its stem carries the root prefix or its
    ``thread`` is the root itself; terminal statuses (``complete``, …) are skipped
    because the tail would exit immediately and the digest already surfaces them
    as unrelayed. ``predicate_unmet`` stays live **only while the poller pid is
    alive**: chrome-only CDP envelopes do not satisfy ``proof_reply_from``, so a
    running wait is real; a dead stall (specimen ``10479-r4-consult`` 2026-09-11)
    is not — tailing it hangs until session death (status is not complete/expired).

    ``exclude_threads`` drops pollers watching the calling seat's **own** lane.
    The ticker arms a closeout watcher on the lane it spawns, so a headless
    successor that counts it reads its own pending closeout as follow-up, hops,
    and spawns a seat whose lane gets the same watcher — an unbounded premium
    chain (specimen ``10534-ticker-opus-10579-closeout`` 2026-09-12, seen from
    inside lane 10579). Waiting on yourself is never a reason to hop.
    """
    excluded = {str(t) for t in exclude_threads if str(t).strip()}
    labels: list[str] = []
    for path in sorted(watch_dir.glob("*.state.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            not isinstance(state, dict)
            or state.get("status") not in LIVE_WATCHER_STATUSES
        ):
            continue
        stem = path.name.removesuffix(".state.json")
        if stem.startswith(HOUSE_LABEL_PREFIX):
            continue
        if str(state.get("thread")) in excluded:
            continue
        if not _poller_alive(stem, state, watch_dir):
            continue
        if stem.startswith(f"{root_id}-") or str(state.get("thread")) == str(root_id):
            labels.append(stem)
    return labels


def build_ide_hop_message(
    root_id: str,
    *,
    row: str,
    arm_labels: list[str],
    tip_cp_ordinal: int | None = None,
    workspace: str = "universal-llm-gateway",
    register: str = "attended",
) -> str:
    """First user message of the successor tab; ``resume <R>`` first so the fence hook fires.

    ``register`` is read from the tick state by the hop script: an overnight chain must
    tell the successor it is autonomous (bind forks itself, page only on designed
    stops, hop itself) in the first line, not leave it to a digest field it may skim.
    """
    arm_lines = [f"ARM: {TAIL_RECIPE.format(label=label)}" for label in arm_labels] or [
        "ARM: none live — Plan from the digest (`scripts/liaison-tick.py --root R --once`)."
    ]
    tip = f" tip_cp={tip_cp_ordinal}" if tip_cp_ordinal is not None else ""
    lines = [
        f"resume {root_id}",
        "",
        f"Liaison IDE hop ({register} register){tip}. "
        f"LOAD the {primary_liaison_slug('ide')} skill (do not skim).",
        # STAY governs the hop only. Tab 12e32c8b (10479, 2026-09-13 06:56Z) read
        # it as "do not dispatch" and parked at 0.7 % with NOW=R12 undone.
        "NOW non-empty ⇒ dispatch its first leg from this tab. "
        "LOAD liaison-cursor. Repo write on this seat → "
        "team_dispatch(seat=cursor-sdk, contract=implement, lane=B). "
        "cursor-auto implement is the life seat (no team_dispatch). "
        "Explore recon in-tab · design/judgment → cdp/fable-5.1 · "
        "independent check → cdp/opus-5. Before any STAY verdict. "
        "STAY = no hop, never = no dispatch.",
        "Hop only when autonomous follow-up remains (live watcher, dispatched NOW, "
        "or CONTEXT_BUDGET with remaining work). HOLD_MERGE / empty NOW / quiet tick "
        "→ STAY — do not hop.",
        "LOAD AND EXECUTE: runbook:bus-consult-watcher (legs 1-3 atomic); "
        "runbook:liaison-operator-guide when a ruling or how-to moves; "
        "git-posture § Land on every land (merge, keep both).",
        "§ Peer-house: isolate; collide ⇒ keep both; this seat repo-write → "
        "cursor-sdk; life repo-write → cursor-auto; then cdp/opus-5.5 (check) → "
        "cdp/fable-5.1 (design/judgment); cursor/claude-opus-5-5 "
        "last-resort only; ¬ cursor/claude-fable-5-1; page human only on "
        "OPERATOR_GATE after that ladder. ¬ hop away unreconciled.",
        f"Guard: workspace must be `{workspace}` — otherwise stop and say so.",
        f"NOW: {row}",
        *arm_lines,
        LOOP_REBUILD.format(root=root_id),
        "Then: harvest watcher wakes -> fold scoreboard -> Plan -> Dispatch "
        "(+watcher) -> CHECKPOINT. Skip CreateGoal. Attach `tail --label` per ARM "
        "label (pollers survive retire). "
        "Hop only if hop_qualifies; else STAY. "
        "STAY with no playable row and no live watcher: do not leave the loop running.",
        OPERATOR_LOOP,
    ]
    return "\n".join(lines) + "\n"


def _bus_auth_headers() -> dict[str, str]:
    return agent_bus_bearer_headers()


def seal_hop_window(
    root_id: str,
    *,
    transcript_id: str,
    residue: str | None = None,
    timeout_s: float = 120.0,
    from_agent: str = "cursor",
) -> dict[str, Any]:
    """Seal the departing IDE tab as ``channel=hop`` before keystroking the successor.

    Posts Stargate ``/api/v1/continuity/checkpoint`` with ``pre_consolidate=False``,
    then blocks on the route's ``poll_hint`` until the CHECKPOINT turn lands. Returns
    a refusal dict on any failure — callers must not keystroke when ``ok`` is false.
    """
    body: dict[str, Any] = {
        "thread": root_id,
        "surface": "cursor",
        "from_agent": from_agent,
        "transcript_id": transcript_id,
        "pre_consolidate": False,
        "channel": "hop",
    }
    if residue is not None:
        body["residue"] = residue
    try:
        with make_sync_client(
            DEFAULT_STARGATE_URL, timeout=_CHECKPOINT_TIMEOUT_S
        ) as client:
            resp = client.post("/api/v1/continuity/checkpoint", json=body)
    except httpx.HTTPError as exc:
        return {"ok": False, "phase": "stargate_unreachable", "error": str(exc)}
    if resp.status_code >= 400:
        try:
            payload = resp.json()
        except ValueError:
            payload = {"message": resp.text[-500:]}
        code = (
            (payload.get("error") or {}).get("code")
            if isinstance(payload, dict)
            else None
        )
        return {
            "ok": False,
            "phase": str(code or f"checkpoint_http_{resp.status_code}"),
            "status_code": resp.status_code,
            "error": payload,
        }
    try:
        accepted = resp.json()
    except ValueError:
        return {"ok": False, "phase": "checkpoint_bad_response"}
    poll_hint = accepted.get("poll_hint") if isinstance(accepted, dict) else None
    args = (
        poll_hint.get("arguments_json")
        if isinstance(poll_hint, dict)
        else None
    ) or {}
    after_turn = int(args.get("after_turn") or 0)
    wait_from = str(args.get("from_agent") or from_agent)
    completion = str(args.get("completion") or "first_reply_from")
    deadline = time.time() + max(timeout_s, 1.0)
    while time.time() < deadline:
        wait_budget = min(_WAIT_SLICE_S, max(1.0, deadline - time.time()))
        headers = _bus_auth_headers()
        if not headers:
            return {
                "ok": False,
                "phase": "wait_auth_missing",
                "error": "AGENT_BUS_TOKEN unset (env and ~/.gateway/mcp.yaml)",
            }
        try:
            with make_sync_client(
                DEFAULT_AGENT_BUS_URL, timeout=wait_budget + 10.0
            ) as bus:
                wait_resp = bus.get(
                    f"/threads/{root_id}/wait",
                    params={
                        "after_turn": after_turn,
                        "wait": min(wait_budget, 60.0),
                        "completion": completion,
                        "from_agent": wait_from,
                    },
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            return {"ok": False, "phase": "wait_unreachable", "error": str(exc)}
        if wait_resp.status_code >= 400:
            return {
                "ok": False,
                "phase": f"wait_http_{wait_resp.status_code}",
                "status_code": wait_resp.status_code,
            }
        try:
            wait_payload = wait_resp.json()
        except ValueError:
            return {"ok": False, "phase": "wait_bad_response"}
        if wait_payload.get("complete"):
            turn_raw = wait_payload.get("qualifying_reply_turn")
            bus_turn = int(turn_raw) if turn_raw is not None else None
            return {
                "ok": True,
                "phase": "sealed",
                "bus_turn": bus_turn,
                "execution_id": accepted.get("execution_id"),
            }
    return {"ok": False, "phase": "seal_timeout", "bus_turn": None}


def find_transcript_id(
    first_user_text: str, transcripts_dir: Path = AGENT_TRANSCRIPTS
) -> str | None:
    """Transcript id of the tab whose first user message contains ``first_user_text``.

    A predecessor hop's jsonl keeps taking mtime updates after the successor
    lands, so mtime-newest among ``resume <R>`` matches is the old tab (hops
    23–24, 2026-09-12). Among matches, prefer the highest ``tip_cp=`` in the
    first line; mtime is the tie-break. Prefer a unique needle (``tip_cp=N``)
    when the caller already knows it. Scan shared with ``ide_budget``.
    """
    matches = first_line_matches(first_user_text, transcripts_dir)
    if not matches:
        return None
    matches.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return matches[0][2]


def remote_launch_command(
    remote_msg_path: str,
    *,
    remote_repo: str,
    focus_title: str | None = None,
    no_raise: bool = False,
) -> str:
    """Build the GUI-host command.

    Keys on the host are Ctrl+T (IDE new tab) → Ctrl+/ grok-4.7 → paste →
    Ctrl+Enter. Raise is compositor ``activate`` on ``focus_title`` (default
    ``Cursor Agents``). ``no_raise`` types into the already-focused window when
    the operator said so. ``--raise-uri`` / ``vscode-remote://`` is not a hop
    raise — Firefox owns that scheme (10588 Fire 2).
    """
    if no_raise:
        focus = "--no-raise"
    else:
        title = focus_title or focus_title_for()
        focus = (
            f"--no-raise --focus-title {shlex.quote(title)} "
            f"--focus-app-id {shlex.quote(AGENTS_WINDOW_APP_ID)}"
        )
    return (
        "export WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000; "
        f"python3 {shlex.quote(f'{remote_repo}/{KEYSTROKE_SCRIPT}')} launch "
        f"--message-file {shlex.quote(remote_msg_path)} "
        f"--repo {shlex.quote(remote_repo)} "
        f"{focus}"
    )


def _parse_keystroke_stdout(stdout: str) -> dict[str, Any]:
    """Structured keystroke telemetry from the remote launcher's stdout."""
    text = stdout.strip()
    if not text:
        return {"raw_stdout": ""}
    for candidate in (text, text.splitlines()[-1]):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {"raw_stdout": stdout[-500:]}


def remote_toplevels(gui_host: str) -> list[dict[str, Any]] | str:
    """Compositor toplevel list from the GUI host — the ``not_landed`` diagnostic.

    A hop can report ``activated`` on a title match and still not produce a chat:
    the matched window may be the only Cursor toplevel while no editor window is
    open, or the title may match a surface that does not host chats. Neither is
    visible from the hub afterwards, so a failed hop captures the list at failure
    time instead of costing an operator round-trip (hop 2026-09-15 05:12Z).
    """
    cmd = (
        "export WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000; "
        f"python3 {shlex.quote(f'{DEFAULT_REMOTE_REPO}/scripts/cosmic_focus_window.py')} list"
    )
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", gui_host, cmd],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"toplevel probe failed: {exc}"
    if proc.returncode != 0:
        return f"toplevel probe exit {proc.returncode}: {proc.stderr[-200:]}"
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return proc.stdout[-500:]
    rows = payload.get("toplevels") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return proc.stdout[-500:]
    return [
        {k: row.get(k) for k in ("title", "app_id", "activated")}
        for row in rows
        if isinstance(row, dict)
    ]


def session_unreachable(gui_host: str) -> dict[str, Any] | None:
    """Refusal payload when the GUI session cannot receive keys; ``None`` when it can.

    A COSMIC lock screen is invisible to every cheap probe: ``loginctl`` reports
    ``LockedHint=no`` (jupiter session 4, 2026-09-15 05:20Z) and the lock surface
    is not a toplevel, so the window list looks normal and ``activate`` on a
    title match still returns a handle. The one observable that does move is
    activation itself — an unlocked desktop always has exactly one activated
    toplevel, and a locked one has none, because the lock surface holds focus.

    Refusing here matters beyond a wasted hop: with the session locked, the
    2026-09-15 05:12Z hop typed its whole 1946-byte message and Ctrl+Enter into
    the lock screen's password field, which is both a failed hop and somewhere a
    handoff message must never go.

    A probe that cannot reach the host is not a refusal — SSH failure is handled
    downstream with its own phase, so an unreadable list returns ``None``.
    """
    toplevels = remote_toplevels(gui_host)
    if not isinstance(toplevels, list) or not toplevels:
        return None
    if any(row.get("activated") for row in toplevels):
        return None
    return {
        "toplevels": toplevels,
        "fix": (
            f"no activated toplevel on {gui_host} — the session is locked or has "
            "no focused window, so keys would go to the lock screen (and a paste "
            "into its password field reads as a failed login). Unlock the desktop, "
            "then re-fire; the hop message is already written and re-fire is idempotent."
        ),
    }


# Seats were filling an unset host from specimen tests (jupiter) while the
# operator was on another node. The refusal text is the reminder; there is
# no default host.
GUI_HOST_UNSET_FIX = (
    "Ask the operator which node they are on before any keystroke. "
    "Do not default to jupiter or copy a specimen host. "
    "After they name it: --set gui_host=<ssh host> on the house, "
    "or pass --gui-host for this hop only."
)


def fire_ide_hop(
    message: str,
    *,
    root_id: str,
    seal: dict[str, Any],
    gui_host: str | None,
    remote_repo: str = DEFAULT_REMOTE_REPO,
    dry_run: bool = False,
    no_raise: bool = False,
    landing_timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Write the hop message where the GUI host sees it (NFS) and keystroke it into a new chat.

    The seal receipt (``seal_hop_window``) is the hard precondition for keystroke:
    callers must pass ``seal`` with ``ok`` true after CHECKPOINT lands; otherwise this
    function refuses before any file write or SSH.

    Refuses when ``gui_host`` is unset (no fallback host). The refusal tells
    the seat to ask the operator which node, not to invent jupiter. The agents window
    (``app_id=cursor``, title ``Cursor Agents``) is focused through
    ``zcosmic_toplevel_manager_v1`` and verified activated before any key is
    sent. ``cursor --folder-uri`` / ``vscode-remote://`` is not used — Firefox
    owns that scheme (10588). ``no_raise`` skips activate only when the operator
    is on the window and says so. ``ok`` means **landed**: a new agent transcript
    carrying the hop header appeared after the keystrokes — sent keys are not a hop.
    After ``ok`` the hop script must ``retire_departing_tab`` (loops, pollers, tails, ``ide:``
    lock). UpdateGoal only if a leftover native goal is still injecting wakes.
    """
    if not seal.get("ok"):
        return {
            "ok": False,
            "phase": "seal_receipt_missing",
            "root": root_id,
            "bus_turn": seal.get("bus_turn"),
            "execution_id": seal.get("execution_id"),
        }
    if not gui_host:
        return {
            "ok": False,
            "phase": "gui_host_unset",
            "root": root_id,
            "fix": GUI_HOST_UNSET_FIX,
        }
    focus_title = None if no_raise else focus_title_for(policy_focus_title(root_id))
    HANDOFF_MSG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    msg_path = HANDOFF_MSG_DIR / f"liaison-{root_id}-{stamp}.md"
    durable_write_text(msg_path, message)
    remote_msg = f"{remote_repo}/{msg_path.relative_to(_REPO)}"
    cmd = remote_launch_command(
        remote_msg,
        remote_repo=remote_repo,
        focus_title=focus_title,
        no_raise=no_raise,
    )
    result: dict[str, Any] = {
        "root": root_id,
        "message_path": str(msg_path),
        "gui_host": gui_host,
        "raise_uri": None,
        "focus_title": focus_title,
        "remote_cmd": cmd,
        "bus_turn": seal.get("bus_turn"),
        "execution_id": seal.get("execution_id"),
    }
    if dry_run:
        return {"ok": True, "dry_run": True, **result}
    locked = session_unreachable(gui_host)
    if locked is not None:
        return {"ok": False, "phase": "session_locked", **locked, **result}
    fired_at = time.time()
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", gui_host, cmd],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "phase": "ssh_timeout", **result}
    if proc.returncode != 0:
        return {
            "ok": False,
            "phase": "keystroke",
            "returncode": proc.returncode,
            "stderr": proc.stderr[-1000:],
            "stdout": proc.stdout[-1000:],
            **result,
        }
    # The remote prints ``json.dumps(out, indent=2)``, so the last stdout line is
    # a bare ``}`` — parsing only that line silently discarded the activate proof
    # on every hop and left ``raw_stdout`` as the sole telemetry (hop 2026-09-15
    # 05:12Z: diagnosis of a not_landed needed the focused handle and could not
    # read it). Parse the whole payload; fall back to the last line for a remote
    # that ever emits single-line JSON.
    keystroke = _parse_keystroke_stdout(proc.stdout)
    landed_id = wait_for_landed_transcript(
        hop_header_line(message),
        since_epoch=fired_at,
        transcripts_dir=AGENT_TRANSCRIPTS,
        timeout_s=landing_timeout_s,
    )
    if landed_id is None:
        toplevels = remote_toplevels(gui_host)
        cursor_windows = (
            [row for row in toplevels if row.get("app_id") == "cursor"]
            if isinstance(toplevels, list)
            else []
        )
        return {
            "ok": False,
            "phase": "not_landed",
            "keystroke": keystroke,
            "toplevels": toplevels,
            "cursor_windows": cursor_windows,
            "fix": (
                "no new Cursor chat carries the hop header — Ctrl+T / paste / "
                f"Ctrl+Enter did not submit, or keys hit another window; "
                f"focus was {focus_title!r} on {gui_host}. Check cursor_windows: "
                "a lone 'Cursor Agents' toplevel with no editor window, or a "
                "Cursor backend error, both activate cleanly and still land nothing."
            ),
            **result,
        }
    return {
        "ok": True,
        "landed_transcript_id": landed_id,
        "keystroke": keystroke,
        **result,
    }
