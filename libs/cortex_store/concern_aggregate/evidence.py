"""Verified evidence records for effect resend, failure, and reconcile.

Does not import ``effects`` (no cycle). The ledger treats ``digest`` as opaque.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .clock import require_aware_utc

EVIDENCE_KINDS = frozenset(
    {"resource_dedup", "confirmed_not_accepted", "not_applied", "applied"}
)
_NON_APPLICATION = frozenset({"confirmed_not_accepted", "not_applied"})
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class EffectEvidence:
    """One observation a provider can verify against an effect key.

    ``superseded_request_id`` is required for ``confirmed_not_accepted`` and
    ``not_applied``: the provider attests that request will not be accepted later.
    """

    kind: str
    provider: str
    effect_key: str
    ref_uri: str
    observed_at: datetime
    digest: str
    superseded_request_id: str = ""

    def validate_shape(self) -> None:
        """Raise ``ValueError`` when the record is not ledger-shaped."""
        if self.kind not in EVIDENCE_KINDS:
            raise ValueError(f"unknown evidence kind: {self.kind!r}")
        for name in ("provider", "effect_key", "ref_uri"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must be non-empty")
        require_aware_utc(self.observed_at, name="observed_at")
        if _HEX64.fullmatch(self.digest) is None:
            raise ValueError("digest must be 64 lowercase hex characters")
        if (
            self.kind in _NON_APPLICATION
            and not str(self.superseded_request_id).strip()
        ):
            raise ValueError(
                "superseded_request_id is required for non-application evidence"
            )

    def to_json(self) -> dict[str, str]:
        """Render the record with the same whole-second UTC ISO form as the ledger."""
        observed = require_aware_utc(self.observed_at, name="observed_at").replace(
            microsecond=0
        )
        return {
            "kind": self.kind,
            "provider": self.provider,
            "effect_key": self.effect_key,
            "ref_uri": self.ref_uri,
            "observed_at": observed.isoformat(),
            "digest": self.digest,
            "superseded_request_id": self.superseded_request_id,
        }


class EvidenceProvider(Protocol):
    """Verifier for ``EffectEvidence``.

    ``name`` must equal ``evidence.provider``. For ``confirmed_not_accepted`` and
    ``not_applied``, a True result attests that the accepting resource has refused
    or fenced ``superseded_request_id`` and will not accept it later. A
    point-in-time observation of absence is not sufficient.
    """

    name: str

    def verify(self, evidence: EffectEvidence) -> bool:
        """Return True only when *evidence* is verified for this provider."""
