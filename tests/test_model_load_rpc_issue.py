#!/usr/bin/env python3
"""
Test script to verify model load state synchronization issue is fixed.

This test will:
1. Start fresh (no models loaded)
2. Trigger a model load
3. Monitor logs to ensure the worker actually receives and processes the load_model RPC
4. Verify that the Gateway only reports success after the model is actually loaded
"""

import asyncio
import time
import httpx
import subprocess
import os
import sys


async def test_model_load_synchronization():
    """Test that model load state is properly synchronized between Gateway and Worker."""
    
    print("=" * 80)
    print("Model Load State Synchronization Test")
    print("=" * 80)
    
    # Configuration
    gateway_url = "http://localhost:9998"
    model_id = "gpt-oss-20b-mxfp4-131072"  # Large model with 131K context
    worker_log = f"/tmp/llm_gateway/worker-logs/{model_id}.log"
    gateway_log = "/tmp/logs/universal-llm-gateway/gateway.log"
    
    print(f"\n📋 Test Configuration:")
    print(f"   Gateway URL: {gateway_url}")
    print(f"   Model ID: {model_id}")
    print(f"   Worker Log: {worker_log}")
    print(f"   Gateway Log: {gateway_log}")
    
    # Step 1: Clean up any existing processes
    print(f"\n🧹 Step 1: Cleaning up existing processes...")
    subprocess.run(["pkill", "-f", f"worker.py.*{model_id}"], stderr=subprocess.DEVNULL)
    time.sleep(2)
    
    # Clear log files for fresh test
    if os.path.exists(worker_log):
        os.remove(worker_log)
    
    # Step 2: Start monitoring logs
    print(f"\n👀 Step 2: Starting log monitoring...")
    
    # Function to check if specific log lines appear
    def check_worker_logs_for_patterns():
        """Check worker logs for expected patterns."""
        if not os.path.exists(worker_log):
            return {"file_exists": False}
        
        with open(worker_log, 'r') as f:
            content = f.read()
        
        patterns = {
            "process_command_received": "Processing supervisor command:" in content,
            "load_model_command": "Processing supervisor command: load_model" in content,
            "load_model_rpc": "Load model RPC received" in content,
            "loading_gguf": "Loading gguf model from" in content or "Loading GGUF model with arguments:" in content,
            "model_loaded": "Model engine loaded successfully" in content or "Model loaded successfully" in content,
            "process_command_result": "Command load_model completed successfully" in content,
        }
        
        patterns["file_exists"] = True
        patterns["total_lines"] = content.count('\n')
        
        return patterns
    
    # Step 3: Trigger model load
    print(f"\n🚀 Step 3: Triggering model load...")
    
    async with httpx.AsyncClient(timeout=300.0) as client:
        load_start_time = time.time()
        
        try:
            response = await client.post(
                f"{gateway_url}/api/v1/models/{model_id}/load",
                json={},
                timeout=300.0
            )
            
            load_end_time = time.time()
            load_duration = load_end_time - load_start_time
            
            print(f"\n📊 Load Response:")
            print(f"   Status Code: {response.status_code}")
            print(f"   Duration: {load_duration:.1f} seconds")
            print(f"   Response: {response.json()}")
            
        except Exception as e:
            print(f"\n❌ Error loading model: {e}")
            return
    
    # Step 4: Check worker logs
    print(f"\n🔍 Step 4: Checking worker logs...")
    
    # Give logs a moment to flush
    await asyncio.sleep(2)
    
    patterns = check_worker_logs_for_patterns()
    
    print(f"\n📋 Worker Log Analysis:")
    for pattern, found in patterns.items():
        if pattern == "file_exists":
            print(f"   Log file exists: {'✅' if found else '❌'}")
        elif pattern == "total_lines":
            print(f"   Total log lines: {found}")
        else:
            print(f"   {pattern}: {'✅ Found' if found else '❌ NOT FOUND'}")
    
    # Step 5: Verify model is actually loaded
    print(f"\n🧪 Step 5: Testing inference to verify model is loaded...")
    
    async with httpx.AsyncClient() as client:
        try:
            inference_start = time.time()
            
            # Test with streaming to see immediate response
            response = await client.post(
                f"{gateway_url}/v1/chat/completions",
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": "Hello"}],
                    "max_tokens": 10,
                    "stream": True
                },
                timeout=30.0
            )
            
            inference_time = time.time() - inference_start
            
            print(f"\n📊 Inference Test:")
            print(f"   Status Code: {response.status_code}")
            print(f"   Response Time: {inference_time:.1f} seconds")
            
            if response.status_code == 200:
                # Read first chunk
                first_chunk = None
                for line in response.iter_lines():
                    if line and line.startswith(b"data: "):
                        first_chunk = line
                        break
                
                if first_chunk:
                    print(f"   First Chunk: {first_chunk[:100]}...")
                    if inference_time < 5:
                        print(f"   ✅ Model responded quickly - appears to be loaded!")
                    else:
                        print(f"   ⚠️  Model took {inference_time:.1f}s to respond - might have loaded during inference")
            
        except Exception as e:
            print(f"\n❌ Inference error: {e}")
    
    # Step 6: Analysis
    print(f"\n📊 Test Analysis:")
    
    issues = []
    
    if load_duration < 10:
        issues.append(f"Model reported as loaded in only {load_duration:.1f} seconds - too fast for a 20B model!")
    
    if not patterns.get("process_command_received"):
        issues.append("Worker never received the process_command RPC call")
    
    if not patterns.get("load_model_rpc"):
        issues.append("Worker never logged receiving load_model RPC")
    
    if not patterns.get("loading_gguf"):
        issues.append("Worker never logged loading the GGUF model")
    
    if issues:
        print(f"\n❌ Issues Found:")
        for issue in issues:
            print(f"   - {issue}")
        print(f"\n🔧 The RPC communication is likely broken!")
    else:
        print(f"\n✅ All checks passed! Model load synchronization appears to be working correctly.")
    
    # Print summary
    print(f"\n" + "=" * 80)
    print(f"Test Complete")
    print(f"=" * 80)


if __name__ == "__main__":
    asyncio.run(test_model_load_synchronization())
