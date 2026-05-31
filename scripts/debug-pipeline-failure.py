#!/usr/bin/env python3
"""Quick diagnostic for pipeline failure logs - validates JSON responses."""

import json
import re
import sys
from pathlib import Path


def parse_log_file(log_path: Path) -> dict[str, dict]:
    """Extract all request/response pairs from log file."""
    content = log_path.read_text()

    pairs = {}

    # Find all request-response pairs
    pattern = r"--- REQUEST \(request_id=([a-f0-9-]+)\) ---\n(.*?)\n--- RESPONSE \(request_id=\1\) ---\n(.*?)(?=\n--- REQUEST|\n--- END|$)"

    for match in re.finditer(pattern, content, re.DOTALL):
        request_id = match.group(1)
        request_json = match.group(2).strip()
        response_json = match.group(3).strip()

        pairs[request_id] = {
            "request": request_json,
            "response": response_json,
        }

    return pairs


def validate_json(text: str) -> tuple[bool, str]:
    """Validate JSON and return (is_valid, error_message)."""
    try:
        json.loads(text)
        return True, ""
    except json.JSONDecodeError as e:
        return False, f"Line {e.lineno}, col {e.colno}: {e.msg}"
    except Exception as e:
        return False, str(e)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/debug-pipeline-failure.py <log_file>")
        print("\nExample:")
        print(
            "  python scripts/debug-pipeline-failure.py /tmp/logs/universal-stargate/pipeline_failures/20260210_230106_*.txt"
        )
        sys.exit(1)

    log_path = Path(sys.argv[1])
    if not log_path.exists():
        print(f"Error: File not found: {log_path}")
        sys.exit(1)

    print(f"Analyzing: {log_path.name}\n")

    pairs = parse_log_file(log_path)

    if not pairs:
        print("No request/response pairs found in log file.")
        sys.exit(1)

    print(f"Found {len(pairs)} request/response pairs\n")

    invalid_count = 0

    for request_id, data in pairs.items():
        # Validate response JSON
        is_valid, error = validate_json(data["response"])

        if not is_valid:
            invalid_count += 1
            print(f"❌ INVALID JSON - request_id: {request_id}")
            print(f"   Error: {error}")
            print("   Response preview:")
            lines = data["response"].split("\n")
            for i, line in enumerate(lines[:15], 1):
                print(f"   {i:3}: {line}")
            if len(lines) > 15:
                print(f"   ... ({len(lines) - 15} more lines)")
            print()
        else:
            # Parse and do semantic validation
            response_obj = json.loads(data["response"])
            request_obj = json.loads(data["request"])

            # Check for common issues
            issues = []

            # Check if it's a classifications response
            if "classifications" in response_obj:
                for idx, item in enumerate(response_obj["classifications"]):
                    if "domain" not in item:
                        issues.append(
                            f"Item {idx} missing 'domain' field (has: {list(item.keys())})"
                        )

            # Check if it's an evaluations response
            if "evaluations" in response_obj:
                # Extract expected count from system prompt
                system_content = request_obj["messages"][0]["content"]
                if "You MUST return exactly" in system_content:
                    match = re.search(r"exactly (\d+) evaluations", system_content)
                    if match:
                        expected = int(match.group(1))
                        actual = len(response_obj["evaluations"])
                        if actual != expected:
                            issues.append(
                                f"Expected {expected} evaluations, got {actual}"
                            )

            if issues:
                invalid_count += 1
                print(f"⚠️  SEMANTIC ISSUES - request_id: {request_id}")
                for issue in issues:
                    print(f"   - {issue}")
                print()
            else:
                print(f"✅ Valid - request_id: {request_id}")

    print(f"\n{'=' * 60}")
    print(f"Summary: {invalid_count}/{len(pairs)} responses have issues")

    if invalid_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
