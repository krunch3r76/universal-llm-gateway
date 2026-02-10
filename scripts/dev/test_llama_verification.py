#!/usr/bin/env python3
"""
Test llama_3_1_8b verification format compliance and accuracy.

Purpose: Determine if llama_3_1_8b has similar format issues to mistral_7b
before considering it as a replacement verifier model.

Focus areas:
- Format compliance: Does it output proper boolean verdicts?
- Accuracy: How well does it handle compounds vs atomics?
- Batch behavior: Does chunk_size=1 (isolation mode) affect results?

Comparison baseline: qwen (known good verifier)

Usage:
    python scripts/dev/test_llama_verification.py [--url URL] [--output FILE]

Example:
    python scripts/dev/test_llama_verification.py
    python scripts/dev/test_llama_verification.py --output /tmp/llama_verification_results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import httpx

# =============================================================================
# CONFIGURATION
# =============================================================================

# Test models: llama_3_1_8b (candidate) vs qwen (baseline)
VERIFIER_MODELS: dict[str, str] = {
    "llama_3_1_8b": "meta-llama-3-1-8b-instruct-q8-0-16384",
    "qwen": "qwen2-5-7b-instruct-q8-0-16384",  # Baseline for comparison
}

# Verification prompt (from prompts.yaml verification_domain_primed)
SYSTEM_PROMPT = """For each statement, determine if it is TRUE or FALSE based on your knowledge.

If you don't know, mark FALSE."""

USER_TEMPLATE = """Statements to evaluate:

{statements}

Respond in JSON format with evaluations array containing objects with statement_number and verdict fields."""


# =============================================================================
# TEST CASES
# =============================================================================

StatementCategory = Literal[
    "valid_compound",
    "partial_false",
    "clearly_false",
    "atomic_true",
    "atomic_false",
]


@dataclass
class TestStatement:
    """A statement to test verification on."""

    text: str
    expected: bool
    category: StatementCategory
    notes: str = ""


TEST_STATEMENTS: list[TestStatement] = [
    # === VALID COMPOUNDS (all parts true - should pass) ===
    TestStatement(
        text="Water boils at 100°C at sea level and freezes at 0°C.",
        expected=True,
        category="valid_compound",
        notes="Basic scientific facts",
    ),
    TestStatement(
        text=(
            "Douglas Adams wrote The Hitchhiker's Guide to the Galaxy, and 42 is "
            "the 'Answer to Life, the Universe, and Everything' in that work."
        ),
        expected=True,
        category="valid_compound",
        notes="Cultural reference - the original failing case",
    ),
    TestStatement(
        text="Python was created by Guido van Rossum and was first released in 1991.",
        expected=True,
        category="valid_compound",
        notes="Tech history compound",
    ),
    TestStatement(
        text=(
            "The Eiffel Tower is located in Paris, France, and was completed in 1889 "
            "as the entrance arch for the World's Fair."
        ),
        expected=True,
        category="valid_compound",
        notes="Historical landmark compound",
    ),
    TestStatement(
        text="Albert Einstein developed the theory of relativity and won the Nobel Prize in Physics in 1921.",
        expected=True,
        category="valid_compound",
        notes="Historical figure compound",
    ),
    # === PARTIALLY FALSE COMPOUNDS (one part false - should fail) ===
    TestStatement(
        text="Water boils at 100°C at sea level and freezes at 10°C.",
        expected=False,
        category="partial_false",
        notes="Second clause false (0°C not 10°C)",
    ),
    TestStatement(
        text=(
            "Douglas Adams wrote The Hitchhiker's Guide to the Galaxy, and 73 is "
            "the 'Answer to Life, the Universe, and Everything' in that work."
        ),
        expected=False,
        category="partial_false",
        notes="Wrong number (42 not 73)",
    ),
    TestStatement(
        text="Python was created by Guido van Rossum and was first released in 1985.",
        expected=False,
        category="partial_false",
        notes="Wrong year (1991 not 1985)",
    ),
    TestStatement(
        text="The Eiffel Tower is located in Paris, France, and was completed in 1920.",
        expected=False,
        category="partial_false",
        notes="Wrong year (1889 not 1920)",
    ),
    # === CLEARLY FALSE COMPOUNDS (both parts false - should fail) ===
    TestStatement(
        text="The sun orbits Earth once per day, and the moon is made of cheese.",
        expected=False,
        category="clearly_false",
        notes="Both claims absurd",
    ),
    TestStatement(
        text="Napoleon Bonaparte was born in Antarctica and ruled the Roman Empire.",
        expected=False,
        category="clearly_false",
        notes="Both claims historically false",
    ),
    # === ATOMIC TRUE (baseline - should pass) ===
    TestStatement(
        text="42 is the 'Answer to Life, the Universe, and Everything' in Douglas Adams's work.",
        expected=True,
        category="atomic_true",
        notes="Single cultural reference claim",
    ),
    TestStatement(
        text="Water boils at 100°C at sea level.",
        expected=True,
        category="atomic_true",
        notes="Single scientific fact",
    ),
    TestStatement(
        text="Python was first released in 1991.",
        expected=True,
        category="atomic_true",
        notes="Single tech history fact",
    ),
    # === ATOMIC FALSE (baseline - should fail) ===
    TestStatement(
        text="Water boils at 50°C at sea level.",
        expected=False,
        category="atomic_false",
        notes="Single false scientific claim",
    ),
    TestStatement(
        text="Python was first released in 1975.",
        expected=False,
        category="atomic_false",
        notes="Single false tech history claim",
    ),
]


# =============================================================================
# API CLIENT
# =============================================================================


@dataclass
class VerificationResult:
    """Result of verifying a statement with a model."""

    model_alias: str
    model_id: str
    statement_idx: int
    statement_text: str
    expected: bool
    verdict: bool | None
    raw_response: str
    error: str | None = None
    latency_ms: float = 0.0
    verdict_type: str = "unknown"  # Track actual type received


async def verify_statement(
    client: httpx.AsyncClient,
    base_url: str,
    model_alias: str,
    model_id: str,
    statement_idx: int,
    statement: TestStatement,
) -> VerificationResult:
    """Send a single statement to a model for verification."""
    # Format the statement with number
    formatted = f"1. {statement.text}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(statements=formatted)},
    ]

    request_body = {
        "model": model_id,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 256,
        "response_format": {"type": "json_object"},
    }

    start = asyncio.get_event_loop().time()
    try:
        response = await client.post(
            f"{base_url}/v1/chat/completions",
            json=request_body,
            timeout=60.0,
        )
        response.raise_for_status()
        data = response.json()
        latency_ms = (asyncio.get_event_loop().time() - start) * 1000

        content = data["choices"][0]["message"]["content"]

        # Parse verdict from response and track its type
        verdict = None
        verdict_type = "unknown"
        try:
            parsed = json.loads(content)
            evaluations = parsed.get("evaluations", [])
            if evaluations:
                raw_verdict = evaluations[0].get("verdict")
                verdict_type = type(raw_verdict).__name__

                if isinstance(raw_verdict, bool):
                    verdict = raw_verdict
                elif isinstance(raw_verdict, str):
                    # Track what string format was used
                    verdict_type = f"string:{raw_verdict}"
                    verdict = raw_verdict.lower() in ("true", "yes", "1")
                elif isinstance(raw_verdict, int):
                    verdict_type = f"int:{raw_verdict}"
                    verdict = bool(raw_verdict)
        except json.JSONDecodeError:
            # Try to find true/false in raw text
            lower = content.lower()
            if "true" in lower:
                verdict = True
                verdict_type = "unparsed_true"
            elif "false" in lower:
                verdict = False
                verdict_type = "unparsed_false"

        return VerificationResult(
            model_alias=model_alias,
            model_id=model_id,
            statement_idx=statement_idx,
            statement_text=statement.text,
            expected=statement.expected,
            verdict=verdict,
            raw_response=content,
            latency_ms=latency_ms,
            verdict_type=verdict_type,
        )

    except Exception as e:
        latency_ms = (asyncio.get_event_loop().time() - start) * 1000
        return VerificationResult(
            model_alias=model_alias,
            model_id=model_id,
            statement_idx=statement_idx,
            statement_text=statement.text,
            expected=statement.expected,
            verdict=None,
            raw_response="",
            error=str(e),
            latency_ms=latency_ms,
        )


async def run_all_verifications(
    base_url: str,
) -> list[VerificationResult]:
    """Run all test statements against all verifier models."""
    results: list[VerificationResult] = []

    async with httpx.AsyncClient() as client:
        tasks = []
        for model_alias, model_id in VERIFIER_MODELS.items():
            for idx, statement in enumerate(TEST_STATEMENTS):
                tasks.append(
                    verify_statement(
                        client, base_url, model_alias, model_id, idx, statement
                    )
                )

        # Run with some concurrency limit to avoid overwhelming the server
        semaphore = asyncio.Semaphore(4)

        async def limited_task(coro):
            async with semaphore:
                return await coro

        results = await asyncio.gather(*[limited_task(t) for t in tasks])

    return list(results)


# =============================================================================
# ANALYSIS
# =============================================================================


@dataclass
class CategoryStats:
    """Statistics for a category of statements."""

    category: str
    total: int = 0
    correct: int = 0
    incorrect: int = 0
    errors: int = 0

    @property
    def accuracy(self) -> float:
        if self.total == 0:
            return 0.0
        return self.correct / self.total


@dataclass
class ModelStats:
    """Statistics for a single model."""

    model_alias: str
    model_id: str
    by_category: dict[str, CategoryStats] = field(default_factory=dict)
    total_latency_ms: float = 0.0
    call_count: int = 0
    verdict_types: dict[str, int] = field(default_factory=dict)

    @property
    def avg_latency_ms(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.total_latency_ms / self.call_count


def analyze_results(results: list[VerificationResult]) -> dict:
    """Analyze verification results and compute statistics."""
    # Build model stats
    model_stats: dict[str, ModelStats] = {}

    for r in results:
        if r.model_alias not in model_stats:
            model_stats[r.model_alias] = ModelStats(
                model_alias=r.model_alias, model_id=r.model_id
            )

        ms = model_stats[r.model_alias]
        ms.total_latency_ms += r.latency_ms
        ms.call_count += 1

        # Track verdict types for format compliance analysis
        ms.verdict_types[r.verdict_type] = ms.verdict_types.get(r.verdict_type, 0) + 1

        # Get category for this statement
        statement = TEST_STATEMENTS[r.statement_idx]
        cat = statement.category

        if cat not in ms.by_category:
            ms.by_category[cat] = CategoryStats(category=cat)

        cs = ms.by_category[cat]
        cs.total += 1

        if r.error:
            cs.errors += 1
        elif r.verdict is None:
            cs.errors += 1
        elif r.verdict == r.expected:
            cs.correct += 1
        else:
            cs.incorrect += 1

    # Build summary
    summary = {
        "timestamp": datetime.now().isoformat(),
        "total_statements": len(TEST_STATEMENTS),
        "total_models": len(VERIFIER_MODELS),
        "total_calls": len(results),
        "models": {},
        "by_category": {},
        "key_metrics": {},
        "format_compliance": {},
    }

    # Per-model breakdown
    for alias, ms in model_stats.items():
        model_data = {
            "model_id": ms.model_id,
            "avg_latency_ms": round(ms.avg_latency_ms, 1),
            "categories": {},
            "verdict_type_distribution": ms.verdict_types,
        }
        for cat, cs in ms.by_category.items():
            model_data["categories"][cat] = {
                "total": cs.total,
                "correct": cs.correct,
                "incorrect": cs.incorrect,
                "errors": cs.errors,
                "accuracy": round(cs.accuracy * 100, 1),
            }
        summary["models"][alias] = model_data

    # Aggregate by category across all models
    cat_totals: dict[str, CategoryStats] = {}
    for ms in model_stats.values():
        for cat, cs in ms.by_category.items():
            if cat not in cat_totals:
                cat_totals[cat] = CategoryStats(category=cat)
            cat_totals[cat].total += cs.total
            cat_totals[cat].correct += cs.correct
            cat_totals[cat].incorrect += cs.incorrect
            cat_totals[cat].errors += cs.errors

    for cat, cs in cat_totals.items():
        summary["by_category"][cat] = {
            "total": cs.total,
            "correct": cs.correct,
            "incorrect": cs.incorrect,
            "errors": cs.errors,
            "accuracy": round(cs.accuracy * 100, 1),
        }

    # Key metrics for decision-making
    valid_compound = cat_totals.get("valid_compound", CategoryStats("valid_compound"))
    partial_false = cat_totals.get("partial_false", CategoryStats("partial_false"))
    clearly_false = cat_totals.get("clearly_false", CategoryStats("clearly_false"))

    summary["key_metrics"] = {
        "valid_compound_false_rejection_rate": round(
            (valid_compound.incorrect / valid_compound.total * 100)
            if valid_compound.total > 0
            else 0,
            1,
        ),
        "partial_false_detection_rate": round(
            (partial_false.correct / partial_false.total * 100)
            if partial_false.total > 0
            else 0,
            1,
        ),
        "clearly_false_detection_rate": round(
            (clearly_false.correct / clearly_false.total * 100)
            if clearly_false.total > 0
            else 0,
            1,
        ),
    }

    # Format compliance analysis
    for alias, ms in model_stats.items():
        bool_count = ms.verdict_types.get("bool", 0)
        total = ms.call_count
        format_compliant_pct = round(bool_count / total * 100, 1) if total > 0 else 0

        summary["format_compliance"][alias] = {
            "format_compliant_percent": format_compliant_pct,
            "verdict_type_distribution": ms.verdict_types,
        }

    return summary


def build_detailed_results(results: list[VerificationResult]) -> list[dict]:
    """Build detailed per-statement results for analysis."""
    detailed = []
    for r in results:
        statement = TEST_STATEMENTS[r.statement_idx]
        detailed.append(
            {
                "statement_idx": r.statement_idx,
                "statement_text": r.statement_text,
                "category": statement.category,
                "notes": statement.notes,
                "expected": r.expected,
                "model_alias": r.model_alias,
                "model_id": r.model_id,
                "verdict": r.verdict,
                "verdict_type": r.verdict_type,
                "correct": r.verdict == r.expected if r.verdict is not None else None,
                "latency_ms": round(r.latency_ms, 1),
                "error": r.error,
                "raw_response": r.raw_response[:500] if r.raw_response else None,
            }
        )
    return detailed


# =============================================================================
# MAIN
# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test llama_3_1_8b verification format compliance"
    )
    parser.add_argument(
        "--url",
        default="http://localhost:9999",
        help="Stargate base URL (default: http://localhost:9999)",
    )
    parser.add_argument(
        "--output",
        default="/tmp/llama_verification_results.json",
        help="Output JSON file path",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Llama 3.1 8B Verification Format Compliance Test")
    print("=" * 60)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Stargate URL: {args.url}")
    print(f"Models: {', '.join(VERIFIER_MODELS.keys())}")
    print(f"Test statements: {len(TEST_STATEMENTS)}")
    print(f"Total calls: {len(TEST_STATEMENTS) * len(VERIFIER_MODELS)}")
    print()
    print("Focus: Format compliance and accuracy comparison")
    print("Baseline: qwen (known good verifier)")
    print("Candidate: llama_3_1_8b (potential replacement for mistral_7b)")
    print()

    print("Running verifications...")
    results = asyncio.run(run_all_verifications(args.url))
    print(f"Completed {len(results)} verification calls")
    print()

    # Analyze
    summary = analyze_results(results)
    detailed = build_detailed_results(results)

    # Print summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print()

    print("Key Metrics:")
    km = summary["key_metrics"]
    print(
        f"  Valid compound false rejection rate: {km['valid_compound_false_rejection_rate']}%"
    )
    print(f"  Partial false detection rate: {km['partial_false_detection_rate']}%")
    print(f"  Clearly false detection rate: {km['clearly_false_detection_rate']}%")
    print()

    print("Format Compliance:")
    for alias, fc in summary["format_compliance"].items():
        print(f"  {alias}:")
        print(f"    Boolean format: {fc['format_compliant_percent']}%")
        print(f"    Verdict type distribution: {fc['verdict_type_distribution']}")
    print()

    print("By Category:")
    for cat, stats in summary["by_category"].items():
        print(
            f"  {cat}: {stats['accuracy']}% accuracy ({stats['correct']}/{stats['total']})"
        )
    print()

    print("By Model:")
    for alias, mdata in summary["models"].items():
        total_correct = sum(c["correct"] for c in mdata["categories"].values())
        total = sum(c["total"] for c in mdata["categories"].values())
        overall_acc = round(total_correct / total * 100, 1) if total > 0 else 0
        print(
            f"  {alias}: {overall_acc}% overall accuracy, {mdata['avg_latency_ms']}ms avg latency"
        )
        # Detailed per-category breakdown
        for cat, cstats in mdata["categories"].items():
            print(f"    {cat}: {cstats['accuracy']}% ({cstats['correct']}/{cstats['total']})")
    print()

    # Decision guidance
    print("=" * 60)
    print("DECISION GUIDANCE")
    print("=" * 60)
    
    llama_format = summary["format_compliance"]["llama_3_1_8b"]["format_compliant_percent"]
    llama_accuracy = next(
        round(sum(c["correct"] for c in mdata["categories"].values()) / 
              sum(c["total"] for c in mdata["categories"].values()) * 100, 1)
        for alias, mdata in summary["models"].items() if alias == "llama_3_1_8b"
    )
    
    qwen_format = summary["format_compliance"]["qwen"]["format_compliant_percent"]
    qwen_accuracy = next(
        round(sum(c["correct"] for c in mdata["categories"].values()) / 
              sum(c["total"] for c in mdata["categories"].values()) * 100, 1)
        for alias, mdata in summary["models"].items() if alias == "qwen"
    )
    
    print(f"Llama 3.1 8B: {llama_format}% format compliant, {llama_accuracy}% accurate")
    print(f"Qwen (baseline): {qwen_format}% format compliant, {qwen_accuracy}% accurate")
    print()
    
    if llama_format >= 95.0 and llama_accuracy >= 85.0:
        print("✓ RECOMMENDED: llama_3_1_8b shows good format compliance and accuracy")
        print("  Consider adding to verify_models as replacement for mistral_7b")
    elif llama_format >= 80.0:
        print("⚠ CONDITIONAL: llama_3_1_8b has acceptable format compliance")
        print("  Review detailed results for specific failure patterns")
    else:
        print("✗ NOT RECOMMENDED: llama_3_1_8b has format compliance issues")
        print("  Similar to mistral_7b - avoid using as verifier")
    print()

    # Save to file
    output_data = {
        "summary": summary,
        "detailed_results": detailed,
        "test_statements": [
            {
                "idx": i,
                "text": s.text,
                "expected": s.expected,
                "category": s.category,
                "notes": s.notes,
            }
            for i, s in enumerate(TEST_STATEMENTS)
        ],
    }

    with open(args.output, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"Full results saved to: {args.output}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
