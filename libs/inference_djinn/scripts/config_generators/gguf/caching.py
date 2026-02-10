"""
Cache Management

Handles caching of complete WholeProfile configurations for incremental builds.
Supports GPU → CPU → push workflow.
"""

import json
import os
import sys

from .profiles import WholeProfile
from .utils import compute_cache_key, get_cache_path


def cache_whole_profile(model_path: str, profile: WholeProfile) -> str:
    """
    Cache complete WholeProfile.

    Args:
        model_path: Path to the model file
        profile: WholeProfile to cache

    Returns:
        Cache key used
    """
    cache_key = compute_cache_key(model_path)
    cache_path = get_cache_path(cache_key)

    try:
        cache_data = {
            "config": profile.to_dict(),
            "timestamp": os.path.getmtime(model_path),
        }

        with open(cache_path, "w") as f:
            json.dump(cache_data, f, indent=2)

        print(f"Cached configuration: {cache_path}", file=sys.stderr)
        return cache_key
    except Exception as e:
        print(f"Warning: Failed to cache profile: {e}", file=sys.stderr)
        return cache_key


def load_cached_profile(model_path: str) -> WholeProfile | None:
    """
    Load cached WholeProfile.

    Args:
        model_path: Path to the model file

    Returns:
        WholeProfile if found and valid, None otherwise
    """
    cache_key = compute_cache_key(model_path)
    cache_path = get_cache_path(cache_key)

    if not cache_path.exists():
        print(f"No cache found: {cache_path}", file=sys.stderr)
        return None

    try:
        with open(cache_path) as f:
            cache_data = json.load(f)

        config = cache_data.get("config")
        if not config:
            print("Cache file missing 'config' field", file=sys.stderr)
            return None

        # Reconstruct WholeProfile from cached dict
        profile = WholeProfile(
            info=config.get("info", {}),
            base_loader=config.get("base_loader", {}),
            profiles=config.get("profiles"),
            cpu_profiles=config.get("cpu_profiles"),
        )

        print(f"Loaded cached configuration from: {cache_path}", file=sys.stderr)
        return profile

    except Exception as e:
        print(f"Warning: Failed to load cached profile: {e}", file=sys.stderr)
        return None


def merge_cached_profiles(model_path: str, new_profile: WholeProfile) -> WholeProfile:
    """
    Merge new profile with existing cached profile.

    Used for incremental builds (e.g., GPU testing followed by CPU testing).

    Args:
        model_path: Path to the model file
        new_profile: New WholeProfile to merge

    Returns:
        Merged WholeProfile
    """
    existing = load_cached_profile(model_path)

    if existing is None:
        # No existing cache, just use new profile
        return new_profile

    # Merge: combine profiles and cpu_profiles from both
    merged_profile = WholeProfile(
        info=new_profile.info,  # Use new info (more recent)
        base_loader=new_profile.base_loader,
        profiles=new_profile.profiles or existing.profiles,
        cpu_profiles=new_profile.cpu_profiles or existing.cpu_profiles,
    )

    return merged_profile
