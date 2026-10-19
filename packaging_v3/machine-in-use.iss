{ Read-only use detection in the FUTURE elevated machine Setup. No shutdown/restart APIs.
  Register the installed file set once; query it again at every replacement boundary. }
type
  TRmFilePointers = record
    Items: array[0..1023] of DWORD_PTR;
  end;
var
  MachineUseSession: DWORD;
  MachineUseSessionOpen: Boolean;
  MachineUseResourcesRegistered: Boolean;

function StartUseSession(var Session: DWORD; Flags: DWORD; Key: String): DWORD;
  external 'RmStartSession@rstrtmgr.dll stdcall';
function RegisterUseResources(Session, Count: DWORD; var Names: TRmFilePointers;
  ApplicationCount: DWORD; Applications: DWORD_PTR; ServiceCount: DWORD; Services: DWORD_PTR): DWORD;
  external 'RmRegisterResources@rstrtmgr.dll stdcall';
function QueryUseList(Session: DWORD; var Needed, Count: DWORD; Processes: DWORD_PTR; var RebootReasons: DWORD): DWORD;
  external 'RmGetList@rstrtmgr.dll stdcall';
function EndUseSession(Session: DWORD): DWORD;
  external 'RmEndSession@rstrtmgr.dll stdcall';
function UseAllocate(Flags: UINT; Bytes: DWORD_PTR): DWORD_PTR;
  external 'GlobalAlloc@kernel32.dll stdcall';
function UseFree(Memory: DWORD_PTR): DWORD_PTR;
  external 'GlobalFree@kernel32.dll stdcall';
function UseCopyText(Destination: DWORD_PTR; Source: String): DWORD_PTR;
  external 'lstrcpyW@kernel32.dll stdcall';

procedure CollectUseFiles(Path: String; var Names: TRmFilePointers; var Count: DWORD);
var
  FindRec: TFindRec;
  Child: String;
begin
  RequireNoReparse(Path);
  if FindFirst(AddBackslash(Path) + '*', FindRec) then begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then begin
          Child := AddBackslash(Path) + FindRec.Name;
          RequireNoReparse(Child);
          if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
            CollectUseFiles(Child, Names, Count)
          else begin
            if Count >= 1024 then RaiseException('Too many installed resources to check safely; no files replaced.');
            Names.Items[Count] := UseAllocate($40, (Length(Child) + 1) * 2);
            if Names.Items[Count] = 0 then RaiseException('Cannot allocate machine use-check buffer.');
            UseCopyText(Names.Items[Count], Child);
            Count := Count + 1;
          end;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

procedure RequireMachineUnused;
var
  Needed, Count, RebootReasons, Status: DWORD;
begin
  if not MachineUseSessionOpen then Exit;
  if not MachineUseResourcesRegistered then
    RaiseException('Machine use resources were not registered successfully; restart Setup.');
  Needed := 0;
  Count := 0;
  RebootReasons := 0;
  { A zero-capacity query is sufficient: any affected process is a refusal.
    No process-name filtering, current-user filtering or permission-error fallback. }
  Status := QueryUseList(MachineUseSession, Needed, Count, 0, RebootReasons);
  if (Status = 234) or (Needed <> 0) or (Count <> 0) or (RebootReasons <> 0) then
    RaiseException('Machine program files are in use, possibly by another signed-in user. Ask all users to exit V3 and retry. Setup will not close or kill their processes.');
  if Status <> 0 then
    RaiseException('Cannot establish that machine program files are unused (Restart Manager ' + IntToStr(Status) + '). No permission-error fallback is allowed.');
end;

procedure BeginMachineUseCheck;
var
  Names: TRmFilePointers;
  Count, Index, Status: DWORD;
  Key: String;
begin
  if MachineUseSessionOpen then begin
    RequireMachineUnused;
    Exit;
  end;
  { No old shared program files exist on a fresh install. }
  if not DirExists(ExpandConstant('{app}')) then Exit;
  Count := 0;
  try
    CollectUseFiles(ExpandConstant('{app}'), Names, Count);
    if Count = 0 then Exit;
    Key := StringOfChar(#0, 33);
    Status := StartUseSession(MachineUseSession, 0, Key);
    if Status <> 0 then RaiseException('Cannot start machine use check: ' + IntToStr(Status));
    MachineUseSessionOpen := True;
    { One batch registration includes EXE, Node, Python DLLs and other installed resources. }
    Status := RegisterUseResources(MachineUseSession, Count, Names, 0, 0, 0, 0);
    if Status <> 0 then RaiseException('Cannot register machine resources: ' + IntToStr(Status));
    MachineUseResourcesRegistered := True;
    RequireMachineUnused;
  finally
    if Count > 0 then
      for Index := 0 to Count - 1 do UseFree(Names.Items[Index]);
  end;
end;

procedure FinishMachineUseCheck;
begin
  if MachineUseSessionOpen then begin
    Log('Restart Manager session end: ' + IntToStr(EndUseSession(MachineUseSession)));
    MachineUseSessionOpen := False;
    MachineUseResourcesRegistered := False;
  end;
end;
