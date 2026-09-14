$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$Python = (Get-Command python -ErrorAction Stop).Source
& $Python --version
if ($LASTEXITCODE -ne 0) { throw "Python could not be started." }

$NuitkaInfo = (& $Python -m nuitka --version 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) { throw "Nuitka is not available." }
if ($NuitkaInfo -notmatch 'Version C compiler:\s+cl') {
    throw "Nuitka did not detect the MSVC compiler."
}

foreach ($Module in @('PySide6', 'pymupdf', 'PIL', 'pytesseract')) {
    & $Python -c "import $Module; print('$Module OK')"
    if ($LASTEXITCODE -ne 0) { throw "Missing Python dependency: $Module" }
}

$OutputRoot = Join-Path $PSScriptRoot 'builds\nuitka-debug'
$DistDir = Join-Path $OutputRoot 'arabic_newspaper_ocr_native.dist'
$ExePath = Join-Path $DistDir 'ArabicNewspaperOCR.exe'
$SmokeOutput = Join-Path $OutputRoot 'smoke-output'

if (Test-Path $OutputRoot) { Remove-Item -Recurse -Force $OutputRoot }
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$env:NUITKA_CACHE_DIR = Join-Path $PSScriptRoot '.nuitka-cache'

$NuitkaArgs = @(
    '-m', 'nuitka', '--standalone', '--windows-console-mode=force',
    '--enable-plugin=pyside6', '--assume-yes-for-downloads',
    '--nofollow-import-to=pymupdf', '--nofollow-import-to=fitz',
    '--no-deployment-flag=excluded-module-usage',
    '--nofollow-import-to=numpy', '--nofollow-import-to=pandas',
    '--nofollow-import-to=cv2', '--nofollow-import-to=streamlit',
    '--nofollow-import-to=streamlit_cropper', '--nofollow-import-to=paddle',
    '--nofollow-import-to=paddleocr', '--nofollow-import-to=scipy',
    "--output-dir=$OutputRoot", '--output-filename=ArabicNewspaperOCR.exe',
    'arabic_newspaper_ocr_native.py'
)
& $Python @NuitkaArgs
if ($LASTEXITCODE -ne 0) { throw "Nuitka debug build failed." }
if (-not (Test-Path $ExePath -PathType Leaf)) { throw "Nuitka debug EXE was not created: $ExePath" }

$PyMuPDFPath = (& $Python -c "import pymupdf, pathlib; print(pathlib.Path(pymupdf.__file__).resolve().parent)" | Select-Object -Last 1).ToString().Trim()
$Destination = Join-Path $DistDir 'pymupdf'
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
Copy-Item -Path (Join-Path $PyMuPDFPath '*') -Destination $Destination -Recurse -Force
Get-ChildItem -Path $Destination -Recurse -File -Include *.pyd,*.dll | ForEach-Object {
    Copy-Item -Path $_.FullName -Destination (Join-Path $DistDir $_.Name) -Force
}

$SmokePdf = Join-Path $PSScriptRoot '5636-004.pdf'
New-Item -ItemType Directory -Force -Path $SmokeOutput | Out-Null
& $ExePath '--smoke-test' $SmokePdf $SmokeOutput
if ($LASTEXITCODE -ne 0) { throw "Nuitka debug runtime smoke test failed." }

Write-Host "NUITKA_DEBUG_BUILD_OK" -ForegroundColor Green
Write-Host "EXE: $ExePath"
Write-Host "DIST: $DistDir"
