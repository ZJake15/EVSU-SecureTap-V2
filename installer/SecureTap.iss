; EVSU SecureTap installer - compiled by installer\build.py (Inno Setup 6).
;
; Installs for the current Windows user only, so it never asks for an
; administrator password, into %LOCALAPPDATA%\Programs\EVSU SecureTap. The
; program folder holds only the program: the data (people, faces, records,
; photos, settings) lives in %LOCALAPPDATA%\EVSU SecureTap (device_setup.py),
; so installing a newer version over an older one never touches it, and
; uninstalling asks before deleting it.

#ifndef AppVersion
  #define AppVersion "1.0"
#endif
#ifndef SourceDir
  #define SourceDir "build\SecureTap"
#endif
#define AppName "EVSU SecureTap"
#define DataFolder "{localappdata}\EVSU SecureTap"

[Setup]
AppId={{8C1B2F7E-5A3D-4E9B-9F21-6D4C7A0E5B13}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=EVSU SecureTap capstone team
DefaultDirName={localappdata}\Programs\EVSU SecureTap
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=EVSU-SecureTap-Setup
SetupIconFile=..\entry-agent\assets\icon.ico
UninstallDisplayIcon={app}\entry-agent\assets\icon.ico
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
; Python and its libraries are many small files - this keeps the progress bar
; honest instead of sitting still on the big ones.
LZMANumBlockThreads=4

[Tasks]
Name: "desktopicon"; Description: "Put an EVSU SecureTap icon on the desktop"

[InstallDelete]
; A newer version replaces the old program files completely, so nothing
; outdated is left behind (the data folder is elsewhere and untouched).
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\backend"
Type: filesandordirs; Name: "{app}\entry-agent"
Type: filesandordirs; Name: "{app}\dashboard"
Type: filesandordirs; Name: "{app}\face-models"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; pythonw.exe: no black console window behind the launcher.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\entry-agent\assets\icon.ico"; Comment: "Open EVSU SecureTap"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\entry-agent\assets\icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; \
  Description: "Open EVSU SecureTap now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Python writes compiled files next to the program while it runs.
Type: filesandordirs; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{#DataFolder}');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox('Also delete SecureTap''s data on this computer - the people, faces, entry records, accounts, '
        + 'photos and settings?' + #13#10#13#10
        + 'Choose No to keep it: installing SecureTap again brings everything back.' + #13#10
        + '(It''s in ' + DataDir + '.)', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
