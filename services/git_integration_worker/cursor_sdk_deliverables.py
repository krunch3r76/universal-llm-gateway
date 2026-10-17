"""Cortex-pinned deliverable resolution + repo closeout sidecar helpers (cursor-sdk)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from implement_admission.closeout_helpers import cortex_files_root
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_WORKSPACES_REPO = "universal-llm-gateway"
_SIDECAR_DIR = "tmp/reviews/closeouts"
# Admitted by cortex pinned_deliverable sandbox (notes/ file-root dir).
_CLOSEOUT_CORTEX_REL_DIR = "notes/system/threads"

PinnedWriteResult = dict[str, Any]
PinnedWriteFn = Callable[..., Coroutine[Any, Any, PinnedWriteResult | None]]


def pinned_write_digest(result: PinnedWriteResult | None) -> str | None:
    """Extract a non-empty digest from a pinned/cortex write ack, if present."""
    if not result or "error" in result:
        return None
    for key in ("written_sha256", "sha256"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def cortex_promote_ack(result: PinnedWriteResult | None) -> str | None:
    """Return cortex:// uri only when the ack includes uri + digest (fail-closed)."""
    if not result or "error" in result:
        return None
    uri = result.get("uri")
    if not isinstance(uri, str) or not uri.startswith("cortex://"):
        return None
    if not pinned_write_digest(result):
        return None
    return uri


@dataclass(frozen=True)
class PinnedResolution:
    uris: list[str]
    satisfied_rels: tuple[str, ...]
    divergent_rels: tuple[str, ...]


def _sidecar_rel_path(dispatch_id: str) -> str:
    return f"{_SIDECAR_DIR}/{dispatch_id}.md"


def sidecar_workspaces_ref(dispatch_id: str) -> str:
    return f"workspaces://{_WORKSPACES_REPO}/{_sidecar_rel_path(dispatch_id)}"


def full_result_text(body: str, degraded_reason: str | None) -> str:
    if degraded_reason:
        return f"status: degraded\nreason: {degraded_reason}\n\n{body}"
    return body


# Maestro step 2 reads the preamble before the first ATX heading, capped at
# 4500 characters (cdp-operator-proxy: fs read ≤4500; a longer first section
# falls through to a line-start VERDICT/status search and skips
# structured_closeout_full). A verbatim model body can hide those fields past
# that window — specimen closeout d40fa4c3b9c3-8dcc4e70.md, 153153-char preamble.
_HARVEST_SECTION_CAP = 4500
_CLOSEOUT_BODY_HEADING = "## closeout_body"
_UNSTATED_STATUS_LINE = "status: unstated"
_EMPTY_RESIDUE_LINE = "residue: none"
_STATUS_LINE_RE = re.compile(r"(?m)^(status:[^\n]*)$")
_VERDICT_LINE_RE = re.compile(r"(?m)^(VERDICT:[^\n]*)$")
_RESIDUE_LINE_RE = re.compile(r"(?m)^(residue:[^\n]*)$")
_ATX_HEADING_RE = re.compile(r"(?m)^#{1,6} ")


def _first_atx_offset(text: str) -> int | None:
    match = _ATX_HEADING_RE.search(text)
    return None if match is None else match.start()


_FENCE_MARK_RE = re.compile(r"(?m)^```")
_STATUS_TOKENS = frozenset(
    {"complete", "partial", "failed", "gated", "degraded", "blocked"}
)


def _fenced_spans(text: str) -> tuple[tuple[int, int], ...]:
    marks = [match.start() for match in _FENCE_MARK_RE.finditer(text)]
    spans: list[tuple[int, int]] = []
    for index in range(0, len(marks), 2):
        end = marks[index + 1] if index + 1 < len(marks) else len(text)
        spans.append((marks[index], end))
    return tuple(spans)


def _unfenced_line_starts(pattern: re.Pattern[str], text: str) -> list[str]:
    spans = _fenced_spans(text)
    hits: list[str] = []
    for match in pattern.finditer(text):
        if any(start <= match.start() < end for start, end in spans):
            continue
        hits.append(match.group(1))
    return hits


def _unique_line(hits: list[str]) -> str | None:
    distinct = list(dict.fromkeys(hits))
    if len(distinct) == 1:
        return distinct[0]
    return None


def _accepted_status_line(line: str) -> bool:
    value = line.split(":", 1)[1].strip().lower()
    token = value.split()[0].split(":")[0] if value else ""
    return token in _STATUS_TOKENS


def _harvest_field_lines(content: str) -> tuple[str, str | None, str]:
    """Status, optional VERDICT, and residue to place in the harvest preamble.

    A leading ``status:`` line is the wrapper prefix (``full_result_text``
    degraded). A single unfenced line is copied up. Fenced lines are ignored.
    Distinct unfenced hits are not collapsed to the last one: status and
    residue fall back to sentinels the receipt can replace, and VERDICT is
    omitted so a quoted artifact cannot become the only line maestro reads.
    """
    if content.startswith("status:"):
        status = content.split("\n", 1)[0]
    else:
        candidate = _unique_line(_unfenced_line_starts(_STATUS_LINE_RE, content))
        status = (
            candidate
            if candidate and _accepted_status_line(candidate)
            else _UNSTATED_STATUS_LINE
        )
    verdict = _unique_line(_unfenced_line_starts(_VERDICT_LINE_RE, content))
    residue = (
        _unique_line(_unfenced_line_starts(_RESIDUE_LINE_RE, content))
        or _EMPTY_RESIDUE_LINE
    )
    return status, verdict, residue


def _missing_harvest_lines(section: str, content: str) -> list[str]:
    status, verdict, residue = _harvest_field_lines(content)
    missing: list[str] = []
    if _STATUS_LINE_RE.search(section) is None:
        missing.append(status)
    if verdict and _VERDICT_LINE_RE.search(section) is None:
        missing.append(verdict)
    if _RESIDUE_LINE_RE.search(section) is None:
        missing.append(residue)
    return missing


def _fit_preamble(lines: list[str]) -> str:
    block = "\n".join(lines) + "\n"
    if len(block) <= _HARVEST_SECTION_CAP:
        return block
    clipped = block[: _HARVEST_SECTION_CAP - 1]
    if clipped.endswith("\n"):
        return clipped
    return clipped + "\n"


def _splice_harvest_lines(
    content: str,
    section: str,
    heading_at: int | None,
    missing: list[str],
) -> str | None:
    extra = "\n".join(missing) + "\n"
    base = section
    if base and not base.endswith("\n"):
        base += "\n"
    if base and not base.endswith("\n\n"):
        base += "\n"
    merged = base + extra
    if len(merged) > _HARVEST_SECTION_CAP:
        return None
    if heading_at is None:
        return merged
    return merged + content[heading_at:]


def bound_closeout_harvest_preamble(content: str) -> str:
    """Keep line-start harvest fields inside a first section of at most 4500 chars.

    The first section is the preamble before the first ATX heading. When that
    preamble already fits and already carries status, residue, and VERDICT
    (only if the model emitted one), the bytes are unchanged. A short preamble
    that is only missing lines gets those lines appended, so an existing
    ``status:`` prefix stays at column 0. A preamble over the cap is closed
    with ``## closeout_body`` and the original bytes follow that heading.
    """
    heading_at = _first_atx_offset(content)
    section = content if heading_at is None else content[:heading_at]
    missing = _missing_harvest_lines(section, content)
    if len(section) <= _HARVEST_SECTION_CAP and not missing:
        return content
    if len(section) <= _HARVEST_SECTION_CAP and missing:
        spliced = _splice_harvest_lines(content, section, heading_at, missing)
        if spliced is not None:
            return spliced
    status, verdict, residue = _harvest_field_lines(content)
    lines = [status]
    if verdict:
        lines.append(verdict)
    lines.append(residue)
    preamble = _fit_preamble(lines)
    if content.startswith(f"{preamble}{_CLOSEOUT_BODY_HEADING}"):
        return content
    return f"{preamble}{_CLOSEOUT_BODY_HEADING}\n\n{content}"


def _replace_exact_line(section: str, old: str, new: str) -> str:
    lines = section.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.rstrip("\r\n") == old:
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = new + newline
            break
    return "".join(lines)


def _clip_harvest_section(section: str) -> str:
    if len(section) <= _HARVEST_SECTION_CAP:
        return section
    clipped = section[: _HARVEST_SECTION_CAP - 1]
    if clipped.endswith("\n"):
        return clipped
    return clipped + "\n"


def _render_residue_line(items: object) -> str | None:
    if isinstance(items, str):
        parts = [items.strip()] if items.strip() else []
    elif isinstance(items, list):
        parts = [str(item).strip() for item in items if str(item).strip()]
    else:
        return None
    if not parts:
        return None
    return "residue: " + "; ".join(part.replace("\n", " ") for part in parts)


def stamp_harvest_status_from_structured(existing: str, full_body: str) -> str:
    """Replace harvest sentinels with the structured closeout's status and residue.

    Model text that never emitted line-start ``status:`` is stored as
    ``status: unstated``. The receipt JSON is the status the harvester must
    see; ``## structured_closeout_full`` itself is excluded from that read.
    """
    try:
        payload = json.loads(full_body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return existing
    if not isinstance(payload, dict):
        return existing
    heading_at = _first_atx_offset(existing)
    section = existing if heading_at is None else existing[:heading_at]
    rest = "" if heading_at is None else existing[heading_at:]
    status = payload.get("status")
    if (
        isinstance(status, str)
        and status.strip()
        and _UNSTATED_STATUS_LINE in section.splitlines()
    ):
        section = _replace_exact_line(
            section, _UNSTATED_STATUS_LINE, f"status: {status.strip()}"
        )
    residue_line = _render_residue_line(payload.get("propagation_residue"))
    if residue_line and _EMPTY_RESIDUE_LINE in section.splitlines():
        section = _replace_exact_line(section, _EMPTY_RESIDUE_LINE, residue_line)
    return _clip_harvest_section(section) + rest


def write_repo_sidecar(source_repo: Path, dispatch_id: str, content: str) -> Path:
    path = source_repo / _sidecar_rel_path(dispatch_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bound_closeout_harvest_preamble(content), encoding="utf-8")
    return path


def normalize_cortex_rel(raw: str) -> str | None:
    path = raw.strip()
    for prefix in ("cortex://", "cortex:"):
        if path.lower().startswith(prefix):
            path = path[len(prefix) :]
            break
    path = path.lstrip("/")
    if not path or ".." in Path(path).parts:
        return None
    return path


def _has_cortex_scheme(raw: str) -> bool:
    s = raw.strip().lower()
    return s.startswith("cortex://") or s.startswith("cortex:")


def cortex_expected_rels(files_expected: list[str]) -> list[str]:
    seen: set[str] = set()
    rels: list[str] = []
    for raw in files_expected:
        if not _has_cortex_scheme(raw):
            continue
        rel = normalize_cortex_rel(raw)
        if rel and rel not in seen:
            seen.add(rel)
            rels.append(rel)
    return rels


def artifact_paths_for_closeout(
    sidecar_ref: str,
    cortex_uris: list[str],
    *,
    cortex_first: bool = False,
    offgit_deliverable_uris: list[str] | None = None,
) -> list[str]:
    if cortex_first and cortex_uris:
        paths: list[str] = []
        seen: set[str] = set()
        for uri in cortex_uris:
            if uri == sidecar_ref:
                continue
            if uri not in seen:
                paths.append(uri)
                seen.add(uri)
        if sidecar_ref not in seen:
            paths.append(sidecar_ref)
        for uri in offgit_deliverable_uris or []:
            if uri not in seen:
                paths.append(uri)
                seen.add(uri)
        return paths
    paths = [sidecar_ref]
    seen = {sidecar_ref}
    for uri in cortex_uris:
        if uri not in seen:
            paths.append(uri)
            seen.add(uri)
    for uri in offgit_deliverable_uris or []:
        if uri not in seen:
            paths.append(uri)
            seen.add(uri)
    return paths


def closeout_cortex_sidecar_rel_path(thread_id: str, dispatch_id: str) -> str:
    return (
        f"{_CLOSEOUT_CORTEX_REL_DIR}/{thread_id}-cursor-sdk-closeout-{dispatch_id}.md"
    )


def body_relocated_meta(
    full_body: str,
    uri: str,
    *,
    sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "uri": uri,
        "sha256": sha256 or hashlib.sha256(full_body.encode("utf-8")).hexdigest(),
        "body_chars": len(full_body),
    }


STRUCTURED_CLOSEOUT_FULL_HEADING = "## structured_closeout_full"
STRUCTURED_CLOSEOUT_FULL_MARKER = f"\n{STRUCTURED_CLOSEOUT_FULL_HEADING}"


def sidecar_has_structured_closeout_full(sidecar_text: str) -> bool:
    return STRUCTURED_CLOSEOUT_FULL_MARKER in sidecar_text


def append_structured_closeout_full_to_repo_sidecar(
    sidecar_path: Path,
    full_body: str,
) -> None:
    existing = sidecar_path.read_text(encoding="utf-8")
    if sidecar_has_structured_closeout_full(existing):
        return
    stamped = stamp_harvest_status_from_structured(existing, full_body)
    sidecar_path.write_text(
        stamped + f"\n\n{STRUCTURED_CLOSEOUT_FULL_HEADING}\n\n" + full_body,
        encoding="utf-8",
    )


def persist_structured_closeout_full_to_repo_sidecar(
    *,
    sidecar_path: Path,
    full_body: str,
    dispatch_id: str,
    thread_id: str,
) -> str | None:
    """Append full ImplementCloseout JSON to repo sidecar; return deviation if failed."""
    from services.git_integration_worker.cursor_sdk_events import (
        emit_sdk_closeout_sidecar_receipt_failed,
    )

    try:
        pretty_body = pretty_relocated_closeout_body(full_body)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        reason = f"serialize:{type(exc).__name__}"
        emit_sdk_closeout_sidecar_receipt_failed(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            reason=reason,
            sidecar_path=str(sidecar_path),
        )
        logger.error(
            "structured closeout receipt serialize failed: dispatch_id=%s reason=%s",
            dispatch_id,
            reason,
        )
        return f"closeout:structured_receipt_sidecar_failed:{reason}"

    try:
        append_structured_closeout_full_to_repo_sidecar(sidecar_path, pretty_body)
    except OSError as exc:
        reason = f"write:{type(exc).__name__}"
        emit_sdk_closeout_sidecar_receipt_failed(
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            reason=reason,
            sidecar_path=str(sidecar_path),
        )
        logger.error(
            "structured closeout receipt write failed: dispatch_id=%s path=%s reason=%s",
            dispatch_id,
            sidecar_path,
            reason,
        )
        return f"closeout:structured_receipt_sidecar_failed:{reason}"

    return None


async def post_closeout_sidecar(
    *,
    full_body: str,
    dispatch_id: str,
    thread_id: str,
    post_pinned: PinnedWriteFn | None = None,
    write_if_absent: bool = True,
) -> PinnedWriteResult | None:
    """Pin closeout bytes at the cortex twin URI.

    Default ``write_if_absent=True`` preserves first-writer for oversize JSON
    relocate. Acceptance-prose promote passes ``False`` so §2 evidence is not
    trapped behind a JSON-only twin at the same path (row 13).
    """
    writer = post_pinned or default_post_pinned_deliverable
    return await writer(
        rel_path=closeout_cortex_sidecar_rel_path(thread_id, dispatch_id),
        content=full_body,
        write_if_absent=write_if_absent,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )


def pretty_relocated_closeout_body(full_body: str) -> str:
    """Multi-line JSON for relocated sidecars — readable under fs-read line limits."""
    return json.dumps(json.loads(full_body), indent=2)


async def relocate_oversize_closeout_body_async(
    *,
    full_body: str,
    sidecar_path: Path,
    sidecar_ref: str,
    dispatch_id: str,
    thread_id: str,
    post_closeout_sidecar_fn: PinnedWriteFn | None = None,
) -> tuple[dict[str, Any], str]:
    pretty_body = pretty_relocated_closeout_body(full_body)
    result = await post_closeout_sidecar(
        full_body=pretty_body,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        post_pinned=post_closeout_sidecar_fn,
    )
    promote_uri = cortex_promote_ack(result)
    if promote_uri:
        return (
            body_relocated_meta(
                pretty_body,
                promote_uri,
                sha256=pinned_write_digest(result),
            ),
            "cortex",
        )
    append_structured_closeout_full_to_repo_sidecar(sidecar_path, pretty_body)
    return body_relocated_meta(pretty_body, sidecar_ref), "repo_sidecar"


def relocate_oversize_closeout_body_sync(
    *,
    full_body: str,
    sidecar_path: Path,
    sidecar_ref: str,
    dispatch_id: str,
    thread_id: str,
    post_closeout_sidecar_fn: Callable[..., PinnedWriteResult | None] | None = None,
) -> tuple[dict[str, Any], str]:
    pretty_body = pretty_relocated_closeout_body(full_body)
    if post_closeout_sidecar_fn is not None:
        result = post_closeout_sidecar_fn(
            full_body=pretty_body,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
        )
        promote_uri = cortex_promote_ack(result)
        if promote_uri:
            return (
                body_relocated_meta(
                    pretty_body,
                    promote_uri,
                    sha256=pinned_write_digest(result),
                ),
                "cortex",
            )
    append_structured_closeout_full_to_repo_sidecar(sidecar_path, pretty_body)
    return body_relocated_meta(pretty_body, sidecar_ref), "repo_sidecar"


async def default_post_pinned_deliverable(
    *,
    rel_path: str,
    content: str,
    write_if_absent: bool,
    dispatch_id: str,
    thread_id: str,
) -> PinnedWriteResult | None:
    payload = {
        "rel_path": rel_path,
        "content": content,
        "write_if_absent": write_if_absent,
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
    }
    try:
        async with make_async_client(DEFAULT_STARGATE_URL, timeout=15.0) as client:
            resp = await client.post(
                "/api/v1/implement/pinned-deliverable", json=payload
            )
        if resp.status_code >= 400:
            logger.warning(
                "pinned deliverable ingress rejected: rel=%s status=%s body=%s",
                rel_path,
                resp.status_code,
                resp.text[:300],
            )
            return None
        return resp.json()
    except Exception as exc:
        logger.warning(
            "pinned deliverable ingress transport error: rel=%s err=%s",
            rel_path,
            exc,
        )
        return None


async def resolve_cortex_pinned_deliverables(
    *,
    files_expected: list[str],
    full_text: str,
    source_repo: Path,
    dispatch_id: str,
    thread_id: str,
    post_pinned: PinnedWriteFn | None = None,
    cortex_root: Path | None = None,
) -> PinnedResolution:
    """Ensure pinned cortex deliverables exist; return resolution metadata."""
    writer = post_pinned or default_post_pinned_deliverable
    root = cortex_root or cortex_files_root()
    uris: list[str] = []
    satisfied: list[str] = []
    divergent: list[str] = []
    for rel in cortex_expected_rels(files_expected):
        try:
            cortex_path = root / rel
            repo_path = source_repo / rel
            if cortex_path.is_dir() or repo_path.is_dir():
                divergent.append(f"pinned_deliverable_invalid_target:{rel}")
                continue
            cortex_is_file = cortex_path.is_file()
            repo_is_file = repo_path.is_file()
        except OSError as exc:
            logger.warning(
                "skipping malformed deliverable rel (len=%d): %s", len(rel), exc
            )
            continue
        if cortex_is_file:
            content = cortex_path.read_text(encoding="utf-8")
            result = await writer(
                rel_path=rel,
                content=content,
                write_if_absent=False,
                dispatch_id=dispatch_id,
                thread_id=thread_id,
            )
        elif repo_is_file:
            content = repo_path.read_text(encoding="utf-8")
            divergent.append(f"pinned_deliverable_wrong_sandbox:{rel}")
            result = await writer(
                rel_path=rel,
                content=content,
                write_if_absent=False,
                dispatch_id=dispatch_id,
                thread_id=thread_id,
            )
        else:
            result = await writer(
                rel_path=rel,
                content=full_text,
                write_if_absent=True,
                dispatch_id=dispatch_id,
                thread_id=thread_id,
            )
        if not result or "error" in result:
            divergent.append(f"pinned_deliverable_write_failed:{rel}")
            continue
        uri = result.get("uri")
        if (
            isinstance(uri, str)
            and uri.startswith("cortex://")
            and pinned_write_digest(result)
        ):
            uris.append(uri)
            if f"pinned_deliverable_wrong_sandbox:{rel}" not in divergent:
                satisfied.append(rel)
    return PinnedResolution(
        uris=uris,
        satisfied_rels=tuple(satisfied),
        divergent_rels=tuple(divergent),
    )
