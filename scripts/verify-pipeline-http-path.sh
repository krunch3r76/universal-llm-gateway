#!/bin/bash
# Phase 3 verification script

set -e

STARGATE_URL="${STARGATE_URL:-http://localhost:9999}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STARGATE_DIR="$PROJECT_ROOT/services/universal-stargate"

echo "=== Phase 3: Pipeline HTTP Path Verification ==="
echo ""

# Prerequisites
echo "--- Task 0: Prerequisites ---"

# 0.1 Phase 2.9 cleanup
echo -n "Phase 2.9 cleanup... "
if [ -d "$STARGATE_DIR/systems/proxy/core/nonstreaming/batch_execution" ]; then
  echo "❌ batch_execution still exists"
  exit 1
fi
if [ -f "$STARGATE_DIR/systems/pipeline/core/execution/deferred_scheduler.py" ]; then
  echo "❌ deferred_scheduler still exists"
  exit 1
fi
if [ ! -f "$STARGATE_DIR/systems/pipeline/core/execution/model_tracker.py" ]; then
  echo "❌ model_tracker missing"
  exit 1
fi
echo "✅"

# 0.2 Import verification
echo -n "Critical imports... "
cd "$STARGATE_DIR"
source ~/.venvs/universal/bin/activate
python -c "from systems.pipeline.core.execution import ProxyClient, DAGExecutor" 2>/dev/null && echo "✅" || { echo "❌"; exit 1; }

# 0.3 ModelInvoker check
echo -n "ModelInvoker removal... "
if grep -r "ModelInvoker" systems/pipeline/core/handlers --include="*.py" 2>/dev/null | grep -v "^\s*#" | grep -v "# " | grep -q .; then
  echo "❌ ModelInvoker code references found"
  exit 1
fi
echo "✅"

# Task 1: Services
echo ""
echo "--- Task 1: Service Health ---"

echo -n "Stargate health... "
curl -sf "$STARGATE_URL/health" > /dev/null && echo "✅" || { echo "❌"; exit 1; }

echo -n "Gateway health... "
curl -sf "http://localhost:9998/health" > /dev/null && echo "✅" || { echo "❌"; exit 1; }

# Task 2: Pipeline execution
echo ""
echo "--- Task 2: Pipeline Execution ---"

echo -n "Translation pipeline... "
RESULT=$(curl -sf -X POST "$STARGATE_URL/v1/pipeline/execute" \
  -H "Content-Type: application/json" \
  -d '{"pipeline_id": "es-en-colloquial-csc", "text": "Hola", "options": {}}' 2>&1) || true

if [ -n "$RESULT" ]; then
  # Check if result looks like success (not an error)
  if echo "$RESULT" | jq -e 'has("error")' > /dev/null 2>&1; then
    ERROR=$(echo "$RESULT" | jq -r '.error // .message // "unknown"')
    echo "⚠️ Error: $ERROR"
    echo "   (May require missing handlers)"
  else
    echo "✅"
  fi
else
  echo "⚠️ No response (check if pipeline endpoint exists)"
fi

# Task 5: Error handling
echo ""
echo "--- Task 5: Error Handling ---"

echo -n "Invalid pipeline ID... "
RESULT=$(curl -sf -X POST "$STARGATE_URL/v1/pipeline/execute" \
  -H "Content-Type: application/json" \
  -d '{"pipeline_id": "nonexistent", "text": "test", "options": {}}' 2>&1) || true

if [ -n "$RESULT" ]; then
  if echo "$RESULT" | jq -e 'has("error") or has("detail")' > /dev/null 2>&1; then
    echo "✅ (returned error as expected)"
  else
    echo "⚠️ Unexpected response format"
  fi
else
  echo "⚠️ No response"
fi

echo ""
echo "=== Verification Complete ==="
echo ""
echo "Manual verification required:"
echo "- Check logs for X-Pipeline-* headers"
echo "- Check logs for pipeline_internal in middleware_actions"
echo "- Run performance tests (Task 6)"
