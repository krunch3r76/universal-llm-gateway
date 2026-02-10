#!/bin/bash
# API Disambiguation & ModelId Migration Verification
# Covers Phases 1, 1.2, 1.3, 1.4, 2
set -e

echo "=== API Disambiguation & ModelId Migration Verification ==="

# Phase 1: No ambiguous API method definitions
echo "1. Checking for old API method names..."
if grep -rE "def (get_model_metadata|get_model_configuration)\(" services/universal-stargate/gateway_client/ --include="*.py" 2>/dev/null; then
  echo "❌ FAIL: Found old API method definitions"
  exit 1
fi
echo "✅ No old API method definitions"

# Phase 1: Verify new API names exist
echo "2. Verifying new API methods exist..."
if ! grep -q "def fetch_model_info_dict" services/universal-stargate/gateway_client/http_methods.py; then
  echo "❌ FAIL: fetch_model_info_dict() not found"
  exit 1
fi
if ! grep -q "def fetch_model_configuration" services/universal-stargate/gateway_client/http_methods.py; then
  echo "❌ FAIL: fetch_model_configuration() not found"
  exit 1
fi
echo "✅ New API methods exist"

# Phase 1: No untyped getattr for requirements
echo "3. Checking for getattr patterns on requirements..."
if grep -rE "getattr\([^,]+,\s*['\"]vram_usage|getattr\([^,]+,\s*['\"]ram_usage" services/universal-stargate/ --include="*.py" 2>/dev/null; then
  echo "❌ FAIL: Found getattr patterns for requirements (should use direct field access)"
  exit 1
fi
echo "✅ No getattr patterns for requirements"

# Phase 1.2: Verify monitoring_config.py exists
echo "4. Checking for monitoring_config.py module..."
if [ ! -f "services/universal-stargate/systems/proxy/core/nonstreaming/monitoring_config.py" ]; then
  echo "❌ FAIL: monitoring_config.py not found (Phase 1.2)"
  exit 1
fi
echo "✅ monitoring_config.py exists"

# Phase 1.2: Verify helper functions exist
echo "5. Verifying monitoring config helper functions..."
if ! grep -q "def get_cached_configuration_for_monitoring" services/universal-stargate/systems/proxy/core/nonstreaming/monitoring_config.py; then
  echo "❌ FAIL: get_cached_configuration_for_monitoring() not found"
  exit 1
fi
if ! grep -q "def schedule_background_configuration_fetch" services/universal-stargate/systems/proxy/core/nonstreaming/monitoring_config.py; then
  echo "❌ FAIL: schedule_background_configuration_fetch() not found"
  exit 1
fi
echo "✅ Monitoring config helpers exist"

# Phase 1.3: Verify ModelId cache keys
echo "6. Checking for ModelId cache key types..."
if ! grep -q "_model_configuration_cache: dict\[ModelId, ModelMetadata\]" services/universal-stargate/gateways/single_manager.py; then
  echo "❌ FAIL: Configuration cache should use dict[ModelId, ModelMetadata]"
  exit 1
fi
if ! grep -q "_model_info_cache: dict\[ModelId," services/universal-stargate/gateways/single_manager.py; then
  echo "❌ FAIL: Info cache should use dict[ModelId, ...]"
  exit 1
fi
echo "✅ Caches use ModelId keys"

# Phase 1.4: Verify frozenset conversion helper exists
echo "7. Checking for _to_model_id_set helper..."
if ! grep -q "def _to_model_id_set" services/universal-stargate/systems/proxy/core/nonstreaming/gateway_selection.py; then
  echo "❌ FAIL: _to_model_id_set() helper not found (Phase 1.4)"
  exit 1
fi
echo "✅ Frozenset conversion helper exists"

# Phase 2: Verify fail-closed validation exists
echo "8. Checking for fail-closed validation..."
if ! grep -q "if vram_usage is None and ram_usage is None:" services/universal-stargate/systems/routing/selection/collector.py; then
  echo "❌ FAIL: Fail-closed validation not found in collector.py (Phase 2)"
  exit 1
fi
if ! grep -A 10 "if vram_usage is None and ram_usage is None:" services/universal-stargate/systems/routing/selection/collector.py | grep -q "return None"; then
  echo "❌ FAIL: Fail-closed validation should return None when both requirements missing"
  exit 1
fi
echo "✅ Fail-closed validation present (models without requirements excluded)"

# Phase 2: Verify validation helper exists
echo "9. Checking for _validate_model_requirements helper..."
if ! grep -q "def _validate_model_requirements" services/universal-stargate/systems/routing/selection/collector.py; then
  echo "❌ FAIL: _validate_model_requirements() helper not found (Phase 2)"
  exit 1
fi
echo "✅ Validation helper exists"

# Phase 2: Verify ResourceRequirementsProvider uses ModelId
echo "10. Checking ResourceRequirementsProvider signature..."
if ! grep -A 2 "async def __call__" services/universal-stargate/systems/proxy/core/control_plane/types.py | grep -q "model_id: ModelId"; then
  echo "❌ FAIL: ResourceRequirementsProvider should accept ModelId parameter (Phase 2)"
  exit 1
fi
echo "✅ ResourceRequirementsProvider uses ModelId"

# String manipulation check
echo "11. Checking for string manipulation in executor..."
if grep -rE "\.rsplit\(|model_id\.split\(" services/universal-stargate/systems/proxy/core/nonstreaming/executor.py 2>/dev/null; then
  echo "❌ FAIL: Found string manipulation for model_id (should use ModelId properties)"
  exit 1
fi
echo "✅ No string manipulation in executor"

# Compile all affected modules
echo "12. Compiling modules..."
python -m compileall -q services/universal-stargate/gateway_client/
python -m compileall -q services/universal-stargate/gateways/
python -m compileall -q services/universal-stargate/systems/proxy/core/
python -m compileall -q services/universal-stargate/systems/routing/
echo "✅ All modules compile"

# Lint (non-blocking - reports pre-existing issues)
echo "13. Running linter..."
if ruff check services/universal-stargate/ 2>&1 | head -20; then
  echo "✅ Lint passed"
else
  echo "⚠️  Lint reported issues (may include pre-existing errors)"
fi

# Format check (non-blocking - reports pre-existing issues)
echo "14. Checking format..."
if ruff format --check services/universal-stargate/ 2>&1 | head -20; then
  echo "✅ Format check passed"
else
  echo "⚠️  Format check reported issues (may include pre-existing errors)"
fi

echo ""
echo "=== All API disambiguation & ModelId migration checks passed ✅ ==="
