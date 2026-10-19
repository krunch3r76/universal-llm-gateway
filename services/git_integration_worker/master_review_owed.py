"""Ledger of reviews owed on hub master after a land.

One canonical ``VERDICT:`` line binds a row. The check lists master commits
with no covering row and posts a gap notice. It does not refuse a land, does
not revert, and does not call ``team_dispatch``. Ride-along notices use the
same poster and ``gap_posted_at`` dedupe and also do not block.

Authority for the rows is the dispatch-ledger table ``master_review_owed``.
The JSON file is the idempotent seed. Recovery is the next check: seed rows
are inserted only when that ``tip_sha`` has no stored verdict.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anthropic_credit.ledger import LedgerUnavailable, validate_ledger_path

from services.git_integration_worker.cursor_dispatch_ledger import (
    resolve_cursor_sdk_dispatch_ledger_path,
)
from services.git_integration_worker.cursor_sdk_closeout_harvest import _fenced_spans

logger = logging.getLogger(__name__)

CANONICAL_VERDICT_RE = re.compile(
    r"(?im)^VERDICT:[ \t]+(APPROVE|REQUEST_CHANGES)[ \t]+"
    r"range=([0-9a-f]{40})\.\.([0-9a-f]{40})[ \t]+head=\3"
    r"(?:[ \t]+fixes=([0-9a-f]{40}))?[ \t]*$"
)
SCAN_CAP = 64
MAX_RANGES_PER_RUN = 3
_GIT_TIMEOUT_S = 10.0
_LEDGER_CONNECT_TIMEOUT_S = 1.0
_LEDGER_CONNECT_BUDGET_S = 2.0
INITIAL_REVIEW_WINDOW_BASE = "cff8a035e09c1dfe6f1cd1b676ca575eb9a430c1"
GAP_THREAD = "15851"
GAP_TO = "grok-bot-gc1"
GAP_FROM = "git-integration-worker"
DATA_FILE = Path(__file__).with_name("master_review_owed_rows.json")
MASTER_REF = "refs/heads/master"

TurnList = Sequence[dict[str, Any]]
TurnSource = TurnList | Callable[[], TurnList]
Poster = Callable[[str, str, str], None]


@dataclass(frozen=True)
class BindResult:
    """Outcome of one canonical-line write. ``status`` is the only signal."""

    status: str
    tip_sha: str | None = None
    base_sha: str | None = None
    fixes_sha: str | None = None
    verdict: str | None = None


@dataclass(frozen=True)
class ReviewCheckResult:
    """``commits`` is a list only when ``ok`` is true; otherwise it is null."""

    ok: bool
    reason: str | None
    commits: list[str] | None
    fix_owed: tuple[str, ...] = ()
    deferred_ranges: tuple[dict[str, Any], ...] = ()
    posted_tips: tuple[str, ...] = ()


@dataclass(frozen=True)
class RideAlongResult:
    """Ride-along notice. ``ok`` false never means the caller should fail."""

    ok: bool
    reason: str | None
    commits: list[str] | None
    posted: int = 0


def _now() -> str:
    return datetime.now(UTC).isoformat()


class _GitTimeoutError(RuntimeError):
    """A git subprocess exceeded ``_GIT_TIMEOUT_S``."""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise _GitTimeoutError(args[0] if args else "git") from exc


def head_ref_sha(repo: str | Path, ref: str = MASTER_REF) -> str | None:
    """Resolve ``ref`` to a full sha, or None when git cannot name it.

    A timeout is None. The land call is not gated on this sha; the post-land
    check fails closed instead of inventing a commit range.
    """
    try:
        proc = _git(Path(repo), "rev-parse", "--verify", ref)
    except _GitTimeoutError:
        return None
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip().lower()
    return sha or None


def _is_commit(repo: Path, sha: str) -> bool:
    proc = _git(repo, "cat-file", "-t", sha)
    return proc.returncode == 0 and proc.stdout.strip() == "commit"


def _parents(repo: Path, sha: str) -> list[str]:
    # ``^@`` is every parent. ``--verify`` rejects that because it is not one object.
    proc = _git(repo, "rev-parse", f"{sha}^@")
    if proc.returncode != 0:
        return []
    return [part.lower() for part in proc.stdout.split() if part.strip()]


def _rev_list(repo: Path, base: str, tip: str) -> list[str] | None:
    proc = _git(repo, "rev-list", "--reverse", f"{base}..{tip}")
    if proc.returncode != 0:
        return None
    return [line.strip().lower() for line in proc.stdout.splitlines() if line.strip()]


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return (
        _git(repo, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0
    )


def _commit_tree(repo: Path, sha: str) -> str | None:
    proc = _git(repo, "rev-parse", "--verify", f"{sha}^{{tree}}")
    if proc.returncode != 0:
        return None
    return proc.stdout.strip().lower()


def _merge_tree(repo: Path, parent_a: str, parent_b: str) -> str | None:
    proc = _git(repo, "merge-tree", "--write-tree", parent_a, parent_b)
    if proc.returncode != 0:
        return None
    lines = [line.strip().lower() for line in proc.stdout.splitlines() if line.strip()]
    return lines[0] if lines else None


def _unfenced_matches(body: str) -> list[re.Match[str]]:
    spans = _fenced_spans(body)
    found: list[re.Match[str]] = []
    for match in CANONICAL_VERDICT_RE.finditer(body):
        if any(start <= match.start() < end for start, end in spans):
            continue
        found.append(match)
    return found


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS master_review_owed (
            tip_sha TEXT PRIMARY KEY,
            base_sha TEXT NOT NULL,
            verdict TEXT,
            review_dispatch_id TEXT,
            bus_thread TEXT,
            bus_turn INTEGER,
            fixes_sha TEXT,
            gap_posted_at TEXT,
            notice_tag TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS master_review_owed_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            review_window_base TEXT NOT NULL,
            verdict_scan_before_id INTEGER NOT NULL,
            verdict_scan_after_turn INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        "INSERT INTO master_review_owed_state (id, review_window_base, verdict_scan_before_id) "
        "VALUES (1, ?, 0) ON CONFLICT(id) DO NOTHING",
        (INITIAL_REVIEW_WINDOW_BASE,),
    )
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(master_review_owed_state)")
    }
    if "verdict_scan_after_turn" not in columns:
        conn.execute(
            "ALTER TABLE master_review_owed_state "
            "ADD COLUMN verdict_scan_after_turn INTEGER NOT NULL DEFAULT 0"
        )
    conn.commit()


def _connect(path: Path) -> sqlite3.Connection:
    """Open the review ledger with a short busy budget.

    The dispatch-ledger helper retries 40 times at a 30s sqlite timeout. This
    check runs on the land path, including after ``refs/heads/master`` has
    moved, so a stuck lock is a failed check rather than that retry.
    """
    deadline = time.monotonic() + _LEDGER_CONNECT_BUDGET_S
    last_err: sqlite3.OperationalError | None = None
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            conn = sqlite3.connect(
                path, timeout=min(_LEDGER_CONNECT_TIMEOUT_S, remaining)
            )
        except sqlite3.OperationalError as exc:
            last_err = exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            time.sleep(min(0.02, max(deadline - time.monotonic(), 0)))
            continue
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            return conn
        except sqlite3.OperationalError as exc:
            conn.close()
            last_err = exc
            message = str(exc).lower()
            if "locked" not in message and "busy" not in message:
                raise
            time.sleep(min(0.02, max(deadline - time.monotonic(), 0)))
    raise sqlite3.OperationalError(str(last_err or "ledger connect budget exceeded"))


def _open_ledger(
    mounts_table: list[tuple[str, str]] | None,
) -> tuple[sqlite3.Connection | None, str | None]:
    path = resolve_cursor_sdk_dispatch_ledger_path()
    try:
        validate_ledger_path(path, mounts_table=mounts_table)
    except LedgerUnavailable:
        return None, "ledger_not_local"
    try:
        conn = _connect(path)
    except sqlite3.OperationalError:
        logger.warning("master review ledger connect failed", exc_info=True)
        return None, "ledger_connect_failed"
    _ensure_schema(conn)
    return conn, None


def _state(conn: sqlite3.Connection) -> sqlite3.Row:
    row = conn.execute(
        "SELECT review_window_base, verdict_scan_before_id, verdict_scan_after_turn "
        "FROM master_review_owed_state WHERE id = 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("master_review_owed_state missing")
    return row


def _rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM master_review_owed").fetchall())


def _insert_seed(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    tip = str(row["tip_sha"]).lower()
    existing = conn.execute(
        "SELECT verdict FROM master_review_owed WHERE tip_sha = ?", (tip,)
    ).fetchone()
    if existing is not None and existing["verdict"]:
        return
    payload = (
        tip,
        str(row["base_sha"]).lower(),
        row.get("verdict"),
        row.get("review_dispatch_id"),
        row.get("bus_thread"),
        row.get("bus_turn"),
        (str(row["fixes_sha"]).lower() if row.get("fixes_sha") else None),
        row.get("gap_posted_at"),
        row.get("notice_tag"),
    )
    if existing is None:
        conn.execute(
            "INSERT INTO master_review_owed ("
            "tip_sha, base_sha, verdict, review_dispatch_id, bus_thread, bus_turn, "
            "fixes_sha, gap_posted_at, notice_tag) VALUES (?,?,?,?,?,?,?,?,?)",
            payload,
        )
        return
    conn.execute(
        "UPDATE master_review_owed SET base_sha=?, verdict=?, review_dispatch_id=?, "
        "bus_thread=?, bus_turn=?, fixes_sha=?, notice_tag=COALESCE(notice_tag, ?) "
        "WHERE tip_sha=? AND verdict IS NULL",
        (
            payload[1],
            payload[2],
            payload[3],
            payload[4],
            payload[5],
            payload[6],
            payload[8],
            tip,
        ),
    )


def _load_data_file(conn: sqlite3.Connection, path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, list):
        raise ValueError("master_review_owed rows must be a list")
    for row in parsed:
        _insert_seed(conn, row)
    conn.commit()


def _store_verdict(
    conn: sqlite3.Connection,
    *,
    tip: str,
    base: str,
    verdict: str,
    fixes: str | None,
    bus_thread: str | None,
    bus_turn: int | None,
    review_dispatch_id: str | None,
) -> BindResult:
    existing = conn.execute(
        "SELECT verdict, base_sha, fixes_sha FROM master_review_owed WHERE tip_sha = ?",
        (tip,),
    ).fetchone()
    if existing is not None and existing["verdict"]:
        return BindResult(
            status="tip_already_bound",
            tip_sha=tip,
            base_sha=existing["base_sha"],
            fixes_sha=existing["fixes_sha"],
            verdict=existing["verdict"],
        )
    if existing is None:
        conn.execute(
            "INSERT INTO master_review_owed ("
            "tip_sha, base_sha, verdict, review_dispatch_id, bus_thread, bus_turn, fixes_sha"
            ") VALUES (?,?,?,?,?,?,?)",
            (tip, base, verdict, review_dispatch_id, bus_thread, bus_turn, fixes),
        )
    else:
        conn.execute(
            "UPDATE master_review_owed SET base_sha=?, verdict=?, review_dispatch_id=?, "
            "bus_thread=?, bus_turn=?, fixes_sha=? WHERE tip_sha=? AND verdict IS NULL",
            (base, verdict, review_dispatch_id, bus_thread, bus_turn, fixes, tip),
        )
    conn.commit()
    return BindResult(
        status="bound", tip_sha=tip, base_sha=base, fixes_sha=fixes, verdict=verdict
    )


def bind_canonical_verdict(
    body: str,
    *,
    repo: str | Path,
    bus_thread: str | None,
    bus_turn: int | None,
    review_dispatch_id: str | None,
    mounts_table: list[tuple[str, str]] | None = None,
    conn: sqlite3.Connection | None = None,
) -> BindResult:
    """Write one row when ``body`` has exactly one unfenced canonical line.

    Zero matches and two or more matches write nothing. A full sha that is
    not a commit object writes nothing (``unresolved_sha``). A second write of
    a tip that already has a verdict returns ``tip_already_bound``.
    """
    matches = _unfenced_matches(body or "")
    if not matches:
        return BindResult(status="no_match")
    if len(matches) != 1:
        return BindResult(status="ambiguous")
    match = matches[0]
    verdict = match.group(1).upper()
    base = match.group(2).lower()
    tip = match.group(3).lower()
    fixes = match.group(4).lower() if match.group(4) else None
    root = Path(repo)
    if not _is_commit(root, base) or not _is_commit(root, tip):
        return BindResult(
            status="unresolved_sha", tip_sha=tip, base_sha=base, verdict=verdict
        )
    if fixes is not None and not _is_commit(root, fixes):
        return BindResult(
            status="unresolved_sha", tip_sha=tip, base_sha=base, verdict=verdict
        )
    own_conn = conn is None
    if own_conn:
        opened, reason = _open_ledger(mounts_table)
        if opened is None:
            return BindResult(status=reason or "ledger_not_local")
        conn = opened
    assert conn is not None
    return _store_verdict(
        conn,
        tip=tip,
        base=base,
        verdict=verdict,
        fixes=fixes,
        bus_thread=bus_thread,
        bus_turn=bus_turn,
        review_dispatch_id=review_dispatch_id,
    )


def _covered_shas(repo: Path, rows: Sequence[sqlite3.Row]) -> set[str]:
    covered: set[str] = set()
    for row in rows:
        if not row["verdict"]:
            continue
        covered.add(str(row["tip_sha"]).lower())
        spanned = _rev_list(repo, str(row["base_sha"]), str(row["tip_sha"]))
        if spanned:
            covered.update(spanned)
    return covered


def _clean_merge_covered(repo: Path, sha: str, rows: Sequence[sqlite3.Row]) -> bool:
    parents = _parents(repo, sha)
    if len(parents) != 2:
        return False
    tree = _commit_tree(repo, sha)
    merged = _merge_tree(repo, parents[0], parents[1])
    if not tree or not merged or tree != merged:
        return False
    second = parents[1]
    return any(str(row["tip_sha"]).lower() == second and row["verdict"] for row in rows)


def _fix_owed(rows: Sequence[sqlite3.Row]) -> list[str]:
    fixes = {
        str(row["fixes_sha"]).lower()
        for row in rows
        if row["verdict"] == "APPROVE" and row["fixes_sha"]
    }
    return [
        str(row["tip_sha"]).lower()
        for row in rows
        if row["verdict"] == "REQUEST_CHANGES"
        and str(row["tip_sha"]).lower() not in fixes
    ]


def _group_ranges(window: Sequence[str], gaps: set[str]) -> list[list[str]]:
    ranges: list[list[str]] = []
    current: list[str] = []
    for sha in window:
        if sha in gaps:
            current.append(sha)
            continue
        if current:
            ranges.append(current)
            current = []
    if current:
        ranges.append(current)
    return ranges


def _default_poster(subject: str, body: str, tag: str) -> None:
    """Post one notice. Under pytest this raises so a test ledger cannot hit the bus."""
    del tag
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError("gap poster disabled under pytest")
    import asyncio

    from services.git_integration_worker.cursor_bus import CursorBusClient

    async def _send() -> None:
        result = await CursorBusClient().reply(
            thread_id=GAP_THREAD,
            to_agent=GAP_TO,
            from_agent=GAP_FROM,
            subject=subject,
            body=body,
        )
        if result.status_code >= 400:
            raise RuntimeError(f"gap post status {result.status_code}")

    asyncio.run(_send())


def _post_one(
    poster: Poster,
    *,
    tag: str,
    base: str,
    tip: str,
    commits: Sequence[str],
) -> None:
    subject = f"NOTICE|{tag}|{tip}"
    body = (
        f"NOTICE: master commits with no covering review row.\n"
        f"tag: {tag}\nrange: {base}..{tip}\n"
        f"commits: {' '.join(commits)}\n"
        "An owed K43 review binds with the canonical VERDICT line. "
        "This notice does not block land or restart.\n"
    )
    poster(subject, body, tag)


def _claim_gap(
    conn: sqlite3.Connection,
    *,
    tip: str,
    base: str,
    tag: str | None,
) -> bool:
    """Claim ``tip`` before a notice post. False when a claim already exists."""
    conn.execute(
        """
        INSERT INTO master_review_owed (tip_sha, base_sha, gap_posted_at, notice_tag)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(tip_sha) DO UPDATE SET
            gap_posted_at = excluded.gap_posted_at,
            notice_tag = COALESCE(master_review_owed.notice_tag, excluded.notice_tag)
        WHERE master_review_owed.gap_posted_at IS NULL
        """,
        (tip, base, _now(), tag),
    )
    claimed = conn.execute("SELECT changes()").fetchone()[0] == 1
    conn.commit()
    return bool(claimed)


def _release_gap_claim(conn: sqlite3.Connection, tip: str) -> None:
    """Drop a claim when the notice post failed so a later run can retry."""
    conn.execute(
        "UPDATE master_review_owed SET gap_posted_at = NULL WHERE tip_sha = ?",
        (tip,),
    )
    conn.commit()


def _resolve_turns(
    source: TurnSource | None,
    *,
    after_turn: int,
) -> list[dict[str, Any]]:
    if source is None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return []
        return _bus_verdict_turns(after_turn)
    if callable(source):
        return list(source())
    selected: list[dict[str, Any]] = []
    for row in source:
        if row.get("turn_number") is None:
            selected.append(row)
            continue
        if int(row["turn_number"]) > after_turn:
            selected.append(row)
    return selected


def _bus_verdict_turns(after_turn: int) -> list[dict[str, Any]]:
    """Read one oldest-first page of thread 15851 after ``after_turn``.

    ``last`` without ``after_turn`` is newest-first and drops a canonical line
    that sits behind the page. Raises on transport failure.
    """
    import asyncio

    from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client

    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    async def _fetch() -> list[dict[str, Any]]:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=5.0) as client:
            resp = await client.get(
                "/turns",
                params={
                    "thread": GAP_THREAD,
                    "after_turn": after_turn,
                    "last": SCAN_CAP,
                },
                headers=headers,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"bus list status {resp.status_code}")
            payload = resp.json()
        turns = payload.get("turns") or []
        selected: list[dict[str, Any]] = []
        for turn in turns:
            number = int(turn.get("turn_number") or 0)
            if number and number <= after_turn:
                continue
            selected.append(turn)
        selected.sort(key=lambda turn: int(turn.get("turn_number") or 0))
        return selected[:SCAN_CAP]

    return asyncio.run(_fetch())


def _scan_turns(
    conn: sqlite3.Connection,
    repo: Path,
    turns: Sequence[dict[str, Any]],
) -> None:
    cursor = int(_state(conn)["verdict_scan_after_turn"] or 0)
    seen = 0
    advanced = cursor
    ordered = sorted(turns, key=lambda turn: int(turn.get("turn_number") or 0))
    for turn in ordered:
        number = int(turn["turn_number"]) if turn.get("turn_number") is not None else 0
        if number and number <= cursor:
            continue
        body = str(turn.get("body") or "")
        if "VERDICT:" in body.upper():
            if seen >= SCAN_CAP:
                break
            seen += 1
            bound = bind_canonical_verdict(
                body,
                repo=repo,
                bus_thread=str(turn.get("thread") or turn.get("bus_thread") or "")
                or None,
                bus_turn=number or None,
                review_dispatch_id=(
                    str(turn["review_dispatch_id"])
                    if turn.get("review_dispatch_id")
                    else None
                ),
                conn=conn,
            )
            if bound.status == "unresolved_sha":
                break
        if number:
            advanced = number
    if advanced != cursor:
        conn.execute(
            "UPDATE master_review_owed_state SET verdict_scan_after_turn=? WHERE id=1",
            (advanced,),
        )
        conn.commit()


def _fail(reason: str) -> ReviewCheckResult:
    return ReviewCheckResult(ok=False, reason=reason, commits=None)


def unreviewed_master_commits(
    repo: str | Path,
    base_sha: str,
    *,
    mounts_table: list[tuple[str, str]] | None = None,
    poster: Poster | None = None,
    verdict_turns: TurnSource | None = None,
    data_path: Path | None = None,
) -> ReviewCheckResult:
    """List uncovered master commits after ``base_sha`` and post at most three gap ranges.

    ``base_sha`` must equal the stored window floor. A mismatch returns
    ``base_rewritten`` and does not move the floor. Failure results set
    ``commits`` to null and do not post.
    """
    root = Path(repo)
    conn, reason = _open_ledger(mounts_table)
    if conn is None:
        return _fail(reason or "ledger_not_local")
    stored = str(_state(conn)["review_window_base"]).lower()
    argued = (base_sha or "").strip().lower()
    if argued != stored:
        return _fail("base_rewritten")
    path = data_path if data_path is not None else DATA_FILE
    try:
        _load_data_file(conn, path)
    except (OSError, json.JSONDecodeError, ValueError, KeyError):
        return _fail("data_file_unreadable")
    try:
        turns = _resolve_turns(
            verdict_turns,
            after_turn=int(_state(conn)["verdict_scan_after_turn"] or 0),
        )
        _scan_turns(conn, root, turns)
    except _GitTimeoutError:
        return _fail("git_timeout")
    except Exception:
        logger.warning("master review bus scan failed", exc_info=True)
        return _fail("bus_read_failed")
    try:
        if not _is_ancestor(root, stored, MASTER_REF):
            return _fail("rev_list_failed")
        window = _rev_list(root, stored, MASTER_REF)
        if window is None:
            return _fail("rev_list_failed")
        rows = _rows(conn)
        covered = _covered_shas(root, rows)
        for sha in window:
            if sha not in covered and _clean_merge_covered(root, sha, rows):
                covered.add(sha)
        owed = _fix_owed(rows)
        gaps = [sha for sha in window if sha not in covered and sha not in owed]
        gap_set = set(gaps)
        ranges = _group_ranges(window, gap_set)
        send = poster or _default_poster
        posted: list[str] = []
        deferred: list[dict[str, Any]] = []
        for index, group in enumerate(ranges):
            tip = group[-1]
            base = group[0]
            if index >= MAX_RANGES_PER_RUN:
                deferred.append(
                    {
                        "tip_sha": tip,
                        "base_sha": base,
                        "commits": list(group),
                        "gap_posted_at": None,
                    }
                )
                continue
            if not _claim_gap(conn, tip=tip, base=base, tag=None):
                continue
            siblings: list[str] = []
            for sha in group:
                if sha == tip:
                    continue
                if _claim_gap(conn, tip=sha, base=base, tag=None):
                    siblings.append(sha)
            try:
                _post_one(send, tag="gap", base=base, tip=tip, commits=group)
            except Exception:
                logger.warning(
                    "master review gap post failed tip=%s", tip, exc_info=True
                )
                _release_gap_claim(conn, tip)
                for sha in siblings:
                    _release_gap_claim(conn, sha)
                continue
            posted.append(tip)
        _advance_floor(conn, root, window, gap_set)
        return ReviewCheckResult(
            ok=True,
            reason=None,
            commits=gaps,
            fix_owed=tuple(owed),
            deferred_ranges=tuple(deferred),
            posted_tips=tuple(posted),
        )
    except _GitTimeoutError:
        return _fail("git_timeout")


def _advance_floor(
    conn: sqlite3.Connection,
    repo: Path,
    window: Sequence[str],
    gaps: set[str],
) -> None:
    del repo
    new_base = str(_state(conn)["review_window_base"]).lower()
    for sha in window:
        if sha in gaps:
            break
        new_base = sha
    conn.execute(
        "UPDATE master_review_owed_state SET review_window_base=? WHERE id=1",
        (new_base,),
    )
    conn.commit()


def _own_range(repo: Path, triggering: str) -> set[str]:
    parents = _parents(repo, triggering)
    if not parents:
        return {triggering.lower()}
    introduced = _rev_list(repo, parents[0], triggering) or []
    return {triggering.lower(), *introduced}


def _ride_along_unreviewed(
    repo: Path,
    live_old_sha: str,
    live_new_sha: str,
    triggering_land_sha: str,
    *,
    mounts_table: list[tuple[str, str]] | None,
    poster: Poster | None,
    data_path: Path | None,
) -> RideAlongResult:
    old = live_old_sha.strip().lower()
    new = live_new_sha.strip().lower()
    triggering = triggering_land_sha.strip().lower()
    if not (
        _is_commit(repo, old) and _is_commit(repo, new) and _is_commit(repo, triggering)
    ):
        return RideAlongResult(ok=False, reason="unresolved_sha", commits=None)
    window = _rev_list(repo, old, new)
    if window is None:
        return RideAlongResult(ok=False, reason="rev_list_failed", commits=None)
    own = _own_range(repo, triggering)
    candidates = [sha for sha in window if sha not in own]
    conn, reason = _open_ledger(mounts_table)
    if conn is None:
        return RideAlongResult(
            ok=False, reason=reason or "ledger_not_local", commits=None
        )
    path = data_path if data_path is not None else DATA_FILE
    try:
        _load_data_file(conn, path)
    except (OSError, json.JSONDecodeError, ValueError, KeyError):
        return RideAlongResult(ok=False, reason="data_file_unreadable", commits=None)
    rows = _rows(conn)
    covered = _covered_shas(repo, rows)
    for sha in candidates:
        if sha not in covered and _clean_merge_covered(repo, sha, rows):
            covered.add(sha)
    owed_fix = set(_fix_owed(rows))
    uncovered = [
        sha for sha in candidates if sha not in covered and sha not in owed_fix
    ]
    send = poster or _default_poster
    posted = 0
    for sha in uncovered:
        if not _claim_gap(conn, tip=sha, base=old, tag="ride_along"):
            continue
        try:
            _post_one(send, tag="ride_along", base=old, tip=sha, commits=[sha])
        except Exception:
            _release_gap_claim(conn, sha)
            raise
        posted += 1
    return RideAlongResult(ok=True, reason=None, commits=uncovered, posted=posted)


def ride_along_unreviewed(
    repo: str | Path,
    live_old_sha: str,
    live_new_sha: str,
    triggering_land_sha: str,
    *,
    mounts_table: list[tuple[str, str]] | None = None,
    poster: Poster | None = None,
    data_path: Path | None = None,
) -> RideAlongResult:
    """Notice commits in ``(live_old, live_new]`` outside the triggering land's range.

    The triggering land's own range is the commits that tip introduces versus
    its first parent (a non-merge tip is that one commit). Each other commit
    with no covering row gets one ``ride_along`` notice and an owed row.
    A second call does not post again once ``gap_posted_at`` is set. This
    never raises and never blocks the caller.
    """
    try:
        return _ride_along_unreviewed(
            Path(repo),
            live_old_sha,
            live_new_sha,
            triggering_land_sha,
            mounts_table=mounts_table,
            poster=poster,
            data_path=data_path,
        )
    except _GitTimeoutError:
        return RideAlongResult(ok=False, reason="git_timeout", commits=None, posted=0)
    except Exception as exc:
        logger.warning("ride_along_unreviewed failed", exc_info=True)
        return RideAlongResult(
            ok=False, reason=type(exc).__name__, commits=None, posted=0
        )


def stored_review_window_base(
    *,
    mounts_table: list[tuple[str, str]] | None = None,
) -> str | None:
    """Return the stored floor, or None when the ledger cannot be opened."""
    conn, _reason = _open_ledger(mounts_table)
    if conn is None:
        return None
    return str(_state(conn)["review_window_base"]).lower()


def apply_review_obligations_after_land(
    *,
    repo: str | Path,
    live_old_sha: str | None,
    live_new_sha: str | None,
    triggering_land_sha: str | None,
    mounts_table: list[tuple[str, str]] | None = None,
    poster: Poster | None = None,
    verdict_turns: TurnSource | None = None,
    data_path: Path | None = None,
) -> None:
    """Run the master-review check and the ride-along notice after a land.

    Both calls are swallowed. A notice failure does not change the land result.
    ``live_*`` shas are the master ref around the land already in settlement;
    a restart that does not pass through this function is not wired here.
    """
    try:
        base = (
            stored_review_window_base(mounts_table=mounts_table)
            or INITIAL_REVIEW_WINDOW_BASE
        )
        unreviewed_master_commits(
            repo,
            base,
            mounts_table=mounts_table,
            poster=poster,
            verdict_turns=verdict_turns,
            data_path=data_path,
        )
        if live_old_sha and live_new_sha and triggering_land_sha:
            ride_along_unreviewed(
                repo,
                live_old_sha,
                live_new_sha,
                triggering_land_sha,
                mounts_table=mounts_table,
                poster=poster,
                data_path=data_path,
            )
        else:
            logger.warning(
                "master review ride-along failed check reason=missing_master_sha"
            )
    except Exception:
        logger.warning("apply_review_obligations_after_land failed", exc_info=True)
