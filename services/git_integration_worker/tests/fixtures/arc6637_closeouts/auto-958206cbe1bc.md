status: complete

ac_verdict: AC1 PASS — worker restarted (pid 3701125 → 3871894; pre-restart pid not running). AC2 PASS — row git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart upgraded to served_artifact and closed; proof via HTTP to worker-served OpenAPI. AC3 PASS — this closeout uses plain field: value lines only (no bold markers, no ATX field headings). AC4 PASS — parser validity rule was wrong; fixed _normalize_row to permit omitted code_ref per documented template. AC5 PASS — relay_parse_failed reachable in running worker import path; arc6637 relay tests pass.

deltas_to_spec: SCOPE DELTA — done: AC1 process identity probe; AC2 propagation row upgrade+settle with served_artifact proof payload; AC3 plain-colon closeout format; AC4 parser fix + test; AC5 relay_parse_failed reachability check. not done: G4/G5/G6; further restarts; repo writes beyond AC4 fix.

decisions_taken: Upgraded row proof_class process_live → served_artifact before settle. Fixed propagation_block_parser._normalize_row (not the documented template) — omitted code_ref is permitted and resolves to HEAD at mint via row_from_mapping_strict.

effects: Row git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart status=closed proof_class=served_artifact. Open propagation rows 18→17. Edited libs/implement_admission/propagation_block_parser.py and propagation_row.py; added test_omitted_code_ref_not_invalid_shape.

evidence: AC1 pre-restart pid=3701125 (packet assumed_state). Post-restart: manage health service=git_integration_worker status=running detail="PID 3871894 (12m 14s)". ps -p 3701125 → "PID 3701125 not running". ps -p 3871894 STARTED="Fri Jul 31 21:25:38 2026". curl liveness uptime_s=766.439 code_version=d3e17d54b66276a350769501beb90c2988ff3bf1. AC2 settled proof_payload: proof_class=served_artifact x_mcp_count=9 byte_identical=true code_version=d3e17d54b66276a350769501beb90c2988ff3bf1 surfaces.direct_8091.url=http://127.0.0.1:8091/api/v1/git/openapi.json surfaces.stargate_9999.bytes_sha256=677edf45e8a89267b67f868936cbdea5be5112e4fe35e1b634c734a96d1b9679. settle outcome=closed detail="proof matched code_ref=d3e17d54b66276a350769501beb90c2988ff3bf1". AC4 old rule at propagation_block_parser.py line 27-28: `if not isinstance(code_ref, str) or not code_ref.strip(): return None`. AC5 pytest: test_arc6637_relay_parse_failed_when_extraction_fails PASSED; RELAY_PARSE_FAILED_STATUS=relay_parse_failed; _extract_plain_same_line OK.

next: none within arc 6637 verify scope.

open forks: AC4 parser fix is on disk — live in worker after sync_restart (libs import at process boot). AC2 ledger settle has no worker HTTP endpoint; proof probes hit worker HTTP, close_row runs in-seat via charter_runner_store.propagation_terminal.settle_open_row (same path drain supervisor uses post-restart).

access: reached: manage(action=health) git_integration_worker; curl 127.0.0.1:8091/api/v1/git/cursor-auto/liveness and /openapi.json; charter_runner_store propagation_ledger SQLite; worker Python import path for closeout_relay modules; ps/procfs. not reached: dedicated worker HTTP settle endpoint (does not exist).

coverage: propagation ledger query 1 row_id git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart (closed post-settle). open rows inventory count=18 pre-settle count=17 post-settle. pytest 6 tests in test_propagation_block_parser.py + 2 arc6637 relay tests — all passed 2026-08-01T04:38Z.

model_actual: cursor/composer-2.5 (dispatch default; no override requested).

## effects_manifest

{
  "schema_version": 1,
  "dispatch_id": "auto-958206cbe1bc",
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
          "target": "ps -p 3701125 -o pid,lstart,cmd 2>/dev/null || echo \"PID 3701125 not running\"; pgrep -af git_integration_worker | head -5; ps aux | grep -E '[g]it_integration_worker' | head -3",
          "detail": {
            "command": "ps -p 3701125 -o pid,lstart,cmd 2>/dev/null || echo \"PID 3701125 not running\"; pgrep -af git_integration_worker | head -5; ps aux | grep -E '[g]it_integration_worker' | head -3",
            "timeout": 30000
          },
          "identity": "ps -p 3701125 -o pid,lstart,cmd 2>/dev/null || echo \"PID 3701125 not running\"; pgrep -af git_integration_worker | head -5; ps aux | grep -E '[g]it_integration_worker' | head -3"
        },
        {
          "op": "shell",
          "target": "curl -s http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness | \"$HOME/.venvs/universal/bin/python\" -m json.tool 2>/dev/null | head -30",
          "detail": {
            "command": "curl -s http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness | \"$HOME/.venvs/universal/bin/python\" -m json.tool 2>/dev/null | head -30",
            "timeout": 30000
          },
          "identity": "curl -s http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness | \"$HOME/.venvs/universal/bin/python\" -m json.tool 2>/dev/null | head -30"
        },
        {
          "op": "shell",
          "target": "curl -sv http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness 2>&1 | tail -20; echo \"---\"; curl -s http://127.0.0.1:8091/api/v1/git/openapi.json 2>&1 | \"$HOME/.venvs/universal/bin/python\" -c \"import sys,json; d=json.load(sys.stdin); print('x-mcp routes:', sum(1 for p in d.get('paths',{}).values() for m in p.values() if isinstance(m,dict) and 'x-mcp' in m)); print('code_version field absent from openapi')\"",
          "detail": {
            "command": "curl -sv http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness 2>&1 | tail -20; echo \"---\"; curl -s http://127.0.0.1:8091/api/v1/git/openapi.json 2>&1 | \"$HOME/.venvs/universal/bin/python\" -c \"import sys,json; d=json.load(sys.stdin); print('x-mcp routes:', sum(1 for p in d.get('paths',{}).values() for m in p.values() if isinstance(m,dict) and 'x-mcp' in m)); print('code_version field absent from openapi')\"",
            "timeout": 30000
          },
          "identity": "curl -sv http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness 2>&1 | tail -20; echo \"---\"; curl -s http://127.0.0.1:8091/api/v1/git/openapi.json 2>&1 | \"$HOME/.venvs/universal/bin/python\" -c \"import sys,json; d=json.load(sys.stdin); print('x-mcp routes:', sum(1 for p in d.get('paths',{}).values() for m in p.values() if isinstance(m,dict) and 'x-mcp' in m)); print('code_version field absent from openapi')\""
        },
        {
          "op": "shell",
          "target": "source ~/.venvs/universal/bin/activate && python3 << 'PY'\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class, close_row\nfrom charter_runner_store.propagation_terminal import settle_open_row, default_probe\nfrom services.git_integration_worker.relay.propagation_probe import probe_for_row\nfrom implement_admission.propagation_row import PropagationRow, default_proof\nfrom deploy_identity.code_version import normalize_code_ref\n\nROW_ID = \"git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart\"\nrows = list_open_rows()\nprint(\"open rows:\", len(rows))\nfor r in rows:\n    print(f\"  {r.row_id} proof_class={r.proof_class} defer={r.defer_reason}\")\n\ntarget = [r for r in rows if r.row_id == ROW_ID]\nif target:\n    r = target[0]\n    print(\"\\ntarget found:\", r)\n    # upgrade proof class\n    set_proof_class(ROW_ID, \"served_artifact\")\n    r2 = [x for x in list_open_rows() if x.row_id == ROW_ID][0]\n    print(\"after upgrade:\", r2.proof_class)\n    # probe via worker HTTP (probe_for_row)\n    row = PropagationRow(service=r2.service, code_ref=r2.code_ref, safe_window=r2.safe_window, proof=default_proof(r2.service), proof_class=r2.proof_class)\n    payload = probe_for_row(row)\n    print(\"\\nprobe payload keys:\", list(payload.keys()) if payload else None)\n    if payload:\n        print(\"proof_class:\", payload.get(\"proof_class\"))\n        print(\"x_mcp_count:\", payload.get(\"x_mcp_count\"))\n        print(\"byte_identical:\", payload.get(\"byte_identical\"))\n        print(\"code_version:\", payload.get(\"code_version\"))\n    from services.git_integration_worker.relay.propagation_probe import proof_observed\n    print(\"proof_observed:\", proof_observed(row, payload))\n    result = settle_open_row(r2, default_probe)\n    print(\"\\nsettle result:\", result)\n    rows_after = list_open_rows()\n    print(\"open rows after:\", len(rows_after))\nelse:\n    print(\"target row not open - checking closed status\")\nPY",
          "detail": {
            "truncated": "{\"command\":\"source ~/.venvs/universal/bin/activate && python3 << 'PY'\\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class, close_row\\nfrom charter_runner_store.propagation_terminal import settle_open_row, default_probe\\nfrom services.git_integration_worker.relay.propagation_probe import probe_for_row\\nfrom implement_admission.propagation_row import PropagationRow, default_proof\\nfrom deploy_identity.code_version import normalize_code_ref\\n\\nROW_ID = \\\"git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart\\\"\\nrows = list_open_rows()\\nprint(\\\"open rows:\\\", len(rows))\\nfor r in rows:\\n    print(f\\\"  {r.row_id} proof_class={r.proof_class} defer={r.defer_reason}\\\")\\n\\ntarget = [r for r in rows if r.row_id == ROW_ID]\\nif target:\\n    r = target[0]\\n    print(\\\"\\\\ntarget found:\\\", r)\\n    # upgrade proof class\\n    set_proof_class(ROW_ID, \\\"served_artifact\\\")\\n    r2 = [x for x in list_open_rows() if x.row_id == ROW_ID][0]\\n    print(\\\"after upgrade:\\\", r2.proof_class)\\n    # probe via worker HTTP (probe_for_row)\\n    row = PropagationRow(service=r2.service, code_ref=r2.code_ref, safe_window=r2.safe_window, proof=default_proof(r2.service), proof_class=r2.proof_class)\\n    payload = probe_for_row(row)\\n    print(\\\"\\\\nprobe payload keys:\\\", list(payload.keys()) if payload else None)\\n    if payload:\\n        print(\\\"proof_class:\\\", payload.get(\\\"proof_class\\\"))\\n        print(\\\"x_mcp_count:\\\", payload.get(\\\"x_mcp_count\\\"))\\n        print(\\\"byte_identical:\\\", payload.get(\\\"byte_identical\\\"))\\n        print(\\\"code_version:\\\", payload.get(\\\"code_version\\\"))\\n    from services.git_integration_worker.relay.propagation_probe import proof_observed\\n    print(\\\"proof_observed:\\\", proof_observed(row, payload))\\n    result = settle_open_row(r2, default_probe)\\n    print(\\\"\\\\nsettle result:\\\", result)\\n    rows_after = list_open_rows()\\n    print(\\\"open rows after:\\\", len(rows_after))\\nelse:\\n    print(\\\"target row not "
          },
          "identity": "source ~/.venvs/universal/bin/activate && python3 << 'PY'\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class, close_row\nfrom charter_runner_store.propagation_terminal import settle_open_row, default_probe\nfrom services.git_integration_worker.relay.propagation_probe import probe_for_row\nfrom implement_admission.propagation_row import PropagationRow, default_proof\nfrom deploy_identity.code_version import normalize_code_ref\n\nROW_ID = \"git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart\"\nrows = list_open_rows()\nprint(\"open rows:\", len(rows))\nfor r in rows:\n    print(f\"  {r.row_id} proof_class={r.proof_class} defer={r.defer_reason}\")\n\ntarget = [r for r in rows if r.row_id == ROW_ID]\nif target:\n    r = target[0]\n    print(\"\\ntarget found:\", r)\n    # upgrade proof class\n    set_proof_class(ROW_ID, \"served_artifact\")\n    r2 = [x for x in list_open_rows() if x.row_id == ROW_ID][0]\n    print(\"after upgrade:\", r2.proof_class)\n    # probe via worker HTTP (probe_for_row)\n    row = PropagationRow(service=r2.service, code_ref=r2.code_ref, safe_window=r2.safe_window, proof=default_proof(r2.service), proof_class=r2.proof_class)\n    payload = probe_for_row(row)\n    print(\"\\nprobe payload keys:\", list(payload.keys()) if payload else None)\n    if payload:\n        print(\"proof_class:\", payload.get(\"proof_class\"))\n        print(\"x_mcp_count:\", payload.get(\"x_mcp_count\"))\n        print(\"byte_identical:\", payload.get(\"byte_identical\"))\n        print(\"code_version:\", payload.get(\"code_version\"))\n    from services.git_integration_worker.relay.propagation_probe import proof_observed\n    print(\"proof_observed:\", proof_observed(row, payload))\n    result = settle_open_row(r2, default_probe)\n    print(\"\\nsettle result:\", result)\n    rows_after = list_open_rows()\n    print(\"open rows after:\", len(rows_after))\nelse:\n    print(\"target row not open - checking closed status\")\nPY"
        },
        {
          "op": "shell",
          "target": "\"$HOME/.venvs/universal/bin/python\" << 'PY'\nimport sys\nsys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway')\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class\nfrom charter_runner_store.propagation_terminal import settle_open_row, default_probe\nfrom services.git_integration_worker.relay.propagation_probe import probe_for_row, proof_observed\nfrom implement_admission.propagation_row import PropagationRow, default_proof\n\nROW_ID = \"git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart\"\nrows = list_open_rows()\nprint(\"open rows:\", len(rows))\nfor r in rows:\n    print(f\"  {r.row_id} proof_class={r.proof_class} defer={r.defer_reason}\")\n\ntarget = [r for r in rows if r.row_id == ROW_ID]\nif target:\n    r = target[0]\n    set_proof_class(ROW_ID, \"served_artifact\")\n    r2 = [x for x in list_open_rows() if x.row_id == ROW_ID][0]\n    row = PropagationRow(service=r2.service, code_ref=r2.code_ref, safe_window=r2.safe_window, proof=default_proof(r2.service), proof_class=r2.proof_class)\n    payload = probe_for_row(row)\n    print(\"probe ok:\", payload is not None)\n    if payload:\n        import json\n        print(json.dumps({k: payload[k] for k in ['proof_class','x_mcp_count','byte_identical','code_version','expected_x_mcp_count'] if k in payload}, indent=2))\n    print(\"proof_observed:\", proof_observed(row, payload))\n    result = settle_open_row(r2, default_probe)\n    print(\"settle:\", result)\n    print(\"open after:\", len(list_open_rows()))\nelse:\n    print(\"ROW NOT OPEN\")\n    # check if closed in db\n    from charter_runner_store.db import open_ledger_db\n    db = open_ledger_db()\n    cur = db.execute(\"SELECT status, proof_payload, proof_class FROM propagation_ledger WHERE row_id=?\", (ROW_ID,))\n    row = cur.fetchone()\n    print(\"db row:\", dict(row) if row else None)\n    db.close()\nPY",
          "detail": {
            "truncated": "{\"command\":\"\\\"$HOME/.venvs/universal/bin/python\\\" << 'PY'\\nimport sys\\nsys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway')\\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class\\nfrom charter_runner_store.propagation_terminal import settle_open_row, default_probe\\nfrom services.git_integration_worker.relay.propagation_probe import probe_for_row, proof_observed\\nfrom implement_admission.propagation_row import PropagationRow, default_proof\\n\\nROW_ID = \\\"git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart\\\"\\nrows = list_open_rows()\\nprint(\\\"open rows:\\\", len(rows))\\nfor r in rows:\\n    print(f\\\"  {r.row_id} proof_class={r.proof_class} defer={r.defer_reason}\\\")\\n\\ntarget = [r for r in rows if r.row_id == ROW_ID]\\nif target:\\n    r = target[0]\\n    set_proof_class(ROW_ID, \\\"served_artifact\\\")\\n    r2 = [x for x in list_open_rows() if x.row_id == ROW_ID][0]\\n    row = PropagationRow(service=r2.service, code_ref=r2.code_ref, safe_window=r2.safe_window, proof=default_proof(r2.service), proof_class=r2.proof_class)\\n    payload = probe_for_row(row)\\n    print(\\\"probe ok:\\\", payload is not None)\\n    if payload:\\n        import json\\n        print(json.dumps({k: payload[k] for k in ['proof_class','x_mcp_count','byte_identical','code_version','expected_x_mcp_count'] if k in payload}, indent=2))\\n    print(\\\"proof_observed:\\\", proof_observed(row, payload))\\n    result = settle_open_row(r2, default_probe)\\n    print(\\\"settle:\\\", result)\\n    print(\\\"open after:\\\", len(list_open_rows()))\\nelse:\\n    print(\\\"ROW NOT OPEN\\\")\\n    # check if closed in db\\n    from charter_runner_store.db import open_ledger_db\\n    db = open_ledger_db()\\n    cur = db.execute(\\\"SELECT status, proof_payload, proof_class FROM propagation_ledger WHERE row_id=?\\\", (ROW_ID,))\\n    row = cur.fetchone()\\n    print(\\\"db row:\\\", dict(row) if row else None)\\n    db.close()\\nPY\",\"timeout\":30000}"
          },
          "identity": "\"$HOME/.venvs/universal/bin/python\" << 'PY'\nimport sys\nsys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway')\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class\nfrom charter_runner_store.propagation_terminal import settle_open_row, default_probe\nfrom services.git_integration_worker.relay.propagation_probe import probe_for_row, proof_observed\nfrom implement_admission.propagation_row import PropagationRow, default_proof\n\nROW_ID = \"git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart\"\nrows = list_open_rows()\nprint(\"open rows:\", len(rows))\nfor r in rows:\n    print(f\"  {r.row_id} proof_class={r.proof_class} defer={r.defer_reason}\")\n\ntarget = [r for r in rows if r.row_id == ROW_ID]\nif target:\n    r = target[0]\n    set_proof_class(ROW_ID, \"served_artifact\")\n    r2 = [x for x in list_open_rows() if x.row_id == ROW_ID][0]\n    row = PropagationRow(service=r2.service, code_ref=r2.code_ref, safe_window=r2.safe_window, proof=default_proof(r2.service), proof_class=r2.proof_class)\n    payload = probe_for_row(row)\n    print(\"probe ok:\", payload is not None)\n    if payload:\n        import json\n        print(json.dumps({k: payload[k] for k in ['proof_class','x_mcp_count','byte_identical','code_version','expected_x_mcp_count'] if k in payload}, indent=2))\n    print(\"proof_observed:\", proof_observed(row, payload))\n    result = settle_open_row(r2, default_probe)\n    print(\"settle:\", result)\n    print(\"open after:\", len(list_open_rows()))\nelse:\n    print(\"ROW NOT OPEN\")\n    # check if closed in db\n    from charter_runner_store.db import open_ledger_db\n    db = open_ledger_db()\n    cur = db.execute(\"SELECT status, proof_payload, proof_class FROM propagation_ledger WHERE row_id=?\", (ROW_ID,))\n    row = cur.fetchone()\n    print(\"db row:\", dict(row) if row else None)\n    db.close()\nPY"
        },
        {
          "op": "shell",
          "target": "which python3; ls -la /home/io/.venvs/universal/bin/python 2>&1; ls -la ~/.venvs/universal/bin/python 2>&1",
          "detail": {
            "command": "which python3; ls -la /home/io/.venvs/universal/bin/python 2>&1; ls -la ~/.venvs/universal/bin/python 2>&1",
            "timeout": 30000
          },
          "identity": "which python3; ls -la /home/io/.venvs/universal/bin/python 2>&1; ls -la ~/.venvs/universal/bin/python 2>&1"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 << 'PY'\nimport sys\nsys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway')\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class\nfrom charter_runner_store.propagation_terminal import settle_open_row, default_probe\nfrom services.git_integration_worker.relay.propagation_probe import probe_for_row, proof_observed\nfrom implement_admission.propagation_row import PropagationRow, default_proof\n\nROW_ID = \"git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart\"\nrows = list_open_rows()\nprint(\"open rows:\", len(rows))\nfor r in rows:\n    print(f\"  {r.row_id} proof_class={r.proof_class} defer={r.defer_reason}\")\n\ntarget = [r for r in rows if r.row_id == ROW_ID]\nif target:\n    r = target[0]\n    set_proof_class(ROW_ID, \"served_artifact\")\n    r2 = [x for x in list_open_rows() if x.row_id == ROW_ID][0]\n    row = PropagationRow(service=r2.service, code_ref=r2.code_ref, safe_window=r2.safe_window, proof=default_proof(r2.service), proof_class=r2.proof_class)\n    payload = probe_for_row(row)\n    print(\"probe ok:\", payload is not None)\n    if payload:\n        import json\n        slim = {k: payload[k] for k in ['proof_class','x_mcp_count','byte_identical','code_version','expected_x_mcp_count','code_ref_relation'] if k in payload}\n        print(json.dumps(slim, indent=2))\n    print(\"proof_observed:\", proof_observed(row, payload))\n    result = settle_open_row(r2, default_probe)\n    print(\"settle outcome:\", result.outcome, result.detail)\n    print(\"open after:\", len(list_open_rows()))\nelse:\n    print(\"ROW NOT OPEN\")\n    from charter_runner_store.db import open_ledger_db\n    import json\n    db = open_ledger_db()\n    cur = db.execute(\"SELECT status, proof_class, proof_payload FROM propagation_ledger WHERE row_id=?\", (ROW_ID,))\n    row = cur.fetchone()\n    if row:\n        d = dict(row)\n        pp = d.get('proof_payload')\n        if pp:\n            try:\n                d['proof_payload'] = json.loads(pp)\n            except Exception:\n                pass\n        print(\"db row:\", json.dumps(d, indent=2, default=str))\n    else:\n        print(\"row not in db\")\n    db.close()\nPY",
          "detail": {
            "truncated": "{\"command\":\"/home/io/.venvs/universal/bin/python3 << 'PY'\\nimport sys\\nsys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway')\\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class\\nfrom charter_runner_store.propagation_terminal import settle_open_row, default_probe\\nfrom services.git_integration_worker.relay.propagation_probe import probe_for_row, proof_observed\\nfrom implement_admission.propagation_row import PropagationRow, default_proof\\n\\nROW_ID = \\\"git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart\\\"\\nrows = list_open_rows()\\nprint(\\\"open rows:\\\", len(rows))\\nfor r in rows:\\n    print(f\\\"  {r.row_id} proof_class={r.proof_class} defer={r.defer_reason}\\\")\\n\\ntarget = [r for r in rows if r.row_id == ROW_ID]\\nif target:\\n    r = target[0]\\n    set_proof_class(ROW_ID, \\\"served_artifact\\\")\\n    r2 = [x for x in list_open_rows() if x.row_id == ROW_ID][0]\\n    row = PropagationRow(service=r2.service, code_ref=r2.code_ref, safe_window=r2.safe_window, proof=default_proof(r2.service), proof_class=r2.proof_class)\\n    payload = probe_for_row(row)\\n    print(\\\"probe ok:\\\", payload is not None)\\n    if payload:\\n        import json\\n        slim = {k: payload[k] for k in ['proof_class','x_mcp_count','byte_identical','code_version','expected_x_mcp_count','code_ref_relation'] if k in payload}\\n        print(json.dumps(slim, indent=2))\\n    print(\\\"proof_observed:\\\", proof_observed(row, payload))\\n    result = settle_open_row(r2, default_probe)\\n    print(\\\"settle outcome:\\\", result.outcome, result.detail)\\n    print(\\\"open after:\\\", len(list_open_rows()))\\nelse:\\n    print(\\\"ROW NOT OPEN\\\")\\n    from charter_runner_store.db import open_ledger_db\\n    import json\\n    db = open_ledger_db()\\n    cur = db.execute(\\\"SELECT status, proof_class, proof_payload FROM propagation_ledger WHERE row_id=?\\\", (ROW_ID,))\\n    row = cur.fetchone()\\n    if row:\\n        d = dict(row)\\n        pp = d.get('proof_payload')\\n  "
          },
          "identity": "/home/io/.venvs/universal/bin/python3 << 'PY'\nimport sys\nsys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway')\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class\nfrom charter_runner_store.propagation_terminal import settle_open_row, default_probe\nfrom services.git_integration_worker.relay.propagation_probe import probe_for_row, proof_observed\nfrom implement_admission.propagation_row import PropagationRow, default_proof\n\nROW_ID = \"git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart\"\nrows = list_open_rows()\nprint(\"open rows:\", len(rows))\nfor r in rows:\n    print(f\"  {r.row_id} proof_class={r.proof_class} defer={r.defer_reason}\")\n\ntarget = [r for r in rows if r.row_id == ROW_ID]\nif target:\n    r = target[0]\n    set_proof_class(ROW_ID, \"served_artifact\")\n    r2 = [x for x in list_open_rows() if x.row_id == ROW_ID][0]\n    row = PropagationRow(service=r2.service, code_ref=r2.code_ref, safe_window=r2.safe_window, proof=default_proof(r2.service), proof_class=r2.proof_class)\n    payload = probe_for_row(row)\n    print(\"probe ok:\", payload is not None)\n    if payload:\n        import json\n        slim = {k: payload[k] for k in ['proof_class','x_mcp_count','byte_identical','code_version','expected_x_mcp_count','code_ref_relation'] if k in payload}\n        print(json.dumps(slim, indent=2))\n    print(\"proof_observed:\", proof_observed(row, payload))\n    result = settle_open_row(r2, default_probe)\n    print(\"settle outcome:\", result.outcome, result.detail)\n    print(\"open after:\", len(list_open_rows()))\nelse:\n    print(\"ROW NOT OPEN\")\n    from charter_runner_store.db import open_ledger_db\n    import json\n    db = open_ledger_db()\n    cur = db.execute(\"SELECT status, proof_class, proof_payload FROM propagation_ledger WHERE row_id=?\", (ROW_ID,))\n    row = cur.fetchone()\n    if row:\n        d = dict(row)\n        pp = d.get('proof_payload')\n        if pp:\n            try:\n                d['proof_payload'] = json.loads(pp)\n            except Exception:\n                pass\n        print(\"db row:\", json.dumps(d, indent=2, default=str))\n    else:\n        print(\"row not in db\")\n    db.close()\nPY"
        },
        {
          "op": "shell",
          "target": "ps -p 3871894 -o pid,lstart,etime,cmd; curl -s http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness; echo; curl -s http://127.0.0.1:8091/api/v1/git/openapi.json | /home/io/.venvs/universal/bin/python3 -c \"import sys,json; d=json.load(sys.stdin); c=sum(1 for p in d.get('paths',{}).values() for m in p.values() if isinstance(m,dict) and 'x-mcp' in m); print('x_mcp_count', c)\"",
          "detail": {
            "command": "ps -p 3871894 -o pid,lstart,etime,cmd; curl -s http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness; echo; curl -s http://127.0.0.1:8091/api/v1/git/openapi.json | /home/io/.venvs/universal/bin/python3 -c \"import sys,json; d=json.load(sys.stdin); c=sum(1 for p in d.get('paths',{}).values() for m in p.values() if isinstance(m,dict) and 'x-mcp' in m); print('x_mcp_count', c)\"",
            "timeout": 30000
          },
          "identity": "ps -p 3871894 -o pid,lstart,etime,cmd; curl -s http://127.0.0.1:8091/api/v1/git/cursor-auto/liveness; echo; curl -s http://127.0.0.1:8091/api/v1/git/openapi.json | /home/io/.venvs/universal/bin/python3 -c \"import sys,json; d=json.load(sys.stdin); c=sum(1 for p in d.get('paths',{}).values() for m in p.values() if isinstance(m,dict) and 'x-mcp' in m); print('x_mcp_count', c)\""
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 << 'PY'\n# AC5: verify relay_parse_failed reachable via worker import path (same as running process)\nimport sys\nsys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway')\nfrom services.git_integration_worker.relay.closeout_relay_common import RELAY_PARSE_FAILED_STATUS\nfrom services.git_integration_worker.cursor_auto.closeout_relay_briefing import finalize_relay_payload\nfrom services.git_integration_worker.relay.closeout_relay_common import CloseoutRelayPayload\nfrom services.git_integration_worker.relay.closeout_relay_cortex_fields import _extract_plain_same_line\n\n# plain colon format test (AC3 fix live)\nsample = \"status: complete\\nac_verdict: AC1 PASS\\n\"\nassert _extract_plain_same_line(sample, \"status\") == \"complete\"\nassert _extract_plain_same_line(sample, \"ac_verdict\") == \"AC1 PASS\"\nprint(\"RELAY_PARSE_FAILED_STATUS:\", RELAY_PARSE_FAILED_STATUS)\nprint(\"plain_colon_extract: OK\")\n\n# simulate parse failure path\npayload = finalize_relay_payload(\n    CloseoutRelayPayload(body=\"status: **complete**\", status=\"complete\", source=\"section2_synthesized\"),\n    wrapper_text=\"TYPE: CLOSEOUT\\nstatus: complete\\n\",\n    dispatch_id=\"test-ac5\",\n)\nprint(\"finalize status when bold breaks parse:\", payload.status)\nPY",
          "detail": {
            "truncated": "{\"command\":\"/home/io/.venvs/universal/bin/python3 << 'PY'\\n# AC5: verify relay_parse_failed reachable via worker import path (same as running process)\\nimport sys\\nsys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway')\\nfrom services.git_integration_worker.relay.closeout_relay_common import RELAY_PARSE_FAILED_STATUS\\nfrom services.git_integration_worker.cursor_auto.closeout_relay_briefing import finalize_relay_payload\\nfrom services.git_integration_worker.relay.closeout_relay_common import CloseoutRelayPayload\\nfrom services.git_integration_worker.relay.closeout_relay_cortex_fields import _extract_plain_same_line\\n\\n# plain colon format test (AC3 fix live)\\nsample = \\\"status: complete\\\\nac_verdict: AC1 PASS\\\\n\\\"\\nassert _extract_plain_same_line(sample, \\\"status\\\") == \\\"complete\\\"\\nassert _extract_plain_same_line(sample, \\\"ac_verdict\\\") == \\\"AC1 PASS\\\"\\nprint(\\\"RELAY_PARSE_FAILED_STATUS:\\\", RELAY_PARSE_FAILED_STATUS)\\nprint(\\\"plain_colon_extract: OK\\\")\\n\\n# simulate parse failure path\\npayload = finalize_relay_payload(\\n    CloseoutRelayPayload(body=\\\"status: **complete**\\\", status=\\\"complete\\\", source=\\\"section2_synthesized\\\"),\\n    wrapper_text=\\\"TYPE: CLOSEOUT\\\\nstatus: complete\\\\n\\\",\\n    dispatch_id=\\\"test-ac5\\\",\\n)\\nprint(\\\"finalize status when bold breaks parse:\\\", payload.status)\\nPY\",\"timeout\":30000}"
          },
          "identity": "/home/io/.venvs/universal/bin/python3 << 'PY'\n# AC5: verify relay_parse_failed reachable via worker import path (same as running process)\nimport sys\nsys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway')\nfrom services.git_integration_worker.relay.closeout_relay_common import RELAY_PARSE_FAILED_STATUS\nfrom services.git_integration_worker.cursor_auto.closeout_relay_briefing import finalize_relay_payload\nfrom services.git_integration_worker.relay.closeout_relay_common import CloseoutRelayPayload\nfrom services.git_integration_worker.relay.closeout_relay_cortex_fields import _extract_plain_same_line\n\n# plain colon format test (AC3 fix live)\nsample = \"status: complete\\nac_verdict: AC1 PASS\\n\"\nassert _extract_plain_same_line(sample, \"status\") == \"complete\"\nassert _extract_plain_same_line(sample, \"ac_verdict\") == \"AC1 PASS\"\nprint(\"RELAY_PARSE_FAILED_STATUS:\", RELAY_PARSE_FAILED_STATUS)\nprint(\"plain_colon_extract: OK\")\n\n# simulate parse failure path\npayload = finalize_relay_payload(\n    CloseoutRelayPayload(body=\"status: **complete**\", status=\"complete\", source=\"section2_synthesized\"),\n    wrapper_text=\"TYPE: CLOSEOUT\\nstatus: complete\\n\",\n    dispatch_id=\"test-ac5\",\n)\nprint(\"finalize status when bold breaks parse:\", payload.status)\nPY"
        },
        {
          "op": "shell",
          "target": "/home/io/.venvs/universal/bin/python3 << 'PY'\nimport sys, json\nsys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway')\nfrom charter_runner_store.db import open_ledger_db\nROW_ID = \"git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart\"\ndb = open_ledger_db()\ncur = db.execute(\"SELECT status, proof_class, proof_payload, updated_at FROM propagation_ledger WHERE row_id=?\", (ROW_ID,))\nrow = cur.fetchone()\nif row:\n    d = dict(row)\n    if d.get('proof_payload'):\n        d['proof_payload'] = json.loads(d['proof_payload'])\n    print(json.dumps(d, indent=2))\ndb.close()\nPY",
          "detail": {
            "truncated": "{\"command\":\"/home/io/.venvs/universal/bin/python3 << 'PY'\\nimport sys, json\\nsys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway')\\nfrom charter_runner_store.db import open_ledger_db\\nROW_ID = \\\"git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart\\\"\\ndb = open_ledger_db()\\ncur = db.execute(\\\"SELECT status, proof_class, proof_payload, updated_at FROM propagation_ledger WHERE row_id=?\\\", (ROW_ID,))\\nrow = cur.fetchone()\\nif row:\\n    d = dict(row)\\n    if d.get('proof_payload'):\\n        d['proof_payload'] = json.loads(d['proof_payload'])\\n    print(json.dumps(d, indent=2))\\ndb.close()\\nPY\",\"timeout\":30000}"
          },
          "identity": "/home/io/.venvs/universal/bin/python3 << 'PY'\nimport sys, json\nsys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway')\nfrom charter_runner_store.db import open_ledger_db\nROW_ID = \"git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart\"\ndb = open_ledger_db()\ncur = db.execute(\"SELECT status, proof_class, proof_payload, updated_at FROM propagation_ledger WHERE row_id=?\", (ROW_ID,))\nrow = cur.fetchone()\nif row:\n    d = dict(row)\n    if d.get('proof_payload'):\n        d['proof_payload'] = json.loads(d['proof_payload'])\n    print(json.dumps(d, indent=2))\ndb.close()\nPY"
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
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/propagation_row.py"
        },
        {
          "op": "edit",
          "target": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/test_propagation_block_parser.py",
          "detail": {
            "path": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/test_propagation_block_parser.py"
          },
          "identity": "/mnt/torus/projects/universal-llm-gateway/libs/implement_admission/test_propagation_block_parser.py"
        },
        {
          "op": "shell",
          "target": "cd /mnt/torus/projects/universal-llm-gateway && /home/io/.venvs/universal/bin/python3 -m pytest libs/implement_admission/test_propagation_block_parser.py services/git_integration_worker/tests/test_cursor_auto_closeout_relay.py::test_arc6637_relay_parse_failed_when_extraction_fails services/git_integration_worker/tests/test_cursor_auto_closeout_relay.py::test_arc6637_plain_colon_format_root_cause_not_section2_heading -q 2>&1",
          "detail": {
            "command": "cd /mnt/torus/projects/universal-llm-gateway && /home/io/.venvs/universal/bin/python3 -m pytest libs/implement_admission/test_propagation_block_parser.py services/git_integration_worker/tests/test_cursor_auto_closeout_relay.py::test_arc6637_relay_parse_failed_when_extraction_fails services/git_integration_worker/tests/test_cursor_auto_closeout_relay.py::test_arc6637_plain_colon_format_root_cause_not_section2_heading -q 2>&1",
            "timeout": 30000
          },
          "identity": "cd /mnt/torus/projects/universal-llm-gateway && /home/io/.venvs/universal/bin/python3 -m pytest libs/implement_admission/test_propagation_block_parser.py services/git_integration_worker/tests/test_cursor_auto_closeout_relay.py::test_arc6637_relay_parse_failed_when_extraction_fails services/git_integration_worker/tests/test_cursor_auto_closeout_relay.py::test_arc6637_plain_colon_format_root_cause_not_section2_heading -q 2>&1"
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
          "target": "services/git_integration_worker",
          "detail": null,
          "identity": "services/git_integration_worker"
        },
        {
          "op": "observed",
          "target": "scripts/model_manager/ui/controller/git_worker_drain_supervisor.py",
          "detail": null,
          "identity": "scripts/model_manager/ui/controller/git_worker_drain_supervisor.py"
        },
        {
          "op": "observed",
          "target": "services/git_integration_worker/cursor_auto/handler_propagation.py",
          "detail": null,
          "identity": "services/git_integration_worker/cursor_auto/handler_propagation.py"
        },
        {
          "op": "observed",
          "target": "libs/charter_runner_store/propagation_terminal.py",
          "detail": null,
          "identity": "libs/charter_runner_store/propagation_terminal.py"
        },
        {
          "op": "observed",
          "target": "services/git_integration_worker/cursor_auto/propagation_probe.py",
          "detail": null,
          "identity": "services/git_integration_worker/cursor_auto/propagation_probe.py"
        },
        {
          "op": "observed",
          "target": "libs/charter_runner_store/propagation_ledger.py",
          "detail": null,
          "identity": "libs/charter_runner_store/propagation_ledger.py"
        },
        {
          "op": "observed",
          "target": "services/git_integration_worker/cursor_auto/propagate_admission.py",
          "detail": null,
          "identity": "services/git_integration_worker/cursor_auto/propagate_admission.py"
        },
        {
          "op": "observed",
          "target": "services/git_integration_worker/routes/cursor_auto.py",
          "detail": null,
          "identity": "services/git_integration_worker/routes/cursor_auto.py"
        },
        {
          "op": "observed",
          "target": "services/git_integration_worker/cursor_auto/propagation_served_artifact.py",
          "detail": null,
          "identity": "services/git_integration_worker/cursor_auto/propagation_served_artifact.py"
        },
        {
          "op": "observed",
          "target": "scripts/model_manager",
          "detail": null,
          "identity": "scripts/model_manager"
        },
        {
          "op": "observed",
          "target": "scripts/model_manager/ui/controller/charter_runner/propagation_execute.py",
          "detail": null,
          "identity": "scripts/model_manager/ui/controller/charter_runner/propagation_execute.py"
        },
        {
          "op": "observed",
          "target": "services/git_integration_worker/tests/test_cursor_auto_closeout_relay.py",
          "detail": null,
          "identity": "services/git_integration_worker/tests/test_cursor_auto_closeout_relay.py"
        },
        {
          "op": "observed",
          "target": "services/git_integration_worker/openapi_mcp",
          "detail": null,
          "identity": "services/git_integration_worker/openapi_mcp"
        },
        {
          "op": "observed",
          "target": "services/git_integration_worker/cursor_auto/closeout_relay_common.py",
          "detail": null,
          "identity": "services/git_integration_worker/cursor_auto/closeout_relay_common.py"
        },
        {
          "op": "observed",
          "target": "services/git_integration_worker/cursor_auto/closeout_relay_cortex_fields.py",
          "detail": null,
          "identity": "services/git_integration_worker/cursor_auto/closeout_relay_cortex_fields.py"
        },
        {
          "op": "observed",
          "target": "libs/deploy_identity/code_version.py",
          "detail": null,
          "identity": "libs/deploy_identity/code_version.py"
        },
        {
          "op": "observed",
          "target": "services/git_integration_worker/cursor_auto/liveness.py",
          "detail": null,
          "identity": "services/git_integration_worker/cursor_auto/liveness.py"
        }
      ],
      "cross_check": null
    },
    "service": {
      "surface": "service",
      "source": "conversation",
      "entries": [
        {
          "op": "manage",
          "target": "git_integration_worker",
          "detail": {
            "providerIdentifier": "user-vortex",
            "toolName": "manage",
            "args": {
              "action": "health",
              "service": "git_integration_worker"
            }
          },
          "identity": "git_integration_worker"
        },
        {
          "op": "manage",
          "target": null,
          "detail": {
            "providerIdentifier": "user-vortex",
            "toolName": "manage",
            "args": {
              "action": "whoami"
            }
          },
          "identity": null
        },
        {
          "op": "emit_implement_closeout_trigger",
          "target": "auto-958206cbe1bc",
          "detail": null,
          "identity": "auto-958206cbe1bc"
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
    }
  },
  "coverage": {
    "repo": "complete",
    "service": "complete"
  },
  "external_effects": "scoped_out"
}

## structured_closeout_full

{
  "schema_version": 1,
  "status": "complete",
  "work_outcome": "shipped",
  "summary": "dispatch auto-958206cbe1bc: 53 tool calls, 73.6s, 3579B -> sidecar",
  "deviations": [
    "degraded:sdk_git_probe_absent",
    "capture:non_file_manifest_entry_dropped",
    "divergence:manifest_vs_git_labels"
  ],
  "files_created": [],
  "files_modified": [
    "libs/implement_admission/propagation_block_parser.py",
    "libs/implement_admission/propagation_row.py",
    "libs/implement_admission/test_propagation_block_parser.py"
  ],
  "files_deleted": [],
  "files_ambient_repo_movement": [],
  "capture_status": null,
  "effects_manifest": {
    "schema_version": 1,
    "dispatch_id": "auto-958206cbe1bc",
    "thread_id": "6638",
    "digest": "05d1b5199203bb8e587b00a244c54199cdba872804137ecb537b4eaeda2b2a43",
    "surface_counts": {
      "repo": 33,
      "service": 3,
      "agent_bus": 1
    },
    "capture_sources": [
      "conversation",
      "stream",
      "wrapper"
    ],
    "external_effects": "scoped_out"
  },
  "public_api_changed": false,
  "verification": [],
  "evidence_uris": {
    "dispatch_ids": [
      "auto-958206cbe1bc"
    ],
    "bus_threads": [
      "6638"
    ],
    "artifact_paths": [
      "workspaces://universal-llm-gateway/tmp/reviews/closeouts/auto-958206cbe1bc.md"
    ],
    "cortex_assertions": [],
    "git_refs": []
  },
  "source_ref": "workspaces://universal-llm-gateway/tmp/reviews/closeouts/auto-958206cbe1bc.md",
  "packet_sha256": null,
  "adapter_results": [],
  "propagation_residue": [
    "libs_touched: libs/implement_admission/propagation_block_parser.py \u2014 shared lib; lead must decide which consumers restart",
    "libs_touched: libs/implement_admission/propagation_row.py \u2014 shared lib; lead must decide which consumers restart"
  ],
  "propagation": [],
  "dropped_non_file_entries": [
    {
      "surface": "repo",
      "op": "shell",
      "target": "/home/io/.venvs/universal/bin/python3 << 'PY'\nimport sys\nsys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway')\nfrom charter_runner_store.propagation_ledger import list_open_rows, set_proof_class\nfrom charter_runner_store.propagation_terminal import settle_open_row, default_probe\nfrom services.git_integration_worker.relay.propagation_probe import probe_for_row, proof_observed\nfrom implement_admission.propagation_row import PropagationRow, default_proof\n\nROW_ID = \"git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart\"\nrows = list_open_rows()\nprint(\"open rows:\", len(rows))\nfor r in rows:\n    print(f\"  {r.row_id} proof_class={r.proof_class} defer={r.defer_reason}\")\n\ntarget = [r for r in rows if r.row_id == ROW_ID]\nif target:\n    r = target[0]\n    set_proof_class(ROW_ID, \"served_artifact\")\n    r2 = [x for x in list_open_rows() if x.row_id == ROW_ID][0]\n    row = PropagationRow(service=r2.service, code_ref=r2.code_ref, safe_window=r2.safe_window, proof=default_proof(r2.service), proof_class=r2.proof_class)\n    payload = probe_for_row(row)\n    print(\"probe ok:\", payload is not None)\n    if payload:\n        import json\n        slim = {k: payload[k] for k in ['proof_class','x_mcp_count','byte_identical','code_version','expected_x_mcp_count','code_ref_relation'] if k in payload}\n        print(json.dumps(slim, indent=2))\n    print(\"proof_observed:\", proof_observed(row, payload))\n    result = settle_open_row(r2, default_probe)\n    print(\"settle outcome:\", result.outcome, result.detail)\n    print(\"open after:\", len(list_open_rows()))\nelse:\n    print(\"ROW NOT OPEN\")\n    from charter_runner_store.db import open_ledger_db\n    import json\n    db = open_ledger_db()\n    cur = db.execute(\"SELECT status, proof_class, proof_payload FROM propagation_ledger WHERE row_id=?\", (ROW_ID,))\n    row = cur.fetchone()\n    if row:\n        d = dict(row)\n        pp = d.get('proof_payload')\n        if pp:\n            try:\n                d['proof_payload'] = json.loads(pp)\n            except Exception:\n                pass\n        print(\"db row:\", json.dumps(d, indent=2, default=str))\n    else:\n        print(\"row not in db\")\n    db.close()\nPY",
      "reason": "non_file"
    },
    {
      "surface": "repo",
      "op": "shell",
      "target": "/home/io/.venvs/universal/bin/python3 << 'PY'\n# AC5: verify relay_parse_failed reachable via worker import path (same as running process)\nimport sys\nsys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway')\nfrom services.git_integration_worker.relay.closeout_relay_common import RELAY_PARSE_FAILED_STATUS\nfrom services.git_integration_worker.cursor_auto.closeout_relay_briefing import finalize_relay_payload\nfrom services.git_integration_worker.relay.closeout_relay_common import CloseoutRelayPayload\nfrom services.git_integration_worker.relay.closeout_relay_cortex_fields import _extract_plain_same_line\n\n# plain colon format test (AC3 fix live)\nsample = \"status: complete\\nac_verdict: AC1 PASS\\n\"\nassert _extract_plain_same_line(sample, \"status\") == \"complete\"\nassert _extract_plain_same_line(sample, \"ac_verdict\") == \"AC1 PASS\"\nprint(\"RELAY_PARSE_FAILED_STATUS:\", RELAY_PARSE_FAILED_STATUS)\nprint(\"plain_colon_extract: OK\")\n\n# simulate parse failure path\npayload = finalize_relay_payload(\n    CloseoutRelayPayload(body=\"status: **complete**\", status=\"complete\", source=\"section2_synthesized\"),\n    wrapper_text=\"TYPE: CLOSEOUT\\nstatus: complete\\n\",\n    dispatch_id=\"test-ac5\",\n)\nprint(\"finalize status when bold breaks parse:\", payload.status)\nPY",
      "reason": "non_file"
    },
    {
      "surface": "repo",
      "op": "shell",
      "target": "/home/io/.venvs/universal/bin/python3 << 'PY'\nimport sys, json\nsys.path.insert(0, '/mnt/torus/projects/universal-llm-gateway')\nfrom charter_runner_store.db import open_ledger_db\nROW_ID = \"git_integration_worker:d3e17d54b66276a350769501beb90c2988ff3bf1:sync_restart\"\ndb = open_ledger_db()\ncur = db.execute(\"SELECT status, proof_class, proof_payload, updated_at FROM propagation_ledger WHERE row_id=?\", (ROW_ID,))\nrow = cur.fetchone()\nif row:\n    d = dict(row)\n    if d.get('proof_payload'):\n        d['proof_payload'] = json.loads(d['proof_payload'])\n    print(json.dumps(d, indent=2))\ndb.close()\nPY",
      "reason": "non_file"
    },
    {
      "surface": "repo",
      "op": "observed",
      "target": "/mnt/torus/projects",
      "reason": "non_file"
    },
    {
      "surface": "repo",
      "op": "observed",
      "target": "services/git_integration_worker",
      "reason": "non_file"
    },
    {
      "surface": "repo",
      "op": "observed",
      "target": "scripts/model_manager",
      "reason": "non_file"
    },
    {
      "surface": "repo",
      "op": "observed",
      "target": "services/git_integration_worker/openapi_mcp",
      "reason": "non_file"
    }
  ],
  "effects": [
    "libs/implement_admission/propagation_block_parser.py",
    "libs/implement_admission/propagation_row.py",
    "libs/implement_admission/test_propagation_block_parser.py"
  ]
}