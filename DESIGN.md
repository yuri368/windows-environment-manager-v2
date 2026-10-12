---
name: Windows 环境变量方案管理器 V2
description: 面向批量方案准备与精确配置编辑的 Windows 原生工作区
colors:
  canvas: "#EDF0ED"
  inventory: "#F6F8F6"
  surface: "#FFFFFF"
  surface-muted: "#F7F9F7"
  input: "#FFFFFF"
  ink: "#22342A"
  text: "#405247"
  secondary: "#5D6F63"
  muted: "#5C6D61"
  disabled: "#99A89E"
  rule: "#E3E9E3"
  control-border: "#D1DDD3"
  control-hover: "#9CB4A3"
  accent: "#286C51"
  accent-hover: "#225E46"
  accent-pressed: "#1D583F"
  selected: "#E7F2EA"
  selector-current: "#F3F7F4"
  selector-hover: "#F4F8F5"
  hover: "#EDF4EE"
  focus: "#4D8765"
  danger: "#A4473E"
  danger-surface: "#F9EDEA"
  warning: "#93611F"
  warning-surface: "#FBF2E5"
  warning-rule: "#EAD7B9"
  scroll: "#C5D2C8"
typography:
  title:
    fontFamily: "Microsoft YaHei UI, Microsoft YaHei, Segoe UI"
    fontSize: "18px"
    fontWeight: 500
  body:
    fontFamily: "Microsoft YaHei UI, Microsoft YaHei, Segoe UI"
    fontSize: "13px"
    fontWeight: 400
  section:
    fontFamily: "Microsoft YaHei UI, Microsoft YaHei, Segoe UI"
    fontSize: "13px"
    fontWeight: 500
  meta:
    fontFamily: "Microsoft YaHei UI, Microsoft YaHei, Segoe UI"
    fontSize: "12px"
    fontWeight: 400
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
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "5px 10px"
  button-secondary:
    backgroundColor: "{colors.hover}"
    textColor: "{colors.accent}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "5px 10px"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "5px 10px"
  input:
    backgroundColor: "{colors.input}"
    textColor: "{colors.text}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "5px 8px"
  manifest-row:
    backgroundColor: "{colors.inventory}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    height: "48px"
    padding: "4px 8px"
  scheme-selector-compact:
    height: "24px"
    fontSize: "12px"
    rounded: "{rounded.xs}"
    backgroundColor: "transparent"
  scheme-selector-primary:
    width: "166px"
    height: "31px"
    fontSize: "12px"
    rounded: "{rounded.sm}"
    backgroundColor: "transparent"
  scheme-popup:
    primaryWidth: "166px"
    compactMinimumWidth: "166px"
    rowHeight: "30px"
    padding: "3px"
    borderWidth: "1px"
    rounded: "{rounded.md}"
  variable-row:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    height: "48px"
    padding: "4px 8px"
  editor-browser:
    backgroundColor: "{colors.inventory}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    width: "224px"
  editor-property-value:
    backgroundColor: "{colors.input}"
    textColor: "{colors.text}"
    typography: "{typography.code}"
    height: "72px"
    rounded: "{rounded.sm}"
---

# Design System: Windows 环境变量方案管理器 V2

## Overview

**Creative North Star: "紧凑的方案工作台"**

界面把 Windows 环境变量的批量方案准备和精确配置编辑呈现在原生 PySide6 工作区中。浅暖灰画布、白色内容面、细分隔线和克制的深绿强调帮助用户快速识别当前对象与操作；字体层级与紧凑对齐负责信息密度，Qt 原生窗口、控件、键盘行为和 DPI 缩放保持一致。

主工作台用约 260px 应用栏、右侧方案明细与固定底部操作区组织流程；应用栏可在 248–320px 间调整。列表行把批量复选、详情导航和方案选择分成独立操作，以计数表达匹配状态；变量表用行式字段呈现，检查依据按需展开。配置编辑器使用应用、真实方案组标题及其变量子项导航、方案范围总览与单条记录属性表单，原始 TOML 和保存差异仍是正式编辑流程的一部分。

**Key Characteristics:**
- 紧凑桌面密度，以真实内容和可读性决定控件尺寸。
- 应用导航、批量选择、当前准备方案、系统激活方案和配置默认方案彼此明确。
- 配置草稿、原始文本、校验、差异保存和执行前预览边界清楚。

**The Separate Intent Rule.** 勾选只决定批量范围，点击应用主体只切换详情；方案选择只改变当前会话选择。

## Colors

暖灰与灰绿色中性色承载清单和分区，白色用于工作面与输入；深绿只强调主要动作、焦点和正向选择，警告与错误各用语义色。

### Primary
- **松针绿**：用于主操作、有效焦点和明确的正向选择；悬停与按下逐步加深。

### Secondary
- **鼠尾草绿灰**：用于选中和悬停底色，帮助定位而不与主要操作争夺注意力。
- **暖赭色**：用于警告状态和待处理信息，与错误色区分。
- **砖红色**：用于输入错误和危险操作提示。

### Neutral
- **暖灰画布**：主窗口和对话框背景。
- **清单灰绿**：应用栏与编辑器导航区域。
- **纸白工作面**：详情区、编辑表单、页眉和底部操作栏。
- **墨色与灰绿文字**：主体内容和次级说明。
- **细线灰绿**：分隔线、输入边框和表格行界线。

激活、匹配、勾选和目标方案是不同状态。匹配结果以短计数或文本呈现；`0/N` 表示未匹配或需配置，检测失败单独呈现错误，不借用绿色完成状态。

**The Sparse Accent Rule.** 常规信息靠文字层级与中性色区分；强调色只承担操作、焦点和明确状态。

## Typography

**Display Font:** Microsoft YaHei UI（优先），Microsoft YaHei、Segoe UI 依次回退  
**Body Font:** Microsoft YaHei UI（优先），Microsoft YaHei、Segoe UI 依次回退  
**Label/Mono Font:** Cascadia Mono、Consolas、Microsoft YaHei UI（代码、路径、原始值）

### Hierarchy
- **标题**（500，18px）：工作区与对话框标题。
- **区段与字段名**（500，13px）：分组标题、变量名和表头重点。
- **正文**（400，13px）：主要内容、输入和按钮。
- **元信息**（400，12px）：作用域、模式、计数和辅助说明。
- **代码与原始值**（400，13px）：保留原始字符串的精确呈现与编辑。

普通信息保持常规字重；不以巨型标题或重复粗体挤压变量和值的阅读空间。

## Layout

界面使用 Qt 逻辑像素和字体度量，间距按 4px 基数组织：基础 4px、字段 8px、分组 12px、区段 16px、工作区 12px。按钮和输入保持紧凑的原生操作热区，滚动区各自独立。

主工作台左侧应用清单初始约 260px，可在 248–320px 间调整；1px splitter 连续分隔清单和明细。应用行约 48px；右侧显示当前应用与目标方案、变量行和可展开的 Windows 对照/检测详情。底部操作栏固定可见，始终说明预览目标范围。应用行中的批量复选框不切换详情；点击行主体导航详情。每个应用可有自己的会话方案选择，系统当前激活方案与配置默认方案各自显示。匹配状态压缩为计数或短文字；长方案名省略显示并提供完整提示。变量行约 48px，以作用域、模式、来源、当前选定值、检测和操作字段支持快速扫描；长值可通过提示或专用完整值查看器读取。

配置编辑器左侧 browser 初始 220px，限于 216–224px；中间总览和右侧单条记录属性表单在宽布局下并排，右侧属性栏限于 320–340px。分区以连续 1px splitter 分隔。低于 1020px 时总览与属性区上下堆叠，保持独立滚动而不丢失所选记录。属性表单使用紧凑网格，不包大卡片；精确默认值编辑器高 72px。导航展示真实方案组标题及其变量子项；总览的“全部方案”只筛选跨方案记录，操作方案仍由左侧实际方案导航决定。它不是新增、删除或设默认方案的操作上下文。原始 TOML、草稿、保存差异、撤销重做和文件变更提示保持在同一正式会话中。

**The Independent Scroll Rule.** 内容超出可见区域时只滚动所属清单或工作区；固定操作区和关键动作仍可达。

**The Overview Scope Rule.** “全部方案”只筛选跨方案总览；实际操作方案由左侧真实方案导航确定，选择总览记录时明确定位到其所属方案和变量。

## Elevation & Depth

界面以浅暖灰、清单灰绿、纸白和 1px 细线建立层次，不使用投影阴影。输入聚焦、选中、悬停和错误通过边框或轻色填充反馈。

**The Flat Surface Rule.** 静态空间由明度与分隔线组织，不用阴影和装饰卡片取代信息层级。

## Shapes

常用输入与按钮使用轻柔小圆角（5px）；局部小标签和导航采用 3–4px，警告区域使用 6px，预览内容容器可用 8px。变量行保持方正无外框，以底部分隔线收尾。状态颜色始终配有可读文字。

## Components

### Buttons
- **Primary:** 深绿填充、白字和中等字重，用于预览、确认或明确保存。
- **Secondary:** 浅绿灰底和深绿文字，用于可逆的次级动作。
- **Quiet:** 工具栏、筛选与行内动作使用透明底，悬停和键盘焦点时出现轻反馈。

### Inputs / Fields
- 输入采用白底、灰绿细边、5px 圆角和紧凑内边距；焦点、悬停、禁用和无效状态有不同反馈。
- 配置编辑器精确文本控件保留空白、引号、反斜线和换行，不将输入格式化成另一种含义。

### Navigation and Lists
- 两个方案入口共用 Qt 选择行为与菜单绘制。侧栏入口高 24px、12px 字体，透明底且仅交互态显示细边；右侧入口固定 166×31px、12px 字体、5px 圆角，关闭态与菜单均为 166px 宽且左边缘、方案文字位置对齐。
- 主入口菜单宽 166px；侧栏菜单跟随入口宽度并至少为 166px，屏幕空间不足时以工作区为限。滚动条占用现有宽度，不另行撑宽菜单。每行基准 30px（按字体高度适配），最多三行可见；列表四周 3px 内边距加 1px 边框，选项无额外 QSS 留白。主入口两方案通常为 166×68px。
- 菜单方案名 12px、匹配计数 11px，同行显示。小勾号只表示当前准备方案，不代替系统激活状态；正常检测只显示短计数，异常保留明确文案。长名称省略；默认/激活辅助信息不挤入菜单行，完整名称、默认/激活与检测语义保留在提示和无障碍文本中。标题区仍明确显示默认与激活状态。
- 右侧方案菜单优先右缘对齐；侧栏有足够宽度时菜单留在应用清单内。所有定位仍受屏幕可用区域约束。
- 主方案入口常态透明底、1px 透明边框占位；悬停使用 selector-hover 浅底和 control-border 细边，键盘焦点/展开时使用白底与 focus 边框。状态变化不改变尺寸或文字起点，避免像路径输入框一样常驻强调。
- 方案菜单的当前准备项与光标候选项使用 selector-current 浅底，悬停使用 surface-muted；光标移到其他候选项时附加轻描边，小勾号仍留在当前准备项。导航反馈不复用匹配 badge 的成功绿色大块；真实匹配仍由计数和检测标签表达。激活记录与 Windows 实时匹配仍彼此独立。
- 方案菜单由单一圆角细边列表承载；外层窗口无系统边框和阴影，不保留菜单上下空白。优先在选择框下方留 4px 展开，靠右时向左调整，底部空间不足时在上方展开，整个弹窗保持在屏幕工作区内。
- 应用行包含批量复选、详情入口、当前准备方案和简短匹配计数；它不重复堆叠激活徽标。
- 变量行分别展示作用域与模式。相同变量名的 User 与 System 记录保持独立。
- 编辑器左侧按应用、实际方案和变量导航；新增、删除与设默认动作靠近相应对象。

### Editor Workspace
- 左侧按真实方案分组并列出变量子项；“全部方案”仅筛选总览记录，不能作为操作方案。
- 总览表按当前应用及筛选范围显示记录；每行代表一条具体变量定义。
- 属性检查器只编辑当前选中记录的名称、作用域、模式、默认值、editable、choices 与 special 等真实字段。
- 表单更新只改变草稿；只有明确查看差异并保存后才写入配置文件。切换、关闭和原文编辑沿用未保存保护。

### Preview and Execution
- Windows 实际值与检测依据默认折叠，按需展开查看。
- 环境变量应用、撤销、冲突处理、排序确认和历史恢复均沿用现有服务层预览与确认流程；界面选择本身不写注册表、不应用环境、不改变 TOML。

**The Draft Boundary Rule.** 更新表单、保存 TOML 和应用 Windows 环境是三个独立步骤，各自保留明确反馈与既有安全流程。

## Do's and Don'ts

### Do:
- **Do** 使用 Microsoft YaHei UI 优先字体，并按 Qt DPI 逻辑尺寸适配窗口。
- **Do** 用约 260px（248–320px 可调）应用清单、48px 清单/变量行、216–224px 编辑器 browser、320–340px 属性栏和 72px 精确值编辑区维持紧凑节奏；分栏用连续 1px splitter。
- **Do** 明确区分导航、批量勾选、目标方案、系统激活状态和配置默认值。
- **Do** 将所有方案总览视作筛选范围，并维持真实当前方案操作上下文。
- **Do** 保留原始值、空字符串、choices 标签和值以及未提交草稿。
- **Do** 在持久化或执行前展示正式差异/预览，并把校验错误放在相关字段旁。

### Don't:
- **Don't** 把原生应用改成网页或 WebView，也不要照搬原型的模拟 Windows 操作和样例 TOML。
- **Don't** 让点击应用行意外勾选批量项，或让勾选复选框切换详情。
- **Don't** 将“全部方案”作为真实操作方案；它只筛选跨方案总览。
- **Don't** 合并同名 User/System 记录，或混淆当前选定值、默认值、上次应用值和系统实值。
- **Don't** 让选择方案直接应用环境、写注册表或修改原始 TOML。
- **Don't** 删除既有撤销、恢复、预览、冲突、排序确认或草稿保护能力。
