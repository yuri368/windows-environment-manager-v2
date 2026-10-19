"""Opt-in browser fixture with real FileEnvironment/Service/Executor history.

From the repository root (Python 3.11+, existing Node planner required):
    python -B -m tests_v3.browser_failure_acceptance --data-dir <fresh-path>
    python -B -m tests_v3.browser_failure_acceptance --data-dir <fresh-path> --seed-only

The directory must not exist, including when empty. It is retained on exit or
failure and is never reused/deleted. Seed-only prints a token-free JSON summary
without creating an HTTP server. To launch later, use another fresh directory.
Normal launch prints the authenticated browser URL and serves until Ctrl+C.

Only the persistent File backend is used. Faults are deliberately injected at
File adapter write boundaries; these are not native Windows/Registry failures.
All plans, reviews, execution journals, contributions and history are produced
by the actual Service and its Executor, never inserted as fabricated DB rows.
The final unresolved operation intentionally leaves the browser recovery entry
available and blocks ordinary execution until reviewed recovery is performed.
"""

import argparse
import json
from pathlib import Path
import uuid

from envmanager_v3.environment import FileEnvironment
from envmanager_v3.service import Service


CONFIG = '''[Compensated]
default_option = "Fixture"
[Compensated.Fixture]
V3_FAIL_COMP_A = { default_value = "new-a", scope = "User", mode = "replace", editable = true }
V3_FAIL_COMP_B = { default_value = "new-b", scope = "User", mode = "replace", editable = true }
[SharedOwner]
default_option = "Fixture"
[SharedOwner.Fixture]
V3_FAIL_SHARED = { default_value = "A;B", scope = "User", mode = "+append", editable = true }
[SharedPeer]
default_option = "Fixture"
[SharedPeer.Fixture]
V3_FAIL_SHARED = { default_value = "A", scope = "User", mode = "+append", editable = true }
[RecoveryRequired]
default_option = "Fixture"
[RecoveryRequired.Fixture]
V3_FAIL_RECOVERY_A = { default_value = "new-a", scope = "User", mode = "replace", editable = true }
V3_FAIL_RECOVERY_B = { default_value = "new-b", scope = "User", mode = "replace", editable = true }
'''


class FaultFileEnvironment(FileEnvironment):
    """One-shot, file-only failure before the selected write.

    For recovery, a separate FileEnvironment writer changes an already verified
    target before the failure, so compensation cannot overwrite that external value.
    Disarm before raising: later browser recovery uses the normal File adapter.
    """

    def __init__(self, path):
        super().__init__(path)
        self.fail_on = None
        self.external_target = None
        self.fault_count = 0

    def write(self, scope, name, raw, type=None, *, expected=None):
        if scope == "User" and name == self.fail_on:
            external_target = self.external_target
            self.fail_on = self.external_target = None
            self.fault_count += 1
            if external_target:
                FileEnvironment(self.path).write("User", external_target, "external-fixture-value")
            raise OSError("File acceptance fixture: injected second-target write failure")
        return super().write(scope, name, raw, type, expected=expected)


def require(condition, message):
    # Do not dump review tokens, receipts or an entire bootstrap on failure.
    if not condition:
        raise RuntimeError("File failure fixture verification failed: " + message)


def execute(service, intent, expected_status, *, before_execute=None):
    receipt = service.plan(intent)
    require(receipt.get("status") == "ready", "plan is not ready")
    reviewed = service.review(receipt["planId"], {
        key: receipt[key] for key in ("version", "digest")
    })
    require("reviewToken" in reviewed, "review did not authorize the frozen plan")
    if before_execute:
        before_execute(receipt)
    request_key = uuid.uuid4().hex
    result = service.execute(receipt["planId"], {
        **reviewed, "idempotencyKey": request_key,
    })
    require(result.get("status") == expected_status,
            f"expected {expected_status}, got {result.get('status')}")
    persisted = service.database.find_execution(request_key)
    require(persisted is not None, "execution was not persisted")
    history = next((item for item in service.history({})["items"]
                    if item["id"] == result["operationId"]), None)
    require(history is not None, "execution has no real history entry")
    # Service projects committed-with-protected-removals as partial intent;
    # the Executor journal and history honestly retain their committed status.
    durable_status = "committed" if expected_status == "applied_partial_intent" else expected_status
    require(persisted["status"] == durable_status, "unexpected journal status")
    require(persisted["body"]["result"]["status"] == durable_status,
            "unexpected persisted result status")
    require(history["status"] == durable_status, "unexpected history status")
    return {
        "operationId": result["operationId"],
        "serviceStatus": result["status"],
        "executionStatus": persisted["status"],
        "historyStatus": history["status"],
        "issueReasons": sorted({issue["reason"] for target in result["targets"]
                                for issue in target["plan"].get("issues", [])
                                if "reason" in issue}),
        "historyTargets": [target["name"] for target in history["targets"]],
    }


def seed(service, environment):
    for prefix in ("V3_FAIL_COMP", "V3_FAIL_RECOVERY"):
        for suffix in ("A", "B"):
            environment.write("User", f"{prefix}_{suffix}", f"old-{suffix.lower()}", "REG_SZ")
    environment.write("User", "V3_FAIL_SHARED", "BASE", "REG_EXPAND_SZ")
    state = service.import_workspace({"text": CONFIG, "filename": "file-failure-fixture.toml"})
    records = {app["name"]: [variable["recordId"]
                            for variable in app["profiles"][0]["vars"]]
               for app in state["apps"]}

    def arm(receipt, prefix, *, external=False):
        require([target["name"] for target in receipt["targets"]]
                == [f"{prefix}_A", f"{prefix}_B"], "unexpected failure target ordering")
        environment.fail_on = f"{prefix}_B"
        environment.external_target = f"{prefix}_A" if external else None

    compensated = execute(service, {"kind": "apply", "recordIds": records["Compensated"]},
                          "compensated",
                          before_execute=lambda receipt: arm(receipt, "V3_FAIL_COMP"))
    for suffix in ("A", "B"):
        require(environment.read("User", f"V3_FAIL_COMP_{suffix}")["raw"] == f"old-{suffix.lower()}",
                "compensated batch did not retain original values")
    require(compensated["historyTargets"] == ["V3_FAIL_COMP_A"],
            "compensation history must contain only the verified first write")
    require(not service.recovery()["items"], "compensated failure left a recovery obligation")

    shared_apply = execute(service, {
        "kind": "apply", "recordIds": records["SharedOwner"] + records["SharedPeer"],
    }, "committed")
    partial = execute(service, {"kind": "undo", "recordIds": records["SharedOwner"]},
                      "applied_partial_intent")
    shared = environment.read("User", "V3_FAIL_SHARED")
    require(shared["raw"].split(";").count("A") == 1
            and "BASE" in shared["raw"].split(";") and "B" not in shared["raw"].split(";"),
            "shared removal must keep A/BASE and actually remove unshared B")
    require("shared" in partial["issueReasons"], "partial intent lacks real shared protection")

    # Always last: ordinary plans are blocked once a recovery obligation exists.
    recovery = execute(service, {"kind": "apply", "recordIds": records["RecoveryRequired"]},
                       "recovery_required",
                       before_execute=lambda receipt: arm(receipt, "V3_FAIL_RECOVERY", external=True))
    require(environment.read("User", "V3_FAIL_RECOVERY_A")["raw"] == "external-fixture-value",
            "compensation overwrote the external file value")
    require(environment.read("User", "V3_FAIL_RECOVERY_B")["raw"] == "old-b",
            "failing second target changed")
    require(environment.fault_count == 2 and environment.fail_on is None,
            "expected two disarmed one-shot faults")
    pending = service.bootstrap()["recovery"]
    require(len(pending) == 1 and pending[0]["operationId"] == recovery["operationId"]
            and pending[0]["status"] == "recovery_required", "missing browser recovery obligation")
    require(len(service.history({})["items"]) == 4, "unexpected number of real history entries")
    return {
        "backend": "file",
        "fixtureScope": "Injected File adapter failures; not native Windows/Registry failures",
        "dataDir": str(service.data_dir),
        "operations": {"compensated": compensated, "sharedApply": shared_apply,
                       "partialIntent": partial, "recoveryRequired": recovery},
        "historyCount": 4,
        "recovery": [{"operationId": item["operationId"], "status": item["status"]}
                     for item in pending],
        "environment": [{key: item[key] for key in ("scope", "name", "raw", "type")}
                        for item in environment.list()["items"]],
        "faultInjectionArmed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True,
                        help="Fresh disposable directory that does not yet exist; never deleted")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--seed-only", action="store_true",
                        help="Verify real persisted history, print token-free JSON, exit without HTTP")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    data_dir = args.data_dir.resolve()
    try:
        # Atomic exclusive creation also rejects existing empty directories.
        # Fresh-only ownership needs no native instance-lock implementation.
        data_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("Require a fresh directory; never reuse or delete acceptance state.")

    environment = FaultFileEnvironment(data_dir / "environment.json")
    service = Service(data_dir, environment=environment)
    try:
        summary = seed(service, environment)
        if args.seed_only:
            print(json.dumps({"status": "seeded", **summary}, ensure_ascii=True), flush=True)
            return 0

        from envmanager_v3.http_server import create_server

        server = create_server(service, Path(__file__).resolve().parent.parent, args.port)
        try:
            print(json.dumps({"status": "listening", "url": server.launch_url, **summary},
                             ensure_ascii=True), flush=True)
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
