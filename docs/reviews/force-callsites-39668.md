# Force call-site table (tracked copy)

- **Source:** `lane-18234/tmp/reviews/force-callsites-39668.md`
- **Written against master:** `df52f1853c64fdb9b2ef40481b9088c76beec547`
- **Friction:** 39668

---

# force call sites — friction:39668

RAG reindex `force=True` and worker `supervisor.stop(force=True)` are out of this table.
Columns: path | line | site | caller_requested | guarded.
Line numbers are after the round-3 edit on this branch.

| path | line | site | caller_requested | guarded |
|---|---|---|---|---|
| scripts/model_manager/ui/api_dispatch.py | 275 | mcp restart `force_context=manage_force_context` | from `params.force` | yes — `run_gated_deferred` enforce |
| scripts/model_manager/ui/api_dispatch.py | 339 | mcp rebuild `force_context=manage_force_context` | from `params.force` | yes — `run_gated_deferred` enforce |
| scripts/model_manager/ui/api_dispatch.py | 363 | mcp sync_restart `force_context=manage_force_context` | from `params.force` | yes — `run_gated_deferred` enforce |
| scripts/model_manager/ui/api_dispatch.py | 238 | stop: enforce then `preempt_stargate_idle_drain_if_needed` | from `params.force` | yes — enforce-first; `run_gated(force_already_enforced=True)` |
| scripts/model_manager/ui/api_dispatch.py | 297 | non-mcp restart: enforce then stargate preempt | from `params.force` | yes — enforce-first; `run_gated(force_already_enforced=True)` |
| scripts/model_manager/ui/api_dispatch.py | 424 | non-mcp sync_restart: enforce then stargate preempt | from `params.force` | yes — enforce-first; `run_gated(force_already_enforced=True)` |
| scripts/model_manager/ui/view/screens/services.py | 708 | `_run_gated_action` | `_force_armed` | yes — `run_gated(caller_requested=force, force_reason=self._force_reason)` |
| scripts/model_manager/ui/view/screens/services.py | 756 | `_restart_local` enforce before `evaluate` | True when Force armed | yes — `enforce_force_reason_if_needed` for stargate then gateway |
| scripts/model_manager/ui/view/screens/services.py | 936 | `_stop_mcp` via `_run_gated_action` | inherited | yes — no direct `force=True` stop |
| services/mcp-server/tools/manage.py | 533 | `self_call_counted=True` for mcp stop/restart/sync_restart/rebuild | n/a (flag) | yes — only when `service==mcp` |
| services/git_integration_worker/relay/manage_sock.py | 86 | `sync_restart_service` forwards `force` | via `manage_force_context` | yes — unchanged; `test_manage_sock_force_forward_is_caller_requested` |
