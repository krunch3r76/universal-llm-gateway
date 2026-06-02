"""Consumer-path regression gate (todo:cursorbuild-green-gate-verifies-consumers).

When the diff-scoped green gate runs, this module exercises the three concrete
regression fixtures through their live consumer paths — not just the changed
boundary in isolation.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Fixture (a): live agent_bus _post_impl path
_FIXTURE_POST_IMPL = _REPO_ROOT / "services/mcp-server/test_agent_bus_post_impl.py"
# Fixture (b): dispatch admission ignores injected stream key
_FIXTURE_STREAM_INJECT = (
    _REPO_ROOT
    / "services/universal-stargate/systems/pipeline/core/handlers"
    / "test_frontier_dispatch.py::test_reject_unknown_runtime_options_ignores_injected_stream_flag"
)
# Fixture (c): import-time _delete_file resolution
_FIXTURE_INDEXING = _REPO_ROOT / "services/rag/test_indexing_consumer_resolution.py"

CONSUMER_FIXTURE_NODEIDS: tuple[str, ...] = (
    "services/mcp-server/test_agent_bus_post_impl.py",
    (
        "services/universal-stargate/systems/pipeline/core/handlers/"
        "test_frontier_dispatch.py::test_reject_unknown_runtime_options_ignores_injected_stream_flag"
    ),
    "services/rag/test_indexing_consumer_resolution.py",
)


@dataclass(frozen=True, slots=True)
class ConsumerGateResult:
    ok: bool
    detail: str


def run_consumer_verification(
    *,
    repo_root: Path | None = None,
    changed_py_files: list[str] | None = None,
) -> ConsumerGateResult:
    """Run the three consumer regression fixtures via pytest.

    ``changed_py_files`` is reserved for future symbol-scoped triggers; the MVP
    runs all fixtures whenever any Python file changed in the arc diff.
    """
    root = repo_root or _REPO_ROOT
    if changed_py_files is not None and not changed_py_files:
        return ConsumerGateResult(
            ok=True, detail="no .py changes; consumer gate skipped"
        )

    nodeids = [str(root / rel) for rel in CONSUMER_FIXTURE_NODEIDS]
    cmd = [sys.executable, "-m", "pytest", "-q", *nodeids]
    logger.info("cursorbuild consumer gate: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode == 0:
        return ConsumerGateResult(ok=True, detail="consumer fixtures passed")
    tail = (proc.stdout + proc.stderr)[-4000:]
    return ConsumerGateResult(
        ok=False,
        detail=f"consumer gate failed (exit {proc.returncode}):\n{tail}",
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="cursorbuild consumer verification gate"
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT,
        help="Repository root (default: workspace root)",
    )
    args = parser.parse_args()
    result = run_consumer_verification(repo_root=args.repo_root)
    if not result.ok:
        print(result.detail, file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
