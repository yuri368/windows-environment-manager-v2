"""Windows IPC, protected receipts, package identity and fixed rule verification.

Native tests may exercise pipes as an ordinary user. This is NOT an assertion
that the current per-user/source distribution is a secure System product.
Production additionally requires the reviewed rule/approval envelope and a
machine-protected, pinned distribution (see system_capability).
"""
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import struct
import time
import sys
import subprocess
from contextvars import ContextVar

from .privileges import ElevationError, _api, _dll, _json_bytes, MAX_REQUEST_BYTES

PROTOCOL = 2
PIPE_PREFIX = "\\\\.\\pipe\\EnvManagerV3-"
ADMIN = "S-1-5-32-544"
SYSTEM = "S-1-5-18"
AUTHENTICATED = "S-1-5-11"
READ = 0x120089
FULL = 0x1F01FF
INVALID = C.c_void_p(-1).value
TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
RULE_NAMES = ("demo-plan.js", "config-format.js", "engine_worker.cjs")
_review_context = ContextVar("envmanager_v3_system_review", default=None)


def fail(code, message):
    raise ElevationError(code, message)


def system_capability():
    """Live package readiness; no test/acceptance flag or environment bypass.

    A ready installation still needs a one-use service review for each write.
    Native elevated acceptance is reported separately from runtime capability.
    """
    try:
        with verified_package() as package:
            return {"ready": True, "protocol": PROTOCOL, "review_required": True, **package}
    except (ElevationError, OSError, ValueError) as exc:
        return {"ready": False, "code": "SYSTEM_CAPABILITY_NOT_READY",
                "reasons": [getattr(exc, "code", "PACKAGE_IO")], "message": str(exc)}


def require_production():
    capability = system_capability()
    if not capability["ready"]:
        raise ElevationError(capability["code"], "Windows System capability is not ready.",
                             capability)
    return capability


def validate_review(envelope, plans=None, rules_sha256=None):
    """Bind the authority's already reviewed receipt, not an HTTP after payload.

    inputs.receipt is the FULL frozen server receipt, including User targets.
    Its digest is recomputed exactly as database.encode, before filtering System.
    operations is the per-System-target list of visible operation arrays.
    The helper separately recomputes verification inputs with its protected,
    pinned Node/worker; no user-selected code or executable path is accepted.
    """
    from .system_helper import validate_request, _same
    fields = {"protocol", "plan_id", "version", "plan_digest", "review_token",
              "intent", "inputs", "operations", "targets", "rules_sha256"}
    if (not isinstance(envelope, dict) or set(envelope) != fields
            or type(envelope["protocol"]) is not int or envelope["protocol"] != PROTOCOL
            or type(envelope["version"]) is not int or envelope["version"] < 1
            or any(not isinstance(envelope[k], str) or not 0 < len(envelope[k]) <= 1024
                   for k in ("plan_id", "review_token"))
            or not isinstance(envelope["intent"], dict) or not isinstance(envelope["inputs"], dict)
            or not isinstance(envelope["operations"], list)
            or not isinstance(envelope["plan_digest"], str)
            or re.fullmatch(r"[0-9a-f]{64}", envelope["plan_digest"]) is None
            or not isinstance(envelope["rules_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", envelope["rules_sha256"]) is None):
        fail("INVALID_REVIEW", "Incomplete or unsupported reviewed System envelope.")
    frozen = validate_request({"protocol": 1, "job_id": "0" * 32, "restore": False,
                               "plans": envelope["targets"]}, allow_type_change=True)["plans"]
    if plans is not None and frozen != plans:
        fail("REVIEW_MISMATCH", "System targets differ from the reviewed plan.")
    receipt = envelope["inputs"].get("receipt")
    if not isinstance(receipt, dict) or not isinstance(receipt.get("targets"), list):
        fail("INVALID_REVIEW", "inputs.receipt must contain the complete server receipt.")
    body = dict(receipt)
    stored_digest = body.pop("digest", None)
    if (stored_digest != envelope["plan_digest"] or hashlib.sha256(_json_bytes(body)).hexdigest() != stored_digest
            or receipt.get("planId") != envelope["plan_id"] or receipt.get("version") != envelope["version"]
            or receipt.get("intent") != envelope["intent"] or receipt.get("engineHash") != envelope["rules_sha256"]
            or receipt.get("status") != "ready"):
        fail("REVIEW_MISMATCH", "Reviewed receipt identity, digest or rule version changed.")
    selected, operations = [], []
    for target in receipt["targets"]:
        if not isinstance(target, dict) or target.get("scope") not in ("User", "System"):
            fail("INVALID_REVIEW", "Invalid reviewed target.")
        if target["scope"] != "System":
            continue
        plan = target.get("plan")
        if (not isinstance(plan, dict) or plan.get("valid") is not True or plan.get("needsOrdering")
                or not isinstance(plan.get("operations"), list)):
            fail("INVALID_REVIEW", "Reviewed target is blocked or has no operations.")
        before, after = target.get("beforeSnapshot"), plan.get("after")
        normalized = validate_request({"protocol": 1, "job_id": "0" * 32, "restore": False,
                    "plans": [{"scope": "System", "name": target.get("name"), "before": before, "after": after}]}, allow_type_change=True)["plans"][0]
        if not _same(plan.get("before"), before):
            # Engine ValueStates need not repeat scope/name; compare exact state fields.
            if (not isinstance(plan.get("before"), dict)
                    or any(plan["before"].get(k) != before[k] for k in ("exists", "raw", "type"))):
                fail("REVIEW_MISMATCH", "Plan before differs from its frozen snapshot.")
        if all(before[k] == after[k] for k in ("exists", "raw", "type")):
            continue
        if not plan["operations"]:
            fail("INVALID_REVIEW", "Changed target has no visible operation.")
        selected.append(normalized); operations.append(plan["operations"])
    if selected != frozen or operations != envelope["operations"]:
        fail("REVIEW_MISMATCH", "Visible System operations/after differ from the reviewed receipt.")
    if rules_sha256 is not None and envelope["rules_sha256"] != rules_sha256:
        fail("RULES_MISMATCH", "Installed rules differ from the reviewed rule version.")
    data = _json_bytes(envelope)
    if len(data) > MAX_REQUEST_BYTES:
        fail("IPC_SIZE", "Review envelope exceeds the bound.")
    return decode(data)


@contextmanager
def system_review(envelope):
    """Trusted Executor integration: one frozen review for one launch attempt.

    The caller verifies/consumes reviewToken in its DB BEFORE entering here.
    ContextVar prevents concurrent HTTP threads sharing an authorization.
    """
    frozen = _json_bytes(validate_review(envelope))
    state = {"data": frozen, "used": False}
    token = _review_context.set(state)
    try:
        yield
    finally:
        _review_context.reset(token)


def consume_review(plans, rules_sha256):
    state = _review_context.get()
    if state is None or state["used"]:
        fail("REVIEW_REQUIRED", "A fresh server-verified review is required; never retry this launch.")
    state["used"] = True  # Also consumed on validation/UAC cancellation/unknown launch.
    return validate_review(decode(state["data"]), plans, rules_sha256)


def _operations_content(operations):
    # MOVE's opaque UI IDs identify rows, but orderIndices authorizes contents.
    return [{k: v for k, v in op.items() if not (op.get("action") == "MOVE" and k == "order")}
            for op in operations]


def _rule_call(package, method, options):
    """Only the installed, pinned Node + fixed worker; no command/code fields.

    This is the explicit protected-runtime exception: the helper never loads
    browser assets or Node from user paths, PATH/NODE_OPTIONS or HTTP parameters.
    """
    if method not in ("build", "buildBatch", "buildElementEdit") or not isinstance(options, dict):
        fail("RULE_PROOF", "Unsupported fixed rule operation.")
    root = Path(package["root"])
    node = root / "_internal/runtime/node.exe"
    worker = root / "_internal/envmanager_v3/engine_worker.cjs"
    assets = root / "_internal/ui-v3"
    request = {"method": method, "options": options, "expectedEngineHash": package["rules_sha256"]}
    data = _json_bytes(request)
    if len(data) > MAX_REQUEST_BYTES:
        fail("IPC_SIZE", "Rule input exceeds the bound.")
    windows = C.create_unicode_buffer(32768)
    count = _api(_dll("kernel32"), "GetWindowsDirectoryW", W.UINT, W.LPWSTR, W.UINT)(windows, len(windows))
    if not 0 < count < len(windows):
        fail("RULE_RUNTIME", "Cannot locate the trusted Windows runtime.")
    # In particular, do not inherit NODE_OPTIONS/ NODE_PATH/ injection settings.
    environment = {"SystemRoot": windows.value, "WINDIR": windows.value, "PATH": str(node.parent)}
    try:
        process = subprocess.run([str(node), str(worker), str(assets)], input=data,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 cwd=str(root), env=environment, timeout=30,
                                 creationflags=0x08000000, shell=False, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ElevationError("RULE_RUNTIME", "Pinned rule worker failed; no environment write authorized.") from exc
    reply = decode(process.stdout)
    if process.returncode or not isinstance(reply, dict) or reply.get("ok") is not True or not isinstance(reply.get("result"), dict):
        fail("RULE_PROOF", "Pinned rule worker rejected the frozen inputs.")
    return reply["result"]


def verify_rule_proofs(review, package, *, rule_call=None):
    """Recompute the SAME rules, then derive only fixed registry projections."""
    from .environment import SUPPORTED_TYPES, canonical_name
    from .system_helper import _state
    call = rule_call or _rule_call
    for target in review["inputs"]["receipt"]["targets"]:
        if target["scope"] != "System":
            continue
        before, plan = target["beforeSnapshot"], target["plan"]
        proof = target.get("verification")
        if not isinstance(proof, dict):
            fail("RULE_PROOF", "Frozen verification inputs are missing.")
        method = proof.get("method")
        if method == "restore":
            if (set(proof) != {"method", "origin", "desiredSnapshot", "requiresExplicitReview"}
                    or proof["requiresExplicitReview"] is not True):
                fail("RULE_PROOF", "Whole restore requires explicit reviewed source snapshots.")
            desired = _state(proof["desiredSnapshot"], target["name"], before=True)
            origin = proof["origin"]
            mode = plan.get("mode")
            if origin is None:
                if desired != before or plan["operations"]:
                    fail("RULE_PROOF", "An origin-free restore can only be a no-op.")
            else:
                if (not isinstance(origin, dict) or origin.get("scope") != "System"
                        or canonical_name(origin.get("name", "")) != canonical_name(target["name"])):
                    fail("RULE_PROOF", "Restore source belongs to another target.")
                source = _state(origin.get("before"), target["name"], before=True)
                recovery = mode == "recovery" and review["intent"].get("kind") == "recovery"
                if not recovery:
                    verified = origin.get("verifiedAfter") or origin.get("before")
                    if any(before.get(k) != verified.get(k) for k in ("scope", "name", "exists", "raw", "type", "revision")):
                        fail("RULE_PROOF", "Historical restore current snapshot/revision differs from its verified source.")
                allowed = (source, before) if recovery and review["intent"].get("resolution") == "accept_current" else (source,)
                if not any(all(desired.get(k) == value.get(k) for k in ("scope", "name", "exists", "raw", "type", "revision")) for value in allowed):
                    fail("RULE_PROOF", "Whole restore after was not derived from the original before.")
                expected_action = {"history-restore": "RESTORE_HISTORY", "contribution-whole-restore": "RESTORE_CONTRIBUTION", "recovery": "RECOVER_BEFORE"}.get(mode)
                if expected_action is None:
                    fail("RULE_PROOF", "Unknown whole restore mode.")
                operations = [] if all(before[k] == desired[k] for k in ("exists", "raw", "type")) else [{"type": "restore", "action": expected_action}]
                actual = [{k: v for k, v in op.items() if k != "contributionId"} for op in plan["operations"]]
                if operations != actual:
                    fail("RULE_PROOF", "Whole restore operations do not match its source.")
            if any(plan["after"].get(k) != desired[k] for k in ("exists", "raw", "type")):
                fail("RULE_PROOF", "Whole restore value differs from its source snapshot.")
            continue
        allowed = {"method", "options", "registryType", "deleteVariable", "orderIndices"}
        if (method not in ("build", "buildBatch", "buildElementEdit") or set(proof) - allowed
                or not {"method", "options", "registryType"} <= proof.keys()
                or proof["registryType"] not in SUPPORTED_TYPES or not isinstance(proof["options"], dict)):
            fail("RULE_PROOF", "Unsupported frozen rule proof fields.")
        options = proof["options"]
        if (options.get("snapshot") != before or not isinstance(options.get("target"), dict)
                or options["target"].get("scope") != "System"
                or canonical_name(options["target"].get("name", "")) != canonical_name(target["name"])):
            fail("RULE_PROOF", "Rule inputs differ from the frozen target/snapshot.")
        computed = call(package, method, options)
        if (not isinstance(computed, dict) or computed.get("valid") is not True
                or not isinstance(computed.get("operations"), list) or not isinstance(computed.get("after"), dict)):
            fail("RULE_PROOF", "The same V3 rules rejected the frozen inputs.")
        after = dict(computed["after"])
        operations = list(computed["operations"])
        if "deleteVariable" in proof:
            if method != "buildElementEdit" or type(proof["deleteVariable"]) is not bool:
                fail("RULE_PROOF", "Explicit deletion is only valid for a manual edit.")
            if proof["deleteVariable"]:
                after = {"exists": False, "raw": None, "type": None}
                if before["exists"] and not operations:
                    operations.append({"type": "remove", "action": "REMOVE_VARIABLE"})
        if after["exists"]:
            after["type"] = proof["registryType"]
            if method != "buildElementEdit" and proof["registryType"] != (before["type"] or "REG_EXPAND_SZ"):
                fail("RULE_PROOF", "Registry type was not derived from the original snapshot.")
            if method == "buildElementEdit" and before["exists"] and before["type"] != proof["registryType"]:
                operations.append({"type": "type-change", "action": "TYPE_CHANGE", "before": before["type"], "after": proof["registryType"]})
        else:
            after.update(raw=None, type=None)
        if "orderIndices" in proof:
            order = proof["orderIndices"]
            if not after["exists"] and order == []:
                if any(after.get(k) != plan["after"].get(k) for k in ("exists", "raw", "type")) or operations != plan["operations"]:
                    fail("RULE_PROOF", "An absent empty ordering candidate cannot create a value.")
                continue
            if method not in ("build", "buildBatch") or not after["exists"] or computed.get("special"):
                fail("RULE_PROOF", "Ordering is only valid for a non-special candidate.")
            values = after["raw"].split(";")
            if (not isinstance(order, list) or any(type(i) is not int for i in order)
                    or sorted(order) != list(range(len(values)))):
                fail("RULE_PROOF", "Ordering must be a complete occurrence-index permutation.")
            raw = ";".join(values[i] for i in order)
            if raw != after["raw"]:
                operations.append({"type": "move", "action": "MOVE"})
            after["raw"] = raw
        if (any(after.get(k) != plan["after"].get(k) for k in ("exists", "raw", "type"))
                or _operations_content(operations) != _operations_content(plan["operations"])):
            fail("RULE_PROOF", "Recomputed operations/after differ from the exact reviewed plan.")


def sid_valid(sid):
    return isinstance(sid, str) and re.fullmatch(r"S-1-(?:[0-9]+-)*[0-9]+", sid) is not None


def pipe_name():
    return PIPE_PREFIX + secrets.token_hex(32)


def validate_pipe_name(name):
    if not isinstance(name, str) or re.fullmatch(re.escape(PIPE_PREFIX) + r"[0-9a-f]{64}", name) is None:
        fail("INVALID_PIPE", "Only a random, local product pipe is accepted.")
    return name


def decode(data):
    from .system_helper import _unique_object, _invalid_constant
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_REQUEST_BYTES:
        fail("IPC_SIZE", "IPC frame exceeds its bound.")
    try:
        value = json.loads(data, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 128:
                fail("IPC_JSON", "IPC nesting exceeds the bound.")
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        return value
    except (ValueError, UnicodeError, RecursionError, ElevationError) as exc:
        raise ElevationError("IPC_JSON", "Invalid IPC JSON.") from exc


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    sid: str
    session: int
    created: int
    image: str
    image_hash: str
    elevated: bool
    admin: bool


def verify_peer(actual, expected, *, elevated):
    # expected comes from a held kernel process handle, never a pipe message.
    if actual != expected or (elevated and not (actual.elevated and actual.admin)):
        fail("PEER_IDENTITY", "Pipe peer is not the bound process/token/image.")


def verify_helper(actual, launched, parent):
    verify_peer(actual, launched, elevated=True)
    if (actual.session != parent.session or actual.image.casefold() != parent.image.casefold()
            or actual.image_hash != parent.image_hash):
        fail("PEER_IDENTITY", "Helper session or fixed package image differs from its parent.")
    # UAC's kernel parent may be AppInfo, so do not trust/check a claimed PPID.
    # The protocol instead pins the actual pipe server PID + creation time and
    # holds its process handle for the entire operation (also for OTS UAC).


class Win32:
    def __init__(self):
        if os.name != "nt":
            fail("UNAVAILABLE", "Windows native IPC requires Windows.")
        self.k, self.a = _dll("kernel32"), _dll("advapi32")

    def kcall(self, name, restype, argtypes, *args):
        return _api(self.k, name, restype, *argtypes)(*args)

    def acall(self, name, restype, argtypes, *args):
        return _api(self.a, name, restype, *argtypes)(*args)

    def check(self, value, code="NATIVE_IPC"):
        if not value:
            raise ElevationError(code, "Windows native security operation failed.",
                                 {"winerror": C.get_last_error()})
        return value

    def close(self, handle):
        if handle not in (None, 0, INVALID):
            self.kcall("CloseHandle", W.BOOL, [W.HANDLE], handle)

    def sid_text(self, sid):
        text = W.LPWSTR()
        self.check(self.acall("ConvertSidToStringSidW", W.BOOL,
                             [C.c_void_p, C.POINTER(W.LPWSTR)], sid, C.byref(text)))
        try:
            return text.value
        finally:
            self.kcall("LocalFree", C.c_void_p, [C.c_void_p], C.cast(text, C.c_void_p))

    @contextmanager
    def security(self, sddl):
        descriptor = C.c_void_p()
        self.check(self.acall("ConvertStringSecurityDescriptorToSecurityDescriptorW", W.BOOL,
                             [W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p), C.c_void_p],
                             sddl, 1, C.byref(descriptor), None))
        class SA(C.Structure):
            _fields_ = [("length", W.DWORD), ("descriptor", C.c_void_p), ("inherit", W.BOOL)]
        attributes = SA(C.sizeof(SA), descriptor, False)
        try:
            yield attributes
        finally:
            self.kcall("LocalFree", C.c_void_p, [C.c_void_p], descriptor)

    @contextmanager
    def process(self, pid):
        if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
            fail("PEER_IDENTITY", "Invalid kernel process id.")
        handle = self.check(self.kcall("OpenProcess", W.HANDLE, [W.DWORD, W.BOOL, W.DWORD],
                                      0x1000 | 0x100000, False, pid))
        try:
            yield handle
        finally:
            self.close(handle)

    def identity(self, handle):
        if self.kcall("WaitForSingleObject", W.DWORD, [W.HANDLE, W.DWORD], handle, 0) != 258:
            fail("PEER_EXITED", "Bound process is no longer alive.")
        pid = self.check(self.kcall("GetProcessId", W.DWORD, [W.HANDLE], handle))
        size, path = W.DWORD(32768), C.create_unicode_buffer(32768)
        self.check(self.kcall("QueryFullProcessImageNameW", W.BOOL,
                             [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)],
                             handle, 0, path, C.byref(size)))
        times = [W.FILETIME() for _ in range(4)]
        self.check(self.kcall("GetProcessTimes", W.BOOL, [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4,
                             handle, *(C.byref(t) for t in times)))
        created = times[0].dwLowDateTime | (times[0].dwHighDateTime << 32)
        token = W.HANDLE()
        self.check(self.acall("OpenProcessToken", W.BOOL,
                             [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)], handle, 8, C.byref(token)))
        try:
            def info(kind):
                count = W.DWORD()
                args = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
                self.acall("GetTokenInformation", W.BOOL, args, token, kind, None, 0, C.byref(count))
                if not count.value or count.value > 1024 * 1024:
                    fail("PEER_TOKEN", "Invalid token information size.")
                buffer = C.create_string_buffer(count.value)
                self.check(self.acall("GetTokenInformation", W.BOOL, args,
                                      token, kind, buffer, count, C.byref(count)))
                return buffer
            user = info(1)  # Keep the SID's backing allocation alive during conversion.
            sid = self.sid_text(C.cast(user, C.POINTER(C.c_void_p))[0])
            session = C.cast(info(12), C.POINTER(W.DWORD))[0]
            elevated = bool(C.cast(info(20), C.POINTER(W.DWORD))[0])
            class Group(C.Structure):
                _fields_ = [("sid", C.c_void_p), ("attributes", W.DWORD)]
            class Groups(C.Structure):
                _fields_ = [("count", W.DWORD), ("groups", Group * 1)]
            groups = info(2)
            count = C.cast(groups, C.POINTER(W.DWORD))[0]
            if count > 4096 or Groups.groups.offset + count * C.sizeof(Group) > len(groups):
                fail("PEER_TOKEN", "Invalid token groups.")
            entries = (Group * count).from_buffer(groups, Groups.groups.offset)
            admin = any(g.attributes & 4 and not g.attributes & 0x10
                        and self.sid_text(g.sid) == ADMIN for g in entries)
        finally:
            self.close(token)
        # Pin the image during hashing; deny replacement/writes while inspecting.
        with self.file_handle(path.value, 0x80000000, share=1) as image:
            image_hash = hashlib.sha256(self.read_all(image, 512 * 1024 * 1024)).hexdigest()
        return ProcessIdentity(pid, sid, session, created, str(Path(path.value)), image_hash, elevated, admin)

    @contextmanager
    def file_handle(self, path, access=0x20000, *, share=3, create=3, attributes=None, directory=False):
        handle = self.kcall("CreateFileW", W.HANDLE,
                           [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE],
                           str(path), access, share, C.byref(attributes) if attributes is not None else None,
                           create, 0x00200000 | (0x02000000 if directory else 0), None)
        if handle in (None, INVALID):
            self.check(False, "SECURE_FILE")
        try:
            # FILE_ATTRIBUTE_TAG_INFO, queried on the actual handle, no lstat race.
            tag = (W.DWORD * 2)()
            self.check(self.kcall("GetFileInformationByHandleEx", W.BOOL,
                                 [W.HANDLE, C.c_int, C.c_void_p, W.DWORD], handle, 9, tag, C.sizeof(tag)))
            if tag[0] & 0x400:
                fail("UNSAFE_PATH", "Reparse points are forbidden.")
            yield handle
        finally:
            self.close(handle)

    def read_all(self, handle, limit=MAX_REQUEST_BYTES):
        chunks, total = [], 0
        while True:
            buffer, read = C.create_string_buffer(min(65536, limit + 1 - total)), W.DWORD()
            self.check(self.kcall("ReadFile", W.BOOL,
                                 [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p],
                                 handle, buffer, len(buffer), C.byref(read), None))
            if not read.value:
                return b"".join(chunks)
            chunks.append(buffer.raw[:read.value]); total += read.value
            if total > limit:
                fail("IPC_SIZE", "File exceeds the allowed bound.")

    def write_all(self, handle, data):
        offset = 0
        while offset < len(data):
            count = W.DWORD()
            buffer = C.create_string_buffer(data[offset:])
            self.check(self.kcall("WriteFile", W.BOOL,
                                 [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p],
                                 handle, buffer, len(data) - offset, C.byref(count), None))
            if not count.value:
                fail("RECEIPT_IO", "No receipt bytes persisted.")
            offset += count.value
        self.check(self.kcall("FlushFileBuffers", W.BOOL, [W.HANDLE], handle))

    def acl(self, handle):
        owner, dacl, descriptor = C.c_void_p(), C.c_void_p(), C.c_void_p()
        result = self.acall("GetSecurityInfo", W.DWORD,
                            [W.HANDLE, C.c_int, W.DWORD] + [C.POINTER(C.c_void_p)] * 5,
                            handle, 1, 5, C.byref(owner), None, C.byref(dacl), None, C.byref(descriptor))
        if result:
            fail("UNSAFE_ACL", "Cannot inspect object security.")
        try:
            if not owner.value or not dacl.value:
                fail("UNSAFE_ACL", "Missing owner or null DACL.")
            control, revision = W.WORD(), W.DWORD()
            self.check(self.acall("GetSecurityDescriptorControl", W.BOOL,
                                 [C.c_void_p, C.POINTER(W.WORD), C.POINTER(W.DWORD)],
                                 descriptor, C.byref(control), C.byref(revision)))
            # ACL header: BYTE revision, BYTE reserved, WORD size/count/reserved.
            count = C.cast(dacl, C.POINTER(W.WORD))[2]
            entries = []
            for index in range(count):
                ace = C.c_void_p()
                self.check(self.acall("GetAce", W.BOOL,
                                     [C.c_void_p, W.DWORD, C.POINTER(C.c_void_p)], dacl, index, C.byref(ace)))
                header = (C.c_ubyte * 4).from_address(ace.value)
                if header[1] & 8:  # inherit-only does not grant access to this object
                    continue
                if header[0] != 0:
                    fail("UNSAFE_ACL", "Unsupported security ACE.")
                mask = W.DWORD.from_address(ace.value + 4).value
                entries.append((self.sid_text(ace.value + 8), mask))
            return self.sid_text(owner), bool(control.value & 0x1000), entries
        finally:
            self.kcall("LocalFree", C.c_void_p, [C.c_void_p], descriptor)


def verify_package_acl(owner, protected, entries):
    """Allow normal inherited Program Files read/execute ACLs, deny user writes."""
    trusted = {ADMIN, SYSTEM, TRUSTED_INSTALLER}
    if owner not in trusted or not entries:
        fail("PACKAGE_ACL", "Package owner or DACL is not machine protected.")
    # Generic all/write, delete, change owner/DACL, file/dir writes and delete-child.
    writable = 0x10000000 | 0x40000000 | 0xD0156
    if any(sid not in trusted and mask & writable for sid, mask in entries):
        fail("PACKAGE_ACL", "Package grants non-administrators write/replace access.")


def known_folder(program_files=False):
    class GUID(C.Structure):
        _fields_ = [("a", W.DWORD), ("b", W.WORD), ("c", W.WORD), ("d", C.c_ubyte * 8)]
    if program_files:  # FOLDERID_ProgramFilesX64, never %ProgramFiles% from environment.
        folder = GUID(0x6D809377, 0x6AF0, 0x444B, (C.c_ubyte * 8)(0x89, 0x57, 0xA3, 0x77, 0x3F, 2, 0x20, 0x0E))
    else:  # FOLDERID_ProgramData
        folder = GUID(0x62AB5D82, 0xFDC1, 0x4DC3, (C.c_ubyte * 8)(0xA9, 0xDD, 7, 0xD, 0x1D, 0x49, 0x5D, 0x97))
    result = W.LPWSTR()
    status = _api(_dll("shell32"), "SHGetKnownFolderPath", C.c_long,
                  C.POINTER(GUID), W.DWORD, W.HANDLE, C.POINTER(W.LPWSTR))(
                      C.byref(folder), 0, None, C.byref(result))
    if status != 0:
        fail("PACKAGE_LOCATION", "Cannot resolve the fixed machine known folder.")
    try:
        return Path(result.value)
    finally:
        _api(_dll("ole32"), "CoTaskMemFree", None, C.c_void_p)(C.cast(result, C.c_void_p))


def package_relative(value):
    # Refuse ADS, drive/UNC paths, aliases, parent traversal and separators that
    # Windows would reinterpret. Manifest is admin protected but still bounded.
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value or "\0" in value
            or any(p in ("", ".", "..") or p.endswith((".", " ")) for p in value.split("/"))
            or Path(value).is_absolute()):
        fail("PACKAGE_SCHEMA", "Manifest path is not a canonical package-relative file.")
    return value


def validate_manifest(manifest):
    if (not isinstance(manifest, dict)
            or set(manifest) != {"protocol", "package_id", "version", "entry", "rules", "files"}
            or type(manifest["protocol"]) is not int or manifest["protocol"] != PROTOCOL
            or manifest["package_id"] != "EnvManagerV3" or manifest["entry"] != "EnvManagerV3.exe"
            or not isinstance(manifest["version"], str) or not 0 < len(manifest["version"]) <= 128
            or not isinstance(manifest["files"], dict) or not 1 <= len(manifest["files"]) <= 10000
            or not isinstance(manifest["rules"], list) or len(manifest["rules"]) != len(RULE_NAMES)):
        fail("PACKAGE_SCHEMA", "Invalid protected package manifest.")
    seen = set()
    for path, digest in manifest["files"].items():
        package_relative(path)
        if (path.casefold() in seen or path.casefold() == "system-package.json"
                or not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
            fail("PACKAGE_SCHEMA", "Duplicate or invalid package file/hash.")
        seen.add(path.casefold())
    for name, rule in zip(RULE_NAMES, manifest["rules"]):
        if (not isinstance(rule, dict) or set(rule) != {"name", "path"} or rule["name"] != name
                or rule["path"] not in manifest["files"]):
            fail("PACKAGE_SCHEMA", "The three rule resources must have the fixed order and names.")
    files = manifest["files"]
    required_rules = ("_internal/ui-v3/demo-plan.js", "_internal/ui-v3/config-format.js", "_internal/envmanager_v3/engine_worker.cjs")
    if (tuple(r["path"] for r in manifest["rules"]) != required_rules
            or manifest["entry"] not in files or "_internal/runtime/node.exe" not in files
            or not any(p.endswith("/base_library.zip") for p in files)
            or not any(re.fullmatch(r"python[0-9]+\.dll", p.split("/")[-1], re.I) for p in files)):
        fail("PACKAGE_SCHEMA", "Manifest omits a required EXE/Python/Node runtime.")
    return manifest


@contextmanager
def verified_package(*, win=None):
    """Check and PIN machine package before UAC through final receipt import.

    The protected owner/DACL establishes trust. Manifest SHA is a content pin,
    never self-reported identity. All listed files and their directories remain
    open without write/delete sharing until the operation ends.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        fail("PACKAGE_SOURCE_MODE", "System writes require the fixed machine-installed executable.")
    win = win or Win32()
    program_files = known_folder(True)
    root = program_files / "EnvManagerV3"
    if (Path(sys.executable).absolute() != root / "EnvManagerV3.exe"
            or Path(getattr(sys, "_MEIPASS", "")).absolute() != root / "_internal"):
        fail("PACKAGE_LOCATION", "Running image/resources are outside the fixed machine installation.")
    with ExitStack() as stack:
        pinned = set()
        def pin_directory(path, check=True):
            key = str(path).casefold()
            if key not in pinned:
                handle = stack.enter_context(win.file_handle(path, directory=True, share=1))
                if check:
                    verify_package_acl(*win.acl(handle))
                pinned.add(key)
        for ancestor in reversed((program_files, *program_files.parents)):
            pin_directory(ancestor, check=ancestor == program_files)
        pin_directory(root)
        handle = stack.enter_context(win.file_handle(root / "system-package.json", 0x80000000 | 0x20000, share=1))
        verify_package_acl(*win.acl(handle))
        manifest_data = win.read_all(handle)
        manifest = validate_manifest(decode(manifest_data))
        rule_data = {}
        image_hash = None
        rule_paths = {r["path"] for r in manifest["rules"]}
        for relative, expected in manifest["files"].items():
            path = root / relative
            directories = []
            directory = path.parent
            while directory != root:
                directories.append(directory); directory = directory.parent
            for directory in reversed(directories):
                pin_directory(directory)
            handle = stack.enter_context(win.file_handle(path, 0x80000000 | 0x20000, share=1))
            verify_package_acl(*win.acl(handle))
            data = win.read_all(handle, 512 * 1024 * 1024)
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                fail("PACKAGE_HASH", "Installed package file differs from the protected manifest.")
            if relative in rule_paths:
                rule_data[relative] = data
            if relative == manifest["entry"]:
                image_hash = actual
        rules = hashlib.sha256()
        for rule in manifest["rules"]:
            rules.update(rule["name"].encode("ascii") + b"\0" + rule_data[rule["path"]] + b"\0")
        yield {"root": str(root), "package_digest": hashlib.sha256(manifest_data).hexdigest(),
               "rules_sha256": rules.hexdigest(), "image_hash": image_hash,
               "version": manifest["version"]}


def verify_receipt_acl(owner, protected, entries, readers):
    if owner not in (ADMIN, SYSTEM) or not protected or not entries:
        fail("UNSAFE_ACL", "Receipt owner/DACL is not administrator protected.")
    writers, readable = set(), set()
    for sid, mask in entries:
        if sid in (ADMIN, SYSTEM):
            if mask != FULL:
                fail("UNSAFE_ACL", "Administrative receipt access is incomplete.")
            writers.add(sid)
        elif sid in readers and mask == READ:
            readable.add(sid)
        else:
            fail("UNSAFE_ACL", "Receipt grants unexpected or writable user access.")
    if writers != {ADMIN, SYSTEM} or not readers <= readable:
        fail("UNSAFE_ACL", "Required receipt access is missing.")


def receipt_sddl(readers):
    if any(not sid_valid(s) or s in (ADMIN, SYSTEM) for s in readers):
        fail("INVALID_SID", "Invalid receipt reader SID.")
    return ("O:BAG:BAD:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)" +
            "".join(f"(A;OICI;0x{READ:x};;;{sid})" for sid in sorted(readers)))


class NativePipe:
    """One instance, overlapped bounded operations, no remote clients/reconnect.

    Client SQOS is Identification, preventing the server from impersonating a
    connected elevated helper. Identity always comes from kernel PID APIs.
    """
    def __init__(self, win, handle, *, server, timeout=30):
        self.win, self.handle, self.server, self.timeout = win, handle, server, timeout
        self.connected = not server

    @classmethod
    def create(cls, sid, *, win=None, name=None, timeout=30):
        if not sid_valid(sid):
            fail("INVALID_SID", "Invalid pipe owner SID.")
        win, name = win or Win32(), validate_pipe_name(name or pipe_name())
        # 0x12019b: duplex data rights without FILE_CREATE_PIPE_INSTANCE (4).
        sddl = f"D:P(A;;0x12019b;;;{sid})(A;;0x12019b;;;BA)(A;;0x12019b;;;SY)"
        with win.security(sddl) as security:
            handle = win.kcall("CreateNamedPipeW", W.HANDLE,
                               [W.LPCWSTR, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, W.DWORD, C.c_void_p],
                               name, 3 | 0x40000000 | 0x80000, 8, 1, 65536, 65536, 0, C.byref(security))
        if handle in (None, INVALID):
            win.check(False)
        pipe = cls(win, handle, server=True, timeout=timeout)
        pipe.name = name
        return pipe

    @classmethod
    def connect(cls, name, *, win=None, timeout=30):
        win, name = win or Win32(), validate_pipe_name(name)
        handle = win.kcall("CreateFileW", W.HANDLE,
                           [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE],
                           name, 0x12019b, 0, None, 3,
                           0x40000000 | 0x100000 | 0x10000, None)
        if handle in (None, INVALID):
            win.check(False)
        return cls(win, handle, server=False, timeout=timeout)

    def _io(self, operation, buffer=None, count=0):
        class Overlapped(C.Structure):
            _fields_ = [("internal", C.c_size_t), ("internal_high", C.c_size_t),
                        ("offset", W.DWORD), ("offset_high", W.DWORD), ("event", W.HANDLE)]
        event = self.win.check(self.win.kcall("CreateEventW", W.HANDLE,
                                            [C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR], None, True, False, None))
        ov, transferred = Overlapped(), W.DWORD()
        ov.event = event
        try:
            if operation == "ConnectNamedPipe":
                ok = self.win.kcall(operation, W.BOOL, [W.HANDLE, C.c_void_p], self.handle, C.byref(ov))
            else:
                ok = self.win.kcall(operation, W.BOOL,
                                   [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p],
                                   self.handle, buffer, count, C.byref(transferred), C.byref(ov))
            error = C.get_last_error() if not ok else 0
            if operation == "ConnectNamedPipe" and error == 535:
                return 0  # client connected before ConnectNamedPipe
            if not ok and error != 997:
                self.win.check(False)
            if not ok:
                wait = self.win.kcall("WaitForSingleObject", W.DWORD, [W.HANDLE, W.DWORD],
                                     event, max(1, int(self.timeout * 1000)))
                if wait != 0:
                    self.win.kcall("CancelIoEx", W.BOOL, [W.HANDLE, C.c_void_p], self.handle, C.byref(ov))
                    # Complete cancellation before freeing OVERLAPPED/buffer.
                    self.win.kcall("GetOverlappedResult", W.BOOL,
                                   [W.HANDLE, C.c_void_p, C.POINTER(W.DWORD), W.BOOL],
                                   self.handle, C.byref(ov), C.byref(transferred), True)
                    fail("IPC_TIMEOUT", "Pipe operation timed out; never replay the operation.")
                self.win.check(self.win.kcall("GetOverlappedResult", W.BOOL,
                                              [W.HANDLE, C.c_void_p, C.POINTER(W.DWORD), W.BOOL],
                                              self.handle, C.byref(ov), C.byref(transferred), False))
            return transferred.value
        finally:
            self.win.close(event)

    def accept(self):
        if not self.server or self.connected:
            fail("IPC_REPLAY", "A pipe accepts exactly one peer.")
        self._io("ConnectNamedPipe")
        self.connected = True

    def peer_pid(self):
        pid = W.ULONG()
        name = "GetNamedPipeClientProcessId" if self.server else "GetNamedPipeServerProcessId"
        self.win.check(self.win.kcall(name, W.BOOL, [W.HANDLE, C.POINTER(W.ULONG)],
                                     self.handle, C.byref(pid)))
        return pid.value

    def _read(self, count):
        result = bytearray()
        deadline = time.monotonic() + self.timeout
        while len(result) < count:
            if time.monotonic() > deadline:
                fail("IPC_TIMEOUT", "Pipe frame deadline exceeded.")
            buffer = C.create_string_buffer(min(65536, count - len(result)))
            length = self._io("ReadFile", buffer, len(buffer))
            if not length:
                fail("IPC_DISCONNECTED", "Pipe peer disconnected.")
            result.extend(buffer.raw[:length])
        return bytes(result)

    def receive(self):
        length = struct.unpack("<I", self._read(4))[0]
        if not 0 < length <= MAX_REQUEST_BYTES:
            fail("IPC_SIZE", "Pipe frame exceeds its bound.")
        return decode(self._read(length))

    def send(self, value):
        payload = _json_bytes(value)
        if not 0 < len(payload) <= MAX_REQUEST_BYTES:
            fail("IPC_SIZE", "Pipe frame exceeds its bound.")
        data, offset = struct.pack("<I", len(payload)) + payload, 0
        deadline = time.monotonic() + self.timeout
        while offset < len(data):
            if time.monotonic() > deadline:
                fail("IPC_TIMEOUT", "Pipe frame deadline exceeded.")
            buffer = C.create_string_buffer(data[offset:offset + 65536])
            length = self._io("WriteFile", buffer, min(65536, len(data) - offset))
            if not length:
                fail("IPC_DISCONNECTED", "Pipe peer disconnected.")
            offset += length

    def close(self):
        if self.handle is not None:
            self.win.close(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class ReceiptStore:
    """Admin-owned nonce directory pinned by non-delete-sharing handles.

    No caller-selected production root. Existing weak directories are rejected,
    never repaired/adopted. This object does not elevate the caller.
    """
    def __init__(self, operator_sid, job_id, *, win=None):
        if not sid_valid(operator_sid) or operator_sid in (ADMIN, SYSTEM):
            fail("INVALID_SID", "Invalid operator SID.")
        if not isinstance(job_id, str) or re.fullmatch(r"[0-9a-f]{32}", job_id) is None:
            fail("INVALID_REQUEST", "Invalid receipt operation id.")
        self.win = win or Win32()
        self.sid, self.job_id = operator_sid, job_id
        self.stack, self.sequence = ExitStack(), 0
        self.journal = None
        self.binding = {}

    def _program_data(self):
        # SHGetKnownFolderPath(FOLDERID_ProgramData), not user-controlled env.
        class GUID(C.Structure):
            _fields_ = [("a", W.DWORD), ("b", W.WORD), ("c", W.WORD), ("d", C.c_ubyte * 8)]
        folder = GUID(0x62AB5D82, 0xFDC1, 0x4DC3, (C.c_ubyte * 8)(0xA9, 0xDD, 7, 0xD, 0x1D, 0x49, 0x5D, 0x97))
        result = W.LPWSTR()
        status = _api(_dll("shell32"), "SHGetKnownFolderPath", C.c_long,
                      C.POINTER(GUID), W.DWORD, W.HANDLE, C.POINTER(W.LPWSTR))(
                          C.byref(folder), 0, None, C.byref(result))
        if status != 0:
            fail("RECEIPT_IO", "Cannot resolve machine ProgramData.")
        try:
            return Path(result.value)
        finally:
            _api(_dll("ole32"), "CoTaskMemFree", None, C.c_void_p)(C.cast(result, C.c_void_p))

    def _directory(self, path, readers, *, exclusive=False):
        with self.win.security(receipt_sddl(readers)) as sa:
            created = self.win.kcall("CreateDirectoryW", W.BOOL, [W.LPCWSTR, C.c_void_p], str(path), C.byref(sa))
        if not created and (C.get_last_error() != 183 or exclusive):
            fail("RECEIPT_REPLAY" if exclusive else "RECEIPT_IO", "Cannot create protected receipt directory.")
        handle = self.stack.enter_context(self.win.file_handle(path, directory=True))
        verify_receipt_acl(*self.win.acl(handle), readers)

    def __enter__(self):
        try:
            with self.win.process(os.getpid()) as handle:
                identity = self.win.identity(handle)
            if not identity.elevated or not identity.admin:
                fail("HELPER_NOT_ADMIN", "Only an elevated administrator may create receipts.")
            base = self._program_data()
            # Pin every existing ancestor; reject junctions before creating.
            for ancestor in reversed((base, *base.parents)):
                self.stack.enter_context(self.win.file_handle(ancestor, directory=True))
            product = base / "EnvManagerV3"
            self._directory(product, {AUTHENTICATED})
            root = product / "system-receipts"
            self._directory(root, {AUTHENTICATED})
            operator = root / self.sid
            self._directory(operator, {self.sid})
            self.path = operator / self.job_id
            self._directory(self.path, {self.sid}, exclusive=True)
            with self.win.security(receipt_sddl({self.sid})) as sa:
                self.journal = self.stack.enter_context(self.win.file_handle(
                    self.path / "progress.jsonl", 0x40000000 | 0x20000, create=1, attributes=sa, share=1))
            verify_receipt_acl(*self.win.acl(self.journal), {self.sid})
            return self
        except BaseException:
            self.stack.close()
            raise

    def append(self, event, **data):
        self.sequence += 1
        record = {"receiptSequence": self.sequence, "event": event, "binding": self.binding, **data}
        self.win.write_all(self.journal, _json_bytes(record) + b"\n")
        # Every ACK follows both a durable append and an atomic receipt update.
        temporary = self.path / ("receipt-" + secrets.token_hex(16) + ".tmp")
        with self.win.security(receipt_sddl({self.sid})) as sa:
            with self.win.file_handle(temporary, 0x40000000 | 0x20000, create=1, attributes=sa, share=1) as handle:
                verify_receipt_acl(*self.win.acl(handle), {self.sid})
                self.win.write_all(handle, _json_bytes(record))
        self.win.check(self.win.kcall("MoveFileExW", W.BOOL, [W.LPCWSTR, W.LPCWSTR, W.DWORD],
                                     str(temporary), str(self.path / "receipt.json"), 1 | 8), "RECEIPT_IO")
        return record

    def __exit__(self, *_):
        self.stack.close()


def validate_binding(nonce, job_id):
    if (not isinstance(nonce, str) or re.fullmatch(r"[0-9a-f]{64}", nonce) is None
            or not isinstance(job_id, str) or re.fullmatch(r"[0-9a-f]{32}", job_id) is None):
        fail("IPC_BINDING", "Invalid operation or handshake nonce.")


def bind_parent(pipe, launched_handle, parent_handle, nonce, job_id):
    """Hold both supplied process handles through the session; one handshake."""
    validate_binding(nonce, job_id)
    win = pipe.win
    parent, launched = win.identity(parent_handle), win.identity(launched_handle)
    if parent.pid != os.getpid() or parent.elevated:
        fail("PARENT_IDENTITY", "The coordinator must be the ordinary invoking process.")
    pipe.accept()
    if pipe.peer_pid() != launched.pid:
        fail("PEER_IDENTITY", "Client PID differs from the actual launched helper handle.")
    with win.process(pipe.peer_pid()) as connected:
        verify_helper(win.identity(connected), launched, parent)
        expected = {"protocol": PROTOCOL, "kind": "hello", "nonce": nonce, "job_id": job_id}
        if pipe.receive() != expected:
            fail("IPC_BINDING", "Helper handshake does not match this operation.")
        # Recheck live handle identities after receiving to close PID-reuse windows.
        verify_peer(win.identity(connected), launched, elevated=True)
        verify_peer(win.identity(parent_handle), parent, elevated=False)
    return parent


@contextmanager
def bind_helper(pipe, parent_pid, nonce, job_id):
    """Pins real pipe-server PID, creation time, session, SID and package image.

    parent_pid is the expected invocation parent, not a self-reported peer field.
    The server must be that process; SID may differ for over-the-shoulder UAC.
    """
    validate_binding(nonce, job_id)
    win = pipe.win
    with win.process(parent_pid) as parent_handle, win.process(os.getpid()) as own_handle:
        parent, own = win.identity(parent_handle), win.identity(own_handle)
        if pipe.peer_pid() != parent.pid or parent.elevated:
            fail("PARENT_IDENTITY", "Helper is not connected to its ordinary invocation parent.")
        verify_helper(own, own, parent)
        pipe.send({"protocol": PROTOCOL, "kind": "hello", "nonce": nonce, "job_id": job_id})
        yield parent
        verify_peer(win.identity(parent_handle), parent, elevated=False)


class OwnerDisconnected(ElevationError):
    """No compensation or further mutation is allowed on a lost owner channel."""
    def __init__(self):
        super().__init__("OWNER_UNKNOWN", "Owner disconnected; reconcile the original protected receipt.")


class PipeJournal:
    """Durable helper record precedes each parent-controlled mutation/ACK."""
    def __init__(self, store, pipe, digest, nonce):
        self.store, self.pipe, self.digest, self.nonce = store, pipe, digest, nonce
        self.failed, self.owner_lost = False, False

    def append(self, event, **data):
        try:
            record = self.store.append(event, digest=self.digest, job_id=self.store.job_id, **data)
        except Exception:
            self.failed = True
            raise
        message = {"protocol": PROTOCOL, "kind": "event", "nonce": self.nonce,
                   "job_id": self.store.job_id, "digest": self.digest, "record": record}
        command = ("compensate" if data.get("compensation") else "commit") if event in ("type_delete_intent", "type_create_intent") else "commit" if event == "write_intent" else "compensate" if event == "compensate_intent" else "continue"
        try:
            self.pipe.send(message)
            expected = {"protocol": PROTOCOL, "kind": command, "nonce": self.nonce,
                        "job_id": self.store.job_id, "digest": self.digest,
                        "receiptSequence": record["receiptSequence"]}
            received = self.pipe.receive()
            if event == "preflight_verified":
                if received == dict(expected, kind="abort"):
                    raise ElevationError("BATCH_ABORTED", "Owner ended the prepared batch before System writes.")
                expected["kind"] = "commit_batch"
            if received != expected:
                fail("IPC_COMMAND", "Unexpected command; this session accepts no replacement plan.")
        except ElevationError as exc:
            if exc.code == "BATCH_ABORTED":
                raise
            self.failed, self.owner_lost = True, True
            raise OwnerDisconnected() from exc
        except Exception as exc:
            self.failed, self.owner_lost = True, True
            raise OwnerDisconnected() from exc

    def close(self):
        pass  # Store lifetime belongs to the helper, never the ordinary process.


def serve_frozen_request(pipe, operator, nonce, job_id, *, package, environment_factory, store_factory=ReceiptStore):
    """Injectable protocol core; caller must authenticate the native pipe first.

    Caller supplies its live verified package identity, never a message's claim.
    Receipt digest and fixed protected-runtime rule outputs are independently
    checked before the fixed System adapter can be constructed.
    """
    from .system_helper import validate_request, execute_helper_request, _error
    validate_binding(nonce, job_id)
    message = pipe.receive()
    fields = {"protocol", "kind", "nonce", "job_id", "digest", "request", "review", "package_digest"}
    if (not isinstance(message, dict) or set(message) != fields or message["protocol"] != PROTOCOL
            or message["kind"] != "request" or message["nonce"] != nonce or message["job_id"] != job_id):
        fail("IPC_BINDING", "Frozen request is not bound to the handshake.")
    request = validate_request(message["request"], allow_type_change=True)
    digest = hashlib.sha256(_json_bytes(request)).hexdigest()
    if message["digest"] != digest or request["job_id"] != job_id:
        fail("DIGEST_MISMATCH", "Frozen request does not match the parent digest/operation.")
    if message["package_digest"] != package["package_digest"]:
        fail("PACKAGE_HASH", "Parent and helper are running different package manifests.")
    review = validate_review(message["review"], request["plans"], package["rules_sha256"])
    review_digest = hashlib.sha256(_json_bytes(review)).hexdigest()
    with store_factory(operator.sid, job_id) as store, ExitStack() as cleanup:
        store.binding = {"operator_sid": operator.sid, "plan_id": review["plan_id"],
                         "version": review["version"], "plan_digest": review["plan_digest"],
                         "rules_sha256": review["rules_sha256"], "review_digest": review_digest,
                         "package_digest": package["package_digest"]}
        journal = PipeJournal(store, pipe, digest, nonce)
        preparation_error, environment = None, None
        try:
            journal.append("review_bound", review=review, package_digest=package["package_digest"])
            verify_rule_proofs(review, package)  # BEFORE adapter construction or PREPARED.
            environment = environment_factory()
            if callable(getattr(environment, "close", None)):
                cleanup.callback(environment.close)
        except Exception as exc:
            preparation_error = {"job_id": job_id, "restore": request["restore"],
                    "status": "recovery_required" if journal.failed else "rejected",
                    "targets": [dict(plan, status="not_started") for plan in request["plans"]],
                    "errors": [_error(exc)]}
        compensated = request["restore"]
        source_snapshots = None
        while True:
            result = preparation_error or execute_helper_request(request, environment, journal_path=None,
                                            journal_factory=lambda _: journal, approved_type_changes=True,
                                            source_snapshots=source_snapshots)
            result.update(digest=digest, review_digest=review_digest, package_digest=package["package_digest"])
            result["channel_state"] = "owner_unknown" if journal.owner_lost else "connected"
            if journal.owner_lost:
                break
            record = store.append("phase", digest=digest, job_id=job_id, result=result)
            try:
                pipe.send({"protocol": PROTOCOL, "kind": "phase", "nonce": nonce, "job_id": job_id,
                           "digest": digest, "receiptSequence": record["receiptSequence"], "result": result})
                command = pipe.receive()
            except Exception:
                result["status"] = "recovery_required"
                result["channel_state"] = "owner_unknown"
                result["errors"].append(OwnerDisconnected().to_dict())
                journal.owner_lost, journal.failed = True, True
                break
            base = {"protocol": PROTOCOL, "nonce": nonce, "job_id": job_id, "digest": digest,
                    "receiptSequence": record["receiptSequence"]}
            if command == dict(base, kind="finish"):
                break
            if (not compensated and result["status"] == "committed"
                    and command == dict(base, kind="compensate_batch")):
                request = dict(request, restore=True)
                source_snapshots = [target["result"] for target in result["targets"]]
                compensated = True
                continue
            fail("IPC_COMMAND", "Only same-plan conditional compensation or finish is allowed.")
        # No peer ACK is needed to retain an owner-lost recovery record.
        record = store.append("terminal", digest=digest, job_id=job_id, result=result)
        if journal.owner_lost:
            return result
        pipe.send({"protocol": PROTOCOL, "kind": "result", "nonce": nonce,
                   "job_id": job_id, "digest": digest, "receiptSequence": record["receiptSequence"],
                   "result": result})
        return result


def drive_frozen_request(pipe, request, digest, nonce, *, review, package, controller=None, verify_phase=None):
    """No retry, reconnect, replacement request or arbitrary command path."""
    job_id = request["job_id"]
    validate_binding(nonce, job_id)
    if hashlib.sha256(_json_bytes(request)).hexdigest() != digest:
        fail("DIGEST_MISMATCH", "Parent's frozen request bytes changed.")
    review = validate_review(review, request["plans"], package["rules_sha256"])
    pipe.send({"protocol": PROTOCOL, "kind": "request", "nonce": nonce,
               "job_id": job_id, "digest": digest, "request": request,
               "review": review, "package_digest": package["package_digest"]})
    sequence = 0
    while True:
        message = pipe.receive()
        if (not isinstance(message, dict) or message.get("protocol") != PROTOCOL
                or message.get("nonce") != nonce or message.get("job_id") != job_id
                or message.get("digest") != digest):
            fail("IPC_BINDING", "Unbound helper message.")
        if message.get("kind") in ("phase", "result"):
            if set(message) != {"protocol", "kind", "nonce", "job_id", "digest", "receiptSequence", "result"}:
                fail("IPC_COMMAND", "Unsupported helper result fields.")
            if message.get("receiptSequence") != sequence + 1:
                fail("IPC_SEQUENCE", "Terminal receipt sequence is not continuous.")
            sequence += 1
            if message["kind"] == "result":
                return message
            if verify_phase:
                verify_phase(message)
            command = controller.phase(message["result"]) if controller else "finish"
            pipe.send({"protocol": PROTOCOL, "kind": command, "nonce": nonce, "job_id": job_id,
                       "digest": digest, "receiptSequence": sequence})
            continue
        if set(message) != {"protocol", "kind", "nonce", "job_id", "digest", "record"} or message["kind"] != "event":
            fail("IPC_COMMAND", "Unexpected helper protocol message.")
        record = message["record"]
        if (not isinstance(record, dict) or type(record.get("receiptSequence")) is not int
                or record["receiptSequence"] != sequence + 1 or record.get("digest") != digest
                or record.get("job_id") != job_id):
            fail("IPC_SEQUENCE", "Receipt sequence/digest differs from this operation.")
        sequence += 1
        event = record.get("event")
        command = ("compensate" if record.get("compensation") else "commit") if event in ("type_delete_intent", "type_create_intent") else "commit" if event == "write_intent" else "compensate" if event == "compensate_intent" else "continue"
        if command in ("commit", "compensate") and controller and not controller.allow_mutation():
            raise OwnerDisconnected()
        if event == "preflight_verified":
            command = controller.prepared() if controller else "commit_batch"
        pipe.send({"protocol": PROTOCOL, "kind": command, "nonce": nonce, "job_id": job_id,
                   "digest": digest, "receiptSequence": sequence})


def read_protected_receipt(operator_sid, job_id, digest, *, win=None):
    """Read-only original-operation reconciliation, no helper restart or replay.

    Only this deterministic root is read. A copied local DB/job receipt never
    enters this method. Directory handles deny delete sharing throughout read.
    """
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        fail("RECEIPT_INVALID", "An original request digest is required for reconciliation.")
    store = ReceiptStore(operator_sid, job_id, win=win)
    win = store.win
    base = store._program_data()
    with ExitStack() as stack:
        for ancestor in reversed((base, *base.parents)):
            stack.enter_context(win.file_handle(ancestor, directory=True))
        product = base / "EnvManagerV3"
        root = product / "system-receipts"
        operator = root / operator_sid
        job = operator / job_id
        for path, readers in ((product, {AUTHENTICATED}), (root, {AUTHENTICATED}),
                              (operator, {operator_sid}), (job, {operator_sid})):
            handle = stack.enter_context(win.file_handle(path, directory=True))
            verify_receipt_acl(*win.acl(handle), readers)
        with win.file_handle(job / "receipt.json", 0x80000000 | 0x20000) as handle:
            verify_receipt_acl(*win.acl(handle), {operator_sid})
            record = decode(win.read_all(handle))
        if (not isinstance(record, dict) or record.get("job_id") != job_id or record.get("digest") != digest
                or type(record.get("receiptSequence")) is not int or record["receiptSequence"] < 1):
            fail("RECEIPT_INVALID", "Protected receipt operation/sequence is invalid.")
        return record


def read_operation_receipt(operation):
    """Read-only startup evidence for the ORIGINAL pending System operation.

    No capability gate/UAC/helper/Node/Registry calls, path arguments or replay.
    SID comes from the caller's kernel token; request digest is reconstructed
    from the complete original server receipt, not a writable local helper copy.
    Even a trusted terminal committed receipt leaves recovery_required: the
    Executor must reconcile all User/System targets and its own durable state.
    Older receipts without an independent review binding return error evidence.
    """
    from .system_helper import validate_request, _error
    evidence = {"status": "recovery_required", "trusted": False, "job_id": None,
                "digest": None, "operator_sid": None, "receipt": None, "errors": []}
    try:
        if not isinstance(operation, dict):
            fail("INVALID_OPERATION", "A complete original execution record is required.")
        job_id = operation.get("operationId")
        if not isinstance(job_id, str) or re.fullmatch(r"[0-9a-f]{32}", job_id) is None:
            fail("INVALID_OPERATION", "Original operation id is not a product job nonce.")
        evidence["job_id"] = job_id
        receipt = operation.get("receipt")
        if not isinstance(receipt, dict) or not isinstance(receipt.get("targets"), list):
            fail("INVALID_OPERATION", "Original frozen server receipt is missing.")
        if any(operation.get(k) != receipt.get(k) for k in ("planId", "version", "digest")):
            fail("REVIEW_MISMATCH", "Execution identifiers differ from the original plan receipt.")
        targets, operations = [], []
        for target in receipt["targets"]:
            if not isinstance(target, dict) or target.get("scope") not in ("User", "System"):
                fail("INVALID_OPERATION", "Malformed original target.")
            if target["scope"] != "System":
                continue
            before, plan = target.get("beforeSnapshot"), target.get("plan")
            if not isinstance(before, dict) or not isinstance(plan, dict) or not isinstance(plan.get("after"), dict):
                fail("INVALID_OPERATION", "Incomplete original System before/after.")
            after = plan["after"]
            if all(k in before and k in after and before[k] == after[k] for k in ("exists", "raw", "type")):
                continue
            targets.append({"scope": "System", "name": target.get("name"), "before": before, "after": after})
            operations.append(plan.get("operations"))
        if not targets:
            fail("NO_SYSTEM_TARGETS", "Original operation has no changed System target or helper receipt.")
        request = validate_request({"protocol": 1, "job_id": job_id, "restore": False,
                                    "plans": targets}, allow_type_change=True)
        # Verify full original receipt, including User targets and frozen proofs.
        # This placeholder is used only for schema/digest comparison; it grants
        # no review authority and is never passed to system_review/consume_review.
        original = validate_review({"protocol": PROTOCOL, "plan_id": receipt.get("planId"),
                    "version": receipt.get("version"), "plan_digest": receipt.get("digest"),
                    "review_token": "read-only-original-receipt-check",
                    "intent": receipt.get("intent"), "inputs": {"receipt": receipt},
                    "operations": operations, "targets": targets,
                    "rules_sha256": receipt.get("engineHash")}, request["plans"])
        digest = hashlib.sha256(_json_bytes(request)).hexdigest()
        evidence["digest"] = digest
        win = Win32()
        with win.process(os.getpid()) as process:
            identity = win.identity(process)
            sid = identity.sid
        if not sid_valid(sid) or sid in (ADMIN, SYSTEM):
            fail("INVALID_SID", "The current kernel token has no valid operator SID.")
        evidence["operator_sid"] = sid
        record = read_protected_receipt(sid, job_id, digest, win=win)
        if (not isinstance(record, dict) or record.get("job_id") != job_id or record.get("digest") != digest
                or type(record.get("receiptSequence")) is not int or record["receiptSequence"] < 1):
            fail("RECEIPT_INVALID", "Protected receipt does not identify the original operation.")
        binding = record.get("binding")
        fields = {"operator_sid", "plan_id", "version", "plan_digest", "rules_sha256", "review_digest", "package_digest"}
        if not isinstance(binding, dict) or set(binding) != fields:
            fail("RECEIPT_BINDING_MISSING", "Protected receipt has no independent original review binding.")
        expected = {"operator_sid": sid, **{k: original[k] for k in ("plan_id", "version", "plan_digest", "rules_sha256")}}
        if (any(binding.get(k) != value for k, value in expected.items())
                or any(not isinstance(binding.get(k), str) or re.fullmatch(r"[0-9a-f]{64}", binding[k]) is None
                       for k in ("review_digest", "package_digest"))):
            fail("RECEIPT_REVIEW_MISMATCH", "Protected receipt belongs to a different operator/plan/review.")
        if "review" in record:
            review = validate_review(record["review"], request["plans"], original["rules_sha256"])
            if (any(review[k] != original[k] for k in ("plan_id", "version", "plan_digest"))
                    or hashlib.sha256(_json_bytes(review)).hexdigest() != binding["review_digest"]):
                fail("RECEIPT_REVIEW_MISMATCH", "Protected frozen review evidence differs from the original plan.")
        if "result" in record:
            result = record["result"]
            terminal = record.get("event") in ("phase", "terminal")
            metadata = {"digest": digest, "review_digest": binding["review_digest"], "package_digest": binding["package_digest"]}
            if (not isinstance(result, dict) or result.get("job_id") != job_id or type(result.get("restore")) is not bool
                    or any((terminal or key in result) and result.get(key) != value for key, value in metadata.items())):
                fail("RECEIPT_REVIEW_MISMATCH", "Protected result differs from its original review binding.")
            from .privileges import _verified_result
            _verified_result(dict(request, restore=result["restore"]), digest, dict(result, digest=digest))
        # Return a detached JSON copy: importing evidence cannot mutate the
        # operation or turn a local copy into a future permission credential.
        evidence.update(trusted=True, receipt=decode(_json_bytes(record)))
    except Exception as exc:
        error = _error(exc)
        if isinstance(exc, FileNotFoundError) or error.get("details", {}).get("winerror") in (2, 3):
            error = {"code": "RECEIPT_MISSING", "message": "No protected receipt exists for the original operation.", "details": error}
        evidence["errors"].append(error)
    return evidence
