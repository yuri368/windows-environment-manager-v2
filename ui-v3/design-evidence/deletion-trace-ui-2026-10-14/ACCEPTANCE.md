# V3.5 删除痕迹验收

状态：本地实现及验证完成；提交后另行记录 CI 与 GPT 最终审查。

修改：
- 编辑器 token 使用整体 inline-flex；原始文本可在框内换行，变更标签和匹配勾保持固定子项。
- 工作台 Actual 保留待删除，After ghost 显示结果排除，使用静态斜纹与虚线，并说明当前尚未应用、不计入最终写入值。
- 原始 ID 从 live rows 删除后，列表保留只读痕迹行，显示原序号、原字符串、本次编辑已移除、不计入结果。纯投影 originalRows 与 live rows，不新增删除状态；锚在原序的下一个存活原 ID 前，无锚则末尾。

验证：
- 17 套自动化测试 332/332（原 321 + 新增 11）；test-report.json。新增覆盖手删/采纳建议删除、重复及空元素稳定 ID、重排锚点、新增后删除、删尽不存在、还原取消、Apply/History 真值、三处 hover 联动。
- 独立内置 Chromium HTTP 标签页；原用户页面未刷新/重置。Actual 待删除与 After 结果排除可见；截图 01-after-workbench.jpg。
- 采纳 Maven 删除后，9 个原元素中 8 个 live，原第 4 行仍有只读斜纹痕迹；截图 02-editor-removed-row.jpg。
- 采纳 1 和 222 追加后，两个新增 token 各一个布局盒，新增标签和匹配勾都在所属框内；截图 03-editor-token-cohesion.jpg。
- 304 字符 token（含中文目录、空格、标签、勾）正常框内换行；截图 04-long-token.jpg。实际 620×765 窄视口（浏览器缩放导致与设置值不同）没有文档/编辑主体/token 水平溢出，全部子项在框内；browser-layout.json 与截图 05-narrow-long-token.jpg。临时 viewport 已恢复。
- 还原后 0 个痕迹行、9 个 live、0 项变化，Apply 禁用。取消后 Actual 原 Maven 项仍在。只采纳删除后 Apply，历史只有 1 项删除且 after 不含 Maven 项，也没有未采纳 bin1 追加；截图 06-apply-history.jpg。浏览器错误日志为空。
- 首次普通刷新沿用旧 JS 缓存；验收采用独立页忽略缓存刷新，随后实际文案与供应文件一致。

影响与边界：
- 幽灵行无 input、button、拖动或常规 Tab 焦点，不进入 live 计数、序列化、计划或历史；新增又删不留幽灵，删除全部后仍为变量不存在。
- DemoPlan、temp.html 执行/快照/历史、V2、真实注册表未改；不合 main、不发布 Release。
- 主开发/HTTP为 H:/Yuri368/环境变量配置V3，Git镜像为 H:/Yuri368/环境变量配置V3-git/ui-v3。
