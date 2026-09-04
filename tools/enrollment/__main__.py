"""Entry point for ``python -m tools.enrollment``."""

from __future__ import annotations

from .activate import main

if __name__ == "__main__":
    raise SystemExit(main())
