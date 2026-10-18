"""Check and start the same product exclusively with a persistent FileEnvironment."""
from __future__ import annotations

import argparse
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener


def run(executable, evidence_dir, *, python=False):
    evidence_dir.mkdir(parents=True, exist_ok=False)
    data = evidence_dir / "data"
    command = [str(executable)] + (["-B", "-m", "envmanager_v3"] if python else [])
    command += ["--backend", "file", "--data-dir", str(data), "--environment-file",
                str(data / "environment.json"), "--no-browser", "--port", "0"]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    if not python:
        # A developer Node/Python on PATH must not make an incomplete bundle pass.
        env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    check = subprocess.run(command + ["--check"], capture_output=True, text=True,
                           encoding="utf-8", timeout=45, env=env)
    (evidence_dir / "check.stdout.txt").write_text(check.stdout, encoding="utf-8")
    (evidence_dir / "check.stderr.txt").write_text(check.stderr, encoding="utf-8")
    if check.returncode:
        raise RuntimeError(f"--check failed ({check.returncode}): {check.stderr}")
    checked = json.loads(check.stdout)
    if checked.get("status") != "ok":
        raise RuntimeError("Unexpected --check reply")
    result = {"backend": "file", "check": checked, "developerPathRemoved": not python, "starts": []}
    for attempt in range(2):
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", env=env)
        replies = queue.Queue()
        threading.Thread(target=lambda: replies.put(process.stdout.readline()), daemon=True).start()
        try:
            line = replies.get(timeout=45)
            started = json.loads(line)
            if started.get("status") != "listening" or started.get("backend") != "file":
                raise RuntimeError(f"Unexpected start reply: {line}")
            url = urlsplit(started["url"])
            if url.hostname != "127.0.0.1" or url.scheme != "http":
                raise RuntimeError("Server did not bind loopback")
            origin = f"{url.scheme}://{url.netloc}"
            opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

            def get(path):
                with opener.open(origin + path, timeout=10) as response:
                    return response.read()

            def post(path, payload, csrf=None):
                headers = {"Origin": origin, "Content-Type": "application/json"}
                if csrf:
                    headers["X-CSRF-Token"] = csrf
                request = Request(origin + path, data=json.dumps(payload).encode(), headers=headers)
                with opener.open(request, timeout=10) as response:
                    return json.load(response)

            health = json.loads(get("/health"))
            if health != {"status": "ok", "product": "EnvManagerV3"}:
                raise RuntimeError("Unexpected health response")
            ticket = parse_qs(url.fragment)["ticket"][0]
            token = post("/api/v1/session/exchange", {"ticket": ticket})["csrfToken"]
            bootstrap = json.loads(get("/api/v1/bootstrap"))
            if bootstrap.get("mode") != "isolated" or bootstrap["workspace"]["id"] != checked["configuration"]["id"]:
                failed = {**result, "status": "failed", "health": health,
                          "observedMode": bootstrap.get("mode"),
                          "observedWorkspaceId": bootstrap.get("workspace", {}).get("id"),
                          "error": "Wrong backend or workspace failed to survive restart"}
                (evidence_dir / "smoke-result.json").write_text(json.dumps(failed, indent=2) + "\n", encoding="utf-8")
                raise RuntimeError(failed["error"])
            html = get("/").decode("utf-8")
            if "product-app.js" not in html or "product-runtime.js" not in html:
                raise RuntimeError("Production HTML scripts are not connected")
            for asset in ("product-app.js", "product-runtime.js", "demo-plan.js", "config-format.js"):
                if not get("/" + asset):
                    raise RuntimeError(f"Empty required asset: {asset}")
            post("/api/v1/app/quit", {}, token)
            process.wait(timeout=15)
            if process.returncode != 0:
                raise RuntimeError(f"Server quit with {process.returncode}")
            result["starts"].append({"attempt": attempt + 1, "health": health,
                                     "mode": bootstrap["mode"], "workspaceId": bootstrap["workspace"]["id"],
                                     "historyCount": len(bootstrap["history"]), "exitCode": process.returncode})
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            stdout, stderr = process.communicate(timeout=5)
            (evidence_dir / f"start-{attempt + 1}.stderr.txt").write_text(stderr, encoding="utf-8")
            # Tickets and session credentials are deliberately not persisted.
            (evidence_dir / f"start-{attempt + 1}.stdout.txt").write_text(stdout, encoding="utf-8")
    result["status"] = "passed"
    (evidence_dir / "smoke-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--python", action="store_true")
    args = parser.parse_args()
    try:
        run(args.exe.resolve(), args.evidence_dir.resolve(), python=args.python)
    except Exception as error:
        result_file = args.evidence_dir.resolve() / "smoke-result.json"
        if result_file.parent.is_dir() and not result_file.exists():
            result_file.write_text(json.dumps({"status": "failed", "backend": "file", "error": str(error)}, indent=2) + "\n", encoding="utf-8")
        raise
