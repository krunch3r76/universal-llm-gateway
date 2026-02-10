"""Allow running as python -m scripts.model_manager"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
