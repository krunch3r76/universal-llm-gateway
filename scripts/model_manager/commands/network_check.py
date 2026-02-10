"""Network flag checking utility for model-manager CLI."""

import argparse
import sys


def check_network_flag(args: argparse.Namespace, command: str) -> int | None:
    """
    Check if --network flag is provided for network commands.

    Returns None if OK to proceed, or exit code if should abort.
    --offline takes precedence over --network (privacy kill switch).
    """
    # Check --offline FIRST - it's a global kill switch for all network operations
    if getattr(args, "offline", False):
        print(f"❌ Cannot {command} in offline mode", file=sys.stderr)
        return 1

    if getattr(args, "network", False):
        return None

    print(
        f"❌ {command.capitalize()} requires --network flag "
        "(makes outbound connection to HuggingFace)"
    )
    print()
    print("Usage:")
    if command == "verify":
        print(f"  model-manager verify {args.path} --repo {args.repo} --network")
    elif command == "promote":
        print(f"  model-manager promote-to-verified {args.model_id} --network")
    else:
        print(f"  model-manager download {args.model_id} --network")
    print()
    print("This flag acknowledges that the command will:")
    print("  • Connect to huggingface.co")
    if command == "verify":
        print("  • Query file metadata for verification")
    elif command == "promote":
        print("  • Query file metadata for registry entry")
    else:
        print("  • Download model files to your system")
    return 1
