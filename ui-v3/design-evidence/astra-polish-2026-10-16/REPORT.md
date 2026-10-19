# V3 UI 与交互改进交付报告

日期：2026-10-16（北京时间）。基线：`56dbf6030aa11a33f192d91761e7063803d9c350`，来自最新分支 `v3/ui-surface-continuity`。交付分支：`v3/astra-interaction-polish-20261016`。

## 完成的改进

- **预览先给结论**：区分有实际变更、当前值无需改变、删除保留、整批阻塞及更新待审阅。目标数、实际变更目标数与保留提示分别表达。
- **按目标阅读**：变量名和作用域成为主标题，应用/方案置于次行；同目标合并，用户/系统分开。目标索引支持点击和键盘定位，较多目标可折叠。
- **保留原因可理解**：分别说明重复、共享和原元素连续性不足；显示当前已知位置。上下文错误列出已知字段，批量冲突列出实际成员模式，不臆造元素归属。
- **返回检查保留草稿**：定位到对应应用、方案和变量，保留输入、基线、删除意图和批量勾选；不会自动修复冲突或应用。
- **工作台行动更清楚**：变量名可通过键盘打开对照；新增紧凑计划提示，进入现有单方案/批量预览。检测结果与输入修改状态仍独立。
- **刷新与窄屏收尾**：原文展开按目标保留；跨版本无法确认元素身份时，将焦点放回同目标标题。预览只有内容区滚动，600px 下操作栏可见。
- **反馈准确**：零写入时明确说明值未改变；部分完成继续保留未解决意图。无变化仍允许记录成功选择。

所有摘要和提示只消费原有冻结计划。`demo-plan.js`、`windows-elements.js`、配置序列化与元素审计算法没有修改。V2/Python 没有修改；未合并 main，未发布 Release。

## Astra 的四轮参与

| 轮次 | 任务 | 记录 |
| --- | --- | --- |
| 1 | 基于源码制定范围、视觉方向与验收标准 | [设计方案](01-design-plan.md) |
| 2 | 独立审查部分应用、无操作、阻塞、刷新身份边界 | [交互边界审查](02-interaction-review.md) |
| 3 | 查看实际截图并复查窄屏、长 Path 与产品文案 | [产品与视觉复查](03-product-review.md) |
| 4 | 针对最终源码、测试与截图进行独立复核 | [最终复核](04-final-audit.md) |

前三轮提出的具体修正已落地，包括：重复可能发生在原匹配阶段；不把元素编辑器说成通用解决手段；无操作确认按钮准确表达确认行为；阻塞时仍可见版本更新；已有上下文字段和成员模式用于诊断。

## 实际验证

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 原始基线完整 `npm test` | 31 组 / 583 项通过，退出码 0 | [基线日志](baseline-npm-test.txt) |
| 改进后完整 `npm test` | 32 组 / 606 项通过，退出码 0 | [最终日志](final-npm-test.txt) |
| 新增审阅交互回归 | 23 / 23 通过，已加入 npm test 和原 CI 执行范围 | [测试源码](../verify_plan_review.cjs)、[结果](review-workflows.json) |
| Linux Chromium 134 无头浏览器 | 13 / 13 流程通过，无页面运行错误 | [浏览器结果](browser-report.json)、[复现脚本](browser-acceptance.cjs) |
| 1360×900、600×640 | 实际渲染截图；长 Path 无整窗横溢，页脚与末目标不遮挡 | 下方截图 |

浏览器流程覆盖：真实键盘激活、Tab/Shift+Tab 焦点限制、Escape 返回、目标导航、共享保护部分应用、重复保护无操作确认、相关刷新两步审阅、跨版本焦点回退、无变化成功选择、排序限制、批量冲突、长路径窄屏和返回检查。

测试中的场景数据由脚本在浏览器内存中构造；操作通过实际页面按钮、输入和键盘执行。它们没有读取真实 Windows 环境。Linux 缺中文字体时仅为 QA 注入 Noto Sans SC；产品没有新增字体下载、运行依赖或构建步骤。

### 截图

| 视图 | 改进前 | 改进后 |
| --- | --- | --- |
| 工作台，1360×900 | [基线](before-workbench-desktop.png) | [工作台](workbench-desktop.png) |
| 有变更的方案预览，1360×900 | [基线](before-preview-desktop.png) | [审阅预览](preview-desktop.png) |

- [删除保留与安全应用，宽屏](preview-retained-desktop.png)
- [更新待审阅](preview-updated.png)
- [长 Path 与删除保留，600×640](preview-retained-600.png)
- [600×640 滚动到最后目标](preview-bottom-600.png)
- [600×640 返回工作台检查](workbench-600.png)

### 复现

运行网页：用浏览器打开 `ui-v3/temp.html`，不需要构建。开发回归：在 `ui-v3/` 执行 `npm ci --ignore-scripts` 和 `npm test`。

浏览器补充检查需要自行准备 Playwright/Chromium。执行 `node design-evidence/astra-polish-2026-10-16/browser-acceptance.cjs`；可用 `V3_PLAYWRIGHT_MODULE` 指定 QA 工具模块、`V3_BROWSER_PATH` 指定已安装浏览器，`V3_FONT_CSS` 指定仅用于截图的本地字体样式。默认直接打开本地 HTML，输出到本目录；`V3_EVIDENCE_DIR` 可改为临时输出目录。

## 已知限制

本次只在 Linux 上完成浏览器演示改进与验证。没有 Win11 原生桌面、注册表、UAC、Windows DPI 或 WebView 桥接验证。自定义 append 排序确认、真实撤销/恢复仍未实现，继续明确阻止执行。页面刷新仍会重置会话演示数据。

精确验证摘要见 [validation-summary.json](validation-summary.json)。
