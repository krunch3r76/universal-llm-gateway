"""Focus target and landing proof for the attended IDE hop.

Why this exists: on COSMIC (jupiter) a native-Wayland Cursor cannot raise itself on
``cursor --folder-uri`` (no activation token; 2026-09-12 04:24Z the
``vscode-remote://`` URI even went to Firefox), and driving the COSMIC launcher by
keystrokes guessed wrong twice (fuzzy-matched UMLet 06:09Z, launched a second IDE
window 06:11Z). Every hop before then typed ``resume <R>`` into whatever window the
operator had in front while the seat reported ``ok: true`` because keys had been
*sent*. Two corrections live here: the target window is named by what the
compositor actually reports — ``ext_foreign_toplevel_list_v1`` on jupiter lists the
agents window as ``app_id=cursor`` / ``title="Cursor Agents"`` (no repo, no SSH
marker in the title) — and a hop is ``landed`` only when a new agent transcript
carrying the hop header appears; sent keys are not a delivered hop.

Land identity (CDP 15456#4): decode the first JSONL user row, strip ``<user_query>``,
require ``resume <R>`` as the first body line and ``tip_cp=N(?!\\d)`` on the
``Liaison IDE hop`` line only — not raw-line regex anywhere in Standing/NOW (F1).
Exclude the departing transcript id on every path (F2).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

AGENTS_WINDOW_TITLE = "Cursor Agents"
AGENTS_WINDOW_APP_ID = "cursor"
DEFAULT_LANDING_TIMEOUT_S = 600.0
_USER_QUERY_RE = re.compile(
    r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL | re.IGNORECASE
)


def armed_fences_via_http(
    root_id: str,
    *,
    since_epoch: float,
    exclude_transcript_ids: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Read-only land probe via agent-bus GET ``/threads/{id}/resume-fence``.

    Hop shells do not set ``AGENT_BUS_DB_PATH`` (host default is
    ``~/.agent-bus/messages.db``; the store's bare ``connect()`` opens
    ``/data/messages.db`` and fails). The managed service owns the DB — probe
    through its HTTP route (a:38474 review B1 / agent-bus:15488#2).
    """
    from bus_watch.digest_budget import agent_bus_bearer_headers
    from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

    headers = agent_bus_bearer_headers()
    if not headers:
        raise RuntimeError("AGENT_BUS_TOKEN unset (env and ~/.gateway/mcp.yaml)")
    with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
        resp = client.get(
            f"/threads/{root_id}/resume-fence",
            params={"since_epoch": float(since_epoch)},
            headers=headers,
        )
    resp.raise_for_status()
    payload = resp.json()
    rows = list(payload.get("armed") or []) if isinstance(payload, dict) else []
    if exclude_transcript_ids:
        skip = {str(x) for x in exclude_transcript_ids}
        rows = [
            r
            for r in rows
            if isinstance(r, dict) and str(r.get("transcript_id") or "") not in skip
        ]
    return rows


def find_land_via_fence(
    root_id: str,
    *,
    since_epoch: float,
    exclude_ids: set[str] | frozenset[str] | None = None,
    armed_since: Callable[..., list[dict[str, Any]]] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Successor transcript id from resume-fence ``armed`` rows since *since_epoch*.

    Fence arms at ``beforeSubmitPrompt`` with ``transcript_id = conversation_id``
    — land proof that does not wait for JSONL birth (a:38474). Default probe is
    the agent-bus HTTP route (not a direct SQLite open).
    """
    tel: dict[str, Any] = {
        "proof": "fence",
        "root_id": root_id,
        "since_epoch": since_epoch,
        "matches": 0,
    }
    if not root_id:
        return None, tel
    probe = armed_since if armed_since is not None else armed_fences_via_http
    try:
        rows = probe(
            root_id,
            since_epoch=since_epoch,
            exclude_transcript_ids=exclude_ids,
        )
    except Exception as exc:  # noqa: BLE001 — land probe must not abort hop
        tel["error"] = f"{type(exc).__name__}:{str(exc)[:160]}"
        return None, tel
    tel["matches"] = len(rows)
    if not rows:
        return None, tel
    # Newest armed wins when several hops race.
    best = max(rows, key=lambda r: float(r.get("created_epoch") or 0.0))
    tid = str(best.get("transcript_id") or "")
    if not tid:
        return None, tel
    tel["fence_id"] = best.get("fence_id")
    tel["matched_transcript_id"] = tid
    return tid, tel


def focus_title_for(policy_override: str | None = None) -> str:
    """Title substring the hop focuses: the agents (Glass) window, unless policy names another.

    The IDE window is titled after the workspace (``… — <repo> [SSH: <host>] — Cursor``);
    the agents window is just ``Cursor Agents``. The house runs in the agents window
    (its transcripts land under the hub's agent-transcripts), so that is the default;
    ``policy.hop_focus_title`` overrides when the operator wants a different toplevel.
    """
    return policy_override or AGENTS_WINDOW_TITLE


def hop_header_line(message: str) -> str:
    """The hop's identifying line (``Liaison IDE hop … tip_cp=N``) used as the landing marker."""
    for line in message.splitlines():
        if line.startswith("Liaison IDE hop"):
            return line
    return message.strip().splitlines()[0] if message.strip() else ""


def hop_land_identity(
    message: str, *, root_id: str | None = None
) -> tuple[str | None, int | None]:
    """``(root_id, tip_cp)`` from the hop paste — tip from the Liaison line only."""
    tip: int | None = None
    header = hop_header_line(message)
    tip_m = re.search(r"tip_cp=(\d+)(?!\d)", header)
    if tip_m:
        tip = int(tip_m.group(1))
    root = (root_id or "").strip() or None
    if root is None:
        for line in (message or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("resume "):
                parts = stripped.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    root = parts[1]
                break
    return root, tip


def extract_hop_body_text(first_line: str) -> str | None:
    """User text from a transcript JSONL first line, with ``<user_query>`` stripped.

    Real hub rows wrap the paste in ``<timestamp>…</timestamp>\\n<user_query>…``;
    land needles live inside that wrapper (15456#4 F3).
    """
    text = (first_line or "").strip()
    if not text:
        return None
    try:
        row = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(row, dict):
        return text
    content = (row.get("message") or {}).get("content")
    if isinstance(content, list):
        parts = [
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict)
        ]
        joined = "\n".join(parts)
    else:
        joined = str(row.get("text") or row.get("content") or "")
    if not joined:
        return None
    m = _USER_QUERY_RE.search(joined)
    if m:
        return m.group(1).strip()
    # Partial wrapper (stream still open): take everything after <user_query>.
    lower = joined.lower()
    idx = lower.find("<user_query>")
    if idx >= 0:
        return joined[idx + len("<user_query>") :].strip()
    return joined.strip()


def first_line_matches_land(
    first_line: str,
    *,
    root_id: str | None,
    tip_cp: int | None,
    marker: str,
) -> bool:
    """True when the decoded hop body is an exact land for this hop.

    With tip: first body line is ``resume <root>`` (alone); ``tip_cp=N`` only on a
    line that starts with ``Liaison IDE hop``. Without tip: when ``root_id`` is set,
    first body line must still be ``resume <root>`` (a:38386 / a:38439 — marker-only
    matched foreign roots that share the Liaison template); then full marker
    substring (mtime gated by the caller).
    """
    body = extract_hop_body_text(first_line)
    if body is None:
        return False
    if tip_cp is not None:
        if not root_id:
            return False
        lines = body.splitlines()
        if not lines:
            return False
        first = lines[0].strip()
        if first != f"resume {root_id}":
            return False
        for line in lines:
            if not line.startswith("Liaison IDE hop"):
                continue
            tip_m = re.search(r"tip_cp=(\d+)(?!\d)", line)
            if tip_m and int(tip_m.group(1)) == tip_cp:
                return True
            return False
        return False
    if root_id:
        lines = body.splitlines()
        if not lines or lines[0].strip() != f"resume {root_id}":
            return False
    text = (marker or "").strip()
    return bool(text) and text in body


def land_find_telemetry(
    *,
    root_id: str | None,
    tip_cp: int | None,
    marker: str,
    matches: int,
    matched_first_line_head: str | None = None,
) -> dict[str, Any]:
    """Needles + match count for ok and not_landed (15456 ask 3)."""
    needles: list[str] = []
    if tip_cp is not None and root_id:
        needles.append(f"resume {root_id}")
        needles.append(f"tip_cp={tip_cp}")
        needles.append("Liaison IDE hop")
    elif root_id:
        needles.append(f"resume {root_id}")
        if (marker or "").strip():
            needles.append(marker.strip())
    elif (marker or "").strip():
        needles.append(marker.strip())
    out: dict[str, Any] = {"needles": needles, "matches": matches}
    if matched_first_line_head is not None:
        out["matched_first_line_head"] = matched_first_line_head[:160]
    return out


def find_transcript_with_hop_header(
    marker: str,
    transcripts_dir: Path,
    *,
    root_id: str | None = None,
    tip_cp: int | None = None,
    since_epoch: float | None = None,
    exclude_ids: set[str] | frozenset[str] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Transcript id whose first JSONL line is an exact land for this hop.

    Tip hops: exact ``(root, tip)`` — no mtime gate (re-hop / JSONL lag / clock skew).
    Tipless hops: full ``marker`` substring **and** ``mtime >= since_epoch - 1`` when
    ``since_epoch`` is set. ``exclude_ids`` drops the departing tab (15456#4 F2).
    """
    empty_tel = land_find_telemetry(
        root_id=root_id, tip_cp=tip_cp, marker=marker, matches=0
    )
    if not transcripts_dir.is_dir():
        return None, empty_tel
    skip = exclude_ids or set()
    rows: list[tuple[float, str, str]] = []
    for path in transcripts_dir.glob("*/*.jsonl"):
        tid = path.parent.name
        if tid in skip:
            continue
        try:
            with path.open(encoding="utf-8") as fh:
                first_line = fh.readline()
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if tip_cp is None and since_epoch is not None and mtime < since_epoch - 1.0:
            continue
        if not first_line_matches_land(
            first_line, root_id=root_id, tip_cp=tip_cp, marker=marker
        ):
            continue
        rows.append((mtime, tid, first_line[:160]))
    tel = land_find_telemetry(
        root_id=root_id,
        tip_cp=tip_cp,
        marker=marker,
        matches=len(rows),
        matched_first_line_head=rows[0][2] if rows else None,
    )
    if not rows:
        return None, tel
    rows.sort(key=lambda row: row[0], reverse=True)
    return rows[0][1], tel


def list_resume_transcript_ids(root_id: str, transcripts_dir: Path) -> set[str]:
    """Ids whose decoded body starts with ``resume <root_id>`` — tipless pre-fire baseline."""
    found: set[str] = set()
    if not root_id or not transcripts_dir.is_dir():
        return found
    for path in transcripts_dir.glob("*/*.jsonl"):
        try:
            with path.open(encoding="utf-8") as fh:
                first_line = fh.readline()
        except OSError:
            continue
        body = extract_hop_body_text(first_line)
        if body is None:
            continue
        first = body.splitlines()[0].strip() if body.splitlines() else ""
        if first == f"resume {root_id}":
            found.add(path.parent.name)
    return found


def wait_for_landed_transcript(
    marker: str,
    *,
    since_epoch: float,
    transcripts_dir: Path,
    timeout_s: float = DEFAULT_LANDING_TIMEOUT_S,
    poll_s: float = 2.0,
    root_id: str | None = None,
    tip_cp: int | None = None,
    pre_existing_ids: set[str] | None = None,
    exclude_ids: set[str] | frozenset[str] | None = None,
    armed_since: Callable[..., list[dict[str, Any]]] | None = None,
    fence_first: bool = True,
) -> tuple[str | None, dict[str, Any]]:
    """Land id via fence journal (preferred) or exact transcript match.

    Fence-first: an ``armed`` row for ``root_id`` with ``created_at >= since_epoch``
    and ``transcript_id`` not departing proves submit without waiting for JSONL
    birth (a:38474; median JSONL lag ~118s, specimen +842s). Transcript scan is
    the long-window fallback inside the same deadline (default ≥600s).
    """
    deadline = time.monotonic() + timeout_s
    skip = set(exclude_ids or ())
    last_tel = land_find_telemetry(
        root_id=root_id, tip_cp=tip_cp, marker=marker, matches=0
    )
    while True:
        if fence_first and root_id:
            fence_id, fence_tel = find_land_via_fence(
                root_id,
                since_epoch=since_epoch,
                exclude_ids=skip,
                armed_since=armed_since,
            )
            if fence_id is not None:
                fence_tel["landed_via"] = "fence"
                return fence_id, fence_tel
            last_tel = {**last_tel, "fence": fence_tel}
        if transcripts_dir.is_dir() and marker:
            found, last_tel = find_transcript_with_hop_header(
                marker,
                transcripts_dir,
                root_id=root_id,
                tip_cp=tip_cp,
                since_epoch=None if tip_cp is not None else since_epoch,
                exclude_ids=skip,
            )
            if found is not None:
                last_tel = {**last_tel, "landed_via": "transcript", "proof": "transcript"}
                return found, last_tel
            if tip_cp is None and pre_existing_ids is not None:
                for path in transcripts_dir.glob("*/*.jsonl"):
                    tid = path.parent.name
                    if tid in pre_existing_ids or tid in skip:
                        continue
                    try:
                        with path.open(encoding="utf-8") as fh:
                            first_line = fh.readline()
                    except OSError:
                        continue
                    if first_line_matches_land(
                        first_line, root_id=root_id, tip_cp=None, marker=marker
                    ):
                        tel = land_find_telemetry(
                            root_id=root_id,
                            tip_cp=None,
                            marker=marker,
                            matches=1,
                            matched_first_line_head=first_line[:160],
                        )
                        tel["landed_via"] = "transcript"
                        tel["proof"] = "transcript"
                        return tid, tel
        if time.monotonic() >= deadline:
            return None, last_tel
        time.sleep(poll_s)


def induction_head_line(message: str) -> str:
    """First line of the induction block — landing marker in the holder transcript."""
    return message.strip().splitlines()[0] if message.strip() else ""


def transcript_byte_size(transcript_id: str, transcripts_dir: Path) -> int:
    """Current byte length of the holder transcript JSONL, or 0 when absent."""
    path = transcripts_dir / transcript_id / f"{transcript_id}.jsonl"
    try:
        return path.stat().st_size
    except OSError:
        return 0


def wait_for_induction_landed(
    transcript_id: str,
    marker: str,
    *,
    since_bytes: int,
    transcripts_dir: Path,
    timeout_s: float = 30.0,
    poll_s: float = 2.0,
) -> bool:
    """True when the holder transcript gains a user row carrying ``marker`` after ``since_bytes``."""
    if not marker:
        return False
    path = transcripts_dir / transcript_id / f"{transcript_id}.jsonl"
    deadline = time.monotonic() + timeout_s
    while True:
        if path.is_file():
            try:
                with path.open(encoding="utf-8") as fh:
                    fh.seek(since_bytes)
                    for line in fh:
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if row.get("role") != "user":
                            continue
                        content = (row.get("message") or {}).get("content") or []
                        text = " ".join(
                            str(block.get("text") or "") for block in content
                        )
                        if marker in text:
                            return True
            except OSError:
                pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_s)


__all__ = [
    "AGENTS_WINDOW_APP_ID",
    "AGENTS_WINDOW_TITLE",
    "DEFAULT_LANDING_TIMEOUT_S",
    "extract_hop_body_text",
    "find_land_via_fence",
    "find_transcript_with_hop_header",
    "first_line_matches_land",
    "focus_title_for",
    "hop_header_line",
    "hop_land_identity",
    "induction_head_line",
    "land_find_telemetry",
    "list_resume_transcript_ids",
    "transcript_byte_size",
    "wait_for_induction_landed",
    "wait_for_landed_transcript",
]
