#!/usr/bin/env python3
"""
Example demonstrating process_ipc schema usage to prevent envelope extraction bugs.

This example shows how the new schema definitions and utilities prevent
the type of bug that occurred in universal-llm-gateway where the LLM
incorrectly assumed the response structure.
"""

from process_ipc import PingResponse, extract_domain_data
from process_ipc.core.messages import create_message


def demonstrate_schema_usage():
    """Demonstrate proper schema usage to prevent envelope extraction bugs."""

    print("=== process_ipc Schema Usage Example ===\n")

    # 1. What the worker returns (domain data)
    worker_response: PingResponse = {
        "status": "pong",
        "timestamp": "2025-10-14T23:03:17.916836",
        "model_id": "hermes3-llama-3.1-70b-uncensored",
        "model_loaded": True,
        "worker_id": "hermes3-llama-3.1-70b-uncensored",
    }

    print("1. Worker returns domain data:")
    print(f"   {worker_response}")
    print()

    # 2. Simple UML Message format (Simple UML Message - architectural cohesion)
    from process_ipc.core import signals

    # Simple format: payload contains worker response directly (no result wrapper)
    simple_message = signals.CommandComplete(
        result=worker_response,  # Direct worker response, no wrapper
        correlation_id="req_abc123",
    )

    print("2. Simple UML Message format:")
    print(f"   Signal: {simple_message['signal']}")
    print(f"   Correlation ID: {simple_message['correlation_id']}")
    print(f"   Payload: {simple_message['payload']}")  # Direct worker response
    print()

    # 3. What the manager receives (from supervisor.execute_command())
    manager_receives = simple_message  # Supervisor returns full message

    print("3. Manager receives Simple UML Message:")
    print(f"   {manager_receives}")
    print()

    # 4. CORRECT way to extract domain data (using schema utility)
    domain_data = extract_domain_data(manager_receives)

    print("4. CORRECT extraction using extract_domain_data():")
    print(f"   Domain data: {domain_data}")
    print(f"   Status: {domain_data['status']}")  # ✅ Works!
    print()

    # 5. WRONG way (what the LLM might do if not using extract_domain_data())
    print("5. WRONG extraction (accessing payload directly without utility):")
    try:
        # This would work, but is not the recommended pattern
        direct_payload = manager_receives["payload"]
        print(f"   Direct payload access: {direct_payload}")
        print(
            "   ⚠️  Works but not recommended - use extract_domain_data() "
            "for consistency"
        )
    except KeyError as e:
        print(f"   ❌ KeyError: {e}")
    print()

    # 6. Streaming format example
    print("6. Streaming format (DATA_STREAM):")
    streaming_message = create_message(
        signal=signals.DATA_STREAM,
        payload={"data": {"chunk": "token1", "index": 0}},  # Streaming payload format
        correlation_id="stream_xyz789",
    )
    print(f"   Message: {streaming_message}")
    streaming_data = extract_domain_data(streaming_message)
    print(
        f"   Extracted data: {streaming_data}"
    )  # Extracts {"chunk": "token1", "index": 0}
    print()

    # 7. Error handling for invalid messages
    print("7. Error handling for invalid messages:")
    try:
        invalid_message = {"signal": "command_complete"}  # Missing payload
        extract_domain_data(invalid_message)  # ❌ ValueError
    except ValueError as e:
        print("   ✅ Correctly detected invalid message:")
        print(f"   {str(e)[:100]}...")
    print()

    print("=== Key Benefits ===")
    print("✅ Simple UML Message format - consistent across all message types")
    print("✅ Canonical extraction utility prevents bugs")
    print("✅ Clear payload structure - no unnecessary nesting")
    print("✅ Architectural cohesion - same pattern for events, streaming, commands")
    print("✅ IDE autocomplete and type checking")
    print("✅ Error handling for invalid message structure")


if __name__ == "__main__":
    demonstrate_schema_usage()
