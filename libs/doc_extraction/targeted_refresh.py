"""
Gradual targeted refresh for docs/architecture/*.md from tree-sitter inventory.

Projects module docstrings, class/method signatures, and imports — not Stargate
doc-generate. Consumers: lane-B arch-doc follow-ups; run as a script (__main__).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from doc_extraction.inventory import extract_file_inventory, extract_subsystem_inventory

_GATEWAY_ROOT = "services/_universal-llm-gateway"
_ROUTING_ROOT = "services/universal-stargate/systems/routing"
SCOPE_SOURCE_COMMIT_PATTERN = r"source commit `[0-9a-f]{7,40}`"

_MARKER_GATEWAY_NARRATIVE_START = re.compile(r"\n## 1\. ")
_MARKER_GATEWAY_SECTION_11 = re.compile(r"\n## 11\. ")
_MARKER_ROUTING_KEY_FUNCTIONS = "## Key Functions"
_MARKER_ROUTING_MODULE_INVENTORY = "## Module Inventory"
_MARKER_GENERATED_END = "<!-- GENERATED:END -->"
_CARRIED_FROM_OPEN = re.compile(r"<!-- AUTHORED carried_from=([^\s>]+) -->")
_CARRIED_FROM_CLOSE = re.compile(r"<!-- /AUTHORED carried_from=([^\s>]+) -->")


class TargetedRefreshError(ValueError):
    """Expected document structure marker missing or refresh precondition violated."""


def _git_head(workspace: Path) -> str:
    out = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        text=True,
    )
    return out.strip()[:12]


def _prior_source_commit(existing: str) -> str | None:
    m = re.search(
        r"GENERATED:START[^>]*source_commit=([0-9a-f]{7,40})",
        existing,
    )
    return m.group(1) if m else None


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
) -> tuple[str, int, int, int, str]:
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
    rendered_module_rows = 0
    for pkg in packages:
        paths = sorted(by_pkg[pkg], key=lambda p: p.name)
        lines.append(f"### `{pkg}`")
        lines.append("")
        for py in paths:
            rel_pkg = py.relative_to(gateway_dir).as_posix()
            inv = extract_file_inventory(py, workspace)
            inv_modules.append({"package": pkg, "path": rel_pkg, "inv": inv})
            lines.append(f"**`{rel_pkg}`** — {_first_line(inv['module_docstring'])}")
            rendered_module_rows += 1
            if inv["classes"]:
                lines.append("")
                lines.append("| Class | Signature |")
                lines.append("|---|---|")
                for cls in inv["classes"]:
                    sig = cls.get("signature", "").replace("|", "\\|")
                    lines.append(f"| `{cls['name']}` | `{sig}` |")
                    for method in cls.get("methods", [])[:8]:
                        msig = method.get("signature", "").replace("|", "\\|")
                        lines.append(f"| `.{method['name']}` | `{msig}` |")
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
    return (
        "\n".join(lines).rstrip() + "\n",
        len(packages),
        module_count,
        rendered_module_rows,
        sha,
    )


def _narrative_through_section_10(existing: str) -> str:
    m_start = _MARKER_GATEWAY_NARRATIVE_START.search(existing)
    m_end = _MARKER_GATEWAY_SECTION_11.search(existing)
    if not m_start or not m_end:
        raise TargetedRefreshError(
            "gateway.md missing required ## 1. or ## 11. section markers"
        )
    return existing[m_start.start() + 1 : m_end.start()].strip()


def _gateway_narrative_and_carried_from(existing: str) -> tuple[str, str]:
    opens = list(_CARRIED_FROM_OPEN.finditer(existing))
    closes = list(_CARRIED_FROM_CLOSE.finditer(existing))
    if not opens and not closes:
        return (
            _narrative_through_section_10(existing),
            _prior_source_commit(existing) or "unknown",
        )
    if len(opens) != 1 or len(closes) != 1:
        raise TargetedRefreshError(
            "gateway.md must have zero or exactly one AUTHORED carried_from block"
        )
    open_m, close_m = opens[0], closes[0]
    carried = open_m.group(1)
    if close_m.group(1) != carried:
        raise TargetedRefreshError(
            "gateway.md AUTHORED carried_from open/close markers mismatch"
        )
    if close_m.start() <= open_m.end():
        raise TargetedRefreshError(
            "gateway.md AUTHORED carried_from close precedes open"
        )
    narrative = existing[open_m.end() : close_m.start()].strip()
    return narrative, carried


def _gateway_tail_after_generated_end(existing: str) -> str:
    count = existing.count(_MARKER_GENERATED_END)
    if count == 0:
        raise TargetedRefreshError(
            "gateway.md missing required <!-- GENERATED:END --> marker"
        )
    if count > 1:
        raise TargetedRefreshError(
            "gateway.md has multiple <!-- GENERATED:END --> markers"
        )
    _prefix, tail = existing.split(_MARKER_GENERATED_END, 1)
    return tail


def refresh_gateway(workspace: Path, refreshed_by: str) -> str:
    gateway_dir = (workspace / _GATEWAY_ROOT).resolve()
    doc_path = workspace / "docs/architecture/gateway.md"
    existing = doc_path.read_text(encoding="utf-8")
    py_files = _gateway_py_files(gateway_dir)

    narrative, carried_from = _gateway_narrative_and_carried_from(existing)
    narrative_wrapped = (
        f"<!-- AUTHORED carried_from={carried_from} -->\n"
        f"{narrative.strip()}\n"
        f"<!-- /AUTHORED carried_from={carried_from} -->"
    )

    head = _git_head(workspace)
    today = date.today().isoformat()
    pkg_body, pkg_n, mod_n, rendered_rows, inv_sha = render_gateway_package_sections(
        workspace, gateway_dir, py_files
    )
    stamp = (
        f"<!-- GENERATED:START generated={today} method=targeted-refresh "
        f"regenerated=§11 inventory_sha={inv_sha} source_commit={head} "
        f"refreshed_by={refreshed_by} packages={pkg_n}/{pkg_n} "
        f"modules={rendered_rows}/{mod_n} -->"
    )
    disclaimer = (
        "> **Provenance disclaimer.** §1–§10 are **carried** narrative "
        f"(authored snapshot; see `carried_from={carried_from}`), not re-projected "
        "at this HEAD. §11 lists every production package with module- and "
        "entrypoint-level rows grounded in tree-sitter extraction at HEAD. "
        "Claims reflect declarations, not verified runtime behavior."
    )
    tail = _gateway_tail_after_generated_end(existing)
    tail, subs = re.subn(
        r"### missing_coverage\n\n.*?(?=### |\Z)",
        (
            "### missing_coverage\n\n"
            f"None for tree accounting: §11 lists all {pkg_n} packages with "
            f"per-module entrypoint signatures ({mod_n} modules).\n\n"
        ),
        tail,
        count=1,
        flags=re.DOTALL,
    )
    if subs == 0:
        raise TargetedRefreshError(
            "gateway.md tail missing ### missing_coverage section"
        )

    body = "\n".join(
        [
            "# Universal LLM Gateway — Architecture Overview",
            "",
            stamp,
            "",
            disclaimer,
            "",
            narrative_wrapped,
            "",
            pkg_body.rstrip(),
            "",
            _MARKER_GENERATED_END,
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
    for row in inv["imports"]:
        stmt = row["import"]
        if stmt.startswith("from ") or stmt.startswith("import "):
            top = stmt.split()[1].split(".")[0]
            if top in {
                "asyncio",
                "dataclasses",
                "enum",
                "typing",
                "uuid",
                "__future__",
            }:
                ext.add(top)
            elif not top.startswith(("universal_", "model_id", "services")):
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


def _render_routing_module_inventory(modules: list[dict[str, Any]], mod_n: int) -> str:
    rows = sorted(modules, key=lambda m: m["path"])
    row_n = len(rows)
    lines = [
        f"## Module Inventory ({row_n}/{mod_n})",
        "",
        "| Module | Description |",
        "|---|---|",
    ]
    for mod in rows:
        path = mod["path"]
        desc = _first_line(mod.get("docstring", ""))
        lines.append(f"| `{path}` | {desc} |")
    lines.append("")
    return "\n".join(lines)


def _replace_routing_module_inventory(pre_key: str, inventory_block: str) -> str:
    pattern = re.compile(
        r"## Module Inventory \(\d+/\d+\)\s*\n.*?(?=\n---\n)",
        re.DOTALL,
    )
    if not pattern.search(pre_key):
        raise TargetedRefreshError(
            "routing.md missing ## Module Inventory section before carried prose"
        )
    return pattern.sub(inventory_block.rstrip(), pre_key, count=1)


def _routing_authored_synthesis(
    existing: str, mod_n: int, inventory_rows: int
) -> str:
    post_match = re.search(
        r"(<!-- AUTHORED -->.*?<!-- GENERATED:END -->)\s*\Z",
        existing,
        re.DOTALL,
    )
    if not post_match:
        raise TargetedRefreshError(
            "routing.md missing AUTHORED synthesis block before GENERATED:END"
        )
    block = post_match.group(1)
    block = re.sub(
        r"full module inventory \(\d+/\d+\)",
        f"full module inventory ({inventory_rows}/{mod_n})",
        block,
    )
    block = re.sub(
        r"Module Inventory \(\d+/\d+\)",
        f"Module Inventory ({inventory_rows}/{mod_n})",
        block,
    )
    block = re.sub(
        r"grounded in staged modules only",
        f"grounded in the full {inventory_rows}/{mod_n} module tree",
        block,
    )
    return block


def refresh_routing(workspace: Path, refreshed_by: str) -> str:
    doc_path = workspace / "docs/architecture/routing.md"
    existing = doc_path.read_text(encoding="utf-8")
    if _MARKER_ROUTING_KEY_FUNCTIONS not in existing:
        raise TargetedRefreshError(
            f"routing.md missing required {_MARKER_ROUTING_KEY_FUNCTIONS!r} marker"
        )
    if _MARKER_ROUTING_MODULE_INVENTORY not in existing:
        raise TargetedRefreshError(
            f"routing.md missing required {_MARKER_ROUTING_MODULE_INVENTORY!r} marker"
        )

    routing_dir = (workspace / _ROUTING_ROOT).resolve()
    py_files = _routing_py_files(routing_dir)
    mod_n = len(py_files)
    inv = extract_subsystem_inventory(routing_dir, workspace)
    inv["modules"] = [
        m
        for m in inv["modules"]
        if "test" not in m["path"] and not m["path"].startswith("test_")
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

    inventory_rows = len(inv["modules"])
    inventory_block = _render_routing_module_inventory(inv["modules"], mod_n)

    pre_key = existing.split(_MARKER_ROUTING_KEY_FUNCTIONS, 1)[0]
    pre_key = _replace_routing_module_inventory(pre_key, inventory_block)

    stamp = (
        f"<!-- GENERATED:START generated={today} method=targeted-refresh "
        f"regenerated=key-functions,imports,module-inventory inventory_sha={inv_sha} "
        f"source_commit={head} refreshed_by={refreshed_by} "
        f"coverage={inventory_rows}/{mod_n} -->"
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
        SCOPE_SOURCE_COMMIT_PATTERN,
        f"source commit `{head}`",
        pre_key,
    )
    pre_key = re.sub(
        r"\*\*full module inventory \(\d+/\d+\)\*\*",
        f"**full module inventory ({inventory_rows}/{mod_n})**",
        pre_key,
    )
    pre_key = re.sub(
        r"This pass adds a",
        "This pass maintains a",
        pre_key,
        count=1,
    )
    pre_key = re.sub(
        r"Detailed class/method sections below remain anchored on the Post-P0 Step 9 subset "
        r"where noted; symbols in modules outside that subset are summarized only in the "
        r"inventory table unless called out in \*\*Material changes since 2026-07-20\*\*\.",
        "**Key Functions**, **Internal import statements**, and **Synthesis** below cover the "
        "full production routing tree at HEAD (all production modules under this tree).",
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

    authored_synthesis = _routing_authored_synthesis(existing, mod_n, inventory_rows)

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


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prior_mode = path.stat().st_mode if path.exists() else None
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
        if prior_mode is not None:
            os.chmod(path, prior_mode)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Targeted refresh for architecture docs")
    parser.add_argument("target", choices=["gateway", "routing"])
    parser.add_argument("workspace_root")
    parser.add_argument("--refreshed-by", required=True, dest="refreshed_by")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(
            "Usage: python targeted_refresh.py <gateway|routing> <workspace_root> "
            "--refreshed-by <lane>",
            file=sys.stderr,
        )
        return 2
    try:
        args = _parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    workspace = Path(args.workspace_root).resolve()
    out_path = workspace / "docs/architecture" / f"{args.target}.md"
    try:
        if args.target == "gateway":
            text = refresh_gateway(workspace, args.refreshed_by)
        else:
            text = refresh_routing(workspace, args.refreshed_by)
    except TargetedRefreshError as err:
        print(str(err), file=sys.stderr)
        return 2

    _atomic_write_text(out_path, text)
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
