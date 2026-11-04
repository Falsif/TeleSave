; Установщик TeleSave (Inno Setup 6.3+)
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{CDBF83E1-C1DD-45FD-AFCF-3C659F14A323}
AppName=TeleSave
AppVersion={#AppVersion}
AppVerName=TeleSave {#AppVersion}
AppPublisher=LUNEXA
DefaultDirName={localappdata}\Programs\TeleSave
DefaultGroupName=TeleSave
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\output
OutputBaseFilename=TeleSave-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\TeleSave.exe
UninstallDisplayName=TeleSave
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\TeleSave\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\TeleSave"; Filename: "{app}\TeleSave.exe"
Name: "{autodesktop}\TeleSave"; Filename: "{app}\TeleSave.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TeleSave.exe"; Description: "{cm:LaunchProgram,TeleSave}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { убрать автозапуск; сам архив с перепиской НЕ удаляется }
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'TeleSave');
end;
