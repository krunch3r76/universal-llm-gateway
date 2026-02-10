#!/usr/bin/env bash
#
# Docker Security Verification Script
# Verifies that containers run as non-root user (UID 1000)
#
# Usage:
#   ./scripts/verify_docker_security.sh <container_name_or_id>
#   ./scripts/verify_docker_security.sh --all  # Check all running containers
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ============================================================================
# Functions
# ============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $*"
}

log_warning() {
    echo -e "${YELLOW}[⚠]${NC} $*"
}

log_error() {
    echo -e "${RED}[✗]${NC} $*"
}

verify_container() {
    local container="$1"
    local container_name
    local exit_code=0
    
    # Get container name if ID was provided
    container_name=$(docker inspect --format='{{.Name}}' "$container" 2>/dev/null | sed 's/^\///' || echo "$container")
    
    log_info "Verifying container: ${container_name}"
    echo "----------------------------------------"
    
    # Check if container is running
    if ! docker ps --format '{{.Names}}' | grep -q "^${container_name}$"; then
        log_error "Container '${container_name}' is not running"
        return 1
    fi
    
    # 1. Check running processes user (docker top)
    log_info "Checking running processes..."
    local top_output
    top_output=$(docker top "$container" -o pid,user,cmd 2>&1)
    
    if echo "$top_output" | tail -n +2 | grep -q "root"; then
        log_error "Container has processes running as root:"
        echo "$top_output" | grep "root" | head -5
        exit_code=1
    else
        log_success "No root processes found"
        echo "$top_output" | head -3
    fi
    
    # 2. Check user inside container (id command)
    log_info "Checking container user..."
    local user_info
    user_info=$(docker exec "$container" id 2>&1)
    
    if echo "$user_info" | grep -q "uid=0(root)"; then
        log_error "Container is running as root user"
        echo "  $user_info"
        exit_code=1
    elif echo "$user_info" | grep -q "uid=1000"; then
        log_success "Container is running as non-root user (UID 1000)"
        echo "  $user_info"
    else
        log_warning "Container is running as non-root user (but not UID 1000)"
        echo "  $user_info"
    fi
    
    # 3. Check USER directive in image
    log_info "Checking Docker image USER directive..."
    local image_user
    image_user=$(docker inspect --format='{{.Config.User}}' "$container" 2>&1)
    
    if [ -z "$image_user" ] || [ "$image_user" = "0" ] || [ "$image_user" = "root" ]; then
        log_error "Image does not specify non-root USER directive"
        echo "  User: ${image_user:-<empty>}"
        exit_code=1
    else
        log_success "Image specifies USER: $image_user"
    fi
    
    # 4. Check file ownership
    log_info "Checking file ownership..."
    local file_ownership
    file_ownership=$(docker exec "$container" ls -la /app 2>&1 | head -5)
    
    if echo "$file_ownership" | grep -q "root root"; then
        log_warning "Some files in /app are owned by root"
        echo "$file_ownership" | grep "root root" | head -3
    else
        log_success "Files in /app are owned by non-root user"
        echo "$file_ownership" | head -3
    fi
    
    # 5. Check security labels
    log_info "Checking security labels..."
    local security_user
    local security_uid
    security_user=$(docker inspect --format='{{index .Config.Labels "security.user"}}' "$container" 2>&1)
    security_uid=$(docker inspect --format='{{index .Config.Labels "security.uid"}}' "$container" 2>&1)
    
    if [ -n "$security_user" ] && [ "$security_user" != "<no value>" ]; then
        log_success "Security labels present: user=$security_user, uid=$security_uid"
    else
        log_warning "No security labels found (image may predate security audit)"
    fi
    
    # Summary
    echo "----------------------------------------"
    if [ $exit_code -eq 0 ]; then
        log_success "Container '${container_name}' PASSED security verification"
        return 0
    else
        log_error "Container '${container_name}' FAILED security verification"
        return 1
    fi
}

verify_all_containers() {
    log_info "Verifying all running containers..."
    echo "========================================"
    
    local containers
    containers=$(docker ps --format '{{.Names}}')
    
    if [ -z "$containers" ]; then
        log_warning "No running containers found"
        return 0
    fi
    
    local total=0
    local passed=0
    local failed=0
    
    while IFS= read -r container; do
        total=$((total + 1))
        echo ""
        if verify_container "$container"; then
            passed=$((passed + 1))
        else
            failed=$((failed + 1))
        fi
    done <<< "$containers"
    
    echo ""
    echo "========================================"
    log_info "Summary: $total containers checked"
    log_success "Passed: $passed"
    if [ $failed -gt 0 ]; then
        log_error "Failed: $failed"
        return 1
    fi
    return 0
}

verify_image() {
    local image="$1"
    
    log_info "Verifying image: $image"
    echo "----------------------------------------"
    
    # Check if image exists
    if ! docker image inspect "$image" > /dev/null 2>&1; then
        log_error "Image '$image' not found"
        return 1
    fi
    
    # Check USER directive
    local user
    user=$(docker image inspect --format='{{.Config.User}}' "$image" 2>&1)
    
    if [ -z "$user" ] || [ "$user" = "0" ] || [ "$user" = "root" ]; then
        log_error "Image does not specify non-root USER"
        echo "  USER: ${user:-<empty>}"
        return 1
    else
        log_success "Image specifies USER: $user"
    fi
    
    # Check security labels
    local security_user
    local security_uid
    security_user=$(docker image inspect --format='{{index .Config.Labels "security.user"}}' "$image" 2>&1)
    security_uid=$(docker image inspect --format='{{index .Config.Labels "security.uid"}}' "$image" 2>&1)
    
    if [ -n "$security_user" ] && [ "$security_user" != "<no value>" ]; then
        log_success "Security labels: user=$security_user, uid=$security_uid"
    else
        log_warning "No security labels found"
    fi
    
    # Check history for USER directive
    log_info "Checking build history..."
    if docker history "$image" | grep -q "USER appuser"; then
        log_success "USER directive found in build history"
    else
        log_warning "USER directive not found in build history"
    fi
    
    echo "----------------------------------------"
    log_success "Image '$image' verification complete"
    return 0
}

show_usage() {
    cat << EOF
Docker Security Verification Script

Usage:
  $0 <container_name_or_id>     Verify specific container
  $0 --all                       Verify all running containers
  $0 --image <image_name>        Verify Docker image
  $0 --help                      Show this help message

Examples:
  $0 golem-gateway-1
  $0 --all
  $0 --image universal-llm-gateway:golem-base

Checks performed:
  1. Running processes user (docker top)
  2. Container user (id command)
  3. USER directive in image
  4. File ownership
  5. Security labels

Exit codes:
  0 - All checks passed
  1 - One or more checks failed
EOF
}

# ============================================================================
# Main
# ============================================================================

main() {
    if [ $# -eq 0 ]; then
        log_error "No arguments provided"
        show_usage
        exit 1
    fi
    
    case "$1" in
        --help|-h)
            show_usage
            exit 0
            ;;
        --all)
            verify_all_containers
            exit $?
            ;;
        --image)
            if [ $# -lt 2 ]; then
                log_error "Image name required"
                show_usage
                exit 1
            fi
            verify_image "$2"
            exit $?
            ;;
        *)
            verify_container "$1"
            exit $?
            ;;
    esac
}

main "$@"
