"""Actual abrupt subprocess termination at durable boundaries, File backend only."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from envmanager_v3.service import Service


def crash_worker(directory, phase):
    service = Service(directory)
    config = '[Tool.P]\nFIRST={default_value="new",scope="User",mode="replace"}\n'
    if phase == "compensating":
        config += 'SECOND={default_value="new-second",scope="User",mode="replace"}\n'
    state = service.import_workspace({"text": config})
    service.environment.write("User", "FIRST", "old")
    if phase == "compensating":
        service.environment.write("User", "SECOND", "old-second")
    records = [v["recordId"] for v in state["apps"][0]["profiles"][0]["vars"]]
    receipt = service.plan({"kind": "apply", "recordIds": records})
    reviewed = service.review(receipt["planId"], {"version": receipt["version"], "digest": receipt["digest"]})
    request = {**reviewed, "idempotencyKey": "crash-key"}
    (Path(directory) / "request.json").write_text(json.dumps(request), encoding="utf-8")
    import os

    if phase == "write-intent":
        save = service.executor._save

        def after_intent(operation, *args, **kwargs):
            result = save(operation, *args, **kwargs)
            if any(t.get("status") == "writing" for t in operation["targets"]):
                os._exit(73)
            return result

        service.executor._save = after_intent
    elif phase in ("write-before-ack", "compensating"):
        write = service.environment.write

        def interrupted_write(scope, name, raw, *args, **kwargs):
            if phase == "compensating" and name == "SECOND":
                raise OSError("injected second-target failure")
            result = write(scope, name, raw, *args, **kwargs)
            if phase == "write-before-ack" or phase == "compensating" and raw == "old":
                os._exit(73)
            return result

        service.environment.write = interrupted_write
    elif phase == "verified-before-commit":
        finish = service.executor._finish

        def interrupted_finish(operation, status, **kwargs):
            if status == "committed":
                os._exit(73)
            return finish(operation, status, **kwargs)

        service.executor._finish = interrupted_finish
    elif phase == "history-before-sql-commit":
        append = service.database.append_history

        def interrupted_history(*args, **kwargs):
            append(*args, **kwargs)
            os._exit(73)

        service.database.append_history = interrupted_history
    else:
        raise ValueError(phase)
    service.execute(receipt["planId"], request)
    raise RuntimeError("The requested crash boundary was not reached.")


class ProcessRecoveryTests(unittest.TestCase):
    def test_abrupt_exit_reopens_durable_journal_and_never_replays_environment(self):
        phases = {"write-intent": "old", "write-before-ack": "new", "verified-before-commit": "new",
                  "history-before-sql-commit": "new", "compensating": "old"}
        for phase, expected in phases.items():
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run([sys.executable, "-B", "-m", "tests_v3.test_process_recovery", "--worker", directory, phase],
                    cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.assertEqual(result.returncode, 73, result.stderr.decode(errors="replace"))
                environment_file = Path(directory) / "environment.json"
                persisted = environment_file.read_bytes()
                request = json.loads((Path(directory) / "request.json").read_text(encoding="utf-8"))
                service = Service(directory)
                try:
                    self.assertEqual(service.environment.read("User", "FIRST")["raw"], expected)
                    original = service.database.find_execution("crash-key")
                    self.assertEqual(original["status"], "recovery_required", original)
                    self.assertEqual(service.history({})["items"], [])
                    replay = service.execute(request["planId"], request)
                    self.assertEqual(replay["status"], "recovery_required", replay)
                    self.assertEqual(replay["operationId"], original["id"])
                    self.assertEqual(environment_file.read_bytes(), persisted)
                    if phase == "compensating":
                        self.assertEqual(service.environment.read("User", "SECOND")["raw"], "old-second")
                finally:
                    service.close()


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker":
        crash_worker(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
