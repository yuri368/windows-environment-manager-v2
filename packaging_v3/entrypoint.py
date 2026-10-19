"""Frozen launcher; keep relative imports inside the real V3 package."""
from envmanager_v3.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
