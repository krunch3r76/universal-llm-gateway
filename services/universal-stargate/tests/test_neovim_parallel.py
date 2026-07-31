#!/usr/bin/env python3
"""
Test script that mimics Neovim plugin's parallel request pattern.

This reproduces the exact behavior described in the client documentation:
- 3 identical parallel requests
- OpenAI-compatible streaming format
- Temperature 0.3 for response variation
"""
import asyncio
import json
import sys
import httpx
from datetime import datetime

# Test configuration matching Neovim plugin
TEST_CONFIG = {
    "base_url": "http://localhost:9999",
    "model": "cursorcore-qw2-5-1-5b-q4-k-m-32768-cpu",  # Or any model
    "messages": [
        {
            "role": "user", 
            "content": "Complete this code:\nfunction test(\n<CURSOR>\n return"
                "true\nend"
        }
    ],
    "stream": True,
    "max_tokens": 100,
    "temperature": 0.3
}

def log(message: str, level: str = "INFO"):
    """Simple logging with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] {level}: {message}")

async def consume_stream(client: httpx.AsyncClient, request_num: int) -> dict:
    """
    Consume a single streaming request, mimicking Neovim client behavior.
    
    Returns:
        Dictionary with results matching what Neovim expects
    """
    url = f"{TEST_CONFIG['base_url']}/v1/chat/completions"
    payload = {
        "model": TEST_CONFIG["model"],
        "messages": TEST_CONFIG["messages"],
        "stream": TEST_CONFIG["stream"],
        "max_tokens": TEST_CONFIG["max_tokens"],
        "temperature": TEST_CONFIG["temperature"]
    }
    
    log(f"Request {request_num}: Starting", "INFO")
    start_time = asyncio.get_event_loop().time()
    
    result = {
        "request_num": request_num,
        "success": False,
        "chunks": [],
        "accumulated_content": "",
        "error": None,
        "duration": 0
    }
    
    try:
        async with client.stream('POST', url, json=payload) as response:
            if response.status_code != 200:
                result["error"] = f"Status {response.status_code}"
                error_body = await response.aread()
                log(
                    f"Request {request_num}: ERROR - {result['error']}: "
                    f"{error_body.decode()}",
                    "ERROR",
                )
                return result
            
            log(f"Request {request_num}: Got 200 response, consuming stream", "SUCCESS")
            
            # Parse SSE stream exactly as Neovim does
            async for line in response.aiter_lines():
                if not line:
                    continue
                
                # Skip keepalive lines (starting with ':')
                if line.startswith(":"):
                    continue
                
                # Handle [DONE] marker
                if line == "data: [DONE]":
                    log(f"Request {request_num}: Received [DONE]", "INFO")
                    result["success"] = True
                    break
                
                # Parse data lines
                if line.startswith("data: "):
                    try:
                        json_str = line[6:]  # Remove "data: " prefix
                        chunk_data = json.loads(json_str)
                        
                        # Extract content (OpenAI format)
                        if "choices" in chunk_data and chunk_data["choices"]:
                            choice = chunk_data["choices"][0]
                            if "delta" in choice and "content" in choice["delta"]:
                                content = choice["delta"]["content"]
                                if content:
                                    result["chunks"].append(content)
                                    result["accumulated_content"] += content
                                    log(f"Request {request_num}: Chunk #{len(result['chunks'])}: {repr(content)}", "DEBUG")
                    
                    except json.JSONDecodeError as e:
                        log(
                            f"Request {request_num}: Failed to parse "
                            f"JSON: {line} - {e}",
                            "ERROR",
                        )
                        result["error"] = f"JSON parse error: {e}"
    
    except Exception as e:
        result["error"] = f"Exception: {type(e).__name__}: {e}"
        log(f"Request {request_num}: {result['error']}", "ERROR")
    
    result["duration"] = asyncio.get_event_loop().time() - start_time
    return result

async def test_parallel_requests():
    """
    Test 3 parallel requests exactly as Neovim plugin does.
    """
    log("=== Starting Neovim-style Parallel Request Test ===", "INFO")
    log(f"Model: {TEST_CONFIG['model']}", "INFO")
    log(f"Temperature: {TEST_CONFIG['temperature']}", "INFO")
    log(f"Max tokens: {TEST_CONFIG['max_tokens']}", "INFO")
    
    # Create client with reasonable timeout
    timeout = httpx.Timeout(60.0)  # 60 second timeout
    async with httpx.AsyncClient(timeout=timeout) as client:
        # Start 3 identical requests in parallel
        tasks = []
        for i in range(1, 4):
            task = asyncio.create_task(consume_stream(client, i))
            tasks.append(task)
        
        log("Started 3 parallel requests", "INFO")
        
        # Wait for all to complete
        results = await asyncio.gather(*tasks)
    
    # Analyze results
    log("\n=== Results Analysis ===", "INFO")
    
    successful = sum(1 for r in results if r["success"])
    level = "SUCCESS" if successful == 3 else "WARNING"
    log(f"Successful requests: {successful}/3", level)
    
    # Check each result
    for result in results:
        req_num = result["request_num"]
        if result["success"]:
            chunks = len(result["chunks"])
            content_len = len(result["accumulated_content"])
            dur = result["duration"]
            log(
                f"Request {req_num}: SUCCESS - {chunks} chunks, "
                f"{content_len} chars in {dur:.2f}s",
                "SUCCESS",
            )
            preview = repr(result["accumulated_content"][:50])
            log(f"  Content preview: {preview}", "INFO")
        else:
            log(f"Request {req_num}: FAILED - {result['error']}", "ERROR")
    
    # Check for unique responses (expected with temperature=0.3)
    contents = [r["accumulated_content"] for r in results if r["success"]]
    if len(contents) >= 2:
        unique_contents = set(contents)
        log(f"\nUnique responses: {len(unique_contents)}/{len(contents)}", "INFO")
        
        if len(unique_contents) < len(contents):
            log("⚠️ WARNING: Some responses are IDENTICAL!", "WARNING")
            log("This suggests deterministic mode or caching when temperature=0.3"
                "should give variation", "WARNING")
            
            # Show which are identical
            for i, content in enumerate(contents):
                for j in range(i + 1, len(contents)):
                    if content == contents[j]:
                        log(f"  Response {i+1} == Response {j+1}", "WARNING")
        else:
            log("✓ All responses are different (expected"
                "with temperature=0.3)", "SUCCESS")
    
    # Mimic Neovim debug output format
    log("\n=== Neovim-style Debug Summary ===", "INFO")
    for i, result in enumerate(results):
        if result["success"]:
            n_chunks = len(result["chunks"])
            log(f"[naive DEBUG] Request {i+1}: Total chunks: {n_chunks}", "INFO")
            acc = result["accumulated_content"]
            log(
                f"[naive DEBUG] Request {i+1}: Accumulated ({len(acc)} chars): "
                f"{acc[:50]}...",
                "INFO",
            )
    
    if successful == 3:
        log(
            f"[naive DEBUG] Formatted {successful} items from "
            f"{successful} suggestions",
            "SUCCESS",
        )
        for i, result in enumerate(results):
            if result["success"]:
                # Simulate how Neovim would show the completion items
                preview = result["accumulated_content"][:30].replace('\n', '\\n')
                n = len(result["accumulated_content"])
                log(f"  Item #{i+1}: word='{preview}...' (len={n})", "INFO")
    else:
        log(f"[naive DEBUG] Only {successful} requests succeeded!", "ERROR")

async def main():
    """Main entry point."""
    # Check if model is provided as argument
    if len(sys.argv) > 1:
        TEST_CONFIG["model"] = sys.argv[1]
    
    await test_parallel_requests()

if __name__ == "__main__":
    asyncio.run(main())
