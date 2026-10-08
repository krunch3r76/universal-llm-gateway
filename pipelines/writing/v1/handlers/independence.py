"""Family check that the reviewer model is not the writer's family.

``model_family`` is pure. ``WritingIndependenceHandler`` reads
``pipelines/writing/models.yaml`` unless tests inject model ids. A shared or
unknown family refuses the run unless ``allow_partial_independence`` is set.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput


def _step(payload: dict[str, Any]) -> StepOutput:
    return StepOutput(raw=json.dumps(payload), json=payload)


def model_family(model_id: str) -> str | None:
    """Map a model id to a vendor family so the reviewer can differ from the writer.

    Rules run in a fixed order on the lowercased id. Unknown ids return None,
    which the independence handler treats as a failed check unless partial
    independence is explicitly allowed.
    """
    text = (model_id or "").lower()
    if text.startswith("cdp/") or "claude" in text:
        return "anthropic"
    if text.startswith("xai/") or "grok" in text:
        return "xai"
    if text.startswith("cursor/composer"):
        return "cursor"
    if "hermes" in text or "llama" in text:
        return "llama"
    if "qwen" in text:
        return "qwen"
    if "gemma" in text:
        return "gemma"
    if text.startswith("openai/") or "gpt" in text:
        return "openai"
    return None


def load_writing_models(path: Path | None = None) -> dict[str, str]:
    """Return writer, reviser, and reviewer model ids from ``models.yaml``.

    ``path`` overrides the file next to the writing pipeline directory.
    Missing aliases are omitted. Callers use the ids for the family check
    and for the finalize seat record. This read does not touch the network.
    """
    yaml_path = path or Path(__file__).resolve().parents[2] / "models.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    models = data.get("models") or {}
    found: dict[str, str] = {}
    if not isinstance(models, dict):
        return found
    for key, row in models.items():
        if isinstance(row, dict) and row.get("model"):
            found[str(key)] = str(row["model"])
    return found


def _allow_partial(options: dict[str, Any]) -> bool:
    value = options.get("allow_partial_independence", False)
    if isinstance(value, dict) and "default" in value:
        value = value.get("default", False)
    return value is True


class WritingIndependenceHandler(BaseHandler):
    """Compare writer and reviewer families before the review generate step.

    Inject ``writer_model`` and ``reviewer_model`` in tests. Otherwise both
    ids come from ``models.yaml``. Equal families, or either family unknown,
    refuse unless the run opted into partial independence.
    """

    step_type = "writing_independence_v1"

    def __init__(
        self,
        writer_model: str | None = None,
        reviewer_model: str | None = None,
    ) -> None:
        super().__init__()
        self._writer_model = writer_model
        self._reviewer_model = reviewer_model

    async def execute(self, step: Any, context: Any) -> StepOutput:
        """Return full, partial, or an independence refusal for this run.

        ``context.options`` supplies ``allow_partial_independence``. The
        handler does not call a model. ``refused`` is null when the families
        differ or when partial independence is allowed.
        """
        del step
        options = getattr(context, "options", {}) or {}
        raw_overrides = options.get("model_ref_overrides")
        overrides = raw_overrides if isinstance(raw_overrides, dict) else {}
        models = load_writing_models()
        writer = (
            self._writer_model
            or overrides.get("draft")
            or overrides.get("writer")
            or models.get("writer")
            or ""
        )
        reviewer = (
            self._reviewer_model
            or overrides.get("review")
            or overrides.get("reviewer")
            or models.get("reviewer")
            or ""
        )
        writer_family = model_family(writer)
        reviewer_family = model_family(reviewer)
        if writer_family and reviewer_family and writer_family != reviewer_family:
            independence: str | None = "full"
            refused = None
        elif _allow_partial(options):
            independence = "partial"
            refused = None
        else:
            independence = None
            refused = "independence_violation"
        return _step(
            {
                "ok": refused is None,
                "refused": refused,
                "independence": independence,
                "writer_model": writer,
                "reviewer_model": reviewer,
                "writer_family": writer_family,
                "reviewer_family": reviewer_family,
            }
        )
