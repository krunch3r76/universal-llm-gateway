#!/bin/bash
# doc-audit.sh - Audit README_AI.md files for freshness and accuracy
#
# Usage: ./scripts/doc-audit.sh [--verbose]
#
# Checks:
# 1. README_AI.md files exist
# 2. YAML frontmatter is present
# 3. SCREAMING_SNAKE_CASE sections exist
# 4. KEY_FILES paths are valid
# 5. Events in code match documented events

cd "$(dirname "$0")/.."

VERBOSE=${1:-""}
ERRORS=0
WARNINGS=0

echo "=== README_AI.md Audit ==="
echo ""

# Define README_AI.md locations
README_AI_FILES=(
    "README_AI.md"
    "services/universal-stargate/README_AI.md"
    "services/_universal-llm-gateway/README_AI.md"
    "libs/inference_djinn/README_AI.md"
)

# Check 1: README_AI.md files exist
echo "1. Checking README_AI.md files exist..."
FOUND_COUNT=0
for readme in "${README_AI_FILES[@]}"; do
    if [ -f "$readme" ]; then
        echo "   ✓ $readme"
        ((FOUND_COUNT++))
    else
        echo "   ⚠ $readme (not found - may not be created yet)"
        ((WARNINGS++))
    fi
done
echo ""

# Exit early if no README_AI.md files exist
if [ $FOUND_COUNT -eq 0 ]; then
    echo "=== No README_AI.md files found ==="
    echo "Run architecture-documentation phase plans to create them."
    echo ""
    exit 0
fi

# Check 2: YAML frontmatter present
echo "2. Checking YAML frontmatter..."
for readme in "${README_AI_FILES[@]}"; do
    if [ -f "$readme" ]; then
        if head -5 "$readme" | grep -q "^---$"; then
            if grep -q "^component:" "$readme" && grep -q "^events_emitted:" "$readme"; then
                echo "   ✓ $readme has valid frontmatter"
            else
                echo "   ✗ $readme missing frontmatter fields (component, events_emitted)"
                ((ERRORS++))
            fi
        else
            echo "   ✗ $readme missing YAML frontmatter"
            ((ERRORS++))
        fi
    fi
done
echo ""

# Check 3: SCREAMING_SNAKE_CASE sections
echo "3. Checking SCREAMING_SNAKE_CASE sections..."
REQUIRED_SECTIONS=("QUICK_NAVIGATION" "INVARIANTS" "ANTI_PATTERNS" "KEY_FILES")
for readme in "${README_AI_FILES[@]}"; do
    if [ -f "$readme" ]; then
        missing=""
        for section in "${REQUIRED_SECTIONS[@]}"; do
            if ! grep -q "^## $section" "$readme"; then
                missing="$missing $section"
            fi
        done
        if [ -z "$missing" ]; then
            echo "   ✓ $readme has all required sections"
        else
            echo "   ✗ $readme missing sections:$missing"
            ((ERRORS++))
        fi
    fi
done
echo ""

# Check 4: KEY_FILES paths valid
echo "4. Checking KEY_FILES paths..."
for readme in "${README_AI_FILES[@]}"; do
    if [ -f "$readme" ]; then
        # Get directory of README_AI.md for relative paths
        dir=$(dirname "$readme")
        [ "$dir" = "." ] && dir=""
        
        # Extract file paths from KEY_FILES table
        invalid_count=0
        while IFS= read -r path; do
            # Clean up the path
            clean_path=$(echo "$path" | tr -d '`' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
            [ -z "$clean_path" ] && continue
            
            # Try relative to README_AI.md location first
            if [ -n "$dir" ]; then
                full_path="$dir/$clean_path"
            else
                full_path="$clean_path"
            fi
            
            # Check if path exists (file or directory)
            if [ ! -e "$full_path" ] && [ ! -e "$clean_path" ]; then
                if [ -n "$VERBOSE" ]; then
                    echo "   ⚠ $readme: $clean_path not found"
                fi
                ((invalid_count++))
            fi
        done < <(grep -A 50 "^## KEY_FILES" "$readme" 2>/dev/null | grep -E "^\|.*\|.*\`" | grep -oE '\`[^`]+\`' | head -20)
        
        if [ $invalid_count -eq 0 ]; then
            echo "   ✓ $readme KEY_FILES paths valid"
        else
            echo "   ⚠ $readme has $invalid_count invalid paths (use --verbose for details)"
            ((WARNINGS++))
        fi
    fi
done
echo ""

# Check 5: Look for undocumented events
echo "5. Checking for potentially undocumented events..."
# Find event emissions in code
EMITTED_EVENTS=$(rg "publish.*\(.*['\"]([A-Z_]+)['\"]" --type py -o 2>/dev/null | grep -oE "[A-Z_]{3,}" | sort -u | head -20)

if [ -n "$EMITTED_EVENTS" ]; then
    echo "   Events found in code (verify documented in README_AI.md):"
    for event in $EMITTED_EVENTS; do
        # Check if event is in any README_AI.md
        found=false
        for readme in "${README_AI_FILES[@]}"; do
            if [ -f "$readme" ] && grep -q "$event" "$readme"; then
                found=true
                break
            fi
        done
        if $found; then
            echo "   ✓ $event documented"
        else
            if [ -n "$VERBOSE" ]; then
                echo "   ⚠ $event may not be documented"
                ((WARNINGS++))
            fi
        fi
    done
else
    echo "   (No events found in quick scan)"
fi
echo ""

# Summary
echo "=== Audit Summary ==="
echo "Errors: $ERRORS"
echo "Warnings: $WARNINGS"
echo ""

if [ $ERRORS -gt 0 ]; then
    echo "❌ Audit failed with $ERRORS errors"
    exit 1
elif [ $WARNINGS -gt 0 ]; then
    echo "⚠ Audit passed with $WARNINGS warnings"
    exit 0
else
    echo "✅ Audit passed"
    exit 0
fi

