
==================================== ERRORS ====================================
___________ ERROR collecting libs/cortex_store/test_digest_attach.py ___________
ImportError while importing test module '/mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/lane-20666/libs/cortex_store/test_digest_attach.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
libs/cortex_store/test_digest_attach.py:16: in <module>
    from cortex_store.dispatch_ops.ops_digest import _build_claim_proposals
E   ImportError: cannot import name '_build_claim_proposals' from 'cortex_store.dispatch_ops.ops_digest' (/mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/lane-20666/libs/cortex_store/dispatch_ops/ops_digest.py). Did you mean: 'digest_claim_proposals'?
=============================== warnings summary ===============================
libs/cortex_store/models/reflective.py:64
  /mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/lane-20666/libs/cortex_store/models/reflective.py:64: UserWarning: Field name "register" in "ReflectiveEntryCreate" shadows an attribute in parent "BaseModel"
    class ReflectiveEntryCreate(BaseModel):

libs/cortex_store/models/reflective.py:75
  /mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/lane-20666/libs/cortex_store/models/reflective.py:75: UserWarning: Field name "register" in "ReflectiveEntryItem" shadows an attribute in parent "BaseModel"
    class ReflectiveEntryItem(BaseModel):

libs/cortex_store/models/substrate_s6_batch3.py:8
  /mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/lane-20666/libs/cortex_store/models/substrate_s6_batch3.py:8: UserWarning: Field name "register" in "RjConsolidateRequest" shadows an attribute in parent "BaseModel"
    class RjConsolidateRequest(BaseModel):

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
SKIPPED [4] libs/cortex_store/conftest.py:29: migration file absent from this worktree: 050_status_trait_normalization_phase0.py
SKIPPED [1] libs/cortex_store/conftest.py:29: migration file absent from this worktree: 048_correspondence_workflow_and_confidence.py
SKIPPED [2] libs/cortex_store/conftest.py:29: migration file absent from this worktree: 068_digest_ledger.py
SKIPPED [1] libs/cortex_store/conftest.py:29: migration file absent from this worktree: 059_implement_input_schema_registry.py
SKIPPED [1] libs/cortex_store/conftest.py:29: migration file absent from this worktree: 073_canonical_seat_slugs.py
SKIPPED [2] libs/cortex_store/conftest.py:29: migration file absent from this worktree: 054_assertion_attributes.py
SKIPPED [1] libs/cortex_store/conftest.py:29: migration file absent from this worktree: 058_relationship_types_inverse_backfill.py
SKIPPED [1] libs/cortex_store/conftest.py:29: migration file absent from this worktree: 037_provenance_v1_schema.py
SKIPPED [1] libs/cortex_store/conftest.py:29: migration file absent from this worktree: 074_seat_claims.py
SKIPPED [1] libs/cortex_store/conftest.py:29: migration file absent from this worktree: 055_session_journals_source_ref.py
SKIPPED [1] libs/cortex_store/conftest.py:29: migration file absent from this worktree: 041_skill_linkage_primitives.py
SKIPPED [1] libs/cortex_store/conftest.py:29: migration file absent from this worktree: 053_thread_compression_derivation_backfill.py
SKIPPED [1] libs/cortex_store/conftest.py:29: migration file absent from this worktree: 045_todo_required_skills_schema.py
SKIPPED [1] libs/cortex_store/conftest.py:29: migration file absent from this worktree: 064_matter_genus_mode.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
19 skipped, 3 warnings, 1 error in 8.33s
