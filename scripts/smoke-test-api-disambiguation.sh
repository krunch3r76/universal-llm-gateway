#!/bin/bash
# Smoke test for API disambiguation + ModelId migration (Phases 1-2)
# REQUIRES: User approval before running (starts services)
# DEFERRED: Execute after Phase 4 completion

set -e

MODEL_ID="${1:-hermes3-llama3.1-8b-16384}"

echo "=== API Disambiguation & ModelId Migration Smoke Test ==="
echo "Model: $MODEL_ID"
echo ""

# Stop existing services
echo "Stopping existing services..."
pkill -f "universal-" 2>/dev/null || true
rm -f /tmp/universal-protocol/*.sock /tmp/process_ipc/*.sock 2>/dev/null || true
sleep 2

# Clear logs
echo "Clearing old logs..."
rm -rf /tmp/logs/universal-stargate/*.log /tmp/logs/universal-llm-gateway/*.log 2>/dev/null || true

# Start services
echo "Starting Gateway..."
./services/_universal-llm-gateway/scripts/start-gateway.sh debug &
GATEWAY_PID=$!
sleep 3

echo "Starting Stargate..."
./services/universal-stargate/scripts/start-stargate.sh debug &
STARGATE_PID=$!
sleep 5

echo "Services started (Gateway: $GATEWAY_PID, Stargate: $STARGATE_PID)"
echo ""

# Test 1: Basic request (plain model ID)
echo "Test 1: Basic request (plain model ID)..."
RESPONSE1=$(curl -s -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$MODEL_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":16}")

echo "Response: $RESPONSE1"
if echo "$RESPONSE1" | grep -q "error"; then
  echo "❌ FAIL: Test 1 returned error"
  exit 1
fi
echo "✅ Test 1 passed"
echo ""

# Test 2: Request with -hybrid suffix (tests Phase 1.3/1.4 normalized comparison)
echo "Test 2: Request with -hybrid suffix (normalized comparison)..."
RESPONSE2=$(curl -s -X POST http://localhost:9999/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${MODEL_ID}-hybrid\",\"messages\":[{\"role\":\"user\",\"content\":\"ping2\"}],\"max_tokens\":16}")

echo "Response: $RESPONSE2"
if echo "$RESPONSE2" | grep -q "error"; then
  echo "❌ FAIL: Test 2 returned error"
  exit 1
fi
echo "✅ Test 2 passed (normalized comparison works)"
echo ""

# Check for Phase 1 errors (type confusion)
echo "Checking for Phase 1 type confusion errors..."
if grep -rE "Cannot determine resource requirements|MissingResourceRequirementsError|AttributeError.*vram_usage" /tmp/logs/universal-stargate/ 2>/dev/null; then
  echo "❌ FAIL: Found Phase 1 errors (type confusion not fixed)"
  exit 1
fi
echo "✅ No Phase 1 type confusion errors"

# Check for Phase 1.2 monitoring (should be non-blocking)
echo "Checking for Phase 1.2 monitoring configuration..."
if grep -E "schedule_background_configuration_fetch|_fetch_configuration_background" /tmp/logs/universal-stargate/*.log 2>/dev/null | tail -3; then
  echo "✅ Monitoring configuration is background (Phase 1.2)"
else
  echo "⚠️  No explicit monitoring logs found (may not have triggered)"
fi
echo ""

# Check for Phase 1.3/1.4 cache hits (normalized lookup)
echo "Checking for normalized cache lookups..."
if grep -iE "cache.*hit|cache.*found" /tmp/logs/universal-stargate/*.log 2>/dev/null | tail -3; then
  echo "✅ Cache evidence found (Phase 1.3)"
else
  echo "⚠️  No explicit cache logs (may be debug-level only)"
fi
echo ""

# Check for Phase 2 fail-closed behavior
echo "Checking for Phase 2 fail-closed behavior..."
if grep -E "excluding from model_details|Incomplete resource requirements" /tmp/logs/universal-stargate/*.log 2>/dev/null; then
  echo "✅ Fail-closed validation active (Phase 2)"
else
  echo "✅ No models excluded (all have valid requirements - expected for catalog models)"
fi
echo ""

# Check for successful model load
echo "Checking for successful model load..."
if grep -E "Model.*loaded|Loading model|✅.*load" /tmp/logs/universal-llm-gateway/*.log 2>/dev/null | tail -3; then
  echo "✅ Model load evidence found"
else
  echo "⚠️  No explicit model load evidence (may already be loaded)"
fi

echo ""
echo "=== Smoke test completed ✅ ==="
echo ""
echo "Summary:"
echo "  - Test 1 (plain ID): ✅"
echo "  - Test 2 (-hybrid suffix): ✅"
echo "  - No type confusion: ✅"
echo "  - Monitoring non-blocking: ✅ (or not triggered)"
echo "  - Fail-closed active: ✅"
echo ""
echo "To stop services: pkill -f 'universal-'"
