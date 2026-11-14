# Runbook: claude.ai compose UI drift census

## Purpose

Periodic census of the claude.ai **compose** chrome on a fresh `/new` tab so selector drift is caught before CDP dispatches fail opaquely as `chip_missing`.

## Cadence

- **Weekly** on Jupiter fleet CDP (standing `fleet` lane, port **9222**), or after any claude.ai UI deploy rumor.
- **Ad hoc** when a CDP dispatch returns `failure_class=ui_drift` or `step=ui_drift`.

## Procedure

1. Open an isolated `/new` tab on fleet CDP (do not reuse a live CSE session).
2. Collect radiogroup census: document title, radiogroup names, per-group radio labels and `aria-checked`, mode-matching controls, and approval chip text.
3. Compare observed labels against the last known-good specimen:
   - Title `New session - Claude` with sidebar **Mode** radiogroup, radios **Claude** (checked) and **Code**, `New task` submit, approval chip **Manual**, and `[data-testid="chat-input"]` present ⇒ compose **readiness** attested (chat path).
   - Legacy specimen: **Surface** radiogroup with **Chat** / **Cowork** radios and title `New chat - Claude` / `New task - Claude`.
4. Record census JSON in the friction thread or cortex note; tag with `ui_census`.

## Failure policy

Unexpected observed labels **fail loudly** as `ui_drift`: `select_compose_mode` must return `ok=false`, `step=ui_drift`, `failure_class=ui_drift`, with `ui_census` and radiogroup names in the payload. Do not guess new Cowork controls from census alone — file friction and extend selectors with a DOM fixture test.

## Operational notes

cdp-lane@ask disabled, launcher no longer knows ask (looks retired), cdp_ask now falls back to fleet :9222 when ask is absent from pins.toml, StartLimitIntervalSec warning; stale-unit flag only.

## Verification commands

```bash
$HOME/.venvs/universal/bin/pytest libs/claude_bundles/test_compose_chip_probe.py -q -k 'readiness or ui_drift'
$HOME/.venvs/universal/bin/python scripts/cortex/cdp_approval_census.py
```