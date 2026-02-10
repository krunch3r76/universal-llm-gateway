#!/usr/bin/env bash
#
# Build Golem Application Code Tarball
# Creates app-code.tar.gz with application code only (no runtime dependencies)
#
# Usage:
#   ./scripts/build_golem_tarball.sh [output_path]
#
# Output:
#   app-code.tar.gz (default) or specified output path
#
# Structure:
#   app/
#   ├── libs/                          # Python libraries
#   ├── services/
#   │   ├── _universal-llm-gateway/    # Gateway service  
#   │   └── universal-stargate/        # Stargate service
#   ├── config/                        # Configuration files
#   ├── sitecustomize.py               # Python path setup
#   └── golem-start.sh                 # Startup script

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_PATH="${1:-${PROJECT_ROOT}/tmp/app-code.tar.gz}"

log_info() {
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] [INFO] $*" >&2
}

log_error() {
    echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] [ERROR] $*" >&2
}

# Signal handler for clean shutdown
cleanup_and_exit() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_error "Build interrupted or failed (exit code: $exit_code)"
    fi
    # Cleanup happens via EXIT trap below
    exit $exit_code
}

# Trap signals for clean shutdown
trap cleanup_and_exit INT TERM

# ============================================================================
# Validation
# ============================================================================

log_info "Validating project structure..."

required_paths=(
    "libs"
    "services/_universal-llm-gateway"
    "services/universal-stargate"
    "config"
    "sitecustomize.py"
    "docker/runtime/golem-start.sh"
)

for path in "${required_paths[@]}"; do
    if [[ ! -e "${PROJECT_ROOT}/${path}" ]]; then
        log_error "Required path missing: ${path}"
        exit 1
    fi
done

log_info "✅ Project structure validated"

# ============================================================================
# Create temporary directory for tarball contents
# ============================================================================

TEMP_DIR=$(mktemp -d)

# Cleanup temporary directory on exit
cleanup_temp() {
    if [[ -n "${TEMP_DIR:-}" && -d "${TEMP_DIR}" ]]; then
        rm -rf "${TEMP_DIR}"
    fi
}
trap cleanup_temp EXIT

APP_DIR="${TEMP_DIR}/app"
mkdir -p "${APP_DIR}"

log_info "Building tarball structure in ${TEMP_DIR}..."

# ============================================================================
# Copy application code (respecting .gitignore)
# ============================================================================

log_info "Copying application code (respecting .gitignore)..."

# Define what to include (working directory state, not git state)
include_paths=(
    "libs"
    "services/_universal-llm-gateway"
    "services/universal-stargate"
    "config"
    "sitecustomize.py"
)

# Use rsync to copy with gitignore rules
# --exclude-from doesn't work directly with .gitignore format, so we'll use explicit excludes
log_info "Copying libs/..."
mkdir -p "${APP_DIR}/libs"
rsync -a \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='*.swp' \
    --exclude='*.swo' \
    --exclude='.DS_Store' \
    --exclude='*.log' \
    --exclude='.pytest_cache' \
    --exclude='.coverage' \
    --exclude='htmlcov' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='.ruff_cache' \
    --exclude='tests' \
    --exclude='test' \
    --exclude='*.md' \
    "${PROJECT_ROOT}/libs/" "${APP_DIR}/libs/"

# Debug: verify libs were copied
if [ -d "${APP_DIR}/libs/inference_djinn" ]; then
    file_count=$(find "${APP_DIR}/libs/" -type f | wc -l)
    log_info "✅ libs/inference_djinn copied successfully (${file_count} files)"
else
    log_error "❌ libs/inference_djinn not found after rsync!"
    ls -la "${APP_DIR}/libs/" || echo "libs/ directory is empty or doesn't exist"
    exit 1
fi

log_info "Copying services/..."
mkdir -p "${APP_DIR}/services"
rsync -a \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='*.swp' \
    --exclude='*.swo' \
    --exclude='.DS_Store' \
    --exclude='*.log' \
    --exclude='.pytest_cache' \
    --exclude='.coverage' \
    --exclude='htmlcov' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='.ruff_cache' \
    --exclude='tests' \
    --exclude='test' \
    --exclude='*.md' \
    "${PROJECT_ROOT}/services/_universal-llm-gateway/" "${APP_DIR}/services/_universal-llm-gateway/"

rsync -a \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='*.swp' \
    --exclude='*.swo' \
    --exclude='.DS_Store' \
    --exclude='*.log' \
    --exclude='.pytest_cache' \
    --exclude='.coverage' \
    --exclude='htmlcov' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='.ruff_cache' \
    --exclude='tests' \
    --exclude='test' \
    --exclude='*.md' \
    "${PROJECT_ROOT}/services/universal-stargate/" "${APP_DIR}/services/universal-stargate/"

log_info "Copying config/..."
rsync -a \
    --exclude='*.bak.*' \
    --exclude='*.local' \
    --exclude='*.swp' \
    --exclude='*.swo' \
    --exclude='*.md' \
    "${PROJECT_ROOT}/config/" "${APP_DIR}/config/"

log_info "Copying sitecustomize.py..."
cp "${PROJECT_ROOT}/sitecustomize.py" "${APP_DIR}/"

log_info "Copying golem-start.sh..."
cp "${PROJECT_ROOT}/docker/runtime/golem-start.sh" "${APP_DIR}/"
chmod +x "${APP_DIR}/golem-start.sh"

# Cleanup already handled by rsync exclude patterns above
log_info "✅ Files copied (excluding cache, tests, and temp files)"

# ============================================================================
# Create tarball
# ============================================================================

log_info "Creating tarball: ${OUTPUT_PATH}"

# Debug: Show what's actually in the temp directory before tarring
log_info "Contents of ${APP_DIR}:"
ls -la "${APP_DIR}/" | head -20 || true
log_info "Sample libs contents:"
find "${APP_DIR}/libs/" -type f | head -5 || true

cd "${TEMP_DIR}"
tar -czf "${OUTPUT_PATH}" app/

# Debug: Verify what's in the tarball immediately after creation
log_info "Verifying libs in tarball:"
if ( set +o pipefail; tar -tzf "${OUTPUT_PATH}" | grep -Fq "app/libs/" ); then
    log_info "✅ app/libs/ found in tarball"
    tar -tzf "${OUTPUT_PATH}" | grep -F "app/libs/" | head -5 || true
else
    log_error "❌ app/libs/ NOT in tarball, but was in temp dir!"
    log_error "Temp dir contents before cleanup:"
    ls -lR "${TEMP_DIR}/app/libs/" | head -30 || true
    exit 1
fi

# ============================================================================
# Verification
# ============================================================================

log_info "Verifying tarball structure..."

# Use || true to ignore SIGPIPE (exit 141) when head closes pipe early
tar -tzf "${OUTPUT_PATH}" | head -20 || true

TARBALL_SIZE=$(du -h "${OUTPUT_PATH}" | cut -f1)
log_info "✅ Tarball created: ${OUTPUT_PATH} (${TARBALL_SIZE})"

# Verify critical files exist in tarball
log_info "Verifying critical files..."

critical_files=(
    "app/libs/"
    "app/services/_universal-llm-gateway/"
    "app/services/universal-stargate/"
    "app/config/"
    "app/sitecustomize.py"
    "app/golem-start.sh"
)

for file in "${critical_files[@]}"; do
    if [[ "${file}" == */ ]]; then
        if ! ( set +o pipefail; tar -tzf "${OUTPUT_PATH}" | grep -Fq "${file}" ); then
            log_error "Critical path missing in tarball: ${file}"
            exit 1
        fi
    else
        if ! ( set +o pipefail; tar -tzf "${OUTPUT_PATH}" | grep -Fxq "${file}" ); then
            log_error "Critical file missing in tarball: ${file}"
            exit 1
        fi
    fi
done

log_info "✅ All critical files present"

# ============================================================================
# Summary
# ============================================================================

log_info "=========================================="
log_info "Golem Application Tarball Build Complete"
log_info "=========================================="
log_info "Output: ${OUTPUT_PATH}"
log_info "Size: ${TARBALL_SIZE}"
log_info ""
log_info "Next steps:"
log_info "1. Build base image:"
log_info "   docker build -f Dockerfile.golem-base -t universal-llm-gateway:golem-base ."
log_info "   gvmkit-build universal-llm-gateway:golem-base"
log_info ""
log_info "2. Deploy with gollm requestor:"
log_info "   python requestor.py --service gateway --tarball ${OUTPUT_PATH}"
log_info ""
log_info "See: /mnt/mai/projects/gollm/docs/GOLEM_CONTAINER_CONTRACT.md"
log_info "=========================================="
