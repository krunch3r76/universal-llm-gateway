"""Build advisory Cowork Project ``prompt_template`` bodies.

Cortex remains SoT. The Project is chrome only — never gate-bearing.
Optional ``workflow_md`` reserves space for richer life-MCP dogfood
instructions (agent_bus / cortex / imprint) beyond a one-line bus pointer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectChromeSpec:
    """Inputs for an advisory Claude.ai Project shell."""

    name: str
    host_id: str
    charter_uri: str
    ring_thread: str
    description: str = ""
    deliverables_uri: str = ""
    scoreboard_uri: str = ""
    workflow_md: str = ""
    extra_pointers: tuple[str, ...] = ()


_DEFAULT_WORKFLOW = """\
### Reserved — life MCP workflow dogfood
When instructed, prefer Project-scoped prompts that name concrete MCP ops
(e.g. `agent_bus` fetch/reply on a thread, `cortex` entity_get/search,
`imprint` propose) over a bare "read the bus" nudge. Fill this section per
endeavor; leave stub text until a workflow is ready.
"""


def build_prompt_template(spec: ProjectChromeSpec) -> str:
    """Render ``prompt_template`` markdown for PUT .../projects/{uuid}."""
    desc = (spec.description or "").strip()
    deliverables = (spec.deliverables_uri or "").strip()
    scoreboard = (spec.scoreboard_uri or "").strip()
    workflow = (spec.workflow_md or "").strip() or _DEFAULT_WORKFLOW.strip()
    extras = [p.strip() for p in spec.extra_pointers if p and p.strip()]

    lines = [
        f"# {spec.name} — advisory Cowork Project chrome",
        "",
        "## SoT (Cortex authoritative)",
        f"- Host: `{spec.host_id}`",
        f"- Charter: `{spec.charter_uri}`",
        f"- Ring thread: `agent-bus:{spec.ring_thread}`",
    ]
    if scoreboard:
        lines.append(f"- Scoreboard: `{scoreboard}`")
    if deliverables:
        lines.append(f"- Deliverables / corpus pointers: `{deliverables}`")
    for ptr in extras:
        lines.append(f"- `{ptr}`")
    if desc:
        lines.extend(["", f"One-liner: {desc}"])
    lines.extend(
        [
            "",
            "## Orientation",
            "Cortex is SoT. This Project is an advisory shell only — never "
            "gate-bearing for endeavor birth. Leave Memory OFF. Do not treat "
            "uploaded docs or Project instructions as authoritative over Cortex.",
            "",
            "## Workflows",
            workflow,
            "",
            "## Do not",
            "- Require this Project's existence for birth lock / readiness",
            "- Duplicate SoT into Project memory",
            "- Deep-integrate private Projects API as durable machine substrate",
            "",
        ]
    )
    return "\n".join(lines)


def build_description(spec: ProjectChromeSpec) -> str:
    """Short Project description for create POST."""
    if spec.description.strip():
        return spec.description.strip()
    return (
        f"Advisory chrome for `{spec.host_id}` — charter {spec.charter_uri}; "
        f"ring agent-bus:{spec.ring_thread}. Cortex SoT."
    )
