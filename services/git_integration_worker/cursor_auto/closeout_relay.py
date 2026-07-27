"""Select operator-facing CLOSEOUT payload for cursor-auto relay.

Proxy §2 wants ``ac_verdict`` / ``deltas_to_spec`` etc. GIW's cursor-sdk bus
turn is a machine capture/manifest that shares the name "closeout". Prefer an
authored §2 body (usually the repo sidecar) when present; otherwise synthesize
§2 from the wrapper manifest so the operator never receives raw JSON alone.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
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
_VALID_WRAPPER_STATUSES = frozenset({"complete", "partial", "blocked"})
_MAX_EXECUTOR_EXCERPT_CHARS = 1500


@dataclass(frozen=True, slots=True)
class CloseoutRelayPayload:
    """Body + status line for ``TYPE: CLOSEOUT`` relay to the operator seat."""

    body: str
    status: str
    source: (
        str  # section2_sidecar | section2_bus | section2_synthesized | wrapper | empty
    )


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
        "effects_manifest" in data
        or "files_created" in data
        or "capture_status" in data
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


def wrapper_status(text: str) -> str | None:
    """Return wrapper manifest ``status`` when it is a known closeout value."""
    if not is_wrapper_manifest(text):
        return None
    try:
        data = json.loads(text.strip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("status")
    if not isinstance(raw, str):
        return None
    normalized = raw.lower()
    if normalized in _VALID_WRAPPER_STATUSES:
        return normalized
    return None


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [entry for entry in value if isinstance(entry, str) and entry]


def _order_preserving_dedup(*sequences: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for sequence in sequences:
        for item in sequence:
            if item not in seen:
                seen.add(item)
                merged.append(item)
    return merged


def _table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def synthesize_section2(
    *,
    wrapper_text: str | None,
    sidecar_text: str | None,
    dispatch_id: str,
) -> str | None:
    """Build operator-facing §2 markdown from a machine wrapper manifest."""
    del dispatch_id  # reserved for future trace headers; selection uses sdk metadata
    if not wrapper_text or not is_wrapper_manifest(wrapper_text):
        return None
    try:
        data = json.loads(wrapper_text.strip())
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    status = data.get("status", "partial")
    files_created = _as_str_list(data.get("files_created"))
    files_modified = _as_str_list(data.get("files_modified"))
    files_deleted = _as_str_list(data.get("files_deleted"))
    files_offgit_produced = _as_str_list(data.get("files_offgit_produced"))
    effects = _as_str_list(data.get("effects"))
    deviations = _as_str_list(data.get("deviations"))
    capture_status = data.get("capture_status")
    evidence_uris = data.get("evidence_uris")
    artifact_paths: list[str] = []
    if isinstance(evidence_uris, dict):
        artifact_paths = _as_str_list(evidence_uris.get("artifact_paths"))

    effects_union = _order_preserving_dedup(
        effects,
        files_created,
        files_modified,
        files_deleted,
        files_offgit_produced,
    )

    ac_verdict = (
        "unauthored — executor emitted no §2 body; machine-derived envelope below. "
        "Not a pass."
    )
    if sidecar_text:
        excerpt = strip_machine_tail(sidecar_text).strip()
        if excerpt:
            if len(excerpt) > _MAX_EXECUTOR_EXCERPT_CHARS:
                excerpt = excerpt[:_MAX_EXECUTOR_EXCERPT_CHARS] + "…"
            ac_verdict = f"{ac_verdict}<br><br>{excerpt}"

    if effects_union:
        effects_cell = "<br>".join(f"- {item}" for item in effects_union)
    else:
        effects_cell = (
            f"none captured — capture_status={capture_status}; "
            'per §4.7 a codeblind read of "none" is not authority'
        )

    evidence_parts: list[str] = []
    if artifact_paths:
        evidence_parts.append("artifact_paths: " + ", ".join(artifact_paths))
    if deviations:
        evidence_parts.append("deviations: " + "; ".join(deviations))
    if capture_status is not None:
        evidence_parts.append(f"capture_status={capture_status}")
    evidence_cell = "; ".join(evidence_parts) if evidence_parts else "none"

    rows = (
        ("status", str(status)),
        ("ac_verdict", ac_verdict),
        ("deltas_to_spec", "unauthored — not reported by executor"),
        ("decisions_taken", "unauthored — not reported by executor"),
        ("effects", effects_cell),
        ("evidence", evidence_cell),
        ("next", "unauthored — operator must derive from effects above"),
        ("open forks", "unknown — executor emitted no §2"),
    )
    lines = [
        "TYPE: CLOSEOUT",
        "",
        "| Field | Value |",
        "|---|---|",
    ]
    for field, value in rows:
        lines.append(f"| {field} | {_table_cell(value)} |")
    return "\n".join(lines)


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
    dispatch_id: str = "",
) -> CloseoutRelayPayload:
    """Prefer authored §2; synthesize from wrapper manifest when absent.

    Selection order:
    1. Repo sidecar with §2 markers (machine tail stripped)
    2. Bus body when it itself looks like §2 (rare)
    3. Synthesized §2 from wrapper manifest JSON
    4. Non-manifest bus body (plain prose)
    5. Empty placeholder
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

    synthesized = synthesize_section2(
        wrapper_text=sdk_body,
        sidecar_text=sidecar_text,
        dispatch_id=dispatch_id,
    )
    if synthesized is not None:
        return CloseoutRelayPayload(
            body=synthesized,
            status=wrapper_status(sdk_body or "") or fallback_status,
            source="section2_synthesized",
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
