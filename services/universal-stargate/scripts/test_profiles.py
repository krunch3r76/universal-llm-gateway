#!/usr/bin/env python3
"""
Test script for GGUF generation parameter profiles.

This script tests the profile management endpoints and one-shot profile usage.
Requires a running proxy (default: localhost:9999) and valid auth token.
"""

import json
import os
import sys

import requests

# Configuration
PROXY_URL = os.environ.get("PROXY_URL", "http://localhost:9999")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")

headers = {
    "Content-Type": "application/json",
}
if AUTH_TOKEN:
    headers["Authorization"] = f"Bearer {AUTH_TOKEN}"


def test_list_available_profiles():
    """Test listing available profile definitions."""
    print("\n=== Test 1: List Available Profiles ===")
    response = requests.get(f"{PROXY_URL}/api/v1/parameters/profiles")
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"Available profiles: {list(data['profiles'].keys())}")
        print(f"Note: {data.get('note')}")
        return True
    else:
        print(f"Error: {response.text}")
        return False


def test_set_global_profile(profile_name="general-chat"):
    """Test setting a global profile."""
    print(f"\n=== Test 2: Set Global Profile ({profile_name}) ===")
    response = requests.post(
        f"{PROXY_URL}/api/v1/parameters/profile",
        headers=headers,
        json={"profile": profile_name},
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"Success: {data.get('success')}")
        print(f"Scope: {data.get('scope')}")
        print(f"Parameters: {json.dumps(data.get('parameters', {}), indent=2)}")
        print(f"Warnings: {data.get('warnings', [])}")
        return True
    else:
        print(f"Error: {response.text}")
        return False


def test_get_active_profiles():
    """Test retrieving active profiles."""
    print("\n=== Test 3: Get Active Profiles ===")
    response = requests.get(f"{PROXY_URL}/api/v1/parameters/profile", headers=headers)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"Global: {data.get('global')}")
        print(f"Model-specific: {data.get('model_specific', {})}")
        return True
    else:
        print(f"Error: {response.text}")
        return False


def test_set_model_profile(model_id, profile_name="code-generation"):
    """Test setting a model-specific profile."""
    print(
        f"\n=== Test 4: Set Model-Specific Profile ({profile_name} for {model_id}) ==="
    )
    response = requests.post(
        f"{PROXY_URL}/api/v1/parameters/profile?model_id={model_id}",
        headers=headers,
        json={"profile": profile_name},
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"Success: {data.get('success')}")
        print(f"Scope: {data.get('scope')}")
        print(f"Model: {data.get('model_id')}")
        print(f"Compatible: {data.get('compatible')}")
        print(f"Format: {data.get('format')}")
        print(f"Warnings: {data.get('warnings', [])}")
        return True
    else:
        print(f"Error: {response.text}")
        return False


def test_clear_model_profile(model_id):
    """Test clearing a model-specific profile."""
    print(f"\n=== Test 5: Clear Model-Specific Profile ({model_id}) ===")
    response = requests.delete(
        f"{PROXY_URL}/api/v1/parameters/profile?model_id={model_id}", headers=headers
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"Success: {data.get('success')}")
        print(f"Cleared: {data.get('cleared')}")
        return True
    else:
        print(f"Error: {response.text}")
        return False


def test_clear_global_profile():
    """Test clearing the global profile."""
    print("\n=== Test 6: Clear Global Profile ===")
    response = requests.delete(
        f"{PROXY_URL}/api/v1/parameters/profile", headers=headers
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"Success: {data.get('success')}")
        print(f"Cleared: {data.get('cleared')}")
        return True
    else:
        print(f"Error: {response.text}")
        return False


def test_one_shot_profile(model_id, profile_name="creative"):
    """Test one-shot profile via chat completion query parameter."""
    print(f"\n=== Test 7: One-Shot Profile ({profile_name}) via Chat Completion ===")
    response = requests.post(
        f"{PROXY_URL}/v1/chat/completions?profile={profile_name}",
        headers=headers,
        json={
            "model": model_id,
            "messages": [{"role": "user", "content": "Hello!"}],
            "max_tokens": 10,
        },
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        print("Success! Profile applied to request (check logs for middleware_actions)")
        return True
    else:
        print(f"Error: {response.text}")
        return False


def test_filter_alias(profile_name="factual-qa"):
    """Test 'filter' alias for 'profile' parameter."""
    print(f"\n=== Test 8: Use 'filter' Alias ({profile_name}) ===")
    response = requests.post(
        f"{PROXY_URL}/api/v1/parameters/profile",
        headers=headers,
        json={"filter": profile_name},  # Use 'filter' instead of 'profile'
    )
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"Success: {data.get('success')}")
        print(f"Profile: {data.get('profile')}")
        return True
    else:
        print(f"Error: {response.text}")
        return False


def main():
    """Run all profile tests."""
    print("=" * 60)
    print("GGUF Profile Management Test Suite")
    print("=" * 60)
    print(f"Proxy URL: {PROXY_URL}")
    print(
        f"Auth: {'Enabled' if AUTH_TOKEN else 'Disabled (may fail if auth required)'}"
    )

    results = []

    # Test 1: List available profiles (no auth required)
    results.append(("List profiles", test_list_available_profiles()))

    # Test 2: Set global profile
    results.append(("Set global", test_set_global_profile("general-chat")))

    # Test 3: Get active profiles
    results.append(("Get active", test_get_active_profiles()))

    # Test 4: Set model-specific profile (use your model ID)
    model_id = sys.argv[1] if len(sys.argv) > 1 else "test-gguf-model"
    results.append(("Set model", test_set_model_profile(model_id, "code-generation")))

    # Test 5: Clear model profile
    results.append(("Clear model", test_clear_model_profile(model_id)))

    # Test 6: Clear global profile
    results.append(("Clear global", test_clear_global_profile()))

    # Test 7: Test 'filter' alias
    results.append(("Filter alias", test_filter_alias("factual-qa")))

    # Test 8: One-shot profile (requires model to be available)
    # results.append(("One-shot", test_one_shot_profile(model_id, "creative")))

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    passed = sum(1 for _, success in results if success)
    total = len(results)

    for name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status}: {name}")

    print(f"\nTotal: {passed}/{total} passed")
    print("=" * 60)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
