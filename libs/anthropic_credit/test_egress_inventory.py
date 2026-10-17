"""A7 — scan for ungated Anthropic API egress outside the credit gate (PLAN r2 § A7)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_ROOTS = ("libs", "services", "scripts", "pipelines")
_EXTENSIONS = frozenset({".py", ".js", ".jsx", ".ts"})

_API_ANTHROPIC_RE = re.compile(r"api\.anthropic\.com")
_RESOLVE_ANTHROPIC_RE = re.compile(
    r"""resolve_llm_adapter\s*\(\s*["']anthropic["']\s*\)"""
)

# Every hit must be listed with a reason (reported in test docstring output on failure).
_ALLOWLIST: dict[str, str] = {
    "services/universal_cloud_proxy/config.py": (
        "Cloud-proxy provider default base URL; requests go through gated AnthropicAdapter"
    ),
    "services/cortex-workbench/CortexWorkbench.jsx": (
        "Browser-direct workbench UI outside ULG billing (r1 § 2.6)"
    ),
    "scripts/dispatch-anti-drift/declarative_probe.py": (
        "A8: Models API GET /v1/models only, not token-billed"
    ),
    "scripts/model_manager/ui/controller/service_config.py": (
        "Comment-only YAML example, not runtime egress"
    ),
    "libs/claude_bundles/skills_api.py": (
        "Skills upload API, not token-billed Messages"
    ),
}

_LINE_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "libs/llm_adapters/core.py": (
        r'os\.getenv\("ANTHROPIC_BASE_URL", "https://api\.anthropic\.com"\)',
    ),
    "libs/llm_adapters/anthropic/adapter.py": (
        r'os\.getenv\("ANTHROPIC_BASE_URL", "https://api\.anthropic\.com"\)',
    ),
}


def _is_test_or_fixture_path(rel: str) -> bool:
    name = Path(rel).name
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    if rel.startswith("tests/"):
        return True
    return False


def _iter_source_files() -> list[Path]:
    files: list[Path] = []
    for root_name in _SCAN_ROOTS:
        root = _REPO_ROOT / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix in _EXTENSIONS and path.is_file():
                rel = _rel_posix(path)
                if _is_test_or_fixture_path(rel):
                    continue
                files.append(path)
    return sorted(files)


def _rel_posix(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def _find_hits(pattern: re.Pattern[str], text: str) -> bool:
    return pattern.search(text) is not None


def test_egress_inventory_no_ungated_api_anthropic_or_resolve_calls() -> None:
    """Break covered: new direct Messages egress or resolve_llm_adapter('anthropic') bypasses gate."""
    hits: dict[str, list[str]] = {}
    for path in _iter_source_files():
        rel = _rel_posix(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        reasons: list[str] = []
        if _find_hits(_API_ANTHROPIC_RE, text):
            reasons.append("api.anthropic.com")
        if _find_hits(_RESOLVE_ANTHROPIC_RE, text):
            reasons.append('resolve_llm_adapter("anthropic")')
        if reasons:
            hits[rel] = reasons

    line_violations: list[str] = []
    for rel, reasons in hits.items():
        if "api.anthropic.com" not in reasons:
            continue
        path = _REPO_ROOT / rel
        allowed = _LINE_ALLOWLIST.get(rel, ())
        if allowed:
            for line_no, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if _API_ANTHROPIC_RE.search(line) and not any(
                    re.search(pat, line) for pat in allowed
                ):
                    line_violations.append(f"{rel}:{line_no}: {line.strip()}")
        elif rel not in _ALLOWLIST:
            line_violations.append(f"{rel}: (whole-file hit)")

    unlisted = sorted(
        rel
        for rel in hits
        if rel not in _ALLOWLIST and rel not in _LINE_ALLOWLIST
    )
    if line_violations:
        pytest.fail(
            "Ungated api.anthropic.com on non-allowlisted lines:\n"
            + "\n".join(f"  {v}" for v in line_violations)
        )
    if unlisted:
        detail = "\n".join(f"  {rel}: {hits[rel]}" for rel in unlisted)
        allowlisted_report = "\n".join(
            f"  {rel}: {_ALLOWLIST[rel]}" for rel in sorted(hits) if rel in _ALLOWLIST
        )
        msg = (
            "Ungated Anthropic egress (add to _ALLOWLIST with reason or route through cloud proxy):\n"
            f"{detail}\n\nAllowlisted hits this scan:\n{allowlisted_report}"
        )
        pytest.fail(msg)

    # Explicit receipt: every allowlisted path that matched is named.
    matched_allowlisted = [rel for rel in hits if rel in _ALLOWLIST]
    assert matched_allowlisted, "expected at least one known allowlisted hit in the tree"
    for rel in matched_allowlisted:
        assert rel in _ALLOWLIST
