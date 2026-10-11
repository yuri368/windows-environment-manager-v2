from __future__ import annotations

import uuid
import json
from copy import deepcopy
from datetime import datetime, timezone

from .engine import calculate_batch, validate_value
from .models import (ConflictError, ExecutionPlan, OperationResult, PlanChange,
                     RegistryState, ValidationError, match_key)
from .selection import SelectionSession


def _target(state):
    return state.scope, match_key(state.name)


def _effect(change):
    cal = change.calculation
    return {"before": change.before.to_dict(), "after": change.after.to_dict(),
            "mode": change.mode, "selected_value": change.selected_value,
            "record_key": change.record_key, "safe": True,
            "contributions": deepcopy(list(change.contributions)),
            "added": list(cal.added) if cal else [], "moved": list(cal.moved) if cal else [],
            "deduplicated": list(cal.deduplicated) if cal else [],
            "original_items": list(cal.original_items) if cal else [],
            "final_items": list(cal.items) if cal else []}


class EnvironmentService:
    def __init__(self, config, store, state_repository):
        self.config = config
        self.store = store
        self.state = state_repository
        snapshot = self.state.snapshot()
        self.selections = SelectionSession(snapshot["last_values"], self._saved_selections(snapshot))
        self._options = {}
        self._initialization_warnings = {}
        self._initialize_options(snapshot)
        self._issued_plans = {}

    @staticmethod
    def _saved_selections(snapshot):
        return {key: value for saved in snapshot.get("successful_applications", {}).values()
                for key, value in saved["selections"].items()}

    def _initialize_options(self, snapshot):
        successes = snapshot.get("successful_applications", {})
        for app in self.config.apps():
            if app in self._options and self._options[app] in self.config.options(app):
                continue
            saved = successes.get(app)
            # Older V2 states retain the successful scheme in their active marker.
            preferred = saved["option"] if saved else snapshot["active"].get(app, {}).get("option")
            if preferred in self.config.options(app):
                self._options[app] = preferred
            else:
                self._options[app] = self.config.default_option(app)
                if preferred is not None:
                    self._initialization_warnings[app] = "上次成功方案已不存在，已初始化为默认方案"
        self._options = {app: option for app, option in self._options.items() if app in self.config.apps()}
        for app, option in self._options.items():
            for spec in self.config.variables(app, option):
                self.selections.get(spec)

    def selected_option(self, app):
        return self._options[app]

    def select_option(self, app, option):
        if option not in self.config.options(app):
            raise ValidationError(f"{app}：方案不存在：{option}")
        if self._options.get(app) != option:
            self._options[app] = option
            self._initialization_warnings.pop(app, None)
            self._issued_plans.clear()

    def initialization_warning(self, app):
        return self._initialization_warnings.get(app)

    def reload(self, config):
        selections = deepcopy(self.selections)
        selections.reconcile(spec for options in config.applications.values()
                             for specs in options.values() for spec in specs)
        snapshot = self.state.snapshot()
        selections.refresh_last(snapshot["last_values"])
        selections.refresh_success(self._saved_selections(snapshot))
        self.config = config
        self.selections = selections
        self._initialize_options(snapshot)
        self._issued_plans.clear()

    def history(self):
        return self.state.history()

    def pending(self):
        return self.state.pending()

    def _issue(self, plan):
        self._issued_plans[plan.id] = deepcopy(plan)
        return plan

    def _shared_apps(self, active, app, state):
        target = _target(state)
        return [name for name, activation in active.items() if name != app
                and any(_target(RegistryState.from_dict(e["after"])) == target
                        for e in activation["effects"])]

    def _undo(self, app, snapshot, joint_switches=()):
        activation = snapshot["active"].get(app)
        changes, conflicts = [], []
        if not activation:
            return changes, conflicts
        for effect in activation["effects"]:
            before = RegistryState.from_dict(effect["before"])
            expected = RegistryState.from_dict(effect["after"])
            current = self.store.read(expected.scope, expected.name)
            if before.same_content(expected):
                # A scheme that merely claimed an existing value has nothing to remove.
                continue
            if not effect.get("safe", False):
                conflicts.append(f"{expected.scope} / {expected.name}：应用期间曾发生外部变更，不能确定撤销范围")
                continue
            if not current.same_content(expected):
                conflicts.append(f"{expected.scope} / {expected.name}：与最近应用结果不同，保留当前值")
                continue
            shared = [name for name in self._shared_apps(snapshot["active"], app, expected)
                      if name not in joint_switches]
            if shared:
                conflicts.append(f"{expected.scope} / {expected.name}：还由 {', '.join(shared)} 管理，不能安全合并撤销")
                continue
            changes.append(PlanChange(current, before, "undo", effect["selected_value"], effect["record_key"]))
        return changes, conflicts

    def plan(self, app, option, orders=None):
        return self.plan_batch({app: option}, orders)

    def plan_batch(self, app_options, orders=None):
        """Resolve all selected applications into one write per registry target."""
        snapshot = self.state.snapshot()
        self.selections.refresh_last(snapshot["last_values"])
        self.selections.refresh_success(self._saved_selections(snapshot))
        requested = (dict(app_options) if isinstance(app_options, dict) else
                     {app: self.selected_option(app) for app in app_options})
        if not requested:
            raise ValidationError("请至少选择一个应用")
        if set(requested) - set(self.config.apps()):
            raise ValidationError("所选应用已不存在，请重新选择")
        applications = tuple((app, requested[app]) for app in self.config.apps() if app in requested)
        for app, option in applications:
            self.select_option(app, option)
        orders = orders or {}
        switching = {app for app, option in applications
                     if app in snapshot["active"] and snapshot["active"][app]["option"] != option}
        conflicts, warnings = [], []
        actuals, virtual, merged = {}, {}, {}

        def read(scope, name):
            target = scope, match_key(name)
            if target not in actuals:
                actuals[target] = self.store.read(scope, name)
            return actuals[target]

        for app, _ in applications:
            if app not in switching:
                continue
            undone, problems = self._undo(app, snapshot, switching)
            conflicts.extend(problems)
            for change in undone:
                target = _target(change.before)
                actuals.setdefault(target, change.before)
                if target in virtual and not virtual[target].same_content(change.after):
                    conflicts.append(f"{change.before.scope} / {change.before.name}：共享方案的原始状态不同，不能安全共同撤销")
                else:
                    virtual[target] = change.after
                    merged[target] = change
        if self.pending():
            conflicts.append("存在未完成执行，请先在历史记录中检查并恢复")
        values, confirmed, success_selections = [], [], {}
        groups, effects_by_app = {}, {}
        for app, option in applications:
            activation = snapshot["active"].get(app)
            effects_by_app[app] = {} if app in switching or not activation else {
                _target(RegistryState.from_dict(e["after"])): deepcopy(e) for e in activation["effects"]}
            if self.initialization_warning(app):
                warnings.append(f"{app}：{self.initialization_warning(app)}")
            for spec in self.config.variables(app, option):
                selection = self.selections.get(spec)
                validate_value(selection.value, spec.mode)
                groups.setdefault(spec.target, []).append((spec, selection.value))
                values.append((spec.key, selection.value))
                success_selections[spec.key] = self.selections.snapshot(spec)
                if selection.warning:
                    warnings.append(f"{app} / {spec.name}：{selection.warning}")
        batch_id = uuid.uuid4().hex
        for target, records in groups.items():
            spec = records[0][0]
            actual = read(spec.scope, spec.name)
            existing = virtual.get(target, actual)
            self.store.check_permission(spec.scope)
            contributions = tuple({"app": item.app, "option": item.option, "record_key": item.key,
                                   "mode": item.mode, "selected_value": value}
                                  for item, value in records)
            order_key = (spec.key if len(records) == 1 else
                         json.dumps(["batch-target", spec.scope, target[1]], ensure_ascii=False))
            modes = {item.mode for item, _ in records}
            mode = "append" if "append" in modes else next(iter(modes)) if len(modes) == 1 else "batch_append"
            selected = records[0][1] if len(records) == 1 else "\n".join(value for _, value in records)
            try:
                cal = calculate_batch(records, existing, orders.get(order_key))
            except ValidationError as exc:
                conflicts.append(f"{spec.scope} / {spec.name}：{exc}")
                merged[target] = PlanChange(actual, existing, mode, selected, order_key,
                                           contributions=contributions)
                continue
            change = PlanChange(actual, cal.result, mode, selected, order_key, cal, contributions)
            merged[target] = change
            for item, value in records:
                effects = effects_by_app[item.app]
                effect = _effect(PlanChange(existing, cal.result, item.mode, value, item.key,
                                           cal, contributions))
                effect["group_id"] = batch_id
                if target in effects:
                    old_effect = effects[target]
                    old_after = RegistryState.from_dict(old_effect["after"])
                    effect["before"] = old_effect["before"]
                    effect["safe"] = old_effect.get("safe", False) and actual.same_content(old_after)
                    if not effect["safe"]:
                        warnings.append(f"{item.app} / {item.name}：检测到外部变更，本次以实际值计算，后续自动撤销会被阻止")
                    effect["previous_inputs"] = old_effect.get("previous_inputs", []) + [old_effect["selected_value"]]
                effects[target] = effect
            shared = [name for name, activation in snapshot["active"].items() if name not in requested
                      and any(_target(RegistryState.from_dict(e["after"])) == target
                              for e in activation["effects"])]
            if shared:
                warnings.append(f"{spec.scope} / {spec.name}：与 {', '.join(shared)} 共享，请检查本次替换或移动的影响")
                if change.changed and "replace" in modes:
                    conflicts.append(f"{spec.scope} / {spec.name}：替换会影响其他激活应用，请先协调或停用共享方案")
                elif change.changed and any(e["mode"] == "replace"
                     for shared_app in shared for e in snapshot["active"][shared_app]["effects"]
                     if _target(RegistryState.from_dict(e["after"])) == spec.target):
                    conflicts.append(f"{spec.scope} / {spec.name}：其他激活方案要求完整替换值，不能同时追加")
            if spec.scope == "System":
                warnings.append(f"{spec.name}：操作系统级变量，需要管理员权限")
            if cal.result.registry_type == 1 and "%" in (cal.result.value or ""):
                warnings.append(f"{spec.name}：保留 REG_SZ，百分号引用将按原始文本保存")
            warnings.extend(cal.warnings)
            if mode == "append" and not cal.needs_order:
                confirmed.extend((item.key, tuple(cal.items)) for item, _ in records)
        active_after = deepcopy(snapshot["active"])
        for app, option in applications:
            active_after[app] = {"option": option, "effects": list(effects_by_app[app].values())}
        changes = tuple(merged.values())
        for change in changes:
            if change.changed:
                self.store.check_permission(change.before.scope)
        app, option = applications[0]
        return self._issue(ExecutionPlan(batch_id, app, option,
            "switch" if switching else "apply", changes, self.config.digest, snapshot["revision"],
            tuple(values), tuple(confirmed), tuple(dict.fromkeys(warnings)),
            tuple(dict.fromkeys(conflicts)), active_after,
            applications=applications, successful_selections=success_selections))

    def plan_deactivate(self, app):
        snapshot = self.state.snapshot()
        changes, conflicts = self._undo(app, snapshot)
        if self.pending():
            conflicts.append("存在未完成执行，请先恢复")
        active = deepcopy(snapshot["active"])
        activation = active.pop(app, None)
        if not activation:
            conflicts.append("该应用没有管理器记录的激活方案")
        for change in changes:
            self.store.check_permission(change.before.scope)
        return self._issue(ExecutionPlan(uuid.uuid4().hex, app, activation["option"] if activation else "",
             "deactivate", tuple(changes), self.config.digest, snapshot["revision"],
             conflicts=tuple(conflicts), active_after=active))

    def plan_restore(self, history_id):
        op = self.state.operation(history_id)
        snapshot = self.state.snapshot()
        conflicts, changes = [], []
        if op["status"] not in {"success", "no_change", "notification_failed"}:
            conflicts.append("该记录不是尚未恢复的成功执行")
        if op.get("after_revision") != snapshot["revision"]:
            conflicts.append("此记录之后还有已提交操作，不能安全覆盖后续状态")
        if self.pending():
            conflicts.append("存在未完成执行，请先恢复")
        for item in op["changes"]:
            expected, original = RegistryState.from_dict(item["after"]), RegistryState.from_dict(item["before"])
            current = self.store.read(expected.scope, expected.name)
            if not current.same_content(expected):
                conflicts.append(f"{expected.scope} / {expected.name}：已发生外部变更，不能恢复快照")
            changes.append(PlanChange(current, original, "restore", item["selected_value"], item["record_key"]))
            if not current.same_content(original):
                self.store.check_permission(current.scope)
        return self._issue(ExecutionPlan(uuid.uuid4().hex, op["app"], op["option"], "restore",
             tuple(changes), self.config.digest, snapshot["revision"], conflicts=tuple(conflicts),
             active_after=deepcopy(op["snapshot_before"]["active"]), history_id=history_id))

    def plan_recover(self, operation_id):
        op = self.state.operation(operation_id)
        snapshot = self.state.snapshot()
        changes, conflicts = [], []
        if operation_id not in {p["id"] for p in self.pending()}:
            conflicts.append("该记录不再是未完成执行")
        # The failed operation did not commit state; only its attempted registry writes can be undone.
        for index in op.get("attempted", []):
            item = op["changes"][index]
            original, expected = RegistryState.from_dict(item["before"]), RegistryState.from_dict(item["after"])
            current = self.store.read(original.scope, original.name)
            if current.same_content(original):
                changes.append(PlanChange(current, original, "recover", item["selected_value"], item["record_key"]))
            elif current.same_content(expected):
                self.store.check_permission(current.scope)
                changes.append(PlanChange(current, original, "recover", item["selected_value"], item["record_key"]))
            else:
                conflicts.append(f"{current.scope} / {current.name}：既不是原始值也不是本次目标，不能直接覆盖")
        return self._issue(ExecutionPlan(uuid.uuid4().hex, op["app"], op["option"], "recover",
              tuple(changes), self.config.digest, snapshot["revision"], conflicts=tuple(conflicts),
              active_after=deepcopy(snapshot["active"]), history_id=operation_id))

    def _put(self, state):
        if state.exists:
            self.store.write(state)
        else:
            self.store.delete(state.scope, state.name)

    def execute(self, plan):
        issued = self._issued_plans.get(plan.id)
        if issued is None or issued != plan:
            raise ConflictError("执行计划无效或已被修改，请重新预览")
        if not plan.can_execute:
            raise ConflictError("排序尚未确认或存在冲突，不能执行")
        with self.state.execution_lock():
            snapshot = self.state.snapshot()
            if snapshot["revision"] != plan.state_revision or self.config.digest != plan.config_digest:
                raise ConflictError("配置或持久化状态已改变，请重新预览")
            if plan.action != "recover" and self.pending():
                raise ConflictError("存在未完成执行，请先恢复")
            for change in plan.changes:
                actual = self.store.read(change.before.scope, change.before.name)
                if not actual.same_content(change.before):
                    raise ConflictError(f"{actual.scope} / {actual.name} 已改变，请重新预览并确认")
                if change.changed:
                    self.store.check_permission(actual.scope)
            if plan.action in {"apply", "switch"}:
                for app, option in plan.applications or ((plan.app, plan.option),):
                    if self.selected_option(app) != option:
                        raise ConflictError("选定方案已改变，请重新预览")
                    for spec in self.config.variables(app, option):
                        if (dict(plan.selected_values).get(spec.key) != self.selections.get(spec).value
                                or plan.successful_selections.get(spec.key) != self.selections.snapshot(spec)):
                            raise ConflictError("选定来源或输入已改变，请重新预览")
            op = {"id": plan.id, "created_at": datetime.now(timezone.utc).isoformat(),
                  "app": plan.app, "option": plan.option, "action": plan.action,
                  "applications": [list(item) for item in plan.applications],
                  "successful_selections": deepcopy(plan.successful_selections),
                  "status": "prepared", "message": "执行记录已保存，尚未写入",
                  "changes": [_effect(c) for c in plan.changes], "attempted": [], "verified": [],
                  "snapshot_before": snapshot, "warnings": list(plan.warnings)}
            self.state.save_operation(op)  # Failure here prevents every registry write.
            self._issued_plans.pop(plan.id, None)
            mutated = False
            notification_error = None
            try:
                for index, change in enumerate(plan.changes):
                    actual = self.store.read(change.before.scope, change.before.name)
                    if not actual.same_content(change.before):
                        raise ConflictError(f"{actual.scope} / {actual.name} 在执行中改变")
                    if change.changed:
                        op["attempted"].append(index)
                        op["status"] = "writing"
                        self.state.save_operation(op)
                        self._put(change.after)
                        mutated = True
                    if not self.store.read(change.after.scope, change.after.name).same_content(change.after):
                        raise RuntimeError(f"{change.after.scope} / {change.after.name} 写入验证失败")
                    op["verified"].append(index)
                    self.state.save_operation(op)
                for change in plan.changes:
                    if not self.store.read(change.after.scope, change.after.name).same_content(change.after):
                        raise ConflictError(f"{change.after.scope} / {change.after.name} 整体核验失败")
                if mutated:
                    try:
                        self.store.notify()
                    except Exception as exc:
                        notification_error = f"环境通知异常：{type(exc).__name__}"
                target_snapshot = deepcopy(snapshot)
                if plan.action == "restore":
                    target_snapshot = deepcopy(self.state.operation(plan.history_id)["snapshot_before"])
                    # Registry restoration changes activation, not the latest successful preferences.
                    for key in ("last_values", "successful_applications"):
                        target_snapshot[key] = deepcopy(snapshot.get(key, {}))
                else:
                    target_snapshot["active"] = deepcopy(plan.active_after)
                    if plan.action in {"apply", "switch"}:
                        target_snapshot["last_values"].update(dict(plan.selected_values))
                        target_snapshot["orders"].update({key: list(order) for key, order in plan.confirmed_orders})
                        successes = target_snapshot.setdefault("successful_applications", {})
                        for app, option in plan.applications or ((plan.app, plan.option),):
                            successes[app] = {
                                "option": option, "operation_id": plan.id, "applied_at": op["created_at"],
                                "selections": {spec.key: deepcopy(plan.successful_selections[spec.key])
                                               for spec in self.config.variables(app, option)}}
                op["status"] = "notification_failed" if notification_error else ("success" if mutated else "no_change")
                op["message"] = ("已写入并验证，环境通知异常" if notification_error else
                                  "全部变量已验证并提交" if mutated else "无需写入，全部变量验证通过并提交")
                op["notification_error"] = notification_error
                op["failure_stage"] = "state_commit"
                self.state.commit(snapshot["revision"], target_snapshot, op, plan.history_id)
            except Exception as exc:
                if op.get("failure_stage") == "state_commit":
                    # A commit can persist before an exception reaches its caller.
                    # Never roll registry values back while a successful state commit remains.
                    try:
                        durable_snapshot = self.state.snapshot()
                        durable_op = self.state.operation(plan.id)
                        expected_snapshot = deepcopy(target_snapshot)
                        expected_snapshot["revision"] = snapshot["revision"] + 1
                        committed = (durable_snapshot == expected_snapshot and
                                     durable_op.get("after_revision") == expected_snapshot["revision"] and
                                     durable_op["status"] == op["status"])
                        uncommitted = (durable_snapshot == snapshot and
                                       "after_revision" not in durable_op)
                    except Exception:
                        committed = uncommitted = False
                    if committed:
                        self.selections.refresh_last(durable_snapshot["last_values"])
                        self.selections.refresh_success(self._saved_selections(durable_snapshot))
                        return OperationResult(durable_op["status"], "执行与状态均已核实提交，提交接口返回异常",
                            plan.id, sum(c.changed for c in plan.changes),
                            errors=(f"提交返回异常：{type(exc).__name__}；已核实持久化结果",),
                            notification_error=notification_error)
                    if not uncommitted:
                        return OperationResult("state_commit_failed", "注册表已验证，状态提交结果无法核实；请保留状态文件并重新启动检查",
                            plan.id, sum(c.changed for c in plan.changes),
                            errors=(f"状态提交结果不确定：{type(exc).__name__}",),
                            notification_error=notification_error)
                # Attempted includes a write that raised after mutating its target.
                return self._rollback(plan, op, exc, mutated)
            committed_snapshot = self.state.snapshot()
            self.selections.refresh_last(committed_snapshot["last_values"])
            self.selections.refresh_success(self._saved_selections(committed_snapshot))
            return OperationResult(op["status"], op["message"], plan.id,
                                   sum(c.changed for c in plan.changes), notification_error=notification_error)

    def _rollback(self, plan, op, failure, mutated):
        stage = "状态提交失败" if op.get("failure_stage") == "state_commit" else "执行失败"
        reason = str(failure) if isinstance(failure, ConflictError) else type(failure).__name__
        errors = [f"{stage}：{reason}"]
        op["status"] = "rolling_back"
        try:
            self.state.save_operation(op)
        except Exception:
            errors.append("无法更新执行日志")
        incomplete = []
        for index in reversed(op["attempted"]):
            change = plan.changes[index]
            try:
                actual = self.store.read(change.before.scope, change.before.name)
                if actual.same_content(change.before):
                    continue
                if not actual.same_content(change.after):
                    raise ConflictError("当前值与写入结果不同，拒绝覆盖后续修改")
                self._put(change.before)
                mutated = True
                if not self.store.read(change.before.scope, change.before.name).same_content(change.before):
                    raise RuntimeError("回滚验证失败")
            except Exception as exc:
                incomplete.append(f"{change.before.scope} / {change.before.name}：{type(exc).__name__}")
        notification_error = None
        if mutated or op["attempted"]:
            try:
                self.store.notify()
            except Exception as exc:
                notification_error = f"回滚环境通知异常：{type(exc).__name__}"
        status = "rollback_incomplete" if incomplete else "rolled_back"
        op.update(status=status, errors=errors + incomplete,
                  message=f"{stage}，回滚不完整" if incomplete else f"{stage}，已验证恢复原始状态",
                  notification_error=notification_error)
        try:
            self.state.save_operation(op)
        except Exception:
            status = "state_commit_failed"
            errors.append("状态日志提交失败；重新启动后检查未完成执行")
        return OperationResult(status, op["message"], plan.id, errors=tuple(errors + incomplete),
                               notification_error=notification_error)
