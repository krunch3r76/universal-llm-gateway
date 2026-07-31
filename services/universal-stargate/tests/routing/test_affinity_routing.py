"""Test cases for affinity-aware routing decision engine."""

import pytest
from unittest.mock import MagicMock

from systems.routing.selection.decision import DecisionEngine, load_routing_policy
from systems.routing.selection.decision.types import FeasibilityTier
from systems.routing.selection.types import Gateway, Placement


@pytest.fixture
def hermes3_affinity_policy():
    """Policy with hard affinity for hermes3 → jupiter."""
    config = {
        "routing": {
            "scoring": {
                "eviction_margin": 25,
            },
            "affinity": [
                {
                    "match": "hermes3-llama-3.1-70b-uncensored-*",
                    "gateway": "jupiter",
                    "strength": "hard",
                    "evict_if_needed": True,
                },
            ],
        },
    }
    return load_routing_policy(config)


@pytest.fixture
def hermes3_placement():
    """Placement for hermes3-70b model."""
    return Placement(
        model_id="hermes3-llama-3.1-70b-uncensored",
        ram_mb=16000,
        vram_mb=30888,
        is_gpu=True,
        original_model_id="hermes3-llama-3.1-70b-uncensored-16384-hybrid",
    )


def make_gateway(
    name: str,
    vram_free: int,
    vram_total: int = 48000,
    loaded_models: frozenset = frozenset(),
    available_models: frozenset = frozenset(),
) -> Gateway:
    """Create test gateway snapshot."""
    ref = MagicMock()
    ref.config.name = name

    return Gateway(
        ref=ref,
        name=name,
        ram_free_mb=64000,
        vram_free_mb=vram_free,
        ram_total_mb=128000,
        vram_total_mb=vram_total,
        loaded_models=loaded_models,
        available_models=available_models,
    )


class TestHermes3AffinityRouting:
    """Test cases for hermes3 → jupiter affinity routing."""

    def test_hermes3_routes_to_jupiter_when_available(
        self, hermes3_affinity_policy, hermes3_placement
    ):
        """hermes3 should route to jupiter when jupiter has resources."""
        engine = DecisionEngine(hermes3_affinity_policy)

        gateways = [
            make_gateway(
                "jupiter",
                vram_free=35000,  # Enough for hermes3
                available_models=frozenset(
                    ["hermes3-llama-3.1-70b-uncensored-16384-hybrid"]
                ),
            ),
            make_gateway(
                "gateway-1",
                vram_free=40000,  # Also has resources but no affinity
                available_models=frozenset(
                    ["hermes3-llama-3.1-70b-uncensored-16384-hybrid"]
                ),
            ),
        ]

        selected, trace = engine.select(gateways, hermes3_placement)

        assert selected is not None
        assert selected.name == "jupiter"
        assert trace.selection_tier == FeasibilityTier.T1_FEASIBLE_NOW
        assert "hard_affinity" in trace.selection_reason

    def test_hermes3_evicts_on_jupiter_when_affinity_hard(
        self, hermes3_affinity_policy, hermes3_placement
    ):
        """hermes3 should evict on jupiter (not route elsewhere) when hard affinity."""
        engine = DecisionEngine(hermes3_affinity_policy)

        gateways = [
            make_gateway(
                "jupiter",
                vram_free=2772,  # NOT enough - needs eviction
                loaded_models=frozenset(["ernie-4-5-21b"]),
                available_models=frozenset(
                    [
                        "hermes3-llama-3.1-70b-uncensored-16384-hybrid",
                        "ernie-4-5-21b",
                    ]
                ),
            ),
            make_gateway(
                "gateway-1",
                vram_free=40000,  # Has resources but no affinity
                available_models=frozenset(
                    ["hermes3-llama-3.1-70b-uncensored-16384-hybrid"]
                ),
            ),
        ]

        # Add model details for eviction planning
        gateways[0] = Gateway(
            **{
                **gateways[0].__dict__,
                "model_details": {
                    "ernie-4-5-21b": {
                        "vram_usage": 29000,
                        "ram_usage": 0,
                        "last_inference_time": None,
                    }
                },
            }
        )

        selected, trace = engine.select(gateways, hermes3_placement)

        assert selected is not None
        assert selected.name == "jupiter"
        assert trace.selection_tier == FeasibilityTier.T2_FEASIBLE_EVICT

        # Verify eviction plan
        jupiter_candidate = next(
            c for c in trace.candidates if c.gateway.name == "jupiter"
        )
        assert jupiter_candidate.eviction_plan is not None
        assert "ernie-4-5-21b" in jupiter_candidate.eviction_plan.models_to_evict

    def test_hermes3_falls_back_when_jupiter_infeasible(
        self, hermes3_affinity_policy, hermes3_placement
    ):
        """hermes3 falls back to other gateway when jupiter cannot serve."""
        engine = DecisionEngine(hermes3_affinity_policy)

        gateways = [
            make_gateway(
                "jupiter",
                vram_free=2772,  # NOT enough
                vram_total=32000,  # Cannot fit even empty
                available_models=frozenset(
                    ["hermes3-llama-3.1-70b-uncensored-16384-hybrid"]
                ),
            ),
            make_gateway(
                "gateway-1",
                vram_free=40000,  # Has resources
                available_models=frozenset(
                    ["hermes3-llama-3.1-70b-uncensored-16384-hybrid"]
                ),
            ),
        ]

        selected, trace = engine.select(gateways, hermes3_placement)

        # Should fall back to gateway-1 with warning
        assert selected is not None
        assert selected.name == "gateway-1"

        # Jupiter should be T0 (infeasible)
        jupiter_candidate = next(
            c for c in trace.candidates if c.gateway.name == "jupiter"
        )
        assert jupiter_candidate.tier == FeasibilityTier.T0_INFEASIBLE


class TestSoftAffinityRouting:
    """Test cases for soft affinity (bonus points, not required)."""

    def test_soft_affinity_prefers_affinity_gateway(self):
        """Soft affinity adds bonus but doesn't restrict."""
        config = {
            "routing": {
                "scoring": {
                    "weights": {"affinity": 100},  # High affinity weight
                },
                "affinity": [
                    {
                        "match": "qwen-*",
                        "gateway": "gateway-2",
                        "strength": "soft",
                        "bonus": 50,
                    },
                ],
            },
        }
        policy = load_routing_policy(config)
        engine = DecisionEngine(policy)

        placement = Placement(
            model_id="qwen-2.5-coder",
            ram_mb=8000,
            vram_mb=12000,
            is_gpu=True,
            original_model_id="qwen-2.5-coder-14b-8192",
        )

        gateways = [
            make_gateway(
                "gateway-1",
                vram_free=20000,
                available_models=frozenset(["qwen-2.5-coder-14b-8192"]),
            ),
            make_gateway(
                "gateway-2",
                vram_free=15000,  # Less resources but has affinity
                available_models=frozenset(["qwen-2.5-coder-14b-8192"]),
            ),
        ]

        selected, trace = engine.select(gateways, placement)

        # Should prefer gateway-2 due to affinity bonus
        assert selected is not None
        assert selected.name == "gateway-2"

        # Check affinity rule was applied
        gw2_candidate = next(
            c for c in trace.candidates if c.gateway.name == "gateway-2"
        )
        assert gw2_candidate.affinity_rule is not None
        assert gw2_candidate.score_components.affinity == 50

