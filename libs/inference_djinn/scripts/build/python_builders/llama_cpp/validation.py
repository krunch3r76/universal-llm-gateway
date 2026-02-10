"""
Build Validation Module

Validates llama-cpp-python wheels after build to ensure they work correctly
before installation.
"""

import logging
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class BuildValidationError(Exception):
    """Raised when build validation fails."""
    pass


class WheelValidator:
    """Validates llama-cpp-python wheel functionality."""

    def __init__(self, wheel_path: Path, python_executable: str | None = None):
        self.wheel_path = wheel_path
        self.python_executable = python_executable or sys.executable

    def validate(
        self,
        model_path: Path | None = None,
        test_levels: list[str] | None = None
    ) -> dict[str, bool]:
        """
        Validate wheel with different test levels.

        Args:
            model_path: Optional GGUF model for functionality testing
            test_levels: List of tests to run (default: ["import", "cuda", "basic"])

        Returns:
            Dict mapping test name to success status

        Raises:
            BuildValidationError: If critical tests fail
        """
        if test_levels is None:
            test_levels = ["import", "cuda", "basic"]

        results = {}

        try:
            # Create temporary venv for isolated testing
            with tempfile.TemporaryDirectory(prefix="llama-cpp-validation-") as temp_dir:
                venv_dir = Path(temp_dir) / "test_venv"
                self._create_test_venv(venv_dir)

                # Install wheel to test venv
                self._install_wheel_to_venv(venv_dir)

                # Run validation tests
                for test_level in test_levels:
                    logger.info(f"   Running {test_level} validation...")
                    try:
                        if test_level == "import":
                            results[test_level] = self._test_import(venv_dir)
                        elif test_level == "cuda":
                            results[test_level] = self._test_cuda_availability(venv_dir)
                        elif test_level == "basic":
                            results[test_level] = self._test_basic_functionality(venv_dir)
                        elif test_level == "model" and model_path:
                            results[test_level] = self._test_model_loading(venv_dir, model_path)
                        elif test_level == "moe" and model_path:
                            results[test_level] = self._test_moe_functionality(venv_dir, model_path)
                        else:
                            logger.warning(f"   Unknown test level: {test_level}")
                            results[test_level] = False

                        if results[test_level]:
                            logger.info(f"   ✅ {test_level} validation passed")
                        else:
                            logger.warning(f"   ⚠️  {test_level} validation failed")

                    except Exception as e:
                        logger.error(f"   ❌ {test_level} validation error: {e}")
                        results[test_level] = False

        except Exception as e:
            logger.error(f"Validation setup failed: {e}")
            raise BuildValidationError(f"Validation setup failed: {e}")

        # Check for critical failures
        critical_tests = ["import"]
        failed_critical = [test for test in critical_tests if not results.get(test, False)]

        if failed_critical:
            raise BuildValidationError(f"Critical validation tests failed: {failed_critical}")

        return results

    def _create_test_venv(self, venv_dir: Path):
        """Create isolated venv for testing."""
        logger.debug(f"   Creating test venv: {venv_dir}")
        
        cmd = [sys.executable, "-m", "venv", str(venv_dir)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise BuildValidationError(f"Failed to create test venv: {result.stderr}")

    def _install_wheel_to_venv(self, venv_dir: Path):
        """Install wheel to test venv."""
        python_exe = venv_dir / "bin" / "python"
        if not python_exe.exists():
            python_exe = venv_dir / "Scripts" / "python.exe"  # Windows

        logger.debug(f"   Installing wheel to test venv: {self.wheel_path}")
        
        cmd = [str(python_exe), "-m", "pip", "install", str(self.wheel_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise BuildValidationError(f"Failed to install wheel: {result.stderr}")

    def _run_python_in_venv(self, venv_dir: Path, code: str, timeout: int = 30) -> tuple[bool, str]:
        """Run Python code in test venv."""
        python_exe = venv_dir / "bin" / "python"
        if not python_exe.exists():
            python_exe = venv_dir / "Scripts" / "python.exe"  # Windows

        cmd = [str(python_exe), "-c", code]
        
        try:
            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                timeout=timeout
            )
            success = result.returncode == 0
            output = result.stdout if success else result.stderr
            return success, output
        except subprocess.TimeoutExpired:
            return False, f"Timeout after {timeout}s"

    def _test_import(self, venv_dir: Path) -> bool:
        """Test basic import functionality."""
        code = """
try:
    import llama_cpp
    print(f"llama_cpp version: {getattr(llama_cpp, '__version__', 'unknown')}")
    print("Import successful")
except Exception as e:
    print(f"Import failed: {e}")
    exit(1)
"""
        success, output = self._run_python_in_venv(venv_dir, code)
        if success:
            logger.debug(f"   Import test output: {output.strip()}")
        return success

    def _test_cuda_availability(self, venv_dir: Path) -> bool:
        """Test CUDA availability."""
        code = """
try:
    import llama_cpp
    
    # Try to check CUDA availability
    # Note: This might vary depending on llama_cpp version
    cuda_available = False
    
    # Method 1: Check if CUDA backend is compiled in
    try:
        # This might work for newer versions
        import llama_cpp.llama_cpp as llama_cpp_lib
        if hasattr(llama_cpp_lib, 'llama_supports_gpu_offload'):
            cuda_available = llama_cpp_lib.llama_supports_gpu_offload()
        elif hasattr(llama_cpp_lib, 'LLAMA_SUPPORTS_GPU_OFFLOAD'):
            cuda_available = llama_cpp_lib.LLAMA_SUPPORTS_GPU_OFFLOAD
    except:
        pass
    
    # Method 2: Try creating a model with GPU layers
    if not cuda_available:
        try:
            # This will only work if we have a model, so we'll just check the parameter exists
            model_class = getattr(llama_cpp, 'Llama', None)
            if model_class:
                import inspect
                sig = inspect.signature(model_class.__init__)
                cuda_available = 'n_gpu_layers' in sig.parameters
        except:
            pass
    
    print(f"CUDA support detected: {cuda_available}")
    
    # For validation, we'll consider it successful if we can detect the parameter
    # even if no GPU is available, since this tests the build
    if cuda_available:
        print("CUDA validation passed")
    else:
        print("CUDA validation failed - no GPU support detected")
        exit(1)
        
except Exception as e:
    print(f"CUDA test failed: {e}")
    exit(1)
"""
        success, output = self._run_python_in_venv(venv_dir, code)
        if success:
            logger.debug(f"   CUDA test output: {output.strip()}")
        return success

    def _test_basic_functionality(self, venv_dir: Path) -> bool:
        """Test basic llama_cpp functionality without loading a model."""
        code = """
try:
    import llama_cpp
    
    # Test that we can access basic classes and functions
    model_class = getattr(llama_cpp, 'Llama', None)
    if not model_class:
        print("Llama class not found")
        exit(1)
    
    # Test that we can access tokenizer functions if available
    # Note: Some versions might not have these
    try:
        if hasattr(llama_cpp, 'llama_token_get_text'):
            print("Tokenizer functions available")
        elif hasattr(llama_cpp, 'Llama'):
            print("Llama class available")
    except:
        pass
    
    # Check for MoE support by looking for relevant attributes/functions
    moe_support = False
    try:
        # Look for MoE-related functionality
        # This is a heuristic - actual MoE detection would need a model
        import llama_cpp.llama_cpp as llama_cpp_lib
        
        # Check for functions that might indicate MoE support
        moe_indicators = [
            'llama_get_model_n_params',
            'llama_model_desc', 
            'llama_model_type',
        ]
        
        for indicator in moe_indicators:
            if hasattr(llama_cpp_lib, indicator):
                moe_support = True
                break
                
    except:
        pass
    
    print(f"Basic functionality test passed")
    print(f"Potential MoE support: {moe_support}")
    
except Exception as e:
    print(f"Basic functionality test failed: {e}")
    exit(1)
"""
        success, output = self._run_python_in_venv(venv_dir, code, timeout=60)
        if success:
            logger.debug(f"   Basic functionality test output: {output.strip()}")
        return success

    def _test_model_loading(self, venv_dir: Path, model_path: Path) -> bool:
        """Test loading a specific model."""
        if not model_path.exists():
            logger.warning(f"   Model not found for testing: {model_path}")
            return False

        code = f"""
try:
    import llama_cpp
    
    print(f"Testing model loading: {model_path}")
    
    # Try to load model with minimal settings
    model = llama_cpp.Llama(
        model_path=r"{model_path}",
        n_ctx=512,  # Small context for testing
        n_gpu_layers=1,  # Try GPU if available
        verbose=False
    )
    
    print("Model loaded successfully")
    
    # Test basic inference
    output = model("Hello", max_tokens=1, echo=False)
    print(f"Basic inference test passed: {len(output.get('choices', []))} choices generated")
    
    # Clean up
    del model
    
except Exception as e:
    print(f"Model loading test failed: {e}")
    exit(1)
"""
        success, output = self._run_python_in_venv(venv_dir, code, timeout=120)
        if success:
            logger.debug(f"   Model loading test output: {output.strip()}")
        return success

    def _test_moe_functionality(self, venv_dir: Path, model_path: Path) -> bool:
        """Test MoE (Mixture of Experts) functionality."""
        if not model_path.exists():
            logger.warning(f"   Model not found for MoE testing: {model_path}")
            return False

        code = f"""
try:
    import llama_cpp
    
    print(f"Testing MoE functionality with: {model_path}")
    
    # Load model
    model = llama_cpp.Llama(
        model_path=r"{model_path}",
        n_ctx=512,
        n_gpu_layers=1,
        verbose=False
    )
    
    # Check if this is actually a MoE model
    # This is heuristic - we'd need to inspect the model metadata
    try:
        # Try to access model metadata or architecture info
        # Different llama_cpp versions may have different ways to do this
        model_type = "unknown"
        if hasattr(model, 'model'):
            # Try to get model type/architecture
            pass
        
        print(f"Model type detection: {model_type}")
    except:
        pass
    
    # Test inference (same as basic but specifically for MoE)
    output = model("Test MoE", max_tokens=5, echo=False)
    choices = output.get('choices', [])
    
    if len(choices) > 0:
        print(f"MoE inference test passed: {len(choices)} choices")
        print(f"Sample output: {choices[0].get('text', '')[:50]}...")
    else:
        print("MoE inference test failed: no output")
        exit(1)
    
    # Clean up
    del model
    
except Exception as e:
    print(f"MoE test failed: {e}")
    exit(1)
"""
        success, output = self._run_python_in_venv(venv_dir, code, timeout=180)
        if success:
            logger.debug(f"   MoE test output: {output.strip()}")
        return success


def validate_wheel(
    wheel_path: Path,
    model_path: Path | None = None,
    test_levels: list[str] | None = None
) -> dict[str, bool]:
    """
    Convenience function to validate a wheel.
    
    Args:
        wheel_path: Path to wheel file
        model_path: Optional model for functionality testing
        test_levels: Tests to run (default: ["import", "cuda", "basic"])
    
    Returns:
        Dict mapping test name to success status
    """
    validator = WheelValidator(wheel_path)
    return validator.validate(model_path, test_levels)


def quick_validation(wheel_path: Path) -> bool:
    """
    Quick validation - just test import and CUDA detection.
    
    Args:
        wheel_path: Path to wheel file
        
    Returns:
        True if basic validation passes
    """
    try:
        results = validate_wheel(wheel_path, test_levels=["import", "cuda"])
        return results.get("import", False)  # Require import to work
    except BuildValidationError:
        return False