#!/usr/bin/env python3
"""CLI entry for the ontology-keyed fleet occupancy view (arc 6885 / 6899).

Fetches cdp-ask ``drain-state`` and the Jupiter registry (SSH), joins hub CSR
sessions, then prints intended-vs-actual. No LLM in the read path. Life seats
cannot call ``manage`` / ``project_ask`` — use ``--publish`` and ``fs``-read the
cortex snapshot. Compose/render logic lives in
``claude_bundles.what_is_running_view``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.what_is_running_view import (  # noqa: E402
    SNAPSHOT_URI,
    compose_view,
    render_text,
    serve_view,
)

_SNAPSHOT_REL = "notes/system/operational/what-is-running.json"


def _fetch_json(url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """GET JSON from a loopback/satellite URL; raises urllib errors to the caller."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_active_work(base_url: str) -> dict[str, Any]:
    """GET satellite drain state for labeled stream and attachment scalars."""
    return _fetch_json(f"{base_url.rstrip('/')}/v1/project-ask/drain-state")


def fetch_registry_via_ssh(ssh_target: str) -> dict[str, dict[str, Any]]:
    """Read Jupiter ``active.json`` over SSH so hub seats can join registrations."""
    remote = (
        "python3 -c \"import json,pathlib;"
        "p=pathlib.Path.home()/'.gateway/cdp-registry/active.json';"
        "print(p.read_text() if p.exists() else '{}')\""
    )
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", ssh_target, remote],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"registry ssh failed rc={proc.returncode}: {proc.stderr.strip()}"
        )
    data = json.loads(proc.stdout or "{}")
    if not isinstance(data, dict):
        raise RuntimeError("registry payload is not an object")
    return data


def load_local_sessions() -> dict[str, dict[str, Any]]:
    """Load hub CSR sessions for lane_thread join (may miss Jupiter-only regs)."""
    from claude_bundles.cdp_registry_store import load_sessions

    return load_sessions()


def load_hop_watches(host_home: str) -> dict[str, dict[str, Any]]:
    """Load hub hop-cadence watches used to join registration → bus lane."""
    path = Path(host_home) / ".gateway/cdp-registry/hop_cadence_watches.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def publish_cortex(view: dict[str, Any]) -> Path:
    """Atomically write the occupancy JSON to the life-readable cortex path."""
    from implement_admission.closeout_helpers import cortex_files_root

    dest = cortex_files_root() / _SNAPSHOT_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(view, indent=2, sort_keys=True) + "\n"
    tmp = dest.with_suffix(dest.suffix + f".tmp-{os.getpid()}")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, dest)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
    return dest


def _emit_overlap_findings(view: dict[str, Any]) -> None:
    """Promote OVERLAP findings to an emitted signal (compose_view stays pure)."""
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))
    findings = [
        f for f in (view.get("findings") or []) if f.get("verdict") == "OVERLAP"
    ]
    if not findings:
        return
    try:
        from claude_bundles.cdp_registry_events import cdp_occupancy_overlap, emit
    except Exception:
        emit = None  # type: ignore[assignment]
        cdp_occupancy_overlap = None  # type: ignore[assignment]
    try:
        from services.git_integration_worker.cursor_auto.hop_cadence_events import (
            emit_overlap,
        )
    except Exception:
        emit_overlap = None  # type: ignore[assignment]
    for finding in findings:
        lane = str(finding.get("lane") or "")
        execs = [str(x) for x in (finding.get("execution_ids") or [])]
        if emit_overlap is not None:
            emit_overlap(lane=lane, execution_ids=execs)
        if emit is not None and cdp_occupancy_overlap is not None:
            emit(cdp_occupancy_overlap(lane=lane, execution_ids=execs))


def build_from_env(
    *,
    project_ask_url: str | None = None,
    ssh_target: str | None = None,
    skip_registry: bool = False,
) -> dict[str, Any]:
    """Resolve PROJECT_ASK_URL / SSH / HOME and return one composed occupancy view."""
    base = (project_ask_url or os.environ.get("PROJECT_ASK_URL") or "").strip()
    if not base:
        raise SystemExit("PROJECT_ASK_URL unset — cannot fetch active-work")
    ssh = (
        ssh_target
        or os.environ.get("WHAT_IS_RUNNING_REGISTRY_SSH")
        or "krunch3r@jupiter"
    ).strip()
    sources = {"project_ask_url": base, "registry": "skipped"}
    active = fetch_active_work(base)
    registry: dict[str, dict[str, Any]] = {}
    if not skip_registry:
        registry = fetch_registry_via_ssh(ssh)
        sources["registry"] = f"ssh:{ssh}:~/.gateway/cdp-registry/active.json"
    prev_home = os.environ.get("HOME")
    host_home = os.environ.get("WHAT_IS_RUNNING_HOST_HOME", "/home/io")
    try:
        if host_home and Path(host_home).is_dir():
            os.environ["HOME"] = host_home
        sessions = load_local_sessions()
        sources["csr_sessions"] = f"HOME={os.environ.get('HOME')}"
    finally:
        if prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = prev_home
    watches = load_hop_watches(host_home)
    sources["hop_watches"] = str(
        Path(host_home) / ".gateway/cdp-registry/hop_cadence_watches.json"
    )
    return compose_view(
        active_work=active,
        registry=registry,
        sessions=sessions,
        hop_watches=watches,
        sources=sources,
    )


def main(argv: list[str] | None = None) -> int:
    """Parse CLI flags, compose the view, optionally publish, print text or JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine JSON")
    parser.add_argument(
        "--publish",
        action="store_true",
        help=f"Write {SNAPSHOT_URI} for life fs read",
    )
    parser.add_argument("--project-ask-url", default=None)
    parser.add_argument("--ssh-target", default=None)
    parser.add_argument(
        "--skip-registry",
        action="store_true",
        help="Streams-only (no SSH registry join)",
    )
    args = parser.parse_args(argv)
    view = build_from_env(
        project_ask_url=args.project_ask_url,
        ssh_target=args.ssh_target,
        skip_registry=args.skip_registry,
    )
    if args.publish:
        path = publish_cortex(view)
        print(f"published {SNAPSHOT_URI} path={path}", file=sys.stderr)
        _emit_overlap_findings(view)
    served = serve_view(view)
    if args.json:
        print(json.dumps(served, indent=2, sort_keys=True))
    else:
        print(render_text(view), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
