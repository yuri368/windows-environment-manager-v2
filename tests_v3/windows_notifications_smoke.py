"""Opt-in native User notification check; ONLY a tool-verified DefaultBox."""
import argparse
import json
import uuid

from envmanager_v3.environment import WindowsEnvironment, V3EnvironmentError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verified-default-box", required=True, action="store_true")
    parser.parse_args()
    environment, outside = WindowsEnvironment(), WindowsEnvironment()
    names = ["ENV_MANAGER_V3_NOTIFY_TEST_" + uuid.uuid4().hex.upper() for _ in range(2)]
    owned = {}
    checks = []
    try:
        status = environment.start_changes()
        if not status["scopes"]["User"]:
            raise RuntimeError("Native User notification listener did not start: " + str(status))
        environment.take_events()
        before = [environment.read("User", name) for name in names]
        if any(item["exists"] for item in before):
            raise RuntimeError("Refusing an existing test variable.")
        owned[names[0]] = environment.write("User", names[0], "A;;A", "REG_EXPAND_SZ", expected=before[0])
        owned[names[1]] = environment.write("User", names[1], "B", "REG_SZ", expected=before[1])
        assert environment.read("User", names[0])["revision"] == owned[names[0]]["revision"]
        assert environment.read("User", names[1])["revision"] == owned[names[1]]["revision"]
        checks.append("same-scope-known-writes-preserve-other-target-revisions")
        environment.take_events()
        cursor = environment.change_cursor()
        external_before = outside.read("User", names[0])
        outside.write("User", names[0], "external-observation", expected=external_before)
        event = environment.wait_for_change(cursor, timeout=5)
        assert event["changed"] and any(item["scope"] == "User" and item["continuity"] == "unknown" for item in event["events"])
        current = environment.read("User", names[0])
        assert current["raw"] == "external-observation"
        stale = owned[names[0]]
        owned[names[0]] = current
        # The unknown key event revokes other observed targets too.
        current_second = environment.read("User", names[1])
        assert current_second["raw"] == "B"
        assert current_second["revision"] != owned[names[1]]["revision"]
        owned[names[1]] = current_second
        try:
            environment.write("User", names[0], "forbidden-stale-write", expected=stale)
        except V3EnvironmentError:
            checks.append("unknown-native-event-invalidates-stale-plan")
        else:
            raise AssertionError("Old snapshot was accepted after a native change.")
        for name in names:
            removed = environment.delete("User", name, expected=owned[name])
            owned.pop(name)
            assert not removed["exists"]
        checks.append("conditional-cleanup-readback")
        print(json.dumps({"status": "passed", "checks": checks, "scope": "User", "notificationBroadcast": False,
                          "systemWrites": False, "listener": status}, ensure_ascii=True))
    finally:
        try:
            for name, snapshot in owned.items():
                environment.delete("User", name, expected=snapshot)
        finally:
            environment.close()
            outside.close()


if __name__ == "__main__":
    main()
