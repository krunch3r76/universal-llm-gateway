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
# Catalog validation pre-commit hook — installed by scripts/hooks/install-hooks.sh
ROOT="$(git rev-parse --show-toplevel)"
exec "$ROOT/scripts/hooks/pre_commit.sh"
EOF

chmod +x "$HOOKS_DIR/pre-commit"
chmod +x "$PROJECT_ROOT/scripts/hooks/pre_commit.sh"
chmod +x "$PROJECT_ROOT/scripts/hooks/resolve_hook_python.sh"

echo "OK Pre-commit hook installed"
echo "   Run 'git commit' to test catalog validation"
