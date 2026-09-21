"""prompt-expand v1 domain handlers — validate, profile lookup, retrieve, classify, format."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, override

import httpx
import yaml
from systems.pipeline.core.constants import (
    RAG_NO_RESULTS_SENTINEL,
    RAG_NO_RETRIEVAL_SENTINEL,
)
from systems.pipeline.core.dag import PipelineExecutionError
from systems.pipeline.core.events.prompt_expand import (
    ExpandAdmitted,
    ExpandCompleted,
    ExpandRetrieveCompleted,
)
from systems.pipeline.core.execution.errors import StepTimeoutError
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.pipeline_call import PipelineCallHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_logging import get_logger

logger = get_logger(__name__)

_PROFILE_TABLES_PATH = Path(__file__).resolve().parent.parent / "profile_tables.yaml"
_RETRIEVE_MAX_ATTEMPTS = 3
_RETRIEVE_BACKOFF_SECONDS = 2.0
_PIPELINE_CALL_HANDLER = PipelineCallHandler()

_VALID_CONTRACTS = frozenset(
    {"consult", "investigate", "implement", "confer", "review", "none"}
)
_VALID_STAGES = frozenset(
    {f"g{i}" for i in range(1, 8)} | {"none"}
)
_VALID_TARGETS = frozenset({"cdp", "cursor", "grok-bot"})
_VALID_DELIVERY = frozenset({"prompt", "dispatch"})
_VALID_RAG_FAIL = frozenset({"abort", "stamp"})

_EMPTY_RETRIEVAL_SENTINEL = (
    "Retrieval unavailable — pipeline 'rag-context' returned empty content"
)
# rag-context empty bodies use the shared sentinels, not the pipeline_call
# wrapper string. Classify used to treat those 50-char strings as rag_status=ok.
_EMPTY_RETRIEVE_MARKERS = (
    _EMPTY_RETRIEVAL_SENTINEL,
    RAG_NO_RESULTS_SENTINEL,
    RAG_NO_RETRIEVAL_SENTINEL,
)

_tables_cache: dict[str, Any] | None = None


def _load_profile_tables() -> dict[str, Any]:
    global _tables_cache  # noqa: PLW0603
    if _tables_cache is not None:
        return _tables_cache
    if not _PROFILE_TABLES_PATH.is_file():
        _tables_cache = {}
        return _tables_cache
    loaded = yaml.safe_load(_PROFILE_TABLES_PATH.read_text(encoding="utf-8"))
    _tables_cache = loaded if isinstance(loaded, dict) else {}
    return _tables_cache


def _typed_reject(code: str, message: str) -> StepOutput:
    payload = {"ok": False, "error": {"code": code, "message": message}}
    return StepOutput(raw=json.dumps(payload), json=payload, error=message)


def _stage_matches(row_stage: str, call_stage: str) -> bool:
    if row_stage == "*":
        return True
    return row_stage == call_stage


def _lookup_profile(
    contract: str, stage: str, executor_tier: str
) -> dict[str, Any] | None:
    tables = _load_profile_tables()
    profiles: list[dict[str, Any]] = tables.get("profiles") or []
    candidates = [
        (contract, stage, executor_tier),
        (contract, "*", executor_tier),
        ("*", "*", executor_tier),
    ]
    for c, s, t in candidates:
        for row in profiles:
            if (
                row.get("contract") == c
                and _stage_matches(str(row.get("stage", "")), s)
                and row.get("executor_tier") == t
            ):
                return row
    return None


def _lookup_target(target_id: str) -> dict[str, Any] | None:
    tables = _load_profile_tables()
    for row in tables.get("targets") or []:
        if row.get("id") == target_id:
            return row
    return None


def _pipeline_meta(context: Any) -> tuple[str, str]:
    pipeline_id = getattr(getattr(context, "pipeline", None), "id", "") or "prompt-expand"
    execution_id = str(getattr(context, "execution_id", "") or "")
    return pipeline_id, execution_id


def _options(context: Any) -> dict[str, Any]:
    return dict(getattr(context, "options", {}) or {})


def _is_timeout_exception(exc: BaseException) -> bool:
    if isinstance(exc, StepTimeoutError | httpx.TimeoutException):
        return True
    return type(exc).__name__ in {
        "TimeoutException",
        "ReadTimeout",
        "StepTimeoutError",
        "HandlerTimeoutError",
    }


def _build_retrieve_pipeline_options(
    step: Any, context: Any, scopes: list[str]
) -> dict[str, Any]:
    step_options: dict[str, Any] = dict(
        step.get_domain_field("pipeline_options", {}) or {}
    )
    forwarded = {
        k: v
        for k, v in _options(context).items()
        if k.startswith(("rag_", "scope_", "rerank_"))
        or k == "include_retrieval_metadata"
    }
    merged: dict[str, Any] = {**step_options, **forwarded, "scope": scopes}
    merged.pop("target", None)
    return merged


def _synthetic_pipeline_call_step(
    parent_step: Any, pipeline_options: dict[str, Any]
) -> Any:
    consumer_model_ref = parent_step.get_domain_field("consumer_model_ref", "")
    stargate_url = parent_step.get_domain_field("stargate_url", None)
    per_attempt_timeout = parent_step.get_domain_field(
        "per_attempt_timeout_seconds", 90
    )

    class _CallStep:
        id = getattr(parent_step, "id", "retrieve_context")
        handler_timeout_seconds = per_attempt_timeout
        timeout_seconds = per_attempt_timeout

        @staticmethod
        def get_domain_field(key: str, default: Any = None) -> Any:
            if key == "pipeline_id":
                return "rag-context"
            if key == "pipeline_options":
                return pipeline_options
            if key == "consumer_model_ref":
                return consumer_model_ref
            if key == "stargate_url" and stargate_url is not None:
                return stargate_url
            return default

    return _CallStep()


class PromptExpandValidateOptionsHandler(BaseHandler):
    """Programmatic admission — typed reject for missing target, grok-bot, invalid pairs."""

    step_type = "prompt_expand_validate_options_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = _options(context)
        target = opts.get("target")
        if not target:
            return _typed_reject("expand.target_required", "options.target is required")
        if target == "grok-bot":
            return _typed_reject(
                "expand.target_unsupported",
                "target grok-bot is not supported in v1",
            )
        if target not in _VALID_TARGETS:
            return _typed_reject(
                "expand.target_invalid",
                f"unknown target {target!r}; expected cdp|cursor",
            )

        contract = opts.get("contract")
        if contract not in _VALID_CONTRACTS:
            return _typed_reject(
                "expand.contract_invalid",
                f"invalid contract {contract!r}",
            )

        stage = opts.get("stage")
        if stage not in _VALID_STAGES:
            return _typed_reject("expand.stage_invalid", f"invalid stage {stage!r}")

        delivery = opts.get("delivery", "prompt")
        if delivery not in _VALID_DELIVERY:
            return _typed_reject(
                "expand.delivery_invalid",
                f"invalid delivery {delivery!r}; slot dropped in v1",
            )

        rag_fail = opts.get("rag_fail", "abort")
        if rag_fail not in _VALID_RAG_FAIL:
            return _typed_reject("expand.rag_fail_invalid", f"invalid rag_fail {rag_fail!r}")

        executor_tier = opts.get("executor_tier", "frontier")
        if executor_tier not in {"frontier", "small"}:
            return _typed_reject(
                "expand.executor_tier_invalid",
                f"invalid executor_tier {executor_tier!r}",
            )

        pipeline_id, execution_id = _pipeline_meta(context)
        self._publish_bus_event(
            context,
            ExpandAdmitted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                contract=str(contract),
                stage=str(stage),
                executor_tier=str(executor_tier),
                target=str(target),
                delivery=str(delivery),
            ),
        )

        payload = {
            "ok": True,
            "contract": contract,
            "stage": stage,
            "executor_tier": executor_tier,
            "target": target,
            "delivery": delivery,
            "rag_fail": rag_fail,
        }
        return StepOutput(raw=json.dumps(payload), json=payload)


class PromptExpandProfileLookupHandler(BaseHandler):
    """Two-key profile row lookup from profile_tables.yaml."""

    step_type = "prompt_expand_profile_lookup_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = _options(context)
        contract = str(opts.get("contract", ""))
        stage = str(opts.get("stage", ""))
        executor_tier = str(opts.get("executor_tier", "frontier"))

        row = _lookup_profile(contract, stage, executor_tier)
        if row is None:
            return _typed_reject(
                "expand.profile_miss",
                f"no profile row for ({contract}, {stage}, {executor_tier})",
            )

        target_row = _lookup_target(str(opts.get("target", "")))
        if target_row is None:
            return _typed_reject(
                "expand.target_miss",
                f"no target row for {opts.get('target')!r}",
            )

        payload = {
            "ok": True,
            "contract": contract,
            "stage": stage,
            "executor_tier": executor_tier,
            "retrieve_scopes": list(row.get("retrieve_scopes") or []),
            "elicitation": bool(row.get("elicitation")),
            "target": target_row.get("id"),
            "target_static": bool(target_row.get("static")),
            "allowed_doors": list(target_row.get("allowed_doors") or []),
        }
        return StepOutput(raw=json.dumps(payload), json=payload)


class PromptExpandRetrieveHandler(BaseHandler):
    """RAG retrieve leg — composes PipelineCallHandler with handler-owned 3× retry."""

    step_type = "prompt_expand_retrieve_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        profile_out = context.get_output("resolve_profile")
        if not profile_out or not profile_out.json:
            return _typed_reject("expand.profile_missing", "resolve_profile output missing")

        scopes = list(profile_out.json.get("retrieve_scopes") or [])
        merged_options = _build_retrieve_pipeline_options(step, context, scopes)
        call_step = _synthetic_pipeline_call_step(step, merged_options)

        last_latency_ms = 0.0
        for attempt in range(1, _RETRIEVE_MAX_ATTEMPTS + 1):
            try:
                call_out = await _PIPELINE_CALL_HANDLER.execute(call_step, context)
            except PipelineExecutionError as exc:
                return StepOutput(
                    raw=str(exc),
                    json={
                        "merged_options": merged_options,
                        "attempts": attempt,
                        "timed_out": False,
                        "upstream_error": True,
                        "latency_ms": last_latency_ms,
                    },
                )
            except Exception as exc:
                if not _is_timeout_exception(exc):
                    raise
                if attempt < _RETRIEVE_MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRIEVE_BACKOFF_SECONDS)
                    continue
                return StepOutput(
                    raw="",
                    json={
                        "merged_options": merged_options,
                        "attempts": attempt,
                        "timed_out": True,
                        "upstream_error": False,
                        "latency_ms": last_latency_ms,
                    },
                )

            last_latency_ms = float(getattr(call_out, "latency_ms", 0) or 0)
            step_json: dict[str, Any] = {
                "merged_options": merged_options,
                "attempts": attempt,
                "timed_out": False,
                "upstream_error": False,
                "latency_ms": last_latency_ms,
            }
            if call_out.json:
                step_json.update(call_out.json)
            return StepOutput(raw=call_out.raw, json=step_json)


class PromptExpandClassifyRetrieveHandler(BaseHandler):
    """Classify retrieve outcome — rag_status, provenance mode, proceed gate."""

    step_type = "prompt_expand_classify_retrieve_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = _options(context)
        rag_fail = str(opts.get("rag_fail", "abort"))
        retrieve_out = context.get_output("retrieve_context")
        profile_out = context.get_output("resolve_profile")
        retrieve_scopes = (
            (profile_out.json or {}).get("retrieve_scopes") if profile_out else []
        ) or []

        raw = (retrieve_out.raw if retrieve_out else "") or ""
        retrieve_json = (retrieve_out.json if retrieve_out else {}) or {}
        timed_out = bool(retrieve_json.get("timed_out"))
        upstream_error = bool(retrieve_json.get("upstream_error"))
        attempts = int(retrieve_json.get("attempts", 0))

        if timed_out:
            rag_status = "deadline"
            provenance_mode = "DEGRADED"
            proceed = True
        elif upstream_error:
            rag_status = "upstream"
            if rag_fail == "stamp":
                provenance_mode = "PRIORS-ONLY"
                proceed = True
            else:
                provenance_mode = "ABORT"
                proceed = False
        elif not raw.strip() or any(marker in raw for marker in _EMPTY_RETRIEVE_MARKERS):
            rag_status = "empty"
            if rag_fail == "stamp":
                provenance_mode = "PRIORS-ONLY"
                proceed = True
            else:
                provenance_mode = "ABORT"
                proceed = False
        else:
            rag_status = "ok"
            provenance_mode = "NORMAL"
            proceed = True

        pipeline_id, execution_id = _pipeline_meta(context)
        self._publish_bus_event(
            context,
            ExpandRetrieveCompleted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                step_name=str(getattr(step, "id", "classify_retrieve")),
                rag_status=rag_status,
                attempts=attempts,
                retrieve_scopes=list(retrieve_scopes),
            ),
        )

        payload = {
            "rag_status": rag_status,
            "attempts": attempts,
            "provenance_mode": provenance_mode,
            "proceed": proceed,
            "retrieve_scopes": retrieve_scopes,
        }
        if not proceed:
            return StepOutput(
                raw=json.dumps(payload),
                json=payload,
                error=f"rag_fail=abort blocked author on rag_status={rag_status}",
            )
        return StepOutput(raw=json.dumps(payload), json=payload)


class PromptExpandFormatOutputHandler(BaseHandler):
    """Emit the retrieval author-bundle for the prelude Cursor-substrate author.

    Authorship is not a DAG generate step — cursor/ models stay off chat
    completions. CDP door checks run after the author returns TASK′.
    """

    step_type = "prompt_expand_format_output_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = _options(context)
        delivery = str(opts.get("delivery", "prompt"))
        target = str(opts.get("target", ""))
        classify_out = context.get_output("classify_retrieve")
        profile_out = context.get_output("resolve_profile")
        retrieve_out = context.get_output("retrieve_context")

        classify_json = (classify_out.json if classify_out else {}) or {}
        profile_json = (profile_out.json if profile_out else {}) or {}
        rag_status = str(classify_json.get("rag_status", "unknown"))
        provenance_mode = str(classify_json.get("provenance_mode", "NORMAL"))
        attempts = int(classify_json.get("attempts") or 1)
        rag_context = (retrieve_out.raw if retrieve_out else "") or ""

        if classify_json.get("proceed") is False:
            err_payload = {
                "ok": False,
                "error": classify_json,
                "rag_status": rag_status,
            }
            return StepOutput(
                raw=json.dumps(err_payload),
                json=err_payload,
                error=f"retrieve leg aborted: rag_status={rag_status}",
            )

        header = {
            "pipeline": "prompt-expand",
            "contract": profile_json.get("contract"),
            "stage": profile_json.get("stage"),
            "executor_tier": profile_json.get("executor_tier"),
            "target": target,
            "static": profile_json.get("target_static", True),
            "retrieve_scopes": profile_json.get("retrieve_scopes"),
            "rag_status": rag_status,
            "provenance_mode": provenance_mode,
            "attempts": attempts,
            "elicitation": profile_json.get("elicitation"),
            "allowed_doors": profile_json.get("allowed_doors"),
        }
        prompt_key = "author_cdp" if target == "cdp" else "author_cursor"
        bundle = {
            "ok": True,
            "delivery": delivery,
            "rag_context": rag_context,
            "text": getattr(context, "source_text", "") or "",
            "contract": profile_json.get("contract"),
            "stage": profile_json.get("stage"),
            "executor_tier": profile_json.get("executor_tier"),
            "elicitation": profile_json.get("elicitation"),
            "target": target,
            "prompt_key": prompt_key,
            "prompt_ref": f"prompt_expand.v1.{prompt_key}",
            "header": header,
            "rag_status": rag_status,
            "provenance_mode": provenance_mode,
            "proceed": True,
        }

        pipeline_id, execution_id = _pipeline_meta(context)
        if delivery == "dispatch":
            envelope = {
                "fire_plan": {
                    "target": target,
                    "delivery": delivery,
                    "prompt_ref": bundle["prompt_ref"],
                    "options": {
                        k: opts[k]
                        for k in (
                            "contract",
                            "stage",
                            "executor_tier",
                            "rag_fail",
                            "follow_up",
                        )
                        if k in opts
                    },
                },
                "header": header,
                "author_bundle": bundle,
                "rag_status": rag_status,
                "degraded": provenance_mode == "DEGRADED",
            }
            raw = json.dumps(envelope, indent=2)
            self._publish_bus_event(
                context,
                ExpandCompleted(
                    pipeline_id=pipeline_id,
                    execution_id=execution_id,
                    delivery=delivery,
                    target=target,
                    rag_status=rag_status,
                    provenance_mode=provenance_mode,
                ),
            )
            return StepOutput(raw=raw, json=envelope)

        raw = json.dumps(bundle)
        self._publish_bus_event(
            context,
            ExpandCompleted(
                pipeline_id=pipeline_id,
                execution_id=execution_id,
                delivery=delivery,
                target=target,
                rag_status=rag_status,
                provenance_mode=provenance_mode,
            ),
        )
        return StepOutput(raw=raw, json=bundle)
