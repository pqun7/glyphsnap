$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$Python = (Get-Command python -ErrorAction Stop).Source

& $Python --version
if ($LASTEXITCODE -ne 0) {
    throw "Python could not be started."
}

$NuitkaInfo = (& $Python -m nuitka --version 2>&1 | Out-String)

if ($LASTEXITCODE -ne 0) {
    throw "Nuitka is not available."
}

if ($NuitkaInfo -notmatch 'Version C compiler:\s+cl') {
    throw "Nuitka did not detect the MSVC compiler."
}

foreach ($Module in @('PySide6', 'pymupdf', 'PIL', 'pytesseract')) {
    & $Python -c "import $Module; print('$Module OK')"

    if ($LASTEXITCODE -ne 0) {
        throw "Missing Python dependency: $Module"
    }
}

$OutputRoot = Join-Path $PSScriptRoot 'builds\nuitka'
$DistDir = Join-Path $OutputRoot 'arabic_newspaper_ocr_native.dist'
$ExePath = Join-Path $DistDir 'ArabicNewspaperOCR.exe'
$SmokeOutput = Join-Path $OutputRoot 'smoke-output'

$IconFile = Join-Path $PSScriptRoot 'assets\app.ico'

if (-not (Test-Path $IconFile -PathType Leaf)) {
    throw "Application icon was not found: $IconFile"
}

if ([System.IO.Path]::GetExtension($IconFile).ToLowerInvariant() -ne '.ico') {
    throw "Application icon must be an .ico file: $IconFile"
}

Write-Host ""
Write-Host "Application icon:" -ForegroundColor Cyan
Write-Host $IconFile -ForegroundColor Gray
Write-Host ""

if (Test-Path $OutputRoot) {
    Remove-Item -Recurse -Force $OutputRoot
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$env:NUITKA_CACHE_DIR = Join-Path $PSScriptRoot '.nuitka-cache'

$NuitkaArgs = @(
    '-m', 'nuitka',
    '--standalone',
    '--windows-console-mode=disable',
    '--enable-plugin=pyside6',
    '--assume-yes-for-downloads',

    '--windows-icon-from-ico=assets/app.ico',
    '--include-data-files=assets/app.ico=assets/app.ico',

    '--nofollow-import-to=pymupdf',
    '--nofollow-import-to=fitz',
    '--no-deployment-flag=excluded-module-usage',

    '--nofollow-import-to=numpy',
    '--nofollow-import-to=pandas',
    '--nofollow-import-to=cv2',
    '--nofollow-import-to=streamlit',
    '--nofollow-import-to=streamlit_cropper',
    '--nofollow-import-to=paddle',
    '--nofollow-import-to=paddleocr',
    '--nofollow-import-to=scipy',

    '--output-dir=builds\nuitka',
    '--output-filename=ArabicNewspaperOCR.exe',

    'arabic_newspaper_ocr_native.py'
)

Write-Host ""
Write-Host "Starting Nuitka production build..." -ForegroundColor Cyan
Write-Host ""

& $Python @NuitkaArgs

if ($LASTEXITCODE -ne 0) {
    throw "Nuitka production build failed."
}

if (-not (Test-Path $ExePath -PathType Leaf)) {
    throw "Nuitka production EXE was not created: $ExePath"
}

Write-Host ""
Write-Host "Nuitka EXE created successfully." -ForegroundColor Green
Write-Host $ExePath -ForegroundColor Gray

$PyMuPDFPath = (
    & $Python -c "import pymupdf, pathlib; print(pathlib.Path(pymupdf.__file__).resolve().parent)" |
    Select-Object -Last 1
).ToString().Trim()

if (-not $PyMuPDFPath) {
    throw "Could not locate PyMuPDF installation."
}

$Destination = Join-Path $DistDir 'pymupdf'

New-Item -ItemType Directory -Force -Path $Destination | Out-Null

Copy-Item `
    -Path (Join-Path $PyMuPDFPath '*') `
    -Destination $Destination `
    -Recurse `
    -Force

Get-ChildItem -Path $Destination -Recurse -File -Include *.pyd, *.dll |
ForEach-Object {
    Copy-Item `
        -Path $_.FullName `
        -Destination (Join-Path $DistDir $_.Name) `
        -Force
}

$SmokePdf = Join-Path $PSScriptRoot '5636-004.pdf'

if (-not (Test-Path $SmokePdf -PathType Leaf)) {
    throw "Smoke test PDF was not found: $SmokePdf"
}

New-Item -ItemType Directory -Force -Path $SmokeOutput | Out-Null

Write-Host ""
Write-Host "Running production EXE smoke test..." -ForegroundColor Cyan
Write-Host ""

& $ExePath '--smoke-test' $SmokePdf $SmokeOutput

if ($LASTEXITCODE -ne 0) {
    throw "Nuitka production runtime smoke test failed."
}

Write-Host ""
Write-Host "NUITKA_PRODUCTION_BUILD_OK" -ForegroundColor Green
Write-Host "EXE:  $ExePath"
Write-Host "DIST: $DistDir"
Write-Host "ICON: $IconFile"