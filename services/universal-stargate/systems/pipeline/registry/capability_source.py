"""Project the live pipeline registry into a capability tree that Stargate uses for capability listing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from capability_tree.members import CapabilityMember
from capability_tree.tree import CapabilityTree

if TYPE_CHECKING:
    from ...proxy.stargate_core import StargateProxy
    from .core import PipelineRegistry


class PipelineRegistrySource:
    """Adapter: each registry pipeline becomes one CapabilityMember. This is the only source in this tree."""

    def __init__(self, registry: PipelineRegistry) -> None:
        self._registry = registry

    def name(self) -> str:
        return "pipeline"

    def members(self) -> list[CapabilityMember]:
        out: list[CapabilityMember] = []
        for pipeline_id, spec in self._registry.pipelines.items():
            summary = {
                "steps": len(spec.steps),
                "models": sorted(
                    {
                        step.model_ref
                        for step in spec.steps
                        if step.model_ref and step.model_ref != pipeline_id
                    }
                ),
                "domain": spec.domain,
                "version": spec.version,
                "timeout_seconds": spec.options.timeout_seconds,
                "category": spec.category,
                "url": f"/api/v1/capabilities/{spec.category}/{pipeline_id}",
            }
            out.append(
                CapabilityMember(
                    id=pipeline_id,
                    category=spec.category,
                    source=self.name(),
                    summary=summary,
                )
            )
        return out


def build_tree(proxy: StargateProxy) -> CapabilityTree:
    """Rebuild the capability tree from the live registry. Empty source when the registry is unset."""
    registry = proxy.pipeline_registry
    if registry is None:
        return CapabilityTree.build(_EmptySource())
    return CapabilityTree.build(PipelineRegistrySource(registry))


class _EmptySource:
    def name(self) -> str:
        return "pipeline"

    def members(self) -> list[CapabilityMember]:
        return []
