"""Format extracted pipeline data into human-readable report."""

from __future__ import annotations

from .extract import (
    PipelineReport,
    StepPrompts,
    StepTiming,
    TokenUsage,
    VerificationVerdict,
)

# ANSI color codes
BOLD = "\033[1m"
BLUE = "\033[0;34m"
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
RESET = "\033[0m"
DIM = "\033[2m"


def _box_header(title: str, char: str = "═") -> str:
    """Create a box header with title."""
    width = 65
    return f"{BOLD}{char * width}{RESET}\n{BOLD}  {title}{RESET}\n{BOLD}{char * width}{RESET}"


def _section_header(title: str) -> str:
    """Create a section header."""
    return f"\n{BLUE}┌{'─' * 63}┐{RESET}\n{BLUE}│  {title:<60}│{RESET}\n{BLUE}└{'─' * 63}┘{RESET}\n"


def _model_short_name(model_id: str) -> str:
    """Convert long model ID to short display name."""
    # Common model name patterns
    if "qwen" in model_id.lower():
        return "Qwen 2.5 7B"
    elif "llama" in model_id.lower():
        return "LLaMA 3.1 8B"
    elif "phi" in model_id.lower():
        return "Phi 3.5 Mini"
    elif "mistral" in model_id.lower():
        return "Mistral 7B"
    elif "gemma" in model_id.lower():
        return "Gemma Writer 10B"
    # Fallback: truncate and clean
    return model_id.split("-q4")[0].replace("-", " ").title()[:20]


def _truncate(text: str, max_chars: int = 500) -> str:
    """Truncate text with ellipsis if needed."""
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n{DIM}... [truncated, {len(text) - max_chars} more chars]{RESET}"
    )


def _format_verdicts(verdicts: list[VerificationVerdict]) -> str:
    """Format verifier verdicts as compact string."""
    parts = []
    for v in verdicts:
        name = _model_short_name(v.verifier_model_id)
        verdict_str = f"{GREEN}TRUE{RESET}" if v.verdict else f"{RED}FALSE{RESET}"
        parts.append(f"{name}={verdict_str}")
    return " | ".join(parts)


def format_section_original_responses(
    report: PipelineReport, truncate: int = 500
) -> str:
    """Format Section 1: Original Responses."""
    lines = [_section_header("SECTION 1: ORIGINAL MODEL RESPONSES")]

    for resp in report.original_responses:
        name = _model_short_name(resp.model_id)
        latency = resp.latency_ms / 1000  # Convert to seconds
        lines.append(f"\n{YELLOW}► {name}{RESET} ({latency:.1f}s)")
        lines.append(f"{DIM}  Model: {resp.model_id}{RESET}")
        lines.append("")
        # Indent response
        truncated = _truncate(resp.output, truncate)
        for line in truncated.split("\n"):
            lines.append(f"  {line}")
        lines.append("")

    return "\n".join(lines)


def format_section_verification_failures(report: PipelineReport) -> str:
    """Format Section 1: Verification Failures (Expanded)."""
    lines = [_section_header("SECTION 1: VERIFICATION FAILURES")]

    if not report.rejected_statements:
        lines.append(f"\n  {GREEN}✓ All statements passed verification{RESET}")
        lines.append(
            f"  {DIM}Accepted: {report.accepted_count}/{report.total_count}{RESET}"
        )
        return "\n".join(lines)

    # Summary at top
    rejected_count = len(report.rejected_statements)
    reject_pct = (
        (rejected_count / report.total_count * 100) if report.total_count > 0 else 0
    )
    accept_pct = 100 - reject_pct

    lines.append(
        f"\n{BOLD}Summary:{RESET} {rejected_count} rejected ({reject_pct:.1f}%), {report.accepted_count} accepted ({accept_pct:.1f}%)"
    )
    lines.append("")

    # Detailed rejected statements
    for i, stmt in enumerate(report.rejected_statements, 1):
        origin_name = _model_short_name(stmt.origin_model_id)

        lines.append(f"\n{BOLD}[{i}] REJECTED STATEMENT{RESET}")
        lines.append(f"  {DIM}Source: {origin_name} ({stmt.statement_id}){RESET}")
        lines.append(
            f"  {DIM}Required: {stmt.min_required} TRUE, Got: {stmt.true_count}{RESET}"
        )
        lines.append("")
        lines.append(f"  {RED}Statement:{RESET}")
        # Full statement text, indented
        for line in stmt.text.split("\n"):
            lines.append(f"    {line}")
        lines.append("")
        lines.append(f"  {YELLOW}Verification Results:{RESET}")
        for v in stmt.verdicts:
            verifier_name = _model_short_name(v.verifier_model_id)
            verdict_display = (
                f"{GREEN}TRUE{RESET}" if v.verdict else f"{RED}FALSE{RESET}"
            )
            lines.append(f"    • {verifier_name}: {verdict_display}")

    return "\n".join(lines)


def format_section_final_outputs(report: PipelineReport) -> str:
    """Format Section 2: Final Synthesized Outputs."""
    lines = [_section_header("SECTION 2: FINAL SYNTHESIZED OUTPUTS")]

    for i, output in enumerate(report.synthesized_outputs):
        name = _model_short_name(output.model_id)
        primary_marker = " (Primary)" if i == 0 else " (Alternate)"

        lines.append(f"\n{GREEN}► {name}{primary_marker}{RESET}")
        lines.append(f"{DIM}  Model: {output.model_id}{RESET}")
        lines.append("")
        # Full output (not truncated for final)
        for line in output.output.split("\n"):
            lines.append(f"  {line}")
        lines.append("")

    return "\n".join(lines)


def format_section_timing(timings: list[StepTiming]) -> str:
    """Format step timing breakdown."""
    lines = [_section_header("TIMING BREAKDOWN")]

    if not timings:
        lines.append(f"  {DIM}No timing data available{RESET}")
        return "\n".join(lines)

    total_ms = sum(t.latency_ms for t in timings)
    total_sec = total_ms / 1000

    lines.append(f"\n{BOLD}Total pipeline time:{RESET} {total_sec:.1f}s\n")

    # Group by phase for cleaner display
    for timing in timings:
        step_sec = timing.latency_ms / 1000
        pct = (timing.latency_ms / total_ms * 100) if total_ms > 0 else 0

        # Color-code by time impact
        if pct > 20:
            color = RED
        elif pct > 10:
            color = YELLOW
        else:
            color = DIM

        # Clean step name for display
        step_name = timing.step_id.replace("_", " ").title()
        bar_len = int(pct / 2)  # Scale to ~50 chars max
        bar = "█" * bar_len

        # Build base line
        line = f"  {timing.step_num:2d}. {step_name:<20} {color}{step_sec:6.1f}s{RESET} ({pct:4.1f}%) {DIM}{bar}{RESET}"

        # Add token info if available
        if timing.tokens:
            tokens_str = f" {DIM}[{timing.tokens.total_tokens:,} tokens]{RESET}"
            line += tokens_str

        lines.append(line)

    return "\n".join(lines)


def format_section_prompts(prompts: StepPrompts | None) -> str:
    """Format prompts used in answer_all step."""
    lines = [_section_header("PROMPTS (answer_all)")]

    if not prompts:
        lines.append(f"  {DIM}No prompt data available{RESET}")
        return "\n".join(lines)

    lines.append(f"\n{YELLOW}System Prompt:{RESET}")
    for line in prompts.system_prompt.split("\n"):
        lines.append(f"  {DIM}{line}{RESET}")

    lines.append(f"\n{YELLOW}User Prompt:{RESET}")
    for line in prompts.user_prompt.split("\n"):
        lines.append(f"  {line}")

    return "\n".join(lines)


def format_section_tokens(
    total_tokens: TokenUsage | None, timings: list[StepTiming]
) -> str:
    """Format token usage summary."""
    lines = [_section_header("TOKEN USAGE")]

    if not total_tokens:
        lines.append(f"  {DIM}No token data available{RESET}")
        return "\n".join(lines)

    # Total summary
    lines.append(f"\n{BOLD}Total Tokens:{RESET} {total_tokens.total_tokens:,}")
    lines.append(f"  {YELLOW}Prompt:{RESET} {total_tokens.prompt_tokens:,}")
    lines.append(f"  {YELLOW}Completion:{RESET} {total_tokens.completion_tokens:,}")

    # Breakdown by step (only steps with tokens)
    steps_with_tokens = [t for t in timings if t.tokens]
    if steps_with_tokens:
        lines.append(f"\n{BOLD}By Step:{RESET}")
        for timing in steps_with_tokens:
            step_name = timing.step_id.replace("_", " ").title()
            tokens = timing.tokens
            lines.append(
                f"  {timing.step_num:2d}. {step_name:<20} "
                f"{tokens.total_tokens:>6,} tokens "
                f"{DIM}(prompt: {tokens.prompt_tokens:,}, completion: {tokens.completion_tokens:,}){RESET}"
            )

    return "\n".join(lines)


def format_report(
    report: PipelineReport,
    truncate_responses: int = 500,
    *,
    show_timing: bool = False,
    show_prompts: bool = False,
) -> str:
    """Format complete pipeline report.

    Args:
        report: Extracted pipeline data
        truncate_responses: Max chars for original responses (0 = no truncation)
        show_timing: Include step-by-step timing and token usage breakdown
        show_prompts: Include prompts sent to answer_all step

    Returns:
        Formatted report string with ANSI colors
    """
    # Header
    header = _box_header("CONSENSUS PIPELINE REPORT")
    meta = f"\n{DIM}Run: {report.run_id}{RESET}"

    # Show original and rewritten questions
    question_parts = []
    if report.question:
        question_parts.append(f'\n{BOLD}Original:{RESET} "{report.question}"')
    if report.rewritten_question:
        # Only show rewritten if different from original
        if report.rewritten_question != report.question:
            question_parts.append(
                f'{YELLOW}Rewritten:{RESET} "{report.rewritten_question}"'
            )

    question_display = "\n".join(question_parts)

    # Sections
    section_failures = format_section_verification_failures(report)
    section_outputs = format_section_final_outputs(report)

    # Footer
    footer = _box_header("END OF REPORT", "─")

    parts = [header, meta]
    if question_display:
        parts.append(question_display)

    # Optional timing section (includes token usage)
    if show_timing:
        parts.append(format_section_timing(report.step_timings))
        parts.append(format_section_tokens(report.total_tokens, report.step_timings))

    # Optional prompts section
    if show_prompts:
        parts.append(format_section_prompts(report.answer_prompts))

    parts.extend([section_failures, section_outputs, footer])

    return "\n".join(parts)


def format_report_plain(
    report: PipelineReport,
    *,
    show_timing: bool = False,
    show_prompts: bool = False,
) -> str:
    """Format report without ANSI colors (for file output)."""
    # Simple approach: format then strip ANSI codes
    import re

    colored = format_report(
        report,
        show_timing=show_timing,
        show_prompts=show_prompts,
    )
    return re.sub(r"\033\[[0-9;]*m", "", colored)
