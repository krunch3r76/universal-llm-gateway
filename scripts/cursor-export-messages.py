#!/usr/bin/env python3
"""Export a Cursor tab (agent-transcripts JSONL) to ulg.continuity.messages/1.

Resolves ``~/.cursor/projects/*/agent-transcripts/<id>/<id>.jsonl`` via
``cortex_store.transcript_assembly.locate_cursor_transcript_jsonl``, then
``continuity_tape.extract_jsonl.extract_turns_from_jsonl``.

Examples::

  AGENTID=f9763dd1-3871-4070-b79f-6b1d6d828f3b scripts/cursor-export-messages.py "$AGENTID"
  # → ~/mcp-data/files/tmp/cursor-transcripts/current_<uuid>.json
  #    (maestro: fs read cortex://tmp/cursor-transcripts/current_<uuid>.json)

  scripts/cursor-export-messages.py <uuid> --tools marker
  scripts/cursor-export-messages.py <uuid> --stdout --messages-only | jq '.[0]'
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path  # noqa: E402 — re-exec below before heavy imports

_REPO = Path(__file__).resolve().parents[1]
_LIBS = _REPO / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))

_VENV_ROOT = Path.home() / ".venvs/universal"
_VENV_PYTHON = _VENV_ROOT / "bin/python"
if _VENV_PYTHON.is_file() and Path(sys.prefix).resolve() != _VENV_ROOT.resolve():
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), *sys.argv])

from continuity_tape.extract_jsonl import extract_turns_from_jsonl  # noqa: E402
from continuity_tape.messages import Tools  # noqa: E402
from cortex_store.transcript_assembly import locate_cursor_transcript_jsonl  # noqa: E402

# Ephemeral exports under CORTEX_FILES_ROOT so MCP fs(cortex://…) can read them on
# orion; same bind as cortex-api (see dispatch_ops._shared FILES_ROOT).
_EXPORT_REL = Path("tmp/cursor-transcripts")


def cortex_files_root() -> Path:
    raw = os.environ.get("CORTEX_FILES_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / "mcp-data" / "files").resolve()


def default_out_path(transcript_id: str) -> Path:
    return cortex_files_root() / _EXPORT_REL / f"current_{transcript_id}.json"


def cortex_uri_for_out_path(out_path: Path) -> str:
    rel = out_path.resolve().relative_to(cortex_files_root())
    return f"cortex://{rel.as_posix()}"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "transcript_id",
        nargs="?",
        default=os.environ.get("AGENTID", "").strip() or None,
        help="Tab UUID (or set env AGENTID)",
    )
    p.add_argument(
        "--tools",
        choices=("none", "marker"),
        default="none",
        help="none: assistant prose only; marker: [tool call: name] lines (default: none)",
    )
    p.add_argument(
        "-o",
        "--out",
        type=Path,
        help="Write JSON here (default: mcp-data tmp/cursor-transcripts/current_<id>.json)",
    )
    p.add_argument(
        "--stdout",
        action="store_true",
        help="Write JSON to stdout instead of the default mcp-data path",
    )
    p.add_argument(
        "--messages-only",
        action="store_true",
        help="Output only the messages array, not meta/jsonl_path wrapper",
    )
    p.add_argument(
        "--compact",
        action="store_true",
        help="Minified JSON (default: indented)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.transcript_id:
        print(
            "cursor-export-messages: pass <transcript_id> or set AGENTID",
            file=sys.stderr,
        )
        return 2
    tid = args.transcript_id.strip()
    jsonl_path, err = locate_cursor_transcript_jsonl(tid)
    if err or jsonl_path is None:
        print(f"cursor-export-messages: {err or 'not_found'} transcript_id={tid!r}", file=sys.stderr)
        return 2 if err == "checkpoint.window_ambiguous" else 1

    tools: Tools = args.tools
    envelope = extract_turns_from_jsonl(
        jsonl_path,
        tools=tools,
        transcript_id=tid,
    )
    if args.messages_only:
        payload: object = envelope.messages
    else:
        payload = {
            "transcript_id": tid,
            "jsonl_path": str(jsonl_path),
            "meta": envelope.meta.model_dump(),
            "messages": envelope.messages,
        }

    text = json.dumps(payload, ensure_ascii=False, indent=None if args.compact else 2)
    text += "\n"
    if args.stdout:
        sys.stdout.write(text)
        return 0
    out_path = args.out if args.out is not None else default_out_path(tid)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    try:
        uri = cortex_uri_for_out_path(out_path.resolve())
    except ValueError:
        uri = None
    extra = f" {uri}" if uri else ""
    print(
        f"wrote {out_path} ({envelope.meta.message_count} messages){extra}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
