"""Persistent configuration identities and server-owned selection/edit sessions."""
from __future__ import annotations

from copy import copy, deepcopy
import uuid

from .database import encode
from .environment import canonical_name


class WorkspaceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


CONFIG_FIELDS = ("name", "scope", "mode", "defaultValue", "editable", "special", "choices")


def _existing(items, incoming, semantic_key, used):
    """Explicit IDs belong to this parent; ID-less imports use semantic paths."""
    record_id = incoming.get("recordId")
    if record_id is not None:
        old = next((item for item in items if item["recordId"] == record_id), None)
        if old is None or record_id in used:
            raise WorkspaceError("INVALID_RECORD_ID", "记录标识不属于当前父级或已重复使用。")
    else:
        old = next((item for item in items if semantic_key(item) == semantic_key(incoming)
                    and item["recordId"] not in used), None)
    if old:
        used.add(old["recordId"])
    return old or {}


def _reconcile_selection(selection, old, variable):
    before = deepcopy(selection)
    context_changed = any(old.get(field) != variable.get(field)
                          for field in ("scope", "mode", "special")) or (
        canonical_name(old["name"]) != canonical_name(variable["name"]))
    source = selection["source"]
    if source == "默认值":
        selection["selectedRaw"] = variable["defaultValue"]
    elif source.startswith("候选值 "):
        try:
            index = int(source[4:]) - 1
            choice = old["choices"][index] if index >= 0 else None
        except (ValueError, IndexError, KeyError):
            choice = None
        # A moved candidate retains its exact label/value identity. A removed
        # candidate must not silently adopt whatever now occupies its index.
        index = next((i for i, candidate in enumerate(variable["choices"])
                      if choice is not None and candidate == choice), None)
        if index is None:
            selection.update(source="默认值", selectedRaw=variable["defaultValue"])
        else:
            selection.update(source=f"候选值 {index + 1}", selectedRaw=variable["choices"][index]["value"])
    if context_changed:
        # Preserve visible input across a target/context edit, without carrying
        # deletion authority into the new context.
        if selection["selectedRaw"] != before["selectedRaw"]:
            selection.update(source="自定义值", selectedRaw=before["selectedRaw"], custom=before["selectedRaw"])
    if context_changed or any(selection[key] != before[key] for key in ("source", "selectedRaw")):
        selection["matchBaseline"] = selection["removalState"] = None
    if selection != before:
        selection["revision"] += 1


class Workspace:
    def __init__(self, database, config, environment, planner):
        self.db, self.config, self.environment, self.planner = database, config, environment, planner
        self.file = config.load()
        self.data = database.get("workspace")
        if self.data is None:
            self.data = {"id": str(uuid.uuid4()), "revision": 0, "apps": [], "selections": {},
                         "profileChoices": {}, "uiDrafts": {}, "fileRevision": self.file["revision"]}
            self.update_config(self.file["apps"])
            # An empty first configuration is still a durable workspace identity.
            self.persist()
        elif self.data["fileRevision"] != self.file["revision"]:
            # External changes do not destroy an unsaved draft.
            self.data["fileConflict"] = True

    def persist(self):
        self.db.set("workspace", self.data)

    def records(self):
        for app in self.data["apps"]:
            for profile in app["profiles"]:
                for variable in profile["vars"]:
                    yield app, profile, variable

    def record(self, record_id):
        for owner in self.records():
            if owner[2]["recordId"] == record_id:
                return owner
        raise WorkspaceError("RECORD_NOT_FOUND", "配置记录已变化，请刷新后重新预览。")

    def candidate(self, incoming, *, base=None):
        candidate = copy(self)
        candidate.data = deepcopy(self.data if base is None else base)
        candidate.update_config(incoming, persist=False)
        return candidate

    def update_config(self, incoming, *, persist=True):
        if not isinstance(incoming, list):
            raise WorkspaceError("INVALID_CONFIG", "配置必须是应用列表。")
        previous = self.data["apps"]
        # Validate/reconcile on copies: a foreign ID later in the submitted tree
        # cannot leave earlier selection changes in memory or on disk.
        selections = deepcopy(self.data["selections"])
        profile_choices = deepcopy(self.data["profileChoices"])
        used = set()
        apps = []
        for item in incoming:
            old_app = _existing(previous, item, lambda a: a["name"], used)
            app_id = old_app.get("recordId", str(uuid.uuid4()))
            app = {"name": item["name"], "default": item["default"], "recordId": app_id, "profiles": [],
                   "icon": item["name"][:2], "iconBg": "#edf3ef", "iconFg": "#286c51", "active": old_app.get("active", "")}
            for source in item["profiles"]:
                old_profile = _existing(old_app.get("profiles", []), source, lambda p: p["name"], used)
                profile = {"name": source["name"], "recordId": old_profile.get("recordId", str(uuid.uuid4())), "vars": []}
                for data in source["vars"]:
                    variable = {"scope": "User", "mode": "replace", "defaultValue": "",
                                "editable": True, "special": False, "choices": []}
                    variable.update({field: deepcopy(data[field]) for field in CONFIG_FIELDS if field in data})
                    old = _existing(old_profile.get("vars", []), {**variable, **({"recordId": data["recordId"]}
                                    if "recordId" in data else {})},
                                    lambda v: (v["scope"], canonical_name(v["name"])), used)
                    record_id = old.get("recordId", str(uuid.uuid4()))
                    variable.update(recordId=record_id, editable=variable.get("editable", True),
                                    special=variable.get("special", False), choices=variable.get("choices", []))
                    profile["vars"].append(variable)
                    selection = selections.get(record_id)
                    if selection is None:
                        selections[record_id] = {
                            "selectedRaw": variable["defaultValue"], "source": "默认值", "custom": None,
                            "savedValue": None, "baselineValue": variable["defaultValue"],
                            "matchBaseline": None, "removalState": None, "revision": 0}
                    else:
                        _reconcile_selection(selection, old, variable)
                app["profiles"].append(profile)
            apps.append(app)
            if profile_choices.get(app_id) not in [p["recordId"] for p in app["profiles"]]:
                default = next((p for p in app["profiles"] if p["name"] == app["default"]), None)
                if default:
                    profile_choices[app_id] = default["recordId"]
        # Runtime state may outlive a temporary invalid configuration draft, but never creates a record.
        if encode(apps) != encode(previous):
            self.data["apps"] = apps
            self.data["selections"] = selections
            self.data["profileChoices"] = profile_choices
            self.data["revision"] += 1
            if persist:
                self.persist()

    def select(self, record_id, *, source, raw=None):
        _, _, variable = self.record(record_id)
        selection = self.data["selections"][record_id]
        if source == "默认值":
            desired = variable["defaultValue"]
        elif source == "已保存值" and selection["savedValue"] is not None:
            desired = selection["savedValue"]
        elif source == "自定义值":
            if variable.get("editable", True) is False:
                raise WorkspaceError("READONLY_SELECTION", "此变量不允许运行时自由输入。")
            desired = raw
        elif isinstance(source, str) and source.startswith("候选值 "):
            try:
                desired = variable["choices"][int(source[4:]) - 1]["value"]
                if int(source[4:]) < 1:
                    raise ValueError()
            except (ValueError, IndexError, TypeError, KeyError) as error:
                raise WorkspaceError("CHOICE_CHANGED", "候选值已变化，请重新选择。") from error
        else:
            raise WorkspaceError("INVALID_SOURCE", "选定值来源无效。")
        if not isinstance(desired, str) or "\0" in desired or len(desired.encode("utf-16-le", "surrogatepass")) // 2 > 32766:
            raise WorkspaceError("INVALID_SELECTION", "选定值必须是不含 NUL、长度有效的原始字符串。")
        if desired == selection["selectedRaw"] and source == selection["source"]:
            return
        if (desired != selection["selectedRaw"] and not selection["matchBaseline"]
                and not variable["special"] and variable["mode"] in ("+append", "-append", "append")):
            snapshot = self.environment.read(variable["scope"], variable["name"])
            selection["matchBaseline"] = self.planner.call("captureBaseline", {
                "target": {"scope": variable["scope"], "name": variable["name"]},
                "snapshot": snapshot, "mode": variable["mode"], "whole": variable["special"],
                "selectedRaw": selection["selectedRaw"], "owner": {"recordId": record_id}})
            selection["removalState"] = self.planner.call("createRemovalState", args=[selection["matchBaseline"]])
        selection.update(selectedRaw=desired, source=source, custom=desired if source == "自定义值" else selection["custom"],
                         revision=selection["revision"] + 1)
        self.data["revision"] += 1
        self.persist()

    def choose_profile(self, app_id, profile_id):
        app = next((a for a in self.data["apps"] if a["recordId"] == app_id), None)
        if app is None or not any(p["recordId"] == profile_id for p in app["profiles"]):
            raise WorkspaceError("PROFILE_NOT_FOUND", "方案已变化。")
        if self.data["profileChoices"].get(app_id) != profile_id:
            self.data["profileChoices"][app_id] = profile_id
            self.data["revision"] += 1
            self.persist()

    def projected_apps(self):
        apps = deepcopy(self.data["apps"])
        for app in apps:
            for profile in app["profiles"]:
                for index, variable in enumerate(profile["vars"]):
                    state = self.data["selections"][variable["recordId"]]
                    variable.update(deepcopy(state), id=index, dirty=state["selectedRaw"] != state["baselineValue"])
        return apps

    def protected(self, scope, name, excluded, snapshot=None):
        """Actually matched declarations/active contributions outside this batch.

        historyContributions is a settings list of {contributionId, scope, name,
        recordIds, withdrawnRecordIds, selectedRaw, baseline, removalState, active,
        ...}. Only remaining owners (recordIds minus withdrawnRecordIds) protect
        a contribution; all remaining owners must be excluded to release it.
        Matching/normalization is exclusively delegated to the shared JS engine.
        """
        target = canonical_name(name)
        snapshot = self.environment.read(scope, name) if snapshot is None else snapshot
        if not snapshot["exists"]:
            return []
        excluded = set(excluded)
        candidates = []
        for _, _, variable in self.records():
            if (variable["recordId"] not in excluded and variable["scope"] == scope
                    and canonical_name(variable["name"]) == target):
                candidates.append((self.data["selections"][variable["recordId"]]["selectedRaw"], variable["special"]))
        for contribution in self.db.get("historyContributions", []):
            if (contribution["active"] and contribution["scope"] == scope
                    and canonical_name(contribution["name"]) == target):
                owners = set(contribution["recordIds"]) - set(contribution.get("withdrawnRecordIds", []))
                if owners.issubset(excluded):
                    continue
                candidates.append((contribution["selectedRaw"], bool((contribution.get("baseline") or {}).get("special", False))))
        calls, values = [], []
        for selected, whole in candidates:
            for raw in ([snapshot["raw"]] if whole else snapshot["raw"].split(";")):
                calls.append({"method": "matches", "args": [raw, [selected], whole]})
                values.append(raw)
        result = []
        for raw, matched in zip(values, self.planner.many(calls) if calls else []):
            if matched and raw not in result:
                result.append(raw)
        return result

    def removal_state(self, variable, snapshot):
        selection = self.data["selections"][variable["recordId"]]
        if selection["matchBaseline"] is not None:
            selection["removalState"] = self.planner.call("syncRemovalState", {
                "baseline": selection["matchBaseline"], "state": selection["removalState"], "snapshot": snapshot})
        return selection["removalState"]

    def advance_known_write(self, plan, snapshot):
        target = plan["targetKey"]
        for _, _, variable in self.records():
            state = self.data["selections"][variable["recordId"]]
            if (state["matchBaseline"] is not None and variable["scope"] == target["scope"]
                    and canonical_name(variable["name"]) == canonical_name(target["name"])):
                # An inactive session may have missed an unknown transition
                # before this verified write. Revoke that gap through normal
                # synchronization, then fold only the actual known transition.
                prior = self.planner.call("syncRemovalState", {
                    "baseline": state["matchBaseline"], "state": state["removalState"],
                    "snapshot": {**plan["before"], "revision": plan["baseRevision"]}})
                state["removalState"] = self.planner.call("advanceTrustedRemovalState", {
                    "baseline": state["matchBaseline"], "state": prior, "plan": plan, "snapshot": snapshot})
