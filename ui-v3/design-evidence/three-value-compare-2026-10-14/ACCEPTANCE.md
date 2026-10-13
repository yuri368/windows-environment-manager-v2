# V3.3 三值对照验收

2026-10-14，主开发和 HTTP 服务目录 H:/Yuri368/环境变量配置V3。任务分支 v3/three-value-compare。

## 实现

统一 token 字符串视图展示 Desired、Actual 和 After。Actual 在原始 occurrence 位置标删除，After 以划线幽灵 token 保留删除证据，独立最终字符串只读 plan.after。新增位置沿冻结操作，保持未变元素顺序/空片段/重复；跨栏关联按确定 occurrence 与 insert 来源，不按相似文本。无效计划保留输入和当前快照，但不提供确定结果。元素编辑器 +/- 能力保留。

最后 live row 被删除时 after.exists=false/raw=null；保留空 row 或显式创建空 row 时 exists=true/raw=""。同目标 mixed Special 请求阻止确认。历史保留计划真实 exists/raw，避免编辑审计空字符串覆盖不存在。

## 检查

15 套 Node/JSDOM，共299/299。新增20项覆盖三值、幽灵排除序列化、前置/后置、重复身份、焦点/指针联动、过期阻止、整体替换和不存在/空值边界。

实际 Chrome 新独立验收标签，保留用户原页：Maven bin→bin1，三值对照与预览一致；确认后历史确实去掉bin并前置bin1；鼠标新增token联动Desired/After，键盘保留token联动Actual/After。1600×729正常两列；1000×765窄视口一列，无横向溢出；状态标签均在色框内部。临时viewport设定已经恢复，刷新独立演示标签回到可供用户查看的未应用样例。

截图：three-value-desktop.jpg、three-value-narrow.jpg、three-value-history.jpg。截图为JPEG二进制；主开发文件与Git镜像同步后按SHA256核对。

## 范围

仅本页演示数据；未访问真实Windows注册表、未迁移V2、未合main、未发Release。截图和自动检查不代表真实安装版/UAC/DPI矩阵验收。
