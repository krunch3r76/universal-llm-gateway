"""
Simplified test for model state consistency fix.

This test validates the core fix: ResourceTracker.get_loaded_models() correctly
filters models by status, excluding ERROR models from the loaded list.

Bug: WorkerController.get_active_models() was returning ALL models with workers,
including those in ERROR state.

Fix: get_active_models() now uses ResourceTracker.get_loaded_models() which
correctly filters by ModelStatus.LOADED or ModelStatus.BUSY.
"""

import pytest
import sys
from pathlib import Path

# Add gateway service to path
gateway_path = Path(__file__).parent.parent / "services" / "_universal-llm-gateway"
if str(gateway_path) not in sys.path:
    sys.path.insert(0, str(gateway_path))

from src.core.resources.tracker import ResourceTracker
from src.core.resources.types import ModelStatus


class TestResourceTrackerFiltering:
    """Test ResourceTracker status filtering logic - core of the fix."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.resource_tracker = ResourceTracker()
    
    def test_get_loaded_models_excludes_error_status(self):
        """
        Test that get_loaded_models() excludes ERROR models.
        
        This is the core fix: models that failed to load should NOT be
        reported as "loaded" in resource status.
        """
        # Register models in various states with proper transitions
        self.resource_tracker.register_model("model-loaded")
        self.resource_tracker.set_model_loading("model-loaded")
        self.resource_tracker.set_model_loaded("model-loaded")
        
        self.resource_tracker.register_model("model-error")
        self.resource_tracker.set_model_loading("model-error")
        self.resource_tracker.set_model_error("model-error", "ENGINE_ERROR: gguf initialization failed")
        
        loaded_models = self.resource_tracker.get_loaded_models()
        
        assert "model-loaded" in loaded_models, \
            "LOADED model should be in loaded_models list"
        assert "model-error" not in loaded_models, \
            "ERROR model should NOT be in loaded_models list (BUG FIX)"
    
    def test_bug_scenario_exact_reproduction(self):
        """
        Test exact bug scenario from investigation.
        
        Scenario:
        1. First model loads successfully (gpt-oss-20b-mxfp4-131072)
        2. Second model fails to load (nous-hermes-2-yi-34b-q4-k-m-4096)
        3. get_loaded_models() should return only first model
        """
        model1 = "gpt-oss-20b-mxfp4-131072"
        model2 = "nous-hermes-2-yi-34b-q4-k-m-4096"
        
        # Model 1: Successfully loaded
        self.resource_tracker.register_model(model1)
        self.resource_tracker.set_model_loading(model1)
        self.resource_tracker.set_model_loaded(model1)
        self.resource_tracker.update_model_resources(
            model_id=model1,
            vram_usage_mb=31000,
            ram_usage_mb=2000
        )
        
        # Model 2: Failed to load
        self.resource_tracker.register_model(model2)
        self.resource_tracker.set_model_loading(model2)
        self.resource_tracker.set_model_error(
            model2,
            "ENGINE_ERROR: gguf initialization failed: Failed to load GGUF model"
        )
        
        # Get loaded models (should exclude model2)
        loaded_models = self.resource_tracker.get_loaded_models()
        
        assert model1 in loaded_models, \
            f"Successfully loaded model {model1} should be in list"
        assert model2 not in loaded_models, \
            f"Failed model {model2} should NOT be in list (this was the bug)"
        assert len(loaded_models) == 1, \
            f"Expected 1 loaded model, got {len(loaded_models)}: {loaded_models}"
        
        # Verify model statuses
        model1_info = self.resource_tracker.get_model_info(model1)
        assert model1_info.status == ModelStatus.LOADED
        
        model2_info = self.resource_tracker.get_model_info(model2)
        assert model2_info.status == ModelStatus.ERROR
        
        # Verify error message is stored
        error_msg = self.resource_tracker.get_model_error(model2)
        assert error_msg is not None
        assert "gguf initialization failed" in error_msg
    
    def test_all_status_filtering(self):
        """
        Test that get_loaded_models() only returns LOADED and BUSY models.
        """
        models = {
            "model-not-loaded": ModelStatus.NOT_LOADED,
            "model-loading": ModelStatus.LOADING,
            "model-loaded": ModelStatus.LOADED,
            "model-busy": ModelStatus.BUSY,
            "model-unloading": ModelStatus.UNLOADING,
            "model-error": ModelStatus.ERROR,
        }
        
        for model_id, status in models.items():
            self.resource_tracker.register_model(model_id)
            self.resource_tracker.set_model_status(model_id, status)
        
        loaded_models = self.resource_tracker.get_loaded_models()
        
        # Only LOADED and BUSY should be returned
        assert "model-loaded" in loaded_models
        assert "model-busy" in loaded_models
        
        # All other statuses should be excluded
        assert "model-not-loaded" not in loaded_models
        assert "model-loading" not in loaded_models
        assert "model-unloading" not in loaded_models
        assert "model-error" not in loaded_models
        
        assert len(loaded_models) == 2, \
            f"Expected 2 loaded models, got {len(loaded_models)}: {loaded_models}"
    
    def test_busy_models_included_in_loaded(self):
        """
        Test that BUSY models are included in loaded models list.
        """
        # Create loaded model with proper state transitions
        self.resource_tracker.register_model("model-1")
        self.resource_tracker.set_model_loading("model-1")
        self.resource_tracker.set_model_loaded("model-1")
        
        # Mark it as busy (processing inference)
        self.resource_tracker.set_model_busy("model-1")
        
        loaded_models = self.resource_tracker.get_loaded_models()
        
        assert "model-1" in loaded_models, \
            "BUSY models should be considered 'loaded' for resource tracking"
    
    def test_transitional_states_excluded(self):
        """
        Test that transitional states (LOADING, UNLOADING) are excluded.
        """
        # Model 1: LOADING
        self.resource_tracker.register_model("model-loading")
        self.resource_tracker.set_model_loading("model-loading")
        
        # Model 2: UNLOADING
        self.resource_tracker.register_model("model-unloading")
        self.resource_tracker.set_model_loading("model-unloading")
        self.resource_tracker.set_model_loaded("model-unloading")
        self.resource_tracker.set_model_unloading("model-unloading")
        
        loaded_models = self.resource_tracker.get_loaded_models()
        
        assert "model-loading" not in loaded_models, \
            "LOADING models should not be in loaded list"
        assert "model-unloading" not in loaded_models, \
            "UNLOADING models should not be in loaded list"
    
    def test_get_busy_models_includes_transitional(self):
        """
        Test that get_busy_models() includes BUSY, LOADING, and UNLOADING.
        """
        models = {
            "model-loaded": ModelStatus.LOADED,
            "model-busy": ModelStatus.BUSY,
            "model-loading": ModelStatus.LOADING,
            "model-unloading": ModelStatus.UNLOADING,
            "model-error": ModelStatus.ERROR,
        }
        
        for model_id, status in models.items():
            self.resource_tracker.register_model(model_id)
            self.resource_tracker.set_model_status(model_id, status)
        
        busy_models = self.resource_tracker.get_busy_models()
        
        # BUSY, LOADING, UNLOADING should be included
        assert "model-busy" in busy_models
        assert "model-loading" in busy_models
        assert "model-unloading" in busy_models
        
        # LOADED and ERROR should be excluded
        assert "model-loaded" not in busy_models
        assert "model-error" not in busy_models


class TestWorkerControllerFix:
    """
    Test that WorkerController.get_active_models() uses ResourceTracker.
    
    This validates the fix at the integration point.
    """
    
    def test_get_active_models_implementation(self):
        """
        Verify that get_active_models() calls resource_tracker.get_loaded_models().
        
        This is a code inspection test - we verify the fix is in place.
        """
        # Read the source code of get_active_models
        controller_file = gateway_path / "src" / "core" / "workers" / "controller.py"
        with open(controller_file) as f:
            content = f.read()
        
        # Find the get_active_models method
        method_start = content.find("async def get_active_models")
        assert method_start != -1, "get_active_models method not found"
        
        method_end = content.find("\n    async def", method_start + 1)
        if method_end == -1:
            method_end = content.find("\n    def", method_start + 1)
        
        method_code = content[method_start:method_end]
        
        # Verify the fix is in place
        assert "resource_tracker" in method_code, \
            "get_active_models should use resource_tracker (FIX NOT APPLIED)"
        assert "get_loaded_models()" in method_code, \
            "get_active_models should call get_loaded_models() (FIX NOT APPLIED)"
        
        # Verify old broken code is NOT present
        assert "list(status.keys())" not in method_code, \
            "Old broken code (list(status.keys())) should be removed"
        
        print("✅ Fix verified: get_active_models() now uses resource_tracker.get_loaded_models()")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

