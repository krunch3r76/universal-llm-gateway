"""Compact executive envelope for large agent-bus closeout / CDP reply bodies."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_COMPACT_DEFAULT_LIMIT = 1500
_CDP_LINE_CAP = 45
_CDP_BYTE_CAP = 1400
_JSON_SUMMARY_LINE_CAP = 40

_EXEC_ID_RE = re.compile(r"execution_id:\s*(\S+)", re.IGNORECASE)
_ARCHIVE_URI_RE = re.compile(r"archive_uri:\s*(\S+)", re.IGNORECASE)


def _body_bytes(raw: str) -> int:
    return len(raw.encode("utf-8"))


def _is_json_closeout(subject: str, body: str) -> bool:
    if "CLOSEOUT" in (subject or ""):
        return True
    stripped = (body or "").lstrip()
    return stripped.startswith('{"schema_version":')


def _is_cdp_reply(subject: str, body: str) -> bool:
    subj = (subject or "").lower()
    if subj.startswith("cdp reply"):
        return True
    return (body or "").startswith("# CDP generate result")


def _lines_cap(text: str, max_lines: int, max_bytes: int) -> str:
    lines = text.splitlines()
    kept: list[str] = []
    size = 0
    for line in lines[:max_lines]:
        add = len(line.encode("utf-8")) + (1 if kept else 0)
        if size + add > max_bytes:
            break
        kept.append(line)
        size += add
    return "\n".join(kept)


def _compact_json_closeout(body: str) -> str:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body[:_COMPACT_DEFAULT_LIMIT]
    if not isinstance(data, dict):
        return body[:_COMPACT_DEFAULT_LIMIT]
    summary = data.get("summary") or data.get("work_outcome") or ""
    if isinstance(summary, dict):
        summary_text = json.dumps(summary, indent=2, sort_keys=True)
    else:
        summary_text = str(summary)
    summary_excerpt = _lines_cap(summary_text, _JSON_SUMMARY_LINE_CAP, 800)
    envelope: dict[str, Any] = {
        "projection": "compact_executive",
        "status": data.get("status"),
        "work_outcome": data.get("work_outcome"),
        "summary_excerpt": summary_excerpt,
        "source_ref": data.get("source_ref"),
    }
    evidence = data.get("evidence_uris")
    if isinstance(evidence, dict):
        envelope["evidence_uris"] = {
            k: evidence.get(k)
            for k in ("artifact_paths", "artifact_digests", "git_refs")
            if k in evidence
        }
    verification = data.get("verification")
    if isinstance(verification, dict):
        envelope["verification"] = {
            k: verification.get(k) for k in list(verification.keys())[:12]
        }
    elif verification is not None:
        envelope["verification"] = verification
    out = json.dumps(envelope, indent=2, sort_keys=True)
    if _body_bytes(out) > _COMPACT_DEFAULT_LIMIT:
        envelope.pop("verification", None)
        out = json.dumps(envelope, indent=2, sort_keys=True)
    return out[:_COMPACT_DEFAULT_LIMIT]


def _compact_cdp_markdown(body: str) -> str:
    lines = body.splitlines()
    header: list[str] = []
    for line in lines:
        if line.startswith("VERDICT:"):
            header.append(line)
        elif line.startswith("read-all-cited-files:"):
            header.append(line)
        else:
            match = _EXEC_ID_RE.search(line)
            if match:
                header.append(f"execution_id: {match.group(1)}")
            match = _ARCHIVE_URI_RE.search(line)
            if match:
                header.append(f"archive_uri: {match.group(1)}")
    excerpt = _lines_cap(body, _CDP_LINE_CAP, _CDP_BYTE_CAP)
    parts = header + ["", excerpt]
    out = "\n".join(parts).strip()
    if _body_bytes(out) > _COMPACT_DEFAULT_LIMIT:
        out = out[:_COMPACT_DEFAULT_LIMIT]
    return out


def project_turn_body(
    turn: dict[str, Any],
    *,
    projection: str | None,
) -> dict[str, Any]:
    """Return a shallow copy of *turn* with optional compact body projection."""
    if not isinstance(turn, dict):
        return turn
    raw_body = turn.get("body")
    if not isinstance(raw_body, str):
        return turn
    raw_bytes = _body_bytes(raw_body)
    mode = (projection or "compact").strip().lower()
    if mode == "full" or raw_bytes <= _COMPACT_DEFAULT_LIMIT:
        return turn
    subject = str(turn.get("subject") or "")
    if not (_is_json_closeout(subject, raw_body) or _is_cdp_reply(subject, raw_body)):
        return turn
    projected = turn.copy()
    if _is_json_closeout(subject, raw_body):
        projected["body"] = _compact_json_closeout(raw_body)
    else:
        projected["body"] = _compact_cdp_markdown(raw_body)
    projected["body_sha256"] = hashlib.sha256(raw_body.encode("utf-8")).hexdigest()
    projected["body_bytes"] = raw_bytes
    projected["projection_available"] = "full"
    return projected
