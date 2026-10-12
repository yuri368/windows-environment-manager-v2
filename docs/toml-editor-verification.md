# TOML 编辑器验收记录

> 历史验收归档：本页记录当时配置编辑器的执行结果；vNext 的统一元素、特殊值、共享与整理验收见 [vnext-verification.md](vnext-verification.md)。

验收日期：2026-10-09。

| 要求 | 实现与直接验证证据 |
| --- | --- |
| 先设计架构，再实现 | `toml-editor-architecture.md` 定义文档、会话、界面三层；实现对应三个独立模块，复用既有 `VariableSpec`、`Choice` 与 `parse_config()` |
| 添加环境变量 | 文档与 UI 测试新增字段记录、候选项、新应用/方案；真实主窗口编辑器联动测试保存新增变量后重新加载核对 |
| 修改环境变量 | 覆盖默认值、作用域、四种模式、编辑权限、候选项、变量重命名与跨组移动 |
| 删除环境变量 | 覆盖指定作用域删除、双作用域折叠、空分组清理；主窗口联动测试在保存后核对被删除变量不再存在 |
| 原格式与字符串保留 | 覆盖注释、未修改字段布局、引号键、Windows 路径、CRLF/混合换行和字符串中的特殊分隔符 |
| 草稿、撤销及源码编辑 | 覆盖撤销/重做、非法源码保留、非法表单保留、导航/退出取消；未明确保存时文件不变 |
| 保存正确性 | 覆盖原子保存、文件在外部修改、多个编辑器、准备刷新期间发生外部修改、磁盘写入失败、刷新失败、已保存但接口返回异常 |
| 运行时刷新与状态隔离 | 主窗口实际编辑器事件循环完成增改删后重新渲染变量卡片；内存存储零写入，已应用状态不被编辑器修改 |
| 主窗口入口 | 可视化入口和源码入口使用同一 `ConfigEditorDialog`，删除旧独立源码保存实现 |
| 运行与依赖 | `--check`、`compileall`、`pip check` 和 Windows PowerShell 启动器语法检查通过；原生 Windows 编辑器预览生成截图，源文件字节未变 |

当日验证：整套原有和新增的 115 项测试通过；随后增加真实主窗口事件循环的增改删保存回归，并重跑两个 UI 测试模块，34 项通过。当时测试集合共 116 项。新编辑器的文档、会话和界面测试使用临时文件及内存环境存储；全套中的 Windows 适配测试仅操作隔离测试键并已清理。后续发布验证见 [Windows 安装包验收](windows-release-verification.md)。

运行命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m unittest tests.test_config_editor_ui tests.test_ui -q
.\.venv\Scripts\python.exe -m envmanager --check
.\.venv\Scripts\python.exe -m compileall -q envmanager
.\.venv\Scripts\python.exe -m pip check
```

公开界面截图见 [桌面界面设计](ui-design.md)。用户的 `env.toml` 未由此次编辑器开发与测试修改；旧项目未参与实现。
