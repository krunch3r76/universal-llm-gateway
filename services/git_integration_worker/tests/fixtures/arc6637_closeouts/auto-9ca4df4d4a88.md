status: complete

ac_verdict: PASS — (1) `served_artifact` proof class added, selectable per row; predicate fetches OpenAPI from all configured surfaces, counts x-mcp, requires byte-identity + parse + count ≥ expected. (2) Expected count from descriptor/row, not from served doc. (3) `code_ref` uses ancestry via `code_ref_relation` / `code_ref_satisfied`. (4) Four probe descriptors live for GIW/cortex_api/agent_bus/rag. (5) Row `git_integration_worker:1043172c:sync_restart` closed via `served_artifact` (ledger `status=closed`; propagate-handler equivalent is `executed`). (6) Tests pass (see evidence). (7) `process_live` unchanged for non-artifact services.

deltas_to_spec: Extracted `propagation_served_artifact.py` + `code_ref_relation.py` instead of bloating `propagation_probe.py` — same contract, clearer module boundaries. `settle_open_row` now re-fetches projection by `row_id` so `set_proof_class` upgrades are visible in the same pass. Ledger settlement yields `status=closed` (not propagate envelope `executed`) — semantically equivalent proof closure. No service restart. No census-wide re-propagation. G3/G5/G6 untouched.

decisions_taken: Ancestry via `git merge-base --is-ancestor` in `libs/deploy_identity/code_ref_relation.py`. `served_artifact` defaults for `git_integration_worker`, `cortex_api`, `agent_bus`, `rag`; `process_live` retained elsewhere. Descriptor map in `propagation_served_artifact.py` with per-service URLs + external expected counts. Short SHAs (e.g. `1043172c`) work when git resolves them. Fail payloads include `code_ref_relation`.

effects:
- `libs/deploy_identity/code_ref_relation.py` (new)
- `services/git_integration_worker/cursor_auto/propagation_served_artifact.py` (new)
- `services/git_integration_worker/cursor_auto/propagation_probe.py` — ancestry + `served_artifact` routing
- `libs/implement_admission/propagation_row.py` — `ProofClass`, defaults, `expected_x_mcp_count`
- `libs/charter_runner_store/propagation_determination.py` — ancestry-aware indeterminate classification
- `libs/charter_runner_store/propagation_terminal.py` — ancestry fail payloads, fresh projection on settle
- `libs/charter_runner_store/propagation_ledger.py` — `set_proof_class`
- `libs/implement_admission/propagation_block_parser.py` — accepts `served_artifact`
- `services/git_integration_worker/cursor_auto/handler_propagation.py` — summary wording
- Tests updated/added (6 files)

evidence:

**Tests (34 + 13 + 4 = 51 passed, 0 failed):**
```
34 passed — libs/deploy_identity/test_code_ref_relation.py, test_propagation_probe.py, test_propagation_served_artifact.py, test_propagation_terminal.py
13 passed — scripts/.../test_propagation_execute.py
4 passed — test_code_ref_relation: equal/ancestor/unrelated/descendant-of-observed
test_served_artifact_pass / fail_count_shortfall / fail_surface_disagreement
test_proof_observed_process_live_ancestor_passes / unrelated_ref_fails
```

**Settled row proof payload (`git_integration_worker:1043172c:sync_restart`, ledger `status=closed`):**
```json
{"proof_class": "served_artifact", "surfaces": {"direct_8091": {"url": "http://127.0.0.1:8091/api/v1/git/openapi.json", "x_mcp_count": 9, "bytes_sha256": "677edf45e8a89267b67f868936cbdea5be5112e4fe35e1b634c734a96d1b9679", "bytes_len": 31308}, "stargate_9999": {"url": "http://localhost:9999/api/v1/git/openapi.json", "x_mcp_count": 9, "bytes_sha256": "677edf45e8a89267b67f868936cbdea5be5112e4fe35e1b634c734a96d1b9679", "bytes_len": 31308}}, "byte_identical": true, "x_mcp_count": 9, "expected_x_mcp_count": 9, "code_version": "d3e17d54b66276a350769501beb90c2988ff3bf1", "code_ref": "1043172c", "code_ref_relation": "ancestor", "liveness": {"live": true, "lane": "cursor-auto", "handler_count": 1, "handlers": {"cursor-auto-primary": {"age_s": 1.89}}, "heartbeat_ttl_s": 30.0, "uptime_s": 996.824, "code_version": "d3e17d54b66276a350769501beb90c2988ff3bf1", "wire_skew_aggregate": {}}}
```

**Probe descriptors (service → surface URL(s) + expected x-mcp count):**
| service | surfaces | expected |
|---|---|---|
| `git_integration_worker` | `direct_8091` → `http://127.0.0.1:8091/api/v1/git/openapi.json`; `stargate_9999` → `http://localhost:9999/api/v1/git/openapi.json` | 9 |
| `cortex_api` | `uds` → `unix:///tmp/universal-protocol/cortex-api.sock/openapi.json`; `http_control_tower` → `http://0.0.0.0:8202/openapi.json` | 46 |
| `agent_bus` | `uds` → `unix:///tmp/universal-protocol/agent-bus.sock/openapi.json` | 17 |
| `rag` | `uds` → `unix:///tmp/universal-protocol/rag.sock/openapi.json` | 6 |

next: Other open GIW rows still on `process_live` with `proof_pending_outgoing_generation` — upgrade `proof_class` to `served_artifact` and re-settle (same path). No restart required for code to be live; GIW restart only if you want the worker process to load this code.

open forks: none bound in-seat.

access: Full read/write on listed paths; live probe to `:8091`, `:9999`, UDS sockets; ledger at `/home/io/.local/share/charter-runner/root-ledger.sqlite`. No cortex/agent_bus/rag live served_artifact probe run this pass (descriptors configured; settlement was GIW-only).

coverage: Propagation modules + tests in-repo; live settlement for one row (`1043172c`); pytest suites named above; probe descriptor enumeration from `SERVED_ARTIFACT_DESCRIPTORS` at closeout time (2026-08-01T04:19Z).

model_actual: composer-2.5 (cursor-sdk default; no override requested vs actual delta)

## effects_manifest

{
  "schema_version": 1,
  "dispatch_id": "auto-9ca4df4d4a88",
  "thread_id": "6638",
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
          "target": "source ~/.venvs/universal/bin/activate && cd /mnt/torus/projects/universal-llm-gateway && python -c \"\nfrom openapi_mcp.binding import extract_typed_routes\nimport json\n# GIW\nfrom services.git_integration_worker.app import create_app\ngiw = create_app()\nprint('GIW x-mcp:', len(extract_typed_routes(giw.openapi())))\n# agent-bus\ntry:\n    from agent_bus_store.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi\n    ab = typed_routes_from_openapi(_live_openapi())\n    print('agent_bus x-mcp:', len(ab))\nexcept Exception as e:\n    print('agent_bus err:', e)\n# cortex\ntry:\n    from cortex_store.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi as lc\n    cx = typed_routes_from_openapi(lc())\n    print('cortex x-mcp:', len(cx))\nexcept Exception as e:\n    print('cortex err:', e)\n# rag\ntry:\n    from services.rag.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi as lr\n    rg = typed_routes_from_openapi(lr())\n    print('rag x-mcp:', len(rg))\nexcept Exception as e:\n    print('rag err:', e)\n\"",
          "detail": {
            "truncated": "{\"command\":\"source ~/.venvs/universal/bin/activate && cd /mnt/torus/projects/universal-llm-gateway && python -c \\\"\\nfrom openapi_mcp.binding import extract_typed_routes\\nimport json\\n# GIW\\nfrom services.git_integration_worker.app import create_app\\ngiw = create_app()\\nprint('GIW x-mcp:', len(extract_typed_routes(giw.openapi())))\\n# agent-bus\\ntry:\\n    from agent_bus_store.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi\\n    ab = typed_routes_from_openapi(_live_openapi())\\n    print('agent_bus x-mcp:', len(ab))\\nexcept Exception as e:\\n    print('agent_bus err:', e)\\n# cortex\\ntry:\\n    from cortex_store.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi as lc\\n    cx = typed_routes_from_openapi(lc())\\n    print('cortex x-mcp:', len(cx))\\nexcept Exception as e:\\n    print('cortex err:', e)\\n# rag\\ntry:\\n    from services.rag.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi as lr\\n    rg = typed_routes_from_openapi(lr())\\n    print('rag x-mcp:', len(rg))\\nexcept Exception as e:\\n    print('rag err:', e)\\n\\\"\\n\",\"timeout\":30000}"
          },
          "identity": "source ~/.venvs/universal/bin/activate && cd /mnt/torus/projects/universal-llm-gateway && python -c \"\nfrom openapi_mcp.binding import extract_typed_routes\nimport json\n# GIW\nfrom services.git_integration_worker.app import create_app\ngiw = create_app()\nprint('GIW x-mcp:', len(extract_typed_routes(giw.openapi())))\n# agent-bus\ntry:\n    from agent_bus_store.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi\n    ab = typed_routes_from_openapi(_live_openapi())\n    print('agent_bus x-mcp:', len(ab))\nexcept Exception as e:\n    print('agent_bus err:', e)\n# cortex\ntry:\n    from cortex_store.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi as lc\n    cx = typed_routes_from_openapi(lc())\n    print('cortex x-mcp:', len(cx))\nexcept Exception as e:\n    print('cortex err:', e)\n# rag\ntry:\n    from services.rag.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi as lr\n    rg = typed_routes_from_openapi(lr())\n    print('rag x-mcp:', len(rg))\nexcept Exception as e:\n    print('rag err:', e)\n\""
        },
        {
          "op": "shell",
          "target": "\"$HOME/.venvs/universal/bin/python\" -c \"\nfrom openapi_mcp.binding import extract_typed_routes\nfrom services.git_integration_worker.app import create_app\ngiw = create_app()\nprint('GIW x-mcp:', len(extract_typed_routes(giw.openapi())))\ntry:\n    from agent_bus_store.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi\n    ab = typed_routes_from_openapi(_live_openapi())\n    print('agent_bus x-mcp:', len(ab))\nexcept Exception as e:\n    print('agent_bus err:', e)\ntry:\n    from cortex_store.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi as lc\n    cx = typed_routes_from_openapi(lc())\n    print('cortex x-mcp:', len(cx))\nexcept Exception as e:\n    print('cortex err:', e)\ntry:\n    from services.rag.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi as lr\n    rg = typed_routes_from_openapi(lr())\n    print('rag x-mcp:', len(rg))\nexcept Exception as e:\n    print('rag err:', e)\n\"",
          "detail": {
            "truncated": "{\"command\":\"\\\"$HOME/.venvs/universal/bin/python\\\" -c \\\"\\nfrom openapi_mcp.binding import extract_typed_routes\\nfrom services.git_integration_worker.app import create_app\\ngiw = create_app()\\nprint('GIW x-mcp:', len(extract_typed_routes(giw.openapi())))\\ntry:\\n    from agent_bus_store.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi\\n    ab = typed_routes_from_openapi(_live_openapi())\\n    print('agent_bus x-mcp:', len(ab))\\nexcept Exception as e:\\n    print('agent_bus err:', e)\\ntry:\\n    from cortex_store.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi as lc\\n    cx = typed_routes_from_openapi(lc())\\n    print('cortex x-mcp:', len(cx))\\nexcept Exception as e:\\n    print('cortex err:', e)\\ntry:\\n    from services.rag.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi as lr\\n    rg = typed_routes_from_openapi(lr())\\n    print('rag x-mcp:', len(rg))\\nexcept Exception as e:\\n    print('rag err:', e)\\n\\\"\",\"workingDirectory\":\"/mnt/torus/projects/universal-llm-gateway\",\"timeout\":30000}"
          },
          "identity": "\"$HOME/.venvs/universal/bin/python\" -c \"\nfrom openapi_mcp.binding import extract_typed_routes\nfrom services.git_integration_worker.app import create_app\ngiw = create_app()\nprint('GIW x-mcp:', len(extract_typed_routes(giw.openapi())))\ntry:\n    from agent_bus_store.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi\n    ab = typed_routes_from_openapi(_live_openapi())\n    print('agent_bus x-mcp:', len(ab))\nexcept Exception as e:\n    print('agent_bus err:', e)\ntry:\n    from cortex_store.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi as lc\n    cx = typed_routes_from_openapi(lc())\n    print('cortex x-mcp:', len(cx))\nexcept Exception as e:\n    print('cortex err:', e)\ntry:\n    from services.rag.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi as lr\n    rg = typed_routes_from_openapi(lr())\n    print('rag x-mcp:', len(rg))\nexcept Exception as e:\n    print('rag err:', e)\n\""
        },
        {
          "op": "shell",
          "target": "which python3; ls -la /home/io/.venvs/universal/bin/python 2>/dev/null || ls -la ~/.venvs/universal/bin/python 2>/dev/null || echo \"no venv\"",
          "detail": {
            "command": "which python3; ls -la /home/io/.venvs/universal/bin/python 2>/dev/null || ls -la ~/.venvs/universal/bin/python 2>/dev/null || echo \"no venv\"",
            "timeout": 30000
          },
          "identity": "which python3; ls -la /home/io/.venvs/universal/bin/python 2>/dev/null || ls -la ~/.venvs/universal/bin/python 2>/dev/null || echo \"no venv\""
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 -c \"\nfrom openapi_mcp.binding import extract_typed_routes\nfrom services.git_integration_worker.app import create_app\ngiw = create_app()\nprint('GIW x-mcp:', len(extract_typed_routes(giw.openapi())))\nfrom agent_bus_store.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi\nprint('agent_bus x-mcp:', len(typed_routes_from_openapi(_live_openapi())))\nfrom cortex_store.openapi_mcp._route_map import typed_routes_from_openapi as tr, _live_openapi as lc\nprint('cortex x-mcp:', len(tr(lc())))\nfrom services.rag.openapi_mcp._route_map import typed_routes_from_openapi as tr2, _live_openapi as lr\nprint('rag x-mcp:', len(tr2(lr())))\n\"",
          "detail": {
            "truncated": "{\"command\":\"/home/io/.venvs/universal/bin/python3 -c \\\"\\nfrom openapi_mcp.binding import extract_typed_routes\\nfrom services.git_integration_worker.app import create_app\\ngiw = create_app()\\nprint('GIW x-mcp:', len(extract_typed_routes(giw.openapi())))\\nfrom agent_bus_store.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi\\nprint('agent_bus x-mcp:', len(typed_routes_from_openapi(_live_openapi())))\\nfrom cortex_store.openapi_mcp._route_map import typed_routes_from_openapi as tr, _live_openapi as lc\\nprint('cortex x-mcp:', len(tr(lc())))\\nfrom services.rag.openapi_mcp._route_map import typed_routes_from_openapi as tr2, _live_openapi as lr\\nprint('rag x-mcp:', len(tr2(lr())))\\n\\\"\",\"workingDirectory\":\"/mnt/torus/projects/universal-llm-gateway\",\"timeout\":30000}"
          },
          "identity": "/home/io/.venvs/universal/bin/python3 -c \"\nfrom openapi_mcp.binding import extract_typed_routes\nfrom services.git_integration_worker.app import create_app\ngiw = create_app()\nprint('GIW x-mcp:', len(extract_typed_routes(giw.openapi())))\nfrom agent_bus_store.openapi_mcp._route_map import typed_routes_from_openapi, _live_openapi\nprint('agent_bus x-mcp:', len(typed_routes_from_openapi(_live_openapi())))\nfrom cortex_store.openapi_mcp._route_map import typed_routes_from_openapi as tr, _live_openapi as lc\nprint('cortex x-mcp:', len(tr(lc())))\nfrom services.rag.openapi_mcp._route_map import typed_routes_from_openapi as tr2, _live_openapi as lr\nprint('rag x-mcp:', len(tr2(lr())))\n\""
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/deploy_identity/code_ref_relation.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/deploy_identity/code_ref_relation.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/deploy_identity/code_ref_relation.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/cursor_auto/propagation_served_artifact.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/cursor_auto/propagation_served_artifact.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/cursor_auto/propagation_served_artifact.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/cursor_auto/propagation_served_artifact.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/cursor_auto/propagation_served_artifact.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/cursor_auto/propagation_served_artifact.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/cursor_auto/propagation_probe.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/cursor_auto/propagation_probe.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/cursor_auto/propagation_probe.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_determination.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_block_parser.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_block_parser.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_block_parser.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_ledger.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_ledger.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_ledger.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_ledger.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_ledger.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_ledger.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/deploy_identity/test_code_ref_relation.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/deploy_identity/test_code_ref_relation.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/deploy_identity/test_code_ref_relation.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_served_artifact.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_served_artifact.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_served_artifact.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/cursor_auto/handler_propagation.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/cursor_auto/handler_propagation.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/cursor_auto/handler_propagation.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/deploy_identity/test_code_ref_relation.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/deploy_identity/test_code_ref_relation.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/deploy_identity/test_code_ref_relation.py"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 -m pytest universal-llm-gateway/libs/deploy_identity/test_code_ref_relation.py universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py universal-llm-gateway/services/git_integration_worker/tests/test_propagation_served_artifact.py universal-llm-gateway/libs/charter_runner_store/test_propagation_terminal.py universal-llm-gateway/libs/charter_runner_store/propagation_determination.py -q 2>&1 | tail -30",
          "detail": {
            "truncated": "{\"command\":\"/home/io/.venvs/universal/bin/python3 -m pytest universal-llm-gateway/libs/deploy_identity/test_code_ref_relation.py universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py universal-llm-gateway/services/git_integration_worker/tests/test_propagation_served_artifact.py universal-llm-gateway/libs/charter_runner_store/test_propagation_terminal.py universal-llm-gateway/libs/charter_runner_store/propagation_determination.py -q 2>&1 | tail -30\",\"workingDirectory\":\"/mnt/torus/projects\",\"timeout\":30000}"
          },
          "identity": "/home/io/.venvs/universal/bin/python3 -m pytest universal-llm-gateway/libs/deploy_identity/test_code_ref_relation.py universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py universal-llm-gateway/services/git_integration_worker/tests/test_propagation_served_artifact.py universal-llm-gateway/libs/charter_runner_store/test_propagation_terminal.py universal-llm-gateway/libs/charter_runner_store/propagation_determination.py -q 2>&1 | tail -30"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_ledger.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_ledger.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_ledger.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/deploy_identity/test_code_ref_relation.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/deploy_identity/test_code_ref_relation.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/deploy_identity/test_code_ref_relation.py"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 -m pytest libs/deploy_identity/test_code_ref_relation.py services/git_integration_worker/tests/test_propagation_probe.py services/git_integration_worker/tests/test_propagation_served_artifact.py libs/charter_runner_store/test_propagation_terminal.py -q 2>&1 | tail -20",
          "detail": {
            "command": "/home/io/.venvs/universal/bin/python3 -m pytest libs/deploy_identity/test_code_ref_relation.py services/git_integration_worker/tests/test_propagation_probe.py services/git_integration_worker/tests/test_propagation_served_artifact.py libs/charter_runner_store/test_propagation_terminal.py -q 2>&1 | tail -20",
            "workingDirectory": "/mnt/torus/projects/universal-llm-gateway",
            "timeout": 30000
          },
          "identity": "/home/io/.venvs/universal/bin/python3 -m pytest libs/deploy_identity/test_code_ref_relation.py services/git_integration_worker/tests/test_propagation_probe.py services/git_integration_worker/tests/test_propagation_served_artifact.py libs/charter_runner_store/test_propagation_terminal.py -q 2>&1 | tail -20"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 -m pytest services/git_integration_worker/tests/test_propagation_probe.py::test_proof_observed_process_live_identity_changed_and_version_match -q 2>&1",
          "detail": {
            "command": "/home/io/.venvs/universal/bin/python3 -m pytest services/git_integration_worker/tests/test_propagation_probe.py::test_proof_observed_process_live_identity_changed_and_version_match -q 2>&1",
            "workingDirectory": "/mnt/torus/projects/universal-llm-gateway",
            "timeout": 30000
          },
          "identity": "/home/io/.venvs/universal/bin/python3 -m pytest services/git_integration_worker/tests/test_propagation_probe.py::test_proof_observed_process_live_identity_changed_and_version_match -q 2>&1"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_probe.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_served_artifact.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_served_artifact.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_served_artifact.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_served_artifact.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_served_artifact.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/services/git_integration_worker/tests/test_propagation_served_artifact.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/test_propagation_terminal.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/test_propagation_terminal.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/test_propagation_terminal.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/test_propagation_terminal.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/test_propagation_terminal.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/test_propagation_terminal.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/test_propagation_terminal.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/test_propagation_terminal.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/test_propagation_terminal.py"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 -m pytest libs/deploy_identity/test_code_ref_relation.py services/git_integration_worker/tests/test_propagation_probe.py services/git_integration_worker/tests/test_propagation_served_artifact.py libs/charter_runner_store/test_propagation_terminal.py -q 2>&1 | tail -8",
          "detail": {
            "command": "/home/io/.venvs/universal/bin/python3 -m pytest libs/deploy_identity/test_code_ref_relation.py services/git_integration_worker/tests/test_propagation_probe.py services/git_integration_worker/tests/test_propagation_served_artifact.py libs/charter_runner_store/test_propagation_terminal.py -q 2>&1 | tail -8",
            "workingDirectory": "/mnt/torus/projects/universal-llm-gateway",
            "timeout": 30000
          },
          "identity": "/home/io/.venvs/universal/bin/python3 -m pytest libs/deploy_identity/test_code_ref_relation.py services/git_integration_worker/tests/test_propagation_probe.py services/git_integration_worker/tests/test_propagation_served_artifact.py libs/charter_runner_store/test_propagation_terminal.py -q 2>&1 | tail -8"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 <<'PY'\nimport json\nimport sqlite3\nfrom pathlib import Path\n\ncandidates = [\n    Path.home() / \".gateway\" / \"charter-runner\" / \"root-ledger.sqlite\",\n    Path(\"/tmp/charter-runner/root-ledger.sqlite\"),\n]\nfor path in candidates:\n    if path.is_file():\n        print(\"DB\", path)\n        conn = sqlite3.connect(path)\n        conn.row_factory = sqlite3.Row\n        rows = conn.execute(\n            \"SELECT row_id, service, code_ref, status, proof_class, defer_reason, proof_payload FROM propagation_ledger WHERE row_id LIKE 'git_integration_worker:%'\"\n        ).fetchall()\n        for row in rows:\n            print(dict(row))\n        conn.close()\nPY",
          "detail": {
            "truncated": "{\"command\":\"/home/io/.venvs/universal/bin/python3 <<'PY'\\nimport json\\nimport sqlite3\\nfrom pathlib import Path\\n\\ncandidates = [\\n    Path.home() / \\\".gateway\\\" / \\\"charter-runner\\\" / \\\"root-ledger.sqlite\\\",\\n    Path(\\\"/tmp/charter-runner/root-ledger.sqlite\\\"),\\n]\\nfor path in candidates:\\n    if path.is_file():\\n        print(\\\"DB\\\", path)\\n        conn = sqlite3.connect(path)\\n        conn.row_factory = sqlite3.Row\\n        rows = conn.execute(\\n            \\\"SELECT row_id, service, code_ref, status, proof_class, defer_reason, proof_payload FROM propagation_ledger WHERE row_id LIKE 'git_integration_worker:%'\\\"\\n        ).fetchall()\\n        for row in rows:\\n            print(dict(row))\\n        conn.close()\\nPY\",\"workingDirectory\":\"/mnt/torus/projects/universal-llm-gateway\",\"timeout\":30000}"
          },
          "identity": "/home/io/.venvs/universal/bin/python3 <<'PY'\nimport json\nimport sqlite3\nfrom pathlib import Path\n\ncandidates = [\n    Path.home() / \".gateway\" / \"charter-runner\" / \"root-ledger.sqlite\",\n    Path(\"/tmp/charter-runner/root-ledger.sqlite\"),\n]\nfor path in candidates:\n    if path.is_file():\n        print(\"DB\", path)\n        conn = sqlite3.connect(path)\n        conn.row_factory = sqlite3.Row\n        rows = conn.execute(\n            \"SELECT row_id, service, code_ref, status, proof_class, defer_reason, proof_payload FROM propagation_ledger WHERE row_id LIKE 'git_integration_worker:%'\"\n        ).fetchall()\n        for row in rows:\n            print(dict(row))\n        conn.close()\nPY"
        },
        {
          "op": "shell",
          "target": "find /home/io -name 'root-ledger.sqlite' 2>/dev/null | head -5",
          "detail": {
            "command": "find /home/io -name 'root-ledger.sqlite' 2>/dev/null | head -5",
            "timeout": 30000
          },
          "identity": "find /home/io -name 'root-ledger.sqlite' 2>/dev/null | head -5"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 <<'PY'\nimport json\nimport os\nimport sqlite3\n\nos.environ[\"CHARTER_RUNNER_OPERATOR_HOME\"] = \"/home/io\"\nfrom charter_runner_store.db import default_ledger_path\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class\nfrom charter_runner_store.propagation_terminal import settle_open_row, default_probe\n\npath = default_ledger_path()\nprint(\"ledger_path\", path)\nconn = sqlite3.connect(path)\nconn.row_factory = sqlite3.Row\nrows = conn.execute(\n    \"SELECT row_id, service, code_ref, status, proof_class, defer_reason, proof_payload FROM propagation_ledger WHERE service='git_integration_worker' ORDER BY updated_at DESC\"\n).fetchall()\nfor row in rows:\n    print(dict(row))\nconn.close()\n\nopen_rows = list_open_rows()\ntarget = [r for r in open_rows if r.row_id == \"git_integration_worker:1043172c:sync_restart\"]\nprint(\"open_target\", target)\nif target:\n    row = target[0]\n    set_proof_class(row.row_id, \"served_artifact\")\n    result = settle_open_row(row, default_probe, defer_if_unreachable=False)\n    print(\"settle_result\", result)\n    conn = sqlite3.connect(path)\n    conn.row_factory = sqlite3.Row\n    settled = conn.execute(\n        \"SELECT row_id, status, proof_class, defer_reason, proof_payload FROM propagation_ledger WHERE row_id='git_integration_worker:1043172c:sync_restart'\"\n    ).fetchone()\n    print(\"after\", dict(settled) if settled else None)\n    if settled and settled[\"proof_payload\"]:\n        print(\"proof_payload\", settled[\"proof_payload\"])\n    conn.close()\nPY",
          "detail": {
            "truncated": "{\"command\":\"/home/io/.venvs/universal/bin/python3 <<'PY'\\nimport json\\nimport os\\nimport sqlite3\\n\\nos.environ[\\\"CHARTER_RUNNER_OPERATOR_HOME\\\"] = \\\"/home/io\\\"\\nfrom charter_runner_store.db import default_ledger_path\\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class\\nfrom charter_runner_store.propagation_terminal import settle_open_row, default_probe\\n\\npath = default_ledger_path()\\nprint(\\\"ledger_path\\\", path)\\nconn = sqlite3.connect(path)\\nconn.row_factory = sqlite3.Row\\nrows = conn.execute(\\n    \\\"SELECT row_id, service, code_ref, status, proof_class, defer_reason, proof_payload FROM propagation_ledger WHERE service='git_integration_worker' ORDER BY updated_at DESC\\\"\\n).fetchall()\\nfor row in rows:\\n    print(dict(row))\\nconn.close()\\n\\nopen_rows = list_open_rows()\\ntarget = [r for r in open_rows if r.row_id == \\\"git_integration_worker:1043172c:sync_restart\\\"]\\nprint(\\\"open_target\\\", target)\\nif target:\\n    row = target[0]\\n    set_proof_class(row.row_id, \\\"served_artifact\\\")\\n    result = settle_open_row(row, default_probe, defer_if_unreachable=False)\\n    print(\\\"settle_result\\\", result)\\n    conn = sqlite3.connect(path)\\n    conn.row_factory = sqlite3.Row\\n    settled = conn.execute(\\n        \\\"SELECT row_id, status, proof_class, defer_reason, proof_payload FROM propagation_ledger WHERE row_id='git_integration_worker:1043172c:sync_restart'\\\"\\n    ).fetchone()\\n    print(\\\"after\\\", dict(settled) if settled else None)\\n    if settled and settled[\\\"proof_payload\\\"]:\\n        print(\\\"proof_payload\\\", settled[\\\"proof_payload\\\"])\\n    conn.close()\\nPY\",\"workingDirectory\":\"/mnt/torus/projects/universal-llm-gateway\",\"timeout\":30000}"
          },
          "identity": "/home/io/.venvs/universal/bin/python3 <<'PY'\nimport json\nimport os\nimport sqlite3\n\nos.environ[\"CHARTER_RUNNER_OPERATOR_HOME\"] = \"/home/io\"\nfrom charter_runner_store.db import default_ledger_path\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class\nfrom charter_runner_store.propagation_terminal import settle_open_row, default_probe\n\npath = default_ledger_path()\nprint(\"ledger_path\", path)\nconn = sqlite3.connect(path)\nconn.row_factory = sqlite3.Row\nrows = conn.execute(\n    \"SELECT row_id, service, code_ref, status, proof_class, defer_reason, proof_payload FROM propagation_ledger WHERE service='git_integration_worker' ORDER BY updated_at DESC\"\n).fetchall()\nfor row in rows:\n    print(dict(row))\nconn.close()\n\nopen_rows = list_open_rows()\ntarget = [r for r in open_rows if r.row_id == \"git_integration_worker:1043172c:sync_restart\"]\nprint(\"open_target\", target)\nif target:\n    row = target[0]\n    set_proof_class(row.row_id, \"served_artifact\")\n    result = settle_open_row(row, default_probe, defer_if_unreachable=False)\n    print(\"settle_result\", result)\n    conn = sqlite3.connect(path)\n    conn.row_factory = sqlite3.Row\n    settled = conn.execute(\n        \"SELECT row_id, status, proof_class, defer_reason, proof_payload FROM propagation_ledger WHERE row_id='git_integration_worker:1043172c:sync_restart'\"\n    ).fetchone()\n    print(\"after\", dict(settled) if settled else None)\n    if settled and settled[\"proof_payload\"]:\n        print(\"proof_payload\", settled[\"proof_payload\"])\n    conn.close()\nPY"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 <<'PY'\nimport json\nimport os\n\nos.environ[\"CHARTER_RUNNER_OPERATOR_HOME\"] = \"/home/io\"\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class\nfrom charter_runner_store.propagation_terminal import settle_open_row, _probe_for_projection, default_probe\nfrom services.git_integration_worker.relay.propagation_probe import probe_for_row\nfrom implement_admission.propagation_row import PropagationRow, default_proof\n\nopen_rows = list_open_rows()\ntarget = [r for r in open_rows if r.row_id == \"git_integration_worker:1043172c:sync_restart\"][0]\nset_proof_class(target.row_id, \"served_artifact\")\nrow = PropagationRow(\n    service=target.service,\n    code_ref=target.code_ref,\n    safe_window=target.safe_window,\n    proof=default_proof(target.service),\n    proof_class=\"served_artifact\",\n)\npayload = probe_for_row(row)\nprint(\"probe_for_row\", json.dumps(payload, indent=2)[:4000] if payload else None)\n\n# settle with projection probe path\nfrom charter_runner_store.propagation_terminal import _projection_to_row\nfrom services.git_integration_worker.relay.propagation_probe import proof_observed\n\nproj_row = target\nprobe_payload = _probe_for_projection(proj_row)\nprint(\"projection_probe keys\", probe_payload.keys() if probe_payload else None)\nif probe_payload:\n    print(\"proof_observed\", proof_observed(_projection_to_row(proj_row), probe_payload))\n\nresult = settle_open_row(target, default_probe, defer_if_unreachable=False)\nprint(\"default_probe settle\", result)\n\n# Custom probe fn using probe_for_row\nfrom charter_runner_store.propagation_terminal import _proof_matches_projection\n\ndef row_probe(_service: str):\n    return _probe_for_projection(target)\n\nresult2 = settle_open_row(target, row_probe, defer_if_unreachable=False)\nprint(\"row_probe settle\", result2)\n\nfrom charter_runner_store.db import default_ledger_path\nimport sqlite3\nconn = sqlite3.connect(default_ledger_path())\nconn.row_factory = sqlite3.Row\nsettled = conn.execute(\n    \"SELECT status, proof_payload FROM propagation_ledger WHERE row_id='git_integration_worker:1043172c:sync_restart'\"\n).fetchone()\nprint(\"final status\", settled[\"status\"])\nprint(\"proof_payload\", settled[\"proof_payload\"])\nconn.close()\nPY",
          "detail": {
            "truncated": "{\"command\":\"/home/io/.venvs/universal/bin/python3 <<'PY'\\nimport json\\nimport os\\n\\nos.environ[\\\"CHARTER_RUNNER_OPERATOR_HOME\\\"] = \\\"/home/io\\\"\\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class\\nfrom charter_runner_store.propagation_terminal import settle_open_row, _probe_for_projection, default_probe\\nfrom services.git_integration_worker.relay.propagation_probe import probe_for_row\\nfrom implement_admission.propagation_row import PropagationRow, default_proof\\n\\nopen_rows = list_open_rows()\\ntarget = [r for r in open_rows if r.row_id == \\\"git_integration_worker:1043172c:sync_restart\\\"][0]\\nset_proof_class(target.row_id, \\\"served_artifact\\\")\\nrow = PropagationRow(\\n    service=target.service,\\n    code_ref=target.code_ref,\\n    safe_window=target.safe_window,\\n    proof=default_proof(target.service),\\n    proof_class=\\\"served_artifact\\\",\\n)\\npayload = probe_for_row(row)\\nprint(\\\"probe_for_row\\\", json.dumps(payload, indent=2)[:4000] if payload else None)\\n\\n# settle with projection probe path\\nfrom charter_runner_store.propagation_terminal import _projection_to_row\\nfrom services.git_integration_worker.relay.propagation_probe import proof_observed\\n\\nproj_row = target\\nprobe_payload = _probe_for_projection(proj_row)\\nprint(\\\"projection_probe keys\\\", probe_payload.keys() if probe_payload else None)\\nif probe_payload:\\n    print(\\\"proof_observed\\\", proof_observed(_projection_to_row(proj_row), probe_payload))\\n\\nresult = settle_open_row(target, default_probe, defer_if_unreachable=False)\\nprint(\\\"default_probe settle\\\", result)\\n\\n# Custom probe fn using probe_for_row\\nfrom charter_runner_store.propagation_terminal import _proof_matches_projection\\n\\ndef row_probe(_service: str):\\n    return _probe_for_projection(target)\\n\\nresult2 = settle_open_row(target, row_probe, defer_if_unreachable=False)\\nprint(\\\"row_probe settle\\\", result2)\\n\\nfrom charter_runner_store.db import default_ledger_path\\nimport sqlite3\\nconn = sqlite3.co"
          },
          "identity": "/home/io/.venvs/universal/bin/python3 <<'PY'\nimport json\nimport os\n\nos.environ[\"CHARTER_RUNNER_OPERATOR_HOME\"] = \"/home/io\"\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class\nfrom charter_runner_store.propagation_terminal import settle_open_row, _probe_for_projection, default_probe\nfrom services.git_integration_worker.relay.propagation_probe import probe_for_row\nfrom implement_admission.propagation_row import PropagationRow, default_proof\n\nopen_rows = list_open_rows()\ntarget = [r for r in open_rows if r.row_id == \"git_integration_worker:1043172c:sync_restart\"][0]\nset_proof_class(target.row_id, \"served_artifact\")\nrow = PropagationRow(\n    service=target.service,\n    code_ref=target.code_ref,\n    safe_window=target.safe_window,\n    proof=default_proof(target.service),\n    proof_class=\"served_artifact\",\n)\npayload = probe_for_row(row)\nprint(\"probe_for_row\", json.dumps(payload, indent=2)[:4000] if payload else None)\n\n# settle with projection probe path\nfrom charter_runner_store.propagation_terminal import _projection_to_row\nfrom services.git_integration_worker.relay.propagation_probe import proof_observed\n\nproj_row = target\nprobe_payload = _probe_for_projection(proj_row)\nprint(\"projection_probe keys\", probe_payload.keys() if probe_payload else None)\nif probe_payload:\n    print(\"proof_observed\", proof_observed(_projection_to_row(proj_row), probe_payload))\n\nresult = settle_open_row(target, default_probe, defer_if_unreachable=False)\nprint(\"default_probe settle\", result)\n\n# Custom probe fn using probe_for_row\nfrom charter_runner_store.propagation_terminal import _proof_matches_projection\n\ndef row_probe(_service: str):\n    return _probe_for_projection(target)\n\nresult2 = settle_open_row(target, row_probe, defer_if_unreachable=False)\nprint(\"row_probe settle\", result2)\n\nfrom charter_runner_store.db import default_ledger_path\nimport sqlite3\nconn = sqlite3.connect(default_ledger_path())\nconn.row_factory = sqlite3.Row\nsettled = conn.execute(\n    \"SELECT status, proof_payload FROM propagation_ledger WHERE row_id='git_integration_worker:1043172c:sync_restart'\"\n).fetchone()\nprint(\"final status\", settled[\"status\"])\nprint(\"proof_payload\", settled[\"proof_payload\"])\nconn.close()\nPY"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/charter_runner_store/propagation_terminal.py"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 <<'PY'\nimport os\nos.environ[\"CHARTER_RUNNER_OPERATOR_HOME\"] = \"/home/io\"\nfrom services.git_integration_worker.relay.propagation_served_artifact import SERVED_ARTIFACT_DESCRIPTORS\nfor service, desc in SERVED_ARTIFACT_DESCRIPTORS.items():\n    urls = [(s.name, s.url, desc.expected_x_mcp_count) for s in desc.surfaces]\n    print(service, urls)\nPY",
          "detail": {
            "command": "/home/io/.venvs/universal/bin/python3 <<'PY'\nimport os\nos.environ[\"CHARTER_RUNNER_OPERATOR_HOME\"] = \"/home/io\"\nfrom services.git_integration_worker.relay.propagation_served_artifact import SERVED_ARTIFACT_DESCRIPTORS\nfor service, desc in SERVED_ARTIFACT_DESCRIPTORS.items():\n    urls = [(s.name, s.url, desc.expected_x_mcp_count) for s in desc.surfaces]\n    print(service, urls)\nPY",
            "workingDirectory": "/mnt/torus/projects/universal-llm-gateway",
            "timeout": 30000
          },
          "identity": "/home/io/.venvs/universal/bin/python3 <<'PY'\nimport os\nos.environ[\"CHARTER_RUNNER_OPERATOR_HOME\"] = \"/home/io\"\nfrom services.git_integration_worker.relay.propagation_served_artifact import SERVED_ARTIFACT_DESCRIPTORS\nfor service, desc in SERVED_ARTIFACT_DESCRIPTORS.items():\n    urls = [(s.name, s.url, desc.expected_x_mcp_count) for s in desc.surfaces]\n    print(service, urls)\nPY"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 -m pytest libs/deploy_identity/test_code_ref_relation.py services/git_integration_worker/tests/test_propagation_probe.py services/git_integration_worker/tests/test_propagation_served_artifact.py libs/charter_runner_store/test_propagation_terminal.py -q 2>&1 | tail -3",
          "detail": {
            "command": "/home/io/.venvs/universal/bin/python3 -m pytest libs/deploy_identity/test_code_ref_relation.py services/git_integration_worker/tests/test_propagation_probe.py services/git_integration_worker/tests/test_propagation_served_artifact.py libs/charter_runner_store/test_propagation_terminal.py -q 2>&1 | tail -3",
            "workingDirectory": "/mnt/torus/projects/universal-llm-gateway",
            "timeout": 30000
          },
          "identity": "/home/io/.venvs/universal/bin/python3 -m pytest libs/deploy_identity/test_code_ref_relation.py services/git_integration_worker/tests/test_propagation_probe.py services/git_integration_worker/tests/test_propagation_served_artifact.py libs/charter_runner_store/test_propagation_terminal.py -q 2>&1 | tail -3"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py -q 2>&1 | tail -8",
          "detail": {
            "command": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py -q 2>&1 | tail -8",
            "workingDirectory": "/mnt/torus/projects/universal-llm-gateway",
            "timeout": 30000
          },
          "identity": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py -q 2>&1 | tail -8"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py::test_execute_closes_on_proof_not_on_restart_status -q 2>&1",
          "detail": {
            "command": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py::test_execute_closes_on_proof_not_on_restart_status -q 2>&1",
            "workingDirectory": "/mnt/torus/projects/universal-llm-gateway",
            "timeout": 30000
          },
          "identity": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py::test_execute_closes_on_proof_not_on_restart_status -q 2>&1"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py -q 2>&1 | tail -5",
          "detail": {
            "command": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py -q 2>&1 | tail -5",
            "workingDirectory": "/mnt/torus/projects/universal-llm-gateway",
            "timeout": 30000
          },
          "identity": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py -q 2>&1 | tail -5"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py -q 2>&1 | tail -3",
          "detail": {
            "command": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py -q 2>&1 | tail -3",
            "workingDirectory": "/mnt/torus/projects/universal-llm-gateway",
            "timeout": 30000
          },
          "identity": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py -q 2>&1 | tail -3"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py::test_execute_defers_when_i2_blocks_giw -q 2>&1",
          "detail": {
            "command": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py::test_execute_defers_when_i2_blocks_giw -q 2>&1",
            "workingDirectory": "/mnt/torus/projects/universal-llm-gateway",
            "timeout": 30000
          },
          "identity": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py::test_execute_defers_when_i2_blocks_giw -q 2>&1"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py -q 2>&1 | tail -3",
          "detail": {
            "command": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py -q 2>&1 | tail -3",
            "workingDirectory": "/mnt/torus/projects/universal-llm-gateway",
            "timeout": 30000
          },
          "identity": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py -q 2>&1 | tail -3"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py -q 2>&1 | tail -3",
          "detail": {
            "command": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py -q 2>&1 | tail -3",
            "workingDirectory": "/mnt/torus/projects/universal-llm-gateway",
            "timeout": 30000
          },
          "identity": "/home/io/.venvs/universal/bin/python3 -m pytest scripts/model_manager/ui/controller/charter_runner/test_propagation_execute.py -q 2>&1 | tail -3"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 <<'PY'\nimport json, os, sqlite3\nos.environ[\"CHARTER_RUNNER_OPERATOR_HOME\"] = \"/home/io\"\nfrom charter_runner_store.db import default_ledger_path\nconn = sqlite3.connect(default_ledger_path())\nconn.row_factory = sqlite3.Row\nrow = conn.execute(\n    \"SELECT status, proof_class, defer_reason, proof_payload FROM propagation_ledger WHERE row_id='git_integration_worker:1043172c:sync_restart'\"\n).fetchone()\nprint(json.dumps(dict(row), indent=2))\nPY",
          "detail": {
            "truncated": "{\"command\":\"/home/io/.venvs/universal/bin/python3 <<'PY'\\nimport json, os, sqlite3\\nos.environ[\\\"CHARTER_RUNNER_OPERATOR_HOME\\\"] = \\\"/home/io\\\"\\nfrom charter_runner_store.db import default_ledger_path\\nconn = sqlite3.connect(default_ledger_path())\\nconn.row_factory = sqlite3.Row\\nrow = conn.execute(\\n    \\\"SELECT status, proof_class, defer_reason, proof_payload FROM propagation_ledger WHERE row_id='git_integration_worker:1043172c:sync_restart'\\\"\\n).fetchone()\\nprint(json.dumps(dict(row), indent=2))\\nPY\",\"workingDirectory\":\"/mnt/torus/projects/universal-llm-gateway\",\"timeout\":30000}"
          },
          "identity": "/home/io/.venvs/universal/bin/python3 <<'PY'\nimport json, os, sqlite3\nos.environ[\"CHARTER_RUNNER_OPERATOR_HOME\"] = \"/home/io\"\nfrom charter_runner_store.db import default_ledger_path\nconn = sqlite3.connect(default_ledger_path())\nconn.row_factory = sqlite3.Row\nrow = conn.execute(\n    \"SELECT status, proof_class, defer_reason, proof_payload FROM propagation_ledger WHERE row_id='git_integration_worker:1043172c:sync_restart'\"\n).fetchone()\nprint(json.dumps(dict(row), indent=2))\nPY"
        },
        {
          "op": "observed",
          "target": "/mnt/torus/projects",
          "detail": null,
          "identity": "/mnt/torus/projects"
        },
        {
          "op": "observed",
          "target": ".",
          "detail": null,
          "identity": "."
        },
        {
          "op": "observed",
          "target": "libs/deploy_identity/code_version.py",
          "detail": null,
          "identity": "libs/deploy_identity/code_version.py"
        },
        {
          "op": "observed",
          "target": "libs/openapi_mcp/binding.py",
          "detail": null,
          "identity": "libs/openapi_mcp/binding.py"
        },
        {
          "op": "observed",
          "target": "services/git_integration_worker/tests/test_giw_stargate_proxy_path_coverage.py",
          "detail": null,
          "identity": "services/git_integration_worker/tests/test_giw_stargate_proxy_path_coverage.py"
        },
        {
          "op": "observed",
          "target": "libs/git_integrate/git_cas.py",
          "detail": null,
          "identity": "libs/git_integrate/git_cas.py"
        },
        {
          "op": "observed",
          "target": "libs/cdp_ask/app.py",
          "detail": null,
          "identity": "libs/cdp_ask/app.py"
        },
        {
          "op": "observed",
          "target": "libs/transport_utils/__init__.py",
          "detail": null,
          "identity": "libs/transport_utils/__init__.py"
        },
        {
          "op": "observed",
          "target": "scripts/model_manager/ui/controller/charter_runner/propagation_execute.py",
          "detail": null,
          "identity": "scripts/model_manager/ui/controller/charter_runner/propagation_execute.py"
        },
        {
          "op": "observed",
          "target": "libs/transport_utils/client_factory.py",
          "detail": null,
          "identity": "libs/transport_utils/client_factory.py"
        },
        {
          "op": "observed",
          "target": "libs/agent_bus_store",
          "detail": null,
          "identity": "libs/agent_bus_store"
        },
        {
          "op": "observed",
          "target": "services/rag",
          "detail": null,
          "identity": "services/rag"
        },
        {
          "op": "observed",
          "target": "libs/agent_bus_store/openapi_mcp/_route_map.py",
          "detail": null,
          "identity": "libs/agent_bus_store/openapi_mcp/_route_map.py"
        },
        {
          "op": "observed",
          "target": "services/rag/openapi_mcp/_route_map.py",
          "detail": null,
          "identity": "services/rag/openapi_mcp/_route_map.py"
        },
        {
          "op": "observed",
          "target": "libs/cortex_store/openapi_mcp/_route_map.py",
          "detail": null,
          "identity": "libs/cortex_store/openapi_mcp/_route_map.py"
        },
        {
          "op": "observed",
          "target": "libs/cortex_store",
          "detail": null,
          "identity": "libs/cortex_store"
        },
        {
          "op": "observed",
          "target": "libs/agent_bus_store/server.py",
          "detail": null,
          "identity": "libs/agent_bus_store/server.py"
        },
        {
          "op": "observed",
          "target": "libs/cortex_store/main.py",
          "detail": null,
          "identity": "libs/cortex_store/main.py"
        },
        {
          "op": "observed",
          "target": "services/universal-stargate",
          "detail": null,
          "identity": "services/universal-stargate"
        },
        {
          "op": "observed",
          "target": "libs/deploy_identity/mcp_health_probe_url.py",
          "detail": null,
          "identity": "libs/deploy_identity/mcp_health_probe_url.py"
        },
        {
          "op": "observed",
          "target": "services/universal-stargate/systems/proxy",
          "detail": null,
          "identity": "services/universal-stargate/systems/proxy"
        },
        {
          "op": "observed",
          "target": "scripts/model_manager/ui/controller/service_ctl/cortex_api_service.py",
          "detail": null,
          "identity": "scripts/model_manager/ui/controller/service_ctl/cortex_api_service.py"
        },
        {
          "op": "observed",
          "target": "scripts/model_manager/ui/controller/service_config.py",
          "detail": null,
          "identity": "scripts/model_manager/ui/controller/service_config.py"
        },
        {
          "op": "observed",
          "target": "services/universal-stargate/systems/proxy/routers/api/git.py",
          "detail": null,
          "identity": "services/universal-stargate/systems/proxy/routers/api/git.py"
        },
        {
          "op": "observed",
          "target": "libs/implement_admission/test_propagation_row.py",
          "detail": null,
          "identity": "libs/implement_admission/test_propagation_row.py"
        },
        {
          "op": "observed",
          "target": "libs/charter_runner_store",
          "detail": null,
          "identity": "libs/charter_runner_store"
        },
        {
          "op": "observed",
          "target": "libs/charter_runner_store/db.py",
          "detail": null,
          "identity": "libs/charter_runner_store/db.py"
        }
      ],
      "cross_check": null
    },
    "agent_bus": {
      "surface": "agent_bus",
      "source": "wrapper",
      "entries": [
        {
          "op": "agent_bus.reply",
          "target": "6638",
          "detail": null,
          "identity": "6638#closeout"
        }
      ],
      "cross_check": null
    },
    "service": {
      "surface": "service",
      "source": "wrapper",
      "entries": [
        {
          "op": "emit_implement_closeout_trigger",
          "target": "auto-9ca4df4d4a88",
          "detail": null,
          "identity": "auto-9ca4df4d4a88"
        }
      ],
      "cross_check": null
    }
  },
  "coverage": {
    "repo": "complete"
  },
  "external_effects": "scoped_out"
}