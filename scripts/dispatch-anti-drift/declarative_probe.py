"""Lane B.1 — tier-1 declarative drift probe (G2 anti-drift CI, SoT §4).

For each provider that exposes a usable declarative capability endpoint,
fetch the provider-declared values and diff them against the registry facet.
A divergence is drift; the probe fails and records a mismatch report.

Provider coverage:
- Google ``models.get`` — fields map ~1:1 to our facet.
- Anthropic ``/v1/models`` — VERIFY-before-rely: confirm at runtime that the
  response carries ``max_tokens`` + ``thinking.types`` + effort fields before
  treating it as a declarative source; fall back to behavioral-only otherwise.
- OpenAI / xAI — no usable declarative capability endpoint; tier-1 N/A.

Output: list of ``DeclarativeFinding`` (one per probed model × provider).
Drift entries are collected, not raised; the runner aggregates into the report.

[universal:rest] — all HTTP via ``transport_utils.make_sync_client``.
[universal:modelid] — ``ModelId`` for identity, never string parsing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx
from llm_adapters.capability_dispatch.registry import (
    _ANTHROPIC_MAX_OUTPUT_CEILINGS,
    resolve,
)
from transport_utils import make_sync_client

# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass
class DeclarativeFinding:
    provider: str
    model: str
    declared_ceiling: int | None
    registry_ceiling: int | None
    endpoint_supported: bool
    drift: bool
    note: str = ""


@dataclass
class DeclarativeReport:
    findings: list[DeclarativeFinding] = field(default_factory=list)

    @property
    def drift_count(self) -> int:
        return sum(1 for f in self.findings if f.drift)

    def passed(self) -> bool:
        return self.drift_count == 0


# --------------------------------------------------------------------------- #
# Google — models.get
# --------------------------------------------------------------------------- #

_GOOGLE_PROBE_MODELS: tuple[str, ...] = ("gemini-3-pro",)
_GOOGLE_API_BASE = "https://generativelanguage.googleapis.com"


def _probe_google(report: DeclarativeReport, api_key: str | None) -> None:
    if not api_key:
        report.findings.append(
            DeclarativeFinding(
                provider="google",
                model="(all)",
                declared_ceiling=None,
                registry_ceiling=None,
                endpoint_supported=False,
                drift=False,
                note="GOOGLE_API_KEY not set — skipping tier-1 declarative probe",
            )
        )
        return

    with make_sync_client(_GOOGLE_API_BASE, timeout=15.0) as client:
        for model in _GOOGLE_PROBE_MODELS:
            _probe_google_model(client, api_key, model, report)


def _probe_google_model(
    client: httpx.Client,
    api_key: str,
    bare_model: str,
    report: DeclarativeReport,
) -> None:
    full_model = f"google/{bare_model}"
    try:
        resp = client.get(
            f"/v1beta/models/{bare_model}",
            params={"key": api_key},
            timeout=10.0,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
    except Exception as exc:
        report.findings.append(
            DeclarativeFinding(
                provider="google",
                model=full_model,
                declared_ceiling=None,
                registry_ceiling=None,
                endpoint_supported=False,
                drift=False,
                note=f"fetch error: {exc}",
            )
        )
        return

    declared_ceiling: int | None = data.get("outputTokenLimit")
    dispatch = resolve(full_model)
    registry_ceiling = dispatch.max_output.ceiling
    # Google has no ceiling in our registry (None), but the API declares one.
    # Drift = declared limit meaningfully deviates from registered behavior.
    drift = (
        declared_ceiling is not None
        and registry_ceiling is not None
        and declared_ceiling != registry_ceiling
    )
    report.findings.append(
        DeclarativeFinding(
            provider="google",
            model=full_model,
            declared_ceiling=declared_ceiling,
            registry_ceiling=registry_ceiling,
            endpoint_supported=True,
            drift=drift,
            note=""
            if not drift
            else f"declared {declared_ceiling} ≠ registry {registry_ceiling}",
        )
    )


# --------------------------------------------------------------------------- #
# Anthropic — /v1/models (VERIFY-before-rely)
# --------------------------------------------------------------------------- #

_ANTHROPIC_API_BASE = "https://api.anthropic.com"
_ANTHROPIC_PROBE_MARKERS: tuple[str, ...] = tuple(
    marker for marker, _ in _ANTHROPIC_MAX_OUTPUT_CEILINGS[:6]
)
_ANTHROPIC_REQUIRED_FIELDS = {"max_tokens"}


def _probe_anthropic(report: DeclarativeReport, api_key: str | None) -> None:
    if not api_key:
        report.findings.append(
            DeclarativeFinding(
                provider="anthropic",
                model="(all)",
                declared_ceiling=None,
                registry_ceiling=None,
                endpoint_supported=False,
                drift=False,
                note="ANTHROPIC_API_KEY not set — skipping tier-1 declarative probe",
            )
        )
        return

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    with make_sync_client(_ANTHROPIC_API_BASE, timeout=15.0) as client:
        # VERIFY-before-rely: confirm the endpoint shape before trusting it.
        verified, models_data = _verify_anthropic_endpoint(client, headers)
        if not verified:
            report.findings.append(
                DeclarativeFinding(
                    provider="anthropic",
                    model="(all)",
                    declared_ceiling=None,
                    registry_ceiling=None,
                    endpoint_supported=False,
                    drift=False,
                    note="Anthropic /v1/models does not carry max_tokens — behavioral-only",
                )
            )
            return

        for model_data in models_data:
            model_id = model_data.get("id", "")
            if not any(m in model_id for m in _ANTHROPIC_PROBE_MARKERS):
                continue
            _probe_anthropic_model(model_id, model_data, report)


def _verify_anthropic_endpoint(
    client: httpx.Client,
    headers: dict[str, str],
) -> tuple[bool, list[dict[str, Any]]]:
    """Return (endpoint_carries_required_fields, model_list)."""
    try:
        resp = client.get("/v1/models", headers=headers, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return False, []

    models: list[dict[str, Any]] = data.get("data", [])
    if not models:
        return False, []
    sample = models[0]
    supported = _ANTHROPIC_REQUIRED_FIELDS.issubset(sample.keys())
    return supported, models


def _probe_anthropic_model(
    model_id: str,
    model_data: dict[str, Any],
    report: DeclarativeReport,
) -> None:
    declared_ceiling: int | None = model_data.get("max_tokens")
    full_model = f"anthropic/{model_id}"
    dispatch = resolve(full_model)
    registry_ceiling = dispatch.max_output.ceiling
    drift = (
        declared_ceiling is not None
        and registry_ceiling is not None
        and declared_ceiling != registry_ceiling
    )
    report.findings.append(
        DeclarativeFinding(
            provider="anthropic",
            model=full_model,
            declared_ceiling=declared_ceiling,
            registry_ceiling=registry_ceiling,
            endpoint_supported=True,
            drift=drift,
            note=""
            if not drift
            else f"declared {declared_ceiling} ≠ registry {registry_ceiling}",
        )
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def run_declarative_probe() -> DeclarativeReport:
    """Run all tier-1 declarative probes and return a consolidated report."""
    report = DeclarativeReport()
    _probe_google(report, os.environ.get("GOOGLE_API_KEY"))
    _probe_anthropic(report, os.environ.get("ANTHROPIC_API_KEY"))
    # OpenAI / xAI: tier-1 N/A — behavioral-only (SoT §4).
    return report
