配置新增能力：[CONFIG_CREATION.txt](CONFIG_CREATION.txt) 规定应用、方案与变量的原子创建、稳定对象基线、差异与还原；不改变运行计划与系统写入边界。

执行前审阅展示：[PLAN_REVIEW_PRESENTATION.txt](PLAN_REVIEW_PRESENTATION.txt) 定义冻结计划的只读摘要、目标定位、原因说明、窄屏与刷新焦点规则。核心无缝刷新与安全边界继续遵循以下契约。

Latest refresh behavior: [SEAMLESS_REFRESH.txt](SEAMLESS_REFRESH.txt) supersedes manual rebase and snapshot-wide stale blocking in V3.2/V3.3, following the owner requirement and Astra/ChatGPT review on 2026-10-16. Original intent, duplicate/shared protection and single-plan execution remain.

Claude 改版修复契约：[草稿恢复、原始字符保真与窄屏布局](CLAUDE_UI_REPAIR_CONTRACT.txt)。仅覆盖本轮已确认的问题，既有 V3.7 建议撤销规则继续适用。

本轮补充契约：[V3.7 建议删除双向采纳](V3_REMOVAL_TOGGLE_V3_7.txt)，建议删除采纳后保留绿色卡片，可按原始稳定 ID 在当前展示位置恢复；其他 V3.6 规则继续适用。

本轮元素编辑器契约：[V3.6 显示顺序与建议切换](V3_EDITOR_ORDER_V3_6.txt)，覆盖 V3.5 的幽灵锚点、原序号和不可移动规则；建议追加采纳后保留并按稳定 ID 撤销。业务计划和历史仍只使用 live rows，其他既有契约继续适用。

元素编辑器最新契约：[V3.4 建议与实际编辑状态](V3_EDITOR_STATES_V3_4.txt)，仅修改编辑器建议/采纳展示；V3.3继续约束工作台三值对照和真实after，V3.2约束匹配基线增删。

最新展示契约：[V3.3 三值对照及修订](V3_THREE_VALUE_V3_3.txt)。修订中的删除幽灵元素仅为展示映射，真实结果、预览应用和历史始终使用冻结 plan.after；V3.2 继续约束匹配基线增删。

# V3 Architecture Documents

正式产品实施：[Astra 2026-10-18 实施方案](V3_PRODUCT_IMPLEMENTATION_2026-10-18.md) 定义独立 V3 运行时、配置与执行持久化、权限、安装和验收。负责人已授权由演示转入真实产品实现；既有文档中的 UI-only 限制仍约束历史演示，不阻止独立产品工作。实现状态以当前代码及实际证据为准。[产品运行说明](../product-v3/RUNNING.md)。

文档优先级如下：

1. 负责人最新明确需求与确认。
2. V3_SELECTION_DIFF_V3_2.txt：本轮选定值增删契约；覆盖 V3.1 中普通选定值编辑只追加的冲突规则。
3. V3_MINIMAL_CONTRACT_V3_1.txt：其他未改变的 UI-only 规则继续适用。
4. 未被本次契约改变且已验证的规则。
5. drafts/ 中的旧架构草案，仅供参考；冲突时不覆盖上位文档，也不代表已实现事实。

drafts/V3_UI_DATA_CONTRACT_PARTIAL.txt 是来源页面可取回的部分内容，状态为 partial，原文在中途结束；它不是完整契约，不能作为完整规则使用。

Claude 修复收尾：[本轮缺陷复核与验证记录](CLAUDE_UI_REPAIR_REVIEW.txt)。最终源码 0665e005，原项目/镜像20组395项及最终源码两项CI通过；GPT已确认最后P2解决。

本轮 GUI 一致性契约：[四项负责人反馈统一任务书](GUI_FEEDBACK_CONTRACT.txt)。覆盖 Claude 修复契约中 ghost 展示原始值的旧显示规则；原快照与删除审计、绿色建议撤销规则保持。

- GUI feedback supplemental presentation/counting direction: `GUI_FEEDBACK_SUPPLEMENT.txt`, following the owner feedback and GPT supplemental approval on 2026-10-14.

- Display movement labels/counts follow `docs/architecture/GUI_DISPLAY_MOVEMENT.txt`; business movement and Audit retain the live-original ordering contract.

Latest user comments 1–9: `GUI_STATUS_FOLLOWUP.txt` governs ghost movement captions, editor count presentation, status-circle alignment and workbench replace editor entry. Existing business/Audit rules remain.

Latest Windows page scope: `WINDOWS_SNAPSHOT_BOUNDARY.txt` supersedes prior inventory scheme overlays and direct-editor proposal context only. Workbench/preview, core plans and manual editor audit remain unchanged.

History display linkage follows `HISTORY_ELEMENT_LINKAGE.txt`: reuse existing recorded element identity for before/after/detail highlighting; no model, Audit or business-rule changes.

最新负责人配置与重复悬停需求：`CONFIG_INLINE_AND_DUPLICATE_HOVER.txt` 覆盖此前 TOML 暂缓及同 ID 限定的悬停展示说明；核心计划、Audit 与稳定身份规则保持。
