# 第 4 轮最终审查

审查基线：`56dbf603`；分支：`v3/astra-interaction-polish-20261016`。本轮只读产品代码，仅新增此报告；未提交、合并或修改核心算法。

## 结论

未发现未解决的 P1/P2、冻结计划被展示层改写、确认绕过或新增误写路径。本轮发现 1 项 P2：新增工作台预览入口的范围标签不随批量勾选更新。主执行者已修复，本审查沿原复现路径确认关闭。没有阻止本轮 UI 改进收尾的问题。

## 已关闭 P2：工作台预览范围标签滞后

位置：`ui-v3/temp.html:158–161` 的 `inspectReviewMarkup()`；`temp.html:222` 的全选/清空处理、`:244` 的 checkbox change、`:377` 的 `renderFooter()`。

复现（本轮通过 JSDOM 调用真实 DOM 点击事件确认）：

1. 重置演示并查看 Java，工作台对照中的 `#inspectPreview` 显示“查看方案预览 →”，`checked.size === 0`。
2. 勾选 Python 的 `[data-check="1"]`，不切换当前应用，也不重新渲染对照区。
3. 入口仍显示“查看方案预览 →”，但点击后打开“批量方案预览”，`modalAction.targets` 为 Python313 的两条变量，Java 不参与。
4. 反向也存在：先在有勾选时渲染对照，再点击“清空”；入口仍显示“查看批量预览 →”，实际恢复为当前应用单方案范围。

原因：按钮文案只在 `renderInspector()` 时生成；批量选择变更只更新列表和 footer。按钮行为委托当前 `previewButton`，所以实际范围是最新的，入口文案却是旧的。新增测试“Workbench review action preserves the visible batch confirmation scope”先手动执行 `renderInspector()`，没有覆盖正常勾选流程。

修复与复核：主执行者新增 `inspectPreviewText()`，在现有 `renderFooter()` 更新路径只同步该按钮的 `textContent`，没有重建三值对照或更改目标选择算法。本轮独立重放真实 checkbox → 打开预览 → 取消 → 清空 → 再打开预览，标签与 Python 批量 / Java 当前方案范围均一致；追加检查全选、清空，原 textarea 节点和值保持不变。新增第 23 项回归覆盖真实勾选路径及输入节点、草稿和光标保留，23/23 检查通过。

## 已核对的关键边界

- 第 3 轮四项 P2 已在源码修正：零操作保留计划使用“模拟确认当前计划”；旧摘要不再声称进入编辑器即可解决全部删除；阻塞叠加更新具有可见版本提示；stale 展示实际 fields，冲突来源展示各成员模式。
- `reviewState`、摘要、目标导航消费既有 rows/plan；没有重建 After、写入删除资格或修改 `demo-plan.js` / `windows-elements.js`。冻结计划、实际模拟结果与历史使用同一计划的新增检查通过。
- 无操作无 issue 仍能确认成功选择且不新增修改历史；零操作含保护 issue 保留输入和基线；安全变更加保护 issue 的通知与历史表达部分完成。invalid、append 和撤销门槛保持。
- 确认发现相关变化时仍先刷新并返回；更新后的第一次点击只审阅，之后另一次确认才能写入。全目标校验与同步提交之间没有新增 await。新导航和返回动作没有写入、重建原基线或标记版本已审阅。
- canonical 目标键区分 User/System；同版本恢复 token 焦点保留前后区域，跨版本回退同目标标题；原文 disclosure 按目标键保留。新增“查看对照”保持只读输入，返回工作台保留批量勾选和当前编辑。

## 验证与实际限制

本轮阅读根与 ui-v3 的 AGENTS、架构优先级和相关 V3/无缝刷新契约、01/02/03 报告；核对 `temp.html`、`surface-ui.css`、`package.json` 差异及新增验证脚本；独立重跑 `node design-evidence/verify_plan_review.cjs`，修复前 22/22、修复后 23/23 通过，另执行上述范围标签复现及修复复核。没有重复执行完整 npm test；最初 32 组 605 项结果来自主执行者记录，新增第 23 项后的完整回归由主执行者另行记录。

已查看 `preview-retained-desktop.png`、`preview-retained-600.png`、`workbench-600.png`，未见新的预览横向溢出、footer 遮挡或状态阅读顺序问题。已核对 `browser-report.json` 及脚本：Linux headless Chromium 134，12/12 流程通过，无运行时错误，1360×900 与 600×640，使用 QA 注入 Noto Sans SC。两张窄屏截图带上一流程的暂态 toast，不宜把该 toast 当作截图内当前预览已经执行的证据。

证据不覆盖真实 Windows 环境、系统字体与 DPI/缩放组合、真实注册表、读屏软件或所有任意长名称。原型仍不执行撤销、append 自定义排序或真实 Windows 监控；本轮没有扩大这些能力。

## 主执行者最终验证补记

在上述 Astra 复核后，主执行者完成最终全套 npm test：32 组、606 项通过，退出码 0；浏览器补充流程扩为 13/13，包含实际勾选/清空后入口文案与请求范围一致性。browser-report.json、validation-summary.json 与 final-npm-test.txt 记录最终结果。截图脚本在每组夹具初始化时清除上一流程 toast，最终截图已重新生成；本段为主执行者验证记录，不扩大 Astra 独立执行范围。
