# V3 前端冻结验证记录

记录日期：2026-10-19。此文件根据本聊天已完成的工具执行输出整理；本次保存记录未重新运行测试。

## 已观察到的结果

| 验证 | 结果 | 证据与范围 |
|---|---|---|
| 产品接线 fixture | 31/31 通过，进程退出码 0 | `node design-evidence/verify_product_runtime.cjs`；Node/JSDOM 契约 fixture |
| 既有 UI 回归 | 33/33 脚本通过，进程退出码 0 | 依次执行 package.json 的 test 脚本清单，断言未修改 |
| 最后 NUL 提示调整后配置回归 | 配置编辑 14/14、配置创建 35/35 通过，进程退出码 0 | `verify_config_editor.cjs`、`verify_config_creation.cjs` |
| JavaScript 语法 | 通过，进程退出码 0 | 对 product-app.js、product-runtime.js、windows-elements.js、verify_product_runtime.cjs 执行 node --check |

产品 fixture 最终汇总原文：

```text
31 product contract checks passed (JSDOM fixtures; real backend/browser acceptance is separate).
```

既有 UI 回归最终汇总原文：

```text
33/33 existing UI regression scripts passed; optional JSON report writes stayed in memory.
```

## 运行方式与时间边界

- 33 个既有回归脚本运行在相互独立的 Node 子进程中，测试断言未修改。为遵守文件写入范围，fs.writeFileSync 仅对 design-evidence 下 JSON 报告进行 JSON 解析并保留在内存，其他写入会抛错；因此这次运行没有更新旧 JSON 报告文件。
- 完整 33 脚本回归在最后一次 NUL 重复提示调整前通过。该调整完成后，重新通过产品 31/31、配置编辑 14/14、配置创建 35/35 及上述语法检查；未重复运行全部 33 脚本。
- 之前的逐项输出保留在本聊天工具执行记录，未保存完整原始 stdout；本文件是依据实际输出整理的摘要，不能作为完整原始日志引用。
- Node/JSDOM 验证不代表真实浏览器、Windows Registry、原生 DPI、UAC 或安装包验收。真实浏览器证据由主 agent 单独记录。

## 产品 fixture 覆盖要点

- ticket/CSRF、输入有序保存与光标保留、审阅与执行分离、预览更新、执行结果按原 key 对账、Ctrl+S。
- 手动编辑失败/取消保留草稿；冻结 audit 的重复/空元素逐 occurrence 标记；ADD/REMOVE/REPLACE/MOVE、类型变化及 Special 摘要。
- apps 非法草稿与 workingApps 运行时隔离、有效 Apply、合法晋级、运行自定义值、prepared 选择。
- configurationBaseline 按 recordId 绑定；合法/非法未保存草稿重启后 diff、新节点 addition、重排后基线及 restore。
- 原生 unsupported 类型 raw:null 只读、编辑/删除/应用入口阻断、类型切换、普通字符串原文保真。
- NUL 错误不被本地与后端重复合并，独立字段错误仍保留；分步空配置创建、生产入口无演示初始化。

## 既有 33 脚本清单

1. `design-evidence/verify_demo_plan.cjs`
2. `design-evidence/verify_dom.cjs`
3. `design-evidence/verify_elements.cjs`
4. `design-evidence/verify_interactions.cjs`
5. `design-evidence/verify_examples.cjs`
6. `design-evidence/verify_change_states.cjs`
7. `design-evidence/verify_element_feedback.cjs`
8. `design-evidence/verify_append_feedback.cjs`
9. `design-evidence/verify_adopt_pending.cjs`
10. `design-evidence/verify_history_sidebar.cjs`
11. `design-evidence/verify_overall_audit.cjs`
12. `design-evidence/verify_unified_plan.cjs`
13. `design-evidence/verify_matched_baseline.cjs`
14. `design-evidence/verify_refresh_plan.cjs`
15. `design-evidence/verify_matched_workflow.cjs`
16. `design-evidence/verify_refresh_workflow.cjs`
17. `design-evidence/verify_three_value_compare.cjs`
18. `design-evidence/verify_editor_suggestion_states.cjs`
19. `design-evidence/verify_deletion_trace_ui.cjs`
20. `design-evidence/verify_editor_order_toggle.cjs`
21. `design-evidence/verify_removal_suggestion_toggle.cjs`
22. `design-evidence/verify_claude_ui_repairs.cjs`
23. `design-evidence/verify_gui_feedback.cjs`
24. `design-evidence/verify_suggestion_manual_edit.cjs`
25. `design-evidence/verify_display_movement.cjs`
26. `design-evidence/verify_gui_followups.cjs`
27. `design-evidence/verify_windows_snapshot.cjs`
28. `design-evidence/ui-navigation-2026-10-15/verify-navigation.cjs`
29. `design-evidence/verify_history_linkage.cjs`
30. `design-evidence/verify_duplicate_hover.cjs`
31. `design-evidence/verify_config_editor.cjs`
32. `design-evidence/verify_config_creation.cjs`
33. `design-evidence/verify_plan_review.cjs`

## 当前冻结文件 SHA-256

| 文件 | SHA-256 |
|---|---|
| `index.html` | `A5CE3691684295C698FEFB662EF8D0AF9559617D2B13911E0120E26F6C75D0AE` |
| `product-app.js` | `F53531A24C15A2A3E60207D9DF1FC623BB7D32BC1188C19F743CA3F0CC419CE6` |
| `product-runtime.js` | `D2A39CC6D4C0E73B2F9D681F9018A7D48A4F545283D4EB61D44317295466F383` |
| `windows-elements.js` | `BE588ADAC01D3387AC939D6464F766036AD05645EAC8D08E7E76E6B11284E709` |
| `design-evidence/verify_product_runtime.cjs` | `CA8E9D997A8D356F5EBDDF3409B3CFAA407AD921087B2529A6206FD6E62287F3` |

哈希在保存本记录时只读采集；未修改上述冻结文件。主 agent 同步 mirror 和构建时可核对。
