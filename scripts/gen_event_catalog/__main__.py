"""Allow running as: python -m scripts.gen_event_catalog"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
