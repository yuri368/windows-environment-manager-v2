# TOML 保存事务验收（Issue #8 / #9）

日期：2026-10-12。相关问题：[Issue #8](https://github.com/yuri368/windows-environment-manager-v2/issues/8)、[Issue #9](https://github.com/yuri368/windows-environment-manager-v2/issues/9)。

## 已复现的问题

旧实现先重载服务，再写配置文件；写入失败时仅恢复配置、选定输入和执行计划。
在临时 TOML 中删除当前选定的 `App.New` 后，注入 `OSError("disk full")`，磁盘仍为原文件，但服务当前方案从 `New` 跳到 `Old`。新增/重命名方案的故障注入还确认了修订号、检测缓存和撤销/切换草案的丢失；初始化中途失败也会留下部分重载状态。

新增回归先在旧实现运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_config_save_transaction.py
```

结果为退出码 1：`4 failed, 1 passed`。其中新增/重命名两个子测试分别失败；删除当前方案的直接断言为 `Old != New`。

## 原子状态边界

`EnvironmentService.configuration_transaction()` 统一保存和恢复下列运行态：

| 状态 | 用途 |
| --- | --- |
| `config`、`selections` | 当前配置，以及完整选定来源、输入、自定义草稿和选择修订号 |
| `_options`、`_initialization_warnings`、`_option_revision` | 当前浏览方案、初始化提示及方案修订号 |
| `_detection`、`_detection_generation` | 只读检测快照及其代次 |
| `_issued_plans` | 已签发的执行预览 |
| `_removal_drafts`、`_removal_snapshots`、`_switch_drafts` | 撤销/切换预览及其依赖快照 |
| `_configuration_save_error` | 保存结果尚未核实的写操作阻断状态 |

`reload()` 在此边界内构建新选择状态，复制方案与提示字典，并通过替换对象失效缓存；不清空保存前仍被引用的缓存字典。重载任意阶段抛异常时，恢复原对象和修订号。编辑器保存的外层事务覆盖重载及文件写入，因此写入失败也恢复整个运行态。后续新增与配置重载相关的服务状态，应同时纳入此边界。

注册表存储、持久化上次应用值和执行历史不属于此运行态事务；配置保存过程只读取持久化快照，不写入这些对象。

## 三种落盘结果

| 核验结果 | 服务和编辑器行为 |
| --- | --- |
| 写入返回成功，或抛异常后磁盘字节与新草稿完全相同 | 保持新运行态，旧预览缓存失效，编辑器建立新基线并清空已保存的撤销历史。接口异常通过 `save_warning` 明确提示已核实的新磁盘结果。 |
| 写入抛异常，且磁盘字节仍与保存前基线完全相同 | 恢复原配置、当前方案、完整选择状态、修订号、检测缓存和冻结预览；保留 dirty 草稿、撤销/重做历史及原异常。 |
| 写入抛异常后磁盘不可读，或字节既不是基线也不是新草稿 | 恢复完整旧运行态和编辑草稿，抛出明确的 `ConflictError`，设置 `configuration_save_error`。服务阻止新计划、冻结草案确认和 `execute()`，防止使用未经确认的磁盘/内存关系写入变量。 |

保存前基线冲突或编辑锁冲突在重载之前拒绝，不覆盖外部文件，也不改变运行态。原子写入前再次核验 `expected_content`；若外部程序在重载期间修改文件，保留外部文件和原编辑草稿，并要求重新加载。

恢复流程：先检查并读取实际磁盘文件，再以 `service.reload(load_config(path))` 重载。成功重载清除阻断并失效旧预览；重载失败保留阻断。编辑器的 `reload_from_disk()` 只更新编辑器内容，本身不会解除服务阻断。

## 回归覆盖与执行结果

`tests/test_config_save_transaction.py` 使用 `MemoryStore`、临时 TOML 和临时 `StateRepository`，覆盖：

- 删除当前方案后写入失败，完整运行态恢复，原冻结撤销/切换草案仍可校验。
- 新增/重命名方案后写入失败，候选来源及切走后的自定义输入草稿保留。
- 独立服务重载及编辑器保存中的初始化中途失败；文件写入函数不被调用，编辑器撤销/重做可用。
- 正常保存及已完成原子替换后接口返回异常；保留新态，旧缓存失效。
- 原子替换已经完成但落盘核验不可读；旧/新操作均阻断，成功重载后恢复。
- 落盘为第三种内容、恢复重载再次失败、保存前外部修改、编辑器锁拒绝，以及重载期间外部文件修改。
- 每个场景核对没有 `MemoryStore` 写入或通知，没有成功历史及持久化上次应用值变更。

关联测试命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_config_save_transaction.py tests/test_config_editing.py tests/test_detection_service.py tests/test_removal_service.py tests/test_switch_service.py tests/test_batch_switch_service.py tests/test_service.py tests/test_success_selection.py
```

结果：退出码 0，`117 passed, 35 subtests passed in 7.00s`。

此记录为本地故障注入和关联回归证据。完整回归命令为 `python -m unittest discover -s tests -v`；本机保存修复版本已通过全部 562 个用例，后续键盘修复另增加两个回归用例。最终源码的 Ubuntu / Windows CI 结果在 [PR #10 检查记录](https://github.com/yuri368/windows-environment-manager-v2/pull/10/checks) 中确认，交付结果在 PR 描述中汇总。
