# vNext 验收用例映射与执行边界

本文件把 [统一元素规范](core-semantics-vnext.md) 的 T01–T31 映射到可执行自动化测试。表格列出用例位置；测试通过状态以实际运行日志和对应提交的执行报告为准。

## 2026-10-12 实际执行结果

在本机 Windows 与仓库 `.venv` 中运行最终源码，`python -m unittest discover -s tests -v` 的实际结果为 **492 项通过，555.126 秒，0 失败、0 错误、0 跳过**。完整日志为 `artifacts/vnext-full-test-audited.log`，该目录为本地忽略的验收产物。

全量结果包含：

- `test_vnext_acceptance.py` 的 39 项用例：T01–T31 及五种范围实际执行、冻结快照、有效空输入、额外元素重复统计。五范围参数化回归包含 15 个场景，分别验证最终状态、共享默认保留、无关目标不整理、每目标一次写，以及 prepare/finalize 后的外部快照变化拒绝。
- `test_vnext_switch_details.py` 的 6 项用例：旧方案独有目标整理预览、全部 8 种设置组合、特殊值保留、同服务和跨服务设置冻结、精确历史恢复。
- `test_vnext_ui.py` 的 12 项 Qt 用例：独立共享决策、特殊整键提示、部分匹配和空值撤销预览、各动作的 System/关键变量删除提示、完整值编辑器的 CR/U+2028/U+2029 原文保留、设置折叠和持久化。
- `test_matching.py` 验证所有普通模式的空选定值仍记录实际额外元素及重复次数；`test_service.py` 验证真实服务恢复/崩溃恢复计划在删除普通 System 目标前给出删除和管理员权限警告。
- 全部既有配置、选择、四种模式、批量、切换、事务、历史和界面回归；3 项隔离 WindowsStore 测试和 1 项原生 HWND 可见性测试。

配置示例校验通过，结果为 9 个应用、10 个方案、16 条变量记录。`compileall` 通过；按 Windows CRLF 约定执行 Git 差异空白检查通过。

[Issue #2](https://github.com/yuri368/windows-environment-manager-v2/issues/2) 正文已同步为新版规范并重新读取核验，保持开放。[PR #3](https://github.com/yuri368/windows-environment-manager-v2/pull/3) 的原始修复提交 `a0c803c` 和测试提交 `49ba28b` 已分别纳入本地开发分支；本次实现保留并扩展其空追加值回归覆盖。

| 编号 | 自动化测试 | 位置 |
| --- | --- | --- |
| T01 | `test_T01_target_names_case_insensitive_and_scopes_independent` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T02 | `test_T02_values_case_sensitive_and_wrong_case_not_removed` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T03 | `test_T03_matching_ignores_order_and_ascii_edge_whitespace` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T04 | `test_T04_replace_matches_sets_but_applies_selected_order` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T05 | `test_T05_replace_extra_elements_are_unmatched_and_preserved` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T06 | `test_T06_partial_replace_removes_only_existing_configured_element` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T07 | `test_T07_lowercase_elements_have_no_uppercase_matches` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T08 | `test_T08_write_cleanup_removes_duplicates_empty_items_and_edge_whitespace` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T09 | `test_T09_cleanup_preserves_case_distinct_elements` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T10 | `test_T10_last_nonempty_element_removal_deletes_key` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T11 | `test_T11_cleanup_preserves_existing_unique_empty` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T12 | `test_T12_empty_append_input_never_matches_nonempty_or_delimiters` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T13 | `test_T13_empty_append_matches_only_existing_raw_empty` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T14 | `test_T14_special_values_match_and_write_raw_complete_text` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T15 | `test_T15_special_append_configuration_is_rejected` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T16 | `test_T16_mixed_special_definitions_fail_across_all_apps_options` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T17 | `test_T17_special_consistency_is_scoped` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T18 | `test_T18_special_shared_complete_value_defaults_keep_and_explicit_remove` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T19 | `test_T19_shared_element_survives_cleanup_of_invalid_copies` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T20 | `test_T20_conflict_ui_groups_target_and_keeps_independent_element_choices` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T21 | `test_T21_readonly_detection_changes_no_config_selection_preferences_or_registry` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T22 | `test_T22_failed_transaction_restores_raw_order_duplicates_empty_items_type_and_absence` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T23 | `test_T23_history_restore_bypasses_default_cleanup` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T24 | `test_T24_cleanup_settings_never_change_matching_results` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T25 | `test_T25_user_system_deletions_have_preview_risks_and_enforce_permission` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T26 | `test_T26_active_revocation_of_exact_unique_empty_deletes_key` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T27 | `test_T27_disabled_cleanup_preserves_exact_unmatched_raw_slices` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T28 | `test_T28_disabled_cleanup_still_deletes_no_effective_remaining_elements` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T29 | `test_T29_partial_ordinary_replace_protects_present_shared_element` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T30 | `test_T30_empty_state_shared_dependency_protects_delete_and_nonempty_switch_apply` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |
| T31 | `test_T31_batch_replace_equal_sets_different_write_order_is_conflict` | [test_vnext_acceptance.py](../tests/test_vnext_acceptance.py) |

## 补充回归

- `tests/test_vnext_acceptance.py` 另覆盖五种撤销范围、所有保护方案、切换单目标单次写入与冻结快照变化、有效空输入及额外元素重复统计。
- `tests/test_vnext_ui.py` 覆盖同目标分组与独立决定、特殊整键选项、普通 replace 部分匹配和空值详情、特殊编辑原文与失败草稿保留、默认折叠设置及持久化零注册表写入。
- `tests/test_config_document.py` 覆盖特殊默认值/候选值原样往返、创建/复制/修改后的全配置一致性检查及失败原文不变。
- 现有 engine、matching、removal、sharing、switching、service、selection、配置编辑器和批量 UI 回归继续覆盖模式排序、双作用域、成功值独立性、快照冲突、事务失败、历史恢复与真实 Qt 交互。

## 运行和人工验证

使用仓库 `.venv/Scripts/python.exe -m unittest discover -s tests -v` 运行全套测试。核心业务用例使用临时配置、临时 SQLite 状态和 MemoryStore，不修改宿主 Windows 环境变量。

实际 Qt 截图在模拟状态下验证普通/特殊共享分组、折叠/展开高级设置、最终整理预览和窄窗口特殊变量编辑。截图仅证明布局和显示，不能证明真实注册表写入、UAC 或发布安装包行为。

`tests/test_windows_store.py` 在真实 Windows 注册表中使用可删除的 `HKCU/Software/EnvironmentManagerV2Tests/<uuid>/User` 与 `/System` 测试路径，验证原始字符串、存在性、类型、删除和作用域隔离。这里的 System 是测试子路径，不是实际 HKLM 系统环境变量。通知广播接口使用 mock。

`tests/test_launch_visibility.py` 在独立进程中使用 Windows Qt 平台和测试窗口的 HWND，验证原生 `IsWindowVisible` 能区分显示与隐藏；该测试不触发 UAC，也不代表安装版启动已经验收。

本轮还通过 Windows Qt 原生平台实际启动主窗口，并由程序内 `IsWindowVisible` 检查后保存 `artifacts/vnext-ui/native-main.png`；特殊值冲突窗口的原生可见性、单目标组、独立决策和默认 KEEP 已检查并保存 `native-special-conflict.png`。截图经视觉检查，内容可读，特殊完整值决策行完整可见。原生窗口仍使用模拟环境数据。

实际权限检查为当前进程无管理员权限，`WindowsStore.check_permission("User")` 成功，`check_permission("System")` 返回没有写入权限且需管理员启动；检查仅请求打开权限，不修改注册表。结果保存在 `artifacts/vnext-permission-check.log`。

本轮没有改写实际 User/System 环境变量。当前非管理员令牌无法执行真实 HKLM 写入；UAC 需要人工同意，未作为无人值守验收执行。实际环境变更通知和新进程读取、提权后的主窗口、安装目录运行与新版安装包仍需人工验收。此次交付为源码升级，没有构建或安装新版发布包；这些发布步骤不属于本次核心语义升级的开发交付。

## 规范条款完成审计

模块依赖保持为：配置/模型定义记录，elements/engine 纯计算，matching/detection 只读计算，removal/sharing/switching 合成目标，service 协调冻结和事务，store/persistence 执行并持久化，UI/配置编辑器展示结果和收集决策。

| 规范范围 | 当前代码证据 | 自动化与运行证据 |
| --- | --- | --- |
| §1–3：结构兼容、名称/作用域、special 类型/模式/全配置一致性、字符串合法性 | `VariableSpec` 可选字段；`config._variable()`、`parse_config()` 全记录 target 校验；`engine.validate_value()`；`selection` 使用原始值 | T01/T15–T17，domain strict bool/双作用域/结构/长度测试，special 默认/候选/成功值原文测试 |
| §4/§9/§11：统一元素解析、三项设置、顺序优先、空值、关闭整理原始切片 | `elements` ASCII-only 归一化及独立开关；`engine.calculate_batch()`；`removal.compose_removal()`；`switching._compose()` | T08–T13/T24/T26–T28，8 种设置组合、前置/后置/手动排序、重复应用、旧方案独有目标和无关目标不整理测试 |
| §5/§6：四模式检测、普通 replace 集合/部分、特殊原文、M/N、错误与重复计数 | `matching.match_variable()` / `match_scheme()`；`detection.scan_all()` / `refresh_targets()` | T02–T07/T14，空输入额外重复计数，原始引用/大小写/错误/每目标一次读取，特殊 Unicode 完整值入口不改原文/来源/revision 测试 |
| §7：五范围、来源合并、其他应用全部方案保护、普通部分匹配/空状态/特殊整值共享 | `removal.expand_sources()` / `build_removal_atoms()`；`sharing.protected_specs()` / `classify_conflicts()`；`switching._net_conflicts()` | T18–T20/T29/T30，五范围实际执行及快照变化的 15 个场景，默认保留和独立强制撤销测试 |
| §7.4/§9：按目标分组、逐项决定、默认保留、bulk 仅共享、正常项最终预览、advanced 折叠 | `SharedConflictDialog`、`PreviewDialog`、`CleanupSettingsDialog`、`VariableCard` | T20/T25，普通/特殊/空值真实按钮和预览回归，设置持久化零注册表写入，原生及模拟截图视觉验收 |
| §8：切换净状态、批量歧义、每目标一次写、冻结/重新比较、成功值独立性 | `switching._compose()`；`engine.calculate_batch()`；`service._validate_frozen_inputs()` / `execute()`；`selection` | T21/T31，切换单次写、批量排序、配置/选择/来源/设置/Windows 快照变化拒绝、成功偏好仅成功后提交测试 |
| §4.3/§8：历史恢复/失败回滚原始存在性、文本、类型，不整理；所有动作删除风险和权限 | `plan_restore()` / `plan_recover()` / `_rollback()` 直接恢复快照；`_issue()` 通用风险；`PreviewDialog` 通用删键分支 | T22/T23/T25，写后异常/崩溃/提交失败/外部变化保护、System 恢复/崩溃恢复删键警告，隔离真实注册表和实际权限拒绝检查 |
| §12：编辑后全配置校验、文档/示例统一、31 场景、旧功能回归、平台边界如实报告 | `ConfigurationDocument._commit()` 原子验证；配置编辑器特殊属性；当前 AGENTS/README/示例/统一规范；Issue #2 | 创建/复制/修改 special 一致性和失败草稿不变、精确 TOML 往返、全量 492 项通过；上文矩阵、文件清单及平台限制 |

## 本轮修改文件

本轮独立开发分支为 `feat/core-semantics-vnext-20261012`。PR #3 的空追加值修复和回归测试已先行纳入分支；新版普通元素、特殊值和空值规则在此基础上统一实施。

以下 48 个文件包含本轮源码、测试、示例与文档变更：

```text
AGENTS.md
README.md
docs/core-semantics-vnext.md
docs/issue-2.md
docs/toml-editor-architecture.md
docs/toml-editor-verification.md
docs/ui-design.md
docs/vnext-verification.md
env.example.toml
envmanager/config.py
envmanager/config_document.py
envmanager/config_editor_ui.py
envmanager/elements.py
envmanager/engine.py
envmanager/matching.py
envmanager/models.py
envmanager/persistence.py
envmanager/removal.py
envmanager/selection.py
envmanager/service.py
envmanager/sharing.py
envmanager/switching.py
envmanager/ui.py
tests/test_batch_engine.py
tests/test_batch_service.py
tests/test_batch_service_review.py
tests/test_batch_switch_service.py
tests/test_batch_switching.py
tests/test_batch_ui.py
tests/test_config_document.py
tests/test_config_editor_ui.py
tests/test_detection.py
tests/test_domain.py
tests/test_issue2_ui.py
tests/test_main_status_options.py
tests/test_matching.py
tests/test_order_anomalies_ui.py
tests/test_removal.py
tests/test_removal_service.py
tests/test_service.py
tests/test_sharing.py
tests/test_success_selection.py
tests/test_switch_service.py
tests/test_switching.py
tests/test_ui.py
tests/test_vnext_acceptance.py
tests/test_vnext_switch_details.py
tests/test_vnext_ui.py
```
