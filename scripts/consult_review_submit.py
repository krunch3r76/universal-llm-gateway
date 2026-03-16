#!/usr/bin/env python3
"""Submit code-review requests with deterministic file outputs.

This helper avoids fragile shell quoting/argument-size failures by building one
payload per file and writing both payload + response artifacts under a session
directory. It is intentionally sequential to reduce server load and simplify
forensics when one file fails.

Run this script with the project venv interpreter:
    ~/.venvs/universal/bin/python scripts/consult_review_submit.py ...
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    import httpx
except ModuleNotFoundError as exc:
    if exc.name == "httpx":
        print(
            "Missing dependency 'httpx'. Run this helper with the project venv:\n"
            "  ~/.venvs/universal/bin/python scripts/consult_review_submit.py ...",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    raise


@dataclass
class SubmissionResult:
    """Structured result for a single review submission."""

    index: int
    file: str
    payload_file: str
    response_file: str
    ok: bool
    status_code: int | None
    response_bytes: int
    error: str | None


def _read_files_list(files: list[str], files_file: str | None) -> list[str]:
    """Load file list from CLI args and/or newline-delimited file."""
    combined = list(files)
    if files_file:
        lines = Path(files_file).read_text(errors="replace").splitlines()
        combined.extend(
            line.strip() for line in lines if line.strip() and not line.startswith("#")
        )
    deduped: list[str] = []
    seen: set[str] = set()
    for entry in combined:
        if entry not in seen:
            seen.add(entry)
            deduped.append(entry)
    return deduped


def _build_payload(path: Path, model: str) -> dict[str, object]:
    """Build one code-review payload for a single source file."""
    content = f"### {path.as_posix()}\n{path.read_text(errors='replace')}"
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
    }


def main() -> int:
    """Run sequential submissions and persist deterministic artifacts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", required=True, help="Review session directory")
    parser.add_argument(
        "--files",
        nargs="*",
        default=[],
        help="Source files to submit (space separated)",
    )
    parser.add_argument(
        "--files-file",
        default=None,
        help="Optional newline-delimited file list (comments start with #)",
    )
    parser.add_argument("--model", default="code-review", help="Model/pipeline id")
    parser.add_argument(
        "--base-url", default="http://localhost:9999", help="API base URL"
    )
    parser.add_argument(
        "--endpoint",
        default="/v1/chat/completions",
        help="API endpoint path",
    )
    parser.add_argument(
        "--max-time",
        type=float,
        default=180.0,
        help="Per-request timeout in seconds",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=1,
        help="Index offset for output filenames",
    )
    parser.add_argument(
        "--summary-name",
        default="consult-review-submit-summary.json",
        help="Summary filename in session directory",
    )
    args = parser.parse_args()

    session_dir = Path(args.session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)

    files = _read_files_list(args.files, args.files_file)
    if not files:
        print("No files provided. Use --files and/or --files-file.", file=sys.stderr)
        return 2

    results: list[SubmissionResult] = []
    url = args.base_url.rstrip("/") + args.endpoint
    had_error = False

    with httpx.Client(timeout=args.max_time) as client:
        for offset, file_str in enumerate(files):
            index = args.start_index + offset
            file_path = Path(file_str)
            payload_path = session_dir / f"cr-batch-file-{index}-payload.json"
            response_path = session_dir / f"code-review-batch-file-{index}.json"

            if not file_path.exists():
                error_msg = f"source file not found: {file_path}"
                response_path.write_text(json.dumps({"error": error_msg}))
                result = SubmissionResult(
                    index=index,
                    file=file_str,
                    payload_file=str(payload_path),
                    response_file=str(response_path),
                    ok=False,
                    status_code=None,
                    response_bytes=len(error_msg),
                    error=error_msg,
                )
                results.append(result)
                had_error = True
                print(f"[{index}] ERROR {file_str} :: {error_msg}")
                continue

            payload = _build_payload(file_path, args.model)
            payload_path.write_text(json.dumps(payload))

            try:
                response = client.post(url, json=payload)
                response_text = response.text
                response_path.write_text(response_text)
                ok = response.status_code == 200
                if not ok:
                    had_error = True
                result = SubmissionResult(
                    index=index,
                    file=file_str,
                    payload_file=str(payload_path),
                    response_file=str(response_path),
                    ok=ok,
                    status_code=response.status_code,
                    response_bytes=len(response_text),
                    error=None if ok else f"http_{response.status_code}",
                )
                print(
                    f"[{index}] {'OK' if ok else 'HTTP'} {response.status_code} "
                    f"{file_str} ({len(response_text)} bytes)"
                )
            except Exception as exc:  # noqa: BLE001 - preserve full error in artifact
                error_text = str(exc)
                response_path.write_text(json.dumps({"error": error_text}))
                had_error = True
                result = SubmissionResult(
                    index=index,
                    file=file_str,
                    payload_file=str(payload_path),
                    response_file=str(response_path),
                    ok=False,
                    status_code=None,
                    response_bytes=len(error_text),
                    error=error_text,
                )
                print(f"[{index}] ERROR {file_str} :: {error_text}")

            results.append(result)

    summary_path = session_dir / args.summary_name
    summary = {
        "model": args.model,
        "url": url,
        "max_time_seconds": args.max_time,
        "total_files": len(files),
        "ok_count": sum(1 for r in results if r.ok),
        "error_count": sum(1 for r in results if not r.ok),
        "results": [asdict(r) for r in results],
    }
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"Summary: {summary_path}")
    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
