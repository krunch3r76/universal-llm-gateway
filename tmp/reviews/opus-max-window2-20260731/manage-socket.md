# manage.sock — dual-controller correctness hazard

**Worker:** Opus-max window 2, 2026-07-31
**Repo:** `/mnt/torus/projects/universal-llm-gateway` @ master `369ca56b`
**Status:** COMPLETE — diagnosis + mechanical fix landed (not live until operator restart)

---

## Question

As handed to me: *two `manage` controllers are bound to one socket; which one answers, is
attribution stable, and what is at risk?*

I did not change the question. I did sharpen step 2 ("how can two processes hold one unix
socket") into a decidable test: **compare the socket inode each process listens on against
the inode the filesystem path currently resolves to.** That single comparison discriminates
between the candidate mechanisms named in the brief (stale fd / unlink+rebind /
`SO_REUSEPORT` / forked supervisor) without needing to reason about any of them.

---

## Checkpoint 1 — process census (OBSERVED)

Re-established live at 14:34–14:35 PDT. Both reported pids are still running; the reported
pids were **not** stale.

```
$ ps -eo pid,ppid,lstart,etime,user,args | rg -i 'manage|model_manager'
 669565       1 Fri Jul 31 10:43:41 2026 03:51:13 io  /bin/bash -O extglob -c ... -- cd /mnt/torus/projects/universal-llm-gateway && nohup setsid /home/io/.venvs/universal/bin/python -m scripts.model_manager.ui ...
 669567  669565 Fri Jul 31 10:43:41 2026 03:51:13 io  /home/io/.local/share/git-integration-worker/cursor-dispatch-homes/auto-9066617448d4-home/.venvs/universal/bin/python -m scripts.model_manager.ui
1176965       1 Fri Jul 31 12:52:19 2026 01:42:36 io  /home/io/.venvs/universal/bin/python -m scripts.model_manager.ui status
1630785    5693 Fri Jul 31 14:20:33 2026    14:22 io  /home/io/.venvs/universal/bin/python -m scripts.model_manager.ui
```

**There are two listeners, and also a third `manage` process nobody mentioned** (`1176965`),
a one-shot `ui status` CLI invocation that has been resident for 1h42m — see Residuals.

### pid 669567 — the orphan

| Field | Value |
|---|---|
| `/proc/669567/cwd` | `/mnt/torus/projects/universal-llm-gateway` |
| `/proc/669567/exe` | `/usr/bin/python3.12` |
| argv[0] | `…/cursor-dispatch-homes/auto-9066617448d4-home/.venvs/universal/bin/python` |
| `HOME` | `/home/io/.local/share/git-integration-worker/cursor-dispatch-homes/auto-9066617448d4-home` |
| `CURSOR_SDK_DISPATCH_ID` | `auto-9066617448d4` |
| `CURSOR_AGENT` | `1` |
| `CURSOR_CONVERSATION_ID` | `agent-ba8ef111-b36f-44d4-a323-e85bd65f5bec` |
| parent | `669565` — a **Cursor agent shell** (`__CURSOR_SANDBOX_ENV_RESTORE`, `dump_bash_state`) |
| started | 10:43:41 |

Provenance is unambiguous from the environment block: this controller was started **by an
agent inside a cursor-sdk dispatch** (`auto-9066617448d4`), by the shell whose recorded
command is `nohup setsid … -m scripts.model_manager.ui > /tmp/manage-restart.log`. It
inherited that dispatch's whole environment, including `CURSOR_API_KEY`.

### pid 1630785 — the tmux TUI (at diagnosis time; live controller is now `2048906`)

| Field | Value |
|---|---|
| `/proc/1630785/cwd` | `/mnt/torus/projects/universal-llm-gateway` |
| `HOME` | `/home/io` |
| `TMUX` | `/tmp/tmux-1000/default,5692,0` |
| `TMUX_PANE` | `%0` |
| parent | `5693` (`-bash`, login shell in the tmux pane) |
| started | 14:20:33 |
| child | `1661288` — `stargate_service_manager.py --environment=debug` (started 14:24:25) |

At diagnosis time this was the canonical `./manage` TUI in tmux `0:0`. A later
clean handoff (15:05:44) replaced it with pid **2048906** — re-read `whoami` or
`ss` before acting on a pid.

---

## Checkpoint 2 — both really are LISTENing (OBSERVED)

```
$ ss -xlp | rg manage
u_str LISTEN 0 100 /tmp/universal-protocol/manage.sock 525711927 * 0 users:(("python",pid=669567,fd=9))
u_str LISTEN 0 100 /tmp/universal-protocol/manage.sock 529522565 * 0 users:(("python",pid=1630785,fd=7))

$ fuser -v /tmp/universal-protocol/manage.sock
io  669567 F.... python
io 1630785 F.... python
```

Two **distinct socket inodes** — `525711927` and `529522565` — both in `LISTEN`, both
reporting the same bind path. That both report the path is expected and is **not**
evidence they are both reachable: a unix socket keeps its bind-time name string in
kernel state even after the path is unlinked from the filesystem.

The filesystem path is a single inode with a single birth time:

```
$ stat /tmp/universal-protocol/manage.sock
  Inode: 116393724   Links: 1
  Birth: 2026-07-31 14:20:35.573418128 -0700
```

**Birth 14:20:35 is 2 seconds after pid 1630785 started (14:20:33) and 3h37m after pid
669567 started (10:43:41).** The path node in the filesystem was created by the *younger*
process. That is the unlink-and-rebind signature.

---

## Checkpoint 3 — which one answers (MEASURED, not inferred)

The brief asked me not to reason about this. I did not. I attributed connections two
independent ways, neither of which depends on the inode-birth argument above.

**Method A — protocol-independent.** Open a connection and send *nothing*. The server
blocks in `reader.readline()` (`api_server.py:110`), so the accepted socket pair stays
visible in `ss -xap` as `ESTAB` with the owning pid. No JSON-RPC method is dispatched,
no event is emitted, nothing mutates. Then read the owning pid off `ss`.

**Method B — fd accounting.** Count `/proc/<pid>/fd` entries for both controllers before
and after the whole probe run. A controller that accepts connections gains fds.

```
$ python /tmp/manage_probe2.py
fd counts BEFORE: {669567: 13, 1630785: 16}

--- 40 sequential connects (fresh connect each; no data sent) ---
{'1630785': 40}

--- 20 concurrent connects ---
{'1630785': 20}

--- 6 real read-only JSON-RPC busy_status calls ---
  call 1..6: responded   {"jsonrpc": "2.0", "result": {"services": {"agent_bus": {"busy": false, ...

fd counts AFTER: {669567: 13, 1630785: 21}
  pid  669567: 13 -> 13
  pid 1630785: 16 -> 21
```

An earlier independent run (`/tmp/manage_probe.py`, 8 probes) also returned `1630785` 8/8.

### Result

| Claim | Verdict | Evidence |
|---|---|---|
| Two processes hold LISTEN sockets on the path | **TRUE** | `ss -xlp`, `fuser` |
| Attribution is *unstable* / nondeterministic | **FALSE** | 68/68 connections → `1630785`, incl. 20 concurrent |
| pid `669567` serves some calls | **FALSE** | fd count 13 → 13 across 66 connects; zero `ESTAB` ever attributed to it |

**Attribution is completely stable, and it is stable in the safe direction.** Every
connection — sequential, reconnecting, and concurrent — reaches pid `1630785`, the tmux
`0:0` TUI. pid `669567` is an **orphan holding an unlinked inode and receiving nothing**.

This is a *weaker* hazard than the brief hypothesized, and I want to be precise about the
correction: the risk was framed as "which controller answers may not be stable." Measured,
it is stable. The real defect is different and is described in Checkpoint 4.

### Sub-finding: the protocol cannot identify its own responder

`api_dispatch.py:286-294` enumerates every valid method:

> `status, health, wait_healthy, start, stop, restart, sync_restart, rebuild, busy_status,
> charter_reload, charter_pause, charter_resume, charter_hold_status, charter_block_root,
> charter_unblock_root, charter_root_status, fleet_sync_restart, fleet_rebuild_deploy`

There is **no `version`, `ping`, `whoami`, `pid`, or `uptime` op**, and no response
envelope field carrying the server's identity. I had to attribute via `ss`/`/proc`, which
is only possible because I am on the same host with procfs access. As the brief
anticipated, that absence is itself the finding: **a caller of `manage.sock` cannot tell
which controller answered it, or what code that controller booted with.** See the spec in
"What I did NOT change".

---

## Checkpoint 4 — the mechanism, and when it happened (OBSERVED + one INFERENCE)

`/tmp/logs/tui/manage-api.log` is append-shared by every controller and gives an exact
bind/stop timeline. Lines 491-495:

```
2026-07-31T10:43:35-0700 INFO ...api_server: Manage API server stopped
2026-07-31T10:44:10-0700 INFO ...api_server: Manage API server listening on /tmp/universal-protocol/manage.sock
2026-07-31T11:24:06-0700 INFO ...api_server: Manage API server listening on /tmp/universal-protocol/manage.sock
2026-07-31T14:20:33-0700 INFO ...api_server: Manage API server stopped
2026-07-31T14:20:35-0700 INFO ...api_server: Manage API server listening on /tmp/universal-protocol/manage.sock
```

Reconstructed timeline (bind/stop lines OBSERVED; pid attribution INFERRED from matching
process start times, since the log carries no pid — see Open questions):

| Time | Event | Consequence |
|---|---|---|
| 10:43:35 | prior controller `stop()` → `_SOCK_PATH.unlink()` (`api_server.py:102`) | path removed |
| 10:43:41 | pid **669567** starts (cursor dispatch `auto-9066617448d4`) | — |
| 10:44:10 | 669567 binds — clean, path was absent | 669567 **is** the live controller |
| **11:24:06** | a third controller binds **with no preceding `stopped` line** | **669567 orphaned here** |
| 14:20:33 | that controller `stop()` → unlink; pid 1630785 starts | path removed |
| 14:20:35 | **1630785** binds — clean | current live controller; path inode birth matches exactly |

**The orphaning happened at 11:24:06, not at 14:20.** pid 669567 has been holding an
unlinked inode and receiving zero connections for **~3h12m**. The 14:20 transition was a
clean handoff and is not implicated.

### Why the guard did not stop it — demonstrated hole, unproven trigger

`ManageAPIServer.start()` is explicitly written to prevent this (`api_server.py:83-89`):
it calls `_is_socket_alive()` and raises `ManageSocketBusyError` rather than orphan a peer.
No `"refused to bind"` line appears anywhere in the log, so **the guard returned `False`
at 11:24:06** — it did not fire.

I checked and **refuted** the obvious explanation: `manage.sock` is not in the
health-checked service set, so `service_state.py`'s `_unlink_stale_socket` (lines 146,
280, 792) never targets it — `rg 'MANAGE_SOCKET|manage\.sock' service_state.py` returns
nothing. Nothing else unlinked the path. I also refuted directory recreation:
`claudeburst-coinbase.sock` in the same directory has survived since `Jun 26 06:25`.

What remains is a real hole in the guard itself (`api_server.py:266-272`):

```python
    except (FileNotFoundError, ConnectionRefusedError):
        return False
    except OSError:
        # ENOTCONN, EAGAIN, ECONNRESET — treat as "not a live listener". The
        # caller will unlink and rebind; if a real listener does appear in
        # the race window, the bind itself will fail with EADDRINUSE.
        return False
```

A live-but-unresponsive listener — accept backlog full (`listen(100)`), or a blocked
asyncio loop tripping the `_LIVE_PROBE_TIMEOUT_S = 0.5` timeout — surfaces as `EAGAIN` or
`socket.timeout`, both `OSError` subclasses, both swallowed into `return False`. The
guard then unlinks and rebinds, orphaning exactly the controller it exists to protect.
The trailing comment's reassurance is wrong for this case: the subsequent `bind()` does
**not** fail with `EADDRINUSE`, because line 90 has already unlinked the path — which is
precisely why we observe two `LISTEN` sockets rather than a failed second bind.

**Distinguishing observation from inference:** that the hole exists in the code is
observed, and I demonstrate it executes in Checkpoint 5. That this hole is *what fired at
11:24:06* is **not established** — I have no capture of 669567's state at that moment.
It is the only surviving candidate I could not refute, which is weaker than proof.

---

## Checkpoint 5 — the guard hole is real, not theoretical (REPRODUCED)

I imported the actual `_is_socket_alive` from the live source and ran it against
throwaway sockets in `/tmp/guardtest-<pid>/`. The sandbox never touches `manage.sock`
except for one read-only `connect()`+`close()` — literally what the guard itself does.

```
$ python /tmp/guard_test.py
_LIVE_PROBE_TIMEOUT_S = 0.5

[control 1] live manage.sock                -> _is_socket_alive = True   (expect True)
[control 2] nonexistent path                -> _is_socket_alive = False  (expect False)
[control 3] healthy listener, backlog empty -> _is_socket_alive = True   (expect True)

[TEST] LIVE listener, backlog saturated (2 queued)
       socket file exists: True   listener process: THIS process (alive)
       _is_socket_alive   = False   <-- guard would UNLINK+REBIND a live controller

  ==> HOLE DEMONSTRATED
```

All three controls behave correctly, which matters: the guard is not broken in general,
and it correctly protects the socket *right now*. The failure is narrow and specific —
**a live controller that is not currently draining its accept backlog is reported dead**,
and `start()` then unlinks the path out from under it (`api_server.py:90`) and binds a
second socket. That is a mechanically exact reproduction of the observed end state.

This also answers the brief's step-2 question. Of the four candidate mechanisms:

| Candidate | Verdict |
|---|---|
| One bound, one holding a stale fd | **Nearly right** — but the orphan holds a *listening* fd, not a stale client fd |
| **Socket file unlinked and re-bound** | **CONFIRMED** — distinct inodes `525711927` / `529522565`, path inode born at the younger bind, orphan gets zero connections |
| `SO_REUSEPORT`-style sharing | **Refuted** — `SO_REUSEPORT` does not exist for `AF_UNIX`; and sharing would distribute connections, but 68/68 went to one pid |
| Supervisor that forked | **Refuted** — different parents (`669565` cursor agent shell vs `5693` tmux login shell), different start times 3h37m apart, different `HOME` |

---

## What I changed

Two commits on master (working tree was clean at `b003446f` before these landed):

| Commit | SHA | Summary |
|---|---|---|
| Guard fix | `71b0b08a3075c130d79fe1f4d2412eab29228325` | `_is_socket_alive()` now raises `SocketProbeAmbiguousError` on any `OSError` other than `FileNotFoundError` / `ConnectionRefusedError`; `ManageAPIServer.start()` translates that to `ManageSocketBusyError` (fail closed — refuse to unlink). Removed the false `EADDRINUSE` comment. Tests: `scripts/model_manager/ui/tests/test_socket_alive_probe.py`. |
| Identity op | `93964e3af8b7397dac7b01c724faf1cc493e5f11` | New read-only `whoami` JSON-RPC method in `api_dispatch.py` returning `{pid, code_version, process_start_time}` via `deploy_identity.code_version.resolve_code_version()` (eager import seal) and `process_age_s()`. Tests: `scripts/model_manager/ui/tests/test_whoami_dispatch.py`. |

Line numbers cited in Checkpoints 3–5 **drifted** after the edit (e.g. the guard hole was `api_server.py:266-272` at diagnosis time; the `OSError` swallow is now `api_server.py:280-283`, and `start()`'s guard block moved to ~`87-101`). Find symbols by name, not by line.

---

## What I did NOT change and why

| Area | Why left alone |
|---|---|
| **`services/mcp-server/tools/manage.py`** | Task scope locked to `scripts/model_manager/` + deliverable. MCP `_VALID_ACTIONS` does not yet expose `whoami`; agents can call it directly on `manage.sock` once live. A follow-up can wire MCP parity. |
| **Orphan pid `669567` / live controller** | Hard prohibition on lifecycle actions this session. Operator must kill/restart — see PROPAGATION REQUIRED. |
| **`service_state.py` stale-socket unlink** | Diagnosis already refuted this path for `manage.sock`. |
| **Retry loop / longer probe timeout / heuristics** | Design call was fail-closed on ambiguity, not probe tuning. |
| **`git rev-parse` in whoami** | `deploy_identity.code_version` resolves eagerly at import; whoami reports what *this* process booted with. |

---

## PROPAGATION REQUIRED

**Landed ≠ live.** Neither fix is active until the running `manage` controller(s) restart and load the new code. Restarting `manage` while two controllers are bound is the operation most likely to go wrong — follow this order.

### Pre-flight (re-checked 2026-07-31 ~15:20 PDT)

| pid | Role | Still running? | Evidence |
|---|---|---|---|
| **669567** | Orphan (cursor dispatch `auto-9066617448d4`, started 10:43:41) | **Yes** | `ps` + `ss -xlp` still show LISTEN inode `525711927`; fd count **13 → 13** across a read-only connect probe (no JSON-RPC sent) — still receiving nothing |
| **2048906** | Live tmux TUI controller (replaced diagnosis pid `1630785`) | **Yes** | `ss` shows LISTEN inode `530951653`; this is the controller that accepts connections today |

The orphan pid is unchanged from diagnosis; the live controller pid rotated (expected across sessions).

### Safe order for the operator

1. **Kill the orphan first** — `kill 669567` (or `kill -TERM` and confirm gone). This drops the stale LISTEN inode that receives zero traffic but still holds kernel state. Do **not** restart the live controller while the orphan still LISTENs on a second inode for the same path string.
2. **Restart the live controller** — tmux `0:0` `./manage` quit/start per standing recipe (`services_ws.mdc` § Manage process recycle). Use drain discipline if `busy_status` shows in-flight work (`restart-drain-discipline_ulg.mdc`).
3. **Verify attribution with `whoami`** — after restart, the new op is the first caller-native way to confirm which controller answered:

   ```bash
   python3 -c "
   import json, socket
   s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
   s.connect('/tmp/universal-protocol/manage.sock')
   s.sendall(json.dumps({'jsonrpc':'2.0','method':'whoami','params':{},'id':1}).encode()+b'\n')
   print(s.recv(4096).decode())
   "
   ```

   Expect `pid` matching the restarted process and `code_version` matching `git rev-parse HEAD` at the moment that process started (via `deploy_identity.code_version` eager seal). **`whoami` is unavailable until step 2 completes** — the running controllers predate that commit.

4. **Regression check (optional)** — attempt a second `./manage` launch; it should now refuse with `ManageSocketBusyError` rather than silently orphaning.

---

## Open questions and residuals

| Item | Status | What would settle it |
|---|---|---|
| Guard hole fired at **11:24:06** | **Unrefuted candidate, not proven** | Process-state capture at bind time (backlog depth, asyncio loop stall, `_is_socket_alive` return value logged with pid). No such capture exists for that event. |
| pid **1176965** — one-shot `ui status` resident 1h42m+ | **Unexplained** | Inspect why `python -m scripts.model_manager.ui status` did not exit; check whether it holds resources or is blocked on a socket read. Outside this fix scope. |
| MCP `manage` tool lacks `whoami` action | **Follow-up** | Add to `_VALID_ACTIONS` + docstring in `services/mcp-server/tools/manage.py` when MCP parity is commissioned. |
| Live controller pid rotation (`1630785` → `2048906`) | **Observed, benign** | Normal tmux recycle; attribution probe should be re-run post-restart anyway. |

---

## Your own errors

| Error | Impact |
|---|---|
| Initially attempted `git stash` to demonstrate revert failure | Stash dropped `SocketProbeAmbiguousError` while the test still imported it — produced an import error, not the intended `DID NOT RAISE` failure. Re-ran with an inline `return False` revert patch; got the correct `FAILED … DID NOT RAISE SocketProbeAmbiguousError`. |
| Diagnosis doc cited live controller as pid `1630785` | Accurate at diagnosis time; pre-propagation re-check found `2048906` as the current live listener. Doc updated in PROPAGATION REQUIRED rather than rewriting Checkpoint 1 history. |
| Could not verify post-restart `whoami` or guard behavior | Hard prohibition on restarts this session — propagation section is operator-facing only. |

