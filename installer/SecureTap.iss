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
; Wide enough for the terms and privacy policy's 76-character lines.
WizardSizePercent=130
; The terms of service (a page of our own - see [Code]) and then the privacy
; policy are the installer's first pages, and both have to be accepted
; before anything is installed (wording below, under [Messages]).
LicenseFile=PRIVACY-POLICY.txt
Compression=lzma2
SolidCompression=yes
; Python and its libraries are many small files - this keeps the progress bar
; honest instead of sitting still on the big ones.
LZMANumBlockThreads=4

[Messages]
WizardLicense=Privacy Policy
LicenseLabel=Please read how EVSU SecureTap handles personal information.
LicenseLabel3=EVSU SecureTap collects face photos, ID numbers and entry records. Please read this privacy policy - you need to accept it before installing.
LicenseAccepted=I have read and &accept the privacy policy
LicenseNotAccepted=I &do not accept

[Tasks]
; Two ways in, for the two kinds of people at this computer. Both are ticked
; by default; a computer used only at a gate would keep just the Gate icon.
Name: "adminicon"; Description: "EVSU SecureTap Admin - dashboard, settings, backups and setup (Admin, SASO)"; \
  GroupDescription: "Desktop icons:"
Name: "gateicon"; Description: "EVSU SecureTap Gate - opens the gate monitor for the guard on duty"; \
  GroupDescription: "Desktop icons:"

[InstallDelete]
; A newer version replaces the old program files completely, so nothing
; outdated is left behind (the data folder is elsewhere and untouched).
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\backend"
Type: filesandordirs; Name: "{app}\entry-agent"
Type: filesandordirs; Name: "{app}\dashboard"
Type: filesandordirs; Name: "{app}\face-models"
; The single "EVSU SecureTap" icon older versions made - replaced by the
; Admin and Gate icons below.
Type: files; Name: "{autodesktop}\{#AppName}.lnk"
Type: files; Name: "{autoprograms}\{#AppName}.lnk"

[Files]
; Read by the terms and privacy pages before installing (InitializeWizard's
; ExtractTemporaryFile) - first, and in their own compressed block
; (solidbreak below), so the pages don't wait for the whole program to be
; unpacked.
Source: "TERMS-OF-SERVICE.txt"; Flags: dontcopy
Source: "PRIVACY-POLICY.txt"; Flags: dontcopy
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs solidbreak
Source: "PRIVACY-POLICY.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "TERMS-OF-SERVICE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; pythonw.exe: no black console window behind the launcher. Admin = the
; launcher (dashboard, gate monitor, settings, backups, setup); Gate =
; launcher.py --gate, straight to the gate monitor with nothing else.
Name: "{autoprograms}\{#AppName} Admin"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\entry-agent\assets\icon.ico"; \
  Comment: "Dashboard, settings, backups and setup - for the Admin and SASO"
Name: "{autoprograms}\{#AppName} Gate"; Filename: "{app}\python\pythonw.exe"; \
  Parameters: """{app}\launcher.py"" --gate"; WorkingDir: "{app}"; IconFilename: "{app}\entry-agent\assets\icon-gate.ico"; \
  Comment: "The gate monitor, for the guard on duty"
Name: "{autodesktop}\{#AppName} Admin"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; \
  WorkingDir: "{app}"; IconFilename: "{app}\entry-agent\assets\icon.ico"; Tasks: adminicon; \
  Comment: "Dashboard, settings, backups and setup - for the Admin and SASO"
Name: "{autodesktop}\{#AppName} Gate"; Filename: "{app}\python\pythonw.exe"; \
  Parameters: """{app}\launcher.py"" --gate"; WorkingDir: "{app}"; IconFilename: "{app}\entry-agent\assets\icon-gate.ico"; \
  Tasks: gateicon; Comment: "The gate monitor, for the guard on duty"
Name: "{autoprograms}\{#AppName} Privacy Policy"; Filename: "{app}\PRIVACY-POLICY.txt"; \
  Comment: "How EVSU SecureTap handles personal information"
Name: "{autoprograms}\{#AppName} Terms of Service"; Filename: "{app}\TERMS-OF-SERVICE.txt"; \
  Comment: "The rules for using EVSU SecureTap"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; \
  Description: "Open EVSU SecureTap Admin now (to finish setting it up)"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Python writes compiled files next to the program while it runs.
Type: filesandordirs; Name: "{app}"

[Code]
var
  TermsPage: TOutputMsgMemoWizardPage;
  TermsAcceptRadio, TermsDeclineRadio: TNewRadioButton;

procedure TermsRadioClick(Sender: TObject);
begin
  WizardForm.NextButton.Enabled := TermsAcceptRadio.Checked;
end;

function NewTermsRadio(Caption: String; Top: Integer): TNewRadioButton;
begin
  Result := TNewRadioButton.Create(TermsPage);
  Result.Parent := TermsPage.Surface;
  Result.Caption := Caption;
  Result.Left := 0;
  Result.Width := TermsPage.SurfaceWidth;
  Result.Height := WizardForm.LicenseAcceptedRadio.Height;
  Result.Top := Top;
  Result.Anchors := [akLeft, akRight, akBottom];
  Result.OnClick := @TermsRadioClick;
end;

// The terms of service, as an accept-or-decline page like the privacy
// policy's (Inno Setup has only one built-in license page).
procedure InitializeWizard;
var
  Terms, Privacy: AnsiString;
begin
  ExtractTemporaryFile('TERMS-OF-SERVICE.txt');
  LoadStringFromFile(ExpandConstant('{tmp}\TERMS-OF-SERVICE.txt'), Terms);
  ExtractTemporaryFile('PRIVACY-POLICY.txt');
  LoadStringFromFile(ExpandConstant('{tmp}\PRIVACY-POLICY.txt'), Privacy);
  TermsPage := CreateOutputMsgMemoPage(wpWelcome, 'Terms of Service',
    'Please read the rules for using EVSU SecureTap.',
    'These terms explain who may use EVSU SecureTap, what it may and may not be used for, and your duties. '
    + 'You need to accept them before installing.', Terms);
  TermsDeclineRadio := NewTermsRadio('I &do not accept',
    TermsPage.SurfaceHeight - WizardForm.LicenseAcceptedRadio.Height);
  TermsAcceptRadio := NewTermsRadio('I have read and &accept the terms of service',
    TermsDeclineRadio.Top - WizardForm.LicenseAcceptedRadio.Height - ScaleY(4));
  // A silent install (/SILENT, /VERYSILENT - run by whoever deploys it)
  // still clicks Next through every page, so it starts accepted - as Inno
  // Setup does with the privacy policy's page.
  if WizardSilent then
    TermsAcceptRadio.Checked := True
  else
    TermsDeclineRadio.Checked := True;
  TermsPage.RichEditViewer.Height := TermsAcceptRadio.Top - TermsPage.RichEditViewer.Top - ScaleY(8);
  // Fixed-width, so the documents' lined-up columns stay lined up. The text
  // is put back after the font changes, or it opens scrolled partway down.
  TermsPage.RichEditViewer.Font.Name := 'Consolas';
  TermsPage.RichEditViewer.RTFText := Terms;
  WizardForm.LicenseMemo.Font.Name := 'Consolas';
  WizardForm.LicenseMemo.RTFText := Privacy;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = TermsPage.ID then
    WizardForm.NextButton.Enabled := TermsAcceptRadio.Checked;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := (CurPageID <> TermsPage.ID) or TermsAcceptRadio.Checked;
end;

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
