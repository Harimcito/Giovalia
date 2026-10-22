; Gioval — instalador por usuario (Windows x64)
#define MyAppName "Gioval"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Gioval"
#define MyAppExeName "Gioval.exe"

[Setup]
AppId={{C9B7A7E1-2B86-49C5-90FD-AE1C1958F722}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Gioval
DefaultGroupName={#MyAppName}
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir=Output
OutputBaseFilename=Gioval-Setup
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
ChangesAssociations=no
DisableProgramGroupPage=yes

[Dirs]
; Los datos personales viven fuera de la carpeta del programa y no se eliminan al desinstalar.
Name: "{localappdata}\Gioval"; Flags: uninsneveruninstall

[Files]
Source: "..\dist\Gioval\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "webview2\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "..\configurar_cliente_gioval.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Gioval"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{localappdata}\Gioval"
Name: "{autodesktop}\Gioval"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{localappdata}\Gioval"
Name: "{autoprograms}\Configurar conexión de Gioval"; Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\configurar_cliente_gioval.ps1"""; WorkingDir: "{localappdata}\Gioval"

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Preparando el componente visual de Windows..."; Flags: waituntilterminated; Check: not IsWebView2Installed
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir Gioval"; WorkingDir: "{localappdata}\Gioval"; Flags: nowait postinstall skipifsilent

[Code]
function WebView2InstalledAt(RootKey: Integer; SubKeyName: String): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(RootKey, SubKeyName, 'pv', Version)
    and (Version <> '')
    and (Version <> '0.0.0.0');
end;

function IsWebView2Installed: Boolean;
begin
  Result :=
    WebView2InstalledAt(HKLM32, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')
    or WebView2InstalledAt(HKLM64, 'Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}')
    or WebView2InstalledAt(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}');
end;
