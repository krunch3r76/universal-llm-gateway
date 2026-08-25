"""Wire-contract records — single source for names, operator prose, executor flags.

Intake validation stays in mcp-server. This module is data + derived sets only.
Operator surfaces may consume name/purpose/closeout_shape/aliases.
Executor surfaces may consume name + routing flags. Do not mix.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContractRecord:
    """One agent-bus request ``contract`` token and its surface fields."""

    name: str
    purpose: str
    closeout_shape: str
    nested_scope: bool = False
    vision_required: bool = False
    code_work: bool = False
    briefing_stanza: str | None = None


RECORDS: tuple[ContractRecord, ...] = (
    ContractRecord(
        name="answer",
        purpose="inline relay; does not execute work",
        closeout_shape="disposition:answered + inline relay",
    ),
    ContractRecord(
        name="confer",
        purpose="codebase-grounded recommendation",
        closeout_shape="codebase-grounded recommendation",
        briefing_stanza="confer",
    ),
    ContractRecord(
        name="ask",
        purpose="read-only how-it-works opener (life coding aperture)",
        closeout_shape="how-it-works in ≤12 lines + file:line anchors",
        briefing_stanza="ask",
    ),
    ContractRecord(
        name="investigate",
        purpose="findings / nested dispatch summary",
        closeout_shape="findings / nested dispatch summary",
        nested_scope=True,
        vision_required=True,
        code_work=True,
        briefing_stanza="codework",
    ),
    ContractRecord(
        name="implement",
        purpose="file changes + AC evidence",
        closeout_shape=(
            "file changes + AC evidence (codework: ``abstraction-layering`` lane)"
        ),
        nested_scope=True,
        vision_required=True,
        code_work=True,
        briefing_stanza="codework",
    ),
    ContractRecord(
        name="verify",
        purpose="verification verdict + evidence",
        closeout_shape=(
            "verification verdict + evidence "
            "(codework: ``abstraction-layering`` G6)"
        ),
        nested_scope=True,
        code_work=True,
        briefing_stanza="codework",
    ),
    ContractRecord(
        name="execute",
        purpose="one tier-M op raw payload",
        closeout_shape="one tier-M op raw payload (body: tool_op + effects_expected)",
    ),
    ContractRecord(
        name="propagate",
        purpose="propagation ledger + drain-gated restart status",
        closeout_shape="propagation ledger + drain-gated restart status",
    ),
    ContractRecord(
        name="seed",
        purpose="mint a closable work item via the seed path",
        closeout_shape=(
            "todo slug + consult URI (if any) + ``abstraction-layering`` entry gate"
        ),
        nested_scope=True,
        vision_required=True,
        code_work=True,
        briefing_stanza="seed",
    ),
    ContractRecord(
        name="recon",
        purpose="cheap recon findings before implement or escalate",
        closeout_shape="recon_core findings (+ optional recon_extra)",
        nested_scope=True,
        vision_required=True,
        code_work=True,
        briefing_stanza="codework",
    ),
)

DEPRECATED_ALIASES: dict[str, str] = {"consult": "confer"}
DEFAULT_CONTRACT: str = "answer"

CANONICAL_CONTRACTS: tuple[str, ...] = tuple(record.name for record in RECORDS)


def vocab_line() -> str:
    """Operator-facing ``a | b | c`` enumeration of canonical names."""
    return " | ".join(CANONICAL_CONTRACTS)


def closeout_table() -> str:
    """Markdown CLOSEOUT-shape table for operator descriptors."""
    rows = ["| contract | CLOSEOUT carries |", "|---|---|"]
    for record in RECORDS:
        rows.append(f"| {record.name} | {record.closeout_shape} |")
    return "\n".join(rows)


def nested_scope_contracts() -> frozenset[str]:
    return frozenset(record.name for record in RECORDS if record.nested_scope)


def vision_required_contracts() -> frozenset[str]:
    return frozenset(record.name for record in RECORDS if record.vision_required)


def vision_required_admit_disclosure(*, wire_style: bool = False) -> str:
    """Operator prose for the vision-field admit gate — derived from ``RECORDS``.

    ``wire_style=True`` matches fol_descriptor / inline tool copy (no backticks).
    """
    members = sorted(vision_required_contracts())
    joined = "|".join(members)
    if wire_style:
        return (
            f"{joined} DIRECTIVE body requires vision: else admit blocks "
            "vision_field_missing (pre-model). See agent_skill:cdp-operator-proxy."
        )
    contract_set = ", ".join(members)
    return (
        f"``contract`` ∈ {{{contract_set}}} ⇒ DIRECTIVE body MUST include a "
        f"``vision:`` line or Auto blocks at admit (``vision_field_missing``) "
        f"before a model runs."
    )


def code_work_contracts() -> frozenset[str]:
    return frozenset(record.name for record in RECORDS if record.code_work)
