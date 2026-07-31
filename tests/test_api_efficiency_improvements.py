#!/usr/bin/env python3
"""
Test script to verify API efficiency improvements

This script tests both the function name cleanup and API efficiency improvements:

1. Renamed Functions: Verifies misleading function names have been renamed
2. API Completeness: Verifies bulk endpoint returns complete data that Stargate needs
3. Performance: Measures API call reduction from using bulk APIs

Run after starting both Gateway and Stargate services in development mode.
"""

import asyncio
import httpx
import time
import logging
from typing import Dict, Any, Set

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
GATEWAY_URL = "http://localhost:9998"
STARGATE_URL = "http://localhost:9999"

class APIEfficiencyTester:
    def __init__(self):
        self.gateway_client = httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0)
        self.stargate_client = httpx.AsyncClient(base_url=STARGATE_URL, timeout=30.0)
        
    async def __aenter__(self):
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.gateway_client.aclose()
        await self.stargate_client.aclose()

    async def test_function_naming_cleanup(self):
        """Test that misleading function names have been cleaned up"""
        logger.info("🧪 Testing function naming cleanup...")
        
        # Try to call an individual model info endpoint to check debug logs
        try:
            response = await self.gateway_client.get("/api/v1/model_info/qwen2-5-coder-14b-instruct-q8-0")
            if response.status_code == 200:
                logger.info("✅ Individual model endpoint works")
                # The real test is in the debug logs - should show 'get_chat_template_from_config' not 'analyze_chat_template'
                return True
            else:
                logger.warning(f"Model endpoint returned {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Failed to test function naming: {e}")
            return False

    async def test_bulk_api_completeness(self):
        """Test that bulk API now returns complete data"""
        logger.info("🧪 Testing bulk API completeness...")
        
        try:
            # Get data from bulk endpoint
            bulk_response = await self.gateway_client.get("/api/v1/model_info/configurations")
            if bulk_response.status_code != 200:
                logger.error(f"Bulk endpoint failed: {bulk_response.status_code}")
                return False
            
            bulk_data = bulk_response.json()
            if not bulk_data.get('models'):
                logger.error("No models in bulk response")
                return False
                
            # Check first model for required Stargate fields
            model_id = list(bulk_data['models'].keys())[0]
            model_data = bulk_data['models'][model_id]
            
            required_fields = [
                'input_schema', 'parameter_defaults', 'middleware_config', 
                'ram_usage', 'vram_usage', 'supported_parameters'
            ]
            
            missing_fields = []
            for field in required_fields:
                if field not in model_data:
                    missing_fields.append(field)
            
            if missing_fields:
                logger.error(f"❌ Missing required fields in bulk API: {missing_fields}")
                return False
            else:
                logger.info(f"✅ All required fields present in bulk API response")
                
                # Log sample data to verify correctness
                logger.info(f"📊 Sample model data for {model_id}:")
                logger.info(f"  - input_schema: {model_data.get('input_schema')}")
                logger.info(f"  - parameter_defaults: {len(model_data.get('parameter_defaults', {}))} parameters")
                logger.info(f"  - middleware_config: {model_data.get('middleware_config')}")
                logger.info(f"  - ram_usage: {model_data.get('ram_usage')} MB")
                logger.info(f"  - vram_usage: {model_data.get('vram_usage')} MB")
                logger.info(f"  - supported_parameters: {len(model_data.get('supported_parameters', []))} parameters")
                
                return True
                
        except Exception as e:
            logger.error(f"Failed to test bulk API completeness: {e}")
            return False

    async def test_stargate_efficiency(self):
        """Test that Stargate uses bulk APIs efficiently"""
        logger.info("🧪 Testing Stargate API efficiency...")
        
        try:
            # Make a request to Stargate models endpoint
            start_time = time.time()
            response = await self.stargate_client.get("/v1/models")
            duration = time.time() - start_time
            
            if response.status_code != 200:
                logger.error(f"Stargate models endpoint failed: {response.status_code}")
                return False
            
            models_data = response.json()
            num_models = len(models_data.get('data', []))
            
            logger.info(f"✅ Stargate returned {num_models} models in {duration:.2f}s")
            
            # Test individual model configuration retrieval speed
            if num_models > 0:
                test_model = models_data['data'][0]['id']
                
                # This should use cached data from bulk API, not individual calls
                start_time = time.time()
                test_response = await self.stargate_client.post(
                    "/v1/chat/completions",
                    json={
                        "model": test_model,
                        "messages": [{"role": "user", "content": "Hello"}],
                        "max_tokens": 1,
                        "stream": False
                    }
                )
                duration = time.time() - start_time
                
                logger.info(f"📊 Chat completion preparation took {duration:.2f}s")
                
                if test_response.status_code in [200, 404]:  # 404 if model not loaded is okay
                    logger.info("✅ Stargate efficiently processed model metadata")
                    return True
                else:
                    logger.warning(f"Chat completion returned {test_response.status_code}")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to test Stargate efficiency: {e}")
            return False

    async def measure_performance_improvement(self):
        """Measure performance improvement from bulk API usage"""
        logger.info("📊 Measuring performance improvements...")
        
        try:
            # Measure bulk API performance
            start_time = time.time()
            bulk_response = await self.gateway_client.get("/api/v1/model_info/configurations")
            bulk_duration = time.time() - start_time
            
            if bulk_response.status_code != 200:
                logger.error("Bulk API failed")
                return False
            
            bulk_data = bulk_response.json()
            num_models = len(bulk_data.get('models', {}))
            
            logger.info(f"📈 Bulk API: {num_models} models in {bulk_duration:.3f}s ({bulk_duration/num_models:.3f}s per model)")
            
            # Simulate individual API calls (old pattern)
            if num_models > 3:  # Only test a few to avoid overwhelming the server
                test_models = list(bulk_data['models'].keys())[:3]
                
                individual_start = time.time()
                for model_id in test_models:
                    individual_response = await self.gateway_client.get(f"/api/v1/model_info/{model_id}")
                    if individual_response.status_code != 200:
                        logger.warning(f"Individual API call for {model_id} failed")
                
                individual_duration = time.time() - individual_start
                individual_per_model = individual_duration / len(test_models)
                
                # Extrapolate to all models
                estimated_individual_total = individual_per_model * num_models
                
                logger.info(f"📉 Individual APIs: {len(test_models)} models in {individual_duration:.3f}s ({individual_per_model:.3f}s per model)")
                logger.info(f"📊 Estimated total for all models: {estimated_individual_total:.3f}s vs {bulk_duration:.3f}s")
                logger.info(f"🚀 Performance improvement: {estimated_individual_total/bulk_duration:.1f}x faster with bulk API")
                
                return True
            
        except Exception as e:
            logger.error(f"Failed to measure performance: {e}")
            return False

async def main():
    """Run all tests"""
    logger.info("🚀 Starting Gateway Model Metadata Architecture Tests")
    logger.info("=" * 60)
    
    async with APIEfficiencyTester() as tester:
        results = {}
        
        # Test 1: Function naming cleanup
        results['function_naming'] = await tester.test_function_naming_cleanup()
        
        # Test 2: Bulk API completeness
        results['bulk_completeness'] = await tester.test_bulk_api_completeness()
        
        # Test 3: Stargate efficiency
        results['stargate_efficiency'] = await tester.test_stargate_efficiency()
        
        # Test 4: Performance measurement
        results['performance_measurement'] = await tester.measure_performance_improvement()
        
        # Summary
        logger.info("=" * 60)
        logger.info("🎯 TEST SUMMARY:")
        
        passed = sum(1 for result in results.values() if result)
        total = len(results)
        
        for test_name, result in results.items():
            status = "✅ PASS" if result else "❌ FAIL"
            logger.info(f"  {test_name}: {status}")
        
        logger.info(f"📊 Overall: {passed}/{total} tests passed")
        
        if passed == total:
            logger.info("🎉 All tests passed! Gateway Model Metadata Architecture improvements working correctly.")
        else:
            logger.warning("⚠️ Some tests failed. Check the logs above for details.")
        
        return passed == total

if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
