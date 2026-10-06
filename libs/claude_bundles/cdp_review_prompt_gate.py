"""Admit gate for CDP code-review and delivery-review prompts (a:38326).

Refuses author bodies that are under 512 UTF-8 bytes or placeholder-shaped
before the async worker is admitted. Digest helpers echo the staged body
on every CDP generate receipt.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root

from claude_bundles.cdp_model_endpoint_staging import (
    CdpStagingError,
    read_prompt_text,
)

REVIEW_PROMPT_MIN_BYTES = 512
REVIEW_CONTRACTS = frozenset({"code-review", "delivery-review"})
_PLACEHOLDER_TOKENS = frozenset({"placeholder", "todo", "tbd"})
_LONE_HEADING = re.compile(r"^#{1,6}\s+\S")
_WORD = re.compile(r"[A-Za-z]+")

FIX_HINT = (
    "land review packet on hub master or use cortex:// sidecar before dispatch"
)


def author_prompt_source(
    *,
    prompt_text: str | None,
    prompt_uri: str | None,
    sidecar_ref: str | None,
    packet_path: str | None,
) -> tuple[str, str, str]:
    """Resolve author body with ``read_prompt_text`` precedence.

    Returns ``(body, source_kind, source_ref)`` where ``source_ref`` names
    the winning input and its literal ref.
    """
    if prompt_text is not None and str(prompt_text).strip():
        body = read_prompt_text(prompt_text=prompt_text)
        return body, "prompt", "prompt"
    for kind, candidate in (
        ("prompt_uri", prompt_uri),
        ("sidecar_ref", sidecar_ref),
        ("packet_path", packet_path),
    ):
        if candidate is None or not str(candidate).strip():
            continue
        raw = str(candidate).strip()
        body = read_prompt_text(**{kind: raw})
        return body, kind, f"{kind}={raw}"
    raise CdpStagingError(
        "CDP generate requires prompt_text, prompt_uri, sidecar_ref, or packet_path",
        code="cdp_prompt_missing",
    )


def is_review_prompt_placeholder(body: str) -> bool:
    """True when the body is placeholder tokens or a lone markdown heading."""
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if len(lines) == 1 and _LONE_HEADING.match(lines[0]):
        return True
    words = _WORD.findall(body)
    if not words:
        return False
    return all(word.casefold() in _PLACEHOLDER_TOKENS for word in words)


def enforce_review_prompt_gate(
    *,
    contract: str | None,
    prompt_text: str | None,
    prompt_uri: str | None = None,
    sidecar_ref: str | None = None,
    packet_path: str | None = None,
) -> None:
    """Refuse tiny or placeholder review packets before worker admit."""
    if contract not in REVIEW_CONTRACTS:
        return
    body, kind, source_ref = author_prompt_source(
        prompt_text=prompt_text,
        prompt_uri=prompt_uri,
        sidecar_ref=sidecar_ref,
        packet_path=packet_path,
    )
    raw = body.encode("utf-8")
    placeholder = is_review_prompt_placeholder(body)
    if not placeholder and len(raw) >= REVIEW_PROMPT_MIN_BYTES:
        return
    code = (
        "review_prompt_placeholder"
        if placeholder
        else "review_prompt_too_small"
    )
    field = "sidecar_ref" if kind == "sidecar_ref" else "prompt"
    if kind == "packet_path":
        field = "prompt"
    raise CdpStagingError(
        f"CDP {contract} prompt refused ({code})",
        code=code,
        field=field,
        details={
            "prompt_byte_count": len(raw),
            "prompt_sha256": hashlib.sha256(raw).hexdigest(),
            "source_ref": source_ref,
            "fix_hint": FIX_HINT,
        },
    )


def cortex_uri_path(uri: str) -> Path:
    """Map ``cortex://`` to an on-disk file under ``cortex_files_root()``."""
    rel = uri.removeprefix("cortex://").lstrip("/")
    path = cortex_files_root() / rel
    if path.is_dir():
        path = path / "prompt.md"
    return path


def read_cortex_uri_bytes(uri: str) -> bytes:
    """Load staged prompt bytes for a ``cortex://`` URI."""
    path = cortex_uri_path(uri)
    if not path.is_file():
        raise CdpStagingError(
            f"staged CDP prompt missing on disk: {uri!r}",
            code="cdp_prompt_missing",
        )
    return path.read_bytes()


def staged_prompt_digest(prompt_uri: str) -> dict[str, int | str]:
    """UTF-8 byte count and SHA-256 hex of the staged prompt body."""
    raw = read_cortex_uri_bytes(prompt_uri)
    return {
        "staged_prompt_byte_count": len(raw),
        "staged_prompt_sha256": hashlib.sha256(raw).hexdigest(),
    }
