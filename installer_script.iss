#define MyAppName "GlyphSnap"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "Ali Alnazer Ahmed"
#define MyAppURL "https://github.com/pqun7/glyphsnap"
#define MyAppExeName "GlyphSnap.exe"
#define MyOutputDir GetEnv("TEMP") + "\GlyphSnap-Inno"

; Prefer the production build path, with compatibility for older builds.
#if DirExists("builds\nuitka\glyphsnap.dist")
  #define MySourceFolder "builds\nuitka\glyphsnap.dist"
#elif DirExists("dist-nuitka\glyphsnap.dist")
  #define MySourceFolder "dist-nuitka\glyphsnap.dist"
#else
  #error "GlyphSnap build not found. Run .\build_exe.ps1 before compiling the installer."
#endif

; Application icon
#define MyIconFile "assets\app.ico"

[Setup]
AppId={{D3F9A401-28B3-4A59-8392-123456789ABC}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
VersionInfoVersion={#MyAppVersion}

DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}

DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; Compile in TEMP to avoid antivirus/file-indexer locks inside the repository.
; build_installer.ps1 copies the verified result to builds\installer.
OutputDir={#MyOutputDir}
OutputBaseFilename=GlyphSnap_Setup

Compression=lzma2/ultra64
SolidCompression=yes

ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

WizardStyle=modern
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
MinVersion=10.0

; Installer icon
SetupIconFile={#MyIconFile}

; Uninstaller
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
Uninstallable=yes

; Windows version information
VersionInfoDescription={#MyAppName}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoCopyright=Copyright (C) 2026 {#MyAppPublisher}

; Prevent multiple installer instances
AppMutex=GlyphSnapInstaller

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; \
    Description: "إنشاء اختصار على سطح المكتب"; \
    GroupDescription: "اختصارات إضافية:"; \
    Flags: unchecked

[Files]
; Copy complete Nuitka standalone directory
Source: "{#MySourceFolder}\*"; \
    DestDir: "{app}"; \
    Excludes: "__pycache__\*,*.pyc,pymupdf\mupdf-devel\*"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu shortcut
Name: "{group}\{#MyAppName}"; \
    Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{#MyIconFile}"

; Desktop shortcut
Name: "{autodesktop}\{#MyAppName}"; \
    Filename: "{app}\{#MyAppExeName}"; \
    IconFilename: "{#MyIconFile}"; \
    Tasks: desktopicon

[UninstallDelete]
Type: filesandordirs; Name: "{app}\__pycache__"

[Run]
Filename: "{app}\{#MyAppExeName}"; \
    Description: "تشغيل التطبيق الآن"; \
    Flags: nowait postinstall skipifsilent

[Code]
function TesseractInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{pf}\Tesseract-OCR\tesseract.exe')) or
            FileExists(ExpandConstant('{pf32}\Tesseract-OCR\tesseract.exe')) or
            FileExists(ExpandConstant('{localappdata}\Programs\Tesseract-OCR\tesseract.exe'));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and (not TesseractInstalled) then
    MsgBox(
      'تم تثبيت GlyphSnap بنجاح.' + #13#10 + #13#10 +
      'ملاحظة: يحتاج OCR إلى Tesseract وحزمتي ara وeng. ثبّتها قبل تشغيل الاستخراج.',
      mbInformation,
      MB_OK
    );
end;
