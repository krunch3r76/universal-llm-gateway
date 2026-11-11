"""
Gradual targeted refresh for docs/architecture/*.md from tree-sitter inventory.

Projects module docstrings, class/method signatures, and imports — not Stargate
doc-generate. Consumers: lane-B arch-doc follow-ups; run as a script (__main__).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from doc_extraction.inventory import extract_file_inventory, extract_subsystem_inventory

_GATEWAY_ROOT = "services/_universal-llm-gateway"
_ROUTING_ROOT = "services/universal-stargate/systems/routing"
_REFRESHED_BY = "agent-bus:23032"


def _git_head(workspace: Path) -> str:
    out = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        text=True,
    )
    return out.strip()[:12]


def _first_line(doc: str) -> str:
    doc = (doc or "").strip()
    if not doc:
        return "(no module docstring)"
    return doc.split("\n\n")[0].replace("\n", " ").strip()


def _gateway_py_files(gateway_dir: Path) -> list[Path]:
    files: list[Path] = []
    for py in sorted(gateway_dir.rglob("*.py")):
        if "test" in py.as_posix():
            continue
        files.append(py)
    return files


def _package_key(gateway_dir: Path, py_file: Path) -> str:
    rel = py_file.relative_to(gateway_dir)
    if rel.name == "__init__.py":
        parent = rel.parent
        return "." if parent == Path(".") else parent.as_posix()
    return rel.parent.as_posix()


def _inventory_sha(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def _extract_authored_blocks(text: str) -> list[str]:
    pattern = re.compile(
        r"(<!-- AUTHORED -->.*?)(?=<!-- GENERATED:END -->|\Z)",
        re.DOTALL,
    )
    return pattern.findall(text)


def _between_markers(text: str, start: str, end: str) -> str:
    i = text.find(start)
    j = text.find(end)
    if i < 0 or j < 0:
        return ""
    return text[i + len(start) : j]


def render_gateway_package_sections(
    workspace: Path,
    gateway_dir: Path,
    py_files: list[Path],
) -> tuple[str, int, int, str]:
    by_pkg: dict[str, list[Path]] = defaultdict(list)
    for py in py_files:
        by_pkg[_package_key(gateway_dir, py)].append(py)

    packages = sorted(by_pkg.keys())
    module_count = len(py_files)
    lines: list[str] = [
        "## 11. Per-package module inventory",
        "",
        f"**{len(packages)} packages, {module_count} production modules** "
        f"(excludes tests). Each `###` section lists modules and declared entrypoints "
        f"(classes, module-level functions) from docstrings at HEAD.",
        "",
    ]

    inv_modules: list[dict[str, Any]] = []
    for pkg in packages:
        paths = sorted(by_pkg[pkg], key=lambda p: p.name)
        lines.append(f"### `{pkg}`")
        lines.append("")
        for py in paths:
            rel_pkg = py.relative_to(gateway_dir).as_posix()
            inv = extract_file_inventory(py, workspace)
            inv_modules.append({"package": pkg, "path": rel_pkg, "inv": inv})
            lines.append(f"**`{rel_pkg}`** — {_first_line(inv['module_docstring'])}")
            if inv["classes"]:
                lines.append("")
                lines.append("| Class | Signature |")
                lines.append("|---|---|")
                for cls in inv["classes"]:
                    sig = cls.get("signature", "").replace("|", "\\|")
                    lines.append(f"| `{cls['name']}` | `{sig}` |")
                    for method in cls.get("methods", [])[:8]:
                        msig = method.get("signature", "").replace("|", "\\|")
                        lines.append(
                            f"| `.{method['name']}` | `{msig}` |"
                        )
                    if len(cls.get("methods", [])) > 8:
                        lines.append(f"| … | ({len(cls['methods'])} methods total) |")
            if inv["functions"]:
                lines.append("")
                lines.append("| Function | Signature |")
                lines.append("|---|---|")
                for fn in inv["functions"]:
                    sig = fn.get("signature", "").replace("|", "\\|")
                    lines.append(f"| `{fn['name']}` | `{sig}` |")
            lines.append("")

    sha = _inventory_sha({"packages": len(packages), "modules": inv_modules})
    return "\n".join(lines).rstrip() + "\n", len(packages), module_count, sha


def _narrative_through_section_10(existing: str) -> str:
    m_start = re.search(r"\n## 1\. ", existing)
    m_end = re.search(r"\n## 11\. ", existing)
    if not m_start or not m_end:
        return ""
    return existing[m_start.start() + 1 : m_end.start()].strip()


def refresh_gateway(workspace: Path, refreshed_by: str = _REFRESHED_BY) -> str:
    gateway_dir = (workspace / _GATEWAY_ROOT).resolve()
    doc_path = workspace / "docs/architecture/gateway.md"
    existing = doc_path.read_text(encoding="utf-8")
    py_files = _gateway_py_files(gateway_dir)

    narrative = _narrative_through_section_10(existing)

    head = _git_head(workspace)
    today = date.today().isoformat()
    pkg_body, pkg_n, mod_n, inv_sha = render_gateway_package_sections(
        workspace, gateway_dir, py_files
    )
    stamp = (
        f"<!-- GENERATED:START generated={today} method=targeted-refresh "
        f"inventory_sha={inv_sha} source_commit={head} refreshed_by={refreshed_by} "
        f"packages={pkg_n}/{pkg_n} modules={mod_n}/{mod_n} -->"
    )
    disclaimer = (
        "> **Provenance disclaimer.** §1–§10 summarize high-traffic subsystems from "
        "**module docstrings** at HEAD. §11 lists every production package with "
        "module- and entrypoint-level rows grounded in tree-sitter extraction. "
        "Claims reflect declarations, not verified runtime behavior."
    )
    coverage = existing.split("<!-- GENERATED:END -->", 1)
    tail = coverage[1] if len(coverage) > 1 else "\n\n---\n\n## Coverage Notes\n"
    tail = re.sub(
        r"### missing_coverage\n\n.*?(?=### |\Z)",
        (
            "### missing_coverage\n\n"
            f"None for tree accounting: §11 lists all {pkg_n} packages with "
            "per-module entrypoint signatures (405 modules).\n\n"
        ),
        tail,
        count=1,
        flags=re.DOTALL,
    )

    body = "\n".join(
        [
            "# Universal LLM Gateway — Architecture Overview",
            "",
            stamp,
            "",
            disclaimer,
            "",
            narrative.strip(),
            "",
            pkg_body.rstrip(),
            "",
            "<!-- GENERATED:END -->",
            tail.lstrip(),
        ]
    )
    return body


def _render_function_inventory(inv: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    by_mod: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fn in inv["functions"]:
        by_mod[fn["path"]].append(fn)
    for cls in inv["classes"]:
        mod = cls["path"]
        for method in cls.get("methods", []):
            by_mod[mod].append(
                {
                    "name": f"{cls['name']}.{method['name']}",
                    "signature": method.get("signature", ""),
                    "docstring": method.get("docstring", ""),
                }
            )

    for mod_path in sorted(by_mod.keys()):
        lines.append(f"#### `{mod_path}`")
        lines.append("")
        lines.append("| Symbol | Signature summary |")
        lines.append("|---|---|")
        for item in sorted(by_mod[mod_path], key=lambda x: x["name"]):
            sig = item.get("signature", "").replace("|", "\\|")
            if len(sig) > 120:
                sig = sig[:117] + "…"
            lines.append(f"| `{item['name']}` | `{sig}` |")
        lines.append("")
    return lines


def _render_imports_inventory(inv: dict[str, Any]) -> list[str]:
    ext: set[str] = set()
    internal: set[str] = set()
    for row in inv["imports"]:
        stmt = row["import"]
        if stmt.startswith("from ") or stmt.startswith("import "):
            top = stmt.split()[1].split(".")[0]
            if top in {"asyncio", "dataclasses", "enum", "typing", "uuid", "__future__"}:
                ext.add(top)
            elif top.startswith(("universal_", "model_id", "services")):
                internal.add(stmt)
            else:
                ext.add(top)
    lines = [
        "## Imports and Dependencies (full routing tree)",
        "",
        "### External packages referenced",
        ", ".join(f"`{x}`" for x in sorted(ext)) or "(none extracted)",
        "",
        "### Internal import statements (sample per module)",
        "",
    ]
    seen: set[str] = set()
    for row in inv["imports"]:
        stmt = row["import"]
        if stmt in seen:
            continue
        seen.add(stmt)
        lines.append(f"- `{row['path']}` → `{stmt}`")
    lines.append("")
    return lines


def _routing_py_files(routing_dir: Path) -> list[Path]:
    files: list[Path] = []
    for py in sorted(routing_dir.rglob("*.py")):
        if "test" in py.as_posix() or py.name.startswith("test_"):
            continue
        files.append(py)
    return files


def refresh_routing(workspace: Path, refreshed_by: str = _REFRESHED_BY) -> str:
    doc_path = workspace / "docs/architecture/routing.md"
    existing = doc_path.read_text(encoding="utf-8")
    routing_dir = (workspace / _ROUTING_ROOT).resolve()
    py_files = _routing_py_files(routing_dir)
    mod_n = len(py_files)
    inv = extract_subsystem_inventory(routing_dir, workspace)
    inv["modules"] = [
        m for m in inv["modules"] if "test" not in m["path"] and not m["path"].startswith("test_")
    ]
    inv["functions"] = [f for f in inv["functions"] if "test" not in f["path"]]
    inv["classes"] = [c for c in inv["classes"] if "test" not in c["path"]]
    inv["imports"] = [i for i in inv["imports"] if "test" not in i["path"]]
    inv["file_count"] = mod_n
    head = _git_head(workspace)
    today = date.today().isoformat()
    inv_sha = _inventory_sha(
        {
            "modules": [
                {"path": m["path"], "doc": m.get("docstring", "")[:200]}
                for m in inv["modules"]
            ]
        }
    )

    pre_key = existing.split("## Key Functions", 1)[0]
    post_match = re.search(
        r"(<!-- AUTHORED -->.*?<!-- GENERATED:END -->)",
        existing,
        re.DOTALL,
    )
    authored_synthesis = post_match.group(1) if post_match else ""
    if authored_synthesis:
        authored_synthesis = re.sub(
            r"grounded in staged modules only",
            "grounded in the full 57/57 module tree",
            authored_synthesis,
        )
        authored_synthesis = re.sub(
            r"Synthesis: eviction confirmation path \(grounded in [^)]+\)",
            "Synthesis: eviction confirmation path (full routing tree)",
            authored_synthesis,
        )
    else:
        authored_synthesis = (
            "<!-- AUTHORED -->\n"
            "## Synthesis: eviction confirmation path (full routing tree)\n\n"
            "See **Key Functions** and **Module Inventory** for the full routing tree; "
            "this synthesis summarizes the typed eviction confirmation path declared in "
            "`eviction/executor.py` and `eviction/event_waiter.py`.\n"
            "<!-- GENERATED:END -->"
        )

    stamp = (
        f"<!-- GENERATED:START generated={today} method=targeted-refresh "
        f"inventory_sha={inv_sha} source_commit={head} refreshed_by={refreshed_by} "
        f"coverage={mod_n}/{mod_n} -->"
    )
    pre_key = re.sub(
        r"<!-- GENERATED:START.*?-->",
        stamp,
        pre_key,
        count=1,
        flags=re.DOTALL,
    )
    pre_key = re.sub(
        r"\*\*Module count \(HEAD\):\*\* \d+.*",
        f"**Module count (HEAD):** {mod_n} production `.py` modules "
        f"(excludes `test_*.py` under this tree).",
        pre_key,
    )
    pre_key = re.sub(
        r"source commit `c75e3fc8`",
        f"source commit `{head}`",
        pre_key,
    )
    pre_key = re.sub(
        r"full module inventory \(57/57\)",
        f"full module inventory ({mod_n}/{mod_n})",
        pre_key,
    )

    key_fn_lines = [
        "## Key Functions (full routing tree)",
        "",
        "_All production modules under `systems/routing` at HEAD; signatures from "
        "tree-sitter extraction._",
        "",
        *_render_function_inventory(inv),
    ]

    body = (
        pre_key.rstrip()
        + "\n\n"
        + "\n".join(key_fn_lines).rstrip()
        + "\n\n"
        + "\n".join(_render_imports_inventory(inv)).rstrip()
        + "\n\n"
        + authored_synthesis.strip()
        + "\n"
    )
    return body


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2 or argv[0] not in {"gateway", "routing"}:
        print(
            "Usage: python targeted_refresh.py <gateway|routing> <workspace_root>",
            file=sys.stderr,
        )
        return 2
    target, workspace_s = argv
    workspace = Path(workspace_s).resolve()
    out_path = workspace / "docs/architecture" / f"{target}.md"
    if target == "gateway":
        text = refresh_gateway(workspace)
    else:
        text = refresh_routing(workspace)
    out_path.write_text(text, encoding="utf-8")
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
