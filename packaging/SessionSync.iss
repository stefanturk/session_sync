; Inno Setup script for the Windows installer.
;
; Built by scripts/build_windows.ps1 after PyInstaller has made dist\SessionSync.
; Installs for the current user only (no administrator prompt), into
; %LOCALAPPDATA%\Programs\SessionSync, and registers sessionsync: links so the
; "▶ Open in Live (Windows)" file in a shared song folder opens the app.

#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{6A1C3B5E-2F4D-4E8B-9C1A-5D7E3F2B8A41}
AppName=SessionSync
AppVersion={#AppVersion}
AppPublisher=SessionSync
DefaultDirName={localappdata}\Programs\SessionSync
DefaultGroupName=SessionSync
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=SessionSync-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\SessionSync.exe
CloseApplications=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Put SessionSync on the Desktop"; Flags: unchecked

[Files]
Source: "..\dist\SessionSync\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\SessionSync"; Filename: "{app}\SessionSync.exe"
Name: "{userdesktop}\SessionSync"; Filename: "{app}\SessionSync.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\sessionsync"; ValueType: string; ValueName: ""; ValueData: "URL:Session Sync"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\sessionsync"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\sessionsync\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\SessionSync.exe"",0"
Root: HKCU; Subkey: "Software\Classes\sessionsync\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\SessionSync.exe"" ""%1"""

[Run]
Filename: "{app}\SessionSync.exe"; Description: "Open SessionSync"; Flags: nowait postinstall skipifsilent
