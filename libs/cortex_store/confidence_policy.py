"""Confidence-derivation policy v2 — knobs, lookups, eligibility, provenance keys.

Pure-policy layer for the belief-graph confidence derivation
(``decision:cortex-confidence-derivation-policy-v1`` decision arc;
``derivation_policy_version = confidence-derivation/v2``). The v2 additions —
internal-provenance Ψ (``PSI_DERIVATION``, §3b) and computed external host
credibility (``host_credibility``, §3c) — are specified in
``cortex:notes/system/specs/cortex-confidence-derivation-policy-v2.md``. No DB,
no numerics —
just the versioned tables and predicates the derivation (``confidence_derivation``)
and snapshot loader (``confidence_snapshot``) consume.

Grounding (corpus, thread 1180 turn 6):
  * Φ/Ψ separation, signed propagation operator, the ``sgn: T → {−1,0,+1}`` map,
    and the ``b = λΨ + (1−λ)c`` prior shape come from
    ``belief-graphs-reasoning-zones.pdf`` (Nikooroo & Engel).
  * ``§17`` deviations from the paper (fixed Ψ denominator = 1.0; cluster-MAX
    prior) are deliberate — see the policy spec.
  * The provenance CLUSTER key (B1) is grounded in PROV: a cluster is a
    provenance-lineage class. v1 proxy = normalized evidence-URI host, collapsed
    by ``seeded_by`` agent (``prov:wasAttributedTo``). Documented approximation
    pending real ``wasDerivedFrom``/``hadPrimarySource`` lineage (Phase 2).

Everything here is calibration-provisional (policy §18): changing a knob is a
recompute/backfill event under a bumped ``derivation_policy_version``, NOT a
schema migration.
"""

from __future__ import annotations

from urllib.parse import urlparse

DERIVATION_POLICY_VERSION = "confidence-derivation/v2"

# §3 — Source credibility Ψ. Fixed source-level trust table; denominator pinned
# at 1.0 (NO ‖Ψ‖∞ running-max normalization — §17 deviation). NULL/unknown
# credibility resolves to ``unrated`` explicitly (never participates as NULL).
PSI_TABLE: dict[str, float] = {
    "authority": 1.00,
    "external-KB": 0.85,
    "recorded-history": 0.60,
    "unrated": 0.30,
}
UNRATED = "unrated"
AUTHORITY = "authority"
# §3b (v2) — internal-provenance credibility Ψ by derivation type. The external
# PSI_TABLE is source-AUTHORITY-only and floors operator-asserted facts at unrated;
# this dimension credits high-trust INTERNAL provenance for a single-operator KG.
# ``user_statement``/``direct_observation`` are the operator's own ground truth →
# internal-authority; ``agent_observation`` (tool/runtime output) is high but not
# operator-asserted → external-KB-equivalent. Other derivation types have no slot
# here (resolve to NULL/external path). v2 calibration-provisional (§18).
INTERNAL_AUTHORITY = "internal-authority"
PSI_DERIVATION: dict[str, tuple[str, float]] = {
    "user_statement": (INTERNAL_AUTHORITY, 1.00),
    "direct_observation": (INTERNAL_AUTHORITY, 1.00),
    "agent_observation": ("external-KB", 0.85),
}
# Provenance bands that satisfy the confirmed-evidence gate's "≥ external-KB"
# floor (§12). ``authority`` and ``internal-authority`` additionally short-circuit
# the ≥2-cluster rule — a single operator/authority cluster confirms (requiring
# two independent operators is wrong for a single-operator KG).
EXTERNAL_KB_OR_BETTER: frozenset[str] = frozenset({"authority", "external-KB"})
GATE_AUTHORITY_BANDS: frozenset[str] = frozenset({AUTHORITY, INTERNAL_AUTHORITY})
GATE_QUALIFYING_BANDS: frozenset[str] = EXTERNAL_KB_OR_BETTER | {INTERNAL_AUTHORITY}

# §3c (v2, option b) — external-host credibility, COMPUTED at derivation time from
# the citation host (NOT a stored backfill — no drift, recompute-not-migrate §18).
# Rule + manual-list, both opt-in: an unlisted host resolves to ``unrated`` (0.30,
# the safe floor). ``*.gov`` is structurally authority (zero maintenance for new
# gov hosts); everything else credible is a small hand-maintained list. Consulted
# only when the assertion's stored ``credibility`` is NULL.
HOST_CREDIBILITY: dict[str, str] = {
    "finra.org": "authority",  # SRO / regulator
    "apidocs.lighter.xyz": "external-KB",
    "docs.anthropic.com": "external-KB",
    "cursor.com": "external-KB",
    "x.ai": "external-KB",
    "x.com": "recorded-history",
}

# §4 — Assertion-confidence prior weights (legal-evidentiary enum, kept separate
# from Ψ). Unknown confidence resolves to 0.0 (contributes no prior mass).
CONFIDENCE_WEIGHT: dict[str, float] = {
    "confirmed": 1.00,
    "believed": 0.70,
    "suspected": 0.40,
    "hypothesized": 0.20,
}
CONFIRMED = "confirmed"

# §5/§6/§11/§13 — calibration-provisional knobs (shadow defaults).
LAMBDA = 0.5  # prior mix: p = λΨ + (1−λ)c
ETA = 1.0  # contradiction penalty in M = A⁺ − ηA⁻
ALPHA_BASE = 0.5  # damping mixing parameter
ALPHA_CONTRACTION_MARGIN = 0.95  # α = min(α_base, margin/‖M‖₂)
TAU_CONFIRM = 0.70  # corpus default θ
TAU_PROVISIONAL = 0.40
CONTRADICTION_CAP_THRESHOLD = 0.40  # band capped at provisional if η(A⁻Φ*) ≥ this
CONVERGENCE_TOL = 1e-10  # ‖Φ_{t+1} − Φ_t‖∞ stop condition
MAX_ITERS = 2000

# §7 — sign map over epistemic edge TYPES. The operator consumes sgn(τ), never
# type names: an absent type contributes zero rows and folds in for free if rows
# later land (corpus: types with sgn=0 are ignored by propagation). ``corroborates``
# is registered at +1 with zero live rows in v1 (B2).
SIGN_MAP: dict[str, int] = {
    "evidence_for": +1,
    "corroborates": +1,
    "contradicts": -1,
}
POSITIVE_EDGE_TYPES: frozenset[str] = frozenset(t for t, s in SIGN_MAP.items() if s > 0)
NEGATIVE_EDGE_TYPES: frozenset[str] = frozenset(t for t, s in SIGN_MAP.items() if s < 0)
ALL_SIGNED_EDGE_TYPES: tuple[str, ...] = tuple(sorted(SIGN_MAP))

# §2 — eligibility filter. An assertion is eligible iff not superseded and not in
# a non-committed review state (NULL review_status counts as committed).
INELIGIBLE_REVIEW_STATUSES: frozenset[str] = frozenset(
    {"flagged", "staged", "rejected"}
)

# §0 / N4 / N5 — entity scope for the shadow write+diff. confidence_band/score is
# written + diffed only for types whose confidence axis is ``status`` and that do
# not use status for adoption semantics (``decision``). Φ* is still computed over
# the global graph; only the write/diff is scoped.
CONFIDENCE_FIELD_STATUS = "status"  # legacy registry value (pre-cutover types)
CONFIDENCE_FIELD_BAND = "confidence_band"
ADOPTION_STATUS_TYPES: frozenset[str] = frozenset({"decision"})

# §16 — legacy status → expected confidence band (the D-core legacy-derived
# baseline, NOT ground truth). Lifecycle/adoption-valued statuses are out of
# scope and excluded from the confusion matrix.
LEGACY_STATUS_TO_BAND: dict[str, str] = {
    "unsubstantiated": "unsubstantiated",
    "provisional": "provisional",
    "confirmed": "confirmed",
}
BAND_RANK: dict[str, int] = {
    "unsubstantiated": 0,
    "provisional": 1,
    "confirmed": 2,
}


def psi_for(credibility: str | None) -> tuple[str, float]:
    """Resolve an assertion ``credibility`` value to a (band, Ψ) pair.

    NULL / unknown / unparseable values resolve to ``unrated`` (0.30) per §3 —
    the caller emits an audit reason for the fallback so the resolution is never
    silent.
    """
    if credibility is None:
        return UNRATED, PSI_TABLE[UNRATED]
    band = credibility.strip()
    if band in PSI_TABLE:
        return band, PSI_TABLE[band]
    return UNRATED, PSI_TABLE[UNRATED]


def psi_for_derivation(derivation_type: str | None) -> tuple[str, float] | None:
    """Resolve a derivation type to an internal-provenance (band, Ψ) — §3b (v2).

    Returns ``None`` for derivation types with no internal-trust slot (they take
    the external source-authority path only). The distinction is what makes a
    no-evidence-URI operator assertion source-citing: an internal-trust type
    self-sources, anything else still contributes 0 to the prior (§8).
    """
    if derivation_type is None:
        return None
    return PSI_DERIVATION.get(derivation_type.strip())


def host_credibility(source_key: str | None) -> str | None:
    """Credibility band for an external citation host (§3c v2, option b).

    ``*.gov`` ⇒ authority (structural, zero-maintenance); else the hand-maintained
    HOST_CREDIBILITY list; else ``None`` (unlisted ⇒ caller falls back to unrated,
    the safe floor). Internal cluster keys (``cortex:…``, ``internal:…``) match
    neither rule and resolve to ``None``.
    """
    if not source_key:
        return None
    key = source_key.strip().lower()
    if key == "gov" or key.endswith(".gov"):
        return AUTHORITY
    return HOST_CREDIBILITY.get(key)


def credibility_for_keys(source_keys: tuple[str, ...]) -> str | None:
    """Highest-Ψ host-derived credibility band over an assertion's cluster keys."""
    best_band: str | None = None
    best_psi = -1.0
    for key in source_keys:
        band = host_credibility(key)
        if band is None:
            continue
        psi = PSI_TABLE.get(band, 0.0)
        if psi > best_psi:
            best_band, best_psi = band, psi
    return best_band


def effective_psi(
    credibility: str | None, derivation_type: str | None
) -> tuple[str, float]:
    """Higher-Ψ of the external source-authority band and the internal-provenance
    band (§3b v2). Internal trust provides a FLOOR — it never dilutes a strong
    external source. NULL credibility + non-internal type ⇒ unrated (0.30)."""
    source_band, source_psi = psi_for(credibility)
    internal = psi_for_derivation(derivation_type)
    if internal is None or internal[1] <= source_psi:
        return source_band, source_psi
    return internal


def confidence_weight(confidence: str | None) -> float:
    """Assertion-confidence prior weight c (§4); unknown → 0.0 (no mass)."""
    if confidence is None:
        return 0.0
    return CONFIDENCE_WEIGHT.get(confidence.strip(), 0.0)


def is_eligible_review_status(review_status: str | None) -> bool:
    """§2 eligibility on review state — NULL counts as committed."""
    if review_status is None:
        return True
    return review_status not in INELIGIBLE_REVIEW_STATUSES


def in_write_scope(confidence_field: str, entity_type: str) -> bool:
    """N4/N5 — entity is in the shadow write+diff scope.

    Confidence-axis on ``status`` / ``confidence_band`` AND not adoption types.
    """
    return (
        confidence_field in (CONFIDENCE_FIELD_STATUS, CONFIDENCE_FIELD_BAND)
        and entity_type not in ADOPTION_STATUS_TYPES
    )


def normalized_source_key(evidence_uri: str) -> str:
    """Normalize an evidence URI to a provenance-cluster key (B1 v1 proxy).

    PROV-faithful intent: the key should identify a primary source / lineage
    root. Lacking real lineage on assertions, v1 uses:
      * network host (lowercased, leading ``www.`` stripped) for http(s) URIs;
      * ``scheme:first-path-segment`` for scheme-only internal URIs
        (e.g. ``agent-bus:1180``, ``cortex:notes``);
      * the raw value otherwise.
    Documented approximation — a true provenance lineage replaces this in Phase 2.
    """
    raw = evidence_uri.strip()
    if not raw:
        return raw
    parsed = urlparse(raw)
    if parsed.netloc:
        host = parsed.netloc.lower()
        return host[4:] if host.startswith("www.") else host
    if parsed.scheme:
        first_segment = parsed.path.lstrip("/").split("/", 1)[0]
        return f"{parsed.scheme}:{first_segment}" if first_segment else parsed.scheme
    return raw


class _UnionFind:
    """Minimal union-find for collapsing provenance clusters by shared agent.

    Two distinct source hosts cited by the SAME ``seeded_by`` agent are not
    independent (PROV ``wasAttributedTo``): they merge into one independence
    group for the §12 ≥2-cluster gate.
    """

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, key: str) -> str:
        self._parent.setdefault(key, key)
        root = key
        while self._parent[root] != root:
            root = self._parent[root]
        # Path-halving for stability without recursion.
        while self._parent[key] != root:
            self._parent[key], key = root, self._parent[key]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Deterministic merge: lexicographically smaller key wins as root.
            lo, hi = (ra, rb) if ra < rb else (rb, ra)
            self._parent[hi] = lo


def independent_cluster_count(
    clusters: list[tuple[str, str | None]],
) -> int:
    """Count independent provenance clusters from (source_key, seeded_by) pairs.

    Distinct ``source_key`` values are distinct clusters, EXCEPT clusters sharing
    a non-null ``seeded_by`` agent collapse into one (same author ≠ independent).
    Empty input → 0.
    """
    if not clusters:
        return 0
    uf = _UnionFind()
    keys = {key for key, _ in clusters}
    for key in keys:
        uf.find(key)
    # Merge all source keys sharing a non-null agent.
    by_agent: dict[str, list[str]] = {}
    for key, agent in clusters:
        if agent:
            by_agent.setdefault(agent, []).append(key)
    for shared_keys in by_agent.values():
        first = shared_keys[0]
        for other in shared_keys[1:]:
            uf.union(first, other)
    return len({uf.find(key) for key in keys})


__all__ = [
    "ADOPTION_STATUS_TYPES",
    "ALL_SIGNED_EDGE_TYPES",
    "ALPHA_BASE",
    "ALPHA_CONTRACTION_MARGIN",
    "AUTHORITY",
    "BAND_RANK",
    "CONFIDENCE_FIELD_BAND",
    "CONFIDENCE_FIELD_STATUS",
    "CONFIDENCE_WEIGHT",
    "CONFIRMED",
    "CONTRADICTION_CAP_THRESHOLD",
    "CONVERGENCE_TOL",
    "DERIVATION_POLICY_VERSION",
    "ETA",
    "EXTERNAL_KB_OR_BETTER",
    "GATE_AUTHORITY_BANDS",
    "GATE_QUALIFYING_BANDS",
    "HOST_CREDIBILITY",
    "INTERNAL_AUTHORITY",
    "LAMBDA",
    "LEGACY_STATUS_TO_BAND",
    "MAX_ITERS",
    "NEGATIVE_EDGE_TYPES",
    "POSITIVE_EDGE_TYPES",
    "PSI_DERIVATION",
    "PSI_TABLE",
    "SIGN_MAP",
    "TAU_CONFIRM",
    "TAU_PROVISIONAL",
    "UNRATED",
    "confidence_weight",
    "credibility_for_keys",
    "effective_psi",
    "host_credibility",
    "in_write_scope",
    "independent_cluster_count",
    "is_eligible_review_status",
    "normalized_source_key",
    "psi_for",
    "psi_for_derivation",
]
