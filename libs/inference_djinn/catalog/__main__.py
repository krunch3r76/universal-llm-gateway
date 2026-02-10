"""Allow running catalog CLI as python -m inference_djinn.catalog"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
