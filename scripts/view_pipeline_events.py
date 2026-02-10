#!/usr/bin/env python3
"""
Filter and display pipeline-related events from stargate event logs.
Shows execution flow, timing, and failures in human-readable format.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Pipeline event signals (must match signals in events.py)
PIPELINE_EVENTS = {
    # Pipeline lifecycle
    "pipeline.started",
    "pipeline.completed",
    "pipeline.failed",
    # Step lifecycle
    "pipeline.step.started",
    "pipeline.step.completed",
    "pipeline.step.failed",
    "pipeline.step.skipped",
    # Map step progress
    "pipeline.map.started",
    "pipeline.map.iteration.started",
    "pipeline.map.iteration.completed",
    "pipeline.map.iteration.failed",
    "pipeline.map.timeout.warning",  # dot-notation (not underscore)
    "pipeline.map.completed",
    # Checkpoint operations (dot-notation signals)
    "pipeline.checkpoint.saved",
    "pipeline.checkpoint.loaded",
    "pipeline.checkpoint.failed",
    # Domain verification (domain-specific verification within steps)
    "pipeline.step.domain.verification.started",
    "pipeline.step.domain.verification.completed",
}

# Color codes
RESET = "\033[0m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
DIM = "\033[2m"


def format_duration(seconds: float | None) -> str:
    """Format duration in human-readable form."""
    if seconds is None:
        return "N/A"
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins = int(seconds // 60)
    secs = seconds % 60
    return f"{mins}m{secs:.1f}s"


def format_event(event: dict[str, Any], show_full: bool = False) -> str:
    """Format a single pipeline event for display."""
    signal = event.get("signal", "unknown")
    timestamp = event.get("timestamp", "")
    data = event.get("payload", {})

    # Parse timestamp
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        time_str = dt.strftime("%H:%M:%S.%f")[:-3]
    except Exception:
        time_str = timestamp[:12] if timestamp else "??:??:??"

    # Pipeline lifecycle events
    if signal == "pipeline.started":
        pipeline_id = data.get("pipeline_id", "?")
        step_count = data.get("step_count", "?")
        timeout = data.get("timeout_seconds")
        domain = data.get("domain", "")
        info = f"{step_count} steps"
        if timeout:
            info += f", timeout={format_duration(timeout)}"
        if domain:
            info += f", domain={domain}"
        return f"{DIM}{time_str}{RESET} {BOLD}{BLUE}PIPELINE_START{RESET} {pipeline_id} ({info})"

    elif signal == "pipeline.completed":
        pipeline_id = data.get("pipeline_id", "?")
        duration = data.get("duration_seconds")
        step_count = data.get("step_count", "?")
        output = data.get("output_step", "")
        info = f"{step_count} steps"
        if output:
            info += f", output={output}"
        return f"{DIM}{time_str}{RESET} {BOLD}{GREEN}PIPELINE_END{RESET} {pipeline_id} ({info}) {DIM}{format_duration(duration)}{RESET}"

    elif signal == "pipeline.failed":
        pipeline_id = data.get("pipeline_id", "?")
        duration = data.get("duration_seconds")
        error = data.get("error", "unknown error")
        failed_step = data.get("failed_step")
        info = f"at {failed_step}" if failed_step else "during initialization"
        return f"{DIM}{time_str}{RESET} {BOLD}{RED}PIPELINE_FAIL{RESET} {pipeline_id} ({info}) {DIM}{format_duration(duration)}{RESET}\n{' ' * 13}{RED}{error}{RESET}"

    # Step lifecycle events
    elif signal == "pipeline.step.started":
        step = data.get("step_name", "?")
        step_type = data.get("step_type", "?")
        model = data.get("model_id", "")
        is_map = data.get("is_map_step", False)
        info = step_type
        if is_map:
            info += ", map"
        if model:
            info += f", model={model}"
        return f"{DIM}{time_str}{RESET}   {CYAN}▶{RESET} {BOLD}STEP_START{RESET} {step} ({info})"

    elif signal == "pipeline.step.completed":
        step = data.get("step_name", "?")
        duration = data.get("duration_seconds")
        output_len = data.get("output_length", 0)
        info = f"{output_len} chars"
        return f"{DIM}{time_str}{RESET}   {GREEN}◀{RESET} {BOLD}STEP_END{RESET} {step} ({info}) {DIM}{format_duration(duration)}{RESET}"

    elif signal == "pipeline.step.failed":
        step = data.get("step_name", "?")
        duration = data.get("duration_seconds")
        error = data.get("error", "unknown error")
        return f"{DIM}{time_str}{RESET}   {RED}✗{RESET} {BOLD}STEP_FAIL{RESET} {step} {DIM}{format_duration(duration)}{RESET}\n{' ' * 13}{RED}{error}{RESET}"

    elif signal == "pipeline.step.skipped":
        step = data.get("step_name", "?")
        reason = data.get("reason", "condition not met")
        return f"{DIM}{time_str}{RESET}   {YELLOW}⊘{RESET} {BOLD}STEP_SKIP{RESET} {step} ({reason})"

    # Map step events (indented under step events)
    elif signal == "pipeline.map.started":
        step = data.get("step_name", "?")
        total = data.get("total_iterations", "?")
        timeout = data.get("timeout_seconds")
        threshold = data.get("threshold")
        info = f"iterations={total}, timeout={format_duration(timeout)}"
        if threshold:
            info += f", threshold={threshold}"
        return (
            f"{DIM}{time_str}{RESET}     {BOLD}{BLUE}MAP_START{RESET} {step} ({info})"
        )

    elif signal == "pipeline.map.iteration.started":
        step = data.get("step_name", "?")
        idx = data.get("iteration_index", "?")
        model = data.get("model_id", "")
        gateway = data.get("gateway_id", "")
        info = f"#{idx}"
        if model:
            info += f" model={model}"
        if gateway:
            info += f" @{gateway}"
        return f"{DIM}{time_str}{RESET}       {CYAN}→{RESET} {step} {info}"

    elif signal == "pipeline.map.iteration.completed":
        step = data.get("step_name", "?")
        idx = data.get("iteration_index", "?")
        duration = data.get("duration_seconds")
        return f"{DIM}{time_str}{RESET}       {GREEN}✓{RESET} {step} #{idx} {DIM}({format_duration(duration)}){RESET}"

    elif signal == "pipeline.map.iteration.failed":
        step = data.get("step_name", "?")
        idx = data.get("iteration_index", "?")
        error = data.get("error", "unknown error")
        failure_type = data.get("failure_type", "error")
        duration = data.get("duration_seconds")
        icon = "⏱" if failure_type == "timeout" else "✗"
        return f"{DIM}{time_str}{RESET}       {RED}{icon}{RESET} {step} #{idx} {DIM}({format_duration(duration)}){RESET}\n{' ' * 17}{RED}{error}{RESET}"

    elif signal == "pipeline.map.timeout.warning":
        step = data.get("step_name", "?")
        percent = data.get("percent_elapsed", "?")
        pending = data.get("pending_iterations", [])
        elapsed = data.get("elapsed_seconds")
        timeout = data.get("timeout_seconds")
        return f"{DIM}{time_str}{RESET}     {YELLOW}⚠ TIMEOUT{RESET} {step} {percent}% elapsed ({format_duration(elapsed)}/{format_duration(timeout)}) - pending: {pending}"

    elif signal == "pipeline.map.completed":
        step = data.get("step_name", "?")
        succeeded = data.get("succeeded_count", 0)
        failed = data.get("failed_count", 0)
        total = data.get("total_count", 0)
        duration = data.get("duration_seconds")
        met_threshold = data.get("met_threshold", True)
        color = GREEN if failed == 0 else YELLOW
        result = f"{succeeded}/{total} ok"
        if failed > 0:
            result += f", {failed} failed"
        if not met_threshold:
            result += f" {RED}(threshold not met){RESET}"
        return f"{DIM}{time_str}{RESET}     {BOLD}{color}MAP_END{RESET} {step} ({result}) {DIM}{format_duration(duration)}{RESET}"

    elif signal == "pipeline.checkpoint.saved":
        step = data.get("step_name", "?")
        key = data.get("checkpoint_key", "")
        return (
            f"{DIM}{time_str}{RESET} {BLUE}💾{RESET} Checkpoint saved: {step} → {key}"
        )

    elif signal == "pipeline.checkpoint.loaded":
        step = data.get("step_name", "?")
        saved_at = data.get("saved_at", "")
        return f"{DIM}{time_str}{RESET} {BLUE}📂{RESET} Checkpoint loaded: {step} (saved {saved_at})"

    elif signal == "pipeline.checkpoint.failed":
        step = data.get("step_name", "?")
        op = data.get("operation", "?")
        error = data.get("error", "unknown")
        return f"{DIM}{time_str}{RESET} {RED}⚠{RESET} Checkpoint {op} failed: {step} - {error}"

    # Domain verification events (within verify_domain_specific step)
    elif signal == "pipeline.step.domain.verification.started":
        step = data.get("step_id", "?")
        domain = data.get("domain", "?")
        model = data.get("model_id", "?")
        count = data.get("statement_count", "?")
        return f"{DIM}{time_str}{RESET}     {CYAN}🔬{RESET} {BOLD}DOMAIN_VERIFY_START{RESET} {domain} ({count} claims) model={model}"

    elif signal == "pipeline.step.domain.verification.completed":
        step = data.get("step_id", "?")
        domain = data.get("domain", "?")
        model = data.get("model_id", "?")
        count = data.get("statement_count", 0)
        passed = data.get("passed_count", 0)
        failed = data.get("failed_count", 0)
        duration_ms = data.get("duration_ms", 0)
        duration_str = (
            f"{duration_ms:.0f}ms"
            if duration_ms < 1000
            else f"{duration_ms / 1000:.1f}s"
        )
        color = GREEN if failed == 0 else YELLOW
        return f"{DIM}{time_str}{RESET}     {color}✓{RESET} {BOLD}DOMAIN_VERIFY_END{RESET} {domain} ({passed}/{count} passed, {failed} failed) {DIM}{duration_str}{RESET}"

    # Unknown event type - show full JSON if requested
    if show_full:
        return f"{DIM}{time_str}{RESET} {signal}: {json.dumps(data, indent=2)}"
    return f"{DIM}{time_str}{RESET} {signal}"


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Display pipeline execution timeline from stargate event logs"
    )
    parser.add_argument(
        "log_file",
        nargs="?",
        default="/tmp/stargate-events/current.jsonl",
        help="Event log file (default: /tmp/stargate-events/current.jsonl)",
    )
    parser.add_argument("-e", "--execution-id", help="Filter by execution ID")
    parser.add_argument("-p", "--pipeline-id", help="Filter by pipeline ID")
    parser.add_argument("-s", "--step", help="Filter by step name")
    parser.add_argument(
        "-f",
        "--full",
        action="store_true",
        help="Show full event data for unknown events",
    )
    parser.add_argument("-n", "--last", type=int, help="Show only last N events")

    args = parser.parse_args()

    log_path = Path(args.log_file)
    if not log_path.exists():
        print(f"{RED}Error: Log file not found: {log_path}{RESET}", file=sys.stderr)
        sys.exit(1)

    events = []
    with log_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                signal = event.get("signal", "")

                # Filter pipeline events
                if signal not in PIPELINE_EVENTS:
                    continue

                # Apply filters
                data = event.get("payload", {})
                if args.execution_id and data.get("execution_id") != args.execution_id:
                    continue
                if args.pipeline_id and data.get("pipeline_id") != args.pipeline_id:
                    continue
                if args.step and data.get("step_name") != args.step:
                    continue

                events.append(event)
            except json.JSONDecodeError:
                continue

    # Apply last N filter
    if args.last:
        events = events[-args.last :]

    if not events:
        print(f"{YELLOW}No pipeline events found{RESET}")
        return

    # Display events
    print(f"\n{BOLD}Pipeline Events{RESET} ({len(events)} events)\n")
    for event in events:
        print(format_event(event, show_full=args.full))
    print()


if __name__ == "__main__":
    main()
