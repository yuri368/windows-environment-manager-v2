"""V3 application authority: identities, intentions, frozen plans and persistence."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import threading
import uuid

from .configuration import ConfigRepository, parse_config, serialize_config, validate_config
from .contributions import Contributions
from .database import Database, encode, now
from .environment import FileEnvironment, WindowsEnvironment, canonical_name
from .execution import Executor
from .planning import Planner
from .workspace import Workspace, WorkspaceError, _reconcile_selection


class ServiceError(WorkspaceError):
    def __init__(self, code, message, *, details=None, status=400):
        super().__init__(code, message)
        self.details, self.status = details or {}, status


def fields(body, allowed, required=()):
    if not isinstance(body, dict) or set(body) - set(allowed) or set(required) - set(body):
        raise ServiceError("INVALID_REQUEST", "请求包含未知字段或缺少必要字段。")


def identifier():
    return uuid.uuid4().hex


def value_state(snapshot):
    return {key: snapshot[key] for key in ("exists", "raw", "type")}


class Service:
    def __init__(self, data_dir, *, environment=None, assets=None, planner=None, monitor_environment=False):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.environment = environment or FileEnvironment(self.data_dir / "environment.json")
        self.database = Database(self.data_dir / "state.sqlite3")
        self.lock = self.database.lock
        self.planner = planner or Planner(assets)
        self.contributions = Contributions(self.planner)
        self._event_condition = threading.Condition()
        self._event_epoch, self._event_sequence = identifier(), 0
        self._closing = False
        workspace_id = self.database.get("workspace_id") or identifier()
        self.database.set("workspace_id", workspace_id)
        self.config = ConfigRepository(self.data_dir / "workspaces" / workspace_id / "config.toml")
        self.work = Workspace(self.database, self.config, self.environment, self.planner)
        self.config_draft = self.database.get("configurationDraft")
        self.work.data.setdefault("validRevision", self.work.data["revision"])
        if "fileApps" not in self.work.data:
            self.work.data["fileApps"] = self.work.candidate(self.work.file["apps"]).data["apps"]
            self.work.persist()
        save_intent = self.database.get("config_save_intent")
        if save_intent:
            disk = self.config.load()
            if disk["revision"] == save_intent["oldHash"]:
                self.database.set("config_save_intent", None)
            elif serialize_config(disk["apps"]) == save_intent["newText"]:
                self.work.data.update(fileRevision=disk["revision"], fileConflict=False)
                self.work.data["fileApps"] = deepcopy(self.work.data["apps"])
                self.work.file = disk
                with self.database.transaction() as connection:
                    self.database.set("workspace", self.work.data, connection=connection)
                    self.database.set("config_save_intent", None, connection=connection)
            else:
                self.work.data["fileConflict"] = True
        self.edit_sessions = self.database.get("edit_sessions", {})
        self.order_sessions = self.database.get("order_sessions", {})
        self.executor = Executor(self.environment, self.database,
                                 revalidate=self._revalidate, on_committed=self._committed,
                                 system_session_factory=self._system_session if isinstance(self.environment, WindowsEnvironment) else None,
                                 system_receipt_reader=self._system_receipt if isinstance(self.environment, WindowsEnvironment) else None)
        self.executor.reconcile_pending()
        if monitor_environment and isinstance(self.environment, WindowsEnvironment):
            self.environment.start_changes(on_change=self._environment_changed)

    def _environment_changed(self):
        # Native worker callback: no DB, Registry reads, or Service lock here.
        with self._event_condition:
            self._event_sequence += 1
            self._event_condition.notify_all()

    def events(self, query):
        if set(query) - {"after", "timeout"}:
            raise ServiceError("INVALID_REQUEST", "事件查询字段无效。")
        after = query.get("after", "")
        try:
            timeout = int(query.get("timeout", "20"))
        except (TypeError, ValueError):
            raise ServiceError("INVALID_REQUEST", "事件等待时间无效。") from None
        if not isinstance(after, str) or not 0 <= timeout <= 25:
            raise ServiceError("INVALID_REQUEST", "事件查询参数无效。")
        with self._event_condition:
            cursor = lambda: f"{self._event_epoch}:{self._event_sequence}"
            if after == cursor() and not self._closing:
                self._event_condition.wait_for(lambda: self._closing or after != cursor(), timeout)
            return {"cursor": cursor(), "changed": bool(after and after != cursor()), "closing": self._closing}

    def _system_session(self, receipt, review_token, operation_id):
        from .native_ipc import PROTOCOL, system_review
        from .privileges import prepare_system_batch
        targets = [t for t in receipt["targets"] if t["scope"] == "System"
                   and value_state(t["beforeSnapshot"]) != t["plan"]["after"]]
        plans = [{"scope": t["scope"], "name": t["name"], "before": deepcopy(t["beforeSnapshot"]),
                  "after": deepcopy(t["plan"]["after"])} for t in targets]
        envelope = {"protocol": PROTOCOL, "plan_id": receipt["planId"], "version": receipt["version"],
                    "plan_digest": receipt["digest"], "review_token": review_token,
                    "intent": deepcopy(receipt["intent"]), "inputs": {"receipt": deepcopy(receipt)},
                    "operations": [deepcopy(t["plan"]["operations"]) for t in targets],
                    "targets": plans, "rules_sha256": receipt["engineHash"]}
        # Executor has consumed the DB review before calling this factory.
        # PreparedSystemBatch captures this thread's context for its one launch.
        with system_review(envelope):
            return prepare_system_batch(plans, self.data_dir / "system-jobs", job_id=operation_id)

    @staticmethod
    def _system_receipt(operation):
        from .native_ipc import read_operation_receipt
        return read_operation_receipt(operation)

    def _workspace(self, workspace_id, body=None):
        if workspace_id != self.work.data["id"]:
            raise ServiceError("WORKSPACE_NOT_FOUND", "工作配置不存在。", status=404)
        if body and "expectedRevision" in body and body["expectedRevision"] != self.work.data["revision"]:
            raise ServiceError("WORKSPACE_CHANGED", "另一个页面已修改配置，请保留当前草稿并刷新。", status=409)

    def _valid(self, *, configuration=False):
        errors = (self.config_draft["errors"] if configuration and self.config_draft
                  else validate_config(self.work.data["apps"]))
        if errors:
            raise ServiceError("INVALID_CONFIG", "配置尚未通过校验。", details={"errors": errors})
        if self.work.data.get("fileConflict"):
            raise ServiceError("FILE_CHANGED", "配置文件已在外部变化，请先处理文件冲突。", status=409)

    def bootstrap(self):
        with self.lock:
            snapshots = self.environment.list()["items"]
            # Include absent configured targets so type/existence are never inferred by the UI.
            seen = {(s["scope"], canonical_name(s["name"])) for s in snapshots}
            for _, _, variable in self.work.records():
                target = (variable["scope"], canonical_name(variable["name"]))
                if target not in seen:
                    snapshots.append(self.environment.read(variable["scope"], variable["name"]))
                    seen.add(target)
            self._sync_contributions(snapshots)
            draft = self._configuration_projection()
            return {"workspace": self.workspace(self.work.data["id"]),
                    "apps": draft.projected_apps(), "workingApps": self.work.projected_apps(),
                    "configurationBaseline": deepcopy(self.work.data["fileApps"]),
                    "profileChoices": deepcopy(self.work.data["profileChoices"]),
                    "draftProfileChoices": deepcopy(draft.data["profileChoices"]),
                    "uiDrafts": deepcopy(self.work.data["uiDrafts"]), "snapshots": snapshots,
                    "history": self.history({})["items"], "recovery": self.recovery()["items"],
                    "mode": "windows" if isinstance(self.environment, WindowsEnvironment) else "isolated",
                    "capabilities": {"configuration": True, "execute": True, "undo": True,
                                     "systemElevation": isinstance(self.environment, WindowsEnvironment)},
                    "engineHash": self.planner.engine_hash,
                    "eventCursor": f"{self._event_epoch}:{self._event_sequence}",
                    "monitoring": self.environment.changes_status() if isinstance(self.environment, WindowsEnvironment) else None}

    def workspace(self, workspace_id):
        with self.lock:
            self._workspace(workspace_id)
            return {"id": self.work.data["id"], "revision": self.work.data["revision"],
                    "fileRevision": self.work.data["fileRevision"], "configPath": str(self.config.path),
                    "fileConflict": self.work.data.get("fileConflict", False),
                    "validRevision": self.work.data["validRevision"],
                    "draftRevision": self.work.data["revision"],
                    "errors": deepcopy(self.config_draft["errors"]) if self.config_draft else validate_config(self.work.data["apps"])}

    def _configuration_projection(self):
        if self.config_draft is None:
            return self.work
        draft = self.work.candidate(self.config_draft["data"]["apps"], base=self.config_draft["data"])
        draft.data["selections"].update(deepcopy(self.work.data["selections"]))
        return draft

    def _promote_configuration(self, candidate):
        # IDs in candidate were validated against server-owned working/draft
        # parents. Runtime edits made while a configuration was invalid survive.
        original = self.work.data
        for _, _, variable in candidate.records():
            try:
                old = self.work.record(variable["recordId"])[2]
            except WorkspaceError:
                continue
            state = deepcopy(original["selections"][variable["recordId"]])
            _reconcile_selection(state, old, variable)
            candidate.data["selections"][variable["recordId"]] = state
        for app in candidate.data["apps"]:
            current = original["profileChoices"].get(app["recordId"])
            if current in {p["recordId"] for p in app["profiles"]}:
                candidate.data["profileChoices"][app["recordId"]] = current
        candidate.data.update(revision=original["revision"] + 1, validRevision=original["revision"] + 1,
                              uiDrafts=deepcopy(original["uiDrafts"]), fileRevision=original["fileRevision"],
                              fileApps=deepcopy(original["fileApps"]),
                              fileConflict=original.get("fileConflict", False))
        self.work.data = candidate.data
        self.config_draft = None

    def import_workspace(self, body):
        fields(body, ("text", "filename"), ("text",))
        with self.lock:
            apps = parse_config(body["text"])
            saved = self.config.import_text(body["text"], self.work.data["fileRevision"])
            self.work.update_config(apps)
            self.work.file = saved
            self.work.data.update(fileRevision=saved["revision"], fileConflict=False, uiDrafts={})
            self.work.data["validRevision"] = self.work.data["revision"]
            self.work.data["fileApps"] = deepcopy(self.work.data["apps"])
            self.config_draft = None
            self.database.set("configurationDraft", None)
            self.work.persist()
            return self.bootstrap()

    def save_draft(self, workspace_id, body):
        fields(body, ("apps", "profileChoices", "uiDrafts", "expectedRevision"), ("expectedRevision",))
        with self.lock:
            self._workspace(workspace_id, body)
            if "uiDrafts" in body and not isinstance(body["uiDrafts"], dict):
                raise ServiceError("INVALID_DRAFT", "界面草稿必须是对象。")
            if "profileChoices" in body and not isinstance(body["profileChoices"], dict):
                raise ServiceError("INVALID_DRAFT", "方案选择必须是对象。")
            original, original_draft = deepcopy(self.work.data), deepcopy(self.config_draft)
            try:
                with self.database.transaction():
                    if "apps" in body:
                        # Invalid field values remain drafts; malformed trees cannot replace state.
                        try:
                            base = self.config_draft["data"] if self.config_draft else self.work.data
                            candidate = self.work.candidate(body["apps"], base=base)
                        except (KeyError, TypeError, AttributeError) as error:
                            raise ServiceError("INVALID_CONFIG_SHAPE", "配置草稿的层级结构无效。") from error
                        errors = validate_config(candidate.data["apps"])
                        if errors:
                            self.config_draft = {"data": candidate.data, "errors": errors}
                            self.work.data["revision"] += 1
                        elif self.config_draft or encode(candidate.data["apps"]) != encode(self.work.data["apps"]):
                            self._promote_configuration(candidate)
                    for app_id, profile_id in body.get("profileChoices", {}).items():
                        working_app = next((a for a in self.work.data["apps"] if a["recordId"] == app_id), None)
                        if working_app and any(p["recordId"] == profile_id for p in working_app["profiles"]):
                            self.work.choose_profile(app_id, profile_id)
                        elif self.config_draft:
                            app = next((a for a in self.config_draft["data"]["apps"] if a["recordId"] == app_id), None)
                            if app is None or not any(p["recordId"] == profile_id for p in app["profiles"]):
                                raise WorkspaceError("PROFILE_NOT_FOUND", "方案已变化。")
                            self.config_draft["data"]["profileChoices"][app_id] = profile_id
                        else:
                            raise WorkspaceError("PROFILE_NOT_FOUND", "方案已变化。")
                    if "uiDrafts" in body:
                        self.work.data["uiDrafts"] = deepcopy(body["uiDrafts"])
                        self.work.data["revision"] += 1
                    self.work.persist()
                    self.database.set("configurationDraft", self.config_draft)
            except BaseException:
                self.work.data = original
                self.config_draft = original_draft
                raise
            return self.bootstrap()

    def create_entry(self, workspace_id, body):
        fields(body, ("apps", "expectedRevision"), ("apps", "expectedRevision"))
        errors = validate_config(body["apps"])
        if errors:
            raise ServiceError("INVALID_CONFIG", "新增记录未通过完整配置校验。", details={"errors": errors})
        return self.save_draft(workspace_id, body)

    def restore_workspace(self, workspace_id, body):
        fields(body, ("expectedRevision",))
        with self.lock:
            self._workspace(workspace_id, body)
            saved = self.config.load()
            self.work.update_config(saved["apps"])
            self.work.file = saved
            self.work.data.update(fileRevision=saved["revision"], fileConflict=False, uiDrafts={})
            self.work.data["validRevision"] = self.work.data["revision"]
            self.work.data["fileApps"] = deepcopy(self.work.data["apps"])
            self.config_draft = None
            self.database.set("configurationDraft", None)
            self.work.persist()
            return self.bootstrap()

    def save_workspace(self, workspace_id, body):
        fields(body, ("expectedDiskHash", "expectedRevision"), ("expectedDiskHash", "expectedRevision"))
        with self.lock:
            self._workspace(workspace_id, body)
            self._valid(configuration=True)
            intent = {"oldHash": body["expectedDiskHash"], "newText": serialize_config(self.work.data["apps"]), "at": now()}
            self.database.set("config_save_intent", intent)
            saved = self.config.save(self.work.data["apps"], body["expectedDiskHash"])
            self.work.data["fileRevision"] = saved["revision"]
            self.work.data["fileApps"] = deepcopy(self.work.data["apps"])
            self.work.file = saved
            with self.database.transaction() as connection:
                self.database.set("workspace", self.work.data, connection=connection)
                self.database.set("config_save_intent", None, connection=connection)
            return self.bootstrap()

    def export_workspace(self, workspace_id):
        with self.lock:
            self._workspace(workspace_id)
            self._valid(configuration=True)
            return {"filename": "env.toml", "text": serialize_config(self.work.data["apps"])}

    def update_selection(self, record_id, body):
        fields(body, ("source", "raw", "expectedRevision"), ("source",))
        with self.lock:
            self._workspace(self.work.data["id"], body)
            self.work.select(record_id, source=body["source"], raw=body.get("raw"))
            return self.bootstrap()

    def restore_selection(self, record_id, body):
        fields(body, ("expectedRevision",))
        with self.lock:
            self._workspace(self.work.data["id"], body)
            self.work.record(record_id)
            state = self.work.data["selections"][record_id]
            variable = self.work.record(record_id)[2]
            source = "默认值" if state["baselineValue"] == variable["defaultValue"] else "自定义值"
            state.update(selectedRaw=state["baselineValue"], source=source, custom=state["baselineValue"],
                         matchBaseline=None, removalState=None, revision=state["revision"] + 1)
            self.work.data["revision"] += 1
            self.work.persist()
            return self.bootstrap()

    def environment_state(self, query):
        if set(query) - {"scope"}:
            raise ServiceError("INVALID_REQUEST", "环境查询字段无效。")
        with self.lock:
            return self.environment.list(query.get("scope"))

    def refresh(self, body):
        fields(body, ("knownRevision", "targets"))
        with self.lock:
            for _, _, variable in self.work.records():
                self.work.removal_state(variable, self.environment.read(variable["scope"], variable["name"]))
            if isinstance(self.environment, WindowsEnvironment):
                self.environment.take_events()
            self.work.persist()
            return self.bootstrap()

    def _sync_contributions(self, snapshots):
        stored = self.database.get("historyContributions", [])
        seen = {(s["scope"], canonical_name(s["name"])) for s in snapshots}
        for item in stored:
            target = (item["scope"], canonical_name(item["name"]))
            if item.get("active") and target not in seen:
                snapshots.append(self.environment.read(item["scope"], item["name"]))
                seen.add(target)
        updated = stored
        if isinstance(self.environment, WindowsEnvironment):
            active = self.environment.changes_status()["scopes"]
            gaps = [{**s, "revision": "monitor-unavailable:" + str(s["revision"])}
                    for s in snapshots if not active.get(s["scope"], False)]
            if gaps:
                updated = self.contributions.sync(updated, gaps)
        updated = self.contributions.sync(updated, snapshots)
        if updated != stored:
            self.database.set("historyContributions", updated)
        return updated

    def _special(self, scope, name):
        return any(v.get("special", False) for _, _, v in self.work.records()
                   if v["scope"] == scope and canonical_name(v["name"]) == canonical_name(name))

    def open_edit(self, body):
        fields(body, ("scope", "name", "source", "create"), ("scope", "name"))
        with self.lock:
            snapshot = self.environment.read(body["scope"], body["name"])
            if snapshot.get("supported") is False:
                raise ServiceError("UNSUPPORTED_TYPE", "该注册表类型只能查看，不能修改。")
            if body.get("create") and snapshot["exists"]:
                raise ServiceError("TARGET_EXISTS", "变量已存在，请使用编辑入口。")
            whole = self._special(body["scope"], body["name"])
            values = ([snapshot["raw"]] if whole else snapshot["raw"].split(";")) if snapshot["exists"] else []
            rows = [{"id": identifier(), "value": raw} for raw in values]
            session = {"sessionId": identifier(), "scope": body["scope"], "name": body["name"],
                       "snapshot": snapshot, "rows": rows, "originalRows": deepcopy(rows), "whole": whole,
                       "revision": 0, "deleteVariable": False, "type": snapshot["type"] or "REG_EXPAND_SZ"}
            self.edit_sessions[session["sessionId"]] = session
            self.database.set("edit_sessions", self.edit_sessions)
            return deepcopy(session)

    def _edit(self, session_id):
        if session_id not in self.edit_sessions:
            raise ServiceError("EDIT_NOT_FOUND", "编辑会话不存在。", status=404)
        return self.edit_sessions[session_id]

    def update_edit(self, session_id, body):
        fields(body, ("rows", "expectedRevision", "deleteVariable", "type"), ("expectedRevision",))
        with self.lock:
            session = self._edit(session_id)
            if body["expectedRevision"] != session["revision"]:
                raise ServiceError("EDIT_CHANGED", "编辑会话版本已变化。", status=409)
            existing = {row["id"] for row in session["originalRows"] + session["rows"]}
            rows, ids = [], set()
            for row in body.get("rows", session["rows"]):
                fields(row, ("id", "value"), ("value",))
                row_id = row.get("id") or identifier()
                if row.get("id") and row_id not in existing:
                    raise ServiceError("INVALID_ELEMENT_ID", "元素不属于当前编辑会话。")
                raw = row["value"]
                if row_id in ids or not isinstance(raw, str) or "\0" in raw or any(0xD800 <= ord(c) <= 0xDFFF for c in raw):
                    raise ServiceError("INVALID_ELEMENT", "元素重复或原始值无效。")
                if not session["whole"] and ";" in raw:
                    raise ServiceError("AMBIGUOUS_ELEMENT", "普通元素不能包含分号，请分别添加元素。")
                ids.add(row_id)
                rows.append({"id": row_id, "value": raw})
            if session["whole"] and len(rows) > 1:
                raise ServiceError("SPECIAL_WHOLE", "特殊变量只能整体编辑。")
            raw = ("" if not rows else rows[0]["value"]) if session["whole"] else ";".join(row["value"] for row in rows)
            if len(raw.encode("utf-16-le", "surrogatepass")) // 2 > 32766:
                raise ServiceError("VALUE_TOO_LONG", "值超过 32766 个 UTF-16 单元。")
            kind = body.get("type", session["type"])
            if kind not in ("REG_SZ", "REG_EXPAND_SZ"):
                raise ServiceError("INVALID_TYPE", "类型只能为 REG_SZ 或 REG_EXPAND_SZ。")
            if "deleteVariable" in body and type(body["deleteVariable"]) is not bool:
                raise ServiceError("INVALID_REQUEST", "删除意图必须是布尔值。")
            session.update(rows=rows, type=kind, deleteVariable=body.get("deleteVariable", session["deleteVariable"]),
                           revision=session["revision"] + 1)
            self.database.set("edit_sessions", self.edit_sessions)
            return deepcopy(session)

    def _manual_plan(self, session):
        original = session["snapshot"]
        current = self.environment.read(session["scope"], session["name"])
        if any(current[k] != original[k] for k in ("exists", "raw", "type", "revision")):
            raise ServiceError("STALE_EDIT", "当前值已变化，草稿保留，请对照最新值后重新打开编辑会话。",
                               details={"snapshot": current, "session": deepcopy(session)}, status=409)
        rows = [] if session["deleteVariable"] else session["rows"]
        raw = (rows[0]["value"] if rows else "") if session["whole"] else ";".join(r["value"] for r in rows)
        before_by_id = {r["id"]: (index, r["value"]) for index, r in enumerate(session["originalRows"])}
        after_by_id = {r["id"]: (index, r["value"]) for index, r in enumerate(rows)}
        elements = []
        for row_id in list(before_by_id) + [row_id for row_id in after_by_id if row_id not in before_by_id]:
            before, after = before_by_id.get(row_id), after_by_id.get(row_id)
            item = {"elementId": row_id, "beforeIndex": before[0] if before else None,
                    "afterIndex": after[0] if after else None,
                    "added": before is None, "removed": after is None,
                    "replaced": bool(before and after and before[1] != after[1]),
                    "moved": bool(before and after and before[0] != after[0])}
            if before:
                item["before"] = before[1]
            if after:
                item["after"] = after[1]
            elements.append(item)
        audit = {"after": raw, "whole": session["whole"], "elements": elements,
                 "counts": {key: sum(bool(e[key]) for e in elements) for key in ("added", "removed", "replaced", "moved")}}
        if original["exists"]:
            audit["before"] = original["raw"]
        options = {"target": {"scope": session["scope"], "name": session["name"]},
                   "snapshot": original, "afterRaw": raw, "audit": audit}
        plan = self.planner.call("buildElementEdit", options)
        if session["deleteVariable"]:
            plan["after"] = {"exists": False, "raw": None, "type": None}
            plan.pop("afterRaw", None)
            if original["exists"] and not plan["operations"]:
                plan["operations"].append({"type": "remove", "action": "REMOVE_VARIABLE"})
        if plan["after"]["exists"]:
            plan["after"]["type"] = session["type"]
            if original["exists"] and original["type"] != session["type"]:
                plan["operations"].append({"type": "type-change", "action": "TYPE_CHANGE", "before": original["type"], "after": session["type"]})
        else:
            plan["after"]["type"] = None
        return {"scope": session["scope"], "name": session["name"], "beforeSnapshot": current, "plan": plan, "members": [],
                "verification": {"method": "buildElementEdit", "options": options,
                                 "registryType": session["type"], "deleteVariable": session["deleteVariable"]}}

    def _apply_targets(self, record_ids):
        self._valid()
        if not isinstance(record_ids, list) or not record_ids or len(set(record_ids)) != len(record_ids):
            raise ServiceError("INVALID_RECORDS", "请选择非重复的变量记录。")
        groups = {}
        for record_id in record_ids:
            _, _, variable = self.work.record(record_id)
            key = (variable["scope"], canonical_name(variable["name"]))
            groups.setdefault(key, []).append(variable)
        targets = []
        for (scope, _), variables in groups.items():
            snapshot = self.environment.read(scope, variables[0]["name"])
            if snapshot.get("supported") is False:
                raise ServiceError("UNSUPPORTED_TYPE", "目标为不可覆盖的注册表类型。")
            protected = self.work.protected(scope, variables[0]["name"], record_ids)
            requests = []
            for variable in variables:
                state = self.work.data["selections"][variable["recordId"]]
                removal_state = self.work.removal_state(variable, snapshot)
                if (state["matchBaseline"] is not None and isinstance(self.environment, WindowsEnvironment)
                        and not self.environment.changes_status()["scopes"].get(scope, False)):
                    lost = self.planner.call("syncRemovalState", {
                        "baseline": state["matchBaseline"], "state": removal_state,
                        "snapshot": {**snapshot, "revision": "monitor-unavailable:" + str(snapshot["revision"])}})
                    removal_state = state["removalState"] = self.planner.call("syncRemovalState", {
                        "baseline": state["matchBaseline"], "state": lost, "snapshot": snapshot})
                requests.append({"id": variable["recordId"], "mode": variable["mode"], "whole": variable["special"],
                                 "selectedRaw": state["selectedRaw"], "baseline": state["matchBaseline"],
                                 "removalState": removal_state})
            options = {"target": {"scope": scope, "name": variables[0]["name"]}, "snapshot": snapshot,
                       "protectedValues": protected}
            if len(requests) == 1:
                options.update(requests[0])
                method = "build"
            else:
                options["requests"] = requests
                method = "buildBatch"
            plan = self.planner.call(method, options)
            if plan["after"]["exists"]:
                plan["after"]["type"] = snapshot["type"] or "REG_EXPAND_SZ"
            else:
                plan["after"]["type"] = None
            plan["needsOrdering"] = any(v["mode"] == "append" for v in variables)
            targets.append({"scope": scope, "name": variables[0]["name"], "beforeSnapshot": snapshot,
                            "plan": plan, "members": [v["recordId"] for v in variables],
                            "verification": {"method": method, "options": options,
                                             "registryType": snapshot["type"] or "REG_EXPAND_SZ"}})
        return targets

    def _receipt(self, intent, targets, *, plan_id=None, version=1):
        issues = [issue for target in targets for issue in target["plan"].get("issues", [])]
        blocked = any(not t["plan"].get("valid", False) for t in targets)
        receipt = {"planId": plan_id or identifier(), "version": version,
                   "intent": deepcopy(intent), "targets": targets, "issues": issues,
                   "status": "blocked" if blocked else "ordering_required" if any(t["plan"].get("needsOrdering") for t in targets) else "ready",
                   "dependencies": {"workspaceRevision": self.work.data["revision"]}, "engineHash": self.planner.engine_hash}
        receipt["digest"] = hashlib.sha256(encode(receipt).encode("utf-8")).hexdigest()
        return receipt

    def plan(self, body):
        fields(body, ("kind", "recordIds", "historyIds", "editSessionId", "orderSessionId", "orderSessionIds", "operationId", "resolution"), ("kind",))
        with self.lock:
            if self.database.unfinished() and body["kind"] != "recovery":
                raise ServiceError("RECOVERY_REQUIRED", "有尚未对账的操作，请先查看恢复记录。", status=409)
            receipt = self._build(body)
            self.work.persist()
            self.database.preview(receipt)
            return receipt

    def _build(self, body, *, plan_id=None, version=1):
        if body["kind"] == "apply":
            targets = self._apply_targets(body.get("recordIds"))
            ordering_ids = body.get("orderSessionIds", [body["orderSessionId"]] if body.get("orderSessionId") else [])
            if not isinstance(ordering_ids, list) or len(ordering_ids) != len(set(ordering_ids)):
                raise ServiceError("INVALID_ORDER", "排序会话列表无效。")
            for session_id in ordering_ids:
                self._ordered(targets, session_id)
        elif body["kind"] == "manual":
            targets = [self._manual_plan(self._edit(body.get("editSessionId")))]
        elif body["kind"] == "undo":
            targets = self._undo_targets(body)
        elif body["kind"] == "restore_history":
            targets = self._history_restore_targets(body)
        elif body["kind"] == "recovery":
            targets = self._recovery_targets(body)
        else:
            raise ServiceError("INVALID_INTENT", "未知操作意图。")
        return self._receipt(body, targets, plan_id=plan_id, version=version)

    def _revalidate(self, receipt):
        fresh = self._build(receipt["intent"], plan_id=receipt["planId"], version=receipt["version"])
        # Unrelated workspace changes must not revoke a target's confirmation.
        if encode(fresh["targets"]) == encode(receipt["targets"]) and fresh["engineHash"] == receipt["engineHash"]:
            return None
        return self._build(receipt["intent"], plan_id=receipt["planId"], version=receipt["version"] + 1)

    def review(self, plan_id, body):
        fields(body, ("version", "digest", "renderedRevision"), ("version", "digest"))
        return self.executor.review({"planId": plan_id, "version": body["version"], "digest": body["digest"]})

    def execute(self, plan_id, body):
        fields(body, ("planId", "version", "digest", "reviewToken", "idempotencyKey"),
               ("version", "digest", "reviewToken", "idempotencyKey"))
        if body.get("planId", plan_id) != plan_id:
            raise ServiceError("INVALID_REQUEST", "计划标识不一致。")
        result = self.executor.execute({**body, "planId": plan_id})
        self.work.data = self.database.get("workspace", self.work.data)
        if result.get("status") == "committed" and any(
            issue.get("reason") in ("duplicate", "shared", "continuity")
            for target in result.get("targets", []) for issue in target["plan"].get("issues", [])
        ):
            result["status"] = "applied_partial_intent"
        result["state"] = self.bootstrap()
        return result

    def _committed(self, receipt, result):
        updated = deepcopy(self.work.data)
        original = self.work.data
        self.work.data = updated
        try:
            for target in receipt["targets"]:
                plan = target["plan"]
                verified = next(item for item in result["targets"] if item["scope"] == target["scope"]
                                and canonical_name(item["name"]) == canonical_name(target["name"]))
                snapshot = verified["verifiedAfter"] or verified["before"]
                self.work.advance_known_write(plan, snapshot)
                for record_id in target["members"]:
                    app, profile, _ = self.work.record(record_id)
                    state = updated["selections"][record_id]
                    unresolved = any(issue.get("reason") in ("duplicate", "shared", "continuity")
                                     and issue.get("requestId", record_id) == record_id for issue in plan.get("issues", []))
                    if not unresolved:
                        state.update(savedValue=state["selectedRaw"], baselineValue=state["selectedRaw"],
                                     matchBaseline=None, removalState=None)
                        app["active"] = profile["name"]
            updated["revision"] += 1
        finally:
            self.work.data = original
        # Executor persists these in the same SQLite transaction as its history/commit.
        contributions = self.contributions.advance(
            self.database.get("historyContributions", []), receipt, result, original)
        return {"workspace": updated, "historyContributions": contributions}

    def open_order(self, body):
        fields(body, ("planId", "targetKey"), ("planId",))
        with self.lock:
            row = self.database.read_preview(body["planId"])
            if not row:
                raise ServiceError("PLAN_NOT_FOUND", "排序候选计划不存在。")
            receipt = row["body"]
            candidates = [t for t in receipt["targets"] if t["plan"].get("needsOrdering")]
            if body.get("targetKey"):
                candidates = [t for t in candidates if body["targetKey"] in (t["scope"] + "|" + canonical_name(t["name"]), t["plan"]["targetKey"])]
            if len(candidates) != 1:
                raise ServiceError("ORDER_TARGET_REQUIRED", "请明确一个需要排序的目标。")
            target = candidates[0]
            plan = target["plan"]
            values = plan["after"]["raw"].split(";") if plan["after"]["exists"] else []
            session = {"sessionId": identifier(), "revision": 0, "scope": target["scope"], "name": target["name"],
                       "candidateDigest": self._candidate_digest(target), "confirmed": False,
                       "rows": [{"id": identifier(), "value": v} for v in values]}
            session["originalRows"] = deepcopy(session["rows"])
            self.order_sessions[session["sessionId"]] = session
            self.database.set("order_sessions", self.order_sessions)
            return deepcopy(session)

    def update_order(self, session_id, body):
        fields(body, ("order", "expectedRevision"), ("order", "expectedRevision"))
        with self.lock:
            session = self.order_sessions.get(session_id)
            if session is None or session["revision"] != body["expectedRevision"]:
                raise ServiceError("ORDER_CHANGED", "排序会话已变化。", status=409)
            by_id = {r["id"]: r for r in session["rows"]}
            order = body["order"]
            if not isinstance(order, list) or len(order) != len(by_id) or set(order) != set(by_id):
                raise ServiceError("INVALID_ORDER", "排序必须包含完整且唯一的服务端元素标识。")
            session.update(rows=[by_id[k] for k in order], confirmed=True, revision=session["revision"] + 1)
            self.database.set("order_sessions", self.order_sessions)
            return deepcopy(session)

    @staticmethod
    def _candidate_digest(target):
        return hashlib.sha256(encode({"before": target["beforeSnapshot"], "after": target["plan"]["after"],
                                      "members": target["members"], "issues": target["plan"].get("issues", [])}).encode()).hexdigest()

    def _ordered(self, targets, session_id):
        session = self.order_sessions.get(session_id)
        if not session or not session["confirmed"]:
            raise ServiceError("ORDER_REQUIRED", "请先完成排序审阅。")
        target = next((t for t in targets if t["scope"] == session["scope"] and canonical_name(t["name"]) == canonical_name(session["name"])), None)
        if not target or session["candidateDigest"] != self._candidate_digest(target):
            raise ServiceError("ORDER_STALE", "排序候选已变化，请重新排序。", status=409)
        plan = target["plan"]
        raw = ";".join(row["value"] for row in session["rows"]) if plan["after"]["exists"] else None
        if plan["after"]["raw"] != raw:
            plan["operations"].append({"type": "move", "action": "MOVE", "order": [r["id"] for r in session["rows"]]})
        plan["after"]["raw"] = plan["afterRaw"] = raw
        plan["needsOrdering"] = False
        indices = {row["id"]: index for index, row in enumerate(session["originalRows"])}
        target["verification"]["orderIndices"] = [indices[row["id"]] for row in session["rows"]]

    def _undo_targets(self, intent):
        records = intent.get("recordIds")
        if not isinstance(records, list) or not records or len(set(records)) != len(records):
            raise ServiceError("UNDO_SCOPE_REQUIRED", "请指定非重复的变量记录撤销范围。")
        stored = self._sync_contributions([])
        snapshots = [self.environment.read(item["scope"], item["name"])
                     for item in stored if item.get("active")]
        targets = self.contributions.build_undo(stored, records, snapshots, self.work.protected)
        if not targets:
            raise ServiceError("NO_UNDO", "所选范围没有尚未撤销的历史贡献。")
        return targets

    def _history_restore_targets(self, intent):
        requested = set(intent.get("historyIds", []))
        entries = [h for h in self.database.history() if h["status"] == "committed"]
        entries = [h for h in entries if h["operationId"] in requested]
        if not requested or len(entries) != len(requested):
            raise ServiceError("NO_UNDO", "请选择完整的已验证执行历史。")
        chosen = {}
        for history in entries:
            for target in history["targets"]:
                key = (target["scope"], canonical_name(target["name"]))
                if key in chosen:
                    raise ServiceError("AMBIGUOUS_RESTORE", "同一目标请选择一条历史进行恢复。")
                chosen[key] = target
        targets = []
        for (scope, _), target in chosen.items():
            current = self.environment.read(scope, target["name"])
            expected = target.get("verifiedAfter") or target["before"]
            if any(current.get(k) != expected.get(k) for k in ("exists", "raw", "type", "revision")):
                raise ServiceError("UNDO_CONFLICT", "当前值已变化，不能用旧历史覆盖。", details={"snapshot": current}, status=409)
            before = value_state(target["before"])
            whole = target["plan"].get("special", False)
            for protected in self.work.protected(scope, target["name"], target.get("members", [])):
                if (self.planner.call("matches", args=[current["raw"] or "", [protected], whole])
                        and not (before["exists"] and self.planner.call("matches", args=[before["raw"], [protected], whole]))):
                    raise ServiceError("RESTORE_SHARED", "其他配置仍依赖当前值，请先处理共享依赖。", status=409)
            plan = {"targetKey": {"scope": scope, "name": target["name"], "canonicalName": canonical_name(target["name"])},
                    "before": value_state(current), "after": before, "baseRevision": current["revision"],
                    "beforeRaw": current["raw"], "afterRaw": before["raw"], "mode": "history-restore",
                    "special": target["plan"].get("special", False), "valid": True, "issues": [],
                    "operations": [] if before == value_state(current) else [{"type": "restore", "action": "RESTORE_HISTORY"}]}
            targets.append({"scope": scope, "name": target["name"], "beforeSnapshot": current, "plan": plan, "members": [],
                            "verification": {"method": "restore", "origin": deepcopy(target),
                                             "desiredSnapshot": deepcopy(target["before"]), "requiresExplicitReview": True}})
        return targets

    def operation_by_key(self, request_key):
        item = self.database.find_execution(request_key)
        if item is None:
            return {"status": "not_found", "idempotencyKey": request_key}
        return deepcopy(item["body"].get("result", {"status": item["status"], "operationId": item["id"]}))

    def _recovery_targets(self, intent):
        pending = next((item for item in self.database.unfinished() if item["id"] == intent.get("operationId")), None)
        resolution = intent.get("resolution")
        if pending is None or resolution not in ("restore_before", "accept_current"):
            raise ServiceError("INVALID_RECOVERY", "请选择有效的恢复记录和处理方式。")
        targets = []
        for target in pending["body"]["targets"]:
            current = self.environment.read(target["scope"], target["name"])
            after = value_state(current) if resolution == "accept_current" else value_state(target["before"])
            plan = {"targetKey": {"scope": target["scope"], "name": target["name"], "canonicalName": canonical_name(target["name"])},
                    "before": value_state(current), "after": after, "baseRevision": current["revision"],
                    "beforeRaw": current["raw"], "afterRaw": after["raw"], "mode": "recovery",
                    "special": target["plan"].get("special", False), "valid": True, "issues": [],
                    "operations": [] if after == value_state(current) else [{"type": "restore", "action": "RECOVER_BEFORE"}]}
            targets.append({"scope": target["scope"], "name": target["name"], "beforeSnapshot": current, "plan": plan, "members": [],
                            "verification": {"method": "restore", "origin": deepcopy(target),
                                             "desiredSnapshot": deepcopy(current if resolution == "accept_current" else target["before"]),
                                             "requiresExplicitReview": True}})
        return targets

    def operation(self, operation_id):
        with self.lock:
            row = self.database.connection.execute("SELECT request_key FROM executions WHERE id=?", (operation_id,)).fetchone()
        if row is None:
            raise ServiceError("OPERATION_NOT_FOUND", "操作记录不存在。", status=404)
        return self.operation_by_key(row[0])

    def history(self, query):
        with self.lock:
            entries = []
            for item in self.database.history():
                record = deepcopy(item)
                kind = (item.get("intent") or {}).get("kind")
                action = {"apply": "应用环境方案", "manual": "手动编辑环境", "undo": "撤销配置贡献",
                          "restore_history": "恢复历史前值", "recovery": "处理恢复记录"}.get(kind, "应用环境方案")
                if item["status"] != "committed":
                    action = {"compensated": "操作已补偿", "cancelled": "操作已取消", "resolved_current": "已确认保留当前值"}.get(item["status"], "操作待恢复")
                record.update(id=item["operationId"], action=action, when=item.get("when", ""),
                              detail={"committed": "已执行并核验", "compensated": "已恢复执行前值", "cancelled": "已取消", "resolved_current": "保留当前值"}.get(item["status"], "待恢复或对账"), changes=[])
                for target in item["targets"]:
                    final = target.get("verifiedBefore") or target.get("verifiedAfter")
                    change = {"scope": target["scope"], "name": target["name"],
                              "whole": target["plan"].get("special", False), "resultStatus": target["status"],
                              "beforeSnapshot": target["before"], "afterSnapshot": final,
                              "attemptedPlan": target["plan"]}
                    if target["before"]["exists"]:
                        change["before"] = target["before"]["raw"]
                    if final and final["exists"]:
                        change["after"] = final["raw"]
                    if item["status"] == "committed":
                        change.update(plan=target["plan"], operations=target["plan"]["operations"], **target["plan"].get("audit", {}))
                    record["changes"].append(change)
                entries.append(record)
            return {"items": entries}

    def history_record(self, history_id):
        item = next((h for h in self.history({})["items"] if h["id"] == history_id), None)
        if item is None:
            raise ServiceError("HISTORY_NOT_FOUND", "历史记录不存在。", status=404)
        return item

    def recovery(self):
        return {"items": [{"operationId": item["id"], "status": item["status"], "details": item["body"]}
                          for item in self.database.unfinished()]}

    def quit(self, body):
        fields(body, ())
        with self.lock:
            running = self.database.connection.execute("SELECT id FROM executions WHERE status IN ('prepared','executing')").fetchone()
            if running:
                raise ServiceError("EXECUTION_BUSY", "操作尚在执行，不能退出。", status=409)
        return {"status": "closing"}

    def close(self):
        with self._event_condition:
            self._closing = True
            self._event_condition.notify_all()
        if isinstance(self.environment, WindowsEnvironment):
            self.environment.close()
        self.database.close()
