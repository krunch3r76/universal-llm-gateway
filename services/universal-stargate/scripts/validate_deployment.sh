#!/usr/bin/env bash
#
# Deployment validation script for atomic VRAM reservation system.
#
# Validates that the system is properly configured and operational before deployment.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Validation results
TOTAL_CHECKS=0
PASSED_CHECKS=0
FAILED_CHECKS=0

# Gateway configuration
STARGATE_URL="${STARGATE_URL:-http://localhost:9999}"
GATEWAY_URL="${GATEWAY_URL:-http://localhost:9998}"

echo "=========================================="
echo "Atomic VRAM Reservation System Validation"
echo "=========================================="
echo ""

# Helper functions
check_pass() {
    echo -e "${GREEN}✓ PASS:${NC} $1"
    ((PASSED_CHECKS++))
    ((TOTAL_CHECKS++))
}

check_fail() {
    echo -e "${RED}✗ FAIL:${NC} $1"
    ((FAILED_CHECKS++))
    ((TOTAL_CHECKS++))
}

check_warn() {
    echo -e "${YELLOW}⚠ WARN:${NC} $1"
    ((TOTAL_CHECKS++))
}

# Validation checks
echo "1. Configuration Validation"
echo "----------------------------"

# Check gateways.yaml exists
if [ -f "$PROJECT_ROOT/config/gateways.yaml" ]; then
    check_pass "Gateway configuration file exists"
    
    # Check for resource_management section
    if grep -q "resource_management:" "$PROJECT_ROOT/config/gateways.yaml"; then
        check_pass "resource_management section present in config"
    else
        check_fail "resource_management section missing from config"
    fi
else
    check_fail "Gateway configuration file not found at config/gateways.yaml"
fi

# Check stargate config
if [ -f "$PROJECT_ROOT/config/stargate.yaml" ]; then
    check_pass "Stargate configuration file exists"
else
    check_warn "Stargate configuration file not found (may use defaults)"
fi

echo ""
echo "2. Service Health Checks"
echo "------------------------"

# Check if stargate is running
if curl -sf "$STARGATE_URL/health" > /dev/null 2>&1; then
    check_pass "Stargate service is running and healthy"
else
    check_fail "Stargate service is not responding at $STARGATE_URL"
fi

# Check if gateway is running
if curl -sf "$GATEWAY_URL/health" > /dev/null 2>&1; then
    check_pass "Gateway service is running and healthy"
else
    check_fail "Gateway service is not responding at $GATEWAY_URL"
fi

echo ""
echo "3. Metrics Availability"
echo "-----------------------"

# Check metrics endpoint
if curl -sf "$STARGATE_URL/metrics" | grep -q "gateway_reservation"; then
    check_pass "Prometheus metrics endpoint is available"
    check_pass "VRAM reservation metrics are exported"
else
    check_warn "Metrics endpoint not available or not exporting reservation metrics"
fi

echo ""
echo "4. Resource Management Components"
echo "----------------------------------"

# Check Python imports (if we can run Python)
if command -v python3 &> /dev/null; then
    PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH" python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')

try:
    from proxy.core.resource_manager import GatewayResourceManager
    from proxy.resource_management import GatewayConfigManager
    print('OK')
except ImportError as e:
    print(f'FAIL: {e}')
    sys.exit(1)
" > /dev/null 2>&1
    
    if [ $? -eq 0 ]; then
        check_pass "Core resource management components importable"
    else
        check_fail "Failed to import resource management components"
    fi
else
    check_warn "Python not available, skipping import checks"
fi

echo ""
echo "5. File System Permissions"
echo "---------------------------"

# Check tmp directories for sockets
if [ -d "/tmp/universal-protocol" ]; then
    check_pass "/tmp/universal-protocol directory exists"
else
    check_warn "/tmp/universal-protocol directory does not exist (will be created)"
fi

if [ -w "/tmp" ]; then
    check_pass "/tmp directory is writable"
else
    check_fail "/tmp directory is not writable"
fi

echo ""
echo "6. Network Connectivity"
echo "-----------------------"

# Check if ports are available
if ! lsof -i:9999 &> /dev/null && ! ss -tlnp 2>/dev/null | grep -q ":9999"; then
    check_warn "Port 9999 is not in use (stargate not running or using different port)"
else
    check_pass "Port 9999 is in use (stargate listening)"
fi

if ! lsof -i:9998 &> /dev/null && ! ss -tlnp 2>/dev/null | grep -q ":9998"; then
    check_warn "Port 9998 is not in use (gateway not running or using different port)"
else
    check_pass "Port 9998 is in use (gateway listening)"
fi

echo ""
echo "7. Test Suite Validation"
echo "-------------------------"

# Check if test files exist
TEST_FILES=(
    "tests/test_atomic_reservation_integration.py"
    "tests/test_chaos_engineering.py"
    "tests/test_reservation_performance.py"
    "tests/test_load_simulation.py"
)

for test_file in "${TEST_FILES[@]}"; do
    if [ -f "$PROJECT_ROOT/$test_file" ]; then
        check_pass "Test file exists: $test_file"
    else
        check_fail "Test file missing: $test_file"
    fi
done

echo ""
echo "=========================================="
echo "Validation Summary"
echo "=========================================="
echo "Total Checks:  $TOTAL_CHECKS"
echo -e "${GREEN}Passed:        $PASSED_CHECKS${NC}"
if [ $FAILED_CHECKS -gt 0 ]; then
    echo -e "${RED}Failed:        $FAILED_CHECKS${NC}"
else
    echo "Failed:        $FAILED_CHECKS"
fi
echo "=========================================="

if [ $FAILED_CHECKS -eq 0 ]; then
    echo -e "${GREEN}✓ Deployment validation PASSED${NC}"
    echo "System is ready for deployment"
    exit 0
else
    echo -e "${RED}✗ Deployment validation FAILED${NC}"
    echo "Please fix the failures before deploying"
    exit 1
fi

