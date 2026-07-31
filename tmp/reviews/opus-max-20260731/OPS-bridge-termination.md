# Orphaned cursor-sdk bridge termination — 2026-07-31T21:21:03Z

Operator-authorized (explicit) termination of 6 orphaned cursor-sdk bridge processes.
All `ppid=1` (reparented to init), spanning multiple dead git_integration_worker generations.

## State captured BEFORE termination

```
pid=666187 ppid=1 state=S rss_kb=67340 started=Fri Jul 31 10:42:19 2026 cpu_time=00:00:03
  home=auto-9066617448d4-home
pid=3153394 ppid=1 state=S rss_kb=67488 started=Thu Jul 30 22:24:46 2026 cpu_time=00:00:04
  home=auto-d6fa4d8aa590-home
pid=3209442 ppid=1 state=S rss_kb=67428 started=Thu Jul 30 22:43:01 2026 cpu_time=00:00:03
  home=auto-b4ed61f9747d-home
pid=3399774 ppid=1 state=S rss_kb=67376 started=Thu Jul 30 23:52:44 2026 cpu_time=00:00:05
  home=auto-06a050743111-home
pid=3428589 ppid=1 state=S rss_kb=70536 started=Fri Jul 31 00:03:14 2026 cpu_time=00:00:05
  home=auto-291a3eea026b-home
pid=3964056 ppid=1 state=S rss_kb=67336 started=Fri Jul 31 02:54:59 2026 cpu_time=00:00:02
  home=auto-735c7f861681-home
```

All six were `state=S` (sleeping) with 2–5 seconds of CPU time across up to 16
hours of wall clock, ~67 MB RSS each. None was driving a live session.

## Termination

SIGTERM to each pid; all six confirmed gone on read-back. ~400 MB reclaimed.
Targeted `kill -TERM <pid>` was used rather than `pkill` — these are cursor-sdk
bridge node processes, not managed ULG services, so this is not a lifecycle
action under `services_ws.mdc` § Scripts.

## git_integration_worker outage and restart

GIW was **not** killed by this operation. It shut down cleanly on its own at
14:14:33 local:

```
INFO:     Shutting down
git-worker drain started: intent_id=lifespan-87eac23d… epoch=1 reason=lifespan_shutdown active=0
git-worker drain completed
git-integration-worker stopped after 4967.3s
INFO:     Finished server process [1173131]
```

External SIGTERM, drained correctly with `active=0`, exited gracefully. Nothing
restarted it. Continuity of the dead process is established: a probe at
`uptime_s=4957` and its exit at `4967.3s` are 10 s apart on the same pid 1173131,
so there was no intervening restart.

Restarted by operator authorization via the sanctioned surface —
`manage.sock` JSON-RPC `{"method":"start","params":{"service":"git_integration_worker"}}`
(note: the status response key is hyphenated, but the RPC accepts only the
underscored form). New pid 1643048 on `127.0.0.1:8091`.

**Verified by observation, not inference** — `code_version a0ee596b`,
`uptime_s 20.29`; `55e69868` (closeout-relay fix) and `4f7367ff` (relay honesty
fix) are both ancestors of the running version, therefore live.

Caveat recorded honestly: GIW currently reports a `code_version` equal to
checkout HEAD. That is legitimate here — the process started from that
checkout seconds earlier — but it is the same shape as the cortex-api defect and
cannot discriminate while the two coincide. The discriminating re-test is to
re-probe after any commit moves HEAD.

## Incidental findings

- **The canonical manage controller (pid 765684, held `manage.sock` from 11:24) is gone.**
  Two processes now bind `/tmp/universal-protocol/manage.sock`: pid 669567,
  running from a **cursor-dispatch-home venv**
  (`…/cursor-dispatch-homes/auto-9066617448d4-home/.venvs/universal/`), and a
  later TUI. A controller serving fleet lifecycle out of a dispatch home is a
  provenance hazard worth a deliberate look.
- `cdp-ask` reports **running** under manage — this resolves the previously
  "inconclusive" propagation row, which had been inconclusive only because
  cdp-ask was assumed remote and probed directly instead of through manage.
- GIW logs a recurring unhandled error on `GET /api/v1/cursor/catalog`:
  `cursor_sdk.errors.ConfigurationError: missing_bridge_endpoint`
  (`services/git_integration_worker/routes/cursor_catalog.py:51`). Latent, not
  the cause of the outage.
