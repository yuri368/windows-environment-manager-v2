# Current project boundary

- Current work is limited to the V3 browser demo and UI-only scope in docs/architecture/V3_THREE_VALUE_V3_3.txt (including its later unified-token and ghost-view revision); V3.2 still governs matched-selection edits.
- Editor suggestion/adoption display follows docs/architecture/V3_EDITOR_STATES_V3_4.txt; suggestions do not determine an editor apply result.
- V3.6 visible ordering and adopted-addition toggles follow docs/architecture/V3_EDITOR_ORDER_V3_6.txt; displayOrder/adoptionMap are editor session UI state only.
- V3.7 adopted-removal toggles follow docs/architecture/V3_REMOVAL_TOGGLE_V3_7.txt; removalAdoptions is session provenance only and restores the exact original ID at its current display position.
- Before changing behavior, consult the corresponding contract section; use docs/architecture/README.md for document priority.
- Do not modify main directly, access or modify the real Windows Registry, or migrate V2 code into V3.
- Check current code and acceptance evidence before describing UI or features as complete. Draft documents are not proof of implementation.


- 将程序拉入Sandboxie中的方法：
方法1：通过创建快捷方式的形式拉入沙盒中，例如创建pwsh的快捷方式
目标："C:\Program Files\Sandboxie-Plus\SandMan.exe" /box:DefaultBox "C:\ProgramData\Microsoft\Windows\Start Menu\Programs\PowerShell\PowerShell 7 (x64).lnk"
运行该快捷方式，则该应用自动被拉入沙盒中。

方法2："H:\Yuri368\环境变量配置V3\PowerShell 7 (x64).lnk"该程序已经被Sandboxie强制接管，使用它运行的程序都会被拉入虚拟环境中。
该pwsh使用模拟管理员身份运行(让程序自认为自己应用管理员权限)，且沙盒中的注册表修改操作只会修改沙盒中的注册表，不会对真实的系统环境产生影响
沙盒路径：C:\Sandbox\Yuri368\DefaultBox