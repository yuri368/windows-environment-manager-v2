"""Real IPv4 loopback transport with a fake service; no Registry or UAC."""

from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import unquote, urlsplit

from envmanager_v3 import http_server as h


class ServiceError(Exception):
    def __init__(self, status=409):
        self.status = status
        self.code = "TARGET_CONFLICT"
        self.message = "The target changed."
        self.details = {"currentRevision": "revision-2"}


class FakeService:
    def __init__(self):
        self.calls = []
        self.result = {"ok": True}
        self.error = None

    def __getattr__(self, method):
        def call(*args):
            self.calls.append((method, args))
            if self.error is not None:
                raise self.error
            return self.result
        return call


class HTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # The server canonicalizes its asset root (including Windows 8.3 aliases).
        self.root = Path(temporary.name).resolve()
        self.assets = self.root / "assets"
        self.assets.mkdir()
        (self.assets / "index.html").write_text("<html>V3 shell</html>", encoding="utf-8")
        (self.assets / "product-app.js").write_text("'use strict';", encoding="utf-8")
        (self.root / "secret.txt").write_text("DO NOT SERVE", encoding="utf-8")
        self.service = FakeService()
        self.server = h.create_server(self.service, self.assets)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.ticket = urlsplit(self.server.launch_url).fragment.removeprefix("ticket=")
        self.cookie = None
        self.csrf = None

    def stop_server(self):
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def request(self, method, path, body=None, *, headers=None, authenticated=True):
        fields = {}
        if authenticated:
            if self.cookie:
                fields["Cookie"] = self.cookie
            if method in ("POST", "PATCH", "PUT", "DELETE"):
                fields["Origin"] = self.server.origin
                if self.csrf:
                    fields["X-CSRF-Token"] = self.csrf
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=True).encode("ascii")
        if body is not None:
            fields["Content-Type"] = "application/json"
        for key, value in (headers or {}).items():
            if value is None:
                fields.pop(key, None)
            else:
                fields[key] = value
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=fields)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def raw_request(self, target="/health", headers=(), body=b"", method="GET"):
        lines = [f"{method} {target} HTTP/1.1", f"Host: {self.server.authority}", *headers]
        with socket.create_connection(self.server.server_address, timeout=3) as connection:
            connection.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body)
            connection.shutdown(socket.SHUT_WR)
            response = http.client.HTTPResponse(connection)
            response.begin()
            return response.status, dict(response.getheaders()), response.read()

    def login(self):
        status, headers, payload = self.request("POST", h.API + "/session/exchange",
                                                {"ticket": self.ticket})
        self.assertEqual(status, 200, payload)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.csrf = json.loads(payload)["csrfToken"]
        return headers

    def assert_error(self, response, status, code):
        actual_status, headers, payload = response
        self.assertEqual(actual_status, status, payload)
        self.assertEqual(json.loads(payload)["error"]["code"], code)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_loopback_random_port_and_fragment_ticket(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertGreater(self.server.server_port, 0)
        self.assertFalse(urlsplit(self.server.launch_url).query)
        self.assertGreaterEqual(len(self.ticket), 32)
        for port in (True, -1, 65536, "0"):
            with self.assertRaises(ValueError):
                h.create_server(self.service, self.assets, port)

    def test_public_shell_health_and_security_headers(self):
        for path in ("/", "/index.html", "/health", "/product-app.js"):
            status, headers, body = self.request("GET", path)
            self.assertEqual(status, 200, body)
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertEqual(headers["X-Frame-Options"], "DENY")
            self.assertEqual(headers["Referrer-Policy"], "no-referrer")
            self.assertNotIn("unsafe-eval", headers["Content-Security-Policy"])
            self.assertNotIn(self.ticket.encode(), body)
        self.assertFalse(self.service.calls)

    def test_ticket_cookie_csrf_and_one_use(self):
        self.assert_error(self.request("GET", h.API + "/bootstrap"), 401, "AUTH_REQUIRED")
        headers = self.login()
        for attribute in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(attribute, headers["Set-Cookie"])
        self.assertEqual(self.request("GET", h.API + "/bootstrap")[0], 200)
        self.assert_error(self.request("POST", h.API + "/session/exchange",
                                       {"ticket": self.ticket}), 401, "INVALID_TICKET")

    def test_invalid_ticket_does_not_consume_valid_ticket(self):
        for body in ({"ticket": "wrong"}, {"ticket": "中文"}):
            self.assert_error(self.request("POST", h.API + "/session/exchange", body),
                              401, "INVALID_TICKET")
        self.login()

    def test_reopen_tickets_are_independent_one_use_and_preserve_cookie_and_csrf(self):
        self.login()
        cookie, csrf = self.cookie, self.csrf
        tickets = [urlsplit(self.server.new_launch_url()).fragment.removeprefix("ticket=") for _ in range(2)]
        self.assertEqual(len(set(tickets + [self.ticket])), 3)
        # Issuing a new grant must not temporarily invalidate an existing tab.
        self.assertEqual(self.request("GET", h.API + "/bootstrap")[0], 200)
        for ticket in tickets:
            status, headers, body = self.request("POST", h.API + "/session/exchange", {"ticket": ticket})
            self.assertEqual(status, 200, body)
            self.assertEqual(headers["Set-Cookie"].split(";", 1)[0], cookie)
            self.assertEqual(json.loads(body)["csrfToken"], csrf)
            self.assert_error(self.request("POST", h.API + "/session/exchange", {"ticket": ticket}),
                              401, "INVALID_TICKET")
        self.assertEqual(self.request("POST", h.API + "/plans", {})[0], 200)

    def test_launch_tickets_expire_without_invalidating_authenticated_tabs(self):
        self.login()
        with mock.patch.object(h.time, "monotonic", return_value=100):
            ticket = urlsplit(self.server.new_launch_url()).fragment.removeprefix("ticket=")
        with mock.patch.object(h.time, "monotonic", return_value=161):
            self.assert_error(self.request("POST", h.API + "/session/exchange", {"ticket": ticket}),
                              401, "INVALID_TICKET")
        self.assertEqual(self.request("GET", h.API + "/bootstrap")[0], 200)

    def test_control_is_not_exposed_as_http_route(self):
        self.login()
        for path in ("/instance/open", "/instance/status", "/session/open"):
            self.assert_error(self.request("POST", h.API + path, {"url": "https://evil.test"}),
                              404, "NOT_FOUND")
        self.assertFalse(self.service.calls)

    def test_authenticated_bootstrap_restores_fresh_tab_csrf_without_new_ticket(self):
        self.login()
        original_csrf = self.csrf
        self.csrf = None
        status, headers, body = self.request("GET", h.API + "/bootstrap")
        self.assertEqual(status, 200, body)
        self.assertEqual(headers["X-CSRF-Token"], original_csrf)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(json.loads(body), self.service.result)
        self.assertEqual(self.service.calls, [("bootstrap", ())])
        self.csrf = headers["X-CSRF-Token"]
        self.assertEqual(self.request("POST", h.API + "/plans", {})[0], 200)

    def test_rejected_bootstrap_never_discloses_csrf(self):
        self.login()
        for fields in ({"Cookie": None}, {"Cookie": h.COOKIE_NAME + "=wrong"},
                       {"Origin": "https://evil.test"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(fields=fields):
                status, headers, body = self.request("GET", h.API + "/bootstrap", headers=fields)
                self.assertIn(status, (401, 403), body)
                self.assertNotIn("X-CSRF-Token", headers)
                self.assertNotIn(self.csrf.encode(), body)
        self.assertFalse(self.service.calls)

    def test_ticket_exchange_exact_schema(self):
        for body in ({}, {"ticket": 4}, {"ticket": self.ticket, "after": "x"}):
            self.assert_error(self.request("POST", h.API + "/session/exchange", body),
                              400, "INVALID_JSON")
        self.login()

    def test_simultaneous_exchange_has_one_winner(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.request("POST", h.API + "/session/exchange",
                                                           {"ticket": self.ticket}), range(2)))
        self.assertEqual(sorted(result[0] for result in results), [200, 401])

    def test_authentication_cannot_be_supplied_via_query_or_header(self):
        for path, headers in ((h.API + "/bootstrap?ticket=" + self.ticket, {}),
                              (h.API + "/bootstrap", {"Authorization": "Bearer " + self.ticket}),
                              (h.API + "/bootstrap", {"Cookie": h.COOKIE_NAME + "=wrong"})):
            self.assert_error(self.request("GET", path, headers=headers), 401, "AUTH_REQUIRED")
        self.assertFalse(self.service.calls)

    def test_cookie_is_required_even_with_valid_csrf(self):
        self.login()
        self.assert_error(self.request("POST", h.API + "/plans", {}, headers={"Cookie": None}),
                          401, "AUTH_REQUIRED")
        self.assertFalse(self.service.calls)

    def test_mutation_requires_exact_origin_and_csrf(self):
        self.login()
        for headers, code in (({"Origin": None}, "ORIGIN_REQUIRED"),
                              ({"Origin": "null"}, "ORIGIN_REJECTED"),
                              ({"Origin": self.server.origin + "/"}, "ORIGIN_REJECTED"),
                              ({"X-CSRF-Token": None}, "CSRF_REJECTED"),
                              ({"X-CSRF-Token": "wrong"}, "CSRF_REJECTED")):
            self.assert_error(self.request("POST", h.API + "/plans", {}, headers=headers), 403, code)
        self.assertFalse(self.service.calls)

    def test_exchange_requires_origin_but_no_csrf(self):
        self.assert_error(self.request("POST", h.API + "/session/exchange", {"ticket": self.ticket},
                                       headers={"Origin": None}), 403, "ORIGIN_REQUIRED")
        self.login()

    def test_host_origin_and_fetch_metadata_on_public_and_api_requests(self):
        self.login()
        for path in ("/", "/health", h.API + "/bootstrap"):
            for headers, code in (({"Host": "localhost:" + str(self.server.server_port)}, "HOST_REJECTED"),
                                  ({"Host": "evil.test"}, "HOST_REJECTED"),
                                  ({"Origin": "https://evil.test"}, "ORIGIN_REJECTED"),
                                  ({"Sec-Fetch-Site": "cross-site"}, "CROSS_SITE_REJECTED")):
                self.assert_error(self.request("GET", path, headers=headers), 403, code)
        self.assertFalse(self.service.calls)

    def test_repeated_security_headers_and_transfer_encoding(self):
        for headers in (("Host: evil.test",),
                        ("Origin: http://one", "Origin: http://two"),
                        ("Sec-Fetch-Site: none", "Sec-Fetch-Site: same-origin")):
            self.assert_error(self.raw_request(headers=headers), 400, "INVALID_HEADERS")
        self.assert_error(self.raw_request(headers=("Transfer-Encoding: chunked",)),
                          400, "TRANSFER_ENCODING_REJECTED")
        self.login()
        for key, value in (("Cookie", self.cookie), ("X-CSRF-Token", self.csrf),
                           ("Content-Length", "2"), ("Content-Type", "application/json")):
            fields = ["Cookie: " + self.cookie, "Origin: " + self.server.origin,
                      "X-CSRF-Token: " + self.csrf, "Content-Length: 2", "Content-Type: application/json"]
            fields.append(key + ": " + value)
            self.assert_error(self.raw_request(h.API + "/plans", fields, b"{}", "POST"),
                              400, "INVALID_HEADERS")

    def test_strict_json_rejects_duplicates_constants_surrogates_and_nonobjects(self):
        self.login()
        for body in (b'{"a":1,"a":2}', b'{"a":{"x":1,"x":2}}', b'{"x":NaN}',
                     b'{"x":Infinity}', b'{"x":-Infinity}', b'{"x":1e999}',
                     b'{"x":"\\ud800"}', b'{"\\udfff":1}', b'[]', b'null', b'1', b'"x"',
                     b'{"x":"\xff"}', b'{} trailing', b'', b'{"a":}'):
            with self.subTest(body=body):
                self.assert_error(self.request("POST", h.API + "/plans", body), 400, "INVALID_JSON")
        self.assertFalse(self.service.calls)

    def test_json_complexity_limits(self):
        self.login()
        for body in (b'{"x":' + b'[' * 66 + b'0' + b']' * 66 + b'}',
                     b'{"x":[' + b'0,' * 100001 + b'0]}'):
            self.assert_error(self.request("POST", h.API + "/plans", body), 400, "INVALID_JSON")

    def test_json_preserves_raw_text_and_passes_service_dto_unchanged(self):
        self.login()
        body = {"raw": " \r\n;重复;;重复; ", "enabled": False, "value": None, "rows": ["", ""]}
        self.assertEqual(self.request("POST", h.API + "/plans", body)[0], 200)
        self.assertEqual(self.service.calls, [("plan", (body,))])

    def test_content_type_and_charset(self):
        self.login()
        for value in (None, "text/plain", "application/json; charset=latin1", "application/json; extra=yes"):
            self.assert_error(self.request("POST", h.API + "/plans", b"{}", headers={"Content-Type": value}),
                              415, "UNSUPPORTED_MEDIA_TYPE")
        for value in ("application/json", "Application/JSON; charset=UTF-8", 'application/json; charset="utf-8"'):
            self.assertEqual(self.request("POST", h.API + "/plans", b"{}", headers={"Content-Type": value})[0], 200)

    def test_body_framing_and_incomplete_body(self):
        self.login()
        base = ["Cookie: " + self.cookie, "Origin: " + self.server.origin,
                "X-CSRF-Token: " + self.csrf, "Content-Type: application/json"]
        for value in (None, "-1", "no", "99999999999999"):
            fields = base + ([] if value is None else ["Content-Length: " + value])
            self.assert_error(self.raw_request(h.API + "/plans", fields, b"{}", "POST"),
                              400, "INVALID_CONTENT_LENGTH")
        self.assert_error(self.raw_request(h.API + "/plans", base + ["Content-Length: 5"], b"{}", "POST"),
                          400, "INVALID_BODY")

    def test_incomplete_live_body_times_out_without_dispatching_service(self):
        self.login()
        original = h._Handler.setup
        def short_timeout(handler):
            original(handler)
            handler.request.settimeout(0.05)
        fields = ["POST " + h.API + "/plans HTTP/1.1", "Host: " + self.server.authority,
                  "Cookie: " + self.cookie, "Origin: " + self.server.origin,
                  "X-CSRF-Token: " + self.csrf, "Content-Type: application/json", "Content-Length: 5"]
        with mock.patch.object(h._Handler, "setup", new=short_timeout), \
                socket.create_connection(self.server.server_address, timeout=3) as connection:
            connection.sendall(("\r\n".join(fields) + "\r\n\r\n{}").encode("ascii"))
            response = http.client.HTTPResponse(connection)
            response.begin()
            self.assert_error((response.status, dict(response.getheaders()), response.read()), 408, "BODY_TIMEOUT")
        self.assertFalse(self.service.calls)

    def test_default_body_limit_and_explicit_import_limit(self):
        self.login()
        fields = ["Cookie: " + self.cookie, "Origin: " + self.server.origin,
                  "X-CSRF-Token: " + self.csrf, "Content-Type: application/json"]
        for path, size in (("/plans", 2 * 1024 * 1024 + 1),
                           ("/workspaces/import", 8 * 1024 * 1024 + 1)):
            self.assert_error(self.raw_request(h.API + path, fields + [f"Content-Length: {size}"], method="POST"),
                              413, "BODY_TOO_LARGE")
        body = {"text": "x" * (2 * 1024 * 1024)}
        self.assertEqual(self.request("POST", h.API + "/workspaces/import", body)[0], 200)

    def test_traversal_encoding_drive_absolute_and_control_paths(self):
        self.login()
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/%252e%252e/secret.txt", "/foo%5cbar",
                     "/C:/secret", "/foo%00bar", "/foo%0abar", "/%ff", "/%zz", "/x#fragment",
                     "http://evil.test/health", "//evil.test/health"):
            with self.subTest(path=path):
                self.assert_error(self.raw_request(path), 400, "INVALID_PATH")
        self.assertFalse(self.service.calls)

    def test_static_allowlist_and_missing_files(self):
        for path in ("/secret.txt", "/.git/config", "/envmanager_v3/http_server.py", "/assets/", "/config.toml"):
            self.assert_error(self.request("GET", path), 404, "NOT_FOUND")
        self.assert_error(self.request("GET", "/product-runtime.js"), 404, "ASSET_NOT_FOUND")

    def test_static_resolved_path_cannot_escape_asset_root(self):
        original = Path.resolve
        def redirected(path, *args, **kwargs):
            if path == self.assets / "index.html":
                return self.root / "secret.txt"
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "resolve", new=redirected):
            self.assert_error(self.request("GET", "/"), 403, "ASSET_REJECTED")

    def test_query_dictionary_and_duplicate_or_malformed_queries(self):
        self.login()
        for endpoint, method in (("environment", "environment_state"), ("history", "history")):
            self.assertEqual(self.request("GET", h.API + "/" + endpoint + "?scope=System&blank=&name=%E4%B8%AD")[0], 200)
            self.assertEqual(self.service.calls[-1], (method, ({"scope": "System", "blank": "", "name": "中"},)))
        for query in ("x=1&x=2", "x=%ff", "x=%zz", "x=%", "&".join(f"x{i}=1" for i in range(101))):
            self.assert_error(self.request("GET", h.API + "/history?" + query), 400, "INVALID_PATH")

    def test_all_existing_routes_keep_service_method_and_argument_mapping(self):
        self.login()
        cases = [
            ("GET", "/bootstrap", "bootstrap", ()),
            ("GET", "/events?after=epoch%3A1&timeout=0", "events", ({"after": "epoch:1", "timeout": "0"},)),
            ("POST", "/workspaces/import", "import_workspace", ({},)),
            ("GET", "/workspaces/w1", "workspace", ("w1",)),
            ("PATCH", "/workspaces/w1/draft", "save_draft", ("w1", {})),
            ("POST", "/workspaces/w1/create", "create_entry", ("w1", {})),
            ("POST", "/workspaces/w1/restore", "restore_workspace", ("w1", {})),
            ("POST", "/workspaces/w1/save", "save_workspace", ("w1", {})),
            ("GET", "/workspaces/w1/export", "export_workspace", ("w1",)),
            ("PATCH", "/selections/r1", "update_selection", ("r1", {})),
            ("POST", "/selections/r1/restore", "restore_selection", ("r1", {})),
            ("GET", "/environment", "environment_state", ({},)),
            ("POST", "/environment/refresh", "refresh", ({},)),
            ("POST", "/edit-sessions", "open_edit", ({},)),
            ("PATCH", "/edit-sessions/e1", "update_edit", ("e1", {})),
            ("POST", "/order-sessions", "open_order", ({},)),
            ("PATCH", "/order-sessions/o1", "update_order", ("o1", {})),
            ("POST", "/plans", "plan", ({},)),
            ("POST", "/plans/p1/review", "review", ("p1", {})),
            ("POST", "/plans/p1/execute", "execute", ("p1", {})),
            ("GET", "/operations/by-key/k1", "operation_by_key", ("k1",)),
            ("GET", "/operations/op1", "operation", ("op1",)),
            ("GET", "/history", "history", ({},)),
            ("GET", "/history/h1", "history_record", ("h1",)),
            ("GET", "/recovery", "recovery", ()),
        ]
        for verb, path, method, args in cases:
            with self.subTest(path=path):
                self.service.result = {"text": "a=1\n", "filename": "out.toml"} if method == "export_workspace" else {"ok": True}
                response = self.request(verb, h.API + path, {} if verb in ("POST", "PATCH") else None)
                self.assertEqual(response[0], 200, response[2])
                self.assertEqual(self.service.calls[-1], (method, args))
        self.assertEqual(len(cases) + 1, len(h.ROUTES))  # quit tested independently

    def test_unknown_routes_ids_methods_and_get_never_mutates(self):
        self.login()
        for path in ("/workspaces/a.b", "/workspaces/" + "a" * 129, "/workspaces/a/b", "/registry/write", "/missing"):
            self.assert_error(self.request("GET", h.API + path), 404, "NOT_FOUND")
        for verb, path in (("GET", h.API + "/plans/p1/execute"), ("GET", h.API + "/app/quit"),
                           ("PUT", h.API + "/edit-sessions/e1"), ("DELETE", h.API + "/edit-sessions/e1"),
                           ("OPTIONS", h.API + "/bootstrap"), ("POST", "/"), ("POST", "/health")):
            self.assert_error(self.request(verb, path, {} if verb in ("POST", "PUT", "DELETE") else None),
                              405, "METHOD_NOT_ALLOWED")
        self.assert_error(self.request("GET", "/api/v2/bootstrap"), 404, "NOT_FOUND")
        self.assertFalse(self.service.calls)

    def test_download_is_service_text_with_safe_unicode_display_filename(self):
        self.login()
        text = 'name = "中文"\r\n\r\n'
        self.service.result = {"text": text, "filename": 'C:\\private\\目录/环境"\r\nX-Evil: yes.toml'}
        status, headers, body = self.request("GET", h.API + "/workspaces/w1/export")
        self.assertEqual(status, 200)
        self.assertEqual(body, text.encode("utf-8"))
        self.assertEqual(headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertNotIn("X-Evil", headers)
        disposition = headers["Content-Disposition"]
        self.assertTrue(disposition.startswith('attachment; filename="environment.toml"; filename*=UTF-8\'\''))
        display = unquote(disposition.split("UTF-8''")[1])
        self.assertNotIn("\r", display)
        self.assertNotIn("\n", display)
        self.assertNotIn("private", display)

    def test_service_error_statuses_and_internal_error_redaction(self):
        self.login()
        for status in (400, 401, 403, 409, 422, 423, 503, 599):
            self.service.error = ServiceError(status)
            self.assert_error(self.request("GET", h.API + "/bootstrap"), status, "TARGET_CONFLICT")
        for status in (200, True, "409", 600):
            self.service.error = ServiceError(status)
            self.assert_error(self.request("GET", h.API + "/bootstrap"), 400, "TARGET_CONFLICT")
        self.service.error = RuntimeError(self.ticket + " SECRET_PATH")
        response = self.request("GET", h.API + "/bootstrap")
        self.assert_error(response, 500, "INTERNAL_ERROR")
        self.assertNotIn(self.ticket.encode(), response[2])
        self.assertNotIn(b"SECRET_PATH", response[2])

    def test_service_must_return_strict_serializable_dictionary(self):
        self.login()
        for result in (None, [], {"value": float("nan")}, {"value": object()}):
            self.service.result = result
            self.assert_error(self.request("GET", h.API + "/bootstrap"), 500, "INTERNAL_ERROR")

    def test_quit_requires_auth_origin_csrf_and_running_guard(self):
        self.assert_error(self.request("POST", h.API + "/app/quit", {}), 401, "AUTH_REQUIRED")
        self.login()
        self.assert_error(self.request("POST", h.API + "/app/quit", {}, headers={"X-CSRF-Token": None}),
                          403, "CSRF_REJECTED")
        self.assert_error(self.request("POST", h.API + "/app/quit", {}, headers={"Origin": None}),
                          403, "ORIGIN_REQUIRED")
        self.assertFalse(self.service.calls)
        self.service.error = ServiceError(423)
        self.assert_error(self.request("POST", h.API + "/app/quit", {}), 423, "TARGET_CONFLICT")
        self.assertTrue(self.thread.is_alive())
        self.assertEqual(self.request("GET", "/health")[0], 200)
        self.service.error = None
        self.assertEqual(self.request("POST", h.API + "/app/quit", {})[0], 200)
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.service.calls[-1], ("quit", ({},)))


if __name__ == "__main__":
    unittest.main()
