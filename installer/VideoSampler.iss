; Inno Setup script - compiled by build_exe.py (needs Inno Setup 6: winget install JRSoftware.InnoSetup)
; Installs per-user (no admin rights needed) from the PyInstaller folder in dist\VideoSampler.

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6E0B8F3A-2C47-4E8B-9C1D-5A7F3B2E9D41}
AppName=Video Sampler
AppVersion={#AppVersion}
AppPublisher=Video Sampler
AppPublisherURL=https://github.com/Stevovoness/videoSongSampler
DefaultDirName={localappdata}\Programs\VideoSampler
DefaultGroupName=Video Sampler
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=VideoSampler-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\VideoSampler.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\VideoSampler\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Video Sampler"; Filename: "{app}\VideoSampler.exe"
Name: "{group}\Uninstall Video Sampler"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Video Sampler"; Filename: "{app}\VideoSampler.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\VideoSampler.exe"; Description: "Start Video Sampler"; Flags: nowait postinstall skipifsilent
