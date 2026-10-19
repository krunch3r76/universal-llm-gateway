## In-flight guard — relocated table

Moved from `in-flight-work-guard_ulg.mdc`. The rule keeps the halt invariant; this table is the paired column.

| Forbidden | Permitted |
|---|---|
| Edit paths the dispatch owns; "head start while SDK runs" | Poll `agent_bus.wait` / read thread / harvest closeout; edits **outside** the overlap |
| Cancel queued cursor-sdk "as cleanup"; tear down a converse lane / live CSE | Author the packet **before** firing; lead verify **after** terminal closeout |
| Infer "no active CDP" from empty `active_work` while the private request lane runs | Read the private request thread — transport idle ≠ bus idle |
| Hop / re-stream / refuse PARK to "keep the tab alive" | PARK; tab ⟂ session (`decision:cse-tab-decoupled-from-session`) |

Doctrine: arc 6328 (wrong abort) · friction 6486/6488 (parallel writer) · `cursor_sdk_conductor_conflict.py`. Also `cdp-operator-proxy` § Interrupt / supersede · `claude-ai-cdp-navigation` · `git-posture`.
