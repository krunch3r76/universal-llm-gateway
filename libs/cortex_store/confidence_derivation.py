"""Belief-graph confidence derivation — Phase 1, shadow-only (§5–§14).

Implements ``decision:cortex-confidence-derivation-policy-v1`` at
``derivation_policy_version = confidence-derivation/v2`` (internal-provenance Ψ
+ computed host credibility; see confidence-derivation-policy-v2.md). Computes a graded
``confidence_score`` = Φ* (the unique fixed point of the signed, damped
propagation operator) and a ``confidence_band`` for every entity, WITHOUT
flipping ``entities.status`` — Phase 1 is shadow-only (B3, thread 1180). The
per-write legacy-status hook (``substantiation_sync``) is untouched; this batch
writes only the migration-050 trait columns.

Pipeline:
  1. ``confidence_snapshot.load_snapshot`` — eligible graph slice.
  2. ``_compute_priors``  — cluster-MAX prior b (§5), kills duplicate inflation.
  3. ``_build_operator`` / ``_fixed_point`` — Φ* over the edge-incident subgraph
     (§6, §8 self-loops, §9 Banach fixed point); prior-only / isolated entities
     resolve to Φ*=b analytically.
  4. ``_band_for`` — raw bands (§11), confirmed-evidence gate (§12), contradiction
     cap (§13), final band (§14).

numpy / scipy are imported here (offline batch path) — NOT at the cortex_store
package init, so the per-write path and service startup are unaffected.
"""

from __future__ import annotations

import datetime
import sqlite3
from dataclasses import dataclass, field

import numpy as np
from scipy.sparse import csr_matrix, lil_matrix
from scipy.sparse.linalg import svds
from universal_logging import get_logger

from . import confidence_policy as pol
from .confidence_snapshot import GraphSnapshot, SourceAssertion, load_snapshot
from .db import execute, query

logger = get_logger("cortex-api.confidence_derivation")


@dataclass(frozen=True)
class EntityConfidence:
    """Per-entity derivation result + auditor explanation packet (§15)."""

    entity_id: str
    entity_type: str
    in_scope: bool
    score: float
    raw_band: str
    final_band: str
    b_prior: float
    prior_source_keys: tuple[str, ...]
    gate_pass: bool
    gate_reason: str
    contradiction_cap: bool
    cap_value: float
    zero_edge: bool
    has_source_citing: bool
    stored_confidence_band: str | None


@dataclass
class DerivationRun:
    results: dict[str, EntityConfidence]
    policy_version: str = pol.DERIVATION_POLICY_VERSION
    alpha_base: float = pol.ALPHA_BASE
    alpha_effective: float = pol.ALPHA_BASE
    m_norm: float = 0.0
    iterations: int = 0
    residual: float = 0.0
    matrix_node_count: int = 0
    edge_count: int = 0
    prior_only_count: int = 0
    null_credibility_count: int = 0
    generated_at: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Prior:
    b: float
    source_keys: tuple[str, ...]
    has_source_citing: bool


def _compute_priors(
    assertions: list[SourceAssertion],
) -> dict[str, _Prior]:
    """§5 cluster-MAX prior b_i = max_k (λ·Ψ_k + (1−λ)·c_k) over provenance clusters.

    Each source-citing assertion joins every one of its host clusters; per cluster
    Ψ_k / c_k are the cluster maxima. b_i is the MAX over clusters — duplicate
    copies cannot inflate the prior; corroboration enters only via §7 edges.
    """
    by_entity: dict[str, list[SourceAssertion]] = {}
    for a in assertions:
        by_entity.setdefault(a.entity_id, []).append(a)

    priors: dict[str, _Prior] = {}
    for entity_id, rows in by_entity.items():
        cluster_psi: dict[str, float] = {}
        cluster_c: dict[str, float] = {}
        for a in rows:
            for key in a.source_keys:
                cluster_psi[key] = max(cluster_psi.get(key, 0.0), a.psi)
                cluster_c[key] = max(cluster_c.get(key, 0.0), a.c)
        b = max(
            (
                pol.LAMBDA * cluster_psi[k] + (1.0 - pol.LAMBDA) * cluster_c[k]
                for k in cluster_psi
            ),
            default=0.0,
        )
        priors[entity_id] = _Prior(
            b=b,
            source_keys=tuple(sorted(cluster_psi)),
            has_source_citing=True,
        )
    return priors


def _gate(entity_assertions: list[SourceAssertion]) -> tuple[bool, str]:
    """§12 confirmed-evidence gate — propagated Φ* alone can NEVER confirm.

    Qualifying assertion: confidence=confirmed AND Ψ-band ∈ GATE_QUALIFYING_BANDS
    (external-KB+ OR internal-authority — source citing + eligible already hold
    for every snapshot row). Pass iff ≥1 authority/internal-authority cluster
    (single-cluster short-circuit) OR ≥2 independent external-KB+ clusters.

    D1 firewall (1172-C): assertions with ``derivation_type=inference`` are
    excluded from the qualifying set regardless of confidence or Ψ-band.  They
    are NOT auditor-validatable and cannot satisfy the gate for their own entity
    or contribute a backing cluster to any other claim.  Source_keys from D3
    filtering (external-URI-only) already exclude internal-provenance URIs from
    the ``independent_cluster_count`` input.
    """
    qualifying = [
        a
        for a in entity_assertions
        if a.confidence == pol.CONFIRMED
        and a.psi_band in pol.GATE_QUALIFYING_BANDS
        and a.derivation_type != pol.INFERENCE_DERIVATION
    ]
    if not qualifying:
        return False, "no_qualifying_confirmed_source_citing_evidence"
    if any(a.psi_band == pol.INTERNAL_AUTHORITY for a in qualifying):
        return True, "internal_authority_cluster"
    if any(a.psi_band == pol.AUTHORITY for a in qualifying):
        return True, "authority_cluster"
    pairs = [(key, a.seeded_by) for a in qualifying for key in a.source_keys]
    n_independent = pol.independent_cluster_count(pairs)
    if n_independent >= 2:
        return True, f"{n_independent}_independent_external_kb_clusters"
    return False, f"only_{n_independent}_independent_cluster"


def _build_operator(
    snapshot: GraphSnapshot, priors: dict[str, _Prior]
) -> tuple[list[str], csr_matrix, csr_matrix, np.ndarray]:
    """Build row-normalized A⁺, A⁻ and prior vector b over edge-incident nodes (§6–§8).

    Echo-collapse (§7): positive edges between same-provenance entities are
    dropped. Self-loops (§8): nodes with no incoming edge get A⁺[i,i]=1 so they
    resolve to Φ*=b_i.
    """
    node_ids = sorted({e.src for e in snapshot.edges} | {e.tgt for e in snapshot.edges})
    index = {nid: i for i, nid in enumerate(node_ids)}
    n = len(node_ids)
    a_plus = lil_matrix((n, n), dtype=np.float64)
    a_minus = lil_matrix((n, n), dtype=np.float64)
    for e in snapshot.edges:
        i, j = index[e.tgt], index[e.src]
        if e.sign > 0:
            sk_t = snapshot.entities[e.tgt].source_key
            sk_s = snapshot.entities[e.src].source_key
            if sk_t is not None and sk_t == sk_s:
                continue  # same-provenance positive echo — collapse (§7)
            a_plus[i, j] += e.weight
        else:
            a_minus[i, j] += e.weight

    plus_rowsum = np.asarray(a_plus.sum(axis=1)).ravel()
    minus_rowsum = np.asarray(a_minus.sum(axis=1)).ravel()
    for i in range(n):
        if plus_rowsum[i] == 0.0 and minus_rowsum[i] == 0.0:
            a_plus[i, i] = 1.0  # §8 deterministic self-loop ⇒ Φ*_i = b_i
            plus_rowsum[i] = 1.0

    a_plus = _row_normalize(a_plus.tocsr(), plus_rowsum)
    a_minus = _row_normalize(a_minus.tocsr(), minus_rowsum)
    b = np.array([priors[nid].b if nid in priors else 0.0 for nid in node_ids])
    return node_ids, a_plus, a_minus, b


def _row_normalize(mat: csr_matrix, rowsum: np.ndarray) -> csr_matrix:
    """Divide each row by max(1, rowsum) (paper eq 4); all-zero rows unchanged."""
    denom = np.maximum(1.0, rowsum)
    idx = np.arange(len(denom))
    scale = csr_matrix((1.0 / denom, (idx, idx)), shape=(len(denom), len(denom)))
    return scale @ mat


def _spectral_norm(m: csr_matrix, n: int) -> float:
    if n == 0:
        return 0.0
    if n == 1:
        return float(abs(m.toarray()[0, 0]))
    return float(svds(m, k=1, return_singular_vectors=False)[0])


def _fixed_point(
    a_plus: csr_matrix, a_minus: csr_matrix, b: np.ndarray
) -> tuple[np.ndarray, float, int, float, float]:
    """Iterate T(x)=clip01((1−α)b + αMx) to the Banach fixed point (§6, §9)."""
    n = len(b)
    m = a_plus - pol.ETA * a_minus
    m_norm = _spectral_norm(m, n)
    alpha = (
        min(pol.ALPHA_BASE, pol.ALPHA_CONTRACTION_MARGIN / m_norm)
        if m_norm > 0
        else pol.ALPHA_BASE
    )
    phi = b.copy()
    residual, iterations = 0.0, 0
    for iterations in range(1, pol.MAX_ITERS + 1):
        nxt = np.clip((1.0 - alpha) * b + alpha * (m @ phi), 0.0, 1.0)
        residual = float(np.max(np.abs(nxt - phi))) if n else 0.0
        phi = nxt
        if residual <= pol.CONVERGENCE_TOL:
            break
    return phi, m_norm, iterations, residual, alpha


def _band_for(
    score: float,
    gate_pass: bool,
    cap: bool,
    has_source_citing: bool,
) -> tuple[str, str]:
    """Raw band (§11) + final band (§14). Returns (raw_band, final_band)."""
    if score >= pol.TAU_CONFIRM:
        raw = "confirmed"
    elif score >= pol.TAU_PROVISIONAL:
        raw = "provisional"
    else:
        raw = "unsubstantiated"
    if score >= pol.TAU_CONFIRM and gate_pass and not cap:
        final = "confirmed"
    elif score >= pol.TAU_PROVISIONAL or has_source_citing:
        final = "provisional"
    else:
        final = "unsubstantiated"
    return raw, final


def run_shadow_derivation(conn: sqlite3.Connection) -> DerivationRun:
    """Compute Φ* + bands for every entity (no writes, no status flip).

    Pure read + compute: callers persist via :func:`persist_traits` and diff via
    ``confidence_shadow_diff``. Safe to run repeatedly — idempotent by §9.
    """
    snapshot = load_snapshot(conn)
    priors = _compute_priors(snapshot.source_assertions)
    assertions_by_entity: dict[str, list[SourceAssertion]] = {}
    for a in snapshot.source_assertions:
        assertions_by_entity.setdefault(a.entity_id, []).append(a)

    node_ids, a_plus, a_minus, b = _build_operator(snapshot, priors)
    phi, m_norm, iterations, residual, alpha = _fixed_point(a_plus, a_minus, b)
    index = {nid: i for i, nid in enumerate(node_ids)}
    cap_vector = pol.ETA * np.asarray(a_minus @ phi).ravel() if node_ids else None

    results: dict[str, EntityConfidence] = {}
    prior_only = 0
    for entity_id, node in snapshot.entities.items():
        prior = priors.get(entity_id)
        has_sc = prior.has_source_citing if prior else False
        if entity_id in index:
            score = float(phi[index[entity_id]])
            cap_val = (
                float(cap_vector[index[entity_id]]) if cap_vector is not None else 0.0
            )
            zero_edge = False
        else:
            score = prior.b if prior else 0.0
            cap_val = 0.0
            zero_edge = True
            prior_only += 1 if prior else 0
        cap = cap_val >= pol.CONTRADICTION_CAP_THRESHOLD
        gate_pass, gate_reason = _gate(assertions_by_entity.get(entity_id, []))
        raw_band, final_band = _band_for(score, gate_pass, cap, has_sc)
        results[entity_id] = EntityConfidence(
            entity_id=entity_id,
            entity_type=node.entity_type,
            in_scope=pol.in_write_scope(node.confidence_field, node.entity_type),
            score=score,
            raw_band=raw_band,
            final_band=final_band,
            b_prior=prior.b if prior else 0.0,
            prior_source_keys=prior.source_keys if prior else (),
            gate_pass=gate_pass,
            gate_reason=gate_reason,
            contradiction_cap=cap,
            cap_value=cap_val,
            zero_edge=zero_edge,
            has_source_citing=has_sc,
            stored_confidence_band=node.stored_confidence_band,
        )

    warnings: list[str] = []
    if node_ids and alpha < 0.10:
        warnings.append(f"prior_dominated_alpha={alpha:.4f}")
    run = DerivationRun(
        results=results,
        alpha_effective=alpha,
        m_norm=m_norm,
        iterations=iterations,
        residual=residual,
        matrix_node_count=len(node_ids),
        edge_count=len(snapshot.edges),
        prior_only_count=prior_only,
        null_credibility_count=snapshot.null_credibility_count,
        generated_at=datetime.datetime.now(tz=datetime.UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        warnings=warnings,
    )
    logger.info(
        "Shadow derivation: %d entities, %d matrix nodes, %d edges, "
        "alpha=%.4f, iters=%d, residual=%.2e",
        len(results),
        len(node_ids),
        len(snapshot.edges),
        alpha,
        iterations,
        residual,
    )
    return run


def persist_traits(conn: sqlite3.Connection, run: DerivationRun) -> int:
    """Write confidence_band/confidence_score for in-scope entities (§0, N5).

    Shadow-only: touches ONLY the migration-050 trait columns, never
    ``entities.status``. Returns the number of rows written.
    """
    written = 0
    for r in run.results.values():
        if not r.in_scope:
            continue
        execute(
            conn,
            "UPDATE entities SET confidence_band = ?, confidence_score = ? WHERE id = ?",
            (r.final_band, r.score, r.entity_id),
        )
        written += 1
    logger.info("Persisted confidence traits for %d in-scope entities", written)
    return written


def entity_count(conn: sqlite3.Connection) -> int:
    """Convenience: total entity rows (smoke check for the batch entrypoint)."""
    rows = query(conn, "SELECT COUNT(*) AS n FROM entities")
    return int(rows[0]["n"]) if rows else 0


__all__ = [
    "DerivationRun",
    "EntityConfidence",
    "entity_count",
    "persist_traits",
    "run_shadow_derivation",
]
