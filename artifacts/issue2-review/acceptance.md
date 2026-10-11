# Issue #2 本地实现与验收结果

项目：Windows Environment Manager V2。

实现源码提交：[`cb45593`](https://github.com/yuri368/windows-environment-manager-v2/commit/cb4559373127078aaa6a69b40a7a442eb2812af5)。本文记录该实现的开发验收结果；本目录作为后续补充资料上传，不是安装包或 Release。

## 已实现

- 全应用、全方案只读匹配检测：完全匹配、部分匹配、尚未配置；读取与校验异常单独展示。正式启动和手动刷新后台扫描，不覆盖用户运行中草稿。
- 单变量、单方案、应用全部方案、批量 A 当前方案、批量 B 所有方案五种撤销；支持手动配置和部分匹配，不依赖软件写入历史。
- 批量共享保护仅覆盖未勾选应用的所有方案；变量和列表元素精确识别，显示全部相关应用及方案。
- 共享窗口只显示真实冲突项；独立保留/撤销及全部操作不影响普通项；敏感值默认隐藏。最终预览包含全部变更、保留项和跳过项。
- 单应用与勾选批次均按实际匹配识别旧方案，将撤销与应用合成净状态；歧义和部分候选明确选择，每个目标最多写入一次。共享保留改变完整列表时重新确认排序。
- 复用执行前冻结验证、历史恢复和失败回滚；保持 TOML 格式、四种操作模式及默认值/草稿/上次成功值的独立关系。
- 更新 AGENTS.md 与 README.md；原 plan_batch 等接口保留兼容行为。

## 自动化验证

最终完整回归命令（项目目录）：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe -m pytest -q --durations=10
```

结果：**409 passed, 176 subtests passed in 884.80s (0:14:44)**。无失败、无通过新增跳过来规避验收。

合并后的定向核心、Service 与 Issue #2 UI 回归：101 passed, 28 subtests passed。原有批量 UI 回归：14 passed, 2 subtests passed。

`git -c core.whitespace=cr-at-eol diff --check` 通过。界面设计检查无主要问题；仅有两处原有 11px 字号的非阻塞建议，未做无关风格改造。

## 界面证据

- [matching-main.png](matching-main.png)：1190×790 主窗口。
- [matching-narrow.png](matching-narrow.png)：850×560 紧凑窗口。
- [shared-conflicts.png](shared-conflicts.png)：逐元素共享决定窗口。
- [removal-preview.png](removal-preview.png)：完整撤销执行预览。
- [report.json](report.json)：截图流程 0 注册表写入、0 通知、状态 revision 0。文件名相对于本目录，不依赖验收机器的绝对路径。

截图复现命令（项目根目录，使用现有开发环境）：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python.exe artifacts/capture_issue2.py
```

脚本只使用硬编码示例配置、MemoryStore 与临时状态目录，不加载个人 `env.toml`。

## 验证与交付边界

新增逻辑、Service 和 UI 测试使用 MemoryStore；真实 Windows 适配测试仅使用 `HKCU\Software\EnvironmentManagerV2Tests\<随机 ID>` 独立测试键，不写真实 User/System Environment 位置。没有对用户真实环境执行撤销或切换。

未发现本次需求的未解决阻塞项。源码、测试和补充资料通过 Git 提交交付；未重新打包安装程序，未关闭 GitHub Issue，也未创建或发布 Release。个人配置、数据库、构建依赖和安装包不包含在本次补充提交中。
