#!/usr/bin/env python3
"""
Install vLLM dependencies from wheel METADATA.

Since vLLM is installed with --no-deps to preserve nightly PyTorch (when building
from source), this script extracts dependencies from the wheel's METADATA and
installs them.

When using pre-built wheels, PyTorch should NOT be protected - let the wheel
dictate the PyTorch version for binary compatibility.

Usage:
    # From wheel file (preferred - called by builder after wheel is built)
    python auto_install_dependencies.py /path/to/vllm-*.whl

    # With --target for Docker builds (install to specific directory)
    python auto_install_dependencies.py --target=/build/packages /path/to/vllm-*.whl

    # Protect PyTorch when building from source (prevents overwriting nightly)
    python auto_install_dependencies.py --protect-pytorch /path/to/vllm-*.whl

    # Fallback: from installed package (if wheel not available)
    python auto_install_dependencies.py
"""

import argparse
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path


def extract_dependencies_from_wheel(wheel_path: Path) -> list[str]:
    """
    Extract dependency requirements from a wheel file's METADATA.

    Args:
        wheel_path: Path to the wheel file

    Returns:
        List of requirement strings (e.g., ["numpy>=1.20", "torch>=2.0"])
    """
    dependencies: list[str] = []

    try:
        with zipfile.ZipFile(wheel_path, "r") as wheel:
            # METADATA is in .dist-info/METADATA
            metadata_files = [f for f in wheel.namelist() if f.endswith("/METADATA")]

            if not metadata_files:
                print(f"⚠️  No METADATA file found in wheel {wheel_path}")
                return dependencies

            metadata_file = metadata_files[0]
            metadata_content = wheel.read(metadata_file).decode("utf-8")

            # Parse Requires-Dist lines
            for line in metadata_content.split("\n"):
                line = line.strip()
                if line.startswith("Requires-Dist:"):
                    req = line[len("Requires-Dist:") :].strip()
                    if req:
                        dependencies.append(req)

    except Exception as e:
        print(f"⚠️  Error extracting dependencies from wheel: {e}")

    return dependencies


def get_dependencies_from_pip_show() -> list[str]:
    """
    Fallback: get dependencies from installed vLLM package.
    Only returns package names without version constraints.
    """
    dependencies: list[str] = []

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", "vllm"],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if line.startswith("Requires:"):
                    requires_str = line.split(":", 1)[1].strip()
                    if requires_str:
                        dependencies = [
                            pkg.strip()
                            for pkg in requires_str.split(",")
                            if pkg.strip()
                        ]
                    break

    except Exception as e:
        print(f"⚠️  Error getting dependencies from pip: {e}")

    return dependencies


def install_dependencies(
    dependencies: list[str],
    target_dir: str | None = None,
    protect_pytorch: bool = False,
) -> bool:
    """
    Install dependencies, optionally skipping protected packages (torch, etc).

    Args:
        dependencies: List of requirement strings
        target_dir: Optional target directory for pip install --target
        protect_pytorch: If True, skip torch/torchvision/torchaudio (for source builds)
                        If False, allow PyTorch installation (for pre-built wheels)

    Returns:
        True if successful, False otherwise
    """
    if not dependencies:
        print("⚠️  No dependencies to install")
        return False

    print(f"📋 Found {len(dependencies)} dependencies:")
    for dep in dependencies[:10]:
        print(f"   - {dep}")
    if len(dependencies) > 10:
        print(f"   ... and {len(dependencies) - 10} more")

    if target_dir:
        print(f"📁 Target directory: {target_dir}")

    # Environment without pip hash requirements
    env = os.environ.copy()
    env.pop("PIP_CONFIG_FILE", None)
    env.pop("PIP_CONFIG_DIR", None)

    # Protected packages (only when building from source)
    protected_packages = (
        {"torch", "torchvision", "torchaudio"} if protect_pytorch else set()
    )

    if protect_pytorch:
        print("🔒 PyTorch protection enabled (source build mode)")
    else:
        print("📦 PyTorch protection disabled (using wheel's PyTorch dependency)")

    install_list: list[str] = []
    for dep in dependencies:
        # Extract package name (before any version specifier or marker)
        # Handle both "package>=1.0" and "package>=1.0; python_version>'3.8'"
        base_dep = dep.split(";")[0].strip() if ";" in dep else dep
        pkg_name_match = re.match(r"^([a-zA-Z0-9_.-]+)", base_dep)

        if pkg_name_match:
            pkg_name = pkg_name_match.group(1).lower()
            if pkg_name in protected_packages:
                print(f"   ⏭️  Skipping protected package: {pkg_name}")
                continue

        # Remove environment markers for installation
        clean_dep = base_dep

        # Handle build-specific versions (like xformers==0.0.33+5d4b92a5)
        # Relax to base version
        if re.search(r"==.*\+[a-f0-9]+", clean_dep):
            base_match = re.match(r"^([a-zA-Z0-9_.-]+)==([0-9.]+)", clean_dep)
            if base_match:
                pkg, version = base_match.groups()
                clean_dep = f"{pkg}>={version}"

        install_list.append(clean_dep)

    if not install_list:
        print("✅ All dependencies are protected packages (already installed)")
        return True

    # Build base pip command
    base_pip_cmd = [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--quiet"]
    if target_dir:
        base_pip_cmd.extend(["--target", target_dir])

    # Install in batches
    print(f"\n📦 Installing {len(install_list)} dependencies...")
    batch_size = 10
    failed_packages: list[str] = []

    for i in range(0, len(install_list), batch_size):
        batch = install_list[i : i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (len(install_list) - 1) // batch_size + 1
        print(f"📦 Installing batch {batch_num}/{total_batches}...")

        result = subprocess.run(
            base_pip_cmd + batch,
            capture_output=True,
            text=True,
            env=env,
        )

        if result.returncode != 0:
            # Try each package individually
            for dep in batch:
                pkg_name = re.split(r"[<=>!]", dep)[0].strip()
                individual_result = subprocess.run(
                    base_pip_cmd + [dep],
                    capture_output=True,
                    text=True,
                    env=env,
                )
                if individual_result.returncode != 0:
                    # Try just the package name
                    simple_result = subprocess.run(
                        base_pip_cmd + [pkg_name],
                        capture_output=True,
                        text=True,
                        env=env,
                    )
                    if simple_result.returncode != 0:
                        failed_packages.append(dep)

    if failed_packages:
        print(f"\n⚠️  {len(failed_packages)} packages could not be installed:")
        for pkg in failed_packages[:5]:
            print(f"   - {pkg}")
        if len(failed_packages) > 5:
            print(f"   ... and {len(failed_packages) - 5} more")
        print("   These may be optional or platform-specific")

    # NOTE: NumPy version enforcement is handled by post-processing steps:
    # - Docker: FINAL step in Dockerfile.gpu enforces numpy<2.3
    # - Bare-metal: build_vllm.sh post-processes after build

    return len(failed_packages) == 0


def main():
    """
    Install vLLM dependencies.

    Usage:
        python auto_install_dependencies.py [--target=DIR] [wheel_path]

    If wheel_path is provided, extracts dependencies from the wheel's METADATA.
    Otherwise, falls back to `pip show vllm` (requires vllm already installed).
    """
    parser = argparse.ArgumentParser(
        description="Install vLLM dependencies from wheel METADATA",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Install from wheel (preferred)
  %(prog)s /path/to/vllm-*.whl

  # Install to specific directory (for Docker builds)
  %(prog)s --target=/build/packages /path/to/vllm-*.whl

  # Fallback: from installed package
  %(prog)s
        """,
    )
    parser.add_argument(
        "--target",
        "-t",
        metavar="DIR",
        help="Install packages into DIR (pip --target)",
    )
    parser.add_argument(
        "--protect-pytorch",
        action="store_true",
        help="Skip torch/torchvision/torchaudio (for source builds with pre-installed PyTorch)",
    )
    parser.add_argument(
        "wheel_path",
        nargs="?",
        type=Path,
        help="Path to vLLM wheel file",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("📦 vLLM Dependency Installer")
    print("=" * 70)
    print()

    dependencies: list[str] = []

    # Check if wheel path provided as argument
    if args.wheel_path:
        wheel_path = args.wheel_path
        if wheel_path.exists() and wheel_path.suffix == ".whl":
            print(f"📦 Extracting dependencies from wheel: {wheel_path.name}")
            dependencies = extract_dependencies_from_wheel(wheel_path)
        else:
            print(f"⚠️  Invalid wheel path: {wheel_path}")
            sys.exit(1)
    else:
        # Fallback to pip show
        print("📦 Getting dependencies from installed vLLM package...")
        dependencies = get_dependencies_from_pip_show()

    if not dependencies:
        print("⚠️  No dependencies found")
        print("   Provide a wheel path or ensure vLLM is installed")
        sys.exit(1)

    success = install_dependencies(
        dependencies, target_dir=args.target, protect_pytorch=args.protect_pytorch
    )

    print()
    print("=" * 70)
    if success:
        print("✅ vLLM dependencies installed successfully!")
    else:
        print("⚠️  vLLM dependencies partially installed (some packages failed)")
        print("   vLLM may still work - check for import errors at runtime")
    print("=" * 70)

    # Always exit 0 - failed packages are optional/platform-specific
    # vLLM can still work without them, let Docker build continue
    sys.exit(0)


if __name__ == "__main__":
    main()
