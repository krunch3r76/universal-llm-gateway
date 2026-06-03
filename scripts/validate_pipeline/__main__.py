"""Allow running as: python -m scripts.validate_pipeline"""

from ._paths import ensure_import_paths
from .main import main

if __name__ == "__main__":
    ensure_import_paths()
    main()
