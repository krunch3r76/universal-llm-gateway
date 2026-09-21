# sitecustomize.py
# Automatically add project modules to Python path when Python starts.
# This enables imports like: from universal_logging import get_logger
# This file can be copied to venv's site-packages/ for automatic path setup.
import os
import sys

_this_file = os.path.abspath(__file__)
# Last-resort when a venv copy has neither PROJECT_ROOT nor ULG_REPO (hub and
# Jupiter NFS checkout). Spawn scripts should set one of those; do not invent
# a libs/services symlink to fake the package.
_HUB_CHECKOUT = "/mnt/torus/projects/universal-llm-gateway"

if "site-packages" in _this_file:
    _project_root = (
        os.environ.get("PROJECT_ROOT")
        or os.environ.get("ULG_REPO")
        or _HUB_CHECKOUT
    )
else:
    _project_root = os.path.dirname(_this_file)

extra = [
    os.path.join(_project_root, "libs"),
    os.path.join(_project_root, "services", "universal-stargate"),
    # Checkout root last in this list → first on sys.path (insert 0). Needed
    # so `import services` is the real tree, not a libs/ shadow.
    _project_root,
]

for p in extra:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)
