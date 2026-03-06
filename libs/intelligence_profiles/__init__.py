"""Intelligence profiles — per-model quality and suitability metadata.

Public API:
  - IntelligenceProfile: per-model profile schema
  - ModelRequirements: declarative pipeline model selection
  - IntelligenceProfileStore: curated + derived profile storage and query
"""

from .requirements import (
    CostBudget,
    ModelRequirements,
    ProviderDiversity,
    SelectionRequest,
)
from .schema import (
    CrossModal,
    DomainScore,
    Evidence,
    GenerationQuality,
    IntelligenceProfile,
    LanguageCoverage,
    RoleSuitabilityEntry,
    Score,
    StyleProfile,
    VariantEntry,
    score_gte,
)
from .store import IntelligenceProfileStore

__all__ = [
    "CostBudget",
    "CrossModal",
    "DomainScore",
    "Evidence",
    "GenerationQuality",
    "IntelligenceProfile",
    "IntelligenceProfileStore",
    "LanguageCoverage",
    "ModelRequirements",
    "ProviderDiversity",
    "SelectionRequest",
    "RoleSuitabilityEntry",
    "Score",
    "StyleProfile",
    "VariantEntry",
    "score_gte",
]
