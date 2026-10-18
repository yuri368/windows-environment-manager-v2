{ New-bundle offline helper. Never run an installed old EXE or the Windows backend. }
const
  UpgradeVersion = '3.0.0.dev1';
  UpgradeWaitMS = 120000;
var
  UpgradeExe, UpgradeSessionDir: String;
  UpgradeLaunched, UpgradeReady, UpgradeFinished: Boolean;

function UpgradeTick: Int64;
  external 'GetTickCount64@kernel32.dll stdcall';

function UpgradeInstallerPID: LongWord;
  external 'GetCurrentProcessId@kernel32.dll stdcall';

procedure CheckUpgradeIdentity;
var
  Executed: Boolean;
  ExitCode: Integer;
begin
  { Synchronous and before data/session/backup preparation. Never infer success
    from ewNoWait or from the absence of a terminal mailbox. }
  ExitCode := -1;
  Executed := ExecAsOriginalUser(UpgradeExe, '--upgrade-helper check-user',
    ExtractFileDir(UpgradeExe), SW_HIDE, ewWaitUntilTerminated, ExitCode);
  if Executed then
    Log('Upgrade identity preflight process exited with code ' + IntToStr(ExitCode))
  else
    Log('Upgrade identity preflight could not launch; OS error ' + IntToStr(ExitCode));
  if not Executed then
    RaiseException('Could not execute upgrade identity preflight: ' + IntToStr(ExitCode) + ' ' + SysErrorMessage(ExitCode));
  if ExitCode <> 0 then
    RaiseException('Upgrade identity preflight failed (exit ' + IntToStr(ExitCode) + '). The new helper requires the original unelevated user (ORIGINAL_USER_REQUIRED). Start Setup from that user without RunAsAdmin or simulated administrator rights. No data backup or program replacement has started. See the Setup log; a launch/runtime error is also a failure, never permission to continue.');
end;

function RunUpgradeControl(Action, Outcome: String): Boolean;
var
  Params: String;
  ExitCode: Integer;
begin
  Params := '--upgrade-helper ' + Action + ' --session-dir ' + AddQuotes(UpgradeSessionDir) + ' --timeout 10';
  if Outcome <> '' then Params := Params + ' --outcome ' + Outcome;
  Result := Exec(UpgradeExe, Params, ExtractFileDir(UpgradeExe), SW_HIDE, ewWaitUntilTerminated, ExitCode);
  Result := Result and (ExitCode = 0);
end;

procedure FinishUpgrade(Outcome: String; RequireSuccess: Boolean);
begin
  if UpgradeLaunched and not UpgradeFinished then begin
    { Also cancels preparation before ready.json exists. The holder checks this marker. }
    if not FileExists(UpgradeSessionDir + '\ready.json') then begin
      { Session is exclusively created by the helper. Never create it in Setup,
        which would make the helper's exclusive mkdir fail. }
      if not DirExists(UpgradeSessionDir) then
        Log('Upgrade session was never created; no cancellation mailbox is available. Parent exit and preparation deadline bound any holder.')
      else if not SaveStringToFile(UpgradeSessionDir + '\cancel.json', '{}', False) then
        Log('Could not write preparation cancellation marker; parent lifetime/deadline still bound the holder.');
    end;
    if FileExists(UpgradeSessionDir + '\ready.json') then begin
      if not RunUpgradeControl('finish', Outcome) then begin
        Log('Upgrade helper finish failed. Backup/session evidence retained at ' + UpgradeSessionDir);
        if RequireSuccess then
          RaiseException('Program files were deployed but the upgrade lease could not finish. Keep the backup and inspect the installation log. Session: ' + UpgradeSessionDir);
      end else
        UpgradeFinished := True;
    end;
  end;
end;

procedure RequireUpgradeLease;
begin
#ifdef MachineInstall
  RequireMachineUnused;
#endif
  { Called before every installed runtime file, and after all files/ACLs. }
  if not UpgradeReady or UpgradeFinished or not RunUpgradeControl('status', '') then
    RaiseException('No live upgrade lock owner acknowledgement. File replacement is stopped. Backup evidence: ' + UpgradeSessionDir);
end;

procedure BeginUpgrade;
var
  Started: Int64;
  Params, Failure: String;
  ExitCode: Integer;
  TerminalText: AnsiString;
begin
  if UpgradeReady and not UpgradeFinished then begin
    RequireUpgradeLease;
    Exit;
  end;
  if UpgradeLaunched then
    RaiseException('This installer session cannot reuse a failed upgrade lease. Restart Setup after reviewing the backup evidence.');
  ExtractTemporaryFiles('{tmp}\upgrade-runtime\*');
  UpgradeExe := ExpandConstant('{tmp}\upgrade-runtime\EnvManagerV3.exe');
#ifdef MachineInstall
  PrepareUpgradeScratch;
#endif
  CheckUpgradeIdentity;
  UpgradeSessionDir := ExpandConstant('{tmp}\upgrade-control\session');
  if not ForceDirectories(ExtractFileDir(UpgradeSessionDir)) then
    RaiseException('Cannot create upgrade session parent.');
  Params := '--upgrade-helper hold --require-unelevated --target-version ' + UpgradeVersion +
            ' --session-dir ' + AddQuotes(UpgradeSessionDir) + ' --lease-seconds 7200' +
            ' --parent-pid ' + IntToStr(UpgradeInstallerPID) + ' --prepare-timeout 120';
  if ExpandConstant('{param:DataDir|}') <> '' then
    Params := Params + ' --data-dir ' + AddQuotes(ExpandConstant('{param:DataDir|}'));
  { No --data-dir derived by elevated Setup. The original-user helper resolves its own Known Folder. }
  if not ExecAsOriginalUser(UpgradeExe, Params, ExtractFileDir(UpgradeExe), SW_HIDE, ewNoWait, ExitCode) then
    RaiseException('Cannot start the new upgrade helper as the original user. Restart Setup unelevated; no administrator-profile fallback is permitted.');
  UpgradeLaunched := True;
  Log('New-bundle upgrade helper launch accepted; waiting for live ready/status acknowledgement. Control evidence: ' + UpgradeSessionDir);
  Started := UpgradeTick;
  while not FileExists(UpgradeSessionDir + '\ready.json') do begin
    if FileExists(UpgradeSessionDir + '\terminal.json') then begin
      Failure := 'Upgrade backup/lock preparation was blocked.';
      if LoadStringFromFile(UpgradeSessionDir + '\terminal.json', TerminalText) then
        Failure := Failure + ' ' + String(TerminalText);
      RaiseException(Failure);
    end;
    if UpgradeTick - Started > UpgradeWaitMS then
      RaiseException('Upgrade helper did not become ready within 120 seconds. No program files replaced; preparation is cancelled.');
    Sleep(100);
  end;
  { ready.json only locates the mailbox. Fresh CLI nonce/response, not file presence, authorizes replacement. }
  if not RunUpgradeControl('status', '') then
    RaiseException('Upgrade ready evidence has no live lock-owner acknowledgement. No program files replaced.');
  UpgradeReady := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then RequireUpgradeLease;
  if CurStep = ssPostInstall then begin
#ifdef MachineInstall
    ProtectProgramTree(ExpandConstant('{app}'));
#endif
    RequireUpgradeLease;
    FinishUpgrade('installed', True);
  end;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if UpgradeLaunched and not UpgradeFinished then FinishUpgrade('cancelled', False);
end;

procedure DeinitializeSetup;
begin
  { Includes preparation/install failures. No automatic restore, replay or environment write. }
  FinishUpgrade('cancelled', False);
#ifdef MachineInstall
  FinishMachineUseCheck;
#endif
end;
