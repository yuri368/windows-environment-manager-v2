#ifndef AppVersion
  #define AppVersion "2.0.1"
#endif
#ifndef BundleDir
  #error BundleDir must point to the standalone application directory
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef SourceDir
  #error SourceDir is required
#endif

[Setup]
AppId={{CF5C80D6-79CB-4B2E-9D67-C2C18DC704B9}
AppName=环境变量方案管理器
AppVersion={#AppVersion}
AppPublisher=Environment Manager V2 Contributors
DefaultDirName={localappdata}\Programs\EnvironmentManagerV2
DefaultGroupName=环境变量方案管理器
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=EnvironmentManagerV2-{#AppVersion}-windows-x64-setup
SetupIconFile={#BundleDir}\app.ico
UninstallDisplayIcon={app}\EnvironmentManagerV2.exe
LicenseFile={#SourceDir}\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UsePreviousAppDir=no
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "{#SourceDir}\packaging\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\环境变量方案管理器"; Filename: "{app}\EnvironmentManagerV2.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\环境变量方案管理器"; Filename: "{app}\EnvironmentManagerV2.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\EnvironmentManagerV2.exe"; Description: "立即启动环境变量方案管理器"; Flags: nowait postinstall skipifsilent runasoriginaluser
