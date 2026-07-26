"""Select operator-facing CLOSEOUT payload for cursor-auto relay.

Proxy §2 wants ``ac_verdict`` / ``deltas_to_spec`` etc. GIW's cursor-sdk bus
turn is a machine capture/manifest that shares the name "closeout". Prefer an
authored §2 body (usually the repo sidecar) when present; fall back to the
wrapper only when it is not.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from services.git_integration_worker.config import load_config

_SIDECAR_REL_DIR = "tmp/reviews/closeouts"

_SECTION2_MARKERS = ("ac_verdict", "deltas_to_spec")
_TAIL_MARKERS = (
    "\n## effects_manifest",
    "\n## structured_closeout_full",
)
_STATUS_RE = re.compile(
    r"(?im)^(?:\*\*)?status(?:\*\*)?\s*[:=]\s*`?(complete|partial|blocked)`?"
)


@dataclass(frozen=True, slots=True)
class CloseoutRelayPayload:
    """Body + status line for ``TYPE: CLOSEOUT`` relay to the operator seat."""

    body: str
    status: str
    source: str  # section2_sidecar | section2_bus | wrapper | empty


def is_wrapper_manifest(text: str) -> bool:
    """True when *text* is the machine SDK capture JSON (not §2 prose)."""
    raw = text.strip()
    if not raw.startswith("{"):
        return False
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    return "schema_version" in data and (
        "effects_manifest" in data or "files_created" in data or "capture_status" in data
    )


def looks_section2(text: str) -> bool:
    """True when *text* carries the load-bearing §2 field markers."""
    low = text.lower()
    return all(marker in low for marker in _SECTION2_MARKERS)


def strip_machine_tail(text: str) -> str:
    """Drop appended GIW machine sections from a repo sidecar body."""
    cut = len(text)
    for marker in _TAIL_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            cut = min(cut, idx)
    return text[:cut].rstrip()


def status_from_section2(text: str) -> str | None:
    """Extract ``complete|partial|blocked`` from authored §2 prose, if present."""
    match = _STATUS_RE.search(text)
    if match is None:
        return None
    return match.group(1).lower()


def ledger_status_to_closeout(terminal_status: str) -> str:
    """Map ledger terminal status → operator CLOSEOUT status line."""
    if terminal_status == "completed":
        return "complete"
    if terminal_status == "failed":
        return "blocked"
    return "partial"


def read_repo_closeout_sidecar(
    dispatch_id: str,
    *,
    source_repo: Path | None = None,
) -> str | None:
    """Read ``tmp/reviews/closeouts/{dispatch_id}.md`` when present."""
    root = source_repo if source_repo is not None else load_config().source_repo
    path = root / _SIDECAR_REL_DIR / f"{dispatch_id}.md"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text or None


def select_closeout_relay_payload(
    *,
    sdk_body: str | None,
    sidecar_text: str | None,
    ledger_status: str,
) -> CloseoutRelayPayload:
    """Prefer authored §2 body; fall back to wrapper manifest.

    Selection order:
    1. Repo sidecar with §2 markers (machine tail stripped)
    2. Bus body when it itself looks like §2 (rare)
    3. Bus wrapper JSON
    4. Empty placeholder
    """
    fallback_status = ledger_status_to_closeout(ledger_status)

    if sidecar_text:
        prose = strip_machine_tail(sidecar_text)
        if looks_section2(prose):
            return CloseoutRelayPayload(
                body=prose,
                status=status_from_section2(prose) or fallback_status,
                source="section2_sidecar",
            )

    if sdk_body and looks_section2(sdk_body) and not is_wrapper_manifest(sdk_body):
        prose = strip_machine_tail(sdk_body)
        return CloseoutRelayPayload(
            body=prose,
            status=status_from_section2(prose) or fallback_status,
            source="section2_bus",
        )

    if sdk_body and sdk_body.strip():
        return CloseoutRelayPayload(
            body=sdk_body.strip(),
            status=fallback_status,
            source="wrapper",
        )

    return CloseoutRelayPayload(
        body="(no cursor-sdk closeout body captured)",
        status=fallback_status,
        source="empty",
    )
