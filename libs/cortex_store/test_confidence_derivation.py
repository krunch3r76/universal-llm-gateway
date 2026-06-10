"""Tests for the Phase 1 shadow-only confidence derivation (policy v1).

Covers: cluster-MAX prior (§5, no duplicate inflation), confirmed-evidence gate
(§12, authority / ≥2 independent external-KB / NULL-credibility / staged / same-
agent collapse), signed propagation + contradiction cap (§6/§13), prior-only /
isolated resolution (§8), determinism (§9), trait persistence isolation (no status
flip, in-scope only), and the §16 rule-vs-rule shadow diff.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from .confidence_derivation import (
    _band_for,
    _gate,
    persist_traits,
    run_shadow_derivation,
)
from .confidence_shadow_diff import (
    BASELINE_LABEL,
    COMPARISON_KIND,
    compute_shadow_diff,
    render_markdown,
)
from .confidence_snapshot import SourceAssertion


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    return migrated_conn


def _entity(
    c,
    eid,
    etype="person",
    *,
    confidence_band="unsubstantiated",
    lifecycle=None,
    source_uri=None,
):
    c.execute(
        "INSERT INTO entities (id, type, name, confidence_band, lifecycle, source_uri) "
        "VALUES (?,?,?,?,?,?)",
        (eid, etype, eid, confidence_band, lifecycle, source_uri),
    )
    c.commit()


def _assert(
    c,
    eid,
    *,
    confidence,
    credibility=None,
    hosts=(),
    seeded_by=None,
    derivation_type=None,
    review_status=None,
    superseded_by=None,
):
    uris = json.dumps([f"https://{h}/x" for h in hosts]) if hosts else None
    if derivation_type is None:
        # D1 firewall: inference cannot satisfy §12 gate; stored external-KB /
        # authority credibility fixtures need a qualifying derivation type.
        derivation_type = (
            "agent_observation"
            if credibility in ("external-KB", "authority")
            else "inference"
        )
    c.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, credibility, "
        "evidence_uris, seeded_by, derivation_type, review_status, superseded_by) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            eid,
            "c",
            confidence,
            credibility,
            uris,
            seeded_by,
            derivation_type,
            review_status,
            superseded_by,
        ),
    )
    c.commit()


def _edge_struct(c, frm, to, etype="evidence_for", strength=1.0):
    c.execute(
        "INSERT INTO relationships (from_entity, to_entity, type, strength, active) "
        "VALUES (?,?,?,?,1)",
        (frm, to, etype, strength),
    )


def _edge_session(
    c,
    frm,
    to,
    etype="contradicts",
    strength=1.0,
    *,
    session_id: str = "confidence-test-session",
):
    c.execute(
        "INSERT INTO session_edges (session_id, agent, from_node, to_node, edge_type, strength) "
        "VALUES (?,?,?,?,?,?)",
        (session_id, "confidence-test", frm, to, etype, strength),
    )


# --- §5 prior ---------------------------------------------------------------


def test_cluster_max_prior_no_duplicate_inflation(conn):
    _entity(conn, "person:one")
    _entity(conn, "person:many")
    _assert(
        conn,
        "person:one",
        confidence="believed",
        credibility="external-KB",
        hosts=["a.com"],
    )
    for _ in range(4):
        _assert(
            conn,
            "person:many",
            confidence="believed",
            credibility="external-KB",
            hosts=["a.com"],
        )
    run = run_shadow_derivation(conn)
    # 0.5*0.85 + 0.5*0.70 = 0.775 — identical regardless of duplicate count.
    assert run.results["person:one"].score == pytest.approx(0.775)
    assert run.results["person:many"].score == pytest.approx(0.775)


def test_prior_takes_max_over_clusters(conn):
    _entity(conn, "person:x")
    _assert(
        conn,
        "person:x",
        confidence="suspected",
        credibility="authority",
        hosts=["a.com"],
        derivation_type="inference",
    )
    _assert(
        conn,
        "person:x",
        confidence="confirmed",
        credibility="unrated",
        hosts=["b.com"],
        derivation_type="inference",
    )
    run = run_shadow_derivation(conn)
    # cluster a: 0.5*1.0+0.5*0.4=0.7 ; cluster b: 0.5*0.3+0.5*1.0=0.65 ; max=0.7
    assert run.results["person:x"].score == pytest.approx(0.7)


# --- §12 gate ---------------------------------------------------------------


def test_two_independent_external_kb_confirms(conn):
    _entity(conn, "person:c")
    _assert(
        conn,
        "person:c",
        confidence="confirmed",
        credibility="external-KB",
        hosts=["a.com"],
        seeded_by="ag1",
    )
    _assert(
        conn,
        "person:c",
        confidence="confirmed",
        credibility="external-KB",
        hosts=["b.com"],
        seeded_by="ag2",
    )
    r = run_shadow_derivation(conn).results["person:c"]
    assert r.gate_pass and r.final_band == "confirmed"  # score 0.925 ≥ τ, 2 clusters


def test_single_external_kb_does_not_confirm(conn):
    _entity(conn, "person:c")
    _assert(
        conn,
        "person:c",
        confidence="confirmed",
        credibility="external-KB",
        hosts=["a.com"],
    )
    r = run_shadow_derivation(conn).results["person:c"]
    assert not r.gate_pass
    assert r.raw_band == "confirmed" and r.final_band == "provisional"


def test_authority_single_cluster_confirms(conn):
    _entity(conn, "person:c")
    _assert(
        conn,
        "person:c",
        confidence="confirmed",
        credibility="authority",
        hosts=["a.com"],
    )
    r = run_shadow_derivation(conn).results["person:c"]
    assert r.gate_pass and r.gate_reason == "authority_cluster"
    assert r.final_band == "confirmed"


def test_null_credibility_resolves_unrated_and_fails_gate(conn):
    _entity(conn, "person:c")
    for h in ("a.com", "b.com"):
        _assert(conn, "person:c", confidence="confirmed", credibility=None, hosts=[h])
    run = run_shadow_derivation(conn)
    r = run.results["person:c"]
    assert not r.gate_pass  # unrated < external-KB
    assert run.null_credibility_count == 2
    # 0.5*0.30 + 0.5*1.0 = 0.65 < τ_confirm → provisional
    assert r.score == pytest.approx(0.65) and r.final_band == "provisional"


# --- §3b v2 internal-provenance derivation dimension ------------------------


def test_user_statement_no_uri_self_sources_and_confirms(conn):
    # Operator user_statement with NO evidence_uris: v1 dropped it entirely; v2
    # self-sources it at internal-authority so a single cluster confirms.
    _entity(conn, "person:op")
    _assert(
        conn,
        "person:op",
        confidence="confirmed",
        derivation_type="user_statement",
        seeded_by="operator",
    )
    r = run_shadow_derivation(conn).results["person:op"]
    assert r.has_source_citing  # reached the prior despite no URIs
    # 0.5*1.0 (internal-authority Ψ) + 0.5*1.0 (confirmed c) = 1.0 ≥ τ_confirm
    assert r.score == pytest.approx(1.0)
    assert r.gate_pass and r.gate_reason == "internal_authority_cluster"
    assert r.final_band == "confirmed"


def test_direct_observation_no_uri_confirms(conn):
    _entity(conn, "fact:d")
    _assert(
        conn,
        "fact:d",
        confidence="confirmed",
        derivation_type="direct_observation",
        seeded_by="reader",
    )
    r = run_shadow_derivation(conn).results["fact:d"]
    assert r.gate_pass and r.gate_reason == "internal_authority_cluster"
    assert r.final_band == "confirmed"


def test_single_agent_observation_does_not_confirm(conn):
    # agent_observation maps to external-KB (0.85), NOT internal-authority — one
    # alone clears τ as provisional but does not pass the ≥2-cluster gate.
    _entity(conn, "fact:a")
    _assert(
        conn,
        "fact:a",
        confidence="confirmed",
        derivation_type="agent_observation",
        seeded_by="tool1",
    )
    r = run_shadow_derivation(conn).results["fact:a"]
    assert not r.gate_pass
    # 0.5*0.85 + 0.5*1.0 = 0.925 ≥ τ_confirm raw, but gate fails → provisional
    assert r.raw_band == "confirmed" and r.final_band == "provisional"


def test_two_agent_observation_clusters_confirm(conn):
    _entity(conn, "fact:aa")
    _assert(
        conn,
        "fact:aa",
        confidence="confirmed",
        derivation_type="agent_observation",
        seeded_by="tool1",
    )
    _assert(
        conn,
        "fact:aa",
        confidence="confirmed",
        derivation_type="agent_observation",
        seeded_by="tool2",
    )
    r = run_shadow_derivation(conn).results["fact:aa"]
    # Two distinct internal:{seeded_by} clusters at external-KB → ≥2 gate passes.
    assert r.gate_pass and r.final_band == "confirmed"


def test_effective_psi_takes_max_external_over_internal(conn):
    # External authority + a weak internal type: max-combination keeps authority,
    # so the single-cluster authority short-circuit confirms.
    _entity(conn, "person:m")
    _assert(
        conn,
        "person:m",
        confidence="confirmed",
        credibility="authority",
        hosts=["a.com"],
        derivation_type="agent_observation",
    )
    r = run_shadow_derivation(conn).results["person:m"]
    assert r.gate_pass and r.gate_reason == "authority_cluster"
    assert r.final_band == "confirmed"


def test_non_internal_no_uri_still_contributes_zero(conn):
    # inference with no evidence_uris is NOT internal-trust → dropped (§8), as v1.
    _entity(conn, "person:z")
    _assert(
        conn,
        "person:z",
        confidence="confirmed",
        derivation_type="inference",
        seeded_by="agentx",
    )
    r = run_shadow_derivation(conn).results["person:z"]
    assert not r.has_source_citing
    assert r.score == pytest.approx(0.0)
    assert r.final_band == "unsubstantiated"


# --- §3c v2 option b: host-derived external credibility -----------------------


def test_gov_host_resolves_authority_and_confirms(conn):
    # NULL stored credibility + a *.gov citation ⇒ authority (single-cluster gate).
    _entity(conn, "fact:gov")
    _assert(
        conn,
        "fact:gov",
        confidence="confirmed",
        credibility=None,
        hosts=["leginfo.legislature.ca.gov"],
        derivation_type="agent_observation",
    )
    r = run_shadow_derivation(conn).results["fact:gov"]
    assert r.gate_pass and r.gate_reason == "authority_cluster"
    assert r.final_band == "confirmed"


def test_manual_list_host_resolves_external_kb(conn):
    # docs.anthropic.com ⇒ external-KB: one alone is provisional, two confirm.
    _entity(conn, "fact:kb1")
    _assert(
        conn,
        "fact:kb1",
        confidence="confirmed",
        credibility=None,
        hosts=["docs.anthropic.com"],
        derivation_type="agent_observation",
    )
    r1 = run_shadow_derivation(conn).results["fact:kb1"]
    assert not r1.gate_pass and r1.final_band == "provisional"

    _entity(conn, "fact:kb2")
    _assert(
        conn,
        "fact:kb2",
        confidence="confirmed",
        credibility=None,
        hosts=["docs.anthropic.com"],
        seeded_by="a1",
        derivation_type="agent_observation",
    )
    _assert(
        conn,
        "fact:kb2",
        confidence="confirmed",
        credibility=None,
        hosts=["cursor.com"],
        seeded_by="a2",
        derivation_type="agent_observation",
    )
    r2 = run_shadow_derivation(conn).results["fact:kb2"]
    assert r2.gate_pass and r2.final_band == "confirmed"


def test_unlisted_host_stays_unrated(conn):
    # An unlisted host falls back to unrated (0.30) — the safe floor, no confirm.
    _entity(conn, "fact:rand")
    _assert(
        conn,
        "fact:rand",
        confidence="confirmed",
        credibility=None,
        hosts=["random-blog.example"],
    )
    r = run_shadow_derivation(conn).results["fact:rand"]
    assert not r.gate_pass
    assert r.score == pytest.approx(0.65)  # 0.5*0.30 + 0.5*1.0


def test_stored_credibility_overrides_host(conn):
    # An explicit stored credibility wins over host derivation (host only fills NULL).
    _entity(conn, "fact:stored")
    _assert(
        conn,
        "fact:stored",
        confidence="confirmed",
        credibility="unrated",
        hosts=["leginfo.legislature.ca.gov"],
    )
    r = run_shadow_derivation(conn).results["fact:stored"]
    assert not r.gate_pass  # stored unrated, not host-derived authority


def test_same_agent_mirror_hosts_not_independent(conn):
    _entity(conn, "person:c")
    _assert(
        conn,
        "person:c",
        confidence="confirmed",
        credibility="external-KB",
        hosts=["a.com"],
        seeded_by="ag1",
    )
    _assert(
        conn,
        "person:c",
        confidence="confirmed",
        credibility="external-KB",
        hosts=["b.com"],
        seeded_by="ag1",
    )
    r = run_shadow_derivation(conn).results["person:c"]
    assert not r.gate_pass and r.final_band == "provisional"


def test_staged_assertion_excluded(conn):
    _entity(conn, "person:c")
    _assert(
        conn,
        "person:c",
        confidence="confirmed",
        credibility="authority",
        hosts=["a.com"],
        review_status="staged",
    )
    r = run_shadow_derivation(conn).results["person:c"]
    assert r.score == 0.0 and not r.has_source_citing
    assert r.final_band == "unsubstantiated"


# --- §6 / §8 / §13 propagation ---------------------------------------------


def test_positive_edge_propagates_downstream(conn):
    _entity(conn, "person:s")
    _entity(conn, "person:t")
    _assert(
        conn,
        "person:s",
        confidence="confirmed",
        credibility="external-KB",
        hosts=["a.com"],
    )
    _edge_struct(conn, "person:s", "person:t", "evidence_for")
    run = run_shadow_derivation(conn)
    # S is prior-dominated (self-loop → Φ*=b=0.925); T inherits half via α=0.5.
    assert run.results["person:s"].score == pytest.approx(0.925)
    assert run.results["person:t"].score == pytest.approx(0.4625)
    assert run.results["person:t"].final_band == "provisional"


def test_contradiction_cap_blocks_confirm(conn):
    _entity(conn, "person:src")
    _entity(conn, "person:tgt")
    _assert(
        conn,
        "person:src",
        confidence="confirmed",
        credibility="authority",
        hosts=["a.com"],
    )
    _assert(
        conn,
        "person:tgt",
        confidence="confirmed",
        credibility="authority",
        hosts=["b.com"],
    )
    _edge_session(conn, "person:src", "person:tgt", "contradicts")
    r = run_shadow_derivation(conn).results["person:tgt"]
    assert r.contradiction_cap and r.cap_value >= 0.40
    assert r.final_band != "confirmed"


def test_isolated_prior_only_resolves_to_b(conn):
    _entity(conn, "person:i")
    _assert(
        conn,
        "person:i",
        confidence="believed",
        credibility="recorded-history",
        hosts=["a.com"],
        derivation_type="inference",
    )
    r = run_shadow_derivation(conn).results["person:i"]
    assert r.zero_edge
    assert r.score == pytest.approx(0.5 * 0.60 + 0.5 * 0.70)  # 0.65


def test_no_evidence_is_unsubstantiated(conn):
    _entity(conn, "person:empty")
    r = run_shadow_derivation(conn).results["person:empty"]
    assert r.score == 0.0 and r.final_band == "unsubstantiated"


# --- §9 determinism ---------------------------------------------------------


def test_determinism(conn):
    _entity(conn, "person:s")
    _entity(conn, "person:t")
    _assert(
        conn,
        "person:s",
        confidence="confirmed",
        credibility="external-KB",
        hosts=["a.com"],
    )
    _edge_struct(conn, "person:s", "person:t", "evidence_for")
    first = {k: v.score for k, v in run_shadow_derivation(conn).results.items()}
    second = {k: v.score for k, v in run_shadow_derivation(conn).results.items()}
    assert first == second


# --- pure band / gate units -------------------------------------------------


def test_band_logic_gate_and_cap():
    assert _band_for(0.9, True, False, True) == ("confirmed", "confirmed")
    assert _band_for(0.9, True, True, True) == ("confirmed", "provisional")  # cap
    assert _band_for(0.9, False, False, True) == ("confirmed", "provisional")  # gate
    assert _band_for(0.1, False, False, True) == ("unsubstantiated", "provisional")
    assert _band_for(0.1, False, False, False) == ("unsubstantiated", "unsubstantiated")


def test_gate_unit_no_qualifying():
    a = SourceAssertion("e", ("a.com",), None, "external-KB", 0.85, "believed", 0.7)
    assert _gate([a]) == (False, "no_qualifying_confirmed_source_citing_evidence")


# --- D1 inference_confirmed firewall (1172-C) ---


def test_inference_confirmed_excluded_from_gate_with_external_uris(conn):
    """inference+confirmed with external evidence_uris contributes to prior but NOT gate."""
    _entity(conn, "fact:inf")
    _assert(
        conn,
        "fact:inf",
        confidence="confirmed",
        credibility="external-KB",
        hosts=["a.com"],
        derivation_type="inference",
    )
    r = run_shadow_derivation(conn).results["fact:inf"]
    # Prior still computed (source-citing via external URI).
    assert r.has_source_citing
    # But gate MUST fail — inference assertions are firewalled (D1).
    assert not r.gate_pass
    assert r.final_band == "provisional"  # score ≥ τ_prov, gate blocked


def test_inference_confirmed_two_clusters_still_fails_gate(conn):
    """Two external-KB clusters with inference type BOTH fail gate (D1)."""
    _entity(conn, "fact:inf2")
    _assert(
        conn,
        "fact:inf2",
        confidence="confirmed",
        credibility="external-KB",
        hosts=["a.com"],
        seeded_by="ag1",
        derivation_type="inference",
    )
    _assert(
        conn,
        "fact:inf2",
        confidence="confirmed",
        credibility="external-KB",
        hosts=["b.com"],
        seeded_by="ag2",
        derivation_type="inference",
    )
    r = run_shadow_derivation(conn).results["fact:inf2"]
    # Two independent clusters — but both inference, so gate still fails.
    assert not r.gate_pass
    assert r.final_band == "provisional"


def test_inference_mixed_with_qualifying_passes_gate(conn):
    """Inference assertion alongside a qualifying non-inference assertion — gate passes."""
    _entity(conn, "fact:mix")
    _assert(
        conn,
        "fact:mix",
        confidence="confirmed",
        credibility="external-KB",
        hosts=["a.com"],
        seeded_by="ag1",
        derivation_type="inference",  # firewalled
    )
    _assert(
        conn,
        "fact:mix",
        confidence="confirmed",
        credibility="authority",
        hosts=["b.gov"],
        seeded_by="ag2",
        derivation_type=None,  # regular — qualifies
    )
    r = run_shadow_derivation(conn).results["fact:mix"]
    assert r.gate_pass  # authority from non-inference assertion passes gate


# --- D3 internal URI classifier (1172-C) ------------------------------------


def test_internal_uri_excluded_from_cluster_count(conn):
    """A cortex: URI is classified as internal and does NOT count as a cluster."""
    _entity(conn, "fact:internal")
    # Insert an assertion with a cortex: URI — must be inserted raw (not via _assert helper)
    import json

    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, credibility, "
        "evidence_uris, seeded_by, derivation_type, review_status, superseded_by) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "fact:internal",
            "c",
            "confirmed",
            "external-KB",
            json.dumps(["cortex:notes/system/threads/1172-c-policy.md"]),
            None,
            "inference",
            None,
            None,
        ),
    )
    r = run_shadow_derivation(conn).results["fact:internal"]
    # cortex: URI filtered out → no external source keys → assertion dropped from source
    assert not r.has_source_citing
    assert r.score == pytest.approx(0.0)


def test_mixed_internal_external_uris_only_external_counts(conn):
    """Mixed evidence_uris: only external ones count toward cluster_count."""
    _entity(conn, "fact:mixed")
    import json

    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, credibility, "
        "evidence_uris, seeded_by, derivation_type, review_status, superseded_by) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "fact:mixed",
            "c",
            "confirmed",
            "authority",
            json.dumps(
                [
                    "https://leginfo.legislature.ca.gov/page",
                    "cortex:notes/internal-trace",
                    "agent-bus:1172",
                ]
            ),
            "ag1",
            "agent_observation",
            None,
            None,
        ),
    )
    r = run_shadow_derivation(conn).results["fact:mixed"]
    # Only the https URL is external → authority cluster → gate passes.
    assert r.has_source_citing
    assert r.gate_pass and r.gate_reason == "authority_cluster"


# --- persistence + §16 ------------------------------------------------------


def test_persist_writes_only_in_scope_traits_no_status_flip(conn):
    _entity(conn, "person:p", confidence_band="unsubstantiated")
    _entity(conn, "decision:d", etype="decision", confidence_band="provisional")
    _assert(
        conn,
        "person:p",
        confidence="confirmed",
        credibility="authority",
        hosts=["a.com"],
    )
    run = run_shadow_derivation(conn)
    written = persist_traits(conn, run)
    assert written == 1  # decision is out of scope
    prow = conn.execute(
        "SELECT lifecycle, confidence_band, confidence_score FROM entities WHERE id='person:p'"
    ).fetchone()
    drow = conn.execute(
        "SELECT lifecycle, confidence_band FROM entities WHERE id='decision:d'"
    ).fetchone()
    assert prow["lifecycle"] is None
    assert prow["confidence_band"] == "confirmed"
    assert prow["confidence_score"] is not None
    assert drow["confidence_band"] == "provisional" and drow["lifecycle"] is None


def test_shadow_diff_rule_vs_rule_labels_and_scope(conn):
    _entity(conn, "person:hit", confidence_band="confirmed")
    _entity(conn, "person:miss", confidence_band="confirmed")
    _entity(conn, "person:lifecycle", confidence_band=None, lifecycle="merged")
    _entity(conn, "decision:d", etype="decision", confidence_band="confirmed")
    _assert(
        conn,
        "person:hit",
        confidence="confirmed",
        credibility="authority",
        hosts=["a.com"],
    )
    # person:miss is legacy-confirmed but derives unsubstantiated (no evidence).
    run = run_shadow_derivation(conn)
    report = compute_shadow_diff(run)
    assert report.baseline_label == BASELINE_LABEL
    assert report.comparison_kind == COMPARISON_KIND
    assert report.excluded_out_of_scope == 1  # decision
    assert report.excluded_unmapped_status == 1  # merged (lifecycle)
    assert report.scoped_count == 2
    assert report.confusion["legacy_confirmed"]["derived_confirmed"] == 1
    assert report.legacy_confirmed_while_derived_not == 1
    assert "rule-vs-rule" in render_markdown(report)
