"""Primary consult CLI entrypoint wiring parser and execution branch handlers.

This module keeps command startup deterministic and delegates branch-specific
runtime behavior to focused helper modules inside the consult CLI package.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from consult_lib.model_selection import load_roles, split_role_config
from consult_lib.pipeline import get_pipeline_id

from .direct_branch import _run_direct_branch
from .parser import _build_parser, _handle_admin_flags, print_role_listing
from .reviewer_pipeline import _run_pipeline_branch


def _normalize_role_specific_args(args: Any) -> None:
    """Apply role-specific CLI normalization before dispatch.

    Reviewer mode always runs through the ``code-review`` pipeline over explicit
    context files. RAG flags are disabled here so shared parser defaults never
    leak into reviewer artifacts, logs, or follow-up debugging.
    """
    if args.role != "reviewer":
        return

    args.pipeline = True
    args.no_rag = True
    args.no_rag_pipeline = True
    args.rag_pipeline = None
    args.rag_top_k = None


def _read_question_from_source(
    parser: Any, question_file: str | None, question_arg: str | None
) -> str:
    """Read question from --question-file, stdin, or positional argument.

    Enforces that --question-file and positional question are mutually exclusive.
    Raises parser.error when input is missing, empty, or unreadable.
    """
    if question_file:
        if question_arg:
            parser.error(
                "Cannot combine --question-file (-Q) with a positional question argument"
            )
        if question_file == "-":
            question = sys.stdin.read().strip()
        else:
            question_path = Path(question_file)
            try:
                question = question_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                parser.error(f"Cannot read question file '{question_file}': {exc}")
        if not question:
            parser.error(
                f"Question file '{question_file}' is empty or contains only whitespace"
            )
        return question

    if question_arg:
        return question_arg

    if not sys.stdin.isatty():
        question = sys.stdin.read().strip()
        if question:
            return question
    parser.error("No question provided")
    return ""


def main() -> None:
    """Parse arguments, normalize request inputs, and dispatch consultation mode."""
    raw_roles = load_roles()
    role_prompts, role_requirements = split_role_config(raw_roles)
    parser = _build_parser(role_prompts)
    args = parser.parse_args()
    started_at = time.monotonic()

    if _handle_admin_flags(args):
        return

    _normalize_role_specific_args(args)

    if args.list_roles:
        print_role_listing(role_prompts)
        return

    args.question = _read_question_from_source(parser, args.question_file, args.question)

    call_id = str(uuid4())
    role_pipeline_id = get_pipeline_id(args.role)

    pipeline_options: dict[str, Any] | None = None
    if args.pipeline_options:
        try:
            pipeline_options = json.loads(args.pipeline_options)
            if not isinstance(pipeline_options, dict):
                parser.error("--pipeline-options must be a JSON object")
        except json.JSONDecodeError as exc:
            parser.error(
                "--pipeline-options is not valid JSON; expected an object like "
                "'{\"metadata_boost_enabled\": false}' "
                f"(parser error: {exc.msg})"
            )
    args.pipeline_options_parsed = pipeline_options

    if args.pipeline:
        _run_pipeline_branch(
            args,
            parser,
            role_requirements,
            started_at,
            call_id,
            role_pipeline_id,
        )
        return

    _run_direct_branch(
        args,
        parser,
        role_requirements,
        started_at,
        call_id,
        role_pipeline_id,
    )
