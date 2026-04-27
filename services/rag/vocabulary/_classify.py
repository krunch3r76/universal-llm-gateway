"""LLM-based vocabulary classification: local model and frontier pipeline paths."""

from __future__ import annotations

import json
import logging

import httpx

from ._categories import DEFAULT_TAXONOMY
from ._prompt import build_classification_prompt
from ._stargate import DEFAULT_STARGATE_CHAT_URL

logger = logging.getLogger(__name__)


async def classify_scope_async(
    *,
    scope: str,
    description: str,
    terms: list[str],
    model: str,
    taxonomy: list[str] | None = None,
    chat_url: str = DEFAULT_STARGATE_CHAT_URL,
    client: httpx.AsyncClient | None = None,
) -> dict[str, list[str]] | None:
    """Classify terms via Stargate chat completions (async).

    taxonomy: ordered list of category names to classify into. Defaults to
    DEFAULT_TAXONOMY when omitted. Pass config.vocabulary_taxonomy so that
    custom categories (e.g. 'quantitative') are included in the prompt and
    parsed from the response.
    """
    effective_taxonomy = taxonomy if taxonomy is not None else DEFAULT_TAXONOMY
    keys_str = ", ".join(effective_taxonomy)
    user_msg = (
        f"Scope: {scope}\n"
        f"Description: {description}\n"
        f"Terms to classify:\n{json.dumps(terms)}\n\n"
        f"Return JSON with keys: {keys_str}."
    )
    prompt = build_classification_prompt(effective_taxonomy)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.2,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
    }
    own_client = client is None
    hc = client or httpx.AsyncClient(timeout=120.0)
    try:
        resp = await hc.post(chat_url, json=payload)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        clean: dict[str, list[str]] = {
            cat: [
                str(t) for t in parsed.get(cat, []) if isinstance(t, str) and t.strip()
            ]
            for cat in effective_taxonomy
        }
        return clean
    except (httpx.RequestError, httpx.HTTPStatusError) as e:
        logger.exception(
            "Classification failed for scope '%s' due to HTTP error: %s", scope, e
        )
        return None
    except (KeyError, json.JSONDecodeError) as e:
        logger.exception(
            "Classification failed for scope '%s' due to JSON parsing error: %s",
            scope,
            e,
        )
        return None
    except Exception as e:
        logger.exception(
            "Classification failed for scope '%s' due to unexpected error: %s", scope, e
        )
        return None
    finally:
        if own_client and hc is not None:
            await hc.aclose()


async def _classify_frontier_scopes(
    scope_names: list[str],
    chat_url: str,
) -> set[str]:
    """Classify scopes via vocab-classify-v1 pipeline (frontier/cloud models).

    The pipeline writes vocabulary to the property index itself; the caller is
    responsible for stamping watermarks. Returns the set of scopes written.
    """
    payload: dict = {
        "model": "vocab-classify-v1",
        "messages": [{"role": "user", "content": "vocabulary classification"}],
        "pipeline_options": {
            "mode": "frontier",
            "scopes": scope_names,
            "skip_fresh": False,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3600.0)) as client:
            resp = await client.post(chat_url, json=payload)
            resp.raise_for_status()
            choices = resp.json().get("choices", [])
            content = choices[0]["message"]["content"] if choices else "{}"
            vocab = json.loads(content).get("vocabulary", {})
            return set(vocab.keys())
    except Exception as exc:
        logger.error(
            "Frontier vocab classification failed for %d scope(s): %s",
            len(scope_names),
            exc,
        )
        return set()
