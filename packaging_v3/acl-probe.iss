; Opt-in native regression for the machine installer's Win32 string ABI.
; Compile with pinned ISCC, then run /VERYSILENT /SUPPRESSMSGBOXES /LOG=<path>.
; This only reads the source directory ACL and converts descriptors in memory.
; InitializeSetup deliberately returns False (exit 1); require the OK log marker
; and absence of FAILED/access violation. No installation or ACL writes occur.
[Setup]
AppName=V3 machine ACL read probe
AppVersion=1
DefaultDirName={tmp}\unused-v3-acl-probe
PrivilegesRequired=lowest
Uninstallable=no
CreateAppDir=no
OutputDir=.
OutputBaseFilename=acl-probe
[Code]
#include "machine-acl.iss"

procedure CheckDescriptor(Text: String);
var Canonical: String;
begin
  Canonical := CanonicalSDDL(Text);
  if (Canonical = '') or (Pos(#0, Canonical) <> 0) then
    RaiseException('Canonical descriptor contains an empty or NUL-padded value.');
  if CanonicalSDDL(Canonical) <> Canonical then
    RaiseException('Canonical descriptor roundtrip differs.');
end;

function InitializeSetup(): Boolean;
var Text: String;
begin
  Result := False;
  Log('V3_ACL_PROBE_BEGIN');
  try
    Text := SecurityText(ExpandConstant('{src}'));
    if (Text = '') or (Pos(#0, Text) <> 0) then
      RaiseException('Native ACL read contains an empty or NUL-padded value.');
    CheckDescriptor(Text);
    CheckDescriptor(ProgramSDDL);
    CheckDescriptor(ReceiptSDDL);
    Log('V3_ACL_PROBE_OK');
  except
    Log('V3_ACL_PROBE_FAILED ' + GetExceptionMessage);
  end;
end;
