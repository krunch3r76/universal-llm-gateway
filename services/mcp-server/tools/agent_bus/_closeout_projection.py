"""Compact executive envelope for large agent-bus closeout / CDP reply bodies."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from implement_admission.closeout_helpers import cortex_files_root

from tools._line_range import DEFAULT_UNWINDOWED_MAX_BYTES
from tools._unwindowed_read_projection import sidecar_section_listing

_COMPACT_MAX_BYTES = 1500
_COMPACT_BUDGET_BYTES = 1450
_CDP_LINE_CAP = 45
_CDP_BYTE_CAP = 1200
_JSON_SUMMARY_LINE_CAP = 40
_JSON_SUMMARY_BYTE_CAP = 700

_EXEC_ID_RE = re.compile(r"execution_id:\s*(\S+)", re.IGNORECASE)
_ARCHIVE_URI_RE = re.compile(r"archive_uri:\s*(\S+)", re.IGNORECASE)
_FENCE_MARK_RE = re.compile(r"(?m)^```")
_VERDICT_LINE_RE = re.compile(r"(?m)^(VERDICT:[^\n]*)$")


def _body_bytes(raw: str) -> int:
    return len(raw.encode("utf-8"))


def _fenced_spans(text: str) -> tuple[tuple[int, int], ...]:
    marks = [match.start() for match in _FENCE_MARK_RE.finditer(text)]
    spans: list[tuple[int, int]] = []
    for index in range(0, len(marks), 2):
        end = marks[index + 1] if index + 1 < len(marks) else len(text)
        spans.append((marks[index], end))
    return tuple(spans)


def _unfenced_verdict_line(body: str) -> str | None:
    spans = _fenced_spans(body)
    hits: list[str] = []
    for match in _VERDICT_LINE_RE.finditer(body):
        if any(start <= match.start() < end for start, end in spans):
            continue
        hits.append(match.group(1))
    distinct = list(dict.fromkeys(hits))
    if len(distinct) == 1:
        return distinct[0]
    return None


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


def _minimal_compact_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    return {
        "projection": "compact_executive",
        "status": envelope.get("status"),
        "landed": envelope.get("landed"),
        "head_sha": envelope.get("head_sha"),
    }


def _hard_cap_json_envelope(envelope: dict[str, Any]) -> str:
    """Last-resort UTF-8 cap preserving valid JSON under *_COMPACT_MAX_BYTES*."""
    env = dict(envelope)
    for key in ("work_outcome", "source_ref"):
        value = env.get(key)
        if not isinstance(value, str) or not value:
            continue
        text = value
        while (
            text
            and _body_bytes(json.dumps(env, indent=2, sort_keys=True))
            >= _COMPACT_MAX_BYTES
        ):
            next_len = len(text) // 2
            if next_len >= len(text):
                env.pop(key, None)
                break
            text = text[:next_len].rstrip()
            if text:
                env[key] = text
            else:
                env.pop(key, None)
                break
    out = json.dumps(env, indent=2, sort_keys=True)
    if _body_bytes(out) < _COMPACT_MAX_BYTES:
        return out
    minimal = _minimal_compact_envelope(env)
    out = json.dumps(minimal, indent=2, sort_keys=True)
    if _body_bytes(out) < _COMPACT_MAX_BYTES:
        return out
    return json.dumps(minimal, sort_keys=True)


def _fit_json_envelope(envelope: dict[str, Any]) -> str:
    """Serialize *envelope* with field truncation until UTF-8 size is under budget."""
    summary_key = "summary_excerpt"
    excerpt = str(envelope.get(summary_key) or "")
    while True:
        out = json.dumps(envelope, indent=2, sort_keys=True)
        if _body_bytes(out) < _COMPACT_MAX_BYTES:
            return out
        if excerpt and len(excerpt) > 80:
            cut = max(80, len(excerpt) // 2)
            if cut < len(excerpt):
                excerpt = excerpt[:cut].rstrip()
                envelope[summary_key] = excerpt
                continue
        envelope.pop("verification", None)
        if (
            _body_bytes(json.dumps(envelope, indent=2, sort_keys=True))
            < _COMPACT_MAX_BYTES
        ):
            return json.dumps(envelope, indent=2, sort_keys=True)
        envelope.pop("evidence_uris", None)
        if (
            _body_bytes(json.dumps(envelope, indent=2, sort_keys=True))
            < _COMPACT_MAX_BYTES
        ):
            return json.dumps(envelope, indent=2, sort_keys=True)
        envelope.pop(summary_key, None)
        if (
            _body_bytes(json.dumps(envelope, indent=2, sort_keys=True))
            < _COMPACT_MAX_BYTES
        ):
            return json.dumps(envelope, indent=2, sort_keys=True)
        return _hard_cap_json_envelope(envelope)


def _compact_json_closeout(body: str) -> str:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raw = body.encode("utf-8")[:_COMPACT_BUDGET_BYTES].decode(
            "utf-8", errors="ignore"
        )
        return raw
    if not isinstance(data, dict):
        raw = body.encode("utf-8")[:_COMPACT_BUDGET_BYTES].decode(
            "utf-8", errors="ignore"
        )
        return raw
    summary = data.get("summary") or data.get("work_outcome") or ""
    if isinstance(summary, dict):
        summary_text = json.dumps(summary, indent=2, sort_keys=True)
    else:
        summary_text = str(summary)
    summary_excerpt = _lines_cap(
        summary_text, _JSON_SUMMARY_LINE_CAP, _JSON_SUMMARY_BYTE_CAP
    )
    envelope: dict[str, Any] = {
        "projection": "compact_executive",
        "status": data.get("status"),
        "work_outcome": data.get("work_outcome"),
        "summary_excerpt": summary_excerpt,
        "source_ref": data.get("source_ref"),
    }
    for key in ("landed", "head_sha"):
        if data.get(key) is not None:
            envelope[key] = data.get(key)
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
    return _fit_json_envelope(envelope)


def _compact_cdp_markdown(body: str) -> str:
    lines = body.splitlines()
    header: list[str] = []
    verdict = _unfenced_verdict_line(body)
    if verdict:
        header.append(verdict)
    for line in lines:
        if line.startswith("read-all-cited-files:"):
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
    if _body_bytes(out) >= _COMPACT_MAX_BYTES:
        out = out.encode("utf-8")[:_COMPACT_BUDGET_BYTES].decode(
            "utf-8", errors="ignore"
        )
    return out


def _load_cortex_sidecar_text(sidecar_uri: str) -> str | None:
    uri = (sidecar_uri or "").strip()
    if not uri.startswith("cortex://"):
        return None
    rel = uri.removeprefix("cortex://").lstrip("/")
    path = (cortex_files_root() / rel).resolve()
    try:
        path.relative_to(cortex_files_root().resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > DEFAULT_UNWINDOWED_MAX_BYTES:
        raw = raw[:DEFAULT_UNWINDOWED_MAX_BYTES]
    return raw.decode("utf-8", errors="replace")


def _attach_sidecar_listing(
    projected: dict[str, Any], sidecar_uri: str
) -> dict[str, Any]:
    text = _load_cortex_sidecar_text(sidecar_uri)
    if text is None:
        projected = projected.copy()
        projected["sidecar_listing_error"] = f"sidecar not readable: {sidecar_uri}"
        return projected
    listing = sidecar_section_listing(sidecar_uri=sidecar_uri, text=text)
    if "error" in listing:
        projected = projected.copy()
        projected["sidecar_listing_error"] = listing["error"]
        return projected
    out = projected.copy()
    out["sidecar_sections"] = listing["sections"]
    out["sidecar_section_reads"] = listing["section_reads"]
    out["sidecar_total_chars"] = listing["total_chars"]
    return out


def _apply_compact_closeout(turn: dict[str, Any], raw_body: str) -> dict[str, Any]:
    raw_bytes = _body_bytes(raw_body)
    subject = str(turn.get("subject") or "")
    projected = turn.copy()
    if _is_json_closeout(subject, raw_body):
        projected["body"] = _compact_json_closeout(raw_body)
    else:
        projected["body"] = _compact_cdp_markdown(raw_body)
    projected["body_sha256"] = hashlib.sha256(raw_body.encode("utf-8")).hexdigest()
    projected["body_bytes"] = raw_bytes
    projected["projection_available"] = "full"
    return projected


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
    if mode == "full":
        return turn

    sidecar_uri = turn.get("sidecar_uri")
    if isinstance(sidecar_uri, str) and sidecar_uri.strip():
        base = turn
        if raw_bytes >= _COMPACT_MAX_BYTES:
            subject = str(turn.get("subject") or "")
            if _is_json_closeout(subject, raw_body) or _is_cdp_reply(subject, raw_body):
                base = _apply_compact_closeout(turn, raw_body)
        return _attach_sidecar_listing(base, sidecar_uri.strip())

    if raw_bytes < _COMPACT_MAX_BYTES:
        return turn
    subject = str(turn.get("subject") or "")
    if not (_is_json_closeout(subject, raw_body) or _is_cdp_reply(subject, raw_body)):
        return turn
    return _apply_compact_closeout(turn, raw_body)
