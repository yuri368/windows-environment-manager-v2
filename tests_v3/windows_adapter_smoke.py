"""Explicit opt-in native adapter smoke. Run ONLY inside the verified DefaultBox."""
from __future__ import annotations

import argparse
import json
import uuid

from envmanager_v3.environment import WindowsEnvironment, V3EnvironmentError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verified-default-box", action="store_true", required=True)
    parser.parse_args()
    environment = WindowsEnvironment()
    name = "ENV_MANAGER_V3_ISOLATED_TEST_" + uuid.uuid4().hex.upper()
    before = environment.read("User", name)
    if before["exists"]:
        raise RuntimeError("Unexpected pre-existing test target; refusing to overwrite.")
    written = None
    try:
        raw = "  %UNEXPANDED%;A;A;;\r\n中文😀\n "
        written = environment.write("User", name, raw, "REG_EXPAND_SZ", expected=before)
        assert written["raw"] == raw and written["type"] == "REG_EXPAND_SZ"
        assert environment.read("User", name)["raw"] == raw
        try:
            environment.write("User", name, "stale", expected=before)
        except V3EnvironmentError:
            pass
        else:
            raise AssertionError("Old snapshot unexpectedly accepted.")
        written = environment.write("User", name, "", expected=written)
        assert written["exists"] and written["raw"] == "" and written["type"] == "REG_EXPAND_SZ"
        deleted = environment.delete("User", name, expected=written)
        written = None
        assert deleted["exists"] is False and deleted["raw"] is None and deleted["type"] is None
        print(json.dumps({"status": "passed", "target": name, "scope": "User",
                          "checks": ["raw-and-type", "readback", "stale-rejected", "existing-empty", "delete-and-readback"],
                          "notificationBroadcast": False, "systemScope": "not-tested"}))
    finally:
        if written is not None:
            # Restore only our exact written value, never overwrite an external change.
            environment.delete("User", name, expected=written)


if __name__ == "__main__":
    main()
