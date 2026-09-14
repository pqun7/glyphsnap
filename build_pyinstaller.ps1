$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$Python = (Get-Command python -ErrorAction Stop).Source
& $Python --version
if ($LASTEXITCODE -ne 0) { throw "Python could not be started." }

foreach ($Module in @('PyInstaller', 'PySide6', 'pymupdf', 'PIL', 'pytesseract')) {
    & $Python -c "import $Module; print('$Module OK')"
    if ($LASTEXITCODE -ne 0) { throw "Missing Python dependency: $Module" }
}

$TesseractCandidates = @(
    'C:\Program Files\Tesseract-OCR\tesseract.exe',
    'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe'
)
$TesseractCommand = $TesseractCandidates | Where-Object { Test-Path $_ -PathType Leaf } | Select-Object -First 1
if (-not $TesseractCommand) {
    $TesseractCommand = (Get-Command tesseract.exe -ErrorAction SilentlyContinue).Source
}
if (-not $TesseractCommand) {
    Write-Warning "Tesseract was not found. The build will succeed, but OCR requires Tesseract with ara.traineddata."
}
else {
    $Languages = & $TesseractCommand --list-langs 2>&1
    if ($Languages -notcontains 'ara') {
        Write-Warning "Tesseract was found, but ara.traineddata is unavailable."
    }
}

$OutputRoot = Join-Path $PSScriptRoot 'builds\pyinstaller'
$DistDir = Join-Path $OutputRoot 'dist'
$WorkDir = Join-Path $OutputRoot 'work'
$ExePath = Join-Path $DistDir 'ArabicNewspaperOCR\ArabicNewspaperOCR.exe'
$SmokeOutput = Join-Path $OutputRoot 'smoke-output'

if (Test-Path $OutputRoot) { Remove-Item -Recurse -Force $OutputRoot }
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

& $Python -m PyInstaller --clean --noconfirm `
    --distpath $DistDir `
    --workpath $WorkDir `
    ArabicNewspaperOCR.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
if (-not (Test-Path $ExePath -PathType Leaf)) { throw "PyInstaller EXE was not created: $ExePath" }

$SmokePdf = Join-Path $PSScriptRoot '5636-004.pdf'
if (-not (Test-Path $SmokePdf -PathType Leaf)) { throw "Smoke-test PDF was not found: $SmokePdf" }
New-Item -ItemType Directory -Force -Path $SmokeOutput | Out-Null
& $ExePath '--smoke-test' $SmokePdf $SmokeOutput
if ($LASTEXITCODE -ne 0) { throw "PyInstaller runtime smoke test failed." }

Write-Host "PYINSTALLER_BUILD_OK" -ForegroundColor Green
Write-Host "EXE: $ExePath"
Write-Host "DIST: $(Split-Path $ExePath)"
