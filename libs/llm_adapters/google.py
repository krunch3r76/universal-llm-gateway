"""Google Gemini adapter — native generateContent API for frontier tools.

Translates FrontierRequest into Gemini's native body shape:
  contents[], systemInstruction, generationConfig, tools[]

Function calling uses Gemini's native functionDeclarations / functionCall /
functionResponse cycle.  The client-side tool loop in _frontier_core calls
append_tool_round to build multi-turn conversations.

Auth: x-goog-api-key header.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from llm_adapters._tool_schema import sanitize_tool_parameters

if TYPE_CHECKING:
    from llm_adapters import FrontierRequest, LLMRequest

logger = logging.getLogger(__name__)


def _openai_tool_to_gemini(tool: dict[str, Any]) -> dict[str, Any] | None:
    """Convert OpenAI function tool to Gemini functionDeclaration."""
    fn = tool.get("function")
    if not isinstance(fn, dict):
        if tool.get("type") == "function" and "name" in tool:
            fn = tool
        else:
            return None
    decl: dict[str, Any] = {}
    if "name" in fn:
        decl["name"] = fn["name"]
    if "description" in fn:
        decl["description"] = fn["description"]
    if "parameters" in fn:
        params = fn["parameters"]
        if isinstance(params, dict):
            decl["parameters"] = sanitize_tool_parameters(params)
        else:
            decl["parameters"] = params
    return decl


def _messages_to_contents(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert OpenAI-style messages to Gemini contents format.

    Gemini uses ``role: "user"`` and ``role: "model"`` (not ``assistant``).
    """
    contents: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        if role == "assistant":
            role = "model"
        elif role == "system":
            continue
        content_val = msg.get("content", "")
        if isinstance(content_val, str):
            parts = [{"text": content_val}]
        elif isinstance(content_val, list):
            parts = []
            for block in content_val:
                if isinstance(block, str):
                    parts.append({"text": block})
                elif isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append({"text": block.get("text", "")})
                    else:
                        parts.append(block)
        else:
            parts = [{"text": str(content_val)}]
        contents.append({"role": role, "parts": parts})
    return contents


class GoogleAdapter:
    """Google Gemini native API — x-goog-api-key auth, client-side tool resolution."""

    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        self._api_key = api_key
        self._base = (
            base_url or "https://generativelanguage.googleapis.com/v1beta"
        ).rstrip("/")

    @property
    def provider_label(self) -> str:
        return "google"

    def build_request(
        self,
        req: LLMRequest,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Build a basic Gemini generateContent request from LLMRequest."""
        headers = {
            "x-goog-api-key": self._api_key,
            "content-type": "application/json",
        }
        contents = _messages_to_contents(req.messages)
        body: dict[str, Any] = {"contents": contents}
        if req.system.strip():
            body["systemInstruction"] = {"parts": [{"text": req.system}]}
        gen_config: dict[str, Any] = {}
        if req.max_tokens is not None:
            gen_config["maxOutputTokens"] = req.max_tokens
        if req.temperature is not None:
            gen_config["temperature"] = req.temperature
        if req.top_p is not None:
            gen_config["topP"] = req.top_p
        if req.stop_sequences:
            gen_config["stopSequences"] = req.stop_sequences
        if gen_config:
            body["generationConfig"] = gen_config
        url = f"{self._base}/models/{req.model}:generateContent"
        return url, headers, body

    def build_frontier_request(
        self,
        req: FrontierRequest,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Build Gemini native generateContent request from FrontierRequest."""
        if req.remote_mcp:
            raise NotImplementedError("google has no native remote MCP protocol")
        headers: dict[str, str] = {
            "x-goog-api-key": self._api_key,
            "content-type": "application/json",
        }
        contents = _messages_to_contents(req.messages)
        # model is included so the cloud-proxy route can extract it for routing/telemetry;
        # forward_native strips it before the actual upstream call to Google
        body: dict[str, Any] = {"model": req.model, "contents": contents}

        if req.system.strip():
            body["systemInstruction"] = {"parts": [{"text": req.system}]}

        gen_config: dict[str, Any] = {}
        model_lower = req.model.lower()
        if req.max_tokens is not None:
            gen_config["maxOutputTokens"] = req.max_tokens
        else:
            # Kludge default: max_tokens is a ceiling not a target — reasoning
            # models don't size to fit it (they generate what the task
            # requires and may overshoot/undershoot, ignoring the limit as a
            # soft target). Default high to avoid silent low caps that
            # truncate mid-thinking-burn. 131072 = 128k, matches Anthropic
            # Opus 4.7's documented streaming max output ceiling; chosen as
            # the highest documented streaming ceiling across the frontier
            # providers we use. If a smaller-variant model (e.g. Gemini
            # Flash-Lite) rejects this, that's a LOUD signal to add a
            # per-model entry per todo:universal-max-tokens-model-ceiling-default.
            gen_config["maxOutputTokens"] = 131072
        if req.temperature is not None:
            gen_config["temperature"] = req.temperature
        if req.top_p is not None:
            gen_config["topP"] = req.top_p
        if req.stop_sequences:
            gen_config["stopSequences"] = req.stop_sequences
        if req.seed is not None:
            gen_config["seed"] = req.seed

        if req.thinking:
            level_raw = req.thinking.get("level") or req.thinking.get("effort")
            if level_raw:
                level = level_raw.strip().lower()
                # includeThoughts=True surfaces thought-summary parts in the
                # response so parse_frontier_response can populate `thinking`
                # — without it, reasoning is invisible for post-hoc triage.
                if model_lower.startswith("gemini-3"):
                    # Gemini 3 thinkingLevel: minimal | low | medium | high
                    # (lowercase per docs/thirdparty/google-api/upstream/
                    # thinking.md). 3.1 Pro does not support `minimal`; emit
                    # anyway — the API rejects with a documented error and
                    # the caller sees provider-native diagnostics.
                    if level in {"minimal", "low", "medium", "high"}:
                        gen_config["thinkingConfig"] = {
                            "thinkingLevel": level,
                            "includeThoughts": True,
                        }
                elif model_lower.startswith("gemini-2.5"):
                    # Gemini 2.5 rejects thinkingLevel; it requires an
                    # integer thinkingBudget. 1024/8192/24576 lie inside
                    # every 2.5 variant's valid range (pro 128-32768,
                    # flash 0-24576, flash-lite 512-24576). Extended
                    # values (none/minimal/xhigh/max) have no documented
                    # 2.5 mapping — fall through to the model default.
                    budget_map = {"low": 1024, "medium": 8192, "high": 24576}
                    budget = budget_map.get(level)
                    if budget is not None:
                        gen_config["thinkingConfig"] = {
                            "thinkingBudget": budget,
                            "includeThoughts": True,
                        }

        if req.response_format:
            fmt = req.response_format
            if isinstance(fmt, dict):
                rf_type = fmt.get("type")
                if rf_type == "json_object":
                    gen_config["responseMimeType"] = "application/json"
                elif rf_type == "json_schema":
                    gen_config["responseMimeType"] = "application/json"
                    schema = fmt.get("json_schema", {}).get("schema")
                    if schema:
                        gen_config["responseSchema"] = schema

        if gen_config:
            body["generationConfig"] = gen_config

        tools_list: list[dict[str, Any]] = []
        function_decls: list[dict[str, Any]] = []
        if req.tools:
            for tool in req.tools:
                if not isinstance(tool, dict):
                    continue
                t_type = tool.get("type", "function")
                if t_type == "function":
                    decl = _openai_tool_to_gemini(tool)
                    if decl:
                        function_decls.append(decl)
                elif t_type in ("google_search", "googleSearch"):
                    tools_list.append({"google_search": {}})
                elif t_type in ("code_execution", "codeExecution"):
                    tools_list.append({"code_execution": {}})
                else:
                    tools_list.append(tool)
        if function_decls:
            tools_list.append({"functionDeclarations": function_decls})
        if tools_list:
            body["tools"] = tools_list

        if req.tool_choice is not None:
            tc = req.tool_choice
            if isinstance(tc, str):
                mode_map = {
                    "auto": "AUTO",
                    "any": "ANY",
                    "none": "NONE",
                    "required": "ANY",
                }
                mode = mode_map.get(tc)
                if mode:
                    body["toolConfig"] = {"functionCallingConfig": {"mode": mode}}
            elif isinstance(tc, dict) and tc.get("type") == "function":
                fn_name = tc.get("function", {}).get("name")
                if fn_name:
                    body["toolConfig"] = {
                        "functionCallingConfig": {
                            "mode": "ANY",
                            "allowedFunctionNames": [fn_name],
                        }
                    }

        vendor_opts = (req.provider_options or {}).get("google", {})
        for k, v in vendor_opts.items():
            if k not in body:
                body[k] = v

        url = f"{self._base}/models/{req.model}:generateContent"
        return url, headers, body

    def parse_frontier_response(self, response_data: dict[str, Any]) -> dict[str, Any]:
        """Parse Gemini generateContent response into standard frontier format."""
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        server_tool_calls: list[dict[str, Any]] = []
        finish_reason: str | None = None

        candidates = response_data.get("candidates", [])
        if candidates:
            candidate = candidates[0]
            finish_reason = candidate.get("finishReason")
            parts = (candidate.get("content") or {}).get("parts", [])
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if part.get("thought") and "text" in part:
                    thinking_parts.append(part["text"])
                elif "text" in part and not part.get("thought"):
                    content_parts.append(part["text"])
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls.append(
                        {
                            "id": fc.get("id"),
                            "name": fc.get("name"),
                            "input": fc.get("args", {}),
                            "arguments": json.dumps(fc.get("args", {})),
                        }
                    )
                elif "executableCode" in part or "codeExecutionResult" in part:
                    server_tool_calls.append(part)

            grounding = candidate.get("groundingMetadata")
            if grounding:
                server_tool_calls.append({"type": "grounding", **grounding})

        prompt_feedback = response_data.get("promptFeedback") or {}
        block_reason = prompt_feedback.get("blockReason")

        usage_meta = response_data.get("usageMetadata", {})
        usage: dict[str, Any] = {
            "input_tokens": usage_meta.get("promptTokenCount", 0),
            "output_tokens": usage_meta.get("candidatesTokenCount", 0),
            "reasoning_tokens": usage_meta.get("thoughtsTokenCount"),
            "cached_tokens": usage_meta.get("cachedContentTokenCount"),
        }

        thinking: dict[str, Any] | None = None
        if thinking_parts:
            thinking = {
                "text": "\n".join(thinking_parts),
                "tokens": usage_meta.get("thoughtsTokenCount", 0),
            }

        return {
            "content": "".join(content_parts),
            "model": response_data.get("modelVersion", ""),
            "provider": "google",
            "usage": usage,
            "thinking": thinking,
            "tool_calls": tool_calls or None,
            "server_tool_calls": server_tool_calls or None,
            "response_id": response_data.get("responseId"),
            "finish_reason": finish_reason,
            "block_reason": block_reason,
            "raw": None,
        }

    def append_tool_round(
        self,
        body: dict[str, Any],
        raw_response: dict[str, Any],
        tool_results: list[dict[str, Any]],
    ) -> None:
        """Append function call + response turns for the next Gemini conversation turn.

        Gemini multi-turn tool calling:
        1. Append the model's response (with functionCall parts) to contents
        2. Append a user turn with functionResponse parts
        """
        candidates = raw_response.get("candidates", [])
        if candidates:
            model_content = candidates[0].get("content") or {}
            if model_content:
                body["contents"].append(model_content)

        response_parts: list[dict[str, Any]] = []
        for tr in tool_results:
            part: dict[str, Any] = {
                "functionResponse": {
                    "name": tr["name"],
                    "response": {"result": tr["content"]},
                }
            }
            if tr.get("id"):
                part["functionResponse"]["id"] = tr["id"]
            response_parts.append(part)

        if response_parts:
            body["contents"].append({"role": "user", "parts": response_parts})

    def strip_tools(self, body: dict[str, Any]) -> None:
        """Remove tool inventory from the body for a no-tools synthesis turn.

        Drops ``tools`` and ``toolConfig`` so the next ``generateContent``
        call carries no functionDeclarations. Prior model/user turns in
        ``body["contents"]`` (including ``functionCall`` / ``functionResponse``
        parts already appended via ``append_tool_round``) are preserved so
        the model can summarize from what it learned.
        """
        body.pop("tools", None)
        body.pop("toolConfig", None)

    def append_exhaustion_advisory(self, body: dict[str, Any], text: str) -> None:
        """Append a trailing user message in ``body["contents"]`` with the advisory.

        Gemini's ``systemInstruction`` is a single block defined at request
        time and not safe to mutate mid-conversation. The cleanest place for
        the advisory is a final user-role turn in ``contents``, which the
        model treats as the most recent instruction.
        """
        contents = body.setdefault("contents", [])
        contents.append({"role": "user", "parts": [{"text": text}]})

    def extract_text(self, response_data: dict[str, Any]) -> str:
        candidates = response_data.get("candidates", [])
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts", [])
        return "".join(
            p.get("text", "")
            for p in parts
            if isinstance(p, dict) and "text" in p and not p.get("thought")
        )

    def extract_usage(self, response_data: dict[str, Any]) -> dict[str, int]:
        u = response_data.get("usageMetadata", {})
        return {
            "input_tokens": int(u.get("promptTokenCount", 0)),
            "output_tokens": int(u.get("candidatesTokenCount", 0)),
        }
