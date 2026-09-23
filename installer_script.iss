#define MyAppName "Text Extractor OCR"
#define MyAppVersion "2.0.0"
#define MyAppPublisher "Ali Alnazer Ahmed"
#define MyAppExeName "ArabicNewspaperOCR.exe"

; Nuitka standalone output
#define MySourceFolder "builds\nuitka\arabic_newspaper_ocr_native.dist"

; Application icon
#define MyIconFile "assets\app.ico"

[Setup]
AppId={{D3F9A401-28B3-4A59-8392-123456789ABC}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}

DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}

DisableProgramGroupPage=yes
PrivilegesRequired=admin

OutputDir=builds\installer
OutputBaseFilename=ArabicNewspaperOCR_Setup

Compression=lzma2/ultra64
SolidCompression=yes

ArchitecturesInstallIn64BitMode=x64compatible

WizardStyle=modern

; Installer icon
SetupIconFile={#MyIconFile}

; Uninstaller
UninstallDisplayName={#MyAppName}
Uninstallable=yes

; Windows version information
VersionInfoDescription={#MyAppName}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoCopyright=Copyright (C) 2026 {#MyAppPublisher}

; Prevent multiple installer instances
AppMutex=TextExtractorOCRInstaller

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"

[Tasks]
Name: "desktopicon"; \
    Description: "إنشاء اختصار على سطح المكتب"; \
    GroupDescription: "اختصارات إضافية:"; \
    Flags: unchecked

[Files]
; Copy complete Nuitka standalone directory
Source: "{#MySourceFolder}\*"; \
    DestDir: "{app}"; \
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

[Run]
Filename: "{app}\{#MyAppExeName}"; \
    Description: "تشغيل التطبيق الآن"; \
    Flags: nowait postinstall skipifsilent
