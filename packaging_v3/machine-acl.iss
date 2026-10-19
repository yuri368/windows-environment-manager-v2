{ Included inside [Code] only by the machine installer.
  No environment adapter, backend execution, shell command or Registry value writes. }
const
  InvalidAttributes = $FFFFFFFF;
  ReparseAttribute = $400;
  SecurityOwnerDacl = $5;
  ProtectedOwnerDacl = $80000005;
  ProgramSDDL = 'O:BAD:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;OICI;0x1200a9;;;AU)';
  ReceiptSDDL = 'O:BAD:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;OICI;0x120089;;;AU)';
type
  TSecurityAttributes = record
    Length: DWORD;
    Descriptor: DWORD_PTR;
    InheritHandle: BOOL;
  end;

function FileAttributes(Path: String): DWORD;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function StringToSD(Text: String; Revision: DWORD; var Descriptor: DWORD_PTR; Size: DWORD_PTR): BOOL;
  external 'ConvertStringSecurityDescriptorToSecurityDescriptorW@advapi32.dll stdcall';
function SDToString(Descriptor: DWORD_PTR; Revision: DWORD; Info: DWORD; var Text: DWORD_PTR; var Length: DWORD): BOOL;
  external 'ConvertSecurityDescriptorToStringSecurityDescriptorW@advapi32.dll stdcall';
function NamedSecurity(Path: String; Kind, Info: DWORD; Owner, Group, Dacl, Sacl: DWORD_PTR; var Descriptor: DWORD_PTR): DWORD;
  external 'GetNamedSecurityInfoW@advapi32.dll stdcall';
function ProtectFile(Path: String; Info: DWORD; Descriptor: DWORD_PTR): BOOL;
  external 'SetFileSecurityW@advapi32.dll stdcall';
function CreateSecureDirectory(Path: String; var Attributes: TSecurityAttributes): BOOL;
  external 'CreateDirectoryW@kernel32.dll stdcall';
function FreeLocal(PointerValue: DWORD_PTR): DWORD_PTR;
  external 'LocalFree@kernel32.dll stdcall';
{ LPWSTR is the string buffer itself, not a pointer to the Pascal string slot. }
function CopyText(Dest: String; Source: DWORD_PTR; Count: Integer): DWORD_PTR;
  external 'lstrcpynW@kernel32.dll stdcall';

procedure RequireNoReparse(Path: String);
var
  Current, Parent: String;
  Attrs: DWORD;
begin
  Current := RemoveBackslashUnlessRoot(Path);
  while Current <> '' do begin
    Attrs := FileAttributes(Current);
    if (Attrs <> InvalidAttributes) and ((Attrs and ReparseAttribute) <> 0) then
      RaiseException('Refusing junction/symlink in protected install path: ' + Current);
    Parent := RemoveBackslashUnlessRoot(ExtractFileDir(Current));
    if Parent = Current then Break;
    Current := Parent;
  end;
end;

function SecurityText(Path: String): String;
var
  SD, Text: DWORD_PTR;
  Length: DWORD;
  Terminator: Integer;
begin
  SD := 0;
  Text := 0;
  if NamedSecurity(Path, 1, SecurityOwnerDacl, 0, 0, 0, 0, SD) <> 0 then
    RaiseException('Cannot inspect protected directory ACL: ' + Path);
  try
    if not SDToString(SD, 1, SecurityOwnerDacl, Text, Length) then
      RaiseException('Cannot serialize protected directory ACL: ' + Path);
    try
      Result := StringOfChar(#0, Length);
      if CopyText(Result, Text, Length) = 0 then RaiseException('Cannot copy security descriptor text.');
      Terminator := Pos(#0, Result);
      if Terminator = 0 then RaiseException('Security descriptor text is not null-terminated.');
      SetLength(Result, Terminator - 1);
    finally
      FreeLocal(Text);
    end;
  finally
    FreeLocal(SD);
  end;
end;

function CanonicalSDDL(Text: String): String;
var
  SD, Canonical: DWORD_PTR;
  Length: DWORD;
  Terminator: Integer;
begin
  SD := 0;
  if not StringToSD(Text, 1, SD, 0) then RaiseException('Invalid installer security descriptor.');
  try
    Canonical := 0;
    if not SDToString(SD, 1, SecurityOwnerDacl, Canonical, Length) then
      RaiseException('Cannot canonicalize installer security descriptor.');
    try
      Result := StringOfChar(#0, Length);
      if CopyText(Result, Canonical, Length) = 0 then RaiseException('Cannot copy canonical security text.');
      Terminator := Pos(#0, Result);
      if Terminator = 0 then RaiseException('Canonical security text is not null-terminated.');
      SetLength(Result, Terminator - 1);
    finally
      FreeLocal(Canonical);
    end;
  finally
    FreeLocal(SD);
  end;
end;

procedure ApplyProtection(Path, SDDL: String);
var
  SD: DWORD_PTR;
begin
  RequireNoReparse(Path);
  SD := 0;
  if not StringToSD(SDDL, 1, SD, 0) then RaiseException('Cannot create protected ACL.');
  try
    if not ProtectFile(Path, ProtectedOwnerDacl, SD) then
      RaiseException('Cannot protect owner/ACL of installed path: ' + Path);
  finally
    FreeLocal(SD);
  end;
  if SecurityText(Path) <> CanonicalSDDL(SDDL) then
    RaiseException('Protected path ACL readback differs: ' + Path);
end;

procedure EnsureProtectedDirectory(Path, SDDL: String; AllowExisting: Boolean);
var
  SA: TSecurityAttributes;
begin
  RequireNoReparse(Path);
  SA.Length := SizeOf(SA);
  SA.Descriptor := 0;
  SA.InheritHandle := False;
  if not StringToSD(SDDL, 1, SA.Descriptor, 0) then RaiseException('Cannot create protected directory ACL.');
  try
    if not CreateSecureDirectory(Path, SA) then begin
      if not AllowExisting or not DirExists(Path) then
        RaiseException('Protected install directory already exists or cannot be created: ' + Path);
      { Never take over or repair an existing weak receipt directory. }
      if SecurityText(Path) <> CanonicalSDDL(SDDL) then
        RaiseException('Existing receipt directory is not administrator-protected; no files changed: ' + Path);
    end;
  finally
    FreeLocal(SA.Descriptor);
  end;
  if SecurityText(Path) <> CanonicalSDDL(SDDL) then
    RaiseException('New protected directory ACL readback differs: ' + Path);
end;

procedure ProvisionMachineDirectories;
var
  ProgramRoot, ReceiptProduct: String;
begin
  ProgramRoot := ExpandConstant('{commonpf64}\EnvManagerV3');
  if CompareText(ExpandConstant('{app}'), ProgramRoot) <> 0 then
    RaiseException('Machine installation must use the fixed Program Files directory.');
  RequireNoReparse(ProgramRoot);
  ReceiptProduct := ExpandConstant('{commonappdata}\EnvManagerV3');
  EnsureProtectedDirectory(ReceiptProduct, ReceiptSDDL, True);
  EnsureProtectedDirectory(ReceiptProduct + '\system-receipts', ReceiptSDDL, True);
  { Only our product directory is modified, never Program Files or ProgramData ACLs. }
  EnsureProtectedDirectory(ProgramRoot, ProgramSDDL, True);
end;

procedure ProtectProgramTree(Path: String);
var
  FindRec: TFindRec;
  Child: String;
begin
  ApplyProtection(Path, ProgramSDDL);
  if FindFirst(AddBackslash(Path) + '*', FindRec) then begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then begin
          Child := AddBackslash(Path) + FindRec.Name;
          RequireNoReparse(Child);
          if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
            ProtectProgramTree(Child)
          else
            ApplyProtection(Child, ProgramSDDL);
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

procedure PrepareUpgradeScratch;
var
  TempRoot, RuntimeRoot, ControlRoot: String;
begin
  TempRoot := ExpandConstant('{tmp}');
  RuntimeRoot := TempRoot + '\upgrade-runtime';
  ControlRoot := TempRoot + '\upgrade-control';
  { Original user must be able to traverse the private temporary root, but not replace runtime. }
  ApplyProtection(TempRoot, ProgramSDDL);
  ProtectProgramTree(RuntimeRoot);
  EnsureProtectedDirectory(ControlRoot,
    'O:BAD:P(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;;0x1200ad;;;AU)(A;OICIIO;0x1200a9;;;AU)(A;OICIIO;FA;;;CO)', False);
end;
