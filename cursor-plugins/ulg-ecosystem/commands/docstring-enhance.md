Enhance Python docstrings via **CDP Sonnet** (Claude subscription). Optional
architecture doc refresh. Stargate API pipeline is frontier-only override.

## Usage

```
/docstring-enhance {path}
/docstring-enhance {path} --with-architecture
/docstring-enhance frontier {path}
```

Where `{path}` is a Python file or directory relative to the project root.
**Default transport:** `team_dispatch(model=cdp/sonnet-5, purpose=produce)`
(effort Extra default; Max via `reasoning_effort=max` — `consult-routing` § CDP
transport). Prefer `team_dispatch(model=cdp/…)`; CLI `project-ask` = escape
only. Use `frontier` only when the operator explicitly approves paid Stargate
API cost.

## Instructions (gradual default — CDP)

Execute in order. Use the `claude-ai-cdp-navigation` skill (fleet skill-delivery
rule: Claude slug → `/` or `Use the`; not a Claude slug → **inline**).

### 1. Validate target + local quality scan

```bash
source ~/.venvs/universal/bin/activate
test -e {path}
scripts/docstring-quality scan "{path}" 2>&1 | tee /tmp/docstring-quality-scan.txt
```

If there are **zero** criticals and warnings are acceptable for arch-doc feedstock,
stop — no CDP needed. Otherwise continue.

### 2. Build inventory + stage cortex corpus

Pick an arc slug (overhaul root slug, or `docstring-enhance-{basename}`).

```bash
ARC="{arc_slug}"
DIR="{path}"   # directory preferred; if file, use its dirname for inventory
"$HOME/.venvs/universal/bin/python" - <<'PY'
import json
from pathlib import Path
from doc_extraction import extract_file_inventory, extract_subsystem_inventory

target = Path(r"""{path}""").resolve()
if target.is_dir():
    inv = extract_subsystem_inventory(str(target))
    inv["target_kind"] = "directory"
else:
    file_inv = extract_file_inventory(str(target))
    inv = {"target_kind": "file", "modules": [file_inv], "classes": [], "functions": []}
out = Path("/tmp/docstring-inventory.json")
out.write_text(json.dumps(inv, indent=2), encoding="utf-8")
print(out, "bytes", out.stat().st_size)
PY
```

Stage under `cortex://notes/system/threads/{arc_slug}/source/`:

- `docstring-quality-scan.txt` (from step 1)
- `docstring-inventory.json` (from above)

### 3. Seal CDP packet

Write `tmp/reviews/docstring-enhance-{slug}-cdp-packet.md` from template
`cortex://notes/system/templates/cdp-overhaul-docstring-enhance.md`
(fill `{directory}`, `{arc_slug}`; **inline** required non-slug excerpts;
engage `/no-silent-inference` + `/evidence-review-discipline`).

### 4. Submit + poll (prefer team_dispatch CDP)

```
team_dispatch(
  op="generate",
  model="cdp/sonnet-5",
  contract="light-bounded",
  purpose="produce",
  packet_path="tmp/reviews/docstring-enhance-{slug}-cdp-packet.md",
  # or sidecar_ref / prompt_uri after staging the sealed packet to cortex
  dispatch_thread_id="<arc-thread>"
)
# → poll_hint; poll via agent_bus.wait (not curl :8765)
```

**Escape only** (team_dispatch CDP unavailable): CLI
`scripts/cortex/claude-ai-sync-jupiter project-ask --converse --no-uuid
--model sonnet-5 --prompt-file …`. Never curl :8765 for project-ask.

### 5. Materialize apply payload + apply

Extract JSON with `review.edits` from the harvest into
`/tmp/docstring-enhance-result.json` (object root, not chat-completions wrap).

```bash
source ~/.venvs/universal/bin/activate
scripts/docstring-apply --input /tmp/docstring-enhance-result.json --target "{path}" --strict
```

Rules:
- Apply only `review.edits` — not speculative prose outside JSON.
- Keep signatures and behavior unchanged.
- `--strict` fails closed if any edit cannot be mapped unambiguously.

### 6. Re-verify

```bash
scripts/docstring-quality scan "{path}"
```

**Fail closed** on remaining criticals. Warnings: thicken further CDP pass or
accept only with CHECKPOINT note that arch-doc feedstock still has named gaps.

### 7. Optional architecture update (`--with-architecture`)

After criticals are clear, run `/overhaul` steps 9–11 for the directory
(**CDP Sonnet draft + CDP review** — ¬ Stargate `doc-generate` on gradual).

## Frontier override — Stargate API (paid)

Only under `/docstring-enhance frontier {path}` **and** operator cost approval:

```bash
curl -s http://localhost:9999/v1/models | jq '.data[] | select(.id == "docstring-enhance")'
TARGET_ABS="$(realpath "{path}")"
DOCSTRING_ENHANCE_RESPONSE="$(curl -s -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"docstring-enhance\",\"messages\":[{\"role\":\"user\",\"content\":\"${TARGET_ABS}\"}]}")"
echo "$DOCSTRING_ENHANCE_RESPONSE" > /tmp/docstring-enhance-response.json
# then parse choices[0].message.content → /tmp/docstring-enhance-result.json
# then scripts/docstring-apply as in step 5
```

Pipeline knobs (frontier only): `pipelines/docstring_enhance/{prompts,models}.yaml`.

## Rules

- Gradual default = **CDP Sonnet** — ¬ burn Stargate API for docstring enhance
- ¬ invent behavior; ground in inventory `body_source`
- ¬ write `docs/architecture/*` directly — use `--with-architecture` → overhaul 9–11
- Fleet skill delivery: Use the `claude-ai-cdp-navigation` skill § Skill delivery
