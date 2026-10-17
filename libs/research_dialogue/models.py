from __future__ import annotations


def normalize_ledger_model(model: str) -> str:
    """Ledger rows use ``anthropic/<api-model>``; API bodies use bare ids."""
    if model.startswith("anthropic/"):
        return model
    return f"anthropic/{model}"
