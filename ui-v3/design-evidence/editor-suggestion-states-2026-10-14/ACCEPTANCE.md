# V3.4 元素编辑器建议与本次编辑验收

主开发和HTTP根目录：H:/Yuri368/环境变量配置V3。基线a7d001f，分支v3/editor-suggestion-states。

## 改动

未采纳方案建议集中到一个紧凑区域，分别标建议删除/建议追加，±只采纳到live rows。列表行和前后对照不再叠selectionPlan的待删除或replacementExpected的将替换；实际变化只来自既有session.plan稳定ID审计。摘要只统计session.plan.operations，未采纳候选数量不计入。无候选/失效建议均有明确说明，重复与共享不提供错误删除按钮。

工作台V3.3三值、DemoPlan、Apply校验、历史处理、实际注册表均未改变。

## 检查

16套Node/JSDOM，共321/321；新增22项覆盖建议未采纳时零编辑、±采纳头尾顺序、手动REPLACE、还原、取消、部分采纳Apply、重复共享、失效建议允许独立手工编辑、过期实际编辑拒绝、whole/replace和结构变化结果相同无操作。旧测试只调整被新展示语义替代的文案/查询，不削弱raw/ID/应用/历史断言。

实际内置浏览器独立HTTP标签页：
- 01-open.jpg：Maven匹配bin→bin1后打开编辑器，只有建议区提删除/追加，两个对照保持原值，0变化Apply禁用。
- 02-adopted.jpg：分别采纳−与＋，实际删除ghost和新增出现，摘要新增1/删除1，建议清空；真实字符串去掉旧bin且前置bin1。
- 03-restored.jpg：还原恢复建议、原行和0变化，Apply禁用。
- 04-cancelled.jpg：已采纳后取消，Actual仍有原bin，未写入bin1。
- 05-applied-history.jpg：只采纳删除并Apply，历史仅REMOVE原bin，未追加未采纳的bin1。
- 06-narrow.jpg：实际CSS视口1000×765，无编辑器横向溢出，建议说明/长PATH/足部按钮可见。默认1600×900亦已查看。临时视口设置已恢复。

用户原HTTP标签页未刷新、未更改正在编辑的内容。本机预览服务原已停止，本次以bundledPython重新启动8000且仅绑定127.0.0.1，根目录仍为主V3。

## 范围

离线演示；不声称真实Windows/UAC/安装版兼容性。保留任务分支，不合main、不发Release。
