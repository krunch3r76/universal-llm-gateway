"""Generate and verify ``libs/continuity_tape/messages_schema.json``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "libs" / "continuity_tape" / "messages_schema.json"


def _generate() -> str:
    from continuity_tape.messages import ContinuityMessagesEnvelope

    schema = ContinuityMessagesEnvelope.model_json_schema()
    schema["$id"] = "ulg.continuity.messages/1"
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    SCHEMA_PATH.write_text(text, encoding="utf-8")
    return text


def _check() -> int:
    if not SCHEMA_PATH.is_file():
        print(f"missing schema file: {SCHEMA_PATH}", file=sys.stderr)
        return 1
    on_disk = SCHEMA_PATH.read_text(encoding="utf-8")
    generated = _generate()
    if on_disk != generated:
        print("messages_schema.json drift — run scripts/gen-continuity-schema generate", file=sys.stderr)
        return 1
    print("messages_schema.json OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Continuity messages JSON Schema generator")
    parser.add_argument(
        "command",
        choices=("generate", "check"),
        nargs="?",
        default="check",
    )
    args = parser.parse_args(argv)
    if args.command == "generate":
        _generate()
        print(f"wrote {SCHEMA_PATH}")
        return 0
    return _check()


if __name__ == "__main__":
    raise SystemExit(main())
