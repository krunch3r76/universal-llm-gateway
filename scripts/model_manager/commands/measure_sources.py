"""Model source selection for remeasure command (API-first with file fallback)."""
# ruff: noqa: PLC0415

from __future__ import annotations

import argparse
import sys

from ..api_client import GatewayAPIClient, get_api_client
from ..config import Config

GGUF_FORMAT = "gguf"
VLLM_FORMATS = {"hf", "awq", "gptq"}


def get_models_to_remeasure(
    args: argparse.Namespace, config: Config
) -> list[tuple[str, str]] | None:
    """
    Determine models to remeasure using Gateway API when available.

    Falls back to file-based catalog when Gateway is unreachable or when
    API lookup fails.
    """
    client = get_api_client(
        getattr(args, "gateway", None),
        api_key=getattr(args, "gateway_api_key", None),
        timeout=getattr(args, "timeout", None),
    )

    if client and not getattr(args, "local", False):
        models = _get_models_from_api(client, args)
        if models is not None:
            return models

    if getattr(args, "verbose", False):
        print("Using file-based catalog (Gateway unavailable)", file=sys.stderr)
    return _get_models_from_files(args, config)


def _get_models_from_api(
    client: GatewayAPIClient, args: argparse.Namespace
) -> list[tuple[str, str]] | None:
    """Retrieve models via Gateway API."""
    if args.model:
        model = client.get_model(args.model)
        if not model:
            print(f"Model not found: {args.model}", file=sys.stderr)
            return None
        model_format = model.metadata.get("format", "")
        return [(args.model, model_format)]

    if not args.all:
        print("Specify --model <id> or --all", file=sys.stderr)
        return None

    gguf_only = getattr(args, "gguf_only", False)
    format_filter = "gguf" if gguf_only else None

    try:
        summaries = client.list_models(format_filter)
    except Exception as exc:  # noqa: BLE001
        print(f"API error: {exc}", file=sys.stderr)
        return None

    models: list[tuple[str, str]] = []
    for m in summaries:
        if m.format == GGUF_FORMAT:
            models.append((m.model_id, m.format))
        elif m.format in VLLM_FORMATS and not gguf_only:
            models.append((m.model_id, m.format))

    return models


def _get_models_from_files(
    args: argparse.Namespace, config: Config
) -> list[tuple[str, str]] | None:
    """Fallback to file-based catalog using inference_djinn."""
    try:
        from inference_djinn.catalog.local_config import (
            load_local_catalog,
            load_static_catalog,
            merge_catalogs,
        )
    except ImportError:
        print("Error: inference_djinn not available", file=sys.stderr)
        return None

    if args.model:
        return _get_single_model_with_format(args.model, config)

    if not args.all:
        print("Specify --model <id> or --all", file=sys.stderr)
        return None

    static_path = config.catalog_path if config.catalog_path.exists() else None
    try:
        static = load_static_catalog(static_path)
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Failed to load static catalog: {exc}", file=sys.stderr)
        return None

    try:
        local = load_local_catalog()
    except Exception:
        local = {"models": {}}

    merged = merge_catalogs(static, local)
    merged_models = merged.get("models", {})

    gguf_only = getattr(args, "gguf_only", False)

    models: list[tuple[str, str]] = []
    for mid, data in merged_models.items():
        if not isinstance(data, dict):
            continue
        model_format = data.get("metadata", {}).get("format", "")

        if model_format == GGUF_FORMAT:
            models.append((mid, model_format))
        elif model_format in VLLM_FORMATS and not gguf_only:
            models.append((mid, model_format))

    return models


def _get_single_model_with_format(
    model_id: str, config: Config
) -> list[tuple[str, str]] | None:
    """Look up format for a single model using file-based catalog."""
    try:
        from inference_djinn.catalog.local_config import (
            load_local_catalog,
            load_static_catalog,
            merge_catalogs,
        )
    except ImportError:
        print("Error: inference_djinn not available", file=sys.stderr)
        return None

    static_path = config.catalog_path if config.catalog_path.exists() else None
    try:
        static = load_static_catalog(static_path)
    except Exception:
        static = {"models": {}}

    try:
        local = load_local_catalog()
    except Exception:
        local = {"models": {}}

    merged = merge_catalogs(static, local)
    model_data = merged.get("models", {}).get(model_id)

    if not model_data:
        print(f"❌ Model '{model_id}' not found in catalog", file=sys.stderr)
        return None

    model_format = model_data.get("metadata", {}).get("format", GGUF_FORMAT)
    return [(model_id, model_format)]
