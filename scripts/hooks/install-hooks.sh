#!/bin/bash
# Install Git hooks for catalog validation
#
# Usage: ./scripts/hooks/install-hooks.sh
#
# Note: This is development tooling for repo contributors.
#       Isolated gateways don't use Git hooks.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOKS_DIR="$PROJECT_ROOT/.git/hooks"

echo "Installing pre-commit hook..."

cat > "$HOOKS_DIR/pre-commit" << 'EOF'
#!/bin/bash
# Catalog validation pre-commit hook
# Installed by scripts/hooks/install-hooks.sh

PYTHON="${HOME}/.venvs/universal/bin/python3"

"$PYTHON" scripts/hooks/validate_catalog.py --staged || exit 1
# Regenerate catalog when staged event sources drift, then re-stage the doc.
"$PYTHON" -m scripts.gen_event_catalog sync --staged || exit 1
git add docs/event-contracts.md 2>/dev/null || true
"$PYTHON" -m scripts.gen_event_catalog check --staged || exit 1
"$PYTHON" scripts/check-rag-events-imports.py --staged || exit 1
"$PYTHON" scripts/lint-fastapi-annotations.py --staged
exit $?
EOF

chmod +x "$HOOKS_DIR/pre-commit"

echo "OK Pre-commit hook installed"
echo "   Run 'git commit' to test catalog validation"
