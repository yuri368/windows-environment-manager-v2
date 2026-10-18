"""Run the existing immutable JS engine as the server's authoritative planner."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys


class PlanningError(RuntimeError):
    def __init__(self, message: str, code: str = "planning_error"):
        super().__init__(message)
        self.code = code
        self.message = message


class Planner:
    def __init__(self, assets: Path | str | None = None, node: str | None = None):
        root = Path(__file__).resolve().parent.parent
        self.assets = Path(assets) if assets else (root / "ui-v3" if (root / "ui-v3" / "demo-plan.js").is_file() else root)
        bundled = Path(getattr(sys, "_MEIPASS", self.assets)) / "runtime" / "node.exe"
        self.node = node or (str(bundled) if bundled.is_file() else shutil.which("node"))
        if not self.node:
            raise PlanningError("未找到 Node.js 运行时，请安装 Node.js 或使用完整发布包。", "runtime_missing")
        self.worker = Path(__file__).with_name("engine_worker.cjs")
        digest = hashlib.sha256()
        for name, source in (("demo-plan.js", self.assets / "demo-plan.js"),
                             ("config-format.js", self.assets / "config-format.js"),
                             ("engine_worker.cjs", self.worker)):
            digest.update(name.encode("ascii") + b"\0" + source.read_bytes() + b"\0")
        self.engine_hash = digest.hexdigest()
        handshake = self.call("handshake")
        if handshake != {"protocol": 1, "engineHash": self.engine_hash}:
            raise PlanningError("计划规则版本不匹配，请使用同一发布包。", "runtime_version_mismatch")

    def _run(self, request: dict):
        request = {**request, "expectedEngineHash": self.engine_hash}
        try:
            process = subprocess.run(
                [self.node, str(self.worker), str(self.assets)],
                input=json.dumps(request, ensure_ascii=True).encode("utf-8"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PlanningError("计划运行时无法完成计算。", "runtime_unavailable") from error
        try:
            reply = json.loads(process.stdout.decode("utf-8"))
        except (ValueError, UnicodeError) as error:
            raise PlanningError("计划运行时返回了无效结果。", "runtime_invalid") from error
        if process.returncode or not reply.get("ok"):
            raise PlanningError(reply.get("error", "计划计算失败。"))
        return reply["result"]

    def call(self, method: str, options=None, *, args=None):
        request = {"method": method, "options": options}
        if args is not None:
            request["args"] = args
        return self._run(request)

    def many(self, calls: list[dict]) -> list:
        return self._run({"calls": calls})

    def advance_trusted_removal_state(self, *, baseline, state, plan, snapshot):
        """Fold an already verified write; unknown refreshes must use syncRemovalState.

        The worker checks the exact opaque before revision and value, then uses
        DemoPlan's existing audit/index checks through synthetic revisions 0/1.
        This is also available as call('advanceTrustedRemovalState', options).
        """
        return self.call("advanceTrustedRemovalState", {
            "baseline": baseline, "state": state, "plan": plan, "snapshot": snapshot})
