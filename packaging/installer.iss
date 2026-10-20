; ShapeShifter installer (Inno Setup 6). Built by build.ps1, which passes:
;   /DAppVersion=2.3.0 /DSourceDir=<frozen app folder> /DLicensesDir=<notices folder>
;   /DRepoRoot=<project folder> /DIconFile=<.ico> /DOutputDir=<dist> [/DSIGN]
; Installs for the current user only (no administrator prompt), into
; %LOCALAPPDATA%\Programs\ShapeShifter, then lets ShapeShifter.exe register the right-click
; entry, Start Menu / startup shortcuts and file associations (ShapeShifter.exe install).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef Publisher
  #define Publisher "Sai Ethihas Chanda"
#endif
#ifndef SourceUrl
  #define SourceUrl "https://github.com/SaiEthihasChanda/shapeshifter"
#endif
#define AppName "ShapeShifter"
#define AppExe "ShapeShifter.exe"

[Setup]
; Never change AppId: it is how Windows recognises an upgrade of the same app.
AppId={{8D2E6C0F-5B8A-4C57-9E3B-2F6A7C1D4E90}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#Publisher}
AppPublisherURL={#SourceUrl}
AppSupportURL={#SourceUrl}/issues
AppUpdatesURL={#SourceUrl}/releases
AppCopyright=Copyright (C) 2026 {#Publisher}
DefaultDirName={localappdata}\Programs\{#AppName}
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile={#RepoRoot}\LICENSE
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename=ShapeShifter-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany={#Publisher}
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}
VersionInfoCopyright=Copyright (C) 2026 {#Publisher}. GNU AGPL v3 or later.
#ifdef SIGN
SignTool=ss
SignedUninstaller=yes
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "full"; Description: "Full installation (includes audio and video conversion)"
Name: "compact"; Description: "Compact installation (no audio/video conversion, about 200 MB smaller)"
Name: "custom"; Description: "Custom installation"; Flags: iscustom

[Components]
Name: "core"; Description: "ShapeShifter"; Types: full compact custom; Flags: fixed
Name: "ffmpeg"; Description: "Audio and video conversion (ffmpeg)"; Types: full

[Tasks]
Name: "startup"; Description: "Start ShapeShifter when I sign in to Windows (needed for the hotkeys)"; GroupDescription: "Options:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "ffmpeg\*"; Flags: recursesubdirs createallsubdirs ignoreversion; Components: core
Source: "{#SourceDir}\ffmpeg\*"; DestDir: "{app}\ffmpeg"; Flags: recursesubdirs createallsubdirs ignoreversion; Components: ffmpeg
Source: "{#RepoRoot}\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion; Components: core
Source: "{#LicensesDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; Components: core

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "{code:InstallArgs}"; StatusMsg: "Registering ShapeShifter with Windows..."; Flags: runhidden waituntilterminated
Filename: "{app}\{#AppExe}"; Parameters: "daemon"; Flags: nowait runhidden
Filename: "{app}\{#AppExe}"; Parameters: "settings"; Description: "Open ShapeShifter settings"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#AppExe}"; Parameters: "uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "UnregisterShapeShifter"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
function InstallArgs(Param: string): string;
begin
  Result := 'install';
  if not WizardIsTaskSelected('startup') then
    Result := Result + ' --no-startup';
end;

procedure StopShapeShifter;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopShapeShifter;
  { A clean slate for the bundled libraries so an upgrade never mixes old and new files. }
  if DirExists(ExpandConstant('{app}\_internal')) then
    DelTree(ExpandConstant('{app}\_internal'), True, True, True);
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopShapeShifter;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    { Silent uninstalls keep the user's data; otherwise ask. }
    if not UninstallSilent then
      if MsgBox('Also delete your ShapeShifter settings, hotkeys and learned conversions?',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(ExpandConstant('{userappdata}\ShapeShifter'), True, True, True);
        DelTree(ExpandConstant('{localappdata}\ShapeShifter'), True, True, True);
      end;
  end;
end;
