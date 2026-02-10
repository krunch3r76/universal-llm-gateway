"""Extract structured data from pipeline summary markdown files."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, kw_only=True)
class ModelResponse:
    """Single model response from answer_all step."""

    model_id: str
    latency_ms: float
    output: str


@dataclass(slots=True, kw_only=True)
class VerificationVerdict:
    """Verdict from a single verifier for a statement."""

    verifier_model_id: str
    verdict: bool


@dataclass(slots=True, kw_only=True)
class RejectedStatement:
    """Statement that failed verification consensus."""

    statement_id: str
    text: str
    origin_model_id: str
    verdicts: list[VerificationVerdict]
    true_count: int
    min_required: int


@dataclass(slots=True, kw_only=True)
class SynthesizedOutput:
    """Output from a synthesizer model."""

    model_id: str
    output: str


@dataclass(slots=True, kw_only=True)
class TokenUsage:
    """Token usage statistics."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(slots=True, kw_only=True)
class StepTiming:
    """Timing information for a pipeline step."""

    step_id: str
    step_num: int
    latency_ms: float
    model: str | None = None
    tokens: TokenUsage | None = None


@dataclass(slots=True, kw_only=True)
class StepPrompts:
    """Prompts used in a pipeline step."""

    step_id: str
    system_prompt: str
    user_prompt: str


@dataclass(slots=True, kw_only=True)
class PipelineReport:
    """Complete extracted data from a pipeline run."""

    run_id: str
    question: str
    rewritten_question: str
    original_responses: list[ModelResponse]
    rejected_statements: list[RejectedStatement]
    accepted_count: int
    total_count: int
    synthesized_outputs: list[SynthesizedOutput]
    step_timings: list[StepTiming]
    answer_prompts: StepPrompts | None = None
    total_tokens: TokenUsage | None = None


def extract_json_block(content: str) -> dict | list | None:
    """Extract first JSON block from markdown content."""
    # Match ```json ... ``` blocks
    pattern = r"```json\s*\n(.*?)\n```"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


def extract_json_from_section(content: str, section_header: str) -> dict | list | None:
    """Extract JSON block following a specific section header."""
    # Find section and extract JSON after it
    pattern = rf"{re.escape(section_header)}.*?```json\s*\n(.*?)\n```"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


def parse_answer_all(path: Path) -> list[ModelResponse]:
    """Parse 02_answer_all.md for original model responses."""
    content = path.read_text()
    responses: list[ModelResponse] = []

    # Pattern: ### Iteration N ... Model: `model-id` ... Latency: XXXms ... **Output:** ```...```
    iteration_pattern = r"### Iteration \d+\s*\n- Model: `([^`]+)`\s*\n- Latency: ([\d.]+)ms.*?\*\*Output:\*\*\s*\n```\n(.*?)\n```"

    for match in re.finditer(iteration_pattern, content, re.DOTALL):
        responses.append(
            ModelResponse(
                model_id=match.group(1),
                latency_ms=float(match.group(2)),
                output=match.group(3).strip(),
            )
        )

    return responses


def parse_filter(path: Path) -> tuple[list[RejectedStatement], int, int]:
    """Parse 10_filter.md for verification results.

    Returns: (rejected_statements, accepted_count, total_count)
    """
    content = path.read_text()

    # Extract JSON Data section
    json_data = extract_json_from_section(content, "### JSON Data")
    if not json_data or not isinstance(json_data, dict):
        return [], 0, 0

    report = json_data.get("report", [])
    rejected: list[RejectedStatement] = []

    for entry in report:
        if not entry.get("accepted", True):
            verdicts = [
                VerificationVerdict(verifier_model_id=k, verdict=v)
                for k, v in entry.get("verdicts", {}).items()
            ]
            rejected.append(
                RejectedStatement(
                    statement_id=entry.get("statement_id", ""),
                    text=entry.get("text", ""),
                    origin_model_id=entry.get("origin", ""),
                    verdicts=verdicts,
                    true_count=entry.get("true_count", 0),
                    min_required=entry.get("min_required", 0),
                )
            )

    return (
        rejected,
        json_data.get("accepted_count", 0),
        json_data.get("total_statements", 0),
    )


def parse_synthesize_all(path: Path) -> list[SynthesizedOutput]:
    """Parse 12_synthesize_all.md for final synthesized outputs."""
    content = path.read_text()
    outputs: list[SynthesizedOutput] = []

    # Pattern: ### Iteration N ... Model: `model-id` ... **Output:** ```...```
    iteration_pattern = r"### Iteration \d+\s*\n- Model: `([^`]+)`.*?\*\*Output:\*\*\s*\n```\n(.*?)\n```"

    for match in re.finditer(iteration_pattern, content, re.DOTALL):
        outputs.append(
            SynthesizedOutput(model_id=match.group(1), output=match.group(2).strip())
        )

    return outputs


def parse_rewrite_prompt(path: Path) -> tuple[str, str]:
    """Parse 01_rewrite_prompt.md for original and rewritten questions.

    Returns: (original_question, rewritten_question)
    """
    content = path.read_text()

    # Extract original from handler inputs (under ### text section)
    # Pattern: ### text ... **Value**: ... ``` content ```
    original_pattern = r"### text.*?\*\*Value\*\*:\s*\n```\n(.*?)\n```"
    original_match = re.search(original_pattern, content, re.DOTALL)
    original = original_match.group(1).strip() if original_match else ""

    # Fallback: try Raw output for rewritten, then JSON Data
    rewritten = ""

    # Try Raw section first (more reliable)
    raw_pattern = r"### Raw\s*\n```json\s*\n(.*?)\n```"
    raw_match = re.search(raw_pattern, content, re.DOTALL)
    if raw_match:
        try:
            raw_json = json.loads(raw_match.group(1))
            if isinstance(raw_json, dict):
                rewritten = raw_json.get("rewritten", "")
        except json.JSONDecodeError:
            pass

    # Fallback to JSON Data section
    if not rewritten:
        json_data = extract_json_from_section(content, "### JSON Data")
        if json_data and isinstance(json_data, dict):
            rewritten = json_data.get("rewritten", "")

    return original, rewritten


def find_file_by_suffix(run_dir: Path, suffix: str) -> Path | None:
    """Find a file ending with the given suffix (e.g., '_filter.md')."""
    for f in run_dir.glob(f"*{suffix}"):
        return f
    return None


def parse_step_timing(path: Path) -> StepTiming | None:
    """Extract timing info from a step summary file."""
    content = path.read_text()

    # Extract step number from filename (e.g., "02_answer_all.md" -> 2)
    step_num_match = re.match(r"(\d+)_(.+)\.md", path.name)
    if not step_num_match:
        return None
    step_num = int(step_num_match.group(1))
    step_id = step_num_match.group(2)

    # Extract latency - try Total Latency first, then Latency
    latency_pattern = r"\*\*(?:Total )?Latency\*\*:\s*([\d.]+)ms"
    latency_match = re.search(latency_pattern, content)
    latency_ms = float(latency_match.group(1)) if latency_match else 0.0

    # Extract model (if single model step)
    model_pattern = r"\*\*Model\*\*:\s*`([^`]+)`"
    model_match = re.search(model_pattern, content)
    model = model_match.group(1) if model_match else None

    # Extract token usage (if present)
    tokens = None
    token_section = re.search(
        r"## Token Usage\s*\n- \*\*Prompt\*\*:\s*(\d+)\s*\n- \*\*Completion\*\*:\s*(\d+)\s*\n- \*\*Total\*\*:\s*(\d+)",
        content,
    )
    if token_section:
        tokens = TokenUsage(
            prompt_tokens=int(token_section.group(1)),
            completion_tokens=int(token_section.group(2)),
            total_tokens=int(token_section.group(3)),
        )

    return StepTiming(
        step_id=step_id,
        step_num=step_num,
        latency_ms=latency_ms,
        model=model,
        tokens=tokens,
    )


def parse_all_step_timings(run_dir: Path) -> list[StepTiming]:
    """Parse timing from all step files in a run directory."""
    timings: list[StepTiming] = []
    for step_file in sorted(run_dir.glob("*.md")):
        if step_file.name == "full_summary.md":
            continue
        timing = parse_step_timing(step_file)
        if timing:
            timings.append(timing)
    return timings


def parse_answer_prompts(path: Path) -> StepPrompts | None:
    """Extract prompts from 02_answer_all.md."""
    content = path.read_text()

    # Extract system prompt from first iteration
    sys_pattern = r"\*System:\*\s*\n```\n(.*?)\n```"
    sys_match = re.search(sys_pattern, content, re.DOTALL)
    system_prompt = sys_match.group(1).strip() if sys_match else ""

    # Extract user prompt from first iteration
    user_pattern = r"\*User:\*\s*\n```\n(.*?)\n```"
    user_match = re.search(user_pattern, content, re.DOTALL)
    user_prompt = user_match.group(1).strip() if user_match else ""

    if not system_prompt and not user_prompt:
        return None

    return StepPrompts(
        step_id="answer_all", system_prompt=system_prompt, user_prompt=user_prompt
    )


def extract_pipeline_report(run_dir: Path) -> PipelineReport:
    """Extract complete report data from a pipeline run directory.

    Auto-detects file patterns for different pipeline versions:
    - v3.1: 10_filter.md, 12_synthesize_all.md
    - v3.3: 12_filter.md, 14_synthesize_all.md
    """
    run_id = run_dir.name

    # Fixed files (same across versions)
    answer_all_path = run_dir / "02_answer_all.md"
    original, rewritten = parse_rewrite_prompt(run_dir / "01_rewrite_prompt.md")
    original_responses = parse_answer_all(answer_all_path)

    # Auto-detect filter and synthesize files
    filter_file = find_file_by_suffix(run_dir, "_filter.md")
    synth_file = find_file_by_suffix(run_dir, "_synthesize_all.md")

    rejected, accepted, total = [], 0, 0
    if filter_file:
        rejected, accepted, total = parse_filter(filter_file)

    synthesized = []
    if synth_file:
        synthesized = parse_synthesize_all(synth_file)

    # Extract timing and prompts
    step_timings = parse_all_step_timings(run_dir)
    answer_prompts = parse_answer_prompts(answer_all_path)

    # Calculate total token usage across all steps
    total_prompt = 0
    total_completion = 0
    for timing in step_timings:
        if timing.tokens:
            total_prompt += timing.tokens.prompt_tokens
            total_completion += timing.tokens.completion_tokens

    total_tokens = None
    if total_prompt > 0 or total_completion > 0:
        total_tokens = TokenUsage(
            prompt_tokens=total_prompt,
            completion_tokens=total_completion,
            total_tokens=total_prompt + total_completion,
        )

    return PipelineReport(
        run_id=run_id,
        question=original,
        rewritten_question=rewritten,
        original_responses=original_responses,
        rejected_statements=rejected,
        accepted_count=accepted,
        total_count=total,
        synthesized_outputs=synthesized,
        step_timings=step_timings,
        answer_prompts=answer_prompts,
        total_tokens=total_tokens,
    )
