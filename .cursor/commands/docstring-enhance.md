Enhance Python docstrings via prompt-tunable pipeline; optional architecture doc refresh.

## Usage

```bash
/docstring-enhance {path}
/docstring-enhance {path} --with-architecture
```

Where `{path}` is a Python file or directory relative to the project root.

## Instructions

Execute in order.

### 1. Validate target and pipeline availability

```bash
source ~/.venvs/universal/bin/activate
test -e {path}
curl -s http://localhost:9999/v1/models | jq '.data[] | select(.id == "docstring-enhance")'
```

If the pipeline is unavailable, stop and ask the user to reload services via
`./manage` so the updated pipeline registry is active.

### 2. Run docstring-enhance pipeline

```bash
TARGET_ABS="$(realpath "{path}")"
DOCSTRING_ENHANCE_RESPONSE="$(curl -s -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"docstring-enhance\",\"messages\":[{\"role\":\"user\",\"content\":\"${TARGET_ABS}\"}]}")"
echo "$DOCSTRING_ENHANCE_RESPONSE" > /tmp/docstring-enhance-response.json
```

Extract validated JSON payload:

```bash
DOCSTRING_ENHANCE_JSON="$(echo "$DOCSTRING_ENHANCE_RESPONSE" | jq -r '.choices[0].message.content // empty')"
echo "$DOCSTRING_ENHANCE_JSON" | jq . > /tmp/docstring-enhance-result.json
```

If parsing fails, stop and report with `/tmp/docstring-enhance-response.json`.

### 3. Apply proposed edits

Apply edits through the deterministic helper:

```bash
source ~/.venvs/universal/bin/activate
scripts/docstring-apply --input /tmp/docstring-enhance-result.json --target "{path}" --strict
```

Rules:
- Apply only edits in `review.edits` (final output), not intermediate drafts.
- Keep signatures and behavior unchanged.
- Preserve public API and import surface.
- `--strict` fails closed if any edit cannot be mapped unambiguously.

### 4. Verify docstring quality

```bash
source ~/.venvs/universal/bin/activate
if [ -d "{path}" ]; then
  scripts/docstring-quality scan "{path}"
else
  scripts/docstring-quality check "{path}"
  scripts/docstring-quality scan "$(dirname "{path}")"
fi
```

Fix critical issues and re-run until no critical findings remain.

### 5. Optional architecture update (`--with-architecture`)

When `--with-architecture` is provided:

1. Determine architecture generation directory:
   - If `{path}` is a directory: use `{path}`
   - If `{path}` is a file: use `$(dirname "{path}")`
2. Run `/overhaul {directory}` steps 9-11 procedure:
   - `doc-generate` run
   - generated doc review
   - frontier review
3. Replace `docs/architecture/{subsystem}.md` only after review passes.

For small targeted file-only enhancements, architecture refresh is optional.

## Prompt/Model tuning

Adjust behavior without code changes:
- Prompt tuning: `pipelines/docstring_enhance/prompts.yaml`
- Model tuning: `pipelines/docstring_enhance/models.yaml`

Validate after edits:

```bash
python scripts/validate-pipeline.py pipelines/docstring_enhance/
```

## Rules

- Do not bypass pipeline output with ad-hoc prompt text generation
- Do not apply ungrounded edits that invent behavior
- Do not write new `docs/architecture/*` files directly
- If pipeline reload is required, user restarts via `./manage` only
