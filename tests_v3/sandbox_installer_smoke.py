"""Opt-in actual user Setup lifecycle; run ONLY through verified DefaultBox MCP.

No automatic test discovery. This runs Setup/uninstall and reads the boxed
environment. It must never be invoked on the host.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid


def run(installer, root):
    if os.name != "nt" or root.exists():
        raise RuntimeError("Require Windows and a fresh, explicitly named test directory.")
    root.mkdir(parents=True)
    app, data = root / "program", root / "operator-data"
    checks = []
    from envmanager_v3.environment import WindowsEnvironment
    environment = WindowsEnvironment()
    def environment_values():
        return sorted((item["scope"], item["name"], item["exists"], item["type"], item["raw"], item.get("display"))
                      for item in environment.list()["items"])
    before_environment = environment_values()

    def invoke(arguments, label, timeout=600):
        log = root / (label + ".stdout.log")
        with log.open("w", encoding="utf-8") as stream:
            result = subprocess.run([str(a) for a in arguments], stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=timeout)
        if result.returncode:
            setup_log = root / (label + ".setup.log")
            details = setup_log.read_text(encoding="utf-8-sig", errors="replace")[-12000:] if setup_log.exists() else log.read_text(errors="replace")[-12000:]
            raise RuntimeError(f"{label} exit {result.returncode}: {details}")

    def setup(label):
        print(f"Starting {label}", file=sys.stderr, flush=True)
        invoke([installer, "/CURRENTUSER", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                f"/DIR={app}", f"/DataDir={data}", f"/LOG={root / (label + '.setup.log')}"], label)
        if not (app / "EnvManagerV3.exe").is_file():
            raise RuntimeError("Setup returned without the installed product.")

    def launch_check(label):
        print(f"Checking {label}", file=sys.stderr, flush=True)
        invoke([app / "EnvManagerV3.exe", "--backend", "file", "--data-dir", data,
                "--check", "--no-browser"], label, timeout=60)
        result = json.loads((root / (label + ".stdout.log")).read_text(encoding="utf-8"))
        if result.get("status") != "ok":
            raise RuntimeError("Installed product did not validate its independent runtime.")

    setup("first-install")
    launch_check("first-start")
    checks.append("actual user Setup and installed EXE File check")
    marker = {"raw": " A;;A;\r\n中文 ", "invalidDraft": "[unfinished", "id": uuid.uuid4().hex}
    with sqlite3.connect(data / "state.sqlite3") as db:
        db.execute("INSERT INTO settings VALUES(?,?)", ("installer-acceptance-fixture", json.dumps(marker)))
        db.execute("INSERT INTO history VALUES(?,?,?,?)",
                   ("installer-fixture", "installer-fixture", json.dumps({"fixture": True, **marker,
                    "operationId": "installer-fixture", "status": "committed", "targets": [],
                    "intent": {"kind": "apply"}, "when": "2026-10-19T00:00:00Z"}), "2026-10-19T00:00:00Z"))
    raw_file = data / "drafts" / "invalid.toml"
    raw_file.parent.mkdir()
    raw_file.write_bytes(b"# unfinished draft\r\n[unfinished\r\n")
    raw_hash = hashlib.sha256(raw_file.read_bytes()).hexdigest()
    setup("upgrade")
    launch_check("after-upgrade")
    backups = list(data.with_name(data.name + ".upgrade-backups").glob("*/manifest.json"))
    if len(backups) < 2:
        raise RuntimeError("First install and upgrade backups were not both retained.")
    preserved_backup = False
    for manifest in backups:
        backup_db = manifest.parent / "data" / "state.sqlite3"
        if backup_db.exists():
            with sqlite3.connect(backup_db) as db:
                row = db.execute("SELECT value FROM settings WHERE key=?", ("installer-acceptance-fixture",)).fetchone()
                preserved_backup |= bool(row and json.loads(row[0]) == marker)
    if not preserved_backup:
        raise RuntimeError("Upgrade backup did not retain the pre-upgrade SQLite fixture.")
    checks.append("actual second Setup uses backup and preserves durable data")
    uninstaller = app / "unins000.exe"
    if not uninstaller.is_file():
        raise RuntimeError("Installed uninstaller is missing.")
    print("Starting uninstall", file=sys.stderr, flush=True)
    invoke([uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            f"/LOG={root / 'uninstall.setup.log'}"], "uninstall", timeout=60)
    deadline = time.monotonic() + 15
    while (app / "EnvManagerV3.exe").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if (app / "EnvManagerV3.exe").exists():
        raise RuntimeError("Uninstall left the installed executable.")
    with sqlite3.connect(data / "state.sqlite3") as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", ("installer-acceptance-fixture",)).fetchone()
        history = db.execute("SELECT body FROM history WHERE id='installer-fixture'").fetchone()
    if not row or json.loads(row[0]) != marker or not history or json.loads(history[0])["id"] != marker["id"]:
        raise RuntimeError("Uninstall lost persisted draft/history fixture.")
    if hashlib.sha256(raw_file.read_bytes()).hexdigest() != raw_hash:
        raise RuntimeError("Raw unfinished draft bytes changed.")
    if environment_values() != before_environment:
        raise RuntimeError("Boxed environment changed during installer lifecycle.")
    checks.extend(["actual uninstall removes program files and preserves draft/history fixtures",
                   "boxed User/System environment inventory unchanged"])
    return {"status": "passed", "scope": "Sandboxie DefaultBox actual user Setup lifecycle",
            "installer": str(installer), "installerSha256": hashlib.sha256(installer.read_bytes()).hexdigest(),
            "root": str(root), "checks": checks, "realUacTested": False,
            "cleanWindowsVmTested": False, "historyFixtureIsSynthetic": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verified-defaultbox-only", action="store_true", required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.installer.resolve(), args.root.resolve())
        code = 0
    except Exception as error:
        result, code = {"status": "failed", "error": str(error), "root": str(args.root)}, 1
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
