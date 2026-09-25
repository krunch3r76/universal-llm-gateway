---
packet_kind: implement
contract: implement
lane: B
sdk_mode: agent
---
<scope>
Mechanical A1 and A2 from the G6 review of todo:cdp-review-contract-shape.
Worktree: /mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/lane-12686
Branch: cursor-sdk/lane-12686
Commit on that branch. Do not merge to master.
Files: libs/review_verdict/grammar.py, libs/review_verdict/test_grammar.py, libs/implement_admission/test_implement_ready_gate6_resolve.py
Do not edit other files. Do not implement review items A3, A4, or A5.
Delete agent-surface/cdp-review-contract-shape-g6-a1a2.md before the commit. Do not commit this packet.
</scope>

<invariants>
- A1: classify the whole normalized verdict token. raw.split()[0] must not turn "RATIFY WITH CONDITIONS", "RATIFY — conditional on AC3", or "RATIFY (with conditions)" into affirmative RATIFY. Trailing words remain only for block tokens REJECT, RETURN, and SCOPE-DRIFT. Tokens outside the closed set are BLOCKED.
- A2: in parse_gate6_markdown, any verdict line that classifies as BLOCKED wins over a later or earlier RATIFY. A body with both **Verdict:** **REJECT** and ## Verdict: **RATIFY** is BLOCKED.
- Keep RATIFY-WITH-CONDITIONS mapping to AMENDMENTS_REQUIRED and gate6_affirmative_disposition False for that token.
- Verify with $HOME/.venvs/universal/bin/pytest on the two test modules and ruff on touched files. Quote the pytest pass line.
</invariants>

<task_guidance>
Edit libs/review_verdict/grammar.py _parse_token_from_raw and parse_gate6_markdown.
Add the three A1 rows and the mixed-verdict A2 row to libs/review_verdict/test_grammar.py.
If the affirmative fold is asserted in libs/implement_admission/test_implement_ready_gate6_resolve.py, add the same not-ratified rows there.
</task_guidance>

<mcp_capabilities>
Native file tools in the lane worktree. No cortex deliverable.
</mcp_capabilities>

<output_format>
Closeout: commit sha on cursor-sdk/lane-12686, pytest line, ruff line.
</output_format>

<corpus>
G6 archive: cortex://notes/system/threads/cdp-ask-archive-cdp-opus-798fd2984847433d94f405d864e6e165.md
Action: AMENDMENTS_REQUIRED. Blockers A1 and A2 only.
</corpus>
