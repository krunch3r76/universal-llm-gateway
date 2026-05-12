# Role lint retired-corpus preflight — 2026-05-12

**Purpose**: falsification receipt requested by web review on `agent-bus:953`.

**Command**: queried Cortex `entities(type=ai_agent, limit=50)` and `entities(type=prompt, limit=50)`, selected deprecated/superseded items, and ran `role_lint.lint_role_payload()` against each item as if its name/description/purpose were a new `role:` payload.

**Result**: 27 retired entities checked. Six violated R2/R3; 21 passed.

## Violations

| Entity | Rules | Matched fragments |
|---|---|---|
| `agent:api_claude` | R2 | `Identity-bound` |
| `agent:bard` | R2 | `Identity-bound` |
| `agent:orion` | R2 | `Identity-bound` |
| `ai_agent:bard` | R3 | `The aperture`, `One of the music makers` |
| `ai_agent:web-claude` | R3 | `the mind that` |
| `ai_agent:cursor-claude` | R3 | `the mind whose` |

## Non-violating deprecated entities

These did not trip R1–R3. Some are operational descriptions, some are prompt-pointer wrappers, and some are persona-like but do not use the currently linted constructions. They are retained here as calibration pressure against overly broad future regexes:

`ai_agent:web-forge`, `ai_agent:web_forge`, `ai_agent:superheavy`, `ai_agent:forge`, `ai_agent:cursor_orion`, `agent:web`, `ai_agent:api-claude`, `ai_agent:oppie`, `ai_agent:orion`, `agent:oppie`, `agent:claude`, `agent:grok`, `prompt:web-forge-birth`, `prompt:forge-birth`, `prompt:cursor-grok-birth`, `prompt:api-claude-birth`, `prompt:bard-birth`, `prompt:cursor-claude-birth`, `prompt:oppie-birth`, `prompt:orion-birth`, `prompt:web-claude-birth`.

## Interpretation

The preflight confirms the lint catches the most salient retired identity constructions without rejecting every deprecated persona entity by name alone. Future additions should prefer construction-shaped patterns over broad role nouns (`advisor`, `reviewer`, `builder`, etc.).
