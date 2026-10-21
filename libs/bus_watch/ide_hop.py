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

Default GUI host is **``:8``** (Xvfb Cursor Agents at ``127.0.0.1:9338``). Override
with ``liaison-tick.py --set gui_host=<ssh host>`` when the operator sits on a
physical COSMIC node instead.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
from cursor_bridge.cursor_agents_hop import (
    cursor_debug_url_for_gui_host,
    is_cursor_xvfb_host,
    launch_cursor_agents_tab,
)
from durable_io.atomic import durable_write_text
from transport_utils import (
    DEFAULT_AGENT_BUS_URL,
    DEFAULT_STARGATE_URL,
    make_sync_client,
)

from bus_watch.digest_budget import POLICY_DEFAULTS, agent_bus_bearer_headers
from bus_watch.doorbell_skills import primary_liaison_slug
from bus_watch.fable_lock import (
    HOUSE_LABEL_PREFIX,
    WATCH_DIR,
    bus_watch_repo_root,
    read_lock,
)
from bus_watch.ide_budget import AGENT_TRANSCRIPTS, first_line_matches
from bus_watch.ide_hop_land import (
    DEFAULT_LANDING_TIMEOUT_S,
    mark_expired,
    mark_fired,
    mark_landed,
)
from bus_watch.ide_hop_landing import (
    find_transcript_with_hop_header,
    focus_title_for,
    hop_header_line,
    hop_land_identity,
    list_resume_transcript_ids,
    wait_for_landed_transcript,
)
from bus_watch.ide_hop_mutex import (
    release_hop_mutex,
    release_window_mutex,
    try_acquire_hop_mutex,
    try_acquire_window_mutex,
)
from bus_watch.liaison_digest import effective_policy
from bus_watch.state import read_state

_REPO = bus_watch_repo_root()
HANDOFF_MSG_DIR = WATCH_DIR / "handoff-messages"
KEYSTROKE_SCRIPT = "scripts/orchestrator_tab_keystroke.py"
# predicate_unmet is not terminal — CDP consults sit there until the first
# qualifying reply (a:33284; hop 15 ARM: none live while G6 was in_flight).
LIVE_WATCHER_STATUSES = frozenset({"polling", "running", "predicate_unmet"})
DEFAULT_REMOTE_REPO = os.environ.get("ORCHESTRATOR_REPO", str(_REPO))
DEFAULT_HOP_MODEL_QUERY = "Grok 4.7"
_CHECKPOINT_TIMEOUT_S = 15.0
_WAIT_SLICE_S = 55.0


def policy_gui_host(root_id: str, watch_dir: Path = WATCH_DIR) -> str:
    """``policy.gui_host`` from liaison tick state; ``:8`` Xvfb when unset."""
    state = read_state(watch_dir / f"liaison-{root_id}.tick.json")
    host = effective_policy(state).get("gui_host")
    return str(host) if host else ":8"


def tick_register(root_id: str, watch_dir: Path = WATCH_DIR) -> str:
    """``register`` from the liaison tick state (``attended`` when unset)."""
    state = read_state(watch_dir / f"liaison-{root_id}.tick.json")
    return str(state.get("register") or "attended")


def policy_judgment_posture(root_id: str, watch_dir: Path = WATCH_DIR) -> str:
    """``policy.judgment_posture`` from liaison tick state; ``distributed`` when unset."""
    state = read_state(watch_dir / f"liaison-{root_id}.tick.json")
    val = effective_policy(state).get("judgment_posture")
    if not val:
        return "distributed"
    normalized = str(val).strip()
    if normalized == "in_seat":
        return "in_seat"
    return "distributed"


def policy_focus_title(root_id: str, watch_dir: Path = WATCH_DIR) -> str | None:
    """``policy.hop_focus_title`` — operator override of the compositor title substring.

    Default is ``Cursor Agents``. Override only when the live toplevel title differs.
    """
    state = read_state(watch_dir / f"liaison-{root_id}.tick.json")
    title = effective_policy(state).get("hop_focus_title")
    return str(title) if title else None


def policy_hop_model_query(root_id: str, watch_dir: Path = WATCH_DIR) -> str:
    """``policy.hop_model_query`` — Ctrl+/ filter after Glass ``Ctrl+N``; default Grok 4.7.

    Missing key → ``DEFAULT_HOP_MODEL_QUERY``. An explicit empty string in policy
    disables ``--model-query`` so the tab keeps the operator's last model pick.
    """
    state = read_state(watch_dir / f"liaison-{root_id}.tick.json")
    val = effective_policy(state).get("hop_model_query")
    if val is None:
        return DEFAULT_HOP_MODEL_QUERY
    return str(val).strip()


_ALLOWED_HOP_MODEL_EFFORT = frozenset({"low", "medium", "high", "xhigh"})


def policy_hop_model_knobs(root_id: str, watch_dir: Path = WATCH_DIR) -> dict[str, str]:
    """``policy.hop_model_knobs`` — Glass ``createAgent`` effort/fast after catalog resolve.

    Merges per-root tick overrides onto ``POLICY_DEFAULTS`` (xhigh, non-fast). House
    knobs override label-derived effort and catalog parameters on hop.
    """
    state = read_state(watch_dir / f"liaison-{root_id}.tick.json")
    defaults_raw = POLICY_DEFAULTS.get("hop_model_knobs") or {
        "effort": "xhigh",
        "fast": "false",
    }
    merged = {str(k): str(v) for k, v in defaults_raw.items()}
    raw = effective_policy(state).get("hop_model_knobs")
    if isinstance(raw, dict):
        for key, val in raw.items():
            merged[str(key)] = str(val)
    return merged


def validate_hop_model_knobs(knobs: dict[str, str]) -> str | None:
    """Return a hop refusal message when ``effort`` is set but not an allowed Glass weight."""
    effort = knobs.get("effort")
    if effort is None or str(effort).strip() == "":
        return None
    normalized = str(effort).strip().lower()
    if normalized in _ALLOWED_HOP_MODEL_EFFORT:
        return None
    allowed = ", ".join(sorted(_ALLOWED_HOP_MODEL_EFFORT))
    return f"policy.hop_model_knobs.effort {effort!r} is invalid; allowed: {allowed}"


TAIL_RECIPE = (
    "tail {label}: watch-supervise.sh tail --label {label} (background Shell, "
    "block_until_ms 0, no notify_on_output). The shell completion is the one "
    "harvest turn. A pattern notify on this exit-on-complete tail opens a "
    "second turn that covers the reply (friction 37400)."
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
    "OPERATOR LOOP: close with a situation report to the operator. "
    "Where this is going: <next leg>. Who carries it: this seat. "
    "Report again: <when>. "
    "You run the bus. Do not ask the operator to abort, hire, or edit. "
    "A finished watch with no next leg is not a situation report."
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


_DISTRIBUTED_JUDGMENT = (
    "Explore recon in-tab · orchestrate in-tab on cursor/grok-4.7 (effort=xhigh) · "
    "outsource regular judgment/design/advice → cursor/gemini-4-argon "
    "(effort=high, fast=false) after retrieval-before-authoring (rag scopes + "
    "targeted reads → retrieval report) · independent check → cdp/opus-5.5 "
    "(checker ≠ author ≠ advisor). divergent-ideation-v1 failure at operator_split "
    "⇒ ¬ block: pass the retrieval report + 2-3 rival frames straight to "
    "cursor/gemini-4-argon under exception: divergent_ideation_degraded_18362; "
    "consult_ref is still required. Before any STAY verdict. "
)
_IN_SEAT_JUDGMENT = (
    "Explore recon in-tab · Judgment forks bind IN-SEAT under the house's explicit "
    "judgment_posture=in_seat override (no Argon confer, no CDP consult to decide); "
    "work is reviewed out-of-seat by a fresh non-author seat before land; "
    "independent check → cdp/opus-5.5 still applies. "
)
_PEER_HOUSE_DISTRIBUTED = (
    "§ Peer-house: isolate; collide ⇒ keep both; this seat repo-write → "
    "cursor-sdk; life repo-write → life_dispatch; orchestration stays in-tab on "
    "cursor/grok-4.7 (effort=xhigh); then cursor/gemini-4-argon (effort=high, "
    "design/judgment/advice, after retrieval-before-authoring) → cdp/opus-5.5 "
    "(independent check); cursor/grok-4.7 for live checkout; "
    "¬ cursor/claude-*; page human only on "
    "OPERATOR_GATE after that ladder. ¬ hop away unreconciled."
)
_PEER_HOUSE_IN_SEAT = (
    "§ Peer-house: isolate; collide ⇒ keep both; this seat repo-write → "
    "cursor-sdk; life repo-write → life_dispatch; orchestration and judgment "
    "stay in-tab on cursor/grok-4.7 (effort=xhigh) → cdp/opus-5.5 "
    "(independent check); cursor/grok-4.7 for live checkout; "
    "¬ cursor/claude-*; page human only on "
    "OPERATOR_GATE after that ladder. ¬ hop away unreconciled."
)
_DIVERGENT_DISTRIBUTED = (
    "DIVERGENT / WRITING DEFAULT: On open design/architecture/strategy forks, "
    "wide/frontier detents, or retry(same_AC), run pipeline(op='run', "
    "pipeline_id='divergent-ideation-v1', messages=[...]) before binding; on high-stakes "
    "outbound prose, chain divergent-ideation-v1 -> writer-specialist-v1 under "
    "outbound-voice-spec. Failure at operator_split ⇒ ¬ block and ¬ retry: hand the "
    "retrieval report + 2-3 rival frames to cursor/gemini-4-argon under "
    "exception: divergent_ideation_degraded_18362, record consult_ref, proceed. "
    "Skip only on closed-spec mechanical/trivial first passes."
)
_DIVERGENT_IN_SEAT = (
    "DIVERGENT / WRITING DEFAULT: On open design/architecture/strategy forks, "
    "wide/frontier detents, or retry(same_AC), run pipeline(op='run', "
    "pipeline_id='divergent-ideation-v1', messages=[...]) before binding; on high-stakes "
    "outbound prose, chain divergent-ideation-v1 -> writer-specialist-v1 under "
    "outbound-voice-spec. Failure at operator_split ⇒ ¬ block and ¬ retry: bind "
    "in-seat on cursor/grok-4.7 (effort=xhigh), record the retrieval report, proceed. "
    "Skip only on closed-spec mechanical/trivial first passes."
)


def build_ide_hop_message(
    root_id: str,
    *,
    row: str,
    arm_labels: list[str],
    tip_cp_ordinal: int | None = None,
    workspace: str = "universal-llm-gateway",
    register: str = "attended",
    judgment_posture: str = "distributed",
    gui_host: str = ":8",
) -> str:
    """First user message of the successor tab; ``resume <R>`` first so the fence hook fires.

    ``register`` is read from the tick state by the hop script: an overnight chain must
    tell the successor it is autonomous (bind forks itself, page only on designed
    stops, hop itself) in the first line, not leave it to a digest field it may skim.
    """
    if arm_labels:
        arm_lines = [
            f"ARM: live watcher {label} (push-wake only — do not attach tail)"
            for label in arm_labels
        ]
    else:
        arm_lines = [
            f"ARM: none live — Plan from the digest "
            f"(`scripts/liaison-tick.py --root {root_id} --once`)."
        ]
    tip = f" tip_cp={tip_cp_ordinal}" if tip_cp_ordinal is not None else ""
    if judgment_posture == "in_seat":
        judgment_line = _IN_SEAT_JUDGMENT
        peer_house_line = _PEER_HOUSE_IN_SEAT
        divergent_line = _DIVERGENT_IN_SEAT
    else:
        judgment_line = _DISTRIBUTED_JUDGMENT
        peer_house_line = _PEER_HOUSE_DISTRIBUTED
        divergent_line = _DIVERGENT_DISTRIBUTED
    host_label = str(gui_host or ":8").strip() or ":8"
    hop_query = policy_hop_model_query(root_id)
    hop_knobs_json = json.dumps(policy_hop_model_knobs(root_id), separators=(",", ":"))
    lines = [
        f"resume {root_id}",
        "",
        f"Liaison IDE hop ({register} register){tip}. "
        f"LOAD the {primary_liaison_slug('ide')} skill (do not skim).",
        (
            "HOP: When the operator tells this tab to hop (`hop`, `ide-hop`, `hop now`) "
            "or when budget.stop_class == 'CONTEXT_BUDGET' (tool_calls >= 35 or "
            "used_tokens >= 55%), immediately seal and hop on this Glass "
            f"({host_label}) interface "
            f'via: "$HOME/.venvs/universal/bin/python" scripts/liaison-ide-hop.py '
            f'--root {root_id} --row "{row}" --transcript-id <uuid> '
            f"--gui-host {host_label} --force "
            "(include --force when the operator explicitly requested the hop)."
        ),
        (
            "This house's Glass hop model is policy.hop_model_query "
            f"({hop_query!r}) plus policy.hop_model_knobs ({hop_knobs_json}), "
            "applied by createAgent on :8 and orion-node."
        ),
        # STAY governs the hop only. Tab 12e32c8b (10479, 2026-09-13 06:56Z) read
        # it as "do not dispatch" and parked at 0.7 % with NOW=R12 undone.
        "NOW non-empty ⇒ dispatch its first leg from this tab. "
        "LOAD liaison-cursor. Repo write on this seat → "
        "team_dispatch(seat=cursor-sdk, job=implement, lane=B). "
        "life repo-write → life_dispatch. "
        f"{judgment_line}"
        "STAY = no hop, never = no dispatch.",
        "Hop only when autonomous follow-up remains (live watcher, dispatched NOW, "
        "or CONTEXT_BUDGET with remaining work). HOLD_MERGE / empty NOW / quiet tick "
        "→ STAY — do not hop.",
        "LOAD AND EXECUTE: runbook:liaison-operator-guide when a ruling or how-to moves; "
        "git-posture § Land on every land (merge, keep both).",
        peer_house_line,
        f"Guard: workspace must be `{workspace}` — otherwise stop and say so.",
        divergent_line,
        f"NOW: {row}",
        *arm_lines,
        "WAKE: Push-driven via endpoint_wake_dispatcher -> display-chat-wake "
        "(BRIDGE_WAKE on house subscription + child-lane parent_thread). "
        "Do NOT start or attach watch-supervise.sh pollers/tails and do NOT start "
        "liaison-tick.py --loop or --heartbeat.",
        "SEAT ID: Your tab transcript UUID is the directory name in <user_info> under "
        "'Current agent's store: .../cursor_agent_stores/<uuid>/files'. Pass "
        f"`--holder ide:<uuid>` to `scripts/liaison-tick.py --root {root_id} --claim "
        "--take-over`; never query state.vscdb or run --find-transcript to discover "
        "your own UUID.",
        f"RENAME: Rename this chat to '{root_id} <slug>' via rename_chat immediately "
        "after continuity(op=resume) so display-chat-wake routes wakes here.",
        "Then: if NOW is not yet admitted, dispatch its first leg; if NOW is already "
        "in flight, do not spawn a second dispatch or background shell watcher — "
        "report status and end your turn immediately so this tab is idle to receive "
        "BRIDGE_WAKE push wakes. Skip CreateGoal.",
        OPERATOR_LOOP,
    ]
    return "\n".join(lines) + "\n"


def _bus_auth_headers() -> dict[str, str]:
    return agent_bus_bearer_headers()


def build_hop_preamble(
    root_id: str,
    *,
    row: str,
    arm_labels: list[str] | None = None,
    tip_cp_ordinal: int | None = None,
    register: str = "attended",
) -> dict[str, str]:
    """Structured stance/facts/next_action sealed into the hop checkpoint sidecar."""
    labels = arm_labels or []
    arm_text = ", ".join(labels) if labels else "none live"
    tip_token = (
        f"tip_cp={tip_cp_ordinal}" if tip_cp_ordinal is not None else "tip_cp=unknown"
    )
    stance = (
        f"Role:root {root_id} on universal-llm-gateway ({register} register); "
        "this seat resumes the continuity house (LOAD ide-continuity, LOAD "
        "liaison-cursor)."
    )
    facts = (
        f"{tip_token}. ARM watchers: {arm_text}. NOW: {row.strip() or '(empty)'}. "
        "These facts are a snapshot taken at checkpoint time and may have drifted."
    )
    row_key = row.strip().casefold()
    if row_key and row_key != "quiet":
        next_action = (
            "Dispatch the first leg of NOW via team_dispatch or life_dispatch per "
            "the liaison ladder; on open forks/retries run divergent-ideation-v1 before "
            "binding; on high-stakes outbound prose chain writer-specialist-v1 after "
            "outbound-voice-spec; rely on push wakes (display-chat-wake); do not attach "
            "watch-supervise.sh tails; stop when the dispatch admits or the leg completes."
        )
    else:
        next_action = (
            f"Run scripts/liaison-tick.py --root {root_id} --once, harvest wakes, "
            "fold scoreboard; STAY if no playable row and no live watcher."
        )
    return {"stance": stance, "facts": facts, "next_action": next_action}


def seal_hop_window(
    root_id: str,
    *,
    transcript_id: str,
    residue: str | None = None,
    preamble: dict[str, str] | None = None,
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
    if preamble:
        body["preamble"] = preamble
        for key in ("stance", "facts", "next_action"):
            if preamble.get(key):
                body[key] = preamble[key]
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
        poll_hint.get("arguments_json") if isinstance(poll_hint, dict) else None
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
    model_query: str = DEFAULT_HOP_MODEL_QUERY,
) -> str:
    """Build the GUI-host Glass hop command.

    Glass (operator 2026-10-02): Ctrl+n (lowercase n, Shift released) → paste →
    Ctrl+Enter via ``glass-launch``. IDE ``launch`` (Ctrl+T) hits the wrong
    toplevel and does not open a Glass agent. ``focus_title`` / ``no_raise`` are
    accepted for call-site compatibility; Glass picks the Agents window itself.
    Non-empty ``model_query`` appends ``--model-query`` so Ctrl+/ selects that model.
    """
    del focus_title, no_raise  # Glass path focuses Agents; IDE flags unused
    base = (
        "export WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000; "
        f"python3 {shlex.quote(f'{remote_repo}/{KEYSTROKE_SCRIPT}')} glass-launch "
        f"--message-file {shlex.quote(remote_msg_path)} "
        f"--repo {shlex.quote(remote_repo)}"
    )
    if model_query:
        return f"{base} --model-query {shlex.quote(model_query)}"
    return base


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

    Xvfb ``:8`` Cursor Agents hops use CDP locally and never hit a COSMIC lock screen.

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
    if is_cursor_xvfb_host(gui_host):
        return None
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


GUI_HOST_UNSET_FIX = (
    "Hop target unset and :8 Xvfb unreachable. "
    "Confirm Cursor Agents on :8 (127.0.0.1:9338) or "
    "--set gui_host=<ssh host> / --gui-host for COSMIC."
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
    landing_timeout_s: float = DEFAULT_LANDING_TIMEOUT_S,
    departing_transcript_id: str | None = None,
    on_fired: Any | None = None,
    armed_since: Any | None = None,
    model_query: str | None = None,
    hop_mutex_preheld: bool = False,
) -> dict[str, Any]:
    """Write the hop message where the GUI host sees it (NFS) and keystroke it into a new chat.

    The seal receipt (``seal_hop_window``) is the hard precondition for keystroke:
    callers must pass ``seal`` with ``ok`` true after CHECKPOINT lands; otherwise this
    function refuses before any file write or SSH.

    Defaults ``gui_host`` to ``:8`` when omitted or empty. The agents window
    (``app_id=cursor``, title ``Cursor Agents``) is focused through
    ``zcosmic_toplevel_manager_v1`` and verified activated before any key is
    sent. ``cursor --folder-uri`` / ``vscode-remote://`` is not used — Firefox
    owns that scheme (10588). ``no_raise`` skips activate only when the operator
    is on the window and says so.

    Land proof (a:38474): after keystroke write ``state=fired``, call ``on_fired``
    (script quiesces), then wait up to ``landing_timeout_s`` (default 600) with
    fence-first then transcript fallback. ``ok`` means **landed**. Timeout →
    ``phase=expired`` / OPERATOR_GATE — never ``not_landed`` re-fire.
    ``departing_transcript_id`` is never accepted as land (15456#4 F2).
    After ``ok`` the hop script must ``retire_departing_tab``.
    """
    if not seal.get("ok"):
        return {
            "ok": False,
            "phase": "seal_receipt_missing",
            "root": root_id,
            "bus_turn": seal.get("bus_turn"),
            "execution_id": seal.get("execution_id"),
        }
    resolved_gui_host = str(gui_host or "").strip() or ":8"
    focus_title = None if no_raise else focus_title_for(policy_focus_title(root_id))
    resolved_model_query = (
        policy_hop_model_query(root_id) if model_query is None else model_query
    )
    resolved_hop_knobs: dict[str, str] | None = None
    if str(resolved_model_query or "").strip():
        resolved_hop_knobs = policy_hop_model_knobs(root_id)
        knob_err = validate_hop_model_knobs(resolved_hop_knobs)
        if knob_err:
            if hop_mutex_preheld:
                release_hop_mutex(root_id)
            return {
                "ok": False,
                "phase": "hop_model_knobs_invalid",
                "error": knob_err,
                "root": root_id,
                "model_query": resolved_model_query,
                "hop_model_knobs": resolved_hop_knobs,
            }
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
        model_query=resolved_model_query,
    )
    result: dict[str, Any] = {
        "root": root_id,
        "message_path": str(msg_path),
        "gui_host": resolved_gui_host,
        "raise_uri": None,
        "focus_title": focus_title,
        "model_query": resolved_model_query,
        "remote_cmd": cmd,
        "bus_turn": seal.get("bus_turn"),
        "execution_id": seal.get("execution_id"),
    }
    if dry_run:
        if hop_mutex_preheld:
            release_hop_mutex(root_id)
        return {"ok": True, "dry_run": True, **result}
    locked = session_unreachable(resolved_gui_host)
    if locked is not None:
        if hop_mutex_preheld:
            release_hop_mutex(root_id)
        return {"ok": False, "phase": "session_locked", **locked, **result}
    marker = hop_header_line(message)
    land_root, tip_cp = hop_land_identity(message, root_id=root_id)
    departing = (departing_transcript_id or "").strip() or None
    exclude = {departing} if departing else set()
    # Prefer skip when the exact land already exists (15456 ask 2) — do not Ctrl+N.
    # Never treat the departing tab as that land (15456#4 F2).
    # Tip hops: exact (root, tip). Tipless: only a *recent* land (partial hop /
    # SIGTERM re-fire grace) — a:38364/a:38439; lifelong marker match is wrong.
    partial_land_grace_s = 180.0
    pre_since = None if tip_cp is not None else (time.time() - partial_land_grace_s)
    pre_id, pre_tel = find_transcript_with_hop_header(
        marker,
        AGENT_TRANSCRIPTS,
        root_id=land_root,
        tip_cp=tip_cp,
        since_epoch=pre_since,
        exclude_ids=exclude,
    )
    if pre_id is not None and pre_id != departing:
        if hop_mutex_preheld:
            release_hop_mutex(root_id)
        return {
            "ok": True,
            "phase": "landed",
            "landed_transcript_id": pre_id,
            "landed_via": "pre_existing",
            "land_find": pre_tel,
            "keystroke": None,
            **result,
        }
    # Root mutex TTL covers the long land window so a second fire cannot stack.
    root_ttl = max(120.0, float(landing_timeout_s) + 30.0)
    acquired_root_here = False
    if hop_mutex_preheld:
        mutex = {"ok": True}
    else:
        mutex = try_acquire_hop_mutex(root_id, ttl_s=root_ttl)
        acquired_root_here = mutex.get("ok") is True
    if not mutex.get("ok"):
        return {"ok": False, **mutex, **result}
    window = try_acquire_window_mutex(
        resolved_gui_host, focus_title, root_id=root_id, ttl_s=root_ttl
    )
    if not window.get("ok"):
        if hop_mutex_preheld or acquired_root_here:
            release_hop_mutex(root_id)
        return {"ok": False, **window, **result}
    pre_existing: set[str] | None = None
    if tip_cp is None and land_root:
        pre_existing = list_resume_transcript_ids(land_root, AGENT_TRANSCRIPTS)
    hop_record: dict[str, Any] | None = None
    release_root_mutex = True
    try:
        if is_cursor_xvfb_host(resolved_gui_host):
            debug_url = cursor_debug_url_for_gui_host(resolved_gui_host)
            requested_agent_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"ulg-ide-hop:{root_id}:{tip_cp}:{seal.get('bus_turn')}",
                )
            )
            # Fence/transcript window. Not pre_since (None on tip hops, 180s
            # earlier on tipless) and not fired_at (sampled after return).
            land_since = float(int(time.time()) - 1)
            keystroke = launch_cursor_agents_tab(
                message,
                model_query=resolved_model_query,
                model_knobs=resolved_hop_knobs,
                debug_url=debug_url,
                gui_host=resolved_gui_host,
                requested_agent_id=requested_agent_id,
            )
            if not keystroke.get("ok"):
                release_root_mutex = False
                return {
                    "ok": False,
                    "phase": "keystroke",
                    "keystroke": keystroke,
                    **result,
                }
        else:
            try:
                land_since = float(int(time.time()) - 1)
                proc = subprocess.run(
                    [
                        "ssh",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "ConnectTimeout=20",
                        resolved_gui_host,
                        cmd,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
            except subprocess.TimeoutExpired:
                release_root_mutex = False
                return {"ok": False, "phase": "ssh_timeout", **result}
            if proc.returncode != 0:
                release_root_mutex = False
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
        # Deadline clock, after the keystroke returns. A sample taken before
        # the keystroke lets the dispatcher expire the hop while this call is
        # still inside ``wait_for_landed_transcript``. The transcript window
        # is land_since, sampled immediately before the keystroke.
        fired_at = time.time()
        hop_record = mark_fired(
            root=root_id,
            tip_cp=tip_cp,
            seal_bus_turn=seal.get("bus_turn"),
            departing_transcript_id=departing,
            fired_at=fired_at,
            deadline_s=landing_timeout_s,
            message_path=str(msg_path),
        )
        result["phase"] = "fired_pending"
        result["hop_land"] = hop_record
        if on_fired is not None:
            try:
                result["quiesce"] = on_fired()
            except Exception as exc:  # noqa: BLE001 — land wait still proceeds
                result["quiesce"] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}:{str(exc)[:160]}",
                }
        landed_id, land_tel = wait_for_landed_transcript(
            marker,
            since_epoch=land_since,
            transcripts_dir=AGENT_TRANSCRIPTS,
            timeout_s=landing_timeout_s,
            root_id=land_root,
            tip_cp=tip_cp,
            pre_existing_ids=pre_existing,
            exclude_ids=exclude,
            armed_since=armed_since,
        )
        landed_via = str((land_tel or {}).get("landed_via") or "wait")
        proof = str((land_tel or {}).get("proof") or landed_via)
        if landed_id is None:
            # One final transcript pass (fence already polled inside wait).
            landed_id, land_tel = find_transcript_with_hop_header(
                marker,
                AGENT_TRANSCRIPTS,
                root_id=land_root,
                tip_cp=tip_cp,
                since_epoch=None if tip_cp is not None else land_since,
                exclude_ids=exclude,
            )
            landed_via = "find_transcript"
            proof = "transcript"
        if landed_id is not None and departing and landed_id == departing:
            landed_id = None
            landed_via = "wait"
        if landed_id is None:
            if hop_record is not None:
                hop_record = mark_expired(hop_record)
            toplevels = (
                []
                if is_cursor_xvfb_host(resolved_gui_host)
                else remote_toplevels(resolved_gui_host)
            )
            cursor_windows = (
                [row for row in toplevels if row.get("app_id") == "cursor"]
                if isinstance(toplevels, list)
                else []
            )
            return {
                **result,
                "ok": False,
                "phase": "expired",
                "keystroke": keystroke,
                "toplevels": toplevels,
                "cursor_windows": cursor_windows,
                "land_find": land_tel,
                "hop_land": hop_record,
                "fix": (
                    "OPERATOR_GATE: no land proof "
                    f"(resume {land_root}, tip_cp={tip_cp}) within "
                    f"{landing_timeout_s:.0f}s of keystroke — fence armed row absent and "
                    "transcript fallback empty. Do NOT re-fire glass-launch / Ctrl+N "
                    "(a:38364 / a:38474). Manual paste once from message_path if still "
                    "needed; departing tab stays quiesced."
                ),
            }
        if hop_record is not None:
            proof_lit = "fence" if proof == "fence" else "transcript"
            hop_record = mark_landed(
                hop_record,
                landed_transcript_id=landed_id,
                proof=proof_lit,  # type: ignore[arg-type]
            )
        return {
            **result,
            "ok": True,
            "phase": "landed",
            "landed_transcript_id": landed_id,
            "landed_via": landed_via,
            "land_find": land_tel,
            "hop_land": hop_record,
            "keystroke": keystroke,
        }
    finally:
        release_window_mutex(resolved_gui_host, focus_title)
        if release_root_mutex and (hop_mutex_preheld or acquired_root_here):
            release_hop_mutex(root_id)


def finalize_xvfb_hop_sidebar(
    root_id: str,
    *,
    departing_agent_id: str,
    landed_agent_id: str,
    slug_fragment: str = "",
    gui_host: str | None = None,
) -> dict[str, Any]:
    """Prune predecessors, rename departing Glass row, subscribe successor."""
    from cursor_bridge.cursor_agents_hop import (
        cursor_debug_url_for_gui_host,
        prune_cursor_agents_predecessors,
        rename_agent_row_in_sidebar,
    )

    from bus_watch.cursor_xvfb_subscription import (
        cursor_xvfb_display_for_gui_host,
        parse_ide_holder_agent_id,
        subscribe_cursor_xvfb_house,
    )

    resolved_host = str(gui_host or policy_gui_host(root_id) or ":8").strip() or ":8"
    display = cursor_xvfb_display_for_gui_host(resolved_host)
    debug_url = cursor_debug_url_for_gui_host(resolved_host)

    out: dict[str, Any] = {"ok": True, "root_id": root_id, "display": display}
    if landed_agent_id:
        out["subscription_update"] = subscribe_cursor_xvfb_house(
            root_id,
            landed_agent_id,
            display=display,
            bound_by="ide-hop-finalize",
            evidence="libs/bus_watch/ide_hop.finalize_xvfb_hop_sidebar",
        )
    if departing_agent_id:
        lock = read_lock(root_id)
        holder_aid = parse_ide_holder_agent_id(str(lock.get("holder") or "")) or ""
        out["archived_departing_tab"] = prune_cursor_agents_predecessors(
            root_id,
            active_agent_id=landed_agent_id,
            departing_agent_id=departing_agent_id,
            holder_agent_id=holder_aid,
            keep_recent_predecessors=0,
            wait_until_idle_s=0.0,
            debug_url=debug_url,
        )
        retired_title = f"[retired] {root_id} {slug_fragment}".strip()
        out["renamed_departing_tab"] = rename_agent_row_in_sidebar(
            departing_agent_id,
            retired_title,
            debug_url=debug_url,
        )
    from bus_watch.impresario_ear_common import (
        hop_watermark_key,
        hop_watermark_present,
        maybe_enqueue_after_hop,
    )

    wm_key = hop_watermark_key(
        root_id, departing_transcript_id=departing_agent_id or None
    )
    if hop_watermark_present(wm_key):
        out["impresario_ear"] = False
    else:
        out["impresario_ear"] = maybe_enqueue_after_hop(
            root_thread=root_id,
            departing_transcript_id=departing_agent_id or None,
            channel="hop",
            to_agent="cursor",
        )
    return out


def cleanup_xvfb_sidebar_on_holder_claim(
    root_id: str,
    holder: str,
    *,
    retired_agent_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Archive only idle root rows already marked retired when a new ``ide:`` holder claims.

    Idle live seats (not retired) stay visible in the sidebar.
    """
    from cursor_bridge.cursor_agents_hop import prune_cursor_agents_predecessors

    from bus_watch.cursor_xvfb_subscription import parse_ide_holder_agent_id

    try:
        if not is_cursor_xvfb_host(policy_gui_host(root_id)):
            return {"ok": True, "skipped": "not_xvfb_host"}
        active_aid = parse_ide_holder_agent_id(holder) or ""
        if not active_aid:
            return {"ok": True, "skipped": "holder_not_ide_uuid"}
        host = policy_gui_host(root_id)
        debug_url = cursor_debug_url_for_gui_host(host)
        lock = read_lock(root_id)
        holder_aid = (
            parse_ide_holder_agent_id(str(lock.get("holder") or "")) or active_aid
        )
        return prune_cursor_agents_predecessors(
            root_id,
            active_agent_id=active_aid,
            departing_agent_id="",
            holder_agent_id=holder_aid,
            keep_recent_predecessors=0,
            wait_until_idle_s=0.0,
            select_active=False,
            only_retired=True,
            retired_agent_ids=retired_agent_ids,
            debug_url=debug_url,
        )
    except (Exception, SystemExit) as exc:
        return {"ok": False, "reason": f"cleanup_failed:{type(exc).__name__}"}
