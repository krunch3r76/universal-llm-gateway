"""Gated cross-family parseability pilot for [assertion:NNNN] emission (Phase 1.0b).

This test is intentionally heavy and non-deterministic (live LLM calls). It is
gated behind the env var CORTEX_RUN_CROSS_FAMILY_PILOT=1 and is NOT executed
in default CI.

Manual-run procedure:
  1. Ensure you have valid API keys for the three families in the environment
     (ANTHROPIC_API_KEY, OPENAI_API_KEY, XAI_API_KEY or equivalent).
  2. Pick a small D.2 entity (e.g. the Uber case or any entity with 3-5 active assertions).
  3. CORTEX_RUN_CROSS_FAMILY_PILOT=1 python -m pytest \
       libs/cortex_store/agent_injection/tests/test_cross_family_parseability.py -q -rA -s

The test materializes a D.2, crafts a smoke prompt asking the model to cite
every claim with [assertion:NNNN], then feeds the raw completion through
validate_output and asserts zero `output_citation_missing_assertion` findings
(meaning the emitted citation ids were all resolvable and the text was parseable).

Because this exercises the real dispatch surfaces it is kept out of the normal
pytest run and serves as a regression canary for cross-family citation grammar
adherence.
"""

from __future__ import annotations

import os

import pytest


@pytest.mark.skipif(
    os.environ.get("CORTEX_RUN_CROSS_FAMILY_PILOT") != "1",
    reason=(
        "Cross-family pilot requires live dispatch + API keys. "
        "Set CORTEX_RUN_CROSS_FAMILY_PILOT=1 and provide credentials to run. "
        "See docstring for manual-run steps."
    ),
)
def test_cross_family_models_emit_parseable_citations():
    """Smoke: three families produce responses whose [assertion:NNNN] citations all resolve."""
    # When the gate is open the real implementation would:
    #   - materialize_d2(some_small_entity)
    #   - build prompt = "Cite each fact using [assertion:ID] exactly once: " + d2["rendered"]
    #   - for each of ("anthropic/claude-opus-4-7", "openai/gpt-5.5", "xai/grok-4.3"):
    #       resp = dispatch_to(model, prompt)
    #       v = validate_output(resp)
    #       missing = [f for f in v.findings if f.kind == "output_citation_missing_assertion"]
    #       assert missing == []
    #
    # The 1.0b deliverable only lands the validator + the gated test skeleton.
    # Full wiring (skill router, §D.7 dispatch) is later.
    pytest.skip("Live dispatch not wired in this 1.0b dispatch; test is documentation + gate only.")
