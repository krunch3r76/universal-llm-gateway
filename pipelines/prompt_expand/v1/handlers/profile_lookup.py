"""prompt-expand v1 domain handlers — validate, profile lookup, retrieve, classify, format."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, override

import yaml
from systems.pipeline.core.events.prompt_expand import (
    ExpandAdmitted,
    ExpandAuthorCompleted,
    ExpandCompleted,
    ExpandRetrieveCompleted,
)
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_PROFILE_TABLES_PATH = Path(__file__).resolve().parent.parent / "profile_tables.yaml"
_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"

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
    """RAG retrieve leg — pipeline_call_v1 semantics with dynamic scope from profile row."""

    step_type = "prompt_expand_retrieve_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        profile_out = context.get_output("resolve_profile")
        if not profile_out or not profile_out.json:
            return _typed_reject("expand.profile_missing", "resolve_profile output missing")

        scopes = profile_out.json.get("retrieve_scopes") or []
        step_options: dict[str, Any] = step.get_domain_field("pipeline_options", {}) or {}
        forwarded = {
            k: v
            for k, v in _options(context).items()
            if k.startswith(("rag_", "scope_", "rerank_"))
            or k == "include_retrieval_metadata"
        }
        merged_options: dict[str, Any] = {
            **step_options,
            **forwarded,
            "scope": scopes,
        }
        if "target" in merged_options:
            del merged_options["target"]

        consumer_model_ref: str = step.get_domain_field("consumer_model_ref", "")
        if consumer_model_ref and context._registry is not None:
            try:
                model_config = context._registry.get_model_config(
                    consumer_model_ref,
                    domain=context.pipeline.domain,
                    search_path=context.pipeline.source_search_path,
                )
                merged_options["consumer_model"] = model_config.model
            except KeyError:
                logger.warning(
                    "prompt_expand_retrieve: consumer_model_ref %r not found",
                    consumer_model_ref,
                )

        try:
            from pipelines.rag.scope_helpers import fetch_scope_options_text

            if "scope_options" not in merged_options:
                merged_options["scope_options"] = fetch_scope_options_text()
        except Exception as exc:  # noqa: BLE001
            logger.warning("prompt_expand_retrieve: scope_options inject failed: %s", exc)

        body = {
            "model": "rag-context",
            "messages": [{"role": "user", "content": context.source_text}],
            "stream": False,
            "pipeline_options": merged_options,
        }

        stargate_url: str = step.get_domain_field("stargate_url", DEFAULT_STARGATE_URL)
        timeout = (step.handler_timeout_seconds or step.timeout_seconds or 60) + 10
        start = time.monotonic()
        timed_out = False
        upstream_error = False
        content = ""
        step_json: dict[str, Any] = {"merged_options": merged_options}

        try:
            async with make_async_client(stargate_url, timeout=timeout) as client:
                response = await client.post(_CHAT_COMPLETIONS_PATH, json=body)
            latency_ms = (time.monotonic() - start) * 1000
            if response.is_error:
                upstream_error = True
                content = f"Sub-pipeline 'rag-context' failed: {response.text}"
            else:
                data = response.json()
                content = (
                    data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    or ""
                )
                if not content.strip():
                    content = (
                        f"{_EMPTY_RETRIEVAL_SENTINEL}. "
                        "The answer is generated from model knowledge only."
                    )
                pipeline_block = data.get("pipeline")
                if isinstance(pipeline_block, dict):
                    retrieval = pipeline_block.get("retrieval")
                    if isinstance(retrieval, dict) and retrieval:
                        step_json["retrieval"] = retrieval
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.monotonic() - start) * 1000
            exc_name = type(exc).__name__
            if exc_name in {"TimeoutException", "ReadTimeout", "StepTimeoutError"}:
                timed_out = True
                content = ""
            else:
                upstream_error = True
                content = f"Sub-pipeline 'rag-context' failed: {exc}"

        step_json.update(
            {
                "timed_out": timed_out,
                "upstream_error": upstream_error,
                "latency_ms": latency_ms,
            }
        )
        return StepOutput(raw=content, json=step_json)


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
        attempts = int(retrieve_json.get("attempts") or 1)

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
        elif _EMPTY_RETRIEVAL_SENTINEL in raw:
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
    """Map delivery mode to prompt markdown or dispatch JSON envelope."""

    step_type = "prompt_expand_format_output_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        opts = _options(context)
        delivery = str(opts.get("delivery", "prompt"))
        target = str(opts.get("target", ""))
        classify_out = context.get_output("classify_retrieve")
        profile_out = context.get_output("resolve_profile")
        author_out = context.get_output("select_author")

        classify_json = (classify_out.json if classify_out else {}) or {}
        profile_json = (profile_out.json if profile_out else {}) or {}
        rag_status = str(classify_json.get("rag_status", "unknown"))
        provenance_mode = str(classify_json.get("provenance_mode", "NORMAL"))
        attempts = int(classify_json.get("attempts") or 1)

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

        authored = (author_out.raw if author_out else "") or ""
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

        pipeline_id, execution_id = _pipeline_meta(context)
        author_step = "author_cdp" if target == "cdp" else "author_cursor"
        if authored.strip():
            self._publish_bus_event(
                context,
                ExpandAuthorCompleted(
                    pipeline_id=pipeline_id,
                    execution_id=execution_id,
                    step_name=author_step,
                    target=target,
                ),
            )

        if delivery == "dispatch":
            envelope = {
                "fire_plan": {
                    "target": target,
                    "delivery": delivery,
                    "prompt_ref": f"prompt_expand.v1.author_{target}",
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
                "prompt": authored,
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

        front_matter = yaml.safe_dump(header, sort_keys=False).strip()
        raw = f"---\n{front_matter}\n---\n\n{authored}"
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
        return StepOutput(raw=raw, json={"header": header, "delivery": delivery})
