"""Autonomous arc G-row decomposition text for materializer_autonomous."""

from __future__ import annotations


def autonomous_arc_guidance(*, revise_cap: int) -> str:
    """Return the G-row decomposition block for autonomous task guidance."""
    return f"""\
## Autonomous arc (G-row decomposition — lay/advance on the scoreboard)
G1  Q (L0)           cursor-sdk Grok — ranked question table + Question set.
G2  A + Gate-2       cursor-sdk Grok — L1/L2 tables + dense spec
                     (doc_validate gates 6/8/9) + implement_ready assertion.
                     END THIS WINDOW with CONSULT_PENDING + consult_role: r_admit
                     — pin the R prompt URI and, on ONE Sidecars row,
                     `Dense spec: cortex://… · spec_sha256:<64-hex>`. Do not spend
                     a separate holder window just to post the consult stop.
G3  R-admit          Consult seat only (admitted by the next tick from G2's
                     CONSULT_PENDING). It owns primary
                     team_dispatch(model=cdp/opus-5)→poll_hint (from=cdp);
                     MCP project_ask = escape only. Consult seat writes shared
                     provenance via consult_provenance_from_r_admit
                     (consultant_family=anthropic / consultant_substrate=web-anthropic).
                     Parse with fail-closed gate before worker resumes to G4.
                     The holder NEVER fires R-admit transport.
G4  implement +      implement the R-admitted bind, then deploy-verify:
    deploy-verify      quality_gate → manage(sync_restart, service=<svc>) →
                       manage(busy_status) wait_healthy → live probe.
                       [restart-auth: manage MCP only]
  G4a/G4b/G4c revise  probe FAILED ⇒ clean CHECKPOINT queues the next revise step
                      (cap={revise_cap}); on exhaustion post BLOCKED (not done).
G5  R-after          MECHANICAL, inside the G4 window — no separate window:
                     quality_gate + files_expected diff + acceptance_criteria
                     + docstring-quality scan. ESCALATE to a second
                     CONSULT_PENDING + consult_role: r_admit (cdp/opus-5 reviewing
                     the DIFF) when ANY holds: files touched outside
                     files_expected; amendments folded differently than the G3
                     verdict specified; revise count >= 1; invariant-touching or
                     cross-agent scope. Repeat CDP is explicitly sanctioned when
                     post-R-admit code changes warrant a second pass.
G6  close            closeout; friction_close; todo-close with evidence URIs."""


__all__ = ["autonomous_arc_guidance"]
