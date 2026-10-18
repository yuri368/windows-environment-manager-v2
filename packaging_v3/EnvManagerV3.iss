; ISCC /DBundleDir=<absolute onedir> /DOutputDir=<absolute output> EnvManagerV3.iss
#ifndef BundleDir
  #error BundleDir is required. Build and smoke the onedir bundle first.
#endif
#ifndef OutputDir
  #define OutputDir SourcePath + "output"
#endif
[Setup]
#ifdef MachineInstall
AppId={{2F072551-FCF1-4455-88FC-3328B22A837C}
AppName=Environment Manager V3 (Machine)
DefaultDirName={commonpf64}\EnvManagerV3
DisableDirPage=yes
UsePreviousAppDir=no
PrivilegesRequired=admin
OutputBaseFilename=EnvManagerV3-3.0.0-dev1-setup-machine-x64
#else
AppId={{7263F5F6-5A39-40BB-B7CC-7DC45D8AE431}
AppName=Environment Manager V3
DefaultDirName={localappdata}\Programs\EnvManagerV3
PrivilegesRequired=lowest
OutputBaseFilename=EnvManagerV3-3.0.0-dev1-setup-user-x64
#endif
AppVersion=3.0.0-dev1
PrivilegesRequiredOverridesAllowed=
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\EnvManagerV3.exe
CloseApplications=no
RestartApplications=no
SetupLogging=yes
CreateUninstallRegKey=yes
; No Run entry: installation never starts the Windows backend.
; Normal per-user uninstall registration is enabled. No environment Registry,
; PATH, driver, service, scheduled task or user-data delete entries.
[Files]
; New runtime is available before any installed old file is replaced.
Source: "{#BundleDir}\*"; DestDir: "{tmp}\upgrade-runtime"; Flags: dontcopy noencryption recursesubdirs createallsubdirs
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; BeforeInstall: RequireUpgradeLease
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Icons]
#ifdef MachineInstall
Name: "{commonprograms}\Environment Manager V3"; Filename: "{app}\EnvManagerV3.exe"; WorkingDir: "{app}"
Name: "{commondesktop}\Environment Manager V3"; Filename: "{app}\EnvManagerV3.exe"; WorkingDir: "{app}"; Tasks: desktopicon
#else
Name: "{userprograms}\Environment Manager V3"; Filename: "{app}\EnvManagerV3.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\Environment Manager V3"; Filename: "{app}\EnvManagerV3.exe"; WorkingDir: "{app}"; Tasks: desktopicon
#endif
[Code]
#ifdef MachineInstall
#include "machine-acl.iss"
#include "machine-in-use.iss"
#endif
#include "upgrade-install.iss"
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  try
#ifdef MachineInstall
    BeginMachineUseCheck;
#endif
    BeginUpgrade;
#ifdef MachineInstall
    ProvisionMachineDirectories;
#endif
    RequireUpgradeLease;
  except
    Result := GetExceptionMessage;
    FinishUpgrade('cancelled', False);
  end;
end;
function InitializeUninstall(): Boolean;
begin
  Result := SuppressibleMsgBox('Quit all V3 instances before continuing. Uninstall removes program files only; configuration, history and recovery receipts in your local application data EnvManagerV3 folder will be retained.', mbConfirmation, MB_OKCANCEL, IDOK) = IDOK;
end;
