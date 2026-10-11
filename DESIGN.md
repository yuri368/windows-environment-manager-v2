---
name: Windows 环境变量方案管理器 V2
description: 面向批量方案准备与精确配置编辑的 Windows 原生工作区
colors:
  canvas: "#F5F3EF"
  inventory: "#F0F0EB"
  surface: "#FCFCFA"
  surface-muted: "#F1F2ED"
  input: "#FFFFFF"
  ink: "#262D28"
  text: "#39443C"
  secondary: "#606B62"
  muted: "#666F63"
  disabled: "#90968D"
  rule: "#DDE1D7"
  control-border: "#BFC7BB"
  control-hover: "#98A692"
  accent: "#38644D"
  accent-hover: "#2C513C"
  accent-pressed: "#254631"
  selected: "#E5ECE2"
  hover: "#EBEFE7"
  focus: "#527A56"
  danger: "#A44336"
  danger-surface: "#FAEEE8"
  warning: "#85612B"
  warning-surface: "#F8EEDB"
  warning-rule: "#E3D1AF"
  scroll: "#BBC4B4"
typography:
  body:
    fontFamily: "Microsoft YaHei UI, Microsoft YaHei, Segoe UI"
    fontSize: "13px"
    fontWeight: 400
  title:
    fontFamily: "Microsoft YaHei UI, Microsoft YaHei, Segoe UI"
    fontSize: "16px"
    fontWeight: 500
  dialog-title:
    fontFamily: "Microsoft YaHei UI, Microsoft YaHei, Segoe UI"
    fontSize: "18px"
    fontWeight: 500
  section:
    fontFamily: "Microsoft YaHei UI, Microsoft YaHei, Segoe UI"
    fontSize: "13px"
    fontWeight: 500
  meta:
    fontFamily: "Microsoft YaHei UI, Microsoft YaHei, Segoe UI"
    fontSize: "12px"
    fontWeight: 400
  action:
    fontFamily: "Microsoft YaHei UI, Microsoft YaHei, Segoe UI"
    fontSize: "13px"
    fontWeight: 500
  code:
    fontFamily: "Cascadia Mono, Consolas, Microsoft YaHei UI"
    fontSize: "13px"
    fontWeight: 400
rounded:
  nav: "3px"
  xs: "4px"
  sm: "5px"
  md: "6px"
  lg: "8px"
spacing:
  base: "4px"
  field: "8px"
  group: "12px"
  section: "16px"
  workspace: "12px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.input}"
    typography: "{typography.action}"
    rounded: "{rounded.sm}"
    padding: "5px 10px"
  button-secondary:
    backgroundColor: "{colors.hover}"
    textColor: "{colors.accent}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "5px 10px"
  input:
    backgroundColor: "{colors.input}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "5px 8px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "5px 10px"
  manifest-row:
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    height: "56px"
    padding: "4px 8px"
  editor-index:
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    height: "30px"
    rounded: "{rounded.nav}"
  badge:
    backgroundColor: "{colors.surface-muted}"
    textColor: "{colors.secondary}"
    rounded: "{rounded.xs}"
    padding: "3px 7px"
  activation-status:
    backgroundColor: "{colors.selected}"
    textColor: "{colors.accent}"
    typography: "{typography.meta}"
    rounded: "{rounded.nav}"
    padding: "1px 5px"
---

# Design System: Windows 环境变量方案管理器 V2

## Overview

**Creative North Star: "档案检索清单"**

把常用工作做成一份随时可见的应用清单：主工作区先扫描批量选择、各自方案和变量输入，再查看详情并统一预览。配置编辑器沿用清单与记录详情并列的关系，让用户切换变量时仍看得到当前应用与方案。浅暖灰纸面、墨色正文和受控的深绿操作色服务于清晰阅读；平面分区、细线和间距承担层次。

密度来自对齐和折叠，而非加粗和堆叠层级。主界面每行并列应用、方案和简要状态；变量名、当前选定值与作用域/模式容易扫读，参考值默认收起。编辑器提供扁平应用列表、方案选择器、变量表和跨应用搜索，再让值编辑与属性设置共享顶部对齐的工作面。

**Key Characteristics:**
- 批量选择优先，详情与批次选择相互独立。
- 暖灰画布、石灰清单面与白色内容区，以明度分区而非阴影造层。
- 原始文本、临时草稿、校验、显式保存与执行前预览保持可见且可控。

**The Inventory Rule.** 展开一条记录时，应用清单仍留在工作区中。

## Colors

色彩以温和的石灰和纸白托住墨色文字；深绿只标记主要执行、选中和正向状态，错误与警告使用各自克制的暖色。

### Primary
- **松针绿**：主要执行按钮与成功/选中提示使用深绿；悬停和按下逐级加深。

### Secondary
- **浅鼠尾草**：选中与普通悬停以低饱和绿灰背景提示位置，不与主要操作抢焦点。
- **暖警示赭**：提示状态使用赭色文字和淡暖底色，和错误红区分。
- **砖红**：输入错误与危险操作提示采用红色文字或浅红底。

应用激活状态使用文字与小色标共同呈现：已激活为绿色，尚未激活为淡黄色。状态与批量勾选、当前方案草稿相互独立；输入无效仍使用红色反馈，并保留真实激活方案信息。

### Neutral
- **暖纸画布**：窗口底色。
- **石灰清单面**：应用清单和编辑器索引的背景。
- **纸白内容面**：变量详情、标题栏和页脚。
- **墨色**：主要文字；较浅墨绿灰承担正文和辅助说明。
- **细线灰绿**：分割线、边框与表格行界线。

**The Sparse Accent Rule.** 主要绿只承担执行和明确的选择反馈；常规信息靠文字层级与中性色区分。

## Typography

**Display Font:** Microsoft YaHei UI（优先），Microsoft YaHei、Segoe UI 依次回退  
**Body Font:** Microsoft YaHei UI（优先），Microsoft YaHei、Segoe UI 依次回退  
**Label/Mono Font:** Cascadia Mono、Consolas、Microsoft YaHei UI（代码和值视图）

**Character:** 原生 Windows 文字形态，中文和拉丁字母保持一致；以常规字重为主，少量中等字重用于标题与字段名。没有代码依据的行高或字距不作规定。

### Hierarchy
- **对话框标题**（500，18px）：独立预览、排序和历史窗口的主标题。
- **工作区标题**（500，16px）：主窗口和编辑器页面标题。
- **区段与字段名**（500，13px）：变量名和区段标识。
- **正文**（400，13px）：输入、按钮和主要内容。
- **元信息**（400，12px）：作用域、模式、数量和次要状态。
- **源码与代码值**（400，13px）：优先使用 Cascadia Mono，其后回退 Consolas。

**The Quiet Weight Rule.** 正文和列表以常规字重为主，工作区标题与主动作使用 500；不要用大号粗体盖过变量和值。表头等原生控件可能保留自身文字样式。

## Layout

应用程序是原生 PySide6/Qt 桌面界面，保留键盘与控件状态反馈。通用间距以 4px 为基数；字段间 8px、分组 12px、区段 16px、工作区边距 12px。主界面左侧批量应用清单约 300px，应用行约 56px，方案直接显示在行内；右侧呈现当前详情，底部摘要与当前内容对应的主按钮固定可达。变量参考信息默认折叠。窄窗口保持双栏可用并让内容滚动，错误信息贴近需修复的字段。

编辑器左侧是应用索引，初始宽度约 240px（可调范围 216–272px）；应用行约 30px 高、不加图标，普通高度下最多展示六行后滚动，短窗口会缩减可见行数以保留三组标题动作。应用和方案标题可点击打开对应概览；应用、方案和变量各自的标题行右侧将新增与删除入口并列。应用概览摘要行右侧提供方案选择器，列出“全部方案”和该应用的所有方案，即使仅有一个方案也保留控件。“全部方案”展示当前应用的聚合记录但不改变左侧当前方案；选择具体方案进入其概览，并与左侧方案选择同步。新增、删除和设默认方案仍按左侧当前方案上下文执行。选择器只在概览页出现，表单页不重复展示。方案选择器下方另有设默认方案；动作按钮统一使用简短文字，提示中保留完整对象说明。变量列表使用独立的 48px 作用域列。搜索覆盖所有应用、方案和变量，搜索状态下匹配结果表替代变量表，应用与方案导航仍保留。右侧值与属性从顶部对齐；默认值编辑区约 72px，候选表只在有项目时出现，两个候选项约 90px 高。无候选时用短状态文案代替空表。属性字段不包卡片，保持相邻可读；窄编辑器将属性栏移到值与候选项下方。

**The Visible Inventory Rule.** 内容展开时保留所在清单，主操作摘要固定于页脚，局部滚动只用于超出当前窗口的内容。

主界面方案选择器按实际字体、图标与样式留白计算宽度；常用方案名称完整可见，超长名称明确省略并保留完整提示。下拉列表按实际行高计算尺寸，常规短列表完整显示，长列表支持滚动。排序窗口为读取到的原始值中的空段和纯空白项显示明确标签，原始字符串保持不变；标签不判断这些内容由 Windows 或其他软件创建。

## Elevation & Depth

界面以平面色块和细分隔线组织工作区，不使用投影阴影。清单、内容、表单和页脚通过暖灰、石灰与纸白的色调差异分层；控件状态以边框或浅色填充变化表达。

**The Flat Surface Rule.** 静态界面以色调和规则线建立空间，避免卡片阴影与装饰性浮层。

## Shapes

常用控件和按钮采用轻柔的小圆角（5px），徽标和变量详情等局部小块用 4px；警示横幅为 6px，预览卡片为 8px。分隔线使用 1px 规则线。主变量行保持方正、无外框，仅以底部分隔线收尾。

## Components

### Buttons
- **Primary:** 深绿填充、白字、中等字重；圆角 5px，内边距 5px × 10px。用于预览/应用或确认执行等当前主操作。
- **Secondary:** 浅绿灰底色配绿色文字，用于次级、可逆的草稿操作。
- **Quiet:** 透明背景和边框，仅在悬停或键盘焦点时显出淡色反馈；用于工具栏与行内动作。
- **Hover / Focus:** 悬停转为浅色底并加深边线；焦点使用绿色边线；禁用状态降低对比度。

### Chips
- 状态徽标使用浅中性底色和辅助文字；当前选中使用浅鼠尾草底色与深绿字，警告使用淡赭底色。

### Cards / Containers
- 工作区清单与内容面是无阴影的大平面。
- 可操作变量行只用底部分隔线；警示容器使用淡暖底和细边线。
- 执行预览中的变更卡片使用纸白底、细线边框和 8px 圆角。

### Inputs / Fields
- 输入使用白底、灰绿细边线和 5px 圆角，内边距 5px × 8px。
- 悬停边线加深；键盘焦点变为绿色边线；非法输入使用错误红边；禁用输入使用浅灰绿底。
- 选择框和单行文本输入保留原生可键盘操作行为，常规高度约 30px；配置编辑器的多行精确默认值区为 72px。

### Navigation
- 主应用清单每行独立呈现勾选、应用名、方案下拉与简要状态；勾选和打开详情是分开的操作。
- 编辑器应用列表不加装饰图标；应用、当前方案和变量表形成可扫读的索引，新增/删除操作在各自标题行并列并靠近其管理对象。
- 选中导航使用纸白或浅鼠尾草背景和绿色文字，不用重字或扩展树枝装饰。

### Signature Component: 应用清单行
主页面的应用清单行把批量勾选与当前详情导航分开，并在行内显示方案和变量数量/状态；底部摘要说明本次批次将预览的应用和变量数。

**The Draft Boundary Rule.** 表单先更新到编辑草稿，只有查看差异并明确保存后才写入 TOML；界面选择和应用不会改写配置定义。

## Do's and Don'ts

### Do:
- **Do** 以 Microsoft YaHei UI 为首选系统字体，并保留系统 DPI 缩放。
- **Do** 用暖灰底、墨色文字和稀疏深绿强调组织页面。
- **Do** 让批量清单、应用/方案关系和当前选择始终容易辨认。
- **Do** 保持原始文本与空格，保留未提交草稿，并明确区分更新草稿和保存文件。
- **Do** 在持久化变更前展示差异或执行预览，并把校验错误放到相关内容旁。

### Don't:
- **Don't** 同时展开应用、方案、变量三层树形导航。
- **Don't** 使用厚重字重、巨型标题、重复图标、作用域拼接后缀或装饰性动画。
- **Don't** 用空候选表、拉伸控件或属性卡片制造额外占位。
- **Don't** 以卡片阴影替代平面分区、细线和留白。
- **Don't** 将选择、更新草稿、保存配置和应用环境变量混成一个含义不明的动作。
