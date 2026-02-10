#!/usr/bin/env python3
"""
DEPRECATED: Use 'model-manager' instead.

This CLI has been consolidated into model-manager.

Migration guide:
  OLD: python -m inference_djinn.catalog export <model-id>
  NEW: model-manager export <model-id>

  OLD: python -m inference_djinn.catalog list --local
  NEW: model-manager list --local

  OLD: python -m inference_djinn.catalog remove <model-id>
  NEW: model-manager remove <model-id>

  OLD: python -m inference_djinn.catalog show <model-id>
  NEW: model-manager show <model-id>

  OLD: python -m inference_djinn.catalog init
  NEW: model-manager init
"""

import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    """Main entry point - deprecated, forwards to model-manager."""
    print("⚠️  DEPRECATED: Use 'model-manager' instead", file=sys.stderr)
    print("", file=sys.stderr)
    print("Migration:", file=sys.stderr)
    print("  model-manager export <model-id>", file=sys.stderr)
    print("  model-manager list [--local|--static|--merged]", file=sys.stderr)
    print("  model-manager remove <model-id>", file=sys.stderr)
    print("  model-manager show <model-id>", file=sys.stderr)
    print("  model-manager init", file=sys.stderr)
    print("", file=sys.stderr)

    # Forward to model-manager if possible
    args = argv if argv is not None else sys.argv[1:]
    if args:
        cmd = ["model-manager"] + args
        print(f"Forwarding to: {' '.join(cmd)}", file=sys.stderr)
        print("", file=sys.stderr)
        try:
            return subprocess.call(cmd)
        except FileNotFoundError:
            print(
                "❌ 'model-manager' not found in PATH. Install it or run directly:",
                file=sys.stderr,
            )
            print(
                f"   python scripts/model_manager.py {' '.join(args)}",
                file=sys.stderr,
            )
            return 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
