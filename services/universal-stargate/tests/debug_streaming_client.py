#!/usr/bin/env python3
"""
Debug script for testing streaming response consumption.

This script helps diagnose client-side issues with streaming responses,
including timeouts, disconnections, and consumption failures.
"""
import asyncio
import time
import json
import sys
from typing import Optional, List, Dict, Any
import httpx
import argparse
from datetime import datetime

# ANSI color codes for output
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    RESET = '\033[0m'


class StreamingDebugClient:
    """Client for debugging streaming response issues."""
    
    def __init__(self, base_url: str = "http://localhost:9999"):
        self.base_url = base_url
        self.logs: List[str] = []
    
    def log(self, message: str, level: str = "INFO"):
        """Log a message with timestamp."""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        color = {
            "INFO": Colors.BLUE,
            "SUCCESS": Colors.GREEN,
            "WARNING": Colors.YELLOW,
            "ERROR": Colors.RED
        }.get(level, Colors.RESET)
        
        log_entry = f"[{timestamp}] {color}{level}{Colors.RESET}: {message}"
        print(log_entry)
        self.logs.append(log_entry)
    
    async def test_streaming_request(
        self,
        model: str,
        messages: List[Dict[str, str]],
        timeout: Optional[float] = None,
        simulate_disconnect: bool = False,
        disconnect_after_seconds: float = 2.0
    ) -> Dict[str, Any]:
        """
        Test a streaming request with detailed diagnostics.
        
        Args:
            model: Model to use
            messages: Chat messages
            timeout: Request timeout (None for no timeout)
            simulate_disconnect: Whether to simulate client disconnect
            disconnect_after_seconds: When to disconnect if simulating
            
        Returns:
            Dictionary with test results
        """
        url = f"{self.base_url}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "stream": True
        }
        
        results = {
            "request_sent": False,
            "response_received": False,
            "status_code": None,
            "headers_received": {},
            "first_chunk_time": None,
            "chunks_received": 0,
            "content": [],
            "error": None,
            "disconnected": False,
            "total_duration": 0
        }
        
        start_time = time.time()
        
        try:
            # Configure timeout
            timeout_config = httpx.Timeout(timeout if timeout else None)
            
            async with httpx.AsyncClient(timeout=timeout_config) as client:
                self.log(f"Sending request to {url}", "INFO")
                self.log(f"Payload: {json.dumps(payload, indent=2)}", "INFO")
                
                results["request_sent"] = True
                
                async with client.stream('POST', url, json=payload) as response:
                    results["response_received"] = True
                    results["status_code"] = response.status_code
                    results["headers_received"] = dict(response.headers)
                    
                    self.log(f"Response received: {response.status_code}", "SUCCESS")
                    headers_json = json.dumps(dict(response.headers), indent=2)
                    self.log(f"Headers: {headers_json}", "INFO")
                    
                    if response.status_code != 200:
                        # Try to read error response
                        error_content = await response.aread()
                        results["error"] = error_content.decode('utf-8')
                        self.log(f"Error response: {results['error']}", "ERROR")
                        return results
                    
                    # Start consuming the stream
                    self.log("Starting to consume stream...", "INFO")
                    
                    disconnect_task = None
                    if simulate_disconnect:
                        # Schedule disconnection
                        async def disconnect_after_delay():
                            await asyncio.sleep(disconnect_after_seconds)
                            self.log(
                                f"Simulating client disconnect after "
                                f"{disconnect_after_seconds}s",
                                "WARNING",
                            )
                            results["disconnected"] = True
                            raise asyncio.CancelledError("Simulated client disconnect")
                        
                        disconnect_task = asyncio.create_task(disconnect_after_delay())
                    
                    try:
                        async for line in response.aiter_lines():
                            if not line:
                                continue
                            
                            if results["first_chunk_time"] is None:
                                results["first_chunk_time"] = time.time() - start_time
                                t = results["first_chunk_time"]
                                self.log(
                                    f"First chunk received after {t:.3f}s",
                                    "SUCCESS",
                                )
                            
                            results["chunks_received"] += 1
                            
                            # Parse SSE format
                            if line.startswith("data: "):
                                data = line[6:]  # Remove "data: " prefix
                                if data == "[DONE]":
                                    self.log("Stream completed with [DONE]", "SUCCESS")
                                    results["content"].append("[DONE]")
                                    break
                                else:
                                    try:
                                        chunk_data = json.loads(data)
                                        results["content"].append(chunk_data)
                                        
                                        # Extract content for logging
                                        content = ""
                                        if "choices" in chunk_data:
                                            for choice in chunk_data["choices"]:
                                                if (
                                                    "delta" in choice
                                                    and "content" in choice["delta"]
                                                ):
                                                    content = choice["delta"]["content"]
                                                elif "text" in choice:
                                                    content = choice["text"]
                                        
                                        if content:
                                            self.log(f"Chunk {results['chunks_received']}: {repr(content)}", "INFO")
                                        
                                        # Check for finish_reason
                                        if "choices" in chunk_data:
                                            for choice in chunk_data["choices"]:
                                                if (
                                                    "finish_reason" in choice
                                                    and choice["finish_reason"]
                                                ):
                                                    self.log(f"Stream finished with reason: {choice['finish_reason']}", "SUCCESS")
                                                    
                                    except json.JSONDecodeError as e:
                                        self.log(f"Failed to parse chunk:"
                                            f"{data[:100]} - {e}",
                                            "ERROR",
                                        )
                            
                    except asyncio.CancelledError:
                        if simulate_disconnect:
                            self.log("Client disconnected as scheduled", "WARNING")
                        else:
                            raise
                    finally:
                        if disconnect_task and not disconnect_task.done():
                            disconnect_task.cancel()
                    
        except httpx.TimeoutException as e:
            results["error"] = f"Request timed out: {e}"
            self.log(results["error"], "ERROR")
        except httpx.ConnectError as e:
            results["error"] = f"Connection failed: {e}"
            self.log(results["error"], "ERROR")
        except Exception as e:
            results["error"] = f"Unexpected error: {type(e).__name__}: {e}"
            self.log(results["error"], "ERROR")
        
        results["total_duration"] = time.time() - start_time
        
        # Summary
        self.log("\n=== Test Summary ===", "INFO")
        self.log(f"Total duration: {results['total_duration']:.3f}s", "INFO")
        self.log(f"Request sent: {results['request_sent']}", "INFO")
        self.log(f"Response received: {results['response_received']}", "INFO")
        self.log(f"Status code: {results['status_code']}", "INFO")
        if results["first_chunk_time"]:
            self.log(
                f"First chunk time: {results['first_chunk_time']:.3f}s",
                "INFO",
            )
        else:
            self.log("No chunks received", "INFO")
        self.log(f"Chunks received: {results['chunks_received']}", "INFO")
        self.log(f"Disconnected: {results['disconnected']}", "INFO")
        if results['error']:
            self.log(f"Error: {results['error']}", "ERROR")
        
        return results
    
    async def test_concurrent_requests(
        self,
        model: str,
        messages: List[Dict[str, str]],
        concurrent_count: int = 3,
        timeout: Optional[float] = None
    ):
        """Test multiple concurrent streaming requests."""
        self.log(f"\n=== Testing {concurrent_count} concurrent requests ===", "INFO")
        
        # Create tasks for concurrent requests
        tasks = []
        for i in range(concurrent_count):
            task = asyncio.create_task(
                self.test_streaming_request(
                    model=model,
                    messages=messages,
                    timeout=timeout
                )
            )
            tasks.append(task)
            self.log(f"Started request {i+1}/{concurrent_count}", "INFO")
        
        # Wait for all to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Analyze results
        successful = sum(1 for r in results if isinstance(r, dict) and
            r.get("chunks_received", 0) > 0)
        failed = sum(1 for r in results if isinstance(r, Exception) or (isinstance(r,
            dict) and r.get("error")))
        
        self.log(f"\n=== Concurrent Test Results ===", "INFO")
        success_level = "SUCCESS" if successful == concurrent_count else "WARNING"
        self.log(
            f"Successful: {successful}/{concurrent_count}",
            success_level,
        )
        fail_level = "ERROR" if failed > 0 else "INFO"
        self.log(f"Failed: {failed}/{concurrent_count}", fail_level)
        
        # Check for response uniqueness
        all_content = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.log(
                    f"Request {i+1}: EXCEPTION - "
                    f"{type(result).__name__}: {result}",
                    "ERROR",
                )
            elif result.get("error"):
                self.log(f"Request {i+1}: ERROR - {result['error']}", "ERROR")
            elif result.get("chunks_received", 0) > 0:
                dur = result["total_duration"]
                n_chunks = result["chunks_received"]
                self.log(
                    f"Request {i+1}: SUCCESS - {n_chunks} chunks in {dur:.3f}s",
                    "SUCCESS",
                )
                # Collect content for uniqueness check
                content_text = ""
                for chunk in result.get("content", []):
                    if isinstance(chunk, dict) and "choices" in chunk:
                        for choice in chunk["choices"]:
                            if "delta" in choice and "content" in choice["delta"]:
                                content_text += choice["delta"]["content"] or ""
                if content_text:
                    all_content.append(content_text)
                    self.log(f"  Content preview: {repr(content_text[:50])}", "INFO")
            else:
                self.log(f"Request {i+1}: NO CHUNKS RECEIVED", "WARNING")
        
        # Check uniqueness if temperature > 0
        if len(all_content) >= 2:
            unique_count = len(set(all_content))
            self.log(f"\n=== Content Uniqueness Check ===", "INFO")
            self.log(f"Unique responses: {unique_count}/{len(all_content)}", 
                    "SUCCESS" if unique_count == len(all_content) else "WARNING")
            if unique_count < len(all_content):
                self.log("⚠️ Some responses are IDENTICAL - this may indicate"
                    "deterministic mode or caching", "WARNING")


async def main():
    parser = argparse.ArgumentParser(description="Debug streaming response issues")
    parser.add_argument("--url", default="http://localhost:9999", help="Stargate URL")
    parser.add_argument("--model", required=True, help="Model to test")
    parser.add_argument(
        "--message",
        default="Hello, please respond with a short greeting.",
        help="Test message",
    )
    parser.add_argument("--timeout", type=float, help="Request timeout in seconds")
    parser.add_argument(
        "--concurrent",
        type=int,
        default=1,
        help="Number of concurrent requests",
    )
    parser.add_argument(
        "--simulate-disconnect",
        action="store_true",
        help="Simulate client disconnect",
    )
    parser.add_argument(
        "--disconnect-after",
        type=float,
        default=2.0,
        help="Seconds before disconnect",
    )
    
    args = parser.parse_args()
    
    client = StreamingDebugClient(args.url)
    messages = [{"role": "user", "content": args.message}]
    
    if args.concurrent > 1:
        await client.test_concurrent_requests(
            model=args.model,
            messages=messages,
            concurrent_count=args.concurrent,
            timeout=args.timeout
        )
    else:
        await client.test_streaming_request(
            model=args.model,
            messages=messages,
            timeout=args.timeout,
            simulate_disconnect=args.simulate_disconnect,
            disconnect_after_seconds=args.disconnect_after
        )


if __name__ == "__main__":
    asyncio.run(main())
