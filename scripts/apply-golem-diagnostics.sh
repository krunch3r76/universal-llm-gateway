#!/usr/bin/env bash
# Apply diagnostic patches for Golem Remote Stargate exit issue
# Usage: ./scripts/apply-golem-diagnostics.sh [--bisect]

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

BISECT_MODE=false
if [[ "${1:-}" == "--bisect" ]]; then
    BISECT_MODE=true
    echo "🧪 BISECT MODE: Will disable hot-reload to test if it's the culprit"
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Golem Diagnostic Patch Application ===${NC}"
echo ""

# Backup files
echo "📋 Creating backups..."
BACKUP_DIR="./tmp/golem-diagnostic-backups-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

cp services/universal-stargate/systems/proxy/app.py "$BACKUP_DIR/" || true
cp libs/universal_hot_reload/watcher.py "$BACKUP_DIR/" || true
cp services/universal-stargate/systems/proxy/stargate/runtime/component_factory.py "$BACKUP_DIR/" || true
cp services/universal-stargate/systems/proxy/stargate/runtime/startup.py "$BACKUP_DIR/" || true
cp docker/golem-start.sh "$BACKUP_DIR/" || true

echo -e "${GREEN}✅ Backups saved to: $BACKUP_DIR${NC}"
echo ""

# Apply patches
echo "🔧 Applying diagnostic patches..."

# Patch 1: Fix hot-reload watcher exception handling
echo "  → Patching hot-reload watcher (libs/universal_hot_reload/watcher.py)..."
cat > /tmp/watcher_patch.py << 'EOF'
    async def _watch_loop(self):
        """Watch for file changes (pure async)."""
        logger.info(f"🔍 [{self.name}] _watch_loop STARTED")
        loop_completed_normally = False
        
        try:
            async for changes in awatch(
                self.watch_path,
                recursive=self.recursive,
                step=100,  # Check every 100ms
            ):
                if not self._enabled:
                    logger.info(f"🔍 [{self.name}] _enabled=False, breaking")
                    break
                    
                for change_type, file_path in changes:
                    await self._handle_change(change_type, file_path)
            
            loop_completed_normally = True
            logger.error(
                f"🚨 [{self.name}] awatch() iterator COMPLETED UNEXPECTEDLY! "
                f"This should run indefinitely. Path: {self.watch_path}"
            )
                    
        except asyncio.CancelledError:
            logger.info(f"🔍 [{self.name}] Watch loop cancelled (expected during shutdown)")
            raise
        except Exception as e:
            logger.error(f"🚨 [{self.name}] Watch loop CRASHED: {e}", exc_info=True)
            logger.error(f"🚨 Watch path: {self.watch_path}, exists: {self.watch_path.exists()}")
            self.error_count += 1
            # DO NOT SILENTLY EXIT - log prominently and re-raise
            raise  # Re-raise to make task exception visible
        finally:
            if not loop_completed_normally:
                logger.warning(f"🔍 [{self.name}] _watch_loop EXITING (normal={loop_completed_normally})")
            else:
                logger.error(f"🚨 [{self.name}] _watch_loop EXITING AFTER COMPLETION (THIS IS A BUG)")
EOF

python3 << 'PYTHON_PATCH'
import sys
import re

# Read the file
with open('libs/universal_hot_reload/watcher.py', 'r') as f:
    content = f.read()

# Find and replace the _watch_loop method
# Pattern: match from "async def _watch_loop" to the next method or end of class
pattern = r'(    async def _watch_loop\(self\):.*?)(    async def |\n\nclass |\Z)'
replacement = open('/tmp/watcher_patch.py').read() + r'\2'

new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

if new_content == content:
    print("⚠️  WARNING: Could not find _watch_loop method to patch", file=sys.stderr)
    sys.exit(1)

# Write back
with open('libs/universal_hot_reload/watcher.py', 'w') as f:
    f.write(new_content)

print("✅ Patched _watch_loop method")
PYTHON_PATCH

if [[ $? -ne 0 ]]; then
    echo -e "${RED}❌ Failed to patch watcher.py${NC}"
    echo "See DIAGNOSTIC_PATCH_INSTRUCTIONS.md for manual application"
else
    echo -e "${GREEN}  ✓ Hot-reload watcher patched${NC}"
fi

# Patch 2: Add task monitoring to lifespan
echo "  → Patching lifespan with task monitor (services/universal-stargate/systems/proxy/app.py)..."
cat > /tmp/lifespan_patch.py << 'EOF'
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events"""
    import asyncio
    
    # Startup
    logger.info("🔍 Lifespan: Starting startup phase...")
    proxy = get_proxy()
    shutdown_reason = "unknown"
    
    # Task monitoring
    async def monitor_tasks():
        """Monitor background tasks and log if any complete unexpectedly."""
        await asyncio.sleep(10)  # Wait for startup to complete
        while True:
            all_tasks = [t for t in asyncio.all_tasks() if not t.done()]
            task_names = [t.get_name() for t in all_tasks]
            logger.debug(f"🔍 Active tasks: {len(all_tasks)}")
            
            # Check for specific critical tasks
            critical_tasks = [
                "HotReload-profiles",
                "federation-periodic-telemetry",
                "http-telemetry-poller",
            ]
            missing = [name for name in critical_tasks if not any(name in tn for tn in task_names)]
            if missing:
                logger.error(f"🚨 CRITICAL TASKS MISSING: {missing}")
                logger.error(f"🚨 This may cause application shutdown!")
            
            await asyncio.sleep(15)  # Check every 15 seconds
    
    monitor_task = None
    
    try:
        await proxy.startup(app)
        logger.info("🔍 Lifespan: Startup completed successfully, yielding to application...")
        
        # Start task monitor
        monitor_task = asyncio.create_task(monitor_tasks(), name="task-monitor")
        logger.info("🔍 Started background task monitor")
        
        yield  # Application runs here
        
        shutdown_reason = "normal_exit_after_yield"
        logger.warning(f"🔍 Lifespan: Application is shutting down... (reason: {shutdown_reason})")
        
    except asyncio.CancelledError:
        shutdown_reason = "cancelled_error"
        logger.error(f"🔍 Lifespan: CancelledError received (reason: {shutdown_reason})")
        raise
    except Exception as e:
        shutdown_reason = f"exception_{type(e).__name__}"
        logger.error(f"🔍 Lifespan: Exception: {e} (reason: {shutdown_reason})", exc_info=True)
        raise
    finally:
        # Cancel monitor
        if monitor_task:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass
        
        # Shutdown
        logger.warning(f"🔍 Lifespan: Running shutdown... (reason: {shutdown_reason})")
        all_tasks = [t for t in asyncio.all_tasks() if not t.done() and t != asyncio.current_task()]
        logger.warning(f"🔍 Tasks still running: {[t.get_name() for t in all_tasks]}")
        
        await proxy.shutdown()
        logger.info("🔍 Lifespan: Shutdown complete")
EOF

python3 << 'PYTHON_PATCH2'
import sys
import re

with open('services/universal-stargate/systems/proxy/app.py', 'r') as f:
    content = f.read()

# Find and replace the lifespan function
pattern = r'@asynccontextmanager\nasync def lifespan\(app: FastAPI\):.*?(?=\n\n# Load federation config)'
replacement = open('/tmp/lifespan_patch.py').read() + '\n'

new_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

if new_content == content:
    print("⚠️  WARNING: Could not find lifespan function to patch", file=sys.stderr)
    sys.exit(1)

with open('services/universal-stargate/systems/proxy/app.py', 'w') as f:
    f.write(new_content)

print("✅ Patched lifespan function")
PYTHON_PATCH2

if [[ $? -ne 0 ]]; then
    echo -e "${YELLOW}⚠️  Failed to patch app.py (may require manual edit)${NC}"
else
    echo -e "${GREEN}  ✓ Lifespan patched with task monitor${NC}"
fi

# Patch 3: Add uvicorn explicit timeouts
echo "  → Patching uvicorn startup (docker/golem-start.sh)..."
sed -i 's/--log-level "\${LOG_LEVEL,,}"/--log-level trace \\\n    --timeout-keep-alive 3600 \\\n    --timeout-graceful-shutdown 120/' docker/golem-start.sh || true
echo -e "${GREEN}  ✓ Uvicorn timeouts configured${NC}"

# Patch 4: Bisect mode - disable hot-reload
if [[ "$BISECT_MODE" == true ]]; then
    echo "  → BISECT: Disabling hot-reload..."
    sed -i 's/^\( *\)await initialize_hot_reload(proxy)/\1# BISECT TEST: await initialize_hot_reload(proxy)\n\1logger.warning("🧪 Hot-reload DISABLED for bisect test")/' \
        services/universal-stargate/systems/proxy/stargate/runtime/startup.py
    echo -e "${YELLOW}  ⚠️  Hot-reload DISABLED (bisect mode)${NC}"
fi

echo ""
echo -e "${GREEN}✅ All patches applied successfully!${NC}"
echo ""
echo "📋 Next steps:"
echo "  1. Rebuild image: docker build -t universal-llm-gateway:golem-base -f docker/Dockerfile.golem-base ."
echo "  2. Recreate tarball: ./scripts/create-app-tarball.sh"
echo "  3. Restart container: docker compose -f docker/docker-compose.golem-federated-test.yml up -d --force-recreate"
echo "  4. Monitor logs: docker logs -f golem-remote-stargate-1 2>&1 | grep '🚨\\|🔍'"
echo ""
echo "📊 What to look for:"
echo "  - '🚨 awatch() iterator COMPLETED UNEXPECTEDLY' → watchfiles issue"
echo "  - '🚨 Hot-reload task CRASHED' → watcher exception"
echo "  - '🚨 CRITICAL TASKS MISSING' → task exited silently"
echo "  - 'reason: normal_exit_after_yield' → uvicorn shutdown trigger"
echo ""
if [[ "$BISECT_MODE" == true ]]; then
    echo -e "${YELLOW}🧪 BISECT MODE: If container stays up, hot-reload is the culprit${NC}"
    echo ""
fi
echo "📄 Backups: $BACKUP_DIR"
echo "📖 Full details: DIAGNOSTIC_PATCH_INSTRUCTIONS.md"
echo ""
echo "To rollback:"
echo "  cp $BACKUP_DIR/*.py services/universal-stargate/systems/proxy/"
echo "  cp $BACKUP_DIR/watcher.py libs/universal_hot_reload/"
echo "  cp $BACKUP_DIR/golem-start.sh docker/"
