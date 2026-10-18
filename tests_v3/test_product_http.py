"""Real HTTP -> service -> shared engine -> durable FileEnvironment integration."""
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit
import uuid
import time

from envmanager_v3.http_server import create_server
from envmanager_v3.service import Service


class ProductHTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = Service(Path(self.directory.name))
        self.server = create_server(self.service, Path(__file__).resolve().parent.parent)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie, self.csrf = "", ""
        ticket = parse_qs(urlsplit(self.server.launch_url).fragment)["ticket"][0]
        status, reply = self.request("POST", "/session/exchange", {"ticket": ticket})
        self.assertEqual(status, 200)
        self.csrf = reply["csrfToken"]

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()
        self.service.close()
        self.directory.cleanup()

    def request(self, method, path, body=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"Origin": self.server.origin}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        encoded = None if body is None else json.dumps(body).encode()
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, "/api/v1" + path, body=encoded, headers=headers)
            response = connection.getresponse()
            cookie = response.getheader("Set-Cookie")
            if cookie:
                self.cookie = cookie.split(";", 1)[0]
            data = response.read()
            return response.status, json.loads(data) if "json" in response.getheader("Content-Type", "") else data.decode()
        finally:
            connection.close()

    def test_event_long_poll_wakes_without_reading_or_writing_environment(self):
        status, initial = self.request("GET", "/events?timeout=0")
        self.assertEqual(status, 200, initial)
        self.assertFalse(initial["changed"])
        timer = threading.Timer(0.05, self.service._environment_changed)
        timer.start()
        try:
            started = time.monotonic()
            status, notified = self.request("GET", "/events?after=" + initial["cursor"] + "&timeout=5")
            self.assertEqual(status, 200, notified)
            self.assertTrue(notified["changed"])
            self.assertNotEqual(notified["cursor"], initial["cursor"])
            self.assertLess(time.monotonic() - started, 3)
        finally:
            timer.join()
        self.assertFalse(self.service.environment.path.exists())
        self.assertEqual(self.service.history({})["items"], [])
        for query in ("timeout=26", "timeout=-1", "timeout=bad", "unknown=1"):
            with self.subTest(query=query):
                status, failure = self.request("GET", "/events?" + query)
                self.assertEqual(status, 400, failure)

    def test_import_review_execute_idempotence_history_and_config_save(self):
        text = "[Real.Local]\nPATH = { default_value=' A;;A;中文 ', mode='-append', scope='User' }\n"
        status, state = self.request("POST", "/workspaces/import", {"text": text, "filename": "test.toml"})
        self.assertEqual(status, 200, state)
        record_id = state["apps"][0]["profiles"][0]["vars"][0]["recordId"]
        workspace = state["workspace"]
        status, plan = self.request("POST", "/plans", {"kind": "apply", "recordIds": [record_id]})
        self.assertEqual(status, 200, plan)
        self.assertFalse(self.service.environment.read("User", "PATH")["exists"])
        status, review = self.request("POST", "/plans/" + plan["planId"] + "/review", {"version": plan["version"], "digest": plan["digest"]})
        self.assertEqual(status, 200, review)
        self.assertIn("reviewToken", review)
        self.assertFalse(self.service.environment.read("User", "PATH")["exists"])
        payload = {**review, "idempotencyKey": uuid.uuid4().hex}
        status, committed = self.request("POST", "/plans/" + plan["planId"] + "/execute", payload)
        self.assertEqual(status, 200, committed)
        self.assertEqual(committed["status"], "committed", committed)
        actual = self.service.environment.read("User", "PATH")
        self.assertEqual(actual["raw"], plan["targets"][0]["plan"]["after"]["raw"])
        status, repeated = self.request("POST", "/plans/" + plan["planId"] + "/execute", payload)
        self.assertEqual(repeated["operationId"], committed["operationId"])
        self.assertEqual(self.service.environment.read("User", "PATH")["revision"], actual["revision"])
        status, history = self.request("GET", "/history")
        self.assertEqual(len(history["items"]), 1)
        self.assertEqual(history["items"][0]["changes"][0]["after"], actual["raw"])
        self.assertTrue(history["items"][0]["when"])
        workspace = committed["state"]["workspace"]
        status, saved = self.request("POST", "/workspaces/" + workspace["id"] + "/save", {"expectedDiskHash": workspace["fileRevision"], "expectedRevision": workspace["revision"]})
        self.assertEqual(status, 200, saved)
        status, exported = self.request("GET", "/workspaces/" + workspace["id"] + "/export")
        self.assertEqual(status, 200)
        self.assertIn('default_value', exported)
        self.assertTrue(self.service.config.path.is_file())

    def test_forged_after_cannot_reach_persistent_environment(self):
        status, reply = self.request("POST", "/plans", {"kind": "apply", "after": "attacker"})
        self.assertEqual(status, 400)
        self.assertEqual(reply["error"]["code"], "INVALID_REQUEST")
        self.assertEqual(self.service.environment.list()["items"], [])
        self.assertEqual(self.service.database.history(), [])


if __name__ == "__main__":
    unittest.main()
