# 第一阶段接手：独立代码与回归审查

审查基线：`5314fd7..573dcfad29f1748d012ac903450c4c49aabcdea1`，分支 `v3/phase1-ui-recovery-20261017`。本记录只针对四个展示文件的 Claude 现场；产品源码未由此审查改动。

## 静态结论

- `temp.html` 的变更集中在工作台和 Windows 的标记、概览与变量行文案、审阅摘要位置、textarea 高度以及 Windows 表头宽度。没有修改 `demo-plan.js`、Windows 快照来源、编辑器提交和历史模型。
- `inspectReviewMarkup` 仍使用 `reviewState(plan)`；changed 只删去与“有实际变更”重复的副文案。retained、blocked、unchanged 的说明仍存在，入口仍调用原 `#previewButton`，批量请求范围由原 `checked` 状态决定。
- Claude 现场的 `#inspectReview` 移入 `.inspecthead` 后，通用 `.inspecthead strong::before` 会给 blocked/retained 状态添绿色菱形。最终产品已将选择器收窄为 `.inspecthead > strong`，状态标签不再误中标题装饰。
- Windows 当前值仅更改外框与间距；元素生成器、`bindComparison`、原文 `textContent`、按 occurrence 进入编辑器的代码未改。多元素、空值和长原文实际排版仍需浏览器检查。
- textarea 高度去除 112px 上限，并在初始化、输入、窗口 resize 时依 `scrollHeight` 更新。JSDOM 不提供可靠真实排版，需浏览器验收长值与重新显示。

## Claude 现场专项测试

在 `573dcfa`、工作树无修改时运行：

| 脚本 | 结果 | 覆盖重点 |
| --- | ---: | --- |
| `verify_plan_review.cjs` | 23/23 | 冻结计划、blocked/retained、批量范围、焦点与刷新 |
| `verify_gui_followups.cjs` | 19/19 | replace 禁止工作台元素编辑、Windows 直编 |
| `verify_duplicate_hover.cjs` | 16/16 | 重复与空元素身份、焦点与数据不变 |
| `verify_windows_snapshot.cjs` | 31/31 | 原文、重复 occurrence、Special、空值、直编与历史 |
| `verify_three_value_compare.cjs` | 25/25 | 三源、after、空值/重复/删除 |
| `verify_claude_ui_repairs.cjs` | 38/38 | 草稿恢复、CRLF/CR/LF 原文 |
| `verify_gui_feedback.cjs` | 30/30 | 元素身份、删除恢复、审计展示 |
| `verify_dom.cjs` | 11/11 | 基本 DOM 与交互 |
| `phase1-recovery-2026-10-17/verify_inspect_review.cjs` | 4/4 | 工作台审阅四态、冻结结果与输入保留 |

这些测试是 JSDOM/源码回归证据，不代表 Chrome 排版、真实 DPI 或 IME 验收。

## 最终产品文件回归

主 Agent 冻结 `temp.html`、`ui-polish.css`、`shell-ui.css`、`surface-ui.css` 后，对当前工作树仅运行一次完整 `npm test`，退出码 0；原始输出保存在 `final-npm-test.txt`。另重跑本目录 `verify_inspect_review.cjs`，4/4 通过，输出保存在 `final-inspect-review.txt`。`git diff --check` 退出码 0。最终差异仅有审阅标题装饰选择器收窄、示例说明默认折叠与少量局部窄屏/自然行高调整；未发现新的业务回退。Chrome 排版验收由主 Agent 的独立浏览器证据记录。


