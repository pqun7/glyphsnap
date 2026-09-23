param(
    [switch]$SkipAppBuild
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$ProductionExe = Join-Path $PSScriptRoot 'builds\nuitka\glyphsnap.dist\GlyphSnap.exe'
$LegacyExe = Join-Path $PSScriptRoot 'dist-nuitka\glyphsnap.dist\GlyphSnap.exe'
$Installer = Join-Path $PSScriptRoot 'builds\installer\GlyphSnap_Setup.exe'
$StageDirectory = Join-Path $env:TEMP 'GlyphSnap-Inno'
$StageInstaller = Join-Path $StageDirectory 'GlyphSnap_Setup.exe'

if (-not $SkipAppBuild) {
    & (Join-Path $PSScriptRoot 'build_exe.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw 'GlyphSnap Nuitka build failed.'
    }
}

if (-not (Test-Path $ProductionExe -PathType Leaf) -and
    -not (Test-Path $LegacyExe -PathType Leaf)) {
    throw "GlyphSnap.exe was not found. Run .\build_exe.ps1 first, or omit -SkipAppBuild."
}

$ISCCCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
) | Where-Object { $_ -and (Test-Path $_ -PathType Leaf) }

$ISCC = $ISCCCandidates | Select-Object -First 1
if (-not $ISCC) {
    throw 'Inno Setup 6 was not found. Install it, then rerun this script.'
}

Write-Host "Compiling GlyphSnap installer with $ISCC" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $StageDirectory | Out-Null
if (Test-Path $StageInstaller -PathType Leaf) {
    Remove-Item -LiteralPath $StageInstaller -Force
}
& $ISCC (Join-Path $PSScriptRoot 'installer_script.iss')
if ($LASTEXITCODE -ne 0) {
    throw 'Inno Setup compilation failed.'
}

if (-not (Test-Path $StageInstaller -PathType Leaf)) {
    throw "Installer was not created: $StageInstaller"
}

New-Item -ItemType Directory -Force -Path (Split-Path $Installer) | Out-Null
Copy-Item -LiteralPath $StageInstaller -Destination $Installer -Force

$File = Get-Item $Installer
$Hash = Get-FileHash $Installer -Algorithm SHA256

Write-Host 'INSTALLER_BUILD_OK' -ForegroundColor Green
Write-Host "Installer: $($File.FullName)"
Write-Host ("Size: {0:N2} MB" -f ($File.Length / 1MB))
Write-Host "SHA256: $($Hash.Hash)"
