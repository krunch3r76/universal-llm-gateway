**status: complete**

**work_key:** `todo:giw-worktree-reaper-step3`  
**land_disposition:** unlanded `0cc6374be57c7799b7443e394ed495cb99b8000e`

status: partial
residue: none
### Deliverables
| Path | Role |
|---|---|
| `scripts/giw_worktree_reclaim.py` | Dry-run-default reclaim (R1–R5): orphan-lock reconcile, candidate sweep, gates, optional `--execute` |
| `tests/test_giw_worktree_reclaim.py` | Hermetic tests including `-k dirt` execute path |
| `tasks/specs/giw-worktree-reclaim-ledger.jsonl` | Tracked meta template; runtime ledger → `$GIT_INTEGRATION_SOURCE_REPO/tasks/specs/giw-worktree-reclaim-ledger.jsonl` |

### Verification (quoted)
```text
pytest tests/test_giw_worktree_reclaim.py -k dirt -q  → 1 passed
pytest tests/test_giw_worktree_reclaim.py -q          → 4 passed
python scripts/giw_worktree_reclaim.py                → exit 0; no "worktree remove" in stdout
git diff --stat master..HEAD                          → 3 files, 678 insertions
```

### AC spot-checks
- **AC5 (hub dry-run):** ledger row 1 `orphan_lock_string=ulg:dispatch=21df2184c5ce-4aae4cf6;thread=18415;pinned_at=2026-10-19T20:55:41Z`, `locked_worktree_count=474`, `lane_dir_count=473`
- **AC3:** `lane-20793` / `lane-11767` → `skipped excluded_protected_*`

### Failure modes ↔ tests
| Breaks when | Test |
|---|---|
| Dirty lane removed without wip commit | `test_dirt_lane_wip_commit_then_removed` |
| Lock with no dir not reconciled | `test_orphan_lock_reconciliation_row` |
| Dry-run emits removal | `test_dry_run_prints_without_remove_subcommand` |

**residue / propagation_residue:** none (no `manage` lifecycle from this seat)

**Bus:** `agent-bus:20901#2` — Phase A closeout; hub ledger ready for Phase B G1.

## effects_manifest

{
  "schema_version": 1,
  "dispatch_id": "eabfbadc27b9-2a81f967",
  "thread_id": "20901",
  "capture_sources": [
    "conversation",
    "stream",
    "wrapper"
  ],
  "surfaces": {
    "repo": {
      "surface": "repo",
      "source": "conversation",
      "entries": [
        {
          "op": "shell",
          "target": "cd /mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/lane-20901 && git log -1 --oneline && git diff --stat master..HEAD 2>/dev/null | tail -5",
          "detail": {
            "command": "cd /mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/lane-20901 && git log -1 --oneline && git diff --stat master..HEAD 2>/dev/null | tail -5",
            "timeout": 30000
          },
