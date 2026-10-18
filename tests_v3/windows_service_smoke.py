"""Native User service acceptance. ONLY run in a tool-verified DefaultBox."""
import argparse
import json
import tempfile
import uuid

from envmanager_v3.environment import WindowsEnvironment
from envmanager_v3.service import Service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verified-default-box", required=True, action="store_true")
    parser.parse_args()
    name = "ENV_MANAGER_V3_SERVICE_TEST_" + uuid.uuid4().hex.upper()
    raw = " A;A;;中文\r\n尾部 "
    notifications = []
    environment = WindowsEnvironment(notifier=lambda: notifications.append("injected") or True)
    before = environment.read("User", name)
    if before["exists"]:
        raise RuntimeError("Refusing a pre-existing native target.")
    owned = None
    service = None
    checks = []
    try:
        with tempfile.TemporaryDirectory(prefix="EnvManagerV3-Service-") as directory:
            service = Service(directory, environment=environment, monitor_environment=True)
            assert environment.changes_status()["scopes"]["User"]
            state = service.import_workspace({"text":
                '[Native.P]\n' + name + '={scope="User",mode="replace",default_value=" A;A;;中文\\r\\n尾部 "}\n'})
            record = state["apps"][0]["profiles"][0]["vars"][0]["recordId"]

            def apply(receipt):
                reviewed = service.review(receipt["planId"], {"version": receipt["version"], "digest": receipt["digest"]})
                assert "reviewToken" in reviewed, reviewed
                result = service.execute(receipt["planId"], {**reviewed, "idempotencyKey": uuid.uuid4().hex})
                assert result["status"] == "committed", result
                return result

            receipt = service.plan({"kind": "apply", "recordIds": [record]})
            assert not environment.read("User", name)["exists"]
            apply(receipt)
            owned = environment.read("User", name)
            assert owned["raw"] == raw and owned["type"] == "REG_EXPAND_SZ"
            checks.append("review-bound-native-User-apply-raw-type-readback")
            edit = service.open_edit({"scope": "User", "name": name})
            rows = edit["rows"]
            second_id = rows[1]["id"]
            rows[1]["value"] = "改第二项"
            service.update_edit(edit["sessionId"], {"rows": rows, "expectedRevision": 0})
            receipt = service.plan({"kind": "manual", "editSessionId": edit["sessionId"]})
            assert any(op.get("elementId") == second_id and op["action"] == "REPLACE" for op in receipt["targets"][0]["plan"]["operations"])
            apply(receipt)
            owned = environment.read("User", name)
            assert owned["raw"] == " A;改第二项;;中文\r\n尾部 "
            checks.append("stable-occurrence-manual-edit-preserves-empty-CRLF")
            service.close()
            environment = WindowsEnvironment(notifier=lambda: notifications.append("injected") or True)
            service = Service(directory, environment=environment, monitor_environment=True)
            assert len(service.history({})["items"]) == 2
            assert service.bootstrap()["apps"][0]["profiles"][0]["vars"][0]["recordId"] == record
            owned = environment.read("User", name)
            checks.append("restart-retains-identities-and-durable-native-history")
            edit = service.open_edit({"scope": "User", "name": name})
            service.update_edit(edit["sessionId"], {"deleteVariable": True, "expectedRevision": 0})
            apply(service.plan({"kind": "manual", "editSessionId": edit["sessionId"]}))
            owned = None
            assert not environment.read("User", name)["exists"]
            assert len(service.history({})["items"]) == 3
            assert len(notifications) == 3
            checks.append("explicit-native-delete-readback-and-persistent-history")
            service.close()
            service = None
            print(json.dumps({"status": "passed", "checks": checks, "target": name, "scope": "User",
                              "notificationBroadcast": "injected-not-native", "systemWrites": False,
                              "uac": "not-tested"}, ensure_ascii=True))
    finally:
        if owned is not None:
            environment.delete("User", name, expected=owned)
        if service is not None:
            service.close()
        else:
            environment.close()


if __name__ == "__main__":
    main()
