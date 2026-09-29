"""GIW suite failure-digest gate: check never writes; ``--measure-and-write`` appends one ledger row and does not commit."""

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ANCHOR_PATH = Path("services/git_integration_worker/suite_failure_anchor.json")
_EXIT_OK, _EXIT_MISMATCH, _EXIT_UNCOMPUTABLE = 0, 2, 3
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_FAILED_LINE = re.compile(r"^FAILED\s+(.+?)(?:\s+-\s+.*)?$")
_FAILED_COUNT = re.compile(r"\b(\d+)\s+failed\b")
_PASSED_COUNT = re.compile(r"\b(\d+)\s+passed\b")
_FAILED_WORD = re.compile(r"\bfailed\b")
_PASSED_WORD = re.compile(r"\bpassed\b")
_PYTEST_TAIL = "services/git_integration_worker/tests -q --tb=no -p no:cacheprovider"


def failure_digest(nodeids: list[str]) -> str:
    return hashlib.sha256(("\n".join(sorted(set(nodeids))) + "\n").encode()).hexdigest()


def parse_pytest_stdout(text: str) -> tuple[list[str], str] | None:
    unique = sorted(
        {
            match.group(1).strip()
            for raw in text.splitlines()
            if (match := _FAILED_LINE.match(raw.strip()))
        }
    )
    both: tuple[int, str] | None = None
    passed_only: tuple[int, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        failed_m, passed_m = _FAILED_COUNT.search(line), _PASSED_COUNT.search(line)
        if failed_m and _PASSED_WORD.search(line):
            both = (int(failed_m.group(1)), line)
        elif passed_m and not _FAILED_WORD.search(line):
            passed_only = (0, line)
    summary = both if both is not None else passed_only
    if summary is None or len(unique) != summary[0]:
        return None
    return unique, summary[1]


def _delta(previous: list[str], current: list[str]) -> tuple[list[str], list[str]]:
    old, new = set(previous), set(current)
    return sorted(new - old), sorted(old - new)


def _strings(value: Any) -> list[str] | None:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


def _row_ok(
    row: Any, prev_digest: str | None, prev_ids: list[str] | None, *, first: bool
) -> bool:
    if not isinstance(row, dict):
        return False
    ids, added, removed = (
        _strings(row.get("failed_nodeids")),
        _strings(row.get("added")),
        _strings(row.get("removed")),
    )
    measured = row.get("measured_commit")
    if ids is None or added is None or removed is None or ids != sorted(set(ids)):
        return False
    if row.get("to_digest") != failure_digest(ids):
        return False
    if not isinstance(measured, str) or _HEX40.fullmatch(measured) is None:
        return False
    if not isinstance(row.get("summary_line"), str):
        return False
    if first:
        return row.get("from_digest") is None and removed == [] and added == ids
    if prev_ids is None or row.get("from_digest") != prev_digest:
        return False
    return (added, removed) == _delta(prev_ids, ids)


def document_consistent(document: dict[str, Any], *, ancestor_ok: bool) -> bool:
    nodeids = _strings(document.get("failed_nodeids"))
    digest, measured, ledger = (
        document.get("failure_digest"),
        document.get("measured_commit"),
        document.get("ledger"),
    )
    if (
        document.get("schema_version") != 1
        or not ancestor_ok
        or nodeids is None
        or nodeids != sorted(set(nodeids))
        or not isinstance(digest, str)
        or failure_digest(nodeids) != digest
        or not isinstance(measured, str)
        or _HEX40.fullmatch(measured) is None
        or not isinstance(document.get("summary_line"), str)
        or not isinstance(ledger, list)
        or not ledger
    ):
        return False
    prev_digest: str | None = None
    prev_ids: list[str] | None = None
    for index, row in enumerate(ledger):
        if not _row_ok(row, prev_digest, prev_ids, first=index == 0):
            return False
        prev_digest, prev_ids = row["to_digest"], row["failed_nodeids"]
    last = ledger[-1]
    return (
        nodeids == last["failed_nodeids"]
        and last["to_digest"] == digest
        and last["measured_commit"] == measured
    )


def make_document(
    nodeids: list[str],
    measured_commit: str,
    summary_line: str,
    *,
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ids = sorted(set(nodeids))
    digest = failure_digest(ids)
    if prior is None:
        from_digest: str | None = None
        added, removed, ledger = list(ids), [], []
    else:
        previous = prior["ledger"][-1]
        from_digest = previous["to_digest"]
        added, removed = _delta(list(previous["failed_nodeids"]), ids)
        ledger = list(prior["ledger"])
    ledger.append(
        {
            "measured_commit": measured_commit,
            "from_digest": from_digest,
            "to_digest": digest,
            "added": added,
            "removed": removed,
            "failed_nodeids": list(ids),
            "summary_line": summary_line,
        }
    )
    return {
        "schema_version": 1,
        "measured_commit": measured_commit,
        "failure_digest": digest,
        "failed_nodeids": list(ids),
        "summary_line": summary_line,
        "ledger": ledger,
    }


def evaluate_check(
    document: dict[str, Any] | None, live_nodeids: list[str], *, ancestor_ok: bool
) -> int:
    if document is None or not document_consistent(document, ancestor_ok=ancestor_ok):
        return _EXIT_UNCOMPUTABLE
    live = sorted(set(live_nodeids))
    differs = (
        live != document["failed_nodeids"]
        or failure_digest(live) != document["failure_digest"]
    )
    return _EXIT_MISMATCH if differs else _EXIT_OK


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def git_porcelain_empty() -> bool:
    proc = _git(["git", "status", "--porcelain"])
    return proc.returncode == 0 and proc.stdout == ""


def git_head() -> str:
    proc = _git(["git", "rev-parse", "HEAD"])
    sha = proc.stdout.strip()
    return "" if proc.returncode != 0 or _HEX40.fullmatch(sha) is None else sha


def git_is_ancestor(commit: str) -> bool:
    if _HEX40.fullmatch(commit) is None:
        return False
    return _git(["git", "merge-base", "--is-ancestor", commit, "HEAD"]).returncode == 0


def run_frozen_pytest() -> tuple[str, str] | None:
    base = tempfile.mkdtemp(prefix="giw-suite-digest-")
    python = str(Path.home() / ".venvs/universal/bin/python")
    cmd = [python, "-m", "pytest", *_PYTEST_TAIL.split(), "--basetemp", base]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return proc.stdout, proc.stderr
    except OSError:
        return None
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _load_anchor() -> dict[str, Any] | None:
    try:
        loaded = json.loads(ANCHOR_PATH.read_text()) if ANCHOR_PATH.is_file() else None
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _tail(computed: str, anchor: str, added: int, removed: int) -> int:
    sys.stderr.write(
        f"computed {computed}\nanchor {anchor}\nadded {added}\nremoved {removed}\n"
    )
    return _EXIT_UNCOMPUTABLE


def _anchor_view(document: dict[str, Any] | None) -> tuple[str, list[str], bool]:
    if document is None:
        return "", [], False
    measured = document.get("measured_commit")
    digest = document.get("failure_digest")
    return (
        digest if isinstance(digest, str) else "",
        _strings(document.get("failed_nodeids")) or [],
        isinstance(measured, str) and git_is_ancestor(measured),
    )


def _check(nodeids: list[str], computed: str, stdout: str) -> int:
    document = _load_anchor()
    anchor_digest, anchor_ids, ancestor_ok = _anchor_view(document)
    code = evaluate_check(document, nodeids, ancestor_ok=ancestor_ok)
    added, removed = _delta(anchor_ids, nodeids)
    listed = "".join(f"{nodeid}\n" for nodeid in added)
    removed_text = "".join(f"{nodeid}\n" for nodeid in removed)
    sys.stdout.write(f"{stdout}--- added\n{listed}--- removed\n{removed_text}")
    if code != _EXIT_OK:
        _tail(computed, anchor_digest, len(added), len(removed))
    return code


def _write_anchor(head: str, nodeids: list[str], summary: str, stdout: str) -> int:
    prior = _load_anchor()
    if ANCHOR_PATH.is_file():
        _digest, _ids, ancestor_ok = _anchor_view(prior)
        if prior is None or not document_consistent(prior, ancestor_ok=ancestor_ok):
            return _EXIT_UNCOMPUTABLE
    document = make_document(
        nodeids, head, summary, prior=prior if ANCHOR_PATH.is_file() else None
    )
    if not document_consistent(document, ancestor_ok=git_is_ancestor(head)):
        return _EXIT_UNCOMPUTABLE
    try:
        ANCHOR_PATH.write_text(json.dumps(document, indent=2) + "\n")
    except OSError:
        return _EXIT_UNCOMPUTABLE
    if stdout and not stdout.endswith("\n"):
        stdout += "\n"
    sys.stdout.write(
        f"{stdout}failure_digest {document['failure_digest']}\nsummary_line {summary}\n"
    )
    return _EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    venv = (Path.home() / ".venvs/universal/bin/python").resolve()
    if (
        args not in ([], ["--measure-and-write"])
        or Path(sys.executable).resolve() != venv
        or not git_porcelain_empty()
    ):
        return _tail("", "", 0, 0)
    head = git_head()
    ran = run_frozen_pytest() if head else None
    if not head or ran is None:
        return _tail("", "", 0, 0)
    stdout, stderr = ran
    if stderr:
        sys.stderr.write(stderr if stderr.endswith("\n") else stderr + "\n")
    parsed = parse_pytest_stdout(stdout)
    if parsed is None:
        sys.stdout.write(stdout)
        return _tail("", "", 0, 0)
    nodeids, summary = parsed
    computed = failure_digest(nodeids)
    if not args:
        return _check(nodeids, computed, stdout)
    code = _write_anchor(head, nodeids, summary, stdout)
    return code if code == _EXIT_OK else _tail(computed, "", 0, 0)


if __name__ == "__main__":
    raise SystemExit(main())
