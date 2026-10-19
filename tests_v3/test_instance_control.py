"""Same-user page control: real ordinary subprocesses, File backend, no browser.

All generated data is temporary. The process harness replaces only the browser
launcher and adds constructor counters; the real main/lock/Service/HTTP/native
channel run unchanged. No Registry, installer, private user state or auth reads.
"""
from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from envmanager_v3 import instance_control as c
from envmanager_v3.__main__ import InstanceLock, InstanceBusy
from envmanager_v3.environment import FileEnvironment


OWN = {"pid": 1, "created": 10, "owner": "test-sid", "login": 5,
       "image": "python", "hash": "hash", "elevated": False}
PEER = dict(OWN, pid=2, created=20)


class Identity:
    win = None

    @contextmanager
    def pin(self, pid):
        yield dict(OWN if pid == 1 else PEER), lambda: None


class Connection:
    def __init__(self, change=None):
        self.sent = []
        self.change = change or {}
        self.closed = False

    def peer_pid(self):
        return 2

    def send(self, message):
        self.sent.append(message)

    def receive(self):
        if len(self.sent) == 2:
            return {"received": "c" * 64}
        hello = self.sent[0]
        return {"protocol": 1, "operation": "open", "config": "config",
                "session": hello["session"], "challenge": hello["challenge"],
                "clientNonce": "c" * 64, "client": PEER, **self.change}

    def close(self):
        self.closed = True


class ControlProtocolTests(unittest.TestCase):
    def controller(self):
        controller = c.InstanceController.__new__(c.InstanceController)
        controller.identity, controller.own = Identity(), OWN
        controller.session, controller.config = "a" * 64, "config"
        controller._held = Mock()
        controller.server = Mock()
        controller.server.new_launch_url.return_value = "http://127.0.0.1:1234/#ticket=fresh"
        controller.opener = Mock(return_value=True)
        return controller

    def test_only_open_status_exact_schema_no_url_or_command(self):
        for change in ({"operation": "execute"}, {"url": "https://evil.test"}, {"command": "calc"},
                       {"operation": "open", "after": "new-write"}, {"protocol": True}):
            with self.subTest(change=change):
                controller = self.controller()
                with self.assertRaises(c.ControlError):
                    controller._exchange(Connection(change))
                controller.opener.assert_not_called()
                controller.server.new_launch_url.assert_not_called()
        with self.assertRaises(c.ControlError):
            c.request_existing("unused", "config", operation="execute")

    def test_replay_wrong_session_challenge_or_process_is_rejected(self):
        for change in ({"session": "b" * 64}, {"challenge": "b" * 64},
                       {"client": dict(PEER, created=21)}, {"clientNonce": "bad"}):
            controller = self.controller()
            with self.assertRaises(c.ControlError):
                controller._exchange(Connection(change))
            controller.opener.assert_not_called()

    def test_scope_config_status_and_browser_failure_have_no_service_entry(self):
        for field in ("owner", "login", "image", "hash", "elevated"):
            with self.subTest(field=field), self.assertRaises(c.ControlError):
                c._same_scope(dict(PEER, **{field: "different"}), OWN)
        for change, status in (({"config": "other"}, "INSTANCE_CONFIG_MISMATCH"),
                               ({"operation": "status"}, "running"), ({}, "opened")):
            controller = self.controller()
            connection = Connection(change)
            controller._exchange(connection)
            self.assertEqual(connection.sent[-1]["status"], status)
            self.assertNotIn("url", connection.sent[-1])
            self.assertEqual(controller.opener.call_count, int(status == "opened"))
        controller = self.controller()
        controller.opener.return_value = False
        connection = Connection()
        controller._exchange(connection)
        self.assertEqual(connection.sent[-1]["status"], "INSTANCE_BROWSER_FAILED")

    def test_client_checks_kernel_peer_hello_and_response_binding_without_retry(self):
        for tamper in ("peer", "hello", "reply", "valid"):
            connection = Mock()
            connection.peer_pid.return_value = 2
            hello = {"protocol": 1, "server": PEER, "session": "a" * 64, "challenge": "b" * 64}
            if tamper == "hello":
                hello["server"] = dict(PEER, created=999)
            def receive():
                if not connection.send.called:
                    return hello
                request = connection.send.call_args.args[0]
                return {"protocol": 1, "status": "opened", "session": "a" * 64,
                        "clientNonce": "wrong" if tamper == "reply" else request["clientNonce"]}
            connection.receive.side_effect = receive
            identity = Identity()
            if tamper == "peer":
                @contextmanager
                def wrong_peer(pid):
                    yield dict(OWN if pid == 1 else dict(PEER, owner="other")), lambda: None
                identity.pin = wrong_peer
            with patch.object(c, "_Identity", return_value=identity), patch.object(c, "_connect_native", return_value=connection) as connect, patch.object(c, "os", SimpleNamespace(name="nt", getpid=lambda: 1, path=os.path)):
                if tamper == "valid":
                    self.assertEqual(c.request_existing(Path.cwd(), "config"), "opened")
                else:
                    with self.assertRaises(c.ControlError):
                        c.request_existing(Path.cwd(), "config")
                connect.assert_called_once()
                connection.close.assert_called_once()


HARNESS = r'''
import json, sys
from envmanager_v3 import __main__ as launch
def counted(factory, event):
    def call(*args, **kwargs):
        print(json.dumps({"event": event}), flush=True)
        return factory(*args, **kwargs)
    return call
launch.Service = counted(launch.Service, "service")
launch.FileEnvironment = counted(launch.FileEnvironment, "adapter")
def forbidden(*args, **kwargs):
    raise AssertionError("Windows adapter forbidden in File-process acceptance")
launch.WindowsEnvironment = forbidden
def browser(url):
    print(json.dumps({"event": "browser", "url": url}), flush=True)
    return True
launch.webbrowser.open = browser
raise SystemExit(launch.main(sys.argv[1:]))
'''


@unittest.skipUnless(os.name == "nt" or (sys.platform.startswith("linux") and hasattr(os, "pidfd_open")),
                     "requires Windows native pipes or Linux peer credentials/pidfd")
class ProcessControlTests(unittest.TestCase):
    def setUp(self):
        self.source = Path(__file__).resolve().parents[1]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.data = self.root / "data"
        self.environment = self.data / "environment.json"
        self.args = ["--backend", "file", "--data-dir", str(self.data), "--assets", str(self.source / "ui-v3")]
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        self.events, self.lines = queue.Queue(), []
        self.processes = []
        self.addCleanup(self.cleanup_processes)
        if not shutil.which("node"):
            self.skipTest("real File Service requires Node")

    def cleanup_processes(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            for stream in (process.stdout, process.stderr):
                if stream:
                    stream.close()

    def command(self, extra=()):
        return [sys.executable, "-B", "-c", HARNESS, *self.args, *extra]

    def secondary(self, extra=()):
        result = subprocess.run(self.command(extra), cwd=self.source, env=self.env,
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        # An attempted fallback/second service is always a failure, even if its
        # eventual startup happens to fail for another reason.
        self.assertNotIn('"event": "service"', result.stdout)
        self.assertNotIn('"event": "adapter"', result.stdout)
        return result

    def start(self, extra=()):
        self.events, self.lines = queue.Queue(), []
        process = subprocess.Popen(self.command(extra), cwd=self.source, env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.processes.append(process)
        def read():
            for line in process.stdout:
                value = json.loads(line)
                self.lines.append(value)
                self.events.put(value)
            self.events.put(None)
        threading.Thread(target=read, daemon=True).start()
        record = self.event("status", "listening")
        self.url = record["url"]
        self.origin = "http://" + urlsplit(self.url).netloc
        self.cookie = self.csrf = None
        if "--no-browser" not in extra:
            self.assertEqual(self.event("event", "browser")["url"], self.url)
        return process

    def event(self, key, value):
        while True:
            item = self.events.get(timeout=20)
            if item is None:
                self.fail("primary exited before expected event: " + self.processes[-1].stderr.read())
            if item.get(key) == value:
                return item

    def http(self, method, path, body=None, *, cookie=True):
        headers = {"Origin": self.origin}
        if cookie and self.cookie:
            headers["Cookie"] = self.cookie
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(body)
        connection = http.client.HTTPConnection("127.0.0.1", urlsplit(self.url).port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

    def exchange(self, url):
        ticket = urlsplit(url).fragment.removeprefix("ticket=")
        return self.http("POST", "/api/v1/session/exchange", {"ticket": ticket}, cookie=False)

    def login(self):
        status, headers, body = self.exchange(self.url)
        self.assertEqual(status, 200, body)
        self.cookie, self.csrf = headers["Set-Cookie"].split(";", 1)[0], body["csrfToken"]

    def quit(self, process):
        self.assertEqual(self.http("POST", "/api/v1/app/quit", {})[0], 200)
        self.assertEqual(process.wait(timeout=15), 0, process.stderr.read())

    def test_real_file_reopen_consumed_ticket_preserves_session_lock_and_restart(self):
        self.data.mkdir()
        FileEnvironment(self.environment).write("User", "V3_REOPEN_SENTINEL", "raw;;value;", "REG_SZ")
        before = self.environment.read_bytes()
        process = self.start()
        self.login()
        original_cookie, original_csrf, original_url = self.cookie, self.csrf, self.url
        self.assertEqual(self.exchange(original_url)[0], 401)
        for _ in range(2):
            result = self.secondary()
            self.assertEqual(result.returncode, 0, result.stderr)
            # Default JSON carries no tickets, nonces, cookies or controller data.
            self.assertEqual(json.loads(result.stdout), {"status": "reopened", "backend": "file"})
            reopened = self.event("event", "browser")["url"]
            self.assertNotEqual(reopened, original_url)
            self.assertEqual(urlsplit(reopened).netloc, urlsplit(original_url).netloc)
            self.assertEqual(self.http("GET", "/api/v1/bootstrap")[0], 200)
            status, headers, body = self.exchange(reopened)
            self.assertEqual(status, 200, body)
            self.assertEqual(headers["Set-Cookie"].split(";", 1)[0], original_cookie)
            self.assertEqual(body["csrfToken"], original_csrf)
            self.assertEqual(self.exchange(reopened)[0], 401)
            with self.assertRaises(InstanceBusy):
                with InstanceLock(self.data / "instance.lock"):
                    pass
        for extra in (["--check"], ["--no-browser"], ["--environment-file", str(self.root / "other.json")],
                      ["--assets", str(self.root)], ["--port", "12345"]):
            result = self.secondary(extra)
            self.assertEqual(result.returncode, 1, (extra, result.stdout, result.stderr))
            if extra[0] not in ("--check", "--no-browser"):
                self.assertIn("INSTANCE_CONFIG_MISMATCH", result.stderr)
        # A backend change is a scope mismatch on Windows; POSIX rejects the
        # unsupported Windows backend before even contacting a controller.
        result = self.secondary(["--backend", "windows"])
        self.assertNotEqual(result.returncode, 0)
        if os.name == "nt":
            self.assertIn("INSTANCE_CONFIG_MISMATCH", result.stderr)
        self.assertEqual(self.environment.read_bytes(), before)
        self.assertEqual(sum(item.get("event") == "service" for item in self.lines), 1)
        self.assertEqual(sum(item.get("event") == "adapter" for item in self.lines), 1)
        self.quit(process)
        # With no locator files, the kernel endpoint dies with its controller;
        # a stale prior session cannot be followed or delete a newer endpoint.
        with self.assertRaises(c.ControlError):
            c.request_existing(self.data, "unused", "status")
        with InstanceLock(self.data / "instance.lock"):
            pass
        process = self.start()
        self.login()
        self.quit(process)

    def test_no_browser_primary_has_no_controller_and_upgrade_lock_refuses_all_launches(self):
        process = self.start(["--no-browser"])
        self.login()
        result = self.secondary()
        self.assertEqual(result.returncode, 1)
        self.assertIn("INSTANCE_CONTROL_UNAVAILABLE", result.stderr)
        self.assertEqual(self.http("GET", "/api/v1/bootstrap")[0], 200)
        self.quit(process)
        from envmanager_v3.upgrade import UpgradeSession
        upgrade = UpgradeSession(self.data, "3.0.0.test", 60)
        try:
            upgrade._acquire()
            for extra in ([], ["--check"], ["--no-browser"]):
                result = self.secondary(extra)
                self.assertEqual(result.returncode, 1)
                if extra:
                    self.assertIn("此数据目录已有 V3 实例运行", result.stderr)
                else:
                    self.assertIn("INSTANCE_CONTROL_UNAVAILABLE", result.stderr)
            upgrade._lock_check()
        finally:
            upgrade.close()


if __name__ == "__main__":
    unittest.main()
