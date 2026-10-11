"""Windowed executable entry point; runtime imports are collected by PyInstaller."""

from envmanager.main import main

if __name__ == "__main__":
    raise SystemExit(main())
