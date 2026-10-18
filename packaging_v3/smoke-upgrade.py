"""Exercise installer helper choreography against isolated files, never run Setup."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time


def run(executable, evidence, *, python=False):
    evidence.mkdir(parents=True, exist_ok=False)
    data = evidence / "operator-data"
    control = evidence / "lease-session"
    prefix = [str(executable)] + (["-B", "-m", "envmanager_v3"] if python else [])
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    if not python:
        env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")

    def invoke(arguments, expected=0):
        result = subprocess.run(prefix + arguments, capture_output=True, env=env, timeout=45)
        # Frozen Python ignores PYTHONUTF8 for stdio initialization on Windows.
        # Keep raw diagnostics and decode the console codepage only where needed.
        def decode(value):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                import ctypes
                return value.decode(f"cp{ctypes.windll.kernel32.GetACP()}")
        result.stdout, result.stderr = decode(result.stdout), decode(result.stderr)
        if result.returncode != expected:
            raise RuntimeError(f"Helper command {arguments[0]} exited {result.returncode}: {result.stdout} {result.stderr}")
        return result

    identity = json.loads(invoke(["--upgrade-helper", "check-user"]).stdout)
    if identity.get("result") != "passed" or data.exists() or control.exists():
        raise RuntimeError("Identity preflight failed or created product state")
    product = ["--backend", "file", "--data-dir", str(data), "--check", "--no-browser"]
    invoke(product)
    (data / "workspaces").mkdir(parents=True, exist_ok=True)
    (data / "workspaces" / "raw.toml").write_bytes(b"# raw draft\r\n[unfinished\r\n")
    (data / "recovery-evidence.bin").write_bytes(bytes(range(256)))
    with sqlite3.connect(data / "state.sqlite3") as db:
        db.execute("INSERT INTO settings VALUES(?,?)", ("upgrade-smoke-fixture", json.dumps({"raw": " A;;A;\r\n", "invalidDraft": "[unfinished"})))

    def inventory():
        return {p.relative_to(data).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in data.rglob("*") if p.is_file() and not p.name.endswith((".lock", "-shm"))}

    baseline = inventory()
    log = (evidence / "hold.log").open("w", encoding="utf-8")
    hold = subprocess.Popen(prefix + ["--upgrade-helper", "hold", "--require-unelevated", "--data-dir", str(data),
                            "--target-version", "3.0.0.dev1", "--session-dir", str(control), "--lease-seconds", "120",
                            "--parent-pid", str(os.getpid()), "--prepare-timeout", "30"],
                            stdout=log, stderr=subprocess.STDOUT, env=env)
    checks = ["synchronous check-user passed before any product data/session creation"]
    try:
        deadline = time.monotonic() + 30
        while not (control / "ready.json").is_file():
            if hold.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Upgrade helper did not publish ready; inspect hold.log")
            time.sleep(0.05)
        ready = json.loads((control / "ready.json").read_text(encoding="utf-8"))
        status = ["--upgrade-helper", "status", "--session-dir", str(control), "--timeout", "2"]
        invoke(status)
        checks.append("fresh status handshake before replacement")
        blocked = invoke(product, expected=1)
        if "此数据目录已有 V3 实例运行" not in blocked.stderr + blocked.stdout:
            raise RuntimeError("Second instance failed for an unexpected reason: " + blocked.stderr)
        checks.append("real product second instance blocked during upgrade")
        manifest = json.loads((Path(ready["backup_path"]) / "manifest.json").read_text(encoding="utf-8"))
        for relative in ("workspaces/raw.toml", "recovery-evidence.bin"):
            backup_file = Path(ready["backup_path"]) / "data" / relative
            if hashlib.sha256(backup_file.read_bytes()).hexdigest() != baseline[relative]:
                raise RuntimeError(f"Backup content mismatch: {relative}")
        # Inspect the immutable snapshot without creating WAL/SHM sidecars in it.
        backup_db = Path(ready["backup_path"]) / "data" / "state.sqlite3"
        with sqlite3.connect(backup_db.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
            if db.execute("SELECT value FROM settings WHERE key='upgrade-smoke-fixture'").fetchone()[0] != json.dumps({"raw": " A;;A;\r\n", "invalidDraft": "[unfinished"}):
                raise RuntimeError("SQLite backup lost draft fixture")
        checks.append("real SQLite backup and raw draft/recovery bytes preserved")
        old_program = evidence / "simulated-program-files"
        old_program.mkdir()
        marker = old_program / "program-version.txt"
        marker.write_text("old", encoding="ascii")
        # This simulates the exact file-replacement gate. It never executes an installer.
        invoke(status)
        marker.write_text("new", encoding="ascii")
        invoke(status)
        invoke(product, expected=1)
        checks.append("lease stayed live and blocked product across simulated replacement")
        invoke(["--upgrade-helper", "finish", "--session-dir", str(control), "--outcome", "installed", "--timeout", "2"])
        if hold.wait(timeout=10) != 0:
            raise RuntimeError("Upgrade holder did not finish cleanly")
        if inventory() != baseline:
            raise RuntimeError("Installer helper changed source data")
        invoke(product)
        checks.append("finish installed released lock; source data unchanged; product reopened")
        result = {"status": "passed", "scope": "isolated helper choreography; simulated program-file replacement",
                  "backend": "file", "hostInstallerExecuted": False, "nativeUacOrInstallAclTested": False,
                  "backupId": ready["backup_id"], "backupManifestSha256": ready["manifest_sha256"],
                  "sourceFileCount": len(baseline), "checks": checks}
        (evidence / "upgrade-smoke-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result))
    finally:
        if hold.poll() is None:
            try:
                invoke(["--upgrade-helper", "finish", "--session-dir", str(control), "--outcome", "cancelled", "--timeout", "2"])
                hold.wait(timeout=5)
            except Exception:
                hold.terminate()
                hold.wait(timeout=10)
        log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--python", action="store_true")
    args = parser.parse_args()
    run(args.exe.resolve(), args.evidence_dir.resolve(), python=args.python)
