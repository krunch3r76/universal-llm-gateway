"""Append one RULE entry from a cursor-sdk closeout file (K29 a)."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_bundles.journal_hop_projection import MAESTRO_JOURNAL_URI
from claude_bundles.journal_lesson import journal_has_source_turn_id, writer_mode

_RULE_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:\*\*)?RULE:",
    re.IGNORECASE,
)
_SOURCE_TURN_ID_RE = re.compile(
    r"^\s*(?:[-*]\s+)?(?:\*\*)?source_turn_id:\s*(\S+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AppendResult:
    ok: bool
    skipped: bool = False
    skipped_reason: str | None = None
    error: dict[str, str] | None = None
    written_sha256: str | None = None


def _files_root() -> Path | None:
    root = os.environ.get("CORTEX_FILES_ROOT", "").strip()
    if not root:
        return None
    return Path(root)


def _uri_to_path(uri: str, root: Path) -> Path:
    if not uri.startswith("cortex://"):
        raise ValueError(f"expected cortex:// URI, got {uri!r}")
    return root / uri.removeprefix("cortex://")


def read_journal_text(uri: str = MAESTRO_JOURNAL_URI) -> tuple[str, str] | dict[str, Any]:
    root = _files_root()
    if root is None:
        return {"error": {"kind": "files_root_unset", "message": "CORTEX_FILES_ROOT unset"}}
    path = _uri_to_path(uri, root)
    if not path.is_file():
        return "", hashlib.sha256(b"").hexdigest()
    data = path.read_bytes()
    return data.decode("utf-8"), hashlib.sha256(data).hexdigest()


def _extract_rule_and_turn(closeout_text: str) -> tuple[str | None, str | None]:
    rule_line: str | None = None
    turn_id: str | None = None
    for line in (closeout_text or "").splitlines():
        if rule_line is None and _RULE_LINE_RE.match(line):
            rule_line = line.rstrip()
        match = _SOURCE_TURN_ID_RE.match(line)
        if match:
            turn_id = match.group(1)
    return rule_line, turn_id


def _format_new_entry(*, rule_line: str, turn_id: str) -> str:
    stamp = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    return (
        f"## {stamp} — projected lesson\n\n"
        f"{rule_line}\n"
        f"source_turn_id: {turn_id}\n\n"
    )


def append_journal_from_closeout(
    closeout_text: str,
    *,
    turn_id: str | None = None,
    journal_uri: str = MAESTRO_JOURNAL_URI,
) -> AppendResult:
    """Write one RULE entry from *closeout_text*; no-op for admit-only payloads."""
    if not (closeout_text or "").strip():
        return AppendResult(ok=True, skipped=True, skipped_reason="no_rule_line")
    if "poll_hint" in closeout_text and "execution_id" in closeout_text:
        if _RULE_LINE_RE.search(closeout_text) is None:
            return AppendResult(ok=True, skipped=True, skipped_reason="admit_envelope")

    rule_line, parsed_turn = _extract_rule_and_turn(closeout_text)
    effective_turn = (turn_id or parsed_turn or "").strip()
    if not rule_line:
        return AppendResult(ok=True, skipped=True, skipped_reason="no_rule_line")
    if not effective_turn:
        return AppendResult(ok=True, skipped=True, skipped_reason="no_turn_id")

    root = _files_root()
    if root is None:
        return AppendResult(
            ok=False,
            error={"kind": "files_root_unset", "message": "CORTEX_FILES_ROOT unset"},
        )

    read_res = read_journal_text(journal_uri)
    if isinstance(read_res, dict):
        return AppendResult(ok=False, error=read_res["error"])
    existing, sha = read_res

    if writer_mode(existing) != "projector":
        return AppendResult(
            ok=False,
            error={"kind": "projector_not_live", "message": "projector_live marker absent"},
        )

    if journal_has_source_turn_id(existing, effective_turn):
        return AppendResult(
            ok=True,
            skipped=True,
            skipped_reason="deduped",
            written_sha256=sha,
        )

    new_block = _format_new_entry(rule_line=rule_line, turn_id=effective_turn)
    updated = new_block + existing
    path = _uri_to_path(journal_uri, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    expected_sha = sha

    for attempt in range(2):
        if path.is_file():
            data = path.read_bytes()
            live_sha = hashlib.sha256(data).hexdigest()
            current_text = data.decode("utf-8")
            if journal_has_source_turn_id(current_text, effective_turn):
                return AppendResult(
                    ok=True,
                    skipped=True,
                    skipped_reason="deduped",
                    written_sha256=live_sha,
                )
        else:
            live_sha = hashlib.sha256(b"").hexdigest()

        if live_sha != expected_sha:
            if attempt == 0:
                continue
            return AppendResult(
                ok=False,
                error={"kind": "cas_conflict", "message": "journal sha changed under write"},
            )

        path.write_text(updated, encoding="utf-8")
        new_sha = hashlib.sha256(updated.encode("utf-8")).hexdigest()
        return AppendResult(ok=True, skipped=False, written_sha256=new_sha)

    return AppendResult(
        ok=False,
        error={"kind": "cas_conflict", "message": "journal append failed after retry"},
    )


def read_closeout_file(repo_root: Path, dispatch_id: str) -> str | None:
    rel = Path("tmp/reviews/closeouts") / f"{dispatch_id}.md"
    path = repo_root / rel
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")
