; Built natively on Windows from reviewed sources and verified runtime only.
#ifndef AppVersion
  #error AppVersion must be supplied
#endif
#ifndef PayloadDir
  #error PayloadDir must be supplied
#endif
#ifndef OutputDir
  #error OutputDir must be supplied
#endif

[Setup]
AppId={{C460558F-C75D-41B2-8C38-DA296201AAC7}
AppName=Agy Console
AppVersion={#AppVersion}
AppPublisher=Agy Console contributors
AppPublisherURL=https://github.com/dneo9527-ctrl/agy-console
AppSupportURL=https://github.com/dneo9527-ctrl/agy-console/issues
DefaultDirName={localappdata}\Programs\AgyConsole
DefaultGroupName=Agy Console
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
DisableProgramGroupPage=yes
AllowNoIcons=no
UninstallDisplayIcon={app}\runtime\python.exe
OutputDir={#OutputDir}
OutputBaseFilename=AgyConsole-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\..\LICENSE
CloseApplications=no
RestartApplications=no
AppMutex=Local\AgyConsole.Foreground.v1
SetupMutex=Local\AgyConsole.Setup.v1
ChangesAssociations=no
ChangesEnvironment=no
AlwaysRestart=no
DirExistsWarning=no
UsePreviousAppDir=yes

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "installer\*,data\*"
Source: "installed-mode.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userdesktop}\Agy Console"; Filename: "{app}\Start-Agy.cmd"; WorkingDir: "{app}"; Comment: "Start Agy Console; keep its terminal open"
Name: "{group}\Agy Console"; Filename: "{app}\Start-Agy.cmd"; WorkingDir: "{app}"
Name: "{group}\Official CLI Installation"; Filename: "{app}\Install-Official-CLI.url"; WorkingDir: "{app}"
Name: "{group}\Official CLI Login"; Filename: "{app}\Login-Agy.cmd"; WorkingDir: "{app}"
Name: "{group}\Open Panel"; Filename: "{app}\Open-Panel.url"; WorkingDir: "{app}"
Name: "{group}\Getting Started"; Filename: "{app}\Getting-Started.html"; WorkingDir: "{app}"
Name: "{group}\Uninstall Agy Console"; Filename: "{uninstallexe}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\Getting-Started.html"; Description: "Open the first-use guide (official CLI installation and login)"; Flags: postinstall shellexec skipifsilent

[Code]
var
  UninstallGuard: NativeInt;

function WinCreateMutex(SecurityAttributes: NativeInt; InitialOwner: Boolean; const Name: String): NativeInt;
  external 'CreateMutexW@kernel32.dll stdcall';
function WinCloseHandle(Handle: NativeInt): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function InitializeSetup(): Boolean;
begin
  Result := not CheckForMutexes('Local\AgyConsole.Foreground.v1');
  if not Result then
    SuppressibleMsgBox('Agy Console is running. Finish tasks and close its foreground window, then run Setup again. No process was stopped.', mbError, MB_OK, IDOK);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if CheckForMutexes('Local\AgyConsole.Foreground.v1') then
    Result := 'Agy Console is running. Close its foreground window before installing. No process was stopped.'
  else if (FileExists(ExpandConstant('{app}\Start-Agy.cmd')) or DirExists(ExpandConstant('{app}\data'))) and
          not FileExists(ExpandConstant('{app}\installed-mode.json')) then
    Result := 'This is a portable or unrecognized installation. Choose a separate empty program directory. Existing data will not be moved or overwritten.';
end;

function InitializeUninstall(): Boolean;
var
  GuardError: LongInt;
begin
  // SetupMutex is not created automatically by Uninstall. Hold the same named
  // object before checking the application, preventing a new launch mid-delete.
  UninstallGuard := WinCreateMutex(0, False, 'Local\AgyConsole.Setup.v1');
  GuardError := DLLGetLastError;
  Result := (UninstallGuard <> 0) and (GuardError <> 183);
  if not Result then begin
    if UninstallGuard <> 0 then WinCloseHandle(UninstallGuard);
    UninstallGuard := 0;
    SuppressibleMsgBox('Another installation or uninstall is active. Finish it first. No process was stopped.', mbError, MB_OK, IDOK);
    Exit;
  end;
  Result := not CheckForMutexes('Local\AgyConsole.Foreground.v1');
  if not Result then begin
    WinCloseHandle(UninstallGuard);
    UninstallGuard := 0;
    SuppressibleMsgBox('Agy Console is running. Finish tasks and close its foreground window before uninstalling. Personal data and official login are retained.', mbError, MB_OK, IDOK);
  end;
end;

procedure DeinitializeUninstall();
begin
  if UninstallGuard <> 0 then WinCloseHandle(UninstallGuard);
  UninstallGuard := 0;
end;

// Intentionally no UninstallDelete/InstallDelete entries. User data is external.
