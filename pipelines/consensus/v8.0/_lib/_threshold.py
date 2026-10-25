"""
Threshold policies for consensus filtering.

Policy implementations for domain-based thresholds.

Available policies:
    - majority: (n // 2) + 1
    - unanimous: n
    - unanimous_reject: 1 (reject only when all votes are False)
    - 2/3_majority: (n * 2 + 2) // 3
    - 1/3_present: max(1, (n + 2) // 3)
"""

from __future__ import annotations

POLICY_IMPLEMENTATIONS: dict[str, callable] = {
    "majority": lambda n: (n // 2) + 1,
    "unanimous": lambda n: n,
    "unanimous_reject": lambda n: 1,
    "2/3_majority": lambda n: (n * 2 + 2) // 3,
    "1/3_present": lambda n: max(1, (n + 2) // 3),
}


def get_policy_fn(policy_name: str) -> callable:
    """
    Get threshold function for named policy.

    Args:
        policy_name: Policy name (majority, unanimous, 2/3_majority, 1/3_present)

    Returns:
        Function mapping total_reviewers -> min_true_count

    Raises:
        ValueError: If policy_name is unknown
    """
    if policy_name not in POLICY_IMPLEMENTATIONS:
        raise ValueError(f"Unknown threshold policy: {policy_name}")
    return POLICY_IMPLEMENTATIONS[policy_name]
