"""Git-reconciliation detector: landed-claim-vs-master-ref ground-truth audit.

``detect_landed_claim_not_on_master`` reconciles live land-asserting assertions
against LOCAL ``refs/heads/master`` reachability (never origin) — catching the
telemetry-vs-git-ground-truth divergence class
(``case:telemetry-vs-git-ground-truth-divergence``, assertion 11575): a claim
narrates "landed" while the commit never reached master.

Placement rationale (spec §2–§3): the divergence was caught both times at
session-open reconciliation; this is the same object as
``entity_source_uri_unresolved`` — a claimed pointer that does not resolve to
ground truth. It runs in the ``FS_TOUCHING`` pass (it touches the repo via the
git-integration-worker), NOT the <100ms graph-only ``session_audit`` default.

SHA-source preference (spec §7 — structured-provenance hardening): a typed
``master_sha`` / ``merge_commit`` attribute on the asserting entity is the
robust signal; regex-over-claim-prose is the fallback for free-text claims.

Reachability is resolved via the worker route ``GET /api/v1/git/reachable``
(through Stargate) — ``cortex_store`` never imports ``git_integrate`` (domain
isolation; the cortex-api process is not guaranteed the repo ``.git`` mount).
"""

from __future__ import annotations

import json
import re
from typing import Any

from transport_utils import DEFAULT_STARGATE_URL, make_sync_client
from universal_logging import get_logger

from ...db import query
from ._shared import _finding

logger = get_logger(__name__)

_KIND = "landed_claim_not_on_master"
_REACHABLE_TIMEOUT = 15.0

# Land-asserting context: a SHA mentioned alongside one of these tokens is
# claimed as landed/merged. A bare SHA ("introduced in <sha>", "reverted
# <sha>") must NOT match — the context token, not SHA presence, is the trigger
# (spec §6 guard 2).
_LAND_CONTEXT_TOKENS = (
    "landed",
    "merge_commit",
    "master_sha",
    "merged to master",
    "cas-advanced",
    "git.land.completed",
)
# Typed-attribute keys carrying a land SHA (structured-provenance path, §7).
_SHA_ATTR_KEYS = ("master_sha", "merge_commit")
# A commit SHA token: 7–40 hex chars on a word boundary.
_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)


def _reachable_via_worker(sha: str) -> dict[str, Any] | None:
    """Query ``GET /api/v1/git/reachable?sha=`` through Stargate.

    Returns the worker payload (``{"sha","exists","reachable",...}``) or None
    when the worker / Stargate is unreachable or the response is malformed —
    an advisory detector never blocks on transient infra (spec §6 guard 4),
    so a failed probe degrades to silent (no finding).
    """
    try:
        with make_sync_client(
            DEFAULT_STARGATE_URL, timeout=_REACHABLE_TIMEOUT
        ) as client:
            resp = client.get("/api/v1/git/reachable", params={"sha": sha})
            resp.raise_for_status()
            payload = resp.json()
        return payload if isinstance(payload, dict) else None
    except Exception as exc:  # noqa: BLE001 — advisory: degrade silent on any transport/parse error
        logger.warning("reachable probe failed for sha=%s: %s", sha, exc)
        return None


def _extract_shas(claim: str, attributes: dict[str, Any] | None) -> list[str]:
    """SHAs to reconcile: typed attributes first (robust), claim regex fallback."""
    if attributes:
        typed = [
            str(attributes[k]).strip()
            for k in _SHA_ATTR_KEYS
            if attributes.get(k) and _SHA_RE.fullmatch(str(attributes[k]).strip())
        ]
        if typed:
            return list(dict.fromkeys(typed))
    return list(dict.fromkeys(_SHA_RE.findall(claim or "")))


def _matched_context_token(claim_lower: str) -> str | None:
    """First land-asserting context token present in the (lowercased) claim."""
    for tok in _LAND_CONTEXT_TOKENS:
        if tok in claim_lower:
            return tok
    return None


def _parse_attrs(attrs_raw: Any) -> dict[str, Any] | None:
    """Decode an entity ``attributes`` cell to a dict (None when absent/invalid)."""
    if attrs_raw is None:
        return None
    try:
        attrs = json.loads(attrs_raw) if isinstance(attrs_raw, str) else attrs_raw
    except (TypeError, ValueError):
        return None
    return attrs if isinstance(attrs, dict) else None


def detect_landed_claim_not_on_master(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Live land-asserting assertions whose claimed SHA is not on local master.

    Implements the §5 algorithm + §6 false-positive guards. Fires:
      - ``phantom``: SHA does not exist in the repo (rev-parse fail) — strongest.
      - ``not_reachable``: SHA exists but is not an ancestor of refs/heads/master.

    Silent on: reachable SHA; superseded assertion; non-committed review_status;
    non-land SHA mention (no context token); origin-lag (the worker reconciles
    LOCAL master only); and worker-unreachable (advisory, never blocking).
    """
    sql = """
        SELECT a.id AS assertion_id, a.entity_id, a.claim, e.attributes
        FROM assertions a
        JOIN entities e ON e.id = a.entity_id
        WHERE a.superseded_by IS NULL
          AND a.review_status = 'committed'
    """
    params: tuple = ()
    if subject:
        sql += " AND a.entity_id = ?"
        params = (subject,)
    rows = query(conn, sql, params)

    findings: list[dict[str, Any]] = []
    for r in rows:
        claim = r.get("claim") or ""
        token = _matched_context_token(claim.lower())
        if not token:
            continue
        shas = _extract_shas(claim, _parse_attrs(r.get("attributes")))
        for sha in shas:
            payload = _reachable_via_worker(sha)
            if payload is None:
                continue
            if not payload.get("exists", False):
                outcome = "phantom"
            elif not payload.get("reachable", False):
                outcome = "not_reachable"
            else:
                continue
            findings.append(
                _finding(
                    _KIND,
                    r["entity_id"],
                    f"assertion {r['assertion_id']} on {r['entity_id']} claims a land "
                    f"(context token {token!r}) citing {sha} but it is {outcome} against "
                    f"refs/heads/master (local). The land may have stranded in a dispatch "
                    f"worktree — reconcile against the ref "
                    f"(case:telemetry-vs-git-ground-truth-divergence).",
                    audit_id=f"{_KIND}:{r['assertion_id']}:{sha[:12]}",
                )
            )
    return findings


__all__ = ["detect_landed_claim_not_on_master"]
