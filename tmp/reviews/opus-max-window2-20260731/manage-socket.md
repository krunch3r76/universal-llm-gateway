# manage.sock — dual-controller correctness hazard

**Worker:** Opus-max window 2, 2026-07-31
**Repo:** `/mnt/torus/projects/universal-llm-gateway` @ master `369ca56b`
**Status:** RESOLVED — dual-controller diagnosis complete; busy-spin root cause fixed
(`a4402bd5`); manage live @ `9e513d70` (pid `2136364` at closeout)

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
| **`services/mcp-server/tools/manage.py`** | Task scope locked to `scripts/model_manager/` + deliverable at diagnosis time. MCP `whoami` landed separately in `a1943f3f` and verified live at closeout. |
| **Orphan pid `669567` / live controller** | Hard prohibition on lifecycle actions this session. Operator must kill/restart — see PROPAGATION REQUIRED. |
| **`service_state.py` stale-socket unlink** | Diagnosis already refuted this path for `manage.sock`. |
| **Retry loop / longer probe timeout / heuristics** | Design call was fail-closed on ambiguity, not probe tuning. |
| **`git rev-parse` in whoami** | `deploy_identity.code_version` resolves eagerly at import; whoami reports what *this* process booted with. |

---

## PROPAGATION — completed 2026-07-31

The propagation sequence documented here was executed after the busy-spin fix landed.
All spinning processes were cleared; `manage` was restarted on the fixed tree; the fleet
was recycled green.

### Verification at closeout

| Check | Result |
|---|---|
| `manage` listener | **Up** — pid `2136364`, `code_version` `9e513d70` |
| `whoami` via MCP | **Success** — `manage(action="whoami")` returns pid + code version |
| Fleet health | **11/11 running/healthy** — `cortex_api` and `mcp` recycled with the rest |
| Spin regression | **Absent** — controller idle after restart; `test_parked_with_dirty_set_does_not_busy_spin` passes |

The dual-controller orphan state from Checkpoints 1–5 is historical. At closeout a single
controller holds the socket path and answers all connections.

---

## Open questions and residuals

| Item | Status | What would settle it |
|---|---|---|
| Guard hole fired at **11:24:06** | **Unrefuted candidate, not proven** | Process-state capture at bind time (backlog depth, asyncio loop stall, `_is_socket_alive` return value logged with pid). No such capture exists for that event. |
| pid **1176965** — one-shot `ui status` resident 1h42m+ | **Explained (open hazard)** | `scripts/model_manager/ui/__main__.py` accepts no arguments — `python -m scripts.model_manager.ui status` silently launches the full TUI. That process then hit the same busy-spin. A real headless status path or non-tty rejection is still a follow-up candidate. |
| MCP `manage` tool lacks `whoami` action | **Landed** (`a1943f3f`) | Verified live at closeout via `manage(action="whoami")`. |
| Live controller pid rotation (`1630785` → `2048906`) | **Observed, benign** | Normal tmux recycle; attribution probe should be re-run post-restart anyway. |

---

## Your own errors

| Error | Impact |
|---|---|
| Initially attempted `git stash` to demonstrate revert failure | Stash dropped `SocketProbeAmbiguousError` while the test still imported it — produced an import error, not the intended `DID NOT RAISE` failure. Re-ran with an inline `return False` revert patch; got the correct `FAILED … DID NOT RAISE SocketProbeAmbiguousError`. |
| Diagnosis doc cited live controller as pid `1630785` | Accurate at diagnosis time; pre-propagation re-check found `2048906` as the current live listener. Doc updated in PROPAGATION REQUIRED rather than rewriting Checkpoint 1 history. |
| Could not verify post-restart `whoami` or guard behavior | Accurate at diagnosis time; superseded by closeout verification in PROPAGATION — completed. |


---

## INCIDENT — 100% CPU busy-spin took manage down (RESOLVED)

Appended by the coordinating seat at `ecadd73d` while the incident was still open.
**Superseded below** — root cause found (`a4402bd5`), fix deployed, manage live.

### Resolution state

**`manage` is up and idle.** At closeout: pid `2136364`, `code_version` `9e513d70`.
`cortex_api` and `mcp` were recycled; all 11 services report running/healthy. The MCP
tool surface is live — `manage(action="whoami")` returns successfully.

### What actually happened

**Every `scripts.model_manager.ui` process on this host was spinning at 100% CPU and
never serving the asyncio event loop.** Measured by sampling `utime+stime` from
`/proc/<pid>/stat` before the fix:

| Process | Elapsed | CPU time | Verdict |
|---|---|---|---|
| `669567` — the "orphan" | 4h48m | 4h48m | pegged one core since 10:43 |
| `1176965` — a stray `ui status` | 2h39m | 2h39m | pegged one core since 12:52 |
| every controller started after 15:24 | — | — | pegged, never accepts |

**This corrects a claim in the diagnosis above, and one I repeated.** Checkpoint 3
established the orphan was receiving zero connections — true, and well evidenced by
fd accounting. It concluded from that the orphan was *inert*. It is not inert. It
was burning a full core continuously for nearly five hours. Nobody measured CPU,
so nobody saw it. Host load average fell from 2.81 to 1.87 within a minute of
clearing them.

**Critical correction:** the multiple long-lived 100%-CPU processes were **not**
competing controllers contending for the socket. They were **independent instances of
the same busy-spin bug** — each process trapped in its own tight loop, starved of the
event loop. The dual-controller orphan analysis (Checkpoints 1–5) remains valid for
socket attribution, but it is a **separate defect** from the spin that ultimately took
manage down.

### Root cause (`a4402bd5`)

In `wake_hub.py:115-118`, `WakeDirtySet.wait(timeout)` has a fast path: if
`self._roots` is non-empty it returns `True` immediately, awaiting nothing:

```python
    async def wait(self, timeout: float | None) -> bool:
        """Return True when the dirty event fired before timeout."""
        if self._roots:
            return True
```

In `wake_consumer.py`, `WakeConsumer._run_loop` used that same call as its *sleep* in
two "parked" branches — when a tick hold is held, and when `services_healthy()` is
false. Before the fix, both branches called `await self.dirty.wait(timeout=self.hold_poll_s)`.

With roots queued and the runner parked (the normal state at startup when the fleet is
down), the loop became `read_hold → wait returns instantly → continue`, spinning at 100%
CPU and never yielding to the asyncio event loop. Consequences: Textual TUI never
painted, and the manage API unix socket never answered — which is why it presented as a
socket problem.

**Fix:** both parked branches now `await asyncio.sleep(self.hold_poll_s)` (lines 302 and
305 after fix). The legitimate fast-path use at line 307
(`triggered = await self.dirty.wait(timeout=self.floor_interval_s)`) was deliberately left
unchanged. Regression test:
`scripts/model_manager/ui/controller/charter_runner/test_wake_consumer.py::test_parked_with_dirty_set_does_not_busy_spin`
— non-vacuous: fails on the buggy code with `"parked loop busy-spin: 501 hold polls"`,
passes after the fix.

### How it was found

`py-spy` as **parent** of the target sidesteps the yama ptrace restriction — no sudo
required. The open report's claim that unprivileged dump was refused and escalation was
needed was **wrong**.

```bash
py-spy record -o /tmp/manage-spin.txt -f raw -d 25 -s -- \
  "$HOME/.venvs/universal/bin/python" -m scripts.model_manager.ui
```

Hot frame: `wake_consumer.py:302` → `wake_hub.py:117`. **Reusable method:** when
`py-spy dump --pid` is blocked by ptrace scope, use `py-spy record -- <command>` so
py-spy launches the process as its child.

### What the spin is NOT (bisection results, still valid)

Ruled out by direct test during triage:

1. **Not the whoami or socket-alive commits.** Reverted `93964e3a` and `71b0b08a` (see
   `8ef43faa`, `0e2b5330`); a controller started on the reverted tree spun identically.
   Both commits were subsequently restored — `12d0e273` (whoami, originally `93964e3a`)
   and `9e513d70` (socket-alive guard, originally `71b0b08a`). Neither was the cause.
   The socket-alive guard is still a correct and worthwhile fix, but it was treating a
   symptom rather than this root cause.
2. **Not contention with the orphan or a stale socket.** Killed all manage processes,
   removed the socket path, started one controller on a clean slate. It spun identically.
   (This ruled out socket contention as the spin mechanism — not that the dual-controller
   orphan defect is imaginary.)
3. **Not the TUI render layer alone.** The `ui status` invocation spins the same way —
   but see secondary finding below.
4. **Not tmux pane geometry.** Pane is `225x49`, client attached and focused.

The spin also **does not respond to SIGINT**: `timeout -s INT 15` failed to stop a
`ui status` reproduction after 69 seconds, and the child climbed to 173% CPU
(multi-core). That points at a tight loop that never reaches a Python signal check.

### Secondary finding — `ui status` silently starts the full TUI (open, not fixed)

`scripts/model_manager/ui/__main__.py` accepts no arguments at all — it just calls
`run()`. So `python -m scripts.model_manager.ui status` silently ignores `status` and
launches the full TUI. Two of the long-lived spinners originated this way: pid `1176965`
from a mistaken "CLI status" invocation, and pid `669567` from a cursor-sdk dispatch
launching manage headless (`nohup setsid … </dev/null >/tmp/manage-restart.log 2>&1`).
A TUI with no terminal is a real hazard worth noting as a follow-up candidate — manage
arguably should reject a non-tty start or offer a real headless status path.

### False leads preserved (teaching value)

The open report at `ecadd73d` framed the spin as a **pre-existing, environmental defect
with unknown cause** and blocked restart on that basis. Both claims were wrong — the
cause was in `wake_consumer.py`, introduced by how parked branches used `dirty.wait()`,
and it was fixed in-tree.

Other claims asserted ahead of evidence:

1. Told the operator the wedge was "not our bind-path change" — before having evidence
   either way.
2. Then, on seeing the spin reproduce, called our change "the prime suspect" on a
   before/after correlation. Also wrong; the revert refuted it.
3. Repeated the diagnosis's "orphan is inert, receiving nothing" without measuring CPU.
   It was burning a core the whole time.
4. Correlated the spin with `cortex_api`/`mcp` restarts based on pid `2048906` (healthy
   until 15:05) vs later spinners. That correlation was never established; the healthy
   controller had simply not yet entered the parked-with-queued-roots state that triggers
   the loop.

The socket-contention framing survived several hours because it was a plausible wrong
hypothesis: multiple LISTEN sockets on the same path string *look* like competing
controllers, and killing the orphan did not stop the spin — which seemed to exonerate
"our" changes while leaving the mechanism mysterious. The actual mechanism was simpler:
every ui process, orphan or canonical, hit the same busy-spin independently.
