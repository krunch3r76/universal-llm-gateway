"""Materialize recon/investigate packet from committed intent + template SOT."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .registry import LifeIntentRegistry, VerbSpec, load_registry

_TEMPLATE_REL = "notes/system/templates/recon-investigate-packet.md"


def _template_path() -> Path | None:
    for env in ("ULG_WORKSPACE_ROOT", "WORKSPACE_ROOT", "PROJECT_ROOT"):
        raw = os.environ.get(env)
        if raw:
            candidate = Path(raw).expanduser() / _TEMPLATE_REL
            if candidate.is_file():
                return candidate.resolve()
    repo = Path(__file__).resolve().parents[2]
    local = repo / _TEMPLATE_REL
    if local.is_file():
        return local
    cortex_files = os.environ.get("CORTEX_FILES_ROOT")
    if cortex_files:
        candidate = Path(cortex_files).expanduser() / _TEMPLATE_REL
        if candidate.is_file():
            return candidate.resolve()
    return None


def slug_from_subject(subject: str) -> str:
    slug = subject.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug[:50].strip("-") or "life-intent"


def todo_id_for_intent(normalized_intent: dict[str, Any]) -> str:
    return f"todo:life-intent-{slug_from_subject(normalized_intent['subject'])}"


def source_uri_for_todo(normalized_intent: dict[str, Any]) -> str:
    slug = slug_from_subject(normalized_intent["subject"])
    return f"cortex://notes/system/specs/{slug}.md"


def fill_recon_packet(
    normalized_intent: dict[str, Any],
    *,
    todo_id: str | None = None,
    registry: LifeIntentRegistry | None = None,
) -> str:
    """Render filled recon packet; detail quoted verbatim in scope."""
    reg = registry or load_registry()
    verb = normalized_intent["verb"]
    spec = reg.verbs[verb]
    todo_ref = todo_id or todo_id_for_intent(normalized_intent)
    detail = normalized_intent["detail"]
    refs = normalized_intent.get("refs") or []

    density = spec.density_triage_at_birth or "judgment_required"
    if verb == "investigate":
        scope_goal = f"Recon/investigate life intent for \"{normalized_intent['subject']}\"."
    elif verb == "fix":
        scope_goal = f"Bug-shaped recon for \"{normalized_intent['subject']}\"."
    else:
        scope_goal = f"Recon for life intent \"{normalized_intent['subject']}\" ({verb})."

    corpus_lines = [f"- {todo_ref} (entity_get)"]
    for ref in refs:
        corpus_lines.append(f"- {ref}")

    return f"""---
contract: consult
density_triage: {density}
todo: {todo_ref}
dispatch_lane: code
---

<scope>
{scope_goal}
Operator context (verbatim): \"{detail}\"
Goal: root cause + bound forks + zoom-out inventory → dense-spec inputs OR settlement escalate.
Output = findings + dispositions + (when closing investigate) dense-spec path / attr distillation pointers.
¬ patch set; ¬ implement.
</scope>

<invariants>
- Load skill: cheap-recon-before-escalation
- Load skill: consult-routing
- Load skill: friction-review
- Load skill: architecture-invariants
- [zoom-out:required] touch-point inventory + bug-class/sibling grep + ## Secondary findings (or None observed.)
- [authority_fork:stop] if fork touches provider default model string | anthropic/ identity | product/catalog identity | external-counterparty artifact | money-/risk-moving config | irreversible deletion ⇒ do-not-settle; tag authority_fork; escalate
- ¬ open-ended redesign under zoom-out cover
</invariants>

<task_guidance>
STEP 0 — Classify: mechanical | judgment_required | recon_pending.
STEP 1 — Cheap recon: caller/line inventory of touch points.
STEP 2 — Class sweep: bug-class / sibling grep across named surfaces.
STEP 3 — Settlement: bind forks blank-first.
STEP 4 — Zoom-out closeout (required): ## Secondary findings — or None observed.
STEP 5 — If investigate-close: distill files_expected / acceptance_criteria / required_skills.
</task_guidance>

<corpus>
{chr(10).join(corpus_lines)}
- cortex://notes/system/specs/life-intent-dispatch-v0.md
</corpus>

<mcp_capabilities>
0. cortex(entity_get, {todo_ref})
1. fs read of named paths
2. cortex(search …) / rag(op=search) as needed
Cite every call.
</mcp_capabilities>

<output_format>
1. Verdict line: RECON_COMPLETE | INVESTIGATE_CLOSE | ESCALATE_AUTHORITY_FORK | ESCALATE_DEADLOCK | BLOCKED
2. Touch-point inventory
3. Fork table (BOUND: / OPEN:)
4. ## Secondary findings (required — or None observed.)
5. Disposition each secondary: verify-now | flag-deferred | spin-ticket
6. If investigate-close: dense-spec URI + attr distillation checklist
</output_format>
"""


def entity_seed_payload(
    normalized_intent: dict[str, Any],
    *,
    todo_id: str | None = None,
    registry: LifeIntentRegistry | None = None,
) -> dict[str, Any] | None:
    """Implementation-seed floor for mutating verbs; None for investigate."""
    reg = registry or load_registry()
    spec: VerbSpec = reg.verbs[normalized_intent["verb"]]
    if not spec.creates_work_item:
        return None

    todo_ref = todo_id or todo_id_for_intent(normalized_intent)
    from .work_order import priority_for_intent

    return {
        "id": todo_ref,
        "type": spec.entity_kind or "todo",
        "name": normalized_intent["subject"],
        "source_uri": source_uri_for_todo(normalized_intent),
        "attributes": {
            "priority": priority_for_intent(normalized_intent, reg),
            "domain": "engineering",
            "density_triage": spec.density_triage_at_birth,
            "required_skills": list(spec.required_skills_seed),
        },
    }
