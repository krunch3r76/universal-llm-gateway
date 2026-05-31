"""
Prompt validation loop for the modularize finalize step.

Cycle per test case:
  1. Run the full modularize pipeline against the test file
  2. Submit (prompt + response) to a verifier model — "did it follow the instructions?"
  3. If violated → send (prompt + violations) to author model for revision
  4. Re-run and re-verify until compliant or max_iterations

Multiple test cases are run sequentially. A single violation in any case triggers
a revision, and ALL cases are re-run after each revision.
"""

import json
import pathlib
import re
import time
import urllib.request

STARGATE = "http://localhost:9999/v1/chat/completions"
PIPELINE_MODEL = "modularize"
AUTHOR_MODEL = "openai/gpt-5.2"
VERIFIER_MODEL = "openai/gpt-5.2-chat"
MAX_ITERATIONS = 3

# Test cases: (label, file_path, expected_branch)
# expected_branch: "decline" (modules should be []) or "split" (modules should be 2+)
TEST_CASES = [
    (
        "single-class / decline",
        "services/universal-stargate/systems/pipeline/core/execution/map_reduce/executor.py",
        "decline",
    ),
    (
        "multi-domain / split",
        "services/universal-stargate/systems/pipeline/core/execution/executor.py",
        "split",
    ),
    (
        "multi-domain / split",
        "services/universal-stargate/systems/pipeline/core/schemas.py",
        "split",
    ),
]

# ── helpers ──────────────────────────────────────────────────────────────────


def call_raw(model: str, messages: list[dict]) -> str:
    body = json.dumps({"model": model, "messages": messages}).encode()
    req = urllib.request.Request(
        STARGATE,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"]


def run_pipeline(file_path: str) -> str:
    source = pathlib.Path(file_path).read_text()
    messages = [{"role": "user", "content": source}]
    return call_raw(PIPELINE_MODEL, messages)


def parse_plan(raw: str) -> dict | None:
    clean = re.sub(r"```json\s*", "", raw)
    clean = re.sub(r"```\s*", "", clean).strip()
    # Find outermost JSON object
    start = clean.find("{")
    if start == -1:
        return None
    try:
        return json.loads(clean[start:])
    except Exception:
        return None


def verify(system_prompt: str, response: str, expected_branch: str) -> dict:
    branch_hint = (
        "For this test case the model SHOULD have set modules=[] with a decline_reason."
        if expected_branch == "decline"
        else "For this test case the model SHOULD have proposed 2+ modules (a genuine split)."
    )
    prompt = f"""You are a strict instruction-compliance auditor.

Below are the INSTRUCTIONS (system prompt) given to a model, followed by its RESPONSE.

{branch_hint}

## INSTRUCTIONS

{system_prompt}

## RESPONSE

{response}

## Your task

Check whether the response:
1. Populated "reasoning" FIRST with observable file-structure facts (class count, method counts, free function count, domain feasibility, explicit decision rule named)
2. Followed the Step 0 evidence-gathering requirements completely
3. Applied the correct decision rule (Step 1 size gate → Step 2 decline → Step 3 split)
4. Produced valid JSON with all required top-level keys: reasoning, improvements, modules, and (when required) decline_reason
5. Did NOT produce a 1-module result (that is forbidden)
6. Did NOT invent improvements not present in the critique

Output JSON only:
{{
  "compliant": true/false,
  "violations": [
    {{
      "instruction": "exact quote of the violated instruction",
      "actual": "what the model produced instead",
      "severity": "critical|warning|minor"
    }}
  ],
  "summary": "one sentence"
}}
"""
    raw = call_raw(VERIFIER_MODEL, [{"role": "user", "content": prompt}])
    clean = re.sub(r"```json\s*", "", raw)
    clean = re.sub(r"```\s*", "", clean).strip()
    start = clean.find("{")
    return json.loads(clean[start:])


def revise_prompt(current_prompt: str, violations: list[dict], label: str) -> str:
    violation_text = "\n".join(
        f"- [{v['severity'].upper()}] Test case: {label!r}\n"
        f'  Instruction: "{v["instruction"]}"\n'
        f"  Model did: {v['actual']}"
        for v in violations
    )
    prompt = f"""A verifier model found these instruction violations when running
the finalize step system prompt on a test case:

## Violations

{violation_text}

## Current system prompt

{current_prompt}

Revise the system prompt to prevent these violations while preserving all
existing correct behaviors. Output ONLY the revised system_prompt text —
no YAML, no wrapper, no explanation.
"""
    return call_raw(AUTHOR_MODEL, [{"role": "user", "content": prompt}])


def extract_system_prompt(prompts_yaml: str) -> str:
    m = re.search(
        r"  finalize:\n.*?system_prompt: \|\n(.*?)(?=\n    template:)",
        prompts_yaml,
        re.DOTALL,
    )
    if not m:
        raise ValueError("Could not find finalize system_prompt in prompts.yaml")
    return re.sub(r"^ {6}", "", m.group(1), flags=re.MULTILINE).rstrip()


def save_prompt(new_prompt: str, yaml_path: pathlib.Path) -> None:
    original = yaml_path.read_text()
    indented = re.sub(r"^", "      ", new_prompt, flags=re.MULTILINE)
    new_yaml = re.sub(
        r"(  finalize:\n.*?system_prompt: \|\n).*?(?=\n    template:)",
        lambda m: m.group(1) + indented,
        original,
        flags=re.DOTALL,
    )
    yaml_path.write_text(new_yaml)


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    yaml_path = pathlib.Path("pipelines/modularize/prompts.yaml")
    system_prompt = extract_system_prompt(yaml_path.read_text())

    print(
        f"Prompt validation loop — {len(TEST_CASES)} test cases, max {MAX_ITERATIONS} revision rounds\n"
    )
    print("=" * 70)

    for revision_round in range(1, MAX_ITERATIONS + 1):
        print(f"\n{'━' * 70}")
        print(f"  REVISION ROUND {revision_round}/{MAX_ITERATIONS}")
        print(f"{'━' * 70}")

        all_violations: list[tuple[str, list[dict]]] = []

        for label, file_path, expected_branch in TEST_CASES:
            short = pathlib.Path(file_path).name
            print(f"\n  ── {label} ({short}) ──")

            # Run pipeline
            print("    [run]    running full modularize pipeline...")
            t0 = time.monotonic()
            response = run_pipeline(file_path)
            elapsed = time.monotonic() - t0
            plan = parse_plan(response)
            if plan:
                decline = plan.get("decline_reason", "(none)")
                modules = len(plan.get("modules", []))
                reasoning_ok = bool(plan.get("reasoning"))
                print(
                    f"    [result] {elapsed:.1f}s — decline={decline!r}  modules={modules}  reasoning={'✓' if reasoning_ok else '✗'}"
                )
            else:
                print(f"    [result] {elapsed:.1f}s — could not parse JSON response")

            # Verify
            print("    [verify] checking compliance...")
            t0 = time.monotonic()
            verdict = verify(system_prompt, response, expected_branch)
            elapsed = time.monotonic() - t0
            compliant = verdict["compliant"]
            n_violations = len(verdict.get("violations", []))
            print(
                f"    [audit]  {elapsed:.1f}s — compliant={compliant}  violations={n_violations}"
            )
            print(f"             {verdict.get('summary', '')}")

            if not compliant:
                for v in verdict["violations"]:
                    print(
                        f"             [{v['severity'].upper()}] {v['instruction'][:72]!r}"
                    )
                    print(f"                      → {v['actual'][:72]!r}")
                all_violations.append((label, verdict["violations"]))

        if not all_violations:
            print(f"\n{'=' * 70}")
            print(
                f"  ✅  All {len(TEST_CASES)} test cases passed — prompt is compliant!"
            )
            print(f"{'=' * 70}")
            save_prompt(system_prompt, yaml_path)
            print(f"  Saved to {yaml_path}")
            return

        if revision_round == MAX_ITERATIONS:
            print(
                f"\n⚠️  Max revision rounds reached with {len(all_violations)} failing case(s)."
            )
            print("   Saving best prompt found.")
            save_prompt(system_prompt, yaml_path)
            return

        # Collect all critical violations across all failing cases and revise once
        all_critical = [
            (label, v)
            for label, viols in all_violations
            for v in viols
            if v["severity"] == "critical"
        ]
        to_fix_labeled = (
            all_critical
            if all_critical
            else [(label, v) for label, viols in all_violations for v in viols]
        )

        # Group by label for the revision request
        by_label: dict[str, list[dict]] = {}
        for label, v in to_fix_labeled:
            by_label.setdefault(label, []).append(v)

        print(
            f"\n  [revise] Requesting prompt revision ({len(to_fix_labeled)} violation(s) across {len(by_label)} case(s))..."
        )
        # Build combined violations text
        all_v_text = "\n".join(
            f"- [{v['severity'].upper()}] Case: {label!r}\n"
            f'  Instruction: "{v["instruction"]}"\n'
            f"  Model did: {v['actual']}"
            for label, v in to_fix_labeled
        )
        revise_prompt_text = f"""A verifier model found these instruction violations:

## Violations

{all_v_text}

## Current system prompt

{system_prompt}

Revise the system prompt to prevent these violations while preserving all
correct behaviors. Output ONLY the revised system_prompt text.
"""
        t0 = time.monotonic()
        system_prompt = call_raw(
            AUTHOR_MODEL, [{"role": "user", "content": revise_prompt_text}]
        )
        print(
            f"  [revise] done in {time.monotonic() - t0:.1f}s — {len(system_prompt)} chars"
        )

    print("\nDone.")


if __name__ == "__main__":
    main()
