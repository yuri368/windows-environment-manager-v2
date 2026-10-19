# 下一阶段设计采样

日期：2026-10-16。基于 Git `912c0fd`，产品提交 `45066dd`。只采集下一阶段计划依据，没有修改产品代码，没有运行本轮回归验收。

在 Chrome 新建独立演示标签，使用 `http://127.0.0.1:8000/temp.html`；实际 CSS 视口为 1383×851。未更改浏览器视口或媒体设置。

1. 选择 Hadoop，定位 Path。将当前选定值填写为 `%HADOOP_HOME%\bin;;%HADOOP_HOME%\bin; H:\开发工具\长目录 ;`，展示重复项、空元素、首尾空格与中文路径；保存 `workbench-current.png/.txt`。
2. 打开 Windows 环境页。模拟快照仍是初始数据；保存 `windows-current.png/.txt`。系统 PATH 和 DEMO_SEARCH_PATH 含已有重复与空元素。
3. 打开历史。当前只有初始记录，保存 `history-initial-current.png`；该图不代表真实操作历史详情的验收，也不能证明当前没有历史详情功能。
4. 返回工作台，打开单应用方案预览，保存 `preview-current.png/.txt`。预览有 HADOOP_HOME 和 Path 两目标，没有确认应用，没有新增模拟写入历史。

独立标签中的选定值修改只存在浏览器内存，未导出或覆盖配置，也未写真实 Windows。旧的配置编辑器成果和其他页截图只作为补充；Astra 应区分当前采样、历史证据与未来设计建议。
