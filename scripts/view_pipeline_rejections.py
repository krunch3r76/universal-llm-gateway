#!/usr/bin/env python3
"""
Extract and display pipeline rejection/filtering events.

Shows what was removed at each stage with reasons, making it easy to
understand why certain claims didn't make it to the final output.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def extract_step_rejections(events: list[dict]) -> dict[str, Any]:
    """Extract rejection information from pipeline events."""
    rejections = {}
    current_execution = None

    for event in events:
        if event.get("signal") == "pipeline.started":
            current_execution = event.get("payload", {}).get("execution_id")
            continue

        if event.get("signal") == "pipeline.step.completed":
            payload = event.get("payload", {})
            if payload.get("execution_id") != current_execution:
                continue

            step_name = payload.get("step_name")
            # Pipeline stores full output; we need to parse it
            # This would require loading the actual step outputs

    return rejections


def parse_stargate_logs(log_path: Path) -> dict[str, list[dict]]:
    """
    Parse Stargate logs for rejection information.

    Returns dict mapping step names to list of rejection events.
    """
    rejections_by_step = {
        "validate_decomposition": [],
        "aggregate_verdicts": [],
        "filter": [],
        "relevance_filter": [],
        "filter_quality": [],
        "deduplicate": [],
    }

    with open(log_path) as f:
        for line in f:
            try:
                log = json.loads(line)
            except json.JSONDecodeError:
                continue

            message = log.get("message", "")
            logger_name = log.get("logger", "")

            # validate_decomposition rejections
            if "Rejected orphan supporting claim" in message:
                rejections_by_step["validate_decomposition"].append(
                    {
                        "timestamp": log.get("@timestamp"),
                        "reason": "orphan (no parent_context)",
                        "text": message.split("Rejected orphan supporting claim: ")[1]
                        if ":" in message
                        else "unknown",
                        "level": "INFO",
                    }
                )
            elif "Reclassified to supporting" in message:
                rejections_by_step["validate_decomposition"].append(
                    {
                        "timestamp": log.get("@timestamp"),
                        "reason": "reclassified (no subject mention)",
                        "text": message.split(
                            "Reclassified to supporting (no subject): "
                        )[1]
                        if ":" in message
                        else "unknown",
                        "level": "DEBUG",
                    }
                )

            # aggregate_verdicts rejections
            elif "rejected: sub-claim(s) failed" in message:
                rejections_by_step["aggregate_verdicts"].append(
                    {
                        "timestamp": log.get("@timestamp"),
                        "reason": "sub_claim_failed",
                        "statement_id": message.split("Statement ")[1].split(
                            " rejected"
                        )[0]
                        if "Statement " in message
                        else "unknown",
                        "level": "DEBUG",
                    }
                )

            # filter rejections
            elif (
                "_pipeline_handlers_consensus_v3_3.filter" in logger_name
                and "rejected:" in message
            ):
                rejections_by_step["filter"].append(
                    {
                        "timestamp": log.get("@timestamp"),
                        "reason": message.split("rejected: ")[1]
                        if "rejected: " in message
                        else "unknown",
                        "statement_id": message.split("Statement ")[1].split(
                            " rejected"
                        )[0]
                        if "Statement " in message
                        else "unknown",
                        "level": "DEBUG",
                    }
                )

            # relevance_filter removals
            elif "Relevance filter: removed" in message:
                rejections_by_step["relevance_filter"].append(
                    {
                        "timestamp": log.get("@timestamp"),
                        "reason": "low_similarity",
                        "similarity": message.split("sim=")[1].split(")")[0]
                        if "sim=" in message
                        else "unknown",
                        "text": message.split("): ")[1]
                        if "): " in message
                        else "unknown",
                        "level": "DEBUG",
                    }
                )

            # deduplicate tautology filtering
            elif "filtered" in message and "tautological" in message:
                count = (
                    message.split("filtered ")[1].split(" tautological")[0]
                    if "filtered " in message
                    else "0"
                )
                rejections_by_step["deduplicate"].append(
                    {
                        "timestamp": log.get("@timestamp"),
                        "reason": "tautological_mechanism",
                        "count": int(count) if count.isdigit() else 0,
                        "level": "DEBUG",
                    }
                )

            # filter_quality (filter_supporting) removals
            elif "filtered" in message and "low-directness" in message:
                count = (
                    message.split("filtered ")[1].split(" low-directness")[0]
                    if "filtered " in message
                    else "0"
                )
                rejections_by_step["filter_quality"].append(
                    {
                        "timestamp": log.get("@timestamp"),
                        "reason": "low_directness",
                        "count": int(count) if count.isdigit() else 0,
                        "level": "INFO",
                    }
                )

    return rejections_by_step


def print_rejections(rejections: dict[str, list[dict]], verbose: bool = False):
    """Pretty-print rejection information."""
    print("\n" + "=" * 80)
    print("PIPELINE REJECTIONS & FILTERING SUMMARY")
    print("=" * 80)

    total_rejections = sum(len(items) for items in rejections.values())

    if total_rejections == 0:
        print("\n✅ No rejections found in logs")
        return

    for step_name, items in rejections.items():
        if not items:
            continue

        print(f"\n{'─' * 80}")
        print(f"📍 {step_name.upper().replace('_', ' ')}")
        print(f"{'─' * 80}")
        print(f"   Rejections: {len(items)}")

        if verbose:
            for i, item in enumerate(items, 1):
                print(f"\n   [{i}] {item.get('reason', 'unknown')}")
                if "text" in item:
                    text = item["text"]
                    if len(text) > 100:
                        text = text[:100] + "..."
                    print(f"       Text: {text}")
                if "statement_id" in item:
                    print(f"       ID: {item['statement_id']}")
                if "similarity" in item:
                    print(f"       Similarity: {item['similarity']}")
                if "count" in item:
                    print(f"       Count: {item['count']}")
                if "timestamp" in item:
                    print(f"       Time: {item['timestamp']}")
        else:
            # Summary by reason
            reasons = {}
            for item in items:
                reason = item.get("reason", "unknown")
                reasons[reason] = reasons.get(reason, 0) + 1

            print("   Breakdown by reason:")
            for reason, count in sorted(reasons.items()):
                print(f"      • {reason}: {count}")

    print(f"\n{'=' * 80}")
    print(f"TOTAL REJECTIONS: {total_rejections}")
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Extract pipeline rejections from logs",
        epilog="Example: python scripts/view_pipeline_rejections.py -v -n 10000",
    )
    parser.add_argument(
        "-l",
        "--log-file",
        default="/tmp/logs/universal-stargate/universal_stargate.log",
        help="Path to Stargate log file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed rejection information",
    )
    parser.add_argument(
        "-n",
        "--last-n-lines",
        type=int,
        default=10000,
        help="Process last N lines of log (default: 10000)",
    )
    parser.add_argument("-o", "--output", help="Export rejections to JSON file")

    args = parser.parse_args()

    log_path = Path(args.log_file)
    if not log_path.exists():
        print(f"❌ Log file not found: {log_path}", file=sys.stderr)
        sys.exit(1)

    # Read last N lines only for performance
    with open(log_path) as f:
        # Seek to end and read backwards if file is large
        if log_path.stat().st_size > 10 * 1024 * 1024:  # 10MB
            f.seek(0, 2)  # Seek to end
            file_size = f.tell()
            # Estimate bytes per line and seek back
            estimated_bytes = args.last_n_lines * 200
            f.seek(max(0, file_size - estimated_bytes))
            f.readline()  # Skip partial line

        lines = f.readlines()[-args.last_n_lines :]

    # Write lines to temp file and parse
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as tmp:
        tmp.writelines(lines)
        tmp_path = Path(tmp.name)

    try:
        rejections = parse_stargate_logs(tmp_path)
        print_rejections(rejections, verbose=args.verbose)

        if args.output:
            output_path = Path(args.output)
            with open(output_path, "w") as f:
                json.dump(rejections, f, indent=2, ensure_ascii=False)
            print(f"\n💾 Exported to: {output_path}")
    finally:
        tmp_path.unlink()


if __name__ == "__main__":
    main()
