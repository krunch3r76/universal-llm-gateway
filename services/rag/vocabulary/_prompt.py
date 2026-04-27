"""Build the system prompt for scope vocabulary classification."""

from __future__ import annotations

from ._categories import DEFAULT_TAXONOMY, _CATEGORY_DESCRIPTIONS


def build_classification_prompt(taxonomy: list[str]) -> str:
    """Build the system prompt for vocabulary classification from a taxonomy list.

    Known categories use their curated descriptions from _CATEGORY_DESCRIPTIONS.
    Unknown categories get a generic description — add to _CATEGORY_DESCRIPTIONS
    when a new category is introduced to give the LLM better guidance.
    """
    bullets: list[str] = []
    for cat in taxonomy:
        desc = _CATEGORY_DESCRIPTIONS.get(
            cat,
            f"terms characteristic of {cat} discourse in the domain.",
        )
        bullets.append(f"- **{cat}**: {desc}")
    bullets_text = "\n\n".join(bullets)
    keys_json = ", ".join(f'"{c}"' for c in taxonomy)
    output_example = (
        "{\n"
        + "\n".join(f'  "{c}": ["term1", "term2", ...],' for c in taxonomy)
        + "\n}"
    )
    return (
        "You are classifying vocabulary terms for a multi-domain RAG retrieval system.\n"
        "Given a scope name, its description, and a list of IDF-scored terms extracted\n"
        f"from that scope's corpus, classify each term into one of these categories:\n\n"
        f"{bullets_text}\n\n"
        "Rules:\n"
        "1. A term may appear in only one category (choose the best fit).\n"
        "2. DROP noise — these are never useful vocabulary:\n"
        "   - Single letters or bare symbols (a, r, x, θ)\n"
        "   - Document structure references (theorem 4.1, lemma a.1, figure 4, table 2)\n"
        "   - Author citation fragments (et al., guijarro-ordonez et al. (2021))\n"
        "   - Mathematical variable names without semantic meaning (z[q], θ[q])\n"
        "   - Overly generic words (model, system, data, method, results, approach)\n"
        "3. Use the scope description to guide domain-appropriate classification.\n"
        "   The same term can belong to different categories in different domains.\n"
        "4. You may add 2-4 additional high-value terms per category that are\n"
        "   obviously missing but central to the scope. Mark these with a trailing\n"
        "   asterisk (*) so the caller knows they were inferred.\n"
        "5. Return valid JSON only.\n\n"
        f"Output format (keys: {keys_json}):\n"
        f"{output_example}\n"
    )


__all__ = ["build_classification_prompt", "DEFAULT_TAXONOMY"]
