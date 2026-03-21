#!/usr/bin/env python3
"""Submit code-review requests with deterministic file outputs.

This helper avoids fragile shell quoting/argument-size failures by building one
payload per grouped batch and writing both payload + response artifacts under a session
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
    label: str
    files: list[str]
    payload_file: str
    response_file: str
    ok: bool
    status_code: int | None
    response_bytes: int
    error: str | None


@dataclass(frozen=True)
class ReviewFile:
    """Source file metadata used for batch planning."""

    path: Path
    size_bytes: int


@dataclass(frozen=True)
class ReviewBatch:
    """One code-review submission containing one or more files."""

    label: str
    files: list[ReviewFile]


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


def _root_parts(path: Path) -> tuple[str, ...]:
    """Bound parent grouping so unrelated subsystems do not merge."""
    parts = path.parts
    if not parts:
        return ()
    if parts[0] in {"services", "libs", "pipelines", "tools"} and len(parts) >= 2:
        return parts[:2]
    return parts[:1]


def _batch_label(files: list[ReviewFile]) -> str:
    """Describe a batch by the deepest shared parent, or the file path for solos."""
    if len(files) == 1:
        return files[0].path.as_posix()

    common_parts = list(files[0].path.parent.parts)
    for review_file in files[1:]:
        parent_parts = review_file.path.parent.parts
        while common_parts and tuple(common_parts) != parent_parts[: len(common_parts)]:
            common_parts.pop()
    return str(Path(*common_parts)) if common_parts else "."


def _split_by_parent(
    files: list[ReviewFile],
    prefix_parts: tuple[str, ...],
    max_batch_bytes: int,
) -> list[ReviewBatch]:
    """Split files until each batch fits within the byte budget."""
    total_bytes = sum(file.size_bytes for file in files)
    if len(files) == 1 or total_bytes <= max_batch_bytes:
        sorted_files = sorted(files, key=lambda item: item.path.as_posix())
        return [ReviewBatch(label=_batch_label(sorted_files), files=sorted_files)]

    child_buckets: dict[tuple[str, ...], list[ReviewFile]] = {}
    for file in files:
        file_parts = file.path.parts
        if len(file_parts) <= len(prefix_parts):
            child_key = file_parts
        else:
            child_key = file_parts[: len(prefix_parts) + 1]
        child_buckets.setdefault(child_key, []).append(file)

    if len(child_buckets) == 1:
        return [
            ReviewBatch(label=file.path.as_posix(), files=[file])
            for file in sorted(files, key=lambda item: item.path.as_posix())
        ]

    batches: list[ReviewBatch] = []
    for child_key in sorted(child_buckets):
        batches.extend(
            _split_by_parent(
                files=child_buckets[child_key],
                prefix_parts=child_key,
                max_batch_bytes=max_batch_bytes,
            )
        )
    return batches


def _plan_batches(files: list[str], max_batch_bytes: int) -> list[ReviewBatch]:
    """Group review files by bounded nearest parent within the byte budget."""
    review_files = [
        ReviewFile(path=Path(file_str), size_bytes=Path(file_str).stat().st_size)
        for file_str in files
    ]
    root_buckets: dict[tuple[str, ...], list[ReviewFile]] = {}
    for review_file in review_files:
        root_buckets.setdefault(_root_parts(review_file.path), []).append(review_file)

    batches: list[ReviewBatch] = []
    for root_key in sorted(root_buckets):
        batches.extend(
            _split_by_parent(
                files=root_buckets[root_key],
                prefix_parts=root_key,
                max_batch_bytes=max_batch_bytes,
            )
        )
    return batches


def _build_payload(batch: ReviewBatch, model: str) -> dict[str, object]:
    """Build one code-review payload for a grouped batch."""
    sections = [
        f"### File: {review_file.path.as_posix()}\n"
        f"{review_file.path.read_text(errors='replace')}"
        for review_file in batch.files
    ]
    content = (
        f"### Review batch: {batch.label}\n"
        f"### File count: {len(batch.files)}\n\n" + "\n\n".join(sections)
    )
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
    parser.add_argument(
        "--max-batch-bytes",
        type=int,
        default=42000,
        help="Maximum total source bytes per grouped batch",
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

    missing_files = [file_str for file_str in files if not Path(file_str).exists()]
    for file_str in missing_files:
        index = args.start_index + len(results)
        payload_path = session_dir / f"cr-batch-{index}-payload.json"
        response_path = session_dir / f"code-review-batch-{index}.json"
        error_msg = f"source file not found: {file_str}"
        response_path.write_text(json.dumps({"error": error_msg}))
        results.append(
            SubmissionResult(
                index=index,
                label=file_str,
                files=[file_str],
                payload_file=str(payload_path),
                response_file=str(response_path),
                ok=False,
                status_code=None,
                response_bytes=len(error_msg),
                error=error_msg,
            )
        )
        had_error = True
        print(f"[{index}] ERROR {file_str} :: {error_msg}")

    existing_files = [file_str for file_str in files if Path(file_str).exists()]
    batches = _plan_batches(existing_files, max_batch_bytes=args.max_batch_bytes)

    with httpx.Client(timeout=args.max_time) as client:
        for offset, batch in enumerate(batches):
            index = args.start_index + len(missing_files) + offset
            payload_path = session_dir / f"cr-batch-{index}-payload.json"
            response_path = session_dir / f"code-review-batch-{index}.json"

            payload = _build_payload(batch, args.model)
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
                    label=batch.label,
                    files=[review_file.path.as_posix() for review_file in batch.files],
                    payload_file=str(payload_path),
                    response_file=str(response_path),
                    ok=ok,
                    status_code=response.status_code,
                    response_bytes=len(response_text),
                    error=None if ok else f"http_{response.status_code}",
                )
                print(
                    f"[{index}] {'OK' if ok else 'HTTP'} {response.status_code} "
                    f"{batch.label} [{len(batch.files)} files] "
                    f"({len(response_text)} bytes)"
                )
            except Exception as exc:  # noqa: BLE001 - preserve full error in artifact
                error_text = str(exc)
                response_path.write_text(json.dumps({"error": error_text}))
                had_error = True
                result = SubmissionResult(
                    index=index,
                    label=batch.label,
                    files=[review_file.path.as_posix() for review_file in batch.files],
                    payload_file=str(payload_path),
                    response_file=str(response_path),
                    ok=False,
                    status_code=None,
                    response_bytes=len(error_text),
                    error=error_text,
                )
                print(f"[{index}] ERROR {batch.label} :: {error_text}")

            results.append(result)

    summary_path = session_dir / args.summary_name
    summary = {
        "model": args.model,
        "url": url,
        "max_time_seconds": args.max_time,
        "total_files": len(files),
        "total_batches": len(batches),
        "max_batch_bytes": args.max_batch_bytes,
        "ok_count": sum(1 for r in results if r.ok),
        "error_count": sum(1 for r in results if not r.ok),
        "results": [asdict(r) for r in results],
    }
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"Summary: {summary_path}")
    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
