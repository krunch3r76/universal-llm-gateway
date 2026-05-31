#!/usr/bin/env python3
"""
Show full details of a specific rejected statement by ID.

Usage:
    python scripts/show_rejected_claim.py "answer_all.llama_3_1_8b:4"
"""

import argparse
import json
import sys
from pathlib import Path


def find_statement_in_logs(log_path: Path, statement_id: str) -> dict | None:
    """Find full statement details from logs."""

    # Search for the statement in verify_all or decompose_all step outputs
    # These steps show the full statements before they're rejected

    found_in_context = []

    with open(log_path) as f:
        for line in f:
            try:
                log = json.loads(line)
            except json.JSONDecodeError:
                continue

            message = log.get("message", "")

            # Look for REQUEST BODY logs that contain the statement ID
            if "REQUEST BODY" in message and statement_id in message:
                # Extract the JSON from the log message
                # Format: "📥 REQUEST BODY (from client): {...}"
                if ": {" in message:
                    json_start = message.index(": {") + 2
                    try:
                        request_body = json.loads(message[json_start:])
                        found_in_context.append(
                            {
                                "timestamp": log.get("@timestamp"),
                                "type": "request_body",
                                "data": request_body,
                            }
                        )
                    except json.JSONDecodeError:
                        pass

            # Look for statement references in rejection messages
            elif statement_id in message and "rejected" in message.lower():
                found_in_context.append(
                    {
                        "timestamp": log.get("@timestamp"),
                        "type": "rejection",
                        "message": message,
                        "logger": log.get("logger"),
                    }
                )

    return found_in_context if found_in_context else None


def extract_statement_from_context(
    contexts: list[dict], statement_id: str
) -> dict | None:
    """Extract actual statement text from context data."""

    for ctx in contexts:
        if ctx["type"] == "request_body":
            data = ctx["data"]

            # Look in messages for the statement
            if "messages" in data:
                for msg in data["messages"]:
                    content = msg.get("content", "")
                    if statement_id in content:
                        # Extract the numbered statement
                        lines = content.split("\n")
                        for i, line in enumerate(lines):
                            if statement_id in line:
                                # Get this line and context
                                statement_lines = [line]
                                # Get a few lines after for context
                                for j in range(i + 1, min(i + 5, len(lines))):
                                    if lines[j].strip() and not lines[j].startswith(
                                        ("[", "{")
                                    ):
                                        statement_lines.append(lines[j])
                                    else:
                                        break

                                return {
                                    "id": statement_id,
                                    "text": "\n".join(statement_lines),
                                    "context": msg,
                                    "timestamp": ctx["timestamp"],
                                }

    return None


def main():
    parser = argparse.ArgumentParser(
        description="Show full details of a rejected statement",
        epilog='Example: python scripts/show_rejected_claim.py "answer_all.llama_3_1_8b:4"',
    )
    parser.add_argument(
        "statement_id",
        help="Statement ID (e.g., answer_all.phi:2, answer_all.llama_3_1_8b:4)",
    )
    parser.add_argument(
        "-l",
        "--log-file",
        default="/tmp/logs/universal-stargate/universal_stargate.log",
        help="Path to Stargate log file",
    )

    args = parser.parse_args()

    log_path = Path(args.log_file)
    if not log_path.exists():
        print(f"❌ Log file not found: {log_path}", file=sys.stderr)
        sys.exit(1)

    print(f"🔍 Searching for statement: {args.statement_id}")
    print("=" * 80)

    contexts = find_statement_in_logs(log_path, args.statement_id)

    if not contexts:
        print(f"\n❌ Statement '{args.statement_id}' not found in logs")
        print("\nTip: Run with --last-n-lines=50000 if the execution was older")
        sys.exit(1)

    print(f"\n✅ Found {len(contexts)} references")

    # Try to extract full statement
    statement = extract_statement_from_context(contexts, args.statement_id)

    if statement:
        print("\n📄 FULL STATEMENT")
        print("─" * 80)
        print(statement["text"])
        print("─" * 80)
        print(f"\nTimestamp: {statement['timestamp']}")

    # Show rejection context
    print("\n🚫 REJECTION CONTEXT")
    print("─" * 80)
    for ctx in contexts:
        if ctx["type"] == "rejection":
            print(f"\nTime: {ctx['timestamp']}")
            print(f"Logger: {ctx['logger']}")
            print(f"Message: {ctx['message']}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
