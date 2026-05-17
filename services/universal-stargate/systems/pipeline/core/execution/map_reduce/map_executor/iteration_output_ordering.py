"""Iteration output ordering helper for map execution modes.

Extracts the common post-collection assembly of successful StepOutput
values into caller-specified order. Both timeout and fail-fast paths
previously duplicated the same three-list building loop; this module
provides a single implementation so future changes stay in one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....schemas import StepOutput


def ordered_successful_outputs(
    iteration_metadata: list[tuple[int, str | None]],
    results_by_index: dict[int, StepOutput],
) -> tuple[list[StepOutput], list[str | None], list[int]]:
    """Return three parallel lists of successful results in original order.

    iteration_metadata supplies the authoritative submission order as
    (index, key) pairs. Only those indices present in results_by_index
    (i.e., the iterations that produced a StepOutput) are emitted.

    The three returned lists are:
      - outputs: the StepOutput values themselves
      - output_keys: the corresponding map keys (or None)
      - output_positions: the original indices (useful for callers that
        need to reconstruct positions)

    The ordering is strictly the order of appearance inside
    iteration_metadata; no sorting or reordering occurs here.
    """
    outputs: list[StepOutput] = []
    output_keys: list[str | None] = []
    output_positions: list[int] = []
    for idx, key in iteration_metadata:
        if idx in results_by_index:
            outputs.append(results_by_index[idx])
            output_keys.append(key)
            output_positions.append(idx)
    return outputs, output_keys, output_positions
