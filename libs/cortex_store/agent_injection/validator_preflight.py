"""D.5 invariant validator for INJECTION PACKETS (not assertion writes).

A packet is a list of block dicts as returned by materialize_d{1,2,3,4}.
Validates the five D.5 invariants on the packet shape. Raises
AgentInjectionAdmissionError on any violation. No silent degradation.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .errors import AgentInjectionAdmissionError, ViolationDetail
from .materializers import compute_d2_content_hash


@dataclass
class ValidationResult:
    ok: bool
    packet_size_bytes: int
    block_count: int


def preflight_validate(injection_packet: list[dict]) -> ValidationResult:
    """Validate D.5 invariants 1-5 over an injection packet (list of materialized blocks).

    Parameter name `injection_packet` is load-bearing (distinguishes from assertion-write validators).
    """
    if not isinstance(injection_packet, list):
        raise AgentInjectionAdmissionError(
            "preflight_validate requires a list of blocks",
            violations=[ViolationDetail(invariant=0, detail="not_a_list")],
        )

    total_bytes = sum(len(str(b.get("rendered", ""))) for b in injection_packet)
    max_bytes = int(os.environ.get("CORTEX_INJECTION_PACKET_MAX_BYTES", "65536"))
    if total_bytes > max_bytes:
        raise AgentInjectionAdmissionError(
            f"D.5 invariant 4: packet exceeds {max_bytes} bytes",
            violations=[
                ViolationDetail(invariant=4, detail=f"packet_too_large:{total_bytes}")
            ],
        )

    for i, block in enumerate(injection_packet):
        rendered = str(block.get("rendered", ""))
        kind = block.get("kind")

        # 1. Evidence envelope first
        if not any(
            rendered.startswith(tok)
            for tok in (
                "[STRUCTURED_LOOKUP",
                "[CONTEXT_PROVISION",
                "[TEMPORAL_QUALIFIED",
                "[BELIEF_INJECTION",
            )
        ):
            raise AgentInjectionAdmissionError(
                "D.5 invariant 1: evidence envelope not first",
                violations=[
                    ViolationDetail(
                        invariant=1, block_index=i, detail="envelope_not_first"
                    )
                ],
            )

        # 2. Citation anchor mandatory: D.1/D.3/D.4 carry block["assertion_id"]; D.2 carries per-row assertion_id= in body
        has_anchor = (
            block.get("assertion_id") is not None
            or re.search(r"assertion_id[:= ]\s*\d+", rendered)
            or re.search(r"source: assertion \d+", rendered)
        )
        if not has_anchor:
            raise AgentInjectionAdmissionError(
                "D.5 invariant 2: missing citation anchor",
                violations=[
                    ViolationDetail(
                        invariant=2, block_index=i, detail="missing_citation_anchor"
                    )
                ],
            )

        # 3. No prose laundering
        lines = rendered.splitlines()
        for ln in lines:
            s = ln.strip()
            if not s:
                continue
            if s.startswith(("[", "|", "]", "#")):
                continue
            if s.startswith(
                ("Field:", "Value:", "Claim:", "Reasoning:", "assertion_id=")
            ):
                continue
            # markdown header or English sentence heuristic
            if s.startswith(("#", "##")) or (
                s[0].isupper()
                and s.endswith(".")
                and "Field:" not in s
                and "Value:" not in s
            ):
                raise AgentInjectionAdmissionError(
                    "D.5 invariant 3: prose laundering or header detected",
                    violations=[
                        ViolationDetail(
                            invariant=3,
                            block_index=i,
                            detail="prose_sentence_or_header",
                        )
                    ],
                )

        # 4. Admission-gated truncation (per-D.2 checks + aggregate already done)
        if kind == "d2":
            if block.get("truncated"):
                if (
                    block.get("cursor") is None
                    or block.get("selection_strategy") == "all"
                ):
                    raise AgentInjectionAdmissionError(
                        "D.5 invariant 4: truncated D.2 block missing cursor or used default strategy",
                        violations=[
                            ViolationDetail(
                                invariant=4,
                                block_index=i,
                                detail="truncation_violation",
                            )
                        ],
                    )

        # 5. Content-hash integrity for D.2
        if kind == "d2":
            ch = block.get("content_hash", "")
            if not re.match(r"^sha256:[0-9a-f]{64}$", ch):
                raise AgentInjectionAdmissionError(
                    "D.5 invariant 5: malformed content_hash",
                    violations=[
                        ViolationDetail(
                            invariant=5, block_index=i, detail="hash_malformed"
                        )
                    ],
                )
            # recompute after stripping the hash line
            body_lines = rendered.splitlines()
            no_hash = [
                ln for ln in body_lines if not re.match(r"^\s*\| content_hash:", ln)
            ]
            body_wo = "\n".join(no_hash)
            recomputed = compute_d2_content_hash(body_wo)
            if recomputed != ch:
                raise AgentInjectionAdmissionError(
                    "D.5 invariant 5: content_hash mismatch",
                    violations=[
                        ViolationDetail(
                            invariant=5, block_index=i, detail="hash_mismatch"
                        )
                    ],
                )

    return ValidationResult(
        ok=True, packet_size_bytes=total_bytes, block_count=len(injection_packet)
    )
