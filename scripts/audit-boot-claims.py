#!/usr/bin/env -S python3.12
"""Audit boot-injection claims against actual code behavior.

Scans operational-context render output, on-demand pointer targets,
docs/tool-reference.md cortex_boot params, operational-lessons.md
preamble, and shared markdown for self-claims that contradict the actual
cortex_boot implementation. Also checks that files agents are instructed
to read on-demand are actually surfaced via the renderer's pointer block.

Exit code: 0 if no drift, 1 if drift found.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORTEX_DATA = Path("/mnt/torus/mcp-data/files")

BOOT_TOOL_PATH = (
    REPO_ROOT / "services/mcp-server/tools/cortex_named_tools/_orchestration_tools.py"
)
BOOT_RUNNER_PATH = (
    REPO_ROOT / "services/mcp-server/tools/cortex_named_tools/_boot_runner.py"
)
OPS_CONTEXT_PATH = REPO_ROOT / "services/mcp-server/tools/_operational_context.py"
TOOL_REFERENCE_PATH = REPO_ROOT / "docs/tool-reference.md"
OPS_LESSONS_PATH = CORTEX_DATA / "notes/system/shared/operational-lessons.md"
SHARED_NOTES_DIR = CORTEX_DATA / "notes/system/shared"


@dataclass
class Finding:
    severity: str  # "critical" | "warning" | "info"
    category: str
    location: str
    claim: str
    reality: str

    def render(self) -> str:
        return (
            f"### [{self.severity.upper()}] {self.category}\n"
            f"- **Location**: `{self.location}`\n"
            f"- **Claim**: {self.claim}\n"
            f"- **Reality**: {self.reality}\n"
        )


@dataclass
class AuditReport:
    findings: list[Finding] = field(default_factory=list)

    def add(self, f: Finding) -> None:
        self.findings.append(f)

    def render(self) -> str:
        if not self.findings:
            return "# Boot Audit\n\nNo drift detected.\n"
        critical = [f for f in self.findings if f.severity == "critical"]
        warning = [f for f in self.findings if f.severity == "warning"]
        info = [f for f in self.findings if f.severity == "info"]
        out = [
            "# Boot Audit\n",
            f"**{len(critical)} critical · {len(warning)} warning · {len(info)} info**\n",
        ]
        for bucket, label in [
            (critical, "Critical"),
            (warning, "Warning"),
            (info, "Info"),
        ]:
            if bucket:
                out.append(f"\n## {label}\n")
                out.extend(f.render() for f in bucket)
        return "\n".join(out)


# === Check 1: cortex_boot tool signature vs documented params ===


def extract_cortex_boot_params() -> set[str]:
    """Parse the cortex_boot tool decorator function and return param names."""
    src = BOOT_TOOL_PATH.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "cortex_boot":
            return {arg.arg for arg in node.args.args if arg.arg != "self"}
    raise RuntimeError(f"cortex_boot function not found in {BOOT_TOOL_PATH}")


def extract_documented_boot_params() -> set[str] | None:
    """Parse docs/tool-reference.md for the cortex_boot params table.

    Returns:
      None  — cortex_boot section not found at all (docs missing)
      set() — section found, but params table failed to parse (regex no-op)
      {...} — params successfully extracted
    """
    text = TOOL_REFERENCE_PATH.read_text()
    boot_section = re.search(
        r"##\s+cortex_boot\b.*?(?=\n##\s+\w|\Z)",
        text,
        re.DOTALL,
    )
    if not boot_section:
        return None
    args_subsection = re.search(
        r"###\s+(?:Args|Params|Parameters)\b.*?(?=\n###?\s+\w|\Z)",
        boot_section.group(0),
        re.DOTALL,
    )
    if not args_subsection:
        return set()
    rows = re.findall(r"^\|\s+`?(\w+)`?\s+\|", args_subsection.group(0), re.MULTILINE)
    return {r for r in rows if r not in ("Arg", "Param", "Parameter", "param", "---")}


def check_documented_vs_actual_params(report: AuditReport) -> None:
    actual = extract_cortex_boot_params()
    documented = extract_documented_boot_params()
    if documented is None:
        report.add(
            Finding(
                severity="warning",
                category="cortex_boot section not found in tool-reference.md",
                location=str(TOOL_REFERENCE_PATH.relative_to(REPO_ROOT)),
                claim="docs/tool-reference.md should document cortex_boot",
                reality="No `### cortex_boot` (or `## cortex_boot`) section located — drift check skipped.",
            )
        )
        return
    if not documented:
        report.add(
            Finding(
                severity="info",
                category="cortex_boot params table failed to parse",
                location=str(TOOL_REFERENCE_PATH.relative_to(REPO_ROOT)),
                claim="cortex_boot section was found",
                reality=(
                    "Section located but the params table regex returned zero rows. "
                    "Either the table format changed or the params table was removed. "
                    "Manual review needed; drift check on documented-vs-actual params skipped."
                ),
            )
        )
        return
    phantom = documented - actual
    for param in phantom:
        report.add(
            Finding(
                severity="critical",
                category="Documented param does not exist",
                location=str(TOOL_REFERENCE_PATH.relative_to(REPO_ROOT)),
                claim=f"cortex_boot accepts `{param}`",
                reality=f"`{param}` not in tool signature {sorted(actual)}",
            )
        )


# === Check 2: post_file mechanism claim vs runner code ===


def check_post_file_claims(report: AuditReport) -> None:
    """Any markdown file in /shared/ claiming 'post_file' load that isn't
    referenced by the boot runner is making a false self-claim."""
    runner_src = BOOT_RUNNER_PATH.read_text()
    has_post_file_mechanism = "post_file" in runner_src or "post_files" in runner_src

    for md_file in SHARED_NOTES_DIR.glob("*.md"):
        text = md_file.read_text()
        preamble = "\n".join(text.splitlines()[:20])
        if re.search(r"post[_\- ]?file", preamble, re.IGNORECASE):
            if not has_post_file_mechanism:
                report.add(
                    Finding(
                        severity="critical",
                        category="Self-claimed auto-load mechanism does not exist",
                        location=str(md_file.relative_to(CORTEX_DATA)),
                        claim="Preamble claims auto-load via post_file mechanism",
                        reality=f"No 'post_file' reference in {BOOT_RUNNER_PATH.name}",
                    )
                )


# === Check 3: on-demand pointer target file existence ===


def extract_on_demand_pointers() -> list[tuple[str, Path]]:
    """Find paths referenced in the operational-context renderer's
    'On-Demand Modules' template."""
    src = OPS_CONTEXT_PATH.read_text()
    match = re.search(r'_ON_DEMAND_POINTERS\s*=\s*"""(.*?)"""', src, re.DOTALL)
    if not match:
        return []
    block = match.group(1)
    refs = re.findall(r'path="([^"]+)"', block)
    return [(p, CORTEX_DATA / p) for p in refs]


def check_pointer_targets_exist(report: AuditReport) -> None:
    for ref, target in extract_on_demand_pointers():
        if not target.exists():
            report.add(
                Finding(
                    severity="critical",
                    category="On-demand pointer target missing",
                    location=str(OPS_CONTEXT_PATH.relative_to(REPO_ROOT)),
                    claim=f"On-demand module: `{ref}`",
                    reality=f"File does not exist at {target}",
                )
            )


# === Check 4: agent-scoped sections in shared files ===


def check_agent_scoped_sections(report: AuditReport) -> None:
    """A section header like '### Foo (web-claude)' inside a file loaded
    identically by all agents is a silent inheritance bug — the section
    will reach agents it claims to exclude."""
    for md_file in SHARED_NOTES_DIR.glob("*.md"):
        text = md_file.read_text()
        if md_file.name.startswith("operational-context-"):
            continue
        for m in re.finditer(
            r"^#{2,4}\s+([^\n]*?\(([\w-]+(?:-claude)?)\s*(?:only)?\))",
            text,
            re.MULTILINE,
        ):
            heading = m.group(1)
            scope = m.group(2)
            report.add(
                Finding(
                    severity="warning",
                    category="Agent-scoped section in shared file",
                    location=f"{md_file.relative_to(CORTEX_DATA)}",
                    claim=f"Section `{heading}` is scoped to {scope}",
                    reality=(
                        "Shared file loaded identically by all agents — "
                        f"section reaches every agent, not just {scope}. "
                        "Either move to per-agent file or rely on agent honoring scope tag."
                    ),
                )
            )


# === Check 5: stale 'last update' claims ===


def check_last_update_freshness(report: AuditReport) -> None:
    """A file with a 'Last structural update: YYYY-MM-DD' footer older
    than 90 days that's also referenced as on-demand should be flagged."""
    pointer_targets = {target for _, target in extract_on_demand_pointers()}
    for target in pointer_targets:
        if not target.exists():
            continue
        text = target.read_text()
        m = re.search(
            r"[Ll]ast (?:structural )?update[:\s]+(\d{4}-\d{2}-\d{2})",
            text,
        )
        if m:
            from datetime import date

            last = date.fromisoformat(m.group(1))
            age_days = (date.today() - last).days
            if age_days > 90:
                report.add(
                    Finding(
                        severity="info",
                        category="Stale on-demand reference",
                        location=str(target.relative_to(CORTEX_DATA)),
                        claim=f"Last update {m.group(1)} ({age_days} days ago)",
                        reality="Consider review or removal",
                    )
                )


# === Check 6: claimed on-demand paths not in renderer pointer block ===

_ON_DEMAND_INSTRUCTION = re.compile(
    r"""fs\s*\(\s*sandbox\s*=\s*["']cortex["']\s*,\s*"""
    r"""op\s*=\s*["']read["']\s*,\s*"""
    r"""path\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE | re.DOTALL,
)


def check_claimed_on_demand_unreferenced(report: AuditReport) -> None:
    """A shared markdown file that instructs agents to read another file
    on demand, but whose target isn't in `_ON_DEMAND_POINTERS`, makes the
    target effectively dormant: discoverable only by agents that already
    know the path. Boot's section manifest is what makes a file a known
    quantity for an agent reading the boot output cold.

    First-run expectation: this fires on `notes/system/shared/model-tier-awareness.md`,
    which is referenced from `operational-lessons.md` as the canonical
    taxonomy for the model-tier protocol but is not listed in the renderer's
    pointer block. The protocol's effective dormancy (Discovery Finding F3)
    is downstream of this discoverability gap.
    """
    pointer_paths = {ref for ref, _target in extract_on_demand_pointers()}
    if not pointer_paths:
        return
    for md_file in SHARED_NOTES_DIR.glob("*.md"):
        if md_file.name.startswith("operational-context-"):
            continue
        text = md_file.read_text()
        for m in _ON_DEMAND_INSTRUCTION.finditer(text):
            claimed_path = m.group(1)
            if claimed_path in pointer_paths:
                continue
            target = CORTEX_DATA / claimed_path
            target_status = "exists" if target.exists() else "missing"
            report.add(
                Finding(
                    severity="warning",
                    category="Claimed-on-demand path not in renderer pointer block",
                    location=str(md_file.relative_to(CORTEX_DATA)),
                    claim=f"Agents instructed to read `{claimed_path}` on demand",
                    reality=(
                        f"Target file is {target_status} but not listed in "
                        "`_ON_DEMAND_POINTERS` — boot's section manifest will "
                        "not surface this file to agents reading the boot output "
                        "cold. The on-demand instruction is a hidden contract, "
                        "discoverable only by agents that already know the path. "
                        "Either add to `_ON_DEMAND_POINTERS` or remove the read "
                        "instruction."
                    ),
                )
            )


# === Driver ===


def main() -> int:
    report = AuditReport()
    check_documented_vs_actual_params(report)
    check_post_file_claims(report)
    check_pointer_targets_exist(report)
    check_agent_scoped_sections(report)
    check_last_update_freshness(report)
    check_claimed_on_demand_unreferenced(report)

    out_path = None
    if "--out" in sys.argv:
        idx = sys.argv.index("--out")
        out_path = Path(sys.argv[idx + 1])

    rendered = report.render()
    if out_path:
        out_path.write_text(rendered)
        print(f"Wrote {out_path}")
    else:
        print(rendered)

    critical = [f for f in report.findings if f.severity == "critical"]
    return 1 if critical else 0


if __name__ == "__main__":
    sys.exit(main())
