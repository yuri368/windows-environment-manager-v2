# 双工作区改版设计简报

## Domain exploration

Domain: 应用清单、方案、默认方案、输入来源、草稿、共享目标、执行预览、成功历史。
Color world: 暖灰工作纸、墨色正文、浅石灰分区、鼠尾草选择状态、深绿执行动作、赭色警告。
Signature: 同一份应用清单始终显示每个应用的方案和变量数量，选择与编辑详情彼此独立。
Avoid: 巨型单应用标题、三层同时展开的导航树、装饰图标与拼接后缀、拉伸空表和属性卡片、常驻解释和删除按钮阵列。

## Grounded directions

1. 软件文档的紧凑索引：任务先行，熟悉但容易沿用旧侧栏。
2. 时刻表的对齐节奏：每行显示应用、方案、数量；批量扫描明确。
3. 瑞士目录的字号层级：压低装饰层级，变量与值成为主体。
4. 规范说明书的分区：位置、规则、值逐步展开，可能仍像属性表。
5. 档案检索清单：清单与记录详情并列，索引不会因打开一条记录而消失。
6. 百科的交叉引用：共享变量与所属应用关系显式，可增加认知负担。
7. 工作准备台：应用选择与输入准备分区，须避免拟物外观。

User-pinned: light warm-gray/ink/restrained accent, batch-first workflow; native Qt controls and existing safety semantics. The concept seed assigned grounded candidate 5 (b9f896bf); use its clear inventory/detail relationship within the pinned direction. Operate rules preserve standard native controls/navigation.
Challenger judgement: transit/orienteering diagrams add scan discipline but competing semantic colors obscure scope and modes; cassette fold and bitmap/strobing data fields fail clarity. The tabbed-manual candidate contributes progressive disclosure, not literal material/chrome. These catalog expressions are declined for this Windows editing workflow. User task confirmation settles the layout emphasis; implementation proceeds within the authorized redesign.

## Direction contract

THESIS: Both screens keep the working inventory visible while placing the currently edited record beside it. Main view prepares a batch; editor changes one shared configuration draft.
OWN-WORLD: warm-gray canvas, stone/soft sage inventory surface, ink primary text, four text roles, one deep green selected/action vocabulary; tonal separation with quiet rules, 4px rhythm and native controls.
STORY: Select applications, see each option, inspect or adjust a record, then preview once. In the editor, find a record, edit default/choices first, adjust properties, update the draft, then review/save.
FIRST VIEWPORT: Main header is compact; left inventory is 300px with 56px application rows and inline options; right detail pane has one compact option context and variable rows. Reference values are collapsed by default. Fixed footer carries summary and one primary action. Editor uses a flat application index, a current-application option selector, and a flat variable list with an independent scope column. Global search can locate records across applications. Values/choices and light property fields align at the top; narrow windows stack properties.
FORM: grounded direction 5, archival finding-list relationship; seed b9f896bf. Native keyboard selection, checkbox state and unchanged business services provide interaction; no decorative animation.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Before-edit checkpoints

Main/editor intent: fast batch preparation and precise draft editing. Hierarchy: current values and aligned record names dominate; title secondary. Palette: user-pinned warm neutrals with green for actionable/selected state. Depth: quiet surface-color shifts and rules, no shadows or property cards. Typography: Microsoft YaHei UI for consistent Chinese and Latin navigation; monospace only values/source. Spacing: 4px base, 8/12px groups and workspace margins. Title 16px medium, field/list 13px regular, metadata 12px regular; only title/actions get emphasis. Native Windows DPI scaling remains intact. Inputs carry focus/error/disabled states, around 30px controls.

Confirmed visual correction: the user explicitly rejected the first capture batch as bulky and confusing, specifically the expanded navigation tree and its lettering. That batch is a rejected visual reference, not approval. The new layout must remove the expanded hierarchy, repeated icons and scope suffixes. Default values are 64–72px high; two choices occupy roughly 90px; empty choices do not reserve a table-shaped blank area. Remaining space belongs below content instead of inside stretched controls.

## Verification boundary

Native Qt capture in one batch: main desktop/narrow, editor desktop/narrow, search-empty, pending/default/choice, raw source and diff, high DPI, hidden-application batch errors and narrow invalid form errors. All previews use MemoryStore and temporary SQLite; user configuration and Windows environment remain untouched. The user's direct rejection authorizes a new layout iteration; review the completed iteration once and address only concrete material defects. No HTML/CSS detector for native Qt.
