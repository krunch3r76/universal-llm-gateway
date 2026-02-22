# sitecustomize.py
# Automatically add project modules to Python path when Python starts.
# This enables imports like: from universal_logging import get_logger
# This file can be copied to venv's site-packages/ for automatic path setup.
import os
import sys

_this_file = os.path.abspath(__file__)

if "site-packages" in _this_file:
    # Running from venv — launch scripts set PROJECT_ROOT in the environment
    _project_root = os.environ.get("PROJECT_ROOT", "")
else:
    _project_root = os.path.dirname(_this_file)

extra = [
    os.path.join(_project_root, "libs"),
    os.path.join(_project_root, "services", "universal-stargate"),
]

for p in extra:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)
