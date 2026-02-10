#!/usr/bin/env python3
"""
Test script for zyphra_math model with T/F output format.

Tests the mathematical verification prompt with simple T/F output
at the end of the response.
"""

import json
import re
import sys
from pathlib import Path

import requests


def test_zyphra_math_verification(
    claim: str, 
    expected_verdict: bool,
    base_url: str = "http://localhost:9999"
) -> dict[str, bool | str]:
    """
    Test zyphra_math model with T/F output format.
    
    Args:
        claim: Mathematical claim to verify
        expected_verdict: Expected truth value
        base_url: API endpoint base URL
        
    Returns:
        dict with test results including success status and details
    """
    system_prompt = """
          Verify T/F whether a given mathematical claim is accurate.

      Output your final answer as \boxed{T} for TRUE or \boxed{F} for FALSE
      """
    user_message = claim
    
    payload = {
        "model": "zyphra-zr1-1-5b-q8-0-4096",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.7,
        "top_p": 0.8,
    }
    
    print(f"Testing claim: {claim}")
    print(f"Expected verdict: {expected_verdict}")
    print(f"\nRequest payload:")
    print(json.dumps(payload, indent=2))
    print(f"\nSending request to {base_url}/v1/chat/completions...")
    
    try:
        response = requests.post(
            f"{base_url}/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        response.raise_for_status()
        
        result = response.json()
        print(f"\nResponse status: {response.status_code}")
        print(f"Full response:")
        print(json.dumps(result, indent=2))
        
        content = result["choices"][0]["message"]["content"]
        print(f"\nModel output content:")
        print(content)
        
        # Look for T or F at the end of the response
        content_stripped = content.strip()
        last_char = content_stripped[-1] if content_stripped else ""
        
        # Also try to find T/F pattern in the last line
        lines = content_stripped.split('\n')
        last_line = lines[-1].strip() if lines else ""
        
        print(f"\nLast line: {last_line}")
        print(f"Last character: '{last_char}'")
        
        # Try multiple patterns to extract verdict
        actual_verdict = None
        
        # Pattern 1: Ends with T or F
        if last_char in ('T', 'F'):
            actual_verdict = last_char == 'T'
            print(f"Extracted verdict from last character: {actual_verdict}")
        # Pattern 2: Last line contains "T" or "F" alone
        elif last_line in ('T', 'F'):
            actual_verdict = last_line == 'T'
            print(f"Extracted verdict from last line: {actual_verdict}")
        # Pattern 3: Search for T/F patterns in last line
        else:
            match = re.search(r'\b([TF])\b', last_line)
            if match:
                actual_verdict = match.group(1) == 'T'
                print(f"Extracted verdict from pattern: {actual_verdict}")
        
        if actual_verdict is None:
            return {
                "success": False,
                "error": "Could not extract T/F verdict from response",
                "raw_content": content
            }
        
        verdict_matches = actual_verdict == expected_verdict
        
        print(f"\nActual verdict: {actual_verdict}")
        print(f"Expected verdict: {expected_verdict}")
        print(f"Match: {verdict_matches}")
        
        return {
            "success": True,
            "verdict_correct": verdict_matches,
            "actual_verdict": actual_verdict,
            "expected_verdict": expected_verdict,
            "raw_content": content
        }
            
    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "error": f"Request failed: {e}"
        }


def main() -> int:
    """Run the test and report results."""
    print("=" * 80)
    print("Testing zyphra_math model with T/F output format")
    print("=" * 80)
    print()
    
    test_claim = "42 is a highly composite number"
    expected_verdict = False
    
    result = test_zyphra_math_verification(test_claim, expected_verdict)
    
    print("\n" + "=" * 80)
    print("TEST RESULTS")
    print("=" * 80)
    
    if not result["success"]:
        print(f"❌ Test FAILED: {result.get('error', 'Unknown error')}")
        if "raw_content" in result:
            print(f"Raw content: {result['raw_content']}")
        return 1
    
    print("✅ Model produced T/F output")
    
    if result["verdict_correct"]:
        print(f"✅ Verdict is CORRECT: {result['actual_verdict']} == {result['expected_verdict']}")
        return 0
    else:
        print(f"⚠️  Verdict is INCORRECT: {result['actual_verdict']} != {result['expected_verdict']}")
        print("\nNote: Model parsed correctly but mathematical reasoning may need review")
        return 0


if __name__ == "__main__":
    sys.exit(main())
