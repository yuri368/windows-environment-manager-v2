# Current project boundary

- Current work is limited to the V3 browser demo and UI-only scope in docs/architecture/V3_MINIMAL_CONTRACT_V3_1.txt.
- Before changing behavior, consult the corresponding contract section; use docs/architecture/README.md for document priority.
- Do not modify main directly, access or modify the real Windows Registry, or migrate V2 code into V3.
- Check current code and acceptance evidence before describing UI or features as complete. Draft documents are not proof of implementation.


- [Sandboxie_DefaultBox] PowerShell 7 (x64) 为Sandboxie Plus的DefaultBox沙盒中的pwsh程序
目标："C:\Program Files\Sandboxie-Plus\SandMan.exe" /box:DefaultBox "C:\ProgramData\Microsoft\Windows\Start Menu\Programs\PowerShell\PowerShell 7 (x64).lnk"
该pwsh使用模拟管理员身份运行(让程序自认为自己应用管理员权限)，且沙盒中的注册表修改操作只会修改沙盒中的注册表，不会对真实的系统环境产生影响
沙盒路径：C:\Sandbox\Yuri368\DefaultBox