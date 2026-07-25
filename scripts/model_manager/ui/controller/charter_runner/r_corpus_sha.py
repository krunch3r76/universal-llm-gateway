"""Pre-fire gate: refuse R-admit when CHECKPOINT Sidecars pin ≠ live dense-spec hash.

F5 / a:26095 — R-admit consult admission must fail closed when the Sidecars
``spec_sha256:<hex>`` pin has drifted from on-disk dense-spec bytes (or is
missing / ambiguous / malformed / unreadable). Gate runs in
``tick_loop._admit_window`` **before** ``mark_admit_intent``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root

_REASON = "stale_r_corpus_sha"
_SUB_REASONS = frozenset(
    {
        "stale",
        "missing_pin",
        "missing_uri",
        "ambiguous_pin",
        "malformed_pin",
        "unreadable",
    }
)

_SIDE_CARS_RE = re.compile(
    r"^##\s+Sidecars\b.*?(?=^##\s|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
_PIN_RE = re.compile(
    r"(?:spec_sha256|sha256)\s*:\s*([0-9a-fA-F]+)\b",
    re.IGNORECASE,
)
_SPEC_URI_RE = re.compile(
    r"(cortex://notes/system/specs/[^\s\)\],|]+)",
    re.IGNORECASE,
)
_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_ROW_RE = re.compile(r"^\s*[-*|](.+)$", re.MULTILINE)


@dataclass(frozen=True)
class RCorpusShaResult:
    """Outcome of verifying a CHECKPOINT Sidecars dense-spec hash pin."""

    ok: bool
    reason: str | None = None
    sub_reason: str | None = None
    pinned_hex: str | None = None
    live_hex: str | None = None
    dense_spec_uri: str | None = None

    def __post_init__(self) -> None:
        if self.ok:
            return
        if self.reason != _REASON:
            object.__setattr__(self, "reason", _REASON)
        if self.sub_reason not in _SUB_REASONS:
            object.__setattr__(self, "sub_reason", self.sub_reason or "stale")


@dataclass(frozen=True)
class ExtractResult:
    """Pin extraction from a CHECKPOINT Sidecars section (same-row association)."""

    ok: bool
    dense_spec_uri: str | None = None
    pinned_hex: str | None = None
    sub_reason: str | None = None


def _sidecars_section(checkpoint_body: str) -> str:
    match = _SIDE_CARS_RE.search(checkpoint_body or "")
    return match.group(0) if match else ""


def _candidate_rows(sidecars: str) -> list[tuple[str, str]]:
    """Return (uri, hex) pairs from Sidecars rows that carry both on the same row."""
    found: list[tuple[str, str]] = []
    for row_m in _ROW_RE.finditer(sidecars):
        row = row_m.group(0)
        uri_m = _SPEC_URI_RE.search(row)
        pin_m = _PIN_RE.search(row)
        if uri_m is None or pin_m is None:
            continue
        found.append((uri_m.group(1).rstrip("."), pin_m.group(1)))
    return found


def extract_r_corpus_pin(checkpoint_body: str) -> ExtractResult:
    """Extract the same-row Sidecars dense-spec URI + ``spec_sha256`` pin.

    A1: pin and URI must share one Sidecars row. Zero candidates with a pin
    elsewhere ⇒ ``missing_uri`` / ``missing_pin``; ≥2 candidates ⇒
    ``ambiguous_pin``; non-64-hex pin ⇒ ``malformed_pin``.
    """
    sidecars = _sidecars_section(checkpoint_body)
    candidates = _candidate_rows(sidecars)
    if len(candidates) > 1:
        return ExtractResult(ok=False, sub_reason="ambiguous_pin")
    if len(candidates) == 1:
        uri, hex_raw = candidates[0]
        if not _HEX64_RE.match(hex_raw):
            return ExtractResult(
                ok=False,
                dense_spec_uri=uri,
                pinned_hex=hex_raw.lower(),
                sub_reason="malformed_pin",
            )
        return ExtractResult(
            ok=True,
            dense_spec_uri=uri,
            pinned_hex=hex_raw.lower(),
        )
    # No same-row candidate — classify missing_pin vs missing_uri from section.
    has_pin = _PIN_RE.search(sidecars) is not None
    has_uri = _SPEC_URI_RE.search(sidecars) is not None
    if has_pin and not has_uri:
        return ExtractResult(ok=False, sub_reason="missing_uri")
    if has_uri and not has_pin:
        # URI present but not co-located with pin on same row.
        pin_elsewhere = _PIN_RE.search(checkpoint_body or "")
        if pin_elsewhere is None:
            return ExtractResult(ok=False, sub_reason="missing_pin")
        return ExtractResult(ok=False, sub_reason="missing_uri")
    if has_pin:
        # Pin without co-located URI (URI may be off-row).
        return ExtractResult(ok=False, sub_reason="missing_uri")
    return ExtractResult(ok=False, sub_reason="missing_pin")


def _resolve_spec_path(uri: str, cortex_root: Path) -> Path | None:
    raw = uri.strip()
    if raw.startswith("cortex://"):
        rel = raw[len("cortex://") :]
    elif raw.startswith("notes/"):
        rel = raw
    else:
        return None
    root = cortex_root.resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def file_sha256_hex(path: Path) -> str:
    """Return lowercase hex SHA-256 of ``path`` file bytes (≡ fs ``read_sha256``)."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_r_corpus_sha(
    checkpoint_body: str,
    *,
    cortex_root: Path | None = None,
) -> RCorpusShaResult:
    """Compare Sidecars same-row pin to live dense-spec file bytes under cortex root.

    Returns ``ok=True`` only when exactly one same-row pin matches the on-disk
    SHA-256. Failures share ``reason=stale_r_corpus_sha`` with a closed
    ``sub_reason`` set (A2).
    """
    extracted = extract_r_corpus_pin(checkpoint_body)
    if not extracted.ok:
        return RCorpusShaResult(
            ok=False,
            reason=_REASON,
            sub_reason=extracted.sub_reason,
            pinned_hex=extracted.pinned_hex,
            dense_spec_uri=extracted.dense_spec_uri,
        )
    assert extracted.dense_spec_uri is not None and extracted.pinned_hex is not None
    root = cortex_root if cortex_root is not None else cortex_files_root()
    path = _resolve_spec_path(extracted.dense_spec_uri, root)
    if path is None or not path.is_file():
        return RCorpusShaResult(
            ok=False,
            reason=_REASON,
            sub_reason="unreadable",
            pinned_hex=extracted.pinned_hex,
            dense_spec_uri=extracted.dense_spec_uri,
        )
    try:
        live = file_sha256_hex(path)
    except OSError:
        return RCorpusShaResult(
            ok=False,
            reason=_REASON,
            sub_reason="unreadable",
            pinned_hex=extracted.pinned_hex,
            dense_spec_uri=extracted.dense_spec_uri,
        )
    if live.lower() != extracted.pinned_hex.lower():
        return RCorpusShaResult(
            ok=False,
            reason=_REASON,
            sub_reason="stale",
            pinned_hex=extracted.pinned_hex,
            live_hex=live,
            dense_spec_uri=extracted.dense_spec_uri,
        )
    return RCorpusShaResult(
        ok=True,
        pinned_hex=extracted.pinned_hex,
        live_hex=live,
        dense_spec_uri=extracted.dense_spec_uri,
    )


async def refuse_stale_r_admit(
    *,
    root_id: str,
    checkpoint: dict | None,
    result: RCorpusShaResult,
    events_module: object,
    log: object,
) -> None:
    """Log + emit ``root_skipped`` for a failed R-corpus gate (no admit_intent)."""
    skip_reason = result.reason or _REASON
    log.warning(  # type: ignore[attr-defined]
        "charter-runner skip r_admit admit root=%s reason=%s sub_reason=%s "
        "pinned=%s live=%s uri=%s",
        root_id,
        skip_reason,
        result.sub_reason,
        result.pinned_hex,
        result.live_hex,
        result.dense_spec_uri,
    )
    cp_turn = (checkpoint or {}).get("turn_number")
    await events_module.emit_manage_charter_tick_root_skipped(  # type: ignore[attr-defined]
        root=root_id,
        reason=f"{skip_reason}:{result.sub_reason or 'stale'}",
        checkpoint_turn=int(cp_turn) if cp_turn is not None else None,
    )
