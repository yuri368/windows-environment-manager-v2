"""Loopback-only HTTP transport for V3; no environment or filesystem mutation.

The caller owns service lifetime and runs serve_forever().  launch_url carries
the one-use ticket in its fragment; the page must remove it with replaceState
and POST {"ticket": ticket} to /api/v1/session/exchange with the exact Origin.
Exchange returns {"csrfToken": token} and sets the HttpOnly session cookie.
Authenticated bootstrap repeats the token in X-CSRF-Token so a fresh same-origin
tab can initialize its own sessionStorage. This never replaces cookie auth.
Query dictionaries contain strings; repeated query keys are rejected.
"""

from __future__ import annotations

from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import secrets
import socket
import threading
from urllib.parse import parse_qsl, unquote, urlsplit


MAX_BODY = 2 * 1024 * 1024
MAX_IMPORT_BODY = 8 * 1024 * 1024
COOKIE_NAME = "envmanager_v3_session"
API = "/api/v1"
STATIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "product-app.js": "text/javascript; charset=utf-8",
    "product-runtime.js": "text/javascript; charset=utf-8",
    "demo-plan.js": "text/javascript; charset=utf-8",
    "config-format.js": "text/javascript; charset=utf-8",
    "config-editor.js": "text/javascript; charset=utf-8",
    "windows-elements.js": "text/javascript; charset=utf-8",
    **{name: "text/css; charset=utf-8" for name in (
        "ui-polish.css", "elements-ui.css", "shell-ui.css", "fx-ui.css",
        "surface-ui.css", "config-editor.css", "config-editor-refresh.css",
        "compact-ui.css",
    )},
}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "base-uri 'none'; object-src 'none'; form-action 'none'; frame-ancestors 'none'"
)
# IDs remain opaque to HTTP.  Accept UUIDs and the service's prefixed IDs, never
# slashes, dots, percent escapes, drive syntax or arbitrary filesystem names.
ID = r"[A-Za-z0-9_-]{1,128}"
ROUTES = (
    ("GET", r"/bootstrap", "bootstrap"),
    ("GET", r"/events", "events"),
    ("POST", r"/workspaces/import", "import_workspace"),
    ("GET", rf"/workspaces/({ID})", "workspace"),
    ("PATCH", rf"/workspaces/({ID})/draft", "save_draft"),
    ("POST", rf"/workspaces/({ID})/create", "create_entry"),
    ("POST", rf"/workspaces/({ID})/restore", "restore_workspace"),
    ("POST", rf"/workspaces/({ID})/save", "save_workspace"),
    ("GET", rf"/workspaces/({ID})/export", "export_workspace"),
    ("PATCH", rf"/selections/({ID})", "update_selection"),
    ("POST", rf"/selections/({ID})/restore", "restore_selection"),
    ("GET", r"/environment", "environment_state"),
    ("POST", r"/environment/refresh", "refresh"),
    ("POST", r"/edit-sessions", "open_edit"),
    ("PATCH", rf"/edit-sessions/({ID})", "update_edit"),
    ("POST", r"/order-sessions", "open_order"),
    ("PATCH", rf"/order-sessions/({ID})", "update_order"),
    ("POST", r"/plans", "plan"),
    ("POST", rf"/plans/({ID})/review", "review"),
    ("POST", rf"/plans/({ID})/execute", "execute"),
    ("GET", rf"/operations/by-key/({ID})", "operation_by_key"),
    ("GET", rf"/operations/({ID})", "operation"),
    ("GET", r"/history", "history"),
    ("GET", rf"/history/({ID})", "history_record"),
    ("GET", r"/recovery", "recovery"),
    ("POST", r"/app/quit", "quit"),
)
_ROUTES = tuple((verb, re.compile(API + path), method) for verb, path, method in ROUTES)


class _HTTPError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite number")
    return number


def _invalid_constant(value):
    raise ValueError("Non-JSON constant")


def _validate_json(value):
    pending = [(value, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if depth > 64 or count > 100_000:
            raise ValueError("JSON complexity limit")
        if isinstance(node, dict):
            pending.extend((key, depth + 1) for key in node)
            pending.extend((item, depth + 1) for item in node.values())
        elif isinstance(node, list):
            pending.extend((item, depth + 1) for item in node)
        elif isinstance(node, str):
            node.encode("utf-8", errors="strict")


class _LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, service, assets, port):
        self.service = service
        self.assets = assets.resolve()
        self._session_lock = threading.Lock()
        self._ticket = secrets.token_urlsafe(32)
        self._session = secrets.token_urlsafe(32)
        self._csrf = secrets.token_urlsafe(32)
        self._slots = threading.BoundedSemaphore(32)
        super().__init__(("127.0.0.1", port), _Handler)
        self.authority = f"127.0.0.1:{self.server_port}"
        self.origin = f"http://{self.authority}"
        self.launch_url = f"{self.origin}/#ticket={self._ticket}"

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        # Do not let BaseServer print exception text containing request secrets.
        pass


class _Handler(BaseHTTPRequestHandler):
    # HTTP/1.0 deliberately closes each connection, including rejected bodies.
    protocol_version = "HTTP/1.0"

    def setup(self):
        self.request.settimeout(10)
        super().setup()

    def version_string(self):
        return "EnvManagerV3"

    def log_message(self, format, *args):
        # URLs, Cookie and CSRF/ticket values must never reach access logs.
        pass

    def send_error(self, code, message=None, explain=None):
        self._error(code, "HTTP_ERROR", "Invalid HTTP request.")

    def __getattr__(self, name):
        if name.startswith("do_"):
            return self._dispatch
        raise AttributeError(name)

    def _respond(self, status, payload, content_type="application/json; charset=utf-8", extra=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Connection", "close")
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True
        if getattr(self, "command", None) != "HEAD":
            self.wfile.write(payload)

    def _json(self, status, body, extra=()):
        payload = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self._respond(status, payload, extra=extra)

    def _error(self, status, code, message, details=None):
        # A rejected small POST may still be sending its body. Bounded draining
        # avoids a Windows TCP reset hiding the JSON rejection from the client.
        # Never wait on large, ambiguous or chunked framing.
        if not getattr(self, "_body_consumed", False) and hasattr(self, "headers"):
            lengths = self.headers.get_all("Content-Length", [])
            if (not self.headers.get_all("Transfer-Encoding", []) and len(lengths) == 1
                    and re.fullmatch(r"[0-9]{1,5}", lengths[0]) and int(lengths[0]) <= 65536):
                self.request.settimeout(0.1)
                try:
                    self.rfile.read(int(lengths[0]))
                except (OSError, TimeoutError):
                    pass
                self._body_consumed = True
        error = {"code": code, "message": message}
        if details is not None:
            error["details"] = details
        self._json(status, {"error": error})

    def _header(self, name):
        values = self.headers.get_all(name, [])
        if len(values) > 1:
            raise _HTTPError(400, "INVALID_HEADERS", "Repeated request header.")
        return values[0] if values else None

    def _boundary(self):
        if self._header("Host") != self.server.authority:
            raise _HTTPError(403, "HOST_REJECTED", "Host does not match this server.")
        origin = self._header("Origin")
        if origin is not None and origin != self.server.origin:
            raise _HTTPError(403, "ORIGIN_REJECTED", "Origin does not match this server.")
        if (self._header("Sec-Fetch-Site") or "").lower() == "cross-site":
            raise _HTTPError(403, "CROSS_SITE_REJECTED", "Cross-site requests are forbidden.")
        if self._header("Transfer-Encoding") is not None:
            raise _HTTPError(400, "TRANSFER_ENCODING_REJECTED", "Transfer-Encoding is forbidden.")
        return origin

    def _target(self):
        # BaseHTTPRequestHandler normalizes a leading // before dispatch. Check
        # the original request target so network-path references stay rejected.
        raw = self.requestline.split()[1]
        if not raw.startswith("/") or raw.startswith("//") or "#" in raw:
            raise _HTTPError(400, "INVALID_PATH", "Invalid request path.")
        parts = urlsplit(raw)
        if re.search(r"%(?![0-9a-fA-F]{2})", parts.path + parts.query):
            raise _HTTPError(400, "INVALID_PATH", "Invalid request path.")
        try:
            path = unquote(parts.path, encoding="utf-8", errors="strict")
            if any(char in path for char in ("\\", "\x00", ":", "%")) or any(
                ord(char) < 32 or ord(char) == 127 for char in path
            ) or any(part in (".", "..") for part in path.split("/")):
                raise ValueError("Unsafe path")
            query_pairs = parse_qsl(parts.query, keep_blank_values=True, encoding="utf-8", errors="strict", max_num_fields=100)
            query = _pairs(query_pairs)
        except (ValueError, UnicodeError):
            raise _HTTPError(400, "INVALID_PATH", "Invalid request path or query.") from None
        return path, query

    def _authenticate(self):
        raw_cookie = self._header("Cookie") or ""
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except CookieError:
            raise _HTTPError(401, "AUTH_REQUIRED", "A local session is required.") from None
        item = cookie.get(COOKIE_NAME)
        with self.server._session_lock:
            valid = self.server._ticket is None and item is not None and secrets.compare_digest(
                item.value.encode("utf-8"), self.server._session.encode("ascii")
            )
        if not valid:
            raise _HTTPError(401, "AUTH_REQUIRED", "A local session is required.")

    def _body(self, limit=MAX_BODY):
        length = self._header("Content-Length")
        if length is None or not re.fullmatch(r"[0-9]+", length) or len(length) > 10:
            raise _HTTPError(400, "INVALID_CONTENT_LENGTH", "A valid Content-Length is required.")
        length = int(length)
        if length > limit:
            raise _HTTPError(413, "BODY_TOO_LARGE", "Request body exceeds the route size limit.")
        content_type = self._header("Content-Type") or ""
        if not re.fullmatch(r'application/json(?:\s*;\s*charset=(?:utf-8|"utf-8"))?', content_type, re.IGNORECASE):
            raise _HTTPError(415, "UNSUPPORTED_MEDIA_TYPE", "Use application/json with UTF-8 encoding.")
        try:
            self._body_consumed = True
            data = self.rfile.read(length)
        except (socket.timeout, TimeoutError):
            raise _HTTPError(408, "BODY_TIMEOUT", "Request body timed out.") from None
        if len(data) != length:
            raise _HTTPError(400, "INVALID_BODY", "Incomplete request body.")
        try:
            body = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                              parse_float=_finite_float, parse_constant=_invalid_constant)
            if not isinstance(body, dict):
                raise ValueError("Object required")
            _validate_json(body)
        except (ValueError, UnicodeError, RecursionError):
            raise _HTTPError(400, "INVALID_JSON", "Body must be a bounded UTF-8 JSON object.") from None
        return body

    def _exchange(self, body):
        if set(body) != {"ticket"} or not isinstance(body["ticket"], str):
            raise _HTTPError(400, "INVALID_JSON", "Exchange requires only a string ticket.")
        with self.server._session_lock:
            ticket = self.server._ticket
            if ticket is None or not secrets.compare_digest(body["ticket"].encode("utf-8"), ticket.encode("ascii")):
                raise _HTTPError(401, "INVALID_TICKET", "Startup ticket is invalid or already used.")
            self.server._ticket = None
        cookie = f"{COOKIE_NAME}={self.server._session}; Path=/; HttpOnly; SameSite=Strict"
        self._json(200, {"csrfToken": self.server._csrf}, (("Set-Cookie", cookie),))

    def _export(self, result):
        text, filename = result["text"], result["filename"]
        if not isinstance(text, str) or not isinstance(filename, str):
            raise TypeError("Invalid export result")
        # Display name only; remove every path/header metacharacter.  RFC 5987
        # retains Unicode in filename* without allowing CRLF/header injection.
        from urllib.parse import quote
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        filename = "".join(c for c in filename if ord(c) >= 32 and ord(c) != 127)[:200] or "environment.toml"
        disposition = f"attachment; filename=\"environment.toml\"; filename*=UTF-8''{quote(filename, safe='')}"
        self._respond(200, text.encode("utf-8"), "text/plain; charset=utf-8", (("Content-Disposition", disposition),))

    def _static(self, path):
        name = "index.html" if path == "/" else path[1:]
        if name not in STATIC_FILES:
            raise _HTTPError(404, "NOT_FOUND", "Resource not found.")
        target = (self.server.assets / name).resolve()
        if target.parent != self.server.assets:
            raise _HTTPError(403, "ASSET_REJECTED", "Asset is outside the resource root.")
        try:
            payload = target.read_bytes()
        except FileNotFoundError:
            raise _HTTPError(404, "ASSET_NOT_FOUND", "Required product asset is missing.") from None
        self._respond(200, payload, STATIC_FILES[name])

    def _dispatch(self):
        try:
            origin = self._boundary()
            path, query = self._target()
            if path == "/health":
                if self.command != "GET":
                    raise _HTTPError(405, "METHOD_NOT_ALLOWED", "Method is not allowed for this route.")
                self._json(200, {"status": "ok", "product": "EnvManagerV3"})
                return
            if path == API or path.startswith(API + "/"):
                exchange = path == API + "/session/exchange"
                if not exchange:
                    self._authenticate()
                if self.command in ("POST", "PATCH", "PUT", "DELETE"):
                    if origin != self.server.origin:
                        raise _HTTPError(403, "ORIGIN_REQUIRED", "An exact Origin is required for mutations.")
                    if not exchange:
                        csrf = self._header("X-CSRF-Token") or ""
                        if not secrets.compare_digest(csrf.encode("utf-8"), self.server._csrf.encode("ascii")):
                            raise _HTTPError(403, "CSRF_REJECTED", "CSRF token is missing or invalid.")
                if exchange:
                    if self.command != "POST":
                        raise _HTTPError(405, "METHOD_NOT_ALLOWED", "Method is not allowed for this route.")
                    self._exchange(self._body())
                    return
                matches = [(verb, match, method) for verb, pattern, method in _ROUTES
                           if (match := pattern.fullmatch(path))]
                if not matches:
                    raise _HTTPError(404, "NOT_FOUND", "API route not found.")
                selected = next((entry for entry in matches if entry[0] == self.command), None)
                if selected is None:
                    raise _HTTPError(405, "METHOD_NOT_ALLOWED", "Method is not allowed for this route.")
                _, match, method = selected
                args = list(match.groups())
                if self.command in ("POST", "PATCH"):
                    args.append(self._body(MAX_IMPORT_BODY if method == "import_workspace" else MAX_BODY))
                elif method in ("environment_state", "history", "events"):
                    args.append(query)
                result = getattr(self.server.service, method)(*args)
                if not isinstance(result, dict):
                    raise TypeError("Service response must be a dict")
                if method == "export_workspace":
                    self._export(result)
                elif method == "bootstrap":
                    self._json(200, result, (("X-CSRF-Token", self.server._csrf),))
                else:
                    self._json(200, result)
                if method == "quit":
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path.startswith("/api/"):
                self._authenticate()
                raise _HTTPError(404, "NOT_FOUND", "API route not found.")
            if self.command != "GET":
                raise _HTTPError(405, "METHOD_NOT_ALLOWED", "Method is not allowed for this route.")
            self._static(path)
        except _HTTPError as error:
            self._error(error.status, error.code, error.message)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            # Only deliberate service errors have a public code/message.  Other
            # exceptions must not expose paths, raw values or session secrets.
            code, message = getattr(error, "code", None), getattr(error, "message", None)
            if isinstance(code, str) and isinstance(message, str):
                status = getattr(error, "status", 400)
                if type(status) is not int or not 400 <= status <= 599:
                    status = 400
                self._error(status, code, message, getattr(error, "details", None))
            else:
                self._error(500, "INTERNAL_ERROR", "The request could not be completed.")


def create_server(service, assets: Path, port=0) -> ThreadingHTTPServer:
    """Create (without starting) a server bound exclusively to IPv4 loopback."""
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be an integer from 0 to 65535")
    return _LocalServer(service, Path(assets), port)
