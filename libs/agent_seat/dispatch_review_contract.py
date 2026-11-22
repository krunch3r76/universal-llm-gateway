"""Review dispatch contract text, coverage manifest schema, and positive gates."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

REVIEW_CONTRACTS = frozenset({"code-review", "check-review", "delivery-review"})

FREEFORM_LADDER_REVIEW_ROUTE = "freeform-ladder"

ActualExtent = Literal[
    "whole",
    "allowed_diff",
    "named_sections",
    "partial",
    "unread",
    "unverified",
]

ACTUAL_EXTENTS: frozenset[str] = frozenset(
    {
        "whole",
        "allowed_diff",
        "named_sections",
        "partial",
        "unread",
        "unverified",
    }
)

# Provisional vocabulary — catalog had no equivalent at Leg 2 confer.
REVIEW_COVERAGE_GATE_EVENT = "dispatch.review.coverage_gate"
REVIEW_COVERAGE_GATE_EVENT_ACTION = "add"

UNSUPPORTED_ROUTE_PREFIX = "REVIEW_COVERAGE_UNSUPPORTED_ROUTE:"

REVIEW_VERDICT_CONTRACT = (
    "REVIEW VERDICT CONTRACT: When the packet names a commit range or head, "
    "the closeout's first line is the one unfenced canonical line "
    "`VERDICT: APPROVE range=<40 lowercase hex>..<40 lowercase hex> "
    "head=<40 lowercase hex>` or the same line with `REQUEST_CHANGES`. "
    "`head` equals the range tip. `fixes=<full sha of the REQUEST_CHANGES tip>` "
    "is present only when this review closes one. There is no second "
    "`VERDICT:` line. When the packet names no commit, do not invent "
    "`range=` or `head=`. Cite findings as `path:line`."
)

REVIEW_COVERAGE_CONTRACT = (
    "REVIEW COVERAGE CONTRACT: Authority for positive whole-file credit is "
    "reviewer-authored receipt bytes addressed by digest; the envelope is "
    "projection only. Emit one unfenced `coverage_ref:` line (digest or sidecar "
    "URI) beside the canonical `VERDICT:` line — never a second `VERDICT:`. "
    "Per-target rows declare `required_extent` from the packet and "
    "`actual_extent` in {whole, allowed_diff, named_sections, partial, unread, "
    "unverified}. Positive whole-file gate requires `actual_extent=whole`, a "
    "server `file_sha256`, matching full-file receipt_digest, and "
    "`actual_reader` equal to this reviewing worker. Truncated export with "
    "matching hash does not pass. Blocking findings survive incomplete coverage; "
    "name `measurement_owner` when proof is pending."
)

REVIEW_GIT_INSPECTION_GUIDANCE = (
    "CODE REVIEW INSPECTION: Use git when available (`git log`, `git diff`, "
    "`git show`), or inspect the diff/files via `fs` or the inline diff/range "
    "provided in the prompt/packet. Do not approve from packet prose alone when "
    "a commit range is named."
)

_LADDER_FREEform_REVIEW_RE = re.compile(
    r"(?im)^(?:policy_route:\s*freeform-ladder|"
    r"PRE-LAND REVIEW|pre-land review|Leg\s*4\s*[-—].*review)\b"
)

_VERDICT_LINE_RE = re.compile(r"(?m)^(VERDICT:[^\n]*)$")
_COVERAGE_REF_LINE_RE = re.compile(r"(?m)^(coverage_ref:[^\n]*)$")


def packet_is_freeform_ladder_review(packet_text: str) -> bool:
    """True when a freeform packet commissions ladder Leg-4 class review coverage."""
    return bool(_LADDER_FREEform_REVIEW_RE.search(packet_text or ""))


@dataclass(frozen=True)
class CoverageTargetRow:
    target: str
    pin: str
    required_extent: str
    actual_reader: str
    actual_extent: ActualExtent
    receipt_uri: str = ""
    receipt_digest: str = ""
    file_length: int | None = None
    tool_trace_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        if out.get("tool_trace_ref") is None:
            out.pop("tool_trace_ref", None)
        if out.get("file_length") is None:
            out.pop("file_length", None)
        return out


@dataclass
class CoverageManifest:
    review_id: str
    policy_route: str
    reviewed_pin: str
    verdict_line: str
    scope: list[str]
    targets: list[CoverageTargetRow] = field(default_factory=list)
    coverage_sidecar_uri: str | None = None
    coverage_sidecar_digest: str | None = None
    measurement_owner: str | None = None
    blocking_findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "policy_route": self.policy_route,
            "reviewed_pin": self.reviewed_pin,
            "verdict_line": self.verdict_line,
            "scope": list(self.scope),
            "targets": [row.to_dict() for row in self.targets],
            "coverage_sidecar_uri": self.coverage_sidecar_uri,
            "coverage_sidecar_digest": self.coverage_sidecar_digest,
            "measurement_owner": self.measurement_owner,
            "blocking_findings": list(self.blocking_findings),
        }

    def canonical_json_bytes(self) -> bytes:
        payload = self.to_dict()
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def sidecar_digest(self) -> str:
        return hashlib.sha256(self.canonical_json_bytes()).hexdigest()

    def compact_coverage_ref(self) -> str:
        if self.coverage_sidecar_uri and self.coverage_sidecar_digest:
            return (
                f"coverage_ref: {self.coverage_sidecar_uri}"
                f"#sha256={self.coverage_sidecar_digest}"
            )
        digest = self.sidecar_digest()
        return f"coverage_ref: inline#sha256={digest}"


@dataclass(frozen=True)
class ReviewRouteResolution:
    policy_route: str
    supported: bool
    unsupported_reason: str | None = None

    def appendix_text(self) -> str | None:
        if not self.supported:
            assert self.unsupported_reason
            return f"{UNSUPPORTED_ROUTE_PREFIX} {self.unsupported_reason}"
        return (
            f"{REVIEW_VERDICT_CONTRACT}\n\n"
            f"{REVIEW_COVERAGE_CONTRACT}\n\n"
            f"{REVIEW_GIT_INSPECTION_GUIDANCE}"
        )


def resolve_review_route(
    resolved_contract: str | None,
    *,
    packet_text: str = "",
) -> ReviewRouteResolution | None:
    """Map contract + packet shape to a review policy route, or None if not review."""
    contract = (resolved_contract or "").strip().lower()
    if contract in REVIEW_CONTRACTS:
        return ReviewRouteResolution(policy_route=contract, supported=True)
    if contract == "freeform" and packet_is_freeform_ladder_review(packet_text):
        return ReviewRouteResolution(
            policy_route=FREEFORM_LADDER_REVIEW_ROUTE,
            supported=True,
        )
    if contract == "freeform" and re.search(r"(?im)\bcode[- ]review\b|\breview\b", packet_text or ""):
        return ReviewRouteResolution(
            policy_route="freeform",
            supported=False,
            unsupported_reason=(
                "freeform review without ladder markers; "
                "set policy_route: freeform-ladder or use code-review"
            ),
        )
    return None


def review_dispatch_system_appendix(
    resolved_contract: str | None,
    *,
    packet_text: str = "",
) -> str | None:
    """Return verdict, coverage, and git-inspection appendix for supported review routes."""
    resolution = resolve_review_route(resolved_contract, packet_text=packet_text)
    if resolution is None:
        return None
    return resolution.appendix_text()


def freeform_ladder_review_coverage_appendix() -> str:
    """Coverage appendix for ladder Leg-4 freeform review dispatches."""
    resolution = ReviewRouteResolution(
        policy_route=FREEFORM_LADDER_REVIEW_ROUTE,
        supported=True,
    )
    text = resolution.appendix_text()
    assert text is not None
    return text


def manifest_from_dict(payload: dict[str, Any]) -> CoverageManifest:
    rows = [
        CoverageTargetRow(
            target=str(row["target"]),
            pin=str(row["pin"]),
            required_extent=str(row["required_extent"]),
            actual_reader=str(row["actual_reader"]),
            actual_extent=row["actual_extent"],  # type: ignore[arg-type]
            receipt_uri=str(row.get("receipt_uri") or ""),
            receipt_digest=str(row.get("receipt_digest") or ""),
            file_length=row.get("file_length"),
            tool_trace_ref=row.get("tool_trace_ref"),
        )
        for row in payload.get("targets") or []
    ]
    return CoverageManifest(
        review_id=str(payload["review_id"]),
        policy_route=str(payload["policy_route"]),
        reviewed_pin=str(payload["reviewed_pin"]),
        verdict_line=str(payload["verdict_line"]),
        scope=[str(item) for item in payload.get("scope") or []],
        targets=rows,
        coverage_sidecar_uri=payload.get("coverage_sidecar_uri"),
        coverage_sidecar_digest=payload.get("coverage_sidecar_digest"),
        measurement_owner=payload.get("measurement_owner"),
        blocking_findings=[
            str(item) for item in payload.get("blocking_findings") or []
        ],
    )


def parse_coverage_ref_line(line: str) -> tuple[str | None, str | None]:
    """Parse ``coverage_ref: <uri>#sha256=<hex>`` or ``inline#sha256=``."""
    prefix = "coverage_ref:"
    if not line.startswith(prefix):
        return None, None
    body = line[len(prefix) :].strip()
    if "#sha256=" in body:
        uri, digest = body.split("#sha256=", 1)
        uri = uri.strip() or None
        return uri, digest.strip() or None
    if body.startswith("inline#sha256="):
        return None, body.split("=", 1)[1].strip()
    return body or None, None


def validate_extent_authorized(
    *,
    actual_extent: ActualExtent,
    required_extent: str,
    pin: str,
    reviewed_pin: str,
    diff_authorized: bool,
) -> bool:
    """Return False for unauthorized diff or pin mismatch on allowed_diff."""
    if pin != reviewed_pin and actual_extent == "allowed_diff":
        return False
    if actual_extent == "allowed_diff" and not diff_authorized:
        return False
    if actual_extent not in ACTUAL_EXTENTS:
        return False
    if required_extent == "whole" and actual_extent not in {
        "whole",
        "partial",
        "unread",
        "unverified",
    }:
        return False
    return True


@dataclass(frozen=True)
class PositiveGateVerdict:
    passed: bool
    reason: str
    legacy_unverified: bool = False


def evaluate_positive_whole_file_gate(
    row: CoverageTargetRow,
    *,
    reviewing_worker_id: str,
    server_file_sha256: str | None,
    receipt_bytes: bytes | None,
    export_byte_length: int | None,
    file_length: int | None,
) -> PositiveGateVerdict:
    """Positive whole-file credit — hash alone is insufficient."""
    if row.actual_extent != "whole":
        return PositiveGateVerdict(False, "actual_extent_not_whole")
    if not server_file_sha256:
        return PositiveGateVerdict(False, "missing_server_file_sha256")
    if not row.receipt_digest:
        return PositiveGateVerdict(False, "missing_receipt_digest")
    if row.actual_reader != reviewing_worker_id:
        return PositiveGateVerdict(False, "wrong_actual_reader")
    if not receipt_bytes:
        return PositiveGateVerdict(False, "missing_receipt_bytes")
    digest = hashlib.sha256(receipt_bytes).hexdigest()
    if digest != row.receipt_digest:
        return PositiveGateVerdict(False, "receipt_digest_mismatch")
    effective_length = file_length if file_length is not None else row.file_length
    if effective_length is not None and export_byte_length is not None:
        if export_byte_length < effective_length:
            return PositiveGateVerdict(False, "truncated_export")
    return PositiveGateVerdict(True, "ok")


def evaluate_envelope_receipt_authority(
    *,
    manifest: CoverageManifest,
    coordinator_restatement_digest: str | None,
) -> PositiveGateVerdict:
    """Coordinator restatement without resolvable worker receipt_digest fails."""
    for row in manifest.targets:
        if row.receipt_digest and coordinator_restatement_digest == row.receipt_digest:
            if row.actual_reader and not row.receipt_uri:
                continue
        if not row.receipt_digest and row.actual_extent == "whole":
            return PositiveGateVerdict(False, "missing_worker_receipt_digest")
    if coordinator_restatement_digest and not any(
        row.receipt_digest == coordinator_restatement_digest for row in manifest.targets
    ):
        return PositiveGateVerdict(False, "coordinator_restatement_not_authority")
    return PositiveGateVerdict(True, "ok")


def legacy_verdict_without_coverage(verdict_line: str | None) -> PositiveGateVerdict:
    """Legacy verdicts stay readable but do not pass the new positive gate."""
    if verdict_line and verdict_line.startswith("VERDICT:"):
        return PositiveGateVerdict(
            False,
            "legacy_verdict_no_coverage",
            legacy_unverified=True,
        )
    return PositiveGateVerdict(False, "no_verdict")


def blocking_survives_incomplete_coverage(
    manifest: CoverageManifest,
) -> tuple[bool, str | None]:
    """Blocking findings still block when coverage proof is incomplete."""
    if not manifest.blocking_findings:
        return False, None
    incomplete = any(
        row.actual_extent in {"partial", "unread", "unverified"}
        or not row.receipt_digest
        for row in manifest.targets
    )
    if incomplete:
        owner = manifest.measurement_owner or "unassigned"
        return True, owner
    return bool(manifest.blocking_findings), manifest.measurement_owner


def unfenced_verdict_lines(text: str) -> list[str]:
    """Return unfenced column-0 VERDICT lines (for harvest promotion checks)."""
    spans: list[tuple[int, int]] = []
    marks = [m.start() for m in re.finditer(r"(?m)^```", text)]
    for index in range(0, len(marks), 2):
        end = marks[index + 1] if index + 1 < len(marks) else len(text)
        spans.append((marks[index], end))

    hits: list[str] = []
    for match in _VERDICT_LINE_RE.finditer(text):
        if any(start <= match.start() < end for start, end in spans):
            continue
        hits.append(match.group(1))
    return hits


def unfenced_coverage_ref_lines(text: str) -> list[str]:
    spans: list[tuple[int, int]] = []
    marks = [m.start() for m in re.finditer(r"(?m)^```", text)]
    for index in range(0, len(marks), 2):
        end = marks[index + 1] if index + 1 < len(marks) else len(text)
        spans.append((marks[index], end))
    hits: list[str] = []
    for match in _COVERAGE_REF_LINE_RE.finditer(text):
        if any(start <= match.start() < end for start, end in spans):
            continue
        hits.append(match.group(1))
    return hits


def harvest_promoted_verdict_with_coverage(
    content: str,
) -> tuple[str | None, list[str]]:
    """Select the single promoted VERDICT and any coverage_ref lines."""
    verdicts = unfenced_verdict_lines(content)
    coverage_refs = unfenced_coverage_ref_lines(content)
    verdict = verdicts[0] if len(verdicts) == 1 else None
    if len(verdicts) > 1:
        verdict = None
    return verdict, coverage_refs


def compact_envelope_coverage_ref(manifest: CoverageManifest) -> str:
    """Line-start ref consumers can resolve without full conversation."""
    return manifest.compact_coverage_ref()


def event_vocabulary_row() -> dict[str, str]:
    """Catalog confirm row for closeout / test fixture."""
    return {
        "signal": REVIEW_COVERAGE_GATE_EVENT,
        "action": REVIEW_COVERAGE_GATE_EVENT_ACTION,
    }
